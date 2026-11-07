"""コマンド。

  python -m recorder new <id> <名前>            空の棟 JSON を data/buildings/<id>.json に作る
  python -m recorder validate <json...>         規約どおりか調べる（違反があれば終了コード 1）
  python -m recorder draw <json> [--out DIR]    図面 SVG を出す（既定 out/<id>/）
  python -m recorder unknowns <json>            未確定の一覧
  python -m recorder site build [--campus X] [--plateau ZIP]
                                                 公開データから敷地の土台を作る（OSM・航空写真・標高・PLATEAU・確認用の図）
  python -m recorder photos import <folder>     写真の EXIF を読んで索引 data/photos/index.json に足す
  python -m recorder photos rectify <写真> [--f35 26]  壁を正面に向け直し out/rectified/ に画像と根拠 JSON を出す
  python -m recorder photos measure <front.jpg> [--known floor_pitch=3.99 | segment=0.698@85.3]
                                                 正面化画像から柱間隔・階高の候補を数える（既知の長さがあれば m に換算）
  python -m recorder measure tape|pitch|uniform|chain …
                                                 メジャーの読みからタイルの周期と根拠の鎖（K-101。measure.py の末尾に使い方）
  python -m recorder photos stitch <A_front.jpg> <B_front.jpg> [--overlap-hint 左|右] [--pin "A:x=B:x"] [--out ...]
                                                 片端ずつの 2 枚を階高で揃えて横につなぐ（決まらなければ ambiguous）
  python -m recorder site confirm <id> <status> --by 名前 [--name 新しい名前] [--note ...]
                                                 職員の確認を敷地 JSON に書く
  python -m recorder site link-box <棟|敷地の物> <箱id> --why 理由 [--ask 撮影依頼の文]
                                                 PLATEAU の箱を棟に結ぶ（足元の 7 割以上が輪郭の中の時だけ。足りなければ撮影依頼）
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
    p.add_argument("--strict", action="store_true", help="警告（± の下限・採用値・根拠 id）もエラー扱いにする")

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

    p = sub.add_parser("counts", help="値の件数と、独立した観察の件数（同じ状態・方法・根拠・値の種類を 1 つに束ねる）")
    p.add_argument("files", nargs="+", help="棟 JSON（複数可）")
    p.add_argument("--faces", action="store_true", help="面ごとの値と観察も出す")
    p.add_argument("--top", type=int, default=10, help="複製の多い観察の上位いくつを出すか")
    p.add_argument("--json", action="store_true", help="JSON で出す（measured の直接／鎖／個数と観察の件数）")

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
    q.add_argument("--campus", default=None)
    q.add_argument("--plateau", default=None, help="PLATEAU の zip。省略すると敷地 JSON の sources.plateau_zip")
    q.add_argument("--no-plateau", action="store_true")
    q = ssub.add_parser("export-web", help="Web 3D 用に web/data/site.json と glb を書き出す")
    q.add_argument("--campus", default=None)
    q.add_argument("--with-coverage", action="store_true", help="out/<棟>/coverage.json の未撮影の範囲（灰色だけ。写真は出さない）を足す")
    q.add_argument("--out", default=None, help="書き出し先（既定 web/。<out>/data/site.json に書く。試しは一時フォルダへ）")
    q = ssub.add_parser("objects", help="敷地の物（objects：通路橋・甲板・段・置物）の検証・検収の数字・glb の書き出し")
    q.add_argument("action", choices=["check", "export"])
    q.add_argument("--campus", default=None)
    q.add_argument("--out", default=None, help="export の書き出し先（<out>/data/models/site_objects.glb）。必須")
    q = ssub.add_parser("link-box", help="PLATEAU の箱を棟（棟 JSON の輪郭）か敷地の物に結ぶ。重なりが 7 割未満なら結ばず撮影依頼")
    q.add_argument("target", help="棟 id（data/buildings/<id>.json）か敷地の物の id（objects.items[].id）")
    q.add_argument("box", help="PLATEAU の箱の gml_id（先頭の一部でよい。bldg_f693ff16 など）")
    q.add_argument("--why", required=True, help="結ぶ理由（観察票・写真など）。敷地 JSON に残す")
    q.add_argument("--by", default="recorder site link-box")
    q.add_argument("--ask", default="", help="結べなかった時の撮影依頼の文")
    q.add_argument("--campus", default=None)
    q = ssub.add_parser("confirm")
    q.add_argument("id")
    q.add_argument("status", choices=["confirmed", "renamed", "removed", "new"])
    q.add_argument("--by", required=True)
    q.add_argument("--name", default=None)
    q.add_argument("--note", default="")
    q.add_argument("--campus", default=None)

    p = sub.add_parser("campus", help="敷地の立ち上げ（敷地 JSON の骨組み・未解決の点検）")
    csub = p.add_subparsers(dest="campus_cmd", required=True)
    q = csub.add_parser("init", help="data/site/<slug>.json の骨組みを作る（あれば上書きしない）")
    q.add_argument("slug")
    q.add_argument("--university", required=True)
    q.add_argument("--campus-name", required=True)
    q.add_argument("--bbox", nargs=4, type=float, default=None, metavar=("LAT0", "LON0", "LAT1", "LON1"),
                   help="キャンパスを囲む緯度経度。原点（中心）と平面直角座標系を決め、OSM の境界を探す範囲にもなる")
    q.add_argument("--osm-relation", type=int, default=None, help="敷地の境界の OSM relation 番号（分かれば）")
    q.add_argument("--osm-way", type=int, default=None, help="敷地の境界の OSM way 番号（境界が way 1 本の大学）")
    q = csub.add_parser("check", help="名前の対応・範囲・人に聞くこと・公式図の許可の未解決を一覧（問題があれば終了コード 1）")
    q.add_argument("slug", nargs="?", default=None)

    p = sub.add_parser("photos")
    psub = p.add_subparsers(dest="photos_cmd", required=True)
    q = psub.add_parser("import")
    q.add_argument("folder")
    q.add_argument("--campus", default=None)
    q.add_argument("--period", choices=["before", "during"], default=None,
                   help="期間前 / 期間中。省略すると敷地 JSON の photo_policy.period_start（あれば）より前の撮影を before、決まりが無ければ during")
    q.add_argument("--ledger", default=None, help="写真台帳.json。棟の候補を source 付きで入れる")
    q = psub.add_parser("list")
    q.add_argument("--building", default=None, help="この棟の写真だけ")
    q.add_argument("--ruler", default=None, help="物差しの印がある写真だけ（例 tape_measure。測量係がメジャーの写真を探す）")
    q = psub.add_parser("assign", help="shiwake の結果（assignments.json）を索引に書く")
    q.add_argument("--from", dest="from_json", required=True)
    q.add_argument("--campus", default=None)
    q = psub.add_parser("set-face", help="仕分け係の結果（out/photos/shiwake_*.json）の面・棟を索引に書く（鍵付き。係は索引を自分で書かない）")
    q.add_argument("--from", dest="from_json", required=True, help="out/photos/shiwake_<…>.json（\"photos\": 写真 id → {building, face, faces, …}）")
    q.add_argument("--by", default="仕分け係", help="notes に残す書き手（例「仕分け係（opus）」）")
    q.add_argument("--building", default=None, help="呼び出した棟 id（行の棟が null の写真はこの棟の写真だけ書く。J-004。省略で出力の \"building\"）")
    from . import photos as _photos_t4   # relink・send・tape・hogo-grid・masks・publish（11/8 T4）
    _photos_t4.add_t4_args(psub)
    q = psub.add_parser("to-confirm", help="根拠の門：measured の根拠の写真のうち、仕分けと合わない・low で未確認の物（out/<棟>/to_confirm.json）")
    q.add_argument("building", help="data/buildings/<id>.json")
    q = psub.add_parser("confirm", help="職員が仕分けを確かめる（棟・面を渡せば直してから。渡さなければ「合っている」）")
    q.add_argument("photo")
    q.add_argument("--building", default=None)
    q.add_argument("--face", default=None)
    q.add_argument("--faces", default=None, help="複数の面（カンマ区切り。主の面を先頭）")
    q.add_argument("--by", default="staff")
    q = psub.add_parser("folders", help="職員が data/photos/<日付>/<棟名>/ に手で分けた写真を索引に反映し、フォルダを棟 id に改名する")
    q.add_argument("folder", help="日付のフォルダ（例 data/photos/20261105）")
    q.add_argument("--campus", default=None)
    q.add_argument("--no-rename", action="store_true", help="フォルダ名を変えない（索引の file だけ合わせる）")
    q = psub.add_parser("rectify")
    q.add_argument("file")
    q.add_argument("--f35", type=float, default=None, help="35mm 換算の焦点距離。省略すると EXIF、無ければ消失点から推定")
    q.add_argument("--out", default="out/rectified")
    q.add_argument("--roi", default=None, help="注目する壁の範囲 x0,y0,x1,y1（画素か 0〜1 の割合）。1枚に複数の面があるときに判断役が指定")
    q = psub.add_parser("measure")
    q.add_argument("front_jpg", help="rectify が出した *_front.jpg")
    q.add_argument("--known", default=None,
                   help="既知の長さ 例: floor_pitch=3.99 / bay_pitch=5.3 / wall_width=47.7。"
                        "メジャーを写した区間は segment=<m>@<正面化画像の px>（例 segment=0.698@85.3。常に measured）")
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
    q.add_argument("--via", default=None, help="鎖の登録：登録済みの写真 id（カンマ区切り）。平らな壁でない写真（階段・デッキ・角）を、"
                                              "その写真との特徴点の対応から視点で登録する（kind pose。--face は要らない）")

    q = psub.add_parser("register-chain", help="未登録の写真を、登録済みと重なる順に鎖の登録で試し、増えなくなるまで繰り返す")
    q.add_argument("building", help="棟 JSON")
    q.add_argument("--n-via", type=int, default=3, help="1 枚に使う登録済みの写真の数（重なりの多い順）")
    q.add_argument("--photos", default=None, help="試す写真 id（カンマ区切り。頭だけでも可。省略で棟の未登録全部）")
    q.add_argument("--registry", default=None)

    q = psub.add_parser("register-sfm", help="平面の鎖で載らない写真（中庭・ピロティ・段・デッキ）を、写真どうしの対応だけでまとめて"
                                             "解き（COLMAP の SfM）、登録済みの写真に合わせて世界座標で登録する（kind pose）")
    q.add_argument("building", help="棟 JSON")
    q.add_argument("--photos", default=None, help="登録を試す写真 id（カンマ区切り。頭だけでも可。省略で棟の未登録全部）")
    q.add_argument("--max", type=int, default=None, help="登録を試す未登録の写真の枚数の上限（登録済みは合わせの基準として全部入れる）")
    q.add_argument("--budget-min", type=float, default=None, help="照合の時間の上限（分）。超えたら途中で止め、同じコマンドで続きから")
    q.add_argument("--features", choices=["rootsift", "aliked-lightglue"], default="rootsift",
                   help="特徴点と照合の方式（既定 rootsift。aliked-lightglue は torch と lightglue が要る）")
    q.add_argument("--registry", default=None)
    from . import registry as _registry      # photos unregister／exclude（11/8 J-013・J-027）
    _registry.add_cli(psub)

    q = psub.add_parser("landmarks", help="目印の登録の準備：棟 JSON から目印の一覧と面の絵、写真の升目の絵、目印係への依頼（out/<棟>/landmarks/）")
    q.add_argument("building", help="棟 JSON")
    q.add_argument("--photos", default=None, help="写真 id（カンマ区切り。頭だけでも可。省略で棟の未登録全部）")
    q.add_argument("--include-registered", action="store_true")
    q = psub.add_parser("landmarks-crop", help="写真の升を 1.25 倍に広げて切り出し、寄りの升目の絵を出す（目印係が寄りの升を指す）")
    q.add_argument("building", help="棟 JSON")
    q.add_argument("--photo", required=True)
    q.add_argument("--cells", required=True, help="升（カンマ区切り。例 C7,D3）")
    q = psub.add_parser("windows", help="窓の箱を零ショットで自動検出（Grounding DINO tiny、Apache-2.0）→ out/<棟>/windows/<写真>.json と重ね画像。箱の 4 角は升＋寄りの升でも出す")
    q.add_argument("building", help="棟 JSON")
    q.add_argument("--photos", required=True, help="写真 id（カンマ区切り。頭だけでも可）")
    q.add_argument("--box-threshold", type=float, default=None, help="箱のスコアの下限（既定 0.3）")
    q.add_argument("--tiles", type=int, default=None, help="n×n の切り出しでも推論する（遠い・小さい窓。既定 1＝全体だけ）")
    q = psub.add_parser("pnp", help="目印の対応（picks JSON）から視点を解き、合否を出して台帳に登録する")
    q.add_argument("building", help="棟 JSON")
    q.add_argument("--picks", required=True, help='{"photo", "picks": [{"id", "cell", "sub"} | {"id", "u", "v"}]} の JSON')
    q.add_argument("--no-register", action="store_true")
    q.add_argument("--method", default="cv2", choices=["cv2", "poselib", "poselib+geocalib"],
                   help="解き方：cv2（従来）／poselib（PoseLib、線の対応 {id: <面>-V01, cells: [C3, C8]} も使う）／poselib+geocalib（GeoCalib の焦点と重力の向きを事前値に）")
    q.add_argument("--check-jpg", nargs="?", const="", default=None,
                   help="合否に関わらず重ね画像を出す（既定 out/<棟>/landmarks/check_<写真id>.jpg。パスを書けばそこへ）")
    q = psub.add_parser("locate", help="模型合わせ：GPS を初期値に、写真 1 枚ごとに模型の縁が写真の縁に合う視点を探して登録する（重なり不要）")
    q.add_argument("building", help="棟 JSON")
    q.add_argument("--photos", default=None, help="試す写真 id（カンマ区切り。頭だけでも可。省略で棟の GPS 付き未登録全部）")
    q.add_argument("--max", type=int, default=None)
    q.add_argument("--radius", type=float, default=None, help="GPS の周りの探索半径 m（既定 40）")
    q.add_argument("--no-register", action="store_true", help="台帳に書かない（結果と重ね画像だけ）")
    q.add_argument("--truth", action="store_true", help="登録済みの写真も試し、既知の視点との差を出す（台帳は書かない）")
    q.add_argument("--registry", default=None)
    q = psub.add_parser("depth", help="写真 1 枚の深さ m（Depth Pro か Depth Anything V2 Small）→ out/<棟>/depth/。--eval で登録済みの写真で評価")
    q.add_argument("building", help="棟 JSON")
    q.add_argument("--photos", default=None, help="写真 id（カンマ区切り）")
    q.add_argument("--model", default="depth_pro", choices=["depth_pro", "da2_small"])
    q.add_argument("--eval", action="store_true", help="台帳の登録済みの写真全部で評価し out/experiments/depth_eval.json に書く")
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
    q.add_argument("--max-usd", type=float, default=20.0, help="棟ごとの費用の上限（USD、既定 20 = agents.DEFAULT_MAX_USD）。当たったら止めて記録。0 で上限なし")
    q = asub.add_parser("resume", help="止まった pipeline・continue を out/<棟>/pipeline_state.json から続ける（429・上限・落ち・事件）")
    q.add_argument("building")
    q.add_argument("--dry-run", action="store_true")
    q.add_argument("--max-usd", type=float, default=None, help="費用の上限を上げて続ける時（USD）")
    q = asub.add_parser("continue", help="追記：前回の棟 JSON を保ち、新しい写真で 撮影→仕分け→関係する面の測量・設計・検収→製図・web")
    q.add_argument("building")
    q.add_argument("--photos", default=None, help="新しい写真のフォルダ（省略で索引に取り込み済みの物）")
    q.add_argument("--faces", default=None, help="カンマ区切りの面 id（省略で撮影係の答えと仕分けで決まる面）")
    q.add_argument("--steps", default=None, help="カンマ区切り（既定: 追記の工程）")
    q.add_argument("--dry-run", action="store_true")
    q.add_argument("--max-usd", type=float, default=20.0, help="棟ごとの費用の上限（USD、既定 20 = agents.DEFAULT_MAX_USD）。0 で上限なし")
    q = asub.add_parser("tasks", help="各工程の依頼文（係の定義のパス＋task）を out/<棟>/agent_tasks.json・.md に出す")
    q.add_argument("building")
    q.add_argument("--photos", default=None, help="取り込む写真フォルダ（pipeline と同じ値を渡す）")
    q = asub.add_parser("events", help="棟（または --site で敷地全体）の出来事を検出")
    q.add_argument("building", nargs="?", default=None)
    q.add_argument("--site", action="store_true")
    q.add_argument("--campus", default=None)
    q = asub.add_parser("decide", help="進行役の決定（語彙・アセットの追加の承認か却下）を data/decisions.json に書く")
    q.add_argument("--kind", required=True, choices=["vocab", "asset"])
    q.add_argument("--target", required=True)
    q.add_argument("--decision", required=True, choices=["approve", "deny"])
    q.add_argument("--why", required=True)
    q.add_argument("--building", default=None)
    q.add_argument("--request", default=None)
    q.add_argument("--scope", default=None)
    q = asub.add_parser("log", help="サブエージェントの試走の 1 工程を out/<棟>/pipeline_<日時>.json に追記")
    q.add_argument("building")
    q.add_argument("--step", required=True)
    q.add_argument("--model", default="opus")
    q.add_argument("--seconds", type=float, default=None)
    q.add_argument("--tokens", type=int, default=None)
    q.add_argument("--note", default="")
    q.add_argument("--record", default=None, help="追記する記録（省略で runner subagent の最新）")
    q.add_argument("--new", action="store_true", help="新しい記録を始める")
    q.add_argument("--started-at", default=None, help="工程を始めた時刻（ISO、例 2026-11-07T09:12:03）")
    q.add_argument("--ended-at", default=None, help="工程が終わった時刻（ISO）。省略で記録した時刻")
    q.add_argument("--attempt", type=int, default=None, help="何回目か（やり直しで 2、3）")
    q.add_argument("--result", default=None, choices=["ok", "retry", "skipped", "failed"], help="この回の結果")
    q.add_argument("--tool-uses", type=int, default=None, help="係が使った道具の回数（Agent の結果の tool_uses）")

    p = sub.add_parser("vocab", help="語彙（部品の型）の台帳 data/vocab.json")
    vsub = p.add_subparsers(dest="vocab_cmd", required=True)
    vsub.add_parser("list")

    p = sub.add_parser("assets", help="CC0 アセットの台帳と取得")
    xsub = p.add_subparsers(dest="assets_cmd", required=True)
    q = xsub.add_parser("list")
    q.add_argument("--kind", default=None, choices=["model", "texture", "hdri"])
    q = xsub.add_parser("fetch")
    q.add_argument("ids", nargs="*")

    # 証拠ビューア（Rerun の .rrd。11/6 追加。ここ 1 か所で引数と実行をまとめる）
    from . import evidence as _evidence
    _evidence.add_args(sub.add_parser("evidence", help="写真の視点・目印の対応・再投影・根拠の件数を Rerun の .rrd に（out/<棟>/evidence.rrd）"))
    if (sys.argv[1:] if argv is None else list(argv))[:1] == ["evidence"]:
        return _evidence.run_cli(ap.parse_args(argv))

    # 進捗報告（棟ごとに 1 ページの HTML。11/6 追加。ここ 1 か所で引数と実行をまとめる）
    from . import report as _report
    _report.add_args(sub.add_parser("report", help="進捗報告：写真 → 立面図 → 証拠 → 模型 → 歩ける 3D → まだ無い物 を 1 ページに（out/<棟>/report.html）"))
    if (sys.argv[1:] if argv is None else list(argv))[:1] == ["report"]:
        return _report.run_cli(ap.parse_args(argv))

    # 進捗報告の絵を道具で撮る（証拠ビューア・歩く画面。11/6 追加。ここ 1 か所で引数と実行をまとめる）
    from . import shots as _shots
    _shots.add_args(sub.add_parser("shots", help="進捗報告の絵を撮る：証拠ビューア（evidence_<種>.png）と歩く画面（walk.png）"))
    if (sys.argv[1:] if argv is None else list(argv))[:1] == ["shots"]:
        return _shots.run_cli(ap.parse_args(argv))

    # 職員の画面の受付（ローカルのサーバ。11/6 追加。ここ 1 か所で引数と実行をまとめる）
    from . import serve as _serve
    _serve.add_args(sub.add_parser("serve", help="職員の画面 web/staff.html の受付：写真の取り込み → 振り分け → 棟ごとの pipeline の起動と進捗（既定 127.0.0.1:8766）"))
    if (sys.argv[1:] if argv is None else list(argv))[:1] == ["serve"]:
        return _serve.run_cli(ap.parse_args(argv))

    # 仕分け係の当たり率（期間前の台帳の棟と比べる。11/6 追加。ここ 1 か所で引数と実行をまとめる）
    from . import shiwake_eval as _shiwake_eval
    _shiwake_eval.add_args(sub.add_parser("shiwake-eval", help="仕分け係の当たり率：仕分け係が確定した棟（面）と期間前の台帳の棟の一致・不一致・判定不能（out/shiwake_eval.json）"))
    if (sys.argv[1:] if argv is None else list(argv))[:1] == ["shiwake-eval"]:
        return _shiwake_eval.run_cli(ap.parse_args(argv))

    # 測量：メジャーからタイルの周期・面の周期の一様・根拠の鎖（K-101。11/8 追加。ここ 1 か所で引数と実行をまとめる）
    from . import measure as _measure
    _measure.add_args(sub.add_parser("measure", help="メジャーの読み → タイルの周期（tape）→ 面の周期の一様（uniform）→ 枚数で長さ（chain）。sokuryo_tape_*/sokuryo_chain_*.json"))
    if (sys.argv[1:] if argv is None else list(argv))[:1] == ["measure"]:
        return _measure.run_cli(ap.parse_args(argv))

    a = ap.parse_args(argv)
    need = _ledger_need(a)
    if need:   # 鍵の待ちを棟ごとに数えるため、label にコマンドの引数（棟 id やパスが入る）を付ける
        need = (need + " " + " ".join((sys.argv[1:] if argv is None else list(argv))[2:]))[:240].strip()
        # 共有の台帳（写真の索引・登録台帳・site.json・決定・CC0 の台帳）を書くコマンドは、読む所から保存する所まで
        # 鍵を持つ（4 棟を同時に回しても書き込みが消えない。ledger.py。審査のコードレビュー A7 の直し）
        from . import ledger
        with ledger.lock("ledger", label=need):
            return _dispatch(a)
    return _dispatch(a)


# 鍵（ledger）が要るコマンド：(コマンド, サブコマンド)。サブコマンドが None の物はコマンド全体
LEDGER_WRITERS = {
    ("photos", "assign"), ("photos", "folders"), ("photos", "set-face"), ("photos", "confirm"),
    ("photos", "locate"), ("photos", "landmarks"),
    ("photos", "register"),
    # photos import も入れない：EXIF・縮小・顔の検出は鍵の外、索引は import_photos が鍵を取って差分だけ書く（11/8 F3）
    # pnp・register-chain・register-sfm・unregister・exclude は入れない：計算は鍵の外、登録台帳の書き込みは
    # registry.save_registry が鍵を取って読み直し、差分だけ書く（11/8 J-012）
    ("site", "export-web"), ("site", "build"), ("site", "confirm"), ("site", "link-box"), ("campus", "init"),
    ("agent", "decide"), ("assets", "fetch"),
}


def _ledger_need(a) -> str | None:
    sub = {"photos": "photos_cmd", "site": "site_cmd", "campus": "campus_cmd", "agent": "agent_cmd",
           "assets": "assets_cmd"}.get(a.cmd)
    key = (a.cmd, getattr(a, sub, None) if sub else None)
    return f"recorder {key[0]} {key[1]}" if key in LEDGER_WRITERS else None


def _dispatch(a) -> int:
    if a.cmd == "agent":
        return _agent(a)
    if a.cmd == "vocab":
        from . import decisions

        print("\n".join(decisions.vocab_list_lines()))
        return 0

    if a.cmd == "site":
        return _site(a)
    if a.cmd == "campus":
        return _campus(a)
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
            warns = schema.warn_building(b)
            if errs or (a.strict and warns):
                bad += 1
                print(f"NG {f}  エラー {len(errs)}  警告 {len(warns)}{'（--strict でエラー扱い）' if a.strict and warns else ''}")
                for e in errs:
                    print(f"   - {e}")
            else:
                print(f"OK {f}  警告 {len(warns)}")
            for w in warns:
                print(f"   ! {w}")
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
        from .evidence import observations, observations_line

        # 値は今まで通り run.count_statuses の数え方（status と value を持つ物）。観察はその値を (状態, 方法, 根拠, 種類) で束ねた数
        o = observations(schema.load_building(a.file), values_only=True)
        print(f"{observations_line(o)}  撮影依頼 open {r['requests_open']}")
        print(f"報告: {Path(r['out']) / 'run_report.json'}  合計 {r['seconds_total']}s")
        return 0 if r["ok"] else 1

    if a.cmd == "counts":
        from .evidence import OBS_NOTE, observations, observations_line

        if a.json:   # measured を直接（メジャーで直接読んだ区間）／鎖／個数に分ける（K-101）
            import json as _json

            rows = []
            for f in a.files:
                b = schema.load_building(f)
                rows.append({"file": f, "measured_kinds": schema.measured_kinds(b), "observations": observations(b, top=a.top)})
            print(_json.dumps(rows, ensure_ascii=False, indent=2, default=str))
            return 0
        for f in a.files:
            b = schema.load_building(f)
            o = observations(b, top=a.top)
            print(f"{f}")
            print(f"  {observations_line(o)}")
            print(f"  {schema.measured_kinds_line(schema.measured_kinds(b))}")
            e = o["eyeball"]
            print(f"  方法が「目視」だけの値 {e['values']}（観察 {e['observations']}）")
            if a.faces:
                for fid, t in o["faces"].items():
                    print(f"    {fid:<8} 値 {t['values']:>4}　観察 {t['observations']:>3}")
                print(f"    {'面の外':<8} 値 {o['other']['values']:>4}　観察 {o['other']['observations']:>3}")
            for t in o["top_copies"]:
                if t["copies"] < 2:
                    break
                print(f"    ×{t['copies']:<4} {t['status']:<9} {t['kind']}　方法「{t['method'] or '—'}」　根拠 {len(t['evidence'])} 件")
        print(OBS_NOTE)
        return 0

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

    if a.agent_cmd == "decide":
        from . import decisions

        try:
            rec = decisions.add_decision(a.kind, a.target, a.decision, a.why, building=a.building, request=a.request, scope=a.scope)
        except ValueError as e:
            print(f"エラー: {e}")
            return 2
        print(f"{rec['id']} {rec['kind']} {rec['decision']}: {rec['target']}")
        return 0
    if a.agent_cmd == "events":
        from .agents import detect_events   # 別の係が作る。無ければここで落ちる（--help は落ちない）

        if not a.site and not a.building:
            print("エラー: 棟 id か --site を指定してください")
            return 2
        doc = detect_events(None if a.site else a.building, cwd=".")
        if isinstance(doc, dict) and doc.get("markdown"):
            print(doc["markdown"])
        else:
            print(json.dumps(doc, ensure_ascii=False, indent=1))
        return 0
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
                                  record=a.record, new=a.new, started_at=a.started_at, ended_at=a.ended_at,
                                  attempt=a.attempt, result=a.result, tool_uses=a.tool_uses)
        elif a.agent_cmd == "resume":
            res = agents.resume_pipeline(a.building, dry_run=a.dry_run, max_usd=a.max_usd)
        elif a.agent_cmd == "continue":
            res = agents.continue_pipeline(a.building, a.photos, faces=a.faces.split(",") if a.faces else None,
                                           steps=a.steps.split(",") if a.steps else None, dry_run=a.dry_run, max_usd=(a.max_usd or None))
        else:
            steps = a.steps.split(",") if a.steps else None
            res = agents.run_pipeline(a.building, a.photos, steps=steps, dry_run=a.dry_run, max_usd=(a.max_usd or None))
    except agents.AgentError as e:
        print(f"エラー: {e}")
        return 1
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


def _photos(a) -> int:
    import json

    from . import photos, site

    if a.photos_cmd in photos.T4_CMDS:   # 11/8 T4（鍵は photos.run_t4 が持つ）
        return photos.run_t4(a)
    if a.photos_cmd == "assign":
        from .photos import assign_photos   # 別の係が作る

        assignments = json.loads(Path(a.from_json).read_text(encoding="utf-8"))
        res = assign_photos(index_path=Path("data/photos/index.json"), assignments=assignments,
                            campus=a.campus or site.default_campus())
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    if a.photos_cmd == "to-confirm":
        from .schema import load_building
        res = photos.write_to_confirm(load_building(a.building), Path("."))
        for it in res["items"]:
            print(f"{it['photo']}: {it['detail']}（使われた所 {len(it['used_at'])}：{it['used_at'][0]}）")
        print(f"確かめてほしい写真 {len(res['items'])} 枚 → {res['path']}")
        return 0
    if a.photos_cmd == "confirm":
        fs = [x.strip() for x in a.faces.split(",") if x.strip()] if a.faces else None
        res = photos.confirm_photo(photos.INDEX_PATH, a.photo, by=a.by, building=a.building, face=a.face, faces=fs)
        print(json.dumps({k: v for k, v in res.items() if k != "record"}, ensure_ascii=False))
        return 0
    if a.photos_cmd == "set-face":
        doc = json.loads(Path(a.from_json).read_text(encoding="utf-8"))
        res = photos.set_faces(photos.INDEX_PATH, doc, a.by, building=a.building)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    if a.photos_cmd == "folders":
        res = photos.assign_from_folders(Path(a.folder), campus=a.campus or site.default_campus(), rename=not a.no_rename)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    if a.photos_cmd == "landmarks":
        from . import landmarks

        ph = [x.strip() for x in a.photos.split(",") if x.strip()] if a.photos else None
        landmarks.prepare(Path(a.building), ph, include_registered=a.include_registered)
        return 0
    if a.photos_cmd == "landmarks-crop":
        from . import landmarks
        from .registry import photo_path, photo_record
        from .rectify import read_image
        from .schema import load_building

        b = load_building(a.building)
        rec = photo_record(a.photo)
        if rec is None:
            print(f"索引に無い: {a.photo}")
            return 2
        img = read_image(photo_path(rec))
        out = Path("out") / b["id"] / "landmarks"
        for cell in [c.strip() for c in a.cells.split(",") if c.strip()]:
            print(landmarks.crop_image(img, cell, out / f"{rec['id']}_{cell.upper()}.jpg"))
        return 0
    if a.photos_cmd in ("unregister", "exclude"):
        from . import registry
        return registry.run_cli(a)
    if a.photos_cmd == "windows":
        from . import windows
        from .schema import load_building

        b = load_building(a.building)
        for ph in [x.strip() for x in a.photos.split(",") if x.strip()]:
            d = windows.detect(ph, box_threshold=a.box_threshold, tiles=a.tiles, building_id=b["id"])
            print(f"{d['photo']}: 窓の箱 {d['n_after_nms']}（大きすぎ {sum(x['too_big'] for x in d['detections'])}）"
                  f" {d['seconds']} s {d['device']} → {windows.cache_path(b['id'], d['photo'], d['params'])}")
        return 0
    if a.photos_cmd == "pnp":
        from . import landmarks

        doc = json.loads(Path(a.picks).read_text(encoding="utf-8"))
        res = landmarks.register_picks(Path(a.building), doc, write=not a.no_register, method=a.method)
        landmarks.release_geocalib()
        print(json.dumps({k: v for k, v in res.items() if k != "camera"}, ensure_ascii=False, indent=1))
        if a.check_jpg is not None:
            from .schema import load_building

            bj = load_building(a.building)
            cj = Path(a.check_jpg) if a.check_jpg else Path("out") / bj["id"] / "landmarks" / f"check_{res['photo']}.jpg"
            got = landmarks.draw_check(bj, res, cj) if res.get("camera") else None
            print(f"重ね画像: {got}" if got else f"重ね画像なし（解く前に止まった: {res.get('code')}）")
        if res.get("status") == "ok":
            c = res["camera"]
            print(f"登録: {res['photo']} 位置 {c['camera_xyz']} 高さ {c['camera_height_m']} m 焦点 {c['focal_px']:.0f} px 面 {[v['face'] for v in res['visible_faces']]}")
        return 0 if res.get("status") == "ok" else 1
    if a.photos_cmd == "locate":
        from . import locate

        ph = [x.strip() for x in a.photos.split(",") if x.strip()] if a.photos else None
        locate.run(Path(a.building), ph, max_n=a.max, register=not (a.no_register or a.truth),
                   radius=a.radius or locate.RADIUS_M, registry_path=Path(a.registry) if a.registry else None,
                   include_registered=a.truth)
        return 0
    if a.photos_cmd == "import":
        sp = site.DATA_SITE / f"{a.campus or site.default_campus()}.json"
        sdoc = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else None
        ledger = photos.load_ledger(Path(a.ledger)) if a.ledger else None
        idx, added = photos.import_photos(Path(a.folder), sdoc, period=a.period, ledger=ledger)
        print(f"索引: {photos.INDEX_PATH}  合計 {len(idx['photos'])} 枚（今回 {len(added)} 枚）")
        print("  期間別:", {k: v for k, v in photos.period_counts(idx, photos.period_start_of(sdoc)).items() if k != "by_building"})
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

        try:
            known = measure.parse_known(a.known)
        except ValueError as e:
            print(f"--known が読めません: {e}")
            return 2
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
        if a.via:
            from . import chain

            if not a.photo:
                print("写真 id が要ります")
                return 1
            try:
                e = chain.register_via(a.photo, Path(a.building), [v.strip() for v in a.via.split(",") if v.strip()], reg)
            except chain.ChainFail as ex:
                print(f"登録できません（{ex.code}）: {ex}")
                for k, v in (ex.stats.get("via") or {}).items():
                    print(f"  via {k}: {v}")
                rec = registry.photo_record(a.photo) or {}
                key = (rec.get("id", a.photo))
                reg["not_registered"] = [n for n in reg.get("not_registered", []) if n.get("photo") != key] + [
                    {"photo": key, "building": registry.load_building(Path(a.building))["id"], "face": rec.get("face"),
                     "period": rec.get("period"), "reason": f"鎖の登録（--via {a.via}）: {ex}", "chain_code": ex.code}]
                registry.save_registry(reg, rp)
                return 1
            registry.upsert(reg, e)
            registry.save_registry(reg, rp)
            c = e["check"]
            print(f"登録（pose）: {e['photo']}  再投影 {c['reproj_rms_px']} px（中央 {c['reproj_median_px']}・最大 {c['reproj_max_px']}）"
                  f"  点 {c['n_points']} / {c['n_candidates']}  カメラ {e['camera']['camera_xyz']}（地面から {c['camera_height_m']} m）")
            print(f"  見えている面: {', '.join(v['face'] + '（' + str(v['area_m2']) + ' m²）' for v in e['visible_faces'])}")
            print(f"  点の出所: {c['points_by_face']}  台帳: {rp}")
            return 0
        if not (a.photo and a.face):
            print("写真 id と --face が要ります（一括は --from-existing、平らでない写真は --via）")
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
    if a.photos_cmd == "register-chain":
        from . import chain, registry

        rp = Path(a.registry) if a.registry else registry.REGISTRY_PATH
        reg = registry.load_registry(rp)
        res = chain.register_chain(Path(a.building), reg, n_via=a.n_via,
                                   photos=[p.strip() for p in a.photos.split(",")] if a.photos else None)
        registry.save_registry(reg, rp)
        print(f"登録できた写真 {res['added']} 枚（{' → '.join(str(r['added']) for r in res['rounds'])}）")
        print(f"登録できない写真 {len(res['failed'])} 枚。理由の分布: {res['reasons']}")
        for pid, why in sorted(res["failed"].items()):
            print(f"  {pid[:40]:<40} {why}")
        print(f"台帳: {rp}")
        return 0
    if a.photos_cmd == "register-sfm":
        from . import registry, sfm

        rp = Path(a.registry) if a.registry else registry.REGISTRY_PATH
        reg = registry.load_registry(rp)
        res = sfm.register_sfm(Path(a.building), reg, photos=[p.strip() for p in a.photos.split(",")] if a.photos else None,
                               max_n=a.max, budget_s=a.budget_min * 60 if a.budget_min else None, features=a.features)
        if res.get("pending"):
            print(f"照合が途中です（残り {res['pending']} 組 / 全 {res['n_pairs']} 組）。同じコマンドで続きから回してください")
            return 2
        registry.save_registry(reg, rp)
        for al in res["aligns"]:
            print(f"模型 {al['model']}（{al['n_images']} 枚）: 合わせ {'可' if al['ok'] else '不可'}  基準 {len(al.get('used', []))} 枚"
                  f"  残差 RMS {al.get('rms_m')} m  最大 {max((al.get('residual_m') or {0: None}).values(), key=lambda v: v or 0)} m"
                  f"  外した {al.get('dropped')}" + (f"  理由: {al['reason']}" if not al["ok"] else ""))
        by_p: dict = {}
        for e in res["added"]:
            by_p[e.get("period")] = by_p.get(e.get("period"), 0) + 1
        print(f"登録できた写真 {len(res['added'])} 枚 {by_p}")
        for e in res["added"]:
            c = e["check"]
            print(f"  登録: {e['photo'][:40]:<40} {c['reproj_rms_px']} px  点 {c['n_points3d']}  高さ {c['camera_height_m']} m"
                  f"  面 {','.join(v['face'] for v in e['visible_faces'])}")
        print(f"登録できない写真 {len(res['failed'])} 枚。理由の分布: {res['reasons']}")
        for pid, (code, why) in sorted(res["failed"].items()):
            print(f"  {pid[:40]:<40} {why}")
        print(f"台帳: {rp}")
        return 0
    if a.photos_cmd == "depth":
        from . import depth
        from .schema import load_building

        bid = load_building(a.building)["id"]
        if a.eval:
            r = depth.evaluate([a.model])
            m = r["models"][a.model]
            print(f"{a.model}: {r['n_photos']} 枚 写真ごとの比の中央値 {m['grid']['photo_medians']} → {m['grid']['judge']}")
        for ph in [x.strip() for x in (a.photos or "").split(",") if x.strip()]:
            s = depth.predict(ph, a.model, bid)
            print(f"{s['photo']}: 深さの中央値 {s['depth_m']['median']} m 焦点 {s['focal_px']} px（EXIF 比 {s['focal_ratio']}）"
                  f" {s['seconds']} s {s['device']} → {s['npz']}")
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
        for r in photos.load_index(photos.INDEX_PATH)["photos"]:
            if a.building and r.get("building") != a.building:
                continue
            if a.ruler and r.get("ruler") != a.ruler:
                continue
            line = photos.summary_line(r)
            if r.get("ruler"):
                line += f"  [物差し {r['ruler']}：{r.get('ruler_note') or ''}]"
            print(line)
        return 0
    return 2


def _site(a) -> int:
    import json
    import time

    from . import site

    if a.site_cmd == "build":
        a.campus = a.campus or site.default_campus()
        a.plateau = a.plateau or site.plateau_zip(a.campus)
        t = time.time()
        try:   # 境界は敷地 JSON の sources.osm.relation / way（無ければ bbox_lonlat から探す）。原点と系も敷地 JSON
            osm = site.osm_for_campus(a.campus)
        except (ValueError, FileNotFoundError) as e:
            print(f"エラー: {e}")
            site.add_question(a.campus, f"OSM の敷地の境界が決まらない：{e}")
            return 1
        except OSError as e:   # requests の通信の失敗（Overpass の 502 など）。人に聞くことではないので questions に足さない
            print(f"エラー: OSM（Overpass）に届かない・混んでいる：{e}。時間をおいて取り直す")
            return 1
        print(f"OSM: 境界 {osm['boundary_ref']}" + (f"（{osm['found_by']}）" if osm.get("found_by") else ""))
        print(f"OSM: 建物 {len(osm['buildings'])} 件、敷地境界 {len(osm['boundary'])} 面 ({time.time() - t:.0f}s)")
        meta = site.fetch_aerial(osm["boundary"])
        print(f"航空写真: {meta['width']}x{meta['height']} px、タイル {meta['tiles']} 枚 ({time.time() - t:.0f}s)")
        dem = site.fetch_dem(osm["boundary"])
        print(f"標高: 格子 {dem['shape']}、{dem['z_min']:.1f}〜{dem['z_max']:.1f} m、欠け {dem['nan_ratio']:.1%} ({time.time() - t:.0f}s)")
        pl = None
        if not a.no_plateau and a.plateau and Path(a.plateau).exists():
            src0 = site.load_campus(a.campus).get("sources") or {}   # 出典の記述は敷地 JSON（同じ zip の時）か zip の名前から
            same = bool(src0.get("plateau_zip")) and Path(src0["plateau_zip"]) == Path(a.plateau)
            pl = site.extract_plateau(a.plateau, osm["boundary"], source=src0.get("plateau") if same else None)
            print(f"PLATEAU: 敷地内の建物 {len(pl['buildings'])} 件 ({time.time() - t:.0f}s)")
        else:
            print("PLATEAU: 省略（zip が無いか --no-plateau）。土台は OSM の輪郭だけ（高さは unknown）")
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
        r = site.export_web(a.campus or site.default_campus(), with_coverage=a.with_coverage,
                            web_dir=Path(a.out) if a.out else None)
        print(f"Web 用: {r['path']}  箱 {r['boxes']} 件、模型 {r['models']}、地面格子 {r['grid']}（標高の欠けを近傍で埋めた割合 {r['nan_filled']:.1%}）")
        return 0

    if a.site_cmd == "objects":
        from . import site_objects as so
        doc = json.loads((site.DATA_SITE / f"{a.campus or site.default_campus()}.json").read_text(encoding="utf-8"))
        bs = so.load_buildings(so.ends_buildings(doc))
        errs, warns = so.validate_objects(doc, bs, so.load_catalog_ids())
        for e in errs:
            print("   - [誤り] " + e)
        for w in warns:
            print("   - [警告] " + w)
        if a.action == "check":
            for it in so.check_objects(doc, bs):
                print(f"{'OK ' if it['ok'] else 'NG '} {it['what']}：差 {it['diff_m']:+.2f} m（± {it['tol_m']:.2f}）")
            print(f"敷地の物 {len(so.objects_of(doc))} 件、誤り {len(errs)}、警告 {len(warns)}")
            return 1 if errs else 0
        if not a.out:
            print("--out が要る（疑似本番の web/data/ は上書きしない）")
            return 2
        r = so.export_web_objects(doc, Path(a.out) / "data", bs)
        print(f"glb: {r['glb']}（三角形 {r['tris']}）")
        for s in r["skipped"]:
            print("   - 描かない: " + s)
        return 1 if r["errors"] else 0

    if a.site_cmd == "link-box":
        path = site.DATA_SITE / f"{a.campus or site.default_campus()}.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        r = site.link_box(doc, a.target, a.box, a.why, a.by, ask=a.ask)
        for k, v in r["overlap"]["by"].items():
            print(f"   {k}: 箱の足元 {r['overlap']['box_area_m2']} m² のうち {v['overlap_m2']} m²（{v['inside_ratio']:.1%}）が {r['target']} の中")
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        msg = {"linked": f"結び付けた：{r['box']} → {r['target']} の plateau_parts（前は {r['held_by']}）",
               "replaced": f"置き換えの印：{r['box']} は敷地の物 {r['target']} に置き換わった（export-web で描かない）",
               "request": f"結ばない（一番大きい重なり {r['overlap']['best']:.1%} < {r['min_ratio']:.0%}）。撮影依頼 {r.get('request')} を敷地 JSON の requests に書いた"}
        print(msg[r["outcome"]])
        return 0

    if a.site_cmd == "confirm":
        path = site.DATA_SITE / f"{a.campus or site.default_campus()}.json"
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


def _campus(a) -> int:
    import json

    from . import site

    if a.campus_cmd == "init":
        path, made = site.campus_init(a.slug, a.university, a.campus_name, tuple(a.bbox) if a.bbox else None,
                                      osm_relation=a.osm_relation, osm_way=a.osm_way)
        if made:
            print(f"敷地 JSON の骨組みを作りました: {path}\n次：`site build --campus {a.slug}`（OSM の境界は sources.osm.relation / way、"
                  "無ければ bbox の中の大学を探す）→ aliases・name_links・scope を埋める")
            return 0
        print(f"既にあるので上書きしません: {path}")
        return 1
    try:
        slug = a.slug or site.default_campus()
        res = site.campus_check(slug, out=site.OUT_SITE / "campus_check.json")
    except (ValueError, FileNotFoundError) as e:
        print(f"エラー: {e}")
        return 1
    print("\n".join(site.campus_check_lines(res)))
    print(f"（{site.OUT_SITE / 'campus_check.json'} にも書きました）")
    return 0 if res["ok"] else 1


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
