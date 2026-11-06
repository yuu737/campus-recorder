"""試走の出発点を作る／戻す。その棟の「人の作業の結果」を trial/<id>/<日時>/ へ退避し、本番と同じ出発点にする。

  python -I -X utf8 scripts/trial_reset.py <棟id>              退避（trial/<id>/<日時>/manifest.json に一覧）
  python -I -X utf8 scripts/trial_reset.py <棟id> --restore    最新の退避を戻す（試走の結果は同じ場所の after/ へ）
  python -I -X utf8 scripts/trial_reset.py <棟id> --clear-building   索引の building も空にする（台帳由来 source の物は残す）
  python -I -X utf8 scripts/trial_reset.py --zero              全部の棟を「写真と職員の振り分けだけ」の出発点に（11/7。下の zero()）

退避する物：scripts/design_<id>.py、data/buildings/<id>.json、data/reports/<id>/（係の報告。11/7 に追加。残すと係が前の結論を読める）、
out/<id>/（丸ごと）、out/<id>_*（作業の残り）、
out/rectified/<id>_*、out/rectified/*/<その棟の写真id>_front*、out/photos/shiwake_<id>*.json、web/data/models/<id>.glb、
data/photos/registry.json のその棟の entries と not_registered、data/photos/index.json のその棟の写真の face / faces。
残す物：道具、係の定義、手順書、data/site/tempaku.json、data/rulers.json、data/assets、他の棟、out/site/。
注意：git の履歴と docs/ には前の結果が残っている（係に見せない運用で補う）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

LEDGER = "台帳(期間前)"
FIELDS = ("building", "face", "faces", "face_source")   # 仕分けが書く索引の項目


def _rel(p: Path, root: Path) -> str:
    return str(p.relative_to(root)).replace("\\", "/")


def _photo_ids(index: dict, bid: str) -> list[str]:
    return [p["id"] for p in index.get("photos", []) if p.get("building") == bid]


def targets(bid: str, root: Path, photo_ids: list[str]) -> list[str]:
    """退避の対象（今ある物だけ。root からの相対パス）。"""
    cand: list[Path] = [root / "scripts" / f"design_{bid}.py", root / "data/buildings" / f"{bid}.json",
                        root / "data/reports" / bid, root / "out" / bid, root / "web/data/models" / f"{bid}.glb"]
    out = root / "out"
    if out.is_dir():
        cand += sorted(out.glob(f"{bid}_*"))
        cand += sorted((out / "rectified").glob(f"{bid}_*")) if (out / "rectified").is_dir() else []
        cand += sorted((out / "photos").glob(f"shiwake_{bid}*.json")) if (out / "photos").is_dir() else []
        ids = set(photo_ids)
        if ids and (out / "rectified").is_dir():
            for f in sorted((out / "rectified").glob("*/*_front*")):
                if f.is_file() and f.name.split("_front")[0] in ids:
                    cand.append(f)
    seen, res = set(), []
    for p in cand:
        if p.exists():
            r = _rel(p, root)
            if r not in seen:
                seen.add(r)
                res.append(r)
    return res


def _move(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def _load(p: Path, default: dict) -> dict:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _save(p: Path, doc: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def _open_trials(bid: str, root: Path) -> list[Path]:
    d = root / "trial" / bid
    res = []
    for m in sorted(d.glob("*/manifest.json")) if d.is_dir() else []:
        if not json.loads(m.read_text(encoding="utf-8")).get("restored"):
            res.append(m)
    return res


def reset(bid: str, root: str | Path = ".", *, clear_building: bool = False, stamp: str | None = None) -> dict:
    root = Path(root)
    if _open_trials(bid, root):
        raise SystemExit(f"{bid} は戻していない退避があります（{_open_trials(bid, root)[-1].parent}）。先に --restore")
    stamp = stamp or f"{datetime.now():%Y%m%d_%H%M%S}"
    dest = root / "trial" / bid / stamp
    dest.mkdir(parents=True, exist_ok=False)
    idx_p, reg_p = root / "data/photos/index.json", root / "data/photos/registry.json"
    index = _load(idx_p, {"photos": []})
    pids = _photo_ids(index, bid)

    moved = targets(bid, root, pids)
    for r in moved:
        _move(root / r, dest / "files" / r)

    # 登録の台帳：その棟の entries・not_registered を抜く
    reg_info = {"entries": 0, "not_registered": 0}
    if reg_p.exists():
        reg = _load(reg_p, {})
        shutil.copyfile(reg_p, dest / "registry_before.json")
        saved = {}
        for k in ("entries", "not_registered"):
            items = reg.get(k, [])
            saved[k] = [e for e in items if e.get("building") == bid]
            reg[k] = [e for e in items if e.get("building") != bid]
            reg_info[k] = len(saved[k])
        _save(dest / f"registry_{bid}.json", saved)
        _save(reg_p, reg)

    # 写真の索引：その棟の写真の face / faces（台帳由来の face は残す）。building は台帳由来なので既定では残す
    cleared: dict[str, dict] = {}
    if idx_p.exists():
        shutil.copyfile(idx_p, dest / "index_before.json")
        for p in index.get("photos", []):
            if p.get("building") != bid:
                continue
            keep = {}
            if p.get("face") is not None and p.get("face_source") != LEDGER:
                keep["face"] = p["face"]
                p["face"] = None
            if "faces" in p:
                keep["faces"] = p.pop("faces")
            if clear_building and p.get("source") != LEDGER and p.get("building_source") != LEDGER:
                keep["building"] = p["building"]
                p["building"] = None
            if keep:
                cleared[p["id"]] = keep
        _save(idx_p, index)

    man = {"building": bid, "stamp": stamp, "made_at": datetime.now().isoformat(timespec="seconds"),
           "moved": moved, "registry": reg_info, "index_cleared": cleared,
           "index_ids_before": [p["id"] for p in index.get("photos", [])],
           "clear_building": clear_building, "restored": False,
           "kept": ["recorder/", ".claude/agents/", "docs/", "data/site/tempaku.json", "data/rulers.json", "data/assets/",
                    "他の棟", "out/site/"]}
    _save(dest / "manifest.json", man)
    return {"dir": str(dest), "moved": len(moved), "registry": reg_info, "index_cleared": len(cleared)}


def restore(bid: str, root: str | Path = ".", *, stamp: str | None = None) -> dict:
    root = Path(root)
    if stamp:
        mp = root / "trial" / bid / stamp / "manifest.json"
    else:
        opens = _open_trials(bid, root)
        if not opens:
            raise SystemExit(f"{bid} に戻す退避がありません")
        mp = opens[-1]
    man = json.loads(mp.read_text(encoding="utf-8"))
    if man.get("restored"):
        raise SystemExit(f"{mp.parent} は戻し済み")
    dest = mp.parent
    after = dest / "after"
    idx_p, reg_p = root / "data/photos/index.json", root / "data/photos/registry.json"
    index = _load(idx_p, {"photos": []})

    # 試走の結果（同じ種類の物で今ある物）を after/ へ、退避を元の場所へ
    now = targets(bid, root, _photo_ids(index, bid) + list(man["index_cleared"]))
    for r in now:
        _move(root / r, after / "files" / r)
    for r in man["moved"]:
        _move(dest / "files" / r, root / r)

    if reg_p.exists() or (dest / f"registry_{bid}.json").exists():
        reg = _load(reg_p, {"entries": [], "not_registered": []})
        if reg_p.exists():
            after.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(reg_p, after / "registry_after.json")
        saved = _load(dest / f"registry_{bid}.json", {})
        orig = _load(dest / "registry_before.json", {})
        for k in ("entries", "not_registered"):
            pos = {json.dumps(e, sort_keys=True, ensure_ascii=False): i for i, e in enumerate(orig.get(k, []))}
            merged = [e for e in reg.get(k, []) if e.get("building") != bid] + saved.get(k, [])
            merged.sort(key=lambda e: pos.get(json.dumps(e, sort_keys=True, ensure_ascii=False), len(pos)))   # 元の並び
            reg[k] = merged
        _save(reg_p, reg)

    removed: list[str] = []
    if idx_p.exists():
        after.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(idx_p, after / "index_after.json")
        before = {p["id"]: p for p in _load(dest / "index_before.json", {"photos": []}).get("photos", [])}
        photos = []
        for p in index.get("photos", []):
            if p["id"] not in before:              # 試走で取り込んだ写真（index_after.json に残る）
                removed.append(p["id"])
                continue
            for k in FIELDS:                       # 仕分けの項目だけ退避前に戻す（他の項目は今のまま）
                if k in before[p["id"]]:
                    p[k] = before[p["id"]][k]
                else:
                    p.pop(k, None)
            photos.append(p)
        index["photos"] = photos
        _save(idx_p, index)

    man.update(restored=True, restored_at=datetime.now().isoformat(timespec="seconds"),
               after=[*now], index_removed=removed)
    _save(mp, man)
    return {"dir": str(dest), "restored": len(man["moved"]), "trial_results_to_after": len(now), "index_removed": len(removed)}


# --- 0 から：写真と職員の振り分けだけを残す（11/7 の疑似本番） --------------------------------------------
# 索引の 1 枚に残す項目：取り込み（コード）が書いた物と、職員が書いた物だけ。係（仕分け・判断役）の判断は消す。
KEEP_PHOTO = ("id", "file", "taken_at", "period", "camera", "width", "height", "orientation", "focal_mm", "focal_35mm",
              "gps", "heading_deg", "heading_ref", "checks", "candidates", "imported_at", "staff_label", "staff_choices")
STAFF_KEEP = ("building", "building_source", "assigned_by", "assigned_at", "interior", "floor")   # 職員が振り分けた写真だけ


def _staff_notes(notes: str | None) -> str | None:
    """notes から職員・取り込みが書いた部分（「職員」で始まる区切り）だけを残す。係のメモは消す。"""
    import re
    keep = [s.strip() for s in re.split(r" \| | / ", str(notes or "")) if s.strip().startswith("職員")]
    return " | ".join(keep) or None


INTAKE_PROXY = "intake_proxy"   # 取り込みの時に付いた棟（職員の振り分けの代わり）。11/3 の写真は判断役（Claude）が付けた
PRIOR_SORT = "prior_sort"       # 職員が候補だけ示した写真で、前の仕分け係が候補から選んだ棟（職員の振り分けの代わり）


def zero_photo(p: dict) -> dict:
    """索引の 1 枚を 0 からの形に。棟を残すのは 2 種類だけ：
    - 職員の振り分け（assigned_by / building_source が staff）
    - 取り込みの時に付いた棟（係がまだ誰も触っていない＝assigned_by が無く、職員の候補 staff_choices も無い）。
      11/3 の写真は職員の画面より前で、判断役（Claude）が取り込みの時に付けた。本番では職員が棟ごとに入れる所なので、
      「職員の振り分けの代わり」として building_source を intake_proxy にして残す（11/7 の利用者の判断）。
    仕分け係が決めた棟（assigned_by shiwake）と、職員が候補だけ示した写真（staff_choices）は空に戻す。"""
    q = {k: p[k] for k in KEEP_PHOTO if k in p}
    staff = p.get("assigned_by") == "staff" or p.get("building_source") == "staff"
    proxy = (not staff and p.get("building") and not p.get("assigned_by") and not p.get("staff_choices"))
    # 職員が候補だけ示した写真で、前の仕分け係が候補の中から選んだ棟（11/7 の利用者の判断「棟は全部渡す」。面は渡さない）
    prior = (not staff and not proxy and p.get("assigned_by") == "shiwake" and p.get("building")
             and (not p.get("staff_choices") or p["building"] in p["staff_choices"]))
    if staff:
        q.update({k: p[k] for k in STAFF_KEEP if k in p})
        if p.get("interior") is True:
            q["interior_views"] = True                 # 内装は面にしない（職員が内装と決めた物）
    elif proxy or prior:
        q["building"], q["building_source"] = p["building"], (INTAKE_PROXY if proxy else PRIOR_SORT)
        q["notes_intake"] = ("取り込みの時に付いた棟（職員の振り分けの代わり。11/3 は判断役が付けた）" if proxy else
                             "職員が示した候補から、前の仕分け係（11/6）が選んだ棟（職員の振り分けの代わり。面は渡さない）")
        for k in ("interior", "floor"):
            if k in p:
                q[k] = p[k]
    else:
        q["building"] = None
        if "interior" in p:                            # 外観／内装はフォルダで職員が決める物（無ければ取り込みの既定）
            q["interior"] = p["interior"]
        if "floor" in p:
            q["floor"] = p["floor"]
    q["face"] = None
    n = _staff_notes(p.get("notes"))
    if n:
        q["notes"] = n
    return q


def zero(root: str | Path = ".", *, buildings: list[str] | None = None, stamp: str | None = None) -> dict:
    """全部の棟の結果と、係・進行役の判断を trial/zero/<日時>/ へ退避し、写真と職員の振り分けだけの出発点にする。

    退避：範囲の棟（敷地 JSON の scope.targets。buildings で上書き可）ごとの reset() と同じ物、
    out/rectified/・out/photos/（全部）、登録台帳の全件、進行役の決定 data/decisions.json（空の台帳にする）、
    索引の係の判断（職員以外の棟の割り当て・面・メモ・確からしさ）。
    残す：写真の索引の取り込み情報と職員の振り分け、敷地 JSON、物差し台帳、語彙、CC0 の台帳、道具、手順書。
    戻す道具は無い（戻したい時は trial/zero/<日時>/ から手で）。"""
    root = Path(root)
    stamp = stamp or f"{datetime.now():%Y%m%d_%H%M%S}"
    dest = root / "trial" / "zero" / stamp
    dest.mkdir(parents=True, exist_ok=False)
    site_p = root / "data/site/tempaku.json"
    if buildings is None:
        site = _load(site_p, {})
        buildings = list((site.get("scope") or {}).get("targets") or [])
    idx_p, reg_p, dec_p = root / "data/photos/index.json", root / "data/photos/registry.json", root / "data/decisions.json"
    index = _load(idx_p, {"photos": []})

    moved: list[str] = []
    for bid in buildings:
        for r in targets(bid, root, _photo_ids(index, bid)):
            if (root / r).exists():
                _move(root / r, dest / "files" / r)
                moved.append(r)
    for extra in ("out/rectified", "out/photos"):
        if (root / extra).exists():
            _move(root / extra, dest / "files" / extra)
            moved.append(extra)

    reg_info = {}
    if reg_p.exists():
        shutil.copyfile(reg_p, dest / "registry_before.json")
        reg = _load(reg_p, {})
        for k in ("entries", "not_registered"):
            reg_info[k] = len(reg.get(k, []))
            reg[k] = []
        _save(reg_p, reg)

    dec_info = 0
    if dec_p.exists():
        shutil.copyfile(dec_p, dest / "decisions_before.json")
        dec = _load(dec_p, {})
        if isinstance(dec, dict) and isinstance(dec.get("decisions"), list):
            dec_info = len(dec["decisions"])
            dec["decisions"] = []
            _save(dec_p, dec)

    idx_info = {"photos": 0, "staff_building": 0, "building_cleared": 0, "face_cleared": 0, "notes_cleared": 0}
    if idx_p.exists():
        shutil.copyfile(idx_p, dest / "index_before.json")
        photos = []
        for p in index.get("photos", []):
            q = zero_photo(p)
            idx_info["photos"] += 1
            idx_info["staff_building"] += bool(q.get("building"))
            idx_info["building_cleared"] += bool(p.get("building")) and not q.get("building")
            idx_info["face_cleared"] += bool(p.get("face") or p.get("faces"))
            idx_info["notes_cleared"] += (p.get("notes") or None) != q.get("notes")
            photos.append(q)
        index["photos"] = photos
        _save(idx_p, index)

    man = {"kind": "zero", "stamp": stamp, "made_at": datetime.now().isoformat(timespec="seconds"),
           "buildings": buildings, "moved": moved, "registry": reg_info, "decisions": dec_info, "index": idx_info,
           "kept": ["写真の索引の取り込み情報と職員の振り分け", "data/site/tempaku.json", "data/rulers.json", "data/vocab.json",
                    "data/assets/", "recorder/", ".claude/agents/", "docs/", "out/site/"],
           "note": "係は trial/・docs/過程/・docs/日誌.md・git の履歴を見ない（前の答えが入っている）"}
    _save(dest / "manifest.json", man)
    return {"dir": str(dest), "buildings": buildings, "moved": len(moved), "registry": reg_info, "decisions": dec_info,
            "index": idx_info}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="trial_reset")
    ap.add_argument("building", nargs="?")
    ap.add_argument("--zero", action="store_true", help="全部の棟を写真と職員の振り分けだけの出発点に（trial/zero/<日時>/）")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--stamp", default=None, help="戻す退避の日時（省略で最新の未復元）")
    ap.add_argument("--clear-building", action="store_true", help="索引の building も空にする（台帳由来は残す）")
    ap.add_argument("--root", default=".")
    a = ap.parse_args(argv)
    if a.zero:
        r = zero(a.root, stamp=a.stamp)
    elif not a.building:
        ap.error("棟 id か --zero を指定してください")
    else:
        r = restore(a.building, a.root, stamp=a.stamp) if a.restore else reset(a.building, a.root, clear_building=a.clear_building)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
