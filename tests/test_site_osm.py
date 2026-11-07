"""敷地の取得を敷地 JSON から読む（監査 A の 1・2・3・5・8・17・20、11/8 F1）のテスト。ネットには出ない。"""
import json

import pytest

from recorder import geo, site, skeleton
from recorder.schema import validate_building


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setattr(site, "DATA_SITE", tmp_path / "data" / "site")
    monkeypatch.setattr(site, "OUT_SITE", tmp_path / "out" / "site")
    monkeypatch.setattr(site, "_overpass", lambda q: (_ for _ in ()).throw(AssertionError("ネットに出た: " + q)))
    geo.reset()
    geo.configure_from_site({"crs": geo.crs_for(35.0, 137.0)})
    yield
    geo.reset()


def _sq(lon0, lat0, d=0.001):
    return [{"lon": lon0, "lat": lat0}, {"lon": lon0 + d, "lat": lat0}, {"lon": lon0 + d, "lat": lat0 + d},
            {"lon": lon0, "lat": lat0 + d}, {"lon": lon0, "lat": lat0}]


def _raw_way(way_id=500, key=True):
    raw = {"elements": [
        {"type": "way", "id": way_id, "tags": {"amenity": "university", "name": "架空大学"}, "geometry": _sq(137.0, 35.0, 0.004)},
        {"type": "way", "id": 1, "tags": {"building": "university", "name": "一号館"}, "geometry": _sq(137.001, 35.001)},
        {"type": "way", "id": 2, "tags": {"building": "yes"}, "geometry": _sq(137.002, 35.002)},
    ], "_fetched_at": "2026-11-08T00:00:00"}
    if key:
        raw["_key"] = f"way/{way_id}"
    return raw


def _write_site(doc):
    p = site.DATA_SITE / f"{doc['campus']}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p


def test_osm_ref_order_and_error():
    assert site.osm_ref({"sources": {"osm": {"relation": 7, "way": 9}}}) == ("relation", 7)
    assert site.osm_ref({"sources": {"osm": {"relation": None, "way": "way/9"}}}) == ("way", 9)
    k, bb = site.osm_ref({"sources": {"osm": {}}, "bbox_lonlat": [35.2, 137.1, 35.1, 137.0]})
    assert k == "bbox" and bb == (35.1, 137.0, 35.2, 137.1)
    with pytest.raises(ValueError, match="境界"):
        site.osm_ref({"sources": {"osm": {"relation": None}}, "bbox_lonlat": None})


def test_fetch_osm_needs_a_reference():
    with pytest.raises(ValueError, match="既定値はありません"):
        site.fetch_osm()


def test_osm_site_with_way_boundary():
    o = site.osm_site(_raw_way())
    assert o["boundary_ref"] == "way/500" and len(o["boundary"]) == 1
    assert [b["osm"] for b in o["buildings"]] == ["way/1", "way/2"]
    o2 = site.osm_site(_raw_way(key=False))        # 旧来のキャッシュ（_key 無し）でも境界を見つける
    assert o2["boundary_ref"] == "way/500"
    with pytest.raises(ValueError, match="relation/99"):
        site.osm_site(_raw_way(), "relation/99")


def test_cache_is_per_campus_and_legacy_only_when_same_boundary():
    cdir = site.DATA_SITE / "cache"
    cdir.mkdir(parents=True)
    legacy = {"elements": [{"type": "relation", "id": 111, "members": []}], "_fetched_at": "old"}
    (cdir / "osm_raw.json").write_text(json.dumps(legacy), encoding="utf-8")
    assert site.fetch_osm(111, campus="a")["_fetched_at"] == "old"            # 旧来の 1 校用の名前：境界が同じなら読む
    with pytest.raises(AssertionError, match="ネットに出た"):
        site.fetch_osm(222, campus="b")                                        # 別の大学の境界なら旧来のキャッシュを使わない
    (cdir / "osm_raw_b.json").write_text(json.dumps(_raw_way(333)), encoding="utf-8")
    assert site.fetch_osm(way_id=333, campus="b")["_key"] == "way/333"
    assert site.osm_cache_path("b").name == "osm_raw_b.json"


def test_osm_for_campus_sets_origin_when_missing():
    p = _write_site({"campus": "w", "crs": {"epsg": None, "origin_lat": None, "origin_lon": None},
                     "sources": {"osm": {"relation": None, "way": 500}}})
    (site.DATA_SITE / "cache").mkdir(parents=True)
    (site.DATA_SITE / "cache" / "osm_raw_w.json").write_text(json.dumps(_raw_way()), encoding="utf-8")
    o = site.osm_for_campus("w")
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["crs"]["origin_lat"] == pytest.approx(35.002, abs=1e-4) and d["crs"]["epsg"] == 6675
    assert geo.current()["origin_lon"] == pytest.approx(137.002, abs=1e-4)
    xs = [x for r in o["boundary"] for x, _ in r]
    assert min(xs) < 0 < max(xs)                      # 原点が境界の中


