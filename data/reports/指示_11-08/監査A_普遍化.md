# 監査 A「普遍化」— 名城・天白の決め打ちの洗い出し（11/8 未明）

- 係：監査係 A（Opus 5.5）。読むだけで、直していない。
- 見た範囲：`recorder/*.py`・`scripts/*.py`・`web/*.html`・`web/look.js`・`.claude/agents/*.md`・`docs/手順_エージェント.md`・`tests/`。grep（名城・天白・tempaku・4 棟の id と名前・面 id・PXL_・原点の数字・PLATEAU・relation 番号）で拾い、`campus init → photos import → serve → pipeline → 歩く画面` をコードの上で順に追った。
- 重さの付け方：**重度**＝新しい大学で動かない、または黙って名城のデータや座標を使う。**中度**＝動くが、結果や根拠の記述がずれる、または係が迷う。**軽度**＝コメント・docstring・例示・評価用の既定値。
- 結論：hub.html・index.html の「棟へ」ボタンは敷地 JSON から作る形になっていて、直っている。残る決め打ちは次の 4 つに集まる。
  1. 敷地の取得（OSM の番号・キャッシュ・原点）がコードの中で天白に固定されている。
  2. 名城のデータがリポジトリの既定の置き場に入っていて、「新しい大学」と区別できない。
  3. 係への依頼文と共通の約束に `tempaku.json` と「4 棟」が直書きされている。
  4. 写真の名前が Pixel（`PXL_日時`）の形を前提にしている。

## 1. 決め打ちの一覧

