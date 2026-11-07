"""本部棟（honbu）の設計。data/buildings/honbu.json はこのスクリプトが出す（JSON を手で直さない）。

設計係（sekkei・claude-opus-5-5）、2026-11-07。
入力：data/reports/honbu/ の観察票（kansatsu_*）・数え（kazoe_*）・測量（sokuryo_*）、面の写真（製図板）
out/honbu/coverage_<面>_photo.png（40 px/m。左端＝面の from、行 r の高さ z＝32.1 − r/40）、測量係の正面化画像。
土台は recorder.skeleton.from_site_cli("honbu")（＝recorder new honbu --from-site）。

座標の決め方（全部の値に共通）
- 高さの原点 z=0 は f6（南南西面）の西端の前の地面。測量係の高さ（笠木 23.15 m・1 階の開口 4.80 m）はここから測った。
  登録（coverage）も各面の前の地面を z=0 に置いて貼っているので、模型も f6・f5・f4・f8 の前の地面を 0 とする
  （ground_offset）。5 m 標高の帯は f6 −1.35・f5 −0.61・f4 +0.64・f8 −0.61（土台の ground_z からの差）で、
  1 m の許容の内外が混ざる。合わせに行かず、method と requests（kind data）に残した。
- 平面は棟の局所の座標 (a, b)：a＝南西の角 v7 から f6 に沿って東へ、b＝f6 から北へ（m）。world(a, b) で世界座標へ。
  南の翼は a 0〜49.72・b 0〜15.81、北の翼は a 0.644〜12.91・b 15.81〜43.87（PLATEAU の足元の頂点から計算）。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from recorder.schema import apply_hypotheses_off, save_building  # noqa: E402
from recorder.skeleton import from_site_cli  # noqa: E402

BID = "honbu"
AT = "2026-11-07"
BY = "設計係（sekkei・claude-opus-5-5）"
REP = "data/reports/honbu/"
OUT = ROOT / "data" / "buildings" / f"{BID}.json"
# 検収係の直し（2026-11-07）。根拠は recorder recheck（out/honbu/recheck/20261107_150933/summary.json）の線のずれと写真 id。
# 報告は data/reports/honbu/kenshu_summary.json・kenshu_<写真id>.json
BY_K = "検収係（kenshu・claude-opus-5-5）"
KEN = REP + "kenshu_summary.json"
RECHECK1 = "recorder recheck（out/honbu/recheck/20261107_150933）"
# f8 に目印（landmarks）か正面化で登録された写真（recheck の柱型の縁を読んだ物）
PH_F8_REG = ["PXL_20261103_043320836", "PXL_20261103_043327622", "PXL_20261103_043334437", "PXL_20261103_043826814",
             "PXL_20261103_044049610.MP", "PXL_20261103_043314396", "PXL_20261103_043342958"]

# 仮説を外した記録（recorder hypotheses --drop が追記する）。{面 id か "*": {"at", "reason"}}
HYPOTHESES_OFF: dict = {}


# --- 測定値 ---------------------------------------------------------------------------------
def est(v, tol, ev, method, unit="m", ref=None, note=""):
    d = {"value": v, "unit": unit, "tol": tol, "status": "estimated", "evidence": list(ev), "method": method, "note": note}
    if ref:
        d["ref"] = ref
    return d


def meas(v, tol, ev, method, unit="m", ref=None):
    d = {"value": v, "unit": unit, "tol": tol, "status": "measured", "evidence": list(ev), "method": method, "note": ""}
    if ref:
        d["ref"] = ref
    return d


def unk(unit="m", note=""):
    return {"value": None, "unit": unit, "tol": None, "status": "unknown", "evidence": [], "method": "", "note": note}


def cov(face, what, px=""):
    """coverage_<面>_photo.png で読んだ px（40 px/m）の書き方。"""
    if px == "":
        return f"out/honbu/coverage_{face}_photo.png（40 px/m）で {what}"
    return f"out/honbu/coverage_{face}_photo.png で {what} {px} px × 1/40 m"


def floor_tol(obj, n=None):
    """面の写真・正面化の画像から読んだ長さ（m）の ± を、縮尺の許容 3%（PLATEAU の辺が元）＋読み 0.05 m より小さくしない。
    上げた値は method に書き足す。"""
    n = n if n is not None else [0]
    if isinstance(obj, dict):
        if "value" in obj and "status" in obj:
            v, t, m = obj.get("value"), obj.get("tol"), str(obj.get("method") or "")
            if (obj.get("status") != "unknown" and obj.get("unit", "m") == "m" and isinstance(v, (int, float))
                    and isinstance(t, (int, float)) and ("coverage_" in m or "stitched" in m or "正面化" in m)):
                lo = round(0.03 * abs(v) + 0.05, 2)
                if t < lo:
                    obj["tol"] = lo
                    obj["method"] = m + f"（± は縮尺の許容 3%＋読み 0.05 m＝{lo}）"
                    n[0] += 1
            return n[0]
        for k, v in obj.items():
            if k not in ("history", "requests", "crs"):
                floor_tol(v, n)
    elif isinstance(obj, list):
        for v in obj:
            floor_tol(v, n)
    return n[0]


# --- 平面の座標 -----------------------------------------------------------------------------
b = from_site_cli(BID)
POLY = [tuple(p) for p in b["footprint"]["polygon"]]
V0, V1, V2, V3, V4, V5, V6, V7, V8, V9 = POLY
_L6 = math.hypot(V8[0] - V7[0], V8[1] - V7[1])
U6 = ((V8[0] - V7[0]) / _L6, (V8[1] - V7[1]) / _L6)          # f6 に沿って東へ
NN = (-U6[1], U6[0])                                          # 北へ（f6 の内向き）


def world(a, bb):
    return [round(V7[0] + a * U6[0] + bb * NN[0], 3), round(V7[1] + a * U6[1] + bb * NN[1], 3)]


def local(p):
    dx, dy = p[0] - V7[0], p[1] - V7[1]
    return (dx * U6[0] + dy * U6[1], dx * NN[0] + dy * NN[1])


def rect_ab(a0, a1, b0, b1):
    return [world(a0, b0), world(a1, b0), world(a1, b1), world(a0, b1)]


A_NW_IN = local(V0)          # 入隅（f8 と f0）a 12.91・b 15.81
B_S = A_NW_IN[1]             # 南の翼の北の壁 b
A_F0 = A_NW_IN[0]            # 北の翼の東の壁 a
A_F4 = local(V5)[0]          # 北の翼の西の壁 a（f5 より 0.64 m 引っ込む）
A_NE = local(V9)[0]          # 北東の角 a
B_N = local(V3)[1]           # 北の翼の北の壁（f3 の線）b
B_STAIR = local(V2)[1]       # 外階段の外面（f1）b
A_STAIR_W = local(V3)[0]     # 外階段の西の側面（f2）a


def _isect(p, d, q, e):
    """直線 p+t·d と q+u·e の交点。"""
    den = d[0] * (-e[1]) - d[1] * (-e[0])
    t = ((q[0] - p[0]) * (-e[1]) - (q[1] - p[1]) * (-e[0])) / den
    return [round(p[0] + t * d[0], 3), round(p[1] + t * d[1], 3)]


# P：f8 の線を西へ延ばして f5 に当たる点（南の翼の北西の角）。Q：f3 の線を東へ延ばして f0 に当たる点（北の翼の北東の角）
P_SW = _isect(V0, (V0[0] - V9[0], V0[1] - V9[1]), V6, (V7[0] - V6[0], V7[1] - V6[1]))
Q_NE = _isect(V3, (V3[0] - V4[0], V3[1] - V4[1]), V0, (V1[0] - V0[0], V1[1] - V0[1]))
F5_SPLIT = round(math.hypot(P_SW[0] - V6[0], P_SW[1] - V6[1]), 2)       # f5 の from から南の翼の妻の始まりまで
F0_WALL = round(math.hypot(Q_NE[0] - V0[0], Q_NE[1] - V0[1]), 2)        # f0 の from から北の翼の北東の角まで
F3_STAIR = round(math.hypot(Q_NE[0] - V3[0], Q_NE[1] - V3[1]), 2)       # f3 の線上で外階段が占める長さ（v3 から東へ）

# --- 根拠の写真 ---------------------------------------------------------------------------------
PH_F6 = ["PXL_20261103_042900775", "PXL_20261103_042734715", "PXL_20261103_042909968", "PXL_20261104_074357308"]
PH_F6_E = ["PXL_20261103_042721424", "PXL_20261103_042822099", "PXL_20261105_064600203"]
PH_F8 = ["PXL_20261103_043856844.MP", "PXL_20261103_043826814", "PXL_20261104_070928590"]
PH_F0 = ["PXL_20261103_043304319", "PXL_20261103_043259125", "PXL_20261103_043753272"]
PH_F4 = ["PXL_20261103_043055592", "PXL_20261103_043103134", "PXL_20261103_043049890"]
PH_F5 = ["PXL_20261103_043004629", "PXL_20261103_043026097", "PXL_20261103_042946748", "PXL_20261103_043034043"]
PH_F7 = ["PXL_20261103_043612124", "PXL_20261103_043623454"]
PH_N = ["PXL_20261103_043123901", "PXL_20261103_043136840", "PXL_20261103_043150092", "PXL_20261103_043129358"]
PH_TOP = ["本部棟屋上"]

# --- 元の値（1 つの変数にして、配る先は ref だけ持つ） ---------------------------------------------
S_FP = est(4.13, 0.14, ["PXL_20261103_042900775", "PXL_20261103_042734715", "PXL_20261103_042909968"],
           "測量係の値（data/reports/honbu/sokuryo_f6.json#floor_pitch）。南の翼の 2〜5 階の階高",
           ref=REP + "sokuryo_f6.json#floor_pitch")
S_PITCH6 = est(5.418, 0.19, ["PXL_20261103_042900775", "PXL_20261103_042734715", "PXL_20261103_042909968"],
               "測量係の値（柱型の芯から芯）", ref=REP + "sokuryo_f6.json#bay_pitch")
S_TOP = est(23.15, 0.77, ["PXL_20261103_042900775", "PXL_20261103_042909968"],
            "測量係の値（f6 西端の地面から笠木の上端）。f5 の面の写真では 22.5 m（f5 の前の地面を 0 とした時、"
            "out/honbu/coverage_f5_photo.png の笠木 383 px）。差 0.65 m は面の前の地面の違い（5 m 標高で f5 の前は f6 の前より "
            "0.74 m 高い）と読めるが確かめていない（未解決）", ref=REP + "sokuryo_f6.json#parapet_top_above_ground")
S_1F = est(4.80, 0.21, ["PXL_20261103_042900775"], "測量係の値（西端の地面から 1 階の開口の上端＝梁の下端）",
           ref=REP + "sokuryo_f6.json#first_floor")
N_FP = est(3.938, 0.14, ["PXL_20261103_043055592", "PXL_20261103_043103134"],
           "測量係の値（北の翼の 2〜3 階）", ref=REP + "sokuryo_f4.json#floor_pitch")
N_TOP = est(17.75, 0.6, ["PXL_20261103_043055592", "PXL_20261103_043304319"],
            "北の翼の笠木の上端。out/honbu/coverage_f4_photo.png で 578 px（z 17.65）、coverage_f0_photo.png で 570 px（z 17.85）"
            "の平均（各面の前の地面を 0 とした登録の座標）。± は縮尺の 3%＋読み 0.1 m")
DECK_Z = est(5.45, 0.4, ["PXL_20261103_043856844.MP", "PXL_20261103_043320836", "本部棟屋上"],
             "2 階のデッキの床。床そのものは腰壁に隠れて見えていない。南の翼の 2 階の窓の彫り込みの下端 5.95 m"
             "（out/rectified/honbu/f6_042900775+042734715_stitched.jpg、135.96 px/m）から腰の高さ約 0.5 m を引いた。"
             "観察票 f8・f0：デッキの床は 2 階の床と同じ高さ")
PIER6 = est(0.93, 0.08, ["PXL_20261103_042900775"], "測量係の参考値（柱型の幅 127 px ÷ 135.96 px/m）",
            ref=REP + "sokuryo_f6.json#bay_pitch")
TOWER_TOP = est(31.0, 2.0, PH_TOP,
                "塔屋の上端。本部棟屋上（高い所から）で塔屋の角の縦の縁が南の翼の屋上から 210〜230 px、同じ写真の南の翼 5 階の"
                "小窓の枠（3.0 m、f6 の正面化で 18.32〜21.33 m）が 80 px → 約 27〜29 px/m で 7.9 m。23.15＋7.9 ≒ 31.0。"
                "北の翼の屋上からも 355 px ≒ 13 m（17.75＋13 ≒ 30.8）。観察票 roof の目安（南の翼より 0.7〜1 階分上、063431446）"
                "とは合わない（観察票は斜め下からの見上げ）。PLATEAU の measuredHeight 32.1 m とは 1.1 m 差で ± の内")

tile = {"name": "小口タイル（南の翼）", "color": "#c4a27a",
        "unit_w": est(0.108, 0.005, ["PXL_20261103_080542963", "PXL_20261103_042909968"],
                      "測量係が目地の周期の比（1.71〜1.72）で小口（108×60 mm）と確かめた（data/reports/honbu/sokuryo_f6.json#floor_pitch）"),
        "unit_h": est(0.060, 0.005, ["PXL_20261103_080542963", "PXL_20261103_042909968"], "同上（台帳 tile_course の 60 mm）"),
        "joint_w": est(0.010, 0.003, ["PXL_20261103_080542963"], "同上（台帳 tile_course の目地 10 mm）")}
tile_n = dict(tile, name="小口タイル（北の翼）", color="#b9a07f")


# --- 面 -------------------------------------------------------------------------------------
faces = {f["id"]: f for f in b["faces"]}
for f in faces.values():
    f["length"]["note"] = "PLATEAU の足元の辺（土台のまま。動かしていない）"
    f["length"]["tol"] = math.ceil(max(1.0, 0.03 * f["length"]["value"]) * 100) / 100
    f["length"]["method"] += "（± は PLATEAU の許容 3% か 1 m の大きい方）"
for fid in faces:
    faces[fid]["materials"] = [dict(tile_n if fid in ("f0", "f1", "f2", "f3", "f4") else tile)]


def ground0(face, ev, why):
    return est(0.0, 0.7, ev, "高さの原点（z=0）＝面の前の地面。" + why)


# ---- f6 南南西面（南の翼の正面）
f6 = faces["f6"]
f6["name"] = "f6 南南西面（南の翼の正面。正面玄関・車道の通り抜け・東のピロティ）"
f6["note"] = "観察票 kansatsu_f6・数え kazoe_f6・測量 sokuryo_f6。1 階の形は 042900775・074357308・080542963・042721424 から（下の history）"
f6["ground_offset"] = ground0("f6", ["PXL_20261103_042900775"],
                              "測量係の高さは全部 f6 西端の地面から（sokuryo_f6.json#parapet_top_above_ground）。"
                              "5 m 標高の帯の中央値は −1.35 m（土台の ground_z から）で ± を超える。未解決（requests）")
f6["top_z"] = dict(S_TOP, method="南の翼の笠木", note="", ref="blocks[south_wing].top_z")
f6["bays"] = {
    "count": meas(9, None, ["PXL_20261104_074357308", "PXL_20261103_042900775", "PXL_20261103_042721424",
                            "PXL_20261103_042822099", "PXL_20261105_064609316", "PXL_20261105_064721039"],
                  "数え係の値（垂れ幕を目印に 2 枚の列をつなぎ 3＋2＋4、5 階の小窓 18＝9×2 で検算）", unit="個",
                  ref=REP + "kazoe_f6.json#bays"),
    "pitch": dict(S_PITCH6),
    "pier_width": dict(PIER6),
    "margin_from": est(0.465, 0.08, ["PXL_20261103_042900775"],
                       "角の柱型の半分（測量係：辺＝9 柱間＋柱型 1 本分、角の柱型の幅は中の柱型と同じ）",
                       ref=REP + "sokuryo_f6.json#bay_pitch"),
    "margin_to": est(0.493, 0.2, ["PXL_20261103_042900775"], "辺 49.72 − 0.465 − 9 × 5.418（余り。南東の角の柱型の半分）"),
}
EV_F6W = ["PXL_20261103_042900775", "PXL_20261103_042734715"]
M_STITCH = "out/rectified/honbu/f6_042900775+042734715_stitched.jpg（測量係の正面化、135.96 px/m、地面 3480 px）で"
REC = est(0.5, 0.15, ["PXL_20261103_042900775", "PXL_20261104_074357308"],
          "見えていない。斜めの写真で見込みの下面が柱型の幅（0.93 m）の約 1/2 に見えると見た（比率から、± 30%）")
SPAN6 = {"height": est(0.6, 0.18, EV_F6W, M_STITCH + " 2 階の窓の彫り込みの下の濃い板 5.95〜6.55 m（82 px）。3・4 階は見上げで下が隠れる"),
         "depth": est(0.0, 0.05, EV_F6W, "彫り込みの中（観察票 f6：窓下の濃い板は彫り込みの中）"),
         "material": "dark", "in_recess": True}
f6["windows"] = []
for fl, top in ((2, 8.92), (3, 13.05), (4, 17.19)):
    zb_open = top - 2.95
    f6["windows"].append({
        "name": f"{fl} 階（横長、柱型の間）",
        "z_bottom": est(round(zb_open + 0.6, 2), 0.2, EV_F6W,
                        M_STITCH + f" 彫り込みの下端 {zb_open:.2f} m に窓下の板 0.6 m を足した", ref="faces.f6.windows.spandrel"),
        "z_top": est(top, 0.17, EV_F6W, f"測量係の窓の上端（{fl} 階）を 2 枚の正面化で読んだ値",
                     ref=REP + "sokuryo_f6.json#floor_pitch"),
        "width": est(4.41, 0.15, EV_F6W, M_STITCH + " 1 柱間目の開口 s 0.97〜5.38 m（600 px）"),
        "recess": dict(REC), "spandrel": json.loads(json.dumps(SPAN6))})
f6["windows"].append({
    "name": "5 階（小窓 2 つずつ）",
    "z_bottom": est(18.32, 0.2, EV_F6W, M_STITCH + " 小窓の枠の下端（1 柱間目・2 柱間目で同じ）"),
    "z_top": est(21.33, 0.2, EV_F6W, M_STITCH + " 小窓の枠の上端"),
    "width": est(5.36, 0.2, EV_F6W, M_STITCH + " 1 柱間の小窓 2 つの中心の間隔 2.68 m の 2 倍（小窓の幅 1.72 m は gap で引く）"),
    "gap": est(0.96, 0.1, EV_F6W, "小窓の中心の間隔 2.68 m − 小窓の幅 1.72 m（s 0.97〜2.68・3.64〜5.38）"),
    "per_bay": 2, "recess": dict(REC)})

# ---- f8 北北東面（南の翼の中庭側）
f8 = faces["f8"]
f8["name"] = "f8 北北東面（南の翼の中庭側。2 階のデッキ・入学センター・車道の出口）"
f8["note"] = ("観察票 kansatsu_f8・数え kazoe_f8・測量 sokuryo_f8。写真の辺は 7.18 柱間 ≒ 38.8 m で PLATEAU の 36.81 m より 2 m 長い。"
              "足元は動かさず margin_to を負にして食い違いを残した（6 柱間目の窓が入隅を約 1.5 m 越える）")
f8["ground_offset"] = ground0("f8", ["PXL_20261103_043856844.MP"],
                              "地面は写真で読めない。測量係は f6 西端の地面と同じと置いた（sokuryo_f8.json#height_origin_note）。"
                              "5 m 標高の帯は −0.61 m")
f8["top_z"] = dict(S_TOP, method="南の翼の笠木（同じ塊）", note="", ref="blocks[south_wing].top_z")
EV_F8 = ["PXL_20261103_043856844.MP", "PXL_20261103_043826814"]
f8["bays"] = {
    "count": meas(6, None, ["本部棟屋上", "PXL_20261104_070928590", "PXL_20261103_043320836",
                            "PXL_20261103_043856844.MP", "PXL_20261103_043826814"],
                  "数え係の値（5 階の小窓 12、縦樋 2 本を目印に 3 通りで 6）", unit="個", ref=REP + "kazoe_f8.json#bays"),
    "pitch": est(5.408, 0.25, EV_F8, "測量係の値", ref=REP + "sokuryo_f8.json#bay_pitch"),
    "pier_width": est(0.80, 0.08, EV_F8 + PH_F8_REG,
                      RECHECK1 + " で f8 の柱型の右の縁が 28 本の平均 −0.12 m、左の縁が 18 本の平均 +0.02 m（写真 7 枚）。"
                      "設計係の 0.93 m（" + cov("f8", "柱型の幅（4・5 階の窓の間）", 37) + "）から 0.13 m 細くした（検収係。" + KEN + "）"),
    "margin_from": est(5.95, 0.3, EV_F8 + ["PXL_20261104_070928590"],
                       cov("f8", "北東の角から 1 柱間目の開口の左の縁まで", 258) + "（6.45 m）から柱型の半分 0.465 m を引いた。"
                       "測量係は角→柱 1/2 の芯 2.09 柱間（≒ 1.09 柱間＝5.89 m）"),
    "margin_to": est(-1.59, 0.4, EV_F8, "辺 36.81 − 5.95 − 6 × 5.408（負：写真の辺が PLATEAU より長い食い違いを残す）"),
}
f8["windows"] = [
    {"name": "2 階（デッキに面するガラス）",
     "z_bottom": dict(DECK_Z, ref="parts[デッキ].z"),
     "z_top": est(8.92, 0.2, EV_F8, "f6 の 2 階の窓の上端と同じ高さに見える（out/honbu/coverage_f8_photo.png で 928 px＝z 8.9）"),
     "width": est(4.6, 0.15, EV_F8 + PH_F8_REG, "柱間 5.408 − 柱型 0.80（検収係が recheck の柱型の縁で直した値）。設計係の読み " + cov("f8", "2 階の開口の幅（柱型の間）", 178)),
     "recess": est(0.3, 0.1, EV_F8, "見えていない。ガラスが柱型のすぐ奥に見える（比率から）"), "rail": False},
]
for fl, zt, zb in ((3, 13.1, 10.05), (4, 17.2, 14.23)):
    f8["windows"].append({
        "name": f"{fl} 階（横長、柱型の間）",
        "z_bottom": est(round(zb + 0.6, 2), 0.25, EV_F8, cov("f8", f"{fl} 階の彫り込みの下端", round((32.1 - zb) * 40)) + "＋窓下の板 0.6 m"),
        "z_top": est(zt, 0.2, EV_F8, cov("f8", f"{fl} 階の彫り込みの上端", round((32.1 - zt) * 40))),
        "width": est(4.6, 0.15, EV_F8 + PH_F8_REG, "柱間 5.408 − 柱型 0.80（検収係が recheck の柱型の縁で直した値）。設計係の読み " + cov("f8", "開口の幅（柱型の間）", 178)),
        "recess": dict(REC), "spandrel": json.loads(json.dumps(SPAN6))})
f8["windows"].append({
    "name": "5 階（小窓 2 つずつ）",
    "z_bottom": est(18.4, 0.2, EV_F8, cov("f8", "小窓の枠の下端", 548)),
    "z_top": est(21.48, 0.2, EV_F8, cov("f8", "小窓の枠の上端", 425)),
    "width": est(5.36, 0.25, EV_F8, "f6 と同じ並び（中心の間隔 2.68 m の 2 倍）に見える：" + cov("f8", "小窓 2 つの中心の間隔", 107)),
    "gap": est(0.96, 0.15, EV_F8, cov("f8", "小窓の幅", 69) + "（1.72 m）を中心の間隔から引いた"),
    "per_bay": 2, "recess": dict(REC)})

# ---- f0 東南東面（北の翼の中庭側）
f0 = faces["f0"]
f0["name"] = "f0 東南東面（北の翼の中庭側。2 階のデッキ・1 階の店先の歩廊）"
f0["note"] = (f"観察票 kansatsu_f0・数え kazoe_f0・測量 sokuryo_f0。北東の角は from から {F0_WALL} m（f3 の線を延ばした点。"
              "写真の 28.15〜28.3 m と合う）。その先 2.7 m は外階段の東の側面（PLATEAU の辺は外階段まで含む）")
f0["ground_offset"] = est(-0.45, 0.3, ["PXL_20261103_043259125", "PXL_20261103_043304319", "PXL_20261103_044907567"],
                          "1 階の床（z=0）から中庭の車道まで、半円形の段と傾路で 3 段ほど（観察票 f0：段は 3〜4 段）× 1 段 0.15 m。"
                          "5 m 標高の帯は +0.04 m")
f0["top_z"] = dict(N_TOP, ref="blocks[north_wing].top_z")
f0["bays"] = {
    "count": meas(4, None, ["本部棟屋上", "PXL_20261103_043753272", "PXL_20261103_043259125"],
                  "数え係の値（両端が入る 2 枚で別々に 4）", unit="個", ref=REP + "kazoe_f0.json#bays"),
    "pitch": est(6.116, 0.5, ["PXL_20261103_043304319", "PXL_20261103_043753272"], "測量係の値", ref=REP + "sokuryo_f0.json#bay_pitch"),
    "pier_width": est(0.9, 0.1, PH_F0, cov("f0", "3 階の柱型の幅", 36)),
    "margin_from": est(3.28, 0.25, PH_F0, cov("f0", "入隅から 1 本目の柱型の芯まで（入隅の細い柱間）", 131)),
    "margin_to": est(round(30.78 - 3.28 - 4 * 6.116, 2), 0.6, PH_F0,
                     f"辺 30.78 − 3.28 − 4 × 6.116。北東の角の柱型の半分と外階段の東の側面（{30.78 - F0_WALL:.2f} m）"),
}
EV_F0 = ["PXL_20261103_043304319", "PXL_20261103_043259125"]
f0["windows"] = [
    {"name": "2 階（デッキに面するガラス）",
     "z_bottom": dict(DECK_Z, ref="parts[デッキ].z"),
     "z_top": est(7.73, 0.25, EV_F0, cov("f0", "2 階の開口の上端（デッキの腰壁の上に見える）", 975)),
     "width": est(5.05, 0.2, EV_F0, cov("f0", "3 階の開口の幅（同じ柱型の間）", 202)),
     "recess": est(0.3, 0.1, EV_F0, "見えていない。ガラスが柱型のすぐ奥に見える（比率から）"), "rail": False},
    {"name": "3 階（横長、柱型の間）",
     "z_bottom": est(9.45, 0.2, EV_F0, cov("f0", "ガラスの下端", 906)),
     "z_top": est(11.98, 0.2, EV_F0, cov("f0", "彫り込みの上端", 805)),
     "width": est(5.05, 0.2, EV_F0, cov("f0", "開口の幅", 202)),
     "recess": dict(REC, evidence=EV_F0),
     "spandrel": {"height": est(0.72, 0.15, EV_F0, cov("f0", "ガラスの下の濃い板", 29)),
                  "depth": est(0.0, 0.05, EV_F0, "彫り込みの中（観察票 f0）"), "material": "dark", "in_recess": True}},
    {"name": "4 階（平らな帯に窓 2 つずつ）",
     "z_bottom": est(13.9, 0.2, EV_F0, cov("f0", "窓の下端", 728)),
     "z_top": est(15.67, 0.2, EV_F0, cov("f0", "窓の上端", 657)),
     "width": est(6.08, 0.25, EV_F0, cov("f0", "1 柱間の窓 2 つの中心の間隔", 122) + " の 2 倍（窓の幅 2.17 m は gap で引く）"),
     "gap": est(0.87, 0.1, EV_F0, cov("f0", "窓の幅", 87) + "（2.17 m）を中心の間隔 3.04 m から引いた"),
     "per_bay": 2, "recess": est(0.15, 0.05, EV_F0, "見えていない。観察票 f0：平らな帯にあけた窓で深い額縁なし（比率から）"),
     "rail": False},
]
f0["glazing"] = [
    {"name": "入隅の細い柱間の 4 階の窓", "along0": est(0.68, 0.15, EV_F0, cov("f0", "窓の左の縁", 27)),
     "along1": est(2.8, 0.15, EV_F0, cov("f0", "窓の右の縁", 112)),
     "z_bottom": est(13.9, 0.2, EV_F0, cov("f0", "窓の下端", 728)), "z_top": est(15.67, 0.2, EV_F0, cov("f0", "窓の上端", 657)),
     "inset": est(0.15, 0.05, EV_F0, "4 階の窓と同じ（比率から）"), "depth": est(4.0, 1.2, EV_F0, "室内の暗い箱の奥行き（見えていない）")},
    {"name": "入隅の細い柱間の 3 階（手すり付きの引っ込んだ開口）", "along0": est(0.2, 0.15, ["PXL_20261103_043753272", "PXL_20261103_043812312"], cov("f0", "開口の左の縁", 8)),
     "along1": est(2.8, 0.15, ["PXL_20261103_043753272"], cov("f0", "柱型の左の縁", 113)),
     "z_bottom": est(8.73, 0.25, ["PXL_20261103_043753272"], cov("f0", "開口の下端（3 階の窓下の板の下端と同じ線）", 935)),
     "z_top": est(11.98, 0.2, ["PXL_20261103_043753272"], cov("f0", "開口の上端", 805)),
     "inset": est(1.5, 0.45, ["PXL_20261103_043753272", "PXL_20261103_043812312"], "見えていない。引っ込みは細い柱間の幅（2.8 m）の約 1/2 と見た（比率から）"),
     "depth": est(3.0, 0.9, ["PXL_20261103_043753272"], "室内の暗い箱の奥行き（見えていない）")},
    {"name": "入隅の細い柱間の 2 階（奥へ入る開口）", "along0": est(0.2, 0.15, ["PXL_20261103_043753272"], cov("f0", "開口の左の縁", 8)),
     "along1": est(2.8, 0.15, ["PXL_20261103_043753272"], cov("f0", "柱型の左の縁", 113)),
     "z_bottom": dict(DECK_Z, ref="parts[デッキ].z"),
     "z_top": est(7.73, 0.25, ["PXL_20261103_043753272"], cov("f0", "2 階の開口の上端", 975)),
     "inset": est(2.0, 0.6, ["PXL_20261103_043753272", "PXL_20261103_043812312"], "見えていない。奥の灯りまで細い柱間の幅の約 0.7 倍と見た（比率から）"),
     "depth": est(3.0, 0.9, ["PXL_20261103_043753272"], "室内の暗い箱の奥行き（見えていない）")},
]

# ---- f4 西北西面（北の翼の西面）
f4 = faces["f4"]
f4["name"] = "f4 西北西面（北の翼の西面。4 階、通用口）"
f4["note"] = "観察票 kansatsu_f4・数え kazoe_f4・測量 sokuryo_f4。南端の細い柱間（2.92 m）は margin_to"
f4["ground_offset"] = ground0("f4", ["PXL_20261103_043055592"], "1 階は道路の高さ（観察票 f4）。5 m 標高の帯は +0.64 m")
f4["top_z"] = dict(N_TOP, ref="blocks[north_wing].top_z")
f4["bays"] = {
    "count": meas(3, None, PH_F4 + ["PXL_20261103_043112969"], "数え係の値（両端が入る 2 枚で別々に 3）", unit="個",
                  ref=REP + "kazoe_f4.json#bays"),
    "pitch": est(6.145, 0.22, ["PXL_20261103_043055592", "PXL_20261103_043103134"], "測量係の値", ref=REP + "sokuryo_f4.json#bay_pitch"),
    "pier_width": est(0.87, 0.06, ["PXL_20261103_043055592"],
                      "測量係の柱型の縁（043055592 の北西の角の柱型 1320.3〜1472.2 px ÷ 174.1 px/m）", ref=REP + "sokuryo_f4.json#bay_pitch"),
    "margin_from": est(0.39, 0.08, ["PXL_20261103_043055592"],
                       "測量係の値から：北西の角の外の縁 1328 px と角の柱型の芯 1396.25 px の差 68 px ÷ 174.1 px/m",
                       ref=REP + "sokuryo_f4.json#bay_pitch"),
    "margin_to": est(2.925, 0.25, ["PXL_20261103_043055592"], "辺 21.75 − 0.39 − 3 × 6.145（南端の細い柱間。測量係の参考 2.92 m と同じ）"),
}
EV_F4 = ["PXL_20261103_043055592", "PXL_20261103_043103134", "PXL_20261103_043049890"]
f4["windows"] = [
    {"name": "1 階（柱型の間の濃い色の壁に窓と戸）",
     "z_bottom": est(0.6, 0.3, EV_F4, "見えていない（面の写真は z 0.64 m から上）。窓の下の壁を 0.6 m と見た（比率から）"),
     "z_top": est(3.4, 0.2, EV_F4, cov("f4", "1 階の開口の上端", 1148)),
     "width": est(5.15, 0.2, EV_F4, cov("f4", "開口の幅（柱型の間）", 206)),
     "recess": est(0.2, 0.06, EV_F4, "見えていない。濃い壁が柱型より少し奥（比率から）"), "rail": False},
    {"name": "2 階（横長）",
     "z_bottom": est(5.1, 0.2, EV_F4, cov("f4", "ガラスの下端", 1080)),
     "z_top": est(7.48, 0.2, EV_F4, cov("f4", "彫り込みの上端", 985)),
     "width": est(5.15, 0.2, EV_F4, cov("f4", "開口の幅", 206)), "recess": dict(REC, evidence=EV_F4),
     "spandrel": {"height": est(0.75, 0.15, EV_F4, cov("f4", "ガラスの下の濃い板", 30)),
                  "depth": est(0.0, 0.05, EV_F4, "彫り込みの中（観察票 f4）"), "material": "dark", "in_recess": True}},
    {"name": "3 階（横長）",
     "z_bottom": est(9.23, 0.2, EV_F4, cov("f4", "ガラスの下端", 915)),
     "z_top": est(11.48, 0.2, EV_F4, cov("f4", "彫り込みの上端", 825)),
     "width": est(5.15, 0.2, EV_F4, cov("f4", "開口の幅", 206)), "recess": dict(REC, evidence=EV_F4),
     "spandrel": {"height": est(0.75, 0.15, EV_F4, cov("f4", "ガラスの下の濃い板", 30)),
                  "depth": est(0.0, 0.05, EV_F4, "彫り込みの中（観察票 f4）"), "material": "dark", "in_recess": True}},
    {"name": "4 階（平らな帯に窓 2 つずつ）",
     "z_bottom": est(13.65, 0.2, EV_F4, cov("f4", "窓の下端", 738)),
     "z_top": est(15.43, 0.2, EV_F4, cov("f4", "窓の上端", 667)),
     "width": est(6.06, 0.25, EV_F4, cov("f4", "1 柱間の窓 2 つの中心の間隔", 121) + " の 2 倍"),
     "gap": est(0.93, 0.1, EV_F4, cov("f4", "窓の幅", 84) + "（2.1 m）を中心の間隔 3.03 m から引いた"),
     "per_bay": 2, "recess": est(0.15, 0.05, EV_F4, "見えていない。平らな帯にあけた窓（比率から）"), "rail": False},
]
f4["glazing"] = [
    {"name": f"南端の細い柱間の {fl} 階の窓", "along0": est(19.3, 0.2, EV_F4, cov("f4", "窓の左の縁", 772)),
     "along1": est(21.2, 0.2, EV_F4, cov("f4", "窓の右の縁", 848)),
     "z_bottom": est(zb, 0.2, EV_F4, cov("f4", "窓の下端", round((32.1 - zb) * 40))),
     "z_top": est(zt, 0.2, EV_F4, cov("f4", "窓の上端", round((32.1 - zt) * 40))),
     "inset": est(0.15, 0.05, EV_F4, "見えていない（比率から）"), "depth": est(3.0, 0.9, EV_F4, "室内の暗い箱の奥行き（見えていない）")}
    for fl, zb, zt in ((4, 13.65, 15.43), (2, 5.1, 7.0))]

# ---- f5 西北西面（階段室＋南の翼の西の妻）
f5 = faces["f5"]
f5["name"] = "f5 西北西面（北の部分：階段室と開いた踊り場、南の部分：南の翼の西の妻）"
f5["note"] = (f"観察票 kansatsu_f5・数え kazoe_f5・測量 sokuryo_f5。from から {F5_SPLIT} m までが北の部分（f8 の線を西へ延ばした点で分けた）。"
              "妻は無地で、北寄りにスリット窓が各階 1 つ（「各階に小窓 1 つ」→ bays count 1。柱割りではない）")
f5["ground_offset"] = ground0("f5", ["PXL_20261103_043004629"], "5 m 標高の帯は −0.61 m（f6 より 0.74 m 高い）")
f5["top_z"] = dict(S_TOP, method="南の翼の笠木（妻の上端）", note="", ref="blocks[south_wing].top_z")
EV_F5 = ["PXL_20261103_043004629", "PXL_20261103_043026097"]
f5["bays"] = {
    "count": est(1, None, EV_F5, "小窓の列 1 つ（数え係：柱割りは無い。スリット窓が各階 1 つ。data/reports/honbu/kazoe_f5.json#floors）", unit="個"),
    "pitch": est(3.6, 0.3, EV_F5, cov("f5", "スリット窓の幅 3.2 m（600〜727 px）に左右 0.2 m を足した窓の枠", 127)),
    "pier_width": est(0.4, 0.1, EV_F5, "窓の枠の左右（比率から）"),
    "margin_from": est(14.8, 0.3, EV_F5, cov("f5", "スリット窓の左の縁", 600) + "（15.0 m）− 0.2 m"),
    "margin_to": est(round(22.13 - 14.8 - 3.6, 2), 0.4, EV_F5, "辺 22.13 − 14.8 − 3.6"),
}
f5["windows"] = [
    {"name": f"{fl} 階（スリット窓）",
     "z_bottom": est(zb, 0.2, EV_F5, cov("f5", "スリット窓の下端", round((32.1 - zb) * 40))),
     "z_top": est(zt, 0.2, EV_F5, cov("f5", "スリット窓の上端", round((32.1 - zt) * 40))),
     "width": est(3.2, 0.2, EV_F5, cov("f5", "スリット窓の幅", 127)),
     "recess": est(0.4, 0.12, EV_F5, "見えていない。観察票 f5：深い彫り込み（比率から）"), "rail": False}
    for fl, zb, zt in ((2, 7.0, 7.8), (3, 11.2, 12.0), (4, 15.4, 16.15), (5, 19.55, 20.35))]
f5["glazing"] = [
    {"name": "防災センターの入口（ガラス戸）", "along0": est(15.0, 0.2, EV_F5, cov("f5", "入口の左の縁", 600)),
     "along1": est(18.25, 0.2, EV_F5, cov("f5", "入口の右の縁", 730)),
     "z_bottom": est(0.0, 0.1, EV_F5, "地面から（面の写真の下端）"),
     "z_top": est(2.1, 0.5, EV_F5, "見えていない（上が影）。観察票 f5：高さは 1 階の高さ 4.8 m の約 2/3 … 戸の部分は約 2.1 m と見た（比率から）"),
     "inset": est(0.3, 0.1, EV_F5, "見えていない（比率から）"), "depth": est(3.0, 0.9, EV_F5, "室内の暗い箱の奥行き（見えていない）")},
    {"name": "妻の 1 階のやや大きい窓", "along0": est(15.0, 0.2, EV_F5, cov("f5", "窓の左の縁", 600)),
     "along1": est(16.8, 0.2, EV_F5, cov("f5", "窓の右の縁", 672)),
     "z_bottom": est(2.6, 0.2, EV_F5, cov("f5", "窓の下端", 1180)), "z_top": est(4.0, 0.2, EV_F5, cov("f5", "窓の上端", 1125)),
     "inset": est(0.3, 0.1, EV_F5, "見えていない（比率から）"), "depth": est(3.0, 0.9, EV_F5, "室内の暗い箱の奥行き（見えていない）")},
]

# ---- f7 東南東面（南の翼の東の妻）
f7 = faces["f7"]
f7["name"] = "f7 東南東面（南の翼の東の妻。無地に小窓 2 列、1 階はピロティと東西の通路）"
f7["note"] = ("観察票 kansatsu_f7・数え kazoe_f7・測量 sokuryo_f7。無地の壁に小窓が各階 2 つ（柱割りではなく小窓の並びを bays count 2 で表した）。"
              "f7 に登録した写真は無いので、窓の位置は 043612124 の正面化（測量係）で角から角を 1 とした比から")
f7["top_z"] = dict(S_TOP, method="南の翼の笠木（同じ塊）", note="", ref="blocks[south_wing].top_z")
EV_F7 = ["PXL_20261103_043612124", "PXL_20261103_043623454"]
M_F7 = ("out/rectified/honbu/PXL_20261103_043612124_front.jpg（235 px/m）で角から角 4036 px に対する比 × PLATEAU の辺 15.81 m"
        "（写真の角は超広角の端で ±8%）")
f7["bays"] = {
    "count": est(2, None, EV_F7, "小窓の列 2 つ（数え係：無地に小窓 2 列、kazoe_f7.json#floors）", unit="個"),
    "pitch": est(2.235, 0.3, EV_F7, M_F7 + "：左の窓の中心 0.527・右の窓の中心 0.669"),
    "pier_width": est(0.4, 0.12, EV_F7, "窓の間の壁（比率から）"),
    "margin_from": est(7.22, 0.6, EV_F7, M_F7 + "：左の窓の中心 8.335 m − 間隔の半分"),
    "margin_to": est(round(15.81 - 7.22 - 2 * 2.235, 2), 0.7, EV_F7, "辺 15.81 − 7.22 − 2 × 2.235"),
}
F7_FP = est(4.245, 0.45, ["PXL_20261103_043612124"], "測量係の値（f7 の小窓の上端の間隔）", ref=REP + "sokuryo_f7.json#floor_pitch")
f7["windows"] = []
for k, (nm, wd, bays) in enumerate((("左（やや横長）", 1.83, [1]), ("右（細い縦長）", 0.9, [2]))):
    for fl in (2, 3, 4, 5):
        zb = round(6.94 + (fl - 2) * 4.245, 2)
        f7["windows"].append({
            "name": f"{fl} 階の小窓 {nm}",
            "z_bottom": est(zb, 0.45, EV_F7, "2 階の窓の下端 6.94 m（043612124 の正面化で 1 階の開口の上端から 2.14 m、開口の上端は f6 と同じ 4.80 m）"
                            "＋階高 4.245 m ×（階−2）", ref="faces.f7.windows#2階"),
            "z_top": est(round(zb + 1.76, 2), 0.45, EV_F7, "窓の高さ 1.76 m（043612124 の正面化で 85 px ÷ 49 px/m の縮小）"),
            "width": est(wd, 0.25, EV_F7, M_F7), "bays": bays,
            "recess": est(0.4, 0.12, EV_F7, "見えていない。観察票 f7：深い彫り込み（比率から）"), "rail": False})

# ---- f1 / f2 / f3（北端）
f1, f2, f3 = faces["f1"], faces["f2"], faces["f3"]
f1["name"] = "f1 北北東面（北端の東寄り。建物の壁ではなく外階段の外面と読む）"
f1["note"] = ("観察票 kansatsu_f1（mid）：外階段の外面。外階段は parts の stair_switchback（f3 の線から北へ。along は負）。"
              "建物の北の壁は f2 の奥行き 2.72 m ぶん引っ込む（塊 north_wing は外階段の長方形を含まない）。窓なし")
f1["top_z"] = dict(N_TOP, ref="blocks[north_wing].top_z")
f2["name"] = "f2 西北西面（北端の段差。外階段の西の側面と読む）"
f2["note"] = "観察票 kansatsu_f2（mid）：外階段の西の側面。窓なし。f1 の続き"
f2["top_z"] = dict(N_TOP, ref="blocks[north_wing].top_z")
f3["name"] = "f3 北北東面（北端の西寄り。配管と室外機の無地の壁）"
f3["note"] = "無地（観察票 kansatsu_f3：窓も柱型も無い。数え kazoe_f3 も not_applicable）。北の翼の北の壁は f3 の線を東へ延ばした所（外階段の奥）まで続く"
f3["top_z"] = dict(N_TOP, ref="blocks[north_wing].top_z")
f3["bands"] = [{"name": "上端の細い濃い笠木の帯", "color": "#5a5048",
                "z_bottom": est(17.55, 0.6, ["PXL_20261103_043123901"], "北の翼の笠木の上端 17.75 m から帯の厚み 0.2 m（観察票 f3：上端に細い濃い笠木、比率から）",
                                ref="blocks[north_wing].top_z"),
                "z_top": dict(N_TOP, ref="blocks[north_wing].top_z"),
                "depth": est(0.03, 0.02, ["PXL_20261103_043123901"], "壁とほぼ同じ面（比率から）")}]


# --- 塊 -------------------------------------------------------------------------------------
TOWER_AB = (2.0, 12.5, 9.6, B_S)
blocks = [
    {"id": "south_wing", "name": "南の翼（5 階）", "polygon": [P_SW, list(V7), list(V8), list(V9), list(V0)],
     "top_z": dict(S_TOP), "floors": est(5, None, ["PXL_20261103_042900775", "PXL_20261104_074357308", "PXL_20261103_042721424"],
                                          "数え係の値（f6・f8・f7・f5 で 5）", unit="階", ref=REP + "kazoe_f6.json#floors"),
     "floor_pitch": dict(S_FP),
     "method": "足元：PLATEAU の f6・f7・f8 と、f8 の線を西へ延ばして f5 に当たる点 P（観察票 roof：南の翼と北の翼は別の塊）"},
    {"id": "north_wing", "name": "北の翼（4 階）",
     "polygon": [list(V0), Q_NE, list(V3), list(V4), list(V5), list(V6), P_SW],
     "top_z": dict(N_TOP), "floors": est(4, None, ["PXL_20261103_043753272", "PXL_20261103_043055592"],
                                         "数え係の値（f0・f4 で 4）", unit="階", ref=REP + "kazoe_f0.json#floors"),
     "floor_pitch": dict(N_FP),
     "method": ("足元：PLATEAU の f0・f3・f4 と、f3 の線を東へ延ばして f0 に当たる点 Q。外階段の長方形（Q・v1・v2・v3、8.55 × 2.72 m）は"
                "外階段なので塊に入れない（観察票 f1・f2：外階段の外面と側面と読む、mid。f0 の写真の辺 28.3 m とも合う）")},
    {"id": "tower", "name": "塔屋（両翼のつなぎ目）", "polygon": rect_ab(*TOWER_AB),
     "top_z": dict(TOWER_TOP),
     "method": ("局所の座標 a 2.0〜12.5・b 9.6〜15.81 m（± 3 m の目安）。本部棟屋上で東の面が南の翼の屋上から、北の面が北の翼の屋上から立つ"
                "（北の面は南の翼の北の壁の線）。観察票 roof：両翼のつなぎ目、西面から入隅の近くまで。大きさは航空写真の明るい四角 約 10.4 × 6 m。"
                "東西の位置は recorder photos roof の大きな暗い所（北の翼の屋上の塔の影、out/honbu/roof.json large_regions）の中心 a 6.7 m に合わせた。"
                "航空写真の明るい四角そのものは倒れ込みを戻すと a 9〜20 m（中庭の上）に出て合わない（未解決、requests）。"
                "検収係（2026-11-07）：この塊の輪郭を塔屋の高さ 31.0 m の倒れ込み（率 0.206、6.4 m）でずらして航空写真（gsi:aerial_z18）に"
                "重ねると、明るい四角とその内側の腰壁の影に約 1 m 以内で重なる（設計係の未解決は塊を分ける前の roof.json の読み）。"
                "f5 の面の写真でも笠木の上に塔屋の西の面が見える（s 7.6〜15 m、043004629・043026097）。西の面は f5 から約 2 m 奥と読める"
                "（低い視点の投影で上端が z 26.9 に下がる量から。確かめていない）")},
]
b["blocks"] = blocks

# --- 棟全体の値 -------------------------------------------------------------------------------
b["floor_count"] = meas(5, None, ["PXL_20261103_042900775", "PXL_20261104_074357308", "PXL_20261103_042721424"],
                        "数え係の値（南の翼。北の翼は 4）", unit="階", ref=REP + "kazoe_f6.json#floors")
b["floor_pitch"] = dict(S_FP, note="南の翼。北の翼は 3.938 m（blocks[north_wing].floor_pitch）")
b["parapet_top"] = dict(S_TOP, note="南の翼の笠木。塔屋の上端は blocks[tower].top_z（31.0 m）")
b["footprint"]["method"] += "。面の id はこの辺の番号で固定。塊は blocks（南の翼・北の翼・塔屋）"


# --- 部品 -------------------------------------------------------------------------------------
parts: list[dict] = []


def comp(name, face, ev, tol, method, prims, along=0.0):
    d = {"type": "composite", "name": name}
    if face:
        d["face"] = face
        d["along"] = along
    d.update({"status": "estimated", "evidence": list(ev), "tol": tol, "method": method, "primitives": prims})
    return d


def box(name, s, d, z, mat="concrete", cut=False):
    p = {"shape": "box", "name": name, "s": [round(s[0], 3), round(s[1], 3)], "d": [round(d[0], 3), round(d[1], 3)],
         "z": [round(z[0], 3), round(z[1], 3)], "material": mat}
    if cut:
        p["cut"] = True
    return p


def prism_ab(name, pts_ab, z, mat="concrete", cut=False):
    p = {"shape": "prism", "name": name, "polygon_xy": [world(a, bb) for a, bb in pts_ab],
         "z": [round(z[0], 3), round(z[1], 3)], "material": mat}
    if cut:
        p["cut"] = True
    return p


def strip_ab(name, p0, p1, t, z, mat="tile"):
    """(a,b) の線分 p0→p1 の左側（内側）に厚さ t の壁。"""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    L = math.hypot(dx, dy)
    nx, ny = -dy / L * t, dx / L * t
    return prism_ab(name, [p0, p1, (p1[0] + nx, p1[1] + ny), (p0[0] + nx, p0[1] + ny)], z, mat)


# 1. 正面玄関の軒下（f6 の 2・3 柱間）
EV_PORCH = ["PXL_20261103_042900775", "PXL_20261104_074357308", "PXL_20261103_080542963"]
PORCH_Z0 = 0.30
parts.append({
    "type": "porch", "name": "正面玄関の軒下（2・3 柱間）", "face": "f6",
    "along": est(5.415, 0.1, EV_PORCH, "1 本目の柱型の左の縁（5.88 − 0.465。柱割りから）", ref="faces.f6.bays"),
    "width": est(11.76, 0.2, EV_PORCH, "1 本目の柱型の左の縁から 3 本目の柱型の右の縁まで（2 × 5.418 + 0.93）", ref="faces.f6.bays"),
    "depth": est(1.5, 0.45, EV_PORCH, "見えていない。観察票 f6：引っ込みは柱型の幅（0.93 m）の 1〜2 倍（080542963 の斜めの目安）、中を取った"),
    "height": est(4.5, 0.25, EV_PORCH, "1 階の開口の上端 4.80 m（sokuryo_f6.json#first_floor）− 床の高さ 0.30 m", ref=REP + "sokuryo_f6.json#first_floor"),
    "z0": est(PORCH_Z0, 0.08, EV_PORCH, "玄関の床は 2 段上がる（観察票 f6、080542963）× 1 段 0.15 m"),
    "glass": {"along0": est(0.93, 0.1, EV_PORCH, "柱型の内側から"), "along1": est(10.83, 0.1, EV_PORCH, "反対の柱型の内側まで"),
              "depth": est(0.3, 0.1, EV_PORCH, "奥のガラスの壁（黒い方立の格子）。ガラスの箱の奥行きは見えていない（比率から）"),
              "mullion_pitch": est(0.75, 0.2, EV_PORCH, "080542963・042900775 で 1 柱間（4.4 m）にガラスの縦の格子が 6 区画ほど"),
              "transom_pitch": est(1.0, 0.3, EV_PORCH, "080542963 で高さ 4.5 m に横の格子 4〜5 段（比率から）")},
    "columns": {"width": dict(PIER6), "material": "tile"},
})
# 玄関の真ん中の柱（2 本目の柱型が地面まで下りる）
parts.append(comp("玄関の中の柱（2 本目の柱型）", "f6", EV_PORCH, 0.1,
                  "柱割りの 2 本目（5.88 + 5.418 = 11.30 m）の柱型が玄関の前で地面まで下りる（042900775・080542963）。幅は柱型と同じ",
                  [box("column", (11.30 - 0.465, 11.30 + 0.465), (-0.93, 0.0), (0.0, 4.8), "tile")]))
# 2. 玄関前の段・植え込み・銘板
M_BASE = "out/rectified/honbu/f6_042900775+042734715_stitched.jpg（135.96 px/m）で"
parts.append({"type": "steps", "name": "玄関の段（3 柱間の右）", "face": "f6",
              "along": est(14.6, 0.25, ["PXL_20261103_042900775", "PXL_20261103_080542963"], M_BASE + " 段の左の縁（植え込みの右）"),
              "width": est(1.6, 0.3, ["PXL_20261103_042900775", "PXL_20261103_080542963"], M_BASE + " 段の幅（3 本目の柱型の左まで）"),
              "depth": est(0.7, 0.2, ["PXL_20261103_080542963"], "見えていない。段 2 段 × 踏み面 0.35 m（080542963 の段の見え方から）"),
              "count": est(2, None, ["PXL_20261103_080542963"], "080542963 で 2 段", unit="段"),
              "rise": est(0.15, 0.04, ["PXL_20261103_080542963"], "080542963 で 1 段が右の壁のタイル 2 段（0.070 m × 2）ほど"),
              "material": "stone"})
parts.append({"type": "planter", "name": "玄関前の植え込み（2 柱間）", "face": "f6",
              "along": est(6.4, 0.25, ["PXL_20261103_042900775"], M_BASE + " 植え込みの左の縁（1 本目の柱型の右）"),
              "width": est(4.6, 0.3, ["PXL_20261103_042900775", "PXL_20261103_042909968", "PXL_20261103_042946748"],
                           M_BASE + " 2 柱間の柱型の間（4.4 m）。検収係が recheck の右の縁（042909968 +0.30・042946748 +0.19 m）で 4.6 m に直した"),
              "depth": est(0.8, 0.25, ["PXL_20261103_042900775", "PXL_20261103_080542963"], "見えていない。段 2 段ほどの奥行きと見た（比率から）"),
              "offset": est(0.0, 0.2, ["PXL_20261103_042900775"], "面の線に沿う"),
              "height": est(0.3, 0.08, ["PXL_20261103_080542963"], "観察票 f6：高さは段 2 段ほど"), "wall": est(0.15, 0.05, ["PXL_20261103_080542963", "PXL_20261103_043259125"], "縁の厚みは段の高さほど（080542963・043259125、比率から）"), "material": "stone"})
parts.append({"type": "planter", "name": "玄関前の植え込み（3 柱間の左、銘板の石）", "face": "f6",
              "along": est(11.8, 0.25, ["PXL_20261103_042900775"], M_BASE + " 2 本目の柱型の右の縁"),
              "width": est(2.8, 0.3, ["PXL_20261103_042900775", "PXL_20261103_080542963"], M_BASE + " 段の左の縁まで"),
              "depth": est(1.2, 0.35, ["PXL_20261103_080542963"], "080542963 で植え込みの奥行きが幅の約 0.4（比率から）"),
              "offset": est(-0.6, 0.3, ["PXL_20261103_080542963"], "080542963 で植え込みは柱型の線より少し奥に入る（比率から）"),
              "height": est(0.3, 0.08, ["PXL_20261103_080542963"], "観察票 f6：高さは段 2 段ほど"), "wall": est(0.15, 0.05, ["PXL_20261103_080542963", "PXL_20261103_043259125"], "縁の厚みは段の高さほど（080542963・043259125、比率から）"), "material": "stone"})
parts.append({"type": "nameplate", "name": "『名城大学 本部』の石の銘板", "face": "f6",
              "along": est(12.5, 0.3, ["PXL_20261103_042900775", "PXL_20261103_080542963"], M_BASE + " 石の左の縁"),
              "offset": est(0.0, 0.3, ["PXL_20261103_080542963"], "植え込みの中（面の線のあたり）"),
              "width": est(1.0, 0.3, ["PXL_20261103_042900775"], M_BASE + " 石の幅（手前に出ているので大きめに写る）"),
              "height": est(1.0, 0.15, ["PXL_20261103_042900775", "PXL_20261103_042909968", "PXL_20261104_074357308", "PXL_20261103_080542963"],
                            RECHECK1 + " で石の上端が 042900775 −0.21・042909968 −0.18・074357308 −0.21 m（042946748 −0.03）。"
                            "設計係の 1.2 m（観察票 f6：1 階の高さの約 1/4）から 0.2 m 下げた（検収係）"),
              "thickness": est(0.3, 0.09, ["PXL_20261103_080542963"], "080542963 で石の厚みが幅の約 0.3（比率から）"),
              "z0": est(0.3, 0.1, ["PXL_20261103_080542963"], "植え込みの土の上"), "material": "dark"})

# 3. 車道の通り抜け（f6 の 4〜6 柱間 → f8）。面をまたぐので f6 の座標で 1 つに
EV_ROAD = ["PXL_20261103_042900775", "PXL_20261104_074357308", "PXL_20261103_043320836", "PXL_20261105_064609316"]
DEPTH_S = B_S
road = [
    box("front_open", (17.18, 32.50), (-2.0, 0.3), (0.0, 4.8), cut=True),
    box("road", (22.60, 32.50), (-(DEPTH_S + 0.4), 0.3), (0.0, 4.8), cut=True),
    box("lobby_glass", (17.25, 22.55), (-1.99, -1.96), (0.0, 4.75), "glass"),
    box("lobby_room", (17.25, 22.55), (-2.0, -1.995), (0.0, 4.75), "interior"),
]
for k, (s, dd) in enumerate(((22.13, 5.4), (22.13, 10.8), (32.97, 5.4), (32.97, 10.8))):
    road.append(box(f"inner_column{k}", (s - 0.4, s + 0.4), (-dd - 0.4, -dd + 0.4), (0.0, 4.8), "tile"))
parts.append(comp("車道の通り抜け（南の翼の 1 階を f6 から f8 へ）", "f6", EV_ROAD, 0.5,
                  "f6 の面の線の開口は 3 本目の柱型の右（17.18 m）から 6 本目の柱型の左（32.50 m）まで、4/5・5/6 の柱型は地面まで下りない"
                  "（数え係 kazoe_f6 exceptions、042900775・064609316）。高さは 1 階の開口の上端 4.80 m（sokuryo_f6.json#first_floor）。"
                  "車道は 5・6 柱間（観察票 f6）を南の翼の奥行き 15.81 m 抜けて f8 へ（043320836）。4 柱間は奥のガラスの壁（観察票 f6、"
                  "奥行きは見えていない。柱型の幅の約 2 倍と見た）。中の柱は 4・6 本目の柱の線に柱間 5.418 m おき（位置は見えていない、比率から ±1.5 m）",
                  road))
# 天井の梁は検収係が composite に置き換えた（2026-11-07）：ceiling_beams は両端（面の線の上）にも梁を置き、f6 の面の線で
# 1 階の開口の上端（4.80 m）の下に 0.5 m 垂れる梁が出ていた。recheck で面の線の梁の下端が 074357308 +0.46・064601738 +0.36・
# 064609316 +0.46・064614185 +0.41 m（写真の縁は開口の上端 4.80 m のまま）。中の梁だけを、設計係の中の柱の線（奥 5.4・10.8 m）と
# その間（2.7 m おき）に置く
ROAD_BEAMS = [box(f"beam_d{k}", (22.6, 32.5), (-dd - 0.2, -dd + 0.2), (S_1F["value"] - 0.5, S_1F["value"]), "white")
              for k, dd in enumerate((2.7, 5.4, 8.1, 10.8, 13.5))]
parts.append(comp("車道の通り抜けの天井の梁", "f6", ["PXL_20261103_043320836", "PXL_20261104_074357308", "PXL_20261105_064609316"], 0.8,
                  "043320836 で天井の梁が柱間（5.418 m）に 2 本ほど、面に平行（比率から）。車道の西の縁 22.6〜東の縁 32.5 m、天井は 1 階の"
                  "開口の上端 4.80 m（sokuryo_f6.json#first_floor）、梁の幅 0.4・成 0.5 m（比率から）。奥へ 2.7 m おきに 5 本"
                  "（両端の面の線の上には置かない：" + RECHECK1 + " で面の線に梁の縁が無い。検収係）",
                  ROAD_BEAMS))

# 4. 東のピロティ（f6 の 7〜9 柱間と f7 の下）と f7 の東西の通路
EV_PIL = ["PXL_20261103_042721424", "PXL_20261103_042822099", "PXL_20261105_064600203", "PXL_20261103_043612124"]
PIL_Z = 1.30
pil = [
    box("podium_open", (33.44, 49.72 + 0.3), (-5.46, 0.3), (PIL_Z, 4.8), cut=True),
    box("ew_passage", (36.0, 49.72 + 0.3), (-9.2, -6.2), (PIL_Z, 4.8), cut=True),
]
for k, s in enumerate((38.39, 43.80)):
    pil.append(box(f"front_column{k}", (s - 0.465, s + 0.465), (-0.93, 0.0), (PIL_Z, 4.8), "tile"))
pil.append(box("corner_column", (48.79, 49.72), (-0.93, 0.0), (PIL_Z, 4.8), "tile"))
pil.append(box("f7_column", (48.6, 49.72), (-6.2, -5.46), (PIL_Z, 4.8), "tile"))
pil.append(box("equipment_room", (44.6, 46.8), (-5.46, -3.6), (PIL_Z, 3.9), "tile"))
parts.append(comp("東のピロティと東西の通路（f6 の 7〜9 柱間、f7 の 1 階）", "f6", EV_PIL, 0.6,
                  "f6 の 7〜9 柱間（6 本目の柱型の右 33.44 m から南東の角まで）の 1 階が開く（観察票 f6、042721424）。床は広場から段 8〜9 段"
                  "（観察票 f6・044712412）× 0.15 m ≒ 1.30 m。天井は 1 階の開口の上端 4.80 m。奥行き（f7 に沿って）は 043612124 の正面化で"
                  "南からピロティ 0〜5.46 m・太い煉瓦の柱 5.46〜6.2 m・東西の通路 6.2〜9.2 m（角から角を 15.81 m に当てた比、±8%）。"
                  "東西の通路は西のピロティの中の大階段まで（043612124 で奥へ抜ける）。中の煉瓦の閉じた箱（設備室か、観察票 f6）は"
                  "042721424 で 9 柱間の奥の左に見える。大きさは柱間の約 0.4 × 0.35（比率から、± 30%）",
                  pil))
# 検収係：ceiling_beams（grid）は梁を 33.44〜49.72 m を等分した位置（33.44・35.77・38.09…）と面の線の上に置き、柱の線（38.39・43.80）と
# 最大 1.06 m ずれていた。recheck の梁の縁は 042721424・042734715・064600203・064601738・064609316・064614185 で柱の縁
# （37.9・43.3・44.3 m）に寄る（例：beam_a4 右端 +0.40 m が 7 枚）。観察票の「間隔は柱間の約 1/2」を柱割りの線（32.97 + 2.709k）に
# 合わせて置き直し、面の線と奥の壁の上の梁は外した
PB_S = [round(32.97 + 2.709 * k, 3) for k in range(1, 6)]          # 35.68・38.39・41.10・43.80・46.51
# 2 回目（検収係）：柱間の真ん中の小梁（35.68・41.10・46.51）は下端が recheck 2 回目で 042721424 +0.24・064600203 +0.45・
# 064601738 +0.36・064609316 +0.31・064614185 +0.37 m（beam_s0）→ 成 0.6 → 0.3 m（s2・s4 は縁が弱く、同じ小梁と見た）。柱の線の梁は 0.6 m のまま
PIL_BEAMS = [box(f"beam_s{k}", (s0 - 0.225, s0 + 0.225), (-5.46, 0.0), (S_1F["value"] - (0.6 if k % 2 else 0.3), S_1F["value"]), "white")
             for k, s0 in enumerate(PB_S)]
PIL_BEAMS += [box(f"beam_d{k}", (33.44, 49.72), (-dd - 0.225, -dd + 0.225), (S_1F["value"] - 0.6, S_1F["value"]), "white")
              for k, dd in enumerate((1.82, 3.64))]
parts.append(comp("東のピロティの天井の梁の格子", "f6", EV_PIL + ["PXL_20261103_044712412"], 0.6,
                  "観察票 f6：梁の格子の間隔は柱間（5.418 m）の約 1/2。面に直角の梁は柱割りの線と柱間の真ん中（35.68・38.39・41.10・43.80・"
                  "46.51 m）、面に平行の梁はピロティの奥行き 5.46 m の 3 等分の 2 本。天井は 1 階の開口の上端 4.80 m、梁の幅 0.45・成 0.6 m"
                  "（044712412・043612124、比率から）。検収係が ceiling_beams から置き換えた（面の線と柱の線とのずれ。上のコメント）",
                  PIL_BEAMS))
# ピロティの中の大階段（車道の東の脇からピロティの床へ）
EV_IN = ["PXL_20261103_044712412", "PXL_20261103_044651355", "PXL_20261103_043320836"]
inner = []
n_in = 9
for k in range(n_in):
    s0 = 32.6 + k * 0.35
    inner.append(box(f"tread{k}", (s0, 33.44 + 2.0), (-(DEPTH_S - 0.5), -1.0), (0.0, round(PIL_Z * (k + 1) / n_in, 3)), "brick"))
parts.append(comp("ピロティの中の大階段（車道の東の脇から）", "f6", EV_IN, 0.6,
                  "044712412（車道の側から東を見る）で幅の広い段が 9 段ほどでピロティの床へ（観察票 f6：8〜9 段）。段の高さはピロティの床 1.30 m を 9 等分、"
                  "踏み面 0.35 m（比率から）。南北の長さは南の翼の奥行きの中（043320836 で北の端は外の段に連なる）。位置は車道の東の縁から（± 1 m）",
                  inner))
parts.append(comp("外階段の 2 階の踊り場の下の鉄柵の門", "f3", ["PXL_20261103_043136840", "PXL_20261103_043150092"], 0.4,
                  "観察票 f1：2 階の踊り場の下は開いていて、通路の入口に鉄柵の門。高さは 1 階の高さ（約 4.3 m）の約 1/3。幅は踊り場の長さ 1.5 m と見た"
                  "（043136840、比率から）。足元は f3 の前の地面（5 m 標高の帯 +0.92 m）",
                  [box("gate", (-F3_STAIR + 0.6, -F3_STAIR + 2.1), (2.62, 2.68), (0.92, 2.32), "metal")]))
ewb = [box(f"beam{k}", (s0 - 0.225, s0 + 0.225), (-9.2, -6.2), (4.2, 4.8), "white") for k, s0 in enumerate([36.0 + 2.7 * i for i in range(6)])]
parts.append(comp("f7 の東西の通路の天井の梁", "f6", ["PXL_20261103_043612124"], 0.5,
                  "043612124 で f7 の中央の開口から西へ抜ける通路の天井に梁の格子（観察票 f7）。間隔はピロティの梁と同じ柱間の約 1/2（2.7 m）、"
                  "幅 0.45・成 0.6 m（比率から）。通路の範囲は上の『東のピロティと東西の通路』と同じ",
                  ewb))
# 南東の大階段（f6 の 8・9 柱間の前。東の続きと f7 沿いの階段は棟の間なので敷地へ：requests）
parts.append({"type": "steps", "name": "南東の大階段（8・9 柱間の前）", "face": "f6",
              "along": est(38.85, 0.4, ["PXL_20261103_042721424", "PXL_20261103_042734715"], "7 本目の柱型の右の縁（柱割りから。042721424 で段の西の端）", ref="faces.f6.bays"),
              "width": est(10.87, 0.5, ["PXL_20261103_042721424"], "南東の角まで（観察票 f6：8〜9 柱間の前から角の外まで。角の外は敷地の物）"),
              "depth": est(2.8, 0.8, ["PXL_20261103_042721424", "PXL_20261105_064600203"], "段 8 段 × 踏み面 0.35 m（042721424 で踏み面が蹴上げの約 2.3 倍、比率から）"),
              "count": est(8, None, ["PXL_20261103_042721424", "PXL_20261105_064600203"], "観察票 f6：段は 8 段ほど", unit="段"),
              "rise": est(0.15, 0.04, ["PXL_20261103_042721424"], "ピロティの床 1.30 m を 8〜9 段で（比率から）"),
              "z0": est(round(PIL_Z - 8 * 0.15, 2), 0.3, ["PXL_20261103_042721424"], "ピロティの床から 8 段下りた広場（f6 の西の車道とほぼ同じ高さ）"),
              "sides": ["from"], "edge": "straight", "material": "brick"})

# 5. ピロティの机とベンチ、掲示板のガラスの箱
EV_TB = ["PXL_20261103_042721424", "PXL_20261105_064600203", "PXL_20261103_043613678"]
tb = []
for k, s in enumerate((40.2, 45.6)):
    tb.append(box(f"table{k}", (s, s + 1.8), (-3.2, -2.4), (PIL_Z, PIL_Z + 0.72), "concrete"))
    tb.append(box(f"bench{k}a", (s, s + 1.8), (-3.65, -3.35), (PIL_Z, PIL_Z + 0.42), "concrete"))
    tb.append(box(f"bench{k}b", (s, s + 1.8), (-2.25, -1.95), (PIL_Z, PIL_Z + 0.42), "concrete"))
parts.append(comp("ピロティの机とベンチ（2 組）", "f6", EV_TB, 0.5,
                  "042721424 で 8 柱間と 9 柱間の奥寄りに机とベンチの組が 1 つずつ（観察票 f6：2〜3 組）。机の長さは柱間の約 1/3（1.8 m）、"
                  "高さは人の腰（比率から、± 30%）。机のアセット table_long は決定 D-0003 が今の台帳に無いので箱（requests kind asset）",
                  tb))
parts.append(comp("ピロティの奥の掲示板（ガラスの箱、中身は写さない）", "f6", ["PXL_20261103_042822099", "PXL_20261103_042721424"], 0.4,
                  "042721424 で 9 柱間の奥の壁に掲示板のガラスの箱。高さは 1 階の高さの約 1/3（観察票 f6）、幅は柱間の約 0.35（比率から）",
                  [box("board", (46.9, 48.8), (-5.46, -5.3), (PIL_Z + 0.8, PIL_Z + 2.3), "glass"),
                   box("board_back", (46.9, 48.8), (-5.46, -5.45), (PIL_Z + 0.8, PIL_Z + 2.3), "dark")]))
parts.append(comp("f7 の北側の壁の掲示板（ガラスの箱、中身は写さない）", "f7", ["PXL_20261103_043612124", "PXL_20261103_043623454"], 0.4,
                  "043612124 で東西の通路の北の壁に掲示板のガラスの箱（観察票 f7）。正面化の比で 9.6〜11.4 m（角から角を 15.81 m に当てた）、"
                  "高さは 1 階の高さの約 1/3",
                  [box("board", (9.6, 11.4), (0.0, 0.15), (PIL_Z + 0.8, PIL_Z + 2.4), "glass"),
                   box("board_back", (9.6, 11.4), (0.0, 0.01), (PIL_Z + 0.8, PIL_Z + 2.4), "dark")]))

# 6. 縦樋
def pipes(name, face, along, z0, z1, ev, method, count=1):
    return {"type": "pipes", "name": name, "face": face, "along": along, "count": est(count, None, ev, method, unit="本"),
            "radius": est(0.06, 0.02, ev, "樋の太さは柱型の幅の約 1/8（比率から）"), "z0": z0, "z1": z1}


parts.append(pipes("南東の角の縦樋（f7 側）", "f7", est(0.3, 0.2, PH_F6_E, "南東の角の f7 側（042721424・064600203）"),
                   est(0.0, 0.3, PH_F6_E, "地面まで"), dict(S_TOP, ref="blocks[south_wing].top_z"), PH_F6_E, "観察票 f6・f7：1 本、屋上から地面まで"))
parts.append(pipes("f8 の縦樋 1（2/3 の柱型の脇）", "f8", est(16.77, 0.3, EV_F8, cov("f8", "樋の位置", 670) + "（柱 2/3 の芯 5.95 + 2 × 5.408 = 16.77）"),
                   dict(DECK_Z, ref="parts[デッキ].z"), dict(S_TOP, ref="blocks[south_wing].top_z"), ["PXL_20261103_043320836"] + EV_F8,
                   "数え係：縦樋 2 本、屋上からデッキまで"))
parts.append(pipes("f8 の縦樋 2（5/6 の柱型の脇）", "f8", est(32.99, 0.3, EV_F8, cov("f8", "樋の位置", 1315)),
                   dict(DECK_Z, ref="parts[デッキ].z"), dict(S_TOP, ref="blocks[south_wing].top_z"), ["PXL_20261103_043320836"] + EV_F8,
                   "数え係：縦樋 2 本、屋上からデッキまで"))
parts.append(pipes("f4 の縦樋（2/3 の柱型の脇）", "f4", est(12.75, 0.2, EV_F4, cov("f4", "樋の位置", 510)),
                   est(0.0, 0.3, EV_F4, "地面まで"), dict(N_TOP, ref="blocks[north_wing].top_z"), EV_F4, "観察票 f4：1 本、屋上から地面まで"))
parts.append(pipes("f5 の縦樋（階段室と妻の境）", "f5", est(8.15, 0.3, EV_F5, cov("f5", "樋の位置", 326)),
                   est(0.0, 0.3, EV_F5, "地面まで"), est(20.0, 1.0, EV_F5, cov("f5", "樋の上端（見える所まで）", 484)), EV_F5,
                   "観察票 f5：階段室と妻の境に縦樋"))
parts.append(pipes("f5 の縦樋（南西の角）", "f5", est(21.85, 0.2, ["PXL_20261103_042927350", "PXL_20261103_043004629"], "南西の角の f5 側（観察票 f5）"),
                   est(0.0, 0.3, EV_F5, "地面まで"), dict(S_TOP, ref="blocks[south_wing].top_z"), ["PXL_20261103_042927350"], "観察票 f5：南西の角に縦樋"))
parts.append(pipes("f3 の縦の配管と縦樋", "f3", est(1.8, 0.4, ["PXL_20261103_043123901", "PXL_20261103_043112969"], "壁の西寄り（観察票 f3、043123901）"),
                   est(0.0, 0.3, PH_N, "地面まで"), dict(N_TOP, ref="blocks[north_wing].top_z"), ["PXL_20261103_043123901", "PXL_20261103_043112969"],
                   "観察票 f3：縦の配管 4〜5 本と縦樋（043123901 で 5 本数えた）", count=5))

# 7. 2 階のデッキ（f8 → 入隅で斜めに折れて f0 → 北東の角を回って外階段の 2 階の踊り場へ）
EV_DECK = ["PXL_20261103_043856844.MP", "PXL_20261103_043320836", "PXL_20261103_043753272", "PXL_20261103_043259125",
           "本部棟屋上", "PXL_20261103_043150092"]
D8, D0, CH = 2.7, 3.5, 3.0          # f8 沿いの出、f0 沿いの出、入隅の斜めの辺の脚
A_DECK_E = A_NE - 5.95             # 東端（東端の階段が上がり着く所）
zt = DECK_Z["value"]
# 腰壁の下端（床の下へ垂れる鼻先）。設計係は 0.85 m。検収係：recheck で腰壁の下端が 043314396 +0.19・043320836 +0.29・
# 043856844.MP +0.22・044049610.MP +0.24 m（043342958 だけ −0.31）高く写る → 0.65 m に直した
DECK_FASCIA = 0.65
outer = [(A_DECK_E, B_S + D8), (A_F0 + D0 + CH, B_S + D8), (A_F0 + D0, B_S + D8 + CH), (A_F0 + D0, B_STAIR)]
deck = [prism_ab("slab", [(A_F0, B_S), (A_DECK_E, B_S)] + outer + [(A_F0, B_STAIR)], (zt - 0.3, zt), "concrete")]
for k in range(len(outer) - 1):
    deck.append(strip_ab(f"parapet{k}", outer[k], outer[k + 1], 0.25, (zt - DECK_FASCIA, zt + 1.1), "tile"))
deck.append(strip_ab("parapet_n", (A_F0 + D0, B_STAIR), (A_F0, B_STAIR), 0.25, (zt - DECK_FASCIA, zt + 1.1), "tile"))
cols = []
for k in range(6):                    # f8 沿い：柱割りの線（車道の真ん中の 1 本は無い：043320836）
    s8 = 5.95 + k * 5.408
    a = A_NE - s8
    if abs(a - 27.55) < 0.6:
        continue
    if a > A_F0 + D0 + CH:
        cols.append((a, B_S + D8 - 0.4))
for k in range(5):                    # f0 沿い
    cols.append((A_F0 + D0 - 0.4, B_S + 3.28 + k * 6.116))
for k, (a, bb) in enumerate(cols):
    deck.append(prism_ab(f"column{k}", [(a - 0.4, bb - 0.4), (a + 0.4, bb - 0.4), (a + 0.4, bb + 0.4), (a - 0.4, bb + 0.4)],
                         (-1.0, zt - 0.3), "tile"))
parts.append(comp("デッキ", None, EV_DECK, 0.6,
                  "2 階の床の高さの歩廊（観察票 f8・f0・f1 の structures）。床 z 5.45 m（DECK_Z：2 階の窓の彫り込みの下端から腰の高さを引いた、"
                  "見えていない）。f8 沿いの出 2.7 m は観察票の目安（柱間 5.408 m の約 1/2、本部棟屋上）、f0 沿いの出 3.5 m は柱間 6.116 m の約 0.57"
                  "（観察票：1/2〜2/3、f8 側より広い）。入隅で斜めに折れる辺は脚 3 m（本部棟屋上の見え方、比率から）。東端は f8 の東の無窓の壁の"
                  "西の端（5.95 m）で東端の階段に続く。北は外階段の 2 階の踊り場まで（f1 の外面の線）。腰壁は煉瓦張りで床の下 0.65 m から上 1.1 m"
                  "（床の下は検収係が recheck の腰壁の下端 4 枚 +0.19〜+0.29 m で 0.85 m から直した）"
                  "（f8 の面の写真で帯の高さが階高の約 0.5〜0.6、観察票）。下は 1 階の柱（柱割りの線、0.8 m 角、車道の上の 1 本は無い）。"
                  "全部 ± 30% の目安", deck))
# 東端の階段（中庭の高い所 → デッキ）
EV_ES = ["PXL_20261105_063029997", "PXL_20261104_070928590", "PXL_20261103_043342958"]
z_low = round(zt - 14 * 0.155, 2)
parts.append(comp("東端の階段（f8 の東の無窓の壁に沿う直階段）", "f8", EV_ES, 0.6,
                  f"063029997 で段 14 段ほど × 0.155 m（ピロティの段と同じ）でデッキ {zt} m へ上がる → 下端 {z_low} m。f8 の北東の角から"
                  "無窓の壁に沿って西へ上がる（観察票 f8）。幅はデッキの出と同じ 2.7 m と見た。下の段の下は煉瓦の腰壁で塞がる（070928590）。"
                  "下端の地面（北東の大階段の上の踊り場）は敷地の物で、この棟には置かない（requests）",
                  [box("base", (0.3, 5.95), (0.0, 2.7), (-0.6, z_low), "tile"),
                   {"shape": "wedge", "name": "flight", "s": [0.3, 5.95], "d": [0.0, 2.45], "z": [z_low, zt], "rise": "+s", "material": "brick"},
                   {"shape": "wedge", "name": "parapet", "s": [0.3, 5.95], "d": [2.45, 2.7], "z": [z_low, zt + 1.1], "rise": "+s", "t": 1.1,
                    "material": "tile"}]))
# 歩行者の通路（f8 の東の 1 階から南のピロティへ）
parts.append(comp("東の歩行者の通路（デッキの下から南のピロティへ）", "f6", ["PXL_20261104_070928590"], 0.6,
                  "070928590 でデッキの下から南の翼の 1 階を南へ抜ける屋根付きの通路（観察票 f8）。f8 の 6.3〜9.1 m（f6 の座標で 40.6〜43.4 m）、"
                  "幅は柱間の約 1/2（観察票：1/2〜1）。床はピロティと同じ 1.30 m、天井は 1 階の天井（4.4 m、比率から）",
                  [box("passage", (40.6, 43.4), (-(DEPTH_S + 0.4), -5.46), (PIL_Z, 4.4), cut=True)]))
parts.append({"type": "nameplate", "name": "『本部棟』の柱の札", "face": "f8",
              "along": est(16.65, 0.4, ["PXL_20261103_043320836", "PXL_20261103_043327622", "PXL_20261103_043334437", "PXL_20261104_070928590"],
                           "車道の東の脇のデッキの柱（043320836、柱割りの 2/3 の線）。検収係が recheck の札の左の縁（043320836 +0.17・043327622 +0.25・"
                           "043334437 +0.11 m）で 16.5 → 16.65 m に直した"),
              "offset": est(2.05, 0.3, ["PXL_20261103_043320836"], "デッキの柱の面（デッキの出 2.7 m − 柱の半分 0.4 − 0.25）"),
              "width": est(0.45, 0.13, ["PXL_20261103_043320836"], "柱の幅の約 0.55（比率から）"),
              "height": est(1.1, 0.3, ["PXL_20261103_043320836", "PXL_20261104_070928590"], "観察票 f8：高さは 1 階の高さの約 1/4"),
              "thickness": est(0.03, 0.01, ["PXL_20261103_043320836"], "柱に貼った札（比率から）"),
              "z0": est(1.5, 0.4, ["PXL_20261103_043320836"], "043320836 で札の下端は柱の高さの約 1/3（比率から）"), "material": "metal"})

# 8. f0 の 1 階の歩廊・ATM・段・傾路・植え込み・分別ごみ箱
EV_AR = ["PXL_20261103_043259125", "PXL_20261103_044822533", "PXL_20261103_044849371", "PXL_20261103_043304319"]
ar = [box("arcade", (0.2, F0_WALL - 0.45), (-2.0, 0.3), (0.0, 4.3), cut=True),
      box("shopfront_glass", (0.25, F0_WALL - 0.5), (-1.99, -1.96), (0.0, 3.2), "glass"),
      box("shopfront_room", (0.25, F0_WALL - 0.5), (-2.0, -1.995), (0.0, 3.2), "interior")]
for k in range(5):
    c = 3.28 + k * 6.116
    ar.append(box(f"column{k}", (c - 0.45, c + 0.45), (-0.9, 0.0), (0.0, 4.3), "tile"))
ar.append(box("atm_booth", (3.75, 5.6), (-1.95, -0.9), (0.0, 2.6), "metal"))
parts.append(comp("f0 の 1 階の歩廊（デッキの下の店先）と ATM のブース", "f0", EV_AR, 0.6,
                  "観察票 f0：1 階はデッキの下で奥に引っ込み、煉瓦の柱が並ぶ屋根付きの歩廊（天井は白く平ら）。引っ込みは見えていない。"
                  "柱間 6.116 m の約 1/3（2.0 m）と見た。天井はデッキの床の下（4.3 m、比率から）。柱は柱割りの線（0.9 m 角）。"
                  "ATM のブース（043259125・044849371）は南寄りの柱の右、高さは 1 階の天井の約 0.6（比率から）。店先のガラスは奥の壁に",
                  ar))
EV_RAMP = ["PXL_20261103_043259125", "PXL_20261103_043304319", "PXL_20261103_044907567", "本部棟屋上"]
g0 = f0["ground_offset"]["value"]
parts.append(comp("白い腰壁の傾路（中庭 → 1 階の床）", "f0", EV_RAMP, 0.8,
                  f"本部棟屋上で傾路は f0 のデッキの南寄り（入隅から約 1/4、6.6〜8.1 m）から中庭へ約 5.5 m 出る。中庭の地面 {g0} m から 1 階の床 0 m へ"
                  "（観察票 f0：段 3〜4 段分）。実際は曲がっているが直線で近似。腰壁は白（高さ 0.9 m、比率から）",
                  [{"shape": "wedge", "name": "ramp", "s": [6.6, 8.1], "d": [D0, D0 + 5.5], "z": [g0, 0.0], "rise": "-d", "material": "concrete"},
                   box("wall_l", (6.45, 6.6), (D0, D0 + 5.5), (g0, 0.9), "white"),
                   box("wall_r", (8.1, 8.25), (D0, D0 + 5.5), (g0, 0.9), "white")]))
parts.append({"type": "steps", "name": "半円形の段（入隅の近く）", "face": "f0",
              "along": est(0.4, 0.5, ["本部棟屋上", "PXL_20261105_063431446"], "本部棟屋上で入隅の近く（観察票 f0）"),
              "width": est(4.0, 1.2, ["本部棟屋上", "PXL_20261105_063431446"], "本部棟屋上で入隅の細い柱間とほぼ同じ幅（比率から）"),
              "depth": est(D0 + 1.2, 0.8, ["本部棟屋上"], "デッキの下からデッキの縁の外へ約 1.2 m 出る（本部棟屋上、比率から）"),
              "count": est(3, None, ["本部棟屋上", "PXL_20261103_043259125"], "観察票 f0：3〜4 段", unit="段"),
              "rise": est(0.15, 0.04, ["PXL_20261103_043259125"], "中庭の地面から 1 階の床まで 0.45 m を 3 段"),
              "z0": dict(f0["ground_offset"], ref="faces.f0.ground_offset"),
              "edge": "curved", "radius": est(2.6, 0.8, ["本部棟屋上"], "半円形（幅の半分＋段の踏み面、比率から）"), "material": "brick"})
parts.append({"type": "planter", "name": "白い縦リブの腰壁の植え込み（傾路の脇）", "face": "f0",
              "along": est(8.6, 0.4, EV_RAMP, "傾路の北の脇（本部棟屋上・043259125）。設計係の 8.3 m を、" + RECHECK1 +
                            " の縁の左端 043259125 +0.42・043304319 +0.24 m で 0.3 m 北へ（検収係）"),
              "width": est(2.5, 0.75, EV_RAMP, "柱間の約 0.4（比率から）"),
              "depth": est(2.0, 0.6, EV_RAMP, "傾路の長さの約 0.4（比率から）"),
              "offset": est(D0, 0.3, EV_RAMP, "デッキの縁から"),
              "height": est(1.1, 0.3, ["PXL_20261103_043259125", "PXL_20261103_044813659.MP"], "観察票 f0：腰壁の高さは 1 階の高さの約 1/4"),
              "z0": dict(f0["ground_offset"], ref="faces.f0.ground_offset"), "wall": est(0.15, 0.05, ["PXL_20261103_080542963", "PXL_20261103_043259125"], "縁の厚みは段の高さほど（080542963・043259125、比率から）"), "material": "white"})
parts.append({"type": "planter", "name": "白い縦リブの腰壁の植え込み（北の端）", "face": "f0",
              "along": est(24.5, 0.8, ["PXL_20261103_043150092", "本部棟屋上"], "北の端のピロティの前（観察票 f0、本部棟屋上）"),
              "width": est(3.0, 0.9, ["本部棟屋上"], "柱間の約 1/2（比率から）"),
              "depth": est(1.5, 0.45, ["本部棟屋上"], "幅の約 1/2（比率から）"),
              "offset": est(D0, 0.3, ["本部棟屋上"], "デッキの縁から"),
              "height": est(1.1, 0.3, ["PXL_20261103_043150092"], "観察票 f0：腰壁の高さは 1 階の高さの約 1/4"),
              "wall": est(0.15, 0.05, ["PXL_20261103_080542963", "PXL_20261103_043259125"], "縁の厚みは段の高さほど（080542963・043259125、比率から）"), "material": "white"})
parts.append(comp("分別ごみ箱の列（4 箱）", "f0", ["PXL_20261103_043259125", "PXL_20261103_044907567"], 0.4,
                  "観察票 f0：傾路の脇、1 階の前に 1 列 4 箱。高さは 1 階の高さの約 1/3（1.4 m）、列の幅は柱間の約 1/2（3.0 m）。"
                  "傾路の南の脇に置いた（043259125、比率から）",
                  [box(f"bin{k}", (3.4 + k * 0.75, 3.4 + k * 0.75 + 0.7), (D0 + 0.2, D0 + 0.9), (g0, g0 + 1.4), "metal") for k in range(4)]))

# 9. 北端の外階段（f1 の外面・f2 の側面。北の壁は f3 の線を東へ延ばした所）
EV_ST = ["PXL_20261103_043136840", "PXL_20261103_043123901", "PXL_20261103_043129358", "PXL_20261103_043150092",
         "PXL_20261103_080431748", "PXL_20261104_073618326"]
nz = N_FP["value"]
landings = [round(zt + k * nz / 2, 2) for k in range(7)]
parts.append({
    "type": "stair_switchback", "name": "北端の折り返しの外階段", "face": "f3",
    "along": est(-F3_STAIR, 0.3, EV_ST, f"北の壁（f3 の線）の上で f1 の幅 8.55 m ぶん東（v3 から {F3_STAIR} m）。観察票 f1：外階段は f1 の全幅"),
    "width": est(F3_STAIR, 0.3, EV_ST, "PLATEAU の辺 f1（8.55 m）"),
    "depth": est(2.72, 0.3, EV_ST, "PLATEAU の辺 f2（2.72 m。観察票 f1：階段の出は f2 の幅と同じと読む）"),
    "landing_z": [est(z, 0.5, EV_ST, ("2 階の踊り場＝デッキの床" if k == 0 else
                                       f"北の翼の階高 {nz} m の半分ずつ（折り返しの中間の踊り場を含む。段の数・中間の踊り場の位置は見えていない、観察票 f1 unknowns）"),
                      ref=("parts[デッキ].z" if k == 0 else "blocks[north_wing].floor_pitch")) for k, z in enumerate(landings)],
    "first_end": "from", "first_lane": "outer",
    "landing_len_from": est(1.5, 0.45, EV_ST, "階段の幅 8.55 m の約 1/6（043136840、比率から）"),
    "landing_len_to": est(1.5, 0.45, EV_ST, "同上"),
    "end_parapet": ["to"],
    "pilotis": {"count": est(1, None, ["PXL_20261103_043136840", "PXL_20261103_043150092"], "観察票 f1：2 階の踊り場の下に煉瓦の柱 1 本", unit="本"),
                "size": est(0.6, 0.18, ["PXL_20261103_043136840"], "柱型の幅（0.87〜0.93 m）の約 0.7（比率から）"), "shape": "square", "material": "tile"},
    "parapet": {"material": "tile", "height": est(1.1, 0.3, EV_ST, "煉瓦張りの腰壁（観察票 f1）。高さは段の上の人の腰ほど（比率から）"),
                "thickness": est(0.2, 0.06, EV_ST, "比率から")},
    "exit_box": {"height": est(2.4, 0.7, ["PXL_20261103_043136840", "本部棟屋上"], "屋上の出入口の小さな箱（観察票 f1）。高さは扉＋梁（比率から）"),
                 "material": "tile"},
    "canopy": {"shape": "barrel", "rise": est(0.9, 0.3, ["PXL_20261103_043136840", "本部棟屋上"], "半円筒形の半透明の庇（観察票 f1）。rise は幅の約 1/2（比率から）"),
               "material": "canopy"},
    "note": "1 本目は西から上がり東の 2 階の踊り場へ（観察票 f1）。2 階の踊り場の東はデッキに続くので from の端の腰壁を外した",
})
parts.append(comp("外階段の 1 本目の段の下の煉瓦の塊（f2）", "f3", ["PXL_20261103_043123901"], 0.5,
                  "043123901：1 本目の段の下は煉瓦で塞いだ三角の塊（観察票 f2）。外階段の西の端（f2 の線）の下に、1 本目の段の下を埋める三角",
                  [{"shape": "wedge", "name": "fill", "s": [-F3_STAIR + 1.5, 0.0], "d": [0.0, 1.36], "z": [0.9, zt - 0.25], "rise": "-s", "material": "tile"}]))

# 10. f4 の通用口の段の板と南端の煉瓦の庇
parts.append({"type": "steps", "name": "通用口の前の鋼の段の板（1 柱間目）", "face": "f4",
              "along": est(2.4, 0.3, EV_F4, cov("f4", "両開きの戸の左の縁", 104) + " の少し左"),
              "width": est(2.0, 0.4, EV_F4, cov("f4", "戸の幅＋左右", 72) + "（比率から）"),
              "depth": est(0.8, 0.25, ["PXL_20261103_043103134"], "見えていない（比率から）"),
              "count": est(1, None, ["PXL_20261103_043103134"], "043103134 で 1 段", unit="段"),
              "rise": est(0.15, 0.05, ["PXL_20261103_043103134"], "比率から"), "material": "metal"})
parts.append(comp("南端の細い柱間の小さな煉瓦の庇", "f4", ["PXL_20261103_043049890", "PXL_20261103_043034043"], 0.3,
                  cov("f4", "庇の範囲 765〜870 px（19.1〜21.75 m）、1105〜1125 px（z 4.47〜3.97）", "") + "。出は見えていない（幅の約 0.4、比率から）",
                  [box("canopy", (19.1, 21.6), (0.0, 1.0), (3.97, 4.47), "tile")]))

# 11. f5 の階段室の灰色の金属板・開いた踊り場・室外機・メーターの箱
parts.append({"type": "patch", "name": "階段室の上部の灰色の金属板", "face": "f5",
              "along0": est(0.6, 0.3, EV_F5 + ["PXL_20261103_043034043"], cov("f5", "金属板の左の縁", 25)),
              "along1": est(4.6, 0.3, EV_F5 + ["PXL_20261103_043034043"], cov("f5", "金属板の右の縁", 183)),
              "z0": est(10.85, 0.4, ["PXL_20261103_043034043"], cov("f5", "金属板の下端", 850)),
              "z1": est(17.7, 0.4, ["PXL_20261103_043034043"], cov("f5", "金属板の上端（北の翼の屋上の高さ。その上に斜めの頂部が見えるが届いていない）", 575)),
              "material": "metal"})
# 検収係が足した（2026-11-07。検収 C の 5：写真にあって模型に無い 1 m 以上の物）：階段室の灰色の箱は北の翼の屋上より上へ出る
EV_SR = ["PXL_20261103_043004629", "PXL_20261103_043026097", "PXL_20261103_043034043", "PXL_20261103_042946748"]
parts.append(comp("階段室の屋上へ出る灰色の箱（塔屋）", "f5", EV_SR, 0.4,
                  "out/honbu/coverage_f5_photo.png（40 px/m、f5 に登録した 043004629・043026097・043034043・042946748 の合成）で灰色の金属板の"
                  "箱が s 0.6〜4.5 m、上端は 490 px（z 19.85 m）。北の翼の笠木（17.75 m、同じ画像で 570 px＝17.85 m）より約 2.1 m 上に出て空と接する。"
                  "043034043 でも北の翼の笠木の線（模型）より上に灰色の板が続く。役割は階段室の屋上への塔屋（屋上から約 1 階ぶん）。"
                  "奥行きは見えていない：幅と同じ 3.9 m と見た（比率から ± 40%。航空写真の倒れ込みの影に入り読めない）",
                  [box("box", (0.6, 4.5), (-3.9, 0.0), (17.75, 19.75), "metal"),
                   box("cap", (0.55, 4.55), (-3.95, 0.05), (19.75, 19.85), "concrete")]))
EV_LD = ["PXL_20261103_043034043", "PXL_20261103_043041105", "PXL_20261103_043004629"]
ld = []
for k, (zb, ztop) in enumerate(((2.5, 4.0), (7.1, 9.0))):
    ld += [box(f"slab{k}", (4.1, 8.6), (0.0, 2.5), (zb, zb + 0.35), "concrete"),
           box(f"parapet{k}", (4.1, 8.6), (2.3, 2.5), (zb, ztop), "tile"),
           box(f"parapet{k}_s", (8.4, 8.6), (0.0, 2.3), (zb, ztop), "tile")]
ld.append(box("column", (6.0, 6.4), (1.9, 2.3), (0.0, 7.1), "tile"))
ld.append(box("passage_dark", (4.4, 7.8), (-0.02, 0.01), (0.0, 2.4), "dark"))   # 右の縁は検収係が直した（7.4 → 7.8。下の method）
parts.append(comp("階段室の開いた踊り場（2 段）と足元の通路の口", "f5", EV_LD, 0.6,
                  cov("f5", "踊り場の腰壁 165〜345 px（4.1〜8.6 m）、上の段 915〜1000 px（z 9.0〜7.1）・下の段 1125〜1185 px（z 4.0〜2.5）", "") +
                  "。手前に出ているので高さは目安。出は北の翼の柱間の約 1/3〜1/2（観察票 f5）で 2.5 m。中に柱 1 本。下の通路の口は暗い開口（2.4 m、比率から）。"
                  "通路の口の右の縁は検収係が 7.4 → 7.8 m に直した（" + RECHECK1 + " で 042946748 +0.32・043004629 +0.42・"
                  "043026097 +0.43・043034043 +0.12 m）",
                  ld))
parts.append({"type": "asset", "asset": "ac_outdoor", "name": "階段室の足元の室外機", "face": "f5",
              "along": est(4.5, 0.3, EV_F5, cov("f5", "室外機の左の縁", 180)), "offset": est(0.2, 0.15, EV_F5, "壁の前（比率から）"),
              "z0": est(0.0, 0.1, EV_F5, "地面置き"), "yaw": 0,
              "count": est(1, None, EV_F5, "観察票 f5：階段室の足元に 1 台", unit="台"),
              "size": {"w": est(0.9, 0.2, EV_F5, cov("f5", "幅", 36)), "d": unk(note="見えていない"), "h": est(0.65, 0.15, EV_F5, cov("f5", "高さ", 26))}})
parts.append({"type": "asset", "asset": "ac_outdoor", "name": "南西の角の室外機（低い白い囲いの中）", "face": "f5",
              "along": est(19.3, 0.5, ["PXL_20261103_042927350", "PXL_20261103_043004629"], cov("f5", "室外機の列の左の縁", 772)),
              "offset": est(0.3, 0.2, ["PXL_20261103_042927350"], "囲いの中（比率から）"),
              "z0": est(0.0, 0.1, ["PXL_20261103_042927350"], "地面置き"), "yaw": 0,
              "count": est(3, None, ["PXL_20261103_042927350"], "観察票 f5：南西の角に 3 台", unit="台"),
              "pitch": est(0.95, 0.2, ["PXL_20261103_042927350"], "台の幅＋隙間（比率から）"),
              "size": {"w": est(0.85, 0.2, ["PXL_20261103_042927350"], "比率から（階段室の足元の物と同じ型に見える）"), "d": unk(note="見えていない"),
                       "h": est(0.7, 0.2, ["PXL_20261103_042927350"], "比率から")}})
parts.append(comp("ガスメーターの箱（妻の 1 階）", "f5", ["PXL_20261103_043004629", "PXL_20261103_043034043"], 0.3,
                  cov("f5", "箱 490〜503 px（12.25〜12.6 m）、1225〜1265 px（z 1.5〜0.5）", "") + "。観察票 f5：高さは 1 階の高さの約 1/4",
                  [box("meter_box", (12.25, 12.6), (0.0, 0.3), (0.5, 1.5), "white")]))

# 12. f3 の室外機の群れ・設備の箱
EV_AC = ["PXL_20261103_043123901", "PXL_20261103_043112969", "PXL_20261104_073547139"]
parts.append(comp("北西の角の室外機の台", "f3", EV_AC, 0.5,
                  "観察票 f3：室外機の群れがコンクリートの台の上（北西の角の地面）。台は f3 の幅いっぱい、出は大きい室外機の奥行き＋通路"
                  "（約 2 m、比率から）。設計係が置いた北側の白い囲い（fence_n、高さ 0.95〜1.9 m）は検収係が外した：043123901・043112969・"
                  "073547139 のどれにも室外機の北に白い壁は無く、観察票の『低い白い壁の囲い』は f4 の 1 柱間目の窓の前の L 字の低い壁"
                  "（下の部品）と読んだ（out/honbu/soten.json）",
                  [box("base", (0.0, 3.72), (0.0, 2.0), (0.6, 0.95), "concrete")]))
EV_LW = ["PXL_20261103_043112969", "PXL_20261104_073547139", "PXL_20261103_043103134"]
parts.append(comp("f4 の 1 柱間目の窓の前の白い低い壁（L 字）", "f4", EV_LW, 0.3,
                  "検収係が足した（観察票 f3 の『低い白い壁の囲い』の実物）。043112969・073547139・043103134 で、北西の角の柱型の右から"
                  "通用口の戸の左まで（f4 の s 1.05〜2.55 m。設計の初めは柱型の右の縁 0.85 m、2 回目の recheck で北の端を 1.05 m に直した。" + cov("f4", "戸の左の縁", 104) + "）、窓の前に白い低い壁が"
                  "あり、北の端で壁へ折れる。高さは 1 階の窓の下端（0.6 m）より少し低い（043112969 で窓の下の縁のすぐ下、比率から）。"
                  "出は壁の長さの約 0.45（073547139、比率から ± 30%）",
                  [box("front", (1.05, 2.55), (0.65, 0.8), (0.0, 0.6), "white"),
                   box("return", (1.05, 1.2), (0.0, 0.8), (0.0, 0.6), "white")]))   # 北の端は 2 回目に 0.85 → 1.05（recheck 043055592 +0.10・043103134 +0.30 m）
parts.append({"type": "asset", "asset": "ac_outdoor", "name": "北西の角の大きい縦型の室外機", "face": "f3",
              "along": est(0.4, 0.4, EV_AC, "043123901 で群れの西寄り（比率から）"), "offset": est(0.3, 0.3, EV_AC, "台の上（比率から）"),
              "z0": est(0.95, 0.3, EV_AC, "コンクリートの台の上"), "yaw": 0,
              "count": est(2, None, EV_AC, "観察票 f3：大きい縦型 2", unit="台"), "pitch": est(1.3, 0.3, EV_AC, "台の幅＋隙間（比率から）"),
              "size": {"w": est(1.1, 0.33, EV_AC, "比率から"), "d": est(0.7, 0.21, EV_AC, "比率から"),
                       "h": est(1.7, 0.5, EV_AC, "観察票 f3：大きい物の高さは 1 階の高さ（約 3.4 m）の約 1/2")}})
parts.append({"type": "asset", "asset": "ac_outdoor", "name": "北西の角の横置きと小さい室外機", "face": "f3",
              "along": est(2.9, 0.5, EV_AC, "群れの東寄り（比率から）"), "offset": est(0.3, 0.3, EV_AC, "台の上（比率から）"),
              "z0": est(0.95, 0.3, EV_AC, "コンクリートの台の上"), "yaw": 0,
              "count": est(1, None, EV_AC, "観察票 f3：横置き 2・小 2（f3 の幅に入る 1 台を置き、残りは f4 の北端：下）", unit="台"),
              "size": {"w": est(0.9, 0.27, EV_AC, "比率から"), "d": est(0.35, 0.1, EV_AC, "比率から"), "h": est(0.9, 0.27, EV_AC, "比率から")}})
parts.append({"type": "asset", "asset": "ac_outdoor", "name": "北西の角の室外機（f4 の北端の外）", "face": "f4",
              "along": est(0.2, 0.4, ["PXL_20261103_043103134", "PXL_20261103_043112969"], "北西の角の外（観察票 f4：f3 の群れと続く。二重に数えない）"),
              "offset": est(0.3, 0.3, ["PXL_20261103_043103134"], "壁の前（比率から）"),
              "z0": est(0.0, 0.2, ["PXL_20261103_043103134"], "地面置き"), "yaw": 0,
              "count": est(3, None, ["PXL_20261103_043103134", "PXL_20261103_043112969"], "観察票 f3 の残り（横置き 1・小 2）", unit="台"),
              "pitch": est(1.0, 0.3, ["PXL_20261103_043103134"], "比率から"),
              "size": {"w": est(0.9, 0.27, ["PXL_20261103_043103134"], "比率から"), "d": est(0.35, 0.1, ["PXL_20261103_043103134"], "比率から"),
                       "h": est(0.9, 0.27, ["PXL_20261103_043103134"], "比率から")}})
parts.append(comp("電気・設備の箱（f3 の配管の脇）", "f3", ["PXL_20261103_043123901"], 0.4,
                  "観察票 f3：2〜3 個、大きい物は 1 階の高さの約 1/4（1.0 m）。043123901 で配管の脇の 1 階の高さ。大きい 1 個を置いた（比率から）",
                  [box("panel", (2.7, 3.6), (2.0, 2.4), (0.95, 2.05), "metal")]))

# 13. 屋上：笠木・塔屋の上の設備・天窓・アンテナ（位置は recorder photos roof、下の ROOF_* に書き写した値）
parts.append({"type": "coping", "name": "笠木（全部の塊の上端）",
              "height": est(0.15, 0.05, ["PXL_20261104_074357308", "PXL_20261103_042900775"], "観察票 roof：細い金属の笠木。厚みはタイル 2 段ほど（比率から）"),
              "overhang": est(0.05, 0.02, ["PXL_20261104_074357308"], "比率から")})


# 屋上の物（位置は recorder photos roof data/buildings/honbu.json の out/honbu/roof.json を書き写した。倒れ込みは塊ごと）
LEAN_S = est([0.5, 5.0], 1.0, ["gsi:aerial_z18"], "recorder photos roof の lean_m（南の翼の高さ 23.15 m、率 0.217、縁は自動・ambiguous でない）の逆向き", unit="m")
LEAN_T = est([0.67, 6.70], 1.4, ["gsi:aerial_z18"], "南の翼の倒れ込み率 0.217 × 塔屋の高さ 31.0 m（roof.json lean_by_block の tower）の逆向き", unit="m")
sk_a, sk_b = local((-24.74, -16.35))
parts.append(comp("南の翼の屋上の天窓（傾いたガラスの箱）", "f6", ["本部棟屋上", "gsi:aerial_z18"], 0.6,
                  f"位置は recorder photos roof の設備 2（中心 (-24.74, -16.35)、倒れ込みを戻した値＝局所 a {sk_a:.1f}・b {sk_b:.1f} m）。"
                  "航空写真の暗い塊 3.5 × 3.3 m は影を含むので、幅は観察票 roof の比（柱間 5.418 m の約 0.55）で 3.0 m、奥行きは本部棟屋上で幅の約 0.8。"
                  "高さは本部棟屋上でガラスの斜面が幅の約 1/3（比率から）。屋上の面は南の翼の笠木の高さに置いた（模型の塊の上面）",
                  [box("curb", (sk_a - 1.5, sk_a + 1.5), (-sk_b - 1.2, -sk_b + 1.2), (23.15, 23.45), "concrete"),
                   {"shape": "wedge", "name": "glass", "s": [round(sk_a - 1.5, 3), round(sk_a + 1.5, 3)], "d": [round(-sk_b - 1.2, 3), round(-sk_b + 1.2, 3)],
                    "z": [23.45, 24.15], "rise": "-d", "material": "glass"}]))
ac_a, ac_b, ac_w, ac_d = 6.25, 13.57, 2.42, 3.07
ac_poly = [[round(x - 0.67, 3), round(y - 6.70, 3)] for x, y in rect_ab(ac_a - ac_w / 2, ac_a + ac_w / 2, ac_b - ac_d / 2, ac_b + ac_d / 2)]
parts.append({"type": "rooftop_units", "name": "塔屋の上の白い設備の箱", "source": "aerial", "lean_correction_m": LEAN_T,
              "z0": dict(TOWER_TOP, ref="blocks[tower].top_z"),
              "units": [{"polygon": ac_poly, "h": est(1.5, 0.45, ["本部棟屋上"], "本部棟屋上で箱の上が塔屋の腰壁より少し出る（比率から）"),
                         "material": "white"}],
              "evidence": ["本部棟屋上", "gsi:aerial_z18"],
              "method": ("recorder photos roof を一時の土台で回した時の明るい設備（航空写真の上の中心 (-40.53, -10.89)、2.42 × 3.07 m、"
                         "観察票 roof の設備 3）を、塔屋の高さの倒れ込みで戻した（局所 a 6.25・b 13.57 m）。polygon は航空写真の上の位置。"
                         "塊を分けて回し直すと明るい塊としては出ない（暗い設備 5 が近くにある）")})
parts.append({"type": "column", "name": "塔屋の上のアンテナの柱", "x": world(4.5, 15.0)[0], "y": world(4.5, 15.0)[1],
              "diameter": est(0.15, 0.05, ["本部棟屋上"], "細い柱（比率から）"),
              "z0": dict(TOWER_TOP, ref="blocks[tower].top_z"),
              "z1": est(40.5, 2.5, ["本部棟屋上", "PXL_20261105_063431446"],
                        "本部棟屋上で柱の長さ 280 px、同じ写真の塔屋の角（南の翼の屋上から 7.9 m）が 230 px → 約 9.5 m を塔屋の上端に足した。"
                        "位置は recorder photos roof の暗い設備 5（局所 a 4.5・b 15.0 m、柱の根元か影）"),
              "section": "round", "material": "metal"})

b["parts"] = parts

# --- 依頼 -------------------------------------------------------------------------------------
REQ = [
    {"kind": "photo", "face": "f6", "what": "f6（南南西面）を南の広場の奥から、両端と垂れ幕が 1 枚に入る写真", "where": "南の広場の南端",
     "why": "柱間 9 と柱間÷階高を両端の入る 1 枚の正面化で確かめる（観察票 f6）"},
    {"kind": "photo", "face": "f6", "what": "f6 の 1 階の柱型にメジャーを沿えて 1 枚（柱型の幅か窓の開口、タイル 10 段）", "where": "正面玄関の西の柱など手の届く所",
     "why": "縮尺が全部 estimated（PLATEAU・タイル）。1 か所あれば measured の縮尺になる（測量係）"},
    {"kind": "photo", "face": "f1", "what": "北端を北の道路の向こうから正面に 1 枚と、西の斜め横から 1 枚", "where": "北端の北の道路・駐車場",
     "why": "f1 が外階段の外面か（mid）、外階段の踊り場の高さと段の数（観察票 f1・数え f1）。模型の外階段は踊り場を半階ずつと置いた"},
    {"kind": "photo", "face": "f5", "what": "西の道路の向こうから階段室とその左右（f4 の南端と南の翼の妻）を正面に 1 枚", "where": "西面の前の道路の西側",
     "why": "f4/f5 の段差、階段室の塔の段と頂部（灰色の金属板の上の斜めの頂部が面の写真に入っていない）"},
    {"kind": "photo", "face": "f5", "what": "塔屋の平面の位置が分かる 2 枚：南の広場から塔屋と南の翼の西の妻が一緒に入る 1 枚、中庭（デッキ）から塔屋の東の面と入隅が入る 1 枚",
     "where": "南の広場の南西寄り・f8 のデッキの東寄り",
     "why": "塔屋の位置を本部棟屋上の 1 枚から ±3 m で置いた。航空写真の倒れ込みを戻すと東へ 8 m ずれて合わない（blocks[tower].method）"},
    {"kind": "photo", "face": "f8", "what": "デッキの端（腰壁）を真横から見て、上の階の壁よりどれだけ前に出るか分かる 1 枚", "where": "f8 の東端の階段の上から西へ、デッキの外側に沿って",
     "why": "デッキの出（2.7 m・3.5 m）とデッキの床の高さが比率の目安だけ"},
    {"kind": "photo", "face": "f0", "what": "デッキの上から、2 階と 3 階の開口の上端が両方入る正面の 1 枚", "where": "南の翼のデッキの東寄り",
     "why": "f0 の階高が測れない（測量係）。北の翼の 2 階の床とデッキの高さの関係も"},
    {"kind": "photo", "face": "f0", "what": "北東の角と外階段（f1）の東の側面が両方入る 1 枚", "where": "北の翼の東の駐車場から",
     "why": "PLATEAU の辺 f0 30.78 m が外階段まで含むか（模型は含むと読んで北の翼の角を 28.05 m に置いた）"},
    {"kind": "photo", "face": "f7", "what": "f7（東の妻）を東の道路の向こうから、南東・北東の角と足元が入る 1 枚（離れて、広角の端を使わない）", "where": "東の道路の向こう",
     "why": "小窓の位置・ピロティと通路の幅・地面が超広角の真下からの 1 枚の比だけ"},
    {"kind": "data", "face": "f8", "what": "PLATEAU の辺 f8 36.81 m に対し、写真では 7.18 柱間 ≒ 38.8 m（+5.5%）",
     "why": "模型は margin_to を負（−1.59 m）にして食い違いを残した。6 柱間目の窓が入隅を越える（入隅の細い柱間の引っ込みの中まで f8 の壁が続く可能性）"},
    {"kind": "data", "face": "f6", "what": "面の前の地面の高さ：5 m 標高の帯（土台の ground_z 45.25 m から f6 −1.35・f5 −0.61・f4 +0.64・f0 +0.04・f8 −0.61・f7 −3.33 m）と、"
                                         "登録の決まり（各面の前の地面を z=0）が食い違う",
     "why": "模型は f6・f5・f4・f8 を 0、f0 を −0.45 m に上書きした。f5 の笠木が f6 より 0.65 m 低く読めるのは 5 m 標高の差（0.74 m）と合う。敷地の検収係へ"},
    {"kind": "tool", "face": "f6", "what": "敷地に置く物：南東の大階段の東の続き（南東の角の外）と f7 沿いに北へ上がる細い階段（白い縦リブの腰壁と植え込み）、本部棟と隣の棟（図書館側）の間",
     "why": "棟と棟の間の地面に立つ物は敷地 JSON（規則）。敷地に段・階段を置く語彙がまだ無い。根拠 042721424・064600203・043608113（段 8 段ほど、幅は約 2 柱間）",
     "to": "tool"},
    {"kind": "tool", "face": "f8", "what": "敷地に置く物：北東の大階段（中庭の車道の脇 → 図書館側の高い所）、本部棟と図書館の間",
     "why": "棟と棟の間の地面に立つ物は敷地 JSON。根拠 044734408・062912368・062916256・043334437（幅は約 2 柱間、端が段ごとに欠ける）。東端の階段の下の踊り場もこれに乗る",
     "to": "tool"},
    {"kind": "tool", "face": "f6", "what": "敷地に置く物：f6 の西寄りの前の高い柱 2 本（旗竿か照明）、前の木 3〜4 本、f3 の北西の角の外の街灯 2 本、中庭の植え込みの島と木 2〜3 本",
     "why": "棟の壁に付かない地面の物は敷地 JSON（規則）。根拠 074357308・042853166（柱、高さ 4〜5 階）、042822099・042844361（木）、043112969・043103134（街灯）、043753272・本部棟屋上（植え込みの島）",
     "to": "tool"},
    {"kind": "asset", "face": "f6", "what": "ピロティの机（長机）の見た目：台帳の table_long は決定 D-0003 で、今の決定の台帳に無い（内装は停止中）",
     "why": "042721424・064600203 の机とベンチ 2 組を箱で置いた。外の物として D-0003 の再承認か別の決定が要る"},
    {"kind": "check", "face": "f6", "what": "祝の垂れ幕 4 枚（4・5 柱間）と立て看板 1 つ（7/8 の柱）は置かなかった",
     "why": "仮設の物（観察票 f6 の questions）。1 m 以上で写っているが季節で替わるので進行役が決める"},
    {"kind": "check", "face": "f6", "what": "out/honbu/coverage_f6_photo.png の 1 階の西半分（s 0〜25 m）には東のピロティ（机とベンチ・煉瓦の段）が貼られている",
     "why": "042830256 などの登録が柱間 2〜3 個ずれている疑い（同じ柱割りが続く面なので上の階では見分けられない）。模型の 1 階は 042900775・074357308・080542963 から取った。検収係へ"},
    {"kind": "check", "face": "f0", "what": "デッキの床の高さ（5.45 m、南の翼の 2 階）と北の翼の 2 階の床の関係",
     "why": "f4 の 2 階の窓の下端は 4.35 m（窓下の板の下端）で、南の翼より約 1 m 低く見える。デッキは 1 つの床と読んだ（観察票）が、f0 沿いで段か傾きがあるか見えていない"},
]
REQ += [
    {"kind": "check", "face": "f6", "what": "f6 の登録 3 枚が柱割り 4 つ分（約 21.7 m）東へずれている：PXL_20261103_042822099・042830256・042844361",
     "why": "検収係：recheck の重ね（out/honbu/recheck/20261107_150933/overlay_*.png）で、模型の正面玄関（2・3 柱間）が写真の 6・7 柱間"
            "（ピロティ・内の大階段・机）に重なる。柱割りが周期なので柱型の線は合格に見える。042822099 が鎖の登録で隣の周期に乗り、"
            "042830256・042844361 はそれを via にした。coverage_f6_photo.png の 1 階の西半分に東のピロティが貼られる原因（設計係の check）。"
            "登録を外すか目印（photos pnp）で登録し直す道具の操作が要る（検収係は台帳を書かない）。この 3 枚は検収の合否に数えていない",
     "to": "tool"},
    {"kind": "photo", "face": "f5", "what": "西の道路の向こう（20 m 以上離れて）から、階段室の灰色の箱・塔屋の西の面・南の翼の西の妻の上端が 1 枚に入る写真",
     "where": "西面の前の道路の西側、離れて",
     "why": "検収係が足した階段室の屋上の箱（上端 19.85 m）の奥行きと、塔屋の西の面が f5 と同じ面か 2 m 奥か（低い視点では区別できない）"},
    {"kind": "photo", "face": "f6", "what": "東のピロティの中（8 柱間の奥）から天井を見上げ、表の梁（1 階の開口の上端）と中の梁の下端・天井が一緒に入る 1 枚",
     "where": "f6 の 8 柱間のピロティの中",
     "why": "検収係：天井の梁を 2 回直しても recheck で梁の下端が面の線で +0.31 m（7 枚）残る。天井の高さと梁の成が写真で決まらない"},
]
for r in REQ:
    r.setdefault("where", "")
    r["status"] = "open"
    r.setdefault("to", "staff" if r["kind"] == "photo" else "tool")
b["requests"] = REQ

# --- 記録 -------------------------------------------------------------------------------------
b["history"] = b.get("history", []) + [
    {"at": AT, "by": BY_K, "what": "検収：recheck（out/honbu/recheck/20261107_150933）と photoproj（out/honbu/photoproj/20261107_151415）、航空写真で検収 C。"
                                    "報告 data/reports/honbu/kenshu_summary.json。直した値：f8 の柱型 0.93→0.80 m と開口 4.45→4.6 m、石の銘板の高さ 1.2→1.0 m、"
                                    "デッキの腰壁の床の下 0.85→0.65 m、f0 の植え込みの位置 8.3→8.6 m、f5 の通路の口の右の縁 7.4→7.8 m"},
    {"at": AT, "by": BY_K, "what": "天井の梁 2 つ（車道の通り抜け・東のピロティ）を ceiling_beams から composite に置き換えた。面の線の上の梁（開口の上端の下に 0.5〜0.6 m 垂れる）"
                                    "が写真に無く（recheck 4〜5 枚で +0.36〜+0.46 m）、ピロティの梁が柱の線と最大 1.06 m ずれていたため"},
    {"at": AT, "by": BY_K, "what": "足した：f5 の階段室の屋上へ出る灰色の箱（s 0.6〜4.5、上端 19.85 m、043004629・043026097・043034043・042946748）、"
                                    "f4 の 1 柱間目の窓の前の白い L 字の低い壁（043112969・073547139・043103134）。外した：f3 の室外機の北の白い囲い（どの写真にも無い。"
                                    "out/honbu/soten.json で決定）"},
    {"at": AT, "by": BY_K, "what": "f6 の登録 042822099・042830256・042844361 は柱割り 4 つ分東へずれた登録と判定し、合否に数えなかった（requests kind check）"},
    {"at": AT, "by": BY_K, "what": "検収 2 回目（out/honbu/recheck/20261107_153306 を見て）：ピロティの柱間の真ん中の小梁の成 0.6→0.3 m、f4 の白い L 字の低い壁の北の端 0.85→1.05 m、"
                                    "玄関前の植え込み（2 柱間）の幅 4.4→4.6 m、『本部棟』の柱の札の位置 16.5→16.65 m"},
    {"at": AT, "by": BY, "what": "観察票・数え・測量から設計を起こした（scripts/design_honbu.py）。塊を南の翼 5 階・北の翼 4 階・塔屋に分けた（観察票 roof）"},
    {"at": AT, "by": BY, "what": "f1・f2 は外階段の外面と側面と読み（観察票 mid）、北の翼の塊から外階段の長方形を除いた。f0 の北東の角（28.05 m）は写真の 28.15〜28.3 m と合う"},
    {"at": AT, "by": BY, "what": "塔屋の高さは本部棟屋上から 31.0 m と置いた。観察票 roof の『南の翼より約 1 階分上』（063431446 の見上げ）は採らなかった。理由：真上に近い本部棟屋上で、"
                                  "塔屋の角が南の翼の屋上から 5 階の小窓の枠の約 2.7 倍出る。捨てた方の写真は PXL_20261105_063431446"},
    {"at": AT, "by": BY, "what": "塔屋の上のアンテナは本部棟屋上で 9.5 m と置いた。観察票 roof の『高さは塔屋の約 1/2』（PXL_20261105_063431446）は採らなかった（真上に近い写真で塔屋の角の高さを物差しにした方を採った）"},
    {"at": AT, "by": BY, "what": "航空写真の暗い設備 1（北の翼の北西）・3・4（南の翼の西寄り）は置かなかった。地上・高所の写真で確かめられない（観察票 roof の unknowns）。設備 1 を観察票は外階段の出入口と読んだが、位置は北の翼の西寄りで合わない。外階段の出入口は stair_switchback の exit_box に置いた"},
    {"at": AT, "by": BY, "what": "f5 の 5 階の窓は数え係の 3 つ（スリット 1＋南西の角寄りの小窓 2）を採ったが、小窓 2 つは 1 m 未満なので置かず、スリットだけ置いた"},
    {"at": AT, "by": BY, "what": "coverage_f6_photo.png の 1 階の西半分は東のピロティの写真が貼られていて使わなかった（requests kind check）。1 階は 042900775・074357308・080542963 と測量係の正面化から"},
]
b["photos"] = sorted({e for pt in parts for e in (pt.get("evidence") or [])} | set(PH_F6 + PH_F6_E + PH_F8 + PH_F0 + PH_F4 + PH_F5 + PH_F7 + PH_N + PH_TOP))
b["made_by"] = BY
b["made_at"] = AT
b["design_script"] = "scripts/design_honbu.py"

n_raised = floor_tol(b)
b = apply_hypotheses_off(b, HYPOTHESES_OFF)
save_building(b, OUT)
print(f"wrote {OUT}  faces {len(b['faces'])}  parts {len(b['parts'])}  requests {len(b['requests'])}  tol raised {n_raised}")
