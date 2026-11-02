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
