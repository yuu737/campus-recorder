"""部品の語彙 その 2（11/4 午後。本部棟で「語彙が無い」と見送られた 9 種）と、既存の不具合 2 件のテスト。

語彙ごとに：根拠の無い値が検証で落ちる／形（parts_geom）が正しい大きさ・個数を返す。立面に出ることは最後にまとめて。
"""
import copy
import json
import math
from pathlib import Path

import pytest

from recorder import drawing, parts_geom as pg, schema

FIX = Path(__file__).parent / "fixtures" / "parts_building.json"


def load():
    return json.loads(FIX.read_text(encoding="utf-8"))


def part(b, t, k=0):
    return [p for p in b["parts"] if p["type"] == t][k]


def faces(b):
    return {f["id"]: f for f in b["faces"]}


def m(v):
    return {"value": v, "status": "estimated", "evidence": [], "method": "テスト"}


def errs_for(b, needle):
    return [e for e in schema.validate_building(b) if needle in e]


def names(prims, pre):
    return [p for p in prims if p["name"].startswith(pre)]


def test_fixture_with_vocab2_is_valid():
    assert schema.validate_building(load()) == []


# --- 1. ceiling_beams ---------------------------------------------------------------

def test_ceiling_beams_grid_and_validation():
    b = load()
    pt = part(b, "ceiling_beams")
    pr = pg.ceiling_beams(pt, faces(b)["S"])
    assert len(pr) == 6                                   # 5 m を 1.0 以下 → 5 区画 6 本（両端を含む）
    mid = pr[2]
    assert (mid["z0"], mid["z1"]) == pytest.approx((3.2, 3.5))
    assert min(q[1] for q in mid["pts"]) == pytest.approx(-1.2)      # side "in"：建物の中へ奥行き 1.2
    g = pg.ceiling_beams({**pt, "direction": "grid", "pitch_n": m(0.6)}, faces(b)["S"])
    assert len(names(g, "beam_n")) == 3                   # 奥へ 1.2 を 0.6 以下 → 3 本
    b2 = load()
    part(b2, "ceiling_beams")["pitch"] = {"value": 1.0, "status": "measured", "evidence": []}
    assert any("pitch" in e and "根拠" in e for e in errs_for(b2, "ceiling_beams"))
    b3 = load()
    part(b3, "ceiling_beams")["direction"] = "diagonal"
    assert any("direction" in e for e in errs_for(b3, "ceiling_beams"))


# --- 2. louver ----------------------------------------------------------------------

def test_louver_blades_and_angle():
    b = load()
    h = pg.louver(part(b, "louver"), faces(b)["W"])
    blades = names(h, "blade")
    assert len(blades) == round(7.0 / 0.3)                # 7 m を間隔 0.3 → 23 枚（区画の中心）
    sec = blades[0]["pts"]                                # 横羽根は n-z の断面、30° 傾く → 外の縁が下がる
    outer = max(sec, key=lambda q: q[0])
    inner = min(sec, key=lambda q: q[0])
    assert outer[1] < inner[1]
    assert blades[0]["a0"] == pytest.approx(2.0) and blades[0]["a1"] == pytest.approx(8.0)
    v = pg.louver(part(b, "louver", 1), faces(b)["W"])
    assert len(names(v, "blade")) == round(2.5 / 0.4) and names(v, "blade")[0]["k"] == "prism"
    b2 = load()
    part(b2, "louver")["angle"] = 30
    assert any("angle" in e and "裸の数値" in e for e in errs_for(b2, "louver"))


# --- 3. planter ---------------------------------------------------------------------

def test_planter_face_and_world():
    b = load()
    pr = pg.planter(part(b, "planter"), faces(b)["S"])
    soil = names(pr, "soil")[0]
    assert soil["mat"] == "leaf" and soil["z1"] == pytest.approx(0.45)      # 縁（0.5）より 0.05 低い
    assert pg.area(soil["pts"]) == pytest.approx((5.0 - 0.3) * (1.2 - 0.3))  # 縁の厚さ 0.15 を内側へ
    rims = names(pr, "rim")
    assert len(rims) == 4 and sum(pg.volume(r) for r in rims) == pytest.approx((5.0 * 1.2 - 4.7 * 0.9) * 0.5)
    w = pg.part_prims(part(b, "planter", 1), faces(b))
    assert w[0] == "world"
    xs = [q[0] for q in names(w[2], "rim")[0]["pts"]]
    assert 22.0 < min(xs) and max(xs) < 26.0
    b2 = load()
    del part(b2, "planter", 1)["x"]
    assert any("x＋y" in e for e in errs_for(b2, "planter"))


