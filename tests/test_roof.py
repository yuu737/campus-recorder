"""航空写真の屋上の読み取り（recorder.roof）を合成画像で確かめる。"""
import cv2
import numpy as np
import pytest

from recorder import roof

PPM = 2.0   # 0.5 m/px（国土地理院 z18 と同じくらい）
RECT = [[-10.0, -5.0], [10.0, -5.0], [10.0, 5.0], [-10.0, 5.0]]   # 足元 20×10 m


def _synthetic(lean=(-2.0, -1.5), box=None, seed=0):
    """地面（中くらいの灰色）に、足元を lean だけずらした屋上（明るい）を描く。box=(cx, cy, w, d) は写真の上の位置。"""
    rng = np.random.default_rng(seed)
    fr = roof.Frame.north_up(PPM, 60.0, 50.0)
    img = np.full((100, 120), 90.0, np.float32)
    poly = np.round(fr.to_px(np.asarray(RECT) + lean)).astype(np.int32)
    cv2.fillPoly(img, [poly], 180.0)
    if box:
        cx, cy, w, d = box
        b = [[cx - w / 2, cy - d / 2], [cx + w / 2, cy - d / 2], [cx + w / 2, cy + d / 2], [cx - w / 2, cy + d / 2]]
        cv2.fillPoly(img, [np.round(fr.to_px(b)).astype(np.int32)], 250.0)
    img += rng.normal(0, 3, img.shape).astype(np.float32)
    return img, fr


def test_edge_scan_finds_shift():
    img, fr = _synthetic(lean=(-2.0, -1.5))
    res = roof.analyze_image(img, fr, RECT, height_m=15.0)
    dx, dy = res["lean_m"]
    assert abs(dx + 2.0) <= 0.5 and abs(dy + 1.5) <= 0.5
    assert len(res["roof_edge_offset_candidates"]) == 3
    assert res["roof_edge_source"] == "auto"
    assert res["roof_edge_offset_candidates"][0]["confidence"] == 1.0


def test_bright_box_is_picked_and_lean_subtracted():
    lean = (-2.0, -1.5)
    img, fr = _synthetic(lean=lean, box=(2.0 + lean[0], 1.0 + lean[1], 2.5, 2.0))
    res = roof.analyze_image(img, fr, RECT, height_m=15.0)
    bright = [u for u in res["units"] if u["contrast"] > 0]
    assert len(bright) == 1
    u = bright[0]
    assert abs(u["w"] - 2.5) < 0.7 and abs(u["d"] - 2.0) < 0.7
    # 中心は倒れ込みを引いた足元の座標
    assert abs(u["cx"] - 2.0) < 0.6 and abs(u["cy"] - 1.0) < 0.6


def test_small_blob_is_dropped():
    img, fr = _synthetic(box=(0.0, 0.0, 0.5, 0.5))
    res = roof.analyze_image(img, fr, RECT, height_m=15.0)
    assert res["units"] == []


def test_ratio_warning():
    img, fr = _synthetic(lean=(-2.0, -1.5))
    ok = roof.analyze_image(img, fr, RECT, height_m=15.0)       # 2.5 / 15 = 0.17
    assert 0.05 <= ok["lean_ratio"] <= 0.25
    assert not any("倒れ込み率" in w for w in ok["warnings"])
    bad = roof.analyze_image(img, fr, RECT, height_m=100.0)     # 0.025
    assert any("倒れ込み率" in w for w in bad["warnings"])


def test_manual_roof_edge_wins():
    img, fr = _synthetic(lean=(-2.0, -1.5))
    pts = [(0.0, 3.5), (8.0, -2.0), (-12.0, 0.0)]   # 足元を (-2, -1.5) ずらした辺の上の点
    res = roof.analyze_image(img, fr, RECT, height_m=15.0, roof_edge_pts=pts)
    assert res["roof_edge_source"] == "manual"
    assert res["lean_m"] == pytest.approx([-2.0, -1.5], abs=0.06)
    assert res["ambiguous"] is False


def test_blocks_scale_with_height():
    g = roof.make_groups(RECT, 20.0, [{"id": "A", "polygon": RECT, "top_z": {"value": 20.0}},
                                      {"id": "B", "polygon": RECT, "top_z": {"value": 10.0}}])
    assert [x["k"] for x in g] == [1.0, 0.5]


def test_parse_pairs():
    assert roof.parse_pairs("1,2;3.5,-4", 2) == [(1.0, 2.0), (3.5, -4.0)]
    assert roof.parse_pairs(None, 2) == []
    with pytest.raises(ValueError):
        roof.parse_pairs("1,2,3", 2)
