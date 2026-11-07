"""敷地の物（新しい語彙。指示書 11/8 の K-107、T6）。

棟と棟の間・2 棟以上に触れる物を、棟 JSON ではなく敷地 JSON（data/site/<campus>.json）の "objects" に置く。
値の決まりは棟と同じ（CLAUDE.md の 1）：measured（根拠の写真あり）／estimated（推定、根拠と方法あり）／unknown（値は null と撮影依頼）。

型（最小の 4 つ）：
    bridge  通路橋。両端の棟・面（ends[2]：building・face・along0・along1・z）と床の高さ floor_z（標高 m）。
            平面は両端の面の区間の 4 点を結んだ四角（棟 JSON の面の from/to から計算。ここに座標を二重に書かない）。
            parapet_height・beam_depth（腰壁の高さ・桁の厚さ）は任意。
    deck    甲板。polygon（[[x,y],…] 原点からの m）と z（床の標高 m）、thickness（任意）。
    steps   棟の間の階段・広場の段。polygon（4 点。最初の辺が下の段の縁、3・4 点目が上の段の縁）、z0・z1（下と上の標高 m）、count（段）。
    asset   CC0 の置物（木・街灯・旗竿・植え込み）。asset（data/assets/catalog.json の id）と position（[x,y]）、z（任意。無ければ地面）。
            見た目はアセット、位置だけが根拠付きの値（CLAUDE.md の 3）。

座標：x＝東・y＝北（原点 北緯35.1355° 東経136.9755°）、高さは標高 m。ただし bridge の ends[].z だけは
その棟の z=0（棟 JSON の ground_z）からの m（棟 JSON の階・部品の高さと同じ物差しで並べて見るため）。

決まり：
    - 根拠の無い measured は検証で落とす。measured の長さ（unit m）は鎖（chain、K-101）が無ければ落とす（個数は evidence だけでよい）。
    - estimated は evidence と method が要る。unknown は value が null。unknown のある物は open の撮影依頼（requests）を持つ。
    - bridge の両端の棟・面は data/buildings/<棟>.json にあること。
    - 生成物（glb）は敷地 JSON からだけ作る。手で直さない。Blender は使わない（板と箱だけ）。

歩く画面：export_web_objects が <web>/data/models/site_objects.glb と site.json の "site_objects" を書く。
glb は他の棟の glb と同じ軸（glTF の Y が上：x＝東、y＝標高、z＝−北）で、高さは標高そのもの（ground_z は 0）。
site.json の models に "site-objects" として足すので、歩く画面（web/index.html）は棟の glb と同じ読み込みで置く。
"""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path

SCHEMA = "campus-recorder/site-objects/1"
TYPES = ("bridge", "deck", "steps", "asset")
STATUSES = ("measured", "estimated", "unknown")
COUNT_UNITS = ("階", "本", "枚", "台", "スパン", "段", "列", "か所")
CHAIN_LINKS = ("tape", "count", "px_ratio", "uniform_period")
NON_PHOTO_PREFIX = ("osm:", "plateau:", "gsi:", "design_", "ref:")
WEB_ID = "site-objects"
WEB_GLB = "site_objects.glb"
DRAW_THICK_M = 0.05      # 厚さが unknown の板を描く時の線の太さ（描画の都合。寸法ではない。JSON には書かない）

ROOT = Path(__file__).resolve().parent.parent
DATA_BUILDINGS = ROOT / "data" / "buildings"
CATALOG = ROOT / "data" / "assets" / "catalog.json"

# 型ごとの値の欄：(名前, 必須か)
FIELDS = {
    "bridge": (("floor_z", True), ("parapet_height", False), ("beam_depth", False)),
    "deck": (("polygon", True), ("z", True), ("thickness", False)),
    "steps": (("polygon", True), ("z0", True), ("z1", True), ("count", True)),
    "asset": (("position", True), ("z", False), ("rotation_deg", False)),
}
END_FIELDS = (("along0", True), ("along1", True), ("z", True))

