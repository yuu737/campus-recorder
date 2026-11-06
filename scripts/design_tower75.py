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

4 版（2026-11-04 未明、測量係・検収係。out/tower75/sokuryo_W.json、out/tower75/kenshu_*.json）
    上の検算は両端の余白を測っていなかった。正面化 5 枚（傾き 0.31° 以内）で読み直した：
    - 壁の北端は柱型から始まる（070327678 の角 1215 px）。南端は柱型 9 の後ろで終わり（約 3105 px）、その右の
      小窓の帯は 0.855 倍に写る＝さらに奥の平行な壁（屋上輪郭の南西の段 2.9 m と合う）。壁の幅＝柱間隔の 9.49 周期。
    - 物差し 1：OSM の辺 32.67 m ÷ 9.49 = 3.44 m。検算：屋上輪郭の西辺 32.16 m で 3.39 m（−1.6%）。
    - 物差し 2（弱い）：PLATEAU 74.2 m。070204974 でパラペット〜入口の床 3533 px＝65.8 m、大階段 15 段 × 0.15 m を足して
      西の広場から 67.9 m → 74.2 m と −8.5%。北の沈んだ広場（1 層下）から測った値なら近づく（決めない）。
    - 階高：白い帯の間隔 241.0（070204974）/256.3（070327678）/262.4（070334137）px → 4.49/4.43/4.44 m。2 枚とも 8 階と 9 階の間で間隔が変わる → 3〜8 階 4.51 m、9 階から上 4.43 m。
    - 最上部 2 段の広い窓は 120 px＝2.24 m（柱型 1.20 m）、高さは帯から 4.79 m と 5.14 m、笠木 0.24 m。広い段の柱型に帯は無い。
    - 基壇の上の帯は入口の床から 6.26 m（336 px）、入口の床は西の広場から 2.25 m（階段）→ 8.5 m。
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

# 4 版：物差し 1 は OSM の西北西の辺 32.67 m ＝ 柱間隔 9.49 周期（out/tower75/sokuryo_W.json）
EV_W4 = ["PXL_20261103_070204974", "PXL_20261103_070327678", "PXL_20261103_070334137"]
SOK = "out/tower75/sokuryo_W.json"
MPP = 3.443 / 184.9                                 # 070204974 の正面化での m/px（柱間隔 184.9 px）
bay_m = 3.44
FLOOR_LO, FLOOR_UP = 4.51, 4.43                     # 3〜8 階と 9 階から上（2 枚の写真とも下の階が約 1.8% 高い。検収 2 回目）
floor_m = round((6 * FLOOR_LO + 5 * FLOOR_UP) / 11, 2)   # 平均 4.47（他の面の仮の割付に使う）
BAND_H = 0.22                                       # 白い帯の見付け 12 px
WIDE_ROW1, WIDE_GAP = 4.79, 0.24                    # 広い段の 1 段目の高さ（帯→中の横桟）、笠木の厚み
PODIUM_TOP = 8.5                                    # 西の広場から基壇の上の帯（の中心）まで
TOP = round(PODIUM_TOP + 6 * FLOOR_LO + 5 * FLOOR_UP + 10.17, 2)   # 11 階ぶん＋広い 2 段と笠木 10.17 m（546 px）＝ 67.88
# 5 版：南西の角の段は西北西面の帯の梯子で z5.2（8 階の床の帯 z5 の 0.2 段上が段の笠木。sokuryo_SW1 の 5.20/5.17）
Z_STEP = round(PODIUM_TOP + 5.2 * FLOOR_LO, 2)      # 31.95
STEP_FLOOR = 8                                      # この階から上の南端 2 列が引っ込む
SB_D, SB_W = 5.4, 6.7                               # 引っ込みの奥行き（5.3〜5.6 m）と幅（1.95 列）
# 北の帯の番号 z（z0＝基壇の上の帯、z13.25＝西北西面のパラペット）を西の広場からの m に。2 点を通る一次式（帯の間隔 4.48 m、北の 4.47 と 0.3%）
ZN_PITCH = (TOP - PODIUM_TOP) / 13.25
def ZN(z):
    return round(PODIUM_TOP + z * ZN_PITCH, 2)
CORE_TOP = ZN(14.06)                                # 中の塊（北の脇の壁の笠木・南西の引っ込みの頂部）71.5
PIER_TOP = ZN(15.8)                                 # f19 の柱型の頂部 79.3
BOX_Z0, BOX_Z1 = ZN(12.6), ZN(15.6)                 # 頂部のガラスの箱 65.0〜78.4
SUNKEN = 3.0                                        # 北の沈んだ広場は西の広場より 3.0 m 低い


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

# --- 5 版：北北東側・南西の上部・頂部（out/tower75/kansatsu_north.md と sokuryo_f19/f20/SW1、kazoe_f19/f20） ---------
EV_N5 = ["PXL_20261103_065946407", "PXL_20261103_070003022"]
EV_SW = ["PXL_20261103_070327678", "PXL_20261103_070334137"]
SOK_N, SOK_19, SOK_SW = "out/tower75/sokuryo_f20.json", "out/tower75/sokuryo_f19.json", "out/tower75/sokuryo_SW1.json"
KAZ_N, KAZ_19 = "out/tower75/kazoe_f20.json", "out/tower75/kazoe_f19.json"
KAN_N = "out/tower75/kansatsu_north.md"
# 北の座標：北西の角（西北西面の北端）を原点に、s＝北北東の線に沿って東へ、n＝北へ（OSM の f0/f20 の線が n=0）
_e0 = edges["f0"]
P_NW = _e0["to"]
uN = ((_e0["from"][0] - _e0["to"][0]) / _e0["L"], (_e0["from"][1] - _e0["to"][1]) / _e0["L"])
nN = (-uN[1], uN[0])
def NP(s, n):
    return [round(P_NW[0] + s * uN[0] + n * nN[0], 2), round(P_NW[1] + s * uN[1] + n * nN[1], 2)]
S_COL = 5.8                 # 北西の角の柱の幅（sokuryo_f20：5.8/5.9 m）
N_FLANK = 3.4               # 脇の壁は角の柱より 3.4 m 北へ出る（sokuryo_f20 の set_back。角の柱の北面を OSM の線に置く：西北西面＝OSM の辺 32.67 m＝9.49 周期と合う向き）
S_FLANK1 = S_COL + 8.7      # 脇の壁 8.7 m（sokuryo_f20）
SLIT = 1.0                  # 脇の壁と柱型の間のガラスのスリット（sokuryo_f19）
PIER19, OPEN19 = 2.6, 10.4  # 柱型の幅・柱型の間（sokuryo_f19）
N_PIER = N_FLANK + 0.3      # 柱型は脇の壁より 0.3 m 前（周期の比 1.008）
N_RECESS = N_FLANK - 1.7    # 凹部の奥の壁は脇の壁の面から 1.7 m 奥（sokuryo_f19 の 1.50/1.70/1.83 m）
S_PW0 = S_FLANK1 + SLIT
S_OP0, S_OP1 = S_PW0 + PIER19, S_PW0 + PIER19 + OPEN19
S_PE1 = S_OP1 + PIER19
S_F17 = 37.62               # OSM の f17（北東の角の段）の位置。東の脇の壁はここまで
N_F16 = -2.42               # OSM の f16（北東の角の北面）


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

# 5 版：北北東側の辺を写真の作りに置き換える（角の柱・脇の壁・スリット・柱型・浅い凹部・東の脇の壁）
_i17 = order.index("f17")
ring_osm = ring2
ring2 = ring_osm[1:_i17 + 1] + [NP(S_F17, N_FLANK), NP(S_PE1, N_FLANK), NP(S_PE1, N_RECESS), NP(S_PW0, N_RECESS),
                                NP(S_PW0, N_FLANK), NP(S_COL, N_FLANK), NP(S_COL, 0.0)]
assert math.dist(ring_osm[_i17], NP(S_F17, N_F16)) < 0.05, (ring_osm[_i17], NP(S_F17, N_F16))
NEWF = {"f0": (NP(S_COL, 0.0), NP(0.0, 0.0)), "f17": (NP(S_F17, N_F16), NP(S_F17, N_FLANK)), "f18": (NP(S_F17, N_FLANK), NP(S_PE1, N_FLANK)),
        "f19": (NP(S_OP1, N_RECESS), NP(S_OP0, N_RECESS)), "f20": (NP(S_PW0, N_FLANK), NP(S_COL, N_FLANK))}
for fd in face_defs:
    if fd["id"] in NEWF:
        fd["from"], fd["to"] = NEWF[fd["id"]]
        fd["L"] = math.dist(fd["from"], fd["to"])
        fd["moved"] = True


def WP(a, d):
    """西北西面に沿って北端から a m、内側へ d m。"""
    W_ = groups[0]
    ux, uy = (W_["to"][0] - W_["from"][0]) / W_["L"], (W_["to"][1] - W_["from"][1]) / W_["L"]
    return [round(W_["from"][0] + ux * a - uy * d, 2), round(W_["from"][1] + uy * a + ux * d, 2)]


def EP(a, d):
    """東南東面に沿って南端から a m、内側へ d m。"""
    E_ = groups[1]
    ux, uy = (E_["to"][0] - E_["from"][0]) / E_["L"], (E_["to"][1] - E_["from"][1]) / E_["L"]
    return [round(E_["from"][0] + ux * a - uy * d, 2), round(E_["from"][1] + uy * a + ux * d, 2)]


# --- 棟 ---------------------------------------------------------------------------------
b = new_building("tower75", "タワー75")
b["site_id"] = "tower75"
b["footprint"] = {"status": "estimated", "evidence": [OSM, OBS_REF, SOK_N, SOK_19] + EV_AER + EV_N5, "polygon": ring2,
                  "method": "OSM の輪郭（屋上の精読で足元の位置が正しいと確認。屋上の像は南へ 15.1 m 倒れ込む）。南南西の凹部だけ観察票に従って 8 m に深くした（要確認）。"
                            "5 版：北北東側は正面化 065946407・070003022 の作りに置き換え：北西の角の柱の北面を OSM の線に置き、脇の壁を 3.4 m 北へ、柱型を 3.7 m、凹部の奥の壁を 1.7 m（OSM の f19 は線から 1.0 m 北で 0.7 m 差）。"
                            "東の脇の壁は西と鏡写しと仮定（070003022 で東の笠木が西の笠木と同じ行に写る）。北東の角の段 f16/f17 は OSM のまま"}
b["ground_z"] = est(42.9, 1.0, ["gsi:dem5a"], "国土地理院 5m 標高を足元で読む")
b["ground_z"]["note"] = "模型の z=0 は西の広場（西の入口の大階段の下）。DEM の 42.9 m が西の広場・北の歩道・北の沈んだ広場のどれかは未確定（sokuryo_f20 の questions）"
b["levels"] = [
    {"id": "west_plaza", "name": "西の広場（z の基準）", "z": est(0.0, 0.0, EV_W4 + [SOK], "z の基準。西北西面の大階段の下の広場")},
    {"id": "entrance", "name": "西の入口の階＝北のテラス", "z": est(2.25, 0.3, EV_W4 + ["PXL_20261103_070123308", SOK], "大階段 15 段 × 0.15 m（sokuryo_W）。070123308 で大階段の上とテラスの縁がそろう（kansatsu_f20）")},
    {"id": "north_sunken_plaza", "name": "北の沈んだ広場（低い地面）", "z": est(-SUNKEN, 1.0, EV_N5 + ["PXL_20261103_070327678", "PXL_20261103_070123308", SOK_N],
        "sokuryo_f20.sunken_plaza：正面化の目の高さ z−1.69 → 北の歩道 E−2.9 m、広場への階段 16 段（measured、kazoe_f20）× 0.15 m → 広場 E−5.3 m、西の広場は E−2.25 m → 3.05 m 低い（約 2/3 階）。模型には faces の ground_offset（f20・f19 に同じ値）で沈みとして出す")},
]
b["floor_count"] = m(16, None, "measured", EV_N5 + [KAZ_N], "kazoe_f20：沈んだ層 1＋基壇 2＋窓の段 13＝16（北北東面、2 枚で一致）。OSM levels=16 と一致。西北西面で見える 15 との差は沈んだ層", unit="階")
b["floor_pitch"] = est(floor_m, 0.06, EV_W4 + [OSM, SOK], "正面化 3 枚の白い帯の間隔 241.0/256.3/262.4 px を柱間隔（OSM 32.67 m ÷ 9.49 周期 = 3.44 m）で換算 → 4.49/4.43/4.44 m。検収で 3〜8 階 4.51 m・9 階から上 4.43 m に分けた（070204974 で 242.8/238.8 px、070327678 で 260/255.3 px、どちらも 8 階と 9 階の間で変わる）。ここは 11 階の平均。北北東面は 4.45/4.49 m（OSM の f0＋f20 14.53 m を物差し、sokuryo_f20）で ±0.4%")
b["parapet_top"] = est(TOP, 2.0, EV_W4 + [OSM, SOK], "西北西面の外周の壁のパラペット（西の広場から）：大階段 2.25 m（15 段 × 0.15 m）＋入口の床〜基壇の上の帯 6.26 m ＋ 3〜8 階 6 × 4.51 m ＋ 9〜13 階 5 × 4.43 m ＋ 広い 2 段と笠木 10.17 m（070204974、縮尺は OSM の辺）。"
                       "5 版：中の塊はこれより 3.6 m 高く 71.5 m（塊 core）、北の柱型は 79.3 m。PLATEAU 74.2 m は北の沈んだ広場（−3.0 m）から中の塊の頂部まで（3.0＋71.5＝74.5 m、+0.4%）と読む（塊 core の top_z の note）")

