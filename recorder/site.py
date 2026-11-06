"""敷地の土台（公開データからの自動生成）。

使う資料（CLAUDE.md の 3）：
    OpenStreetMap（ODbL）     敷地境界と建物の輪郭・名前
    国土地理院                 航空写真（seamlessphoto）と 5m 標高（dem5a）
    PLATEAU（名古屋市 2022）   建物の足元形状（LOD0）と高さ（LOD1）。土台にだけ使い、写真で上書きする

出力：
    data/site/cache/osm_raw.json      Overpass の生の返事（git 管理外）
    data/site/plateau.json            敷地内の PLATEAU 建物（足元形状と高さ）
    data/site/<campus>.json           敷地 JSON（唯一の元データ。建物の名前と確認状態をここに持つ）
    out/site/aerial_z18.png + .json   航空写真の切り出しと位置の対応
    out/site/dem5a.npz                5m 標高を 1m 格子に（原点からの m）
    out/site/overview.png             職員に見せる確認用の図

決まり：
    ここから出る値はすべて「土台」。棟 JSON の measured には絶対に流さない。
    OSM の building:levels や PLATEAU の高さは参考（reference）として敷地 JSON に残すだけ。
"""

from __future__ import annotations

import io
import json
import math
import re
import sys
import time
import warnings
import zipfile
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

import numpy as np

from . import geo

SITE_SCHEMA = "campus-recorder/site/1"
OSM_RELATION_TEMPAKU = 18057087
USER_AGENT = "campus-recorder/0.1 (Built with Claude Japan 2026)"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
GSI_PHOTO = "https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg"
GSI_DEM5A = "https://cyberjapandata.gsi.go.jp/xyz/dem5a_png/{z}/{x}/{y}.png"

DATA_SITE = Path("data/site")
OUT_SITE = Path("out/site")

CONFIRM_STATUSES = ("unconfirmed", "confirmed", "renamed", "removed", "new")


# --- OpenStreetMap -------------------------------------------------------------

def fetch_osm(relation_id: int = OSM_RELATION_TEMPAKU, cache: Path | None = None) -> dict:
    """敷地のリレーションと、その中の建物を Overpass から取る。cache があればそれを返す。"""
    cache = cache or DATA_SITE / "cache" / "osm_raw.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    import requests

    q = (
        "[out:json][timeout:120];"
        f"rel({relation_id});out geom;"
        f"rel({relation_id});map_to_area->.a;"
        '(way["building"](area.a);rel["building"](area.a););out geom;'
    )
    r = requests.post(OVERPASS_URL, data={"data": q}, headers={"User-Agent": USER_AGENT}, timeout=180)
    r.raise_for_status()
    d = r.json()
    d["_fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return d


def _ring_xy(geom: list[dict]) -> list[list[float]]:
    return [list(geo.lonlat_to_xy(p["lon"], p["lat"])) for p in geom]


def _osm_polygon(el: dict) -> list[list[list[float]]]:
    """way か multipolygon relation → 外周リングの一覧（原点からの m）。内側の穴は無視する。"""
    if el["type"] == "way":
        return [_ring_xy(el["geometry"])]
    from shapely.geometry import LineString
    from shapely.ops import polygonize, unary_union

    lines = []
    for m in el.get("members", []):
        if m.get("type") == "way" and m.get("role") in ("outer", "") and m.get("geometry"):
            lines.append(LineString(_ring_xy(m["geometry"])))
    if not lines:
        return []
    polys = list(polygonize(unary_union(lines)))
    return [[list(c) for c in p.exterior.coords] for p in polys]


def osm_site(raw: dict, relation_id: int = OSM_RELATION_TEMPAKU) -> dict:
    """生の返事から、敷地境界と建物の一覧（名前付き）を作る。"""
    rel = next(e for e in raw["elements"] if e["type"] == "relation" and e["id"] == relation_id)
    boundary = _osm_polygon(rel)
    buildings = []
    for e in raw["elements"]:
        t = e.get("tags", {})
        if "building" not in t:
            continue
        rings = _osm_polygon(e)
        if not rings:
            continue
        buildings.append({
            "osm": f"{e['type']}/{e['id']}",
            "name": t.get("name"),
            "building": t.get("building"),
            "levels_osm": t.get("building:levels"),
            "rings": [[[round(x, 2), round(y, 2)] for x, y in r] for r in rings],
        })
    return {"boundary": [[[round(x, 2), round(y, 2)] for x, y in r] for r in boundary],
            "buildings": buildings, "fetched_at": raw.get("_fetched_at")}


# --- OpenStreetMap：地表の種類（道・歩道・芝・駐車場）----------------------------------------
# 地面の塗り分けの根拠。値はすべて estimated（出典 OSM、ODbL）。道の幅は OSM の width が無ければ種類ごとの標準幅
# （「標準幅で描いた」と width_method に書く）。取得は 1 回（data/site/cache/osm_ground_raw.json）、
# 整えた物を敷地 JSON の ground_osm に置く（build_site で作り直しても引き継ぐ）。

GROUND_KINDS = ("unknown", "asphalt", "paving", "grass", "parking", "steps", "building")   # マスクの id ＝ この並びの番号
GROUND_KIND_LABEL = {"unknown": "根拠なし（OSM に無い）", "asphalt": "車道（アスファルト）", "paving": "歩道・広場（舗装）",
                     "grass": "芝・植え込み", "parking": "駐車場", "steps": "階段", "building": "建物の足元"}
GROUND_KIND_COLOR = {"unknown": (150, 146, 138), "asphalt": (62, 63, 66), "paving": (184, 172, 152), "grass": (98, 140, 70),
                     "parking": (104, 106, 110), "steps": (214, 208, 196), "building": (40, 40, 40)}
ROAD_WIDTH_M = {"road": 6.0, "service": 4.0, "foot": 2.0, "steps": 2.0}   # OSM に width が無い時の標準幅（m）
ROAD_HIGHWAYS = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street",
                 "road", "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"}
FOOT_HIGHWAYS = {"footway", "path", "pedestrian", "cycleway", "track", "corridor", "bridleway"}
GRASS_TAGS = (("landuse", "grass"), ("landuse", "meadow"), ("landuse", "forest"), ("landuse", "village_green"),
              ("leisure", "park"), ("leisure", "garden"), ("natural", "grassland"), ("natural", "scrub"),
              ("natural", "wood"), ("natural", "heath"))
SURFACE_KIND = {"asphalt": "asphalt", "paving_stones": "paving", "concrete": "paving",
                "concrete:plates": "paving", "sett": "paving", "bricks": "paving", "brick": "paving", "tiles": "paving",
                "grass": "grass", "grass_paver": "grass"}   # surface=paved は「舗装」としか言わないので種類の既定のまま


