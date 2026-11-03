# campus-recorder

**大学の職員がスマホで建物を撮るだけで、受験生がスマホで歩ける3Dキャンパスができる道具。**
Claude エージェントが写真から寸法を測り、根拠付きの設計図を起こし、Blender で組み立て、写真と重ねて検収し、Web 3D に追加します。

Built with Claude: Japan 2026（2026-11-02 〜 11-08）の提出物です。コードはすべて期間中に新しく書いています。

## 誰の、どんな困りごとか
- **地方の受験生**：交通費と日数の問題でオープンキャンパスに行けない。360度写真の案内では、建物どうしの距離や坂、駅からの道のりが分からない。
- **大学の広報担当**：歩ける3Dを作りたくても、業者に頼む予算も、学内に3Dの専門家もいない。

作者は名城大学天白キャンパスの歩ける3Dを、約1,500枚の写真から約2か月かけて一人で作りました。その「写真を見て、測って、形を決める」手順を、職員が使える道具にしたものです。

## 仕組み
```
職員のスマホ ──写真──▶ 作業機（Claude エージェント＋Blender）──▶ 公開 URL ──▶ 受験生のスマホ
     ▲                        │
     └── 「次はこの面の両端が入る1枚を」 ◀─┘
```
元データは**棟ごとの JSON 1つ**（`data/buildings/<id>.json`）です。図面 SVG・Blender の模型・写真への重ね合わせは、すべてそこから生成します。

- **根拠の無い寸法は書けない。** 全部の値に `measured`（根拠の写真番号あり）／`estimated`（推定と明記）／`unknown` のどれかが付き、規約に合わない JSON は検証で落ちます。
- **未確定は赤で図面に出て、撮影のお願いになる。** 「図書館の北東面は両端が写っていないので縮尺が出ません。坂の下から面全体が入る1枚をください」のように、理由付きで職員に頼みます。
- **Claude が検収する。** 写真から復元したカメラで模型を描画して写真と重ね、ずれを見て JSON を直します。

## 使い方（開発中）
```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
python -I -X utf8 -m recorder new honbu 本部棟          # 空の棟 JSON を作る
python -I -X utf8 -m recorder validate data/buildings/honbu.json
python -I -X utf8 -m recorder draw data/buildings/honbu.json   # out/honbu/ に図面 SVG
python -I -X utf8 -m recorder site build                        # 公開データから敷地の土台（OSM・航空写真・標高・PLATEAU・確認用の図）
python -I -X utf8 -m recorder site confirm honbu confirmed --by 職員A   # 職員の確認を敷地 JSON に記録
python -I -X utf8 -m recorder photos import data/photos/20261103  # 写真の EXIF を読んで索引に足す（棟は候補だけ、確定しない）
python -I -X utf8 -m recorder photos rectify data/photos/20261103/IMG_0001.jpg --roi 0,0,0.9,0.85   # 壁を正面に向け直す（根拠 JSON 付き）
python -I -X utf8 -m recorder photos measure out/rectified/IMG_0001_front.jpg --known floor_pitch=3.99  # 柱間隔・階高を数えて m に換算
python -I -X utf8 -m pytest
```

## 構成
| 場所 | 中身 |
|---|---|
| `recorder/schema.py` | 棟 JSON の形と検証（根拠の規約） |
| `recorder/drawing.py` | JSON から立面図・平面図の SVG |
| `recorder/geo.py` | 座標変換（緯度経度 ⇔ 原点からの m、国土地理院タイル） |
| `recorder/site.py` | 敷地の土台：OSM の境界と建物名、国土地理院の航空写真と 5m 標高、PLATEAU の足元と高さ、職員の確認状態 |
| `recorder/photos.py` | 写真の取り込み：EXIF（GPS・方位・焦点距離）、使えるかの材料、写っていそうな棟の候補 |
| `recorder/rectify.py` | 写真の正面化：線分 → 消失点 → 射影変換。変換行列と検算値を JSON に残す。1枚に複数の面があるときは判断役が注目範囲（ROI）を指定 |
| `recorder/measure.py` | 正面化画像から柱間隔・階高の繰り返しを数える。既知の長さが1つあれば m に換算し、出どころを残す |
| `data/site/` | 敷地 JSON（建物の名前と確認状態）と PLATEAU の抜き出し |
| `data/buildings/` | 棟ごとの JSON（唯一の元データ） |
| `docs/撮影ガイド.md` | 職員向けの撮り方 |
| `docs/日誌.md` | 作業時間の記録 |

## 使っている公開データと素材（出典）
- 国土地理院 航空写真・5m 標高（出典を表示）
- PLATEAU（国土交通省）建物・地形・土地利用。土台にだけ使い、写真で上書きします
- OpenStreetMap（ODbL）：敷地境界（relation 18057087）と建物の輪郭・名前
- 大学の公式キャンパス図：大学の許可を得て、建物の名称と有無の確認にだけ参照。形・寸法はなぞらず、リポジトリにも入れていません
- 写真はすべて作者が期間中に撮影したものです。人物・車のナンバーが写ったものは入れていません

## 作者
kou（名城大学 情報工学部 2年、[yuu737](https://github.com/yuu737)）
