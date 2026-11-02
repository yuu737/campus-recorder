"""コマンド。

  python -m recorder new <id> <名前>            空の棟 JSON を data/buildings/<id>.json に作る
  python -m recorder validate <json...>         規約どおりか調べる（違反があれば終了コード 1）
  python -m recorder draw <json> [--out DIR]    図面 SVG を出す（既定 out/<id>/）
  python -m recorder unknowns <json>            未確定の一覧
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import drawing, schema


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="recorder")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("new")
    p.add_argument("id")
    p.add_argument("name")
    p.add_argument("--dir", default="data/buildings")

    p = sub.add_parser("validate")
    p.add_argument("files", nargs="+")

    p = sub.add_parser("draw")
    p.add_argument("file")
    p.add_argument("--out", default=None)

    p = sub.add_parser("unknowns")
    p.add_argument("file")

    a = ap.parse_args(argv)

    if a.cmd == "new":
        path = Path(a.dir) / f"{a.id}.json"
        if path.exists():
            print(f"すでにあります: {path}")
            return 1
        schema.save_building(schema.new_building(a.id, a.name), path)
        print(f"作成: {path}")
        return 0

    if a.cmd == "validate":
        bad = 0
        for f in a.files:
            errs = schema.validate_building(schema.load_building(f))
            if errs:
                bad += 1
                print(f"NG {f}")
                for e in errs:
                    print(f"   - {e}")
            else:
                print(f"OK {f}")
        return 1 if bad else 0

    if a.cmd == "draw":
        b = schema.load_building(a.file)
        errs = schema.validate_building(b)
        if errs:
            print("規約違反があるので描きません:")
            for e in errs:
                print(f"   - {e}")
            return 1
        out = Path(a.out) if a.out else Path("out") / b["id"]
        out.mkdir(parents=True, exist_ok=True)
        for name, svg in drawing.all_drawings(b).items():
            (out / f"{name}.svg").write_text(svg, encoding="utf-8")
            print(f"出力: {out / (name + '.svg')}")
        return 0

    if a.cmd == "unknowns":
        for u in schema.unknowns(schema.load_building(a.file)):
            print(u)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
