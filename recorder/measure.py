"""正面化した壁の画像から、繰り返しの間隔（柱のスパン・階高）を数える。

全部コード。やること：
    1. 壁の範囲（wall_box）を切り出し、縦のエッジの強さを列ごとに、横のエッジの強さを行ごとに足す（プロファイル）
    2. プロファイルの自己相関から、一番強い繰り返しの周期（画素）を求める＝柱間隔・階高
    3. 周期に合う山（エッジの位置）を拾い、本数を数える
    4. 既知の長さが1つあれば（メジャーを写した区間、PLATEAU の高さ など）画素→m に換算する。無ければ画素のまま

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
    candidates: list[dict] = field(default_factory=list)  # 周期の候補（上位 3 つ）。{period_px, strength, note}。先頭が採用した物
    confidence: str = "low"         # high / mid / low。判断役が候補から選ぶ時の目安
    warnings: list[str] = field(default_factory=list)     # 「2 倍周期の可能性」「相関が弱い」など


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


def _refine(ac: np.ndarray, i: int) -> float:
    if 1 <= i < len(ac) - 1:
        y0, y1, y2 = ac[i - 1], ac[i], ac[i + 1]
        denom = y0 - 2 * y1 + y2
        return float(i + (0.5 * (y0 - y2) / denom if abs(denom) > 1e-9 else 0.0))
    return float(i)


def _ncc(x: np.ndarray, max_lag: int) -> np.ndarray:
    """ずらした 2 本の重なり部分どうしの正規化相関（長いずれでも重なりの分で割るので、長い周期が不利にならない）。"""
    n = len(x)
    full = np.correlate(x, x, mode="full")[n - 1:]
    c2 = np.concatenate([[0.0], np.cumsum(x * x)])
    out = np.zeros(max_lag + 1)
    for k in range(1, max_lag + 1):
        a = c2[n - k] - c2[0]            # x[0 : n-k] の二乗和
        b = c2[n] - c2[k]                # x[k : n] の二乗和
        d = float(np.sqrt(a * b))
        out[k] = full[k] / d if d > 1e-9 else 0.0
    return out


def _gauss(p: np.ndarray, sigma: float) -> np.ndarray:
    r = max(1, int(4 * sigma))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    return np.convolve(p, k / k.sum(), mode="same")


def _local_peaks(ac: np.ndarray, lo: int, hi: int, thr: float) -> list[tuple[float, float]]:
    return [(_refine(ac, i), float(ac[i])) for i in range(max(lo, 1), min(hi, len(ac) - 1))
            if ac[i] >= ac[i - 1] and ac[i] >= ac[i + 1] and ac[i] > thr]


def _near(cands: list[tuple[float, float]], target: float, tol: float = 0.08):
    hit = [c for c in cands if abs(c[0] - target) <= tol * target]
    return max(hit, key=lambda c: c[1]) if hit else None


def _candidates(p: np.ndarray, min_px: int, max_px: int) -> tuple[list[dict], list[str]]:
    """周期の候補を上位 3 つまで（先頭が採用）。戻り値 (候補, 警告)。

    1. 普通の自己相関の山（柱・階の周期）。
    2. 一番短い強い山が壁の長さの 1/40 より細かければ、タイルの目地などの細かい繰り返しとみなし、
       その幅でならした並びで重なり部分の正規化相関を取り直す（細かい繰り返しの整数倍に引きずられない）。
    3. 一番強い山の 1/2・1/3 にも山があれば短い方を採る（stitch.py と同じ考え）。2 倍周期の疑いは警告に出す。
    """
    n = len(p)
    warns: list[str] = []
    x = p - p.mean()
    if x.std() < 1e-6 or n < 2 * min_px:
        return [], warns
    ac = np.correlate(x, x, mode="full")[n - 1:]
    ac = ac / ac[0]
    hi = min(max_px, n - 1)
    if hi <= min_px:
        return [], warns
    cands = _local_peaks(ac, min_px, hi, 0.05)
    # 細かい繰り返し（目地）の判定：下限より短い所（4 px〜）から見て、強い山が壁の長さの 1/40 より細かければ目地
    texture = None
    fine = _local_peaks(ac, 4, hi, 0.05)
    if fine:
        best = max(c[1] for c in fine)
        first = next((c for c in sorted(fine) if c[1] >= 0.5 * best), None)
        if first and first[0] < n / 40 and first[1] >= 0.2:
            texture = first[0]
    if texture:
        sm = _gauss(p, texture)
        xs = sm - sm.mean()
        if xs.std() > 1e-6:
            top = int(0.7 * n)
            nc = _ncc(xs, top)
            lo = max(int(3 * texture), min_px)
            coarse = _local_peaks(nc, lo, top, 0.15)
            if coarse:
                warns.append(f"細かい繰り返し（目地・タイル、{texture:.1f} px）を除いて探した")
                cands = coarse
    if not cands:
        return [], warns
    # 順位：強さ × 重なりの広さ（長い周期ほどデータが少ないので少し割り引く）
    def score(c):
        sc = c[1] * (1.0 - c[0] / n) ** 0.5
        if c[0] > 0.5 * n and c[1] < 0.6:        # 繰り返しが 2 回に満たない周期は、強くなければ偶然の相関とみなす
            sc *= 0.5
        return sc
    ordered = sorted(cands, key=score, reverse=True)
    best = ordered[0]
    # 1/2・1/3 に山があれば短い方
    for _ in range(3):
        for k in (2, 3):
            sub = _near(cands, best[0] / k)
            if sub and sub[0] >= min_px and sub[1] >= max(0.1, 0.85 * best[1]):
                warns.append(f"{best[0]:.1f} px の 1/{k} にも山（{sub[0]:.1f} px、強さ {sub[1]:.2f}）→ 短い方を採用")
                best = (best[0] / k, sub[1])
                break
        else:
            break
    out = [{"period_px": round(best[0], 1), "strength": round(best[1], 3), "note": "採用"}]
    half = _near(cands, best[0] / 2)
    if half and half[0] >= min_px and half[1] >= 0.1 and abs(half[0] - best[0]) > 0.08 * best[0]:
        out.append({"period_px": round(half[0], 1), "strength": round(half[1], 3), "note": "採用の 1/2"})
    for c in ordered:
        if len(out) >= 3:
            break
        if all(abs(c[0] - o["period_px"]) > 0.08 * o["period_px"] for o in out):
            note = ""
            r = c[0] / best[0]
            if abs(r - 2) < 0.16:
                note = "採用の 2 倍"
            elif abs(r - 0.5) < 0.04:
                note = "採用の 1/2"
            out.append({"period_px": round(c[0], 1), "strength": round(c[1], 3), "note": note})
    # 2 倍周期の疑い：採用の 1/2 に（採用ほど強くはないが）山がある、または採用が 2 倍の候補の 1/2
    if best[0] > 0.5 * n:
        warns.append("周期が壁の半分より長い（繰り返しが 2 回未満。偶然の相関かもしれない）")
    if half and half[0] >= min_px and half[1] >= 0.25 * best[1] and not any("1/2" in w for w in warns):
        warns.append(f"2 倍周期の可能性（{half[0]:.1f} px にも強さ {half[1]:.2f} の山）")
    if best[1] < 0.3:
        warns.append("相関が弱い")
    return out, warns


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
            known: tuple | None = None, known_status: str = "measured") -> MeasureResult:
    """正面化した画像から柱間隔と階高の候補を出す。

    known: ("floor_pitch" | "bay_pitch" | "wall_height" | "wall_width", m)。1つあれば画素→m に換算する。
           ("segment", m, px) はメジャーを写した区間（正面化画像で読んだ px と、その区間の m）。
           物差し台帳の tape_measure。縮尺は m÷px、status は measured に固定する。
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
        cands, warns = _candidates(_profile(crop, axis, signed=True), min_px=max(8, length // 40), max_px=int(length * 0.8))
        period, strength = (cands[0]["period_px"], cands[0]["strength"]) if cands else (None, 0.0)
        p = _profile(crop, axis)
        pk = _peaks(p, period) if period else []
        conf = "low" if not cands or strength < 0.3 else ("high" if strength >= 0.5 and not [w for w in warns if "目地" not in w] else "mid")
        out[axis] = Periodic(axis=axis, period_px=period, strength=strength,
                             peaks_px=[round(v, 1) for v in pk], count=_count(pk, period) if period else 0,
                             candidates=cands, confidence=conf, warnings=warns)
    res.bays, res.floors = out["x"], out["y"]
    if res.bays.period_px and res.floors.period_px:
        res.ratio_bay_over_floor = round(res.bays.period_px / res.floors.period_px, 3)

    if known:
        what, m = known[0], known[1]
        if what == "segment":
            # メジャーの区間：画素は判断役が正面化画像で読んだ値。周期には依らない
            px = float(known[2]) if len(known) > 2 and known[2] else None
            what, known_status = "tape_measure", "measured"
        else:
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


def parse_known(text: str | None) -> tuple | None:
    """`--known` の文字列を読む。

    "floor_pitch=3.99" → ("floor_pitch", 3.99)（bay_pitch・wall_height・wall_width も同じ）
    "segment=0.698@85.3" → ("segment", 0.698, 85.3)（メジャーの区間 0.698 m が正面化画像で 85.3 px）
    """
    if not text:
        return None
    k, _, v = text.partition("=")
    k = k.strip()
    if not v:
        raise ValueError(f"--known は 名前=値 で書きます: {text}")
    if k == "segment":
        m, sep, px = v.partition("@")
        if not sep:
            raise ValueError(f"segment は 区間の m@画素 で書きます（例 segment=0.698@85.3）: {text}")
        m, px = float(m), float(px)
        if m <= 0 or px <= 0:
            raise ValueError(f"segment の m と画素は正の数: {text}")
        return ("segment", m, px)
    if k not in ("floor_pitch", "bay_pitch", "wall_height", "wall_width"):
        raise ValueError(f"--known の名前が分かりません: {k}（floor_pitch・bay_pitch・wall_height・wall_width・segment）")
    return (k, float(v))


def measure_file(front_jpg: Path, front_json: Path | None = None, out_json: Path | None = None,
                 known: tuple | None = None, known_status: str = "measured") -> MeasureResult:
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


# ---------------------------------------------------------------------------
# 11/8 K-101：メジャーからタイルの周期 → 面の中で周期が一様か → 枚数で長さ、を「根拠の鎖」で出す
#
#   recorder measure tape <写真id> --read "0→91.0" --n 4 --what tile_pitch_h --building honbu [--face f6]
#       係が目で読んだ目盛り（cm）とまたがったタイルの枚数から周期と ± を出し、
#       data/reports/<棟>/sokuryo_tape_<写真id>.json に tape の輪として書く。続けて棟の周期を突き合わせる。
#   recorder measure pitch <棟> --what tile_pitch_h [--write-rulers]
#       棟のメジャー写真の周期を突き合わせ（差が ± の和を超えれば警告）、棟の周期を決める。
#       --write-rulers で物差し台帳 data/rulers.json の tile_pitch_h_<棟> に書く（台帳は道具でだけ書く）。
#   recorder measure uniform <正面化.jpg> --what tile_pitch_h --photo <写真id> --face f6 [--parts 4]
#       正面化した面を 3〜4 か所に分け、それぞれで周期（px）を取る。ばらつきが 3 % 以下なら uniform_period の輪。
#   recorder measure chain <棟> --face f6 --key bay_pitch --what tile_pitch_h --count 24 --count-photo <id> --count-what "…"
#       [--uniform <uniform.json>] [--ratio 180 910 --ratio-photo <id> --ratio-what "…"]
#       周期 × 枚数（× px の比）で値を出し、鎖・tol・measured_kind・前の値との差（縮尺の付け替え）を
#       data/reports/<棟>/sokuryo_chain_<面>.json に書く。棟 JSON は書かない（写すのは設計係）。
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parent.parent
MEASURED_LINKS = ("tape", "count", "px_ratio", "uniform_period")   # これだけの鎖なら measured（K-101）
TAPE_END_M = 0.005          # メジャーの読み：両端 ±0.5 cm（第 1 部の例のとおり、周期の ± は 0.005 m ÷ 枚数）
UNIFORM_THR = 0.03          # 面の中の周期のばらつき（(最大−最小)÷平均）の閾値。超えれば uniform_period の輪は出さない
                            # （11/8 Fable：1 % → 3 %。ばらつきの半幅 ÷ 平均は ± に積む）
AXIS_OF = {"tile_pitch_h": "x", "tile_pitch_v": "y"}   # 横の周期は縦の目地（列ごと）、縦の周期は横の目地（行ごと）


def _ceil(v: float, step: float) -> float:
    """± は切り上げて丸める（丸めで鎖の ± より狭くならないように）。"""
    import math

    return round(math.ceil(v / step - 1e-9) * step, 10)


def parse_read(text: str) -> tuple[float, float, str]:
    """目盛りの読み "0→91.0"（単位が無ければ cm。"0→910 mm"・"0→0.91 m"・"0→91.0 cm" も読む）を
    (始め cm, 終わり cm, 表示用の文字 "a→b cm") に。→ の代わりに -> 〜 ~ も読む。読めなければ ValueError。
    schema.chain_tol に渡す read はこの表示用の文字（cm）にそろえる（監査 B：解釈を 1 か所に）。"""
    import re

    s = str(text).strip().lower().replace("−", "-")
    m = re.fullmatch(r"(.*?)\s*(mm|cm|m)?", s)
    body, unit = m.group(1), (m.group(2) or "cm")
    parts = [t.strip() for t in re.split(r"→|->|〜|~", body)]
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"目盛りは 始め→終わり（cm）で書きます（例 0→91.0）: {text}")
    try:
        a, b = float(parts[0]), float(parts[1])
    except ValueError:
        raise ValueError(f"目盛りが数ではありません: {text}") from None
    if b <= a:
        raise ValueError(f"終わりの目盛りが始めより大きくありません: {text}")
    k = {"mm": 0.1, "cm": 1.0, "m": 100.0}[unit]
    if unit == "cm":
        return a, b, f"{parts[0]}→{parts[1]} cm"
    a, b = a * k, b * k
    return a, b, f"{a:g}→{b:g} cm"


def tape_link(photo: str, read: str, n: int, what: str) -> dict:
    """メジャーの読みから tape の輪を作る。value＝(終わり−始め)÷枚数（m）、tol＝0.005 m ÷ 枚数。"""
    if not isinstance(n, int) or n <= 0:
        raise ValueError(f"枚数 n は正の整数: {n}")
    a, b, shown = parse_read(read)
    return {"link": "tape", "photo": photo, "read": shown, "n": n, "what": what,
            "value": round((b - a) / 100.0 / n, 6), "tol": _ceil(TAPE_END_M / n, 1e-6)}


def chain_tol(chain: list[dict], value: float) -> float:
    """鎖から積んだ ±（m）。式は 1 か所：recorder.schema.chain_tol（T1）を呼ぶ（監査 B）。

    式（schema の docstring と同じ）：chain_tol = |value| × Σ（輪ごとの相対の誤差）
      tape 0.005 m ÷ 区間／count tol ÷ n（confirmed_by があれば 0）／px_ratio px_tol ÷ a + px_tol ÷ b／
      uniform_period (最大 − 最小) ÷ 2 ÷ 平均／それ以外の輪 0。
    渡す前に tape の read を "a→b cm" にそろえる（単位なしは cm、mm・m も通す）。
    読めない輪（n=0、read が読めない、px が足りない…）や数でない value は ValueError（黙って 0 にしない）。"""
    import math

    from . import schema

    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"value が数ではありません: {value!r}")
    norm = []
    for ln in chain or []:
        if not isinstance(ln, dict):
            raise ValueError(f"鎖の輪が辞書ではありません: {ln!r}")
        if ln.get("link") == "tape" and ln.get("read") is not None:
            ln = {**ln, "read": parse_read(ln["read"])[2]}
        rel, why = schema.link_rel(ln)
        if rel is None:
            raise ValueError(f"鎖の輪が読めません（{ln.get('link')}）: {why}")
        norm.append(ln)
    t = schema.chain_tol(norm, value)
    if not math.isfinite(t):
        raise ValueError("鎖の ± が出せません（schema.chain_tol が inf）")
    return t


