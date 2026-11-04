"""棟 JSON から図面（SVG）を描く。

- elevation_svg(b, face_id): 1つの面の立面図。寸法線に値と根拠の写真番号を書く。
- plan_svg(b): 平面図（足元の外形と面の名前）。

色の決まり
    黒      measured（根拠あり）
    琥珀    estimated（推定。破線）
    赤      unknown（未確定。破線。下に理由と撮影依頼を書く）

高さの決まり
    levels / windows / bands / openings の z は「その面の地面からの高さ（m）」。
    ground_z だけが標高（m）。
"""

from __future__ import annotations

import html
import math
from typing import Any

from . import parts_geom, schema
from .schema import Measurement

MARGIN_WARNINGS: list[str] = []   # 端の余白が unknown で仮に等分した面（draw が表示する）

SCALE = 14.0  # px / m
LEFT = 70
TOP = 110
RIGHT_LABELS = 300
BOTTOM = 40

COL = {"measured": "#222", "estimated": "#b07a10", "unknown": "#c8322b"}
DASH = {"measured": "", "estimated": "6 4", "unknown": "6 4"}
FILL_WALL = "#d9b48a"
FILL_PIER = "#c99a69"
FILL_WIN = "#3f4753"
FILL_BAND = "#555b63"
FILL_SPANDREL = "#4a3b30"
FILL_RECESS = "#6f665c"
# 部品の素材 → 立面の塗り（parts_geom の mat の名前）
FILL_PART = {"tile": FILL_PIER, "glass": FILL_WIN, "metal": "#9aa0a6", "dark": FILL_BAND, "brick": "#a5523f",
             "stone": "#7a6a6a", "concrete": "#e6e2da", "canopy": "#d5e4d6", "white": "#f2f0ea", "coping": "#444",
             "leaf": "#6f8f55", "interior": "#2a2c30", "roof": "#8a8883", "curtain": "#6b6a66"}


def _fill(mat) -> str:
    if isinstance(mat, str) and mat.startswith("asset:"):
        return "#b9b2a6"            # CC0 の質感（立面では無地の灰）
    return FILL_PART.get(mat or "tile", FILL_PIER)


def _prim_depth(pr: dict) -> float:
    """素片の手前側の n（大きいほど見る人に近い。立面は奥から順に描く）。"""
    if pr["k"] == "prism":
        return max(q[1] for q in pr["pts"])
    if pr["k"] == "ext_a":
        return pr["n1"]
    return max(q[0] for q in pr["pts"])


def _m(d: dict | None, key: str) -> Measurement:
    return Measurement.from_json((d or {}).get(key))


def _esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def _style(m: Measurement) -> str:
    st = m.status if m.status in COL else "unknown"
    extra = f' stroke-dasharray="{DASH[st]}"' if DASH[st] else ""
    return f'stroke="{COL[st]}"{extra}'


class _Svg:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def add(self, s: str) -> None:
        self.parts.append(s)

    def text(self, x: float, y: float, s: str, size: int = 12, fill: str = "#222",
             anchor: str = "start", weight: str = "normal") -> None:
        self.add(
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}" font-weight="{weight}" '
            f'font-family="\'Noto Sans JP\', \'Yu Gothic UI\', sans-serif">{_esc(s)}</text>'
        )

    def line(self, x1: float, y1: float, x2: float, y2: float, attrs: str, width: float = 1.0) -> None:
        self.add(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" {attrs} stroke-width="{width}"/>')

    def rect(self, x: float, y: float, w: float, h: float, fill: str, attrs: str = 'stroke="none"',
             width: float = 1.0) -> None:
        self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{fill}" {attrs} stroke-width="{width}"/>')

    def render(self, w: float, h: float) -> str:
        body = "\n".join(self.parts)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h:.0f}" '
            f'viewBox="0 0 {w:.0f} {h:.0f}">\n<rect width="100%" height="100%" fill="#fbfaf7"/>\n{body}\n</svg>\n'
        )


