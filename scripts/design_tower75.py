"""タワー75 の設計（棟 JSON）。写真 20261103b の 070327678（南南西面の全体）を主な根拠にする。

縮尺の決め方（メジャー無し）
    PLATEAU の measuredHeight 74.2 m（主塔）と 69.5 m（低い方の段）の差 4.7 m が、写真で見える「段の部分が主塔より 1 階低い」と合う。
    なので 74.2 m を物差しにして、正面化画像のパラペット上端（y=1050）〜街路（y≈5350）の 4300 px を 74.2 m と置く → 0.01726 m/px。
    階高 255 px → 4.40 m、柱間隔 199 px → 3.43 m。16 階 × 4.40 = 70.4 m（＋塔屋・パラペット）で辻褄が合う。
    タイル 1 段（17.3 px）は小さい形式のタイルで JIS 二丁掛ではないので、物差しに使わない。

足元
    OSM の輪郭は裾（基壇）を含む凸凹の形で、PLATEAU の塔の部品は塔の一部しか無い。
    塔の主要部は、OSM の南南西の長い辺と西北西の辺の向きに合わせた長方形（写真の幅 25.4 m＋段 7.9 m、奥行きは OSM の辺から 30 m）で置く。
    基壇は OSM の輪郭そのまま（高さは写真の目視）。全部 estimated。
"""
import json, math, sys
from pathlib import Path
sys.path.insert(0, ".")
from recorder.schema import new_building, save_building, validate_building, Measurement
from recorder.photos import load_index

EV = ["PXL_20261103_070327678"]            # 南南西面の全体
EV_W = ["PXL_20261103_070204974"]          # 西北西面（入口・大階段）
EV_S = ["PXL_20261103_074413490"]          # 南の凹部
PL = ["plateau:bldg_f2cc5f1b-1403-4764-8a92-d081923b0f6a", "plateau:bldg_f693ff16-7d7b-4e35-b1c2-cff40827fcc1"]   # 高さだけ使う
OSM = "osm:way/tower75"

MPP = 74.2 / 4300.0
BAY_PX, FLOOR_PX = 199.0, 255.0
bay_m, floor_m = BAY_PX * MPP, FLOOR_PX * MPP
MAIN_W, SETBACK_W = 1470 * MPP, 460 * MPP
DEPTH = 30.0


def m(value, tol, status, evidence=(), method="", note="", unit="m"):
    return Measurement(round(value, 2) if isinstance(value, float) else value, unit, tol, status, list(evidence), method, note).to_json()


def est(value, tol, evidence, method, unit="m"):
    return m(value, tol, "estimated", evidence, method, unit=unit)


# --- 足元の幾何 ---------------------------------------------------------------------------
site = json.loads(Path("data/site/tempaku.json").read_text(encoding="utf-8"))
osm_ring = [b for b in site["buildings"] if b["id"] == "tower75"][0]["footprint_osm"][0]
# 南南西の長い辺 (39.54,5.78)->(50.53,0.04)、西北西の辺 (35.82,31.54)->(25.98,12.77)
a0, a1 = (39.54, 5.78), (50.53, 0.04)
w0, w1 = (25.98, 12.77), (35.82, 31.54)
ux, uy = a1[0] - a0[0], a1[1] - a0[1]
L = math.hypot(ux, uy); ux, uy = ux / L, uy / L                     # 南南西面に沿う向き（西→東）
vx, vy = w1[0] - w0[0], w1[1] - w0[1]
Lv = math.hypot(vx, vy); vx, vy = vx / Lv, vy / Lv                  # 西北西の辺に沿う向き（南→北）
# 原点：南南西面の線と西北西面の線の交点
def inter(p, d, q, e):
    den = d[0] * e[1] - d[1] * e[0]
    t = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / den
    return (p[0] + t * d[0], p[1] + t * d[1])
P = inter(a0, (ux, uy), w0, (vx, vy))


def rect(x0, x1, y0, y1):
    pts = []
    for (s, t) in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        pts.append([round(P[0] + ux * s + vx * t, 2), round(P[1] + uy * s + vy * t, 2)])
    return pts


