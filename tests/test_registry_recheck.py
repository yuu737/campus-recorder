"""写真の登録（H_plane）・自動の再検収（線のずれ）・被覆図の計算。合成のカメラと画像で。"""

from __future__ import annotations

import numpy as np

from recorder import coverage, recheck, registry

FACE = {"id": "S", "from": [0.0, 0.0], "to": [20.0, 0.0]}      # 外向きは (0, −1)＝南


def camera(C=(10.0, -15.0, 1.5), f=1500.0, w=2000, h=1500):
    R = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float)      # 北（+y）を向く
    t = -R @ np.array(C, float)
    return R, t, f, w, h


def test_H_plane_roundtrip_and_matches_pose():
    R, t, f, w, h = camera()
    H = registry.H_from_pose(R, t, f, w, h, FACE)
    s = np.array([1.0, 5.0, 12.5, 19.0])
    z = np.array([0.5, 3.0, 8.0, 12.0])
    u, v, d = registry.apply_H(H, s, z)
    assert (d > 0).all()
    s2, z2 = registry.invert_H(H, u, v)
    assert np.allclose(s2, s, atol=1e-9) and np.allclose(z2, z, atol=1e-9)
    cam = {"R_cv": R.tolist(), "t_cv": t.tolist(), "focal_px": f, "image_size": [w, h]}
    uu, vv, _ = registry.project_world(cam, registry.plane_to_world(FACE, s, z))
    assert np.allclose(uu, u) and np.allclose(vv, v)
    # 定数倍（符号を含む）を揃える
    assert np.allclose(registry.normalize_like(-3.0 * H, H), H)


def test_registry_read_write(tmp_path):
    p = tmp_path / "registry.json"
    reg = registry.load_registry(p)
    reg["not_registered"].append({"photo": "A", "building": "x", "face": "S", "reason": "正面化が無い"})
    e = {"photo": "A", "building": "x", "face": "S", "H_plane": np.eye(3).tolist()}
    registry.upsert(reg, e)
    registry.upsert(reg, {**e, "note": "置き換え"})
    registry.save_registry(reg, p)
    back = registry.load_registry(p)
    assert len(back["entries"]) == 1 and back["entries"][0]["note"] == "置き換え"
    assert back["not_registered"] == []          # 登録できたら理由の一覧から外れる
    assert registry.entries_for(back, "x", ["A"]) and not registry.entries_for(back, "y")


def _synthetic_photo(H, w, h, z_edge, s_edge, ppm=100):
    """面の上に「z < z_edge は暗い」「s > s_edge は暗い」の縁を描いた平面を、H で写真に写す。"""
    import cv2

    Wp, Hp = 20 * ppm, 15 * ppm
    plane = np.full((Hp, Wp), 200, np.uint8)
    zrow = int(round(Hp - z_edge * ppm))
    plane[zrow:, :] = 60
    plane[:, int(round(s_edge * ppm)):] = 110
    A = np.array([[ppm, 0, 0], [0, -ppm, Hp], [0, 0, 1]], float)      # 面 (s, z) → 平面の画像 px
    M = H @ np.linalg.inv(A)
    img = cv2.warpPerspective(plane, M, (w, h), flags=cv2.INTER_LINEAR, borderValue=128)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def test_recheck_measures_shift_in_m():
    R, t, f, w, h = camera()
    H = registry.H_from_pose(R, t, f, w, h, FACE)
    img = _synthetic_photo(H, w, h, z_edge=4.10, s_edge=12.0)
    entry = {"H_plane": H.tolist(), "face_frame": dict(FACE), "image_size": [w, h],
             "camera": {"R_cv": R.tolist(), "t_cv": t.tolist(), "focal_px": f, "image_size": [w, h]}}
    pr = recheck.Projector(entry, FACE)
    edge = recheck.EdgeImage(img)
    zl = recheck._line("帯 下端", "面", "level", "z", 4.0, 0.0, [(2.0, 10.0)])
    r = recheck.measure_line(edge, pr, zl)
    assert r["found"] and abs(r["diff_m"] - 0.10) < 0.02
    xl = recheck._line("柱型1左", "面", "periodic", "s", 12.12, 0.0, [(5.0, 12.0)])
    r = recheck.measure_line(edge, pr, xl)
    assert r["found"] and abs(r["diff_m"] + 0.12) < 0.02
    # 面が登録の後に動いても（同じ直線の上で始点が 1 m 先へ）、今の面の座標で同じ縁を指す
    moved = {"from": [1.0, 0.0], "to": [20.0, 0.0]}
    r = recheck.measure_line(edge, recheck.Projector(entry, moved),
                             recheck._line("柱型1左", "面", "periodic", "s", 11.12, 0.0, [(5.0, 12.0)]))
    assert r["found"] and abs(r["diff_m"] + 0.12) < 0.02


