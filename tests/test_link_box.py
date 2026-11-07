"""site link-box：PLATEAU の箱を棟・敷地の物に結ぶか、重なりが足りなければ撮影依頼にする（11/8 F6）。"""
import json

import pytest

from recorder import site


def _sq(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


@pytest.fixture
def world(tmp_path):
    bdir = tmp_path / "buildings"
    bdir.mkdir()
    poly = _sq(0, 0, 20, 10)
    faces = [{"id": f"f{i}", "from": poly[i], "to": poly[(i + 1) % 4]} for i in range(4)]
    (bdir / "a.json").write_text(json.dumps({"id": "a", "footprint": {"polygon": poly}, "faces": faces}), encoding="utf-8")
    plateau = {"buildings": [
        {"gml_id": "bldg_in000000-x", "footprint": _sq(1, 1, 9, 9), "height_m": 30.0, "base_z": 40.0},      # 全部 a の中
        {"gml_id": "bldg_half0000-x", "footprint": _sq(15, 0, 25, 10), "height_m": 20.0, "base_z": 40.0},   # 半分だけ
        {"gml_id": "bldg_deck0000-x", "footprint": _sq(30, 0, 40, 10), "height_m": 24.0, "base_z": 40.0},   # 甲板の中
    ]}
    doc = {"buildings": [
        {"id": "a", "plateau_parts": [], "confirmation": {"status": "unconfirmed", "by": None, "at": None, "note": ""}},
        {"id": "osm-x", "plateau_parts": ["bldg_in000000-x"], "confirmation": {"status": "unconfirmed"}},
        {"id": "osm-y", "plateau_parts": ["bldg_half0000-x"], "confirmation": {"status": "unconfirmed"}},
        {"id": "osm-z", "plateau_parts": ["bldg_deck0000-x"], "confirmation": {"status": "unconfirmed"}}],
        "plateau_only": [],
        "objects": {"items": [{"id": "deck-1", "type": "deck", "polygon": {"value": _sq(29, -1, 41, 11)}}]}}
    return doc, plateau, bdir


def test_links_box_inside_outline(world):
    doc, pl, bdir = world
    r = site.link_box(doc, "a", "bldg_in0", "写真で同じ塊", "test", plateau=pl, buildings_root=bdir)
    assert r["outcome"] == "linked" and r["overlap"]["best"] == pytest.approx(1.0)
    parts = {b["id"]: b["plateau_parts"] for b in doc["buildings"]}
    assert parts["a"] == ["bldg_in000000-x"] and parts["osm-x"] == []
    assert doc["buildings"][0]["confirmation"]["plateau_links"][0]["inside_ratio"] == pytest.approx(1.0)
    assert not doc.get("requests")


def test_half_overlap_writes_request_not_link(world):
    doc, pl, bdir = world
    r = site.link_box(doc, "a", "bldg_half", "?", "test", plateau=pl, buildings_root=bdir, ask="西から 2 枚")
    assert r["outcome"] == "request" and r["overlap"]["best"] == pytest.approx(0.5)
    assert doc["buildings"][2]["plateau_parts"] == ["bldg_half0000-x"]       # 結んでいない
    req = doc["requests"][0]
    assert req["status"] == "open" and req["ask"] == "西から 2 枚" and req["box"] == "bldg_half0000-x"
    site.link_box(doc, "a", "bldg_half", "?", "test", plateau=pl, buildings_root=bdir)
    assert len(doc["requests"]) == 1                                         # 同じ依頼は重ねない


def test_replaced_by_site_object_and_rebuild_keeps_link(world):
    doc, pl, bdir = world
    r = site.link_box(doc, "deck-1", "bldg_deck", "甲板", "test", plateau=pl, buildings_root=bdir)
    assert r["outcome"] == "replaced"
    assert site.replaced_boxes(doc) == {"bldg_deck0000-x": "deck-1"}
    site.link_box(doc, "a", "bldg_in0", "写真で同じ塊", "test", plateau=pl, buildings_root=bdir)
    doc["buildings"][1]["plateau_parts"] = ["bldg_in000000-x"]                # site build で作り直した時の形
    site.apply_plateau_links(doc)
    assert doc["buildings"][1]["plateau_parts"] == [] and doc["buildings"][0]["plateau_parts"] == ["bldg_in000000-x"]


def test_unknown_box_or_target(world):
    doc, pl, bdir = world
    with pytest.raises(KeyError):
        site.link_box(doc, "a", "bldg_nope", "?", "test", plateau=pl, buildings_root=bdir)
    with pytest.raises(KeyError):
        site.link_box(doc, "zz", "bldg_in0", "?", "test", plateau=pl, buildings_root=bdir)
