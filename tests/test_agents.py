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
    # 1 回の費用の上限 → 自動の続き 2 回 → 3 回目も止まったので止める（人を呼ぶ事件に出す）
    assert r["stopped_early"] and len(r["steps"]) == 1 + agents.AUTO_RESUME_MAX and r["steps"][0]["stopped_by_limit"]
    assert r["stopped"]["reason"] == "limit" and r["stopped"]["auto_resumes"] == agents.AUTO_RESUME_MAX
    assert [e["kind"] for e in r["auto_resumes"]["shiwake"]] == ["max_budget", "max_budget"]
    assert any(e["kind"] == "human" and "shiwake" in e["why"] for e in r["pending_events"])


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
    assert seen[0][0] is None and seen[0][1].endswith("\n\n数える") and "予算（手数 30・" in seen[0][1]
    assert seen[1][0] == "S1" and "続きから" in seen[1][1] and "数える" in seen[1][1]
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
    n_kz = (agents.RETRY_MAX + 1) * (1 + agents.AUTO_RESUME_MAX)       # やり直し 3 回 × 自動の続き 2 回の後に止まる
    assert len([c for c in calls if c[0] == "kazoe"]) == n_kz
    st = agents.load_state("b1", tmp_path)
    assert st["status"] == "stopped" and st["sessions"] == {"kansatsu": "S-kansatsu", "kazoe": "KZ"}
    assert len(st["usage_calls"]) == 1 + n_kz and [e["kind"] for e in st["auto_resumes"]["kazoe"]] == ["rate_limit"] * 2
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
    assert [s["step"] for s in rec["steps"]] == ["kansatsu"] + ["kazoe"] * (2 + agents.AUTO_RESUME_MAX) + ["sokuryo"]
    assert [x["kind"] for x in rec["runs"]] == ["start", "resume"] and rec["runs"][1]["reason"] == "rate_limit"
    assert rec["steps"][-2]["attempt"] == 2 + agents.AUTO_RESUME_MAX
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


