"""app2 の場面を声付きの mp4 にする（係 S、11/8）。

    python -I -X utf8 scripts/video/app2/make_av.py 01_osu 02_satsuei 03_shiwake

前に：<場面>/narration.txt（1 行 1 文）を tts_google.py で out/video/app2/narration/<場面>/ に wav にしておく（narration.json が要る）。
ここでは timeline.json を作り直し（total＝values の dur と「声の頭＋長さ＋0.4 秒」の大きい方、sentences に声の頭）、
render_html.py --mp4 で out/video/app2/<場面>_noaudio.mp4、mux_narration.py で out/video/app2/B<nn>_<名前>.mp4 を出す。
声の頭は values.json の narration_start（既定 0.8 秒）。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUT = ROOT / "out" / "video" / "app2"
PY = [sys.executable, "-I", "-X", "utf8"]


def main() -> None:
    for n in sys.argv[1:]:
        v = json.loads((HERE / n / "values.json").read_text(encoding="utf-8"))
        nj = json.loads((OUT / "narration" / n / "narration.json").read_text(encoding="utf-8"))
        t, sents = float(v.get("narration_start", 0.8)), []
        for it in nj["items"]:
            sents.append({"file": it["file"], "start": round(t, 3), "seconds": it["seconds"], "text": it["text"]})
            t += it["seconds"] + 0.25
        total = round(max(float(v["dur"]), sents[-1]["start"] + sents[-1]["seconds"] + 0.4), 2)
        tl = {"note": "make_av.py が作る。手で直さない", "fps": 30, "size": [1920, 1080], "total": total,
              "scenes": [{"id": n, "start": 0, "sentences": sents}]}
        (HERE / n / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding="utf-8")
        num, name = n.split("_", 1)
        silent = OUT / f"B{num}_{name}_noaudio.mp4"
        subprocess.run(PY + ["scripts/video/render_html.py", "--page", f"scripts/video/app2/{n}/index.html",
                             "--out", f"out/video/app2/{n}", "--mp4", str(silent)], cwd=ROOT, check=True)
        subprocess.run(PY + ["scripts/video/mux_narration.py", "--timeline", str(HERE / n / "timeline.json"),
                             "--wav-dir", str(OUT / "narration" / n), "--video", str(silent),
                             "--out", str(OUT / f"B{num}_{name}.mp4")], cwd=ROOT, check=True)
        print(n, total, "秒")


if __name__ == "__main__":
    main()