# 塊（5 版）：西の外周の帯（67.9 m）、南西の角の下（段の笠木 31.95 m）、東の外周の帯（西と同じと仮定）、中の塊（71.5 m）、北の柱型 2 本（79.3 m）、基壇
poly = Polygon(ring_osm)
foot = Polygon(ring2)
assert foot.is_valid, "北北東側の輪郭が自己交差"
OV = 0.05                                 # 塊どうしを少し重ねる（足し算のブーリアンで面が一致しないように）
w_n = Polygon([WP(0, 0), WP(32.67 - SB_W, 0), WP(32.67 - SB_W, SB_D + OV), WP(0, SB_D + OV)]).union(
      Polygon([NP(0, 0), NP(S_COL + OV, 0), NP(S_COL + OV, -SB_D), NP(0, -SB_D)])).intersection(foot)
w_s = Polygon([WP(32.67 - SB_W, 0), WP(33.5, 0), WP(33.5, SB_D + OV), WP(32.67 - SB_W, SB_D + OV)]).intersection(foot)
# 2026-11-06 検収係：東南東面も南端 2 列（南から）は 8 階の床の帯の少し上で 5.4 m 奥へ引っ込む（060310392 で南 2 列の窓は基壇の上に 5 段＋α、
# 主塔の 7 列は頂部まで。航空写真の屋上の精読でも南東の角 4.5×6.4 m に屋上の高さの屋根が無い＝引っ込みの穴）。西の角（Ws・Wsb）と同じ作り
E_PITCH = round(28.33 / 9.49, 3)          # 東南東面の柱間隔（OSM の辺 28.33 m ÷ 西と同じ 9.49 周期）
ES_W = round(1.95 * E_PITCH, 2)           # 南端 2 列の幅（西の 1.95 列と同じ割合）
e_s = Polygon([EP(ES_W, 0), EP(28.33 + 1.0, 0), EP(28.33 + 1.0, SB_D + OV), EP(ES_W, SB_D + OV)]).intersection(foot)
e_ss = Polygon([EP(-1.0, 0), EP(ES_W + OV, 0), EP(ES_W + OV, SB_D + OV), EP(-1.0, SB_D + OV)]).intersection(foot)
# 北西の角の低い塊（2026-11-06 検収係）：北西の角の柱 f0 の前（北）に、西北西面の面にそろって立つ低い箱。上端は 061455930 と 060842230 の
# 2 本の視線（箱の北西の上の角）を西北西面の線を北へ延ばした所で読み z 21.9／25.6 → 23.8 m。北の面は脇の壁 f20 の面（3.4 m 北）にそろう
# （060927494 で箱の東の側面が見えず、上端の東の端が脇の壁との入隅に来る）。幅は角の柱の幅 5.8 m（同じ写真で箱の北の面が角の柱の幅いっぱい）
NWBOX_TOP = 23.8
nw_box = [NP(0.0, -OV), NP(S_COL, -OV), NP(S_COL, N_FLANK), NP(0.0, N_FLANK)]
cut_w = Polygon([WP(-1, -1), WP(33.5, -1), WP(33.5, SB_D), WP(-1, SB_D)]).union(Polygon([NP(-1, 1), NP(S_COL, 1), NP(S_COL, -SB_D), NP(-1, -SB_D)]))
cut_e = Polygon([EP(-1.0, -1), EP(29.33, -1), EP(29.33, SB_D), EP(-1.0, SB_D)])
core = foot.difference(cut_w).difference(cut_e).buffer(0)
if core.geom_type == "MultiPolygon":
    core = max(core.geoms, key=lambda g: g.area)


def ringof(g):
    g = g.simplify(0.02)
    if g.geom_type == "MultiPolygon":
        g = max(g.geoms, key=lambda q: q.area)
    return [[round(x, 2), round(y, 2)] for x, y in list(g.exterior.coords)[:-1]]


pier_w = [NP(S_PW0, N_RECESS - 0.5), NP(S_OP0, N_RECESS - 0.5), NP(S_OP0, N_PIER), NP(S_PW0, N_PIER)]
pier_e = [NP(S_OP1, N_RECESS - 0.5), NP(S_PE1, N_RECESS - 0.5), NP(S_PE1, N_PIER), NP(S_OP1, N_PIER)]
podium = [[round(x, 2), round(y, 2)] for x, y in list(poly.buffer(4.0, join_style=2).simplify(0.5).exterior.coords)[:-1]]
STRIP_WHY = ("中の塊の西の面は西北西面から 5.4 m 奥：南西の引っ込みの奥の壁（sokuryo_SW1 の 5.3〜5.6 m）、北西の角の柱の幅 5.8 m（sokuryo_f20）、"
             "屋上の西の帯の暗い影の内側の縁 2.3＋3.3＝5.6 m（out/tower75_屋上.json の lower_step、確信度 低）の 3 つが合う")
b["blocks"] = [
    {"id": "Wn", "name": "西の外周の帯（西北西面と北西の角の柱。パラペット 67.9 m）", "polygon": ringof(w_n),
     "top_z": est(TOP, 2.0, EV_W4 + [OSM, SOK, SOK_N], "西北西面のパラペット（parapet_top と同じ）。北西の角の柱の頂部も同じ高さ（sokuryo_f20 の w_parapet z13.25）"),
     "depth": est(SB_D, 0.2, EV_SW + [SOK_SW, SOK_N, "out/tower75_屋上.json"], STRIP_WHY)},
    {"id": "Ws", "name": "南西の角の下の部分（8 階の床より上の南端 2 列は 5.4 m 奥へ引っ込む）", "polygon": ringof(w_s),
     "top_z": est(Z_STEP, 0.3, EV_SW + [SOK_SW], "段の笠木：070327678 で z5.20、070334137 で z5.17（西北西面の帯の梯子）＝ 8.5＋5.2×4.51 m"),
     "width": est(SB_W, 0.3, ["PXL_20261103_070327678", SOK_SW], "正面化で 1.95 列（388 px）＝6.7 m。南端は西北西面の南端とそろう")},
    {"id": "Es", "name": "東の外周の帯（主塔の 7 列。西と同じ作りと仮定）", "polygon": ringof(e_s),
     "top_z": est(TOP, 2.5, EV_E + ["PXL_20261105_060310392", OBS_REF], "060310392 で主塔の 7 列の頂部は広い 2 段の上（西北西面と同じ作り）。高さは西北西面のパラペットと同じと仮定（東の写真は未登録で測っていない）")},
    {"id": "Ess", "name": "東南東 南東の角の下の部分（南端 2 列。8 階の床より上は 5.4 m 奥へ引っ込む）", "polygon": ringof(e_ss),
     "top_z": est(Z_STEP, 2.5, ["PXL_20261105_060310392", "PXL_20261105_062246708"] + EV_AER,
                  "060310392（未登録）で南端 2 列の窓は基壇の上に 5 段＋上の段の途中までで笠木、主塔の 7 列は頂部まで続く（観察票の『7 列＋南端 2 列』）。"
                  "登録済みの 062246708 に f13 の梯子を写すと南東の角の段の上端が z 31.95（西の段 Ws と同じ）の線に乗る。西の段と同じ値に置いた"),
     "width": est(ES_W, 0.5, ["PXL_20261105_060310392"] + EV_AER, "南端 2 列＝西と同じ 1.95 周期 × 東の柱間隔。航空写真の屋上の精読の南東の穴 4.5×6.4 m と ±0.6 m")},
    {"id": "NWb", "name": "北西の角の低い塊（角の柱の前。西北西面にそろう）", "polygon": nw_box,
     "top_z": est(NWBOX_TOP, 2.0, ["PXL_20261105_061455930", "PXL_20261105_060842230", "PXL_20261105_061002256", "PXL_20261105_061122585"],
                  "登録済みの 061455930・060842230 で箱の北西の上の角を指し、視線が西北西面の線を北へ 3.4 m 延ばした所を通る高さ 21.9／25.6 m の平均（2 枚の差 3.7 m）。"
                  "061002256・061122585（未登録）でも角の柱の前に 7〜8 階ほどの箱として写る"),
     "depth": est(N_FLANK, 1.5, ["PXL_20261105_060927494", "PXL_20261105_061002256"], "北の面は脇の壁 f20 の面にそろう（060927494 で箱の東の側面が見えない）"),
     "width": est(S_COL, 1.0, ["PXL_20261105_060927494", "PXL_20261105_061002256"], "角の柱の幅いっぱい（060927494）")},
    {"id": "core", "name": "中の塊（外周の帯より 3.6 m 高い）", "polygon": ringof(core),
     "top_z": {**est(CORE_TOP, 1.0, EV_N5 + ["PXL_20261103_070044607"] + EV_SW + [SOK_N, SOK_SW],
                     "北の脇の壁の笠木 z14.06（14.11/13.99/14.12、065946407・070003022）と南西の引っ込みの頂部 z14.01/14.03（070327678・070334137）。西北西面のパラペット 67.88 m（z13.25）＋0.81 段 × 4.43 m＝71.5 m。南・東の外周は見えていないので同じと仮定"),
               "note": "PLATEAU の 74.2 m との関係（estimated）：北の沈んだ広場（levels.north_sunken_plaza、−3.0 m）から測ると 3.0＋71.5＝74.5 m で +0.4%。西の広場から測った外周のパラペット 67.9 m とでは −8.5% だったのは、地面の差（3.0 m）と頂部の差（3.6 m）の 2 つ。PLATEAU の高さの取り方（最高点か代表値か）は未確定"}},
    {"id": "P19w", "name": "北北東 西の柱型（塔でいちばん高い）", "polygon": pier_w,
     "top_z": est(PIER_TOP, 1.5, EV_N5 + [SOK_19], "065946407 で z15.5〜15.8、070003022 で z15.9（柱型は 0.3 m 前のぶん −0.1）。67.88＋(15.8−13.25)×4.43")},
    {"id": "P19e", "name": "北北東 東の柱型", "polygon": pier_e,
     "top_z": est(PIER_TOP, 1.5, EV_N5 + [SOK_19], "西の柱型と同じ高さ（070003022 で東の柱型の頂部の行 61 px が西 63 px とそろう）")},
    {"id": "C", "name": "基壇（入口・大階段・通路橋の根元）", "polygon": podium,
     "top_z": est(PODIUM_TOP, 0.5, EV_W4 + [SOK], "070204974：入口の床〜基壇の上の帯 336 px＝6.26 m ＋ 大階段 2.25 m（蹴上げ 0.15 m の慣用値）。輪郭は塔を 4 m 広げた仮の形（形は unknown）")},
]

tile = {"name": "橙のタイル（小型。形式は未確認）", "color": "#d9915a",
        "color_note": "写真は夕日で橙が強い（#eaa96b）。少し戻した推定。昼の日陰の写真で取り直す",
        "unit_w": est(11.6 * MPP - 0.008, 0.02, EV_W4, "070204974 の柱型で横の周期 11.6 px（千鳥で半周期 5.8 px も出る）から目地 8 mm を引いた推定。形式未確認"),
        "unit_h": est(4.03 * MPP - 0.008, 0.01, EV_W4, "070204974 の柱型で縦の周期 4.03 px（4 枚の写真で柱間隔の 46.2 段）から目地 8 mm を引いた推定"),
        "joint_w": est(0.008, 0.004, [], "小型タイルの標準目地")}
PIER = 1.97          # 柱型は柱間隔の 0.575（070204974 で 105〜108 px / 184.9 px）
WIN = bay_m - PIER
WIDE_W = 2.24        # 最上部 2 段の広い窓（070204974 で 120.4 px）


