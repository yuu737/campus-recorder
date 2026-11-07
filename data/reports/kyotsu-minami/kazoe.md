# 数え：共通講義棟南（kyotsu-minami）

- 係：数え係 kazoe（claude-opus-5-5）、2026-11-07 09:55
- 入力：観察票 `kansatsu_<面>.json`・`kansatsu_building.json`、その面の写真（索引の 29 枚、全部 11/5・期間中）
- 出力：`kazoe_f0.json`〜`kazoe_f19.json`（20 面）。拡大の切り出しは `out/kyotsu-minami/kazoe/`（git 管理外）
- measured は 2 枚以上で突き合わせ、根拠の写真は全部、索引でその面に仕分けられている（low なし）ことを確かめた

## 面ごと
| 面 | スパン（bays） | 階（floors） | 根拠・メモ |
|---|---|---|---|
| f0 北翼の西端 | 無地 | 5 estimated（同じ塊の f19 から） | 梁 3 measured（071424391・071509670。載る所で対応） |
| f1 北翼の南面 | unknown | 5 estimated（071413956 の 1 枚） | 中庭 2 の口ごしの斜め |
| f2 中庭 2 の奥 | 1 measured | 5 measured | 071413956・071424391 |
| f3 中翼の北面 | unknown | 5 estimated（071424391 の 1 枚） | |
| f4 中翼の西端 | 無地 | 5 estimated（同じ塊の f13・f19 から） | 梁 unknown |
| f5 中翼の南面 | unknown | 5 estimated（071334183 の 1 枚） | |
| f6・f10 出っ張りの脇 | 1 estimated | 5 estimated | 2 枚で一致するが、写真の faces に面が無い（根拠の門） |
| f7・f9 出っ張りの側面 | unknown | unknown | 真横で写らない |
| f8 出っ張りの正面 | 1 measured | 5 estimated（同じ塊の f2 から） | ガラスの横桟は階段室の踊り場と読む（仮説） |
| f11 南翼の北面 | unknown | unknown（見えた 3〜4） | 解釈 5（仮説） |
| f12 南翼の西端 | 無地 | 5 estimated（同じ塊の f13 から） | 梁 3 estimated（観察票は 4） |
| f13 南翼の南面 | **8 measured** | 5 measured | 1 階の開口を 2 枚で。目印は西から 4 つ目の扉と西の壁の暗い開口。端の 2 つは segments |
| f14・f16・f18 | unknown | unknown | 写真なし |
| f15 背骨の南面 | unknown | 見えた 2 estimated | 解釈：木の陰に 3（仮説） |
| f17 背骨の東面 | 区画ごと：柱型・北 5、柱型・中 5（柱 6）measured、柱型・南 unknown | 5 measured | ガラス帯 2・南端の塊・北端の塔は segments |
| f19 北面 | unknown | 5 measured | 西部の窓の列の東寄りが木で隠れる。西端の地面すれすれの小窓 → 地下 1 層の仮説 |

## 観察票と食い違った所（disagreement）
- f1・f5：観察票「帯の段 3〜4」→ 拡大で 1 階＋凹み 4＝5（estimated）
- f6・f10：観察票「窓の段 4」→ 塀の上に 5 段目の上端がのぞく、5（estimated）
- f12 の梁：観察票 4 → 3。4 本目の端は f12 の北の斜めの線より上に浮き、奥の f4 側と読む（estimated）
- f13：観察票 null（2 階の窓 7 以上）→ 8（足し）

## 撮影依頼（主な物）
- f19 の 2・3 階の窓の列を東端から西端まで（木より建物寄りから）。西端の小窓を腰壁の上からのぞき込む 1 枚
- f17 の柱型・南を正面から、1 階の柱廊の柱を正面から
- 中庭 1・2 の中から両側の面（f1・f3・f5・f11）と奥（f6〜f10）
- 道路の向かいから西端 3 面（梁の本数）。f13 の 3〜5 階の段。f14・f15・f16・f18
- 仕分け係へ：071334183・071347810 の faces に f6・f10 を足すか（足せば f6・f10 の bays を measured にできる）
