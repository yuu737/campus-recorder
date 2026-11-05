"""進行役（shinko＝Fable）の決定の台帳 `data/decisions.json` と、語彙の台帳 `data/vocab.json`。

語彙（部品の型）とアセットの追加は、ここに approve の決定が無いと棟 JSON の検証（validate）と
`assets fetch`・台帳への追加が受け付けない。決定は `python -m recorder agent decide` で書く（手で直さない）。

台帳の形：{"schema": "campus-recorder/decisions/1", "decisions": [{"id": "D-0001", "kind": "vocab"|"asset",
          "target", "decision": "approve"|"deny", "by", "at", "why", "building", "request", "scope"?}]}
語彙の台帳は「型の名前の一覧」だけを持つ（形は recorder/parts_geom.py など）。
bpy を使わない純 Python。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DECISIONS = ROOT / "data" / "decisions.json"   # テストで差し替える
VOCAB = ROOT / "data" / "vocab.json"           # テストで差し替える

KINDS = ("vocab", "asset")
VERDICTS = ("approve", "deny")
SCHEMA = "campus-recorder/decisions/1"
NEED = "進行役の決定が要る"


def _jload(p: Path) -> dict:
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# --- 決定の台帳 --------------------------------------------------------------------

def load_decisions(path: str | Path | None = None) -> dict:
    d = _jload(Path(path) if path else DECISIONS)
    d.setdefault("schema", SCHEMA)
    d.setdefault("decisions", [])
    return d


def find(did: str | None, doc: dict | None = None) -> dict | None:
    if not did:
        return None
    for d in (doc if doc is not None else load_decisions()).get("decisions", []):
        if d.get("id") == did:
            return d
    return None


def approved(kind: str, target_substr: str | None = None, doc: dict | None = None) -> list[dict]:
    out = []
    for d in (doc if doc is not None else load_decisions()).get("decisions", []):
        if d.get("kind") == kind and d.get("decision") == "approve" and (not target_substr or target_substr in str(d.get("target", ""))):
            out.append(d)
    return out


def match_request(doc: dict, building: str, req: dict) -> dict | None:
    """requests[] の 1 件に対応する approve の決定（同じ building と request の文字列）。無ければ None。
    request の文字列は "kind:<kind> <what>" か what そのもののどちらでもよい。"""
    what = str(req.get("what") or "")
    keys = {what, f"kind:{req.get('kind')} {what}"}
    for d in doc.get("decisions", []):
        if d.get("decision") == "approve" and d.get("building") == building and d.get("request") in keys:
            return d
    return None


def add_decision(kind: str, target: str, decision: str, why: str, *, building: str | None = None,
                 request: str | None = None, scope: str | None = None, by: str = "shinko(fable)",
                 path: str | Path | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind は {'/'.join(KINDS)} です（{kind}）")
    if decision not in VERDICTS:
        raise ValueError(f"decision は {'/'.join(VERDICTS)} です（{decision}）")
    if not target or not why:
        raise ValueError("target と why は必須です（決定には理由を書く）")
    p = Path(path) if path else DECISIONS
    doc = load_decisions(p)
    nums = [int(str(d["id"])[2:]) for d in doc["decisions"] if str(d.get("id", "")).startswith("D-") and str(d["id"])[2:].isdigit()]
    rec = {"id": "D-%04d" % (max(nums, default=0) + 1), "kind": kind, "target": target, "decision": decision, "by": by,
           "at": datetime.now().astimezone().isoformat(timespec="seconds"), "why": why, "building": building, "request": request}
    if scope:
        rec["scope"] = scope
    doc["decisions"].append(rec)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return rec


def problem(did: str | None, what: str, doc: dict | None = None) -> str | None:
    """決定 id が台帳にあって approve か。そうでなければ理由（文）。"""
    if not did:
        return f"{what}: 決定（decision）がありません"
    d = find(did, doc)
    if d is None:
        return f"{what}: 決定 {did} が決定の台帳にありません"
    if d.get("decision") != "approve":
        return f"{what}: 決定 {did} は {d.get('decision')} です（承認されていません）"
    return None


# --- 語彙の台帳 --------------------------------------------------------------------

def load_vocab(path: str | Path | None = None) -> dict:
    v = _jload(Path(path) if path else VOCAB)
    v.setdefault("types", [])
    return v


def vocab_entry(t: str, vocab: dict | None = None) -> dict | None:
    for e in (vocab if vocab is not None else load_vocab()).get("types", []):
        if e.get("type") == t:
            return e
    return None


def vocab_list_lines() -> list[str]:
    doc = load_decisions()
    lines = []
    for e in load_vocab().get("types", []):
        p = problem(e.get("decision"), e.get("type", ""), doc)
        mark = "" if p is None else "  ※承認されていない"
        lines.append(f"{e.get('type', ''):<18} {e.get('decision', '-'):<7} {e.get('since', ''):<11} {e.get('what', '')}{mark}")
    return lines


# --- 棟 JSON の検証（recorder/schema.py の差し込み口から呼ばれる）-------------------------

def _walk_types(o, path: str, out: list):
    """type を持つ dict を再帰で全部（path, type, name）。"""
    if isinstance(o, dict):
        t = o.get("type")
        if isinstance(t, str):
            out.append((path, t, str(o.get("name") or o.get("id") or path)))
        for k, v in o.items():
            _walk_types(v, f"{path}.{k}", out)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            _walk_types(v, f"{path}[{i}]", out)


def _walk_assets(o, out: set):
    if isinstance(o, str):
        if o.startswith("asset:"):
            out.add(o[len("asset:"):])
    elif isinstance(o, dict):
        if o.get("type") == "asset" and isinstance(o.get("asset"), str):
            out.add(o["asset"])
        for v in o.values():
            _walk_assets(v, out)
    elif isinstance(o, list):
        for v in o:
            _walk_assets(v, out)


def _part_roots(b: dict):
    yield "parts", b.get("parts")
    for i, blk in enumerate(b.get("blocks") or []):
        if isinstance(blk, dict):
            yield f"blocks[{i}].parts", blk.get("parts")
    for i, fc in enumerate(b.get("faces") or []):
        if isinstance(fc, dict):
            yield f"faces[{i}].parts", fc.get("parts")


def validate_building_hook(b: dict) -> list[str]:
    errs: list[str] = []
    doc = load_decisions()
    vocab = load_vocab()

    def check_type(t: str, name: str, what: str):
        e = vocab_entry(t, vocab)
        if e is None:
            errs.append(f"{what} {name} の型 {t} は語彙の台帳（data/vocab.json）に無い（{NEED}：recorder agent decide --kind vocab …）")
            return
        p = problem(e.get("decision"), f"{what} {name} の型 {t}", doc)
        if p:
            errs.append(f"{p}（{NEED}：recorder agent decide --kind vocab …）")

    sources = list(_part_roots(b))
    for root, parts in sources:
        found: list = []
        _walk_types(parts, root, found)
        for _path, t, name in found:
            check_type(t, name, "部品")
        # composite の素片（shape）と cut
        stack = [parts]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                if o.get("type") == "composite":
                    for pr in o.get("primitives") or []:
                        if isinstance(pr, dict):
                            nm = str(pr.get("name") or o.get("name") or "composite")
                            if isinstance(pr.get("shape"), str):
                                check_type(pr["shape"], nm, "composite の素片")
                            if pr.get("cut"):
                                check_type("cut", nm, "composite の素片")
                stack.extend(o.values())
            elif isinstance(o, list):
                stack.extend(o)

    # アセット（material: asset:<id> と type: asset）
    ids: set[str] = set()
    for _root, parts in sources:
        _walk_assets(parts, ids)
    if ids:
        from . import assets

        try:
            cat = assets.load_catalog()
        except (OSError, ValueError):
            cat = {"items": []}
        by_id = {it.get("id"): it for it in cat.get("items", []) if isinstance(it, dict)}
        for aid in sorted(ids):
            it = by_id.get(aid)
            if it is None:
                errs.append(f"アセット {aid} は台帳（data/assets/catalog.json）に無い（{NEED}：recorder agent decide --kind asset …）")
                continue
            p = problem(it.get("decision"), f"アセット {aid}", doc)
            if p:
                errs.append(f"{p}（{NEED}：recorder agent decide --kind asset …）")

    if b.get("interior"):
        check_type("interior", "interior", "内装の欄")
    return errs
