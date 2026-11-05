"""SfM の登録（recorder.sfm）：合成の箱の周りの 8 視点。SfM の座標（縮尺・回転・並進がずれた座標）の視点を、
登録済みの 4 枚に合わせて世界座標に戻す（相似変換の復元）と、品質の止め。COLMAP そのものは回さない。"""

from __future__ import annotations

import math

import numpy as np
import pytest

from recorder import registry, sfm

W, H, F = 1200, 900, 1000.0
TOP = 10.0
FACES = [{"id": "S", "from": [0.0, 0.0], "to": [20.0, 0.0]},
         {"id": "E", "from": [20.0, 0.0], "to": [20.0, 12.0]},
         {"id": "N", "from": [20.0, 12.0], "to": [0.0, 12.0]},
         {"id": "W", "from": [0.0, 12.0], "to": [0.0, 0.0]}]
B = {"id": "box", "parapet_top": {"value": TOP, "status": "measured", "evidence": ["t"]}, "faces": FACES}


def look_at(C, target):
    C, target = np.asarray(C, float), np.asarray(target, float)
    fwd = target - C
    fwd /= np.linalg.norm(fwd)
    right = np.cross(fwd, [0, 0, 1.0])
    right /= np.linalg.norm(right)
    down = np.cross(fwd, right)
    R = np.stack([right, down, fwd])
    return R, -R @ C


def views() -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """箱（20×12 m）の周りを 1 周する 8 視点（高さ 1.4〜1.8 m、箱の中心を見る）。"""
    out = {}
    for i in range(8):
        a = 2 * math.pi * i / 8
        C = [10 + 26 * math.cos(a), 6 + 20 * math.sin(a), 1.4 + 0.05 * i]
        out[f"v{i}"] = look_at(C, [10.0, 6.0, 4.0])
    return out


# 世界 = s·Rs·SfM + T（SfM の座標は縮尺 1/3、向き・原点も勝手）
S0 = 2.7
RS0 = np.array([[0.36, 0.48, -0.8], [-0.8, 0.6, 0.0], [0.48, 0.64, 0.6]])
T0 = np.array([3.0, -7.0, 1.2])


def to_sfm(R, t):
    C = -R.T @ t
    Cs = RS0.T @ (C - T0) / S0
    Rsf = R @ RS0
    return Rsf, -Rsf @ Cs


def make(anchor_ids=("v0", "v2", "v4", "v6"), tweak=None):
    vs = views()
    reg = {"entries": [], "not_registered": []}
    idx = {"photos": [{"id": k, "building": "box", "face": "(pose)", "file": f"{k}.jpg", "period": "during"} for k in vs]}
    for k in anchor_ids:
        R, t = vs[k]
        registry.upsert(reg, {"photo": k, "building": "box", "face": "(pose)", "kind": "pose", "period": "during",
                              "file": f"{k}.jpg", "image_size": [W, H], "camera": registry.camera_dict(R, t, F, W, H, 0.0),
                              "visible_faces": [{"face": "S"}]})
    imgs = {}
    for k, (R, t) in vs.items():
        Rs, ts = to_sfm(R, t)
        imgs[k] = {"R": Rs, "t": ts, "focal_px": F, "w": W, "h": H, "n_points3d": 400, "reproj_rms_px": 0.8}
    if tweak:
        tweak(imgs, reg)
    return vs, reg, idx, [{"id": 0, "images": imgs, "n_points3d": 3000}]


def test_umeyama_recovers_similarity():
    rng = np.random.default_rng(1)
    src = rng.normal(size=(8, 3))
    dst = (S0 * (RS0 @ src.T)).T + T0
    s, R, T = sfm.umeyama(src, dst)
    assert abs(s - S0) < 1e-9 and np.allclose(R, RS0) and np.allclose(T, T0)


