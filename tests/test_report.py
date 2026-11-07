"""進捗報告（recorder/report.py）：モデル無しで動く部分と、生成物がある時の 1 ページの生成。"""
from __future__ import annotations

import json
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


@pytest.mark.skipif(not (any(Path("out/honbu").glob("elev_*.svg")) and Path("data/photos/registry.json").exists()),
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


def test_refresh_report_in_pipeline_root(tmp_path: Path):
    from recorder import agents
    from recorder.schema import new_building, save_building

    assert agents.refresh_report("nope", tmp_path) == {"skipped": "棟 JSON がまだ無い"}
    j = tmp_path / "data" / "buildings" / "b1.json"
    j.parent.mkdir(parents=True)
    save_building(new_building("b1", "見本棟"), j)
    r = agents.refresh_report("b1", tmp_path)
    assert (tmp_path / "out" / "b1" / "report.html").exists(), r
    assert r["stages"]["photos"] == "todo" and r["stages"]["walk"] == "todo"


# --- 11/8：報告の数字の整合（監査 C の 1・2） ---------------------------------------------------

def test_report_json_goes_next_to_html_not_real_out(tmp_path: Path):
    """--out で別の場所に出した時、report.json も同じ場所に書く（本物の out/<棟>/report.json を上書きしない）。"""
    from recorder.schema import new_building, save_building

    j = tmp_path / "data" / "buildings" / "b1.json"
    j.parent.mkdir(parents=True)
    save_building(new_building("b1", "見本棟"), j)
    s = rp.run(j, tmp_path / "elsewhere" / "r.html", root=tmp_path)
    assert (tmp_path / "elsewhere" / "report.json").exists()
    assert not (tmp_path / "out" / "b1" / "report.json").exists()
    assert s["html"].endswith("r.html")


@pytest.mark.skipif(not Path("out/honbu/report.json").exists(), reason="本部棟の報告が無い")
def test_report_honbu_to_tmp_keeps_real_report(tmp_path: Path):
    real = Path("out/honbu/report.json")
    before = real.read_bytes()
    rp.run(Path("data/buildings/honbu.json"), tmp_path / "r.html")
    assert real.read_bytes() == before
    assert (tmp_path / "report.json").exists()


def test_unknown_paths_match_status_counts():
    """報告の「未確定の値」の一覧と counts.unknown は同じ物を数える（部品・塊の unknown も入る）。"""
    from recorder.evidence import status_counts

    b = {"faces": [{"id": "f0", "height": {"value": None, "status": "unknown"},
                    "bays": {"count": {"value": 3, "status": "measured", "evidence": ["P1"]}}}],
         "parts": [{"size": {"d": {"value": None, "status": "unknown"}, "w": {"value": 1.0, "status": "estimated"}}}],
         "requests": [{"what": "x", "status": "open"}], "history": [{"status": "unknown"}]}
    paths = rp.unknown_paths(b)
    assert paths == ["faces[f0].height", "parts[0].size.d"]
    assert len(paths) == status_counts(b)["total"]["unknown"]


def test_kenshu_faces_two_shapes(tmp_path: Path):
    d = tmp_path / "data" / "reports"
    (d / "a").mkdir(parents=True)
    (d / "a" / "kenshu_summary.json").write_text(json.dumps({"faces": {
        "f0": {"pass": True}, "f1": {"pass": True, "note": "弱い合格：1 枚だけ"}, "f2": {"pass": False}, "f3": {"pass": None}}}), encoding="utf-8")
    k = rp.kenshu_faces(tmp_path, "a")
    assert k["pass"] == ["f0"] and k["weak"] == ["f1"] and k["fail"] == ["f2"] and k["not_judged"] == ["f3"]
    (d / "b").mkdir()
    (d / "b" / "kenshu_summary.json").write_text(json.dumps({"faces": [{"face": "f0", "pass": True}, {"face": "f1", "pass": False}]}), encoding="utf-8")
    k = rp.kenshu_faces(tmp_path, "b")
    assert k["pass"] == ["f0"] and k["fail"] == ["f1"]
    assert rp.kenshu_faces(tmp_path, "none") is None


def test_stage_status_shows_overall_next_to_best():
    d = {"photos": {"total": 1, "during": 1, "before": 0}, "elev": [],
         "evidence": {"registered": 3, "picks": 9, "resid_median": 12.0,
                      "resid": {"median_px": 12.0, "median_m": 0.2, "best_face": {"face": "f1", "median_px": 3.0, "median_m": 0.05, "picks": 4}}},
         "model": {"glb": None, "overlays": [], "assumed": [], "kenshu": {"pass": ["f0"], "weak": ["f1"], "fail": [], "not_judged": ["f2"]}},
         "walk": {"on_site": False, "colored": 0, "gray": 0},
         "rest": {"requests": [], "tool_requests": [], "unknowns": [], "gray_faces": []}}
    st = rp.stage_status(d)
    assert "再投影の中央値 12.0 px（約 0.2 m）、面ごとの最良 f1 3.0 px（約 0.05 m）" in st["evidence"][1]
    assert "検収で合格した面 1（ほか弱い合格 1）・不合格 0・判定せず 1" in st["model"][1]


# --- scripts/readme_numbers.py：README の表を report.json と照合する --------------------------------

def _readme_numbers():
    import importlib.util
    spec = importlib.util.spec_from_file_location("readme_numbers", Path(__file__).resolve().parent.parent / "scripts" / "readme_numbers.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_readme_numbers_block_roundtrip_and_detects_diff(tmp_path: Path, monkeypatch):
    rn = _readme_numbers()
    monkeypatch.setattr(rn, "name_of", lambda b: {"a": "棟A", "b": "棟B"}[b])
    vals = {"a": [10, 5, 3, 1, 100, 4, 2, 3, 80, 1, 6, 2, 4, 1, 17.6, 0.18, 17.1, 0.18],
            "b": [1, 0, 0, 0, 5, 0, 0, 0, 5, 0, 0, 0, 0, 0, None, None, None, None]}
    ja, en = tmp_path / "ja.md", tmp_path / "en.md"
    for lang, p in (("ja", ja), ("en", en)):
        p.write_text("前\n" + rn.block(lang, ["a", "b"], {}, vals, "2026-11-08 09:00") + "\n後\n", encoding="utf-8")
    monkeypatch.setattr(rn, "READMES", {"ja": ja, "en": en})
    assert rn.check("ja", ["a", "b"], vals) == [] and rn.check("en", ["a", "b"], vals) == []
    ja.write_text(ja.read_text(encoding="utf-8").replace("| 棟A | 10 |", "| 棟A | 11 |"), encoding="utf-8")
    diffs = rn.check("ja", ["a", "b"], vals)
    assert len(diffs) == 1 and "列 0" in diffs[0]
    # 印の外に手で書いた表の行が残っていれば差に数える
    ja.write_text(ja.read_text(encoding="utf-8").replace("| 棟A | 11 |", "| 棟A | 10 |") + "| 棟A | 99 | 1 |\n", encoding="utf-8")
    assert any("印の外" in x for x in rn.check("ja", ["a", "b"], vals))