A = rect(0.0, MAIN_W, 0.0, DEPTH)
B = rect(MAIN_W, MAIN_W + SETBACK_W, 3.0, DEPTH)     # 段の部分：南南西面から 3 m 引っ込む（仮）
b = new_building("tower75", "タワー75")
b["site_id"] = "tower75"
b["footprint"] = {"status": "estimated", "evidence": [OSM] + EV, "polygon": [list(p) for p in osm_ring],
                  "method": "基壇は OSM の輪郭。塔の主要部は写真の幅（正面化 1470 px + 460 px）と OSM の辺の向きから置いた長方形（blocks を見る）"}
b["ground_z"] = est(42.9, 1.0, ["gsi:dem5a"], "国土地理院 5m 標高を足元で読む")
b["floor_count"] = est(16, None, EV + ["osm:levels=16"], "正面化画像で階の帯を数えた（上 12 段が見え、下は基壇と木で隠れる）。OSM の levels=16 と合う", unit="階")
b["floor_pitch"] = est(floor_m, 0.15, EV + PL, f"横エッジの自己相関 {FLOOR_PX} px。縮尺は PLATEAU の高さ 74.2 m（パラペット〜街路 4300 px）")
b["parapet_top"] = est(74.2, 1.5, PL, "PLATEAU の measuredHeight（主塔）。段の部分は 69.5 m で、差 4.7 m ≒ 1 階ぶんと写真が合う")
b["blocks"] = [
    {"id": "A", "name": "主塔", "polygon": A, "top_z": est(74.2, 1.5, PL, "PLATEAU")},
    {"id": "B", "name": "段（主塔より 1 階低い）", "polygon": B, "top_z": est(69.5, 1.5, PL, "PLATEAU の 2 つ目の高さ。写真で 1 階ぶん低い")},
    {"id": "C", "name": "基壇（入口・大階段）", "polygon": [list(p) for p in osm_ring],
     "top_z": est(2 * floor_m - 1.0, 1.5, EV_W, "写真 8 で入口の基壇は 2 層分。目視")},
]
faces = []
def face(fid, name, p0, p1, ev):
    L = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
    f = {"id": fid, "name": name, "from": p0, "to": p1, "length": est(L, 0.8, ev, "塔の長方形の辺"),
         "bays": {}, "windows": [], "bands": [], "openings": [], "glazing": [], "ledges": [], "materials": []}
    faces.append(f)
    return f

sw = face("SW", "南南西面（主塔。写真 11 で全体）", A[0], A[1], EV)
sw_b = face("SW_B", "南南西面（段の部分）", B[0], B[1], EV)
ese = face("E", "東南東面（段の部分の側面）", B[1], B[2], EV)
nne = face("N", "北北東面", B[2], A[3], [])
wnw = face("W", "西北西面（入口側）", A[3], A[0], EV_W)
for f in (sw, sw_b, ese, nne, wnw):
    f["top_z"] = est(69.5 if f["id"] in ("SW_B", "E") else 74.2, 1.5, PL, "塊の高さ")
sw["render_view"] = True
wnw["render_view"] = True

tile = {"name": "橙のタイル（小型。形式は未確認）", "color": "#d9915a",
        "color_note": "写真は夕日で橙が強い（#eaa96b）。少し戻した推定。昼の日陰の写真で取り直す",
        "unit_w": est(12.5 * MPP, 0.05, EV, "横の周期 12.5 px。形式未確認"),
        "unit_h": est(17.3 * MPP - 0.008, 0.03, EV, "縦の周期 17.3 px から目地 8 mm を引いた推定"),
        "joint_w": est(0.008, 0.004, [], "小型タイルの標準目地")}
