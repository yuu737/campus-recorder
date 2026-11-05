"""内装（棟 JSON の `interior` 欄）の語彙：検証と形の計算。bpy を使わない（Blender の中からも、図面からも使う）。

進行役の決定 D-0002（data/decisions.json）：
    部屋の輪郭・床と天井の高さ・柱の格子・ガラスの壁・扉・置物。部屋は棟の座標の多角形＋床 z＋天井高。
    柱は格子で。ガラスの壁は外観の面を参照。置物は CC0（type:asset）。立面には出さず平面図（階ごと）に出す。
    歩く画面では中に入れる（部屋の形で塊をくり抜く）。

書き方（測定値は schema.Measurement の形 {value, status, evidence, method, tol}）
    "interior": {
      "rooms": [{
        "id": "hall_1f", "name": "学生ホール", "floor": 1,
        "polygon": {"points": [[x, y], …], "status", "evidence", "method"},   棟の座標（footprint と同じ系、m）
        "floor_z":   {M}     床の高さ（棟の z=0 ＝ ground_z からの m）
        "ceiling_h": {M}     床から天井まで（m）
        "wall_t":    {M}     外壁の厚さ（省略可。くり抜きを外壁の線からこれだけ内側に止める。無ければ模型は 0.25 m と仮置き）
        "columns": {"grid": {"origin": {"value": [x, y], …}, "axis_face": "f19"（省略可。x 軸を面の向きに）,
                             "dx": {M}, "dy": {M}, "nx": {M 本}, "ny": {M 本}, "size": {M}},
                    "omit": [[i, j], …]}                 角柱の格子。omit で無い所を抜く
        "glass_walls": [{"face": "f19", "from": {M}, "to": {M}, "mullion_pitch": {M}}]
                     外観の面 face の from→to に沿った区間（m）がガラス。高さは床から天井まで
        "doors": [{"face": "f19", "at": {M}, "width": {M}, "height": {M}, "kind": "auto_sliding" | "swing"}]
                     at は面に沿った扉の中心（m）
        "parts": [{"type": "asset", "asset": "<台帳の id>", "at": [x, y], "rot_deg": 0, "count": 1, "pitch": {M},
                   "size": {"w","d","h"}（省略可）, "evidence", "status", "method"},
                  {"type": "box", "name": "自販機", "at": [x, y], "size": {"w","d","h"}, "material": "metal", …}]
                     置物。位置（at）は部品ごとの status / evidence で根拠を持つ（composite と同じまとめの形）
        "materials": {"floor": "wood_plank" | "asset:<id>", "ceiling": "ceiling_tile", "wall": "plaster"}
        "lights": {"grid": true}   天井の照明（見た目だけ。値は持たない）
      }],
      "requests": [{"face", "what", "where", "why"}]     足りない写真
    }

模型（blender_build）：塊の窓・部品のくり抜きの後に、部屋の形（外壁の線から wall_t 内側に寄せた多角形 = cavity_polygon）
で床 z〜天井を引き算し、その後にガラスの壁・扉の開口を引き算する（窓の彫り込みとは別の回）。
"""

from __future__ import annotations

import math

ROOM_KINDS = ("floor", "ceiling", "columns", "glass", "door", "lights", "wall", "parts")
DOOR_KINDS = ("auto_sliding", "swing")
WALL_T_DEFAULT = 0.25            # 外壁の厚さが無いときの仮の値（模型だけ。JSON には書かない）
LIGHT_PITCH_DEFAULT = 3.6        # 柱の格子が無いときの照明の間隔（見た目だけ）
ON_FACE_TOL = 0.6                # 部屋の辺が外壁の線に「沿う」とみなす距離（m）
# 内装の素材の名前（Blender の色。図面の塗り）。asset:<id> は台帳の texture
MATERIALS = {
    "wood_plank": (0.55, 0.40, 0.26, 1.0),
    "ceiling_tile": (0.90, 0.90, 0.88, 1.0),
    "plaster": (0.86, 0.85, 0.82, 1.0),
    "concrete": (0.70, 0.69, 0.66, 1.0),
    "terrazzo": (0.78, 0.76, 0.72, 1.0),
    "vinyl_tile": (0.72, 0.72, 0.70, 1.0),
    "carpet_gray": (0.35, 0.36, 0.38, 1.0),
    "metal": (0.75, 0.76, 0.78, 1.0),
    "white": (0.88, 0.88, 0.86, 1.0),
    "wood": (0.55, 0.40, 0.26, 1.0),
    "dark": (0.22, 0.17, 0.13, 1.0),
}
MATERIAL_DEFAULT = {"floor": "vinyl_tile", "ceiling": "ceiling_tile", "wall": "plaster"}
_PART_WORDS = {"type", "asset", "name", "at", "rot_deg", "count", "pitch", "size", "evidence", "status", "method",
               "note", "material", "z0"}


