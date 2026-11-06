"""recorder.depth のモデル無しで動く部分（比の集計・npz の読み書き・判定）。モデルが入っていなければ推論は skip。"""

import importlib.util

import numpy as np
import pytest

from recorder import depth


def test_npz_roundtrip_and_sample(tmp_path):
    D = np.arange(12, dtype=float).reshape(3, 4) + 1.0        # 格子 4×3、元の写真は 8×6（scale 2）
    p = depth.save_npz(tmp_path / "a_depth_pro.npz", D, (8, 6), 1234.5, "depth_pro")
    doc = depth.load_npz(p)
    assert doc["scale"] == 2.0 and doc["orig_size"] == (8, 6) and doc["focal_px"] == 1234.5 and doc["model"] == "depth_pro"
    v = depth.sample(doc, [0.0, 6.0, 100.0], [0.0, 4.0, 0.0], win=0)
    assert v[0] == 1.0 and v[1] == D[2, 3] and np.isnan(v[2])
    doc2 = depth.load_npz(depth.save_npz(tmp_path / "b.npz", D, (8, 6), None, "da2_small"))
    assert doc2["focal_px"] is None


def test_summarize_and_spread():
    s = depth.summarize([1.0, 1.0, 1.0, None, float("nan")])
    assert s["n"] == 3 and s["median"] == 1.0 and s["spread"] == 0.0 and s["abs_err_median"] == 0.0
    assert depth.summarize([])["n"] == 0
    r = [0.9, 1.0, 1.1, 1.0, 1.0]
    assert abs(depth.rel_spread(r) - 0.0) < 1e-9          # MAD は 0（半分以上が 1.0）
    assert depth.rel_spread([0.5, 1.0, 1.5]) == pytest.approx(1.4826 * 0.5 / 1.0)


def test_judge_and_bands():
    assert depth.judge(0.05, 0.02).startswith("検算の門")
    assert depth.judge(0.05, -0.5).startswith("向きの参考")    # 散らばりが小さくても偏りが大きければ門には使えない
    assert depth.judge(0.2).startswith("粗い")
    assert depth.judge(None).startswith("判定できない")
    assert depth.band_of(3.0, depth.DIST_BANDS) == "5 m 未満"
    assert depth.band_of(20.0, depth.DIST_BANDS) == "15 m 超"
    assert depth.band_of(45.0, depth.ELEV_BANDS) == "見上げ 30° 超"


def test_aggregate_minimal():
    pts = [{"ratio": 0.5, "z_true": 10.0, "elev_deg": 5.0}, {"ratio": 0.6, "z_true": 20.0, "elev_deg": 40.0}]
    one = {"all": depth.summarize([0.5, 0.6]), "by_dist": {}, "by_elev": {}}
    per = [{"building": "x", "focal_err": 0.1, "landmark": one, "grid": one, "landmark_points": pts, "grid_points": pts}]
    a = depth.aggregate(per, "depth_pro")
    assert a["grid"]["points"]["n"] == 2 and a["grid"]["by_dist"]["5〜15 m"]["n"] == 1
    assert a["focal_err"]["median"] == 0.1


@pytest.mark.skipif(importlib.util.find_spec("depth_pro") is None or not depth.CKPT_DIR.joinpath("depth_pro.pt").exists(),
                    reason="Depth Pro が入っていない")
def test_model_available():
    assert "depth_pro" in depth.MODELS
