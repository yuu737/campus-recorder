"""Web ビューア用の書き出し（site export-web）のテスト。"""
import json

import pytest

from recorder import site


def test_export_web_writes_site_json(tmp_path):
    if not (site.DATA_SITE / "tempaku.json").exists() or not (site.OUT_SITE / "dem5a.npz").exists():
        pytest.skip("敷地 JSON か標高が無い")
    r = site.export_web(web_dir=tmp_path)
    d = json.loads((tmp_path / "data" / "site.json").read_text(encoding="utf-8"))
    g = d["ground"]
    assert len(g["z"]) == g["nx"] * g["ny"]
    assert r["boxes"] == len(d["boxes"]) > 0
    for b in d["boxes"]:
        assert len(b["footprint"]) >= 3
        assert b["grounded"] == b["evidenced"] == (b["id"] in d["designed"] and d["designed"][b["id"]]["evidenced"])
    for m in d["models"].values():
        assert (tmp_path / m["url"]).exists()


def test_viewer_hides_glb_terrain():
    """歩く画面の地面は site.json の格子に統一。glb の terrain は viewer が非表示にする。"""
    from pathlib import Path
    html = (Path(site.__file__).resolve().parent.parent / "web" / "index.html").read_text(encoding="utf-8")
    assert "/^terrain/.test(o.name)" in html and "o.visible = false" in html


def test_far_terrain_is_coarser_than_near():
    import re
    from pathlib import Path
    src = (Path(site.__file__).resolve().parent / "blender_build.py").read_text(encoding="utf-8")   # bpy が無くても読める
    assert float(re.search(r"^TERRAIN_FAR_STEP_M = ([\d.]+)", src, re.M).group(1)) >= 10.0
    assert int(re.search(r"^TERRAIN_FAR_SMOOTH_PX = (\d+)", src, re.M).group(1)) >= 15
