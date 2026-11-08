"""時刻表（timeline.json の scenes[].sentences）どおりに文ごとの wav を並べ、音量を揃えて（loudnorm）映像に乗せる。

使い方:
    python -I -X utf8 scripts/video/mux_narration.py --timeline scripts/video/opening_v6/timeline.json \
        --wav-dir out/video/narration_v6 --video out/video/opening_v6_noaudio.mp4 --out out/video/opening_v6.mp4

音楽・効果音は入れない。音量は既定 -16 LUFS（--lufs で変える）。並べた声だけの wav も <out>.narration.wav に残す。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeline", type=Path, required=True)
    ap.add_argument("--wav-dir", type=Path, required=True)
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--lufs", type=float, default=-16.0)
    a = ap.parse_args()
    tl = json.loads(a.timeline.read_text(encoding="utf-8"))
    sents = [x for s in tl["scenes"] for x in s.get("sentences", [])]
    if not sents:
        raise SystemExit("時刻表に sentences が無い（make_timeline.py --narration で作る）")
    ff = shutil.which("ffmpeg") or "ffmpeg"
    cmd = [ff, "-y", "-loglevel", "error"]
    for x in sents:
        cmd += ["-i", str(a.wav_dir / x["file"])]
    parts = []
    for k, x in enumerate(sents):
        ms = int(round(x["start"] * 1000))
        parts.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=mono,adelay={ms}:all=1[d{k}]")
    mix = "".join(f"[d{k}]" for k in range(len(sents)))
    total = tl["total"]
    flt = ";".join(parts) + f";{mix}amix=inputs={len(sents)}:normalize=0,apad,atrim=0:{total:.3f}," \
        f"loudnorm=I={a.lufs}:TP=-1.5:LRA=11,aresample=48000[a]"
    narr = a.out.with_suffix(".narration.wav")
    subprocess.run(cmd + ["-filter_complex", flt, "-map", "[a]", "-ac", "1", str(narr)], check=True)
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(a.video), "-i", str(narr), "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest", "-movflags", "+faststart",
                    str(a.out)], check=True)
    r = subprocess.run([ff, "-hide_banner", "-nostats", "-i", str(a.out), "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    integ = [ln.strip() for ln in r.stderr.splitlines() if ln.strip().startswith("I:")]
    print(f"声 {len(sents)} 文を並べた → {narr}")
    print(f"mp4 → {a.out}  音量 {integ[-1] if integ else '（測れず）'}")


if __name__ == "__main__":
    main()