| 番号 | ファイル:行 | 何が決め打ちか | 重さ | 一般化の案（1 行） | 直す係 |
|---|---|---|---|---|---|
| 1 | recorder/__main__.py:1112、recorder/site.py:40・54・97 | `site build` が `fetch_osm()` を引数なしで呼ぶ。このため敷地の OSM relation が天白の `18057087` に固定される。敷地 JSON の `sources.osm.relation`（campus init の案内で「ここに書け」と言っている欄）は読まれていない | 重度 | `sources.osm.relation`（無ければ `bbox_lonlat`）から取る。どちらも無ければ止めて questions に入れる | recorder |
| 2 | recorder/site.py:56・151 | OSM の生の返事のキャッシュ `data/site/cache/osm_raw.json`・`osm_ground_raw.json` が、どのキャンパスでも同じ 1 ファイル。ファイルがあれば中身を確かめずに返すので、2 校目も名城の建物・道で敷地 JSON ができる | 重度 | キャッシュ名に slug と relation 番号を入れ、中身の relation が違えば取り直す | recorder |
| 3 | recorder/site.py:98 | `osm_site` が敷地の境界を「relation」と決めつけている（`next(...type=="relation")`）。境界が way 1 本だけの大学（よくある）では StopIteration で落ちる | 重度 | way・relation・bbox のどれからでも境界を作れるようにする | recorder |
| 4 | recorder/geo.py:16-18 | 座標の原点（北緯35.1355° 東経136.9755°）と平面直角座標の系（EPSG 6675＝VII 系）がモジュールの定数になっている。`campus_init` は bbox の中心を敷地 JSON の `crs` に書くが、geo はそれを読まない。さらに `build_site`（site.py:769）が `crs` を定数で上書きする。東京だと原点から約 230 km、札幌だと約 380 km 離れた数字になる。Blender・three.js では大きな数字ほど小数の精度が落ちるので、模型が震える。系の中心から遠いため「北」も 1.5〜3° 回り、方位・太陽・面の向きがずれる | 重度 | 原点と系を敷地 JSON の `crs` から読む（geo を「敷地を渡して初期化する」形に）。系は経度から 19 系の中で選ぶ | recorder |
| 5 | recorder/site.py:772 | `build_site` が `sources.osm.relation` に天白の番号を書き込む。他校の敷地 JSON の出典が名城になる | 重度 | 実際に取った relation の番号を書く | recorder |
| 6 | recorder/site.py:1350、recorder/serve.py:117 | `campus_init` は bbox が無いと原点を天白にする。職員の画面からの立ち上げ（`/api/campus`）は bbox を渡さないので、必ず天白の原点になる | 重度 | bbox を必須にする（職員の画面で地図の範囲か住所から取る）か、site build で境界の中心に原点を決め直す | recorder / web |
| 7 | recorder/schema.py:1224・1232、recorder/skeleton.py:39 | `new_building` の既定値が `campus="meijo-tempaku"` と天白の原点。`recorder new --from-site`（skeleton）は campus を渡さないので、どの大学の棟 JSON にも「meijo-tempaku」が入る。進捗報告の見出し（report.py:358）にも出る | 重度 | `campus` を必須の引数にする。skeleton は敷地 JSON の `campus` と `crs` を写す | recorder |
| 8 | recorder/skeleton.py:98 | `from_site_cli` が `data/site/plateau.json` を必ず読みに行く。PLATEAU が無い市町村（多い）では `recorder new --from-site` が FileNotFoundError で落ち、測量係の最後の工程と設計係が止まる。OSM の輪郭を土台にする道（skeleton.py:42-48）は書いてあるのに、そこまで届かない | 重度 | plateau.json が無ければ空の一覧を from_site に渡す（OSM の輪郭で土台を作る） | recorder |
| 9 | recorder/serve.py:106-113、web/staff.html:216-219 | 職員の画面の「敷地を用意する」は、敷地 JSON が既に 1 つあれば何もしない。リポジトリには `data/site/tempaku.json` が git で入っているので、新しい大学が clone して開くと「敷地の準備は済んでいます（キャンパス tempaku）」と出て、名城の敷地のまま進む | 重度 | 名城の一式を `examples/meijo-tempaku/` に移し、既定の置き場は空で配る（番号 10 と同じ） | recorder / docs |
| 10 | 置き場の設計（`data/buildings/*.json`、`data/photos/index.json`、`data/rulers.json`、`data/daylight.json`、`data/decisions.json`、`data/site/plateau.json`、`out/site/`、site.py:1190 以降の `export_web`） | キャンパスごとに分かれているのは `data/site/<slug>.json` だけで、ほかは全部共通の置き場。しかも名城のデータが「既定の置き場」に入っている。`export_web` は `data/buildings/*.json` を全部読むので、2 校目の歩く画面に名城の 4 棟が混ざる。物差し台帳の名前（`tile_pitch_h_<棟>`）は棟 id（library など）だけで作るので他校の棟とぶつかる。航空写真と 5m 標高は上書きされる | 重度 | `data/campuses/<slug>/{site,buildings,photos,rulers,daylight,decisions}` と `out/<slug>/site/` にまとめる。少なくとも export_web は敷地の `scope.targets`／`campus` が合う棟だけ読む | recorder |
| 11 | recorder/serve.py:118-120、recorder/agents.py:32-35 | 職員の画面は「OSM・PLATEAU・標高の取得はこのあと係が進めます」と言うが、`site build` を呼ぶ工程も API も無い（PIPELINE_STEPS に敷地の工程が無い）。敷地 JSON の `buildings` が空のままなので、写真を棟に振り分けられない | 重度 | serve に `/api/site/build`（または pipeline の 0 番の工程）を足し、進行役の setup を呼ぶ | recorder / web |
| 12 | recorder/agents.py:349・363・367・371 | 係への依頼文（全係・全棟に毎回入る文）に `data/site/tempaku.json` が直書きされている。2 校目の係は無いファイルを読みに行く | 重度 | `site.campus_path(default_campus())` を差し込む | recorder |
| 13 | .claude/agents/_共通.md:9 | 共通の約束に「範囲は本部棟・図書館・タワー75・共通講義棟南の 4 棟」と固定されている。2 校目の係は、敷地の `scope.targets` と食い違う指示を読むことになる | 重度 | 「範囲は敷地 JSON の `scope.targets`」とだけ書く。名城の 4 棟は敷地 JSON の側に置く | agents |
| 14 | .claude/agents/shiwake.md:11 | 仕分け係の入力が `data/site/tempaku.json` と直書き | 重度 | 「敷地 JSON（`data/site/<campus>.json`）」にする | agents |
| 15 | recorder/photoproj.py:671-674 | `short_id`（写真の短い名前）が Pixel の `PXL_日付_時刻` の形を前提にしている。iPhone の `IMG_1234` は全部 `IMG` に、Galaxy の `20261103_123456` は全部その日付になる。Blender の視点の名前（729・734 行）と描画の読み戻し（751 行）が写真どうしで上書きされ、写真の投影（photoproj）の結果が別の写真の物になる | 重度 | 短い名前は id の末尾の英数字にし、重なったら連番を付ける。または id の hash の先頭 8 文字にする | recorder |
| 16 | recorder/photos.py:396（`pid = f.stem`） | 写真 id がファイル名そのまま。Pixel のファイル名は撮影日時なので重ならないが、iPhone の `IMG_0001` は職員・端末ごとに重なる。既にある id は「EXIF と checks だけ更新」するので、別の職員の別の写真が同じ記録に黙って混ざる | 重度 | 取り込みで重なりを見つけたら警告し、撮影日時か内容の hash を付けた id にする | recorder |
| 17 | recorder/agents.py:600-603、recorder/site.py:1393-1397 | 公式図の許可が `granted` でないと、`campus check` の問題が永久に 1 件残り、進行役の事件 `human` も毎回出る。手順書（shinko.md:27）は「許可が無ければ `status: "none"` にして OSM の名前だけで進める」と言っており、コードと手順が食い違っている。名城が許可を取れた経緯に合わせた作りになっている | 重度 | `status: "none"`（地図を使わないと決めた状態）も「解決済み」に数える | recorder |
| 18 | recorder/agents.py:566-577 | `_site_json` は `default_campus()`（文字列を返す）を dict と思い込んで捨てるので、実際には `data/site/*.json` が 1 つの時しか読まない。2 校目の敷地 JSON を足した瞬間に None になり、立ち上げの事件（scope.targets が無い・name_links が未解決）が黙って出なくなる | 中度 | `load_campus(default_campus())` を使い、複数ある時は default_campus.txt に従う | recorder |
| 19 | recorder/roof.py:599-601 | 敷地 JSON を `campus.split('-')[-1]`（"meijo-tempaku" → "tempaku"）で探している。番号 7 の既定値に合わせた応急処置。他校の slug に `-` があれば別の名前になり、名城の tempaku.json が残っていればそちらの「低い建物」で検算してしまう | 中度 | 棟 JSON の `campus` をそのまま slug に使う（番号 7 を直せば不要） | recorder |
| 20 | recorder/site.py:619、recorder/skeleton.py:59 | PLATEAU の出典が「名古屋市 2022（2021 年測量）」という固定の文字列。他市の zip を使っても、棟 JSON の `footprint.method`（根拠の欄）にそう書かれる。規約 1 の根拠の記述が事実と合わなくなる | 中度 | zip の中の CityGML の metadata か、敷地 JSON の `sources.plateau`（市・年度）から書く | recorder |
| 21 | recorder/photos.py:156 | 磁気偏角が −8.0°（名古屋付近の値）で固定。GPS・方位から棟の候補（目安）を出す所に効く。日本の中でも −5°（沖縄）〜 −10°（北海道）の幅がある | 中度 | 敷地 JSON に `declination_deg` を持つ。無ければ原点の緯度経度から国土地理院の近似式で出す | recorder |
| 22 | recorder/photos.py:295・319・1148、recorder/publish.py:44 | `PERIOD_START = 2026-11-02 12:00`（ハッカソンの開始）より前の写真は自動で `before` になり、係に見せる縮小版と公開から黙って外れる。他の大学が手持ちの写真を入れると消える | 中度 | 期間の決まりを敷地 JSON（任意の `photo_policy.period_start`）に移し、既定は「期間の決まり無し」にする | recorder |
| 23 | web/index.html:562、data/daylight.json（lat/lon） | 太陽の位置の既定値が天白の緯度経度。daylight.json も天白の値で、全キャンパス共通 | 中度 | 敷地 JSON の `crs.origin_*` を既定にする。daylight は敷地ごとに持つ | web / recorder |
| 24 | recorder/serve.py:76-81・116、web/staff.html:212 | slug は英数字だけで作る。日本語の大学名・キャンパス名（「天白キャンパス」「○○大学」）はどちらも "campus" になる | 中度 | ローマ字の slug を職員に入れてもらう欄を足す（空なら確認を求める） | web / recorder |
| 25 | recorder/agents.py:359、.claude/agents/_共通.md:9-10、.claude/agents/sokuryo.md:44 | `D:\名城大学` と期間前の写真を禁じる文が、全係・全棟の依頼文と約束に入っている。今回の大会の規則であり、他の大学には意味が無い（害は小さいが、道具の規則と大会の事情が混ざっている） | 中度 | 「持ち込み禁止の場所」を敷地 JSON（または CLAUDE.md）の設定にし、依頼文はそこから読む | agents / recorder |
| 26 | recorder/agents.py:389、.claude/agents/sekkei.md:7-8 | 設計係の形の見本が `scripts/design_honbu.py`・`design_tower75.py`。同じ定義の 8 行目は「見本の設計スクリプトは置いていない」と言っていて矛盾する。2 校目の設計係が名城の値（柱割り・階高）を写すきっかけになる | 中度 | 見本は値を抜いた雛形（`scripts/design_template.py`、全部 unknown）にする | agents / recorder |
| 27 | .claude/agents/kansatsu.md:7 | 観察票の見本が `out/tower75_観察票.json`（schema 名も `tower75-observation/1`）。out/ は git に入らないので、clone した先には無い | 中度 | 見本を `docs/見本/観察票.json`（棟名なし）に置き、schema 名を `campus-recorder/observation/1` にする | agents / docs |
| 28 | recorder/landmarks.py:37・40、recorder/locate.py:56 | 登録の合否を決めるしきい値（縁に乗る割合 0.15、勾配 0.27、カメラの高さの上限 12 m）は、本部棟の登録済み 17 枚で決めた値。タイル張りの低い棟で合わせた値なので、ガラス張りや打ち放しの棟では、誤って合格・不合格になる割合が変わる | 中度 | 値を設定ファイルに出し、新しいキャンパスでは「人が 4 点指して登録した N 枚で合わせ直す」手順を手順書に書く | recorder / docs |
| 29 | docs/手順_エージェント.md:294-300 | 「本部棟の追記を職員の画面から回す（11/8 の本番）」という当日の運用（`メジャー/本部棟_honbu`、`resume honbu`）が一般の手順書に混ざっている | 中度 | 「棟の追記を職員の画面から回す」に一般化し、本部棟は例として 1 行だけ残す | docs |
| 30 | docs/手順_エージェント.md:12 | 係の表で、仕分け係の入力が `data/site/tempaku.json` | 中度 | `data/site/<campus>.json` にする | docs |
| 31 | scripts/trial_reset.py:12・136・272・325 | 試走のリセットが `data/site/tempaku.json` を名指ししている | 中度 | `site.default_campus()` から取る | recorder |
| 32 | .claude/agents/sokuryo.md:13・36 | 縮尺の優先順が「メジャー → PLATEAU の足元の辺 → PLATEAU の高さ → JIS タイル…」。PLATEAU が無い市でも次の物差しへ進む道はあるが、手順書の例は全部 PLATEAU がある前提 | 軽度 | 「PLATEAU が無い市では OSM の辺（± を大きく）か JIS の物差しから」と 1 行足す | agents |
| 33 | web/staff.html:118 | キャンパス名の欄の placeholder がまだ「例：天白キャンパス」（大学名の欄は「例：○○大学」に直っている） | 軽度 | 「例：○○キャンパス」にする | web |
| 34 | web/staff.html:559 | メジャーの写真の取り込み例に `--face f6` | 軽度 | `--face <面id>` にする | web |
| 35 | .claude/agents/hogo.md:38-43 | 出力の例が `メジャー/本部棟_honbu` と `PXL_…` | 軽度 | `<フォルダ>`・`<写真id>` にする | agents |
| 36 | .claude/agents/shiwake.md:19、shinko.md:29、docs/手順_エージェント.md:399 | 例に `f4/f5`・「11 号館のように」が出てくる | 軽度 | 例としては残してよい（「例：」を付ける） | agents / docs |
| 37 | recorder/depth.py:469 | 奥行きの評価で、`tower75` の `f19・f20・W` だけ地面を検算する分岐がある | 軽度（評価用） | 評価の引数（棟・面の一覧）にする | recorder |
| 38 | recorder/landmarks_eval.py:128・197・267、recorder/shiwake_eval.py:3 | 実験の既定の棟が `honbu`・`tower75`、使い方の例が `--campus tempaku` | 軽度（実験用） | 既定値を外して必須の引数にする | recorder |
| 39 | recorder/schema.py:1021・1036 | 写真の索引が空の時だけ、`PXL_` で始まる id を根拠として通す | 軽度 | 索引が空なら全部「索引に無い」と警告する（Pixel だけを特別扱いしない） | recorder |
| 40 | web/look.js:23、recorder/daylight.py:54 | 太陽の式が日本標準時（東経 135°）の前提 | 軽度（国内なら正しい） | 敷地 JSON に `utc_offset` を持てば国外でも使える。今は README に「日本国内が前提」と書く | docs |
| 41 | recorder/site.py:6、geo.py:4、site_objects.py:15、evidence.py:5・84・320・451、blender_build.py:4、shots.py:9、measure.py:362・683、locate.py:4-18・438、landmarks.py:10・46・649・666、registry.py:844、parts_geom.py:436、schema.py:412、stitch.py:264、sfm.py:41、photos.py:12・333・681・724・882・920・1187・1203、publish.py:7 | docstring・コメント・help の例に、本部棟・タワー75・図書館・原点の数字・`本部棟_honbu` が出てくる | 軽度 | 例としては残してよい。ただし evidence.py:451 の座標の説明は出力（rrd の注記）に出るので、敷地の原点から作る | recorder |
| 42 | tests/test_site_objects.py:167-171 | 名城の `tempaku.json` にある通路橋 `bridge-library-tower75` を、skip 無しで読むテスト。名城のデータを外した clone では落ちる | 軽度 | ファイルが無ければ skip する、または `examples/` を読む | recorder |

