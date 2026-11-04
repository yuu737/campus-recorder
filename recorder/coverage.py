"""被覆図：面ごとに、登録済みの写真を面の座標（m）に貼り、立面図の線を重ねる。覆われていない所は灰色。

写真は「適した部分だけ」を使う。切り取りの規則（この順。写真ごとの理由は coverage.json の photos[].trimmed）
  1. 面の輪郭の外（隣の面・空・地面）を捨てる：面の幅 × 高さ（地面〜面の上端）の矩形の内側だけ
  2. 斜めで引き伸ばされた所を捨てる：H_plane のヤコビアンから面の各点の「写真の画素数／m」（縮む向きの値）を出し、
     その写真の（面の中の）中央値の 1/3 を下回る所、または面の法線と視線の角度が 70° を超える所
  3. 手前の物（木・車・人・街灯）で隠れた所：registry の登録の exclude（面の m 座標の矩形）
  捨てた所は「写真はあるが使えない」（薄い斜線）として未撮影（灰色）と分ける。被覆率は使えた範囲で数える。
  重なる所は画素数／m が多い写真（正面に近い方）を上にする（点ごとに選ぶ。継ぎ目は隠さない）。

出力（out/<棟>/。git 管理外。写真を含むので Web には出さない）
  coverage_<面>.svg        立面図＋写真（<image> 相対パス）＋未撮影の灰色＋使えない所の斜線＋立面の線（薄い赤）＋写真の枠と id
  coverage_<面>_photo.png  面の座標の写真の合成（RGBA。使えない所・未撮影は透明。px_per_m 既定 40）
  coverage_<面>_check.png  上の PNG に灰色・斜線・立面の線・枠を描いた確認用
  coverage.json            面ごとの被覆率 %、未撮影・使えない区間、理由ごとの面積、撮影依頼の文
--no-photo（photo=False）なら写真を読まず、今までの塗り（写真ごとの半透明の範囲）に斜線を足した SVG だけ。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np

from . import drawing
from .registry import (compass8, cover_polygon, entries_for, face_frame, face_top, load_registry, photo_path, plane_to_world,
                       world_to_plane)
from .schema import Measurement, load_building

CELL_M = 0.25
OBLIQUE_RATIO = 1 / 3      # 画素数／m がその写真の中央値のこれ未満なら斜めで使わない
MAX_VIEW_DEG = 70.0        # 面の法線と視線の角度がこれを超えたら使わない
PX_PER_M = 40.0            # 面の座標の写真の解像度
PALETTE = ["#1f77b4", "#ff9f1c", "#2ca02c", "#9467bd", "#17becf", "#e377c2", "#8c564b", "#bcbd22"]   # 赤は立面の線に使うので除く


def _known_len(f: dict) -> float | None:
    m = f.get("length")
    if isinstance(m, (int, float)):
        return float(m)
    if isinstance(m, dict):
        mm = Measurement.from_json(m)
        if mm.known:
            return float(mm.value)
    if f.get("from") and f.get("to"):
        return face_frame(f)[3]
    return None


def reg_to_now(e: dict, f: dict) -> tuple[float, float]:
    """登録の面の s と今の面の s の関係 s_reg = a·s_now + c の (a, c)。同じ直線の上なら a = ±1。"""
    now = {"from": f["from"], "to": f["to"]}
    P = plane_to_world(now, np.array([0.0, 1.0, 0.0]), np.array([0.0, 0.0, 1.0]))
    q = world_to_plane(e["face_frame"], P)
    return float(q[1, 0] - q[0, 0]), float(q[0, 0])


def entry_H_now(e: dict, f: dict) -> np.ndarray:
    """登録の H_plane を、今の面の (s, z) → 写真 px に直す。面が登録の後に動いても（同じ直線の上なら）合う。"""
    a, c = reg_to_now(e, f)
    T = np.array([[a, 0, c], [0, 1, 0], [0, 0, 1]], float)
    return np.array(e["H_plane"], float) @ T


def entry_cover_now(e: dict, f: dict, rect: tuple[float, float, float, float]) -> list[tuple[float, float]]:
    """登録の H_plane を、今の面の (s, z) に直して覆う範囲を切る。"""
    w, h = e["image_size"]
    return cover_polygon(entry_H_now(e, f), w, h, rect)


def raster(polys: list[list[tuple[float, float]]], rect, cell: float = CELL_M) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """面の矩形を cell m の格子にし、どれかの多角形に入る格子を True。返り値 (mask[z, s], s の中心, z の中心)。"""
    s0, s1, z0, z1 = rect
    sc = np.arange(s0 + cell / 2, s1, cell)
    zc = np.arange(z0 + cell / 2, z1, cell)
    S, Z = np.meshgrid(sc, zc)
    mask = np.zeros(S.shape, bool)
    for poly in polys:
        if len(poly) < 3:
            continue
        inside = np.ones(S.shape, bool)
        n = len(poly)
        area2 = sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n))
        sgn = 1.0 if area2 >= 0 else -1.0
        for i in range(n):            # 凸多角形（射影で切った矩形は凸）：全部の辺の内側
            (xa, ya), (xb, yb) = poly[i], poly[(i + 1) % n]
            inside &= sgn * ((xb - xa) * (Z - ya) - (yb - ya) * (S - xa)) >= -1e-9
        mask |= inside
    return mask, sc, zc


def uncovered_rects(mask: np.ndarray, sc: np.ndarray, zc: np.ndarray, cell: float = CELL_M, max_n: int = 4,
                    min_area: float = 4.0, min_side: float = 1.0) -> list[dict]:
    """覆われていない格子を、大きい長方形から順に取り出す（撮影依頼の区間）。"""
    free = ~mask
    out = []
    for _ in range(max_n):
        best = None
        H, W = free.shape
        hist = np.zeros(W, int)
        for r in range(H):
            hist = np.where(free[r], hist + 1, 0)
            stack: list[int] = []
            for c in range(W + 1):
                hc = hist[c] if c < W else 0
                while stack and hist[stack[-1]] >= hc:
                    top = stack.pop()
                    left = stack[-1] + 1 if stack else 0
                    area = hist[top] * (c - left)
                    if best is None or area > best[0]:
                        best = (area, r - hist[top] + 1, r, left, c - 1)
                stack.append(c)
        if not best or best[0] == 0:
            break
        _, r0, r1, c0, c1 = best
        a = (r1 - r0 + 1) * cell * (c1 - c0 + 1) * cell
        ws, hz = (c1 - c0 + 1) * cell, (r1 - r0 + 1) * cell
        if a < min_area or ws < min_side or hz < min_side:
            break
        out.append({"s0": round(float(sc[c0] - cell / 2), 2), "s1": round(float(sc[c1] + cell / 2), 2),
                    "z0": round(float(zc[r0] - cell / 2), 2), "z1": round(float(zc[r1] + cell / 2), 2), "area_m2": round(a, 1)})
        free[r0:r1 + 1, c0:c1 + 1] = False
    return out


def request_text(b: dict, f: dict, r: dict, ground: float, L: float) -> str:
    p0, p1 = np.array(f["from"], float), np.array(f["to"], float)
    end = compass8(*(p0 - p1))
    name = f.get("name") or f["id"]
    return (f"{f['id']} 面（{name}）の{end}端（from）から {r['s0']:.0f}〜{r['s1']:.0f} m・高さ（地面から）"
            f"{max(0.0, r['z0'] - ground):.0f}〜{r['z1'] - ground:.0f} m が未撮影")


# --- 写真の切り取り（面の点ごとの判定） ------------------------------------------------

def camera_center(e: dict) -> np.ndarray | None:
    cam = e.get("camera") or {}
    if cam.get("camera_xyz"):
        return np.array(cam["camera_xyz"], float)
    if cam.get("R_cv") and cam.get("t_cv") is not None:
        R, t = np.array(cam["R_cv"], float), np.array(cam["t_cv"], float).ravel()
        return -R.T @ t
    return None


def photo_fields(e: dict, f: dict, S: np.ndarray, Z: np.ndarray, min_depth: float = 0.5) -> dict:
    """面の点 (S, Z)（今の面の m）ごとに：写真の px (u, v)、写真の枠に入るか、画素数／m（縮む向き）、視線の角度（度）。"""
    H = entry_H_now(e, f)
    W, Hh = e["image_size"]
    q0 = H[0, 0] * S + H[0, 1] * Z + H[0, 2]
    q1 = H[1, 0] * S + H[1, 1] * Z + H[1, 2]
    q2 = H[2, 0] * S + H[2, 1] * Z + H[2, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        u, v = q0 / q2, q1 / q2
        # ヤコビアン d(u, v)/d(s, z)
        a = (H[0, 0] - u * H[2, 0]) / q2
        b = (H[0, 1] - u * H[2, 1]) / q2
        c = (H[1, 0] - v * H[2, 0]) / q2
        d = (H[1, 1] - v * H[2, 1]) / q2
    p, r, qq = a * a + c * c, b * b + d * d, a * b + c * d          # JᵀJ = [[p, qq], [qq, r]]
    mn = (p + r) / 2 - np.sqrt(((p - r) / 2) ** 2 + qq ** 2)
    dens = np.sqrt(np.clip(np.nan_to_num(mn), 0, None))
    in_frame = (q2 >= min_depth) & (u >= 0) & (u < W) & (v >= 0) & (v < Hh)
    C = camera_center(e)
    if C is not None:
        fr = {"from": f["from"], "to": f["to"]}
        _, _, nv, _ = face_frame(fr)
        P = plane_to_world(fr, S, Z)
        D = C - P
        cosv = np.abs(D[..., 0] * nv[0] + D[..., 1] * nv[1]) / np.maximum(np.linalg.norm(D, axis=-1), 1e-9)
        ang = np.degrees(np.arccos(np.clip(cosv, 0, 1)))
    else:
        ang = np.zeros_like(dens)
    return {"u": u, "v": v, "in_frame": in_frame, "dens": dens, "angle": ang}


def exclude_now(e: dict, f: dict) -> list[tuple[float, float, float, float, str]]:
    """登録の exclude（登録の面の m：[along0, along1, z0, z1] か {"rect": [...], "why": ...}）を今の面の s に直す。"""
    a, c = reg_to_now(e, f)
    out = []
    notes = e.get("exclude_note") or []
    for i, x in enumerate(e.get("exclude") or []):
        rect, why = (x.get("rect"), x.get("why", "")) if isinstance(x, dict) else (x, notes[i] if i < len(notes) else "")
        s0, s1, z0, z1 = (float(v) for v in rect)
        n0, n1 = (s0 - c) / a, (s1 - c) / a
        out.append((min(n0, n1), max(n0, n1), min(z0, z1), max(z0, z1), why))
    return out


def trim_masks(e: dict, f: dict, S: np.ndarray, Z: np.ndarray, med: float | None = None) -> dict:
    """規則 1〜3 で写真の使える所を決める。S, Z は面の矩形の中の点（規則 1 は点の取り方で満たす）。"""
    pf = photo_fields(e, f, S, Z)
    fr = pf["in_frame"]
    if med is None:
        med = float(np.median(pf["dens"][fr])) if fr.any() else 0.0
    oblique = fr & ((pf["dens"] < med * OBLIQUE_RATIO) | (pf["angle"] > MAX_VIEW_DEG))
    ex = np.zeros(S.shape, bool)
    for s0, s1, z0, z1, _ in exclude_now(e, f):
        ex |= (S >= s0) & (S <= s1) & (Z >= z0) & (Z <= z1)
    excluded = fr & ~oblique & ex
    return {**pf, "oblique": oblique, "excluded": excluded, "usable": fr & ~oblique & ~excluded, "median": med}


def mask_to_rects(mask: np.ndarray, sc: np.ndarray, zc: np.ndarray, cell: float = CELL_M) -> list[list[float]]:
    """格子の True を、行ごとの連なりを上下で同じ幅ならつなげた矩形 [s0, s1, z0, z1] の一覧にする（全部を覆う）。"""
    out: list[list[float]] = []
    open_: dict[tuple[int, int], list[float]] = {}
    for zi in range(mask.shape[0]):
        runs = []
        c = 0
        while c < mask.shape[1]:
            if not mask[zi, c]:
                c += 1
                continue
            c0 = c
            while c < mask.shape[1] and mask[zi, c]:
                c += 1
            runs.append((c0, c - 1))
        nxt = {}
        for r in runs:
            if r in open_:
                open_[r][3] = round(float(zc[zi] + cell / 2), 2)
                nxt[r] = open_.pop(r)
            else:
                nxt[r] = [round(float(sc[r[0]] - cell / 2), 2), round(float(sc[r[1]] + cell / 2), 2),
                          round(float(zc[zi] - cell / 2), 2), round(float(zc[zi] + cell / 2), 2)]
        out += open_.values()
        open_ = nxt
    out += open_.values()
    return sorted(out, key=lambda r: (r[2], r[0]))


def coarse_rects(mask: np.ndarray, sc: np.ndarray, zc: np.ndarray, cell: float = CELL_M, web_cell: float = 1.0) -> list[list[float]]:
    """格子を web_cell m に粗くして（半分以上が True の格子を True）矩形にする。Web の薄い板の数を減らすため。"""
    k = max(1, int(round(web_cell / cell)))
    nz, ns = -(-mask.shape[0] // k), -(-mask.shape[1] // k)
    pad = np.zeros((nz * k, ns * k), float)
    pad[:] = np.nan
    pad[:mask.shape[0], :mask.shape[1]] = mask
    blk = np.nanmean(pad.reshape(nz, k, ns, k), axis=(1, 3)) >= 0.5
    c = cell * k
    s0, z0 = sc[0] - cell / 2, zc[0] - cell / 2
    rs = mask_to_rects(blk, s0 + c / 2 + np.arange(ns) * c, z0 + c / 2 + np.arange(nz) * c, c)
    s1, z1 = sc[-1] + cell / 2, zc[-1] + cell / 2          # 端の格子は面の矩形で切る
    return [[r[0], round(min(r[1], s1), 2), r[2], round(min(r[3], z1), 2)] for r in rs]


def _load_photo(e: dict) -> np.ndarray | None:
    from .rectify import read_image
    p = photo_path(e)
    if not p.exists():
        return None
    return read_image(p)


def grid_size(rect, ppm: float) -> tuple[int, int]:
    s0, s1, z0, z1 = rect
    return max(1, int(round((s1 - s0) * ppm))), max(1, int(round((z1 - z0) * ppm)))


def pixel_grid(rect, ppm: float) -> tuple[np.ndarray, np.ndarray]:
    """格子の画素の中心の (S, Z)。行は上（z1）から下へ。"""
    s0, s1, z0, z1 = rect
    W, Hh = grid_size(rect, ppm)
    return np.meshgrid(s0 + (np.arange(W) + 0.5) / ppm, z1 - (np.arange(Hh) + 0.5) / ppm)


def warp_to_face(img: np.ndarray, e: dict, f: dict, rect, ppm: float, med_dens: float) -> np.ndarray:
    """写真を面の座標の格子（ppm px/m、左上が s=s0・z=z1）に逆変換（cv2.warpPerspective の逆写像）。返り値 BGR。"""
    import cv2
    s0, s1, z0, z1 = rect
    W, Hh = grid_size(rect, ppm)
    k = max(1.0, med_dens / (2.0 * ppm))          # 先に縮めてから貼る（縮めずに貼ると細かい模様がちらつく）
    small = cv2.resize(img, (max(1, int(round(img.shape[1] / k))), max(1, int(round(img.shape[0] / k)))),
                       interpolation=cv2.INTER_AREA) if k > 1.0 else img
    sx, sy = small.shape[1] / img.shape[1], small.shape[0] / img.shape[0]
    Hn = np.diag([sx, sy, 1.0]) @ entry_H_now(e, f)
    A = np.array([[1 / ppm, 0, s0 + 0.5 / ppm], [0, -1 / ppm, z1 - 0.5 / ppm], [0, 0, 1]], float)
    # 写真の画素の中心は (i+0.5)·sx − 0.5。縮めた分の半画素のずれを直す
    Csh = np.array([[1, 0, 0.5 * sx - 0.5], [0, 1, 0.5 * sy - 0.5], [0, 0, 1]], float) if k > 1.0 else np.eye(3)
    return cv2.warpPerspective(small, Csh @ Hn @ A, (W, Hh), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))


def composite(ents: list[dict], f: dict, rect, ppm: float, meds: list[float], loader) -> tuple[np.ndarray, list[str]]:
    """使える所だけを、点ごとに画素数／m の多い写真を上にして合成。返り値 RGBA（使えない所・未撮影は透明）と、読めなかった写真。"""
    S, Z = pixel_grid(rect, ppm)
    best = np.full(S.shape, -1.0)
    rgba = np.zeros(S.shape + (4,), np.uint8)
    missing = []
    for e, med in zip(ents, meds):
        tm = trim_masks(e, f, S, Z, med)
        take = tm["usable"] & (tm["dens"] > best)
        if not take.any():
            continue
        img = loader(e)
        if img is None:
            missing.append(e["photo"])
            continue
        warped = warp_to_face(img, e, f, rect, ppm, med)
        rgba[take, :3] = warped[take][:, ::-1]
        rgba[take, 3] = 255
        best = np.where(take, tm["dens"], best)
    return rgba, missing


def contour_polys(mask: np.ndarray, sc: np.ndarray, zc: np.ndarray, cell: float) -> list[list[tuple[float, float]]]:
    """格子の True の外形（面の m の多角形。面積の大きい順）。"""
    import cv2
    if not mask.any():
        return []
    m = np.pad(mask.astype(np.uint8), 1)
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    s0, z0 = sc[0] - cell / 2, zc[0] - cell / 2
    for c in sorted(cs, key=cv2.contourArea, reverse=True):
        c = cv2.approxPolyDP(c, 1.0, True).reshape(-1, 2)
        if len(c) < 3 or cv2.contourArea(c.reshape(-1, 1, 2).astype(np.float32)) < 4:
            continue
        out.append([(round(float(s0 + (x - 0.5) * cell), 2), round(float(z0 + (y - 0.5) * cell), 2)) for x, y in c])
    return out


# --- 本体 -----------------------------------------------------------------------------

def coverage(building_json: Path, registry: dict | None = None, out_dir: Path | None = None, cell: float = CELL_M,
             photo: bool = True, px_per_m: float = PX_PER_M, loader=None) -> tuple[dict, Path]:
    b = load_building(building_json)
    reg = registry if registry is not None else load_registry()
    ents = entries_for(reg, b["id"])
    out = out_dir or Path("out") / b["id"]
    out.mkdir(parents=True, exist_ok=True)
    loader = loader or _load_photo
    res = {"building": b["id"], "made_by": "coverage（recorder）", "made_at": datetime.now().isoformat(timespec="seconds"),
           "cell_m": cell, "px_per_m": px_per_m if photo else None,
           "note": ("写真の使える所（面の矩形の内・斜めで引き伸ばされていない・exclude の外）だけを数える。"
                    "exclude の無い遮蔽（木・車など）は覆ったと数えてしまう"),
           "rules": {"oblique_ratio": OBLIQUE_RATIO, "max_view_deg": MAX_VIEW_DEG}, "faces": [], "requests": []}
    _, Hb, _ = drawing._face_height(b)
    a2 = cell * cell
    for f in b.get("faces", []):
        fid = f["id"]
        L = _known_len(f)
        g, _src = drawing.face_ground_line(b, f) if f.get("from") and f.get("to") else (0.0, "none")
        top = face_top(b, f)
        unknown = L is None or not (f.get("from") and f.get("to"))
        row = {"face": fid, "name": f.get("name"), "unknown": unknown, "photos": [], "coverage_pct": 0.0, "frame_pct": 0.0,
               "uncovered": [], "length_m": round(L, 2) if L else None, "ground_z": round(g, 2), "top_z": top,
               "unusable_m2": {}, "uncovered_m2": None, "uncovered_rects": [], "unusable_rects": []}
        cm = None
        if not unknown:
            rect = (0.0, L, g, top)
            sc = np.arange(cell / 2, L, cell)
            zc = np.arange(g + cell / 2, top, cell)
            S, Z = np.meshgrid(sc, zc)
            fents, meds, tms = [], [], []
            for e in ents:
                if e["face"] != fid:
                    continue
                poly = entry_cover_now(e, f, rect)
                if len(poly) < 3:
                    continue
                tm = trim_masks(e, f, S, Z)
                fents.append(e)
                meds.append(tm["median"])
                tms.append(tm)
                ex = exclude_now(e, f)
                amax = float(tm["angle"][tm["in_frame"]].max()) if tm["in_frame"].any() else 0.0
                trimmed = [f"1. 面の輪郭の外（隣の面・空・地面）を捨てた：面の矩形 s 0〜{L:.1f} m・z {g:.1f}〜{top:.1f} m の内側で "
                           f"写真の枠に入るのは {tm['in_frame'].sum() * a2:.1f} m²",
                           f"2. 斜めで引き伸ばされた所 {tm['oblique'].sum() * a2:.1f} m² を捨てた（画素数／m が中央値 {tm['median']:.0f} の "
                           f"1/3 未満、または視線と面の法線の角度 {MAX_VIEW_DEG:.0f}° 超。角度の最大 {amax:.0f}°）",
                           (f"3. 遮蔽（exclude）{len(ex)} 個で {tm['excluded'].sum() * a2:.1f} m² を捨てた（" + "・".join(w for *_, w in ex if w) + "）")
                           if ex else "3. 遮蔽の記録（exclude）なし。木・車などで隠れた所があっても捨てていない"]
                row["photos"].append({"photo": e["photo"], "period": e.get("period"), "polygon": [list(p) for p in poly],
                                      "px_per_m_median": round(tm["median"], 1),
                                      "frame_m2": round(float(tm["in_frame"].sum() * a2), 1),
                                      "oblique_m2": round(float(tm["oblique"].sum() * a2), 1),
                                      "excluded_m2": round(float(tm["excluded"].sum() * a2), 1),
                                      "used_m2": round(float(tm["usable"].sum() * a2), 1),
                                      "trimmed": trimmed})
            # 重ね順：面の点ごとに画素数／m の多い写真が上。order は中央値の多い順（参考）
            for rank, i in enumerate(sorted(range(len(fents)), key=lambda i: -meds[i])):
                row["photos"][i]["order"] = rank + 1
            frame_any = np.zeros(S.shape, bool)
            usable_any = np.zeros(S.shape, bool)
            best = np.full(S.shape, -1.0)
            winner = np.full(S.shape, -1)
            reason_best = np.full(S.shape, -1.0)
            reason = np.zeros(S.shape, np.int8)      # 使えない所の理由（その点で画素数／m の最も多い写真の理由）：1 斜め、2 遮蔽
            for i, tm in enumerate(tms):
                frame_any |= tm["in_frame"]
                usable_any |= tm["usable"]
                take = tm["usable"] & (tm["dens"] > best)
                best = np.where(take, tm["dens"], best)
                winner = np.where(take, i, winner)
                bad = tm["in_frame"] & ~tm["usable"] & (tm["dens"] > reason_best)
                reason = np.where(bad & tm["oblique"], 1, np.where(bad & tm["excluded"], 2, reason))
                reason_best = np.where(bad, tm["dens"], reason_best)
            for i, tm in enumerate(tms):
                row["photos"][i]["top_m2"] = round(float((winner == i).sum() * a2), 1)
                row["photos"][i]["outline"] = [[list(p) for p in poly] for poly in contour_polys(tm["usable"], sc, zc, cell)]
                top_here = winner == i if (winner == i).any() else tm["usable"]
                if top_here.any():           # id の札：その写真が上になっている所の中央の列、その列の上端から下げて（写真ごとにずらす）
                    cs_ = float(np.median(S[top_here]))
                    col_ = top_here[:, int(np.argmin(np.abs(sc - cs_)))]
                    zt_ = float(zc[col_].max()) if col_.any() else float(Z[top_here].max())
                    row["photos"][i]["label_at"] = [round(cs_, 2), round(zt_ - 0.8 - 1.6 * (row["photos"][i]["order"] - 1), 2)]
            unusable = frame_any & ~usable_any
            uncovered = ~frame_any
            row["coverage_pct"] = round(100.0 * float(usable_any.mean()), 1) if usable_any.size else 0.0
            row["frame_pct"] = round(100.0 * float(frame_any.mean()), 1) if frame_any.size else 0.0
            row["unusable_m2"] = {"oblique": round(float((unusable & (reason == 1)).sum() * a2), 1),
                                  "exclude": round(float((unusable & (reason == 2)).sum() * a2), 1)}
            row["uncovered_m2"] = round(float(uncovered.sum() * a2), 1)
            row["uncovered_rects"] = mask_to_rects(uncovered, sc, zc, cell)
            row["unusable_rects"] = mask_to_rects(unusable, sc, zc, cell)
            # Web 用（site export-web --with-coverage）：1 m の格子に粗くした矩形（半分以上が当てはまる格子）
            row["web_uncovered"] = coarse_rects(uncovered, sc, zc, cell)
            row["web_unusable"] = coarse_rects(unusable, sc, zc, cell)
            row["uncovered"] = uncovered_rects(usable_any, sc, zc, cell)
            for r in (row["uncovered"] if fents else []):
                r["text"] = request_text(b, f, r, g, L)
                sel = (S >= r["s0"]) & (S <= r["s1"]) & (Z >= r["z0"]) & (Z <= r["z1"])
                if (unusable & sel).any():
                    r["text"] += "（写真はあるが斜め・遮蔽で使えない所を含む）"
                res["requests"].append({"face": fid, **r})
            if not fents:
                res["requests"].append({"face": fid, "s0": 0.0, "s1": round(L, 2), "z0": g, "z1": top,
                                        "text": f"{fid} 面（{f.get('name') or fid}）は登録済みの写真が 1 枚も無い（全面が未撮影）"})
            cm = {"usable": usable_any, "unusable": unusable, "uncovered": uncovered, "sc": sc, "zc": zc}
            if photo and fents:
                import cv2
                rgba, missing = composite(fents, f, rect, px_per_m, meds, loader)
                if missing:
                    row["missing_photos"] = missing
                pp = out / f"coverage_{fid}_photo.png"
                cv2.imencode(".png", cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))[1].tofile(str(pp))   # 日本語のパスでも書ける
                row["photo_png"] = str(pp)
                cm["png"] = pp.name
                cm["png_size_m"] = (rgba.shape[1] / px_per_m, rgba.shape[0] / px_per_m)
                cm["rgba"] = rgba
        else:
            res["requests"].append({"face": fid, "text": f"{fid} 面（{f.get('name') or fid}）は位置か長さが未確定（unknown）。面の両端が入る写真が要る"})
        try:
            svg = drawing.elevation_svg(b, fid)
        except Exception as ex:          # 立面が描けない面（unknown）は被覆だけの小さい図
            svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="600" height="200"><text x="20" y="40">{fid}: 立面図を描けません（{ex}）</text></svg>'
        (out / f"coverage_{fid}.svg").write_text(overlay_svg(svg, row, cm, Hb, cell, photo), encoding="utf-8")
        row["svg"] = str(out / f"coverage_{fid}.svg")
        if cm is not None and cm.get("rgba") is not None:
            cp = out / f"coverage_{fid}_check.png"
            check_png(cm.pop("rgba"), row, cm, svg, Hb, px_per_m, cell, cp)
            row["check_png"] = str(cp)
        res["faces"].append(row)
    jp = out / "coverage.json"
    jp.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res, jp


# --- 描画 -----------------------------------------------------------------------------

LINE_RED = "#ff4040"
_SHAPE = r"<(rect|polygon|line)\b[^>]*/>"


def elevation_lines(svg: str) -> list[str]:
    """立面図（drawing.elevation_svg）の rect・polygon・line を、塗り無し・薄い赤の線にして返す（背景の全面の rect は除く）。"""
    import re
    out = []
    for m in re.finditer(_SHAPE, svg):
        el = m.group(0)
        if 'width="100%"' in el:
            continue
        for k in ("fill", "fill-opacity", "stroke", "stroke-width"):
            el = re.sub(rf'\s{k}="[^"]*"', "", el)
        out.append(el[:-2] + f' fill="none" stroke="{LINE_RED}" stroke-opacity="0.85" stroke-width="0.8"/>')
    return out


def elevation_segments_m(svg: str, H_draw: float) -> list[list[tuple[float, float]]]:
    """立面図の rect・polygon・line を、面の (s, z) m の折れ線にする（確認用 PNG に描くため）。"""
    import re
    S, LEFT, TOP = drawing.SCALE, drawing.LEFT, drawing.TOP
    y0 = TOP + H_draw * S

    def m(x, y):
        return ((x - LEFT) / S, (y0 - y) / S)

    def attr(el, k):
        r = re.search(rf'\s{k}="([-0-9.]+)"', el)
        return float(r.group(1)) if r else 0.0

    segs = []
    for mm in re.finditer(_SHAPE, svg):
        el, kind = mm.group(0), mm.group(1)
        if 'width="100%"' in el:
            continue
        if kind == "rect":
            x, y, w, h = (attr(el, k) for k in ("x", "y", "width", "height"))
            segs.append([m(x, y), m(x + w, y), m(x + w, y + h), m(x, y + h), m(x, y)])
        elif kind == "line":
            segs.append([m(attr(el, "x1"), attr(el, "y1")), m(attr(el, "x2"), attr(el, "y2"))])
        else:
            pts = re.search(r'points="([^"]*)"', el).group(1).split()
            q = [m(*map(float, p.split(","))) for p in pts]
            segs.append(q + q[:1])
    return segs


def _rows_svg(mask, sc, zc, cell, X, Y, S, fill: str, opacity: float) -> list[str]:
    g = []
    for zi in range(mask.shape[0]):           # 行ごとにまとめて塗る
        c = 0
        while c < mask.shape[1]:
            if not mask[zi, c]:
                c += 1
                continue
            c0 = c
            while c < mask.shape[1] and mask[zi, c]:
                c += 1
            s0, s1 = sc[c0] - cell / 2, sc[c - 1] + cell / 2
            z1 = zc[zi] + cell / 2
            g.append(f'<rect x="{X(s0):.1f}" y="{Y(z1):.1f}" width="{(s1 - s0) * S:.1f}" height="{cell * S:.2f}" '
                     f'fill="{fill}" fill-opacity="{opacity}" stroke="none"/>')
    return g


def overlay_svg(svg: str, row: dict, cm: dict | None, H_draw: float, cell: float, photo: bool = True) -> str:
    """立面図の SVG に、写真（面の座標）・未撮影の灰色・使えない所の斜線・立面の線・写真の枠と id を重ねる。座標は drawing と同じ。"""
    S, LEFT, TOP = drawing.SCALE, drawing.LEFT, drawing.TOP

    def X(s):
        return LEFT + s * S

    def Y(z):
        return TOP + H_draw * S - z * S

    g = ['<defs><pattern id="cov-hatch" patternUnits="userSpaceOnUse" width="8" height="8" patternTransform="rotate(45)">'
         '<rect width="8" height="8" fill="#ffffff" fill-opacity="0.35"/><line x1="0" y1="0" x2="0" y2="8" stroke="#555" stroke-width="2"/>'
         '</pattern></defs>',
         '<g id="coverage">']
    if row["unknown"] or cm is None:
        g.append('<rect x="0" y="0" width="100%" height="100%" fill="#888" fill-opacity="0.55"/>')
        g.append(f'<text x="{LEFT}" y="{TOP - 12}" font-size="14" fill="#c8322b">被覆 0 %（面の位置か長さが unknown）</text>')
    else:
        sc, zc = cm["sc"], cm["zc"]
        if photo and cm.get("png"):
            w_m, h_m = cm["png_size_m"]
            g.append(f'<image href="{cm["png"]}" x="{X(0):.2f}" y="{Y(row["top_z"]):.2f}" width="{w_m * S:.2f}" '
                     f'height="{h_m * S:.2f}" preserveAspectRatio="none"/>')
        if not photo:
            for i, p in enumerate(row["photos"]):
                col = PALETTE[i % len(PALETTE)]
                pts = " ".join(f"{X(a):.1f},{Y(z):.1f}" for a, z in p["polygon"])
                g.append(f'<polygon points="{pts}" fill="{col}" fill-opacity="0.18" stroke="none"/>')
        g += _rows_svg(cm["uncovered"], sc, zc, cell, X, Y, S, "#777", 0.6)
        g += _rows_svg(cm["unusable"], sc, zc, cell, X, Y, S, "url(#cov-hatch)", 1.0)
        if photo:
            g.append('<g id="elevation-lines">')
            g += elevation_lines(svg)
            g.append("</g>")
        for i, p in enumerate(row["photos"]):
            col = PALETTE[i % len(PALETTE)]
            for poly in p.get("outline", []):
                pts = " ".join(f"{X(a):.1f},{Y(z):.1f}" for a, z in poly)
                g.append(f'<polygon points="{pts}" fill="none" stroke="{col}" stroke-width="1.5"/>')
            if p.get("label_at"):
                cx, cz = p["label_at"]
                g.append(f'<text x="{X(cx):.1f}" y="{Y(cz):.1f}" font-size="11" fill="{col}" stroke="#fff" stroke-width="2.5" '
                         f'paint-order="stroke" text-anchor="middle">{p["photo"][:24]}（{p.get("order", "")}）</text>')
        g.append(f'<text x="{LEFT}" y="{TOP - 12}" font-size="14" fill="#1f4e79">被覆 {row["coverage_pct"]} %（使える範囲。写真の枠に入るのは '
                 f'{row["frame_pct"]} %。写真 {len(row["photos"])} 枚。灰色＝未撮影、斜線＝写っているが使えない'
                 f'{"、赤＝立面の線" if photo else ""}）</text>')
    g.append("</g>")
    i = svg.rfind("</svg>")
    return svg[:i] + "\n".join(g) + svg[i:] if i >= 0 else svg


def _font(size: int):
    from PIL import ImageFont
    for name in ("YuGothM.ttc", "meiryo.ttc", "msgothic.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def check_png(rgba: np.ndarray, row: dict, cm: dict, svg: str, H_draw: float, ppm: float, cell: float, path: Path) -> None:
    """確認用：面の座標の写真に、未撮影の灰色・使えない所の斜線・立面の線（赤）・写真の枠と id を描いた PNG。"""
    from PIL import Image, ImageDraw
    Hh, W = rgba.shape[:2]
    top = row["top_z"]
    s0g, z0g = cm["sc"][0] - cell / 2, cm["zc"][0] - cell / 2

    def P(s, z):
        return (s * ppm, (top - z) * ppm)

    # 格子（cell）→ 画素の対応
    yy, xx = np.mgrid[0:Hh, 0:W]
    ci = np.clip(((xx + 0.5) / ppm - s0g) // cell, 0, cm["uncovered"].shape[1] - 1).astype(int)
    zi = np.clip(((top - (yy + 0.5) / ppm) - z0g) // cell, 0, cm["uncovered"].shape[0] - 1).astype(int)
    unc, unu = cm["uncovered"][zi, ci], cm["unusable"][zi, ci]
    img = np.full((Hh, W, 3), (247, 250, 251)[::-1], np.float32)
    a = rgba[..., 3:4].astype(np.float32) / 255
    img = img * (1 - a) + rgba[..., :3].astype(np.float32) * a
    img[unc] = img[unc] * 0.35 + np.array([119, 119, 119]) * 0.65
    img[unu] = img[unu] * 0.7 + 255 * 0.3
    img[unu & (((xx + yy) % 12) < 2)] = (60, 60, 60)
    base = Image.fromarray(img.clip(0, 255).astype(np.uint8), "RGB").convert("RGBA")
    ov = Image.new("RGBA", (W, Hh), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    for seg in elevation_segments_m(svg, H_draw):
        d.line([P(s, z) for s, z in seg], fill=(255, 40, 40, 210), width=1)
    font = _font(max(12, int(ppm * 0.4)))
    for i, p in enumerate(row["photos"]):
        h = PALETTE[i % len(PALETTE)].lstrip("#")
        col = tuple(int(h[k:k + 2], 16) for k in (0, 2, 4)) + (255,)
        for poly in p.get("outline", []):
            d.line([P(s, z) for s, z in poly + poly[:1]], fill=col, width=3)
        if p.get("label_at"):
            x, y = P(*p["label_at"])
            d.text((x, y), f'{p["photo"][:24]}（{p.get("order", "")}）', fill=col, font=font, anchor="mm",
                   stroke_width=3, stroke_fill=(255, 255, 255, 255))
    base.alpha_composite(ov)
    base.convert("RGB").save(path)