# --- 値 ---------------------------------------------------------------------------

def _is_m(d) -> bool:
    return isinstance(d, dict) and "value" in d and "status" in d


def v(m, default=None):
    """測定値の値（unknown・無しは default）。裸の数は使わない（検証で落とす）。"""
    if _is_m(m) and m.get("value") is not None and m.get("status") != "unknown":
        return m["value"]
    return default


def _plain_val(m, default=None, what: str = "", why: str = ""):
    return v(m, default)


def _Mprob(m, where: str, required: bool = True) -> list[str]:
    from .schema import Measurement
    if m is None:
        return [f"{where}: ありません（unknown でも鍵は書く）"] if required else []
    if not _is_m(m):
        return [f"{where}: 根拠の無い裸の値です（{{\"value\", \"status\", \"evidence\", \"method\"}} の形で書く）"]
    out = Measurement.from_json(m).problems(where)
    if m.get("basis"):
        out.append(f"{where}: 内装は仮説で作らない（basis は使えません）")
    return out


def _status_prob(d: dict, where: str) -> list[str]:
    """まとめの形（polygon・置物）の status / evidence / method の規約。"""
    from .schema import STATUSES
    st = d.get("status")
    if st not in STATUSES:
        return [f"{where}: status は measured / estimated / unknown のどれかです（{st!r}）"]
    if st == "measured" and not d.get("evidence"):
        return [f"{where}: measured なのに根拠（evidence）がありません"]
    if st == "estimated" and not (d.get("method") or d.get("note")):
        return [f"{where}: estimated なのに推定の仕方（method か note）がありません"]
    return []


# --- 平面の幾何 -----------------------------------------------------------------------

def area(pts) -> float:
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts))) / 2


def ccw(pts) -> list[tuple[float, float]]:
    pts = [(float(p[0]), float(p[1])) for p in pts]
    return pts if area(pts) >= 0 else list(reversed(pts))


def point_in_polygon(p, pts) -> bool:
    x, y = p
    inside = False
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            if x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
                inside = not inside
    return inside


def dist_to_segment(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)


def dist_to_boundary(p, pts) -> float:
    return min(dist_to_segment(p, pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts)))


def inside_or_on(p, pts, tol: float = 0.05) -> bool:
    return point_in_polygon(p, pts) or dist_to_boundary(p, pts) <= tol


def _seg_cross(a, b, c, d) -> bool:
    """2 本の線分が本当に交わるか（端・辺の上で触れるだけは数えない）。"""
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    eps = 1e-6
    return ((o1 > eps and o2 < -eps) or (o1 < -eps and o2 > eps)) and ((o3 > eps and o4 < -eps) or (o3 < -eps and o4 > eps))


def polygons_overlap(A, B, tol: float = 0.05) -> bool:
    """面積を持って重なるか（辺を共有して隣り合うのは重ならない）。"""
    for i in range(len(A)):
        for j in range(len(B)):
            if _seg_cross(A[i], A[(i + 1) % len(A)], B[j], B[(j + 1) % len(B)]):
                return True
    for P, Q in ((A, B), (B, A)):
        for p in P:
            if point_in_polygon(p, Q) and dist_to_boundary(p, Q) > tol:
                return True
    # 辺が重なって並ぶ場合（頂点がどれも相手の辺の上）：重なりの候補の箱の中を細かく調べる
    x0, x1 = max(min(p[0] for p in A), min(p[0] for p in B)), min(max(p[0] for p in A), max(p[0] for p in B))
    y0, y1 = max(min(p[1] for p in A), min(p[1] for p in B)), min(max(p[1] for p in A), max(p[1] for p in B))
    if x1 - x0 <= tol or y1 - y0 <= tol:
        return False
    step = max(0.1, min(x1 - x0, y1 - y0) / 20)
    y = y0 + step / 2
    while y < y1:
        x = x0 + step / 2
        while x < x1:
            q = (x, y)
            if (point_in_polygon(q, A) and point_in_polygon(q, B)
                    and dist_to_boundary(q, A) > tol and dist_to_boundary(q, B) > tol):
                return True
            x += step
        y += step
    return False