件数：**重度 17 件（番号 1〜17）・中度 14 件（番号 18〜31）・軽度 11 件（番号 32〜42。番号 41 はコメント類をまとめた 1 件）**。

### 決め打ちが無かった所・直っていた所（確認済み）
- web/index.html の「<棟>へ」ボタン（439-456 行）と web/hub.html の大学名・棟の一覧（60-77 行）は、敷地 JSON（`site.json` の `scope.targets`・`designed`・`university`）から作っている。名前・座標の直書きは無い。
- 面 id は道具が足元の辺の番号（`f0`〜）として振る。コードの分岐に面 id が入っているのは、評価用の depth.py:469 だけ。
- PLATEAU の建物 id（`bldg_…`）はコードに直書きされていない（根拠 id の形を確かめる所だけ）。
- `site.default_campus()`・`campus_aliases()`・`plateau_zip()` は敷地 JSON から読む形になっている（ただし番号 1・2・4 がそこを素通りしている）。
- `scripts/design_<棟>.py` は棟ごとの設計（データの役）なので、名城の値が入っていて正しい。問題は、それを係に見本として読ませている所（番号 26）。

## 2. 新しい大学で最初から使う時の流れ（コードを読んで追った結果）

前提：大学 X の職員が今のリポジトリを clone する。git に入っている名城のデータもそのまま付いてくる。

