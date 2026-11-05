"""職員がフォルダで振り分けた写真を索引に反映する assign_from_folders のテスト（tmp_path。本物は触らない）。"""
import json

import pytest

from recorder import photos

ALIASES = {"本部棟": "honbu", "図書館": "library", "名城大学付属図書館": "library", "タワー75": "tower75",
           "タワー": "tower75", "共通講義棟南": "kyotsu-minami", "honbu": "honbu"}


@pytest.fixture(autouse=True)
def _aliases(monkeypatch):
    monkeypatch.setattr(photos.site, "campus_aliases", lambda campus=None: dict(ALIASES), raising=False)


def test_parse_folder_label():
    r = photos.parse_folder_label("タワー", ALIASES)
    assert r == {"ids": ["tower75"], "interior": False, "floor": None}
    r = photos.parse_folder_label("主に図書館and本部棟と図書館の間", ALIASES)
    assert r["ids"] == ["library", "honbu"] and not r["interior"]
    r = photos.parse_folder_label("タワー 1階 内装", ALIASES)
    assert r == {"ids": ["tower75"], "interior": True, "floor": 1}
    r = photos.parse_folder_label("タワー75_3F", ALIASES)
    assert r["interior"] and r["floor"] == 3 and r["ids"] == ["tower75"]
    assert photos.parse_folder_label("謎の棟", ALIASES)["ids"] == []


def _setup(tmp_path):
    root = tmp_path / "photos"
    day = root / "20261105"
    (day / "タワー").mkdir(parents=True)
    (day / "主に図書館and本部棟と図書館の間").mkdir()
    (day / "謎の棟").mkdir()
    for name, folder in [("A.jpg", "タワー"), ("B.jpg", "タワー"), ("C.jpg", "主に図書館and本部棟と図書館の間"),
                         ("D.jpg", "謎の棟")]:
        (day / folder / name).write_bytes(b"x")
    (day / "E.jpg").write_bytes(b"x")   # フォルダ外（内装で既に振り分け済み）
    recs = []
    for pid in "ABCDE":
        recs.append({"id": pid, "file": f"20261105/{pid}.jpg", "building": None, "face": None, "notes": "",
                     "taken_at": "2026-11-05T10:00:00", "candidates": []})
    recs[1]["building"] = "tower75"; recs[1]["assigned_by"] = "staff"; recs[1]["interior"] = False   # B は既に職員が tower75
    recs[4].update({"building": "tower75", "assigned_by": "staff", "interior": True, "floor": 1})
    idx = root / "index.json"
    idx.write_text(json.dumps({"schema": "x", "photos": recs}, ensure_ascii=False), encoding="utf-8")
    return root, day, idx


def test_folders_assign_choices_rename_paths(tmp_path):
    root, day, idx = _setup(tmp_path)
    r = photos.assign_from_folders(day, index_path=idx, now="2026-11-06T09:00:00")
    assert r["assigned"] == 1 and r["unchanged"] == 1 and r["choices"] == 1
    assert any(w.get("folder") == "謎の棟" for w in r["warnings"])
    assert (day / "tower75").is_dir() and not (day / "タワー").exists()
    assert (day / "library-honbu").is_dir()
    assert (day / "謎の棟").is_dir()          # 読めない名前は変えない
    ph = {p["id"]: p for p in json.loads(idx.read_text(encoding="utf-8"))["photos"]}
    assert ph["A"]["building"] == "tower75" and ph["A"]["assigned_by"] == "staff" and ph["A"]["file"] == "20261105/tower75/A.jpg"
    assert ph["B"]["file"] == "20261105/tower75/B.jpg" and "職員が振り分け" not in ph["B"]["notes"]   # 書き直さない
    assert ph["C"]["building"] is None and ph["C"]["staff_choices"] == ["library", "honbu"]
    assert ph["C"]["file"] == "20261105/library-honbu/C.jpg" and ph["C"]["staff_label"] == "主に図書館and本部棟と図書館の間"
    assert ph["D"]["building"] is None and ph["D"]["file"] == "20261105/謎の棟/D.jpg" and ph["D"]["staff_label"] == "謎の棟"
    assert ph["E"]["file"] == "20261105/E.jpg" and ph["E"]["interior"] is True
    assert r["paths_updated"] == 4 and not r["unmatched"]


def test_folders_idempotent_and_merge(tmp_path):
    root, day, idx = _setup(tmp_path)
    photos.assign_from_folders(day, index_path=idx, now="2026-11-06T09:00:00")
    # 職員がもう 1 枚を「タワー」フォルダに足した → 既存の tower75 に合流
    (day / "タワー").mkdir()
    (day / "タワー" / "F.jpg").write_bytes(b"x")
    d = json.loads(idx.read_text(encoding="utf-8"))
    d["photos"].append({"id": "F", "file": "20261105/F.jpg", "building": None, "face": None, "notes": "", "candidates": []})
    idx.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    r = photos.assign_from_folders(day, index_path=idx, now="2026-11-06T10:00:00")
    assert r["assigned"] == 1 and r["unchanged"] == 2 and r["choices"] == 0
    assert (day / "tower75" / "F.jpg").exists() and not (day / "タワー").exists()
    ph = {p["id"]: p for p in json.loads(idx.read_text(encoding="utf-8"))["photos"]}
    assert ph["F"]["file"] == "20261105/tower75/F.jpg" and ph["F"]["building"] == "tower75"
    assert ph["C"]["notes"].count("職員のフォルダ") == 1


def test_no_rename(tmp_path):
    root, day, idx = _setup(tmp_path)
    r = photos.assign_from_folders(day, index_path=idx, rename=False)
    assert (day / "タワー").is_dir()
    ph = {p["id"]: p for p in json.loads(idx.read_text(encoding="utf-8"))["photos"]}
    assert ph["A"]["file"] == "20261105/タワー/A.jpg" and r["paths_updated"] == 4
