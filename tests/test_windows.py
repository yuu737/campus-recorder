"""窓の自動検出（recorder/windows.py）のモデル無しで動く部分：箱→升、IoU の突き合わせ、正解の箱の投影。"""

import importlib.util

import numpy as np
import pytest

from recorder import windows as W
from recorder.landmarks import pick_to_px


def test_px_to_cell_roundtrip_within_subcell():
    w, h = 4000, 3000
    rng = np.random.default_rng(0)
    for u, v in rng.uniform([0, 0], [w, h], size=(200, 2)):
        cell, sub = W.px_to_cell(u, v, w, h)
        u2, v2, how = pick_to_px({"cell": cell, "sub": sub}, w, h)
        assert how == "cell+sub"
        # 寄りの升は元の升の 1.5/10、半分の幅以内に戻る（縁の升は寄りの範囲が切れて少し小さい）
        assert abs(u2 - u) <= 0.5 * 1.5 * w / 100 + 1e-6
        assert abs(v2 - v) <= 0.5 * 1.5 * h / 100 + 1e-6


def test_px_to_cell_corners():
    assert W.px_to_cell(0, 0, 1000, 1000)[0] == "A1"
    assert W.px_to_cell(999.9, 999.9, 1000, 1000)[0] == "J10"
    assert W.px_to_cell(1000, 1000, 1000, 1000)[0] == "J10"     # 縁ちょうどは最後の升


def test_box_cells_has_four_corners():
    c = W.box_cells([100, 200, 900, 700], 1000, 1000)
    assert set(c) == {"左上", "右上", "右下", "左下"}
    assert c["左上"]["cell"] == "B3" and c["右下"]["cell"] == "J8"


def test_iou_basic():
    assert W.iou([0, 0, 10, 10], [0, 0, 10, 10]) == pytest.approx(1.0)
    assert W.iou([0, 0, 10, 10], [5, 0, 15, 10]) == pytest.approx(50 / 150)
    assert W.iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0


def test_match_one_to_one_and_prf():
    gt = [[0, 0, 10, 10], [20, 0, 30, 10], [40, 0, 50, 10]]
    det = [[0, 0, 10, 10], [1, 0, 11, 10], [21, 0, 31, 10], [100, 100, 110, 110]]
    m = W.match(gt, det, 0.5, scores=[0.9, 0.8, 0.7, 0.6])
    assert (m["tp"], m["fp"], m["fn"]) == (2, 2, 1)       # 同じ窓の 2 つ目の箱は誤り
    r = W.prf(m["tp"], m["fp"], m["fn"])
    assert r["precision"] == 0.5 and r["recall"] == pytest.approx(0.667, abs=1e-3)


def test_nms_drops_duplicates():
    keep = W.nms([[0, 0, 10, 10], [1, 1, 11, 11], [50, 50, 60, 60]], [0.5, 0.9, 0.3])
    assert sorted(keep) == [1, 2]


def test_best_shift_recovers_translation():
    gt = [[100 + 60 * i, 100 + 80 * j, 140 + 60 * i, 160 + 80 * j] for i in range(5) for j in range(3)]
    det = [[a + 23, b - 31, c + 23, d - 31] for a, b, c, d in gt[:12]]
    dx, dy, tp = W.best_shift(gt, det, 0.5, w=1000, h=1000)
    assert tp == 12 and abs(dx - 23) <= 5 and abs(dy + 31) <= 5


def test_tile_rects_cover_image():
    rs = W.tile_rects(1600, 1200, 3)
    assert len(rs) == 9
    assert min(r[0] for r in rs) == 0 and max(r[2] for r in rs) == 1600
    assert min(r[1] for r in rs) == 0 and max(r[3] for r in rs) == 1200


def _toy_building():
    return {"id": "toy", "faces": [{
        "id": "S", "from": [0.0, 0.0], "to": [20.0, 0.0], "ground_offset": 0.0, "top_z": 10.0,
        "bays": {"count": 4, "pitch": 5.0, "pier_width": 0.6, "margin_from": 0.0, "margin_to": 0.0},
        "windows": [{"name": "2階窓", "per_bay": 1, "z_bottom": 4.0, "z_top": 6.0, "width": 3.0}],
    }]}


def test_face_window_rects_follow_bays():
    b = _toy_building()
    rs = W.face_window_rects(b, b["faces"][0])
    assert len(rs) == 4
    assert rs[0]["s0"] == pytest.approx(2.5 - 1.45) and rs[0]["s1"] == pytest.approx(2.5 + 1.45)   # 幅 3.0 − 隙間 0.1
    assert rs[-1]["z0"] == 4.0 and rs[-1]["z1"] == 6.0


def test_gt_boxes_front_and_back():
    """外向きは (u.y, −u.x) ＝ 南（y<0）。南に立つカメラから窓 4 つ、北（裏）からは 0。"""
    b = _toy_building()
    w, h, f = 1600, 1200, 1200.0

    def cam_at(C, look):
        z = np.array(look, float) - np.array(C, float)
        z /= np.linalg.norm(z)
        x = np.cross(z, [0, 0, 1.0])
        x /= np.linalg.norm(x)
        y = np.cross(z, x)
        R = np.vstack([x, y, z])
        t = -R @ np.array(C, float)
        return {"R_cv": R.tolist(), "t_cv": t.tolist(), "focal_px": f, "image_size": [w, h]}

    front = W.gt_boxes(b, cam_at([10, -25, 1.5], [10, 0, 5]))
    assert len(front["windows"]) == 4
    xs = sorted((g["box"][0] + g["box"][2]) / 2 for g in front["windows"])
    assert xs[0] < xs[-1]
    back = W.gt_boxes(b, cam_at([10, 25, 1.5], [10, 0, 5]))
    assert back["windows"] == []


@pytest.mark.skipif(importlib.util.find_spec("transformers") is None, reason="transformers 未導入")
def test_transformers_has_grounding_dino():
    from transformers import AutoModelForZeroShotObjectDetection  # noqa: F401
