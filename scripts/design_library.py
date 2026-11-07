"""図書館（library）の設計：観察・数え・測量の報告（data/reports/library/）を棟 JSON（data/buildings/library.json）に落とす。

    .venv/Scripts/python -I -X utf8 scripts/design_library.py

土台は recorder.skeleton.from_site_cli("library")（＝ recorder new library --from-site）。JSON は手で直さず、ここを直して出し直す。

製図板（px → m）
  f0  coverage_f0_photo.png の左半分は 075051437 の手前の木が上に来る（sokuryo_f0.json#face_photo_note）ので、
      同じ登録の正面化 out/rectified/library_f0_070904733（113.58 px/m、x_origin 502 px、パラペット上端 933.5 px＝25.3 m）で読む
      s＝(px−502)/113.58、z＝25.3−(py−933.5)/113.58
  f1  coverage_f1_photo.png（40 px/m。左端＝f1 の from、z＝33.7−py/40）。縮尺は PLATEAU の辺（sokuryo_f1.json#scale）
  f2  正面化 out/rectified/library_f2_075639031（149.57 px/m、タイル 1 段、x_origin −463 px、パラペット上端 601 px）
      s＝(px+463)/149.57、z＝25.3−(py−601)/149.57。北寄りは coverage_f2_photo.png（z＝33.7−py/40）
  f3  coverage_f3_photo.png は使える所 0.6% なので、正面化 out/rectified/library_f3_064942113（compare 133 px/m、
      x_origin 1084 px、パラペット上端 251.8 px）の南東の端で読む
z は棟の z=0（ground_z 41.67 m）から。高さの原点はパラペット上端 25.3 m（sokuryo_f0.json#parapet_for_registration）。

寸法の取り方の記号（係の定義 sekkei.md）
  (a) 面の写真・正面化の px を数えて m → estimated
  (b) 比率（階高・柱間に対する比）と写真 1 枚 → estimated、tol は値の 20〜30%（見えない寸法は広く）
  (c) 2 枚以上で検算 → measured（図書館はメジャーの写真が無いので、measured は数（列・段）だけ）
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

BID = "library"
OUT = ROOT / "data" / "buildings" / f"{BID}.json"
REP = "data/reports/library"
BY = "設計係（sekkei・claude-opus-5-5）"
NOW = datetime.now().astimezone().isoformat(timespec="seconds")

# 仮説を外した記録（recorder hypotheses --drop が 1 行ずつ足す）。再生成しても外れたまま
HYPOTHESES_OFF: dict = {}

# --- 写真 id -----------------------------------------------------------------------------
P = "PXL_2026110"
F0_RECT = "PXL_20261105_070904733"          # f0 ほぼ正面（正面化・登録済み）
F0_FRONT2 = "PXL_20261103_075102006"
F0_SE = "PXL_20261103_080014086"            # 南東のテラスから南端
F0_S_UP = "PXL_20261105_064951872"          # 南から見上げ
F0_ARCH = "PXL_20261105_065035660"          # アーチの凹みを正面の近くから
F0_NE = "PXL_20261103_075153504"
F0_NE2 = "PXL_20261105_065122957"
F1_COV = ["PXL_20261103_075500328", "PXL_20261103_075437320"]   # coverage_f1 の上に来る 2 枚（登録済み）
F1_DECK = "PXL_20261105_063449740"          # 甲板から東を見通す（登録済み）
F1_WEST = "PXL_20261105_063455681"          # 西端の 3 列
F1_UNDER = "PXL_20261105_063517313"         # 列 8 の曲面ガラスと列 9 の軒下を真下から
F1_NW = "PXL_20261105_063529987"            # 北西の角の柱と風除室
F1_STAIR = ["PXL_20261103_075510510", "PXL_20261103_075517975"]
F2_RECT = "PXL_20261103_075639031"          # 正面入口（正面化・登録済み）
F2_NORTH = "PXL_20261105_063115879.MP"      # 北寄り（正面化・登録済み）
F2_CANOPY = "PXL_20261105_064354855"
F2_NREC = ["PXL_20261103_075705849", "PXL_20261105_063540463"]
F2_SW = ["PXL_20261105_064514061", "PXL_20261105_064524057"]
F2_PLATE = "PXL_20261103_075718159"
F3_RECT = "PXL_20261105_064942113"          # f3 を南東の角から（正面化・登録済み）
F3_SW = ["PXL_20261105_064539230", "PXL_20261103_075830062", "PXL_20261105_064510192"]

PLATEAU = "plateau:bldg_feae3e15-dd53-4b4a-8f30-bb05525d15e3"

# 物差しの許容（data/rulers.json の既定と同じ割合）
R_PLATEAU = 0.03
R_TILE = 0.005 / 0.070

# --- 1 つの値を配る元（ref で指す） ------------------------------------------------------
PARAPET = 25.3          # sokuryo_f0.json#parapet_for_registration（4 面同じと仮定して登録した高さの原点）
DECK_Z = 6.6            # 甲板・入口の床（f2 の正面化で入口の床 py 3395 → 25.3−(3395−601)/149.57）
F1_PITCH, F1_PIER = 5.384, 1.004
F1_M0 = 0.517           # 北東の角の柱型 1.019 − 中の柱型 1.004/2
F3_PITCH, F3_PIER = 5.385, 1.0
F3_M0 = 0.49
TILE_COLOR = "#b0916b"
# 進行役の決定（11/7）：K-005 大階段・f0 の前の段々の広場と空堀は棟 JSON に置く（steps＋composite）。K-006 階高は面ごとの値のまま
DECISION = "進行役 K-005・K-006"
F0_MID = 1.8            # f0 の中段の踊り場（エネルギーセンターの扉の下端と同じ高さ。parts「灰色の大きな段」の上の段）
F0_PLAZA_PHOTOS = ["PXL_20261105_070904733", "PXL_20261105_065101059", "PXL_20261103_075102006", "PXL_20261105_065027824",
                   "PXL_20261103_080014086", "PXL_20261105_064951872", "PXL_20261105_065035660"]
GRAND_PHOTOS = ["PXL_20261103_075629456", "PXL_20261103_075639031", "PXL_20261103_075623165", "PXL_20261105_062927980",
                "PXL_20261105_062929449", "PXL_20261105_062930796", "PXL_20261105_062946059", "PXL_20261103_080725416"]


# --- 値の書き方 ---------------------------------------------------------------------------
def est(v, tol, ev, method, unit="m", ref=None, note=""):
    d = schema.Measurement(v, unit, tol, "estimated", list(ev), method, note).to_json()
    if ref:
        d["ref"] = ref
    return d


def meas(v, tol, ev, method, unit="m", note=""):
    return schema.Measurement(v, unit, tol, "measured", list(ev), method, note).to_json()


def unk(unit="m", note=""):
    return schema.Measurement(None, unit, None, "unknown", [], "", note).to_json()


def tz(z, r):
    """高さの ±：パラペット上端（高さの原点）からの差 × 物差しの割合、と値そのもの × 割合の大きい方＋読み 0.05 m。"""
    return round(max(r * abs(z), r * abs(PARAPET - z)) + 0.05, 2)


def ts(s, r):
    return round(r * abs(s) + 0.05, 2)


def tb(v, k=0.3):
    """(b) 比率と写真 1 枚から置いた値の ±（値の 20〜30%。既定 30%）。"""
    return round(max(abs(v) * k, 0.05), 2)


M_F0 = ("正面化 out/rectified/library_f0_070904733（PLATEAU の辺 39.45 m を角の外から外 4480.6 px に当てた 113.58 px/m、"
        "x_origin 502 px、パラペット上端 933.5 px＝25.3 m。data/reports/library/sokuryo_f0.json#registration）で px を読んだ。"
        "coverage_f0_photo.png の左半分は 075051437 の木が上に来るので同じ登録の正面化を製図板にした")
M_F1 = ("coverage_f1_photo.png（40 px/m。登録の縮尺は PLATEAU の辺 49.42 m ÷ 5794.3 px、data/reports/library/sokuryo_f1.json#scale）"
        "で px を数えて ÷40（z＝33.7−py/40）")
M_F2 = ("正面化 out/rectified/library_f2_075639031（タイル 1 段 0.070 m で 149.57 px/m、x_origin −463 px、パラペット上端 601 px＝25.3 m。"
        "data/reports/library/sokuryo_f2.json#registration）で px を読んだ")
M_F2C = ("coverage_f2_photo.png（40 px/m。登録の縮尺はタイル 1 段、data/reports/library/sokuryo_f2.json#scale）で px を数えて ÷40（z＝33.7−py/40）")
M_F3 = ("正面化 out/rectified/library_f3_064942113 の南東の端（compare の 133 px/m、x_origin 1084 px、パラペット上端 251.8 px＝25.3 m。"
        "縦はタイル 1 段で換算した縮尺と 2.6% 違う。data/reports/library/sokuryo_f3.json#scale）で px を読んだ")


def zf0(z, what):
    return est(z, tz(z, R_PLATEAU), [F0_RECT], f"{M_F0}：{what}")


def sf0(s, what):
    return est(s, ts(s, R_PLATEAU), [F0_RECT], f"{M_F0}：{what}")


def zf1(z, what):
    return est(z, tz(z, R_PLATEAU), F1_COV, f"{M_F1}：{what}")


def zf2(z, what, ev=(F2_RECT,), m=None):
    return est(z, tz(z, R_TILE), list(ev), f"{m or M_F2}：{what}")


def sf2(s, what, ev=(F2_RECT,), m=None):
    return est(s, ts(s, R_TILE), list(ev), f"{m or M_F2}：{what}")


def zf3(z, what):
    return est(z, tz(z, R_TILE), [F3_RECT], f"{M_F3}：{what}（タイルの縮尺の ±）")


# --- 面の作り（glazing・bands・ledges） --------------------------------------------------
def glz(name, a0, a1, zb, zt, inset, depth=1.0):
    """面の一部のガラス（窓・凹みの奥のガラス）。a0/a1/zb/zt/inset/depth は測定値の dict。"""
    return {"name": name, "along0": a0, "along1": a1, "z_bottom": zb, "z_top": zt, "inset": inset, "depth": depth}


def band(name, a0, a1, zb, zt, dep, color=TILE_COLOR):
    return {"name": name, "along0": a0, "along1": a1, "z_bottom": zb, "z_top": zt, "depth": dep, "color": color}


def tile_material(ev, note):
    return [{"name": "タイル（小口・馬踏み目地）", "color": TILE_COLOR,
             "unit_w": est(0.108, 0.008, [F2_PLATE], "小口タイルの割付（108×60、目地 10）。縦横の目地の周期の比 1.68〜1.71 で小口と確かめた"
                           "（data/reports/library/sokuryo_f1.json の tile_type。寄り 075718159 で馬踏みの割付）"),
             "unit_h": est(0.060, 0.005, [F2_PLATE], "同上（1 段 0.070 m＝タイル 0.060＋目地 0.010。台帳 tile_course）"),
             "joint_w": est(0.010, 0.003, [F2_PLATE], "同上"),
             "note": f"色は写真の色の目安（光で変わる。測った値ではない）。{note}", "evidence_photos": list(ev)}]


# --- 組み立て部品（composite）の道具：棟ごとの語彙 ------------------------------------
def box(name, s, d, z, mat="tile", cut=False):
    p = {"shape": "box", "name": name, "s": [round(s[0], 3), round(s[1], 3)], "d": [round(d[0], 3), round(d[1], 3)],
         "z": [round(z[0], 3), round(z[1], 3)], "material": mat}
    if cut:
        p["cut"] = True
    return p


def wedge(name, s, d, z, rise, t=None, mat="concrete"):
    p = {"shape": "wedge", "name": name, "s": list(s), "d": list(d), "z": list(z), "rise": rise, "material": mat}
    if t is not None:
        p["t"] = t
    return p


def cyl(name, at, r, z, mat="concrete"):
    return {"shape": "cylinder", "name": name, "at": list(at), "r": r, "z": list(z), "material": mat}


def arc(name, center, r, deg, z, t=0.04, mat="glass"):
    return {"shape": "arc_wall", "name": name, "center": [round(center[0], 3), round(center[1], 3)], "r": round(r, 3),
            "deg": [round(deg[0], 2), round(deg[1], 2)], "z": list(z), "t": t, "material": mat}


def comp(name, face, prims, ev, method, tol, note=""):
    d = {"type": "composite", "name": name}
    if face:
        d["face"] = face
        d["along"] = 0.0
    d.update({"status": "estimated", "evidence": list(ev), "tol": tol, "method": method, "primitives": prims})
    if note:
        d["note"] = note
    return d


def proj_box(name, face, s0, s1, d, z, ev, method, tol, front=None, note=""):
    """張り出し箱：壁から d 出る箱（タイル）。front＝(s0, s1, z0, z1) の暗い開口（ガラリ）を正面に貼る。"""
    prims = [box("body", (s0, s1), (0.0, d), z)]
    if front:
        fs0, fs1, fz0, fz1 = front
        prims.append(box("opening", (fs0, fs1), (d, d + 0.02), (fz0, fz1), "dark"))
    return comp(name, face, prims, ev, method, tol, note)


def patch(name, face, a0, a1, z0, z1, mat, ev, method, tol_s, tol_z):
    """面の一部の板。along の ± は読みの幅 tol_s と、物差し（PLATEAU 3%）× 値の大きい方。"""
    return {"type": "patch", "name": name, "face": face,
            "along0": est(a0, max(tol_s, ts(a0, R_PLATEAU)), ev, method), "along1": est(a1, max(tol_s, ts(a1, R_PLATEAU)), ev, method),
            "z0": est(z0, tol_z, ev, method), "z1": est(z1, tol_z, ev, method), "material": mat}


def pipes(name, face, along, z0, z1, radius, ev, method, tol_s, count=1):
    return {"type": "pipes", "name": name, "face": face, "along": est(along, tol_s, ev, method),
            "count": est(count, None, ev, method, unit="本"),
            "radius": est(radius, tb(radius, 0.3), ev, "(b) 縦樋の太さは柱型の幅に対する比（約 0.1）から。" + method),
            "z0": est(z0, 0.5, ev, method), "z1": est(z1, tz(z1, R_PLATEAU), ev, method)}


def world(face, s, d, faces):
    f = faces[face]
    (x0, y0), (x1, y1) = f["from"], f["to"]
    L = math.hypot(x1 - x0, y1 - y0)
    ux, uy = (x1 - x0) / L, (y1 - y0) / L
    nx, ny = uy, -ux
    return [round(x0 + ux * s + nx * d, 2), round(y0 + uy * s + ny * d, 2)]


# =========================================================================================
def build() -> dict:
    b = from_site_cli(BID)
    faces = {f["id"]: f for f in b["faces"]}
    for f in b["faces"]:
        # 土台の ±1 m は PLATEAU の許容（3%）より小さいので、物差しの許容に直す
        L = f["length"]
        L["tol"] = round(R_PLATEAU * L["value"] + 0.01, 2)
        L["method"] = "PLATEAU LOD0 の足元の辺の長さ（± は PLATEAU の 3%）"

    # --- 棟の値 ----------------------------------------------------------------------------
    b["parapet_top"] = est(
        PARAPET, 1.3, [F0_RECT, "gsi:dem5a"],
        "パラペット上端（棟の z=0＝ground_z 41.67 m から）。正面化 070904733 で北東の広場の壁の足元 3840 px − パラペット上端 936.4 px "
        "＝ 2903.6 px ÷ 113.58 px/m（PLATEAU の辺 f0）＝ 25.56 m（広場から）、広場の 5m 標高 − ground_z ＝ −0.3 m を足して 25.3 m。"
        "4 面とも同じ高さ（観察票：上端は一直線、角で段がつながる）。未解決：PLATEAU measuredHeight 33.7 m と 8.4 m 違う"
        "（屋上の塔を含む最高の高さと見られる。値は合わせに行かない）",
        ref="data/reports/library/sokuryo_f0.json#parapet_for_registration")
    b["floor_count"] = meas(
        5, None, [F0_RECT, F0_FRONT2, "PXL_20261103_075437320", "PXL_20261103_075500328", F2_RECT, "PXL_20261105_064524057"],
        "見えた窓の段の数 L1〜L5。4 面とも 5 段で、角の写真で段がつながる（data/reports/library/kazoe_f0.json〜kazoe_f3.json の floors）。"
        "PLATEAU の storeysAboveGround 5 と合う（根拠にはしていない）", unit="階",
        note="空堀の底に面する地下の段は数え係の解釈（kazoe_f0.json の interpreted、仮説）で、見えた 5 段に混ぜない（levels の B1）")
    b["floor_pitch"] = est(
        4.472, 0.22, ["PXL_20261103_075437320", PLATEAU],
        "f1 の値（両端が入る正面化 075437320 で PLATEAU の辺 49.42 m ÷ 5794.3 px、階高 524.3 px。2 本目の物差しタイルとの差 +2.2%、"
        "柱間隔÷階高の比が 3 枚で 0.96% 以内）。他の面の換算値：f0 4.386（PLATEAU の辺 f0）・f2 4.566（タイル）・f3 4.607（タイル）。"
        "どの面もタイル 65 段前後で同じ階高。窓の高さは面ごとの製図板（それぞれの縮尺）から読んだので、ここは参考の 1 値",
        note="進行役 K-006：階高は面ごとの値のまま揃えない（窓の高さは各面の製図板の値）",
        ref="data/reports/library/sokuryo_f1.json#floor_pitch")
    b["levels"] = [{"id": "B1", "name": "地下（数え係の解釈）",
                    "z": unk(note="data/reports/library/kazoe_f0.json の interpreted（空堀の底に面した開口と窓。底の高さが写真で決まらない）。"
                                  "仮説の 3 規則（対称・同じ塊・足元）のどれにも当たらないので値は置かない。空堀の見下ろしの撮影依頼あり")}]

    # --- f0 東南東面 ----------------------------------------------------------------------
    f0 = faces["f0"]
    f0["name"] = "東南東面 f0（共通講義棟東の側。方位 118°、39.5 m。from＝南東の角）"
    f0["note"] = ("柱割り無し（data/reports/library/kazoe_f0.json の bays.not_applicable。無地ではない）。南北の窓の列・横長の彫り込みの枠・"
                  "張り出し箱 2・中央のアーチの凹み（L2〜L4）と L5 の帯窓。窓は柱割りが無いので glazing（ガラスの面）で書いた")
    f0["materials"] = tile_material([F0_RECT], "f0 は 070904733 の曇天の色")
    ins_w = est(0.3, tb(0.3), [F0_RECT], "(b) 窓の上の見込みの影が 070904733 で約 0.25 m。登録の視点（面から 13.3 m・高さ 1.5 m、registry）"
                "の見上げの角度で戻すと 0.2 m 前後。窓の奥行きは見えていないので 0.3 m と置いた")
    W0 = {}   # 窓（s0, s1, zb, zt, 窓下板の上端）
    W0["北 L4"] = (36.45, 38.30, 16.15, 19.15, 16.60)
    W0["北 L3"] = (36.45, 38.30, 11.75, 14.90, 12.30)
    W0["北 L2 右"] = (36.70, 38.30, 7.60, 10.55, 8.05)
    W0["北 L2 左"] = (28.75, 30.20, 7.60, 10.55, 8.05)
    glazing0 = []
    for k, (a0, a1, zb, zt, _sp) in W0.items():
        glazing0.append(glz(f"窓 {k}", sf0(a0, f"窓 {k} の南の縁"), sf0(a1, f"窓 {k} の北の縁"),
                            zf0(zb, f"窓 {k} の下端（窓下の濃い板の下）"), zf0(zt, f"窓 {k} の上端"), ins_w,
                            est(0.5, None, [F0_RECT], "ガラスの奥の暗い室内の板の厚さ（見た目の道具の値。寸法ではない）")))
    # 南の窓の列：右（北）の縁は正面化で 3.9 m（木の枝ごし）、幅は北の列の窓 1.85 m と同じと見た（080014086 で南北の窓が同じ大きさ）
    m_s = ("(b) 南の窓の列は 070904733 で木の枝ごしに北の縁 s 3.9 m だけ読める。幅は北の列の窓（1.85 m）と同じ、段は北の列と同じ"
           "（080014086・064951872 で南の窓が北の列と同じ大きさで、f3 の L2〜L4 と段が揃う）と見た")
    for lv, (zb, zt) in {"L4": (16.15, 19.15), "L3": (11.75, 14.90), "L2": (7.60, 10.55)}.items():
        W0[f"南 {lv}"] = (2.05, 3.90, zb, zt, zb + (0.45 if lv != "L3" else 0.55))
        glazing0.append(glz(f"窓 南 {lv}", est(2.05, tb(1.85, 0.25), [F0_RECT, F0_SE, F0_S_UP], m_s),
                            est(3.90, ts(3.9, R_PLATEAU) + 0.2, [F0_RECT], f"{M_F0}：南の窓の北の縁（木の枝ごし）"),
                            est(zb, tz(zb, R_PLATEAU), [F0_SE, F0_S_UP], m_s), est(zt, tz(zt, R_PLATEAU), [F0_SE, F0_S_UP], m_s),
                            ins_w, est(0.5, None, [F0_RECT], "ガラスの奥の暗い室内の板の厚さ（見た目の道具の値）")))
    # L5 の帯窓（アーチの真上）
    glazing0.append(glz("L5 の帯窓", sf0(15.55, "帯窓の南の縁 x 2266 px"), sf0(24.40, "帯窓の北の縁 x 3273 px"),
                        zf0(23.05, "帯窓の下端 py 1187"), zf0(23.75, "帯窓の上端 py 1110"), ins_w,
                        est(0.5, None, [F0_RECT], "ガラスの奥の暗い室内の板の厚さ（見た目の道具の値）")))
    # アーチの凹み（L2〜L4 の深い凹み、奥に平らなガラス）
    arch_depth = est(3.4, 1.0, [F0_RECT, F0_ARCH],
                     "(b) 凹みの南の脇の壁の見え幅：070904733 で凹みの南の縁 s 15.45 m から奥の角 s 19.3 m まで見える。登録の視点"
                     "（registry：面から 13.3 m、s 34.5 m）から奥の角の深さを戻すと 3.4 m。観察票の比（凹みの幅の 1/3 くらい＝3.0 m）と合う。"
                     "± は値の約 30%")
    glazing0.append(glz("アーチの凹み（奥の平らなガラス）", sf0(15.45, "凹みの南の縁"), sf0(24.35, "凹みの北の縁"),
                        zf0(7.75, "凹みの下端（水切りの上）"), zf0(19.55, "凹みの上端"), arch_depth,
                        est(4.0, None, [F0_RECT], "ガラスの奥の暗い室内の箱の奥行き（見た目の道具の値。寸法ではない）")))
    f0["glazing"] = glazing0
    f0["ledges"] = [{"name": "アーチの凹みの下の水切り", "z": zf0(7.55, "水切りの上面 7.75 から厚さ 0.2"),
                     "depth": est(0.15, tb(0.15), [F0_ARCH], "(b) 065035660 で水切りの出は窓の段の間隔の約 0.03"),
                     "height": est(0.2, tb(0.2), [F0_RECT, F0_ARCH], f"{M_F0}：水切りの厚さ（凹みの下端の明るい帯 7.55〜7.75）"),
                     "along0": est(15.2, tb(0.6) + 0.5, [F0_ARCH], "(b) 065035660 で水切りは凹みの両脇へ少し出る（凹みの幅の約 0.03 ずつ）"),
                     "along1": est(24.6, tb(0.6) + 0.5, [F0_ARCH], "(b) 同上"), "material": "concrete"}]
    dep_f = est(-0.05, 0.03, [F0_SE, F0_S_UP], "(b) 浅い凹み（斜めの写真で影の線が見える程度。観察票 kansatsu_f0.json の bands）")
    bands0 = []
    for side, (a0, a1) in {"北": (28.70, 38.40), "南": (1.95, 11.10)}.items():
        for lv, (zb, zt) in {"L4": (16.10, 19.55), "L3": (11.75, 15.05)}.items():
            ev_a0 = sf0(a0, f"{side}の枠の南の縁") if side == "北" else est(a0, tb(1.95, 0.3), [F0_SE], "(b) 南の枠は窓の列（2.05 m〜）を囲む。北の枠が窓を囲むのと同じ作り（080014086）")
            bands0.append(band(f"{side}の横長の彫り込みの枠 {lv}", ev_a0, sf0(a1, f"{side}の枠の北の縁"),
                               zf0(zb, f"{side}の枠 {lv} の下端"), zf0(zt, f"{side}の枠 {lv} の上端"), dep_f))
    m_l1 = "L1 の凹んだ大きな板（観察票 kansatsu_f0.json の parts）。"
    # 検収（11/7）：板の上の枠の線は recheck で 3 枚とも +0.11〜+0.12（070904733・075102006・075051437）、正面化 070904733 で 6.17。6.0 → 6.1
    m_l1top = ("板の上の枠の線。検収係が正面化 070904733 で読み直して 6.17、recheck（070904733・075102006・075051437）で 6.0 から +0.11〜+0.12 "
               "→ 6.1（data/reports/library/kenshu_summary.json）")
    bands0.append(band("L1 の凹んだ板（アーチの下）", sf0(15.45, m_l1 + "アーチの凹みと同じ幅（065035660）"), sf0(24.35, m_l1 + "同上"),
                       est(0.0, 0.6, [F0_ARCH], "(b) 見えていない：下端は空堀の擁壁に隠れる。北東の広場の高さ（z 約 −0.3〜0）まで続くと置いた"),
                       zf0(6.1, m_l1top), dep_f))
    # 検収（11/7）：柱型の縁を正面化 070904733 で読み直した（柱型は面の平面なので視差なし）。南の板 28.86〜30.56、北の板 36.66〜38.47
    for k, (a0, a1) in enumerate([(28.86, 30.57), (36.66, 38.47)]):
        bands0.append(band(f"L1 の凹んだ板（北の柱型の{'左' if k == 0 else '右'}）",
                           sf0(a0, m_l1 + "板の南の縁（検収係が正面化 070904733 で読み直し。設計 " + ("29.0" if k == 0 else "37.0") + "）"),
                           sf0(a1, m_l1 + "板の北の縁（検収係が読み直し。設計 " + ("30.6" if k == 0 else "38.6") + "）"),
                           zf0(-0.3, "北東の広場の壁の足元 py 3840"), zf0(6.1, m_l1top), dep_f))
    f0["bands"] = bands0

    # --- f1 北北東面 ----------------------------------------------------------------------
    f1 = faces["f1"]
    f1["name"] = "北北東面 f1（タワー75 との間の坂の道。方位 28°、49.4 m。from＝北東の角）"
    f1["note"] = ("9 列 × 5 段の彫りの深い格子（列は東から 1〜9）。列 7・8 の L2〜L3 は凹みの全面ガラス、列 9 の L2〜L3 は北西の角の軒下"
                  "（部品の composite）。L1 は無窓の凹んだ板")
    f1["materials"] = tile_material(F1_COV, "f1 は 075500328 の夕方の色")
    ref_p = "data/reports/library/sokuryo_f1.json#bay_pitch"
    f1["bays"] = {
        "count": meas(9, None, ["PXL_20261103_075437320", F1_DECK, "PXL_20261103_075500328"],
                      "両端が入る 2 枚で別々に数えて 9、縦樋 4 本の位置も同じ（data/reports/library/kazoe_f1.json#bays）", unit="列"),
        "pitch": est(F1_PITCH, 0.19, ["PXL_20261103_075437320", PLATEAU],
                     "PLATEAU の辺 49.42 m ÷ 5794.3 px＝117.25 px/m、柱間隔 631.3 px（data/reports/library/sokuryo_f1.json#bay_pitch）", ref=ref_p),
        "pier_width": est(F1_PIER, 0.04, ["PXL_20261103_075437320"], "中の柱型 117.7 px ÷ 117.25 px/m",
                          ref="data/reports/library/sokuryo_f1.json#pier_and_recess.pier_width"),
        "margin_from": est(F1_M0, 0.06, ["PXL_20261103_075437320"],
                           "北東の角の柱型 1.019 m（119.5 px）の内の縁が列 1 の凹みの縁。柱割りの線（柱型の中心）は 1.019 − 1.004/2",
                           ref="data/reports/library/sokuryo_f1.json#pier_and_recess.corner_pier_NE"),
        "margin_to": est(round(49.42 - F1_M0 - 9 * F1_PITCH, 3), 0.1, ["PXL_20261103_075437320"],
                         "辺 49.42 − margin_from − 9 × 柱間隔（北西の角の柱型 0.965 m と 2% で合う）",
                         ref="data/reports/library/sokuryo_f1.json#pier_and_recess.corner_pier_NW"),
    }
    rec_big = est(1.2, tb(1.2), F1_COV,
                  "(b) 窓の上の見込みの天井の見え幅：coverage_f1 で L4 1.4 m・L3 1.0 m・L2 0.85 m。075500328 の登録の視点"
                  "（registry：面から 11.8 m、高さ 2.8 m）の見上げの角度で戻すと 1.1〜1.5 m。奥行きは見えていないので 1.2 m と置いた")
    rec_small = est(0.75, tb(0.75), F1_COV, "(b) L5 の小窓の見込みの見え幅 1.2 m を同じ視点で戻して 0.7 m 前後")
    sp_ev = ["PXL_20261103_075500328"]
    w_big1 = est(4.38, 0.15, ["PXL_20261103_075437320"], "L4 の凹み 513.5 px ÷ 117.25 px/m（PLATEAU の辺。柱型:凹み＝1:4.36）",
                 ref="data/reports/library/sokuryo_f1.json#pier_and_recess.recess_width")
    all9 = list(range(1, 10))
    f1["windows"] = [
        {"name": "L2 大窓", "width": w_big1, "bays": [1, 2, 3, 4, 5, 6], "z_bottom": zf1(7.60, "L2 のガラスの下端"), "z_top": zf1(10.40, "L2 の窓の凹みの上端"),
         "recess": rec_big, "spandrel": {"height": est(0.45, 0.1, sp_ev, f"{M_F1}：窓下の濃い板 7.15〜7.60（観察票：段の間隔の約 0.11）"),
                                         "depth": est(0.03, None, sp_ev, "板の厚さ（見た目の値）"), "material": "dark", "in_recess": True}},
        {"name": "L3 大窓", "width": w_big1, "bays": [1, 2, 3, 4, 5, 6], "z_bottom": zf1(11.70, "L3 のガラスの下端"), "z_top": zf1(14.90, "L3 の窓の凹みの上端"),
         "recess": rec_big, "spandrel": {"height": est(0.25, 0.08, sp_ev, f"{M_F1}：窓下の濃い板 11.45〜11.70（観察票：段の間隔の約 0.05）"),
                                         "depth": est(0.03, None, sp_ev, "板の厚さ（見た目の値）"), "material": "dark", "in_recess": True}},
        {"name": "L4 大窓", "width": w_big1, "bays": all9, "z_bottom": zf1(16.00, "L4 の窓の凹みの下端"), "z_top": zf1(19.40, "L4 の窓の凹みの上端"),
         "recess": rec_big},
        {"name": "L5 小窓（各列 2）", "bays": all9, "per_bay": 2, "z_bottom": zf1(20.40, "L5 の小窓の凹みの下端"),
         "z_top": zf1(23.50, "L5 の小窓の凹みの上端"), "recess": rec_small,
         "width": est(F1_PITCH, 0.19, F1_COV, "2 つの小窓の組の幅の決まり：外の縁は下の大窓の凹みの縁に揃い、間の壁は柱型と同じ幅"
                      "（観察票 kansatsu_f1.json）。生成器では width＝柱間隔、gap＝柱型の幅で、小窓 1 つ 1.69 m（coverage_f1 で 1.7・1.75 m）",
                      ref="faces.f1.bays.pitch"),
         "gap": est(F1_PIER, 0.04, F1_COV, "2 つの小窓の間の壁＝柱型の幅（coverage_f1 で 0.95 m）", ref="faces.f1.bays.pier_width")},
    ]
    piers = [F1_M0 + i * F1_PITCH for i in range(10)]
    dep1 = est(-0.05, 0.03, F1_COV, "(b) 浅い凹み（観察票 kansatsu_f1.json：L1 の凹んだ板。凹凸は目地の影程度）")
    f1["bands"] = [band(f"L1 の凹んだ板 列 {i}", est(round(piers[i - 1] + F1_PIER / 2, 3), 0.2, F1_COV, "列の凹みの縁（柱割りから）", ref="faces.f1.bays"),
                        est(round(piers[i] - F1_PIER / 2, 3), 0.2, F1_COV, "同上", ref="faces.f1.bays"),
                        est(0.5, 0.5, F1_COV, "(b) 見えていない：下端は歩道（列 1 で約 0.7、列 4 で約 1.6。坂）より下。地面で隠れる所まで"),
                        zf1(6.0, "L1 の板の上の枠の線 py 1110"), dep1) for i in range(1, 7)]
    f1["ledges"] = [{"name": "L1 と L2 の境の水切り（列 1〜6）", "z": zf1(7.0, "細い横の線 py 1068"),
                     "depth": est(0.05, None, F1_COV, "細い線（見た目の値）"), "height": est(0.06, None, F1_COV, "細い線（見た目の値）"),
                     "along0": est(0.0, 0.2, F1_COV, "北東の角から"), "along1": est(round(piers[6], 2), 0.3, F1_COV, "列 6 の西の柱型まで", ref="faces.f1.bays"),
                     "material": "tile"}]
    ins78 = est(1.3, tb(1.3), [F1_UNDER, F1_WEST], "(b) 063517313 でガラスは柱型の面から柱間隔の約 1/4 奥。奥行きは真横の写真が無く見えていない")
    f1["glazing"] = [
        glz(f"列 {c} の凹みの全面ガラス（L2〜L3）", est(round(piers[c - 1] + F1_PIER / 2, 3), 0.2, F1_COV, "列の凹みの縁（柱割りから）", ref="faces.f1.bays"),
            est(round(piers[c] - F1_PIER / 2, 3), 0.2, F1_COV, "同上", ref="faces.f1.bays"),
            est(DECK_Z, tz(DECK_Z, R_TILE), [F2_RECT, F1_WEST], "甲板・入口の床（L2 の床。観察票：外階段の踊り場・甲板の床は L2 の床とほぼ同じ）", ref="design_library.DECK_Z"),
            zf1(14.90, f"列 {c} の凹みの上端（L3 の窓の凹みの上端と同じ線）"), ins78,
            est(6.0, None, [F1_UNDER], "ガラスの奥の暗い室内の箱の奥行き（見た目の道具の値。寸法ではない）")) for c in (7, 8)]

    # --- f2 西北西面 ----------------------------------------------------------------------
    f2 = faces["f2"]
    f2["name"] = "西北西面 f2（本部棟の側。正面入口。方位 298°、39.4 m。from＝北西の角）"
    f2["note"] = ("柱割り無し（data/reports/library/kazoe_f2.json の bays.not_applicable。無地ではない）。北の凹み（L2〜L3）・銘板の柱型・"
                  "正面入口の深い凹み（L2〜L4）・横長の彫り込みの枠・南西の角の窓の列。南寄り（s 34 m 〜南西の角）は本部棟に隠れ、"
                  "南西の角からの斜めの写真だけ")
    f2["materials"] = tile_material([F2_RECT], "f2 は 075639031 の夕方の色")
    ins_w2 = est(0.3, tb(0.3), [F2_RECT, "PXL_20261105_063540463"], "(b) f0 の窓と同じ作りの浅い窓（063540463 で北の L4 の窓の見込みは浅い）。見えていない")
    dark_box = est(0.5, None, [F2_RECT], "ガラスの奥の暗い室内の板の厚さ（見た目の道具の値）")
    ent_depth = est(3.2, 1.0, [F2_RECT, F2_CANOPY],
                    "(b) 入口の凹みの天井の見え幅：075639031 の正面化で凹みの上端 19.16 m からガラスの壁の上端 16.6 m まで 2.56 m。"
                    "登録の視点（registry：面から 12.5 m、高さ 6.5 m）の見上げの角度で戻すと 3.2 m。± は約 30%（ガラスの上に暗い板があれば浅くなる）")
    nrec_depth = est(1.5, tb(1.5), F2_NREC, "(b) 063540463 で北の凹みの天井の見え幅は凹みの高さの約 0.18。奥行きは真横の写真が無く見えていない")
    f2["glazing"] = [
        glz("正面入口の凹み（奥のガラスの壁）", sf2(15.53, "凹みの北の縁 x 1860 px（帯窓の左の縁と同じ。sokuryo_f2.json#band_window_position）"),
            sf2(24.65, "凹みの南の縁 x 3223 px"), zf2(DECK_Z, "入口の床 py 3395"), zf2(19.16, "凹みの上端 py 1520"), ent_depth,
            est(6.0, None, [F2_RECT], "ガラスの奥の暗い室内の箱の奥行き（見た目の道具の値）")),
        glz("北の凹み（奥のガラスの壁。北端の軒下は部品）", est(5.4, 1.6, F2_NREC, "(b) 北西の角の軒下（部品）の南の縁から。軒下の奥行きと同じ"),
            sf2(10.8, "北の凹みの南の縁（W の 0.12＋1.07 倍、W＝入口の凹みの幅 9.12 m。kansatsu_f2.json の segments）", ev=[F2_NORTH] + F2_NREC),
            zf2(DECK_Z, "甲板の床（入口の床と同じ高さ）"), zf2(14.6, "北の凹みの上端 py 765", ev=[F2_NORTH], m=M_F2C), nrec_depth,
            est(6.0, None, F2_NREC, "ガラスの奥の暗い室内の箱の奥行き（見た目の道具の値）")),
        glz("L5 の帯窓", sf2(15.53, "帯窓の北の縁"), sf2(24.65, "帯窓の南の縁"), zf2(22.89, "帯窓の下端 py 961"), zf2(23.47, "帯窓の上端 py 874"),
            ins_w2, dark_box),
        glz("L5 の小窓（北）", sf2(10.02, "小窓の北の縁 x 1036 px"), sf2(11.0, "小窓の南の縁 x 1182 px"), zf2(20.99, "小窓の下端 py 1246"),
            zf2(23.53, "小窓の上端 py 865"), ins_w2, dark_box),
        glz("L5 の小窓（南）", sf2(29.17, "小窓の北の縁 x 3899 px"), sf2(30.2, "小窓の南の縁 x 4054 px"), zf2(20.99, "小窓の下端"),
            zf2(23.53, "小窓の上端"), ins_w2, dark_box),
        glz("L4 の窓（北の枠の北端）", sf2(1.08, "窓の北の縁 x 43 px", ev=[F2_NORTH], m=M_F2C), sf2(3.05, "窓の南の縁 x 122 px", ev=[F2_NORTH], m=M_F2C),
            zf2(15.8, "窓の下端 py 715", ev=[F2_NORTH], m=M_F2C), zf2(18.95, "窓の上端 py 590", ev=[F2_NORTH], m=M_F2C), ins_w2, dark_box),
    ]
    m_sw = ("(b) 南西の角の窓の列は 064514061・064524057 の急な斜めだけ（本部棟に隠れて正面が無い）。段は f3 の西端の列の L2〜L4 と揃う"
            "（kazoe_f2.json）。幅・高さは段の間隔（4.47 m）に対する比で、L2 は幅 0.55・高さ 0.6、L3 は幅 0.3・高さ 0.65、"
            "L4 は幅 0.22・高さ 0.5。南の縁は南西の角の柱型（約 1 m、f1 の角の柱型と同じくらいに見える）の内側")
    for nm, (a0, a1, zb, zt) in {"L2": (35.90, 38.40, 7.40, 10.10), "L3": (37.05, 38.40, 11.70, 14.60), "L4": (37.45, 38.40, 16.40, 18.65)}.items():
        f2["glazing"].append(glz(f"南西の角の窓 {nm}", est(a0, tb(38.4 - a0) + 0.1, F2_SW, m_sw), est(a1, 0.4, F2_SW, m_sw),
                                 est(zb, tz(zb, R_TILE), F2_SW, m_sw), est(zt, tz(zt, R_TILE), F2_SW, m_sw), ins_w2, dark_box))
    dep2 = est(-0.05, 0.03, [F2_RECT], "(b) 浅い彫り込み（075639031 で板の縁に細い影。観察票 kansatsu_f2.json の bands）")
    f2["bands"] = [band("北の横長の彫り込みの枠 L4", sf2(1.0, "枠の北の縁 x 40 px", ev=[F2_NORTH], m=M_F2C),
                        sf2(11.0, "枠の南の縁 x 440 px", ev=[F2_NORTH], m=M_F2C), zf2(15.8, "枠の下端 py 715", ev=[F2_NORTH], m=M_F2C),
                        zf2(19.25, "枠の上端 py 578", ev=[F2_NORTH], m=M_F2C), dep2)]
    m_end = ("(b) 南の端は本部棟に隠れて正面からは見えない。064514061 で枠が南西の角の窓の列まで続く（窓は枠の南端）ので、"
             "南西の角の柱型の内側（38.4 m）まで続くと置いた（同じ枠かは確かめられない：kansatsu_f2.json）")
    for nm, (zb, zt) in {"L4": (15.73, 19.25), "L3": (11.15, 14.62), "L2": (7.36, 10.04)}.items():
        f2["bands"].append(band(f"入口の南の横長の彫り込みの板 {nm}", sf2(29.15, "板の北の縁 x 3897 px"),
                                est(38.4, 1.5, [F2_RECT] + F2_SW, m_end), zf2(zb, f"板の下端"), zf2(zt, f"板の上端"), dep2))

    # --- f3 南南西面 ----------------------------------------------------------------------
    f3 = faces["f3"]
    f3["name"] = "南南西面 f3（11 号館の側。方位 208°、49.4 m。from＝南西の角）"
    f3["note"] = ("f1 と同じ作りの 9 列 × 5 段の格子（列は西から 1〜9）。写真は角からの急な斜めだけ（coverage の使える所 0.6%）。"
                  "窓の高さは南東の端の列を正面化で読み、中ほどの列の L2 は竹に隠れるので同じ面の見えた列から仮説で写した")
    f3["materials"] = tile_material([F3_RECT], "f3 は 064942113 の色")
    f3["bays"] = {
        "count": meas(9, None, [F3_RECT] + F3_SW[:2],
                      "2 枚の鮮明な半分を縦樋でつないで 9（8・10・11 列は縦樋の位置が合わない）。正面化の L5 の小窓 18 個も等間隔"
                      "（data/reports/library/kazoe_f3.json#bays）", unit="列"),
        "pitch": est(F3_PITCH, 0.2, [F3_RECT, PLATEAU], "PLATEAU の辺 49.42 m ÷ 角の外から外の 9.177 列（data/reports/library/sokuryo_f3.json#bay_pitch）",
                     ref="data/reports/library/sokuryo_f3.json#bay_pitch"),
        "pier_width": est(F3_PIER, 0.06, [F3_RECT], "正面化 064942113 の南東の端で中の柱型 136.4 px ÷ 1 列 734.0 px × 柱間隔（PLATEAU の辺から）",
                          ref="data/reports/library/sokuryo_f3.json#readings_px"),
        "margin_from": est(F3_M0, 0.1, [F3_RECT], "南西の角の柱型（南東と同じ 0.98 m と仮定：sokuryo_f3.json）の半分",
                           ref="data/reports/library/sokuryo_f3.json#bay_pitch"),
        "margin_to": est(round(49.42 - F3_M0 - 9 * F3_PITCH, 3), 0.1, [F3_RECT], "辺 49.42 − margin_from − 9 × 柱間隔（南東の角の柱型 0.98 m の半分と合う）",
                         ref="data/reports/library/sokuryo_f3.json#bay_pitch"),
    }
    w_big3 = est(4.39, 0.2, [F3_RECT], "正面化 064942113 の南東の端で L4 の凹み 598.5 px ÷ 1 列 734.0 px × 柱間隔 5.385 m（柱型:凹み＝1:4.39）",
                 ref="data/reports/library/sokuryo_f3.json#readings_px")
    rec3 = est(1.2, tb(1.2), [F3_RECT], "(b) 064942113 で凹みの脇の壁が深く見え、f1 と同じ作り（観察票 kansatsu_f3.json）。奥行きは見えていない")
    rec3s = est(0.75, tb(0.75), [F3_RECT], "(b) L5 の小窓の脇の壁の見え方が f1 と同じ。奥行きは見えていない")
    l2_seen = {"name": "L2 大窓（見えた列）", "width": w_big3, "bays": [1, 8, 9], "z_bottom": zf3(7.55, "L2 のガラスの下端"), "z_top": zf3(10.60, "L2 の凹みの上端"),
               "recess": rec3, "spandrel": {"height": est(0.15, 0.08, [F3_RECT], f"{M_F3}：窓下の濃い板（竹ごし）"),
                                             "depth": est(0.03, None, [F3_RECT], "板の厚さ（見た目の値）"), "material": "dark", "in_recess": True}}
    why_h = "同じ面の見えた列（西端の列 1 と東端の列 8・9）と同じ窓。中ほどの列の L2 は竹と木に隠れて見えない（kazoe_f3.json の floors.hidden）"
    l2_hyp = {"name": "L2 大窓（中ほどの列。仮説）", "bays": [2, 3, 4, 5, 6, 7],
              "z_bottom": schema.hypothesis_from(l2_seen["z_bottom"], "same_block", "f3", why_h),
              "z_top": schema.hypothesis_from(l2_seen["z_top"], "same_block", "f3", why_h),
              "recess": schema.hypothesis_from(rec3, "same_block", "f3", why_h),
              "width": schema.hypothesis_from(w_big3, "same_block", "f3", why_h)}
    f3["windows"] = [
        l2_seen, l2_hyp,
        {"name": "L3 大窓", "width": w_big3, "bays": all9, "z_bottom": zf3(11.75, "L3 のガラスの下端"), "z_top": zf3(15.05, "L3 の凹みの上端"), "recess": rec3,
         "spandrel": {"height": est(0.2, 0.08, [F3_RECT], f"{M_F3}：窓下の濃い板"), "depth": est(0.03, None, [F3_RECT], "板の厚さ（見た目の値）"),
                      "material": "dark", "in_recess": True}},
        {"name": "L4 大窓", "width": w_big3, "bays": all9, "z_bottom": zf3(16.05, "L4 の凹みの下端"), "z_top": zf3(19.55, "L4 の凹みの上端"), "recess": rec3},
        {"name": "L5 小窓（各列 2）", "bays": all9, "per_bay": 2, "z_bottom": zf3(20.55, "L5 の小窓の凹みの下端"), "z_top": zf3(23.60, "L5 の小窓の凹みの上端"),
         "recess": rec3s,
         "width": est(F3_PITCH, 0.2, [F3_RECT], "2 つの小窓の外の縁は大窓の凹みの縁に揃い、間の壁は柱型と同じ幅（kazoe_f3.json の exceptions）。"
                      "生成器の決まりで width＝柱間隔", ref="faces.f3.bays.pitch"),
         "gap": est(F3_PIER, 0.06, [F3_RECT], "2 つの小窓の間の壁＝柱型の幅", ref="faces.f3.bays.pier_width")},
    ]

    # =========================================================================================
    parts: list[dict] = []
    # --- f0 の部品 ---------------------------------------------------------------------------
    # 検収（11/7）：正面化は壁の面に写した見かけなので、壁から 1.1 m 出た箱の縁は登録の視点（070904733：面から 13.31 m、s 34.5 m、
    # 高さ 1.53 m）の側へずれて大きく高く写る。見かけ → 実際＝視点 + (見かけ − 視点) × (13.31 − 出) / 13.31。出は同じ写真の箱の下面の
    # 斜めの縁（前の下の縁 見かけ z 8.42・壁との境 7.81、左の縁 見かけ s 30.20・壁との境 30.55）から 1.17・1.08 m と出て、設計の 1.1 m と合う
    m_box = ("検収係（data/reports/library/kenshu_summary.json）が正面化 070904733 の見かけの縁を登録の視点（registry：面から 13.31 m・s 34.5 m・"
             "高さ 1.53 m）で箱の前の面（出 1.1 m）へ戻した：見かけ s 30.20・36.85 → 30.57・36.65、見かけの上端 11.49 → 10.65、下面は壁との境で 7.8"
             "（見かけの前の下の縁 8.42 を戻しても 7.84）、開口 見かけ s 32.78〜36.32・z 8.6〜10.73 → s 32.95〜36.2・z 8.0〜9.95。"
             "出 1.1 m は同じ写真の下面の斜めの縁（z の差 0.61 m・s の差 0.35 m）から 1.17・1.08 m と出る。設計は見かけの値のまま"
             "（recheck で左端 +0.35・+0.44、下端 −0.20、上端は探す幅 0.5 m の外）。2 回目の recheck（20261107_150541）で上端は "
             "070904733 +0.20・075102006 +0.21 とそろったので 10.65 → 10.85（上面の縁の読みが戻しの式より 0.2 m 高い）")
    parts.append(proj_box(
        "張り出し箱（北、L2）", "f0", 30.57, 36.65, 1.1, (7.8, 10.85), [F0_RECT, F0_FRONT2, F0_NE, F0_NE2],
        f"{M_F0}。{m_box}", 0.3, front=(32.95, 36.2, 8.0, 9.95)))
    parts.append(comp("張り出し箱の下の幅の広い柱型（北）", "f0", [box("pier", (30.57, 36.66), (0.0, 0.15), (-0.3, 7.8))], [F0_RECT, "PXL_20261103_075111985"],
                      f"{M_F0}：柱型の縁 s 30.57・36.66（検収係が正面化 070904733 で読み直し。設計 30.6・37.0、recheck の右端 −0.37）、"
                      "上は箱の下面 7.8、足元は北東の広場 −0.3。出は (b)：斜めの写真で段が見える程度＝0.15 m", 0.1))
    parts.append(proj_box(
        "張り出し箱（南、L2）", "f0", 4.0, 9.1, 0.9, (7.8, 11.0), [F0_SE, "PXL_20261103_080029484", F0_S_UP, "PXL_20261105_065001701"],
        "(b) 南の箱は木と段に隠れ、正面化では読めない。幅は面の約 0.13（観察票 kansatsu_f0.json：s 0.10〜0.23 × 39.45 m）、"
        "高さは北の箱と同じ（観察票：段の間隔の約 0.9、北の箱と同じと見た。北の箱は検収で 7.8〜10.85 に直した）。上端は検収の recheck（20261107_150541）で "
        "070904733 +0.38・075114044 +0.32 → 11.0（北の箱と 0.15 m 違うのは読みの幅の内）、出は L2 の窓の幅の半分（約 0.9 m）。"
        "正面の上の奥に横長のガラリ（080014086 で箱の幅の約 0.8、高さの約 0.3）。± は値の 30%", 1.5, front=(4.5, 8.6, 9.6, 10.4)))
    parts.append(comp("張り出し箱の下の幅の広い柱型（南）", "f0", [box("pier", (4.3, 8.8), (0.0, 0.15), (0.0, 7.8))], [F0_SE, F0_S_UP],
                      "(b) 080014086 で箱の下に地面まで幅の広い柱型（幅は箱とほぼ同じ）。下は木とテラスの腰壁に隠れて見えない。"
                      "幅・位置は箱から、足元は z 0（見えていない）", 1.4))
    # アーチの曲面ガラス：凹みの奥の壁（深さ 3.4 m）から外へふくらむ縦長のガラス、上は半円
    cg_c, cg_chord, cg_sag, cg_back = 19.9, 5.1, 2.0, -3.4
    R = (cg_chord ** 2 / 4 + cg_sag ** 2) / (2 * cg_sag)
    cd = cg_back - (R - cg_sag)
    half = math.degrees(math.asin(cg_chord / 2 / R))
    rings = [arc("glass", (cg_c, cd), R, (90 - half, 90 + half), (7.9, 16.5))]
    for k, (z0, z1, f) in enumerate([(16.5, 17.3, 0.92), (17.3, 17.9, 0.75), (17.9, 18.35, 0.52), (18.35, 18.6, 0.25)]):
        hc = cg_chord / 2 * f
        sag_k = max(cg_sag * f, 0.05)
        Rk = (hc ** 2 + sag_k ** 2) / (2 * sag_k)
        hk = math.degrees(math.asin(min(hc / Rk, 1.0)))
        rings.append(arc(f"dome{k}", (cg_c, cg_back - (Rk - sag_k)), Rk, (90 - hk, 90 + hk), (z0, z1)))
    parts.append(comp(
        "アーチの曲面ガラス（凹みの奥）", "f0", rings, [F0_ARCH, F0_RECT, "PXL_20261105_065016249"],
        "(b) 065035660（正面の近く）で曲面ガラスは凹みの中央、幅は凹みの幅の約 0.57（5.1 m）、下端は水切りの上、上は半円。"
        "070904733 の正面化では奥にあるため視差で北へずれて s 19.4〜23.9 m に見える（登録の視点 s 34.5 m の側）。"
        "ふくらみ（奥の壁から 2.0 m）は見えていないので、凹みの奥行き 3.4 m に収まる弧と置いた。上の半円は 4 段の弧で近似。± は値の 30%",
        1.5))
    parts.append(patch("扉（エネルギーセンター）", "f0", 25.4, 26.33, F0_MID, 4.08, "dark", [F0_RECT, F0_FRONT2, "PXL_20261105_065036832"],
                       f"{M_F0}：扉の枠 s 25.40〜26.33（x 387〜492 px を検収係が読み直し。設計 25.15〜26.0 は recheck で 070904733 +0.25・+0.32、"
                       "075102006 +0.31・+0.32 と 2 枚そろってずれた）、上端 4.08。下端は中段の踊り場の腰壁に隠れ、踊り場の高さ 1.8 m"
                       "（parts「灰色の大きな段」の上の段）と置いた（観察票：扉は中段の踊り場の高さ）",
                       0.1, 0.3))
    m_p0 = "080014086・064926962 で南東の角に沿って太い縦樋 2 本が全高。位置は角の柱型の上（(b) 角から柱型の幅の 0.15・0.45）"
    parts.append(pipes("南東の角の太い縦樋", "f0", 0.25, -0.5, PARAPET, 0.12, [F0_SE, "PXL_20261105_064926962"], m_p0, 0.2, count=2))
    parts[-1]["note"] = "2 本は 0.3 m おき（生成器の決まり）"
    m_pa = ("(b) 065035660 でアーチの凹みの両脇の L1 の壁を縦樋（ピンク色）が下りる。位置は凹みの両脇の縁の外 0.2 m（凹みの幅の約 0.02）、"
            "下は地面、上は凹みの下端の水切り。凹みの中の続きは奥で見えないので置かない")
    parts.append(pipes("アーチの脇の縦樋（南）", "f0", 15.25, -0.3, 7.6, 0.06, [F0_ARCH], m_pa, 0.3))
    parts.append(pipes("アーチの脇の縦樋（北）", "f0", 24.55, -0.3, 7.6, 0.06, [F0_ARCH], m_pa, 0.3))

    # f0 の前の段々の広場と空堀（K-005：棟 JSON に置く）。灰色の段は steps（面へ上がる）、赤い煉瓦の段・テラス・空堀の擁壁と手すりは composite
    m_gray = ("(b) 070904733 の正面化で灰色の段 7（観察票 6〜7）。北東の広場 −0.3 m（sokuryo_f0.json#parapet_for_registration）から中段の踊り場"
              "（エネルギーセンターの扉の下端：扉の枠の上端 4.05 m − 扉の高さ（幅 0.85 m の約 2.5 倍）≒ 1.8 m）まで 2.1 m を 7 段で割って 1 段 0.3 m。"
              "065101059 で段は面へ向かって上がり、北は面に直交するコンクリートの壁（手すり付き）で切れ、上の段は扉の前の踊り場。"
              "位置は正面化の見かけ s 16〜29 m（手前の物なので視差で登録の視点 s 34.5 m・面から 13.3 m の側へ広がる）を戻して s 22〜30 m、"
              "踏み面は蹴上げの約 2 倍に見える（065101059）ので 0.6 m、上の踊り場の奥行き 4.4 m。± は値の 30%")
    parts.append({"type": "steps", "name": "灰色の大きな段と中段の踊り場（f0 の前）", "face": "f0",
                  "along": est(22.0, tb(8.0), F0_PLAZA_PHOTOS, m_gray), "width": est(8.0, tb(8.0), F0_PLAZA_PHOTOS, m_gray),
                  "depth": est(8.0, tb(8.0), F0_PLAZA_PHOTOS, m_gray),
                  "count": est(7, None, [F0_RECT, "PXL_20261105_065101059"],
                               "070904733・065101059 で見えた灰色の段の数（観察票 kansatsu_f0.json の structures は 6〜7）", unit="段"),
                  "rise": est(0.3, tb(0.3), F0_PLAZA_PHOTOS, m_gray), "tread": est(0.6, tb(0.6), F0_PLAZA_PHOTOS, m_gray),
                  "z0": est(-0.3, 0.8, [F0_RECT, "gsi:dem5a"], "北東の広場の高さ（data/reports/library/sokuryo_f0.json#parapet_for_registration）",
                            ref="data/reports/library/sokuryo_f0.json#parapet_for_registration"),
                  "edge": "straight", "material": "stone", "note": DECISION + "：棟 JSON に置く（f0 の前の段々の広場）"})
    red_n, red_rise, red_tread, red_s1, red_d = 8, 0.2, 0.35, 22.0, (2.8, 8.0)
    red = [box(f"red_step{k}", (red_s1 - (k + 1) * red_tread, red_s1 - k * red_tread), red_d, (-1.0, F0_MID + (k + 1) * red_rise), "brick")
           for k in range(red_n)]
    terrace_z = F0_MID + red_n * red_rise
    red.append(box("terrace", (8.0, red_s1 - red_n * red_tread), red_d, (-1.0, terrace_z), "brick"))
    parts.append(comp("赤い煉瓦の段と南東のテラスの北の端（f0 の前）", "f0", red, F0_PLAZA_PHOTOS,
                      "(b) 070904733・065101059 で中段の踊り場から南へ赤い煉瓦の段 8（観察票 7〜8）が面に沿って上がり、南東の高いテラスへ続く。"
                      "065027824 でテラスの煉瓦の床は空堀の擁壁のすぐ外。1 段の高さは灰色の段より低く見える（約 2/3）ので 0.2 m、踏み面 0.35 m、"
                      "幅（面からの出）は 065027824 で空堀の幅の約 2 倍＝擁壁の外 d 2.8〜8.0 m。テラスは s 8 m まで置き、その南（11 号館の屋上庭園へ続く所）は"
                      "置いていない（範囲外の棟の上）。足元は 5m 標高（約 −1.0 m）まで。± は値の 30%", 1.5,
                      note=DECISION + "：棟 JSON に置く（f0 の前の段々の広場）"))
    dry_s0, dry_s1, dry_w, dry_bot = 9.0, 22.0, 2.5, -2.8
    par = 1.0
    red_s0 = red_s1 - red_n * red_tread
    dry = [box("floor", (dry_s0, dry_s1), (0.0, dry_w), (dry_bot, dry_bot + 0.2)),
           box("wall_terrace", (dry_s0, red_s0), (dry_w, dry_w + 0.3), (dry_bot, terrace_z + par)),
           box("wall_steps", (red_s0, dry_s1), (dry_w, dry_w + 0.3), (dry_bot, F0_MID + par)),
           wedge("wall_steps_top", (red_s0, dry_s1), (dry_w, dry_w + 0.3), (F0_MID + par, terrace_z + par), "-s"),
           box("wall_north", (dry_s1, dry_s1 + 0.3), (0.0, dry_w + 0.3), (dry_bot, F0_MID + par)),
           box("rail_bar", (dry_s0, red_s0), (dry_w + 0.12, dry_w + 0.18), (terrace_z + par + 0.95, terrace_z + par + 1.0), "metal")]
    for k in range(int((red_s0 - dry_s0) // 2.0) + 1):
        s = dry_s0 + 0.1 + 2.0 * k
        dry.append(box(f"rail_post{k}", (s, s + 0.05), (dry_w + 0.12, dry_w + 0.18), (terrace_z + par, terrace_z + par + 1.0), "metal"))
    parts.append(comp("空堀（擁壁・手すり）", "f0", dry, ["PXL_20261105_065027824", F0_ARCH, F0_RECT, "PXL_20261105_065101059"],
                      "(b) 065027824 で空堀は壁沿いにアーチの南からエネルギーセンターの扉の手前まで（s 9〜22 m）、幅は L1 の凹んだ板の高さの約 0.4（2.5 m）、"
                      "外の縁はコンクリートの擁壁で、上に金属の手すり（擁壁の上から約 1.0 m）。擁壁の上はテラスと段の床から約 1.0 m（065035660 の腰壁）。"
                      "底は見えていない（地下の段の解釈 levels B1）ので、5m 標高（約 −1.0 m）より下の −2.8 m と置いた（地形に隠れる）。"
                      "北の端の下へ降りる段は置いていない（見えている幅が 1 m 未満）。± は値の 30% 以上", 1.5,
                      note=DECISION + "：棟 JSON に置く（空堀）。地形（5m 標高）を切り欠かないので空堀の底は地面の下に隠れる"))

    # --- 屋上 -----------------------------------------------------------------------------
    # 検収（11/7）：塔の位置と高さを 2 枚の登録済みの写真（070904733・075102006。どちらも正面化＋compare の登録）の三角測量で直した。
    # 設計は 1 枚（070904733）だけから「上端＝PLATEAU 33.7 m」と置いて奥行きを戻していたが、2 枚目で奥行きが決まり、上端は 29.7 m だった
    m_tower = ("検収係の三角測量（data/reports/library/kenshu_summary.json#tower）：塔の上の 3 つの角を 070904733 と 075102006（どちらも f0 の"
               "正面化＋compare の登録、視点は面から 13.31 m・s 34.5 m と 13.54 m・s 29.1 m）で指し、各写真の H_plane で壁の面の見かけの点に"
               "直してから登録の視点と結んだ。東の上の角 2 つは見かけの s が 2 枚で 10.46/10.44・12.28/12.28 とほぼ同じ"
               "（壁から 1 m 奥なら 2 枚で約 0.4 m 違う。同じ壁の点で 2 枚の登録は 0.2 m 違う）→ 塔の東の面は f0 の壁の面にある（壁から +0.2±0.5 m）、"
               "s 10.8〜12.5 m、上端 29.6〜29.8 m（見かけ 30.04〜30.24 m）。北西の上の角（見かけ 14.91/26.56・14.33/26.70）を上端 29.7 m に置くと"
               "壁から 1.67・1.59 m 奥 → 北の面の奥行き約 1.6 m。080014086（南東のテラスから f0 に沿って北を見る、未登録）でも塔の東の面の下の縁が"
               "f0 のパラペットの線に乗り、壁と同じ面に見える。上の細いアンテナは置いていない。"
               "2 回目（recheck 20261107_150541）：東の面を壁の面に置いたのに三角測量の交点（壁から +0.2 m＝壁より手前）の s・z を使っていたので、"
               "左端 −0.42・−0.37、右端 −0.28・−0.20、上端 +0.41（2 枚）とずれた。東の面は壁の面なので見かけの値がそのまま実際："
               "s 10.45〜12.25、上端 30.1。北西の上の角を上端 30.1 で戻すと壁から 1.88・1.81 m 奥、s 12.14・12.36 → 奥行き 1.85 m。"
               "設計の値（s 6.9〜9.1、壁から 1.7〜3.7 m 奥、上端 33.7＝PLATEAU measuredHeight と仮定）は 2 枚目と合わない"
               "（その位置なら 2 枚の見かけの s が約 1 m 違う）ので外した。PLATEAU 33.7 m とパラペット 25.3 m の差は塔では説明が付かず、data の依頼のまま。"
               "役割：f0 の壁と同じ面から立ち上がるタイル貼りの細い塔（煙突か排気の塔と見る。下の L1 にエネルギーセンターの扉）。"
               "± は上端 0.4 m（2 枚の登録の差 0.2 m＋読み）、平面 0.5 m")
    parts.append(comp(
        "屋上の塔（タイル貼りの細長い箱）", "f0", [box("tower", (10.45, 12.25), (-1.85, -0.02), (PARAPET, 30.1))],
        [F0_RECT, F0_FRONT2, F0_SE, "PXL_20261105_065101059"], m_tower, 0.4,
        note="上の細いアンテナ（細い）は置いていない。検収係が位置と上端を直した（設計：s 6.9〜9.1・壁から 1.7〜3.7 m 奥・上端 33.7）"))
    # 「塔の北の低い盛り上がり」は検収 C の 1（役割が書けない）で外した。out/library/soten.json に争点（decided）、屋上の撮影依頼のまま
    hump_m = ("(b) 075639031 の正面化でパラペットの上に 2 つの箱が見える（入口の凹みの中心から左右対称。見かけの中心 s 14.45・25.16 m、"
              "幅 3.5・3.6 m、上端の見かけ 26.1 m）。奥にあるので視差で登録の視点（registry：面から 12.5 m、s 18.4 m）の側へ寄って見える。"
              "奥行き（壁から）は見えていないので 2.0 m と置いて戻した：中心 13.8・26.3 m、幅 3.8 m。箱の奥行きは 3.0 m、上端はパラペットから 2.5 m"
              "（見えた 0.8 m は下限）と置いた。航空写真の長い盛り上がり A・B の西の端の可能性（kansatsu_roof.json）。± は値の 30%")
    parts.append(comp("屋上の盛り上がり（入口の上、北）", "f2", [box("hump", (11.9, 15.7), (-5.0, -2.0), (PARAPET, PARAPET + 2.5))],
                      [F2_RECT, "PXL_20261103_075629456", "PXL_20261103_075623165"], hump_m, 1.1))
    parts.append(comp("屋上の盛り上がり（入口の上、南）", "f2", [box("hump", (24.4, 28.2), (-5.0, -2.0), (PARAPET, PARAPET + 2.5))],
                      [F2_RECT, "PXL_20261103_075629456", "PXL_20261103_075623165"], hump_m, 1.1))

    # --- f1 の部品 ---------------------------------------------------------------------------
    # 北西の角の軒下（列 9 の L2〜L3）。角の柱型 1 本が独立して立ち、奥に風除室。f2 の北の凹みへ回る
    eav_d = 4.5
    pier_nw = (49.42 - 1.0, 49.42)
    m_eav = ("列 9 の凹みの縁は柱割りから（s 44.08〜48.42、faces.f1.bays）、上端は coverage_f1 で 14.9 m（L3 の凹みの上端と同じ線）、"
             "床は甲板の高さ 6.6 m。角の柱型は甲板の高さから L3 まで独立して立ち（063455681・063529987）、幅 1.0 m（北西の角の柱型 0.965 m）。"
             "(b) 奥行きは見えていない：063517313 で天井の格子の板が奥へ 6 枚ほど・柱間隔の約 0.85＝4.5 m と見た（± 30%）。"
             "天井は暗い格子の板（063517313）")
    parts.append(comp("北西の角の軒下（列 9 の L2〜L3 と f2 の北端）", "f1", [
        box("void_front", (44.08, 48.42), (-eav_d, 0.5), (DECK_Z, 14.9), cut=True),
        box("void_behind_pier", (48.42, 49.92), (-eav_d, -1.0), (DECK_Z, 14.9), cut=True),
        box("ceiling", (44.1, 49.4), (-eav_d, -1.0), (14.82, 14.9), "dark"),
        box("ceiling_front", (44.1, 48.42), (-1.0, 0.0), (14.82, 14.9), "dark"),
    ], [F1_UNDER, F1_NW, F1_WEST, "PXL_20261103_075437320"], m_eav, 1.4))
    parts.append(comp("列 8 の西端の曲面ガラスと軒下の東のガラス", "f1", [
        arc("curve", (44.08, -2.3), 1.0, (0.0, 90.0), (DECK_Z, 14.8)),
        box("east_glass", (45.06, 45.1), (-eav_d + 0.05, -2.3), (DECK_Z, 14.8), "glass"),
    ], [F1_UNDER, F1_WEST, "PXL_20261105_063509567"],
        "(b) 063517313 で列 8 の凹みのガラスが西端で曲がり、列 9 の軒下の奥へ回る。曲がりの大きさは見えていないので半径を柱型の幅（1.0 m）と置き、"
        "軒下の東の縁のガラスは柱型の裏から 1.0 m 西と置いた。高さは凹みと同じ 2 段分。± は値の 50%（見えていない寸法）", 0.5))
    parts.append(comp("f2 の北の凹みの北端の曲面ガラス", "f2", [
        arc("curve", (5.4, -2.5), 1.0, (90.0, 180.0), (DECK_Z, 14.5)),
        box("north_glass", (4.38, 4.42), (-eav_d + 0.05, -2.5), (DECK_Z, 14.5), "glass"),
    ], F2_NREC + [F2_NORTH],
        "(b) 観察票 kansatsu_f2.json：北の凹みのガラスの壁は北の端で曲面になり北西の角の軒下の奥へ回る（075705849・063540463）。"
        "曲がりは見えていないので f1 の列 8 と同じ半径 1.0 m と置いた。± は値の 50%", 0.5))
    vb = [world("f1", s, d, faces) for s, d in [(45.6, -eav_d + 0.1), (48.0, -eav_d + 0.1), (48.0, -2.6), (45.6, -2.6)]]
    m_vest = ("(b) 063529987・063455681 で軒下の奥に扉付きのガラスの箱。平面は見えていないので、軒下の奥の壁に付けて幅は軒下の幅の約 0.55"
              "（2.4 m）、出は約 1.8 m、高さは段の間隔の約 0.65（2.9 m）と置いた。床は甲板の高さ。± は値の 30%")
    parts.append({"type": "vestibule", "name": "北西の角の軒下の風除室", "polygon": vb, "wall_edges": [0],
                  "z0": est(DECK_Z, tz(DECK_Z, R_TILE), [F1_NW, F2_RECT], "甲板の床（f2 の入口の床と同じ）", ref="design_library.DECK_Z"),
                  "height": est(2.9, tb(2.9), [F1_NW, F1_WEST], m_vest),
                  "mullion_pitch": est(1.2, tb(1.2), [F1_NW], "(b) 063529987 で縦の桟は扉 1 枚分ずつ（約 1.2 m）"),
                  "transom_pitch": est(2.2, tb(2.2), [F1_NW], "(b) 063529987 で扉の上に横の桟 1 本"),
                  "roof": {"thickness": est(0.15, None, [F1_NW], "見た目の値"), "overhang": est(0.05, None, [F1_NW], "見た目の値"), "material": "dark"}})
    # 外階段（直進の 2 本と中間の踊り場、上の踊り場は通路橋へ続く）
    m_st = ("coverage_f1_photo.png で階段の外の腰壁の上の縁が s 23.8 m・z 2.4 m から s 30.3 m・z 6.95 m へ上がり、そこから中間の踊り場の縁が"
            " z 7.05 m で水平に続く（075510510 で下の長い段→踊り場→上の短い段→上の踊り場）。手前に出た物は視差で広がって高く写るので、"
            "075500328 の視点（registry：面から 11.8 m、s 19.2 m、高さ 2.8 m）で壁から 2 m 手前に戻した：下の段 s 23.2〜28.4 m・"
            "z 1.5〜5.25 m（勾配 0.72）、中間の踊り場 s 28.4〜31.1 m、上の段 s 31.1〜32.8 m（同じ勾配で甲板の 6.6 m へ）、上の踊り場 s 32.8〜38.6 m"
            "（列 7 の前。観察票：踊り場は列 7〜8 の前）。(b) 階段の幅 1.8 m・上の踊り場の出 2.5 m・白い箱形の下面・腰壁 1.1 m は見えていない"
            "寸法で、075510510 の段の幅と柱間隔の比（約 0.33）から置いた（± 30%）。下の段の下は空いている（ガラスの扉と照明）")
    parts.append(comp("外階段（直進 2 本と踊り場）", "f1", [
        wedge("flight_low", (23.2, 28.4), (0.0, 1.8), (1.1, 5.25), "+s", t=0.4),
        wedge("parapet_low", (23.2, 28.4), (1.6, 1.8), (1.1, 6.24), "+s", t=1.36, mat="tile"),
        box("landing_mid", (28.4, 31.1), (0.0, 1.8), (4.85, 5.25)),
        box("parapet_mid", (28.4, 31.1), (1.6, 1.8), (4.85, 6.35), "tile"),
        wedge("flight_up", (31.1, 32.8), (0.0, 1.8), (4.85, 6.6), "+s", t=0.4),
        wedge("parapet_up", (31.1, 32.8), (1.6, 1.8), (4.85, 7.6), "+s", t=1.4, mat="tile"),
        box("landing_top", (32.8, 38.6), (0.0, 2.5), (6.2, 6.6)),
        box("landing_box", (33.3, 38.1), (0.3, 2.5), (4.8, 6.2), "white"),
        box("base_wall", (27.5, 33.0), (1.8, 2.0), (1.5, 2.8), "tile"),
    ], F1_STAIR + ["PXL_20261103_075500328", "PXL_20261103_075437320", "PXL_20261105_063510309"], m_st, 0.6))
    parts.append({"type": "rail", "name": "外階段の上の踊り場の白い手すり", "face": "f1",
                  "along0": est(32.8, 0.6, F1_STAIR, "上の踊り場の東の端（外階段と同じ）"),
                  "along1": est(38.6, 0.6, F1_STAIR, "上の踊り場の西の端（ここから通路橋：敷地の物）"),
                  "z": est(DECK_Z, tz(DECK_Z, R_TILE), F1_STAIR, "甲板の床と同じ", ref="design_library.DECK_Z"),
                  "height": est(1.1, tb(1.1), F1_STAIR, "(b) 075517975 で手すりの高さは段の間隔の約 0.25"),
                  "post_pitch": est(0.15, None, F1_STAIR, "白い縦格子（見た目の値）"),
                  "offset": est(2.45, 0.75, F1_STAIR, "上の踊り場の出 2.5 m の縁", ref="parts.外階段.landing_top")})
    m_p1 = "縦樋 4 本は列 1|2・3|4・6|7・8|9 の柱型の上で全高（data/reports/library/kazoe_f1.json#bays の landmarks）。位置は柱割りから"
    for k, i in enumerate((1, 3, 6, 8)):
        parts.append(pipes(f"縦樋（列 {i}|{i + 1}）", "f1", round(piers[i] - 0.15, 2), 0.5, PARAPET, 0.06, F1_COV + [F1_DECK], m_p1, 0.2))
        parts[-1]["along"]["ref"] = "faces.f1.bays"
    parts.append({"type": "asset", "name": "壁付けの室外機（列 9 の L1）", "asset": "ac_outdoor", "face": "f1",
                  "along": est(44.9, 1.0, ["PXL_20261103_075517975", "PXL_20261103_075510510"],
                               "(b) 075517975 で通路橋の下、茶色の扉の西の列 9 の L1 の上部に 2 台並ぶ。位置は柱割りの列 9 の東寄り（± 1 m）"),
                  "offset": est(0.2, None, ["PXL_20261103_075517975"], "壁付け（見た目の値）"),
                  "z0": est(4.4, 0.8, ["PXL_20261103_075517975"], "(b) 室外機の下端は L1 の上の枠（6.0 m）から段の間隔の約 0.35 下"),
                  "yaw": 0, "count": meas(2, None, ["PXL_20261103_075517975", "PXL_20261103_075510510"], "2 枚で 2 台", unit="台"),
                  "pitch": est(0.95, 0.2, ["PXL_20261103_075517975"], "(b) 2 台が接して並ぶ（1 台の幅＋隙間）"),
                  "size": {"w": est(0.8, 0.2, ["PXL_20261103_075517975"], "(b) 室外機の幅は柱型の幅の約 0.8"),
                           "d": unk(note="奥行きは見えていない（真横の写真が無い）"),
                           "h": est(0.65, 0.2, ["PXL_20261103_075517975"], "(b) 高さは幅の約 0.8")}})
    m_door = "(b) 075510510・075517975 の斜めで、扉の位置は柱割りの列、高さは段の間隔に対する比（扉の高さ約 0.5）。足元は歩道（坂）"
    for nm, a0, a1, z0, z1 in [("外階段の下のガラスの扉", 27.0, 28.4, 1.5, 3.9), ("茶色の両開きの扉（列 8）", 39.6, 41.2, 2.0, 4.3),
                               ("上が丸い白い縁の扉（列 9）", 46.7, 47.7, 2.2, 4.7)]:
        parts.append(patch(nm, "f1", a0, a1, z0, z1, "dark", F1_STAIR, m_door, 0.6, 0.6))
    m_sq = f"{M_F1}：列 2・3 の L1 の細い板の中の濃い色の四角（観察票 kansatsu_f1.json：各列 2 列×2 段）"
    m_sq_low = "(b) 下の段の四角は coverage_f1 の下の縁（z 1.55 m）で切れる。上の縁 2.35 m は読め、高さは上の段と同じ 1.4 m と置いた（見えていない）"
    for a0, a1 in [(6.25, 7.1), (9.6, 10.45), (11.6, 12.5), (15.0, 15.9)]:
        parts.append(patch(f"L1 の濃い色の四角（上、s {a0}）", "f1", a0, a1, 3.45, 4.85, "dark", F1_COV, m_sq, 0.2, 0.25))
        parts.append(patch(f"L1 の濃い色の四角（下、s {a0}）", "f1", a0, a1, 0.95, 2.35, "dark", F1_COV, m_sq_low, 0.2, 0.42))
    parts.append({"type": "planter", "name": "歩道沿いの白い縁の低い植え込み（列 1〜5）", "face": "f1",
                  "along": est(0.6, 0.5, ["PXL_20261103_075437320", "PXL_20261103_075448020", "PXL_20261103_075500328"],
                               "(b) 075437320・075448020 で北東の角から外階段の下端（s 23.2 m）の手前まで、途切れながら続く。区切りは数えにくいので 1 本にした"),
                  "width": est(22.3, 2.0, ["PXL_20261103_075437320"], "(b) 同上（北東の角から外階段の下端まで）"),
                  "depth": est(0.8, tb(0.8), ["PXL_20261103_075500328"], "(b) 植え込みの奥行きは柱型の幅の約 0.8"),
                  "offset": est(0.0, None, ["PXL_20261103_075500328"], "壁に接する（見た目の値）"),
                  "height": est(0.4, tb(0.4), ["PXL_20261103_075500328"], "(b) 白い縁の高さは L1 の板の高さの約 0.08"),
                  "wall": est(0.12, None, ["PXL_20261103_075500328"], "縁の厚さ（見た目の値）"), "material": "white"})

    # --- f2 の部品 ---------------------------------------------------------------------------
    m_can = ("(b) 075639031 の正面化で庇は見かけ s 16.0〜27.7 m（観察票：W の約 1.3、凹みより北へ少し・南へ大きく）、丸柱 2 本は見かけの中心"
             " s 17.4・24.4 m、太さ 0.8 m（段の間隔の約 0.18）。庇は手前に出ているので正面化では大きく高く写る。064354855 で丸柱の高さは太さの"
             "約 5 倍（3.9 m）、庇の出は庇の幅の約半分（5.5 m）、柱は出の約 0.7 の所。庇の鼻先は丸い（角を落とした箱で近似）。"
             "幅は正面化の見かけの幅のまま、床は入口の床 6.6 m。± は値の 30%")
    # 検収（11/7）：庇の幅と丸柱の s は正面化の見かけのままだった（m_can の「幅は正面化の見かけの幅のまま」）。手前に出た物は登録の視点
    # （075639031：面から 12.51 m、s 18.44 m）の側から広がって写るので、設計の出（柱 3.85 m・鼻先 5.85 m）のまま実際の s に戻した。
    # 見かけ → 実際＝18.44 + (見かけ − 18.44) × (12.51 − 出) / 12.51。丸柱の見かけ（H_plane で戻した柱の縁）17.24〜17.93・23.84〜25.29、鼻先の両端 16.06・27.83
    m_can2 = ("検収係（data/reports/library/kenshu_summary.json#canopy）：正面化 075639031 の見かけを登録の視点（registry：面から 12.51 m・s 18.44 m）で"
              "設計の出のまま戻した。丸柱の中心 見かけ 17.59・24.57 → 実際 17.85・22.68（出 3.85 m）、太さ 見かけ 0.69 m → 0.5 m、"
              "庇の鼻先 見かけ 16.06〜27.83 → 17.17〜23.44（出 5.85 m）。2 本の柱は庇の両端からそれぞれ 0.7 m 内側でそろい、庇の中心 20.3 m は"
              "入口の凹みの中心 20.1 m と合う。設計の値（柱 17.4・24.4、庇 15.0〜26.8）は recheck で南の柱が探す幅の外（見つからない）、"
              "photoproj の悪い順 3 位（北の柱の所）。出（3.85・5.85 m）は見えていない寸法のまま（064354855 の比、± 30%）。"
              "北の柱は 075639031＋062929449・075629456 の 2 組の交点で壁から 5.5〜5.7 m（2 組で 0.2 m 以内）とも出たが、どちらの相手も鎖の登録"
              "（面から外れた点の誤差が分からない）なので出は変えず、真横の撮影依頼のまま。"
              "2 回目（recheck 20261107_150541）：高さも見かけのままだった。075639031 で柱の上端（天井との境）は見かけ z 11.01・11.06 →"
              "出 3.85 m で 9.62・9.65（2 本でそろう）＝天井 9.63。柱の下端は見かけ 6.5＝視点の高さ（入口の床 6.6 と合う）。天井の前の縁"
              "（見かけ 11.93）が天井 9.63 になる出は 5.3 m、そこで鼻先の上の縁（見かけ 13.68）は 10.64、両端（見かけ 16.06・27.83）は 17.07・23.85。"
              "設計の天井 10.5・鼻先 10.3〜11.35 は、鼻先の下の縁の線が写真の鼻先の上の縁に乗って recheck を通っていた（取り違え。柱の上端は "
              "11 枚中 7 枚で −0.17〜−0.38、正しい境は探す幅の外）。柱の中心は 2 回目の recheck の縁から 17.8・22.7")
    parts.append(comp("正面入口の黒い庇と丸柱 2 本", "f2", [
        box("slab", (17.07, 23.85), (0.0, 4.9), (9.63, 10.45), "dark"),
        box("nose", (17.07, 23.85), (4.5, 5.3), (9.63, 10.45), "dark"),
        wedge("nose_top", (17.07, 23.85), (4.5, 5.3), (10.45, 10.64), "-d", mat="dark"),
        cyl("column_n", (17.8, 3.85), 0.25, (DECK_Z, 9.63), "dark"),
        cyl("column_s", (22.7, 3.85), 0.25, (DECK_Z, 9.63), "dark"),
    ], [F2_RECT, F2_CANOPY, "PXL_20261105_063611859.MP", "PXL_20261105_063612628", "PXL_20261103_075629456"], m_can + "。" + m_can2, 1.0))
    # 大階段（K-005：棟 JSON に置く）。上の段が正面入口の床（庇の下）を兼ねる
    g_n, g_tread, g_top_d = 15, 0.35, 5.0
    g_z0 = 2.85
    g_rise = round((DECK_Z - g_z0) / g_n, 3)
    g_top = (15.0, 31.0)
    g_A = g_top[0] - (g_n - 1) * g_tread
    g_W = g_top[1] - g_top[0] + 2 * (g_n - 1) * g_tread
    g_D = g_top_d + (g_n - 1) * g_tread
    m_g = ("(b) 075629456 で西の広場から正面入口の床へ 1 本で上がる段、見えた蹴上げ 15（観察票 14〜15）、下ほど左右に広がる（両端が斜め）。"
           "上の段（入口の床）は 075639031 の正面化で s 15.0〜31.0 m（北は植え込みの台の端、南は路地の段の腰壁）・高さ 6.6 m（design_library.DECK_Z）、"
           "奥行きは丸柱（面から 3.85 m）の約 1 m 先まで 5.0 m。踏み面は 075629456 の下の段で蹴上げの約 1.4 倍に見えるので 0.35 m。"
           "下の段の高さは 5m 標高の広場（面から 10〜12 m で 2.6〜3.2 m、中央 2.85 m）。1 段の高さ＝(6.6 − 2.85) ÷ 15 ≒ 0.25 m。"
           "写真の段はこれより浅く見え、上端（f2 の高さはタイルの縮尺とパラペット 25.3 m の仮定から）か下端の 5m 標高のどちらかが違う：未解決"
           "（requests の data）。両端は 1 段ごとに踏み面の分だけ広がる（sides from・to）。± は値の 30%")
    parts.append({"type": "steps", "name": "大階段（西の広場から正面入口の床へ。上の段が入口の床）", "face": "f2",
                  "along": est(round(g_A, 2), tb(5.0), GRAND_PHOTOS, m_g), "width": est(round(g_W, 2), tb(g_W), GRAND_PHOTOS, m_g),
                  "depth": est(round(g_D, 2), tb(g_D), GRAND_PHOTOS, m_g),
                  "count": est(g_n, None, ["PXL_20261103_075629456"], "075629456 で見えた蹴上げの数（観察票 kansatsu_f2.json は 14〜15）", unit="段"),
                  "rise": est(g_rise, tb(g_rise), GRAND_PHOTOS + ["gsi:dem5a"], m_g),
                  "tread": est(g_tread, tb(g_tread), ["PXL_20261103_075629456"], m_g),
                  "z0": est(g_z0, 1.0, ["gsi:dem5a"],
                            "国土地理院 5m 標高を大階段の下端（f2 から 10〜12 m、s 15〜30 m）で読み ground_z 41.67 m を引いた（2.6〜3.2 m の中央）"),
                  "sides": ["from", "to"], "edge": "straight", "material": "brick",
                  "note": DECISION + "：棟 JSON に置く。上の段は正面入口の床（庇の下）を兼ねる。北の植え込みの台・街灯・木は敷地の依頼に残した"})
    parts.append({"type": "nameplate", "name": "銘板（名城大学附属図書館）", "face": "f2",
                  "along": est(12.1, 1.0, [F2_PLATE, "PXL_20261103_075710233", "PXL_20261105_063548675"],
                               "(b) 銘板の柱型（s 11.0〜15.5 m）の中ほど。075718159 の斜めで銘板の幅は柱型の半分くらい"),
                  "offset": est(0.0, None, [F2_PLATE], "壁に貼る"),
                  "width": est(2.3, tb(2.3), [F2_PLATE], "(b) 柱型の幅 4.5 m の約半分"),
                  "height": est(0.6, tb(0.6), [F2_PLATE], "(b) 文字 2 行の高さは幅の約 0.25"),
                  "thickness": est(0.03, None, [F2_PLATE], "見た目の値"),
                  "z0": est(8.1, 0.8, [F2_PLATE, "PXL_20261103_075710233"], "(b) 甲板の床から段の間隔の約 0.33 上"), "material": "metal"})
    parts.append({"type": "nameplate", "name": "掲示板（枠付きの板。中身は描かない）", "face": "f2",
                  "along": sf2(25.04, "掲示板の北の縁 x 3283 px"), "offset": est(0.0, None, [F2_RECT], "壁に付く"),
                  "width": est(2.45, 0.2, [F2_RECT], f"{M_F2}：掲示板の幅 s 25.04〜27.5"),
                  "height": est(1.3, 0.2, [F2_RECT], f"{M_F2}：掲示板の高さ z 7.5〜8.8"),
                  "thickness": est(0.08, None, [F2_RECT], "見た目の値"), "z0": zf2(7.5, "掲示板の下端 py 3262"), "material": "dark"})
    parts.append({"type": "planter", "name": "入口の南の白い植木鉢の箱", "face": "f2", "along": sf2(27.57, "箱の北の縁 x 3661 px"),
                  "width": est(2.98, 0.3, [F2_RECT], f"{M_F2}：箱の幅 s 27.57〜30.55"),
                  "depth": est(0.6, tb(0.6), [F2_RECT], "(b) 奥行きは高さとほぼ同じに見える"),
                  "offset": est(0.1, None, [F2_RECT], "壁の前（見た目の値）"), "height": est(0.7, 0.2, [F2_RECT], f"{M_F2}：箱の上端 7.3 − 床 6.6"),
                  "z0": est(DECK_Z, tz(DECK_Z, R_TILE), [F2_RECT], "入口の床", ref="design_library.DECK_Z"),
                  "wall": est(0.08, None, [F2_RECT], "見た目の値"), "material": "white"})

    # --- f3 の部品 ---------------------------------------------------------------------------
    piers3 = [F3_M0 + i * F3_PITCH for i in range(10)]
    m_p3 = "縦樋 4 本は西から列 1|2・3|4・6|7・8|9 の柱型の上（data/reports/library/kazoe_f3.json#bays の landmarks。f3 の写真だけで数えた）。位置は柱割りから"
    for i in (1, 3, 6, 8):
        parts.append(pipes(f"縦樋（列 {i}|{i + 1}）", "f3", round(piers3[i] - 0.15, 2), 0.0, PARAPET, 0.06, [F3_RECT] + F3_SW[:1], m_p3, 0.25))
        parts[-1]["along"]["ref"] = "faces.f3.bays"
    parts.append({"type": "planter", "name": "白い縦格子の縁の植え込み（南西寄り）", "face": "f3",
                  "along": est(0.5, 0.5, F3_SW, "(b) 064510192・075830062 で南西の角から壁沿いに続く"),
                  "width": est(10.8, 3.2, F3_SW, "(b) 見えていない：奥が竹に隠れる。見えた所は西の 2 列分（柱間隔の 2 倍）"),
                  "depth": est(1.0, tb(1.0), F3_SW, "(b) 奥行きは柱型の幅くらい"),
                  "offset": est(0.0, None, F3_SW, "壁に接する（見た目の値）"),
                  "height": est(0.9, tb(0.9), F3_SW, "(b) 白い縦格子の高さは L1 の板の高さの約 0.2（腰の高さ）"),
                  "wall": est(0.05, None, F3_SW, "格子の厚さ（見た目の値）"), "material": "white"})
    b["parts"] = parts

    # --- requests -----------------------------------------------------------------------
    rq = []

    def req(kind, face, what, where, why, to=None, status="open", note=None):
        d = {"kind": kind, "face": face, "what": what, "where": where, "why": why, "status": status, "by": "sekkei"}
        if to:
            d["to"] = to
        if note:
            d["note"] = note
        rq.append(d)

    # 前の係の依頼（測量係が棟 JSON に書いた物を残す）
    req("photo", "f2", "f2：メジャーを壁に沿えて 1 枚（タイル 10 段か、入口の南の彫り込みの板の高さ。メジャーの目盛りと壁が正面から 1 枚に入るように）",
        "正面入口の南の壁（甲板・入口の床の高さから手が届く所）を正面から",
        "図書館にはメジャーの写真が無い。f2 は両端が 1 枚に入らず PLATEAU の辺が使えないので、縮尺がタイル 1 本だけ（2 本目の物差しが無い）。"
        "data/reports/library/sokuryo_f2.json", to="staff")
    rq[-1]["by"] = "sokuryo"
    req("tool", "f0", "registry の登録に exclude（面の m の矩形）を足す道具（例 recorder photos exclude <写真> --face <面> --rect s0,z0,s1,z1 --note …）",
        "recorder/registry.py・recorder/__main__.py",
        "f0 の面の写真で 075051437 の手前の木（面の 0〜25 m）が壁の上に貼られる。共有の台帳は道具だけが書くので係が書けない。"
        "data/reports/library/sokuryo_f0.json", to="tool")
    rq[-1]["by"] = "sokuryo"
    # 撮影の依頼（観察・数えの依頼をまとめた）
    req("photo", "f3", "f3 の正面寄りの 1 枚（両端が入れば一番よい。入らなければ列 4〜6 と縦樋 2 本＝列 3|4・6|7 が入る 1 枚を数歩ずつ横に 3〜4 枚）",
        "11 号館の屋上庭園（f0 の南の築山）か 11 号館の上の階の窓から",
        "f3 は角からの急な斜めだけで、中ほどの列の L2 を同じ面の見えた列から仮説で写した（windows「L2 大窓（中ほどの列。仮説）」）。"
        "列数 9 の確かめ、柱型と窓の比、凹みの奥行きにも使う（kansatsu_f3.json・kazoe_f3.json の依頼と同じ）", to="staff")
    req("photo", "f0", "空堀を上から見下ろして、底・底に面した開口・北東の広場の地面が 1 枚に入る写真と、南の張り出し箱の下の柱型・L1 の南端を正面寄りに 1 枚",
        "中段の踊り場の手すり越し（エネルギーセンターの扉の脇）と、南東の高いテラスの北の端から",
        "地下の段の解釈（levels B1、kazoe_f0.json の interpreted）と、南の箱の下の柱型の位置・幅（今は比から置いた）を決める", to="staff")
    req("photo", "f0", "張り出し箱 2 つとアーチの凹みを真横に近い角度から（出・奥行き・曲面ガラスのふくらみ）",
        "北東の角の広場から壁沿いに南を見る位置と、南東のテラスから北を見る位置",
        "箱の出・凹みの奥行き・曲面ガラスのふくらみは比と視差から置いた（± 30%）", to="staff")
    req("photo", "f1", "北西の角の軒下と列 7・8 の凹みを真横から 1 枚と、軒下の天井を見上げる 1 枚。外階段を真横（道の向かいの歩道）から段の両端と踊り場が入る 1 枚",
        "甲板の北西の角（f2 の側から f1 に沿って東を見る）と、f1 の前の道の北側の歩道",
        "軒下の奥行き 4.5 m・凹みのガラスの奥 1.3 m・階段の幅 1.8 m・上の踊り場の出 2.5 m は見えていない寸法で比から置いた", to="staff")
    req("photo", "f2", "f2 の南寄り（入口の南から南西の角まで）を正面寄りに 1 枚と、正面入口の庇・凹みを真横（北か南）から 1 枚",
        "本部棟の上の階の窓か路地の段の途中、大階段の上の北の植え込みの脇",
        "南西の角の窓 3 つと横長の枠の南端は斜めの写真の比から置いた。庇の出・柱の位置・入口の凹みの奥行きも見えていない", to="staff")
    req("photo", "*", "図書館の屋上全体を見下ろす写真 2〜3 枚（塔・中央の囲い・長い盛り上がり 2・小さい囲いが入るように）",
        "タワー75 の上の階（図書館の北）の窓か、共通講義棟東の上の階（東）",
        "屋上の物は地上からはパラペットの上に出る所しか見えず、航空写真では盛り上がりか凹みか決まらない（kansatsu_roof.json）。"
        "航空写真の囲い・長い盛り上がりは置いていない。塔と入口の上の 2 つの箱は比と視差から置いた", to="staff")
    # 敷地に置く物（棟と棟の間・2 棟に触れる物）。敷地 JSON に置く語彙がまだ無いので進行役へ
    req("tool", "f1", "敷地に置く物：通路橋（図書館 f1 の西寄り・列 8〜9 の前から道をまたいでタワー75 の側へ）。両側にタイルの腰壁＋笠木＋金属の手すり、床は煉瓦色、下面はコンクリートの梁",
        "data/site/tempaku.json（敷地の部品の欄）",
        "2 棟（library・tower75）に触れるので棟 JSON には置かない。寸法の根拠：OSM way/1304081004（f1 の s 41.4〜47.4 m から北へ 15 m 超）、"
        "写真 063412307・063413358・063417721・063418811・063420663・063439559・075517975。床は甲板・外階段の上の踊り場と同じ 6.6 m（design_library.DECK_Z）", to="tool")
    req("tool", "f2", "敷地に置く物：甲板（デッキ。天窓 2 と西の縁の腰壁・手すり）と青い鉄骨の庇（通路の屋根、柱 6 本以上）、甲板へ上がる斜路と曲がった腰壁の段",
        "data/site/tempaku.json（敷地の部品の欄）",
        "甲板は OSM で通路橋と 1 つの多角形（way/1304081004：f2 の北西の角から s 22 m まで・出 5〜8.4 m、北西の角の外へ広がる）で、通路橋を経てタワー75 に続く。"
        "写真 063449740・063455681・063540463・075705849・063106256。床は 6.6 m（f2 の入口の床）。棟 JSON には甲板の高さの風除室と、大階段の上の段（入口の床）を置いた"
        "（" + DECISION + "）", to="tool")
    req("tool", "f2", "敷地に置く物：路地の段（南西の角から北へ入口の高さまで、本部棟の壁との間）と f2 側の白い縁の段々の植え込み",
        "data/site/tempaku.json（敷地の部品の欄）",
        "本部棟との間の地面に立つ物（" + DECISION + " で敷地のまま）。写真 064524057・064508808・064525427・075837016。上端は入口の床 6.6 m（大階段の上の段）", to="tool")
    req("tool", "f0", "敷地に置く物：南の築山の高い木 3 以上・広葉樹 1・街灯 2、北東の角の白い縦格子の植え込み・案内板・カーブミラー、大階段の北の 4 灯の街灯と木・生け垣の植え込みの台",
        "data/site/tempaku.json（敷地の部品の欄）。木・街灯は CC0 アセット tree_small・street_lamp",
        "棟と棟の間の地面に立つ物（075036075・075051437・080029484・075153504・065125602・075623165・064353508）", to="tool")
    # 争点（規則で決まらない物）
    req("check", "f2", "正面入口の床（庇の下）を棟 JSON に置き、甲板・大階段は敷地に上げた。この境目でよいか",
        "parts「大階段」と requests の甲板",
        "玄関は棟の物（係の定義）だが、入口の床は甲板・大階段の上端と一続きで、OSM は甲板と通路橋を 1 つの多角形にしている。"
        "写真でどの壁に付くかだけでは決まらない（観察票 kansatsu_f2.json の questions）", status="closed",
        note=DECISION + " で決定：大階段（西の広場から正面入口の床へ）と f0 の前の段々の広場・空堀は棟 JSON に置く（steps＋composite）。"
             "通路橋・甲板・青い鉄骨の庇・斜路・本部棟との間の路地の段・木・街灯・案内板は敷地の依頼（kind tool）のまま。"
             "入口の床は大階段の上の段として置いた")
    req("check", "*", "階高が面で 2〜5% 違う（f0 4.386・f1 4.472・f2 4.566・f3 4.607 m）。棟で 1 つにするか、面ごとの換算値のままか",
        "floor_pitch と faces[].windows の高さ",
        "どの面もタイル 65 段前後で同じ階高に見えるが、縮尺の物差し（PLATEAU の辺かタイルか）で換算値が違う。窓の高さは面ごとの製図板から読んだので、"
        "角で 0.1〜0.2 m の段差が出る（sokuryo_f1.json の questions）", status="closed",
        note=DECISION + " で決定：階高は面ごとの値のまま揃えない。窓の高さは各面の製図板の値のまま、floor_pitch は f1 の値を参考の 1 値として残す")
    req("data", "f2", "大階段の上端（入口の床 6.6 m）と下端の広場（5m 標高で約 2.85 m）の差 3.75 m が、見えた蹴上げ 15 段では 1 段 0.25 m になり、写真の浅い段と合わない",
        "parts「大階段」と f2 の高さの原点（パラペット 25.3 m の仮定・タイルの縮尺）",
        "f2 の登録の視点の高さも高め（sokuryo_f2.json の camera_check）。メジャーの写真（f2 の依頼）か大階段の真横の写真で上端の高さか 1 段の高さを測れば決まる。"
        "未解決のまま（地盤の取り方は変えない）")
    req("data", "*", "PLATEAU measuredHeight 33.7 m とパラペット上端 25.3 m の差 8.4 m",
        "parapet_top と屋上の塔",
        "設計では塔の上端に PLATEAU の高さを当てたが、検収（data/reports/library/kenshu_summary.json#tower）で塔の上端は 2 枚の登録済みの写真の"
        "三角測量から 30.1 m と出た（パラペットから 4.8 m）。地上の写真でパラペットより上に出る物では 8.4 m の差の説明が付かない"
        "（PLATEAU の高さが空堀の底など低い地面から測った値か、屋上の別の物を含む値かは分からない）。未解決のまま（合わせに行かない）")
    rq[-1]["note"] = "検収係が why を直した（塔の上端は PLATEAU ではなく三角測量の 30.1 m）"
    # 検収係の依頼（data/reports/library/kenshu_summary.json）
    req("check", "f1", "甲板から撮った写真の組の登録を確かめてほしい：063449740（正面化＋compare の登録）と、それを経由した鎖の 9 枚"
        "（063405325・063406464・063412307・063413358・063417721・063418811・063420663・063455681・063529987）の視点の高さが 9.9〜10.4 m",
        "data/photos/registry.json の 063449740 と鎖の 9 枚（目印係の photos landmarks／pnp で 063449740 を登録し直す）",
        "甲板の床は 6.6 m（design_library.DECK_Z）＋目の高さ 1.5 m＝約 8.1 m で、2 m 高い。この組だけ f1 の窓の線（L2 上端 −0.20〜−0.40、"
        "L3 下端 −0.35、L4 下端 −0.16〜−0.21）が地上の写真（正面化 075437320・075500328、目印 075510510・075517975）と逆にずれる。"
        "値は地上の写真に合わせて直していない（recheck 20261107_151135）。目印係の作業", to="tool")
    rq[-1]["by"] = "kenshu"
    req("data", "f3", "f3 の前の通路の高さ：目印で登録した 064525427・064508808 と鎖の 064539230・064510192・075830062・075837016 の視点の高さが"
        "棟の z=0 から 5.4〜6.2 m（5m 標高からは 4.5〜5.1 m）。目の高さ 1.5 m を引くと通路は z 4.0〜4.7 m で、5m 標高（約 0.6〜1.7 m）より 3 m ほど高い",
        "faces.f3.ground_offset（今は無い）と、白い縦格子の縁の植え込み（z0 は地形から）",
        "f3 の前は 11 号館の高い所へ続く通路で、5m 標高の 5 m 格子では段が均される。ground_offset を置くと面の前 12 m の帯が上がり 11 号館の壁に"
        "かかるので、検収係は値を置かなかった。メジャーか通路の段の数（f2 の南西の角の路地の段）で通路の高さを決めてほしい（測量係の作業）", to="tool")
    rq[-1]["by"] = "kenshu"
    b["requests"] = rq

    # --- history ------------------------------------------------------------------------
    b["history"] = [
        {"at": "", "by": "recorder new --from-site", "what": "PLATEAU と 5m 標高から土台を起こした。写真はまだ無い"},
        {"at": NOW, "by": BY, "what":
            "観察票・数え・測量（data/reports/library/）から設計した。parapet_top を PLATEAU の 33.7 m（塔屋を含む）から測った 25.3 m に直した。"
            "f1・f3 は 9 列の柱割り、f0・f2 は柱割り無しで窓を glazing、凹み・軒下・庇・箱・階段は composite で書いた"},
        {"at": NOW, "by": BY, "what":
            "f3 の列数は観察票（null。064539230・064942113 の急な斜めで遠い列が重なる）と数え係（9。064942113 を f3 の範囲だけ正面化し直し、"
            "064539230・075830062 と縦樋でつないだ）で食い違い、数え係の 9 を採った（縦樋の並びが合うのは 9 だけで、L5 の小窓 18 個も等間隔）"},
        {"at": NOW, "by": BY, "what":
            "数え係の interpreted（f0 の空堀に面した地下の段）は見えた 5 段に混ぜず、levels の B1（z unknown）と撮影依頼にした。"
            "仮説の 3 規則のどれにも当たらないので値は置いていない"},
        {"at": NOW, "by": BY, "what":
            "通路橋・甲板・青い鉄骨の庇・大階段・路地の段・f0 の前の段々の広場と空堀・木・街灯は、棟と棟の間か 2 棟に触れる物なので棟 JSON に置かず、"
            "requests（kind tool）で敷地に上げた。正面入口の床だけ玄関として置き、境目を check に上げた"},
        {"at": NOW, "by": BY, "what":
            DECISION + " に従ってやり直した：大階段（steps、上の段が入口の床。それまでの「正面入口の床」の composite は外した）と、f0 の前の"
            "灰色の段（steps）・赤い煉瓦の段とテラスの北の端・空堀の擁壁と手すり（composite）を棟 JSON に置いた。敷地の依頼から大階段と段々の広場を外した。"
            "エネルギーセンターの扉の下端を中段の踊り場 1.8 m に揃えた。階高は面ごとの値のまま。check 2 件を closed にした"},
        {"at": NOW, "by": "検収係（kenshu・claude-opus-5-5）", "what":
            "recheck（out/library/recheck/20261107_144045）と photoproj（20261107_145028）から直した（data/reports/library/kenshu_summary.json）。"
            "①f0 の北の張り出し箱と下の柱型：正面化の見かけの縁を登録の視点で箱の前の面へ戻した（s 30.20〜36.85 → 30.57〜36.65、z 8.3〜11.55 → 7.8〜10.65、"
            "開口も同様。柱型 30.6〜37.0 → 30.57〜36.66）。南の箱は「北と同じ高さ」なので 7.8〜10.65。"
            "②f0 の L1 の凹んだ板：上端 6.0 → 6.1、北の板の縁を読み直し。③f0 のエネルギーセンターの扉 s 25.15〜26.0 → 25.40〜26.33。"
            "④屋上の塔：2 枚の登録済みの写真の三角測量で s 6.9〜9.1・壁から 1.7〜3.7 m 奥・上端 33.7 → s 10.8〜12.5・壁の面から 1.6 m 奥まで・上端 29.7。"
            "⑤塔の北の低い盛り上がりは役割が書けない（検収 C の 1）ので外した（out/library/soten.json、屋上の撮影依頼のまま）。"
            "⑥f2 の正面入口の庇と丸柱：見かけの幅のままだった s を設計の出のまま戻した（庇 15.0〜26.8 → 17.17〜23.44、柱 17.4・24.4 → 17.85・22.68、太さ 0.8 → 0.5）"},
        {"at": NOW, "by": "検収係（kenshu・claude-opus-5-5）", "what":
            "2 回目の recheck（out/library/recheck/20261107_150541）から直した。①f0 の北の箱の上端 10.65 → 10.85（070904733 +0.20・075102006 +0.21）、"
            "南の箱の上端 10.65 → 11.0（070904733 +0.38・075114044 +0.32）。②屋上の塔：東の面は壁の面なので見かけの値がそのまま実際 → "
            "s 10.8〜12.5 → 10.45〜12.25、上端 29.7 → 30.1、奥行き 1.6 → 1.85（1 回目は三角測量の交点（壁より 0.2 m 手前）の値を使っていた）。"
            "③f2 の庇：高さも見かけのままだった。柱の上端（天井との境）を 075639031 で戻して天井 9.63（設計 10.5）、鼻先 9.63〜10.64（設計 10.3〜11.35）、"
            "前の縁の出 5.85 → 5.3、両端 17.07〜23.85、柱 17.8・22.7"},
    ]
    b["photos"] = []
    b = schema.apply_hypotheses_off(b, HYPOTHESES_OFF)
    return b


def main() -> int:
    b = build()
    errs = schema.validate_building(b)
    if errs:
        print("validate のエラー：")
        for e in errs:
            print("  ", e)
    schema.save_building(b, OUT)
    print(f"書いた：{OUT.relative_to(ROOT)}（faces {len(b['faces'])}、parts {len(b['parts'])}、requests {len(b['requests'])}）")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
