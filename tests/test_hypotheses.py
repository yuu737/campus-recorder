"""仮説（basis: hypothesis）：validate の規則、--drop で unknown に戻る、evidence_summary、立面の斜線。"""
import copy

from recorder import __main__ as cli
from recorder import drawing, schema


def M(v, st="measured"):
    return {"value": v, "unit": "m", "tol": None, "status": st, "evidence": ["P1"], "method": "正面化", "note": ""}


def base():
    b = schema.new_building("t", "試し")
    b["footprint"] = {"status": "estimated", "evidence": ["plateau:x"], "method": "PLATEAU",
                      "polygon": [[0, 0], [20, 0], [20, 10], [0, 10]]}
    b["floor_count"] = {**M(3), "unit": "階"}
    b["floor_pitch"] = M(4.0)
    b["parapet_top"] = M(12.0)
    b["blocks"] = [{"id": "A", "polygon": b["footprint"]["polygon"], "top_z": M(12.0)}]
    b["faces"] = [
        {"id": "f0", "name": "南", "from": [0, 0], "to": [20, 0], "length": M(20.0),
         "bays": {"count": {**M(4), "unit": "個"}, "pitch": M(5.0), "pier_width": M(0.8)},
         "windows": [{"name": "窓", "floors": [2], "per_bay": 1, "z_bottom": M(5.0), "z_top": M(7.0), "width": M(3.0)}]},
        {"id": "f2", "name": "北", "from": [20, 10], "to": [0, 10], "length": M(20.0), "bays": {}, "windows": []},
    ]
    f0, f2 = b["faces"]
    f2["bays"] = {k: schema.hypothesis_from(f0["bays"][k], "symmetry", "f0", "対面") for k in ("count", "pitch", "pier_width")}
    f2["windows"] = [{"name": "窓", "floors": [2], "per_bay": 1,
                      **{k: schema.hypothesis_from(f0["windows"][0][k], "symmetry", "f0", "対面") for k in ("z_bottom", "z_top", "width")}}]
    return b


def test_valid_hypothesis_passes_and_summary_is_hypothesis():
    b = base()
    assert schema.validate_building(b) == []
    s = schema.evidence_summary(b)
    assert s["faces"]["f2"]["state"] == "hypothesis" and s["faces"]["f0"]["state"] == "measured"
    assert len(list(schema.iter_hypotheses(b))) == 6


def test_validate_rejects_bad_basis_rule_and_missing_from():
    b = base()
    b["faces"][1]["bays"]["count"]["basis"] = "guess"
    assert any("basis" in e for e in schema.validate_building(b))
    b = base()
    b["faces"][1]["bays"]["count"]["hypothesis"]["rule"] = "looks_like"
    assert any("rule" in e for e in schema.validate_building(b))
    b = base()
    del b["faces"][1]["bays"]["count"]["hypothesis"]["from"]
    assert any("hypothesis.from" in e for e in schema.validate_building(b))
    b = base()
    b["faces"][1]["bays"]["count"]["hypothesis"]["from"] = "f9"
    assert any("f9" in e for e in schema.validate_building(b))


def test_validate_rejects_hypothesis_parts():
    b = base()
    b["parts"] = [{"type": "box", "polygon": [[0, 0], [1, 0], [1, 1]],
                   "z0": M(0.0), "z1": schema.hypothesis(3.0, "same_block", "A", "箱")}]
    assert any("部品は仮説で作らない" in e for e in schema.validate_building(b))


def test_drop_returns_unknown_and_history():
    b = base()
    orig = copy.deepcopy(b)
    nb, dropped = schema.drop_hypotheses(b, "f2", "写真で列数が 5 と分かった", "2026-11-04T20:00")
    assert b == orig and len(dropped) == 6                    # 元は変えない
    f2 = nb["faces"][1]
    assert f2["bays"]["count"]["status"] == "unknown" and f2["bays"]["count"]["value"] is None
    assert "basis" not in f2["bays"]["count"]
    assert f2["windows"] == []                                # 全部仮説だった窓は項目ごと外す
    assert "写真で列数" in nb["history"][-1]["what"]
    assert schema.validate_building(nb) == []
    assert schema.evidence_summary(nb)["faces"]["f2"]["state"] == "unknown"
    assert schema.apply_hypotheses_off(b, {"f2": {"at": "", "reason": "x"}})["faces"][1]["windows"] == []


def test_cli_drop_writes_json_and_script(tmp_path):
    p = tmp_path / "t.json"
    schema.save_building(base(), p)
    sp = tmp_path / "design_t.py"
    sp.write_text("HYPOTHESES_OFF: dict = {}\n", encoding="utf-8")
    assert cli.main(["hypotheses", str(p), "--list"]) == 0
    assert cli.main(["validate", "--no-hypotheses", str(p)]) == 0
    assert cli.main(["hypotheses", str(p), "--drop"]) == 1            # 理由が要る
    assert cli.main(["hypotheses", str(p), "--drop", "--face", "f2", "--reason", "矛盾", "--script", str(sp)]) == 0
    assert list(schema.iter_hypotheses(schema.load_building(p))) == []
    assert 'HYPOTHESES_OFF["f2"]' in sp.read_text(encoding="utf-8")


def test_elevation_hatch_only_on_hypothesis_face():
    b = base()
    assert 'data-hypothesis="f2"' in drawing.elevation_svg(b, "f2")
    assert "data-hypothesis" not in drawing.elevation_svg(b, "f0")
