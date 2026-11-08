"""塊 B「流れ」の場面ごとの準備：係の一覧（_shell/agents.json）と、場面の時刻表（<場面>/timeline.json）を作る。

使い方:
    python -I -X utf8 scripts/video/flow/make_flow.py                     # 全場面（narration.txt のある物）
    python -I -X utf8 scripts/video/flow/make_flow.py 04_sokuryo          # 1 場面だけ
    python -I -X utf8 scripts/video/flow/make_flow.py 04_sokuryo --wav-dir out/video/flow_voice/04_sokuryo   # 声の長さで

- 係の名前とモデルは .claude/agents/<id>.md の frontmatter（name・model・description の最初の「。」まで）から読む。
- 時刻表：文は narration.txt（1 行 1 文、暫定）。声が無い間は 1 秒 RATE 文字の仮。場面の長さは
  max(めくれ＋文の合計＋間＋0.4 秒の静止, values.json の min_dur)。文の時刻は sentences[] に入る。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FLOW = Path(__file__).resolve().parent
RATE = 3.6          # 1 秒に読む文字数（声なしの仮）
FLIP = 0.6          # めくれで入る
LEAD = 0.3          # めくれの後、声が始まるまで
GAP = 0.35          # 文と文の間
HOLD = 0.4          # 最後の静止
# 塊 B の柱に並べる順（id は .claude/agents の名前。staff は人）
ORDER = ["staff", "satsuei", "shiwake", "sokuryo", "sekkei", "seizu", "kenshu", "shinko"]
SHORT = {"seizu": "製図係"}   # 「製図・模型係」は柱では短く（description の名前はそのまま agents.json の full に残す）


def read_agents() -> list[dict]:
    out = []
    for aid in ORDER:
        if aid == "staff":
            out.append({"id": "staff", "name": "職員", "full": "職員（人）", "model": "人", "model_raw": None,
                        "source": "人の操作（職員の画面）"})
            continue
        p = ROOT / ".claude" / "agents" / f"{aid}.md"
        txt = p.read_text(encoding="utf-8")
        fm = txt.split("---")[1]
        meta = dict(re.findall(r"^(\w+):\s*(.+)$", fm, re.M))
        full = meta.get("description", "").split("。")[0]
        model = meta.get("model", "").strip()
        out.append({"id": aid, "name": SHORT.get(aid, full), "full": full, "model": model.capitalize(), "model_raw": model,
                    "source": f".claude/agents/{aid}.md"})
    return out


def chars(s: str) -> int:
    return len(re.sub(r"[\s、。，．・「」（）()]", "", s))


def build(scene: Path, wav_dir: Path | None) -> dict:
    lines = [x.strip() for x in (scene / "narration.txt").read_text(encoding="utf-8").splitlines() if x.strip()]
    vals = json.loads((scene / "values.json").read_text(encoding="utf-8")) if (scene / "values.json").exists() else {}
    t = FLIP + LEAD
    sents = []
    for i, s in enumerate(lines):
        if i:
            t += GAP
        if wav_dir:
            with wave.open(str(wav_dir / f"{i + 1:03d}.wav"), "rb") as w:
                dur = w.getnframes() / w.getframerate()
            src = "wav"
        else:
            dur = chars(s) / RATE
            src = f"rate:{RATE}"
        sents.append({"n": i + 1, "text": s, "start": round(t, 3), "dur": round(dur, 3), "src": src})
        t += dur
    total = max(t + 0.5 + HOLD, float(vals.get("min_dur", 0)))
    return {"note": "make_flow.py が作る。手で直さない", "fps": 30, "size": [1920, 1080], "flip": FLIP, "hold": HOLD,
            "total": round(total, 3), "speech_end": round(t, 3), "sentences": sents}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenes", nargs="*")
    ap.add_argument("--wav-dir", type=Path)
    a = ap.parse_args()
    ag = read_agents()
    (FLOW / "_shell" / "agents.json").write_text(json.dumps({"note": "make_flow.py が .claude/agents から作る", "agents": ag},
                                                           ensure_ascii=False, indent=1), encoding="utf-8")
    print("係：" + "、".join(f"{x['name']}（{x['model']}）" for x in ag))
    names = a.scenes or sorted(p.name for p in FLOW.iterdir() if (p / "narration.txt").exists())
    for n in names:
        tl = build(FLOW / n, a.wav_dir)
        (FLOW / n / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{n}: {tl['total']:.2f} 秒（声 {tl['speech_end']:.2f} まで、{len(tl['sentences'])} 文）")
    if not names:
        sys.exit("場面が無い")


if __name__ == "__main__":
    main()