def _face_geometry(b: dict, fc: dict) -> tuple[Measurement, float, list[str]]:
    """面の長さ（m）と、その決め方の説明。無ければ仮の幅を返す（赤で描く）。"""
    notes: list[str] = []
    length = _m(fc, "length")
    bays = fc.get("bays") or {}
    cnt, pitch = _m(bays, "count"), _m(bays, "pitch")
    if length.known:
        return length, float(length.value), notes
    if cnt.known and pitch.known:
        mf, mt, _w = schema.bay_margins(bays, None)
        known = [x for x in (mf, mt) if x is not None]
        L = cnt.value * pitch.value + (sum(known) if len(known) == 2 else 2 * known[0] if known else 0.0)
        notes.append("面の長さはスパン数×スパンから")
        return Measurement(L, status="estimated", method="スパン数×スパン", evidence=cnt.evidence + pitch.evidence), float(L), notes
    notes.append("面の長さが未確定（仮の幅 40 m で描画）")
    return Measurement(None), 40.0, notes


def _face_height(b: dict) -> tuple[Measurement, float, list[str]]:
    notes: list[str] = []
    top = _m(b, "parapet_top")
    if top.known:
        return top, float(top.value), notes
    cnt, pitch = _m(b, "floor_count"), _m(b, "floor_pitch")
    if cnt.known and pitch.known:
        H = cnt.value * pitch.value
        notes.append("高さは階数×階高から（パラペットは未確定）")
        return Measurement(H, status="estimated", method="階数×階高", evidence=cnt.evidence + pitch.evidence), float(H), notes
    notes.append("高さが未確定（仮の高さ 20 m で描画）")
    return Measurement(None), 20.0, notes


def face_ground_line(b: dict, fc: dict, dem: dict | None = None) -> tuple[float, str]:
    """立面の地面の線の高さ（z=0 から）と出どころ。地形（面の前の帯の中央値）を優先し、無ければ ground_offset、どちらも無ければ 0。
    模型は地形の面を使うので、立面もそれに合わせる。dem を渡さなければ out/site/dem5a.npz を探す。"""
    gz = (b.get("ground_z") or {})
    gzv = gz.get("value") if isinstance(gz, dict) and gz.get("status") != "unknown" else (gz if isinstance(gz, (int, float)) else None)
    if gzv is not None and fc.get("from") and fc.get("to"):
        try:
            from . import site
            d = dem if dem is not None else site.load_dem()
            v = site.face_band_offset(d, fc["from"], fc["to"], gzv)
            if v is not None:
                return float(v), "terrain"
        except (OSError, FileNotFoundError, KeyError, ValueError):
            pass
    if "ground_offset" in fc and schema.Measurement.from_json(fc.get("ground_offset")).known:
        return schema.face_ground_offset(fc), "ground_offset"
    return 0.0, "none"


