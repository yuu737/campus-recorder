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


# --- 柱型の余白（margin_from / margin_to）---------------------------------

def _bays(**kw):
    def m(v, st="estimated"):
        return {"value": v, "status": st, "evidence": [], "method": "test"}
    d = {"count": m(6), "pitch": m(5.0)}
    for k, v in kw.items():
        d[k] = m(v) if v is not None else {"value": None, "status": "unknown", "evidence": [], "method": ""}
    return d


def test_bay_edges_margin_rules():
    from recorder.schema import bay_edges
    # 長さ 36 = 6×5 + 余り 6
    e, _ = bay_edges(_bays(margin_from=3.0), 36.0)
    assert e[0] == 3.0 and e[-1] == 33.0
    e, _ = bay_edges(_bays(margin_from=None, margin_to=0.0), 36.0)    # to だけ → 余りを from 側へ
    assert abs(e[0] - 6.0) < 1e-9
    e, w = bay_edges(_bays(margin_from=None, margin_to=None), 36.0)  # 両方 unknown → 等分
    assert abs(e[0] - 3.0) < 1e-9 and w
    e, _ = bay_edges(_bays(end_margin=1.5), 36.0)                    # 従来名
    assert e[0] == 1.5


def test_compare_face_lines_follow_margin():
    b = _face_json()
    f = b["faces"][0]
    f["length"] = {"value": 36.0, "status": "estimated", "evidence": [], "method": "t"}
    f["bays"].pop("end_margin")
    f["bays"]["margin_from"] = {"value": None, "status": "unknown", "evidence": [], "method": ""}
    f["bays"]["margin_to"] = {"value": 0.0, "status": "estimated", "evidence": [], "method": "t"}
    xs, _ = compare.face_lines(b, "S")
    assert abs(xs[0][1] - (6.0 - 0.4)) < 1e-6                         # 柱型0左 = 余り 6 − pier/2


def test_drawing_uses_same_edges():
    from recorder import drawing
    b = _face_json()
    b["faces"][0]["length"] = {"value": 30.0, "status": "estimated", "evidence": [], "method": "t"}
    assert "<svg" in drawing.elevation_svg(b, "S")


# --- 黒い余白のマスク・--lines・auto ---------------------------------------

def test_black_margin_edge_not_picked_and_given_lines_win():
    b = _face_json()
    img = _synthetic(b)
    H, W = img.shape[:2]
    big = np.zeros((H, W + 400, 3), np.uint8)       # 左に黒い余白を足す（正面化の外側）
    big[:, 400:] = img
    box = [460, 100, big.shape[1] - 60, H - 30]
    res = compare.compare(big, b, "S", wall_box=box, tol_m=0.1)
    assert res.ok and res.valid_ratio < 1.0
    xs_det = [c["px"] for c in res.candidates["x"]]
    assert all(abs(p - 400) > 12 for p in xs_det)                    # 余白の縁（x=400）を線として拾わない
    assert all("strength" in c and c["auto"] is True for c in res.candidates["x"])
    assert all(l.auto for l in res.lines)
    # 判断役の線
    pier0_l = 400 + 120 - 0.4 * 40
    res2 = compare.compare(big, b, "S", wall_box=box, tol_m=0.1, px_per_m=40.0,
                           given_lines={"x_named": {"柱型0左": pier0_l, "柱型0右": pier0_l + 32}})
    assert abs(res2.x_origin_px - (400 + 120)) < 1.0
    assert all(l.auto is False for l in res2.lines if l.axis == "x")


# --- overlay の出力先 ------------------------------------------------------

def test_overlay_default_out_name(tmp_path, monkeypatch):
    import cv2
    from recorder import __main__ as cli
    monkeypatch.chdir(tmp_path)
    cv2.imwrite(str(tmp_path / "p1.jpg"), np.full((40, 60, 3), 100, np.uint8))
    (tmp_path / "out" / "bld").mkdir(parents=True)
    cv2.imwrite(str(tmp_path / "out" / "bld" / "view_photo_p1.png"), np.full((40, 60, 3), 200, np.uint8))
    assert cli.main(["photos", "overlay", "p1.jpg", "--render", "out/bld/view_photo_p1.png"]) == 0
    assert (tmp_path / "out" / "bld" / "overlay_p1.png").exists()
    assert cli.main(["photos", "overlay", "p1.jpg", "--render", "out/bld/view_photo_p1.png", "--out", "x/y.png"]) == 0
    assert (tmp_path / "x" / "y.png").exists()
