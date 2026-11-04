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
    ll = V(pt.get("landing_len"), None)
    if ll is None and not ("landing_len_from" in pt and "landing_len_to" in pt):
        ll = V(None, min(1.5, W / 4), f"{tag} の踊り場の長さ", "未測。min(1.5 m, 幅/4) と置いた")
    # 両端で長さが違う踊り場（landing_len_from / landing_len_to）。無ければ landing_len
    dflt = ll if ll is not None else min(1.5, W / 4)
    llf = float(V(pt.get("landing_len_from"), dflt))
    llt = float(V(pt.get("landing_len_to"), dflt))
    lane = float(V(pt.get("flight_width"), D / 2))
    slab = float(V(pt.get("slab"), 0.25))
    ends = {"from": (A, A + llf), "to": (A + W - llt, A + W)}
    lanes = {"outer": (D - lane, D), "inner": (0.0, lane)}
    other = {"from": "to", "to": "from", "outer": "inner", "inner": "outer"}
    a_lo, a_hi = A + llf, A + W - llt
    end_par = pt.get("end_parapet", ["from", "to"])      # 端の腰壁を立てる側（隣に踊り場・床が続く側は外す）
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
            if end in end_par:
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
        # ガラスの箱は奥の壁から 0.05 m 離し、奥の壁に室内の暗い板（透けるガラスの向こうがタイルにならないように）
        out.append(rect_prism("glass", g0, g1, nb + 0.05, nf, z0, z0 + gh, "glass"))
        out.append(rect_prism("interior", g0, g1, nb + 0.002, nb + 0.008, z0, z0 + gh, "interior"))
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

def spandrel(sp: dict, s: float, wsub: float, zb: float, base: float, V=plain, tag="spandrel", rec: float = 0.0):
    """窓（中心 s、幅 wsub、下端 zb）の下の濃いパネル。壁の面（帯の引っ込みの中なら -base）から外へ depth（0 なら薄い板）。
    in_recess: true なら窓の奥まり（rec）の中に置く：ガラスと同じ奥 −(base+rec) から手前へ depth。模型は窓の彫り込みを
    パネルの下端まで下げる（spandrel_cut_bottom）。"""
    h = V(sp.get("height"), None, f"{tag} の高さ", "height が unknown。置かなかった")
    if h is None:
        return None
    dep = float(V(sp.get("depth"), 0.0))
    t = max(dep, 0.02)
    n0 = -(base + rec) if sp.get("in_recess") else -base
    return rect_prism("spandrel", s - wsub / 2, s + wsub / 2, n0, n0 + t, zb - h, zb, sp.get("material", "dark"))


def spandrel_cut_bottom(sp: dict | None, zb: float, V=plain) -> float:
    """窓の彫り込みの下端。奥まりの中のパネル（in_recess）があればパネルの下端まで、無ければ窓の下端。"""
    if sp and sp.get("in_recess"):
        h = V(sp.get("height"))
        if h is not None:
            return zb - float(h)
    return zb


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


# --- 11/4 午後の語彙（本部棟で「語彙が無い」と見送られた物） ---------------------

def spaced(lo: float, hi: float, pitch: float | None) -> list[float]:
    """lo〜hi を pitch 以下の等間隔で、両端を含めて割った位置。"""
    if not pitch or pitch <= 0 or hi - lo <= 1e-9:
        return [lo, hi] if hi - lo > 1e-9 else [lo]
    cnt = max(1, math.ceil((hi - lo) / pitch - 1e-9))
    return [lo + (hi - lo) * i / cnt for i in range(cnt + 1)]


def cells(lo: float, hi: float, pitch: float) -> list[float]:
    """lo〜hi を間隔 pitch に近い個数の区画に割り、各区画の中心（羽根・梁を区画の真ん中に置く時）。"""
    cnt = max(1, round((hi - lo) / pitch)) if pitch and pitch > 0 else 1
    return [lo + (hi - lo) * (k + 0.5) / cnt for k in range(cnt)]


def ccw_pts(pts):
    pts = [tuple(map(float, p)) for p in pts]
    a2 = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
    return pts if a2 >= 0 else list(reversed(pts))


