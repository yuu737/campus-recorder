"""保護係の箱の寄せ（photos.hogo_refine_tasks・refine_masks）と、塗りの見た目（privacy.paint_masks のまとめと札）。11/8 F11。"""

import json

import pytest
from PIL import Image

from recorder import photos, privacy


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(privacy, "detect_faces", lambda im, model=None, score=None: [])
    root = tmp_path / "data" / "photos"
    (root / "20261104").mkdir(parents=True)
    Image.new("RGB", (1600, 1200), (180, 120, 60)).save(root / "20261104" / "P.jpg", "JPEG", quality=95)
    rows = [{"id": "P", "file": "20261104/P.jpg", "period": "during", "building": "honbu",
             "auto_mask": {"faces": "yunet_2023mar"},
             "masks": [{"kind": "face", "box": [0.1, 0.1, 0.12, 0.12], "by": "auto"},
                       {"kind": "poster", "box": [0.6, 0.2, 0.8, 0.4], "by": "hogo", "cells": "G3:H4", "note": "幕"},
                       {"kind": "plate", "box": [0.9, 0.5, 1.0, 0.6], "by": "review", "cells": "J6"}]},
            {"id": "Q", "file": "20261104/Q.jpg", "period": "during", "building": "library", "masks": []}]
    idx = root / "index.json"
    idx.write_text(json.dumps({"schema": "x", "photos": rows}, ensure_ascii=False), encoding="utf-8")
    return root, idx


def _got(idx):
    return {r["id"]: r for r in json.loads(idx.read_text(encoding="utf-8"))["photos"]}


def test_region_is_box_plus_quarter_each_side_and_clipped():
    assert photos.refine_region([0.6, 0.2, 0.8, 0.4]) == [0.55, 0.15, 0.85, 0.45]
    assert photos.refine_region([0.9, 0.0, 1.0, 0.1]) == [0.875, 0.0, 1.0, 0.125]


def test_refine_never_grows_beyond_the_original_box():
    box = [0.6, 0.2, 0.8, 0.4]
    region = photos.refine_region(box)
    assert photos.refine_cells_to_box(region, box, "A1:J10") == box            # 寄せの絵いっぱい → 元の箱に切り詰め
    nb = photos.refine_cells_to_box(region, box, "D4:E8")
    assert box[0] <= nb[0] < nb[2] <= box[2] and box[1] <= nb[1] < nb[3] <= box[3]
    assert nb == [0.64, 0.24, 0.7, 0.39]
    with pytest.raises(ValueError):
        photos.refine_cells_to_box(region, box, "A1")                          # 赤の線の外だけ → 書かない


def test_refine_tasks_write_grids_and_tasks(tmp_path, monkeypatch):
    root, idx = _setup(tmp_path, monkeypatch)
    before = idx.read_text(encoding="utf-8")
    res = photos.hogo_refine_tasks(idx, building="honbu", out_dir=tmp_path / "out")
    assert res["n"] == 2 and res["photos"] == 1 and res["missing"] == []
    doc = json.loads((tmp_path / "out" / "tasks_refine_honbu.json").read_text(encoding="utf-8"))
    assert [(r["photo"], r["n"], r["kind"]) for r in doc["boxes"]] == [("P", 1, "poster"), ("P", 2, "plate")]
    assert doc["boxes"][0]["region"] == [0.55, 0.15, 0.85, 0.45]
    g = Image.open(doc["boxes"][0]["grid"])
    assert max(g.size) == 1200                                                 # 小さな箱は拡大して見せる
    assert (tmp_path / "out" / "P_2_grid.jpg").exists()
    assert idx.read_text(encoding="utf-8") == before                           # 索引は書かない
    assert photos.hogo_refine_tasks(idx, building="library", out_dir=tmp_path / "o2")["n"] == 0


