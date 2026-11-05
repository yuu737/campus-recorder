"""CC0 アセットの台帳（recorder/assets.py）と、棟 JSON の asset 部品の検証のテスト。ネットは使わない。"""
import copy
import json
import math
from pathlib import Path

import pytest

from recorder import assets, schema

FIX = Path(__file__).parent / "fixtures" / "assets_catalog.json"
PARTS = Path(__file__).parent / "fixtures" / "parts_building.json"


def cat():
    return json.loads(FIX.read_text(encoding="utf-8"))


# --- 台帳の検証 ------------------------------------------------------------------

def test_fixture_catalog_ok():
    assert assets.validate_catalog(cat()) == []


def test_real_catalog_ok_and_cc0_only():
    c = assets.load_catalog()
    assert assets.validate_catalog(c) == []
    assert {it["license"] for it in c["items"]} == {"CC0-1.0"}
    assert c["not_found"], "該当なしの物も台帳に書く"


@pytest.mark.parametrize("lic", ["CC-BY-4.0", "CC-BY-SA-4.0", None, "cc0"])
def test_non_cc0_is_rejected(lic):
    c = cat()
    c["items"][0]["license"] = lic
    errs = assets.validate_catalog(c)
    assert any("CC0" in e for e in errs)


def test_model_needs_size_and_safe_path():
    c = cat()
    del c["items"][0]["size_m"]
    c["items"][1]["file"] = "polyhaven/../../evil"
    errs = assets.validate_catalog(c)
    assert any("size_m" in e for e in errs)
    assert any("相対パス" in e for e in errs)


def test_texture_needs_tile_and_color():
    c = cat()
    del c["items"][1]["tile_m"]
    c["items"][1]["maps"].pop("color")
    errs = assets.validate_catalog(c)
    assert any("tile_m" in e for e in errs) and any("maps.color" in e for e in errs)


def test_duplicate_id():
    c = cat()
    c["items"].append(copy.deepcopy(c["items"][0]))
    assert any("重複" in e for e in assets.validate_catalog(c))


# --- resolve --------------------------------------------------------------------

