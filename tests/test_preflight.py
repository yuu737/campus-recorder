"""再開の前の点検（recorder agent preflight）。SDK は呼ばない（--call-models は agents._invoke をモック）。"""
import json
import shutil
from pathlib import Path

import pytest

from recorder import agents, preflight

ROOT = Path(__file__).resolve().parents[1]
STEPS = ["sokuryo", "sekkei", "seizu", "kenshu", "seizu_final", "web"]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    shutil.copytree(ROOT / ".claude" / "agents", tmp_path / ".claude" / "agents")
    (tmp_path / "data/photos").mkdir(parents=True)
    (tmp_path / "data/photos/index.json").write_text('{"photos": []}', encoding="utf-8")
    (tmp_path / "data/photos/registry.json").write_text('{"entries": []}', encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/手順_エージェント.md").write_text("# 手順\n", encoding="utf-8")
    blender = tmp_path / "blender.exe"
    blender.write_text("x", encoding="utf-8")
    monkeypatch.setenv("BLENDER", str(blender))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("RECORDER_LOCK_DIR", str(tmp_path / "locks"))
    return tmp_path


def _state(repo, **kw):
    st = agents._new_state("b1", "continue", None, STEPS, None, 40.0)
    st.update(status="stopped", done=["sokuryo", "sekkei", "seizu", "kenshu"], spent_usd=9.0,
              stopped={"step": "kenshu", "reason": "event", "events": []}, **kw)
    agents._save_state(st, repo)
    return st


def _levels(doc, what):
    return [i["level"] for i in doc["items"] if i["what"] == what]


def test_ok_state_has_no_ng(repo):
    _state(repo)
    doc = preflight.run("b1", cwd=repo, check_serve=False)
    assert doc["ok"], [i for i in doc["items"] if i["level"] == "ng"]
    assert _levels(doc, "記録") == ["ok"] and _levels(doc, "BLENDER") == ["ok"]
    assert _levels(doc, "費用") == ["ok"] and (repo / "out/b1/preflight.json").exists()
    assert any("落ちる 0" in x for x in preflight.lines(doc))


def test_missing_and_broken_state(repo):
    doc = preflight.run("b1", cwd=repo, check_serve=False)
    assert not doc["ok"] and _levels(doc, "記録") == ["ng"]
    p = agents.state_path("b1", repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"building": ', encoding="utf-8")
    doc = preflight.run("b1", cwd=repo, check_serve=False)
    assert not doc["ok"] and "読めない" in [i for i in doc["items"] if i["what"] == "記録"][0]["why"]


def test_done_out_of_order_and_no_blender(repo, monkeypatch):
    st = _state(repo)
    st["done"] = ["sekkei", "sokuryo"]
    agents._save_state(st, repo)
    monkeypatch.delenv("BLENDER")
    doc = preflight.run("b1", cwd=repo, check_serve=False)
    assert not doc["ok"] and _levels(doc, "done の順") == ["ng"] and _levels(doc, "BLENDER") == ["ng"]


def test_budget_and_key_warnings(repo, monkeypatch):
    _state(repo, max_usd=12.0)
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    doc = preflight.run("b1", cwd=repo, check_serve=False)
    assert doc["ok"] and _levels(doc, "API キー") == ["warn"] and _levels(doc, "費用") == ["warn"]
    _state(repo, max_usd=9.0)
    doc = preflight.run("b1", cwd=repo, check_serve=False)
    assert not doc["ok"] and _levels(doc, "費用") == ["ng"]


def test_events_plan(repo):
    (repo / "data/buildings").mkdir(parents=True)
    (repo / "data/buildings/b1.json").write_text(json.dumps({"id": "b1", "requests": []}), encoding="utf-8")
    (repo / "out/b1").mkdir(parents=True, exist_ok=True)
    (repo / "out/b1/soten.json").write_text(json.dumps({"items": [{"status": "open"}]}), encoding="utf-8")
    _state(repo)
    doc = preflight.run("b1", cwd=repo, check_serve=False)
    ev = [i for i in doc["items"] if i["what"] == "事件"][0]
    assert ev["level"] == "ok" and ev["shinko_ok"] and ev["stop_events"][0].startswith("stuck|")
    assert doc["plan"]["after_step"] == "kenshu"


def test_serve_not_running_is_warning(repo):
    _state(repo)
    doc = preflight.run("b1", cwd=repo, port=1)
    assert _levels(doc, "serve") == ["warn"]


def test_call_models_classifies(repo, monkeypatch):
    _state(repo)
    seen = []

    def fake(spec, task, cwd, max_turns, max_budget_usd, resume=None):
        seen.append((spec["model"], max_turns, max_budget_usd))
        if spec["model"] == "fable":
            raise agents.InvokeError("API Error: 429 rate_limit", rate_limited=True)
        if spec["model"] == "haiku":
            raise agents.InvokeError("API Error: 404 not_found_error model: claude-haiku-5-5")
        if spec["model"] == "sonnet":
            raise agents.InvokeError("API Error: 403 permission_error")
        return {"text": "OK", "is_error": False, "cost_usd": 0.001, "stop": "success"}

    monkeypatch.setattr(agents, "_invoke", fake)
    doc = preflight.run("b1", cwd=repo, check_serve=False, call_models=True)
    models = {i["model"]: i for i in doc["items"] if i.get("model")}
    assert all(t == 1 and u == 0.05 for _m, t, u in seen)
    assert models["opus"]["level"] == "ok"
    assert models["fable"]["kind"].startswith("429")
    assert models["haiku"]["kind"].startswith("モデルが無い")
    assert models["sonnet"]["kind"].startswith("権限が無い")
    assert not doc["ok"]


def test_cli_exit_code(repo, monkeypatch, capsys):
    from recorder.__main__ import main
    monkeypatch.chdir(repo)
    rc = main(["agent", "preflight", "b1", "--no-serve"])
    assert rc == 1 and "落ちる" in capsys.readouterr().out
