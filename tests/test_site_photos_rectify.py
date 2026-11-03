"""土台（site）・写真の取り込み（photos）・正面化（rectify）のテスト。ネットには出ない。"""

import json
from pathlib import Path

import numpy as np
import pytest

from recorder import geo, photos, rectify, site


# --- geo ---------------------------------------------------------------------

def test_origin_maps_to_zero():
    x, y = geo.lonlat_to_xy(geo.ORIGIN_LON, geo.ORIGIN_LAT)
    assert abs(x) < 1e-6 and abs(y) < 1e-6


def test_xy_roundtrip_and_direction():
    lon, lat = geo.xy_to_lonlat(100.0, 200.0)
    assert lon > geo.ORIGIN_LON and lat > geo.ORIGIN_LAT  # x=東, y=北
    x, y = geo.lonlat_to_xy(lon, lat)
    assert abs(x - 100) < 1e-3 and abs(y - 200) < 1e-3


def test_tile_roundtrip():
    tx, ty = geo.lonlat_to_tile(136.9755, 35.1355, 18)
    lon, lat = geo.tile_to_lonlat(tx, ty, 18)
    assert abs(lon - 136.9755) < 1e-9 and abs(lat - 35.1355) < 1e-9


def test_mesh3_code_of_campus():
    # 天白キャンパスの原点は 3次メッシュ 52365768（PLATEAU のファイル名と一致）
    assert site.mesh3_of_lonlat(136.9755, 35.1355) == "52365768"
    assert site.mesh3_of_lonlat(136.9740, 35.1355) == "52365767"  # すぐ西隣


# --- site -----------------------------------------------------------------------

def _square(cx, cy, s):
    return [[cx - s, cy - s], [cx + s, cy - s], [cx + s, cy + s], [cx - s, cy + s], [cx - s, cy - s]]


@pytest.fixture
def osm_doc():
    return {
        "boundary": [_square(0, 0, 300)],
        "buildings": [
            {"osm": "way/1", "name": "本部棟", "building": "public", "levels_osm": "5", "rings": [_square(0, 0, 20)]},
            {"osm": "way/2", "name": None, "building": "yes", "levels_osm": None, "rings": [_square(100, 0, 10)]},
            {"osm": "way/3", "name": "タワー75", "building": "university", "levels_osm": "16", "rings": [_square(200, 0, 15)]},
        ],
        "fetched_at": "2026-11-03T10:00:00",
    }


@pytest.fixture
def plateau_doc():
    return {"source": "PLATEAU test", "buildings": [
        {"gml_id": "A", "name": None, "usage": "422", "height_m": 30.0, "base_z": 42.0, "storeys": 5, "footprint": _square(0, 0, 19)[:-1]},
        {"gml_id": "B", "name": None, "usage": "422", "height_m": 74.0, "base_z": 45.0, "storeys": 16, "footprint": _square(222, 0, 6)[:-1]},  # タワーの横にずれている
        {"gml_id": "C", "name": None, "usage": "422", "height_m": 3.0, "base_z": 40.0, "storeys": 1, "footprint": _square(-200, -200, 3)[:-1]},
    ]}


def test_build_site_matches_plateau_and_keeps_confirmation(tmp_path, osm_doc, plateau_doc):
    out = tmp_path / "t.json"
    doc = site.build_site("t", osm_doc, plateau_doc, out)
    assert site.validate_site(doc) == []
    honbu = next(b for b in doc["buildings"] if b["id"] == "honbu")
    assert honbu["plateau_parts"] == ["A"] and honbu["plateau_match"] == "overlap"
    assert honbu["reference"]["plateau_height_m"] == 30.0
    assert honbu["confirmation"]["status"] == "unconfirmed"
    tower = next(b for b in doc["buildings"] if b["id"] == "tower75")
    assert tower["plateau_match"] == "nearby" and tower["plateau_parts"] == ["B"]  # 重ならないが 15m 以内
    assert doc["plateau_only"] == ["C"]
    unnamed = next(b for b in doc["buildings"] if b["osm"] == "way/2")
    assert unnamed["name"] is None and unnamed["id"].startswith("osm-")

    # 職員の確認を書いてから作り直しても、確認は残る
    site.confirm(doc, "honbu", "renamed", by="職員A", name="本部棟（旧）")
    out.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    doc2 = site.build_site("t", osm_doc, plateau_doc, out)
    h2 = next(b for b in doc2["buildings"] if b["id"] == "honbu")
    assert h2["name"] == "本部棟（旧）" and h2["confirmation"]["status"] == "renamed"
    assert h2["confirmation"]["name_old"] == "本部棟" and h2["name_source"] == "staff"


