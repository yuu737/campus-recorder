"""図書館（library）の設計（棟 JSON）初版。観察票・数え・測量の結果を塊・面に落とす。

元：out/library/kansatsu_f0〜f3.json・kansatsu.md（観察係）、kazoe_f1.json（数え係）、sokuryo_f1.json（測量係）、
out/photos/shiwake_library.json（仕分け係）。数えられたのは f1（北北東）だけ。
f0・f2・f3 は割付が無い／数えられないので窓なしの壁にし、寸法の無い物（屋上の塔・大階段・曲面ガラス・
張り出しの箱・西端の空洞・外階段・通路橋）は塊にせず requests に回す（決めつけない）。
実行: python -I -X utf8 scripts/design_library.py  → data/buildings/library.json
"""
import json, sys
from pathlib import Path
sys.path.insert(0, ".")
from recorder.schema import new_building, save_building, validate_building, Measurement
from recorder.rectify import read_image

PL = "plateau:bldg_feae3e15-dd53-4b4a-8f30-bb05525d15e3"
KZ = json.loads(Path("out/library/kazoe_f1.json").read_text(encoding="utf-8"))
SK = json.loads(Path("out/library/sokuryo_f1.json").read_text(encoding="utf-8"))
STITCH = "out/rectified/20261103b/library_f1_stitched.jpg"
EV_ST = ["PXL_20261103_075500328", "PXL_20261103_075510510"]          # つなぎ画像の元 2 枚
EV_F1 = KZ["bays"]["evidence"]                                       # 数えの 5 枚
EV_WEST = ["PXL_20261103_075437320", "PXL_20261103_075448020", "PXL_20261103_075510510"]

# --- つなぎ画像の行（測量係の値＋設計係が同じ画像で読んだ行） ---------------------------
PX_PER_M = round((SK["scale"]["px_per_m"] + SK["scale"]["check_px_per_m"]) / 2, 1)   # 122.3（PLATEAU 辺 122.6 とタイル 122.0 の平均）
ROW_PARAPET = 313          # 設計係：sky→壁の縦の勾配の山（x 1400・3000・4600・6200 で 309〜315）
WALL_H = 24.5              # 測量係：東の角の地面〜パラペット上端 約 3000 px
ROW_SMALL_TOP, ROW_SMALL_BOT = 550, 933            # 設計係：最上段の小窓の上端・下端（x 1380〜1480 の縦の勾配）
ROW_WIN_TOP = {4: 1049, 3: 1603, 2: 2154}          # 測量係：横長窓の開口の上端
ROW_SILL_TOP = {4: 1471, 3: 2024, 2: 2578}         # 測量係：窓の下の腰壁の上端
SMALL_W_PX, SMALL_MID_PX = 209, 120                # 設計係：小窓の幅 208・210 px、2 つの間の柱 120 px（x 1313〜1851）


def z_of(row):
    """パラペット上端（WALL_H）から下へ px を換算した高さ（足元の地面からの m）。"""
    return WALL_H - (row - ROW_PARAPET) / PX_PER_M


def m(value, tol, status, evidence=(), method="", note="", unit="m"):
    return Measurement(round(value, 2) if isinstance(value, float) else value, unit, tol, status, list(evidence), method, note).to_json()


def unk(unit="m", method="", note=""):
    return Measurement(None, unit, None, "unknown", [], method, note).to_json()


def est(value, tol, evidence, method, unit="m"):
    return m(value, tol, "estimated", evidence, method, unit=unit)


def copy_meas(src, unit="m"):
    """係の結果をそのまま写す（status・evidence・method）。"""
    return Measurement(src["value"], src.get("unit", unit), src.get("tol"), src["status"], list(src["evidence"]),
                       src["method"], src.get("note", "")).to_json()


Z_NOTE = (f"つなぎ画像 library_f1_stitched の行を、パラペット上端（行 {ROW_PARAPET}、設計係が勾配で読んだ）＝壁の高さ {WALL_H} m（estimated）から"
          f"下へ {PX_PER_M} px/m（測量係の 2 つの縮尺の平均）で換算。上端の値が推定なので estimated")

