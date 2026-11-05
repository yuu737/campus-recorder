"""地面の 3 段（11/5）：OSM の地表の種類のマスク ／ 棟 JSON の ground_offset で帯を上書き ／ 5 m 標高。"""
import json
from pathlib import Path

import numpy as np
import pytest

from recorder import site

K = {k: i for i, k in enumerate(site.GROUND_KINDS)}


# --- OSM の分類 -------------------------------------------------------------------

def test_classify_osm_tags():
    c = site.classify_osm_ground
    assert c({"highway": "residential"}, False)[:2] == ("asphalt", "line")
    assert c({"highway": "residential", "surface": "paved"}, False)[0] == "asphalt"      # paved は種類の既定のまま
    assert c({"highway": "footway"}, False)[:2] == ("paving", "line")
    assert "surface タグ無し" in c({"highway": "footway"}, False)[2]
    assert c({"highway": "steps"}, False)[0] == "steps"
    assert c({"highway": "service", "service": "parking_aisle"}, False)[0] == "parking"
    assert c({"highway": "pedestrian"}, True)[:2] == ("paving", "area")
    assert c({"amenity": "parking"}, True)[:2] == ("parking", "area")
    assert c({"landuse": "grass"}, True)[:2] == ("grass", "area")
    assert c({"landuse": "education"}, True) is None          # 区域であって地表ではない
    assert c({"landuse": "grass"}, False) is None             # 閉じていない線は面にしない


def _raw_way(i, tags, pts):
    """原点からの m の点 → Overpass の geometry（緯度経度）。"""
    from recorder import geo
    g = []
    for x, y in pts:
        lon, lat = geo.xy_to_lonlat(x, y)
        g.append({"lon": lon, "lat": lat})
    return {"type": "way", "id": i, "tags": tags, "geometry": g}


def test_osm_ground_width_default_and_tag():
    raw = {"elements": [_raw_way(1, {"highway": "residential"}, [(0, 0), (50, 0)]),
                        _raw_way(2, {"highway": "footway", "width": "3.5"}, [(0, 10), (50, 10)]),
                        _raw_way(3, {"highway": "service"}, [(0, 20), (50, 20)])],
           "_fetched_at": "t"}
    go = site.osm_ground(raw)
    by = {f["osm"]: f for f in go["features"]}
    assert by["way/1"]["width_m"] == 6.0 and "標準幅 6 m" in by["way/1"]["width_method"]
    assert by["way/2"]["width_m"] == 3.5 and "width=3.5" in by["way/2"]["width_method"]
    assert by["way/3"]["width_m"] == 4.0
    assert go["license"] == "ODbL" and go["status"] == "estimated"
    assert by["way/1"]["coords"][1][0] == pytest.approx(50.0, abs=0.05)


# --- マスク ------------------------------------------------------------------------

def _line(kind, coords, w):
    return {"kind": kind, "shape": "line", "coords": coords, "width_m": w}


