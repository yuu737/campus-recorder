"""自動の進行役（11/8）：止める事件が出たら進行役（shinko）を呼び、決定ファイルを書かせ、その決定で同じ記録のまま続ける。
SDK は呼ばない（agents._invoke をモックする。tests/test_agents.py と同じ型）。"""
import json
import re

import pytest

from recorder import agents

AD = ".claude/agents"
STEPS = ["kansatsu", "kazoe", "sokuryo"]


def _ok(sid="S", cost=0.01, text="ok"):
    return {"text": text, "input_tokens": 100, "output_tokens": 10, "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0, "cost_usd": cost, "turns": 1, "is_error": False, "stop": "success", "session_id": sid}


def _soten(tmp_path, n_open=1, bid="b1"):
    """棟 JSON と、open の争点 n 件（止める事件 stuck「争点 n 件が open のまま」）。"""
    (tmp_path / "data/buildings").mkdir(parents=True, exist_ok=True)
    (tmp_path / f"data/buildings/{bid}.json").write_text(json.dumps({"id": bid, "requests": []}), encoding="utf-8")
    (tmp_path / f"out/{bid}").mkdir(parents=True, exist_ok=True)
    (tmp_path / f"out/{bid}/soten.json").write_text(
        json.dumps({"items": [{"status": "open", "rule": "規則で決まらない"}] * n_open}, ensure_ascii=False), encoding="utf-8")


def _shinko_writes(tmp_path, redo_from=None, summary="測り直す", stop_for_human=False, write=True):
    """進行役の返事：依頼文の決定ファイルの名前に決定を書き、最後の行に JSON。"""
    def answer(task):
        path = re.search(r"決定を新しいファイル (\S+) に書く", task).group(1)
        if write:
            p = tmp_path / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# 自動の決定\n- 争点が open の時は、測量からやり直す（理由：テスト）。", encoding="utf-8")
        line = json.dumps({"decision_file": path, "redo_from": redo_from, "summary": summary,
                           "stop_for_human": stop_for_human}, ensure_ascii=False)
        return _ok(sid="F", cost=0.5, text=f"決めました。\n\n```json\n{line}\n```\n")
    return answer


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(agents, "_sleep", lambda s: None)


def test_stuck_calls_shinko_and_redoes(tmp_path, monkeypatch, key):
    """(a) stuck で進行役が呼ばれ、決定ファイルが書かれ、redo_from から同じ記録のまま続く。"""
    _soten(tmp_path)
    seen, ans = [], _shinko_writes(tmp_path, redo_from="kansatsu")

    def fake(spec, task, cwd, max_turns, max_budget_usd, resume=None):
        seen.append((spec["name"], resume, task, max_turns, max_budget_usd))
        return ans(task) if spec["name"] == "shinko" else _ok(sid=f"S{len(seen)}")

    monkeypatch.setattr(agents, "_invoke", fake)
    r = agents.run_pipeline("b1", steps=STEPS, cwd=tmp_path, agents_dir=AD, max_usd=20.0)
    assert [s[0] for s in seen] == ["kansatsu", "shinko", "kansatsu", "kazoe", "sokuryo"]
    sh = seen[1]
    assert sh[3] == agents.agent_limits("shinko", "fable")["max_turns"] == 40 and sh[4] == agents.LIMITS["fable"]["max_budget_usd"]
    assert "[stuck]" in sh[2] and "out/b1/soten.json" in sh[2] and "stop_for_human" in sh[2]
    redo_task = seen[2][2]
    assert seen[2][1] is None and "進行役の決定（data/reports/指示_" in redo_task and "測量からやり直す" in redo_task
    assert not r["stopped_early"] and r["done"] == STEPS and r["shinko_total"] == 1
    st = agents.load_state("b1", tmp_path)
    e = st["shinko"][0]
    assert e["n"] == 1 and e["step"] == "kansatsu" and e["redo_from"] == "kansatsu" and e["review"] is None
    assert e["decision_file"].endswith("決定_自動_b1_1.md") and (tmp_path / e["decision_file"]).read_text(encoding="utf-8")
    assert e["events"][0].startswith("stuck|") and e["cost_usd"] == pytest.approx(0.5) and e["session_id"] == "F"
    assert st["spent_usd"] == pytest.approx(0.5 + 4 * 0.01)
    assert any(c["step"] == "shinko" for c in st["usage_calls"])
    assert [x["kind"] for x in st["runs"]] == ["start", "shinko"] and st["runs"][1]["redo_from"] == "kansatsu"
    assert st["decisions"][-1]["by"] == "shinko_auto_1" and e["events"][0] in st["acknowledged_events"]
    rec = json.loads(open(r["record"], encoding="utf-8").read())
    assert rec["shinko_total"] == 1 and [s["step"] for s in rec["steps"]] == ["kansatsu", "shinko", "kansatsu", "kazoe", "sokuryo"]


def test_third_event_stops_for_human(tmp_path, monkeypatch, key):
    """(b) 自動で決めるのは 2 回まで。3 回目の事件は止めて human の事件。"""
    _soten(tmp_path, 1)
    n_open = {"n": 1}
    ans = _shinko_writes(tmp_path)

    def fake(spec, task, cwd, max_turns, max_budget_usd, resume=None):
        if spec["name"] == "shinko":
            return ans(task)
        n_open["n"] += 1          # 係が回るたびに争点が 1 件増える → 事件の文が変わり、新しい事件になる
        _soten(tmp_path, n_open["n"])
        return _ok()

    monkeypatch.setattr(agents, "_invoke", fake)
    r = agents.run_pipeline("b1", steps=STEPS + ["sekkei", "mejirushi"], cwd=tmp_path, agents_dir=AD, max_usd=40.0)
    assert r["stopped"]["reason"] == "event" and r["stopped"]["step"] == "sekkei" and r["stopped"]["shinko"] == "max"
    assert r["shinko_total"] == agents.SHINKO_AUTO_MAX == 3
    assert any(p["kind"] == "human" and "3 回を超えた" in p["why"] for p in r["pending_events"])


def test_broken_reply_stops(tmp_path, monkeypatch, key):
    """(c) 返事の JSON が壊れている・決定ファイルが無い・工程名が不正なら止めて human の事件。"""
    for name, ans in (("json", lambda t: _ok(text="決めました。{\"decision_file\": ")),
                      ("nofile", _shinko_writes(tmp_path, write=False)),
                      ("badstep", _shinko_writes(tmp_path, redo_from="mejirushi"))):
        bid = f"b{name}"
        _soten(tmp_path, bid=bid)
        monkeypatch.setattr(agents, "_invoke", lambda spec, task, *a, resume=None, ans=ans:
                            ans(task) if spec["name"] == "shinko" else _ok())
        r = agents.run_pipeline(bid, steps=STEPS, cwd=tmp_path, agents_dir=AD, max_usd=20.0)
        assert r["stopped"]["reason"] == "event" and r["stopped"]["step"] == "kansatsu", name
        assert r["stopped"]["shinko"] == "failed" and r["done"] == ["kansatsu"], name
        st = agents.load_state(bid, tmp_path)
        assert st["shinko"][0]["failed"] and st["runs"][-1]["kind"] == "shinko", name
        assert any(p["kind"] == "human" and "形にならなかった" in p["why"] for p in st["pending_events"]), name


def test_review_revoke_halts_before_next_step(tmp_path, monkeypatch, key):
    """(d) 職員が決定を取り消す（別プロセスがファイルに書く）→ 次の工程の前で止まる。halt は halted に移る。"""
    _soten(tmp_path)
    ans = _shinko_writes(tmp_path)
    seen = []

    def fake(spec, task, cwd, max_turns, max_budget_usd, resume=None):
        seen.append(spec["name"])
        if spec["name"] == "shinko":
            return ans(task)
        if spec["name"] == "kazoe":      # 走っている間に職員の画面が取り消す
            got = agents.review_decision("b1", 1, "revoke", "数え直しの方が先", cwd=tmp_path)
            assert got["review"]["verdict"] == "revoke"
        return _ok()

    monkeypatch.setattr(agents, "_invoke", fake)
    r = agents.run_pipeline("b1", steps=STEPS, cwd=tmp_path, agents_dir=AD, max_usd=20.0)
    assert seen == ["kansatsu", "shinko", "kazoe"]
    assert r["stopped"]["reason"] == "halt" and r["stopped"]["step"] == "sokuryo" and r["done"] == ["kansatsu", "kazoe"]
    assert r["stopped"]["label"] == agents.STOP_REASONS["halt"] and "取り消した" in r["stopped"]["why"]
    st = agents.load_state("b1", tmp_path)
    assert st["shinko"][0]["review"]["verdict"] == "revoke" and st["shinko"][0]["review"]["note"] == "数え直しの方が先"
    assert st["halt"] is None and st["halted"][0]["by"] == "staff" and st["halted"][0]["stopped_before"] == "sokuryo"
    for bad in ((2, "approve"), (1, "ok")):
        with pytest.raises(agents.AgentError):
            agents.review_decision("b1", *bad, cwd=tmp_path)
    # 次の resume では止まらない
    r2 = agents.resume_pipeline("b1", cwd=tmp_path, agents_dir=AD)
    assert not r2["stopped_early"] and r2["done"] == STEPS
    # 承認と止める合図
    assert agents.review_decision("b1", 1, "approve", cwd=tmp_path)["review"]["verdict"] == "approve"
    assert agents.request_halt("b1", "職員が止めた", cwd=tmp_path)["by"] == "staff"
    assert agents.load_state("b1", tmp_path)["halt"]["why"] == "職員が止めた"


def test_resume_shinko_from_running_state(tmp_path, monkeypatch, key):
    """(e) running のまま（落ちた）の記録から、済んだ工程の後の事件を取り直し（acknowledged でも）進行役を呼んで続ける。"""
    _soten(tmp_path)
    sig = agents._event_sig(agents._stop_events(agents.detect_events("b1", tmp_path, write=False), [])[0])
    st = agents._new_state("b1", "continue", None, STEPS, None, 20.0)
    st.update(status="running", current="kazoe", done=["kansatsu"], sessions={"kansatsu": "K1", "kazoe": "K2"},
              acknowledged_events=[sig], spent_usd=9.3)
    agents._save_state(st, tmp_path)
    seen = []
    ans = _shinko_writes(tmp_path, redo_from="kazoe")
    monkeypatch.setattr(agents, "_invoke", lambda spec, task, *a, resume=None:
                        seen.append((spec["name"], resume)) or (ans(task) if spec["name"] == "shinko" else _ok()))
    # 普通の resume では acknowledged なので止まらない事件を、--shinko の dry-run は計画に出す（呼ばない・書かない）
    dr = agents.resume_pipeline("b1", dry_run=True, cwd=tmp_path, agents_dir=AD, shinko=True)
    plan = dr["shinko_plan"]
    assert seen == [] and plan["would_call"] and plan["after_step"] == "kansatsu" and plan["events"][0]["sig"] == sig
    assert agents.load_state("b1", tmp_path)["status"] == "running"
    r = agents.resume_pipeline("b1", cwd=tmp_path, agents_dir=AD, shinko=True)
    assert seen == [("shinko", None), ("kazoe", None), ("sokuryo", None)]      # やり直す工程は新しい会話で
    assert not r["stopped_early"] and r["done"] == STEPS and r["shinko_total"] == 1
    s2 = agents.load_state("b1", tmp_path)
    assert s2["shinko"][0]["step"] == "kansatsu" and s2["runs"][-2]["kind"] == "resume" and s2["runs"][-2]["shinko"]
    assert s2["redo"]["previous_sessions"] == {"kazoe": "K2"} and s2["record_name"] == st["record_name"]


def test_no_budget_no_shinko(tmp_path, monkeypatch, key):
    """(f) 棟の費用の残りが進行役 1 回の上限より小さければ呼ばない（理由 max_usd）。上限が無い記録も呼ばない。"""
    _soten(tmp_path)
    seen = []
    monkeypatch.setattr(agents, "_invoke", lambda spec, task, *a, resume=None: seen.append(spec["name"]) or _ok(cost=1.0))
    r = agents.run_pipeline("b1", steps=STEPS, cwd=tmp_path, agents_dir=AD, max_usd=6.5)
    assert seen == ["kansatsu"] and r["stopped"]["reason"] == "max_usd" and r["stopped"]["shinko"] == "max_usd"
    seen.clear()
    r = agents.run_pipeline("b1", steps=STEPS, cwd=tmp_path, agents_dir=AD, max_usd=None)
    assert seen == ["kansatsu"] and r["stopped"]["reason"] == "event" and r["stopped"]["shinko"] == "no_cap"


def test_short_budget_stops_with_shinko_pending(tmp_path, monkeypatch, key):
    """(g) 費用の残りが足りない時は reason max_usd・shinko_pending で止まり、human の事件は増えない（画面が上限を聞く）。"""
    _soten(tmp_path)
    monkeypatch.setattr(agents, "_invoke", lambda spec, task, *a, resume=None: _ok(cost=1.0))
    r = agents.run_pipeline("b1", steps=STEPS, cwd=tmp_path, agents_dir=AD, max_usd=6.5)
    stp = r["stopped"]
    assert stp["reason"] == "max_usd" and stp["shinko_pending"] is True and stp["step"] == "kansatsu"
    assert stp["events"][0].startswith("stuck|") and "10 USD" in stp["why"] and "5.50 USD" in stp["why"]
    assert stp["label"] == agents.STOP_REASONS["max_usd"]
    assert not any(p["kind"] == "human" for p in r["pending_events"]) and r["shinko_total"] == 0


def test_resume_after_short_budget_calls_shinko(tmp_path, monkeypatch, key):
    """(h) その記録を上限を上げて resume すると、--shinko 無しで進行役が呼ばれて続く。上げなければ AgentError。"""
    _soten(tmp_path)
    seen = []
    ans = _shinko_writes(tmp_path, redo_from="kazoe")

    def fake(spec, task, cwd, max_turns, max_budget_usd, resume=None):
        seen.append(spec["name"])
        return ans(task) if spec["name"] == "shinko" else _ok(cost=1.0)

    monkeypatch.setattr(agents, "_invoke", fake)
    r = agents.run_pipeline("b1", steps=STEPS, cwd=tmp_path, agents_dir=AD, max_usd=6.5)
    assert r["stopped"]["shinko_pending"] and seen == ["kansatsu"]
    with pytest.raises(agents.AgentError):
        agents.resume_pipeline("b1", cwd=tmp_path, agents_dir=AD)
    r2 = agents.resume_pipeline("b1", cwd=tmp_path, agents_dir=AD, max_usd=20.0)
    assert seen == ["kansatsu", "shinko", "kazoe", "sokuryo"]
    assert not r2["stopped_early"] and r2["done"] == STEPS and r2["shinko_total"] == 1
    st = agents.load_state("b1", tmp_path)
    assert st["shinko"][0]["step"] == "kansatsu" and st["runs"][-2]["kind"] == "resume"


# ---- 11/8 昼：返事の解析の堅牢化・名前違いの決定ファイル・区切った時の続き・記録の保存 ----

def test_parse_reply_variants():
    """決定の JSON を後ろから探す：JSON の後ろに文、複数行の JSON、コードの囲み、decision_file の無い JSON は飛ばす。"""
    j = {"decision_file": "data/reports/指示_11-08/決定_自動_b1_1.md", "redo_from": None, "summary": "s", "stop_for_human": False}
    one = json.dumps(j, ensure_ascii=False)
    multi = json.dumps(j, ensure_ascii=False, indent=2)
    assert agents.parse_shinko_reply(f"決めました。\n{one}\n以上です。手順書にも足しました。") == j
    assert agents.parse_shinko_reply(f"決めました。\n```json\n{multi}\n```\n\n補足：{{\"x\": 1}}") == j
    assert agents.parse_shinko_reply(f"前の案 {{\"decision_file\": \"a.md\"}} は捨てた。\n{multi}") == j    # 最後の物
    assert agents.parse_shinko_reply('{"summary": "decision_file が無い"}') is None
    assert agents.parse_shinko_reply('決めました。{"decision_file": ') is None
    assert agents.parse_shinko_reply(None) is None


def test_norm_redo_and_decision_line():
    steps = ["satsuei", "shiwake", "sokuryo", "sekkei", "seizu", "kenshu", "seizu_final", "web"]
    assert agents._norm_redo("null", steps) == (None, None) and agents._norm_redo("なし", steps) == (None, None)
    assert agents._norm_redo("sokuryo", steps) == ("sokuryo", None)
    rf, note = agents._norm_redo("mejirushi", steps)        # 追記の記録に無い工程は後ろの最初の工程に寄せる
    assert rf == "kenshu" and "寄せた" in note
    assert agents._norm_redo("kansatsu", steps)[0] == "sokuryo"
    assert agents._norm_redo("foo", steps) == ("foo", None)
    assert agents.decision_redo_from("# 決定\n- 規則\nやり直す工程：sokuryo\n") == (True, "sokuryo")
    assert agents.decision_redo_from("やり直す工程: なし") == (True, None)
    assert agents.decision_redo_from("規則だけ") == (False, None)


def test_misnamed_file_without_json_is_picked_up(tmp_path, monkeypatch, key):
    """返事に JSON が無く、決定ファイルを依頼と違う名前で書いた → 指示_*/ の新しいファイルを拾い、redo_from はその行から。"""
    _soten(tmp_path)
    seen = []

    def fake(spec, task, cwd, max_turns, max_budget_usd, resume=None):
        seen.append(spec["name"])
        if spec["name"] == "shinko":
            d = re.search(r"決定を新しいファイル (\S+)/[^/\s]+ に書く", task).group(1)
            p = tmp_path / d / "決定_b1_自動.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# 決定\n- 争点が open の時は数えからやり直す。\nやり直す工程：kazoe\n", encoding="utf-8")
            return _ok(sid="F", cost=0.5, text="決定ファイルを書きました。手順書にも 1 行足しました。")
        return _ok()

    monkeypatch.setattr(agents, "_invoke", fake)
    r = agents.run_pipeline("b1", steps=STEPS, cwd=tmp_path, agents_dir=AD, max_usd=20.0)
    assert seen == ["kansatsu", "shinko", "kazoe", "sokuryo"]
    assert not r["stopped_early"] and r["done"] == STEPS
    e = agents.load_state("b1", tmp_path)["shinko"][0]
    assert e["decision_file"].endswith("決定_b1_自動.md") and e["redo_from"] == "kazoe" and "failed" not in e
    assert any("拾った" in x for x in e["notes"])


