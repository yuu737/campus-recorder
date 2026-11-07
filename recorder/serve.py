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
                                   （省けば agents.DEFAULT_MAX_USD＝20 USD、0 で上限なし）
    GET  /api/progress?buildings=a,b   report.json の stages・counts と、動いている pipeline の状態。
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
        if st.get("mode") == "continue":   # 棟 JSON がある棟は追記（11/8、K-106。T3 の continue_pipeline）
            res = agents.continue_pipeline(bid, st.get("photos_folder"), dry_run=st["dry_run"], cwd=root, max_usd=st.get("max_usd"))
        else:
            res = agents.run_pipeline(bid, st.get("photos_folder"), dry_run=st["dry_run"], cwd=root, max_usd=st.get("max_usd"))
        steps = [s.get("step") for s in res.get("steps", []) if isinstance(s, dict)]
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
    except Exception as e:  # noqa: BLE001  1 棟の失敗でサーバは落とさない
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


def _run_public(st: dict | None) -> dict | None:
    if not st:
        return None
    keys = ("state", "started_at", "finished_at", "dry_run", "last_step", "stopped_at", "error", "record", "events",
            "mode", "max_usd", "spent_usd", "stopped")
    out = {k: st.get(k) for k in keys if k in st}
    out["continuing"] = st.get("mode") == "continue"   # 進捗の印「追記で回している」
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
        out[b] = {"report": report, "run": run}
    site_run = _site_public()
    running = any((v["run"] or {}).get("state") == "running" for v in out.values()) or (site_run or {}).get("state") == "running"
    return {"buildings": out, "site": site_run, "running": running, "at": _now()}


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
