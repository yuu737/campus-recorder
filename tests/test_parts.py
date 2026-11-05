"""部品の新しい語彙（11/4。本部棟の観察係の差分表から）のテスト。

語彙ごとに：検証が通る／根拠の無い measured・裸の数が落ちる／形（parts_geom）が正しい大きさを返す。
最後に Blender で見本棟を組み、玄関のくり抜きが体積どおりか確かめる（BLENDER が無ければ飛ばす）。
"""
import copy
import json
import math
import os
import subprocess
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


def test_fixture_with_all_new_parts_is_valid():
    assert schema.validate_building(load()) == []


# --- 検証：根拠の無い値は落ちる -------------------------------------------------

def test_stair_measured_without_evidence_is_rejected():
    b = load()
    part(b, "stair_switchback")["width"] = {"value": 10.0, "status": "measured", "evidence": []}
    assert any("width" in e and "根拠" in e for e in errs_for(b, "stair_switchback"))
    b = load()
    del part(b, "stair_switchback")["floors"]
    assert any("踊り場の高さ" in e for e in errs_for(b, "stair_switchback"))


def test_porch_bare_number_is_rejected():
    b = load()
    part(b, "porch")["height"] = 3.5
    assert any("height" in e and "裸の数値" in e for e in errs_for(b, "porch"))
    b = load()
    part(b, "porch")["glass"]["mullion_pitch"]["status"] = "measured"      # 根拠（evidence）が空
    assert any("mullion_pitch" in e for e in errs_for(b, "porch"))


def test_steps_curved_needs_radius_and_evidence():
    b = load()
    del part(b, "steps", 1)["radius"]
    assert any("radius" in e for e in errs_for(b, "steps"))
    b = load()
    part(b, "steps")["rise"] = {"value": 0.15, "status": "measured", "evidence": []}
    assert any("rise" in e and "根拠" in e for e in errs_for(b, "steps"))


def test_spandrel_measured_without_evidence_is_rejected():
    b = load()
    b["faces"][0]["windows"][0]["spandrel"]["height"] = {"value": 0.6, "status": "measured", "evidence": []}
    assert any("spandrel.height" in e for e in schema.validate_building(b))


def test_rail_bare_number_is_rejected():
    b = load()
    b["faces"][0]["bands"][0]["rail"]["post_pitch"] = 1.5
    assert any("bands[0].rail.post_pitch" in e for e in schema.validate_building(b))
    b = load()
    part(b, "rail")["z"] = 3.0
    assert any("rail" in e and ".z" in e for e in schema.validate_building(b))


def test_column_bare_number_is_rejected_but_xy_are_coordinates():
    b = load()
    part(b, "column")["z1"] = 4.0
    assert any("z1" in e for e in errs_for(b, "column"))
    assert errs_for(load(), "column") == []          # x, y は座標なので裸の数でよい


def test_rooftop_units_need_lean_correction_with_evidence():
    b = load()
    part(b, "rooftop_units")["lean_correction_m"] = [-0.4, -1.0]   # 裸の [dx,dy]
    assert any("lean_correction_m" in e for e in errs_for(b, "rooftop_units"))
    b = load()
    part(b, "rooftop_units")["units"][0]["h"] = {"value": 1.8, "status": "measured", "evidence": []}
    assert any("units[0].h" in e for e in errs_for(b, "rooftop_units"))


def test_ac_units_z0_bare_number_is_rejected():
    b = load()
    part(b, "ac_units")["z0"] = 2.0
    assert any("ac_units" in e and "z0" in e for e in schema.validate_building(b))


# --- 形の大きさ -------------------------------------------------------------------

