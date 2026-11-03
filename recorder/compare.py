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

    def to_json(self) -> dict:
        return asdict(self)


def _v(m) -> float | None:
    if isinstance(m, (int, float)):
        return float(m)
    mm = Measurement.from_json(m)
    return float(mm.value) if mm.known else None


def face_lines(b: dict, face_id: str) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    """JSON の面から、横方向の線（x, m）と高さの線（z, m）を列挙する。"""
    f = next((f for f in b.get("faces", []) if f["id"] == face_id), None)
    if f is None:
        raise KeyError(f"面 {face_id} がありません")
    xs: list[tuple[str, float]] = []
    zs: list[tuple[str, float]] = []
    bays = f.get("bays") or {}
    count, pitch = _v(bays.get("count")), _v(bays.get("pitch"))
    pier = _v(bays.get("pier_width")) or 0.0
    length = _v(f.get("length"))
    if length is None and f.get("from") and f.get("to"):
        length = float(np.hypot(f["to"][0] - f["from"][0], f["to"][1] - f["from"][1]))
    edges, _w = bay_edges(bays, length)     # margin_from / margin_to / end_margin の規則は drawing.py と共通
    if edges:
        for i, c in enumerate(edges):
            if pier > 0:
                xs.append((f"柱型{i}左", c - pier / 2))
                xs.append((f"柱型{i}右", c + pier / 2))
            else:
                xs.append((f"柱線{i}", c))
    for w in f.get("windows") or []:
        zb, zt = _v(w.get("z_bottom")), _v(w.get("z_top"))
        if zb is not None:
            zs.append((f"{w.get('name', '窓')} 下端", zb))
        if zt is not None:
            zs.append((f"{w.get('name', '窓')} 上端", zt))
    for op in f.get("openings") or []:
        zt = _v(op.get("z_top"))
        if zt is not None:
            zs.append((f"{op.get('name', '開口')} 天井", zt))
    for lg in f.get("ledges") or []:
        z = _v(lg.get("z"))
        if z is not None:
            zs.append((f"{lg.get('name', '段')}", z))
    top = _v(f.get("top_z")) or _v(b.get("parapet_top"))
    if top is not None:
        zs.append(("パラペット上端", top))
    # 同じ位置の重複を除く
    xs = sorted({(round(x, 3)): (n, x) for n, x in xs}.values(), key=lambda t: t[1])
    zs = sorted({(round(z, 3)): (n, z) for n, z in zs}.values(), key=lambda t: t[1])
    return xs, zs


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


def compare(img: np.ndarray, b: dict, face_id: str, wall_box: list[float] | None = None,
            tol_m: float = 0.15, px_per_m: float | None = None,
            given_lines: dict | None = None, top_n: int = 20) -> CompareResult:
    import cv2

    res = CompareResult(ok=False, face=face_id, tol_m=tol_m)
    xs, zs = face_lines(b, face_id)
    f = next(f for f in b.get("faces", []) if f["id"] == face_id)
    pitch = _v((f.get("bays") or {}).get("pitch"))
    m = measure(img, wall_box)
    if not m.ok or not m.bays.period_px:
        res.reason = "写真から柱間隔の周期が取れません（面が小さいか、木で隠れています）"
        return res
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    x0, y0, x1, y1 = [int(v) for v in m.wall_box]
    crop = gray[y0:y1, x0:x1]
    # 画素→m
    if px_per_m is None:
        if not pitch:
            res.reason = "JSON の柱間隔が無いので縮尺が決まりません（--px-per-m で与えてください）"
            return res
        px_per_m = m.bays.period_px / pitch
    res.px_per_m = round(px_per_m, 3)
    # 検出エッジ（縦：柱型の縁、横：窓の上下など）。周期の 0.4 倍まで近い物も拾う
    # 正面化の外側の黒い余白は縁の検出から外す
    valid = valid_mask(img)
    res.valid_ratio = round(float(valid.mean()), 3)
    vcrop = valid[y0:y1, x0:x1]
    fper = m.floors.period_px or m.bays.period_px
    px_x = _strong_peaks(_masked_profile(crop, vcrop, "x"), m.bays.period_px * 0.4)
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
    # 横の原点：JSON の x（m→px）を検出エッジに重ねる
    jx = np.array([x * px_per_m for _, x in xs])
    H, W = gray.shape[:2]
    res.x_origin_px = round(_fit_offset(jx, det_x, cap=0.3 * m.bays.period_px, lo=-W, hi=W), 1) if len(jx) else 0.0
    ox = [float(u) - x * px_per_m for n, x in xs for k, u in named_x.items() if k == n]
    if ox:
        res.x_origin_px = round(float(np.median(ox)), 1)
    # 縦の原点：z は上向き、画像の y は下向き。y = z_origin − z·ppm
    jz = np.array([-z * px_per_m for _, z in zs])
    res.z_origin_px = round(_fit_offset(jz, det_y, cap=0.3 * fper, lo=0, hi=H + 0.3 * H), 1) if len(jz) else float(y1)
    oz = [float(u) + z * px_per_m for n, z in zs for k, u in named_z.items() if k == n]
    if oz:
        res.z_origin_px = round(float(np.median(oz)), 1)

    def assign(axis, items, det, sign, limit):
        """JSON の線と検出エッジを、近い組から順に 1 対 1 で結ぶ（1 本のエッジを 2 本の線で取り合わない）。"""
        pairs = []
        for i, (name, val_m, pos) in enumerate(items):
            for j, d in enumerate(det):
                if abs(d - pos) <= limit:
                    pairs.append((abs(d - pos), i, j))
        used_i, used_j = set(), set()
        got = {}
        for dist, i, j in sorted(pairs):
            if i in used_i or j in used_j:
                continue
            used_i.add(i)
            used_j.add(j)
            got[i] = j
        for i, (name, val_m, pos) in enumerate(items):
            if i in got:
                d_m = float(sign * (det[got[i]] - pos) / px_per_m)
                res.lines.append(LineCheck(axis, name, round(val_m, 3), round(val_m + d_m, 3), round(d_m, 3), bool(abs(d_m) <= tol_m), auto_x if axis == "x" else auto_z))
            else:
                res.lines.append(LineCheck(axis, name, round(val_m, 3), None, None, None, auto_x if axis == "x" else auto_z))

    # 切り出しの縁の近くはエッジ検出が効かない（移動平均の端）ので比べない
    ex, ey = 0.25 * m.bays.period_px, 0.25 * fper
    items_x = [(name, x, px + res.x_origin_px) for (name, x), px in zip(xs, jx) if x0 + ex <= px + res.x_origin_px <= x1 - ex]
    items_z = [(name, z, pz + res.z_origin_px) for (name, z), pz in zip(zs, jz) if y0 + ey <= pz + res.z_origin_px <= y1 - ey]
    assign("x", items_x, det_x, +1, 0.25 * m.bays.period_px)
    assign("z", items_z, det_y, -1, 0.25 * fper)
    # 階高の検算（縮尺に依らない）
    fp = _v(b.get("floor_pitch"))
    if fp and m.floors.period_px:
        res.floor_pitch_json_m = fp
        res.floor_pitch_photo_m = round(m.floors.period_px / px_per_m, 3)
    res.n_ok = sum(1 for l in res.lines if l.ok is True)
    res.n_ng = sum(1 for l in res.lines if l.ok is False)
    res.n_missing = sum(1 for l in res.lines if l.ok is None)
    diffs = [abs(l.diff_m) for l in res.lines if l.diff_m is not None]
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
