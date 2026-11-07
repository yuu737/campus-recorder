"""写真 id の普遍化（11/8 F3）：どの機種のスマホの名前でも id が重ならない・短い名前が重ならない・
期間の決まり・偏角・鍵の外で重い仕事をして差分だけ書く・photos ruler-mark。写真本体は使わず小さな画像を作る。"""

import argparse
import json
import shutil
from pathlib import Path

from PIL import Image

from recorder import photos


def _img(path: Path, color, size=(64, 48)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, quality=95)
    return path


def _imp(folder, idx_path, root, **kw):
    return photos.import_photos(folder, None, index_path=idx_path, root=root, period="during", send=False, **kw)


# --- 取り込みの id ----------------------------------------------------------------------------

def test_iphone_same_name_different_photo_gets_hash_suffix(tmp_path):
    root = tmp_path / "photos"
    a = _img(root / "20261107" / "職員A" / "IMG_0001.jpg", (200, 30, 30))
    b = _img(root / "20261107" / "職員B" / "IMG_0001.jpg", (30, 30, 200))
    idx_path = root / "index.json"
    idx, added = _imp(root / "20261107", idx_path, root)
    assert len(added) == 2
    ids = {r["id"]: r for r in idx["photos"]}
    sha_b = photos.file_sha256(b)
    assert set(ids) == {"IMG_0001", f"IMG_0001_{sha_b[:8]}"}
    assert ids["IMG_0001"]["file"] == "20261107/職員A/IMG_0001.jpg"
    assert ids[f"IMG_0001_{sha_b[:8]}"]["file"] == "20261107/職員B/IMG_0001.jpg"   # file が別の物を指さない
    assert ids[f"IMG_0001_{sha_b[:8]}"]["id_collision"]["stem"] == "IMG_0001"
    assert ids["IMG_0001"]["sha256"] == photos.file_sha256(a)
    # 取り込み直しても増えない・id は同じ
    idx2, added2 = _imp(root / "20261107", idx_path, root)
    assert added2 == [] and {r["id"] for r in idx2["photos"]} == set(ids)


def test_same_photo_from_another_folder_is_the_same_record(tmp_path):
    root = tmp_path / "photos"
    a = _img(root / "d1" / "IMG_0042.jpg", (10, 120, 10))
    (root / "d2").mkdir(parents=True)
    shutil.copy(a, root / "d2" / "IMG_0042.jpg")
    idx_path = root / "index.json"
    _imp(root / "d1", idx_path, root)
    idx, added = _imp(root / "d2", idx_path, root)
    assert added == [] and [r["id"] for r in idx["photos"]] == ["IMG_0042"]


def test_old_record_without_hash_is_checked_by_content(tmp_path):
    """11/8 より前の記録（sha256 なし）：元の写真の中身で比べる。人が入れた値は残す。"""
    root = tmp_path / "photos"
    old = _img(root / "old" / "20261107_150551.jpg", (90, 90, 90))   # Galaxy の名前
    idx_path = root / "index.json"
    photos.save_index({"schema": photos.INDEX_SCHEMA, "photos": [
        {"id": "20261107_150551", "file": "old/20261107_150551.jpg", "building": "honbu", "face": "f1", "notes": "人"}]},
        idx_path)
    (root / "same").mkdir()
    shutil.copy(old, root / "same" / "20261107_150551.jpg")
    _img(root / "other" / "20261107_150551.jpg", (250, 250, 0))
    idx, added = _imp(root / "same", idx_path, root)
    assert added == []
    rec = idx["photos"][0]
    assert (rec["building"], rec["face"], rec["notes"]) == ("honbu", "f1", "人")
    idx, added = _imp(root / "other", idx_path, root)
    assert len(added) == 1 and added[0]["id"].startswith("20261107_150551_")
    assert idx["photos"][0]["file"] == "same/20261107_150551.jpg"   # 同じ写真を別の場所から入れ直した所


