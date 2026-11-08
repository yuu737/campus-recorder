# 塊 B「流れ」の場面の作り方

1. **場面を足す**：`_template/` を `scripts/video/flow/<nn>_<name>/`（例 `05_sekkei`）に写す。`index.html` の `agent`（柱で点く係、`_shell/agents.json` の id）と `build`・`seek` を書く。板の座標は 1412×1024（左上 0,0）。`t` は場面の頭からの秒で、0〜0.6 秒は右の板がめくれて入る時間。終わりは 0.4 秒止める。
2. **共通の殻** `_shell/`：`shell.css`（地・柱・色・字体。字体は `--font-ja` の 1 か所）、`shell.js`（`FLOW.scene`）。使える道具：`F.rise/pop/draw/drawDown`（出る・押す・線を引く）、`F.kb`（寄り 100→110 %）、`F.spot`＋`F.spotAt`（注目の枠と周りの暗転）、`F.countTo`（数え上げ）、`F.cursor(track)`（偽のカーソル。`[[t,x,y],[t,x,y,"click"]]`、クリックで波紋）、`F.headline`、`F.caption`（右下の出典）、`F.S(n)`（文 n の時刻）。`seek` が Promise を返すと撮る前に待つ（連番の画像を差し替える時）。
3. **values.json**：画面に出す数字と文は全部ここに置き、値ごとに `source`（出典のファイル名と欄）を書く。記録に無い物は入れない。`min_dur`＝場面の最短秒、`cap`＝右下の出典の行。写真は `data/photos/public/` だけ（`R+"data/photos/public/…"`）。
4. **narration.txt**：1 行 1 文の暫定の文（利用者が差し替える）。
5. **時刻表**：`python -I -X utf8 scripts/video/flow/make_flow.py [<nn>_<name> …]` → 各場面の `timeline.json`（声なしは 1 秒 3.6 文字）と `_shell/agents.json`（`.claude/agents/*.md` の model から。測量 Opus、進行役 Fable など）。声ができたら `--wav-dir <001.wav… の所>`。
6. **撮る**：確認は `python -I -X utf8 scripts/video/render_html.py --page scripts/video/flow/<場面>/index.html --out out/video/flow/<場面> --stills 1`（1 秒おきと montage.png）。本番は `--stills` を外して `--mp4 out/video/flow/<場面>.mp4`。Edge は `--disable-gpu`（GPU の鍵は取らない）。
7. **繋ぐ**：`python -I -X utf8 scripts/video/join.py --dir out/video/flow --out out/video/flow_B.mp4`（名前の順。音声なしでも、音声付きが混ざっても繋げる）。
8. **職員の画面を撮る**：`scripts/video/capture_staff.py`。必ず**複製の作業フォルダ**で `recorder serve` を、**他と重ならないポート**（例 8791）で立て、`netstat` でそのポートが自分の serve だけかを確かめてから `--url http://127.0.0.1:<port>/web/staff.html`。8767 は他の会話の serve が本物の data/ で動いている事がある（11/8 にこれで本物の索引に 4 枚書いてしまい、元に戻した）。
9. 確かめる事：1 秒おきの静止画で、字のはみ出し・重なり、1 秒以上止まる所、数字が values.json と同じか。
