"""場面ごとの mp4 を順に繋ぐ（ffmpeg concat）。音声なしの mp4 だけでも、音声付きでも動く。

使い方:
    python -I -X utf8 scripts/video/join.py --out out/video/flow_B.mp4 out/video/flow/01_osu.mp4 out/video/flow/04_sokuryo.mp4 …
    python -I -X utf8 scripts/video/join.py --out out/video/flow_B.mp4 --dir out/video/flow       # 名前の順（01_… 02_…）で全部

- 全部が同じ大きさ・fps（1920×1080、30）である事を確かめてから、作り直し（再エンコード）で繋ぐ（継ぎ目でずれない）。
- 音声：1 本でも音のある mp4 があれば、音の無い mp4 には同じ長さの無音を足して、全体に AAC の音を付ける。
  1 本も無ければ映像だけ。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def probe(p: Path) -> dict:
    ff = shutil.which("ffprobe") or "ffprobe"
    r = subprocess.run([ff, "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate:format=duration",
                        "-of", "json", str(p)], capture_output=True, text=True, check=True)
    j = json.loads(r.stdout)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    return {"w": v["width"], "h": v["height"], "fps": v["r_frame_rate"], "audio": any(s["codec_type"] == "audio" for s in j["streams"]),
            "dur": float(j["format"]["duration"])}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--dir", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    files = list(a.files) or sorted(p for p in a.dir.glob("*.mp4"))
    if not files:
        raise SystemExit("繋ぐ mp4 が無い")
    info = [probe(p) for p in files]
    sz = {(i["w"], i["h"], i["fps"]) for i in info}
    if len(sz) != 1:
        raise SystemExit(f"大きさか fps が揃っていない：{sz}")
    any_audio = any(i["audio"] for i in info)
    ff = shutil.which("ffmpeg") or "ffmpeg"
    cmd = [ff, "-y", "-loglevel", "error"]
    for p in files:
        cmd += ["-i", str(p)]
    parts, n = [], len(files)
    for k, i in enumerate(info):
        if any_audio:
            if i["audio"]:
                parts.append(f"[{k}:v:0][{k}:a:0]")
            else:
                cmd += ["-f", "lavfi", "-t", f"{i['dur']:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
                parts.append(f"[{k}:v:0][{n}:a:0]")
                n += 1
        else:
            parts.append(f"[{k}:v:0]")
    flt = "".join(parts) + f"concat=n={len(files)}:v=1:a={1 if any_audio else 0}[v]" + ("[a]" if any_audio else "")
    cmd += ["-filter_complex", flt, "-map", "[v]"] + (["-map", "[a]", "-c:a", "aac", "-b:a", "192k"] if any_audio else ["-an"])
    cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", "16", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(a.out)]
    subprocess.run(cmd, check=True)
    print(f"{len(files)} 本を繋いだ → {a.out}（{sum(i['dur'] for i in info):.2f} 秒、音 {'あり' if any_audio else 'なし'}）")


if __name__ == "__main__":
    main()