def test_real_index_copy_keeps_existing_ids(tmp_path):
    """本物の索引の写しに iPhone 風の名前の写真を入れても、既にある id（PXL_…・日本語名）と中身は変わらない。"""
    real = Path("data/photos/index.json")
    if not real.exists():
        return
    idx_path = tmp_path / "index.json"
    shutil.copy(real, idx_path)
    before = json.loads(idx_path.read_text(encoding="utf-8"))["photos"]
    root = tmp_path / "photos"
    _img(root / "20261108" / "IMG_0001.jpg", (1, 2, 3))
    _img(root / "20261108" / "sub" / "IMG_0001.jpg", (3, 2, 1))
    idx, added = _imp(root / "20261108", idx_path, root)
    assert len(added) == 2 and len({r["id"] for r in added}) == 2
    after = {r["id"]: r for r in idx["photos"]}
    assert all(after[r["id"]] == r for r in before)


# --- 短い名前 ---------------------------------------------------------------------------------

def test_short_names_are_device_independent_and_unique():
    ids = ["PXL_20261103_043259125", "PXL_20261104_043259125", "IMG_0001", "IMG_0002", "IMG_0001_ab12cd34",
           "20261107_150551", "20261107_150552", "本部棟正面柱92cm", "本部棟正面柱92cm.copy"]
    sn = photos.short_names(ids)
    assert len(set(sn.values())) == len(ids)
    assert photos.short_name("PXL_20261103_043259125") == "043259125"            # 前の形（既にある出力の名前）
    assert photos.short_name("PXL_20261103_043259125.MP") == "043259125"
    assert photos.short_name("IMG_0001") == "IMG_0001" and photos.short_name("IMG_0002") != "IMG"
    assert photos.short_name("本部棟正面柱92cm") == "本部棟正面柱92cm"            # 日本語名はそのまま
    assert sn["PXL_20261103_043259125"] != sn["PXL_20261104_043259125"]          # 別の日の同じ時刻
    assert photos.short_names(list(reversed(ids))) == sn                          # 並びの順に依らない
    long = "あ" * 40
    assert len(photos.short_name(long).encode("utf-8")) <= 40
    a = photos.short_name("本部棟", ascii_only=True)
    assert a.startswith("photo_") and a != photos.short_name("図書館", ascii_only=True)


def test_photoproj_short_id_not_collapsed():
    from recorder import photoproj
    assert photoproj.short_id("IMG_0001") != photoproj.short_id("IMG_0002")
    assert photoproj.short_id("20261107_150551") != photoproj.short_id("20261108_093000")


def test_evidence_entity_name_japanese_not_all_photo():
    from recorder import evidence
    assert evidence.entity_name("本部棟正面") != evidence.entity_name("図書館南")
    assert evidence.entity_name("P223_本部棟の長辺") == "P223"


def test_registry_lines_lookup_does_not_take_other_device_tail(tmp_path):
    from recorder import registry
    d = tmp_path / "compare"
    d.mkdir()
    cj = d / "x_compare.json"
    cj.write_text("{}", encoding="utf-8")
    (d / "lines_f1_0001.json").write_text("{}", encoding="utf-8")   # DSC_0001 の線（前の形なら IMG_0001 も拾っていた）
    assert registry._lines_of(tmp_path, cj, "IMG_0001", "f1") is None
    (d / "lines_f1_043259125.json").write_text("{}", encoding="utf-8")
    assert registry._lines_of(tmp_path, cj, "PXL_20261103_043259125", "f1").name == "lines_f1_043259125.json"


# --- 偏角・期間 -------------------------------------------------------------------------------

def test_declination_from_location():
    nagoya = photos.declination_gsi(35.1355, 136.9755)
    assert -8.3 < nagoya < -7.2
    assert photos.declination_gsi(43.06, 141.35) < nagoya < photos.declination_gsi(26.2, 127.7)   # 北海道 < 名古屋 < 沖縄
    info = {"heading_deg": 100.0, "heading_ref": "magnetic", "gps": {"lat": 26.2, "lon": 127.7}}
    assert abs(photos.true_heading(info) - (100.0 + photos.declination_gsi(26.2, 127.7))) < 1e-9
    assert photos.true_heading({"heading_deg": 100.0, "heading_ref": "magnetic"}, {"declination_deg": -6.0}) == 94.0
    site = {"crs": {"origin_lat": 43.06, "origin_lon": 141.35}}
    assert photos.declination_for({}, site) == photos.declination_gsi(43.06, 141.35)


