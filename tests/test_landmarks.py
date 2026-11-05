"""目印の対応で登録する landmarks のテスト（升の変換・目印の一覧・PnP）。写真と台帳は触らない。"""
import math

import numpy as np
import pytest

from recorder import landmarks as LM
from recorder import locate
from recorder.registry import K_matrix
from recorder.schema import load_building


def test_parse_cell_and_rect():
    assert LM.parse_cell("C7") == (2, 6) and LM.parse_cell(" j10 ") == (9, 9)
    with pytest.raises(ValueError):
        LM.parse_cell("K3")
    with pytest.raises(ValueError):
        LM.parse_cell("C11")
    x0, y0, x1, y1 = LM.cell_rect("A1", 1000, 500)
    assert (x0, y0, x1, y1) == (0.0, 0.0, 100.0, 50.0)
    x0, y0, x1, y1 = LM.cell_rect("A1", 1000, 500, expand=0.25)
    assert (x0, y0) == (0.0, 0.0) and x1 == 125.0 and y1 == 62.5          # 枠の外には広げない


def test_pick_to_px_precision():
    w, h = 4000, 3000
    u, v, how = LM.pick_to_px({"id": "x", "cell": "C7"}, w, h)
    assert how == "cell" and u == 1000.0 and v == 1950.0
    u2, v2, how2 = LM.pick_to_px({"id": "x", "cell": "C7", "sub": "A1"}, w, h)
    assert how2 == "cell+sub"
    # 寄りの 1 升は元の 0.15 升＝幅の 1.5 %
    u3, _, _ = LM.pick_to_px({"id": "x", "cell": "C7", "sub": "B1"}, w, h)
    assert abs((u3 - u2) - 0.015 * w) < 1e-6
    assert LM.pick_to_px({"id": "x", "u": 12.5, "v": 7}, w, h) == (12.5, 7.0, "px")


def test_catalog_from_fixture():
    b = load_building("tests/fixtures/parts_building.json")
    cat = LM.catalog(b)
    assert cat and all(len(m["xyz"]) == 3 for m in cat)
    ids = [m["id"] for m in cat]
    assert len(ids) == len(set(ids))
    assert all("×" in m["name"] for m in cat)
    # 同じ面の目印は面の平面上（from→to の線上）にある
    f = b["faces"][0]
    mine = [m for m in cat if m["face"] == f["id"]]
    p0 = np.array(f["from"], float); d = np.array(f["to"], float) - p0; n = np.array([d[1], -d[0]]) / np.linalg.norm(d)
    assert all(abs((np.array(m["xyz"][:2]) - p0) @ n) < 1e-6 for m in mine)


def test_solve_recovers_camera_with_cell_quantization():
    b = load_building("tests/fixtures/parts_building.json")
    cat = LM.catalog(b)
    f = b["faces"][0]
    p0 = np.array(f["from"], float); d = np.array(f["to"], float) - p0; L = np.linalg.norm(d); u = d / L; n = np.array([u[1], -u[0]])
    C = np.r_[p0 + u * L / 2 + n * 18.0, 1.6]                   # 面の前 18 m、中央、目の高さ
    yaw = math.degrees(math.atan2(-n[0], -n[1])) % 360          # 面を見る
    R = locate.rotation_cv(yaw, 12.0, 1.0)
    w, h, focal = 4000, 3000, 2600.0
    K = K_matrix(focal, w, h)
    P = np.array([m["xyz"] for m in cat if m["face"] == f["id"]])
    X = (P - C) @ R.T
    uv = np.c_[focal * X[:, 0] / X[:, 2] + w / 2, focal * X[:, 1] / X[:, 2] + h / 2]
    ok = (X[:, 2] > 1) & (uv[:, 0] > 0.05 * w) & (uv[:, 0] < 0.95 * w) & (uv[:, 1] > 0.05 * h) & (uv[:, 1] < 0.95 * h)
    assert ok.sum() >= 4
    # 升＋寄りの升に丸める（係が正しく指した時の精度）
    picks = []
    sel = np.nonzero(ok)[0]
    sel = sel[np.linspace(0, len(sel) - 1, min(6, len(sel))).round().astype(int)]      # 左右に散らす
    for (uu, vv) in uv[sel]:
        col, row = min(9, int(uu / w * 10)), min(9, int(vv / h * 10))
        cell = f"{LM.COLS[col]}{row + 1}"
        x0, y0, x1, y1 = LM.cell_rect(cell, w, h, expand=LM.CROP_EXPAND)
        sc, sr = min(9, int((uu - x0) / (x1 - x0) * 10)), min(9, int((vv - y0) / (y1 - y0) * 10))
        picks.append(LM.pick_to_px({"cell": cell, "sub": f"{LM.COLS[sc]}{sr + 1}"}, w, h)[:2])
    sol = LM.solve(P[sel], np.array(picks), w, h, focal0=focal * 1.03)
    Cp = -sol["R"].T @ sol["t"]
    assert math.hypot(Cp[0] - C[0], Cp[1] - C[1]) < 1.2 and abs(Cp[2] - C[2]) < 1.0
    assert sol["rms_px"] < LM.MAX_REPROJ_FRAC * w and abs(sol["focal"] / focal - 1) < 0.1
