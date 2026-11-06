"""目印の登録の道具の穴 4 件（11/7）：地面の線の目印、升の半分の位置、--check-jpg、カメラの高さの基準の地面。
合成データだけ。写真の索引・台帳は触らない（write=False、索引は引数で渡す）。"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from recorder import landmarks as LM
from recorder import locate
from recorder import registry as R_
from recorder.schema import load_building

FIX = Path("tests/fixtures/parts_building.json")
W, H, FOCAL = 4000, 3000, 2600.0


# --- 1. 地面の線 ---------------------------------------------------------------------------

def test_ground_lines_in_catalog():
    b = load_building(FIX)
    allm = LM.catalog(b, lines=True)
    g = [m for m in allm if m.get("ground")]
    ids = {m["id"] for m in g}
    assert {"S-G01", "E-G01", "N-G01", "W-G01"} <= ids                       # 面の足元は全部の面
    s = {m["id"]: m for m in g if m["face"] == "S"}
    assert s["S-G01"]["z"] == 0.0 and s["S-G01"]["kind"] == "line" and s["S-G01"]["dir"] == "h"
    # 段（parts の steps）の上端：z0（無ければ足元）＋count×rise、along〜along+width
    assert s["S-G02"]["z"] == pytest.approx(0.30) and (s["S-G02"]["s0"], s["S-G02"]["s1"]) == (7.5, 12.5)
    assert s["S-G03"]["z"] == pytest.approx(0.80)
    assert len(ids) == len(g)
    # 点の目印の一覧は変わらない
    assert [m for m in allm if m.get("kind") != "line"] == LM.catalog(b)


def test_ground_line_skipped_when_offset_unknown_and_shifted_when_known():
    b = load_building(FIX)
    b["faces"][1]["ground_offset"] = {"value": None, "unit": "m", "tol": None, "status": "unknown", "evidence": [], "method": ""}
    b["faces"][2]["ground_offset"] = {"value": -3.0, "unit": "m", "tol": 1.0, "status": "estimated", "evidence": ["x"], "method": "t"}
    ids = {m["id"]: m for m in LM.catalog(b, lines=True) if m.get("ground")}
    fid1, fid2 = b["faces"][1]["id"], b["faces"][2]["id"]
    assert f"{fid1}-G01" not in ids                                             # unknown は決めつけない
    assert ids[f"{fid2}-G01"]["z"] == -3.0 and abs(ids[f"{fid2}-G01"]["xyz1"][2] + 3.0) < 1e-9


def test_draw_face_with_ground_lines(tmp_path):
    b = load_building(FIX)
    cat = LM.catalog(b)
    lines = [m for m in LM.catalog(b, lines=True) if m.get("kind") == "line" and m["face"] == "S"]
    p = LM.draw_face(b, "S", [m for m in cat if m["face"] == "S"], tmp_path / "S.png", line_marks=lines)
    assert p and p.exists() and p.stat().st_size > 1000


# --- 3. 升の半分の位置 ---------------------------------------------------------------------

def test_fractional_cells():
    assert LM.parse_cell("C7") == (2, 6) and isinstance(LM.parse_cell("C7")[0], int)
    assert LM.parse_cell("F3.5") == (5, 2.5)
    assert LM.parse_cell("F+3") == (5.5, 2) and LM.parse_cell("F.5 3") == (5.5, 2)
    with pytest.raises(ValueError):
        LM.parse_cell("F11.5")
    w, h = 1000, 1000
    # F3.5 の中心は F3 と F4 の境（v = 300）、F+3 の中心は F と G の境（u = 600）
    u, v, _ = LM.pick_to_px({"cell": "F3.5"}, w, h)
    assert (u, v) == (550.0, 300.0)
    u, v, _ = LM.pick_to_px({"cell": "F+3"}, w, h)
    assert (u, v) == (600.0, 250.0)
    # 寄りの升の半分：E5 と E6 の中点 = E5.5
    a = LM.pick_to_px({"cell": "C7", "sub": "E5"}, 4000, 3000)
    b2 = LM.pick_to_px({"cell": "C7", "sub": "E6"}, 4000, 3000)
    c = LM.pick_to_px({"cell": "C7", "sub": "E5.5"}, 4000, 3000)
    assert c[0] == a[0] and abs(c[1] - (a[1] + b2[1]) / 2) < 1e-9
    # 線の対応の cells / subs にも書ける
    pts = LM._line_pick_px({"id": "x", "cells": ["C3.5", "C8"], "subs": ["A1", "B2.5"]}, 4000, 3000)
    assert len(pts) == 2


# --- 4. カメラの高さの基準の地面 ＋ 2. 重ね画像 ------------------------------------------------

def _synthetic_picks(b, cam_z: float, dist: float = 14.0, w: int = W, h: int = H, focal: float = FOCAL):
    f = next(f for f in b["faces"] if f["id"] == "S")
    p0 = np.array(f["from"], float); d = np.array(f["to"], float) - p0; L = np.linalg.norm(d); u = d / L; n = np.array([u[1], -u[0]])
    C = np.r_[p0 + u * L / 2 + n * dist, cam_z]
    yaw = math.degrees(math.atan2(-n[0], -n[1])) % 360
    R = locate.rotation_cv(yaw, 20.0, 0.5)
    t = -R @ C
    cat = [m for m in LM.catalog(b) if m["face"] == "S"]
    q, dd = LM._project(R, t, focal, w, h, np.array([m["xyz"] for m in cat]))
    picks = [{"id": m["id"], "u": float(a[0]), "v": float(a[1])} for m, a, z in zip(cat, q, dd)
             if z > 1 and 0.03 * w < a[0] < 0.97 * w and 0.03 * h < a[1] < 0.97 * h]
    assert len(picks) >= 6
    return {"photo": "SYN1", "face": "S", "picks": picks}, C


def _idx(w=W, h=H, focal=FOCAL, file="nonexistent/SYN1.jpg"):
    return {"photos": [{"id": "SYN1", "width": w, "height": h, "focal_35mm": focal * 36.0 / max(w, h), "file": file}]}


def test_ground_reference_switch():
    b = load_building(FIX)
    doc, C = _synthetic_picks(b, cam_z=0.5)
    base = LM.register_picks(FIX, doc, index=_idx(), write=False, b=b)
    assert base["check"]["ground_ref"]["mode"] == "auto"
    # 段の上端（S-G03、0.8 m）を足元とみなすとカメラは地面の下 → 高さの門で落ちる
    r = LM.register_picks(FIX, {**doc, "ground": "S-G03"}, index=_idx(), write=False, b=b)
    assert r["check"]["ground_ref"]["z"] == pytest.approx(0.8)
    assert r["check"]["ground_ref"]["camera_height_m"] == pytest.approx(0.5 - 0.8, abs=0.1)
    assert r["status"] == "fail" and r["code"] == "camera_height" and "S-G03" in r["reason"]
    # 面の前の地面（S の ground_offset ＝ 0）なら 0.5 m で通る
    r2 = LM.register_picks(FIX, {**doc, "ground": "face:S"}, index=_idx(), write=False, b=b)
    assert r2["check"]["ground_ref"]["mode"] == "face" and r2["check"]["ground_ref"]["camera_height_m"] == pytest.approx(0.5, abs=0.1)
    assert r2["code"] != "camera_height"
    # 分からない指定は従来に落として problems に書く
    r3 = LM.register_picks(FIX, {**doc, "ground": "moon"}, index=_idx(), write=False, b=b)
    assert r3["check"]["ground_ref"]["mode"] == "auto" and any("moon" in p for p in r3["problems"])


def test_ground_reference_plaza_uses_face_offset():
    b = load_building(FIX)
    b["faces"][0]["ground_offset"] = {"value": -3.0, "unit": "m", "tol": 1.0, "status": "estimated", "evidence": ["x"], "method": "t"}
    g = LM.ground_reference(b, "plaza", 0.0, -3.0, "S")
    assert g["mode"] == "face" and g["z"] == -3.0
    assert LM.ground_reference(b, "dem", 1.2, -3.0, "S")["z"] == 1.2
    assert LM.ground_reference(b, "plaza", 0.0, None, None)["mode"] == "auto"
    assert LM.ground_reference(b, "S-G01", 0.0, None, None)["z"] == -3.0


def test_check_jpg_written_even_on_fail(tmp_path, monkeypatch):
    import cv2

    b = load_building(FIX)
    w, h, focal = W // 4, H // 4, FOCAL / 4
    ph = tmp_path / "SYN1.jpg"
    cv2.imencode(".jpg", np.full((h, w, 3), 128, np.uint8))[1].tofile(str(ph))
    idx = _idx(w, h, focal, str(ph))
    monkeypatch.setattr(R_, "photo_record", lambda pid, index=None: idx["photos"][0])
    doc, _ = _synthetic_picks(b, cam_z=0.5, w=w, h=h, focal=focal)
    res = LM.register_picks(FIX, {**doc, "ground": "S-G03"}, index=idx, write=False, b=b)
    assert res["status"] == "fail" and res.get("camera")                  # 不合格でも解はある
    out = LM.draw_check(b, res, tmp_path / "check_SYN1.jpg")
    assert out and out.exists() and out.stat().st_size > 1000


def test_cli_pnp_check_jpg(tmp_path, monkeypatch):
    """`photos pnp --check-jpg PATH --no-register` が重ね画像を出す（不合格でも）。"""
    from recorder import __main__ as M

    b = load_building(FIX)
    fake = {"photo": "SYN1", "building": b["id"], "status": "fail", "code": "camera_height", "camera": {"R_cv": []}}
    called = {}

    def fake_reg(*a, **k):
        called["write"] = k.get("write")
        return fake

    monkeypatch.setattr(LM, "register_picks", fake_reg)
    monkeypatch.setattr(LM, "draw_check", lambda bb, res, p: (Path(p).write_bytes(b"x"), Path(p))[1])
    pk = tmp_path / "picks.json"
    pk.write_text(json.dumps({"photo": "SYN1", "picks": []}), encoding="utf-8")
    out = tmp_path / "c.jpg"
    rc = M.main(["photos", "pnp", str(FIX), "--picks", str(pk), "--no-register", "--check-jpg", str(out)])
    assert rc == 1 and out.exists() and called["write"] is False
