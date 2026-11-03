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
