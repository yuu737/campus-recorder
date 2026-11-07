"""敷地の物（recorder/site_objects.py、K-107・T6）のテスト。棟 JSON・web/data/ は触らない（棟は小さな作り物、書き出しは一時フォルダ）。"""

import json
import struct
from pathlib import Path

import pytest

from recorder import site_objects as so

ROOT = Path(__file__).resolve().parent.parent


def est(v, tol=0.5, ev=("PXL_1",), method="写真の比", unit="m"):
    return {"value": v, "unit": unit, "tol": tol, "status": "estimated", "evidence": list(ev), "method": method, "note": ""}


def unk():
    return {"value": None, "unit": "m", "tol": None, "status": "unknown", "evidence": [], "method": "", "note": ""}


BUILDINGS = {
    "a": {"id": "a", "ground_z": est(40.0, 1.0, ["gsi:dem5a"]), "faces": {"f1": {"from": [0, 0], "to": [10, 0]}}},
    "b": {"id": "b", "ground_z": est(41.0, 1.0, ["gsi:dem5a"]),
          "faces": [{"id": "f4", "from": [10, 20], "to": [0, 20]}]},
}


def bridge(**kw):
    o = {"id": "br", "type": "bridge", "evidence": ["PXL_1"],
         "ends": [{"building": "a", "face": "f1", "along0": est(2.0), "along1": est(8.0), "z": est(6.0)},
                  {"building": "b", "face": "f4", "along0": est(2.0), "along1": est(8.0), "z": est(5.0)}],
         "floor_z": est(46.0, 0.5), "parapet_height": unk(), "beam_depth": est(1.0),
         "requests": [{"kind": "photo", "what": "腰壁にメジャー", "status": "open", "to": "staff"}]}
    o.update(kw)
    return o


def site_with(*items):
    return {"objects": {"schema": so.SCHEMA, "items": list(items)}}


def test_ok_bridge():
    errs, warns = so.validate_objects(site_with(bridge()), BUILDINGS, set())
    assert errs == [] and warns == []


def test_measured_without_photo_fails():
    o = bridge()
    o["floor_z"] = {"value": 46.0, "unit": "m", "tol": 0.1, "status": "measured", "evidence": ["osm:way/1"], "method": "x"}
    errs, _ = so.validate_objects(site_with(o), BUILDINGS, set())
    assert any("根拠の写真の無い measured" in e for e in errs)


def test_measured_length_needs_chain():
    o = bridge()
    o["beam_depth"] = {"value": 1.0, "unit": "m", "tol": 0.02, "status": "measured", "evidence": ["PXL_9"], "method": "メジャー"}
    errs, _ = so.validate_objects(site_with(o), BUILDINGS, set())
    assert any("鎖（chain）が無い" in e for e in errs)
    o["beam_depth"]["chain"] = [{"link": "tape", "photo": "PXL_9", "read": "0→100.0 cm", "n": 1}]
    errs, _ = so.validate_objects(site_with(o), BUILDINGS, set())
    assert errs == []
    o["beam_depth"]["chain"].append({"link": "eye", "photo": "PXL_9"})
    errs, _ = so.validate_objects(site_with(o), BUILDINGS, set())
    assert any("measured に使えない" in e for e in errs)


def test_measured_count_needs_only_evidence():
    o = {"id": "st", "type": "steps", "evidence": ["PXL_2"], "polygon": est([[0, 0], [4, 0], [4, 3], [0, 3]]),
         "z0": est(40.0), "z1": est(41.5),
         "count": {"value": 9, "unit": "段", "tol": None, "status": "measured", "evidence": ["PXL_2"], "method": "数えた"}}
    errs, _ = so.validate_objects(site_with(o), BUILDINGS, set())
    assert errs == []


def test_estimated_needs_evidence_and_method_unknown_needs_null():
    o = bridge(floor_z=est(46.0, ev=(), method=""), parapet_height={**unk(), "value": 1.1})
    errs, _ = so.validate_objects(site_with(o), BUILDINGS, set())
    assert any("根拠（evidence）の無い estimated" in e for e in errs)
    assert any("方法（method）の無い estimated" in e for e in errs)
    assert any("unknown なのに value がある" in e for e in errs)