def centroid(pts) -> tuple[float, float]:
    a = area(pts)
    if abs(a) < 1e-9:
        return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    cx = cy = 0.0
    for i in range(len(pts)):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % len(pts)]
        c = x0 * y1 - x1 * y0
        cx += (x0 + x1) * c
        cy += (y0 + y1) * c
    return cx / (6 * a), cy / (6 * a)


def _line_isect(p, d, q, e):
    """点 p 向き d の直線と、点 q 向き e の直線の交点（平行なら None）。"""
    den = d[0] * e[1] - d[1] * e[0]
    if abs(den) < 1e-9:
        return None
    t = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / den
    return p[0] + d[0] * t, p[1] + d[1] * t


def offset_edges(pts, offs) -> list[tuple[float, float]]:
    """反時計回りの多角形の辺 i を内向きに offs[i] m 寄せた多角形（隣の辺の寄せた線の交点）。"""
    pts = ccw(pts)
    n = len(pts)
    lines = []
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        L = math.hypot(b[0] - a[0], b[1] - a[1]) or 1.0
        d = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
        nin = (-d[1], d[0])                      # 反時計回りの内向き
        lines.append(((a[0] + nin[0] * offs[i], a[1] + nin[1] * offs[i]), d, nin))
    out = []
    for i in range(n):
        p0, d0, n0 = lines[i - 1]
        p1, d1, n1 = lines[i]
        x = _line_isect(p0, d0, p1, d1)
        if x is None:                            # 一直線に並ぶ頂点：大きい方の寄せで動かす
            o = max(offs[i - 1], offs[i])
            x = (pts[i][0] + n1[0] * o, pts[i][1] + n1[1] * o)
        out.append(x)
    return out


# --- 面 -------------------------------------------------------------------------------

def face_frame(f: dict):
    """面の始点・単位方向・外向き法線・長さ（blender_build.face_frame と同じ向き）。"""
    (x0, y0), (x1, y1) = f["from"], f["to"]
    L = math.hypot(x1 - x0, y1 - y0)
    u = ((x1 - x0) / L, (y1 - y0) / L)
    return (float(x0), float(y0)), u, (u[1], -u[0]), L


def face_point(f: dict, along: float, inward: float = 0.0) -> tuple[float, float]:
    p0, u, n, _L = face_frame(f)
    return p0[0] + u[0] * along - n[0] * inward, p0[1] + u[1] * along - n[1] * inward


def _faces(b: dict) -> dict:
    return {f["id"]: f for f in b.get("faces", []) if f.get("id") and f.get("from") and f.get("to")}


# --- 部屋の形 -------------------------------------------------------------------------

def rooms(b: dict) -> list[dict]:
    return list(((b.get("interior") or {}).get("rooms")) or [])


def room_points(room: dict):
    pg = room.get("polygon")
    if not isinstance(pg, dict) or pg.get("status") == "unknown":
        return None
    pts = pg.get("points")
    if not isinstance(pts, list) or len(pts) < 3:
        return None
    return ccw(pts)


def floors_with_rooms(b: dict) -> list[int]:
    return sorted({r.get("floor") for r in rooms(b) if isinstance(r.get("floor"), int)})


def room_z(room: dict, b: dict, val=_plain_val) -> tuple[float | None, float | None]:
    """(床の z, 天井の z)。棟の z=0 から m。val は blender_build.val（仮の値を記録する）か既定。"""
    fl = room.get("floor") if isinstance(room.get("floor"), int) else 1
    pitch = v(b.get("floor_pitch"))
    fz = val(room.get("floor_z"), (fl - 1) * pitch if pitch else None,
             f"interior.{room.get('id')}.floor_z", "床の高さ unknown。（階−1）×階高と置いた")
    ch = val(room.get("ceiling_h"), (pitch - 0.8) if pitch else None,
             f"interior.{room.get('id')}.ceiling_h", "天井高 unknown。階高−0.8 m（梁と床の厚さ）と置いた")
    if fz is None or ch is None:
        return None, None
    return float(fz), float(fz) + float(ch)