def strip_windows(f, ev, bays_all, floors_cut=None, wide_top=False, top=TOP, from_floor=3, to_floor=99, scale=1.0):
    """縦長の帯の窓を階ごとに。floors_cut: STEP_FLOOR 階から上で外す列（南西の角の引っ込み）。wide_top: 最上部 2 段は 2 倍幅。
    from_floor〜to_floor: 窓と帯を置く階（引っ込んだ壁は 8 階から、南の角寄りの面は段の下まで）。"""
    k, z = 1, PODIUM_TOP
    rows = []
    while z + (FLOOR_LO if k <= 6 else FLOOR_UP) < top - 1.0:
        rows.append((k, z))
        z += FLOOR_LO if k <= 6 else FLOOR_UP
        k += 1
    ev4 = list(ev) + [SOK]
    # 白い帯は柱型の上だけ（観察票「窓の帯のところで切れる」、070204974 で確認）。柱型ごとに along0/along1 で置く
    bb = f["bays"]
    L = f["length"]["value"]
    pitch, pier, cnt = bb["pitch"]["value"], bb["pier_width"]["value"], bb["count"]["value"]
    mf = (bb.get("margin_from") or bb.get("end_margin"))["value"]
    pier_spans = [(max(0.0, mf + i * pitch - pier / 2), min(L, mf + i * pitch + pier / 2)) for i in range(cnt + 1)]
    for idx, (k, z) in enumerate(rows):
        floor = k + 2
        bays = list(bays_all)
        if floor < from_floor or floor > to_floor:
            continue
        cut = bool(floors_cut) and floor >= STEP_FLOOR
        if cut:
            bays = [i for i in bays if i not in floors_cut]
        wide = wide_top and idx >= len(rows) - 2
        # z は白い帯の中心。窓の列は帯の 0.20 m 上（格子パネルの下端）から次の帯の下端まで（070204974 で 11 px / 帯の見付け 12 px）
        zb, zt = z + 0.20, z + (FLOOR_LO if k <= 6 else FLOOR_UP) + 0.07
        how_b = "070204974：帯の中心から格子パネルの下端まで 11 px（0.20 m）"
        how_t = "070204974：上のガラスの上端は次の帯の中心より 4 px（0.07 m）上"
        if wide:
            z0 = rows[len(rows) - 2][1]                       # 広い段の下の帯
            if idx == len(rows) - 2:
                zb, zt = z0 + 0.20, z0 + WIDE_ROW1 - 0.10
                how_t = "070204974：帯から中の横桟まで 257 px（4.79 m）の 0.10 m 下"
            else:
                zb, zt = z0 + WIDE_ROW1 + 0.15, top - WIDE_GAP
                how_b, how_t = "中の横桟の上", "笠木の下端（パラペット上端から 13 px ＝ 0.24 m）"
        f["windows"].append({"name": f"{floor}階窓" + ("（広い）" if wide else ""), "floors": [floor], "per_bay": 1, "bays": bays,
                             "z_bottom": est(zb, 0.15, ev4, how_b),
                             "z_top": est(zt, 0.15, ev4, how_t),
                             "width": est((WIDE_W if wide else WIN) * scale, 0.1, ev4, "070204974：広い窓 120.4 px ＝ 2.24 m（柱型 1.20 m）" if wide else "柱間隔 − 柱型（柱型 0.575 周期）"),
                             "recess": est(0.2, 0.1, ev, "目視")})
        # 広い段の柱型にも中ほどに帯が 1 本ある（070204974 の柱型 x=1700 の縦の断面で y 1254。検収 B の重ねで確認）
        for j, (a0, a1) in enumerate(pier_spans):
            # 引っ込んだ部分の柱型の帯は奥の壁へ。両隣の列（あるもの）が全部外れる柱型だけ外す（W は南端 8・9 列、E は南端 1・2 列）
            if cut and all(i in floors_cut for i in (j, j + 1) if 1 <= i <= cnt):
                continue
            f["ledges"].append({"name": f"{floor}階の帯" + ("" if j == 0 else f"（柱型{j}）"), "along0": round(a0, 3), "along1": round(a1, 3),
                                "z": est(z - BAND_H / 2, 0.1, ev4, "070204974 などの白い帯の中心 − 見付けの半分。柱型の上だけ"),
                                "depth": est(0.1, 0.05, ev, "目視"), "height": est(BAND_H, 0.04, ev4, "帯の見付け 12 px（070204974）"), "material": "concrete"})


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
TOP_OF = {"W": TOP, "E": TOP, "SW1": TOP, "f13": TOP, "f0": TOP, "f16": TOP}     # 外周の帯。ほかは中の塊
for fd in face_defs:
    tz = TOP_OF.get(fd["id"], CORE_TOP)
    f = {"id": fd["id"], "name": fd["name"], "from": fd["from"], "to": fd["to"],
         "length": est(fd["L"], 0.8, [OSM], "OSM の輪郭の辺" + ("（一直線の辺をまとめた）" if fd.get("merged") else "")),
         "top_z": est(tz, 2.0, EV_W4 + [SOK], "西北西面のパラペット（外周の帯）") if tz == TOP else
                  est(tz, 1.0, EV_N5 + EV_SW + [SOK_N, SOK_SW], "中の塊の頂部（塊 core と同じ）。北の脇の壁と南西の引っ込みで測った。ほかの面は同じと仮定"),
         "bays": {}, "windows": [], "bands": [], "openings": [], "glazing": [], "ledges": [], "materials": [tile]}
    faces.append(f)
F = {f["id"]: f for f in faces}
# 5 版で写真から置き直した面の長さ
F["f0"]["length"] = est(S_COL, 0.3, EV_N5 + [SOK_N], "sokuryo_f20：角の柱 285/265 px（3.4 m 奥の縮尺 0.911）→ 5.8/5.9 m")
F["f20"]["length"] = est(S_PW0 - S_COL, 0.4, EV_N5 + [SOK_N, SOK_19], "脇の壁 8.7 m（465/435 px、sokuryo_f20）＋スリット 1.0 m（sokuryo_f19）")
F["f19"]["length"] = est(OPEN19, 0.4, ["PXL_20261103_070003022", SOK_19], "柱型の間：070003022 の正面化で 455→975 px ÷ 50.0 px/m（sokuryo_f19）。OSM の f19 14.16 m は物差しにしない")
F["f18"]["length"] = est(S_F17 - S_PE1, 1.0, [OSM, SOK_19], "東のスリット 1.0 m＋脇の壁。脇の壁の東端は OSM の f17 の位置のまま（写真では白い棟で隠れる）。OSM の f18 8.94 m より 2.4 m 短いのは、柱型＋凹部が OSM の f19 より 1.4 m 長いのを西から積んだため")
F["f17"]["length"] = est(N_FLANK - N_F16, 1.0, [OSM], "東の脇の壁を西と同じ 3.4 m 北へ出したので、OSM の北東の角の段 2.42 m が 5.82 m に伸びた（北東の角は撮影依頼）")
F["f0"]["name"] = "北北東 北西の角の柱（縦のガラス帯 2 本。西北西面の北端）"
F["f20"]["name"] = "北北東 西の脇の壁（小窓 1 列・13 段、凹部側にガラスのスリット）"
F["f18"]["name"] = "北北東 東の脇の壁（西と鏡写しと仮定。下半分は白い棟で隠れる）"
F["f17"]["name"] = "東北東 北東の角の段（OSM。脇の壁を北へ出したぶん伸びた）"

# 西北西面：9 列、南端（to 側）2 列は 8 階から上を外す（南西の角の引っ込み。sokuryo_SW1）
w = F["W"]
W_MF = PIER / 2       # 北端（from 側）は柱型から始まる：070327678 の角 1215 px と柱型 0 の左端 1222 px
w["bays"] = {"count": est(9, None, EV_W + [OBS_REF], "観察票：窓の縦の列 9（070327678 の正面化でも 9）", unit="個"),
             "pitch": est(bay_m, 0.08, EV_W4 + [OSM, SOK], "OSM の辺 32.67 m ÷ 壁の幅 9.49 周期（070327678：角 1215〜南端 約 3105 px、周期 199.1 px）。検算：屋上輪郭の西辺 32.16 m で 3.39 m（−1.6%）"),
             "pier_width": est(PIER, 0.1, EV_W4 + [OBS_REF], "柱型は柱間隔の 0.575（070204974 で 105〜108 px / 184.9 px）"),
             "margin_from": est(W_MF, 0.15, EV_W4, "北端は柱型から始まる（070327678・070334137 で角と柱型 0 の左端が一致）"),
             "margin_to": est(w["length"]["value"] - 9 * bay_m - W_MF, 0.5, EV_W4 + [OSM], "残り（南端の柱型 9 は 104 px とやや細く写る。その奥に小窓の帯の別の壁）"),
             "pier_depth": unk(note="柱型と壁の面の出入りは写真から読めない（3 版の 0.15 m は目視）。0.15 m の箱を全高に立てると、柱型の上の白い帯と最上部 2 段の広い窓が隠れた（検収 B、070204974）。凹凸は窓の奥行き 0.2 m で出す")}
strip_windows(w, EV_W, range(1, 10), floors_cut=(8, 9), wide_top=True)
w["render_view"] = True
# 南西の角の引っ込んだ奥の壁（中の塊の西の面）。帯は同じ階に見える（sokuryo_SW1 の周期 230.3/236.9 px）。窓の割付は西北西面と同じと仮定
sb = {"id": "Wsb", "name": "西北西 南西の角の引っ込んだ奥の壁（8 階から上、5.4 m 奥）", "from": WP(32.67 - SB_W, SB_D), "to": WP(32.67, SB_D),
      "length": est(SB_W, 0.3, ["PXL_20261103_070327678", SOK_SW], "正面化で 1.95 列（388 px）＝6.7 m"),
      "top_z": est(CORE_TOP, 1.0, EV_SW + [SOK_SW], "頂部 z14.01/14.03（070327678・070334137）＝中の塊と同じ"),
      "bays": {"count": est(2, None, EV_SW + [SOK_SW], "南端 2 列が引っ込む（sokuryo_SW1）", unit="個"),
               "pitch": est(SB_W / 2, 0.2, EV_SW + [SOK_SW], "壁の幅を 2 等分（西北西面の柱間隔 3.44 m に近い）。窓の位置は写真で読んでいない"),
               "pier_width": est(PIER, 0.2, EV_W4, "西北西面と同じと仮定"), "margin_from": est(0.0, 0.3, [], "壁の幅を 2 等分と仮定")},
      "windows": [], "bands": [], "openings": [], "glazing": [], "ledges": [], "materials": [tile]}
faces.append(sb)
F["Wsb"] = sb
strip_windows(sb, EV_SW, range(1, 3), wide_top=True, from_floor=STEP_FLOOR)
# 東南東面：列数未確定（7＋2 列らしい）。8 列と置く
e = F["E"]
ne_ = 9
EV_E6 = EV_E + ["PXL_20261105_060310392"]
e["bays"] = {"count": est(ne_, None, EV_E6 + [OBS_REF], "060310392 で窓の縦の列を数えた：南端 2 列（低い）＋主塔 7 列＝9（観察票の『7 列＋南端 2 列』と一致）", unit="個"),
             "pitch": est(E_PITCH, 0.3, EV_E6 + [OSM], "OSM の辺 28.33 m ÷ 西北西面と同じ 9.49 周期。西の 3.44 m より 13% 狭い（OSM の東の辺が短いか、列の割付が違うか。東の写真は未登録で柱間隔は測っていない）"),
             "pier_width": est(round(E_PITCH * 0.575, 2), 0.2, EV_W, "柱型は柱間隔の 0.575（西北西面と同じ割合）"),
             "margin_from": est(round(E_PITCH * 0.73 / bay_m, 2), 0.5, EV_E6, "西北西面の南端の余白 0.73 m（柱型 9 の中心から壁の端）と同じ割合。北端は柱型から始まる"),
             "pier_depth": unk(note="西北西面と同じ理由で unknown")}