def test_cover_polygon_and_coverage_pct():
    # 写真 1000×1000 px に、面の s 0〜10 m・z 0〜10 m がちょうど入る（1 m = 100 px）
    H = np.array([[100.0, 0, 0], [0, -100.0, 1000.0], [0, 0, 1]])
    poly = registry.cover_polygon(H, 1000, 1000, (0.0, 20.0, 0.0, 10.0))
    assert abs(registry.poly_area(poly) - 100.0) < 1e-6
    mask, sc, zc = coverage.raster([poly], (0.0, 20.0, 0.0, 10.0), 0.25)
    assert abs(mask.mean() - 0.5) < 1e-9
    rects = coverage.uncovered_rects(mask, sc, zc, 0.25)
    assert rects and rects[0]["s0"] == 10.0 and rects[0]["s1"] == 20.0 and rects[0]["z1"] == 10.0
    # カメラの後ろは覆わない
    R, t, f, w, h = camera()
    Hc = registry.H_from_pose(R, t, f, w, h, FACE)
    behind = registry.cover_polygon(Hc, w, h, (0.0, 20.0, 0.0, 10.0))
    assert len(behind) >= 3
    flipped = {"from": [20.0, -40.0], "to": [0.0, -40.0]}      # カメラの後ろ（y = −40）の面
    Hb = registry.H_from_pose(R, t, f, w, h, flipped)
    assert registry.cover_polygon(Hb, w, h, (0.0, 20.0, 0.0, 10.0)) == []


def test_compass8():
    assert registry.compass8(1, 0) == "東" and registry.compass8(0, -1) == "南" and registry.compass8(-1, 1) == "北西"


def test_check_lines_include_new_parts():
    f = {**FACE, "length": 20.0, "bays": {"count": 4, "pitch": 5.0, "pier_width": 0.8},
         "windows": [{"name": "2階窓", "z_bottom": 5.0, "z_top": 7.0, "width": 3.0, "spandrel": {"height": 0.5}}],
         "bands": [{"name": "腰壁", "z_bottom": 3.0, "z_top": 4.0, "depth": 1.0, "rail": {"height": 0.9}}]}
    b = {"id": "t", "parapet_top": 12.0, "faces": [f],
         "parts": [{"type": "column", "name": "柱", "x": 10.0, "y": -2.0, "size": 0.8, "z0": 0.0, "z1": 3.0}]}
    lines = recheck.face_check_lines(b, "S")
    names = [l["what"] for l in lines]
    assert "2階窓 上端" in names and "柱型1左" in names
    sp = next(l for l in lines if l["what"].startswith("窓下パネル"))
    assert abs(sp["json_m"] - 4.5) < 1e-9 and sp["group"] == "部品"
    rl = next(l for l in lines if l["what"].startswith("帯の手すり"))
    assert abs(rl["json_m"] - 4.9) < 1e-9 and rl["depth_m"] == 0.5
    band = next(l for l in lines if l["what"] == "腰壁 上端")
    assert band["depth_m"] == 1.0          # 手前に出た帯は前の縁
    assert any(l["what"].startswith("部品0（柱）") and l["group"] == "部品" for l in lines)   # 面の前の単独の柱
