"""土台の棟 JSON（PLATEAU から起こす灰色の箱）のテスト。"""
from recorder import skeleton
from recorder.schema import validate_building


def _site_and_plateau():
    site = {"buildings": [
        {"id": "x", "name": "試し棟", "osm": "way/1", "plateau_parts": ["p1"], "footprint_osm": [[[0, 0], [20, 0], [20, 10], [0, 10]]]},
        {"id": "y", "name": "PLATEAU 無し", "osm": "way/2", "plateau_parts": [], "footprint_osm": [[[0, 0], [30, 0], [30, 10], [0, 10]]]},
    ]}
    plateau = {"buildings": [
        {"gml_id": "p1", "height_m": 21.5, "storeys": 5, "base_z": 40.0, "footprint": [[0, 0], [20, 0], [20, 10], [0, 10]]},
        {"gml_id": "p2", "height_m": 9.0, "storeys": 2, "base_z": 40.0, "footprint": [[20, 0], [26, 0], [26, 10], [20, 10]]},
    ]}
    return site, plateau


def test_from_site_with_plateau():
    site, plateau = _site_and_plateau()
    b = skeleton.from_site("x", site, plateau)
    assert validate_building(b) == []
    assert b["footprint"]["status"] == "estimated" and len(b["footprint"]["polygon"]) == 4
    assert b["parapet_top"]["value"] == 21.5 and b["parapet_top"]["status"] == "estimated"
    assert b["floor_count"]["value"] == 5
    assert len(b["faces"]) == 4 and all(f["length"]["status"] == "estimated" for f in b["faces"])
    assert len(b["requests"]) == 4 and all(r["status"] == "open" for r in b["requests"])


def test_from_site_union_of_parts_and_osm_fallback():
    site, plateau = _site_and_plateau()
    b = skeleton.from_site("x", site, plateau, plateau_ids=["p1", "p2"])
    xs = [p[0] for p in b["footprint"]["polygon"]]
    assert max(xs) == 26.0 and b["parapet_top"]["value"] == 21.5
    c = skeleton.from_site("y", site, plateau)
    assert validate_building(c) == []
    assert c["footprint"]["method"].startswith("PLATEAU に無い") and c["parapet_top"]["status"] == "unknown"
