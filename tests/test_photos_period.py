"""期間前 / 期間中の判定、台帳の読み込み、既存の索引が変わらないこと。写真本体は使わず小さな画像を作る。"""

import json
from pathlib import Path

from PIL import Image

import pytest

from recorder import photos


START = "2026-11-02T12:00:00"
SITE = {"photo_policy": {"period_start": START}}   # 期間の決まりは敷地 JSON の photo_policy（11/8 F3）


def test_period_of():
    assert photos.period_of("2026-11-02T11:59:59", START) == "before"
    assert photos.period_of("2026-11-02T12:00:00", START) == "during"
    assert photos.period_of("2026-11-03T13:27:21", START) == "during"
    assert photos.period_of("2026-09-07", START) == "before"
    assert photos.period_of(None, START) is None
    # 期間の決まりが無い（既定）：他の大学の手持ちの写真も消えない
    assert photos.period_of("2026-09-07") == "during"
    assert photos.period_start_of(SITE) == START and photos.period_start_of(None) is None


@pytest.fixture(autouse=True)
def _aliases(monkeypatch):
    # 名前の対応表は site.campus_aliases が持つ。ここでは固定の表を差す
    monkeypatch.setattr(photos.site, "campus_aliases", lambda campus=None: {
        "本部棟": "honbu", "タワー75": "tower75", "タワー": "tower75", "図書館": "library", "11号館": "b11"}, raising=False)


def test_ledger_building_mapping():
    assert photos.ledger_building("06_本部棟_外観とピロティ") == "honbu"
    assert photos.ledger_building("01_タワー75_外観と入口") == "tower75"
    assert photos.ledger_building("03_附属図書館周辺_赤い階段と庇") == "library"
    assert photos.ledger_building("11_11号館周辺") == "b11"
    assert photos.ledger_building("15_LIONS_ARENA") is None
    assert photos.ledger_building("図書館・11号館間の上段通路") is None   # 2 棟にまたがる物は決めない


def _make(folder: Path, name: str):
    folder.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 48), (120, 120, 120)).save(folder / name)


def test_import_with_ledger_and_period(tmp_path):
    src = tmp_path / "写真資料_x"
    _make(src / "06_本部棟_外観とピロティ", "P001_a.jpg")
    _make(src / "15_LIONS_ARENA", "P002_b.jpg")
    _make(src / "閲覧用", "サムネイル.jpg")   # 台帳に無い物は拾わない
    ledger_path = src / "写真台帳.json"
    ledger_path.write_text(json.dumps([
        {"id": "P001", "path": "06_本部棟_外観とピロティ/P001_a.jpg", "folder": "06_本部棟_外観とピロティ",
         "subject": "柱列", "takenAt": "2026:09:07 17:36:24", "sha256": "ab"},
        {"id": "P002", "path": "15_LIONS_ARENA/P002_b.jpg", "folder": "15_LIONS_ARENA", "subject": "階段",
         "takenAt": "2026:09:07 17:40:00"},
    ], ensure_ascii=False), encoding="utf-8")
    idx_path = tmp_path / "index.json"
    idx, added = photos.import_photos(src, SITE, index_path=idx_path, root=tmp_path / "data",
                                      ledger=photos.load_ledger(ledger_path))
    assert len(added) == 2
    by = {r["id"]: r for r in idx["photos"]}
    a, b = by["P001_a"], by["P002_b"]
    assert a["period"] == "before" and a["taken_at"] == "2026-09-07T17:36:24"
    assert a["building"] == "honbu" and a["building_source"] == "台帳(期間前)"
    assert a["face"] is None and a["ledger"]["source"] == "台帳(期間前)"
    assert b["building"] is None and b["ledger_subject"] == "15_LIONS_ARENA"
    assert Path(a["file"]).is_absolute() or ":" in a["file"] or a["file"].startswith("/")   # フォルダ外は絶対パス
    # period を指定すると優先
    idx2, _ = photos.import_photos(src, SITE, index_path=tmp_path / "i2.json", root=tmp_path / "data",
                                   period="during", ledger=photos.load_ledger(ledger_path))
    assert {r["period"] for r in idx2["photos"]} == {"during"}
    counts = photos.period_counts(idx)
    assert counts["before"] == 2 and counts["ledger"] == 2


def test_reimport_keeps_human_values(tmp_path):
    src = tmp_path / "s"
    _make(src, "IMG_1.jpg")
    idx_path = tmp_path / "index.json"
    idx, _ = photos.import_photos(src, None, index_path=idx_path, root=tmp_path / "data", period="during")
    idx["photos"][0].update({"building": "honbu", "face": "approach", "notes": "人が入れた"})
    photos.save_index(idx, idx_path)
    before = json.loads(idx_path.read_text(encoding="utf-8"))["photos"][0]
    idx, added = photos.import_photos(src, None, index_path=idx_path, root=tmp_path / "data", period="during")
    after = idx["photos"][0]
    assert not added and len(idx["photos"]) == 1
    assert (after["building"], after["face"], after["notes"]) == (before["building"], before["face"], before["notes"])


def test_committed_index_existing_records_intact():
    """取り込み前からある 20261103 系の記録が残っていて、期間中と判定される。"""
    p = Path("data/photos/index.json")
    if not p.exists():
        return
    rows = [r for r in json.loads(p.read_text(encoding="utf-8"))["photos"] if str(r.get("file", "")).startswith("2026110")]
    assert len(rows) >= 124
    assert all(photos.period_of(r["taken_at"], START) == "during" for r in rows)
    assert all(r.get("period") == "during" for r in rows)
