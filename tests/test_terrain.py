"""模型の地形（recorder.site の terrain_grid / fill_gaps / check_ground_offsets）。

11/4：面ごとの「地面の板」（面の幅 × 奥行き 12 m の仮の長方形）が屋根伏せと鳥瞰を埋め尽くし、板同士が重なり、
入隅で建物に食い込んだ。地面は 5m 標高から 1 枚の地形にし、ground_offset は検算だけに使う。その再発防止。
"""
import math

import numpy as np
import pytest

from recorder import site


def slope_dem(nan_box=None):
    """1 m 格子、東へ 1/10 で上る斜面（標高 = 50 + x/10）。nan_box=(x0,x1,y0,y1) を欠けにする。"""
    gx = np.arange(-100.0, 101.0)
    gy = np.arange(-100.0, 101.0)
    GX, GY = np.meshgrid(gx, gy)
    Z = 50.0 + GX / 10.0
    if nan_box:
        x0, x1, y0, y1 = nan_box
        Z[(GX >= x0) & (GX <= x1) & (GY >= y0) & (GY <= y1)] = np.nan
    return {"x": gx, "y": gy, "z": Z}


SQ = [[0, 0], [10, 0], [10, 10], [0, 10]]


def test_terrain_covers_footprint_with_margin():
    t = site.terrain_grid(slope_dem(), SQ, ground_z=50.0)
    assert t["x"][0] <= -20 and t["x"][-1] >= 30 and t["y"][0] <= -20 and t["y"][-1] >= 30
    assert t["mask"].all()
    # 高さは ground_z を引いた棟の z=0 からの m。斜面はそのまま（均しても一次の斜面は変わらない）
    assert site.grid_z(t, 5.0, 5.0) == pytest.approx(0.5, abs=1e-6)
    assert site.grid_z(t, -10.0, 5.0) == pytest.approx(-1.0, abs=1e-6)


def test_coarse_grid_matches_fine_grid():
    """背景の粗い格子（5 m）は細かい格子と同じ均した場から間引くので、同じ点で同じ高さ（継ぎ目が合う）。"""
    dem = slope_dem()
    dem["z"] = dem["z"] + np.sin(dem["x"][None, :] / 3.0)        # 凸凹
    fine = site.terrain_grid(dem, SQ, 50.0)
    far = site.terrain_grid(dem, SQ, 50.0, margin_m=60.0, step_m=5.0)
    for x, y in ((-15.0, -15.0), (0.0, 10.0), (25.0, 25.0)):
        assert site.grid_z(far, x, y) == pytest.approx(site.grid_z(fine, x, y), abs=1e-9)


def test_fill_gaps_from_neighbours():
    Z = np.array([[1.0, 1.0, 1.0], [1.0, np.nan, 3.0], [1.0, 3.0, 3.0]])
    F, ratio = site.fill_gaps(Z)
    assert not np.isnan(F).any()
    assert F[1, 1] == pytest.approx(2.0)                     # 上下左右 1,3,1,3 の平均
    assert ratio == pytest.approx(1 / 9)
    # 足元の欠け（建物の中など）も地形の格子では埋まる
    t = site.terrain_grid(slope_dem(nan_box=(-2, 12, -2, 12)), SQ, 50.0)
    assert not np.isnan(t["z"]).any() and t["nan_ratio"] > 0


def test_fill_gaps_all_missing_stays_nan():
    F, ratio = site.fill_gaps(np.full((3, 3), np.nan))
    assert np.isnan(F).all() and ratio == 1.0


def _bld(go, tol=0.3):
    return {"ground_z": {"value": 50.0, "status": "estimated"},
            "faces": [{"id": "E", "from": [10, 0], "to": [10, 10],
                       "ground_offset": {"value": go, "tol": tol, "status": "estimated", "evidence": [], "method": "t"}},
                      {"id": "S", "from": [0, 0], "to": [10, 0]}]}


def test_ground_offset_check_ok_and_warning():
    dem = slope_dem()
    # E 面の前（x = 10〜22）の中央値は 50 + 1.6 → +1.6 m
    checks, warns = site.check_ground_offsets(dem, _bld(1.6))
    assert [c["face"] for c in checks] == ["E"] and checks[0]["ok"] and not warns
    assert checks[0]["terrain_band_median"] == pytest.approx(1.6, abs=0.01)
    checks, warns = site.check_ground_offsets(dem, _bld(3.0))
    assert not checks[0]["ok"] and len(warns) == 1 and "E の ground_offset" in warns[0]


def test_polygon_distance():
    d = site.polygon_distance(np.array([5.0, 15.0, 13.0]), np.array([5.0, 5.0, 14.0]), SQ)
    assert d[0] == 0.0 and d[1] == pytest.approx(5.0) and d[2] == pytest.approx(5.0)
