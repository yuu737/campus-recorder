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
  python -m recorder photos stitch <A_front.jpg> <B_front.jpg> [--overlap-hint 左|右] [--pin "A:x=B:x"] [--out ...]
                                                 片端ずつの 2 枚を階高で揃えて横につなぐ（決まらなければ ambiguous）
  python -m recorder site confirm <id> <status> --by 名前 [--name 新しい名前] [--note ...]
                                                 職員の確認を敷地 JSON に書く
  python -m recorder assets list                CC0 アセットの台帳（data/assets/catalog.json）と取得状況
  python -m recorder assets fetch [id ...]      台帳のアセットを assets/ に取る（差分だけ。sha256 を台帳に書く）
  python -m recorder photos register <写真id> --building <json> --face <面> [--from-rectify F --compare C] [--pose P]
  python -m recorder photos register --from-existing out/<棟> --building <json>
                                                 写真を面に登録（data/photos/registry.json）。以後は recheck が自動で重ね直す
  python -m recorder recheck <json> [--photos ...] [--fast] [--no-render]
                                                 登録済みの写真を全部、今の棟 JSON に重ね直し、線ごとのずれを一覧（out/<棟>/recheck/<日時>/）
  python -m recorder photoproj <json> [--photos id,...] [--fast] [--keep-renders]
                                                 登録済みの写真を模型に投影し、写真どうしの食い違い（縁の強さ）から形の悪い所を悪い順に（out/<棟>/photoproj/<日時>/）
  python -m recorder agent tasks <id> [--photos F]  各工程の依頼文を out/<id>/agent_tasks.json・.md に（API とサブエージェントで同じ文）
  python -m recorder agent log <id> --step S [--model opus --seconds N --tokens N --note ...]
                                                 サブエージェントの試走の 1 工程を out/<id>/pipeline_<日時>.json に追記
  python -m recorder hypotheses <json> [--list]  仮説（basis: hypothesis）の一覧：面・場所・値・規則・出所
  python -m recorder hypotheses <json> --drop [--face F] --reason "…"
                                                 仮説を unknown に戻す（JSON と history、design スクリプトの HYPOTHESES_OFF に追記）
  python -m recorder validate|draw <json> --no-hypotheses / model build <json> --no-hypotheses
                                                 仮説を外した状態で検証・描画・模型（out/<id>/no_hypotheses/）
  python -m recorder coverage <json>             面ごとの被覆図（登録済みの写真が覆う範囲、未撮影は灰色）と撮影依頼の候補
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
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("--dir", default="data/buildings")
    p.add_argument("--from-site", action="store_true", help="敷地 JSON と PLATEAU から土台（灰色の箱）を起こす")
    p.add_argument("--plateau-ids", default=None, help="PLATEAU の gml_id をカンマ区切りで（自動の対応が無い棟用）")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("validate")
    p.add_argument("files", nargs="+")
    p.add_argument("--no-hypotheses", action="store_true", help="仮説を全部外した状態で検証する")

    p = sub.add_parser("draw")
    p.add_argument("file")
    p.add_argument("--out", default=None)
    p.add_argument("--no-hypotheses", action="store_true", help="仮説を外した図面（既定の出力 out/<id>/no_hypotheses/）")

    p = sub.add_parser("hypotheses", help="仮説（basis: hypothesis）の一覧と取り下げ")
    p.add_argument("file")
    p.add_argument("--list", action="store_true", help="一覧（既定）")
    p.add_argument("--drop", action="store_true", help="仮説を unknown に戻す")
    p.add_argument("--face", default=None, help="この面だけ（省略で棟全体）")
    p.add_argument("--reason", default="", help="外す理由（--drop では必須。history に書く）")
    p.add_argument("--script", default=None, help="追記する design スクリプト（既定 scripts/design_<id>.py）")
    p.add_argument("--no-script", action="store_true", help="design スクリプトに書かない（試しに外すだけ。再生成で元に戻る）")

    p = sub.add_parser("unknowns")
    p.add_argument("file")

    p = sub.add_parser("run", help="validate → draw → model → overlay → run_report.json を 1 回で")
    p.add_argument("file")
    p.add_argument("--out", default=None)
    p.add_argument("--fast", action="store_true")
    p.add_argument("--skip-model", action="store_true")
    p.add_argument("--blender", default=None)

    p = sub.add_parser("model", help="棟 JSON から Blender で模型を組み、四面図・鳥瞰・glb を出す")
    msub = p.add_subparsers(dest="model_cmd", required=True)
    q = msub.add_parser("build")
    q.add_argument("file")
    q.add_argument("--out", default=None)
    q.add_argument("--blender", default=None, help="Blender 5.2 のパス（無ければ環境変数 BLENDER）")
    q.add_argument("--fast", action="store_true", help="小さめの描画")
    q.add_argument("--cameras", nargs="*", default=None, help="写真の視点（photos pose が出した *_pose.json）。同じ視点で描く")
    q.add_argument("--no-hypotheses", action="store_true", help="仮説を外した模型（既定の出力 out/<id>/no_hypotheses/）")

    p = sub.add_parser("site")
    ssub = p.add_subparsers(dest="site_cmd", required=True)
    q = ssub.add_parser("build")
    q.add_argument("--campus", default="tempaku")
    q.add_argument("--plateau", default=r"D:\名城大学\data\raw\plateau\23100_nagoya-shi_city_2022_citygml_4_op.zip")
    q.add_argument("--no-plateau", action="store_true")
    q = ssub.add_parser("export-web", help="Web 3D 用に web/data/site.json と glb を書き出す")
    q.add_argument("--campus", default="tempaku")
    q.add_argument("--with-coverage", action="store_true", help="out/<棟>/coverage.json の未撮影の範囲（灰色だけ。写真は出さない）を足す")
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
    q.add_argument("--period", choices=["before", "during"], default=None,
                   help="期間前 / 期間中。省略すると撮影日が 2026-11-02 12:00 JST より前なら before")
    q.add_argument("--ledger", default=None, help="写真台帳.json。棟の候補を source 付きで入れる")
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
    q = psub.add_parser("stitch", help="片端ずつ写した 2 枚の正面化画像を、階高を揃えて横につなぐ")
    q.add_argument("a_front", help="A（rectify が出した *_front.jpg）")
    q.add_argument("b_front", help="B（同じ面の別の端）")
    q.add_argument("--overlap-hint", choices=["左", "右"], default=None, help="B が A の 右 / 左 にある")
    q.add_argument("--pin", default=None, help='人が読んだ目印の対応 1 組 "A:x_px=B:x_px"（どちらも元の正面化画像の画素）')
    q.add_argument("--band", default=None, help="比べる行の範囲 y0,y1（A の画素）。木で隠れた階を外す時")
    q.add_argument("--out", default=None, help="out/rectified/<name>_stitched.jpg")

    q = psub.add_parser("pose", help="写真の視点を逆算：正面化＋検収の結果から（--from-rectify）か、点の対応（--points）から")
    q.add_argument("photo")
    q.add_argument("--points", default=None, help='{"points": [{"px": [u, v], "xyz": [x, y, z], "what": "..."}]}')
    q.add_argument("--from-rectify", default=None, help="rectify が出した *_front.json")
    q.add_argument("--compare", default=None, help="photos compare が出した *_compare.json")
    q.add_argument("--building", default=None)
    q.add_argument("--face", default=None)
    q.add_argument("--out", default=None, help="pose JSON の出力先（省略時は out/<棟id>/poses/<写真>_pose.json）")
    q = psub.add_parser("overlay", help="写真と同じ視点の描画を写真に重ねる")
    q.add_argument("photo")
    q.add_argument("--render", required=True, help="model build --cameras が出した view_photo_*.png")
    q.add_argument("--building", default=None, help="棟 id（省略時は --render のある out/<棟id>/ の名前）")
    q.add_argument("--out", default=None)
    q = psub.add_parser("compare", help="検収：正面化した写真と棟 JSON の面を m で重ね、ずれを数字で出す")
    q.add_argument("front_jpg", help="rectify が出した *_front.jpg")
    q.add_argument("--building", required=True, help="棟 JSON")
    q.add_argument("--face", required=True, help="面の id（SW など）")
    q.add_argument("--tol", type=float, default=0.15, help="合格の目安（m）")
    q.add_argument("--px-per-m", type=float, default=None, help="縮尺を自分で与える（省略時は柱間隔から）")
    q.add_argument("--out-dir", default=None, help="compare の JSON・PNG の出力先（省略時は out/<棟id>/compare/）")
    q.add_argument("--lines", default=None, help='判断役が読んだ線の位置（正面化画像の px）{"x_px": [...], "z_px": [...]}。あれば自動検出より優先')

    q = psub.add_parser("register", help="写真を面に登録（面の平面→写真の射影と視点）。以後は recheck が自動で重ね直す")
    q.add_argument("photo", nargs="?", default=None, help="索引の写真 id")
    q.add_argument("--building", required=True)
    q.add_argument("--face", default=None)
    q.add_argument("--from-rectify", default=None, help="rectify が出した *_front.json")
    q.add_argument("--compare", default=None, help="photos compare が出した *_compare.json（原点と縮尺）")
    q.add_argument("--pose", default=None, help="photos pose が出した *_pose.json")
    q.add_argument("--lines", default=None, help="compare に使った --lines の JSON（記録だけ）")
    q.add_argument("--from-existing", default=None, help="out/<棟>：poses と compare から一括登録し、登録できない写真に理由を付ける")
    q.add_argument("--registry", default=None, help="台帳の場所（既定 data/photos/registry.json）")

    q = psub.add_parser("roof", help="航空写真（国土地理院 z18）で屋上の縁・倒れ込み・設備を読む")
    q.add_argument("building", help="棟 JSON")
    q.add_argument("--out-dir", default=None, help="roof.json・roof_aerial.png の出力先（省略時は out/<棟id>/）")
    q.add_argument("--roof-edge", default=None, help='人が指した屋上の縁の点（写真の上の m）"x,y;x,y;..."。あれば自動より優先')
    q.add_argument("--units", default=None, help='人が指した設備（写真の上の中心 m と大きさ）"x,y,w,d;..."。あれば自動より優先')

    p = sub.add_parser("recheck", help="登録済みの写真を全部、今の棟 JSON に重ね直してずれを一覧にする")
    p.add_argument("file")
    p.add_argument("--photos", nargs="*", default=None, help="写真 id（省略で全部）")
    p.add_argument("--fast", action="store_true", help="描画を小さく")
    p.add_argument("--no-render", action="store_true", help="線のずれだけ（Blender を使わない）")
    p.add_argument("--blender", default=None)
    p.add_argument("--registry", default=None)

    p = sub.add_parser("photoproj", help="登録済みの写真を模型に投影し、写真どうしの食い違いから形の悪い所を一覧にする")
    p.add_argument("file")
    p.add_argument("--photos", default=None, help="写真 id（カンマ区切り。頭だけでも可。省略で全部）")
    p.add_argument("--fast", action="store_true", help="描画を写真の 1/8 に（通常 1/4）")
    p.add_argument("--keep-renders", action="store_true", help="Blender の P・ID（render/*.npz）を残す")
    p.add_argument("--reuse-renders", default=None, help="前の --keep-renders の render/ を使う（Blender を動かさない。棟 JSON を直した後は使わない）")
    p.add_argument("--blender", default=None)
    p.add_argument("--registry", default=None)

    p = sub.add_parser("coverage", help="面ごとの被覆図と撮影依頼の候補")
    p.add_argument("file")
    p.add_argument("--registry", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--no-photo", action="store_true", help="写真を貼らず、覆う範囲の塗りだけにする")
    p.add_argument("--px-per-m", type=float, default=40.0, help="面の座標の写真の解像度（px/m）")

    p = sub.add_parser("agent", help="係（.claude/agents）を API で動かす")
    asub = p.add_subparsers(dest="agent_cmd", required=True)
    asub.add_parser("list")
    q = asub.add_parser("run")
    q.add_argument("name")
    q.add_argument("--task", required=True)
    q.add_argument("--dry-run", action="store_true")
    q = asub.add_parser("pipeline")
    q.add_argument("building")
    q.add_argument("--photos", default=None, help="取り込む写真フォルダ（省略で索引の既存の写真）")
    q.add_argument("--steps", default=None, help="カンマ区切り（既定: 全工程）")
    q.add_argument("--dry-run", action="store_true")
    q = asub.add_parser("tasks", help="各工程の依頼文（係の定義のパス＋task）を out/<棟>/agent_tasks.json・.md に出す")
    q.add_argument("building")
    q.add_argument("--photos", default=None, help="取り込む写真フォルダ（pipeline と同じ値を渡す）")
    q = asub.add_parser("log", help="サブエージェントの試走の 1 工程を out/<棟>/pipeline_<日時>.json に追記")
    q.add_argument("building")
    q.add_argument("--step", required=True)
    q.add_argument("--model", default="opus")
    q.add_argument("--seconds", type=float, default=None)
    q.add_argument("--tokens", type=int, default=None)
    q.add_argument("--note", default="")
    q.add_argument("--record", default=None, help="追記する記録（省略で runner subagent の最新）")
    q.add_argument("--new", action="store_true", help="新しい記録を始める")

    p = sub.add_parser("assets", help="CC0 アセットの台帳と取得")
    xsub = p.add_subparsers(dest="assets_cmd", required=True)
    q = xsub.add_parser("list")
    q.add_argument("--kind", default=None, choices=["model", "texture", "hdri"])
    q = xsub.add_parser("fetch")
    q.add_argument("ids", nargs="*")

    a = ap.parse_args(argv)

    if a.cmd == "agent":
        return _agent(a)

    if a.cmd == "site":
        return _site(a)
    if a.cmd == "photos":
        return _photos(a)
    if a.cmd == "assets":
        return _assets(a)
    if a.cmd == "recheck":
        from . import recheck, registry

        reg = registry.load_registry(Path(a.registry)) if a.registry else None
        summ, out = recheck.recheck(Path(a.file), a.photos, a.fast, not a.no_render, registry=reg, blender=a.blender)
        if not summ["photos"]:
            print("登録済みの写真がありません（recorder photos register）")
        for p in summ["photos"]:
            wf, wp = p["worst_face"], p["worst_parts"]
            print(f"{p['photo'][:34]:<34} {p['face']:<5} 面 {'合格' if p['pass_face'] else ('不合格' if p['pass_face'] is False else '—')}"
                  f" 最大 {wf['diff_m']:+.2f} m（{wf['what']}）" if wf else f"{p['photo'][:34]:<34} {p['face']:<5} 面 —", end="")
            print(f" / 部品 最大 {wp['diff_m']:+.2f} m（{wp['what']}）" if wp else " / 部品 —", f" 線 {p['n_found']}/{p['n_lines']}")
        for w in summ["warnings"]:
            print("注意:", w)
        for r in summ.get("renders", []):
            print(f"重ね: {r.get('overlay') or r.get('reason')}")
        print(f"一覧: {out / 'summary.md'}")
        return 0
    if a.cmd == "photoproj":
        from . import photoproj, registry

        reg = registry.load_registry(Path(a.registry)) if a.registry else None
        ph = [x.strip() for x in a.photos.split(",") if x.strip()] if a.photos else None
        summ, out = photoproj.photoproj(Path(a.file), ph, a.fast, a.blender, reg, keep_renders=a.keep_renders,
                                        reuse=Path(a.reuse_renders) if a.reuse_renders else None)
        for k, r in enumerate(summ["ranking"][:10]):
            print(f"{k + 1:>2}. {r['id']:<30} {r['kind']}  score {r['score']:.2f}  平均 {r['mean']:.3f}  p90 {r['p90']:.3f}  （{r['pair']}）")
        for c in summ.get("cells", [])[:5]:
            print(f"    升: {c['face']} s {c['s'][0]:.0f}–{c['s'][1]:.0f} m z {c['z'][0]:.0f}–{c['z'][1]:.0f} m  score {c['score']:.2f}（{c['pair']}）")
        for r in summ["suspects"][:5]:
            print(f"怪しい（1 枚の所）: {r['id']} +{r['increase']:.3f}（{photoproj.short_id(r['photo'])} {r['view']}）")
        for r in summ.get("unsupported", [])[:5]:
            print(f"縁の裏付けなし: {r['id']} {r['support']:.2f}（{photoproj.short_id(r['photo'])}）")
        for w in summ["warnings"]:
            print("注意:", w)
        print(f"一覧: {out / 'summary.md'}")
        return 0
    if a.cmd == "coverage":
        from . import coverage, registry

        reg = registry.load_registry(Path(a.registry)) if a.registry else None
        res, jp = coverage.coverage(Path(a.file), reg, Path(a.out) if a.out else None, photo=not a.no_photo, px_per_m=a.px_per_m)
        for f in res["faces"]:
            print(f"{f['face']:<7} 被覆 {f['coverage_pct']:5.1f} %（枠 {f['frame_pct']:5.1f} %）  写真 {len(f['photos'])} 枚"
                  + ("  (unknown)" if f["unknown"] else "") + (f"  確認用 {f['check_png']}" if f.get("check_png") else ""))
        for r in res["requests"]:
            print("撮影依頼の候補:", r["text"])
        print(f"JSON: {jp}")
        return 0

    if a.cmd == "new":
        path = Path(a.dir) / f"{a.id}.json"
        if path.exists() and not a.force:
            print(f"すでにあります: {path}（上書きは --force）")
            return 1
        if a.from_site:
            from . import skeleton

            ids = [x for x in (a.plateau_ids or "").split(",") if x]
            b = skeleton.from_site_cli(a.id, ids or None)
        else:
            if not a.name:
                print("name が要ります")
                return 1
            b = schema.new_building(a.id, a.name)
        errs = schema.validate_building(b)
        if errs:
            print("規約違反:", *errs, sep=chr(10) + "  ")
            return 1
        schema.save_building(b, path)
        print(f"作成: {path}  面 {len(b.get('faces', []))}  撮影依頼 {len(b.get('requests', []))}")
        return 0

    if a.cmd == "validate":
        bad = 0
        for f in a.files:
            b = schema.load_building(f)
            if a.no_hypotheses:
                b, dropped = schema.drop_hypotheses(b)
                print(f"仮説を外して検証: {f}（{len(dropped)} 値を unknown に）")
            errs = schema.validate_building(b)
            if errs:
                bad += 1
                print(f"NG {f}")
                for e in errs:
                    print(f"   - {e}")
            else:
                print(f"OK {f}")
        return 1 if bad else 0

    if a.cmd == "hypotheses":
        return _hypotheses(a)

    if a.cmd == "draw":
        b = schema.load_building(a.file)
        if a.no_hypotheses:
            b, _ = schema.drop_hypotheses(b)
        errs = schema.validate_building(b)
        if errs:
            print("規約違反があるので描きません:")
            for e in errs:
                print(f"   - {e}")
            return 1
        out = Path(a.out) if a.out else Path("out") / b["id"] / ("no_hypotheses" if a.no_hypotheses else "")
        out.mkdir(parents=True, exist_ok=True)
        for name, svg in drawing.all_drawings(b).items():
            (out / f"{name}.svg").write_text(svg, encoding="utf-8")
            print(f"出力: {out / (name + '.svg')}")
        return 0

    if a.cmd == "run":
        from . import run as runner

        r = runner.run(Path(a.file), Path(a.out) if a.out else None, a.fast, a.skip_model, a.blender)
        for st in r["steps"]:
            print(f"{'OK' if st['ok'] else 'NG'} {st['name']:<9} {st['seconds']}s  {st['error']}")
        c = r["counts"]
        print(f"measured {c['measured']} / estimated {c['estimated']} / unknown {c['unknown']}  撮影依頼 open {r['requests_open']}")
        print(f"報告: {Path(r['out']) / 'run_report.json'}  合計 {r['seconds_total']}s")
        return 0 if r["ok"] else 1

    if a.cmd == "unknowns":
        for u in schema.unknowns(schema.load_building(a.file)):
            print(u)
        return 0

    if a.cmd == "model":
        from . import model

        b = schema.load_building(a.file)
        src, out_dir = Path(a.file), Path(a.out) if a.out else None
        if a.no_hypotheses:
            b, dropped = schema.drop_hypotheses(b)
            out_dir = out_dir or Path("out") / b["id"] / "no_hypotheses"
            out_dir.mkdir(parents=True, exist_ok=True)
            src = out_dir / f"{b['id']}.json"
            schema.save_building(b, src)
            print(f"仮説を外して組む（{len(dropped)} 値を unknown に）: {src}")
        errs = schema.validate_building(b)
        if errs:
            print("規約違反があるので組みません:")
            for e in errs:
                print(f"   - {e}")
            return 1
        out = model.build(src, out_dir, a.blender, a.fast, [Path(c) for c in a.cameras] if a.cameras else None)
        print(f"模型: {out}")
        print(f"四面図: {model.sheet(out, b)}")
        return 0

    return 2


HYP_MARK = "HYPOTHESES_OFF: dict = {}"


def _hypotheses(a) -> int:
    """仮説の一覧（--list）と取り下げ（--drop）。取り下げは JSON に書き、design スクリプトの HYPOTHESES_OFF にも追記する
    （design スクリプトが元なので、再生成しても外れたまま）。"""
    import datetime
    import json

    b = schema.load_building(a.file)
    hyps = list(schema.iter_hypotheses(b))
    if not a.drop:
        if not hyps:
            print("仮説はありません")
        for where, fid, m in hyps:
            h = m.get("hypothesis") or {}
            print(f"{fid or '-':<6} {where:<40} {m.get('value')!s:<8} {h.get('rule', '?'):<10} from {h.get('from', '?'):<6} {h.get('note', '')[:60]}")
        faces = sorted({fid or "-" for _, fid, _ in hyps})
        print(f"仮説 {len(hyps)} 値（面 {', '.join(faces) if faces else 'なし'}）")
        return 0
    if not a.reason:
        print("--drop には --reason（外す理由。history に書く）が要ります")
        return 1
    at = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M")
    nb, dropped = schema.drop_hypotheses(b, a.face, a.reason, at)
    if not dropped:
        print(f"外す仮説がありません（{a.face or '棟全体'}）")
        return 0
    errs = schema.validate_building(nb)
    if errs:
        print("外した後に規約違反があるので書きません:", *errs, sep=chr(10) + "  ")
        return 1
    schema.save_building(nb, a.file)
    for w in dropped:
        print(f"unknown に戻した: {w}")
    print(f"書いた: {a.file}（history に理由）")
    if a.no_script:
        print("design スクリプトには書いていません（再生成すると仮説が戻ります）")
        return 0
    sp = Path(a.script) if a.script else Path("scripts") / f"design_{b['id']}.py"
    if not sp.exists():
        print(f"design スクリプトがありません: {sp}（再生成で戻らないよう、スクリプトに HYPOTHESES_OFF を書いてください）")
        return 0
    text = sp.read_text(encoding="utf-8")
    if HYP_MARK not in text:
        print(f"注意: {sp} に『{HYP_MARK}』の行がありません。再生成で仮説が戻ります（行を足して apply_hypotheses_off を呼ぶ）")
        return 1
    entry = json.dumps({"at": at, "reason": a.reason}, ensure_ascii=False)
    line = f"HYPOTHESES_OFF[{json.dumps(a.face or '*', ensure_ascii=False)}] = {entry}   # recorder hypotheses --drop"
    text = text.replace(HYP_MARK, HYP_MARK + chr(10) + line, 1)
    sp.write_bytes(text.encode("utf-8"))
    print(f"design スクリプトに追記: {sp}（{line}）")
    return 0


def _agent(a) -> int:
    import json

    from . import agents

    try:
        if a.agent_cmd == "list":
            for d in agents.load_agent_defs().values():
                print(f"{d['name']:<9} {d['model_id']:<20} {','.join(d['tools'])}  {d['description'][:50]}")
            return 0
        if a.agent_cmd == "run":
            res = agents.run_agent(a.name, a.task, cwd=".", dry_run=a.dry_run)
        elif a.agent_cmd == "tasks":
            res = agents.write_tasks(a.building, a.photos)
        elif a.agent_cmd == "log":
            res = agents.log_step(a.building, a.step, model=a.model, seconds=a.seconds, tokens=a.tokens, note=a.note,
                                  record=a.record, new=a.new)
        else:
            steps = a.steps.split(",") if a.steps else None
            res = agents.run_pipeline(a.building, a.photos, steps=steps, dry_run=a.dry_run)
    except agents.AgentError as e:
        print(f"エラー: {e}")
        return 1
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


def _photos(a) -> int:
    import json

    from . import photos, site

    if a.photos_cmd == "import":
        sp = site.DATA_SITE / f"{a.campus}.json"
        sdoc = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else None
        ledger = photos.load_ledger(Path(a.ledger)) if a.ledger else None
        idx, added = photos.import_photos(Path(a.folder), sdoc, period=a.period, ledger=ledger)
        print(f"索引: {photos.INDEX_PATH}  合計 {len(idx['photos'])} 枚（今回 {len(added)} 枚）")
        print("  期間別:", {k: v for k, v in photos.period_counts(idx).items() if k != "by_building"})
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
    if a.photos_cmd == "stitch":
        from . import stitch

        band = tuple(float(v) for v in a.band.split(",")) if a.band else None
        res, js = stitch.stitch_files(Path(a.a_front), Path(a.b_front), Path(a.out) if a.out else None,
                                      hint=a.overlap_hint, pin=a.pin, band=band)
        for w in res.get("warnings", []):
            print("注意:", w)
        if res["status"] != "ok":
            print(f"{'決まりません' if res['status'] == 'ambiguous' else 'つなげません'}: {res['reason']}")
            print(f"結果 JSON: {js}")
            return 2 if res["status"] == "ambiguous" else 1
        c = res["check"]
        print(f"つないだ画像: {res['image']}  B の拡縮 {res['scale_b']}  ずれ dx {res['dx_px']} px  dy {res['dy_px']} px"
              f"  相関 {res['ncc']}（2 位との比 {res['second_over_first']}）")
        print(f"検算 柱間隔÷階高: A {c['ratio_A']} / B {c['ratio_B']} / つないだ画像 {c['ratio_stitched']}"
              f"（柱 {c['bay_px']} px、階 {c['floor_px']} px）  一致 {c['ratio_agree']}")
        print(f"結果 JSON: {js}")
        return 0
    if a.photos_cmd == "pose":
        from . import pose

        if a.from_rectify:
            res = pose.from_rectification(Path(a.photo), Path(a.from_rectify), Path(a.compare), Path(a.building), a.face,
                                           out_json=Path(a.out) if a.out else None)
        else:
            res = pose.solve_file(Path(a.photo), Path(a.points))
        if res.reason:
            print("注意:", res.reason)
        if not res.ok:
            print(f"視点が決まりません: {res.reason}")
            return 1
        print(f"カメラ位置 {res.camera_xyz}（m）  焦点距離 {res.focal_px:.0f} px  再投影誤差 最大 {res.reproj_max_px} px  各点 {res.reproj_px}")
        if res.holdout_reproj_px is not None:
            print(f"検算（解に使わなかった {res.n_holdout} 点）再投影誤差 最大 {res.holdout_reproj_px} px、平均 {res.holdout_mean_px} px  カメラ高 {res.camera_height_m} m  面まで {res.distance_to_face_m} m")
        return 0
    if a.photos_cmd == "overlay":
        from . import pose

        if a.out:
            out = Path(a.out)
        else:       # run の overlay 工程と同じ命名：out/<棟id>/overlay_<写真id>.png
            bid = a.building or (Path(a.render).parent.name if Path(a.render).parent.name not in ("", ".") else "overlay")
            out = Path("out") / bid / f"overlay_{Path(a.photo).stem}.png"
        print(f"重ね合わせ: {pose.overlay(Path(a.photo), Path(a.render), out)}")
        return 0
    if a.photos_cmd == "compare":
        from . import compare

        res, png = compare.compare_file(Path(a.front_jpg), Path(a.building), a.face, tol_m=a.tol, px_per_m=a.px_per_m,
                                       lines_json=Path(a.lines) if a.lines else None,
                                       out_dir=Path(a.out_dir) if a.out_dir else None)
        if not res.ok:
            print(f"比べられません: {res.reason}")
            return 1
        print(f"面 {res.face}  縮尺 {res.px_per_m} px/m  合格 {res.n_ok}  不合格 {res.n_ng}  見つからず {res.n_missing}  最大ずれ {res.worst_m} m")
        if res.floor_pitch_photo_m:
            print(f"階高: JSON {res.floor_pitch_json_m} m / 写真 {res.floor_pitch_photo_m} m")
        for l in res.lines:
            mark = "OK " if l.ok else ("NG " if l.ok is False else "-- ")
            d = f"{l.diff_m:+.2f} m" if l.diff_m is not None else "見つからず"
            print(f"  {mark}{l.axis} {l.what:<14} JSON {l.json_m:6.2f}  {d}")
        print(f"画像: {png}")
        return 0
    if a.photos_cmd == "register":
        from . import registry

        rp = Path(a.registry) if a.registry else registry.REGISTRY_PATH
        reg = registry.load_registry(rp)
        if a.from_existing:
            done, nr = registry.register_existing(Path(a.from_existing), Path(a.building), reg)
            registry.save_registry(reg, rp)
            for e in done:
                c = e["check"]
                print(f"登録: {e['photo'][:40]:<40} {e['face']:<4} 被覆 {e['cover']['area_m2']} m²  検算 {c['pose_holdout_px']} px"
                      f"  既存の pose との差 {c['existing_pose_vs_compare_px']} px{'（解き直した）' if e['source']['pose_resolved'] else ''}")
            print(f"登録できない写真 {len(nr)} 枚:")
            for n in nr:
                print(f"  {n['photo'][:40]:<40} {n['face']:<12} {n['reason']}")
            print(f"台帳: {rp}")
            return 0
        if not (a.photo and a.face):
            print("写真 id と --face が要ります（一括は --from-existing）")
            return 1
        try:
            e = registry.register(a.photo, Path(a.building), a.face, Path(a.from_rectify) if a.from_rectify else None,
                                  Path(a.compare) if a.compare else None, Path(a.pose) if a.pose else None,
                                  Path(a.lines) if a.lines else None)
        except ValueError as ex:
            print(f"登録できません: {ex}")
            return 1
        registry.upsert(reg, e)
        registry.save_registry(reg, rp)
        print(f"登録: {e['photo']} {e['face']}  被覆 {e['cover']['area_m2']} m²  検算 {e['check']}  台帳: {rp}")
        return 0
    if a.photos_cmd == "roof":
        from . import roof

        res, jp, png = roof.run(Path(a.building), out_dir=Path(a.out_dir) if a.out_dir else None,
                                roof_edge=a.roof_edge, units=a.units)
        lx, ly = res["lean_m"]
        print(f"倒れ込み lean_m=({lx:+.2f}, {ly:+.2f}) m  |{res['lean_abs_m']}| m  向き {res['lean_dir_deg']}°  率 {res['lean_ratio']}  縁={res['roof_edge_source']}  ambiguous={res['ambiguous']}")
        for c in res["roof_edge_offset_candidates"]:
            print(f"  候補 dx={c['dx']:+.2f} dy={c['dy']:+.2f}  点 {c['score']}  比 {c['rel']}  確からしさ {c['confidence']}")
        ch = res["check_low_building"]
        print(f"低い建物の検算: {ch.get('id')} ずれ {ch.get('offset_m')} → {ch.get('note')}")
        for i, u in enumerate(res["units"], 1):
            print(f"  設備 {i}: 中心 ({u['cx']}, {u['cy']})  {u['w']}×{u['d']} m  明るさ {u['brightness']}  {u['note']}")
        for w in res["warnings"]:
            print(f"警告: {w}")
        print(f"JSON: {jp}")
        print(f"画像: {png}")
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

    if a.site_cmd == "export-web":
        r = site.export_web(a.campus, with_coverage=a.with_coverage)
        print(f"Web 用: {r['path']}  箱 {r['boxes']} 件、模型 {r['models']}、地面格子 {r['grid']}（標高の欠けを近傍で埋めた割合 {r['nan_filled']:.1%}）")
        return 0

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


def _assets(a) -> int:
    from . import assets

    cat = assets.load_catalog()
    errs = assets.validate_catalog(cat)
    if errs:
        print("台帳の規約違反:")
        for e in errs:
            print(f"   - {e}")
        return 1
    if a.assets_cmd == "list":
        for it in cat["items"]:
            if a.kind and it["kind"] != a.kind:
                continue
            try:
                assets.resolve(it["id"], cat)
                st = "取得済み"
            except FileNotFoundError:
                st = "未取得"
            size = " x ".join(f"{v:g}" for v in it.get("size_m") or it.get("tile_m") or [])
            print(f"{it['id']:<16} {it['kind']:<8} {it['source']:<10} {st:<5} {size:<20} {it['what']}")
        for nf in cat.get("not_found", []):
            print(f"{'(該当なし)':<16} {nf['what']} → {nf['fallback']}")
        return 0
    r = assets.fetch(cat, a.ids or None)
    assets.save_catalog(cat)
    print(f"取得 {len(r['ok'])} / 変化なし {len(r['skip'])} / 失敗 {len(r['fail'])}")
    return 1 if r["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