strip_windows(e, EV_E6, range(1, ne_ + 1), floors_cut=(1, 2), wide_top=True, scale=E_PITCH / bay_m)       # 窓の幅も柱間隔の割合で
e["render_view"] = True
# 南南西の角寄り：窓の帯 2 列。SW1 は段（8 階の床）より下だけ（上は西の 5.4 m が無くなり、残りの面の窓は unknown）。f13（南東の角）は引っ込みが unknown なので全高
for fid in ("SW1", "f13"):
    f = F[fid]
    f["bays"] = {"count": est(2, None, EV_S + [OBS_REF], "観察票：窓の帯 2 列", unit="個"), "pitch": est(bay_m, 0.2, EV_W, "西北西面と同じ"),
                 "pier_width": est(PIER, 0.2, EV_W, "同上"), "end_margin": est((f["length"]["value"] - 2 * bay_m) / 2, 0.4, EV_S, "余りを両端に半分ずつ"), "pier_depth": unk(note="西北西面と同じ理由で unknown")}
    strip_windows(f, EV_S, range(1, 3), top=TOP, to_floor=(STEP_FLOOR - 1 if fid == "SW1" else 99))
# f7・f11：無地。f8・f10（凹部の内側）、f6・f12（角の小面）：各階に小窓 1 つ
for fid in ("f8", "f6", "f12"):
    small_windows(F[fid], EV_S if fid == "f8" else EV_S + ["PXL_20261103_070409570"])

# --- 2026-11-06 検収係（目印係 3 本と窓検出の係が指した穴。out/tower75/kenshu_PXL_20261105_061649102.json） --------------
# 南の面の階の梯子：西北西面と同じ帯（3〜8 階 4.51 m、9 階から上 4.43 m）。登録済みの南の写真（061649102・062246708）に梯子を写すと
# f7・f11 の白い帯と f10 の小窓の段に乗る（kenshu の lines）
EV_S6 = ["PXL_20261105_061649102", "PXL_20261105_061704401", "PXL_20261105_061711714", "PXL_20261105_062246708",
         "PXL_20261105_062254010", "PXL_20261105_062300065", "PXL_20261105_062307232", "PXL_20261105_062311895"]
KEN6 = "out/tower75/kenshu_PXL_20261105_061649102.json"


def ladder(top):
    """(階, 帯の中心 z, 次の帯の z)。z は西の広場から。top − 1.0 m より下の帯だけ。"""
    k, z, out = 1, PODIUM_TOP, []
    while z < top - 1.0:
        h = FLOOR_LO if k <= 6 else FLOOR_UP
        out.append((k + 2, round(z, 2), round(z + h, 2)))
        z += h
        k += 1
    return out


# f10（凹部の東の内壁）：小窓は面の中ほどではなく f11 の角のすぐ脇に各階 1 つ、中の塊の頂部の下まで（3〜16 階）。
f10 = F["f10"]
L10 = f10["length"]["value"]
F10_HOW = ("061649102 に今の面（f10）の梯子と to 端から 0.5/1.0/1.5/2.0 m の縦線を写して読んだ：小窓の列は to 端（f11 の角）から約 1.0 m を中心に並ぶ。"
           "3 版の『面の中ほどに 1 つ』（観察票）は位置が違った")
f10["bays"] = {"count": est(1, None, EV_S6 + [KEN6], "各階に小窓 1 つ（写真で 14〜16 段）", unit="個"),
               "pitch": est(2.0, 0.4, EV_S6, "窓の中心から角までの 1.0 m の 2 倍（窓の位置を出すための割付）"),
               "pier_width": est(2.0 - 1.3, 0.3, EV_S6, "小窓 1.3 m（目視）を引いた残り"),
               "margin_from": est(L10 - 2.0, 0.5, EV_S6, F10_HOW), "margin_to": est(0.0, 0.2, EV_S6, "角寄りの端")}
for fl, z, zn in ladder(CORE_TOP):
    f10["windows"].append({"name": f"{fl}階小窓", "floors": [fl], "per_bay": 1,
                           "z_bottom": est(z + 1.0, 0.3, EV_S6, "目視（帯から 1.0 m）。段は梯子の帯の間ごと"),
                           "z_top": est(z + 2.4, 0.3, EV_S6, "目視（帯から 2.4 m）"),
                           "width": est(1.3, 0.3, EV_S6, "目視"), "recess": est(0.2, 0.1, EV_S6, "目視"), "rail": False})
f10["windows_note"] = "14 段（3〜16 階、中の塊の頂部 71.5 m の下まで）。写真では 14〜16 個に見える"

# f7・f11（凹部の両脇の窓の無い壁）：白い帯が各階にある（3 版の『無地』は窓が無いという意味で、帯はあった）
# 1 回目の recheck で f7 の帯は写真が 0.26 m 下（28 本の中央値。061649102 で −0.20〜−0.27、061704401 で −0.32〜−0.39 と 1 枚の中でそろう）、
# f11 は −0.09 m（21 本）で合格の内。f7 だけ 0.25 m 下げた（同じ高さのはずの f11 と食い違うので、f7 の面の位置か視点の偏りの可能性も残る：soten）
# 2 回目：0.25 m 下げても中央値は −0.21 m（34 本）で縮まらない＝帯の高さではなく、縁の探し方か f7 の面の位置・視点の偏り。2 回で収束しないので下げを戻した（soten）
BAND_SHIFT = {"f7": 0.0, "f11": 0.0}
for fid in ("f7", "f11"):
    f = F[fid]
    for fl, z, zn in ladder(CORE_TOP):
        f["ledges"].append({"name": f"{fl}階の帯", "z": est(z - BAND_H / 2 + BAND_SHIFT[fid], 0.15, EV_S6 + [KEN6],
                                                            "南の写真に西北西面と同じ梯子を写すと帯に乗る（061649102・062246708）" + ("。recheck 1 回目の中央値 −0.26 m で 0.25 m 下げた" if BAND_SHIFT[fid] else "")),
                            "depth": est(0.1, 0.05, EV_S6, "目視"), "height": est(BAND_H, 0.04, EV_W4, "西北西面の帯と同じ見付け"), "material": "concrete"})
# f9：凹部の奥の全面ガラス＋各階の水平ルーバー
f9 = F["f9"]
f9["name"] = "南南西 中央の凹部（下はガラス・各階に水平ルーバー、上の 6 階ぶんは吹き抜け）"
# 2026-11-06 検収係：061649102・062246708 ではガラスとルーバーは 11 階の床の帯（z 44.4）あたりまでで、その上は頂部の格子枠の下まで
# 奥が暗く抜けた吹き抜け（凹部の奥の壁と縦のガラス帯が見える）。今の f9 の面に梯子を写すとガラスの上端は 062246708 で z 約 46、
# 061649102 で z 約 40（凹部の深さ 8 m が仮なので 2 枚で食い違う）。梯子の帯 z 44.42 に置き ±3 m。吹き抜けの奥行きは unknown（requests）
GLASS9_TOP = ladder(CORE_TOP)[8][1]          # 11 階の床の帯 44.42
f9["glazing"] = [{"name": "凹部の奥のガラス（二重）。上端より上は吹き抜け", "along0": 0.3, "along1": f9["length"]["value"] - 0.3, "z_bottom": PODIUM_TOP,
                  "z_top": est(GLASS9_TOP, 3.0, EV_S6 + [KEN6], "061649102・062246708 に今の f9 の面の梯子を写して、ガラスの上端は z 約 40／46（凹部の深さの仮の 8 m のため 2 枚で食い違う）。間の帯 z 44.42（11 階の床）に置いた"),
                  "inset": est(0.1, 0.1, EV_S, "目視"), "depth": est(2.0, 1.0, EV_S, "目視"), "beam": False}]
for fl, z, zn in ladder(CORE_TOP):
    if zn > GLASS9_TOP + 0.1:
        break
    f9["ledges"].append({"name": f"{fl}階のルーバー", "z": est(zn - 0.6, 0.3, EV_S6, "各階に水平ルーバー（観察票）。ガラスの上端（z 44.4）より上には無い（061649102・062246708）"),
                         "depth": est(0.6, 0.2, EV_S, "目視"), "height": est(0.12, 0.05, EV_S, "目視"), "material": "metal"})
f9["windows_note"] = "ガラスの上端（z 44.4）から頂部の格子枠までは吹き抜け（奥行き unknown）。模型はその間を壁のまま残す（吹き抜けを彫る形が無い）"
f9["render_view"] = True


def north_bands(f, ks, ev, how, a0=0.0, a1=None):
    for k in ks:
        f["ledges"].append({"name": f"帯 z{k:g}", "along0": a0, "along1": a1 if a1 is not None else f["length"]["value"],
                            "z": est(ZN(k) - BAND_H / 2, 0.15, ev, how), "depth": est(0.1, 0.05, ev, "目視"),
                            "height": est(BAND_H, 0.04, EV_W4, "西北西面の帯と同じ見付け"), "material": "concrete"})


# 北北東の中央 f19：浅い凹部の奥の壁に各階 1 本の連窓（5 枚割）。ルーバー・白い帯は無い（kansatsu_f19）
f19 = F["f19"]
f19["name"] = "北北東 中央の凹部の奥の壁（浅い 1.7 m。各階 1 本の連窓・5 枚割。頂部にガラスの箱）"
f19["bays"] = {"count": est(1, None, EV_N5 + [KAZ_19], "列ではなく各階 1 本の連窓（kazoe_f19）", unit="個"),
               "pitch": est(OPEN19, 0.4, ["PXL_20261103_070003022", SOK_19], "柱型の間＝奥の壁の幅"),
               "pier_width": est(0.2, 0.1, ["PXL_20261103_070003022"], "連窓の両脇に残る壁（正面化で奥の壁 525〜975 px に連窓がほぼいっぱい）"),
               "margin_from": est(0.0, 0.1, [], "面いっぱいに 1 本"), "margin_to": est(0.0, 0.1, [], "同上")}
RIB_B, RIB_T = 1.95, 3.63
RIB_HOW = ("070003022 の正面化で連窓の上下 y1120/1200・1337/1418 px を奥の壁の縮尺（214.8 px/段、周期の比 0.956）と目の高さ（z−1.7）で段の番号に直し、"
           "下の帯から +0.44/+0.43 段・+0.815/+0.805 段 → ×4.48 m")
for k in range(2, 13):
    f19["windows"].append({"name": f"連窓 z{k}", "floors": [k + 3], "per_bay": 5, "gap": est(0.15, 0.05, ["PXL_20261103_070003022"], "枠の見付け（目視）"),
                           "z_bottom": est(ZN(k) + RIB_B, 0.2, EV_N5 + [KAZ_19], RIB_HOW), "z_top": est(ZN(k) + RIB_T, 0.2, EV_N5 + [KAZ_19], RIB_HOW),
                           "width": est(OPEN19 - 0.2, 0.3, ["PXL_20261103_070003022"], "奥の壁いっぱい。5 枚割（ガラスブロック／窓／ガラスブロック（幅広）／窓／ガラスブロック、kansatsu_f19）。"
                                        "模型は 5 等分で、ガラスブロックと透明ガラスを分けていない"),
                           "recess": est(0.15, 0.1, ["PXL_20261103_070003022"], "目視"), "rail": False})
f19["windows_note"] = "見えるのは z2〜z12 の 11 段（measured、kazoe_f19）。z0・z1 と基壇は木で隠れて全段数は unknown（requests）"
f19["render_view"] = True

# 面の前の地面の高さ faces[].ground_offset（2026-11-04 設計係）：北の沈んだ広場は levels.north_sunken_plaza（−3.0 m）と同じ値を、
# 広場に面する f20（前は沈んだ広場、北の歩道から階段：kansatsu_f20）と f19（下は沈んだ広場の上のパーゴラのテラス：kansatsu_f19）に書く。
# levels は「どの高さの地面があるか」の記録として残し、ground_offset は「その面の前の地面」を模型・図面・検収に渡す欄（同じ定数 SUNKEN から出すので食い違わない）。
# f18 は下半分が白い棟で隠れ、前が沈んだ広場か分からないので unknown。広場の奥行き（面から歩道の階段まで）は未測で depth は書かない（模型は仮の 12 m）
SUNKEN_HOW = ("levels.north_sunken_plaza と同じ値。sokuryo_f20.sunken_plaza：正面化の目の高さ z−1.69 → 北の歩道 E−2.9 m、広場への階段 16 段（measured、kazoe_f20：下 7・踊り場・上 9）× 0.15 m "
              "→ 広場 E−5.3 m、西の広場は E−2.25 m → 3.05 m 低い。検算：検収 B（070003022）のカメラ z 0.85 m は北の歩道（−0.6 m）＋目の高さ 1.5 m と合う。"
              "国土地理院 5m 標高は面の前 0〜12 m で ground_z 42.9＋4〜5 m、北の歩道（カメラの足元）で 48.4 m と、逆に高く読める（西の広場の前は +1.1〜+2.3 m）。"
              "5m 標高が沈んだ広場とテラス・パーゴラ・橋を分けられていない可能性があるが、未解決（requests）")
