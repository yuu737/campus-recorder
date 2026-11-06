"""仕分け係の当たり率（recorder/shiwake_eval.py）：小さな索引で一致・不一致・判定不能と一致率を確かめる。"""
from __future__ import annotations

import json
from pathlib import Path

from recorder import shiwake_eval as SE

ALIASES = {"タワー75": "tower75", "本部棟": "honbu", "図書館": "library", "9号館": "b09"}


def _lb(name: str) -> str | None:
    hits = {b for k, b in ALIASES.items() if k in name}
    return hits.pop() if len(hits) == 1 else None


def _rec(pid, building, folder, subject, notes="仕分け係（opus）：high", face=None, ledger_face=None, **kw):
    led = {"id": pid, "folder": folder, "source": "台帳(期間前)"}
    if ledger_face:
        led["face"] = ledger_face
    return {"id": pid, "building": building, "face": face, "notes": notes, "period": "before",
            "ledger_subject": subject, "ledger": led, "source": "台帳(期間前)", **kw}


def _index() -> dict:
    return {"photos": [
        _rec("a1", "tower75", "01_タワー75_外観", "タワー75_外観"),                 # 一致
        _rec("a2", "tower75", "01_タワー75_外観", "タワー75_外観"),                 # 一致
        _rec("a3", "library", "02_タワー75西側", "タワー75西側"),                   # 不一致
        _rec("a4", None, "06_本部棟_外観", "本部棟_外観"),                          # 判定不能（仕分け係が決めない）
        _rec("a5", "tower75", "S22_9号館_タワー75側", "9号館_タワー75側"),           # 判定不能（台帳で 2 棟）
        _rec("a6", "honbu", "06_本部棟_外観", "本部棟_外観", face="f1", ledger_face="f2"),  # 台帳に面 → 面が違うので不一致
        _rec("a7", "honbu", "06_本部棟_外観", "本部棟_外観", face="f2", ledger_face="f2"),  # 面も一致
        _rec("x1", "honbu", "06_本部棟_外観", "本部棟_外観", notes=""),              # 仕分け係が見ていない → 対象外
        {"id": "d1", "building": "library", "face": "f0", "notes": "", "assigned_by": "shiwake", "period": "during"},  # 台帳なし
    ]}


def test_counts_rate_and_mismatch_ids():
    r = SE.evaluate(_index(), _lb)
    c = r["counts"]
    assert (c["shiwake_reviewed"], c["with_ledger"], c["no_ledger"]) == (8, 7, 1)
    assert (c["match"], c["mismatch"], c["undetermined"], c["compared"]) == (3, 2, 2, 5)
    assert c["rate"] == 0.6
    assert (c["face_compared"], c["face_match"]) == (2, 1)
    assert sorted(m["id"] for m in r["mismatches"]) == ["a3", "a6"]
    assert sorted(r["undetermined_reasons"].values()) == [1, 1]
    tb = {t["ledger_building"]: t for t in r["by_building"]}
    assert tb["tower75"] == {"ledger_building": "tower75", "compared": 3, "match": 2, "mismatch": 1, "undetermined": 0,
                             "rate": round(2 / 3, 4)}
    assert tb["honbu"]["undetermined"] == 1 and tb["honbu"]["compared"] == 2
    assert tb[SE.NO_LEDGER_BUILDING]["undetermined"] == 1


def test_no_comparable_photos_gives_none_rate(tmp_path: Path):
    r = SE.evaluate({"photos": [_rec("a4", None, "06_本部棟_外観", "本部棟_外観")]}, _lb)
    assert r["counts"]["compared"] == 0 and r["counts"]["rate"] is None
    assert "比べられる写真が無い" in SE.summary_lines({**r})[0]


def test_run_writes_json(tmp_path: Path, monkeypatch):
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps({"schema": "x", "photos": _index()["photos"]}, ensure_ascii=False), encoding="utf-8")
    from recorder import photos
    monkeypatch.setattr(photos, "ledger_building", lambda name, campus=None: _lb(name))
    out = tmp_path / "eval.json"
    res = SE.run(idx, campus="test", out=out)
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["counts"] == res["counts"] and saved["counts"]["match"] == 3
