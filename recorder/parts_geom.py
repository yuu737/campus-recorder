"""部品の形（幾何）を棟 JSON から作る。bpy を使わない純 Python。

模型（blender_build.py）と立面図（drawing.py）が同じ形を使うための共通の場所。テストもここを直接呼ぶ。
Blender の中からも `import parts_geom` で読めるよう、相対 import はしない。

形は「素片（prim）」の並びで返す。素片は dict:
    {"k": "prism", "pts": [(a, n), ...], "z0", "z1"}   平面の多角形を z0〜z1 に持ち上げた柱体
    {"k": "ext_a", "pts": [(a, z), ...], "n0", "n1"}   面に平行な断面（a-z）を n0〜n1 の厚みに押し出した物（斜めの段・腰壁・かまぼこ庇）
    {"k": "ext_n", "pts": [(n, z), ...], "a0", "a1"}   面に直角な断面（n-z）を a0〜a1 に押し出した物（横の手すり棒）
共通の鍵: "name"（部品の中の名前）、"mat"（素材の名前。"tile" は面の仕上げ）。

座標
    面に付く部品：a＝面の始点（from）から面に沿った m、n＝面から外向きの m（壁の外面が n=0、壁の中は負）、z＝棟の z=0 からの m
    単独の部品（column / rooftop_units）：a＝x（東）、n＝y（北）、z。frame は "world"

値の取り方
    V(m, default, what, why) で Measurement（か裸の数）から値を取る。unknown なら default。
    模型では V に「仮の値を report に記録する」関数を渡す（blender_build.val）。図面とテストは plain を使う。
"""

from __future__ import annotations

import math


def plain(m, default=None, what: str = "", why: str = ""):
    if isinstance(m, (int, float)) and not isinstance(m, bool):
        return m
    if isinstance(m, dict) and m.get("value") is not None and m.get("status") != "unknown":
        return m["value"]
    return default


def face_ground(face: dict | None) -> float:
    """面の前の地面の高さ（faces[].ground_offset。無い・unknown は 0）。schema.face_ground_offset と同じ規則。"""
    g = plain((face or {}).get("ground_offset"), 0.0)
    return float(g or 0.0)


# --- 素片の道具 -------------------------------------------------------------------

def P(k, name, pts, lo, hi, mat="tile"):
    key = {"prism": ("z0", "z1"), "ext_a": ("n0", "n1"), "ext_n": ("a0", "a1")}[k]
    return {"k": k, "name": name, "pts": [tuple(map(float, p)) for p in pts], key[0]: float(lo), key[1]: float(hi), "mat": mat}


def rect_prism(name, a0, a1, n0, n1, z0, z1, mat="tile"):
    return P("prism", name, [(a0, n0), (a1, n0), (a1, n1), (a0, n1)], z0, z1, mat)


def circle_pts(ca, cn, r, seg=16):
    return [(ca + r * math.cos(2 * math.pi * i / seg), cn + r * math.sin(2 * math.pi * i / seg)) for i in range(seg)]


def area(pts) -> float:
    n = len(pts)
    return abs(sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))) / 2


def volume(p: dict) -> float:
    a = area(p["pts"])
    if p["k"] == "prism":
        return a * (p["z1"] - p["z0"])
    if p["k"] == "ext_a":
        return a * (p["n1"] - p["n0"])
    return a * (p["a1"] - p["a0"])


def elevation_outline(p: dict) -> list[tuple[float, float]]:
    """立面（a-z）に投影した輪郭。ext_a は断面そのもの（斜めの段は斜めの線になる）。"""
    if p["k"] == "ext_a":
        return list(p["pts"])
    if p["k"] == "prism":
        a = [q[0] for q in p["pts"]]
        return [(min(a), p["z0"]), (max(a), p["z0"]), (max(a), p["z1"]), (min(a), p["z1"])]
    z = [q[1] for q in p["pts"]]
    return [(p["a0"], min(z)), (p["a1"], min(z)), (p["a1"], max(z)), (p["a0"], max(z))]


def dedupe(pts, eps=1e-6):
    out = []
    for q in pts:
        if not out or abs(q[0] - out[-1][0]) > eps or abs(q[1] - out[-1][1]) > eps:
            out.append(q)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) <= eps and abs(out[0][1] - out[-1][1]) <= eps:
        out.pop()
    return out


# --- 手すり（rail） ---------------------------------------------------------------

