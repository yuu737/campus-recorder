"""写真 1 枚から実寸（m）の深さを出す公開モデルを「第二の物差し」として試す（11/6 の実験）。

使うモデル（この順。重み・コードの許諾は MODELS の license に）
    depth_pro   Apple Depth Pro（github.com/apple/ml-depth-pro）。深さ m と焦点（px）を出す。
                重み `checkpoints/depth_pro.pt`（git 管理外。取得 URL は WEIGHTS_URL）。入力は内部で 1536×1536。
    da2_small   Depth Anything V2 Metric Outdoor Small（transformers、Apache-2.0）。深さ m だけ（焦点は出さない）。
                Large・Base は CC-BY-NC なので使わない。

出力
    out/<棟>/depth/<写真id>_<model>.npz   depth（float16、縮めた格子）・scale（元の px ÷ 格子の px）・orig_size・focal_px
    out/<棟>/depth/<写真id>_<model>.json  要約（made_by にモデル名と許諾、所要秒、GPU/CPU）
    out/<棟>/depth/<写真id>_<model>.jpg   深さの色と、目印の位置に 真値/推定 の数字（evaluate が描く）

評価（evaluate）
    台帳 data/photos/registry.json の視点から、棟の目印（landmarks.catalog）と面の格子（面の内側 10〜90 %）の
    カメラの奥行き（真値）と、その px の推定の深さを比べる。比 = 推定 / 真値。
    焦点を出すモデルは、焦点の比（推定 / EXIF）と、EXIF の焦点を与えた時の比（depth × f_exif / f_est）も出す。
地面（ground_check）
    写真の下の帯（地面が写っている所）の深さを、台帳の視点で世界に戻し、足元の地面の高さを読む。
    5m 標高（− ground_z）と面の ground_offset と並べる。
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from . import registry as R_
from .photos import INDEX_PATH, load_index

CKPT_DIR = Path("checkpoints")
WEIGHTS_URL = "https://ml-site.cdn-apple.com/models/depth-pro/depth_pro.pt"
MODELS = {
    "depth_pro": {"name": "Apple Depth Pro (ml-depth-pro 0.1, depth_pro.pt)",
                  "license": "コード：Apple Sample Code License（緩い許諾）。重み：同リポジトリの取得スクリプトの URL（HF 版の model card は apple-amlr＝研究用と表示。再配布しない）",
                  "focal": True},
    "da2_small": {"name": "depth-anything/Depth-Anything-V2-Metric-Outdoor-Small-hf",
                  "license": "Apache-2.0（Small のみ。Base・Large は CC-BY-NC で使わない）", "focal": False},
}
GRID_LONG = 1536            # 保存する深さの格子の長辺
WIN = 2                     # 深さを読む窓（格子の ±WIN 画素の中央値）
EDGE_MARGIN = 0.02          # 写真の縁からこの割合の内側の点だけ使う
FACE_INSET = 0.1            # 面の格子：面の端からこの割合の内側
GROUND_BAND = (0.86, 0.98)  # 地面を読む写真の下の帯（高さの割合）
GROUND_COLS = (0.25, 0.75)
DIST_BANDS = ((0.0, 5.0, "5 m 未満"), (5.0, 15.0, "5〜15 m"), (15.0, 1e9, "15 m 超"))
ELEV_BANDS = ((-90.0, 10.0, "見上げ 10° 未満"), (10.0, 30.0, "見上げ 10〜30°"), (30.0, 90.0, "見上げ 30° 超"))
GATE_OK, GATE_HINT = 0.10, 0.30   # 散らばりの判定（検算の門に使える／向きの参考まで）

_LOADED: dict = {}


# --- 置き場 ------------------------------------------------------------------------------------

def out_dir(building_id: str) -> Path:
    return Path("out") / building_id / "depth"


def npz_path(building_id: str, photo_id: str, model: str) -> Path:
    return out_dir(building_id) / f"{photo_id}_{model}.npz"


def save_npz(path: Path, depth: np.ndarray, orig_size: tuple[int, int], focal_px: float | None, model: str) -> Path:
    """depth は縮めた格子（h×w、m）。orig_size は元の写真の (w, h)。focal_px は元の写真の px。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    h, w = depth.shape
    np.savez_compressed(path, depth=depth.astype(np.float16), scale=np.float64(orig_size[0] / w),
                        orig_size=np.array(orig_size, int), focal_px=np.float64(np.nan if focal_px is None else focal_px),
                        model=np.array(model))
    return path


