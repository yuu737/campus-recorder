"""ElevenLabs の Text to Speech で、1 行 1 文のテキストを文ごとの wav にする（tts_aivis.py と同じ入出力）。

使い方:
    set ELEVENLABS_API_KEY=...   （鍵は環境変数からだけ読む。引数・設定ファイルには書かない）
    python -I -X utf8 scripts/video/tts_elevenlabs.py --in 文.txt --out-dir out/video/narration \
        --voice-id <声の ID> [--model eleven_multilingual_v2] [--speed 1.0]

出力:
    <out-dir>/001.mp3 … ElevenLabs から受け取ったまま（mp3_44100_128）
    <out-dir>/001.wav … ffmpeg で変換した物（長さはこちらで測る）
    <out-dir>/narration.json … 文・ファイル名・長さ（秒）・声・モデル・話速の一覧

API（2026-11-08 に公式ドキュメント https://elevenlabs.io/docs/api-reference/text-to-speech/convert で確認）:
    POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128
    ヘッダ xi-api-key、本文 JSON {text, model_id, voice_settings: {speed, ...}}
    model_id の既定は eleven_multilingual_v2。eleven_v3 も指定できる。

注意（公式ヘルプ「Can I publish the content I generate on the platform?」）:
    無料プランは商用不可で、公開する時は題名に "elevenlabs.io" か "11.ai" を入れる必要がある。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

API_BASE = "https://api.elevenlabs.io"
OUTPUT_FORMAT = "mp3_44100_128"
MODELS_HINT = ("eleven_multilingual_v2", "eleven_v3", "eleven_flash_v2_5")


def synth(key: str, voice_id: str, text: str, model: str, speed: float | None,
          stability: float | None, language_code: str | None) -> bytes:
    url = (f"{API_BASE}/v1/text-to-speech/{urllib.parse.quote(voice_id, safe='')}"
           f"?output_format={OUTPUT_FORMAT}")
    body: dict = {"text": text, "model_id": model}
    vs = {}
    if speed is not None:
        vs["speed"] = speed
    if stability is not None:
        vs["stability"] = stability
    if vs:
        body["voice_settings"] = vs
    if language_code:
        body["language_code"] = language_code
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("xi-api-key", key)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "audio/mpeg")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:800]
        raise RuntimeError(f"ElevenLabs がエラーを返しました（{e.code}）: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"ElevenLabs につながりません: {e}") from e


def to_wav(ffmpeg: str, src: Path, dst: Path) -> None:
    subprocess.run([ffmpeg, "-y", "-v", "error", "-i", str(src), "-c:a", "pcm_s16le", str(dst)],
                   check=True)


def wav_seconds(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / float(w.getframerate())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="ElevenLabs で 1 行 1 文を文ごとの wav にする。鍵は環境変数 ELEVENLABS_API_KEY からだけ読む")
    ap.add_argument("--in", dest="src", required=True, help="1 行 1 文のテキスト（UTF-8）。空行は飛ばす")
    ap.add_argument("--out-dir", required=True, help="mp3・wav・narration.json の出力先")
    ap.add_argument("--voice-id", required=True, help="声の ID（ElevenLabs の My Voices / Voice Library で見る）")
    ap.add_argument("--model", default="eleven_multilingual_v2",
                    help="モデル ID（既定 eleven_multilingual_v2。例: " + "、".join(MODELS_HINT) + "）")
    ap.add_argument("--speed", type=float, default=None, help="話速（voice_settings.speed、既定 1）")
    ap.add_argument("--stability", type=float, default=None, help="voice_settings.stability（既定 0.5）")
    ap.add_argument("--language-code", default=None,
                    help="ISO 639-1（例 ja）。公式の説明では multilingual_v2 系では使えない")
    a = ap.parse_args(argv)

    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        print("環境変数 ELEVENLABS_API_KEY を設定してください", file=sys.stderr)
        return 2
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ffmpeg が見つかりません（mp3 を wav に変えて長さを測るのに使います）", file=sys.stderr)
        return 2

    lines = [s.strip() for s in Path(a.src).read_text(encoding="utf-8-sig").splitlines()]
    lines = [s for s in lines if s]
    if not lines:
        print(f"文がありません: {a.src}", file=sys.stderr)
        return 1

    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    items = []
    total = 0.0
    for i, text in enumerate(lines, 1):
        try:
            data = synth(key, a.voice_id, text, a.model, a.speed, a.stability, a.language_code)
        except RuntimeError as e:
            print(f"{i} 文目で止まりました: {e}", file=sys.stderr)
            return 1
        mp3 = out / f"{i:03d}.mp3"
        wav = out / f"{i:03d}.wav"
        mp3.write_bytes(data)
        to_wav(ffmpeg, mp3, wav)
        sec = round(wav_seconds(wav), 3)
        total += sec
        items.append({"index": i, "text": text, "file": wav.name, "mp3": mp3.name, "seconds": sec})
        print(f"{wav.name}\t{sec:.2f}s\t{text}")

    meta = {
        "engine": "elevenlabs", "voice_id": a.voice_id, "model": a.model,
        "output_format": OUTPUT_FORMAT, "speed": a.speed, "stability": a.stability,
        "source": str(Path(a.src)), "total_seconds": round(total, 3), "items": items,
    }
    (out / "narration.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"計 {len(items)} 文 {total:.2f} 秒 → {out / 'narration.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