RAIL_R = 0.022        # 手すりの棒の半径（見た目の太さ。寸法の主張ではない）


def rail_prims(name, a0, a1, z, height, post_pitch, n, mat="metal", z1=None):
    """帯・腰壁の上の手すり。支柱（細い円柱）を post_pitch 以下の等間隔で両端を含めて立て、上に横棒。
    z1 を渡すと a0→a1 で z→z1 に上がる斜めの手すり（階段）。"""
    z1 = z if z1 is None else z1
    run = a1 - a0
    cnt = max(1, math.ceil(abs(run) / post_pitch - 1e-9)) if post_pitch and post_pitch > 0 else 1
    out = []
    for i in range(cnt + 1):
        a = a0 + run * i / cnt
        zb = z + (z1 - z) * i / cnt
        out.append(P("prism", f"{name}_post{i}", circle_pts(a, n, RAIL_R, 8), zb, zb + height, mat))
    t = RAIL_R * 2
    top0, top1 = z + height, z1 + height
    out.append(P("ext_a", f"{name}_bar", [(a0, top0 - t), (a1, top1 - t), (a1, top1), (a0, top0)], n - t / 2, n + t / 2, mat))
    return out


# --- 折り返しの外階段（stair_switchback） -----------------------------------------

def stair_landings_z(pt: dict, z0: float, V=plain, tag="stair"):
    """踊り場の高さの並び（下から）。landing_z（各踊り場の z）か、floors（層数）× floor_pitch（中間の踊り場あり）。"""
    if pt.get("landing_z"):
        zs = [V(m) for m in pt["landing_z"]]
        if any(z is None for z in zs):
            V(None, None, f"{tag} の踊り場の高さ", "landing_z に unknown がある。階段を置かなかった")
            return None
        return [float(z) for z in zs]
    floors = V(pt.get("floors"), None, f"{tag} の層数", "floors が unknown。階段を置かなかった")
    pitch = V(pt.get("floor_pitch"), None, f"{tag} の階高", "floor_pitch が unknown。階段を置かなかった")
    if floors is None or pitch is None:
        return None
    if pt.get("mid_landings", True):
        return [z0 + k * pitch / 2 for k in range(1, 2 * int(floors) + 1)]
    return [z0 + k * pitch for k in range(1, int(floors) + 1)]


