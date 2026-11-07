# F16 写真を入れずに「③ 記録を始める」（前に送った未使用の写真で追記）

## 不具合
職員の画面の③は、この画面で写真を入れないと押せなかった。棟 JSON がある棟の追記は、職員が前に送った写真
（索引で assigned_by: staff、棟 JSON にまだ id が出ない物。`agents.find_new_photos`）で始められるのに、画面から始める道が無かった。

## 直した所（3 ファイルだけ。agents.py・out/・data/ の物は触らない）
- `recorder/serve.py`：`unused_staff_photos(root, 棟)` を足し、`GET /api/progress` の棟ごとに
  `unused_staff_photos: {n, tape, building_photos, name}` を返す（`find_new_photos` を呼ぶ。棟 JSON が無い棟は出さない。
  数えるのに失敗しても進捗は止めない）。冒頭の API の説明にも足した。
- `web/staff.html`：この画面で写真を入れていなくても、未使用の写真がある棟（係が作業中・別の窓で実行中の棟は除く）があれば
  ③を押せる。状態の文は「前に送った写真のうち未使用 n 枚（メジャー m 枚）で追記します：<棟の名前>」。
  棟の名前は web/data/site.json の designed → 棟の一覧 → serve の返り値（棟 JSON の name）の順で取る（コードに書かない）。
  候補が 2 棟以上の時は選ぶ欄（既定は先頭）を出し、押すと**選んだ 1 棟だけ** `/api/start`（max_usd は欄の値。写真フォルダ無し →
  continue_pipeline が未使用の写真を使う）→ 待機画面へ。写真を入れた時は今のまま。候補が無ければ今のまま押せない。
- `tests/test_serve.py`：2 件（未使用の数え方：頭が同じ id の取り違え無し・メジャー・assigned_by 無しは数えない・棟 JSON 無しは出さない・
  使い終われば 0／画面が unused_staff_photos を読んで 1 棟だけ起動する）。

## 確かめ
- `pytest tests/test_serve.py`：22 件通過。serve・staff・progress に関係するテスト（`-k "serve or staff or progress"`）34 件通過。
- 目視：キー無しの serve を 8768 で立て（ANTHROPIC_API_KEY を消した）、③のボタンが押せる状態（disabled でない）、
  文「前に送った写真のうち未使用 33 枚（メジャー 17 枚）で追記します：本部棟（未使用の写真がある棟は 4 棟。追記する棟を選べます）」、
  選ぶ欄を共通講義棟南に変えると「未使用 5 枚（メジャー 4 枚）」に変わることを見た。コンソールのエラー無し。**ボタンは押していない**。
  8767 の本物の serve には触っていない。8768 は止めた。

## 気づいた事
- 今の索引では 4 棟とも未使用の写真がある（本部棟 33・タワー75 76・図書館 17・共通講義棟南 5）。そのため一度に全部起動せず、1 棟を選ぶ形にした。
- 目視の間、画面の定期読み込み（GET /api/confirm）が out/<棟>/to_confirm.json を書き直す（もともとの動き。8767 の画面でも同じ）。
- 狭い幅で横にはみ出すのは既存の敷地の選ぶ欄（#campusSel）。今回の選ぶ欄は幅 100% までに抑えた。