def test_mask_kinds_and_widths():
    go = {"features": [
        {"kind": "grass", "shape": "area", "rings": [[[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]]},
        _line("asphalt", [[0, 50.2], [100, 50.2]], 6.0),      # 東西の車道、幅 6 m（47.2〜53.2）
        _line("paving", [[30.2, 0], [30.2, 100]], 2.0),       # 南北の歩道、幅 2 m（29.2〜31.2。車道の後に塗るので交差点は歩道）
    ]}
    foot = [[[70, 70], [80, 70], [80, 80], [70, 80]]]
    gm = site.ground_mask(go, (0, 0, 100, 100), foot)
    assert gm["mask"].shape == (100, 100) and gm["y_top"] == 100
    at = lambda x, y: int(site.mask_kind_at(gm, np.array([x]), np.array([y]))[0])
    assert at(10, 50) == K["asphalt"] and at(10, 52.4) == K["asphalt"] and at(10, 48.0) == K["asphalt"] and at(10, 55) == K["grass"] and at(10, 45.5) == K["grass"]   # 境界は 1 m 格子（±1 画素）
    assert at(30, 20) == K["paving"] and at(29.9, 20) == K["paving"] and at(32.6, 20) == K["grass"] and at(27.6, 20) == K["grass"]
    assert at(75, 75) == K["building"]                      # 建物の足元は除く
    assert at(-5, 50) == K["unknown"]                       # 範囲外
    # 車道の面積 ≒ 100 m × 6 m（歩道との交差 2×6 を引く）
    assert gm["counts"]["asphalt"]["area_m2"] == pytest.approx(600 - 12, abs=40)
    assert gm["counts"]["paving"]["area_m2"] == pytest.approx(200, abs=20)


def test_mask_png_is_indexed_with_ids(tmp_path):
    from PIL import Image
    gm = site.ground_mask({"features": [_line("asphalt", [[0, 5], [20, 5]], 4.0)]}, (0, 0, 20, 10))
    site.save_ground_mask_png(gm, tmp_path / "m.png")
    im = Image.open(tmp_path / "m.png")
    assert im.mode == "P"
    assert np.array_equal(np.asarray(im), gm["mask"])
    pal = im.getpalette()[:3 * len(site.GROUND_KINDS)]
    assert tuple(pal[3 * K["asphalt"]:3 * K["asphalt"] + 3]) == site.GROUND_KIND_COLOR["asphalt"]


# --- ground_offset の上書き ------------------------------------------------------------

def _flat(n=81, z=50.0):
    gx = np.arange(-40.0, -40.0 + n)
    GX, GY = np.meshgrid(gx, gx)
    return GX, GY, np.full(GX.shape, z)


def _bld(value, depth=None, status="estimated"):
    go = {"value": value, "tol": 0.5, "status": status, "evidence": ["P1"], "method": "t"}
    if depth:
        go["depth"] = depth
    # 正方形 0〜10。E 面（x=10、南→北）の外向きは +x
    return {"id": "t", "ground_z": {"value": 50.0, "status": "estimated"},
            "faces": [{"id": "E", "from": [10, 0], "to": [10, 10], "ground_offset": go},
                      {"id": "S", "from": [0, 0], "to": [10, 0]}]}


def _z(GX, GY, Z, x, y):
    j, i = int(np.argmin(abs(GY[:, 0] - y))), int(np.argmin(abs(GX[0] - x)))
    return Z[j, i]


def test_override_band_and_smooth_blend():
    GX, GY, Z = _flat()
    specs = site.ground_override_specs(_bld(-3.0))
    assert [s["face"] for s in specs] == ["E"] and specs[0]["depth_m"] == 12.0 and specs[0]["target_z"] == 47.0
    Z2, recs = site.apply_ground_overrides(GX, GY, Z, specs, z_ref=0.0)
    assert recs[0]["applied"] and recs[0]["shift_m"] == -3.0 and recs[0]["evidence"] == ["P1"]
    assert _z(GX, GY, Z2, 15, 5) == pytest.approx(47.0)          # 帯の中（面から 5 m）
    assert _z(GX, GY, Z2, 22, 5) == pytest.approx(47.0)          # 帯の奥の端（12 m）
    assert _z(GX, GY, Z2, 28, 5) == pytest.approx(50.0)          # 帯の外 6 m（> 5 m）は元のまま
    t = 1 - 2 / 5                                                # 帯の外 2 m：smoothstep(0.6) の重み
    assert _z(GX, GY, Z2, 24, 5) == pytest.approx(50 - 3 * t * t * (3 - 2 * t))
    prof = [_z(GX, GY, Z2, x, 5) for x in range(22, 29)]
    assert all(b >= a for a, b in zip(prof, prof[1:]))           # 滑らかに上る（段や逆戻りが無い）
    assert _z(GX, GY, Z2, 15, 20) == pytest.approx(50.0)         # 面の幅の外 5 m 超（y=15 より先）は元のまま


def test_override_keeps_relief_and_uses_depth():
    GX, GY, Z = _flat()
    Z = Z + GX / 10.0                                            # 東へ上る斜面
    Z2, recs = site.apply_ground_overrides(GX, GY, Z, site.ground_override_specs(_bld(2.0, depth=6)), z_ref=0.0)
    band = (GX >= 10) & (GX <= 16) & (GY >= 0) & (GY <= 10)
    assert np.median(Z2[band]) == pytest.approx(52.0)
    assert np.allclose(np.diff(Z2[band].reshape(-1, 7), axis=1), 0.1)   # 帯の中の起伏（1/10）はそのまま
    assert recs[0]["depth_m"] == 6


def test_override_skips_large_conflict_and_unknown():
    GX, GY, Z = _flat()
    Z2, recs = site.apply_ground_overrides(GX, GY, Z, site.ground_override_specs(_bld(-8.0)), z_ref=0.0)
    assert not recs[0]["applied"] and "食い違う" in recs[0]["skipped"] and np.array_equal(Z2, Z)
    assert site.ground_override_specs(_bld(None, status="unknown")) == []


def test_override_relative_grid_for_model():
    """模型の地形（ground_z を引いた値）にも同じ帯が効く。"""
    GX, GY, Z = _flat(z=0.0)
    Z2, _ = site.apply_ground_overrides(GX, GY, Z, site.ground_override_specs(_bld(1.5)), z_ref=50.0)
    assert _z(GX, GY, Z2, 15, 5) == pytest.approx(1.5)


# --- 書き出し ----------------------------------------------------------------------------

def test_export_web_writes_mask_and_overrides(tmp_path):
    if not (site.DATA_SITE / "tempaku.json").exists() or not (site.OUT_SITE / "dem5a.npz").exists():
        pytest.skip("敷地 JSON か標高が無い")
    doc = json.loads((site.DATA_SITE / "tempaku.json").read_text(encoding="utf-8"))
    if not doc.get("ground_osm") and not (site.DATA_SITE / "cache" / "osm_ground_raw.json").exists():
        pytest.skip("OSM の地表の種類が未取得（ネットに出ない）")
    site.export_web(web_dir=tmp_path)
    d = json.loads((tmp_path / "data" / "site.json").read_text(encoding="utf-8"))
    g = d["ground"]
    m = g["mask"]
    assert (tmp_path / m["url"]).exists() and (tmp_path / "data" / "ground_mask.json").exists()
    assert [k["kind"] for k in m["kinds"]] == list(site.GROUND_KINDS)
    assert m["license"] == "ODbL" and m["status"] == "estimated"
    assert m["x0"] == g["x0"] and m["w"] == (g["nx"] - 1) * g["step"]
    assert m["counts"]["asphalt"]["area_m2"] > 0
    for o in g["overrides"]:
        assert o["evidence"] is not None and ("method" in o) == o["applied"]
        assert o["applied"] or o["skipped"]


def test_viewer_uses_mask_and_override_frames():
    html = (Path(site.__file__).resolve().parent.parent / "web" / "index.html").read_text(encoding="utf-8")
    assert "groundMaskShader" in html and "G.overrides" in html and "OpenStreetMap" in html