def edge_offsets(pts, b: dict, wall_t: float) -> list[float]:
    """部屋の辺ごとに、外壁の線（faces）から wall_t の厚さを残すための寄せ量（m）。外壁に沿わない辺は 0。
    沿う＝面の線と平行（5° 以内）、辺の中点が面の長さの範囲、両端が線から内向き −0.3〜wall_t＋0.6 m。"""
    faces = list(_faces(b).values())
    offs = []
    n = len(pts)
    for i in range(n):
        a, c = pts[i], pts[(i + 1) % n]
        L = math.hypot(c[0] - a[0], c[1] - a[1]) or 1.0
        d = ((c[0] - a[0]) / L, (c[1] - a[1]) / L)
        best = 0.0
        for f in faces:
            p0, u, nn, FL = face_frame(f)
            if abs(d[0] * u[1] - d[1] * u[0]) > 0.09:              # 平行（5° 以内）でない
                continue
            mid = ((a[0] + c[0]) / 2, (a[1] + c[1]) / 2)
            mt = (mid[0] - p0[0]) * u[0] + (mid[1] - p0[1]) * u[1]
            if not (-0.3 <= mt <= FL + 0.3):
                continue
            hs = [-((q[0] - p0[0]) * nn[0] + (q[1] - p0[1]) * nn[1]) for q in (a, c)]   # 内向きの距離
            if min(hs) >= -0.3 and max(hs) < wall_t + ON_FACE_TOL:
                best = max(best, wall_t - min(hs))
        offs.append(max(0.0, best))
    return offs


def wall_thickness(room: dict, val=_plain_val) -> float:
    return float(val(room.get("wall_t"), WALL_T_DEFAULT, f"interior.{room.get('id')}.wall_t",
                     f"外壁の厚さ未測。{WALL_T_DEFAULT} m と置いた（くり抜きを外壁の線から内側に止める）"))


def cavity_polygon(room: dict, b: dict, val=_plain_val):
    """くり抜く多角形：部屋の輪郭の、外壁に沿う辺を壁厚ぶん内側へ寄せた物。"""
    pts = room_points(room)
    if pts is None:
        return None
    wt = wall_thickness(room, val)
    return offset_edges(pts, edge_offsets(pts, b, wt))


def cut_volume(room: dict, b: dict, val=_plain_val) -> float:
    """くり抜きの体積の見積り（m³）＝ くり抜く多角形の面積 × 天井高。柱は引かない（柱は後から足す別の物）。"""
    cav = cavity_polygon(room, b, val)
    z0, z1 = room_z(room, b, val)
    if cav is None or z0 is None:
        return 0.0
    return abs(area(cav)) * (z1 - z0)


def grid_axes(grid: dict, b: dict) -> tuple[tuple[float, float], tuple[float, float]]:
    f = _faces(b).get(grid.get("axis_face"))
    if f:
        _p0, u, _n, _L = face_frame(f)
        return u, (-u[1], u[0])
    return (1.0, 0.0), (0.0, 1.0)


def column_positions(room: dict, b: dict, val=_plain_val) -> list[dict]:
    """柱の中心 [{i, j, x, y, size, ux, uy}]。格子の値が unknown なら空（決めつけない）。"""
    cols = room.get("columns") or {}
    g = cols.get("grid") or {}
    org = v(g.get("origin"))
    dx, dy = v(g.get("dx")), v(g.get("dy"))
    nx, ny = v(g.get("nx")), v(g.get("ny"))
    if org is None or nx is None or ny is None:
        return []
    nx, ny = int(nx), int(ny)
    if (nx > 1 and dx is None) or (ny > 1 and dy is None):
        return []
    size = float(val(g.get("size"), 0.6, f"interior.{room.get('id')}.columns.size", "柱の太さ未測。0.6 m と置いた"))
    omit = {(int(a), int(c)) for a, c in (cols.get("omit") or [])}
    ux, uy = grid_axes(g, b)
    out = []
    for i in range(nx):
        for j in range(ny):
            if (i, j) in omit:
                continue
            x = org[0] + ux[0] * i * (dx or 0) + uy[0] * j * (dy or 0)
            y = org[1] + ux[1] * i * (dx or 0) + uy[1] * j * (dy or 0)
            out.append({"i": i, "j": j, "x": x, "y": y, "size": size, "ux": ux})
    return out


