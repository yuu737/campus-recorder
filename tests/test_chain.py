"""鎖の登録（recorder.chain）：合成の箱（面 4 枚、うち 2 枚を plane で登録済み）を、角を写した写真で鎖に登録する。"""

from __future__ import annotations

import json
import math

import cv2
import numpy as np
import pytest

from recorder import chain, coverage, registry

W, H, F = 1200, 900, 1000.0
TOP = 10.0
FACES = [{"id": "S", "from": [0.0, 0.0], "to": [20.0, 0.0]},       # 外向き 南
         {"id": "E", "from": [20.0, 0.0], "to": [20.0, 12.0]},     # 外向き 東
         {"id": "N", "from": [20.0, 12.0], "to": [0.0, 12.0]},
         {"id": "W", "from": [0.0, 12.0], "to": [0.0, 0.0]}]
F35 = F * 43.27 / math.hypot(W, H)         # pose.focal_px_from_35mm の逆


def look_at(C, target):
    C, target = np.asarray(C, float), np.asarray(target, float)
    fwd = target - C
    fwd /= np.linalg.norm(fwd)
    right = np.cross(fwd, [0, 0, 1.0])
    right /= np.linalg.norm(right)
    down = np.cross(fwd, right)
    R = np.stack([right, down, fwd])
    return R, -R @ C