def test_json_points_to_wrong_name_is_picked_up(tmp_path, monkeypatch, key):
    """返事の JSON が指すファイルが無く、別の名前で書かれていた → 拾う。redo_from は返事の JSON の値。"""
    _soten(tmp_path)

    def fake(spec, task, cwd, max_turns, max_budget_usd, resume=None):
        if spec["name"] == "shinko":
            path = re.search(r"決定を新しいファイル (\S+) に書く", task).group(1)
            p = tmp_path / path.replace("決定_自動_b1_1", "決定_自動_b1")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# 決定\n- 規則", encoding="utf-8")
            body = json.dumps({"decision_file": path, "redo_from": None, "summary": "進める"}, ensure_ascii=False, indent=2)
            return _ok(sid="F", cost=0.5, text=f"{body}\nこれで進めます。")
        return _ok()

    monkeypatch.setattr(agents, "_invoke", fake)
    r = agents.run_pipeline("b1", steps=STEPS, cwd=tmp_path, agents_dir=AD, max_usd=20.0)
    assert not r["stopped_early"] and r["done"] == STEPS
    e = agents.load_state("b1", tmp_path)["shinko"][0]
    assert e["decision_file"].endswith("決定_自動_b1.md") and e["redo_from"] is None and e["summary"] == "進める"


