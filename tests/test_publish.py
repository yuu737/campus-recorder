"""提出用の公開版（recorder/publish.py、K-103）と保護係の升の書き戻し（photos.set_masks、K-104）。11/8 T4。"""

import hashlib
import json

from PIL import Image

from recorder import photos, privacy, publish


def _jpg(path, size=(3200, 2400)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (200, 30, 30)).save(path, "JPEG", quality=95)
    return path


def _got(path):
    return {r["id"]: r for r in json.loads(path.read_text(encoding="utf-8"))["photos"]}


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(privacy, "detect_faces", lambda im, model=None, score=None: [])
    monkeypatch.setattr(photos, "REPO_ROOT", tmp_path)      # メジャー/ がリポジトリの中にある形
    root = tmp_path / "data" / "photos"
    for n in ("A", "B", "C", "E"):
        _jpg(root / "20261105" / "honbu" / f"{n}.jpg")
    ext = _jpg(tmp_path / "メジャー" / "本部棟_honbu" / "T.jpg", (1200, 1600))
    rows = [
        {"id": "A", "file": "20261105/honbu/A.jpg", "period": "during", "auto_mask": {"faces": "yunet_2023mar"},
         "masks": [{"kind": "face", "box": [0.5, 0.5, 0.6, 0.6], "by": "auto"}]},
        {"id": "B", "file": "20261105/honbu/B.jpg", "period": "during"},
        {"id": "C", "file": "20261105/honbu/C.jpg", "period": "during"},
        {"id": "E", "file": "20261105/honbu/E.jpg", "period": "before"},
        {"id": "M", "file": "20261105/honbu/M.jpg", "period": "during"},
        {"id": "T", "file": ext.as_posix(), "period": "during"},
    ]
    idx = root / "index.json"
    idx.write_text(json.dumps({"schema": "x", "photos": rows}, ensure_ascii=False), encoding="utf-8")
    return root, idx


def test_set_masks_cells_errors_replace_and_review(tmp_path, monkeypatch):
    root, idx = _setup(tmp_path, monkeypatch)
    doc = {"pass": "hogo", "made_by": "保護係（haiku）", "photos": {
        "A": {"masks": [{"kind": "poster", "cells": "A1:B2", "note": "柱の貼り紙"}], "ok": True},
        "B": {"masks": [{"kind": "graffiti", "cells": "A1"}]},          # 知らない kind → 書かない
        "C": {"exclude": "研究室の名前の札が読める"},
        "T": {"masks": [], "crop": "A1:H10"},
        "Z": {"masks": []}}}
    res = photos.set_masks(idx, doc, now="T0")
    got = _got(idx)
    assert res["photos"] == 3 and res["masks"] == 1 and res["crops"] == 1 and res["not_found"] == ["Z"]
    assert res["excluded"] == ["C"] and [e["id"] for e in res["errors"]] == ["B"]
    assert [m["by"] for m in got["A"]["masks"]] == ["auto", "hogo"] and got["A"]["masks"][1]["box"] == [0.0, 0.0, 0.2, 0.2]
    assert "hogo" not in got["B"] and got["C"]["public"] is False and got["T"]["crop"]["box"] == [0.0, 0.0, 0.8, 1.0]
    assert got["A"]["hogo"]["by"] == "保護係（haiku）"
    photos.set_masks(idx, doc, now="T1")                                    # やり直しても増えない
    assert len(_got(idx)["A"]["masks"]) == 2
    rev = {"pass": "review", "photos": {"A": {"drop": ["A1:B2"], "masks": [{"kind": "plate", "cells": "J10"}]},
                                        "C": {"include": True, "ok": True}}}
    r2 = photos.set_masks(idx, rev)
    got = _got(idx)
    assert r2["dropped"] == 1 and [(m["by"], m["kind"]) for m in got["A"]["masks"]] == [("auto", "face"), ("review", "plate")]
    assert got["C"]["public"] is None


