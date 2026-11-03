"""本部棟の設計（棟 JSON）を、今日の写真の計測と目視の読み取りから組み立てる。

役割分担：数える・換算するのはコード（measure.py）。「どの部品がどこにあるか」は設計者（Claude）が
写真と航空写真を見て書く。値は全部 measured / estimated / unknown のどれかで、推定には方法を書く。
実行: python -I -X utf8 scripts/design_honbu.py  → data/buildings/honbu.json
"""
import json, sys
from pathlib import Path
sys.path.insert(0, ".")
from recorder.schema import new_building, save_building, validate_building, Measurement
from recorder.rectify import read_image

PL = "plateau:bldg_05e6cc71-7768-43d3-9af5-3ba5663c44c8"
EV_SW = ["PXL_20261103_042900775", "PXL_20261103_042822099", "PXL_20261103_042853166"]   # 南西面の正面化 3 枚
EV_NE = ["PXL_20261103_043312297", "期間前の写真（削除）", "期間前の写真（削除）"]   # 北東面（中庭側）。P・A は期間前
EV_NE_OBS = EV_NE + ["期間前の写真（削除）", "期間前の写真（削除）"]   # 観察票 used_for_NE
EV_NE_COUNT = ["期間前の写真（削除）", "期間前の写真（削除）", "PXL_20261103_043312297", "期間前の写真（削除）"]
EV_NE_PITCH = ["PXL_20261103_043312297", "期間前の写真（削除）"]
EV_E_MOVE = ["PXL_20261103_043259125", "PXL_20261103_043304319", "期間前の写真（削除）", "期間前の写真（削除）",
             "期間前の写真（削除）", "期間前の写真（削除）", "期間前の写真（削除）", "期間前の写真（削除）"]
EV_NW = ["PXL_20261103_043004629", "PXL_20261103_043034043", "PXL_20261103_043055592", "PXL_20261103_043103134"]  # 北西面（4階の翼）
EV_ST = ["PXL_20261103_043123901", "PXL_20261103_043136840", "PXL_20261103_043150092"]   # 北端の階段塔
EV_WC = ["PXL_20261103_042927350", "PXL_20261103_042946748", "PXL_20261103_043004629"]   # 西角の階段塔（無窓）
EV_AER = ["gsi:aerial_z18"]

# --- 南西面の計測（measure.py の結果と格子の目視） ------------------------------------------
PLATEAU_SW_LEN, BAYS_SW, BAY_PX, FLOOR_PX = 49.7, 9, 672.2, 513.9
MPP = PLATEAU_SW_LEN / BAYS_SW / BAY_PX
TILE_COURSE_PX = 8.65
MPP_TILE = 0.070 / TILE_COURSE_PX
GROUND_ROW, PILOTIS_CEIL, PARAPET_ROW, WIN5_TOP, WIN5_BOT, PIER_PX = 3211, 2615, 329, 557, 909, 135
bay_m = PLATEAU_SW_LEN / BAYS_SW
floor_m = FLOOR_PX * MPP
parapet = (GROUND_ROW - PARAPET_ROW) * MPP
pilotis = (GROUND_ROW - PILOTIS_CEIL) * MPP
pier_m = PIER_PX * MPP
scale_note = (f"画素→m は PLATEAU の南西面の長さ {PLATEAU_SW_LEN} m ÷ {BAYS_SW} スパンで換算（{MPP:.5f} m/px）。"
              f"検算：タイル1段 {TILE_COURSE_PX} px を JIS の 70 mm と置くと {MPP_TILE:.5f} m/px（差 {abs(MPP_TILE/MPP-1)*100:.1f}%）")


def m(value, tol, status, evidence=(), method="", note="", unit="m"):
    return Measurement(round(value, 2) if isinstance(value, float) else value, unit, tol, status, list(evidence), method, note).to_json()


def unk(unit="m", note=""):
    return Measurement(None, unit, None, "unknown", [], "", note).to_json()


def est(value, tol, evidence, method, unit="m"):
    return m(value, tol, "estimated", evidence, method, unit=unit)


# --- 足元と塊 -----------------------------------------------------------------------------
# PLATEAU の足元（反時計回り）。X は南西の塊の北東の辺と翼の西の辺の交点（計算値）
P = [[-51.7, -13.3], [-7.7, -36.5], [-0.4, -22.5], [-32.9, -5.3], [-18.6, 21.9], [-26.1, 25.9], [-27.4, 23.5], [-30.7, 25.2], [-40.8, 6.0], [-41.4, 6.3]]
X = [-44.32, 0.74]
BLOCK_A = [P[0], P[1], P[2], X]                       # 5 階の塊（南西面・幅 15.8 m）
BLOCK_B = [X, P[3], P[4], P[5], P[6], P[7], P[8], P[9]]   # 4 階の翼（幅 12.9 m）

