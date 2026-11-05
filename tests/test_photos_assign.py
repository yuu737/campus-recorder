"""職員の振り分け assign_photos のテスト（索引は tmp_path。本物は触らない）。"""
import json

import pytest

from recorder import photos

ALIASES = {"本部棟": "honbu", "honbu": "honbu", "タワー75": "tower75", "tower75": "tower75"}


@pytest.fixture(autouse=True)
def _aliases(monkeypatch):
    monkeypatch.setattr(photos.site, "campus_aliases", lambda campus=None: dict(ALIASES), raising=False)


def _index(tmp_path, photos_):
    p = tmp_path / "index.json"
    p.write_text(json.dumps({"schema": "x", "photos": photos_}, ensure_ascii=False), encoding="utf-8")
    return p


def _load(p):
    return json.loads(p.read_text(encoding="utf-8"))["photos"]


def _ph(pid, **kw):
    d = {"id": pid, "file": f"20261103/{pid}.jpg", "building": None, "face": "f1", "notes": "", "taken_at": "2026-11-03T10:00:00"}
    d.update(kw)
    return d


def test_by_id_and_file_and_alias(tmp_path):
    p = _index(tmp_path, [_ph("A"), _ph("B")])
    r = photos.assign_photos(p, [{"id": "A", "building": "本部棟", "interior": False},
                                 {"file": "B.jpg", "building": "tower75", "interior": False}],
                             now="2026-11-05T10:00:00")
    assert r["assigned"] == 2 and not r["errors"] and not r["not_found"]
    a, b = _load(p)
    assert a["building"] == "honbu" and a["assigned_by"] == "staff" and a["assigned_at"] == "2026-11-05T10:00:00"
    assert b["building"] == "tower75"
    assert a["face"] == "f1" and a["interior"] is False   # 外観は face に触らない


def test_interior_clears_face(tmp_path):
    p = _index(tmp_path, [_ph("A")])
    r = photos.assign_photos(p, [{"id": "A", "building": "honbu", "interior": True, "floor": 3}])
    a = _load(p)[0]
    assert r["interior"] == 1
    assert a["face"] is None and a["interior"] is True and a["floor"] == 3 and a["interior_views"] is True


def test_unknown_alias_and_not_found(tmp_path):
    p = _index(tmp_path, [_ph("A")])
    r = photos.assign_photos(p, [{"id": "A", "building": "謎棟"}, {"id": "ZZ", "building": "honbu"},
                                 {"file": "none.jpg", "building": "honbu"}])
    assert r["assigned"] == 0 and len(r["errors"]) == 1
    assert r["not_found"] == ["ZZ", "none.jpg"]
    assert _load(p)[0]["building"] is None


def test_gps_warning_keeps_staff_value(tmp_path):
    cands = [{"building": "tower75", "distance_m": 20.0}, {"building": "honbu", "distance_m": 50.0}]
    near = [{"building": "tower75", "distance_m": 20.0}, {"building": "honbu", "distance_m": 25.0}]
    p = _index(tmp_path, [_ph("A", candidates=cands), _ph("B", candidates=near), _ph("C", candidates=cands)])
    r = photos.assign_photos(p, [{"id": "A", "building": "honbu"}, {"id": "B", "building": "honbu"},
                                 {"id": "C", "building": "tower75"}])
    assert [w["id"] for w in r["warnings"]] == ["A"]
    assert r["warnings"][0]["staff"] == "honbu" and r["warnings"][0]["gps"] == "tower75"
    assert _load(p)[0]["building"] == "honbu"


def test_keeps_other_fields_and_notes(tmp_path):
    p = _index(tmp_path, [_ph("A", notes="仕分け係のメモ", ledger={"x": 1}, gps={"x": 1.0})])
    photos.assign_photos(p, [{"id": "A", "building": "honbu"}], now="2026-11-05T10:00:00")
    a = _load(p)[0]
    assert a["ledger"] == {"x": 1} and a["gps"] == {"x": 1.0}
    assert a["notes"].startswith("仕分け係のメモ") and "職員が振り分け（2026-11-05T10:00:00）" in a["notes"]