def test_publish_masks_excludes_and_counts(tmp_path, monkeypatch):
    root, idx = _setup(tmp_path, monkeypatch)
    photos.set_masks(idx, {"pass": "hogo", "photos": {
        "A": {"masks": [{"kind": "poster", "cells": "A1:B2"}]}, "B": {"masks": []},
        "C": {"exclude": "研究室の中が見える"}, "T": {"masks": [], "crop": "A1:H10"}}})
    src_hash = hashlib.sha256((root / "20261105/honbu/A.jpg").read_bytes()).hexdigest()
    res = publish.publish(idx, now="T", allow_unreviewed=True)
    got = _got(idx)
    assert res["index"] == 6 and res["public"] == 3 and res["before"] == 1 and res["missing"] == ["M"]
    assert [e["id"] for e in res["excluded"]] == ["C"] and res["masked"] == 1 and res["cropped"] == 1
    assert res["boxes"] == {"face": 1, "poster": 1} and res["not_reviewed"] == 3 and res["auto_now"] == 2
    a = root / got["A"]["public_file"]
    assert got["A"]["public_file"] == "public/20261105/honbu/A.jpg"
    im = Image.open(a)
    assert max(im.size) == 1600 and not im.getexif()
    assert im.getpixel((5, 5))[0] < 160                       # 掲示物の箱（左上）は灰色
    assert im.getpixel((int(0.55 * 1600), int(0.505 * 1200)))[0] < 160   # 顔の自動の箱
    assert im.getpixel((1500, 1100))[0] > 180                 # 箱の外は元の色
    assert got["T"]["public_file"] == "public/メジャー/本部棟_honbu/T.jpg"
    assert Image.open(root / got["T"]["public_file"]).size == (960, 1600)   # 右の 2 列を切り落とし
    assert got["C"].get("public_file") is None and not (root / "public/20261105/honbu/C.jpg").exists()
    assert hashlib.sha256((root / "20261105/honbu/A.jpg").read_bytes()).hexdigest() == src_hash
    # 後から外した写真は、前に出した公開版を消す
    photos.set_masks(idx, {"pass": "review", "photos": {"A": {"exclude": "研究内容のポスター"}}})
    res2 = publish.publish(idx, allow_unreviewed=True)
    assert res2["removed"] == 1 and not a.exists() and _got(idx)["A"]["public_file"] is None
    assert "除外 A：研究内容のポスター" in publish.summary_text(res2)


def test_publish_dry_run_writes_nothing(tmp_path, monkeypatch):
    root, idx = _setup(tmp_path, monkeypatch)
    before = idx.read_text(encoding="utf-8")
    res = publish.publish(idx, dry_run=True, allow_unreviewed=True)
    assert res["public"] == 4 and res["not_reviewed"] == 4 and idx.read_text(encoding="utf-8") == before
    assert not (root / "public").exists()


def test_cli_publish_and_masks_on_copy(tmp_path, monkeypatch, capsys):
    from recorder.__main__ import main

    root, idx = _setup(tmp_path, monkeypatch)
    hogo = tmp_path / "hogo.json"
    hogo.write_text(json.dumps({"pass": "hogo", "photos": {"B": {"masks": [{"kind": "phone", "cells": "C3"}]}}}), encoding="utf-8")
    assert main(["photos", "masks", "--from", str(hogo), "--index", str(idx)]) == 0
    assert main(["photos", "publish", "--index", str(idx), "--photos", "B", "--allow-unreviewed"]) == 0
    out = capsys.readouterr().out
    assert "公開 1" in out and (root / "public/20261105/honbu/B.jpg").exists()


def test_publish_default_holds_photos_not_through_both_hogo_and_review(tmp_path, monkeypatch):
    """既定は保護係（hogo）と見直し（review）の両方を通った写真だけ出す。--allow-unreviewed で残りも出す。"""
    root, idx = _setup(tmp_path, monkeypatch)
    photos.set_masks(idx, {"pass": "hogo", "photos": {"A": {"masks": []}, "B": {"masks": []}}})
    photos.set_masks(idx, {"pass": "review", "photos": {"A": {"masks": [], "ok": True}}})
    res = publish.publish(idx)
    got = _got(idx)
    assert res["public"] == 1 and got["A"]["public_file"] == "public/20261105/honbu/A.jpg"
    assert sorted(res["held"]) == ["B", "C", "T"] and res["not_reviewed"] == 3
    assert got["B"].get("public_file") is None and not (root / "public/20261105/honbu/B.jpg").exists()
    assert "出していない" in publish.summary_text(res)
    from recorder.__main__ import main
    assert main(["photos", "publish", "--index", str(idx), "--allow-unreviewed"]) == 0
    assert (root / "public/20261105/honbu/B.jpg").exists()
