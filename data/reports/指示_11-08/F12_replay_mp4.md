# F12 待機画面の「再生」を mp4 に撮る道具（11/8）

## 作った物・変えた物
- 新規 `scripts/record_replay.py`：`recorder serve` が配る `web/progress.html?building=<棟>&replay=1` を headless の Edge（`recorder.shots.headless_browser`、GPU の鍵を持つ）で開いてそのまま撮る。絵も数も作らない。
  - 枠の集め方：`Page.captureScreenshot`（JPEG）を約 10 fps で繰り返し、撮った時刻（呼ぶ前後の中ほど）を記録。最初の場面が出た時刻を 0 秒にする。
  - 書き方：OpenCV の `VideoWriter` で 30 fps。各出力枠の時刻に合わせて直前の撮った枠を複製するので、**動画の長さ＝実時間**。
    Windows では MSMF の `avc1`（H.264）、使えなければ FFMPEG の `mp4v`。MSMF の avc1 は約 40 Mbps と大きいので、ffmpeg（libx264）が PATH にあれば crf 18 で書き直して小さくする（`--no-shrink` で止める）。OpenCV の FFMPEG 側の avc1 は OpenH264 の DLL が無くて失敗するため、試さない。
  - 終わりの判定：ページの印 `window.__replay`。最後の場面の時間が過ぎると `ended: true` になり、その後 `--tail`（既定 1 秒）だけ最後の絵を残して止める。見せる物が無いページ（`empty: true`）は撮らずに止める。`--max-seconds` は安全の上限。
  - GPU の鍵は `--wait-gpu` 秒（既定 600）まで待って取り、録画の間だけ持つ。
- `web/progress.js`（3 行）：その印を足した。`show()` で `window.__replay = {scene, index, count, ended:false}`、最後の場面が終わった時に `ended:true`、生成物が無い時に `{empty:true, ended:true}`。画面の見た目は変えていない。
- `web/progress.html`（1 行）：`progress.js?v=1` → `?v=2`（古い js が残らないように）。

## 使い方
```
.venv\Scripts\python.exe -I -X utf8 scripts/record_replay.py --building <棟 id>
.venv\Scripts\python.exe -I -X utf8 scripts/record_replay.py --building <棟 id> --speed 0.4 --theme dark
```
| 引数 | 既定 | 意味 |
|---|---|---|
| `--building` | 必須 | 棟 id |
| `--out` | `out/video/replay_<棟>.mp4` | 出力 |
| `--speed` | 1 | ページの `&speed=`（2 で倍速、0.5 でゆっくり。ページの下限は 0.1） |
| `--theme` | light | `dark` で `&theme=dark` |
| `--max-seconds` | 300 | 録画の上限（秒） |
| `--base` | `http://localhost:8767` | serve の URL |
| `--size` / `--fps` / `--capture-fps` | 1600x900 / 30 / 10 | 大きさ・動画の fps・撮る速さの目安 |
| `--tail` | 1 | 終わった後に残す秒数 |
| `--wait-gpu` | 600 | GPU の鍵を待つ上限（秒） |
| `--no-shrink` | — | ffmpeg での書き直しをしない |

終わると、終わり方（最後まで行ったか／上限で打ち切りか）、再生の全長、動画の長さ・枠数・codec・撮った枠の数と平均 fps・ファイルの大きさ・場面が変わった時刻・鍵を待った秒数を表示する。

## 試しの結果（本部棟、`--max-seconds 15`、1 回だけ）
- 鍵の待ち 0.0 秒。出力は scratchpad（`out/` には書いていない）。
- 15.0 秒（実時間 15.0 秒）、451 枠（30 fps、avc1）、撮った枠 139（平均 9.2 fps）。場面 a@0.15 s → b@6.04 s → c@12.07 s（ページの 6 秒・6 秒の通り）。
- 大きさ：MSMF の avc1 のままだと 81.5 MB。libx264 crf 18 で書き直すと 1.9 MB（451 枠・30 fps のまま、h264）。書き直しは、この試しの後に足したので、同じ動画に後から当てて確かめた（write＋shrink は撮らずに作った枠でも確かめた）。この版で撮り直してはいない。
- 枠を目で見た：仕分け・正面化の場面が 1600×900 で欠けなく写っている。
- 気付き：右の記録の欄に、ページの元データにある係の失敗の文（「単価が渡されていない…」「InvokeError…」）がそのまま出る。ページをそのまま撮る決まりなので消していない。動画に出したくなければ、撮る前にページ側の扱いを指示役が決める。

## 3 分に収める `--speed` の目安
- 本部棟の再生の全長を Browser の窓で測った（`&speed=10` で回し、`window.__replay` の変化の時刻から 1 倍速に直した。GPU の鍵は使っていない）：**全長 約 70 秒（1 倍速）**、9 場面（a 0 → b 6.2 → c 12.4 → d 20.6 → e 27.6 → f 41.7 → g 48 → h 56 → i 62.2 → 終わり 70.2 秒）。
- 場面の長さの決まり（progress.js）からの上限は、9 場面すべてあって最も長い時で約 74 秒。生成物の無い場面は飛ぶので、他の棟はこれ以下。
- よって **`--speed 1` で 3 分に収まる（約 71 秒＋終わりの 1 秒）**。3 分いっぱい使うなら `--speed 0.4`（約 176 秒）。ゆっくりにするほど録画の間 GPU の鍵を長く持つことに注意（0.4 倍で約 3 分）。
- 目安の式：動画の長さ ≒ 全長 ÷ speed ＋ tail。`--max-seconds` はそれより長く（既定 300 秒で 0.25 倍まで足りる）。

## 触っていない物
`scripts/` の他のファイル、`recorder/`、棟 JSON、索引、台帳。コミットしていない。本撮りはしていない。