def test_validate_site_requires_who_and_when(osm_doc, tmp_path):
    doc = site.build_site("t", osm_doc, None, tmp_path / "t.json")
    doc["buildings"][0]["confirmation"] = {"status": "confirmed", "by": None, "at": None}
    errs = site.validate_site(doc)
    assert any("誰が・いつ" in e for e in errs)
    doc["buildings"][0]["confirmation"] = {"status": "bogus"}
    assert any("status が不正" in e for e in site.validate_site(doc))


def test_confirm_rejects_unknown_status(osm_doc, tmp_path):
    doc = site.build_site("t", osm_doc, None, tmp_path / "t.json")
    with pytest.raises(ValueError):
        site.confirm(doc, "honbu", "maybe", by="x")


def test_aerial_px_roundtrip():
    meta = {"z": 18, "tile_x0": 230812.3, "tile_y0": 103241.7, "tile_x1": 230818.0, "tile_y1": 103251.5,
            "width": int(5.7 * 256), "height": int(9.8 * 256)}
    px, py = site.aerial_xy_to_px(meta, 12.0, -34.0)
    x, y = site.aerial_px_to_xy(meta, px, py)
    assert abs(x - 12.0) < 0.01 and abs(y + 34.0) < 0.01


def test_dem_decode_and_lookup():
    import io

    from PIL import Image

    # 値 v = R*65536 + G*256 + B、標高 = v*0.01。(0,10,0) → 25.6 m、(128,0,0) → 無効
    a = np.zeros((256, 256, 3), np.uint8)
    a[..., 1] = 10
    a[0, 0] = (128, 0, 0)
    buf = io.BytesIO()
    Image.fromarray(a).save(buf, format="PNG")
    z = site._decode_dem_png(buf.getvalue())
    assert abs(z[5, 5] - 25.6) < 1e-6 and np.isnan(z[0, 0])
    dem = {"x": np.arange(-5, 5, 1.0), "y": np.arange(-5, 5, 1.0), "z": np.full((10, 10), 40.0)}
    assert site.dem_at(dem, 0.4, -0.4) == 40.0
    assert site.dem_at(dem, 50, 50) is None


# --- photos ----------------------------------------------------------------------

def _jpeg_with_exif(path: Path, lat=35.1360, lon=136.9760, heading=135.0, f35=26, size=(1600, 1200)):
    from PIL import ExifTags, Image

    im = Image.new("RGB", size, (180, 170, 160))
    ex = Image.Exif()
    ex[ExifTags.Base.Make] = "TestPhone"
    ex[ExifTags.Base.Model] = "T1"
    ifd = ex.get_ifd(ExifTags.IFD.Exif)
    ifd[ExifTags.Base.DateTimeOriginal] = "2026:11:03 13:05:09"
    ifd[ExifTags.Base.FocalLength] = 5.1
    ifd[ExifTags.Base.FocalLengthIn35mmFilm] = f35
    if lat is not None:
        g = ex.get_ifd(ExifTags.IFD.GPSInfo)
        G = ExifTags.GPS

        def dms(v):
            v = abs(v)
            d = int(v)
            m = int((v - d) * 60)
            s = (v - d - m / 60) * 3600
            return (d, m, round(s, 4))

        g[G.GPSLatitudeRef] = "N"
        g[G.GPSLatitude] = dms(lat)
        g[G.GPSLongitudeRef] = "E"
        g[G.GPSLongitude] = dms(lon)
        g[G.GPSAltitude] = 48.5
        if heading is not None:
            g[G.GPSImgDirectionRef] = "T"
            g[G.GPSImgDirection] = heading
    im.save(path, "JPEG", exif=ex.tobytes())


def test_read_exif_and_candidates(tmp_path, osm_doc):
    p = tmp_path / "IMG_0001.jpg"
    lon, lat = geo.xy_to_lonlat(-40.0, 0.0)
    _jpeg_with_exif(p, lat=lat, lon=lon, heading=90.0)  # 本部棟の西 40 m に立って東を向く
    info = photos.read_exif(p)
    assert info["focal_35mm"] == 26 and info["heading_deg"] == 90.0 and info["taken_at"] == "2026-11-03T13:05:09"
    assert abs(info["gps"]["x"] + 40) < 1.0 and abs(info["gps"]["y"]) < 1.0
    sdoc = site.build_site("t", osm_doc, None, tmp_path / "s.json")
    cands = photos.building_candidates(info, sdoc)
    ids = [c["building"] for c in cands]
    assert ids[0] == "honbu" and cands[0]["distance_m"] == 20.0
    assert "osm-way-2" not in ids          # 東 130 m は遠すぎる
    assert "tower75" not in ids
    info["heading_deg"] = 270.0            # 西を向いたら本部棟は候補から外れる
    assert "honbu" not in [c["building"] for c in photos.building_candidates(info, sdoc)]