def load_npz(path: Path) -> dict:
    d = np.load(path)
    f = float(d["focal_px"])
    return {"depth": d["depth"].astype(np.float32), "scale": float(d["scale"]), "orig_size": tuple(int(v) for v in d["orig_size"]),
            "focal_px": None if math.isnan(f) else f, "model": str(d["model"])}


def sample(doc: dict, u, v, win: int = WIN) -> np.ndarray:
    """元の写真の px (u, v) の深さ m（格子の ±win の中央値）。範囲外は NaN。"""
    D, s = doc["depth"], doc["scale"]
    h, w = D.shape
    u, v = np.atleast_1d(np.asarray(u, float)), np.atleast_1d(np.asarray(v, float))
    out = np.full(len(u), np.nan)
    for k, (uu, vv) in enumerate(zip(u / s, v / s)):
        i, j = int(round(vv)), int(round(uu))
        if 0 <= i < h and 0 <= j < w:
            patch = D[max(0, i - win):i + win + 1, max(0, j - win):j + win + 1]
            out[k] = float(np.median(patch))
    return out


# --- 推論 ------------------------------------------------------------------------------------

def _device():
    import torch

    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _load_model(model: str, device):
    import torch

    key = (model, str(device))
    if key in _LOADED:
        return _LOADED[key]
    if model == "depth_pro":
        import depth_pro
        from depth_pro.depth_pro import DEFAULT_MONODEPTH_CONFIG_DICT, DepthProConfig

        ck = CKPT_DIR / "depth_pro.pt"
        if not ck.exists():
            raise FileNotFoundError(f"{ck} が無い。{WEIGHTS_URL} から取る")
        cfg = DepthProConfig(**{**DEFAULT_MONODEPTH_CONFIG_DICT.__dict__, "checkpoint_uri": str(ck)})
        dtype = torch.float16 if device.type == "cuda" else torch.float32
        m, tf = depth_pro.create_model_and_transforms(config=cfg, device=device, precision=dtype)
        m.eval()
        _LOADED[key] = (m, tf)
    elif model == "da2_small":
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation

        name = MODELS[model]["name"]
        proc = AutoImageProcessor.from_pretrained(name)
        m = AutoModelForDepthEstimation.from_pretrained(name).to(device).eval()
        _LOADED[key] = (m, proc)
    else:
        raise ValueError(f"知らないモデル {model}（{', '.join(MODELS)}）")
    return _LOADED[key]


def release() -> None:
    _LOADED.clear()
    try:
        import torch

        torch.cuda.empty_cache()
    except Exception:
        pass


def _load_image(rec: dict):
    from PIL import Image, ImageOps

    return ImageOps.exif_transpose(Image.open(R_.photo_path(rec))).convert("RGB")


def _run(model: str, im, device) -> tuple[np.ndarray, float | None]:
    """縮めた画像 im（PIL）→ (深さ m の格子 im の大きさ, 焦点 px（im の px）か None)。"""
    import torch

    m, tf = _load_model(model, device)
    with torch.no_grad():
        if model == "depth_pro":
            x = tf(np.asarray(im)).to(device)
            pred = m.infer(x)
            D = pred["depth"].float().cpu().numpy()
            return D, float(pred["focallength_px"].float().cpu())
        inputs = tf(images=im, return_tensors="pt").to(device)
        pd = m(**inputs).predicted_depth            # (1, h', w')
        pd = torch.nn.functional.interpolate(pd[:, None], size=(im.height, im.width), mode="bilinear", align_corners=False)
        return pd[0, 0].float().cpu().numpy(), None