def test_build_site_writes_actual_osm_and_keeps_crs(tmp_path):
    crs = geo.crs_for(35.002, 137.002)
    p = _write_site({"campus": "w", "crs": crs, "bbox_lonlat": [35.0, 137.0, 35.004, 137.004],
                     "sources": {"osm": {"relation": None, "way": None, "license": "ODbL"}, "plateau": None},
                     "objects": [{"id": "o1"}], "scope": {"targets": []}})
    geo.configure_from_site(json.loads(p.read_text(encoding="utf-8")))
    osm = site.osm_site(_raw_way())
    doc = site.build_site("w", osm, None)
    assert doc["sources"]["osm"]["way"] == 500 and "relation" not in doc["sources"]["osm"]
    assert doc["crs"] == crs                           # 定数で上書きしない
    assert doc["bbox_lonlat"] and doc["objects"] == [{"id": "o1"}]   # 知らない欄も引き継ぐ
    assert doc["sources"]["plateau"] is None and all(b["plateau_parts"] == [] for b in doc["buildings"])


def test_campus_check_permission_none_is_resolved(tmp_path):
    d = {"campus": "x", "buildings": [], "scope": {"targets": []}, "name_links": [], "questions": [],
         "sources": {"campus_map": {"permission": {"status": "none"}}}}
    _write_site(d)
    assert site.campus_check("x")["ok"]
    d["sources"]["campus_map"]["permission"]["status"] = None
    _write_site(d)
    r = site.campus_check("x")
    assert not r["ok"] and any("none" in l for l in site.campus_check_lines(r))


def test_plateau_label_from_zip_name():
    assert "nagoya-shi" in site.plateau_label("23100_nagoya-shi_city_2022_citygml_4_op.zip")
    assert "2022" in site.plateau_label("23100_nagoya-shi_city_2022_citygml_4_op.zip")
    assert site.plateau_label("foo.zip") == "PLATEAU（foo.zip）"
    assert skeleton.plateau_label({"source": "PLATEAU 某市 2024（国土交通省）、建物 LOD0/LOD1 のみ"}) == "PLATEAU 某市 2024（国土交通省）"


def test_from_site_cli_without_plateau_uses_osm_only():
    crs = geo.crs_for(35.002, 137.002)
    _write_site({"campus": "w", "crs": crs, "buildings": [
        {"id": "ichigo", "name": "一号館", "osm": "way/1", "plateau_parts": ["gone"],
         "footprint_osm": [[[0, 0], [30, 0], [30, 12], [0, 12], [0, 0]]]}]})
    assert not (site.DATA_SITE / "plateau.json").exists()
    b = skeleton.from_site_cli("ichigo")
    assert validate_building(b) == []
    assert b["campus"] == "w" and b["crs"] == crs
    assert b["footprint"]["evidence"] == ["osm:way/1"] and "PLATEAU を使っていない" in b["footprint"]["method"]
    assert b["parapet_top"]["status"] == "unknown" and b["parapet_top"]["value"] is None
    lens = [f["length"] for f in b["faces"]]
    assert lens and all(L["status"] == "estimated" and L["evidence"] == ["osm:way/1"] for L in lens)


def test_default_campus_env_first(monkeypatch):
    _write_site({"campus": "a"})
    assert site.default_campus() == "a"
    monkeypatch.setenv("RECORDER_CAMPUS", "b")
    assert site.default_campus() == "b"


def test_build_site_carries_photo_policy_and_declination():
    _write_site({"campus": "w", "crs": geo.crs_for(35.002, 137.002), "photo_policy": {"period_start": None},
                 "declination_deg": -7.5, "sources": {"osm": {"way": 500}}})
    doc = site.build_site("w", site.osm_site(_raw_way()), None)
    assert doc["photo_policy"] == {"period_start": None} and doc["declination_deg"] == -7.5


def test_new_building_defaults_from_site():
    from recorder.schema import new_building
    crs = geo.crs_for(35.6812, 139.7671)
    _write_site({"campus": "tokyo-x", "crs": crs})
    b = new_building("a", "A")
    assert b["campus"] == "tokyo-x" and b["crs"]["epsg"] == 6677 and b["crs"]["origin_lat"] == crs["origin_lat"]
    b2 = new_building("a", "A", campus="other", crs={"epsg": 1})
    assert b2["campus"] == "other" and b2["crs"] == {"epsg": 1}


def test_web_header_has_university_and_campus_name():
    h = site.web_header({"university": "架空大学", "campus_name": "本キャンパス"}, "x")
    assert list(h)[:2] == ["schema", "campus"] and h["university"] == "架空大学" and h["campus_name"] == "本キャンパス"
    assert site.web_header({}, "y")["university"] is None