MATERIAL_RGBA = {
    "brick": (0.60, 0.33, 0.25, 1.0), "concrete": (0.66, 0.66, 0.63, 1.0), "tile": (0.78, 0.60, 0.42, 1.0),
    "stone": (0.62, 0.62, 0.60, 1.0), "metal": (0.55, 0.57, 0.60, 1.0), "unknown": (0.55, 0.56, 0.57, 1.0),
}


# --- 読み書き -----------------------------------------------------------------------------

def objects_of(site: dict) -> list[dict]:
    return list(((site.get("objects") or {}).get("items")) or [])


def load_buildings(ids, root: Path | None = None) -> dict[str, dict]:
    """両端に出てくる棟の JSON を読む（無い棟は入れない。検証で「棟 JSON に無い」になる）。"""
    root = root or DATA_BUILDINGS
    out = {}
    for bid in sorted(set(ids)):
        f = root / f"{bid}.json"
        if f.exists():
            out[bid] = json.loads(f.read_text(encoding="utf-8"))
    return out


def ends_buildings(site: dict) -> list[str]:
    return [e.get("building") for o in objects_of(site) if o.get("type") == "bridge" for e in (o.get("ends") or [])
            if e.get("building")]


def load_catalog_ids(path: Path | None = None) -> set[str]:
    p = path or CATALOG
    if not p.exists():
        return set()
    return {i.get("id") for i in json.loads(p.read_text(encoding="utf-8")).get("items", [])}


def write_objects(site_path: Path, objects: dict, buildings: dict | None = None, catalog_ids: set | None = None) -> list[str]:
    """敷地 JSON の "objects" を差し替える（検証が通った時だけ書く）。書式は敷地 JSON と同じ（indent=1・改行なし）。返り値は誤り。"""
    from .ledger import write_text_atomic

    doc = json.loads(site_path.read_text(encoding="utf-8"))
    doc["objects"] = objects
    if buildings is None:
        buildings = load_buildings(ends_buildings(doc))
    errs, _ = validate_objects(doc, buildings, catalog_ids)
    if errs:
        return errs
    write_text_atomic(site_path, json.dumps(doc, ensure_ascii=False, indent=1))
    return []


# --- 検証 -------------------------------------------------------------------------------

def _is_photo(e) -> bool:
    return isinstance(e, str) and bool(e.strip()) and not e.startswith(NON_PHOTO_PREFIX)


def check_measurement(m, where: str) -> list[str]:
    """値 1 つ（value/unit/tol/status/evidence/method）の決まり。棟 JSON と同じ 3 状態。"""
    if not isinstance(m, dict):
        return [f"{where}: 値の形（value/unit/status/evidence/method）ではない"]
    errs = []
    st = m.get("status")
    if st not in STATUSES:
        return [f"{where}: status が {STATUSES} のどれでもない（{st!r}）"]
    ev = m.get("evidence") or []
    if st == "unknown":
        if m.get("value") is not None:
            errs.append(f"{where}: unknown なのに value がある（null にする）")
        return errs
    if m.get("value") is None:
        errs.append(f"{where}: {st} なのに value が null（読めなければ unknown にする）")
    if st == "measured":
        if not any(_is_photo(e) for e in ev):
            errs.append(f"{where}: 根拠の写真の無い measured（evidence に写真 id が要る）")
        if m.get("unit") == "m":
            chain = m.get("chain") or []
            if not chain:
                errs.append(f"{where}: measured の長さに鎖（chain）が無い（K-101。鎖が書けなければ estimated）")
            for i, ln in enumerate(chain):
                if ln.get("link") not in CHAIN_LINKS:
                    errs.append(f"{where}: chain[{i}] の link {ln.get('link')!r} は measured に使えない（estimated にする）")
                if not ln.get("photo"):
                    errs.append(f"{where}: chain[{i}] に写真 id が無い")
            if m.get("tol") is None:
                errs.append(f"{where}: ± の無い measured の長さ")
    if st == "estimated":
        if not ev:
            errs.append(f"{where}: 根拠（evidence）の無い estimated")
        if not (m.get("method") or "").strip():
            errs.append(f"{where}: 方法（method）の無い estimated")
    return errs


