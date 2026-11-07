"""写真の保護：顔・ナンバーの自動の箱と、灰色の箱＋理由の文字での塗りつぶし（11/8、K-104）。

決まり（data/reference/index.json の permission.interpretations）：
- 掲示物・人名・電話番号・顔・ナンバーは**灰色の箱＋理由の札**で塗る（箱は隠す範囲だけ。札は箱の左上の隅、11/8 F11）。ぼかし・モザイク（戻せる恐れ）や
  周りの模様で埋める（写っていない物を描く）ことはしない。画像の端の掲示物は切り落としてもよい。
- 塗るのは縮小版（係に見せる send と、提出用の public）にだけ。**元の写真は開いて読むだけで書かない。**
- 研究・研究室に関する物は塗りつぶしで済ませず、公開しない（publish.py が見る）。

箱の形（索引の `masks` の 1 件）：
    {"kind": "face"|"plate"|"poster"|"name"|"phone", "box": [x0, y0, x1, y1], "by": "auto"|"hogo"|"review", ...}
    box は**向きを直した（EXIF の回転を当てた）画像**の幅・高さに対する割合（0〜1）。縮小しても同じ値で使える。
    係が升で指した物は "cells": "C3:E5"（目印係と同じ 10×10 の升。列 A〜J・行 1〜10）も残す。

自動で見る物：
- 顔：OpenCV の YuNet（assets/opencv_zoo/face_detection_yunet_2023mar.onnx、scripts/fetch_yunet.py で取る）。
  モデルが無ければ箱を置かずに縮小版を作り、警告を返す（"faces": "no_model"）。
- ナンバー：**未対応**（"plates": "unsupported"）。日本のナンバーの形（白・黄・緑の 2:1 の板）を輪郭で探すと、
  建物の窓・タイル・看板を大量に拾い、測量に使う壁を塗ってしまう。学習済みの CC0／MIT の検出器も手元に無い。
  ナンバーは保護係（Haiku）の升と、Sonnet の見直しで拾う。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
YUNET_PATH = ROOT / "assets" / "opencv_zoo" / "face_detection_yunet_2023mar.onnx"
YUNET_ID = "yunet_2023mar"
SEND_LONG = 1600              # 係に見せる縮小版の長辺
SEND_QUALITY = 85
DETECT_LONG = 3200            # 顔を探す時の長辺（遠くの小さな顔も拾うため、縮小版より大きく見る）
FACE_SCORE = 0.7              # YuNet の確からしさの下限
FACE_PAD = 0.25               # 顔の箱を各側にこの割合だけ広げる（髪・耳まで覆う）
GRAY = (0x8a, 0x8a, 0x8a)     # 灰色の箱（RGB、#8a8a8a。不透明）
EDGE = (40, 40, 40)           # 箱の縁取り（2 px）
EDGE_PX = 2
TAG_BG = (30, 30, 30)         # 札の帯（濃い色）。文字は白
TAG_FG = (255, 255, 255)
TAG_H = 0.02                  # 札の文字の高さ＝写真の高さのこの割合（一定。箱の大きさに合わせない）
KINDS = {"face": "顔", "plate": "ナンバー", "poster": "掲示物", "name": "人名", "phone": "電話番号"}
GRID_N = 10
COLS = "ABCDEFGHIJ"


# --- 画像の読み書き（元の写真は読むだけ） ---------------------------------------------------------

def load_oriented(path: Path):
    """元の写真を開き、EXIF の向きを当てた RGB の PIL 画像を返す（元のファイルは書かない）。"""
    from PIL import Image, ImageOps

    try:
        import pillow_heif  # noqa: F401

        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    with Image.open(path) as im:
        return ImageOps.exif_transpose(im).convert("RGB")


def resize_long(im, long: int):
    from PIL import Image

    w, h = im.size
    s = long / max(w, h)
    if s >= 1:
        return im.copy()
    return im.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)


def save_jpeg(im, dst: Path, quality: int) -> None:
    """EXIF（GPS・撮影機器）を付けずに JPEG で書く。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f".{dst.name}.tmp")
    im.save(tmp, "JPEG", quality=quality, optimize=True)
    tmp.replace(dst)


# --- 升（目印係と同じ 10×10） ----------------------------------------------------------------------