def predict(photo_id: str, model: str = "depth_pro", building_id: str | None = None, index: dict | None = None,
            long_side: int = GRID_LONG, device=None) -> dict:
    """写真 1 枚の深さを出して npz と要約 JSON に保存。返り値は要約。GPU で落ちたら CPU でやり直す。"""
    idx = index or load_index(INDEX_PATH)
    rec = R_.photo_record(photo_id, idx)
    if rec is None:
        raise KeyError(f"索引に無い写真 {photo_id}")
    bid = building_id or rec.get("building") or "_"
    im0 = _load_image(rec)
    W, H = im0.size
    k = long_side / max(W, H)
    im = im0.resize((round(W * k), round(H * k)))
    dev = device or _device()
    t0 = time.time()
    try:
        D, f = _run(model, im, dev)
    except RuntimeError as e:          # GPU のメモリ不足など
        if dev.type != "cuda":
            raise
        print(f"  GPU で落ちた（{str(e)[:80]}）→ CPU でやり直す")
        release()
        dev = _device().__class__("cpu")
        t0 = time.time()
        D, f = _run(model, im, dev)
    sec = time.time() - t0
    f_orig = f * W / im.width if f is not None else None
    p = save_npz(npz_path(bid, rec["id"], model), D, (W, H), f_orig, model)
    from .landmarks import focal_px_from_exif

    f_exif = focal_px_from_exif(rec)
    summ = {"photo": rec["id"], "building": bid, "period": rec.get("period"), "model": model,
            "orig_size": [W, H], "grid_size": [im.width, im.height], "npz": str(p).replace("\\", "/"),
            "focal_px": None if f_orig is None else round(f_orig, 1), "focal_exif_px": round(f_exif, 1),
            "focal_ratio": None if f_orig is None else round(f_orig / f_exif, 4),
            "depth_m": {"p05": round(float(np.nanpercentile(D, 5)), 2), "median": round(float(np.nanmedian(D)), 2),
                        "p95": round(float(np.nanpercentile(D, 95)), 2)},
            "seconds": round(sec, 2), "device": dev.type,
            "made_by": {"係": "depth（recorder/depth.py）", "model": MODELS[model]["name"], "license": MODELS[model]["license"]},
            "made_at": datetime.now().isoformat(timespec="seconds")}
    p.with_suffix(".json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    return summ


# --- 評価 ------------------------------------------------------------------------------------

def rel_spread(r) -> float | None:
    """比の散らばり：1.4826 × MAD ÷ 中央値（正規分布なら標準偏差の割合）。"""
    r = np.asarray([x for x in np.ravel(r) if x is not None and np.isfinite(x)], float)
    if len(r) < 2:
        return None
    m = float(np.median(r))
    return float(1.4826 * np.median(np.abs(r - m)) / m) if m > 0 else None


def summarize(ratios) -> dict:
    """比の集計：点の数・中央値・四分位・散らばり（rel_spread）・|比−1| の中央値。"""
    r = np.asarray([x for x in np.ravel(ratios) if x is not None and np.isfinite(x)], float)
    if len(r) == 0:
        return {"n": 0, "median": None, "q25": None, "q75": None, "spread": None, "abs_err_median": None}
    return {"n": int(len(r)), "median": round(float(np.median(r)), 3), "q25": round(float(np.percentile(r, 25)), 3),
            "q75": round(float(np.percentile(r, 75)), 3), "spread": (None if rel_spread(r) is None else round(rel_spread(r), 3)),
            "abs_err_median": round(float(np.median(np.abs(r - 1))), 3)}


def band_of(x: float, bands) -> str:
    for lo, hi, name in bands:
        if lo <= x < hi:
            return name
    return bands[-1][2]


def judge(spread: float | None, bias: float | None = None) -> str:
    """散らばり（と偏り |中央値−1|）で使い道を決める。"""
    if spread is None:
        return "判定できない（点が足りない）"
    worst = max(spread, abs(bias or 0.0))
    if worst <= GATE_OK:
        return "検算の門に使える（10 % 以内）"
    if worst > GATE_HINT:
        return "向きの参考まで（30 % 超）"
    return "粗い検算まで（10〜30 %）"


def _face_rects(b: dict) -> list[tuple]:
    rects = []
    for f in b.get("faces", []):
        if f.get("from") and f.get("to"):
            fr = R_.face_rect_frame(b, f)
            p0, u, _, L = R_.face_frame(fr)
            rects.append((f["id"], p0, u, L, fr["ground_offset"], fr["top_z"]))
    return rects


def truth_points(b: dict, cam: dict, kind: str = "landmark") -> list[dict]:
    """視点 cam から見える点（目印か面の格子）と、その px・カメラの奥行き（真値）・距離・見上げ角。
    見える面（visible_faces）の上で、写真の枠の内側、カメラが面の外側、他の面に隠れない物だけ。"""
    from .landmarks import catalog

    w, h = cam["image_size"]
    R, t = np.array(cam["R_cv"], float), np.array(cam["t_cv"], float).ravel()
    C = -R.T @ t
    vis = {v["face"] for v in R_.visible_faces(b, cam)}
    faces = {f["id"]: f for f in b.get("faces", []) if f.get("from") and f.get("to")}
    rects = _face_rects(b)
    pts = []
    if kind == "landmark":
        for m in catalog(b):
            if m["face"] in vis:
                pts.append({"id": m["id"], "face": m["face"], "xyz": m["xyz"]})
    else:
        for fid in vis:
            fr = R_.face_rect_frame(b, faces[fid])
            L, g, top = fr["length_m"], fr["ground_offset"], fr["top_z"]
            for s in np.linspace(L * FACE_INSET, L * (1 - FACE_INSET), 9):
                for z in np.linspace(g + (top - g) * FACE_INSET, top - (top - g) * FACE_INSET, 7):
                    P = R_.plane_to_world(fr, s, z).reshape(3)
                    pts.append({"id": f"{fid}@{s:.1f},{z:.1f}", "face": fid, "xyz": [float(v) for v in P]})
    if not pts:
        return []
    P = np.array([p["xyz"] for p in pts], float)
    u, v, zc = R_.project_world(cam, P)
    occ = np.zeros(len(pts), bool)
    for fid in {p["face"] for p in pts}:
        sel = np.array([p["face"] == fid for p in pts])
        occ[sel] = R_._occluded(C, P[sel], rects, fid)
    out = []
    mx, my = EDGE_MARGIN * w, EDGE_MARGIN * h
    for k, p in enumerate(pts):
        fr = R_.face_rect_frame(b, faces[p["face"]])
        p0, _, nv, _ = R_.face_frame(fr)
        if occ[k] or zc[k] <= 0.5 or not (mx <= u[k] < w - mx and my <= v[k] < h - my) or float((C[:2] - p0) @ nv) <= 0.3:
            continue
        d = P[k] - C
        horiz = float(np.hypot(d[0], d[1]))
        out.append({**p, "u": float(u[k]), "v": float(v[k]), "z_true": float(zc[k]), "dist": float(np.linalg.norm(d)),
                    "elev_deg": float(math.degrees(math.atan2(d[2], horiz)))})
    return out


def eval_photo(b: dict, entry: dict, doc: dict, f_exif: float) -> dict:
    """1 枚の評価：目印と面の格子それぞれの比、距離帯・見上げ角の帯ごと。"""
    cam = entry["camera"]
    res = {"photo": entry["photo"], "building": entry["building"], "face": entry["face"], "kind": entry.get("kind", "plane"),
           "period": entry.get("period"), "focal_reg_px": round(cam["focal_px"], 1), "focal_exif_px": round(f_exif, 1),
           "focal_est_px": None if doc["focal_px"] is None else round(doc["focal_px"], 1)}
    kf = (f_exif / doc["focal_px"]) if doc["focal_px"] else None      # EXIF の焦点を与えた時の深さの倍率
    res["focal_err"] = None if doc["focal_px"] is None else round(doc["focal_px"] / f_exif - 1, 4)
    for kind in ("landmark", "grid"):
        pts = truth_points(b, cam, kind)
        est = sample(doc, [p["u"] for p in pts], [p["v"] for p in pts]) if pts else np.array([])
        for p, e in zip(pts, est):
            p["z_est"] = None if not np.isfinite(e) else float(e)
            p["ratio"] = None if p["z_est"] is None else p["z_est"] / p["z_true"]
        ratios = [p["ratio"] for p in pts]
        blk = {"all": summarize(ratios),
               "by_dist": {n: summarize([p["ratio"] for p in pts if band_of(p["z_true"], DIST_BANDS) == n]) for *_, n in DIST_BANDS},
               "by_elev": {n: summarize([p["ratio"] for p in pts if band_of(p["elev_deg"], ELEV_BANDS) == n]) for *_, n in ELEV_BANDS}}
        if kf is not None:
            blk["all_exif_focal"] = summarize([None if r is None else r * kf for r in ratios])
        res[kind] = blk
        res[f"{kind}_points"] = [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in p.items() if k != "xyz"} for p in pts]
    return res


