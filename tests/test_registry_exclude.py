"""登録の外し方（J-013・J-027）：photos unregister／exclude、周期の面の原点の確かめ、隠れた所の記録。

台帳は一時フォルダの写しだけ。棟は合成の小さな物。"""

from __future__ import annotations

import json

import numpy as np
import pytest

from recorder import registry


@pytest.fixture(autouse=True)
def _tmp_lock_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDER_LOCK_DIR", str(tmp_path / "locks"))


def m(v):
    return {"value": v, "unit": "m", "status": "estimated", "evidence": ["P1"], "method": "試し"}


def ent(photo, face="S", building="x", **kw):
    return {"photo": photo, "building": building, "face": face, "H_plane": np.eye(3).tolist(), **kw}


def pose_ent(photo, C=(10.0, -15.0, 1.5), building="x", f=1500.0, w=2000, h=1500):
    R = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float)      # 北（+y）を向く
    t = -R @ np.array(C, float)
    return {"photo": photo, "building": building, "face": "(pose)", "kind": "pose", "image_size": [w, h],
            "camera": {"R_cv": R.tolist(), "t_cv": t.tolist(), "focal_px": f, "image_size": [w, h]}}


def _reg(tmp_path, entries):
    p = tmp_path / "registry.json"
    p.write_text(json.dumps({"schema": registry.SCHEMA, "entries": entries, "not_registered": []}), encoding="utf-8")
    return p


# --- unregister ----------------------------------------------------------------------------

def test_unregister_keeps_history_and_matches_tail(tmp_path):
    p = _reg(tmp_path, [ent("PXL_20261103_042822099"), ent("PXL_20261103_042822099", face="E"), ent("PXL_20261103_042830256")])
    reg = registry.load_registry(p)
    gone = registry.unregister(reg, "042822099", "柱割り 4 つ分ずれた（J-027）", face="S", by="kenshu")
    assert [(e["photo"][-9:], e["face"]) for e in gone] == [("042822099", "S")]
    assert len(reg["entries"]) == 2
    h = reg["unregistered"][0]
    assert h["why"].startswith("柱割り") and h["by"] == "kenshu" and h["entry"]["face"] == "S"
    with pytest.raises(ValueError, match="2 枚に当たる"):
        registry.unregister(reg, "PXL_20261103", "曖昧")
    with pytest.raises(ValueError, match="理由"):
        registry.unregister(reg, "042830256", "")


def test_cli_unregister_and_exclude(tmp_path, capsys):
    from recorder import __main__ as M

    p = _reg(tmp_path, [ent("A1"), ent("B2")])
    assert M.main(["photos", "unregister", "A1", "--why", "地面を直す前の登録", "--registry", str(p)]) == 0
    assert M.main(["photos", "exclude", "B2", "--why", "別の棟が写る", "--registry", str(p)]) == 0
    assert M.main(["photos", "unregister", "ZZ", "--why", "無い", "--registry", str(p)]) == 1
    back = json.loads(p.read_text(encoding="utf-8"))
    assert back["entries"] == []
    assert [x["photo"] for x in back["excluded"]] == ["B2"]
    assert [x["why"] for x in back["unregistered"]] == ["地面を直す前の登録", "exclude: 別の棟が写る"]
    assert "外した: A1" in capsys.readouterr().out


# --- exclude -------------------------------------------------------------------------------

def test_exclude_photo_blocks_reregistration_until_lift(tmp_path):
    reg = registry.load_registry(_reg(tmp_path, [ent("A")]))
    r = registry.exclude(reg, "A", "柱割りがずれる")
    assert r == {"mode": "photo", "photo": "A", "buildings": ["x"], "unregistered": 1}
    assert reg["entries"] == []
    assert registry.upsert(reg, ent("A")) is False                 # 鎖・SfM が試し直しても入らない
    n = next(x for x in reg["not_registered"] if x["photo"] == "A")
    assert n["excluded_code"] == "excluded" and "柱割り" in n["excluded_reason"]
    assert registry.lift_exclude(reg, "A") == 1
    assert registry.upsert(reg, ent("A")) is True
    # 登録の前に外す時は棟が要る
    with pytest.raises(ValueError, match="--building"):
        registry.exclude(reg, "Q", "まだ無い")
    assert registry.exclude(reg, "Q", "まだ無い", building="x")["buildings"] == ["x"]