1. **職員の画面を開く**（`recorder serve` → staff.html）：`/api/site` は `web/data/site.json`（名城の物）を読み、「敷地の準備は済んでいます（tempaku）」と出る。**ここで名城のまま進む**（番号 9）。
2. 名城の一式を手で消して、**`recorder campus init x-univ --university … --campus-name …`** を打つ：bbox を付けないと原点は天白になる（番号 6）。職員の画面から押すと、slug は "campus"、bbox 無しになる（番号 24・6）。
3. **`recorder site build --campus x-univ`**：OSM の relation 番号を読まずに天白を取る（番号 1）。キャッシュが残っていれば、取りにも行かない（番号 2）。relation を直しても、境界が way の大学では落ちる（番号 3）。座標は天白の原点・VII 系で計算され（番号 4）、出典の欄には天白の番号が書かれる（番号 5）。航空写真と標高は `out/site/` で上書きされる（番号 10）。PLATEAU の zip が無い市では `plateau.json` ができない。
4. **`recorder campus check`**：公式図の許可を取らない大学は、問題が 0 にならない（番号 17）。
5. **写真の取り込み**（`photos import` か職員の画面）：iPhone の `IMG_0001` は職員どうしで id がぶつかり、別の写真が 1 つの記録に混ざる（番号 16）。11/2 より前の写真は `before` になって係から見えなくなる（番号 22）。方位の候補は名古屋の偏角で回される（番号 21）。
6. **`serve` で記録を始める → pipeline**：係の依頼文は `data/site/tempaku.json` を読めと言い（番号 12）、共通の約束は「範囲は名城の 4 棟」と言う（番号 13・14）。測量係の最後の `recorder new --from-site` は plateau.json が無いと落ちる（番号 8）。できた棟 JSON の campus は "meijo-tempaku" になり（番号 7）、根拠の欄には「PLATEAU 名古屋市 2022」と書かれる（番号 20）。設計係は見本として名城の設計スクリプトを読む（番号 26）。
7. **写真の投影（photoproj）**：iPhone の写真は短い名前が全部 "IMG" になり、視点ごとの描画が上書きされる（番号 15）。
8. **歩く画面**（`site export-web`）：`data/buildings/*.json` を全部読むので、名城の 4 棟の模型が X 大学の敷地の座標に置かれる（番号 10）。太陽は天白の緯度で計算される（番号 23）。