# --- 面の中の周期（タイルの目地の細かい繰り返し） ---

def fine_period(p: np.ndarray, lo: float = 4, hi: float | None = None, guess: float | None = None) -> tuple[float | None, float]:
    """細かい繰り返し（タイルの目地）の周期 px を自己相関から。戻り値 (周期 px, 強さ 0〜1)。

    guess が無ければ [lo, hi] の山のうち、一番強い山の半分以上の強さを持つ一番短い山（基本の周期）。
    guess があれば guess ±15 % の一番強い山。最後に 1・2・3… 倍の山の位置を原点を通る直線で当てて小数 px にする
    （1 つの山より何倍も細かく読める）。"""
    x = np.asarray(p, float)
    x = x - x.mean()
    n = len(x)
    if n < 8 or x.std() < 1e-9:
        return None, 0.0
    ac = np.correlate(x, x, mode="full")[n - 1:]
    ac = ac / ac[0]
    per, s0, _, _ = _ac_period(ac, lo, hi, guess)
    return per, s0


def _ac_period(ac: np.ndarray, lo: float = 4, hi: float | None = None, guess: float | None = None,
               m_cap: int = 40) -> tuple[float | None, float, int, float | None]:
    """自己相関の並び ac から (周期 px, 強さ, 最後に当てた倍数 m, その山の位置 px)。

    最後の 2 つは「m 周期にまたがる長さ（px）」の読み（px_ratio の輪に使う。m は自己相関の m 番目の山）。"""
    n = len(ac)
    hi = int(min(hi or n // 3, n - 2))
    if guess:
        a, b = int(max(2, guess * 0.85)), int(min(hi, guess * 1.15) + 1)
        pk = _local_peaks(ac, a, b, -1.0)
        if not pk:
            return None, 0.0, 0, None
        p0, s0 = max(pk, key=lambda c: c[1])
    else:
        pk = _local_peaks(ac, int(max(2, lo)), hi, 0.05)
        if not pk:
            return None, 0.0, 0, None
        best = max(c[1] for c in pk)
        p0, s0 = next(c for c in sorted(pk) if c[1] >= 0.5 * best)
    ms, lags = [1.0], [p0]
    cur = p0
    for m in range(2, m_cap + 1):
        c = m * cur                       # 今までの当てはめで次の山の位置を予想（ずれが積もらない）
        if c + 0.3 * p0 >= n / 2:
            break
        near = _local_peaks(ac, int(c - 0.3 * p0), int(c + 0.3 * p0) + 1, -1.0)
        if not near:
            break
        lag = min(near, key=lambda q: abs(q[0] - c))[0]
        ms.append(float(m))
        lags.append(lag)
        ms_a, lags_a = np.array(ms), np.array(lags)
        cur = float((ms_a * lags_a).sum() / (ms_a * ms_a).sum())
    return cur, float(s0), int(ms[-1]), float(lags[-1])


def _line_ac(crop: np.ndarray, axis: str) -> np.ndarray:
    """1 行（axis="x"）か 1 列（"y"）ずつ自己相関を取って平均する。

    段ごとに半枚ずれる貼り方（馬踏み）では、列ごとに足した並びの周期が半分に出る。1 行ずつなら行の中の周期（1 枚）が残る。"""
    import cv2

    g = cv2.GaussianBlur(crop.astype(np.float32), (0, 0), 1.0)
    d = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3) if axis == "x" else cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3).T
    d = d - d.mean(axis=1, keepdims=True)
    n = d.shape[1]
    f = np.fft.rfft(d, 2 * n, axis=1)
    ac = np.fft.irfft(f * np.conj(f), axis=1)[:, :n]
    z = ac[:, :1]
    ac = ac[(z[:, 0] > 1e-9)] / z[(z[:, 0] > 1e-9)]
    return ac.mean(axis=0) if len(ac) else np.zeros(n)