b = new_building("honbu", "本部棟")
b["site_id"] = "honbu"
b["footprint"] = {"status": "estimated", "evidence": [PL],
                  "method": "PLATEAU 名古屋市 2022 版（2021 年測量）の LOD0 の足元形状をそのまま使う", "polygon": P}
b["ground_z"] = m(43.9, 0.7, "estimated", [], "国土地理院 5m 標高（DEM5A）を南西面の中点で読む", "PLATEAU の base_z は 41.94。差 2 m は未解決")
b["floor_count"] = m(5, None, "measured", EV_SW, "正面化した写真で窓の段を数えた（判断役 Claude）。1 階はピロティ", unit="階")
b["floor_pitch"] = est(floor_m, 0.15, EV_SW, "正面化→横エッジの自己相関で周期 513.9 px。" + scale_note)
b["parapet_top"] = m(parapet, 0.4, "estimated", EV_SW, "正面化画像でパラペット上端と柱の足元の行の差 2882 px を換算",
                     "PLATEAU の measuredHeight 32.1 m との差 8.4 m は屋上の塔屋（航空写真に小さい四角）と解釈")
b["levels"] = [{"id": "pilotis_ceiling", "name": "ピロティ天井", "z": est(pilotis, 0.3, EV_SW, "正面化画像の行の差 596 px を換算")}]
b["blocks"] = [
    {"id": "A", "name": "南西の塊（5 階）", "polygon": BLOCK_A, "top_z": est(parapet, 0.4, EV_SW, "parapet_top と同じ")},
    {"id": "B", "name": "北の翼（4 階）", "polygon": BLOCK_B,
     "top_z": est(17.5, 1.5, EV_NW, "写真で 4 階建て、西側の地盤が約 0.8 m 高い。階高 3.8 m×4＋パラペット 1 m＋地盤差 0.8 と置いた目視の推定")},
]

# --- 面 ------------------------------------------------------------------------------------
edges = [
    ("NE", "北東面（中庭側・ピロティ）", P[2], P[3], 36.8),
    ("E", "東面（翼の東側）", P[3], P[4], 30.8),
    ("NE2", "北端の面（階段塔）", P[4], P[5], 8.5),
    ("N_jog1", "北端の段差（西向き）", P[5], P[6], 2.7),
    ("N_jog2", "北端の段差（北東向き）", P[6], P[7], 3.7),
    ("NW_n", "北西面 北半", P[7], P[8], 21.8),
    ("NW_s", "北西面 南半", P[9], P[0], 22.1),
    ("SW", "南西面（正面・ピロティ）", P[0], P[1], 49.7),
    ("SE", "南東端面", P[1], P[2], 15.8),
]
faces = {}
for fid, name, p0, p1, L in edges:
    faces[fid] = {"id": fid, "name": name, "from": p0, "to": p1,
                  "length": est(L, 1.0, [PL], "PLATEAU LOD0 の辺の長さ"),
                  "bays": {}, "windows": [], "bands": [], "openings": [], "glazing": [], "ledges": [], "materials": []}
b["faces"] = list(faces.values())