def _font(size: int):
    from PIL import ImageFont

    for f in ("C:/Windows/Fonts/meiryo.ttc", "C:/Windows/Fonts/YuGothM.ttc", "C:/Windows/Fonts/msgothic.ttc"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw(doc: dict, rec: dict, pts: list[dict], out_jpg: Path, long_side: int = 1200) -> Path:
    """深さの色（turbo、対数）と、目印の位置に 真値/推定 の m。"""
    import matplotlib

    from PIL import Image, ImageDraw

    D = doc["depth"]
    lg = np.log(np.clip(D, 0.5, 200))
    lo, hi = np.nanpercentile(lg, 2), np.nanpercentile(lg, 98)
    col = (matplotlib.colormaps["turbo"](np.clip((lg - lo) / max(hi - lo, 1e-6), 0, 1))[..., :3] * 255).astype(np.uint8)
    photo = _load_image(rec).resize((D.shape[1], D.shape[0]))
    im = Image.blend(photo, Image.fromarray(col), 0.55)
    k = long_side / max(im.size)
    im = im.resize((round(im.width * k), round(im.height * k)))
    dr = ImageDraw.Draw(im)
    font = _font(15)
    s = k / doc["scale"]
    for p in pts:
        x, y = p["u"] * s, p["v"] * s
        dr.ellipse([x - 4, y - 4, x + 4, y + 4], outline=(255, 255, 255), width=2)
        txt = f"{p['z_true']:.1f}/{p['z_est']:.1f}" if p.get("z_est") is not None else f"{p['z_true']:.1f}/-"
        dr.text((x + 6, y - 8), txt, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0), font=font)
    dr.text((8, 8), f"{doc['model']}  真値/推定（カメラの奥行き m）", fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0),
            font=_font(20))
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_jpg, quality=88)
    return out_jpg


