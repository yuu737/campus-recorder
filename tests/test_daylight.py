"""太陽と空の設定（data/daylight.json）を Blender と歩く画面が同じに読むことの検査。"""
import math
from pathlib import Path

import pytest

from recorder import daylight, site


def test_file_has_required_keys():
    d = daylight.load()
    assert {"date", "time", "lat", "lon", "blender"} <= set(d)
    assert d["date"] == "2026-11-06" and daylight.hour_of(d["time"]) == pytest.approx(13.5)


def test_day_and_hour():
    assert daylight.day_of_year("2026-11-06") == 310
    assert daylight.day_of_year("2026-01-01") == 1
    assert daylight.hour_of("9:15") == pytest.approx(9.25)


@pytest.mark.parametrize("doy,t,alt,az", [
    (310, 13.5, 31.4150, 212.3646),   # 値は web/look.js の sunPosition を node で回した物（11/7）。Python と JS が同じ式であることの固定
    (310, 10.0, 33.2650, 152.2596),
    (172, 12.0, 78.2318, 187.2174),
    (1, 9.25, 20.4963, 140.6590),
])
def test_sun_position_matches_look_js(doy, t, alt, az):
    h, a = daylight.sun_position(doy, t, 35.1355, 136.9755)
    assert h == pytest.approx(alt, abs=1e-3) and a == pytest.approx(az, abs=1e-3)


def test_blender_angles_reproduce_previous_values():
    # 11/7 まで blender_build に直書きしていた値（方位 215°・高さ 42°）：rotation_euler (48°, 0, -35°)、空の sun_rotation 145°
    rx, ry, rz = daylight.blender_sun_euler(42, 215)
    assert (math.degrees(rx), ry, math.degrees(rz)) == pytest.approx((48, 0, -35))
    assert math.degrees(daylight.blender_sky_rotation(215)) == pytest.approx(145)


@pytest.mark.parametrize("alt,az", [(31.4, 212.4), (33.3, 152.3), (60, 10), (5, 300)])
def test_blender_light_and_web_sun_point_the_same_way(alt, az):
    """Blender の光の進む向き（x＝東, y＝北, z＝上）＝ 歩く画面の太陽へ向かう向き（three.js の x, y＝上, −z＝北）の逆。"""
    tx, ty, tz = daylight.light_travel_xy(daylight.blender_sun_euler(alt, az))
    wx, wy, wz = daylight.web_sun_vector(alt, az)
    assert (tx, ty, tz) == pytest.approx((-wx, wz, -wy), abs=1e-9)
    # 空の太陽（Cycles：向き = (cos(rot+90°), sin(rot+90°))）も同じ方位
    rot = daylight.blender_sky_rotation(az)
    sx, sy = math.cos(rot + math.pi / 2), math.sin(rot + math.pi / 2)
    assert (sx, sy) == pytest.approx((math.sin(math.radians(az)), math.cos(math.radians(az))), abs=1e-9)


def test_site_json_gets_daylight():
    d = site._daylight_for_web()
    alt, az = daylight.sun_of(daylight.load())
    assert d["source"] == "data/daylight.json" and d["date"] == "2026-11-06" and d["time"] == "13:30"
    assert d["sun_alt_deg"] == pytest.approx(alt, abs=0.01) and d["sun_az_deg"] == pytest.approx(az, abs=0.01)


def test_web_reads_daylight_not_hardcoded():
    html = Path("web/index.html").read_text(encoding="utf-8")
    look = Path("web/look.js").read_text(encoding="utf-8")
    assert "site.daylight" in html and "time: DAY?.time" in html
    assert "opt.time" in look and "opt.date" in look


def test_blender_build_reads_daylight():
    src = Path("recorder/blender_build.py").read_text(encoding="utf-8")
    assert "daylight.load()" in src and "math.radians(42)" not in src and "math.radians(145)" not in src
