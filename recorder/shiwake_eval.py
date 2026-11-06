"""仕分け係の当たり率：期間前の写真で、仕分け係が目視で確定した棟（と面）が、過去の分類台帳の棟と合っているか。

`python -m recorder shiwake-eval [--index data/photos/index.json] [--campus tempaku] [--out out/shiwake_eval.json]`

比べ方（索引 data/photos/index.json だけを読む。写真は開かない）：
  対象  … 台帳の記録（`ledger`、`source: "台帳(期間前)"`）がある写真のうち、仕分け係が見た物
          （`assigned_by: "shiwake"` か、notes に署名「仕分け係（…）」）。
  台帳の棟 … 取り込み時と同じ規則（photos.ledger_fields）：台帳のフォルダ名＋場所名を敷地 JSON の別名表に当て、
          棟が 1 つに決まった時だけ。索引の `building` は仕分け係が上書きするので、台帳の値はここで引き直す。
  仕分け係の棟・面 … 索引の `building` / `face`。
  一致     … 棟が同じ（台帳に面があれば面も同じ）。
  不一致   … 棟が違う（台帳に面があり、棟は同じで面が違う時も）。
  判定不能 … 仕分け係が棟を決めなかった（建物が写らない等で `building: null`）か、台帳の場所名から棟が 1 つに決まらない。
当たり率 = 一致 / （一致＋不一致）。判定不能は分母に入れない（件数と理由は別に出す）。
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Callable

from . import photos

SCHEMA = "campus-recorder/shiwake-eval/1"
OUT_PATH = Path("out/shiwake_eval.json")
NO_LEDGER_BUILDING = "（台帳で棟が決まらない）"

R_SHIWAKE_NONE = "仕分け係が棟を決めなかった（building: null）"
R_LEDGER_NONE = "台帳の場所名から棟が 1 つに決まらない"


def is_shiwake(p: dict) -> bool:
    """仕分け係が見た写真か（assigned_by: shiwake、または notes に仕分け係の署名「仕分け係（opus…）」）。
    「仕分け係が選ぶ」のような依頼の文は見た印にしない。"""
    return p.get("assigned_by") == "shiwake" or "仕分け係（" in str(p.get("notes") or "")


def ledger_name(p: dict) -> str:
    """台帳の棟を引く文字列（取り込み時の `folder + location` と同じ。location は ledger_subject に残っている）。"""
    lg = p.get("ledger") or {}
    return f"{lg.get('folder') or ''} {p.get('ledger_subject') or ''}".strip()


def ledger_face(p: dict) -> str | None:
    """台帳の面。今の台帳には面が無い（ledger.face があれば使う）。"""
    return (p.get("ledger") or {}).get("face")


def evaluate(idx: dict, ledger_building: Callable[[str], str | None]) -> dict[str, Any]:
    """索引から一致・不一致・判定不能を数える。ledger_building は 場所名 → 棟 id（決まらなければ None）。"""
    rows = idx.get("photos", [])
    reviewed = [p for p in rows if is_shiwake(p)]
    targets = [p for p in reviewed if p.get("ledger")]
    by_b: dict[str, dict[str, int]] = {}
    mismatches: list[dict] = []
    undetermined: list[dict] = []
    reasons: dict[str, int] = {}
    n_match = n_mis = n_face = n_face_match = 0
    for p in targets:
        lb = ledger_building(ledger_name(p))
        lf = ledger_face(p)
        sb, sf = p.get("building"), p.get("face")
        key = lb or NO_LEDGER_BUILDING
        t = by_b.setdefault(key, {"match": 0, "mismatch": 0, "undetermined": 0})
        if sb is None or lb is None:
            reason = R_SHIWAKE_NONE if sb is None else R_LEDGER_NONE
            reasons[reason] = reasons.get(reason, 0) + 1
            t["undetermined"] += 1
            undetermined.append({"id": p["id"], "reason": reason, "ledger_building": lb, "shiwake_building": sb,
                                 "ledger_folder": (p.get("ledger") or {}).get("folder")})
            continue
        ok = sb == lb
        if ok and lf is not None:          # 台帳に面がある時だけ面も比べる
            n_face += 1
            ok = sf == lf
            n_face_match += 1 if ok else 0
        if ok:
            n_match += 1
            t["match"] += 1
        else:
            n_mis += 1
            t["mismatch"] += 1
            mismatches.append({"id": p["id"], "ledger_building": lb, "shiwake_building": sb,
                               "ledger_face": lf, "shiwake_face": sf,
                               "ledger_folder": (p.get("ledger") or {}).get("folder"),
                               "notes": p.get("notes") or ""})
    compared = n_match + n_mis
    table = []
    for b in sorted(by_b, key=lambda k: (k == NO_LEDGER_BUILDING, k)):
        t = by_b[b]
        c = t["match"] + t["mismatch"]
        table.append({"ledger_building": b, "compared": c, **t,
                      "rate": round(t["match"] / c, 4) if c else None})
    return {
        "counts": {
            "photos": len(rows),
            "shiwake_reviewed": len(reviewed),
            "with_ledger": len(targets),
            "no_ledger": len(reviewed) - len(targets),
            "match": n_match, "mismatch": n_mis, "undetermined": len(undetermined),
            "compared": compared,
            "rate": round(n_match / compared, 4) if compared else None,
            "face_compared": n_face, "face_match": n_face_match,
        },
        "undetermined_reasons": reasons,
        "by_building": table,
        "mismatches": mismatches,
        "undetermined": undetermined,
    }


def run(index_path: Path = photos.INDEX_PATH, campus: str | None = None, out: Path | None = OUT_PATH) -> dict:
    """索引を読んで評価し、out に JSON を書く。campus を省くと既定の敷地。"""
    from . import site

    campus = campus or site.default_campus()
    idx = photos.load_index(Path(index_path))
    res = {
        "schema": SCHEMA,
        "made_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "index": str(index_path).replace("\\", "/"),
        "campus": campus,
        "rule": ("対象は台帳(期間前)の記録があり仕分け係が見た写真。台帳の棟は台帳のフォルダ名＋場所名を別名表に当てて 1 つに"
                 "決まった物、仕分け係の棟は索引の building。台帳に面があれば面も比べる（今の台帳には面が無いので棟だけ）。"
                 "当たり率は 一致 /（一致＋不一致）、判定不能は分母に入れない"),
        **evaluate(idx, lambda name: photos.ledger_building(name, campus)),
    }
    if out is not None:
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def summary_lines(res: dict) -> list[str]:
    c = res["counts"]
    rate = f"{c['rate'] * 100:.1f}%" if c["rate"] is not None else "—（比べられる写真が無い）"
    lines = [f"仕分け係の当たり率（棟{'・面' if c['face_compared'] else ''}）：一致 {c['match']} / 比較できた {c['compared']} = {rate}",
             f"  仕分け係が見た写真 {c['shiwake_reviewed']} 枚（台帳あり {c['with_ledger']}、台帳なし {c['no_ledger']} は対象外）"
             f"、不一致 {c['mismatch']}、判定不能 {c['undetermined']}"]
    for r, n in res["undetermined_reasons"].items():
        lines.append(f"  判定不能の理由：{r} … {n}")
    lines.append("  台帳の棟        比較  一致  不一致  判定不能  一致率")
    for t in res["by_building"]:
        r = f"{t['rate'] * 100:.1f}%" if t["rate"] is not None else "—"
        lines.append(f"  {t['ledger_building']:<14}{t['compared']:>5}{t['match']:>6}{t['mismatch']:>8}{t['undetermined']:>10}  {r}")
    for m in res["mismatches"]:
        lines.append(f"  不一致：{m['id']}  台帳 {m['ledger_building']}{'/' + m['ledger_face'] if m['ledger_face'] else ''}"
                     f" → 仕分け係 {m['shiwake_building']}{'/' + m['shiwake_face'] if m['shiwake_face'] else ''}")
    return lines


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--index", type=Path, default=photos.INDEX_PATH, help="写真の索引（既定 data/photos/index.json）")
    p.add_argument("--campus", default=None, help="敷地（別名表の在りか。既定は data/site の既定の敷地）")
    p.add_argument("--out", type=Path, default=OUT_PATH, help="結果の JSON（既定 out/shiwake_eval.json）")


def run_cli(a: argparse.Namespace) -> int:
    res = run(a.index, a.campus, a.out)
    print("\n".join(summary_lines(res)))
    print(f"→ {a.out}")
    return 0
