"""Google Cloud Text-to-Speech（REST、API キー）で、1 行 1 文のテキストを文ごとの wav にする。
tts_aivis.py と同じ入出力。標準ライブラリだけで動く（SDK は使わない）。

使い方:
    python -I -X utf8 scripts/video/tts_google.py --in 文.txt --out-dir out/video/narration \
        [--voice ja-JP-Neural2-B] [--speed 1.0]
    python -I -X utf8 scripts/video/tts_google.py --list      … ja-JP の声の一覧

鍵（API キー）の読み方（この順）:
    1. 環境変数 GOOGLE_TTS_API_KEY
    2. %USERPROFILE%\\.secrets\\google_tts_key.txt の 1 行目
    --model（Gemini-TTS）の時は別の鍵: 環境変数 GOOGLE_TTS_GEMINI_KEY、無ければ
    %USERPROFILE%\\.secrets\\google_tts_gemini_key.txt（サービスアカウントに結び付けた鍵）
    鍵の値はログ・JSON・標準出力に出さない。送る時も URL ではなく x-goog-api-key ヘッダに入れる。

API（2026-11-08 に公式ドキュメントで確認）:
    POST https://texttospeech.googleapis.com/v1/text:synthesize
        {input: {text}, voice: {languageCode, name}, audioConfig: {audioEncoding: LINEAR16,
         speakingRate (0.25〜2.0), sampleRateHertz}}
        → {audioContent: base64}。LINEAR16 は WAV ヘッダ付きで返る。
    GET  https://texttospeech.googleapis.com/v1/voices?languageCode=ja-JP
        → {voices: [{languageCodes, name, ssmlGender, naturalSampleRateHertz}]}
    API キーは x-goog-api-key ヘッダで渡す（docs/authentication/api-keys-use）。

Gemini-TTS（読み方を文で指示できるモデル、--model を付けた時だけ）:
    POST https://aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/global/publishers/google/models/{model}:generateContent（11/8 に v1beta1 から切替。texttospeech は API キーを受け付けない）
        contents: 「指示文：文」、generationConfig: {responseModalities: [AUDIO], speechConfig: {languageCode, voiceConfig.prebuiltVoiceConfig.voiceName（例 Achernar）}}。返りは audio/l16 の生 PCM（WAV の頭を付ける）。鍵はサービスアカウント付き
    モデル ID（公式 docs/gemini-tts、11/8 確認）: gemini-3.1-flash-tts-preview、gemini-2.5-flash-tts、
        gemini-2.5-flash-lite-preview-tts、gemini-2.5-pro-tts
    料金（公式 pricing、11/8）: Gemini-TTS は無料枠「Not available」。3.1 Flash TTS (Preview) は
        入力 $1.00／100 万テキストトークン、出力 $20.00／100 万音声トークン（音声 1 秒＝25 トークン）。
    例: --model gemini-3.1-flash-tts-preview --voice Achernar --prompt "ニュース番組のアナウンサーのように…"
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

API = "https://texttospeech.googleapis.com/v1"
AIP = "https://aiplatform.googleapis.com/v1"
PROJECT = os.environ.get("GOOGLE_TTS_PROJECT", "claudespeech")
SAMPLE_RATE = 24000
KEY_ENV = "GOOGLE_TTS_API_KEY"


GEMINI_KEY_ENV = "GOOGLE_TTS_GEMINI_KEY"


def key_file(gemini: bool = False) -> Path:
    home = os.environ.get("USERPROFILE") or str(Path.home())
    name = "google_tts_gemini_key.txt" if gemini else "google_tts_key.txt"
    return Path(home) / ".secrets" / name


def load_key(gemini: bool = False) -> str | None:
    """gemini=True は Gemini-TTS 用の 2 本目の鍵（サービスアカウントに結び付けた鍵）。
    普通の API キーには「誰か」が無いので aiplatform.endpoints.predict の権限を持てず 403 になる（11/8 確認）。"""
    k = os.environ.get(GEMINI_KEY_ENV if gemini else KEY_ENV, "").strip()
    if k:
        return k
    f = key_file(gemini)
    try:
        lines = f.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return None
    return lines[0].strip() if lines and lines[0].strip() else None


def _call(key: str, url: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="GET" if body is None else "POST")
    req.add_header("x-goog-api-key", key)
    if body is not None:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace").replace(key, "<鍵>")[:800]
        raise RuntimeError(f"Google TTS がエラーを返しました（{e.code}）: {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Google TTS につながりません: {e.reason}") from None


def list_voices(key: str, lang: str) -> list[dict]:
    q = urllib.parse.urlencode({"languageCode": lang})
    return _call(key, f"{API}/voices?{q}").get("voices", [])


def synth(key: str, text: str, voice: str, speed: float, lang: str | None = None,
          model: str | None = None, prompt: str | None = None) -> bytes:
    if not lang:
        parts = voice.split("-")
        lang = "-".join(parts[:2]) if len(parts) >= 3 else "ja-JP"
    if model:
        return synth_gemini(key, text, voice, lang, model, prompt)
    inp: dict = {"text": text}
    vsel: dict = {"languageCode": lang, "name": voice}
    base = API
    body = {
        "input": inp,
        "voice": vsel,
        "audioConfig": {"audioEncoding": "LINEAR16", "speakingRate": speed,
                        "sampleRateHertz": SAMPLE_RATE},
    }
    res = _call(key, f"{base}/text:synthesize", body)
    if "audioContent" not in res:
        raise RuntimeError("応答に audioContent がありません")
    return base64.b64decode(res["audioContent"])


def synth_gemini(key: str, text: str, voice: str, lang: str, model: str, prompt: str | None) -> bytes:
    """Gemini-TTS を Agent Platform（aiplatform）の generateContent で呼ぶ。
    texttospeech の v1beta1 は API キーを受け付けず 401、aiplatform はサービスアカウント付きの鍵を受け付ける（11/8 確認）。
    返りは audio/l16（生の PCM、24000 Hz、モノラル）なので WAV の頭を付ける。話速の指定は無く、指示文で頼む。"""
    url = f"{AIP}/projects/{PROJECT}/locations/global/publishers/google/models/{model}:generateContent"
    said = f"{prompt}：{text}" if prompt else text
    body = {"contents": [{"role": "user", "parts": [{"text": said}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"languageCode": lang,
                                                  "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
    res = _call(key, url, body)
    try:
        part = res["candidates"][0]["content"]["parts"][0]["inlineData"]
    except (KeyError, IndexError):
        raise RuntimeError("応答に音声がありません") from None
    rate = SAMPLE_RATE
    for kv in part.get("mimeType", "").split(";"):
        k, _, v = kv.strip().partition("=")
        if k == "rate" and v.isdigit():
            rate = int(v)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(base64.b64decode(part["data"]))
    return buf.getvalue()


def wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / float(w.getframerate())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Google Cloud Text-to-Speech で 1 行 1 文を文ごとの wav（LINEAR16・24000 Hz）にする。"
                    f"鍵は環境変数 {KEY_ENV}、無ければ %%USERPROFILE%%\\.secrets\\google_tts_key.txt の 1 行目")
    ap.add_argument("--in", dest="src", help="1 行 1 文のテキスト（UTF-8）。空行は飛ばす")
    ap.add_argument("--out-dir", help="wav と narration.json の出力先")
    ap.add_argument("--voice", default="ja-JP-Neural2-B", help="声の名前（既定 ja-JP-Neural2-B）")
    ap.add_argument("--speed", type=float, default=1.0, help="話速 speakingRate（0.25〜2.0、既定 1.0）")
    ap.add_argument("--model", default=None,
                    help="Gemini-TTS のモデル ID（例 gemini-3.1-flash-tts-preview）。付けた時だけ Agent Platform の generateContent を呼ぶ。無料枠なし。--speed は効かない（指示文で頼む）")
    ap.add_argument("--prompt", default=None, help="読み方の指示文（--model の時だけ使う）")
    ap.add_argument("--lang", default=None, help="言語コード（既定は声の名前から、取れなければ ja-JP）")
    ap.add_argument("--list", action="store_true", help="ja-JP の声の一覧を出して終わる")
    a = ap.parse_args(argv)

    if not a.list and (not a.src or not a.out_dir):
        ap.error("--in と --out-dir が要ります（一覧だけなら --list）")
    if a.prompt and not a.model:
        ap.error("--prompt は --model（Gemini-TTS）の時だけ使えます")
    if not 0.25 <= a.speed <= 2.0:
        ap.error("--speed は 0.25〜2.0")

    gem = bool(a.model)
    key = load_key(gem)
    if not key:
        env = GEMINI_KEY_ENV if gem else KEY_ENV
        print(f"鍵がありません（環境変数 {env} も {key_file(gem)} も見つからないか空です）", file=sys.stderr)
        if gem:
            print("Gemini-TTS はサービスアカウントに結び付けた別の鍵が要ります（docs/声の設定_google_tts.md）", file=sys.stderr)
        return 2
    if not key.isascii() or any(c.isspace() for c in key):
        print(f"鍵の 1 行目が鍵の形ではありません（日本語や空白が入っています）。{key_file(gem)} の中身を鍵だけにしてください",
              file=sys.stderr)
        return 2

    try:
        if a.list:
            for v in sorted(list_voices(key, "ja-JP"), key=lambda v: v["name"]):
                print(f'{v["name"]}\t{v.get("ssmlGender", "")}\t{v.get("naturalSampleRateHertz", "")}')
            return 0

        lines = [s.strip() for s in Path(a.src).read_text(encoding="utf-8-sig").splitlines()]
        lines = [s for s in lines if s]
        if not lines:
            print(f"文がありません: {a.src}", file=sys.stderr)
            return 1
        out = Path(a.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        items, total = [], 0.0
        for i, text in enumerate(lines, 1):
            data = synth(key, text, a.voice, a.speed, a.lang, a.model, a.prompt)
            name = f"{i:03d}.wav"
            (out / name).write_bytes(data)
            sec = round(wav_seconds(data), 3)
            total += sec
            items.append({"index": i, "text": text, "file": name, "seconds": sec})
            print(f"{name}\t{sec:.2f}s\t{text}")
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    meta = {
        "engine": "google-cloud-tts", "voice": a.voice, "speed": a.speed,
        "model": a.model, "prompt": a.prompt,
        "audio_encoding": "LINEAR16", "sample_rate_hz": SAMPLE_RATE,
        "source": str(Path(a.src)), "total_seconds": round(total, 3), "items": items,
    }
    (out / "narration.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"計 {len(items)} 文 {total:.2f} 秒 → {out / 'narration.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