def test_refine_masks_replace_split_and_errors(tmp_path, monkeypatch):
    root, idx = _setup(tmp_path, monkeypatch)
    doc = {"pass": "refine", "made_by": "保護係（haiku）", "photos": {
        "P": [{"n": 1, "box": [0.6, 0.2, 0.8, 0.4], "cells": ["C2:D9", "G2:H9"], "note": "幕 2 本"},
              {"n": 2, "cells": "A1:J10"}],
        "Z": [{"n": 0, "cells": "A1"}]}}
    res = photos.refine_masks(idx, doc, now="T0")
    assert res["photos"] == 1 and res["boxes"] == 2 and res["new_boxes"] == 3 and res["not_found"] == ["Z"]
    assert res["area_after"] < res["area_before"]
    m = _got(idx)["P"]["masks"]
    assert [x["by"] for x in m] == ["auto", "hogo", "hogo", "review"]          # pass はそのまま（やり直しで置き換わる）
    for x in m[1:3]:
        assert x["box_before"] == [0.6, 0.2, 0.8, 0.4] and x["kind"] == "poster" and x["note"] == "幕 2 本"
        assert 0.6 <= x["box"][0] < x["box"][2] <= 0.8 and 0.2 <= x["box"][1] < x["box"][3] <= 0.4
    assert m[1]["box"][2] < m[2]["box"][0]                                     # 分けた 2 本の間の壁は塗らない
    assert m[3]["box"] == [0.9, 0.5, 1.0, 0.6] and m[3]["refined"]["cells"] == "A1:J10"
    # 見直しの drop は元の升（box_before）でも外せる
    photos.set_masks(idx, {"pass": "review", "photos": {"P": {"drop": ["G3:H4"]}}}, now="T1")
    assert [x["kind"] for x in _got(idx)["P"]["masks"]] == ["face"]


@pytest.mark.parametrize("item", [{"n": 0, "cells": "A1"},                        # auto の箱は寄せない
                                  {"n": 9, "cells": "A1"},                        # 番号の箱が無い
                                  {"n": 1, "box": [0.5, 0.2, 0.8, 0.4], "cells": "A1:J10"},   # 依頼の時と箱が違う
                                  {"n": 1, "cells": []},                          # 空 → 見直しの drop を使う
                                  {"n": 1, "cells": "K1"}])                       # 升の書き方が違う
def test_refine_masks_bad_item_is_skipped_but_others_written(tmp_path, monkeypatch, item):
    """だめな項目だけ飛ばし、同じ写真の他の箱は書く（11/8 追記 2）。"""
    root, idx = _setup(tmp_path, monkeypatch)
    before = _got(idx)["P"]["masks"]
    res = photos.refine_masks(idx, {"pass": "refine", "photos": {"P": [{"n": 2, "cells": "A1:J10"}, item]}})
    assert res["written"] == ["P"] and res["skipped"] == [] and len(res["errors"]) == 1
    assert res["errors"][0]["n"] == item["n"]
    after = _got(idx)["P"]["masks"]
    assert after[:2] == before[:2]                                              # だめな項目の箱は元のまま
    assert after[2]["refined"]["cells"] == "A1:J10"                             # 他の箱は書いた


