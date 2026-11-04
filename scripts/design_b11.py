"""11 号館（b11）の土台。2026-11-04 仕分け係（公式ガイドブック照合）。

分かったこと（out/b11/shiwake_公式図照合.md）
    - 公式ガイドブック（data/reference/guidebook_C.pdf、4-23・4-24「11号館 配置図」）で、11 号館は
      「斜めの低い翼（屋上が庭園）」と「まっすぐな 5 階の翼」がつながった 1 つの建物と分かった。
      図は名称と有無・大まかな位置の確認にだけ使い、形・寸法・座標はここに入れていない。
    - 台帳で「11 号館」の写真 17 枚（白い 5 階の棟、看板の文言が図の 1 階の部屋名と一致）は 5 階の翼。
    - 元の足元 PLATEAU bldg_b5e26f45（高さ 12 m、OSM「11号館」1 階、屋上緑地）は別の建物ではなく、同じ 11 号館の低い翼。

なので
    - 足元（footprint）と面は、写真のある 5 階の翼 = PLATEAU bldg_326ebceb（OSM way/163518234、5 階・27.1 m）に差し替える。
    - 低い翼は塊（blocks）の N として PLATEAU の形と高さのまま残す。面はまだ起こさない（写真が無い）。
    - 寸法はすべて PLATEAU / OSM / 5 m 標高の estimated。写真で測った値はまだ無い。
"""
import json, sys
from pathlib import Path
sys.path.insert(0, ".")
from recorder import skeleton
from recorder.schema import save_building, validate_building

PL_S = "bldg_326ebceb-417f-45a5-9633-710933a76db7"   # 5 階の翼（OSM way/163518234）
PL_N = "bldg_b5e26f45-552b-49bf-81ba-06e890b7ef76"   # 低い翼（OSM way/163518240「11号館」）
SHIWAKE = "out/b11/shiwake_公式図照合.md"
PHOTOS_5F = ["期間前の写真（削除）", "期間前の写真（削除）"]   # 窓 5 段が一致（期間前）

plateau = {p["gml_id"]: p for p in json.loads(Path("data/site/plateau.json").read_text(encoding="utf-8"))["buildings"]}

b = skeleton.from_site_cli("b11", [PL_S])
b["footprint"]["evidence"] += ["osm:way/163518234", SHIWAKE]
b["footprint"]["note"] = ("11 号館の 5 階の翼。足元を低い翼（PLATEAU bldg_b5e26f45）から差し替え。"
                          "どの翼が 11 号館かの確認だけ公式ガイドブックで行った（形・寸法は図から取っていない）")
fc = b["floor_count"]
fc["evidence"] = fc["evidence"] + PHOTOS_5F
fc["method"] = "PLATEAU の storeysAboveGround 5 と、写真 A222・A226 で窓 5 段が一致（期間前の写真。屋上の塔屋は写らず不明）"


def ring(gml):
    return [list(p) for p in plateau[gml]["footprint"]]


b["blocks"] = [
    {"id": "S", "name": "5 階の翼（写真の白い棟。足元と同じ）", "polygon": b["footprint"]["polygon"],
     "top_z": {"value": plateau[PL_S]["height_m"], "unit": "m", "tol": 1.5, "status": "estimated",
               "evidence": [f"plateau:{PL_S}"], "method": "PLATEAU の measuredHeight（塔屋を含む最高高さ）", "note": ""}},
    {"id": "N", "name": "低い翼（屋上が緑地。写真はまだ無い）", "polygon": ring(PL_N),
     "top_z": {"value": plateau[PL_N]["height_m"], "unit": "m", "tol": 2.5, "status": "estimated",
               "evidence": [f"plateau:{PL_N}", "osm:way/163518240"],
               "method": "PLATEAU の measuredHeight",
               "note": f"PLATEAU の地面が 5 階の翼より {plateau[PL_N]['base_z'] - plateau[PL_S]['base_z']:.2f} m 高い（坂）。"
                       "棟の地面は 1 つなので tol を広げた。2 つの翼の間 0.6 m は PLATEAU のまま埋めていない"}},
]
from shapely.geometry import Polygon
_cn = Polygon(ring(PL_N)).centroid
_near = min(b["faces"], key=lambda f: (((f["from"][0] + f["to"][0]) / 2 - _cn.x) ** 2 + ((f["from"][1] + f["to"][1]) / 2 - _cn.y) ** 2))
b["requests"].append({"face": _near["id"], "what": "低い翼（屋上緑地）の外壁を両端が入るように 1 枚ずつ、2 つの翼のつなぎ目も",
                      "where": "図書館側の上段通路と西の広場から", "why": "低い翼は塊だけで面がまだ無い（依頼は低い翼に向き合う 5 階の翼の面に付けた）", "status": "open"})
b["history"] = [
    {"at": "", "by": "recorder new --from-site", "what": "PLATEAU と 5m 標高から土台を起こした（低い翼 bldg_b5e26f45）"},
    {"at": "2026-11-04", "by": "仕分け係（opus, claude-opus-5-5）scripts/design_b11.py",
     "what": "公式ガイドブックで 11 号館が 2 つの翼の 1 棟と確認。足元を写真のある 5 階の翼（bldg_326ebceb）に差し替え、低い翼は塊 N"},
]

errs = validate_building(b)
if errs:
    print("規約違反:", *errs, sep="\n  ")
    sys.exit(1)
save_building(b, Path("data/buildings/b11.json"))
print("保存: data/buildings/b11.json  面", len(b["faces"]), " 塊", [k["id"] for k in b["blocks"]])
