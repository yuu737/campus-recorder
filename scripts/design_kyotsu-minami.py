"""共通講義棟南（kyotsu-minami）の設計。実行すると data/buildings/kyotsu-minami.json を出す（JSON を手で直さない）。

    .venv/Scripts/python -I -X utf8 scripts/design_kyotsu-minami.py

入力（係の報告。根拠のパスはここを指す）
    観察票 data/reports/kyotsu-minami/kansatsu_<面>.json・kansatsu_building.json
    数え   data/reports/kyotsu-minami/kazoe_<面>.json
    測量   data/reports/kyotsu-minami/sokuryo_f13.json・sokuryo_f17.json・sokuryo_f19.json
    製図板 out/kyotsu-minami/coverage_<面>_photo.png（1 px = 1/40 m、左端 = 面の from、下端 = 面の前の地面）と coverage.json

座標
    棟の平面は PLATEAU の足元（recorder.skeleton.from_site_cli）。ほぼ直交なので、f13（南翼の南面）の向きを s、
    その内向きの法線を t とする局所の座標で塊を切る（s = 南西の角 P13 から東南東へ、t = 北北東へ）。
    高さ z は棟の z=0（ground_z）からの m。

高さの基準（11/7 設計係の判断。history に書いた）
    同じ床の印（柱型の白い横帯、帯の中の位置）が coverage_f13_photo と coverage_f17_photo では同じ高さ（4.2・8.2・12.3・16.4 m）に
    読めるが、coverage_f19_photo では 1.04 m 低く読める（帯 5 本の上端・下端の差 0.97〜1.10 m）。棟の床は 1 つの高さと置き、
    2 面が合う f13・f17 の高さを採った。f19 の面の写真で読んだ高さには +1.04 m を足して使う（F19_DZ）。
    地面（ground_offset・ground_z）の取り方は変えていない。f19 の足元の取り直しは requests（kind data）に出した。
"""

from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from recorder import schema  # noqa: E402
from recorder.skeleton import from_site_cli  # noqa: E402

BID = "kyotsu-minami"
OUT = ROOT / "data" / "buildings" / f"{BID}.json"
REP = f"data/reports/{BID}"
BY = "設計係 sekkei（claude-opus-5-5）"
AT = "2026-11-07T14:40:00"
KB = "検収係 kenshu（claude-opus-5-5）"
KAT = "2026-11-07T16:30:00"

# recorder hypotheses --drop がここに書き足す（再生成しても外れたまま）
HYPOTHESES_OFF: dict = {}

PPM = 40.0                     # 製図板の px/m（out/kyotsu-minami/coverage.json の px_per_m）
F19_DZ = 1.04                  # f19 の面の写真の高さの補正（上の「高さの基準」）

# ---- 写真 id -------------------------------------------------------------------------------------
P = "PXL_20261105_"
PH = {k: P + k for k in (
    "071033016.MP", "071255844", "071303445", "071309389", "071316984", "071325894", "071334183", "071347810",
    "071400772.MP", "071408553.MP", "071413956", "071424391", "071434753", "071509670", "071517479", "071527944",
    "071529022", "071536516", "071548825", "074837587", "074849844", "074903876", "074913586", "074925492",
    "074939442", "074949973", "075121795", "075128406", "075147867")}


def ph(*keys):
    return [PH[k] for k in keys]


# 製図板（面の写真）に貼られた登録済みの写真（coverage.json の faces[].photos で使われた物）
REG = {
    "f13": ph("071033016.MP", "071255844"),
    "f17": ph("074949973", "074939442", "075128406", "074913586", "074925492", "074903876"),
    "f19": ph("071536516", "071529022", "071527944", "071509670"),
}
PLATEAU = "plateau:bldg_f8e12e3c-913b-4ee1-926c-bec978e8ef65"
OSM_BOX = "osm:way/1304077324"


# ---- 測定値の書き方 ---------------------------------------------------------------------------------
def est(value, tol, evidence, method, unit="m", note="", ref=None):
    d = {"value": round(value, 3) if isinstance(value, float) else value, "unit": unit,
         "tol": None if tol is None else round(tol, 3), "status": "estimated",
         "evidence": list(evidence), "method": method, "note": note}
    if ref:
        d["ref"] = ref
    return d


def meas(value, tol, evidence, method, unit="m", note="", ref=None):
    d = est(value, tol, evidence, method, unit, note, ref)
    d["status"] = "measured"
    return d


def unk(method, unit="m"):
    return {"value": None, "unit": unit, "tol": None, "status": "unknown", "evidence": [], "method": method, "note": ""}


def board(face, value, axis, px_note, extra=""):
    """製図板（coverage_<面>_photo.png）で読んだ値。axis 's'＝横（PLATEAU の辺 ±3% と読み ±4 px）、
    'z'＝高さ（階高の ± 7.7%（sokuryo_f13）と読み ±4 px）、'len'＝長さ（±3% と ±4 px×2）。"""
    if axis == "s":
        tol = 0.03 * abs(value) + 0.1
    elif axis == "len":
        tol = 0.03 * abs(value) + 0.2
    else:
        tol = 0.077 * abs(value) + 0.1
        if face == "f19":
            tol += 0.07          # 高さの基準の補正 F19_DZ の幅（帯 5 本の差 0.97〜1.10）
    m = f"out/{BID}/coverage_{face}_photo.png で {px_note}（1 px = 1/{PPM:g} m）"
    if face == "f19" and axis == "z":
        m += f"。f19 の面の写真の高さは f13・f17 より 1.04 m 低く読めるので +{F19_DZ} m を足した（設計の history）"
    if extra:
        m += "。" + extra
    return est(value, tol, REG[face], m)


def ratio(value, evidence, how, frac=0.25):
    """(b) 面の写真が無い所：比率と写真 1 枚から。tol は値の 25%（20〜30% の決まり）。"""
    return est(value, abs(value) * frac, evidence, "比率から（面の写真なし）：" + how)


# ---- 局所の座標（f13 の向き s、内向き t） -------------------------------------------------------------
SK = from_site_cli(BID)
FP = [tuple(p) for p in SK["footprint"]["polygon"]]
_f13 = next(f for f in SK["faces"] if f["id"] == "f13")
O = tuple(_f13["from"])
_dx, _dy = _f13["to"][0] - O[0], _f13["to"][1] - O[1]
_L = math.hypot(_dx, _dy)
U = (_dx / _L, _dy / _L)
V = (-U[1], U[0])          # 足元は反時計回り → 左の法線が内向き


def xy(s, t):
    return [round(O[0] + s * U[0] + t * V[0], 3), round(O[1] + s * U[1] + t * V[1], 3)]


def st(p):
    x, y = p[0] - O[0], p[1] - O[1]
    return (x * U[0] + y * U[1], x * V[0] + y * V[1])


ST = [st(p) for p in FP]   # 足元の頂点の (s, t)。翼・背骨の境はこの値から


def rect(s0, s1, t0, t1):
    return [xy(s0, t0), xy(s1, t0), xy(s1, t1), xy(s0, t1)]


# 足元の頂点（番号は PLATEAU の多角形の並び。P13 が南西の角）
def vtx(i):
    return ST[i]


S_WEST_S = vtx(13)[0]          # 南翼の西端（f12 の線）
S_WEST_M = vtx(5)[0]           # 中翼の西端（f4 の線）
S_WEST_N = vtx(1)[0]           # 北翼の西端（f0 の線）
S_SPINE = vtx(11)[0]           # 中庭の奥（翼と背骨の境、f10・f2 の線）
T_S = (0.0, vtx(12)[1])                    # 南翼の t の範囲（f13〜f11）
T_M = (vtx(5)[1], vtx(4)[1])               # 中翼（f5〜f3）
T_N = (vtx(1)[1], vtx(0)[1])               # 北翼（f1〜f19）
S_E19 = vtx(23)[0]             # f19 の東端（from）の s
S_E17 = vtx(18)[0]             # f17 の線の s

# ---- 面の写真で読んだ値（px は製図板の px。1 px = 0.025 m） ---------------------------------------------
# 帯（タイルの手すり壁）の上端・下端（f13 の s=25・28・31 の 3 列の断面、赤み−青み > 18 の連なり。f19 は s=12・16・27・31）
F13_BANDS = [(3.55, 5.17), (7.62, 9.2), (11.65, 13.25), (15.7, 17.27), (19.72, 21.3)]
F19_BANDS_RAW = [(2.52, 4.07), (6.57, 8.1), (10.62, 12.17), (14.67, 16.27), (18.75, 20.27)]
F19_BANDS = [(a + F19_DZ, b + F19_DZ) for a, b in F19_BANDS_RAW]
# 段の西端（3・4・5 階）。f13 は s、f19 は f19 の from（東端）からの距離
F13_STEP = {3: 10.4, 4: 17.0, 5: 20.3}               # px 416・680・812
F13_PIER_S = 32.95                                   # 東端の柱型の西の縁 px 1318
F19_STEP = {3: 30.65, 4: 24.2, 5: 21.0}              # px 1226・968・840（f19 の from から）
F19_TOWER = (3.45, 8.05)                             # 北の塔 px 138〜322
F19_WEST_2F = 33.3                                   # 1・2 階の帯の西の端 px 1332

# ---- 共通の値 ---------------------------------------------------------------------------------------
FLOOR_PITCH = est(4.03, 0.31, ph("071255844", "071033016.MP") + [PLATEAU],
                  "測量係の f13 の階高（PLATEAU の辺 37.43 m を物差し、東端の柱型の白い横帯の間隔 265.5 px）。"
                  "f17・f19 にも同じ値を移した（タイルの段数／階が 1% 以内でそろう）",
                  ref=f"{REP}/sokuryo_f13.json#floor_pitch")
# 床の高さ：f17 の柱型の白い横帯の中心（s=28.4・40.5 の断面で 4.07〜4.30・8.10〜8.32・12.12〜12.37・16.40〜16.47）と
#           f13 の東端の柱型の白い横帯（px 868・705・545・380 → 4.20・8.27・12.27・16.40）がそろう。2 階 4.20 から階高で配る
FL2 = 4.2
FL = {n: FL2 + (n - 2) * 4.03 for n in range(1, 7)}   # 1〜5 階と屋上（6）


def fl_value(n):
    return est(round(FL[n], 2), 0.1 + 0.077 * abs(FL[n] - FL2) + 0.1,
               REG["f17"] + REG["f13"],
               f"2 階の床＝柱型の白い横帯の中心（coverage_f17_photo.png の s=28.4・40.5 の断面で 4.07〜4.30 m、coverage_f13_photo.png の "
               f"東端の柱型で 4.20 m）。{n} 階はそこから階高 4.03 m×{n - 2} を足した",
               ref="faces.f17 の白い帯 / floor_pitch")


# ---- 部品の関数（棟ごとの語彙） -------------------------------------------------------------------------
def comp(name, face, prims, status_ev, method, tol, note=""):
    d = {"type": "composite", "name": name, "status": "estimated", "evidence": list(status_ev),
         "tol": round(tol, 3), "method": method, "primitives": prims}
    if face:
        d["face"] = face
        d["along"] = 0.0
    if note:
        d["note"] = note
    return d


def box(s0, s1, d0, d1, z0, z1, mat="concrete", name=None, cut=False):
    p = {"shape": "box", "s": [round(s0, 3), round(s1, 3)], "d": [round(d0, 3), round(d1, 3)], "z": [round(z0, 3), round(z1, 3)],
         "material": mat}
    if name:
        p["name"] = name
    if cut:
        p["cut"] = True
    return p


def wedge(s0, s1, d0, d1, z0, z1, rise, mat="tile", t=None, name=None):
    p = {"shape": "wedge", "s": [round(s0, 3), round(s1, 3)], "d": [round(d0, 3), round(d1, 3)], "z": [round(z0, 3), round(z1, 3)],
         "rise": rise, "material": mat}
    if t is not None:
        p["t"] = t
    if name:
        p["name"] = name
    return p


def glassblock(s0, s1, d_back, z0, z1, name):
    """奥の壁の前のガラスブロックの窓：白っぽい板の前に薄いガラス"""
    return [box(s0, s1, d_back, d_back + 0.025, z0, z1, "white", name + "_gb"),
            box(s0, s1, d_back + 0.025, d_back + 0.04, z0, z1, "glass", name + "_g")]


def recess_band(z0, z1, a0, a1, depth, face, src, why):
    """帯の引っ込み（負の depth）。奥の壁は打ち放しの色。src は (z_bottom, z_top, along0, along1, depth) の測定値"""
    return {"name": why, "z_bottom": src[0], "z_top": src[1], "along0": src[2], "along1": src[3], "depth": src[4],
            "color": "#b4b2ab"}


