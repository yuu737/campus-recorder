"""被覆図の写真：面の座標への逆変換・切り取りの規則・重ね順・未撮影の矩形・--no-photo・Web の灰色の重ね。合成の写真で。"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from recorder import coverage, schema, site

FIX = Path(__file__).parent / "fixtures" / "sample_building.json"
FACE = {"id": "S", "from": [0.0, 0.0], "to": [30.0, 0.0]}       # fixture の S 面（30 m、外向きは南）


def entry(photo: str, ppm: float, w: int, h: int, cam=(10.0, -15.0, 5.0), **kw) -> dict:
    """面の (s, z) → 写真 (ppm·s, h − ppm·z)。正面から撮った写真。"""
    H = [[ppm, 0, 0], [0, -ppm, h], [0, 0, 1]]
    return {"photo": photo, "building": "sample", "face": "S", "period": "during", "file": f"{photo}.jpg", "image_size": [w, h],
            "H_plane": H, "face_frame": {"from": FACE["from"], "to": FACE["to"]}, "camera": {"camera_xyz": list(cam)}, **kw}


@pytest.fixture
def flat_ground(monkeypatch):
    # 地面の線を 0 に固定（地形の DEM を読まない）
    monkeypatch.setattr(coverage.drawing, "face_ground_line", lambda b, f, dem=None: (0.0, "none"))


def test_warp_roundtrip_rectangle():
    """写真の中の矩形（面の s 2〜4・z 3〜6）が、面の座標の格子の同じ所に来る（縮めてから貼る経路も通る）。"""
    e = entry("A", 100.0, 2000, 1000)
    img = np.zeros((1000, 2000, 3), np.uint8)
    img[1000 - 600:1000 - 300, 200:400] = (0, 0, 255)           # v = 1000 − 100 z、u = 100 s
    rect, ppm = (0.0, 30.0, 0.0, 15.0), 20.0
    out = coverage.warp_to_face(img, e, FACE, rect, ppm, 100.0)
    assert out.shape == (300, 600, 3)
    ys, xs = np.nonzero(out[..., 2] > 128)
    s = (xs + 0.5) / ppm
    z = 15.0 - (ys + 0.5) / ppm
    assert abs(s.min() - 2.0) < 0.1 and abs(s.max() - 4.0) < 0.1
    assert abs(z.min() - 3.0) < 0.1 and abs(z.max() - 6.0) < 0.1
    # 写真の外は黒（使わない）
    assert out[:, 420:].max() == 0


def test_trim_oblique_by_density_and_angle():
    S, Z = np.meshgrid(np.arange(0.25, 20, 0.5), np.arange(0.25, 10, 0.5))
    # 透視の項で遠い側（s 大）ほど画素数／m が減る：中央値の 1/3 未満を捨てる
    e = entry("P", 100.0, 10 ** 6, 10 ** 6, cam=(10.0, -50.0, 5.0))
    e["H_plane"] = [[100.0, 0, 0], [0, -100.0, 1000.0], [1.0, 0, 1.0]]
    tm = coverage.trim_masks(e, FACE, S, Z)
    assert tm["in_frame"].all()
    assert not tm["oblique"][:, 0].any() and tm["oblique"][:, -1].all()
    # 視線と法線の角度 70° 超（カメラが面の端の横 3 m）
    e2 = entry("Q", 100.0, 10 ** 6, 10 ** 6, cam=(-5.0, -3.0, 5.0))
    tm2 = coverage.trim_masks(e2, FACE, S, Z)
    D = np.stack([-5.0 - S, np.full(S.shape, -3.0), 5.0 - Z], -1)          # 点 → カメラ（3 次元の視線）
    ang = np.degrees(np.arccos(3.0 / np.linalg.norm(D, axis=-1)))
    assert tm2["oblique"][ang > 70.5].all() and not tm2["oblique"][ang < 69.5].any()


def test_overlap_order_exclude_and_uncovered(tmp_path, flat_ground):
    """重なる所は画素数／m が多い写真が上。exclude は捨て、下の写真が見える。両方が捨てた所は『使えない』。"""
    e1 = entry("near", 100.0, 2000, 1000, exclude=[[2.0, 4.0, 3.0, 6.0]], exclude_note=["木"])   # s 0〜20・z 0〜10
    e2 = entry("far", 50.0, 1000, 1000, exclude=[[6.0, 8.0, 3.0, 6.0]])                         # s 0〜20・z 0〜15
    e1b = dict(e1, exclude=[[2.0, 4.0, 3.0, 6.0], [6.0, 8.0, 3.0, 6.0]])                          # 6〜8 は両方で捨てる
    colors = {"near": (0, 0, 255), "far": (255, 0, 0)}
    loader = lambda e: np.full((e["image_size"][1], e["image_size"][0], 3), colors[e["photo"]], np.uint8)  # noqa: E731
    reg = {"entries": [e1b, e2], "not_registered": []}
    res, jp = coverage.coverage(FIX, reg, tmp_path, photo=True, px_per_m=20.0, loader=loader)
    row = next(r for r in res["faces"] if r["face"] == "S")
    by = {p["photo"]: p for p in row["photos"]}
    assert by["near"]["order"] == 1 and by["far"]["order"] == 2
    assert by["near"]["px_per_m_median"] == pytest.approx(100.0) and by["far"]["px_per_m_median"] == pytest.approx(50.0)
    assert any("木" in t for t in by["near"]["trimmed"])
    rgba = cv2.cvtColor(cv2.imread(str(tmp_path / "coverage_S_photo.png"), cv2.IMREAD_UNCHANGED), cv2.COLOR_BGRA2RGBA)
    assert rgba.shape == (300, 600, 4)

    def px(s, z):
        return rgba[int((15.0 - z) * 20), int(s * 20)]
    assert tuple(px(10, 5)) == (255, 0, 0, 255)          # 両方：近い（near、赤）が上
    assert tuple(px(3, 5)) == (0, 0, 255, 255)           # near の遮蔽 → far（青）
    assert tuple(px(15, 12)) == (0, 0, 255, 255)         # far だけ
    assert px(7, 5)[3] == 0                              # 両方が捨てた：使えない（透明）
    assert px(25, 5)[3] == 0                             # 未撮影
    assert row["unusable_m2"]["exclude"] == pytest.approx(6.0, abs=0.01)
    assert row["uncovered_rects"] == [[20.0, 30.0, 0.0, 15.0]]
    assert row["unusable_rects"] == [[6.0, 8.0, 3.0, 6.0]]
    assert row["coverage_pct"] == pytest.approx(100 * (20 * 15 - 6) / (30 * 15), abs=0.1)
    svg = (tmp_path / "coverage_S.svg").read_text(encoding="utf-8")
    assert 'href="coverage_S_photo.png"' in svg and 'id="elevation-lines"' in svg and "url(#cov-hatch)" in svg
    assert (tmp_path / "coverage_S_check.png").exists()
    # Web の重ね：写真ではなく、未撮影・使えない所の矩形だけ
    b = schema.load_building(FIX)
    ev = schema.evidence_summary(b)
    layer = site.coverage_layer(b, ev, jp)
    for f in layer["faces"]:
        assert set(f) == {"id", "from", "to", "coverage_pct", "uncovered", "unusable"}
    if (ev["faces"].get("S") or {}).get("state") not in (None, "unknown"):
        fS = next(f for f in layer["faces"] if f["id"] == "S")
        assert fS["uncovered"] == [[20.0, 30.0, 0.0, 15.0]]
        assert fS["unusable"] == [[6.0, 8.0, 3.0, 6.0]]


def test_no_photo_keeps_fill_only(tmp_path, flat_ground):
    def boom(e):
        raise AssertionError("--no-photo では写真を読まない")
    reg = {"entries": [entry("near", 100.0, 2000, 1000)], "not_registered": []}
    res, _ = coverage.coverage(FIX, reg, tmp_path, photo=False, loader=boom)
    row = next(r for r in res["faces"] if r["face"] == "S")
    assert row["coverage_pct"] == pytest.approx(100 * 200 / 450, abs=0.1)
    assert not list(tmp_path.glob("*.png"))
    svg = (tmp_path / "coverage_S.svg").read_text(encoding="utf-8")
    assert "<image" not in svg and 'fill-opacity="0.18"' in svg and "elevation-lines" not in svg


def test_mask_to_rects_covers_exactly():
    rng = np.random.default_rng(0)
    m = rng.random((12, 17)) > 0.6
    sc, zc = np.arange(17) * 0.5 + 0.25, np.arange(12) * 0.5 + 0.25
    rs = coverage.mask_to_rects(m, sc, zc, 0.5)
    back = np.zeros_like(m)
    for s0, s1, z0, z1 in rs:
        back[int(round(z0 / 0.5)):int(round(z1 / 0.5)), int(round(s0 / 0.5)):int(round(s1 / 0.5))] |= True
    assert (back == m).all()
    assert sum((r[1] - r[0]) * (r[3] - r[2]) for r in rs) == pytest.approx(m.sum() * 0.25)