def face_line(b: dict, fid: str):
    """棟 JSON の面の from/to（faces は dict か list のどちらも読む）。無ければ None。"""
    faces = b.get("faces") or {}
    f = faces.get(fid) if isinstance(faces, dict) else next((x for x in faces if x.get("id") == fid), None)
    if not f or not f.get("from") or not f.get("to"):
        return None
    return tuple(f["from"]), tuple(f["to"])


def _val(m):
    return m.get("value") if isinstance(m, dict) else None


def _tol(m) -> float:
    t = m.get("tol") if isinstance(m, dict) else None
    return float(t) if t is not None else 0.0


def validate_objects(site: dict, buildings: dict[str, dict], catalog_ids: set | None = None) -> tuple[list[str], list[str]]:
    """敷地 JSON の objects を検証する。返り値は（誤り, 警告）。誤りがあれば書かない・出さない。"""
    errs: list[str] = []
    warns: list[str] = []
    sec = site.get("objects")
    if sec is None:
        return errs, warns
    if not isinstance(sec, dict) or not isinstance(sec.get("items"), list):
        return ["objects: {\"schema\", \"items\": [...]} の形ではない"], warns
    if sec.get("schema") != SCHEMA:
        errs.append(f"objects.schema が {SCHEMA} ではない")
    seen = set()
    for i, o in enumerate(sec["items"]):
        oid = o.get("id") or f"#{i}"
        w = f"objects[{oid}]"
        if not o.get("id"):
            errs.append(f"{w}: id が無い")
        elif oid in seen:
            errs.append(f"{w}: id が重なっている")
        seen.add(oid)
        t = o.get("type")
        if t not in TYPES:
            errs.append(f"{w}: type {t!r} は {TYPES} のどれでもない（新しい型はコードに足してから）")
            continue
        has_unknown = False
        for name, req in FIELDS[t]:
            m = o.get(name)
            if m is None:
                if req:
                    errs.append(f"{w}.{name}: 欄が無い（分からなければ unknown で置く）")
                continue
            errs += check_measurement(m, f"{w}.{name}")
            has_unknown |= m.get("status") == "unknown" if isinstance(m, dict) else False
        if t == "bridge":
            ends = o.get("ends") or []
            if len(ends) != 2:
                errs.append(f"{w}.ends: 両端の 2 つが要る（{len(ends)} 個）")
            for k, e in enumerate(ends):
                we = f"{w}.ends[{k}]"
                bid, fid = e.get("building"), e.get("face")
                b = buildings.get(bid)
                if b is None:
                    errs.append(f"{we}: 棟 {bid!r} が data/buildings/ に無い")
                    continue
                ln = face_line(b, fid)
                if ln is None:
                    errs.append(f"{we}: 面 {fid!r} が棟 {bid} の JSON に無い")
                    continue
                L = math.dist(*ln)
                for name, req in END_FIELDS:
                    m = e.get(name)
                    if m is None:
                        if req:
                            errs.append(f"{we}.{name}: 欄が無い")
                        continue
                    errs += check_measurement(m, f"{we}.{name}")
                    has_unknown |= m.get("status") == "unknown"
                for name in ("along0", "along1"):
                    v = _val(e.get(name))
                    if v is not None and not (-_tol(e[name]) <= v <= L + _tol(e[name])):
                        errs.append(f"{we}.{name}: {v} m が面 {bid}.{fid} の長さ 0〜{L:.2f} m の外")
                a0, a1 = _val(e.get("along0")), _val(e.get("along1"))
                if a0 is not None and a1 is not None and a1 <= a0:
                    errs.append(f"{we}: along1（{a1}）が along0（{a0}）以下")
        if t == "asset":
            aid = o.get("asset")
            if not aid:
                errs.append(f"{w}.asset: アセットの id が無い")
            elif catalog_ids is not None and aid not in catalog_ids:
                errs.append(f"{w}.asset: {aid!r} が data/assets/catalog.json に無い（CC0 の台帳に載せてから）")
        if t in ("deck", "steps"):
            pv = _val(o.get("polygon"))
            if pv is not None and (not isinstance(pv, list) or len(pv) < 3):
                errs.append(f"{w}.polygon: 3 点以上の [[x,y],…] ではない")
            if t == "steps" and pv is not None and len(pv) != 4:
                errs.append(f"{w}.polygon: steps は 4 点（下の縁 2 点 → 上の縁 2 点）")
        if has_unknown and not any(r.get("status") == "open" for r in (o.get("requests") or [])):
            warns.append(f"{w}: unknown があるのに open の撮影依頼（requests）が無い")
        if not o.get("evidence"):
            warns.append(f"{w}: 物の有無の根拠（写真 id の evidence）が無い")
    return errs, warns