# --- 建物全体 ------------------------------------------------------------------------------
P = [[42.15, -57.77], [60.68, -22.94], [17.05, 0.27], [-1.48, -34.55]]
b = new_building("library", "名城大学付属図書館")
b["site_id"] = "library"
b["footprint"] = {"status": "estimated", "evidence": [PL], "polygon": P,
                  "method": "PLATEAU 名古屋市 2022 版（2021 年測量）の LOD0 の足元形状。測量係が f1 の幅（角〜角 6060 px）を辺 49.42 m と置き、タイル 1 段の物差しと 0.5% で合った。"
                            "OSM は (24.01,-3.44) で f1 を 2 辺に分けるが、9 柱間の壁は角から角まで続き段差は見えない（測量係）ので PLATEAU のまま"}
b["ground_z"] = m(41.67, 1.0, "estimated", ["gsi:dem5a"], "国土地理院 5m 標高を足元の中心で読む",
                  "f1 の前の道路は東が高い坂（観察票）。足元の地面の高さは面と位置で違い、模型の z=0 はこの 1 点")
b["floor_count"] = copy_meas({**KZ["floors"], "unit": "階", "tol": None})
fp = SK["floor_pitch"]
b["floor_pitch"] = Measurement(fp["value"], "m", fp["tol"], fp["status"], list(fp["evidence"]), fp["method"],
                               "適用範囲：" + fp["applies_to"]).to_json()
b["parapet_top"] = est(WALL_H, 1.0, EV_ST,
                       "測量係 sokuryo_f1：東の角の地面〜パラペット上端がつなぎ画像で約 3000 px → 122.6 px/m で 約 24.5 m。"
                       "内訳の検算：5 段×階高 4.52 m＝22.6 m。残り約 1.9 m は、足元の段（坂で東西の地面の高さが違い個別には未測。窓の段の間隔より約 4% 短い）と、"
                       "最上段の小窓の上の無地の帯・パラペットの分と見る（どちらも個別には測っていない）。"
                       "PLATEAU の measuredHeight 33.7 m は屋上の塔（f2 側に 2 本）を含む最高高さなので使わない")
b["parapet_top"]["note"] = "f1 の東の角で測った高さ。他の面で同じかは未確認（全周に同じと仮定して塊を作った）"
b["blocks"] = [{"id": "A", "name": "図書館の本体（5 段）", "polygon": P,
                "top_z": est(WALL_H, 1.0, EV_ST, "parapet_top と同じ。f1 で測った壁の高さを全周に同じと仮定")}]

# --- 面 ------------------------------------------------------------------------------------
edges = [("f0", "東南東面 0（方位 118°、39.5 m）", P[0], P[1], 39.45),
         ("f1", "北北東面 1（方位 28°、49.4 m）", P[1], P[2], 49.42),
         ("f2", "西北西面 2（方位 298°、39.4 m）・主入口", P[2], P[3], 39.44),
         ("f3", "南南西面 3（方位 208°、49.4 m）", P[3], P[0], 49.42)]
faces = {}
for fid, name, p0, p1, L in edges:
    faces[fid] = {"id": fid, "name": name, "from": p0, "to": p1,
                  "length": est(L, 1.0, [PL], "足元の辺の長さ"),
                  "bays": {}, "windows": [], "bands": [], "openings": [], "glazing": [], "ledges": [], "materials": []}
b["faces"] = list(faces.values())