def inset_polygon(pts, d: float):
    """多角形を内側へ d（負なら外側へ）ずらした多角形。各辺を平行にずらし、隣の辺との交点を取る（単純な多角形向け）。"""
    pts = ccw_pts(pts)
    n = len(pts)
    lines = []
    for i in range(n):
        (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
        L = math.hypot(x1 - x0, y1 - y0) or 1.0
        nx, ny = -(y1 - y0) / L, (x1 - x0) / L         # 反時計回りの内向き
        lines.append(((x0 + nx * d, y0 + ny * d), ((x1 - x0) / L, (y1 - y0) / L)))
    out = []
    for i in range(n):
        (p, u), (q, v) = lines[i - 1], lines[i]
        den = u[0] * v[1] - u[1] * v[0]
        if abs(den) < 1e-9:
            out.append(q)
            continue
        t = ((q[0] - p[0]) * v[1] - (q[1] - p[1]) * v[0]) / den
        out.append((p[0] + u[0] * t, p[1] + u[1] * t))
    return out


def edge_quad(p, q, t: float):
    """平面の線分 p→q を太さ t（中心線の両側 t/2）の四角にした点の並び。"""
    L = math.hypot(q[0] - p[0], q[1] - p[1]) or 1.0
    nx, ny = -(q[1] - p[1]) / L * t / 2, (q[0] - p[0]) / L * t / 2
    return [(p[0] - nx, p[1] - ny), (q[0] - nx, q[1] - ny), (q[0] + nx, q[1] + ny), (p[0] + nx, p[1] + ny)]


def square_pts(c, s: float):
    return [(c[0] - s / 2, c[1] - s / 2), (c[0] + s / 2, c[1] - s / 2), (c[0] + s / 2, c[1] + s / 2), (c[0] - s / 2, c[1] + s / 2)]


def rotated_rect(cx: float, cy: float, w: float, d: float, yaw_deg: float):
    """中心 (cx, cy)、幅 w（yaw の向き）× 奥行き d の長方形。yaw は x 軸（東）から反時計回りの度。"""
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    return [(cx + c * a - s * b, cy + s * a + c * b) for a, b in ((-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2))]


def _yaw(pt: dict, V) -> float:
    y = pt.get("yaw", 0.0)
    if isinstance(y, (int, float)) and not isinstance(y, bool):
        return float(y)
    return float(V(y, 0.0))


def ceiling_beams(pt: dict, face: dict | None, V=plain, tag="ceiling_beams") -> list[dict]:
    """ピロティ・軒下の天井の小梁の格子。面の along0〜along1、面から depth（side "in" は建物の中＝ピロティ、"out" は外＝軒下）。
    天井の高さ z から下へ梁の成 beam_height。direction "across"（面に直角の梁を面に沿って pitch で並べる）／
    "along"（面に平行の梁を奥へ pitch で並べる）／"grid"（両方。奥への間隔は pitch_n、無ければ pitch）。"""
    a0 = V(pt.get("along0"), None, f"{tag} の始まり", "along0 が unknown。置かなかった")
    a1 = V(pt.get("along1"), None, f"{tag} の終わり", "along1 が unknown。置かなかった")
    D = V(pt.get("depth"), None, f"{tag} の奥行き", "depth が unknown。置かなかった")
    z = V(pt.get("z"), None, f"{tag} の天井の高さ", "z が unknown。置かなかった")
    pitch = V(pt.get("pitch"), None, f"{tag} の梁の間隔", "pitch が unknown。置かなかった")
    if None in (a0, a1, D, z, pitch):
        return []
    bw = float(V(pt.get("beam_width"), 0.3, f"{tag} の梁の幅", "未測。0.3 m と置いた"))
    bh = float(V(pt.get("beam_height"), 0.5, f"{tag} の梁の成", "未測。0.5 m と置いた"))
    n0, n1 = (-D, 0.0) if pt.get("side", "in") == "in" else (0.0, D)
    dirn = pt.get("direction", "across")
    mat = pt.get("material", "white")
    out = []
    if dirn in ("across", "grid"):
        for i, a in enumerate(spaced(a0, a1, pitch)):
            out.append(rect_prism(f"beam_a{i}", max(a0, a - bw / 2), min(a1, a + bw / 2), n0, n1, z - bh, z, mat))
    if dirn in ("along", "grid"):
        pn = float(V(pt.get("pitch_n"), pitch))
        for i, nn in enumerate(spaced(n0, n1, pn)):
            out.append(rect_prism(f"beam_n{i}", a0, a1, max(n0, nn - bw / 2), min(n1, nn + bw / 2), z - bh, z, mat))
    return out


def louver(pt: dict, face: dict | None, V=plain, tag="louver") -> list[dict]:
    """ルーバー。面の along〜along+width、z0〜z1。羽根は blade_pitch ごと（区画の中心）、幅 blade_width（面から外へ）、
    厚さ blade_thickness、角度 angle（度。横羽根は外の縁が下がる向き、縦羽根は to 側へ振る向きが正）。
    orientation "horizontal"（横羽根）/ "vertical"（縦羽根）。offset は壁から羽根の内の縁まで。frame で両端（上下）の枠。"""
    A = V(pt.get("along"), None, f"{tag} の位置", "along が unknown。置かなかった")
    W = V(pt.get("width"), None, f"{tag} の幅", "width が unknown。置かなかった")
    z0 = V(pt.get("z0"), None, f"{tag} の下端", "z0 が unknown。置かなかった")
    z1 = V(pt.get("z1"), None, f"{tag} の上端", "z1 が unknown。置かなかった")
    pitch = V(pt.get("blade_pitch"), None, f"{tag} の羽根の間隔", "blade_pitch が unknown。置かなかった")
    if None in (A, W, z0, z1, pitch):
        return []
    bw = float(V(pt.get("blade_width"), 0.15, f"{tag} の羽根の幅", "未測。0.15 m と置いた"))
    bt = float(V(pt.get("blade_thickness"), 0.02))
    ang = math.radians(float(V(pt.get("angle"), 0.0, f"{tag} の羽根の角度", "未測。0° と置いた")))
    off = float(V(pt.get("offset"), 0.0))
    mat = pt.get("material", "metal")
    nc = off + bw / 2
    c, s = math.cos(ang), math.sin(ang)
    out = []
    if pt.get("orientation", "horizontal") == "horizontal":
        for k, zc in enumerate(cells(z0, z1, pitch)):
            sec = [(nc + dx * c + dz * s, zc - dx * s + dz * c) for dx, dz in ((-bw / 2, -bt / 2), (bw / 2, -bt / 2), (bw / 2, bt / 2), (-bw / 2, bt / 2))]
            out.append(P("ext_n", f"blade{k}", sec, A, A + W, mat))
        if pt.get("frame", True):
            f = 0.05
            out.append(rect_prism("frame0", A, A + f, off, off + bw, z0, z1, mat))
            out.append(rect_prism("frame1", A + W - f, A + W, off, off + bw, z0, z1, mat))
    else:
        for k, ac in enumerate(cells(A, A + W, pitch)):
            sec = [(ac + da * c - dn * s, nc + da * s + dn * c) for da, dn in ((-bt / 2, -bw / 2), (bt / 2, -bw / 2), (bt / 2, bw / 2), (-bt / 2, bw / 2))]
            out.append(P("prism", f"blade{k}", sec, z0, z1, mat))
        if pt.get("frame", True):
            f = 0.05
            out.append(rect_prism("frame0", A, A + W, off, off + bw, z0, z0 + f, mat))
            out.append(rect_prism("frame1", A, A + W, off, off + bw, z1 - f, z1, mat))
    return out


def _footprint(pt: dict, V, tag: str, w_key="width", d_key="depth"):
    """面に付く物は (面の座標の長方形)、単独の物は (世界座標の多角形)。返り値 (pts, world か) か None。"""
    if pt.get("polygon"):
        return [tuple(map(float, q)) for q in pt["polygon"]], True
    W = V(pt.get(w_key), None, f"{tag} の幅", f"{w_key} が unknown。置かなかった")
    D = V(pt.get(d_key), None, f"{tag} の奥行き", f"{d_key} が unknown。置かなかった")
    if W is None or D is None:
        return None
    if pt.get("face"):
        A = V(pt.get("along"), 0.0)
        off = float(V(pt.get("offset"), 0.0))
        return [(A, off), (A + W, off), (A + W, off + D), (A, off + D)], False
    if pt.get("x") is None or pt.get("y") is None:
        return None
    return rotated_rect(float(pt["x"]), float(pt["y"]), W, D, _yaw(pt, V)), True


def planter(pt: dict, face: dict | None, V=plain, tag="planter") -> list[dict]:
    """植え込み・花壇。縁（material、既定 brick）の壁 wall 厚、中は緑（leaf）で縁より 0.05 m 低い。
    面に付く物は {face, along, width, depth, offset}、単独は {x, y, width, depth, yaw} か {polygon}。z0 の既定は足元の地面。"""
    fp = _footprint(pt, V, tag)
    H = V(pt.get("height"), None, f"{tag} の高さ", "height が unknown。置かなかった")
    if fp is None or H is None:
        return []
    pts, world = fp
    z0 = float(V(pt.get("z0"), 0.0 if world else face_ground(face)))
    wt = float(V(pt.get("wall"), 0.15, f"{tag} の縁の厚さ", "未測。0.15 m と置いた"))
    mat = pt.get("material", "brick")
    outer = ccw_pts(pts)
    inner = inset_polygon(outer, wt)
    out = []
    n = len(outer)
    for i in range(n):
        q = [outer[i], outer[(i + 1) % n], inner[(i + 1) % n], inner[i]]
        if area(q) > 1e-6:
            out.append(P("prism", f"rim{i}", q, z0, z0 + H, mat))
    out.append(P("prism", "soil", inner, z0, z0 + max(H - 0.05, 0.01), "leaf"))
    return out


def nameplate(pt: dict, face: dict | None, V=plain, tag="nameplate") -> list[dict]:
    """銘板・看板。幅 width × 高さ height × 厚さ thickness、下端 z0。面に付く物は {face, along, offset}（壁から offset 離す。
    既定 0＝壁に貼る）、単独は {x, y, yaw}（中心。板の面は yaw の向き）。material stone / metal / dark。"""
    Hh = V(pt.get("height"), None, f"{tag} の高さ", "height が unknown。置かなかった")
    z0 = V(pt.get("z0"), None, f"{tag} の下端", "z0 が unknown。置かなかった")
    if Hh is None or z0 is None:
        return []
    fp = _footprint(pt, V, tag, d_key="thickness")
    if fp is None:
        return []
    return [P("prism", "plate", fp[0], z0, z0 + Hh, pt.get("material", "stone"))]


def patch(pt: dict, face: dict | None, V=plain, tag="patch") -> list[dict]:
    """面の一部だけ別の素材。along0〜along1、z0〜z1 に、壁から 5 mm 浮かせた厚さ 5 mm の板（material か asset:<id>）。"""
    vals = [V(pt.get(k), None, f"{tag} の {k}", f"{k} が unknown。置かなかった") for k in ("along0", "along1", "z0", "z1")]
    if None in vals:
        return []
    a0, a1, z0, z1 = vals
    return [rect_prism("patch", a0, a1, 0.005, 0.010, z0, z1, pt.get("material", "concrete"))]


def vestibule(pt: dict, V=plain, tag="vestibule") -> list[dict]:
    """角の出たガラスの箱（風除室）。平面 polygon（世界座標）、z0〜z0+height。辺ごとにガラスの板、縦の格子を mullion_pitch
    以下の等間隔（角を含む）、横の格子を transom_pitch ごと（上下の枠は常に）。wall_edges（壁に付く辺の番号）にはガラスを
    張らない。屋根は薄い板（roof{thickness, overhang, material}）。"""
    pts = pt.get("polygon")
    z0 = V(pt.get("z0"), None, f"{tag} の床の高さ", "z0 が unknown。置かなかった")
    H = V(pt.get("height"), None, f"{tag} の高さ", "height が unknown。置かなかった")
    if not pts or len(pts) < 3 or z0 is None or H is None:
        return []
    mp = float(V(pt.get("mullion_pitch"), 1.2, f"{tag} の縦の格子の間隔", "未測。1.2 m と置いた"))
    tp = V(pt.get("transom_pitch"))
    skip = set(pt.get("wall_edges") or [])
    poly = [tuple(map(float, q)) for q in pts]
    n = len(poly)
    f, g = 0.06, 0.02
    top = z0 + H
    out = []
    for i in range(n):
        if i in skip:
            continue
        p, q = poly[i], poly[(i + 1) % n]
        out.append(P("prism", f"glass{i}", edge_quad(p, q, g), z0, top, pt.get("material", "glass")))
        L = math.hypot(q[0] - p[0], q[1] - p[1])
        for k, t in enumerate(spaced(0.0, L, mp)):
            c = (p[0] + (q[0] - p[0]) * t / L, p[1] + (q[1] - p[1]) * t / L)
            out.append(P("prism", f"mullion{i}_{k}", square_pts(c, f), z0, top, "metal"))
        zs = [z0 + f / 2, top - f / 2]
        if tp:
            k = 1
            while z0 + k * tp < top - 0.1:
                zs.append(z0 + k * tp)
                k += 1
        for k, zz in enumerate(zs):
            out.append(P("prism", f"transom{i}_{k}", edge_quad(p, q, f), zz - f / 2, zz + f / 2, "metal"))
    rf = pt.get("roof") or {}
    rt = float(V(rf.get("thickness"), 0.15, f"{tag} の屋根の厚さ", "未測。0.15 m と置いた"))
    ov = float(V(rf.get("overhang"), 0.0))
    out.append(P("prism", "roof", inset_polygon(poly, -ov) if ov else poly, top, top + rt, rf.get("material", "concrete")))
    return out


def _clip_line(poly, c, d):
    """直線 c + t·d と多角形の交わる区間 [(t0, t1), ...]。"""
    ts = []
    n = len(poly)
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        e = (q[0] - p[0], q[1] - p[1])
        den = d[0] * e[1] - d[1] * e[0]
        if abs(den) < 1e-12:
            continue
        w = (p[0] - c[0], p[1] - c[1])
        t = (w[0] * e[1] - w[1] * e[0]) / den
        s = (w[0] * d[1] - w[1] * d[0]) / den
        if -1e-9 <= s < 1 - 1e-9:
            ts.append(t)
    ts.sort()
    return [(ts[k], ts[k + 1]) for k in range(0, len(ts) - 1, 2)]


def pergola(pt: dict, V=plain, tag="pergola") -> list[dict]:
    """柱と梁の格子（パーゴラ）。平面 polygon（世界座標）、z0〜z0+height。柱は辺に沿って post_pitch 以下の等間隔（角を含む）、
    太さ post_size。外周の大梁（辺の上）と、最初の辺に平行な小梁を beam_pitch ごと（多角形で切る）。梁の幅 beam_width・成 beam_height。
    大梁は z_top−2×成〜z_top−成、小梁はその上（z_top まで）。"""
    pts = pt.get("polygon")
    z0 = V(pt.get("z0"), None, f"{tag} の足元", "z0 が unknown。置かなかった")
    H = V(pt.get("height"), None, f"{tag} の高さ", "height が unknown。置かなかった")
    pp = V(pt.get("post_pitch"), None, f"{tag} の柱の間隔", "post_pitch が unknown。置かなかった")
    bp = V(pt.get("beam_pitch"), None, f"{tag} の梁の間隔", "beam_pitch が unknown。置かなかった")
    if not pts or len(pts) < 3 or None in (z0, H, pp, bp):
        return []
    ps = float(V(pt.get("post_size"), 0.25, f"{tag} の柱の太さ", "未測。0.25 m と置いた"))
    bw = float(V(pt.get("beam_width"), 0.15, f"{tag} の梁の幅", "未測。0.15 m と置いた"))
    bh = float(V(pt.get("beam_height"), 0.25, f"{tag} の梁の成", "未測。0.25 m と置いた"))
    mat = pt.get("material", "concrete")
    poly = ccw_pts(pts)
    n = len(poly)
    top = z0 + H
    out = []
    seen = []
    for i in range(n):
        p, q = poly[i], poly[(i + 1) % n]
        L = math.hypot(q[0] - p[0], q[1] - p[1])
        for t in spaced(0.0, L, pp):
            c = (p[0] + (q[0] - p[0]) * t / max(L, 1e-9), p[1] + (q[1] - p[1]) * t / max(L, 1e-9))
            if any(math.hypot(c[0] - o[0], c[1] - o[1]) < 1e-3 for o in seen):
                continue
            seen.append(c)
            out.append(P("prism", f"post{len(seen) - 1}", square_pts(c, ps), z0, top - 2 * bh, mat))
        out.append(P("prism", f"girder{i}", edge_quad(p, q, bw), top - 2 * bh, top - bh, mat))
    (x0, y0), (x1, y1) = poly[0], poly[1]
    L0 = math.hypot(x1 - x0, y1 - y0) or 1.0
    d = ((x1 - x0) / L0, (y1 - y0) / L0)
    nrm = (-d[1], d[0])
    proj = [(q[0] - x0) * nrm[0] + (q[1] - y0) * nrm[1] for q in poly]
    k = 0
    for s in cells(min(proj), max(proj), bp):
        c = (x0 + nrm[0] * s, y0 + nrm[1] * s)
        for t0, t1 in _clip_line(poly, c, d):
            a, b = (c[0] + d[0] * t0, c[1] + d[1] * t0), (c[0] + d[0] * t1, c[1] + d[1] * t1)
            out.append(P("prism", f"rafter{k}", edge_quad(a, b, bw), top - bh, top, mat))
            k += 1
    return out


def bridge(pt: dict, V=plain, tag="bridge"):
    """渡り廊下・橋。from [x,y]（床 z_from）→ to [x,y]（床 z_to）、幅 width、天井まで height、床の厚さ slab。
    walls: "full"（壁と屋根で閉じた廊下）/ "glass"（腰壁 0.9 m＋上はガラス、屋根あり）/ "parapet"（腰壁だけ、屋根なし）/ "none"。
    roof を true/false で上書きできる。座標は部品の局所（a＝from から to へ、n＝横、z）。返り値 (frame, prims)。"""
    fr, to = pt.get("from"), pt.get("to")
    zf = V(pt.get("z_from"), None, f"{tag} の始点の床", "z_from が unknown。置かなかった")
    zt = V(pt.get("z_to"), None, f"{tag} の終点の床", "z_to が unknown。置かなかった")
    W = V(pt.get("width"), None, f"{tag} の幅", "width が unknown。置かなかった")
    if not fr or not to or None in (zf, zt, W):
        return None, []
    L = math.hypot(to[0] - fr[0], to[1] - fr[1])
    if L < 1e-6:
        return None, []
    walls = pt.get("walls", "full")
    H = float(V(pt.get("height"), 2.8, f"{tag} の天井の高さ", "未測。2.8 m と置いた"))
    slab = float(V(pt.get("slab"), 0.3))
    wt = float(V(pt.get("wall_thickness"), 0.15))
    mat = pt.get("material", "concrete")

    def band(name, lo, hi, n0, n1, m):
        return P("ext_a", name, [(0.0, zf + lo), (L, zt + lo), (L, zt + hi), (0.0, zf + hi)], n0, n1, m)

    out = [band("slab", -slab, 0.0, -W / 2, W / 2, mat)]
    sides = ((-W / 2, -W / 2 + wt), (W / 2 - wt, W / 2))
    for k, (n0, n1) in enumerate(sides):
        if walls == "full":
            out.append(band(f"wall{k}", 0.0, H, n0, n1, mat))
        elif walls == "glass":
            par = 0.9
            out.append(band(f"wall{k}", 0.0, par, n0, n1, mat))
            out.append(band(f"glass{k}", par, H, (n0 + n1) / 2 - 0.01, (n0 + n1) / 2 + 0.01, "glass"))
        elif walls == "parapet":
            ph = float(V(pt.get("parapet_h"), 1.1, f"{tag} の腰壁の高さ", "未測。建築基準法の最低 1.1 m"))
            out.append(band(f"wall{k}", 0.0, ph, n0, n1, mat))
    if pt.get("roof", walls in ("full", "glass")):
        out.append(band("roof", H, H + slab, -W / 2, W / 2, mat))
    return {"from": [float(fr[0]), float(fr[1])], "to": [float(to[0]), float(to[1])]}, out


def curved_glass(pt: dict, face: dict | None, V=plain, tag="curved_glass") -> list[dict]:
    """曲面ガラス。面の along0〜along1 を弦にして外へふくらむ半径 radius の円弧（24 分割）、z0〜z1。
    縦の格子は弧の長さで mullion_pitch 以下の等間隔（両端を含む）、横の格子は transom_pitch ごと（上下の枠は常に）。
    cap（既定 true）で上下にふくらみの形の板。奥の壁には室内の暗い板（interior）を貼る。"""
    vals = [V(pt.get(k), None, f"{tag} の {k}", f"{k} が unknown。置かなかった") for k in ("along0", "along1", "radius", "z0", "z1")]
    if None in vals:
        return []
    a0, a1, R, z0, z1 = map(float, vals)
    h = (a1 - a0) / 2
    if R < h - 1e-6:
        V(None, None, f"{tag} の半径", f"半径 {R} m が弦の半分 {h:.2f} m より小さい。置かなかった")
        return []
    mp = float(V(pt.get("mullion_pitch"), 1.2, f"{tag} の縦の格子の間隔", "未測。1.2 m と置いた"))
    tp = V(pt.get("transom_pitch"))
    ac, nc = (a0 + a1) / 2, -math.sqrt(max(R * R - h * h, 0.0))
    th0, th1 = math.atan2(-nc, a0 - ac), math.atan2(-nc, a1 - ac)
    seg = 24
    g, f = 0.02, 0.06

    def at(th, r):
        return (ac + r * math.cos(th), nc + r * math.sin(th))

    ths = [th0 + (th1 - th0) * i / seg for i in range(seg + 1)]
    mat = pt.get("material", "glass")
    out = []
    for i in range(seg):
        q = [at(ths[i], R), at(ths[i + 1], R), at(ths[i + 1], R - g), at(ths[i], R - g)]
        out.append(P("prism", f"glass{i}", q, z0, z1, mat))
    arc = abs(th1 - th0) * R
    for k, s in enumerate(spaced(0.0, arc, mp)):
        th = th0 + (th1 - th0) * s / max(arc, 1e-9)
        out.append(P("prism", f"mullion{k}", square_pts(at(th, R - g / 2), f), z0, z1, "metal"))
    zs = [z0 + f / 2, z1 - f / 2]
    if tp:
        k = 1
        while z0 + k * tp < z1 - 0.1:
            zs.append(z0 + k * tp)
            k += 1
    for k, zz in enumerate(zs):
        for i in range(seg):
            q = [at(ths[i], R + 0.01), at(ths[i + 1], R + 0.01), at(ths[i + 1], R - f), at(ths[i], R - f)]
            out.append(P("prism", f"transom{k}_{i}", q, zz - f / 2, zz + f / 2, "metal"))
    out.append(rect_prism("interior", a0, a1, 0.002, 0.008, z0, z1, "interior"))
    if pt.get("cap", True):
        region = dedupe([at(t, R) for t in ths])
        out.append(P("prism", "cap_top", region, z1, z1 + 0.15, pt.get("cap_material", "concrete")))
        out.append(P("prism", "cap_bottom", region, z0 - 0.15, z0, pt.get("cap_material", "concrete")))
    return out


# --- 部品の type → 形 ------------------------------------------------------------

FACE_PART_TYPES = ("stair_switchback", "porch", "steps", "rail", "ceiling_beams", "louver", "patch", "curved_glass")
WORLD_PART_TYPES = ("column", "rooftop_units", "vestibule", "pergola", "bridge")
FLEX_PART_TYPES = ("planter", "nameplate")          # face＋along か x,y（か polygon）
ALL_PART_TYPES = FACE_PART_TYPES + WORLD_PART_TYPES + FLEX_PART_TYPES
FACE_FUNCS = {"steps": steps, "ceiling_beams": ceiling_beams, "louver": louver, "patch": patch, "curved_glass": curved_glass,
              "planter": planter, "nameplate": nameplate}


def part_prims(pt: dict, faces: dict, V=plain, tag: str | None = None):
    """新しい部品 1 つの形。返り値: (frame, cut, prims)。frame は面 id、"world"、または局所の線 {"from","to"}（渡り廊下）。
    対象外の type は None。"""
    t = pt.get("type")
    tag = tag or t
    if t in FLEX_PART_TYPES and not pt.get("face"):
        return "world", None, FACE_FUNCS[t](pt, None, V, tag)
    if t in FACE_PART_TYPES or t in FLEX_PART_TYPES:
        f = faces.get(pt.get("face"))
        if not f:
            return None
        if t == "stair_switchback":
            return pt["face"], None, stair_switchback(pt, f, V, tag)
        if t == "porch":
            cut, prims = porch(pt, f, V, tag)
            return pt["face"], cut, prims
        if t in FACE_FUNCS:
            return pt["face"], None, FACE_FUNCS[t](pt, f, V, tag)
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
    if t == "vestibule":
        return "world", None, vestibule(pt, V, tag)
    if t == "pergola":
        return "world", None, pergola(pt, V, tag)
    if t == "bridge":
        fr, prims = bridge(pt, V, tag)
        return (fr or "world"), None, prims
    return None


# --- 座標の変換（立面図・平面図で、別の座標の部品を面に投影する） ----------------

def frame_axes(frame, faces: dict | None = None):
    """frame（"world"・面 id・{"from","to"}）の (原点 x, y, 沿う向き ux, uy, 外向き nx, ny)。面の外向きは (u.y, −u.x)。"""
    if frame == "world":
        return 0.0, 0.0, 1.0, 0.0, 0.0, 1.0
    f = frame if isinstance(frame, dict) else (faces or {})[frame]
    (x0, y0), (x1, y1) = f["from"], f["to"]
    L = math.hypot(x1 - x0, y1 - y0) or 1.0
    ux, uy = (x1 - x0) / L, (y1 - y0) / L
    return float(x0), float(y0), ux, uy, uy, -ux


def prim_vertices(pr: dict, axes) -> list[tuple[float, float, float]]:
    """素片の頂点を世界座標 (x, y, z) で。"""
    x0, y0, ux, uy, nx, ny = axes

    def W(a, n, z):
        return (x0 + ux * a + nx * n, y0 + uy * a + ny * n, z)

    if pr["k"] == "prism":
        return [W(a, n, z) for a, n in pr["pts"] for z in (pr["z0"], pr["z1"])]
    if pr["k"] == "ext_a":
        return [W(a, n, z) for a, z in pr["pts"] for n in (pr["n0"], pr["n1"])]
    return [W(a, n, z) for n, z in pr["pts"] for a in (pr["a0"], pr["a1"])]


def to_frame(verts, axes) -> list[tuple[float, float, float]]:
    """世界座標の頂点を frame の (a, n, z) に。"""
    x0, y0, ux, uy, nx, ny = axes
    return [((x - x0) * ux + (y - y0) * uy, (x - x0) * nx + (y - y0) * ny, z) for x, y, z in verts]


def hull(pts) -> list[tuple[float, float]]:
    """2 次元の凸包（立面・平面に投影した輪郭の近似）。"""
    P2 = sorted(set((round(p[0], 6), round(p[1], 6)) for p in pts))
    if len(P2) < 3:
        return P2

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lo, up = [], []
    for p in P2:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(P2):
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    return lo[:-1] + up[:-1]
