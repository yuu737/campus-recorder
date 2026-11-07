"""街路の視点の立ち位置（recorder/street_view.py。blender_build.py の persp_street.png と web_cameras.street）のテスト。

手で作った小さな棟（20 m 角、高さ 10 m）と登録台帳・敷地 JSON で確かめる。本物のデータは読まない。
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from recorder import street_view as SV

ROOT = Path(__file__).resolve().parents[1]
GZ = 50.0


def _v(x):
    return {"value": x, "unit": "m", "status": "estimated", "evidence": ["P1"], "method": "試し", "note": ""}


def _building(L=20.0):
    pts = [[0, 0], [L, 0], [L, L], [0, L]]
    faces = [{"id": f"f{i}", "from": pts[i], "to": pts[(i + 1) % 4], "length": _v(L)} for i in range(4)]
    return {"id": "t", "campus": "c", "footprint": {"status": "estimated", "evidence": ["P1"], "polygon": pts},
            "ground_z": _v(GZ), "faces": faces}


def _reg(face, x, y, bid="t", photo="P"):
    # R = 単位行列、t = −c なら位置 −Rᵀt = c
    return {"photo": photo, "building": bid, "face": face,
            "camera": {"R_cv": np.eye(3).tolist(), "t_cv": [-x, -y, -1.5]}}


def _registry(entries):
    return {"entries": entries}


def flat(x, y):
    return GZ


def test_most_registered_face_and_distance():
    b = _building()
    # 東の面 f1（(20,0)→(20,20)、外向き +x）に 4 枚、南の面 f0 に 2 枚
    reg = _registry([_reg("f1", 30, 10, photo=f"E{i}") for i in range(4)] + [_reg("f0", 10, -10, photo=f"S{i}") for i in range(2)])
    r = SV.choose_standpoint(b, 10.0, registry=reg, ground_at=flat)
    assert r["face"] == "f1"
    sp = r["standpoint"]
    assert sp["registrations"] == 4 and sp["why_face"] == "登録が一番多い面"
    assert sp["distance_m"] == 38.0                 # max(38, 20×0.85, 10×0.9)
    assert np.allclose(r["eye_xy"], [20 + 38, 10])  # 真正面（横のずれ 0）
    assert np.allclose(r["target_xy"], [20, 10])


def test_distance_uses_length_and_height():
    b = _building(L=60.0)
    r = SV.choose_standpoint(b, 50.0, registry=None, ground_at=flat)
    assert math.isclose(r["standpoint"]["distance_m"], round(max(38, 60 * 0.85, 50 * 0.9), 2))


def test_too_few_registrations_falls_back_to_longest_face():
    b = _building()
    b["faces"][2]["from"], b["faces"][2]["to"] = [20, 20], [-5, 20]      # f2 を一番長く（25 m）
    reg = _registry([_reg("f1", 30, 10, photo=f"E{i}") for i in range(2)])   # 3 枚未満
    r = SV.choose_standpoint(b, 10.0, registry=reg, ground_at=flat)
    assert r["face"] == "f2"
    assert "一番長い面" in r["standpoint"]["why_face"]


def test_side_offset_clipped_to_third_of_length():
    b = _building()
    # f1 の中点 (20,10) から前 10 m・横 +30 m（面の長さの 1/3 = 6.67 m を超える）
    reg = _registry([_reg("f1", 30, 40, photo=f"E{i}") for i in range(3)])
    r = SV.choose_standpoint(b, 10.0, registry=reg, ground_at=flat)
    d = r["standpoint"]["direction"]
    assert math.isclose(d["side_at_standpoint_m"], 20 / 3, abs_tol=0.01)
    ex, ey = np.array(r["eye_xy"]) - np.array([20, 10])
    assert math.isclose(ey, 20 / 3, abs_tol=1e-6)                 # 立つ点の横のずれは長さの 1/3 まで
    assert math.isclose(math.hypot(ex, ey), 38.0, abs_tol=1e-6)   # 距離はそのまま


def test_side_offset_follows_median_direction_when_small():
    b = _building()
    # 前 30 m・横 −2 m：38 m 先では横 −2×38/√(30²+2²) ≈ −2.53 m（1/3 より小さいので切らない）
    reg = _registry([_reg("f1", 50, 8, photo=f"E{i}") for i in range(3)])
    r = SV.choose_standpoint(b, 10.0, registry=reg, ground_at=flat)
    ex, ey = np.array(r["eye_xy"]) - np.array([20, 10])
    assert math.isclose(ey, -2 * 38 / math.hypot(30, 2), abs_tol=1e-6)


def test_pose_entry_counts_for_largest_visible_face():
    b = _building()
    entries = []
    for i in range(3):
        e = _reg("(pose)", 30, 10, photo=f"P{i}")
        e["visible_faces"] = [{"face": "f0", "area_m2": 5.0}, {"face": "f1", "area_m2": 80.0}]
        entries.append(e)
    entries.append(_reg("f0", 10, -10, bid="other"))     # 他の棟の登録は数えない
    r = SV.choose_standpoint(b, 10.0, registry=_registry(entries), ground_at=flat)
    assert r["face"] == "f1" and r["standpoint"]["registrations"] == 3


def test_eye_height_from_dem():
    b = _building()
    r = SV.choose_standpoint(b, 10.0, registry=None, ground_at=lambda x, y: GZ - 3.0)
    assert math.isclose(r["eye_z_rel"], -3.0 + 1.6)
    assert r["standpoint"]["ground_elev_m"] == GZ - 3.0
    r = SV.choose_standpoint(b, 10.0, registry=None, ground_at=None)      # 5m 標高が無い時は ground_z + 1.6
    assert math.isclose(r["eye_z_rel"], 1.6)
    assert "仮の値" in r["standpoint"]["eye_z_rule"]


def _box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def test_standpoint_inside_other_building_is_shrunk():
    b = _building()
    reg = _registry([_reg("f1", 30, 10, photo=f"E{i}") for i in range(3)])
    # 38 m 先（x=58）に他の棟。x=52〜70 を塞ぐ
    other = {"id": "o", "footprint": {"polygon": _box(52, 0, 70, 20)}, "parapet_top": _v(5.0)}
    r = SV.choose_standpoint(b, 10.0, registry=reg, ground_at=flat, others=[other])
    sp = r["standpoint"]
    assert r["face"] == "f1"
    assert r["eye_xy"][0] < 52 - SV.CLEAR_M
    assert sp["shrunk"] and sp["shrunk"][0]["id"] == "o" and "avoided" in sp
    assert not SV.point_in_polygon(*r["eye_xy"], other["footprint"]["polygon"])


def test_blocked_face_switches_to_next():
    b = _building()
    reg = _registry([_reg("f1", 30, 10, photo=f"E{i}") for i in range(5)] + [_reg("f0", 10, -10, photo=f"S{i}") for i in range(3)])
    # f1 の前 3〜80 m を全部塞ぐ（敷地 JSON の棟と PLATEAU の箱）
    campus = {"buildings": [{"id": "big", "footprint_osm": [_box(23.5, -5, 100, 25)],
                             "reference": {"plateau_base_z": GZ, "plateau_height_m": 30.0}}]}
    r = SV.choose_standpoint(b, 10.0, registry=reg, ground_at=flat, campus_doc=campus)
    assert r["face"] == "f0"
    sp = r["standpoint"]
    assert sp["faces_tried"][0]["face"] == "f1"
    assert sp["why_face"] == "登録の多い順の次の面"


def test_tall_box_on_sight_line_rejects_point():
    b = _building()
    reg = _registry([_reg("f1", 30, 10, photo=f"E{i}") for i in range(3)])
    # 立つ点（x=58）の外、視線の上（x=35〜45）に高い PLATEAU の箱：立つ点は中でないが、中ほどへの視線が遮られる
    plateau = {"buildings": [{"gml_id": "tall", "footprint": _box(35, 0, 45, 20), "base_z": GZ, "height_m": 40.0}]}
    r = SV.choose_standpoint(b, 10.0, registry=reg, ground_at=flat, plateau_doc=plateau)
    assert r["face"] != "f1" or r["eye_xy"][0] < 35
    # 目の高さ（1.6 m）より低い箱（1 m）なら視線は越える
    plateau["buildings"][0]["height_m"] = 1.0
    r = SV.choose_standpoint(b, 10.0, registry=reg, ground_at=flat, plateau_doc=plateau)
    assert r["face"] == "f1" and math.isclose(r["eye_xy"][0], 58.0)


def test_own_plateau_parts_are_not_obstacles():
    b = _building()
    b["site_id"] = "t"
    campus = {"buildings": [{"id": "t", "plateau_parts": ["mine"], "footprint_osm": [_box(0, 0, 20, 20)]}]}
    # 自分の PLATEAU の箱（大半が自分の足元）は他の物に数えない
    plateau = {"buildings": [{"gml_id": "mine", "footprint": _box(0, 0, 20, 20), "base_z": GZ, "height_m": 10.0},
                             {"gml_id": "almost", "footprint": _box(1, 1, 21, 21), "base_z": GZ, "height_m": 10.0}]}
    own, obs = SV.obstacles(b, campus, plateau, [])
    assert [o["id"] for o in obs] == []


def test_blender_build_uses_street_view():
    src = (ROOT / "recorder" / "blender_build.py").read_text(encoding="utf-8")
    assert "street_view.choose_standpoint(" in src
    assert 'web_cams["street"]["standpoint"]' in src
    assert "mid + n0 * 38.0" not in src                  # 古い既定（一番長い面の 38 m 先、地形を見ない）は使わない
    sv = (ROOT / "recorder" / "street_view.py").read_text(encoding="utf-8")
    imports = {ln.split()[1].split(".")[0] for ln in sv.splitlines() if ln.startswith(("import ", "from "))}
    assert imports <= {"__future__", "json", "math", "pathlib", "numpy"}, imports   # Blender の中で読むので numpy と標準だけ


def test_side_variant_used_when_registration_direction_blocked():
    b = _building()
    reg = _registry([_reg("f1", 30, 10, photo=f"E{i}") for i in range(3)])
    # f1 の真正面に細長い低い物（y=9〜11、x=24〜70、高さ 1 m）：法線の上はどの距離でも足元の中。横にずらせば立てる
    plateau = {"buildings": [{"gml_id": "rail", "footprint": _box(24, 9, 70, 11), "base_z": GZ, "height_m": 1.0}]}
    r = SV.choose_standpoint(b, 10.0, registry=reg, ground_at=flat, plateau_doc=plateau)
    sp = r["standpoint"]
    assert r["face"] == "f1"
    assert sp["sight"]["visible"] >= SV.SIGHT_PASSES[0][1]
    assert abs(sp["direction"]["side_at_standpoint_m"]) > 0 and "横のずれ" in sp["avoided"]
    assert abs(sp["direction"]["side_at_standpoint_m"]) <= 20 / 3 + 0.01


def test_never_stands_inside_other_footprint_when_all_blocked():
    b = _building()
    reg = _registry([_reg("f1", 30, 10, photo=f"E{i}") for i in range(3)])
    # 棟のすぐ外（5〜15 m）を高い壁で囲む（立つ点は壁の外になり、どこからも見える割合が足りない）
    walls = [_box(-15, -15, 35, -5), _box(-15, 25, 35, 35), _box(-15, -5, -5, 25), _box(25, -5, 35, 25)]
    plateau = {"buildings": [{"gml_id": f"w{i}", "footprint": w, "base_z": GZ, "height_m": 60.0} for i, w in enumerate(walls)]}
    r = SV.choose_standpoint(b, 10.0, registry=reg, ground_at=flat, plateau_doc=plateau)
    for w in walls:
        assert not SV.point_in_polygon(*r["eye_xy"], w)
    assert "一番よく見えた所" in r["standpoint"]["why_face"]


def test_replaced_boxes_are_not_obstacles_but_corridor_is():
    """site link-box で置き換えた箱（replaced）とその OSM の輪郭は障害に数えず、渡り廊下（corridor）は上端付きで数える（11/8 F9）。"""
    b = _building()
    b["site_id"] = "t"
    campus = {"buildings": [{"id": "t", "plateau_parts": [], "footprint_osm": [_box(0, 0, 20, 20)]},
                            {"id": "osm-deck", "plateau_parts": ["deckbox"], "footprint_osm": [_box(40, 0, 60, 20)]},
                            {"id": "osm-other", "plateau_parts": ["other"], "footprint_osm": [_box(80, 0, 90, 10)]}],
              "plateau_links": [{"box": "deckbox", "kind": "replaced", "replaced_by": ["deck-1", "bridge-1"]}],
              "objects": {"items": [{"id": "cor-1", "type": "corridor", "polygon": _v(_box(25, 0, 35, 6)),
                                     "floor_z": _v(GZ + 6.0), "height": _v(4.0)}]}}
    plateau = {"buildings": [{"gml_id": "deckbox", "footprint": _box(40, 0, 60, 20), "base_z": GZ, "height_m": 24.0},
                             {"gml_id": "other", "footprint": _box(80, 0, 90, 10), "base_z": GZ, "height_m": 10.0}]}
    own, obs = SV.obstacles(b, campus, plateau, [])
    ids = {o["id"]: o for o in obs}
    assert "deckbox" not in ids and "osm-deck" not in ids
    assert "other" in ids and "osm-other" in ids
    assert ids["cor-1"]["top"] == GZ + 10.0


def test_load_inputs_finds_site_doc_by_site_id(tmp_path):
    """棟 JSON の campus が敷地 JSON の名前と違う（古い呼び名）時も、site_id を持つ敷地 JSON を読む。"""
    import json as _json
    (tmp_path / "data" / "site").mkdir(parents=True)
    (tmp_path / "data" / "buildings").mkdir(parents=True)
    (tmp_path / "data" / "site" / "new.json").write_text(_json.dumps({"campus": "new", "buildings": [{"id": "t"}, {"id": "u"}]}),
                                                          encoding="utf-8")
    b = {**_building(), "campus": "old-name", "site_id": "t"}
    (tmp_path / "data" / "buildings" / "u.json").write_text(_json.dumps({"id": "u", "campus": "new", "site_id": "u"}), encoding="utf-8")
    got = SV.load_inputs(b, tmp_path)
    assert got["campus_doc"]["campus"] == "new"
    assert [o["id"] for o in got["others"]] == ["u"]