# ---- 土台から棟を組む ---------------------------------------------------------------------------------
b = copy.deepcopy(SK)
b["name"] = "共通講義棟南"
faces = {f["id"]: f for f in b["faces"]}
FACE_NAMES = {
    "f0": "北翼の西端（西の道路沿い）", "f1": "北翼の南面（中庭 2 の北側）", "f2": "中庭 2 の奥（背骨の西面）",
    "f3": "中翼の北面（中庭 2 の南側）", "f4": "中翼の西端（西の道路沿い）", "f5": "中翼の南面（中庭 1 の北側）",
    "f6": "中庭 1 の奥・出っ張りの北脇", "f7": "中庭 1 の奥・出っ張りの北側面", "f8": "中庭 1 の奥・出っ張りの正面（コア）",
    "f9": "中庭 1 の奥・出っ張りの南側面", "f10": "中庭 1 の奥・出っ張りの南脇", "f11": "南翼の北面（中庭 1 の南側）",
    "f12": "南翼の西端（西の道路沿い）", "f13": "南翼の南面（南の横断歩道側）", "f14": "南東の柱型の東の側面",
    "f15": "背骨の南面", "f16": "背骨の南端の東面", "f17": "背骨の東面（東の庭側）", "f18": "北東の角の北面",
    "f19": "北面（北翼と背骨の北端）"}
for fid, f in faces.items():
    f["name"] = f"{FACE_NAMES[fid]}（{f['name']}）"
    Lm = f["length"]
    Lm["tol"] = math.ceil(max(1.0, 0.03 * Lm["value"]) * 100) / 100      # PLATEAU の辺 ±3%（物差しの許容）
    Lm["method"] = "足元の辺の長さ（PLATEAU、±3% か ±1 m の大きい方）"

TILE = {"name": "タイル（赤茶）", "color": "#9d826b",
        "unit_w": est(0.195, 0.03, REG["f13"] + ph("074949973"),
                      "測量係のタイルの縦目地÷段の比 2.86〜2.92 × 段の周期 0.0674 m（階高 4.03 m ÷ 59.75 段）。目地込みの周期。種類未確認",
                      ref=f"{REP}/sokuryo_f13.json#ratio_check"),
        "unit_h": est(0.0674, 0.006, REG["f13"], "階高 4.03 m ÷ 1 階分の段数 59.75（測量係、071033016）。目地込みの周期",
                      ref=f"{REP}/sokuryo_f13.json#ratio_check"),
        "joint_w": unk("目地の幅は測っていない")}
CONCRETE = {"name": "打ち放しコンクリート", "color": "#b4b2ab"}
for fid in faces:
    faces[fid]["materials"] = [copy.deepcopy(CONCRETE if fid in ("f2", "f6", "f10") else TILE)]
# 色の根拠：coverage_f13_photo.png の 2 階の帯（s 20〜30 m、z 7.8〜8.3 m）の平均 RGB(158,130,108)
b["materials_note"] = "タイルの色は coverage_f13_photo.png の 2 階の帯（s 20〜30・z 7.8〜8.3 m）の平均。打ち放しは写真の明るい所の目安"

b["floor_count"] = meas(5, None, ph("071033016.MP", "071255844"),
                        "数え係：f13 で 1 階＋帯の段 4＝5（2 枚）。f17・f19・f2 でも 5", unit="階",
                        ref=f"{REP}/kazoe_f13.json#floors")
b["floor_pitch"] = FLOOR_PITCH
b["parapet_top"] = board("f13", 21.3, "z", "最上段の帯の上端（s=25・28・31 の断面）を px 184 で読んだ",
                         "公開資料：PLATEAU の高さ 25.9 m（頂部 73.03 m）は東端の柱型の頂部 23.85 m（標高 72.03 m）とも 1.0 m 違う。"
                         "検収：最高点は背骨の南の濃い灰色の塔屋（部品、上端 24.85 m）と読んだ（071033016・航空写真）")
b["levels"] = [{"id": f"F{n}", "name": f"{n} 階の床", "z": fl_value(n)} for n in range(1, 6)] + \
              [{"id": "R", "name": "屋上（5 階の天井の上）", "z": fl_value(6)}]

# ---- 塊（blocks） -------------------------------------------------------------------------------------
blocks = []
low_top = board("f13", 9.2, "z", "2 階の上の帯（3 階のテラスの腰壁）の上端 px 667",
                "f19 の同じ帯は 8.10 + 1.04 = 9.14 m")
blocks.append({"id": "B_低層", "name": "1・2 階（足元いっぱい。3 階のテラスの腰壁の上端まで）", "polygon": [list(p) for p in FP],
               "top_z": low_top,
               "note": "足元は PLATEAU のまま。西端の 1・2 階は足元の線まで（coverage_f13_photo の帯が s=0 まで、coverage_f19_photo の帯が西端の 0.75 m 手前まで）"})

S_IN = S_SPINE + 0.7          # 翼の段の塊は背骨の塊に 0.7 m 食い込ませて足し算で 1 つに


def step_block(bid, name, s0, t, top, note):
    return {"id": bid, "name": name, "polygon": rect(s0, S_IN, t[0], t[1]), "top_z": top, "note": note}


STEP_NOTE = "段の西の縁は面の写真で読んだ位置。翼の幅いっぱい（南北の面の間）に同じ位置で下がると置いた（段の縁は南北の 2 面でしか見えていない）"
for n, top_i in ((3, 2), (4, 3), (5, 4)):
    s0 = F13_STEP[n]
    top = board("f13", F13_BANDS[top_i][1], "z", f"{n} 階の段の上の帯の上端")
    blocks.append(step_block(f"B_南翼_{n}階", f"南翼の {n} 階より上（西端 s={s0}）", s0, T_S, top,
                             f"西の縁：coverage_f13_photo.png の {n} 階の袖壁の西の縁 px {round(s0 * PPM)}（s={s0} m、±{0.03 * s0 + 0.1:.2f}）。" + STEP_NOTE))
for n, top_i in ((3, 2), (4, 3), (5, 4)):
    s0 = S_E19 - F19_STEP[n]
    top = board("f19", F19_BANDS[top_i][1], "z", f"{n} 階の段の上の帯の上端（f19 の高さ {F19_BANDS_RAW[top_i][1]} m）")
    blocks.append(step_block(f"B_北翼_{n}階", f"北翼の {n} 階より上（西端 s={s0:.2f}）", s0, T_N, top,
                             f"西の縁：coverage_f19_photo.png の {n} 階の袖壁の西の縁 px {round(F19_STEP[n] * PPM)}（f19 の東端から {F19_STEP[n]} m）。" + STEP_NOTE))
# 中翼：面の写真が無い。西から（071413956・071347810・071408553）段が 3 つ見え、作りは北翼・南翼と同じ（観察票 B_翼の段）。
# 西端の線（f4）は北翼の西端（f0）と同じ一直線、翼の幅も同じ（20.1 m と 20.2 m）なので、北翼の段を同じ塊の仮説で写す
for n in (3, 4, 5):
    src = next(bk for bk in blocks if bk["id"] == f"B_北翼_{n}階")
    off = (S_E19 - F19_STEP[n]) - S_WEST_N
    s0 = S_WEST_M + off
    top = schema.hypothesis_from(src["top_z"], "same_block", f"B_北翼_{n}階",
                                 f"中翼の {n} 階の段の高さを北翼と同じと置いた（同じ作りの翼。中翼は西から斜めにしか見えていない）。"
                                 f"段の西の縁も北翼の西端からの距離 {off:.2f} m を写した")
    blocks.append(step_block(f"B_中翼_{n}階", f"中翼の {n} 階より上（仮説：北翼と同じ段）", s0, T_M, top,
                             f"仮説（same_block、B_北翼_{n}階）。西の縁は北翼の西端からの距離 {off:.2f} m を写した。"
                             "中翼の段は 071413956・071347810 で 3 段あるのは見えるが、位置は測っていない"))

# 背骨（翼の東の根元から f17 まで）。上端は f13 の最上段の帯が s 26.3〜32.95 の背骨の南面へ続き、f19 の最上段の帯も背骨へ続く
spine_st = [(S_SPINE, 0.0)] + [ST[i] for i in range(14, 24)] + [(S_SPINE, T_N[1]), ST[2], ST[3], (S_SPINE, T_M[0]),
                                                                 ST[6], ST[7], ST[8], ST[9], ST[10], ST[11]]
blocks.append({"id": "B_背骨", "name": "背骨（中庭の奥から東の面 f17 まで）", "polygon": [xy(s, t) for s, t in spine_st],
               "top_z": board("f13", 21.3, "z", "最上段の帯の上端が s 26.3〜32.95 の背骨の南面に続く（px 184）",
                              "f19 の最上段の帯も背骨の側（s 16〜28.9）へ続き 20.27 + 1.04 = 21.31 m。東の塊（f17 側）が背骨の西より高いという観察票の読み"
                              "（航空写真の影）は高さが測れないので塊を分けていない"),
               "note": "中庭の奥の線（s=26.3）より東。翼の段の塊と足し算で 1 つになる"})
# 南東の角の柱型（f13 の東端、屋上より高い）
blocks.append({"id": "B_南東の柱型", "name": "南翼の南東の角の全高のタイルの柱型（S8 d）",
               "polygon": rect(F13_PIER_S, ST[14][0], 0.0, ST[15][1]),
               "top_z": board("f13", 23.85, "z", "東端の柱型の頂部 px 82",
                              "測量係の PLATEAU の高さの検算では頂部 72.05 m（標高）。PLATEAU の頂部 73.03 m と 0.98 m 違う（測量係の ± の内）"),
               "note": f"幅：coverage_f13_photo.png の柱型の西の縁 px 1318（s={F13_PIER_S}）〜面の東端。奥行きは足元の f14 の辺（2.07 m）まで。"
                       "それより北へ続くかは見えていない（071033016 で南の面だけ）"})
# 北の塔（f19 の東寄り、屋上より高い。アンテナが載る）
s_t0, s_t1 = S_E19 - F19_TOWER[1], S_E19 - F19_TOWER[0]
TOWER_D = ratio(4.6, ph("071536516", "071548825"),
                "見えていない。塔の奥行きは正面の幅 4.6 m（coverage_f19_photo.png の px 138〜322）と同じと見た（071548825 で側面が正面と同じくらいに見える）",
                0.3)
blocks.append({"id": "B_北の塔", "name": "北面の東寄りのタイルの塔（S8 b、アンテナ）",
               "polygon": rect(s_t0, s_t1, T_N[1] - TOWER_D["value"], T_N[1]),
               "top_z": board("f19", 22.9 + F19_DZ, "z", "塔の頂部 px 120（f19 の高さ 22.9 m）"),
               "depth": TOWER_D,
               "note": f"幅：coverage_f19_photo.png の px 138〜322（f19 の東端から 3.45〜8.05 m）"})
# 北東の角の塔（f17 の北端）
# 検収（11/7 kenshu）：頂部と南の縁を登録済みの 2 枚の三角測量で直した（設計は 22.7 m・69.6 m）。
#   塔の正面の頂部の北の角を 074903876（3195,195 px）と 074913586（3025,329 px）で指して交点 → s=71.93（面の終点と一致）・d=0.14・z=20.56（±0.13、指す誤差 3 px）。
#   庇の先の北の角（074903876 2946.5,224・074913586 2867.5,221.5）→ s=70.42：庇は塔の南の面に突き当たる＝塔の南の縁は 70.4。
#   設計が捨てた coverage_f17_photo の 20.9 m の方が正しかった（「庇より上に出る」は庇が 19.2 m にあるため。下の庇を参照）
NE_TOWER_S0 = 70.4
t_ne0 = ST[18][1] + NE_TOWER_S0
blocks.append({"id": "B_北東の塔", "name": "東面の北端のタイルの塔（S8 c）",
               "polygon": rect(ST[20][0], ST[19][0], t_ne0, ST[19][1]),
               "top_z": est(20.6, 0.2, ph("074903876", "074913586"),
                            "検収の三角測量：塔の正面の頂部の北の角を登録済みの 074903876（3195,195 px）と 074913586（3025,329 px）で指し、"
                            "2 本の視線の交点 s=71.93・d=0.14・z=20.56（残差 1 px 以下、指す誤差 3 px で ±0.13）。coverage_f17_photo.png の 20.9 m とも合う。"
                            "観察票「庇より 1/3 階ほど上」は庇の上端 19.2 m ＋ 1.36 m で合う",
                            note=f"{REP}/kenshu_PXL_20261105_074903876.json"),
               "note": "幅：南の縁は庇の先の北の角の三角測量 s=70.42（074903876・074913586）、北の縁は面の終点 71.93 m。"
                       "奥行きは足元の北東の角の切り欠き（1.96 m）まで。上端が背骨の塊（21.3 m）より低いので、模型では背骨に埋まる"
                       "（f17 の北端で背骨の上端 21.3 m が写真の空に出る食い違いは争点 out/kyotsu-minami/soten.json）"})
# 中庭 1 の奥のコア（f8）の頂部のタイルの箱は塊にせず部品（composite）にした：塊にすると背骨との足し算が壊れた（11/7、体積の検査で止まる）
# 足元の線と同じ所に面が重なると、Blender の足し算（EXACT UNION）が壊れる（ほぼ同じ面が 2 枚になる。11/7 に 1 回失敗）。
# 低層以外の塊は輪郭を 2 cm 外側へ広げる（形の値ではなく、模型を組むための寄せ。値の ± より十分小さい）
from shapely.geometry import Polygon as _Poly  # noqa: E402

