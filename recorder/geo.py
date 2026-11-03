"""座標の変換。

このプロジェクトの座標（CLAUDE.md）：
    JGD2011 平面直角座標系 VII 系（EPSG:6675）を、原点 北緯 35.1355° 東経 136.9755° からの m にずらしたもの。
    x = 東、y = 北。高さは標高 m。

ここでは緯度経度（WGS84/JGD2011、EPSG:4326 相当）と、この「原点からの m」を相互に変換する。
国土地理院のタイル番号の計算もここに置く。
"""

from __future__ import annotations

import math
from functools import lru_cache

ORIGIN_LAT = 35.1355
ORIGIN_LON = 136.9755
EPSG_PLANE = 6675


@lru_cache(maxsize=1)
def _transformers():
    from pyproj import Transformer

    fwd = Transformer.from_crs("EPSG:6668", f"EPSG:{EPSG_PLANE}", always_xy=True)
    inv = Transformer.from_crs(f"EPSG:{EPSG_PLANE}", "EPSG:6668", always_xy=True)
    ox, oy = fwd.transform(ORIGIN_LON, ORIGIN_LAT)
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
