"""職員の画面（web/staff.html）の受付をする小さなローカルのサーバ `recorder serve`。

本番の形は「職員の PC 1 台でローカルに動く」。標準ライブラリの http.server だけで、依存を足さない。
判断や計算はしない。既存の関数（photos.import_photos / photos.assign_photos / agents.run_pipeline /
site.campus_init）をそのまま呼ぶだけの受付。

配信：リポジトリ直下の web/・out/・data/photos/ だけ（それ以外の道は 404）。`/` は /web/staff.html へ。
API（どれも JSON を返し、失敗してもサーバは落ちない）：
    GET  /api/site                 web/data/site.json の有無と scope.targets、data/site/ の敷地 JSON の一覧（campuses）と選んだ物
    POST /api/campus               {"university", "campus_name", "slug"?, "bbox"? | "address"?, "osm_relation"?, "osm_way"?}
                                   data/site/<slug>.json が無ければ campus_init（bbox か住所が要る。住所は OSM の Nominatim で範囲に）
    POST /api/campus/select        {"campus"} このサーバで使う敷地を選ぶ（環境変数 RECORDER_CAMPUS にも入れ、係に渡る）
    POST /api/site/build           {"campus"?} 敷地の取得（`recorder site build` → `site export-web`）を別スレッドで。
                                   進捗は /api/progress の "site"
    POST /api/photos               multipart：写真（photos）＋ assignments（JSON 文字列）→ 保存 → 取り込み → 振り分け
    POST /api/start                {"buildings": [...], "dry_run": false, "max_usd": n?} 棟ごとに別スレッドで。棟 JSON
                                   （data/buildings/<棟>.json）がある棟は追記（agents.continue_pipeline、前回の値を保って
                                   新しい写真の分だけ育てる）、無い棟は run_pipeline。max_usd は棟ごとの費用の上限
                                   （省けば agents.DEFAULT_MAX_USD＝40 USD、0 で上限なし）
    POST /api/resume               {"building", "max_usd"?, "redo_from"?, "decision_file"?} 止まった棟を agents.resume_pipeline で
                                   続ける（別スレッド。11/8、F15）。redo_from・decision_file（data/reports/ の下）は F18。
                                   このサーバで作業中・キーが無い・状態が無い・止まっていない時は断る（409、理由の文）。
                                   費用の上限で止まった棟は、使った額より大きい max_usd が要る
                                   "shinko": true で、止まった記録の事件を進行役（shinko）に自動で決めさせてから続ける（11/8、F19）。
                                   記録が「実行中」のままでも、このサーバで動かしておらず updated_at が 10 分より古ければ
                                   落ちたと見て続けられる（code "stale"）
    POST /api/review               {"building", "n", "verdict": "approve"|"revoke", "note"?} 進行役の自動の決定 n を職員が承認／取り消す
                                   （agents.review_decision。取り消しは halt も書き、工程の輪が次の工程の前で止まる。F19）
    GET  /api/decision?file=…      進行役の決定のファイル（data/reports/ の下の md・txt）の中身を文字で返す（data/reports/ は静的配信しない）
    GET  /api/progress?buildings=a,b   report.json の stages・counts と、動いている pipeline の状態。
                                   pipeline_state.json がある棟は resume（止まっているか・画面から続けられるか）も。
                                   棟 JSON がある棟は unused_staff_photos {n, tape, building_photos, name}（職員が前に送って
                                   棟 JSON にまだ出ない写真。写真を入れずに追記を始められる。F16）も
    GET  /api/steps                工程 → 係の名前・モデル・一言（.claude/agents/*.md の frontmatter と agents の工程の一覧。待機画面）
    GET  /api/pipeline?building=a  最新の pipeline の記録の要約（工程・係・モデル・秒・費用・要約 1 行）と pipeline_state.json の状態
    GET  /api/artifacts?building=a 待機画面の場面に使う、今ある生成物の一覧（写真と灰色の箱・正面化・立面図・製図板・検収・証拠・歩く画面）
    GET  /api/confirm?buildings=a,b    根拠の門で「確かめてほしい」と出た写真（棟ごと。out/<棟>/to_confirm.json も書く）と面の一覧
    POST /api/confirm              {"photo", "building"?, "face"?} 職員が 1 枚を確かめる（棟・面が無ければ「合っている」）
    POST /api/tape                 multipart：メジャーの写真（photos）＋ building・face?・note（11/8、K-102）→ 置く → 取り込み →
                                   職員の申告として棟と ruler: tape_measure・ruler_note（職員のメモ）を付ける
                                   counts.requests は職員宛ての撮影依頼だけ（to="staff"）、counts.tool_requests は
                                   道具・係の作業。どちらも棟 JSON から今の値で数え直す（report.json が古くても正しい）

写真は data/photos/<今日 YYYYMMDD>/<棟 id>/ に置く（git 管理外）。索引 data/photos/index.json に入る。
サーバはリポジトリ直下を cwd にして起動する（敷地 JSON・索引の道は cwd からの相対で読まれるため）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

DEFAULT_PORT = 8766
DEFAULT_HOST = "127.0.0.1"
STATIC_PREFIXES = ("web/", "out/", "data/photos/")
MAX_BODY = 1 << 30            # 1 回の送信の上限（1 GiB）
UNSORTED = "unsorted"         # 棟 id に直せない写真の置き場（振り分けは警告で返す）
_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_IMG_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}

_photos_lock = threading.Lock()   # 索引 index.json の書き込みを 1 本に
_runs_lock = threading.Lock()
RUNS: dict[str, dict] = {}        # 棟 id → pipeline の状態（このサーバが起動した物だけ）
_last_folder: dict[str, str] = {}  # 棟 id → 最後に写真を置いたフォルダ（cwd からの相対）
SELECTED: dict[str, str | None] = {"campus": None}   # 画面で選んだ敷地（無ければ環境変数 → data/site/ の既定）
SITE_RUN: dict = {}               # 敷地の取得（site build）の状態。1 本だけ
CAMPUS_ENV = "RECORDER_CAMPUS"    # agents.CAMPUS_ENV と同じ名前（係の依頼文に敷地 JSON の道を差し込む）


def log(msg: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _jload(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def slug_of(text: str | None) -> str:
    """staff.html の slugOf と同じ：NFKC → 小文字 → 英数字以外を - に。空なら campus。"""
    import unicodedata

    s = unicodedata.normalize("NFKC", text or "campus").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "campus"


def _default_campus() -> str | None:
    """このサーバで使う敷地：画面で選んだ物 → 環境変数 RECORDER_CAMPUS → data/site/ に 1 つならそれ（複数なら default_campus.txt）。"""
    from . import site

    if SELECTED.get("campus"):
        return SELECTED["campus"]
    v = (os.environ.get(CAMPUS_ENV) or "").strip()
    if v:
        return v
    try:
        return site.default_campus()
    except Exception:  # noqa: BLE001  敷地 JSON が無い・複数でも受付は続ける
        return None


def _campuses(root: Path) -> list[dict]:
    """data/site/ の敷地 JSON の一覧（plateau.json を除く）。built は site build 済み（buildings が 1 つ以上）。"""
    d = root / "data" / "site"
    out = []
    for p in (sorted(d.glob("*.json")) if d.is_dir() else []):
        if p.name == "plateau.json":
            continue
        doc = _jload(p)
        if not isinstance(doc, dict):
            continue
        out.append({"campus": p.stem, "university": doc.get("university"), "campus_name": doc.get("campus_name"),
                    "buildings": len(doc.get("buildings") or []), "built": bool(doc.get("buildings")),
                    "targets": list((doc.get("scope") or {}).get("targets") or []),
                    "osm_relation": ((doc.get("sources") or {}).get("osm") or {}).get("relation"),
                    "has_bbox": bool(doc.get("bbox_lonlat"))})
    return out


def _select(root: Path, campus: str) -> None:
    """敷地を選ぶ。このプロセスの環境変数にも入れる（係の依頼文と、係が打つ recorder のコマンドに渡る）。
    敷地 JSON が 2 つ以上ある時は data/site/default_campus.txt にも書く（site.default_campus の決まり。CLI も同じ物を使う）。"""
    SELECTED["campus"] = campus
    os.environ[CAMPUS_ENV] = campus
    if len(_campuses(root)) >= 2:
        (root / "data" / "site" / "default_campus.txt").write_text(campus + chr(10), encoding="utf-8")


# --- API の中身（ハンドラから切り離して、テストでも呼べる形） -----------------------------------

def api_site(root: Path) -> dict:
    from . import agents

    p = root / "web" / "data" / "site.json"
    doc = _jload(p) if p.exists() else None
    campus = _default_campus()
    cs = _campuses(root)
    sel = next((c for c in cs if c["campus"] == campus), None)
    web_campus = (doc or {}).get("campus") if isinstance(doc, dict) else None
    return {"ready": isinstance(doc, dict), "path": "web/data/site.json",
            "campus": web_campus,
            "university": (doc or {}).get("university") if isinstance(doc, dict) else None,
            "targets": list(((doc or {}).get("scope") or {}).get("targets") or []) if isinstance(doc, dict) else [],
            "campus_json": campus, "campuses": cs, "selected": sel,
            # 歩く画面の書き出し（web/data/site.json）が選んだ敷地と同じか。違えば敷地の取得（site build → export-web）がまだ
            "web_matches": bool(sel) and web_campus == campus,
            "site_run": _site_public(),
            "max_usd_default": agents.DEFAULT_MAX_USD,
            "api_key": bool(os.environ.get("ANTHROPIC_API_KEY"))}


def _bbox_of(v) -> tuple[float, float, float, float] | None:
    """[南の緯度, 西の経度, 北の緯度, 東の経度]（campus init --bbox と同じ並び）。文字列ならカンマか空白区切り。"""
    if v in (None, "", []):
        return None
    if isinstance(v, str):
        v = [x for x in re.split(r"[,\s、，]+", v.strip()) if x]
    b = tuple(float(x) for x in v)
    if len(b) != 4:
        raise ValueError("範囲（bbox）は 南の緯度, 西の経度, 北の緯度, 東の経度 の 4 つの数")
    lat0, lon0, lat1, lon1 = b
    if not (-90 <= lat0 < lat1 <= 90 and -180 <= lon0 < lon1 <= 180):
        raise ValueError("範囲（bbox）の並びが違います：南 < 北、西 < 東（4 つの数を 南, 西, 北, 東 の順に）")
    if (lat1 - lat0) > 0.2 or (lon1 - lon0) > 0.2:
        raise ValueError("範囲（bbox）が広すぎます（キャンパス 1 つ分、緯度経度で 0.2° 以内）")
    return b


def geocode(address: str) -> tuple[float, float, float, float]:
    """住所・施設名 → 範囲（OSM の Nominatim。地図の出典は OSM だけ。Google は使わない）。見つからなければ ValueError。"""
    import urllib.parse
    import urllib.request

    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": address, "format": "json", "limit": 1, "accept-language": "ja"})
    req = urllib.request.Request(url, headers={"User-Agent": "campus-recorder/1 (local staff screen)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            hits = json.loads(r.read())
    except OSError as e:
        raise ValueError(f"住所から範囲を引けません（{e}）。範囲（bbox）を数で入れてください") from e
    if not hits:
        raise ValueError(f"住所が見つかりません: {address}。範囲（bbox）を数で入れてください")
    s_, n_, w_, e_ = (float(x) for x in hits[0]["boundingbox"])
    return _bbox_of([s_, w_, n_, e_])


def call_campus_init(root: Path, slug: str, university: str, campus_name: str,
                     bbox: tuple[float, float, float, float], osm_relation: int | None = None,
                     osm_way: int | None = None) -> tuple[Path, bool]:
    """site.campus_init を呼ぶのはここ 1 か所（site.py の引数が変わったらここだけ直す）。
    OSM の敷地の番号（relation か way）は敷地 JSON の sources.osm に入り、site build がそれを取る（F1、11/8）。"""
    from . import site

    return site.campus_init(slug, university, campus_name, bbox, data_site=root / "data" / "site",
                            osm_relation=osm_relation, osm_way=osm_way)


def api_campus(root: Path, body: dict) -> dict:
    univ = str(body.get("university") or "").strip()
    name = str(body.get("campus_name") or "").strip()
    if not univ or not name:
        raise ValueError("大学名とキャンパス名の両方が要ります")
    raw = str(body.get("slug") or "").strip()
    slug = slug_of(raw) if raw else slug_of(name if slug_of(name) != "campus" else univ)
    if slug == "campus":   # 日本語の名前だけだと英数字の名前が作れない（監査 A 24）
        raise ValueError("敷地の短い名前（英数字とハイフン。例 main-campus）を入れてください。日本語の名前からは作れません")
    p = root / "data" / "site" / f"{slug}.json"
    if p.exists():
        _select(root, slug)
        return {"made": False, "campus": slug, "path": p.relative_to(root).as_posix(),
                "message": "この名前の敷地 JSON は既にあります（作り直していません。この敷地を選びました）"}
    bbox = _bbox_of(body.get("bbox"))
    how = "範囲（bbox）"
    if bbox is None and str(body.get("address") or "").strip():
        bbox, how = geocode(str(body["address"]).strip()), "住所（OSM の Nominatim）"
    if bbox is None:   # 範囲が無いと原点が道具の既定（別の大学の場所）になる（監査 A 6）
        raise ValueError("キャンパスの範囲（bbox）か住所が要ります。座標の原点をその中心に置くためです")
    osm = {}
    for key, label in (("osm_relation", "relation"), ("osm_way", "way")):
        v = str(body.get(key) or "").strip()
        if v and not v.isdigit():
            raise ValueError(f"OSM の {label} は数字だけ")
        osm[key] = int(v) if v else None
    path, made = call_campus_init(root, slug, univ, name, bbox, **osm)
    _select(root, slug)
    return {"made": made, "campus": slug, "path": Path(path).relative_to(root).as_posix(), "bbox": list(bbox), "bbox_from": how,
            "next": f"敷地の取得（画面の「地図と標高を取る」、または `recorder site build --campus {slug}`）",
            "message": (f"敷地 JSON の骨組みを作りました（範囲は{how}から）。次に「地図と標高を取る」を押してください"
                        if made else "敷地 JSON は既にあります")}


def api_campus_select(root: Path, body: dict) -> dict:
    c = str(body.get("campus") or "").strip()
    if not _ID_RE.match(c) or not (root / "data" / "site" / f"{c}.json").exists():
        raise ValueError(f"data/site/ に無い敷地です: {c}")
    _select(root, c)
    log(f"敷地を選びました: {c}")
    return {"campus": c}


# --- 敷地の取得（site build → export-web）。コマンドの形はここ 1 か所 ---------------------------------

def site_build_commands(campus: str) -> list[list[str]]:
    """敷地の取得で順に打つコマンド。site.py の関数を直接呼ばず CLI を通す（引数が変わっても受付は壊れない）。"""
    py = [sys.executable, "-I", "-X", "utf8", "-m", "recorder"]
    return [py + ["site", "build", "--campus", campus],
            py + ["site", "export-web", "--campus", campus]]


def _run_cmd(cmd: list[str], root: Path, on_line) -> int:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    pr = subprocess.Popen(cmd, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                          text=True, encoding="utf-8", errors="replace")
    assert pr.stdout is not None
    for line in pr.stdout:
        on_line(line.rstrip())
    return pr.wait()


def _site_public() -> dict | None:
    with _runs_lock:
        if not SITE_RUN:
            return None
        return {k: (list(v[-12:]) if k == "log" else v) for k, v in SITE_RUN.items()}


def _site_worker(root: Path, campus: str) -> None:
    def add(line: str) -> None:
        if line:
            with _runs_lock:
                SITE_RUN["log"].append(line)
    names = ("site build", "export-web")
    try:
        for i, cmd in enumerate(site_build_commands(campus)):
            with _runs_lock:
                SITE_RUN["step"] = names[i] if i < len(names) else cmd[-1]
            code = _run_cmd(cmd, root, add)
            if code != 0:
                with _runs_lock:
                    SITE_RUN.update(state="error", error=f"{SITE_RUN['step']} が終了コード {code} で止まりました（記録の最後の行を見てください）")
                break
        else:
            with _runs_lock:
                SITE_RUN["state"] = "done"
    except Exception as e:  # noqa: BLE001  サーバは落とさない
        with _runs_lock:
            SITE_RUN.update(state="error", error=f"{type(e).__name__}: {e}")
    with _runs_lock:
        SITE_RUN["finished_at"] = _now()
    log(f"敷地の取得 {campus}: {SITE_RUN['state']}" + (f"（{SITE_RUN.get('error')}）" if SITE_RUN.get("error") else ""))


def api_site_build(root: Path, body: dict) -> dict:
    campus = str(body.get("campus") or "").strip() or _default_campus()
    if not campus or not _ID_RE.match(campus) or not (root / "data" / "site" / f"{campus}.json").exists():
        raise ValueError("先に敷地 JSON を作ってください（大学名・キャンパス名・範囲を入れて「敷地 JSON を作る」）")
    with _runs_lock:
        if SITE_RUN.get("state") == "running":
            raise Busy(["site"])
        SITE_RUN.clear()
        SITE_RUN.update(campus=campus, state="running", step=None, started_at=_now(), finished_at=None, error=None, log=[])
    _select(root, campus)
    threading.Thread(target=_site_worker, args=(root, campus), name="site-build", daemon=True).start()
    log(f"敷地の取得を始めました: {campus}")
    return {"started": True, "campus": campus, "commands": [" ".join(c[5:]) for c in site_build_commands(campus)]}


def _safe_stem(name: str) -> tuple[str, str]:
    base = Path(str(name).replace("\\", "/")).name
    stem, ext = os.path.splitext(base)
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem).strip("._")
    return (stem or "photo"), ext.lower()


def _folder_of(building: str, aliases: dict[str, str]) -> str | None:
    b = (building or "").strip()
    if b in aliases:
        return aliases[b]
    if b in set(aliases.values()):
        return b
    return b if (_ID_RE.match(b) and not aliases) else None


def _place(data: bytes, dest: Path, stem: str, ext: str, taken_ids: set[str]) -> tuple[Path, bool]:
    """dest に stem+ext で置く。同じ中身が既にあればそれを使う（二度押し）。同名で中身が違うか、索引に同じ id があれば連番。"""
    digest = hashlib.sha256(data).hexdigest()
    i = 1
    while True:
        s = stem if i == 1 else f"{stem}_{i}"
        f = dest / f"{s}{ext}"
        if f.exists():
            if hashlib.sha256(f.read_bytes()).hexdigest() == digest:
                return f, True
        elif s not in taken_ids:
            f.write_bytes(data)
            return f, False
        i += 1


def api_photos(root: Path, files: list[tuple[str, bytes]], assignments: list[dict], day: str | None = None) -> dict:
    """写真を data/photos/<日付>/<棟>/ に置き → import_photos（期間は EXIF の撮影日時から）→ assign_photos。"""
    from . import photos, site

    day = day or time.strftime("%Y%m%d")
    campus = _default_campus()
    aliases = photos.campus_aliases(campus) if campus else {}
    by_file: dict[str, dict] = {}
    for a in assignments:
        if isinstance(a, dict) and a.get("file"):
            by_file[Path(str(a["file"]).replace("\\", "/")).name] = a
    photo_root = root / photos.PHOTO_DIR
    index_path = root / photos.INDEX_PATH
    warnings: list[dict] = []
    placed: list[tuple[str, Path, dict]] = []   # (棟フォルダ, 置いた道, 振り分け)
    from .ledger import lock as _ledger_lock   # 係のコマンド（別のプロセス）と索引の書き込みがぶつからないように（ledger.py）
    # 台帳の鍵（ledger）は索引を書く所だけで持つ。import_photos は中で自分で鍵を取る（11/8、F3。重い縮小・顔の検出は鍵の外）ので、
    # その外側では持たない（持つと取り込みの間じゅう係のコマンドを待たせる）。_photos_lock はこのサーバの中の順番だけ。
    with _photos_lock:
        idx = photos.load_index(index_path)   # 読むだけ（同じ名前の写真に連番を付けるため）
        taken = {str(p.get("id")) for p in idx.get("photos", [])}
        saved = reused = 0
        for name, data in files:
            stem, ext = _safe_stem(name)
            if ext not in _IMG_EXTS:
                warnings.append({"file": name, "warning": "写真の拡張子ではないので置いていません"})
                continue
            a = by_file.get(Path(str(name).replace("\\", "/")).name)
            if not a or not str(a.get("building") or "").strip():
                warnings.append({"file": name, "warning": "振り分け（棟）が無いので置いていません"})
                continue
            if a.get("interior") and a.get("floor") is None:
                warnings.append({"file": name, "warning": "内装なのに階が無いので置いていません"})
                continue
            folder = _folder_of(str(a["building"]), aliases)
            if folder is None:
                folder = UNSORTED
                warnings.append({"file": name, "building": a["building"],
                                 "warning": f"棟の名前を棟 id に直せないので {UNSORTED}/ に置きました（仕分け係が決めます）"})
            dest = photo_root / day / folder
            dest.mkdir(parents=True, exist_ok=True)
            f, same = _place(data, dest, stem, ext, taken)
            taken.add(f.stem)
            reused += same
            saved += not same
            placed.append((folder, f, a))
        sdoc = None
        if campus:
            sdoc = _jload(root / site.DATA_SITE / f"{campus}.json")
        added_total = 0
        for folder in sorted({p[0] for p in placed}):
            _idx, added = photos.import_photos(photo_root / day / folder, sdoc, index_path=index_path,
                                               root=photo_root, period=None)  # 期間は撮影日時（EXIF）から決める。期間前の写真を during にしない
            added_total += len(added)
            # 撮影日時（EXIF）が無い写真だけ、受け取った日＝期間中とする（理由を残す）。日時がある物は EXIF で決まる
            fix = [r for r in added if not r.get("period")]
            if fix:
                ids = {r["id"] for r in fix}
                with _ledger_lock("ledger", label="serve 撮影日時の無い写真"):
                    _idx = photos.load_index(index_path)   # 鍵を取ってから読み直す（取り込みの後に係が書いた分を消さない）
                    for r in _idx.get("photos", []):
                        if r["id"] in ids and not r.get("period"):
                            r["period"] = "during"
                            r["period_note"] = "EXIF に撮影日時が無く、職員の画面から受け取った日（期間中）とした"
                    photos.save_index(_idx, index_path)
            _last_folder[folder] = (photos.PHOTO_DIR / day / folder).as_posix()
        rows = [{"id": f.stem, "building": a.get("building"), "interior": bool(a.get("interior")),
                 "floor": a.get("floor") if a.get("interior") else None}
                for folder, f, a in placed if folder != UNSORTED]
        if rows:
            with _ledger_lock("ledger", label="serve 写真の振り分け"):   # assign_photos の鍵は呼ぶ側が持つ
                res = photos.assign_photos(index_path, rows, campus=campus)
        else:
            res = {"assigned": 0, "interior": 0, "unchanged": 0, "warnings": [], "errors": [], "not_found": []}
    by_b: dict[str, int] = {}
    for folder, _f, _a in placed:
        by_b[folder] = by_b.get(folder, 0) + 1
    return {"received": len(files), "saved": saved, "reused": reused, "imported": added_total,
            "folder": (photos.PHOTO_DIR / day).as_posix(), "by_building": by_b,
            "buildings": sorted(b for b in by_b if b != UNSORTED),
            "ids": [f.stem for folder, f, _a in placed if folder != UNSORTED],
            "assign": res, "warnings": warnings + [{"gps": w} for w in res.get("warnings", [])]}


def api_tape(root: Path, files: list[tuple[str, bytes]], fields: dict[str, str], day: str | None = None) -> dict:
    """メジャーの枠（K-102）：職員が棟（と分かれば面）とメモを入れて送ったメジャーの写真を置き、取り込み、
    職員の申告（assigned_by staff）として棟・ruler: tape_measure・ruler_note を付ける。仕分け係は棟を変えず面と ruler_note だけ足す。"""
    from . import photos

    building = str(fields.get("building") or "").strip()
    face = str(fields.get("face") or "").strip() or None
    note = str(fields.get("note") or "").strip()[:500] or None
    if not building:
        raise ValueError("棟の名前が要ります")
    if not files:
        raise ValueError("写真がありません")
    campus = _default_campus()
    aliases = photos.campus_aliases(campus) if campus else {}
    bid = _folder_of(building, aliases)
    if bid is None:
        raise ValueError(f"棟の名前を棟 id に直せません: {building}（一覧の名前を選んでください）")
    if face is not None and not _ID_RE.match(face):
        raise ValueError(f"面の id の書き方が違います: {face}（例 f6）")
    rows = [{"file": name, "building": bid, "interior": False, "floor": None} for name, _d in files]
    res = api_photos(root, files, rows, day=day)
    from .ledger import lock as _ledger_lock
    with _photos_lock, _ledger_lock("ledger", label="serve メジャーの枠"):
        mk = photos.mark_tape(root / photos.INDEX_PATH, res["ids"], bid, face=face, note=note, aliases=aliases)
    log(f"メジャーの写真 {len(mk['marked'])} 枚：{bid}{'・' + face if face else ''}")
    return {**res, "tape": {"building": bid, "face": face, "note": note, "marked": mk["marked"], "not_found": mk["not_found"]}}


class Busy(Exception):
    pass


def _record(root: Path, bid: str, st: dict) -> None:
    try:
        p = root / st["record"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        log(f"記録を書けません {bid}: {e}")


def _worker(root: Path, bid: str) -> None:
    from . import agents

    st = RUNS[bid]
    try:
        if st.get("mode") == "resume":     # 止まった所から続ける（11/8、F15。CLI の recorder agent resume と同じ関数）
            kw = {"shinko": True} if st.get("shinko") else {}   # 進行役に決めさせる時だけ渡す（F19）
            res = agents.resume_pipeline(bid, dry_run=False, cwd=root, max_usd=st.get("resume_max_usd"),
                                         redo_from=st.get("redo_from"), decision_file=st.get("decision_file"), **kw)
        elif st.get("mode") == "continue":   # 棟 JSON がある棟は追記（11/8、K-106。T3 の continue_pipeline）
            res = agents.continue_pipeline(bid, st.get("photos_folder"), dry_run=st["dry_run"], cwd=root, max_usd=st.get("max_usd"))
        else:
            res = agents.run_pipeline(bid, st.get("photos_folder"), dry_run=st["dry_run"], cwd=root, max_usd=st.get("max_usd"))
        steps = [s.get("step") for s in res.get("steps", []) if isinstance(s, dict)]
        if res.get("nothing"):
            st["note"] = res["nothing"]
        with _runs_lock:
            st["last_step"] = steps[-1] if steps else None
            st["steps"] = steps
            if res.get("stopped_at"):
                st["state"], st["stopped_at"] = "stopped", res["stopped_at"]
                st["events"] = [{"kind": e.get("kind"), "why": e.get("why")} for e in (res.get("events") or {}).get("events", [])]
            elif res.get("stopped_early"):
                st["state"], st["stopped_at"] = "stopped", st["last_step"]
            else:
                st["state"] = "done"
            st["pipeline_record"] = res.get("record")
            if isinstance(res.get("stopped"), dict) and st["state"] != "stopped":
                st["state"], st["stopped_at"] = "stopped", res["stopped"].get("step")
            for k in ("stopped", "spent_usd", "pending_events"):
                if k in res:
                    st[k] = res[k]
    except (Exception, SystemExit) as e:  # noqa: BLE001  1 棟の失敗でサーバは落とさない（SystemExit も拾い、running のまま残さない）
        with _runs_lock:
            st["state"], st["error"] = "error", f"{type(e).__name__}: {e}"
        log(f"pipeline {bid} エラー: {st['error']}")
    st["finished_at"] = _now()
    log(f"pipeline {bid} {st['state']}" + (f"（{st.get('stopped_at')} で止まる）" if st.get("stopped_at") else ""))
    _record(root, bid, st)


def api_start(root: Path, body: dict) -> dict:
    blds = body.get("buildings") or []
    if isinstance(blds, str):
        blds = [blds]
    blds = list(dict.fromkeys(str(b).strip() for b in blds if str(b).strip()))
    if not blds:
        raise ValueError("buildings（棟 id の並び）が空です")
    bad = [b for b in blds if not _ID_RE.match(b) or b == UNSORTED]
    if bad:
        raise ValueError(f"棟 id ではない名前: {bad}")
    from . import agents

    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    dry = bool(body.get("dry_run")) or not has_key
    max_usd = body.get("max_usd")
    if max_usd in (None, ""):
        max_usd = agents.DEFAULT_MAX_USD
    max_usd = float(max_usd)
    if max_usd < 0:
        raise ValueError("max_usd は 0 以上の数（USD、棟ごと。0 で上限なし）")
    max_usd = max_usd or None
    with _runs_lock:
        busy = [b for b in blds if (RUNS.get(b) or {}).get("state") == "running"]
        if busy:
            raise Busy(busy)
        stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
        started = []
        for b in blds:
            mode = "continue" if (root / "data" / "buildings" / f"{b}.json").exists() else "new"
            st = {"building": b, "state": "running", "started_at": _now(), "finished_at": None, "dry_run": dry,
                  "mode": mode, "max_usd": max_usd,
                  "photos_folder": _last_folder.get(b), "last_step": None, "stopped_at": None, "error": None,
                  "record": f"out/{b}/serve_{stamp}.json"}
            RUNS[b] = st
            started.append(b)
    for b in started:
        _record(root, b, RUNS[b])
        threading.Thread(target=_worker, args=(root, b), name=f"pipeline-{b}", daemon=True).start()
        log(f"pipeline {b} 起動（{'追記' if RUNS[b]['mode'] == 'continue' else '最初から'}）" + ("（dry-run）" if dry else ""))
    out = {"started": started, "dry_run": dry, "records": [RUNS[b]["record"] for b in started],
           "modes": {b: RUNS[b]["mode"] for b in started}, "max_usd": max_usd}
    if not has_key:
        out["note"] = ("この PC に API キー（ANTHROPIC_API_KEY）が設定されていないので、係（Claude）を呼ばずに"
                       "「手順だけ確かめる試し」で動かしました。写真の置き場と振り分けは済み、棟の記録（寸法・模型）は増えていません")
    return out


# --- 止まった所から続ける（11/8、F15）。画面から本番を起動するので、止まった時も画面から続けられるように --------

class Refused(Exception):
    """続けられない（理由の文を職員に返す。409）。args = (code, 理由の文)"""


def resume_check(root: Path, b: str, run: dict | None) -> dict | None:
    """棟の pipeline_state.json を見て、画面から続けられるかを返す。状態ファイルが無ければ None。
    {"ok", "code", "why", "stopped": bool, "reason", "label", "step", "need_max_usd", "spent_usd", "max_usd", "api_key"}
    code：running（このサーバで動いている）・done・not_stopped・no_key・need_max_usd・ok。
    run はこのサーバの RUNS の物（呼ぶ側が _runs_lock の中で渡す）。"""
    from . import agents

    state = agents.load_state(b, root)
    if not isinstance(state, dict):
        return None
    status = state.get("status")
    stop = state.get("stopped") if isinstance(state.get("stopped"), dict) else {}
    run_state = (run or {}).get("state")
    spent = float(state.get("spent_usd") or 0.0)
    cap = state.get("max_usd")
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    # 「実行中」のまま残った状態は、このサーバの係のスレッドが落ちた（error）時だけ落ちたと見て続ける。
    # それ以外（別の窓の CLI で動いているかもしれない）は画面からは続けない
    crashed = status == "running" and run_state in ("error", "stopped")
    # 別の窓の係かもしれないが、このサーバの RUNS に無く 10 分以上更新が無ければ落ちたと見る（11/8、F19。code "stale"）
    stale = status == "running" and run is None and _older_than(state.get("updated_at"), STALE_SECONDS)
    stopped = status == "stopped" or crashed or stale
    reason = stop.get("reason") if status == "stopped" else ("crash" if crashed else ("stale" if stale else None))
    need = stopped and (reason == "max_usd" or (cap is not None and spent >= float(cap)))
    out = {"stopped": stopped, "reason": reason, "label": stop.get("label") if status == "stopped" else None,
           "step": stop.get("step") if status == "stopped" else state.get("current"), "why_stop": stop.get("why"),
           "need_max_usd": bool(need), "spent_usd": round(spent, 4), "max_usd": cap, "api_key": has_key,
           "events": (stop.get("events") or []) if status == "stopped" else [],
           # 進行役を呼ぶ費用が上限の残りに足りずに止まった（reason は max_usd。上限を上げて続けると進行役が決める。F19）
           "shinko_pending": bool(status == "stopped" and stop.get("shinko_pending")),
           "shinko_cost_usd": (agents.LIMITS.get("fable") or {}).get("max_budget_usd"),   # 進行役 1 回の費用の上限（画面の勧める額）
           "human_events": sum(1 for e in (state.get("pending_events") or []) if isinstance(e, dict) and e.get("kind") == "human"),
           "updated_at": state.get("updated_at")}
    if run_state == "running":
        code, why = "running", "係が作業中です。終わるか止まるまで待ってください"
    elif status == "done":
        code, why = "done", "前の実行は最後まで済んでいます（続ける所がありません）"
    elif not stopped:
        code, why = "not_stopped", ("記録は「実行中」のままです。別の窓（recorder agent pipeline など）で動いているかもしれないので、"
                                    "画面からは続けません。落ちたと分かっているなら recorder agent resume で続けてください"
                                    if status == "running" else f"止まっていません（状態 {status}）")
    elif not has_key:
        code, why = "no_key", "この PC に API キー（ANTHROPIC_API_KEY）が無いので、係（Claude）を呼んで続けることができません"
    elif need:
        code, why = "need_max_usd", f"費用の上限で止まっています。使った額 {spent:.2f} USD より大きい上限（USD）を入れて続けてください"
    elif stale:
        code, why = "stale", (f"記録は「実行中」のままですが、10 分以上更新が無いので落ちたと見ます（最後の更新 {state.get('updated_at')}）。"
                              "止まった所から続けられます")
    else:
        code, why = "ok", "止まった所から続けられます"
    out.update(ok=code in ("ok", "need_max_usd", "stale"), code=code, why=why)
    return out


def _older_than(at, seconds: float) -> bool:
    """state の時刻（ISO、agents._now の形）が seconds より古いか。読めなければ False（落ちたとは見ない）。"""
    if not at:
        return False
    try:
        t = datetime.fromisoformat(str(at))
    except ValueError:
        return False
    now = datetime.now(t.tzinfo) if t.tzinfo else datetime.now()
    return (now - t).total_seconds() > seconds


def api_resume(root: Path, body: dict) -> dict:
    """POST /api/resume {"building", "max_usd"?, "redo_from"?, "decision_file"?}：止まった棟を agents.resume_pipeline で続ける（別スレッド）。
    redo_from は済んだ工程からのやり直し（最後まで済んだ記録も可）、decision_file は進行役の決定（data/reports/ の下の md か txt）。
    断る時：このサーバで動いている（Busy＝409）・キーが無い・状態が無い・止まっていない・費用の上限で止まったのに
    使った額より大きい上限が無い（Refused＝409、理由の文）。"""
    from . import agents

    b = str(body.get("building") or "").strip()
    if not _ID_RE.match(b) or b == UNSORTED:
        raise ValueError(f"棟 id ではない名前: {b!r}")
    mu = body.get("max_usd")
    max_usd = None if mu in (None, "") else float(mu)
    redo_from, decision_file = _resume_redo_args(root, b, body)
    shinko = body.get("shinko") is True     # 進行役に決めさせて続ける（F19）
    with _runs_lock:
        prev = RUNS.get(b)
        if (prev or {}).get("state") == "running":
            raise Busy([b])
        chk = resume_check(root, b, prev)
        if chk is None:
            raise Refused("no_state", f"続ける記録がありません（out/{b}/pipeline_state.json が無い）。先に「記録を始める」で起動してください")
        if chk["code"] == "done" and redo_from and chk["api_key"]:     # 最後まで済んだ記録も、工程を指せばやり直せる
            pass
        elif chk["code"] == "need_max_usd":
            if max_usd is None or max_usd <= chk["spent_usd"]:
                raise Refused("need_max_usd", chk["why"])
        elif not chk["ok"]:
            raise Refused(chk["code"], chk["why"])
        if max_usd is not None and max_usd <= chk["spent_usd"]:
            raise Refused("need_max_usd", f"上限 {max_usd} USD は使った額 {chk['spent_usd']:.2f} USD 以下です。もっと大きい数を入れてください")
        fee = agents.LIMITS["fable"]["max_budget_usd"]
        cap_now = max_usd if max_usd is not None else chk["max_usd"]
        if (shinko or chk.get("shinko_pending")) and cap_now is not None and float(cap_now) < chk["spent_usd"] + fee:
            raise Refused("need_max_usd", f"進行役を呼ぶには、使った額 {chk['spent_usd']:.2f} USD に進行役の費用 {fee:g} USD 分を足した "
                                          f"{chk['spent_usd'] + fee:.2f} USD より大きい上限を入れてください（今の上限 {cap_now} USD）")
        stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
        st = {"building": b, "state": "running", "started_at": _now(), "finished_at": None, "dry_run": False,
              "mode": "resume", "max_usd": max_usd if max_usd is not None else chk["max_usd"], "resume_max_usd": max_usd,
              "resumed_from": {"step": chk["step"], "reason": chk["reason"]},
              "redo_from": redo_from, "decision_file": decision_file, "shinko": shinko,
              "photos_folder": None, "last_step": None, "stopped_at": None, "error": None,
              "record": f"out/{b}/serve_{stamp}.json"}
        RUNS[b] = st
    _record(root, b, st)
    threading.Thread(target=_worker, args=(root, b), name=f"pipeline-{b}", daemon=True).start()
    log(f"pipeline {b} 続きから（{chk['step']} の {chk['reason']} から" + (f"、上限 {max_usd} USD" if max_usd is not None else "")
        + (f"、{redo_from} からやり直し" if redo_from else "") + (f"、決定 {decision_file}" if decision_file else "")
        + ("、進行役に決めさせる" if shinko else "") + "）")
    return {"started": b, "resumed_from": st["resumed_from"], "max_usd": st["max_usd"], "record": st["record"],
            "redo_from": redo_from, "decision_file": decision_file, "shinko": shinko, "code": chk["code"]}


DECISION_DIR = "data/reports/"      # 画面から渡せる進行役の決定のファイルの置き場（リポジトリの中のここの下だけ）
STALE_SECONDS = 600                 # 「実行中」のまま、この秒より更新が無く、このサーバでも動かしていなければ落ちたと見る（F19）


def _resume_redo_args(root: Path, b: str, body: dict) -> tuple[str | None, str | None]:
    """POST /api/resume の任意の redo_from・decision_file を確かめる（ValueError＝400）。
    redo_from は棟の state の steps にある工程。decision_file は data/reports/ の下の md か txt（リポジトリからの相対）。"""
    from . import agents

    redo_from = str(body.get("redo_from") or "").strip() or None
    decision_file = str(body.get("decision_file") or "").strip().replace("\\", "/") or None
    if redo_from is not None:
        state = agents.load_state(b, root)
        steps = (state or {}).get("steps") or []
        if redo_from not in steps:
            raise ValueError(f"やり直す工程 {redo_from!r} はこの棟の記録の工程にありません（ある工程: {steps}）")
    if decision_file is not None:
        try:
            rel, _text = agents.read_decision(decision_file, root)
        except agents.AgentError as e:
            raise ValueError(str(e)) from None
        if not rel.startswith(DECISION_DIR):
            raise ValueError(f"決定のファイルは {DECISION_DIR} の下の物だけ受けます：{decision_file}")
        decision_file = rel
    return redo_from, decision_file


# --- 進行役の自動の決定を職員が見て承認／取り消す（11/8、F19） ----------------------------------------------

def _decision_summary(text) -> str | None:
    """決定の文の最初の見出し（無ければ最初の行）を 1 行で。"""
    for line in str(text or "").splitlines():
        t = line.strip().lstrip("#").strip()
        if t:
            return _one_line(t)
    return None


def state_review(state: dict | None) -> dict:
    """pipeline_state.json から、職員の画面と待機画面が見る物の抜粋：
    shinko（進行役の自動の決定）・halt・halted・pending_events・stopped（events を含む）・decisions（文の中身は 1 行に）。
    無い物は空（list は []、dict は None）。"""
    s = state if isinstance(state, dict) else {}

    def lst(v):
        return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []

    def dic(v):
        return v if isinstance(v, dict) else None

    decisions = [{"at": d.get("at"), "file": d.get("file"), "redo_from": d.get("redo_from"), "chars": d.get("chars"),
                  "summary": _decision_summary(d.get("text")), "withdrawn": dic(d.get("withdrawn"))}
                 for d in lst(s.get("decisions"))]
    halted = s.get("halted")
    return {"shinko": lst(s.get("shinko")), "halt": dic(s.get("halt")),
            "halted": lst(halted) if isinstance(halted, list) else dic(halted),
            "pending_events": lst(s.get("pending_events")), "stopped": dic(s.get("stopped")), "decisions": decisions}


# --- 再開した記録を待機画面と職員の画面で同じに見せる（11/8、F20） --------------------------------------------
# 1 つの記録（pipeline_state.json）には、最初の起動（runs の start）・再開（resume）・記録を戻した（rewind）・
# 進行役（shinko）が並び、results には同じ工程が周ごとに何度も入る。画面はここで作った物だけを見る（画面で推し量らない）

RUN_START_KINDS = ("start", "resume")


def _rcost(r: dict) -> float:
    c = r.get("cost_usd")
    if c is None:
        c = r.get("cost_usd_estimate")
    try:
        return float(c or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _split_note(r: dict) -> tuple[str | None, str | None]:
    """結果の 1 行の要約と警告。「警告：」で始まる note（単価が無い等）は要約にせず警告に分ける。"""
    note = str(r.get("note") or "").strip()
    warn = None
    if note.startswith("警告"):
        warn, note = _one_line(note), ""
    return _one_line(r.get("summary") or note or r.get("stop") or r.get("error")), warn


def state_timeline(state: dict | None) -> dict:
    """results と runs を時刻順に 1 本に並べ、周（round：start か resume 1 回分）を振る。
    {"rounds": [{n, kind, at, ended_at, from_step, reason, redo_from, rewound, cost_usd, steps}],
     "items": [{"type": "result"|"run", "round", "at", …}], "latest": {工程: その工程の最新の結果の周},
     "history": {resumes, rewinds: [{at, to}], shinko, shinko_waiting, auto_resumes, this_round_at, this_round_cost_usd}}"""
    s = state if isinstance(state, dict) else {}
    runs = [r for r in (s.get("runs") or []) if isinstance(r, dict)]
    results = [r for r in (s.get("results") or []) if isinstance(r, dict)]
    rounds = []
    for r in runs:
        if r.get("kind") in RUN_START_KINDS:
            rounds.append({"n": len(rounds) + 1, "kind": r.get("kind"), "at": r.get("at"), "ended_at": r.get("ended_at"),
                           "from_step": r.get("from_step"), "reason": r.get("reason"), "why": _one_line(r.get("why")),
                           "redo_from": r.get("redo_from"), "decision_file": r.get("decision_file"),
                           "rewound": False, "cost_usd": 0.0, "steps": []})
    starts = [str(x.get("at") or "") for x in rounds]

    def round_of(at) -> int | None:
        a = str(at or "")
        n = None
        for i, t in enumerate(starts):
            if t and a >= t:
                n = i + 1
        return n

    items = []
    latest: dict[str, int | None] = {}
    for r in results:
        step = r.get("step") or r.get("agent")
        n = round_of(r.get("started_at") or r.get("ended_at"))
        summary, warn = _split_note(r)
        cost = _rcost(r)
        items.append({"type": "result", "round": n, "at": r.get("started_at"), "ended_at": r.get("ended_at") or r.get("logged_at"),
                      "step": step, "agent": r.get("agent"), "model": r.get("model"), "seconds": r.get("seconds"),
                      "cost_usd": round(cost, 6), "summary": summary, "warn": warn,
                      "error": bool(r.get("error") or r.get("is_error")), "shinko_n": r.get("shinko_n")})
        latest[step] = n
        if n is not None:
            rounds[n - 1]["cost_usd"] = round(rounds[n - 1]["cost_usd"] + cost, 6)
            if step not in rounds[n - 1]["steps"]:
                rounds[n - 1]["steps"].append(step)
    rewinds = []
    for r in runs:
        if r.get("kind") in RUN_START_KINDS:
            continue
        n = round_of(r.get("at"))
        item = {"type": "run", "kind": r.get("kind"), "round": n, "at": r.get("at"), "why": _one_line(r.get("why")),
                "to": r.get("to"), "n": r.get("n"), "redo_from": r.get("redo_from"), "decision_file": r.get("decision_file"),
                "failed": r.get("failed")}
        items.append(item)
        if r.get("kind") == "rewind":
            rewinds.append({"at": r.get("at"), "to": r.get("to"), "why": item["why"]})
            if n is not None:          # 戻した時に動いていた周は取り下げ（その周の結果は記録に残る）
                rounds[n - 1]["rewound"] = True
    for x in rounds:
        items.append({"type": "run", "kind": x["kind"], "round": x["n"], "at": x["at"], "from_step": x["from_step"],
                      "reason": x["reason"], "why": x["why"], "redo_from": x["redo_from"], "decision_file": x["decision_file"]})
    # 同じ時刻なら周の始まり → 結果 → その他の印 の順
    rank = {"start": 0, "resume": 0}
    items.sort(key=lambda i: (str(i.get("at") or ""), rank.get(i.get("kind"), 1) if i["type"] == "run" else 1))
    sh = [x for x in (s.get("shinko") or []) if isinstance(x, dict)]
    ar = s.get("auto_resumes") or {}
    n_ar = sum(len(v) for v in ar.values() if isinstance(v, list)) if isinstance(ar, dict) else 0
    # 周の終わり：runs の ended_at、無ければその周の結果の最後の終わり、それも無ければ始まり（取り下げた周など）。
    # 今動いている最後の周（状態が running）は None（画面が今の時刻で数える）
    running = s.get("status") == "running"
    for x in rounds:
        if not x["ended_at"] and not (running and x is rounds[-1]):
            ends = [i.get("ended_at") for i in items if i["type"] == "result" and i["round"] == x["n"] and i.get("ended_at")]
            x["ended_at"] = max(ends) if ends else x["at"]
    work = 0.0      # 係が動いていた時間の合計（止まっていた間は入れない）
    for x in rounds:
        try:
            if x["ended_at"]:
                work += max(0.0, (datetime.fromisoformat(str(x["ended_at"])) - datetime.fromisoformat(str(x["at"]))).total_seconds())
        except (TypeError, ValueError):
            pass
    live_rounds = [x for x in rounds if not x["rewound"]]
    last = (live_rounds or rounds or [None])[-1]     # 「この再開」：取り下げた（記録を戻した）周は数えない
    hist = {"rounds": len(rounds), "resumes": sum(1 for x in rounds if x["kind"] == "resume"), "rewinds": rewinds,
            "shinko": len(sh), "shinko_waiting": sum(1 for x in sh if not x.get("review")),
            "shinko_last": (sh[-1].get("summary") if sh else None), "auto_resumes": n_ar,
            "this_round_at": last["at"] if last else s.get("started_at"), "this_round_kind": last["kind"] if last else None,
            "this_round_cost_usd": last["cost_usd"] if last else None, "this_round_n": last["n"] if last else None,
            "this_round_ended_at": last["ended_at"] if last else None,
            "work_seconds_ended": round(work, 1), "running": running, "started_at": s.get("started_at")}
    return {"rounds": rounds, "items": items, "latest": latest, "history": hist}


def _sig_event(x) -> dict | None:
    """事件の印（"kind|why" の文字列か {kind, why} の dict）→ {kind, why}。"""
    if isinstance(x, dict):
        return {"kind": x.get("kind"), "why": x.get("why")}
    if isinstance(x, str):
        k, _, w = x.partition("|")
        return {"kind": k, "why": w} if w else {"kind": None, "why": k}
    return None


def incident(root: Path, b: str, state: dict | None) -> dict | None:
    """画面に出す「事件」の源を 1 つに（11/8、F20）。
    - 進行役が考えている（current shinko）：今回進行役に出した事件。state の shinko_pending_events があればそれ、
      無ければ out/<棟>/events.json の最新のうち止める事件（stuck・vocab・asset）で、決定済み（acknowledged_events）を除いた物
    - 止まっている：止まった時の事件（stopped.events）。続ける時に取り直す
    - それ以外で進行役の決定がある：最新の決定 shinko[n].events（進行役が受け取った事件）
    {"source": "pending"|"events_json"|"stopped"|"shinko", "n"?, "at"?, "events": [{kind, why}]}。無ければ None。"""
    from . import agents

    s = state if isinstance(state, dict) else None
    if not s:
        return None
    ack = set(s.get("acknowledged_events") or [])
    if s.get("status") == "running" and s.get("current") == "shinko":
        pend = s.get("shinko_pending_events")
        if isinstance(pend, list) and pend:
            return {"source": "pending", "events": [e for e in map(_sig_event, pend) if e]}
        doc = _jload(root / "out" / b / "events.json")
        doc = doc if isinstance(doc, dict) else {}
        evs = [e for e in (doc.get("events") or []) if isinstance(e, dict)
               and e.get("kind") in agents.STOP_EVENTS and str(e.get("kind")) + "|" + str(e.get("why")) not in ack]
        return {"source": "events_json", "at": doc.get("made_at"),
                "events": [{"kind": e.get("kind"), "why": e.get("why")} for e in evs]}
    stop = s.get("stopped") if isinstance(s.get("stopped"), dict) else None
    if s.get("status") == "stopped" and stop and stop.get("events"):
        return {"source": "stopped", "at": stop.get("at"), "events": [e for e in map(_sig_event, stop["events"]) if e]}
    sh = [x for x in (s.get("shinko") or []) if isinstance(x, dict)]
    if sh:
        return {"source": "shinko", "n": sh[-1].get("n"), "at": sh[-1].get("at"),
                "events": [e for e in map(_sig_event, sh[-1].get("events") or []) if e]}
    return None


LIVE_LABEL = {"running": "係が作業中", "shinko": "進行役が決めています", "starting": "係を起動しています",
              "stopped": "止まりました", "stale": "止まっています（10 分以上更新が無いので落ちたと見ます）",
              "done": "係の工程が終わりました", "error": "エラー", "idle": "記録はまだ動いていません"}


def live_phase(state: dict | None, run: dict | None, rs: dict | None) -> dict:
    """職員の画面と待機画面が同じ言葉で言うための「今どうなっているか」。
    phase：running（係が作業中）・shinko（進行役が考えている）・starting（このサーバで起動した直後、状態ファイルがまだ古い）・
    stopped・stale（落ちたと見る）・done・error・idle。where：this（このサーバの係）・other（別の窓かもしれない）・None。"""
    st = state if isinstance(state, dict) else None
    rstate = (run or {}).get("state")
    status = (st or {}).get("status")
    cur = (st or {}).get("current")
    where = "this" if rstate == "running" else None
    stop = (st or {}).get("stopped") if isinstance((st or {}).get("stopped"), dict) else None
    if rstate == "running":
        if status == "running" and cur:
            phase = "shinko" if cur == "shinko" else "running"
        else:
            phase = "starting"
    elif rs and rs.get("code") == "stale":
        phase = "stale"
    elif status == "running":
        if rs and rs.get("reason") == "crash":
            phase = "stopped"
        else:
            phase, where = ("shinko" if cur == "shinko" else "running"), "other"
    elif status == "stopped":
        phase = "stopped"
    elif status == "done":
        phase = "done"
    elif st is None and rstate in ("done", "stopped", "error"):
        phase = rstate
    else:
        phase = "idle"
    out = {"phase": phase, "label": LIVE_LABEL[phase], "current": cur if phase in ("running", "shinko") else None,
           "where": where, "updated_at": (st or {}).get("updated_at")}
    if phase == "stopped":
        s = stop or (run or {}).get("stopped") or {}
        out.update(step=s.get("step"), reason=s.get("reason") or ("crash" if rs and rs.get("reason") == "crash" else None),
                   why_label=s.get("label"))
    if phase == "starting":
        out["since"] = (run or {}).get("started_at")
    return out


def _building_known(root: Path, b: str) -> bool:
    from . import agents
    return agents.load_state(b, root) is not None or (root / "data" / "buildings" / f"{b}.json").exists()


def api_review(root: Path, body: dict) -> dict:
    """POST /api/review {"building", "n", "verdict": "approve"|"revoke", "note"?}：agents.review_decision を呼ぶ。
    400：棟が無い（棟 JSON も状態も無い）・n が整数でない・verdict が 2 つ以外・agents が断った（AgentError）。
    係が動いている最中の取り消しも受ける（halt は次の工程の前で効く）。返事：書いた項目（item）と halt。"""
    from . import agents

    b = str(body.get("building") or "").strip()
    if not _ID_RE.match(b) or b == UNSORTED:
        raise ValueError(f"棟 id ではない名前: {b!r}")
    if not _building_known(root, b):
        raise ValueError(f"棟がありません（data/buildings/{b}.json も out/{b}/pipeline_state.json も無い）")
    n = body.get("n")
    if isinstance(n, str) and n.strip().isdigit():
        n = int(n.strip())
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError(f"n は 1 以上の整数（進行役の決定の番号）: {body.get('n')!r}")
    verdict = str(body.get("verdict") or "").strip()
    if verdict not in ("approve", "revoke"):
        raise ValueError(f"verdict は approve か revoke: {verdict!r}")
    note = body.get("note")
    note = (str(note).strip() or None) if note is not None else None
    try:
        res = agents.review_decision(b, n, verdict, note, cwd=root)
    except agents.AgentError as e:
        raise ValueError(str(e)) from None
    state = res if isinstance(res, dict) and "shinko" in res else agents.load_state(b, root)
    items = (state or {}).get("shinko") if isinstance((state or {}).get("shinko"), list) else []
    item = next((x for x in items if isinstance(x, dict) and x.get("n") == n), None)
    if item is None and isinstance(res, dict) and "shinko" not in res:
        item = res
    with _runs_lock:
        running = (RUNS.get(b) or {}).get("state") == "running"
    log(f"進行役の決定 {b} #{n} を職員が{'承認' if verdict == 'approve' else '取り消し'}" + (f"（{note}）" if note else "")
        + ("（係は作業中。次の工程の前で止まります）" if verdict == "revoke" and running else ""))
    return {"building": b, "n": n, "verdict": verdict, "item": item, "halt": (state or {}).get("halt"), "running": running}


def api_decision(root: Path, file: str) -> dict:
    """GET /api/decision?file=：進行役の決定のファイルの中身（data/reports/ の下の md・txt だけ。外は 400）。"""
    from . import agents

    f = str(file or "").strip().replace("\\", "/")
    if not f:
        raise ValueError("file（data/reports/ の下の決定のファイル）が要ります")
    try:
        rel, text = agents.read_decision(f, root)
    except agents.AgentError as e:
        raise ValueError(str(e)) from None
    if not rel.startswith(DECISION_DIR):
        raise ValueError(f"決定のファイルは {DECISION_DIR} の下の物だけ読めます：{f}")
    return {"file": rel, "text": text, "summary": _decision_summary(text)}


def _run_public(st: dict | None) -> dict | None:
    if not st:
        return None
    keys = ("state", "started_at", "finished_at", "dry_run", "last_step", "stopped_at", "error", "record", "events",
            "mode", "max_usd", "spent_usd", "stopped", "resumed_from", "note", "shinko")
    out = {k: st.get(k) for k in keys if k in st}
    out["continuing"] = st.get("mode") == "continue"   # 進捗の印「追記で回している」
    out["resumed"] = st.get("mode") == "resume"        # 進捗の印「止まった所から続けている」
    return out


def request_counts(root: Path, bid: str) -> dict | None:
    """棟 JSON の open の依頼を宛先で数える {"requests": 職員への撮影依頼, "tool_requests": 道具の作業, "photo_requests": [...]}。
    職員の画面の「あなた待ち」は staff 宛てだけ。棟 JSON が無ければ None。"""
    from .schema import split_requests
    b = _jload(root / "data" / "buildings" / f"{bid}.json")
    if not isinstance(b, dict):
        return None
    by = split_requests(b)
    return {"requests": len(by["staff"]), "tool_requests": len(by["tool"]),
            "photo_requests": [{k: r.get(k) for k in ("face", "what", "where", "why")} for r in by["staff"]]}


def unused_staff_photos(root: Path, bid: str) -> dict | None:
    """棟 JSON がある棟の「職員が画面で送った写真のうち、棟 JSON にまだ id が出ない物」（agents.find_new_photos。11/8、F16）。
    写真フォルダ無しの /api/start（追記）はこの写真を新しい写真として使う。
    {"n": 枚数, "tape": うちメジャー, "building_photos": この棟の職員の写真の数, "name": 棟 JSON の name}。棟 JSON が無い・読めなければ None。"""
    from . import agents

    b = _jload(root / "data" / "buildings" / f"{bid}.json")
    if not isinstance(b, dict):
        return None
    try:
        r = agents.find_new_photos(bid, root)
    except Exception as e:  # noqa: BLE001  進捗は止めない
        log(f"未使用の写真を数えられません {bid}: {type(e).__name__}: {e}")
        return None
    return {"n": len(r.get("ids") or []), "tape": len(r.get("tape") or []),
            "building_photos": r.get("building_photos"), "name": b.get("name")}


def api_progress(root: Path, buildings: list[str] | None) -> dict:
    if not buildings:
        buildings = list(RUNS) + [t for t in api_site(root)["targets"] if t not in RUNS]
    out = {}
    for b in buildings:
        if not _ID_RE.match(b):
            continue
        rep = _jload(root / "out" / b / "report.json")
        report = {"stages": rep.get("stages"), "counts": rep.get("counts")} if isinstance(rep, dict) else None
        rc = request_counts(root, b)
        if report is not None and rc is not None:
            report["counts"] = {**(report["counts"] or {}), "requests": rc["requests"], "tool_requests": rc["tool_requests"]}
            report["photo_requests"] = rc["photo_requests"]
        with _runs_lock:
            run = _run_public(RUNS.get(b))
            rs = resume_check(root, b, RUNS.get(b))
        out[b] = {"report": report, "run": run}
        us = unused_staff_photos(root, b)
        if us is not None:   # 棟 JSON がある棟だけ：前に送って未使用の写真（写真を入れずに追記を始められるか。F16）
            out[b]["unused_staff_photos"] = us
        if rs is not None:   # pipeline_state.json がある棟だけ：止まっているか・画面から続けられるか（F15）
            out[b]["resume"] = rs
            from . import agents
            stt = agents.load_state(b, root)
            out[b]["state"] = state_review(stt)   # 進行役の決定・職員宛ての事件など（F19）
            out[b]["history"] = state_timeline(stt)["history"]   # 再開の回数・戻した・進行役（F20）
            out[b]["incident"] = incident(root, b, stt)          # 画面に出す事件の源（待機画面と同じ。F20）
        with _runs_lock:
            run_raw = RUNS.get(b)
        if rs is not None or run_raw is not None:   # 職員の画面と待機画面で同じ言葉（F20）
            from . import agents
            out[b]["live"] = live_phase(agents.load_state(b, root), run_raw, rs)
    site_run = _site_public()
    running = any((v["run"] or {}).get("state") == "running" for v in out.values()) or (site_run or {}).get("state") == "running"
    return {"buildings": out, "site": site_run, "running": running, "at": _now()}


# --- 待機画面（web/progress.html）が読む物（11/8、F8）。どれも読むだけ ---------------------------------

MODEL_JA = {"haiku": "Claude Haiku", "sonnet": "Claude Sonnet", "opus": "Claude Opus", "fable": "Claude Fable"}


def _split_desc(desc: str) -> tuple[str, str]:
    """係の定義の description「〇〇係。説明…」→（係の名前, 一言）。句点が無ければ名前は空。"""
    d = (desc or "").strip()
    if "。" in d:
        name, rest = d.split("。", 1)
        return name.strip(), rest.strip()
    return "", d


def api_steps(root: Path) -> dict:
    """工程 → 係の名前・モデル・一言。工程の順は agents.PIPELINE_STEPS（と追記の CONTINUE_STEPS）、
    係の名前・モデル・一言は .claude/agents/*.md の frontmatter から（ここに固定で書かない）。"""
    from . import agents

    defs = agents.load_agent_defs(root / agents.DEFAULT_DIR)
    order = list(dict.fromkeys(list(agents.PIPELINE_STEPS) + list(agents.CONTINUE_STEPS)))
    steps = []
    for s in order:
        a = agents.step_agent(s)
        d = defs.get(a) or {}
        name, line = _split_desc(d.get("description", ""))
        code = s in agents.CODE_STEPS
        steps.append({"step": s, "agent": a, "name": name or a, "line": line, "code": code,
                      "model": "code" if code else d.get("model"),
                      "model_id": None if code else d.get("model_id"),
                      "model_ja": "コード" if code else MODEL_JA.get(d.get("model") or "", d.get("model") or "")})
    return {"steps": steps, "pipeline": list(agents.PIPELINE_STEPS), "continue": list(agents.CONTINUE_STEPS),
            "max_usd_default": agents.DEFAULT_MAX_USD}


def _pipeline_records(root: Path, b: str) -> list[Path]:
    """pipeline の記録（data/reports/<棟>/pipeline_*.json と out/<棟>/pipeline_*.json。状態ファイルは除く）を古い順に。"""
    out = []
    for d in (root / "data" / "reports" / b, root / "out" / b):
        if d.is_dir():
            out += [p for p in d.glob("pipeline_*.json") if not p.name.startswith("pipeline_state")]
    return sorted(out, key=lambda p: (p.name, p.stat().st_mtime))


def _one_line(s) -> str | None:
    if not s:
        return None
    s = " ".join(str(s).split())
    return s if len(s) <= 140 else s[:139] + "…"


def api_pipeline(root: Path, b: str) -> dict:
    """棟の最新の pipeline の記録の要約（工程・係・モデル・秒・費用・要約 1 行）と、今の状態（out/<棟>/pipeline_state.json）、
    このサーバが起動した物の状態。記録が無ければ record は None。"""
    from . import agents

    if not _ID_RE.match(b or ""):
        raise ValueError(f"棟 id ではない名前: {b}")
    state = agents.load_state(b, root)
    recs = _pipeline_records(root, b)
    want = (state or {}).get("record_name")
    p = next((r for r in reversed(recs) if r.name == want), None) or (recs[-1] if recs else None)
    doc = _jload(p) if p else None
    steps = []
    for r in (doc or {}).get("steps") or []:
        if not isinstance(r, dict):
            continue
        cost = r.get("cost_usd_estimate")
        if cost is None:
            cost = r.get("cost_usd")
        steps.append({"step": r.get("step") or r.get("agent"), "agent": r.get("agent"), "model": r.get("model"),
                      "seconds": r.get("seconds"), "cost_usd": cost, "result": r.get("result"),
                      "started_at": r.get("started_at"), "ended_at": r.get("ended_at") or r.get("logged_at"),
                      "summary": _split_note(r)[0], "warn": _split_note(r)[1]})
    st = None
    if isinstance(state, dict):
        st = {k: state.get(k) for k in ("mode", "status", "current", "done", "steps", "stopped", "spent_usd", "max_usd",
                                        "started_at", "updated_at", "pending_events",
                                        "auto_resumes", "auto_resumes_total")}
        st.update(state_review(state))   # shinko・halt・halted・pending_events・stopped・decisions（F19）
        rd = state.get("redo") if isinstance(state.get("redo"), dict) else None
        st["redo"] = {k: rd.get(k) for k in ("from", "steps", "at", "file")} if rd else None
    with _runs_lock:
        run_raw = RUNS.get(b)
        run = _run_public(run_raw)
        rs = resume_check(root, b, run_raw)
    tl = state_timeline(state) if isinstance(state, dict) else None   # 周ごとの結果と再開・戻した印（F20）
    live = live_phase(state, run_raw, rs)
    rec = None
    if isinstance(doc, dict) and p is not None:
        rec = {"file": p.relative_to(root).as_posix(), "made_at": doc.get("made_at"), "dry_run": doc.get("dry_run"),
               "runner": doc.get("runner"), "mode": doc.get("mode"), "started_at": doc.get("started_at"),
               "seconds_total": doc.get("seconds_total"), "wall_seconds": doc.get("wall_seconds"),
               "cost_usd": doc.get("spent_usd", doc.get("cost_usd_estimate")), "max_usd": doc.get("max_usd"),
               "stopped": doc.get("stopped"), "stopped_early": doc.get("stopped_early"), "steps": steps}
    return {"building": b, "record": rec, "state": st, "run": run, "resume": rs, "records": len(recs), "at": _now(),
            "live": live, "timeline": tl, "incident": incident(root, b, state)}


def _url(root: Path, p: Path | str | None) -> str | None:
    """リポジトリの中のファイル → 配信の道（/out/…・/web/…・/data/photos/…）。配信しない所・無い物は None。"""
    if not p:
        return None
    q = Path(str(p).replace("\\", "/"))
    q = q if q.is_absolute() else root / q
    if not q.is_file():
        return None
    try:
        rel = q.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None
    return "/" + rel if rel.startswith(STATIC_PREFIXES) else None


def _measured_chains(b: dict, limit: int = 12) -> list[dict]:
    """棟 JSON の measured の値のうち鎖（chain）を持つ物を、どこの値か（道）と一緒に。"""
    out: list[dict] = []

    def walk(o, path):
        if len(out) >= limit:
            return
        if isinstance(o, dict):
            if o.get("status") == "measured" and o.get("chain"):
                out.append({"where": path, "value": o.get("value"), "unit": o.get("unit", "m"), "tol": o.get("tol"),
                            "measured_kind": o.get("measured_kind"), "chain": o.get("chain"),
                            "method": _one_line(o.get("method"))})
                return
            for k, v in o.items():
                if k in ("photos", "requests", "history"):
                    continue
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{(v.get('id') if isinstance(v, dict) and v.get('id') else i)}]")
    walk(b, "")
    return out


def _dirs(d: Path) -> list[Path]:
    return sorted(x for x in d.glob("*") if x.is_dir()) if d.is_dir() else []


def _shown_masks(r: dict) -> list[dict]:
    """待機画面に描く箱：索引の masks のうち box のある物。by: auto のまま（見直しで確かめていない）の箱は描かない（F11 の決まり）。"""
    from . import privacy

    return [{"kind": m.get("kind"), "label": privacy.KINDS.get(m.get("kind"), m.get("kind")), "box": m.get("box"),
             "note": m.get("note")} for m in r.get("masks") or [] if m.get("box") and m.get("by") != "auto"]


def _clip_front(poly, hrow, sign: float, eps: float) -> list:
    """写真 px の多角形を、H で写した時に面の手前（w·sign > eps）になる側だけに切る（地平線の向こうは面に乗らない）。"""
    import numpy as np

    def wv(p):
        return float(np.dot(hrow, [p[0], p[1], 1.0])) * sign - eps
    out = []
    for i, cur in enumerate(poly):
        prv = poly[i - 1]
        a_, b_ = wv(prv), wv(cur)
        if b_ >= 0:
            if a_ < 0:
                t = a_ / (a_ - b_)
                out.append((prv[0] + t * (cur[0] - prv[0]), prv[1] + t * (cur[1] - prv[1])))
            out.append(cur)
        elif a_ >= 0:
            t = a_ / (a_ - b_)
            out.append((prv[0] + t * (cur[0] - prv[0]), prv[1] + t * (cur[1] - prv[1])))
    return out


def _map_boxes(H, w: float, h: float, masks: list[dict], ref: tuple[float, float] | None = None) -> list[dict] | None:
    """箱（割合）を 写真 px → 別の平面 の H で写した多角形。ref は写した先で手前にあると分かっている写真の点（無ければ写真の中心）。
    箱のうち地平線の向こう（平面に乗らない所）は切り捨てる。写せない（H が無い・形が変）なら None。"""
    import numpy as np

    if not masks:
        return []
    if H is None or not w or not h:
        return None
    try:
        Hm = np.array(H, float).reshape(3, 3)
    except (TypeError, ValueError):
        return None
    rx, ry = ref if ref is not None else (w / 2, h / 2)
    sign = float(Hm[2] @ np.array([rx, ry, 1.0]))
    if sign == 0 or not np.isfinite(sign):
        return None
    eps = abs(sign) * 0.02          # 地平線のすぐ手前は遠くへ飛ぶので、手前の点の 2 % までで止める
    sign = 1.0 if sign > 0 else -1.0
    out = []
    for m in masks:
        x0, y0, x1, y1 = m["box"]
        poly = _clip_front([(x0 * w, y0 * h), (x1 * w, y0 * h), (x1 * w, y1 * h), (x0 * w, y1 * h)], Hm[2], sign, eps)
        if len(poly) < 3:
            continue                 # 箱が丸ごと平面の外（空など）
        q = (Hm @ np.array([[x, y, 1.0] for x, y in poly]).T).T
        if not np.all(np.isfinite(q)):
            return None
        out.append({"kind": m["kind"], "label": m["label"], "poly": [[round(float(a_), 3), round(float(b_), 3)] for a_, b_ in q[:, :2] / q[:, 2:3]]})
    return out


def _board_masks(root: Path, b: str, face_id: str, photo_ids: list[str], masks_by: dict[str, list[dict]],
                 outlines: dict[str, list] | None = None) -> list[dict] | None:
    """製図板の面の (s, z) に写した箱。H は登録（registry）の面ごとの H_plane（coverage と同じ entry_H_now の逆）。
    箱のある写真のどれか 1 枚でも写せなければ None（その面は見せない）。"""
    import numpy as np

    need = [pid for pid in photo_ids if masks_by.get(pid)]
    if not need:
        return []
    try:
        from . import coverage as cov
        from .schema import load_building

        bld = load_building(root / "data" / "buildings" / f"{b}.json")
        reg = cov.load_registry(root / "data" / "photos" / "registry.json")
        ents = cov.expand_entries(cov.entries_for(reg, b), bld)
        f = next(x for x in bld.get("faces", []) if x.get("id") == face_id)
    except Exception:  # noqa: BLE001  写せない → 見せない
        return None
    out = []
    for pid in need:
        e = next((e for e in ents if e.get("face") == face_id and e.get("photo") == pid), None)
        if e is None or not e.get("image_size"):
            return None
        try:
            Hinv = np.linalg.inv(cov.entry_H_now(e, f))      # 写真 px → 面の (s, z)
        except Exception:  # noqa: BLE001
            return None
        w, h = e["image_size"]
        Hf = cov.entry_H_now(e, f)
        ol = (outlines or {}).get(pid) or []
        pts = [pt for poly in ol for pt in poly]
        if not pts:
            return None
        sc, zc = float(np.mean([p_[0] for p_ in pts])), float(np.mean([p_[1] for p_ in pts]))
        q = Hf @ np.array([sc, zc, 1.0])            # その写真が面を写している点（使える所の中心）→ 写真 px
        if q[2] == 0:
            return None
        polys = _map_boxes(Hinv, w, h, masks_by[pid], ref=(q[0] / q[2], q[1] / q[2]))
        if polys is None:
            return None
        out += [{"photo": pid, **m} for m in polys]
    return out


def api_artifacts(root: Path, b: str) -> dict:
    """待機画面の場面に使う、今ある生成物の一覧（無い物は入れない）。絵は配信の道、数は元の JSON の物。"""
    if not _ID_RE.match(b or ""):
        raise ValueError(f"棟 id ではない名前: {b}")
    out_b = root / "out" / b
    bj = _jload(root / "data" / "buildings" / f"{b}.json")
    bj = bj if isinstance(bj, dict) else {}
    face_names = {f.get("id"): f.get("name") for f in bj.get("faces") or [] if isinstance(f, dict)}

    # a・b 写真（係に見せる縮小版 send_file。無ければ報告の縮小版）と灰色の箱（索引の masks）。公開しない写真は出さない
    idx = _jload(root / "data" / "photos" / "index.json") or {}
    photos_ = []
    for r in sorted((r for r in idx.get("photos") or [] if isinstance(r, dict) and r.get("building") == b
                     and r.get("public") is not False and r.get("period") != "before"),
                    key=lambda r: str(r.get("taken_at") or r.get("id"))):
        src = _url(root, Path("data/photos") / r["send_file"]) if r.get("send_file") else None
        src = src or _url(root, out_b / "report" / f"thumb_{r['id']}.jpg")
        if not src:
            continue
        photos_.append({"id": r["id"], "src": src, "face": r.get("face"), "taken_at": r.get("taken_at"),
                        "ruler": r.get("ruler"), "masks": _shown_masks(r)})
    srcs = {p["id"]: p["src"] for p in photos_}
    # 元の写真から作った絵（正面化・製図板・検収の重ね・模型の描画）にも同じ箱を描く（11/8、利用者の決定）
    masks_by = {r["id"]: _shown_masks(r) for r in idx.get("photos") or [] if isinstance(r, dict) and r.get("building") == b}

    # c 正面化（compare）と測量係の線（sokuryo_lines）、メジャーの読み（sokuryo_tape_select）
    compare = []
    for j in (sorted(out_b.glob("compare/*_compare.json")) if out_b.is_dir() else []):
        stem = j.name[: -len("_compare.json")]
        if "_" not in stem:
            continue
        pid, face = stem.rsplit("_", 1)
        d = _jload(j) or {}
        if not d.get("ok"):
            continue
        sl = _jload(out_b / "sokuryo_lines" / f"{pid}_{face}.json")
        # 正面化の H（写真 px → 正面化した絵 px）は rectify の JSON（正面化した絵と同じ名前の .json）
        rj = _jload(root / Path(str(d.get("source") or "").replace("\\", "/")).with_suffix(".json")) if d.get("source") else None
        rmask = _map_boxes((rj or {}).get("H"), (rj or {}).get("width"), (rj or {}).get("height"), masks_by.get(pid) or []) \
            if isinstance(rj, dict) else (None if masks_by.get(pid) else [])
        compare.append({"masks": masks_by.get(pid) or [], "rect_masks": rmask, "masks_ok": rmask is not None, "photo": pid, "face": face, "photo_src": srcs.get(pid), "png": _url(root, j.with_suffix(".png")),
                        "rectified": _url(root, d.get("source")), "px_per_m": d.get("px_per_m"),
                        "x_origin_px": d.get("x_origin_px"), "z_origin_px": d.get("z_origin_px"),
                        "lines": [{k: ln.get(k) for k in ("axis", "what", "json_m", "photo_m", "diff_m", "ok")}
                                  for ln in d.get("lines") or []][:40],
                        "sokuryo_lines": sl if isinstance(sl, dict) else None})
    compare.sort(key=lambda c: (not c["masks_ok"], c["sokuryo_lines"] is None, c["photo_src"] is None, c["rectified"] is None))
    tape = _jload(root / "data" / "reports" / b / "sokuryo_tape_select.json")
    prim = tape.get("primary") if isinstance(tape, dict) else None
    tape_out = None
    if isinstance(prim, dict):
        tape_out = {k: prim.get(k) for k in ("photo", "face", "what", "read", "n_tiles", "pitch_m", "pitch_tol_m", "calc")}
        tape_out.update(made_by=tape.get("made_by"), status_note=tape.get("status_note"))

    # e 立面図
    elev = [{"face": p.stem[5:], "name": face_names.get(p.stem[5:]), "svg": _url(root, p)}
            for p in (sorted(out_b.glob("elev_*.svg")) if out_b.is_dir() else [])]

    # f 製図板（coverage_<面>_photo.png と、写真ごとの使える所の多角形）
    cov = _jload(out_b / "coverage.json") or {}
    coverage = []
    for f in cov.get("faces") or []:
        png = _url(root, out_b / f"coverage_{f.get('face')}_photo.png")
        if not png or f.get("unknown"):
            continue
        pids = [p.get("photo") for p in f.get("photos") or []]
        bm = _board_masks(root, b, f.get("face"), pids, masks_by,
                          {p.get("photo"): p.get("outline") or ([p["polygon"]] if p.get("polygon") else []) for p in f.get("photos") or []})
        coverage.append({"masks": bm, "masks_ok": bm is not None,
                         "face": f.get("face"), "name": f.get("name"), "png": png, "length_m": f.get("length_m"),
                         "ground_z": f.get("ground_z"), "top_z": f.get("top_z"), "coverage_pct": f.get("coverage_pct"),
                         "photos": [{"photo": p.get("photo"), "order": p.get("order"), "used_m2": p.get("used_m2"),
                                     "outline": p.get("outline") or ([p["polygon"]] if p.get("polygon") else []),
                                     "label_at": p.get("label_at")} for p in f.get("photos") or []]})
    coverage.sort(key=lambda c: (not c["masks_ok"], -len(c["photos"])))

    # g 検収（合格の面だけ。検収係の kenshu_summary は report.json の counts.kenshu に写っている）
    rep = _jload(out_b / "report.json") or {}
    counts = rep.get("counts") or {}
    ken = counts.get("kenshu") or {}
    passed = set(ken.get("pass") or [])
    rdirs, pdirs = _dirs(out_b / "recheck"), _dirs(out_b / "photoproj")
    kenshu = []
    if rdirs:
        rs = _jload(rdirs[-1] / "summary.json") or {}
        for ph in rs.get("photos") or []:
            if ph.get("face") not in passed:
                continue
            png = _url(root, rdirs[-1] / f"lines_{ph.get('photo')}_{ph.get('face')}.png")
            if not png:
                continue
            lines = [{k: ln.get(k) for k in ("what", "group", "axis", "diff_m", "ok", "weak")}
                     for ln in ph.get("lines") or [] if ln.get("diff_m") is not None]
            kenshu.append({"masks": masks_by.get(ph.get("photo")) or [],   # 検収の重ね・模型の描画は写真と同じ枠 → 割合のまま
                           "photo": ph.get("photo"), "face": ph.get("face"), "photo_src": srcs.get(ph.get("photo")),
                           "view": _url(root, out_b / f"view_photo_{ph.get('photo')}.png"),
                           "overlay": _url(root, pdirs[-1] / f"overlay_{ph.get('photo')}.png") if pdirs else None,
                           "lines_png": png, "recheck": rdirs[-1].name, "n_ok": ph.get("n_ok"), "n_ng": ph.get("n_ng"),
                           "n_lines": ph.get("n_lines"), "tol_m": rs.get("tol_m"), "lines": lines})
        kenshu.sort(key=lambda k: (not (k["view"] and k["photo_src"]), -(k["n_ok"] or 0), k["n_ng"] or 0))

    # i 歩く画面の視点（model_report の web_cameras.street.query）
    mr = _jload(out_b / "model_report.json") or {}
    cam = (((mr.get("web_cameras") or {}).get("street")) or {}).get("query")
    return {"building": b, "name": bj.get("name"), "has_building_json": bool(bj), "photos": photos_,
            "compare": compare, "tape": tape_out, "chains": _measured_chains(bj) if bj else [],
            "elev": elev, "coverage": coverage, "kenshu": kenshu, "kenshu_faces": ken,
            "evidence": {k: _url(root, out_b / f"evidence_{k}.png") for k in ("photo", "three", "overview")},
            "walk": _url(root, out_b / "walk.png"), "walk_query": cam, "sheet": _url(root, out_b / "sheet.png"),
            "report_html": _url(root, out_b / "report.html"), "report": {"stages": rep.get("stages"), "counts": counts},
            "at": _now()}


# --- HTTP ---------------------------------------------------------------------------------------

def api_confirm_list(root: Path, buildings: list[str] | None) -> dict:
    """根拠の門で「確かめてほしい」と出た写真（棟ごと）と、その棟の面の一覧。職員の画面の 4 の欄が読む。"""
    from . import photos, schema
    if buildings is None:
        site = _jload(root / "web" / "data" / "site.json") or {}
        buildings = list((site.get("scope") or {}).get("targets") or [])
    out: dict = {}
    for bid in buildings:
        bp = root / "data" / "buildings" / f"{bid}.json"
        if not bp.exists():
            out[bid] = {"items": [], "faces": [], "note": "棟 JSON がまだ無い"}
            continue
        b = schema.load_building(bp)
        res = photos.write_to_confirm(b, root)
        items = []
        for it in res["items"]:
            f = str(it.get("file") or "")
            items.append({**{k: it.get(k) for k in ("photo", "problems", "detail", "building", "face", "faces", "confidence",
                                                     "used_at", "value_faces")},
                          "url": f"/data/photos/{f}" if f else None,
                          "why": (str(it.get("notes") or "").split(" | ")[-1])[:200]})
        out[bid] = {"items": items, "faces": [fc.get("id") for fc in b.get("faces", []) if fc.get("id")],
                    "name": b.get("name")}
    return {"buildings": out}


def api_confirm(root: Path, body: dict) -> dict:
    """職員が 1 枚を確かめる：{"photo", "building"?, "face"?, "faces"?}。棟・面が無ければ「合っている」。"""
    from . import photos
    from .ledger import lock as _ledger_lock
    pid = str(body.get("photo") or "")
    if not pid:
        raise ValueError("photo が要ります")
    with _photos_lock, _ledger_lock("ledger", label=f"serve 確かめ {pid}"):
        res = photos.confirm_photo(root / photos.INDEX_PATH, pid, by="staff", building=body.get("building"),
                                   face=body.get("face"), faces=body.get("faces"))
    log(f"職員が確かめた：{pid}（{'直した' if res['changed'] else '合っている'}）")
    return {"photo": pid, "changed": res["changed"]}


def _fix_text(s: str | None) -> str:
    """multipart のヘッダに生の UTF-8 で来た名前（ブラウザの既定）を読み直す。"""
    if not s:
        return ""
    try:
        return s.encode("utf-8", "surrogateescape").decode("utf-8")
    except UnicodeError:
        return s


def parse_multipart(ctype: str, body: bytes) -> tuple[list[tuple[str, bytes]], dict[str, str]]:
    msg = BytesParser(policy=policy.default).parsebytes(
        b"MIME-Version: 1.0\r\nContent-Type: " + ctype.encode("latin-1") + b"\r\n\r\n" + body)
    if not msg.is_multipart():
        raise ValueError("multipart/form-data ではありません")
    files, fields = [], {}
    for part in msg.iter_parts():
        name = _fix_text(part.get_param("name", header="content-disposition"))
        fname = part.get_filename()
        data = part.get_payload(decode=True) or b""
        if fname is not None:
            files.append((_fix_text(fname), data))
        else:
            fields[name] = data.decode("utf-8", "replace")
    return files, fields


class Handler(SimpleHTTPRequestHandler):
    server_version = "campus-recorder/1"

    def __init__(self, *args, **kw):
        super().__init__(*args, directory=str(args[2].root), **kw)  # args = (request, client_address, server)

    @property
    def root(self) -> Path:
        return self.server.root  # type: ignore[attr-defined]

    def log_message(self, fmt, *args):  # 1 行ずつ標準出力へ
        log(f"{self.address_string()} {fmt % args}")

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, code: int, obj) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise OverflowError(f"送信が大きすぎます（{n} バイト、上限 {MAX_BODY}）")
        return self.rfile.read(n) if n > 0 else b""

    def _guard(self, fn) -> None:
        try:
            fn()
        except Busy as e:
            self._json(409, {"error": "作業中です（同じ棟・敷地の取得は同時に 1 つ）", "busy": e.args[0]})
        except Refused as e:
            self._json(409, {"error": e.args[1], "code": e.args[0]})
        except OverflowError as e:
            self._json(413, {"error": str(e)})
        except (ValueError, KeyError, TypeError) as e:
            self._json(400, {"error": f"{type(e).__name__}: {e}"})
        except Exception as e:  # noqa: BLE001  サーバは落とさない
            log("エラー: " + traceback.format_exc().strip().replace("\n", " | "))
            self._json(500, {"error": f"{type(e).__name__}: {e}"})

    # GET
    def do_GET(self):  # noqa: N802
        u = urlsplit(self.path)
        if u.path.startswith("/api/"):
            return self._guard(lambda: self._api_get(u))
        if u.path in ("/", ""):
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/web/staff.html")
            self.end_headers()
            return None
        rel = unquote(u.path).lstrip("/")
        parts = rel.split("/")
        if not rel.startswith(STATIC_PREFIXES) or any(p.startswith(".") for p in parts):
            return self._json(404, {"error": "配信していない道です", "path": u.path})
        return super().do_GET()

    def do_HEAD(self):  # noqa: N802
        rel = unquote(urlsplit(self.path).path).lstrip("/")
        if not rel.startswith(STATIC_PREFIXES) or any(p.startswith(".") for p in rel.split("/")):
            self.send_response(404)
            self.end_headers()
            return None
        return super().do_HEAD()

    def _api_get(self, u) -> None:
        q = parse_qs(u.query)
        if u.path == "/api/site":
            return self._json(200, api_site(self.root))
        if u.path == "/api/progress":
            b = [x.strip() for v in q.get("buildings", []) for x in v.split(",") if x.strip()]
            return self._json(200, api_progress(self.root, b or None))
        if u.path == "/api/confirm":
            b = [x.strip() for v in q.get("buildings", []) for x in v.split(",") if x.strip()]
            return self._json(200, api_confirm_list(self.root, b or None))
        if u.path == "/api/steps":
            return self._json(200, api_steps(self.root))
        if u.path == "/api/pipeline":
            return self._json(200, api_pipeline(self.root, (q.get("building") or [""])[0].strip()))
        if u.path == "/api/decision":
            return self._json(200, api_decision(self.root, (q.get("file") or [""])[0]))
        if u.path == "/api/artifacts":
            return self._json(200, api_artifacts(self.root, (q.get("building") or [""])[0].strip()))
        return self._json(404, {"error": "知らない API", "path": u.path})

    # POST
    def do_POST(self):  # noqa: N802
        self._guard(self._api_post)

    def _api_post(self) -> None:
        u = urlsplit(self.path)
        if u.path == "/api/photos":
            ctype = self.headers.get("Content-Type") or ""
            files, fields = parse_multipart(ctype, self._body())
            assignments = json.loads(fields.get("assignments") or "[]")
            if not isinstance(assignments, list):
                raise ValueError("assignments は配列の JSON")
            res = api_photos(self.root, files, assignments)
            log(f"写真 {res['received']} 枚を受け取り、置いた {res['saved']}・同じ物 {res['reused']}・索引に新規 {res['imported']}・棟 {res['by_building']}")
            return self._json(200, res)
        if u.path == "/api/tape":
            files, fields = parse_multipart(self.headers.get("Content-Type") or "", self._body())
            return self._json(200, api_tape(self.root, files, fields))
        body = json.loads(self._body() or b"{}")
        if not isinstance(body, dict):
            raise ValueError("本文は JSON のオブジェクト")
        if u.path == "/api/start":
            return self._json(200, api_start(self.root, body))
        if u.path == "/api/resume":
            return self._json(200, api_resume(self.root, body))
        if u.path == "/api/review":
            return self._json(200, api_review(self.root, body))
        if u.path == "/api/campus":
            res = api_campus(self.root, body)
            log(f"敷地 {res['campus']}: {res['message']}")
            return self._json(200, res)
        if u.path == "/api/campus/select":
            return self._json(200, api_campus_select(self.root, body))
        if u.path == "/api/site/build":
            return self._json(200, api_site_build(self.root, body))
        if u.path == "/api/confirm":
            return self._json(200, api_confirm(self.root, body))
        return self._json(404, {"error": "知らない API", "path": u.path})


def make_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, root: Path | None = None) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    srv.root = Path(root or Path.cwd()).resolve()  # type: ignore[attr-defined]
    return srv


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"ポート（既定 {DEFAULT_PORT}）")
    p.add_argument("--campus", default=None, help="使う敷地（data/site/<campus>.json）。省略で環境変数 RECORDER_CAMPUS か data/site/ の既定。画面でも選べる")
    p.add_argument("--host", default=DEFAULT_HOST,
                   help="待ち受ける場所（既定 127.0.0.1＝この PC だけ。0.0.0.0 は学内公開向けで、認証が無い）")


def run_cli(a: argparse.Namespace) -> int:
    root = Path.cwd()
    if not (root / "web" / "staff.html").exists():
        print(f"web/staff.html がありません。リポジトリ直下で起動してください（今の場所: {root}）")
        return 2
    if getattr(a, "campus", None):
        if not (root / "data" / "site" / f"{a.campus}.json").exists():
            print(f"data/site/{a.campus}.json がありません")
            return 2
        SELECTED["campus"] = a.campus
        os.environ[CAMPUS_ENV] = a.campus
    srv = make_server(a.host, a.port, root)
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        log(f"注意：{a.host} で待ち受けます。このサーバには認証がありません。学内に出すなら認証付きの中継（リバースプロキシ）を前に置いてください")
    log(f"職員の画面: http://{'localhost' if a.host in ('127.0.0.1', '0.0.0.0') else a.host}:{srv.server_address[1]}/web/staff.html"
        f"（API キー {'あり' if os.environ.get('ANTHROPIC_API_KEY') else 'なし＝係を呼ばずに手順だけ確かめる試し'}、"
        f"敷地 {_default_campus() or '未定'}）。止めるには Ctrl+C")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("止めます")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    ap = argparse.ArgumentParser(prog="recorder serve")
    add_args(ap)
    sys.exit(run_cli(ap.parse_args()))