## 3. 新しい大学で壊れる順（上位 10）

流れの早い段階で止まる物、黙って名城を使う物から順に並べた。

| 順 | 番号 | 何が起きるか | 一般化の案 |
|---|---|---|---|
| 1 | 9・10 | clone した時点で、名城の敷地・棟・写真の索引・物差し・太陽が「既定の置き場」に入っている。職員の画面が「準備済み」と言って名城のまま進む | 名城の一式を `examples/meijo-tempaku/` に移す。データの置き場をキャンパスごとに分ける |
| 2 | 1・2・5 | `site build` が天白の OSM を取る（番号が固定）か、キャッシュに残った天白を返す。出典の欄も天白になる | relation を敷地 JSON から読み、キャッシュをキャンパスごとに分ける |
| 3 | 4・6 | 座標の原点と平面直角座標の系が天白で固定。遠い大学では座標が数百 km になり、北が回り、模型が震える | 原点と系を敷地 JSON の `crs` から読む。系は経度から選ぶ |
| 4 | 3 | 境界が way の大学では `site build` が落ちる | way・relation・bbox のどれでも境界を作れるようにする |
| 5 | 11 | 職員の画面から敷地の取得（site build）へ進む道が無い。棟の一覧が空のままなので写真を振り分けられない | serve に敷地の取得の API か、pipeline の 0 番の工程を足す |
| 6 | 12・13・14 | 係が `tempaku.json` と「名城の 4 棟」を指示され、迷うか名城の物を読む | 依頼文と約束には、敷地 JSON の場所と `scope.targets` を差し込むだけにする |
| 7 | 16 | iPhone の写真で id がぶつかり、別の写真が 1 つの記録に混ざる（根拠の写真番号が信用できなくなる） | 取り込みで重なりを見つけ、一意の id にする |
| 8 | 8 | PLATEAU が無い市で `recorder new --from-site` が落ち、測量係・設計係が止まる | plateau.json が無ければ OSM の輪郭で土台を作る |
| 9 | 15 | iPhone・Galaxy の写真で、写真の投影（検収の材料）に別の写真の描画が使われる | 短い名前を一意にする |
| 10 | 17 | 公式図の許可を取らない大学は `campus check` が 0 にならず、進行役が毎回人を呼ぶ | `status: "none"` を解決済みに数える |

