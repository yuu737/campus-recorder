# 塊 B「アプリ版」2 の場面（11/8 夕方の演出の決まり）

1 場面＝**アプリの画面（1 秒の文脈、めくれで入る）→ 注目部分の切り出しが画面の 60 % 以上に寄る → 右に「Claude の声」（係が本当に書いた文）を打ち出す → 結果の数字・生成物が出る → 0.4 秒止める**。
見た目は導入 v7 と同じ（方眼紙・黒の線・差し色 #0031d8・BIZ UDPGothic・板の影）。本文の字は 28 px 以上、出典の行だけ 20 px。同時に出す文字の塊は 6 個まで。

## 11/8 夕方の直し（利用者の判定：切り出しだけだとプレゼン資料に見える）
- **全画面の小窓**に戻した：values.app に `mini:{z0:1.5, focus:[x,y], spot:[x,y,w,h], spot_at:.5, t_shrink:1.2, d_shrink:1.2, w:520, x:60, y:206, link_to:[620,260], link_t:2.3}` を書くと、殻が「生成物の所に寄せた全画面（z0 倍）→ 0〜1.2 秒で全体へ引く（spot に枠と暗転）→ 1.2〜2.4 秒で左上の小窓（幅 520）に縮む → spot から link_to へ青い線」をする。本文（本物の生成物の切り出し・声・数字）は小窓の右（x 620〜1862）と下（y 530〜）に置く。見本は 04_sokuryo。shots（切り出しの小窓）は mini が無い時だけ。
- 時間の線：線の右端を時刻の文字の左 40 px までにした（つまみと文字が重ならない）。

## 動きの部品（11/8 夕方、再生の見せ方を塊 B の芯に）
- 小窓を動画に：`python -I -X utf8 scripts/video/app2/cut_replay.py <場面> <始め秒> <終わり秒>`（replay_honbu.mp4 → `out/video/app2/_src/replay_<場面>/f0001.png…` 10 fps）。表示された `"video":{dir,fps,n}` を values.app に書くと、小窓（mini）が t で連番を差し替えて流れる（`t0` で遅らせられる）。職員の画面は従来どおり `app.seq`。
- `F.rectify({img, w, h, quad:[[x,y]×4], rect:[x,y,w,h], x, y, scale, t0, dur})`：写真の 4 点（記録の正面化の四角形、画像の px）が長方形へ matrix3d で起き上がる。途中は白い格子。quad と rect は記録の値を values.json に出典付きで。
- `F.puzzle({base, x, y, w, h, img, imgw, imgh, patches:[{poly:[[x,y]…]}], t0, step})`：面の絵（例 `out/honbu/coverage_f0_photo.png`、40 px/m）を写真ごとの使える範囲（`out/honbu/coverage.json` の faces[].photos[].polygon、m → px は x＝s×40・y＝画像の高さ−z×40）で切った欠片が order の順に落ちて埋まる。**f6 の coverage の絵には学生名の懸垂幕が写るので使わない**（見本は 03_shiwake の f0）。
- `await F.drawSvg({url, x, y, w, t0, dur})`：立面図 SVG（`out/honbu/elev_f*.svg`）の線を stroke-dash で順に描き、最後に字を出す。build を `async build(F,S){…}` にして await する。
- 既存の `F.claudeVoice`・`F.tag`・`F.link` はそのまま。

## 殻と部品（`_shell/app.js`・`_shell/app.css`。触るのは係 S だけ。旧版は `_shell/old_app.*`）
- 場面：`APP.scene({step:"<係の id>", build(F,S){…}, seek(F,t,o){…}})`。S＝舞台 1920×1080。右の欄は `F.RIGHT`（x 1290・幅 572）、切り出しの箱は `F.AREA`（x 60・y 206・1190×820）。
- アプリの画面：values.json の `app` = `{w,h, src | seq:{pattern:"out/…/f{n}.jpg",pad:5,keys:[[秒,コマ],…]}, url, spot_at, shots:[{t:1.0,d:1.4,crop:[x,y,w,h]}, {t:4,cut:true,crop:[…]}]}`。殻が 0〜0.6 秒めくれ→spot_at から枠と暗転→shots へ寄る。`F.toStage(画像x,画像y)`＝今の寄りでの舞台の点。`F.lay`＝画像の座標で物を置く層。
- **Claude の声**：`F.claudeVoice({who:"検収係", model:"Claude Opus", took:"18:47", text:"<記録の実文>", src:"<ファイル名>", t0:2.7, ms:65, x,y,w})`。t0 から 1 字ずつ、3 行まで（超えると window.WARN）。返り値 `.end`＝打ち終わりの秒。who・model・took・text は記録の実値だけ（values.json に出典）。
- **札**：`F.tag("claude"|"code"|"human", "面の判定は Claude が決めた", {x,y | at:[画像x,画像y],dx,dy, t0})`。青＝Claude が決めた、黒＝コードが測った、白＝人。
- **小窓 → 青い線 → 中の仕組み（全場面で残す）**：小窓は全画面を縮めた物ではなく、**線の起点になる本物の要素が読める大きさの切り出し**（待機画面の右の欄の係の一言・写真の並び・立面図の寸法線など）。values.json の `app.shots` にその切り出し（小さく置くなら `area:{x,y,w,h}`、寄らずに替えるなら `cut:true`）と、`app.anchor=[画像x,y,w,h]`・`app.anchor_t`（秒）を書くだけで、殻が起点を青枠で囲み、そこから最初の Claude の声の吹き出しへ線を引く。声の板は「小窓の中のその一言を拡大して打ち出す」位置（小窓の右）に置く（見本 02_satsuei：小窓 x60・幅 680、声 x820・幅 1042・size 42）。声が無い場面や別の板へ引く時は `F.link({from:[画像x,y,w,h], to:<要素|声|[x,y]>, t0})`（別名 `F.focusWindow`）。
- **内部の番号は本文に出さない**：決定の K-401、依頼の番号、results[0] の類は出典の行（20 px）にだけ書く。本文・声・札には出さない（声の文に番号が入っている時は、番号の無い文を記録から選ぶ）。
- 波紋：`F.ripple(画像x, 画像y, t0)`。板：`F.board("見出し", x,y,w,h, html)`。動き：`F.rise / F.pop / F.draw / F.countTo / F.pulse`。
- **時間の線**（帯の下）：values.json の `clock` = `{from:"2026-11-08T04:13:33", at:"2026-11-08T04:15:16"}`（任意で `state`）。記録 `data/reports/honbu/pipeline_state_20261108_final.json` の最初の始まり〜最後の終わり（04:13→12:16）の上を 0.2〜1.4 秒で from→at に伸び、時刻と費用（at までに終わった工程の cost_usd の和 / 上限 40 USD）を出す。目盛り＝各工程の始まり（青＝Claude、灰＝コード）。
- 時刻表：`<場面>/timeline.json` の total（`python -I -X utf8 scripts/video/app2/make_app.py <場面>` が values の dur から作る）。
- 撮る：`python -I -X utf8 scripts/video/render_html.py --page scripts/video/app2/<場面>/index.html --out out/video/app2/<場面> --stills 1`（1 秒おき＋montage）。動画は `--mp4 out/video/app2/<場面>.mp4`。
- 見本：`01_osu`（職員、声なし・コマ送り）、`02_satsuei`（声・札・数え上げ）、`03_shiwake`（切り出しから写真が箱へ飛ぶ）。
