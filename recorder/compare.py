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
from .schema import Measurement


@dataclass
class LineCheck:
    axis: str          # "x"（横の位置、面に沿った m）か "z"（高さ m）
    what: str          # 何の線か
    json_m: float      # JSON の位置（m）
    photo_m: float | None   # 写真で一番近いエッジの位置（m）
    diff_m: float | None    # 写真 − JSON（m）
    ok: bool | None         # |diff| ≤ tol なら True、見つからなければ None


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
    margin = _v(bays.get("end_margin")) or 0.0
    if count and pitch:
        for i in range(int(count) + 1):
            c = margin + i * pitch
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


def compare(img: np.ndarray, b: dict, face_id: str, wall_box: list[float] | None = None,
            tol_m: float = 0.15, px_per_m: float | None = None) -> CompareResult:
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
    det_x = np.array(_peaks(_profile(crop, "x"), m.bays.period_px * 0.4)) + x0
    fper = m.floors.period_px or m.bays.period_px
    det_y = np.array(_peaks(_profile(crop, "y"), fper * 0.4)) + y0
    # 横の原点：JSON の x（m→px）を検出エッジに重ねる
    jx = np.array([x * px_per_m for _, x in xs])
    H, W = gray.shape[:2]
    res.x_origin_px = round(_fit_offset(jx, det_x, cap=0.3 * m.bays.period_px, lo=-W, hi=W), 1) if len(jx) else 0.0
    # 縦の原点：z は上向き、画像の y は下向き。y = z_origin − z·ppm
    jz = np.array([-z * px_per_m for _, z in zs])
    res.z_origin_px = round(_fit_offset(jz, det_y, cap=0.3 * fper, lo=0, hi=H + 0.3 * H), 1) if len(jz) else float(y1)

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
                res.lines.append(LineCheck(axis, name, round(val_m, 3), round(val_m + d_m, 3), round(d_m, 3), bool(abs(d_m) <= tol_m)))
            else:
                res.lines.append(LineCheck(axis, name, round(val_m, 3), None, None, None))

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
                 tol_m: float = 0.15, px_per_m: float | None = None) -> tuple[CompareResult, Path]:
    import cv2

    from .rectify import read_image
    from .schema import load_building

    b = load_building(building_json)
    img = read_image(front_jpg)
    fj = front_jpg.with_suffix(".json")
    wb = json.loads(fj.read_text(encoding="utf-8")).get("wall_box") if fj.exists() else None
    res = compare(img, b, face_id, wb, tol_m, px_per_m)
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