# 南西面：計測した面
sw = faces["SW"]
sw["bays"] = {
    "count": m(BAYS_SW, None, "measured", EV_SW, "正面化画像に周期の格子を重ね、判断役 Claude が端から端まで数えた", unit="個"),
    "pitch": est(bay_m, 0.15, EV_SW, "PLATEAU の辺の長さ ÷ スパン数。" + scale_note),
    "pier_width": est(pier_m, 0.15, EV_SW, "正面化画像で柱型の幅 135 px を換算"),
    "end_margin": est(0.0, 0.3, [], "両端の柱型は柱間隔の格子に乗っている（端の余白なし）と置く"),
    "pier_depth": est(0.12, 0.08, EV_SW, "柱型がわずかに出る。斜めの写真 32 の目視"),
}
sw["render_view"] = True
win_top = (GROUND_ROW - WIN5_TOP) * MPP
win_bot = (GROUND_ROW - WIN5_BOT) * MPP
for k in (2, 3, 4, 5):
    off = (5 - k) * floor_m
    sw["windows"].append({
        "name": f"{k}階窓", "floors": [k], "per_bay": 2 if k == 5 else 1, "gap": 0.6,
        # 5 階だけ小窓が 1 スパンに 2 つ（数え係の報告 out/honbu_SW_スパン数.md。5 階の窓は全部で 18）
        "z_bottom": est(win_bot - off, 0.2, EV_SW, "5階の窓の下端を正面化画像で読み、階高の倍数で下の階へ写す"),
        "z_top": est(win_top - off, 0.2, EV_SW, "5階の窓の上端を正面化画像で読み、階高の倍数で下の階へ写す"),
        "width": est(bay_m - pier_m, 0.2, EV_SW, "柱間隔 − 柱型の幅"),
        "recess": est(0.5, 0.2, EV_SW + ["PXL_20261103_043405159"], "斜めの写真で柱型の奥行きを目視。柱間隔との比で約 0.5 m"),
    })
    sw["ledges"].append({"name": f"{k}階の窓台の段", "z": est(win_bot - off - 0.2, 0.2, EV_SW, "窓の下の横の段。正面化画像の目視"),
                         "depth": est(0.08, 0.05, EV_SW, "目視"), "height": est(0.2, 0.05, EV_SW, "目視"), "material": "tile"})
sw["openings"] = [
    {"name": "ピロティ（駐車）", "bays": [4, 5, 6], "z_top": est(pilotis, 0.3, EV_SW, "正面化画像の行の差を換算"),
     "depth": est(8.0, 2.0, EV_SW, "写真 6 で奥に壁が見える。目視")},
    {"name": "ピロティ（通り抜けの道路）", "bays": [7, 8, 9], "z_top": est(pilotis, 0.3, EV_SW, "正面化画像の行の差を換算"),
     "depth": est(15.8, 1.0, [PL, "PXL_20261103_043320836"], "写真 28 で反対側（中庭）まで抜けて見える → 塊の幅（PLATEAU）と同じ")},
]
sw["glazing"] = [{"name": "1階の事務室（ガラス張り）", "along0": 0.0, "along1": est(3 * bay_m, 0.3, EV_SW, "スパン 1〜3"),
                  "z_bottom": 0.0, "z_top": est(pilotis - 0.5, 0.3, EV_SW, "ピロティ天井の梁の下まで"),
                  "inset": est(0.3, 0.2, EV_SW, "柱型の面からの引っ込み。目視"), "depth": est(15.8, 1.0, [PL], "塊の幅")}]
img = read_image("out/rectified/20261103/PXL_20261103_042900775_front.jpg")
bgr = img[345:520, 2000:4600].reshape(-1, 3).mean(axis=0)
color = "#%02x%02x%02x" % (int(bgr[2]), int(bgr[1]), int(bgr[0]))
sw["materials"] = [{"name": "煉瓦タイル（橙）", "where": "柱型・パラペット・壁面全体", "color": color,
                    "color_note": "正面化画像のパラペットの平均色（日向。白バランス未補正）",
                    "unit_w": est(21.9 * MPP, 0.03, EV_SW, "正面化画像の縦エッジの自己相関 21.9 px を換算。千鳥積みなので実寸の半分の可能性あり"),
                    "unit_h": est(0.06, 0.01, EV_SW, "1段の周期 8.65 px＝0.071 m。JIS 二丁掛・小口の高さ 60 mm＋目地 10 mm と一致"),
                    "joint_w": est(0.01, 0.005, [], "JIS の標準目地幅と置く")}]

# 北東面（中庭側）：観察票 out/honbu/kansatsu_NE.json・数え kazoe_NE.json・測量 sokuryo_NE.json（2026-11-04）から
# 南東から：無窓の端の壁（幅 unknown）→ 歩行者の通り抜けの上 2 スパン → 縦樋 → 道路の上 3 スパン → 縦樋 → 入隅側 1 スパン → 入隅
NE_PITCH, NE_PIER, NE_PITCH_PX, NE_PX_PER_M = 5.62, 0.85, 1253, 223.1
ne = faces["NE"]
ne["length"]["method"] += ("。食い違いあり（未解決）：測量係の P130 の正面化で面の長さ 2058 px を階高 217 px＝4.22 m で換算すると 40.0 m（+8.7%）、"
                           "36.8 m を当てると階高 3.88 m（−8%）。一方 P124 では 6 スパン×5.62＝33.7 m＋無窓の端の壁 0.53 スパン以上（2.98 m 以上）＝36.7 m 以上で矛盾しない。"
                           "端の壁が南東端面へ回り込んで見えている可能性があり、端の壁の幅が決まるまで足元（PLATEAU）は動かさない")
