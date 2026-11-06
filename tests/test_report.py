"""進捗報告（recorder/report.py）：モデル無しで動く部分と、生成物がある時の 1 ページの生成。"""
from __future__ import annotations

from pathlib import Path

import pytest

from recorder import report as rp


def test_quick_reply_flags_problems():
    rec = {"checks": {"resolution_ok": True, "blur_suspected": False, "has_gps": True, "edge_touch_left": 0.1}}
    q = rp.quick_reply(rec)
    assert q["usable"] and q["problems"] == []
    rec2 = {"checks": {"resolution_ok": False, "blur_suspected": True, "has_gps": False, "edge_touch_right": 0.8}}
    q2 = rp.quick_reply(rec2)
    assert not q2["usable"]
    assert "解像度が足りない" in q2["problems"] and "ぶれの疑い" in q2["problems"]
    assert any("右" in p for p in q2["problems"]) and any("GPS" in p for p in q2["problems"])


def test_inline_svg_strips_fixed_size(tmp_path: Path):
    p = tmp_path / "e.svg"
    p.write_text('<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50" viewBox="0 0 100 50"><rect/></svg>', encoding="utf-8")
    s = rp.inline_svg(p)
    assert s.startswith("<svg") and 'width="100"' not in s and 'height="50"' not in s and 'viewBox="0 0 100 50"' in s


def test_street_cam_from_model_report():
    assert rp._street_cam({"web_cameras": {"street": {"query": "cam=1,2,3"}}}) == "1,2,3"
    assert rp._street_cam({}) is None
    assert rp._street_cam({"web_cameras": {}}) is None


def test_stage_status_levels():
    d = {"photos": {"total": 0, "during": 0, "before": 0},
         "elev": [{"svg": Path("x"), "state": "estimated", "measured": 0}, {"svg": None, "state": "unknown", "measured": 0}],
         "evidence": {"registered": 1, "picks": 4, "resid_median": 10.0},
         "model": {"glb": Path("x"), "overlays": [], "height_m": 10.0, "assumed": []},
         "walk": {"on_site": False, "colored": 1, "gray": 2},
         "rest": {"requests": [1], "unknowns": [], "gray_faces": ["a"]}}
    st = rp.stage_status(d)
    assert st["photos"][0] == "todo" and st["elev"][0] == "partial" and st["evidence"][0] == "partial"
    assert st["model"][0] == "partial" and st["walk"][0] == "todo" and st["rest"][0] == "partial"


@pytest.mark.skipif(not (Path("out/honbu/elev_SW.svg").exists() and Path("data/photos/registry.json").exists()),
                    reason="本部棟の生成物が無い")
def test_report_honbu_builds_page(tmp_path: Path):
    out = tmp_path / "report.html"
    s = rp.run(Path("data/buildings/honbu.json"), out)
    html = out.read_text(encoding="utf-8")
    assert "<title>本部棟 進捗報告</title>" in html
    for key in ("photos", "elev", "evidence", "model", "walk", "rest"):
        assert f'id="{key}"' in html
        assert key in s["stages"]
    assert "<svg" in html and "measured" in html
    assert s["counts"]["photos"] > 0
