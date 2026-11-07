import json
import warnings

import pytest

from recorder import agents

AGENTS = {"satsuei", "shiwake", "kansatsu", "kazoe", "sokuryo", "sekkei", "seizu", "kenshu", "chizu", "shikichi", "shinko", "mejirushi", "hogo"}  # hogo：保護の係（K-104、T4）


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


# ---- 11/8 T3：事件の検出の分け方・再開・追記・費用の上限 ----

def test_tool_request_class():
    c = agents.tool_request_class
    assert c({"kind": "tool", "what": "敷地に置く物：通路橋", "why": "敷地に段を置く語彙がまだ無い"}) == "site"
    assert c({"kind": "tool", "what": "registry の exclude を書く道具", "where": "recorder/registry.py"}) == "tool"
    assert c({"kind": "tool", "what": "螺旋階段", "why": "部品の型に無く composite でも表せない"}) == "vocab"
    assert c({"kind": "tool", "what": "何か", "need": "vocab"}) == "vocab"
    assert c({"kind": "tool", "what": "敷地に置く物", "need": "tool_bug"}) == "tool"


def _b(tmp_path, bid="b1", requests=(), soten=None):
    (tmp_path / "data/buildings").mkdir(parents=True, exist_ok=True)
    (tmp_path / f"data/buildings/{bid}.json").write_text(json.dumps({"id": bid, "requests": list(requests)}, ensure_ascii=False),
                                                         encoding="utf-8")
    if soten is not None:
        (tmp_path / f"out/{bid}").mkdir(parents=True, exist_ok=True)
        (tmp_path / f"out/{bid}/soten.json").write_text(json.dumps({"items": soten}, ensure_ascii=False), encoding="utf-8")


def test_events_split_tool_check_soten(tmp_path):
    reqs = [{"kind": "tool", "what": "敷地に置く物：大階段", "status": "open", "to": "tool"},
            {"kind": "tool", "what": "photos exclude のコマンド", "where": "recorder/registry.py", "status": "open"},
            {"kind": "tool", "what": "閉じた依頼", "why": "部品の型", "status": "closed"},
            {"kind": "check", "what": "デッキは棟か敷地か", "status": "open", "to": "tool"},
            {"kind": "check", "what": "扉の位置を確かめて", "status": "open", "to": "staff"},
            {"kind": "photo", "what": "北面を撮る", "status": "open"}]
    _b(tmp_path, requests=reqs, soten=[{"status": "open", "rule": "止める理由 4。規則で決まらない"},
                                       {"status": "decided", "rule": "検収 C の 1"}])
    d = agents.detect_events("b1", tmp_path, write=False)
    s = d["summary"]
    assert s["vocab"] == 0 and s["site"] == 1 and s["tool"] == 1 and s["check"] == 1 and s["stuck"] == 1
    assert s["human"] == 2                                   # 職員宛ての check と撮影依頼
    by = {e["kind"]: e for e in d["events"]}
    assert by["site"]["call"] == "record" and by["tool"]["call"] == "record" and by["check"]["call"] == "fable"
    assert "open のまま" in by["stuck"]["why"]               # rule が埋まっていても open は stuck（J-028）
    # 止めるのは stuck だけ（site・tool・check は止めない）
    assert [e["kind"] for e in agents._stop_events(d, [])] == ["stuck"]


def _line(what, d, weak=False):
    return {"what": what, "diff_m": d, "ok": abs(d) <= 0.15, "weak": weak}