for fid, extra in (("f20", "前は沈んだ広場（北の歩道から幅広の階段、kansatsu_f20）。"), ("f19", "下は沈んだ広場の上の青いパーゴラのテラス（kansatsu_f19）。テラス（+2.25 m か）は模型に無い。")):
    F[fid]["ground_offset"] = est(-SUNKEN, 1.0, EV_N5 + ["PXL_20261103_070327678", "PXL_20261103_070123308", SOK_N, "gsi:dem5a@(60,41)"], extra + SUNKEN_HOW)
F["f18"]["ground_offset"] = unk(note="下半分が白い棟で隠れ、前が沈んだ広場か分からない（kansatsu_f18）。北東の角を 45° から撮る依頼（requests）で決める")
# 柱型の前面（帯だけ。頂部はパラペットより 1.7 段高い）
for pid, s0, s1, nm in (("P19w", S_PW0, S_OP0, "西"), ("P19e", S_OP1, S_PE1, "東")):
    pf = {"id": pid, "name": f"北北東 {nm}の柱型の前面（脇の壁より 0.3 m 前）", "from": NP(s1, N_PIER), "to": NP(s0, N_PIER),
          "length": est(PIER19, 0.2, EV_N5 + [SOK_19], "130/140 px（sokuryo_f19）"),
          "top_z": est(PIER_TOP, 1.5, EV_N5 + [SOK_19], "z15.8（sokuryo_f19）"),
          "bays": {}, "windows": [], "bands": [], "openings": [], "glazing": [], "ledges": [], "materials": [tile]}
    north_bands(pf, list(range(2, 13)) + [13.2, 14.06], EV_N5,
                "070003022 の柱型の帯 y910/1134/1361… を柱型の面（0.3 m 前、226.5 px/段）で段の番号に直すと脇の壁の帯と同じ段（z12.04・13.28・14.11 など）")
    faces.append(pf)
    F[pid] = pf

# 脇の壁 f20（西）と f18（東）：小窓 1 列（幅 0.67 m、脇の壁の角寄りの端から中心まで 1.64 m）、凹部側にガラスのスリット
FL_HOW = "070003022 の正面化（50.0 px/m、sokuryo_f20）で小窓 1513 px・幅 33 px、脇の壁 1160〜1595 px → 角寄りの端から中心 1.64 m"
WIN_B, WIN_T, WIN_T12 = 0.98, 3.24, 3.09     # 最上段（z12）の小窓だけ上端が低い（2 枚で +3.04/+3.13。検収 A の 2 回目）
WIN_HOW = ("小窓の下端・上端を帯から：070003022 で z10 +0.96/+3.13・z12 +0.89/+3.04 m（224.7 px/段）、065946407 で z0 +1.05/+3.37・z1 +1.01/+3.31・z10 +0.99/+3.16・z12 +1.03/+3.13 m（239.4 px/段）の平均。"
           "検収 A の 1 回目で上端が +0.66/+0.74 m ずれた（5 版の初めは段の換算を誤って 2.38 m としていた）")
for fid, ev, mirror in (("f20", EV_N5 + [SOK_N, KAZ_N], False), ("f18", EV_N5 + ["kansatsu_f18"], True)):
    f = F[fid]
    L = f["length"]["value"]
    st_how = "" if not mirror else "（西の脇の壁と鏡写しと仮定：kansatsu_f18 の「小窓 1 列、西と鏡写し」。段数は白い棟で隠れて数えていない）"
    if not mirror:      # from 側（東端）がスリット、to 側（西端）が角寄り
        f["bays"] = {"count": est(1, None, ev, "小窓 1 列（kazoe_f20）", unit="個"), "pitch": est(3.28, 0.3, ["PXL_20261103_070003022"], "窓の中心から角寄りの端まで 1.64 m の 2 倍（窓の位置を出すための割付）"),
                     "pier_width": est(3.28 - 0.67, 0.3, ["PXL_20261103_070003022"], FL_HOW),
                     "margin_from": est(L - 3.28, 0.4, ["PXL_20261103_070003022"], "スリット 1.0 m＋脇の壁の残り"), "margin_to": est(0.0, 0.1, [], "角寄りの端")}
        slit = (0.0, SLIT)
    else:               # from 側（東端）が角寄り、to 側（西端）がスリット
        f["bays"] = {"count": est(1, None, ev, "小窓 1 列（西と鏡写しと仮定）", unit="個"), "pitch": est(3.28, 0.5, [], "西の脇の壁と鏡写しと仮定"),
                     "pier_width": est(3.28 - 0.67, 0.5, [], "西の脇の壁と鏡写しと仮定"),
                     "margin_from": est(0.0, 0.5, [], "角寄りの端（鏡写しと仮定）"), "margin_to": est(L - 3.28, 1.0, [], "残り")}
        slit = (L - SLIT, L)
    for k in range(0, 13):
        f["windows"].append({"name": f"小窓 z{k}", "floors": [k + 3], "per_bay": 1,
                             "z_bottom": est(ZN(k) + WIN_B, 0.2, ev, WIN_HOW + st_how), "z_top": est(ZN(k) + (WIN_T12 if k == 12 else WIN_T), 0.2, ev, WIN_HOW + st_how),
                             "width": est(0.67, 0.1, ["PXL_20261103_070003022"], FL_HOW + st_how), "recess": est(0.2, 0.1, ev, "目視"), "rail": False})
    north_bands(f, list(range(0, 13)) + [13.2], ev, "脇の壁の白い帯 z0〜z12 は等間隔（070003022 で 3641〜945 px、224.7 px/段）、z13.2 は最上段の上（670 px）" + st_how,
                a0=(SLIT if not mirror else 0.0), a1=(L if not mirror else L - SLIT))
    f["glazing"].append({"name": "凹部側の縦のガラスのスリット", "along0": slit[0], "along1": slit[1],
                         "z_bottom": est(ZN(2.8), 1.0, ["PXL_20261103_070003022"], "見えている下端（070003022 の 3000 px＝z2.8）。それより下は木で隠れて unknown"),
                         "z_top": est(ZN(13.9), 0.5, ["PXL_20261103_070003022"], "070003022 の 495 px＝z14.0 の少し下（笠木の下）"),
                         "inset": est(0.3, 0.2, EV_N5, "目視"), "depth": est(0.5, 0.3, EV_N5, "目視"), "beam": False})
    f["render_view"] = fid == "f20"
# 北西の角の柱 f0：縦のガラス帯 2 本（070003022 の正面化で 1620〜1680 px と 1755〜1830 px、角の柱 1625〜1890 px・45.5 px/m）
f0 = F["f0"]
f0["glazing"].append({"name": "角の柱の縦のガラス帯", "along0": 2.9, "along1": 4.5,
                      "z_bottom": est(PODIUM_TOP, 0.5, ["PXL_20261103_070003022"], "z0 の帯（低い塊の上）から"),
                      "z_top": est(TOP - 0.4, 0.5, ["PXL_20261103_070003022"], "角の柱の頂部（926 px）のすぐ下 945 px まで"),
                      "inset": est(0.3, 0.2, EV_N5, "目視"), "depth": est(0.5, 0.3, EV_N5, "目視"), "beam": False,
                      "note": "065946407 で 1311〜1380 px（角の柱 1165〜1450 px、49.1 px/m）＝角から 1.4〜2.8 m、070003022 で 1.3〜2.9 m"})
# 脇の壁寄りの 1 本は格子の縦溝（065946407 で灰色の格子に見える。070003022 では空が映って青い）。溝の深さは目視
f0["bands"].append({"name": "角の柱の格子の縦溝", "along0": 0.2, "along1": 1.2,
                    "z_bottom": est(PODIUM_TOP, 0.5, EV_N5, "ガラス帯と同じ範囲"), "z_top": est(TOP - 0.4, 0.5, EV_N5, "同上"),
                    "depth": est(-0.3, 0.2, EV_N5, "目視（溝の奥行き）"), "color": "#8a8f96",
                    "note": "065946407 で 1170〜1230 px（角から 4.5〜5.7 m）、070003022 で 4.6〜5.8 m。kansatsu_f20 の「格子の縦溝」"})
# 北東の角 f16：縦のガラス帯（3 版の観察票、目視のまま）
f16 = F["f16"]
f16["glazing"].append({"name": "角寄りの縦のガラス帯", "along0": 0.6, "along1": 2.0, "z_bottom": PODIUM_TOP, "z_top": est(TOP - 1.5, 1.5, EV_N, "屋上の少し下まで（3 版の観察票、目視）"),
                       "inset": est(0.3, 0.2, EV_N, "目視"), "depth": est(0.5, 0.3, EV_N, "目視"), "beam": False})

b["faces"] = faces

# --- 部品 --------------------------------------------------------------------------------
def rect_from(face, along0, along1, out0, out1):
    (x0, y0), (x1, y1) = face["from"], face["to"]
    L = face["L"] if "L" in face else face["length"]["value"]
    ux, uy = (x1 - x0) / L, (y1 - y0) / L
    nx, ny = uy, -ux
    return [[round(x0 + ux * a + nx * o, 2), round(y0 + uy * a + ny * o, 2)] for a, o in ((along0, out0), (along1, out0), (along1, out1), (along0, out1))]


BOX_BACK = -4.9      # 屋上の精読の PH1 の南の縁（n＝−4.84/−4.88）
parts = [
    {"type": "coping", "name": "パラペットの笠木", "height": est(0.24, 0.05, EV_W4, "070204974 で白い笠木 13 px"), "overhang": est(0.1, 0.05, [], "目視")},
    {"type": "box", "name": "北北東 頂部のガラスの箱（柱型の間。屋上の精読の候補 PH1）", "material": "glass",
     "polygon": [NP(S_OP0, N_PIER), NP(S_OP1, N_PIER), NP(S_OP1, BOX_BACK), NP(S_OP0, BOX_BACK)],
     "z0": est(BOX_Z0, 2.0, EV_N5 + [SOK_19], "z12.6（070003022 の正面化で箱の前面を柱型の面に置いて換算。箱が前へ出ていれば少し低い）"),
     "z1": est(BOX_Z1, 2.0, EV_N5 + [SOK_19], "z15.6（同上）"),
     "front": est(N_PIER - N_RECESS, None, EV_N5, "前面は柱型の面にそろえた。下面が見える＝奥の壁より前へ出るが、出の量は unknown（kansatsu_f19）"),
     "back": est(N_RECESS - BOX_BACK, 1.0, EV_AER, "屋上の精読の PH1（12×5.8 m）の南の縁まで")},
    {"type": "box", "name": "屋上の設備の列 1（屋上の精読）", "material": "metal", "polygon": rect_from(F["f7"], -1.0, 26.0, -6.0, -10.0),
     "z0": est(CORE_TOP, 1.0, EV_N5 + EV_SW, "中の塊の屋上"), "z1": est(CORE_TOP + 2.5, 1.0, EV_AER, "屋上の精読：27×4 m の列が 2 本。高さは目視")},
    {"type": "box", "name": "屋上の設備の列 2（屋上の精読）", "material": "metal", "polygon": rect_from(F["f7"], -1.0, 26.0, -12.0, -16.0),
     "z0": est(CORE_TOP, 1.0, EV_N5 + EV_SW, "中の塊の屋上"), "z1": est(CORE_TOP + 2.5, 1.0, EV_AER, "同上")},
    {"type": "box", "name": "通路橋（西北西から取り付く白い橋。屋上の精読 29×7 m）", "material": "concrete",
     "polygon": rect_from(F["W"], 3.0, 10.0, 4.0, 33.0), "z0": est(7.5, 1.0, EV_W + EV_AER, "基壇の上あたりの高さ（観察票）。倒れ込みの小ささから約 10 m（屋上の精読）"), "z1": est(9.5, 1.0, EV_AER, "床板と手すりで 2 m と置く")},
]
b["parts"] = parts

# --- 写真（観察票の仕分け直しを索引にも反映） ------------------------------------------------
idx = load_index()
re_map = {r["photo"]: r["new"] for r in OBS.get("face_reassignment", [])}
# 2026-11-06：索引は仕分け係の持ち物なので書き戻さない（以前は観察票の振り分けで上書きしていた）。観察票の振り分けは索引の面が空の時だけ使う
for rec in idx["photos"]:
    if rec.get("building") == "tower75" and rec["id"] in re_map and not rec.get("face"):
        rec["face"] = re_map[rec["id"]]