def ground_bbox_lonlat(x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
    """原点からの m の長方形 → (south, west, north, east)（Overpass の bbox の並び）。"""
    lon0, lat0 = geo.xy_to_lonlat(x0, y0)
    lon1, lat1 = geo.xy_to_lonlat(x1, y1)
    return round(min(lat0, lat1), 6), round(min(lon0, lon1), 6), round(max(lat0, lat1), 6), round(max(lon0, lon1), 6)


def fetch_osm_ground(bbox_xy: tuple[float, float, float, float], cache: Path | None = None) -> dict:
    """道・歩道・芝・駐車場・木を Overpass から 1 回だけ取る（cache があればそれを返す）。bbox_xy は (x0, y0, x1, y1) m。"""
    cache = cache or DATA_SITE / "cache" / "osm_ground_raw.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    import requests

    s, w, n, e = ground_bbox_lonlat(*bbox_xy)
    bb = f"({s},{w},{n},{e})"
    q = ("[out:json][timeout:120];("
         f'way["highway"]{bb};way["area:highway"]{bb};'
         f'way["landuse"]{bb};rel["landuse"]{bb};way["leisure"]{bb};rel["leisure"]{bb};'
         f'way["amenity"="parking"]{bb};rel["amenity"="parking"]{bb};'
         f'way["natural"]{bb};rel["natural"]{bb};node["natural"="tree"]{bb};'
         ");out geom;")
    r = requests.post(OVERPASS_URL, data={"data": q}, headers={"User-Agent": USER_AGENT}, timeout=180)
    r.raise_for_status()
    d = r.json()
    d["_fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    d["_query"] = q
    d["_bbox_xy"] = [round(float(v), 1) for v in bbox_xy]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return d


def _osm_width(t: dict) -> float | None:
    m = re.match(r"^\s*([\d.]+)\s*(m)?\s*$", str(t.get("width", "")))
    return float(m.group(1)) if m else None


def classify_osm_ground(t: dict, closed: bool) -> tuple[str, str, str] | None:
    """OSM のタグ → (種類, 形 "line"|"area", 種類を決めたタグ)。地表の種類に関係しない物（landuse=education 等）は None。"""
    hw, sf = t.get("highway"), t.get("surface")
    if t.get("area:highway"):
        ah = t["area:highway"]
        k = SURFACE_KIND.get(sf, "asphalt" if ah in ROAD_HIGHWAYS | {"service"} else "paving")
        return k, "area", f"area:highway={ah}" + (f", surface={sf}" if sf else "")
    if hw:
        is_area = closed and (t.get("area") == "yes" or hw == "pedestrian")
        if hw == "steps":
            return "steps", "line", "highway=steps"
        if hw in ROAD_HIGHWAYS:
            base = "asphalt"
        elif hw == "service":
            base = "parking" if t.get("service") == "parking_aisle" else "asphalt"
        elif hw in FOOT_HIGHWAYS:
            base = "paving"
        else:
            return None
        k = SURFACE_KIND.get(sf, base)
        why = f"highway={hw}" + (f", surface={sf}" if sf in SURFACE_KIND else
                                 f", surface={sf}（種類の既定）" if sf else "（surface タグ無し：種類の既定）")
        return k, ("area" if is_area else "line"), why
    if not closed:
        return None
    if t.get("amenity") == "parking":
        return "parking", "area", "amenity=parking"
    if t.get("leisure") == "pitch":
        k = SURFACE_KIND.get(sf)
        return (k, "area", f"leisure=pitch, surface={sf}") if k else None
    for key, v in GRASS_TAGS:
        if t.get(key) == v:
            return "grass", "area", f"{key}={v}"
    return None


def _road_class(t: dict) -> str:
    hw = t.get("highway")
    if hw == "steps":
        return "steps"
    if hw == "service":
        return "service"
    if hw in FOOT_HIGHWAYS:
        return "foot"
    return "road"


def osm_ground(raw: dict) -> dict:
    """生の返事から、地表の種類の図形の一覧（原点からの m）を作る。敷地 JSON の ground_osm に入る。"""
    feats, trees = [], []
    for e in raw.get("elements", []):
        t = e.get("tags", {})
        if e["type"] == "node":
            if t.get("natural") == "tree":
                x, y = geo.lonlat_to_xy(e["lon"], e["lat"])
                trees.append({"osm": f"node/{e['id']}", "xy": [round(x, 2), round(y, 2)]})
            continue
        if e["type"] == "way":
            g = e.get("geometry") or []
            if len(g) < 2:
                continue
            closed = len(g) >= 4 and g[0] == g[-1]
        else:
            closed = True
        c = classify_osm_ground(t, closed)
        if not c:
            continue
        kind, shape, why = c
        f = {"osm": f"{e['type']}/{e['id']}", "kind": kind, "shape": shape, "tags_used": why}
        if t.get("name"):
            f["name"] = t["name"]
        if shape == "line":
            w = _osm_width(t)
            rc = _road_class(t)
            f["coords"] = [[round(x, 2), round(y, 2)] for x, y in _ring_xy(e["geometry"])]
            f["width_m"] = w if w else ROAD_WIDTH_M[rc]
            f["width_method"] = f"OSM width={t['width']}" if w else f"OSM に width が無いので {rc} の標準幅 {ROAD_WIDTH_M[rc]:g} m"
        else:
            rings = _osm_polygon(e)
            if not rings:
                continue
            f["rings"] = [[[round(x, 2), round(y, 2)] for x, y in r] for r in rings]
        feats.append(f)
    return {"source": "OpenStreetMap contributors（ODbL）", "license": "ODbL", "fetched_at": raw.get("_fetched_at"),
            "bbox_xy": raw.get("_bbox_xy"), "status": "estimated",
            "method": "OSM のタグから地表の種類を決める（surface があればそれ、無ければ種類の既定）。道は中心線を幅で太らせる",
            "standard_width_m": ROAD_WIDTH_M, "features": feats, "trees": trees}


# --- 国土地理院 -------------------------------------------------------------------

def _bbox_lonlat(rings: Iterable[list[list[float]]], margin_m: float = 60.0) -> tuple[float, float, float, float]:
    pts = [p for r in rings for p in r]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs) - margin_m, max(xs) + margin_m, min(ys) - margin_m, max(ys) + margin_m
    lon0, lat0 = geo.xy_to_lonlat(x0, y0)
    lon1, lat1 = geo.xy_to_lonlat(x1, y1)
    return lon0, lat0, lon1, lat1


def _get(url: str, cache_dir: Path) -> bytes | None:
    import requests

    name = re.sub(r"[^0-9A-Za-z._-]+", "_", url.split("//", 1)[1])
    p = cache_dir / name
    if p.exists():
        return p.read_bytes()
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    cache_dir.mkdir(parents=True, exist_ok=True)
    p.write_bytes(r.content)
    return r.content


def fetch_aerial(rings, z: int = 18, out: Path | None = None, cache_dir: Path | None = None) -> dict:
    """航空写真タイルをつなげて bbox で切り出す。位置の対応（タイル座標→緯度経度）を json に残す。"""
    from PIL import Image

    out = out or OUT_SITE / f"aerial_z{z}.png"
    cache_dir = cache_dir or DATA_SITE / "cache" / "gsi"
    lon0, lat0, lon1, lat1 = _bbox_lonlat(rings)
    tx0, ty1 = geo.lonlat_to_tile(lon0, lat0, z)   # 南西
    tx1, ty0 = geo.lonlat_to_tile(lon1, lat1, z)   # 北東
    X0, X1, Y0, Y1 = int(tx0), int(tx1), int(ty0), int(ty1)
    W, H = (X1 - X0 + 1) * 256, (Y1 - Y0 + 1) * 256
    canvas = Image.new("RGB", (W, H), (0, 0, 0))
    n = 0
    for tx in range(X0, X1 + 1):
        for ty in range(Y0, Y1 + 1):
            b = _get(GSI_PHOTO.format(z=z, x=tx, y=ty), cache_dir)
            if b:
                canvas.paste(Image.open(io.BytesIO(b)).convert("RGB"), ((tx - X0) * 256, (ty - Y0) * 256))
                n += 1
    px0, py0 = int((tx0 - X0) * 256), int((ty0 - Y0) * 256)
    px1, py1 = int((tx1 - X0) * 256), int((ty1 - Y0) * 256)
    img = canvas.crop((px0, py0, px1, py1))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    meta = {"z": z, "tile_x0": X0 + px0 / 256, "tile_y0": Y0 + py0 / 256,
            "tile_x1": X0 + px1 / 256, "tile_y1": Y0 + py1 / 256,
            "width": img.width, "height": img.height, "tiles": n,
            "source": "国土地理院 全国最新写真（シームレス） https://maps.gsi.go.jp/development/ichiran.html"}
    out.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def aerial_xy_to_px(meta: dict, x: float, y: float) -> tuple[float, float]:
    """原点からの m → 航空写真の画素。"""
    lon, lat = geo.xy_to_lonlat(x, y)
    tx, ty = geo.lonlat_to_tile(lon, lat, meta["z"])
    return (tx - meta["tile_x0"]) * 256, (ty - meta["tile_y0"]) * 256


def aerial_px_to_xy(meta: dict, px: float, py: float) -> tuple[float, float]:
    """航空写真の画素 → 原点からの m。"""
    lon, lat = geo.tile_to_lonlat(meta["tile_x0"] + px / 256, meta["tile_y0"] + py / 256, meta["z"])
    return geo.lonlat_to_xy(lon, lat)


def _decode_dem_png(b: bytes) -> np.ndarray:
    from PIL import Image

    a = np.asarray(Image.open(io.BytesIO(b)).convert("RGB"), dtype=np.int64)
    v = a[..., 0] * 65536 + a[..., 1] * 256 + a[..., 2]
    out = v.astype(np.float64) * 0.01
    out[v >= 2 ** 23] -= 2 ** 24 * 0.01
    out[v == 2 ** 23] = np.nan  # 無効値
    return out


def fetch_dem(rings, z: int = 15, step_m: float = 1.0, out: Path | None = None, cache_dir: Path | None = None) -> dict:
    """5m 標高（dem5a）を取り、原点からの m の格子（step_m 間隔）に並べ直す（最近傍。補間はしない）。"""
    out = out or OUT_SITE / "dem5a.npz"
    cache_dir = cache_dir or DATA_SITE / "cache" / "gsi"
    lon0, lat0, lon1, lat1 = _bbox_lonlat(rings, margin_m=100.0)
    tx0, ty1 = geo.lonlat_to_tile(lon0, lat0, z)
    tx1, ty0 = geo.lonlat_to_tile(lon1, lat1, z)
    X0, X1, Y0, Y1 = int(tx0), int(tx1), int(ty0), int(ty1)
    grid = np.full(((Y1 - Y0 + 1) * 256, (X1 - X0 + 1) * 256), np.nan)
    missing = []
    for tx in range(X0, X1 + 1):
        for ty in range(Y0, Y1 + 1):
            b = _get(GSI_DEM5A.format(z=z, x=tx, y=ty), cache_dir)
            if b is None:
                missing.append((tx, ty))
                continue
            grid[(ty - Y0) * 256:(ty - Y0 + 1) * 256, (tx - X0) * 256:(tx - X0 + 1) * 256] = _decode_dem_png(b)

    pts = [p for r in rings for p in r]
    x_min, x_max = math.floor(min(p[0] for p in pts) - 100), math.ceil(max(p[0] for p in pts) + 100)
    y_min, y_max = math.floor(min(p[1] for p in pts) - 100), math.ceil(max(p[1] for p in pts) + 100)
    gx = np.arange(x_min, x_max + step_m, step_m)
    gy = np.arange(y_min, y_max + step_m, step_m)
    # 格子点を一括で緯度経度→タイル画素に変換
    from pyproj import Transformer

    _, inv, ox, oy = geo._transformers()
    GX, GY = np.meshgrid(gx, gy)
    lon, lat = inv.transform(GX + ox, GY + oy)
    n = 2 ** z
    txf = (lon + 180.0) / 360.0 * n
    lat_r = np.radians(lat)
    tyf = (1.0 - np.log(np.tan(lat_r) + 1.0 / np.cos(lat_r)) / np.pi) / 2.0 * n
    px = ((txf - X0) * 256).astype(int)
    py = ((tyf - Y0) * 256).astype(int)
    ok = (px >= 0) & (px < grid.shape[1]) & (py >= 0) & (py < grid.shape[0])
    Z = np.full(GX.shape, np.nan)
    Z[ok] = grid[py[ok], px[ok]]
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, z=Z, x=gx, y=gy)
    return {"shape": list(Z.shape), "x_range": [float(gx[0]), float(gx[-1])], "y_range": [float(gy[0]), float(gy[-1])],
            "missing_tiles": missing, "nan_ratio": float(np.isnan(Z).mean()),
            "z_min": float(np.nanmin(Z)), "z_max": float(np.nanmax(Z)),
            "source": "国土地理院 標高タイル（DEM5A）"}


