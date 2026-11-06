"""面の上端の高さ（registry.face_top）を棟 JSON から引く。目印（landmarks）の上端の線・登録の面の矩形はこれを使う。"""

from __future__ import annotations

import warnings

import pytest

from recorder import registry


def m(v, status="measured"):
    return {"value": v, "status": status, "evidence": ["P1"]}


FACE = {"id": "f0", "from": [0.0, 0.0], "to": [20.0, 0.0]}
BLOCKS = [{"id": "low", "polygon": [[0, 0], [20, 0], [20, 10], [0, 10]], "top_z": m(23.8)},
          {"id": "tower", "polygon": [[0, 10], [20, 10], [20, 30], [0, 30]], "top_z": m(67.88)}]


def test_face_top_order_and_fallback():
    # 面の top_z が最優先
    b = {"id": "t", "blocks": BLOCKS, "parapet_top": m(67.88)}
    assert registry.face_top_source(b, {**FACE, "top_z": m(71.51)}) == (71.51, "face.top_z")
    # 面の両端を頂点に持つ塊の top_z（棟の parapet_top より先）
    assert registry.face_top_source(b, FACE) == (23.8, "blocks[low].top_z")
    # 両方の塊に接する面（y=10 の辺）は高い方
    assert registry.face_top(b, {"id": "mid", "from": [20.0, 10.0], "to": [0.0, 10.0]}) == 67.88
    # 塊に当たらなければ parapet_top（タワー75 の 68 m 級。20 m に落ちない）
    far = {"id": "x", "from": [100.0, 0.0], "to": [120.0, 0.0]}
    assert registry.face_top_source(b, far) == (67.88, "parapet_top")
    # unknown の値は飛ばして floor_count × floor_pitch（棟の z=0 からなので ground_z は足さない）
    b2 = {"id": "t", "ground_z": m(42.9), "parapet_top": {"value": None, "status": "unknown"},
          "floor_count": m(16), "floor_pitch": m(4.47)}
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        top, src = registry.face_top_source(b2, far)
    assert top == pytest.approx(71.52) and src == "floor_count×floor_pitch"
    # 何も無い時だけ 20 m と警告
    with pytest.warns(UserWarning, match="仮に 20 m"):
        assert registry.face_top({"id": "t"}, far) == 20.0
    # 既存の呼び出し元（face_rect_frame）にも入る
    assert registry.face_rect_frame(b, FACE)["top_z"] == 23.8