def light_positions(room: dict, b: dict) -> list[tuple[float, float]]:
    """天井の照明の位置（見た目だけ）。柱の格子があればその升目の中心、無ければ 3.6 m 間隔。くり抜きの中だけ。"""
    if not (room.get("lights") or {}).get("grid"):
        return []
    cav = cavity_polygon(room, b)
    if cav is None:
        return []
    g = (room.get("columns") or {}).get("grid") or {}
    org, dx, dy = v(g.get("origin")), v(g.get("dx")), v(g.get("dy"))
    ux, uy = grid_axes(g, b)
    if org is None or not dx or not dy:
        c = centroid(cav)
        org, dx, dy = c, LIGHT_PITCH_DEFAULT, LIGHT_PITCH_DEFAULT
    # 升目の中心を部屋全体に広げる
    span = max(math.hypot(p[0] - org[0], p[1] - org[1]) for p in cav)
    ni, nj = int(span / dx) + 2, int(span / dy) + 2
    out = []
    for i in range(-ni, ni + 1):
        for j in range(-nj, nj + 1):
            a, c = (i + 0.5) * dx, (j + 0.5) * dy
            p = (org[0] + ux[0] * a + uy[0] * c, org[1] + ux[1] * a + uy[1] * c)
            if point_in_polygon(p, cav) and dist_to_boundary(p, cav) > 0.6:
                out.append(p)
    return out


def glass_segment(gw: dict, b: dict) -> tuple[float, float] | None:
    a0, a1 = v(gw.get("from")), v(gw.get("to"))
    if a0 is None or a1 is None or gw.get("face") not in _faces(b):
        return None
    return float(min(a0, a1)), float(max(a0, a1))


def mullions(gw: dict, b: dict) -> list[float]:
    """ガラスの壁の縦枠の位置（面に沿った m）。ピッチ unknown なら両端だけ。"""
    seg = glass_segment(gw, b)
    if seg is None:
        return []
    a0, a1 = seg
    p = v(gw.get("mullion_pitch"))
    if not p or p <= 0:
        return [a0, a1]
    n = max(1, round((a1 - a0) / p))
    return [a0 + k * (a1 - a0) / n for k in range(n + 1)]


def door_span(dr: dict, b: dict) -> tuple[float, float, float] | None:
    """(面に沿った始まり, 終わり, 高さ)。どれか unknown なら None（開けない）。"""
    at, w, h = v(dr.get("at")), v(dr.get("width")), v(dr.get("height"))
    if at is None or w is None or h is None or dr.get("face") not in _faces(b):
        return None
    return float(at) - float(w) / 2, float(at) + float(w) / 2, float(h)


def part_spots(pt: dict) -> list[tuple[float, float, float]]:
    """置物の置き場所 [(x, y, 向きのラジアン)]。status unknown や位置が無ければ置かない。"""
    if pt.get("status") == "unknown":
        return []
    at = pt.get("at")
    if not (isinstance(at, (list, tuple)) and len(at) == 2):
        return []
    n = v(pt.get("count")) if isinstance(pt.get("count"), dict) else pt.get("count", 1)
    n = int(n or 1)
    pitch = v(pt.get("pitch")) or 0.0
    if n > 1 and not pitch:
        return []
    yaw = math.radians(float(pt.get("rot_deg") or 0.0))
    ux, uy = math.cos(yaw), math.sin(yaw)
    return [(at[0] + ux * pitch * k, at[1] + uy * pitch * k, yaw) for k in range(n)]


def part_size(pt: dict) -> list[float | None]:
    s = pt.get("size") or {}
    return [v(s.get(k)) for k in ("w", "d", "h")]


def room_state(room: dict) -> str:
    """部屋の根拠の状態（glb の extras.evidence）：輪郭が unknown なら unknown、全部 measured なら measured、他は estimated。"""
    pg = room.get("polygon") or {}
    if pg.get("status") not in ("measured", "estimated"):
        return "unknown"
    sts = [pg.get("status")] + [m.get("status") for m in (room.get("floor_z"), room.get("ceiling_h")) if _is_m(m)]
    if "unknown" in sts or len(sts) < 3:
        return "estimated"
    return "measured" if all(s == "measured" for s in sts) else "estimated"


