"""検収（写真と JSON の重ね合わせ）のテスト。人工の壁で、ずれが小さく出ることを確かめる。"""
import numpy as np

from recorder import compare


def _face_json(count=6, pitch=5.0, pier=0.8, floors=((3.0, 5.0), (7.0, 9.0), (11.0, 13.0)), top=15.0):
    def m(v):
        return {"value": v, "status": "estimated", "evidence": [], "method": "test"}
    return {
        "id": "t", "name": "t", "parapet_top": m(top), "floor_pitch": m(4.0),
        "faces": [{"id": "S", "name": "s", "from": [0, 0], "to": [count * pitch, 0],
                   "bays": {"count": m(count), "pitch": m(pitch), "pier_width": m(pier), "end_margin": m(0.0)},
                   "windows": [{"name": f"{i+2}階窓", "z_bottom": m(zb), "z_top": m(zt)} for i, (zb, zt) in enumerate(floors)],
                   "openings": [], "ledges": []}],
    }


def _synthetic(b, ppm=40.0, shift_m=0.0, x_origin=120, ground=760):
    """JSON どおりの壁を描く。窓の上端だけ shift_m ずらしておく（検出できるかの確認用）。"""
    f = b["faces"][0]
    W, H = int(x_origin * 2 + 6 * 5.0 * ppm), ground + 60
    img = np.full((H, W, 3), 210, np.uint8)
    pitch, pier = 5.0, 0.8
    for i in range(7):
        c = x_origin + i * pitch * ppm
        img[:, int(c - pier / 2 * ppm):int(c + pier / 2 * ppm)] = 120
    for w in f["windows"]:
        zb, zt = w["z_bottom"]["value"], w["z_top"]["value"] + shift_m
        yb, yt = int(ground - zb * ppm), int(ground - zt * ppm)
        for i in range(6):
            xa = int(x_origin + i * pitch * ppm + pier / 2 * ppm + 4)
            xb = int(x_origin + (i + 1) * pitch * ppm - pier / 2 * ppm - 4)
            img[yt:yb, xa:xb] = 40
    img[: int(ground - 15.0 * ppm), :] = 235   # パラペットより上は空
    img[ground:, :] = 90                        # 地面
    return img


def test_compare_zero_shift_is_small():
    b = _face_json()
    img = _synthetic(b)
    res = compare.compare(img, b, "S", wall_box=[60, 100, img.shape[1] - 60, img.shape[0] - 30], tol_m=0.1)
    assert res.ok
    assert abs(res.px_per_m - 40.0) / 40.0 < 0.03
    xs = [l for l in res.lines if l.axis == "x" and l.diff_m is not None]
    assert len(xs) >= 8 and max(abs(l.diff_m) for l in xs) < 0.12   # エッジ位置は整数画素（40 px/m → 0.1 m 刻み）
    zs = [l for l in res.lines if l.axis == "z" and l.diff_m is not None]
    assert len(zs) >= 4 and max(abs(l.diff_m) for l in zs) < 0.1


def test_compare_detects_shifted_window_tops():
    b = _face_json()
    img = _synthetic(b, shift_m=0.4)
    res = compare.compare(img, b, "S", wall_box=[60, 100, img.shape[1] - 60, img.shape[0] - 30], tol_m=0.1)
    tops = [l for l in res.lines if l.axis == "z" and "上端" in l.what and "窓" in l.what and l.diff_m is not None]
    bots = [l for l in res.lines if l.axis == "z" and "下端" in l.what and l.diff_m is not None]
    assert tops and all(l.diff_m > 0.25 for l in tops)      # 写真の方が高い → 正のずれ
    assert bots and all(abs(l.diff_m) < 0.1 for l in bots)
