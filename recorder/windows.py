"""窓の箱を零ショットで自動検出する（11/6 の実験。目印係の手間を減らすため）。

使うモデル：Grounding DINO（IDEA-Research、Apache 2.0）の tiny 版を Hugging Face transformers 経由で
（`IDEA-Research/grounding-dino-tiny`）。文字の指示（既定 "window ."）で箱を出す。重みは HF のキャッシュに落ちる（git に入らない）。

流れ
    detect(photo_id)   → out/<棟>/windows/<写真>.json（箱は元の写真の px）と <写真>.jpg（箱と 10×10 の升目の重ね画像）。
                          2 回目はキャッシュを読む。
    box_cells(box)     → 箱の 4 角を「升＋寄りの升」で表す（landmarks.py の升の規約と同じ。目印係への手渡しの形）。
    evaluate(...)      → 登録済みの写真で、模型の窓（棟 JSON の面の窓の列・段）を台帳の視点で写真に投影した「正解の箱」と
                          IoU で突き合わせ、P/R/F1 を出す。

寸法は読まない。箱は「目印の候補の場所」で、列・段の番号付けは係（または人）がする。
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from . import registry as R_
from .landmarks import CROP_EXPAND, GRID_N, COLS, cell_rect
from .schema import bay_edges, load_building

MODEL_ID = "IDEA-Research/grounding-dino-tiny"
MODEL_LICENSE = "Apache-2.0（IDEA-Research/GroundingDINO。transformers 経由）"
INFER_LONG = 1600           # 推論の前に長辺をこの px に縮める
NMS_IOU = 0.5               # 同じ窓の重なった箱を 1 つに（Grounding DINO は NMS を持たない）
MAX_BOX_FRAC = 0.25         # 写真の面積のこの割合を超える箱は「面全体を 1 つの窓と取った」物として外す（記録には残す）
OUT_ROOT = Path("out")
TILE_OVERLAP = 0.2
DEFAULT_PROMPT = "window ."
DEFAULT_BOX = 0.3
DEFAULT_TEXT = 0.25
DEFAULT_TILES = 1

_MODEL = None               # (processor, model, device)


# --- 箱と升 ---------------------------------------------------------------------------------

def px_to_cell(u: float, v: float, w: int, h: int) -> tuple[str, str]:
    """元の写真の px → (升, 寄りの升)。landmarks.pick_to_px の逆（寄りの升の中心に戻すと幅の約 1.5 % 以内）。"""
    u = min(max(float(u), 0.0), w - 1e-6)
    v = min(max(float(v), 0.0), h - 1e-6)
    col = min(GRID_N - 1, int(u / (w / GRID_N)))
    row = min(GRID_N - 1, int(v / (h / GRID_N)))
    cell = f"{COLS[col]}{row + 1}"
    cx0, cy0, cx1, cy1 = cell_rect(cell, w, h, expand=CROP_EXPAND)
    sc = min(GRID_N - 1, max(0, int((u - cx0) / ((cx1 - cx0) / GRID_N))))
    sr = min(GRID_N - 1, max(0, int((v - cy0) / ((cy1 - cy0) / GRID_N))))
    return cell, f"{COLS[sc]}{sr + 1}"


def box_cells(box, w: int, h: int) -> dict:
    """箱 [x0, y0, x1, y1]（px）→ 4 角の升＋寄りの升。目印係はこの角を「窓の列・段の端」の候補として使う。"""
    x0, y0, x1, y1 = (float(t) for t in box)
    out = {}
    for name, (u, v) in (("左上", (x0, y0)), ("右上", (x1, y0)), ("右下", (x1, y1)), ("左下", (x0, y1))):
        c, s = px_to_cell(u, v, w, h)
        out[name] = {"cell": c, "sub": s}
    return out


def iou(a, b) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    iw = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    ih = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = iw * ih
    ua = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return float(inter / ua) if ua > 0 else 0.0


def iou_matrix(A, B) -> np.ndarray:
    if not len(A) or not len(B):
        return np.zeros((len(A), len(B)))
    A, B = np.asarray(A, float), np.asarray(B, float)
    iw = np.clip(np.minimum(A[:, None, 2], B[None, :, 2]) - np.maximum(A[:, None, 0], B[None, :, 0]), 0, None)
    ih = np.clip(np.minimum(A[:, None, 3], B[None, :, 3]) - np.maximum(A[:, None, 1], B[None, :, 1]), 0, None)
    inter = iw * ih
    aa = (A[:, 2] - A[:, 0]) * (A[:, 3] - A[:, 1])
    ab = (B[:, 2] - B[:, 0]) * (B[:, 3] - B[:, 1])
    ua = aa[:, None] + ab[None, :] - inter
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(ua > 0, inter / ua, 0.0)


def match(gt: list, det: list, thr: float = 0.5, scores: list | None = None) -> dict:
    """正解の箱と検出の箱を IoU ≥ thr で 1 対 1 に突き合わせる（検出はスコアの高い順に、まだ取られていない最良の正解へ）。
    返り値 {"tp", "fp", "fn", "pairs": [(det_i, gt_j, iou)]}。"""
    order = list(range(len(det)))
    if scores is not None:
        order.sort(key=lambda i: -scores[i])
    M = iou_matrix(det, gt) if len(det) and len(gt) else np.zeros((len(det), len(gt)))
    free = np.ones(len(gt), bool)
    pairs = []
    for i in order:
        if not len(gt):
            break
        row = np.where(free, M[i], -1.0)
        bj = int(np.argmax(row))
        if row[bj] >= thr:
            free[bj] = False
            pairs.append((i, bj, round(float(M[i, bj]), 3)))
    tp = len(pairs)
    return {"tp": tp, "fp": len(det) - tp, "fn": len(gt) - tp, "pairs": pairs}


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
    rd = lambda x: None if x is None else round(x, 3)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": rd(p), "recall": rd(r), "f1": rd(f)}


def nms(boxes: list, scores: list, thr: float = NMS_IOU) -> list[int]:
    keep: list[int] = []
    for i in sorted(range(len(boxes)), key=lambda i: -scores[i]):
        if all(iou(boxes[i], boxes[k]) < thr for k in keep):
            keep.append(i)
    return keep


# --- 推論 -----------------------------------------------------------------------------------

def _load_model(device: str | None = None):
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    import torch
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

    dev = device or ("cuda" if torch.cuda.is_available() else "cpu")
    proc = AutoProcessor.from_pretrained(MODEL_ID)
    model = AutoModelForZeroShotObjectDetection.from_pretrained(MODEL_ID).to(dev).eval()
    _MODEL = (proc, model, dev)
    return _MODEL


def _infer(pil_img, prompt: str, box_threshold: float, text_threshold: float, device: str | None = None):
    import torch

    proc, model, dev = _load_model(device)
    inputs = proc(images=pil_img, text=prompt, return_tensors="pt").to(dev)
    try:
        with torch.no_grad():
            outputs = model(**inputs)
    except torch.cuda.OutOfMemoryError:
        # GPU を他の係と共有している。落ちたら CPU に移して続ける
        global _MODEL
        torch.cuda.empty_cache()
        model = model.to("cpu")
        _MODEL = (proc, model, "cpu")
        dev = "cpu"
        inputs = proc(images=pil_img, text=prompt, return_tensors="pt")
        with torch.no_grad():
            outputs = model(**inputs)
    size = [(pil_img.height, pil_img.width)]
    try:
        res = proc.post_process_grounded_object_detection(outputs, inputs.input_ids, threshold=box_threshold,
                                                          text_threshold=text_threshold, target_sizes=size)[0]
    except TypeError:
        res = proc.post_process_grounded_object_detection(outputs, inputs.input_ids, box_threshold=box_threshold,
                                                          text_threshold=text_threshold, target_sizes=size)[0]
    labels = res.get("text_labels") or res.get("labels")
    out = ([[float(v) for v in b] for b in res["boxes"].cpu().tolist()], [float(s) for s in res["scores"].cpu().tolist()],
           [str(l) for l in labels], dev)
    del outputs, inputs
    if dev == "cuda":
        torch.cuda.empty_cache()
    return out


def _photo(photo_id: str, index: dict | None = None) -> dict:
    rec = R_.photo_record(photo_id, index)
    if rec is None:
        raise KeyError(f"索引に無い写真: {photo_id}")
    return rec


def out_dir_for(building_id: str) -> Path:
    return OUT_ROOT / building_id / "windows"


def tile_rects(w: int, h: int, n: int, overlap: float = TILE_OVERLAP) -> list[tuple[int, int, int, int]]:
    """n×n の重なり付きの切り出し（px）。n ≤ 1 なら全体 1 枚。"""
    if n <= 1:
        return [(0, 0, w, h)]
    tw, th = w / (n - (n - 1) * overlap), h / (n - (n - 1) * overlap)
    out = []
    for r in range(n):
        for c in range(n):
            x0, y0 = c * tw * (1 - overlap), r * th * (1 - overlap)
            out.append((int(x0), int(y0), int(min(w, x0 + tw)), int(min(h, y0 + th))))
    return out


def cache_path(building_id: str, photo_id: str, params: dict) -> Path:
    """既定の設定は <写真>.json、違う設定は <写真>__<設定>.json（実験で並べて比べるため）。"""
    if params == default_params():
        return out_dir_for(building_id) / f"{photo_id}.json"
    tag = f"t{params['tiles']}_b{params['box_threshold']}_x{params['text_threshold']}_{hashlib.md5(params['prompt'].encode()).hexdigest()[:6]}"
    return out_dir_for(building_id) / f"{photo_id}__{tag}.json"


def default_params() -> dict:
    return {"prompt": DEFAULT_PROMPT, "box_threshold": DEFAULT_BOX, "text_threshold": DEFAULT_TEXT, "infer_long": INFER_LONG,
            "tiles": DEFAULT_TILES}


def detect(photo_id: str, prompt: str = None, box_threshold: float = None, text_threshold: float = None,
           tiles: int = None, building_id: str | None = None, index: dict | None = None, use_cache: bool = True,
           device: str | None = None, draw: bool = True) -> dict:
    """写真 1 枚の窓の箱。結果は out/<棟>/windows/<写真>.json（箱は元の写真の px）。
    tiles=n なら全体 1 枚＋n×n の重なり付きの切り出しでも推論して NMS で合わせる（遠い・小さい窓のため）。"""
    from PIL import Image, ImageOps

    prompt = DEFAULT_PROMPT if prompt is None else prompt
    box_threshold = DEFAULT_BOX if box_threshold is None else box_threshold
    text_threshold = DEFAULT_TEXT if text_threshold is None else text_threshold
    tiles = DEFAULT_TILES if tiles is None else tiles
    rec = _photo(photo_id, index)
    bid = building_id or rec.get("building") or "unknown"
    params = {"prompt": prompt, "box_threshold": box_threshold, "text_threshold": text_threshold, "infer_long": INFER_LONG,
              "tiles": tiles}
    js = cache_path(bid, rec["id"], params)
    od = js.parent
    if use_cache and js.exists():
        doc = json.loads(js.read_text(encoding="utf-8"))
        if doc.get("params") == params:
            return doc
    im = ImageOps.exif_transpose(Image.open(R_.photo_path(rec))).convert("RGB")
    W, H = im.size
    s = min(1.0, INFER_LONG / max(W, H))
    small = im.resize((int(round(W * s)), int(round(H * s))), Image.LANCZOS) if s < 1 else im
    t0 = time.perf_counter()
    boxes, scores, labels = [], [], []
    rects = tile_rects(small.width, small.height, 1) + (tile_rects(small.width, small.height, tiles) if tiles > 1 else [])
    for (x0, y0, x1, y1) in rects:
        crop = small if (x0, y0, x1, y1) == (0, 0, small.width, small.height) else small.crop((x0, y0, x1, y1))
        bb, ss, ll, dev = _infer(crop, prompt, box_threshold, text_threshold, device)
        for b_, s_, l_ in zip(bb, ss, ll):
            # 切り出しの縁で切れた箱（縁から 2 px 以内に辺があり、その縁が写真の縁でない）は捨てる。全体の推論が拾う
            cut = ((b_[0] <= 2 and x0 > 0) or (b_[1] <= 2 and y0 > 0) or (b_[2] >= crop.width - 2 and x1 < small.width)
                   or (b_[3] >= crop.height - 2 and y1 < small.height))
            if cut and crop is not small:
                continue
            boxes.append([b_[0] + x0, b_[1] + y0, b_[2] + x0, b_[3] + y0])
            scores.append(s_)
            labels.append(l_)
    sec = time.perf_counter() - t0
    boxes = [[v / s for v in b] for b in boxes]
    raw = len(boxes)
    keep = nms(boxes, scores)
    dets = []
    for i in keep:
        b = [round(min(max(boxes[i][0], 0), W), 1), round(min(max(boxes[i][1], 0), H), 1),
             round(min(max(boxes[i][2], 0), W), 1), round(min(max(boxes[i][3], 0), H), 1)]
        frac = (b[2] - b[0]) * (b[3] - b[1]) / (W * H)
        dets.append({"box": b, "score": round(scores[i], 3), "label": labels[i], "area_frac": round(frac, 4),
                     "too_big": frac > MAX_BOX_FRAC, "cells": box_cells(b, W, H)})
    dets.sort(key=lambda d: -d["score"])
    doc = {"photo": rec["id"], "building": bid, "image_size": [W, H], "params": params,
           "made_by": f"windows.detect（{MODEL_ID}、{MODEL_LICENSE}）", "made_at": datetime.now().isoformat(timespec="seconds"),
           "device": dev, "seconds": round(sec, 2), "n_raw": raw, "n_after_nms": len(dets),
           "note": f"箱は元の写真（EXIF の向きを反映）の px。NMS IoU {NMS_IOU}。面積が写真の {MAX_BOX_FRAC:.0%} を超える箱は too_big",
           "detections": dets}
    od.mkdir(parents=True, exist_ok=True)
    js.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    if draw:
        draw_overlay(im, doc, od / f"{js.stem}.jpg")
    return doc


def draw_overlay(im, doc: dict, out_jpg: Path, gt: list | None = None, long_side: int = 1600) -> Path:
    """箱とスコア（黄緑）、正解の箱（あれば水色）、10×10 の升目（薄く）。"""
    import cv2

    img = cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR) if not isinstance(im, np.ndarray) else im
    H, W = img.shape[:2]
    s = long_side / max(W, H)
    small = cv2.resize(img, (int(W * s), int(H * s)), interpolation=cv2.INTER_AREA)
    lay = small.copy()
    h, w = small.shape[:2]
    for i in range(GRID_N + 1):
        cv2.line(lay, (int(i * w / GRID_N), 0), (int(i * w / GRID_N), h - 1), (0, 255, 255), 1)
        cv2.line(lay, (0, int(i * h / GRID_N)), (w - 1, int(i * h / GRID_N)), (0, 255, 255), 1)
    for i in range(GRID_N):
        cv2.putText(lay, COLS[i], (int((i + 0.5) * w / GRID_N) - 6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(lay, str(i + 1), (3, int((i + 0.5) * h / GRID_N) + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
    small = cv2.addWeighted(lay, 0.35, small, 0.65, 0)
    for g in gt or []:
        x0, y0, x1, y1 = (int(v * s) for v in g)
        cv2.rectangle(small, (x0, y0), (x1, y1), (255, 200, 0), 1)
    for d in doc["detections"]:
        x0, y0, x1, y1 = (int(v * s) for v in d["box"])
        col = (0, 0, 255) if d.get("too_big") else (0, 230, 120)
        cv2.rectangle(small, (x0, y0), (x1, y1), col, 2)
        cv2.putText(small, f"{d['score']:.2f}", (x0 + 2, max(10, y0 - 3)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1, cv2.LINE_AA)
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tofile(str(out_jpg))
    return out_jpg


# --- 正解の箱（模型の窓を台帳の視点で投影）---------------------------------------------------

def _v(m):
    if isinstance(m, (int, float)) and not isinstance(m, bool):
        return float(m)
    if isinstance(m, dict) and m.get("status") != "unknown" and m.get("value") is not None:
        return float(m["value"])
    return None


def face_window_rects(b: dict, f: dict) -> list[dict]:
    """面の窓の矩形 (s0, s1, z0, z1)（面の平面上、m）。blender_build の窓の置き方と同じ式
    （柱割りの始まり＋(列−1)×柱間隔＋柱間隔/2、1 スパンに per_bay 個、幅は width か 柱間隔−柱型、隙間 gap）。"""
    bays = f.get("bays") or {}
    fr = R_.face_rect_frame(b, f)
    L = _v(f.get("length")) or fr["length_m"]
    edges, _ = bay_edges(bays, L)
    cnt, pitch = _v(bays.get("count")), _v(bays.get("pitch"))
    if not edges or not cnt or not pitch:
        return []
    pitch = (edges[-1] - edges[0]) / cnt if cnt else pitch
    margin = edges[0]
    pier = _v(bays.get("pier_width")) or 0.6
    out = []
    for w in f.get("windows") or []:
        zb, zt = _v(w.get("z_bottom")), _v(w.get("z_top"))
        if zb is None or zt is None:
            continue
        ww = _v(w.get("width")) or (pitch - pier)
        per = int(w.get("per_bay") or 1)
        sub = ww / per
        gap = (_v(w.get("gap")) if _v(w.get("gap")) is not None else 0.15) if per > 1 else 0.1
        for i in w.get("bays") or range(1, int(cnt) + 1):
            for k in range(per):
                sc = margin + (i - 1) * pitch + pitch / 2 + (k - (per - 1) / 2) * sub
                ws = sub - gap
                out.append({"face": f["id"], "row": w.get("name", "窓"), "bay": int(i), "k": k,
                            "s0": sc - ws / 2, "s1": sc + ws / 2, "z0": zb, "z1": zt})
    return out


def face_glazing_rects(b: dict, f: dict) -> list[dict]:
    fr = R_.face_rect_frame(b, f)
    out = []
    for gz in f.get("glazing") or []:
        zt = _v(gz.get("z_top"))
        if zt is None:
            continue
        out.append({"face": f["id"], "s0": _v(gz.get("along0")) or 0.0, "s1": _v(gz.get("along1")) or fr["length_m"],
                    "z0": _v(gz.get("z_bottom")) or 0.0, "z1": zt})
    return out


def _project_rect(cam: dict, fr: dict, r: dict, n_edge: int = 5):
    """面の矩形 → 写真の箱（辺を n_edge 点ずつ取り、前にある点の外接箱）。返り値 (箱 or None, 世界の 4 角)。"""
    t = np.linspace(0, 1, n_edge)
    S = np.concatenate([r["s0"] + (r["s1"] - r["s0"]) * t, np.full(n_edge, r["s1"]), r["s1"] - (r["s1"] - r["s0"]) * t, np.full(n_edge, r["s0"])])
    Z = np.concatenate([np.full(n_edge, r["z0"]), r["z0"] + (r["z1"] - r["z0"]) * t, np.full(n_edge, r["z1"]), r["z1"] - (r["z1"] - r["z0"]) * t])
    P = R_.plane_to_world(fr, S, Z)
    u, v, d = R_.project_world(cam, P)
    if (d <= 0.5).any():
        return None, P
    return [float(u.min()), float(v.min()), float(u.max()), float(v.max())], P


def gt_boxes(b: dict, cam: dict, min_px: float = 12.0, min_inside: float = 0.6, occ_max: float = 0.5) -> dict:
    """写真に写るはずの窓の箱。見える面（カメラが面の外側＝表向き、写真の枠に入る、他の面に隠れない範囲あり）だけ。
    窓ごとに 4 角と中心の 5 点の隠れを調べ、occ_max を超えて隠れる窓は外す。箱が枠からはみ出す割合が大きい物
    （枠内の割合 < min_inside）、短辺が min_px 未満（元の写真の px を長辺 1600 に縮めた尺度）は外す。"""
    w, h = cam["image_size"]
    scale = 1600 / max(w, h)
    Rm, tv = np.array(cam["R_cv"], float), np.array(cam["t_cv"], float).ravel()
    C = -Rm.T @ tv
    faces = [f for f in b.get("faces", []) if f.get("from") and f.get("to")]
    rects = []
    for f in faces:
        fr = R_.face_rect_frame(b, f)
        p0, uu, _, L = R_.face_frame(fr)
        rects.append((f["id"], p0, uu, L, fr["ground_offset"], fr["top_z"]))
    vis = {v["face"]: v for v in R_.visible_faces(b, cam)}
    out, glz, regions = [], [], []
    stats = {"faces_visible": sorted(vis), "dropped_occluded": 0, "dropped_frame": 0, "dropped_small": 0}
    for f in faces:
        if f["id"] not in vis:
            continue
        fr = R_.face_rect_frame(b, f)
        p0, _, nv, L = R_.face_frame(fr)
        dist = float((C[:2] - p0) @ nv)
        mid = p0 + R_.face_frame(fr)[1] * (L / 2)
        to_cam = np.array([C[0] - mid[0], C[1] - mid[1]])
        ang = math.degrees(math.acos(max(-1.0, min(1.0, float(to_cam @ nv) / (np.linalg.norm(to_cam) + 1e-9)))))
        whole = {"s0": 0.0, "s1": L, "z0": fr["ground_offset"], "z1": fr["top_z"]}
        H = R_.H_from_pose(cam["R_cv"], cam["t_cv"], cam["focal_px"], w, h, fr)
        poly = R_.cover_polygon(H, w, h, (0.0, L, fr["ground_offset"], fr["top_z"]))
        if len(poly) >= 3:
            uu_, vv_, _ = R_.apply_H(H, np.array([p[0] for p in poly]), np.array([p[1] for p in poly]))
            regions.append({"face": f["id"], "poly_px": [[float(a), float(c)] for a, c in zip(uu_, vv_)]})
        for r in face_window_rects(b, f):
            box, P = _project_rect(cam, fr, r)
            if box is None:
                stats["dropped_frame"] += 1
                continue
            cx = [0.5 * (r["s0"] + r["s1"])]
            cz = [0.5 * (r["z0"] + r["z1"])]
            Pc = np.vstack([R_.plane_to_world(fr, [r["s0"], r["s1"], r["s1"], r["s0"]], [r["z0"], r["z0"], r["z1"], r["z1"]]),
                            R_.plane_to_world(fr, cx, cz)])
            occ = float(R_._occluded(C, Pc, rects, f["id"]).mean())
            if occ > occ_max:
                stats["dropped_occluded"] += 1
                continue
            cb = [max(0.0, box[0]), max(0.0, box[1]), min(float(w), box[2]), min(float(h), box[3])]
            a_full = (box[2] - box[0]) * (box[3] - box[1])
            a_in = max(0.0, cb[2] - cb[0]) * max(0.0, cb[3] - cb[1])
            if a_full <= 0 or a_in / a_full < min_inside:
                stats["dropped_frame"] += 1
                continue
            if min(cb[2] - cb[0], cb[3] - cb[1]) * scale < min_px:
                stats["dropped_small"] += 1
                continue
            out.append({**{k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()}, "box": [round(v, 1) for v in cb],
                        "face_dist_m": round(dist, 1), "view_angle_deg": round(ang, 1)})
        for r in face_glazing_rects(b, f):
            box, _ = _project_rect(cam, fr, r)
            if box is not None:
                glz.append({"face": f["id"], "box": [max(0.0, box[0]), max(0.0, box[1]), min(float(w), box[2]), min(float(h), box[3])]})
    return {"windows": out, "glazing": glz, "face_regions": regions, "stats": stats}


def _point_in_poly(x: float, y: float, poly) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0 + 1e-12) + x0:
            inside = not inside
    return inside


def view_class(dist_m: float, ang_deg: float, far_m: float = 50.0, front_deg: float = 30.0) -> str:
    """遠景（面までの距離 > far_m）・正面に近い（視線と面の法線の角 ≤ front_deg）・斜め。"""
    if dist_m > far_m:
        return "遠景"
    return "正面寄り" if ang_deg <= front_deg else "斜め"


# --- 評価 -----------------------------------------------------------------------------------

def best_shift(gt: list, det: list, thr: float = 0.3, max_frac: float = 0.3, w: int = 4000, h: int = 3000) -> tuple[float, float, int]:
    """正解の箱を平行移動して検出と一番多く重なる移動量（px）。登録の視点の誤差（数 m ＝数十〜数百 px）と検出の良し悪しを
    分けるため。候補は大きさの近い (正解, 検出) の組の中心の差、移動量は写真の幅・高さの max_frac まで。
    窓は周期的に並ぶので 1 スパンずれた移動も同じくらい合う（検出の評価には差し支えないが、登録の検算には使えない）。"""
    if not gt or not det:
        return 0.0, 0.0, 0
    G, D = np.array(gt, float), np.array(det, float)
    gc, dc = (G[:, :2] + G[:, 2:]) / 2, (D[:, :2] + D[:, 2:]) / 2
    gs, ds = G[:, 2:] - G[:, :2], D[:, 2:] - D[:, :2]
    cands = [(0.0, 0.0)]
    for i in range(len(G)):
        for j in range(len(D)):
            r = ds[j] / np.maximum(gs[i], 1e-6)
            if np.all(r > 0.5) and np.all(r < 2.0):
                dx, dy = dc[j] - gc[i]
                if abs(dx) <= max_frac * w and abs(dy) <= max_frac * h:
                    cands.append((float(dx), float(dy)))
    cands = list({(round(dx / 10) * 10, round(dy / 10) * 10) for dx, dy in cands})     # 10 px に丸めて重複を除く
    best = (0.0, 0.0, match(gt, det, thr)["tp"])
    for dx, dy in cands:
        sh = (G + np.array([dx, dy, dx, dy])).tolist()
        tp = match(sh, det, thr)["tp"]
        if tp > best[2] or (tp == best[2] and dx * dx + dy * dy < best[0] ** 2 + best[1] ** 2):
            best = (float(dx), float(dy), tp)
    return best


def _score(g: dict, boxes: list, scores: list, sel: list[int], thr: float, shift=(0.0, 0.0)) -> dict:
    dx, dy = shift
    gtb = [[q[0] + dx, q[1] + dy, q[2] + dx, q[3] + dy] for q in (gw["box"] for gw in g["windows"])]
    m = match(gtb, [boxes[i] for i in sel], thr, [scores[i] for i in sel])
    matched_gt = {j for _, j, _ in m["pairs"]}
    faces: dict = {}
    for j, gw in enumerate(g["windows"]):
        fd = faces.setdefault(gw["face"], {"gt": 0, "tp": 0, "fp": 0, "view": view_class(gw["face_dist_m"], gw["view_angle_deg"]),
                                           "face_dist_m": gw["face_dist_m"], "view_angle_deg": gw["view_angle_deg"]})
        fd["gt"] += 1
        fd["tp"] += j in matched_gt
    matched_det = {p[0] for p in m["pairs"]}
    for k, i in enumerate(sel):         # 誤りの検出を、中心が入る面に割り振る
        if k in matched_det:
            continue
        bx = boxes[i]
        cxp, cyp = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
        fid = next((r["face"] for r in g["face_regions"] if _point_in_poly(cxp, cyp, r["poly_px"])), None)
        fd = faces.setdefault(fid, {"gt": 0, "tp": 0, "fp": 0, "view": None})
        fd["fp"] += 1
    return {**prf(m["tp"], m["fp"], m["fn"]), "faces": faces,
            "mean_iou_tp": round(float(np.mean([p[2] for p in m["pairs"]])), 3) if m["pairs"] else None}


def evaluate(building_jsons: list[Path], prompt: str | None = None, box_threshold: float | None = None,
             text_threshold: float | None = None, tiles: int | None = None, iou_thrs=(0.5, 0.3),
             out_json: Path | None = None, draw: bool = True, log=print) -> dict:
    """登録済み写真（台帳の entries のうち camera のある物）で、検出と模型の窓の投影を突き合わせる。
    precision は「見える面の範囲（投影した面の多角形）に中心がある検出」だけで数える（他の棟・木・空の箱は別に数える）。
    ガラスの帯（glazing）の中に中心がある検出と、面全体を 1 つと取った巨大な箱（too_big）は数えない。
    raw は台帳の視点そのまま、aligned は正解の箱を写真ごとに平行移動して合わせた後（登録の誤差を除いた検出の力）。"""
    from .photos import INDEX_PATH, load_index

    idx = load_index(INDEX_PATH)
    reg = R_.load_registry()
    per_photo = []
    for bj in building_jsons:
        b = load_building(bj)
        for e in reg.get("entries", []):
            if e["building"] != b["id"] or not (e.get("camera") or {}).get("R_cv"):
                continue
            cam = e["camera"]
            try:
                doc = detect(e["photo"], prompt, box_threshold, text_threshold, tiles, building_id=b["id"], index=idx)
            except Exception as ex:          # noqa: BLE001  写真が読めない・GPU が落ちた等は記録して次へ
                log(f"  {e['photo']}: 検出できず {ex}")
                per_photo.append({"photo": e["photo"], "building": b["id"], "error": str(ex)})
                continue
            W, H = doc["image_size"]
            sx, sy = cam["image_size"][0] / W, cam["image_size"][1] / H
            g = gt_boxes(b, cam)
            dets_all = doc["detections"]
            boxes = [[d["box"][0] * sx, d["box"][1] * sy, d["box"][2] * sx, d["box"][3] * sy] for d in dets_all]
            scores = [d["score"] for d in dets_all]
            cls = []
            for d, bx in zip(dets_all, boxes):
                cxp, cyp = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
                if d.get("too_big"):
                    cls.append("too_big")
                elif any(q[0] <= cxp <= q[2] and q[1] <= cyp <= q[3] for q in (gz["box"] for gz in g["glazing"])):
                    cls.append("glazing")
                elif any(_point_in_poly(cxp, cyp, r["poly_px"]) for r in g["face_regions"]):
                    cls.append("face")
                else:
                    cls.append("outside")
            sel = [i for i, c in enumerate(cls) if c == "face"]
            gtb = [gw["box"] for gw in g["windows"]]
            dx, dy, _ = best_shift(gtb, [boxes[i] for i in sel], 0.3, w=cam["image_size"][0], h=cam["image_size"][1])
            rec = {"photo": e["photo"], "building": b["id"], "period": e.get("period"), "device": doc["device"],
                   "seconds": doc["seconds"], "image_size": [W, H], "n_det": len(dets_all),
                   "n_det_face": len(sel), "n_det_outside": cls.count("outside"), "n_det_glazing": cls.count("glazing"),
                   "n_too_big": cls.count("too_big"), "n_gt": len(gtb), "gt_stats": g["stats"],
                   "align_shift_px": [round(dx, 1), round(dy, 1)],
                   "align_shift_frac_w": round(math.hypot(dx, dy) / cam["image_size"][0], 3),
                   "by_iou": {str(t): _score(g, boxes, scores, sel, t) for t in iou_thrs},
                   "aligned": {str(t): _score(g, boxes, scores, sel, t, (dx, dy)) for t in iou_thrs}}
            per_photo.append(rec)
            r5, a5 = rec["by_iou"][str(iou_thrs[0])], rec["aligned"][str(iou_thrs[0])]
            log(f"  {b['id']} {e['photo'][:34]:34s} 検出 {rec['n_det']:3d}（面内 {rec['n_det_face']}）正解 {rec['n_gt']:3d} "
                f"P {r5['precision']} R {r5['recall']} F1 {r5['f1']} | 合わせ後 P {a5['precision']} R {a5['recall']} F1 {a5['f1']} "
                f"ずれ {rec['align_shift_frac_w']:.0%}  {doc['seconds']} s {doc['device']}")
            if draw:
                try:
                    from PIL import Image, ImageOps
                    im = ImageOps.exif_transpose(Image.open(R_.photo_path(R_.photo_record(e["photo"], idx)))).convert("RGB")
                    tag = "" if doc["params"] == default_params() else "__" + cache_path(b["id"], doc["photo"], doc["params"]).stem.split("__")[-1]
                    draw_overlay(im, doc, out_dir_for(b["id"]) / f"{doc['photo']}{tag}_eval.jpg", gt=gtb)
                except Exception:                # noqa: BLE001
                    pass
    summary = summarize(per_photo, iou_thrs)
    res = {"made_by": f"windows.evaluate（{MODEL_ID}、{MODEL_LICENSE}）", "made_at": datetime.now().isoformat(timespec="seconds"),
           "params": {**default_params(), **{k: v for k, v in {"prompt": prompt, "box_threshold": box_threshold,
                      "text_threshold": text_threshold, "tiles": tiles}.items() if v is not None}, "nms_iou": NMS_IOU,
                      "iou_thrs": list(iou_thrs)},
           "method": "正解＝棟 JSON の面の窓（列×段、blender_build と同じ並べ方）を台帳の視点（registry.json の camera）で写真に投影した外接箱。"
                     "見える面（registry.visible_faces：表向き・枠内・他の面に隠れない）だけ、窓ごとに 4 角＋中心の隠れが半分を超える物は外す。"
                     "precision は見える面の多角形の中に中心がある検出だけで数える。ガラスの帯の中の検出は数えない。",
           "summary": summary, "photos": per_photo}
    if out_json:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def summarize(per_photo: list[dict], iou_thrs=(0.5, 0.3)) -> dict:
    out = {}
    ok = [p for p in per_photo if "by_iou" in p]
    secs = [p["seconds"] for p in ok]
    out["n_photos"] = len(ok)
    out["seconds_per_photo"] = {"median": round(float(np.median(secs)), 2) if secs else None,
                                "max": round(float(np.max(secs)), 2) if secs else None,
                                "devices": sorted({p["device"] for p in ok})}
    for mode, thr in [(m, t) for m in ("by_iou", "aligned") for t in iou_thrs]:
        k = str(thr)
        agg: dict[str, dict] = {}

        def add(key, tp, fp, fn):
            a = agg.setdefault(key, {"tp": 0, "fp": 0, "fn": 0})
            a["tp"] += tp
            a["fp"] += fp
            a["fn"] += fn

        for p in ok:
            r = p[mode][k]
            add("全体", r["tp"], r["fp"], r["fn"])
            add(f"棟:{p['building']}", r["tp"], r["fp"], r["fn"])
            for fid, fd in r["faces"].items():
                tp, gt, fp = fd.get("tp", 0), fd.get("gt", 0), fd.get("fp", 0)
                if fid is not None:
                    add(f"面:{p['building']}/{fid}", tp, fp, gt - tp)
                if fd.get("view"):
                    add(f"見え方:{fd['view']}", tp, fp, gt - tp)
                    add(f"見え方:{p['building']}/{fd['view']}", tp, fp, gt - tp)
        out[f"{'raw' if mode == 'by_iou' else 'aligned'}_iou{thr}"] = {kk: prf(v["tp"], v["fp"], v["fn"]) for kk, v in sorted(agg.items())}
    return out