def test_ends_must_exist_in_building_json():
    o = bridge()
    o["ends"][0]["building"] = "zz"
    o["ends"][1]["face"] = "f9"
    errs, _ = so.validate_objects(site_with(o), BUILDINGS, set())
    assert any("棟 'zz' が data/buildings/ に無い" in e for e in errs)
    assert any("面 'f9' が棟 b の JSON に無い" in e for e in errs)


def test_along_outside_face_fails():
    o = bridge()
    o["ends"][0]["along1"] = est(30.0, 0.5)
    errs, _ = so.validate_objects(site_with(o), BUILDINGS, set())
    assert any("の外" in e for e in errs)


def test_unknown_without_open_request_warns_and_unknown_type_fails():
    o = bridge(requests=[])
    _, warns = so.validate_objects(site_with(o), BUILDINGS, set())
    assert any("撮影依頼" in w for w in warns)
    errs, _ = so.validate_objects(site_with({"id": "x", "type": "fountain"}), BUILDINGS, set())
    assert any("type" in e for e in errs)


def test_asset_must_be_in_catalog():
    o = {"id": "t1", "type": "asset", "asset": "tree_big", "evidence": ["PXL_3"], "position": est([1.0, 2.0])}
    errs, _ = so.validate_objects(site_with(o), BUILDINGS, {"tree_small"})
    assert any("catalog" in e for e in errs)
    o["asset"] = "tree_small"
    assert so.validate_objects(site_with(o), BUILDINGS, {"tree_small"})[0] == []


def test_bridge_quad_pairs_ends_without_crossing():
    q = so.bridge_quad(bridge(), BUILDINGS)
    # a.f1 の 2→8 m は (2,0)(8,0)、b.f4 は (10,20)→(0,20) の 2→8 m で (8,20)(2,20)。交わらない並び
    assert q == [(2.0, 0.0), (8.0, 0.0), (8.0, 20.0), (2.0, 20.0)]


def test_steps_are_not_drawn_without_count():
    o = {"id": "st", "type": "steps", "polygon": est([[0, 0], [4, 0], [4, 3], [0, 3]]), "z0": est(40.0),
         "z1": est(41.5), "count": {**unk(), "unit": "段"}}
    meshes, skipped = so.object_meshes(o, BUILDINGS)
    assert meshes == [] and "なめらかな坂にはしない" in skipped[0]
    o["count"] = est(3, unit="段")
    meshes, _ = so.object_meshes(o, BUILDINGS)
    zs = sorted({round(p[2], 3) for t in meshes[0]["tris"] for p in t})
    assert zs == [40.0, 40.5, 41.0, 41.5]


def test_export_glb_axes_and_skips_unknown(tmp_path):
    doc = site_with(bridge())
    r = so.export_web_objects(doc, tmp_path / "data", BUILDINGS, set())
    assert r["errors"] == [] and r["model"] == {"url": "data/models/site_objects.glb", "ground_z": 0.0}
    g = so.read_glb_json(tmp_path / "data" / "models" / "site_objects.glb")
    assert [n["name"] for n in g["nodes"]] == ["br"]
    acc = g["accessors"][0]
    # glTF の Y が上：床の上端は標高 46.0、下端は 46−桁 1.0。z は −北（北 0〜20 m → −20〜0）
    assert acc["max"][1] == pytest.approx(46.0) and acc["min"][1] == pytest.approx(45.0)
    assert acc["min"][2] == pytest.approx(-20.0) and acc["max"][2] == pytest.approx(0.0)
    assert any("parapet_height" in s for s in r["skipped"])          # unknown の腰壁は描かない
    b = (tmp_path / "data" / "models" / "site_objects.glb").read_bytes()
    assert struct.unpack("<I", b[8:12])[0] == len(b) and len(b) % 4 == 0


def test_export_refuses_on_errors(tmp_path):
    o = bridge()
    o["ends"][0]["building"] = "zz"
    r = so.export_web_objects(site_with(o), tmp_path / "data", BUILDINGS, set())
    assert r["errors"] and r["glb"] is None and not (tmp_path / "data" / "models").exists()