# f1：数え・測量の結果（from＝東の角、柱間の番号は東から 1〜9）
f1 = faces["f1"]
L1 = f1["length"]["value"]
bp = SK["bay_pitch"]
pitch, pier = bp["value"], bp["pier_width"]["value"]
f1["bays"] = {
    "count": copy_meas({**KZ["bays"], "unit": "個", "tol": None}),
    "pitch": Measurement(bp["value"], "m", bp["tol"], bp["status"], list(bp["evidence"]), bp["method"]).to_json(),
    "pier_width": copy_meas(bp["pier_width"]),
    "end_margin": est(round((L1 - KZ["bays"]["value"] * pitch) / 2, 2), 0.1, EV_ST,
                      f"(辺 {L1} − 9×{pitch}) ÷ 2。測量係：面の幅 6060 px − 9×661 px ≒ 110 px ≒ 0.9 m が角の柱型の外側ぶん（角の柱型は 0.96 m）。"
                      "東西の角で同じ作りに見えるので半分ずつ置いた"),
}
f1["render_view"] = True
win_w = pitch - pier
# 横長窓：2〜4 段目。2・3 段目は東から 1〜6 柱間だけ（7〜9 は空洞・ガラス・踊り場・橋で窓でない → requests）
for k in (2, 3, 4):
    w = {"name": f"{k}段目の横長窓（1 つ/柱間）", "floors": [k], "per_bay": 1,
         "z_bottom": est(z_of(ROW_SILL_TOP[k]), 0.4, EV_ST, f"窓の下の腰壁の上端 行 {ROW_SILL_TOP[k]}（測量係）。" + Z_NOTE),
         "z_top": est(z_of(ROW_WIN_TOP[k]), 0.4, EV_ST, f"横長窓の開口の上端 行 {ROW_WIN_TOP[k]}（測量係）。" + Z_NOTE),
         "width": m(win_w, 0.08, "measured", EV_ST, f"測量係：窓の開口 661−123＝538 px ≒ 4.39 m（柱間隔 {pitch} − 柱型 {pier}）"),
         "recess": unk(method="観察票：深い彫りの格子だが『窓の彫りの深さ』は unknown。斜めの写真で測っていない",
                       note="模型は道具の既定値で置く（requests に撮影依頼）")}
    if k in (2, 3):
        w["bays"] = [1, 2, 3, 4, 5, 6]
        w["note"] = ("東から 7〜9 柱間は窓にしない。3 段目は空洞とガラス面（数え係の例外）、2 段目は外階段の踊り場・通路橋・煉瓦の壁（設計係がつなぎ画像で確認）。"
                     "寸法が無いので空洞にもせず壁のまま（未設計）。requests 参照")
    f1["windows"].append(w)
# 最上段（5 段目）：柱間ごとに小窓 2 つ
f1["windows"].append({
    "name": "5段目の小窓（2 つ/柱間）", "floors": [5], "per_bay": 2,
    "z_bottom": est(z_of(ROW_SMALL_BOT), 0.4, EV_ST, f"小窓の下端 行 {ROW_SMALL_BOT}（設計係がつなぎ画像の x 1380〜1480 の縦の勾配で読んだ）。" + Z_NOTE),
    "z_top": est(z_of(ROW_SMALL_TOP), 0.4, EV_ST, f"小窓の上端 行 {ROW_SMALL_TOP}（同上）。" + Z_NOTE),
    "width": m(win_w, 0.08, "measured", EV_ST, "柱型の間の幅（2 つの窓と中の柱の合計）。窓 1 つは 208・210 px ≒ 1.71 m、中の柱 120 px ≒ 0.98 m（設計係が x 1313〜1851 で読んだ）"),
    "gap": est(round(win_w / 2 - SMALL_W_PX / PX_PER_M, 2), 0.1, EV_ST,
               f"模型の割付（柱間の内側を 2 等分し、それぞれから gap を引く）で窓 1 つの幅 {SMALL_W_PX / PX_PER_M:.2f} m を出すための値。"
               "実物は窓が柱型に接し中の柱 0.98 m なので、模型の窓の中心は実物より約 0.25 m 柱間の中央に寄る"),
    "recess": unk(method="観察票：彫りの深さは unknown", note="模型は道具の既定値"),
    "rail": False,
})
# 色（つなぎ画像の柱型の平均色。夕方・白バランス未補正）
img = read_image(STITCH)
bgr = img[1100:1500, 1860:1960].reshape(-1, 3).mean(axis=0)
color = "#%02x%02x%02x" % (int(bgr[2]), int(bgr[1]), int(bgr[0]))
f1["materials"] = [{"name": "外装タイル（黄土色）", "where": "柱型・腰壁・パラペット・足元", "color": color,
                    "color_note": "つなぎ画像の柱型（x 1860〜1960、y 1100〜1500）の平均色。夕方の撮影で白バランス未補正",
                    "unit_h": est(0.06, 0.01, EV_ST, "測量係：腰壁の縦の周期 8.53 px＝1 段 70 mm（タイル 60 mm＋目地 10 mm と置いた。45 二丁なら PLATEAU と 15% 以上食い違うので違う）"),
                    "joint_w": est(0.01, 0.005, [], "JIS の標準目地幅と置く")}]