def test_align_and_register_8_views():
    vs, reg, idx, models = make()
    res = sfm.register_models(models, B, reg, idx, None)
    al = res["aligns"][0]
    assert al["ok"] and al["rms_m"] < 0.01 and max(al["residual_m"].values()) < 0.01
    got = {e["photo"]: e for e in res["added"]}
    assert set(got) == {"v1", "v3", "v5", "v7"}
    for k, e in got.items():
        R, t = vs[k]
        C = -R.T @ t
        Cg = -np.array(e["camera"]["R_cv"]).T @ np.array(e["camera"]["t_cv"])
        assert np.linalg.norm(C - Cg) < 0.01
        assert sfm.rot_deg(e["camera"]["R_cv"], R) < 0.01
        assert e["kind"] == "pose" and e["source"]["kind"] == "sfm" and e["visible_faces"]
    # registry.expand_entries が面ごとの H_plane を出せる（既存の pose と同じ形）
    ex = registry.expand_entries([got["v1"]], B)
    assert ex and all(np.asarray(x["H_plane"]).shape == (3, 3) for x in ex)
    R, t = vs["v1"]
    fr = ex[0]["face_frame"]
    Ht = registry.H_from_pose(R, t, F, W, H, fr)
    Hg = np.asarray(ex[0]["H_plane"])
    for s_, z_ in ((1.0, 1.0), (fr["length_m"] - 1, 8.0)):
        a = registry.apply_H(Ht, np.array([s_]), np.array([z_]))
        b = registry.apply_H(Hg, np.array([s_]), np.array([z_]))
        assert math.hypot(a[0][0] - b[0][0], a[1][0] - b[1][0]) < 1.0


def test_bad_anchor_is_dropped():
    def tweak(imgs, reg):
        e = next(e for e in reg["entries"] if e["photo"] == "v2")
        R = np.array(e["camera"]["R_cv"])
        C = -R.T @ np.array(e["camera"]["t_cv"]) + np.array([2.0, 0.0, 0.0])      # 2 m ずれた登録
        e["camera"]["t_cv"] = list(-R @ C)
    vs, reg, idx, models = make(("v0", "v2", "v4", "v6", "v5"), tweak)
    res = sfm.register_models(models, B, reg, idx, None)
    al = res["aligns"][0]
    assert al["ok"] and "v2" in al["dropped"] and al["rms_m"] < 0.01


def test_quality_gates():
    def tweak(imgs, reg):
        imgs["v1"]["reproj_rms_px"] = 5.0
        imgs["v3"]["n_points3d"] = 30
        R, t = imgs["v5"]["R"], imgs["v5"]["t"]           # SfM の座標で 15 m 持ち上げる
        C = -R.T @ t + RS0.T @ np.array([0, 0, 15.0]) / S0
        imgs["v5"]["t"] = -R @ C
    vs, reg, idx, models = make(tweak=tweak)
    res = sfm.register_models(models, B, reg, idx, None)
    assert {e["photo"] for e in res["added"]} == {"v7"}
    codes = {k: c for k, (c, _) in res["failed"].items()}
    assert codes == {"v1": "reproj", "v3": "few_points", "v5": "camera_height"}
    nr = {n["photo"]: n for n in reg["not_registered"]}
    assert nr["v5"]["sfm_code"] == "camera_height" and "高さ" in nr["v5"]["sfm_reason"]


def test_too_few_anchors():
    vs, reg, idx, models = make(("v0", "v4"))
    res = sfm.register_models(models, B, reg, idx, None)
    assert not res["added"] and set(res["reasons"]) == {"few_anchors"}


def test_registered_photos_not_overwritten():
    vs, reg, idx, models = make()
    before = [dict(e) for e in reg["entries"]]
    sfm.register_models(models, B, reg, idx, None)
    for e in before:
        now = next(x for x in reg["entries"] if x["photo"] == e["photo"])
        assert now["camera"] == e["camera"] and "source" not in now


@pytest.mark.parametrize("pid", ["PXL_20261103_044700654", "期間前の写真（削除）"])
def test_ascii_name(pid):
    n = sfm._ascii_name(pid)
    assert n.isascii() and n.endswith(".jpg")