def test_face_ng_lines_groups():
    # 写真 A1・A2 は根 A の組、B1 は根 B、C1 は根 C
    roots = {"A1": "A", "A2": "A", "B1": "B", "C1": "C"}
    summ = {"tol_m": 0.15, "photos": [
        {"photo": "A1", "face": "f1", "lines": [_line("甲板", 0.5), _line("帯", 0.4), _line("縁", 0.9, weak=True), _line("一組", 0.6)]},
        {"photo": "A2", "face": "f1", "lines": [_line("甲板", 0.6), _line("帯", 0.3), _line("一組", 0.6)]},
        {"photo": "B1", "face": "f1", "lines": [_line("甲板", 0.05), _line("帯", 0.3)]},
        {"photo": "C1", "face": "f1", "lines": [_line("甲板", 0.02), _line("帯", 0.02)]},
    ]}
    out = agents.face_ng_lines(summ, roots)
    # 甲板：外れるのは 1 組（A）だけ → 数えない。帯：A と B の 2 組（3 組の過半）→ 数える。
    # 縁：weak は数えない。一組：見ているのが 1 組だけ → 数えない
    assert set(out) == {"f1"} and set(out["f1"]) == {"帯"}
    assert out["f1"]["帯"]["groups"] == 3 and out["f1"]["帯"]["groups_ng"] == 2


def test_stuck_by_face_and_kenshu_pass(tmp_path):
    _b(tmp_path)
    reg = {"entries": [{"photo": "P1", "building": "b1"}, {"photo": "P2", "building": "b1"},
                       {"photo": "P3", "building": "b1", "via": ["P1", "P2"]}]}
    (tmp_path / "data/photos").mkdir(parents=True)
    (tmp_path / "data/photos/registry.json").write_text(json.dumps(reg), encoding="utf-8")
    summ = {"tol_m": 0.15, "photos": [{"photo": p, "face": f, "lines": [_line("帯", 0.4)]}
                                      for p in ("P1", "P2", "P3") for f in ("f1", "f2")]}
    for name in ("20261107_100000", "20261107_110000"):
        (tmp_path / f"out/b1/recheck/{name}").mkdir(parents=True)
        (tmp_path / f"out/b1/recheck/{name}/summary.json").write_text(json.dumps(summ, ensure_ascii=False), encoding="utf-8")
    d = agents.detect_events("b1", tmp_path, write=False)
    st = [e for e in d["events"] if e["kind"] == "stuck"]
    assert len(st) == 1 and set(st[0]["evidence"]["faces"]) == {"f1", "f2"} and "2 面" in st[0]["why"]
    # 検収係が再検収の後に f1 を合格にした → f1 は数えない
    (tmp_path / "data/reports/b1").mkdir(parents=True)
    (tmp_path / "data/reports/b1/kenshu_summary.json").write_text(json.dumps(
        {"made_at": "2026-11-07T12:00:00+09:00", "faces": {"f1": {"pass": True}, "f2": {"pass": False}}}), encoding="utf-8")
    st = [e for e in agents.detect_events("b1", tmp_path, write=False)["events"] if e["kind"] == "stuck"]
    assert set(st[0]["evidence"]["faces"]) == {"f2"} and st[0]["evidence"]["passed_by_kenshu"] == ["f1"]
    # 検収の判定が再検収より古ければ使わない
    (tmp_path / "data/reports/b1/kenshu_summary.json").write_text(json.dumps(
        {"made_at": "2026-11-07T10:30:00", "faces": {"f1": {"pass": True}}}), encoding="utf-8")
    st = [e for e in agents.detect_events("b1", tmp_path, write=False)["events"] if e["kind"] == "stuck"]
    assert set(st[0]["evidence"]["faces"]) == {"f1", "f2"}