def test_exclude_rect_plane_and_pose(tmp_path):
    b = {"id": "x", "faces": [{"id": "S", "from": [0.0, 0.0], "to": [20.0, 0.0], "top_z": m(12.0)}]}
    reg = registry.load_registry(_reg(tmp_path, [ent("P"), pose_ent("Q")]))
    registry.exclude(reg, "P", "木", face="S", rect=[2, 5, 0, 4])
    registry.exclude(reg, "Q", "車", face="S", rect=[8, 11, 0, 1.5])
    pe = next(e for e in reg["entries"] if e["photo"] == "P")
    assert pe["exclude"][0]["rect"] == [2.0, 5.0, 0.0, 4.0] and pe["exclude"][0]["why"] == "木"
    ex = registry.expand_entries(registry.entries_for(reg, "x"), b)
    q = next(e for e in ex if e["photo"] == "Q")
    assert q["kind"] == "pose_face" and q["exclude"][0]["why"] == "車"
    with pytest.raises(ValueError, match="--face"):
        registry.exclude(reg, "P", "木", rect=[0, 1, 0, 1])
    with pytest.raises(ValueError, match="面 E の登録が無い"):
        registry.exclude(reg, "P", "木", face="E", rect=[0, 1, 0, 1])


# --- 周期の面の原点（J-027）------------------------------------------------------------------

def periodic_building(parts=()):
    return {"id": "x", "faces": [{"id": "S", "from": [0.0, 0.0], "to": [40.0, 0.0], "top_z": m(12.0),
                                  "bays": {"count": {"value": 8, "unit": "個", "status": "measured", "evidence": ["P1"]}}}],
            "parts": list(parts)}


def cover(s0, s1):
    return {"type": "polygon", "pts": [[s0, 0], [s1, 0], [s1, 10], [s0, 10]]}


def test_anchor_check_plane_cover():
    b = periodic_building()
    assert registry.periodic_faces(b) == {"S": {"bays": 8, "length_m": 40.0, "pitch_m": 5.0}}
    mid = registry.anchor_check(ent("M", cover=cover(12, 26)), b)
    assert not mid["ok"] and mid["code"] == "origin_ambiguous" and "J-027" in mid["reason"]
    assert registry.anchor_check(ent("E", cover=cover(0.5, 9)), b)["ok"]              # 西の端が写る
    assert registry.anchor_check(ent("W", cover=cover(30, 39.5)), b)["ok"]            # 東の端
    porch = {"type": "porch", "name": "正面玄関", "face": "S", "along": m(15.0), "width": m(4.0)}
    got = registry.anchor_check(ent("M", cover=cover(12, 26)), periodic_building([porch]))
    assert got["ok"] and "正面玄関" in got["reason"]
    two = [porch, {**porch, "name": "裏口"}]                                          # 2 つあれば唯一ではない
    assert not registry.anchor_check(ent("M", cover=cover(12, 26)), periodic_building(two))["ok"]


def test_upsert_refuses_ambiguous_origin_and_keeps_old(tmp_path, monkeypatch):
    b = periodic_building()
    reg = registry.load_registry(_reg(tmp_path, [ent("M", cover=cover(0, 9), note="前の登録")]))
    assert registry.upsert(reg, ent("M", cover=cover(12, 26)), b=b) is False
    assert reg["entries"][0]["note"] == "前の登録"                                   # 前の登録は残る
    n = reg["not_registered"][0]
    assert n["anchor_code"] == "origin_ambiguous"
    assert registry.upsert(reg, ent("N", cover=cover(0.2, 8)), b=b) is True
    assert reg["entries"][-1]["check"]["anchor"]["code"] == "anchored"
    monkeypatch.setenv(registry.ANCHOR_ENV, "0")                                    # 試しの時だけ切れる
    assert registry.upsert(reg, ent("M", cover=cover(12, 26)), b=b) is True


