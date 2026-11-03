"""写真の正面化（壁を正面から見た形に向け直す）。

やること（全部コード。Claude は使わない）：
    1. 画像から線分を検出する（LSD。無ければ Hough）
    2. 縦っぽい線・横っぽい線に分け、それぞれの消失点を RANSAC で求める
    3. 焦点距離（EXIF の 35mm 換算。無ければ2つの消失点から推定）からカメラの向きを求め、
       壁の面を正面に向ける射影変換（ホモグラフィ）を作って画像を変換する
    4. 変換行列・消失点・使った線分・焦点距離の出どころを JSON に残す（根拠）

出てくるのは「形」だけで、縮尺（m/px）はまだ無い。縮尺はメジャーなど既知の長さを当てて別に決める。
縦横の消失点が見つからない時は失敗として返し、決めつけない。
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

DIAG_35MM = math.hypot(36.0, 24.0)


@dataclass
class RectifyResult:
    ok: bool
    reason: str = ""
    width: int = 0
    height: int = 0
    focal_px: float | None = None
    focal_source: str = ""
    vp_h: list[float] | None = None     # 横線の消失点（画素。無限遠なら大きな値）
    vp_v: list[float] | None = None     # 縦線の消失点
    inliers_h: int = 0
    inliers_v: int = 0
    segments: int = 0
    H: list[list[float]] = field(default_factory=list)   # 元画像 → 正面化画像
    out_size: list[int] = field(default_factory=list)
    residual_deg_h: float | None = None   # 正面化後の横線の傾きの中央値（0 に近いほどよい）
    residual_deg_v: float | None = None

    def to_json(self) -> dict:
        return asdict(self)


# --- 線分 ---------------------------------------------------------------------

def detect_segments(gray: np.ndarray, min_len: float = 25.0) -> np.ndarray:
    """線分 (N, 4) = x1,y1,x2,y2。LSD が使えなければ Canny+HoughP。"""
    import cv2

    segs = None
    try:
        lsd = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
        lines, _, _, _ = lsd.detect(gray)
        if lines is not None:
            segs = lines.reshape(-1, 4)
    except (cv2.error, AttributeError):
        segs = None
    if segs is None:
        edges = cv2.Canny(gray, 60, 150)
        lines = cv2.HoughLinesP(edges, 1, np.pi / 360, 60, minLineLength=min_len, maxLineGap=4)
        segs = lines.reshape(-1, 4).astype(np.float64) if lines is not None else np.zeros((0, 4))
    segs = segs.astype(np.float64)
    length = np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1])
    return segs[length >= min_len]


def _angles(segs: np.ndarray) -> np.ndarray:
    """線分の向き（度、0=右向き、90=上向き）。-90..90 に畳む。"""
    a = np.degrees(np.arctan2(-(segs[:, 3] - segs[:, 1]), segs[:, 2] - segs[:, 0]))
    a = (a + 90) % 180 - 90
    return a


def _homog_lines(segs: np.ndarray) -> np.ndarray:
    p1 = np.c_[segs[:, 0], segs[:, 1], np.ones(len(segs))]
    p2 = np.c_[segs[:, 2], segs[:, 3], np.ones(len(segs))]
    l = np.cross(p1, p2)
    n = np.linalg.norm(l[:, :2], axis=1, keepdims=True)
    n[n == 0] = 1
    return l / n


def _vp_score(vp: np.ndarray, segs: np.ndarray, tol_deg: float) -> np.ndarray:
    """各線分が消失点 vp を向いているか（中点から vp への方向と線分の向きの角度差）。"""
    mx = (segs[:, 0] + segs[:, 2]) / 2
    my = (segs[:, 1] + segs[:, 3]) / 2
    dx, dy = segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1]
    if abs(vp[2]) < 1e-9:
        vx, vy = np.full_like(mx, vp[0]), np.full_like(my, vp[1])
    else:
        vx, vy = vp[0] / vp[2] - mx, vp[1] / vp[2] - my
    cos = np.abs(dx * vx + dy * vy) / (np.hypot(dx, dy) * np.hypot(vx, vy) + 1e-9)
    ang = np.degrees(np.arccos(np.clip(cos, -1, 1)))
    return ang < tol_deg


def find_vp(segs: np.ndarray, iters: int = 600, tol_deg: float = 1.5, seed: int = 0) -> tuple[np.ndarray | None, int]:
    """RANSAC で消失点（同次座標）を求める。長い線分ほど選ばれやすくする。"""
    if len(segs) < 4:
        return None, 0
    rng = np.random.default_rng(seed)
    L = _homog_lines(segs)
    length = np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1])
    w = length / length.sum()
    best, best_n = None, 0
    for _ in range(iters):
        i, j = rng.choice(len(segs), 2, replace=False, p=w)
        vp = np.cross(L[i], L[j])
        if np.linalg.norm(vp) < 1e-9:
            continue
        inl = _vp_score(vp, segs, tol_deg)
        n = int((inl * length).sum())
        if n > best_n:
            best, best_n = vp, n
    if best is None:
        return None, 0
    # インライアの線分で最小二乗（l·v = 0 の v）
    inl = _vp_score(best, segs, tol_deg)
    A = L[inl] * length[inl, None]
    if inl.sum() >= 2:
        _, _, vt = np.linalg.svd(A)
        best = vt[-1]
    return best, int(inl.sum())


# --- 正面化 ---------------------------------------------------------------------

def focal_px_from_35mm(focal_35mm: float, width: int, height: int) -> float:
    return focal_35mm / DIAG_35MM * math.hypot(width, height)


def focal_from_vps(vp_h: np.ndarray, vp_v: np.ndarray, cx: float, cy: float) -> float | None:
    """直交する2つの消失点から焦点距離を推定（主点は画像中心と仮定）。"""
    if abs(vp_h[2]) < 1e-9 or abs(vp_v[2]) < 1e-9:
        return None
    a = vp_h[:2] / vp_h[2] - (cx, cy)
    b = vp_v[:2] / vp_v[2] - (cx, cy)
    f2 = -float(np.dot(a, b))
    return math.sqrt(f2) if f2 > 0 else None


def rectify(img: np.ndarray, focal_35mm: float | None = None, max_scale: float = 3.0) -> tuple[np.ndarray | None, RectifyResult]:
    """壁を正面に向け直す。戻り値は (正面化した画像 | None, 結果)。"""
    import cv2

    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    segs = detect_segments(gray, min_len=max(20.0, min(w, h) / 60))
    res = RectifyResult(ok=False, width=w, height=h, segments=int(len(segs)))
    if len(segs) < 10:
        res.reason = "線分が少なすぎます（壁が小さいか、ぶれています）"
        return None, res
    ang = _angles(segs)
    seg_h = segs[np.abs(ang) <= 35]
    seg_v = segs[np.abs(ang) >= 55]
    vp_h, n_h = find_vp(seg_h)
    vp_v, n_v = find_vp(seg_v, seed=1)
    res.inliers_h, res.inliers_v = n_h, n_v
    if vp_h is None or vp_v is None or n_h < 6 or n_v < 6:
        res.reason = f"縦横の線がそろいません（横 {n_h} 本、縦 {n_v} 本）"
        return None, res
    cx, cy = w / 2.0, h / 2.0
    if focal_35mm:
        f = focal_px_from_35mm(focal_35mm, w, h)
        res.focal_source = "exif_35mm"
    else:
        f = focal_from_vps(vp_h, vp_v, cx, cy)
        res.focal_source = "estimated_from_vps"
        if f is None or not (0.3 * w < f < 5 * w):
            res.reason = "焦点距離が分かりません（EXIF が無く、消失点からも推定できません）"
            return None, res
    res.focal_px = round(f, 1)
    K = np.array([[f, 0, cx], [0, f, cy], [0, 0, 1.0]])
    Kinv = np.linalg.inv(K)

    def direction(vp):
        d = Kinv @ vp
        d = d / np.linalg.norm(d)
        return d

    d1 = direction(vp_h)              # 壁の横方向
    d2 = direction(vp_v)              # 壁の縦方向
    if d1[0] < 0:
        d1 = -d1
    if d2[1] > 0:                     # 画像座標は下向きが +y。縦方向は上向き(-y)に
        d2 = -d2
    d2 = d2 - d1 * np.dot(d1, d2)     # 直交化（消失点の誤差を縦方向に押し付ける）
    if np.linalg.norm(d2) < 1e-6:
        res.reason = "縦横の消失点が同じ方向です"
        return None, res
    d2 /= np.linalg.norm(d2)
    x_new, y_new = d1, -d2            # 新しいカメラの x=壁の横（右）, y=下向き（画像の向きに合わせる）
    z_new = np.cross(x_new, y_new)    # 右手系で z=前（壁の法線）
    if z_new[2] < 0:
        res.reason = "カメラの向きが決まりません（消失点の組み合わせが不自然です）"
        return None, res
    R = np.c_[x_new, y_new, z_new]
    Hm = K @ R.T @ Kinv
    # 出力範囲：元画像の四隅を写して囲う（大きくなりすぎる時は縮める）
    corners = np.array([[0, 0, 1], [w, 0, 1], [w, h, 1], [0, h, 1]], dtype=float).T
    pc = Hm @ corners
    if np.any(np.abs(pc[2]) < 1e-6) or np.any(pc[2] * pc[2][0] <= 0):
        res.reason = "正面化すると画像が裏返ります（壁に対して斜めすぎます）"
        return None, res
    pc = pc[:2] / pc[2]
    # 出力は「縦横の線がある範囲」（＝壁）に 10% の余白を足した所まで。元画像の枠より外には出さない
    inl_h = seg_h[_vp_score(vp_h, seg_h, 1.5)]
    inl_v = seg_v[_vp_score(vp_v, seg_v, 1.5)]
    pts = np.vstack([inl_h[:, :2], inl_h[:, 2:], inl_v[:, :2], inl_v[:, 2:]])
    pp = Hm @ np.c_[pts, np.ones(len(pts))].T
    pp = pp[:2] / pp[2]
    mx, my = 0.1 * (pp[0].max() - pp[0].min()), 0.1 * (pp[1].max() - pp[1].min())
    x0, y0 = max(pc[0].min(), pp[0].min() - mx), max(pc[1].min(), pp[1].min() - my)
    x1, y1 = min(pc[0].max(), pp[0].max() + mx), min(pc[1].max(), pp[1].max() + my)
    ow, oh = x1 - x0, y1 - y0
    s = min(1.0, max_scale * w / ow, max_scale * h / oh)
    T = np.array([[s, 0, -x0 * s], [0, s, -y0 * s], [0, 0, 1]])
    Hm = T @ Hm
    out_w, out_h = int(ow * s), int(oh * s)
    if out_w < 16 or out_h < 16:
        res.reason = "出力が小さすぎます"
        return None, res
    out = cv2.warpPerspective(img, Hm, (out_w, out_h), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
    res.H = [[float(v) for v in row] for row in Hm]
    res.out_size = [out_w, out_h]
    res.vp_h = [float(v) for v in (vp_h / (vp_h[2] if abs(vp_h[2]) > 1e-9 else 1))]
    res.vp_v = [float(v) for v in (vp_v / (vp_v[2] if abs(vp_v[2]) > 1e-9 else 1))]

    # 検算：正面化後の線分の傾き
    def residual(seg):
        p1 = Hm @ np.c_[seg[:, 0], seg[:, 1], np.ones(len(seg))].T
        p2 = Hm @ np.c_[seg[:, 2], seg[:, 3], np.ones(len(seg))].T
        p1, p2 = p1[:2] / p1[2], p2[:2] / p2[2]
        a = np.degrees(np.arctan2(p2[1] - p1[1], p2[0] - p1[0]))
        return a

    ah = residual(seg_h[_vp_score(vp_h, seg_h, 1.5)])
    av = residual(seg_v[_vp_score(vp_v, seg_v, 1.5)])
    ah = (ah + 90) % 180 - 90
    av = np.abs((av % 180) - 90)
    res.residual_deg_h = round(float(np.median(np.abs(ah))), 2) if len(ah) else None
    res.residual_deg_v = round(float(np.median(av)), 2) if len(av) else None
    res.ok = True
    return out, res


def rectify_file(src: Path, out_dir: Path, focal_35mm: float | None = None) -> RectifyResult:
    """ファイルを正面化し、画像と根拠 JSON を out_dir に書く。"""
    import cv2

    img = cv2.imread(str(src), cv2.IMREAD_COLOR)
    if img is None:
        from PIL import Image

        img = cv2.cvtColor(np.asarray(Image.open(src).convert("RGB")), cv2.COLOR_RGB2BGR)
    out, res = rectify(img, focal_35mm)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = src.stem
    if out is not None:
        cv2.imwrite(str(out_dir / f"{stem}_front.jpg"), out, [cv2.IMWRITE_JPEG_QUALITY, 92])
    d = res.to_json()
    d["source"] = str(src)
    (out_dir / f"{stem}_front.json").write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def synthetic_facade(w: int = 1600, h: int = 1200, focal_35mm: float = 26.0, yaw_deg: float = 30.0,
                     pitch_deg: float = 12.0, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """テスト用：窓の並んだ壁を、斜めから撮った形に歪ませた画像を作る。
    H = K R K^-1 をそのまま使う（主点は画像中心、焦点距離は focal_35mm どおり）ので、本物のカメラの回転と同じ性質。
    戻り値 (画像, 使った H)。"""
    import cv2

    rng = np.random.default_rng(seed)
    front = np.full((h, w, 3), 90, np.uint8)
    # 壁は中央 55% に描く（回転しても画面に残るように）
    x0, y0, ww, hh = int(w * 0.22), int(h * 0.22), int(w * 0.56), int(h * 0.56)
    cv2.rectangle(front, (x0, y0), (x0 + ww, y0 + hh), (200, 200, 200), -1)
    cols, rows = 7, 4
    cw, ch = ww // cols, hh // rows
    for r in range(rows):
        for c in range(cols):
            x, y = x0 + c * cw + cw // 6, y0 + r * ch + ch // 6
            cv2.rectangle(front, (x, y), (x + cw * 2 // 3, y + ch * 2 // 3), (60, 60, 60), -1)
            cv2.rectangle(front, (x, y), (x + cw * 2 // 3, y + ch * 2 // 3), (20, 20, 20), 2)
    for r in range(rows + 1):
        cv2.line(front, (x0, y0 + r * ch), (x0 + ww, y0 + r * ch), (120, 120, 120), 2)
    noise = rng.integers(0, 10, (h, w, 1), dtype=np.uint8)
    front = cv2.subtract(front, np.repeat(noise, 3, axis=2))
    f = focal_px_from_35mm(focal_35mm, w, h)
    K = np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1.0]])
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    Ry = np.array([[math.cos(yaw), 0, math.sin(yaw)], [0, 1, 0], [-math.sin(yaw), 0, math.cos(yaw)]])
    Rx = np.array([[1, 0, 0], [0, math.cos(pitch), -math.sin(pitch)], [0, math.sin(pitch), math.cos(pitch)]])
    H = K @ (Rx @ Ry) @ np.linalg.inv(K)
    warped = cv2.warpPerspective(front, H, (w, h), borderValue=(90, 90, 90))
    return warped, H