# --- 4. nameplate -------------------------------------------------------------------

def test_nameplate_face_and_world():
    b = load()
    fr, _c, pr = pg.part_prims(part(b, "nameplate"), faces(b))
    assert fr == "world" and pg.volume(pr[0]) == pytest.approx(1.8 * 0.25 * 0.9)
    fr2, _c, pr2 = pg.part_prims(part(b, "nameplate", 1), faces(b))
    assert fr2 == "S" and (pr2[0]["z0"], pr2[0]["z1"]) == pytest.approx((2.2, 2.6))
    b2 = load()
    part(b2, "nameplate")["z0"] = 0.0
    assert any("z0" in e for e in errs_for(b2, "nameplate"))


# --- 5. patch -----------------------------------------------------------------------

def test_patch_thin_plate_off_wall():
    b = load()
    pr = pg.patch(part(b, "patch"), faces(b)["N"])
    n = [q[1] for q in pr[0]["pts"]]
    assert (min(n), max(n)) == pytest.approx((0.005, 0.010))            # 壁から 5 mm 浮かせる
    assert pr[0]["mat"] == "asset:concrete_dark"
    b2 = load()
    del part(b2, "patch")["material"]
    assert any("material" in e for e in errs_for(b2, "patch"))
    b3 = load()
    part(b3, "patch")["material"] = "asset:no_such_texture"
    assert any("no_such_texture" in e for e in schema.validate_building(b3))


# --- 6. vestibule -------------------------------------------------------------------

def test_vestibule_glass_box():
    b = load()
    pr = pg.vestibule(part(b, "vestibule"))
    assert len(names(pr, "glass")) == 3                   # 4 辺のうち壁に付く 1 辺（wall_edges）は張らない
    roof = names(pr, "roof")[0]
    assert roof["z0"] == pytest.approx(2.6) and pg.area(roof["pts"]) == pytest.approx(2.7 * 3.2)   # 張り出し 0.1
    # 横の格子：上下の枠＋0.7 ごと（0.7, 1.4, 2.1）→ 1 辺に 5 本
    assert len(names(pr, "transom0_")) == 5
    b2 = load()
    part(b2, "vestibule")["polygon"] = [[0, 0], [1, 0]]
    assert any("polygon" in e for e in errs_for(b2, "vestibule"))


# --- 7. pergola ---------------------------------------------------------------------

def test_pergola_posts_and_rafters():
    b = load()
    pr = pg.pergola(part(b, "pergola"))
    posts = names(pr, "post")
    assert len(posts) == 10                               # 6×8 の外周を 3 m 以下 → 角 4＋6 m の辺の中 1×2＋8 m の辺の中 2×2
    rafters = names(pr, "rafter")
    assert len(rafters) == round(8.0 / 0.6)               # 最初の辺（東西）に平行、南北 8 m を 0.6 ごと
    assert all(r["z1"] == pytest.approx(3.0) for r in rafters)
    assert pg.area(rafters[0]["pts"]) == pytest.approx(6.0 * 0.12, rel=1e-6)   # 多角形で切った長さ 6 m


# --- 8. bridge ----------------------------------------------------------------------

def test_bridge_slope_and_walls():
    b = load()
    fr, _c, pr = pg.part_prims(part(b, "bridge"), faces(b))
    assert fr == {"from": [20.0, 10.5], "to": [32.0, 10.5]}
    slab = names(pr, "slab")[0]
    assert slab["k"] == "ext_a" and slab["pts"][2] == pytest.approx((12.0, 4.6))      # 終点の床 4.6（斜め）
    assert len(names(pr, "glass")) == 2 and names(pr, "roof")
    ax = pg.frame_axes(fr)
    xs = [v[0] for v in pg.prim_vertices(slab, ax)]
    assert (min(xs), max(xs)) == pytest.approx((20.0, 32.0))
    _f, _c, pr2 = pg.part_prims({**part(b, "bridge"), "walls": "none"}, faces(b))
    assert [p["name"] for p in pr2] == ["slab"]
    b2 = load()
    part(b2, "bridge")["walls"] = "brick"
    assert any("walls" in e for e in errs_for(b2, "bridge"))