次点：番号 7（棟 JSON に "meijo-tempaku"）、20（根拠の欄に「名古屋市 2022」）、22（11/2 より前の写真が黙って消える）、18（2 校目で立ち上げの事件が黙る）。

## 4. 質問（利用者に決めてほしい事）
1. 名城のデータ（`data/site/tempaku.json`・`data/buildings/*.json`・`data/photos/index.json`・`data/rulers.json`・`data/daylight.json`）は、提出物の「成果」でもある。次のどちらにするか。
   - **`examples/meijo-tempaku/` へ移して、既定の置き場を空にする。** 道具の多くのパス（番号 10）に手が入るので、提出前の時間と相談になる。
   - **今のまま既定の置き場を名城とし、README に「2 校目は別の clone で」と書く。**
2. 「他大学を実際に立ち上げる試しはハッカソン中はしない」（手順書 403 行）の方針を続けるか。続けるなら、今夜直すのは **番号 1・2・5・12・13・14・33** に絞るのを勧める（どれも数行で済み、名城での動作は変わらない）。番号 4（原点と系）と 10（置き場）は大きいので、README の「既知の制約」に書く案。
3. 写真の id（番号 15・16）：職員が iPhone で撮る想定を README に書くか。書くなら、15・16 は普遍化というより不具合なので、先に直したい。