for k, bk in enumerate(blocks):
    if bk["id"] == "B_低層":
        continue
    # 塊ごとに広げる幅を変える（0.01 m × 番号）。同じ幅だと翼の段の塊どうしの面が同じ平面に重なり、中翼の帯の彫り込みで壊れた（11/7）
    g = _Poly(bk["polygon"]).buffer(0.01 * k, join_style=2)
    bk["polygon"] = [[round(x, 3), round(y, 3)] for x, y in list(g.exterior.coords)[:-1]]
    bk["note"] = bk.get("note", "") + "（輪郭は足し算のために塊ごとに 1〜13 cm 外側へ広げた。内側へ寄せると帯の彫り込みの底と低層の上端が同じ面になり、同じ幅だと塊どうしの面が重なって壊れた）"
b["blocks"] = blocks

# ---- 面 -----------------------------------------------------------------------------------------------
REC_D = est(1.74, 0.35, ph("071033016.MP", "071255844"),
            "測量係が 2 枚の柱間隔が同じになるように解いた 2 階の帯の張り出し d＝1.736 m。各階の窓の面は帯の面より同じだけ奥と置いた。"
            "tol は Z の読みと縮尺の 20%", ref=f"{REP}/sokuryo_f13.json#bay_pitch")


def neg(v):
    d = copy.deepcopy(v)
    d["value"] = -d["value"]
    return d


# f13 南翼の南面 ------------------------------------------------------------------------------------------
f = faces["f13"]
f["bays"] = {
    "count": meas(8, None, ph("071033016.MP", "071255844"),
                  "数え係：1 階の柱の間の開口を 2 枚で数え、扉（西から 4 つ目）と西の壁の暗い開口で対応", unit="個",
                  ref=f"{REP}/kazoe_f13.json#bays"),
    "pitch": est(3.30, 0.36, ph("071033016.MP", "071255844") + [PLATEAU],
                 "測量係：窓の面（帯より 1.736 m 奥）の柱間隔。2 枚の正面化から奥行きを解いた", ref=f"{REP}/sokuryo_f13.json#bay_pitch"),
    "margin_to": est(4.37, 0.2, ph("071255844"), "測量係：東端の柱型の正面 4.37 m（071255844 の 2730〜3018 px）",
                     ref=f"{REP}/sokuryo_f13.json#bay_pitch"),
    "margin_from": est(6.66, 0.5, ph("071255844", "071033016.MP"),
                       "面の長さ 37.43 − 8×3.30 − 4.37。西端の打ち放しの壁・角の柱型（数え係の segments）。数え係の 0.7〜1.1 スパンより長い（測量係の問い）",
                       ref=f"{REP}/sokuryo_f13.json#bay_pitch"),
}
f["note"] = "帯（タイルの手すり壁）が面の線、各階の窓（ガラスブロック）は帯より 1.74 m 奥。窓は bays の割りで composite（f13 の窓）に置いた"
SODE = 1.0     # 段の西端の袖壁（タイル）の幅：coverage_f13_photo.png で 3 階 px 416〜460、4 階 680〜720、5 階 812〜848（0.9〜1.1 m）
EXT13 = {1: (0.0, F13_PIER_S), 2: (0.0, F13_PIER_S), 3: (F13_STEP[3] + SODE, F13_PIER_S), 4: (F13_STEP[4] + SODE, F13_PIER_S),
         5: (F13_STEP[5] + SODE, F13_PIER_S)}
bands13 = []
zone13 = {1: (FL[1], F13_BANDS[0][0])}
for n in (2, 3, 4, 5):
    zone13[n] = (F13_BANDS[n - 2][1], F13_BANDS[n - 1][0])
for n, (z0, z1) in zone13.items():
    a0, a1 = EXT13[n]
    zb = fl_value(1) if n == 1 else board("f13", z0, "z", f"{n - 1} 階の上の帯の上端（3 列の断面）")
    bands13.append({"name": f"{n} 階の窓の面の引っ込み",
                    "z_bottom": zb,
                    "z_top": board("f13", z1, "z", f"{n} 階の上の帯の下端（3 列の断面）"),
                    "along0": board("f13", a0, "s", f"{n} 階の袖壁の東の縁 px {round(a0 * PPM)}（段の西の縁＋袖壁 1.0 m）") if a0 else est(0.0, 0.1, REG["f13"], "面の始点"),
                    "along1": board("f13", a1, "s", f"東端の柱型の西の縁 px {round(a1 * PPM)}"),
                    "depth": neg(REC_D), "color": "#b4b2ab"})
f["bands"] = bands13

# f19 北面 ------------------------------------------------------------------------------------------------
f = faces["f19"]
f["bays"] = {}
f["note"] = ("西部（北翼）は f13 と同じ作り（帯の奥に窓）。柱割りは数え係で unknown（木で隠れる）なので窓は置かず、帯の引っ込みだけ。"
             "東部は北の塔と低い塊、前にガラスの箱（S3）")
EXT19 = {1: (F19_TOWER[1], F19_WEST_2F), 2: (F19_TOWER[1], F19_WEST_2F), 3: (F19_TOWER[1], F19_STEP[3] - 0.65),
         4: (F19_TOWER[1], F19_STEP[4] - 0.7), 5: (F19_TOWER[1], F19_STEP[5] - 0.8)}
zone19 = {1: (FL[1], F19_BANDS[0][0])}
for n in (2, 3, 4, 5):
    zone19[n] = (F19_BANDS[n - 2][1], F19_BANDS[n - 1][0])
bands19 = []
for n, (z0, z1) in zone19.items():
    a0, a1 = EXT19[n]
    zb = fl_value(1) if n == 1 else board("f19", z0, "z", f"{n - 1} 階の上の帯の上端（f19 の高さ {z0 - F19_DZ:.2f} m）")
    bands19.append({"name": f"{n} 階の窓の面の引っ込み",
                    "z_bottom": zb,
                    "z_top": board("f19", z1, "z", f"{n} 階の上の帯の下端（f19 の高さ {z1 - F19_DZ:.2f} m）"),
                    "along0": board("f19", a0, "s", f"北の塔の西の縁 px {round(a0 * PPM)}"),
                    "along1": board("f19", a1, "s", f"{n} 階の西の端（袖壁の内側）px {round(a1 * PPM)}"),
                    "depth": copy.deepcopy(neg(REC_D)) | {"method": REC_D["method"] + "。f19 も同じ作り（観察票）なので同じ奥行きと置いた"},
                    "color": "#b4b2ab"})
f["bands"] = bands19

# 中庭に面する翼の面（f1・f3・f5・f11）：斜めにしか見えない。帯の高さと段の範囲を同じ塊の仮説で写す ------------------------
COURT = {  # 面: (写す元の面, 帯の並び, 面の along の取り方, 翼の西端, 段)
    "f1": ("f19", bands19, lambda s: s - S_WEST_N, {n: S_E19 - F19_STEP[n] for n in (3, 4, 5)}),
    "f3": ("f19", bands19, lambda s: ST[3][0] - s, {n: S_WEST_M + (S_E19 - F19_STEP[n]) - S_WEST_N for n in (3, 4, 5)}),
    "f5": ("f19", bands19, lambda s: s - S_WEST_M, {n: S_WEST_M + (S_E19 - F19_STEP[n]) - S_WEST_N for n in (3, 4, 5)}),
    "f11": ("f13", bands13, lambda s: ST[11][0] - s, {n: F13_STEP[n] for n in (3, 4, 5)}),
}
# 検収（11/7 kenshu）：写真が新しく登録された面の仮説を確定する（手順書「仮説の置き方と戻し方」）。
#   f5：目印係が 071334183 を登録（点＋線 10 点、11.45 px）。recheck 20261107_151014 で f5 の帯の高さの線 6 本が +0.14・−0.13・+0.35・+0.18・−0.12・+0.10
#   （平均 +0.09、偏りなし。斜め 70° 前後の見通しで ±0.35 は縁の取り違えの幅）。段 3 つと帯の奥の窓の作りが北翼（f19）と同じに見える → 確定（値はそのまま、tol を広げる）
CONFIRMED = {
    "f5": (ph("071334183"), "recheck 20261107_151014：f5 の帯の高さの線 6 本 平均 +0.09 m・最大 0.35 m、偏りなし。段 3 つと帯の奥の窓が見える"),
}
for fid, (src_face, src_bands, along_of, steps) in COURT.items():
    f = faces[fid]
    L = f["length"]["value"]
    out = []
    for n, sb in enumerate(src_bands, start=1):
        s_west = {1: None, 2: None}.get(n, steps.get(n))
        dec = along_of(0.0) > along_of(1.0)
        if s_west is None:
            a0, a1 = (0.3, L) if dec else (0.0, L - 0.3)      # 中庭の奥の入隅は 0.3 m 手前で止める（入隅の柱の分。見えていない）
        else:
            a = along_of(s_west + SODE)          # 段の西端の袖壁の分だけ内側から
            a0, a1 = (0.3, a) if dec else (a, L - 0.3)
        why = (f"{FACE_NAMES[fid]}は中庭の口から斜めにしか見えない（観察票：{src_face} と同じ作り）。{n} 階の帯の高さと奥行きを {src_face} から写し、"
               f"範囲は同じ翼の段の西の縁（s={s_west if s_west is not None else '西端'}）＋袖壁 1.0 m から中庭の奥まで")
        if fid in CONFIRMED:
            ev, chk = CONFIRMED[fid]
            def conf(v, tol, what, _sb=None):
                return est(v, tol, ev + list((_sb or {}).get("evidence", [])),
                           f"検収で仮説を確定（{chk}）。{what}", note=f"{REP}/kenshu_summary.json")
            out.append({"name": f"{n} 階の窓の面の引っ込み",
                        "z_bottom": conf(sb["z_bottom"]["value"], max(0.4, sb["z_bottom"]["tol"] or 0), f"高さは {src_face} の {n} 階の帯の値（同じ作り）", sb["z_bottom"]),
                        "z_top": conf(sb["z_top"]["value"], max(0.4, sb["z_top"]["tol"] or 0), f"高さは {src_face} の {n} 階の帯の値（同じ作り）", sb["z_top"]),
                        "along0": conf(round(a0, 2), 1.0, "範囲は同じ翼の段の西の縁＋袖壁 1.0 m から中庭の奥まで（段の位置は北翼から写した値。"
                                                        "071334183 で段が 3 つ見えるが、段の縁は線が取れず検算していない。tol 1.0 m）"),
                        "along1": conf(round(a1, 2), 1.0, "同じ（段の位置は北翼から写した値、tol 1.0 m）"),
                        "depth": conf(sb["depth"]["value"], 0.5, f"帯の奥の窓の面の奥行きは {src_face} と同じ（071334183 で帯の奥に窓の面が見える。奥行きは測っていない）", sb["depth"]),
                        "color": "#b4b2ab"})
            continue
        out.append({"name": f"{n} 階の窓の面の引っ込み（仮説）",
                    "z_bottom": schema.hypothesis_from(sb["z_bottom"], "same_block", src_face, why),
                    "z_top": schema.hypothesis_from(sb["z_top"], "same_block", src_face, why),
                    "along0": schema.hypothesis(round(a0, 2), "same_block", src_face, why + "（along は段の位置から計算）"),
                    "along1": schema.hypothesis(round(a1, 2), "same_block", src_face, why + "（along は段の位置から計算）"),
                    "depth": schema.hypothesis_from(sb["depth"], "same_block", src_face, why), "color": "#b4b2ab"})
    f["bands"] = out
    f["note"] = f"中庭 {'2' if fid in ('f1', 'f3') else '1'} の口から斜めに見える（数え係：5 階 estimated、列 unknown）。帯は {src_face} からの仮説"
    if fid in CONFIRMED:
        f["note"] = (f"中庭 1 の口から斜めに見える。帯は {src_face} と同じ作りと置いた仮説を、検収が目印で登録された 071334183 の再検収で確定した"
                     f"（{CONFIRMED[fid][1]}）")

# 西端（f0・f4・f12）：台形の階段の壁の奥の建物の壁は無地。上端の笠木と手すりだけ見える ------------------------------------
for fid, photos in (("f0", ph("071424391", "071434753", "071509670")), ("f4", ph("071347810", "071400772.MP", "071408553.MP")),
                    ("f12", ph("071309389", "071303445", "071316984"))):
    f = faces[fid]
    f["bays"] = {}
    f["note"] = ("無地（観察票 kansatsu_%s.json：前面は窓も柱型も無いタイルの壁）。手前（道路側）に台形の階段の壁が立つ（composite）。"
                 "足元の線を手前の階段の壁か奥の建物の壁かは写真で決められない（観察票の問い 2）。f13・f19 の面の写真で 1・2 階の帯が西端（の手前 0.75 m）まで"
                 "届くので、足元の線＝奥の建物の壁と置き、階段の壁はその外に置いた" % fid)
    f["bands"] = [{"name": "3 階のテラスの腰壁の上端の笠木", "color": "#55524f",
                   "z_bottom": ratio(9.0, photos, "腰壁の上端（低層の上端 9.2 m、f13 の面の写真）から笠木の厚さ 0.2 m（タイル 3 段分に見える）"),
                   "z_top": est(9.2, 0.81, REG["f13"], "低層の上端と同じ（coverage_f13_photo.png の 2 階の上の帯の上端）", ref="blocks.B_低層.top_z"),
                   "depth": ratio(0.05, photos, "笠木の出（帯より少し出る、観察票）")}]

