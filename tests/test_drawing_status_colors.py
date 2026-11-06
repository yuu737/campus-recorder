"""立面図の下の柱間（スパン）の色が根拠の段に従うこと（11/6）。
黒＝measured、琥珀＝estimated、赤の破線＝unknown。以前は柱間が赤に固定で、本部棟の推定の柱間 5.52 が未確定に見えた。"""
import json
import re
from pathlib import Path

from recorder import drawing

FIX = Path(__file__).parent / "fixtures" / "sample_building.json"


def _span_marks(svg: str, value: str):
    """柱間の数字の文字の色と、柱間の線（data-status 付き）の stroke と破線。"""
    txt = re.findall(rf'<text [^>]*fill="(#[0-9a-f]{{3,6}})"[^>]*>{re.escape(value)}</text>', svg)
    lines = re.findall(r'<line [^>]*data-status="(\w+)" stroke="(#[0-9a-f]{3,6})"( stroke-dasharray="[^"]*")?', svg)
    return txt, lines


def test_span_color_follows_pitch_status():
    for status, col, dashed in (("measured", "#222", False), ("estimated", "#b07a10", True)):
        b = json.loads(FIX.read_text(encoding="utf-8"))
        p = b["faces"][0]["bays"]["pitch"]
        p["status"] = status
        if status == "estimated":
            p["evidence"], p["method"] = [], "スパン数÷面の長さ"
        svg = drawing.elevation_svg(b, "S")
        txt, lines = _span_marks(svg, "4.80")
        assert txt and set(txt) == {col}, (status, txt)
        assert lines and {(s, c) for s, c, _ in lines} == {(status, col)}
        assert all(bool(d) == dashed for _, _, d in lines)
        assert "#c8322b" not in txt                         # 赤（unknown）では描かない
