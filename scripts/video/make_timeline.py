"""導入 v2 の時刻表（scripts/video/opening/timeline.json）を作る。

文の時刻（いつから何秒読むか）を決め、場面の始まり・終わりをそこから出す。画面（index.html）の拍は
すべて「場面の始まり」「文の始まり」「文の長さ」からの割合で決まるので、声を差し替えたらこれを作り直すだけで
拍が付いてくる。

使い方:
    python -I -X utf8 scripts/video/make_timeline.py                 # 声なし：1 秒 RATE 文字の目安で
    python -I -X utf8 scripts/video/make_timeline.py --rate 4.0
    python -I -X utf8 scripts/video/make_timeline.py --out scripts/video/opening_v3/timeline.json --min A=6.4,B=3.6,C=5.4,D=5.8   # v3
    python -I -X utf8 scripts/video/make_timeline.py --values scripts/video/opening_v5/values.json --out scripts/video/opening_v5/timeline.json --min A=6.2,B=4.2,C=5.2,D=5.8   # v5
    python -I -X utf8 scripts/video/make_timeline.py --narration out/video/narration_v6/narration.json --values scripts/video/opening_v6/values.json --out scripts/video/opening_v6/timeline.json   # v6（声あり）
    python -I -X utf8 scripts/video/make_timeline.py --narration out/video/narration_v7/narration.json --map T:1,A:2,B:3,C:4,D:5 --values scripts/video/opening_v7/values.json --lead T=0.4,C=0.4,D=0.4 --tail T=0.5,D=0.9 --min A=5.6,B=4.2 --out scripts/video/opening_v7/timeline.json   # v7
    python -I -X utf8 scripts/video/make_timeline.py --wav-dir out/video/narration_google   # 001.wav〜004.wav の長さで

--wav-dir を渡すと、文 i の長さ＝ <dir>/00i.wav の長さ（秒）。wav は ffprobe ではなく標準ライブラリ wave で読む。
"""
from __future__ import annotations

import argparse
import json
import re
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "scripts" / "video" / "opening" / "timeline.json"

# 文は台本（利用者）の 4 文だけ。画面での区切り（pre＝前置き、lines＝見出しの行）は見せ方の都合で、文は変えない。
SCENES = [
    {"id": "A", "pillar": "受験生", "text": "地方の受験生は、志望校を見ないまま決めている",
     "pre": "地方の受験生は、", "lines": ["志望校を見ないまま", "決めている"], "lead": 0.8, "tail": 0.9},
    {"id": "B", "pillar": "作者", "text": "自分もそうだった",
     "pre": "", "lines": ["自分も", "そうだった"], "lead": 0.55, "tail": 0.75},
    {"id": "C", "pillar": "手間", "text": "それには 2 か月と専門知識が要る",
     "pre": "それには", "lines": ["2 か月と", "専門知識が要る"], "lead": 1.2, "tail": 0.7},
    {"id": "D", "pillar": "広報", "text": "大学の広報には予算も専門家もいない",
     "pre": "大学の広報には", "lines": ["予算も専門家も", "いない"], "lead": 0.6, "tail": 1.6},
]
RATE = 4.5        # 1 秒に読む文字数の目安（声なしの初版）
MIN_SCENE = {"A": 5.6, "B": 2.6, "C": 4.8, "D": 5.6}   # 場面の中の拍（写真の山・日めくり・札）が収まる最短


def chars(s: str) -> int:
    return len(re.sub(r"[\s、。，．・「」]", "", s))


