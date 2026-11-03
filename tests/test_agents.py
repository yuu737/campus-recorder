import json
import warnings

import pytest

from recorder import agents

AGENTS = {"satsuei", "shiwake", "kansatsu", "kazoe", "sokuryo", "sekkei", "seizu", "kenshu"}


def test_load_defs():
    d = agents.load_agent_defs()
    assert set(d) == AGENTS
    assert d["satsuei"]["model_id"] == "claude-haiku-5-5" and d["seizu"]["model_id"] == "claude-sonnet-5-5"
    assert d["kansatsu"]["model_id"] == "claude-opus-5-5"
    assert all(v["system_prompt"].startswith("# 係の共通の約束") for v in d.values())
    assert "Bash" in d["kenshu"]["tools"] and "Write" in d["kenshu"]["tools"]


def test_fable_falls_back(tmp_path):
    (tmp_path / "x.md").write_text("---\nname: x\nmodel: fable\ntools: Read\n---\n本文", encoding="utf-8")
    with pytest.warns(UserWarning):
        d = agents.load_agent_defs(tmp_path)
    assert d["x"]["model_id"] == "claude-opus-5-5"


def test_dry_run():
    r = agents.run_agent("kansatsu", "テスト", dry_run=True)
    assert r["dry_run"] and r["model"] == "claude-opus-5-5" and r["task"] == "テスト"
    assert "Glob" in r["tools"] and r["system_prompt"].startswith("# 係の共通")
    json.dumps(r, ensure_ascii=False)


def test_no_key_stops(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(agents.AgentError):
        agents.run_agent("kansatsu", "x")


def test_pipeline_dry_run_writes_nothing(tmp_path):
    r = agents.run_pipeline("b1", steps=["shiwake", "seizu"], dry_run=True, cwd=tmp_path, agents_dir=".claude/agents")
    assert [s["agent"] for s in r["steps"]] == ["shiwake", "seizu"]
    assert not (tmp_path / "out").exists()


def test_pipeline_mocked(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    calls = []

    def fake(spec, task, cwd, max_turns, max_budget_usd):
        calls.append((spec["name"], max_turns, max_budget_usd))
        (cwd / "out" / "b1").mkdir(parents=True, exist_ok=True)
        (cwd / "out" / "b1" / f"{spec['name']}_x.json").write_text("{}", encoding="utf-8")
        return {"text": "ok", "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.01, "turns": 2, "is_error": False, "stop": "success"}

    monkeypatch.setattr(agents, "_invoke", fake)
    ran = {}
    import recorder.run as runner
    monkeypatch.setattr(runner, "run", lambda p, out: ran.setdefault("p", str(p)) and {"ok": True, "out": str(out)})
    r = agents.run_pipeline("b1", steps=["satsuei", "shiwake", "seizu", "kenshu"], cwd=tmp_path, agents_dir=".claude/agents")
    assert [c[0] for c in calls] == ["satsuei", "shiwake", "kenshu"]
    assert calls[0][1:] == (12, 0.5) and calls[1][1:] == (30, 4.0)
    assert "b1.json" in ran["p"]
    rec = json.loads(open(r["record"], encoding="utf-8").read())
    assert [s["agent"] for s in rec["steps"]] == ["satsuei", "shiwake", "seizu", "kenshu"]
    assert rec["steps"][0]["outputs"] == ["out/b1/satsuei_x.json"] and rec["steps"][0]["input_tokens"] == 10
    assert rec["cost_usd_estimate"] == 0.03


def test_pipeline_stops_on_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(agents, "_invoke", lambda *a: {"text": "", "cost_usd": 4.0, "is_error": True, "stop": "error_max_budget_usd"})
    r = agents.run_pipeline("b1", steps=["shiwake", "kansatsu"], cwd=tmp_path, agents_dir=".claude/agents")
    assert r["stopped_early"] and len(r["steps"]) == 1 and r["steps"][0]["stopped_by_limit"]