def load_dem(path: Path | None = None) -> dict:
    d = np.load(path or OUT_SITE / "dem5a.npz")
    return {"z": d["z"], "x": d["x"], "y": d["y"]}


def dem_at(dem: dict, x: float, y: float) -> float | None:
    """格子から標高を読む（最近傍）。範囲外・無効は None。"""
    gx, gy, Z = dem["x"], dem["y"], dem["z"]
    i = int(round((x - gx[0]) / (gx[1] - gx[0])))
    j = int(round((y - gy[0]) / (gy[1] - gy[0])))
    if 0 <= j < Z.shape[0] and 0 <= i < Z.shape[1] and not np.isnan(Z[j, i]):
        return float(Z[j, i])
    return None


# --- 地形（棟の模型の地面）-------------------------------------------------------------
# 模型の地面は国土地理院 5m 標高から作る 1 枚の面。面ごとの ground_offset は形には使わず、検算にだけ使う。
# Blender の中（blender_build.py）からも `from recorder import site` で読む（numpy だけで動くこと）。

TERRAIN_MARGIN_M = 20.0        # 足元の外側この距離までを細かい格子（1 m）で
TERRAIN_SMOOTH_PX = 5          # 5m 標高の画素（1 m 格子では 4〜5 個の同じ値の段）を均す窓。標高の解像度より細かい形は作らない
FACE_BAND_M = (0.0, 12.0)      # ground_offset の検算に読む面の前の帯（面からの m）。棟 JSON の method と同じ


def fill_gaps(Z: np.ndarray) -> tuple[np.ndarray, float]:
    """欠け（NaN）を隣の有効値の平均で外から埋める（補間ではなく延長）。返り値：(埋めた格子, 欠けの割合)。
    export_web の地面格子と模型の地形で共用する。全部欠けていれば NaN のまま返す。"""
    Z = np.array(Z, dtype=float)
    valid = ~np.isnan(Z)
    ratio = float(1 - valid.mean()) if Z.size else 0.0
    if not valid.any():
        return Z, ratio
    for _ in range(Z.shape[0] + Z.shape[1]):
        bad = np.isnan(Z)
        if not bad.any():
            break
        P = np.pad(Z, 1, constant_values=np.nan)
        nb = np.stack([P[:-2, 1:-1], P[2:, 1:-1], P[1:-1, :-2], P[1:-1, 2:]])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            m = np.nanmean(nb, axis=0)
        Z = np.where(bad, m, Z)
    return Z, ratio


def box_mean(Z: np.ndarray, k: int) -> np.ndarray:
    """k×k の窓の平均（端は端の値で延長）。k<=1 はそのまま。"""
    if k <= 1:
        return Z
    r = k // 2
    P = np.pad(Z, r, mode="edge")
    c = np.cumsum(np.cumsum(np.pad(P, ((1, 0), (1, 0))), axis=0), axis=1)
    h, w = Z.shape
    s = c[k:k + h, k:k + w] - c[0:h, k:k + w] - c[k:k + h, 0:w] + c[0:h, 0:w]
    return s / (k * k)


def polygon_distance(px: np.ndarray, py: np.ndarray, poly) -> np.ndarray:
    """点（配列）から多角形までの距離（m）。中は 0。"""
    px, py = np.asarray(px, float), np.asarray(py, float)
    d = np.full(px.shape, np.inf)
    inside = np.zeros(px.shape, bool)
    n = len(poly)
    for i in range(n):
        (ax, ay), (bx, by) = poly[i], poly[(i + 1) % n]
        ex, ey = bx - ax, by - ay
        L2 = ex * ex + ey * ey or 1e-12
        t = np.clip(((px - ax) * ex + (py - ay) * ey) / L2, 0, 1)
        d = np.minimum(d, np.hypot(px - (ax + t * ex), py - (ay + t * ey)))
        cross = ((ay > py) != (by > py)) & (px < ax + (py - ay) * (bx - ax) / ((by - ay) or 1e-12))
        inside ^= cross
    return np.where(inside, 0.0, d)


def dem_sample(dem: dict, X: np.ndarray, Y: np.ndarray) -> np.ndarray:
    """dem_at を配列でまとめて読む（最近傍。範囲外・無効は NaN）。"""
    gx, gy, Z = dem["x"], dem["y"], dem["z"]
    i = np.rint((np.asarray(X, float) - gx[0]) / (gx[1] - gx[0])).astype(int)
    j = np.rint((np.asarray(Y, float) - gy[0]) / (gy[1] - gy[0])).astype(int)
    ok = (i >= 0) & (i < Z.shape[1]) & (j >= 0) & (j < Z.shape[0])
    out = np.full(np.shape(X), np.nan)
    out[ok] = Z[j[ok], i[ok]]
    return out


def terrain_grid(dem: dict, poly, ground_z: float, margin_m: float = TERRAIN_MARGIN_M, step_m: float = 1.0,
                 smooth_px: int = TERRAIN_SMOOTH_PX) -> dict:
    """足元の外接長方形の外側 margin_m までの地形の格子（step_m おき）。高さは ground_z を引いた値（棟の z=0 からの m）。

    返り値 {x, y, z, mask, nan_ratio}：x, y は 1 次元、z は (ny, nx)、mask は面を張る格子点（長方形なので全部 True）。
    1 m 格子で読み、欠けは fill_gaps で近傍から埋め、5m 標高の画素の段を smooth_px の窓で均してから step_m おきに間引く
    （粗い格子でも細かい格子と同じ高さになり、継ぎ目が合う）。"""
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    k = max(1, int(round(step_m)))
    x0, y0 = math.floor(min(xs) - margin_m), math.floor(min(ys) - margin_m)
    nx = int(math.ceil((max(xs) + margin_m - x0) / k)) * k + 1
    ny = int(math.ceil((max(ys) + margin_m - y0) / k)) * k + 1
    gx1 = x0 + np.arange(nx, dtype=float)
    gy1 = y0 + np.arange(ny, dtype=float)
    GX, GY = np.meshgrid(gx1, gy1)
    Z, ratio = fill_gaps(dem_sample(dem, GX, GY))
    if np.isnan(Z).all():
        raise ValueError("足元の周りに 5m 標高が 1 点もありません")
    Z = box_mean(Z, max(1, int(smooth_px))) - float(ground_z)
    Z = Z[::k, ::k]
    return {"x": gx1[::k], "y": gy1[::k], "z": Z, "mask": np.ones(Z.shape, bool), "nan_ratio": ratio}


def grid_z(t: dict, x: float, y: float) -> float:
    """格子（terrain_grid）の高さを双一次で読む。範囲外は端の値。"""
    gx, gy, Z = t["x"], t["y"], t["z"]
    fx = min(max((x - gx[0]) / (gx[1] - gx[0]), 0.0), len(gx) - 1.000001)
    fy = min(max((y - gy[0]) / (gy[1] - gy[0]), 0.0), len(gy) - 1.000001)
    i, j = int(fx), int(fy)
    a, c = fx - i, fy - j
    return float((Z[j, i] * (1 - a) + Z[j, i + 1] * a) * (1 - c) + (Z[j + 1, i] * (1 - a) + Z[j + 1, i + 1] * a) * c)


def face_band_offset(dem: dict, p0, p1, ground_z: float, band=FACE_BAND_M, step_m: float = 1.0) -> float | None:
    """面の前の帯（面の幅 × 面から band m、step_m おき）の 5m 標高の中央値 − ground_z。棟 JSON の ground_offset の読み方と同じ。
    外向き法線は (u.y, −u.x)（blender_build.face_frame と同じ）。"""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    L = math.hypot(dx, dy)
    if L == 0:
        return None
    ux, uy = dx / L, dy / L
    nx, ny = uy, -ux
    vals = []
    for a in np.arange(0.0, L + 1e-6, step_m):
        for n in np.arange(band[0], band[1] + 1e-6, step_m):
            z = dem_at(dem, p0[0] + ux * a + nx * n, p0[1] + uy * a + ny * n)
            if z is not None:
                vals.append(z)
    return float(np.median(vals)) - float(ground_z) if vals else None


def _mval(m):
    if isinstance(m, (int, float)) and not isinstance(m, bool):
        return float(m), None
    if isinstance(m, dict) and m.get("value") is not None and m.get("status") != "unknown":
        return float(m["value"]), m.get("tol")
    return None, None


