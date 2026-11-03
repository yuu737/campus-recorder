"""タワー75 の設計（棟 JSON）3 版。観察票（out/tower75_観察票.json）と屋上の精読（out/tower75_屋上.json）に基づく。

分かったこと（観察係・屋上係の報告、2026-11-03 深夜）
    - 写真 070327678（1 版で「南南西面」と読んだ物）は**西北西面**。入口の大階段と通路橋が写っている。9 列、13 段が見える。
    - 柱型の方が窓より広い（約 1.35:1）。最上部 2 段は窓が約 2 倍幅。
    - 南南西の中央は深いガラスの凹部（各階に水平ルーバー）。両脇の f7・f11 は無地。凹部の内側 f8・f10 と角の小面 f6・f12 は各階に小窓 1 つ。
      外側の角寄り f4+f5・f13 は窓の帯 2 列ずつ。
    - 北北東の中央は奥まった凹部に横長の連窓、頂部にガラスの箱。北西・北東の角寄りに縦のガラス帯 1 本ずつ（格子の縦溝と並ぶ）。
    - 南西・南東の角（南端 2 列）は 8〜9 階から上が引っ込み、頂部が約 1 階低い。
    - PLATEAU の 74.2 / 69.5 m の部品は足元の外（通路橋の上）で、高さの根拠として弱い。
    - 屋上の像は足元から南へ 15.1 m ずれて写る。屋上の輪郭は OSM とほぼ同じ。南東の角（4.5×6.4 m）は屋上の高さに屋根が無い。

縮尺
    写真 070327678 を正面化し、階高 255 px・柱間隔 199 px。PLATEAU の高さ 74.2 m を物差しにすると 0.01726 m/px → 柱間隔 3.43 m、階高 4.40 m。
    検算：西北西面は OSM で 32.6 m、9 列 × 3.43 = 30.9 m で両端に 0.85 m ずつ余る → 辻褄が合う（高さが 90 m なら 37 m になって合わない）。
    なので 74.2 m は「PLATEAU の部品の位置は怪しいが、値は写真と整合する」として estimated で使う。
"""
import json, math, sys
from pathlib import Path
sys.path.insert(0, ".")
from shapely.geometry import Polygon
from recorder.schema import new_building, save_building, validate_building, Measurement
from recorder.photos import load_index, save_index

OBS = json.loads(Path("out/tower75_観察票.json").read_text(encoding="utf-8"))
ROOF = json.loads(Path("out/tower75_屋上.json").read_text(encoding="utf-8"))
EV_W = ["PXL_20261103_070327678", "PXL_20261103_070334137", "PXL_20261103_070204974"]
EV_S = ["PXL_20261103_074413490", "PXL_20261103_074445985", "PXL_20261103_074457079"]
EV_N = ["PXL_20261103_065946407", "PXL_20261103_070003022", "PXL_20261103_070044607"]
EV_E = ["PXL_20261103_074606922", "PXL_20261103_074549768"]
EV_AER = ["gsi:aerial_z18", "out/tower75_屋上.json"]
PL = ["plateau:bldg_f2cc5f1b-1403-4764-8a92-d081923b0f6a"]
OSM = "osm:way/tower75"
OBS_REF = "out/tower75_観察票.json"

MPP = 74.2 / 4300.0
BAY_PX, FLOOR_PX = 199.0, 255.0
bay_m, floor_m = BAY_PX * MPP, FLOOR_PX * MPP      # 3.43, 4.40
TOP = 74.2
PODIUM_TOP = 2 * floor_m - 1.0                      # 7.8
Z_STEP = PODIUM_TOP + 8 * floor_m                   # 南の角の段（8 階の上）
TOP_CORNER = TOP - floor_m                          # 角の上部は 1 階低い


def m(value, tol, status, evidence=(), method="", note="", unit="m"):
    return Measurement(round(value, 2) if isinstance(value, float) else value, unit, tol, status, list(evidence), method, note).to_json()