def uniform_period(img: np.ndarray, *, axis: str, wall_box: list[float] | None = None, parts: int = 4,
                   lo: float = 4, hi: float | None = None, thr: float = UNIFORM_THR, rowwise: bool = False) -> dict:
    """正面化した面を axis の向きに parts か所に分け、それぞれで周期（px）を取る。

    戻り値 {"ok", "period_px"（全体）, "px"（か所ごと）, "spread"（(最大−最小)÷平均）, "parts"（px の範囲）, "reason"}。
    ok は全部のか所で周期が取れて、ばらつきが thr 以下の時だけ。"""
    import cv2

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    H, W = gray.shape[:2]
    x0, y0, x1, y1 = [int(round(v)) for v in (wall_box or [0, 0, W, H])]
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    crop = gray[y0:y1, x0:x1]
    out = {"ok": False, "axis": axis, "wall_box": [x0, y0, x1, y1], "period_px": None, "strength": 0.0, "px": [],
           "parts": [], "spread": None, "threshold": thr, "reason": ""}
    length = crop.shape[1] if axis == "x" else crop.shape[0]
    if not 3 <= parts <= 8 or length < parts * 40:
        out["reason"] = f"面が短いか、分け方が変です（長さ {length} px、{parts} か所）"
        return out
    if rowwise:     # 1 行（1 列）ずつの自己相関の平均。馬踏みの横の周期・長い区間の px の読みに
        whole, strength, m_last, lag_last = _ac_period(_line_ac(crop, axis), lo, hi, None, m_cap=100)
        out["span"] = {"m": m_last, "px": round(lag_last, 2)} if whole else None
    else:
        whole, strength = fine_period(_profile(crop, axis, signed=True), lo, hi)
    out["rowwise"] = rowwise
    out["period_px"], out["strength"] = (round(whole, 3) if whole else None), round(strength, 3)
    if not whole:
        out["reason"] = "面の全体で周期が見つかりません"
        return out
    edges = np.linspace(0, length, parts + 1).astype(int)
    for a, b in zip(edges[:-1], edges[1:]):
        sub = crop[:, a:b] if axis == "x" else crop[a:b, :]
        if rowwise:
            per = _ac_period(_line_ac(sub, axis), lo, hi, whole)[0]
        else:
            per, _ = fine_period(_profile(sub, axis, signed=True), lo, hi, guess=whole)
        out["parts"].append([int(a), int(b)])
        out["px"].append(round(per, 2) if per else None)
    if any(v is None for v in out["px"]):
        out["reason"] = "周期が取れないか所がある（木・影・別の仕上げ）"
        return out
    xs = out["px"]
    out["spread"] = round((max(xs) - min(xs)) / (sum(xs) / len(xs)), 5)
    out["drift"] = _drift(xs, axis)
    if out["drift"]:
        out["note"] = "正面化の歪みの疑い（か所ごとの周期が端から端へ一方向に変わる。検収係が見る）"
    if out["spread"] > thr:
        out["reason"] = (f"ばらつき {out['spread'] * 100:.2f} % が閾値 {thr * 100:.1f} % を超える"
                         "（周期が一様と言えない → その面は estimated）")
        return out
    out["ok"] = True
    return out