ne["bays"] = {
    "count": m(6, None, "measured", EV_NE_COUNT,
               "数え係：P124 の正面化で左端の窓（x=353）から入隅側の窓（x=2742）まで 柱間隔 479 px で 4.99 間隔＝6 スパン、左は無窓の煉瓦・右は入隅。"
               "別の数え方（P026 の縦樋 2 本の間が 3.00 スパン＋南東に 2、043312297 で縦樋→1 スパン→入隅）でも 6。観察票（P026＋A061＋043312297）とも一致", unit="個"),
    "pitch": est(NE_PITCH, 0.1, EV_NE_PITCH,
                 f"測量係：043312297 を北東面だけの ROI で正面化し、柱型の縁どうし {NE_PITCH_PX} px。縮尺は外装タイル 1 段（15.62 px＝70 mm、{NE_PX_PER_M} px/m）→ 5.616 m。"
                 "検算：P026 の正面化で柱間隔÷階高＝288.4/216.3＝1.333（043312297 で 1.332）×階高 4.22＝5.627 m。南西面の比 1.306〜1.310 より約 2% 大きく、"
                 "南西面の 5.52 m と同じとは置かない。タイルの型番（小口と判断）は未確認なので estimated"),
    "pier_width": est(NE_PIER, 0.05, EV_NE_PITCH, "測量係：043312297_left で柱型 188 px（0.84 m）、P026 で 43〜44 px（0.85 m、階高換算）。柱型：窓≒1:5.6（観察票の 1:5〜6 と合う）"),
    # from＝南東の角 (-0.4,-22.5)、to＝入隅 (-32.9,-5.3)。kansatsu_NE.json は南東の端に無窓の壁、入隅側は柱割りの終わり（余りなし）
    "margin_from": unk(note="南東の端の無窓の煉瓦の壁の幅。P124 では 0.53 スパン以上（左が写真の枠で切れる）、P130 の正面化では約 1.2 スパンと食い違い、"
                            "南東端面（SE）へ回り込んで見えている可能性もある。決まるまで null（requests に撮影依頼）。模型・立面は余り（36.8−33.72＝3.08 m）をこの南東側に置く"),
    "margin_to": est(0.0, 0.3, EV_NE_OBS, "入隅側は余りなし。観察票 out/honbu/kansatsu_NE.json：道路の北西に 1 スパンだけあって入隅（043312297）。端の壁は南東だけ"),
}
NE_WIN = {3: (1, None), 4: (1, None), 5: (2, 0.6)}
EV_NE_KENSHU = ["期間前の写真（削除）", "PXL_20261103_043312297"]
# 検収係（2026-11-04）：開口の高さ（上端＝楣のタイルの下端、下端＝下の壁の上端）を 2 枚の正面化で読む
#  P026：5F 158 px・4F 157.5 px・3F 158.5 px ÷ 階高 216.5 px × 4.22 → 3.07〜3.09 m
#  043312297_left：4F 674 px・3F 680 px ÷ 223.1 px/m（タイル）→ 3.02・3.05 m　→ 平均 3.06 m
NE_WIN_H = 3.06
for k, (per, gap) in NE_WIN.items():
    off = (5 - k) * floor_m
    w = {"name": f"{k}階窓" + ("（小窓 2 つ/スパン）" if per == 2 else "（横長 1 つ/スパン）"), "floors": [k], "per_bay": per,
         "z_bottom": est(round(win_top - off - NE_WIN_H, 2), 0.1, EV_NE_KENSHU,
                         f"上端から開口の高さ {NE_WIN_H} m を引く。開口の高さは検収係が正面化 2 枚で読んだ（P026：3.07〜3.09 m、3・4・5 階。043312297_left：3.02・3.05 m、3・4 階）。"
                         "P026 の正面化（上端とパラペットで原点を合わせた）で下端は 5F 18.73・4F 14.50・3F 10.26 m、この値との差 0.04 m 以内。"
                         "前版は南西面の窓の下端を写して 0.16〜0.21 m 高かった"),
         "z_top": est(win_top - off, 0.15, EV_NE_PITCH + ["期間前の写真（削除）"], "南西面の同じ階の窓の上端を写す（階高が南西面と同じ 4.22 m）。検収係：P026 の正面化で 5F 21.74〜21.90・4F 17.57・3F 13.35 m と読め、0.05 m 以内で一致"),
         "width": est(NE_PITCH - NE_PIER, 0.15, EV_NE_PITCH, "柱間隔 5.62 − 柱型 0.85"),
         "recess": est(0.5, 0.2, EV_NE_OBS, "南西面と同じと仮定（観察票：3・4 階は横長の窓、下に焦げ茶の腰板。奥行きは測っていない）")}
    if gap:
        w["gap"] = gap
    ne["windows"].append(w)
