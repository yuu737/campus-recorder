"""顔の自動検出に使う OpenCV の YuNet のモデル（ONNX、約 230 KB）を assets/opencv_zoo/ に取る（11/8、K-104）。

    .venv/Scripts/python.exe -I -X utf8 scripts/fetch_yunet.py [--force]

- 本体は git に入れない（assets/ は .gitignore 済み）。このスクリプトで誰でも同じ物を取り直せる。
- 出典・ライセンス・取得日・sha256 を data/assets/catalog.json の "tools" に書く（鍵を取って書く。台帳は道具でだけ書く）。
  "items"（見た目の CC0 アセット）には入れない：YuNet は絵の素材ではなく道具のモデルで、ライセンスは MIT。
  使うのは recorder/privacy.py（取り込みの時に「係に見せる縮小版」の顔に灰色の箱を置く）だけ。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODEL_NAME = "face_detection_yunet_2023mar.onnx"
DEST = ROOT / "assets" / "opencv_zoo" / MODEL_NAME
URLS = [   # 上から順に試す（どちらも opencv_zoo の同じ物）
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/" + MODEL_NAME,
    "https://huggingface.co/opencv/face_detection_yunet/resolve/main/" + MODEL_NAME,
]
ENTRY = {
    "id": "yunet_2023mar",
    "kind": "tool_model",
    "what": "顔の検出（OpenCV の YuNet、ONNX）。係に見せる縮小版と公開版の顔に灰色の箱を置く自動の保険",
    "source": "opencv_zoo",
    "page": "https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet",
    "license": "MIT",
    "license_url": "https://github.com/opencv/opencv_zoo/blob/main/models/face_detection_yunet/LICENSE",
    "file": "opencv_zoo/" + MODEL_NAME,
    "used_by": "recorder/privacy.py",
    "note": "見た目のアセットではない（items の CC0 の決まりは見た目の素材の決まり）。取得は scripts/fetch_yunet.py。git に入れない",
}


def fetch(force: bool = False) -> dict:
    if DEST.exists() and not force:
        data = DEST.read_bytes()
        url = None
    else:
        data, url, errs = None, None, []
        for u in URLS:
            try:
                with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "campus-recorder"}), timeout=60) as r:
                    data = r.read()
                if len(data) < 50_000 or data[:4] == b"vers":   # LFS の指し示しだけが返った
                    errs.append(f"{u}: 中身が小さすぎる（{len(data)} バイト）")
                    data = None
                    continue
                url = u
                break
            except Exception as e:  # noqa: BLE001
                errs.append(f"{u}: {e}")
        if data is None:
            raise SystemExit("取得できません：\n  " + "\n  ".join(errs))
        DEST.parent.mkdir(parents=True, exist_ok=True)
        DEST.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    return {"path": str(DEST), "bytes": len(data), "sha256": sha, "url": url}


def record(res: dict) -> None:
    """catalog.json の "tools" に 1 件を書く（同じ id は置き換え）。"""
    from recorder import ledger

    cat_path = ROOT / "data" / "assets" / "catalog.json"
    with ledger.lock("ledger", label="scripts/fetch_yunet.py"):
        cat = json.loads(cat_path.read_text(encoding="utf-8"))
        tools = [t for t in cat.get("tools", []) if t.get("id") != ENTRY["id"]]
        old = next((t for t in cat.get("tools", []) if t.get("id") == ENTRY["id"]), {})
        e = dict(ENTRY)
        e["url"] = res["url"] or old.get("url") or URLS[0]
        e["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+09:00") if res["url"] else (old.get("fetched_at") or time.strftime("%Y-%m-%dT%H:%M:%S+09:00"))
        e["sha256"] = res["sha256"]
        e["bytes"] = res["bytes"]
        tools.append(e)
        cat["tools"] = tools
        ledger.write_text_atomic(cat_path, json.dumps(cat, ensure_ascii=False, indent=1) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fetch_yunet")
    ap.add_argument("--force", action="store_true", help="あっても取り直す")
    ap.add_argument("--no-record", action="store_true", help="catalog.json に書かない（試しの時）")
    a = ap.parse_args(argv)
    res = fetch(a.force)
    if not a.no_record:
        record(res)
    print(f"YuNet: {res['path']}（{res['bytes']} バイト、sha256 {res['sha256'][:16]}…）"
          + ("" if res["url"] else "（既にあったので取り直していない）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
