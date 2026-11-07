"""photos relink（K-105）・メジャーの枠（K-102）・set-face の穴（J-004）。11/8 T4。本物の索引は使わない。"""

import json

from PIL import Image

from recorder import photos

ALIASES = {"本部棟": "honbu", "図書館": "library", "タワー75": "tower75"}


def _jpg(path, size=(640, 480)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (90, 90, 90)).save(path, "JPEG")
    return path


def _idx(path, rows):
    path.write_text(json.dumps({"schema": "x", "photos": rows}, ensure_ascii=False), encoding="utf-8")
    return path


def _got(path):
    return {r["id"]: r for r in json.loads(path.read_text(encoding="utf-8"))["photos"]}


def test_relink_finds_moved_photos_by_name(tmp_path):
    new = tmp_path / "moved" / "メジャー"
    _jpg(new / "本部棟_honbu" / "A.jpg")
    _jpg(new / "x" / "B.jpg")
    _jpg(new / "y" / "B.jpg")                       # 同じ名前が 2 つ → 決めない
    _jpg(new / "図書館_library" / "C.jpg", (300, 200))  # 大きさが索引と違う → 付け直さない
    _jpg(tmp_path / "photos" / "ok" / "D.jpg")
    idx = _idx(tmp_path / "photos" / "index.json", [
        {"id": "A", "file": "E:/old/メジャー/本部棟_honbu/A.jpg", "width": 640, "height": 480},
        {"id": "B", "file": "E:/old/B.jpg"},
        {"id": "C", "file": "E:/old/C.jpg", "width": 640, "height": 480},
        {"id": "D", "file": "ok/D.jpg"},
        {"id": "Z", "file": "E:/old/Z.jpg"},
    ])
    rep = photos.relink(new, idx, aliases=ALIASES)
    got = _got(idx)
    assert [r["id"] for r in rep["relinked"]] == ["A"]
    assert got["A"]["file"] == (new / "本部棟_honbu" / "A.jpg").resolve().as_posix()
    assert got["A"]["relinked_from"] == "E:/old/メジャー/本部棟_honbu/A.jpg"
    assert [a["id"] for a in rep["ambiguous"]] == ["B"] and got["B"]["file"] == "E:/old/B.jpg"
    assert [m["id"] for m in rep["mismatch"]] == ["C"] and got["C"]["file"] == "E:/old/C.jpg"
    assert rep["still_missing"] == ["Z"] and got["D"]["file"] == "ok/D.jpg"
    assert "building" not in got["A"]                 # --assign-from-folder が無ければ棟は書かない


def test_relink_relative_inside_photo_root(tmp_path):
    root = tmp_path / "photos"
    _jpg(root / "20261108" / "honbu" / "A.jpg")
    idx = _idx(root / "index.json", [{"id": "A", "file": "20261105/A.jpg"}])
    photos.relink(root / "20261108", idx, aliases=ALIASES)
    assert _got(idx)["A"]["file"] == "20261108/honbu/A.jpg"


def test_relink_dry_run_writes_nothing(tmp_path):
    _jpg(tmp_path / "new" / "A.jpg")
    idx = _idx(tmp_path / "index.json", [{"id": "A", "file": "E:/old/A.jpg"}])
    before = idx.read_text(encoding="utf-8")
    rep = photos.relink(tmp_path / "new", idx, aliases=ALIASES, dry_run=True)
    assert len(rep["relinked"]) == 1 and idx.read_text(encoding="utf-8") == before


def test_assign_from_folder_is_staff_and_respects_existing_staff(tmp_path):
    m = tmp_path / "メジャー"
    for name in ("S1", "S2", "W", "N", "U"):
        _jpg(m / ("未定" if name == "U" else "本部棟_honbu") / f"{name}.jpg")
    idx = _idx(tmp_path / "index.json", [
        {"id": "S1", "file": "E:/old/S1.jpg", "building": "honbu", "assigned_by": "staff"},          # 同じ → 変えない
        {"id": "S2", "file": "E:/old/S2.jpg", "building": "library", "assigned_by": "staff"},        # 職員の別の棟 → 変えない
        {"id": "W", "file": (m / "本部棟_honbu" / "W.jpg").as_posix(), "building": "library",
         "assigned_by": "shiwake", "face": "f3", "faces": ["f3"]},                                   # 仕分け係の棟 → 職員の棟にして面を消す
        {"id": "N", "file": "E:/old/N.jpg", "building": None},
        {"id": "U", "file": "E:/old/U.jpg", "building": None},                                       # 未定のフォルダ → 決めない
    ])
    rep = photos.relink(m, idx, assign_from_folder=True, aliases=ALIASES, now="T")
    got = _got(idx)
    assert sorted(rep["assigned"]) == ["N", "W"] and rep["unchanged"] == 1
    assert [c["id"] for c in rep["conflicts"]] == ["S2"] and got["S2"]["building"] == "library"
    assert got["N"]["building"] == "honbu" and got["N"]["assigned_by"] == "staff"
    assert got["N"]["building_source"] == "staff_folder" and got["N"]["staff_label"] == "本部棟_honbu"
    assert got["W"]["building"] == "honbu" and got["W"]["face"] is None and got["W"]["faces"] == []
    assert rep["changed"][0]["from"]["building"] == "library"
    assert [u["id"] for u in rep["undecided"]] == ["U"] and got["U"]["building"] is None


