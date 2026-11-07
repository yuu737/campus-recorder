# 設計の報告 本部棟（honbu）

設計係（sekkei・claude-opus-5-5）、2026-11-07。設計は `scripts/design_honbu.py`、出力は `data/buildings/honbu.json`、数は `sekkei_summary.json`。

- `recorder validate`：OK（`--strict` でも警告 0）。`recorder run --fast`：OK（validate・draw・model）
- 値 452 件：measured 5（数え係の柱間 f6 9・f8 6・f0 4・f4 3、階数 5）／estimated 445／unknown 2（室外機 2 か所の奥行き）。仮説は 0
- 塊：南の翼 5 階（笠木 23.15 m）・北の翼 4 階（17.75 m）・塔屋（31.0 m）。北の翼の塊から外階段の長方形（f1・f2）を除いた
- 観察票の 1 m 以上の物：置いた 46、置かなかった 7（仮設の垂れ幕・看板、棟の間の地面の物＝敷地 JSON、航空写真だけの暗い点）
- 語彙に無い形は composite で 22 個（デッキ・車道の通り抜け・ピロティ・東端の階段・歩廊など）
- requests 18：撮影 9・data 2・tool 3（敷地に置く物）・asset 1・check 3

## 未解決（requests に書いた物）
- 塔屋の位置は ±3 m（本部棟屋上と、航空写真の北の翼の屋上に落ちた影から）
- 面の前の地面：登録の決まり（各面の前の地面を z=0）と 5 m 標高が f6 で 1.35 m 食い違う
- f8 の辺：写真 38.8 m・PLATEAU 36.81 m（margin_to −1.59 m で残した）
- `out/honbu/coverage_f6_photo.png` の 1 階の西半分に東のピロティの写真が貼られている（登録のずれの疑い。検収係へ）
- f1・f2 は外階段の面なので根拠の層では unknown（灰色）のまま
