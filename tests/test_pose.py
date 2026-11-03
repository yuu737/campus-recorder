"""写真の視点の逆算（PnP）のテスト。既知のカメラで模型の角を投影し、位置と向きが戻ることを確かめる。"""
import math

import numpy as np

from recorder import pose


def _project(pts_xyz, R, t, f, w, h):
    out = []
    for p in pts_xyz:
        c = R @ np.asarray(p, float) + t
        out.append([f * c[0] / c[2] + w / 2, f * c[1] / c[2] + h / 2])
    return out


def _look_at(cam, target):
    """OpenCV の向き（+Z 前、+Y 下）で cam から target を見る回転。"""
    z = np.asarray(target, float) - np.asarray(cam, float)
    z /= np.linalg.norm(z)
    up = np.array([0, 0, 1.0])
    x = np.cross(z, up)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    R = np.stack([x, y, z])          # 行が各カメラ軸（世界→カメラ）
    t = -R @ np.asarray(cam, float)
    return R, t


def test_pose_recovers_camera():
    w, h = 4000, 3000
    f = pose.focal_px_from_35mm(24.0, w, h)
    cam = [20.0, -45.0, 1.6]
    R, t = _look_at(cam, [0.0, 0.0, 10.0])
    corners = [[-15, 0, 0], [15, 0, 0], [15, 0, 20], [-15, 0, 20], [15, 12, 20], [15, 12, 0]]
    px = _project(corners, R, t, f, w, h)
    rng = np.random.default_rng(0)
    px = [[u + rng.normal(0, 1.5), v + rng.normal(0, 1.5)] for u, v in px]
    res = pose.solve(px, corners, w, h, f)
    assert res.ok, res.reason
    assert np.linalg.norm(np.array(res.camera_xyz) - np.array(cam)) < 0.3
    assert res.reproj_max_px < 8
    bl = res.blender
    assert abs(bl["lens_mm"] - f * 36 / w) < 1e-6
    # Blender のカメラの前方（局所 -Z）が target を向いているか
    ex, ey, ez = bl["rotation_euler"]
    Rx = np.array([[1, 0, 0], [0, math.cos(ex), -math.sin(ex)], [0, math.sin(ex), math.cos(ex)]])
    Ry = np.array([[math.cos(ey), 0, math.sin(ey)], [0, 1, 0], [-math.sin(ey), 0, math.cos(ey)]])
    Rz = np.array([[math.cos(ez), -math.sin(ez), 0], [math.sin(ez), math.cos(ez), 0], [0, 0, 1]])
    fwd = (Rz @ Ry @ Rx) @ np.array([0, 0, -1.0])
    want = np.array([0, 0, 10.0]) - np.array(cam)
    want /= np.linalg.norm(want)
    assert np.dot(fwd, want) > 0.999


def test_pose_rejects_too_few_points():
    res = pose.solve([[0, 0], [1, 1], [2, 2]], [[0, 0, 0], [1, 0, 0], [0, 1, 0]], 100, 100, 100.0)
    assert not res.ok


def _rect_setup(tmp_path, f_true=1000.0, f_claimed=1000.0):
    import json
    w, h = 1600, 1200
    cam, tgt = (10.0, -12.0, 1.6), (10.0, 0.0, 5.0)
    R, t = _look_at(cam, tgt)
    K = np.array([[f_true, 0, w / 2], [0, f_true, h / 2], [0, 0, 1.0]])
    Hp = K @ np.column_stack([R[:, 0], R[:, 2], t])           # 面 (s, z) → 写真 px
    ppm, xo, zo = 50.0, 100.0, 700.0
    A = np.array([[ppm, 0, xo], [0, -ppm, zo], [0, 0, 1.0]])
    H_img2rect = A @ np.linalg.inv(Hp)
    mm = lambda v: {"value": v, "status": "estimated", "evidence": [], "method": "t"}
    b = {"schema": "x", "id": "t", "name": "t", "parapet_top": mm(10.0),
         "faces": [{"id": "S", "from": [0.0, 0.0], "to": [20.0, 0.0]}]}
    (tmp_path / "b.json").write_text(json.dumps(b), encoding="utf-8")
    (tmp_path / "f.json").write_text(json.dumps({"H": H_img2rect.tolist(), "width": w, "height": h, "focal_px": f_claimed}), encoding="utf-8")
    (tmp_path / "c.json").write_text(json.dumps({"px_per_m": ppm, "x_origin_px": xo, "z_origin_px": zo}), encoding="utf-8")
    return tmp_path


def test_from_rectification_holdout_and_camera(tmp_path):
    d = _rect_setup(tmp_path)
    res = pose.from_rectification(tmp_path / "p.jpg", d / "f.json", d / "c.json", d / "b.json", "S", out_json=d / "o.json")
    assert res.ok and res.n_holdout >= 3
    assert abs(res.camera_height_m - 1.6) < 0.05 and abs(res.distance_to_face_m - 12.0) < 0.1
    assert res.holdout_reproj_px is not None and res.holdout_reproj_px < 0.5


def test_from_rectification_holdout_catches_wrong_focal(tmp_path):
    d = _rect_setup(tmp_path, f_true=1000.0, f_claimed=1500.0)     # 焦点距離が食い違うと、使わなかった点で誤差が出る
    res = pose.from_rectification(tmp_path / "p.jpg", d / "f.json", d / "c.json", d / "b.json", "S", out_json=d / "o.json")
    assert res.holdout_reproj_px > 1.0