def _cell(c: str) -> tuple[int, int]:
    c = c.strip().upper()
    if len(c) < 2 or c[0] not in COLS or not c[1:].isdigit() or not 1 <= int(c[1:]) <= GRID_N:
        raise ValueError(f"升の書き方が違う: {c!r}（例 C7。列 A〜J・行 1〜10）")
    return COLS.index(c[0]), int(c[1:]) - 1


def cells_to_box(cells: str) -> list[float]:
    """"C3" か "C3:E5"（左上の升:右下の升）→ [x0, y0, x1, y1]（割合）。順が逆でも並べ直す。"""
    parts = [p for p in str(cells).replace("-", ":").split(":") if p.strip()]
    if not 1 <= len(parts) <= 2:
        raise ValueError(f"升の範囲の書き方が違う: {cells!r}（例 C3:E5）")
    (c0, r0), (c1, r1) = _cell(parts[0]), _cell(parts[-1])
    ca, cb, ra, rb = min(c0, c1), max(c0, c1), min(r0, r1), max(r0, r1)
    return [ca / GRID_N, ra / GRID_N, (cb + 1) / GRID_N, (rb + 1) / GRID_N]


def norm_box(box) -> list[float]:
    x0, y0, x1, y1 = (float(v) for v in box)
    x0, x1 = sorted((min(max(x0, 0.0), 1.0), min(max(x1, 0.0), 1.0)))
    y0, y1 = sorted((min(max(y0, 0.0), 1.0), min(max(y1, 0.0), 1.0)))
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        raise ValueError(f"箱の大きさが 0: {box}")
    return [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)]


def grid_image(im):
    """保護係が升を指すための升目の絵（黄色の線と A〜J・1〜10）。入力は PIL、出力も PIL。"""
    from PIL import ImageDraw

    out = im.copy()
    d = ImageDraw.Draw(out)
    w, h = out.size
    for i in range(GRID_N + 1):
        x, y = round(i * w / GRID_N), round(i * h / GRID_N)
        d.line([(x, 0), (x, h - 1)], fill=(255, 255, 0), width=1)
        d.line([(0, y), (w - 1, y)], fill=(255, 255, 0), width=1)
    font = _font(max(14, h // 50))
    for i in range(GRID_N):
        x, y = int((i + 0.5) * w / GRID_N), int((i + 0.5) * h / GRID_N)
        d.text((x - 6, 2), COLS[i], fill=(255, 255, 0), font=font, stroke_width=2, stroke_fill=(0, 0, 0))
        d.text((3, y - 8), str(i + 1), fill=(255, 255, 0), font=font, stroke_width=2, stroke_fill=(0, 0, 0))
    return out


# --- 顔（YuNet）とナンバー --------------------------------------------------------------------------

def detect_faces(im, model: Path = YUNET_PATH, score: float = FACE_SCORE) -> list[dict] | None:
    """顔の箱（割合）を返す。モデルが無ければ None（呼ぶ側が警告を出す）。"""
    if not Path(model).exists():
        return None
    import cv2

    small = resize_long(im, DETECT_LONG)
    w, h = small.size
    bgr = np.ascontiguousarray(np.asarray(small)[:, :, ::-1])
    det = cv2.FaceDetectorYN.create(str(model), "", (w, h), score, 0.3, 5000)
    det.setInputSize((w, h))
    _ok, faces = det.detect(bgr)
    out = []
    for f in (faces if faces is not None else []):
        x, y, fw, fh, sc = float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[-1])
        px, py = fw * FACE_PAD, fh * FACE_PAD
        out.append({"kind": "face", "box": norm_box([(x - px) / w, (y - py) / h, (x + fw + px) / w, (y + fh + py) / h]),
                    "by": "auto", "score": round(sc, 3), "model": YUNET_ID})
    return out


def detect_plates(im) -> None:
    """ナンバーの自動検出は未対応（理由はこのモジュールの説明）。常に None。"""
    return None


def auto_masks(im, model: Path = YUNET_PATH) -> tuple[list[dict], dict]:
    """自動の箱と、その記録（索引の `auto_mask`）を返す。"""
    status: dict[str, Any] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "plates": "unsupported"}
    masks: list[dict] = []
    try:
        faces = detect_faces(im, model)
    except Exception as e:  # noqa: BLE001  検出の失敗で取り込みを止めない（警告に残す）
        faces, status["faces"], status["error"] = None, "error", f"{type(e).__name__}: {e}"
    else:
        status["faces"] = YUNET_ID if faces is not None else "no_model"
    if faces:
        masks += faces
    status["n_faces"] = len(faces or [])
    return masks, status


