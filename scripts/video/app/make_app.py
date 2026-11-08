"""塊 B「アプリ版」の準備：係の一覧（_shell/agents.json）、場面の時刻表（<場面>/timeline.json）、
アプリの画面の静止画（待機画面の再生 out/video/replay_honbu.mp4 から、values.json の app.replay_t の秒）を作る。

    python -I -X utf8 scripts/video/app/make_app.py            # 全場面
    python -I -X utf8 scripts/video/app/make_app.py 04_sokuryo

- 係の名前・モデル・定義の文は .claude/agents/<id>.md の frontmatter（description・model）から読む（書き換えない）。
- 再生の mp4 は 11/8 12:24 に web/progress.html?building=honbu&replay=1 を撮った物（1600×900）。ここでは 1 コマ切り出すだけ。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
SRC = ROOT / "out" / "video" / "app" / "_src"
REPLAY = ROOT / "out" / "video" / "replay_honbu.mp4"
# 上の帯の並び（流れの順）。staff・walk は係ではない
ORDER = [("staff", "職員"), ("satsuei", "撮影係"), ("shiwake", "仕分け係"), ("sokuryo", "測量係"), ("sekkei", "設計係"),
         ("seizu", "製図係"), ("kenshu", "検収係"), ("shinko", "進行役"), ("walk", "歩く画面")]


def agents() -> list[dict]:
    out = []
    for aid, short in ORDER:
        if aid == "staff":
            out.append({"id": aid, "short": short, "name": "職員", "model": "人", "long_model": "人（大学の職員）",
                        "desc": ["撮った写真を職員の画面に落とし、棟の名前を付けて「記録を始める」を押す。"], "source": "人の操作（web/staff.html）"})
            continue
        if aid == "walk":
            out.append({"id": aid, "short": short, "name": "歩く画面", "model": "", "long_model": "web/index.html",
                        "desc": ["受験生がスマホで歩く 3D。"], "source": "web/index.html"})
            continue
        txt = (ROOT / ".claude" / "agents" / f"{aid}.md").read_text(encoding="utf-8")
        fm = txt.split("---")[1]
        meta = dict(re.findall(r"^(\w+):\s*(.+)$", fm, re.M))
        d = meta["description"].strip()
        name, rest = d.split("。", 1)
        sents = [x + "。" for x in rest.split("。") if x.strip()]
        if not rest.rstrip().endswith("。") and sents:
            sents[-1] = sents[-1][:-1]
        model = meta.get("model", "").strip()
        out.append({"id": aid, "short": short, "name": name, "model": model.capitalize(), "long_model": f"Claude {model.capitalize()}",
                    "desc": sents, "source": f".claude/agents/{aid}.md"})
    return out


def frame(t: float) -> Path:
    SRC.mkdir(parents=True, exist_ok=True)
    p = SRC / f"replay_{t:05.1f}.png"
    if not p.exists():
        ff = shutil.which("ffmpeg") or "ffmpeg"
        subprocess.run([ff, "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(REPLAY), "-frames:v", "1", str(p)], check=True)
    return p


def main() -> None:
    (HERE / "_shell" / "agents.json").write_text(json.dumps({"note": "make_app.py が作る。手で直さない", "agents": agents()},
                                                             ensure_ascii=False, indent=1), encoding="utf-8")
    names = sys.argv[1:] or sorted(p.name for p in HERE.iterdir() if (p / "values.json").exists())
    for n in names:
        v = json.loads((HERE / n / "values.json").read_text(encoding="utf-8"))
        a = v["app"]
        if a.get("replay_t") is not None:
            p = frame(float(a["replay_t"]))
            a["src"] = p.relative_to(ROOT).as_posix()
            (HERE / n / "values.json").write_text(json.dumps(v, ensure_ascii=False, indent=1), encoding="utf-8")
        tl = {"note": "make_app.py が作る。手で直さない", "fps": 30, "size": [1920, 1080], "total": v["dur"]}
        (HERE / n / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding="utf-8")
        print(n, v["dur"], a.get("src"))


if __name__ == "__main__":
    main()