# --- 形（板と箱）-----------------------------------------------------------------------------

def bridge_quad(o: dict, buildings: dict[str, dict]) -> list[tuple[float, float]] | None:
    """両端の面の区間（along0〜along1）の 4 点を、辺が交わらない順に並べる。どれかが unknown なら None。"""
    pts = []
    for e in o["ends"]:
        ln = face_line(buildings[e["building"]], e["face"])
        a0, a1 = _val(e.get("along0")), _val(e.get("along1"))
        if ln is None or a0 is None or a1 is None:
            return None
        (x0, y0), (x1, y1) = ln
        L = math.dist(ln[0], ln[1])
        ux, uy = (x1 - x0) / L, (y1 - y0) / L
        pts.append(((x0 + ux * a0, y0 + uy * a0), (x0 + ux * a1, y0 + uy * a1)))
    (p0, p1), (q0, q1) = pts
    if math.dist(p1, q0) + math.dist(p0, q1) <= math.dist(p1, q1) + math.dist(p0, q0):
        return [p0, p1, q0, q1]
    return [p0, p1, q1, q0]


def _ccw(ring):
    a = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]))
    return ring if a > 0 else ring[::-1]


def _triangulate(ring):
    """耳切りで多角形（反時計回り）を三角形に。返り値は添字の 3 つ組。"""
    idx = list(range(len(ring)))
    tris = []

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = ring[i0], ring[i1], ring[i2]
            if cross(a, b, c) <= 1e-12:
                continue
            if any(cross(a, b, ring[j]) >= 0 and cross(b, c, ring[j]) >= 0 and cross(c, a, ring[j]) >= 0
                   for j in idx if j not in (i0, i1, i2)):
                continue
            tris.append((i0, i1, i2))
            idx.pop(k)
            break
        else:
            break
    if len(idx) == 3:
        tris.append(tuple(idx))
    return tris


def prism(ring, z0: float, z1: float) -> list[tuple]:
    """多角形を z0〜z1 に立ち上げた三角形の並び（(x,y,z) の 3 つ組。外向きが表）。"""
    ring = _ccw([tuple(p) for p in ring])
    out = []
    for a, b, c in _triangulate(ring):
        out.append(((*ring[a], z1), (*ring[b], z1), (*ring[c], z1)))
        out.append(((*ring[a], z0), (*ring[c], z0), (*ring[b], z0)))
    for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1]):
        out.append(((ax, ay, z0), (bx, by, z0), (bx, by, z1)))
        out.append(((ax, ay, z0), (bx, by, z1), (ax, ay, z1)))
    return out


def _wall(p, q, z0, z1, thick):
    """p→q の線に沿う薄い壁（線の左に thick）。"""
    dx, dy = q[0] - p[0], q[1] - p[1]
    L = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / L * thick, dx / L * thick
    return prism([p, q, (q[0] + nx, q[1] + ny), (p[0] + nx, p[1] + ny)], z0, z1)