def stair_switchback(pt: dict, face: dict | None, V=plain, tag="stair_switchback") -> list[dict]:
    """折り返しの外階段。面の外（n=0〜depth）に、along〜along+width の範囲で置く。

    両端に踊り場（長さ landing_len、奥行き depth 全体）、間を斜めの段（傾いた板、段は刻まない）が往復する。
    斜めの段は 2 つの通路（外側 n=depth−幅〜depth、内側 0〜幅）を交互に使う。1 本目は first_end（既定 "to"）
    の端へ first_lane（既定 "outer"）で上がる。腰壁は外側の通路の斜めの帯と、踊り場の外周。"""
    A = V(pt.get("along"), 0.0)
    W = V(pt.get("width"), None, f"{tag} の幅", "width が unknown。階段を置かなかった")
    D = V(pt.get("depth"), None, f"{tag} の奥行き", "depth が unknown。階段を置かなかった")
    if W is None or D is None:
        return []
    g = face_ground(face)
    z0 = float(V(pt.get("z0"), g))
    Z = stair_landings_z(pt, z0, V, tag)
    if not Z:
        return []
    ll = float(V(pt.get("landing_len"), min(1.5, W / 4), f"{tag} の踊り場の長さ", "未測。min(1.5 m, 幅/4) と置いた"))
    lane = float(V(pt.get("flight_width"), D / 2))
    slab = float(V(pt.get("slab"), 0.25))
    ends = {"from": (A, A + ll), "to": (A + W - ll, A + W)}
    lanes = {"outer": (D - lane, D), "inner": (0.0, lane)}
    other = {"from": "to", "to": "from", "outer": "inner", "inner": "outer"}
    a_lo, a_hi = A + ll, A + W - ll
    par = pt.get("parapet")
    ph = float(V((par or {}).get("height"), 1.1, f"{tag} の腰壁の高さ", "未測。建築基準法の最低 1.1 m")) if par else 0.0
    pthk = float(V((par or {}).get("thickness"), 0.15)) if par else 0.0
    pmat = (par or {}).get("material", "tile")
    rail = pt.get("rail")
    rh = float(V((rail or {}).get("height"), 0.15)) if rail else 0.0
    rp = float(V((rail or {}).get("post_pitch"), 1.5)) if rail else 0.0
    smat = pt.get("material", "concrete")

    out: list[dict] = []
    prev = z0
    end, ln = pt.get("first_end", "to"), pt.get("first_lane", "outer")
    flights = []      # (lane, z_at_a_lo, z_at_a_hi)
    landings = []     # (a0, a1, z)
    for i, z in enumerate(Z):
        if end == "to":
            zl, zh = prev, z
        else:
            zl, zh = z, prev
        n0, n1 = lanes[ln]
        out.append(P("ext_a", f"flight{i}", [(a_lo, zl - slab), (a_hi, zh - slab), (a_hi, zh), (a_lo, zl)], n0, n1, smat))
        flights.append((ln, zl, zh))
        la0, la1 = ends[end]
        out.append(rect_prism(f"landing{i}", la0, la1, 0.0, D, z - slab, z, smat))
        landings.append((la0, la1, z))
        if par and ln == "outer":
            out.append(P("ext_a", f"flight{i}_parapet", [(a_lo, zl - slab), (a_hi, zh - slab), (a_hi, zh + ph), (a_lo, zl + ph)], D - pthk, D, pmat))
            if rail:
                out += rail_prims(f"flight{i}_rail", a_lo, a_hi, zl + ph, rh, rp, D - pthk / 2, z1=zh + ph)
        if par:
            out.append(rect_prism(f"landing{i}_parapet", la0, la1, D - pthk, D, z - slab, z + ph, pmat))
            ea0, ea1 = (la0, la0 + pthk) if end == "from" else (la1 - pthk, la1)
            out.append(rect_prism(f"landing{i}_end", ea0, ea1, 0.0, D - pthk, z - slab, z + ph, pmat))
            if rail:
                out += rail_prims(f"landing{i}_rail", la0, la1, z + ph, rh, rp, D - pthk / 2)
        prev = z
        end, ln = other[end], other[ln]

    def underside(a: float) -> float | None:
        c = [z - slab for la0, la1, z in landings if la0 - 1e-6 <= a <= la1 + 1e-6]
        for lnm, zl, zh in flights:
            if lnm == "outer" and a_lo <= a <= a_hi and a_hi > a_lo:
                c.append(zl + (zh - zl) * (a - a_lo) / (a_hi - a_lo) - slab)
        c = [x for x in c if x > z0 + 0.5]
        return min(c) if c else None

    pil = pt.get("pilotis")
    if pil:
        size = float(V(pil.get("size") or pil.get("diameter"), 0.5, f"{tag} のピロティの柱の太さ", "未測。0.5 m と置いた"))
        if pil.get("along"):
            pos = [A + float(V(m, 0.0)) for m in pil["along"]]
        else:
            cnt = int(V(pil.get("count"), 2))
            pos = [A + size / 2 + (W - size) * k / max(1, cnt - 1) for k in range(cnt)] if cnt > 1 else [A + size / 2]
        for k, a in enumerate(pos):
            top = underside(a)
            if top is None:
                continue
            if pil.get("shape", "square") == "round":
                out.append(P("prism", f"pilotis{k}", circle_pts(a, D - size / 2, size / 2), z0, top, pil.get("material", "tile")))
            else:
                out.append(rect_prism(f"pilotis{k}", a - size / 2, a + size / 2, D - size, D, z0, top, pil.get("material", "tile")))

    eb = pt.get("exit_box")
    top_z = Z[-1]
    box_rng = None
    canopy_base = None
    if eb is not None:
        la0, la1, _ = landings[-1]
        e0 = A + float(V(eb.get("along0"), la0 - A))
        e1 = A + float(V(eb.get("along1"), la1 - A))
        ed = float(V(eb.get("depth"), D))
        eh = float(V(eb.get("height"), 2.5, f"{tag} の出口の箱の高さ", "未測。2.5 m と置いた"))
        out.append(rect_prism("exit_box", e0, e1, 0.0, ed, top_z, top_z + eh, eb.get("material", "concrete")))
        box_rng = (e0, e1, ed)
        canopy_base = top_z + eh
    cp = pt.get("canopy")
    if cp is not None and cp.get("shape", "barrel") == "barrel":
        if box_rng is None:
            la0, la1, _ = landings[-1]
            box_rng = (la0, la1, D)
            canopy_base = top_z + float(V(cp.get("z"), 2.5, f"{tag} のかまぼこ庇の高さ", "出口の箱が無い。最上の踊り場＋2.5 m と置いた"))
        e0, e1, ed = box_rng
        ov = float(V(cp.get("overhang"), 0.1))
        hw = (e1 - e0) / 2 + ov
        rise = float(V(cp.get("rise"), hw, f"{tag} のかまぼこ庇の高さ（rise）", "未測。半円（幅の半分）と置いた"))
        t = float(V(cp.get("thickness"), 0.05))
        out.append(barrel(f"canopy", (e0 + e1) / 2, hw, rise, t, canopy_base, -0.0, ed + ov, cp.get("material", "canopy")))
    return out