def elevation_svg(b: dict, face_id: str) -> str:
    faces = {f["id"]: f for f in b.get("faces", [])}
    if face_id not in faces:
        raise KeyError(f"面 {face_id} がありません")
    fc = faces[face_id]

    length_m, L, notes = _face_geometry(b, fc)
    height_m, H, n2 = _face_height(b)
    notes += n2
    unknown_lines: list[str] = []

    W = L * SCALE
    Hpx = H * SCALE
    x0, y_ground = LEFT, TOP + Hpx          # y_ground は z=0（棟の基準面）の行
    g_m = _m(fc, "ground_offset")           # 面の前の地面の高さ（z=0 から。沈んだ広場は負）。省略は 0
    g, g_src = face_ground_line(b, fc)
    below = max(0.0, -g) * SCALE            # 地面が z=0 より低い時は、壁を地面まで下げて描く
    if g_src != "terrain" and "ground_offset" in fc and not g_m.known:
        unknown_lines.append("面の前の地面の高さ（ground_offset）")
    svg = _Svg()

    def X(u: float) -> float:
        return x0 + u * SCALE

    def Y(z: float) -> float:
        return y_ground - z * SCALE

    # 題名
    svg.text(LEFT, 34, f"{b.get('name', '')}　{fc.get('name', face_id)}　立面図", 18, weight="bold")
    svg.text(LEFT, 56, "寸法は m。[ ] は根拠の写真番号。赤の破線は未確定（下に理由）。琥珀は推定。", 11, "#666")

    # 壁の本体
    wall_attr = _style(height_m if not length_m.known or height_m.status == "unknown" else length_m)
    if length_m.known and height_m.known:
        wall_attr = 'stroke="#222"'
    svg.rect(x0, TOP, W, Hpx + below, FILL_WALL, wall_attr, 1.2)

    # 柱型（bays）
    bays = fc.get("bays") or {}
    cnt, pitch, pier = _m(bays, "count"), _m(bays, "pitch"), _m(bays, "pier_width")
    bay_edges: list[float] = []
    if cnt.known and pitch.known:
        bay_edges, mwarn = schema.bay_edges(bays, L)
        notes += mwarn
        MARGIN_WARNINGS.extend(f"{b.get('id')} {face_id}: {w}" for w in mwarn)
        pw = pier.value if pier.known else 0.0
        if pw > 0:
            for e in bay_edges:
                svg.rect(X(e - pw / 2), TOP, pw * SCALE, Hpx, FILL_PIER, _style(pier), 0.6)
        else:
            for e in bay_edges:
                svg.line(X(e), TOP, X(e), y_ground, 'stroke="#8a6a45"', 0.6)
        if not pier.known:
            unknown_lines.append("柱型の幅（pier_width）")
    elif bays:
        unknown_lines.append("スパン数・スパン（bays）")

    # 開口（ピロティなど）：地面から z_top まで、指定スパンを抜く
    for op in fc.get("openings", []):
        zt = _m(op, "z_top")
        idx = op.get("bays") or []
        if bay_edges and idx:
            for i in idx:
                if 1 <= i <= len(bay_edges) - 1:
                    a, c = bay_edges[i - 1], bay_edges[i]
                    pw = (pier.value if pier.known else 0.0) / 2
                    top_z = zt.value if zt.known else 4.0
                    svg.rect(X(a + pw), Y(top_z), (c - a - 2 * pw) * SCALE, top_z * SCALE, "#fbfaf7", _style(zt), 0.8)
            if zt.known:
                svg.text(X((bay_edges[idx[0] - 1] + bay_edges[idx[-1]]) / 2), Y(zt.value) + 16, op.get("name", "開口"), 11, "#555", "middle")
            else:
                unknown_lines.append(f"{op.get('name', '開口')}の上端の高さ（z_top）")

    # 帯（bands）
    for bd in fc.get("bands", []):
        zb, zt = _m(bd, "z_bottom"), _m(bd, "z_top")
        if zb.known and zt.known:
            svg.rect(x0, Y(zt.value), W, (zt.value - zb.value) * SCALE, FILL_BAND, _style(zb), 0.6)
            rl = bd.get("rail")
            if rl:      # 帯（腰壁）の上の手すり：横の線と支柱
                rh = _m(rl, "height")
                h = float(rh.value) if rh.known else 0.9
                if not rh.known:
                    unknown_lines.append(f"帯『{bd.get('name', '')}』の手すりの高さ")
                a0 = parts_geom.plain(bd.get("along0"), 0.0)
                a1 = parts_geom.plain(bd.get("along1"), L)
                svg.add('<g data-part="rail">')
                for pr in parts_geom.rail_prims("rail", a0, a1, zt.value, h, parts_geom.plain(rl.get("post_pitch"), 1.5), 0.0):
                    q = parts_geom.elevation_outline(pr)
                    svg.add('<polygon points="' + " ".join(f"{X(a):.1f},{Y(z):.1f}" for a, z in q) + f'" fill="#9aa0a6" {_style(rh)} stroke-width="0.4"/>')
                svg.add('</g>')
        else:
            unknown_lines.append(f"帯『{bd.get('name', '')}』の高さ")

    # 階の線
    fcnt, fpitch = _m(b, "floor_count"), _m(b, "floor_pitch")
    if fcnt.known and fpitch.known:
        for k in range(1, int(fcnt.value)):
            z = k * fpitch.value
            if z < H:
                svg.line(x0, Y(z), x0 + W, Y(z), 'stroke="#8a6a45" stroke-dasharray="2 3"', 0.5)
                svg.text(x0 - 6, Y(z) + 4, f"{k + 1}F", 10, "#8a6a45", "end")
    else:
        unknown_lines.append("階数か階高（floor_count / floor_pitch）")

    # 窓
    for w in fc.get("windows", []):
        zb, zt, ww = _m(w, "z_bottom"), _m(w, "z_top"), _m(w, "width")
        per = int(w.get("per_bay", 1) or 1)
        if not (zb.known and zt.known):
            unknown_lines.append(f"窓『{w.get('name', '')}』の上下の高さ")
        zb_v = zb.value if zb.known else (zt.value - 1.5 if zt.known else 1.0)
        zt_v = zt.value if zt.known else zb_v + 1.5
        st = zb if not zb.known else (zt if not zt.known else zb)
        sp = w.get("spandrel")
        sp_h = _m(sp, "height") if sp else None
        if sp and not sp_h.known:
            unknown_lines.append(f"窓『{w.get('name', '')}』の下のパネルの高さ")

        def spandrel(xw: float, wv: float) -> None:     # 窓の下の濃いパネル（windows[].spandrel）
            if sp and sp_h.known:
                svg.rect(X(xw), Y(zb_v), wv * SCALE, float(sp_h.value) * SCALE, FILL_SPANDREL, _style(sp_h), 0.5)
        if bay_edges:
            pw = (pier.value if pier.known else 0.0)
            for i in range(len(bay_edges) - 1):
                a, c = bay_edges[i] + pw / 2, bay_edges[i + 1] - pw / 2
                inner = c - a
                wv = ww.value if ww.known else inner / per * 0.8
                gap = (inner - per * wv) / (per + 1)
                for j in range(per):
                    xw = a + gap * (j + 1) + wv * j
                    svg.rect(X(xw), Y(zt_v), wv * SCALE, (zt_v - zb_v) * SCALE, FILL_WIN, _style(st), 0.7)
                    spandrel(xw, wv)
        else:
            wv = ww.value if ww.known else 2.0
            n = max(1, int(L // (wv * 2)))
            step = L / n
            for i in range(n):
                svg.rect(X(step * i + (step - wv) / 2), Y(zt_v), wv * SCALE, (zt_v - zb_v) * SCALE, FILL_WIN, _style(st), 0.7)
                spandrel(step * i + (step - wv) / 2, wv)

    # 部品。形は parts_geom（模型と同じ）を立面に投影し、奥から順に描く。
    # 面に付く部品はこの面の物だけ。単独の部品（風除室・パーゴラ・渡り廊下・x,y の植え込みなど）は、面より手前（n > 0）に
    # 出ていて面の幅に重なる物を、頂点の凸包で描く
    faces_all = {f["id"]: f for f in b.get("faces", [])}
    fax = parts_geom.frame_axes(face_id, faces_all) if fc.get("from") and fc.get("to") else None
    part_items: list = []
    for i, pt in enumerate(b.get("parts", [])):
        t = pt.get("type")
        if t not in parts_geom.ALL_PART_TYPES:
            continue
        own = pt.get("face") == face_id
        if pt.get("face") and not own:
            continue
        if not own and fax is None:
            continue
        got = parts_geom.part_prims(pt, faces_all)
        if not got or not (got[1] or got[2]):
            if own:
                unknown_lines.append(f"{pt.get('name', t)}（parts[{i}]）の寸法が未確定で描けません")
            continue
        fr, cut, prims = got
        if own:
            items = [(_prim_depth(pr), parts_geom.elevation_outline(pr), pr) for pr in prims]
        else:
            pax = parts_geom.frame_axes(fr, faces_all)
            items = []
            for pr in prims:
                v = parts_geom.to_frame(parts_geom.prim_vertices(pr, pax), fax)
                if max(q[1] for q in v) <= 0.05 or max(q[0] for q in v) <= 0 or min(q[0] for q in v) >= L:
                    continue
                items.append((max(q[1] for q in v), parts_geom.hull([(q[0], q[2]) for q in v]), pr))
            if not items:
                continue
        if cut:     # くり抜き（玄関の軒下）：引っ込みの枠を破線で（一番奥）
            q = parts_geom.elevation_outline(cut)
            part_items.append((-1e9, t, q, None))
        part_items += [(d, t, q, pr) for d, q, pr in items]
    # 部品をまたいで奥から順に（壁の素材の板 patch が外階段の上に描かれないように）
    for _d, t, q, pr in sorted(part_items, key=lambda it: it[0]):
        pts_s = " ".join(f"{X(a):.1f},{Y(z):.1f}" for a, z in q)
        if pr is None:
            svg.add(f'<polygon data-part="{_esc(t)}" points="{pts_s}" fill="{FILL_RECESS}" stroke="#222" stroke-dasharray="5 3" stroke-width="1"/>')
            continue
        op = ' fill-opacity="0.45"' if pr.get("mat") == "glass" else ""
        svg.add(f'<polygon data-part="{_esc(t)}" points="{pts_s}" fill="{_fill(pr.get("mat"))}"{op} stroke="#333" stroke-width="0.6"/>')

    # 地面
    if g != 0.0:
        # 面の前の地面が z=0 と違う：z=0 を細い破線で、実際の地面（面の前）を太い線で描く
        if g > 0:
            svg.rect(x0 - 20, Y(g), W + 40, g * SCALE, "#cfc9bd", 'stroke="none"')       # 地面より下に隠れる壁の根元
        else:
            svg.rect(x0 - 20, Y(g), W + 40, 10, "#cfc9bd", 'stroke="none"')
        svg.line(x0 - 20, y_ground, x0 + W + 20, y_ground, 'stroke="#888" stroke-dasharray="4 3"', 0.8)
        svg.text(x0 + W + 24, y_ground + 4, "z=0（棟の基準）", 10, "#888")
    svg.line(x0 - 20, Y(g), x0 + W + 20, Y(g), 'stroke="#222"', 1.5)

    # 右側の寸法ラベル（高さ順に並べ、重なりを避ける）
    labels: list[tuple[float, str, Measurement]] = []
    if height_m.known:
        labels.append((float(height_m.value), "パラペット上端" if height_m.status == "measured" else "高さ", height_m))
    for lv in b.get("levels", []):
        z = _m(lv, "z")
        if z.known:
            labels.append((float(z.value), lv.get("name", lv.get("id", "")), z))
    for w in fc.get("windows", []):
        for key, nm in (("z_top", "上端"), ("z_bottom", "下端")):
            z = _m(w, key)
            if z.known:
                labels.append((float(z.value), f"{w.get('name', '窓')} {nm}", z))
    for bd in fc.get("bands", []):
        for key, nm in (("z_top", "上端"), ("z_bottom", "下端")):
            z = _m(bd, key)
            if z.known:
                labels.append((float(z.value), f"{bd.get('name', '帯')} {nm}", z))
    for op in fc.get("openings", []):
        z = _m(op, "z_top")
        if z.known:
            labels.append((float(z.value), f"{op.get('name', '開口')} 上端", z))
    # 地面を基準に、下から上へ 14px 以上の間隔で積む（地面のラベルは動かさない）
    if g != 0.0:
        labels.append((g, "地面（面の前）", g_m))
    else:
        labels.append((0.0, "地面", Measurement(0.0, status="measured", evidence=[])))
    labels.sort(key=lambda t: t[0])
    last_y = 1e9
    xl = x0 + W + 26
    for z, name, m in labels:
        y = Y(z)
        ty = min(y, last_y - 14)
        last_y = ty
        svg.line(x0 + W, y, xl - 6, ty, 'stroke="#999"', 0.5)
        svg.text(xl, ty + 4, f"{z:.2f}　{name}　{m.label().split(' ', 1)[1] if ' ' in m.label() else ''}".rstrip(), 11, COL.get(m.status, "#222"))

    # 下の寸法（スパン）
    yb = y_ground + below + 28
    if bay_edges:
        for i in range(len(bay_edges) - 1):
            a, c = bay_edges[i], bay_edges[i + 1]
            svg.line(X(a), yb, X(c), yb, 'stroke="#c8322b"', 0.8)
            svg.line(X(a), yb - 4, X(a), yb + 4, 'stroke="#c8322b"', 0.8)
            svg.text(X((a + c) / 2), yb - 5, f"{pitch.value:.2f}", 10, "#c8322b", "middle")
        svg.line(X(bay_edges[-1]), yb - 4, X(bay_edges[-1]), yb + 4, 'stroke="#c8322b"', 0.8)
        yb += 22
    svg.line(x0, yb, x0 + W, yb, 'stroke="#222"', 1)
    svg.line(x0, yb - 5, x0, yb + 5, 'stroke="#222"', 1)
    svg.line(x0 + W, yb - 5, x0 + W, yb + 5, 'stroke="#222"', 1)
    Ltxt = f"全長 {L:.1f} m" if length_m.known or length_m.status == "estimated" else "全長 未確定"
    if length_m.evidence:
        Ltxt += "  [" + ",".join(length_m.evidence) + "]"
    if length_m.status == "estimated":
        Ltxt += "（推定）"
    svg.text(x0 + W / 2, yb + 16, Ltxt, 11, COL.get(length_m.status, "#222"), "middle")

    # 未確定の一覧と撮影依頼
    ylist = yb + 44
    reqs = [r for r in b.get("requests", []) if r.get("face") in (face_id, "*") and r.get("status", "open") == "open"]
    if not length_m.known:
        unknown_lines.insert(0, "面の長さ（length）")
    if not height_m.known:   # estimated は正規の値（未確定には入れない。琥珀の破線で描く）
        unknown_lines.insert(0, "パラペットの上端（parapet_top）")
    seen: set[str] = set()
    uniq = [u for u in unknown_lines if not (u in seen or seen.add(u))]
    if uniq or reqs or notes:
        svg.text(LEFT, ylist, "未確定・撮影のお願い", 12, "#c8322b", weight="bold")
        ylist += 16
        for u in uniq:
            svg.text(LEFT, ylist, f"・{u}", 11, "#c8322b")
            ylist += 14
        for n in notes:
            svg.text(LEFT, ylist, f"・{n}", 11, "#b07a10")
            ylist += 14
        for r in reqs:
            where = f"（{r['where']}）" if r.get("where") else ""
            svg.text(LEFT, ylist, f"▶ {r.get('what', '')}{where}：{r.get('why', '')}", 11, "#c8322b")
            ylist += 14

    # 根拠の写真
    ev: list[str] = []
    for key in ("length",):
        ev += _m(fc, key).evidence
    for sect in (fc.get("bays") or {}).values() if isinstance(fc.get("bays"), dict) else []:
        if isinstance(sect, dict):
            ev += sect.get("evidence", [])
    for grp in ("windows", "bands", "openings", "materials"):
        for it in fc.get(grp, []):
            for v in it.values():
                if isinstance(v, dict) and "evidence" in v:
                    ev += v["evidence"]
    ev = sorted(set(ev))
    if ev:
        ylist += 6
        svg.text(LEFT, ylist, "根拠の写真: " + ", ".join(ev), 11, "#555")
        ylist += 14

    total_w = max(LEFT + W + RIGHT_LABELS, 720)
    total_h = ylist + BOTTOM
    return svg.render(total_w, total_h)


def plan_svg(b: dict) -> str:
    fp = b.get("footprint") or {}
    poly: list[list[float]] = fp.get("polygon") or []
    st = fp.get("status", "unknown")
    svg = _Svg()
    svg.text(LEFT, 34, f"{b.get('name', '')}　平面図（足元の外形）", 18, weight="bold")
    svg.text(LEFT, 56, "座標は原点からの m（x=東, y=北）。面の名前は faces の id。", 11, "#666")
    if len(poly) < 3:
        svg.text(LEFT, 110, "外形は未確定です。航空写真の屋上の外形（倒れ込み補正）か、写真から決めます。", 13, "#c8322b")
        return svg.render(720, 160)
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    span = max(maxx - minx, maxy - miny, 1.0)
    sc = min(560 / span, SCALE * 1.5)
    ox, oy = LEFT + 40, TOP + 40 + (maxy - miny) * sc

    def P(p: list[float]) -> tuple[float, float]:
        return ox + (p[0] - minx) * sc, oy - (p[1] - miny) * sc

    pts = " ".join(f"{P(p)[0]:.1f},{P(p)[1]:.1f}" for p in poly)
    col = COL.get(st, "#c8322b")
    dash = f' stroke-dasharray="{DASH.get(st, "6 4")}"' if DASH.get(st) else ""
    svg.add(f'<polygon points="{pts}" fill="{FILL_WALL}" stroke="{col}"{dash} stroke-width="1.5"/>')
    for i, p in enumerate(poly):
        q = poly[(i + 1) % len(poly)]
        Lm = math.hypot(q[0] - p[0], q[1] - p[1])
        mx, my = (P(p)[0] + P(q)[0]) / 2, (P(p)[1] + P(q)[1]) / 2
        nx, ny = (q[1] - p[1]) / Lm, -(q[0] - p[0]) / Lm  # 外向き（時計回りなら）
        svg.text(mx - nx * 16, my + ny * 16 + 4, f"{Lm:.1f}", 10, "#444", "middle")
    for fc in b.get("faces", []):
        a, c = fc.get("from"), fc.get("to")
        if a and c:
            ax, ay = P(a)
            cx, cy = P(c)
            svg.line(ax, ay, cx, cy, 'stroke="#185fa5"', 3)
            svg.text((ax + cx) / 2, (ay + cy) / 2 - 8, fc.get("id", ""), 11, "#185fa5", "middle", "bold")
    # 単独の部品（柱・屋上の設備・風除室・パーゴラ・渡り廊下・x,y の植え込みや銘板）。頂点を平面に落とした凸包
    for pt in b.get("parts", []):
        t = pt.get("type")
        if t not in parts_geom.WORLD_PART_TYPES and not (t in parts_geom.FLEX_PART_TYPES and not pt.get("face")):
            continue
        got = parts_geom.part_prims(pt, {})
        if not got:
            continue
        pax = parts_geom.frame_axes(got[0], {})
        dash = ' stroke-dasharray="4 3"' if t == "rooftop_units" else ""
        for pr in got[2]:
            q2 = pr["pts"] if (got[0] == "world" and pr["k"] == "prism") else parts_geom.hull([(v[0], v[1]) for v in parts_geom.prim_vertices(pr, pax)])
            pp = " ".join(f"{P(q)[0]:.1f},{P(q)[1]:.1f}" for q in q2)
            svg.add(f'<polygon data-part="{_esc(t)}" points="{pp}" fill="{_fill(pr.get("mat")) if t not in ("column", "rooftop_units") else "#9aa0a6"}" fill-opacity="0.6" stroke="#333"{dash} stroke-width="0.8"/>')
    # 北
    nxp, nyp = LEFT + 40 + (maxx - minx) * sc + 60, TOP + 60
    svg.line(nxp, nyp + 30, nxp, nyp, 'stroke="#222"', 1.2)
    svg.add(f'<polygon points="{nxp - 5:.1f},{nyp + 8:.1f} {nxp + 5:.1f},{nyp + 8:.1f} {nxp:.1f},{nyp:.1f}" fill="#222"/>')
    svg.text(nxp, nyp - 6, "N", 11, "#222", "middle")
    evid = ", ".join(fp.get("evidence", []))
    svg.text(LEFT, oy + 30, f"外形: {st}" + (f"　根拠: {evid}" if evid else "") + (f"　方法: {fp['method']}" if fp.get("method") else ""), 11, col)
    return svg.render(max(720, ox + (maxx - minx) * sc + 160), oy + 70)


def all_drawings(b: dict) -> dict[str, str]:
    out = {"plan": plan_svg(b)}
    for fc in b.get("faces", []):
        out[f"elev_{fc['id']}"] = elevation_svg(b, fc["id"])
    return out