def room_evidence(room: dict) -> list[str]:
    """部屋の根拠の写真番号（輪郭・床・天井・柱・ガラス・扉の evidence を順に、重複なし）。"""
    seen: list[str] = []

    def walk(o):
        if isinstance(o, dict):
            for e in o.get("evidence") or []:
                if str(e) not in seen:
                    seen.append(str(e))
            for k, x in o.items():
                if k != "evidence" and k != "parts":
                    walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(room)
    return seen


# --- 検証（schema.validate_building の最後から呼ばれる） ------------------------------------

def _catalog_kind(aid: str, want: str, where: str) -> list[str]:
    from . import schema
    return schema._catalog_kind(aid, want, where)


def _material_prob(name, where: str) -> list[str]:
    if not isinstance(name, str) or not name:
        return [f"{where}: 素材の名前がありません"]
    if name.startswith("asset:"):
        return _catalog_kind(name[len("asset:"):], "texture", where)
    if name not in MATERIALS:
        return [f"{where}: 素材 {name} は知りません（{', '.join(MATERIALS)} か asset:<id>）"]
    return []


def _check_on_room_edge(f: dict, a0: float, a1: float, pts, wall_t: float, where: str) -> list[str]:
    """面の区間（外壁の線）が部屋の輪郭に接しているか（壁の厚さ＋余裕の中）。"""
    tol = wall_t + ON_FACE_TOL
    for a in (a0, (a0 + a1) / 2, a1):
        q = face_point(f, a)
        q_in = face_point(f, a, inward=min(wall_t, 0.1))
        if dist_to_boundary(q, pts) > tol and not point_in_polygon(q_in, pts):
            return [f"{where}: 面 {f['id']} の {a:.2f} m の所が部屋の輪郭に接していません（部屋の外の壁です）"]
    return []