# f2（中庭 2 の奥）：各階に横長の窓 1 つ -----------------------------------------------------------------------
f = faces["f2"]
L2 = f["length"]["value"]
W2 = ratio(round(0.85 * L2, 2), ph("071413956"), "窓の幅は面の幅の 0.85（071413956、正面寄り。打ち放しの枠の内側）")
f["bays"] = {"count": meas(1, None, ph("071413956", "071424391"), "数え係：各階に横長の窓 1 つ（2 枚）", unit="個",
                           ref=f"{REP}/kazoe_f2.json#bays"),
             "pitch": est(L2, 1.0, [PLATEAU], "1 列なので面の長さ（PLATEAU の辺）", ref="faces.f2.length"),
             "margin_from": est(0.0, 0.1, [PLATEAU], "1 列で面いっぱい"), "margin_to": est(0.0, 0.1, [PLATEAU], "1 列で面いっぱい")}
f["windows"] = []
for n in range(1, 6):
    f["windows"].append({
        "name": f"{n} 階の横長の窓",
        "z_bottom": ratio(round(FL[n] + 0.95, 2), ph("071413956"), f"窓の下端は {n} 階の床＋0.95 m（窓の下のガラスブロックの帯の上。床の高さは levels）"),
        "z_top": ratio(round(FL[n] + 0.95 + 0.55 * 4.03, 2), ph("071413956"), "窓の高さは階高の 0.55（観察票「階高の半分強」、071413956）"),
        "width": copy.deepcopy(W2),
        "spandrel": {"height": ratio(0.55, ph("071413956"), "ガラスブロックの帯は窓の高さの 1/4（観察票）"),
                     "depth": ratio(0.05, ph("071413956"), "窓と同じ面（奥まり）"), "material": "white", "in_recess": True}})
f["note"] = "打ち放しの枠の面。各階に横長の窓 1 つと下のガラスブロックの帯（数え係 5 階 measured）"

# f6・f10（中庭 1 の奥・出っ張りの両脇）：各階に窓 1 つ＋ガラスブロックの腰 ---------------------------------------------------------
for fid in ("f6", "f10"):
    f = faces[fid]
    Lf = f["length"]["value"]
    f["bays"] = {"count": est(1, None, ph("071334183", "071347810"),
                              "数え係：2 枚で一致するが、写真の faces にこの面が無い（根拠の門）ので estimated", unit="個",
                              ref=f"{REP}/kazoe_{fid}.json#bays"),
                 "pitch": est(Lf, 1.0, [PLATEAU], "1 列なので面の長さ"),
                 "margin_from": est(0.0, 0.1, [PLATEAU], "1 列で面いっぱい"), "margin_to": est(0.0, 0.1, [PLATEAU], "1 列で面いっぱい")}
    f["windows"] = []
    for n in range(1, 6):
        f["windows"].append({
            "name": f"{n} 階の窓",
            "z_bottom": ratio(round(FL[n] + 0.95, 2), ph("071334183"), f"{n} 階の床＋0.95 m（f2 と同じ打ち放しの面の作りと見た）"),
            "z_top": ratio(round(FL[n] + 0.95 + 0.55 * 4.03, 2), ph("071334183"), "窓の高さは階高の 0.55（071334183 で f2 と同じに見える）"),
            "width": ratio(round(0.55 * Lf, 2), ph("071334183"), "窓の幅は面の幅の半分強 0.55（観察票、071334183 正面寄り）"),
            "spandrel": {"height": ratio(0.55, ph("071334183"), "ガラスブロックの腰"), "depth": ratio(0.05, ph("071334183"), "窓と同じ面"),
                         "material": "white", "in_recess": True}})
    f["note"] = "打ち放しの壁に各階 1 つの窓とガラスブロックの腰（数え係 estimated。写真の faces にこの面が無い）"

# f8（中庭 1 の奥のコアの正面）：全高のガラスの筋＋両縁のベージュのパネル -----------------------------------------------------
f = faces["f8"]
L8 = f["length"]["value"]
PAN = round(L8 / 6, 2)     # ガラスの 1/4 の幅のパネルが両縁：ガラス w、パネル w/4 ×2 → w = L/1.5
f["glazing"] = [{"name": "全高のガラスの筋",
                 "along0": ratio(PAN, ph("071334183"), "両縁のベージュのパネルはガラスの 1/4 の幅（観察票）。面の長さ÷6"),
                 "along1": ratio(round(L8 - PAN, 2), ph("071334183"), "同じ"),
                 "z_bottom": ratio(1.0, ph("071334183", "071347810"), "1 階の塀の上から見える下端。1 階の床＋1 m（塀に隠れて下は見えない）"),
                 "z_top": ratio(round(FL[6] - 0.3, 2), ph("071334183", "071347810"), "上端は頂部のタイルの箱の下（屋上の高さ − 0.3）"),
                 "inset": ratio(0.3, ph("071334183"), "ガラスの面の引っ込み（浅く見える）")}]
F8_PANELS = [(0.0, PAN), (round(L8 - PAN, 2), L8)]
f["note"] = "全高のガラスの筋（縦の桟 3 枚・横の桟 6 段前後）と両縁のベージュのパネル、頂部のタイルの箱（部品「f8 の頂部のタイルの箱」）"

# f17 東面：柱型・ガラス帯・5 階のガラスブロック・庇・柱廊は composite で -------------------------------------------------------
f = faces["f17"]
f["bays"] = {}
f["note"] = ("区画ごとに作りが違う（南から：未撮影 0〜18 m、ガラス帯・南 18〜27.4、柱型・中 27.4〜44.5（柱型 6・窓 5）、"
             "ガラス帯・北 44.5〜54.8、柱型・北 54.8〜69.5（柱型 5）、北端の塔 69.6〜71.93）。面の線は柱型の前面と置いた。"
             "柱割りが区画ごとなので bays は書かず、柱型・窓・柱廊は composite に置いた")
WB = [(4.07, 4.30), (8.10, 8.32), (12.12, 12.37), (16.40, 16.47)]   # 白い横帯（s=28.4・40.5 の断面）
f["bands"] = []
for (z0, z1), sec in zip(WB, ("2 階", "3 階", "4 階", "5 階")):
    for a0, a1, nm in ((27.4, 44.5, "柱型・中"), (54.8, 69.5, "柱型・北")):
        f["bands"].append({"name": f"{sec}の床の白い横帯（{nm}）", "color": "#d8d6cf",
                           "z_bottom": board("f17", z0, "z", f"白い横帯の下端（s=28.4・40.5 の断面）"),
                           "z_top": board("f17", z1, "z", f"白い横帯の上端（同）"),
                           "along0": board("f17", a0, "s", f"{nm}の南の縁 px {round(a0 * PPM)}"),
                           "along1": board("f17", a1, "s", f"{nm}の北の縁 px {round(a1 * PPM)}"),
                           "depth": ratio(0.03, REG["f17"], "柱型の面とほぼ同じ（目地の帯）")})
f["glazing"] = []
for a0, a1, nm in ((18.0, 27.4, "ガラス帯・南"), (44.5, 54.8, "ガラス帯・北")):
    for n in (2, 3, 4):
        f["glazing"].append({"name": f"{nm}の {n} 階の連続窓",
                             "along0": board("f17", a0, "s", f"{nm}の南の縁 px {round(a0 * PPM)}"),
                             "along1": board("f17", a1, "s", f"{nm}の北の縁 px {round(a1 * PPM)}"),
                             "z_bottom": board("f17", round(WB[n - 2][1] + 0.3, 2), "z", f"{n} 階の床の白い帯の上 0.3 m（腰のガラスブロックの下端）"),
                             "z_top": board("f17", round(WB[n - 1][0] - 0.4, 2), "z", f"{n + 1} 階の床の白い帯の下 0.4 m（床の板の厚さ）"),
                             "inset": ratio(1.2, ph("074903876", "074939442"), "連続窓は柱型の面より奥（074903876 で床の板の出が柱型の幅の 0.6 倍）")})
f["glazing"].append({"name": "5 階のガラスブロックの列（奥に下がる）",
                     "along0": board("f17", 18.0, "s", "5 階のガラスブロックの列の南の端（未撮影の境）"),
                     "along1": board("f17", 69.5, "s", "北端の塔の南の縁 px 2780"),
                     "z_bottom": board("f17", 16.75, "z", "5 階のガラスブロックの下端 px 366"),
                     "z_top": board("f17", 18.3, "z", "5 階のガラスブロックの上端 px 304"),
                     "inset": ratio(1.0, ph("074903876", "074837587"), "ガラスブロックの列は柱型の面より奥（074837587 で柱型の幅の 1/2）")})

# ---- 部品 ---------------------------------------------------------------------------------------------
parts = []

# f13 の窓（ガラスブロック）と扉：bays の割りの中心に。窓の面は帯より 1.74 m 奥
pitch13, m13 = 3.30, 6.66
WIN13 = {1: [1, 2, 3, 4, 5, 6, 7, 8], 2: [1, 2, 3, 4, 5, 6, 7, 8], 3: [4, 5, 6, 7, 8], 4: [5, 6, 7, 8], 5: [6, 7, 8]}
prims = []
dback = -1.74
for n, bays_ in WIN13.items():
    z0 = 0.78 if n == 1 else zone13[n][0]
    z1 = 3.15 if n == 1 else zone13[n][0] + 1.85
    for i in bays_:
        c = m13 + (i - 0.5) * pitch13
        if n == 1 and i == 4:
            prims += [box(c - 0.75, c + 0.75, dback, dback + 0.03, 0.0, 2.6, "interior", f"door_1f"),
                      box(c - 0.75, c + 0.75, dback + 0.03, dback + 0.045, 0.0, 2.6, "glass", "door_1f_g")]
            continue
        prims += glassblock(c - 0.75, c + 0.75, dback, z0, z1, f"w{n}_{i}")
parts.append(comp("f13 の窓（ガラスブロック）と 1 階の扉", "f13", prims, REG["f13"] + ph("071255844"),
                  "窓の中心は bays の割り（margin_from 6.66 m・柱間隔 3.30 m、測量係）。どの段にどの列の窓があるかは数え係の exceptions"
                  "（5 階は東から 3、4 階は 4、3 階は 5、2 階・1 階は 8、1 階の西から 4 列目は扉）。窓の幅 1.5 m は coverage_f13_photo.png の 2 階の窓"
                  "（60 px）、高さは 2 階の窓（帯の上端から 74 px＝1.85 m）を各階に、1 階は px 910〜1005（0.78〜3.15 m）。窓の面の奥行き 1.74 m は測量係",
                  0.3, note=f"ref: faces.f13.bays, {REP}/kazoe_f13.json#exceptions, {REP}/sokuryo_f13.json#bay_pitch"))

# f13・f19 の 1 階の前のタイルの低い壁（S7）
parts.append(comp("f13 の 1 階の前のタイルの低い壁（S7）", "f13",
                  [box(0.0, 14.6, -0.25, 0.0, -0.71, 1.3, "tile", "west"), box(17.25, F13_PIER_S, -0.25, 0.0, -0.71, 1.3, "tile", "east")],
                  REG["f13"], "coverage_f13_photo.png で上端 px 984（1.30 m）、下端は面の前の地面（−0.71 m、5m 標高）。切れ目（段）は px 584〜690"
                  "（14.6〜17.25 m）。西の端は 071255844 の貼り込みで s=0 近くまで。厚さ 0.25 m は比率（タイル 4 枚分に見える、071255844）", 0.25))
parts.append(comp("f19 の 1 階の前のタイルの低い壁（S7）", "f19",
                  [box(F19_TOWER[1] + 3.5, F19_WEST_2F, -0.25, 0.0, -1.03, -0.68 + F19_DZ, "tile")],
                  REG["f19"], "coverage_f19_photo.png で上端 px 1063（f19 の高さ −0.68 m に +1.04）、下端は面の前の地面（−1.03 m、5m 標高）。"
                  "東の端はガラスの箱のピロティの西（f19 の東端から 11.55 m、木で縁は見えない）〜西の 1・2 階の端", 0.3))

