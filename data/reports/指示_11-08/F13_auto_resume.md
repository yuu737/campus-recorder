# F13 自動の続き（起動 1 回で最後まで）

担当：Opus（直す係）。範囲は `recorder/agents.py` と `tests/test_agents.py` だけ。コミットはしていない。out/・data/ の state・棟 JSON・索引・台帳は触っていない。

## 変えたこと

1. **自動の続き**（`_drive` → `_auto_resume_loop`）
   - 係の工程が「落ちた（例外。max turns の ResultError を含む）」「手数の上限」「1 回の費用の上限（SDK の max_budget）」
     「429・混雑（run_agent の RETRY_MAX 回のやり直しの後）」「係が【続きあり】で区切った」で止まった時、
     人の手を待たずに**同じ会話（session_id）の続きから**自動で再開する。工程ごと・1 回の実行で **2 回まで**（`AUTO_RESUME_MAX`）。
   - 429 で止まった時は続ける前に 480 秒待つ（`AUTO_RESUME_WAIT_S`）。
   - 3 回目に止まったら今まで通り止めて state の `stopped` に書き（`auto_resumes: 2`、理由の並びを why に）、
     `pending_events` に **human（人を呼ぶ）** の事件を足す。
   - 棟の費用の上限（`max_usd`・`spent_usd`）に届いていたら自動で続けず `max_usd` で止める。続ける時も 1 回の上限は棟の残りで小さくなる（今の判定のまま）。
2. **費用を二重に数えない**
   - 1 回分ずつ `calls` に入れ、`spent_usd` には工程の記録ごとに 1 回だけ足す（今まで通り）。
   - 同梱の CLI は resume の時に会話の費用の合計を書き起こしから戻す（`costLedger.restore`）ので、続きの呼び出しの
     `total_cost_usd` は会話の合計になる。会話ごとの合計を `state.session_costs` に覚え、続きの時は**差を 1 回分**にする
     （元の値は `cost_usd_sdk_total`）。報告が前の合計より小さい（戻されなかった）時はそのまま 1 回分。
   - トークン数（usage）は 1 回分と見ている（未確認。単価表で見積もる時に効く）。
3. **記録**：state と `pipeline_*.json` に `auto_resumes`（工程ごとに n・理由 kind［max_turns・max_budget・rate_limit・crash・partial］・
   日本語の label・why・会話 id・待った秒・結果）と `auto_resumes_total`。工程の記録には `auto_resume: n`。README に「自動で n 回続けた」と書ける。
4. **手数の上限**：`AGENT_LIMITS`（係ごとの上書き）で測量・設計・検収を 30 → **60**。他の係は今のまま（`agent_limits()`）。
5. **依頼文に上限を明記**（利用者の案）：毎回の依頼文の先頭に `【この呼び出しの上限】手数は N 手まで。費用は X USD まで（棟の残り Y USD）。`
   と「上限の 8 割を使ったら途中の結果をファイルに保存し、返事の 1 行目を【続きあり】にして残りを箇条書きで終える（自動の続きはその会話から）」。
   続きの依頼文には「この工程でこれまでに使った手数・費用」も入る。値は lim と state から。棟の名前は入れない。
   - 付け足し：係が 8 割で自分で区切ると SDK は「成功」で返すので、そのままだと工程が済んだ扱いになる。返事の 1 行目の【続きあり】を見て
     理由 `partial` として自動の続きに回すようにした（これも 2 回までに数える）。

## serve（職員の画面）

`recorder/serve.py` の `/api/start` → `api_start` → `_worker` は、棟 JSON があれば `agents.continue_pipeline`、無ければ `agents.run_pipeline` を呼ぶ。
どちらも `_drive` を通るので、自動の続きは画面からの実行にも効く。
ただし serve には **resume の入口が無い**（3 回目で止まった後に続けるのは CLI の `recorder agent resume` だけ）。直すかは指示役が決める。

## テスト

`tests/test_agents.py` に 5 件追加（係ごとの手数、手数の上限 → 自動で続き → 通る＋費用が二重にならない、3 回止まると人を呼ぶ事件、
棟の上限では続けない、依頼文に上限が入る＋【続きあり】で続く）。今の振る舞いが変わった 4 件（止まるまでの回数・依頼文の先頭）を直した。
`test_agents.py`・`test_serve.py`・`test_events.py` の 61 件が通過（全体は回していない）。