# f0・f2・f3：割付が無い／数えられない → 列数・窓は unknown、窓なしの壁
WHY = {"f0": "観察票 kansatsu_f0：格子の窓割りが無い（無地のタイル壁＋中央の縦長の曲面ガラス＋張り出しの箱 2 か所＋小窓が縦 3 段）。割付が無いので数えられない",
       "f2": "観察票 kansatsu_f2：中央がガラスの凹部（4 列×5〜6 段の格子）で、両端が入る写真が無く数えられない",
       "f3": "観察票 kansatsu_f3：南東の角からの強い斜め 4 枚だけ。列数・段数・窓の形とも不明"}
for fid, why in WHY.items():
    f = faces[fid]
    f["bays"] = {"count": unk("個", method=why), "pitch": unk(method=why), "pier_width": unk(method=why)}
    f["windows_note"] = "窓は unknown。模型と立面は窓なしの壁（" + why + "）"

# --- 写真 ------------------------------------------------------------------------------------
from recorder.photos import load_index
for rec in load_index()["photos"]:
    if rec.get("building") != "library":
        continue
    b["photos"].append({"id": rec["id"], "file": rec["file"], "face": rec.get("face"),
                        "role": "数え・測量" if rec["id"] in EV_F1 else ("期間前" if rec.get("period") == "before" else "観察")})

