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
    # 面の端（landmark）は、この人工の壁では柱の中心に当たり縁が無いので数えない
    xs = [l for l in res.lines if l.axis == "x" and l.diff_m is not None and l.kind == "periodic"]
    assert len(xs) >= 8 and max(abs(l.diff_m) for l in xs) < 0.13   # エッジ位置は整数画素（40 px/m → 0.1 m 刻み）
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


# --- 横の原点：柱の繰り返しの取り違え（11/4 夜、本部棟 南西面で 2 スパン・北東面で 15 m）----------------

def _lattice_wall(n_img_piers, ppm=40.0, x_origin=100.0, ground=700, pitch=5.0, pier=0.8, strip=None):
    """柱が n_img_piers 本並ぶ人工の壁。strip=(a_m, b_m) は 柱の繰り返しとは関係の無い暗い帯（階段塔の面のつもり）。"""
    W, H = int(x_origin * 2 + (n_img_piers - 1) * pitch * ppm), ground + 60
    img = np.full((H, W, 3), 205, np.uint8)
    for i in range(n_img_piers):
        c = x_origin + i * pitch * ppm
        img[:, int(c - pier / 2 * ppm):int(c + pier / 2 * ppm)] = 120
    if strip:
        img[:, int(x_origin + strip[0] * ppm):int(x_origin + strip[1] * ppm)] = 70
    img[: int(ground - 15.0 * ppm), :] = 235
    img[ground:, :] = 90
    return img


def _wall_json(count, pitch=5.0, with_part=None, length=None):
    def m(v):
        return {"value": v, "status": "estimated", "evidence": [], "method": "test"}
    b = {"id": "t", "name": "t", "parapet_top": m(15.0), "floor_pitch": m(4.0),
         "faces": [{"id": "S", "name": "s", "from": [0, 0], "to": [length or count * pitch, 0],
                    "bays": {"count": m(count), "pitch": m(pitch), "pier_width": m(0.8), "margin_from": m(0.4), "margin_to": m(0.4)},
                    "windows": [], "openings": [], "ledges": []}]}
    if length is not None:
        b["faces"][0]["length"] = m(length)
    if with_part:
        a, c = with_part
        b["parts"] = [{"type": "box", "name": "階段塔", "polygon": [[a, 0], [c, 0], [c, 2], [a, 2]], "z0": m(0.0), "z1": m(15.0)}]
    return b


def test_origin_candidates_include_period_multiples_and_flag_ambiguity():
    """柱だけでは何本目か決まらない：写真には JSON より多い柱が写る。候補が周期の整数倍で並び、ambiguous と警告が出る。"""
    b = _wall_json(count=4)                                  # 柱 5 本ぶん（端の余白 0.4 m）
    img = _lattice_wall(n_img_piers=9, x_origin=100.0)       # 写真は柱 9 本
    res = compare.compare(img, b, "S", wall_box=[60, 100, img.shape[1] - 60, img.shape[0] - 30], tol_m=0.1)
    assert res.ok and len(res.origin_candidates) >= 2
    shifts = {round(c["shift_periods"]) for c in res.origin_candidates}
    assert 0 in shifts and len(shifts) >= 2                  # 周期の整数倍ずれた候補が並ぶ
    assert all(abs(c["shift_periods"] - round(c["shift_periods"])) < 0.15 for c in res.origin_candidates[:3])
    assert all("score" in c and "origin_px" in c for c in res.origin_candidates)
    assert res.ambiguous and res.warnings and "原点" in res.warnings[0]


def test_nonperiodic_part_edges_pick_the_right_period():
    """部品（階段塔）の縁という非周期の目印があれば、柱の数え違いを避けて正しい原点を選び、あいまいと言わない。"""
    pitch, ppm, x_origin = 5.0, 40.0, 100.0
    # 実際の面の始点 s=0 は 写真の柱 2 本目（x_origin + 2 周期）。JSON の帯は s=2.0〜3.5（柱の間の壁）
    true_origin = x_origin + 2 * pitch * ppm - 0.4 * ppm * 0     # 柱 2 の中心が s=0.4 → 面の始点は柱 2 の中心から 0.4 m 手前
    true_origin = x_origin + 2 * pitch * ppm - 0.4 * ppm
    img = _lattice_wall(n_img_piers=9, x_origin=x_origin, strip=(2 * pitch + 2.0 - 0.4, 2 * pitch + 3.5 - 0.4))
    b = _wall_json(count=4, with_part=(2.0, 3.5))
    res = compare.compare(img, b, "S", wall_box=[60, 100, img.shape[1] - 60, img.shape[0] - 30], tol_m=0.15)
    assert res.ok
    assert abs(res.x_origin_px - true_origin) < 0.2 * ppm
    best, second = res.origin_candidates[0], res.origin_candidates[1]
    assert best["score"] - second["score"] >= 0.75 and not res.ambiguous
    assert best["landmarks_found"] >= 2
    parts = [l for l in res.lines if l.kind == "part" and l.in_plane]
    assert len(parts) >= 2 and all(l.ok for l in parts)      # 部品の縁（縦の線）が compare の線に入っている


