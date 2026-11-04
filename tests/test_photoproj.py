"""photoproj：写真の投影と食い違い（Blender を使わない合成。箱 1 つと 2 視点の幾何計算）。"""

from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
import pytest

from recorder import photoproj as pp

W, H, F = 320, 240, 300.0


def look_at(eye, target) -> pp.Cam:
    eye, target = np.asarray(eye, float), np.asarray(target, float)
    fwd = target - eye
    fwd /= np.linalg.norm(fwd)
    right = np.cross(fwd, [0, 0, 1.0])
    right /= np.linalg.norm(right)
    down = np.cross(fwd, right)
    R = np.stack([right, down, fwd])
    assert abs(np.linalg.det(R) - 1) < 1e-9
    return pp.Cam(R, -R @ eye, F, W, H)


def raycast_box(cam: pp.Cam, lo, hi, size=(W, H)) -> tuple[np.ndarray, np.ndarray]:
    """軸に平行な箱を描く（P と ID。ID は 1:−x 2:+x 3:−y 4:+y 5:−z 6:+z）。"""
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    o, d = pp.pixel_rays(cam, size)
    with np.errstate(divide="ignore", invalid="ignore"):
        t1, t2 = (lo - o) / d, (hi - o) / d
    tn, tf = np.minimum(t1, t2), np.maximum(t1, t2)
    tmin, tmax = tn.max(axis=-1), tf.min(axis=-1)
    axis = tn.argmax(axis=-1)
    hit = (tmax >= tmin) & (tmin > 0)
    P = o + d * tmin[..., None]
    P[~hit] = np.nan
    neg = np.take_along_axis(d, axis[..., None], -1)[..., 0] > 0          # 光線が + 向き＝− 側の面に当たる
    ID = np.where(hit, 1 + 2 * axis + (~neg).astype(int), 0)
    return P, ID


def texture(P: np.ndarray) -> np.ndarray:
    """箱の表面の模様（世界座標の縞。灰色 0..255）。何も無い所は 30。"""
    g = 127 + 45 * np.sign(np.sin(2 * np.pi * (P[..., 0] + P[..., 1]) / 0.8)) + 45 * np.sign(np.sin(2 * np.pi * P[..., 2] / 0.6))
    return np.where(np.isfinite(g), g, 30.0).astype(np.float32)


LO, HI = (-2, -2, 0), (2, 2, 4)
A_EYE, B_EYE = (-3.0, -10.0, 2.0), (4.0, -10.0, 2.5)
LABELS = {1: "face:W", 2: "face:E", 3: "face:S", 4: "face:N", 5: "face:bottom", 6: "face:top"}


def view(cam, lo=LO, hi=HI, name="v") -> pp.View:
    P, ID = raycast_box(cam, lo, hi)
    return pp.View(name, cam, (W, H), P, ID)


def photo(cam, model_lo=LO, model_hi=HI, name="A") -> pp.Photo:
    """写真は本当の箱（LO, HI）から、模型の描画は model の箱から。"""
    Ptrue, _ = raycast_box(cam, LO, HI)
    return pp.Photo(name, name, [], view(cam, model_lo, model_hi, name), texture(Ptrue), np.zeros((H, W), bool))


def test_project_roundtrip():
    cam = look_at(A_EYE, (0, 0, 2))
    v = view(cam)
    u, w, d = pp.project(cam, np.nan_to_num(v.P), (W, H))
    gu, gv = np.meshgrid(np.arange(W) + 0.5, np.arange(H) + 0.5)
    assert v.hit.sum() > 5000
    assert np.abs(u - gu)[v.hit].max() < 1e-6 and np.abs(w - gv)[v.hit].max() < 1e-6
    assert (d[v.hit] > 0).all()


