"""共有の台帳と GPU の鍵（recorder/ledger.py）と、仕分け係の書き戻し（photos.set_faces）。11/7、4 棟を同時に回すため。"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from recorder import ledger, photos

REPO = Path(__file__).resolve().parents[1]

_WORKER = r"""
import json, sys, time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from recorder import ledger
p = Path(sys.argv[2])
for _ in range(int(sys.argv[3])):
    with ledger.lock("ledger", label="test"):
        n = json.loads(p.read_text(encoding="utf-8"))["n"]
        time.sleep(0.005)                       # 読む → 書くの間に他のプロセスが割り込める隙を作る
        ledger.write_text_atomic(p, json.dumps({"n": n + 1}))
"""

_HOLDER = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from recorder import ledger
with ledger.lock(sys.argv[2], label="holder"):
    print("held", flush=True)
    time.sleep(float(sys.argv[3]))
"""


@pytest.fixture
def lockdir(tmp_path, monkeypatch):
    d = tmp_path / "locks"
    monkeypatch.setenv("RECORDER_LOCK_DIR", str(d))
    return d


def _env(lockdir):
    e = dict(os.environ)
    e["RECORDER_LOCK_DIR"] = str(lockdir)
    return e


def test_processes_do_not_lose_writes(tmp_path, lockdir):
    """4 つのプロセスが同じ台帳を 15 回ずつ「読む → 足す → 書く」。鍵があれば 60 回分が全部残る。"""
    p = tmp_path / "counter.json"
    p.write_text(json.dumps({"n": 0}), encoding="utf-8")
    procs = [subprocess.Popen([sys.executable, "-c", _WORKER, str(REPO), str(p), "15"], env=_env(lockdir))
             for _ in range(4)]
    for pr in procs:
        assert pr.wait(timeout=120) == 0
    assert json.loads(p.read_text(encoding="utf-8"))["n"] == 60
    waits = [json.loads(x) for x in (lockdir / "waits.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(waits) == 60 and all(w["lock"] == "ledger" for w in waits)


def test_other_process_waits_and_timeout(lockdir):
    """別のプロセスが鍵を持っている間は取れない（timeout で TimeoutError）。離されたら取れる。"""
    h = subprocess.Popen([sys.executable, "-c", _HOLDER, str(REPO), "gpu", "2.0"], env=_env(lockdir),
                         stdout=subprocess.PIPE, text=True)
    assert h.stdout.readline().strip() == "held"
    with pytest.raises(TimeoutError):
        with ledger.lock("gpu", timeout=0.5):
            pass
    t0 = time.time()
    with ledger.lock("gpu", timeout=30):
        waited = time.time() - t0
    h.wait(timeout=30)
    assert waited > 0.3


def test_reentrant_in_same_thread_and_threads_wait(lockdir):
    order = []
    with ledger.lock("ledger"):
        with ledger.lock("ledger"):                      # 同じスレッドの入れ子はすぐ通る
            order.append("nested")

        def other():
            with ledger.lock("ledger"):
                order.append("thread")
        t = threading.Thread(target=other)
        t.start()
        time.sleep(0.3)
        order.append("main-still-holding")              # 別のスレッドは待っている
    t.join(timeout=10)
    assert order == ["nested", "main-still-holding", "thread"]


def test_lock_order_violation(lockdir):
    with ledger.lock("gpu"):
        with pytest.raises(RuntimeError):
            with ledger.lock("ledger"):
                pass
    with ledger.lock("ledger"):                          # 正しい順（ledger → gpu）は通る
        with ledger.lock("gpu"):
            assert ledger.held() == ["ledger", "gpu"]
    assert ledger.held() == []


def test_write_text_atomic(tmp_path):
    p = tmp_path / "a" / "x.json"
    ledger.write_text_atomic(p, '{"a": 1}')
    ledger.write_text_atomic(p, '{"a": 2}')
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 2}
    assert [f.name for f in p.parent.iterdir()] == ["x.json"]          # 一時ファイルが残らない


def test_cli_commands_that_need_the_lock():
    from argparse import Namespace

    from recorder.__main__ import _ledger_need
    assert _ledger_need(Namespace(cmd="photos", photos_cmd="set-face")) == "recorder photos set-face"
    assert _ledger_need(Namespace(cmd="photos", photos_cmd="pnp"))
    assert _ledger_need(Namespace(cmd="photos", photos_cmd="register-chain"))
    assert _ledger_need(Namespace(cmd="agent", agent_cmd="decide"))
    assert _ledger_need(Namespace(cmd="photos", photos_cmd="rectify")) is None
    assert _ledger_need(Namespace(cmd="validate")) is None


def _idx(tmp_path, rows):
    p = tmp_path / "index.json"
    p.write_text(json.dumps({"schema": "x", "photos": rows}, ensure_ascii=False), encoding="utf-8")
    return p


def test_set_faces_rules(tmp_path):
    p = _idx(tmp_path, [
        {"id": "S", "building": "b1", "assigned_by": "staff", "face": None, "notes": "職員が振り分け（t）"},
        {"id": "N", "building": None, "face": None},
        {"id": "C", "building": None, "staff_choices": ["b1", "b2"], "face": None},
        {"id": "O", "building": "b2", "assigned_by": "shiwake", "face": "f0"},
        {"id": "I", "building": "b1", "assigned_by": "staff", "interior": True, "face": None},
    ])
    doc = {"photos": {
        "S": {"building": "b1", "face": "f1", "faces": ["f1", "f2"], "confidence": "high", "evidence": "正面"},
        "N": {"building": "b1", "face": "f3", "faces": ["f3"], "confidence": "mid", "evidence": "北の端"},
        "C": {"building": "b3", "face": "f0"},
        "O": {"building": "b1", "face": "f1"},
        "I": {"building": "b1", "face": "f0"},
        "Z": {"building": "b1", "face": "f0"},
    }}
    res = photos.set_faces(p, doc, "仕分け係（opus）", now="T")
    got = {r["id"]: r for r in json.loads(p.read_text(encoding="utf-8"))["photos"]}
    assert res["set"] == 3 and res["building_set"] == 1 and res["not_found"] == ["Z"]
    assert {s["id"] for s in res["skipped"]} == {"C", "O"}
    assert got["S"]["face"] == "f1" and got["S"]["faces"] == ["f1", "f2"] and got["S"]["assigned_by"] == "staff"
    assert got["S"]["notes"] == "職員が振り分け（t） | 仕分け係（opus）：正面" and got["S"]["confidence"] == "high"
    assert got["N"]["building"] == "b1" and got["N"]["assigned_by"] == "shiwake" and got["N"]["assigned_at"] == "T"
    assert got["C"]["building"] is None and got["O"]["building"] == "b2" and got["O"]["face"] == "f0"
    assert got["I"]["face"] is None and got["I"]["interior_views"] is True


def test_set_faces_marks_tape_photos(tmp_path):
    """メジャーの写真の印（ruler）を索引に書く。台帳に無い印は書かずに理由を出す（面は書く）。"""
    p = _idx(tmp_path, [{"id": "T", "building": "b1", "assigned_by": "staff", "face": None},
                        {"id": "U", "building": "b1", "assigned_by": "staff", "face": None},
                        {"id": "V", "building": "b1", "assigned_by": "staff", "face": None}])
    doc = {"photos": {
        "T": {"face": "f1", "ruler": "tape_measure", "ruler_note": "f1 の柱型の幅に水平。0〜120 cm"},
        "U": {"face": "f2", "ruler": "folding_rule"},
        "V": {"face": "f3"}}}
    res = photos.set_faces(p, doc, "仕分け係")
    got = {r["id"]: r for r in json.loads(p.read_text(encoding="utf-8"))["photos"]}
    assert got["T"]["ruler"] == "tape_measure" and got["T"]["ruler_note"].startswith("f1 の柱型")
    assert "ruler" not in got["U"] and got["U"]["face"] == "f2" and any(s["id"] == "U" for s in res["skipped"])
    assert "ruler" not in got["V"]


def test_photos_list_filters_by_ruler(tmp_path, monkeypatch, capsys):
    from recorder.__main__ import main
    p = _idx(tmp_path, [{"id": "T", "building": "b1", "file": "x/T.jpg", "ruler": "tape_measure", "ruler_note": "f1"},
                        {"id": "N", "building": "b1", "file": "x/N.jpg"},
                        {"id": "O", "building": "b2", "file": "x/O.jpg", "ruler": "tape_measure"}])
    monkeypatch.setattr(photos, "INDEX_PATH", p)
    assert main(["photos", "list", "--building", "b1", "--ruler", "tape_measure"]) == 0
    out = capsys.readouterr().out
    assert "T" in out and "物差し tape_measure" in out and "N.jpg" not in out and "O.jpg" not in out


def test_agent_tasks_mention_tape_both_ways():
    """依頼文：仕分け係は印を付け、測量係はメジャーがある時もない時も進む。"""
    from recorder import agents
    import inspect
    src = inspect.getsource(agents)
    assert 'ruler: \\"tape_measure\\"' in src and "--ruler tape_measure" in src
    assert "無ければ PLATEAU 以下で換算して estimated、止めずに撮影依頼を 1 件だけ足す" in src
    assert "別の面へ当てはめた値は仮定で estimated" in src


def test_log_step_records_times_attempts_and_lock_waits(tmp_path, lockdir):
    """agent log：始め・終わりの時刻、何回目か、結果、道具の回数と、その間にこの棟のコマンドが鍵を待った秒を残す。"""
    from recorder import agents
    lockdir.mkdir(parents=True, exist_ok=True)
    (lockdir / "waits.jsonl").write_text("\n".join(json.dumps(w, ensure_ascii=False) for w in [
        {"at": "2026-11-07T09:00:30", "lock": "ledger", "label": "recorder photos set-face --from out/photos/shiwake_b1.json", "waited_s": 4.0},
        {"at": "2026-11-07T09:01:00", "lock": "gpu", "label": "model build b1 --fast", "waited_s": 20.5},
        {"at": "2026-11-07T09:01:10", "lock": "gpu", "label": "model build b2 --fast", "waited_s": 99.0},     # 別の棟
        {"at": "2026-11-07T09:30:00", "lock": "ledger", "label": "recorder photos register x --building data/buildings/b1.json", "waited_s": 7.0},  # 時間の外
    ]) + "\n", encoding="utf-8")
    rec = tmp_path / "rec.json"
    r1 = agents.log_step("b1", "shiwake", model="opus", seconds=120, tokens=50000, record=rec, cwd=tmp_path,
                         started_at="2026-11-07T09:00:00", ended_at="2026-11-07T09:02:00", attempt=1, result="ok", tool_uses=31)
    a = r1["added"]
    assert a["started_at"] == "2026-11-07T09:00:00" and a["attempt"] == 1 and a["result"] == "ok" and a["tool_uses"] == 31
    assert a["lock_wait_s"] == {"ledger": 4.0, "gpu": 20.5, "n": 2}
    r2 = agents.log_step("b1", "kansatsu", seconds=60, tokens=30000, record=rec, cwd=tmp_path, ended_at="2026-11-07T09:05:00")
    assert r2["added"]["started_at"] == "2026-11-07T09:04:00"                         # 終わり − seconds
    doc = json.loads(rec.read_text(encoding="utf-8"))
    assert doc["tokens_total"] == 80000 and doc["seconds_total"] == 180 and doc["wall_seconds"] == 300
    assert doc["lock_wait_total_s"] == {"ledger": 4.0, "gpu": 20.5}


def test_rulers_tape_note_has_conditions():
    r = json.loads((REPO / "data/rulers.json").read_text(encoding="utf-8"))
    t = next(x for x in r["rulers"] if x["id"] == "tape_measure")
    assert t["status"] == "measured" and t["tol"] == 0.01
    assert "同じ正面化画像・同じ面" in t["note"] and "読み取り" in t["note"] and "estimated" in t["note"]
