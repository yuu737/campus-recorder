"""太陽と空の設定（data/daylight.json）。Blender の描画と歩く画面が同じ物を読む。

前は Blender（blender_build.setup_scene）と歩く画面（web/look.js）が別々の数字を持っていて、11/6 に歩く画面の見た目を
変えた時に太陽の向きが約 63° ずれた（Blender 方位 215°・高さ 42°、歩く画面 152°・33°）。今は日付・時刻・緯度経度を
このファイル 1 つに置き、太陽の位置は両方とも同じ式（sun_position、look.js の sunPosition と同じ）で計算する。

- 方位 az は北から時計回り（°）、高さ alt は地平線から（°）。棟の座標は x＝東、y＝北。
- Blender の太陽ライトは局所 -Z に光が進む。Euler (rx, 0, rz) の光の進む向きの xy は (-sin rz, cos rz)。
  なので rx = 90° − alt、rz = 180° − az。
- Blender の空（Sky Texture、Nishita／Multiple scattering）の sun_rotation は、Cycles の式で太陽の向きが
  (cos(rot + 90°), sin(rot + 90°)) になる。北から時計回りの方位に直すと az = −rot、つまり rot = 360° − az。
- 歩く画面（three.js：x＝東、y＝上、−z＝北）の太陽の向き：(sin az·cos alt, sin alt, −cos az·cos alt)。

bpy を使わないので Blender の中でも外でも読める（pyproj などにも頼らない）。
"""
from __future__ import annotations

import json
import math
from datetime import date as _date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PATH = REPO / "data" / "daylight.json"


def load(path: str | Path | None = None) -> dict:
    """data/daylight.json を読む。必須の鍵（date・time・lat・lon）が無ければ ValueError。"""
    p = Path(path) if path else PATH
    d = json.loads(p.read_text(encoding="utf-8"))
    missing = [k for k in ("date", "time", "lat", "lon") if k not in d]
    if missing:
        raise ValueError(f"{p} に {missing} がありません")
    return d


def hour_of(time_str: str) -> float:
    """"13:30" → 13.5（日本時間の時）。"""
    h, _, m = str(time_str).partition(":")
    return int(h) + (int(m) / 60 if m else 0.0)


def day_of_year(date_str: str) -> int:
    """"2026-11-06" → 310（1 月 1 日＝1）。"""
    return _date.fromisoformat(date_str).timetuple().tm_yday


def sun_position(doy: int, t: float, lat: float, lon: float) -> tuple[float, float]:
    """太陽の高さ・方位（°）。web/look.js の sunPosition と同じ式（簡単な式。誤差は 1° 程度）。
    doy は 1 月 1 日＝1、t は日本時間の時（小数可）。方位は北から時計回り。"""
    B = 2 * math.pi * (doy - 81) / 364
    eot = 9.87 * math.sin(2 * B) - 7.53 * math.cos(B) - 1.5 * math.sin(B)           # 均時差（分）
    dec = math.radians(23.45 * math.sin(2 * math.pi * (284 + doy) / 365))            # 赤緯
    solar = t + (lon - 135) / 15 + eot / 60                                           # 日本標準時の子午線は東経 135°
    H = math.radians(15 * (solar - 12))
    phi = math.radians(lat)
    h = math.asin(math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.cos(H))
    az = math.atan2(math.sin(H), math.cos(H) * math.sin(phi) - math.tan(dec) * math.cos(phi)) + math.pi
    return math.degrees(h), math.degrees(az) % 360.0


def sun_of(d: dict) -> tuple[float, float]:
    """設定から（高さ, 方位）。"""
    return sun_position(day_of_year(d["date"]), hour_of(d["time"]), float(d["lat"]), float(d["lon"]))


def blender_sun_euler(alt: float, az: float) -> tuple[float, float, float]:
    """Blender の太陽ライトの rotation_euler（ラジアン）。"""
    return (math.radians(90.0 - alt), 0.0, math.radians(180.0 - az))


def blender_sky_rotation(az: float) -> float:
    """Blender の Sky Texture の sun_rotation（ラジアン、0〜2π）。"""
    return math.radians((360.0 - az) % 360.0)


def light_travel_xy(euler: tuple[float, float, float]) -> tuple[float, float, float]:
    """Blender の太陽ライトの光が進む向き（世界座標、単位ベクトル）。検査用。"""
    rx, _, rz = euler
    return (-math.sin(rz) * math.sin(rx), math.cos(rz) * math.sin(rx), -math.cos(rx))


def web_sun_vector(alt: float, az: float) -> tuple[float, float, float]:
    """歩く画面（three.js）の太陽の向き（太陽へ向かう単位ベクトル）。look.js と同じ式。"""
    a, z = math.radians(alt), math.radians(az)
    return (math.sin(z) * math.cos(a), math.sin(a), -math.cos(z) * math.cos(a))


def for_web(d: dict | None = None) -> dict:
    """site.json の "daylight"（歩く画面が既定値に使う）。高さ・方位は確かめ用に添える。"""
    d = d or load()
    alt, az = sun_of(d)
    return {"date": d["date"], "time": d["time"], "lat": d["lat"], "lon": d["lon"],
            "sun_alt_deg": round(alt, 2), "sun_az_deg": round(az, 2), "source": "data/daylight.json"}