def barrel(name, ca, hw, rise, t, zb, n0, n1, mat="canopy", seg=12):
    """かまぼこ（半楕円の殻）。断面は a-z、軸は面に直角（n）。"""
    outer = [(ca + hw * math.cos(math.pi * (1 - i / seg)), zb + rise * math.sin(math.pi * (1 - i / seg))) for i in range(seg + 1)]
    hi, ri = max(hw - t, 0.01), max(rise - t, 0.01)
    inner = [(ca + hi * math.cos(math.pi * i / seg), zb + ri * math.sin(math.pi * i / seg)) for i in range(seg + 1)]
    return P("ext_a", name, outer + inner, n0, n1, mat)


# --- 玄関の軒下（porch） ----------------------------------------------------------

def porch(pt: dict, face: dict | None, V=plain, tag="porch"):
    """壁を along〜along+width、奥へ depth、地面から height までくり抜く（返す cut を面ごとのブーリアンで引く）。
    内側に格子付きのガラスの箱（glass）、開口の左右にレンガの柱（columns）、天井に軒裏の板。
    返り値: (cut 素片 or None, 置く素片の並び)"""
    A = V(pt.get("along"), 0.0)
    W = V(pt.get("width"), None, f"{tag} の幅", "width が unknown。置かなかった")
    D = V(pt.get("depth"), None, f"{tag} の奥行き", "depth が unknown。置かなかった")
    H = V(pt.get("height"), None, f"{tag} の高さ", "height が unknown。置かなかった")
    if W is None or D is None or H is None:
        return None, []
    z0 = float(V(pt.get("z0"), face_ground(face)))
    top = z0 + H
    cut = rect_prism("cut", A, A + W, -D, 0.25, z0 - 0.02, top, None)
    out = [rect_prism("soffit", A, A + W, -D, 0.0, top - 0.04, top - 0.01, pt.get("soffit_material", "dark"))]
    col = pt.get("columns")
    if col:
        cw = float(V(col.get("width"), 0.6, f"{tag} の柱の幅", "未測。0.6 m と置いた"))
        for k, a in enumerate((A + cw / 2, A + W - cw / 2)):
            out.append(rect_prism(f"column{k}", a - cw / 2, a + cw / 2, -cw, 0.0, z0, top, col.get("material", "tile")))
    gl = pt.get("glass")
    if gl:
        g0 = A + float(V(gl.get("along0"), 0.0))
        g1 = A + float(V(gl.get("along1"), W))
        gd = float(V(gl.get("depth"), D / 2, f"{tag} のガラスの箱の奥行き", "未測。軒下の奥行きの半分と置いた"))
        gh = float(V(gl.get("height"), H - 0.05))
        nb, nf = -D, -D + gd
        out.append(rect_prism("glass", g0, g1, nb, nf, z0, z0 + gh, "glass"))
        mp = float(V(gl.get("mullion_pitch"), 1.2, f"{tag} の縦の格子の間隔", "未測。1.2 m と置いた"))
        tp = V(gl.get("transom_pitch"))
        f = 0.05
        # 正面の縦桟（両端を含む等間隔）
        cnt = max(1, math.ceil((g1 - g0) / mp - 1e-9))
        for i in range(cnt + 1):
            a = g0 + (g1 - g0) * i / cnt
            out.append(rect_prism(f"mullion{i}", a - f / 2, a + f / 2, nf - f / 2, nf + f / 2, z0, z0 + gh, "metal"))
        # 横の面（左右の端）の縦桟
        cs = max(1, math.ceil(gd / mp - 1e-9))
        for i in range(1, cs):
            nn = nb + gd * i / cs
            for side, a in (("L", g0), ("R", g1)):
                out.append(rect_prism(f"mullion{side}{i}", a - f / 2, a + f / 2, nn - f / 2, nn + f / 2, z0, z0 + gh, "metal"))
        if tp:
            k = 1
            while z0 + k * tp < z0 + gh - 0.05:
                zz = z0 + k * tp
                out.append(rect_prism(f"transom{k}", g0, g1, nf - f / 2, nf + f / 2, zz - f / 2, zz + f / 2, "metal"))
                k += 1
    return cut, out


# --- 段の塊（steps） --------------------------------------------------------------

def steps(pt: dict, face: dict | None, V=plain, tag="steps") -> list[dict]:
    """面の前の段。along〜along+width、面から外へ depth。count 段、1 段 rise。下の段ほど外へ広い。
    1 段の奥行きは tread（無ければ depth/count）。sides に "from" / "to" があれば、その端も 1 段ずつ回り込む（L 字）。
    edge: "curved" は前の縁を半径 radius の円弧に（中心は幅の中央、段ごとに同心で 1 段ずつ小さく）。"""
    A = V(pt.get("along"), 0.0)
    W = V(pt.get("width"), None, f"{tag} の幅", "width が unknown。置かなかった")
    D = V(pt.get("depth"), None, f"{tag} の奥行き", "depth が unknown。置かなかった")
    cnt = V(pt.get("count"), None, f"{tag} の段数", "count が unknown。置かなかった")
    rise = V(pt.get("rise"), None, f"{tag} の 1 段の高さ", "rise が unknown。置かなかった")
    if None in (W, D, cnt, rise):
        return []
    cnt = int(cnt)
    tread = float(V(pt.get("tread"), D / cnt))
    z0 = float(V(pt.get("z0"), face_ground(face)))
    sides = pt.get("sides") or []
    curved = pt.get("edge", "straight") == "curved"
    R = float(V(pt.get("radius"), max(W / 2, D), f"{tag} の縁の半径", "未測。幅の半分か奥行きの大きい方と置いた")) if curved else 0.0
    mat = pt.get("material", "brick")
    out = []
    for k in range(cnt):
        d = D - k * tread
        if d <= 0:
            break
        a0 = A + (k * tread if "from" in sides else 0.0)
        a1 = A + W - (k * tread if "to" in sides else 0.0)
        zb, zt = z0 + k * rise, z0 + (k + 1) * rise
        if not curved:
            out.append(rect_prism(f"step{k}", a0, a1, 0.0, d, zb, zt, mat))
            continue
        r = R - k * tread
        ca, cn = A + W / 2, D - R          # 円の中心（全段で共通＝同心）
        seg = 24
        arc = []
        for i in range(seg + 1):
            a = a0 + (a1 - a0) * i / seg
            dx = a - ca
            nn = cn + math.sqrt(r * r - dx * dx) if r * r > dx * dx else 0.0
            arc.append((a, max(0.0, min(d, nn))))
        pts = dedupe([(a0, 0.0), (a1, 0.0)] + list(reversed(arc)))     # 壁ぎわの辺 → 前の縁の円弧（to から from へ）
        if area(pts) < 1e-4:
            continue
        out.append(P("prism", f"step{k}", pts, zb, zt, mat))
    return out


# --- 窓の下のパネル（windows[].spandrel） -----------------------------------------

def spandrel(sp: dict, s: float, wsub: float, zb: float, base: float, V=plain, tag="spandrel"):
    """窓（中心 s、幅 wsub、下端 zb）の下の濃いパネル。壁の面（帯の引っ込みの中なら -base）から外へ depth（0 なら薄い板）。"""
    h = V(sp.get("height"), None, f"{tag} の高さ", "height が unknown。置かなかった")
    if h is None:
        return None
    dep = float(V(sp.get("depth"), 0.0))
    t = max(dep, 0.02)
    return rect_prism("spandrel", s - wsub / 2, s + wsub / 2, -base, -base + t, zb - h, zb, sp.get("material", "dark"))


# --- 単独の柱（column）・屋上の設備（rooftop_units） ------------------------------

