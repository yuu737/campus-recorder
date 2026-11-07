"""内装の語彙（interior。決定 D-0002）のテスト。

検証の合否・平面図の要素数・形の計算（柱の位置・くり抜きの多角形と体積）。
最後に Blender で小さな棟をくり抜き、体積が見積りどおりか確かめる（BLENDER が無ければ飛ばす）。
"""
import copy
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from recorder import assets, drawing, interior as I, schema

FIX = Path(__file__).parent / "fixtures" / "interior_building.json"
FIX_CAT = Path(__file__).parent / "fixtures" / "assets_catalog.json"


@pytest.fixture(autouse=True)
def _own_decisions(tmp_path, monkeypatch):
    """本物の data/decisions.json を読まない（11/8 F5）。

    疑似本番の --zero で決定の台帳が作り直され、D-0002 の番号が別の決定（机の asset、deny）に使い回された。
    このテストは内装の語彙の検証・形の計算を確かめる物で、台帳の今の中身は確かめない。語彙の台帳（data/vocab.json）
    の写しと、そこに載った決定 id を全部 approve にした決定の台帳を一時フォルダに置いて差し替える。"""
    from recorder import decisions

    vocab = decisions.load_vocab()
    ids = sorted({e["decision"] for e in vocab.get("types", []) if e.get("decision")})
    (tmp_path / "vocab.json").write_text(json.dumps(vocab, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "decisions.json").write_text(json.dumps({
        "schema": decisions.SCHEMA,
        "decisions": [{"id": i, "kind": "vocab", "target": "test", "decision": "approve", "by": "test", "at": "2026-11-08",
                       "why": "テスト用の台帳"} for i in ids]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(decisions, "DECISIONS", tmp_path / "decisions.json")
    monkeypatch.setattr(decisions, "VOCAB", tmp_path / "vocab.json")


def load() -> dict:
    return schema.load_building(FIX)


def room(b):
    return b["interior"]["rooms"][0]


def errs_of(b):
    return [e for e in schema.validate_building(b) if e.startswith("interior")]


# --- 検証 ----------------------------------------------------------------------------

def test_fixture_is_valid():
    assert schema.validate_building(load()) == []


def test_building_without_interior_is_untouched():
    b = load()
    del b["interior"]
    assert I.validate_building_hook(b) == []


def test_polygon_outside_footprint_fails():
    b = load()
    room(b)["polygon"]["points"][1] = [25.0, 0.0]
    e = errs_of(b)
    assert any("footprint" in x and "外" in x for x in e), e


def test_bare_polygon_without_status_fails():
    b = load()
    room(b)["polygon"] = [[13, 0], [20, 0], [20, 12], [13, 12]]
    assert any("根拠の無い座標" in x for x in errs_of(b))


def test_missing_face_fails():
    b = load()
    room(b)["glass_walls"][0]["face"] = "X"
    room(b)["doors"][0]["face"] = "Y"
    e = errs_of(b)
    assert any("face=X" in x for x in e) and any("face=Y" in x for x in e), e


def test_asset_not_in_catalog_fails(monkeypatch):
    monkeypatch.setattr(assets, "CATALOG", FIX_CAT)          # 見本の台帳（box_unit だけ）
    b = load()
    room(b)["parts"][2]["asset"] = "box_unit"
    assert errs_of(b) == []
    room(b)["parts"][2]["asset"] = "no_such_asset"
    assert any("no_such_asset" in x and "台帳" in x for x in errs_of(b))


def test_measured_without_evidence_fails():
    b = load()
    room(b)["ceiling_h"]["evidence"] = []
    assert any("ceiling_h" in x and "根拠" in x for x in errs_of(b))
    b = load()
    room(b)["polygon"].update(status="measured", evidence=[])
    assert any("polygon" in x and "根拠" in x for x in errs_of(b))


def test_bare_number_and_unknown_with_value_fail():
    b = load()
    room(b)["floor_z"] = 0.15
    room(b)["columns"]["grid"]["size"] = {"value": 0.5, "status": "unknown", "evidence": [], "method": ""}
    e = errs_of(b)
    assert any("floor_z" in x and "裸" in x for x in e)
    assert any("size" in x and "unknown なのに value" in x for x in e)


def test_overlapping_rooms_on_same_floor_fail_but_neighbours_pass():
    b = load()
    r2 = copy.deepcopy(room(b))
    r2.update(id="next", glass_walls=[], doors=[], parts=[], columns=None)
    del r2["columns"]
    r2["polygon"]["points"] = [[10, 0], [13, 0], [13, 12], [10, 12]]       # 辺を共有して隣り合う
    b["interior"]["rooms"].append(r2)
    assert errs_of(b) == []
    r2["polygon"]["points"] = [[10, 0], [15, 0], [15, 12], [10, 12]]       # 重なる
    assert any("重なっています" in x for x in errs_of(b))
    r2["floor"] = 2                                                         # 別の階なら重なってよい
    assert not any("重なっています" in x for x in errs_of(b))


def test_column_outside_room_and_bad_door_kind_fail():
    b = load()
    room(b)["columns"]["grid"]["nx"]["value"] = 3                           # 3 列目 x=22 は部屋の外
    room(b)["doors"][0]["kind"] = "revolving"
    e = errs_of(b)
    assert any("柱" in x and "部屋の外" in x for x in e)
    assert any("kind" in x for x in e)
    room(b)["columns"]["omit"] = [[2, 0], [2, 1], [2, 2]]
    room(b)["doors"][0]["kind"] = "swing"
    assert errs_of(b) == []


def test_glass_wall_on_a_face_away_from_room_fails():
    b = load()
    room(b)["glass_walls"][0].update(face="W")          # 西面（x=0）は部屋（x 13〜20）に接していない
    room(b)["glass_walls"][0]["from"]["value"] = 2.0
    room(b)["glass_walls"][0]["to"]["value"] = 8.0
    assert any("接していません" in x for x in errs_of(b))


# --- 形の計算 -------------------------------------------------------------------------

def test_cavity_keeps_exterior_wall_thickness():
    b = load()
    cav = I.cavity_polygon(room(b), b)
    xs, ys = sorted({round(p[0], 3) for p in cav}), sorted({round(p[1], 3) for p in cav})
    # 南・東・北は外壁の線に沿うので 0.25 m 内側。西（x=13）は部屋の境で寄せない
    assert xs == [13.0, 19.75] and ys == [0.25, 11.75]
    room(b)["wall_t"] = {"value": 0.4, "status": "estimated", "evidence": [], "method": "テスト"}
    cav = I.cavity_polygon(room(b), b)
    assert sorted({round(p[0], 3) for p in cav}) == [13.0, 19.6]


def test_cut_volume_estimate():
    b = load()
    # (19.75−13) × (11.75−0.25) × 天井高 3.2
    assert I.cut_volume(room(b), b) == pytest.approx(6.75 * 11.5 * 3.2)


def test_column_positions_and_omit():
    b = load()
    cols = I.column_positions(room(b), b)
    assert [(c["x"], c["y"]) for c in cols] == [(15.0, 3.0), (15.0, 6.5), (15.0, 10.0), (18.5, 3.0), (18.5, 6.5), (18.5, 10.0)]
    room(b)["columns"]["omit"] = [[1, 1]]
    assert (18.5, 6.5) not in [(c["x"], c["y"]) for c in I.column_positions(room(b), b)]
    room(b)["columns"]["grid"]["dx"] = {"value": None, "status": "unknown", "evidence": [], "method": ""}
    assert I.column_positions(room(b), b) == []           # 間隔が分からなければ立てない（決めつけない）


def test_grid_follows_axis_face():
    b = load()
    b["faces"][0]["from"], b["faces"][0]["to"] = [0, 0], [0, 10]                # 面 S を北向きにしたら x 軸が北
    cols = I.column_positions(room(b), b)
    assert (cols[3]["x"], cols[3]["y"]) == pytest.approx((15.0, 6.5))


def test_mullions_doors_and_parts():
    b = load()
    assert I.mullions(room(b)["glass_walls"][0], b) == pytest.approx([14.0, 15.35, 16.7, 18.05, 19.4])
    assert I.door_span(room(b)["doors"][0], b) == pytest.approx((15.8, 17.6, 2.2))
    spots = I.part_spots(room(b)["parts"][0])
    assert spots == [(16.75, 4.75, 0.0)]
    pt = dict(room(b)["parts"][0], count=3, pitch={"value": 2.0, "status": "estimated", "evidence": [], "method": "t"})
    assert [round(s[0], 3) for s in I.part_spots(pt)] == [16.75, 18.75, 20.75]
    assert I.part_spots(dict(pt, status="unknown")) == []


def test_room_state_and_evidence():
    b = load()
    assert I.room_state(room(b)) == "estimated"
    assert I.room_evidence(room(b)) == ["T001", "T002"]
    room(b)["polygon"]["status"] = "unknown"
    assert I.room_state(room(b)) == "unknown"


# --- 平面図 ---------------------------------------------------------------------------

def test_plan_per_floor_and_elements():
    b = load()
    out = drawing.all_drawings(b)
    assert "plan_1f" in out and "plan_2f" not in out
    svg = out["plan_1f"]
    kinds = re.findall(r'data-kind="([a-z_]+)"', svg)
    count = {k: kinds.count(k) for k in set(kinds)}
    assert count["room"] == 1
    assert count["column"] == 6
    assert count["glass"] == 2                 # 細い二重線
    assert count["mullion"] == 5
    assert count["door"] == 2                  # 自動の引き戸は 2 枚の弧
    assert count["part"] == 3
    assert 'width="' in svg and "mm" in svg.split(">")[0]      # 1:200 の印刷寸法
    assert "1:200" in svg
    # 推定の部屋の輪郭は琥珀の破線（立面と同じ規則）
    assert re.search(r'data-kind="room"[^>]*stroke="#b07a10" stroke-dasharray="6 4"', svg)


def test_elevations_unchanged_by_interior():
    b = load()
    a = drawing.all_drawings(b)
    b2 = copy.deepcopy(b)
    del b2["interior"]
    a2 = drawing.all_drawings(b2)
    for k in a2:
        assert a[k] == a2[k]


# --- Blender ----------------------------------------------------------------------------

BLENDER = os.environ.get("BLENDER")
needs_blender = pytest.mark.skipif(not BLENDER or not Path(BLENDER).exists(), reason="Blender 5.2 が無い")
HERE = Path(__file__).resolve().parent.parent / "recorder"

INNER = r'''
import sys, json, bpy
sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import blender_build as bb
src = sys.argv[-1]
b = json.loads(open(src, encoding="utf-8").read())
bpy.ops.wm.read_factory_settings(use_empty=True)
col = bpy.data.collections.new(b["id"])
bpy.context.scene.collection.children.link(col)
info = bb.build_building(b, col)
names = sorted(o.name for o in bpy.data.objects)
print("RESULT", json.dumps({"volume": bb.mesh_volume(info["body"]), "names": names,
                            "room_polys": len(info["room_polys"]),
                            "rooms": [o.get("room") for o in bpy.data.objects if o.get("room")]}))
'''


def _m(v):
    return {"value": v, "status": "estimated", "evidence": [], "method": "テスト"}


@needs_blender
def test_blender_cuts_room_and_adds_columns(tmp_path: Path):
    sq = [[0, 0], [10, 0], [10, 8], [0, 8]]
    b = {"id": "t", "name": "t", "footprint": {"polygon": sq, "status": "estimated", "method": "t"}, "parapet_top": _m(8.0),
         "faces": [{"id": "S", "from": [0, 0], "to": [10, 0], "length": _m(10.0)},
                   {"id": "E", "from": [10, 0], "to": [10, 8], "length": _m(8.0)},
                   {"id": "N", "from": [10, 8], "to": [0, 8], "length": _m(10.0)},
                   {"id": "W", "from": [0, 8], "to": [0, 0], "length": _m(8.0)}],
         "interior": {"rooms": [{
             "id": "r", "name": "部屋", "floor": 1,
             "polygon": {"points": sq, "status": "estimated", "evidence": [], "method": "t"},
             "floor_z": _m(0.0), "ceiling_h": _m(3.0), "wall_t": _m(0.3),
             "columns": {"grid": {"origin": _m([3.0, 3.0]), "dx": _m(4.0), "dy": _m(2.0), "nx": _m(2), "ny": _m(2), "size": _m(0.4)}},
             "glass_walls": [{"face": "S", "from": _m(2.0), "to": _m(8.0), "mullion_pitch": _m(1.5)}],
             "doors": [], "parts": [], "lights": {"grid": True}}]}}
    src = tmp_path / "t.json"
    src.write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
    script = tmp_path / "inner.py"
    script.write_text(INNER, encoding="utf-8")
    r = subprocess.run([BLENDER, "-b", "-P", str(script), "--", str(HERE), str(src)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")), None)
    assert line, r.stdout[-3000:] + r.stderr[-2000:]
    res = json.loads(line[len("RESULT "):])
    cavity = (10 - 0.6) * (8 - 0.6) * 3.0             # 外壁 0.3 m を残す
    glass_open = 6.0 * 0.3 * 3.0                       # 南の外壁のガラスの区間
    assert res["volume"] == pytest.approx(10 * 8 * 8 - cavity - glass_open, abs=0.5)
    cols = [n for n in res["names"] if n.startswith("room_r_column_")]
    assert len(cols) == 4
    assert {"room_r_floor", "room_r_ceiling"} <= set(res["names"])
    assert any(n.startswith("room_r_glass_") for n in res["names"])
    assert not any(n.startswith("cut_room") for n in res["names"])        # 彫る箱は消える
    assert res["room_polys"] >= 6                                          # 内壁 4 面・床・天井
    assert set(res["rooms"]) == {"r"}
