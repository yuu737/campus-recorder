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