def wav_seconds(p: Path) -> float:
    with wave.open(str(p), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def build(rate: float, wav_dir: Path | None) -> dict:
    t = 0.0
    scenes = []
    for i, s in enumerate(SCENES):
        if wav_dir:
            dur = wav_seconds(wav_dir / f"{i + 1:03d}.wav")
            src = f"wav:{(wav_dir / f'{i + 1:03d}.wav').as_posix()}"
        else:
            dur = chars(s["text"]) / rate
            src = f"rate:{rate}"
        start = t
        text_in = start + s["lead"]
        end = max(text_in + dur + s["tail"], start + MIN_SCENE[s["id"]])
        scenes.append({**{k: s[k] for k in ("id", "pillar", "text", "pre", "lines")},
                       "start": round(start, 3), "text_in": round(text_in, 3), "text_dur": round(dur, 3),
                       "end": round(end, 3), "dur_source": src})
        t = end
    return {"note": "make_timeline.py が作る。手で直さない。声を替えたら --wav-dir で作り直す。",
            "fps": 30, "size": [1920, 1080], "flip": 0.6, "total": round(t, 3), "scenes": scenes}


# v6：題の板＋声。文（narration.json の順）と場面の対応、場面の前後の間
V6_MAP = [("T", [1]), ("A", [2, 3]), ("B", [4, 5]), ("C", [6]), ("D", [7])]
V6_LEAD = {"T": 0.5, "A": 0.6, "B": 0.5, "C": 0.6, "D": 0.5}
V6_TAIL = {"T": 0.6, "A": 0.5, "B": 0.5, "C": 0.5, "D": 1.6}
V6_GAP = 0.35            # 同じ場面の文と文の間
V6_TITLE = {"id": "T", "pillar": "", "text": "写真を撮るだけで、根拠付きの、歩ける大学の 3D 空間がつくれる",
            "pre": "", "lines": ["写真を撮るだけで、", "根拠付きの、", "歩ける大学の 3D 空間がつくれる"]}


def build_v6(narr: Path, vmap=None) -> dict:
    """narration.json（tts の出力）の文の長さから、場面と文の時刻を決める。画面の見出しは SCENES のまま（題は V6_TITLE）。"""
    nj = json.loads(narr.read_text(encoding="utf-8"))
    items = nj.get("items") or nj.get("sentences") or nj
    durs = [float(x.get("seconds") or x.get("duration") or x.get("dur")) for x in items]
    files = [x.get("file") or x.get("wav") for x in items]
    texts = [x.get("text") for x in items]
    by_id = {s["id"]: s for s in SCENES}
    t, scenes = 0.0, []
    for sid, idx in (vmap or V6_MAP):
        s = V6_TITLE if sid == "T" else by_id[sid]
        start = t
        cur = start + V6_LEAD[sid]
        sents = []
        for k, i in enumerate(idx):
            if k:
                cur += V6_GAP
            sents.append({"n": i, "text": texts[i - 1], "file": files[i - 1], "start": round(cur, 3), "dur": round(durs[i - 1], 3)})
            cur += durs[i - 1]
        end = max(cur + V6_TAIL[sid], start + MIN_SCENE.get(sid, 0))
        scenes.append({**{k: s[k] for k in ("id", "pillar", "text", "pre", "lines")},
                       "start": round(start, 3), "text_in": sents[0]["start"], "text_dur": sents[0]["dur"],
                       "speech_end": round(cur, 3), "end": round(end, 3), "sentences": sents,
                       "dur_source": f"narration:{narr.as_posix()}"})
        t = end
    return {"note": "make_timeline.py --narration が作る。手で直さない。", "fps": 30, "size": [1920, 1080], "flip": 0.6,
            "total": round(t, 3), "scenes": scenes}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=RATE)
    ap.add_argument("--wav-dir", type=Path)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--values", type=Path, help="v5 以降：values.json の effort.headline で手間の場面の文を差し替える")
    ap.add_argument("--narration", type=Path, help="v6：tts の narration.json から題の板＋4 場面を声に合わせる")
    ap.add_argument("--map", default="", help="v7：場面と文の対応（例 T:1,A:2,B:3,C:4,D:5、1 場面に複数なら A:2+3）")
    ap.add_argument("--lead", default="", help="場面の前の間を上書き（例 T=0.4,A=0.5）")
    ap.add_argument("--tail", default="", help="場面の後の間を上書き（例 D=1.0）")
    ap.add_argument("--min", default="", help="場面の最短を上書き（例 A=6.4,B=3.6）。v3 は絵が多いので長め")
    a = ap.parse_args()
    if a.values:
        v = json.loads(a.values.read_text(encoding="utf-8"))
        hl, cost = v["effort"]["headline"], v["effort"]["cost"]
        fill = lambda x: x.replace("{low}", str(cost["low"])).replace("{high}", str(cost["high"]))
        for sc in SCENES:
            if sc["id"] == "C":
                sc.update(text=fill(hl["text"]), pre="", lines=[fill(x) for x in hl["lines"]])
            h2 = v.get("headlines", {}).get(sc["id"])     # v7：画面の見出しを声の文に揃える
            if h2:
                sc.update(text=h2["text"], pre=h2.get("pre", ""), lines=h2["lines"])
    for kv in filter(None, a.min.split(",")):
        k, v = kv.split("=")
        MIN_SCENE[k.strip()] = float(v)
    for opt, dst in ((a.lead, V6_LEAD), (a.tail, V6_TAIL)):
        for kv in filter(None, opt.split(",")):
            k, v = kv.split("=")
            dst[k.strip()] = float(v)
    vmap = [(kv.split(":")[0], [int(x) for x in kv.split(":")[1].split("+")]) for kv in a.map.split(",")] if a.map else None
    tl = build_v6(a.narration, vmap) if a.narration else build(a.rate, a.wav_dir)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding="utf-8")
    for s in tl["scenes"]:
        print(f"{s['id']} {s['start']:5.2f}〜{s['end']:5.2f}  文 {s['text_in']:5.2f}＋{s['text_dur']:.2f}  {s['text']}"
              + (f"  声の終わり {s['speech_end']:.2f}" if 'speech_end' in s else ""))
    print(f"合計 {tl['total']:.2f} 秒 → {a.out}")


if __name__ == "__main__":
    main()
