"""提出用の公開版の写真 `recorder photos publish`（11/8、K-103・K-104）。

本番では写真は公開しない（歩く画面だけ）。提出用のリポジトリには、索引の写真を**研究室が分かる物以外、全部**、
縮小して入れる（「使っているのに無い」の食い違いを作らないため）。

- 出す所：`data/photos/public/<元のフォルダ>/<id>.jpg`（長辺 1600 px・JPEG 品質 80・EXIF なし）。
  元のフォルダは photos.photo_folder（例 20261105/tower75、メジャー/本部棟_honbu）。
- 塗る物：索引の `masks`（auto＝YuNet の顔、hogo＝保護係の升、review＝見直しの升）を灰色の箱＋理由の文字で。
  `crop` があれば切り落とし（箱を塗ってから切る）。顔の自動の箱がまだ無い写真は、ここで YuNet を当てて masks に足す。
- 出さない物：`public: false`（保護係・見直しが研究・研究室に関する物として外した。理由は public_reason）、
  期間前（period "before"）の写真、元の写真が見つからない物（missing。relink で付け直す）。
  外した写真の公開版が前の実行で出ていれば消す（自分が出した物だけ）。
- 既定では**保護係（hogo、Haiku）と見直し（review、Sonnet）の両方の記録がある写真だけ**出す（提出用の一回きりの作業なので
  安全側。11/8 Fable）。まだの写真は出さずに not_reviewed に数える。allow_unreviewed=True（--allow-unreviewed）で自動の箱だけでも出す。
- 索引に `public_file`（索引のフォルダからの相対）・`public_at` を書く。元の写真は読むだけ。
  縮小と顔の検出は鍵の外で回し、最後に鍵（ledger）を取って索引の差分だけ書く（photos.patch_index）。
"""

from __future__ import annotations

import copy
import time
from pathlib import Path

from . import photos, privacy

PUBLIC_DIRNAME = "public"


def publish(index_path: Path = photos.INDEX_PATH, *, out_dir: Path | None = None, ids: list[str] | None = None,
            long: int = 1600, quality: int = 80, dry_run: bool = False, photo_root: Path | None = None,
            now: str | None = None, allow_unreviewed: bool = False) -> dict:
    now = now or time.strftime("%Y-%m-%dT%H:%M:%S")
    index_path = Path(index_path)
    photo_root = Path(photo_root or index_path.parent)
    out_dir = Path(out_dir or index_path.parent / PUBLIC_DIRNAME)
    idx = photos.load_index(index_path)
    bases = {r["id"]: copy.deepcopy(r) for r in idx["photos"]}   # 鍵の外で縮小・顔の検出をし、最後に差分だけ書く
    res: dict = {"index": 0, "public": 0, "masked": 0, "boxes": {}, "cropped": 0, "excluded": [], "missing": [],
                 "before": 0, "not_reviewed": 0, "auto_now": 0, "warnings": [], "removed": 0, "out": out_dir.as_posix(),
                 "dry_run": dry_run, "allow_unreviewed": allow_unreviewed, "held": []}
    for rec in idx["photos"]:
        if ids and not any(rec["id"].startswith(i) for i in ids):
            continue
        res["index"] += 1
        old = rec.get("public_file")
        if rec.get("period") == "before":
            res["before"] += 1
            continue
        if rec.get("public") is False:
            res["excluded"].append({"id": rec["id"], "reason": rec.get("public_reason")})
            if old and not dry_run:
                f = photo_root / old
                if f.exists() and _inside(f, out_dir):
                    f.unlink()
                    res["removed"] += 1
                rec["public_file"] = None
            continue
        src = photos.source_path(rec, photo_root)
        if not src.exists():
            res["missing"].append(rec["id"])
            continue
        if not is_reviewed(rec):
            res["not_reviewed"] += 1
            if not allow_unreviewed:
                res["held"].append(rec["id"])
                continue
        dst = out_dir / photos.photo_folder(rec) / f"{rec['id']}.jpg"
        if dry_run:
            res["public"] += 1
            continue
        im = privacy.load_oriented(src)
        if not rec.get("auto_mask"):   # 顔の自動の箱がまだ無い（取り込みが 11/8 より前）→ ここで当てる
            auto, status = privacy.auto_masks(im)
            rec["masks"] = auto + [m for m in rec.get("masks") or [] if m.get("by") != "auto"]
            rec["auto_mask"] = status
            res["auto_now"] += 1
            if status.get("faces") != privacy.YUNET_ID:
                res["warnings"].append(f"{rec['id']}: 顔の自動の箱が作れない（{status.get('faces')}）")
        masks = rec.get("masks") or []
        small = privacy.resize_long(im, max(long, 1))
        out = privacy.paint_masks(small, masks)
        crop = (rec.get("crop") or {}).get("box")
        if crop:
            out = privacy.apply_crop(out, crop)
            res["cropped"] += 1
        privacy.save_jpeg(out, dst, quality)
        if masks:
            res["masked"] += 1
            for m in masks:
                res["boxes"][m.get("kind")] = res["boxes"].get(m.get("kind"), 0) + 1
        new = photos._rel_or_abs(dst, photo_root)
        if old and old != new:
            f = photo_root / old
            if f.exists() and _inside(f, out_dir):
                f.unlink()
        rec["public_file"] = new
        rec["public_at"] = now
        res["public"] += 1
    if not dry_run:   # 鍵を取って索引を読み直し、変わった項目だけ書く（11/8 監査 B 3、J-012 の再発を防ぐ）
        photos.patch_index(index_path, {r["id"]: photos.record_diff(bases.get(r["id"]), r) for r in idx["photos"]},
                           label="photos publish")
    return res


def is_reviewed(rec: dict) -> bool:
    """見直し済み：保護係（hogo）と見直し（review）の両方の記録がある（photos.set_masks が書く）。"""
    return bool(rec.get("hogo")) and bool(rec.get("review"))


def _inside(f: Path, d: Path) -> bool:
    try:
        f.resolve().relative_to(Path(d).resolve())
        return True
    except ValueError:
        return False


def summary_text(res: dict) -> str:
    box = "・".join(f"{privacy.KINDS.get(k, k)} {n}" for k, n in sorted(res["boxes"].items(), key=lambda x: str(x[0])))
    lines = [
        f"索引 {res['index']} 枚 → 公開 {res['public']}・塗りつぶしあり {res['masked']}（箱 {box or 'なし'}）・"
        f"切り落とし {res['cropped']}・除外 {len(res['excluded'])}・元が見つからない {len(res['missing'])}・期間前 {res['before']}",
        f"保護係と見直しをまだ通っていない {res['not_reviewed']} 枚"
        + ("（出した）" if res.get("allow_unreviewed") else "（出していない。--allow-unreviewed で出す）")
        + f"・顔の自動の箱をここで当てた {res['auto_now']} 枚・前の公開版を消した {res['removed']}"
        + ("（dry-run：書いていない）" if res["dry_run"] else ""),
        f"出した所：{res['out']}",
    ]
    for e in res["excluded"][:20]:
        lines.append(f"  除外 {e['id']}：{e['reason']}")
    if res["missing"]:
        lines.append(f"  元が見つからない（recorder photos relink <フォルダ>）：{', '.join(res['missing'][:10])}"
                     + (" ほか" if len(res["missing"]) > 10 else ""))
    for w in res["warnings"][:10]:
        lines.append(f"  警告 {w}")
    return "\n".join(lines)