def test_resume_redo_from_with_decision(tmp_path, monkeypatch):
    """F18：事件で止まった後、進行役の決定を渡して、同じ state・同じ記録のまま指定の工程から新しい会話でやり直す。"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    _b(tmp_path, requests=[{"kind": "check", "what": "デッキは棟か敷地か", "status": "open", "to": "tool"}],
       soten=[{"status": "open", "rule": "規則で決まらない"}])
    seen = []

    def fake(spec, task, cwd, max_turns, max_budget_usd, resume=None):
        seen.append((spec["name"], resume, task))
        return {**_ok(sid=f"S{len(seen)}-{spec['name']}"), "cost_usd": 0.5}

    monkeypatch.setattr(agents, "_invoke", fake)
    steps = ["kansatsu", "kazoe", "sokuryo"]
    r = agents.run_pipeline("b1", steps=steps, cwd=tmp_path, agents_dir=".claude/agents")
    assert r["stopped"]["reason"] == "event" and r["done"] == ["kansatsu"]
    r = agents.resume_pipeline("b1", cwd=tmp_path, agents_dir=".claude/agents")       # 事件を見て続けた → 最後まで
    assert r["done"] == steps and agents.load_state("b1", tmp_path)["status"] == "done"
    st0 = agents.load_state("b1", tmp_path)
    old_sessions, rec_name = dict(st0["sessions"]), st0["record_name"]
    assert st0["spent_usd"] == pytest.approx(1.5) and any(e["kind"] == "check" for e in st0["pending_events"])
    # 決定のファイルの断り：無い・リポジトリの外・拡張子・知らない工程
    for bad in ({"decision_file": "data/reports/無い.md"}, {"decision_file": "../x.md"},
                {"redo_from": "mejirushi"}):
        with pytest.raises(agents.AgentError):
            agents.resume_pipeline("b1", cwd=tmp_path, agents_dir=".claude/agents", **bad)
    dec = tmp_path / "data/reports/指示/決定.md"
    dec.parent.mkdir(parents=True)
    dec.write_text("﻿数えからやり直す。\nf5 は estimated のまま、撮影依頼を 1 件。", encoding="utf-8")
    (tmp_path / "data/reports/指示/決定.json").write_text("{}", encoding="utf-8")
    with pytest.raises(agents.AgentError):
        agents.resume_pipeline("b1", cwd=tmp_path, agents_dir=".claude/agents", decision_file="data/reports/指示/決定.json")
    # dry_run：計画だけ（状態は書かない）
    dr = agents.resume_pipeline("b1", dry_run=True, cwd=tmp_path, agents_dir=".claude/agents", redo_from="kazoe",
                                decision_file="data/reports/指示/決定.md")
    assert [s["step"] for s in dr["steps"][-2:]] == ["kazoe", "sokuryo"] and dr["steps"][-1]["resume"] is None
    assert dr["steps"][-1]["task"].startswith("進行役の決定（data/reports/指示/決定.md）：\n数えからやり直す。")
    assert agents.load_state("b1", tmp_path)["done"] == steps
    seen.clear()
    r2 = agents.resume_pipeline("b1", cwd=tmp_path, agents_dir=".claude/agents", redo_from="kazoe",
                                decision_file="data/reports/指示/決定.md")
    assert [(n, res) for n, res, _t in seen] == [("kazoe", None), ("sokuryo", None)]      # 新しい会話で
    head = agents.DECISION_HEAD.format(file="data/reports/指示/決定.md", text="数えからやり直す。\nf5 は estimated のまま、撮影依頼を 1 件。")
    for _n, _r, t in seen:       # 前置き（予算）の後、依頼文の先頭に決定の全文
        assert t.index("予算（") == 0 and ("\n\n" + head) in t and t.index(head) < t.index("対象の棟は b1")
    st = agents.load_state("b1", tmp_path)
    assert not r2["stopped_early"] and st["status"] == "done" and st["done"] == steps
    assert st["record_name"] == rec_name and r2["record"].endswith(rec_name)
    assert st["spent_usd"] == pytest.approx(2.5) and st["attempts"] == {"kansatsu": 1, "kazoe": 2, "sokuryo": 2}
    assert st["sessions"]["kansatsu"] == old_sessions["kansatsu"] and st["sessions"]["kazoe"] != old_sessions["kazoe"]
    assert st["redo"]["previous_sessions"] == {"kazoe": old_sessions["kazoe"], "sokuryo": old_sessions["sokuryo"]}
    d = st["decisions"][-1]
    assert d["file"] == "data/reports/指示/決定.md" and d["redo_from"] == "kazoe" and d["text"].startswith("数えから")
    assert any(a.startswith("check|") for a in d["acknowledged"]) and any(a.startswith("check|") for a in st["acknowledged_events"])
    assert st["runs"][-1]["kind"] == "resume" and st["runs"][-1]["redo_from"] == "kazoe"
    assert st["runs"][-1]["decision_file"] == "data/reports/指示/決定.md"
    rec = json.loads(open(r2["record"], encoding="utf-8").read())
    assert [s["step"] for s in rec["steps"]] == steps + ["kazoe", "sokuryo"] and len(rec["runs"]) == 3
    # 決定だけ（redo_from 無し）で最後まで済んだ記録は続ける所が無い
    assert agents.resume_pipeline("b1", cwd=tmp_path, agents_dir=".claude/agents")["nothing"]


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


def _idx(tmp_path, photos):
    (tmp_path / "data/photos").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data/photos/index.json").write_text(json.dumps({"photos": photos}, ensure_ascii=False), encoding="utf-8")


def test_continue_keeps_previous_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    _b(tmp_path)
    _idx(tmp_path, [{"id": "N1", "building": "b1", "assigned_by": "staff"}])
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
    _idx(tmp_path, [{"id": "N1", "building": "b1", "assigned_by": "staff"}])
    r = agents.continue_pipeline("b1", None, steps=["satsuei", "shiwake"], dry_run=True, cwd=tmp_path, agents_dir=".claude/agents")
    assert not r["stopped_early"] and any("アセット" in w for w in r["runs"][0]["known_events"])
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


def test_invoke_reads_sdk_result_cost_and_usage(tmp_path, monkeypatch):
    """_invoke が SDK の ResultMessage から total_cost_usd と usage を拾い、大きな道具の結果用に max_buffer_size を渡す。"""
    import claude_agent_sdk as sdk

    seen = {}

    async def fake_query(prompt, options):
        seen["buf"] = options.max_buffer_size
        yield sdk.ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=2,
                                session_id="s1", total_cost_usd=0.123, result="ok",
                                usage={"input_tokens": 100, "output_tokens": 20, "cache_creation_input_tokens": 5,
                                       "cache_read_input_tokens": 7})

    monkeypatch.setattr(sdk, "query", fake_query)
    spec = {"system_prompt": "x", "model_id": "claude-haiku-5-5", "tools": ["Read"]}
    r = agents._invoke(spec, "t", tmp_path, 3, 1.0)
    assert seen["buf"] == agents.SDK_MAX_BUFFER_SIZE and agents.SDK_MAX_BUFFER_SIZE > 1048576
    assert r["cost_usd"] == pytest.approx(0.123) and r["input_tokens"] == 100 and r["output_tokens"] == 20
    assert r["token_note"] is None and r["session_id"] == "s1"
    # 単価表が無くても、SDK の total_cost_usd が棟の使った額（spent_usd）に入る
    assert agents._rec_cost({"cost_usd_estimate": None, "cost_usd": r["cost_usd"]}) == pytest.approx(0.123)


def test_invoke_error_after_result_keeps_cost(tmp_path, monkeypatch):
    """SDK は is_error の ResultMessage（max turns）を yield してから ResultError を投げる。その呼び出しの費用・トークン数・
    turns を捨てず、記録の calls と cost に入れて棟の使った額（spent_usd）に足す。止まった理由は今まで通り記録し、会話の id は残す。"""
    import claude_agent_sdk as sdk

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.delenv(agents.PRICES_ENV, raising=False)
    msg = "Claude Code returned an error result: Reached maximum number of turns (30)"

    async def fake_query(prompt, options):
        yield sdk.ResultMessage(subtype="error_max_turns", duration_ms=1, duration_api_ms=1, is_error=True, num_turns=30,
                                session_id="SK", total_cost_usd=1.25, result=None,
                                usage={"input_tokens": 300, "output_tokens": 40})
        raise sdk.ResultError(msg, data={"subtype": "error_max_turns", "is_error": True}, exit_code=1)

    monkeypatch.setattr(sdk, "query", fake_query)
    with pytest.raises(agents.InvokeError) as ei:
        agents._invoke({"system_prompt": "x", "model_id": "claude-sonnet-5-5", "tools": ["Read"]}, "t", tmp_path, 30, 5.0)
    assert ei.value.session_id == "SK" and ei.value.result["cost_usd"] == pytest.approx(1.25)
    assert ei.value.result["turns"] == 30 and ei.value.result["stop"] == "error_max_turns"

    r = agents.run_pipeline("b1", steps=["sokuryo"], cwd=tmp_path, agents_dir=".claude/agents", max_usd=20.0)
    rec = r["steps"][0]
    st = agents.load_state("b1", tmp_path)
    assert st["spent_usd"] == pytest.approx(1.25) and st["sessions"]["sokuryo"] == "SK"
    c = st["usage_calls"][0]
    assert c["cost_usd"] == pytest.approx(1.25) and c["input_tokens"] == 300 and c["turns"] == 30
    assert "maximum number of turns" in c["error"] and c["sdk_stop"] == "error_max_turns"
    assert rec["cost_usd"] == pytest.approx(1.25) and rec["turns"] == 30 and "maximum number of turns" in rec["error"]
    assert rec["stop"] == "crash" and r["stopped_early"]
    # 結果が届く前に落ちた時は今まで通り null
    async def crash_query(prompt, options):
        raise RuntimeError("boom")
        yield  # noqa: unreachable（async generator にするため）

    monkeypatch.setattr(sdk, "query", crash_query)
    with pytest.raises(agents.InvokeError) as e2:
        agents._invoke({"system_prompt": "x", "model_id": "claude-sonnet-5-5", "tools": ["Read"]}, "t", tmp_path, 30, 5.0)
    assert e2.value.result is None


# ---- 自動の続き（11/8、F13）：落ちた・手数の上限・1 回の費用の上限・429・係が区切った → 同じ会話の続きから自動で（2 回まで） ----
def _max_turns_err(sid="SK", total=1.0, turns=60):
    return agents.InvokeError("ResultError: Claude Code returned an error result: Reached maximum number of turns (60)",
                              session_id=sid, result={"cost_usd": total, "turns": turns, "stop": "error_max_turns",
                                                      "input_tokens": 100, "output_tokens": 10})


def test_limits_per_agent():
    for a in ("sokuryo", "sekkei", "kenshu"):
        assert agents.agent_limits(a, "opus") == {"max_turns": 60, "max_budget_usd": 4.0}
    assert agents.agent_limits("kansatsu", "opus")["max_turns"] == 30 and agents.agent_limits("satsuei", "haiku")["max_turns"] == 12


def test_auto_resume_after_max_turns(tmp_path, monkeypatch):
    """測量係が手数の上限で止まる → 人の手なしで同じ会話の続きから → 通る。費用は会話の合計から 1 回分に直して二重に数えない。"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.delenv(agents.PRICES_ENV, raising=False)
    seen = []

    def fake(spec, task, cwd, max_turns, max_budget_usd, resume=None):
        seen.append((spec["name"], resume, max_turns, task))
        if spec["name"] == "sokuryo" and resume is None:
            raise _max_turns_err(total=1.0)
        if spec["name"] == "sokuryo":
            return {**_ok(sid="SK"), "cost_usd": 1.6, "turns": 12}     # CLI は resume で会話の合計（1.0＋0.6）を返す
        return _ok(sid="SS", cost=0.01)

    monkeypatch.setattr(agents, "_invoke", fake)
    r = agents.run_pipeline("b1", steps=["sokuryo", "sekkei"], cwd=tmp_path, agents_dir=".claude/agents", max_usd=20.0)
    assert not r["stopped_early"] and r["done"] == ["sokuryo", "sekkei"]
    assert [(n, s, t) for n, s, t, _ in seen] == [("sokuryo", None, 60), ("sokuryo", "SK", 60), ("sekkei", None, 60)]
    t2 = seen[1][3]
    assert "自動の続き 1/2" in t2 and "手数の上限" in t2 and "手数 60 手・費用 1.00 USD" in t2 and "棟の残り 19.00 USD" in t2
    st = agents.load_state("b1", tmp_path)
    assert st["spent_usd"] == pytest.approx(1.0 + 0.6 + 0.01)               # 1.6 を足して 2.6 にしない
    assert [c["cost_usd"] for c in st["usage_calls"]] == pytest.approx([1.0, 0.6, 0.01])
    assert st["usage_calls"][1]["cost_usd_sdk_total"] == pytest.approx(1.6)
    a = st["auto_resumes"]["sokuryo"]
    assert len(a) == 1 and a[0]["kind"] == "max_turns" and a[0]["result"] == "ok" and a[0]["session_id"] == "SK"
    rec = json.loads(open(r["record"], encoding="utf-8").read())
    assert rec["auto_resumes_total"] == 1 and rec["auto_resumes"]["sokuryo"][0]["label"] == "手数の上限"
    assert [s.get("auto_resume") for s in rec["steps"]] == [None, 1, None]
    # 戻されなかった（報告が前の合計より小さい）時は、そのまま 1 回の分
    sc = {}
    assert agents._own_cost(1.0, None, "X", sc) == (1.0, None)
    assert agents._own_cost(0.3, "X", "X", sc) == (0.3, None) and sc["X"] == pytest.approx(1.3)


