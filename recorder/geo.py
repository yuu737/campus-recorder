"""座標の変換。

このプロジェクトの座標：
    JGD2011 平面直角座標系（系の番号はキャンパスごと）を、敷地の原点からの m にずらしたもの。
    x = 東、y = 北。高さは標高 m。

原点と系は **敷地 JSON（data/site/<campus>.json）の `crs`** が正。
    {"epsg": 6675, "origin_lat": 35.1355, "origin_lon": 136.9755, ...}
`configure_from_site(doc)` で明示的に合わせるか、何もしなければ最初の変換の時に既定のキャンパスの敷地 JSON を読む。
敷地 JSON が読めない時だけ下の DEFAULT_*（名城大学 天白キャンパスの値）を使い、警告を出す。

ここでは緯度経度（WGS84/JGD2011、EPSG:4326 相当）と、この「原点からの m」を相互に変換する。
国土地理院のタイル番号の計算もここに置く。
"""

from __future__ import annotations

import math
import sys
import warnings
from functools import lru_cache

# 敷地 JSON に crs が無い時の既定（名城大学 天白キャンパス、VII 系）。正は敷地 JSON。
DEFAULT_ORIGIN_LAT = 35.1355
DEFAULT_ORIGIN_LON = 136.9755
DEFAULT_EPSG_PLANE = 6675

# JGD2011 平面直角座標系 I〜XIX 系：(系, EPSG, 原点の緯度, 原点の経度)。国土交通省告示の原点。
PLANE_ZONES: tuple[tuple[int, int, float, float], ...] = (
    (1, 6669, 33.0, 129.5), (2, 6670, 33.0, 131.0), (3, 6671, 36.0, 132 + 10 / 60), (4, 6672, 33.0, 133.5),
    (5, 6673, 36.0, 134 + 20 / 60), (6, 6674, 36.0, 136.0), (7, 6675, 36.0, 137 + 10 / 60), (8, 6676, 36.0, 138.5),
    (9, 6677, 36.0, 139 + 50 / 60), (10, 6678, 40.0, 140 + 50 / 60), (11, 6679, 44.0, 140.25), (12, 6680, 44.0, 142.25),
    (13, 6681, 44.0, 144.25), (14, 6682, 26.0, 142.0), (15, 6683, 26.0, 127.5), (16, 6684, 26.0, 124.0),
    (17, 6685, 26.0, 131.0), (18, 6686, 20.0, 136.0), (19, 6687, 26.0, 154.0),
)

_state: dict = {"epsg": None, "origin_lat": None, "origin_lon": None, "source": None}


def plane_zone_for(lat: float, lon: float) -> tuple[int, int]:
    """緯度経度に近い平面直角座標系 (系, EPSG) を選ぶ（目安）。

    正式な系は都道府県で決まる（例：兵庫は V 系）。ここでは原点への近さ（経度の差を重く）で選ぶので、
    県境の近くでは正式と違う系になることがある。違えば敷地 JSON の crs.epsg を書き直せば、道具はそれに従う。
    どの系でも原点はキャンパスの中なので、数字の大きさ（精度）は変わらない。"""
    best = min(PLANE_ZONES, key=lambda z: math.hypot((lat - z[2]) * 0.5, lon - z[3]))
    return best[0], best[1]


def crs_for(lat: float, lon: float) -> dict:
    """原点（キャンパスの中心）から、敷地 JSON の crs の欄を作る。"""
    zone, epsg = plane_zone_for(lat, lon)
    return {"epsg": epsg, "zone": zone, "origin_lat": round(lat, 6), "origin_lon": round(lon, 6),
            "note": "原点からの m。x=東, y=北。z は標高 m。系は原点に近い物を道具が選んだ目安（違えば epsg を直す）"}