def test_cli_refine(tmp_path, monkeypatch, capsys):
    from recorder.__main__ import main

    root, idx = _setup(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    assert main(["photos", "hogo-grid", "--refine", "--photos", "P", "--index", str(idx), "--out", "o"]) in (0, None)
    assert (tmp_path / "o" / "P_1_grid.jpg").exists()
    f = tmp_path / "r.json"
    f.write_text(json.dumps({"pass": "refine", "photos": {"P": [{"n": 1, "cells": "D4:E8"}]}}), encoding="utf-8")
    assert main(["photos", "masks", "--pass", "refine", "--from", str(f), "--index", str(idx)]) in (0, None)
    assert _got(idx)["P"]["masks"][1]["box"] == [0.64, 0.24, 0.7, 0.39]


# --- 塗りの見た目 ------------------------------------------------------------------------------------

BG = (200, 30, 30)


def _is_tag(p):
    return p == privacy.TAG_BG or p == privacy.TAG_FG


def test_paint_only_the_box_with_edge_and_tag():
    im = Image.new("RGB", (1000, 800), BG)
    out = privacy.paint_masks(im, [{"kind": "poster", "box": [0.3, 0.3, 0.5, 0.6]}])
    assert out.getpixel((400, 400)) == privacy.GRAY == (0x8A, 0x8A, 0x8A)
    assert out.getpixel((450, 479)) == privacy.EDGE and out.getpixel((499, 400)) == privacy.EDGE   # 2 px の縁
    assert out.getpixel((450, 477)) == privacy.GRAY
    assert out.getpixel((500, 400)) == BG and out.getpixel((299, 400)) == BG                        # 箱の外は変えない
    assert out.getpixel((301, 241)) == privacy.TAG_BG                                               # 左上の隅に札
    tag_rows = [y for y in range(230, 300) if any(out.getpixel((x, y)) == privacy.TAG_BG for x in range(300, 330))]
    assert 16 <= len(tag_rows) <= 40                                    # 文字の高さ 2 %（800 → 16 px）＋余白


def test_tag_height_is_constant_and_may_overflow_small_box():
    im = Image.new("RGB", (1000, 800), BG)
    out = privacy.paint_masks(im, [{"kind": "face", "box": [0.5, 0.5, 0.505, 0.505]}])   # 5×4 px の顔
    assert out.getpixel((500, 400)) == privacy.TAG_BG
    assert any(_is_tag(out.getpixel((x, 410))) for x in range(500, 530))  # 札は箱からはみ出してよい
    assert any(out.getpixel((x, y)) == privacy.TAG_FG for x in range(500, 540) for y in range(400, 430))


def test_touching_same_kind_boxes_merge_with_one_tag():
    im = Image.new("RGB", (1000, 800), BG)
    masks = [{"kind": "poster", "box": [0.1, 0.1, 0.2, 0.5]}, {"kind": "poster", "box": [0.2, 0.3, 0.3, 0.5]},
             {"kind": "poster", "box": [0.6, 0.1, 0.7, 0.5]}]
    assert len(privacy.group_masks(masks, 1000, 800)) == 2
    out = privacy.paint_masks(im, masks)
    assert out.getpixel((200, 350)) == privacy.GRAY                     # 境目に縁取りを引かない（1 つにまとめた）
    assert out.getpixel((250, 200)) == BG                               # 和で塗る（外接の長方形にしない）
    assert out.getpixel((210, 250)) == privacy.GRAY                   # 2 つ目の箱の札は無い
    assert out.getpixel((450, 300)) == BG                               # 離れた箱との間の壁は見える
    assert out.getpixel((601, 81)) == privacy.TAG_BG                    # 離れた箱は自分の札


def test_different_kinds_do_not_merge_and_tags_do_not_overlap():
    masks = [{"kind": "poster", "box": [0.1, 0.1, 0.2, 0.5]}, {"kind": "name", "box": [0.2, 0.1, 0.3, 0.5]},
             {"kind": "poster", "box": [0.205, 0.6, 0.3, 0.7]}]
    assert len(privacy.group_masks(masks, 1000, 800)) == 3
    from PIL import ImageDraw

    canvas = Image.new("RGB", (1000, 800), BG)
    placed = []
    a = privacy._tag(ImageDraw.Draw(canvas), 100, 80, "掲示物", 1000, 800, placed)
    b = privacy._tag(ImageDraw.Draw(canvas), 120, 80, "人名", 1000, 800, placed)
    assert b[1] >= a[3]                                                 # 重なる札は下へずらす
    for k, word in privacy.KINDS.items():
        assert word in ("顔", "ナンバー", "人名", "掲示物", "電話番号")


def test_no_masks_returns_copy():
    im = Image.new("RGB", (100, 80), BG)
    out = privacy.paint_masks(im, [])
    assert out is not im and out.getpixel((5, 5)) == BG


# --- 自動の顔の箱は見直しが確かめた物だけ（11/8 F11 追記、利用者「顔はあかんやろ」） ------------------------

def test_refine_auto_cuts_face_boxes_with_score(tmp_path, monkeypatch):
    root, idx = _setup(tmp_path, monkeypatch)
    idx.write_text(idx.read_text(encoding="utf-8").replace('"by": "auto"}', '"by": "auto", "score": 0.71}'), encoding="utf-8")
    assert photos.hogo_refine_tasks(idx, ids=["P"], out_dir=tmp_path / "a")["n"] == 2          # 既定は auto を含まない
    res = photos.hogo_refine_tasks(idx, ids=["P"], out_dir=tmp_path / "b", auto=True)
    rows = json.loads((tmp_path / "b" / "tasks_refine.json").read_text(encoding="utf-8"))["boxes"]
    face = [r for r in rows if r["by"] == "auto"]
    assert res["n"] == 3 and len(face) == 1 and face[0]["kind"] == "face" and face[0]["score"] == 0.71
    assert face[0]["region"] == photos.refine_region([0.1, 0.1, 0.12, 0.12]) and (tmp_path / "b" / "P_0_grid.jpg").exists()


def test_review_confirm_keeps_and_drop_removes_auto_faces(tmp_path, monkeypatch):
    root, idx = _setup(tmp_path, monkeypatch)
    rows = json.loads(idx.read_text(encoding="utf-8"))
    rows["photos"][0]["masks"].append({"kind": "face", "box": [0.3, 0.3, 0.32, 0.32], "by": "auto"})
    idx.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    bad = photos.set_masks(idx, {"pass": "review", "photos": {"P": {"auto": [{"n": 0}]}}})     # どちらも無い → 書かない
    assert len(bad["errors"]) == 1
    res = photos.set_masks(idx, {"pass": "review", "made_by": "見直し（sonnet）",
                                 "photos": {"P": {"auto": [{"n": 0, "confirm": True}, {"n": 3, "drop": True}]}}})
    assert res["confirmed"] == 1 and res["dropped_auto"] == 1 and not res["errors"]
    got = _got(idx)["P"]
    faces = [m for m in got["masks"] if m["kind"] == "face"]
    assert len(faces) == 1 and faces[0]["by"] == "review" and faces[0]["confirmed"]["from"] == "auto"
    assert got["dropped_auto"][0]["box"] == [0.3, 0.3, 0.32, 0.32]
    photos.set_masks(idx, {"pass": "review", "photos": {"P": {"masks": []}}})                 # やり直しでも確かめた顔は残る
    assert [m["kind"] for m in _got(idx)["P"]["masks"]].count("face") == 1
    # 寄せでも同じ：confirm＋cells で締める、drop で外す
    root2, idx2 = _setup(tmp_path / "r", monkeypatch)
    r = photos.refine_masks(idx2, {"pass": "refine", "photos": {"P": [{"n": 0, "confirm": True, "cells": "D4:G7"}]}})
    m0 = _got(idx2)["P"]["masks"][0]
    assert r["confirmed"] == 1 and m0["by"] == "review" and m0["box_before"] == [0.1, 0.1, 0.12, 0.12]
    assert 0.1 <= m0["box"][0] < m0["box"][2] <= 0.12
    r = photos.refine_masks(idx2, {"pass": "refine", "photos": {"P": [{"n": 0, "cells": "A1"}]}})   # 確かめ済みは寄せの対象
    assert len(r["errors"]) == 1 and "外" in r["errors"][0]["error"]


def test_publish_and_send_do_not_draw_unconfirmed_auto_faces():
    im = Image.new("RGB", (1000, 800), BG)
    masks = [{"kind": "face", "box": [0.1, 0.1, 0.3, 0.3], "by": "auto"},
             {"kind": "face", "box": [0.6, 0.6, 0.8, 0.8], "by": "review", "confirmed": {"from": "auto"}}]
    out = privacy.paint_masks(im, privacy.drawn_masks(masks))
    assert out.getpixel((200, 200)) == BG and out.getpixel((700, 560)) == privacy.GRAY
    assert privacy.unconfirmed_auto(masks) == 1


# --- 追記 2：寄せの drop・升の一部が外・見直しの番号指定・結果の一覧 ------------------------------------------

def test_refine_drop_non_auto_and_partial_outside_cells(tmp_path, monkeypatch):
    root, idx = _setup(tmp_path, monkeypatch)
    res = photos.refine_masks(idx, {"pass": "refine", "made_by": "保護係（haiku）", "photos": {
        "P": [{"n": 1, "cells": ["D4:E8", "A1"]},                                  # A1 は元の箱の外 → 捨てて D4:E8 で書く
              {"n": 2, "drop": True, "note": "車ではなく植え込み"}],
        "Q": [{"n": 0, "cells": "A1"}]}})                                          # Q に箱は無い → 飛ばす
    assert res["written"] == ["P"] and res["skipped"] == ["Q"] and res["dropped"] == 1
    assert len(res["warnings"]) == 1 and res["warnings"][0]["cells"] == "A1"
    got = _got(idx)["P"]
    assert [m["kind"] for m in got["masks"]] == ["face", "poster"] and got["masks"][1]["box"] == [0.64, 0.24, 0.7, 0.39]
    d = got["dropped"][0]
    assert d["dropped_by"] == "refine" and d["note"] == "車ではなく植え込み" and d["box"] == [0.9, 0.5, 1.0, 0.6]
    assert [e["id"] for e in res["errors"]] == ["Q"]


def test_review_drop_and_confirm_by_index(tmp_path, monkeypatch):
    root, idx = _setup(tmp_path, monkeypatch)
    photos.refine_masks(idx, {"pass": "refine", "photos": {"P": [{"n": 1, "cells": ["C2:D9", "G2:H9"]}]}})
    m = _got(idx)["P"]["masks"]
    assert [x["kind"] for x in m] == ["face", "poster", "poster", "plate"]
    res = photos.set_masks(idx, {"pass": "review", "photos": {"P": {"drop": [2, {"n": 3}], "confirm": [0],
                                                                    "masks": []}}})
    assert res["written"] == ["P"] and res["dropped"] == 2 and res["confirmed"] == 1 and not res["errors"]
    got = _got(idx)["P"]
    assert [(x["kind"], x["by"]) for x in got["masks"]] == [("face", "review"), ("poster", "hogo")]
    assert got["masks"][1]["box"] == m[1]["box"] and len(got["dropped"]) == 2
    bad = photos.set_masks(idx, {"pass": "review", "photos": {"P": {"drop": [9]}}})
    assert bad["skipped"] == ["P"] and bad["written"] == [] and "masks[9]" in bad["errors"][0]["error"]


def test_send_no_detect_redraws_only_confirmed_boxes(tmp_path, monkeypatch):
    """追記 3：--no-detect は顔を探さず、確かめた箱だけを描いて縮小版を作り直す（masks・auto_mask は変えない）。"""
    root, idx = _setup(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(privacy, "detect_faces", lambda *a, **k: calls.append(1) or [])
    (root / "send").mkdir()
    Image.new("RGB", (1600, 1200), (128, 128, 128)).save(root / "send" / "P.jpg")       # 古い縮小版（焼き込み）
    before = _got(idx)["P"]
    res = photos.make_sends(idx, ["P"], no_detect=True)
    assert res["made"] == 1 and not calls
    got = _got(idx)["P"]
    assert got["masks"] == before["masks"] and got["auto_mask"] == before["auto_mask"] and got["send_file"] == "send/P.jpg"
    im = Image.open(root / "send" / "P.jpg")
    assert max(im.size) == 1600
    px = lambda x, y: im.getpixel((int(x * im.size[0]), int(y * im.size[1])))           # noqa: E731
    assert abs(px(0.11, 0.115)[0] - 180) < 20                                           # 自動の顔の箱（未確認）は描かない
    assert abs(px(0.7, 0.35)[0] - 0x8A) < 10                                            # 保護係の箱は描く
