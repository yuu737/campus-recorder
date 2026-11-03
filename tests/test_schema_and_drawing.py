import copy
import json
from pathlib import Path

import pytest

from recorder import drawing, schema

FIX = Path(__file__).parent / "fixtures" / "sample_building.json"


def load():
    return json.loads(FIX.read_text(encoding="utf-8"))


def test_sample_is_valid():
    assert schema.validate_building(load()) == []


def test_measured_without_evidence_is_rejected():
    b = load()
    b["parapet_top"]["evidence"] = []
    errs = schema.validate_building(b)
    assert any("parapet_top" in e and "根拠" in e for e in errs)


def test_bare_number_is_rejected():
    b = load()
    b["floor_pitch"] = 3.6  # 根拠の無い裸の数値
    errs = schema.validate_building(b)
    assert any("floor_pitch" in e for e in errs)


def test_unknown_with_value_is_rejected():
    b = load()
    b["faces"][0]["windows"][0]["width"]["value"] = 2.0  # status は unknown のまま
    errs = schema.validate_building(b)
    assert any("windows[0].width" in e for e in errs)


def test_estimated_needs_method():
    b = load()
    b["ground_z"]["method"] = ""
    errs = schema.validate_building(b)
    assert any("ground_z" in e and "推定" in e for e in errs)


def test_request_must_point_to_existing_face():
    b = load()
    b["requests"][0]["face"] = "X"
    errs = schema.validate_building(b)
    assert any("requests[0]" in e for e in errs)


def test_unknowns_lists_missing_values():
    u = schema.unknowns(load())
    assert "faces[S].windows[0].width" in u
    assert "faces[S].windows[1].z_bottom" in u


def test_elevation_svg_has_dimensions_and_evidence():
    svg = drawing.elevation_svg(load(), "S")
    assert "<svg" in svg and "</svg>" in svg
    assert "15.00" in svg and "パラペット上端" in svg
    assert "[T001]" in svg
    assert "未確定" in svg  # 窓幅・4階窓の下端
    assert "4階の窓を真横に近い角度で1枚" in svg  # 撮影依頼


def test_elevation_for_empty_face_is_drawn_as_unknown():
    svg = drawing.elevation_svg(load(), "E")
    assert "全長 未確定" in svg
    assert "両端が入る1枚" in svg


def test_plan_svg_and_all_drawings():
    out = drawing.all_drawings(load())
    assert set(out) == {"plan", "elev_S", "elev_E"}
    assert "平面図" in out["plan"]


def test_new_building_is_valid_and_all_unknown():
    b = schema.new_building("x", "X棟")
    assert schema.validate_building(b) == []
    assert "footprint" in schema.unknowns(b)


def test_missing_face_raises():
    with pytest.raises(KeyError):
        drawing.elevation_svg(load(), "Z")


# --- 面の端の余白（margin_from / margin_to） ---------------------------------------

def _mm(v, status="estimated"):
    if v is None:
        return {"value": None, "unit": "m", "tol": None, "status": "unknown", "evidence": [], "method": "", "note": ""}
    return {"value": v, "unit": "m", "tol": 0.1, "status": status, "evidence": [], "method": "テスト", "note": ""}


def _face_b(**bays_extra):
    """長さ 36.8、6 スパン×5.6 = 33.6、余り 3.2。柱の幅は 0（線で描く）。"""
    b = load()
    f = b["faces"][0]
    f["length"] = {"value": 36.8, "unit": "m", "tol": 0.1, "status": "estimated", "evidence": [], "method": "テスト", "note": ""}
    f["bays"] = {"count": {"value": 6, "unit": "個", "tol": None, "status": "estimated", "evidence": [], "method": "テスト", "note": ""},
                 "pitch": _mm(5.6), "pier_width": _mm(0.0)}
    f["bays"].update(bays_extra)
    return b, f


def _edges(b, f):
    mf, mt, w = schema.bay_margins(f["bays"], f["length"]["value"])
    return mf, mt, w


def test_margin_from_only():
    b, f = _face_b(margin_from=_mm(0.0))
    mf, mt, w = _edges(b, f)
    assert mf == 0.0 and abs(mt - 3.2) < 1e-9 and not w
    assert schema.validate_building(b) == []
    svg = drawing.elevation_svg(b, f["id"])
    assert "仮に等分" not in svg


def test_margin_to_only_puts_remainder_at_from_side():
    b, f = _face_b(margin_to=_mm(0.0), margin_from=_mm(None))
    mf, mt, w = _edges(b, f)
    assert abs(mf - 3.2) < 1e-9 and mt == 0.0 and not w      # unknown の from 側に余りが出る（0 ではない）
    assert schema.validate_building(b) == []