def test_project_scaled_size_matches_full():
    cam = look_at(A_EYE, (0, 0, 2))
    X = np.array([[0.5, -2.0, 1.3]])
    u1, v1, _ = pp.project(cam, X, (W, H))
    u4, v4, _ = pp.project(cam, X, (W // 4, H // 4))
    assert u4[0] == pytest.approx(u1[0] / 4) and v4[0] == pytest.approx(v1[0] / 4)


def test_blender_view_matches_cv_camera():
    cam = look_at(A_EYE, (0, 0, 2))
    bv = pp.blender_view(cam, (W, H), "A")
    M = np.array(bv["matrix_world"])
    assert np.allclose(M[:3, 3], A_EYE)
    fwd_bl = -M[:3, 2]                                    # Blender のカメラは −Z が前
    assert np.allclose(fwd_bl, cam.R.T @ [0, 0, 1])
    up_bl = M[:3, 1]
    assert np.allclose(up_bl, -(cam.R.T @ [0, 1, 0]))      # +Y が上（cv の y は下）
    assert bv["lens_mm"] == pytest.approx(F / W * 36.0)


@pytest.mark.parametrize("up,deg", [(False, 15), (False, -15), (True, 15)])
def test_orbit_keeps_pivot_pixel(up, deg):
    cam = look_at(A_EYE, (0, 0, 2))
    pivot = np.array([0.3, -2.0, 1.7])
    c2 = pp.orbit(cam, pivot, deg, up)
    u1, v1, d1 = pp.project(cam, pivot[None], (W, H))
    u2, v2, d2 = pp.project(c2, pivot[None], (W, H))
    assert u2[0] == pytest.approx(u1[0]) and v2[0] == pytest.approx(v1[0]) and d2[0] == pytest.approx(d1[0])
    assert np.linalg.norm(c2.center - pivot) == pytest.approx(np.linalg.norm(cam.center - pivot))
    if up:
        assert c2.center[2] > cam.center[2]
    else:
        a, b = (cam.center - pivot)[:2], (c2.center - pivot)[:2]
        ang = math.degrees(math.atan2(a[0] * b[1] - a[1] * b[0], a @ b))
        assert ang == pytest.approx(deg, abs=1e-6)
        assert c2.center[2] == pytest.approx(cam.center[2])


def test_visibility_uses_depth_and_facing():
    a = view(look_at(A_EYE, (0, 0, 2)), name="A")
    b = view(look_at((10.0, 3.0, 2.0), (0, 0, 2)), name="B")      # B は東から：東の面だけ
    ok, _, _ = pp.visible_in(a, b.P, None, b.N)
    east = b.ID == 2
    assert east.sum() > 1000
    assert ok[east].mean() < 0.01                                 # A から東の面は見えない
    ok_s, _, _ = pp.visible_in(b, a.P, None, a.N)
    south = a.ID == 3
    assert ok_s[south].mean() < 0.01                              # B から南の面は見えない（裏・枠の外）
    ok_self, _, _ = pp.visible_in(a, a.P, None, a.N)
    assert ok_self[a.ID == 3].mean() > 0.99


def test_normals_point_to_camera():
    a = view(look_at(A_EYE, (0, 0, 2)))
    n = a.N[a.ID == 3]
    n = n[np.isfinite(n[:, 0])]
    assert np.allclose(np.median(n, axis=0), [0, -1, 0], atol=1e-6)


def test_aggregate_by_id():
    ID = np.array([[1, 1, 2, 2], [1, 1, 2, 0]])
    diff = np.array([[0.1, 0.3, 1.0, np.nan], [0.2, 0.2, 0.5, 9.0]])
    rows = pp.aggregate_by_id(diff, ID, {1: "face:A", 2: "part:x"}, min_px=1)
    by = {r["id"]: r for r in rows}
    assert by["face:A"]["n_px"] == 4 and by["face:A"]["mean"] == pytest.approx(0.2)
    assert by["part:x"]["n_px"] == 2 and by["part:x"]["mean"] == pytest.approx(0.75)
    assert rows[0]["id"] == "part:x"                              # 悪い順
    assert by["face:A"]["p90"] == pytest.approx(np.percentile([0.1, 0.3, 0.2, 0.2], 90))


def test_edge_strength_ignores_brightness():
    g = np.zeros((40, 40), np.float32)
    g[:, 20:] = 100
    e1 = pp.edge_strength(g)
    e2 = pp.edge_strength(g * 0.5 + 30)
    assert np.allclose(e1, e2, atol=1e-4)


def test_correct_model_has_small_discrepancy_and_wrong_face_stands_out():
    ca, cb = look_at(A_EYE, (0, 0, 2)), look_at(B_EYE, (0, 0, 2))
    A, B = photo(ca, name="A"), photo(cb, name="B")
    good = pp.compare_pair(A, B, LABELS, min_px=50)
    gs = {r["id"]: r["mean"] for r in good["ids"]}
    assert gs["face:S"] < 0.12                                   # 形が合っていれば、残るのは画素の取り方の差だけ
    # 南の面を 1 m 手前に出した模型（形が悪い）：同じ写真を貼ると、南の面だけ食い違いが大きくなる
    lo2 = (-2, -3, 0)
    A2, B2 = photo(ca, lo2, HI, "A"), photo(cb, lo2, HI, "B")
    bad = pp.compare_pair(A2, B2, LABELS, min_px=50)
    bs = {r["id"]: r["mean"] for r in bad["ids"]}
    assert bs["face:S"] > 3 * gs["face:S"] and bs["face:S"] > 0.25


def test_rank_ids_averages_directions_and_uses_score():
    pairs = [{"A": "a", "B": "b", "ids": [{"id": "face:X", "kind": "面", "mean": 0.4, "p90": 0.8, "n_px": 100, "score": 2.0},
                                          {"id": "part:p", "kind": "部品", "mean": 0.9, "p90": 1.0, "n_px": 50, "score": 1.0}]},
             {"A": "b", "B": "a", "ids": [{"id": "face:X", "kind": "面", "mean": 0.2, "p90": 0.4, "n_px": 100, "score": 1.0}]},
             {"A": "a", "B": "c", "ids": [{"id": "ground", "kind": "その他", "mean": 2.0, "p90": 2.0, "n_px": 10, "score": 5.0}]}]
    ranking, others = pp.rank_ids(pairs)
    assert [r["id"] for r in ranking] == ["face:X", "part:p"]
    assert ranking[0]["score"] == pytest.approx(1.5) and ranking[0]["directions"] == 2
    assert others[0]["id"] == "ground"


def test_face_cells():
    faces = {"S": {"from": [0, 0], "to": [10, 0]}}
    P = np.zeros((2, 3, 3))
    P[..., 0] = [[1, 5, 9], [1, 5, 9]]
    P[..., 2] = [[1, 1, 1], [4, 4, 4]]
    ID = np.ones((2, 3), int)
    diff = np.array([[0.1, 0.5, 0.2], [0.1, 0.1, 0.1]])
    cells = pp.face_cells(diff, ID, {1: "face:S"}, P, faces, cell=(4.0, 3.0), min_px=1)
    top = cells[0]
    assert top["face"] == "S" and top["s"] == [4.0, 8.0] and top["z"] == [0.0, 3.0] and top["mean"] == pytest.approx(0.5)


def test_exclude_mask_projects_face_rect():
    # 面の (s, z) → 写真 px が u = 100 s, v = 400 − 100 z の登録（写真 800×400）
    Hp = [[100, 0, 0], [0, -100, 400], [0, 0, 1]]
    e = {"image_size": [800, 400], "H_plane": Hp, "exclude": [[1.0, 2.0, 1.0, 3.0]]}
    m = pp.exclude_mask([e], (200, 100))                          # 1/4 の大きさ
    assert m[50, 37] and not m[50, 60] and not m[90, 37]
    assert m.sum() == pytest.approx(25 * 50, rel=0.15)


def test_single_mask_and_parallel_edges_shape():
    ca = look_at(A_EYE, (0, 0, 2))
    A = photo(ca, name="A")
    single = pp.single_mask(A.view, [])
    assert (single == A.view.hit).all()
    e = pp.model_edges(A.view)
    assert e.any()
    par, band = pp.parallel_offset_edges(A.gray, A.view.hit, e, 1.0)
    assert par.shape == A.gray.shape and band.any()


@pytest.mark.skipif(not os.environ.get("BLENDER") or not Path(os.environ.get("BLENDER", "")).exists(), reason="BLENDER が無い")
def test_blender_render_matches_numpy(tmp_path):
    """Blender の --photoproj の描画（P・ID）が numpy の投影と画素の中心で合う（棟 JSON の小さな見本で）。"""
    import json

    fx = Path(__file__).parent / "fixtures"
    cands = sorted(fx.glob("*.json"))
    src = None
    for c in cands:
        try:
            d = json.loads(c.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(d, dict) and d.get("faces") and d.get("footprint"):
            src = c
            break
    if src is None:
        pytest.skip("棟 JSON の見本が無い")
    b = json.loads(src.read_text(encoding="utf-8"))
    xs = [p[0] for p in b["footprint"]["polygon"]] if isinstance(b["footprint"], dict) else [p[0] for p in b["footprint"]]
    ys = [p[1] for p in b["footprint"]["polygon"]] if isinstance(b["footprint"], dict) else [p[1] for p in b["footprint"]]
    c = np.array([np.mean(xs), np.mean(ys), 5.0])
    cam = look_at(c + np.array([0, -60.0, 0]), c)
    size = (160, 120)
    pp.run_blender(src, {"views": [pp.blender_view(cam, size, "t")]}, tmp_path)
    v = pp.load_view(tmp_path, "t", cam, size)
    if not v.hit.any():
        pytest.skip("見本の棟が写らない")
    u, w, _ = pp.project(cam, np.nan_to_num(v.P), size)
    gu, gv = np.meshgrid(np.arange(size[0]) + 0.5, np.arange(size[1]) + 0.5)
    assert np.percentile(np.hypot(u - gu, w - gv)[v.hit], 99) < 0.75


def test_edge_support_drops_when_model_edge_has_no_photo_edge():
    ca = look_at(A_EYE, (0, 0, 2))
    good = {r["id"]: r["support"] for r in pp.edge_support(photo(ca, name="A"), LABELS, low=1.01, min_px=10)}
    bad = {r["id"]: r["support"] for r in pp.edge_support(photo(ca, (-2, -2, 0), (2, 2, 5.5), "A"), LABELS, low=1.01, min_px=10)}
    # 模型の箱を 1.5 m 高くした：南の面の上の縁（空との境）に写真の縁が無くなる
    assert good["face:S"] > 0.8
    assert bad["face:S"] < good["face:S"] - 0.1
