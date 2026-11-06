"""値の個数と独立した観察の個数（recorder/evidence.py の observations）。手で作った小さな棟 JSON で確かめる。"""
from __future__ import annotations

import json
from pathlib import Path

from recorder import evidence as E
from recorder.run import count_statuses

ROOT = Path(__file__).resolve().parents[1]


def _v(value, status="estimated", method="目視", evidence=("P1", "P2")):
    return {"value": value, "unit": "m", "status": status, "evidence": list(evidence), "method": method, "note": ""}


def _ledge(z):
    return {"name": "帯", "z": _v(z, method="帯の中心から"), "depth": _v(0.1), "height": _v(0.22, method="帯の見付け 12 px（P1）")}


def _building():
    return {
        "id": "t", "name": "試し",
        "height": _v(20.0, status="measured", method="段数 × 階高", evidence=("P9",)),
        "faces": [
            {"id": "A", "ledges": [_ledge(3.0), _ledge(6.0), _ledge(9.0)],
             "top_z": {"value": None, "status": "unknown", "evidence": [], "method": "", "note": ""}},
            {"id": "B", "ledges": [_ledge(3.0)], "length": _v(10.0, method="足元の辺の長さ", evidence=("osm",))},
        ],
        "requests": [{"status": "open", "what": "撮影依頼は数えない"}],
    }


def test_three_copies_bundle_into_one_observation():
    o = E.observations(_building())
    a = o["faces"]["A"]
    # 面 A：帯 3 つ × 3 値＝9 値＋top_z 1 値。帯の値は 3 つの複製が 1 つの観察に束なる（z は値が違っても方法と根拠が同じなので 1 つ）
    assert a["values"] == 10
    assert a["observations"] == 4          # z・depth・height・top_z
    assert a["derived"] == 6


def test_totals_match_existing_counts():
    b = _building()
    o = E.observations(b)
    t = E.status_counts(b)["total"]
    assert o["values"] == sum(t.values())
    for s in E.STATUSES:
        assert o["by_status"][s]["values"] == t.get(s, 0)
    o2 = E.observations(b, values_only=True)
    c = count_statuses(b)
    assert o2["values"] == sum(c.values())
    assert {s: o2["by_status"][s]["values"] for s in E.STATUSES} == c


def test_counts_across_faces_and_eyeball():
    o = E.observations(_building())
    # 全体：height（measured）1、帯の 3 種（面 A と B で同じ鍵）3、top_z（unknown）1、length 1 → 観察 6
    assert o["values"] == 1 + 12 + 1 + 1
    assert o["observations"] == 6
    assert o["by_status"]["measured"] == {"values": 1, "observations": 1}
    assert o["by_status"]["unknown"] == {"values": 1, "observations": 1}
    assert o["by_status"]["estimated"] == {"values": 13, "observations": 4}
    assert o["derived"] == o["values"] - o["observations"] == 9
    assert o["eyeball"] == {"values": 4, "observations": 1}     # depth の「目視」4 つは 1 回の観察
    top = o["top_copies"][0]
    assert top["copies"] == 4 and top["kind"].startswith("faces[].ledges[].")
    assert o["other"]["values"] == 1 and o["other"]["observations"] == 1


def test_different_evidence_is_a_different_observation():
    b = _building()
    b["faces"][1]["ledges"][0]["depth"]["evidence"] = ["P7"]      # 別の写真で見た帯の出
    o = E.observations(b)
    assert o["observations"] == 7
    assert o["eyeball"] == {"values": 4, "observations": 2}


def test_line_and_summary_text_have_observations():
    o = E.observations(_building())
    line = E.observations_line(o)
    assert line.startswith("値 measured 1 / estimated 13 / unknown 1")
    assert "独立した観察 measured 1 / estimated 4 / unknown 1（複製 9）" in line
    pl = {"name": "試し", "building": "t", "counts": E.status_counts(_building()), "observations": o,
          "photos": [], "faces": ["A", "B"]}
    md = E.summary_text(pl)
    assert "独立した観察" in md and E.OBS_NOTE in md and "| 観察 |" in md
    assert "| A | 0 | 9 | 1 | 4 |" in md


def test_tower75_observations_far_fewer_than_values():
    p = ROOT / "data" / "buildings" / "tower75.json"
    o = E.observations(json.loads(p.read_text(encoding="utf-8")))
    assert o["values"] > 1000
    assert o["observations"] < o["values"] / 4