def ground_check(b: dict, entry: dict, doc: dict, dem: dict | None, scale_fix: float | None = None) -> dict:
    """写真の下の帯の深さを台帳の視点で世界に戻し、足元の地面の高さ（棟の z=0 から m）を読む。
    scale_fix（深さの倍率）があれば、深さをそれで割った値も出す。評価では使わない：比は距離で変わり
    （近い 0.8、遠い 0.3〜0.4）、遠い壁の比で近い地面を直すと逆に狂う（11/6 の実験）。"""
    from .landmarks import _face_ground
    from .schema import Measurement
    from .site import dem_sample

    cam = entry["camera"]
    w, h = cam["image_size"]
    R, t = np.array(cam["R_cv"], float), np.array(cam["t_cv"], float).ravel()
    C = -R.T @ t
    us, vs = np.meshgrid(np.linspace(GROUND_COLS[0] * w, GROUND_COLS[1] * w, 25), np.linspace(GROUND_BAND[0] * h, GROUND_BAND[1] * h, 7))
    us, vs = us.ravel(), vs.ravel()
    d = sample(doc, us, vs)
    Kinv = np.linalg.inv(R_.K_matrix(cam["focal_px"], w, h))
    rays = (Kinv @ np.vstack([us, vs, np.ones_like(us)])).T          # z=1 の向き（深さは z 方向）
    gz = Measurement.from_json(b.get("ground_z"))
    fg, ffid = _face_ground(b, C)
    out = {"photo": entry["photo"], "face": entry["face"], "camera_xyz": [round(float(v), 2) for v in C],
           "camera_height_reg_m": cam.get("camera_height_m"), "face_ground_offset": fg, "nearest_face": ffid,
           "ground_z": gz.value if gz.known else None}
    for tag, kfix in (("raw", 1.0), ("scale_fixed", scale_fix)):
        if kfix is None or not np.isfinite(kfix) or kfix <= 0:
            continue
        X = rays * (d / kfix)[:, None]
        Pw = (X - t) @ R                                               # R^T (X − t)
        ok = np.isfinite(Pw).all(axis=1) & (d / kfix < 60)
        if ok.sum() < 5:
            out[tag] = None
            continue
        Pw = Pw[ok]
        zg = float(np.median(Pw[:, 2]))
        dz = dem_sample(dem, Pw[:, 0], Pw[:, 1]) if dem is not None else np.full(len(Pw), np.nan)
        dem_rel = float(np.nanmedian(dz) - gz.value) if (gz.known and np.isfinite(dz).any()) else None
        dem_cam = None
        if dem is not None and gz.known:
            dc = dem_sample(dem, np.array([C[0]]), np.array([C[1]]))[0]
            dem_cam = None if not np.isfinite(dc) else float(dc - gz.value)
        hd = np.hypot(Pw[:, 0] - C[0], Pw[:, 1] - C[1])
        out[tag] = {"ground_z_rel": round(zg, 2), "ground_z_iqr": round(float(np.subtract(*np.percentile(Pw[:, 2], [75, 25]))), 2),
                    "n": int(ok.sum()), "horiz_dist_m": [round(float(np.percentile(hd, 10)), 1), round(float(np.percentile(hd, 90)), 1)],
                    "camera_height_m": round(float(C[2] - zg), 2),
                    "dem_rel_at_points": None if dem_rel is None else round(dem_rel, 2),
                    "dem_rel_at_camera": None if dem_cam is None else round(dem_cam, 2),
                    "minus_dem": None if dem_rel is None else round(zg - dem_rel, 2),
                    "minus_face_offset": None if fg is None else round(zg - fg, 2)}
    return out