def check_ground_offsets(dem: dict, b: dict) -> tuple[list[dict], list[str]]:
    """faces[].ground_offset を地形で検算する。帯の中央値との差が tol（無ければ 0.5 m）を超えたら warning。"""
    gz, _ = _mval(b.get("ground_z"))
    checks, warns = [], []
    if gz is None:
        return checks, warns
    for f in b.get("faces", []):
        go, tol = _mval(f.get("ground_offset"))
        if go is None or not f.get("from") or not f.get("to"):
            continue
        band = face_band_offset(dem, f["from"], f["to"], gz)
        if band is None:
            continue
        tol = float(tol) if tol is not None else 0.5
        diff = round(go - band, 2)
        ok = abs(diff) <= tol
        checks.append({"face": f["id"], "ground_offset": go, "terrain_band_median": round(band, 2), "diff": diff, "tol": tol, "ok": ok})
        if not ok:
            warns.append(f"{f['id']} の ground_offset {go:+.2f} m が地形（面の前 {FACE_BAND_M[0]:g}〜{FACE_BAND_M[1]:g} m の中央値 {band:+.2f} m）と "
                         f"{diff:+.2f} m ずれる（tol {tol} m）。棟 JSON の値か ground_z を見直す")
    return checks, warns

# --- PLATEAU ---------------------------------------------------------------------

NS = {
    "gml": "http://www.opengis.net/gml",
    "bldg": "http://www.opengis.net/citygml/building/2.0",
    "core": "http://www.opengis.net/citygml/2.0",
}


def mesh3_of_lonlat(lon: float, lat: float) -> str:
    """緯度経度 → 3次メッシュコード（PLATEAU のファイル名の先頭 8 桁）。"""
    p = int(lat * 1.5)
    u = int(lon) - 100
    q = int((lat * 1.5 - p) * 8)
    v = int((lon - int(lon)) * 8)
    r = int(((lat * 1.5 - p) * 8 - q) * 10)
    w = int(((lon - int(lon)) * 8 - v) * 10)
    return f"{p:02d}{u:02d}{q}{v}{r}{w}"


def extract_plateau(zip_path: str | Path, rings, out: Path | None = None) -> dict:
    """PLATEAU の建物 GML から、敷地境界に重なる建物の足元形状（LOD0）と高さ（LOD1）を抜く。"""
    from shapely.geometry import MultiPolygon, Polygon

    out = out or DATA_SITE / "plateau.json"
    site_poly = MultiPolygon([Polygon(r) for r in rings if len(r) >= 3]).buffer(0)
    pts = [p for r in rings for p in r]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    meshes = set()
    for x in np.linspace(min(xs) - 50, max(xs) + 50, 8):
        for y in np.linspace(min(ys) - 50, max(ys) + 50, 8):
            meshes.add(mesh3_of_lonlat(*geo.xy_to_lonlat(float(x), float(y))))
    z = zipfile.ZipFile(zip_path)
    files = [n for n in z.namelist() if n.endswith(".gml") and "/bldg/" in n and any(m in n for m in meshes)]
    found = []
    for name in files:
        with z.open(name) as f:
            for _, el in ET.iterparse(f, events=("end",)):
                if el.tag != f"{{{NS['bldg']}}}Building":
                    continue
                b = _parse_building(el)
                el.clear()
                if not b:
                    continue
                poly = Polygon(b["footprint"])
                if not poly.is_valid:
                    poly = poly.buffer(0)
                if poly.area > 0 and poly.intersection(site_poly).area / poly.area > 0.3:
                    b["file"] = name
                    found.append(b)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {"schema": "campus-recorder/plateau/1",
           "source": "PLATEAU 名古屋市 2022（国土交通省）、建物 LOD0/LOD1 のみ",
           "zip": Path(zip_path).name, "meshes": sorted(meshes), "files": files, "buildings": found}
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc


def _parse_building(el: ET.Element) -> dict | None:
    gid = el.get(f"{{{NS['gml']}}}id")
    h = el.find("bldg:measuredHeight", NS)
    fp = el.find("bldg:lod0FootPrint", NS)
    if fp is None:
        fp = el.find("bldg:lod0RoofEdge", NS)
    if fp is None:
        return None
    pos = fp.find(".//gml:posList", NS)
    if pos is None or not pos.text:
        return None
    v = [float(t) for t in pos.text.split()]
    pts = []
    for i in range(0, len(v) - 2, 3):
        lat, lon = v[i], v[i + 1]
        pts.append([round(c, 2) for c in geo.lonlat_to_xy(lon, lat)])
    if len(pts) < 4:
        return None
    base = None
    solid = el.find(".//bldg:lod1Solid//gml:posList", NS)
    if solid is not None and solid.text:
        sv = [float(t) for t in solid.text.split()]
        base = min(sv[2::3]) if len(sv) >= 3 else None
    usage = el.find("bldg:usage", NS)
    name = el.find("gml:name", NS)
    storeys = el.find("bldg:storeysAboveGround", NS)
    return {
        "gml_id": gid,
        "name": name.text if name is not None else None,
        "usage": usage.text if usage is not None else None,
        "height_m": (float(h.text) if h is not None and h.text and float(h.text) > -1000 else None),  # -9999 は欠測
        "base_z": round(base, 2) if base is not None else None,
        "storeys": int(storeys.text) if storeys is not None and storeys.text else None,
        "footprint": pts[:-1] if pts[0] == pts[-1] else pts,
    }


# --- 敷地 JSON ----------------------------------------------------------------------

# --- 敷地の立ち上げ（大学ごとの設定は data/site/<campus>.json に持つ。コードに大学名・棟名を書かない）-----------------------

NON_CAMPUS_JSON = {"plateau.json"}


def campus_path(campus: str, data_site: Path | None = None) -> Path:
    return (data_site or DATA_SITE) / f"{campus}.json"


def load_campus(campus: str, data_site: Path | None = None) -> dict:
    """敷地 JSON を読む。無ければ {}。"""
    p = campus_path(campus, data_site)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def campus_aliases(campus: str, data_site: Path | None = None) -> dict[str, str]:
    """別名（OSM の名前・台帳の場所名・職員の呼び名）→ 棟 id。敷地 JSON の aliases。無ければ {}。"""
    return dict(load_campus(campus, data_site).get("aliases") or {})


def default_campus(data_site: Path | None = None) -> str:
    """既定のキャンパス。data/site/ の <campus>.json（plateau.json は除く）が 1 つだけならそれ。
    複数なら default_campus.txt の中身。どちらでもなければエラー。"""
    d = data_site or DATA_SITE
    found = sorted(p.stem for p in d.glob("*.json") if p.name not in NON_CAMPUS_JSON) if d.exists() else []
    if len(found) == 1:
        return found[0]
    txt = d / "default_campus.txt"
    if txt.exists():
        v = txt.read_text(encoding="utf-8").strip()
        if v:
            return v
    if not found:
        raise ValueError(f"敷地 JSON が {d} にありません。`recorder campus init <slug> --university ... --campus-name ...` で作ってください")
    raise ValueError(f"敷地 JSON が複数あります（{', '.join(found)}）。--campus で指すか、{txt} に既定の名前を 1 行書いてください")


def plateau_zip(campus: str, data_site: Path | None = None) -> str | None:
    """PLATEAU の zip の場所（敷地 JSON の sources.plateau_zip）。"""
    return (load_campus(campus, data_site).get("sources") or {}).get("plateau_zip")


def _slug(name: str | None, osm: str, aliases: dict[str, str] | None = None) -> str:
    if name and aliases and name in aliases:
        return aliases[name]
    return "osm-" + osm.replace("/", "-")


CAMPUS_CARRY_KEYS = ("university", "campus_name", "aliases", "scope", "name_links", "questions")


def build_site(campus: str, osm: dict, plateau: dict | None, out: Path | None = None) -> dict:
    """OSM の建物を軸に、PLATEAU の部品を足元の重なりで結びつけ、確認状態を付けて敷地 JSON を作る。
    既存の敷地 JSON があれば、職員の確認（confirmation）と追加（staff_additions）は引き継ぐ。"""
    from shapely.geometry import MultiPolygon, Polygon

    out = out or DATA_SITE / f"{campus}.json"
    prev = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    prev_b = {b["id"]: b for b in prev.get("buildings", [])}
    aliases = prev.get("aliases") or campus_aliases(campus)

    pl = []
    for p in (plateau or {}).get("buildings", []):
        poly = Polygon(p["footprint"])
        pl.append((p, poly if poly.is_valid else poly.buffer(0)))

    buildings = []
    used = set()
    for b in osm["buildings"]:
        bid = _slug(b["name"], b["osm"], aliases)
        shape = MultiPolygon([Polygon(r) for r in b["rings"] if len(r) >= 3]).buffer(0)
        parts = [p["gml_id"] for p, poly in pl
                 if poly.area > 0 and poly.intersection(shape).area / poly.area > 0.5]
        match = "overlap" if parts else "none"
        if not parts:
            # 高い建物は航空写真（OSM の元）で倒れ込み、足元が数十 m ずれることがある。
            # 重ならない時は 15 m 以内の PLATEAU 部品を「近く」として残し、確定は人（か写真）に任せる
            parts = [p["gml_id"] for p, poly in pl if p["gml_id"] not in used and poly.distance(shape) < 15.0]
            match = "nearby" if parts else "none"
        used.update(parts)
        heights = [p["height_m"] for p, _ in pl if p["gml_id"] in parts and p["height_m"]]
        bases = [p["base_z"] for p, _ in pl if p["gml_id"] in parts and p["base_z"] is not None]
        old = prev_b.get(bid, {})
        conf = old.get("confirmation") or {"status": "unconfirmed", "by": None, "at": None, "note": ""}
        staff_named = conf.get("status") in ("confirmed", "renamed", "new") and old.get("name")
        buildings.append({
            "id": bid,
            "name": old["name"] if staff_named else b["name"],
            "name_source": old.get("name_source") if staff_named else ("osm" if b["name"] else None),
            "osm": b["osm"],
            "building_tag": b["building"],
            "reference": {"levels_osm": b["levels_osm"],
                          "plateau_height_m": max(heights) if heights else None,
                          "plateau_base_z": min(bases) if bases else None,
                          "note": "参考値。棟 JSON の measured には使わない"},
            "footprint_osm": b["rings"],
            "plateau_parts": parts,
            "plateau_match": match,
            "centroid": [round(shape.centroid.x, 1), round(shape.centroid.y, 1)],
            "confirmation": conf,
        })
    extra = [p["gml_id"] for p, _ in pl if p["gml_id"] not in used]
    doc = {
        "schema": SITE_SCHEMA,
        "campus": campus,
        "crs": {"epsg": geo.EPSG_PLANE, "origin_lat": geo.ORIGIN_LAT, "origin_lon": geo.ORIGIN_LON,
                "note": "原点からの m。x=東, y=北。z は標高 m"},
        "boundary_osm": osm["boundary"],
        "sources": {"osm": {"relation": OSM_RELATION_TEMPAKU, "license": "ODbL", "fetched_at": osm.get("fetched_at")},
                    "plateau": (plateau or {}).get("source"),
                    "gsi": "国土地理院 航空写真・DEM5A"},
        "confirmation_statuses": list(CONFIRM_STATUSES),
        "buildings": buildings,
        "plateau_only": extra,
        "staff_additions": prev.get("staff_additions", []),
    }
    for k in CAMPUS_CARRY_KEYS:                       # 立ち上げの欄は作り直しても引き継ぐ
        if k in prev:
            doc[k] = prev[k]
    for k, v in (prev.get("sources") or {}).items():
        doc["sources"].setdefault(k, v)
    if prev.get("ground_osm"):
        doc["ground_osm"] = prev["ground_osm"]        # 地表の種類（OSM）は別に取った物。作り直しても引き継ぐ
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc


def validate_site(doc: dict) -> list[str]:
    errs: list[str] = []
    if doc.get("schema") != SITE_SCHEMA:
        errs.append(f"schema は {SITE_SCHEMA} にしてください")
    ids: set[str] = set()
    for b in doc.get("buildings", []) + doc.get("staff_additions", []):
        bid = b.get("id")
        if not bid or bid in ids:
            errs.append(f"buildings: id が無いか重複（{bid}）")
        ids.add(bid)
        c = b.get("confirmation") or {}
        st = c.get("status")
        if st not in CONFIRM_STATUSES:
            errs.append(f"{bid}: confirmation.status が不正（{st}）")
            continue
        if st != "unconfirmed" and not (c.get("by") and c.get("at")):
            errs.append(f"{bid}: 確認済みなのに by / at（誰が・いつ）がありません")
        if st == "renamed" and not c.get("name_old"):
            errs.append(f"{bid}: renamed なのに name_old がありません")
        if st in ("confirmed", "renamed", "new") and not b.get("name"):
            errs.append(f"{bid}: 確認済みなのに名前がありません")
    return errs


def confirm(doc: dict, bid: str, status: str, by: str, name: str | None = None, note: str = "") -> dict:
    """職員の確認を敷地 JSON に書く。確認の UI（11/6）はこれを呼ぶ。"""
    if status not in CONFIRM_STATUSES:
        raise ValueError(f"status が不正: {status}")
    for b in doc["buildings"]:
        if b["id"] == bid:
            c = {"status": status, "by": by, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "note": note}
            if status == "renamed":
                c["name_old"] = b.get("name")
                b["name"] = name
                b["name_source"] = "staff"
            elif status in ("confirmed", "new") and name:
                b["name"] = name
                b["name_source"] = b.get("name_source") or "staff"
            b["confirmation"] = c
            return b
    raise KeyError(f"建物がありません: {bid}")


# --- 確認用の図 -----------------------------------------------------------------------

def draw_overview(site: dict, aerial_meta: dict | None, aerial_png: Path | None, out: Path | None = None) -> Path:
    """航空写真に敷地境界・建物の輪郭・名前・確認状態を重ねた図。職員が「ある／ない／名前」を答えるための物。"""
    from PIL import Image, ImageDraw, ImageFont

    out = out or OUT_SITE / "overview.png"
    if aerial_meta and aerial_png and Path(aerial_png).exists():
        img = Image.open(aerial_png).convert("RGB")

        def to_px(x, y):
            return aerial_xy_to_px(aerial_meta, x, y)
    else:
        img = Image.new("RGB", (1600, 1600), (245, 243, 238))
        pts = [p for r in site["boundary_osm"] for p in r]
        x0, x1 = min(p[0] for p in pts) - 50, max(p[0] for p in pts) + 50
        y0, y1 = min(p[1] for p in pts) - 50, max(p[1] for p in pts) + 50
        s = min(1600 / (x1 - x0), 1600 / (y1 - y0))

        def to_px(x, y):
            return ((x - x0) * s, (y1 - y) * s)
    d = ImageDraw.Draw(img, "RGBA")
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/YuGothM.ttc", 16)
        font_s = ImageFont.truetype("C:/Windows/Fonts/YuGothM.ttc", 12)
    except OSError:
        font = font_s = ImageFont.load_default()
    for r in site["boundary_osm"]:
        d.line([to_px(*p) for p in r] + [to_px(*r[0])], fill=(230, 40, 40, 255), width=3)
    col = {"unconfirmed": (255, 200, 0, 90), "confirmed": (60, 200, 90, 90), "renamed": (60, 140, 255, 90),
           "removed": (120, 120, 120, 120), "new": (200, 60, 220, 90)}
    for b in site["buildings"]:
        st = b["confirmation"]["status"]
        for r in b["footprint_osm"]:
            d.polygon([to_px(*p) for p in r], fill=col.get(st, col["unconfirmed"]), outline=(255, 255, 255, 230))
        cx, cy = to_px(*b["centroid"])
        label = b["name"] or "（名前なし）"
        w = d.textlength(label, font=font)
        d.rectangle([cx - w / 2 - 3, cy - 10, cx + w / 2 + 3, cy + 10], fill=(255, 255, 255, 200))
        d.text((cx - w / 2, cy - 9), label, fill=(20, 20, 20), font=font)
    legend = ["黄＝未確認  緑＝確認済み  青＝改称  灰＝撤去  紫＝新設", "赤線＝OSM の敷地境界",
              "背景：国土地理院 航空写真 / 輪郭・名前：OpenStreetMap (ODbL)"]
    y = 8
    for t in legend:
        d.rectangle([8, y, 8 + d.textlength(t, font=font_s) + 8, y + 18], fill=(255, 255, 255, 220))
        d.text((12, y + 2), t, fill=(20, 20, 20), font=font_s)
        y += 20
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out


# --- 地面の 3 段：OSM の塗り分け ／ 棟 JSON の ground_offset ／ 5 m 標高 -------------------------------
# 1) 地表の種類（道・歩道・芝・駐車場）は OSM から（estimated、出典 OSM）。1 m 格子のマスクにする（ground_mask）。
# 2) 建物まわりの段差は棟 JSON の faces[].ground_offset から、面の前の帯だけ地形を上書きする（apply_ground_overrides）。
# 3) それ以外は 5 m 標高を均した面。道路の勾配を測ったわけではない（5 m 標高の画素より細かい形は無い）。

MASK_STEP_M = 1.0
GROUND_OVERRIDE_DEPTH_M = 12.0   # ground_offset を効かせる面の前の帯の奥行き（ground_offset.depth があればそれ）。読み方（FACE_BAND_M）と同じ
GROUND_OVERRIDE_BLEND_M = 5.0    # 帯の外、この距離で元の地形へ滑らかに戻す
GROUND_OVERRIDE_MAX_SHIFT_M = 4.0   # 帯の地形をこれより大きく動かす値は上書きしない（5 m 標高の均しで隠れる段差を超える＝ground_z か値の食い違い）


