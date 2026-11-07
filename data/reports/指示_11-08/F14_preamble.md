# F14 依頼文の共通の前置きと、写真フォルダ無しの追記の「新しい写真」

担当：Opus（直す係）。範囲は `recorder/agents.py` と `tests/test_agents.py` だけ。コミットはしていない。out/・data/ の state・棟 JSON・索引は触っていない（この報告だけ書いた）。

## 1. 共通の前置き（`LIMITS_HEADER` / `limits_header`）

F13 の「【この呼び出しの上限】…8 割（N 手か X USD）…」を、指示の文そのままに置き換えた。数は lim と state から。

> 予算（手数 N・1 回の費用 X USD・棟の残り Y USD）はこれだけ。試行錯誤は同じ事を 2 回までにし、手戻りの無い順で進む。省いてよい物：言い直し、同じ確かめの繰り返し、報告の飾り。省いてはいけない物：規約 1・5（根拠の無い寸法を書かない、決めつけない）、鎖の各輪の写真 id と ±、2 つ目の物差しでの検算、測れない物の unknown と撮影依頼、台帳を道具でだけ書くこと。上限の 8 割を使ったら途中を保存し、返事の 1 行目を【続きあり】にして残りを箇条書きで終える。

- 棟の費用の上限が無い時は「棟の残り 上限なし」。
- 続きの回の文（「この工程でこれまでに使ったのは 手数 n 手・費用 x USD。この呼び出しで使えるのは上の分…」）は今のまま、前置きの後ろに続ける。
- コードの工程（`CODE_STEPS`）には付けない。`_drive` ではもともと run_agent を通らないが、`run_agent` でも名前が CODE_STEPS なら空にした（dry の `limits_header` も空）。
- 【続きあり】の検出（`_has_continue_mark`）と自動の続きは F13 のまま。

## 2. 写真フォルダ無しの追記（`continue_pipeline(photos_folder=None)`、serve の /api/start）

- `find_new_photos(棟, cwd)`：索引で building がその棟で **assigned_by が staff（職員が画面で送った）** の写真のうち、前回の棟 JSON の文字列（キーも値も、evidence・chain・requests・answered_by など全部）のどこにも id が出てこない物。
  assigned_by の無い古い写真（前回の疑似本番で撮影係・仕分けが見た物）と、assigned_by が shiwake の写真は、棟 JSON に id が無くても新しい写真にしない（指示役の指示で狭めた）。
  id が別の id の頭と同じ（例「…正面」と「…正面柱92cm」）でも取り違えないよう、長い id から先に当てる。`ruler: tape_measure` の物は `tape` に分ける。
- 始める時に state に `new_photos`（id）と `new_photos_tape`、`runs[0].new_photos`（n・tape・この棟の写真の数）を記録。
- `continue_task` は係の依頼文に「【新しい写真】写真フォルダは無い。新しい写真は n 枚（うちメジャー m 枚）。条件…」を足す。
  30 枚以下は id を並べ（メジャーは別に）、多い時は数と、`recorder photos list --building <棟>`（メジャーは `--ruler tape_measure`）で出す条件と、state の new_photos の場所を書く。
- 0 枚なら最初の工程の前で止める（reason `event`、`pending_events` に human の事件）。人が見て `recorder agent resume` すれば既知として続ける。
- photos_folder が有る時は今のまま（new_photos は None、依頼文も変わらない）。

### 本部棟の数（dry に関数だけ呼んだ。state は書いていない）

`find_new_photos("honbu")`：本部棟の写真 121 枚（assigned_by が staff 33・shiwake 12・無し 76）のうち、職員の写真 33 枚がどれも棟 JSON に出てこないので、**33 枚（うちメジャー 17 枚）**。
（狭める前の定義では 69 枚。差の 36 枚は assigned_by の無い古い写真。）30 枚を超えるので、依頼文は数と条件だけになる。

## テスト

`tests/test_agents.py` に 4 件追加（前置きの全文・続きの文・コードの工程に付かない／新しい写真の判定（職員の写真だけ、assigned_by の無い物・shiwake の物は外す）と id の頭の取り違え／依頼文に id か条件が入る・フォルダ有りは今のまま／0 枚で止まり resume で続く）。
前置きの文が変わった 3 件と、索引が要るようになった追記の 2 件を直した。
`test_agents.py`・`test_events.py`・`test_serve.py` の 70 件が通過（全体は回していない）。
