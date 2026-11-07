"""座標の原点と系を敷地 JSON から読む（監査 A の 4・6、11/8 F1）のテスト。"""
import json

import pytest

from recorder import geo, site


@pytest.fixture(autouse=True)
def _reset_geo():
    geo.reset()
    yield
    geo.reset()


@pytest.mark.parametrize("lat,lon,zone", [
    (35.1355, 136.9755, 7),    # 名古屋（天白）
    (35.6812, 139.7671, 9),    # 東京
    (34.7025, 135.4959, 6),    # 大阪
    (33.5902, 130.4017, 2),    # 福岡
    (43.0687, 141.3508, 12),   # 札幌
    (38.2601, 140.8822, 10),   # 仙台
    (26.2124, 127.6809, 15),   # 那覇
    (33.8392, 132.7657, 4),    # 松山
])
def test_plane_zone_for(lat, lon, zone):
    z, epsg = geo.plane_zone_for(lat, lon)
    assert z == zone and epsg == 6668 + zone


def test_configure_from_site_moves_origin():
    doc = {"campus": "x", "crs": geo.crs_for(35.1543, 136.9664)}
    assert doc["crs"]["epsg"] == 6675
    assert geo.configure_from_site(doc)
    x, y = geo.lonlat_to_xy(136.9664, 35.1543)
    assert abs(x) < 1e-6 and abs(y) < 1e-6
    lon, lat = geo.xy_to_lonlat(100.0, 50.0)
    assert lon > 136.9664 and lat > 35.1543
    assert geo.ORIGIN_LAT == pytest.approx(35.1543) and geo.EPSG_PLANE == 6675
    # 東京の原点に変えると、同じ緯度経度が別の値になる（定数に戻らない）
    geo.configure_from_site({"crs": geo.crs_for(35.6812, 139.7671)})
    assert geo.EPSG_PLANE == 6677
    x2, y2 = geo.lonlat_to_xy(139.7671, 35.6812)
    assert abs(x2) < 1e-6 and abs(y2) < 1e-6


def test_configure_from_site_ignores_empty_crs():
    geo.configure(6675, 35.0, 137.0)
    assert not geo.configure_from_site({"crs": {"epsg": None, "origin_lat": None, "origin_lon": None}})
    assert not geo.configure_from_site({})
    assert geo.current()["origin_lat"] == 35.0
    assert geo.configure_from_site({"axes": {"epsg": 6677, "origin_lat": 35.6, "origin_lon": 139.7}})   # axes でも読む
    assert geo.current()["epsg"] == 6677


def test_default_with_warning_when_no_site(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "DATA_SITE", tmp_path / "nothing")
    with pytest.warns(UserWarning, match="既定"):
        cur = geo.current()
    assert cur["epsg"] == geo.DEFAULT_EPSG_PLANE and cur["origin_lat"] == geo.DEFAULT_ORIGIN_LAT


def test_lazy_reads_default_campus(tmp_path, monkeypatch):
    d = tmp_path / "site"
    d.mkdir()
    (d / "u.json").write_text(json.dumps({"campus": "u", "crs": geo.crs_for(43.07, 141.35)}), encoding="utf-8")
    monkeypatch.setattr(site, "DATA_SITE", d)
    assert geo.current()["epsg"] == 6680 and geo.ORIGIN_LON == pytest.approx(141.35)


def test_campus_init_without_bbox_leaves_origin_empty(tmp_path):
    path, made = site.campus_init("y", "U", "C", data_site=tmp_path)
    d = json.loads(path.read_text(encoding="utf-8"))
    assert made and d["crs"]["origin_lat"] is None and d["crs"]["epsg"] is None   # 名城の原点を黙って入れない
    path, _ = site.campus_init("z", "U", "C", (35.67, 139.75, 35.69, 139.78), data_site=tmp_path, osm_way=123)
    d = json.loads(path.read_text(encoding="utf-8"))
    assert d["crs"]["epsg"] == 6677 and d["crs"]["zone"] == 9
    assert d["sources"]["osm"]["way"] == 123 and d["sources"]["osm"]["relation"] is None