b["photos"] = [{"id": r["id"], "file": r["file"], "face": r.get("face"), "role": "全体" if r["id"] in EV_W + EV_N + EV_S + EV_E else "細部"}
               for r in idx["photos"] if r.get("building") == "tower75"]
b["requests"] = [{"face": "f19", "what": "北北東の凹部の下半分を正面で（連窓の z0・z1 と足元、スリットの下端）", "where": "北の芝生の木の無い所か、沈んだ広場の中から", "why": "下 2 段と基壇が木で隠れ、連窓の全段数とスリットの下端が unknown（kazoe_f19）", "status": "open"},
                 {"face": "f9", "what": "南南西の全体の正面と、凹部を斜め 45° から", "where": "南の広場から", "why": "凹部の深さ（8 m と置いた）が未確定。北の f19 は 1.7 m と浅く、対称ではなかった", "status": "open"},
                 {"face": "E", "what": "東南東の全体を日中に、屋上まで", "where": "東側の道路から", "why": "列数が未確定（7＋2 か）。東の外周の帯の高さ・奥行きは西と同じと仮定（5 版）", "status": "open"},
                 {"face": "f18", "what": "北東の角を 45° から（白い棟に隠れない位置）", "where": "北東の道路から", "why": "東の脇の壁の位置（西と鏡写しで 3.4 m 北へ出すと仮定）・段数・北東の角の段の高さが unknown（kansatsu_f18）", "status": "open"},
                 {"face": "f13", "what": "南東の角を 45° から、塔の上まで入るように", "where": "南東の交差点から", "why": "南東の角の上部の引っ込み（何階から・奥行き）が unknown。写真は強い見上げ 1 枚（074606922、sokuryo_SW1）", "status": "open"},
                 {"face": "SW1", "what": "南西の角を 45° から、段の上の南向きの面が入るように", "where": "南西の交差点", "why": "8 階から上の南向きの面（引っ込みの横の壁と残りの 2.75 m）の窓が unknown。段の奥行き 5.4 m の検算にもなる", "status": "open"},
                 {"face": "f0", "what": "北西の角を 45° から、低い塊と角の柱が入るように", "where": "北西の歩道から", "why": "北西の角の低い塊（ガラスの縦帯 2 本）の出と高さが unknown。角の柱の 2 本の縦帯のどちらかが溝か（観察票と正面化で食い違い）", "status": "open"},
                 {"face": "*", "what": "屋上を高い所から", "where": "", "why": "中の塊（71.5 m）の平面は南西と北西の角でしか確かめていない（外周の帯 5.4 m）。脇の壁の奥にさらに高い塊が両側に見える（070003022）。ガラスの箱の出の量", "status": "open"},
                 {"face": "f20", "what": "北の沈んだ広場の中から、階段と西の脇の壁の足元を横向きに 1 枚", "where": "北の沈んだ広場", "why": "沈んだ広場の深さ 3.0 m は階段 16 段×慣用の蹴上げ 0.15 m と目の高さの仮定から（±1 m）。PLATEAU 74.2 m の説明（3.0＋71.5）の根拠", "status": "open"},
                 {"face": "*", "kind": "tool", "what": "模型の地面を場所ごとの高さで描けるように（levels か pavement で低い地面を彫る）", "where": "recorder/blender_build.py の地面（z=0 の 1 枚）",
                  "why": "北の沈んだ広場（levels.north_sunken_plaza、−3.0 m）が模型に出ない。地面の板が −0.3〜0 m で全部を覆うので、部品を z<0 に置いても隠れる", "status": "open"},
                 {"face": "W", "what": "西北西面の北端（通路橋の下）を、柱の番号が端から数えられるように 1 枚", "where": "西の広場の北寄り", "why": "070204974 の検収 B で南西の角の引っ込みが 1 列ぶん合わない。4 版の pose の柱の番号（北端が通路橋で隠れる）か、引っ込みの幅（正面の 070327678 では 2 列）かを決める", "status": "open"},
                 {"face": "*", "what": "昼の日陰のタイルを 1 m から", "where": "", "why": "色が夕日で橙に寄っている。タイルの形式も未確認", "status": "open"}]
b["history"] = [
    {"at": "2026-11-03T21:30", "by": "Claude（設計者）", "what": "南南西面の写真 1 枚と PLATEAU の高さから初版（長方形）"},
    {"at": "2026-11-03T22:20", "by": "Claude（設計者）", "what": "2 版：足元を OSM の輪郭に、全面に窓、北にガラス帯、東南東にガラスの張り出し"},
    {"at": "2026-11-04T01:30", "by": "Claude（設計者）", "what": "3 版：観察票と屋上の精読に従う。写真 11 は西北西面だった。凹部のガラス（南南西）、北の凹部の連窓と頂部のガラスの箱、北の 2 角のガラス帯、南の 2 角の上部の引っ込み、屋上の設備、通路橋。東南東の張り出しと f19 のガラス帯は撤回"},
    {"at": "2026-11-04T03:40", "by": "測量係・検収係（Claude, claude-opus-5-5）", "what": "4 版：西北西面の正面化 5 枚で縮尺を OSM の辺に取り直す（壁＝9.49 周期、北端は柱型から）。柱間隔 3.43→3.44、階高 4.40→3〜8 階 4.51／9 階から上 4.43（平均 4.47）、パラペット 74.2→67.9（西の広場から。PLATEAU と −8.5% で 5% 超、理由は out/tower75/sokuryo_W.json）、基壇の上 7.8→8.5、窓の列は帯の 0.20 m 上〜次の帯の下、帯の見付け 0.3→0.22、最上部 2 段の広い窓 2.92→2.24 m と高さ、白い帯を柱型の上だけに（窓の列で切れる）、柱型の出 0.15 m（目視）を unknown に（全高の箱が帯と広い窓を隠していた）、西北西面の余白を北 0.98 m／南 0.73 m に。タイルの周期を 070204974 で読み直し"},
    {"at": "2026-11-04T04:30", "by": "設計係（Claude, claude-opus-5-5）", "what": "5 版：北北東側・南西の上部・頂部を kansatsu_north.md と sokuryo_f19/f20/SW1・kazoe_f19/f20 で置き換え。1 塊（67.9 m）をやめ、西の外周の帯（5.4 m、67.9 m）・南西の角の下（段の笠木 31.95 m＝8 階の床の帯の 0.2 段上）・東の外周の帯（西と同じと仮定）・中の塊（71.5 m）・北の柱型 2 本（79.3 m）に分けた。南西の引っ込みは bottom_z を使わず、下だけの塊（Ws）と奥の中の塊の足し算で表した（上だけ引っ込む角）。南東は unknown のまま。f19 の凹部 8 m（仮）→1.7 m、柱型 2.6 m・柱型の間 10.4 m、連窓は z2〜z12 の 11 段・5 枚割・ルーバー無し・白い帯無し。脇の壁は角の柱より 3.4 m 北（角の柱の北面を OSM の線に）、小窓は幅 0.67 m・13 段、凹部側にスリット 1.0 m。頂部のガラスの箱 67.9〜71.9 m（目視）→65.0〜78.4 m。北の沈んだ広場（−3.0 m）は levels に、模型の地面では表せないので requests（kind: tool）。PLATEAU 74.2 m は沈んだ広場から中の塊の頂部まで 74.5 m（+0.4%）と読む（塊 core の note）。floor_count 16 を measured に（沈んだ層 1＋基壇 2＋13）"},
    {"at": "2026-11-04T04:38", "by": "検収係（Claude, claude-opus-5-5）", "what": "5 版の検収：北北東の正面化 070003022・065946407 で f20・f19・柱型の線を手で読んで compare（kenshu_PXL_20261103_070003022/065946407.json）。脇の壁の小窓の上端 帯＋2.38→3.24 m（最上段 3.09）・下端 0.95→0.98（1 回目で +0.66/+0.74 m。段の換算の誤り）、北西の角の柱の脇の壁寄りの縦帯をガラスから格子の溝に（065946407）。2 回目で全線 0.15 m 以内。検収 B のカメラ (60.99, 77.28, 0.85)・(49.15, 80.9, 0.78)。070204974 の検収 B では南西の角の引っ込みが 1 列ぶん合わない（4 版の pose の柱の番号か、引っ込みの幅か。値は直さず requests）"},
]
for rq in b["requests"]:
    if rq.get("kind") == "tool" and "沈んだ広場" in rq.get("why", ""):
        rq["status"] = "done"
        rq["note"] = "2026-11-04 schema に faces[].ground_offset が入り、f20・f19 の前に −3.0 m の沈み（奥行きは仮の 12 m）を作れるようになった"
b["requests"].append({"face": "f20", "kind": "data", "what": "北の沈んだ広場・北の歩道の高さと 5m 標高の食い違いを確かめる",
                      "where": "広場の中と北の歩道で、同じ 1 枚に段と足元が入る写真（または現地で段の数え直し）",
                      "why": "写真の推定では北の歩道は西の広場より 0.6 m 低く、沈んだ広場は 3.0 m 低い。5m 標高では北側（48.4 m）が西の広場の前（44〜45 m）より 3〜4 m 高い。どちらかが違う",
                      "status": "open"})
b["history"].append({"at": "2026-11-04T05:40", "by": "設計係（Claude, claude-opus-5-5）",
                     "what": "北の沈んだ広場（−3.0 m ±1）を faces.f20・f19 の ground_offset にも書いた（levels.north_sunken_plaza は記録として残し、同じ定数から出す）。"
                             "f18 は前が見えないので ground_offset を unknown。模型は面の前に沈み（奥行き 12 m は仮）を作る。"
                             "5m 標高は北側を逆に 4〜5 m 高く読み、写真の推定と合わない（requests に追加、未解決）"})

# --- 検収 C（意味の検査、2026-11-04 検収係）：out/tower75/kenshu_C.json ----------------------------------
# 部品・塊ごとに「役割・役割に合う大きさ・航空写真・二重計上」を見た。外したのは次の 4 つ（値は直さない。直し方は requests）。
KC = "out/tower75/kenshu_C.json"
KC_REMOVE_BLOCKS = {"C": "基壇：輪郭は塔の足元を 4 m 広げた仮の形（形は unknown と自分で書いている＝仮の幅）。写真の基壇は西北西・東南東の壁そのものの下 2〜3 層で、"
                         "4 m 出た台は写っていない（観察票 #8・#15・#16・#23・#24）。北側では沈んだ広場（−3.0 m）とパーゴラのテラスの上に 8.5 m の台が載ってしまう。航空写真でも塔の周りに台の屋根は無い（roof.json の C の屋根の明るさ 71.9＝影の地面）"}