# 2 階：煉瓦の腰壁の帯（手すり付き、面より手前に出る）とその奥のガラスの段
ne["bands"] = [{"name": "2階の煉瓦の腰壁（上にステンレスの手すり、下は 1 階の白い軒裏）", "color": color,
                "z_bottom": est(5.1, 0.1, EV_NE_KENSHU, "煉瓦の最下段の下端。043312297 の視点で 5.08 m、P026 の視点で 5.13 m（面の平面へ写った位置を帯の平面へ戻す）。"
                                "その下に白い軒の見付けがあり下端 4.84〜4.90 m＝ピロティ天井 4.9 m と合う"),
                "z_top": est(7.1, 0.1, EV_NE_KENSHU,
                             "手すりの上端。043312297_left で帯のタイル 1 段が 24.0 px（面のタイル 15.62 px の 1.54 倍＝帯は面より手前）→ 342.9 px/m で、"
                             "手すりの上端 3152 px〜煉瓦の下端 3825 px の 673 px＝高さ 1.97 m。位置は写真の視点（photos pose）で面の平面の値を帯の平面へ戻した："
                             "043312297（面から 8.39 m・高さ 2.63 m）で 7.05 m、P026（面から 65.2 m・高さ 6.16 m）で 7.20 m。"
                             "2 階のガラス 4.9〜9.13 m の下半分が隠れる（観察票）とも合う。前版 6.1 m は『2 階床＋1.2 m』と置いた値"),
                "depth": est(2.9, 0.2, ["PXL_20261103_043312297"],
                             "043312297 の視点は面から 8.39 m。帯のタイルが面のタイルの 24.0/15.62＝1.54 倍に見える → 帯は 8.39×(1−1/1.54)＝2.93 m 手前。"
                             "タイルの周期の読み ±0.3 px で 2.89〜2.97 m。前版 2.5 m はテラスの目視"),
                "note": "南東の端は無窓の壁の手前で止まり、入隅で直角に曲がって E 面へ続く（観察票）。端の壁の幅が unknown なので面の全長に置いている"}]
ne["glazing"] = [{"name": "2階の奥まったガラスの段（方立ての格子）", "along0": 0.0, "along1": 36.8,
                  "z_bottom": est(pilotis, 0.3, EV_NE_OBS, "2 階床（ピロティ天井と同じ高さ）"),
                  "z_top": est(9.13, 0.1, ["期間前の写真（削除）"],
                               "検収係：P026 の正面化でガラスの段の上端 842 px＝9.14 m。上の階の窓の上端の並び（3 階 13.35 m − 階高 4.22 m＝9.13 m）と同じ高さ。"
                               "前版 8.62 m は『3 階床の梁成 0.5 m』と置いた値"),
                  "inset": est(0.3, 0.2, EV_NE_OBS, "腰壁の奥、柱型の面から少し引っ込む。目視"),
                  "note": "1 スパンの枚数は数えられない（観察票 per_bay null）。端の壁の幅が unknown なので面の全長に置いている"}]
ne["materials"] = sw["materials"]

