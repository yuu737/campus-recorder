"""タワー75（tower75）の設計：観察・数え・測量の報告（data/reports/tower75/）を棟 JSON（data/buildings/tower75.json）に落とす。

    .venv/Scripts/python -I -X utf8 scripts/design_tower75.py

土台は recorder.skeleton.from_site_cli("tower75")（＝ recorder new tower75 --from-site。PLATEAU に無い棟なので OSM の輪郭）。
JSON は手で直さず、ここを直して出し直す。

製図板（px → m）
  西 W（f1・f2・f3 の 1 直線）  coverage_f3_photo.png（40 px/m、左端＝f3 の from、z＝71.6−py/40）。f1・f2 も同じ登録（f3 の原点から
        f2 −6.04 m・f1 −11.47 m ずらした。data/reports/tower75/sokuryo_f3.json#registration_method）。
        デッキの階は正面化 out/rectified/tower75_f3_061442402（94.783 px/m、x_origin 1641.8 px＝f3 の s 0、パラペット上端 623.5 px＝71.6 m）、
        2F と 1F の上の帯は正面化 out/rectified/tower75_f3_061341818（82.265 px/m、x_origin 1195.0 px、パラペット 595.5 px）
  東 E（f14・f15）  coverage_f15_photo.png（上の段、遠景 #37 が貼られた所）と、低層は正面化 out/rectified/tower75_f15_060328137
        （82.956 px/m、x_origin 1081.2 px＝f15 の s 0、パラペット 621.5 px）。f14 は f15 の原点から −3.41 m
  他の面は測量していない（sokuryo.md「測っていない面」）。面の写真は登録の視点から貼っただけなので製図板にしない。
        寸法は (b) 比率（1 段＝その面に近い測量の面の階高、柱の列の幅）と写真 1 枚から、± は値の 20〜30%

寸法の取り方の記号（係の定義 sekkei.md）
  (a) 面の写真・正面化の px を数えて m → estimated
  (b) 比率と写真 1 枚 → estimated、tol は値の 20〜30%
  (c) 2 枚以上で検算 → measured（タワー75 はメジャーの写真が無いので measured は数（列・段）だけ）
高さの原点：パラペット上端 71.6 m（棟の z=0＝ground_z 42.9 m から。PLATEAU の塔の部品の中ほど 114.5 m − 42.9。測量係の仮定）。
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from recorder import schema  # noqa: E402
from recorder.skeleton import from_site_cli  # noqa: E402

BID = "tower75"
OUT = ROOT / "data" / "buildings" / f"{BID}.json"
REP = "data/reports/tower75"
BY = "設計係（sekkei・claude-opus-5-5）"
KBY = "検収係（kenshu・claude-opus-5-5）"
NOW = datetime.now().astimezone().isoformat(timespec="seconds")

# 仮説を外した記録（recorder hypotheses --drop が 1 行ずつ足す）。再生成しても外れたまま
HYPOTHESES_OFF: dict = {}

# --- 写真 id（#n は仕分け係の一覧番号） -----------------------------------------------------
N1 = "PXL_20261103_065946407"       # #1 北の広場から北面
N3 = "PXL_20261103_070044607"       # #3 北西から
W10 = "PXL_20261103_070222827"      # #10 西面 北寄り（正面化）
W12 = "PXL_20261103_070327678"      # #12 西面 正面寄りの遠景（正面化・面の写真の上の段）
W13 = "PXL_20261103_070334137"      # #13 同
W14 = "PXL_20261103_070409570"      # #14 南西から（正面化）
W9 = "PXL_20261103_070204974"       # #9 大階段とエレベーター箱
W16 = "PXL_20261103_070606125"      # #16 デッキの上から入口
W17 = "PXL_20261103_070619739"
E24 = "PXL_20261103_074606922"      # #24 東の低層（シャッター・扉・ガラリ）
E25 = "PXL_20261103_074621752"      # #25 東面（正面化）
E37 = "PXL_20261105_055234749"      # #37 東の遠景（正面化・塔屋）
E41 = "PXL_20261105_060316957"      # #41 東面（正面化）
E42 = "PXL_20261105_060328137"      # #42 東の低層（正面化）
E44 = "PXL_20261105_060528804"
E47 = "PXL_20261105_060548823"
E49 = "PXL_20261105_060601578"
N52 = "PXL_20261105_060725949.MP"   # #52 北東の見上げ
N53 = "PXL_20261105_060730259"
N55 = "PXL_20261105_060756828"      # #55 f19 の足元から
N58 = "PXL_20261105_060804738"
N59 = "PXL_20261105_060807986"
N61 = "PXL_20261105_060842230"
N62 = "PXL_20261105_060854125"      # #62 北から
N64 = "PXL_20261105_060915328"
N65 = "PXL_20261105_060927494"
W76 = "PXL_20261105_061341818"      # #76 西面 道路から見上げ（正面化）
W80 = "PXL_20261105_061418906"
W81 = "PXL_20261105_061423669"
W82 = "PXL_20261105_061434917"      # #82 デッキの上から
W83 = "PXL_20261105_061442402"      # #83 デッキの上から（正面化）
S86 = "PXL_20261105_061511227"
S90 = "PXL_20261105_061647959"      # #90 南西から南面
S93 = "PXL_20261105_061708177"      # #93 橋の上から西の袖と南西の低い塊
W100 = "PXL_20261105_061834617"
W101 = "PXL_20261105_061836019"     # #101 北西の入口・銀色の箱
W104 = "PXL_20261105_061905208.MP"
W107 = "PXL_20261105_061910368"     # #107 エレベーター箱
S113 = "PXL_20261105_062046145"
S118 = "PXL_20261105_062246708"
S121 = "PXL_20261105_062307232"     # #121 吹き抜けの見上げ
S20 = "PXL_20261103_074413490"      # #20 吹き抜けの足元
S123 = "PXL_20261105_062317561"
S124 = "PXL_20261105_062323658"     # #124 東の袖と南東の低い塊
S126 = "PXL_20261105_062527087"
S127 = "PXL_20261105_062537872"
S119 = "PXL_20261105_062254010"
S120 = "PXL_20261105_062300065"
S122 = "PXL_20261105_062311895"
S125 = "PXL_20261105_062330274"
# 検収係（kenshu）が直した値の根拠：目印係の登録（data/reports/tower75/mejirushi/）8 枚の視点から面の上の縁を読んだ
KENSHU = "data/reports/tower75/kenshu_summary.json"
RECHECK1 = "out/tower75/recheck/20261107_153223"
S_REG = [S113, S118, S119, S120, S121, S122, S124, S125]

AERIAL = "gsi:aerial_z18"
OSM = "osm:relation/17880184"
PLATEAU_IDS = ["plateau:bldg_f693ff16-7d7b-4e35-b1c2-cff40827fcc1", "plateau:bldg_f2cc5f1b-1403-4764-8a92-d081923b0f6a"]

# --- 1 つの値を配る元（ref で指す） --------------------------------------------------------
PARAPET = 71.6          # data/reports/tower75/sokuryo_f15.json#plateau_top（114.5 − ground_z 42.9）
PW, PW_TOL = 4.37, 0.18  # 西の階高 data/reports/tower75/sokuryo_f3.json#floor_pitch
PE, PE_TOL = 4.33, 0.16  # 東の階高 data/reports/tower75/sokuryo_f15.json#floor_pitch
BAY_W, BAY_E = 3.40, 3.34
R_W, R_E = PW_TOL / PW, PE_TOL / PE      # 縮尺の割合（4.1%・3.7%）
REF_PW = "data/reports/tower75/sokuryo_f3.json#floor_pitch"
REF_PE = "data/reports/tower75/sokuryo_f15.json#floor_pitch"
REF_SW = "data/reports/tower75/sokuryo_f3.json#stack"
REF_SE = "data/reports/tower75/sokuryo_f15.json#stack"
B12_W = PARAPET - 1.25 * PW          # 西：最上段と 2 段目の間の白帯 B1/2（66.14）
B12_E = PARAPET - 1.33 * PE          # 東：65.84（測量係の近くの 3 枚の読み 1.33 P）
# 床の白帯（階 → z）。窓の段 13（2F〜14F）＋デッキの階（1F）。階の呼び名は観察係の解釈（levels_note）
LVW = {f: round(B12_W - (14 - f) * PW, 2) for f in range(2, 15)}
LVW[1] = round(LVW[2] - 1.48 * PW, 2)          # デッキの床（13 段目の水切りから 1.48 P 下。sokuryo_f3.json#stack）
LVE = {f: round(B12_E - (14 - f) * PE, 2) for f in range(2, 15)}
DECK = LVW[1]                                  # 7.23
ROAD_W, ROAD_E = 2.1, -2.0                     # sokuryo_f3.json#positions_on_face.z・sokuryo_f15.json#positions_on_face.z
TILE = "#b4774f"
TILE_E = "#8d5e44"

# W の柱割り（f3 の座標。主部の中ほど s 1.20、柱間隔 3.40、7 列）。f2 は +6.04、f1 は +11.47
W_C = [round(1.2 + (k - 4) * BAY_W, 2) for k in range(1, 8)]    # -9.0 … 11.4
E_C = [round(19.22 - (7 - k) * BAY_E, 2) for k in range(1, 9)]  # f15 の s：-0.82 … 19.22、8 本目 22.56（北東の低い塊）
OFF_F2, OFF_F1, OFF_F14 = 6.04, 11.47, 3.41


# --- 値の書き方 ----------------------------------------------------------------------------
def est(v, tol, ev, method, unit="m", ref=None, note=""):
    d = schema.Measurement(v if not isinstance(v, float) else round(v, 3), unit, tol, "estimated", list(ev), method, note).to_json()
    if ref:
        d["ref"] = ref
    return d


def meas(v, tol, ev, method, unit="m", note=""):
    return schema.Measurement(v, unit, tol, "measured", list(ev), method, note).to_json()


def unk(unit="m", note=""):
    return schema.Measurement(None, unit, None, "unknown", [], "", note).to_json()


def hyp(v, rule, frm, note, ev=(), method="", unit="m", tol=None):
    return schema.hypothesis(round(v, 3) if isinstance(v, float) else v, rule, frm, note, ev, method, unit, tol)


def tb(v, k=0.25):
    """(b) 比率と写真 1 枚から置いた値の ±（値の 20〜30%。既定 25%）。"""
    return round(max(abs(v) * k, 0.05), 2)


def tzw(z):
    """西の製図板で読んだ高さの ±：2 つの大きい方＋読み 0.05 m。
    ① 高さの原点（パラペット 71.6）からの距離 × 縮尺の割合（階高 4.37 ±0.18 m の 4.1%）
    ② 値 × PLATEAU の 3%（縮尺の元が PLATEAU の高さなので、棟の z=0 から測った値の下限。validate の物差しの下限と同じ）"""
    return round(max(R_W * abs(PARAPET - z), 0.03 * abs(z)) + 0.05, 2)


def tze(z):
    return round(max(R_E * abs(PARAPET - z), 0.03 * abs(z)) + 0.05, 2)


def tsw(s):
    """西の製図板で読んだ面に沿う位置の ±：登録の原点（f3 の s 1.20）からの距離 × 4.1% と 値 × 3% の大きい方＋読み 0.1 m。"""
    return round(max(R_W * abs(s - 1.2), 0.03 * abs(s)) + 0.1, 2)


def tse(s):
    return round(max(R_E * abs(s - 21.81), 0.03 * abs(s)) + 0.1, 2)


M_CW = ("coverage_f3_photo.png（40 px/m、z＝71.6−py/40。登録の縮尺は階高 4.37 m＝PLATEAU の高さ÷床の帯の段数、"
        "data/reports/tower75/sokuryo_f3.json#scale）で px を数えて ÷40")
M_W83 = ("正面化 out/rectified/tower75_f3_061442402（デッキの上から。94.783 px/m、x_origin 1641.8 px＝f3 の s 0、"
         "パラペット上端 623.5 px＝71.6 m。data/reports/tower75/sokuryo_f3.json#scale）で px を読んだ")
M_W76 = ("正面化 out/rectified/tower75_f3_061341818（道路から。82.265 px/m、x_origin 1195.0 px、パラペット上端 595.5 px）で px を読んだ")
M_CE = ("coverage_f15_photo.png（40 px/m、z＝71.6−py/40。上の段は遠景 #37 が貼られた所。縮尺は階高 4.33 m、"
        "data/reports/tower75/sokuryo_f15.json#scale）で px を数えて ÷40")
M_E42 = ("正面化 out/rectified/tower75_f15_060328137（#42。82.956 px/m、x_origin 1081.2 px＝f15 の s 0、パラペット上端 621.5 px＝71.6 m。"
         "data/reports/tower75/sokuryo_f15.json#scale）で px を読んだ")


# --- 面の座標 -----------------------------------------------------------------------------
def frame(f):
    (x0, y0), (x1, y1) = f["from"], f["to"]
    L = math.hypot(x1 - x0, y1 - y0)
    ux, uy = (x1 - x0) / L, (y1 - y0) / L
    return (x0, y0), (ux, uy), (uy, -ux), L


def world(f, s, d):
    (x0, y0), (ux, uy), (nx, ny), _L = frame(f)
    return [round(x0 + ux * s + nx * d, 3), round(y0 + uy * s + ny * d, 3)]


# --- 部品の道具（棟ごとの語彙） -------------------------------------------------------------
def box(name, s, d, z, mat="tile", cut=False):
    p = {"shape": "box", "name": name, "s": [round(s[0], 3), round(s[1], 3)], "d": [round(d[0], 3), round(d[1], 3)],
         "z": [round(z[0], 3), round(z[1], 3)], "material": mat}
    if cut:
        p["cut"] = True
    return p


def wedge(name, s, d, z, rise, t=None, mat="concrete"):
    p = {"shape": "wedge", "name": name, "s": [round(s[0], 3), round(s[1], 3)], "d": [round(d[0], 3), round(d[1], 3)],
         "z": [round(z[0], 3), round(z[1], 3)], "rise": rise, "material": mat}
    if t is not None:
        p["t"] = t
    return p


def cyl(name, at, r, z, mat="metal"):
    return {"shape": "cylinder", "name": name, "at": [round(at[0], 3), round(at[1], 3)], "r": r, "z": [round(z[0], 3), round(z[1], 3)], "material": mat}


def prism_xy(name, pts, z, mat="tile"):
    return {"shape": "prism", "name": name, "polygon_xy": [[round(p[0], 3), round(p[1], 3)] for p in pts],
            "z": [round(z[0], 3), round(z[1], 3)], "material": mat}


def comp(name, face, prims, ev, method, tol, note=""):
    d = {"type": "composite", "name": name}
    if face:
        d["face"] = face
        d["along"] = 0.0
    d.update({"status": "estimated", "evidence": list(ev), "tol": tol, "method": method, "primitives": prims})
    if note:
        d["note"] = note
    return d


def patch(name, face, a0, a1, z0, z1, mat, ev, method, tol_s, tol_z):
    return {"type": "patch", "name": name, "face": face,
            "along0": est(a0, tol_s, ev, method), "along1": est(a1, tol_s, ev, method),
            "z0": est(z0, tol_z, ev, method), "z1": est(z1, tol_z, ev, method), "material": mat}


def glz(name, a0, a1, zb, zt, inset, depth):
    return {"name": name, "along0": a0, "along1": a1, "z_bottom": zb, "z_top": zt, "inset": inset, "depth": depth}


def antenna(name, x, y, z0, h, ev, method, tol):
    """携帯の基地局らしい柱のアンテナ（棟ごとの語彙。4 回出る）：柱 1 本＋板のアンテナ 3 枚。世界座標（x＝東、y＝北）。"""
    prims = [{"shape": "cylinder", "name": "mast", "at": [round(x, 3), round(y, 3)], "r": 0.12, "z": [round(z0, 3), round(z0 + h, 3)], "material": "metal"}]
    for k, (dx, dy) in enumerate(((0.35, 0.0), (-0.18, 0.3), (-0.18, -0.3))):
        prims.append({"shape": "box", "name": f"panel{k}", "x": [round(x + dx - 0.12, 3), round(x + dx + 0.12, 3)],
                      "y": [round(y + dy - 0.12, 3), round(y + dy + 0.12, 3)], "z": [round(z0 + h * 0.55, 3), round(z0 + h * 0.85, 3)],
                      "material": "white"})
    return comp(name, None, prims, ev, method, tol)


def tile_material(ev, color, note):
    m = ("柱型のタイルは 1 階高に 60 段（data/reports/tower75/sokuryo_f3.json の tile_reference：W #76 59.9・デッキ 061442402 60.0・"
         "E 060316957 60.4）、横の目地の周期は段の 2.9 倍。階高 4.37 m ÷ 60 ＝ 1 段 0.073 m、横 0.073 × 2.9 ＝ 0.21 m。"
         "タイルの種類は未確認（測量係）で、物差しには使っていない")
    return [{"name": "タイル（種類未確認）", "color": color,
             "unit_w": est(0.211, 0.02, ev, m, ref=REF_PW), "unit_h": est(0.073, 0.006, ev, m, ref=REF_PW),
             "note": f"色は写真の色の目安（光で変わる。測った値ではない）。目地の幅は見えていない（模型の既定）。{note}",
             "evidence_photos": list(ev)}]


# =========================================================================================
def build() -> dict:
    b = from_site_cli(BID)
    faces = {f["id"]: f for f in b["faces"]}
    for f in b["faces"]:
        L = f["length"]
        L["tol"] = round(0.05 * L["value"] + 0.01, 2)
        L["method"] = "OSM の足元の辺の長さ（± は OSM の 5%）。PLATEAU に塔の足元が無い"
    b["footprint"]["method"] += ("。設計係：写真の西の線（8.21 階高＝35.9 m）・東の線（8.15 階高＝35.3 m）は OSM（32.67・30.29 m）より長いが、"
                                 "縮尺（PLATEAU の高さ）が未解決なので足元は動かしていない（sokuryo_f3.json・sokuryo_f15.json の widths。requests の data）")

    # --- 棟の値 ----------------------------------------------------------------------------
    b["parapet_top"] = est(
        PARAPET, 2.7, PLATEAU_IDS + [W12, E37],
        "塔の上端の標高を PLATEAU の 2 つの部品（足元の外に付いた塔の値：69.5 m＋基盤 43.53＝113.0、74.2 m＋基盤 41.81＝116.0）の中ほど "
        "114.5 m（±1.5）と置き、土台の ground_z 42.9 m を引いた 71.6 m（114.5 − 42.9）。西面の手前のパラペット上端・東面のパラペット上端をこの高さと置いた"
        "（仮定）。± は PLATEAU ±3%（2.2 m）と 2 部品の差の半分（1.5 m）を積んだ 2.7 m。未解決：PLATEAU の部品がどの高さ（パラペット・塔屋・"
        "ガラスの冠）か写真で決まらない（requests の data）",
        ref="data/reports/tower75/sokuryo_f15.json#plateau_top",
        note="測量係が登録の高さの原点に置いた値。塊 T（主部）の上端。南西の角の塊 SW は低い（blocks）")
    b["floor_count"] = meas(
        14, None, [W76, W10],
        "デッキの床から屋上までに見えた段：窓の段 13＋デッキの階 1（data/reports/tower75/kazoe_f3.json の floors。#76・#10 の 2 枚で一致）。"
        "北面の B のスリットも 13＋テラスの高さ 1（kazoe_f20.json）", unit="階",
        note="デッキより下の層（西の道路の階・北の広場の B1・東の道路の B2）は数え係の解釈（interpreted、仮説）で、見えた段に混ぜない。levels の B1・B2")
    b["floor_pitch"] = est(
        PW, PW_TOL, [W12, W76, W83, W13] + PLATEAU_IDS + ["gsi:dem5a"],
        "西面 W の値（(114.5 − 45.0) ÷ 15.91 段 ＝ 4.37 m。data/reports/tower75/sokuryo_f3.json#floor_pitch）。東面は 4.33 m"
        "（sokuryo_f15.json#floor_pitch）。未解決：OSM の西の線との差 −8.9%、PLATEAU の部品の高さの対応",
        ref=REF_PW, note="進行役 K-006 の規則：面ごとの階高のまま揃えない。窓の高さは西の面は西の値、東の面は東の値で置いた")
    lv = []
    for fl in range(1, 15):
        z = LVW[fl]
        lv.append({"id": f"{fl}F", "name": f"{fl}F（西面の床の白帯の高さ。呼び名は観察係の解釈）" if fl > 1 else "1F（西のデッキの床）",
                   "z": est(z, tzw(z), [W12, W76, W83],
                            "西面の床の白帯の高さ（パラペット 71.6 から B1/2 まで 1.25 P、その下は 1 P ごと、デッキは 13 段目の水切りから 1.48 P 下。"
                            "data/reports/tower75/sokuryo_f3.json#stack）。床の面そのものとは決めていない", ref=REF_SW)})
    lv.append({"id": "B1", "name": "北の広場の階・東の B1（数え係の解釈）",
               "z": unk(note="data/reports/tower75/kazoe_f3.json・kazoe_f15.json の interpreted（デッキの下の層）。見えた段に混ぜない。"
                             "東の低層の窓の高さは faces.f15 の glazing に置いた")})
    lv.append({"id": "B2", "name": "東の道路の階（数え係の解釈）",
               "z": unk(note="東の道路 −2.0 m（sokuryo_f15.json#stack の ground）。床の高さは測っていない")})
    b["levels"] = lv

    # --- 塊（blocks） ----------------------------------------------------------------------
    f3 = faces["f3"]
    A = world(f3, 14.15, 0.0)                     # 主部の南の縁（西面の上の段で空との境、sokuryo_f3.json#positions_on_face）
    Bc = list(f3["to"])                           # 南西の角
    C = list(faces["f4"]["to"])
    D = list(faces["f5"]["to"])
    (fx, fy), (ux, uy), _n, _L = frame(f3)
    sD = (D[0] - fx) * ux + (D[1] - fy) * uy
    E = [round(D[0] + (14.15 - sD) * ux, 3), round(D[1] + (14.15 - sD) * uy, 3)]   # 西の袖 f6 の線の上で A と同じ s
    poly = [list(p) for p in b["footprint"]["polygon"]]
    if poly[0] == poly[-1]:
        poly = poly[:-1]          # OSM の輪の閉じ点（始点と同じ点）を外す。形は変わらない（立面図の道具が長さ 0 の辺で止まるため）
    b["footprint"]["polygon"] = [list(p) for p in poly]
    # 主部 T：足元の輪郭のうち、f3 の上（A）から D・E を回って南西の角の塊を除いた形
    iB = next(i for i, p in enumerate(poly) if abs(p[0] - Bc[0]) < 1e-6 and abs(p[1] - Bc[1]) < 1e-6)
    iD = next(i for i, p in enumerate(poly) if abs(p[0] - D[0]) < 1e-6 and abs(p[1] - D[1]) < 1e-6)
    t_poly = poly[:iB] + [A, E] + poly[iD:]
    sw_poly = [A, Bc, C, D, E]
    sw_top = 69.65
    b["blocks"] = [
        {"id": "T", "name": "主部（塔・西と東の袖・北の部分）", "polygon": t_poly,
         "top_z": est(PARAPET, 2.7, PLATEAU_IDS + [W12, E37], "parapet_top と同じ（測量係の高さの原点）", ref="parapet_top"),
         "note": "四隅の低い塊（北 4F・南 6F まで）とその上の引っ込んだ部分は、引っ込みの奥行きが測れないので塊に分けない（低い塊の上端は faces の ledges）。"
                 "南西の角だけ上端が低いのが西面の写真で読めるので塊 SW に分けた"},
        {"id": "SW", "name": "南西の角（低い塊 SWb とその上の引っ込んだ部分 SWu）", "polygon": sw_poly,
         "top_z": est(sw_top, 0.9, [W12, W13, W14],
                      "coverage_f3_photo.png で SWu の笠木 py 135 → 68.25 m（面の上に貼った読み、s 14.0〜19.8）。SWu は西面より奥にあり、同じ写真で "
                      "SWu の白帯が主部の同じ段の白帯より 1.4 m 低く写る（64.7 対 66.14）。白帯は同じ床と見て、笠木も同じだけ下がって写ったと読んで "
                      "68.25＋1.4＝69.65。± は帯のずれの読み（1.3〜1.7 m）と縮尺。数え係・観察係の『主部より 1 段より小さく低い』（kazoe_f3.json の disagreement）と合う",
                      ref="out/tower75/coverage_f3_photo.png"),
         "note": "輪郭は西面 f3 の s 14.15〜21.19（主部の南の縁から南西の角）と f4・f5（南面）、東は西の袖 f6 の線。SWu の引っ込み（西で 1.4 m ほど、南は不明）は"
                 "塊にしていない（寸法が決まらない。requests の photo）"},
    ]

    # --- 面の共通の作り（西・東の主部の縦の窓の列） ------------------------------------------------
    win_w_n = est(1.35, 0.1, [W12, W13], f"{M_CW}：普通の段の窓の列の幅（4.6 m の列で s 3.95〜5.30 の 54 px、8.0 m の列で 7.30〜8.70）",
                  ref="out/tower75/coverage_f3_photo.png")
    win_w_w = est(2.25, 0.12, [W12, W13], f"{M_CW}：上 2 段の太い窓の幅（4.6 m の列で s 3.45〜5.75、8.0 m の列で 6.95〜9.20 の 90 px）",
                  ref="out/tower75/coverage_f3_photo.png")
    win_w_ne = est(1.3, 0.1, [E37], f"{M_CE}：普通の段の窓の列の幅（1 本目 s −1.6〜−0.3 の 52 px）", ref="out/tower75/coverage_f15_photo.png")
    win_w_we = est(2.25, 0.12, [E37], f"{M_CE}：上 2 段の太い窓の幅（1 本目 s −1.96〜0.29 の 90 px）", ref="out/tower75/coverage_f15_photo.png")
    gb_h = 0.60

    def tower_windows(face_id, bay_idx, side, rows=range(2, 15), wide_rows=(13, 14), skip=None, ev=None, hyp_from=None, hyp_note=""):
        """縦の窓の列（各段：白帯の上にガラスブロック 0.6 m、その上に窓、上 2 段は太い窓）。side は "W" か "E"。
        hyp_from を渡すと値を仮説（same_block / symmetry）で写す。"""
        LV, P, tz_, ref = (LVW, PW, tzw, REF_SW) if side == "W" else (LVE, PE, tze, REF_SE)
        ev = ev or ([W12, W13, W76] if side == "W" else [E37, E41, E42])
        mth = (f"{M_CW if side == 'W' else M_CE}：普通の段は白帯の上 0.15〜0.75 m がガラスブロック、その上から次の白帯の 0.08 m 下までが窓。"
               f"上 2 段は白帯から 0.08 m 上〜笠木の 0.25 m 下まで太い窓（中にガラスブロック）。白帯の高さは床の帯の段数から（ref）")
        if side == "W":
            mth += ("。検収（" + KENSHU + "#W）：再検収 " + RECHECK1 + " の写真の縁の中央値で、普通の段の窓の上端を白帯の 0.02 m 上まで（+0.10。"
                    "2F〜4F の上端が 34〜37 枚で +0.17〜+0.21、窓の上の固定ガラスが白帯の下端まで続く：061341818 の拡大）、"
                    "13F の太い窓の上端と 14F の太い窓の下端を 0.17 m 下げた（17 枚 −0.20・10 枚 −0.17）")
        out = []
        for F in rows:
            if skip and F in skip:
                continue
            bays = [i for i in bay_idx if not (skip and (F, i) in skip)]
            if not bays:
                continue
            wide = F in wide_rows
            # 検収（KENSHU_W）：西は普通の段の窓の上端が写真で +0.10〜+0.21（2F〜9F、37〜20 枚の中央値）、
            # 13F の太い窓の上端 −0.20・14F の太い窓の下端 −0.17（B1/2 の白帯が 0.17 低い）。東は合っているので直さない
            top_n = 0.02 if side == "W" else -0.08
            b12_w = -0.17 if side == "W" else 0.0
            if wide:
                zb = LV[F] + 0.08 + (b12_w if F == 14 else 0.0)
                zt = (LV[F + 1] - 0.04 + (b12_w if F + 1 == 14 else 0.0)) if F < 14 else PARAPET - 0.25
            else:
                zb = LV[F] + 0.15 + gb_h + 0.02
                zt = LV[F + 1] + top_n if F < 14 else PARAPET - 0.25
            if hyp_from:
                zbv = hyp(zb, hyp_from[0], hyp_from[1], hyp_note, ev, f"仮説（{hyp_from[0]}）：{hyp_from[1]} の段の高さを写した。{mth}", tol=tz_(zb))
                ztv = hyp(zt, hyp_from[0], hyp_from[1], hyp_note, ev, f"仮説（{hyp_from[0]}）：{hyp_from[1]} の段の高さを写した。{mth}", tol=tz_(zt))
                wv = hyp((win_w_w if wide else win_w_n)["value"] if side == "W" else (win_w_we if wide else win_w_ne)["value"],
                         hyp_from[0], hyp_from[1], hyp_note, ev, f"仮説（{hyp_from[0]}）：{hyp_from[1]} の窓の幅を写した", tol=0.1)
            else:
                zbv = est(zb, tz_(zb), ev, mth, ref=ref)
                ztv = est(zt, tz_(zt), ev, mth, ref=ref)
                wv = (win_w_w if wide else win_w_n) if side == "W" else (win_w_we if wide else win_w_ne)
            w = {"name": f"{F}F" + ("（太い窓）" if wide else ""), "z_bottom": zbv, "z_top": ztv, "width": wv, "bays": bays}
            if not wide:
                if hyp_from:
                    sp_h = hyp(gb_h, hyp_from[0], hyp_from[1], hyp_note, ev, f"仮説（{hyp_from[0]}）：{hyp_from[1]} の窓の下のガラスブロックの升の高さを写した", tol=0.1)
                else:
                    sp_h = est(gb_h, 0.08, ev, f"{M_CW if side == 'W' else M_CE}：窓の下のガラスブロックの升（白帯の上 0.15〜0.75 m）。"
                                               "色だけ白（ガラスブロックの素材は語彙に無い）")
                w["spandrel"] = {"height": sp_h, "material": "white", "in_recess": True}
            out.append(w)
        return out

    def bays(count, pitch, margin_from, pier, ev, method, count_st="estimated", count_ev=None, count_method="", hyp_from=None, hyp_note=""):
        if hyp_from:
            return {"count": hyp(count, hyp_from[0], hyp_from[1], hyp_note, ev, count_method, unit="列"),
                    "pitch": hyp(pitch, hyp_from[0], hyp_from[1], hyp_note, ev, method, tol=tb(pitch, 0.25)),
                    "margin_from": hyp(margin_from, hyp_from[0], hyp_from[1], hyp_note, ev, method, tol=tb(max(abs(margin_from), 0.4), 0.3)),
                    "pier_width": hyp(pier, hyp_from[0], hyp_from[1], hyp_note, ev, method, tol=tb(pier, 0.25))}
        cnt = (meas(count, None, count_ev or ev, count_method, unit="列") if count_st == "measured"
               else est(count, None, count_ev or ev, count_method, unit="列"))
        return {"count": cnt, "pitch": pitch, "margin_from": margin_from, "pier_width": pier}

    pitch_w = est(BAY_W, 0.14, [W12, W13, W14], "西の柱間隔（#12 の正面化で柱間隔 225.0 px÷階高 289.5 px × 4.37 m。"
                  "data/reports/tower75/sokuryo_f3.json#bay_pitch）", ref="data/reports/tower75/sokuryo_f3.json#bay_pitch")
    pier_w = est(round(BAY_W - 1.35, 2), 0.15, [W12, W13], f"柱間隔 3.40 − 窓の列 1.35（{M_CW}）")
    pitch_e = est(BAY_E, 0.13, [E37, E25], "東の柱間隔（#37 の正面化で 95.3 px÷123.5 px × 4.33 m。data/reports/tower75/sokuryo_f15.json#bay_pitch）",
                  ref="data/reports/tower75/sokuryo_f15.json#bay_pitch")
    pier_e = est(round(BAY_E - 1.3, 2), 0.15, [E37], f"柱間隔 3.34 − 窓の列 1.30（{M_CE}）")
    M_WREG = ("W の 7 列を 1 本の格子にして辺へ配った：主部の中ほど f3 の s 1.20、柱間隔 3.40（sokuryo_f3.json#positions_on_face・bay_pitch）。"
              "列の中心は f3 の s −9.0・−5.6・−2.2・1.2・4.6・8.0・11.4（coverage_f3_photo.png で 1.2〜11.4 の 4 本、#76 の正面化で −2.2 を確かめた）")
    M_EREG = ("E の 7 列を 1 本の格子にして辺へ配った：7 列目の中心 f15 の s 19.22、柱間隔 3.34（sokuryo_f15.json#positions_on_face）。"
              "列の中心は f15 の s −0.82・2.52・5.86・9.20・12.54・15.88・19.22（coverage_f15_photo.png と #42 の正面化で 8 本目 22.5 を確かめた）")

    # ===== 西 W：f1・f2・f3 =====
    mat_w = tile_material([W12, W76, W83], TILE, "西面は西日の色")
    for fid in ("f1", "f2", "f3"):
        faces[fid]["materials"] = mat_w
        faces[fid]["ground_offset"] = est(ROAD_W, 1.0, ["gsi:dem5a", W76], "西の道路（5m 標高 45.0 m − ground_z 42.9。sokuryo_f3.json#stack.ground）",
                                          ref="data/reports/tower75/sokuryo_f3.json#stack")
    W_KAZOE = "data/reports/tower75/kazoe_f3.json の bays 7（measured、#12・#14）を辺へ配った。" + M_WREG
    # f3：列 4〜7（s 1.2・4.6・8.0・11.4）
    f3["name"] = "西北西面 f3（西面 W の南寄り。方位 298°、21.2 m。from＝北、W＝f1・f2・f3 の 1 直線）"
    f3["note"] = ("W（f1・f2・f3）の主部の 4〜7 列目と、南端の南西の角（SWb の 2 列・その上の SWu の 2 列）。f1・f2・f3 は 1 直線（法線差 3° 以内）だが、"
                  "登録（registry）が面ごとの座標なので 1 つの面にまとめず、W の 1 本の格子を各辺に配った（wall_group W）。"
                  "SWb・SWu の 2 列は主部の格子と間隔が違うので glazing（縦の帯）で置いた")
    f3["wall_group"] = {"id": "W", "faces": ["f1", "f2", "f3"], "primary": "f3"}
    f3["bays"] = bays(4, pitch_w, est(-0.5, tsw(-0.5), [W12], M_WREG + "：f3 の始点の手前 0.5 m が 3|4 列目の柱型の中心"), pier_w,
                      [W12, W13], M_WREG, "estimated", [W12, W14], W_KAZOE)
    f3["windows"] = tower_windows("f3", [1, 2, 3, 4], "W")
    # SWb（2F〜6F）・SWu（7F〜13F）の 2 列（主部の格子と別。縦の帯）
    sw_cols = [(14.10, 15.20), (17.10, 18.25)]
    g3 = []
    for k, (a0, a1) in enumerate(sw_cols):
        for seg, (zb, zt, ev, what) in {"SWb": (LVW[2] + 0.15, LVW[7] - 0.15, [W14, W76, W10], "SWb の 2F〜6F（窓の段 5、kazoe_f3.json の segments）"),
                                        "SWu": (LVW[7] + 0.15, LVW[14] - 0.08, [W12, W14], "SWu の 7F〜13F（窓の段 7、14F は窓なし。kazoe_f3.json の segments・exceptions）")}.items():
            mth = (f"{M_CW}：{what} の列 {k + 1} の左右の縁（SWu の列は奥に引っ込んで写るが、遠景 #12 の視点（面から 50 m）では横のずれは 0.03 m 以下）。"
                   "高さは西の床の白帯（ref）")
            g3.append(glz(f"{seg} の窓の列 {k + 1}", est(a0, tsw(a0), ev, mth), est(a1, tsw(a1), ev, mth),
                          est(zb, tzw(zb), ev, mth, ref=REF_SW), est(zt, tzw(zt), ev, mth, ref=REF_SW),
                          est(0.3, tb(0.3, 0.3), ev, "(b) 窓の奥行きは見えていない。主部の窓の見込み（#14 の斜めで列の幅の約 0.2）から"),
                          est(0.5, None, ev, "ガラスの奥の暗い室内の板の厚さ（見た目の道具の値。寸法ではない）")))
    # デッキの階の入口（ガラスの扉・店先）
    ent_ins = est(0.4, tb(0.4, 0.3), [W16, W82], "(b) 入口のガラスは庇の下で柱型より奥（#16・#82 の斜めで柱型の幅の約 0.2）")
    ent_dep = est(3.0, None, [W16], "ガラスの奥の暗い室内の箱の奥行き（見た目の道具の値。寸法ではない）")
    for nm, a0, a1 in (("デッキの階の入口（中）", 0.0, 6.3), ("デッキの階の入口（南、学生ホール）", 7.6, 12.8)):
        mth = f"{M_W83}：入口のガラスの左右の縁と、デッキの床（扉の下）〜欄間の下（庇の上）"
        g3.append(glz(nm, est(a0, tsw(a0), [W83, W16, W82], mth), est(a1, tsw(a1), [W83, W16, W82], mth),
                      est(DECK, tzw(DECK), [W83], mth, ref=REF_SW), est(10.8, tzw(10.8), [W83], mth), ent_ins, ent_dep))
    f3["glazing"] = g3
    f3["ledges"] = [{"name": "南西の低い塊 SWb の上端（6F の上の水切り）", "z": est(LVW[7], tzw(LVW[7]), [W14, W12],
                                                                       "#14・#12 で SWb の上端は 7F の白帯の高さの段（kazoe_f3.json の segments）。ref の白帯", ref=REF_SW),
                     "depth": est(0.3, tb(0.3, 0.3), [W14], "(b) #14 で水切りの出は窓の列の幅の約 0.2"),
                     "height": est(0.2, tb(0.2, 0.3), [W14], "(b) #14 で厚さは白帯の約 1.5 倍"),
                     "along0": est(14.15, tsw(14.15), [W12], "主部の南の縁（sokuryo_f3.json#positions_on_face）"),
                     "along1": est(21.19, 0.3, [W14], "南西の角（f3 の終点。#14 の compare で縁と 0.09 m）"), "material": "coping"}]
    # f2：列 2・3（f2 の s 0.44・3.84）
    f2 = faces["f2"]
    f2["name"] = "西北西面 f2（西面 W の中ほど、渡り廊下の取り付き。方位 298°、6.0 m）"
    f2["note"] = "W の 2・3 列目。窓の段・帯は f3 と同じ（同じ登録）。2F の段は 3 列目が無地（kazoe_f3.json の exceptions）、2 列目は渡り廊下で見えない（窓を置かない）"
    f2["wall_group"] = {"id": "W", "faces": ["f1", "f2", "f3"], "primary": "f3"}
    f2["bays"] = bays(2, pitch_w, est(-1.26, tsw(-7.3), [W12], M_WREG + "：f2 の始点の手前 1.26 m が 1|2 列目の柱型の中心"), pier_w,
                      [W12, W10], M_WREG, "estimated", [W12, W14], W_KAZOE)
    f2["windows"] = tower_windows("f2", [1, 2], "W", skip={2})
    f2_g = []
    mth = f"{M_W83}：入口のガラス（渡り廊下の下の暗い所。#16・#101 で店先が続く）"
    f2_g.append(glz("デッキの階の入口（北）", est(0.5, tsw(-5.5) + 0.3, [W83, W16, W101], mth), est(6.04, tsw(0), [W83], mth),
                    est(DECK, tzw(DECK), [W83], mth, ref=REF_SW), est(10.8, tzw(10.8), [W83], mth), ent_ins, ent_dep))
    f2["glazing"] = f2_g
    # f1：列 1（f1 の s 2.47）。北西の低い塊 NWb の西向きの 1 列は OSM の北西の角より北（足元の外）
    f1 = faces["f1"]
    f1["name"] = "西北西面 f1（西面 W の北端。方位 298°、5.4 m）"
    f1["note"] = ("W の 1 列目。北西の低い塊 NWb の西向きの 1 列（kazoe_f1.json の segments、measured）は、登録の縮尺で主部の北の縁が f1 の始点より "
                  "0.29 m 北、NWb がさらに 2.9 m 北になり、OSM の足元の外に出るので置いていない（足元の西の線の食い違い。requests の data）。"
                  "2F の段は渡り廊下と木で見えない（窓を置かない）")
    f1["wall_group"] = {"id": "W", "faces": ["f1", "f2", "f3"], "primary": "f3"}
    f1["bays"] = bays(1, pitch_w, est(0.77, tsw(-10.7), [W12], M_WREG + "：f1 の s 0.77 が 1 列目の北の柱型の中心"), pier_w,
                      [W12, W10], M_WREG, "estimated", [W12, W14], W_KAZOE)
    f1["windows"] = tower_windows("f1", [1], "W", skip={2})
    m_nwe = ("(b) 北西の入口：#100・#101 で扉は 1F の高さの約 1/2、欄間は約 1/3（観察票 kansatsu_f1.json の parts）。1F の高さ＝デッキの床 7.23〜2F の白帯 13.70。"
             "幅は f1 の柱型を除いた所（列 1 の窓の両脇まで）と見た")
    f1["glazing"] = [glz("北西の入口（ガラスの扉）", est(0.6, tb(0.6, 0.3) + 0.2, [W100, W101], m_nwe), est(4.6, tb(4.6, 0.25), [W100, W101], m_nwe),
                         est(DECK, tzw(DECK), [W101], m_nwe, ref=REF_SW), est(DECK + 3.2, tb(3.2, 0.25), [W101], m_nwe), ent_ins, ent_dep)]

    # ===== 東 E：f14・f15 =====
    mat_e = tile_material([E37, E41, E42], TILE_E, "東面は曇天の色")
    f15 = faces["f15"]
    f14 = faces["f14"]
    for f in (f14, f15):
        f["materials"] = mat_e
        f["ground_offset"] = est(ROAD_E, 0.3, ["gsi:dem5a", E42], "東の道路（5m 標高 40.9 m − ground_z 42.9。sokuryo_f15.json#stack.ground）",
                                 ref="data/reports/tower75/sokuryo_f15.json#stack")
        f["wall_group"] = {"id": "E", "faces": ["f14", "f15"], "primary": "f15"}
    E_KAZOE = "data/reports/tower75/kazoe_f15.json の bays 7（measured、#37・#42）を辺へ配った。" + M_EREG
    f15["name"] = "東南東面 f15（東面 E。方位 118°、24.9 m。from＝南、E＝f14・f15 の 1 直線）"
    f15["note"] = ("E の主部の 2〜7 列目と、8 本目（北東の低い塊 NEb の東向きの 1 列、2F〜4F だけ）。北端の上の段は窓の無い壁（NEu、奥に引っ込む）。"
                   "低層（B2・B1・1F）は正面化 #42 で読んだ。窓の高さは東の値（進行役 K-006：面ごとの階高）")
    f15["bays"] = bays(7, pitch_e, est(0.85, tse(0.85), [E37, E42], M_EREG + "：f15 の s 0.85 が 1|2 列目の柱型の中心"), pier_e,
                       [E37, E42], M_EREG, "estimated", [E37, E42], E_KAZOE + "。7 本目は NEb の 1 列（2F〜4F）")
    win15 = tower_windows("f15", [1, 2, 3, 4, 5, 6], "E")
    for w in win15:
        if w["name"] in ("2F", "3F", "4F"):
            w["bays"] = [1, 2, 3, 4, 5, 6, 7]
    f15["windows"] = win15
    f14["name"] = "東南東面 f14（東面 E の南寄り。方位 118°、3.4 m）"
    f14["note"] = ("E の 1 列目（f14 の s 2.59）。南東の低い塊 SEb・その上の SEu の東向きの 2 列は、登録の縮尺で f15 の s −10.3〜−3.8 になり、"
                   "OSM の足元（南東の角 s −5.37）の外か足元に無い短い辺（1.96 m、土台で面にしていない）に当たるので置いていない（requests の data）")
    f14["bays"] = bays(1, pitch_e, est(0.92, tse(-2.49), [E37, E42], M_EREG + "：f14 の s 0.92 が 1 列目の南の柱型の中心"), pier_e,
                       [E37, E42], M_EREG, "estimated", [E37, E42], E_KAZOE)
    f14["windows"] = tower_windows("f14", [1], "E")
    # 低層（B2・B1・1F）。#42 の正面化で読んだ（面の上の物だけ。張り出す物は composite）
    E_LOW = {  # 名前: (s0, s1, z0, z1)
        "1F 窓 1": (-1.0, 3.6, 8.3, 10.2), "1F 窓 2": (5.1, 10.2, 8.3, 10.2),
        "B1 窓 1": (-1.0, 4.4, 4.5, 6.9), "B1 窓 2": (5.0, 10.2, 4.5, 6.9),
    }
    g15, g14 = [], []
    for nm, (a0, a1, zb, zt) in E_LOW.items():
        mth = f"{M_E42}：{nm} の左右の縁と上下（面の上の窓。張り出す帯より上か脇の見える所）"
        ins = est(0.25, tb(0.25, 0.3), [E24, E42], "(b) 窓の見込み：#24 で窓の列の幅の約 0.1")
        dep = est(3.0, None, [E42], "ガラスの奥の暗い室内の箱の奥行き（見た目の道具の値。寸法ではない）")
        for f, off, gl in ((f15, 0.0, g15), (f14, OFF_F14, g14)):
            s0, s1 = max(a0 + off, 0.0), min(a1 + off, f["length"]["value"])
            if s1 - s0 < 0.3:
                continue
            gl.append(glz(nm, est(s0, tse(a0), [E42, E24], mth), est(s1, tse(a1), [E42, E24], mth),
                          est(zb, tze(zb), [E42, E24], mth, ref=REF_SE), est(zt, tze(zt), [E42, E24], mth, ref=REF_SE), ins, dep))
    f15["glazing"] = g15
    f14["glazing"] = g14

    # ===== 北 N：f20・f0（N-west）、f19、f18、f16、f17 =====
    f0, f20, f19, f18, f16, f17 = (faces[k] for k in ("f0", "f20", "f19", "f18", "f16", "f17"))
    for f in (f0, f20, f19, f18, f16):
        f["materials"] = tile_material([N1, N62], TILE, "北面は日陰（西面の色を写した）")
    HN_NW = ("北西の部分の段は B のスリットの段と並ぶ（data/reports/tower75/kazoe_f20.json：NWb 3＋NWu 10＝B のスリット 13）。同じ棟の床なので、"
             "西面の床の白帯の高さと同じ窓の段と置いた（西面 W の北の角の NWb の列も同じ段、kazoe_f1.json）")
    f0["name"] = "北北東面 f0（北面の西寄り N-west。方位 28°、6.4 m）"
    f0["note"] = ("北西の部分：低い塊 NWb（2 列、窓の段 3）とその上の NWu（2 列、窓の段 10、奥へ引っ込む）。列は kazoe_f20.json（measured 2）、"
                  "f0 と f20 の境は写真に無く、観察票の見立て（北西の 2 列は f0、B は f20）で置いた。列の位置は足元の辺に 2 列（仮説 footprint）")
    f0["wall_group"] = {"id": "N-west", "faces": ["f20", "f0"], "primary": "f20"}
    p0 = round(f0["length"]["value"] / 2, 3)
    f0["bays"] = {"count": est(2, None, [N1, N62], "data/reports/tower75/kazoe_f20.json の bays 2（measured、#1・#62）。f0 に置いたのは観察票の見立て", unit="列"),
                  "pitch": hyp(p0, "footprint", "f0", "f0 の辺 6.41 m に 2 列が等しく収まると置いた（列の位置を測った写真が無い）", [N1, N62],
                               "仮説（footprint）：辺の長さ ÷ 2", tol=tb(p0, 0.25)),
                  "margin_from": hyp(0.0, "footprint", "f0", "辺の両端を柱型の中心と置いた", [N1, N62], "仮説（footprint）", tol=0.6),
                  "pier_width": hyp(round(p0 - 1.35, 3), "same_block", "f3", "窓の列の幅は西面と同じ（観察票：西面と同じ縦長の列）", [W12],
                                    "仮説（same_block）：f3 の窓の列 1.35 m を写した", tol=0.3)}
    f0["windows"] = tower_windows("f0", [1, 2], "W", wide_rows=(), ev=[N1, N62], hyp_from=("same_block", "f3"), hyp_note=HN_NW)
    f0["ledges"] = [{"name": "北西の低い塊 NWb の上端（4F の上の水切り）",
                     "z": est(LVW[5], tzw(LVW[5]) + 0.3, [N1, N62, N3], "#1・#62 で NWb の上端の段は窓の段 3 の上（kazoe_f20.json の NWb 3 段）。西面の 5F の白帯の高さ（ref）と置いた",
                              ref=REF_SW),
                     "depth": est(0.3, tb(0.3, 0.3), [N1], "(b) #1 で笠木の出は窓の列の幅の約 0.2"),
                     "height": est(0.25, tb(0.25, 0.3), [N1], "(b) #1 で笠木の厚さは白帯の約 2 倍"), "material": "coping"}]
    f20["name"] = "北北東面 f20（北面 N-west の東寄り。方位 28°、8.1 m）"
    f20["note"] = ("B（スリット窓の壁柱、全高、各階にスリット 1）と、f19 との境の柱型・全高の縦のガラスのすき間。B の位置は辺の中ほど（仮説 footprint。"
                   "B と北西の部分の境が足元のどの辺に乗るかは写真で決まらない、観察票の nulls）")
    f20["wall_group"] = {"id": "N-west", "faces": ["f20", "f0"], "primary": "f20"}
    L20 = f20["length"]["value"]
    m_slit = ("(b) B のスリット：#1・#62 で幅は北西の窓の列の約 1/2（観察票 kansatsu_f20.json の window_shape）＝0.68 m、高さは段の約 0.45"
              "（#1 の拡大で 1 段の白帯の間の半分弱）＝1.95 m、段の上寄り（白帯の 1.6 m 上から）")
    f20["bays"] = {"count": est(1, None, [N1, N3], "B のスリットの縦の列 1（kazoe_f20.json の segments、measured）", unit="列"),
                   "pitch": hyp(round(L20 - 2.2, 2), "footprint", "f20", "B の柱を辺の中ほどに 1 列と置いた（東の 2.2 m は f19 との境の柱型とガラスのすき間）",
                                [N1], "仮説（footprint）", tol=1.5),
                   "margin_from": hyp(2.2, "footprint", "f20", "東の端（from）の柱型とすき間の分", [N1], "仮説（footprint）", tol=0.8),
                   "pier_width": hyp(round(L20 - 2.2 - 0.68, 2), "footprint", "f20", "スリットの両脇は壁", [N1], "仮説（footprint）", tol=1.5)}
    wins20 = []
    for F in range(2, 15):
        zb = LVW[F] + 1.6
        zt = zb + 1.95
        wins20.append({"name": f"{F}F の B のスリット",
                       "z_bottom": hyp(zb, "same_block", "f3", "B のスリット 13 段は西面の窓の段 13 と同じ床（kazoe_f20.json）", [N1, N62], m_slit, tol=tb(1.95, 0.3)),
                       "z_top": hyp(zt, "same_block", "f3", "同上", [N1, N62], m_slit, tol=tb(1.95, 0.3)),
                       "width": est(0.68, tb(0.68, 0.3), [N1, N62], m_slit), "bays": [1]})
    f20["windows"] = wins20
    f20["glazing"] = [glz("f19 との境の全高の縦のガラスのすき間",
                          est(0.2, tb(0.6, 0.3), [N3, N59, N61], "(b) #3・#59 で f19 の柱型の西の脇、幅は B のスリットの約 1/2（観察票 kansatsu_f20.json の parts）"),
                          est(0.55, tb(0.55, 0.3), [N3, N59, N61], "(b) 同上（幅 0.35 m）"),
                          est(LVW[2], tzw(LVW[2]) + 0.5, [N3, N61], "(b) 1F の上から（観察票：1F の上から屋上の下まで）", ref=REF_SW),
                          est(70.5, 1.0, [N3, N61], "(b) 屋上の下まで（パラペットの約 1 m 下）"),
                          est(0.6, tb(0.6, 0.3), [N59], "(b) 引っ込む（#59 で柱型の側面が見える。奥行きは柱型の幅の約 1/3）"),
                          est(0.5, None, [N59], "ガラスの奥の暗い室内の板の厚さ（見た目の道具の値）"))]
    # f19：横長の帯窓の柱間（引っ込む）と頂部のガラスの冠
    f19["name"] = "北北東面 f19（北面の中央。方位 28°、14.2 m。横長の帯窓の柱間）"
    f19["note"] = ("両脇の柱型（外へ出る、白帯）に挟まれた横長の帯窓の柱間（引っ込む）。土台の足元では f19 が外へ出ているが写真では引っ込む"
                   "（観察票 vs_provisional）。足元は動かしていない（引っ込みの奥行きが測れない。requests の photo）。帯窓は冠の下に 11 段"
                   "（#1、kazoe_f19.json の seen。数え係は unknown）、頂部 2 段は冠")
    m_rib = ("(b) 帯窓の高さは階高の約 1/4（#55 の見上げ、観察票 kansatsu_f19.json の pier_to_window）＝1.1 m、段の中ほどより上。"
             "幅は両脇の柱型（#1 で柱間の約 0.1 ずつ）を除いた柱間の全幅")
    f19["bays"] = {"count": meas(1, None, [N1, N62], "両脇の柱型に挟まれた横長窓の柱間 1（kazoe_f19.json の bays、#1・#62）", unit="列"),
                   "pitch": est(11.4, tb(11.4, 0.25), [N1, N62], m_rib),
                   "margin_from": est(1.4, tb(1.4, 0.3), [N1, N62], m_rib),
                   "pier_width": est(0.0, 0.3, [N1], "柱間 1 つの帯窓（中の柱型なし）")}
    wins19 = []
    for F in range(2, 13):
        zb = LVW[F] + 2.4
        zt = zb + 1.1
        wins19.append({"name": f"{F}F の帯窓",
                       "z_bottom": hyp(zb, "same_block", "f3", "帯窓は B のスリットと同じ階に並ぶ（観察票 kansatsu_f19.json の floors）。西面の床の白帯の高さを写した",
                                       [N1, N62], m_rib, tol=tb(1.1, 0.3)),
                       "z_top": hyp(zt, "same_block", "f3", "同上", [N1, N62], m_rib, tol=tb(1.1, 0.3)),
                       "width": est(11.4, tb(11.4, 0.2), [N1, N62], m_rib), "bays": [1]})
    f19["windows"] = wins19
    f19["bands"] = [{"name": "足元の粗いタイルの腰と笠木", "z_bottom": est(4.5, 1.5, [N55, N58], "(b) 足元（f19 の前の 5m 標高 5.05 m・砂利の側溝 #57・#58）から"),
                     "z_top": est(9.0, 1.5, [N55, N58], "(b) 腰の高さは 1 段分ほど（観察票 kansatsu_f19.json の parts、#55・#58）。足元から 4.4 m。"
                                                         "その上の帯窓の段は木で隠れて数えられない（2F より下の帯窓は置いていない）"),
                     "depth": est(0.15, tb(0.15, 0.3), [N55], "(b) 腰は壁より少し出る（笠木の影）"), "color": "#8c6a52"}]
    # f18：B'（スリット 1 列）
    f18["name"] = "北北東面 f18（北面の東寄り。方位 28°、8.9 m。スリット窓の壁柱 B'）"
    f18["note"] = "B'（B と同じ作り。各段にスリット 1）。段数は #52 の見上げで 13 に見えるが ±1（kazoe_f18.json は unknown）。段は B（f20）から仮説 same_block"
    L18 = f18["length"]["value"]
    f18["bays"] = {"count": meas(1, None, [N1, N52], "スリットの縦の列 1（kazoe_f18.json、#1・#52）", unit="列"),
                   "pitch": hyp(round(L18 - 2.0, 2), "footprint", "f18", "スリットの列を辺の中ほどに 1 列と置いた（位置を測った写真が無い）", [N52], "仮説（footprint）", tol=2.0),
                   "margin_from": hyp(1.0, "footprint", "f18", "辺の両端 1 m を柱型と置いた", [N52], "仮説（footprint）", tol=1.0),
                   "pier_width": hyp(round(L18 - 2.0 - 0.68, 2), "footprint", "f18", "スリットの両脇は壁", [N52], "仮説（footprint）", tol=2.0)}
    f18["windows"] = [{"name": w["name"].replace("B の", "B' の"), "z_bottom": hyp(w["z_bottom"]["value"], "same_block", "f20",
                                                                                 "B' は B と同じ作り（観察票 kansatsu_f18.json）。B のスリットの段を写した",
                                                                                 [N52, N1], m_slit, tol=w["z_bottom"]["tol"]),
                       "z_top": hyp(w["z_top"]["value"], "same_block", "f20", "同上", [N52, N1], m_slit, tol=w["z_top"]["tol"]),
                       "width": est(0.68, tb(0.68, 0.3), [N52], m_slit), "bays": [1]} for w in wins20]
    # f16：北東の部分（NEb 2 列・3 段、NEu 2 列）
    f16["name"] = "北北東面 f16（北東の部分の北向き。方位 28°、5.2 m）"
    f16["note"] = ("北東の低い塊 NEb（2 列、窓の段 3）とその上の NEu（2 列、引っ込む、段は見上げで数えられない）。NEu の段は北西の部分（f0）と"
                   "対称と置いた（仮説 symmetry）。土台では f16 が f18 より南へ引っ込むが、#52 では低い段で同じ面に見える（足元は動かしていない）")
    p16 = round(f16["length"]["value"] / 2, 3)
    f16["bays"] = {"count": meas(2, None, [N52, N53], "NEb の窓の縦の列 2（kazoe_f16.json、#52・#53）", unit="列"),
                   "pitch": hyp(p16, "footprint", "f16", "f16 の辺 5.19 m に 2 列が等しく収まると置いた", [N52], "仮説（footprint）", tol=tb(p16, 0.25)),
                   "margin_from": hyp(0.0, "footprint", "f16", "辺の両端を柱型の中心と置いた", [N52], "仮説（footprint）", tol=0.6),
                   "pier_width": hyp(round(p16 - 1.3, 3), "same_block", "f15", "窓の列の幅は東面と同じ（観察票：西面と同じ縦長の列）", [E37],
                                     "仮説（same_block）：f15 の窓の列 1.30 m を写した", tol=0.3)}
    w16 = tower_windows("f16", [1, 2], "E", rows=range(2, 5), wide_rows=(), ev=[N52, N53], hyp_from=("same_block", "f15"),
                        hyp_note="NEb の窓の段 3（kazoe_f16.json）は東面の 2F〜4F の段（東面の 8 本目の列、#42 で 2F〜4F）と同じ床")
    w16 += tower_windows("f16", [1, 2], "E", rows=range(5, 15), wide_rows=(), ev=[N52, N1], hyp_from=("symmetry", "f0"),
                         hyp_note="NEu の段は北西の部分 NWu（f0、5F〜14F の 10 段）と対称と置いた（北東・北西の低い塊がどちらも 4F まで、観察票 blocks）")
    f16["windows"] = w16
    m_16g = "(b) #52：NEb の 1F は上半分がガラスブロックの欄間（parts）、下半分が 4 枚の窓（観察票 kansatsu_f16.json の parts）。1F は東面の 2F の白帯の 6.5 m 下から"
    f16["glazing"] = [glz("NEb の 1F の 4 枚の窓", est(0.4, tb(0.4, 0.3) + 0.2, [N52], m_16g), est(4.8, tb(4.8, 0.2), [N52], m_16g),
                          est(LVE[2] - 6.0, 1.0, [N52], m_16g, ref=REF_SE), est(LVE[2] - 3.4, 1.0, [N52], m_16g, ref=REF_SE),
                          est(0.25, tb(0.25, 0.3), [N52], "(b) 窓の見込みは窓の列の幅の約 0.2"), est(2.0, None, [N52], "ガラスの奥の暗い室内の箱の奥行き（見た目の道具の値）"))]
    m_0g = ("(b) NWb の 1F の窓：#1・#62 でガラスブロックの帯の下に窓（観察票 kansatsu_f20.json の glazing）。高さは北西の入口（f1）と同じ比、"
            "幅は 2 列の窓の幅")
    f0["glazing"] = [glz("NWb の 1F の窓（テラスの高さ）", est(0.6, tb(0.6, 0.3) + 0.2, [N1, N62], m_0g), est(5.8, tb(5.8, 0.2), [N1, N62], m_0g),
                         est(DECK + 0.2, tzw(DECK) + 0.3, [N1, N62], m_0g, ref=REF_SW), est(DECK + 3.2, tzw(DECK + 3.2) + 0.3, [N1, N62], m_0g),
                         est(0.3, tb(0.3, 0.3), [N1], "(b) 窓の見込みは窓の列の幅の約 0.2"), est(2.0, None, [N1], "ガラスの奥の暗い室内の箱の奥行き（見た目の道具の値）"))]
    f17["name"] = "東南東面 f17（土台の北東の切り欠きの東向きの段。方位 117°、2.4 m）"
    f17["note"] = "どの写真でも東向きの細い面として見えない（観察票・数え係 unknown）。灰色のまま。北東の角の撮影依頼（requests）"

    # ===== 南 S：f4・f5（SWb）、f6・f7・f8（西の袖 WW）、f9（吹き抜け）、f10・f11・f12（東の袖 WE）、f13（SEb） =====
    for k in ("f4", "f5", "f6", "f7", "f8", "f10", "f11", "f12", "f13", "f9"):
        faces[k]["materials"] = tile_material([S90, S93, S121, S124], TILE, "南面は曇天の色（西面の色を写した）")
    f4, f5, f6, f7, f8, f9, f10, f11, f12, f13 = (faces[k] for k in ("f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12", "f13"))
    HN_SW = "南西の低い塊 SWb の南向きの段は、同じ塊の西向き（f3 の南端の 2 列、窓の段 5＝2F〜6F、kazoe_f3.json の segments）と同じ床"
    f4["name"] = "南南西面 f4（南西の低い塊 SWb の南向き。方位 208°、6.0 m）"
    f4["note"] = ("SWb の 2 列（kazoe_f4.json：2 列・窓の段 5＋デッキの階）と 1F のガラスブロックの欄間・4 枚の窓。その上の SWu の南向きは引っ込み、"
                  "列は 1 か 2（unknown）で置いていない。f4・f5 は 1 直線（S-SW）。列の位置は観察票の比（端の壁：列：間の壁：列：端の壁 ≈ 1.5：1：1.5：1：1.5、"
                  "#93）を f4＋f5 から入隅のすき間を除いた 6.79 m に当てた")
    f4["wall_group"] = {"id": "S-SW", "faces": ["f4", "f5"], "primary": "f4"}
    m_f4 = ("(b) 観察票 kansatsu_f4.json の pier_to_window（#93 の正面、端の壁：列：間の壁：列：端の壁 ≈ 1.5：1：1.5：1：1.5）を、f4＋f5 の 8.14 m から"
            "東端の入隅のすき間（窓の列 1 本分 1.35 m）を除いた 6.79 m に当てた：1 単位 1.045 m")
    f4["bays"] = {"count": est(2, None, [S90, S93], "kazoe_f4.json の bays 2（#90・#93。写真の面が f9・f7 に仕分けられているので estimated）", unit="列"),
                  "pitch": est(2.61, tb(2.61, 0.25), [S93], m_f4), "margin_from": est(0.78, tb(0.78, 0.3), [S93], m_f4),
                  "pier_width": est(1.57, tb(1.57, 0.25), [S93], m_f4)}
    f4["windows"] = [{"name": f"{F}F",
                      "z_bottom": hyp(LVW[F] + 0.77, "same_block", "f3", HN_SW, [S90, S93], "仮説（same_block）：f3 の SWb の段の高さ", tol=tzw(LVW[F])),
                      "z_top": hyp(LVW[F + 1] - 0.08, "same_block", "f3", HN_SW, [S90, S93], "仮説（same_block）：f3 の SWb の段の高さ", tol=tzw(LVW[F + 1])),
                      "width": est(1.045, tb(1.045, 0.25), [S93], m_f4), "bays": [1, 2],
                      "spandrel": {"height": est(gb_h, 0.15, [S93], "(b) #93 でガラスブロックの升は各段の下（西面と同じ作り）"), "material": "white", "in_recess": True}}
                     for F in range(2, 7)]
    m_f4g = ("(b) 1F：#86・#93 でガラスブロックの欄間は列 2 本分の幅で 1F の上半分、下に 4 枚の窓が下半分（観察票 kansatsu_f4.json の parts。"
             "1F の高さ＝デッキの床 7.23〜2F の白帯 13.70）")
    f4["glazing"] = [glz("1F の 4 枚の窓", est(1.2, tb(1.2, 0.3), [S86, S93], m_f4g), est(5.3, tb(5.3, 0.2), [S86, S93], m_f4g),
                         est(DECK + 0.4, 0.8, [S93], m_f4g, ref=REF_SW), est(10.4, 0.8, [S93], m_f4g),
                         est(0.25, tb(0.25, 0.3), [S93], "(b) 窓の見込みは窓の列の幅の約 0.2"), est(2.0, None, [S93], "ガラスの奥の暗い室内の箱の奥行き（見た目の道具の値）"))]
    f4["ledges"] = [{"name": "SWb の上端（6F の上の水切り）", "z": est(LVW[7], tzw(LVW[7]) + 0.3, [S90, S93, W14],
                                                              "#90・#93 で SWb の上端は窓の段 5 の上（西面 f3 の SWb の上端と同じ段、#14）", ref=REF_SW),
                     "depth": est(0.3, tb(0.3, 0.3), [W14], "(b) #14 で水切りの出は窓の列の幅の約 0.2"),
                     "height": est(0.2, tb(0.2, 0.3), [W14], "(b) #14 で厚さは白帯の約 1.5 倍"), "material": "coping"}]
    f5["name"] = "南南西面 f5（S-SW の東端、西の袖 f6 との入隅。方位 207°、2.2 m）"
    f5["note"] = "f4 の続き（S-SW）。東端に全高の縦のガラスのすき間（入隅）"
    f5["wall_group"] = {"id": "S-SW", "faces": ["f4", "f5"], "primary": "f4"}
    m_slit5 = "(b) 入隅の縦のガラスのすき間：#90・#93 で幅は窓の列 1 本分（1.35 m）、1F の上から袖の上の方まで（観察票 kansatsu_f5.json の parts）"
    f5["glazing"] = [glz("入隅の縦のガラスのすき間", est(0.81, tb(0.81, 0.3), [S90, S93, S118], m_slit5), est(2.16, 0.2, [S90, S93], m_slit5 + "（f5 の東の端まで）"),
                         est(LVW[2], tzw(LVW[2]) + 0.5, [S93], m_slit5, ref=REF_SW), est(LVW[14], tzw(LVW[14]) + 1.0, [S90, S118], m_slit5, ref=REF_SW),
                         est(0.5, tb(0.5, 0.3), [S93], "(b) 入隅で奥へ引っ込む（#93 で列の幅の約 0.4）"), est(1.0, None, [S93], "ガラスの奥の暗い室内の板の厚さ（見た目の道具の値）"))]
    # 袖の側面（深い開口が各階に 1 つ）
    HN_WING = "袖の側面の開口は各階に 1 つ（観察票）。段は数え切れない（kazoe は unknown）が、同じ棟の床なので西（東）面の床の白帯の高さと置いた"

    def wing_side(f, from_face, LV, tz_, ev, wdt, hgt, ins_v, what, rows=range(2, 14)):
        L = f["length"]["value"]
        m = (f"(b) {what}：開口の幅は側面の幅の約 {wdt / L:.1f}（{wdt:.1f} m）、高さは段の約 {hgt / 4.37:.2f}（{hgt:.1f} m）で白帯のすぐ下"
             f"（#12・#14・#121 の見上げ）。深い開口（奥が暗い）")
        f["bays"] = {"count": est(1, None, ev, "深い開口の縦の列 1（kazoe の bays）", unit="列"),
                     "pitch": est(round(wdt + 0.4, 2), tb(wdt, 0.25), ev, m), "margin_from": est(round((L - wdt - 0.4) / 2, 2), tb(L / 2, 0.3), ev, m + "。辺の中ほど"),
                     "pier_width": est(0.4, 0.2, ev, "(b) 開口の脇の壁（見た目の値に近い。開口が辺の中ほどにある）")}
        wl = []
        for F in rows:
            zt = LV[F + 1] - 0.25
            zb = zt - hgt
            wl.append({"name": f"{F}F の深い開口",
                       "z_bottom": hyp(zb, "same_block", from_face, HN_WING, ev, m, tol=tz_(zb) + 0.3),
                       "z_top": hyp(zt, "same_block", from_face, HN_WING, ev, m, tol=tz_(zt) + 0.3),
                       "width": est(wdt, tb(wdt, 0.25), ev, m), "recess": est(ins_v, tb(ins_v, 0.3), ev, "(b) 深い開口：#14 の斜めで奥行きは幅の約 0.6"), "bays": [1]})
        f["windows"] = wl

    f6["name"] = "西北西面 f6（西の袖 WW の西向きの側面。方位 297°、2.9 m）"
    f6["note"] = "各階に深い開口 1 つ（横長、奥が暗い）。開口の手すりは見える段と見えない段があり置いていない（数が決まらない）。6F より上で側面は北へ続く（SWu の引っ込み、塊にしていない）"
    wing_side(f6, "f3", LVW, tzw, [W14, W12, S86], 1.7, 1.1, 1.0, "西の袖の西向き")
    f8["name"] = "東南東面 f8（西の袖 WW の東向きの側面、吹き抜けの西の壁。方位 117°、3.0 m）"
    f8["note"] = "各階に縦長の開口 1 つ。鉄骨のはしごと手すり（parts）"
    wing_side(f8, "f3", LVW, tzw, [S121, S113], 0.9, 1.9, 0.8, "西の袖の東向き（縦長）")
    f10["name"] = "西北西面 f10（東の袖 WE の西向きの側面、吹き抜けの東の壁。方位 298°、2.6 m）"
    f10["note"] = "各階に深い開口 1 つ"
    wing_side(f10, "f15", LVE, tze, [S121, S90], 1.5, 1.1, 1.0, "東の袖の西向き")
    f12["name"] = "東南東面 f12（東の袖 WE の東向きの側面。方位 118°、2.5 m）"
    f12["note"] = "各階に深い開口 1 つ（#127・#37 の左端）"
    wing_side(f12, "f15", LVE, tze, [S127, E37], 1.4, 1.1, 1.0, "東の袖の東向き")
    # 袖の正面（無地）
    # 検収（KENSHU）：袖の白帯は仮説（same_block：塔の床の高さ 12 本）だったが、目印係の登録 8 枚（S_REG）で袖の正面の縦の線（s 3.5）に沿って
    # 明るい帯を拾うと、2F の灰色の帯から上端までに 13 本・間隔 4.0〜4.2 m で、塔の床（4.37 m）から上へ行くほどずれる（14F で −3.5 m 前後）。
    # 仮説を写真の値で確定した（帯の中心＝写真ごとの中央値。f7 は正面寄りの 6 枚、f11 は 8 枚。± は 0.3＋写真の散らばりの半分）
    WING_BANDS = {
        # f7 の 8〜12 本目は検収 2 回目に −0.10（再検収 20261107_160222 で上端 −0.16〜−0.46・下端 −0.07〜−0.25、1〜4 枚）
        "f7": ([17.94, 22.12, 26.29, 30.41, 34.58, 38.56, 42.63, 46.50, 50.46, 54.44, 58.38, 62.21, 67.08],
               [0.40, 0.48, 0.53, 0.55, 0.57, 0.55, 0.55, 0.57, 0.64, 0.72, 0.90, 1.00, 1.33], [S113, S118, S119, S120, S121, S122]),
        "f11": ([18.15, 22.43, 26.68, 30.91, 35.09, 39.17, 43.15, 47.05, 51.02, 55.00, 59.01, 62.81, 67.53],
                [0.69, 0.87, 1.05, 1.18, 1.44, 1.45, 1.65, 1.82, 2.00, 2.25, 2.42, 2.67, 3.03], S_REG),
    }
    for f, nm, ev, frm in ((f7, "西の袖 WW の正面", [S93, S121], "f3"), (f11, "東の袖 WE の正面", [S124, S123], "f15")):
        LV, tz_ = (LVW, tzw) if frm == "f3" else (LVE, tze)
        f["name"] = f"南南西面 {f['id']}（{nm}。方位 208°、{f['length']['value']:.1f} m）"
        f["note"] = (f"無地（観察票 kansatsu_{f['id']}.json：窓の無いタイルの壁、白帯、全高）。bays は書かない。下の 2 段は白帯の代わりに灰色の太い帯、"
                     "足元は石積み風の粗いタイルの基壇（parts の patch）。白帯は検収係が登録 8 枚の視点から読んだ 13 本（塔の床の 12 本ではない。"
                     f"{KENSHU}#{f['id']}）")
        zc, sp, evb = WING_BANDS[f["id"]]
        m_wb = (f"検収（{KENSHU}#{f['id']}）：目印係の登録の視点（data/reports/tower75/mejirushi/picks_*.json、recorder photos pnp）で、"
                f"{f['id']} の s 3.5 の縦の線に沿って写真の明るい帯（周り ±0.6 m の中央値より 25 以上明るい山）の高さを面の平面の上で読み、"
                f"写真ごとの中央値（{len(evb)} 枚）。2F の灰色の帯から上端までに 13 本（どの写真も 13 本）。± は 0.3＋写真の散らばりの半分")
        bl = []
        for k, (c, spread) in enumerate(zip(zc, sp)):
            t = round(0.3 + spread / 2, 2)
            bl.append({"name": f"白帯 {k + 1}（2F の灰色の帯から上へ {k + 1} 本目）",
                       "z_bottom": est(c - 0.06, t, evb, m_wb), "z_top": est(c + 0.06, t, evb, m_wb),
                       "depth": est(0.02, 0.02, evb, "白帯は壁から少し出る（見た目の値。写真では厚みが読めない）"),
                       "color": "#e9e4da"})
        m_g = "(b) 灰色の太い帯：#93・#121・#124 で 1F の床ほど（基壇の上端）と 2F の床ほどに、白帯の約 3 倍の太さ（観察票 kansatsu_f7.json の bands）"
        for nm2, zc in (("2F の床の灰色の太い帯", LV[2] if frm == "f3" else LVE[2]), ("基壇の上端の灰色の太い帯", DECK)):
            # 検収 2 回目：2F の帯の下端は f7 2 枚 +0.24・f11 4 枚 +0.18（帯は 0.2 m ほど）→ 下端を帯の中心の高さに
            zb_g = zc if nm2.startswith("2F") else zc - 0.2
            bl.append({"name": nm2, "z_bottom": est(zb_g, 0.6, ev, m_g + ("。下端は検収 2 回目に +0.2（再検収 20261107_160222）" if nm2.startswith("2F") else "")),
                       "z_top": est(zc + 0.2, 0.6, ev, m_g),
                       "depth": est(0.05, 0.03, ev, m_g), "color": "#9a958c"})
        if f["id"] == "f11":
            m_g3 = (f"検収（{KENSHU}#f11）：基壇の中ほどの灰色の帯（目の高さの少し上。吹き抜けの前の低い壁の笠木と同じ高さに続く）。"
                    "登録 5 枚（062323658・062330274・062046145・062307232・062311895）の視点で f11 の s 4.0 の縦の線の明るさの段差を読んだ："
                    "下の縁 2.18〜2.52（中央値 2.43）、上の縁 2.42〜2.80（中央値 2.70）")
            bl.append({"name": "基壇の中ほどの灰色の帯（低い壁の笠木の高さ）", "z_bottom": est(2.43, 0.25, [S124, S125, S113, S121, S122], m_g3),
                       "z_top": est(2.70, 0.25, [S124, S125, S113, S121, S122], m_g3),
                       "depth": est(0.05, 0.03, [S124], "見た目の値（写真では出が読めない）"), "color": "#9a958c"})
        f["bands"] = bl
    # 吹き抜け f9
    f9["name"] = "南南西面 f9（両袖の間の吹き抜けのガラス。方位 208°、12.4 m）"
    f9["note"] = ("両袖の間の全幅のガラス（柱間 1、kazoe_f9.json）。日よけ棚 7（下の太い棚 2・上の格子の棚 5）。ガラスは基壇の上 3.0 から 7F の床 35.45 まで、"
                  "7F〜11F の 5 段はガラスの無い吹き抜け（parts の cut）、12F〜14F に鉄骨とガラスの枠（S11）。高さは検収係が登録 4 枚の視点で読んだ"
                  f"（{KENSHU}#f9。設計の『頂部 2 段』は読み違い）。前の西寄りに高さ 2.7 m の低い壁（三角測量）")
    f9["bays"] = {"count": meas(1, None, [S121, S20], "両袖の間の全幅のガラスの柱間 1（kazoe_f9.json、#121・#20）", unit="列"),
                  "pitch": est(12.4, 0.65, [S121], "辺の全幅（OSM。柱型の割りは無い）"), "margin_from": est(0.0, 0.3, [S121], "辺の両端は袖の側面の入隅")}
    # 検収（KENSHU#f9）：設計（観察票の「頂部 2 段が吹き抜け」）ではガラスが 12F の床まで・吹き抜け 12F〜13F・枠 14F だったが、
    # 目印係の登録 4 枚（062046145・062307232・062311895・062300065）の視点で f9 の中ほど（s 6.2 ± 1.5）の縦の線の明るさの段差を読むと、
    # ガラスの上端 35.42〜35.60（4 枚とも。西の 7F の床 35.55 と同じ）、暗い吹き抜け 35.5〜57.4（段差が 1 つも無い）、枠の下端 57.36〜57.86、
    # 枠の上端 71.4〜72.9。ガラスの下端は低い壁（基壇）の笠木 2.9〜3.6。日よけ棚 7 本（太い 2 本＋格子 5 本、観察票と同じ数）の高さも読んだ
    m_f9 = (f"検収（{KENSHU}#f9）：目印係の登録 4 枚の視点で f9 の s 6.2（± 1.5 の 9 本の中央値）の縦の線に沿って明るさの段差を面の平面の上で読んだ。"
            "ガラスの上端 35.42〜35.60（4 枚、明るいガラス→暗い吹き抜けの段差 −157〜−172）、下端は基壇の笠木の上（2.9〜3.6 の段差、4 枚）。"
            "設計の『吹き抜けの下端＝12F の床』は観察票の『頂部 2 段』の読み違いで、吹き抜けは 7F〜11F の 5 段分")
    f9["glazing"] = [glz("吹き抜けのガラスのカーテンウォール", est(0.1, 0.2, [S121], "(b) 袖の側面の入隅から"), est(12.3, 0.4, [S121], "(b) 同上"),
                         est(3.0, 0.4, [S113, S121, S122, S120], m_f9), est(35.45, 0.6, [S113, S121, S122, S120], m_f9),
                         est(0.3, tb(0.3, 0.3), [S121], "(b) ガラスは袖の側面の入隅の奥（#121 で棚の出の約 1/4）"),
                         est(6.0, None, [S121], "ガラスの奥の暗い吹き抜けの箱の奥行き（見た目の道具の値。寸法ではない）"))]
    m_sh = (f"検収（{KENSHU}#f9）：日よけ棚 7 本（太い棚 2・格子の棚 5。観察票 kansatsu_f9.json と同じ数）の高さを、登録 4 枚の視点で f9 の s 6.2 の"
            "縦の線の明るさの段差と、062046145 に高さの線を重ねた拡大（格子の斜めの縞が線に乗る所）で読んだ：太い棚 5.3〜5.9・11.7〜12.6、"
            "格子の棚 16.5・20.6・24.8・29.0・33.2（間隔 4.2 m）。棚がガラスに付く所の高さ。出は袖の側面の開口の幅の約 0.7（観察票）。"
            "検収 2 回目：再検収 out/tower75/recheck/20261107_160222 で格子の棚 2・3 が 8・6 枚の中央値 +0.15・+0.16 → 20.75・24.95")
    led9 = []
    for nm, z, dep, hgt in [("太い棚 1（一番下の高い段の上）", 5.6, 1.2, 0.35), ("太い棚 2", 12.2, 1.2, 0.35)] + \
                           [(f"格子の棚 {k + 1}", z, 1.0, 0.12) for k, z in enumerate((16.5, 20.75, 24.95, 29.0, 33.2))]:  # 2・3 は検収 2 回目 +0.15
        led9.append({"name": nm, "z": est(z, 0.5, [S113, S121, S122, S120], m_sh), "depth": est(dep, tb(dep, 0.3), [S121], m_sh),
                     "height": est(hgt, tb(hgt, 0.3), [S121], m_sh), "along0": est(0.1, 0.2, [S121], m_sh), "along1": est(12.3, 0.4, [S121], m_sh),
                     "material": "metal"})
    f9["ledges"] = led9
    # f13：南東の低い塊 SEb の南向き
    f13["name"] = "南南西面 f13（南東の低い塊 SEb の南向き。方位 208°、8.0 m）"
    f13["note"] = ("SEb の 2 列（kazoe_f13.json：2 列・窓の段 5＋1F＋B1＋基壇）。西の端に入隅の縦のガラスのすき間。列の位置は f4 と同じ比"
                   "（観察票：f4 と同じに見える）を 8.0 m からすき間を除いた 6.65 m に当てた。段は東面の床（SEb の東向きと同じ塊、仮説 same_block）")
    m_f13 = ("(b) 観察票 kansatsu_f13.json の pier_to_window（f4 と同じ、端の壁：列 ≈ 1.5：1、#124）を、f13 の 8.0 m から西端の入隅のすき間 1.35 m を"
             "除いた 6.65 m に当てた：1 単位 1.023 m")
    f13["bays"] = {"count": meas(2, None, [S124, S126], "SEb の窓の縦の列 2（kazoe_f13.json、#124・#126）", unit="列"),
                   "pitch": est(2.56, tb(2.56, 0.25), [S124], m_f13), "margin_from": est(1.35 + 1.53 + 0.51 - 1.28, tb(2.1, 0.3), [S124], m_f13),
                   "pier_width": est(1.53, tb(1.53, 0.25), [S124], m_f13)}
    HN_SE = "SEb の南向きの段は、同じ塊の東向き（東面の南端、2F〜6F）と同じ床。東面の床の白帯の高さを写した"
    f13["windows"] = [{"name": f"{F}F",
                       "z_bottom": hyp(LVE[F] + 0.77, "same_block", "f15", HN_SE, [S124, S126], "仮説（same_block）：f15 の段の高さ", tol=tze(LVE[F])),
                       "z_top": hyp(LVE[F + 1] - 0.08, "same_block", "f15", HN_SE, [S124, S126], "仮説（same_block）：f15 の段の高さ", tol=tze(LVE[F + 1])),
                       "width": est(1.023, tb(1.023, 0.25), [S124], m_f13), "bays": [1, 2],
                       "spandrel": {"height": est(gb_h, 0.15, [S124], "(b) #124 でガラスブロックの升は各段の下"), "material": "white", "in_recess": True}}
                      for F in range(2, 7)]
    m_f13g = ("(b) #124：1F はガラスブロックの欄間（上半分）と 4 枚の窓（下半分）、B1 は大きな窓（幅は 2 列の幅、高さ 2/3 段）。"
              "高さは東面の低層の窓（#42 の正面化：1F の窓 8.3〜10.2、B1 の窓 4.5〜6.9）と同じ床と見た")
    f13["glazing"] = [
        glz("入隅の縦のガラスのすき間", est(0.0, 0.2, [S124, S126], m_slit5.replace("f5", "f13")), est(1.35, tb(1.35, 0.25), [S124, S126], m_slit5),
            est(LVE[2], tze(LVE[2]) + 0.5, [S124], m_slit5, ref=REF_SE), est(LVE[14], tze(LVE[14]) + 1.0, [S124], m_slit5, ref=REF_SE),
            est(0.5, tb(0.5, 0.3), [S124], "(b) 入隅で奥へ引っ込む（#124 で列の幅の約 0.4）"), est(1.0, None, [S124], "ガラスの奥の暗い室内の板の厚さ（見た目の道具の値）")),
        glz("1F の 4 枚の窓", est(2.4, tb(2.4, 0.25), [S124], m_f13g), est(6.6, tb(6.6, 0.15), [S124], m_f13g),
            est(8.3, 0.8, [S124, E42], m_f13g), est(10.2, 0.8, [S124, E42], m_f13g),
            est(0.25, tb(0.25, 0.3), [S124], "(b) 窓の見込みは窓の列の幅の約 0.2"), est(2.0, None, [S124], "ガラスの奥の暗い室内の箱の奥行き（見た目の道具の値）")),
        glz("B1 の大きな窓", est(2.4, tb(2.4, 0.25), [S124], m_f13g), est(6.6, tb(6.6, 0.15), [S124], m_f13g),
            est(4.0, 0.8, [S124, E42], m_f13g), est(6.9, 0.8, [S124, E42], m_f13g),
            est(0.25, tb(0.25, 0.3), [S124], "(b) 同上"), est(2.0, None, [S124], "ガラスの奥の暗い室内の箱の奥行き（見た目の道具の値）"))]
    f13["ledges"] = [{"name": "SEb の上端（6F の上の水切り）", "z": est(LVE[7], tze(LVE[7]) + 0.3, [S124, S127],
                                                              "#124 で SEb の上端は窓の段 5 の上（観察票：南西の低い塊と同じ段）", ref=REF_SE),
                      "depth": est(0.3, tb(0.3, 0.3), [S124], "(b) 水切りの出は窓の列の幅の約 0.2"),
                      "height": est(0.2, tb(0.2, 0.3), [S124], "(b) 厚さは白帯の約 1.5 倍"), "material": "coping"}]

    b["parts"] = parts_list(faces, A, E)
    b["requests"] = requests_list()
    b["history"] = history_list()
    b["photos"] = []
    b = schema.apply_hypotheses_off(b, HYPOTHESES_OFF)
    return b


# =========================================================================================
def parts_list(faces, A, E) -> list:
    P = []
    f3, f15, f9 = faces["f3"], faces["f15"], faces["f9"]

    # --- S1 西のデッキ（赤れんがの床と縁の腰壁）---------------------------------------------
    deck_d = 14.0
    m_deck = ("(b) デッキの床は 1F の床（西面の stack で 7.23 m、ref）。出は登録したカメラの位置（registry）：デッキの上で撮った 8 枚が面から 9.9〜13.5 m、"
              "カメラの高さ z 7.6〜8.7（床＋手持ち）。縁の腰壁は窓の段の約 1/3（観察票 S1）。縁の位置は見えていない（大階段・エレベーター箱の前）ので "
              "14 m（± 30%）。範囲は西面の北西の角（f1 の始点）から南西の角まで（南西の端は道路をまたぐ橋 S2 に続く。橋は敷地の依頼）")
    P.append(comp("西のデッキ（S1：赤れんがの床と縁の腰壁）", "f3", [
        box("floor", (-11.47, 21.19), (0.0, deck_d), (DECK - 0.5, DECK), "brick"),
        box("parapet_south", (8.3, 21.19), (deck_d - 0.25, deck_d), (DECK, DECK + 1.1), "tile"),
        box("front_wall_south", (8.3, 21.19), (deck_d - 0.25, deck_d), (ROAD_W, DECK - 0.5), "tile"),
    ], [W16, W17, W80, W81, W82, W83, W9, W107], m_deck + "。南の縁の腰壁と道路までの壁は #9・#107（エレベーター箱の南へ続くタイルの壁と青い手すり）", 4.2,
        note="床の下の作り（柱・店）は見えていないので置いていない。北の端は北西のテラス（S6）"))

    # --- S5 西の青い鉄骨の庇 ----------------------------------------------------------------
    m_cn = ("(b) 屋根の高さはデッキ階の入口の上端（ガラスブロックの欄間の下）ほど（観察票 S5。#83 の正面化で欄間の下 10.9 m）。出は #82・#16 の柱の台の位置："
            "カメラ（registry：面から 10.7・11.4 m）の手前 3〜4.5 m に柱の台が写る → 柱の線は面から約 7 m。柱の間隔は西面の窓の列 2 つ分ほど（観察票）＝6.8 m。"
            "北西の角では渡り廊下の下を抜けて北へ続く（続きは置いていない：見えた範囲だけ）")
    cn = [box("roof", (-11.47, 21.19), (0.0, 7.5), (10.75, 10.95), "metal"), box("edge_beam", (-11.47, 21.19), (7.2, 7.5), (10.45, 10.95), "metal")]
    for k, s in enumerate((-10.0, -3.2, 3.6, 10.4, 17.2)):
        cn.append(box(f"column{k}", (s - 0.12, s + 0.12), (7.0, 7.24), (DECK + 0.5, 10.75), "metal"))
        cn.append(box(f"base{k}", (s - 0.3, s + 0.3), (6.82, 7.42), (DECK, DECK + 0.5), "concrete"))
    P.append(comp("西の青い鉄骨の庇（S5）", "f3", cn, [W16, W17, W82, W83, W100], m_cn, 2.1))

    # --- S4 西の大階段 -----------------------------------------------------------------------
    m_st = ("(b) 観察票 S4：幅の広い段が 2 つに分かれ、間に踊り場（踊り場は上の段の上端の約半分の高さ）、上の段の上端がデッキの床。幅は西面の窓の列の間隔の 3〜4 倍"
            "（3.5 × 3.40 ＝ 11.9 m）。位置は #9・#107 で渡り廊下の下、エレベーター箱の北脇（#76 の正面化でエレベーター箱の北の縁が f3 の s 2.8）。"
            "1 つの段の流れは蹴上げ 0.17 m・踏み面 0.30 m（#9 で段の数 15 前後）と見た")
    half = (DECK - ROAD_W) / 2 + 0.28   # 検収（KENSHU#W）：踊り場の上端が 11 枚で +0.28（再検収 RECHECK1 の中央値）
    run = 15 * 0.30
    st = [wedge("upper_flight", (-9.1, 2.8), (deck_d, deck_d + run), (ROAD_W + half, DECK), "-d", mat="stone"),
          box("landing", (-9.1, 2.8), (deck_d + run, deck_d + run + 2.0), (ROAD_W, ROAD_W + half), "stone"),
          wedge("lower_flight", (-9.1, 2.8), (deck_d + run + 2.0, deck_d + 2 * run + 2.0), (ROAD_W, ROAD_W + half), "-d", mat="stone"),  # 下の段は half まで
          box("upper_base", (-9.1, 2.8), (deck_d, deck_d + run), (ROAD_W, ROAD_W + half), "stone")]
    for k, s in enumerate((-9.0, -5.0, -1.0, 2.7)):
        st.append(box(f"rail{k}", (s - 0.03, s + 0.03), (deck_d, deck_d + 2 * run + 2.0), (DECK + 0.85, DECK + 0.9), "metal"))
    m_st += f"。踊り場の高さは検収（{KENSHU}#W）で道路とデッキの中ほど＋0.28 m（再検収 {RECHECK1}：踊り場の上端 11 枚の中央値 +0.28）"
    P.append(comp("西の大階段（S4：道路からデッキへ）", "f3", st, [W9, W76, W104, W107, "PXL_20261103_070134689"], m_st, 3.6,
                  note="手すりは 3〜4 本（観察票）。高さは上の段の上端に合わせた水平の棒で近似した"))

    # --- エレベーター箱 --------------------------------------------------------------------------
    m_ev = ("(b) #107・#9 の比：箱の高さは道路からデッキの腰壁の上端（6.2 m）の約 1.65 倍（#107 で 320 px 対 190 px）＝道路から 10.2 m、"
            "幅は高さの約 0.55（#107 で 185 px）＝5.5 m。観察票の比（デッキの床＋1/3 段、窓の列 1.5 本分）より高く読めたので写真の比を採った。"
            "位置は #76 の正面化で北の縁が f3 の s 2.8（カメラの真横なので位置のずれが小さい）、前面はデッキの縁の腰壁より 1 m 前（#107）。"
            "扉の上にコンクリートの小庇（扉の上端＝高さの約 0.35）")
    ev_top = ROAD_W + 10.2
    P.append(comp("エレベーター箱（大階段の南脇）", "f3", [
        box("shaft", (2.8, 8.3), (deck_d - 2.5, deck_d + 1.0), (ROAD_W, ev_top), "tile"),
        box("door_canopy", (3.4, 7.7), (deck_d + 1.0, deck_d + 1.8), (ROAD_W + 3.4, ROAD_W + 3.6), "concrete"),
        box("door", (4.5, 6.0), (deck_d + 1.0, deck_d + 1.03), (ROAD_W, ROAD_W + 2.6), "white"),
    ], [W107, W9, W76, W104], m_ev, 1.4))

    # --- デッキの階の銀色の箱（北西の入口の南、渡り廊下の下） ---------------------------------------
    m_sb = ("(b) #101 の 1 枚：高さはデッキ階の約 2/3（観察票 kansatsu_f3.json の parts）＝4.3 m。幅は窓の列の間隔の約 1 倍、出は幅の約 0.8 と見た。"
            "位置は北西の入口（f1）の南、渡り廊下の下（f2 の北寄り）")
    P.append(comp("デッキの階の銀色の箱（風除室か機械室）", "f3", [box("box", (-9.0, -5.6), (0.0, 2.8), (DECK, DECK + 4.3), "metal")],
                  [W101], m_sb, 1.1))

    # --- ガラスブロックの欄間（デッキの階・西） --------------------------------------------------
    m_gb = (f"{M_W83}：ガラスブロックの欄間の左右の縁と上下（10.9〜11.75 m）。上端は検収（{KENSHU}#W）で 12.0 に上げた"
            f"（再検収 {RECHECK1} の写真の縁の中央値：欄間 1・2 は 20・24 枚で +0.32、3・4 は 19・28 枚で +0.16・+0.19、f2 は 12 枚で +0.18）")
    for k, (a0, a1) in enumerate(((-2.5, 0.5), (1.0, 6.3), (7.6, 12.8), (14.0, 16.5))):
        for fid, off in (("f3", 0.0), ("f2", OFF_F2)):
            L = faces[fid]["length"]["value"]
            s0, s1 = max(a0 + off, 0.0), min(a1 + off, L)
            if s1 - s0 < 0.3:
                continue
            P.append(patch(f"デッキの階のガラスブロックの欄間 {k + 1}（{fid}）", fid, s0, s1, 10.9, 12.0, "white", [W83, W82, W16], m_gb, tsw(a1), tzw(11.0)))
    # 北西の入口の欄間（f1）
    m_gb1 = ("(b) 北西の入口の上の大きなガラスブロックの欄間：#100・#101 で 1F の約 1/3 の高さ、扉と同じ幅（観察票 kansatsu_f1.json の parts）")
    P.append(patch("北西の入口のガラスブロックの欄間（f1）", "f1", 0.6, 4.6, DECK + 3.4, DECK + 5.6, "white", [W100, W101], m_gb1, tb(4.0, 0.25), 0.7))

    # --- 南西：SWb の 1F のガラスブロック（f4） ------------------------------------------------
    m_f4gb = "(b) #86・#93：SWb の 1F の上半分にガラスブロックの欄間、幅は列 2 本分（観察票 kansatsu_f4.json の parts）"
    P.append(patch("SWb の 1F のガラスブロックの欄間（f4）", "f4", 1.2, 5.3, 10.6, 13.3, "white", [S86, S93], m_f4gb, tb(4.1, 0.25), 0.8))

    # --- 西の袖の足元（基壇・扉・道路へ下りる段）と東の袖・SEb の足元 ---------------------------
    m_base = ("(b) 石積み風の粗いタイルの基壇：#93・#121・#124 で道路から 1F の床ほど（灰色の太い帯の下）まで（観察票 kansatsu_f7.json・f11・f13 の base）。"
              "下端は道路（5m 標高で面の前 0.4〜1.1 m）より下から")
    for fid, ev in (("f7", [S93, S121]), ("f9", [S121, S20]), ("f11", [S124, S123]), ("f13", [S124, S126])):
        L = faces[fid]["length"]["value"]
        ztop = {"f13": 2.6, "f9": 2.9}.get(fid, DECK - 0.2)
        extra = {"f13": "。f13 は B1 の大きな窓の下まで（#124）",
                 "f9": f"。f9 はガラスの下端（基壇の笠木の上 3.0）まで：検収（{KENSHU}#f9）で登録 4 枚の視点の明るさの段差 2.9〜3.6。"
                       "設計の 7.03（1F の床）は袖の正面の灰色の帯を吹き抜けにも写した読み違い"}.get(fid, "")
        evb = ev + ([S113, S122, S120] if fid == "f9" else [])
        P.append(patch(f"石積み風の基壇（{fid}）", fid, 0.0, L, -1.5, ztop, "stone", evb,
                       m_base + extra + "。左右は辺の全幅（± は OSM の辺の 5%＋タイルの読み）",
                       round(0.08 * L + 0.05, 2), 1.0 if fid != "f9" else 0.4))
    m_door = "(b) 扉：高さは 1 段の約 2/3（2.9 m）、幅は高さの約 0.4（観察票の parts、#121・#124・#126）。位置は写真の中の比"
    P.append(patch("西の袖の扉（f7）", "f7", 4.6, 5.8, 0.4, 3.3, "white", [S121], m_door + "：#121 で f7 の東寄り（幅の 0.65〜0.8）", tb(1.2, 0.3), 0.7))
    P.append(patch("東の袖の扉（f11）", "f11", 5.2, 6.4, 0.0, 2.9, "white", [S124], m_door + "：#124 で f11 の東寄り", tb(1.2, 0.3), 0.7))
    P.append(patch("SEb の基壇の扉（f13）", "f13", 6.3, 7.5, -1.3, 1.6, "white", [S126, S127], m_door + "：#126 で f13 の東寄り", tb(1.2, 0.3), 0.7))
    P.append(comp("メーターの箱（東の袖の基壇）", "f11", [box("box", (1.0, 1.9), (0.0, 0.35), (0.11, 1.93), "metal")], [S123, S124, S125, S122],
                  "(b) 観察票 kansatsu_f11.json：高さ 1/3 段（1.45 m）、基壇の西寄り。幅は高さの約 0.6。上下の縁は検収 2 回目（"
                  + KENSHU + "#f11）：再検収 out/tower75/recheck/20261107_160222 の下端 5 枚 −0.19・上端 3 枚 +0.18 で 0.3〜1.75 → 0.11〜1.93", 0.45))
    m_ds = ("(b) 道路へ下りる短い階段：#93・#118 で段 6〜8、幅は f6 の開口 1 つ分ほど（1.7 m）（観察票 kansatsu_f7.json の parts）。"
            "袖の西の脇（f6 の前）、デッキの橋との間を南へ下りる。高さは段 7 × 0.19 m（段の高さは扉の高さの約 1/15）")
    P.append(comp("道路へ下りる短い階段（西の袖の西の脇）", "f6", [wedge("steps", (0.3, 2.4), (0.2, 1.9), (0.4, 1.73), "-s", mat="stone")],
                  [S93, S118], m_ds, 0.5))

    # --- f8 のはしご --------------------------------------------------------------------------
    m_ld = "(b) 鉄骨のはしごと手すり：#121 で下の方、はしごの高さ 2 段分（8.7 m）（観察票 kansatsu_f8.json の parts）。吹き抜けの棚の点検用に見える"
    P.append(comp("鉄骨のはしご（f8、吹き抜けの西の壁）", "f8", [
        box("rail_a", (0.5, 0.56), (0.25, 0.31), (LVW[2], LVW[2] + 8.7), "metal"),
        box("rail_b", (0.95, 1.01), (0.25, 0.31), (LVW[2], LVW[2] + 8.7), "metal"),
        box("cage", (0.45, 1.06), (0.6, 0.66), (LVW[2] + 2.2, LVW[2] + 8.7), "metal")], [S121], m_ld, 2.2))

    # --- f9：吹き抜け（頂部 2 段の空き）・頂部の枠 S11・植え込み・コンクリートの台 ------------------
    void_d = 6.0
    m_void = ("(b) ガラスの無い吹き抜け（#121・#20、観察票 S11・kansatsu_f9.json）。高さは検収（" + KENSHU + "#f9）：登録 4 枚（062046145・062307232・"
              "062311895・062300065）の視点で f9 の s 6.2 の縦の線を読むと、ガラスの上端 35.42〜35.60 から枠の下端 57.36〜57.86 まで明るさの段差が 1 つも無い"
              "暗い面（奥の壁）。設計の『頂部 2 段（57.40〜66.14）』は観察票の読み違いで、7F〜11F の 5 段分。"
              "奥行きは見えていない（#121 の見上げで袖の内側の壁が奥へ続き、奥の壁が暗い）。吹き抜けの幅 12.4 m の約 1/2（6 m）と置いた。"
              "上は枠の箱（下の部品）が蓋をしていて、屋上まで抜けてはいない（航空写真は倒れ込みを 14.5 m と測り直すと、この位置は暗い穴ではなく格子に見える。"
              "out/tower75/kenshu_roof/roof_aerial.png）")
    P.append(comp("吹き抜け（ガラスの無い 7F〜11F の 5 段）", "f9", [box("void", (0.05, 12.35), (-void_d, 0.5), (35.45, 57.5), cut=True)],
                  [S121, S20, S113, S122, S120], m_void, 1.8))
    m_fr = ("(b) 観察票 S11：吹き抜けの全幅の鉄骨とガラスの格子の箱、上に手すり。高さは検収（" + KENSHU + "#f9）：登録 4 枚の視点で枠の下端 57.36〜57.86"
            "（暗い吹き抜け→明るい枠の段差 +133〜+148）、上端 71.4〜72.9（空との境）。設計の『1 段分（66.14〜71.0）』は読み違いで 12F〜14F の 3 段分。"
            "奥行きは見えていない（#121 の見上げで枠の底の格子の奥行きは幅の約 0.25）＝3 m")
    fr = [box("frame_zone", (0.05, 12.35), (-3.2, 0.5), (57.5, PARAPET + 0.5), "concrete", cut=True),
          box("glass_front", (0.1, 12.3), (-0.05, 0.03), (57.5, 71.0), "glass"),
          box("glass_back", (0.1, 12.3), (-3.0, -2.92), (57.5, 71.0), "glass"),
          box("bottom", (0.1, 12.3), (-3.0, 0.03), (57.25, 57.5), "metal"),
          box("roof_grating", (0.1, 12.3), (-3.2, 0.03), (71.3, 71.4), "metal"),
          box("top_rail", (0.1, 12.3), (-0.05, 0.03), (71.0, 71.1), "metal")]
    for k in range(9):
        s = 0.1 + k * (12.2 / 8)
        fr.append(box(f"post{k}", (s - 0.05, s + 0.05), (-0.05, 0.05), (57.5, PARAPET + 0.4), "metal"))
    P.append(comp("吹き抜けの頂部の鉄骨とガラスの枠（S11、12F〜14F）", "f9", fr, [S121, S118, S113, S122, S120, "PXL_20261103_070742138"], m_fr, 1.2))
    m_hd = ("(b) 植え込み（生け垣）：#20・#113・#121 で高さは 1/2 段の約半分（1.2 m）、幅は f9 の約 2/3、基壇の前（観察票 kansatsu_f9.json の parts は高さ 1/2 段）。"
            "西の端は検収（" + KENSHU + "#f9）で低い壁の角（s 4.13）の東に寄せた（#113・#121 で生け垣は壁の角の右から始まる）")
    P.append(comp("吹き抜けの前の植え込み（生け垣）", "f9", [box("hedge", (4.2, 10.9), (0.3, 1.5), (0.0, 1.6), "leaf"),
                                                    box("edge", (4.15, 11.0), (0.2, 1.6), (0.0, 0.35), "concrete")], [S20, S113, S121], m_hd, 0.5))
    m_pl = ("(b) 観察票 kansatsu_f9.json の parts『コンクリートの台（上に格子の蓋）、西寄り、高さ 1/3 段』。位置と高さは検収（" + KENSHU + "#f9）で"
            "登録 2 枚（062307232・062046145）の同じ点を三角測量した（手順書「奥行きは 2 枚で三角測量」）：笠木の前の東の角 f9 の s 4.13・面から 1.85 m・"
            "高さ 2.69 m（交わりの残り 0.09 m）、壁の照明 s 1.62・面から 1.49 m・0.65 m（同 0.06 m）。高さ 1/3 段（1.45 m）ではなく 2.7 m の低い壁で、"
            "面は基壇と同じ粗いタイル、上に灰色の笠木（目印係の『南の足元の低い壁』）。西の端は袖の側面 f8 に付く")
    P.append(comp("吹き抜けの前の低い壁（コンクリートの台、上に格子の蓋）", "f9", [box("base", (0.0, 4.13), (0.0, 1.85), (0.0, 2.55), "stone"),
                                                                    box("coping", (0.0, 4.13), (0.0, 1.85), (2.55, 2.69), "concrete"),
                                                                    box("grating", (0.1, 4.03), (0.1, 1.75), (2.69, 2.72), "metal")],
                  [S20, S121, S113], m_pl, 0.3))

    # --- 東 E：低層の張り出し帯 2・シャッター・扉・ガラリ・低い壁・ガラスブロックの欄間 -------------------
    m_bd = ("(b) 張り出した帯：観察票 kansatsu_f15.json の parts（帯の高さ 2/3 段、出は窓の列 2 本分ほど＝2.7 m、下面は白い軒裏）。範囲と高さは #42 の正面化と #24"
            "（張り出す物は正面化で大きく写るので、面の上の窓との位置関係から）：上の帯は 1F の窓の前の北寄りの半分、下の帯は B1 の窓の上の全幅に近い所")
    P.append(comp("東の張り出した帯（上：1F の窓の前、北寄り）", "f15", [
        box("band", (10.4, 22.4), (0.0, 2.7), (LVE[2] - 6.4, LVE[2] - 3.5), "tile"),
        box("soffit", (10.4, 22.4), (0.0, 2.7), (LVE[2] - 6.45, LVE[2] - 6.4), "white")], [E42, E24, E44, E47], m_bd, 0.9))
    P.append(comp("東の張り出した帯（下：B1 の窓の上）", "f15", [
        box("band", (-3.4, 18.0), (0.0, 2.7), (1.2, 4.1), "tile"),
        box("soffit", (-3.4, 18.0), (0.0, 2.7), (1.15, 1.2), "white")], [E42, E24, E44], m_bd + "。南は f14 まで続く（#24）", 0.9))
    m_e = f"{M_E42}：面の上の物（B2 の道路の階）"
    P.append(patch("シャッター（B2）", "f15", 5.3, 9.8, ROAD_E, 1.1, "metal", [E42, E24, E41],
                   m_e + f"。#24 でシャッターの右に黄色の柱 2。上端は検収（{KENSHU}#E）で 0.9 → 1.1（再検収 {RECHECK1}：12 枚の中央値 +0.20）", tse(5.3), tze(0.0)))
    P.append(patch("ベージュの両開きの大扉（B2、南寄り）", "f15", 0.0, 2.4, ROAD_E, 1.4, "white", [E42, E24], m_e + "。#24 で大扉の幅は柱の列 2 本分（観察票）", 0.6, tze(0.0)))
    P.append(patch("ベージュの両開きの大扉（B2、南寄り、f14 の分）", "f14", 2.0, 3.41, ROAD_E, 1.4, "white", [E42, E24], m_e, 0.6, tze(0.0)))
    m_lv = "(b) ガラリ（換気口）：#24 で大扉の南に 2 つ（観察票は 1/2 段ほど）。位置と大きさは大扉の幅・高さとの比"
    P.append({"type": "louver", "name": "ガラリ 1（B2、大扉の南脇、f14）", "face": "f14", "along": est(0.9, 0.5, [E24], m_lv),
              "width": est(0.95, tb(0.95, 0.3), [E24], m_lv + "：#24 で大扉（幅 3.9 m＝290 px）の左に 70 px"),
              "z0": est(0.45, 0.6, [E24], m_lv + "：大扉の高さ 3.4 m（365 px）に対して上端は扉の上端ほど、高さ 105 px"),
              "z1": est(1.45, 0.6, [E24], m_lv), "blade_pitch": est(0.1, 0.03, [E24], m_lv + "。羽根は細かい横羽根"),
              "blade_width": est(0.08, 0.03, [E24], m_lv), "blade_thickness": est(0.01, 0.005, [E24], m_lv),
              "angle": est(30, 15, [E24], m_lv, unit="度"), "orientation": "horizontal"})
    P.append({"type": "louver", "name": "ガラリ 2（B2、南の端、f14 の南の短い辺の前）", "face": "f14", "along": est(-2.0, 0.8, [E24], m_lv + "：#24 で大扉の左端から 295 px 左"),
              "width": est(1.5, tb(1.5, 0.3), [E24], m_lv + "：#24 で 110 px"), "z0": est(0.85, 0.6, [E24], m_lv), "z1": est(1.65, 0.6, [E24], m_lv),
              "blade_pitch": est(0.12, 0.03, [E24], m_lv), "blade_width": est(0.1, 0.03, [E24], m_lv), "blade_thickness": est(0.01, 0.005, [E24], m_lv),
              "angle": est(30, 15, [E24], m_lv, unit="度"), "orientation": "horizontal"})
    m_lw = "(b) B2 の入口の前の低い壁（ベージュ）：観察票 kansatsu_f15.json の parts（高さ 1/2 段＝2.2 m、長さは列 2 本分＝6.7 m）。#42・#44 で北寄りのインターホンの前"
    P.append(comp("B2 の入口の前の低い壁（ベージュ）", "f15", [box("wall", (13.0, 19.7), (1.2, 1.5), (ROAD_E, ROAD_E + 2.2), "white")], [E42, E44], m_lw, 1.7))
    m_egb = (f"{M_E42}：1F のガラスブロックの欄間（10.5〜12.15 m）。欄間 3 の左端・欄間 4 の左右は検収（{KENSHU}#E）で −0.25 m"
             f"（再検収 {RECHECK1}：欄間 3 の左端 12 枚 −0.26、欄間 4 の左端 8 枚 −0.26・右端 3 枚 −0.25）")
    for k, (a0, a1) in enumerate(((-1.0, 3.6), (5.1, 10.2), (11.74, 16.8), (18.35, 22.95))):
        for fid, off in (("f15", 0.0), ("f14", OFF_F14)):
            L = faces[fid]["length"]["value"]
            s0, s1 = max(a0 + off, 0.0), min(a1 + off, L)
            if s1 - s0 < 0.3:
                continue
            P.append(patch(f"1F のガラスブロックの欄間 {k + 1}（{fid}）", fid, s0, s1, 10.5, 12.15, "white", [E42, E24], m_egb, tse(a1), tze(11.0)))

    m_f13t = ("(b) #124：SEb の 1F の上半分にガラスブロックの欄間（観察票 kansatsu_f13.json の parts：欄間 1/2 段）。"
              "高さは東面の 1F のガラスブロックの欄間（#42 の正面化 10.5〜12.15 m）と同じ床と見た。幅は 4 枚の窓と同じ")
    P.append(patch("SEb の 1F のガラスブロックの欄間（f13）", "f13", 2.4, 6.6, 10.5, 12.15, "white", [S124, E42], m_f13t, tb(4.2, 0.25), 0.8))
    m_f0 = ("(b) 北西の低い塊 NWb の 1F：#1・#62 でガラスブロックの帯（上）と窓（下）、テラスの高さ（観察票 kansatsu_f20.json の glazing）。"
            "高さは西の北西の入口（f1）と同じ比（扉 1/2・欄間 1/3、1F＝7.23〜13.70）、幅は 2 列の窓の幅")
    P.append(patch("NWb の 1F のガラスブロックの帯（f0）", "f0", 0.6, 5.8, DECK + 3.4, DECK + 5.6, "white", [N1, N62], m_f0, tb(5.2, 0.25), 0.8))
    # --- 北：f16 の 1F・雨どい、f19 のガラスの冠・ガラリの帯、f20 の北西のテラス・パーゴラ・B1 の入口 -------
    m_f16 = "(b) #52：1F の大きなガラスブロックの欄間（窓 2 列分より広い、1F の上半分）と 4 枚の窓（観察票 kansatsu_f16.json の parts）"
    P.append(patch("NEb の 1F のガラスブロックの欄間（f16）", "f16", 0.4, 4.8, LVE[2] - 3.3, LVE[2] - 0.6, "white", [N52], m_f16, tb(4.4, 0.25), 0.8))
    P.append({"type": "pipes", "name": "白い雨どい（f16、庇の脇）", "face": "f16",
              "along": est(0.25, 0.5, [N52, N53], "(b) #52・#53：鉄骨の庇の脇（f16 の西寄り）、1F の高さ全部（観察票 kansatsu_f16.json の parts）"),
              "count": est(1, None, [N52, N53], "#52・#53 で 1 本", unit="本"),
              "radius": est(0.06, 0.02, [N52], "(b) 太さは窓の列の幅の約 0.1"),
              "z0": est(LVE[2] - 6.5, 1.0, [N52], "(b) 足元（北の通路）から"), "z1": est(LVE[2] - 0.4, 1.0, [N52], "(b) 1F の上端まで")})
    f19 = faces["f19"]
    m_cr = ("(b) 頂部のガラスの冠：観察票 kansatsu_f19.json の parts（高さ 2〜3 段分、幅は柱間の全幅、ひれは冠の高さの 1/3 ほど、下は庇のように張り出す）。"
            "冠の下に帯窓 11 段（#1）なので 13F の床（西の白帯 61.77）から上と置いた。両脇の柱型は冠より少し高い（#1・#3）")
    c0, c1 = world(f19, 1.4, -0.4), world(f19, 12.8, -0.4)
    c2, c3 = world(f19, 12.8, 1.0), world(f19, 1.4, 1.0)
    P.append({"type": "box", "name": "f19 の頂部のガラスの冠", "polygon": [c0, c1, c2, c3],
              "z0": est(LVW[13], 1.0, [N1, N3, N61, N62], m_cr, ref=REF_SW), "z1": est(PARAPET - 0.6, 1.0, [N1, N3, N62], m_cr), "material": "glass"})
    fins = [box("canopy", (1.4, 12.8), (0.0, 1.6), (LVW[13] - 0.3, LVW[13]), "metal")]
    for k, s in enumerate((2.6, 5.4, 8.2, 11.0)):
        fins.append(wedge(f"fin{k}", (s - 0.08, s + 0.08), (-0.4, 1.0), (PARAPET - 0.6, PARAPET + 2.6), "+d", mat="metal"))
    P.append(comp("f19 のガラスの冠のひれ 4 枚と下の庇", "f19", fins, [N1, N3, N62, N64], m_cr, 1.0))
    m_gr = "(b) 一番下の帯窓の上の暗いガラリの帯：#55・#58 で帯窓の高さの約 1/3（観察票 kansatsu_f19.json の parts）"
    P.append({"type": "louver", "name": "f19 の一番下の帯窓の上のガラリの帯", "face": "f19", "along": est(1.4, 0.4, [N55, N58], m_gr),
              "width": est(11.4, tb(11.4, 0.2), [N55, N58], m_gr), "z0": est(LVW[2] + 3.55, 0.8, [N55], m_gr, ref=REF_SW),
              "z1": est(LVW[2] + 3.95, 0.8, [N55], m_gr, ref=REF_SW), "blade_pitch": est(0.1, 0.03, [N55], m_gr),
              "blade_width": est(0.08, 0.03, [N55], m_gr), "blade_thickness": est(0.01, 0.005, [N55], m_gr),
              "angle": est(30, 15, [N55], m_gr, unit="度"), "orientation": "horizontal"})
    # 北西のテラス（1F の高さ）と下の B1 の入口・パーゴラ 3 基（f0 の座標。f0・f20 は 1 直線）
    f0 = faces["f0"]
    plaza = round(DECK - PW, 2)
    t_d = 8.0
    m_tr = (f"(b) 北西のテラス（S1 の北の端）：床はデッキ階と同じ（7.23）、その下が北の沈んだ広場（B1、デッキより 1 段低い＝{plaza} m、観察票 S7）。"
            "テラスの前面はタイルの壁で、B1 のガラスの入口（1 段分の高さ、薄い庇、細い黒い柱 2 本）。出は #1・#3 でテラスの前面の壁の長さの約 0.5（8 m）、"
            "範囲は f0 と f20 の西半分（#1 で B のスリットの柱の西まで）")
    tr = [box("floor", (-4.0, 6.41 + 3.0), (0.0, t_d), (DECK - 0.5, DECK), "brick"),
          box("front_wall", (-4.0, 6.41 + 3.0), (t_d - 0.3, t_d), (plaza, DECK - 0.5), "tile"),
          box("parapet", (-4.0, 6.41 + 3.0), (t_d - 0.25, t_d), (DECK, DECK + 1.1), "tile"),
          box("b1_glass", (-2.5, 4.5), (t_d - 0.32, t_d + 0.02), (plaza + 0.1, plaza + PW - 0.5), "glass"),
          box("b1_canopy", (-2.8, 4.8), (t_d, t_d + 2.2), (plaza + PW - 0.5, plaza + PW - 0.35), "metal"),
          box("b1_col0", (-1.5, -1.38), (t_d + 2.0, t_d + 2.12), (plaza, plaza + PW - 0.5), "dark"),
          box("b1_col1", (3.4, 3.52), (t_d + 2.0, t_d + 2.12), (plaza, plaza + PW - 0.5), "dark")]
    P.append(comp("北西のテラスと下の B1 のガラスの入口（S6・S7）", "f0", tr, [N1, N3, N62, N64, N65], m_tr, 2.4,
                  note="広場から上がる幅の広い大階段（#1 の右）と擁壁の丸い照明は置いていない（大階段は広場の物で敷地の依頼、照明は 1 m 未満）"))
    m_pg = ("(b) 北西のテラスの青い鉄骨のパーゴラ：#3 で 3 基、高さはデッキ階の約 2/3 段（2.9 m）（観察票 S6）。1 基の幅は窓の列の間隔の約 1 倍、"
            "奥行きは幅の約 1 倍、テラスの前寄りに横に並ぶ")
    for k, s in enumerate((-2.5, 1.2, 4.9)):
        poly = [world(f0, s, 3.0), world(f0, s + 3.0, 3.0), world(f0, s + 3.0, 6.2), world(f0, s, 6.2)]
        P.append({"type": "pergola", "name": f"北西のテラスのパーゴラ {k + 1}", "polygon": poly,
                  "z0": est(DECK, tzw(DECK), [N3, N1], m_pg, ref=REF_SW), "height": est(2.9, tb(2.9, 0.3), [N3, N1], m_pg),
                  "post_pitch": est(3.0, tb(3.0, 0.3), [N3], m_pg), "post_size": est(0.15, 0.05, [N3], m_pg),
                  "beam_pitch": est(0.5, 0.15, [N3], m_pg + "。上の細い桟の間隔は柱の間隔の約 1/6"),
                  "beam_width": est(0.08, 0.03, [N3], m_pg), "beam_height": est(0.15, 0.05, [N3], m_pg)})

    # --- 屋上：塔屋・アンテナ -------------------------------------------------------------------
    m_ph = ("(a)＋(b) 塔屋：遠景 #37 の正面化（data/reports/tower75/sokuryo_f15.json の readings_px：塔屋の上端 355.5 px、パラペットの 1.31 P 上＝5.67 m）。"
            "東面の座標で s 8.6〜19.6（#37 の列 1〜7 の中心 s −0.82・19.22 から比で読んだ）。#37 では塔屋の正面のタイルがパラペットの線から上へ続き、"
            "視点（registry：面から 117 m・高さ −7.2 m）から見て 1 段ほどの高さで写るには正面が東面とほぼ同じ面にある必要がある（奥へ r m 引っ込むと高さが "
            "0.72r m ずつ高く読める）ので、東面と同じ面と置いた。奥行きは見えていない（#12 の西からは西のパラペットに隠れる）。幅の約 0.6（6.5 m）と置いた。"
            "航空写真（out/tower75/roof.json）は倒れ込みの読みが 6〜15 m で割れ、位置の確かめに使えない")
    ph_top = PARAPET + 5.67
    q = [world(f15, 8.6, 0.0), world(f15, 19.6, 0.0), world(f15, 19.6, -6.5), world(f15, 8.6, -6.5)]
    P.append(comp("塔屋（屋上の北寄りのタイルの箱）", None, [prism_xy("penthouse", q, (PARAPET - 1.0, ph_top), "tile"),
                                                   {"shape": "cylinder", "name": "pole", "at": world(f15, 15.0, -3.2), "r": 0.06,
                                                    "z": [ph_top, ph_top + 5.0], "material": "metal"}],
                  [E37, W12], m_ph, 1.7, note="上の細い棒は #37 で塔屋の高さの約 0.9（5 m）、#12 でも西のパラペットの上に見える"))
    m_an = ("(b) 携帯の基地局らしい柱のアンテナ：高さは 1 段分ほど（4.4 m、観察票 kansatsu_summary.json の roof）、柱に板のアンテナ。位置は写真の比：")
    for nm, fid, s, d, z0, ev, why in (
            ("アンテナ（南西の角の塊の屋上）", "f3", 19.3, -3.0, sw_top_of(), [W12, W14], "coverage_f3_photo.png で柱が s 19.0〜19.6（遠景 #12 の視点 50 m から、奥 3 m と置くと横のずれ 0.3 m 以下）"),
            ("アンテナ（東の袖の屋上）", "f11", 3.5, -3.0, PARAPET, [E37], "#37 の左（東面の座標で s −9.0 に写る。視点から戻すと東の袖の上）"),
            ("アンテナ（北東の角）", "f15", 22.5, -2.0, PARAPET, [E37], "#37 の右（東面の s 22.5、主部の北の縁の上）"),
            ("アンテナ（B の上）", "f20", 4.0, -1.5, PARAPET, [N1, N62, W12], "#1・#62 で B の上端に 1 本、#12 の北西の角の上")):
        x, y = world(faces[fid], s, d)
        P.append(antenna(nm, x, y, z0, 4.4, ev, m_an + why, 1.3))
    return P


def sw_top_of():
    return 69.65


# =========================================================================================
def requests_list() -> list:
    rq = []

    def req(kind, face, what, where, why, to=None, status="open", by="sekkei", note=None, faces_also=None):
        d = {"kind": kind, "face": face, "what": what, "where": where, "why": why, "status": status, "by": by}
        if to:
            d["to"] = to
        if faces_also:
            d["faces_also"] = faces_also
        if note:
            d["note"] = note
        rq.append(d)

    # 前の係（測量係）の依頼を残す（data/reports/tower75/sokuryo_f3.json・sokuryo_f15.json の requests）
    for r in json.loads((ROOT / REP / "sokuryo_f3.json").read_text(encoding="utf-8"))["requests"]:
        fl = [x.strip() for x in str(r.get("face", "")).split(",") if x.strip()]
        face = fl[0] if fl and fl[0] != "all" else "*"
        req(r["kind"], face, r["what"], r.get("where", ""), r["why"], by="sokuryo", faces_also=fl[1:] or None,
            to="staff" if r["kind"] == "photo" else ("tool" if r["kind"] == "tool" else None))
        rq[-1]["ref"] = "data/reports/tower75/sokuryo_f3.json#requests"
    # 撮影の依頼（観察・数えの依頼と、設計で見えていない寸法をまとめた）
    req("photo", "f3", "西面の南端（南西の角の SWu と主部の南西の角）を、上端が入る遠景で南西から 1 枚と、四隅（北東・北西・南東・南西）を斜め 45° から上端が入る遠景で",
        "本部棟の前の道路の南、西面から 60〜100 歩。四隅は棟から 60〜100 歩離れた所",
        "南西の角の塊 SW の上端 69.65 m は帯のずれから戻した値。四隅の上の部分の引っ込みの奥行き（今は塊に分けていない）と高さを決める"
        "（kansatsu_summary.json・kazoe_f3.json の依頼と同じ）", to="staff", faces_also=["f0", "f4", "f13", "f16"])
    req("photo", "f9", "吹き抜けを遠めの正面から（南の道路の向かい）と、吹き抜けの上の枠・空いた所が見える高い所から 1 枚",
        "南の道路の向かい側。高い所はタワー75 の東西の棟の上の階の窓",
        "ガラスの段と日よけ棚の対応、吹き抜けの奥行き（航空写真から 9 m と置いた）、頂部の枠の奥行き（3 m と置いた）", to="staff")
    req("photo", "f6", "西の袖・東の袖の側面と正面を全段が入るように（南西・南東から 45°）",
        "デッキの上の南西の端、南東の道路の向かい",
        "袖の白帯と開口の段（今は棟の床の高さで仮説）、6F より上の側面の奥行き", to="staff", faces_also=["f7", "f8", "f10", "f11", "f12"])
    req("photo", "f17", "北東の角と北面の東半分（f16・f17・f18）を見上げずに",
        "10 号館と 9 号館の間の通路の東の端、または 10 号館の上の階",
        "f17 は写真に無い（灰色）。B' の段数、f16 の上の部分 NEu の段（北西と対称の仮説）、f19 の引っ込み", to="staff", faces_also=["f16", "f18", "f19"])
    req("photo", "f3", "西のデッキの縁（大階段の上・エレベーター箱の南の腰壁）を道路から正面に 1 枚と、デッキの上から西を見て縁と庇の柱が入る 1 枚",
        "西の道路（大階段の下）と、デッキの北西の角",
        "デッキの出 14 m・庇の出 7.5 m・柱の位置・大階段の幅は登録したカメラの位置と比から置いた（± 30%）", to="staff")
    req("photo", "*", "屋上の塔屋とアンテナを北か西の遠くから 1 枚",
        "北の広場の北の端、または西の道路の先",
        "塔屋の位置と奥行き（東の遠景 #37 と航空写真から置いた）、アンテナの位置", to="staff")
    # 敷地に置く物（棟と棟の間・2 棟に触れる物）。敷地 JSON に置く語彙がまだ無いので進行役へ
    req("tool", "f2", "敷地に置く物：渡り廊下 S3（f2 の 2F から西へ、大階段の上を渡る灰色の金属パネルの箱、四角い柱 2 本ずつ）",
        "data/site/tempaku.json（敷地の部品の欄）",
        "2 棟に触れる（仕分け係の OSM 1304077323）ので棟 JSON に置かない。寸法の根拠：床は 2F の床の高さ（西の白帯 13.70 m）、箱の高さ 1 段分（4.37 m）、"
        "写真 070114062・070134689・061241221・061836019（観察票 S3）", to="tool")
    req("tool", "f3", "敷地に置く物：南西のデッキの橋 S2（道路をまたぐコンクリートの梁の橋と手すり、下の店、脇の三角柱の穴あき金属の箱 2 以上）",
        "data/site/tempaku.json（敷地の部品の欄）",
        "道路をまたいで隣の側（図書館の甲板の通路橋と同じ物の可能性。data/buildings/library.json の requests）につながるので棟に置かない。"
        "床はデッキ階と同じ 7.23 m（棟の parts「西のデッキ」）。写真 070409570・070422004・061708177・061934785・062246708", to="tool")
    req("tool", "f18", "敷地に置く物：北面の屋根付き通路 S8（塔と 10 号館の間、黒い鉄骨の柱と梁の庇、白い雨どい）と北東のルーバーの壁と擁壁 S10",
        "data/site/tempaku.json（敷地の部品の欄）",
        "2 棟（tower75・10 号館）の間の物。庇の高さは 1F の窓の下端ほど、ルーバーの壁は 2 段分ほど（観察票 S8・S10）。写真 060725949.MP・060730259・060804738・"
        "074649138.MP・060548823・060601578", to="tool", faces_also=["f16"])
    req("tool", "f15", "敷地に置く物：東のガラスの渡り廊下 S9（東面の南寄りから 9 号館の方へ、ガラスの屋根と手すり壁、白い丸柱 2 本）",
        "data/site/tempaku.json（敷地の部品の欄）",
        "2 棟に触れる。床は東面の 1F の床ほど、丸柱は 2 段分ほど（観察票 S9）。写真 074445985・060239806・062317561・062527087", to="tool", faces_also=["f14"])
    req("tool", "f20", "敷地に置く物：北の沈んだ広場（B1 の高さ）と、広場から上がる幅の広い大階段・両脇のタイルの擁壁",
        "data/site/tempaku.json（敷地の部品の欄）",
        "広場はタワー75 と周りの棟（12 号館・10 号館の側）の間の地面。広場の床はデッキより 1 段低い（7.23 − 4.37 ＝ 2.86 m、観察票 S7）。"
        "写真 065946407・070003022・070044607・060854125・060904811（棟に付く B1 のガラスの入口とテラスは棟の parts に置いた）", to="tool", faces_also=["f0", "f19"])
    req("tool", "f3", "敷地に置く物：西のデッキと北西のテラスのベンチ・テーブル（テーブル 3 以上・ベンチ 1 以上）、街灯 3 以上、高木（数は区別できない）、西の道路際の植え込み",
        "data/site/tempaku.json（敷地の部品の欄）。ベンチ・街灯・木は CC0 アセット bench・street_lamp・tree_small",
        "棟と棟の間の地面に立つ物（観察票 surroundings）。写真 061708177・061834617・061836019・070327678・060854125・060950084・061918505・061934785", to="tool")
    # 争点
    req("check", "f3", "西のデッキ S1（と庇 S5・大階段 S4・エレベーター箱）を棟 JSON に置いた。デッキは南西で道路をまたぐ橋 S2 に続く。進行役 K-005 の②（通路橋につながる甲板は敷地）に当たるか",
        "parts「西のデッキ」「西の青い鉄骨の庇」「西の大階段」「エレベーター箱」",
        "デッキは西面の入口の床で、西面の前にしか無い（写真で付いているのはタワー75 の壁だけ）。一方で南西の端は道路をまたぐ橋につながり、"
        "図書館の設計で『通路橋につながる甲板』は敷地にされた（data/buildings/library.json の requests）。写真だけでは境目が決まらない")
    req("tool", "*", "recorder photos roof の屋上の高さ：roof.py の roof_height が parapet_top 71.6（棟の z=0 から）を ground_z 42.9 より大きいので標高と読み、"
        "屋上の高さを 28.7 m にする。縁の探索も ±6 m で止まり（dx −0.5, dy −6.0 に張り付く）、設備の自動抽出が 0 個",
        "recorder/roof.py の roof_height（parapet_top の基準の判定）と縁の探索の幅",
        "検収係が同じ考え（足元の辺の勾配の重なり）で ±20 m を走査すると (−1.0〜−2.0, −14.5) が最大（点 29.8、道具の (−0.5, −6) は 8.5）。"
        "倒れ込み率 0.20（71.6 m）。out/tower75/kenshu_roof/roof_aerial.png。棟 JSON は直していない", to="tool", by="kenshu")
    req("check", "*", "航空写真の屋上の中ほどの暗い長方形（写真の上で約 14×7 m、倒れ込み 14.5 m を戻すと足元の x 46〜61・y 9〜22）が何か決まらない："
        "屋上に開いた中庭（吹き抜け）か、黒い屋根材・太陽光の板か、塔屋の影か",
        "gsi:aerial_z18（out/site/aerial_z18.png）。地上の写真には写らない",
        "役割が書けないので置いていない（検収 C の 1）。f9 の吹き抜けとは位置が 13 m 以上離れ、つながりも見えない。職員の確認か、近くの高い所（隣の棟の上の階）から屋上が見える写真で決まる",
        to="staff", by="kenshu")
    req("photo", "*", "屋上（南半分の小さな設備の箱、f19 のガラスの冠の奥行き、塔屋の長さ）が見える写真を、近くの高い所から 1〜2 枚",
        "隣の棟の上の階・渡り廊下の上など、タワー75 の屋上より高い所か同じくらいの所",
        "航空写真では南半分に 1〜3 m の箱が十数個見えるが位置と高さが決まらず置いていない。f19 の冠の後ろに明るい四角 2 つ（各 5×6 m ほど）があり、"
        "冠の奥行き（模型 1.3 m）が足りないかもしれない。塔屋の北に明るい平らな所が続く", to="staff", by="kenshu")
    req("photo", "f8", "袖の側面（f8・f10、f6・f12）の深い開口の段を、正面から数えられる写真（f8・f10 は吹き抜けの前の歩道の端から、f6 は西の道路へ下りる階段から）",
        "南の道路の歩道、西の袖の脇の短い階段",
        "袖の正面の白帯は 13 本・間隔 4.0〜4.2 m で塔の床（4.37 m・12 本）と揃わない（検収 data/reports/tower75/kenshu_summary.json#f7）。"
        "側面の開口は今は塔の床の高さで仮説（same_block）のまま。開口が白帯と揃うか塔の床と揃うかで直す",
        to="staff", by="kenshu", faces_also=["f10", "f6", "f12"])
    req("check", "f7", "西の袖の扉（parts「西の袖の扉（f7）」）が f7（袖の正面）ではなく f8（袖の吹き抜け側の側面）にあるかもしれない",
        "parts「西の袖の扉（f7）」",
        "#121（062307232）・#113（062046145）で扉は吹き抜けの前の低い壁（三角測量で f9 から 1.85 m 前）の上、袖の側面の面に見える。"
        "三角測量で同じ点を 2 枚で指せる写真が無く、位置は直していない", to="staff", by="kenshu", faces_also=["f8"])
    req("photo", "f1", "W の北端 f1 の上の段（11F〜屋上）を西から正面寄りに、上端が入る遠景で 1 枚",
        "西の道路の向かい（f1 の正面）",
        "検収で f1 の写真だけ 12F の上端 −0.20（8 枚）・13F の太い窓の下端 −0.26（10 枚）。f2・f3 の同じ線は ±0.07 で、W の 1 本の格子は動かしていない"
        "（data/reports/tower75/kenshu_summary.json#f1）", to="staff", by="kenshu")
    req("photo", "f15", "東面の上の段（10F〜屋上）を中くらいの距離（40〜60 m）から正面寄りに 1 枚",
        "東の道路の向かいの歩道（遠景 #37 と見上げの間）",
        "検収で 10F〜12F・14F の線が写真で向きが割れる（遠景 055234749 は +0.20〜+0.42、見上げ 5 枚は −0.18〜−0.43）。値は直していない"
        "（data/reports/tower75/kenshu_summary.json#f15）", to="staff", by="kenshu", faces_also=["f14"])
    req("data", "f1", "OSM の足元の西の北端と東の南端が写真より短い：NWb の西向きの 1 列（f1 の北 2.9 m）と SEb・SEu の東向きの 2 列（f14 の南 4.9 m）が足元の外に出るので置いていない",
        "footprint と faces.f1・f14 の窓",
        "縮尺（PLATEAU の高さ）と OSM の両方が未解決（sokuryo_f3.json・sokuryo_f15.json の unresolved）。足元は動かしていない", faces_also=["f14"])
    return rq


def history_list() -> list:
    return [
        {"at": "", "by": "recorder new --from-site", "what": "PLATEAU と 5m 標高から土台を起こした（PLATEAU に塔の足元が無いので OSM の輪郭）"},
        {"at": NOW, "by": BY, "what": "足元の多角形の最後の点（OSM の輪の閉じ点＝始点と同じ点）を外した。形は変わらない。長さ 0 の辺で平面図（recorder draw）が止まったため"},
        {"at": NOW, "by": BY, "what":
            "観察票・数え・測量（data/reports/tower75/）から設計した。西 W（f1・f2・f3）・東 E（f14・f15）は面の写真と正面化で窓の列・段・入口を読み、"
            "北・南の面は数え係の列数と観察票の比（± 20〜30%）、段は仮説（same_block・symmetry・footprint）で置いた"},
        {"at": NOW, "by": BY, "what":
            "W（f1・f2・f3）・E（f14・f15）・N-west（f20・f0）・S-SW（f4・f5）は 1 直線（法線差 3° 以内）だが 1 つの面にまとめなかった。"
            "登録台帳（data/photos/registry.json）の写真が面ごとの座標（f1・f2・f3 それぞれの from が原点）で、まとめると登録と検収の線がずれるため。"
            "代わりに 1 本の格子（W は f3 の座標、E は f15 の座標）を各辺に配り、面に wall_group を書いた"},
        {"at": NOW, "by": BY, "what":
            "列の内訳は数え係を採った（W：主部 7＋SWu 2・下の段 NWb 1＋主部 7＋SWb 2。観察票の『北 1＋主部 6＋南 2』は #3・#101 で北の 2 本目の列が NWb の上端で"
            "切れずに続くので捨てた。写真 070044607・061836019）。NWu の段は数え係の 10（観察票 9。#1・#62 で一番下の段が笠木で半分隠れる、捨てた方の写真 065946407・060854125）"},
        {"at": NOW, "by": BY, "what":
            "東西の 1・2 段目の間の白帯 B1/2 の高さの違い（西 1.25 P＝66.14、東 1.33 P＝65.84）は揃えず、面ごとの値のまま（進行役 K-006）。"
            "東の遠景 #37（055234749）だけは 1.275 P で西に近いが、近くの 3 枚（060316957・074621752・060328137）の読みを採った測量係の値のまま"},
        {"at": NOW, "by": BY, "what":
            "南西の角だけ塊 SW（上端 69.65 m）に分けた。他の四隅の低い塊と上の引っ込んだ部分は、引っ込みの奥行きが測れないので塊に分けず、"
            "低い塊の上端を水切り（ledges）で示した。f19 の引っ込み（土台では外へ出る）も足元は動かしていない"},
        {"at": NOW, "by": BY, "what":
            "渡り廊下 S3・南西の橋 S2・北の屋根付き通路 S8・東のガラスの渡り廊下 S9・北東のルーバーの壁 S10・ベンチ・街灯・木は 2 棟に触れるか棟の間の物なので"
            "棟 JSON に置かず requests（kind tool）で敷地に上げた（進行役 K-005 の②）。西のデッキ・庇・大階段・エレベーター箱は西面にだけ付くので棟に置き、境目を check に上げた"},
        {"at": NOW, "by": BY, "what":
            "置かなかった物：開口の手すり（f6。数が決まらない）、擁壁の丸い照明（1 m 未満）、掲示物（幕・ポスター。規約で外す）、車、"
            "広場から上がる幅の広い大階段（北の沈んだ広場の物で、棟と棟の間の地面。requests の tool）、内装（決定 D-0002 は停止中、K-001）"},
        {"at": NOW, "by": KBY, "what":
            "検収 1 回目（再検収 out/tower75/recheck/20261107_153223、目印係が登録した南の 8 枚を含む 88 枚）。吹き抜け f9 の高さを登録 4 枚の視点で読み直した："
            "ガラスは基壇の笠木の上 3.0 から 7F の床 35.45 まで（設計 7.23〜57.40）、7F〜11F の 5 段がガラスの無い吹き抜け（設計 12F〜13F の 2 段）、"
            "12F〜14F に枠（設計 14F の 1 段）。日よけ棚 7 本の高さも写真の値に。観察票の『頂部 2 段』の読み違い。理由と数は data/reports/tower75/kenshu_summary.json#f9"},
        {"at": NOW, "by": KBY, "what":
            "袖の正面 f7・f11 の白帯の仮説（same_block、塔の床 12 本）を写真の値で確定した：登録 8 枚で 13 本・間隔 4.0〜4.2 m（14F で塔の床と −3.5 m）。"
            "f11 に基壇の中ほどの灰色の帯（2.43〜2.70）を足した。吹き抜けの前の『コンクリートの台』は 2 枚の三角測量で高さ 2.69 m・出 1.85 m の低い壁と分かり直した"},
        {"at": NOW, "by": KBY, "what":
            "西 W：普通の段の窓の上端 +0.10、13F・14F の太い窓の境 −0.17、デッキの階のガラスブロックの欄間の上端 11.75 → 12.0、大階段の踊り場 +0.28。"
            "東 E：シャッターの上端 0.9 → 1.1、1F のガラスブロックの欄間 3・4 を −0.25 m。どれも再検収の写真の縁の中央値（10〜37 枚、同じ向き 78〜100%）"},
        {"at": NOW, "by": KBY, "what":
            "検収 2 回目（再検収 out/tower75/recheck/20261107_160222）：f9 の格子の棚 2・3 を +0.15、f7 の白帯 8〜12 本目を −0.10、"
            "f7・f11 の 2F の灰色の帯の下端を +0.2（帯は 0.2 m ほど）、メーターの箱を 0.11〜1.93 m に。2 回で止め、残りは撮影依頼（kenshu_summary.json）"},
    ]


def main() -> int:
    b = build()
    errs = schema.validate_building(b)
    if errs:
        print("validate のエラー：")
        for e in errs:
            print("  ", e)
    schema.save_building(b, OUT)
    print(f"書いた：{OUT.relative_to(ROOT)}（faces {len(b['faces'])}、blocks {len(b['blocks'])}、parts {len(b['parts'])}、requests {len(b['requests'])}）")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