def test_both_null_splits_remainder_and_warns():
    b, f = _face_b(margin_from=_mm(None), margin_to=_mm(None))
    mf, mt, w = _edges(b, f)
    assert abs(mf - 1.6) < 1e-9 and abs(mt - 1.6) < 1e-9
    assert w and "仮に等分" in w[0]
    drawing.MARGIN_WARNINGS.clear()
    svg = drawing.elevation_svg(b, f["id"])
    assert "端の余白 unknown、仮に等分" in svg
    assert any("仮に等分" in x for x in drawing.MARGIN_WARNINGS)


def test_pier_positions_follow_margin_side():
    b, f = _face_b(margin_from=_mm(None), margin_to=_mm(0.0))
    svg = drawing.elevation_svg(b, f["id"])
    left = drawing.LEFT
    # 最初の柱線は from 端から 3.2 m（余り）の所、最後は面の端
    x_first = left + 3.2 * drawing.SCALE
    x_last = left + 36.8 * drawing.SCALE
    assert f'x1="{x_first:.1f}"' in svg or f'x1="{x_first:.0f}"' in svg or f"{x_first:.1f}" in svg
    assert f"{x_last:.1f}" in svg


def test_end_margin_is_alias_of_margin_from():
    b, f = _face_b(end_margin=_mm(0.6))
    mf, mt, w = _edges(b, f)
    assert mf == 0.6 and abs(mt - 2.6) < 1e-9
    assert schema.validate_building(b) == []
    b2, f2 = _face_b(margin_from=_mm(0.6))
    assert drawing.elevation_svg(b, f["id"]) == drawing.elevation_svg(b2, f2["id"])


def test_margin_sum_mismatch_fails_validate():
    b, f = _face_b(margin_from=_mm(1.0), margin_to=_mm(0.5))     # 合計 1.5 だが余りは 3.2
    errs = schema.validate_building(b)
    assert any("余白の合計" in e for e in errs)
    b, f = _face_b(margin_from=_mm(1.6), margin_to=_mm(1.6))
    assert schema.validate_building(b) == []


# --- 面ごとの地面の高さ（faces[].ground_offset）---------------------------------

def _with_ground(v, status="estimated", evidence=None, method="タワー75 の広場の段差から"):
    b = load()
    b["faces"][0]["ground_offset"] = {"value": v, "unit": "m", "tol": 0.2, "status": status,
                                      "evidence": evidence or [], "method": method, "note": ""}
    return b


def test_ground_offset_is_optional_and_existing_json_still_valid():
    b = load()
    assert "ground_offset" not in b["faces"][0] and schema.validate_building(b) == []
    assert schema.face_ground_offset(b["faces"][0]) == 0.0


def test_ground_offset_is_a_measurement_with_rules():
    b = _with_ground(-3.0)
    assert schema.validate_building(b) == [] and schema.face_ground_offset(b["faces"][0]) == -3.0
    bad = _with_ground(1.4, status="measured", evidence=[])                       # measured は根拠が要る
    assert any("ground_offset" in e and "根拠" in e for e in schema.validate_building(bad))
    bare = load()
    bare["faces"][0]["ground_offset"] = 1.4                                       # 裸の数は落とす
    assert any("ground_offset" in e for e in schema.validate_building(bare))
    unk = _with_ground(None, status="unknown", method="")
    assert schema.validate_building(unk) == [] and schema.face_ground_offset(unk["faces"][0]) == 0.0
    assert "faces[S].ground_offset" in schema.unknowns(unk)


def test_elevation_draws_ground_line_at_offset():
    import re
    base = drawing.elevation_svg(load(), "S")
    sunk = drawing.elevation_svg(_with_ground(-3.0), "S")
    raised = drawing.elevation_svg(_with_ground(1.4), "S")
    assert "地面（面の前）" in sunk and "z=0（棟の基準）" in sunk and "地面（面の前）" not in base
    # 太い地面の線（stroke-width 1.5）の y。沈むと下がり（画像の y は増える）、高いと上がる
    def ground_y(svg):
        return max(float(m.group(1)) for m in re.finditer(r'<line x1="[\d.]+" y1="([\d.]+)" x2="[\d.]+" y2="\1"[^>]*stroke-width="1.5"', svg))
    y0, ys, yr = ground_y(base), ground_y(sunk), ground_y(raised)
    assert ys > y0 > yr
    assert abs((ys - y0) - 3.0 * drawing.SCALE) < 1.0 and abs((y0 - yr) - 1.4 * drawing.SCALE) < 1.0
