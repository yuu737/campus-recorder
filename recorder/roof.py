"""航空写真（国土地理院 z18）から屋上を読む。

1. 足元（PLATEAU/OSM）を囲む範囲を切り出して 6 倍に拡大し、足元（赤）と既存の屋上部品（水色）を重ねる。
2. 屋上の縁：足元の形を平行移動 (dx, dy) で −6〜+6 m 走査し、写真の縁（明るさの勾配）と足元の辺が
   いちばん重なる位置を取る。上位 3 つと確からしさを出し、1 位と 2 位が近ければ ambiguous。
   人が縁の点を指した（--roof-edge）ならそれを優先する。
3. 倒れ込み：屋上の縁 − 足元 = lean_m。率 = |lean| / 屋上の高さ（地面から）。
   検算に近くの低い建物（高さ 5 m 未満）で同じ走査をし、ずれが 1 m 未満なら写真の位置合わせは良い。
4. 設備：屋上の縁の内側で、屋根面と明るさが大きく違う塊を拾い、w×d（m）と中心（倒れ込みを引いた足元の座標）を出す。
   人が指した物（--units）があればそれを優先する。

ここで出す値は「航空写真で読んだ推定」。棟 JSON に書くときは estimated とし、method にこの道具と
roof.json を書く（設計係の仕事）。
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np

from . import site

SCALE = 6                 # 切り出しの拡大率
MARGIN_PX = 20            # 足元の外の余白（元の画素）
SHIFT_M = 6.0             # 走査の範囲 ±m
STEP_M = 0.25             # 走査の刻み
SAMPLE_M = 0.25           # 辺の上の点の間隔
AMBIGUOUS_RATIO = 0.92    # 2 位の点 / 1 位の点 がこれ以上なら ambiguous
CAND_SEP_M = 1.0          # 候補どうしはこれ以上離す（同じ山を 2 回数えない）
LEAN_RATIO_RANGE = (0.05, 0.25)
LOW_H_MAX = 5.0           # 検算に使う低い建物の高さの上限（PLATEAU の参考値）
LOW_OK_M = 1.0            # 低い建物のずれがこれ未満なら位置合わせは良い
UNIT_MIN_M = 1.0          # これ未満の塊は捨てる
UNIT_MAX_AREA_M2 = 30.0   # これより大きい塊は設備ではなく屋根の別の面（large_regions に回す）
UNIT_INSET_M = 1.0        # 屋上の縁から内側にこれだけ離れた所だけ見る（パラペットと影を外す）


# ---------------------------------------------------------------- 座標の対応

class Frame:
    """切り出し画像の画素 ⇔ 原点からの m。航空写真は北が上の Web メルカトルなので、
    数十 m の範囲では 1 次（アフィン）で十分。site.aerial_xy_to_px で係数を作る。"""

    def __init__(self, meta: dict, x0: float, y0: float, crop_px0: float = 0.0, crop_py0: float = 0.0):
        p0 = np.array(site.aerial_xy_to_px(meta, x0, y0))
        px = np.array(site.aerial_xy_to_px(meta, x0 + 10.0, y0))
        py = np.array(site.aerial_xy_to_px(meta, x0, y0 + 10.0))
        self.A = np.column_stack([(px - p0) / 10.0, (py - p0) / 10.0])   # d(px)/d(xy)
        self.Ainv = np.linalg.inv(self.A)
        self.x0, self.y0 = x0, y0
        self.p0 = p0 - np.array([crop_px0, crop_py0])
        self.px_per_m = float(math.sqrt(abs(np.linalg.det(self.A))))

    def to_px(self, xy) -> np.ndarray:
        xy = np.asarray(xy, dtype=float)
        return (xy - [self.x0, self.y0]) @ self.A.T + self.p0

    def to_xy(self, pxy) -> np.ndarray:
        pxy = np.asarray(pxy, dtype=float)
        return (pxy - self.p0) @ self.Ainv.T + [self.x0, self.y0]

    @classmethod
    def north_up(cls, px_per_m: float, px0: float, py0: float) -> "Frame":
        """北が上・等方の対応（合成画像のテスト用）。原点 (0, 0) m が画素 (px0, py0)。"""
        f = cls.__new__(cls)
        f.A = np.array([[px_per_m, 0.0], [0.0, -px_per_m]])
        f.Ainv = np.linalg.inv(f.A)
        f.x0, f.y0 = 0.0, 0.0
        f.p0 = np.array([px0, py0], dtype=float)
        f.px_per_m = float(px_per_m)
        return f


# ---------------------------------------------------------------- 縁の走査

def _gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 3:
        img = img[..., :3].astype(float) @ [0.299, 0.587, 0.114]
    return img.astype(np.float32)


def _gradient(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    import cv2

    g = cv2.GaussianBlur(gray, (3, 3), 0.7)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3) / 8.0
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3) / 8.0
    return gx, gy


def _bilinear(a: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    h, w = a.shape
    x = np.clip(x, 0, w - 1.001)
    y = np.clip(y, 0, h - 1.001)
    x0 = np.floor(x).astype(int)
    y0 = np.floor(y).astype(int)
    fx, fy = x - x0, y - y0
    return (a[y0, x0] * (1 - fx) * (1 - fy) + a[y0, x0 + 1] * fx * (1 - fy)
            + a[y0 + 1, x0] * (1 - fx) * fy + a[y0 + 1, x0 + 1] * fx * fy)


def _edge_samples(poly: list, step_m: float = SAMPLE_M) -> tuple[np.ndarray, np.ndarray]:
    """足元の辺の上の点（m）と、その点での辺の法線（m 座標の単位ベクトル）。"""
    pts, nrm = [], []
    P = np.asarray(poly, dtype=float)
    for i in range(len(P)):
        a, b = P[i], P[(i + 1) % len(P)]
        d = b - a
        L = float(np.hypot(*d))
        if L < 1e-6:
            continue
        n = np.array([d[1], -d[0]]) / L
        k = max(1, int(L / step_m))
        for t in (np.arange(k) + 0.5) / k:
            pts.append(a + d * t)
            nrm.append(n)
    return np.array(pts), np.array(nrm)


def _group_samples(groups: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """塊ごとの辺の点・法線・高さの比 k（その塊のずれ＝走査のずれ × k）。
    低い塊の辺のうち、高い塊の辺に重なる所（0.4 m 以内）は高い塊の縁が見えるので外す。"""
    P_all, N_all, K_all = [], [], []
    for g in groups:
        pts, nrm = _edge_samples(g["polygon"])
        if not len(pts):
            continue
        keep = np.ones(len(pts), bool)
        for o in groups:
            if o is g or o["k"] <= g["k"]:
                continue
            Q = np.asarray(o["polygon"], dtype=float)
            keep &= np.array([_dist_to_boundary(p, Q) > 0.4 for p in pts])
        P_all.append(pts[keep]); N_all.append(nrm[keep]); K_all.append(np.full(keep.sum(), g["k"]))
    return np.concatenate(P_all), np.concatenate(N_all), np.concatenate(K_all)


def scan_offsets(gray: np.ndarray, frame: Frame, poly: list | None = None, shift_m: float = SHIFT_M,
                 step_m: float = STEP_M, groups: list[dict] | None = None) -> dict:
    """足元の形を (dx, dy) m ずらしたとき、辺に沿った「辺に直交する勾配の強さ」の平均。
    groups（塊ごとの polygon と高さの比 k）を渡すと、塊ごとに (dx, dy)×k ずらす（低い塊は倒れ込みが小さい）。
    返り値 {"dx": 1 次元, "dy": 1 次元, "score": 2 次元 [iy, ix]}。"""
    gx, gy = _gradient(gray)
    if groups is None:
        groups = [{"polygon": poly, "k": 1.0}]
    pts, nrm, kk = _group_samples(groups)
    base = frame.to_px(pts)
    # 法線を画素の向きへ（y が下向きになる）
    npx = nrm @ frame.A.T
    npx /= np.linalg.norm(npx, axis=1, keepdims=True)
    offs = np.arange(-shift_m, shift_m + 1e-9, step_m)
    score = np.zeros((len(offs), len(offs)))
    for iy, dy in enumerate(offs):
        for ix, dx in enumerate(offs):
            d = np.array([dx, dy]) @ frame.A.T
            q = base + kk[:, None] * d
            vx = _bilinear(gx, q[:, 0], q[:, 1])
            vy = _bilinear(gy, q[:, 0], q[:, 1])
            score[iy, ix] = float(np.mean(np.abs(vx * npx[:, 0] + vy * npx[:, 1])))
    return {"dx": offs, "dy": offs, "score": score}


def pick_candidates(scan: dict, n: int = 3, sep_m: float = CAND_SEP_M) -> list[dict]:
    """点の高い順に、互いに sep_m 以上離れた候補を n 個。"""
    S = scan["score"]
    order = np.argsort(S, axis=None)[::-1]
    out: list[dict] = []
    for k in order:
        iy, ix = np.unravel_index(k, S.shape)
        dx, dy = float(scan["dx"][ix]), float(scan["dy"][iy])
        if any(math.hypot(dx - c["dx"], dy - c["dy"]) < sep_m for c in out):
            continue
        out.append({"dx": round(dx, 2), "dy": round(dy, 2), "score": round(float(S[iy, ix]), 3)})
        if len(out) >= n:
            break
    if out:
        top = out[0]["score"] or 1.0
        med = float(np.median(S))
        for c in out:
            c["rel"] = round(c["score"] / top, 3)
            # 確からしさ：その点が走査全体の中央値からどれだけ抜けているか（1 位 = 1）
            c["confidence"] = round(max(0.0, (c["score"] - med) / max(top - med, 1e-9)), 3)
    return out


def is_ambiguous(cands: list[dict], ratio: float = AMBIGUOUS_RATIO) -> bool:
    return len(cands) >= 2 and cands[1]["score"] >= ratio * cands[0]["score"]


def _dist_to_boundary(p: np.ndarray, poly: np.ndarray) -> float:
    best = math.inf
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        d = b - a
        t = float(np.clip(np.dot(p - a, d) / max(np.dot(d, d), 1e-12), 0, 1))
        best = min(best, float(np.hypot(*(a + d * t - p))))
    return best


def offset_from_points(poly: list | None, points: list[tuple[float, float]], shift_m: float = SHIFT_M,
                       step_m: float = 0.05, groups: list[dict] | None = None) -> dict:
    """人が指した屋上の縁の点（写真の上の m）に、足元の辺（塊ごとに ×k）がいちばん近づく平行移動。"""
    if groups is None:
        groups = [{"polygon": poly, "k": 1.0}]
    Gs = [(np.asarray(g["polygon"], dtype=float), g["k"]) for g in groups]
    pts = np.asarray(points, dtype=float)

    def err(dx, dy):
        return float(np.mean([min(_dist_to_boundary(p, P + np.array([dx, dy]) * k) for P, k in Gs) for p in pts]))

    best = None
    for dy in np.arange(-shift_m, shift_m + 1e-9, 0.25):
        for dx in np.arange(-shift_m, shift_m + 1e-9, 0.25):
            e = err(dx, dy)
            if best is None or e < best[0]:
                best = (e, dx, dy)
    # 細かく詰める
    _, bx, by = best
    for dy in np.arange(by - 0.25, by + 0.25 + 1e-9, step_m):
        for dx in np.arange(bx - 0.25, bx + 0.25 + 1e-9, step_m):
            e = err(dx, dy)
            if e < best[0]:
                best = (e, dx, dy)
    return {"dx": round(float(best[1]), 2), "dy": round(float(best[2]), 2), "mean_dist_m": round(float(best[0]), 3)}


# ---------------------------------------------------------------- 設備

def _axes(poly: list) -> tuple[np.ndarray, np.ndarray]:
    """棟の向き：いちばん長い辺の向き u と、それに直交する v（m 座標の単位ベクトル）。"""
    P = np.asarray(poly, dtype=float)
    best, u = -1.0, np.array([1.0, 0.0])
    for i in range(len(P)):
        d = P[(i + 1) % len(P)] - P[i]
        L = float(np.hypot(*d))
        if L > best:
            best, u = L, d / L
    if u[0] < 0:
        u = -u
    return u, np.array([-u[1], u[0]])


def find_units(gray: np.ndarray, frame: Frame, roofs: list[dict],
               inset_m: float = UNIT_INSET_M, min_m: float = UNIT_MIN_M, max_area_m2: float = UNIT_MAX_AREA_M2,
               k_mad: float = 3.5, min_contrast: float = 22.0) -> tuple[list[dict], list[dict], list[dict]]:
    """屋上（roofs: 塊ごとの {"id","polygon"（写真の上の位置 m）,"lean","k"}）の縁から inset_m 内側で、
    その塊の屋根面の明るさの中央値から大きく違う塊。返り値 (設備, 大きすぎて設備ではない領域, 塊ごとの明るさ)。"""
    import cv2

    h, w = gray.shape
    rr = max(1, int(round(inset_m * frame.px_per_m)))
    ker = np.ones((2 * rr + 1, 2 * rr + 1), np.uint8)
    taken = np.zeros((h, w), bool)
    units: list[dict] = []
    large: list[dict] = []
    stats_all: list[dict] = []
    ref = None   # 主の屋根面（k がいちばん 1 に近い塊）の明るさ
    mains = [r for r in roofs if not r.get("part")]
    if mains:
        r0 = min(mains, key=lambda t: abs(t.get("k", 1.0) - 1.0))
        m0 = np.zeros((h, w), np.uint8)
        cv2.fillPoly(m0, [np.round(frame.to_px(r0["polygon"])).astype(np.int32)], 1)
        v0 = gray[cv2.erode(m0, ker) > 0]
        if v0.size >= 20:
            md0 = float(np.median(v0))
            ref = (md0, float(np.median(np.abs(v0 - md0))) * 1.4826)
    for r in sorted(roofs, key=lambda t: -t.get("k", 1.0)):     # 高い塊から。低い塊は高い塊に隠れた所を除く
        m = np.zeros((h, w), np.uint8)
        cv2.fillPoly(m, [np.round(frame.to_px(r["polygon"])).astype(np.int32)], 1)
        own = (m > 0) & ~taken
        taken |= m > 0
        inner = cv2.erode(own.astype(np.uint8), ker) > 0
        vals = gray[inner]
        if r.get("part") and ref is not None:
            # 部品（階段塔など）は小さく中身が混ざるので、自分の中央値ではなく主の屋根面の明るさで比べる
            if not inner.any():
                stats_all.append({"id": r.get("id"), "roof_median": None})
                continue
            med, mad = ref
        elif vals.size < 20:
            stats_all.append({"id": r.get("id"), "roof_median": None})
            continue
        else:
            med = float(np.median(vals))
            mad = float(np.median(np.abs(vals - med))) * 1.4826
        thr = max(min_contrast, k_mad * mad)
        stats_all.append({"id": r.get("id"), "roof_median": round(med, 1), "roof_mad": round(mad, 1), "threshold": round(thr, 1),
                          **({"from": "主の屋根面"} if r.get("part") else {})})
        u, v = _axes(r["polygon"])
        lean = r["lean"]
        for sign, label in ((1, "明るい（白い箱か明るい屋根材）"), (-1, "暗い（影か暗い物）")):
            sel = ((sign * (gray - med)) > thr) & inner
            n, lab, _, _ = cv2.connectedComponentsWithStats(sel.astype(np.uint8), connectivity=8)
            for i in range(1, n):
                ys, xs = np.nonzero(lab == i)
                if xs.size < 2:
                    continue
                corners = np.concatenate([np.column_stack([xs + ox, ys + oy]) for ox in (-0.5, 0.5) for oy in (-0.5, 0.5)])
                xy = frame.to_xy(corners)
                pu, pv = xy @ u, xy @ v
                bw, bd = float(pu.max() - pu.min()), float(pv.max() - pv.min())
                # 大きさは 2 次モーメントから（矩形なら 幅² = 12×分散）。斜めの画素の外接箱で膨らまない
                cxy = frame.to_xy(np.column_stack([xs, ys]).astype(float))
                pix2 = 1.0 / frame.px_per_m ** 2 / 12.0
                wu = float(math.sqrt(12 * (np.var(cxy @ u) + pix2)))
                dv = float(math.sqrt(12 * (np.var(cxy @ v) + pix2)))
                if wu < min_m or dv < min_m:
                    continue
                c = frame.to_xy(np.array([[xs.mean(), ys.mean()]]))[0]
                d = {"cx": round(float(c[0] - lean[0]), 2), "cy": round(float(c[1] - lean[1]), 2),
                     "cx_aerial": round(float(c[0]), 2), "cy_aerial": round(float(c[1]), 2),
                     "w": round(wu, 2), "d": round(dv, 2), "bbox_w": round(bw, 2), "bbox_d": round(bd, 2), "axis_u": [round(float(u[0]), 4), round(float(u[1]), 4)],
                     "area_m2": round(xs.size / frame.px_per_m ** 2, 2),
                     "brightness": round(float(gray[ys, xs].mean()), 1),
                     "contrast": round(float(gray[ys, xs].mean() - med), 1),
                     "block": r.get("id"), "note": label, "source": "auto"}
                if d["area_m2"] > max_area_m2:
                    d["note"] = "設備にしては大きい：" + ("影（高い塊・塔の影か）" if sign < 0 else "明るい屋根材の面")
                    large.append(d)
                else:
                    units.append(d)
    units.sort(key=lambda d: (str(d["block"]), -d["area_m2"]))
    return units, large, stats_all


def parse_pairs(s: str | None, n: int) -> list[tuple[float, ...]]:
    if not s:
        return []
    out = []
    for chunk in s.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        v = tuple(float(t) for t in chunk.split(","))
        if len(v) != n:
            raise ValueError(f"{n} 個の数をカンマで区切る: {chunk!r}")
        out.append(v)
    return out


# ---------------------------------------------------------------- 棟 JSON から

def _val(x):
    if isinstance(x, dict):
        return x.get("value")
    return x


def roof_height(b: dict) -> tuple[float | None, str]:
    """屋上の高さ（地面から m）。parapet_top が地面からの値（z=0＝地面）ならそのまま、標高なら ground_z を引く。"""
    pt = _val(b.get("parapet_top"))
    gz = _val(b.get("ground_z"))
    if pt is None:
        return None, "parapet_top が無い"
    if gz is not None and pt > gz:
        return float(pt - gz), "parapet_top（標高）− ground_z"
    return float(pt), "parapet_top（z=0 が地面の棟座標）"


def rooftop_parts(b: dict) -> list[dict]:
    """既存の部品のうち屋上にある物（z0 が屋上の高さの 8 割以上）。{"name","polygon","z1"}。"""
    h, _ = roof_height(b)
    out = []
    for p in b.get("parts") or []:
        poly = p.get("polygon")
        z0 = _val(p.get("z0"))
        if poly and z0 is not None and h and z0 >= 0.8 * h:
            out.append({"name": p.get("name"), "polygon": poly, "z1": _val(p.get("z1")) or z0})
    return out


def tall_parts(b: dict) -> list[dict]:
    """塊の屋上より高く上がる部品（polygon と z1 があり、z1 が屋上の高さより上）。"""
    h, _ = roof_height(b)
    out = []
    for p in b.get("parts") or []:
        z1 = _val(p.get("z1"))
        z0 = _val(p.get("z0")) or 0.0
        if p.get("polygon") and z1 and h and z1 > h + 0.5 and z0 < 0.8 * h:   # 屋上に載る部品（塔屋など）は確かめる側なので入れない
            out.append({"id": p.get("name") or p.get("type"), "polygon": p["polygon"], "top_z": float(z1)})
    return out


def pick_low_building(site_doc: dict, near_xy, exclude: set[str] = frozenset(), h_max: float = LOW_H_MAX,
                      min_side_m: float = 4.0) -> dict | None:
    """近くの高さ 5 m 未満（PLATEAU の参考値）の建物を 1 つ。選ぶだけで、値は棟 JSON に入れない。"""
    best = None
    for s in site_doc.get("buildings") or []:
        h = (s.get("reference") or {}).get("plateau_height_m")
        fp = s.get("footprint_osm") or []
        if h is None or h >= h_max or not fp or s.get("id") in exclude:
            continue
        ring = np.asarray(fp[0], dtype=float)
        ext = ring.max(axis=0) - ring.min(axis=0)
        if ext.min() < min_side_m:
            continue
        c = ring.mean(axis=0)
        d = float(np.hypot(*(c - np.asarray(near_xy))))
        if best is None or d < best[0]:
            best = (d, s, ring)
    if not best:
        return None
    d, s, ring = best
    return {"id": s["id"], "plateau_height_m": s["reference"]["plateau_height_m"], "distance_m": round(d, 1),
            "polygon": ring.tolist()}


# ---------------------------------------------------------------- 本体

def _crop(img: np.ndarray, meta: dict, polys: list[list], margin_px: int) -> tuple[np.ndarray, Frame, tuple[int, int]]:
    pts = np.concatenate([np.array([site.aerial_xy_to_px(meta, x, y) for x, y in p]) for p in polys])
    x0 = max(0, int(math.floor(pts[:, 0].min())) - margin_px)
    y0 = max(0, int(math.floor(pts[:, 1].min())) - margin_px)
    x1 = min(img.shape[1], int(math.ceil(pts[:, 0].max())) + margin_px)
    y1 = min(img.shape[0], int(math.ceil(pts[:, 1].max())) + margin_px)
    c = np.asarray(polys[0], dtype=float).mean(axis=0)
    fr = Frame(meta, float(c[0]), float(c[1]), x0 + 0.5, y0 + 0.5)   # 配列の添字＝画素の中心
    return img[y0:y1, x0:x1], fr, (x0, y0)


def make_groups(footprint: list, height_m: float | None, blocks: list[dict] | None = None) -> list[dict]:
    """塊（blocks の polygon と top_z）があれば塊ごと、無ければ足元 1 つ。k = 塊の高さ / 屋上の高さ。"""
    out = []
    for bl in blocks or []:
        tz = _val(bl.get("top_z"))
        if bl.get("polygon") and tz and height_m:
            out.append({"id": bl.get("id"), "polygon": bl["polygon"], "top_z": float(tz), "k": float(tz) / height_m})
    return out or [{"id": None, "polygon": footprint, "top_z": height_m, "k": 1.0}]


def analyze_image(img: np.ndarray, frame: Frame, footprint: list, height_m: float | None,
                  roof_edge_pts=None, units_in=None, blocks: list[dict] | None = None,
                  tall_parts: list[dict] | None = None) -> dict:
    """切り出し済みの画像（RGB か灰色）と座標の対応から、縁・倒れ込み・設備を読む。テストからも呼ぶ。
    lean_m は屋上の高さ（height_m）の所のずれ。塊ごとのずれは高さに比例（lean_by_block）。"""
    import cv2

    gray = _gray(img)
    groups = make_groups(footprint, height_m, blocks)
    scan = scan_offsets(gray, frame, groups=groups)
    cands = pick_candidates(scan)
    amb = is_ambiguous(cands)
    res: dict = {"roof_edge_offset_candidates": cands, "ambiguous": amb}
    if roof_edge_pts:
        man = offset_from_points(None, roof_edge_pts, groups=groups)
        lean = (man["dx"], man["dy"])
        res["roof_edge_source"] = "manual"
        res["roof_edge_manual"] = {"points": [list(p) for p in roof_edge_pts], **man}
        res["ambiguous"] = False
        res["ambiguous_auto"] = amb
    else:
        lean = (cands[0]["dx"], cands[0]["dy"]) if cands else (0.0, 0.0)
        res["roof_edge_source"] = "auto"
    res["lean_m"] = [round(float(lean[0]), 2), round(float(lean[1]), 2)]
    L = float(math.hypot(*lean))
    res["lean_abs_m"] = round(L, 2)
    res["lean_dir_deg"] = round(math.degrees(math.atan2(lean[0], lean[1])) % 360, 1)   # 北から時計回り
    warns = []
    if height_m:
        ratio = L / height_m
        res["lean_ratio"] = round(ratio, 4)
        if not (LEAN_RATIO_RANGE[0] <= ratio <= LEAN_RATIO_RANGE[1]):
            warns.append(f"倒れ込み率 {ratio:.3f} が {LEAN_RATIO_RANGE[0]}〜{LEAN_RATIO_RANGE[1]} の外：写真の位置ずれか縁の読み違いの疑い")
    else:
        res["lean_ratio"] = None
        warns.append("屋上の高さが無いので倒れ込み率を出せない")
    if amb and not roof_edge_pts:
        warns.append("縁の候補の 1 位と 2 位が近い（ambiguous）。--roof-edge で縁の点を指すと決まる")
    roofs = []
    for g in groups:
        lg = (lean[0] * g["k"], lean[1] * g["k"])
        roofs.append({"id": g["id"], "k": g["k"], "lean": lg,
                      "polygon": (np.asarray(g["polygon"], dtype=float) + lg).tolist()})
    # 塊より高い部品（階段塔など）：位置が写真と合うか分からないので、設備の検出には使わず「その上に載っていたら」の印だけ付ける
    parts_sh = []
    for tp in tall_parts or []:
        if height_m:
            k = tp["top_z"] / height_m
            lg = (lean[0] * k, lean[1] * k)
            parts_sh.append({"id": tp["id"], "top_z": tp["top_z"], "lean": lg,
                             "polygon": (np.asarray(tp["polygon"], dtype=float) + lg).tolist()})
    res["tall_parts_aerial"] = [{"id": t["id"], "top_z": t["top_z"], "lean_m": [round(t["lean"][0], 2), round(t["lean"][1], 2)],
                                 "polygon": [[round(x, 2), round(y, 2)] for x, y in t["polygon"]]} for t in parts_sh]
    res["lean_by_block"] = [{"id": r["id"], "top_z": g["top_z"], "lean_m": [round(r["lean"][0], 2), round(r["lean"][1], 2)]}
                            for r, g in zip(roofs, groups)]
    res["roof_polygon_aerial"] = [[[round(x, 2), round(y, 2)] for x, y in r["polygon"]] for r in roofs]

    def lean_at(x, y):
        for r in sorted(roofs, key=lambda r: -r["k"]):
            if cv2.pointPolygonTest(np.asarray(r["polygon"], np.float32), (float(x), float(y)), False) >= 0:
                return r["lean"], r["id"]
        return lean, None

    auto, large, stats = find_units(gray, frame, roofs)
    for u in auto + large:
        for t in parts_sh:
            if cv2.pointPolygonTest(np.asarray(t["polygon"], np.float32), (u["cx_aerial"], u["cy_aerial"]), False) >= 0:
                u["on_part"] = t["id"]
                u["cx_if_on_part"] = round(u["cx_aerial"] - t["lean"][0], 2)
                u["cy_if_on_part"] = round(u["cy_aerial"] - t["lean"][1], 2)
    if units_in:
        res["units"] = []
        for x, y, w, d in units_in:
            lg, bid = lean_at(x, y)
            res["units"].append({"cx": round(x - lg[0], 2), "cy": round(y - lg[1], 2), "cx_aerial": x, "cy_aerial": y,
                                 "w": w, "d": d, "brightness": None, "block": bid,
                                 "note": "人が指した物（--units、写真の上の位置から倒れ込みを引いた）", "source": "manual"})
        res["units_auto"] = auto
    else:
        res["units"] = auto
    res["large_regions"] = large
    res["roof_brightness"] = stats
    res["warnings"] = warns
    res["px_per_m"] = round(frame.px_per_m, 4)
    return res


def _draw(crop: np.ndarray, frame: Frame, footprint: list, parts: list[list], res: dict | None, out: Path,
          low: dict | None = None) -> Path:
    import cv2

    H, W = crop.shape[:2]
    pad = 40
    big = cv2.resize(crop[..., ::-1] if crop.ndim == 3 else cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR),
                     (W * SCALE, H * SCALE), interpolation=cv2.INTER_CUBIC)
    canvas = np.full((H * SCALE + 2 * pad, W * SCALE + 2 * pad, 3), 255, np.uint8)
    canvas[pad:pad + H * SCALE, pad:pad + W * SCALE] = big

    def P(xy):
        q = frame.to_px(np.asarray(xy, dtype=float))
        return np.round((q + 0.5) * SCALE + pad).astype(np.int32)   # 画素の中心 → 拡大後の位置

    cv2.polylines(canvas, [P(footprint)], True, (0, 0, 255), 2, cv2.LINE_AA)
    # 既存の屋上部品（水色）：結果があれば上端の高さの倒れ込みだけずらして写真の上に置く（写真と同じ見え方で比べる）
    for p in parts:
        k = (p["z1"] / res["height_m"]) if res and res.get("height_m") else 0.0
        off = np.array(res["lean_m"]) * k if res else np.zeros(2)
        cv2.polylines(canvas, [P(np.asarray(p["polygon"], dtype=float) + off)], True, (255, 220, 0), 2, cv2.LINE_AA)
    if res:
        for rp in res["roof_polygon_aerial"]:
            cv2.polylines(canvas, [P(rp)], True, (0, 200, 0), 2, cv2.LINE_AA)
        for i, u in enumerate(res.get("units") or [], 1):
            ax = np.asarray(u.get("axis_u") or [1.0, 0.0])
            av = np.array([-ax[1], ax[0]])
            c = np.array([u["cx_aerial"], u["cy_aerial"]])
            hw, hd = u["w"] / 2, u["d"] / 2
            rect = [c - ax * hw - av * hd, c + ax * hw - av * hd, c + ax * hw + av * hd, c - ax * hw + av * hd]
            cv2.polylines(canvas, [P(rect)], True, (0, 230, 255), 2, cv2.LINE_AA)
            t = P([c])[0]
            cv2.putText(canvas, str(i), (int(t[0]) + 6, int(t[1]) - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 230, 255), 2, cv2.LINE_AA)
        lx, ly = res["lean_m"]
        cv2.putText(canvas, f"lean dx={lx:+.2f} dy={ly:+.2f} m  ratio={res.get('lean_ratio')}  ambiguous={res['ambiguous']}",
                    (pad, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    if low:
        cv2.polylines(canvas, [P(low["polygon"])], True, (0, 0, 255), 1, cv2.LINE_AA)
    # 目盛り（5 m おき）：上に x、左に y
    xy00 = frame.to_xy(np.array([[-0.5, -0.5]]))[0]
    xy11 = frame.to_xy(np.array([[W - 0.5, H - 0.5]]))[0]
    for xv in np.arange(math.ceil(min(xy00[0], xy11[0]) / 5) * 5, max(xy00[0], xy11[0]), 5):
        t = P([[xv, xy00[1]]])[0]
        X = int(t[0])
        cv2.line(canvas, (X, pad - 8), (X, pad), (0, 0, 0), 1)
        cv2.putText(canvas, f"{xv:.0f}", (X - 12, pad - 22 if res else pad - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 1, cv2.LINE_AA)
    for yv in np.arange(math.ceil(min(xy00[1], xy11[1]) / 5) * 5, max(xy00[1], xy11[1]), 5):
        t = P([[xy00[0], yv]])[0]
        Y = int(t[1])
        cv2.line(canvas, (pad - 8, Y), (pad, Y), (0, 0, 0), 1)
        cv2.putText(canvas, f"{yv:.0f}", (2, Y + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 1, cv2.LINE_AA)
    cv2.putText(canvas, f"x, y [m]  {frame.px_per_m:.3f} px/m  x{SCALE}", (pad, canvas.shape[0] - 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
    out.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".png", canvas)
    out.write_bytes(buf.tobytes())   # 日本語のパスでも書ける
    return out


def run(building_json: Path, out_dir: Path | None = None, roof_edge: str | None = None, units: str | None = None,
        aerial_png: Path | None = None, site_json: Path | None = None) -> tuple[dict, Path, Path]:
    import cv2

    b = json.loads(Path(building_json).read_text(encoding="utf-8"))
    out_dir = Path(out_dir) if out_dir else Path("out") / b["id"]
    aerial_png = Path(aerial_png) if aerial_png else site.OUT_SITE / "aerial_z18.png"
    meta = json.loads(aerial_png.with_suffix(".json").read_text(encoding="utf-8"))
    img = cv2.imdecode(np.frombuffer(aerial_png.read_bytes(), np.uint8), cv2.IMREAD_COLOR)[..., ::-1]
    fp = b["footprint"]["polygon"]
    margin = max(MARGIN_PX, int(math.ceil(SHIFT_M * 2.1)) + 4)
    crop, frame, _ = _crop(img, meta, [fp], margin)
    h, h_how = roof_height(b)
    res = analyze_image(crop, frame, fp, h, parse_pairs(roof_edge, 2) or None, parse_pairs(units, 4) or None,
                        blocks=b.get("blocks"), tall_parts=tall_parts(b))
    res["height_m"] = h
    res["height_from"] = h_how

    # 検算：近くの低い建物
    site_json = Path(site_json) if site_json else site.DATA_SITE / f"{b.get('campus', 'tempaku').split('-')[-1]}.json"
    if not site_json.exists():
        site_json = site.DATA_SITE / "tempaku.json"
    low = None
    chk: dict = {"ok": None, "note": "低い建物が見つからない"}
    if site_json.exists():
        sdoc = json.loads(site_json.read_text(encoding="utf-8"))
        excl = {b.get("site_id")} if b.get("site_id") else set()
        low = pick_low_building(sdoc, np.asarray(fp, dtype=float).mean(axis=0), exclude=excl)
        if low:
            lcrop, lframe, _ = _crop(img, meta, [low["polygon"]], margin)
            ls = scan_offsets(_gray(lcrop), lframe, low["polygon"])
            lc = pick_candidates(ls)
            sh = math.hypot(lc[0]["dx"], lc[0]["dy"]) if lc else None
            chk = {"id": low["id"], "plateau_height_m": low["plateau_height_m"], "distance_m": low["distance_m"],
                   "offset_m": [lc[0]["dx"], lc[0]["dy"]] if lc else None, "offset_abs_m": round(sh, 2) if sh is not None else None,
                   "candidates": lc, "ambiguous": is_ambiguous(lc),
                   "ok": bool(sh is not None and sh < LOW_OK_M),
                   "note": ("写真の位置合わせは良い（低い建物のずれ 1 m 未満）" if sh is not None and sh < LOW_OK_M
                            else "低い建物でも 1 m 以上ずれる：写真の位置ずれか、低い建物の縁の読み違いの疑い")}
            if not chk["ok"]:
                res["warnings"].append(chk["note"])
    res["check_low_building"] = chk
    res.update({"building": b["id"], "source": "gsi aerial z18", "aerial": str(aerial_png).replace("\\", "/"),
                "made_by": "recorder.roof", "made_at": datetime.now().isoformat(timespec="seconds"),
                "method": "足元の形を ±6 m 平行移動して航空写真の勾配（辺に直交する成分）との重なりが最大の位置＝屋上の縁。"
                          "設備は縁から 1 m 内側で屋根面の明るさの中央値から閾値以上違う連結成分。値は estimated として使う"})
    out_dir.mkdir(parents=True, exist_ok=True)
    jp = out_dir / "roof.json"
    jp.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    # 既存の屋上部品の位置に何か写っているか（倒れ込みを引いた足元の座標で、部品の polygon に入る設備）
    rp = rooftop_parts(b)
    res["existing_rooftop_parts"] = []
    for p in rp:
        poly = np.asarray(p["polygon"], np.float32)
        hits = [i for i, u in enumerate(res["units"], 1)
                if cv2.pointPolygonTest(poly, (float(u["cx"]), float(u["cy"])), False) >= 0]
        res["existing_rooftop_parts"].append({"name": p["name"], "z1": p["z1"], "units_inside": hits,
                                              "note": "写真では中に設備が無い" if not hits else f"設備 {hits} が中にある"})
    jp.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    png = _draw(crop, frame, fp, rp, res, out_dir / "roof_aerial.png")
    return res, jp, png
