"""検収：正面化した写真と、棟 JSON の面（立面）を m 単位で重ね、ずれを数字で出す。

写真は壁の平面に正面化してあるので（rectify.py）、「画素→m」と「原点」さえ決まれば
JSON の線（柱型の縁・窓の上下・ピロティ天井・パラペット）を写真の上に描ける。
ずれは線ごとに m で出す。判断役（検収係 Claude か人）はこの数字を見て JSON を直す。

決め方
    画素→m      写真で検出した柱間隔の周期（px） ÷ JSON の柱間隔（m）。
                 JSON の柱間隔が写真由来なら循環するので、別の写真で検算する（1 枚から書いた JSON を残りの写真で検める）
    原点（横）   JSON の柱型の縁の位置の並びを、検出した縦エッジの並びに一番よく重なる位置にずらす
    原点（縦）   JSON の高さの線の並びを、検出した横エッジの並びに一番よく重なる位置にずらす
    ずれ         JSON の線ごとに、一番近い検出エッジまでの距離（m）。近くに無ければ「見つからず」

出てくる物
    <stem>_<face>_compare.json   線ごとの ずれ、画素→m、階高の比の検算、合否
    <stem>_<face>_compare.png    写真に JSON の線（青）と検出エッジ（赤）、ずれの数字を描いた物
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .measure import _peaks, _period, _profile, measure
from .schema import Measurement, bay_edges


@dataclass
class LineCheck:
    axis: str          # "x"（横の位置、面に沿った m）か "z"（高さ m）
    what: str          # 何の線か
    json_m: float      # JSON の位置（m）
    photo_m: float | None   # 写真で一番近いエッジの位置（m）
    diff_m: float | None    # 写真 − JSON（m）
    ok: bool | None         # |diff| ≤ tol なら True、見つからなければ None
    auto: bool = True       # 結んだ検出エッジが道具の自動検出なら True、判断役が --lines で与えた位置なら False
    kind: str = "periodic"  # periodic（柱）/ landmark（面の端）/ part（部品・塊の縁）/ level（高さの線）
    in_plane: bool = True   # 面の平面上の線か。False（手前に出た踊り場など）は縮尺が違うので参考値で、合否の件数に数えない
    depth_m: float = 0.0


@dataclass
class CompareResult:
    ok: bool
    reason: str = ""
    face: str = ""
    source: str = ""
    px_per_m: float | None = None
    x_origin_px: float | None = None
    z_origin_px: float | None = None     # 地面（z=0）の行
    floor_pitch_json_m: float | None = None
    floor_pitch_photo_m: float | None = None
    lines: list[LineCheck] = field(default_factory=list)
    tol_m: float = 0.15
    n_ok: int = 0
    n_ng: int = 0
    n_missing: int = 0
    worst_m: float | None = None
    candidates: dict = field(default_factory=dict)   # {"x": [{"px", "strength", "auto"}...], "z": [...]} 強い順の上位 N 本
    valid_ratio: float | None = None                 # 正面化画像のうち有効な画素の割合（黒い余白を除く）
    ground_offset_m: float = 0.0                     # 面の前の地面の高さ（faces[].ground_offset。z=0 からの m）
    origin_candidates: list[dict] = field(default_factory=list)   # 横の原点の候補（score の高い順）。{origin_px, score, ...}
    ambiguous: bool = False                          # 1 位と 2 位の差が小さい（柱の繰り返しで何本目か決められない）
    warnings: list[str] = field(default_factory=list)
    n_lines_off_plane: int = 0

    def to_json(self) -> dict:
        return asdict(self)


def _v(m) -> float | None:
    if isinstance(m, (int, float)):
        return float(m)
    mm = Measurement.from_json(m)
    return float(mm.value) if mm.known else None


SKY_BLUE_MARGIN = 25       # 青が赤よりこれ以上強ければ空と見なす（0〜255）
PLANE_TOL_M = 0.5          # 面の平面から この距離以内の縁は「面の上」（正面化の縮尺がそのまま使える）
OFF_PLANE_MAX_M = 20.0     # これより手前に出ている物は線の候補にしない
AMBIGUOUS_DIFF = 0.75      # 横の原点の 1 位と 2 位の点の差がこれ未満なら ambiguous


@dataclass
class FaceLine:
    axis: str                 # "x" か "z"
    name: str
    m: float                  # 面に沿った m（x）または高さ m（z）
    kind: str = "periodic"    # periodic（柱の繰り返し）/ landmark（面の端）/ part（部品・塊の縁）/ level（高さの線）
    weight: float = 1.0       # 横の原点の順位付けでの重み。0 は順位付けに使わない（比べるだけ）
    in_plane: bool = True     # 面の平面上にあるか。手前に出ている物（踊り場の手すりなど）は縮尺が違うので False
    depth_m: float = 0.0      # 面からの出（m）


def _face_frame(f: dict):
    if not (f.get("from") and f.get("to")):
        return None
    p0 = np.array(f["from"], float)
    d = np.array(f["to"], float) - p0
    Lg = float(np.hypot(*d))
    if Lg <= 0:
        return None
    u = d / Lg
    return p0, u, np.array([u[1], -u[0]]), Lg


def _short(s: str, n: int = 10) -> str:
    return (s or "").split("（")[0][:n]


def face_lines_detail(b: dict, face_id: str) -> tuple[list[FaceLine], list[FaceLine], dict]:
    """JSON の面から、横の線（x、面の始点からの m）と高さの線（z、m）を、種類つきで列挙する。

    柱割り（bays）が無い面でも、面の両端・パラペット・帯・塊と部品（階段塔・箱・外階段の踊り場）の縁が線の候補になる。
    部品・塊は、面の平面から PLANE_TOL_M 以内の縁を in_plane（縮尺がそのまま使える）、手前に出ている縁を
    off-plane（depth_m つき。比べるが原点の順位付けには使わない）として扱う。"""
    f = next((f for f in b.get("faces", []) if f["id"] == face_id), None)
    if f is None:
        raise KeyError(f"面 {face_id} がありません")
    xs: list[FaceLine] = []
    zs: list[FaceLine] = []
    bays = f.get("bays") or {}
    pier = _v(bays.get("pier_width")) or 0.0
    length = _v(f.get("length"))
    fr = _face_frame(f)
    if length is None and fr:
        length = fr[3]
    edges, _w = bay_edges(bays, length)     # margin_from / margin_to / end_margin の規則は drawing.py と共通
    for i, c in enumerate(edges):
        if pier > 0:
            xs.append(FaceLine("x", f"柱型{i}左", c - pier / 2))
            xs.append(FaceLine("x", f"柱型{i}右", c + pier / 2))
        else:
            xs.append(FaceLine("x", f"柱線{i}", c))
    if length:
        # 面の端が柱型の中に入る（端の柱の中心が端にある）時は、端そのものの縁は写らない。重みを下げる
        def end_w(s_: float) -> float:
            return 0.25 if any(abs(c - s_) <= pier / 2 + 0.05 for c in edges) and pier > 0 else 1.0
        xs.append(FaceLine("x", "面の端0", 0.0, "landmark", end_w(0.0)))
        xs.append(FaceLine("x", "面の端L", float(length), "landmark", end_w(float(length))))
    ground = _v(f.get("ground_offset")) or 0.0
    if "ground_offset" in f and ground:
        zs.append(FaceLine("z", "地面（面の前）", ground, "level"))
    for w in f.get("windows") or []:
        zb, zt = _v(w.get("z_bottom")), _v(w.get("z_top"))
        if zb is not None:
            zs.append(FaceLine("z", f"{w.get('name', '窓')} 下端", zb, "level"))
        if zt is not None:
            zs.append(FaceLine("z", f"{w.get('name', '窓')} 上端", zt, "level"))
    for bd in f.get("bands") or []:
        for key, nm in (("z_bottom", "下端"), ("z_top", "上端")):
            z = _v(bd.get(key))
            if z is not None:
                zs.append(FaceLine("z", f"{bd.get('name', '帯')} {nm}", z, "level"))
    for op in f.get("openings") or []:
        zt = _v(op.get("z_top"))
        if zt is not None:
            zs.append(FaceLine("z", f"{op.get('name', '開口')} 天井", zt, "level"))
    for lg in f.get("ledges") or []:
        z = _v(lg.get("z"))
        if z is not None:
            zs.append(FaceLine("z", f"{lg.get('name', '段')}", z, "level"))
    top = _v(f.get("top_z")) or _v(b.get("parapet_top"))
    if top is not None:
        zs.append(FaceLine("z", "パラペット上端", top, "level"))

    tall: list[dict] = []       # 面の上端より高く立つ部品（階段塔など）。空に突き出る輪郭が、横の位置の手がかりになる
    if fr:
        p0, u, n, Lg = fr
        Lf = float(length) if length else Lg

        def proj(P):
            q = np.array(P, float) - p0
            return float(q @ u), float(q @ n)       # 面に沿った m、面の外向きの出（m。奥は負）

        # 塊（blocks）：面の上にある辺の端（段差）と上端の高さ
        for blk in b.get("blocks") or []:
            poly = blk.get("polygon") or []
            top_b = _v(blk.get("top_z"))
            for j in range(len(poly)):
                (sa, na), (sb, nb) = proj(poly[j]), proj(poly[(j + 1) % len(poly)])
                if abs(na) > PLANE_TOL_M or abs(nb) > PLANE_TOL_M or abs(sb - sa) < 0.3:
                    continue
                for s_ in (sa, sb):
                    if 0.3 < s_ < Lf - 0.3:
                        xs.append(FaceLine("x", f"塊{blk.get('id', '?')} 端 {s_:.1f}", s_, "part", 0.8))
                if top_b is not None:
                    zs.append(FaceLine("z", f"塊{blk.get('id', '?')} 上端", top_b, "level"))
        # 部品
        for i, pt in enumerate(b.get("parts") or []):
            t = pt.get("type")
            tag = f"部品{i}（{_short(pt.get('name', t))}）"
            if t == "box" and pt.get("polygon"):
                z0, z1 = _v(pt.get("z0")) or 0.0, _v(pt.get("z1"))
                seen_in = False
                for k, P in enumerate(pt["polygon"]):
                    s_, nd = proj(P)
                    if abs(nd) <= PLANE_TOL_M and -0.3 <= s_ <= Lf + 0.3:
                        xs.append(FaceLine("x", f"{tag} 縁{k}", s_, "part", 1.0))
                        seen_in = True
                    elif PLANE_TOL_M < nd <= OFF_PLANE_MAX_M and -0.3 <= s_ <= Lf + 0.3:
                        xs.append(FaceLine("x", f"{tag} 縁{k}（手前 {nd:.1f} m）", s_, "part", 0.0, False, nd))
                if seen_in:
                    ins = [proj(P)[0] for P in pt["polygon"] if abs(proj(P)[1]) <= PLANE_TOL_M]
                    if z1 is not None and top is not None and z1 > top + 0.5 and len(ins) >= 2 and max(ins) - min(ins) > 0.5:
                        tall.append({"name": tag, "s0": min(ins), "s1": max(ins), "z0": top, "z1": z1})
                    if z1 is not None:
                        zs.append(FaceLine("z", f"{tag} 上端", z1, "part"))
                    if z0 > 0.3:
                        zs.append(FaceLine("z", f"{tag} 下端", z0, "part"))
            elif t == "balcony" and pt.get("face") == face_id:
                a0, a1, z = _v(pt.get("along0")), _v(pt.get("along1")), _v(pt.get("z"))
                dep = _v(pt.get("depth")) or 0.0
                ph = _v(pt.get("parapet_h")) or 0.0
                for nm, a in (("左", a0), ("右", a1)):
                    if a is not None:
                        xs.append(FaceLine("x", f"{tag} {nm}端", a, "part", 0.0, dep <= PLANE_TOL_M, dep))
                if z is not None:
                    zs.append(FaceLine("z", f"{tag} 床", z, "part", 0.0, dep <= PLANE_TOL_M, dep))
                    if ph:
                        zs.append(FaceLine("z", f"{tag} 手すり上端", z + ph, "part", 0.0, dep <= PLANE_TOL_M, dep))

    # 同じ位置の重複を除く（柱の線と面の端・部品の縁が重なる時は、情報の多い方＝柱以外を残す）
    def dedupe(items: list[FaceLine]) -> list[FaceLine]:
        out: dict[tuple, FaceLine] = {}
        for it in items:
            key = (round(it.m, 2), it.in_plane)
            old = out.get(key)
            if old is None or (old.kind == "periodic" and it.kind != "periodic"):
                out[key] = it
        return sorted(out.values(), key=lambda t: t.m)

    return dedupe(xs), dedupe(zs), {"length": length, "ground_offset": ground, "face_top": top, "tall_parts": tall}


def face_lines(b: dict, face_id: str) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    """JSON の面から、横方向の線（x, m）と高さの線（z, m）を列挙する（柱割りと高さの線だけ。従来の互換）。"""
    xs, zs, _ = face_lines_detail(b, face_id)
    keep = ("periodic", "level")      # 従来の中身（柱割りと高さの線）。面の端・部品の縁は face_lines_detail で
    return [(l.name, l.m) for l in xs if l.in_plane and l.kind in keep], [(l.name, l.m) for l in zs if l.in_plane and l.kind in keep]


def _fit_offset(json_px: np.ndarray, det: np.ndarray, cap: float, lo: float, hi: float) -> float:
    """JSON の線（相対 px）を検出エッジに一番よく重ねる平行移動を総当たりで決める。
    cap より遠い線は一律 cap の罰（外れ値に強い）。lo〜hi の範囲に入る候補だけ。"""
    if len(json_px) == 0 or len(det) == 0:
        return 0.0
    best, best_score = 0.0, None
    cands = (det[None, :] - json_px[:, None]).ravel()
    cands = cands[(cands >= lo) & (cands <= hi)]
    if len(cands) == 0:
        return 0.0
    for off in np.unique(np.round(cands, 1)):
        pos = json_px + off
        d = np.abs(pos[:, None] - det[None, :]).min(axis=1)
        score = np.minimum(d, cap).sum()
        if best_score is None or score < best_score - 1e-9:
            best, best_score = float(off), float(score)
    return best


def valid_mask(img: np.ndarray, margin_px: int = 6) -> np.ndarray:
    """正面化の外側の黒い余白（無効画素）を除いた「使える画素」の真偽マスク。
    ほぼ真っ黒（≤2）の塊のうち小さい物（暗い窓など）は余白と見なさず、余白の縁から margin_px 内側も外す。"""
    import cv2

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    invalid = (gray <= 2).astype(np.uint8)
    invalid = cv2.morphologyEx(invalid, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    if margin_px > 0:
        invalid = cv2.dilate(invalid, np.ones((2 * margin_px + 1, 2 * margin_px + 1), np.uint8))
    return invalid == 0


def _masked_profile(gray: np.ndarray, valid: np.ndarray, axis: str) -> np.ndarray:
    """measure._profile と同じ縁の強さだが、無効画素の上の勾配は数えず、有効画素だけで平均する。
    有効画素が少ない列・行（余白の中）は 0。"""
    import cv2

    if valid.all():
        return _profile(gray, axis)      # 余白が無ければ measure と同じ値（従来の挙動）
    g = gray.astype(np.float32)
    if not valid.all():
        fill = float(g[valid].mean()) if valid.any() else 0.0
        g = np.where(valid, g, fill)
    g = cv2.GaussianBlur(g, (0, 0), 1.2)
    d = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3) if axis == "x" else cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    d = np.abs(d) * valid
    ax = 0 if axis == "x" else 1
    cnt = valid.sum(axis=ax)
    p = d.sum(axis=ax) / np.maximum(cnt, 1)
    p[cnt < 0.3 * valid.shape[ax]] = 0.0
    k = max(15, int(len(p) * 0.05) | 1)
    p = p - np.convolve(p, np.ones(k) / k, mode="same")
    return np.clip(p, 0, None)


def sky_masks(img: np.ndarray, valid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(空, 壁) の真偽マスク。空は青が赤より強いか、とても明るい（雲）画素。壁は空でも無効でもない画素。"""
    import cv2

    bgr = img if img.ndim == 3 else cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    sky = ((bgr[..., 0].astype(np.int16) - bgr[..., 2].astype(np.int16)) > SKY_BLUE_MARGIN) | (bgr.min(axis=2) > 225)
    return sky & valid, (~sky) & valid


