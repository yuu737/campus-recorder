"""メジャーを写した区間（--known segment=<m>@<px>）を縮尺にする。"""
import json

import cv2
import numpy as np
import pytest

from recorder import __main__ as cli
from recorder import measure
from tests.test_measure_period import stripes


def test_parse_known_segment_and_named():
    assert measure.parse_known("segment=0.698@85.3") == ("segment", 0.698, 85.3)
    assert measure.parse_known("floor_pitch=3.99") == ("floor_pitch", 3.99)
    assert measure.parse_known(None) is None
    for bad in ("segment=0.698", "segment=0@85", "height=3", "floor_pitch"):
        with pytest.raises(ValueError):
            measure.parse_known(bad)


def test_segment_scale_is_measured_and_converts_periods():
    img = stripes()                                    # 柱の周期 120 px
    r = measure.measure(img, known=("segment", 0.5, 100.0), known_status="estimated")
    assert r.scale["what"] == "tape_measure" and r.scale["status"] == "measured"
    assert r.scale["px"] == 100.0 and r.scale["m"] == 0.5 and abs(r.scale["m_per_px"] - 0.005) < 1e-12
    assert abs(r.bays.period_m - r.bays.period_px * 0.005) < 1e-3 and r.bays.status == "measured"


def test_cli_known_segment_writes_scale(tmp_path):
    jpg = tmp_path / "w_front.jpg"
    cv2.imwrite(str(jpg), cv2.cvtColor(stripes(), cv2.COLOR_GRAY2BGR))
    assert cli.main(["photos", "measure", str(jpg), "--known", "segment=0.698@85.3"]) == 0
    d = json.loads((tmp_path / "w_front_measure.json").read_text(encoding="utf-8"))
    assert d["scale"]["what"] == "tape_measure" and d["scale"]["status"] == "measured"
    assert d["scale"]["px"] == 85.3 and d["scale"]["m"] == 0.698
    assert cli.main(["photos", "measure", str(jpg), "--known", "segment=0.698"]) == 2
