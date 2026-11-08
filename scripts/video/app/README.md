# 塊 B「アプリ版」の場面（静止画）

1 場面＝**アプリの画面（本物の録画の 1 コマ）を寄りから全体へ引く → 画面が左上に縮み、右に「中の仕組み」が大きく出る**。
左下は係の札（`.claude/agents/*.md` の定義の文とモデル）、上の帯は流れの中の今の係。見た目は導入 v7 と同じ（方眼紙・黒の線・差し色の青）。

- 殻：`_shell/app.css`・`_shell/app.js`（`APP.scene({step, build(F,M), seek(F,t,o)})`。M＝仕組みの欄 1268×900）。時刻は 0〜2.4 秒 寄り→全体、1.2〜3.05 秒 注目の枠、3.0〜4.0 秒 縮む、3.7 秒〜 仕組み。
- 場面：`01_osu`〜`09_aruku`（index.html・values.json）。画面に出す数字と文は全部 values.json に出典付き。
- 準備：`python -I -X utf8 scripts/video/app/make_app.py`（係の一覧 `_shell/agents.json`、時刻表、待機画面の再生 `out/video/replay_honbu.mp4` から values の `app.replay_t` 秒の 1 コマを `out/video/app/_src/` へ）。
- 撮る：`python -I -X utf8 scripts/video/render_html.py --page scripts/video/app/<場面>/index.html --out out/video/app/<場面> --at 0 --at 2.6 --at 3.5 --at <終わり−0.2>`。動画にする時は `--at` を外して `--mp4 out/video/app/<場面>.mp4`。
- まとめて撮る：`python -I -X utf8 scripts/video/app/keys.py [場面…]`（4 枚ずつ撮り、stills/ に写して一覧を作る）。
- 出来上がり：`out/video/app/stills/`（場面ごとに a_寄り・b_全体と枠・c_縮む・d_仕組み の 4 枚、`00_一覧.png`）。