def test_mark_tape_and_shiwake_keeps_staff_building_and_memo(tmp_path):
    idx = _idx(tmp_path / "index.json", [{"id": "T", "building": None, "face": None},
                                         {"id": "T2", "building": "library", "assigned_by": "shiwake", "face": "f1"}])
    res = photos.mark_tape(idx, ["T", "T2", "X"], "本部棟", face="f6", note="正面の柱型に水平。0〜92 cm", aliases=ALIASES, now="T")
    got = _got(idx)
    assert res["building"] == "honbu" and res["marked"] == ["T", "T2"] and res["not_found"] == ["X"]
    t = got["T"]
    assert (t["building"], t["assigned_by"], t["ruler"], t["ruler_by"]) == ("honbu", "staff", "tape_measure", "staff")
    assert t["ruler_note"] == t["ruler_note_staff"] == "正面の柱型に水平。0〜92 cm" and t["face"] == "f6"
    assert got["T2"]["building"] == "honbu" and got["T2"]["face"] == "f6" and res["changed"][0]["from"] == "library"
    # 仕分け係は棟を変えず、面と ruler_note だけ足す
    doc = {"building": "library", "photos": {"T": {"building": "library", "face": "f2"},
                                             "T2": {"face": "f5", "ruler": "tape_measure", "ruler_note": "目盛り 0〜90 が読める"}}}
    out = photos.set_faces(idx, doc, "仕分け係", building="honbu")
    got = _got(idx)
    assert got["T"]["building"] == "honbu" and got["T"]["face"] == "f6"     # 別の棟の行は書かない
    assert got["T2"]["face"] == "f5" and got["T2"]["ruler_note"] == "職員：正面の柱型に水平。0〜92 cm ／ 係：目盛り 0〜90 が読める"
    assert {s["id"] for s in out["skipped"]} == {"T"}


def test_mark_tape_rejects_unknown_building(tmp_path):
    idx = _idx(tmp_path / "index.json", [{"id": "T"}])
    try:
        photos.mark_tape(idx, ["T"], "知らない棟", aliases=ALIASES)
    except ValueError:
        pass
    else:
        raise AssertionError("知らない棟は書かない")


def test_set_face_null_building_row_does_not_touch_other_buildings_photo(tmp_path):
    """J-004：行の棟が null の時は呼び出した棟の写真として扱い、別の棟の写真の面を消さない。"""
    idx = _idx(tmp_path / "index.json", [
        {"id": "P", "building": "honbu", "assigned_by": "shiwake", "face": "f4", "faces": ["f4"]},
        {"id": "Q", "building": "kyotsu-minami", "assigned_by": "staff", "face": None},
        {"id": "R", "building": None, "face": None},
    ])
    doc = {"building": "kyotsu-minami", "photos": {"P": {"building": None, "face": None, "faces": []},
                                                   "Q": {"building": None, "face": "f2", "faces": ["f2"]},
                                                   "R": {"building": None, "face": "f1"}}}
    res = photos.set_faces(idx, doc, "仕分け係")
    got = _got(idx)
    assert got["P"]["face"] == "f4" and got["P"]["faces"] == ["f4"]
    assert got["Q"]["face"] == "f2" and got["R"]["face"] == "f1" and got["R"]["building"] is None
    assert [s["id"] for s in res["skipped"]] == ["P"]
    # 引数の棟が出力の building より先
    res2 = photos.set_faces(idx, {"building": "honbu", "photos": {"Q": {"face": "f9"}}}, "x", building="kyotsu-minami")
    assert _got(idx)["Q"]["face"] == "f9" and res2["set"] == 1


def test_cli_relink_on_copy(tmp_path):
    from recorder.__main__ import main

    _jpg(tmp_path / "m" / "タワー75_tower75" / "A.jpg")
    idx = _idx(tmp_path / "copy.json", [{"id": "A", "file": "E:/old/A.jpg"}])
    assert main(["photos", "relink", str(tmp_path / "m"), "--assign-from-folder", "--index", str(idx)]) == 0
    a = _got(idx)["A"]
    assert a["building"] == "tower75" and a["assigned_by"] == "staff" and a["file"].endswith("タワー75_tower75/A.jpg")
