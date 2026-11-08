"""塊 B「アプリ版」の静止画を撮ってまとめる：場面ごとに 4 枚（a_寄り 0 秒・b_全体と枠 2.6 秒・c_縮む 3.5 秒・d_仕組み 終わり−0.2 秒）。

    python -I -X utf8 scripts/video/app/keys.py              # 全場面
    python -I -X utf8 scripts/video/app/keys.py 04_sokuryo   # 1 場面だけ撮り直す（一覧は全場面から作り直す）

撮った物は out/video/app/<場面>/frames/at_<秒>.png、まとめは out/video/app/stills/B<nn>_<名前>_<a〜d>.png と 00_一覧.png。
先に make_app.py（時刻表と再生の 1 コマ）を回しておく。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUT = ROOT / "out" / "video" / "app"
STILLS = OUT / "stills"
LABELS = ["a_寄り", "b_全体と枠", "c_縮む", "d_仕組み"]


def times(n: str) -> list[float]:
    tot = json.loads((HERE / n / "timeline.json").read_text(encoding="utf-8"))["total"]
    return [0.0, 2.6, 3.5, round(tot - 0.2, 2)]


def shoot(n: str) -> None:
    cmd = [sys.executable, "-I", "-X", "utf8", "scripts/video/render_html.py", "--page", f"scripts/video/app/{n}/index.html",
           "--out", f"out/video/app/{n}"]
    for t in times(n):
        cmd += ["--at", str(t)]
    subprocess.run(cmd, cwd=ROOT, check=True)


def frame(n: str, t: float) -> Path:
    return OUT / n / "frames" / f"at_{t:06.2f}.png"


def main() -> None:
    allnames = sorted(p.name for p in HERE.iterdir() if (p / "values.json").exists())
    for n in sys.argv[1:] or allnames:
        shoot(n)
    STILLS.mkdir(parents=True, exist_ok=True)
    rows = []
    for n in allnames:
        fs = [frame(n, t) for t in times(n)]
        if not all(f.exists() for f in fs):
            print("まだ撮っていない：", n)
            continue
        for f, lab in zip(fs, LABELS):
            shutil.copy(f, STILLS / f"B{n[:2]}_{n[3:]}_{lab}.png")
        rows.append((n, fs))
    W, H = 480, 270
    sheet = Image.new("RGB", (W * 4, (H + 24) * len(rows)), "white")
    d = ImageDraw.Draw(sheet)
    for i, (n, fs) in enumerate(rows):
        for j, f in enumerate(fs):
            sheet.paste(Image.open(f).resize((W, H)), (j * W, i * (H + 24) + 24))
        d.text((6, i * (H + 24) + 6), n, fill="red")
    sheet.save(STILLS / "00_一覧.png")
    print(STILLS, len(rows), "場面")


if __name__ == "__main__":
    main()