def est(value, tol, evidence, method, unit="m"):
    return m(value, tol, "estimated", evidence, method, unit=unit)


def unk(unit="m", note=""):
    return Measurement(None, unit, None, "unknown", [], "", note).to_json()


# --- 足元 ----------------------------------------------------------------------------------
site = json.loads(Path("data/site/tempaku.json").read_text(encoding="utf-8"))
ring = [list(p) for p in [b for b in site["buildings"] if b["id"] == "tower75"][0]["footprint_osm"][0]]
if not Polygon(ring).exterior.is_ccw:
    ring = ring[::-1]
# 辺（2 m 以上）に 2 版と同じ id を付ける（観察票の id と合わせる）
COMPASS = ["北", "北北東", "北東", "東北東", "東", "東南東", "南東", "南南東", "南", "南南西", "南西", "西南西", "西", "西北西", "北西", "北北西"]
edges = {}
order = []
for i in range(len(ring)):
    (x0, y0), (x1, y1) = ring[i], ring[(i + 1) % len(ring)]
    L = math.hypot(x1 - x0, y1 - y0)
    if L < 2.0:
        continue
    nx, ny = (y1 - y0) / L, -(x1 - x0) / L
    az = (math.degrees(math.atan2(nx, ny)) + 360) % 360
    fid = f"f{len(edges)}"
    edges[fid] = {"from": [x0, y0], "to": [x1, y1], "L": L, "az": az, "comp": COMPASS[int((az + 11.25) // 22.5) % 16]}
    order.append(fid)

# 南南西の凹部 f9 を深くする（観察票：OSM の 2.5〜2.8 m より深く 10 m 前後に見える。8 m と置く）
RECESS = 8.0
e9 = edges["f9"]
nx, ny = (e9["to"][1] - e9["from"][1]) / e9["L"], -(e9["to"][0] - e9["from"][0]) / e9["L"]   # 外向き
push = RECESS - 2.7
for key in ("from", "to"):
    e9[key] = [round(e9[key][0] - nx * push, 2), round(e9[key][1] - ny * push, 2)]
edges["f8"]["to"] = e9["from"]
edges["f10"]["from"] = e9["to"]
for fid in ("f8", "f10"):
    e = edges[fid]
    e["L"] = math.hypot(e["to"][0] - e["from"][0], e["to"][1] - e["from"][1])
# 輪郭を辺から作り直す（短い辺は元の頂点のまま）
ring2 = []
for fid in order:
    ring2.append(edges[fid]["from"])
# 短い辺（2 m 未満）が落ちているので、順番どおりに from だけ並べれば多角形になる（辺は連続）


def merged(ids, new_id, name):
    """一直線に並ぶ辺をひとつの面にする（窓の格子を通すため）。"""
    e0, e1 = edges[ids[0]], edges[ids[-1]]
    L = math.hypot(e1["to"][0] - e0["from"][0], e1["to"][1] - e0["from"][1])
    return {"id": new_id, "name": name, "from": e0["from"], "to": e1["to"], "L": L, "az": e0["az"], "merged": ids}


groups = [merged(["f1", "f2", "f3"], "W", "西北西面（入口・大階段。9 列）"), merged(["f14", "f15"], "E", "東南東面（西北西面と同じ帯。列数は未確定）"),
          merged(["f4", "f5"], "SW1", "南南西 西の角寄り（窓の帯 2 列）")]
used = {i for g in groups for i in g["merged"]}
face_defs = []
for fid in order:
    if fid in used:
        g = next((g for g in groups if g["merged"][0] == fid), None)
        if g:
            face_defs.append(g)
        continue
    e = edges[fid]
    face_defs.append({"id": fid, "name": f"{e['comp']}面（方位 {e['az']:.0f}°、{e['L']:.1f} m）", "from": e["from"], "to": e["to"], "L": e["L"], "az": e["az"]})

# --- 棟 ---------------------------------------------------------------------------------
b = new_building("tower75", "タワー75")
b["site_id"] = "tower75"
b["footprint"] = {"status": "estimated", "evidence": [OSM, OBS_REF] + EV_AER, "polygon": ring2,
                  "method": "OSM の輪郭（屋上の精読で足元の位置が正しいと確認。屋上の像は南へ 15.1 m 倒れ込む）。南南西の凹部だけ観察票に従って 8 m に深くした（要確認）"}
b["ground_z"] = est(42.9, 1.0, ["gsi:dem5a"], "国土地理院 5m 標高を足元で読む")
b["floor_count"] = est(16, None, EV_W + ["osm:levels=16"], "西北西面で 13 段が見え、基壇に 2〜3 層が隠れる。OSM の levels=16 と合う", unit="階")
b["floor_pitch"] = est(floor_m, 0.15, EV_W + PL, f"横エッジの自己相関 {FLOOR_PX} px。縮尺は PLATEAU の高さ 74.2 m（パラペット〜街路 4300 px）。検算：西北西面 32.6 m に 9 列 × 3.43 m が収まる")
b["parapet_top"] = est(TOP, 2.0, PL + [OSM], "PLATEAU の measuredHeight 74.2 m。部品の位置は足元の外で怪しいが、西北西面の長さ（OSM 32.6 m）と 9 列 × 3.43 m の整合で支持される。屋上を高い所から撮って確かめる")

# 塊：下（全体）、上（南の 2 角を 1 列ぶん欠く）、南の角の上部（引っ込み・1 階低い）、基壇
poly = Polygon(ring2)
W, E = groups[0], groups[1]
def corner_square(face, at_end: bool, size: float, inset: float):
    """面の端（at_end=True なら to 側）の角に、面に沿って size、内側へ size の正方形。inset だけ内側へずらす。"""
    (x0, y0), (x1, y1) = face["from"], face["to"]
    L = face["L"]
    ux, uy = (x1 - x0) / L, (y1 - y0) / L
    nx, ny = uy, -ux                      # 外向き
    s0 = L - size if at_end else 0.0
    pts = []
    for (s, t) in ((s0, 0), (s0 + size, 0), (s0 + size, size), (s0, size)):
        pts.append([round(x0 + ux * s - nx * (t + inset) - ux * (inset if at_end else -inset), 2),
                    round(y0 + uy * s - ny * (t + inset) - uy * (inset if at_end else -inset), 2)])
    return pts

CS = 2 * bay_m                            # 南端 2 列ぶん
sq_sw = corner_square(W, True, CS, 0.0)   # 西北西面の南端（to 側）
sq_se = corner_square(E, False, CS, 0.0)  # 東南東面の南端（from 側）
upper = poly.difference(Polygon(sq_sw)).difference(Polygon(sq_se)).buffer(0)
if upper.geom_type == "MultiPolygon":
    upper = max(upper.geoms, key=lambda g: g.area)
upper_ring = [[round(x, 2), round(y, 2)] for x, y in list(upper.exterior.coords)[:-1]]
podium = [[round(x, 2), round(y, 2)] for x, y in list(poly.buffer(4.0, join_style=2).simplify(0.5).exterior.coords)[:-1]]
# 南の 2 角の「8〜9 階から上が引っ込み、1 階低い」は寸法が unknown（観察票）。塊で試作したが位置が決まらず絵が崩れたので、
# 3 版では 1 塊にして requests に残す（角を 45° から撮る依頼）。
b["blocks"] = [
    {"id": "A", "name": "塔（南の 2 角の上部の引っ込みは未反映）", "polygon": ring2, "top_z": est(TOP, 2.0, PL + [OSM], "PLATEAU。西北西面 32.6 m と 9 列 × 3.43 m の整合で支持")},
    {"id": "C", "name": "基壇（入口・大階段・通路橋の根元）", "polygon": podium,
     "top_z": est(PODIUM_TOP, 1.5, EV_W, "写真 8 で入口の基壇は 2 層分。輪郭は塔を 4 m 広げた仮の形")},
]

tile = {"name": "橙のタイル（小型。形式は未確認）", "color": "#d9915a",
        "color_note": "写真は夕日で橙が強い（#eaa96b）。少し戻した推定。昼の日陰の写真で取り直す",
        "unit_w": est(12.5 * MPP, 0.05, EV_W, "横の周期 12.5 px。形式未確認"),
        "unit_h": est(17.3 * MPP - 0.008, 0.03, EV_W, "縦の周期 17.3 px から目地 8 mm を引いた推定"),
        "joint_w": est(0.008, 0.004, [], "小型タイルの標準目地")}
PIER = 1.97          # 柱型:窓 ≒ 1.35:1 → 3.43 を 1.97 + 1.46 に分ける
WIN = bay_m - PIER


def strip_windows(f, ev, bays_all, floors_cut=None, wide_top=False, top=TOP):
    """縦長の帯の窓を階ごとに。floors_cut: 上部で外す列（南の角）。wide_top: 最上部 2 段は 2 倍幅。"""
    k, z = 1, PODIUM_TOP
    rows = []
    while z + floor_m < top - 1.0:
        rows.append((k, z))
        z += floor_m
        k += 1
    for idx, (k, z) in enumerate(rows):
        floor = k + 2
        bays = list(bays_all)
        if floors_cut and floor > 8 + 2:
            bays = [i for i in bays if i not in floors_cut]
        wide = wide_top and idx >= len(rows) - 2
        f["windows"].append({"name": f"{floor}階窓" + ("（広い）" if wide else ""), "floors": [floor], "per_bay": 1, "bays": bays,
                             "z_bottom": est(z + 0.25, 0.2, ev, "階の帯（白い水切り）の上端から。目視"),
                             "z_top": est(z + floor_m - 0.3, 0.2, ev, "次の帯の下端まで。目視"),
                             "width": est(min(bay_m - 0.4, WIN * 2) if wide else WIN, 0.15, ev, "最上部 2 段は約 2 倍幅（観察票）" if wide else "柱型:窓 ≒ 1.35:1（観察票）"),
                             "recess": est(0.2, 0.1, ev, "目視")})
        f["ledges"].append({"name": f"{floor}階の帯", "z": est(z - 0.05, 0.2, ev, "白い水切りの帯"),
                            "depth": est(0.1, 0.05, ev, "目視"), "height": est(0.3, 0.05, ev, "帯の見付け 18 px"), "material": "concrete"})


def small_windows(f, ev, top=TOP):
    """各階に小窓 1 つ（凹部の内側・角の小面）。"""
    k, z = 1, PODIUM_TOP
    f["bays"] = {"count": est(1, None, ev, "各階に小窓 1 つ（観察票）", unit="個"), "pitch": est(f["length"]["value"], 0.3, ev, "面の幅"),
                 "pier_width": est(max(0.5, f["length"]["value"] - 1.3), 0.3, ev, "小窓 1.3 m を引いた残り"), "end_margin": est(0.0, 0.2, [], "面いっぱいに 1 つなので余白なし")}
    while z + floor_m < top - 1.0:
        f["windows"].append({"name": f"{k+2}階小窓", "floors": [k + 2], "per_bay": 1,
                             "z_bottom": est(z + 1.0, 0.3, ev, "目視"), "z_top": est(z + 2.4, 0.3, ev, "目視"),
                             "width": est(1.3, 0.3, ev, "目視"), "recess": est(0.2, 0.1, ev, "目視"), "rail": False})
        z += floor_m
        k += 1


faces = []
for fd in face_defs:
    f = {"id": fd["id"], "name": fd["name"], "from": fd["from"], "to": fd["to"],
         "length": est(fd["L"], 0.8, [OSM], "OSM の輪郭の辺" + ("（一直線の辺をまとめた）" if fd.get("merged") else "")),
         "top_z": est(TOP, 2.0, PL, "塔の高さ"), "bays": {}, "windows": [], "bands": [], "openings": [], "glazing": [], "ledges": [], "materials": [tile]}
    faces.append(f)
F = {f["id"]: f for f in faces}

# 西北西面：9 列、南端（to 側）2 列は 8 階より上を外す
w = F["W"]
w["bays"] = {"count": est(9, None, EV_W + [OBS_REF], "観察票：窓の縦の列 9", unit="個"), "pitch": est(bay_m, 0.1, EV_W, f"縦エッジの自己相関 {BAY_PX} px を換算"),
             "pier_width": est(PIER, 0.15, EV_W + [OBS_REF], "柱型:窓 ≒ 1.35:1（観察票）"), "end_margin": est((w["length"]["value"] - 9 * bay_m) / 2, 0.3, EV_W, "余りを両端に半分ずつ"),
             "pier_depth": est(0.15, 0.1, EV_W, "柱型は窓よりわずかに前。目視")}
strip_windows(w, EV_W, range(1, 10), wide_top=True)
w["render_view"] = True
# 東南東面：列数未確定（7＋2 列らしい）。8 列と置く。南端（from 側）2 列は上を外す
e = F["E"]
ne_ = 8
e["bays"] = {"count": est(ne_, None, EV_E + [OBS_REF], "観察票では 7 列＋南端 2 列らしいが未確定。8 と置く", unit="個"), "pitch": est(bay_m, 0.2, EV_W, "西北西面と同じ"),
             "pier_width": est(PIER, 0.2, EV_W, "同上"), "end_margin": est((e["length"]["value"] - ne_ * bay_m) / 2, 0.5, EV_E, "余りを両端に半分ずつ"),
             "pier_depth": est(0.15, 0.1, EV_W, "同上")}
strip_windows(e, EV_E, range(1, ne_ + 1), wide_top=True)
e["render_view"] = True
# 南南西の角寄り：窓の帯 2 列
for fid in ("SW1", "f13"):
    f = F[fid]
    f["bays"] = {"count": est(2, None, EV_S + [OBS_REF], "観察票：窓の帯 2 列", unit="個"), "pitch": est(bay_m, 0.2, EV_W, "西北西面と同じ"),
                 "pier_width": est(PIER, 0.2, EV_W, "同上"), "end_margin": est((f["length"]["value"] - 2 * bay_m) / 2, 0.4, EV_S, "余りを両端に半分ずつ"), "pier_depth": est(0.15, 0.1, EV_W, "同上")}
    strip_windows(f, EV_S, range(1, 3), top=TOP_CORNER)
# f7・f11：無地。f8・f10（凹部の内側）、f6・f12（角の小面）：各階に小窓 1 つ
for fid in ("f8", "f10", "f6", "f12"):
    small_windows(F[fid], EV_S if fid in ("f8", "f10") else EV_S + ["PXL_20261103_070409570"])
# f9：凹部の奥の全面ガラス＋各階の水平ルーバー
f9 = F["f9"]
f9["name"] = "南南西 中央の凹部（全面ガラス・各階に水平ルーバー）"
f9["glazing"] = [{"name": "凹部の奥のガラス（二重）", "along0": 0.3, "along1": f9["length"]["value"] - 0.3, "z_bottom": PODIUM_TOP,
                  "z_top": est(TOP - 3.0, 2.0, EV_S, "頂部は窓の無い奥の壁と格子枠（観察票）。3 m 下まで"),
                  "inset": est(0.1, 0.1, EV_S, "目視"), "depth": est(2.0, 1.0, EV_S, "目視"), "beam": False}]
k, z = 1, PODIUM_TOP
while z + floor_m < TOP - 3.0:
    f9["ledges"].append({"name": f"{k+2}階のルーバー", "z": est(z + floor_m - 0.6, 0.3, EV_S, "各階に水平ルーバー（観察票）"),
                         "depth": est(0.6, 0.2, EV_S, "目視"), "height": est(0.12, 0.05, EV_S, "目視"), "material": "metal"})
    z += floor_m
    k += 1
f9["render_view"] = True
# 北北東の中央 f19：奥まった凹部に横長の連窓。頂部にガラスの箱（部品）
f19 = F["f19"]
f19["name"] = "北北東 中央（凹部・横長の連窓・頂部にガラスの箱）"
f19["bays"] = {"count": est(1, None, EV_N + [OBS_REF], "各階に横長の連窓 1 つ（観察票）", unit="個"), "pitch": est(f19["length"]["value"], 0.5, EV_N, "面の幅"),
               "pier_width": est(2.0, 0.5, EV_N, "両端にガラスブロックのパネル。目視"), "end_margin": est(0.0, 0.3, [], "面いっぱいに 1 つなので余白なし")}
k, z = 1, PODIUM_TOP
while z + floor_m < TOP - 4.0:
    f19["windows"].append({"name": f"{k+2}階連窓", "floors": [k + 2], "per_bay": 1, "z_bottom": est(z + 1.0, 0.3, EV_N, "目視"), "z_top": est(z + floor_m - 0.8, 0.3, EV_N, "目視"),
                           "width": est(f19["length"]["value"] - 2.0, 0.5, EV_N, "面の幅 − 両端のパネル"), "recess": est(0.3, 0.1, EV_N, "目視"), "rail": False})
    z += floor_m
    k += 1
f19["render_view"] = True
# 北の 2 角のガラスの縦帯（f20 の北西角寄り、f18 の北東角寄り）＋小窓
for fid, at_end in (("f20", True), ("f18", False)):
    f = F[fid]
    L = f["length"]["value"]
    small_windows(f, EV_N)
    a0, a1 = (L - 2.0, L - 0.6) if at_end else (0.6, 2.0)
    f["glazing"].append({"name": "角寄りの縦のガラス帯", "along0": a0, "along1": a1, "z_bottom": PODIUM_TOP, "z_top": est(TOP - 1.5, 1.5, EV_N, "屋上の少し下まで（観察票）"),
                         "inset": est(0.3, 0.2, EV_N, "目視"), "depth": est(2.0, 1.0, EV_N, "目視"), "beam": False})

b["faces"] = faces

# --- 部品 --------------------------------------------------------------------------------
def rect_from(face, along0, along1, out0, out1):
    (x0, y0), (x1, y1) = face["from"], face["to"]
    L = face["L"] if "L" in face else face["length"]["value"]
    ux, uy = (x1 - x0) / L, (y1 - y0) / L
    nx, ny = uy, -ux
    return [[round(x0 + ux * a + nx * o, 2), round(y0 + uy * a + ny * o, 2)] for a, o in ((along0, out0), (along1, out0), (along1, out1), (along0, out1))]

nx19, ny19 = None, None
parts = [
    {"type": "coping", "name": "パラペットの笠木", "height": est(0.4, 0.1, EV_W, "目視"), "overhang": est(0.1, 0.05, [], "目視")},
    {"type": "box", "name": "北北東 頂部のガラスの箱（塔屋。屋上の精読の候補 PH1 と一致）", "material": "glass",
     "polygon": rect_from(F["f19"], 1.0, F["f19"]["length"]["value"] - 1.0, 0.0, -6.0), "z0": TOP, "z1": est(TOP + 4.0, 1.5, EV_N + EV_AER, "観察票（頂部のガラスの箱）と屋上の精読（12×6 m の候補）。高さは目視")},
    {"type": "box", "name": "屋上の設備の列 1（屋上の精読）", "material": "metal", "polygon": rect_from(F["f7"], -1.0, 26.0, -6.0, -10.0), "z0": TOP, "z1": est(TOP + 2.5, 1.0, EV_AER, "屋上の精読：27×4 m の列が 2 本。高さは目視")},
    {"type": "box", "name": "屋上の設備の列 2（屋上の精読）", "material": "metal", "polygon": rect_from(F["f7"], -1.0, 26.0, -12.0, -16.0), "z0": TOP, "z1": est(TOP + 2.5, 1.0, EV_AER, "同上")},
    {"type": "box", "name": "通路橋（西北西から取り付く白い橋。屋上の精読 29×7 m）", "material": "concrete",
     "polygon": rect_from(F["W"], 3.0, 10.0, 4.0, 33.0), "z0": est(7.5, 1.0, EV_W + EV_AER, "基壇の上あたりの高さ（観察票）。倒れ込みの小ささから約 10 m（屋上の精読）"), "z1": est(9.5, 1.0, EV_AER, "床板と手すりで 2 m と置く")},
]
b["parts"] = parts

# --- 写真（観察票の仕分け直しを索引にも反映） ------------------------------------------------
idx = load_index()
re_map = {r["photo"]: r["new"] for r in OBS.get("face_reassignment", [])}
for rec in idx["photos"]:
    if rec.get("building") == "tower75" and rec["id"] in re_map:
        rec["face"] = re_map[rec["id"]]
save_index(idx)
b["photos"] = [{"id": r["id"], "file": r["file"], "face": r.get("face"), "role": "全体" if r["id"] in EV_W + EV_N + EV_S + EV_E else "細部"}
               for r in idx["photos"] if r.get("building") == "tower75"]
b["requests"] = [{"face": "f19", "what": "北北東の全体を遠くから正面で", "where": "北側の道路から", "why": "凹部の深さと連窓の割付が目視", "status": "open"},
                 {"face": "f9", "what": "南南西の全体の正面と、凹部を斜め 45° から", "where": "南の広場から", "why": "凹部の深さ（8 m と置いた）が未確定", "status": "open"},
                 {"face": "E", "what": "東南東の全体を日中に", "where": "東側の道路から", "why": "列数が未確定（7＋2 か）", "status": "open"},
                 {"face": "*", "what": "4 つの角を 45° から 1 枚ずつ", "where": "", "why": "角の低い塊・上部の引っ込みの寸法が未確定", "status": "open"},
                 {"face": "*", "what": "屋上を高い所から", "where": "", "why": "高さ 74.2 m の根拠（PLATEAU）が弱い。塔屋・設備の確認", "status": "open"},
                 {"face": "*", "what": "昼の日陰のタイルを 1 m から", "where": "", "why": "色が夕日で橙に寄っている。タイルの形式も未確認", "status": "open"}]
b["history"] = [
    {"at": "2026-11-03T21:30", "by": "Claude（設計者）", "what": "南南西面の写真 1 枚と PLATEAU の高さから初版（長方形）"},
    {"at": "2026-11-03T22:20", "by": "Claude（設計者）", "what": "2 版：足元を OSM の輪郭に、全面に窓、北にガラス帯、東南東にガラスの張り出し"},
    {"at": "2026-11-04T01:30", "by": "Claude（設計者）", "what": "3 版：観察票と屋上の精読に従う。写真 11 は西北西面だった。凹部のガラス（南南西）、北の凹部の連窓と頂部のガラスの箱、北の 2 角のガラス帯、南の 2 角の上部の引っ込み、屋上の設備、通路橋。東南東の張り出しと f19 のガラス帯は撤回"},
]
errs = validate_building(b)
print("validate:", errs or "OK")
save_building(b, Path("data/buildings/tower75.json"))
print("saved: faces", len(faces), "parts", len(parts), "blocks", len(b["blocks"]), "upper ring pts", len(upper_ring))
