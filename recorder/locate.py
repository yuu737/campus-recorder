"""模型合わせ（11/6 の試し。結論：GPS だけからの探索は使えない。目印の対応 → PnP が本線）。

狙い
    登録に要るのは「どこから・どの向きで・どの画角で」撮ったか。鎖や一括復元は写真どうしの重なりが要り、本部棟 103 枚は
    17 枚しか載らなかった。模型はもうあるので、GPS と方位を初期値に 1 枚ずつ模型の縁を写真の縁に合わせれば済む、と考えた。

本部棟の登録済み 17 枚（視点が既知）で測った結果（docs/日誌.md 11/6）
    - 使える部品：写真の鉛直線の消失点から仰角と傾き（EdgeDT.estimate_tilt）。誤差の中央値 仰角 1.2°・傾き 0.5°（17 枚）。
      回転の約束（rotation_cv / angles_of）は登録のカメラと一致。
    - 使えない：縁の距離変換（Chamfer）はタイルの目地で縁が画面の 9 % を埋め、どの視点でも点が縁の近くに落ちる（真値と誤答の差が無い）。
      線の向きに直角な勾配（grad_scores）なら真値 0.31〜0.41・誤答 0.15〜0.23 と分かれるが、粗い格子（4 m・6°）では真値が
      200〜1300 位に埋もれ、面の前に絞っても同じ。真値は極小（真値から動かない）だが谷が狭く、3 m 離れた種からの座標降下は
      隣の柱割り（本部棟は 3.5 m 周期）の極小に落ちる（17 枚で 2 m 以内 0 %。ぼかしを 6→3→1.5 と細くしても 50 %）。
    - 決め手：目印（面の四隅・1/4 点）の対応を幅の 1.5 % の誤差で与えて PnP → 位置の誤差の中央値 1.0 m（8 枚中 7 枚が 2 m 以内）、
      3 % でも 1.5 m。その後に勾配の細探索を掛けると悪化する（2.4 m）ので掛けない。

つまり「どの目印が写真のどこか」を係（人）が指し、道具は PnP で解いて再投影の残差で合否を出す、が本線。
係に要る精度は目印 4 点以上・幅の 1.5〜3 %（升目で指して寄りで詰める）。これは本部棟の 17 枚を人が 4 点指して登録したのと同じ原理で、
写真どうしの重なりは要らない。GPS・方位・消失点の仰角は種と検算に使う。

残している物：EdgeDT（縁・勾配・消失点）、model_points（模型の線の点）、rotation_cv / angles_of、grad_scores（合否の検算に使える）、
coarse_search / refine / search_from_seed（試した記録。`recorder photos locate` から動くが登録には使わない）。
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from . import registry as R_
from .photos import INDEX_PATH, load_index, true_heading
from .registry import face_frame, face_rect_frame, plane_to_world
from .schema import load_building

LONG_SIDE = 1600          # 探索に使う写真の長辺（px）
TRUNC_COARSE = 24.0       # 粗探索の距離の上限（px）
TRUNC_FINE = 12.0         # 細探索の距離の上限（px）
INLIER_PX = 3.0           # 「縁に乗った」と見なす距離（px、長辺 1600 で）
RADIUS_M = 40.0           # GPS の周りの探索半径
STEP_M = 2.0              # 粗探索の格子（m）
YAW_RANGE = 35.0          # 方位の探索幅（±度）
YAW_STEP = 3.0
PITCHES = (-6.0, 0.0, 6.0, 12.0, 18.0, 24.0, 30.0)
CAM_HEIGHT = 1.5          # 粗探索のカメラ高さ（地面から m）
N_COARSE_PTS = 160        # 粗探索に使う模型の点の数（面ごとに間引き）
TOP_K = 48                # 粗探索で残す候補
DISTINCT_M = 4.0          # 候補を「別の場所」と見なす距離
DISTINCT_DEG = 10.0
N_REFINE = 12             # 細探索に進める候補の数
N_MULTI = 40              # 間引いた点で速く寄せる候補の数（多点からの山登り）
ACCEPT_GRAD = 0.27        # 合格：模型の線の上の勾配の平均（本部棟の登録済み 4 枚の真値 0.31〜0.41、誤った視点 0.15〜0.23）
MARGIN = 0.15             # 合格：2 位（別の場所）との差（割合）
MIN_INSIDE = 0.25         # 写真の枠に入る点の割合がこれ未満なら罰（寄りの写真は 3 割しか入らない）
MIN_SPREAD = 0.08         # 枠に入った点の広がり（標準偏差÷枠。縦横の小さい方）がこれ未満なら罰（1 本の縁に乗るだけの視点）
MIN_FRAC_ALL = 0.05       # 枠に入った点が模型の全点のこれ未満なら罰（遠くから 1 面の端だけを写す視点）
MIN_CAM_HEIGHT, MAX_CAM_HEIGHT = 0.5, 12.0
CELL = 40                 # 写真→模型の照合の升（px、長辺 1600 で）
CELL_COARSE = 80          # 同、粗探索（点を間引くので升を大きく）
GRAD_SIGMA_COARSE = 6.0   # 勾配の採点のぼかし（粗探索）
GRAD_SIGMA_MID = 3.0      # 同（中間）
GRAD_SIGMA_FINE = 1.5     # 同（細探索）
COVERAGE_W = 0.6          # 模型の点が落ちた升にある写真の長い線の割合（coverage）の不足に掛ける重み


# --- 回転と投影 ------------------------------------------------------------------------------

def rotation_cv(yaw_deg: float, pitch_deg: float, roll_deg: float = 0.0) -> np.ndarray:
    """方位（真北から時計回り）・仰角（上が正）・傾き（時計回りが正）→ OpenCV のカメラ回転 R（世界→カメラ。X_cam = R (P − C)）。
    世界は x=東・y=北・z=上。カメラは x=右・y=下・z=前。"""
    th, ph, ro = map(math.radians, (yaw_deg, pitch_deg, roll_deg))
    f = np.array([math.sin(th) * math.cos(ph), math.cos(th) * math.cos(ph), math.sin(ph)])
    r0 = np.array([math.cos(th), -math.sin(th), 0.0])
    d0 = np.cross(f, r0)
    d0 /= np.linalg.norm(d0)
    r = math.cos(ro) * r0 + math.sin(ro) * d0
    d = -math.sin(ro) * r0 + math.cos(ro) * d0
    return np.stack([r, d, f])


def angles_of(R: np.ndarray) -> tuple[float, float, float]:
    """R_cv → (方位, 仰角, 傾き) 度。rotation_cv の逆。"""
    R = np.asarray(R, float)
    f = R[2]
    yaw = math.degrees(math.atan2(f[0], f[1])) % 360
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, f[2]))))
    r0 = np.array([math.cos(math.radians(yaw)), -math.sin(math.radians(yaw)), 0.0])
    d0 = np.cross(f, r0)
    d0 /= np.linalg.norm(d0)
    roll = math.degrees(math.atan2(float(R[0] @ d0), float(R[0] @ r0)))
    return yaw, pitch, roll


def focal_px_from_exif(rec: dict) -> float:
    """35mm 換算の焦点距離 → 長辺の px。無ければ 26 mm 相当。"""
    f35 = rec.get("focal_35mm") or 26.0
    w, h = int(rec.get("width") or 4000), int(rec.get("height") or 3000)
    return float(f35) / 36.0 * max(w, h)


# --- 模型の縁の点 ------------------------------------------------------------------------------

def model_points(b: dict) -> dict:
    """面の線（recheck.face_check_lines）を世界座標の点にする。
    返り値 {"P": (N,3), "face": (N,) 面の番号, "p0": (F,2), "n": (F,2), "ids": [面 id]}。"""
    from .recheck import face_check_lines

    faces = [f for f in b.get("faces", []) if f.get("from") and f.get("to")]
    P, fi, vt, dirs, p0s, ns, ids = [], [], [], [], [], [], []
    for k, f in enumerate(faces):
        fr = face_rect_frame(b, f)
        p0, uvec, nv, _ = face_frame(fr)
        p0s.append(p0)
        ns.append(nv)
        ids.append(f["id"])
        try:
            lines = face_check_lines(b, f["id"])
        except Exception:
            lines = []
        for ln in lines:
            if not ln.get("samples"):
                continue
            s = np.array([(a, c, zz) for a, c, zz in ln["samples"]], float)   # (s, n, z)
            W = plane_to_world(fr, s[:, 0], s[:, 2], s[:, 1])
            P.append(W)
            fi.append(np.full(len(W), k))
            is_v = ln.get("dir") == "s"
            vt.append(np.full(len(W), is_v))       # dir "s"＝鉛直の線（写真でもほぼ縦）、"z"＝水平の線
            u3 = np.array([0.0, 0.0, 1.0]) if is_v else np.array([uvec[0], uvec[1], 0.0])
            dirs.append(np.tile(u3, (len(W), 1)))
    if not P:
        return {"P": np.zeros((0, 3)), "D": np.zeros((0, 3)), "face": np.zeros(0, int), "vert": np.zeros(0, bool),
                "p0": np.zeros((0, 2)), "n": np.zeros((0, 2)), "ids": ids}
    return {"P": np.concatenate(P), "D": np.concatenate(dirs), "face": np.concatenate(fi), "vert": np.concatenate(vt),
            "p0": np.array(p0s), "n": np.array(ns), "ids": ids}


def thin_points(mp: dict, per_face: int, seed: int = 0) -> dict:
    """面ごとに per_face 点まで間引く（粗探索用）。"""
    rng = np.random.default_rng(seed)
    keep = []
    for k in range(len(mp["ids"])):
        idx = np.nonzero(mp["face"] == k)[0]
        if len(idx) > per_face:
            idx = rng.choice(idx, per_face, replace=False)
        keep.append(idx)
    keep = np.sort(np.concatenate(keep)) if keep else np.zeros(0, int)
    return {**mp, "P": mp["P"][keep], "D": mp["D"][keep], "face": mp["face"][keep], "vert": mp["vert"][keep]}


# --- 写真の縁 ------------------------------------------------------------------------------

class EdgeDT:
    """写真の縁（Canny）までの距離（px）。探索用に長辺を long_side に縮めて持つ。"""

    def __init__(self, img: np.ndarray, long_side: int = LONG_SIDE):
        import cv2

        h, w = img.shape[:2]
        self.scale = long_side / max(w, h)
        self.w, self.h = int(round(w * self.scale)), int(round(h * self.scale))
        small = cv2.resize(img, (self.w, self.h), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small
        gray = cv2.GaussianBlur(gray, (0, 0), 1.2)
        med = float(np.median(gray))
        canny = cv2.Canny(gray, max(20, 0.5 * med), max(60, 1.2 * med))
        # タイルの目地や葉で Canny の縁は画面の 5〜10 % を埋め、どの視点でも点が縁の近くに落ちてしまう。
        # 模型の線（面の端・柱割り・窓の上下端・帯）は長い線なので、写真側も長い線分（Hough）だけにして粒度を揃える。
        # (1) Canny を縁の向きで縦・横に分け、短い走り（目地・葉）をモルフォロジーの開きで落とす（縦は 15 px 以上の縦の走り、横も同様。
        #     3 px 太らせてから開くので 15° までの傾きは残る）。(2) 斜めの長い線（床の線の遠近）は Hough で拾って足す。
        gx = cv2.Sobel(gray.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray.astype(np.float32), cv2.CV_32F, 0, 1, ksize=3)
        vert_c = ((canny > 0) & (np.abs(gx) > np.abs(gy))).astype(np.uint8) * 255
        horz_c = ((canny > 0) & ~(np.abs(gx) > np.abs(gy))).astype(np.uint8) * 255
        run = max(12, int(0.01 * max(self.w, self.h)))
        vert = cv2.morphologyEx(cv2.dilate(vert_c, np.ones((1, 3), np.uint8)), cv2.MORPH_OPEN, np.ones((run, 1), np.uint8))
        horz = cv2.morphologyEx(cv2.dilate(horz_c, np.ones((3, 1), np.uint8)), cv2.MORPH_OPEN, np.ones((1, run), np.uint8))
        min_len = max(24, int(0.02 * max(self.w, self.h)))
        segs = cv2.HoughLinesP(canny, 1, np.pi / 180, threshold=30, minLineLength=min_len, maxLineGap=6)
        self.n_segments = 0
        self.segments = np.zeros((0, 4), float)
        if segs is not None:
            self.segments = np.asarray(segs).reshape(-1, 4).astype(float)
            for x1, y1, x2, y2 in self.segments:
                tgt = vert if abs(x2 - x1) < abs(y2 - y1) else horz        # 線分の向き：縦の線分／横（斜め含む）の線分
                cv2.line(tgt, (int(x1), int(y1)), (int(x2), int(y2)), 255, 1)
                self.n_segments += 1
        self.edges = cv2.bitwise_or(vert, horz)
        self.dt_v = cv2.distanceTransform(255 - vert, cv2.DIST_L2, 3).astype(np.float32)
        self.dt_h = cv2.distanceTransform(255 - horz, cv2.DIST_L2, 3).astype(np.float32)
        self.dt = np.minimum(self.dt_v, self.dt_h)
        self.small = small
        # 写真→模型の向きの照合（遠くから小さく写す視点が模型→写真の距離だけで勝つのを防ぐ）：
        # 写真を CELL px の升に切り、升ごとの長い線の量を持つ。視点ごとに模型の点が落ちた升の線の量の割合（coverage）を見る
        self.cells: dict[int, tuple[int, int, np.ndarray]] = {}
        e = (self.edges > 0).astype(np.float32)
        for cell in (CELL, CELL_COARSE):
            ncx, ncy = -(-self.w // cell), -(-self.h // cell)
            mass = np.zeros((ncy, ncx), np.float32)
            for j in range(ncy):
                for i in range(ncx):
                    mass[j, i] = e[j * cell:(j + 1) * cell, i * cell:(i + 1) * cell].sum()
            self.cells[cell] = (ncx, ncy, (mass / max(1.0, mass.sum())).ravel())
        # 勾配（線の向きに直角な成分で採点する用）。粗い探索は大きくぼかした物、細かい探索は小さくぼかした物
        g32 = gray.astype(np.float32)
        self.grad: dict[float, tuple[np.ndarray, np.ndarray]] = {}
        for sigma in (GRAD_SIGMA_COARSE, GRAD_SIGMA_MID, GRAD_SIGMA_FINE):
            gb = cv2.GaussianBlur(g32, (0, 0), sigma)
            gx_ = cv2.Sobel(gb, cv2.CV_32F, 1, 0, ksize=3)
            gy_ = cv2.Sobel(gb, cv2.CV_32F, 0, 1, ksize=3)
            mag = np.hypot(gx_, gy_)
            p95 = float(np.percentile(mag[::4, ::4], 95)) or 1.0
            self.grad[sigma] = (np.clip(gx_ / p95, -1, 1), np.clip(gy_ / p95, -1, 1))

    def grad_at(self, u: np.ndarray, v: np.ndarray, nx: np.ndarray, ny: np.ndarray, sigma: float) -> np.ndarray:
        """(u, v) は縮めた写真の px、(nx, ny) は線の直角方向（単位）。直角方向の勾配の大きさ（0〜1 強）。枠の外は 0。"""
        gx_, gy_ = self.grad[sigma]
        ui, vi = np.rint(u).astype(int), np.rint(v).astype(int)
        inside = (ui >= 0) & (ui < self.w) & (vi >= 0) & (vi < self.h)
        out = np.zeros(u.shape, np.float32)
        out[inside] = np.abs(gx_[vi[inside], ui[inside]] * nx[inside] + gy_[vi[inside], ui[inside]] * ny[inside])
        return out, inside

    def estimate_tilt(self, focal_px: float, max_from_vertical_deg: float = 25.0, min_len: float = 40.0) -> dict | None:
        """縦に近い長い線分の消失点から仰角と傾きを出す（建物の鉛直線は写真で 1 点に集まる）。
        focal_px は元の写真の px。返り値 {"pitch", "roll", "n_lines", "resid_px"}。線分が 8 本未満なら None。
        式：世界の上向き (0,0,1) のカメラ座標 d=(dx,dy,dz)。仰角 = asin(dz)、傾き = −asin(dx / cos(仰角))（rotation_cv の約束）。"""
        if len(self.segments) == 0:
            return None
        s = self.segments
        dx, dy = s[:, 2] - s[:, 0], s[:, 3] - s[:, 1]
        length = np.hypot(dx, dy)
        ang = np.degrees(np.arctan2(np.abs(dx), np.abs(dy)))          # 0 = 鉛直
        sel = (ang <= max_from_vertical_deg) & (length >= min_len)
        if sel.sum() < 8:
            return None
        # 元の写真の px に戻し、中心を原点にした同次座標の直線 l = p1 × p2
        f = focal_px
        cx, cy = self.w / self.scale / 2.0, self.h / self.scale / 2.0
        p1 = np.c_[s[sel, 0] / self.scale - cx, s[sel, 1] / self.scale - cy, np.ones(sel.sum())]
        p2 = np.c_[s[sel, 2] / self.scale - cx, s[sel, 3] / self.scale - cy, np.ones(sel.sum())]
        L = np.cross(p1, p2)
        L /= np.linalg.norm(L[:, :2], axis=1, keepdims=True)         # 距離が px になる正規化
        wgt = np.sqrt(length[sel] / self.scale)
        # 2 回の最小二乗（1 回目の残差の大きい線分を外す）
        keep = np.ones(len(L), bool)
        for _ in range(2):
            A = L[keep] * wgt[keep, None]
            _, _, Vt = np.linalg.svd(A, full_matrices=False)
            vp = Vt[-1]
            if abs(vp[2]) < 1e-9:
                return None
            vp = vp / vp[2]
            resid = np.abs(L @ vp)
            keep = resid < max(6.0, 3.0 * np.median(resid[keep]))
            if keep.sum() < 6:
                return None
        d = np.array([vp[0] / f, vp[1] / f, 1.0])
        d /= np.linalg.norm(d)
        if d[1] > 0:                 # 消失点が中心より下＝上向きはカメラの後ろ側（見下ろし）
            d = -d
        pitch = math.degrees(math.asin(max(-1.0, min(1.0, d[2]))))
        cp = max(1e-6, math.cos(math.radians(pitch)))
        roll = -math.degrees(math.asin(max(-1.0, min(1.0, d[0] / cp))))
        return {"pitch": round(pitch, 2), "roll": round(roll, 2), "n_lines": int(keep.sum()),
                "resid_px": round(float(np.median(resid[keep])), 2)}

    def coverage(self, u: np.ndarray, v: np.ndarray, use: np.ndarray, cell: int = CELL) -> np.ndarray:
        """(B,N) の点が落ちた升にある長い線の量の割合（0〜1）。use は枠の中で有効な点。cell は升の大きさ（粗探索は大きい升）。"""
        ncx, ncy, mass = self.cells[cell]
        ci = np.clip(np.floor(u / cell), 0, ncx - 1).astype(int)
        cj = np.clip(np.floor(v / cell), 0, ncy - 1).astype(int)
        idx = cj * ncx + ci
        B = u.shape[0]
        occ = np.zeros((B, ncx * ncy), bool)
        rows = np.broadcast_to(np.arange(B)[:, None], u.shape)
        occ[rows[use], idx[use]] = True
        return occ @ mass

    def sample(self, u: np.ndarray, v: np.ndarray, trunc: float, vert=None) -> np.ndarray:
        """(u, v) は縮めた写真の px。枠の外は trunc。vert（同じ形の bool）があれば縦の線は縦の縁、横の線は横の縁で測る。"""
        ui, vi = np.rint(u).astype(int), np.rint(v).astype(int)
        inside = (ui >= 0) & (ui < self.w) & (vi >= 0) & (vi < self.h)
        out = np.full(u.shape, trunc, np.float32)
        if vert is None:
            out[inside] = np.minimum(self.dt[vi[inside], ui[inside]], trunc)
        else:
            vv = np.broadcast_to(vert, u.shape)
            d = np.where(vv[inside], self.dt_v[vi[inside], ui[inside]], self.dt_h[vi[inside], ui[inside]])
            out[inside] = np.minimum(d, trunc)
        return out, inside


# --- 採点 ------------------------------------------------------------------------------

def score_poses(C: np.ndarray, R: np.ndarray, focal: np.ndarray, mp: dict, dt: EdgeDT, trunc: float) -> np.ndarray:
    """まとめて採点。C (B,3)、R (B,3,3)、focal (B,) は元の写真の px。返り値 (B,) 小さいほど合う（0〜1 強）。"""
    P, fidx = mp["P"], mp["face"]
    B = len(C)
    # 面が見えるか：カメラが面の外側（法線の側）に 0.3 m 以上
    facing = (np.einsum("bfk,fk->bf", C[:, None, :2] - mp["p0"][None], mp["n"]) > 0.3)       # (B,F)
    valid = facing[:, fidx]                                                                     # (B,N)
    X = np.einsum("bij,bnj->bni", R, P[None] - C[:, None, :])                                  # (B,N,3)
    z = X[..., 2]
    front = z > 0.5
    s = dt.scale
    with np.errstate(divide="ignore", invalid="ignore"):
        u = (focal[:, None] * X[..., 0] / z + dt.w / s / 2.0) * s
        v = (focal[:, None] * X[..., 1] / z + dt.h / s / 2.0) * s
    u = np.where(front, u, -1e6)
    v = np.where(front, v, -1e6)
    d, inside = dt.sample(u, v, trunc, mp["vert"][None, :])
    d = np.where(valid, d, 0.0)
    nvalid = valid.sum(1)
    use = inside & valid
    nuse = use.sum(1)
    with np.errstate(divide="ignore", invalid="ignore"):
        mean = d.sum(1) / np.maximum(nvalid, 1) / trunc
        frac_in = nuse / np.maximum(nvalid, 1)
        # 写真の中の点の広がり（標準偏差／枠）。1 本の縁に点の列が乗るだけの視点を落とす
        uu = np.where(use, u, 0.0)
        vv = np.where(use, v, 0.0)
        mu_u, mu_v = uu.sum(1) / np.maximum(nuse, 1), vv.sum(1) / np.maximum(nuse, 1)
        sd_u = np.sqrt(np.maximum((np.where(use, (u - mu_u[:, None]) ** 2, 0.0)).sum(1) / np.maximum(nuse, 1), 0.0)) / dt.w
        sd_v = np.sqrt(np.maximum((np.where(use, (v - mu_v[:, None]) ** 2, 0.0)).sum(1) / np.maximum(nuse, 1), 0.0)) / dt.h
        frac_all = nuse / max(1, P.shape[0])
    pen = np.where(frac_in < MIN_INSIDE, 1.0 + (MIN_INSIDE - frac_in) * 2.0, 1.0)
    pen = pen + np.maximum(0.0, MIN_SPREAD - np.minimum(sd_u, sd_v)) * 10.0
    pen = pen + np.maximum(0.0, MIN_FRAC_ALL - frac_all) * 4.0
    cov = dt.coverage(u, v, use)
    mean = np.where(nvalid < 30, 1.5, mean * pen + COVERAGE_W * (1.0 - cov))
    return mean


def pose_stats(C, R, focal, mp: dict, dt: EdgeDT) -> dict:
    """1 つの視点の詳しい値：平均距離（px）・縁に乗った割合・枠に入る割合・使った点の数。"""
    P, fidx = mp["P"], mp["face"]
    facing = np.einsum("fk,fk->f", C[None, :2] - mp["p0"], mp["n"]) > 0.3
    valid = facing[fidx]
    X = (P - C) @ R.T
    z = X[:, 2]
    front = z > 0.5
    s = dt.scale
    with np.errstate(divide="ignore", invalid="ignore"):
        u = (focal * X[:, 0] / z + dt.w / s / 2.0) * s
        v = (focal * X[:, 1] / z + dt.h / s / 2.0) * s
    u = np.where(front, u, -1e6)
    v = np.where(front, v, -1e6)
    d, inside = dt.sample(u, v, TRUNC_FINE, mp["vert"])
    use = valid & inside
    n = int(use.sum())
    if n == 0:
        return {"grad": 0.0, "coverage": 0.0, "mean_px": None, "inlier": 0.0, "inside": 0.0, "n_points": 0, "spread": 0.0, "frac_all": 0.0}
    gr = grad_scores(C[None], R[None], np.array([focal]), mp, dt, GRAD_SIGMA_FINE)
    return {"grad": round(float(gr["grad"][0]), 3), "coverage": round(float(gr["coverage"][0]), 3),
            "mean_px": round(float(d[use].mean()), 2), "inlier": round(float((d[use] < INLIER_PX).mean()), 3),
            "inside": round(float(use.sum() / max(1, valid.sum())), 3), "n_points": n,
            "spread": round(float(min(u[use].std() / dt.w, v[use].std() / dt.h)), 3),
            "frac_all": round(float(n / max(1, len(P))), 3)}


# --- 探索 ------------------------------------------------------------------------------

def ground_fn(b: dict, dem: dict | None):
    """(x, y) → 地面の z（棟の z=0 基準）。5m 標高が無ければ 0。"""
    from .sfm import ground_at

    def g(x: float, y: float) -> float:
        return ground_at(float(x), float(y), b, dem)[0]
    return g


def _grid_search(centers: list[tuple[float, float, float, float, float]], dxy: np.ndarray, dyaw: np.ndarray,
                 dpitch: np.ndarray, droll: np.ndarray, focal: float, mp: dict, dt: EdgeDT, ground, sigma: float,
                 cov_w: float, top_k: int, batch: int = 4096) -> list[dict]:
    """centers（x, y, yaw, pitch, roll）の周りに dxy × dxy × dyaw × dpitch × droll の格子を張って採点。上位 top_k（別の場所どうし）。"""
    rows = []
    for (cx, cy, cyaw, cpitch, croll) in centers:
        for dx in dxy:
            for dy in dxy:
                for yw in dyaw:
                    for pt in dpitch:
                        for ro in droll:
                            rows.append((cx + dx, cy + dy, (cyaw + yw) % 360.0, cpitch + pt, croll + ro))
    grid = np.array(rows, float)
    gz_cache: dict[tuple[float, float], float] = {}
    gz = np.empty(len(grid))
    for i, (x, y) in enumerate(grid[:, :2]):
        key = (round(float(x), 2), round(float(y), 2))
        if key not in gz_cache:
            gz_cache[key] = ground(x, y)
        gz[i] = gz_cache[key]
    C_all = np.c_[grid[:, 0], grid[:, 1], gz + CAM_HEIGHT]
    Rs = np.array([rotation_cv(yw, pt, ro) for yw, pt, ro in grid[:, 2:5]])
    scores = np.empty(len(grid))
    for i in range(0, len(grid), batch):
        sl = slice(i, i + batch)
        scores[sl] = grad_cost(C_all[sl], Rs[sl], np.full(len(C_all[sl]), focal), mp, dt, sigma, cov_w, CELL_COARSE)
    order = np.argsort(scores)
    out: list[dict] = []
    for j in order:
        c = {"x": float(grid[j, 0]), "y": float(grid[j, 1]), "z": float(C_all[j, 2]), "yaw": float(grid[j, 2]),
             "pitch": float(grid[j, 3]), "roll": float(grid[j, 4]), "focal": float(focal), "score": float(scores[j])}
        if any(math.hypot(c["x"] - o["x"], c["y"] - o["y"]) < DISTINCT_M and abs((c["yaw"] - o["yaw"] + 180) % 360 - 180) < DISTINCT_DEG
               for o in out):
            continue
        out.append(c)
        if len(out) >= top_k:
            break
    return out


def face_prior(b: dict, face_id: str | None, max_dist: float = 50.0, step: float = 2.0, yaw_half: float = 50.0,
               yaw_step: float = 3.0) -> tuple[list[tuple[float, float]], np.ndarray] | None:
    """仕分け係が付けた面 id から、立てる場所と向きを絞る：面の前（法線の側）3〜max_dist m、面に沿って両端 ±10 m、
    向きは面を見る方向（−法線）±yaw_half°。面 id が棟の面に無ければ None（全周の探索に戻る）。"""
    f = next((f for f in b.get("faces", []) if f["id"] == face_id and f.get("from") and f.get("to")), None)
    if f is None:
        return None
    p0, u, nv, L = face_frame(face_rect_frame(b, f))
    look = math.degrees(math.atan2(-nv[0], -nv[1])) % 360.0           # −法線の方位（x=東, y=北 → 北から時計回り）
    pos = [(float(p0[0] + u[0] * sv + nv[0] * d), float(p0[1] + u[1] * sv + nv[1] * d))
           for sv in np.arange(-10.0, L + 10.0 + 1e-6, step) for d in np.arange(3.0, max_dist + 1e-6, step)]
    yaws = (np.arange(-yaw_half, yaw_half + 1e-6, yaw_step) + look) % 360.0
    return pos, yaws


def coarse_search(x0: float, y0: float, heading: float | None, focal: float, mp: dict, dt: EdgeDT, ground,
                  radius: float = RADIUS_M, step: float = STEP_M, tilt: dict | None = None,
                  prior: tuple[list[tuple[float, float]], np.ndarray] | None = None) -> list[dict]:
    """格子探索。返り値は上位 TOP_K（別の場所どうし）。
    仰角・傾きは写真の鉛直線の消失点（EdgeDT.estimate_tilt）で先に決め、探索は x・y・方位の 3 つ（無ければ仰角 4 段・傾き 0）。
    prior（面の前の場所と向き。face_prior）があればその中だけ 2 m・3° で。無ければ GPS の周り ±radius を 2·step・6°・全方位
    （EXIF の方位は横持ちで約 85° ずれていた例があるので初期値にしない。本部棟の試しでは全周の探索は真値が 200〜1300 位に埋もれた）。
    2 段目：1 段目の上位の周りを xy ±step（step/2 刻み）・方位 ±5°（1.5° 刻み）・仰角 ±3°。"""
    if tilt:
        pitch0, roll0 = tilt["pitch"], tilt["roll"]
        dpitch1 = np.array([0.0])
    else:
        pitch0, roll0 = 0.0, 0.0
        dpitch1 = np.array([-4.0, 6.0, 16.0, 26.0])
    if prior is not None:
        pos, yaws = prior
        centers1 = [(x, y, 0.0, pitch0, roll0) for x, y in pos]
        c1 = _grid_search(centers1, np.array([0.0]), yaws, dpitch1, np.array([0.0]),
                          focal, mp, dt, ground, sigma=GRAD_SIGMA_COARSE, cov_w=COVERAGE_W, top_k=TOP_K)
    else:
        xs1 = np.arange(-radius, radius + 1e-6, 2 * step)
        c1 = _grid_search([(x0, y0, 0.0, pitch0, roll0)], xs1, np.arange(0.0, 360.0, 6.0), dpitch1, np.array([0.0]),
                          focal, mp, dt, ground, sigma=GRAD_SIGMA_COARSE, cov_w=COVERAGE_W, top_k=TOP_K)
    centers = [(c["x"], c["y"], c["yaw"], c["pitch"], c["roll"]) for c in c1]
    xs2 = np.arange(-step, step + 1e-6, step / 2)
    return _grid_search(centers, xs2, np.arange(-5.0, 5.0 + 1e-6, 1.5), np.array([-3.0, 0.0, 3.0]), np.array([0.0]),
                        focal, mp, dt, ground, sigma=GRAD_SIGMA_COARSE, cov_w=COVERAGE_W, top_k=TOP_K)


def refine(c: dict, mp: dict, dt: EdgeDT, ground, rounds: int = 4, sigma: float = GRAD_SIGMA_FINE) -> dict:
    """座標降下：x y z・向き・仰角・傾き・焦点距離を順に動かし、縮めていく。z は地面＋高さで動かす。"""
    keys = ["x", "y", "h", "yaw", "pitch", "roll", "focal"]
    cur = dict(c)
    cur["h"] = cur["z"] - ground(cur["x"], cur["y"])
    steps = {"x": 2.0, "y": 2.0, "h": 0.4, "yaw": 3.0, "pitch": 3.0, "roll": 2.0, "focal": cur["focal"] * 0.06}
    limits = {"h": (MIN_CAM_HEIGHT, MAX_CAM_HEIGHT), "roll": (-15.0, 15.0), "pitch": (-30.0, 60.0),
              "focal": (c["focal"] * 0.75, c["focal"] * 1.3)}

    def ev(p: dict) -> float:
        C = np.array([[p["x"], p["y"], ground(p["x"], p["y"]) + p["h"]]])
        R = rotation_cv(p["yaw"], p["pitch"], p["roll"])[None]
        return float(grad_cost(C, R, np.array([p["focal"]]), mp, dt, sigma)[0])

    best = ev(cur)
    for _ in range(rounds):
        improved = True
        while improved:
            improved = False
            for k in keys:
                for sgn in (1, -1):
                    trial = dict(cur)
                    trial[k] = cur[k] + sgn * steps[k]
                    lo, hi = limits.get(k, (-1e9, 1e9))
                    if not (lo <= trial[k] <= hi):
                        continue
                    sc = ev(trial)
                    if sc < best - 1e-6:
                        cur, best, improved = trial, sc, True
                        break
        for k in steps:
            steps[k] *= 0.5
    cur["z"] = ground(cur["x"], cur["y"]) + cur["h"]
    cur["score"] = best
    return cur


def pose_camera(c: dict, w: int, h: int, ground) -> dict:
    R = rotation_cv(c["yaw"], c["pitch"], c["roll"])
    C = np.array([c["x"], c["y"], c["z"]])
    t = -R @ C
    return R_.camera_dict(R, t, c["focal"], w, h, ground(c["x"], c["y"]))


def locate_photo(rec: dict, b: dict, mp: dict, dem: dict | None = None, img: np.ndarray | None = None,
                 radius: float = RADIUS_M, seed_xy: tuple[float, float] | None = None, seed_heading: float | None = None,
                 log=print) -> dict:
    """写真 1 枚の模型合わせ。rec は索引の 1 件（gps・heading_deg・focal_35mm・width・height・file）。
    seed_xy / seed_heading は GPS の代わりの種（係が「どの面をどこから」と言った時）。"""
    from .rectify import read_image

    t0 = time.time()
    ground = ground_fn(b, dem)
    if img is None:
        img = read_image(R_.photo_path(rec))
    h, w = img.shape[:2]
    dt = EdgeDT(img)
    gps = rec.get("gps") or {}
    if seed_xy is not None:
        x0, y0 = seed_xy
    elif gps.get("x") is not None:
        x0, y0 = float(gps["x"]), float(gps["y"])
    else:
        return {"photo": rec["id"], "status": "fail", "code": "no_seed", "reason": "GPS も種（seed）も無い"}
    heading = seed_heading if seed_heading is not None else true_heading(rec)
    focal = focal_px_from_exif(rec)
    tilt = dt.estimate_tilt(focal)
    mp_c = thin_points(mp, N_COARSE_PTS)
    prior = face_prior(b, rec.get("face"))
    cands = coarse_search(x0, y0, heading, focal, mp_c, dt, ground, radius=radius, tilt=tilt, prior=prior)
    t1 = time.time()
    # 多点からの山登り：間引いた点・粗いぼかしで上位 N_MULTI を速く寄せ、良い順に N_REFINE 本だけ本番の細探索
    quick = [refine(c, mp_c, dt, ground, rounds=2, sigma=GRAD_SIGMA_COARSE) for c in cands[:N_MULTI]]
    quick.sort(key=lambda c: c["score"])
    refined = [refine(c, mp, dt, ground) for c in quick[:N_REFINE]]
    refined.sort(key=lambda c: c["score"])
    # 別の場所の 2 位
    best = refined[0]
    second = next((c for c in refined[1:] if math.hypot(c["x"] - best["x"], c["y"] - best["y"]) >= DISTINCT_M
                   or abs((c["yaw"] - best["yaw"] + 180) % 360 - 180) >= DISTINCT_DEG), None)
    C = np.array([best["x"], best["y"], best["z"]])
    R = rotation_cv(best["yaw"], best["pitch"], best["roll"])
    st = pose_stats(C, R, best["focal"], mp, dt)
    margin = (second["score"] - best["score"]) / max(best["score"], 1e-6) if second else None
    ok = st["grad"] >= ACCEPT_GRAD
    status, code, why = "ok", None, None
    if not ok:
        status, code = "fail", "poor_fit"
        why = f"模型の線の上の勾配 {st['grad']:.2f}（{ACCEPT_GRAD} 要る。登録済みの真値は 0.31〜0.41、誤った視点は 0.15〜0.23）"
    elif margin is not None and margin < MARGIN:
        status, code = "ambiguous", "second_close"
        why = f"別の場所の候補（{second['x']:.1f}, {second['y']:.1f}, 向き {second['yaw']:.0f}°）との差が {margin:.0%}（{MARGIN:.0%} 要る）"
    out = {"photo": rec["id"], "status": status, "code": code, "reason": why,
           "seed": {"x": x0, "y": y0, "heading": heading, "focal_px": focal, "from": "seed" if seed_xy else "gps", "tilt": tilt,
                    "face_prior": rec.get("face") if prior is not None else None},
           "best": {**{k: round(v, 3) for k, v in best.items() if isinstance(v, float)}, **st, "margin": None if margin is None else round(margin, 3),
                    "moved_m": round(math.hypot(best["x"] - x0, best["y"] - y0), 1),
                    "turned_deg": None if heading is None else round((best["yaw"] - heading + 180) % 360 - 180, 1)},
           "candidates": [{k: round(v, 3) for k, v in c.items() if isinstance(v, float)} for c in refined],
           "image_size": [w, h], "seconds": {"coarse": round(t1 - t0, 1), "refine": round(time.time() - t1, 1)}}
    out["camera"] = pose_camera(best, w, h, ground)
    log(f"  {rec['id']}: {status} 勾配 {st['grad']:.2f}・覆い {st['coverage']:.2f}・平均 {st['mean_px']} px・乗った {st['inlier']:.0%}・移動 {out['best']['moved_m']} m・"
        f"回転 {out['best']['turned_deg']}°・{out['seconds']['coarse'] + out['seconds']['refine']:.0f} 秒")
    return out


# --- 重ね画像 ------------------------------------------------------------------------------

def draw_overlay(img: np.ndarray, cands: list[dict], mp: dict, out_png: Path, long_side: int = 1600) -> Path:
    """写真に模型の点を重ねる（1 位は緑、2 位は橙、3 位は水色）。"""
    import cv2

    h, w = img.shape[:2]
    s = long_side / max(w, h)
    small = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    colors = [(60, 220, 60), (0, 160, 255), (255, 200, 60)]
    for k, c in enumerate(cands[:3]):
        C = np.array([c["x"], c["y"], c["z"]])
        R = rotation_cv(c["yaw"], c["pitch"], c.get("roll", 0.0))
        facing = np.einsum("fk,fk->f", C[None, :2] - mp["p0"], mp["n"]) > 0.3
        X = (mp["P"] - C) @ R.T
        use = facing[mp["face"]] & (X[:, 2] > 0.5)
        u = (c["focal"] * X[use, 0] / X[use, 2] + w / 2.0) * s
        v = (c["focal"] * X[use, 1] / X[use, 2] + h / 2.0) * s
        for uu, vv in zip(u, v):
            if 0 <= uu < small.shape[1] and 0 <= vv < small.shape[0]:
                cv2.circle(small, (int(uu), int(vv)), 2 if k == 0 else 1, colors[k], -1)
        label = f"{k + 1}: ({c['x']:.1f}, {c['y']:.1f}) yaw {c['yaw']:.0f} score {c['score']:.3f}"
        cv2.putText(small, label, (10, 30 + 28 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.8, colors[k], 2, cv2.LINE_AA)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tofile(str(out_png))
    return out_png


# --- まとめて実行・登録 ------------------------------------------------------------------------------

def make_entry(rec: dict, b: dict, res: dict, by: str = "locate") -> dict:
    cam = res["camera"]
    vis = R_.visible_faces(b, cam)
    return {"photo": rec["id"], "building": b["id"], "face": rec.get("face") or "(pose)", "kind": "pose",
            "period": rec.get("period"), "file": rec.get("file"), "image_size": res["image_size"],
            "camera": cam, "visible_faces": vis,
            "source": {"method": "locate：GPS と方位を初期値に、模型の縁（面の線）が写真の縁（Canny の距離変換）に合う視点を格子探索→座標降下で解く",
                       "kind": "locate", "seed": res["seed"], "focal_source": "exif_35mm を初期値に探索で直す"},
            "check": {"grad": res["best"]["grad"], "coverage": res["best"]["coverage"],
                      "mean_px": res["best"]["mean_px"], "inlier": res["best"]["inlier"], "inside": res["best"]["inside"],
                      "n_points": res["best"]["n_points"], "margin": res["best"]["margin"],
                      "moved_from_gps_m": res["best"]["moved_m"], "turned_from_heading_deg": res["best"]["turned_deg"]},
            "registered_at": datetime.now().isoformat(timespec="seconds"), "registered_by": by}


def run(building_json: Path, photos: list[str] | None = None, max_n: int | None = None, register: bool = True,
        radius: float = RADIUS_M, out_dir: Path | None = None, registry_path: Path | None = None,
        include_registered: bool = False, log=print) -> dict:
    """棟の写真（索引で building が一致、GPS あり、未登録）を順に模型合わせし、合格を台帳に登録する。
    include_registered=True は登録済みも試して既知の視点との差を出す（精度の確かめ。台帳は書かない）。"""
    from .rectify import read_image

    b = load_building(building_json)
    idx = load_index(INDEX_PATH)
    reg = R_.load_registry(registry_path or R_.REGISTRY_PATH)
    done = {e["photo"]: e for e in R_.entries_for(reg, b["id"])}
    out = out_dir or Path("out") / b["id"] / "locate"
    out.mkdir(parents=True, exist_ok=True)
    dem = None
    try:
        from .site import load_dem
        dem = load_dem()
    except (OSError, FileNotFoundError, KeyError):
        dem = None
    recs = [r for r in idx.get("photos", []) if r.get("building") == b["id"] and not r.get("interior")]
    if photos:
        recs = [r for r in recs if any(p in r["id"] for p in photos)]
    if not include_registered:
        recs = [r for r in recs if r["id"] not in done]
    recs = [r for r in recs if (r.get("gps") or {}).get("x") is not None]
    if max_n:
        recs = recs[:max_n]
    mp = model_points(b)
    log(f"模型の点 {len(mp['P'])}（面 {len(mp['ids'])}）、写真 {len(recs)} 枚、半径 {radius} m")
    summ = {"building": b["id"], "made_at": datetime.now().isoformat(timespec="seconds"), "n_photos": len(recs),
            "ok": [], "ambiguous": [], "fail": [], "truth": [], "results": {}}
    for r in recs:
        try:
            img = read_image(R_.photo_path(r))
        except (OSError, FileNotFoundError) as ex:
            summ["fail"].append(r["id"])
            summ["results"][r["id"]] = {"status": "fail", "code": "no_file", "reason": str(ex)}
            continue
        res = locate_photo(r, b, mp, dem, img=img, radius=radius, log=log)
        if res["status"] != "fail" or res.get("candidates"):
            draw_overlay(img, res.get("candidates", []), mp, out / f"{r['id']}_locate.jpg")
        if r["id"] in done and done[r["id"]].get("camera", {}).get("R_cv"):
            cam = done[r["id"]]["camera"]
            yaw, pitch, roll = angles_of(cam["R_cv"])
            C = np.array(cam["camera_xyz"], float)
            bst = res.get("best") or {}
            err = {"photo": r["id"], "pos_err_m": round(float(math.hypot(bst.get("x", 1e9) - C[0], bst.get("y", 1e9) - C[1])), 2),
                   "z_err_m": round(float(bst.get("z", 1e9) - C[2]), 2),
                   "yaw_err_deg": round(float((bst.get("yaw", 0) - yaw + 180) % 360 - 180), 2),
                   "pitch_err_deg": round(float(bst.get("pitch", 0) - pitch), 2),
                   "focal_err_pct": round(float((bst.get("focal", 0) / cam["focal_px"] - 1) * 100), 1),
                   "gps_err_m": round(float(math.hypot(res["seed"]["x"] - C[0], res["seed"]["y"] - C[1])), 1),
                   "status": res["status"]}
            res["truth"] = err
            summ["truth"].append(err)
            log(f"    既知の視点との差：位置 {err['pos_err_m']} m（GPS は {err['gps_err_m']} m）・向き {err['yaw_err_deg']}°・"
                f"仰角 {err['pitch_err_deg']}°・焦点 {err['focal_err_pct']}%")
        summ["results"][r["id"]] = {k: v for k, v in res.items() if k != "camera"}
        summ[res["status"]].append(r["id"])
        (out / f"{r['id']}_locate.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        if register and res["status"] == "ok" and r["id"] not in done:
            R_.upsert(reg, make_entry(r, b, res))
        elif register and res["status"] != "ok" and r["id"] not in done:
            R_.note_not_registered(reg, b["id"], r["id"], idx, res.get("reason") or "", str(res.get("code")), "locate")
    if register:
        R_.save_registry(reg, registry_path or R_.REGISTRY_PATH)
    (out / "locate.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"合格 {len(summ['ok'])}・保留 {len(summ['ambiguous'])}・不合格 {len(summ['fail'])} → {out}")
    return summ


# --- 勾配による採点（距離変換はタイルの目地で飽和し、誤った視点と真値の差が出なかった）------------------------------

def grad_scores(C: np.ndarray, R: np.ndarray, focal: np.ndarray, mp: dict, dt: EdgeDT, sigma: float, cell: int = CELL) -> dict:
    """まとめて採点：模型の点を投影し、線の向きに直角な勾配の強さの平均（高いほど合う）。
    返り値 {"grad": (B,), "coverage": (B,), "frac_in": (B,), "spread": (B,), "nuse": (B,)}。"""
    P, D, fidx = mp["P"], mp["D"], mp["face"]
    facing = (np.einsum("bfk,fk->bf", C[:, None, :2] - mp["p0"][None], mp["n"]) > 0.3)
    valid = facing[:, fidx]
    X = np.einsum("bij,bnj->bni", R, P[None] - C[:, None, :])
    X2 = np.einsum("bij,bnj->bni", R, (P + 0.3 * D)[None] - C[:, None, :])
    z, z2 = X[..., 2], X2[..., 2]
    front = (z > 0.5) & (z2 > 0.5)
    s = dt.scale
    cx, cy = dt.w / s / 2.0, dt.h / s / 2.0
    with np.errstate(divide="ignore", invalid="ignore"):
        u = (focal[:, None] * X[..., 0] / z + cx) * s
        v = (focal[:, None] * X[..., 1] / z + cy) * s
        u2 = (focal[:, None] * X2[..., 0] / z2 + cx) * s
        v2 = (focal[:, None] * X2[..., 1] / z2 + cy) * s
    du, dv = u2 - u, v2 - v
    ln = np.hypot(du, dv) + 1e-6
    nx, ny = -dv / ln, du / ln                    # 線の直角方向
    u = np.where(front, u, -1e6)
    v = np.where(front, v, -1e6)
    g, inside = dt.grad_at(u, v, nx, ny, sigma)
    use = inside & valid
    nuse = use.sum(1)
    nvalid = valid.sum(1)
    with np.errstate(divide="ignore", invalid="ignore"):
        grad = np.where(use, g, 0.0).sum(1) / np.maximum(nuse, 1)
        frac_in = nuse / np.maximum(nvalid, 1)
        uu = np.where(use, u, 0.0)
        vv = np.where(use, v, 0.0)
        mu_u, mu_v = uu.sum(1) / np.maximum(nuse, 1), vv.sum(1) / np.maximum(nuse, 1)
        sd_u = np.sqrt(np.maximum((np.where(use, (u - mu_u[:, None]) ** 2, 0.0)).sum(1) / np.maximum(nuse, 1), 0.0)) / dt.w
        sd_v = np.sqrt(np.maximum((np.where(use, (v - mu_v[:, None]) ** 2, 0.0)).sum(1) / np.maximum(nuse, 1), 0.0)) / dt.h
    cov = dt.coverage(u, v, use, cell)
    return {"grad": grad, "coverage": cov, "frac_in": frac_in, "spread": np.minimum(sd_u, sd_v), "nuse": nuse,
            "frac_all": nuse / max(1, P.shape[0])}


def grad_cost(C, R, focal, mp, dt, sigma, cov_w: float = COVERAGE_W, cell: int = CELL) -> np.ndarray:
    """小さいほど合う：1 − 勾配の平均 ＋ 不足の罰（coverage・枠の中の割合・広がり・点の数）。
    粗探索は点を間引くので cell を大きく（CELL_COARSE）して升を埋める。"""
    r = grad_scores(C, R, focal, mp, dt, sigma, cell)
    cost = 1.0 - r["grad"]
    cost = cost + cov_w * (1.0 - r["coverage"])
    cost = cost + np.maximum(0.0, MIN_INSIDE - r["frac_in"]) * 2.0
    cost = cost + np.maximum(0.0, MIN_SPREAD - r["spread"]) * 10.0
    cost = cost + np.maximum(0.0, MIN_FRAC_ALL - r["frac_all"]) * 4.0
    return np.where(r["nuse"] < 30, 2.5, cost)


# --- 種（係や人が言った「どの面を・どこから」）からの局所探索 ------------------------------------------------

def search_from_seed(seed_xy: tuple[float, float], seed_yaw: float, focal: float, mp: dict, mp_c: dict, dt: EdgeDT, ground,
                     tilt: dict | None, radius_m: float = 6.0, step_m: float = 1.0, yaw_half: float = 15.0, yaw_step: float = 2.0,
                     n_quick: int = 10, n_full: int = 4) -> list[dict]:
    """種の周り（±radius_m を step_m 刻み、向き ±yaw_half を yaw_step 刻み）の密な格子 → 間引いた点で速く寄せる → 本番の細探索。
    返り値は細探索した候補（良い順）。全周の探索（coarse_search）は真値が埋もれたので、種がある時はこちらを使う。"""
    pitch0, roll0 = (tilt["pitch"], tilt["roll"]) if tilt else (10.0, 0.0)
    dpitch = np.array([0.0]) if tilt else np.array([-6.0, 4.0, 14.0, 24.0])
    xs = np.arange(-radius_m, radius_m + 1e-6, step_m)
    yws = np.arange(-yaw_half, yaw_half + 1e-6, yaw_step)
    c1 = _grid_search([(seed_xy[0], seed_xy[1], seed_yaw, pitch0, roll0)], xs, yws, dpitch, np.array([0.0]),
                      focal, mp_c, dt, ground, sigma=GRAD_SIGMA_COARSE, cov_w=COVERAGE_W, top_k=n_quick)
    quick = [refine(c, mp_c, dt, ground, rounds=2, sigma=GRAD_SIGMA_COARSE) for c in c1]
    quick.sort(key=lambda c: c["score"])
    full = [refine(c, mp, dt, ground) for c in quick[:n_full]]
    full.sort(key=lambda c: c["score"])
    return full