def test_import_photos_keeps_manual_fields(tmp_path, osm_doc):
    root = tmp_path / "photos"
    day = root / "20261103"
    day.mkdir(parents=True)
    _jpeg_with_exif(day / "IMG_0001.jpg")
    _jpeg_with_exif(day / "IMG_0002.jpg", lat=None)  # GPS なし
    (day / "note.txt").write_text("x", encoding="utf-8")
    idx_path = root / "index.json"
    sdoc = site.build_site("t", osm_doc, None, tmp_path / "s.json")
    idx, added = photos.import_photos(day, sdoc, idx_path, root)
    assert len(added) == 2 and idx["photos"][0]["file"] == "20261103/IMG_0001.jpg"
    assert idx["photos"][1]["checks"]["has_gps"] is False and idx["photos"][1]["candidates"] == []
    assert idx["photos"][0]["building"] is None  # 決めつけない
    idx["photos"][0]["building"] = "honbu"
    idx["photos"][0]["face"] = "SW"
    photos.save_index(idx, idx_path)
    idx2, added2 = photos.import_photos(day, sdoc, idx_path, root)
    assert added2 == [] and idx2["photos"][0]["building"] == "honbu" and idx2["photos"][0]["face"] == "SW"
    assert "GPS なし" in photos.summary_line(idx2["photos"][1])


# --- rectify -------------------------------------------------------------------------

def test_rectify_synthetic_facade_becomes_front():
    img, _ = rectify.synthetic_facade(yaw_deg=30, pitch_deg=12)
    out, res = rectify.rectify(img, focal_35mm=26.0)
    assert res.ok, res.reason
    assert res.residual_deg_h < 0.5 and res.residual_deg_v < 0.5
    assert out.shape[0] > 100 and out.shape[1] > 100


def test_rectify_estimates_focal_without_exif():
    img, _ = rectify.synthetic_facade(yaw_deg=30, pitch_deg=12)
    out, res = rectify.rectify(img, focal_35mm=None)
    assert res.ok and res.focal_source == "estimated_from_vps"
    true_f = rectify.focal_px_from_35mm(26.0, 1600, 1200)
    assert abs(res.focal_px - true_f) / true_f < 0.15


def test_rectify_refuses_blank_image():
    img = np.full((800, 1000, 3), 128, np.uint8)
    out, res = rectify.rectify(img, focal_35mm=26.0)
    assert out is None and not res.ok and res.reason


# --- rectify: roi / measure ---------------------------------------------------------

def test_rectify_roi_restricts_segments():
    img, _ = rectify.synthetic_facade(yaw_deg=30, pitch_deg=12)
    out, res = rectify.rectify(img, focal_35mm=26.0, roi=(0.1, 0.1, 0.9, 0.9))
    assert res.ok and res.roi == [160.0, 120.0, 1440.0, 1080.0]
    assert res.wall_box and res.wall_box[2] > res.wall_box[0]
    _, res2 = rectify.rectify(img, focal_35mm=26.0, roi=(0.0, 0.0, 0.05, 0.05))  # 壁の無い所
    assert not res2.ok and res2.reason


def test_measure_recovers_bay_floor_ratio():
    from recorder import measure

    img, _ = rectify.synthetic_facade(yaw_deg=25, pitch_deg=10)
    out, res = rectify.rectify(img, focal_35mm=26.0)
    assert res.ok
    m = measure.measure(out, res.wall_box)
    assert m.ok and m.bays.period_px and m.floors.period_px
    # 人工の壁は 7 列 × 4 段、窓の列の間隔 128 px・段の間隔 168 px（正面化前の画素）。比は縮尺に依らない
    assert abs(m.ratio_bay_over_floor - 128 / 168) / (128 / 168) < 0.03
    assert m.bays.count >= 4 and m.floors.count >= 3  # 区間の数は候補。端は落ちることがある
    m2 = measure.measure(out, res.wall_box, known=("floor_pitch", 3.6), known_status="measured")
    assert m2.floors.period_m == 3.6 and m2.floors.status == "measured"
    assert abs(m2.bays.period_m - 3.6 * 128 / 168) < 0.1
    assert m2.scale["what"] == "floor_pitch"