KC_REMOVE_PARTS = {
    "屋上の設備の列 1": "列の 26%（28.6 m²）が南南西の凹部（8 m に深くした所）の上に出て宙に浮く。高さ 2.5 m は真上の写真では読めない値（『目視』）で、室外機の目安 1 m の 2.5 倍。位置も屋上の精読 EQ-A と 55% しか重ならない",
    "屋上の設備の列 2": "高さ 2.5 m は真上の写真では読めない値（『目視』）で室外機の目安の 2.5 倍。位置は屋上の精読 EQ-B と 38% しか重ならず中心が 2.6 m ずれる（rect_from(f7) で置いたため）",
    "通路橋": "置き場所が屋上の精読の橋（podium_bridge.bridge）と 63% しか重ならず中心が 4.4 m ずれる。塔の壁から 4 m 離れた所（仮の基壇の縁）から始まり、基壇を外すと壁から離れて宙に浮く。柱（観察票：2 本ずつ並ぶ）も無い。床板 2 m は『置く』",
}
b["blocks"] = [bl for bl in b["blocks"] if bl["id"] not in KC_REMOVE_BLOCKS]
b["parts"] = [p for p in b["parts"] if not any(k in p.get("name", "") for k in KC_REMOVE_PARTS)]
parts = b["parts"]
b["requests"] += [
    {"face": "*", "kind": "data", "what": "屋上の設備の列 2 本の高さ（屋上が見える高い所から斜めに 1 枚、または影の長さが読める日中の航空写真）",
     "where": "隣の高い棟の上の階", "why": f"検収 C で列を外した（{KC}）。置き直すときは屋上の精読の EQ-A・EQ-B の多角形（out/tower75_屋上.json）をそのまま使い、高さは rooftop_units の語彙で根拠付きに。列 1 は凹部の深さ（下記）が決まるまで置けない",
     "status": "open"},
    {"face": "W", "kind": "data", "what": "通路橋を真横から 1 枚（床の下端・屋根・柱が入るように）と、塔に取り付く所を 1 枚",
     "where": "西の広場の北寄り、橋から離れて",
     "why": f"検収 C で外した（{KC}）。置き直すときは屋上の精読の橋の多角形 (9.8,44.6)(35.6,31.2)(38.8,37.4)(13.1,50.8) と柱（column）で。床の高さ 7〜13 m は倒れ込みからの粗い値",
     "status": "open"},
    {"face": "*", "kind": "data", "what": "塔の足元の基壇（下 2〜3 層）が壁から前へ出ているかを、各面の角を 45° から 1 枚ずつ",
     "where": "4 つの角", "why": f"検収 C で『輪郭を 4 m 広げた仮の基壇』を外した（{KC}）。南東の角だけ #22 で『2 層ほどの基壇が角を包む』と見える。出の量は写真に無い",
     "status": "open"},
    {"face": "f9", "kind": "check", "what": "南南西の凹部の深さ 8 m（仮）と航空写真の食い違いを決める（凹部を斜め 45° から 1 枚、上の格子の枠も入れる）",
     "where": "南の広場", "why": "航空写真の屋上の輪郭は凹部の所で OSM と同じ 2.8 m の切り欠きしか無く、設備の列 EQ-A がその 1.2 m 奥から始まる。8 m にした輪郭とは 68.8 m² 食い違う。"
     "観察票の『暗い四角＝凹部の上の開口』は倒れ込みを戻す前の読みで、屋上の精読では暗い四角（VOID）は凹部ではなく屋上の中ほど (44.7,16.1)〜(59.3,16.4)。ガラス壁だけが奥で、上は格子の枠と屋根が 2.8 m の線まで来ている可能性",
     "status": "open"},
    {"face": "f20", "kind": "check", "what": "北の脇の壁が角の柱より 3.4 m 北へ出ているか（北西の角を真上か 45° から）",
     "where": "北西の歩道か隣の棟の上の階", "why": "航空写真の屋上の輪郭は北辺が北西の角から一直線（OSM の線の 0〜1 m 北）で、模型の脇の壁 f20 の中点は輪郭の 3.8 m 外、柱型は 2.7 m 外（69 m² 食い違う）。sokuryo_f20 の『角の柱の北面を OSM の線に置く』か、航空写真の北辺か、どちらかが違う",
     "status": "open"},
    {"face": "E", "kind": "check", "what": "南東の角の上部が屋上まで届いているか（南東から 45° で塔の上まで）",
     "where": "南東の交差点", "why": "航空写真では南東の角 4.5×6.4 m（屋上の精読 glass_projection.se_notch_xy）に屋上の高さの像が無いが、模型の東の外周の帯 Es はそこを 67.9 m まで立てている（22.5 m²）。東南東面の #23 でも南端 2 列の頂部が 2 段に低くなる",
     "status": "open"},
    {"face": "*", "kind": "check", "what": "北の柱型（79.3 m）と頂部のガラスの箱（78.4 m）が PLATEAU の高さ 74.2 m を超えることの説明",
     "where": "", "why": "core の note では PLATEAU 74.2 m＝沈んだ広場（−3.0 m）から中の塊の頂部（71.5 m）まで。PLATEAU の高さが最高点なら、柱型は沈んだ広場から 82.3 m で 8 m 超える。PLATEAU の取り方（最高点か代表値か）を確かめる",
     "status": "open"},
    {"face": "*", "kind": "tool", "what": "パーゴラ（青い鉄骨の格子の屋根）の語彙",
     "where": "recorder/parts_geom.py", "why": "北の沈んだ広場の上のテラスの青いパーゴラ（観察票 #0・#1・#2）と西の入口の青い鉄骨のひさし（#15・#16・#28）を表す部品が無い。porch は箱の屋根なので格子にならない",
     "status": "open"},
    {"face": "*", "kind": "tool", "what": "渡り廊下・通路橋の語彙（両端の取り付き面・床の高さ・幅・柱）",
     "where": "recorder/parts_geom.py", "why": "塔には通路橋が 3 本ある（北西の白い橋、南西の道路をまたぐ橋、東南東の 3〜4 階のガラスの渡り廊下）。box と column の組み合わせでは取り付きと宙に浮くかの検査ができない",
     "status": "open"},
    {"face": "*", "kind": "tool", "what": "recorder photos roof の屋上の高さ：parapet_top が棟座標（西の広場からの 67.9 m）なのに ground_z 42.9 より大きいので標高と取り違え、height_m＝25.0 m と出る",
     "where": "recorder/roof.py の roof_height", "why": "倒れ込み率 0.211 と塊ごとのずれ（中の塊 15.0 m）は比で計算するので結果は正しいが、lean_m 5.27 m と height_m は意味が違う数になって読み手を誤らせる",
     "status": "open"},
    {"face": "f20", "kind": "tool", "what": "沈んだ広場の奥行き（模型の仮の 12 m）を使わない描き方",
     "where": "recorder/blender_build.py の ground_offset", "why": "奥行きは未測で、仮の幅は CLAUDE.md の決まり 5 に当たる。奥行きが unknown のときは面の前に印だけ置くなど",
     "status": "open"},
]
b["history"].append({"at": "2026-11-04T11:49", "by": "検収係（検収 C, Claude, claude-opus-5-5）",
                     "what": "検収 C（意味の検査、out/tower75/kenshu_C.json・kenshu_C.md）。外した：塊 C（基壇。塔を 4 m 広げた仮の形）、部品 屋上の設備の列 1・2（高さ 2.5 m が真上の写真では読めない値、列 1 は凹部の上で宙に浮く）、"
                             "通路橋（屋上の精読と 4.4 m ずれ、仮の基壇の縁から始まるので基壇を外すと浮く）。残した：笠木、頂部のガラスの箱（屋上の精読 PH1 と 76% 重なる。高さは正面化で測った）、柱型 2 本、外周の帯・中の塊・南西の角。"
                             "倒れ込みは recorder photos roof で率 0.211（中の塊 15.0 m、屋上の精読の手の読み 15.1 m と一致）。航空写真との食い違い 3 件（南南西の凹部 8 m、北の脇の壁 3.4 m、南東の角）は値を直さず requests へ"})

# --- 内装：1 階の学生ホール（2026-11-05 夜、設計係。決定 D-0002 内装の語彙・D-0003 机と椅子） ----------------------------
# 写真 6 枚（索引 interior: true, floor: 1）。3・5・6 は人の顔・掲示物が写るので寸法の根拠にだけ使い、動画・README には出さない。
# 座標は西北西面 W に沿った (a, d)：a＝北端から南へ（m）、d＝面から内側（東南東）へ（m）。WP(a, d) で棟の座標へ。
IP_HALL, IP_CORR, IP_DESK, IP_ENT = "20261105_165235743", "20261105_165344878", "20261105_165424644", "20261105_165632499"
IP_P5, IP_P6 = "PXL_20261105_075519850", "PXL_20261105_075532769"
EV_IN = [IP_HALL, IP_CORR, IP_DESK, IP_ENT, IP_P5, IP_P6]
EV_ENT_OUT = ["PXL_20261103_070204974", "PXL_20261103_070327678", SOK]
FACE_NOTE = "写真 20261105_165424644・PXL_20261105_075519850・PXL_20261105_075532769 は人の顔と掲示物が写る：寸法の根拠にだけ使い、動画・README には出さない"
A_N = 4.0        # ホールの北の壁（カウンターの壁）の a
A_S = 18.0       # 写っている範囲の南の端（ここから先は未確認）
D_E = 25.0       # 写っている範囲の奥（東南東）の端
W_u = ((groups[0]["to"][0] - groups[0]["from"][0]) / groups[0]["L"], (groups[0]["to"][1] - groups[0]["from"][1]) / groups[0]["L"])
YAW_A = round(math.degrees(math.atan2(W_u[1], W_u[0])), 1)          # a の向き（南南西）
P5_HOW = ("PXL_20261105_075519850（14 mm 相当、f＝1871 px）で柱 3 本の幅と天井との取り合いから距離を出し（目の高さ 1.3 m・天井 4.3 m）、"
          "天井の照明の列の消失点（カメラの向きからホールの軸が 33° 右）で回した")


def ia(a, d):
    return WP(a, d)


def chair_pair(a, d, side, ev):
    """机の片側の椅子 2 脚（a に沿って 0.9 m 間隔、机の方を向く）。count/pitch は向きの方向に並ぶので 1 脚ずつ書く。"""
    return [{"type": "asset", "asset": "chair_stack", "at": ia(a + da, d + side * 0.75), "rot_deg": YAW_A + (180 if side < 0 else 0),        # 模型の正面は yaw=0 で -y（assets_blender.import_model）→ 机の方を向く
             "count": 1, "status": "estimated", "evidence": ev,
             "method": "机の長い辺に 2 脚ずつ（写真の机はどれも長い辺に 2 脚ずつ）、机の中心から 0.75 m・横に ±0.45 m。見た目は CC0 のスタッキングチェア（布張りの椅子の代用）"}
            for da in (-0.45, 0.45)]


desk_rows = [21.0, 17.4, 13.8, 10.2, 6.6]
desk_cols = [6.5, 11.35, 15.8]
ROW_HOW = ("PXL_20261105_075519850 で机の列を数えた：ホールの軸に沿って 5 列（列の間隔は机の奥行き＋椅子 2 脚ぶん≒3.6 m、柱の行の間隔 10.7 m の約 1/3）、"
           "横に 3 台（北の壁と柱の列の間・柱の列の間・柱の列の外。柱の横の間隔 4.7 m に 1 台ずつ）。写真の机は約 30 台見える（2 台つなぎを含む）が、"
           "奥の机は重なって 1 台ずつの位置が読めないので 15 台（升目に 1 台）と置いた。位置は ±1.5 m")
iparts = []
for d_ in desk_rows:
    iparts.append({"type": "asset", "asset": "table_long", "at": ia(desk_cols[0], d_), "rot_deg": YAW_A, "count": 3,
                   "pitch": est(round((desk_cols[2] - desk_cols[0]) / 2, 2), 1.0, [IP_P5, IP_DESK], ROW_HOW),
                   "size": {"w": est(1.6, 0.3, [IP_P5, IP_P6], "手前の机に椅子が 2 脚並ぶ（椅子の幅 0.5 m＋間）。長机の慣用 1.5〜1.8 m"),
                            "d": est(0.75, 0.15, [IP_P5, IP_P6], "慣用（会議用の長机 0.6〜0.9 m）。写真で幅の約半分"),
                            "h": est(0.72, 0.05, [IP_P6], "慣用（机の高さ 0.70〜0.72 m）")},
                   "status": "estimated", "evidence": [IP_P5, IP_P6, IP_DESK], "method": ROW_HOW})
    for a_ in desk_cols:
        iparts += chair_pair(a_, d_, -1, [IP_P5, IP_P6, IP_DESK]) + chair_pair(a_, d_, +1, [IP_P5, IP_P6, IP_DESK])