def _lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def object_meshes(o: dict, buildings: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """1 つの物の形：[{"name","material","tris"}] と、描かなかった欄の理由。値の無い（unknown の）欄は描かない。"""
    t = o["type"]
    meshes, skipped = [], []
    oid = o["id"]
    if t == "bridge":
        quad = bridge_quad(o, buildings)
        fz = _val(o.get("floor_z"))
        if quad is None or fz is None:
            return [], [f"{oid}: 平面か床の高さが unknown なので描かない"]
        bd = _val(o.get("beam_depth"))
        if bd is None:
            skipped.append(f"{oid}.beam_depth: unknown（床は厚さ {DRAW_THICK_M} m の線で描いた。寸法ではない）")
        meshes.append({"name": f"{oid}_floor", "material": o.get("floor_material") or "brick",
                       "tris": prism(quad, fz - (bd or DRAW_THICK_M), fz)})
        ph = _val(o.get("parapet_height"))
        if ph is None:
            skipped.append(f"{oid}.parapet_height: unknown（腰壁は描かない）")
        else:
            # 腰壁は両端をつなぐ 2 本の長い辺（quad の 1→2 と 3→0）に沿って、内側に置く
            meshes.append({"name": f"{oid}_parapet", "material": o.get("parapet_material") or "tile",
                           "tris": _wall(quad[1], quad[2], fz, fz + ph, DRAW_THICK_M) +
                           _wall(quad[3], quad[0], fz, fz + ph, DRAW_THICK_M)})
    elif t == "deck":
        poly, z = _val(o.get("polygon")), _val(o.get("z"))
        if poly is None or z is None:
            return [], [f"{oid}: 平面か床の高さが unknown なので描かない"]
        th = _val(o.get("thickness"))
        if th is None:
            skipped.append(f"{oid}.thickness: unknown（厚さ {DRAW_THICK_M} m の線で描いた）")
        meshes.append({"name": f"{oid}_floor", "material": o.get("material") or "brick",
                       "tris": prism(poly, z - (th or DRAW_THICK_M), z)})
    elif t == "steps":
        poly, z0, z1, n = (_val(o.get(k)) for k in ("polygon", "z0", "z1", "count"))
        if None in (poly, z0, z1, n) or int(n) < 1:
            return [], [f"{oid}: 平面・高さ・段の数のどれかが unknown なので描かない（なめらかな坂にはしない）"]
        p0, p1, p2, p3 = [tuple(p) for p in poly]
        n = int(n)
        tris = []
        for i in range(n):
            ta, tb = i / n, (i + 1) / n
            ring = [_lerp(p0, p3, ta), _lerp(p1, p2, ta), _lerp(p1, p2, tb), _lerp(p0, p3, tb)]
            tris += prism(ring, z0, z0 + (z1 - z0) * (i + 1) / n)
        meshes.append({"name": f"{oid}_steps", "material": o.get("material") or "stone", "tris": tris})
    elif t == "asset":
        skipped.append(f"{oid}: 置物（{o.get('asset')}）は glb に描かず site.json の site_objects.assets に位置だけ出す")
    return meshes, skipped


# --- glb（trimesh を使わない最小の書き出し）---------------------------------------------------------

def _to_gltf(p):
    return (p[0], p[2], -p[1])      # (x 東, y 北, z 標高) → glTF (x, y 上, z 南)


def write_glb(path: Path, nodes: list[dict]) -> int:
    """nodes = [{"name", "extras", "meshes": [{"name","material","tris"}]}] を glb 1 つに。面ごとに頂点を分けた平らな陰影。返り値は三角形の数。"""
    mat_ids: dict[str, int] = {}
    materials = []
    buf = bytearray()
    views, accessors, meshes, gnodes = [], [], [], []
    ntri = 0

    def add_view(data: bytes, target: int) -> int:
        while len(buf) % 4:
            buf.append(0)
        views.append({"buffer": 0, "byteOffset": len(buf), "byteLength": len(data), "target": target})
        buf.extend(data)
        return len(views) - 1

    for nd in nodes:
        prims = []
        for m in nd["meshes"]:
            if not m["tris"]:
                continue
            mk = m["material"] if m["material"] in MATERIAL_RGBA else "unknown"
            if mk not in mat_ids:
                mat_ids[mk] = len(materials)
                materials.append({"name": f"site_{mk}", "doubleSided": True,
                                  "pbrMetallicRoughness": {"baseColorFactor": list(MATERIAL_RGBA[mk]),
                                                           "metallicFactor": 0.0, "roughnessFactor": 0.9}})
            pos, nor = [], []
            for tri in m["tris"]:
                a, b, c = (_to_gltf(p) for p in tri)
                ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
                vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
                nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
                ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
                for p in (a, b, c):
                    pos.append(p)
                    nor.append((nx / ln, ny / ln, nz / ln))
            ntri += len(m["tris"])
            pv = add_view(b"".join(struct.pack("<3f", *p) for p in pos), 34962)
            nv = add_view(b"".join(struct.pack("<3f", *n) for n in nor), 34962)
            xs, ys, zs = zip(*pos)
            accessors.append({"bufferView": pv, "componentType": 5126, "count": len(pos), "type": "VEC3",
                              "min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]})
            accessors.append({"bufferView": nv, "componentType": 5126, "count": len(nor), "type": "VEC3"})
            prims.append({"attributes": {"POSITION": len(accessors) - 2, "NORMAL": len(accessors) - 1},
                          "material": mat_ids[mk], "mode": 4})
        if not prims:
            continue
        meshes.append({"name": nd["name"], "primitives": prims})
        gnodes.append({"name": nd["name"], "mesh": len(meshes) - 1, "extras": nd.get("extras") or {}})
    while len(buf) % 4:
        buf.append(0)
    gltf = {"asset": {"version": "2.0", "generator": "campus-recorder site_objects"},
            "scene": 0, "scenes": [{"nodes": list(range(len(gnodes)))}], "nodes": gnodes, "meshes": meshes,
            "materials": materials, "accessors": accessors, "bufferViews": views,
            "buffers": [{"byteLength": len(buf)}]}
    js = json.dumps(gltf, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    js += b" " * (-len(js) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(buf), 0x004E4942) + bytes(buf)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body)
    return ntri


def read_glb_json(path: Path) -> dict:
    """glb の JSON の塊を読む（テストと確認用）。"""
    b = path.read_bytes()
    magic, ver, total = struct.unpack("<III", b[:12])
    if magic != 0x46546C67 or ver != 2 or total != len(b):
        raise ValueError(f"{path}: glb の頭が正しくない")
    ln, kind = struct.unpack("<II", b[12:20])
    if kind != 0x4E4F534A:
        raise ValueError(f"{path}: 最初の塊が JSON ではない")
    return json.loads(b[20:20 + ln].decode("utf-8"))


# --- 歩く画面への書き出し ------------------------------------------------------------------------

def _count_states(o: dict) -> dict:
    acc: dict = {}

    def walk(x):
        if isinstance(x, dict):
            s = x.get("status")
            if s in STATUSES and "value" in x:
                acc[s] = acc.get(s, 0) + 1
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(o)
    return acc


def export_web_objects(site: dict, data_dir: Path, buildings: dict | None = None, catalog_ids: set | None = None) -> dict:
    """<data_dir>/models/site_objects.glb と、site.json に入れる要約を作る。
    返り値 {"model": {"url","ground_z"} か None, "summary": {...}, "errors", "warnings", "skipped", "glb", "tris"}。
    検証で誤りがあれば glb は出さない（要約に誤りを載せる）。"""
    items = objects_of(site)
    if buildings is None:
        buildings = load_buildings(ends_buildings(site))
    if catalog_ids is None:
        catalog_ids = load_catalog_ids()
    errs, warns = validate_objects(site, buildings, catalog_ids)
    res = {"model": None, "errors": errs, "warnings": warns, "skipped": [], "glb": None, "tris": 0,
           "summary": {"schema": SCHEMA, "count": len(items), "objects": [], "assets": [], "errors": errs}}
    if errs or not items:
        return res
    nodes = []
    for o in items:
        meshes, skipped = object_meshes(o, buildings)
        res["skipped"] += skipped
        ent = {"id": o["id"], "type": o["type"], "name": o.get("name"), "counts": _count_states(o),
               "evidence": o.get("evidence") or [], "drawn": bool(meshes),
               "open_requests": sum(1 for r in (o.get("requests") or []) if r.get("status") == "open")}
        res["summary"]["objects"].append(ent)
        if o["type"] == "asset":
            pos = _val(o.get("position"))
            if pos is not None:
                res["summary"]["assets"].append({"id": o["id"], "asset": o.get("asset"), "x": pos[0], "y": pos[1],
                                                 "z": _val(o.get("z")), "rotation_deg": _val(o.get("rotation_deg")),
                                                 "status": o["position"].get("status")})
        if meshes:
            nodes.append({"name": o["id"], "meshes": meshes,
                          "extras": {"object": o["id"], "type": o["type"], "counts": ent["counts"]}})
    if nodes:
        glb = data_dir / "models" / WEB_GLB
        res["tris"] = write_glb(glb, nodes)
        res["glb"] = glb
        res["model"] = {"url": f"data/models/{WEB_GLB}", "ground_z": 0.0}
        res["summary"]["url"] = res["model"]["url"]
    res["summary"]["skipped"] = res["skipped"]
    res["summary"]["note"] = ("敷地の物（data/site/<campus>.json の objects）。glb の高さは標高そのもの（ground_z 0）。"
                              "unknown の欄は描かない。厚さが unknown の板は線の太さで描いた（寸法ではない）")
    return res


# --- 敷地の検収（shikichi 用の数字）-----------------------------------------------------------------

def check_objects(site: dict, buildings: dict[str, dict]) -> list[dict]:
    """橋の両端の高さ（標高）を棟の ground_z＋端の z で出し、橋の床 floor_z との差を tol の和と比べる。
    返り値は shikichi_kenshu.json の items と同じ形（what・buildings・diff_m・tol_m・ok・evidence・fix_in・action）。"""
    out = []
    for o in objects_of(site):
        if o.get("type") != "bridge":
            continue
        fz = o.get("floor_z") or {}
        zs = []
        for e in o.get("ends") or []:
            b = buildings.get(e.get("building"))
            if not b or _val(e.get("z")) is None or _val(b.get("ground_z")) is None:
                continue
            z_abs = _val(b["ground_z"]) + _val(e["z"])
            tol = math.hypot(_tol(e["z"]), _tol(b["ground_z"]))
            zs.append((e["building"], z_abs, tol))
            if _val(fz) is not None:
                d = round(_val(fz) - z_abs, 2)
                lim = round(_tol(fz) + tol, 2)
                out.append({"what": f"{o['id']}：橋の床と {e['building']}.{e.get('face')} の端の床の高さ（標高）",
                            "buildings": [e["building"]], "diff_m": d, "tol_m": lim, "ok": abs(d) <= lim,
                            "evidence": [f"site objects {o['id']}.floor_z", f"{e['building']}.ground_z＋ends.z"],
                            "fix_in": e["building"] if abs(d) > lim else None,
                            "action": "ok" if abs(d) <= lim else "request"})
        if len(zs) == 2:
            (ba, za, ta), (bb, zb, tb) = zs
            d = round(zb - za, 2)
            out.append({"what": f"{o['id']}：両端の棟の床の高さの差（{bb} − {ba}）", "buildings": [ba, bb],
                        "diff_m": d, "tol_m": round(ta + tb, 2), "ok": abs(d) <= ta + tb,
                        "evidence": [f"{ba}.ground_z＋ends.z", f"{bb}.ground_z＋ends.z"],
                        "fix_in": None if abs(d) <= ta + tb else "soten", "action": "ok" if abs(d) <= ta + tb else "soten"})
    return out
