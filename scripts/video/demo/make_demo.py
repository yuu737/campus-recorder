"""デモ版の場面の時刻表（timeline.json）を作る。手で直さない。

場面の長さは values.json の dur。文の時刻は narration.txt の 1 行 1 文を、声なしの仮として 1 秒 3.6 文字で
（頭 0.5 秒あけて、文の間 0.25 秒）並べる。声ができたら差し替える。

    python -I -X utf8 scripts/video/demo/make_demo.py            # 全部の場面
    python -I -X utf8 scripts/video/demo/make_demo.py 02_photo   # 1 つだけ
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RATE = 3.6


def make(d: Path) -> None:
    v = json.loads((d / "values.json").read_text(encoding="utf-8"))
    lines = [s.strip() for s in (d / "narration.txt").read_text(encoding="utf-8").splitlines() if s.strip()]
    t, sents = 0.5, []
    for i, s in enumerate(lines, 1):
        dur = round(len(s) / RATE, 3)
        sents.append({"n": i, "text": s, "start": round(t, 3), "dur": dur, "src": f"rate:{RATE}"})
        t += dur + 0.25
    total = float(v["dur"])
    tl = {"note": "make_demo.py が作る。手で直さない", "fps": 30, "size": [1920, 1080], "total": total,
          "speech_end": round(t - 0.25, 3), "speech_fits": t - 0.25 <= total, "sentences": sents}
    (d / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{d.name}: {total:.1f} 秒、仮の声 {t - 0.25:.1f} 秒" + ("" if t - 0.25 <= total else "  ※声が長い"))


def main() -> None:
    names = sys.argv[1:] or sorted(p.name for p in HERE.iterdir() if p.is_dir() and p.name[:2].isdigit())
    for n in names:
        make(HERE / n)


if __name__ == "__main__":
    main()