for f, L in ((sw, MAIN_W), (sw_b, SETBACK_W)):
    n = max(1, int(round((L - 1.4) / bay_m)))
    margin = (L - n * bay_m) / 2
    f["bays"] = {"count": est(n, None, EV, "正面化画像で窓の縦の列を数えた", unit="個"),
                 "pitch": est(bay_m, 0.1, EV, f"縦エッジの自己相関 {BAY_PX} px を換算"),
                 "pier_width": est(104 * MPP, 0.1, EV, "柱型（タイルの帯）の幅 104 px"),
                 "end_margin": est(margin, 0.3, EV, "角の柱型が広い。余りを両端に半分ずつ"),
                 "pier_depth": est(0.15, 0.1, EV, "柱型は窓よりわずかに前。目視")}
    top = 74.2 if f is sw else 69.5
    k = 1
    z = 2 * floor_m - 1.0      # 基壇の上から
    while z + floor_m < top - 1.0:
        f["windows"].append({"name": f"{k+2}階窓", "floors": [k + 2], "per_bay": 1,
                             "z_bottom": est(z + 0.25, 0.2, EV, "階の帯（白い水切り）の上端から。目視"),
                             "z_top": est(z + floor_m - 0.3, 0.2, EV, "次の帯の下端まで。目視"),
                             "width": est(95 * MPP, 0.1, EV, "窓の帯の幅 95 px"),
                             "recess": est(0.2, 0.1, EV, "目視")})
        f["ledges"].append({"name": f"{k+2}階の帯", "z": est(z - 0.05, 0.2, EV, "白い水切りの帯"),
                            "depth": est(0.1, 0.05, EV, "目視"), "height": est(0.3, 0.05, EV, "帯の見付け 18 px"), "material": "concrete"})
        z += floor_m
        k += 1
    f["materials"] = [tile]
for f in (ese, nne, wnw):
    f["materials"] = [tile]
b["faces"] = faces
b["parts"] = [
    {"type": "coping", "name": "パラペットの笠木", "height": est(0.4, 0.1, EV, "目視"), "overhang": est(0.1, 0.05, [], "目視")},
    {"type": "box", "name": "屋上のアンテナ台（仮）", "material": "concrete",
     "polygon": [[round(P[0] + ux * 2 + vx * 2, 2), round(P[1] + uy * 2 + vy * 2, 2)], [round(P[0] + ux * 6 + vx * 2, 2), round(P[1] + uy * 6 + vy * 2, 2)],
                 [round(P[0] + ux * 6 + vx * 5, 2), round(P[1] + uy * 6 + vy * 5, 2)], [round(P[0] + ux * 2 + vx * 5, 2), round(P[1] + uy * 2 + vy * 5, 2)]],
     "z0": 74.2, "z1": est(75.0, 0.5, EV, "写真でアンテナの根元の小さな箱。目視")},
]
b["photos"] = [{"id": r["id"], "file": r["file"], "face": r.get("face"), "role": "全体" if r.get("face") in ("f5", "f8", "f0") else "細部"}
               for r in load_index()["photos"] if r.get("building") == "tower75"]
b["requests"] = [
    {"face": "N", "what": "北北東面の全体（遠くから）", "where": "北側の道路から", "why": "写真はあるが面の区画が決まらず、窓の割付も未確認", "status": "open"},
    {"face": "E", "what": "東南東面の全体", "where": "東側から", "why": "同上", "status": "open"},
    {"face": "W", "what": "入口の基壇と大階段を正面から、段が数えられる 1 枚", "where": "通路橋の下から", "why": "基壇の高さが目視の推定", "status": "open"},
    {"face": "*", "what": "昼の日陰のタイルを 1 m から", "where": "", "why": "色が夕日で橙に寄っている。タイルの形式（寸法）も未確認", "status": "open"},
    {"face": "SW", "what": "塔の足元が木に隠れない位置から 1 枚", "where": "広場の東側", "why": "最下 4 階と基壇の取り合いが見えていない", "status": "open"},
]
b["history"] = [{"at": "2026-11-03T21:30", "by": "Claude（設計者）", "what": "南南西面の写真 1 枚と PLATEAU の高さから初版。塔の主要部は長方形で置いた。段の部分は 1 階低い"}]
errs = validate_building(b)
print("validate:", errs or "OK")
save_building(b, Path("data/buildings/tower75.json"))
print("saved: bay", round(bay_m, 2), "floor", round(floor_m, 2), "main_w", round(MAIN_W, 1), "setback_w", round(SETBACK_W, 1), "P", [round(v, 1) for v in P])
