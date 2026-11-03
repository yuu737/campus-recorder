"""正面化した壁の画像から、繰り返しの間隔（柱のスパン・階高）を数える。

全部コード。やること：
    1. 壁の範囲（wall_box）を切り出し、縦のエッジの強さを列ごとに、横のエッジの強さを行ごとに足す（プロファイル）
    2. プロファイルの自己相関から、一番強い繰り返しの周期（画素）を求める＝柱間隔・階高
    3. 周期に合う山（エッジの位置）を拾い、本数を数える
    4. 既知の長さが1つあれば（メジャー、PLATEAU の高さ など）画素→m に換算する。無ければ画素のまま

出てくる物は「候補」。何階建てか・スパン数が本当に合っているかは、判断役（Claude か人）が画像を見て採否を決める。
換算の根拠は result["scale"] に残す（何を・何 m と置いたか）。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class Periodic:
    axis: str                       # "x"（柱・縦の繰り返し）か "y"（階・横の繰り返し）
    period_px: float | None         # 繰り返しの周期（画素）
    strength: float                 # 自己相関のピークの高さ（0〜1。0.3 未満は弱い）
    peaks_px: list[float] = field(default_factory=list)   # エッジの位置（壁の範囲の左上からの画素）
    count: int = 0                  # 周期に合う区間の数（スパン数・階数の候補）
    period_m: float | None = None
    status: str = "unknown"         # 換算できたら estimated か measured、できなければ unknown


@dataclass
class MeasureResult:
    ok: bool
    reason: str = ""
    wall_box: list[float] | None = None
    bays: Periodic | None = None
    floors: Periodic | None = None
    ratio_bay_over_floor: float | None = None   # 縮尺に依らない比。方法の検算に使う
    scale: dict | None = None                   # {"what":..., "px":..., "m":..., "m_per_px":..., "status":...}

    def to_json(self) -> dict:
        return asdict(self)


def _profile(gray: np.ndarray, axis: str, signed: bool = False) -> np.ndarray:
    """axis="x": 縦エッジの強さを列ごとに。axis="y": 横エッジの強さを行ごとに。

    signed=True は明→暗と暗→明を区別した値（周期を求める用）。
    窓の上端（明→暗）と下端（暗→明）が等間隔に並ぶと、向きを無視した強さでは
    周期が階高の半分に出てしまう。符号付きなら半分の周期は負の相関になり、本当の階高だけが残る。
    signed=False は向きを無視した強さ（山の位置を拾う用）。"""
    import cv2

    g = cv2.GaussianBlur(gray, (0, 0), 1.2).astype(np.float32)
    d = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3) if axis == "x" else cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    if not signed:
        d = np.abs(d)
    p = d.mean(axis=0) if axis == "x" else d.mean(axis=1)
    # ゆっくりした明るさの変化を除く（木の影など）
    k = max(15, int(len(p) * 0.05) | 1)
    base = np.convolve(p, np.ones(k) / k, mode="same")
    p = p - base
    return p if signed else np.clip(p, 0, None)


def _period(p: np.ndarray, min_px: int, max_px: int) -> tuple[float | None, float]:
    """自己相関から周期を求める。戻り値 (周期 px, 強さ 0〜1)。"""
    x = p - p.mean()
    if x.std() < 1e-6 or len(x) < 2 * min_px:
        return None, 0.0
    ac = np.correlate(x, x, mode="full")[len(x) - 1:]
    ac = ac / ac[0]
    hi = min(max_px, len(ac) - 1)
    if hi <= min_px:
        return None, 0.0
    seg = ac[min_px:hi]
    i = int(np.argmax(seg)) + min_px
    # 山の頂点を放物線で補間（小数画素）
    if 1 <= i < len(ac) - 1:
        y0, y1, y2 = ac[i - 1], ac[i], ac[i + 1]
        denom = y0 - 2 * y1 + y2
        off = 0.5 * (y0 - y2) / denom if abs(denom) > 1e-9 else 0.0
    else:
        off = 0.0
    return float(i + off), float(ac[i])


def _peaks(p: np.ndarray, period: float) -> list[float]:
    """周期の 0.6 倍以上離れた局所最大を拾う（強い物から）。"""
    if period is None or period < 3:
        return []
    k = max(3, int(period * 0.3) | 1)
    sm = np.convolve(p, np.ones(k) / k, mode="same")
    thr = sm.max() * 0.15
    idx = [i for i in range(1, len(sm) - 1) if sm[i] >= sm[i - 1] and sm[i] >= sm[i + 1] and sm[i] > thr]
    idx.sort(key=lambda i: -sm[i])
    kept: list[int] = []
    for i in idx:
        if all(abs(i - j) >= 0.5 * period for j in kept):
            kept.append(i)
    return sorted(float(i) for i in kept)


def _count(peaks: list[float], period: float) -> int:
    """周期の格子に乗っている山だけを使い、その両端の間に何区間あるかを数える。
    余計な山（木の枝など）と抜けた山（隠れた柱）の両方に強い。"""
    if not peaks or not period:
        return 0
    ph = np.array(peaks) / period * 2 * np.pi
    phase = np.arctan2(np.sin(ph).mean(), np.cos(ph).mean()) / (2 * np.pi) * period  # 格子の位相
    on = [pk for pk in peaks if abs(((pk - phase) / period + 0.5) % 1.0 - 0.5) <= 0.15]
    if len(on) < 2:
        return 0
    return int(round((max(on) - min(on)) / period))


def measure(img: np.ndarray, wall_box: list[float] | None = None,
            known: tuple[str, float] | None = None, known_status: str = "measured") -> MeasureResult:
    """正面化した画像から柱間隔と階高の候補を出す。

    known: ("floor_pitch" | "bay_pitch" | "wall_height" | "wall_width", m)。1つあれば画素→m に換算する。
    known_status: その長さの出どころ（メジャーなら measured、PLATEAU の高さなどなら estimated）。
    """
    import cv2

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    H, W = gray.shape[:2]
    x0, y0, x1, y1 = [int(round(v)) for v in (wall_box or [0, 0, W, H])]
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    res = MeasureResult(ok=False, wall_box=[x0, y0, x1, y1])
    if x1 - x0 < 100 or y1 - y0 < 100:
        res.reason = "壁の範囲が小さすぎます"
        return res
    crop = gray[y0:y1, x0:x1]
    w, h = x1 - x0, y1 - y0

    out = {}
    for axis, length in (("x", w), ("y", h)):
        period, strength = _period(_profile(crop, axis, signed=True), min_px=max(8, length // 40), max_px=length // 2)
        p = _profile(crop, axis)
        pk = _peaks(p, period) if period else []
        out[axis] = Periodic(axis=axis, period_px=round(period, 1) if period else None, strength=round(strength, 3),
                             peaks_px=[round(v, 1) for v in pk], count=_count(pk, period) if period else 0)
    res.bays, res.floors = out["x"], out["y"]
    if res.bays.period_px and res.floors.period_px:
        res.ratio_bay_over_floor = round(res.bays.period_px / res.floors.period_px, 3)

    if known:
        what, m = known
        px = {"floor_pitch": res.floors.period_px, "bay_pitch": res.bays.period_px,
              "wall_height": float(h), "wall_width": float(w)}.get(what)
        if px:
            mpp = m / px
            res.scale = {"what": what, "px": px, "m": m, "m_per_px": mpp, "status": known_status}
            for per in (res.bays, res.floors):
                if per.period_px:
                    per.period_m = round(per.period_px * mpp, 3)
                    per.status = known_status
    res.ok = bool(res.bays.period_px or res.floors.period_px)
    if not res.ok:
        res.reason = "繰り返しが見つかりません（壁が小さいか、木などで隠れています）"
    return res


def measure_file(front_jpg: Path, front_json: Path | None = None, out_json: Path | None = None,
                 known: tuple[str, float] | None = None, known_status: str = "measured") -> MeasureResult:
    from .rectify import read_image

    img = read_image(front_jpg)
    wb = None
    front_json = front_json or front_jpg.with_suffix(".json")
    if front_json.exists():
        wb = json.loads(front_json.read_text(encoding="utf-8")).get("wall_box")
    res = measure(img, wb, known, known_status)
    out_json = out_json or front_jpg.with_name(front_jpg.stem + "_measure.json")
    d = res.to_json()
    d["source"] = str(front_jpg)
    out_json.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def draw_overlay(img: np.ndarray, res: MeasureResult) -> np.ndarray:
    """検出した柱・階の線を画像に描く（判断役が見るため）。"""
    import cv2

    out = img.copy()
    x0, y0, x1, y1 = [int(v) for v in res.wall_box]
    cv2.rectangle(out, (x0, y0), (x1, y1), (0, 255, 255), 3)
    if res.bays:
        for px in res.bays.peaks_px:
            cv2.line(out, (x0 + int(px), y0), (x0 + int(px), y1), (0, 0, 255), 3)
    if res.floors:
        for py in res.floors.peaks_px:
            cv2.line(out, (x0, y0 + int(py)), (x1, y0 + int(py)), (255, 0, 0), 3)
    return out