def test_stair_switchback_geometry():
    b = load()
    pt = part(b, "stair_switchback")
    prims = pg.stair_switchback(pt, faces(b)["N"])
    by = {p["name"]: p for p in prims}
    # 3 層・中間の踊り場あり → 踊り場 6、斜めの段 6
    assert len([n for n in by if n.startswith("landing") and n[7:].isdigit()]) == 6
    flights = [by[f"flight{i}"] for i in range(6)]
    # 斜めの段は傾いた板：長さ (10 − 2×2) × 板厚 0.25 × 通路の幅 1.5
    for f in flights:
        assert pg.volume(f) == pytest.approx(6.0 * 0.25 * 1.5, rel=1e-6)
    # 1 本目は地面（0）から 2.0 m の踊り場へ、to 側（along の大きい方）へ上がる
    zs = {a: z for a, z in flights[0]["pts"]}
    assert flights[0]["pts"][3] == pytest.approx((6.0, 0.0)) and flights[0]["pts"][2] == pytest.approx((12.0, 2.0))
    assert flights[0]["n0"] == pytest.approx(1.5) and flights[1]["n1"] == pytest.approx(1.5)   # 外側と内側を交互に
    # 最上の踊り場（z=12）の上に出口の箱 2.5 m、その上にかまぼこ庇（高さ 0.9）
    eb = by["exit_box"]
    assert (eb["z0"], eb["z1"]) == pytest.approx((12.0, 14.5))
    zc = [q[1] for q in by["canopy"]["pts"]]
    assert max(zc) == pytest.approx(14.5 + 0.9) and min(zc) == pytest.approx(14.5)
    # 腰壁は外側の通路の斜めの段だけ（3 本）。ピロティの柱は 2 本、上は踊り場か段の裏
    assert len([n for n in by if n.startswith("flight") and n.endswith("_parapet")]) == 3
    pil = [p for n, p in by.items() if n.startswith("pilotis")]
    assert len(pil) == 2 and all(p["z0"] == 0.0 and p["z1"] > 0.5 for p in pil)


def test_porch_cut_and_glass():
    b = load()
    cut, prims = pg.porch(part(b, "porch"), faces(b)["S"])
    a = [q[0] for q in cut["pts"]]
    n = [q[1] for q in cut["pts"]]
    assert (min(a), max(a)) == pytest.approx((7.5, 12.5))
    assert min(n) == pytest.approx(-3.0)                     # 壁の中へ 3 m
    assert cut["z1"] == pytest.approx(3.5)
    by = {p["name"]: p for p in prims}
    g = by["glass"]
    assert pg.volume(g) == pytest.approx((4.2 - 0.8) * (1.8 - 0.05) * 3.45, rel=1e-6)    # 奥の壁から 0.05 離す
    assert "interior" in by                                  # 奥の壁に室内の暗い板
    assert len([k for k in by if k.startswith("mullion") and k[7:].isdigit()]) == 5     # 3.4 m を 1.0 以下 → 4 区画 5 本
    assert "column0" in by and "column1" in by and "transom1" in by


def test_steps_straight_and_curved():
    b = load()
    pr = pg.steps(part(b, "steps"), faces(b)["S"])          # 2 段、L 字（両端も回る）
    assert len(pr) == 2
    assert pg.volume(pr[0]) == pytest.approx(5.0 * 0.9 * 0.15)
    assert pg.volume(pr[1]) == pytest.approx((5.0 - 0.9) * 0.45 * 0.15)
    assert pr[1]["z0"] == pytest.approx(0.15)
    cv = pg.steps(part(b, "steps", 1), faces(b)["S"])       # 5 段、半径 4 の円弧
    assert len(cv) == 5
    for k, p in enumerate(cv):
        nmax = max(q[1] for q in p["pts"])
        assert nmax == pytest.approx(3.0 - k * 0.6, abs=0.02)        # 中央が一番前（奥行き − 段×踏み面）
        assert pg.volume(p) < 6.0 * (3.0 - k * 0.6) * 0.16            # 角は円弧で削れる


def test_spandrel_rail_column_rooftop_ac():
    sp = pg.spandrel({"height": m(0.6), "depth": m(0.05)}, s=5.0, wsub=3.1, zb=5.2, base=0.0)
    assert (sp["z0"], sp["z1"]) == pytest.approx((4.6, 5.2))
    assert pg.volume(sp) == pytest.approx(3.1 * 0.05 * 0.6)
    posts = [p for p in pg.rail_prims("r", 0.0, 10.0, 5.0, 0.9, 1.5, 0.1) if "post" in p["name"]]
    assert len(posts) == 8                                          # 10 m を 1.5 以下 → 7 区画 8 本
    assert posts[0]["z1"] == pytest.approx(5.9)
    c = pg.column({"x": 1.0, "y": 2.0, "diameter": m(0.6), "z1": m(4.0)})
    assert pg.volume(c) == pytest.approx(math.pi * 0.09 * 4.0, rel=0.03)
    sq = pg.column({"x": 1.0, "y": 2.0, "diameter": m(0.5), "z1": m(3.0), "section": "square"})
    assert pg.volume(sq) == pytest.approx(0.75)
    b = load()
    ru = pg.rooftop_units(part(b, "rooftop_units"))
    xs = [q[0] for q in ru[0]["pts"]]
    ys = [q[1] for q in ru[0]["pts"]]
    assert (min(xs), min(ys)) == pytest.approx((6.0 - 1.5 - 0.4, 7.0 - 1.0 - 1.0))     # 倒れ込みの補正を足す
    assert (ru[0]["z0"], ru[0]["z1"]) == pytest.approx((13.0, 14.8))
    assert pg.ac_units_base({"z0": m(2.0)}, None) == pytest.approx(2.0)
    assert pg.ac_units_base({}, {"ground_offset": m(1.4)}) == pytest.approx(1.5)     # 地面の板の上


