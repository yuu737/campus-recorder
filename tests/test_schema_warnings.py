"""棟 JSON の警告（± の下限・採用値の照合・根拠 id の実在）。警告はエラーにしない（既存の棟を落とさない）。"""
import json
from pathlib import Path

from recorder import schema

FIX = Path(__file__).parent / "fixtures" / "sample_building.json"


def load():
    return json.loads(FIX.read_text(encoding="utf-8"))


def _m(value, tol, method, evidence=("PXL_1",), unit="m"):
    return {"value": value, "unit": unit, "tol": tol, "status": "measured", "evidence": list(evidence), "method": method}


def test_tol_floor_by_ruler():
    ratios = {"plateau": 0.03, "osm": 0.05, "tile": 0.005 / 0.07, "tape": 0.01}
    b = {"faces": [{"id": "S",
                    "length": _m(50.0, 1.0, "PLATEAU の足元の辺"),                     # 下限 1.5 → 警告
                    "ground_offset": _m(50.0, 1.6, "PLATEAU の足元の辺")},             # 1.6 ≧ 1.5 → 通る
                   {"id": "N", "length": _m(30.0, 0.2, "測った", ["osm:way/123"])},   # evidence が OSM → 下限 1.5
                   {"id": "E", "length": _m(10.0, 0.11, "メジャーとタイルで検算")},     # 細かい方（tape 1%）→ 0.1 で通る
                   {"id": "W", "length": _m(10.0, 0.05, "目視")}],                     # 物差しが分からない → 調べない
         "floor_count": _m(5, 0, "PLATEAU", unit="階")}                                # m でない → 調べない
    w = schema.warn_tol_floor(b, ratios)
    assert len(w) == 2, w
    assert any(x.startswith("faces[S].length") and "PLATEAU" in x for x in w)
    assert any(x.startswith("faces[N].length") and "OSM" in x for x in w)
    # 警告はエラーに入らない
    fx = load()
    fx["parapet_top"]["method"] = "PLATEAU の高さ"
    fx["parapet_top"]["tol"] = 0.01
    assert schema.validate_building(fx) == []
    assert any("parapet_top" in x for x in schema.warn_tol_floor(fx, ratios))


def test_adopted_value_mismatch():
    b = {"floor_pitch": _m(4.52, 0.2, "y 554・551 px、採用 552.5 px。縮尺 2 つ。採用は 2 つの平均 4.52 m"),
         "parapet_top": _m(4.30, 0.2, "採用 4.19 m"),                    # 2.6% 違う → 警告
         "ground_z": _m(4.21, 0.2, "採用は平均 4.19"),                    # 0.5% → 通る
         "levels": [{"id": "1F", "z": _m(0.07, 0.01, "採用 70 mm")}]}     # mm を m に直して一致
    assert schema.adopted_values("採用 552.5 px（窓）。採用は 2 つの平均 4.52 m") == [4.52]
    w = schema.warn_adopted(b)
    assert len(w) == 1 and w[0].startswith("parapet_top") and "4.19" in w[0], w


def test_evidence_ids_exist(tmp_path):
    (tmp_path / "data" / "reports").mkdir(parents=True)
    (tmp_path / "data" / "reports" / "ok.json").write_text("{}", encoding="utf-8")
    ev = ["PXL_A", "plateau:bldg_x", "osm:way/163518234", "osm:relation/9", "gsi:dem5a",
          "data/reports/ok.json", "osm:way/tower75", "kansatsu_f18", "data/reports/missing.json",
          "out/t/a.json", "out/t/b.json", "PXL_UNLISTED"]
    b = {"faces": [{"id": "S", "length": _m(10.0, 0.5, "x", ev)}],
         "footprint": {"status": "measured", "evidence": ["kansatsu_f18"], "polygon": []}}
    w = schema.warn_evidence_ids(b, {"PXL_A"}, tmp_path)
    joined = "\n".join(w)
    for bad in ("osm:way/tower75", "kansatsu_f18", "data/reports/missing.json", "PXL_UNLISTED"):
        assert bad in joined, bad
    for good in ("PXL_A ", "plateau:bldg_x", "osm:way/163518234", "osm:relation/9", "gsi:dem5a", "ok.json"):
        assert good not in joined, good
    assert "（2 か所）" in next(x for x in w if "kansatsu_f18" in x)        # 同じ id は 1 件にまとめる
    outs = [x for x in w if x.startswith("out/ の根拠")]
    assert len(outs) == 1 and "2 か所" in outs[0]                         # out/ はまとめて 1 件
    assert len(w) == 5
    # 索引が無い時は PXL_ を通す
    assert not any("PXL_UNLISTED" in x for x in schema.warn_evidence_ids(b, set(), tmp_path))