def column(pt: dict, V=plain, tag="column"):
    """柱割りから外れた単独の柱。世界座標（a=x, n=y）。"""
    x, y = pt.get("x"), pt.get("y")
    z1 = V(pt.get("z1"), None, f"{tag} の上端", "z1 が unknown。置かなかった")
    if x is None or y is None or z1 is None:
        return None
    z0 = float(V(pt.get("z0"), 0.0))
    dia = float(V(pt.get("diameter") or pt.get("size"), 0.6, f"{tag} の太さ", "未測。0.6 m と置いた"))
    mat = pt.get("material", "concrete")
    if pt.get("section", "round") == "square":
        return rect_prism("column", x - dia / 2, x + dia / 2, y - dia / 2, y + dia / 2, z0, z1, mat)
    return P("prism", "column", circle_pts(x, y, dia / 2, 20), z0, z1, mat)


def rooftop_units(pt: dict, V=plain, tag="rooftop_units") -> list[dict]:
    """屋上の設備。units[] は polygon（[[x,y],..]）か center＋w×d の箱、それぞれ高さ h。
    位置は航空写真で読んだまま（source: "aerial"）。lean_correction_m（[dx,dy]、Measurement）を足して屋上の本当の位置に戻す。
    z0 は屋上の高さ（unit ごとに z0 があればそれ）。"""
    lc = V(pt.get("lean_correction_m"), [0.0, 0.0])
    dx, dy = (float(lc[0]), float(lc[1])) if lc else (0.0, 0.0)
    z0 = V(pt.get("z0"), None, f"{tag} の置く高さ（屋上）", "z0 が unknown。置かなかった")
    out = []
    for i, u in enumerate(pt.get("units") or []):
        zb = V(u.get("z0"), z0)
        if zb is None:
            continue
        h = float(V(u.get("h"), 1.5, f"{tag}[{i}] の高さ", "未測。1.5 m と置いた"))
        if u.get("polygon"):
            pts = [(p[0] + dx, p[1] + dy) for p in u["polygon"]]
        elif u.get("center"):
            w = V(u.get("w"), None, f"{tag}[{i}] の幅", "w が unknown。置かなかった")
            d = V(u.get("d"), None, f"{tag}[{i}] の奥行き", "d が unknown。置かなかった")
            if w is None or d is None:
                continue
            cx, cy = u["center"][0] + dx, u["center"][1] + dy
            pts = [(cx - w / 2, cy - d / 2), (cx + w / 2, cy - d / 2), (cx + w / 2, cy + d / 2), (cx - w / 2, cy + d / 2)]
        else:
            continue
        out.append(P("prism", f"unit{i}", pts, zb, zb + h, u.get("material", "metal")))
    return out


def ac_units_base(pt: dict, face: dict | None, V=plain) -> float:
    """室外機の下端。z0 があればそれ（壁付け）、無ければ面の前の地面＋0.1（地面の板に埋まらないように）。"""
    z0 = V(pt.get("z0"))
    return float(z0) if z0 is not None else face_ground(face) + 0.1


# --- 部品の type → 形 ------------------------------------------------------------

FACE_PART_TYPES = ("stair_switchback", "porch", "steps", "rail")
WORLD_PART_TYPES = ("column", "rooftop_units")


def part_prims(pt: dict, faces: dict, V=plain, tag: str | None = None):
    """新しい部品 1 つの形。返り値: (frame, cut, prims)。frame は面 id か "world"。対象外の type は None。"""
    t = pt.get("type")
    tag = tag or t
    if t in FACE_PART_TYPES:
        f = faces.get(pt.get("face"))
        if not f:
            return None
        if t == "stair_switchback":
            return pt["face"], None, stair_switchback(pt, f, V, tag)
        if t == "porch":
            cut, prims = porch(pt, f, V, tag)
            return pt["face"], cut, prims
        if t == "steps":
            return pt["face"], None, steps(pt, f, V, tag)
        a0, a1 = V(pt.get("along0"), 0.0), V(pt.get("along1"))
        z, h = V(pt.get("z")), V(pt.get("height"), 1.1, f"{tag} の高さ", "未測。1.1 m と置いた")
        if a1 is None or z is None:
            return None
        return pt["face"], None, rail_prims("rail", a0, a1, z, h, V(pt.get("post_pitch"), 1.5), V(pt.get("offset"), 0.1))
    if t == "column":
        c = column(pt, V, tag)
        return "world", None, [c] if c else []
    if t == "rooftop_units":
        return "world", None, rooftop_units(pt, V, tag)
    return None