def _ok(tokens=100, cost=0.01, sid="S"):
    return {"text": "ok", "input_tokens": tokens, "output_tokens": 10, "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 50, "cost_usd": cost, "turns": 1, "is_error": False, "stop": "success", "session_id": sid}


def test_retry_429_then_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    waits, seen = [], []
    monkeypatch.setattr(agents, "_sleep", waits.append)
    n = {"i": 0}

    def fake(spec, task, cwd, max_turns, max_budget_usd, resume=None):
        seen.append((resume, task))
        n["i"] += 1
        if n["i"] <= 2:
            raise agents.InvokeError("HTTP 429 rate_limit", session_id="S1", rate_limited=True)
        return _ok(sid="S1")

    monkeypatch.setattr(agents, "_invoke", fake)
    r = agents.run_agent("kazoe", "数える", cwd=tmp_path, agents_dir=".claude/agents", building_id="b1")
    assert waits == [60.0, 120.0] and len(r["calls"]) == 3 and r["session_id"] == "S1"
    assert seen[0] == (None, "数える") and seen[1][0] == "S1" and "続きから" in seen[1][1] and "数える" in seen[1][1]
    assert r["input_tokens"] == 100 and r["stop"] == "success" and not r["is_error"] and "呼び出し 3 回" in r["note"]
    assert agents.is_rate_limit(RuntimeError("You have hit your 5-hour limit")) and not agents.is_rate_limit(ValueError("x"))


def test_rate_limit_stop_and_resume(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(agents, "_sleep", lambda s: None)
    calls = []
    mode = {"fail": True}

    def fake(spec, task, cwd, max_turns, max_budget_usd, resume=None):
        calls.append((spec["name"], resume))
        if spec["name"] == "kazoe" and mode["fail"]:
            raise agents.InvokeError("429 Too Many Requests", session_id="KZ", rate_limited=True)
        return _ok(sid=f"S-{spec['name']}")

    monkeypatch.setattr(agents, "_invoke", fake)
    r = agents.run_pipeline("b1", steps=["kansatsu", "kazoe", "sokuryo"], cwd=tmp_path, agents_dir=".claude/agents")
    assert r["stopped_early"] and r["stopped"]["reason"] == "rate_limit" and r["stopped"]["step"] == "kazoe"
    assert r["stopped"]["session_id"] == "KZ" and r["done"] == ["kansatsu"]
    assert len([c for c in calls if c[0] == "kazoe"]) == agents.RETRY_MAX + 1
    st = agents.load_state("b1", tmp_path)
    assert st["status"] == "stopped" and st["sessions"] == {"kansatsu": "S-kansatsu", "kazoe": "KZ"}
    assert len(st["usage_calls"]) == 1 + agents.RETRY_MAX + 1
    # dry_run の再開：計画だけ（状態は書かない）。止まった工程は同じ会話の続きから
    dr = agents.resume_pipeline("b1", dry_run=True, cwd=tmp_path, agents_dir=".claude/agents")
    assert [s["step"] for s in dr["steps"][-2:]] == ["kazoe", "sokuryo"] and dr["steps"][-2]["resume"] == "KZ"
    assert dr["steps"][-1]["resume"] is None and dr["state_preview"]["done"] == ["kansatsu", "kazoe", "sokuryo"]
    assert agents.load_state("b1", tmp_path)["status"] == "stopped"
    # 本当の再開：同じ記録の名前に足す
    mode["fail"] = False
    calls.clear()
    r2 = agents.resume_pipeline("b1", cwd=tmp_path, agents_dir=".claude/agents")
    assert calls == [("kazoe", "KZ"), ("sokuryo", None)]
    assert not r2["stopped_early"] and r2["done"] == ["kansatsu", "kazoe", "sokuryo"] and r2["record"] == r["record"]
    rec = json.loads(open(r2["record"], encoding="utf-8").read())
    assert [s["step"] for s in rec["steps"]] == ["kansatsu", "kazoe", "kazoe", "sokuryo"]
    assert [x["kind"] for x in rec["runs"]] == ["start", "resume"] and rec["runs"][1]["reason"] == "rate_limit"
    assert rec["steps"][2]["attempt"] == 2
    assert agents.resume_pipeline("b1", cwd=tmp_path, agents_dir=".claude/agents")["nothing"]


def test_resume_after_crash_mid_step(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    st = agents._new_state("b1", "full", None, ["kansatsu", "kazoe"], None, None)
    st.update(status="running", current="kazoe", done=["kansatsu"], sessions={"kazoe": "K9"})
    agents._save_state(st, tmp_path)
    seen = []
    monkeypatch.setattr(agents, "_invoke", lambda spec, task, *a, resume=None: seen.append((spec["name"], resume)) or _ok())
    r = agents.resume_pipeline("b1", cwd=tmp_path, agents_dir=".claude/agents")
    assert seen == [("kazoe", "K9")] and r["runs"][-1]["reason"] == "crash" and r["done"] == ["kansatsu", "kazoe"]


def test_resume_after_event(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    _b(tmp_path, soten=[{"status": "open", "rule": "規則で決まらない"}])
    monkeypatch.setattr(agents, "_invoke", lambda spec, task, *a, resume=None: _ok())
    r = agents.run_pipeline("b1", steps=["kansatsu", "kazoe", "sokuryo"], cwd=tmp_path, agents_dir=".claude/agents")
    assert r["stopped"]["reason"] == "event" and r["stopped"]["step"] == "kansatsu" and r["done"] == ["kansatsu"]
    # 進行役が見て続けると決めた → 同じ事件では止めない
    r2 = agents.resume_pipeline("b1", cwd=tmp_path, agents_dir=".claude/agents")
    assert not r2["stopped_early"] and r2["done"] == ["kansatsu", "kazoe", "sokuryo"]


def test_max_usd(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    budgets = []

    def fake(spec, task, cwd, max_turns, max_budget_usd, resume=None):
        budgets.append(max_budget_usd)
        return _ok(cost=0.6)

    monkeypatch.setattr(agents, "_invoke", fake)
    r = agents.run_pipeline("b1", steps=["kansatsu", "kazoe", "sokuryo"], cwd=tmp_path, agents_dir=".claude/agents", max_usd=1.0)
    assert budgets == [1.0, 0.4]                 # 1 回の上限は棟の上限の残り
    assert r["stopped"]["reason"] == "max_usd" and r["spent_usd"] == pytest.approx(1.2) and r["done"] == ["kansatsu", "kazoe"]
    rec = json.loads(open(r["record"], encoding="utf-8").read())
    assert rec["stopped"]["reason"] == "max_usd" and rec["usage_calls_total"]["input_tokens"] == 200
    with pytest.raises(agents.AgentError):
        agents.resume_pipeline("b1", cwd=tmp_path, agents_dir=".claude/agents")
    r2 = agents.resume_pipeline("b1", cwd=tmp_path, agents_dir=".claude/agents", max_usd=5.0)
    assert not r2["stopped_early"] and budgets[-1] == pytest.approx(3.8)


def test_continue_dry_run(tmp_path):
    with pytest.raises(agents.AgentError):
        agents.continue_pipeline("b1", "メジャー/本部棟_honbu", dry_run=True, cwd=tmp_path, agents_dir=".claude/agents")
    _b(tmp_path)
    r = agents.continue_pipeline("b1", "メジャー/本部棟_honbu", faces=["f6"], dry_run=True, cwd=tmp_path,
                                 agents_dir=".claude/agents")
    assert [s["step"] for s in r["steps"]] == agents.CONTINUE_STEPS and r["mode"] == "continue"
    by = {s["step"]: s for s in r["steps"]}
    for st in ("satsuei", "shiwake", "sokuryo", "sekkei", "kenshu"):
        t = by[st]["task"]
        assert t.startswith(agents.step_task(st, "b1", "メジャー/本部棟_honbu")) and "【追記】" in t
        assert "前の値と差を method" in t and "--zero にしない" in t and "面 f6" in t
    assert "satsuei_answers.json" in by["satsuei"]["task"] and "answered" in by["sekkei"]["task"]
    assert "recorder photos import メジャー/本部棟_honbu --period during" in by["satsuei"]["task"]
    assert by["seizu"]["task"] == "recorder run data/buildings/b1.json --fast"
    assert r["state_preview"]["mode"] == "continue" and r["state_preview"]["faces"] == ["f6"]
    assert not (tmp_path / "out/b1/pipeline_state.json").exists()


def test_continue_keeps_previous_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    _b(tmp_path)
    monkeypatch.setattr(agents, "_invoke", lambda spec, task, *a, resume=None: _ok())
    old = agents._new_state("b1", "full", None, ["kansatsu"], None, None)
    old.update(status="stopped", record_name="pipeline_20261107_083839.json")
    agents._save_state(old, tmp_path)
    r = agents.continue_pipeline("b1", None, steps=["satsuei", "shiwake"], cwd=tmp_path, agents_dir=".claude/agents",
                                 max_usd=2)
    assert (tmp_path / "out/b1/pipeline_state_20261107_083839.json").exists()
    st = agents.load_state("b1", tmp_path)
    assert st["mode"] == "continue" and st["status"] == "done" and st["max_usd"] == 2 and r["runs"][0]["previous_state"]


def test_continue_known_events_do_not_stop(tmp_path):
    _b(tmp_path, requests=[{"kind": "asset", "what": "長机の見た目", "status": "open"}])
    r = agents.continue_pipeline("b1", None, steps=["satsuei", "shiwake"], dry_run=True, cwd=tmp_path, agents_dir=".claude/agents")
    assert not r["stopped_early"] and "アセット" in r["runs"][0]["known_events"][0]
    # 最初からの pipeline では止まる
    r2 = agents.run_pipeline("b1", steps=["satsuei", "shiwake"], dry_run=True, cwd=tmp_path, agents_dir=".claude/agents")
    assert r2["stopped"]["reason"] == "event"


def test_step_task_takes_campus_at_run_time(tmp_path, monkeypatch):
    """依頼文に敷地 JSON の道と scope.targets を実行時に差し込む（名城の tempaku.json・4 棟を直書きしない。監査 A 12・13）。"""
    (tmp_path / "data" / "site").mkdir(parents=True)
    (tmp_path / "data" / "site" / "x-univ.json").write_text(json.dumps(
        {"campus": "x-univ", "university": "X 大学", "scope": {"targets": ["a1", "b2"]}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.delenv(agents.CAMPUS_ENV, raising=False)
    assert agents.campus_slug(tmp_path) == "x-univ"
    for st in ("satsuei", "shiwake", "kansatsu", "sokuryo", "sekkei"):
        t = agents.step_task(st, "a1", cwd=tmp_path)
        assert "data/site/x-univ.json" in t and "a1・b2" in t and "tempaku" not in t and "名城" not in t
    assert "--building a1" in agents.step_task("shiwake", "a1", cwd=tmp_path)
    monkeypatch.setenv(agents.CAMPUS_ENV, "y-campus")    # recorder serve が画面で選んだ敷地
    t = agents.step_task("kansatsu", "a1", cwd=tmp_path)
    assert "data/site/y-campus.json" in t and "未設定" in t
    monkeypatch.delenv(agents.CAMPUS_ENV)
    (tmp_path / "data" / "site" / "x-univ.json").unlink()
    t = agents.step_task("kansatsu", "a1", cwd=tmp_path)
    assert "まだ決まっていない" in t and "推測しない" in t


def test_continue_satsuei_mentions_relink():
    t = agents.continue_task("satsuei", "b1", "メジャー/b1")
    assert "photos relink <フォルダ> --assign-from-folder" in t


def test_permission_none_is_settled(tmp_path, monkeypatch):
    monkeypatch.delenv(agents.CAMPUS_ENV, raising=False)
    p = tmp_path / "data" / "site" / "c.json"
    p.parent.mkdir(parents=True)
    for status, n in (("none", 0), ("granted", 0), (None, 1), ("asked", 1)):
        p.write_text(json.dumps({"scope": {"targets": ["b1"]},
                                 "sources": {"campus_map": {"permission": {"status": status}}}}), encoding="utf-8")
        assert len(agents._site_events(tmp_path)) == n, status
