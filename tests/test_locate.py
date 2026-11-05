"""模型合わせ locate の部品のテスト（回転の約束・焦点・面の前の絞り・勾配の採点の形）。写真は使わない。"""
import math

import numpy as np

from recorder import locate


def test_rotation_round_trip():
    for yaw, pitch, roll in [(0, 0, 0), (13.2, 28.4, -6.1), (247.5, 30.1, 0.9), (359.0, -10.0, 4.0)]:
        R = locate.rotation_cv(yaw, pitch, roll)
        assert np.allclose(R @ R.T, np.eye(3), atol=1e-9) and abs(np.linalg.det(R) - 1) < 1e-9
        y2, p2, r2 = locate.angles_of(R)
        assert abs((y2 - yaw + 180) % 360 - 180) < 1e-6 and abs(p2 - pitch) < 1e-6 and abs(r2 - roll) < 1e-6


def test_rotation_convention():
    # 北を向いて水平：前＝+y、右＝+x、下＝−z
    R = locate.rotation_cv(0, 0, 0)
    assert np.allclose(R[2], [0, 1, 0]) and np.allclose(R[0], [1, 0, 0]) and np.allclose(R[1], [0, 0, -1])
    # 東を向く
    assert np.allclose(locate.rotation_cv(90, 0, 0)[2], [1, 0, 0])
    # 見上げると前の z が正
    assert locate.rotation_cv(0, 30, 0)[2][2] > 0


def test_focal_from_exif():
    assert abs(locate.focal_px_from_exif({"focal_35mm": 14.0, "width": 4624, "height": 3472}) - 14 / 36 * 4624) < 1e-6
    assert abs(locate.focal_px_from_exif({"focal_35mm": 25.0, "width": 3472, "height": 4624}) - 25 / 36 * 4624) < 1e-6


def test_face_prior_in_front_of_face():
    b = {"faces": [{"id": "S", "from": [0.0, 0.0], "to": [20.0, 0.0], "top_z": 10.0}]}
    pos, yaws = locate.face_prior(b, "S", max_dist=10.0, step=5.0)
    # 面は x 軸上、外向きは (u.y, −u.x) = (0, −1)：南側に立ち、北（0°）を向く
    assert all(y < 0 for _, y in pos) and min(y for _, y in pos) >= -10.5
    assert all(abs((y + 180) % 360 - 180) <= 50.0 + 1e-6 for y in yaws) and any(abs((y + 180) % 360 - 180) <= 1.5 for y in yaws)
    assert locate.face_prior(b, "passage") is None


def test_model_points_and_thin(tmp_path):
    from recorder.schema import load_building
    b = load_building("tests/fixtures/parts_building.json")
    mp = locate.model_points(b)
    assert len(mp["P"]) > 100 and mp["P"].shape[1] == 3 and mp["D"].shape == mp["P"].shape
    assert set(np.unique(mp["face"])) <= set(range(len(mp["ids"])))
    thin = locate.thin_points(mp, 20)
    assert len(thin["P"]) <= 20 * len(mp["ids"]) and len(thin["vert"]) == len(thin["P"])


def test_grad_scores_shape_with_synthetic_image():
    import cv2
    img = np.full((600, 800, 3), 128, np.uint8)
    cv2.rectangle(img, (200, 150), (600, 450), (40, 40, 40), 3)
    dt = locate.EdgeDT(img, long_side=800)
    b = {"faces": [{"id": "S", "from": [-5.0, 0.0], "to": [5.0, 0.0], "top_z": 6.0,
                    "bays": {"count": 2}, "levels": {"count": 2, "floor_pitch": 3.0}}]}
    mp = locate.model_points(b)
    if len(mp["P"]) == 0:
        return
    C = np.array([[0.0, -12.0, 1.5]]); R = locate.rotation_cv(0, 10, 0)[None]
    r = locate.grad_scores(C, R, np.array([800.0]), mp, dt, locate.GRAD_SIGMA_FINE)
    assert r["grad"].shape == (1,) and 0 <= r["grad"][0] <= 1.5 and 0 <= r["coverage"][0] <= 1
