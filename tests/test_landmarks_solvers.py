"""目印の登録の解き方（cv2／PoseLib／PoseLib＋GeoCalib、線の対応、4 自由度）のテスト。合成データだけ。写真と台帳は触らない。"""
import math

import numpy as np
import pytest

from recorder import landmarks as LM
from recorder import locate
from recorder.schema import load_building

poselib = pytest.importorskip("poselib")

W, H, FOCAL = 4000, 3000, 2600.0


def _scene(pitch=12.0, roll=1.0, dist=18.0):
    b = load_building("tests/fixtures/parts_building.json")
    f = b["faces"][0]
    p0 = np.array(f["from"], float); d = np.array(f["to"], float) - p0; L = np.linalg.norm(d); u = d / L; n = np.array([u[1], -u[0]])
    C = np.r_[p0 + u * L / 2 + n * dist, 1.6]
    yaw = math.degrees(math.atan2(-n[0], -n[1])) % 360
    R = locate.rotation_cv(yaw, pitch, roll)
    return b, f, C, R, -R @ C


def _visible(b, f, R, t):
    cat = [m for m in LM.catalog(b) if m["face"] == f["id"]]
    P = np.array([m["xyz"] for m in cat])
    uv, d = LM._project(R, t, FOCAL, W, H, P)
    ok = (d > 1) & (uv[:, 0] > 0.05 * W) & (uv[:, 0] < 0.95 * W) & (uv[:, 1] > 0.05 * H) & (uv[:, 1] < 0.95 * H)
    return [cat[i] for i in np.nonzero(ok)[0]], P[ok], uv[ok]


def test_poselib_matches_cv2_on_synthetic():
    b, f, C, R, t = _scene()
    cat, P, uv = _visible(b, f, R, t)
    sel = np.linspace(0, len(P) - 1, min(8, len(P))).round().astype(int)
    noisy = uv[sel] + np.random.default_rng(1).normal(0, 4.0, (len(sel), 2))
    a = LM.solve(P[sel], noisy, W, H, FOCAL, method="cv2")
    p = LM.solve(P[sel], noisy, W, H, FOCAL, method="poselib")
    Ca, Cp = -a["R"].T @ a["t"], -p["R"].T @ p["t"]
    assert p["method"] == "poselib" and a["method"] == "cv2"
    assert np.linalg.norm(Ca - Cp) < 0.3                       # 同じ点からは同じ視点
    assert np.linalg.norm(Cp - C) < 1.5
    assert abs(a["rms_px"] - p["rms_px"]) < 1.0


def test_solve_default_is_cv2_and_bad_method_raises():
    b, f, C, R, t = _scene()
    _, P, uv = _visible(b, f, R, t)
    assert LM.solve(P[:6], uv[:6], W, H, FOCAL)["method"] == "cv2"
    with pytest.raises(ValueError):
        LM.solve(P[:6], uv[:6], W, H, FOCAL, method="magic")


def test_four_dof_with_gravity_prior_from_three_points():
    b, f, C, R, t = _scene(pitch=15.0, roll=-2.0)
    _, P, uv = _visible(b, f, R, t)
    sel = [0, len(P) // 2, len(P) - 1]
    sol = LM.solve(P[sel], uv[sel], W, H, FOCAL, method="poselib+geocalib", prior={"focal": FOCAL, "roll": -2.0, "pitch": 15.0})
    assert sol["method"] == "poselib+geocalib(4dof)" and sol["dof"] == 4
    assert np.linalg.norm(-sol["R"].T @ sol["t"] - C) < 0.05


def test_gravity_prior_rejects_wrong_tilt_candidates():
    b, f, C, R, t = _scene(pitch=10.0, roll=0.0)
    _, P, uv = _visible(b, f, R, t)
    sel = np.linspace(0, len(P) - 1, 8).round().astype(int)
    # わざと 25° 違う事前値：PoseLib の解は落ち、4 自由度の解（事前値の傾き）に置き換わる
    sol = LM.solve(P[sel], uv[sel], W, H, FOCAL, method="poselib+geocalib", prior={"focal": FOCAL, "roll": 0.0, "pitch": 35.0})
    assert sol["n_rejected_by_gravity"] >= 1 and sol.get("dof") == 4
    _, pitch, _ = locate.angles_of(sol["R"])
    assert abs(pitch - 35.0) < 1e-6


def test_line_marks_catalog_and_ids():
    b = load_building("tests/fixtures/parts_building.json")
    pts = LM.catalog(b)
    allm = LM.catalog(b, lines=True)
    lines = [m for m in allm if m.get("kind") == "line"]
    assert lines and [m for m in allm if m.get("kind") != "line"] == pts      # 点の一覧と id は変わらない
    assert all(("-V" in m["id"]) or ("-H" in m["id"]) for m in lines)
    assert not ({m["id"] for m in lines} & {m["id"] for m in pts})
    assert {m["dir"] for m in lines} == {"v", "h"}


def test_points_and_lines_pnpl_recovers_camera():
    b, f, C, R, t = _scene()
    _, P, uv = _visible(b, f, R, t)
    lines = [m for m in LM.catalog(b, lines=True) if m.get("kind") == "line" and m["face"] == f["id"]]
    lns = []
    for m in lines:
        X = np.linspace(m["xyz1"], m["xyz2"], 20)
        q, d = LM._project(R, t, FOCAL, W, H, X)
        ok = np.nonzero((d > 1) & (q[:, 0] > 0) & (q[:, 0] < W) & (q[:, 1] > 0) & (q[:, 1] < H))[0]
        if len(ok) >= 4:
            lns.append({"X1": m["xyz1"], "X2": m["xyz2"], "x1": q[ok[1]], "x2": q[ok[-2]]})
    assert len(lns) >= 2
    sel = [0, len(P) - 1]
    sol = LM.solve(P[sel], uv[sel], W, H, FOCAL, method="poselib", lines=lns[:3], search_focal=False)
    assert sol and np.linalg.norm(-sol["R"].T @ sol["t"] - C) < 0.05
    assert len(sol["line_inliers"]) == len(lns[:3]) and max(sol["line_reproj_px"]) < 1.0


def test_line_pick_px_formats():
    a = LM._line_pick_px({"id": "x", "cells": ["C3", "C8"]}, W, H)
    assert a[0] == (1000.0, 750.0) and a[1] == (1000.0, 2250.0)
    b2 = LM._line_pick_px({"id": "x", "cells": [{"cell": "C3", "sub": "A1"}, "C8"], "subs": [None, "J10"]}, W, H)
    assert b2[0] != a[0] and b2[1] != a[1]
    with pytest.raises(ValueError):
        LM._line_pick_px({"id": "x", "cells": ["C3"]}, W, H)


def test_geocalib_prior_skips_without_install(tmp_path):
    pytest.importorskip("geocalib")
    pytest.importorskip("torch")
    # 保存済みの結果は読み直す（GeoCalib を走らせない）
    (tmp_path / "geocalib_X.json").write_text('{"focal_px": 3000.0, "raw_roll_deg": 1.5, "raw_pitch_deg": 7.0}', encoding="utf-8")
    pr = LM.geocalib_prior({"id": "X"}, "b", out_dir=tmp_path)
    assert pr["focal"] == 3000.0 and pr["roll"] == LM.GC_ROLL_SIGN * 1.5 and pr["pitch"] == LM.GC_PITCH_SIGN * 7.0
