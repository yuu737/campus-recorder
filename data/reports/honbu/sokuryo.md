# 本部棟（honbu）測量の報告

測量係（sokuryo・claude-opus-5-5）、2026-11-07 10:53。JSON は `data/reports/honbu/sokuryo_<面>.json`。

- メジャーの写真：無い（`photos list --building honbu --ruler tape_measure` が 0 件）。縮尺は全部 estimated。撮影依頼を 1 件（f6 の柱型にメジャー）出した。
- タイルは小口（目地の周期の比 1.70〜1.73 を 6 枚で確かめた）。台帳 tile_course 0.070 m を 2 本目の物差しに使った。

| 面 | 階高 | 柱間隔 | 物差し | 検算（2 本目） | 登録 | 面の写真 |
|---|---|---|---|---|---|---|
| f6 | 4.130 ±0.14 m | 5.418 ±0.19 m | plateau_wall（135.96 px/m） | tile_course（差 1.6%） | 13 枚 | out/honbu/coverage_f6_photo.png |
| f8 | 4.130 ±0.16 m | 5.408 ±0.25 m | f6 から移した縮尺（f6 は plateau_wall）（141.53 px/m） | tile_course（差 1.1%） | 2 枚 | out/honbu/coverage_f8_photo.png |
| f4 | 3.938 ±0.14 m | 6.145 ±0.22 m | plateau_wall（174.1 px/m） | tile_course（差 0.06%） | 3 枚 | out/honbu/coverage_f4_photo.png |
| f0 | unknown | 6.116 ±0.50 m | tile_course（145.0 px/m） | f4 の PLATEAU の辺で測ったタイル 1 段（差 0.0%） | 2 枚 | out/honbu/coverage_f0_photo.png |
| f5 | 4.189 ±0.31 m | 対象外 | tile_course（114.1 px/m） | f6 の PLATEAU の辺で測ったタイル 1 段（差 -1.6%） | 5 枚 | out/honbu/coverage_f5_photo.png |
| f7 | 4.245 ±0.45 m | 対象外 | tile_course（235.0 px/m） | f6 の PLATEAU の辺で測ったタイル 1 段（差 -1.6%） | 0 枚 | out/honbu/coverage_f7_photo.png |

## 食い違い（足元は動かしていない）
- f8：PLATEAU の辺 36.81 m、写真は 7.18 柱間 ≒ 38.8 m（+5.5%）→ 物差しにせず、f6 から縮尺を移した。
- f0：PLATEAU の辺 30.78 m、写真は 4.63 柱間 ≒ 28.3 m（−8%）。外階段まで含む辺の見込み → タイルを物差しにした（chain で登録された 043259125 でも北東の角は from から 28.15 m）。
- f7：写真の角から角 17.2 m（+8.6%）だが超広角の端で信用できない。f7 に登録した写真は無い（地面が写っていない）。coverage_f7_photo.png は他の面の写真の端に斜めに写った分だけ（被覆 10%）。
- 高さ：f6 西端の笠木は地面から 23.15 m。PLATEAU の高さ 32.1 m と差 8.95 m（未解決。合わせに行っていない）。

## 測っていない面
- f1・f2・f3（外階段と北端の短い壁）：柱割りが無く、まとまった正面の写真も無い。登録も無い。

## 登録の原点（設計係へ）
- 横の原点は各面の from の角の縁（--lines の x_origin_px）。高さの原点は面の前の地面で、--lines の z_named（パラペット上端）に「地面 − 32.1 m × 縮尺」を入れて与えた（土台のパラペット上端 32.1 m は PLATEAU の最高高さで、写真の上端と合わないため）。
- f8 の地面は写真で読めないので、f6 の西端の地面と同じ高さと置いた（仮定。sokuryo_f8.json の height_origin_note）。

## 登録
- 手で登録 8 枚（compare → register）、register-chain で 18 枚。honbu の登録は 26 枚、未登録 62 枚（理由は各 JSON の not_registered）。
- 測っていない面 f1・f3 は登録 0。f2 は姿勢で登録された写真 1 枚から 97.6% 貼られている。
