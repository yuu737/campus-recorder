"""組み立て部品 composite（11/5）。語彙に無い形を箱・くさび・円柱・柱体・円弧の壁で組む。

形ごとに 1 件（大きさ・位置）、cut、検証（まとめの形／素片ごとの形／仮説は不可）、立面と根拠の層。見本棟（parts_building.json）の 2 つも。
"""
import json
import math
from pathlib import Path

import pytest

from recorder import drawing, parts_geom as pg, schema

FIX = Path(__file__).parent / "fixtures" / "parts_building.json"


def load():
    return json.loads(FIX.read_text(encoding="utf-8"))


def faces(b):
    return {f["id"]: f for f in b["faces"]}


def comp(prims, face="S", along=2.0, **kw):
    d = {"type": "composite", "name": "テスト", "status": "estimated", "evidence": ["P1"], "method": "テスト", "tol": 0.3,
         "primitives": prims}
    if face:
        d.update(face=face, along=along)
    d.update(kw)
    return d


def bbox(prims):
    v = [q for pr in prims for q in pg.prim_vertices(pr, (0, 0, 1, 0, 0, 1))]
    return [(min(q[i] for q in v), max(q[i] for q in v)) for i in range(3)]


def errs(pt):
    b = load()
    b["parts"] = [pt]
    return schema.validate_building(b)


def test_box_on_face_offsets_by_along():
    b = load()
    fr, cut, pr = pg.part_prims(comp([{"shape": "box", "s": [0, 12], "d": [-2.0, 0], "z": [6.8, 7.1]}]), faces(b))
    assert fr == "S" and cut is None and len(pr) == 1
    (a0, a1), (n0, n1), (z0, z1) = bbox(pr)
    assert (a0, a1, n0, n1, z0, z1) == pytest.approx((2.0, 14.0, -2.0, 0.0, 6.8, 7.1))
    assert pr[0]["mat"] == "concrete"                       # 既定の素材
    assert pg.volume(pr[0]) == pytest.approx(12 * 2 * 0.3)


def test_wedge_rises_toward_rise_side_and_slab_with_t():
    b = load()
    _f, _c, pr = pg.part_prims(comp([{"shape": "wedge", "s": [0, 4], "d": [0, 2], "z": [0, 1], "rise": "+s"}], along=0), faces(b))
    w = pr[0]
    assert w["k"] == "ext_a" and pg.volume(w) == pytest.approx(0.5 * 4 * 1 * 2)
    assert max(z for a, z in w["pts"] if a == pytest.approx(4)) == pytest.approx(1)      # to 側（+s）が高い
    assert max(z for a, z in w["pts"] if a == pytest.approx(0)) == pytest.approx(0)
    _f, _c, pr2 = pg.part_prims(comp([{"shape": "wedge", "s": [0, 4], "d": [0, 2], "z": [0, 1], "rise": "-d", "t": 0.2}], along=0), faces(b))
    s2 = pr2[0]
    assert s2["k"] == "ext_n" and (s2["a0"], s2["a1"]) == (0.0, 4.0)
    assert pg.volume(s2) == pytest.approx(0.2 * 2 * 4)                                   # 斜めの板＝厚さ×奥行き×幅
    assert max(z for n, z in s2["pts"] if n == pytest.approx(0)) == pytest.approx(1)     # -d：d の小さい側が高い


def test_cylinder_and_prism():
    b = load()
    _f, _c, pr = pg.part_prims(comp([{"shape": "cylinder", "at": [3, 1], "r": 0.3, "z": [0, 4], "seg": 24},
                                     {"shape": "prism", "polygon_sd": [[0, 0], [2, 0], [1, 1.5]], "z": [0, 1]}], along=1), faces(b))
    cyl, tri = pr
    assert len(cyl["pts"]) == 24
    (a0, a1), (n0, n1), _z = bbox([cyl])
    assert ((a0 + a1) / 2, (n0 + n1) / 2, a1 - a0) == pytest.approx((4.0, 1.0, 0.6), abs=1e-6)
    assert pg.area(tri["pts"]) == pytest.approx(1.5)                                     # 三角 2×1.5/2
    assert pg.volume(tri) == pytest.approx(1.5)                                          # × 高さ 1
    assert min(q[0] for q in tri["pts"]) == pytest.approx(1.0)                           # along 1 だけずれる


def test_arc_wall_segments_and_volume():
    b = load()
    _f, _c, pr = pg.part_prims(comp([{"shape": "arc_wall", "center": [5, 0], "r": 3.0, "deg": [0, 90], "z": [0, 2], "t": 0.2}], along=0), faces(b))
    assert len(pr) == 9                                                                    # 90° を 10° 以下 → 9 区画
    vol = sum(pg.volume(p) for p in pr)
    exact = (math.pi / 2) * 3.0 * 0.2 * 2                                                  # 中心線の弧長 × 厚さ × 高さ
    assert vol == pytest.approx(exact, rel=0.01)
    (_a0, a1), (_n0, n1), _z = bbox(pr)
    assert (a1, n1) == pytest.approx((5 + 3.1, 3.1))