# --- 撮影依頼（面・何を・どこから・なぜ） ------------------------------------------------------
b["requests"] = [
    {"face": "f0", "what": "f0 を正面から、南端と北端がそれぞれ入る 2 枚（あれば両端が入る遠めの 1 枚）", "where": "共通講義棟東の上階の廊下（P135 と同じ場所）か、東の通路の向かい側",
     "why": "f0 は列数・窓が unknown で窓なしの壁にしている。期間中の正面寄りは北半分だけで南端は強い斜め", "status": "open"},
    {"face": "f0", "what": "中央の縦長の曲面ガラスを正面から 1 枚と、壁沿いの斜めから 1 枚（出が分かるように）", "where": "f0 の前の灰色タイルの広場から",
     "why": "曲面ガラスの位置・幅・高さ・出が無いので模型に入れていない", "status": "open"},
    {"face": "f0", "what": "張り出しの箱（黒いガラリ）2 か所を、それぞれ正面と真横から", "where": "f0 の前の通路から",
     "why": "箱の位置・大きさ・出が無いので塊にしていない", "status": "open"},
    {"face": "f2", "what": "北端が入る 1 枚と南端が入る 1 枚（できれば両端が入る遠めの 1 枚）", "where": "道路の向こう（本部棟側）の歩道。北端はタワー75 側の坂の上から",
     "why": "端が隠れていて面の長さと入口・ガラスの凹部の位置の比が出せない。f2 は窓なしの壁にしている", "status": "open"},
    {"face": "f2", "what": "中央のガラスの凹部を正面から 1 枚と、凹部の縁を斜めから 1 枚", "where": "大階段の下の道路から",
     "why": "凹部の幅・深さ・上下端（4 列×5〜6 段の格子の寸法）が無いので glazing を入れていない", "status": "open"},
    {"face": "f2", "what": "大階段を真横から 1 枚（段が全部入るように）と、踊り場の有無が分かる上からの 1 枚", "where": "大階段の北か南の横、階段から離れて",
     "why": "段数が 15〜17（±2）で決まらず、踊り場があるかも不明。寸法が無いので部品にしていない", "status": "open"},
    {"face": "*", "what": "屋上の塔 2 本が見える高い所からの 1 枚と、地上から塔が壁の上に見える 1 枚", "where": "タワー75 の上階の窓・廊下から（屋上が見える所）",
     "why": "PLATEAU の高さ 33.7 m と壁の高さ 24.5 m の差は塔と見るが、位置・大きさを確かめていないので塊にしていない", "status": "open"},
    {"face": "f1", "what": "西端 3 柱間（東から 7〜9）の 2・3 段目の空洞とガラス面を、斜め 2 方向から各 1 枚と、空洞の中から天井・奥の壁を 1 枚", "where": "f1 の前の坂道と、外階段の踊り場から",
     "why": "空洞の奥行き・床の高さ・ガラス面の引っ込みが無いので窓の例外にも空洞にもしていない（今は壁のまま）。数え係は 2〜4 段目の横長窓を 9 と数えたが、つなぎ画像では 2 段目の 7〜9 も踊り場・橋・壁で窓に見えない。この食い違いも確かめたい", "status": "open"},
    {"face": "f1", "what": "外階段と通路橋を真横から 1 枚ずつ（段と橋の下端が入るように）", "where": "f1 の前の坂道の西端と、橋の下の通路から",
     "why": "外階段の段数・幅、通路橋の取り付く高さ（3 段目の床の前後、±半階で決めていない）と幅が無いので部品にしていない", "status": "open"},
    {"face": "f1", "what": "東の角と西の角で、地面から足元の段の上端までが入る正面の 1 枚ずつ", "where": "f1 の前の道路の反対側",
     "why": "足元の段の高さは坂で東西が違い個別に未測。壁の高さ 24.5 m（estimated）の内訳（5×4.52＝22.6 m との差 1.9 m）を確かめる", "status": "open"},
    {"face": "f1", "what": "壁沿いの強い斜めから 1 枚（窓の彫りの奥行きが見えるように）", "where": "f1 の東の角の先から壁に沿って",
     "why": "窓の彫りの深さ（recess）が unknown。模型は道具の既定値で置いている", "status": "open"},
    {"face": "f3", "what": "f3 を正面から、東端・西端がそれぞれ入る 2 枚（重なり 3 柱間以上）と遠めの 1 枚", "where": "11 号館側の広場・通路から、壁から十分離れて",
     "why": "南東の角からの強い斜めしか無く、列数・段数・窓が unknown。窓なしの壁にしている", "status": "open"},
]
b["history"] = [
    {"at": "", "by": "recorder new --from-site", "what": "PLATEAU と 5m 標高から土台を起こした。写真はまだ無い"},
    {"at": "2026-11-04T02:52", "by": "Claude（設計係, claude-opus-5-5）",
     "what": "初版。観察票・数え・測量（out/library/kansatsu_f0〜f3.json・kazoe_f1.json・sokuryo_f1.json）から。"
             "f1 は 9 柱間・5 段・柱間隔 5.40・柱型 1.00・階高 4.52 を係の status と根拠のまま写した。窓の上下端はつなぎ画像の行を壁の高さ 24.5 m（estimated）から換算。"
             "塊は 1 つ（壁の高さ 24.5 m、PLATEAU 33.7 m は塔込みなので不採用）。f0・f2・f3 は割付が無い／数えられないので窓なしの壁。"
             "屋上の塔・大階段・曲面ガラス・張り出しの箱・西端の空洞・外階段・通路橋は寸法が無いので塊・部品にせず requests へ"},
    {"at": "2026-11-04T02:52", "by": "Claude（設計係, claude-opus-5-5）",
     "what": "数え係との食い違い（未解決）：数えは 2〜4 段目の横長窓を各 9 としたが、つなぎ画像では 2 段目の東から 7〜9 柱間も外階段の踊り場・通路橋・煉瓦の壁で窓が見えない。"
             "2・3 段目の窓は 1〜6 柱間だけ置き、7〜9 は壁のまま requests に回した"},
]

errs = validate_building(b)
print("validate:", errs or "OK")
save_building(b, Path("data/buildings/library.json"))


def count_status(o, c):
    if isinstance(o, dict):
        if "status" in o and "value" in o:
            c[o["status"]] = c.get(o["status"], 0) + 1
            return
        for v in o.values():
            count_status(v, c)
    elif isinstance(o, list):
        for v in o:
            count_status(v, c)
    return c


print("saved: faces", len(b["faces"]), "blocks", len(b["blocks"]), "requests", len(b["requests"]),
      "status", count_status({k: v for k, v in b.items() if k not in ("photos",)}, {}))
