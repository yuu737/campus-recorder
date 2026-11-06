"""写真を模型に投影して、写真どうしの食い違いから「模型の形が悪い所」を見つける（改修の道具。最終の模型には写真を入れない）。

考え方（Google Earth の投影テクスチャと同じ）
    登録済みの写真（data/photos/registry.json の camera：R_cv・t_cv・focal_px）は、それぞれ「どこから・どの向きで」撮ったかが分かっている。
    写真 A を A の視点から模型に貼る（投影する）と、A の視点から見る限りは模型の形が違っていても必ず合って見える。
    ところが別の写真 B の視点から見ると、形の違う所だけ A の模様がずれて貼られている。B の実写と比べれば、ずれた所＝模型の形が悪い所。

やり方（遅延シェーディング：Blender は形だけ、写真の計算は numpy）
    1. Blender（blender_build.py の --photoproj）で、写真の視点ごとに「画素に写る模型の点の世界座標 P」と「面・部品の番号 ID」を描く。
       カメラは登録の R_cv・t_cv・focal_px からそのまま作る（H_plane と同じ視点）。描画は写真の 1/4（--fast は 1/8）。
    2. 投影：B の画素の P を A のカメラに投げて A の写真の画素 (u, v) を得る（UV Project と同じ式）。
       A から見えるか：A の視点の深度（P から計算）と比べる（距離のバッファ。裏側・遮蔽は貼らない）。
       A の exclude（面の m 座標の矩形。木・車など）は H_plane で写真に写した多角形をマスクにする。
    3. 比べる物は縁の強さ（勾配の大きさ。色は使わない）。A の写真の縁の強さを B の視点に貼り、B の実写の縁の強さとの差の絶対値を
       B の画素で出し、ID ごとに平均と 90 パーセンタイル。A→B と B→A の両向き。
    4. 1 枚しか無い所：A を貼った模型を A の視点から左右 ±15°・上へ 15° 回した視点で描き、模型の縁（ID の境・深度の段）の脇（2〜16 px）に
       模型の縁と平行な写真の縁が、元の視点より増えた所を「怪しい」（弱い印）とする。形が違うと視点を回した時に縁が模型の縁から離れる。

出力 out/<棟>/photoproj/<日時>/
    heat_<A>_from_<B>.png  B の実写に、A を貼った模型との縁の食い違い（赤ほど大）を重ねた物
    summary.json・summary.md  面・部品 × 写真の組の食い違い（悪い順）と、1 枚の所の「怪しい」
    render/  Blender の P・ID（--keep-renders の時だけ残す）
写真・投影した画像は out/ だけ。glb・Web には入れない（model build は変えない）。
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent

SCALE = 4                 # 描画は写真の 1/SCALE
SCALE_FAST = 8
SENSOR_MM = 36.0
DEPTH_TOL_ABS = 0.15      # 見えるかどうか：A の深度バッファより これ＋相対 だけ奥なら隠れている
DEPTH_TOL_REL = 0.015
EDGE_BLUR = 1.0           # 縁の強さを取る前のぼかし（描画の画素で）
EDGE_SPREAD = 1.5         # 縁の強さの地図をこれだけ広げてから比べる（視点の 1〜2 px の誤差を許す）
MIN_PX = 120              # これより少ない画素の ID は一覧に出さない
MAX_VIEW_DEG = 70.0       # 面をこれより寝た角度から見る写真は貼らない（引き伸ばされて縁が流れる）
SHIFT_DEG = 15.0
BAND_PX = (2, 16)          # 模型の縁から この範囲の画素で「平行な写真の縁」を数える
PARALLEL_COS = math.cos(math.radians(20))
SUSPECT_MIN = 0.08        # 怪しいの印：回した視点で増えた量がこれ以上
NON_BUILDING = ("ground", "tree", "other:")


# --- カメラ ------------------------------------------------------------------------------------

@dataclass
class Cam:
    """写真の視点（OpenCV の向き：x 右、y 下、z 前）。f は写真の画素（全体の大きさ w×h）での焦点距離。"""
    R: np.ndarray
    t: np.ndarray
    f: float
    w: int
    h: int

    @property
    def center(self) -> np.ndarray:
        return -self.R.T @ self.t

    def size(self, scale: float) -> tuple[int, int]:
        return max(8, int(round(self.w / scale))), max(8, int(round(self.h / scale)))


def cam_from_entry(e: dict) -> Cam | None:
    c = e.get("camera") or {}
    if not c.get("R_cv") or c.get("t_cv") is None or not c.get("focal_px"):
        return None
    w, h = c.get("image_size") or e["image_size"]
    return Cam(np.array(c["R_cv"], float), np.array(c["t_cv"], float).ravel(), float(c["focal_px"]), int(w), int(h))


def K_for(cam: Cam, size: tuple[int, int]) -> np.ndarray:
    W, H = size
    f = cam.f * W / cam.w
    return np.array([[f, 0, W / 2.0], [0, f, H / 2.0], [0, 0, 1]], float)


def project(cam: Cam, P: np.ndarray, size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """世界の点 → 描画の大きさ size での画素 (u, v)（連続座標。画素 i は [i, i+1)）と奥行き m。P は (..., 3)。"""
    P = np.asarray(P, float)
    X = P @ cam.R.T + cam.t
    K = K_for(cam, size)
    with np.errstate(divide="ignore", invalid="ignore"):
        u = K[0, 0] * X[..., 0] / X[..., 2] + K[0, 2]
        v = K[1, 1] * X[..., 1] / X[..., 2] + K[1, 2]
    return u, v, X[..., 2]


def pixel_rays(cam: Cam, size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """画素の中心を通る光線（世界）。返り値 (カメラの位置, 向き (H, W, 3) 単位でない：カメラ z=1)。"""
    W, H = size
    K = K_for(cam, size)
    u, v = np.meshgrid(np.arange(W) + 0.5, np.arange(H) + 0.5)
    d_cam = np.stack([(u - K[0, 2]) / K[0, 0], (v - K[1, 2]) / K[1, 1], np.ones_like(u)], axis=-1)
    return cam.center, d_cam @ cam.R          # R^T d を行ベクトルで


def blender_view(cam: Cam, size: tuple[int, int], name: str) -> dict:
    """Blender のカメラ（−Z が前、+Y が上）。sensor_fit AUTO は長い辺に sensor_width を当てる。"""
    Rbl = cam.R.T @ np.diag([1.0, -1.0, -1.0])
    M = np.eye(4)
    M[:3, :3] = Rbl
    M[:3, 3] = cam.center
    W, H = size
    f = cam.f * W / cam.w
    return {"name": name, "matrix_world": M.tolist(), "lens_mm": f / max(W, H) * SENSOR_MM,
            "sensor_width_mm": SENSOR_MM, "resolution": [W, H]}


def rotation(axis, deg: float) -> np.ndarray:
    k = np.asarray(axis, float)
    k = k / np.linalg.norm(k)
    a = math.radians(deg)
    Kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]], float)
    return np.eye(3) + math.sin(a) * Kx + (1 - math.cos(a)) * Kx @ Kx


def orbit(cam: Cam, pivot: np.ndarray, deg: float, up: bool = False) -> Cam:
    """カメラを pivot のまわりに deg 度回す（向きも一緒に。pivot は同じ画素に写ったまま）。
    up=False は鉛直の軸のまわり（左右）、up=True はカメラの右向きの水平の軸のまわり（正でカメラが上がる）。"""
    if up:
        r = cam.R.T @ np.array([1.0, 0, 0])
        Q = rotation([r[0], r[1], 0.0], deg)
        if (Q @ (cam.center - pivot))[2] < (cam.center - pivot)[2]:
            Q = rotation([r[0], r[1], 0.0], -deg)
        if deg < 0:
            Q = Q.T
    else:
        Q = rotation([0, 0, 1], deg)
    C = np.asarray(pivot, float) + Q @ (cam.center - pivot)
    R = cam.R @ Q.T
    return Cam(R, -R @ C, cam.f, cam.w, cam.h)


def pivot_of(e: dict) -> np.ndarray | None:
    """写真の真ん中の光線が、登録した面の平面に当たる点（H_plane の逆で (s, z) → 世界）。"""
    from .registry import invert_H, plane_to_world

    w, h = e["image_size"]
    s, z = invert_H(e["H_plane"], np.array([w / 2.0]), np.array([h / 2.0]))
    if not np.isfinite(s).all():
        return None
    return plane_to_world(e["face_frame"], s, z)[0]


# --- 描画（P と ID）---------------------------------------------------------------------------

@dataclass
class View:
    name: str
    cam: Cam
    size: tuple[int, int]
    P: np.ndarray                    # (H, W, 3) 世界座標。無い所は nan
    ID: np.ndarray                   # (H, W) int。0 は何も無い
    depth: np.ndarray = field(init=False)
    N: np.ndarray = field(init=False)            # 面の向き（カメラの側を向くように揃えた単位ベクトル。分からない所は nan）

    def __post_init__(self):
        _, _, d = project(self.cam, np.nan_to_num(self.P), self.size)
        self.depth = np.where(np.isfinite(self.P[..., 0]), d, np.nan)
        self.N = normals(self.P, self.ID, self.cam.center)

    @property
    def hit(self) -> np.ndarray:
        return np.isfinite(self.P[..., 0])


def normals(P: np.ndarray, ID: np.ndarray, eye: np.ndarray) -> np.ndarray:
    """P の地図から面の向き（隣の画素との差の外積）。同じ ID の隣が無い所は片側の差で、それも無ければ nan。"""
    H, W = ID.shape

    def diff(axis: int) -> np.ndarray:
        fwd = np.full(P.shape, np.nan)
        bwd = np.full(P.shape, np.nan)
        sl_a = [slice(None)] * 2
        sl_b = [slice(None)] * 2
        sl_a[axis], sl_b[axis] = slice(1, None), slice(None, -1)
        same = ID[tuple(sl_a)] == ID[tuple(sl_b)]
        dd = np.where(same[..., None], P[tuple(sl_a)] - P[tuple(sl_b)], np.nan)
        fwd[tuple(sl_b)] = dd
        bwd[tuple(sl_a)] = dd
        return np.where(np.isfinite(fwd), fwd, bwd)

    n = np.cross(diff(1), diff(0))
    ln = np.linalg.norm(n, axis=-1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        n = n / ln
        s = np.sign(np.sum(n * (np.asarray(eye) - P), axis=-1, keepdims=True))
    return n * s


def sample_nearest(img: np.ndarray, u: np.ndarray, v: np.ndarray, fill=np.nan) -> np.ndarray:
    H, W = img.shape[:2]
    iu, iv = np.floor(u).astype(np.int64, copy=False), np.floor(v).astype(np.int64, copy=False)
    ok = np.isfinite(u) & np.isfinite(v) & (iu >= 0) & (iu < W) & (iv >= 0) & (iv < H)
    out = np.full(u.shape, fill, dtype=float)
    out[ok] = img[iv[ok], iu[ok]]
    return out


def sample_linear(img: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    import cv2

    mu = np.nan_to_num(u - 0.5, nan=-10).astype(np.float32)
    mv = np.nan_to_num(v - 0.5, nan=-10).astype(np.float32)
    return cv2.remap(img.astype(np.float32), mu, mv, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def visible_in(src: View, P: np.ndarray, mask_src: np.ndarray | None = None,
               N: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """点 P（(..., 3)）が視点 src から見えるか（枠の中・前・深度バッファで隠れていない・src の exclude の外。
    N（点の面の向き）があれば、src のカメラが面の表の側に MAX_VIEW_DEG より立った角度であること：厚みの無い壁の裏や
    寝た角度からは貼らない）。返り値 (見える, u, v)。"""
    u, v, d = project(src.cam, np.nan_to_num(P), src.size)
    D = sample_nearest(src.depth, u, v)
    ok = np.isfinite(P[..., 0]) & (d > 0.1) & np.isfinite(D) & (d <= D + DEPTH_TOL_ABS + DEPTH_TOL_REL * D)
    if N is not None:
        with np.errstate(invalid="ignore", divide="ignore"):
            to_cam = src.cam.center - P
            facing = np.sum(N * to_cam, axis=-1) / np.linalg.norm(to_cam, axis=-1)
        ok &= ~(np.isfinite(facing) & (facing <= math.cos(math.radians(MAX_VIEW_DEG))))
    if mask_src is not None:
        ok &= ~(sample_nearest(mask_src.astype(float), u, v, fill=1.0) > 0.5)
    return ok, u, v


# --- 縁の強さ --------------------------------------------------------------------------------

def gradients(gray: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    import cv2

    g = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), EDGE_BLUR)
    return cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)


def edge_strength(gray: np.ndarray, valid: np.ndarray | None = None) -> np.ndarray:
    """勾配の大きさを広げて、valid の中の 90 パーセンタイルで割った物（写真の明るさ・コントラストの違いを消す）。"""
    import cv2

    gx, gy = gradients(gray)
    m = cv2.GaussianBlur(np.hypot(gx, gy), (0, 0), EDGE_SPREAD)
    ref = m[valid] if valid is not None and valid.any() else m
    s = float(np.percentile(ref, 90)) if ref.size else 1.0
    return m / (s if s > 1e-6 else 1.0)


def compare_edges(ea_warped: np.ndarray, eb: np.ndarray, ok: np.ndarray) -> np.ndarray:
    """縁の強さの差の絶対値（ok の外は nan）。"""
    d = np.abs(ea_warped - eb)
    return np.where(ok, d, np.nan)


def aggregate_by_id(diff: np.ndarray, ID: np.ndarray, labels: dict, min_px: int = MIN_PX,
                    num: np.ndarray | None = None, den: np.ndarray | None = None, P: np.ndarray | None = None) -> list[dict]:
    """ID ごとの平均・90 パーセンタイル・画素数（diff が nan の画素は数えない）。num/den があれば相対値 Σ|差|/Σ(縁の和) も。"""
    ok = np.isfinite(diff) & (ID > 0)
    out = []
    for i in np.unique(ID[ok]):
        m = ok & (ID == i)
        n = int(m.sum())
        if n < min_px:
            continue
        vals = diff[m]
        row = {"id": labels.get(int(i), f"#{int(i)}"), "n_px": n, "mean": round(float(vals.mean()), 4),
               "p90": round(float(np.percentile(vals, 90)), 4)}
        if num is not None and den is not None:
            dd = float(den[m].sum())
            row["rel"] = round(float(num[m].sum()) / dd, 4) if dd > 0 else None
        if P is not None:                         # 一番食い違う所（その ID の上位 10% の画素）の世界座標の中央値
            hot = m & (diff >= np.percentile(vals, 90))
            q = P[hot]
            q = q[np.isfinite(q[:, 0])]
            if len(q):
                row["hot_xyz"] = [round(float(x), 2) for x in np.median(q, axis=0)]
        out.append(row)
    out.sort(key=lambda r: -r["mean"])
    return out


CELL_M = (4.0, 3.0)       # 面の中の升（面に沿う m × 高さ m）


def face_cells(diff: np.ndarray, ID: np.ndarray, labels: dict, P: np.ndarray, faces: dict,
               cell: tuple[float, float] = CELL_M, min_px: int = MIN_PX // 2) -> list[dict]:
    """面の壁（ラベル face:<面>）の食い違いを、面の座標（s：面の始点からの m、z：模型の高さ m）の升ごとに。"""
    from .registry import world_to_plane

    ok = np.isfinite(diff)
    out = []
    for i, lab in labels.items():
        if not lab.startswith("face:") or lab.count(":") != 1:
            continue
        f = faces.get(lab[5:])
        m = ok & (ID == i)
        if f is None or m.sum() < min_px:
            continue
        q = world_to_plane(f, P[m])
        ks, kz = np.floor(q[:, 0] / cell[0]).astype(int), np.floor(q[:, 2] / cell[1]).astype(int)
        vals = diff[m]
        for a, c in set(zip(ks.tolist(), kz.tolist())):
            sel = (ks == a) & (kz == c)
            if sel.sum() < min_px:
                continue
            v = vals[sel]
            out.append({"face": lab[5:], "s": [a * cell[0], (a + 1) * cell[0]], "z": [c * cell[1], (c + 1) * cell[1]],
                        "n_px": int(sel.sum()), "mean": round(float(v.mean()), 4), "p90": round(float(np.percentile(v, 90)), 4)})
    out.sort(key=lambda r: -r["mean"])
    return out


def kind_of(label: str) -> str:
    if label.startswith("part:"):
        return "部品"
    if label.startswith("face:") or label.startswith("body:"):
        return "面"
    return "その他"


# --- 写真の exclude（面の m の矩形 → 写真の多角形）---------------------------------------------

def exclude_mask(entries: list[dict], size: tuple[int, int]) -> np.ndarray:
    """登録の exclude（登録の面の (s0, s1, z0, z1) m）を H_plane で写真に写し、描画の大きさのマスクに。"""
    import cv2

    from .registry import apply_H

    W, H = size
    m = np.zeros((H, W), np.uint8)
    for e in entries:
        w0, h0 = e["image_size"]
        sx, sy = W / w0, H / h0
        notes = e.get("exclude_note") or []
        for i, x in enumerate(e.get("exclude") or []):
            rect = x.get("rect") if isinstance(x, dict) else x
            s0, s1, z0, z1 = (float(q) for q in rect)
            S = np.array([s0, s1, s1, s0])
            Z = np.array([z0, z0, z1, z1])
            u, v, w = apply_H(e["H_plane"], S, Z)
            if (w <= 0).any():
                continue
            pts = np.stack([u * sx, v * sy], axis=1).round().astype(np.int32)
            cv2.fillPoly(m, [pts], 1)
            _ = notes
    return m.astype(bool)


# --- 写真の組の比較 ----------------------------------------------------------------------------

@dataclass
class Photo:
    pid: str
    short: str
    entries: list
    view: View
    gray: np.ndarray                 # 描画の大きさに縮めた写真（0..255）
    excl: np.ndarray                 # exclude のマスク（描画の大きさ）
    edge: np.ndarray = field(init=False)
    bgr: np.ndarray | None = None

    def __post_init__(self):
        self.edge = edge_strength(self.gray, self.view.hit & ~self.excl)


def sampling_ratio(u: np.ndarray, v: np.ndarray, ok: np.ndarray) -> float:
    """target の 1 画素が A の写真の何画素に当たるか（写像の微分の行列式の平方根の中央値）。1 未満＝A の方が粗い。"""
    if ok.sum() < 16:
        return 1.0
    uy, ux = np.gradient(np.where(ok, u, np.nan))
    vy, vx = np.gradient(np.where(ok, v, np.nan))
    det = np.abs(ux * vy - uy * vx)
    m = ok & np.isfinite(det)
    return float(np.sqrt(np.median(det[m]))) if m.any() else 1.0


def blur_sigma(ratio: float) -> float:
    """細かい方を粗い方に合わせるぼかし（画素）。比が 0.8〜1.25 なら何もしない。"""
    r = ratio if ratio >= 1 else 1.0 / max(ratio, 1e-3)
    return 0.0 if r < 1.25 else 0.5 * math.sqrt(r * r - 1)


def _blurred_edge(ph: Photo, sigma: float) -> np.ndarray:
    import cv2

    if sigma <= 0:
        return ph.edge
    return edge_strength(cv2.GaussianBlur(ph.gray, (0, 0), sigma), ph.view.hit & ~ph.excl)


def project_into(A: Photo, target: View, target_excl: np.ndarray | None = None,
                 edge: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """A の写真を模型に貼り、target の視点から見た縁の強さと（貼れた所の）マスク。返り値 (縁, 灰色, ok, u, v)。"""
    ok, u, v = visible_in(A.view, target.P, A.excl, target.N)
    ok &= target.hit
    if target_excl is not None:
        ok &= ~target_excl
    ea = sample_linear(A.edge if edge is None else edge, u, v)
    ga = sample_linear(A.gray, u, v)
    return np.where(ok, ea, 0.0), np.where(ok, ga, 0.0), ok, u, v


def compare_pair(A: Photo, B: Photo, labels: dict, min_px: int = MIN_PX, faces: dict | None = None) -> dict:
    """A を貼った模型を B の視点で描き、B の実写と縁の強さで比べる。写真の細かさが違えば、細かい方をぼかして揃える
    （遠い写真を近い写真に貼ると煉瓦の目地が消えて、形と関係ない食い違いになるため）。"""
    ea, _, ok, u, v = project_into(A, B.view, B.excl)
    ratio = sampling_ratio(u, v, ok)
    sig = blur_sigma(ratio)
    eb = B.edge
    if sig > 0 and ratio < 1:                     # A が粗い：B をぼかす
        eb = _blurred_edge(B, sig)
    elif sig > 0:                                 # A が細かい：A をぼかしてから貼る
        ea, _, ok, u, v = project_into(A, B.view, B.excl, _blurred_edge(A, sig))
    diff = compare_edges(ea, eb, ok)
    rows = aggregate_by_id(diff, B.view.ID, labels, min_px, num=np.abs(ea - eb), den=ea + eb, P=B.view.P)
    bld = np.isfinite(diff) & np.isin(B.view.ID, [i for i, l in labels.items() if kind_of(l) != "その他"])
    base = float(diff[bld].mean()) if bld.any() else float("nan")       # この組の建物全体の平均（写真の季節・明るさの違いの分）
    for r in rows:
        r["kind"] = kind_of(r["id"])
        r["score"] = round(r["mean"] / base, 3) if base > 0 else None
    cells = face_cells(diff, B.view.ID, labels, B.view.P, faces) if faces else []
    for c in cells:
        c["score"] = round(c["mean"] / base, 3) if base > 0 else None
    return {"A": A.pid, "B": B.pid, "n_overlap_px": int(ok.sum()), "diff": diff, "ok": ok, "ids": rows, "cells": cells,
            "baseline": round(base, 4), "sampling_ratio": round(ratio, 3), "blur_px": round(sig, 2)}


# --- 模型の縁の裏付け（写真 1 枚ずつ。比べる相手が要らない）------------------------------------------

SUPPORT_LOW = 0.3          # 模型の縁のうち、写真の縁（強い縁の 0.5 倍以上）が 2 px 以内にある割合がこれ未満なら印
SUPPORT_MIN_PX = 60


def edge_owner(view: View) -> np.ndarray:
    """模型の縁の画素に、手前の側の ID を入れた地図（縁でない所は 0）。"""
    ID, D = view.ID, np.nan_to_num(view.depth, nan=1e6)
    own = np.zeros(ID.shape, int)
    for dy, dx in ((0, 1), (1, 0)):
        a, b = ID[: ID.shape[0] - dy, : ID.shape[1] - dx], ID[dy:, dx:]
        da, db = D[: D.shape[0] - dy, : D.shape[1] - dx], D[dy:, dx:]
        jump = (a != b) | (np.abs(da - db) > 0.05 * np.minimum(da, db))
        o = np.where(da <= db, a, b)
        sub = own[: ID.shape[0] - dy, : ID.shape[1] - dx]
        sub[jump] = o[jump]
    return own


def edge_support(ph: "Photo", labels: dict, low: float = SUPPORT_LOW, min_px: int = SUPPORT_MIN_PX) -> list[dict]:
    """写真の視点で、模型の縁（ID の境・深度の段）の脇 2 px 以内に写真の縁があるかを ID ごとに。
    無い割合が高い＝模型には縁があるのに写真には無い（その位置に物が無い・位置が違う）。弱い印。"""
    import cv2

    gx, gy = gradients(ph.gray)
    valid = ph.view.hit & ~ph.excl
    mag = np.hypot(gx, gy)
    ref = float(np.percentile(mag[valid], 90)) if valid.any() else 1.0
    near = cv2.dilate(mag / max(ref, 1e-6), np.ones((5, 5), np.uint8))
    own = edge_owner(ph.view)
    own[ph.excl] = 0
    out = []
    for i in np.unique(own[own > 0]):
        m = own == i
        if m.sum() < min_px:
            continue
        sup = float(np.mean(near[m] > 0.5))
        if sup < low:
            ys, xs = np.nonzero(m)
            out.append({"id": labels.get(int(i), f"#{int(i)}"), "kind": kind_of(labels.get(int(i), "")), "photo": ph.pid,
                        "support": round(sup, 3), "n_px": int(m.sum()),
                        "where_xyz": [round(float(q), 2) for q in np.nanmedian(ph.view.P[ys, xs], axis=0)]})
    return sorted(out, key=lambda r: r["support"])


# --- 1 枚しか無い所（視点を回して縁が離れるか）---------------------------------------------------

def model_edges(view: View) -> np.ndarray:
    """模型の縁：ID の境と深度の段（相対 5% 以上）。"""
    ID, D = view.ID, np.nan_to_num(view.depth, nan=1e6)
    e = np.zeros(ID.shape, bool)
    for dy, dx in ((0, 1), (1, 0)):
        a, b = ID[: ID.shape[0] - dy, : ID.shape[1] - dx], ID[dy:, dx:]
        da, db = D[: D.shape[0] - dy, : D.shape[1] - dx], D[dy:, dx:]
        jump = (a != b) | (np.abs(da - db) > 0.05 * np.minimum(da, db))
        e[: ID.shape[0] - dy, : ID.shape[1] - dx] |= jump
        e[dy:, dx:] |= jump
    return e


def parallel_offset_edges(gray: np.ndarray, ok: np.ndarray, edges: np.ndarray, norm: float) -> tuple[np.ndarray, np.ndarray]:
    """模型の縁の脇（BAND_PX）で、模型の縁と平行に流れる写真の縁の強さ（norm で割る）。返り値 (強さ, 帯のマスク)。"""
    import cv2

    gx, gy = gradients(np.where(ok, gray, 0).astype(np.float32))
    okk = cv2.erode(ok.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)      # 貼れた所の境の縁は数えない
    src = (~edges).astype(np.uint8)
    dist, lab = cv2.distanceTransformWithLabels(src, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)
    ys, xs = np.nonzero(edges)
    if ys.size == 0:
        return np.zeros(gray.shape), np.zeros(gray.shape, bool)
    ey = np.zeros(lab.max() + 1)
    ex = np.zeros(lab.max() + 1)
    lab_at_edges = lab[ys, xs]
    ey[lab_at_edges], ex[lab_at_edges] = ys, xs
    yy, xx = np.mgrid[0: gray.shape[0], 0: gray.shape[1]]
    ny, nx = yy - ey[lab], xx - ex[lab]
    nn = np.hypot(nx, ny)
    nn[nn == 0] = 1
    nx, ny = nx / nn, ny / nn                     # 模型の縁に直角な向き
    g = np.hypot(gx, gy)
    along = np.abs(gx * nx + gy * ny)             # 勾配が縁に直角＝写真の縁が模型の縁と平行
    par = np.where(along >= PARALLEL_COS * np.maximum(g, 1e-6), along, 0.0) / max(norm, 1e-6)
    band = okk & (dist >= BAND_PX[0]) & (dist <= BAND_PX[1])
    return par, band


def single_mask(view: View, others: list["Photo"]) -> np.ndarray:
    """view の画素の点が、他のどの写真からも見えない（＝1 枚しか無い）所。"""
    seen = np.zeros(view.ID.shape, bool)
    for o in others:
        ok, _, _ = visible_in(o.view, view.P, o.excl, view.N)
        seen |= ok
    return view.hit & ~seen


def suspects_for(A: Photo, shifted: list[View], others: list["Photo"], labels: dict, min_px: int = MIN_PX // 3) -> list[dict]:
    """A しか写していない所で、±SHIFT_DEG 回した視点で模型の縁の脇に平行な写真の縁が増えた ID（弱い印）。"""
    import cv2

    gx, gy = gradients(A.gray)
    norm = float(np.percentile(np.hypot(gx, gy)[A.view.hit & ~A.excl], 90)) if (A.view.hit & ~A.excl).any() else 1.0
    base_ok = A.view.hit & ~A.excl & single_mask(A.view, others)
    par0, band0 = parallel_offset_edges(A.gray, A.view.hit & ~A.excl, model_edges(A.view), norm)
    base = {}
    for i in np.unique(A.view.ID[band0 & base_ok]):
        m = band0 & base_ok & (A.view.ID == i)
        if m.sum() >= min_px:
            base[int(i)] = float(par0[m].mean())
    best: dict = {}
    for S in shifted:
        _, g, ok, _, _ = project_into(A, S)
        ok &= single_mask(S, others)
        par, band = parallel_offset_edges(g, ok, model_edges(S), norm)
        sel = band & ok
        for i in np.unique(S.ID[sel]):
            m = sel & (S.ID == i)
            if m.sum() < min_px or int(i) not in base:
                continue
            val = float(par[m].mean())
            inc = val - base[int(i)]
            if inc >= SUSPECT_MIN and (int(i) not in best or inc > best[int(i)]["increase"]):
                ys, xs = np.nonzero(m)
                best[int(i)] = {"id": labels.get(int(i), f"#{int(i)}"), "photo": A.pid, "view": S.name,
                                "increase": round(inc, 4), "shifted": round(val, 4), "original": round(base[int(i)], 4),
                                "n_px": int(m.sum()), "where_xyz": [round(float(q), 2) for q in np.nanmedian(S.P[ys, xs], axis=0)]}
    _ = cv2
    out = sorted(best.values(), key=lambda r: -r["increase"])
    for r in out:
        r["kind"] = kind_of(r["id"])
    return out


# --- 熱図 --------------------------------------------------------------------------------------

def _font(size: int):
    from PIL import ImageFont

    for f in ("C:/Windows/Fonts/meiryo.ttc", "C:/Windows/Fonts/msgothic.ttc"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def heat_image(B: Photo, res: dict, labels_top: list[dict], out_png: Path, vmax: float = 1.0) -> Path:
    """B の実写（灰色）に、食い違い（0〜vmax を青→赤）を重ね、ID の境を白い細線、上位の ID に番号を付ける。"""
    import cv2
    from PIL import Image, ImageDraw

    base = cv2.cvtColor(B.gray.astype(np.uint8), cv2.COLOR_GRAY2BGR).astype(np.float32)
    d = np.nan_to_num(res["diff"], nan=0.0)
    ok = res["ok"]
    sm = cv2.GaussianBlur(d.astype(np.float32), (0, 0), 2.0)
    wt = cv2.GaussianBlur(ok.astype(np.float32), (0, 0), 2.0)
    sm = np.where(wt > 1e-3, sm / np.maximum(wt, 1e-3), 0)
    col = cv2.applyColorMap(np.clip(sm / vmax * 255, 0, 255).astype(np.uint8), cv2.COLORMAP_JET).astype(np.float32)
    a = 0.55
    img = np.where(ok[..., None], base * (1 - a) + col * a, base * 0.6)
    img[model_edges(B.view) & B.view.hit] = (255, 255, 255)
    pil = Image.fromarray(cv2.cvtColor(img.clip(0, 255).astype(np.uint8), cv2.COLOR_BGR2RGB))
    dr = ImageDraw.Draw(pil)
    f = _font(max(12, pil.width // 70))
    inv = {v: k for k, v in res.get("labels", {}).items()}
    for k, r in enumerate(labels_top):
        i = inv.get(r["id"])
        if i is None:
            continue
        ys, xs = np.nonzero(ok & (B.view.ID == i))
        if ys.size == 0:
            continue
        x, y = int(np.median(xs)), int(np.median(ys))
        t = f"{k + 1}. {r['id']} {r['mean']:.2f}"
        dr.text((x + 1, y + 1), t, fill=(0, 0, 0), font=f)
        dr.text((x, y), t, fill=(255, 255, 0), font=f)
    dr.text((8, 6), f"{res['A']} を貼った模型 vs {res['B']} の実写（縁の強さの差。赤ほど食い違い大。白線＝模型の縁）",
            fill=(255, 255, 255), font=f)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    pil.save(out_png)
    return out_png


# --- 全体 --------------------------------------------------------------------------------------

def rank_ids(pairs: list[dict]) -> tuple[list[dict], list[dict]]:
    """面・部品ごとの悪い順。写真の組（向きを問わない 2 枚）ごとに A→B と B→A の score（組の建物全体の平均に対する比）を
    平均し、組の中で一番悪い値をその面・部品の値にする。"""
    per: dict = {}
    for pr in pairs:
        key = tuple(sorted((pr["A"], pr["B"])))
        for r in pr["ids"]:
            per.setdefault((r["id"], key), []).append((pr, r))
    agg: dict = {}
    for (lab, key), lst in per.items():
        sc = [r["score"] for _, r in lst if r.get("score") is not None]
        if not sc:
            continue
        score = float(np.mean(sc))
        worst_pr, worst = max(lst, key=lambda t: t[1]["mean"])
        row = {"id": lab, "kind": worst["kind"], "score": round(score, 3),
               "mean": round(float(np.mean([r["mean"] for _, r in lst])), 4),
               "p90": round(float(np.mean([r["p90"] for _, r in lst])), 4),
               "n_px": int(sum(r["n_px"] for _, r in lst)), "rel": worst.get("rel"),
               "pair": "↔".join(short_id(k) for k in key), "directions": len(lst), "hot_xyz": worst.get("hot_xyz")}
        cur = agg.get(lab)
        if cur is None or row["score"] > cur["score"]:
            row["n_pairs"] = (cur or {}).get("n_pairs", 0) + 1
            agg[lab] = row
        else:
            cur["n_pairs"] = cur.get("n_pairs", 1) + 1
    rows = sorted(agg.values(), key=lambda r: -r["score"])
    return [r for r in rows if r["kind"] != "その他"], [r for r in rows if r["kind"] == "その他"]


def rank_cells(pairs: list[dict], n: int = 20) -> list[dict]:
    """面の中の升（4 m × 3 m）の悪い順（score）。同じ升は組・向きの中で一番悪い物だけ。"""
    best: dict = {}
    for pr in pairs:
        for c in pr.get("cells", []):
            k = (c["face"], tuple(c["s"]), tuple(c["z"]))
            if c.get("score") is not None and (k not in best or c["score"] > best[k]["score"]):
                best[k] = {**c, "pair": f"{short_id(pr['A'])}→{short_id(pr['B'])}"}
    return sorted(best.values(), key=lambda r: -r["score"])[:n]


def shift_views(deg: float) -> list[tuple[str, float, bool]]:
    """ずらした視点：左右 ±deg と、上へ deg（下へは地面に潜るので回さない）。横の縁の奥行きの違いは上下に回さないと出ない。"""
    k = int(round(deg))
    return [(f"p{k}", deg, False), (f"m{k}", -deg, False), (f"u{k}", deg, True)]


def short_id(pid: str) -> str:
    if pid.startswith("PXL_"):
        return pid.split("_")[-1].split(".")[0]
    return pid.split("_")[0]


def run_blender(building_json: Path, spec: dict, rdir: Path, blender: str | None = None) -> None:
    from .model import blender_path

    rdir.mkdir(parents=True, exist_ok=True)
    sp = rdir / "spec.json"
    sp.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    cmd = [str(blender_path(blender)), "-b", "-P", str(HERE / "blender_build.py"), "--", str(building_json.resolve()),
           str(rdir.resolve()), f"--photoproj={sp.resolve()}"]
    from .ledger import lock   # GPU は 1 枚（ledger.py）
    with lock("gpu", label=f"photoproj {building_json.stem}"):
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or "Traceback" in r.stdout or not (rdir / "pp_ids.json").exists():
        print(r.stdout[-4000:], r.stderr[-2000:], file=sys.stderr)
        raise SystemExit(f"Blender が失敗しました（終了コード {r.returncode}）")


def load_view(rdir: Path, name: str, cam: Cam, size) -> View:
    z = np.load(rdir / f"{name}.npz")
    return View(name, cam, tuple(size), z["P"].astype(float), z["ID"])


def photoproj(building_json: Path, photos: list[str] | None = None, fast: bool = False, blender: str | None = None,
              registry: dict | None = None, out_root: Path | None = None, keep_renders: bool = False,
              shift_deg: float = SHIFT_DEG, reuse: Path | None = None) -> tuple[dict, Path]:
    from .rectify import read_image
    from .registry import entries_for, expand_entries, load_registry, photo_path
    from .schema import load_building
    import cv2

    b = load_building(building_json)
    faces = {f["id"]: f for f in b.get("faces", []) if f.get("from") and f.get("to")}
    reg = registry if registry is not None else load_registry()
    ents = [e for e in expand_entries(entries_for(reg, b["id"]), b) if not photos or any(q in e["photo"] for q in photos)]   # 頭・途中の一部でも可
    out = (out_root or Path("out") / b["id"] / "photoproj") / datetime.now().strftime("%Y%m%d_%H%M%S")
    rdir = Path(reuse) if reuse else out / "render"      # reuse：前の --keep-renders の描画を使う（棟 JSON を直したら使わない）
    out.mkdir(parents=True, exist_ok=True)
    scale = SCALE_FAST if fast else SCALE
    warnings: list[str] = []
    by_photo: dict = {}
    for e in ents:
        by_photo.setdefault(e["photo"], []).append(e)
    cams, views_spec, pivots = {}, [], {}
    for pid, es in by_photo.items():
        cam = cam_from_entry(es[0])
        if cam is None:
            warnings.append(f"{pid}: 登録に視点（camera の R_cv・t_cv）が無いので貼れない")
            continue
        if not photo_path(es[0]).exists():
            warnings.append(f"{pid}: 写真が見つからない（{photo_path(es[0])}）")
            continue
        size = cam.size(scale)
        cams[pid] = (cam, size)
        views_spec.append(blender_view(cam, size, short_id(pid)))
        pv = pivot_of(es[0])
        if pv is not None:
            pivots[pid] = pv
            for tag, d, up in shift_views(shift_deg):
                views_spec.append(blender_view(orbit(cam, pv, d, up), size, f"{short_id(pid)}__{tag}"))
    if not cams:
        raise SystemExit("貼れる写真がありません（登録に視点が無いか、写真が見つからない）")
    if reuse:
        keep_renders = True
    else:
        run_blender(building_json, {"views": views_spec}, rdir, blender)
    labels = {int(k): v for k, v in json.loads((rdir / "pp_ids.json").read_text(encoding="utf-8")).items()}

    P: dict = {}
    for pid, (cam, size) in cams.items():
        es = by_photo[pid]
        img = read_image(photo_path(es[0]))
        if [img.shape[1], img.shape[0]] != [cam.w, cam.h]:
            warnings.append(f"{pid}: 写真の大きさが登録と違う {img.shape[1]}x{img.shape[0]}")
            continue
        small = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
        v = load_view(rdir, short_id(pid), cam, size)
        ph = Photo(pid, short_id(pid), es, v, cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32), exclude_mask(es, size))
        P[pid] = ph
        # 投影の往復：描いた点を自分のカメラに投げ直すと、その画素の中心に戻るはず
        uu, vv, _ = project(cam, np.nan_to_num(v.P), size)
        H, W = v.ID.shape
        gu, gv = np.meshgrid(np.arange(W) + 0.5, np.arange(H) + 0.5)
        rt = np.hypot(uu - gu, vv - gv)[v.hit]
        if rt.size and float(np.percentile(rt, 99)) > 1.0:
            warnings.append(f"{pid}: Blender の描画と numpy の投影が合わない（往復 99% 点 {np.percentile(rt, 99):.2f} px）")

    pairs, heat_files = [], []
    pids = list(P)
    for a in pids:
        for bb in pids:
            if a == bb:
                continue
            res = compare_pair(P[a], P[bb], labels, faces=faces)
            if res["n_overlap_px"] < MIN_PX * 4:
                continue
            res["labels"] = labels
            top = [r for r in res["ids"] if r["kind"] != "その他"][:6]
            png = heat_image(P[bb], res, top, out / f"heat_{P[a].short}_from_{P[bb].short}.png")
            heat_files.append(str(png))
            pairs.append({"A": a, "B": bb, "n_overlap_px": res["n_overlap_px"], "baseline": res["baseline"],
                          "sampling_ratio": res["sampling_ratio"], "blur_px": res["blur_px"], "heat": str(png),
                          "ids": res["ids"], "cells": res["cells"][:12]})
    ranking, others = rank_ids(pairs)
    cells = rank_cells(pairs)

    suspects = []
    for pid, ph in P.items():
        sh = []
        for tag, d, up in shift_views(shift_deg):
            nm = f"{ph.short}__{tag}"
            if (rdir / f"{nm}.npz").exists():
                sh.append(load_view(rdir, nm, orbit(ph.view.cam, pivots[pid], d, up), ph.view.size))
        if sh:
            suspects += suspects_for(ph, sh, [o for q, o in P.items() if q != pid], labels)
    suspects.sort(key=lambda r: -r["increase"])
    unsupported = sorted([r for ph in P.values() for r in edge_support(ph, labels)], key=lambda r: r["support"])

    summary = {"building": b["id"], "building_json": str(building_json), "made_by": "photoproj（recorder）",
               "made_at": datetime.now().isoformat(timespec="seconds"),
               "method": ("Blender で写真の視点ごとに世界座標と面・部品の ID を描き、写真の投影（UV Project と同じ式）・"
                          "見えるかどうか（深度の比較）・exclude のマスクは numpy。比べるのは縁の強さ（勾配の大きさ、90 パーセンタイルで正規化）"),
               "scale": f"1/{scale}", "photos": list(P), "pairs": pairs, "ranking": ranking, "cells": cells, "others": others,
               "suspects": suspects, "unsupported": unsupported, "warnings": warnings,
               "note": ("mean・p90 は縁の強さの差（写真の強い縁＝1）。rel は Σ|差|/Σ(両方の縁)。値は形の悪さの目安で寸法ではない。"
                        "木・人・ガラスの映り込みも食い違いになるので、上位は写真で目視して確かめる。suspects は 1 枚しか無い所の弱い印")}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "summary.md").write_text(summary_md(summary), encoding="utf-8")
    if not keep_renders:
        shutil.rmtree(rdir, ignore_errors=True)
    return summary, out


def summary_md(s: dict) -> str:
    L = [f"# 写真の投影で見る模型の悪い所（{s['building']}、{s['made_at']}）", "",
         f"写真 {len(s['photos'])} 枚、組 {len(s['pairs'])}（両向き）。描画 {s['scale']}。{s['note']}", "",
         "## 悪い順（面・部品）", "",
         "score＝その組の建物全体の平均に対する比（1 より大きいほど、その組の中で悪い）。両向きの平均で、組の中で一番悪い値。"
         "平均・p90 は縁の強さの差（両向きの平均）。目安の位置は一番食い違う画素の世界座標の中央値。", "",
         "| 順 | 面・部品 | 種類 | score | 平均 | p90 | 画素 | 組 | 目安の位置 (x, y, z) |", "|---|---|---|---|---|---|---|---|---|"]
    for k, r in enumerate(s["ranking"][:25]):
        L.append(f"| {k + 1} | {r['id']} | {r['kind']} | {r['score']:.2f} | {r['mean']:.3f} | {r['p90']:.3f} | {r['n_px']} | {r['pair']} | {r.get('hot_xyz') or '—'} |")
    L += ["", "## 面の中の悪い所（升 4 m × 3 m。s は面の始点から、z は模型の高さ）", "",
          "| 順 | 面 | s m | z m | score | 平均 | 画素 | 組 |", "|---|---|---|---|---|---|---|---|"]
    for k, c in enumerate(s.get("cells", [])[:15]):
        L.append(f"| {k + 1} | {c['face']} | {c['s'][0]:.0f}–{c['s'][1]:.0f} | {c['z'][0]:.0f}–{c['z'][1]:.0f} | {c['score']:.2f} | {c['mean']:.3f} | {c['n_px']} | {c['pair']} |")
    L += ["", "## 写真の組ごと", ""]
    for pr in s["pairs"]:
        top = [r for r in pr["ids"] if r["kind"] != "その他"][:5]
        L.append(f"- {short_id(pr['A'])} → {short_id(pr['B'])}（重なり {pr['n_overlap_px']} px、全体の平均 {pr['baseline']}）: "
                 + "、".join(f"{r['id']} {r['mean']:.2f}" for r in top) + f"　`{Path(pr['heat']).name}`")
    L += ["", "## 1 枚しか無い所の「怪しい」（弱い印。視点を左右 ±15°・上へ 15° 回すと模型の縁の脇に平行な写真の縁が増えた）", ""]
    if not s["suspects"]:
        L.append("- なし")
    for r in s["suspects"][:15]:
        L.append(f"- {r['id']}（{short_id(r['photo'])}、{r['view']}）: +{r['increase']:.3f}（{r['original']:.3f}→{r['shifted']:.3f}）、"
                 f"{r['n_px']} px、目安の位置 {r['where_xyz']}")
    L += ["", "## 模型の縁に写真の縁が無い所（弱い印。写真 1 枚ずつ。模型の縁の脇 2 px に写真の縁がある割合）", ""]
    if not s.get("unsupported"):
        L.append("- なし")
    for r in s.get("unsupported", [])[:15]:
        L.append(f"- {r['id']}（{short_id(r['photo'])}）: 裏付け {r['support']:.2f}、縁 {r['n_px']} px、目安の位置 {r['where_xyz']}")
    if s["others"]:
        L += ["", "## その他（地面・木など。形の判定には使わない）", ""]
        L += [f"- {r['id']} {r['mean']:.3f}（{r['pair']}）" for r in s["others"][:8]]
    if s["warnings"]:
        L += ["", "## 注意", ""] + [f"- {w}" for w in s["warnings"]]
    return "\n".join(L) + "\n"