def test_auto_resume_gives_up_after_three_stops(tmp_path, monkeypatch):
    """3 回目に止まったら今まで通り止めて state に記録し、人を呼ぶ事件に出す。手で resume すれば同じ会話から。"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.delenv(agents.PRICES_ENV, raising=False)
    seen, total = [], {"v": 0.0}

    def fake(spec, task, cwd, max_turns, max_budget_usd, resume=None):
        seen.append(resume)
        total["v"] += 0.5
        raise _max_turns_err(sid="S", total=total["v"])          # 会話の合計 0.5・1.0・1.5

    monkeypatch.setattr(agents, "_invoke", fake)
    r = agents.run_pipeline("b1", steps=["kenshu"], cwd=tmp_path, agents_dir=".claude/agents", max_usd=20.0)
    assert seen == [None, "S", "S"] and r["stopped_early"] and r["stopped"]["reason"] == "crash"
    assert r["stopped"]["auto_resumes"] == 2 and "自動の続き 2 回" in r["stopped"]["why"]
    assert r["spent_usd"] == pytest.approx(1.5)                              # 0.5＋1.0＋1.5＝3.0 にしない
    st = agents.load_state("b1", tmp_path)
    assert st["status"] == "stopped" and [e["result"] for e in st["auto_resumes"]["kenshu"]] == ["crash", "crash"]
    ev = [e for e in st["pending_events"] if e["kind"] == "human"]
    assert len(ev) == 1 and ev[0]["call"] == "human" and "kenshu" in ev[0]["why"]
    seen.clear()
    monkeypatch.setattr(agents, "_invoke", lambda spec, task, *a, resume=None: seen.append(resume) or {**_ok(sid="S"), "cost_usd": 1.7})
    r2 = agents.resume_pipeline("b1", cwd=tmp_path, agents_dir=".claude/agents")
    assert seen == ["S"] and not r2["stopped_early"] and r2["spent_usd"] == pytest.approx(1.7)


def test_auto_resume_respects_max_usd(tmp_path, monkeypatch):
    """棟の費用の上限に届いていたら自動で続けない（今の上限の判定）。"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.delenv(agents.PRICES_ENV, raising=False)
    seen = []

    def fake(spec, task, cwd, max_turns, max_budget_usd, resume=None):
        seen.append(resume)
        raise _max_turns_err(total=1.0)

    monkeypatch.setattr(agents, "_invoke", fake)
    r = agents.run_pipeline("b1", steps=["sokuryo"], cwd=tmp_path, agents_dir=".claude/agents", max_usd=1.0)
    assert seen == [None] and r["stopped"]["reason"] == "max_usd" and r["spent_usd"] == pytest.approx(1.0)
    assert "自動で続けない" in r["stopped"]["why"] and not r["auto_resumes"].get("sokuryo") and r["auto_resumes_total"] == 0