def texture(seed: int, L: float, ppm: float = 40.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    w, h = int(L * ppm), int(TOP * ppm)
    t = cv2.resize(rng.uniform(40, 215, (h // 8, w // 8)).astype(np.uint8), (w, h), interpolation=cv2.INTER_CUBIC)
    for _ in range(int(L * 12)):
        c = (int(rng.integers(0, w)), int(rng.integers(0, h)))
        col = int(rng.integers(0, 255))
        if rng.random() < 0.5:
            cv2.circle(t, c, int(rng.integers(3, 14)), col, -1)
        else:
            cv2.rectangle(t, c, (c[0] + int(rng.integers(4, 24)), c[1] + int(rng.integers(4, 24))), col, -1)
    return t


TEX = {f["id"]: texture(i + 1, registry.face_frame(f)[3]) for i, f in enumerate(FACES)}


def render(R, t) -> np.ndarray:
    """面に模様を貼った箱を写真に描く（手前の面が上。凸な箱なので面どうしは重ならない）。"""
    img = np.full((H, W), 128, np.uint8)
    C = -R.T @ t
    for f in FACES:
        p0, _, nv, L = registry.face_frame(f)
        if (C[:2] - p0) @ nv <= 0:
            continue
        Hp = registry.H_from_pose(R, t, F, W, H, f)
        ppm = 40.0
        A = np.array([[1 / ppm, 0, 0], [0, -1 / ppm, TOP], [0, 0, 1]], float)
        M = Hp @ A
        tex = TEX[f["id"]]
        warped = cv2.warpPerspective(tex, M, (W, H), flags=cv2.INTER_LINEAR)
        mask = cv2.warpPerspective(np.full(tex.shape, 255, np.uint8), M, (W, H), flags=cv2.INTER_NEAREST)
        img[mask > 0] = warped[mask > 0]
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


VIEWS = {"viaS": look_at([10.0, -16.0, 1.5], [10.0, 0.0, 5.0]),
         "viaE": look_at([36.0, 6.0, 1.5], [20.0, 6.0, 5.0]),
         "corner": look_at([31.0, -13.0, 1.6], [19.0, 1.0, 4.5]),
         "north": look_at([10.0, 30.0, 1.5], [10.0, 12.0, 5.0])}


@pytest.fixture()
def world(tmp_path):
    b = {"id": "box", "parapet_top": {"value": TOP, "status": "measured", "evidence": ["t"]}, "faces": FACES}
    bj = tmp_path / "box.json"
    bj.write_text(json.dumps(b), encoding="utf-8")
    idx = {"photos": []}
    for k, (R, t) in VIEWS.items():
        p = tmp_path / f"{k}.png"
        cv2.imwrite(str(p), render(R, t))
        idx["photos"].append({"id": k, "building": "box", "face": "corner" if k == "corner" else k[-1], "file": str(p),
                              "focal_35mm": F35, "period": "during"})
    reg = registry.load_registry(tmp_path / "reg.json")
    for k, fid in (("viaS", "S"), ("viaE", "E")):
        R, t = VIEWS[k]
        f = next(x for x in FACES if x["id"] == fid)
        fr = registry.face_rect_frame(b, f)
        registry.upsert(reg, {"photo": k, "building": "box", "face": fid, "period": "during", "file": str(tmp_path / f"{k}.png"),
                              "image_size": [W, H], "H_plane": registry.H_from_pose(R, t, F, W, H, fr).tolist(),
                              "face_frame": fr, "camera": registry.camera_dict(R, t, F, W, H, 0.0)})
    cache = chain.FeatureCache(None)
    return b, bj, idx, reg, cache


def reproj_vs_truth(cam: dict, R, t) -> float:
    pts = np.array([[x, y, z] for x in (0, 10, 20) for y in (0, 6, 12) for z in (0, 5, 10)], float)
    u, v, _ = registry.project_world(cam, pts)
    K = registry.K_matrix(F, W, H)
    X = pts @ R.T + t
    q = X @ K.T
    uu, vv = q[:, 0] / q[:, 2], q[:, 1] / q[:, 2]
    ok = (X[:, 2] > 0) & (uu > 0) & (uu < W) & (vv > 0) & (vv < H)
    return float(np.hypot(u - uu, v - vv)[ok].max())


def test_chain_recovers_corner_view(world):
    b, bj, idx, reg, cache = world
    e = chain.register_via("corner", bj, ["viaS", "viaE"], reg, idx, cache)
    R, t = VIEWS["corner"]
    assert e["kind"] == "pose" and e["face"] == "corner"
    assert e["check"]["reproj_rms_px"] <= 1.0 and e["check"]["n_points"] >= chain.MIN_POINTS
    assert reproj_vs_truth(e["camera"], R, t) < 1.0                         # 鎖の視点が真値に戻る
    assert np.allclose(e["camera"]["camera_xyz"], [31.0, -13.0, 1.6], atol=0.1)
    assert {v["face"] for v in e["visible_faces"]} == {"S", "E"}
    assert {k.split(":")[0] for k in e["check"]["points_by_face"]} == {"viaS", "viaE"}   # 2 枚の違う面の点を使った
    assert abs(e["check"]["camera_height_m"] - 1.6) < 0.1


def test_pose_entry_expands_to_H_plane(world):
    b, bj, idx, reg, cache = world
    R, t = VIEWS["corner"]
    cam = registry.camera_dict(R, t, F, W, H, 0.0)
    e = {"photo": "corner", "building": "box", "face": "corner", "kind": "pose", "file": "x", "image_size": [W, H],
         "camera": cam, "visible_faces": registry.visible_faces(b, cam)}
    assert {v["face"] for v in e["visible_faces"]} == {"S", "E"}
    ex = registry.expand_entries([e, reg["entries"][0]], b)
    pf = {x["face"]: x for x in ex if x.get("kind") == "pose_face"}
    assert set(pf) == {"S", "E"} and any(x.get("kind") is None for x in ex)       # plane の entry はそのまま
    for fid, x in pf.items():
        f = next(q for q in FACES if q["id"] == fid)
        Ht = registry.H_from_pose(R, t, F, W, H, f)
        s, z = np.meshgrid(np.linspace(0, registry.face_frame(f)[3], 5), np.linspace(0, TOP, 4))
        u1, v1, _ = registry.apply_H(x["H_plane"], s, z)
        u2, v2, _ = registry.apply_H(Ht, s, z)
        assert np.allclose(u1, u2) and np.allclose(v1, v2)
        assert x["cover"]["area_m2"] > 2.0
    # coverage が pose の登録を面の被覆に数える
    reg2 = {"entries": [e], "not_registered": []}
    res, _ = coverage.coverage(bj, reg2, bj.parent / "cov", photo=False)
    got = {r["face"]: r["coverage_pct"] for r in res["faces"]}
    assert got["S"] > 0 and got["E"] > 0 and got["N"] == 0


def test_quality_thresholds(world, monkeypatch):
    b, bj, idx, reg, cache = world
    with pytest.raises(chain.ChainFail) as ei:               # 北を写した写真は登録済みの面と重ならない
        chain.register_via("north", bj, ["viaS", "viaE"], reg, idx, cache)
    assert ei.value.code in ("few_matches", "few_inliers", "pnp_fail")
    monkeypatch.setattr(chain, "MAX_REPROJ_PX", 1e-4)        # 誤差の閾値
    with pytest.raises(chain.ChainFail) as ei:
        chain.register_via("corner", bj, ["viaS", "viaE"], reg, idx, cache)
    assert ei.value.code == "reproj" and "px" in str(ei.value)
    monkeypatch.setattr(chain, "MAX_REPROJ_PX", 3.0)
    monkeypatch.setattr(chain, "MIN_POINTS", 10 ** 6)       # 点の数の閾値
    with pytest.raises(chain.ChainFail) as ei:
        chain.register_via("corner", bj, ["viaS", "viaE"], reg, idx, cache)
    assert ei.value.code in ("few_matches", "few_inliers")


def test_register_chain_loop(world):
    b, bj, idx, reg, cache = world
    logs = []
    res = chain.register_chain(bj, reg, idx, cache, log=logs.append)
    assert "corner" in res["rounds"][0]["photos"] and res["added"] == 1
    assert "north" in res["failed"]
    assert any(n["photo"] == "north" and n.get("chain_code") for n in reg["not_registered"])
