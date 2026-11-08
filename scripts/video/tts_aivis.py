"""AivisSpeech Engine で、1 行 1 文のテキストを文ごとの wav にする。

使い方:
    python -I -X utf8 scripts/video/tts_aivis.py --in 文.txt --out-dir out/video/narration \
        --style ノーマル --speed 1.0 [--speaker まお] [--url http://127.0.0.1:10101]

    --list で、エンジンに入っている話者とスタイルの一覧だけを出す。

出力:
    <out-dir>/001.wav, 002.wav, ...（文の順）
    <out-dir>/narration.json  … 文・ファイル名・長さ（秒）・話者・スタイル・話速の一覧

前提: AivisSpeech（または AivisSpeech Engine）が起動していて、PC 内の API が返ること。
標準ライブラリだけで動く（追加の pip は不要）。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:10101"


class EngineDown(RuntimeError):
    pass


def _request(url: str, method: str = "GET", body: bytes | None = None,
             content_type: str | None = None, timeout: float = 120) -> bytes:
    req = urllib.request.Request(url, data=body, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"エンジンがエラーを返しました（{e.code}）: {url}\n{detail}") from e
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise EngineDown(str(e)) from e


def get_speakers(base: str) -> list[dict]:
    return json.loads(_request(f"{base}/speakers", timeout=10))


def find_style_id(speakers: list[dict], speaker: str | None, style: str) -> tuple[int, str, str]:
    cands = []
    for sp in speakers:
        if speaker and sp["name"] != speaker:
            continue
        for st in sp["styles"]:
            if st["name"] == style:
                cands.append((st["id"], sp["name"], st["name"]))
    if not cands:
        names = [f'{sp["name"]}: ' + "・".join(st["name"] for st in sp["styles"]) for sp in speakers]
        raise SystemExit(
            f"話者「{speaker or '（指定なし）'}」のスタイル「{style}」が見つかりません。\n"
            "入っている話者とスタイル:\n  " + "\n  ".join(names))
    if len(cands) > 1 and not speaker:
        print(f"スタイル「{style}」は複数の話者にあります（" + "、".join(c[1] for c in cands)
              + f"）。最初の「{cands[0][1]}」を使います。変えるなら --speaker で指定。", file=sys.stderr)
    return cands[0]


def synth(base: str, text: str, style_id: int, speed: float) -> bytes:
    q = urllib.parse.urlencode({"text": text, "speaker": style_id})
    query = json.loads(_request(f"{base}/audio_query?{q}", method="POST", body=b""))
    query["speedScale"] = speed
    return _request(f"{base}/synthesis?speaker={style_id}", method="POST",
                    body=json.dumps(query).encode("utf-8"), content_type="application/json")


def wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / float(w.getframerate())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AivisSpeech Engine で 1 行 1 文を wav にする")
    ap.add_argument("--in", dest="src", help="1 行 1 文のテキスト（UTF-8）。空行は飛ばす")
    ap.add_argument("--out-dir", help="wav と narration.json の出力先")
    ap.add_argument("--style", default="ノーマル", help="スタイル名（例: ノーマル）")
    ap.add_argument("--speaker", default="", help="話者名（例: まお）。省くと全話者から探し、最初に見つかった話者を使う")
    ap.add_argument("--speed", type=float, default=1.0, help="話速（1.0 が既定）")
    ap.add_argument("--url", default=DEFAULT_URL, help=f"エンジンの URL（既定 {DEFAULT_URL}）")
    ap.add_argument("--list", action="store_true", help="話者とスタイルの一覧を出して終わる")
    a = ap.parse_args(argv)
    base = a.url.rstrip("/")

    try:
        speakers = get_speakers(base)
    except EngineDown as e:
        print(f"AivisSpeech のエンジンにつながりません（{base}）。\n"
              "AivisSpeech を起動するか、インストール先の AivisSpeech-Engine\\run.exe を起動してから、"
              f"もう一度実行してください。\n（詳細: {e}）", file=sys.stderr)
        return 2

    if a.list:
        for sp in speakers:
            for st in sp["styles"]:
                print(f'{sp["name"]}\t{st["name"]}\t{st["id"]}')
        return 0
    if not a.src or not a.out_dir:
        ap.error("--in と --out-dir が要ります（一覧だけなら --list）")

    lines = [s.strip() for s in Path(a.src).read_text(encoding="utf-8-sig").splitlines()]
    lines = [s for s in lines if s]
    if not lines:
        print(f"文がありません: {a.src}", file=sys.stderr)
        return 1
    style_id, sp_name, st_name = find_style_id(speakers, a.speaker or None, a.style)

    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    items = []
    total = 0.0
    for i, text in enumerate(lines, 1):
        try:
            data = synth(base, text, style_id, a.speed)
        except EngineDown as e:
            print(f"{i} 文目の途中でエンジンが応答しなくなりました（{base}）: {e}", file=sys.stderr)
            return 2
        name = f"{i:03d}.wav"
        (out / name).write_bytes(data)
        sec = round(wav_seconds(data), 3)
        total += sec
        items.append({"index": i, "text": text, "file": name, "seconds": sec})
        print(f"{name}\t{sec:.2f}s\t{text}")

    meta = {
        "engine": base, "speaker": sp_name, "style": st_name, "style_id": style_id,
        "speed": a.speed, "source": str(Path(a.src)), "total_seconds": round(total, 3),
        "items": items,
    }
    (out / "narration.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"計 {len(items)} 文 {total:.2f} 秒 → {out / 'narration.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
