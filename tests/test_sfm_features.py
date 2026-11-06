"""register-sfm の特徴点の方式：ALIKED＋LightGlue の照合が通って DB に書けること、RootSIFT の既定が壊れていないこと。"""

import inspect
from pathlib import Path

import numpy as np
import pytest

from recorder import sfm


def _pair_images():
    """合成の 2 枚：ぼかした乱数の模様＋図形の画像と、それを少し回して寄せた画像（平面の射影変換）。"""
    import cv2

    rng = np.random.default_rng(1)
    h, w = 600, 800
    base = rng.integers(0, 255, (h // 8, w // 8), dtype=np.uint8)
    img = cv2.resize(base, (w, h), interpolation=cv2.INTER_CUBIC)
    for _ in range(60):
        c = (int(rng.integers(0, w)), int(rng.integers(0, h)))
        cv2.circle(img, c, int(rng.integers(5, 40)), int(rng.integers(0, 255)), -1)
        p = (int(rng.integers(0, w)), int(rng.integers(0, h)))
        cv2.rectangle(img, p, (p[0] + int(rng.integers(10, 60)), p[1] + int(rng.integers(10, 60))), int(rng.integers(0, 255)), 2)
    img = cv2.GaussianBlur(img, (3, 3), 0)
    H = cv2.getRotationMatrix2D((w / 2, h / 2), 6.0, 1.08)
    H = np.vstack([H, [0, 0, 1]])
    H[0, 2] += 25
    img2 = cv2.warpPerspective(img, H, (w, h), borderMode=cv2.BORDER_REFLECT)
    to_bgr = lambda g: cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)  # noqa: E731
    return {"a.jpg": to_bgr(img), "b.jpg": to_bgr(img2)}


def _db_run(tmp_path: Path, feats: dict, write_descriptors: bool, matcher):
    items = [{"key": k, "name": k, "w": feats[k][2][0], "h": feats[k][2][1], "focal": 800.0} for k in ("a.jpg", "b.jpg")]
    db = tmp_path / "database.db"
    ids = sfm.build_database(db, items, feats, write_descriptors=write_descriptors)
    names = {k: k for k in ids}
    left = matcher(db, [("a.jpg", "b.jpg")], names, ids)
    assert left == 0
    return sfm.pair_stats(db, [("a.jpg", "b.jpg")], ids)


@pytest.mark.skipif(not sfm.aliked_available(), reason="torch / lightglue が入っていない")
def test_aliked_lightglue_matches_and_writes_db(tmp_path):
    imgs = _pair_images()
    alg = sfm.AlikedLightGlue(tmp_path / "feat", loader=lambda p: imgs[Path(p).name], max_kp=1024, max_side=800, log=lambda *a: None)
    al = {k: alg.feats(k, Path(k)) for k in imgs}
    assert all(len(d["kp"]) > 100 for d in al.values())
    assert (tmp_path / "feat" / "a.npz").exists()
    m = alg.match(al["a.jpg"], al["b.jpg"])
    assert m.shape[1] == 2 and len(m) > 50
    feats = {k: (d["pts"], d["des"], d["size"]) for k, d in al.items()}
    st = _db_run(tmp_path, feats, False,
                 lambda db, pairs, names, ids: sfm.match_pairs_lightglue(db, pairs, names, ids, tmp_path, None, alg, al,
                                                                         log=lambda *a: None))
    assert st["n_verified"] == 1 and st["median_inliers_verified"] > 50
    # キャッシュから読み直しても同じ点
    alg2 = sfm.AlikedLightGlue(tmp_path / "feat", loader=lambda p: (_ for _ in ()).throw(AssertionError("読まない")), log=lambda *a: None)
    assert np.allclose(alg2.feats("a.jpg", Path("a.jpg"))["pts"], al["a.jpg"]["pts"])


def test_rootsift_default_unchanged(tmp_path):
    assert inspect.signature(sfm.register_sfm).parameters["features"].default == "rootsift"
    assert inspect.signature(sfm.register_models).parameters["features"].default == "rootsift"
    assert sfm.FEATURES[0] == "rootsift"
    from recorder.chain import FeatureCache

    imgs = _pair_images()
    cache = FeatureCache(None, loader=lambda p: imgs[Path(p).name])
    feats = {k: cache.get(k, Path(k)) for k in imgs}
    st = _db_run(tmp_path, feats, True,
                 lambda db, pairs, names, ids: sfm.match_pairs(db, pairs, names, ids, tmp_path, None, log=lambda *a: None))
    assert st["n_verified"] == 1


def test_register_sfm_rejects_unknown_features(tmp_path):
    with pytest.raises(ValueError):
        sfm.register_sfm(Path("data/buildings/none.json"), {"entries": []}, {"photos": []}, features="superpoint")


def test_align_rejects_flipped_orientation():
    """位置は合うが向きが裏返し（11/6 の本部棟の実験：残差 0.33 m で向きが 176° 違う）なら合わせない。"""
    rng = np.random.default_rng(3)
    sfm_imgs, anchors, flipped = {}, {}, {}
    Rz = np.array([[-1.0, 0, 0], [0, -1.0, 0], [0, 0, 1.0]])           # 鉛直軸まわりに 180°
    for i in range(4):
        C = rng.normal(size=3) * 5
        R = np.eye(3)
        t = -R @ C
        sfm_imgs[f"p{i}"] = {"R": R, "t": t}
        anchors[f"p{i}"] = {"R_cv": R.tolist(), "t_cv": t.tolist()}
        Rf = R @ Rz
        flipped[f"p{i}"] = {"R_cv": Rf.tolist(), "t_cv": (-Rf @ C).tolist()}
    ok = sfm.align(sfm_imgs, anchors)
    assert ok["ok"] and max(ok["rot_deg"].values()) < 1
    bad = sfm.align(sfm_imgs, flipped)
    assert not bad["ok"] and bad["code"] == "rot" and bad["rms_m"] < 0.01