# 段のテラス・帯の上の手すり（f13・f19）
for fid, bandz, ext, ev in (("f13", F13_BANDS, {1: (0.0, F13_PIER_S), 2: (0.0, F13_PIER_S), 3: (F13_STEP[3], F13_PIER_S),
                                                 4: (F13_STEP[4], F13_PIER_S), 5: (F13_STEP[5], F13_PIER_S)}, REG["f13"]),
                            ("f19", F19_BANDS, {1: (F19_TOWER[1], F19_WEST_2F), 2: (F19_TOWER[1], F19_WEST_2F),
                                                3: (F19_TOWER[1], F19_STEP[3]), 4: (F19_TOWER[1], F19_STEP[4]),
                                                5: (F19_TOWER[1], F19_STEP[5])}, REG["f19"])):
    for k, (zb, zt) in enumerate(bandz, start=1):
        a0, a1 = ext[k]
        parts.append({"type": "rail", "name": f"{fid} の {k + 1} 階の帯の上の手すり" if k < 5 else f"{fid} の屋上の帯の上の手すり",
                      "face": fid,
                      "along0": board(fid, a0, "s", f"帯の端 px {round(a0 * PPM)}") if a0 else est(0.0, 0.1, ev, "帯の端＝面の始点"),
                      "along1": board(fid, a1, "s", f"帯の端 px {round(a1 * PPM)}"),
                      "z": board(fid, zt, "z", "帯の上端（3 列の断面）"),
                      "height": est(0.3, 0.1, ev, f"coverage_{fid}_photo.png で帯の上端から手すりの上端まで 8〜12 px")})

# 西端の台形の階段の壁・段の箱・上の箱・外階段（f0・f4・f12）
STAIR_W = ratio(1.2, ph("071517479", "071255844"), "階段の幅は端の低い箱の幅の 2/3（071517479 の北西の角）", 0.3)


def west_end(fid, photos, stair_dir, ground, method_extra, up_ends=("from", "to")):
    """台形の階段の壁（道路側）と両端の段の箱・上の箱・外階段。d＞0 が道路側（足元の線の外）。"""
    L = faces[fid]["length"]["value"]
    w = STAIR_W["value"]
    d0, d1 = w, w + 0.3                       # 壁は階段の外
    top = 9.2                                  # 平らな上端＝3 階のテラスの腰壁の上端
    end_h = 2.0                                # 端の低い箱の高さ（1 階の階高の半分）
    flat = 0.2 * L                             # 平らな上端の長さ（071309389 で全長の 0.2）
    slope = (L - flat) / 2
    zb = ground
    pr = [box(0.0, L, d0, d1, zb, zb + end_h, "tile", "wall_base"),
          wedge(0.0, slope, d0, d1, zb + end_h, top, "+s", name="wall_slope_from"),
          box(slope, slope + flat, d0, d1, zb + end_h, top, "tile", "wall_flat"),
          wedge(slope + flat, L, d0, d1, zb + end_h, top, "-s", name="wall_slope_to"),
          box(slope, slope + flat, d0 - 0.05, d1 + 0.05, top, top + 0.15, "coping", "wall_coping"),
          box(-0.0, 2.0, 0.0, d1, zb, zb + end_h, "concrete", "box_low_from"),
          box(L - 2.0, L, 0.0, d1, zb, zb + end_h, "concrete", "box_low_to"),
          ]
    if "from" in up_ends:
        pr.append(box(0.0, 2.5, 0.0, d1, FL[2], FL[2] + 4.5, "concrete", "box_up_from"))
    if "to" in up_ends:
        pr.append(box(L - 2.5, L, 0.0, d1, FL[2], FL[2] + 4.5, "concrete", "box_up_to"))
    if stair_dir == "+s":        # from 側の箱から to へ上がる
        pr.append(wedge(2.0, slope + 2.0, 0.05, w - 0.05, zb + end_h - 0.3, top - 1.1, "+s", "metal", t=0.25, name="stair"))
    elif stair_dir == "-s":
        pr.append(wedge(L - slope - 2.0, L - 2.0, 0.05, w - 0.05, zb + end_h - 0.3, top - 1.1, "-s", "metal", t=0.25, name="stair"))
    return comp(f"{fid} の台形の階段の壁と段の箱・上の箱" + ("・外階段" if stair_dir else ""), fid, pr, photos,
                "比率から（西端の面は面の写真で使える所が無い：coverage の斜め 100%）。平らな上端は奥の 3 階のテラスの腰壁と同じ高さ（観察票、9.2 m は f13 の面の写真）、"
                "平らな所は全長の 0.2・両端の斜めは同じ傾き（071309389 の正面寄り）、端の低い箱は 1 階の階高の半分（2.0 m）、上の箱は 2 階の床から"
                "1 階分強（4.5 m）、階段の幅 1.2 m（071517479）、壁の足元は面の前の地面（5m 標高、coverage.json の ground_z）。" + method_extra,
                0.3 * 4.0, note="寸法は全部 estimated（tol は値の 25〜30%。全体の tol は位置の幅 1.2 m）")


# 西の道路の高さ（検収 11/7 kenshu）：5m 標高の帯（f12 1.77・f4 1.44）は道路の西の擁壁の上の高い地面と混ざって高すぎる。
#   目印で登録した 071303445（f12）と 071334183（f5）は、5m 標高のままだとカメラが地面の下（−0.79 m・−4.36 m）になり、重ね画像に建物が写らない。
#   登録の視点から面の外 1.5 m（階段の壁の外の面）に z の線を写すと、壁の足元は f12 で z 0.6（北）〜0.0（南の角）、f4 で z −0.4 前後に乗る
#   （071303445：z=0.5 の線が足元の 0〜35 px 上、67 px/m。071334183：z=−0.5 の線が足元にほぼ重なる）。カメラは足元から 1.7・1.6 m で手持ちと合う。
#   目印の登録は点が少ない（6・10 点、6.3・11.5 px）ので tol 0.6 m。f0 は確かな登録が無いので 5m 標高のまま（requests）
WEST_GROUND = {
    "f12": est(0.3, 0.6, ph("071303445"),
               "検収：目印で登録した 071303445 の視点から、f12 の外 1.5 m の面に z の線を写して階段の壁の足元を読んだ（北 0.6・中 0.4・南の角 0.0、平均 0.3）。"
               "5m 標高の帯 1.77 m は西の擁壁の上の高い地面と混ざる（カメラが地面の下 0.79 m になっていた）", note=f"{REP}/kenshu_PXL_20261105_071303445.json"),
    "f4": est(-0.3, 0.6, ph("071334183"),
              "検収：目印で登録した 071334183 の視点から、f4 の外 1.5 m の面に z の線を写して段の箱と階段の壁の足元を読んだ（−0.4 前後）。"
              "5m 標高の帯 1.44 m はカメラが地面の下 4.36 m になる高さで、道路の値ではない", note=f"{REP}/kenshu_PXL_20261105_071334183.json"),
}
for _fid, _g in WEST_GROUND.items():
    faces[_fid]["ground_offset"] = copy.deepcopy(_g)
parts.append(west_end("f12", ph("071309389", "071303445", "071316984", "071255844", "071325894"), "-s", WEST_GROUND["f12"]["value"],
                      "外階段は南端の箱から北へ上がる（071255844）。壁の足元は検収が直した道路の高さ（faces.f12.ground_offset、設計は 5m 標高の 1.77 m）"))
parts.append(west_end("f4", ph("071347810", "071400772.MP", "071408553.MP", "071325894"), None, WEST_GROUND["f4"]["value"],
                      "中翼の外階段の向きは見えていない（置かない）。壁の足元は検収が直した道路の高さ（faces.f4.ground_offset、設計は 5m 標高の 1.44 m）"))
parts.append(west_end("f0", ph("071424391", "071434753", "071509670", "071517479", "071413956"), "+s", 1.96,
                      "外階段は北端の箱から南へ上がる（071517479・071509670）。上の階の箱は南端（中庭 2 の口の上）の 1 つだけ（観察票）", up_ends=("to",)))
# 西端の縦の配管（雨水）：両端の上の箱の脇から地面まで（観察票「2 本以上」）
for fid, g, photos in (("f12", WEST_GROUND["f12"]["value"], ph("071309389", "071325894")), ("f4", WEST_GROUND["f4"]["value"], ph("071347810", "071408553.MP")),
                       ("f0", 1.96, ph("071424391", "071413956"))):
    Lw = faces[fid]["length"]["value"]
    for nm, a in (("from", 2.6), ("to", Lw - 2.7)):
        parts.append({"type": "pipes", "name": f"{fid} の縦の配管（{nm} の端の箱の脇）", "face": fid,
                      "along": ratio(round(a, 2), photos, "上の箱（幅 2.5 m）のすぐ脇（071309389・071413956 で箱の縁に沿う）", 0.2),
                      "count": est(1, None, photos, "端ごとに 1 本（観察票「2 本以上」）", unit="本"),
                      "radius": ratio(0.08, photos, "太さは手すりの柱の 2 倍前後"),
                      "z0": (est(g, 0.6, WEST_GROUND[fid]["evidence"], "面の前の道路の高さ（検収が直した faces.%s.ground_offset）" % fid,
                                 ref=f"faces.{fid}.ground_offset") if fid in WEST_GROUND
                             else est(g, 1.0, ["gsi:dem5a"], "面の前の地面（5m 標高、coverage.json の ground_z）")),
                      "z1": ratio(round(FL[2] + 4.5, 2), photos, "上の箱の上端から（観察票「上の箱から地面まで」）")})

# 中庭の口の塀（S2）：2 枚が前後にずれる。中庭 2 は f0 の線の延長、中庭 1 は f4 の線の延長
def court_wall(name, face_from, along_a, along_b, photos, why, g=1.7, g_why="西の道路の高さ（f0・f4・f12 の面の前の地面 1.44〜1.96 m の中ほど 1.7 m）"):
    # face_from の面の延長の上に置く（その面の座標で along がマイナス＝面の始点より手前）
    w_h = 2.7
    pr = [box(along_a[0], along_a[1], -0.3, 0.0, g, g + w_h, "tile", "front"),
          box(along_b[0], along_b[1], -1.8, -1.5, g, g + w_h, "tile", "back"),
          box(along_a[0], along_a[1], -0.33, 0.03, g + w_h, g + w_h + 0.1, "coping", "front_cap"),
          box(along_b[0], along_b[1], -1.83, -1.47, g + w_h, g + w_h + 0.1, "coping", "back_cap")]
    return comp(name, face_from, pr, photos,
                "比率から：高さは 1 階の階高の 2/3（2.7 m、観察票）、足元は" + g_why + "、"
                "口の幅の 6 割を手前の 1 枚、残りと重なりを 1.5 m 奥の 1 枚（" + why + "）。穴（丸・四角）は塀の高さの 1/3 で小さく、置いていない",
                0.8)


L0 = faces["f0"]["length"]["value"]
LMOUTH2 = ST[1][1] - ST[4][1]        # 中庭 2 の口の幅（t）
parts.append(court_wall("中庭 2 の口の塀（S2）", "f0", (L0, L0 + 0.6 * LMOUTH2), (L0 + 0.45 * LMOUTH2, L0 + LMOUTH2),
                        ph("071413956", "071424391", "071408553.MP"), "071413956：左（北）の 1 枚が手前、右に敷石の入口と奥の 1 枚"))
L4 = faces["f4"]["length"]["value"]
LMOUTH1 = ST[5][1] - ST[12][1]
parts.append(court_wall("中庭 1 の口の塀（S2）", "f4", (L4, L4 + 0.6 * LMOUTH1), (L4 + 0.45 * LMOUTH1, L4 + LMOUTH1),
                        ph("071334183", "071347810", "071325894"), "071334183：同じ作りで四角い穴",
                        g=WEST_GROUND["f4"]["value"],
                        g_why="検収が 071334183 の視点から読んだ塀の足元の道路の高さ（−0.4 前後、faces.f4.ground_offset −0.3。設計は 1.7 m）"))

# f17 の柱型（柱型・中 6 本、柱型・北 5 本）と間の窓、1 階の柱廊
PIERS_M = [(27.4, 29.4), (30.5, 32.4), (33.5, 35.5), (36.5, 38.5), (39.5, 41.5), (42.5, 44.5)]
PIERS_N = [(55.0, 57.1), (58.1, 60.2), (61.1, 63.2), (64.2, 66.4), (67.4, 69.5)]
pr = []
for grp in (PIERS_M, PIERS_N):
    for (a, bb_), (c, _) in zip(grp, grp[1:]):
        pr.append(box(bb_, c, -0.45, 0.5, FL[2] + 0.1, WB[3][0], cut=True, name=f"gap_{bb_}"))
        pr.append(box(bb_, c, -0.47, -0.43, FL[2] + 0.1, WB[3][0], "concrete", f"gapwall_{bb_}"))
        for n in (2, 3, 4):
            zf = WB[n - 2][1]
            pr += [box(bb_ + 0.1, c - 0.1, -0.43, -0.40, zf + 0.25, zf + 0.95, "white", f"gb_{bb_}_{n}"),
                   box(bb_ + 0.1, c - 0.1, -0.40, -0.39, zf + 0.25, zf + 0.95, "glass", f"gbg_{bb_}_{n}"),
                   box(bb_ + 0.1, c - 0.1, -0.43, -0.40, zf + 1.05, zf + 2.75, "interior", f"win_{bb_}_{n}"),
                   box(bb_ + 0.1, c - 0.1, -0.40, -0.39, zf + 1.05, zf + 2.75, "glass", f"wing_{bb_}_{n}")]