def validate_room(room: dict, b: dict, where: str) -> list[str]:
    errs: list[str] = []
    faces = _faces(b)
    if not room.get("id"):
        errs.append(f"{where}: id がありません")
    if not isinstance(room.get("floor"), int) or isinstance(room.get("floor"), bool):
        errs.append(f"{where}: floor（階の番号、整数）がありません")
    pg = room.get("polygon")
    pts = None
    if isinstance(pg, list):
        errs.append(f"{where}.polygon: 根拠の無い座標の並びです（{{\"points\", \"status\", \"evidence\", \"method\"}} の形で書く）")
    elif not isinstance(pg, dict):
        errs.append(f"{where}.polygon: ありません")
    else:
        errs += _status_prob(pg, f"{where}.polygon")
        if pg.get("status") != "unknown":
            raw = pg.get("points")
            if not isinstance(raw, list) or len(raw) < 3:
                errs.append(f"{where}.polygon: points は 3 点以上必要です")
            else:
                pts = ccw(raw)
                if abs(area(pts)) < 0.5:
                    errs.append(f"{where}.polygon: 面積がほとんどありません")
                fp = ((b.get("footprint") or {}).get("polygon")) or []
                if len(fp) >= 3:
                    for k, p in enumerate(pts):
                        if not inside_or_on(p, fp, 0.05):
                            errs.append(f"{where}.polygon.points[{k}] {list(p)} が足元の外形（footprint）の外です")
                    for k in range(len(pts)):
                        m = ((pts[k][0] + pts[(k + 1) % len(pts)][0]) / 2, (pts[k][1] + pts[(k + 1) % len(pts)][1]) / 2)
                        if not inside_or_on(m, fp, 0.05):
                            errs.append(f"{where}.polygon: {k} 番目の辺が足元の外形（footprint）の外を通ります")
        elif pg.get("points"):
            errs.append(f"{where}.polygon: unknown なのに points が入っています")
    for k in ("floor_z", "ceiling_h"):
        errs += _Mprob(room.get(k), f"{where}.{k}")
    if "wall_t" in room:
        errs += _Mprob(room.get("wall_t"), f"{where}.wall_t")
    ch = v(room.get("ceiling_h"))
    if ch is not None and ch <= 0:
        errs.append(f"{where}.ceiling_h: 0 以下です")
    wall_t = v(room.get("wall_t"), WALL_T_DEFAULT)

    cols = room.get("columns")
    if cols is not None:
        g = cols.get("grid") if isinstance(cols, dict) else None
        if not isinstance(g, dict):
            errs.append(f"{where}.columns: grid がありません")
        else:
            gw = f"{where}.columns.grid"
            errs += _Mprob(g.get("origin"), f"{gw}.origin")
            o = v(g.get("origin"))
            if o is not None and not (isinstance(o, (list, tuple)) and len(o) == 2):
                errs.append(f"{gw}.origin: value は [x, y] です")
            for k in ("dx", "dy", "nx", "ny", "size"):
                errs += _Mprob(g.get(k), f"{gw}.{k}")
            if g.get("axis_face") and g["axis_face"] not in faces:
                errs.append(f"{gw}: axis_face={g['axis_face']} は faces にありません")
            for k in ("nx", "ny"):
                n = v(g.get(k))
                if n is not None and (int(n) != n or n < 1):
                    errs.append(f"{gw}.{k}: 1 以上の整数です")
            for om in cols.get("omit") or []:
                if not (isinstance(om, (list, tuple)) and len(om) == 2 and all(isinstance(x, int) for x in om)):
                    errs.append(f"{where}.columns.omit: [i, j]（整数）の並びです（{om!r}）")
            if pts is not None and not errs:
                for c in column_positions(room, b):
                    if not inside_or_on((c["x"], c["y"]), pts, 0.05):
                        errs.append(f"{where}.columns: 柱 [{c['i']}, {c['j']}]（{c['x']:.2f}, {c['y']:.2f}）が部屋の外です（omit で抜くか格子を直す）")

    for k, gw in enumerate(room.get("glass_walls") or []):
        w = f"{where}.glass_walls[{k}]"
        f = faces.get(gw.get("face"))
        if f is None:
            errs.append(f"{w}: face={gw.get('face')} は faces にありません")
        for kk in ("from", "to", "mullion_pitch"):
            errs += _Mprob(gw.get(kk), f"{w}.{kk}")
        seg = glass_segment(gw, b)
        if f is not None and seg is not None:
            L = face_frame(f)[3]
            if seg[0] < -0.05 or seg[1] > L + 0.05:
                errs.append(f"{w}: 区間 {seg[0]:.2f}〜{seg[1]:.2f} m が面 {f['id']} の長さ {L:.2f} m の外です")
            if pts is not None:
                errs += _check_on_room_edge(f, *seg, pts, wall_t, w)

    for k, dr in enumerate(room.get("doors") or []):
        w = f"{where}.doors[{k}]"
        f = faces.get(dr.get("face"))
        if f is None:
            errs.append(f"{w}: face={dr.get('face')} は faces にありません")
        for kk in ("at", "width", "height"):
            errs += _Mprob(dr.get(kk), f"{w}.{kk}")
        if dr.get("kind") not in DOOR_KINDS:
            errs.append(f"{w}: kind は {' / '.join(DOOR_KINDS)} のどれかです（{dr.get('kind')!r}）")
        sp = door_span(dr, b)
        if f is not None and sp is not None:
            L = face_frame(f)[3]
            if sp[0] < -0.05 or sp[1] > L + 0.05:
                errs.append(f"{w}: 扉 {sp[0]:.2f}〜{sp[1]:.2f} m が面 {f['id']} の長さ {L:.2f} m の外です")
            if pts is not None:
                errs += _check_on_room_edge(f, sp[0], sp[1], pts, wall_t, w)
            ch2 = v(room.get("ceiling_h"))
            if ch2 is not None and sp[2] > ch2 + 0.01:
                errs.append(f"{w}: 扉の高さ {sp[2]:.2f} m が天井高 {ch2:.2f} m より高いです")

    for k, pt in enumerate(room.get("parts") or []):
        w = f"{where}.parts[{k}]"
        t = pt.get("type")
        if t not in ("asset", "box"):
            errs.append(f"{w}: type は asset か box です（{t!r}）")
            continue
        errs += _status_prob(pt, w)
        unknown_words = set(pt) - _PART_WORDS
        if unknown_words:
            errs.append(f"{w}: 知らない鍵 {sorted(unknown_words)}")
        if t == "asset":
            aid = pt.get("asset")
            if not isinstance(aid, str) or not aid:
                errs.append(f"{w}: asset（台帳の id）がありません")
            else:
                errs += _catalog_kind(aid, "model", w)
        else:
            if not pt.get("name"):
                errs.append(f"{w}: box には name（何の箱か）が要ります")
            s = pt.get("size")
            if not isinstance(s, dict) or any(kk not in s for kk in ("w", "d", "h")):
                errs.append(f"{w}: box には size {{w, d, h}} が要ります")
            if pt.get("material") is not None:
                errs += _material_prob(pt.get("material"), f"{w}.material")
        if isinstance(pt.get("size"), dict):
            for kk, mv in pt["size"].items():
                errs += _Mprob(mv, f"{w}.size.{kk}")
        for kk in ("pitch", "z0"):
            if kk in pt:
                errs += _Mprob(pt.get(kk), f"{w}.{kk}")
        cnt = pt.get("count", 1)
        if isinstance(cnt, dict):
            errs += _Mprob(cnt, f"{w}.count")
            cnt = v(cnt, 1)
        if not isinstance(cnt, (int, float)) or isinstance(cnt, bool) or int(cnt) != cnt or cnt < 1:
            errs.append(f"{w}: count は 1 以上の整数です")
        elif cnt > 1 and v(pt.get("pitch")) is None:
            errs.append(f"{w}: count が 2 以上なら pitch（m）が要ります")
        rd = pt.get("rot_deg", 0)
        if not isinstance(rd, (int, float)) or isinstance(rd, bool):
            errs.append(f"{w}: rot_deg は度の数です")
        at = pt.get("at")
        if pt.get("status") != "unknown":
            if not (isinstance(at, (list, tuple)) and len(at) == 2 and all(isinstance(x, (int, float)) for x in at)):
                errs.append(f"{w}: at は [x, y]（棟の座標 m）です")
            elif pts is not None:
                for (x, y, _yaw) in part_spots(pt):
                    if not inside_or_on((x, y), pts, 0.05):
                        errs.append(f"{w}: 置き場所（{x:.2f}, {y:.2f}）が部屋の外です")
                        break

    mats = room.get("materials")
    if mats is not None:
        if not isinstance(mats, dict):
            errs.append(f"{where}.materials: {{floor, ceiling, wall}} です")
        else:
            for k, name in mats.items():
                if k not in MATERIAL_DEFAULT:
                    errs.append(f"{where}.materials: 鍵は floor / ceiling / wall です（{k}）")
                else:
                    errs += _material_prob(name, f"{where}.materials.{k}")
    lt = room.get("lights")
    if lt is not None and not (isinstance(lt, dict) and isinstance(lt.get("grid", False), bool)):
        errs.append(f"{where}.lights: {{\"grid\": true|false}} です")
    return errs


