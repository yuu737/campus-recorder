"""最終の書き出し（11/8 夜、docs/指示_最終_11-08.md の 1-3）：全場面を声付きの mp4 にする。

    python -I -X utf8 scripts/video/app2/make_final.py 00_hashi 01_osu …     # 場面を並べる（1 プロセスで順に）

- 声は作成済みの wav を使う（作り直さない）：B0〜C3 は out/video/app2/narration_BC/001〜014.wav、
  C_seika は out/video/app2/narration/C_seika/001.wav、C4 は out/video/narration_v7/001.wav（題の 1 文）。
- 声の頭：B1〜B10 は 2.4 秒（小窓が縮んで本文が出る所）、B0・C1〜C4・C_seika は 0.8 秒。
- 場面の長さ＝max(values の dur（DUR に上書きがあればそれ）, 声の頭＋声の長さ＋0.5 秒)。
- 出す物：out/video/app2/<名前>_noaudio.mp4 → mux_narration.py（−16 LUFS）→ out/video/app2/<名前>.mp4。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUT = ROOT / "out" / "video" / "app2"
PY = [sys.executable, "-I", "-X", "utf8"]
BC = OUT / "narration_BC"
# 場面 → （声の wav, 出す名前）
VOICE = {
    "00_hashi": (BC, "001.wav", "B00_hashi"), "01_osu": (BC, "002.wav", "B01_osu"), "02_satsuei": (BC, "003.wav", "B02_satsuei"),
    "03_shiwake": (BC, "004.wav", "B03_shiwake"), "04_kansatsu": (BC, "005.wav", "B04_kansatsu"), "05_sokuryo": (BC, "006.wav", "B05_sokuryo"),
    "06_sekkei": (BC, "007.wav", "B06_sekkei"), "07_shoko": (BC, "008.wav", "B07_shoko"), "08_kenshu": (BC, "009.wav", "B08_kenshu"),
    "09_shinko": (BC, "010.wav", "B09_shinko"), "10_aruku": (BC, "011.wav", "B10_aruku"),
    "C1_sumaho": (BC, "012.wav", "C1_sumaho"), "C2_kiroku": (BC, "013.wav", "C2_kiroku"), "C3_koe": (BC, "014.wav", "C3_koe"),
    "C_seika": (OUT / "narration" / "C_seika", "001.wav", "C_seika"),
    "C4_tadaigaku": (ROOT / "out" / "video" / "narration_v7", "001.wav", "C4_tadaigaku"),
}
EARLY = {"00_hashi", "C1_sumaho", "C2_kiroku", "C3_koe", "C4_tadaigaku", "C_seika"}
# 3 分に収めるための詰め（指示書 1-4 の順：C1 10→8、B5 14→13。先に当てた）
DUR = {"C1_sumaho": 8.0, "05_sokuryo": 13.0}


def item(d: Path, f: str) -> dict:
    nj = json.loads((d / "narration.json").read_text(encoding="utf-8"))
    return next(i for i in nj["items"] if i["file"] == f)


def main() -> None:
    for n in sys.argv[1:]:
        d, f, name = VOICE[n]
        it = item(d, f)
        wd = OUT / "narration_final" / n
        wd.mkdir(parents=True, exist_ok=True)
        shutil.copy(d / f, wd / "001.wav")
        v = json.loads((HERE / n / "values.json").read_text(encoding="utf-8"))
        st = 0.8 if n in EARLY else 2.4
        sec = float(it["seconds"])
        total = round(max(float(DUR.get(n, v["dur"])), st + sec + 0.5), 2)
        tl = {"note": "make_final.py が作る。手で直さない", "fps": 30, "size": [1920, 1080], "total": total,
              "scenes": [{"id": n, "start": 0, "end": total,
                          "sentences": [{"n": 1, "file": "001.wav", "start": st, "seconds": sec, "dur": sec, "text": it["text"]}]}]}
        (HERE / n / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding="utf-8")
        silent = OUT / f"{name}_noaudio.mp4"
        subprocess.run(PY + ["scripts/video/render_html.py", "--page", f"scripts/video/app2/{n}/index.html",
                             "--out", f"out/video/app2/{n}", "--mp4", str(silent)], cwd=ROOT, check=True)
        subprocess.run(PY + ["scripts/video/mux_narration.py", "--timeline", str(HERE / n / "timeline.json"),
                             "--wav-dir", str(wd), "--video", str(silent), "--out", str(OUT / f"{name}.mp4"), "--lufs", "-16"],
                       cwd=ROOT, check=True)
        print(n, "→", f"{name}.mp4", total, "秒（声", st, "+", sec, "）", flush=True)


if __name__ == "__main__":
    main()