def test_world_frame_aliases_and_cut():
    b = load()
    pt = comp([{"shape": "box", "x": [16, 19], "y": [9, 12.1], "z": [0, 3], "cut": True},
               {"shape": "box", "x": [16, 19], "y": [10, 10.3], "z": [2.6, 3], "material": "white"},
               {"shape": "prism", "polygon_xy": [[17, 10.5], [17.3, 10.5], [17.3, 10.8]], "z": [0, 2.6]}], face=None)
    fr, cut, pr = pg.part_prims(pt, faces(b))
    assert fr == "world"
    cl = pg.cut_list(cut)
    assert len(cl) == 1 and cl[0]["mat"] is None and pg.volume(cl[0]) == pytest.approx(3 * 3.1 * 3)
    assert [p["mat"] for p in pr] == ["white", "concrete"]
    assert errs(pt) == []


def test_unknown_values_skip_only_that_primitive_and_unknown_status_skips_all():
    b = load()
    u = {"value": None, "status": "unknown"}

    def m(v):
        return {"value": v, "status": "measured", "evidence": ["P1"]}

    pt = {"type": "composite", "name": "x", "face": "S",
          "primitives": [{"shape": "box", "s": [m(1.0), m(3.0)], "d": [m(0.0), m(1.0)], "z": [m(0.0), m(1.0)]},
                         {"shape": "box", "s": [m(1.0), m(2.0)], "d": [m(0.0), m(1.0)], "z": [u, m(1.0)]}]}
    log = []

    def V(mm, d=None, what="", why=""):
        v = pg.plain(mm, d)
        if what and pg.plain(mm) is None:
            log.append(what)
        return v

    _f, _c, pr = pg.part_prims(pt, faces(b), V)
    assert len(pr) == 1 and any("primitives[1" in w for w in log)
    assert errs(pt) == []                                      # 素片ごとの形：unknown は可
    _f, _c, pr2 = pg.part_prims(comp([{"shape": "box", "s": [0, 1], "d": [0, 1], "z": [0, 1]}], status="unknown"), faces(b))
    assert pr2 == []


def test_validation_group_form_and_per_value_form():
    box = {"shape": "box", "s": [0, 1], "d": [0, 1], "z": [0, 1]}
    assert errs(comp([box])) == []
    # まとめの形でない（status が無い）のに裸の数 → 根拠の無い値
    bare = {"type": "composite", "name": "x", "face": "S", "primitives": [box]}
    assert any("裸の数値" in e for e in errs(bare))
    # estimated でも根拠の写真が要る／推定の仕方が要る
    assert any("evidence" in e for e in errs(comp([box], evidence=[])))
    assert any("method" in e for e in errs(comp([box], method="")))
    # measured は根拠が要る
    assert any("根拠" in e for e in errs(comp([box], status="measured", evidence=[])))
    # 部品は仮説で作らない
    assert any("仮説" in e for e in errs(comp([box], basis="hypothesis")))
    # 形の名前・必須の鍵・rise・cut の型・多角形
    assert any("shape は" in e for e in errs(comp([{**box, "shape": "sphere"}])))
    assert any("r がありません" in e for e in errs(comp([{"shape": "cylinder", "at": [0, 0], "z": [0, 1]}])))
    assert any("rise は" in e for e in errs(comp([{**box, "shape": "wedge", "rise": "up"}])))
    assert any("cut は" in e for e in errs(comp([{**box, "cut": "yes"}])))
    assert any("polygon_sd" in e for e in errs(comp([{"shape": "prism", "polygon_sd": [[0, 0], [1, 0]], "z": [0, 1]}])))
    # 素片ごとの形：center（座標の鍵）も寸法として調べる
    m = {"value": 1.0, "status": "estimated", "method": "t"}
    arc = {"shape": "arc_wall", "center": [0, 0], "r": m, "deg": [m, m], "z": [m, m], "t": m}
    assert any("center" in e and "裸の数値" in e for e in errs({"type": "composite", "name": "x", "face": "S", "primitives": [arc]}))
    # 名前が無い・素片が無い・面が無い
    assert any("name" in e for e in errs(comp([box], name="")))
    assert any("primitives" in e for e in errs(comp([])))
    assert any("faces にありません" in e for e in errs(comp([box], face="Z")))
    # asset の素材は台帳を見る
    assert any("no_such_thing" in e for e in errs(comp([{**box, "material": "asset:no_such_thing"}])))


def test_part_state_of_group_form():
    assert schema.part_state(comp([{"shape": "box", "s": [0, 1], "d": [0, 1], "z": [0, 1]}])) == "estimated"
    assert schema.part_state(comp([], status="measured")) == "measured"
    assert schema.part_state(comp([], status="unknown")) == "unknown"


def test_fixture_composites_validate_and_draw():
    b = load()
    assert schema.validate_building(b) == []
    deck, pil = [p for p in b["parts"] if p["type"] == "composite"]
    assert (deck["name"], pil["name"]) == ("デッキ", "ピロティの天井の梁")
    fr, cut, pr = pg.part_prims(deck, faces(b))
    assert fr == "W" and cut is None and {"slab", "parapet", "coping", "post0", "post1"} <= {p["name"] for p in pr}
    fr2, cut2, pr2 = pg.part_prims(pil, faces(b))
    assert fr2 == "world" and len(pg.cut_list(cut2)) == 1 and len(pr2) == 3
    ev = schema.evidence_summary(b)["parts"]
    assert ev[schema.part_name(deck, b["parts"].index(deck))] == "estimated"
    w = drawing.elevation_svg(b, "W")
    assert w.count('data-part="composite"') >= 5                 # デッキの素片が西の立面に
    n = drawing.elevation_svg(b, "N")
    assert 'data-part="composite"' in n                          # ピロティ（世界座標）は北の立面に手前の物として
    assert 'data-part="composite"' in drawing.plan_svg(b)        # 世界座標の組み立ては平面にも