def test_no_json_no_file_stops_for_human(tmp_path, monkeypatch, key):
    _soten(tmp_path)
    monkeypatch.setattr(agents, "_invoke", lambda spec, task, *a, resume=None:
                        _ok(text="考えましたが決められません。") if spec["name"] == "shinko" else _ok())
    r = agents.run_pipeline("b1", steps=STEPS, cwd=tmp_path, agents_dir=AD, max_usd=20.0)
    assert r["stopped"]["reason"] == "event" and r["stopped"]["shinko"] == "failed"
    assert any(p["kind"] == "human" and "形にならなかった" in p["why"] for p in r["pending_events"])


def test_partial_shinko_continues_same_session(tmp_path, monkeypatch, key):
    """進行役が【続きあり】で区切った → 同じ会話から続ける。費用は会話の合計から 1 回分に直して数える。"""
    _soten(tmp_path)
    ans = _shinko_writes(tmp_path, redo_from=None)
    seen = []

    def fake(spec, task, cwd, max_turns, max_budget_usd, resume=None):
        seen.append((spec["name"], resume))
        if spec["name"] == "shinko":
            if resume is None:
                return _ok(sid="F", cost=0.5, text=agents.CONTINUE_MARK + "\n- 決定ファイルを書く\n- 手順書に 1 行")
            r = ans(task)
            r["cost_usd"] = 0.9          # SDK は resume の時に会話の合計を返す
            return r
        return _ok()

    monkeypatch.setattr(agents, "_invoke", fake)
    r = agents.run_pipeline("b1", steps=STEPS, cwd=tmp_path, agents_dir=AD, max_usd=20.0)
    assert seen[:3] == [("kansatsu", None), ("shinko", None), ("shinko", "F")]
    assert not r["stopped_early"] and r["done"] == STEPS
    st = agents.load_state("b1", tmp_path)
    e = st["shinko"][0]
    assert e["calls"] == 2 and e["cost_usd"] == pytest.approx(0.9) and "failed" not in e
    assert st["spent_usd"] == pytest.approx(0.9 + 3 * 0.01)