def test_resolve(tmp_path):
    c = cat()
    p = tmp_path / "polyhaven" / "box_unit" / "box_unit_1k.gltf"
    with pytest.raises(FileNotFoundError):
        assets.resolve("box_unit", c, tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text("{}", encoding="utf-8")
    assert assets.resolve("box_unit", c, tmp_path) == p
    with pytest.raises(KeyError):
        assets.resolve("nope", c, tmp_path)


def test_resolve_refuses_non_cc0(tmp_path):
    c = cat()
    c["items"][0]["license"] = "CC-BY-4.0"
    with pytest.raises(ValueError):
        assets.resolve("box_unit", c, tmp_path, must_exist=False)


def test_texture_maps(tmp_path):
    m = assets.texture_maps("tile", cat(), tmp_path)
    assert m["color"] == tmp_path / "polyhaven" / "tile" / "tile_diff_1k.jpg"
    assert set(m) == {"color", "normal", "rough"}
    with pytest.raises(ValueError):
        assets.texture_maps("box_unit", cat(), tmp_path)


# --- 拡大率 ----------------------------------------------------------------------

def test_scale_exact():
    f, w = assets.scale_factors([0.8, 0.4, 1.0], [0.8, 0.4, 1.0])
    assert f == pytest.approx((1, 1, 1)) and w == []


def test_scale_per_axis():
    f, w = assets.scale_factors([0.8, 0.4, 1.0], [1.2, 0.5, 1.5])
    assert f == pytest.approx((1.5, 1.25, 1.5)) and w == []


def test_scale_partial_unknown_uses_geometric_mean():
    f, w = assets.scale_factors([0.8, 0.4, 1.0], [1.6, None, 0.5])
    assert f[0] == pytest.approx(2.0) and f[2] == pytest.approx(0.5)
    assert f[1] == pytest.approx(math.sqrt(2.0 * 0.5))
    assert any("unknown" in x for x in w)


def test_scale_all_unknown_keeps_asset_size():
    f, w = assets.scale_factors([0.8, 0.4, 1.0], [None, None, None])
    assert f == (1.0, 1.0, 1.0) and any("仮" in x for x in w)


def test_scale_warns_out_of_range():
    _, w = assets.scale_factors([0.7, 0.4, 3.87], [0.7, 0.4, 9.0])
    assert any("h=2.33" in x for x in w)
    _, w = assets.scale_factors([2.0, 2.0, 2.0], [0.9, 2.0, 2.0])
    assert any("w=0.45" in x for x in w)


# --- 置き場所 --------------------------------------------------------------------

def M(v, unit="m"):
    return {"value": v, "unit": unit, "tol": 0.1, "status": "measured", "evidence": ["F1"], "method": "試験", "note": ""}


def test_placements_on_face_with_count():
    face = {"id": "S", "from": [0.0, 0.0], "to": [10.0, 0.0]}   # 外向きは -y（南）
    pt = {"type": "asset", "asset": "box_unit", "face": "S", "along": M(2.0), "offset": M(0.5),
          "z0": M(0.1), "count": M(3, "個"), "pitch": M(1.5), "yaw": 0}
    spots, w = assets.placements(pt, face)
    assert w == []
    assert [(round(x, 3), round(y, 3), z) for x, y, z, _ in spots] == [(2.0, -0.5, 0.1), (3.5, -0.5, 0.1), (5.0, -0.5, 0.1)]
    assert spots[0][3] == pytest.approx(0.0)   # 正面（-y）がそのまま外を向く


def test_placements_east_face_turns_model():
    face = {"id": "E", "from": [0.0, 0.0], "to": [0.0, 10.0], "ground_offset": M(-0.6)}   # 外向きは +x（東）
    spots, _ = assets.placements({"face": "E", "along": M(4.0), "offset": M(1.0)}, face)
    x, y, z, yaw = spots[0]
    assert (round(x, 3), round(y, 3), z) == (1.0, 4.0, -0.6)    # z0 が無ければ面の前の地面
    # yaw だけ回した模型の正面 (0,-1) が外向き (1,0) になる
    assert (math.sin(yaw), -math.cos(yaw)) == pytest.approx((1.0, 0.0))


def test_placements_unknown_along_places_nothing():
    face = {"id": "S", "from": [0.0, 0.0], "to": [10.0, 0.0]}
    unk = {"value": None, "status": "unknown", "evidence": []}
    spots, w = assets.placements({"face": "S", "along": unk}, face)
    assert spots == [] and any("unknown" in x for x in w)


def test_placements_world_xy():
    spots, _ = assets.placements({"x": 3.0, "y": -2.0, "yaw": 90}, None)
    assert spots[0][:3] == (3.0, -2.0, 0.0) and spots[0][3] == pytest.approx(math.pi / 2)


# --- glTF の外形 -----------------------------------------------------------------

def test_gltf_size_with_nodes(tmp_path):
    g = {"scene": 0, "scenes": [{"nodes": [0, 1]}],
         "nodes": [{"name": "a", "mesh": 0, "translation": [-0.5, 0, 0]},
                   {"name": "b", "mesh": 0, "translation": [0.5, 0, 0], "scale": [1, 2, 1]}],
         "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
         "accessors": [{"min": [-0.4, 0.0, -0.2], "max": [0.4, 0.9, 0.2]}]}
    p = tmp_path / "m.gltf"
    p.write_text(json.dumps(g), encoding="utf-8")
    assert assets.gltf_size(p) == [1.8, 0.4, 1.8]      # w=x, d=z, h=y
    assert assets.gltf_size(p, "a") == [0.8, 0.4, 0.9]


# --- 取得（ネットなし）-------------------------------------------------------------

def test_fetch_skips_unchanged(tmp_path):
    c = cat()
    c["items"] = c["items"][:1]
    c["items"][0]["decision"] = "D-0001"   # 進行役の決定が無い項目は fetch が拒否する（recorder/decisions.py）
    p = tmp_path / c["items"][0]["file"]
    p.parent.mkdir(parents=True)
    p.write_text("{}", encoding="utf-8")
    c["items"][0]["sha256"] = {c["items"][0]["file"]: assets.sha256(p)}
    r = assets.fetch(c, store=tmp_path, log=lambda *_: None)
    assert r == {"ok": [], "skip": ["box_unit"], "fail": {}}


def test_fetch_refuses_bad_catalog(tmp_path):
    c = cat()
    c["items"][0]["license"] = "CC-BY-4.0"
    with pytest.raises(ValueError):
        assets.fetch(c, store=tmp_path, log=lambda *_: None)


def test_polyhaven_plan_stays_in_folder():
    c = cat()
    files = {"gltf": {"1k": {"gltf": {"url": "https://x/a.gltf", "md5": "m",
                                      "include": {"textures/a.jpg": {"url": "https://x/a.jpg"}, "a.bin": {"url": "https://x/a.bin"}}}}}}
    plan = assets.polyhaven_plan(c["items"][0], files)
    assert [r for _, r, _ in plan] == ["polyhaven/box_unit/box_unit_1k.gltf", "polyhaven/box_unit/textures/a.jpg", "polyhaven/box_unit/a.bin"]
    files["gltf"]["1k"]["gltf"]["include"] = {"../../evil.py": {"url": "https://x/e"}}
    with pytest.raises(ValueError):
        assets.polyhaven_plan(c["items"][0], files)


def test_assets_blender_imports_without_bpy():
    from recorder import assets_blender
    assert callable(assets_blender.import_model) and callable(assets_blender.material_from_texture)
    assert assets_blender.material_for("brick") is None


# --- 棟 JSON の asset 部品（台帳は data/assets/catalog.json の本物）---------------------

def building():
    return json.loads(PARTS.read_text(encoding="utf-8"))


def asset_part(**kw):
    pt = {"type": "asset", "asset": "ac_outdoor", "face": None, "along": M(2.0), "offset": M(0.3),
          "z0": M(0.1), "yaw": 0, "count": M(2, "個"), "pitch": M(1.0),
          "size": {"w": M(0.8), "d": M(0.35), "h": M(0.9)}}
    pt.update(kw)
    return pt


def test_schema_asset_part_ok():
    b = building()
    b["parts"].append(asset_part(face=b["faces"][0]["id"]))
    assert schema.validate_building(b) == []


def test_schema_asset_part_bare_size_rejected():
    b = building()
    b["parts"].append(asset_part(face=b["faces"][0]["id"], size={"w": 0.8, "d": M(0.35), "h": M(0.9)}))
    errs = schema.validate_building(b)
    assert any("size.w" in e and "裸の数値" in e for e in errs)


def test_schema_asset_part_measured_without_evidence_rejected():
    b = building()
    h = M(0.9)
    h["evidence"] = []
    b["parts"].append(asset_part(face=b["faces"][0]["id"], size={"w": M(0.8), "d": M(0.35), "h": h}))
    assert any("根拠" in e for e in schema.validate_building(b))


def test_schema_asset_part_unknown_size_ok_but_key_required():
    b = building()
    unk = {"value": None, "status": "unknown", "evidence": []}
    b["parts"].append(asset_part(face=b["faces"][0]["id"], size={"w": unk, "d": unk, "h": unk}))
    assert schema.validate_building(b) == []
    b["parts"][-1]["size"] = {"w": unk, "h": unk}
    assert any("size: d" in e for e in schema.validate_building(b))


def test_schema_asset_part_wrong_id_or_kind():
    b = building()
    b["parts"].append(asset_part(face=b["faces"][0]["id"], asset="nope"))
    b["parts"].append(asset_part(face=b["faces"][0]["id"], asset="brick_ochre"))
    errs = schema.validate_building(b)
    assert any("nope" in e and "台帳" in e for e in errs)
    assert any("brick_ochre" in e and "model が要ります" in e for e in errs)


def test_schema_asset_part_needs_place():
    b = building()
    pt = asset_part()
    del pt["face"]
    b["parts"].append(pt)
    assert any("x＋y" in e for e in schema.validate_building(b))


def test_schema_material_asset_texture():
    b = building()
    b["parts"].append({"type": "pavement", "polygon": [[0, 0], [1, 0], [1, 1]], "z": M(0.0), "material": "asset:paving_block"})
    assert schema.validate_building(b) == []
    b["parts"][-1]["material"] = "asset:ac_outdoor"
    assert any("texture が要ります" in e for e in schema.validate_building(b))
    b["parts"][-1]["material"] = "asset:nope"
    assert any("nope" in e for e in schema.validate_building(b))


# --- Blender の中で取り込む（BLENDER と取得済みのアセットが無ければ飛ばす）-------------------

def test_blender_import_model_scale_and_materials():
    import os
    import subprocess

    exe = os.environ.get("BLENDER")
    if not exe or not Path(exe).exists():
        pytest.skip("BLENDER が無い")
    c = assets.load_catalog()
    try:
        for aid in assets.ids(c):
            assets.resolve(aid, c)
    except FileNotFoundError:
        pytest.skip("アセット未取得（recorder assets fetch）")
    script = Path(__file__).parent / "blender_assets_check.py"
    r = subprocess.run([exe, "-b", "--factory-startup", "-P", str(script)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    line = next(x for x in r.stdout.splitlines() if x.startswith("RESULT "))
    out = json.loads(line[len("RESULT "):])
    ac = out["ac_outdoor"]                       # 1.0 x 0.4 x 0.9 を 90° 回した → x と y が入れ替わる
    assert ac["size_xyz"] == pytest.approx([0.4, 1.0, 0.9], abs=0.01)
    assert ac["bottom"] == pytest.approx(0.2) and ac["center"] == pytest.approx([10.0, 5.0], abs=0.01)
    assert out["street_lamp"]["size_xyz"][2] == pytest.approx(4.5, abs=0.01)
    assert out["shared_mesh"] is True
    assert all("ShaderNodeNormalMap" in v for v in out["materials"].values())
    assert out["material_for"] == "asset:brick_ochre"