def sky_boundary_rows(sky: np.ndarray, wall: np.ndarray, box: tuple[int, int, int, int], run: int = 12,
                      min_support: float = 0.05) -> list[tuple[float, float]]:
    """空の下から壁が始まる行（パラペットや塔の上端）を、(行 px, その行で始まる列の割合) で返す。
    各列を上から見て、空が run 画素続いた直後に壁が run 画素続く最初の行。同じ行（±4 px）の列を数える。"""
    x0, y0, x1, y1 = box
    sk, wl = sky[y0:y1, x0:x1].astype(np.int32), wall[y0:y1, x0:x1].astype(np.int32)
    h, w = sk.shape
    if h < 3 * run or w < 8:
        return []
    cs_s = np.vstack([np.zeros((1, w), np.int32), np.cumsum(sk, axis=0)])
    cs_w = np.vstack([np.zeros((1, w), np.int32), np.cumsum(wl, axis=0)])
    r = np.arange(run, h - run)
    sky_before = cs_s[r] - cs_s[r - run]
    wall_after = cs_w[r + run] - cs_w[r]
    hit = (sky_before >= run) & (wall_after >= run)
    first = np.where(hit.any(axis=0), hit.argmax(axis=0), -1)
    rows = first[first >= 0] + run + y0
    if len(rows) == 0:
        return []
    hist = np.bincount((rows - y0) // 4, minlength=h // 4 + 2)
    out = []
    for i in np.argsort(-hist):
        if hist[i] < min_support * w:
            break
        sel = rows[(rows - y0) // 4 == i]
        out.append((float(np.median(sel)), float(hist[i]) / w))
    kept: list[tuple[float, float]] = []
    for r_, f_ in out:
        if all(abs(r_ - k) > 12 for k, _ in kept):
            kept.append((r_, f_))
    return kept[:6]


def _strong_peaks(p: np.ndarray, period: float) -> list[tuple[float, float]]:
    """measure._peaks と同じ拾い方で（位置, 強さ）を強い順に返す。"""
    if period is None or period < 3 or len(p) < 5:
        return []
    k = max(3, int(period * 0.3) | 1)
    sm = np.convolve(p, np.ones(k) / k, mode="same")
    if sm.max() <= 0:
        return []
    thr = sm.max() * 0.15
    idx = [i for i in range(1, len(sm) - 1) if sm[i] >= sm[i - 1] and sm[i] >= sm[i + 1] and sm[i] > thr]
    idx.sort(key=lambda i: -sm[i])
    kept: list[int] = []
    for i in idx:
        if all(abs(i - j) >= 0.5 * period for j in kept):
            kept.append(i)
    return [(float(i), float(sm[i])) for i in kept]


def rank_x_origins(items: list[tuple[FaceLine, float]], det: np.ndarray, area: tuple[float, float],
                   tolm: float, valid_cols: tuple[float, float] | None, pitch_px: float | None,
                   extra=None, top: int = 5) -> list[dict]:
    """横の原点（面の s=0 が正面化画像の何 px か）の候補を、点数の高い順に返す。

    柱の繰り返しだけでは「何本目の柱か」が決まらない（原点が周期の整数倍ずれても線がそろう）ので、
    非周期の目印で順位を付ける。items は (線, 原点 0 のときの px) の並び。
        柱の線        見つかった割合（周期の整数倍ずれると、端で本数が変わるだけで差がつかない）
        面の端・部品・塊の縁   見つかれば +w、写真の範囲にあるのに見つからなければ −w/4（見つかった度合 q に比例）
        柱列の外への延長   柱列の外側に、同じ周期でエッジが続いていれば −0.5（柱の数え違いのしるし）
        空に突き出る部品（階段塔）の輪郭   部品の上の帯で、部品の幅の中だけ壁（空でない）、外は空なら +、逆なら −（最大 ±2）
        空に突き出る部品（階段塔）の輪郭   部品の上の帯で、部品の幅の中は壁（空でない）、外は空なら +、逆なら −（
        正面化画像の有効画素の端   面の端が有効画素の端に合えば +0.1（写真の縁と壁の端は別の物なので小さく）
    area は エッジを当てにしてよい列の範囲（切り出しの縁から少し内側）。範囲外の線は点数に入れない。"""
    if not items or len(det) == 0:
        return []
    jpx = np.array([p for _, p in items], float)
    cand = np.unique(np.round((det[None, :] - jpx[:, None]).ravel(), 0))
    lo, hi = area
    per_idx = [i for i, (l, _) in enumerate(items) if l.kind == "periodic" and l.weight > 0]
    lm_idx = [i for i, (l, _) in enumerate(items) if l.kind != "periodic" and l.weight > 0]
    n_per = len(per_idx)
    first = last = []
    if pitch_px and n_per >= 2:
        ordered = sorted(per_idx, key=lambda i: jpx[i])
        k_end = 2 if len(ordered) >= 4 and abs((jpx[ordered[1]] - jpx[ordered[0]]) - pitch_px) > 0.2 * pitch_px else 1
        first, last = ordered[:k_end], ordered[-k_end:]
    ends = [i for i in lm_idx if items[i][0].name in ("面の端0", "面の端L")]

    def score(ox: float) -> dict:
        pos = jpx + ox
        d = np.abs(pos[:, None] - det[None, :]).min(axis=1)
        q = np.clip(1.0 - d / tolm, 0.0, 1.0)
        inside = (pos >= lo) & (pos <= hi)
        sc, parts = 0.0, {}
        pi = [i for i in per_idx if inside[i]]
        if pi:
            parts["periodic"] = float(q[pi].sum() / max(len(pi), min(4, n_per)))
            sc += parts["periodic"]
        got = exp = 0
        lm = 0.0
        for i in lm_idx:
            if inside[i]:
                w = items[i][0].weight
                lm += w * (q[i] - 0.25 * (1.0 - q[i]))
                exp += 1
                got += int(q[i] > 0.5)
        sc += lm
        parts["landmarks"] = lm
        if first and pitch_px:
            ex_q = []
            for grp, sgn in ((first, -1), (last, +1)):
                for k in range(1, int((hi - lo) / pitch_px) + 2):
                    for i in grp:
                        pp = pos[i] + sgn * k * pitch_px
                        if lo <= pp <= hi:
                            ex_q.append(max(0.0, 1.0 - float(np.abs(det - pp).min()) / tolm))
            if ex_q:
                parts["overflow"] = -0.5 * float(np.mean(ex_q))
                sc += parts["overflow"]
        if valid_cols and ends:
            bonus = 0.0
            for i in ends:
                if min(abs(pos[i] - valid_cols[0]), abs(pos[i] - valid_cols[1])) <= 0.5 * tolm:
                    bonus += 0.1
            sc += bonus
            parts["valid_edge"] = bonus
        if extra is not None:
            e = extra(ox)
            if e is not None:
                sc += e
                parts["silhouette"] = e
        return {"origin_px": float(ox), "score": round(sc, 3), "landmarks_found": got, "landmarks_expected": exp,
                "parts": {k: round(v, 3) for k, v in parts.items()}}

    scored = sorted((score(float(o)) for o in cand), key=lambda t: -t["score"])
    kept: list[dict] = []
    for sc in scored:
        if all(abs(sc["origin_px"] - k["origin_px"]) >= 1.5 * tolm for k in kept):
            kept.append(sc)
        if len(kept) >= top:
            break
    return kept


def compare(img: np.ndarray, b: dict, face_id: str, wall_box: list[float] | None = None,
            tol_m: float = 0.15, px_per_m: float | None = None,
            given_lines: dict | None = None, top_n: int = 20) -> CompareResult:
    import cv2

    res = CompareResult(ok=False, face=face_id, tol_m=tol_m)
    fx, fz, info = face_lines_detail(b, face_id)
    g_off = float(info["ground_offset"] or 0.0)
    res.ground_offset_m = g_off
    f = next(f for f in b.get("faces", []) if f["id"] == face_id)
    pitch = _v((f.get("bays") or {}).get("pitch"))
    m = measure(img, wall_box)
    bay_px = m.bays.period_px if (m.bays and m.bays.period_px) else None
    floor_px = m.floors.period_px if (m.floors and m.floors.period_px) else None
    if m.wall_box is None:
        res.reason = "壁の範囲が小さすぎます"
        return res
    # 画素→m：与えられた縮尺 > 柱間隔。柱割りの無い面は壁のタイルなどから読んだ縮尺が要る（検収係が --px-per-m で与える）
    if px_per_m is None:
        if pitch and bay_px:
            px_per_m = bay_px / pitch
        elif not pitch:
            res.reason = "JSON の柱間隔が無いので縮尺が決まりません（--px-per-m で与えてください。柱割りの無い面は壁のタイルなどから読んだ縮尺が要ります）"
            return res
        else:
            res.reason = "写真から柱間隔の周期が取れません（面が小さいか、木で隠れています。--px-per-m で縮尺を与えられます）"
            return res
    res.px_per_m = round(px_per_m, 3)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    x0, y0, x1, y1 = [int(v) for v in m.wall_box]
    crop = gray[y0:y1, x0:x1]
    # 検出エッジ（縦：柱型の縁・部品の縁、横：窓の上下・パラペットなど）。周期の 0.4 倍まで近い物も拾う
    # 正面化の外側の黒い余白は縁の検出から外す
    valid = valid_mask(img)
    res.valid_ratio = round(float(valid.mean()), 3)
    vcrop = valid[y0:y1, x0:x1]
    sky, wall_px = sky_masks(img, valid)
    sky_rows = sky_boundary_rows(sky, wall_px, (x0, y0, x1, y1))
    # 周期が取れない面（柱割り・窓の無い壁）は、縮尺から 2 m を山の間隔の目安にする
    use_bay = bool(bay_px) and bool(pitch)
    xper = bay_px if use_bay else 2.0 * px_per_m
    fper_ok = bool(floor_px) and 1.5 <= floor_px / px_per_m <= 12.0     # タイル目地の周期（数 cm）は階高と見なさない
    fper = floor_px if fper_ok else 2.0 * px_per_m
    px_x = _strong_peaks(_masked_profile(crop, vcrop, "x"), xper * 0.4)
    px_y = _strong_peaks(_masked_profile(crop, vcrop, "y"), fper * 0.4)
    px_x = [(u + x0, st) for u, st in px_x]
    px_y = [(u + y0, st) for u, st in px_y]
    given = given_lines or {}
    named_x, named_z = given.get("x_named") or {}, given.get("z_named") or {}
    auto_x, auto_z = not ("x_px" in given or named_x), not ("z_px" in given or named_z)
    if not auto_x:
        px_x = [(float(u), 0.0) for u in given.get("x_px", [])]
    if not auto_z:
        px_y = [(float(u), 0.0) for u in given.get("z_px", [])]
    # {"x_named": {"柱型4左": 622.0}} のように線の名前も添えれば、どの柱か（周期の取り違え）まで判断役が決める
    px_x += [(float(u), 0.0) for u in named_x.values() if not any(abs(float(u) - q) < 1 for q, _ in px_x)]
    px_y += [(float(u), 0.0) for u in named_z.values() if not any(abs(float(u) - q) < 1 for q, _ in px_y)]
    res.candidates = {
        "x": [{"px": round(u, 1), "strength": round(st, 2), "auto": auto_x} for u, st in sorted(px_x, key=lambda t: -t[1])[:top_n]],
        "z": [{"px": round(u, 1), "strength": round(st, 2), "auto": auto_z} for u, st in sorted(px_y, key=lambda t: -t[1])[:top_n]],
    }
    det_x = np.array([u for u, _ in px_x], dtype=float)
    det_y = np.array([u for u, _ in px_y], dtype=float)
    H, W = gray.shape[:2]
    # 切り出しの縁の近くはエッジ検出が効かない（移動平均の端）ので比べない
    ex = 0.25 * xper if use_bay else 0.1 * px_per_m
    ey = 0.25 * fper if fper_ok else 0.1 * px_per_m
    lim_x = 0.25 * bay_px if use_bay else 0.6 * px_per_m          # エッジと結ぶ距離の上限
    lim_z = 0.25 * fper if fper_ok else 0.6 * px_per_m
    # 縦の原点：z は上向き、画像の y は下向き。y = z_origin − z·ppm
    # 空の下から壁が始まる行（パラペット・塔の上端）があれば、それを上端の線の位置の候補に使う（窓の無い壁でも決まる）
    jz_all = np.array([-l.m * px_per_m for l in fz])
    jz = np.array([-l.m * px_per_m for l in fz if l.in_plane])      # 原点の当てはめは面の上の線だけで（手前に出た物は縮尺が違う）
    det_y_all = np.array(sorted(set([float(u) for u in det_y] + [r for r, _ in sky_rows])), dtype=float)
    anchors = [l for l in fz if l.in_plane and l.name.endswith("上端") and l.name.startswith(("パラペット", "塊", "部品"))]
    z_note = ""
    if len(jz) and len(det_y_all):
        if sky_rows and anchors:
            best_z, best_c = None, None
            for r_, _f in sky_rows:
                for an in anchors:
                    zo = r_ + an.m * px_per_m
                    yy = jz + zo
                    inside = (yy >= y0 + ey) & (yy <= y1 - ey)
                    if not inside.any():
                        continue
                    d_ = np.minimum(np.abs(yy[inside][:, None] - det_y_all[None, :]).min(axis=1), lim_z * 1.2)
                    # 写真の範囲に入る線の平均の距離。パラペット（面の上端）への当てはめを優遇する
                    # （空と壁の境の大半は面の上端。他の線への当てはめは、パラペットの半分以下の距離に収まる時だけ採る）
                    c = float(d_.mean()) * (1.0 if an.name.startswith("パラペット") else 2.5)
                    if best_c is None or c < best_c:
                        best_z, best_c = zo, c
            if best_z is None:
                best_z = float(_fit_offset(jz, det_y_all, cap=lim_z * 1.2, lo=0, hi=H + 0.3 * H))
            # 行は「壁が始まる最初の行」なので、近くの検出エッジ（上端の勾配の中心）に二乗和で詰める
            tol_z = 0.3 * px_per_m
            offs = best_z + np.arange(-tol_z, tol_z + 0.5, 1.0)
            near = np.abs((jz + best_z)[:, None] - det_y_all[None, :]).min(axis=1) <= tol_z
            use = near if near.any() else np.ones(len(jz), bool)
            cs = np.array([(np.minimum(np.abs((jz[use] + o)[:, None] - det_y_all[None, :]).min(axis=1), tol_z) ** 2).sum() for o in offs])
            best_z = float(offs[cs <= cs.min() + 1e-9].mean())
            res.z_origin_px = round(float(best_z), 1)
            z_note = "高さの原点：空の下から壁が始まる行を上端の線に合わせた"
            det_y = det_y_all
        else:
            res.z_origin_px = round(_fit_offset(jz, det_y_all, cap=lim_z * 1.2, lo=0, hi=H + 0.3 * H), 1)
    else:
        res.z_origin_px = round(float(y1 + g_off * px_per_m), 1)       # 手がかりが無ければ、壁の切り出しの下端＝面の前の地面
    if z_note:
        res.warnings.append(z_note)
    jz = jz_all
    oz = [float(u) + l.m * px_per_m for l in fz for k, u in named_z.items() if k == l.name]
    if oz:
        res.z_origin_px = round(float(np.median(oz)), 1)

    # 空に突き出る部品（階段塔など）の輪郭：部品の上の帯で、部品の幅の中は壁、外は空のはず
    tall = info.get("tall_parts") or []
    if tall and res.z_origin_px is not None:
        wall_img = wall_px

        def silhouette(ox: float) -> float | None:
            vals = []
            for tp in tall:
                ya = int(round(res.z_origin_px - (tp["z1"] - 0.3) * px_per_m))
                yb = int(round(res.z_origin_px - (tp["z0"] + 0.3) * px_per_m))
                ya, yb = max(ya, 0), min(yb, H)
                if yb - ya < 0.3 * (tp["z1"] - tp["z0"] - 0.6) * px_per_m or yb - ya < 10:
                    continue
                xa, xb = ox + tp["s0"] * px_per_m, ox + tp["s1"] * px_per_m
                if min(xb, W) - max(xa, 0) < 0.6 * (xb - xa):        # 部品の幅の 6 割以上が写っていなければ当てにしない
                    continue
                Lpx = (info["length"] or 0.0) * px_per_m
                strip_w, strip_v = wall_img[ya:yb], valid[ya:yb]

                def frac(c0: float, c1: float, want_wall: bool) -> float | None:
                    c0, c1 = int(max(c0, 0)), int(min(c1, W))
                    if c1 - c0 < 8:
                        return None
                    v = strip_v[:, c0:c1]
                    if v.sum() < 0.3 * v.size:
                        return None
                    w_ = (strip_w[:, c0:c1] & v).sum() / v.sum()
                    return float(w_ if want_wall else 1.0 - w_)

                got = [frac(xa + 3, xb - 3, True)]
                if Lpx:
                    got += [frac(ox, xa - 3, False), frac(xb + 3, ox + Lpx, False)]
                got = [g for g in got if g is not None]
                if got:
                    vals.append(float(np.mean(got)))
            return 4.0 * (float(np.mean(vals)) - 0.5) if vals else None
    else:
        silhouette = None
    # 横の原点：非周期の目印で順位を付けた候補から 1 位を採る
    jx_all = np.array([l.m * px_per_m for l in fx])
    in_x = [(l, float(p)) for l, p in zip(fx, jx_all) if l.in_plane]
    cols = np.where(vcrop.mean(axis=0) > 0.5)[0]
    vcols = (float(cols.min() + x0), float(cols.max() + x0)) if len(cols) else None
    tolm = max(0.4 * px_per_m, 8.0)
    pitch_px = pitch * px_per_m if pitch else None
    pier_px = (_v((f.get('bays') or {}).get('pier_width')) or 0.0) * px_per_m
    cands = rank_x_origins(in_x, det_x, (x0 + ex, x1 - ex), tolm, vcols, pitch_px, silhouette) if (in_x and len(det_x)) else []
    if cands:
        res.x_origin_px = cands[0]["origin_px"]
        # 1 位の候補は、近くの ±tolm を 1 px ずつ動かして、線とエッジの距離（tolm で頭打ち）の二乗和が最小の所に詰める
        # （候補は整数画素に丸めてあるので）。同点が続く時はその中央
        pj = np.array([p for _, p in in_x])
        inside = (pj + res.x_origin_px >= x0 + ex) & (pj + res.x_origin_px <= x1 - ex)
        if inside.any():
            d0 = np.abs((pj + res.x_origin_px)[:, None] - det_x[None, :]).min(axis=1)
            use = inside & (d0 <= 0.75 * tolm)            # 候補の位置で結べている線だけで詰める
            if not use.any():
                use = inside
            offs = res.x_origin_px + np.arange(-tolm, tolm + 0.5, 1.0)
            costs = np.array([(np.minimum(np.abs((pj[use] + o)[:, None] - det_x[None, :]).min(axis=1), tolm) ** 2).sum() for o in offs])
            best = offs[costs <= costs.min() + 1e-9]
            res.x_origin_px = round(float(best.mean()), 1)
            cands[0]["origin_px"] = res.x_origin_px
        base = cands[0]["origin_px"]
        for c in cands:
            c["origin_px"] = round(c["origin_px"], 1)
            c["shift_periods"] = round((c["origin_px"] - base) / pitch_px, 2) if pitch_px else None
        res.origin_candidates = cands
        if len(cands) >= 2 and cands[0]["score"] - cands[1]["score"] < AMBIGUOUS_DIFF:
            res.ambiguous = True
            sh = f"（柱 {cands[1]['shift_periods']:+g} 本ぶん）" if cands[1].get("shift_periods") else ""
            res.warnings.append(
                f"横の原点が決まりません：1 位 {cands[0]['origin_px']} px（{cands[0]['score']}）と 2 位 {cands[1]['origin_px']} px（{cands[1]['score']}）{sh}の差が小さい。"
                "--lines の x_named（例 {\"柱型0左\": 622.0}）か x_origin_px で判断役が決めてください")
    else:
        res.x_origin_px = 0.0
    if "x_origin_px" in given:
        res.x_origin_px = round(float(given["x_origin_px"]), 1)
        res.ambiguous = False
        res.warnings.append("横の原点は --lines の x_origin_px（判断役）で与えられた")
    ox = [float(u) - l.m * px_per_m for l in fx for k, u in named_x.items() if k == l.name]
    if ox:
        res.x_origin_px = round(float(np.median(ox)), 1)
        res.ambiguous = False
    def assign(axis, items, det, sign, limit):
        """JSON の線と検出エッジを、近い組から順に 1 対 1 で結ぶ（1 本のエッジを 2 本の線で取り合わない）。
        面の上の線を先に結び、手前に出た線は残りのエッジから結ぶ。"""
        pairs = []
        for i, (ln, pos) in enumerate(items):
            for j, d in enumerate(det):
                if abs(d - pos) <= limit:
                    pairs.append((0 if ln.in_plane else 1, abs(d - pos), i, j))
        used_i, used_j = set(), set()
        got = {}
        for _, dist, i, j in sorted(pairs):
            if i in used_i or j in used_j:
                continue
            used_i.add(i)
            used_j.add(j)
            got[i] = j
        auto = auto_x if axis == "x" else auto_z
        for i, (ln, pos) in enumerate(items):
            extra = dict(kind=ln.kind, in_plane=ln.in_plane, depth_m=round(ln.depth_m, 2))
            if i in got:
                d_m = float(sign * (det[got[i]] - pos) / px_per_m)
                res.lines.append(LineCheck(axis, ln.name, round(ln.m, 3), round(ln.m + d_m, 3), round(d_m, 3), bool(abs(d_m) <= tol_m), auto, **extra))
            else:
                res.lines.append(LineCheck(axis, ln.name, round(ln.m, 3), None, None, None, auto, **extra))

    items_x = [(l, float(p) + res.x_origin_px) for l, p in zip(fx, jx_all) if x0 + ex <= p + res.x_origin_px <= x1 - ex]
    items_z = [(l, float(p) + res.z_origin_px) for l, p in zip(fz, jz) if y0 + ey <= p + res.z_origin_px <= y1 - ey]
    assign("x", items_x, det_x, +1, lim_x)
    assign("z", items_z, det_y, -1, lim_z)
    # 階高の検算（縮尺に依らない）
    fp = _v(b.get("floor_pitch"))
    if fp and fper_ok:
        res.floor_pitch_json_m = fp
        res.floor_pitch_photo_m = round(floor_px / px_per_m, 3)
    inpl = [l for l in res.lines if l.in_plane]
    res.n_ok = sum(1 for l in inpl if l.ok is True)
    res.n_ng = sum(1 for l in inpl if l.ok is False)
    res.n_missing = sum(1 for l in inpl if l.ok is None)
    res.n_lines_off_plane = len(res.lines) - len(inpl)
    diffs = [abs(l.diff_m) for l in inpl if l.diff_m is not None]
    res.worst_m = round(max(diffs), 3) if diffs else None
    res.ok = bool(res.lines)
    if not res.ok:
        res.reason = "比べられる線が写真の範囲にありません"
    return res


def draw(img: np.ndarray, b: dict, res: CompareResult) -> np.ndarray:
    """写真に JSON の線（青）と検出エッジ（赤）とずれを描く。"""
    import cv2

    out = img.copy()
    H, W = out.shape[:2]
    ppm = res.px_per_m or 1.0
    for l in res.lines:
        col = (255, 120, 0) if l.ok else ((0, 0, 255) if l.ok is False else (0, 180, 255))
        if l.axis == "x":
            x = int(res.x_origin_px + l.json_m * ppm)
            cv2.line(out, (x, 0), (x, H), col, 3)
            if l.diff_m is not None:
                xp = int(res.x_origin_px + l.photo_m * ppm)
                cv2.line(out, (xp, 0), (xp, H), (0, 0, 255), 1)
                cv2.putText(out, f"{l.diff_m:+.2f}", (x + 4, 80 + (hash(l.what) % 5) * 40), cv2.FONT_HERSHEY_SIMPLEX, 1.1, col, 3)
        else:
            y = int(res.z_origin_px - l.json_m * ppm)
            cv2.line(out, (0, y), (W, y), col, 3)
            if l.diff_m is not None:
                yp = int(res.z_origin_px - l.photo_m * ppm)
                cv2.line(out, (0, yp), (W, yp), (0, 0, 255), 1)
                cv2.putText(out, f"{l.what} {l.diff_m:+.2f}m", (20, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 1.1, col, 3)
    txt = f"{res.face}  ok {res.n_ok} / ng {res.n_ng} / none {res.n_missing}  worst {res.worst_m} m  {res.px_per_m} px/m"
    cv2.putText(out, txt, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 5)
    cv2.putText(out, txt, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
    return out


def compare_file(front_jpg: Path, building_json: Path, face_id: str, out_dir: Path | None = None,
                 tol_m: float = 0.15, px_per_m: float | None = None,
                 lines_json: Path | None = None) -> tuple[CompareResult, Path]:
    import cv2

    from .rectify import read_image
    from .schema import load_building

    b = load_building(building_json)
    img = read_image(front_jpg)
    fj = front_jpg.with_suffix(".json")
    wb = json.loads(fj.read_text(encoding="utf-8")).get("wall_box") if fj.exists() else None
    given = json.loads(Path(lines_json).read_text(encoding="utf-8")) if lines_json else None
    res = compare(img, b, face_id, wb, tol_m, px_per_m, given)
    res.source = str(front_jpg)
    out_dir = out_dir or Path("out") / b["id"] / "compare"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{front_jpg.stem.replace('_front', '')}_{face_id}_compare"
    (out_dir / f"{stem}.json").write_text(json.dumps(res.to_json(), ensure_ascii=False, indent=1), encoding="utf-8")
    png = out_dir / f"{stem}.png"
    if res.ok:
        ov = draw(img, b, res)
        h, w = ov.shape[:2]
        s = min(1.0, 2400 / w)
        if s < 1:
            ov = cv2.resize(ov, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        cv2.imencode(".png", ov)[1].tofile(str(png))
    return res, png
