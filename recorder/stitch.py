"""片端ずつ写した 2 枚の正面化画像を、横につないで 1 枚の正面図にする。

全部コード。やること：
    1. A・B それぞれで階高の周期（px）を measure と同じ方法で取り、B を拡縮して A の階高 px に揃える
    2. 縦のずれ：行ごとの明暗の並び（横方向の平均）を正規化相互相関で比べ、候補を 3 つまで出す
    3. 横のずれ：同じ階の帯（A の壁の中央 ±1 階）で、列ごとの明暗の並び（縦方向の平均）を作り、
       正規化相互相関で横のずれを求める。1 位と 2 位の山が近い（2 位 ≥ 0.9×1 位）時は「決まらない」
       （status="ambiguous"）と返し、人（数え係）に目印の対応を頼む（--pin "A:x_px=B:x_px"）
    4. つないだ画像で柱間隔・階高を数え直し、比（柱間隔÷階高）が A・B と一致するかを検算に書く

座標の約束：拡縮後の B の画素 (u, v) は、A の画素 (u + dx, v + dy) に重なる。
縮尺（m/px）はまだ無い。縮尺は measure の --known など既知の長さで別に決める。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

AMBIGUOUS_RATIO = 0.9      # 2 位 / 1 位 がこれ以上なら決まらない
SCALE_WARN = 0.02          # 階高のずれの警告（2%）
RATIO_TOL = 0.02           # 検算：柱間隔÷階高 の一致の目安（2%）
APERIODIC_MIN = 0.06       # 柱間ごとの違いがこれ未満なら「周期の模様しか無い」として決めない（合成で 周期だけ 0.03〜0.04、窓の明るさが違う壁 0.08〜0.12）
SMOOTH_PX = 2.5            # 並びを少しぼかす（拡縮の画素のずれで出る細かい模様を消す）


# --- 1 次元の並び ---------------------------------------------------------------

def _gray(img: np.ndarray) -> np.ndarray:
    import cv2

    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img


def _valid(gray: np.ndarray) -> np.ndarray:
    """正面化で写真の外になった真っ黒の部分を除く。"""
    return gray > 4


def _highpass(p: np.ndarray, k: int) -> np.ndarray:
    """ゆっくりした明るさの変化（日なたと日陰など）を除く。NaN は無視して平均する。"""
    k = max(3, min(int(k), len(p) - 1) | 1)
    if k >= len(p):
        k = max(1, (len(p) - 1) | 1) if len(p) > 1 else 1
    ok = ~np.isnan(p)
    v = np.where(ok, p, 0.0)
    ker = np.ones(k)
    s = np.convolve(v, ker, mode="same")
    n = np.convolve(ok.astype(float), ker, mode="same")
    base = s / np.maximum(n, 1)
    out = p - base
    out[~ok] = np.nan
    return out


def _smooth(p: np.ndarray, sigma: float) -> np.ndarray:
    """NaN を無視したガウスぼかし。"""
    r = int(3 * sigma)
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    ok = ~np.isnan(p)
    s = np.convolve(np.where(ok, p, 0.0), k, mode="same")
    n = np.convolve(ok.astype(float), k, mode="same")
    out = s / np.maximum(n, 1e-9)
    out[~ok] = np.nan
    return out


def col_profile(gray: np.ndarray, y0: int, y1: int, min_valid: float = 0.6) -> np.ndarray:
    """行 y0..y1 の帯で、列ごとの明暗の平均（縦方向の平均）。写真の外が多い列は NaN。"""
    band = gray[max(0, y0):max(0, y1)].astype(np.float64)
    if band.shape[0] == 0:
        return np.full(gray.shape[1], np.nan)
    m = _valid(band)
    n = m.sum(axis=0)
    p = (band * m).sum(axis=0) / np.maximum(n, 1)
    p[n < min_valid * band.shape[0]] = np.nan
    return p


def row_profile(gray: np.ndarray, x0: int, x1: int, min_valid: float = 0.6) -> np.ndarray:
    band = gray[:, max(0, x0):max(0, x1)].astype(np.float64)
    m = _valid(band)
    n = m.sum(axis=1)
    p = (band * m).sum(axis=1) / np.maximum(n, 1)
    p[n < min_valid * band.shape[1]] = np.nan
    return p


def box_period(p: np.ndarray, period: float) -> np.ndarray:
    """幅がちょうど柱 1 間の移動平均。どの柱間にも共通の模様（窓・柱の形）は一定値になって消え、
    柱間ごとの違い（窓の明るさ・汚れ・外階段・張り出しなど）だけが残る。
    周期の模様のまま比べると、柱 1 本ずれた所も同じくらい似てしまい、横のずれが決まらないため。
    （写真ごとに平均の型を作って引く方法は、A と B で写っている柱が違うと型も違い、うまく行かなかった）"""
    if p.ndim == 2:
        return np.array([box_period(r, period) for r in p])
    ok = ~np.isnan(p)
    x = np.arange(len(p), dtype=np.float64)
    c = np.r_[0.0, np.cumsum(np.where(ok, p, 0.0))]
    n = np.r_[0.0, np.cumsum(ok.astype(float))]
    xe = np.arange(len(p) + 1, dtype=np.float64)       # 累積和の境目
    lo, hi = np.clip(x + 0.5 - period / 2, 0, len(p)), np.clip(x + 0.5 + period / 2, 0, len(p))
    s = np.interp(hi, xe, c) - np.interp(lo, xe, c)
    m = np.interp(hi, xe, n) - np.interp(lo, xe, n)
    out = s / np.maximum(m, 1e-9)
    out[(m < 0.8 * period) | ~ok] = np.nan             # 柱 1 間が丸ごと入らない端は使わない
    return out


def ncc_shifts(a: np.ndarray, b: np.ndarray, shifts: np.ndarray, min_overlap: int, return_n: bool = False):
    """b を shift だけずらして a と重ねた時の正規化相互相関（重なった部分だけ、NaN は除く）。
    b[u] は a[u + shift] に重なる。重なりが min_overlap 未満なら NaN。
    a・b は 1 次元か、(階の数, 列) の 2 次元（階ごとの並びを全部まとめて 1 つの相関にする）。"""
    out = np.full(len(shifts), np.nan)
    ncols = np.zeros(len(shifts))
    a, b = np.atleast_2d(a), np.atleast_2d(b)
    na, nb = a.shape[1], b.shape[1]
    for k, s in enumerate(shifts):
        s = int(s)
        a0, a1 = max(0, s), min(na, nb + s)
        if a1 - a0 < min_overlap:
            continue
        x = a[:, a0:a1].ravel()
        y = b[:, a0 - s:a1 - s].ravel()
        ok = ~(np.isnan(x) | np.isnan(y))
        if ok.sum() < min_overlap:
            continue
        x, y = x[ok] - x[ok].mean(), y[ok] - y[ok].mean()
        d = np.sqrt((x * x).sum() * (y * y).sum())
        if d > 1e-9:
            out[k] = float((x * y).sum() / d)
            ncols[k] = ok.sum() / a.shape[0]
    return (out, ncols) if return_n else out


def significance(r: np.ndarray, ncols: np.ndarray, unit_px: float, rows: int) -> np.ndarray:
    """相関の確からしさ（Fisher の z × √(独立な標本数−3)）。重なりが狭いと偶然高い相関が出やすいので、
    相関の値だけでなく、重なった柱間の数（× 階の数）で重みを付けて順位を決める。"""
    n_eff = ncols / max(unit_px, 1.0) * rows
    z = np.arctanh(np.clip(r, -0.999, 0.999)) * np.sqrt(np.maximum(n_eff - 3, 1.0))
    z[np.isnan(r)] = np.nan
    return z


def local_peaks(score: np.ndarray, min_sep: int) -> list[int]:
    """局所最大を強い順に。互いに min_sep 未満の山は 1 つにまとめる（同じ山の裾を 2 位にしない）。"""
    s = np.where(np.isnan(score), -np.inf, score)
    idx = [i for i in range(len(s)) if np.isfinite(s[i])
           and (i == 0 or s[i] >= s[i - 1]) and (i == len(s) - 1 or s[i] >= s[i + 1])]
    idx.sort(key=lambda i: -s[i])
    kept: list[int] = []
    for i in idx:
        if all(abs(i - j) >= min_sep for j in kept):
            kept.append(i)
    return kept


def _subpix(score: np.ndarray, i: int) -> float:
    if 0 < i < len(score) - 1 and not np.isnan(score[i - 1:i + 2]).any():
        y0, y1, y2 = score[i - 1], score[i], score[i + 1]
        d = y0 - 2 * y1 + y2
        if abs(d) > 1e-12:
            return float(np.clip(0.5 * (y0 - y2) / d, -0.5, 0.5))
    return 0.0


# --- 本体 ---------------------------------------------------------------------

def _sub_harmonic(img: np.ndarray, wb, target: float, axis: str) -> tuple[float, float] | None:
    """自己相関で target（px）の ±6% に山があるか。あれば (周期 px, 強さ)。measure と同じ並びを使う。"""
    from .measure import _period, _profile

    g = _gray(img)
    x0, y0, x1, y1 = [int(round(v)) for v in wb]
    crop = g[max(0, y0):y1, max(0, x0):x1]
    prof = _profile(crop, axis, signed=True)
    lo, hi = int(target * 0.94), int(target * 1.06) + 1
    per, strength = _period(prof, lo, hi)
    if per is None or strength < 0.05 or not (lo + 1 <= per <= hi - 2):
        return None
    return float(per), float(strength)


def _near(img: np.ndarray, wb, target: float, axis: str) -> tuple[float, float] | None:
    """target（px）の周期を、target・2×・3× の山のうち一番強い物から求める（÷倍数）。
    1 周期の山が弱い壁（階ごとに見た目が交互に変わるなど）でも、長い周期の山で正確に測るため。"""
    best = None
    x0, y0, x1, y1 = wb
    length = (x1 - x0) if axis == "x" else (y1 - y0)
    for m in (1, 2, 3):
        if target * m * 1.06 >= length / 2:
            break
        r = _sub_harmonic(img, wb, target * m, axis)
        if r and (best is None or r[1] > best[1]):
            best = (r[0] / m, r[1])
    return best


def _fundamental(img: np.ndarray, wb, period: float | None, strength: float, axis: str) -> tuple[float | None, list[str]]:
    """自己相関の一番強い山が、本当の繰り返しの 2 倍・3 倍（2 階分・2 柱間分）を拾っていることがある。
    1/2・1/3 の位置にも山（強さ 0.1 以上かつ元の 0.5 倍以上）があれば、短い方を 1 単位とする。直した記録も返す。"""
    notes: list[str] = []
    if not period:
        return period, notes
    for _ in range(3):
        for k in (2, 3):
            sub = _sub_harmonic(img, wb, period / k, axis)
            if sub and sub[0] >= 8 and sub[1] >= max(0.1, 0.5 * strength):
                # 値は「長い周期 ÷ k」を使う（長い方の山は何周期分も平均していて位置が正確。短い方の山は弱くずれやすい）
                notes.append(f"{period:.1f} px（強さ {strength:.2f}）の 1/{k} にも山（{sub[0]:.1f} px、強さ {sub[1]:.2f}）"
                             f"→ {period / k:.1f} px")
                period, strength = period / k, sub[1]
                break
        else:
            break
    return period, notes


def _parse_pin(pin: str) -> tuple[float, float]:
    """"A:1234=B:56" / "1234=56" → (A の x, B の x)。B は拡縮前の B の画素。"""
    a, b = pin.split("=")
    a = a.split(":")[-1].strip().removeprefix("x_px").strip()
    b = b.split(":")[-1].strip().removeprefix("x_px").strip()
    return float(a), float(b)


def stitch(img_a: np.ndarray, img_b: np.ndarray, wb_a=None, wb_b=None,
           hint: str | None = None, pin: str | None = None, min_overlap_frac: float = 0.15,
           band: tuple[float, float] | None = None):
    """戻り値 (つないだ画像 | None, 結果 dict)。

    hint: "右"（B は A の右側）か "左"。無ければ両方探す。
    pin: "A:x_px=B:x_px"（拡縮前の B の画素）。人が目印の対応を 1 組与えた時。
    band: 比べる行の範囲（A の画素 y0, y1）。木などで隠れた階を外したい時に人が指定する。"""
    import cv2

    from .measure import measure

    res: dict = {"status": "failed", "reason": "", "warnings": [], "method": {
        "scale": "階高の周期（measure と同じ自己相関）を A に揃える",
        "dy": "行ごとの明暗の並びの正規化相互相関（候補 3 つ）",
        "dx": "A の壁の中央 ±1 階の帯で、列ごとの明暗の並びの正規化相互相関。粗くは柱 1 間の移動平均（周期の模様を消した物）で、細かくは元の並びで ±半柱間",
        "rank": "相関を Fisher の z にし、重なった柱間の数×階の数で重みを付けた確からしさ（score）。狭い重なりの偶然の高相関を下げる",
        "ambiguous_if": f"score の 2 位 / 1 位 ≥ {AMBIGUOUS_RATIO}、または柱間ごとの違い < {APERIODIC_MIN}",
    }}
    ha, wa = img_a.shape[:2]
    hb, wb = img_b.shape[:2]
    wb_a = [float(v) for v in (wb_a or [0, 0, wa, ha])]
    wb_b = [float(v) for v in (wb_b or [0, 0, wb, hb])]

    # 1. 縦の縮尺を揃える
    ma, mb = measure(img_a, wb_a), measure(img_b, wb_b)
    fa, fb = ma.floors.period_px if ma.floors else None, mb.floors.period_px if mb.floors else None
    res["A"] = {"px_per_floor": fa, "floor_strength": ma.floors.strength if ma.floors else None,
                "bay_px": ma.bays.period_px if ma.bays else None, "ratio_bay_over_floor": ma.ratio_bay_over_floor,
                "wall_box": wb_a, "size": [wa, ha]}
    res["B"] = {"px_per_floor": fb, "floor_strength": mb.floors.strength if mb.floors else None,
                "bay_px": mb.bays.period_px if mb.bays else None, "ratio_bay_over_floor": mb.ratio_bay_over_floor,
                "wall_box": wb_b, "size": [wb, hb]}
    if not fa or not fb:
        res["reason"] = "階高の周期が取れません（どちらかの壁の範囲に階の繰り返しが写っていない）"
        return None, res
    # 2 階分（倍の周期）を拾うことがある（実写の図書館北面で起きた）。1/2・1/3 に山があれば 1 階分に直す
    for key, img_, wb_, mr in (("A", img_a, wb_a, ma), ("B", img_b, wb_b, mb)):
        f0 = res[key]["px_per_floor"]
        f1, notes = _fundamental(img_, wb_, f0, mr.floors.strength, "y")
        b0 = mr.bays.period_px if mr.bays else None
        b1, bnotes = _fundamental(img_, wb_, b0, mr.bays.strength if mr.bays else 0, "x")
        if notes:
            res[key]["px_per_floor_corrected"] = round(f1, 1)
            res["warnings"].append(f"{key} の階高を直しました：" + "、".join(notes))
        if bnotes:
            res[key]["bay_px_corrected"] = round(b1, 1)
            res["warnings"].append(f"{key} の柱間隔を直しました：" + "、".join(bnotes))
        res[key]["_f"], res[key]["_b"] = f1, b1
    fa, fb = res["A"].pop("_f"), res["B"].pop("_f")
    bay_a0, bay_b0 = res["A"].pop("_b"), res["B"].pop("_b")
    r2 = fa / fb if fa >= fb else fb / fa
    if r2 > 1.5:
        res["warnings"].append(f"A と B の階高の px が {r2:.2f} 倍違います。撮った距離がかなり違うか、どちらかの階高が誤りです")
    res["A"]["ratio_bay_over_floor"] = round(bay_a0 / fa, 3) if bay_a0 else None
    res["B"]["ratio_bay_over_floor"] = round(bay_b0 / fb, 3) if bay_b0 else None
    s = fa / fb
    res["scale_b"] = round(s, 5)
    imb = cv2.resize(img_b, (max(1, int(round(wb * s))), max(1, int(round(hb * s)))),
                     interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
    wbb = [v * s for v in wb_b]
    sub_b = _near(imb, wbb, fa, "y")                   # 拡縮後の B で、A の階高の ±6% に山があるか
    fb2 = round(sub_b[0], 1) if sub_b else None
    res["B"]["px_per_floor_after_scale"] = fb2
    if fb2:
        dev = abs(fb2 / fa - 1)
        res["floor_match_after_scale"] = round(dev, 4)
        if dev >= SCALE_WARN:
            res["warnings"].append(f"拡縮後も階高が {dev:.1%} ずれます（2% 以上）。正面化の傾き・焦点距離を確かめてください")
    else:
        res["warnings"].append("拡縮後の B で、A の階高の近くに繰り返しが見つかりません")
    ga, gb = _gray(img_a), _gray(imb)
    hb2, wb2 = gb.shape

    # 2. 縦のずれの候補
    pa = _highpass(row_profile(ga, int(wb_a[0]), int(wb_a[2])), 4 * fa)
    pb = _highpass(row_profile(gb, int(wbb[0]), int(wbb[2])), 4 * fa)
    # 壁どうしが縦に半分以上重なるずれだけを探す（屋上と地面がずれた答えを除く）
    hwa, hwb = wb_a[3] - wb_a[1], wbb[3] - wbb[1]
    need = 0.5 * min(hwa, hwb)
    dys = np.arange(int(np.floor(wb_a[1] - wbb[3] + need)), int(np.ceil(wb_a[3] - wbb[1] - need)) + 1)
    sc_y = ncc_shifts(pa, pb, dys, min_overlap=int(0.4 * min(ha, hb2)))
    pk_y = local_peaks(sc_y, max(3, int(0.3 * fa)))
    # 縦は 1 位に近い候補（0.9 倍以上）だけ横の探索で比べる。明らかに劣る縦のずれは使わない
    dy_cands = [int(dys[i]) for i in pk_y[:3] if sc_y[i] >= AMBIGUOUS_RATIO * sc_y[pk_y[0]]]
    if not dy_cands:
        res["reason"] = "縦の重なりが見つかりません"
        return None, res
    res["dy_candidates"] = [{"dy": int(dys[i]), "ncc": round(float(sc_y[i]), 4)} for i in pk_y[:3]]

    # 3. 横のずれ（帯：A と B の壁が縦に重なる範囲を 1 階ずつに分け、階ごとの並びをまとめて比べる）
    bay_a, bay_b = bay_a0, (bay_b0 * s if bay_b0 else None)
    bay = 0.5 * (bay_a + bay_b) if bay_a and bay_b and abs(bay_a / bay_b - 1) < 0.05 else None
    res["periodic_removed_bay_px"] = round(bay, 2) if bay else None
    if not bay:
        res["warnings"].append("柱の周期が A・B でそろわないので、周期の模様を引かずに比べます（決まらないになりやすい）")

    def prep(p):
        return _smooth(_highpass(p, 2 * fa), SMOOTH_PX)

    def floor_bands(dy):
        """A の行で、両方の壁に入る範囲を 1 階ずつの帯に分ける。band（人の指定）があればその中だけ。"""
        y0 = max(wb_a[1], wbb[1] + dy, 0, dy)
        y1 = min(wb_a[3], wbb[3] + dy, ha, hb2 + dy)
        if band:
            y0, y1 = max(y0, band[0]), min(y1, band[1])
        n = int((y1 - y0) // fa)
        if n < 1:
            return [(int(y0), int(y1))] if y1 - y0 >= 10 else []
        return [(int(y0 + k * fa), int(y0 + (k + 1) * fa)) for k in range(n)]

    # 重なりは最低でも柱 2 本分。1 柱間だけの重なりは窓 1 つの明るさで決まってしまい、当てにならない
    min_ov = int(max(min_overlap_frac * min(wa, wb2), 2 * bay if bay else 0))
    res["min_overlap_px"] = min_ov
    pin_ab = _parse_pin(pin) if pin else None
    if pin_ab:
        dx0 = pin_ab[0] - pin_ab[1] * s
        # 人の読み取りの誤差を詰める範囲：柱間の 1/4（隣の柱に飛ばないように）
        win = max(15, int(0.25 * bay)) if bay else max(15, int(0.02 * min(wa, wb2)))
        dxs = np.arange(int(dx0) - win, int(dx0) + win + 1)
        res["pin"] = {"A_x_px": pin_ab[0], "B_x_px": pin_ab[1], "dx_from_pin": round(dx0, 2), "refine_window_px": win}
    else:
        lo, hi = -wb2 + min_ov, wa - min_ov
        if hint == "右":
            lo = max(lo, 0)
        elif hint == "左":
            hi = min(hi, 0)
        dxs = np.arange(lo, hi + 1)

    # 粗く：柱 1 間の移動平均（周期の模様を消した物）で、どの柱に合わせるかを決める（ピン無しの時）
    # 細かく：元の並びで、粗い答えの ±半柱間 だけを探して小数画素まで詰める
    best = None
    for dy in dy_cands:
        bands = floor_bands(dy)
        if not bands:
            continue
        prof_a = np.array([prep(col_profile(ga, y0, y1)) for y0, y1 in bands])
        prof_b = np.array([prep(col_profile(gb, y0 - dy, y1 - dy)) for y0, y1 in bands])
        frac = None
        if bay and not pin_ab:
            ba, bb = box_period(prof_a, bay), box_period(prof_b, bay)
            # 柱間ごとの違いの強さ（元の並びに対する割合）。小さい＝周期の模様しか無く、どの柱に合わせても同じ
            frac = [float(np.nanstd(ba) / max(np.nanstd(prof_a), 1e-9)), float(np.nanstd(bb) / max(np.nanstd(prof_b), 1e-9))]
            r, nc = ncc_shifts(ba, bb, dxs, max(10, min_ov - int(bay)), return_n=True)
            sc = significance(r, nc, bay, len(bands))
            pk = local_peaks(sc, max(3, int(0.5 * bay)))
        else:
            r, nc = ncc_shifts(prof_a, prof_b, dxs, min_ov, return_n=True)
            sc = significance(r, nc, bay or fa, len(bands))
            pk = local_peaks(sc, max(3, int(0.15 * fa)))
        if not pk:
            continue
        if best is None or sc[pk[0]] > best["sc"][best["pk"][0]]:
            best = {"dy": dy, "sc": sc, "r": r, "pk": pk, "frac": frac, "prof_a": prof_a, "prof_b": prof_b, "bands": bands}
    if best is None:
        res["reason"] = "同じ階の帯が B に入りません（縦のずれが大きすぎるか、B の壁が低い）"
        return None, res
    sc, pk, rr = best["sc"], best["pk"], best["r"]
    i1 = pk[0]
    n1 = float(sc[i1])
    n2 = float(sc[pk[1]]) if len(pk) > 1 else None
    ratio = (n2 / n1) if (n2 is not None and n1 > 0) else None
    res["score"] = round(n1, 3)
    res["score_second"] = round(n2, 3) if n2 is not None else None
    coarse = float(dxs[i1])
    # 細かく
    half = int(0.5 * bay) if (bay and not pin_ab) else 0
    if half:
        fx = np.arange(int(coarse) - half, int(coarse) + half + 1)
        fsc = ncc_shifts(best["prof_a"], best["prof_b"], fx, min_ov)
        j = int(np.nanargmax(fsc))
        dx = float(fx[j]) + _subpix(fsc, j)
        res["ncc_fine"] = round(float(fsc[j]), 4)
    else:
        dx = coarse + _subpix(sc, i1)
    if pin_ab:
        a_ok = ~np.isnan(sc)
        edge = i1 in (int(np.argmax(a_ok)), len(sc) - 1 - int(np.argmax(a_ok[::-1])))
        res["pin"]["refined_dx"] = round(dx, 2)
        if edge:
            # 詰めた答えが探す範囲の端＝山が範囲内に無い。人の対応点をそのまま使う
            res["warnings"].append(f"ピンの近くに相関の山がありません（端 {dx:.0f} px）。ピンの値 {dx0:.1f} px をそのまま使います")
            dx = dx0
            res["pin"]["used"] = "pin_as_is"
        else:
            res["pin"]["used"] = "refined"
        ratio = None
    res["bands_a_rows"] = [list(b) for b in best["bands"]]
    res["aperiodic_fraction"] = [round(v, 3) for v in best["frac"]] if best["frac"] else None
    r1 = float(rr[i1])
    r2_ = float(rr[pk[1]]) if len(pk) > 1 else None
    res.update({"dx_px": round(dx, 2), "dy_px": best["dy"], "ncc": round(r1, 4),
                "ncc_second": round(r2_, 4) if r2_ is not None else None,
                "second_over_first": round(ratio, 4) if ratio is not None else None,
                "score_kind": "柱1間の移動平均の相関" if (bay and not pin_ab) else "元の並びの相関",
                "dx_candidates": [{"dx": int(dxs[i]), "ncc": round(float(rr[i]), 4), "score": round(float(sc[i]), 3)} for i in pk[:5]]})
    if r1 < 0.3:
        res["warnings"].append(f"相関が弱い（{r1:.2f}）。重なりが少ないか、帯に木などが写っています")
    if bay and not pin_ab:
        # 参考：周期の模様を消さない元の並びの相関で、1 位と 2 位（柱 1 本ずれ）の比。周期的な壁では 1 に近い
        rsc = ncc_shifts(best["prof_a"], best["prof_b"], dxs, min_ov)
        rpk = local_peaks(rsc, max(3, int(0.5 * bay)))
        if len(rpk) > 1 and rsc[rpk[0]] > 0:
            res["raw"] = {"dx": int(dxs[rpk[0]]), "ncc": round(float(rsc[rpk[0]]), 4),
                          "second_over_first": round(float(rsc[rpk[1]] / rsc[rpk[0]]), 4)}
    fr = best["frac"]
    if fr and min(fr) < APERIODIC_MIN and not pin_ab:
        res["status"] = "ambiguous"
        res["reason"] = (f"柱間ごとの違いがほとんど無く（{min(fr):.3f} < {APERIODIC_MIN}）、どの柱に合わせても同じに見えます。"
                         "両方の画像で同じ目印（外階段の端・角の柱など）の x を読み、--pin \"A:x_px=B:x_px\" で与えてください")
        return None, res
    # 決まらない条件は 2 つ：確からしさの 2 位が 1 位の 0.9 倍以上、または相関そのものが 1 位の 0.9 倍以上で
    # 偶然とは言えない（確からしさ ≥ 3）別の候補がある（重なりの広さの違いだけで決めつけないため）
    rivals = [i for i in pk[1:] if rr[i] >= AMBIGUOUS_RATIO * r1 and sc[i] >= 3.0]
    res["ncc_rivals"] = [{"dx": int(dxs[i]), "ncc": round(float(rr[i]), 4), "score": round(float(sc[i]), 3)} for i in rivals]
    if not pin_ab and ((ratio is not None and ratio >= AMBIGUOUS_RATIO) or rivals):
        res["status"] = "ambiguous"
        alt = rivals[0] if rivals else pk[1]
        res["reason"] = (f"横のずれが 1 つに決まりません（1 位 dx={int(dxs[i1])} 相関 {r1:.3f}・確からしさ {n1:.2f}、"
                         f"対抗 dx={int(dxs[alt])} 相関 {rr[alt]:.3f}・確からしさ {sc[alt]:.2f}）。"
                         "両方の画像で同じ目印（外階段の端・角の柱など）の x を読み、--pin \"A:x_px=B:x_px\" で与えてください")
        return None, res

    # 4. つなぐ
    dy = best["dy"]
    ox, oy = -min(0.0, dx), -min(0, dy)
    W = int(np.ceil(max(wa, dx + wb2) + ox))
    Hh = int(max(ha, dy + hb2) + oy)
    Ma = np.float32([[1, 0, ox], [0, 1, oy]])
    Mb = np.float32([[1, 0, dx + ox], [0, 1, dy + oy]])
    ca = cv2.warpAffine(img_a, Ma, (W, Hh), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
    cb = cv2.warpAffine(imb, Mb, (W, Hh), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
    va, vb = _valid(_gray(ca)), _valid(_gray(cb))
    out = np.where(va[..., None], ca, cb)
    both = va & vb
    if both.any():
        cols = np.where(both.any(axis=0))[0]
        c0, c1 = int(cols.min()), int(cols.max()) + 1
        x = np.arange(c0, c1, dtype=np.float32)
        t = (x - c0) / max(1, c1 - c0 - 1)          # 0→1 で A から B へ
        a_left = ox < dx + ox                        # A が左
        wa_ = (1 - t) if a_left else t
        seg_a, seg_b = ca[:, c0:c1].astype(np.float32), cb[:, c0:c1].astype(np.float32)
        mix = seg_a * wa_[None, :, None] + seg_b * (1 - wa_[None, :, None])
        sub = out[:, c0:c1]
        bm = both[:, c0:c1]
        sub[bm] = np.clip(mix[bm], 0, 255).astype(np.uint8)
        out[:, c0:c1] = sub
        res["overlap_cols"] = [c0, c1]
    res["canvas"] = {"size": [W, Hh], "A_offset": [round(ox, 2), oy], "B_offset": [round(dx + ox, 2), dy + oy]}

    # 5. 検算：つないだ画像で柱間隔・階高を数え直す
    wbs = [min(wb_a[0] + ox, wbb[0] + dx + ox), min(wb_a[1] + oy, wbb[1] + dy + oy),
           max(wb_a[2] + ox, wbb[2] + dx + ox), max(wb_a[3] + oy, wbb[3] + dy + oy)]
    # 期待する周期（A の柱間隔・階高）の ±6% で山を探す。2 倍・半分を拾って比が狂うのを避ける
    sb = _near(out, wbs, bay, "x") if bay else None
    sf = _near(out, wbs, fa, "y")
    rs = round(sb[0] / sf[0], 3) if sb and sf else None
    chk = {"wall_box": [round(v, 1) for v in wbs],
           "bay_px": round(sb[0], 1) if sb else None, "bay_strength": round(sb[1], 3) if sb else None,
           "floor_px": round(sf[0], 1) if sf else None, "floor_strength": round(sf[1], 3) if sf else None,
           "ratio_stitched": rs, "ratio_A": res["A"]["ratio_bay_over_floor"], "ratio_B": res["B"]["ratio_bay_over_floor"]}
    rats = [r for r in (res["A"]["ratio_bay_over_floor"], res["B"]["ratio_bay_over_floor"], rs) if r]
    if len(rats) >= 2:
        spread = (max(rats) - min(rats)) / float(np.median(rats))
        chk["ratio_spread"] = round(spread, 4)
        chk["ratio_agree"] = bool(spread < RATIO_TOL)
        if not chk["ratio_agree"]:
            res["warnings"].append(f"柱間隔÷階高 が A・B・つないだ画像で {spread:.1%} ずれます（目安 {RATIO_TOL:.0%}）。"
                                   "片方が倍の周期を拾ったか、柱が隠れています。画像で確かめてください")
    else:
        chk["ratio_agree"] = None
    res["check"] = chk
    if chk["ratio_agree"] is False or not sb:
        # 検算が合わない（または柱が数えられない）時は ok にしない。画像は人が見るために残す
        res["status"] = "ambiguous"
        res["reason"] = ("つないだ画像の検算が合いません（柱間隔÷階高 が A・B と一致しない、または柱の周期が取れない）。"
                         "画像を見て、目印の対応を --pin で与えるか、--band で木などを外してください")
        return out, res
    res["status"] = "ok"
    return out, res


def stitch_files(a_jpg: Path, b_jpg: Path, out_jpg: Path | None = None,
                 hint: str | None = None, pin: str | None = None,
                 band: tuple[float, float] | None = None) -> tuple[dict, Path]:
    import cv2

    from .rectify import read_image

    def wall_box(p: Path):
        j = p.with_suffix(".json")
        if j.exists():
            return j, json.loads(j.read_text(encoding="utf-8")).get("wall_box")
        return None, None

    ja, wa = wall_box(a_jpg)
    jb, wb = wall_box(b_jpg)
    out, res = stitch(read_image(a_jpg), read_image(b_jpg), wa, wb, hint=hint, pin=pin, band=band)
    if out_jpg is None:
        sa = a_jpg.stem.removesuffix("_front")
        sb = b_jpg.stem.removesuffix("_front")
        out_jpg = Path("out/rectified") / f"{sa}+{sb}_stitched.jpg"
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    res.setdefault("A", {})
    res.setdefault("B", {})
    res["A"].update({"front_jpg": str(a_jpg), "front_json": str(ja) if ja else None})
    res["B"].update({"front_jpg": str(b_jpg), "front_json": str(jb) if jb else None})
    res["hint"] = hint
    if out is not None:
        _, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 92])
        out_jpg.write_bytes(buf.tobytes())
        res["image"] = str(out_jpg)
    else:
        res["image"] = None
    out_json = out_jpg.with_suffix(".json")
    out_json.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res, out_json