def test_anchor_check_pose_sees_end_or_not():
    b = periodic_building()
    near_mid = pose_ent("C", C=(20.0, -6.0, 1.5), f=800.0)       # 正面の真ん中に近い：端は枠の外
    far = pose_ent("F", C=(20.0, -40.0, 1.5))                     # 引いて全体：端が入る
    assert not registry.anchor_check(near_mid, b)["ok"]
    assert registry.anchor_check(far, b)["ok"]


# --- 隠れ（J-013）---------------------------------------------------------------------------

def test_expand_entries_records_hidden_part_and_uses_current_faces():
    b = {"id": "x", "faces": [
        {"id": "S", "from": [0.0, 0.0], "to": [20.0, 0.0], "top_z": m(12.0)},
        {"id": "B", "from": [8.0, -5.0], "to": [12.0, -5.0], "top_z": m(4.0)},     # 手前の低い箱の正面
        {"id": "N", "from": [20.0, 10.0], "to": [0.0, 10.0], "top_z": m(12.0)},    # 北の面（裏）
    ]}
    e = pose_ent("Q", C=(10.0, -15.0, 1.5))
    e["visible_faces"] = [{"face": "S"}, {"face": "N"}]           # 登録の時の古い一覧（N は見えない）
    ex = {x["face"]: x for x in registry.expand_entries([e], b)}
    assert set(ex) == {"S", "B"}                                   # 今の面で見え直す：N に貼らない
    hid = ex["S"]["hidden"]
    assert hid and ex["S"]["unoccluded"] < 1.0
    s0 = min(r["rect"][0] for r in hid)
    s1 = max(r["rect"][1] for r in hid)
    z1 = max(r["rect"][3] for r in hid)
    assert 6.0 < s0 < 8.0 and 12.0 < s1 < 14.0                     # 箱の陰（7.3〜12.7 m 付近）
    assert 4.5 < z1 < 6.5                                          # 5.25 m まで隠れる
    assert ex["B"]["hidden"] == [] and ex["S"]["exclude"] == []


def test_exclude_after_unregister_uses_full_id(tmp_path):
    reg = registry.load_registry(_reg(tmp_path, [ent("PXL_20261103_042822099", building="honbu_t")]))
    registry.unregister(reg, "042822099", "ずれ", building="honbu_t")
    r = registry.exclude(reg, "042822099", "柱割りがずれる", building="honbu_t")
    assert r["photo"] == "PXL_20261103_042822099"                  # 外した記録から全部の id に
    assert registry.upsert(reg, ent("PXL_20261103_042822099", building="honbu_t")) is False


def test_anchor_check_uses_chain_inlier_positions():
    """鎖の登録は、視点に合った点の面の上の位置（check.inlier_s_by_face）で見る。写る範囲に端があっても、
    対応が真ん中の柱割りだけなら止める（J-027 の型）。"""
    b = periodic_building()
    far = pose_ent("F", C=(20.0, -40.0, 1.5))                       # 写る範囲には両端が入る
    far["check"] = {"inlier_s_by_face": {"S": [14.2, 16.0, 21.5, 24.9]}}
    got = registry.anchor_check(far, b)
    assert not got["ok"] and "鎖の内点" in got["reason"]
    far["check"] = {"inlier_s_by_face": {"S": [0.3, 14.2, 16.0]}}
    assert registry.anchor_check(far, b)["ok"]


def test_excluded_ids_and_coverage_reads_hidden():
    from recorder import coverage

    reg = {"entries": [], "excluded": [{"photo": "A", "building": "x"}, {"photo": "B", "building": "y"}]}
    assert registry.excluded_ids(reg, "x") == {"A"}
    f = {"id": "S", "from": [0.0, 0.0], "to": [20.0, 0.0]}
    e = {"face_frame": {"from": [0.0, 0.0], "to": [20.0, 0.0]}, "exclude": [{"rect": [1, 2, 0, 1], "why": "木"}],
         "hidden": [{"rect": [7, 13, 0, 5], "why": "隠れ"}]}
    got = coverage.exclude_now(e, f)
    assert [w for *_, w in got] == ["木", "隠れ"] and got[1][:2] == pytest.approx((7.0, 13.0))