# --- 柱割りの無い面と部品の縁 ---------------------------------------------

def _bayless_json():
    def m(v):
        return {"value": v, "status": "estimated", "evidence": [], "method": "test"}
    poly_tower = [[3.0, 0], [9.0, 0], [9.0, 3], [3.0, 3]]       # 面の内側（法線は −y が外向き）
    return {"id": "t", "name": "t", "parapet_top": m(12.0),
            "faces": [{"id": "N", "name": "n", "from": [0, 0], "to": [20, 0], "length": m(20.0),
                       "bays": {}, "windows": [], "openings": [], "ledges": []}],
            "blocks": [{"id": "A", "polygon": [[0, 0], [20, 0], [20, 10], [0, 10]], "top_z": m(12.0)}],
            "parts": [{"type": "box", "name": "階段塔", "polygon": poly_tower, "z0": m(0.0), "z1": m(16.0)},
                      {"type": "balcony", "name": "踊り場", "face": "N", "along0": m(12.0), "along1": m(15.0), "z": m(6.0),
                       "depth": m(2.0), "parapet_h": m(1.0)}]}


def test_face_lines_detail_adds_part_edges_without_bays():
    b = _bayless_json()
    xs, zs, info = compare.face_lines_detail(b, "N")
    names = {l.name: l for l in xs}
    inplane = [l for l in xs if l.kind == "part" and l.in_plane]
    assert sorted(round(l.m, 2) for l in inplane) == [3.0, 9.0]                 # 階段塔の縁（面の上）
    off = [l for l in xs if not l.in_plane]
    assert sorted(round(l.m, 2) for l in off) == [12.0, 15.0] and all(l.depth_m == 2.0 and l.weight == 0 for l in off)  # 踊り場は手前
    assert "面の端0" in names and "面の端L" in names
    zset = {l.name: l.m for l in zs}
    assert zset["パラペット上端"] == 12.0 and any(abs(v - 16.0) < 1e-6 for v in zset.values())   # 塔の上端
    assert info["tall_parts"] and info["tall_parts"][0]["z1"] == 16.0


def test_compare_runs_on_bayless_face_with_given_scale():
    """窓も柱も無い壁（北西面の型）でも、面の端・塔の縁・パラペット・塔の上端で compare が動き、原点が合う。"""
    ppm, ground, x_origin = 40.0, 760, 120
    b = _bayless_json()
    W, H = int(x_origin * 2 + 20 * ppm), ground + 80
    img = np.full((H, W, 3), 150, np.uint8)                          # 壁
    img[:, : x_origin] = 20
    img[:, x_origin + int(20 * ppm):] = 20                           # 面の外（余白）
    img[: int(ground - 12.0 * ppm), :] = (235, 200, 150)             # 空（青）
    img[int(ground - 16.0 * ppm): ground, x_origin + int(3 * ppm): x_origin + int(9 * ppm)] = 100    # 塔（足元から 16 m まで、壁より暗い）
    img[ground:, :] = 90
    res = compare.compare(img, b, "N", wall_box=[x_origin - 40, 60, W - 80, H - 30], tol_m=0.2, px_per_m=ppm)
    assert res.ok, res.reason
    inpl = [l for l in res.lines if l.in_plane]
    assert len(inpl) >= 4                                            # 端 2 + 塔の縁 2 + 高さ 2
    assert abs(res.x_origin_px - x_origin) < 0.3 * ppm
    assert any(l.kind == "part" and l.ok for l in inpl)
    assert abs(res.z_origin_px - ground) < 0.3 * ppm


# --- 面ごとの地面の高さ --------------------------------------------------

def _gm(v):
    return {"value": v, "unit": "m", "status": "estimated", "evidence": [], "method": "test"}


def test_compare_uses_ground_offset_for_height_origin():
    """手がかりの線が無い時、壁の下端を面の前の地面（ground_offset）と見て、z=0 の行を決める。"""
    b = _face_json()
    b["faces"][0]["windows"] = []
    b["faces"][0]["ground_offset"] = _gm(-3.0)
    b["parapet_top"] = {"value": None, "status": "unknown", "evidence": [], "method": ""}
    img = _synthetic(_face_json())
    res = compare.compare(img, b, "S", wall_box=[60, 100, img.shape[1] - 60, img.shape[0] - 30], px_per_m=40.0, tol_m=0.1)
    assert res.ground_offset_m == -3.0
    xs, zs, info = compare.face_lines_detail(b, "S")
    assert any(l.name == "地面（面の前）" and l.m == -3.0 for l in zs) and info["ground_offset"] == -3.0
