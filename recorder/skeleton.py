"""土台の棟 JSON を公開データから起こす（写真が届く前の「灰色の箱」）。

PLATEAU の足元と高さ、5m 標高の地盤、面ごとの撮影依頼。値は全部 estimated（出どころ付き）か unknown。
写真が届いたら、設計者（Claude）が面ごとに bays / windows / parts を書き足していく。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from . import site as site_mod
from .schema import Measurement, new_building

COMPASS = ["北", "北北東", "北東", "東北東", "東", "東南東", "南東", "南南東", "南", "南南西", "南西", "西南西", "西", "西北西", "北西", "北北西"]


def _ccw(pts):
    a2 = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
    return list(pts) if a2 >= 0 else list(reversed(pts))


def _est(value, tol, evidence, method, unit="m"):
    return Measurement(round(value, 2), unit, tol, "estimated", list(evidence), method, "").to_json()


def _unk(unit="m", note=""):
    return Measurement(None, unit, None, "unknown", [], "", note).to_json()


def from_site(site_id: str, site_doc: dict, plateau_doc: dict, dem=None, plateau_ids: list[str] | None = None) -> dict:
    """敷地 JSON の棟 id から土台の棟 JSON を作る。plateau_ids を与えればその部品を使う（自動の対応が無い棟用）。"""
    b_site = next((b for b in site_doc["buildings"] if b["id"] == site_id), None)
    if b_site is None:
        raise KeyError(f"敷地に {site_id} がありません")
    parts = {p["gml_id"]: p for p in plateau_doc["buildings"]}
    ids = plateau_ids or b_site.get("plateau_parts") or []
    b = new_building(site_id, b_site.get("name") or site_id)
    b["site_id"] = site_id
    if not ids:
        # PLATEAU に無い棟：OSM の輪郭を仮の足元にする（航空写真の倒れ込みあり。写真で直す前提）
        ring = (b_site.get("footprint_osm") or [[]])[0]
        poly = _ccw([tuple(p) for p in ring]) if ring else []
        b["footprint"] = {"status": "estimated", "evidence": [f"osm:{b_site.get('osm')}"], "polygon": [list(p) for p in poly],
                          "method": "PLATEAU に無いので OSM の輪郭（航空写真由来、高い棟は南へ倒れ込む）。写真で直す"}
        h = None
        evid_h = []
    else:
        from shapely.geometry import Polygon
        from shapely.ops import unary_union

        polys = [Polygon(parts[i]["footprint"]) for i in ids if i in parts]
        u = unary_union(polys).buffer(0)
        if u.geom_type == "MultiPolygon":
            u = max(u.geoms, key=lambda g: g.area)
        poly = _ccw([(round(x, 2), round(y, 2)) for x, y in list(u.exterior.simplify(0.3).coords)[:-1]])
        b["footprint"] = {"status": "estimated", "evidence": [f"plateau:{i}" for i in ids], "polygon": [list(p) for p in poly],
                          "method": "PLATEAU 名古屋市 2022 版（2021 年測量）の LOD0 の足元形状"}
        hs = [parts[i]["height_m"] for i in ids if i in parts and parts[i].get("height_m")]
        h = max(hs) if hs else None
        evid_h = [f"plateau:{i}" for i in ids]
        st = [parts[i].get("storeys") for i in ids if i in parts and parts[i].get("storeys")]
        if st:
            b["floor_count"] = _est(max(st), None, evid_h, "PLATEAU の storeysAboveGround（目安。写真で数え直す）", unit="階")
    if h:
        b["parapet_top"] = _est(h, 1.5, evid_h, "PLATEAU の measuredHeight（塔屋を含む最高高さ。パラペットはこれより低いことが多い）")
    # 地盤：5m 標高を足元の中心で
    if poly and dem is not None:
        cx = sum(p[0] for p in poly) / len(poly)
        cy = sum(p[1] for p in poly) / len(poly)
        z = site_mod.dem_at(dem, cx, cy)
        if z is not None and not (isinstance(z, float) and math.isnan(z)):
            b["ground_z"] = _est(float(z), 1.0, ["gsi:dem5a"], "国土地理院 5m 標高を足元の中心で読む")
    # 面：足元の辺ごと。短い辺（2 m 未満）は飛ばす
    faces = []
    for i in range(len(poly)):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % len(poly)]
        L = math.hypot(x1 - x0, y1 - y0)
        if L < 2.0:
            continue
        nx, ny = (y1 - y0) / L, -(x1 - x0) / L
        az = (math.degrees(math.atan2(nx, ny)) + 360) % 360
        comp = COMPASS[int((az + 11.25) // 22.5) % 16]
        fid = f"f{len(faces)}"
        faces.append({"id": fid, "name": f"{comp}面 {i}（方位 {az:.0f}°、{L:.1f} m）", "from": [x0, y0], "to": [x1, y1],
                      "length": _est(L, 1.0, b["footprint"]["evidence"], "足元の辺の長さ"),
                      "bays": {}, "windows": [], "bands": [], "openings": [], "glazing": [], "ledges": [], "materials": []})
    b["faces"] = faces
    b["requests"] = [{"face": f["id"], "what": "両端が入る1枚と、壁沿いの斜め1枚", "where": "", "why": "この面はまだ写真が無い（土台の灰色の箱）", "status": "open"}
                     for f in faces if Measurement.from_json(f["length"]).value >= 8.0]
    b["history"] = [{"at": "", "by": "recorder new --from-site", "what": "PLATEAU と 5m 標高から土台を起こした。写真はまだ無い"}]
    return b


def from_site_cli(site_id: str, plateau_ids: list[str] | None = None) -> dict:
    site_doc = json.loads((site_mod.DATA_SITE / "tempaku.json").read_text(encoding="utf-8"))
    plateau_doc = json.loads((site_mod.DATA_SITE / "plateau.json").read_text(encoding="utf-8"))
    try:
        dem = site_mod.load_dem()
    except Exception:  # noqa: BLE001
        dem = None
    return from_site(site_id, site_doc, plateau_doc, dem, plateau_ids)