def test_partial_shinko_gives_up_after_two(tmp_path, monkeypatch, key):
    _soten(tmp_path)
    n = {"shinko": 0}

    def fake(spec, task, cwd, max_turns, max_budget_usd, resume=None):
        if spec["name"] == "shinko":
            n["shinko"] += 1
            return _ok(sid="F", cost=0.1 * n["shinko"], text=agents.CONTINUE_MARK + "\n- まだ")
        return _ok()

    monkeypatch.setattr(agents, "_invoke", fake)
    r = agents.run_pipeline("b1", steps=STEPS, cwd=tmp_path, agents_dir=AD, max_usd=20.0)
    assert n["shinko"] == 1 + agents.SHINKO_CONTINUE_MAX
    assert r["stopped"]["shinko"] == "failed"


def test_same_redo_twice_then_human(tmp_path, monkeypatch, key):
    """進行役が 2 回とも同じ redo_from（前の工程）を返し、事件が続く → 3 回目は回さずに人を呼ぶ（無限に回らない）。"""
    _soten(tmp_path, 1)
    k = {"n": 1}
    ans = _shinko_writes(tmp_path, redo_from="kansatsu")
    seen = []

    def fake(spec, task, cwd, max_turns, max_budget_usd, resume=None):
        seen.append(spec["name"])
        if spec["name"] == "shinko":
            return ans(task)
        k["n"] += 1
        _soten(tmp_path, k["n"])
        return _ok()

    monkeypatch.setattr(agents, "_invoke", fake)
    r = agents.run_pipeline("b1", steps=STEPS, cwd=tmp_path, agents_dir=AD, max_usd=20.0)
    assert seen.count("shinko") == agents.SHINKO_AUTO_MAX and len(seen) < 10
    assert r["stopped"]["shinko"] == "max"


def test_save_state_survives_broken_file(tmp_path):
    """記録のファイルが壊れている・無い時も _save_state は落ちない（取り込む物が無いだけ）。"""
    st = agents._new_state("b1", "full", None, STEPS, None, 20.0)
    agents._save_state(st, tmp_path)                       # 無い
    p = agents.state_path("b1", tmp_path)
    p.write_text('{"building": "b1", "shin', encoding="utf-8")   # 書きかけ
    agents._save_state(st, tmp_path)
    assert agents.load_state("b1", tmp_path)["record_name"] == st["record_name"]
    p.write_text("[]", encoding="utf-8")                   # dict でない
    agents._save_state(st, tmp_path)
    assert not list(p.parent.glob("*.tmp"))


def test_review_under_lock_keeps_runner_fields(tmp_path):
    """職員の画面の書き込み（review）と係の側の保存が入れ違っても、互いの欄を消さない。"""
    st = agents._new_state("b1", "full", None, STEPS, None, 20.0)
    st["shinko"] = [{"n": 1, "review": None}]
    agents._save_state(st, tmp_path)
    agents.review_decision("b1", 1, "approve", cwd=tmp_path)    # 画面（別プロセスの代わり）
    st["done"] = ["kansatsu"]
    agents._save_state(st, tmp_path)                          # 係の側（メモリの state）
    disk = agents.load_state("b1", tmp_path)
    assert disk["done"] == ["kansatsu"] and disk["shinko"][0]["review"]["verdict"] == "approve"


def test_event_after_last_step_is_recorded_only(tmp_path, monkeypatch, key):
    """最後の工程の後の事件は記録だけ（進行役を呼ばない。審査 11）。"""
    _soten(tmp_path)
    seen = []
    monkeypatch.setattr(agents, "_invoke", lambda spec, task, *a, resume=None: seen.append(spec["name"]) or _ok())
    r = agents.run_pipeline("b1", steps=["kansatsu"], cwd=tmp_path, agents_dir=AD, max_usd=20.0)
    assert seen == ["kansatsu"] and not r["stopped_early"]
    assert any(p.get("after_last_step") and p["kind"] == "stuck" for p in r["pending_events"])


def test_accept_faces_and_string_null(tmp_path, monkeypatch, key):
    """返事の "redo_from": "null"（文字列）は null、"stop_for_human": "false" は偽、accept_faces は state に残る。"""
    _soten(tmp_path)

    def fake(spec, task, cwd, max_turns, max_budget_usd, resume=None):
        if spec["name"] == "shinko":
            path = re.search(r"決定を新しいファイル (\S+) に書く", task).group(1)
            (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / path).write_text("# 決定\n不合格のまま進める面：f4, f5\n", encoding="utf-8")
            return _ok(sid="F", text=json.dumps({"decision_file": path, "redo_from": "null", "accept_faces": ["f4", "f5"],
                                                 "summary": "進める", "stop_for_human": "false"}))
        return _ok()

    monkeypatch.setattr(agents, "_invoke", fake)
    r = agents.run_pipeline("b1", steps=STEPS, cwd=tmp_path, agents_dir=AD, max_usd=20.0)
    assert not r["stopped_early"]
    st = agents.load_state("b1", tmp_path)
    assert st["shinko"][0]["redo_from"] is None and st["accepted_faces"] == ["f4", "f5"]
    assert "stop_for_human" not in st["shinko"][0]


def test_shinko_header_has_no_continue_rule(tmp_path, monkeypatch, key):
    _soten(tmp_path)
    tasks = {}
    ans = _shinko_writes(tmp_path)
    monkeypatch.setattr(agents, "_invoke", lambda spec, task, *a, resume=None:
                        tasks.setdefault(spec["name"], task) and (ans(task) if spec["name"] == "shinko" else _ok()))
    agents.run_pipeline("b1", steps=STEPS, cwd=tmp_path, agents_dir=AD, max_usd=20.0)
    assert agents.CONTINUE_MARK not in tasks["shinko"] and agents.CONTINUE_MARK in tasks["kansatsu"]


def test_shinko_pending_events_set_then_cleared(tmp_path, monkeypatch, key):
    """進行役を呼ぶ前の保存で shinko_pending_events に事件の印が入り、決まった後（と失敗で止めた後）は空。"""
    _soten(tmp_path)
    ans = _shinko_writes(tmp_path)
    during = []

    def fake(spec, task, cwd, max_turns, max_budget_usd, resume=None):
        if spec["name"] == "shinko":
            disk = agents.load_state("b1", tmp_path)
            during.append((disk["status"], disk["current"], disk["shinko_pending_events"]))
            return ans(task)
        return _ok()

    monkeypatch.setattr(agents, "_invoke", fake)
    agents.run_pipeline("b1", steps=STEPS, cwd=tmp_path, agents_dir=AD, max_usd=40.0)
    assert during[0][:2] == ("running", "shinko") and during[0][2][0].startswith("stuck|")
    assert not agents.load_state("b1", tmp_path)["shinko_pending_events"]
    monkeypatch.setattr(agents, "_invoke", lambda spec, task, *a, resume=None:
                        _ok(text="決められない") if spec["name"] == "shinko" else _ok())
    _soten(tmp_path, bid="b2")
    r = agents.run_pipeline("b2", steps=STEPS, cwd=tmp_path, agents_dir=AD, max_usd=40.0)
    assert r["stopped"]["shinko"] == "failed" and agents.load_state("b2", tmp_path)["shinko_pending_events"] == []
