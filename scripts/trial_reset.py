"""試走の出発点を作る／戻す。その棟の「人の作業の結果」を trial/<id>/<日時>/ へ退避し、本番と同じ出発点にする。

  python -I -X utf8 scripts/trial_reset.py <棟id>              退避（trial/<id>/<日時>/manifest.json に一覧）
  python -I -X utf8 scripts/trial_reset.py <棟id> --restore    最新の退避を戻す（試走の結果は同じ場所の after/ へ）
  python -I -X utf8 scripts/trial_reset.py <棟id> --clear-building   索引の building も空にする（台帳由来 source の物は残す）

退避する物：scripts/design_<id>.py、data/buildings/<id>.json、out/<id>/（丸ごと）、out/<id>_*（作業の残り）、
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
                        root / "out" / bid, root / "web/data/models" / f"{bid}.glb"]
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="trial_reset")
    ap.add_argument("building")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--stamp", default=None, help="戻す退避の日時（省略で最新の未復元）")
    ap.add_argument("--clear-building", action="store_true", help="索引の building も空にする（台帳由来は残す）")
    ap.add_argument("--root", default=".")
    a = ap.parse_args(argv)
    r = restore(a.building, a.root, stamp=a.stamp) if a.restore else reset(a.building, a.root, clear_building=a.clear_building)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