def ground_mask(ground_osm: dict | None, bounds: tuple[float, float, float, float], footprints=(), step: float = MASK_STEP_M) -> dict:
    """OSM の図形から地表の種類のマスク（1 m 格子、値は GROUND_KINDS の番号）を作る。bounds は (x0, y0, x1, y1) m。
    行 0 が北の端（画像と同じ向き）。画素 (r, c) の中心は (x0 + (c+0.5)·step, y_top − (r+0.5)·step)。
    塗る順（後が上）：芝 → 駐車場 → 舗装の面 → 車道の面 → 道の線（車道・駐車場・歩道・階段）→ 建物の足元。"""
    from PIL import Image, ImageDraw

    x0, y0, x1, y1 = bounds
    W, H = int(math.ceil((x1 - x0) / step)), int(math.ceil((y1 - y0) / step))
    y_top = y0 + H * step
    S = 4                                          # PIL は頂点を整数に切り捨てて縁を含めて塗るので、4 倍で描いて各画素の中心を拾う
    im = Image.new("L", (W * S, H * S), 0)
    dr = ImageDraw.Draw(im)
    kid = {k: i for i, k in enumerate(GROUND_KINDS)}

    def px(p):
        return ((p[0] - x0) / step * S, (y_top - p[1]) / step * S)

    feats = (ground_osm or {}).get("features", [])
    counts = {k: {"features": 0, "area_m2": 0.0} for k in GROUND_KINDS}
    area_order = ("grass", "parking", "paving", "steps", "asphalt")
    for k in area_order:
        for f in feats:
            if f["shape"] == "area" and f["kind"] == k:
                for r in f["rings"]:
                    if len(r) >= 3:
                        dr.polygon([px(p) for p in r], fill=kid[k])
                counts[k]["features"] += 1
    line_order = ("asphalt", "parking", "paving", "grass", "steps")
    for k in line_order:
        for f in feats:
            if f["shape"] == "line" and f["kind"] == k:
                pts = [px(p) for p in f["coords"]]
                rad = f["width_m"] / step / 2 * S
                for (ax, ay), (bx, by) in zip(pts, pts[1:]):   # 区間ごとに幅の長方形（PIL の太線は丸めがずれるので使わない）
                    L = math.hypot(bx - ax, by - ay)
                    if L == 0:
                        continue
                    ox, oy = -(by - ay) / L * rad, (bx - ax) / L * rad
                    dr.polygon([(ax + ox, ay + oy), (bx + ox, by + oy), (bx - ox, by - oy), (ax - ox, ay - oy)], fill=kid[k])
                for qx, qy in pts:                        # 曲がり角の欠けを丸で埋める
                    dr.ellipse([qx - rad, qy - rad, qx + rad, qy + rad], fill=kid[k])
                counts[k]["features"] += 1
    for poly in footprints:
        if len(poly) >= 3:
            dr.polygon([px(p) for p in poly], fill=kid["building"])
    M = np.ascontiguousarray(np.asarray(im, dtype=np.uint8)[S // 2::S, S // 2::S])
    for k, i in kid.items():
        counts[k]["area_m2"] = round(float((M == i).sum()) * step * step, 1)
    return {"mask": M, "x0": float(x0), "y_top": float(y_top), "step": float(step), "w": W, "h": H, "counts": counts}


def mask_kind_at(gm: dict, X, Y) -> np.ndarray:
    """マスクの種類番号を (x, y) で読む（最近傍。範囲外は 0 = unknown）。Blender の地形の色分けにも使う。"""
    M = gm["mask"]
    c = np.floor((np.asarray(X, float) - gm["x0"]) / gm["step"]).astype(int)
    r = np.floor((gm["y_top"] - np.asarray(Y, float)) / gm["step"]).astype(int)
    ok = (c >= 0) & (c < M.shape[1]) & (r >= 0) & (r < M.shape[0])
    out = np.zeros(np.shape(X), dtype=np.uint8)
    out[ok] = M[r[ok], c[ok]]
    return out


def save_ground_mask_png(gm: dict, path: Path) -> None:
    """インデックス色の PNG（パレットは GROUND_KIND_COLOR）。画素の値＝種類の番号。"""
    from PIL import Image

    im = Image.fromarray(gm["mask"], mode="L").convert("P")
    pal = []
    for k in GROUND_KINDS:
        pal.extend(GROUND_KIND_COLOR[k])
    im.putpalette(pal + [0] * (768 - len(pal)))
    im.putdata(list(gm["mask"].ravel()))         # convert("P") で番号が変わらないよう、値をそのまま入れ直す
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, optimize=True)


def _save_mask_color(gm: dict, path: Path) -> None:
    from PIL import Image

    lut = np.array([GROUND_KIND_COLOR["unknown" if k == "building" else k] for k in GROUND_KINDS], dtype=np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(lut[gm["mask"]], mode="RGB").save(path, optimize=True)


def load_ground_mask(path: Path | None = None) -> dict | None:
    """export_web が書いた out/site/ground_mask.npz（Blender から読む。無ければ None）。"""
    path = path or OUT_SITE / "ground_mask.npz"
    if not path.exists():
        return None
    d = np.load(path)
    return {"mask": d["mask"], "x0": float(d["x0"]), "y_top": float(d["y_top"]), "step": float(d["step"])}


def ground_override_specs(b: dict) -> list[dict]:
    """棟 JSON の faces[].ground_offset（measured / estimated で値がある物）→ 地形を上書きする帯の一覧。
    target_z は標高（ground_z + value）。unknown・値の無い物・面の位置が無い物は入れない。"""
    gz, _ = _mval(b.get("ground_z"))
    out = []
    if gz is None:
        return out
    for f in b.get("faces", []):
        go = f.get("ground_offset")
        v, tol = _mval(go)
        if v is None or not f.get("from") or not f.get("to"):
            continue
        dep, _ = _mval(go.get("depth")) if isinstance(go, dict) else (None, None)
        out.append({"building": b.get("id"), "face": f["id"], "from": list(f["from"]), "to": list(f["to"]),
                    "value": v, "tol": tol, "status": go.get("status", "estimated") if isinstance(go, dict) else "estimated",
                    "evidence": list(go.get("evidence") or []) if isinstance(go, dict) else [],
                    "depth_m": float(dep) if dep else GROUND_OVERRIDE_DEPTH_M, "ground_z": gz, "target_z": gz + v})
    return out


def apply_ground_overrides(X: np.ndarray, Y: np.ndarray, Z: np.ndarray, specs: list[dict], z_ref: float = 0.0,
                           blend_m: float = GROUND_OVERRIDE_BLEND_M,
                           max_shift_m: float = GROUND_OVERRIDE_MAX_SHIFT_M) -> tuple[np.ndarray, list[dict]]:
    """面の前の帯（面の幅 × 面から depth m。外向き法線は (u.y, −u.x)）の地形を、帯の中央値が target_z − z_ref になるよう上げ下げする。
    帯の中の 5 m 標高の起伏はそのまま残す（平面を当てはめない）。帯の外 blend_m で滑らかに（smoothstep）元の地形へ戻す。
    帯が重なる所（入隅）は重みで平均。動かす量が max_shift_m を超える帯は上書きせず skipped に理由を書く（決めつけない）。
    Z は X, Y と同じ形。z_ref：Z が棟の z=0 からの m なら ground_z、標高なら 0。
    返り値：(上書きした Z, 一覧（根拠付き）)。"""
    Z = np.array(Z, dtype=float)
    num = np.zeros_like(Z)
    den = np.zeros_like(Z)
    wmax = np.zeros_like(Z)
    recs = []
    for s in specs:
        (ax, ay), (bx, by) = s["from"], s["to"]
        L = math.hypot(bx - ax, by - ay)
        if L == 0:
            continue
        ux, uy = (bx - ax) / L, (by - ay) / L
        nx, ny = uy, -ux
        a = (X - ax) * ux + (Y - ay) * uy
        d = (X - ax) * nx + (Y - ay) * ny
        D = s["depth_m"]
        band = (a >= 0) & (a <= L) & (d >= 0) & (d <= D)
        rec = {k: s[k] for k in ("building", "face", "value", "tol", "status", "evidence")}
        rec.update({"from": s["from"], "to": s["to"], "depth_m": D, "blend_m": blend_m, "applied": False})
        if not band.any():
            rec["skipped"] = "帯の中に格子点が無い（格子が粗い）"
            recs.append(rec)
            continue
        target = s["target_z"] - z_ref
        med = float(np.median(Z[band]))
        shift = target - med
        rec.update({"terrain_band_median": round(med - target + s["value"], 2), "shift_m": round(shift, 2), "cells": int(band.sum())})
        if abs(shift) > max_shift_m:
            rec["skipped"] = (f"5 m 標高の帯の中央値 {med - target + s['value']:+.2f} m と {shift:+.2f} m 食い違う（上限 {max_shift_m:g} m）。"
                              "ground_z か ground_offset を見直すまで上書きしない")
            recs.append(rec)
            continue
        dist = np.hypot(np.maximum(np.maximum(-a, a - L), 0.0), np.maximum(np.maximum(-d, d - D), 0.0))
        t = np.clip(1.0 - dist / blend_m, 0.0, 1.0) if blend_m > 0 else (dist == 0).astype(float)
        w = t * t * (3 - 2 * t)
        num += w * shift
        den += w
        wmax = np.maximum(wmax, w)
        rec.update({"applied": True, "method": f"面の前 0〜{D:g} m の帯の 5 m 標高（均した面）の中央値 {med - target + s['value']:+.2f} m を "
                              f"ground_offset {s['value']:+.2f} m に {shift:+.2f} m 動かす（帯の中の起伏は残す）。帯の外 {blend_m:g} m で元の地形へ"})
        recs.append(rec)
    with np.errstate(invalid="ignore", divide="ignore"):
        Z = Z + np.where(den > 0, wmax * num / np.where(den > 0, den, 1.0), 0.0)
    return Z, recs


def _designed_buildings() -> list[dict]:
    out = []
    for f in sorted((DATA_SITE.parent / "buildings").glob("*.json")):
        out.append(json.loads(f.read_text(encoding="utf-8")))
    return out


def ensure_ground_osm(campus: str, bbox_xy: tuple[float, float, float, float]) -> dict:
    """敷地 JSON に ground_osm が無ければ OSM から 1 回取って書き足す（取得は fetch_osm_ground のキャッシュ）。"""
    path = DATA_SITE / f"{campus}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("ground_osm"):
        return doc["ground_osm"]
    go = osm_ground(fetch_osm_ground(bbox_xy))
    doc["ground_osm"] = go
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return go


# --- Web ビューア用の書き出し --------------------------------------------------------

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def _count_status(node, acc: dict) -> None:
    if isinstance(node, dict):
        s = node.get("status")
        if s in ("measured", "estimated", "unknown"):
            acc[s] = acc.get(s, 0) + 1
        for v in node.values():
            _count_status(v, acc)
    elif isinstance(node, list):
        for v in node:
            _count_status(v, acc)


def _has_evidence(node) -> bool:
    """faces のどこかに、写真など PLATEAU 以外の根拠（evidence）を持つ measured/estimated があるか。
    "plateau:" で始まる根拠だけの値は、写真の根拠とは数えない。"""
    if isinstance(node, dict):
        if node.get("status") in ("measured", "estimated") and any(
                not str(e).startswith("plateau:") for e in (node.get("evidence") or [])):
            return True
        return any(_has_evidence(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_evidence(v) for v in node)
    return False


WEB_ENV_HDRI = "hdri_overcast"          # 空と環境光（CC0。data/assets/catalog.json）
WEB_GROUND_TEX = ("asphalt", "paving_block")   # 地面の質感（CC0）。web/data/tex/ に小さくして写す
WEB_TEX_PX = 512


def copy_web_assets(data_dir: Path, cat: dict | None = None, store: Path | None = None, px: int = WEB_TEX_PX) -> dict:
    """歩く画面用に CC0 のアセットを写す。HDRI（1k の .hdr）→ data/env/、地面の質感（色と法線を px 四方の jpg）→ data/tex/。
    取得していない物は飛ばし、skipped に理由を書く（画面は 1 色に落ちて動く）。返り値は site.json の "env" と "ground_tex"。"""
    import shutil
    from . import assets

    cat = cat if cat is not None else assets.load_catalog()
    res: dict = {"env": None, "ground_tex": {}, "skipped": []}
    try:
        it = assets.item(cat, WEB_ENV_HDRI)
        src = assets.resolve(WEB_ENV_HDRI, cat, store)
        (data_dir / "env").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, data_dir / "env" / src.name)
        res["env"] = {"url": f"data/env/{src.name}", "asset": WEB_ENV_HDRI, "license": it["license"], "page": it.get("page")}
    except (KeyError, ValueError, FileNotFoundError) as e:
        res["skipped"].append(f"{WEB_ENV_HDRI}: {e}")
    for aid in WEB_GROUND_TEX:
        try:
            it = assets.item(cat, aid)
            maps = assets.texture_maps(aid, cat, store)
            (data_dir / "tex").mkdir(parents=True, exist_ok=True)
            rec = {"asset": aid, "tile_m": it["tile_m"], "license": it["license"], "page": it.get("page")}
            for k in ("color", "normal"):
                if k not in maps or not maps[k].exists():
                    raise FileNotFoundError(f"{maps.get(k)} がありません")
                _small_jpg(maps[k], data_dir / "tex" / f"{aid}_{k}.jpg", px)
                rec[k] = f"data/tex/{aid}_{k}.jpg"
            res["ground_tex"][aid] = rec
        except (KeyError, ValueError, FileNotFoundError) as e:
            res["skipped"].append(f"{aid}: {e}")
    return res


def _small_jpg(src: Path, dst: Path, px: int) -> None:
    from PIL import Image

    with Image.open(src) as im:
        im = im.convert("RGB")
        if max(im.size) > px:
            im = im.resize((px, px), Image.LANCZOS)
        im.save(dst, "JPEG", quality=85)


def _daylight_for_web() -> dict | None:
    """data/daylight.json（Blender と同じ太陽と空の設定）を site.json の daylight に。無ければ None（画面は既定値で動く）。"""
    from recorder import daylight
    try:
        return daylight.for_web()
    except (OSError, ValueError, KeyError) as e:
        print(f"[warn] daylight: {e}", file=sys.stderr)
        return None


def export_web(campus: str | None = None, web_dir: Path | None = None, step_m: int = 2, margin_m: float = 120.0,
               with_coverage: bool = False, out_root: Path | None = None) -> dict:
    """Web 3D 用に web/data/site.json を書く。土台（PLATEAU の箱）・地面（5m 標高を均し、ground_offset の帯を上書きし、step_m 間隔に間引き）・
    地表の種類のマスク（OSM。web/data/ground_mask.png と ground_mask.json、Blender 用に out/site/ground_mask.npz）・
    設計済みの棟の一覧。glb がある設計済みの棟は箱を出さず、glb を web/data/models/ に写す。
    CC0 の空（HDRI）と地面の質感も web/data/env/・web/data/tex/ に写す（copy_web_assets）。
    glb の軸：glTF は Y が上。Blender の (x,y,z) → glTF の (x, z, -y)。つまり glb は x=東, y=高さ, z=-北。
    glb の高さ(y)は ground_z 基準の相対値なので、置く時に ground_z を足す。
    with_coverage：out/<棟>/coverage.json（recorder coverage）があれば、unknown でない面の「未撮影」と「写っているが使えない」
    範囲（面の m の矩形）だけを designed[].coverage.faces[] に足す。写真そのもの（顔・ナンバー・貼り紙が写る）は Web に出さない。"""
    import shutil

    from recorder import schema

    web_dir = web_dir or WEB_DIR
    data_dir = web_dir / "data"
    (data_dir / "models").mkdir(parents=True, exist_ok=True)
    campus = campus or default_campus()
    doc = json.loads((DATA_SITE / f"{campus}.json").read_text(encoding="utf-8"))
    pl_path = DATA_SITE / "plateau.json"
    pl = {p["gml_id"]: p for p in json.loads(pl_path.read_text(encoding="utf-8"))["buildings"]} if pl_path.exists() else {}

    designed = {}
    for f in sorted((DATA_SITE.parent / "buildings").glob("*.json")):
        b = json.loads(f.read_text(encoding="utf-8"))
        if not b.get("faces"):
            continue
        acc: dict = {}
        _count_status(b, acc)
        ev = schema.evidence_summary(b)        # 根拠の層：形がある割合・根拠がある割合と、面・部品ごとの状態
        designed[b.get("site_id") or b["id"]] = {"id": b["id"], "name": b["name"], "counts": acc,
                                                  "evidenced": _has_evidence(b["faces"]),
                                                  "ground_z": b["ground_z"]["value"],
                                                  "shape_ratio": ev["shape_ratio"], "evidence_ratio": ev["evidence_ratio"],
                                                  "faces": {fid: d["state"] for fid, d in ev["faces"].items()},
                                                  "parts": ev["parts"], "body": ev["body"]}
        if with_coverage:
            cv = coverage_layer(b, ev, (out_root or OUT_SITE.parent) / b["id"] / "coverage.json")
            if cv:
                designed[b.get("site_id") or b["id"]]["coverage"] = cv
    models = {}
    boxes = []
    used_parts: set[str] = set()
    for sb in doc["buildings"]:
        if sb["confirmation"]["status"] == "removed":
            continue
        d = designed.get(sb["id"])
        glb = OUT_SITE.parent / sb["id"] / f"{sb['id']}.glb"
        if d and d["evidenced"] and glb.exists() and glb.stat().st_size > 10_000:
            shutil.copyfile(glb, data_dir / "models" / glb.name)
            models[d["id"]] = {"url": f"data/models/{glb.name}", "ground_z": d["ground_z"]}
            used_parts.update(sb["plateau_parts"])
            continue
        for gid in sb["plateau_parts"]:
            p = pl.get(gid)
            if not p:
                continue
            used_parts.add(gid)
            boxes.append(_box(p, sb["id"], sb["name"], d))
    for gid in doc.get("plateau_only", []):
        p = pl.get(gid)
        if p and gid not in used_parts:
            boxes.append(_box(p, gid, None, None))

    xs = [x for b in boxes for x, _ in b["footprint"]]
    ys = [y for b in boxes for _, y in b["footprint"]]
    dem = load_dem()
    x0, x1 = min(xs) - margin_m, max(xs) + margin_m
    y0, y1 = min(ys) - margin_m, max(ys) + margin_m
    X0, Y0 = math.floor(x0 / step_m) * step_m, math.floor(y0 / step_m) * step_m
    nx, ny = len(np.arange(X0, x1, step_m)), len(np.arange(Y0, y1, step_m))
    X1, Y1 = X0 + (nx - 1) * step_m, Y0 + (ny - 1) * step_m
    # 地形：模型と同じ作り方（1 m 格子で読み、欠けを埋め、5 m 標高の画素の段を均す）→ ground_offset の帯を上書き → step_m おきに間引く
    gx1, gy1 = np.arange(X0, X1 + 0.5, 1.0), np.arange(Y0, Y1 + 0.5, 1.0)
    GX, GY = np.meshgrid(gx1, gy1)
    Z, nan_ratio = fill_gaps(dem_sample(dem, GX, GY))   # 欠けを隣の有効値の平均で外から埋める（補間ではなく延長）
    Z = box_mean(Z, TERRAIN_SMOOTH_PX)
    specs = [s for b in _designed_buildings() for s in ground_override_specs(b)]
    Z, overrides = apply_ground_overrides(GX, GY, Z, specs, z_ref=0.0)
    k = int(step_m)
    Z = Z[::k, ::k]
    gx, gy = gx1[::k], gy1[::k]
    # 地表の種類（OSM）：1 m 格子のマスク。建物の足元（OSM・PLATEAU・設計済みの棟）は building
    gosm = ensure_ground_osm(campus, (X0, Y0, X1, Y1))
    feet = [r for sb in doc["buildings"] if sb["confirmation"]["status"] != "removed" for r in sb.get("footprint_osm", [])]
    feet += [p["footprint"] for p in pl.values()]
    feet += [b["footprint"]["polygon"] for b in _designed_buildings() if (b.get("footprint") or {}).get("polygon")]
    gm = ground_mask(gosm, (X0, Y0, X1, Y1), feet)
    save_ground_mask_png(gm, data_dir / "ground_mask.png")
    OUT_SITE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT_SITE / "ground_mask.npz", mask=gm["mask"], x0=gm["x0"], y_top=gm["y_top"], step=gm["step"])
    _save_mask_color(gm, OUT_SITE / "ground_mask_color.png")   # Blender の背景の地形に貼る色の画像（建物の足元は根拠なしと同じ灰色）
    mask_info = {"url": "data/ground_mask.png", "x0": gm["x0"], "y_top": gm["y_top"], "step": gm["step"], "w": gm["w"], "h": gm["h"],
                 "kinds": [{"id": i, "kind": kd, "label": GROUND_KIND_LABEL[kd], "color": "#%02x%02x%02x" % GROUND_KIND_COLOR[kd]}
                           for i, kd in enumerate(GROUND_KINDS)],
                 "counts": gm["counts"], "status": "estimated", "source": gosm.get("source"), "license": gosm.get("license"),
                 "fetched_at": gosm.get("fetched_at"), "method": gosm.get("method"), "standard_width_m": gosm.get("standard_width_m"),
                 "note": "画素の値＝種類の番号（行 0 が北）。道は OSM の中心線を width（無ければ標準幅）で太らせた。unknown は OSM に何も無い所"}
    (data_dir / "ground_mask.json").write_text(json.dumps(mask_info, ensure_ascii=False, indent=1), encoding="utf-8")
    wa = copy_web_assets(data_dir)
    out = {
        "schema": "campus-recorder/web/1",
        "campus": campus,
        "scope": doc.get("scope"),
        "axes": "x=東, y=北, z=標高 m（原点 北緯35.1355° 東経136.9755°）。three.js 側で (x, z, -y) に並べ替える",
        "ground": {"x0": float(gx[0]), "y0": float(gy[0]), "step": step_m, "nx": len(gx), "ny": len(gy),
                   "z": [round(float(v), 2) for v in Z.ravel()], "nan_ratio_filled": round(nan_ratio, 3),
                   "source": f"国土地理院 標高（DEM5A）を 1 m 格子で {TERRAIN_SMOOTH_PX} 画素の窓で均した面。道路の勾配を測った物ではない",
                   "overrides": overrides, "overrides_note": "棟 JSON の faces[].ground_offset で面の前の帯を上書きした所（根拠は evidence）",
                   "mask": mask_info},
        "boxes": boxes,
        "models": models,
        "designed": designed,
        "sources": doc.get("sources"),
        "env": wa["env"],
        "daylight": _daylight_for_web(),
        "ground_tex": wa["ground_tex"],
        "ground_note": "地面の 3 段：地表の種類は OSM（estimated）、建物まわりの段差は棟 JSON の ground_offset、残りは 5 m 標高を均した面",
    }
    from .ledger import write_text_atomic   # 歩く画面が読んでいる間に半分だけの site.json を見せない
    write_text_atomic(data_dir / "site.json", json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    return {"path": data_dir / "site.json", "boxes": len(boxes), "models": list(models), "grid": (len(gx), len(gy)),
            "nan_filled": nan_ratio, "env": (wa["env"] or {}).get("url"), "tex": sorted(wa["ground_tex"]),
            "skipped": wa["skipped"], "mask": {k: v for k, v in gm["counts"].items()}, "overrides": overrides}


def coverage_layer(b: dict, ev: dict, cov_json: Path) -> dict | None:
    """被覆図の結果から Web の灰色の重ね（未撮影・使えない所の矩形だけ）を作る。面の位置が unknown の面は出さない（本体が灰色）。
    z は棟の z=0（ground_z）からの m。along は面の始点 from から m。"""
    if not cov_json.exists():
        return None
    cj = json.loads(cov_json.read_text(encoding="utf-8"))
    faces = {f["id"]: f for f in b.get("faces", [])}
    out = []
    for r in cj.get("faces", []):
        f = faces.get(r["face"])
        st = (ev["faces"].get(r["face"]) or {}).get("state")
        if r.get("unknown") or not f or not (f.get("from") and f.get("to")) or st in (None, "unknown"):
            continue
        out.append({"id": r["face"], "from": f["from"], "to": f["to"], "coverage_pct": r.get("coverage_pct"),
                    "uncovered": r.get("web_uncovered", r.get("uncovered_rects", [])),
                    "unusable": r.get("web_unusable", r.get("unusable_rects", []))})
    return {"made_at": cj.get("made_at"), "note": "灰色＝未撮影、薄い灰色＝写っているが斜め・遮蔽で使えない。写真は出さない", "faces": out}


def _box(p: dict, sid: str, name: str | None, d: dict | None) -> dict:
    c = (d or {}).get("counts", {})
    grounded = bool(d) and d.get("evidenced", False)
    return {"id": (d or {}).get("id", sid), "name": (d or {}).get("name") or name,
            "footprint": p["footprint"], "base": p["base_z"], "height": p["height_m"],
            "grounded": grounded, "evidenced": grounded,
            "measured": c.get("measured", 0), "estimated": c.get("estimated", 0), "unknown": c.get("unknown", 0),
            "shape_ratio": (d or {}).get("shape_ratio"), "evidence_ratio": (d or {}).get("evidence_ratio")}


# --- 立ち上げ：campus init / campus check ------------------------------------------------------

def campus_init(slug: str, university: str, campus_name: str, bbox: tuple[float, float, float, float] | None = None,
                data_site: Path | None = None) -> tuple[Path, bool]:
    """敷地 JSON の骨組みを作る。返り値 (パス, 新しく作ったか)。既にあれば上書きしない。
    bbox は (lat0, lon0, lat1, lon1)。あれば原点をその中心にする（無ければ geo の既定の原点）。"""
    path = campus_path(slug, data_site)
    if path.exists():
        return path, False
    lat, lon = geo.ORIGIN_LAT, geo.ORIGIN_LON
    if bbox:
        lat, lon = round((bbox[0] + bbox[2]) / 2, 6), round((bbox[1] + bbox[3]) / 2, 6)
    doc = {
        "schema": SITE_SCHEMA,
        "campus": slug,
        "university": university,
        "campus_name": campus_name,
        "crs": {"epsg": geo.EPSG_PLANE, "origin_lat": lat, "origin_lon": lon,
                "note": "原点からの m。x=東, y=北。z は標高 m"},
        "bbox_lonlat": list(bbox) if bbox else None,
        "boundary_osm": [],
        "sources": {
            "osm": {"relation": None, "license": "ODbL", "fetched_at": None},
            "plateau": None,
            "plateau_zip": None,
            "gsi": "国土地理院 航空写真・DEM5A",
            "campus_map": {"url": None, "checked_at": None, "map_date": None,
                           "permission": {"status": None, "ref": None, "date": None},
                           "use": "名称・有無・大まかな位置の確認のみ。形・寸法はなぞらない（CLAUDE.md 規約 4）"},
        },
        "aliases": {},
        "scope": {"targets": [], "decided_by": None, "decided_at": None, "note": ""},
        "name_links": [],
        "questions": [],
        "confirmation_statuses": list(CONFIRM_STATUSES),
        "buildings": [],
        "plateau_only": [],
        "staff_additions": [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return path, True


def campus_check(campus: str, data_site: Path | None = None, out: Path | None = None) -> dict:
    """立ち上げの未解決を集める。problems が空なら 0 件。out があれば JSON でも書く。"""
    doc = load_campus(campus, data_site)
    if not doc:
        raise FileNotFoundError(f"敷地 JSON がありません: {campus_path(campus, data_site)}")
    links = doc.get("name_links") or []
    ids = {b.get("id") for b in doc.get("buildings", [])}
    res = {"campus": campus, "university": doc.get("university"), "campus_name": doc.get("campus_name")}
    for st in ("map_only", "footprint_only", "ambiguous"):
        res[st] = [l for l in links if l.get("status") == st]
    res["targets_missing"] = [t for t in (doc.get("scope") or {}).get("targets", []) if t not in ids]
    res["questions_open"] = [q for q in doc.get("questions") or [] if q.get("status") == "open"]
    perm = (((doc.get("sources") or {}).get("campus_map")) or {}).get("permission") or {}
    res["campus_map_permission"] = perm.get("status")
    res["permission_ok"] = perm.get("status") == "granted"
    n = (len(res["map_only"]) + len(res["footprint_only"]) + len(res["ambiguous"]) + len(res["targets_missing"])
         + len(res["questions_open"]) + (0 if res["permission_ok"] else 1))
    res["problem_count"] = n
    res["ok"] = n == 0
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def campus_check_lines(res: dict, limit: int = 8) -> list[str]:
    """campus_check の結果を日本語の行に。"""
    def nm(l):
        return l.get("map_name") or l.get("building_id") or l.get("footprint") or "?"
    L = [f"{res.get('university') or ''} {res.get('campus_name') or res['campus']}：問題 {res['problem_count']} 件"]
    for key, label in (("map_only", "図にあるが足元が無い"), ("footprint_only", "足元はあるが図に名前が無い"), ("ambiguous", "対応が決まらない")):
        items = res[key]
        if items:
            L.append(f"  {label}（{len(items)}）：" + "、".join(nm(l) for l in items[:limit]) + ("…" if len(items) > limit else ""))
    if res["targets_missing"]:
        L.append(f"  範囲の棟が敷地 JSON の buildings に無い（{len(res['targets_missing'])}）：" + "、".join(res["targets_missing"]))
    if res["questions_open"]:
        L.append(f"  人に聞くことが残っている（{len(res['questions_open'])}）：")
        L += [f"    - {q.get('q')}" for q in res["questions_open"]]
    if not res["permission_ok"]:
        L.append(f"  公式図の許可が granted ではない（{res['campus_map_permission']}）")
    return L