# --- 塗りつぶしと切り落とし --------------------------------------------------------------------------

_FONT_CACHE: dict[int, Any] = {}


def _font(size: int):
    from PIL import ImageFont

    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    f = None
    for name in ("YuGothB.ttc", "YuGothM.ttc", "meiryo.ttc", "msgothic.ttc", "NotoSansCJK-Regular.ttc"):
        try:
            f = ImageFont.truetype(name, size)
            break
        except OSError:
            continue
    _FONT_CACHE[size] = f or ImageFont.load_default()
    return _FONT_CACHE[size]


def _px_box(box, w: int, h: int) -> tuple[int, int, int, int]:
    """割合の箱 → px の箱（右・下の端を含まない）。最低 1 px。"""
    x0, y0, x1, y1 = norm_box(box)
    a, b = int(x0 * w), int(y0 * h)
    return a, b, max(a + 1, int(round(x1 * w))), max(b + 1, int(round(y1 * h)))


def group_masks(masks: list[dict], w: int, h: int) -> list[dict]:
    """同じ種類で重なる・接する箱を 1 つにまとめる（11/8 F11）。
    戻り値：[{"kind", "rects": [px の箱…]}]。まとめた物は箱の和（外接の長方形ではない）として塗るので、
    箱と箱の間の壁は塗らない。接するは 1 px の隙間まで（升の境で丸めの差が出るため）。"""
    rects = [(m.get("kind"), _px_box(m["box"], w, h)) for m in masks]
    parent = list(range(len(rects)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (ki, a) in enumerate(rects):
        for j in range(i + 1, len(rects)):
            kj, b = rects[j]
            if ki == kj and a[0] <= b[2] + 1 and b[0] <= a[2] + 1 and a[1] <= b[3] + 1 and b[1] <= a[3] + 1:
                parent[find(i)] = find(j)
    groups: dict[int, dict] = {}
    for i, (k, r) in enumerate(rects):
        g = groups.setdefault(find(i), {"kind": k, "rects": []})
        g["rects"].append(r)
    return list(groups.values())


def _tag(d, x: int, y: int, label: str, w: int, h: int, placed: list | None = None) -> tuple[int, int, int, int]:
    """札：濃い帯に白の文字。文字の高さは写真の高さの TAG_H（一定）。箱の左上の隅に置き、写真の外に出る時だけ内へずらす。
    placed（先に置いた札の範囲）と重なる時は、重なった札のすぐ下へずらす（隣の箱の札で文字が欠けないように）。"""
    size = max(10, round(h * TAG_H))
    f = _font(size)
    tb = d.textbbox((0, 0), label, font=f)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    pad = max(2, size // 4)
    bw, bh = tw + 2 * pad, th + 2 * pad
    x = min(max(0, x), max(0, w - bw))
    y = min(max(0, y), max(0, h - bh))
    for _ in range(50):
        hit = next((q for q in placed or [] if x < q[2] and q[0] < x + bw and y < q[3] and q[1] < y + bh), None)
        if hit is None:
            break
        y = hit[3]
    d.rectangle((x, y, x + bw - 1, y + bh - 1), fill=TAG_BG)
    d.text((x + pad - tb[0], y + pad - tb[1]), label, fill=TAG_FG, font=f)
    rect = (x, y, x + bw, y + bh)
    if placed is not None:
        placed.append(rect)
    return rect


def drawn_masks(masks: list[dict]) -> list[dict]:
    """描く箱：確かめていない自動の顔の箱（by: auto のまま）は描かない（11/8 F11 追記、利用者「顔はあかんやろ」：
    石垣の模様に「顔」の札が出た）。見直しが切り出しを見て confirm した物は by: review になって描かれる。索引には残す。"""
    return [m for m in masks if m.get("by") != "auto"]


def unconfirmed_auto(masks: list[dict]) -> int:
    return sum(1 for m in masks if m.get("by") == "auto")


def paint_masks(im, masks: list[dict]):
    """隠す範囲を塗る（入力の PIL 画像はそのまま、塗った写しを返す。11/8 F11 で見た目を改めた）。
    (a) 隠す範囲だけを不透明の灰色 #8a8a8a で塗る (b) 2 px の濃い縁取り (c) 箱の左上の隅に札
    （濃い帯に白の文字、文字の高さは写真の高さの 2 %。箱からはみ出してよい）(d) 同じ種類で重なる・接する箱は
    1 つにまとめ、札は 1 回 (e) 札の文字は KINDS（顔・ナンバー・人名・掲示物・電話番号）。"""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    out = im.copy()
    if not masks:
        return out
    w, h = out.size
    groups = group_masks(masks, w, h)
    d = ImageDraw.Draw(out)
    for g in groups:
        bx0, by0 = min(r[0] for r in g["rects"]), min(r[1] for r in g["rects"])
        bx1, by1 = max(r[2] for r in g["rects"]), max(r[3] for r in g["rects"])
        gw, gh = bx1 - bx0, by1 - by0
        sel = Image.new("L", (gw + 2, gh + 2), 0)   # まとめた形の外接の範囲だけで描く（周りに 1 px の余白）
        sd = ImageDraw.Draw(sel)
        for r in g["rects"]:
            sd.rectangle((r[0] - bx0 + 1, r[1] - by0 + 1, r[2] - bx0, r[3] - by0), fill=255)
        # 縁取り：まとめた形の外周の内側 EDGE_PX。外へ出さないので塗る範囲は広がらない
        inner = sel.filter(ImageFilter.MinFilter(2 * EDGE_PX + 1))
        edge = ImageChops.subtract(sel, inner).crop((1, 1, gw + 1, gh + 1))
        fill = sel.crop((1, 1, gw + 1, gh + 1))
        out.paste(GRAY, (bx0, by0, bx1, by1), fill)
        out.paste(EDGE, (bx0, by0, bx1, by1), edge)
    placed: list = []
    anchors = []
    for g in groups:   # 札は塗りと縁取りの後（隣の箱の塗りに消されないように）。上から、同じ高さなら左から置く
        label = KINDS.get(g["kind"], str(g["kind"] or ""))
        if label:
            r = min(g["rects"], key=lambda r: (r[1], r[0]))   # いちばん上（同じなら左）の箱の左上
            anchors.append((r[1], r[0], label))
    for y, x, label in sorted(anchors):
        _tag(d, x, y, label, w, h, placed)
    return out


def apply_crop(im, crop):
    """切り落とし：残す範囲 [x0, y0, x1, y1]（割合）。"""
    x0, y0, x1, y1 = norm_box(crop)
    w, h = im.size
    return im.crop((int(x0 * w), int(y0 * h), int(round(x1 * w)), int(round(y1 * h))))


# --- 係に見せる縮小版（send） ----------------------------------------------------------------------

def make_send(src: Path, dst: Path, extra_masks: list[dict] | None = None, model: Path = YUNET_PATH,
              long: int = SEND_LONG, quality: int = SEND_QUALITY) -> dict:
    """元の写真 src から、顔の箱を塗った縮小版 dst を作る。戻り値 {"masks"（auto だけ）, "auto_mask", "warnings"}。
    extra_masks（係が指した升など）も一緒に塗る。元の写真は開いて読むだけ。"""
    im = load_oriented(Path(src))
    masks, status = auto_masks(im, model)
    small = resize_long(im, long)
    save_jpeg(paint_masks(small, drawn_masks(masks + list(extra_masks or []))), Path(dst), quality)
    warnings = []
    if status.get("faces") == "no_model":
        warnings.append(f"顔のモデルが無いので箱なしで作りました（scripts/fetch_yunet.py で取ってください）: {Path(src).name}")
    if status.get("faces") == "error":
        warnings.append(f"顔の検出に失敗し、箱なしで作りました（{status.get('error')}）: {Path(src).name}")
    status["size"] = list(small.size)
    return {"masks": masks, "auto_mask": status, "warnings": warnings}
