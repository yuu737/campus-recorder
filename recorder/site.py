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

SLUGS = {"本部棟": "honbu", "名城大学付属図書館": "library", "11号館": "b11", "12号館": "b12", "13号館": "b13",
         "14号館": "b14", "6号館": "b06", "7号館": "b07", "8号館": "b08", "9号館": "b09", "10号館": "b10",
         "タワー75": "tower75", "共通講義棟南": "kyotsu-minami", "共通講義棟東": "kyotsu-higashi",
         "共通講義棟北": "kyotsu-kita", "新体育館": "gym", "研究実験棟Ⅰ": "lab1", "研究実験棟Ⅱ": "lab2",
         "研究実験棟Ⅲ": "lab3", "研究実験棟Ⅳ": "lab4", "名城大学 校友会館": "koyu-kaikan"}


def _slug(name: str | None, osm: str) -> str:
    if name in SLUGS:
        return SLUGS[name]
    return "osm-" + osm.replace("/", "-")


def build_site(campus: str, osm: dict, plateau: dict | None, out: Path | None = None) -> dict:
    """OSM の建物を軸に、PLATEAU の部品を足元の重なりで結びつけ、確認状態を付けて敷地 JSON を作る。
    既存の敷地 JSON があれば、職員の確認（confirmation）と追加（staff_additions）は引き継ぐ。"""
    from shapely.geometry import MultiPolygon, Polygon

    out = out or DATA_SITE / f"{campus}.json"
    prev = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    prev_b = {b["id"]: b for b in prev.get("buildings", [])}

    pl = []
    for p in (plateau or {}).get("buildings", []):
        poly = Polygon(p["footprint"])
        pl.append((p, poly if poly.is_valid else poly.buffer(0)))

    buildings = []
    used = set()
    for b in osm["buildings"]:
        bid = _slug(b["name"], b["osm"])
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


def export_web(campus: str = "tempaku", web_dir: Path | None = None, step_m: int = 4, margin_m: float = 120.0) -> dict:
    """Web 3D 用に web/data/site.json を書く。土台（PLATEAU の箱）・地面（5m 標高を step_m 間隔に間引き）・
    設計済みの棟の一覧。glb がある設計済みの棟は箱を出さず、glb を web/data/models/ に写す。
    CC0 の空（HDRI）と地面の質感も web/data/env/・web/data/tex/ に写す（copy_web_assets）。
    glb の軸：glTF は Y が上。Blender の (x,y,z) → glTF の (x, z, -y)。つまり glb は x=東, y=高さ, z=-北。
    glb の高さ(y)は ground_z 基準の相対値なので、置く時に ground_z を足す。"""
    import shutil

    from recorder import schema

    web_dir = web_dir or WEB_DIR
    data_dir = web_dir / "data"
    (data_dir / "models").mkdir(parents=True, exist_ok=True)
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
    gx = np.arange(math.floor(x0 / step_m) * step_m, x1, step_m)
    gy = np.arange(math.floor(y0 / step_m) * step_m, y1, step_m)
    Z = np.full((len(gy), len(gx)), np.nan)
    for j, y in enumerate(gy):
        for i, x in enumerate(gx):
            z = dem_at(dem, float(x), float(y))
            if z is not None:
                Z[j, i] = z
    Z, nan_ratio = fill_gaps(Z)   # 欠けを隣の有効値の平均で外から埋める（補間ではなく延長）
    wa = copy_web_assets(data_dir)
    out = {
        "schema": "campus-recorder/web/1",
        "campus": campus,
        "axes": "x=東, y=北, z=標高 m（原点 北緯35.1355° 東経136.9755°）。three.js 側で (x, z, -y) に並べ替える",
        "ground": {"x0": float(gx[0]), "y0": float(gy[0]), "step": step_m, "nx": len(gx), "ny": len(gy),
                   "z": [round(float(v), 1) for v in Z.ravel()], "nan_ratio_filled": round(nan_ratio, 3),
                   "source": "国土地理院 標高（DEM5A）"},
        "boxes": boxes,
        "models": models,
        "designed": designed,
        "sources": doc.get("sources"),
        "env": wa["env"],
        "ground_tex": wa["ground_tex"],
        "ground_note": "地面の土地被覆（芝・舗装の区分）は根拠のあるデータが無いので塗り分けない。全体を舗装の質感にする",
    }
    (data_dir / "site.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {"path": data_dir / "site.json", "boxes": len(boxes), "models": list(models), "grid": (len(gx), len(gy)),
            "nan_filled": nan_ratio, "env": (wa["env"] or {}).get("url"), "tex": sorted(wa["ground_tex"]),
            "skipped": wa["skipped"]}


def _box(p: dict, sid: str, name: str | None, d: dict | None) -> dict:
    c = (d or {}).get("counts", {})
    grounded = bool(d) and d.get("evidenced", False)
    return {"id": (d or {}).get("id", sid), "name": (d or {}).get("name") or name,
            "footprint": p["footprint"], "base": p["base_z"], "height": p["height_m"],
            "grounded": grounded, "evidenced": grounded,
            "measured": c.get("measured", 0), "estimated": c.get("estimated", 0), "unknown": c.get("unknown", 0),
            "shape_ratio": (d or {}).get("shape_ratio"), "evidence_ratio": (d or {}).get("evidence_ratio")}
