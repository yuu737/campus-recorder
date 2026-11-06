"""立面図の右の注記・下の一覧が図の幅の外に出て切れないこと（11/6）。"""
import copy
import json
import re
from pathlib import Path

from recorder import drawing

FIX = Path(__file__).parent / "fixtures" / "sample_building.json"


def load():
    return json.loads(FIX.read_text(encoding="utf-8"))


def _size(svg):
    m = re.search(r'<svg[^>]*width="(\d+)" height="(\d+)" viewBox="0 0 (\d+) (\d+)"', svg)
    w, h, vw, vh = map(int, m.groups())
    assert (w, h) == (vw, vh)          # viewBox も合わせて広げる
    return w, h


def _wide_face(b):
    """長さ 70 m の面にする（今までの幅 = 70 + 70×14 + 300 = 1350）。"""
    b["faces"][0]["length"] = {"value": 70.0, "unit": "m", "status": "measured", "evidence": ["T001"]}
    return b


def test_short_labels_keep_old_width():
    b = _wide_face(load())
    w, _ = _size(drawing.elevation_svg(b, "S"))
    assert w == 70 + 70 * 14 + drawing.RIGHT_LABELS
    w0, _ = _size(drawing.elevation_svg(load(), "S"))
    assert w0 == max(70 + 30 * 14 + drawing.RIGHT_LABELS, 720)


def test_long_label_widens_svg():
    b = _wide_face(load())
    base_w, _ = _size(drawing.elevation_svg(b, "S"))
    b2 = copy.deepcopy(b)
    b2["levels"][0]["name"] = "ピロティ（スパン 4〜6 は通り抜けの道路、7〜9 は歩行者の通り抜け・大階段）の天井の下端"
    w, _ = _size(drawing.elevation_svg(b2, "S"))
    assert w > base_w
    # 見積もった右端が幅に収まる
    txt = [t for t in re.findall(r">([^<]*大階段[^<]*)</text>", drawing.elevation_svg(b2, "S"))][0]
    x = 70 + 70 * 14 + 26
    assert x + drawing._text_w(txt, 11) <= w


def test_many_evidence_is_folded_for_display_only():
    b = load()
    ev = [f"PXL_20261103_0429007{i:02d}" for i in range(5)]
    b["levels"][0]["z"]["evidence"] = list(ev)
    svg = drawing.elevation_svg(b, "S")
    assert f"[{ev[0]},{ev[1]} ほか3]" in svg
    assert ev[4] not in svg.split("未確定・撮影のお願い")[0]
    assert b["levels"][0]["z"]["evidence"] == ev        # JSON は変えない


def test_long_request_wraps_into_tspans():
    b = load()
    before = drawing.elevation_svg(b, "S")
    rq = next(r for r in b["requests"] if r.get("face") in ("S", "*"))
    rq["why"] = "とても長い理由の文。" * 40
    svg = drawing.elevation_svg(b, "S")
    w, h = _size(svg)
    _, h0 = _size(before)
    assert svg.count("<tspan") > before.count("<tspan") + 1
    assert h > h0
    for line in re.findall(r"<tspan[^>]*>([^<]*)</tspan>", svg):
        assert 70 + drawing._text_w(line, 11) <= w - 20 + 1e-6
    # 短い依頼は折り返さない（tspan を使わない）
    assert before.count("<tspan") == 0


def test_wrap_and_text_width():
    assert drawing._text_w("あa", 10) == 10 + 5.5
    lines = drawing._wrap("あ" * 25, 10, 100)
    assert len(lines) == 3 and lines[0] == "あ" * 10
    assert drawing._wrap("短い", 11, 500) == ["短い"]
