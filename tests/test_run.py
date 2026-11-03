import json

from recorder import run as runner
from recorder import schema


def test_run_skip_model(tmp_path):
    b = schema.new_building("t1", "テスト棟")
    src = tmp_path / "t1.json"
    schema.save_building(b, src)
    r = runner.run(src, tmp_path / "out", skip_model=True)
    assert r["ok"]
    assert [s["name"] for s in r["steps"]] == ["validate", "draw"]
    assert (tmp_path / "out" / "plan.svg").exists()
    rep = json.loads((tmp_path / "out" / "run_report.json").read_text(encoding="utf-8"))
    assert set(rep["counts"]) == {"measured", "estimated", "unknown"}


def test_run_stops_on_invalid(tmp_path):
    b = schema.new_building("t2", "不正棟")
    b["footprint"] = {"status": "measured", "evidence": [], "method": "", "polygon": []}
    src = tmp_path / "t2.json"
    src.write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
    r = runner.run(src, tmp_path / "out", skip_model=True)
    assert not r["ok"]
    assert [s["name"] for s in r["steps"]] == ["validate"]
    assert r["steps"][0]["errors"]
