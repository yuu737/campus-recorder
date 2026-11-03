"""コマンド。

  python -m recorder new <id> <名前>            空の棟 JSON を data/buildings/<id>.json に作る
  python -m recorder validate <json...>         規約どおりか調べる（違反があれば終了コード 1）
  python -m recorder draw <json> [--out DIR]    図面 SVG を出す（既定 out/<id>/）
  python -m recorder unknowns <json>            未確定の一覧
  python -m recorder site build [--campus tempaku] [--plateau ZIP]
                                                 公開データから敷地の土台を作る（OSM・航空写真・標高・PLATEAU・確認用の図）
  python -m recorder photos import <folder>     写真の EXIF を読んで索引 data/photos/index.json に足す
  python -m recorder photos rectify <写真> [--f35 26]  壁を正面に向け直し out/rectified/ に画像と根拠 JSON を出す
  python -m recorder photos measure <front.jpg> [--known floor_pitch=3.99]
                                                 正面化画像から柱間隔・階高の候補を数える（既知の長さがあれば m に換算）
  python -m recorder site confirm <id> <status> --by 名前 [--name 新しい名前] [--note ...]
                                                 職員の確認を敷地 JSON に書く
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

    p = sub.add_parser("site")
    ssub = p.add_subparsers(dest="site_cmd", required=True)
    q = ssub.add_parser("build")
    q.add_argument("--campus", default="tempaku")
    q.add_argument("--plateau", default=r"D:\名城大学\data\raw\plateau\23100_nagoya-shi_city_2022_citygml_4_op.zip")
    q.add_argument("--no-plateau", action="store_true")
    q = ssub.add_parser("confirm")
    q.add_argument("id")
    q.add_argument("status", choices=["confirmed", "renamed", "removed", "new"])
    q.add_argument("--by", required=True)
    q.add_argument("--name", default=None)
    q.add_argument("--note", default="")
    q.add_argument("--campus", default="tempaku")

    p = sub.add_parser("photos")
    psub = p.add_subparsers(dest="photos_cmd", required=True)
    q = psub.add_parser("import")
    q.add_argument("folder")
    q.add_argument("--campus", default="tempaku")
    q = psub.add_parser("list")
    q = psub.add_parser("rectify")
    q.add_argument("file")
    q.add_argument("--f35", type=float, default=None, help="35mm 換算の焦点距離。省略すると EXIF、無ければ消失点から推定")
    q.add_argument("--out", default="out/rectified")
    q.add_argument("--roi", default=None, help="注目する壁の範囲 x0,y0,x1,y1（画素か 0〜1 の割合）。1枚に複数の面があるときに判断役が指定")
    q = psub.add_parser("measure")
    q.add_argument("front_jpg", help="rectify が出した *_front.jpg")
    q.add_argument("--known", default=None, help="既知の長さ 例: floor_pitch=3.99 / bay_pitch=5.3 / wall_width=47.7")
    q.add_argument("--known-status", default="measured", choices=["measured", "estimated"])

    a = ap.parse_args(argv)

    if a.cmd == "site":
        return _site(a)
    if a.cmd == "photos":
        return _photos(a)

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


def _photos(a) -> int:
    import json

    from . import photos, site

    if a.photos_cmd == "import":
        sp = site.DATA_SITE / f"{a.campus}.json"
        sdoc = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else None
        idx, added = photos.import_photos(Path(a.folder), sdoc)
        print(f"索引: {photos.INDEX_PATH}  合計 {len(idx['photos'])} 枚（今回 {len(added)} 枚）")
        for r in added:
            print("  " + photos.summary_line(r))
        return 0
    if a.photos_cmd == "rectify":
        from . import rectify

        src = Path(a.file)
        f35 = a.f35
        if f35 is None:
            f35 = photos.read_exif(src).get("focal_35mm")
        roi = tuple(float(v) for v in a.roi.split(",")) if a.roi else None
        res = rectify.rectify_file(src, Path(a.out), f35, roi=roi)
        if res.ok:
            print(f"正面化: {Path(a.out) / (src.stem + '_front.jpg')}  焦点距離 {res.focal_px}px（{res.focal_source}）"
                  f"  検算の傾き 横 {res.residual_deg_h}° 縦 {res.residual_deg_v}°  線分 横 {res.inliers_h} 縦 {res.inliers_v}")
        else:
            print(f"正面化できません: {res.reason}（線分 {res.segments} 本）")
        return 0 if res.ok else 1
    if a.photos_cmd == "measure":
        from . import measure

        known = None
        if a.known:
            k, v = a.known.split("=")
            known = (k, float(v))
        res = measure.measure_file(Path(a.front_jpg), known=known, known_status=a.known_status)
        if not res.ok:
            print(f"数えられません: {res.reason}")
            return 1
        for name, per in (("柱間隔", res.bays), ("階高", res.floors)):
            m = f"  ≈ {per.period_m} m（{per.status}）" if per.period_m else ""
            print(f"{name}: 周期 {per.period_px} px（強さ {per.strength}）  区間 {per.count}  エッジ {len(per.peaks_px)} 本{m}")
        print(f"比（柱間隔÷階高）: {res.ratio_bay_over_floor}   結果 JSON: {Path(a.front_jpg).with_name(Path(a.front_jpg).stem + '_measure.json')}")
        return 0
    if a.photos_cmd == "list":
        for r in photos.load_index()["photos"]:
            print(photos.summary_line(r))
        return 0
    return 2


def _site(a) -> int:
    import json
    import time

    from . import site

    if a.site_cmd == "build":
        t = time.time()
        osm = site.osm_site(site.fetch_osm())
        print(f"OSM: 建物 {len(osm['buildings'])} 件、敷地境界 {len(osm['boundary'])} 面 ({time.time() - t:.0f}s)")
        meta = site.fetch_aerial(osm["boundary"])
        print(f"航空写真: {meta['width']}x{meta['height']} px、タイル {meta['tiles']} 枚 ({time.time() - t:.0f}s)")
        dem = site.fetch_dem(osm["boundary"])
        print(f"標高: 格子 {dem['shape']}、{dem['z_min']:.1f}〜{dem['z_max']:.1f} m、欠け {dem['nan_ratio']:.1%} ({time.time() - t:.0f}s)")
        pl = None
        if not a.no_plateau and Path(a.plateau).exists():
            pl = site.extract_plateau(a.plateau, osm["boundary"])
            print(f"PLATEAU: 敷地内の建物 {len(pl['buildings'])} 件 ({time.time() - t:.0f}s)")
        else:
            print("PLATEAU: 省略（zip が無いか --no-plateau）")
        doc = site.build_site(a.campus, osm, pl)
        errs = site.validate_site(doc)
        named = sum(1 for b in doc["buildings"] if b["name"])
        print(f"敷地 JSON: {site.DATA_SITE / (a.campus + '.json')}  建物 {len(doc['buildings'])} 件（名前あり {named}）")
        out = site.draw_overview(doc, meta, site.OUT_SITE / "aerial_z18.png")
        print(f"確認用の図: {out} ({time.time() - t:.0f}s)")
        for e in errs:
            print("   - " + e)
        return 1 if errs else 0

    if a.site_cmd == "confirm":
        path = site.DATA_SITE / f"{a.campus}.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        b = site.confirm(doc, a.id, a.status, a.by, a.name, a.note)
        errs = site.validate_site(doc)
        if errs:
            for e in errs:
                print("   - " + e)
            return 1
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{b['id']}: {b['name']} → {a.status}（{a.by}）")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
