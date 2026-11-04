"""Web 用の焼き込み（手続き的な素材 → 画像）と、歩く画面へのアセットの写しのテスト（bpy 不要）。

11/4：Blender の描画（タイル＋太陽＋空＋影）と歩く画面（1 色の壁、半球光、影なし）で見た目が違った。
タイルはノードの模様なので glb に出ない。周期の板に焼いて画像で入れる。"""
import json
import math
from pathlib import Path

import pytest

from recorder import assets_blender, site

ROOT = Path(__file__).resolve().parent.parent


# --- 焼く対象の選び方 ---

def test_needs_bake_only_procedural_without_image():
    assert assets_blender.needs_bake(["ShaderNodeTexBrick", "ShaderNodeTexNoise", "ShaderNodeBsdfPrincipled"])
    # CC0 の画像素材はそのまま glb に入る（焼かない）
    assert not assets_blender.needs_bake(["ShaderNodeTexImage", "ShaderNodeMapping", "ShaderNodeBsdfPrincipled"])
    assert not assets_blender.needs_bake(["ShaderNodeTexBrick", "ShaderNodeTexImage"])
    # 色だけの素材は glb の色でそのまま出る
    assert not assets_blender.needs_bake(["ShaderNodeBsdfPrincipled", "ShaderNodeOutputMaterial"])


# --- 周期 ---

@pytest.mark.parametrize("w,h,j", [(0.227, 0.06, 0.01), (0.227, 0.06, 0.005), (0.095, 0.045, 0.008)])
def test_brick_period_is_whole_number_of_tiles(w, h, j):
    pw, ph = assets_blender.brick_period(w, h, j)
    nw, nh = pw / (w + j), ph / (2 * (h + j))     # 縦は 1 段おきにずれるので 2 段で 1 周期
    assert abs(nw - round(nw)) < 1e-9 and abs(nh - round(nh)) < 1e-9
    assert abs(pw - assets_blender.BAKE_TARGET_M) <= (w + j) / 2 + 1e-9
    assert abs(ph - assets_blender.BAKE_TARGET_M) <= (h + j) + 1e-9


def test_brick_period_rejects_zero():
    with pytest.raises(ValueError):
        assets_blender.brick_period(0, 0, 0)


# --- m 単位の UV ---

def test_face_uv_wall_is_metres_along_wall_and_height():
    # 南向きの壁：u は東へ、v は高さ
    assert assets_blender.face_uv((3.0, 5.0, 2.0), (0, -1, 0)) == (3.0, 2.0)
    # 斜めの壁でも、壁に沿って 1 m 離れた 2 点の u の差は 1 m（箱投影のように伸びない）
    n = (math.cos(math.radians(30)), math.sin(math.radians(30)), 0)
    t = (-n[1], n[0])
    a = assets_blender.face_uv((10.0, 20.0, 1.0), n)
    b = assets_blender.face_uv((10.0 + t[0], 20.0 + t[1], 4.0), n)
    assert b[0] - a[0] == pytest.approx(1.0) and b[1] - a[1] == pytest.approx(3.0)


def test_face_uv_floor_and_roof_use_xy():
    assert assets_blender.face_uv((1.5, -2.5, 9.0), (0, 0, 1)) == (1.5, -2.5)
    assert assets_blender.face_uv((1.5, -2.5, 9.0), (0, 0.1, -0.99)) == (1.5, -2.5)


def test_blender_build_bakes_before_glb_and_records_time():
    src = (ROOT / "recorder" / "blender_build.py").read_text(encoding="utf-8")
    i_bake, i_glb = src.index("export_web_materials(objs"), src.index("export_scene.gltf(")
    assert i_bake < i_glb
    assert '"web_bake"' in src and '"web_cameras"' in src
    assert 'coord.outputs["Object"], brick' not in src      # タイルは m 単位の UV で貼る（物体座標の x, y だと壁で縦縞）


# --- export-web がアセットを写す ---

def _fake_store(tmp_path):
    from PIL import Image
    store = tmp_path / "store"
    (store / "ph" / "sky").mkdir(parents=True)
    (store / "ph" / "sky" / "sky_1k.hdr").write_bytes(b"#?RADIANCE\n")
    items = [{"id": "hdri_overcast", "kind": "hdri", "license": "CC0-1.0", "file": "ph/sky/sky_1k.hdr"}]
    for aid in site.WEB_GROUND_TEX:
        d = store / "ph" / aid
        d.mkdir(parents=True)
        Image.new("RGB", (1024, 1024), (90, 90, 90)).save(d / "c.jpg")
        Image.new("RGB", (1024, 1024), (128, 128, 255)).save(d / "n.jpg")
        items.append({"id": aid, "kind": "texture", "license": "CC0-1.0", "file": f"ph/{aid}",
                      "maps": {"color": "c.jpg", "normal": "n.jpg"}, "tile_m": [2.0, 2.0]})
    return {"items": items}, store


def test_copy_web_assets_writes_env_and_small_textures(tmp_path):
    from PIL import Image
    cat, store = _fake_store(tmp_path)
    data = tmp_path / "web" / "data"
    r = site.copy_web_assets(data, cat=cat, store=store)
    assert r["skipped"] == []
    assert r["env"]["url"] == "data/env/sky_1k.hdr" and (data / "env" / "sky_1k.hdr").exists()
    for aid in site.WEB_GROUND_TEX:
        g = r["ground_tex"][aid]
        assert g["tile_m"] == [2.0, 2.0] and g["license"] == "CC0-1.0"
        for k in ("color", "normal"):
            p = data.parent / g[k]
            with Image.open(p) as im:
                assert max(im.size) == site.WEB_TEX_PX


def test_copy_web_assets_skips_missing(tmp_path):
    cat, store = _fake_store(tmp_path)
    (store / "ph" / "sky" / "sky_1k.hdr").unlink()
    r = site.copy_web_assets(tmp_path / "data", cat=cat, store=store)
    assert r["env"] is None and any("hdri_overcast" in s for s in r["skipped"])


def test_export_web_site_json_has_env_and_ground_tex(tmp_path):
    if not (site.DATA_SITE / "tempaku.json").exists() or not (site.OUT_SITE / "dem5a.npz").exists():
        pytest.skip("敷地 JSON か標高が無い")
    site.export_web(web_dir=tmp_path)
    d = json.loads((tmp_path / "data" / "site.json").read_text(encoding="utf-8"))
    assert "env" in d and "ground_tex" in d
    if d["env"]:
        assert (tmp_path / d["env"]["url"]).exists()
    for g in d["ground_tex"].values():
        assert (tmp_path / g["color"]).exists() and (tmp_path / g["normal"]).exists()


# --- 歩く画面 ---

def test_viewer_lighting_and_cam_param():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    for s in ("PMREMGenerator", "shadowMap.enabled = true", "mapSize.set(2048, 2048)", "toneMapping",
              "SRGBColorSpace", 'Q.get("cam")', "receiveShadow"):
        assert s in html, s
    assert "0x9fae8c" not in html          # 緑 1 色の地面はやめた
