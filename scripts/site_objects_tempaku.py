"""天白の敷地の物を 2 つ置く（11/8 F9、Fable の決定）。何度流しても同じ結果（同じ id は差し替え）。

  1. 渡り廊下 S3（corridor-tower75-west）：タワー75 の f2 から西へ、大階段の上を渡る灰色の金属パネルの箱。
     足元は OSM way/1304077323（PLATEAU の箱 bldg_f693ff16 と 99.9 % 同じ）。床と箱の高さは観察票 S3 の比（写真）とタワー75 の階。
  2. 図書館の北西の甲板（deck-library-nw）：PLATEAU の箱 bldg_2e2a316f（＝OSM way/1304081004）から、
     タワー75 の輪郭と部品（西のデッキ S1・大階段 S4 など）と通路橋 bridge-library-tower75 を引いた残り。
     床の標高は図書館側 48.27 とタワー側 50.13 が食い違う争点なので unknown（決めない）。争点は敷地 JSON の requests に書く。
  3. 北の沈んだ広場から上がる大階段（steps-tower75-north-plaza）：タワー75 の北西のテラス S6 の西脇、B1 の広場から北の地面へ。
     段数・下の高さは写真と観察票から estimated、平面と上の高さは読めないので unknown と撮影依頼（11/8 Fable の判断を受けて F9）。

値の決まりは棟と同じ（measured／estimated／unknown、根拠は写真 id）。書くのは site_objects.write_objects（検証が通った時だけ）。
使い方: .venv\\Scripts\\python.exe -I -X utf8 scripts/site_objects_tempaku.py
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from recorder import site, site_objects as so  # noqa: E402

SITE = ROOT / "data" / "site" / "tempaku.json"
BY = "F9（直す係、Claude Opus 5.5）"

# 写真（索引 data/photos/index.json の id。全部 period during）
S3_PHOTOS = ["PXL_20261103_070114062", "PXL_20261103_070134689", "PXL_20261105_061241221", "PXL_20261105_061836019"]
S3_INSIDE = ["PXL_20261105_061241221", "PXL_20261105_061836019", "PXL_20261105_061836600", "PXL_20261105_061837232",
             "PXL_20261103_070204974", "PXL_20261105_061034445"]   # 視点が箱 f693ff16 の足元の中（敷地の検収係 11/8）
DECK_PHOTOS = ["PXL_20261105_063449740", "PXL_20261105_063455681", "PXL_20261105_063540463", "PXL_20261103_075705849",
               "PXL_20261105_063106256"]
DECK_ON = ["PXL_20261105_063413358", "PXL_20261105_063418811", "PXL_20261105_063449740", "PXL_20261105_063455681",
           "PXL_20261105_063529987", "PXL_20261103_075705849", "PXL_20261103_075710233", "PXL_20261103_075718159",
           "PXL_20261105_063106256", "PXL_20261105_063115879.MP", "PXL_20261105_063548675"]   # 登録済みの視点が甲板の上


def m(value, unit, tol, status, evidence, method, note="", ref=None):
    out = {"value": value, "unit": unit, "tol": tol, "status": status, "evidence": evidence, "method": method, "note": note}
    if ref:
        out["ref"] = ref
    return out


def unknown(unit, note):
    return {"value": None, "unit": unit, "tol": None, "status": "unknown", "evidence": [], "method": "", "note": note}


def level_z(b: dict, prefix: str) -> dict:
    return next(lv["z"] for lv in b["levels"] if lv["name"].startswith(prefix))


def corridor(doc: dict, tower: dict) -> dict:
    osm = next(sb for sb in doc["buildings"] if sb["osm"] == "way/1304077323")
    ring = osm["footprint_osm"][0]
    ring = ring[:-1] if ring[0] == ring[-1] else ring
    gz = tower["ground_z"]
    z2, z3 = level_z(tower, "2F"), level_z(tower, "3F")
    floor = round(gz["value"] + z2["value"], 2)
    floor_tol = round(math.hypot(z2["tol"], gz["tol"]), 2)
    pitch = round(z3["value"] - z2["value"], 2)
    return {
        "id": "corridor-tower75-west", "type": "corridor",
        "name": "渡り廊下 S3（タワー75 の西面 f2 の 2F から西へ、大階段の上を渡る灰色の金属パネルの箱）",
        "evidence": S3_PHOTOS + [p for p in S3_INSIDE if p not in S3_PHOTOS],
        "seen": ("灰色の金属パネルの箱に横長の窓、四角い柱 2 本ずつで支え、西の大階段とデッキの上を渡る（070114062・061241221）。"
                 "タワー75 の側は f2 の 2F に付き、西の端は範囲外の棟へ続く。観察票 data/reports/tower75/kansatsu_summary.json の S3。"
                 "PLATEAU の箱 bldg_f693ff16（69.5 m）はこの足元で、高さだけ塔の値が付いている（敷地の検収係 11/8："
                 "視点が箱の中の写真 6 枚、061241221 で箱の上は空）"),
        "polygon": m(ring, "m", 1.0, "estimated", ["osm:way/1304077323", "plateau:bldg_f693ff16"] + S3_PHOTOS[:2],
                     "OSM way/1304077323 の多角形そのまま（PLATEAU の箱 bldg_f693ff16 の足元と 99.9 % 重なる、F6）。"
                     "070114062・070134689 で渡り廊下が f2 から西へ、幅は塔の窓の列 2 つ分ほどの帯で延びる。± は OSM の目安",
                     note="柱（四角い柱 2 本ずつ）は置いていない"),
        "floor_z": m(floor, "m", floor_tol, "estimated", S3_PHOTOS,
                     f"観察票 S3『床はデッキ階の 1 つ上（2F）の床の高さ』（写真の比）。タワー75 の ground_z {gz['value']}＋2F の白帯 "
                     f"{z2['value']}（levels）＝{floor}。± は 2F の ± と ground_z の ± の二乗和",
                     ref="tower75:levels[2F].z"),
        "height": m(pitch, "m", round(pitch * 0.3, 2), "estimated", S3_PHOTOS,
                    f"観察票 S3『箱の高さは 1 段分』（写真の比）。タワー75 の 2F→3F の白帯の差 {z3['value']}−{z2['value']}＝{pitch} m。"
                    "± は比の読みの 30 %",
                    ref="tower75:levels[3F].z−levels[2F].z"),
        "material": "metal",
        "requests": [{"kind": "photo", "status": "open", "by": "F9", "to": "staff",
                      "what": "渡り廊下の側面を真横から 1 枚（塔の窓の列と箱の上下が 1 枚に入るように）と、下面と柱を見上げる 1 枚",
                      "where": "西の大階段の下の広場（渡り廊下の南の脇）",
                      "why": "床・箱の高さは観察票の比（2F・1 段分）だけ。正面化して塔の階高で測れば estimated の幅を狭められる"}],
    }


def deck(doc: dict, bs: dict) -> dict:
    from shapely.geometry import Polygon
    pl = {p["gml_id"]: p for p in json.loads((ROOT / "data" / "site" / "plateau.json").read_text(encoding="utf-8"))["buildings"]}
    box = next(g for g in pl if g.startswith("bldg_2e2a316f"))
    P = site._poly(pl[box]["footprint"])
    T = site.outline_shapes(bs["tower75"])["all"]
    br = next(o for o in so.objects_of(doc) if o["id"] == "bridge-library-tower75")
    Q = site.object_shape(br, bs)
    rest = P.difference(T).difference(Q)
    pieces = sorted(getattr(rest, "geoms", [rest]), key=lambda g: -g.area)
    g = pieces[0].simplify(0.05)
    ring = [[round(x, 2), round(y, 2)] for x, y in list(g.exterior.coords)[:-1]]
    area = Polygon(ring).area
    left = sum(p.area for p in pieces[1:])
    return {
        "id": "deck-library-nw", "type": "deck",
        "name": "図書館の北西の甲板（赤れんがの床、通路橋とタワー75 の西のデッキの間）",
        "evidence": DECK_PHOTOS + [p for p in DECK_ON if p not in DECK_PHOTOS],
        "seen": ("赤れんがの床の広い甲板。縁に金属の手すりと低い腰壁、青い鉄骨の庇の柱が並ぶ（063449740・063455681）。"
                 "図書館の正面入口 f2 の前から北西へ広がり、通路橋とタワー75 の西のデッキに続く。登録済みの図書館の写真 11 枚の視点がこの上"
                 "（図書館の地面から 9.0〜10.4 m、敷地の検収係 11/8）"),
        "polygon": m(ring, "m", 1.0, "estimated", ["osm:way/1304081004", "plateau:bldg_2e2a316f"] + DECK_PHOTOS[:2],
                     f"PLATEAU の箱 bldg_2e2a316f（＝OSM way/1304081004、100 %）から、タワー75 の輪郭と部品（西のデッキ S1・大階段 S4 など、"
                     f"棟 JSON の parts）と通路橋 bridge-library-tower75 の平面を引いた残りの一番大きい塊（{area:.1f} m²。"
                     f"残りの小片 {left:.1f} m² は捨てた）。1 つの物は棟か敷地の 1 か所（K-005）。縁の形は OSM のまま",
                     note="図書館の部品（風除室・庇）はこの床の上に立つ物で、床とは別"),
        "z": unknown("m", "床の標高が 2 つの棟で食い違う（図書館側 41.67＋6.6＝48.27、タワー75 の西のデッキ 42.9＋7.23＝50.13、差 1.86 m）。"
                          "どちらにも決めない（敷地 JSON の requests: deck-library-nw-z）"),
        "material": "brick",
        "requests": [{"kind": "data", "status": "open", "by": "F9", "to": "soten",
                      "what": "甲板の床の標高を決める（図書館側 48.27 と タワー75 側 50.13 が 1.86 m 違う）",
                      "where": "data/buildings/library.json（DECK_Z 6.6・ground_z 41.67）と tower75.json（levels 1F 7.23・ground_z 42.9）",
                      "why": "写真では甲板・通路橋・西のデッキは段差なしで続く（063412307・063439559・070409570）。どちらかの棟の ground_z か床の高さが違う"},
                     {"kind": "photo", "status": "open", "by": "F9", "to": "staff",
                      "what": "甲板の縁から下の道へ、メジャーを笠木から道の面まで垂らして 1 枚（目盛りの 0 と上端が写るように）",
                      "where": "甲板の北西の縁（通路橋の西の脇）",
                      "why": "甲板の床の標高を道の面（5m 標高）から測り、2 つの棟のどちらが合うかを決める"}],
    }


STAIR_PHOTOS = ["PXL_20261103_065946407", "PXL_20261103_070003022"]


def plaza_steps(tower: dict) -> dict:
    gz = tower["ground_z"]
    b1 = 2.86      # 棟 JSON の parts「北西のテラスと下の B1 のガラスの入口（S6・S7）」の front_wall の z0（観察票 S7：デッキより 1 段低い）
    z0 = round(gz["value"] + b1, 2)
    return {
        "id": "steps-tower75-north-plaza", "type": "steps",
        "name": "北の沈んだ広場の大階段（タワー75 の北西のテラスの西脇、B1 の広場から北の地面へ）",
        "evidence": STAIR_PHOTOS,
        "seen": ("065946407・070003022（北の広場から南を向いて）：テラスの前面の壁と B1 のガラスの入口の右（西）に、"
                 "沈んだ広場から北の地面へ上がる幅の広い段（踏み面の広い段が並ぶ）と、壁沿いの手すり付きの細い段。上の段の先は北の地面で人が立つ。"
                 "PLATEAU の箱 bldg_f2cc5f1b（74.2 m）の足元は、テラス（棟 JSON の parts）と、この広場・大階段を含むと見る（テラスで 52.5 %）"),
        "polygon": unknown("m", "平面が読めない：065946407・070003022 は登録（視点）が無く、GPS だけ。OSM にも段（highway=steps）が無い。"
                                "PLATEAU の箱の輪郭は広場（平らな床）と大階段を分けないので、段の縁の位置にはしない（決めつけない）"),
        "z0": m(z0, "m", round(math.hypot(1.3, gz["tol"]), 2), "estimated", STAIR_PHOTOS + ["PXL_20261103_070044607"],
                f"下の段＝B1 の広場の床。タワー75 の ground_z {gz['value']}＋B1 {b1}（棟 JSON の S6・S7 の front_wall の下端。"
                "観察票 S7『デッキより 1 段低い』：7.23 − 4.37）。± は 1 段の読みの 30 % と ground_z の ± の二乗和",
                ref="tower75:parts[S6・S7].front_wall.z0"),
        "z1": unknown("m", "上の段＝北の地面の標高。写真に物差しが無く、5m 標高は沈んだ広場と混ざる"),
        "count": m(8, "段", 2, "estimated", STAIR_PHOTOS[:1],
                   "065946407 を拡大して広い段の縁の線を数えた（8）。070003022 は遠く斜めで数え切れない（2 枚で一致していないので estimated、± 2 段）"),
        "material": "stone",
        "requests": [{"kind": "photo", "status": "open", "by": "F9", "to": "staff",
                      "what": "大階段を真横（東の広場の床）から 1 枚：段の全部と上下の縁が入るように。メジャーを 1 段の蹴上げに当てて 1 枚",
                      "where": "タワー75 の北の沈んだ広場（B1 のガラスの入口の前）",
                      "why": "z1（上の地面の標高）と段数を写真で決める（蹴上げ×段数）"},
                     {"kind": "photo", "status": "open", "by": "F9", "to": "staff",
                      "what": "大階段の上の縁と下の縁の四隅が入るように、北の地面の上から見下ろして 2 枚（タワー75 の f0・f1 の角が同じ写真に入る所から）",
                      "where": "大階段の北の地面（上の段の先）",
                      "why": "平面（polygon）が unknown。塔の角を目印に視点を解けば縁の位置が読める"}],
    }


def main() -> int:
    doc = json.loads(SITE.read_text(encoding="utf-8"))
    bs = so.load_buildings(["tower75", "library"])
    new = [corridor(doc, bs["tower75"]), deck(doc, bs), plaza_steps(bs["tower75"])]
    objs = dict(doc.get("objects") or {"schema": so.SCHEMA, "items": []})
    ids = {o["id"] for o in new}
    objs["items"] = [o for o in objs.get("items", []) if o["id"] not in ids] + new
    errs = so.write_objects(SITE, objs, bs, so.load_catalog_ids())
    if errs:
        for e in errs:
            print("   - " + e)
        return 1
    # 争点（甲板の床の標高）を敷地 JSON の requests に（敷地の検収係の形）。同じ id は差し替え
    doc = json.loads(SITE.read_text(encoding="utf-8"))
    rid = "deck-library-nw-z"
    req = {"id": rid, "status": "open", "action": "soten", "fix_in": "soten", "buildings": ["library", "tower75"],
           "object": "deck-library-nw",
           "what": ("図書館の北西の甲板（deck-library-nw）の床の標高が 2 つの棟で食い違う：図書館の甲板 41.67＋6.6＝48.27、"
                    "タワー75 の西のデッキ 42.9＋7.23＝50.13（差 1.86 m）。写真では甲板・通路橋・西のデッキは段差なしで続く。"
                    "甲板の z は unknown にして描かない（決めない）"),
           "ask": "どちらかの棟の ground_z か床の高さを写真（メジャー）で決める。決まれば deck-library-nw.z と通路橋 floor_z を直す",
           "why": "通路橋の floor_z は 2 つの値の重み付き平均で置いてある（±1.5）。甲板まで同じ扱いにすると食い違いが見えなくなる",
           "evidence": ["PXL_20261105_063412307", "PXL_20261105_063439559", "PXL_20261103_070409570",
                        "library:design_library.DECK_Z", "tower75:levels[1F].z"],
           "by": BY, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    old = next((r for r in doc.get("requests") or [] if r.get("id") == rid), None)
    if old:
        req["at"] = old.get("at", req["at"])
    doc["requests"] = [r for r in doc.get("requests") or [] if r.get("id") != rid] + [req]
    from recorder.ledger import write_text_atomic
    write_text_atomic(SITE, json.dumps(doc, ensure_ascii=False, indent=1))
    for o in new:
        pv = o["polygon"]["value"]
        from shapely.geometry import Polygon
        print(f"{o['id']}（{o['type']}）: 足元 {(f'{Polygon(pv).area:.1f} m²') if pv else 'unknown'}、"
              + "、".join(f"{k} {o[k]['value']}（{o[k]['status']}）" for k in ("floor_z", "height", "z", "z0", "z1", "count") if k in o))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
