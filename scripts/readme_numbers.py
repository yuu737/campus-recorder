"""README（日・英）の棟ごとの表を、報告 out/<棟>/report.json と `recorder counts --json` から自動で書く（11/8）。

  python -I -X utf8 scripts/readme_numbers.py            report.json が無い棟だけ recorder report で出し、表を書き、照合する
  python -I -X utf8 scripts/readme_numbers.py --refresh  4 棟とも recorder report で出し直してから書く
  python -I -X utf8 scripts/readme_numbers.py --check    書かずに照合だけ（README の表と report.json の差を数える。差があれば終了コード 1）

表は README.ja.md・README.md の印 <!-- numbers:start --> 〜 <!-- numbers:end --> の間だけを書き換える。手で書いた数字は印の外に置かない。
棟の並びは data/site/tempaku.json の scope.targets（無ければ data/buildings/*.json）。名前は棟 JSON の name。
照合：README の表の各行の数字を読み戻し、report.json（と counts --json）から作った数字の列と 1 つずつ比べる。差の数を出す。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
START, END = "<!-- numbers:start -->", "<!-- numbers:end -->"
READMES = {"ja": ROOT / "README.ja.md", "en": ROOT / "README.md"}
NAME_EN = {"honbu": "Main building", "library": "Library", "tower75": "Tower 75", "kyotsu-minami": "Kyotsu Lecture Bldg. South"}


def targets() -> list[str]:
    site = ROOT / "data" / "site" / "tempaku.json"
    if site.exists():
        t = (json.loads(site.read_text(encoding="utf-8")).get("scope") or {}).get("targets")
        if t:
            order = ["honbu", "library", "tower75", "kyotsu-minami"]       # README の並び（知らない棟は後ろに）
            return sorted(t, key=lambda b: (order.index(b) if b in order else len(order), b))
    return sorted(p.stem for p in (ROOT / "data" / "buildings").glob("*.json"))


def run_report(bid: str) -> None:
    subprocess.run([sys.executable, "-I", "-X", "utf8", "-m", "recorder", "report", f"data/buildings/{bid}.json"],
                   cwd=ROOT, check=True, stdout=subprocess.DEVNULL)


def load_reports(bids: list[str], refresh: bool) -> dict[str, dict]:
    out = {}
    for b in bids:
        p = ROOT / "out" / b / "report.json"
        if refresh or not p.exists():
            run_report(b)
        rep = json.loads(p.read_text(encoding="utf-8"))
        html = str(rep.get("html") or "").replace("\\", "/")
        if not html.endswith(f"out/{b}/report.html"):
            raise SystemExit(f"{p} の html が {html}（本物の報告ではない。--refresh で出し直す）")
        out[b] = rep
    return out


def load_counts(bids: list[str]) -> dict[str, dict]:
    files = [f"data/buildings/{b}.json" for b in bids]
    r = subprocess.run([sys.executable, "-I", "-X", "utf8", "-m", "recorder", "counts", "--json", *files],
                       cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8")
    rows = json.loads(r.stdout)
    return {Path(x["file"]).stem: x for x in rows}


def name_of(bid: str) -> str:
    b = json.loads((ROOT / "data" / "buildings" / f"{bid}.json").read_text(encoding="utf-8"))
    return b.get("name") or bid


# --- 1 棟の数字の列（表の並びと同じ。照合はこの列と表を比べる） ----------------------------------

def numbers(rep: dict, cnt: dict) -> list[float | None]:
    c = rep["counts"]
    mk = cnt["measured_kinds"]
    ob = cnt["observations"]["by_status"]
    ks = c.get("kenshu") or {}
    rs = c.get("resid") or {}
    bf = rs.get("best_face") or {}
    return [c["photos"], c["registered"],
            c["measured"], mk["direct"] + mk["chain"],
            c["estimated"], c.get("hypotheses", 0),
            c["unknown"],
            ob["measured"]["observations"], ob["estimated"]["observations"], ob["unknown"]["observations"],
            c["requests"], c["tool_requests"],
            len(ks.get("pass") or []), len(ks.get("weak") or []),
            rs.get("median_px"), rs.get("median_m"), bf.get("median_px"), bf.get("median_m")]


def consistency(bid: str, rep: dict, cnt: dict) -> list[str]:
    """report.json と counts --json の同じ数が合っているか。合っていなければ表に書く前に止める。"""
    c, ob = rep["counts"], cnt["observations"]["by_status"]
    bad = []
    for k in ("measured", "estimated", "unknown"):
        if c[k] != ob[k]["values"]:
            bad.append(f"{bid}: {k} report {c[k]} ≠ counts {ob[k]['values']}")
        if c["observations"][k] != ob[k]["observations"]:
            bad.append(f"{bid}: 観察 {k} report {c['observations'][k]} ≠ counts {ob[k]['observations']}")
    if c["unknown"] != c.get("unknowns"):
        bad.append(f"{bid}: report の unknown {c['unknown']} ≠ 未確定の値 {c.get('unknowns')}")
    return bad


def _n(x) -> str:
    if x is None:
        return "—"
    if isinstance(x, float) and not x.is_integer():
        return f"{x:g}"
    return f"{int(x):,}"


def row(label: str, v: list, lang: str) -> str:
    (ph, reg, m, mlen, e, hyp, u, om, oe, ou, rq, tq, kp, kw, rpx, rm, bpx, bm) = v
    weak = (f"（弱い合格 {_n(kw)}）" if lang == "ja" else f" (+{_n(kw)} weak)") if kw else ""
    best = f"{_n(bpx)} px / {_n(bm)} m"
    lp, rp = ("（", "）") if lang == "ja" else (" (", ")")
    return (f"| {label} | {_n(ph)} | {_n(reg)} | {_n(m)}{lp}{_n(mlen)}{rp} | {_n(e)}{lp}{_n(hyp)}{rp} | {_n(u)} | "
            f"{_n(om)} / {_n(oe)} / {_n(ou)} | {_n(rq)} / {_n(tq)} | {_n(kp)}{weak} | {_n(rpx)} px / {_n(rm)} m | {best} |")


def total_row(vals: list[list], lang: str) -> str:
    s = [sum(v[i] or 0 for v in vals) for i in range(14)]
    label = "計" if lang == "ja" else "Total"
    weak = (f"（弱い合格 {_n(s[13])}）" if lang == "ja" else f" (+{_n(s[13])} weak)") if s[13] else ""
    lp, rp = ("（", "）") if lang == "ja" else (" (", ")")
    return (f"| {label} | {_n(s[0])} | {_n(s[1])} | {_n(s[2])}{lp}{_n(s[3])}{rp} | {_n(s[4])}{lp}{_n(s[5])}{rp} | {_n(s[6])} | "
            f"{_n(s[7])} / {_n(s[8])} / {_n(s[9])} | {_n(s[10])} / {_n(s[11])} | {_n(s[12])}{weak} | | |")


HEAD = {
    "ja": ("| 棟 | 棟に振り分けた写真 | 視点を解いた写真 | measured（うち長さ） | estimated（うち仮説） | unknown | 独立した観察 measured / estimated / unknown | 撮影依頼 open 職員宛て / 道具宛て | 検収で合格した面 | 再投影の中央値 全体（px / m） | 同 面ごとの最良（px / m） |\n"
           "|---|---|---|---|---|---|---|---|---|---|---|"),
    "en": ("| Building | Photos assigned | Photos with a solved camera | `measured` (of which lengths) | `estimated` (of which hypothesis) | `unknown` | Independent observations m / e / u | Open requests to staff / to tools | Faces passing inspection | Reprojection median, all (px / m) | Same, best face (px / m) |\n"
           "|---|---|---|---|---|---|---|---|---|---|---|"),
}


def photo_split(bids: list[str]) -> dict:
    """索引の写真のうち、表の棟に振り分けた数・敷地（棟と棟の間・地面）の数・棟を決めていない数。"""
    ph = json.loads((ROOT / "data" / "photos" / "index.json").read_text(encoding="utf-8")).get("photos", [])
    return {"total": len(ph), "assigned": sum(p.get("building") in bids for p in ph),
            "site": sum(p.get("building") == "site" for p in ph),
            "none": sum(not p.get("building") for p in ph),
            "other": sum(bool(p.get("building")) and p.get("building") not in bids and p.get("building") != "site" for p in ph)}


def block(lang: str, bids: list[str], reps: dict, vals: dict, when: str, split: dict | None = None) -> str:
    rows = [row(name_of(b) if lang == "ja" else NAME_EN.get(b, b), vals[b], lang) for b in bids]
    sp = split or {}
    if lang == "ja":
        intro = (f"**今の値**（{when} 時点。`out/<棟>/report.json` と `recorder counts --json` から "
                 "`scripts/readme_numbers.py` が書いた表。手で直さない）")
        notes = [
            "- 「棟に振り分けた写真」は索引でその棟に振り分けた写真の数（11/7 夜に取り込んだメジャーの写真を含む）。「視点を解いた写真」は登録台帳で視点が解けた数。",
            *([f"- 索引の写真 {sp['total']} 枚と表の計 {sp['assigned']} 枚の差 {sp['total'] - sp['assigned']} 枚は、敷地（棟と棟の間・地面・通路）に振り分けた {sp['site']} 枚と、棟を決めていない {sp['none']} 枚"
               + (f"、ほかの棟 {sp['other']} 枚" if sp.get("other") else "") + "（屋上からの眺め・棟の決まらないメジャーの写真など）です。"] if sp else []),
            "- 「measured（うち長さ）」の長さは、写真で直接測った値と、メジャーから測った輪がつながる鎖の値の数（`recorder counts` の direct＋chain）。残りは数（階数・柱間の数・台数）。",
            "- 「独立した観察」は、同じ状態・方法・根拠・値の種類の写しを 1 つに束ねた数。",
            "- 「道具宛て」は職員に撮ってもらう物ではなく、道具（recorder）や係が直す・調べる作業の票。",
            "- 「検収で合格した面」は検収係のまとめ（`data/reports/<棟>/kenshu_summary.json`）の、面ごとに最新の回の判定で合格の面（上の faces は手で書く欄で古いままの事があるので、一番新しい round_* の判定を使う。`report.kenshu_faces`）。合否は**面ごと**に、その面に登録した写真の上で模型の線（柱型の縁・窓の上下・帯）の近く ±0.5 m に写真の縁を探し、見つかった線のずれが全部 0.15 m 以内なら合格（1 枚だけ外れた時は縁の取り違えを先に疑い、他の写真で合っていれば合格、理由を書く）。弱い合格は写真 1 枚・線が少ない合格。",
            "- 再投影の中央値は、目印係が升目で指した点と、解いた視点で模型の目印を写した点のずれ。m は ずれ px × 目印までの距離 ÷ 焦点距離 で直した物。面ごとの最良は点が 3 つ以上の面のうち一番小さい物で、全体と並べて書く。",
            "- **検収の 0.15 m と再投影の中央値は別の物です。** 再投影は視点を解く時に升目で指した点の粗さと、目印の取り違えを含む、写真の位置合わせの目安です。検収は、その視点の上で模型の線と写真の縁を線ごとに比べた差です。再投影の中央値が 0.15 m を超えていても（本部棟・タワー75）、線ごとの差が 0.15 m 以内の面は合格になります。",
        ]
    else:
        intro = (f"**Current values** (as of {when}; generated by `scripts/readme_numbers.py` from `out/<bldg>/report.json` "
                 "and `recorder counts --json`; do not edit by hand)")
        notes = [
            "- \"Photos assigned\" counts photos assigned to the building in the index (including the tape-measure photos imported on the night of 11/7). \"Photos with a solved camera\" counts photos whose pose was solved in the registry.",
            *([f"- The difference between the {sp['total']} photos in the index and the {sp['assigned']} in the table ({sp['total'] - sp['assigned']}) is {sp['site']} photos assigned to the site (between buildings, ground, paths) and {sp['none']} photos with no building decided"
               + (f", plus {sp['other']} for other buildings" if sp.get("other") else "") + " (views from roofs, tape-measure photos whose building is not decided, etc.)."] if sp else []),
            "- \"Of which lengths\" counts `measured` values read directly from a photo or through a chain that ends at a tape measure (`recorder counts`: direct + chain). The rest are counts (floors, bays, units).",
            "- \"Independent observations\" groups copies with the same status, method, evidence and value kind.",
            "- \"To tools\" are work tickets for the tool (`recorder`) or the agents, not photo requests to staff.",
            "- \"Faces passing inspection\" are faces whose latest verdict in the inspection agent's summary (`data/reports/<bldg>/kenshu_summary.json`) is a pass: per face, the newest `round_*` verdict is used, and the top-level `faces` (written by hand, sometimes stale) counts as the oldest round (`report.kenshu_faces`). The verdict is **per face**: on the photos registered to that face, the tool searches ±0.5 m around each model line (pier edges, window tops and bottoms, bands) for a photo edge, and the face passes if every found line is within 0.15 m (if one photo alone fails, an edge mix-up is suspected first and the face passes when the other photos agree, with the reason written down). \"Weak\" passes rest on one photo or few lines.",
            "- Reprojection median: the distance between where a landmark agent pointed on the grid and where the solved camera projects the model landmark. m = px × distance to the landmark ÷ focal length in px. The best face (faces with 3+ points) is shown next to the overall median, never alone.",
            "- **The 0.15 m inspection threshold and the reprojection median are different things.** Reprojection reflects how coarse the grid pointing is when the camera is solved, including landmark mix-ups; it is a guide to photo alignment. Inspection compares, line by line on that camera, the model's lines with the photo's edges. A face can pass (all lines within 0.15 m) even where the reprojection median is above 0.15 m (main building, Tower 75).",
        ]
    return "\n".join([START, intro, "", HEAD[lang], *rows, total_row([vals[b] for b in bids], lang), "", *notes, END])


# --- 照合 -------------------------------------------------------------------------------------

_NUM = re.compile(r"—|-?\d[\d,]*(?:\.\d+)?")


def parse_row(line: str) -> list[float | None]:
    cells = [c.strip() for c in line.strip().strip("|").split("|")][1:]
    out: list[float | None] = []
    for i, c in enumerate(cells):
        nums = [None if t == "—" else float(t.replace(",", "")) for t in _NUM.findall(c)]
        if i == 7 and len(nums) == 1:          # 検収（8 列目）：弱い合格が無ければ 0
            nums.append(0.0)
        out += nums
    return out


def check(lang: str, bids: list[str], vals: dict) -> list[str]:
    text = READMES[lang].read_text(encoding="utf-8")
    if text.count(START) != 1 or text.count(END) != 1:
        return [f"{READMES[lang].name}: 印が 1 組ではない"]
    body = text[text.index(START):text.index(END)]
    lines = [ln for ln in body.splitlines() if ln.startswith("| ") and not ln.startswith("|---")][1:]   # 見出しを除く
    diffs = []
    for b, ln in zip(bids, lines):
        got = parse_row(ln)
        want = [None if x is None else float(x) for x in vals[b]]
        if len(got) != len(want):
            diffs.append(f"{READMES[lang].name} {b}: 数の個数 {len(got)} ≠ {len(want)}")
            continue
        for i, (g, w) in enumerate(zip(got, want)):
            if g != w:
                diffs.append(f"{READMES[lang].name} {b}: 列 {i} README {g} ≠ report {w}")
    if len(lines) < len(bids):
        diffs.append(f"{READMES[lang].name}: 行が {len(lines)}（棟 {len(bids)}）")
    # 印の外に棟の表の行が残っていないか（手で書いた数字を残さない）
    outside = text[:text.index(START)] + text[text.index(END):]
    for b in bids:
        label = name_of(b) if lang == "ja" else NAME_EN.get(b, b)
        if re.search(rf"^\| {re.escape(label)} \| \d", outside, re.M):
            diffs.append(f"{READMES[lang].name}: 印の外に {label} の数字の行がある")
    return diffs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refresh", action="store_true", help="4 棟とも recorder report で出し直す")
    ap.add_argument("--check", action="store_true", help="書かずに照合だけ")
    a = ap.parse_args(argv)
    bids = targets()
    reps = load_reports(bids, a.refresh and not a.check)
    cnts = load_counts(bids)
    bad = [x for b in bids for x in consistency(b, reps[b], cnts[b])]
    if bad:
        print("report.json と counts --json が食い違う（表は書かない）：\n  " + "\n  ".join(bad))
        return 1
    vals = {b: numbers(reps[b], cnts[b]) for b in bids}
    when = max(reps[b]["made_at"] for b in bids).replace("T", " ")[:16]
    if not a.check:
        for lang, p in READMES.items():
            text = p.read_text(encoding="utf-8")
            if text.count(START) != 1 or text.count(END) != 1:
                print(f"{p.name}: 印 {START} 〜 {END} が 1 組ではない")
                return 1
            new = text[:text.index(START)] + block(lang, bids, reps, vals, when, photo_split(bids)) + text[text.index(END) + len(END):]
            if new != text:
                p.write_text(new, encoding="utf-8")
                print(f"書いた：{p.name}")
    diffs = [x for lang in READMES for x in check(lang, bids, vals)]
    n = sum(len(v) for v in vals.values())
    print(f"照合：{len(bids)} 棟 × {len(next(iter(vals.values())))} 列 × 日英 2 = {2 * n} 個の数字、差 {len(diffs)}（{when} の report.json）")
    for x in diffs:
        print("  " + x)
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