def evaluate(models: list[str], buildings: list[str] | None = None, out_json: Path = Path("out/experiments/depth_eval.json"),
             run_missing: bool = True, draw_images: bool = True) -> dict:
    """登録済みの写真（台帳の entries、kind plane と pose）で各モデルを評価して JSON に書く。"""
    from .landmarks import focal_px_from_exif
    from .schema import load_building

    reg = R_.load_registry()
    idx = load_index(INDEX_PATH)
    ents = [e for e in reg["entries"] if (e.get("camera") or {}).get("R_cv")
            and (not buildings or e["building"] in buildings)]
    seen, uniq = set(), []
    for e in ents:
        if (e["building"], e["photo"]) not in seen:
            seen.add((e["building"], e["photo"]))
            uniq.append(e)
    blds = {bid: load_building(Path("data/buildings") / f"{bid}.json") for bid in {e["building"] for e in uniq}}
    try:
        from .site import load_dem

        dem = load_dem()
    except Exception:
        dem = None
    report = {"made_by": {"係": "depth（recorder/depth.py evaluate）", "models": {m: MODELS[m] for m in models}},
              "made_at": datetime.now().isoformat(timespec="seconds"),
              "truth": "台帳の視点（registry.json の camera）から目印（landmarks.catalog）と面の格子（面の内側 10〜90 %）までのカメラの奥行き",
              "ratio": "推定の深さ ÷ 真値（カメラの奥行き z）。spread＝1.4826×MAD÷中央値",
              "n_photos": len(uniq), "models": {}}
    for model in models:
        per, ground, secs, devs = [], [], [], set()
        for e in uniq:
            rec = R_.photo_record(e["photo"], idx)
            p = npz_path(e["building"], rec["id"], model)
            if not p.exists():
                if not run_missing:
                    continue
                s = predict(rec["id"], model, e["building"], idx)
                print(f"  {model} {rec['id']} {s['seconds']} s {s['device']} 焦点比 {s['focal_ratio']}")
            js = json.loads(p.with_suffix(".json").read_text(encoding="utf-8"))
            secs.append(js["seconds"])
            devs.add(js["device"])
            doc = load_npz(p)
            r = eval_photo(blds[e["building"]], e, doc, focal_px_from_exif(rec))
            per.append(r)
            if draw_images:
                draw(doc, rec, r["landmark_points"], p.with_suffix(".jpg"))
            if e["building"] == "tower75" and e["face"] in ("f19", "f20", "W"):
                ground.append(ground_check(blds[e["building"]], e, doc, dem))
        report["models"][model] = aggregate(per, model)
        report["models"][model].update({"seconds_per_photo": round(float(np.median(secs)), 2) if secs else None,
                                        "device": sorted(devs), "ground": ground,
                                        "per_photo": [{k: v for k, v in r.items() if not k.endswith("_points")} for r in per]})
        release()
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def aggregate(per: list[dict], model: str) -> dict:
    """写真ごとの結果 → 全体・棟別・距離帯別・見上げ角別。写真ごとの比の中央値の散らばりで判定する。"""
    out = {"n_photos": len(per)}
    for kind in ("landmark", "grid"):
        pts = [p for r in per for p in r[f"{kind}_points"]]
        photo_med = [r[kind]["all"]["median"] for r in per if r[kind]["all"]["median"] is not None]
        blk = {"points": summarize([p.get("ratio") for p in pts]), "photo_medians": summarize(photo_med),
               "within_photo_spread_median": (round(float(np.median([r[kind]["all"]["spread"] for r in per
                                                                       if r[kind]["all"]["spread"] is not None])), 3)
                                              if any(r[kind]["all"]["spread"] is not None for r in per) else None),
               "by_building": {}, "by_dist": {}, "by_elev": {}}
        for bid in sorted({r["building"] for r in per}):
            rs = [r for r in per if r["building"] == bid]
            blk["by_building"][bid] = {"points": summarize([p.get("ratio") for r in rs for p in r[f"{kind}_points"]]),
                                       "photo_medians": summarize([r[kind]["all"]["median"] for r in rs])}
        for *_, n in DIST_BANDS:
            blk["by_dist"][n] = summarize([p.get("ratio") for p in pts if band_of(p["z_true"], DIST_BANDS) == n])
        for *_, n in ELEV_BANDS:
            blk["by_elev"][n] = summarize([p.get("ratio") for p in pts if band_of(p["elev_deg"], ELEV_BANDS) == n])
        if per and "all_exif_focal" in per[0][kind]:
            blk["photo_medians_exif_focal"] = summarize([r[kind]["all_exif_focal"]["median"] for r in per])
        pm = blk["photo_medians"]
        blk["judge"] = judge(pm["spread"], None if pm["median"] is None else pm["median"] - 1)
        out[kind] = blk
    fe = [r["focal_err"] for r in per if r.get("focal_err") is not None]
    out["focal_err"] = None if not fe else {"n": len(fe), "median": round(float(np.median(fe)), 3),
                                            "q25": round(float(np.percentile(fe, 25)), 3), "q75": round(float(np.percentile(fe, 75)), 3),
                                            "abs_median": round(float(np.median(np.abs(fe))), 3)}
    return out
