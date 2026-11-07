# F18 決定を渡してやり直す resume（redo_from・decision_file）

作った人：Opus（係）／2026-11-08。コミットはしていない。

## 何ができるようになったか
事件（stopped.reason "event"）で止まった後、進行役（Claude）が決めた事をファイルで渡し、**同じ state・同じ記録（record_name）のまま**、指定の工程から新しい会話でやり直せる。

- `agents.resume_pipeline(棟, ..., redo_from=None, decision_file=None)`
  - `redo_from`：state の `steps` の順で、その工程以降を `done` から外す。最後まで済んだ（status done）記録でもやり直せる。やり直す工程の前の会話 id は `sessions` から外し、`redo.previous_sessions` に残す（新しい会話で始まる）。
  - `decision_file`：md か txt、リポジトリからの相対（外・無い・空・別の拡張子は断る。BOM は外す）。`state.decisions` に `{at, file, text（先頭 300 字）, chars, redo_from, steps, acknowledged}` を足す。全文は `state.redo.text` に持ち、やり直す工程（redo_from が無ければ残りの工程）の係の依頼文の先頭（予算の前置きの後）に「進行役の決定（<file>）：」＋全文を入れる。コードの工程には入れない。
  - 止めた事件（stopped.events）は今まで通り acknowledged_events へ。決定がある時は pending_events のうち STOP_EVENTS か call が fable の物も acknowledged_events に移す（human・record は残す）。
  - `runs` に `{kind: "resume", ..., redo_from, decision_file}`。費用（spent_usd）・attempts・results は積み上がる。費用の上限の判定と自動の再開は今まで通り。
  - redo_from 無しで決定だけの時は、落ちた工程の同じ会話の続きはそのまま（決定は続きの依頼文の中に入る）。
- CLI：`recorder agent resume <棟> --redo-from <工程> --decision <ファイル>`（`--dry-run` で計画と依頼文だけ）。
- serve：`POST /api/resume` に任意の `redo_from`・`decision_file`。決定は `data/reports/` の下の md・txt だけ、工程は state の steps にある物だけ（違えば 400）。status done の記録も redo_from があれば受ける。待機画面の帯はそのまま。

## 確かめた事
- テスト：`tests/test_agents.py::test_resume_redo_from_with_decision`、`tests/test_serve.py::test_resume_with_redo_from_and_decision_file` を足し、agents・serve のテスト 64 件通過。
- 本部棟の state の写し（scratch）で、_invoke・コードの工程・事件の検出をモックして `redo_from="sokuryo"`＋試しの決定で回した：sokuryo・sekkei・kenshu が新しい会話（resume なし）で呼ばれ、3 つとも依頼文の前置きの直後に決定が入った。record_name は `pipeline_20261108_041333.json` のまま、spent_usd 5.45 → 6.20（0.25×3）、done は全 8 工程、runs に redo_from と decision_file。本物の `out/honbu/pipeline_state.json` は変わっていない（バイト比較）。

## 本番で使う時
1. 進行役が決定を `data/reports/指示_11-08/決定_<何か>.md` に書く。
2. `.venv\Scripts\python.exe -I -X utf8 -m recorder agent resume <棟> --redo-from sokuryo --decision data/reports/指示_11-08/決定_<何か>.md --dry-run` で依頼文を見る → `--dry-run` を外して回す（または画面から POST /api/resume）。