parts.append(comp("f17 の柱型の間の窓（柱型・中・柱型・北）", "f17", pr, REG["f17"],
                  "柱型の縁は coverage_f17_photo.png の 3 階の断面で読んだ（柱型・中 px 1096〜1780、柱型・北 px 2200〜2780）。測量係の柱間隔 3.09 m・3.04 m と合う。"
                  "窓は各階の白い帯の上 1.05〜2.75 m（px で 42〜110）、その下のガラスブロックの腰 0.25〜0.95 m（観察票「窓の高さの 1/3」）。"
                  "柱型の間の奥まり 0.45 m は比率（074903876 で柱型の幅の 0.2）", 0.3,
                  note=f"ref: {REP}/sokuryo_f17.json#sections"))
# 柱廊のタイルの角柱：coverage_f17_photo.png の 1 階（z 0.5〜4 m）で明るい室内の前に暗いタイルの柱が立つ所（px で 1092〜1172・1356〜1396・
# 1708〜1780・2216〜2280・2468〜2504）。広い 3 本は照明付き
# 検収（11/7 kenshu）：角柱の縁を recheck 20261107_151014 の縦の線（正面寄りの登録 3〜5 枚の平均）で直した。
#   27.3〜29.3 の右の縁 +0.18（074939442 +0.19・075128406 +0.18）→ 29.5、42.7〜44.5 の右 +0.29（074925492 +0.38・074939442 +0.36・074949973 +0.14）→ 44.8、
#   55.4〜57.0 の左 −0.21（074837587 −0.17・074903876 −0.12・074913586 −0.16・074925492 −0.40）→ 55.2・右 +0.23（+0.36・+0.13・+0.29・+0.12）→ 57.2、
#   61.7〜62.6 の右 +0.19（074837587 +0.29・074903876 +0.26・074913586 +0.24・074925492 +0.07・075128406 +0.11）→ 62.8。33.9〜34.9 は合格で動かさない
COLS = [(27.3, 29.5), (33.9, 34.9), (42.7, 44.8), (55.2, 57.2), (61.7, 62.8)]
CDEP = 3.0
pr = [box(18.0, 69.5, -CDEP, 0.5, FL[1] - 0.3, WB[0][0], cut=True, name="colonnade_void"),
      box(18.0, 69.5, -CDEP - 0.02, -CDEP + 0.02, FL[1], WB[0][0], "white", "colonnade_backwall")]
for a0, a1, nm in ((30.6, 33.2, "door_mid"), (49.6, 52.5, "door_north")):     # 入口のガラスの扉（coverage_f17_photo.png の 1 階の明るいガラス面 px 1224〜1328・1984〜2100）
    pr += [box(a0, a1, -CDEP + 0.02, -CDEP + 0.05, FL[1], FL[1] + 2.6, "interior", nm),
           box(a0, a1, -CDEP + 0.05, -CDEP + 0.07, FL[1], FL[1] + 2.6, "glass", nm + "_g")]
for a, c in COLS:
    pr.append(box(a, c, -0.6, 0.0, FL[1] - 0.3, WB[0][0], "tile", f"col_{a}"))
parts.append(comp("f17 の 1 階の柱廊（S5）", "f17", pr, REG["f17"] + ph("075147867"),
                  "柱廊の範囲は柱型の区画の 1 階（coverage_f17_photo.png、南の 0〜18 m は未撮影で置かない）、天井は 2 階の床の白い帯の下端 4.07 m。"
                  "奥行き 3.0 m は比率（見えていない。074903876 で柱の幅 2 m の 1.5 倍）。タイルの角柱 5 本は coverage_f17_photo.png の 1 階で読み、"
                  "検収が再検収の縦の線（正面寄りの 3〜5 枚の平均）で縁を直した（27.3〜29.5・33.9〜34.9・42.7〜44.8・55.2〜57.2・61.7〜62.8 m、"
                  f"{REP}/kenshu_summary.json。柱の奥行き 0.6 m は比率）。他にも細い柱があるかは木と暗さで読めない。奥の壁はベージュ。入口のガラスの扉 2 か所は coverage_f17_photo.png の 1 階の明るいガラス面"
                  "（30.6〜33.2・49.6〜52.5 m、高さは 1 階の天井の 2/3）", 0.9,
                  note="奥行きと柱の位置は比率（tol 30%）。柱型の位置そのものは製図板で読んだ"))
# 大きな庇
# 検収（11/7 kenshu）：出と高さを登録済みの写真の三角測量で直した（設計は出 2.3 m・上端 21.35 m。設計は coverage_f17_photo の庇の先 23.4 m を
#   カメラの距離 25 m と置いて解いたが、台帳の f17 の写真のカメラは面から 9.6〜10.8 m で、同じ式で解き直すと上端 21.35 なら出 0.9 m、出 2.3 なら上端 18.4 m になる）。
#   ① 庇の先の北の角を 074903876（2946.5,224 px）と 074913586（2867.5,221.5 px）で指した交点：s=70.42・d=1.87・z=19.23（±0.06・±0.12）。
#   ② 正面寄りの 6 枚（074913586・074925492・074939442・074949973・074903876・074837587）の庇の先と空の境（縦の明るさの段）を、出 D の板の上の高さに直すと
#      D=2.0 で 18.90〜19.16 m、D=1.5 で 19.89〜20.02 m（どの写真も同じ式の線に乗る）。① と合わせて D≈1.9・上端≈19.2。
#   ③ 5 階のガラスブロックの上端 18.3 m（製図板）より下には来られない。製図板の庇の先の明るい帯（23.1〜23.4 m）から鼻先の厚さ 0.3〜0.4 m。
#   ④ 071033016（南から）で庇が東の塊の南の端（f17 の始点）まで続く（観察票「面の全長」）。北の端は北東の塔の南の面（s=70.4）
#   ⑤ 2 回目の recheck（20261107_154620）：上端 19.2 は 7 枚で −0.09〜+0.03、下端 18.85 は +0.06（平均）、鼻先の垂れ（18.75）は +0.15（平均、7 枚 +0.08〜+0.32）
#      で垂れは無い → 鼻先の下端を板の下端 18.85 に揃えた
EAVE_D = 1.9
EAVE_TOP = 19.2
EAVE_BOT = 18.85
parts.append(comp("f17 の片持ちの大きな庇", "f17",
                  [box(0.0, NE_TOWER_S0, -0.5, EAVE_D, EAVE_BOT, EAVE_TOP, "concrete", "eave"),
                   box(0.0, NE_TOWER_S0, EAVE_D - 0.15, EAVE_D, EAVE_BOT, EAVE_TOP, "coping", "fascia")],
                  ph("074903876", "074913586", "074925492", "074939442", "074949973", "074837587", "071033016.MP"),
                  "検収の三角測量（設計の値 出 2.3 m・上端 21.35 m を直した）：庇の先の北の角を 074903876・074913586 の 2 本の視線の交点で s=70.42・出 1.87 m・"
                  "上端 19.23 m。正面寄りの 6 枚の庇の先と空の境を出 D の板の上に直した高さは D=2.0 で 18.90〜19.16 m（同じ線に乗る）。合わせて出 1.9 m・上端 19.2 m。"
                  "厚さ 0.35 m は coverage_f17_photo.png の鼻先の明るい帯（23.1〜23.4 m に写る、出の分だけ拡大）から。範囲は 071033016 で南の端まで続き、北は北東の塔の南の面（s=70.4）まで。"
                  "tol は出 ±0.3・高さ ±0.35（3 枚目 074837587 を入れると 出 2.13・上端 18.75 に動く幅）",
                  0.35, note=f"ref: {REP}/kenshu_PXL_20261105_074903876.json"))
# 北の塔の上のアンテナ
s_ant = S_E19 - 5.65
parts.append(comp("北の塔の上のアンテナ（板の付いた柱）", "f19",
                  [{"shape": "cylinder", "at": [5.65, -2.3], "r": 0.15, "z": [23.94, 28.4], "material": "metal", "name": "pole"},
                   box(5.2, 6.1, -2.6, -2.0, 26.0, 27.8, "metal", "panels")],
                  REG["f19"] + ph("071548825", "074837587"),
                  "coverage_f19_photo.png で柱の中心 px 226（f19 の東端から 5.65 m）、塔の頂部（23.94 m）から上へ。高さは観察票「塔の上に出る高さは 1 階分強」で 4.5 m"
                  "（写真の上端 25.9 m より上は切れる）。奥行きの位置は塔の中ほどと置いた", 1.1))

# 中庭 1 の奥のコアの頂部のタイルの箱（f8 の上）
L8f = faces["f8"]["length"]["value"]
D8 = ST[7][0] - ST[8][0]          # コアの出（f7 の辺の長さ）
parts.append(comp("f8 の頂部のタイルの箱（中庭 1 のコア）", "f8",
                  [box(0.0, L8f, -D8, 0.0, 21.3, round(21.3 + 0.75 * 4.03, 2), "tile", "top_box")],
                  ph("071334183", "071347810"),
                  "比率から（面の写真なし）：観察票「頂部の箱は高さが階高の 1 倍弱、両脇の壁の上端より上に出る」。両脇の上端＝背骨の上端 21.3 m（f13 の面の写真）から"
                  "0.75 階分（3.0 m）上へ。平面は足元の出っ張り（f7・f8・f9 の辺）いっぱい", 0.8))

for k, (a0, a1) in enumerate(F8_PANELS):
    parts.append({"type": "patch", "name": f"f8 のガラスの筋の縁のベージュのパネル（{'北' if k == 0 else '南'}）", "face": "f8",
                  "along0": ratio(a0, ph("071334183"), "ガラスの 1/4 の幅（観察票）。面の端から") if a0 else est(0.0, 0.1, [PLATEAU], "面の端"),
                  "along1": ratio(a1, ph("071334183"), "ガラスの 1/4 の幅（観察票）"),
                  "z0": ratio(1.0, ph("071334183", "071347810"), "ガラスの筋の下端と同じ（塀に隠れて下は見えない）"),
                  "z1": ratio(round(FL[6] - 0.3, 2), ph("071334183", "071347810"), "ガラスの筋の上端と同じ"),
                  "material": "white"})

