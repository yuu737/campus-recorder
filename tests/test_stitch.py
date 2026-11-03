"""stitch：合成の壁（縦縞＋横縞）を 2 枚に切って重ねを付け、既知のずれを復元できるか。"""

from __future__ import annotations

import json

import cv2
import numpy as np

from recorder import stitch

FLOOR, BAY = 100, 130          # 階高・柱間隔（px）。比 1.3


def wall(w=1600, h=900, random_windows=True, seed=0, every=0):
    """上に空、下に地面、間に 7 階分の壁。柱（縦縞）と床（横縞）、窓の明るさは乱数（random_windows=False なら全部同じ）。"""
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), 170, np.uint8)
    img[:60] = (235, 215, 190)                       # 空
    img[60 + 7 * FLOOR:] = (80, 85, 90)              # 地面
    for f in range(7):
        y = 60 + f * FLOOR
        img[y:y + 12] = 120                          # 床の帯
        for b in range(w // BAY + 1):
            x = b * BAY
            img[y + 12:y + FLOOR, x:x + 18] = 140    # 柱
            v = int(rng.integers(30, 110)) if random_windows else 60
            if every:                                # 窓の明るさが every 柱間ごとに繰り返す（2 柱間ずれても同じに見える）
                v = (40, 100, 70)[b % every]
            img[y + 30:y + 85, x + 32:x + BAY - 14] = v   # 窓
    if not random_windows:
        return img                                   # 完全に周期的な壁（模様の手がかりなし）
    noise = rng.normal(0, 3, img.shape)              # 壁の汚れ（両方の写真に同じく写る）
    return np.clip(img + noise, 1, 255).astype(np.uint8)


def cut(img, s=1.15, seed=1):
    """A は左 900 列、B は 30 行下・600 列右から（正解 dx=600, dy=30）、B は 1.15 倍。
    写真ごとの雑音（センサー）は別々に足す。"""
    rng = np.random.default_rng(seed)
    a = img[0:900, 0:900].astype(np.float64)
    b = img[30:900, 600:1600].astype(np.float64)
    b = cv2.resize(b, (int(round(b.shape[1] * s)), int(round(b.shape[0] * s))), interpolation=cv2.INTER_LINEAR)
    a = np.clip(a + rng.normal(0, 2, a.shape), 1, 255).astype(np.uint8)
    b = np.clip(b + rng.normal(0, 2, b.shape), 1, 255).astype(np.uint8)
    return a, b


def test_recovers_known_shift():
    a, b = cut(wall())
    out, res = stitch.stitch(a, b, hint="右")
    assert res["status"] == "ok", res
    assert abs(res["dx_px"] - 600) <= 2
    assert abs(res["dy_px"] - 30) <= 2
    assert abs(res["scale_b"] - 1 / 1.15) < 0.01
    assert res["second_over_first"] is None or res["second_over_first"] < 0.9
    assert out is not None
    chk = res["check"]
    assert chk["bay_px"] and abs(chk["bay_px"] - BAY) <= 3
    assert chk["ratio_agree"] is True


def test_without_hint_also_recovers():
    a, b = cut(wall(seed=3))
    _, res = stitch.stitch(a, b)
    assert res["status"] == "ok"
    assert abs(res["dx_px"] - 600) <= 2


def test_periodic_wall_is_ambiguous_and_pin_resolves():
    a, b = cut(wall(random_windows=False))
    out, res = stitch.stitch(a, b, hint="右")
    assert res["status"] == "ambiguous"
    assert out is None
    # 元の並びでは柱 1 本ずれた所も同じくらい似る（2 位 ≥ 0.9×1 位）。周期の模様を消すと手がかりが残らない
    assert res["raw"]["second_over_first"] >= 0.9
    assert min(res["aperiodic_fraction"]) < stitch.APERIODIC_MIN
    assert "--pin" in res["reason"]
    # 人が目印を 1 組：A の x=632（柱の左端）は B の拡縮前 x=32
    out, res = stitch.stitch(a, b, pin="A:632=B:32")
    assert res["status"] == "ok"
    assert abs(res["dx_px"] - 600) <= 2
    assert out is not None


def test_two_bay_repeat_is_ambiguous_by_second_peak():
    a, b = cut(wall(every=2))
    out, res = stitch.stitch(a, b, hint="右")
    assert res["status"] == "ambiguous"
    # 2 柱間ずれた所の相関が 1 位の 0.9 倍以上（重なりの広さだけでは決めない）
    assert any(r["ncc"] >= 0.9 * res["ncc"] for r in res["ncc_rivals"])
    assert any(abs(abs(r["dx"] - 600) - 2 * BAY) <= 15 or abs(r["dx"] - 600) <= 15 for r in res["ncc_rivals"] + [{"dx": res["dx_px"]}])
    assert out is None


def test_files(tmp_path):
    a, b = cut(wall(seed=5))
    pa, pb = tmp_path / "a_front.jpg", tmp_path / "b_front.jpg"
    cv2.imwrite(str(pa), a, [cv2.IMWRITE_JPEG_QUALITY, 95])
    cv2.imwrite(str(pb), b, [cv2.IMWRITE_JPEG_QUALITY, 95])
    (tmp_path / "a_front.json").write_text(json.dumps({"wall_box": [0, 0, 900, 900]}), encoding="utf-8")
    res, js = stitch.stitch_files(pa, pb, tmp_path / "x_stitched.jpg", hint="右")
    assert res["status"] == "ok"
    d = json.loads(js.read_text(encoding="utf-8"))
    assert d["A"]["front_json"].endswith("a_front.json")
    assert d["B"]["front_json"] is None
    assert (tmp_path / "x_stitched.jpg").exists()
    assert d["A"]["px_per_floor"] and d["B"]["px_per_floor"]