def test_period_rule_comes_from_site(tmp_path):
    root = tmp_path / "photos"
    _img(root / "x" / "IMG_0100_20260907.jpg", (5, 5, 5))   # 日付はファイル名から
    idx, _ = photos.import_photos(root / "x", None, index_path=root / "i1.json", root=root, send=False)
    assert idx["photos"][0]["period"] == "during"            # 決まり無し：消えない
    site = {"photo_policy": {"period_start": "2026-11-02T12:00:00"}}
    idx, _ = photos.import_photos(root / "x", site, index_path=root / "i2.json", root=root, send=False)
    assert idx["photos"][0]["period"] == "before"


# --- 鍵の外で重い仕事、差分だけ書く ---------------------------------------------------------------

def test_patch_index_keeps_masks_written_meanwhile(tmp_path):
    idx_path = tmp_path / "index.json"
    photos.save_index({"schema": photos.INDEX_SCHEMA, "photos": [
        {"id": "IMG_0001", "masks": [], "notes": ""}]}, idx_path)
    snap = photos.load_index(idx_path)
    base = json.loads(json.dumps(snap["photos"][0]))
    rec = snap["photos"][0]
    rec["masks"] = [{"by": "auto", "kind": "face", "box": [0, 0, 1, 1]}]
    rec["send_file"] = "send/IMG_0001.jpg"
    # 鍵の外にいる間に保護係が升を書いた
    cur = photos.load_index(idx_path)
    cur["photos"][0]["masks"] = [{"by": "hogo", "kind": "poster", "cells": [1]}]
    cur["photos"][0]["notes"] = "保護係"
    photos.save_index(cur, idx_path)
    out = photos.patch_index(idx_path, {"IMG_0001": photos.record_diff(base, rec)})
    r = out["photos"][0]
    assert r["send_file"] == "send/IMG_0001.jpg" and r["notes"] == "保護係"
    assert {m["by"] for m in r["masks"]} == {"auto", "hogo"}


def test_import_does_not_hold_lock_for_heavy_work(tmp_path, monkeypatch):
    """取り込みの縮小（顔の検出）は鍵の外で走る。"""
    from recorder import ledger
    root = tmp_path / "photos"
    _img(root / "d" / "IMG_0001.jpg", (40, 40, 40))
    seen = []

    def fake_send(rec, src, send_root, photo_root, force=False):
        seen.append(list(ledger.held()))
        return None

    monkeypatch.setenv("RECORDER_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setattr(photos, "make_send_for", fake_send)
    photos.import_photos(root / "d", None, index_path=root / "index.json", root=root, period="during")
    assert seen == [[]]


# --- photos ruler-mark（古い名前 tape） ---------------------------------------------------------

def test_ruler_mark_and_old_name(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RECORDER_LOCK_DIR", str(tmp_path / "locks"))
    idx_path = tmp_path / "index.json"
    photos.save_index({"schema": photos.INDEX_SCHEMA, "photos": [{"id": "IMG_0001", "building": None, "face": None}]}, idx_path)
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="photos_cmd")
    photos.add_t4_args(sub)
    for cmd in ("ruler-mark", "tape"):
        a = p.parse_args([cmd, "IMG_0001", "--building", "b1", "--index", str(idx_path)])
        monkeypatch.setattr(photos, "_default_aliases", lambda campus: {"一号館": "b1"})
        assert photos.run_t4(a) == 0
    err = capsys.readouterr().err
    assert "photos tape は古い名前" in err and err.count("古い名前") == 1
    r = photos.load_index(idx_path)["photos"][0]
    assert r["building"] == "b1" and r["ruler"] == "tape_measure"