def test_prompt_states_limits_and_partial_continues(tmp_path, monkeypatch):
    """依頼文の先頭に手数・1 回の費用・棟の残りと 8 割で区切る決まりが入る（棟の名前は入れない）。
    係が【続きあり】で区切ったら工程は済んでいないので、同じ会話の続きから自動で。"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.delenv(agents.PRICES_ENV, raising=False)
    seen = []

    def fake(spec, task, cwd, max_turns, max_budget_usd, resume=None):
        seen.append((resume, task, max_budget_usd))
        if resume is None:
            return {**_ok(sid="SP", cost=0.4), "turns": 48, "text": f"{agents.CONTINUE_MARK}\n- 面 f2 の検算が残り"}
        return {**_ok(sid="SP"), "cost_usd": 0.5}

    monkeypatch.setattr(agents, "_invoke", fake)
    r = agents.run_pipeline("zz9", steps=["sokuryo"], cwd=tmp_path, agents_dir=".claude/agents", max_usd=3.0)
    hdr = seen[0][1].split("\n\n")[0]
    assert hdr.startswith("予算（手数 60・1 回の費用 3.00 USD・棟の残り 3.00 USD）はこれだけ。")
    assert "上限の 8 割を使ったら途中を保存し" in hdr and agents.CONTINUE_MARK in hdr
    assert "zz9" not in hdr and seen[0][2] == 3.0
    assert not r["stopped_early"] and [s for s, _, _ in seen] == [None, "SP"]
    h2 = seen[1][1].split("\n\n")[0]
    assert "手数 48 手・費用 0.40 USD" in h2 and "棟の残り 2.60 USD" in h2 and "係が上限の 8 割で区切った" in seen[1][1]
    assert r["auto_resumes"]["sokuryo"][0]["kind"] == "partial" and r["spent_usd"] == pytest.approx(0.5)
    dr = agents.run_agent("sokuryo", "x", dry_run=True, max_budget_usd=2.0)
    assert dr["max_turns"] == 60 and "1 回の費用 2.00 USD" in dr["limits_header"]


def test_preamble_text_and_not_for_code_steps():
    """係の依頼文の共通の前置き（予算・試行錯誤 2 回・省いてよい物／いけない物・8 割で【続きあり】）。コードの工程には付けない。"""
    h = agents.limits_header(60, 3.0, None)
    assert h == ("予算（手数 60・1 回の費用 3.00 USD・棟の残り 上限なし）はこれだけ。試行錯誤は同じ事を 2 回までにし、手戻りの無い順で進む。"
                 "省いてよい物：言い直し、同じ確かめの繰り返し、報告の飾り。省いてはいけない物：規約 1・5（根拠の無い寸法を書かない、決めつけない）、"
                 "鎖の各輪の写真 id と ±、2 つ目の物差しでの検算、測れない物の unknown と撮影依頼、台帳を道具でだけ書くこと。"
                 "上限の 8 割を使ったら途中を保存し、返事の 1 行目を【続きあり】にして残りを箇条書きで終える。\n\n")
    h2 = agents.limits_header(60, 1.5, 4.0, used_turns=48, used_usd=0.4)
    assert "棟の残り 4.00 USD" in h2 and "これまでに使ったのは 手数 48 手・費用 0.40 USD" in h2 and h2.endswith("\n\n")
    assert agents.run_agent("seizu", "x", dry_run=True, max_budget_usd=2.0)["limits_header"] == ""
    assert agents.run_agent("kenshu", "x", dry_run=True, max_budget_usd=2.0)["limits_header"].startswith("予算（")


def test_find_new_photos_by_building_json(tmp_path):
    """写真フォルダ無しの追記：索引でこの棟の写真のうち、前回の棟 JSON に id が出てこない物が新しい写真。
    id が別の id の頭と同じでも取り違えない。メジャーは分けて数える。"""
    (tmp_path / "data/buildings").mkdir(parents=True)
    (tmp_path / "data/buildings/b1.json").write_text(json.dumps({
        "id": "b1", "faces": [{"h": {"value": 3.4, "status": "measured",
                                     "evidence": {"photos": ["P1"], "chain": [{"photo": "柱92cm"}]}}}],
        "requests": [{"kind": "photo", "status": "answered", "answered_by": ["P2"]}]}), encoding="utf-8")   # ascii の \u で書く
    st = {"assigned_by": "staff"}
    _idx(tmp_path, [{"id": "P1", "building": "b1", **st}, {"id": "P2", "building": "b1", **st}, {"id": "柱", "building": "b1", **st},
                    {"id": "柱92cm", "building": "b1", **st}, {"id": "T1", "building": "b1", "ruler": "tape_measure", **st},
                    {"id": "N1", "building": "b1", **st}, {"id": "X1", "building": "b2", **st}, {"id": "P10", "building": "b1", **st},
                    {"id": "OLD1", "building": "b1"}, {"id": "OLD2", "building": "b1", "assigned_by": "shiwake"}])
    f = agents.find_new_photos("b1", tmp_path)      # 職員の写真だけ。assigned_by の無い古い写真・仕分けの写真は新しくない
    assert f == {"ids": ["柱", "T1", "N1", "P10"], "tape": ["T1"], "building_photos": 7}


def test_continue_without_folder_names_new_photos(tmp_path):
    """写真フォルダ無しで始めた追記は、state の new_photos を依頼文に書く（多ければ数と条件）。フォルダが有れば今のまま。"""
    _b(tmp_path, requests=[{"kind": "photo", "answered_by": ["OLD"]}])
    _idx(tmp_path, [{"id": "OLD", "building": "b1", "assigned_by": "staff"}, {"id": "N1", "building": "b1", "assigned_by": "staff"},
                    {"id": "T1", "building": "b1", "ruler": "tape_measure", "assigned_by": "staff"}, {"id": "OLD2", "building": "b1"}])
    r = agents.continue_pipeline("b1", None, dry_run=True, cwd=tmp_path, agents_dir=".claude/agents")
    assert r["state_preview"]["new_photos"] == ["N1", "T1"]
    assert r["runs"][0]["new_photos"] == {"n": 2, "tape": 1, "building_photos": 3}
    by = {s["step"]: s for s in r["steps"]}
    for st in ("satsuei", "shiwake", "sokuryo", "sekkei", "kenshu"):
        t = by[st]["task"]
        assert "新しい写真は 2 枚（うちメジャー（ruler: tape_measure）1 枚）" in t and "id：N1。" in t and "メジャーの写真 id：T1。" in t
        assert "OLD" not in t
    assert by["seizu"]["task"] == "recorder run data/buildings/b1.json --fast"
    r2 = agents.continue_pipeline("b1", "フォルダ/b1", dry_run=True, cwd=tmp_path, agents_dir=".claude/agents")
    assert r2["state_preview"]["new_photos"] is None and "【新しい写真】" not in r2["steps"][0]["task"]
    _idx(tmp_path, [{"id": f"N{i}", "building": "b1", "assigned_by": "staff", **({"ruler": "tape_measure"} if i < 3 else {})}
                    for i in range(40)])
    r3 = agents.continue_pipeline("b1", None, steps=["satsuei"], dry_run=True, cwd=tmp_path, agents_dir=".claude/agents")
    t = r3["steps"][0]["task"]
    assert "新しい写真は 40 枚（うちメジャー（ruler: tape_measure）3 枚）" in t and "N39" not in t
    assert "`recorder photos list --building b1` で出る写真のうち assigned_by が staff で data/buildings/b1.json に id が無い物" in t
    assert "`recorder photos list --building b1 --ruler tape_measure`" in t


def test_continue_without_new_photos_stops_for_human(tmp_path, monkeypatch):
    """新しい写真が 0 枚なら最初の工程の前で止めて人を呼ぶ。人が見て resume すれば続ける。"""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    _b(tmp_path, requests=[{"kind": "photo", "answered_by": ["OLD"]}])
    _idx(tmp_path, [{"id": "OLD", "building": "b1", "assigned_by": "staff"}, {"id": "OLD2", "building": "b1"}])
    calls = []
    monkeypatch.setattr(agents, "_invoke", lambda spec, task, *a, resume=None: calls.append(task) or _ok())
    r = agents.continue_pipeline("b1", None, steps=["satsuei", "shiwake"], cwd=tmp_path, agents_dir=".claude/agents")
    assert calls == [] and r["stopped"]["reason"] == "event" and r["stopped"]["step"] == "satsuei"
    assert "新しい写真が 0 枚" in r["stopped"]["why"]
    st = agents.load_state("b1", tmp_path)
    assert st["status"] == "stopped" and st["new_photos"] == [] and st["done"] == []
    assert [e["kind"] for e in st["pending_events"]] == ["human"] and st["pending_events"][0]["call"] == "human"
    r2 = agents.resume_pipeline("b1", cwd=tmp_path, agents_dir=".claude/agents")
    assert len(calls) == 2 and not r2["stopped_early"] and r2["done"] == ["satsuei", "shiwake"]