def _drift(xs: list[float], axis: str) -> dict | None:
    """か所ごとの周期が一方向に（単調に）変わるか。変われば {"direction", "percent"}（最初→最後の変化 %）。"""
    if len(xs) < 3:
        return None
    d = [b - a for a, b in zip(xs[:-1], xs[1:])]
    if not (all(v > 0 for v in d) or all(v < 0 for v in d)):
        return None
    pct = (xs[-1] - xs[0]) / xs[0] * 100
    way = "上から下" if axis == "y" else "左から右"
    return {"direction": f"{way}へ{'大きく' if pct > 0 else '小さく'}なる", "percent": round(pct, 2)}


def uniform_link(res: dict, *, photo: str, face: str | None, what: str) -> dict | None:
    """uniform_period の結果を鎖の輪に。ok でなければ None（輪を出さない）。"""
    if not res.get("ok"):
        return None
    dir_ = "横" if res["axis"] == "x" else "縦"
    ln = {"link": "uniform_period", "photo": photo, "px": res["px"],
          "what": f"正面化した {face or '面'} の中（{res['wall_box']}）で{dir_}の周期が一様（{len(res['px'])} か所、ばらつき "
                  f"{res['spread'] * 100:.2f} % ≤ {res['threshold'] * 100:.1f} %、{what}）",
          "period_px": res["period_px"]}
    if res.get("drift"):
        ln["drift"] = res["drift"]
        ln["note"] = res.get("note") or "正面化の歪みの疑い"
    return ln


# --- 棟の周期を決める（メジャー写真の突き合わせ）と物差し台帳 ---

def _reports(reports: Path | None) -> Path:
    return Path(reports) if reports else REPO / "data" / "reports"


def write_tape(link: dict, *, building: str, face: str | None, reports: Path | None = None, by: str = "") -> Path:
    from datetime import datetime

    d = {"schema": "sokuryo-tape/1", "building": building, "face": face, "photo": link["photo"],
         "made_by": by, "made_at": datetime.now().isoformat(timespec="seconds"), "link": link}
    p = _reports(reports) / building / f"sokuryo_tape_{link['photo']}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def pitch_summary(building: str, what: str, reports: Path | None = None) -> dict:
    """棟のメジャー写真（sokuryo_tape_*.json）の周期を突き合わせ、棟の周期を決める。

    採るのは ± が一番狭い読み（またがった枚数が一番多い物）。他の読みは検算（checks）に残し、
    差が ± の和を超えれば警告。同じ面の別の読みで差が ± の和以内なら、その面の uniform_period の代わりに使える。"""
    rows = []
    for f in sorted((_reports(reports) / building).glob("sokuryo_tape_*.json")):
        if f.name == "sokuryo_tape_pitch.json":
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ln = d.get("link") or {}
        if ln.get("link") == "tape" and ln.get("what") == what:
            rows.append({"photo": ln["photo"], "face": d.get("face"), "value": ln["value"], "tol": ln["tol"], "link": ln})
    out = {"building": building, "what": what, "value": None, "tol": None, "photo": None, "face": None,
           "link": None, "checks": [], "warnings": []}
    if not rows:
        out["warnings"].append(f"{building} に {what} のメジャーの読みがありません（estimated の道で進める）")
        return out
    rows.sort(key=lambda r: (r["tol"], -r["link"]["n"]))
    best = rows[0]
    out.update({k: best[k] for k in ("value", "tol", "photo", "face", "link")})
    for r in rows[1:]:
        diff = r["value"] - best["value"]
        ok = abs(diff) <= r["tol"] + best["tol"] + 1e-12
        out["checks"].append({"photo": r["photo"], "face": r["face"], "value": r["value"], "tol": r["tol"],
                              "diff": round(diff, 6), "diff_pct": round(diff / best["value"] * 100, 2), "ok": ok})
        if not ok:
            out["warnings"].append(f"{r['photo']} の周期 {r['value']} m と {best['photo']} の {best['value']} m の差 "
                                   f"{diff * 1000:+.2f} mm が ± の和 {(r['tol'] + best['tol']) * 1000:.2f} mm を超える"
                                   "（読み直すか、別のタイルか、伸縮目地をまたいでいないか）")
    return out


