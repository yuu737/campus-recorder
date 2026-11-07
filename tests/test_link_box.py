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


# --- 11/8 F9：複数の相手・部品の平面・LOD1 の底 ------------------------------------------------

def test_multiple_targets_union_replaces_box(world):
    """1 つの箱が棟の外の 2 つの敷地の物にまたがる：どちらか 1 つでは 7 割に届かず、和で届けば replaced_by の並びで結ぶ。"""
    doc, pl, bdir = world
    pl["buildings"].append({"gml_id": "bldg_split000-x", "footprint": _sq(50, 0, 60, 10), "height_m": 24.0, "base_z": 40.0})
    doc["buildings"].append({"id": "osm-w", "plateau_parts": ["bldg_split000-x"], "confirmation": {"status": "unconfirmed"}})
    doc["objects"]["items"] += [{"id": "deck-w", "type": "deck", "polygon": {"value": _sq(50, 0, 55, 10)}},
                                {"id": "deck-e", "type": "deck", "polygon": {"value": _sq(55, 0, 60, 10)}}]
    r1 = site.link_box(doc, "deck-w", "bldg_split", "?", "test", plateau=pl, buildings_root=bdir)
    assert r1["outcome"] == "request" and r1["overlap"]["best"] == pytest.approx(0.5)
    r = site.link_box(doc, ["deck-w", "deck-e"], "bldg_split", "甲板が 2 つ", "test", plateau=pl, buildings_root=bdir)
    assert r["outcome"] == "replaced" and r["overlap"]["by"]["union"]["inside_ratio"] == pytest.approx(1.0)
    assert site.replaced_boxes(doc)["bldg_split000-x"] == ["deck-w", "deck-e"]
    req = next(q for q in doc["requests"] if q["box"] == "bldg_split000-x")
    assert req["status"] == "closed"                                          # 前に結べなかった依頼は閉じる


def test_multiple_targets_building_and_object(world):
    """棟（輪郭）と敷地の物の和：半分が棟 a、残りが甲板なら結ぶ。和が 7 割未満なら依頼だけ（id に相手を並べる）。"""
    doc, pl, bdir = world
    doc["objects"]["items"].append({"id": "deck-h", "type": "deck", "polygon": {"value": _sq(20, 0, 25, 10)}})
    r = site.link_box(doc, ["a", "deck-h"], "bldg_half", "棟と甲板", "test", plateau=pl, buildings_root=bdir)
    assert r["outcome"] == "replaced" and r["overlap"]["best"] == pytest.approx(1.0)
    assert r["target"] == ["a", "deck-h"]
    r2 = site.link_box(doc, ["a", "deck-1"], "bldg_in0", "?", "test", plateau=pl, buildings_root=bdir, min_ratio=1.01)
    assert r2["outcome"] == "request" and r2["request"] == "plateau-in000000-a+deck-1"


def test_building_parts_count_as_outline(world):
    """棟 JSON の部品（composite のデッキ）の平面も棟の輪郭に入る（足元の外のテラス・デッキに付いた箱）。"""
    doc, pl, bdir = world
    b = json.loads((bdir / "a.json").read_text(encoding="utf-8"))
    # f0 は (0,0)→(20,0)、外向きは −y。d 0〜10 のデッキ＝ y −10〜0
    b["parts"] = [{"type": "composite", "name": "デッキ", "face": "f0", "along": 0.0, "status": "estimated", "evidence": ["p"],
                   "method": "m", "tol": 1.0,
                   "primitives": [{"shape": "box", "s": [0, 20], "d": [0, 10], "z": [0, 1], "material": "brick"}]}]
    (bdir / "a.json").write_text(json.dumps(b), encoding="utf-8")
    pl["buildings"].append({"gml_id": "bldg_terr000-x", "footprint": _sq(2, -9, 18, -1), "height_m": 70.0, "base_z": 40.0})
    doc["buildings"].append({"id": "osm-t", "plateau_parts": ["bldg_terr000-x"], "confirmation": {"status": "unconfirmed"}})
    r = site.link_box(doc, "a", "bldg_terr", "テラス", "test", plateau=pl, buildings_root=bdir)
    assert r["outcome"] == "linked"
    assert r["overlap"]["by"]["footprint"]["inside_ratio"] == 0.0 and r["overlap"]["by"]["parts"]["inside_ratio"] == pytest.approx(1.0)


def test_parse_building_lod1_bottom_when_no_lod0():
    """LOD0 が無く LOD1 の立体だけの建物は、立体の底（一番低い z の面）を足元にして拾う（出典に「LOD1 の底」）。"""
    import xml.etree.ElementTree as ET
    from recorder import geo

    def ring(pts, z):
        out = []
        for x, y in pts + pts[:1]:
            lon, lat = geo.xy_to_lonlat(x, y)
            out += [f"{lat:.9f}", f"{lon:.9f}", f"{z}"]
        return " ".join(out)

    sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
    ns = ('xmlns:bldg="http://www.opengis.net/citygml/building/2.0" xmlns:gml="http://www.opengis.net/gml"')
    polys = [ring(sq, 40.0), ring(sq, 70.0)]       # 底と上の面だけ（側面は底の判定に効かない）
    surf = "".join(f"<gml:surfaceMember><gml:Polygon><gml:exterior><gml:LinearRing><gml:posList>{p}</gml:posList>"
                   f"</gml:LinearRing></gml:exterior></gml:Polygon></gml:surfaceMember>" for p in polys)
    xml = (f'<bldg:Building {ns} gml:id="bldg_t"><bldg:measuredHeight>30</bldg:measuredHeight>'
           f'<bldg:lod1Solid><gml:Solid><gml:exterior><gml:CompositeSurface>{surf}</gml:CompositeSurface></gml:exterior>'
           f'</gml:Solid></bldg:lod1Solid></bldg:Building>')
    b = site._parse_building(ET.fromstring(xml))
    assert b is not None and b["footprint_source"] == "LOD1 の底" and b["base_z"] == 40.0
    from shapely.geometry import Polygon
    assert Polygon(b["footprint"]).area == pytest.approx(100.0, rel=0.01)
