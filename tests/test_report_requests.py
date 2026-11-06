"""依頼（requests）の宛先：職員への撮影依頼（staff）と道具・係の作業（tool）を分ける（11/6）。"""
import json
from pathlib import Path

from recorder import drawing, report as rp, schema

FIX = Path(__file__).parent / "fixtures" / "sample_building.json"


def test_request_to_rules():
    rt = schema.request_to
    assert rt({"what": "x", "to": "staff"}) == "staff" and rt({"what": "南面を 1 枚", "to": "tool"}) == "tool"
    assert rt({"what": "両端が入る1枚"}) == "staff"
    assert rt({"what": "屋上の塔", "why": "写真が無い"}) == "staff"
    assert rt({"what": "北東の角を 45° から"}) == "staff"                 # 「撮」と書かない撮影依頼
    assert rt({"what": "東南東の全体を日中に", "where": "東側の道路から"}) == "staff"
    assert rt({"what": "パーゴラの語彙", "where": "recorder/parts_geom.py"}) == "tool"
    assert rt({"what": "1 枚を 2 面で同時に登録する印", "where": "recorder/photoproj.py"}) == "tool"   # コードの場所が先
    assert rt({"what": "階の番号", "where": "interior.rooms[hall_1f].floor"}) == "tool"
    assert rt({"what": "再計算する"}) == "tool"
    assert rt({"what": "x", "kind": "tool"}) == "tool" and rt({"what": "x", "kind": "photo"}) == "staff"


def test_validate_rejects_bad_to():
    b = json.loads(FIX.read_text(encoding="utf-8"))
    b["requests"][0]["to"] = "someone"
    errs = schema.validate_building(b)
    assert any("requests[0]: to=" in e for e in errs)
    b["requests"][0]["to"] = "tool"
    assert not any("requests[0]: to=" in e for e in schema.validate_building(b))


def test_split_and_report_counts_only_staff():
    b = json.loads(FIX.read_text(encoding="utf-8"))
    b["requests"] += [{"face": "S", "what": "spandrel を奥まりに置く欄", "where": "recorder/parts_geom.py", "why": "語彙が無い", "status": "open"},
                      {"face": "S", "what": "南面を 1 枚", "why": "済み", "status": "done"}]
    by = schema.split_requests(b)
    assert len(by["staff"]) == 2 and len(by["tool"]) == 1          # done は数えない
    d = {"photos": {"total": 0, "during": 0, "before": 0}, "elev": [], "evidence": {"registered": 0, "picks": 0, "resid_median": None},
         "model": {"glb": None, "overlays": [], "assumed": []}, "walk": {"on_site": False, "colored": 0, "gray": 0},
         "rest": {"requests": by["staff"], "tool_requests": by["tool"], "unknowns": [], "gray_faces": []}}
    assert "撮影依頼 2 件、道具の作業 1 件" in rp.stage_status(d)["rest"][1]
    svg = drawing.elevation_svg(b, "S")
    head, _, tail = svg.partition("道具の作業")
    assert "4階の窓を真横" in head and "spandrel を奥まりに置く欄" in tail and "spandrel" not in head


def test_serve_progress_counts_staff(tmp_path: Path):
    from recorder import serve
    b = json.loads(FIX.read_text(encoding="utf-8"))
    b["requests"].append({"face": "*", "what": "photoproj の差を出す", "where": "recorder/photoproj.py", "why": "-", "status": "open"})
    (tmp_path / "data" / "buildings").mkdir(parents=True)
    (tmp_path / "data" / "buildings" / "b1.json").write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "out" / "b1").mkdir(parents=True)
    (tmp_path / "out" / "b1" / "report.json").write_text(json.dumps({"stages": {}, "counts": {"requests": 99}}), encoding="utf-8")
    rep = serve.api_progress(tmp_path, ["b1"])["buildings"]["b1"]["report"]
    assert rep["counts"]["requests"] == 2 and rep["counts"]["tool_requests"] == 1   # 古い report.json の 99 を数え直す
    assert len(rep["photo_requests"]) == 2
