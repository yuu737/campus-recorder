import json
import warnings

import pytest

from recorder import agents

AGENTS = {"satsuei", "shiwake", "kansatsu", "kazoe", "sokuryo", "sekkei", "seizu", "kenshu", "chizu", "shikichi", "shinko", "mejirushi"}


def test_load_defs():
    d = agents.load_agent_defs()
    assert set(d) == AGENTS
    assert d["satsuei"]["model_id"] == "claude-haiku-5-5" and d["seizu"]["model_id"] == "claude-sonnet-5-5"
    assert d["kansatsu"]["model_id"] == "claude-opus-5-5"
    assert all(v["system_prompt"].startswith("# 係の共通の約束") for v in d.values())
    assert "Bash" in d["kenshu"]["tools"] and "Write" in d["kenshu"]["tools"]


def test_fable_model(tmp_path):
    (tmp_path / "x.md").write_text("---\nname: x\nmodel: fable\ntools: Read\n---\n本文", encoding="utf-8")
    d = agents.load_agent_defs(tmp_path)
    assert d["x"]["model"] == "fable" and d["x"]["model_id"] == "claude-fable-5-1"
    assert agents.LIMITS["fable"] == {"max_turns": 20, "max_budget_usd": 6.0}


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
    monkeypatch.setattr(runner, "run", lambda p, out, **kw: ran.setdefault("p", str(p)) and {"ok": True, "out": str(out)})
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


def test_tasks_cover_all_steps(tmp_path):
    doc = agents.build_tasks("b1", "data/photos/20261104")
    assert [s["step"] for s in doc["steps"]] == agents.PIPELINE_STEPS
    assert agents.PIPELINE_STEPS[-3:] == ["kenshu", "seizu_final", "web"]
    by = {s["step"]: s for s in doc["steps"]}
    for s in doc["steps"]:
        assert s["task"]
        if s["kind"] == "agent":
            assert s["system_files"][1] == f".claude/agents/{s['agent']}.md" and s["model_id"]
    assert "--period during" in by["satsuei"]["task"]
    assert "scripts/design_b1.py" in by["sekkei"]["task"] and "--from-site" in by["sekkei"]["task"]
    for w in ("recheck", "photoproj", "register", "coverage", "検収 C"):
        assert w in by["kenshu"]["task"]
    assert by["web"]["command"] == "recorder site export-web --with-coverage" and by["seizu_final"]["agent"] == "seizu"
    # 目印係は模型の後・検収の前。依頼文は升を指して道具（pnp）が登録する形
    i = agents.PIPELINE_STEPS.index
    assert i("seizu") < i("mejirushi") < i("kenshu")
    m = by["mejirushi"]
    assert m["kind"] == "agent" and m["system_files"][1] == ".claude/agents/mejirushi.md"
    for w in ("recorder photos landmarks data/buildings/b1.json", "landmarks-crop", "data/reports/b1/mejirushi/picks_<写真id>.json",
              "photos pnp data/buildings/b1.json --picks data/reports/b1/mejirushi/", "--check-jpg", "寸法", "registry.json"):
        assert w in m["task"], w
    # 係の報告 JSON の置き場は data/reports/<棟>/（画像と道具の出力は out/）
    for st, w in (("kansatsu", "data/reports/b1/kansatsu_<面id>.json"), ("kazoe", "data/reports/b1/kazoe_<面id>.json"),
                  ("sokuryo", "data/reports/b1/sokuryo_<面id>.json"), ("sekkei", "data/reports/b1/sekkei_summary.json"),
                  ("kenshu", "data/reports/b1/kenshu_summary.json")):
        assert w in by[st]["task"], (st, w)
        assert f"out/b1/{w.rsplit('/', 1)[1]}" not in by[st]["task"]
    assert "out/b1/coverage_<面>_photo.png" in by["sekkei"]["task"]
    # API の pipeline と同じ文（状態に依らない）
    r = agents.run_pipeline("b1", "data/photos/20261104", steps=["sekkei", "kenshu"], dry_run=True, cwd=tmp_path,
                            agents_dir=".claude/agents")
    assert [x["task"] for x in r["steps"]] == [by["sekkei"]["task"], by["kenshu"]["task"]]


def test_write_tasks(tmp_path):
    import shutil
    shutil.copytree(".claude/agents", tmp_path / ".claude/agents")
    w = agents.write_tasks("b1", None, cwd=tmp_path)
    doc = json.loads((tmp_path / "out/b1/agent_tasks.json").read_text(encoding="utf-8"))
    assert w["steps"] == len(doc["steps"]) == len(agents.PIPELINE_STEPS)
    md = (tmp_path / "out/b1/agent_tasks.md").read_text(encoding="utf-8")
    assert all(s["task"] in md for s in doc["steps"])


def test_log_appends(tmp_path):
    r1 = agents.log_step("b1", "shiwake", model="opus", seconds=60, tokens=1000, note="一回目", cwd=tmp_path)
    (tmp_path / "out/b1/kansatsu_f0.json").write_text("{}", encoding="utf-8")
    r2 = agents.log_step("b1", "kansatsu", seconds=30.5, tokens=500, cwd=tmp_path)
    assert r1["record"] == r2["record"]
    doc = json.loads(open(r2["record"], encoding="utf-8").read())
    assert doc["runner"] == "subagent" and [s["step"] for s in doc["steps"]] == ["shiwake", "kansatsu"]
    assert doc["steps"][0]["model"] == "claude-opus-5-5" and doc["steps"][1]["outputs"] == ["out/b1/kansatsu_f0.json"]
    assert doc["seconds_total"] == 90.5 and doc["tokens_total"] == 1500
    r3 = agents.log_step("b1", "web", model="code", cwd=tmp_path, new=True)
    assert r3["record"] != r1["record"] and r3["steps"] == 1
    with pytest.raises(agents.AgentError):
        agents.log_step("b1", "nai", cwd=tmp_path)