# 屋上の設備：東の塊の北寄りの白い箱 2 つ（観察票の航空写真の読み）。recorder photos roof --units で人が指した
def unit_poly(cx, cy, w, d):
    """航空写真の上の中心と大きさ（w＝s の向き、d＝棟の長い向き t）から、棟の向きに回した四角"""
    return [[round(cx + a * w / 2 * U[0] + b_ * d / 2 * V[0], 2), round(cy + a * w / 2 * U[1] + b_ * d / 2 * V[1], 2)]
            for a, b_ in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


ROOF_EV = ["gsi:aerial_z18"]
ROOF_M = ("recorder photos roof data/buildings/kyotsu-minami.json --units \"32.2,107.7,2.7,7.5;28.1,99.6,3.2,6.5\"（out/kyotsu-minami/roof.json）。"
          "白い箱を roof_aerial.png の拡大で人が指した（自動の 4 件は段のテラスの縁と影で、設備ではない）。")
parts.append({"type": "rooftop_units", "name": "東の塊の北寄りの白い箱 2 つ（屋上の設備）", "source": "aerial",
              "lean_correction_m": {"value": [0.0, 4.0], "unit": "m", "tol": 2.0, "status": "estimated", "evidence": ROOF_EV,
                                    "method": ROOF_M + "倒れ込み lean_m (0, −4) m（率 0.19）の逆。縁の候補の 1 位と 2 位が近い（ambiguous、2 位は (5.75, −6)）"
                                              "ので ± 2 m。低い建物の検算でも 1 m ずれる", "note": ""},
              "z0": est(21.35, 1.0, REG["f13"], "背骨の塊の上端（屋上の帯の上端）に置いた。屋根の床はそれより約 1 m 低い（帯の上端は床＋0.97 m）",
                        ref="blocks.B_背骨.top_z"),
              "units": [{"polygon": unit_poly(32.2, 107.7, 2.7, 7.5),
                         "h": ratio(2.0, ph("074837587"), "見えていない。074837587 で北東の塔の奥の灰色の箱が庇の上に半階ほど出る")},
                        {"polygon": unit_poly(28.1, 99.6, 3.2, 6.5),
                         "h": ratio(2.0, ph("074837587"), "見えていない。同じ高さと見た")}],
              "note": "位置と大きさは航空写真の上の読み（w×d＝2.7×7.5 m・3.2×6.5 m、tol は 1 m 前後）。どの塊の上か：背骨の東寄り（s≈37.8）"})

# ガラスの箱（S3）：OSM の輪郭、柱の上
osm_ring = [[53.74, 123.13], [49.67, 125.23], [45.41, 116.98], [30.28, 124.81], [25.44, 123.15], [24.79, 121.9],
            [34.2, 116.88], [42.45, 112.77], [41.8, 111.52], [46.48, 109.09]]
Z_PIL = 4.4
Z_PAN = 1.8
H_GL = 3.6
parts.append({"type": "vestibule", "name": "北東のピロティの上のガラスの箱（S3）", "polygon": osm_ring, "wall_edges": [5, 6, 7],
              "z0": ratio(Z_PIL + Z_PAN, ph("071548825", "074837587", "071529022"),
                          "柱（ピロティ）の高さは 1 階分強（1.1×4.03＝4.4 m）＋下のパネルの帯はガラスの半分（1.8 m）（観察票）"),
              "height": ratio(H_GL, ph("071548825", "074837587", "071529022"), "ガラスの部分の高さは本体の 1 階分弱（0.9×4.03）"),
              "mullion_pitch": ratio(1.3, ph("074837587", "071536516"), "北面 16 枚前後（観察票）、輪郭の北の長い辺 17 m ÷ 13〜16 枚", 0.25),
              "transom_pitch": ratio(3.6, ph("074837587"), "横の桟は上下の枠だけに見える"),
              "roof": {"thickness": ratio(0.3, ph("074837587"), "屋根の板の縁はパネルの帯の 1/6"),
                       "overhang": ratio(0.2, ph("074837587"), "屋根の縁が少し出る")},
              "note": "平面は OSM の osm-way-1304077324（ODbL、estimated）。北の広場の向こうの棟（共通講義棟北）とは OSM で 30 m 以上離れ、"
                      "写真でもこの棟の f19・f18 に付く。f19 の上の辺（5）と f18 の前（6・7）は壁の側"})
# ガラスの箱の下のパネルの帯と屋上の手すり、東の辺の柱
ring_closed = osm_ring + [osm_ring[0]]
pan = [{"shape": "prism", "polygon_xy": osm_ring, "z": [Z_PIL, Z_PIL + Z_PAN], "material": "metal", "name": "panel_band"}]
for i, (p0, p1) in enumerate(zip(ring_closed, ring_closed[1:])):
    if i in (5, 6, 7):
        continue
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    Ln = math.hypot(dx, dy)
    nx, ny = -dy / Ln * 0.06, dx / Ln * 0.06     # 内側へ 6 cm（反時計回りかは問わない細い板）
    pan.append({"shape": "prism", "polygon_xy": [p0, p1, [p1[0] + nx, p1[1] + ny], [p0[0] + nx, p0[1] + ny]],
                "z": [Z_PIL + Z_PAN + H_GL + 0.3, Z_PIL + Z_PAN + H_GL + 0.3 + 1.2], "material": "metal", "name": f"roofrail_{i}"})
e0, e1 = osm_ring[9], osm_ring[0]        # 東の辺（f17 の延長の 3 m 外）
for k in range(4):
    r = (k + 0.5) / 4
    cx, cy = e0[0] + (e1[0] - e0[0]) * r, e0[1] + (e1[1] - e0[1]) * r
    pan.append({"shape": "prism", "polygon_xy": [[cx - 0.5, cy - 0.5], [cx + 0.5, cy - 0.5], [cx + 0.5, cy + 0.5], [cx - 0.5, cy + 0.5]],
                "z": [-0.5, Z_PIL], "material": "concrete", "name": f"pilotis_{k}"})
parts.append(comp("ガラスの箱の下のパネルの帯・屋上の手すり・東の辺の柱（S3）", None, pan,
                  ph("074837587", "071548825", "071529022", "074849844"),
                  "比率から：パネルの帯はガラスの半分（1.8 m）、屋上の手すりはガラスの 1/3（1.2 m）（観察票）。東の辺の打ち放しの柱は 074837587 で 4 本が"
                  "ほぼ等間隔に並ぶので辺を 4 等分の中心に置いた（太さ 1.0 m は柱の間の 1/4）。他の辺の柱は全部が入る写真が無く置いていない", 0.5,
                  note="平面は OSM（osm-way-1304077324）。柱の数は観察票で null（6 本以上見える）"))

# f15 の 5 階の張り出した箱・東端の柱型（071033016 の 1 枚）
L15 = faces["f15"]["length"]["value"]
parts.append(comp("f15 の 5 階の張り出した打ち放しの箱と東端のタイルの柱型", "f15",
                  [box(0.40 * L15, 0.76 * L15, 0.0, 0.6, FL[5], FL[6] + 0.6, "concrete", "box5f"),
                   box(0.43 * L15, 0.73 * L15, 0.6, 0.62, FL[5] + 0.9, FL[5] + 2.9, "glass", "box5f_win"),
                   box(0.66 * L15, L15, -0.01, 0.3, 0.0, 23.85, "tile", "east_pier")],
                  ph("071033016.MP"),
                  "比率から（f15 は 071033016 の 1 枚で斜め）：箱の幅は f15 の 0.36（px で 345〜545 ÷ 250〜810）で中ほど、高さは 5 階の床から屋上の上 0.6 m（観察票「1 階分」）、"
                  "出 0.6 m（「出は小さい」）。東端の柱型は f15 の 1/3 の幅で、頂部は南東の柱型（23.85 m）と同じに見える", 1.0,
                  note="濃い灰色の高い塊（塔屋）は下の部品「背骨の南の濃い灰色の塔屋」（検収が足した）"))

# 背骨の南の濃い灰色の塔屋（S8 a）。検収（11/7 kenshu）が足した：写真 071033016 に南東の柱型より高く写る 3 m 以上の物で、模型に無かった（検収 C の 5）。
#   071033016（f13 に登録、南東の柱型の頂部 23.85 m の線が写真の縁に乗る）で塔屋の南東の角・南西の角・北東の角の上端を指し、視線を延ばすと、
#   f15 の線（t=2.07）より奥で上端 24.85 m（PLATEAU の最高点 73.03 m − ground_z 48.18）になる所は s 35.7〜38.8・t 3.1〜8.3。
#   航空写真（out/site/aerial_z18.png）の南端の暗い塊は倒れ込み（0.188×24.85＝4.7 m）を戻すと中心 s≈37.4・t≈7・幅 5 m 前後で同じ所。
#   上端は PLATEAU の最高点を採った（どの塔も PLATEAU より約 1 m 低い、という設計の未解決の答え。南東の柱型 23.85・北の塔 23.94 では説明できない）
TOWER_S = rect(35.7, 38.5, 3.2, 8.3)
parts.append(comp("背骨の南の濃い灰色の塔屋（S8 a）", None,
                  [{"shape": "prism", "polygon_xy": TOWER_S, "z": [21.3, 24.85], "material": "dark", "name": "penthouse"}],
                  ph("071033016.MP") + ["gsi:aerial_z18", PLATEAU],
                  "検収：071033016 で塔屋の上端の角 3 つ（南東 px 3025,786 ほか）の視線を延ばし、f15 の線より奥で PLATEAU の最高点 24.85 m（標高 73.03 m）に"
                  "届く所を平面とした（s 35.7〜38.5・t 3.2〜8.3、2.8 m × 5.1 m）。航空写真の南端の暗い塊（倒れ込み 4.7 m を戻して中心 s≈37.4・t≈7）と同じ所。"
                  "高さは屋上（21.3 m）から 3.55 m（塔屋 1 階分）。上端を ±0.35 m 変えると視線の上で t が ±0.5 m 動く。細い棒のアンテナ（1 m 前後）は置いていない",
                  0.6, note=f"役割：階段室・EV 機械室と読む（屋上から 1 階分、濃い灰色の外装）。ref: {REP}/kenshu_summary.json"))

b["parts"] = parts

# ---- 写真の一覧 --------------------------------------------------------------------------------------
idx = json.loads((ROOT / "data/photos/index.json").read_text(encoding="utf-8"))
b["photos"] = [{"id": p["id"], "file": p["file"], "face": p.get("face"), "faces": p.get("faces", []),
                "role": "登録済み（製図板）" if any(p["id"] in v for v in REG.values()) else "観察・数え"}
               for p in idx["photos"] if p.get("building") == BID and p.get("period") == "during"]

# ---- 依頼 -------------------------------------------------------------------------------------------------
def rq(face, kind, what, why, where=""):
    return {"face": face, "kind": kind, "what": what, "where": where, "why": why, "status": "open",
            "to": "staff" if kind == "photo" else "tool"}


b["requests"] = [
    rq("f19", "data", "f19 の面の写真（coverage_f19_photo.png）の足元を取り直す。同じ床の印が f13・f17 より 1.04 m 低く写る",
       "設計では f19 の高さに +1.04 m を足した（帯 5 本の差 0.97〜1.10 m、塔の白い帯も同じ位置）。"
       "検収（11/7）：f17 の写真の組から鎖で登録した 074837587 で f19 の帯・北の塔の上端が +1.04 後の高さに合う（4 階の帯 +0.04・北の塔 +0.07）、"
       "遠い 071509670・071517479 も帯 11・10 本が中央値 +0.02・0.00。近い 071536516（平面の登録）とその鎖 071527944・071529022 だけが中央値 −0.24〜−0.28 で、"
       "重ね（overlay_PXL_20261105_071536516.png）でも模型の輪郭が写真より約 1 m 上に出る。違っているのは近い 3 枚の高さの原点（5m 標高の面の前 −1.03 m）で、"
       "f19 の前の地面は z≈0.0。測量係に 071536516 の登録の高さの原点を +1.04 m 直して鎖の 2 枚と製図板を出し直してもらう（設計の F19_DZ はそれで不要になる）"),
    rq("f13", "data", "PLATEAU の高さ 25.9 m（頂部 73.03 m）の最高点がどの部品か（南東の柱型 23.85 m・北の塔 23.94 m・背骨の南の濃い灰色の塊）を航空写真で",
       "どの塔も PLATEAU の頂部より約 1 m 低い。未解決") | {"status": "answered",
       "answer": "検収（11/7）：背骨の南の濃い灰色の塔屋（S8 a）。071033016 で南東の柱型より高く写り、視線と航空写真の南端の暗い塊（倒れ込み 4.7 m を戻す）が "
                 "s≈36〜38.5・t≈3〜8 で重なる。部品「背骨の南の濃い灰色の塔屋」として上端 24.85 m（＝PLATEAU の最高点）で置いた。高さの裏付けは写真 1 枚なので撮影依頼 f15 は残す"},
    rq("f17", "photo", "東面の南の 0〜18 m（柱型・南の区画とガラス帯・南）を正面から（東の庭の南寄りから）",
       "製図板が未撮影（被覆 70.7%）。柱型・南の本数・南端のタイルの塊が置けない（庇は 071033016 で南の端まで続くので検収が延ばした）", "東の庭の南寄り"),
    rq("f17", "photo", "北端か南端から面に沿って横から、庇の出と厚さ、柱廊の奥行きが分かる 1 枚",
       "柱廊の奥行き 3.0 m は比率（tol 0.9 m）。庇は検収が 2 枚の三角測量で出 1.9 m・上端 19.2 m に直した（tol 0.3〜0.35 m）が、横からの 1 枚で厚さと奥の屋上の高さが決まる"),
    rq("f19", "photo", "北面の 1・2 階の窓の列を東端から西端まで（木より建物寄りから）",
       "柱割りが数えられず窓を置いていない（帯の引っ込みだけ）"),
    rq("f12", "photo", "西の道路の向かい（擁壁の上）から各翼の西端を正面に、台形の壁の両端と奥の段が全部入るように（f0・f4・f12）",
       "台形の壁の平らな所の長さ・端の箱・上の箱が比率だけ。足元の線が階段の壁か奥の建物の壁かも決まらない"),
    rq("f4", "photo", "中翼の段（3・4・5 階の西の縁）を中庭 1 か 2 の中から斜めに、段の角が入るように",
       "中翼の段は北翼からの仮説（same_block）で置いた"),
    rq("f1", "photo", "中庭 1・2 の中から両側の面（f1・f3・f5・f11）を斜めに、両端が入るように",
       "帯の高さと範囲を f13・f19 からの仮説で置いた。窓の列は置いていない"),
    rq("f8", "photo", "中庭 1 の中に入り、奥のコア（ガラスの筋と頂部のタイルの箱）と両脇（f6〜f10）を正面から",
       "頂部の箱の高さ・ガラスの筋の幅が比率だけ。f6・f10 の窓は根拠の門で estimated"),
    rq("f15", "photo", "南端（横断歩道の北）から背骨の南面と、その上の濃い灰色の塊（塔屋）とアンテナを両端が入るように",
       "検収が 071033016 の 1 枚と航空写真・PLATEAU から塔屋を置いた（平面 ±0.6 m・上端は PLATEAU の最高点）。2 枚目で平面と高さを確かめたい。5 階の箱も 1 枚の比率だけ"),
    rq("f18", "photo", "北東のガラスの箱の下から f18 の壁と f19 との段、ピロティの柱を全部",
       "f18 は写真なし（灰色）。ガラスの箱の柱は東の辺の 4 本しか置いていない"),
    rq("f14", "photo", "南東の柱型の東の側面（f14）と背骨の南端の東面（f16）を南東から",
       "写真なし（灰色）。南東の柱型の奥行きが足元の辺の 2.07 m までしか分からない"),
    rq("f12", "tool", "敷地に置く物：西の道路の上の打ち放しの梁の列（S1）。擁壁（敷地）と f0・f4・f12 の台形の壁の上端をつなぐ。"
                      "本数は f12 で 3〜4、f0 で 3（数え係 measured）、道路沿いに 9〜11 本。梁の成は台形の壁の高さの 1/6〜1/8（071309389）",
       "2 つ以上の物（擁壁と棟）に触れるので敷地 JSON の物（設計係の規則）。敷地 JSON に置く語彙が無いので棟には置いていない"),
    rq("f17", "tool", "敷地に置く物：南の渡り（S4、OSM osm-way-1304077323）。f17 の南端と東の棟（osm-way-1304077325）をつなぐ柱の上の箱。床は 3 階の床前後（075121795・074939442、遠く斜め）",
       "2 つの棟に触れるので敷地 JSON の物。棟には置いていない"),
    rq("f19", "check", "北東のガラスの箱（S3）を棟 JSON に置いた。北の棟（共通講義棟北）につながらないか",
       "写真ではこの棟の f19・f18 に付き、OSM の輪郭は北の棟から 30 m 以上離れる。つながるなら敷地 JSON へ移す"),
    rq("f0", "check", "西端の足元の線（PLATEAU）は奥の建物の壁か、手前の台形の階段の壁か",
       "f13・f19 の面の写真で 1・2 階の帯が西端（の 0.75 m 手前）まで届くので奥の建物の壁と置き、階段の壁を外 1.2〜1.5 m に置いた"),
    rq("f13", "tool", "観察票の面の名前の番号が id とずれる（f17＝「東南東面 18」など）。recorder new --from-site の名前の付け方",
       "設計では面の名前の頭に場所の名前を足した。番号は土台のまま"),
    # 目印係（11/7 15:08）が棟 JSON に直接足した 4 件。設計スクリプトを流すと消えるので、検収がここへ写した（文は目印係のまま。後ろに検収の答え）
    rq("f12", "tool", "目印の一覧（recorder photos landmarks）に composite の角が無い：西端の台形の階段の壁と箱（f0・f4・f12）、f17 の柱型・窓、北東のガラスの箱（S3）、北の塔・北東の塔",
       "西の道路の写真 7 枚と東面・北面の 3 枚は、面の端が階段の壁・木・ガラスの箱に隠れ、縦の線が 0〜1 本で散らばりの門を満たせず止めた。部品の角が目印になれば解ける見込み",
       "data/reports/kyotsu-minami/mejirushi.json"),
    rq("f12", "check", "西の道路の高さ（f0・f4・f12 の面の前の地面 1.44〜1.96 m、5m 標高）が模型の z=0 に対して 1.5〜2 m 高すぎる疑い",
       "目印で登録した 071303445・071334183 はカメラが面の足元から 2.0・1.3 m（道路の上の手持ち）。重ねると f12 の階段の壁の足元（z=1.77）が写真の道路より上に来る（検収係へ）",
       "data/reports/kyotsu-minami/mejirushi.json") | {"status": "answered",
       "answer": "検収（11/7）：f12 は 0.3 m、f4 は −0.3 m（tol 0.6）に直し faces[].ground_offset に書いた（2 枚の登録の視点から壁の足元を読んだ）。"
                 "f0 は確かな登録が無く 5m 標高 1.96 m のまま（撮影依頼 f0 を足した）"},
    rq("f2", "check", "071413956 の目印の登録（道具は合格）の高さと仰角が怪しい：面の足元から 6.2 m、仰角 1.2°（消失点 9.2°）",
       "同じ道路の 2 枚は 1.3〜2.0 m で消失点と合う。f1 の 1 階の軒裏の線（仮説 3.56 m）を足すと外れて解が崩れる。f2 の面の線は合うので f2 に写すのは使えるが、近い面には数 m ずれる恐れ（検収係へ）",
       "data/reports/kyotsu-minami/mejirushi/picks_PXL_20261105_071413956.json") | {"status": "answered",
       "answer": "検収（11/7）：f2 の線は合う（帯・窓 4 本 中央値 +0.01）が、f2 は 4.03 m ごとに同じ窓が並ぶので、1 階分（4.03 m）上にずれた解でも合う。"
                 "カメラ 6.23 m − 4.03 ＝ 2.2 m なら手持ちと合うので、1 階ずれの疑いが強い。合否と値の根拠に使っていない。"
                 "目印係に、繰り返さない目印（f0・f4 の階段の壁の上端、中庭 2 の口の塀の上端）を 1 つ足して解き直してもらう"},
    rq("f8", "check", "中庭 1 のコア（f8）の頂部のタイルの箱の下端が、両脇の壁（f6・f10）の上端より低く写る（071334183 の F3・F4 の寄り）",
       "模型は箱を 21.3 m から上（両脇の上端と同じ高さから）に置くが、3.5 m 手前の箱の下端が奥の壁の上端より下に見えるので、箱の下端（ガラスの筋の上端）は 21.3 m より低い（検収係へ）",
       "data/reports/kyotsu-minami/mejirushi.json"),
    # 検収係（11/7）
    rq("f13", "tool", "recheck の「柱線」を、帯の引っ込み（負の depth）の奥の窓の面に置いて比べてほしい",
       "f13 は帯（手すり壁）が面の線で、柱と窓は 1.74 m 奥。柱線を面の線（depth 0）で探すため、071033016 で ±0.2〜0.36 の交互のずれが出る（奥の窓の縁を拾う取り違え）"),
    rq("f19", "data", "近い 3 枚（071536516・071527944・071529022）の登録の高さの原点を +1.04 m 直し、古い鎖 071517479・074837587・075121795 も解き直す（測量係）",
       "これらの写真の f19 の線の不合格（中央値 −0.24〜−0.28）は模型ではなく登録の原点のずれ。直せば recheck で f19 を全部の写真で判定できる"),
    rq("f0", "photo", "西の道路の北の部分（f0 の前）で、北翼の西端の台形の壁の足元と上の箱が両方入る 1 枚（道路に立って、手持ちの高さで）",
       "f12・f4 の前の道路は 5m 標高より 1.5〜1.8 m 低かった（検収）。f0 の前は確かな登録が無く 5m 標高 1.96 m のまま"),
    rq("f19", "photo", "北東のガラスの箱を東の端から真横に（箱の東の面が線に見える向き）、f17 の 2 階の白い帯と一緒に",
       "074837587 で箱の下のパネルの帯の下端が f17 の 2 階の床の白い帯（4.1〜4.3 m）と同じ高さに写り、OSM の輪郭の位置なら箱は模型より 1〜1.5 m 低い。"
       "輪郭の位置（奥行き）が確かめられず、比率の値（柱 4.4・パネル 1.8・ガラス 3.6 m）を直していない（争点）"),
    rq("f17", "check", "東の塊（f17 側）の屋上の高さ。f17 の北端で背骨の塊の上端 21.3 m が写真の空に出る（074903876：s=70.4・面の線・z 20.6〜21.3 は空）",
       "東から庇の奥は見えない。航空写真では背骨の西との境に幅 1.5〜2 m の暗い帯（影なら東の塊が高い）。北東の塔の上端は 20.56 m（三角測量）。"
       "背骨を 2 つの塊に分ける境と高さが決まらない（争点 out/kyotsu-minami/soten.json）"),
]

b["history"] = [
    {"at": "", "by": "recorder new --from-site", "what": "PLATEAU と 5m 標高から土台を起こした"},
    {"at": AT, "by": BY,
     "what": "観察票・数え・測量と面の写真（coverage_f13・f17・f19_photo.png）から設計。翼 3 本の段（南翼は f13、北翼は f19 の面の写真、中翼は北翼からの仮説）、"
             "背骨、南東の柱型・北の塔・北東の塔・中庭 1 のコアの塊、f13・f19 の帯の引っ込み、f17 の柱型・ガラス帯・柱廊・庇、西端の台形の階段の壁、中庭の口の塀、ガラスの箱"},
    {"at": AT, "by": BY,
     "what": "高さの基準：同じ床の印が coverage_f13_photo と coverage_f17_photo で同じ高さ（白い帯 4.2・8.2・12.3・16.4 m）、coverage_f19_photo では 1.04 m 低い"
             "（帯 5 本の上端・下端の差 0.97〜1.10 m、塔の白い帯も帯の下端から 0.63 m で f13 の 0.65 m と同じ位置）。棟の床は 1 つと置き、2 面が合う f13・f17 を採った。"
             "f19 で読んだ高さに +1.04 m を足した（捨てた方：f19 の面の写真の素の高さ。根拠の写真 071536516・071529022・071527944・071509670）。"
             "地面（ground_offset・ground_z）は変えていない。未解決として requests（data）"},
    {"at": AT, "by": BY,
     "what": "数え係と観察票の食い違い：f13 の列は数え係の 8（measured）を採った（観察票は null）。f12 の梁は数え係 3・観察票 4 だが梁は敷地の物で棟に置いていない。"
             "f6・f10 の窓の段は数え係の 5（観察票 4）を採った（塀の上に 5 段目の上端がのぞく、071334183・071347810）"},
    {"at": AT, "by": BY,
     "what": "f17 の北東の塔の頂部：coverage_f17_photo では 20.9 m に写るが、074837587・074903876・074913586 で庇より上に出ているので面の線より奥と読み、"
             "観察票の比（庇より 1/3 階上）で 22.7 m とした"},
]
b["history"] += [
    {"at": KAT, "by": KB,
     "what": "検収（recheck 20261107_151014・photoproj 20261107_152318）：f17 の片持ちの庇を出 2.3→1.9 m・上端 21.35→19.2 m・範囲 18〜69.5→0〜70.4 m に直した。"
             "設計はカメラの距離を 25 m と置いたが台帳では 9.6〜10.8 m。庇の先の角を 074903876・074913586 の 2 本の視線の交点で s=70.42・出 1.87・上端 19.23、"
             "正面寄り 6 枚の庇の先も D=2.0 で 18.9〜19.2 m に揃う。photoproj でも庇（composite_26）の模型の縁に写真の縁が無い（裏付け 0.02〜0.14、5 枚）"
             f"（{REP}/kenshu_PXL_20261105_074903876.json）"},
    {"at": KAT, "by": KB,
     "what": "検収：北東の塔の上端 22.7→20.6 m・南の縁 69.6→70.4 m（074903876・074913586 の三角測量、角が面の終点 71.93 m に一致）。"
             "設計が捨てた製図板の 20.9 m の方が合っていた（庇が 19.2 m なので「庇より上に出る」と矛盾しない）。recheck の北東の塔の上端の合格（074925492 −0.02）は庇の先の縁を拾った取り違え"},
    {"at": KAT, "by": KB,
     "what": "検収：f17 の柱廊の角柱 4 本の縁を recheck の縦の線（正面寄り 3〜5 枚の平均）で直した（29.3→29.5・44.5→44.8・55.4/57.0→55.2/57.2・62.6→62.8）"},
    {"at": KAT, "by": KB,
     "what": "検収：西の道路の高さ。f12 1.77→0.3 m・f4 1.44→−0.3 m（faces[].ground_offset を新しく書き、階段の壁・段の箱・配管・中庭 1 の口の塀の足元も揃えた）。"
             "目印で登録した 071303445・071334183 のカメラが 5m 標高の地面の下（−0.79・−4.36 m）で、視点から壁の足元を読むと 0.6〜0.0・−0.4 前後。f0 は 5m 標高のまま"},
    {"at": KAT, "by": KB,
     "what": "検収：f5 の仮説（帯 5 段の高さ・範囲・奥行き、same_block from f19）を、目印係が登録した 071334183 の recheck（帯の高さの線 6 本 平均 +0.09・最大 0.35、偏りなし）で確定し estimated にした。"
             "段の位置（along）は北翼から写した値のままで tol 1.0 m（段の縁は線が取れず検算していない）"},
    {"at": KAT, "by": KB,
     "what": "検収 C の 5（写真にあって模型に無い 3 m 以上の物）：背骨の南の濃い灰色の塔屋（S8 a）を足した。071033016 の視線と航空写真の南端の塊で平面 s 35.7〜38.5・t 3.2〜8.3、"
             "上端は PLATEAU の最高点 24.85 m（どの塔も PLATEAU より 1 m 低いという設計の未解決の答え）"},
    {"at": KAT, "by": KB,
     "what": "検収：目印係が棟 JSON に直接足した requests 4 件を設計スクリプトへ写した（流し直すと消えていた）。検収の依頼 5 件を足した"},
    {"at": KAT, "by": KB,
     "what": "検収（2 回目の recheck 20261107_154620）：庇の鼻先の垂れ 0.1 m を外した（鼻先の下端の線が 7 枚で平均 +0.15、板の下端 18.85 は +0.06）"},
]
b["made_by"] = BY
b["made_at"] = AT

b = schema.apply_hypotheses_off(b, HYPOTHESES_OFF)
schema.save_building(b, OUT)
print("saved", OUT, "blocks", len(b["blocks"]), "parts", len(b["parts"]))