iparts += [
    {"type": "box", "name": "壁沿いのカウンター（ハイカウンター、背の高い椅子が並ぶ）", "at": ia(A_N + 0.3, 20.0), "rot_deg": YAW_A + 90, "count": 1,
     "size": {"w": est(6.0, 2.0, [IP_P6, IP_P5], "PXL_20261105_075532769 で背の高い椅子 7 脚（間隔約 0.7 m）＋端。奥の端は柱型の手前まで"),
              "d": est(0.6, 0.15, [IP_P6], "天板の奥行き：椅子の座面の幅（約 0.45 m）の約 1.3 倍"),
              "h": est(1.0, 0.1, [IP_P6], "背の高い椅子の座面（約 0.65 m）より天板が 0.35 m ほど上（座面の高さとの比）")},
     "material": "wood", "status": "estimated", "evidence": [IP_P6, IP_P5],
     "method": "北の壁（窓とロールスクリーンの壁）に沿う。PXL_20261105_075532769・075519850 はその壁の端から撮った物。位置は ±2 m"},
    {"type": "box", "name": "自販機（3 台：コーヒー・青・赤）", "at": ia(8.0, D_E - 0.45), "rot_deg": YAW_A, "count": 3,
     "pitch": est(1.05, 0.1, [IP_DESK, IP_CORR], "隙間なく並ぶ（幅 1.0 m＋0.05 m）"),
     "size": {"w": est(1.0, 0.2, [IP_DESK, IP_CORR], "慣用寸法（自販機 1.0 m）"), "d": est(0.8, 0.2, [IP_DESK, IP_CORR], "慣用寸法（0.8 m）"),
              "h": est(1.9, 0.2, [IP_DESK, IP_CORR], "慣用寸法（1.83〜1.9 m）。20261105_165424644 で横の扉（2.05 m）の約 0.9 倍で矛盾しない")},
     "material": "metal", "status": "estimated", "evidence": [IP_DESK, IP_CORR],
     "method": "20261105_165424644 で机の列の奥の壁に 3 台（黒・青・赤）、20261105_165344878 で同じ並び（黒・青、赤は端で切れる）を廊下の右に。"
               "依頼文の 2 台は写真 2 で赤が切れているためで、3 台とした。部屋の中の位置は写真どうしのつながりが決まらず ±5 m（interior.requests）"},
    {"type": "box", "name": "展示ケース（ガラス、2 台）", "at": ia(A_S - 0.4, 8.0), "rot_deg": YAW_A + 90, "count": 2,
     "pitch": est(6.0, 2.0, [IP_CORR], "20261105_165344878 で手前のケースと奥のケースの間に角柱 1 本（柱の行の間隔の約半分）"),
     "size": {"w": est(1.2, 0.3, [IP_CORR], "手前のケースの幅は横の扉（2.05 m の慣用）の幅の約 1.3 倍"),
              "d": est(0.5, 0.15, [IP_CORR], "側面の見えの幅から（幅の約 0.4 倍）"),
              "h": est(1.8, 0.3, [IP_CORR], "隣の扉の高さ（2.05 m の慣用）の約 0.85 倍")},
     "material": "white", "status": "estimated", "evidence": [IP_CORR],
     "method": "廊下（エスカレーターの囲いの脇）に並ぶ。部屋の中の位置は写っている範囲の南の端に沿わせた目安で ±5 m"},
]
CEIL_HOW = ("扉の高さ 2.0〜2.1 m（慣用、2.05 m）を物差しに、同じ壁の天井との取り合いまでを比で：20261105_165235743 の左の扉 562 px・壁 1140 px → 4.16 m、"
            "右の扉 445 px・壁 910 px → 4.19 m、20261105_165424644 の扉 385 px・壁 830 px → 4.42 m。平均 4.3 m。システム天井の格子は形式不明なので使わない。"
            "入口の床〜基壇の上の帯 6.26 m（sokuryo_W）から床と天井裏 2.0 m を引いても 4.3 m で矛盾しない")
b["interior"] = {
    "rooms": [{
        "id": "hall_1f", "name": "学生ホール（入口のホール・机の列）", "floor": 1,
        "polygon": {"points": [ia(A_N, 0.0), ia(A_S, 0.0), ia(A_S, D_E), ia(A_N, D_E)], "status": "estimated", "evidence": EV_IN + EV_ENT_OUT,
                    "method": f"写真 6 枚の範囲、端は未確認。西の辺は西北西面 W（入口のガラスの壁）。北の辺 a={A_N} m はカウンターの壁（PXL_20261105_075532769）で、"
                              f"入口の扉がこの壁のすぐ南に写る（PXL_20261105_075519850 の奥）ことと、外から見た入口が北端から 4.2〜10.6 m（070327678 の正面化）から置いた。"
                              f"奥 d={D_E} m は {P5_HOW}た距離（入口の壁まで約 23 m）＋カメラの後ろ 1.5 m。南の端 a={A_S} m は 075519850 の左のガラスの間仕切り（柱の列の外）の目安。"
                              "南と奥の端がどこで壁になるかは撮っていない（interior.requests）",
                    "note": FACE_NOTE},
        "floor_z": est(2.25, 0.3, [IP_ENT] + EV_ENT_OUT, "levels.entrance（大階段の上＝西の入口の床）と同じ。20261105_165632499 で外の床と中の床が扉の敷居で揃う"),
        "ceiling_h": est(4.3, 0.3, [IP_HALL, IP_DESK], CEIL_HOW),
        "columns": {"grid": {
            "origin": est([round(x, 2) for x in ia(9.0, 2.0)], 2.0, [IP_P5, IP_HALL],
                          f"{P5_HOW}。入口側の行の柱は入口の壁から約 2 m、北の壁から約 5 m。20261105_165632499 で扉の右（南）の白い角柱と合う。外観の柱割り（3.44 m）とは合わない（下の dx・dy）"),
            "axis_face": "W",
            "dx": est(4.7, 1.2, [IP_P5], f"{P5_HOW}：同じ奥行き（11.1 m）に並ぶ 2 本の横の間隔。外観の柱間隔 3.44 m の 1.37 倍で整数倍にならないので外観の格子は採らない"),
            "dy": est(10.7, 2.5, [IP_P5], f"{P5_HOW}：同じ列の 2 本の奥行きの差（11.1 m と 21.8 m）。外観の柱間隔の約 3 倍（10.3 m）"),
            "nx": est(2, None, [IP_P5, IP_HALL], "写っている範囲（a 4〜18 m）に入る列の数", unit="本"),
            "ny": est(2, None, [IP_P5], "写っている範囲（d 0〜25 m）に入る行の数", unit="本"),
            "size": est(1.1, 0.3, [IP_P5, IP_DESK], f"{P5_HOW}：柱の見付け 206 px（距離 11 m）→ 1.2 m、166 px（13.6 m）→ 1.2 m。打ち放しの角柱。面取りぶん少し小さく")},
            "omit": []},
        "glass_walls": [{"face": "W", "from": est(4.2, 1.5, [IP_ENT] + EV_ENT_OUT,
                                                  "070327678 の正面化（57.88 px/m）で入口のガラスの左端 1458 px − 壁の北端 1215 px。入口は面より手前の高さにあるので ±1.5 m"),
                         "to": est(10.6, 1.5, [IP_ENT] + EV_ENT_OUT, "同じ写真で入口のガラスの右端（エレベーターの箱の左）1826 px"),
                         "mullion_pitch": est(1.6, 0.3, [IP_ENT], "20261105_165632499 で扉（2 枚引き、高さ 2.05 m の慣用で幅 2.0 m）の左右の縦枠の間隔が扉の幅の約 0.8 倍")}],
        "doors": [{"face": "W", "at": est(6.8, 1.0, [IP_ENT, IP_P5] + EV_ENT_OUT,
                                          "外から：070327678 で入口の中央 1635 px → 北端から 7.3 m。中から：PXL_20261105_075519850 で扉は北の壁から約 2.2 m（a≈6.2）。平均"),
                   "width": est(2.0, 0.3, [IP_ENT], "20261105_165632499 で扉 340 px・高さ 350 px（2.05 m の慣用）→ 2.0 m（2 枚引き）"),
                   "height": est(2.05, 0.1, [IP_ENT], "自動扉の慣用寸法 2.0〜2.1 m"), "kind": "auto_sliding"}],
        "parts": iparts,
        "materials": {"floor": "wood_plank", "ceiling": "ceiling_tile", "wall": "plaster"},
        "lights": {"grid": True},
    }],
    "requests": [
        {"face": "W", "what": "入口の内側から、自動扉・北の壁（カウンターの壁）・入口側の柱が 1 枚に入る写真",
         "where": "ホールの中、入口から 8〜10 m 入った所から入口を向いて", "why": "扉の位置が外から 7.3 m・中から 6.2 m と食い違う（±1 m で置いた）。北の壁が外壁か中の壁かも未確認"},
        {"face": "*", "what": "ホールの南の端（a 18 m より南）と奥（入口から 25 m より奥）がどこで壁になるか",
         "where": "ホールの南の端と奥の端から、壁に沿って", "why": "輪郭の南と奥の辺は写真の範囲で切った（壁の位置ではない）"},
        {"face": "*", "what": "天井の広角（柱の頭と天井の取り合い、照明の列が入る）", "where": "ホールの真ん中で上を向いて",
         "why": "天井高は扉の比だけで 4.16〜4.42 m とばらつく。システム天井の格子の寸法が分かれば検算できる"},
        {"face": "*", "what": "柱の列に沿った奥行きの写真（柱が端から端まで並ぶように）", "where": "柱の列の延長上、壁際から",
         "why": "柱の間隔 4.7 m・10.7 m は 1 枚の写真の推定。外観の柱間隔 3.44 m の倍数にならない理由を確かめる"},
        {"face": "*", "what": "エスカレーターと廊下（自販機・展示ケース）がホールのどこにあるか分かる写真",
         "where": "入口の扉の内側から右・正面・左の 3 方向", "why": "20261105_165632499（外から扉の正面にエスカレーター）と PXL_20261105_075519850（同じ辺りに机の列）が食い違い、エスカレーターは置かなかった。自販機・展示ケースは ±5 m"},
    ],
    "notes": FACE_NOTE,
}
b["requests"].append({"face": "*", "kind": "check", "what": "学生ホールの階の番号：索引（職員の振り分け）は 1 階、館内の階の案内板（20261105_165424644）は「2 学生ホール」",
                      "where": "interior.rooms[hall_1f].floor", "why": "棟の階の数え方（沈んだ層を 1 階と数えるか）。床の高さは levels.entrance（2.25 m）で置いたので模型は変わらない。平面図の名前（plan_1f）だけに関わる",
                      "status": "open"})
b["history"].append({"at": "2026-11-05T22:30", "by": "設計係（Claude, claude-opus-5-5）",
                     "what": "内装（D-0002・D-0003）：学生ホール 1 室を interior.rooms に追加。入口は 20261105_165632499 のガラスブロックの帯・青い鉄骨・ステンレスの軒裏が "
                             "070204974/070327678 の大階段の上の入口と同じなので西北西面 W（GPS なしのため目視）。床 2.25 m は levels.entrance。天井高 4.3 m は扉 2.05 m の比 3 回の平均。"
                             "柱は PXL_20261105_075519850 の 3 本から 4.7 m × 10.7 m（外観の 3.44 m の格子は合わないので採らない）。机 15 台・椅子 60 脚・カウンター・自販機 3 台・展示ケース 2 台。"
                             "エスカレーターは写真どうしで位置が食い違うので置かず interior.requests へ。外観の設計は変えていない"})

# --- 2026-11-06 検収係：模型の穴 5 件（目印係 3 本と窓検出の係が独立に指した物）。out/tower75/kenshu_*.json・soten.json ----------
b["requests"] += [
    {"face": "f9", "what": "南南西の凹部の吹き抜け（ガラスの上端より上）を斜め 45° から、奥の壁と頂部の格子枠が入るように",
     "where": "南の広場の東寄りと西寄りから 1 枚ずつ", "why": "ガラスの上端は 2 枚で z 40／46 と食い違う（凹部の深さ 8 m が仮のため）。吹き抜けの奥行きが unknown で、模型はガラスの上を壁のまま残している", "status": "open"},
    {"face": "f0", "what": "北西の角の低い塊を北西から 45° で、足元から上端まで（木と鉄骨の棚に隠れない位置）",
     "where": "北の沈んだ広場の西の端", "why": "上端 23.8 m は 2 枚の視線で 21.9／25.6 m と 3.7 m 食い違う。北への出（脇の壁の面にそろうと置いた 3.4 m）と東の端は 060927494 の見え方からの推定", "status": "open"},
    {"face": "E", "what": "東南東面を正面から 1 枚（南端 2 列の段の笠木と主塔の頂部が同じ写真に入るように）と、南東の角を 45° から",
     "where": "東側の道路の向かいの歩道", "why": "南端 2 列の段の高さ（西と同じ 31.95 m と置いた）と柱間隔（OSM 28.33 m ÷ 9.49 周期＝2.99 m で西より 13% 狭い）は東の写真が未登録で測っていない", "status": "open"},
]
b["history"].append({"at": "2026-11-06T18:30", "by": "検収係（Claude, claude-opus-5-5）",
                     "what": "模型の穴 5 件（目印係・窓検出の係の指摘）を登録済みの写真で確かめて直した：(1) 北西の角の低い塊 NWb（上端 23.8 m、061455930・060842230 の視線）を追加、"
                             "(2) f10 の小窓を f11 の角から 1.0 m の所へ寄せ、3〜16 階の 14 段に（061649102）、(3) f9 のガラスとルーバーを 11 階の床の帯 z 44.4 までに（上は吹き抜け、奥行き unknown）、"
                             "(4) f7・f11 に各階の白い帯、(5) 東南東面を 9 列（南端 2 列＋主塔 7 列、060310392）にし、南端 2 列は段 31.95 m より上を 5.4 m 引っ込める（塊 Ess。航空写真の南東の穴と合う）。"
                             "あわせて設計スクリプトが索引（仕分け係の持ち物）を書き戻していたのを止めた"})

errs = validate_building(b)
print("validate:", errs or "OK")
save_building(b, Path("data/buildings/tower75.json"))
print("saved: faces", len(faces), "parts", len(parts), "blocks", len(b["blocks"]))