# 北西面（4 階の翼）：濃い帯の中に横長の窓、1 階は地面に扉
for fid in ("NW_s", "NW_n", "E"):
    f = faces[fid]
    L = f["length"]["value"]
    pitch = 3.6
    cnt = int(L // pitch)
    ev = EV_NW if fid != "E" else EV_ST
    how = "写真 11〜17 の目視。窓 1 組が約 3.6 m 間隔に見える" if fid != "E" else "東面の写真は斜めの 1 枚だけ。西面と同じ割付と仮定"
    f["top_z"] = b["blocks"][1]["top_z"]
    f["render_view"] = fid == "NW_s"
    f["bays"] = {"count": est(cnt, None, ev, how, unit="個"), "pitch": est(pitch, 0.4, ev, how),
                 "pier_width": est(0.9, 0.3, ev, "目視。帯の幅との比"), "end_margin": est((L - cnt * pitch) / 2, 0.5, ev, "余りを両端に半分ずつ"),
                 "pier_depth": est(0.3, 0.1, ev, "柱型が壁から出ている。写真の影の幅と柱型の幅の比から目視")}
    floor_h = 3.8
    for k in range(4):
        z0 = 0.9 + k * floor_h
        zt = z0 + 2.0
        if k == 3:        # 最上階は小さい窓が 3 つ
            z0, zt, per, wid = 0.9 + k * floor_h + 0.6, 0.9 + k * floor_h + 1.8, 3, 2.9
        elif k == 0:      # 1 階は扉と窓（地面から）
            z0, zt, per, wid = 0.1, 2.3, 1, 2.7
        else:             # 2・3 階は横連窓
            per, wid = 1, 2.7
        f["windows"].append({"name": f"{k+1}階窓", "floors": [k + 1], "per_bay": per, "gap": 0.2,
                             "z_bottom": est(z0, 0.4, ev, "階高 3.8 m（4 階で塊の高さに合う値）と窓台の高さを目視で置いた推定"),
                             "z_top": est(zt, 0.4, ev, "窓の高さ。目視"),
                             "width": est(wid, 0.3, ev, "柱型の間いっぱい。目視"), "recess": est(0.12, 0.1, ev, "帯の面からの引っ込み。目視")})
        bz0, bz1 = (0.9 + k * floor_h - 0.1, 0.9 + k * floor_h + 2.1) if k not in (0, 3) else ((0.0, 2.4) if k == 0 else (0.9 + k * floor_h + 0.4, 0.9 + k * floor_h + 2.0))
        f["bands"].append({"name": f"{k+1}階の濃い腰壁（引っ込む）", "color": "#3a2c22",
                           "z_bottom": est(bz0, 0.4, ev, "窓を含む濃い帯。目視"), "z_top": est(bz1, 0.4, ev, "目視"),
                           "depth": est(-0.25, 0.1, ev, "帯はタイル面より引っ込む。写真の影から目視")})
        f["ledges"].append({"name": f"{k+1}階の帯の下の水切り", "z": est(bz0 - 0.15, 0.3, ev, "帯の下端。目視"),
                            "depth": est(0.1, 0.05, ev, "目視"), "height": est(0.15, 0.05, ev, "目視"), "material": "tile"})
    f["materials"] = sw["materials"]

# --- 部品（設計者の読み取り） ----------------------------------------------------------------
def rect_on(face_id, along0, along1, depth_out, depth_in=0.0):
    """面に沿った長方形（外側へ depth_out、内側へ depth_in）。"""
    import math
    f = faces[face_id]
    (x0, y0), (x1, y1) = f["from"], f["to"]
    dx, dy = x1 - x0, y1 - y0
    L = math.hypot(dx, dy)
    ux, uy = dx / L, dy / L
    nx, ny = uy, -ux
    a = [x0 + ux * along0 - nx * depth_in, y0 + uy * along0 - ny * depth_in]
    bb = [x0 + ux * along1 - nx * depth_in, y0 + uy * along1 - ny * depth_in]
    c = [x0 + ux * along1 + nx * depth_out, y0 + uy * along1 + ny * depth_out]
    d = [x0 + ux * along0 + nx * depth_out, y0 + uy * along0 + ny * depth_out]
    return [a, bb, c, d]


b["parts"] = [
    {"type": "coping", "name": "パラペットの笠木", "height": est(0.35, 0.1, EV_SW, "正面化画像の上端の濃い帯"), "overhang": est(0.08, 0.05, [], "目視")},
    {"type": "box", "name": "屋上の塔屋（エレベーター機械室か階段）", "material": "tile",
     "polygon": rect_on("SW", 19.0, 26.0, 0.0, 10.0)[:],   # 南西面の 19〜26 m、面から 3〜10 m 奥 → 下で作り直す
     "z0": est(parapet, 0.4, EV_SW, "塊の上"), "z1": est(parapet + 8.4, 1.0, [PL] + EV_AER, "PLATEAU の measuredHeight 32.1 − パラペット 23.7"),
     "note": "位置は航空写真（倒れ込み未補正）で屋上中央やや西の小さい四角。大きさは目視"},
    {"type": "box", "name": "西角の階段塔（無窓・塊より高い）", "material": "tile",
     "polygon": rect_on("NW_s", 15.5, 22.1, 0.0, 6.5),
     "z0": 0.0, "z1": est(parapet + 1.8, 0.8, EV_WC, "写真 10〜11 で 5 階の塊より 1 階分弱高い。目視")},
    {"type": "box", "name": "北端の階段塔", "material": "tile",
     "polygon": rect_on("NE2", 0.0, 8.5, 0.0, 5.5),
     "z0": 0.0, "z1": est(18.5, 1.0, EV_ST, "翼より 1 m 高い。写真 19〜22 の目視")},
    {"type": "balcony", "name": "階段塔の踊り場 1", "face": "NE2", "along0": 1.0, "along1": 6.5, "z": est(4.6, 0.4, EV_ST, "階段塔の踊り場。翼の階高 3.8 の半分ずれ。目視"),
     "depth": est(2.2, 0.4, EV_ST, "目視"), "parapet_h": est(1.2, 0.1, EV_ST, "煉瓦の手すり壁。目視"), "material": "tile"},
    {"type": "balcony", "name": "階段塔の踊り場 2", "face": "NE2", "along0": 1.0, "along1": 6.5, "z": est(8.4, 0.4, EV_ST, "同上"),
     "depth": est(2.2, 0.4, EV_ST, "目視"), "parapet_h": est(1.2, 0.1, EV_ST, "同上"), "material": "tile"},
    {"type": "balcony", "name": "階段塔の踊り場 3", "face": "NE2", "along0": 1.0, "along1": 6.5, "z": est(12.2, 0.4, EV_ST, "同上"),
     "depth": est(2.2, 0.4, EV_ST, "目視"), "parapet_h": est(1.2, 0.1, EV_ST, "同上"), "material": "tile"},
    # 北東面 2 階のテラス（balcony）は faces.NE.bands の腰壁に置き換えた（2026-11-04、history）
    {"type": "ac_units", "name": "階段塔の足元の室外機", "face": "NE2", "along": 0.5, "count": 3, "w": 0.9, "h": 1.0, "d": 0.4, "gap": 0.2},
    {"type": "ac_units", "name": "北西面の室外機", "face": "NW_s", "along": 9.0, "count": 1, "w": 0.8, "h": 0.6, "d": 0.3, "gap": 0.2},
    {"type": "pipes", "name": "階段塔の縦配管", "face": "NE2", "along": 7.6, "count": 3, "radius": 0.06, "z0": 0.0, "z1": 17.0},
    {"type": "pipes", "name": "北西面の雨樋", "face": "NW_n", "along": 10.9, "count": 1, "radius": 0.07, "z0": 0.0, "z1": 17.0},
    {"type": "pavement", "name": "中庭の煉瓦舗装", "material": "brick", "z": 0.0,
     "polygon": [P[3], P[2], [6.0, -10.0], [-8.0, 16.0], P[4]]},
    {"type": "pavement", "name": "南側の広場の煉瓦舗装", "material": "brick", "z": 0.0,
     "polygon": [[-7.7, -36.5], [-0.4, -22.5], [12.0, -29.0], [4.0, -44.0]]},
]
# 塔屋の位置：航空写真で屋上中央やや西。南西面から 6〜11 m 奥、面に沿って 18〜25 m
b["parts"][1]["polygon"] = rect_on("SW", 18.0, 25.0, -6.0, 11.0)
# 木は入れない（利用者の判断：建物の完成度が先。敷地の工程で CC0 の木に置き換える）

# --- 写真と依頼 -----------------------------------------------------------------------------
from recorder.photos import load_index
for rec in load_index()["photos"]:
    b["photos"].append({"id": rec["id"], "file": rec["file"], "face": rec.get("face"),
                        "role": "全体" if rec.get("face") in ("SW", "NE", "W", "E") else "細部"})
b["requests"] = [
    {"face": "E", "what": "両端が入る正面に近い1枚", "where": "東側の道路から", "why": "東面は斜めの写真しか無く、西面と同じ割付と仮定している", "status": "open"},
    {"face": "NW_s", "what": "両端が入る1枚", "where": "西側の道路の反対側から、やや引いて", "why": "北西面は近くからの斜めの写真だけで、柱間隔と階高が目視の推定", "status": "open"},
    {"face": "NE", "what": "南東の無窓の端の壁から入隅まで全部が入る正面の写真 2〜3 枚（木を避けて少しずつ横にずらす）", "where": "道路の向こう側（中庭の北東、横断歩道の先の歩道）から",
     "why": "端の壁の幅（bays.end_margin）が P124 で 0.53 スパン以上・P130 で約 1.2 スパンと食い違い null。これで PLATEAU の辺 36.8 m と階高 4.22 m の 8% の食い違いも決着する", "status": "open"},
    {"face": "NE", "what": "南東の角を北東面と南東端面の両方が見える斜めから 1 枚", "where": "南東の角の北東側、少し離れて",
     "why": "無窓の端の壁が北東面の物か、南東端面（SE）が斜めに見えているのかが決まらない", "status": "open"},
    {"face": "NE", "what": "道路の通り抜けの中から、北東面の線上の 1 階の柱を横から 1 枚", "where": "通り抜けの道路の中、歩道側から",
     "why": "1 階の柱の本数と、道路の上 3 スパン（北東面のスパン 3〜5）が南西面のスパン 7〜9 と同じ位置かを平面で照合するため。北東面の開口（openings）は今は入れていない", "status": "open"},
    {"face": "E", "what": "生協の玄関・スロープ・黒い檻のある面の正面の 1 枚と、入隅の通路の 1 枚", "where": "中庭側から E 面に正対して",
     "why": "観察票で生協の玄関・ガラスは北東面ではなく E 面と分かった。前版で北東面に置いた 1 階のガラスを外したので、E 面に置き直す根拠が要る", "status": "open"},
    {"face": "SE", "what": "南東の端の壁の全体", "where": "東側の道路から", "why": "窓の有無が未確認（模型では無窓）", "status": "open"},
    {"face": "*", "what": "屋上が見える高い所からの1枚（タワー75 など）", "where": "", "why": "塔屋の位置と大きさ、屋上の設備が航空写真の目視だけ", "status": "open"},
]
b["history"] = [
    {"at": "2026-11-03T14:30", "by": "Claude（測量係）", "what": "南西面の写真 3 枚から初版。縮尺は PLATEAU の壁長さ、検算にタイル1段"},
    {"at": "2026-11-03T16:30", "by": "Claude（設計者）", "what": "塊 2 つ（5 階・4 階）、笠木、塔屋、階段塔 2 本と踊り場、テラス、北東・北西・東面の窓と帯、室外機、配管、舗装、木を設計として追加。目視の推定は全部 estimated と方法付き"},
    {"at": "2026-11-04T03:10", "by": "Claude（設計係, claude-opus-5-5）",
     "what": "北東面 NE を観察票・数え・測量（out/honbu/kansatsu_NE.json・kazoe_NE.json・sokuryo_NE.json）で差し替え。"
             "スパン 6 を measured（P124 の正面化と P026＋043312297 の 2 通りで一致）。柱間隔は南西面と同じ 5.52 の仮定をやめ 5.62（タイル物差し、比 1.333 が南西面より 2% 大）、柱型 0.85。"
             "5 階を小窓 2 つ/スパンに（前版は 1 つ）。2 階に煉瓦の腰壁の帯と奥のガラスの段を追加し、テラス（balcony 部品）は帯に置き換え。"
             "南東の無窓の端の壁は幅が決まらないので end_margin を unknown（前版の『余りを両端に半分ずつ』は観察と合わない）。"
             "1 階の『生協・玄関のガラス』は北東面ではなく E 面と分かったので外した。NE の根拠写真から 043259125・043304319 を外した（主題は E 面）"},
    {"at": "2026-11-04T03:10", "by": "Claude（設計係, claude-opus-5-5）",
     "what": "NE から E へ移す候補（E 面を直す根拠がまだ揃っていないので今回は E を変えず requests に回した）：生協の玄関・ガラス・スロープ・黒い檻、"
             "2 階の腰壁の帯が入隅で直角に曲がって E 面へ続くこと、写真 " + "・".join(EV_E_MOVE) + "。P236 は NE と E の入隅の通路"},
    {"at": "2026-11-04T03:05", "by": "Claude（検収係, claude-opus-5-5）",
     "what": "北東面の検収（P026・043312297 の正面化と 043312297 の視点の重ね）で直した：3・4・5 階の窓の下端を 0.16 m 下げた（開口の高さ 2.89→3.06 m、2 枚で 3.02〜3.09 m）。"
             "2 階の腰壁の帯を 4.6〜6.1 m・張り出し 2.5 m から 5.1〜7.1 m・2.9 m に（帯のタイルの大きさの倍率で高さ 1.97 m と張り出し、2 枚の視点で高さの位置を測った）。"
             "2 階のガラスの段の上端を 8.62→9.13 m（P026 で上の階の窓の上端の並びと同じ）。南東端の壁の幅は写真で読めず unknown のまま。out/honbu/kenshu_*.json"},
]

errs = validate_building(b)
print("validate:", errs or "OK")
save_building(b, Path("data/buildings/honbu.json"))
print("saved: faces", len(b["faces"]), "parts", len(b["parts"]), "blocks", len(b["blocks"]))
