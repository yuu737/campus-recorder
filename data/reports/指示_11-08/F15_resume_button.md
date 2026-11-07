# F15 画面から「続きから」（11/8）

## 何をしたか
職員の画面から本番（API）を起動するので、止まった時（自動の再開 2 回の後の 3 回目・費用の上限・人を呼ぶ事件）も
画面から続けられるようにした。中身は CLI の `recorder agent resume <棟> [--max-usd N]` と同じ `agents.resume_pipeline` を呼ぶだけ。

## 変えた物
- `recorder/serve.py`
  - `POST /api/resume {"building", "max_usd"?}`：/api/start と同じ RUNS・別スレッド（`_worker` の mode `resume`）で `agents.resume_pipeline` を呼ぶ。
  - 断る時（409、理由の文と code）：このサーバで作業中（busy、/api/start と同じ）・キーが無い（no_key。続きは dry-run だと何も書かないので試しにも落とさない）・
    状態ファイルが無い（no_state）・最後まで済んでいる（done）・止まっていない（not_stopped）・費用の上限で止まったのに使った額より大きい上限が無い（need_max_usd）。
  - 「実行中」のまま残った状態は、このサーバの係のスレッドが error／stopped の時だけ「落ちた」と見て続ける。
    それ以外は別の窓（CLI）で動いているかもしれないので画面からは続けない（CLI の resume を案内）。
  - `resume_check`：止まっているか・画面から続けられるかを `/api/progress`（状態ファイルがある棟だけ `resume`）と `/api/pipeline`（`resume`）に出す。
  - 費用の上限で止まった時だけでなく、使った額が上限以上の時も上限の欄を求める（そのまま続けても直後に止まるため）。
  - 上限なし（0）は resume_pipeline の作りで表せないので、使った額より大きい数だけを受ける。
- `web/progress.js`（`?v=4`）・`web/progress.html`：生きている時の「止まっています」の帯に「続きから」ボタン。費用の上限で止まった時だけ上限（USD）の欄。
  押すと /api/resume、成功したら読み直して生きている表示に戻る（係のスレッドが running なら、状態ファイルがまだ stopped でも止まった表示にしない）。
  1 秒ごとの描き直しで打ちかけの欄を消さない。再生では出さない。API の無いサーバでは今まで通り CLI の案内。
- `web/staff.html`：進捗の欄の止まった棟だけ「止まった理由」と「続きから」（費用の上限なら上限の欄）。キーが無い時はボタンを押せず理由を出す。
  CLI だけで動いた棟も、状態ファイルが止まっていれば出る。「画面でまだできない事」の文から「止まった係の続き」を外した。staff の js は html の中にあり `?v=` は無い。
- `tests/test_serve.py`：5 件（キー無しで断る・resume_pipeline が別スレッドで呼ばれ dry_run にしない・作業中は resume も start も 409・
  状態無し／済み／実行中の記録を断る・費用の上限の後は使った額より大きい上限が要り、それが渡る）。

## 確かめた事
- `tests/test_serve.py` ほか serve に関係する 5 ファイル 49 件通過（`tests/test_agents.py` は別の係が直している最中なので回していない）。
- 目視：scratch に root を作った偽の serve（8768、resume_pipeline は偽物）で、待機画面の帯（上限の欄・使った額以下の拒否・押した後に「3/3 … が動いています」と上限 10 USD に戻る）、
  再生で帯が出ない事、職員の画面で止まった棟だけにボタンが出て押すと「係が作業中」に変わる事を見た。サーバは止めた。out/・data/ は触っていない。