def test_unknown_size_places_nothing_and_reports():
    got = []

    def V(mm, default=None, what="", why=""):
        v = pg.plain(mm, default)
        if what and v is default:
            got.append(what)
        return v

    pt = {"type": "porch", "face": "S", "along": m(1.0), "width": {"value": None, "status": "unknown"},
          "depth": m(2.0), "height": m(3.0)}
    cut, prims = pg.porch(pt, {}, V)
    assert cut is None and prims == [] and any("幅" in w for w in got)


# --- 図面 -------------------------------------------------------------------------

def test_drawing_shows_new_parts():
    b = load()
    s = drawing.elevation_svg(b, "S")
    assert 'data-part="porch"' in s and 'data-part="steps"' in s and 'data-part="rail"' in s
    assert drawing.FILL_SPANDREL in s
    nsvg = drawing.elevation_svg(b, "N")
    assert 'data-part="stair_switchback"' in nsvg
    assert 'data-part="rail"' in drawing.elevation_svg(b, "E")
    plan = drawing.plan_svg(b)
    assert 'data-part="column"' in plan and 'data-part="rooftop_units"' in plan


# --- Blender ------------------------------------------------------------------------

BLENDER = os.environ.get("BLENDER")
HERE = Path(__file__).resolve().parent.parent / "recorder"
INNER = r'''
import sys, json, bpy
sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import blender_build as bb
b = json.loads(open(sys.argv[-1], encoding="utf-8").read())
bpy.ops.wm.read_factory_settings(use_empty=True)
col = bpy.data.collections.new(b["id"])
bpy.context.scene.collection.children.link(col)
info = bb.build_building(b, col)
print("RESULT", json.dumps({"volume": bb.mesh_volume(info["body"]), "names": sorted(o.name for o in bpy.data.objects),
                            "ac_z": min(v.co.z for o in bpy.data.objects if o.name.startswith("ac_units") for v in o.data.vertices)}))
'''


@pytest.mark.skipif(not BLENDER or not Path(BLENDER).exists(), reason="Blender 5.2 が無い")
def test_blender_builds_all_new_parts(tmp_path: Path):
    script = tmp_path / "inner.py"
    script.write_text(INNER, encoding="utf-8")
    r = subprocess.run([BLENDER, "-b", "-P", str(script), "--", str(HERE), str(FIX)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")), None)
    assert line, r.stdout[-3000:] + r.stderr[-2000:]
    res = json.loads(line[len("RESULT "):])
    # 本体 20×12×13 から、玄関のくり抜き 5×3×3.5 と窓 8 か所（3.1×0.3×2.0）、3 階は奥まりの中のパネル（0.6）の分も彫る
    # 組み立て部品（composite_21 ピロティ）の cut：x 16.6〜19.4 × y 9.6〜12（本体の中）× z 0〜3.2
    assert res["volume"] == pytest.approx(20 * 12 * 13 - 5 * 3 * 3.5 - 8 * 3.1 * 0.3 * 2.0 - 4 * 3.1 * 0.3 * 0.6
                                          - 2.8 * 2.4 * 3.2, abs=0.5)
    names = res["names"]
    for pre in ("stair_switchback_3_flight0", "stair_switchback_3_canopy", "porch_0_glass", "steps_2_step4",
                "spandrel_S_", "bandrail_S_", "rail_4_rail_bar", "column_6_column", "rooftop_units_7_unit1",
                "ceiling_beams_8_beam_a0", "louver_9_blade0", "louver_10_blade0", "planter_11_soil", "planter_12_rim0",
                "nameplate_13_plate", "nameplate_14_plate", "patch_15_patch", "vestibule_16_glass0", "pergola_17_rafter0",
                "bridge_18_slab", "curved_glass_19_glass23", "composite_20_slab", "composite_20_coping",
                "composite_20_round_planter_35", "composite_21_beam1", "composite_21_column"):
        assert any(n.startswith(pre) for n in names), pre
    assert res["ac_z"] == pytest.approx(2.0, abs=1e-4)          # 壁付けの室外機は z0 から
