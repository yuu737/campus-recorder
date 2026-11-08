# 声の設定（Google Cloud Text-to-Speech）

1. Google Cloud コンソールでプロジェクトを作る。料金ページの原文：「You must enable billing to use Text-to-Speech, and will be automatically charged if your usage exceeds the number of free characters allowed per month.」（請求先の設定が要る）
2. そのプロジェクトで「Cloud Text-to-Speech API」を有効にする。
3. 「API とサービス → 認証情報 → 認証情報を作成 → API キー」で鍵を作り、「API の制限」で Cloud Text-to-Speech API だけに絞る。
4. 鍵を `%USERPROFILE%\.secrets\google_tts_key.txt` の 1 行目に保存する（`.secrets` フォルダは自分で作る。リポジトリの外なので git に入らない）。道具は鍵の値を画面にもファイルにも出さない。
5. 無料枠（https://cloud.google.com/text-to-speech/pricing 、11/8 の原文）：「Neural2 voices … 0 to 1 million characters」、「Chirp 3: HD voices … 0 to 1 million characters」（どちらも月あたり。超えると Neural2 は US$16、Chirp 3: HD は US$30／100 万文字）。
6. 声の候補（公式の声一覧の ja-JP から、名前だけ。聞いていない）：Neural2 女 `ja-JP-Neural2-B`、Neural2 男 `ja-JP-Neural2-C`、Chirp 3 HD 女 `ja-JP-Chirp3-HD-Kore`、Chirp 3 HD 男 `ja-JP-Chirp3-HD-Charon`。
7. 鍵が入ったかの確認：`python -I -X utf8 scripts/video/tts_google.py --list`
8. 4 文を 4 声で試す（PowerShell 1 行）：`foreach ($v in "ja-JP-Neural2-B","ja-JP-Neural2-C","ja-JP-Chirp3-HD-Kore","ja-JP-Chirp3-HD-Charon") { .venv\Scripts\python -I -X utf8 scripts/video/tts_google.py --in out/video/voice_test/four.txt --out-dir out/video/voice_test/google_$v --voice $v --speed 1.0 }`

## Gemini-TTS（読み方を文で指示できる、プレビュー）
- モデル ID（公式 https://cloud.google.com/text-to-speech/docs/gemini-tts 、11/8）：`gemini-3.1-flash-tts-preview`。ja-JP は「GA」と書かれている。声の名前は Chirp 3: HD と同じ並び（Achernar 女、Charon 男 など）。
- 料金（pricing の Gemini-TTS の行の原文）：「Gemini 3.1 Flash TTS (Preview) Not available Input tokens: $1.00 per 1 million text tokens* … Output tokens: $20.00 per 1 million audio tokens*」、「* Audio tokens correspond to 25 tokens per second of audio」。**無料枠は「Not available」＝無い**。4 文（約 13 秒）なら 1 声およそ 325 音声トークン＝約 $0.0065（見積もり）。
- **鍵は別に要る**（11/8 確認）：普通の API キーで呼ぶと 403「IAM_PERMISSION_DENIED（aiplatform.endpoints.predict）」。普通の鍵には「誰の鍵か」が無く、権限を持てないため。コンソールの表示も「Agent Platform API は認証キーが必要で、標準キーと組み合わせられない」。手順：
  1. 「IAM と管理 → サービス アカウント → 作成」。名前は例 `tts-gemini`。ロールに「Vertex AI ユーザー」（roles/aiplatform.user）を付ける。
  2. 「API とサービス → 認証情報 → 認証情報を作成 → API キー」で、作る時に「サービス アカウントを通じて API 呼び出しを認証する」を選び、1 のサービスアカウントを指定。API の制限は Agent Platform API（選べれば Cloud Text-to-Speech API も）。
  3. 鍵を `%USERPROFILE%\.secrets\google_tts_gemini_key.txt` の 1 行目に保存（環境変数なら `GOOGLE_TTS_GEMINI_KEY`）。道具は `--model` の時だけこの鍵を使う。
- 使い方（PowerShell 1 行）：`foreach ($v in "Achernar","Charon") { .venv\Scripts\python -I -X utf8 scripts/video/tts_google.py --in out/video/voice_test/four.txt --out-dir out/video/voice_test/gemini_$v --voice $v --lang ja-JP --model gemini-3.1-flash-tts-preview --prompt "ニュース番組のアナウンサーのように、落ち着いて明瞭に、淡々と読んでください。" }`
- **呼ぶ窓口**（11/8 確認）：texttospeech の v1beta1 はサービスアカウント付きの鍵でも 401「API keys are not supported」。道具は `--model` の時、Agent Platform（`aiplatform.googleapis.com` の generateContent）を呼ぶ。ロールの名前は改名されていて「**Agent Platform ユーザー**」（検索欄に `aiplatform.user`）。`--speed` は効かないので、速さは指示文で頼む。プロジェクトは既定 `claudespeech`（環境変数 `GOOGLE_TTS_PROJECT` で変えられる）。
- 試し読み（4 文）：`out/video/voice_test/gemini_Achernar.wav`（14.7 秒）、`gemini_Charon.wav`（13.9 秒）。

## 決めた声（11/8 昼、利用者が試し読みを聞いて決定）
- モデル：`gemini-3.1-flash-tts-preview`（Gemini-TTS、Agent Platform 経由）
- 声：`Achernar`（女声）、言語 `ja-JP`
- 読み方の指示文：「ニュース番組のアナウンサーのように、落ち着いて明瞭に、淡々と読んでください」
- 話速：指定なし（この窓口は数値で決められない。変える時は指示文で頼む）
- 本番の作り方（PowerShell 1 行、台本は 1 行 1 文の UTF-8）：`.venv\Scripts\python -I -X utf8 scripts/video/tts_google.py --in <台本.txt> --out-dir out/video/narration --voice Achernar --lang ja-JP --model gemini-3.1-flash-tts-preview --prompt "ニュース番組のアナウンサーのように、落ち着いて明瞭に、淡々と読んでください"`
- 出る物：`out/video/narration/001.wav …` と `narration.json`（文・ファイル名・長さ秒）。映像係は `scripts/video/opening/make_timeline.py --wav-dir out/video/narration` で拍を合わせる。