def validate_building_hook(b: dict) -> list[str]:
    it = b.get("interior")
    if it is None:
        return []
    if not isinstance(it, dict):
        return ["interior: {rooms, requests} の形です"]
    errs: list[str] = []
    seen: set[str] = set()
    placed: list[tuple[int, str, list]] = []
    for i, room in enumerate(it.get("rooms") or []):
        where = f"interior.rooms[{room.get('id', i)}]"
        if room.get("id") in seen:
            errs.append(f"{where}: id が重複しています")
        seen.add(room.get("id"))
        errs += validate_room(room, b, where)
        pts = room_points(room)
        if pts is not None and isinstance(room.get("floor"), int):
            for fl, rid, other in placed:
                if fl == room["floor"] and polygons_overlap(pts, other):
                    errs.append(f"{where}: 同じ {fl} 階の部屋 {rid} と輪郭が重なっています")
            placed.append((room["floor"], room.get("id"), pts))
    faces = _faces(b)
    for k, rq in enumerate(it.get("requests") or []):
        w = f"interior.requests[{k}]"
        if not isinstance(rq, dict):
            errs.append(f"{w}: {{face, what, where, why}} です")
            continue
        if rq.get("face") and rq["face"] != "*" and rq["face"] not in faces:
            errs.append(f"{w}: face={rq['face']} は faces にありません")
        if not rq.get("what"):
            errs.append(f"{w}: what（何を撮るか）がありません")
    return errs
