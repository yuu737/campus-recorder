"""登録台帳の鍵（J-012）：読む → 計算（鍵の外）→ 鍵を取って読み直し → 差分を書く。

台帳は一時フォルダの写しだけ（本物の data/photos/registry.json は触らない）。鍵の場所も一時フォルダ。"""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager

import numpy as np
import pytest

from recorder import ledger, registry


@pytest.fixture(autouse=True)
def _tmp_lock_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDER_LOCK_DIR", str(tmp_path / "locks"))


def ent(photo, face="S", building="x", **kw):
    return {"photo": photo, "building": building, "face": face, "H_plane": np.eye(3).tolist(), **kw}


def _write(p, entries, nr=()):
    p.write_text(json.dumps({"schema": registry.SCHEMA, "entries": entries, "not_registered": list(nr)}), encoding="utf-8")


def test_save_applies_only_my_diff(tmp_path):
    p = tmp_path / "registry.json"
    _write(p, [ent("A"), ent("D")], [{"photo": "N", "building": "x", "face": "S", "reason": "前の理由"}])
    mine = registry.load_registry(p)              # 長い計算の前に読む
    other = registry.load_registry(p)             # 別のプロセス（同時に回る別の棟）
    registry.upsert(other, ent("B"))
    other["not_registered"].append({"photo": "M", "building": "y", "face": "E", "reason": "別の棟"})
    registry.save_registry(other, p)
    # 自分の計算の結果：C を足し、A を直し、D を外す
    registry.upsert(mine, ent("C"))
    registry.upsert(mine, ent("A", note="直した"))
    mine["entries"] = [e for e in mine["entries"] if e["photo"] != "D"]
    st = registry.save_registry(mine, p)
    back = json.loads(p.read_text(encoding="utf-8"))
    got = {e["photo"]: e for e in back["entries"]}
    assert set(got) == {"A", "B", "C"}            # 他のプロセスの B が消えない
    assert got["A"]["note"] == "直した"
    assert {n["photo"] for n in back["not_registered"]} == {"N", "M"}
    assert st["mode"] == "diff" and st["changed"] == 3 and st["conflicts"] == []
    assert {e["photo"] for e in mine["entries"]} == {"A", "B", "C"}   # 手元も書いた中身にそろう
    # 続けて保存しても、前の保存からの差分だけ
    registry.upsert(mine, ent("E"))
    assert registry.save_registry(mine, p)["changed"] == 1


def test_same_unit_changed_by_both_mine_wins_and_is_counted(tmp_path):
    p = tmp_path / "registry.json"
    _write(p, [ent("A")])
    mine, other = registry.load_registry(p), registry.load_registry(p)
    registry.upsert(other, ent("A", note="他"))
    registry.save_registry(other, p)
    registry.upsert(mine, ent("A", note="自分"))
    st = registry.save_registry(mine, p)
    assert st["conflicts"] == [["entries", ["x", "A", "S"]]]
    assert json.loads(p.read_text(encoding="utf-8"))["entries"][0]["note"] == "自分"


def test_not_loaded_dict_is_written_whole(tmp_path):
    p = tmp_path / "registry.json"
    _write(p, [ent("A")])
    reg = {"schema": registry.SCHEMA, "entries": [ent("Z")], "not_registered": []}
    assert registry.save_registry(reg, p)["mode"] == "whole"
    assert [e["photo"] for e in json.loads(p.read_text(encoding="utf-8"))["entries"]] == ["Z"]


def _record_lock(monkeypatch):
    """ledger.lock を包み、鍵を持った時間を数える。"""
    spans = []
    real = ledger.lock

    @contextmanager
    def rec(name="ledger", **kw):
        with real(name, **kw):
            t0 = time.time()
            try:
                yield
            finally:
                spans.append((name, time.time() - t0, kw.get("label")))

    monkeypatch.setattr(ledger, "lock", rec)
    return spans


def test_register_chain_holds_lock_only_while_writing(tmp_path, monkeypatch):
    """register-chain の照合（ここでは 1.2 秒眠る偽物）の間は鍵を持たず、別のプロセス役が鍵を取って書ける。"""
    from recorder import __main__ as M
    from recorder import chain

    p = tmp_path / "registry.json"
    _write(p, [ent("A")])
    spans = _record_lock(monkeypatch)
    seen = {}

    def fake_chain(building_json, reg, n_via=3, photos=None):
        seen["held_during_compute"] = ledger.held()
        t0 = time.time()

        def other_writer():                       # 計算の途中で別の係が台帳を書く
            with ledger.lock("ledger", timeout=0.5, label="other"):
                r = registry.load_registry(p)
                registry.upsert(r, ent("B"))
                registry.save_registry(r, p)
            seen["other_ok"] = True

        th = threading.Thread(target=other_writer)
        th.start()
        th.join(5)
        time.sleep(max(0.0, 1.2 - (time.time() - t0)))
        registry.upsert(reg, ent("C"))
        return {"added": 1, "rounds": [{"added": 1}], "failed": {}, "reasons": {}}

    monkeypatch.setattr(chain, "register_chain", fake_chain)
    t0 = time.time()
    rc = M.main(["photos", "register-chain", str(tmp_path / "b.json"), "--registry", str(p)])
    total = time.time() - t0
    assert rc == 0
    assert seen["held_during_compute"] == [] and seen.get("other_ok")
    mine = [s for n, s, label in spans if n == "ledger" and label != "other"]
    assert total >= 1.2 and mine and max(mine) < 0.5       # 鍵を持つのは読み直しと書き込みだけ
    back = {e["photo"] for e in json.loads(p.read_text(encoding="utf-8"))["entries"]}
    assert back == {"A", "B", "C"}


@pytest.mark.parametrize("cmd", ["register-chain", "register-sfm", "pnp", "unregister", "exclude"])
def test_long_registration_commands_do_not_take_the_whole_command_lock(cmd):
    from recorder import __main__ as M

    class A:
        pass

    a = A()
    a.cmd, a.photos_cmd = "photos", cmd
    assert M._ledger_need(a) is None
    a.photos_cmd = "assign"                       # 索引を書くコマンドは今まで通り全体で鍵
    assert M._ledger_need(a) == "recorder photos assign"
    a.photos_cmd = "import"                       # 取り込みは縮小・顔の検出を鍵の外で回し、差分だけ鍵の中で書く（11/8 F3）
    assert M._ledger_need(a) is None