def _crs_of(doc: dict | None) -> dict | None:
    """敷地 JSON の crs（無ければ axes）。原点と epsg が揃っていなければ None。"""
    if not isinstance(doc, dict):
        return None
    for k in ("crs", "axes"):
        c = doc.get(k)
        if isinstance(c, dict) and c.get("epsg") and c.get("origin_lat") is not None and c.get("origin_lon") is not None:
            return c
    return None


def configure(epsg: int, origin_lat: float, origin_lon: float, source: str = "") -> None:
    """原点と系を決める（以後の変換はこれ）。"""
    _state.update(epsg=int(epsg), origin_lat=float(origin_lat), origin_lon=float(origin_lon), source=source)
    _transformers.cache_clear()


def configure_from_site(doc: dict | None, source: str = "") -> bool:
    """敷地 JSON の crs で決める。crs が無い・欠けていれば False（何も変えない）。"""
    c = _crs_of(doc)
    if not c:
        return False
    configure(c["epsg"], c["origin_lat"], c["origin_lon"], source or f"敷地 JSON（{(doc or {}).get('campus')}）")
    return True


def reset() -> None:
    """未設定に戻す（次の変換で既定のキャンパスの敷地 JSON を読み直す）。テスト用。"""
    _state.update(epsg=None, origin_lat=None, origin_lon=None, source=None)
    _transformers.cache_clear()


def _ensure() -> None:
    if _state["epsg"] is not None:
        return
    try:
        from . import site as _site

        doc = _site.load_campus(_site.default_campus())
        if configure_from_site(doc):
            return
        why = f"敷地 JSON（{doc.get('campus')}）に crs がありません"
    except Exception as e:  # noqa: BLE001  敷地 JSON が無い・複数で既定が無い など
        why = f"敷地 JSON が読めません（{e}）"
    warnings.warn(f"座標の原点と系：{why}。既定（北緯{DEFAULT_ORIGIN_LAT}° 東経{DEFAULT_ORIGIN_LON}°、EPSG:{DEFAULT_EPSG_PLANE}）を使います",
                  stacklevel=3)
    print(f"[warn] 座標の原点と系：{why}。既定の原点（名城大学 天白）を使います", file=sys.stderr)
    configure(DEFAULT_EPSG_PLANE, DEFAULT_ORIGIN_LAT, DEFAULT_ORIGIN_LON, "既定（敷地 JSON に無い）")


def current() -> dict:
    """今の原点と系 {"epsg", "origin_lat", "origin_lon", "source"}。"""
    _ensure()
    return dict(_state)


def __getattr__(name: str):
    # 旧来の geo.ORIGIN_LAT / ORIGIN_LON / EPSG_PLANE は「今の値」を返す（敷地 JSON に合わせた後の値）
    m = {"ORIGIN_LAT": "origin_lat", "ORIGIN_LON": "origin_lon", "EPSG_PLANE": "epsg"}
    if name in m:
        _ensure()
        return _state[m[name]]
    raise AttributeError(name)


@lru_cache(maxsize=1)
def _transformers():
    from pyproj import Transformer

    _ensure()
    epsg = _state["epsg"]
    fwd = Transformer.from_crs("EPSG:6668", f"EPSG:{epsg}", always_xy=True)
    inv = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:6668", always_xy=True)
    ox, oy = fwd.transform(_state["origin_lon"], _state["origin_lat"])
    return fwd, inv, ox, oy


def lonlat_to_xy(lon: float, lat: float) -> tuple[float, float]:
    """緯度経度 → 原点からの m（x=東, y=北）。"""
    fwd, _, ox, oy = _transformers()
    e, n = fwd.transform(lon, lat)
    return e - ox, n - oy


def xy_to_lonlat(x: float, y: float) -> tuple[float, float]:
    _, inv, ox, oy = _transformers()
    return inv.transform(x + ox, y + oy)


# --- 国土地理院タイル（XYZ、Web メルカトル）-----------------------------------

def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[float, float]:
    """タイル座標（小数）。整数部がタイル番号、小数部がタイル内の位置。"""
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def tile_to_lonlat(x: float, y: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat
