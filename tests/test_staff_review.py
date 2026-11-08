"""職員の画面から進行役の自動の決定を見て承認／取り消す API（11/8、F19）。
agents.review_decision・resume_pipeline はモック（SDK は呼ばない）。サーバの立て方は tests/test_serve.py と同じ。"""
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import pytest

from recorder import agents, serve


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv(serve.CAMPUS_ENV, raising=False)
    monkeypatch.setitem(serve.SELECTED, "campus", None)
    serve.SITE_RUN.clear()
    (tmp_path / "web" / "data").mkdir(parents=True)
    (tmp_path / "web" / "staff.html").write_text("<!doctype html><title>t</title>", encoding="utf-8")
    (tmp_path / "web" / "data" / "site.json").write_text(json.dumps(
        {"campus": "test", "university": "テスト大学", "scope": {"targets": ["honbu", "tower75"]}}), encoding="utf-8")
    (tmp_path / "data" / "site").mkdir(parents=True)
    (tmp_path / "data" / "site" / "test.json").write_text(json.dumps(
        {"campus": "test", "aliases": {"honbu": "honbu", "tower75": "tower75"}}), encoding="utf-8")
    serve.RUNS.clear()
    serve._last_folder.clear()
    s = serve.make_server("127.0.0.1", 0, tmp_path)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{s.server_address[1]}", tmp_path
    s.shutdown()
    s.server_close()
    import os
    os.environ.pop(serve.CAMPUS_ENV, None)


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _post(url, obj):
    req = urllib.request.Request(url, data=json.dumps(obj).encode(), method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


SHINKO = [{"n": 1, "at": "2026-11-08T10:00:00", "step": "kenshu", "events": ["stuck|f5 が 2 回不合格"],
           "decision_file": "data/reports/指示_1108/決定_自動_honbu_1.md", "redo_from": "sokuryo",
           "summary": "f5 の登録を解き直す", "cost_usd": 0.42, "session_id": "s1", "review": None}]


def _write_state(root, bid="honbu", **kw):
    st = {"building": bid, "mode": "full", "steps": ["satsuei", "sokuryo", "kenshu"], "done": ["satsuei"],
          "status": "stopped", "current": None, "spent_usd": 1.0, "max_usd": 20.0, "sessions": {}, "runs": [],
          "stopped": {"step": "kenshu", "reason": "event", "why": "再検収 2 回で不合格の面 f5", "label": "事件",
                      "events": ["stuck|f5"]},
          "pending_events": [{"kind": "human", "call": "human", "why": "撮影の依頼が 3 件"},
                             {"kind": "tool", "call": "record", "why": "道具"}],
          "decisions": [{"at": "2026-11-08T09:00:00", "file": "data/reports/指示_1108/決定.md", "text": "# 決定の見出し\n本文",
                         "redo_from": "sokuryo", "chars": 10}],
          "shinko": SHINKO, "halt": None, "updated_at": "2026-11-08T10:00:00"}
    st.update(kw)
    p = agents.state_path(bid, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    return st


def _fake_review(monkeypatch, root, calls):
    def fake(building_id, n, verdict, note=None, *, cwd="."):
        calls.append((building_id, n, verdict, note))
        st = agents.load_state(building_id, cwd)
        item = next((x for x in st["shinko"] if x["n"] == n), None)
        if item is None:
            raise agents.AgentError(f"決定 {n} がありません")
        item["review"] = {"verdict": verdict, "at": "2026-11-08T10:05:00", "note": note}
        if verdict == "revoke":
            st["halt"] = {"why": f"決定 {n} の取り消し", "at": "2026-11-08T10:05:00", "by": "staff"}
        agents.state_path(building_id, cwd).write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        return st
    monkeypatch.setattr(agents, "review_decision", fake)


# (a) /api/review の approve・revoke・400
def test_review_approve_and_revoke(srv, monkeypatch):
    base, root = srv
    calls = []
    _fake_review(monkeypatch, root, calls)
    _write_state(root)
    st, j = _post(base + "/api/review", {"building": "honbu", "n": 1, "verdict": "approve", "note": "読んだ"})
    assert st == 200 and j["item"]["review"]["verdict"] == "approve" and j["halt"] is None
    _write_state(root)
    st, j = _post(base + "/api/review", {"building": "honbu", "n": 1, "verdict": "revoke"})
    assert st == 200 and j["item"]["review"]["verdict"] == "revoke" and j["halt"]["by"] == "staff"
    assert calls == [("honbu", 1, "approve", "読んだ"), ("honbu", 1, "revoke", None)]


def test_review_revoke_accepted_while_running(srv, monkeypatch):
    base, root = srv
    calls = []
    _fake_review(monkeypatch, root, calls)
    _write_state(root, status="running", current="sokuryo", stopped=None)
    serve.RUNS["honbu"] = {"state": "running"}
    st, j = _post(base + "/api/review", {"building": "honbu", "n": 1, "verdict": "revoke"})
    assert st == 200 and j["running"] is True and j["halt"] is not None


def test_review_400(srv, monkeypatch):
    base, root = srv
    calls = []
    _fake_review(monkeypatch, root, calls)
    _write_state(root)
    assert _post(base + "/api/review", {"building": "nothere", "n": 1, "verdict": "approve"})[0] == 400   # 棟が無い
    assert _post(base + "/api/review", {"building": "../x", "n": 1, "verdict": "approve"})[0] == 400
    assert _post(base + "/api/review", {"building": "honbu", "n": "1.5", "verdict": "approve"})[0] == 400
    assert _post(base + "/api/review", {"building": "honbu", "n": 1.5, "verdict": "approve"})[0] == 400
    assert _post(base + "/api/review", {"building": "honbu", "n": True, "verdict": "approve"})[0] == 400
    assert _post(base + "/api/review", {"building": "honbu", "n": 1, "verdict": "ok"})[0] == 400
    assert calls == []
    st, j = _post(base + "/api/review", {"building": "honbu", "n": 9, "verdict": "approve"})   # agents が断る
    assert st == 400 and "決定 9" in j["error"]


# (b) /api/pipeline・/api/progress に shinko・halt などが通る
def test_pipeline_and_progress_pass_review_fields(srv):
    base, root = srv
    _write_state(root, halt={"why": "取り消し", "at": "x", "by": "staff"})
    _st, p = _get(base + "/api/pipeline?building=honbu")
    s = p["state"]
    assert s["shinko"][0]["summary"] == "f5 の登録を解き直す" and s["halt"]["by"] == "staff"
    assert s["stopped"]["events"] == ["stuck|f5"] and s["halted"] is None
    assert s["decisions"][0]["summary"] == "決定の見出し" and "text" not in s["decisions"][0]
    assert [e["kind"] for e in s["pending_events"]] == ["human", "tool"]
    _st, g = _get(base + "/api/progress?buildings=honbu,tower75")
    b = g["buildings"]["honbu"]
    assert b["state"]["shinko"][0]["n"] == 1 and b["state"]["halt"]["why"] == "取り消し"
    assert b["resume"]["why_stop"].startswith("再検収") and b["resume"]["events"] == ["stuck|f5"]
    assert b["resume"]["human_events"] == 1 and b["resume"]["reason"] == "event"
    assert "state" not in g["buildings"]["tower75"]   # 状態ファイルが無い棟には出さない


def test_pipeline_state_without_new_fields_is_empty(srv):
    base, root = srv
    _write_state(root, shinko=None, decisions=None, pending_events=None)
    _st, p = _get(base + "/api/pipeline?building=honbu")
    assert p["state"]["shinko"] == [] and p["state"]["decisions"] == [] and p["state"]["pending_events"] == []
    assert p["state"]["halt"] is None


# (c) stale の判定
def test_stale_running_state_can_resume(srv, monkeypatch):
    base, root = srv
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-key")
    old = (datetime.now() - timedelta(minutes=11)).isoformat(timespec="seconds")
    new = (datetime.now() - timedelta(minutes=2)).isoformat(timespec="seconds")
    _write_state(root, status="running", current="sokuryo", stopped=None, updated_at=new)
    _st, p = _get(base + "/api/progress?buildings=honbu")
    assert p["buildings"]["honbu"]["resume"]["code"] == "not_stopped"   # 新しい：別の窓で動いているかもしれない
    _write_state(root, status="running", current="sokuryo", stopped=None, updated_at=old)
    _st, p = _get(base + "/api/progress?buildings=honbu")
    rs = p["buildings"]["honbu"]["resume"]
    assert rs["code"] == "stale" and rs["ok"] is True and rs["stopped"] is True and "10 分以上更新が無い" in rs["why"]
    serve.RUNS["honbu"] = {"state": "done"}   # このサーバの RUNS にある棟は stale と見ない
    assert serve.resume_check(root, "honbu", serve.RUNS["honbu"])["code"] == "not_stopped"


# (d) /api/resume の shinko が渡る
def test_resume_passes_shinko(srv, monkeypatch):
    base, root = srv
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-key")
    got = []

    def fake(bid, *, dry_run=False, cwd=".", agents_dir=None, prices=None, max_usd=None, redo_from=None,
             decision_file=None, shinko=False):
        got.append((bid, shinko))
        return {"building": bid, "steps": [{"step": "kenshu"}], "stopped_early": False}
    monkeypatch.setattr(agents, "resume_pipeline", fake)
    old = (datetime.now() - timedelta(minutes=30)).isoformat(timespec="seconds")
    _write_state(root, status="running", current="kenshu", stopped=None, updated_at=old)
    st, j = _post(base + "/api/resume", {"building": "honbu", "shinko": True})
    assert st == 200 and j["shinko"] is True and j["code"] == "stale"
    for _ in range(100):
        if got and (serve.RUNS.get("honbu") or {}).get("state") != "running":
            break
        time.sleep(0.05)
    assert got == [("honbu", True)]
    _write_state(root)   # 事件で止まった記録。shinko 無しなら渡さない（古い関数の形でも動く）
    st, j = _post(base + "/api/resume", {"building": "honbu"})
    assert st == 200 and j["shinko"] is False
    for _ in range(100):
        if len(got) == 2:
            break
        time.sleep(0.05)
    assert got[1] == ("honbu", False)


# (e) /api/decision：外の path は 400、data/reports/ の下は中身
def test_decision_reads_only_reports(srv):
    base, root = srv
    d = root / "data" / "reports" / "指示_1108"
    d.mkdir(parents=True)
    (d / "決定_自動_honbu_1.md").write_text("# f5 を解き直す\n本文", encoding="utf-8")
    (root / "data" / "other.md").write_text("x", encoding="utf-8")
    from urllib.parse import quote
    st, j = _get(base + "/api/decision?file=" + quote("data/reports/指示_1108/決定_自動_honbu_1.md"))
    assert st == 200 and j["text"].startswith("# f5") and j["summary"] == "f5 を解き直す"
    assert _get(base + "/api/decision?file=" + quote("../outside.md"))[0] == 400
    assert _get(base + "/api/decision?file=" + quote("data/other.md"))[0] == 400
    assert _get(base + "/api/decision?file=")[0] == 400
    assert _get(base + "/data/reports/" + quote("指示_1108/決定_自動_honbu_1.md"))[0] == 404   # data/reports/ は静的配信しない


def test_staff_html_has_review_controls():
    from pathlib import Path
    s = (Path(__file__).resolve().parents[1] / "web" / "staff.html").read_text(encoding="utf-8")
    for w in ("/api/review", "/api/decision", "進行役に決めさせて続ける", "あなたに聞いています",
              "取り消すと次の工程の前で止まります。よいですか", "data-shinko"):
        assert w in s


def test_staff_html_always_shows_max_usd_field():
    """費用の上限の欄は need_max_usd の時だけでなく常に出し、既定は今の上限（無ければ 20）、空なら送らない（F19 の追加）。"""
    from pathlib import Path
    s = (Path(__file__).resolve().parents[1] / "web" / "staff.html").read_text(encoding="utf-8")
    assert "(rs.need_max_usd ? `<label>費用の上限" not in s          # 上限の欄を need_max_usd の時だけにしない
    assert '`<label>費用の上限 <input type="number" min="0" step="1" data-resume-max=' in s
    assert "cap == null ? 40 : Math.ceil(Math.max(cap, spent + 20))" in s and "空なら今の上限のまま" in s and "より大きく" in s
    assert 'v === "" && inp.dataset.need' in s and 'if (v !== "")' in s


def test_resume_check_shinko_pending_needs_max_usd(srv, monkeypatch):
    """進行役を呼ぶ費用が上限の残りに足りずに止まった記録：shinko_pending が真、need_max_usd も真（F19）。"""
    base, root = srv
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-key")
    _write_state(root, spent_usd=16.0, max_usd=20.0,
                 stopped={"step": "kenshu", "reason": "max_usd", "why": "進行役を呼ぶ費用 6 USD が上限の残りに足りません",
                          "events": ["stuck|f5"], "shinko_pending": True, "label": "費用の上限"})
    _st, p = _get(base + "/api/progress?buildings=honbu")
    rs = p["buildings"]["honbu"]["resume"]
    assert rs["shinko_pending"] is True and rs["need_max_usd"] is True and rs["code"] == "need_max_usd" and rs["ok"] is True
    assert rs["why_stop"].startswith("進行役を呼ぶ費用") and rs["events"] == ["stuck|f5"]
    _write_state(root)   # 普通の事件の止まり：shinko_pending は偽
    _st, p = _get(base + "/api/progress?buildings=honbu")
    assert p["buildings"]["honbu"]["resume"]["shinko_pending"] is False


def test_staff_html_shows_shinko_pending():
    from pathlib import Path
    s = (Path(__file__).resolve().parents[1] / "web" / "staff.html").read_text(encoding="utf-8")
    assert "進行役が決めるには費用の上限が足りません。上限を入れて続けてください" in s
    assert 'rs.code === "stale" || rs.shinko_pending' in s   # この時も「進行役に決めさせて続ける」を出す


def test_screens_after_judge_review():
    """審査員役の指摘 3 件：shinko_pending の勧める額は使った額＋7、待機画面は shinko を「進行役」と出す、
    最後まで済んだ記録の取り消しは「記録だけ」（F19）。"""
    from pathlib import Path
    web = Path(__file__).resolve().parents[1] / "web"
    s = (web / "staff.html").read_text(encoding="utf-8")
    js = (web / "progress.js").read_text(encoding="utf-8")
    assert "spent + sc + 1" in s and "＋進行役の費用 ${Number(rs.shinko_cost_usd) || 10} USD 分より大きく" in s
    assert "spent + (rs.shinko_pending ? sc + 1 : 20)" in js and "＋進行役の費用 ${sc} USD 分より大きく" in js
    for t in (s, js):
        assert "? 7 : 5" not in t and "6 USD 分" not in t   # 決め打ちの 6・7 を残さない
    assert 'shinko: {step: "shinko", agent: "shinko", name: "進行役"' in js and "EXTRA_STEPS[s]" in js
    assert 'rs.code === "done"' in s and "取り消し（記録だけ）" in s and "記録に残しました（工程は済んでいます）" in s


def test_resume_check_returns_shinko_cost_and_start_default_40(srv):
    """resume_check は進行役 1 回の費用（agents.LIMITS["fable"]）を返す。「記録を始める」の上限の既定は 40 USD 以上。"""
    base, root = srv
    _write_state(root)
    _st, p = _get(base + "/api/progress?buildings=honbu")
    assert p["buildings"]["honbu"]["resume"]["shinko_cost_usd"] == agents.LIMITS["fable"]["max_budget_usd"]
    from pathlib import Path
    s = (Path(__file__).resolve().parents[1] / "web" / "staff.html").read_text(encoding="utf-8")
    assert "Math.max(Number(d) || 0, 40)" in s and "mu.value = startUsd()" in s


# --- 再開した記録を待機画面と職員の画面で同じに見せる（F20） ------------------------------------------------

def _rounds_state(**kw):
    st = {"building": "honbu", "mode": "continue", "steps": ["sokuryo", "sekkei", "kenshu"], "done": ["sokuryo", "sekkei", "kenshu"],
          "status": "stopped", "current": None, "spent_usd": 3.0, "max_usd": 40.0, "sessions": {},
          "stopped": {"step": "kenshu", "reason": "event", "why": "f5 が不合格", "label": "事件", "events": ["stuck|f5"]},
          "started_at": "2026-11-08T04:00:00", "updated_at": "2026-11-08T10:51:00",
          "runs": [{"kind": "start", "at": "2026-11-08T04:00:00", "ended_at": "2026-11-08T05:00:00"},
                   {"kind": "resume", "at": "2026-11-08T09:00:00", "from_step": "kenshu", "reason": "event", "redo_from": "sokuryo",
                    "ended_at": "2026-11-08T09:30:00"},
                   {"kind": "resume", "at": "2026-11-08T09:36:00", "from_step": "kenshu"},
                   {"kind": "rewind", "at": "2026-11-08T10:51:00", "to": "kenshu の後の事件（09:35）", "why": "取り下げ"}],
          "results": [{"step": "sokuryo", "agent": "sokuryo", "started_at": "2026-11-08T04:00:00", "ended_at": "2026-11-08T04:30:00",
                       "cost_usd": 1.0, "summary": "1 周目の測量", "note": "警告：単価が渡されていない"},
                      {"step": "kenshu", "agent": "kenshu", "started_at": "2026-11-08T04:30:00", "ended_at": "2026-11-08T05:00:00", "cost_usd": 0.5},
                      {"step": "sokuryo", "agent": "sokuryo", "started_at": "2026-11-08T09:00:00", "ended_at": "2026-11-08T09:10:00",
                       "cost_usd": 1.5, "summary": "2 周目の測量"}],
          "shinko": [], "pending_events": []}
    st.update(kw)
    return st


def test_timeline_rounds_rewind_and_this_round():
    tl = serve.state_timeline(_rounds_state())
    r = tl["rounds"]
    assert [x["kind"] for x in r] == ["start", "resume", "resume"] and r[2]["rewound"] is True and r[1]["rewound"] is False
    assert r[0]["cost_usd"] == 1.5 and r[1]["cost_usd"] == 1.5 and r[1]["steps"] == ["sokuryo"]
    h = tl["history"]
    assert h["resumes"] == 2 and h["this_round_n"] == 2 and h["this_round_at"] == "2026-11-08T09:00:00"   # 取り下げた周は数えない
    assert h["rewinds"][0]["to"].startswith("kenshu") and h["work_seconds_ended"] == 3600 + 1800   # 止まっていた間は入れない
    assert tl["latest"]["sokuryo"] == 2   # 工程ごとの最新の結果は 2 周目
    res = [i for i in tl["items"] if i["type"] == "result"]
    assert [i["round"] for i in res] == [1, 1, 2]
    assert res[0]["summary"] == "1 周目の測量" and res[0]["warn"].startswith("警告")   # 警告は要約にしない
    kinds = [i.get("kind") for i in tl["items"] if i["type"] == "run"]
    assert kinds == ["start", "resume", "resume", "rewind"]   # 時刻順


def test_live_phase_words():
    st = _rounds_state()
    assert serve.live_phase(st, None, {"code": "ok"})["phase"] == "stopped"
    assert serve.live_phase(st, None, {"code": "ok"})["step"] == "kenshu"
    # このサーバで続きを起動した直後（状態ファイルはまだ stopped）
    lv = serve.live_phase(st, {"state": "running", "started_at": "x"}, None)
    assert lv["phase"] == "starting" and lv["since"] == "x"
    run = _rounds_state(status="running", current="shinko", stopped=None)
    assert serve.live_phase(run, {"state": "running"}, None)["phase"] == "shinko"
    lv = serve.live_phase(run, None, {"code": "not_stopped"})
    assert lv["phase"] == "shinko" and lv["where"] == "other"   # 別の窓で進行役が考えている
    assert serve.live_phase(run, None, {"code": "stale"})["phase"] == "stale"
    assert serve.live_phase(_rounds_state(status="done", stopped=None), None, None)["phase"] == "done"


def test_pipeline_and_progress_carry_live_and_timeline(srv):
    base, root = srv
    p = agents.state_path("honbu", root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_rounds_state(), ensure_ascii=False), encoding="utf-8")
    _st, j = _get(base + "/api/pipeline?building=honbu")
    assert j["live"]["phase"] == "stopped" and j["timeline"]["history"]["this_round_n"] == 2
    assert j["state"]["redo"] is None
    _st, g = _get(base + "/api/progress?buildings=honbu")
    b = g["buildings"]["honbu"]
    assert b["live"]["phase"] == "stopped" and b["history"]["resumes"] == 2   # 職員の画面も同じ言葉


def test_progress_js_stays_on_current_step_when_live():
    """live は場面を流さない：次の場面への時計は再生の時だけ、工程が変わった時だけ切り替える。"""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "web" / "progress.js").read_text(encoding="utf-8")
    assert "if (!LIVE && !paused) nextTimer = setTimeout(next, dur)" in js
    assert "if (t !== liveStep)" in js and "function targetStep()" in js and "今の工程に戻る" in js
    assert 'key: "s", step: "shinko"' in js and "前の記録の生成物あり" in js


def test_incident_source_follows_shinko(srv):
    """事件の源：進行役が考えている間は events.json の最新の止める事件（決定済みを除く）か shinko_pending_events、
    決まった後は shinko[n].events、止まった時は stopped.events。/api/pipeline と /api/progress で同じ（F20）。"""
    base, root = srv
    p = agents.state_path("honbu", root)
    p.parent.mkdir(parents=True, exist_ok=True)
    (root / "out" / "honbu" / "events.json").write_text(json.dumps({"made_at": "2026-11-08T11:30:00", "events": [
        {"kind": "stuck", "why": "5 面・37 本"}, {"kind": "check", "why": "止めない"}, {"kind": "stuck", "why": "済んだ"}]},
        ensure_ascii=False), encoding="utf-8")
    st = _rounds_state(status="running", current="shinko", stopped=None, acknowledged_events=["stuck|済んだ"])
    p.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    _s, j = _get(base + "/api/pipeline?building=honbu")
    assert j["incident"]["source"] == "events_json" and [e["why"] for e in j["incident"]["events"]] == ["5 面・37 本"]
    st.update(shinko_pending_events=["stuck|今回出した物"])
    p.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    _s, j = _get(base + "/api/pipeline?building=honbu")
    assert j["incident"]["source"] == "pending" and j["incident"]["events"][0]["why"] == "今回出した物"
    st = _rounds_state(status="running", current="sokuryo", stopped=None,
                       shinko=[{"n": 1, "at": "x", "events": ["stuck|5 面・37 本"], "review": None}])
    p.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    _s, g = _get(base + "/api/progress?buildings=honbu")
    inc = g["buildings"]["honbu"]["incident"]
    assert inc["source"] == "shinko" and inc["n"] == 1 and inc["events"] == [{"kind": "stuck", "why": "5 面・37 本"}]
    p.write_text(json.dumps(_rounds_state(), ensure_ascii=False), encoding="utf-8")
    _s, j = _get(base + "/api/pipeline?building=honbu")
    assert j["incident"]["source"] == "stopped" and j["incident"]["events"][0]["why"] == "f5"


def test_film_uses_record_steps_and_help_is_current():
    """下の列の済は、記録（pipeline_state）の done から（再生でも）。report の途中は title に。ヘルプは今の事実（F20）。"""
    from pathlib import Path
    web = Path(__file__).resolve().parents[1] / "web"
    js = (web / "progress.js").read_text(encoding="utf-8")
    assert "if (!st || !st.steps || !FILM_STEPS[k]) return rep;" in js and "if (!LIVE || !st" not in js
    assert "中身は${ST_JA[s.status] || s.status}" in js
    s = (web / "staff.html").read_text(encoding="utf-8")
    assert "1 棟を最後まで通した記録は、まだありません" not in s
    assert "最初から最後まで止まらずに通した記録はまだありません" in s and "進行役に決めさせて続ける」で再開し、歩く画面まで届きました" in s