def test_log_mejirushi(tmp_path):
    (tmp_path / "data/reports/b1/mejirushi").mkdir(parents=True)
    (tmp_path / "data/reports/b1/mejirushi/picks_P1.json").write_text("{}", encoding="utf-8")
    (tmp_path / "out/b1/landmarks").mkdir(parents=True)
    (tmp_path / "out/b1/landmarks/landmarks.json").write_text("{}", encoding="utf-8")
    r = agents.log_step("b1", "mejirushi", model="opus", seconds=120, tokens=800, cwd=tmp_path)
    add = r["added"]
    assert add["agent"] == "mejirushi" and add["step"] == "mejirushi" and add["model"] == "claude-opus-5-5"
    assert add["outputs"] == ["data/reports/b1/mejirushi/picks_P1.json"]
    # 記録は out/ と data/reports/ の両方に同じ物
    a = json.loads(open(r["record"], encoding="utf-8").read())
    b = json.loads(open(r["report_record"], encoding="utf-8").read())
    assert a == b and "data/reports/b1/pipeline_" in r["report_record"].replace("\\", "/")


def test_pipeline_record_shape(tmp_path, monkeypatch):
    """SDK を呼ばずに（_invoke をモック）、係ごとの実行記録の形だけ確かめる。"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.delenv(agents.PRICES_ENV, raising=False)
    answers = {"kansatsu": {"text": "\n# 観察票 3 面を書いた\n詳細…", "input_tokens": 1000, "output_tokens": 200,
                            "cache_creation_input_tokens": None, "cache_read_input_tokens": 4000, "token_note": None,
                            "cost_usd": 0.02, "turns": 3, "is_error": False, "stop": "success"},
               "kazoe": {"text": "数えた", "input_tokens": None, "output_tokens": None,
                         "token_note": "SDK の結果（usage・model_usage）に input_tokens・output_tokens が無いので null",
                         "cost_usd": None, "turns": 1, "is_error": False, "stop": "success"}}
    monkeypatch.setattr(agents, "_invoke", lambda spec, *a: answers[spec["name"]])
    (tmp_path / "data/reports/b1").mkdir(parents=True)
    (tmp_path / "data/reports/b1/kansatsu_f0.json").write_text("{}", encoding="utf-8")
    prices = {"opus": {"input": 5.0, "output": 25.0}}
    r = agents.run_pipeline("b1", steps=["kansatsu", "kazoe"], cwd=tmp_path, agents_dir=".claude/agents", prices=prices)
    out = json.loads(open(r["record"], encoding="utf-8").read())
    rep = json.loads(open(r["report_record"], encoding="utf-8").read())
    assert out == rep and r["record"].replace("\\", "/").split("/")[-1] == r["report_record"].replace("\\", "/").split("/")[-1]
    k, z = out["steps"]
    for s in (k, z):
        for key in ("agent", "model", "started_at", "ended_at", "seconds", "input_tokens", "output_tokens",
                    "cost_usd_estimate", "summary", "note"):
            assert key in s, key
    assert k["agent"] == "kansatsu" and k["model"] == "claude-opus-5-5" and k["summary"] == "観察票 3 面を書いた"
    assert k["outputs"] == ["data/reports/b1/kansatsu_f0.json"]
    # 1000×5 + 200×25 + 4000×(5×0.1) = 12000 → 0.012 USD
    assert k["cost_usd_estimate"] == pytest.approx(0.012) and k["cache_read_input_tokens"] == 4000
    assert z["input_tokens"] is None and z["cost_usd_estimate"] is None
    assert "無いので null" in z["note"] and "トークン数が取れない" in z["note"]
    assert out["cost_usd_estimate"] == pytest.approx(0.012) and out["input_tokens_total"] == 1000
    # 単価を渡さなければ見積もりは null と理由
    r2 = agents.run_pipeline("b1", steps=["kansatsu"], cwd=tmp_path, agents_dir=".claude/agents")
    s2 = r2["steps"][0]
    assert s2["cost_usd_estimate"] is None and "単価が渡されていない" in s2["note"] and s2["cost_usd"] == 0.02


def test_usage_tokens_from_model_usage():
    toks, note = agents._usage_tokens(None, {"claude-opus-5-5": {"inputTokens": 7, "outputTokens": 3, "cacheReadInputTokens": 0,
                                                                  "cacheCreationInputTokens": 2}})
    assert toks == {"input_tokens": 7, "output_tokens": 3, "cache_creation_input_tokens": 2, "cache_read_input_tokens": 0} and note is None
    toks, note = agents._usage_tokens({}, None)
    assert toks["input_tokens"] is None and "input_tokens" in note


def test_pipeline_dry_run_mejirushi(tmp_path):
    r = agents.run_pipeline("b1", steps=["seizu", "mejirushi", "kenshu"], dry_run=True, cwd=tmp_path, agents_dir=".claude/agents")
    assert [s["agent"] for s in r["steps"]] == ["seizu", "mejirushi", "kenshu"]
    assert r["steps"][1]["task"] == agents.step_task("mejirushi", "b1") and r["steps"][1]["model"] == "claude-opus-5-5"