def test_check_objects_end_heights():
    items = so.check_objects(site_with(bridge()), BUILDINGS)
    a = next(i for i in items if i["buildings"] == ["a"])
    assert a["diff_m"] == pytest.approx(0.0) and a["ok"]              # 40＋6＝46
    b_ = next(i for i in items if i["buildings"] == ["b"])
    assert b_["diff_m"] == pytest.approx(0.0)                          # 41＋5＝46
    o = bridge()
    o["ends"][1]["z"] = est(12.0, 0.1)
    bad = [i for i in so.check_objects(site_with(o), BUILDINGS) if not i["ok"]]
    assert bad and bad[0]["action"] in ("request", "soten")


def test_real_site_json_objects_validate():
    """リポジトリの data/site/tempaku.json の objects（通路橋の実例）が検証を通る。"""
    doc = json.loads((ROOT / "data" / "site" / "tempaku.json").read_text(encoding="utf-8"))
    items = so.objects_of(doc)
    assert any(o["id"] == "bridge-library-tower75" for o in items)
    bs = so.load_buildings(so.ends_buildings(doc))
    errs, warns = so.validate_objects(doc, bs, so.load_catalog_ids())
    assert errs == [] and warns == []
    br = next(o for o in items if o["id"] == "bridge-library-tower75")
    assert br["parapet_height"]["status"] == "unknown" and br["parapet_height"]["value"] is None
    assert all(not str(e).startswith("osm:") for e in br["evidence"])   # 物の有無の根拠は写真


def test_write_objects_keeps_format_and_rejects_errors(tmp_path):
    p = tmp_path / "s.json"
    base = {"schema": "campus-recorder/site/1", "buildings": []}
    p.write_text(json.dumps(base, ensure_ascii=False, indent=1), encoding="utf-8")
    o = bridge()
    o["ends"][0]["building"] = "zz"
    assert so.write_objects(p, {"schema": so.SCHEMA, "items": [o]}, BUILDINGS, set())
    assert json.loads(p.read_text(encoding="utf-8")) == base                 # 誤りがあれば書かない
    assert so.write_objects(p, {"schema": so.SCHEMA, "items": [bridge()]}, BUILDINGS, set()) == []
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["objects"]["items"][0]["id"] == "br"
    assert p.read_text(encoding="utf-8") == json.dumps(d, ensure_ascii=False, indent=1)


def test_corridor_draws_box_or_floor_only():
    """渡り廊下 corridor（11/8 F9）：高さが分かれば箱、unknown なら床だけ（箱は描かない）。棟 JSON は要らない。"""
    from recorder import site_objects as so
    poly = {"value": [[0, 0], [30, 0], [30, 6], [0, 6]], "unit": "m", "tol": 1.0, "status": "estimated",
            "evidence": ["PXL_x"], "method": "OSM"}
    fz = {"value": 56.6, "unit": "m", "tol": 2.6, "status": "estimated", "evidence": ["PXL_x"], "method": "観察票の比"}
    o = {"id": "c1", "type": "corridor", "evidence": ["PXL_x"], "polygon": poly, "floor_z": fz,
         "height": {"value": 4.37, "unit": "m", "tol": 1.3, "status": "estimated", "evidence": ["PXL_x"], "method": "1 段分"}}
    site = {"objects": {"schema": so.SCHEMA, "items": [o]}}
    errs, warns = so.validate_objects(site, {})
    assert errs == [] and warns == []
    meshes, skipped = so.object_meshes(o, {})
    zs = [p[2] for t in meshes[0]["tris"] for p in t]
    assert min(zs) == 56.6 and max(zs) == pytest.approx(60.97) and not skipped
    o["height"] = {"value": None, "unit": "m", "tol": None, "status": "unknown", "evidence": [], "method": "", "note": ""}
    errs, warns = so.validate_objects(site, {})
    assert errs == [] and any("撮影依頼" in w for w in warns)
    meshes, skipped = so.object_meshes(o, {})
    assert meshes and max(p[2] for t in meshes[0]["tris"] for p in t) == 56.6 and "height" in skipped[0]
