"""街路の視点（persp_street.png と model_report.json の web_cameras.street、歩く画面の「<棟>へ」の立ち位置）を決める。

blender_build.py（Blender の中）からも読むので、numpy と標準ライブラリだけで書く（bpy・shapely を使わない）。

決め方（11/8 監査 B の 5「街路の視点」）：
  1. 面：登録台帳（data/photos/registry.json）でこの棟の登録が一番多い面（MIN_REGS 枚以上）。無ければ一番長い面。
     登録の面が "(pose)" など面 id でない時は、visible_faces の中で写った面積が一番大きい面に数える。
  2. 方向：その面の登録のカメラ位置（−R_cvᵀ t_cv）を、面の中点から見た「前」と「横」に分けて中央値を取り、
     横は面の長さの ±1/3 に切る。中点からその向きに立つ（登録が足りない面は面の外向きの法線）。
  3. 距離：max(38 m, 面の長さ×0.85, 高さ×0.9)。
  4. 目の高さ：その点の 5m 標高 + 1.6 m を、棟の ground_z からの高さ（Blender の z）に直す。
  5. 立つ点が足元（自分の棟・他の棟 JSON・敷地 JSON の棟・PLATEAU の箱）の中に入るなら、距離を縮める → 面を替える、の順で避ける。
     中点への視線が他の棟の足元を通る時も同じに扱う（他の棟の壁しか写らないため）。
大学・棟の固有の値は書かない。決めた根拠は standpoint（dict）にして model_report.json に書く。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

DIST_MIN_M = 38.0          # 街路の視点の距離の下限（11/4 からの既定）
LEN_FACTOR = 0.85          # 面の長さ×この値（persp_street_<面> と同じ式）
HEIGHT_FACTOR = 0.9        # 高さ×この値
SIDE_LIMIT = 1.0 / 3.0     # 横のずれは面の長さのこの割合まで
MIN_REGS = 3               # 登録がこの枚数以上の面だけを「撮られた面」として使う
TARGET_FACTOR = 0.45       # 見る点の高さ（棟の高さ×この値。blender_build の街路視点のカメラの的と同じ）
# 見えの確かめ：目から面の上の的（面に沿って ALONG の位置 × 高さ×HEIGHTS）への視線のうち、他の物（上端の標高が分かる物）
# に遮られない割合。2 回に分けて探す：まず全部の面で SIGHT_PASSES[0] の割合以上、無ければ [1] の割合以上
SIGHT_ALONG = (0.1, 0.3, 0.5, 0.7, 0.9)
SIGHT_HEIGHTS = (0.2, 0.5, 0.85)
SIGHT_CORNERS = 8
SIGHT_RULE = ("目から棟の的（面に沿った {} 点と足元の角 最大 {} 個 × 高さ×{}）への視線のうち、他の物（PLATEAU の箱・棟 JSON・"
              "敷地 JSON の棟で上端の標高が分かる物）の上端より上を通る割合").format(len(SIGHT_ALONG), 8, list(SIGHT_HEIGHTS))
SIGHT_PASSES = (("ほぼ全部が見える", 0.9), ("4 分の 3 が見える", 0.75), ("半分より多く見える", 0.6))
EYE_M = 1.6                # 目の高さ（地面から）
SHRINK_STEP_M = 2.0        # 足元の中に入る時に距離を縮める刻み
SHRINK_MIN_M = 12.0        # 縮めるのはここまで（これより近いと棟の全体が入らない）
SHRINK_MIN_FRAC = 0.5      # 縮めるのは決めた距離のこの割合まで。それでも避けられなければ面を替える
CLEAR_M = 3.0              # 足元の縁からこれだけ離れていないと「中」と見なす
OWN_OVERLAP = 0.5          # 他の足元の面積のこの割合以上が自分の足元に重なる物は、自分の一部（同じ棟の別の記録）と見なす


# --- 幾何（numpy だけ）---------------------------------------------------------------

def _ring(poly) -> np.ndarray:
    a = np.asarray(poly, dtype=float)
    if len(a) > 1 and np.allclose(a[0], a[-1]):
        a = a[:-1]
    return a


def point_in_polygon(x: float, y: float, poly) -> bool:
    a = _ring(poly)
    inside = False
    n = len(a)
    for i in range(n):
        x1, y1 = a[i]
        x2, y2 = a[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xi:
                inside = not inside
    return inside


def distance_to_polygon_edge(x: float, y: float, poly) -> float:
    a = _ring(poly)
    b = np.roll(a, -1, axis=0)
    d = b - a
    ll = np.maximum((d ** 2).sum(1), 1e-12)
    t = np.clip(((x - a[:, 0]) * d[:, 0] + (y - a[:, 1]) * d[:, 1]) / ll, 0.0, 1.0)
    px, py = a[:, 0] + t * d[:, 0], a[:, 1] + t * d[:, 1]
    return float(np.sqrt((px - x) ** 2 + (py - y) ** 2).min())


def polygon_area(poly) -> float:
    a = _ring(poly)
    x, y = a[:, 0], a[:, 1]
    return float(abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2)


def inside_with_clearance(x: float, y: float, poly, clear: float = CLEAR_M) -> bool:
    """足元の中か、縁から clear m 以内なら True。"""
    return point_in_polygon(x, y, poly) or distance_to_polygon_edge(x, y, poly) < clear


def overlap_fraction(poly, own, step: float = 1.0) -> float:
    """poly の面積のうち own に入る割合（1 m 格子で数える。shapely を使わない）。"""
    a = _ring(poly)
    x0, y0 = a.min(0)
    x1, y1 = a.max(0)
    xs = np.arange(x0 + step / 2, x1, step)
    ys = np.arange(y0 + step / 2, y1, step)
    if not len(xs) or not len(ys):
        cx, cy = a.mean(0)
        return 1.0 if point_in_polygon(cx, cy, own) else 0.0
    n_in = n_own = 0
    for yy in ys:
        for xx in xs:
            if point_in_polygon(xx, yy, a):
                n_in += 1
                if point_in_polygon(xx, yy, own):
                    n_own += 1
    return n_own / n_in if n_in else 0.0


# --- 入力 ---------------------------------------------------------------------------

def face_frame(f: dict):
    p0 = np.array(f["from"][:2], dtype=float)
    p1 = np.array(f["to"][:2], dtype=float)
    L = float(np.linalg.norm(p1 - p0))
    u = (p1 - p0) / L if L > 0 else np.array([1.0, 0.0])
    n = np.array([u[1], -u[0]])          # 外向き（blender_build.face_frame と同じ向き）
    return p0, p1, u, n, L


def camera_xy(cam: dict) -> tuple[float, float] | None:
    """登録の camera から位置（−R_cvᵀ t_cv）の x, y。無ければ camera_xyz。"""
    R, t = cam.get("R_cv"), cam.get("t_cv")
    if R is not None and t is not None:
        c = -np.asarray(R, dtype=float).T @ np.asarray(t, dtype=float)
        return float(c[0]), float(c[1])
    xyz = cam.get("camera_xyz")
    return (float(xyz[0]), float(xyz[1])) if xyz else None


def entry_face(e: dict, face_ids: set[str]) -> str | None:
    """登録の面。面 id でなければ visible_faces で写った面積が一番大きい面。"""
    f = e.get("face")
    if f in face_ids:
        return f
    vis = [v for v in e.get("visible_faces") or [] if v.get("face") in face_ids]
    if vis:
        return max(vis, key=lambda v: v.get("area_m2") or 0)["face"]
    return None


def registrations_by_face(registry: dict | None, bid: str, face_ids: set[str]) -> dict[str, list[dict]]:
    """{面 id: [{photo, x, y}, ...]}（その棟の登録だけ。カメラの無い登録は数えない）。"""
    out: dict[str, list[dict]] = {}
    for e in (registry or {}).get("entries", []):
        if e.get("building") != bid or not e.get("camera"):
            continue
        fid = entry_face(e, face_ids)
        xy = camera_xy(e["camera"])
        if fid is None or xy is None:
            continue
        out.setdefault(fid, []).append({"photo": e.get("photo"), "x": xy[0], "y": xy[1]})
    return out


def obstacles(b: dict, campus_doc: dict | None, plateau_doc: dict | None, others: list[dict]) -> tuple[list, list[dict]]:
    """立ってはいけない足元の一覧。返り値は（自分の足元の多角形のリスト, [{id, kind, polygon}]（他の物））。
    敷地 JSON・PLATEAU の箱のうち、自分の足元に大半が重なる物は自分の一部として他から外す。"""
    own = []
    fp = (b.get("footprint") or {}).get("polygon")
    if fp:
        own.append(fp)
    for blk in b.get("blocks") or []:
        if blk.get("polygon"):
            own.append(blk["polygon"])
    bid = b.get("id")
    own_ids = {bid, b.get("site_id")}

    def is_own(poly) -> bool:
        if not own:
            return False
        a, o = _ring(poly), _ring(own[0])
        if (a[:, 0].max() < o[:, 0].min() or a[:, 0].min() > o[:, 0].max()
                or a[:, 1].max() < o[:, 1].min() or a[:, 1].min() > o[:, 1].max()):
            return False
        return overlap_fraction(poly, own[0]) >= OWN_OVERLAP

    obs: list[dict] = []
    for ob in others:
        if ob.get("id") == bid:
            continue
        p = (ob.get("footprint") or {}).get("polygon")
        if p:
            obs.append({"id": ob["id"], "kind": "棟 JSON の足元", "polygon": p, "top": _mval(ob.get("parapet_top"))})
    designed = {ob.get("id") for ob in others} | {ob.get("site_id") for ob in others}
    own_parts: set[str] = set()
    # site link-box で敷地の物（か複数の相手）に置き換わった PLATEAU の箱は立っている物ではない（歩く画面でも描かない）。
    # 箱の代わりに、上端の分かる敷地の物（渡り廊下 corridor）を障害に足す（11/8 F9）
    replaced = {lk["box"] for lk in (campus_doc or {}).get("plateau_links") or [] if lk.get("kind") == "replaced"}
    for o in ((campus_doc or {}).get("objects") or {}).get("items") or []:
        if o.get("type") != "corridor":
            continue
        poly, fz, h = _mval(o.get("polygon")), _mval(o.get("floor_z")), _mval(o.get("height"))
        if poly and len(poly) >= 3:
            obs.append({"id": o.get("id"), "kind": "敷地の物（渡り廊下）", "polygon": poly,
                        "top": (fz + h) if fz is not None and h is not None else None})
    for sb in (campus_doc or {}).get("buildings", []) + (campus_doc or {}).get("staff_additions", []):
        if sb.get("id") in own_ids:
            own_parts.update(sb.get("plateau_parts") or [])
            continue
        if sb.get("plateau_parts") and set(sb["plateau_parts"]) <= replaced:
            continue                       # OSM の輪郭が置き換わった箱そのもの（甲板・渡り廊下の OSM の多角形）
        rings = sb.get("footprint_osm") or ([sb["footprint"]] if sb.get("footprint") else [])
        for r in rings:
            if len(r) < 3 or is_own(r):
                continue
            kind = "敷地 JSON の棟（棟 JSON あり）" if sb.get("id") in designed else "敷地 JSON の棟の足元（OSM）"
            ref = sb.get("reference") or {}
            top = (ref["plateau_base_z"] + ref["plateau_height_m"]
                   if ref.get("plateau_base_z") is not None and ref.get("plateau_height_m") else None)
            obs.append({"id": sb.get("id"), "kind": kind, "polygon": r, "top": top})
    for pb in (plateau_doc or {}).get("buildings", []):
        r = pb.get("footprint")
        if not r or len(r) < 3 or pb.get("gml_id") in own_parts or pb.get("gml_id") in replaced or is_own(r):
            continue
        top = pb["base_z"] + pb["height_m"] if pb.get("base_z") is not None and pb.get("height_m") else None
        obs.append({"id": pb.get("gml_id"), "kind": "PLATEAU の箱", "polygon": r, "top": top})
    return own, obs


def _mval(v):
    return v.get("value") if isinstance(v, dict) else v


# --- 決める -------------------------------------------------------------------------

def _hit(x: float, y: float, own: list, obs: list[dict]) -> dict | None:
    for p in own:
        if inside_with_clearance(x, y, p):
            return {"id": "（自分の棟）", "kind": "自分の足元"}
    for o in obs:
        if inside_with_clearance(x, y, o["polygon"]):
            return {"id": o["id"], "kind": o["kind"]}
    return None


def points_in_polygon(xs: np.ndarray, ys: np.ndarray, poly) -> np.ndarray:
    """多数の点が多角形の中か（偶奇則、numpy）。"""
    a = _ring(poly)
    inside = np.zeros(len(xs), dtype=bool)
    n = len(a)
    for i in range(n):
        x1, y1 = a[i]
        x2, y2 = a[(i + 1) % n]
        if y1 == y2:
            continue
        c = (y1 > ys) != (y2 > ys)
        xi = x1 + (ys - y1) * (x2 - x1) / (y2 - y1)
        inside ^= c & (xs < xi)
    return inside


def sight_targets(f: dict, footprint=None) -> list[np.ndarray]:
    """見えを確かめる的の平面位置：面 f に沿った SIGHT_ALONG の点と、棟の足元の角（多ければ SIGHT_CORNERS 個に間引く）。
    足元の角を入れるのは、面の外にはみ出した棟の幅（歩く画面の広い画角に入る）も他の物に隠れていないか見るため。"""
    p0, p1, u, n, L = face_frame(f)
    pts = [p0 + (p1 - p0) * t for t in SIGHT_ALONG]
    if footprint:
        a = _ring(footprint)
        idx = np.unique(np.linspace(0, len(a) - 1, min(len(a), SIGHT_CORNERS)).round().astype(int))
        pts += [a[i] for i in idx]
    return pts


def visible_fraction(eye: np.ndarray, z_eye: float | None, f: dict, z_base: float | None, height: float,
                     obs: list[dict], step: float = 1.0, footprint=None) -> tuple[float | None, dict | None]:
    """目から棟の的（sight_targets × SIGHT_HEIGHTS）への視線のうち、他の物の上端より上を通る（遮られない）割合。
    的のすぐ手前 2 m は見ない。上端の標高が分からない物は数えない。目か地面の標高が分からなければ (None, None)。
    自分の棟は他の物に入れないので、足元の裏側の角も「自分に隠れる」とは数えない（他の物だけを見る）。
    返り値：（割合, 一番多く遮った物 {id, kind, rays, of}）。"""
    if z_eye is None or z_base is None:
        return None, None
    tall = [o for o in obs if o.get("top") is not None]
    for o in tall:
        if "bbox" not in o:
            r = _ring(o["polygon"])
            o["bbox"] = (r[:, 0].min(), r[:, 1].min(), r[:, 0].max(), r[:, 1].max())
    clear = total = 0
    blockers: dict = {}
    for tgt in sight_targets(f, footprint):
        d = tgt - eye
        dist = float(np.linalg.norm(d))
        if dist < 3.0:
            continue
        k = np.arange(1, max(2, int((dist - 2.0) / step)))
        s = k * step / dist
        pts = eye[None, :] + d[None, :] * s[:, None]
        if not len(pts):
            continue
        bx = (pts[:, 0].min(), pts[:, 1].min(), pts[:, 0].max(), pts[:, 1].max())
        near = [o for o in tall if not (o["bbox"][2] < bx[0] or o["bbox"][0] > bx[2] or o["bbox"][3] < bx[1] or o["bbox"][1] > bx[3])]
        inside = {id(o): points_in_polygon(pts[:, 0], pts[:, 1], o["polygon"]) for o in near}
        for hf in SIGHT_HEIGHTS:
            total += 1
            z_line = z_eye + (z_base + height * hf - z_eye) * s
            hit = None
            for o in near:
                m = inside[id(o)]
                if m.any() and (o["top"] > z_line[m]).any():
                    hit = o
                    break
            if hit is None:
                clear += 1
            else:
                key = (hit["id"], hit["kind"])
                blockers[key] = blockers.get(key, 0) + 1
    if not total:
        return None, None
    worst = max(blockers.items(), key=lambda kv: kv[1]) if blockers else None
    return clear / total, ({"id": worst[0][0], "kind": worst[0][1], "rays": worst[1], "of": total} if worst else None)


def _dir_note(direction: dict, fixed, lat: float) -> dict:
    if fixed is None:
        return {**direction, "side_at_standpoint_m": round(lat, 2)}
    return {"from": "横のずれを替えて避けた（登録の向き・法線では見える割合が足りない）", "side_at_standpoint_m": round(lat, 2),
            "registration_direction": direction}


def choose_standpoint(b: dict, height: float, registry: dict | None = None, campus_doc: dict | None = None,
                      plateau_doc: dict | None = None, others: list[dict] | None = None,
                      ground_at=None) -> dict | None:
    """街路の視点を決める。ground_at(x, y) は標高 m（5m 標高）か None を返す関数。

    返り値：{eye_xy, eye_z_rel, target_xy, face, standpoint}（面が無ければ None）。
    eye_z_rel は棟の ground_z からの高さ（Blender の z）。standpoint は model_report に書く根拠。"""
    faces = [f for f in b.get("faces") or [] if f.get("from") and f.get("to")]
    if not faces:
        return None
    face_ids = {f["id"] for f in faces}
    regs = registrations_by_face(registry, b.get("id"), face_ids)
    own, obs = obstacles(b, campus_doc, plateau_doc, others or [])

    def flen(f):
        return face_frame(f)[4]

    reg_faces = sorted([f for f in faces if len(regs.get(f["id"], [])) >= MIN_REGS],
                       key=lambda f: (-len(regs[f["id"]]), -flen(f)))
    rest = sorted([f for f in faces if f not in reg_faces], key=lambda f: -flen(f))
    order = [(f, "登録が一番多い面" if i == 0 else "登録の多い順の次の面") for i, f in enumerate(reg_faces)]
    order += [(f, "一番長い面（登録が {} 枚以上の面が無い）".format(MIN_REGS) if not reg_faces and i == 0
               else "長い順の次の面") for i, f in enumerate(rest)]

    gz = _mval(b.get("ground_z"))
    tried: list[dict] = []
    best: dict | None = None        # どの面でも足りない時に使う：足元の外で一番よく見えた所
    for pass_name, need in SIGHT_PASSES:
        for f, why in order:
            p0, p1, u, n, L = face_frame(f)
            mid = (p0 + p1) / 2
            rs = regs.get(f["id"], [])
            direction = {"from": "面の外向きの法線（登録が {} 枚未満）".format(MIN_REGS)}
            sin_t = 0.0                    # 中点から見た向き（法線からの傾きの sin。横 +u 向きが正）
            cut = L * SIDE_LIMIT
            if len(rs) >= MIN_REGS:
                fw = [float(np.dot(np.array([r["x"], r["y"]]) - mid, n)) for r in rs]
                sd = [float(np.dot(np.array([r["x"], r["y"]]) - mid, u)) for r in rs]
                mf, ms = float(np.median(fw)), float(np.median(sd))
                if mf > 0.5:
                    sin_t = ms / math.hypot(mf, ms)
                    direction = {"from": "登録のカメラ位置（−R_cvᵀ t_cv）の中央値の向き（中点から）",
                                 "median_front_m": round(mf, 2), "median_side_m": round(ms, 2),
                                 "side_limit_m": round(cut, 2),
                                 "note": "立つ点の横のずれ（距離×sin）を面の長さの ±1/3 に切る"}
                else:
                    direction = {"from": "面の外向きの法線（登録の中央値が面の後ろ側）", "median_front_m": round(mf, 2)}
            dist0 = max(DIST_MIN_M, L * LEN_FACTOR, height * HEIGHT_FACTOR)
            dmin = max(SHRINK_MIN_M, dist0 * SHRINK_MIN_FRAC)
            shrinks: list[dict] = []
            # 立ち位置の候補：まず登録の向き（無ければ法線）、足りなければ横のずれを 0・±1/6・±1/3（面の長さ）に替えて
            # 同じ面で探す（登録の向きに近い順）。横のずれは面の長さの ±1/3 を超えない
            reg_lat0 = max(-cut, min(cut, dist0 * sin_t))
            variants = [None] + sorted({0.0, cut / 2, -cut / 2, cut, -cut} - {round(reg_lat0, 9)},
                                       key=lambda v: (abs(v - reg_lat0), v))
            n_side = 0
            for fixed in variants:
                dist = dist0
                while dist >= dmin - 1e-9:
                    lat = max(-cut, min(cut, dist * sin_t)) if fixed is None else max(-dist, min(dist, fixed))
                    eye = mid + u * lat + n * math.sqrt(dist * dist - lat * lat)
                    hit = _hit(eye[0], eye[1], own, obs)
                    vis = None
                    if hit is None:
                        ze = ground_at(eye[0], eye[1]) if ground_at else None
                        vis, worst = visible_fraction(eye, None if ze is None else ze + EYE_M, f, gz, height, obs,
                                                      footprint=own[0] if own else None)
                        if vis is not None and (best is None or vis > best["visible"]):
                            best = {"visible": vis, "eye": eye.copy(), "f": f, "why": why, "rs": rs, "dist": dist, "dist0": dist0,
                                    "direction": _dir_note(direction, fixed, lat), "worst": worst}
                        if vis is not None and vis < need:
                            hit = {**(worst or {}), "visible": round(vis, 2),
                                   "why": "面の見える割合 {:.0%} が {:.0%} に足りない（他の物に遮られる）".format(vis, need)}
                    else:
                        hit = {**hit, "why": "立つ点が足元の中"}
                    if hit is None:
                        z_abs = ground_at(eye[0], eye[1]) if ground_at else None
                        if z_abs is not None and gz is not None:
                            z_rel = z_abs + EYE_M - gz
                            zsrc = "5m 標高（out/site/dem5a.npz）の値 + {} m − ground_z".format(EYE_M)
                        else:
                            z_rel = EYE_M
                            zsrc = "5m 標高か ground_z が無い。ground_z + {} m（地形を見ていない仮の値）".format(EYE_M)
                        sp = {
                            "face": f["id"], "face_length_m": round(L, 2), "why_face": why,
                            "registrations": len(rs), "photos": [r["photo"] for r in rs],
                            "distance_m": round(dist, 2), "distance_rule": "max({}, 面の長さ×{}, 高さ×{}) = {:.2f}".format(
                                DIST_MIN_M, LEN_FACTOR, HEIGHT_FACTOR, dist0),
                            "shrunk": shrinks, "faces_tried": tried,
                            "ground_elev_m": None if z_abs is None else round(z_abs, 2), "ground_z": gz,
                            "eye_z_rule": zsrc, "obstacles_checked": len(obs) + len(own),
                            "sight": {"pass": pass_name, "need": need, "visible": None if vis is None else round(vis, 2),
                                      "rule": SIGHT_RULE},
                        }
                        sp["direction"] = _dir_note(direction, fixed, lat)
                        if fixed is not None:
                            sp["avoided"] = "登録の向きでは {:.1f}〜{:.1f} m のどこも足りず、横のずれを {:.1f} m に替えて避けた（他に {} 点を外した）".format(
                                dist0, dmin, lat, n_side)
                        elif shrinks:
                            sp["avoided"] = "距離を {:.1f} m から {:.1f} m に縮めて避けた".format(dist0, dist)
                        return {"eye_xy": [float(eye[0]), float(eye[1])], "eye_z_rel": float(z_rel),
                                "target_xy": [float(mid[0]), float(mid[1])], "face": f["id"], "standpoint": sp}
                    if fixed is None:
                        shrinks.append({"distance_m": round(dist, 2), **hit})
                    else:
                        n_side += 1
                    dist -= SHRINK_STEP_M
            tried.append({"face": f["id"], "registrations": len(rs), "why_face": why, "sight_pass": pass_name,
                          "rejected": "距離 {:.1f}〜{:.1f} m のどこも足元の中か視線が遮られる".format(dist0, dmin),
                          "first_hit": shrinks[0] if shrinks else None})
    if best is not None:
        # どの面でも見える割合が足りない：足元の外で一番よく見えた所に立つ（根拠に書く）
        f, eye = best["f"], best["eye"]
        p0, p1, u, n, L = face_frame(f)
        mid = (p0 + p1) / 2
        z_abs = ground_at(eye[0], eye[1]) if ground_at else None
        z_rel = (z_abs + EYE_M - gz) if (z_abs is not None and gz is not None) else EYE_M
        sp = {"face": f["id"], "face_length_m": round(L, 2),
              "why_face": "どの面でも見える割合が {:.0%} に届かない。足元の外で一番よく見えた所（{}）".format(
                  SIGHT_PASSES[-1][1], best["why"]),
              "registrations": len(best["rs"]), "photos": [r["photo"] for r in best["rs"]], "direction": best["direction"],
              "distance_m": round(best["dist"], 2), "distance_rule": "max({}, 面の長さ×{}, 高さ×{}) = {:.2f}".format(
                  DIST_MIN_M, LEN_FACTOR, HEIGHT_FACTOR, best["dist0"]),
              "faces_tried": tried, "ground_elev_m": None if z_abs is None else round(z_abs, 2), "ground_z": gz,
              "eye_z_rule": "5m 標高（out/site/dem5a.npz）の値 + {} m − ground_z".format(EYE_M),
              "obstacles_checked": len(obs) + len(own),
              "sight": {"pass": "一番よく見えた所", "visible": round(best["visible"], 2), "worst_blocker": best["worst"],
                        "rule": SIGHT_RULE}}
        return {"eye_xy": [float(eye[0]), float(eye[1])], "eye_z_rel": float(z_rel), "target_xy": [float(mid[0]), float(mid[1])],
                "face": f["id"], "standpoint": sp}
    # 足元の外の点が 1 つも無い（見えの確かめもできない）：一番目の面の法線の先に立つ（根拠に書く）
    f = order[0][0]
    p0, p1, u, n, L = face_frame(f)
    mid = (p0 + p1) / 2
    dist = max(DIST_MIN_M, L * LEN_FACTOR, height * HEIGHT_FACTOR)
    eye = mid + n * dist
    z_abs = ground_at(eye[0], eye[1]) if ground_at else None
    z_rel = (z_abs + EYE_M - gz) if (z_abs is not None and gz is not None) else EYE_M
    return {"eye_xy": [float(eye[0]), float(eye[1])], "eye_z_rel": float(z_rel), "target_xy": [float(mid[0]), float(mid[1])],
            "face": f["id"], "standpoint": {"face": f["id"], "why_face": "どの面でも足元を避けられなかった（仮の立ち位置）",
                                            "faces_tried": tried, "distance_m": round(dist, 2), "registrations": len(regs.get(f["id"], []))}}


def load_inputs(b: dict, repo: Path) -> dict:
    """リポジトリから choose_standpoint の入力を読む（無い物は None / []）。"""
    def rd(p: Path):
        try:
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        except (OSError, ValueError):
            return None
    campus = b.get("campus")
    campus_doc = rd(repo / "data" / "site" / f"{campus}.json") if campus else None
    if campus_doc is None:
        # 棟 JSON の campus が古い呼び名（meijo-tempaku など）で敷地 JSON の名前と違う時：棟の site_id を持つ敷地 JSON を探す
        sid = b.get("site_id") or b.get("id")
        for sp in sorted((repo / "data" / "site").glob("*.json")):
            d = rd(sp)
            if isinstance(d, dict) and any(sb.get("id") == sid for sb in d.get("buildings") or []):
                campus_doc = d
                break
    ok = {None, campus, (campus_doc or {}).get("campus")}
    site_ids = {sb.get("id") for sb in (campus_doc or {}).get("buildings") or []}
    others = []
    for p in sorted((repo / "data" / "buildings").glob("*.json")):
        d = rd(p)
        if isinstance(d, dict) and d.get("id") and d.get("id") != b.get("id") and (
                not campus or d.get("campus") in ok or (d.get("site_id") or d.get("id")) in site_ids):
            others.append(d)
    return {"registry": rd(repo / "data" / "photos" / "registry.json"), "campus_doc": campus_doc,
            "plateau_doc": rd(repo / "data" / "site" / "plateau.json"), "others": others}