# --- 9. curved_glass ----------------------------------------------------------------

def test_curved_glass_arc():
    b = load()
    pr = pg.curved_glass(part(b, "curved_glass"), faces(b)["N"])
    gl = names(pr, "glass")
    assert len(gl) == 24
    R, h = 2.5, 2.0
    sag = R - math.sqrt(R * R - h * h)                    # ふくらみ 1.0 m
    assert max(q[1] for g in gl for q in g["pts"]) == pytest.approx(sag, abs=0.01)
    ends = gl[0]["pts"][0], gl[-1]["pts"][1]
    assert ends[0] == pytest.approx((15.0, 0.0), abs=1e-6) and ends[1] == pytest.approx((19.0, 0.0), abs=1e-6)
    assert names(pr, "interior") and names(pr, "cap_top")
    b2 = load()
    part(b2, "curved_glass")["radius"] = m(1.5)          # 弦の半分 2.0 より小さい
    assert any("半径" in e for e in errs_for(b2, "curved_glass"))
    assert pg.curved_glass(part(b2, "curved_glass"), faces(b2)["N"]) == []


# --- 既存の不具合 ----------------------------------------------------------------------

def test_stair_landing_len_from_to_and_end_parapet():
    b = load()
    pt = copy.deepcopy(part(b, "stair_switchback"))
    del pt["landing_len"]
    pt["landing_len_from"], pt["landing_len_to"] = m(3.0), m(2.0)
    by = {p["name"]: p for p in pg.stair_switchback(pt, faces(b)["N"])}
    a = [q[0] for q in by["flight0"]["pts"]]
    assert (min(a), max(a)) == pytest.approx((4.0 + 3.0, 4.0 + 10.0 - 2.0))
    l1 = [q[0] for q in by["landing1"]["pts"]]          # 2 本目の踊り場は from 側：長さ 3.0
    assert max(l1) - min(l1) == pytest.approx(3.0)
    pt["end_parapet"] = ["to"]                           # from 側は隣の床へ続く（腰壁を立てない）
    by2 = {p["name"]: p for p in pg.stair_switchback(pt, faces(b)["N"])}
    assert "landing1_end" not in by2 and "landing0_end" in by2
    b2 = load()
    del part(b2, "stair_switchback")["landing_len"]
    part(b2, "stair_switchback")["landing_len_from"] = m(3.0)
    assert any("landing_len_to" in e for e in errs_for(b2, "stair_switchback"))


def test_spandrel_in_recess():
    sp = {"height": m(0.6), "depth": m(0.0), "in_recess": True}
    p = pg.spandrel(sp, s=5.0, wsub=3.1, zb=9.2, base=0.0, rec=0.3)
    n = [q[1] for q in p["pts"]]
    assert (min(n), max(n)) == pytest.approx((-0.3, -0.28))           # ガラスと同じ奥から手前へ
    assert pg.spandrel_cut_bottom(sp, 9.2) == pytest.approx(8.6)       # 彫り込みはパネルの下端まで
    assert pg.spandrel_cut_bottom({"height": m(0.6)}, 9.2) == pytest.approx(9.2)


# --- 図面 -------------------------------------------------------------------------

def test_drawing_shows_vocab2():
    b = load()
    s = drawing.elevation_svg(b, "S")
    for t in ("ceiling_beams", "planter", "nameplate"):
        assert f'data-part="{t}"' in s, t
    n = drawing.elevation_svg(b, "N")
    assert 'data-part="patch"' in n and 'data-part="curved_glass"' in n
    # patch（壁の 5 mm 前）は外階段より先に（奥に）描く
    assert n.index('data-part="patch"') < n.index('data-part="stair_switchback"')
    e = drawing.elevation_svg(b, "E")
    assert 'data-part="vestibule"' in e and 'data-part="bridge"' in e
    w = drawing.elevation_svg(b, "W")
    assert 'data-part="louver"' in w and 'data-part="pergola"' in w
    plan = drawing.plan_svg(b)
    for t in ("vestibule", "pergola", "bridge", "planter", "nameplate"):
        assert f'data-part="{t}"' in plan, t