def write_pitch_summary(s: dict, reports: Path | None = None) -> Path:
    p = _reports(reports) / s["building"] / "sokuryo_tape_pitch.json"
    try:
        allw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        allw = {"schema": "sokuryo-tape-pitch/1", "building": s["building"]}
    allw[s["what"]] = s
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(allw, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def _dump_rulers(d: dict) -> str:
    """data/rulers.json の書式（上の鍵は 1 行ずつ、rulers は 1 物差し 1 行）のまま書く。"""
    lines = ["{"]
    for k in [k for k in d if k != "rulers"]:
        lines.append(f"  {json.dumps(k, ensure_ascii=False)}: {json.dumps(d[k], ensure_ascii=False)},")
    lines.append('  "rulers": [')
    lines.append(",\n".join("    " + json.dumps(r, ensure_ascii=False) for r in d.get("rulers", [])))
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


def write_ruler(s: dict, rulers: Path | None = None) -> dict:
    """棟の周期を物差し台帳の <what>_<棟>（例 tile_pitch_h_honbu）に書く。台帳はこの道具でだけ書く。

    項目が無ければ足す（T1 が足す項目と同じ id）。value・tol・status measured・evidence（写真 id）・chain（tape 1 輪）・method。
    family（同じ見た目の棟）など他の欄は触らない（family は根拠にしない欄）。鍵（ledger）を持って読む→書く。"""
    from datetime import datetime

    from . import ledger

    if s.get("value") is None:
        raise ValueError(f"{s['building']} の {s['what']} はメジャーの読みが無いので書けません")
    path = Path(rulers) if rulers else REPO / "data" / "rulers.json"
    rid = f"{s['what']}_{s['building']}"
    ln = s["link"]
    with ledger.lock("ledger", label=f"measure pitch {rid}"):
        d = json.loads(path.read_text(encoding="utf-8"))
        rs = d.setdefault("rulers", [])
        r = next((x for x in rs if x.get("id") == rid), None)
        if r is None:
            r = {"id": rid,
                 "what": f"{s['building']} の外装タイルの{'横' if s['what'].endswith('_h') else '縦'}の周期（タイル＋目地）",
                 "value": None, "unit": "m", "note": "measure pitch --write-rulers が足した（台帳に項目が無かった）"}
            rs.append(r)
        checks = "（" + "、".join(f"{c['photo']} {c['diff_pct']:+.2f} %" for c in s["checks"]) + "）" if s["checks"] else ""
        r.update({"value": s["value"], "unit": "m", "tol": s["tol"], "status": "measured", "evidence": [s["photo"]],
                  "chain": [ln],
                  "method": f"メジャーの目盛り {ln['read']} を {ln['n']} 枚で割った（写真 {s['photo']}、面 {s.get('face') or '不明'}）。"
                            f"± は両端 ±0.5 cm ÷ {ln['n']} 枚。検算 {len(s['checks'])} 枚{checks}",
                  "written_at": datetime.now().isoformat(timespec="seconds")})
        if s.get("warnings"):
            r["warnings"] = s["warnings"]
        else:
            r.pop("warnings", None)
        ledger.write_text_atomic(path, _dump_rulers(d))
    return r


# --- 鎖から値を出す ---

KEY_IN_FACE = {"bay_pitch": "bays.pitch", "pier_width": "bays.pier_width", "bay_count": "bays.count"}


def _building_value(building: str, face: str, key: str, buildings: Path | None) -> dict | None:
    """棟 JSON（読むだけ）の同じ面・同じ鍵の今の値。鍵は面の中の点区切り（例 bays.pitch・length・top_z）か
    別名（bay_pitch → bays.pitch）。面に無い floor_pitch などは棟の上の段の同じ鍵（棟で 1 つの値）。"""
    p = (Path(buildings) if buildings else REPO / "data" / "buildings") / f"{building}.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    f = next((x for x in d.get("faces") or [] if isinstance(x, dict) and x.get("id") == face), None)
    if f is None:
        return None
    path = KEY_IN_FACE.get(key, key)
    v, where = f, f"faces[{face}].{path}"
    for k in path.split("."):
        v = v.get(k) if isinstance(v, dict) else None
    if not (isinstance(v, dict) and v.get("value") is not None) and isinstance(d.get(key), dict):
        v, where = d[key], key
    if not isinstance(v, dict) or v.get("value") is None:
        return None
    rel = p.resolve().relative_to(REPO).as_posix() if p.resolve().is_relative_to(REPO) else p.as_posix()
    return {"ref": f"{rel}#{where}", "value": v["value"], "status": v.get("status"), "tol": v.get("tol"),
            "ruler": None, "baseline": "building_json"}


def _baseline(building: str, face: str, key: str, reports: Path | None, buildings: Path | None) -> dict | None:
    """縮尺の付け替えの前の値：棟 JSON の今の値を優先し、無ければ sokuryo_<面>.json（11/8 Fable）。"""
    return _building_value(building, face, key, buildings) or _prev_value(building, face, key, reports)


def _prev_value(building: str, face: str, key: str, reports: Path | None) -> dict | None:
    """同じ面の前の値（sokuryo_<面>.json の key。疑似本番では PLATEAU 由来の estimated）。"""
    p = _reports(reports) / building / f"sokuryo_{face}.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    v = d.get(key)
    if not isinstance(v, dict) or v.get("value") is None:
        return None
    ruler = (d.get("scale") or {}).get("ruler")
    rel = p.resolve().relative_to(REPO).as_posix() if p.resolve().is_relative_to(REPO) else p.as_posix()
    return {"ref": f"{rel}#{key}", "value": v["value"], "status": v.get("status"), "tol": v.get("tol"), "ruler": ruler,
            "baseline": "sokuryo_json"}


def build_chain(*, building: str, face: str, pitch: dict, count: dict | None = None, uniform: dict | None = None,
                ratios: list[dict] | None = None, unit: str = "m", ratio_from_h: dict | None = None) -> dict:
    """周期 × 枚数 × px の比 で値を出し、第 1 部「鎖の書き方」の形で返す。

    pitch：pitch_summary の戻り（building・face・value・tol・photo・link・checks）。
    count：{"photo","n","what","tol"(既定 1)}。uniform：uniform_period の輪（無ければ同じ面の 2 枚目のメジャーで代える）。
    ratios：[{"photo","px":[a,b],"what"}]（値に a÷b を掛ける）。
    measured になるのは鎖が tape・count・px_ratio・uniform_period だけの時。次の時は estimated で、理由の輪を足す：
      メジャーが別の棟（claim：同じタイルという申告）、メジャーが別の面か面が不明（assume）、
      枚数で広げるのに面の周期が一様の根拠が無い（assume）。

    ratio_from_h（11/8 Fable、縦のメジャー写真が無い時）：横の周期（tape）から、同じ正面化画像の px の比で縦の周期を出す。
      {"photo", "h_px", "n_h", "v_px", "n_v", "uniform_h", "uniform_v"}。h_px は横 n_h 枚にまたがる長さ、v_px は縦 n_v 段に
      またがる長さ（どちらも 1 行・1 列ずつの自己相関の n 番目の山の位置）。値 ＝ 横の周期 × (v_px ÷ h_px) × (n_h ÷ n_v)。
      n は自己相関の n 番目の山なので数え違いは 0（count の tol 0。周期の取り違えは uniform と近接の縦の読みで検算）。"""
    if pitch.get("value") is None:
        raise ValueError("周期の値がありません（measure tape を先に）")
    tl = dict(pitch["link"])
    chain: list[dict] = [tl]
    value = float(tl["value"])
    if pitch["building"] != building:
        chain.append({"link": "claim", "what": f"利用者の申告：{pitch['building']} と {building} は同じタイル（確かめていない。"
                                              f"{building} にメジャー写真が無いので {pitch['building']} の周期を使った）"})
    elif pitch.get("face") != face:
        chain.append({"link": "assume", "what": f"メジャーを当てた面（{pitch.get('face') or '不明'}）の周期を {face} に当てた"})
    if count:
        if uniform is None:
            same = [c for c in pitch.get("checks", []) if c.get("face") == face and c.get("ok")]
            if pitch["building"] == building and pitch.get("face") == face and same:
                c = same[0]
                # 欄の名前は px（検証が読む欄）。中身は 2 枚のメジャーの周期を mm で（比だけ使うので単位は効かない）
                uniform = {"link": "uniform_period", "photo": c["photo"],
                           "px": [round(pitch["value"] * 1000, 3), round(c["value"] * 1000, 3)],
                           "px_unit": "mm",     # 2 枚のメジャーの周期（ばらつきの比だけ使う。T1 の検証は "px" か "mm"）
                           "what": f"{face} の別の場所のメジャー（{c['photo']}）で周期を検算（差 {c['diff_pct']:+.2f} %、± の和以内）"}
        if uniform is not None:
            chain.append(uniform)
        else:
            chain.append({"link": "assume", "what": f"{face} の中で周期が一様と置いた（自己相関もメジャーの検算も無い）"})
        n = int(count["n"])
        if n <= 0:
            raise ValueError(f"枚数は正の整数: {n}")
        cl = {"link": "count", "photo": count["photo"], "n": n, "what": count.get("what", ""), "tol": count.get("tol", 1)}
        if count.get("confirmed_by"):
            if count["confirmed_by"] == count["photo"]:
                raise ValueError(f"2 枚目の数えの写真が 1 枚目と同じです: {count['photo']}")
            cl["confirmed_by"] = count["confirmed_by"]
        chain.append(cl)
        value *= n
    if ratio_from_h:
        if count:
            raise ValueError("ratio_from_h と count は一緒に使わない")
        q = ratio_from_h
        for k in ("uniform_h", "uniform_v"):
            chain.append(q[k] if q.get(k) else
                         {"link": "assume", "what": f"{face} の中で{'横' if k.endswith('h') else '縦'}の周期が一様と置いた（ばらつきが閾値を超えた）"})
        nh, nv, hp, vp = int(q["n_h"]), int(q["n_v"]), float(q["h_px"]), float(q["v_px"])
        if min(nh, nv) <= 0 or min(hp, vp) <= 0:
            raise ValueError(f"ratio_from_h の枚数と px は正の数: {q}")
        why = "自己相関の {} 番目の山（数え違いは 0。周期の取り違えは一様の輪と近接の縦の読みで検算）"
        chain.append({"link": "count", "photo": q["photo"], "n": nh, "tol": 0, "role": "times",
                      "what": f"横 {nh} 枚（{why.format(nh)}）"})
        chain.append({"link": "count", "photo": q["photo"], "n": nv, "tol": 0, "role": "divide",
                      "what": f"縦 {nv} 段（{why.format(nv)}）"})
        chain.append({"link": "px_ratio", "photo": q["photo"], "px": [round(vp, 2), round(hp, 2)],
                      "what": f"縦 {nv} 段の px ÷ 横 {nh} 枚の px（同じ正面化画像の中）"})
        value *= (vp / hp) * (nh / nv)
    for r in ratios or []:
        a, b = float(r["px"][0]), float(r["px"][1])
        if a <= 0 or b <= 0:
            raise ValueError(f"px の比は正の数: {r['px']}")
        chain.append({"link": "px_ratio", "photo": r["photo"], "px": [a, b], "what": r.get("what", "")})
        value *= a / b
    measured = all(ln["link"] in MEASURED_LINKS for ln in chain)
    value = round(value, 5)
    tol = _ceil(max(chain_tol(chain, value), 1e-4), 1e-4)
    ev: list[str] = []
    for ln in chain:
        for ph in (ln.get("photo"), ln.get("confirmed_by")):
            if ph and ph not in ev:
                ev.append(ph)
    words = {"tape": "メジャー", "count": "数えた枚数", "px_ratio": "px の比", "uniform_period": "周期が一様",
             "claim": "申告", "assume": "仮定"}
    parts = [f"タイルの周期 {tl['value']} m（メジャー {tl['photo']}：{tl['read']}、{tl['n']} 枚）"]
    if count:
        again = f"、{count['confirmed_by']} でも同じ枚数" if count.get("confirmed_by") else ""
        parts.append(f"× {count['n']} 枚（{count['photo']} で数えた{('：' + count['what']) if count.get('what') else ''}{again}）")
    if ratio_from_h:
        q = ratio_from_h
        parts.append(f"× 縦 {q['n_v']} 段 {q['v_px']:.2f} px ÷ 横 {q['n_h']} 枚 {q['h_px']:.2f} px × {q['n_h']}/{q['n_v']}"
                     f"（{q['photo']} の正面化画像の中の比）")
    for r in ratios or []:
        parts.append(f"× {r['px'][0]}/{r['px'][1]} px（{r['photo']}{('：' + r['what']) if r.get('what') else ''}）")
    names = [words.get(ln["link"], ln["link"]) for ln in chain]
    method = " ".join(parts) + f" = {value} {unit}。鎖：" + " → ".join(names) + f"。± は輪ごとに積んだ = {tol} {unit}"
    if not measured:
        method += ("。鎖に測っていない輪（" + "・".join(sorted({ln["link"] for ln in chain if ln["link"] not in MEASURED_LINKS}))
                   + "）があるので estimated")
    m = {"value": value, "unit": unit, "tol": tol, "status": "measured" if measured else "estimated"}
    if measured:
        m["measured_kind"] = "direct" if len(chain) == 1 and chain[0]["link"] == "tape" else "chain"
    m.update({"evidence": ev, "method": method, "chain": chain})
    return m


def rescale_record(m: dict, prev: dict | None) -> dict | None:
    """縮尺の付け替え：同じ面の前の値（PLATEAU 由来）から何 % 動いたか（README の「付け替えで何 % 動いたか」用）。"""
    if not prev or not prev.get("value"):
        return None
    diff = (m["value"] - float(prev["value"])) / float(prev["value"]) * 100
    return {"baseline": prev.get("baseline", "sokuryo_json"), "ref": prev["ref"], "prev_value": prev["value"], "prev_status": prev.get("status"), "prev_tol": prev.get("tol"),
            "prev_ruler": prev.get("ruler"), "new_value": m["value"], "new_status": m["status"], "new_tol": m["tol"],
            "diff_pct": round(diff, 2)}


def write_chain(building: str, face: str, key: str, m: dict, *, rescale: dict | None = None, scale: dict | None = None,
                reports: Path | None = None) -> Path:
    """data/reports/<棟>/sokuryo_chain_<面>.json の values[key] に書く（他の鍵は残す）。"""
    from datetime import datetime

    p = _reports(reports) / building / f"sokuryo_chain_{face}.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        d = {"schema": "sokuryo-chain/1", "building": building, "face": face, "values": {}, "rescale": {}, "scale": {}}
    d.setdefault("values", {})[key] = m
    if rescale:
        d.setdefault("rescale", {})[key] = rescale
    else:
        d.setdefault("rescale", {}).pop(key, None)
    if scale:
        d.setdefault("scale", {})[key] = scale
    d["made_at"] = datetime.now().isoformat(timespec="seconds")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


# --- コマンド（recorder measure …） ---

def add_args(p) -> None:
    s = p.add_subparsers(dest="measure_cmd", required=True)
    q = s.add_parser("tape", help="メジャーの目盛り（係が読んだ cm）と枚数から周期と ± → sokuryo_tape_<写真id>.json")
    q.add_argument("photo")
    q.add_argument("--read", required=True, help='目盛り cm（例 "0→91.0"）。両端はタイルの目地の芯')
    q.add_argument("--n", type=int, required=True, help="またがったタイルの枚数")
    q.add_argument("--what", required=True, choices=sorted(AXIS_OF))
    q.add_argument("--building", required=True)
    q.add_argument("--face", default=None)
    q.add_argument("--by", default="", help="読んだ係とモデル")
    q.add_argument("--reports", default=None, help="既定 data/reports（試す時は一時フォルダ）")
    q = s.add_parser("pitch", help="棟のメジャー写真の周期を突き合わせて棟の周期を決める（--write-rulers で物差し台帳に）")
    q.add_argument("building")
    q.add_argument("--what", required=True, choices=sorted(AXIS_OF))
    q.add_argument("--write-rulers", action="store_true")
    q.add_argument("--rulers", default=None, help="既定 data/rulers.json（試す時は写し）")
    q.add_argument("--reports", default=None)
    q = s.add_parser("uniform", help="正面化した面を 3〜4 か所に分けて周期（px）が一様か → uniform_period の輪")
    q.add_argument("front_jpg")
    q.add_argument("--what", required=True, choices=sorted(AXIS_OF))
    q.add_argument("--photo", required=True, help="正面化の元の写真 id")
    q.add_argument("--face", default=None)
    q.add_argument("--parts", type=int, default=4)
    q.add_argument("--px-range", type=float, nargs=2, default=None, metavar=("LO", "HI"), help="周期を探す px の範囲")
    q.add_argument("--thr", type=float, default=UNIFORM_THR)
    q.add_argument("--wall-box", type=float, nargs=4, default=None, metavar=("X0", "Y0", "X1", "Y1"),
                   help="壁の範囲（無ければ正面化の JSON の wall_box か画像全体）")
    q.add_argument("--out", default=None, help="既定 <front>_uniform_<what>.json")
    q = s.add_parser("chain", help="周期 × 枚数（× px の比）で値と鎖 → sokuryo_chain_<面>.json")
    q.add_argument("building")
    q.add_argument("--face", required=True)
    q.add_argument("--key", required=True, help="値の名前（bay_pitch・floor_pitch・length など。前の値の照合にも使う）")
    q.add_argument("--what", required=True, choices=sorted(AXIS_OF))
    q.add_argument("--pitch-from", default=None, help="別の棟の周期を使う（その棟にメジャー写真が無い時だけ。estimated・申告の輪）")
    q.add_argument("--count", type=int, default=None)
    q.add_argument("--count-photo", default=None)
    q.add_argument("--count-photo2", default=None, help="2 枚目の写真で同じ枚数を数えた時の写真 id（数え違いの ± を 0 に）")
    q.add_argument("--count-what", default="")
    q.add_argument("--count-tol", type=float, default=1)
    q.add_argument("--uniform", default=None, help="measure uniform の結果 JSON")
    q.add_argument("--ratio", type=float, nargs=2, action="append", default=None, metavar=("A", "B"))
    q.add_argument("--ratio-photo", action="append", default=None)
    q.add_argument("--ratio-what", action="append", default=None)
    q.add_argument("--reports", default=None)
    q.add_argument("--buildings", default=None, help="前の値を読む棟 JSON のフォルダ（既定 data/buildings。読むだけ）")
    q.add_argument("--ratio-from-h", default=None, metavar="FRONT_JPG",
                   help="--what tile_pitch_v で、横のメジャーの周期から同じ正面化画像の px の比で縦を出す（縦のメジャー写真が無い時）")
    q.add_argument("--photo", default=None, help="--ratio-from-h の正面化の元の写真 id")
    q.add_argument("--h-box", type=float, nargs=4, default=None, metavar=("X0", "Y0", "X1", "Y1"),
                   help="横の周期を取る所（横に長くタイルが続く帯）")
    q.add_argument("--v-box", type=float, nargs=4, default=None, metavar=("X0", "Y0", "X1", "Y1"),
                   help="縦の周期を取る所（縦に長くタイルが続く柱型）")
    q.add_argument("--px-range", type=float, nargs=2, default=(6, 40), metavar=("LO", "HI"))


def _cli_uniform(a) -> int:
    from .rectify import read_image

    f = Path(a.front_jpg)
    wb = a.wall_box
    if wb is None and f.with_suffix(".json").exists():
        wb = json.loads(f.with_suffix(".json").read_text(encoding="utf-8")).get("wall_box")
    lo, hi = a.px_range or (4, None)
    res = uniform_period(read_image(f), axis=AXIS_OF[a.what], wall_box=wb, parts=a.parts, lo=lo, hi=hi, thr=a.thr)
    res.update({"photo": a.photo, "face": a.face, "what": a.what, "image": str(f),
                "link": uniform_link(res, photo=a.photo, face=a.face, what=a.what)})
    out = Path(a.out) if a.out else f.with_name(f"{f.stem}_uniform_{a.what}.json")
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    sp = "-" if res["spread"] is None else f"{res['spread'] * 100:.2f} %"
    print(f"周期（全体）{res['period_px']} px、か所ごと {res['px']}、ばらつき {sp} → {out}")
    if res.get("drift"):
        print(f"注意：{res['note']}（{res['drift']['direction']}、{res['drift']['percent']:+.2f} %）")
    print("uniform_period の輪を出した" if res["ok"] else f"輪を出さない：{res['reason']}")
    return 0 if res["ok"] else 1


def _cli_ratio_from_h(a, reports: Path | None) -> int:
    from .rectify import read_image

    if a.what != "tile_pitch_v" or not (a.photo and a.h_box and a.v_box):
        print("--ratio-from-h は --what tile_pitch_v と --photo・--h-box・--v-box と一緒に使います")
        return 2
    s = pitch_summary(a.building, "tile_pitch_h", reports)
    if s.get("value") is None:
        print(f"{a.building} に tile_pitch_h のメジャーの読みがありません（measure tape を先に）")
        return 1
    img = read_image(Path(a.ratio_from_h))
    lo, hi = a.px_range
    rh = uniform_period(img, axis="x", wall_box=a.h_box, lo=lo, hi=hi, rowwise=True)
    rv = uniform_period(img, axis="y", wall_box=a.v_box, lo=lo, hi=hi, rowwise=True)
    if not (rh.get("span") and rv.get("span")):
        print(f"周期が取れません（横 {rh.get('reason')}／縦 {rv.get('reason')}）")
        return 1
    q = {"photo": a.photo, "h_px": rh["span"]["px"], "n_h": rh["span"]["m"], "v_px": rv["span"]["px"], "n_v": rv["span"]["m"],
         "uniform_h": uniform_link(rh, photo=a.photo, face=a.face, what="tile_pitch_h"),
         "uniform_v": uniform_link(rv, photo=a.photo, face=a.face, what="tile_pitch_v")}
    m = build_chain(building=a.building, face=a.face, pitch=s, ratio_from_h=q)
    rs = rescale_record(m, _baseline(a.building, a.face, a.key, reports, Path(a.buildings) if a.buildings else None))
    scale = {"image": a.ratio_from_h, "h": {k: rh.get(k) for k in ("wall_box", "period_px", "px", "spread", "drift", "span")},
             "v": {k: rv.get(k) for k in ("wall_box", "period_px", "px", "spread", "drift", "span")}}
    p = write_chain(a.building, a.face, a.key, m, rescale=rs, scale=scale, reports=reports)
    kind = f"・{m['measured_kind']}" if m.get("measured_kind") else ""
    print(f"{a.building} {a.face} {a.key} = {m['value']} m ± {m['tol']}（{m['status']}{kind}）→ {p}")
    print(f"  横 {q['n_h']} 枚 = {q['h_px']} px（ばらつき {rh['spread'] * 100:.2f} %）、縦 {q['n_v']} 段 = {q['v_px']} px"
          f"（ばらつき {rv['spread'] * 100:.2f} %{'、drift ' + str(rv['drift']) if rv.get('drift') else ''}）")
    return 0


def _cli_chain(a, reports: Path | None) -> int:
    if a.ratio_from_h:
        return _cli_ratio_from_h(a, reports)
    src = a.pitch_from or a.building
    if a.pitch_from and a.pitch_from != a.building and pitch_summary(a.building, a.what, reports).get("value") is not None:
        print(f"{a.building} にメジャーの読みがあるので、別の棟（{a.pitch_from}）の周期は使いません")
        return 2
    s = pitch_summary(src, a.what, reports)
    if s.get("value") is None:
        print(f"{src} に {a.what} のメジャーの読みがありません（measure tape を先に。無ければ PLATEAU で estimated）")
        return 1
    count = None
    if a.count is not None:
        if not a.count_photo:
            print("--count には --count-photo（数えた写真 id）が要ります")
            return 2
        if a.count_photo2 and a.count_photo2 == a.count_photo:
            print("--count-photo2 は --count-photo と別の写真にします（同じ写真で 2 回数えても確かめにならない）")
            return 2
        count = {"photo": a.count_photo, "n": a.count, "what": a.count_what, "tol": a.count_tol}
        if a.count_photo2:
            count["confirmed_by"] = a.count_photo2
    uni, scale = None, None
    if a.uniform:
        u = json.loads(Path(a.uniform).read_text(encoding="utf-8"))
        uni = u.get("link")
        if uni and u.get("face") and u["face"] != a.face:
            print(f"{a.uniform} は面 {u['face']} の物です（{a.face} ではない）")
            return 2
        if u.get("period_px"):
            mpp = float(s["value"]) / float(u["period_px"])
            scale = {"image": u.get("image"), "period_px": u["period_px"], "m_per_px": round(mpp, 7),
                     "px_per_m": round(1 / mpp, 3), "what": f"{a.what} {s['value']} m ÷ {u['period_px']} px"}
        if uni is None:
            print(f"{a.uniform} は周期が一様の輪を出していません（{u.get('reason')}）→ estimated で進めます")
    ratios = []
    for i, r in enumerate(a.ratio or []):
        ph = a.ratio_photo[i] if a.ratio_photo and i < len(a.ratio_photo) else None
        if not ph:
            print("--ratio ごとに --ratio-photo が要ります")
            return 2
        wt = a.ratio_what[i] if a.ratio_what and i < len(a.ratio_what) else ""
        ratios.append({"photo": ph, "px": list(r), "what": wt})
    m = build_chain(building=a.building, face=a.face, pitch=s, count=count, uniform=uni, ratios=ratios)
    rs = rescale_record(m, _baseline(a.building, a.face, a.key, reports, Path(a.buildings) if a.buildings else None))
    p = write_chain(a.building, a.face, a.key, m, rescale=rs, scale=scale, reports=reports)
    kind = f"・{m['measured_kind']}" if m.get("measured_kind") else ""
    print(f"{a.building} {a.face} {a.key} = {m['value']} m ± {m['tol']}（{m['status']}{kind}）→ {p}")
    if rs:
        print(f"縮尺の付け替え：前の値 {rs['prev_value']} m（{rs['prev_status']}、{rs['baseline']}：{rs['ref']}）から {rs['diff_pct']:+.2f} %")
    return 0


def run_cli(a) -> int:
    reports = Path(a.reports) if getattr(a, "reports", None) else None
    try:
        if a.measure_cmd == "tape":
            ln = tape_link(a.photo, a.read, a.n, a.what)
            p = write_tape(ln, building=a.building, face=a.face, reports=reports, by=a.by)
            print(f"{a.photo}：{ln['read']} ÷ {ln['n']} 枚 = 周期 {ln['value']} m ± {ln['tol']} m → {p}")
            s = pitch_summary(a.building, a.what, reports)
            write_pitch_summary(s, reports)
            print(f"棟 {a.building} の {a.what}：{s['value']} m ± {s['tol']}（{s['photo']}）、検算 {len(s['checks'])} 枚")
            for w in s["warnings"]:
                print("警告：" + w)
            return 0
        if a.measure_cmd == "pitch":
            s = pitch_summary(a.building, a.what, reports)
            p = write_pitch_summary(s, reports)
            print(f"棟 {a.building} の {a.what}：{s['value']} m ± {s['tol']}（{s['photo']}）、検算 {len(s['checks'])} 枚 → {p}")
            for c in s["checks"]:
                print(f"  検算 {c['photo']}：{c['value']} m ± {c['tol']}、差 {c['diff_pct']:+.2f} %（{'± 以内' if c['ok'] else '± を超える'}）")
            for w in s["warnings"]:
                print("警告：" + w)
            if a.write_rulers and s["value"] is not None:
                r = write_ruler(s, Path(a.rulers) if a.rulers else None)
                print(f"物差し台帳 {r['id']} = {r['value']} m ± {r['tol']}")
            return 0 if s["value"] is not None else 1
        if a.measure_cmd == "uniform":
            return _cli_uniform(a)
        if a.measure_cmd == "chain":
            return _cli_chain(a, reports)
    except ValueError as e:
        print(f"読めません: {e}")
        return 2
    return 2
