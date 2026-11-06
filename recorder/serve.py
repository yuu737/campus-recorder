"""職員の画面（web/staff.html）の受付をする小さなローカルのサーバ `recorder serve`。

本番の形は「職員の PC 1 台でローカルに動く」。標準ライブラリの http.server だけで、依存を足さない。
判断や計算はしない。既存の関数（photos.import_photos / photos.assign_photos / agents.run_pipeline /
site.campus_init）をそのまま呼ぶだけの受付。

配信：リポジトリ直下の web/・out/・data/photos/ だけ（それ以外の道は 404）。`/` は /web/staff.html へ。
API（どれも JSON を返し、失敗してもサーバは落ちない）：
    GET  /api/site                 web/data/site.json の有無と scope.targets
    POST /api/campus               {"university", "campus_name"} 敷地 JSON が無ければ campus_init
    POST /api/photos               multipart：写真（photos）＋ assignments（JSON 文字列）→ 保存 → 取り込み → 振り分け
    POST /api/start                {"buildings": [...], "dry_run": false} 棟ごとに run_pipeline を別スレッドで
    GET  /api/progress?buildings=a,b   report.json の stages・counts と、動いている pipeline の状態

写真は data/photos/<今日 YYYYMMDD>/<棟 id>/ に置く（git 管理外）。索引 data/photos/index.json に入る。
サーバはリポジトリ直下を cwd にして起動する（敷地 JSON・索引の道は cwd からの相対で読まれるため）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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
    from . import site

    try:
        return site.default_campus()
    except Exception:  # noqa: BLE001  敷地 JSON が無い・複数でも受付は続ける
        return None


# --- API の中身（ハンドラから切り離して、テストでも呼べる形） -----------------------------------

def api_site(root: Path) -> dict:
    p = root / "web" / "data" / "site.json"
    doc = _jload(p) if p.exists() else None
    campus = _default_campus()
    return {"ready": isinstance(doc, dict), "path": "web/data/site.json",
            "campus": (doc or {}).get("campus") if isinstance(doc, dict) else None,
            "university": (doc or {}).get("university") if isinstance(doc, dict) else None,
            "targets": list(((doc or {}).get("scope") or {}).get("targets") or []) if isinstance(doc, dict) else [],
            "campus_json": campus, "api_key": bool(os.environ.get("ANTHROPIC_API_KEY"))}


def api_campus(root: Path, body: dict) -> dict:
    from . import site

    univ = str(body.get("university") or "").strip()
    name = str(body.get("campus_name") or "").strip()
    have = _default_campus()
    if have:
        return {"made": False, "campus": have, "path": f"data/site/{have}.json", "message": "敷地 JSON は既にあります（何もしていません）"}
    if not univ or not name:
        raise ValueError("大学名とキャンパス名の両方が要ります")
    slug = slug_of(name if slug_of(name) != "campus" else univ)
    path, made = site.campus_init(slug, univ, name, None, data_site=root / "data" / "site")
    return {"made": made, "campus": slug, "path": Path(path).relative_to(root).as_posix(),
            "next": f"recorder site build --campus {slug}",
            "message": "敷地 JSON の骨組みを作りました。地図（OSM）・PLATEAU・標高の取得はこのあと係が進めます" if made else "敷地 JSON は既にあります"}


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
    """写真を data/photos/<日付>/<棟>/ に置き → import_photos（period during）→ assign_photos。"""
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
    with _photos_lock:
        idx = photos.load_index(index_path)
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
                                               root=photo_root, period="during")
            added_total += len(added)
            _last_folder[folder] = (photos.PHOTO_DIR / day / folder).as_posix()
        rows = [{"id": f.stem, "building": a.get("building"), "interior": bool(a.get("interior")),
                 "floor": a.get("floor") if a.get("interior") else None}
                for folder, f, a in placed if folder != UNSORTED]
        res = photos.assign_photos(index_path, rows, campus=campus) if rows else {
            "assigned": 0, "interior": 0, "unchanged": 0, "warnings": [], "errors": [], "not_found": []}
    by_b: dict[str, int] = {}
    for folder, _f, _a in placed:
        by_b[folder] = by_b.get(folder, 0) + 1
    return {"received": len(files), "saved": saved, "reused": reused, "imported": added_total,
            "folder": (photos.PHOTO_DIR / day).as_posix(), "by_building": by_b,
            "buildings": sorted(b for b in by_b if b != UNSORTED),
            "assign": res, "warnings": warnings + [{"gps": w} for w in res.get("warnings", [])]}


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
        res = agents.run_pipeline(bid, st.get("photos_folder"), dry_run=st["dry_run"], cwd=root)
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
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    dry = bool(body.get("dry_run")) or not has_key
    with _runs_lock:
        busy = [b for b in blds if (RUNS.get(b) or {}).get("state") == "running"]
        if busy:
            raise Busy(busy)
        stamp = f"{datetime.now():%Y%m%d_%H%M%S}"
        started = []
        for b in blds:
            st = {"building": b, "state": "running", "started_at": _now(), "finished_at": None, "dry_run": dry,
                  "photos_folder": _last_folder.get(b), "last_step": None, "stopped_at": None, "error": None,
                  "record": f"out/{b}/serve_{stamp}.json"}
            RUNS[b] = st
            started.append(b)
    for b in started:
        _record(root, b, RUNS[b])
        threading.Thread(target=_worker, args=(root, b), name=f"pipeline-{b}", daemon=True).start()
        log(f"pipeline {b} 起動" + ("（dry-run）" if dry else ""))
    out = {"started": started, "dry_run": dry, "records": [RUNS[b]["record"] for b in started]}
    if not has_key:
        out["note"] = "ANTHROPIC_API_KEY が無いので dry-run（係は呼ばず、送る内容だけ作る）で動かしました"
    return out


def _run_public(st: dict | None) -> dict | None:
    if not st:
        return None
    keys = ("state", "started_at", "finished_at", "dry_run", "last_step", "stopped_at", "error", "record", "events")
    return {k: st.get(k) for k in keys if k in st}


def api_progress(root: Path, buildings: list[str] | None) -> dict:
    if not buildings:
        buildings = list(RUNS) + [t for t in api_site(root)["targets"] if t not in RUNS]
    out = {}
    for b in buildings:
        if not _ID_RE.match(b):
            continue
        rep = _jload(root / "out" / b / "report.json")
        report = {"stages": rep.get("stages"), "counts": rep.get("counts")} if isinstance(rep, dict) else None
        with _runs_lock:
            run = _run_public(RUNS.get(b))
        out[b] = {"report": report, "run": run}
    running = any((v["run"] or {}).get("state") == "running" for v in out.values())
    return {"buildings": out, "running": running, "at": _now()}


# --- HTTP ---------------------------------------------------------------------------------------

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
            self._json(409, {"error": "その棟は係が作業中です（同じ棟は同時に 1 つ）", "busy": e.args[0]})
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
        body = json.loads(self._body() or b"{}")
        if not isinstance(body, dict):
            raise ValueError("本文は JSON のオブジェクト")
        if u.path == "/api/start":
            return self._json(200, api_start(self.root, body))
        if u.path == "/api/campus":
            res = api_campus(self.root, body)
            log(f"敷地 {res['campus']}: {res['message']}")
            return self._json(200, res)
        return self._json(404, {"error": "知らない API", "path": u.path})


def make_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, root: Path | None = None) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    srv.root = Path(root or Path.cwd()).resolve()  # type: ignore[attr-defined]
    return srv


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"ポート（既定 {DEFAULT_PORT}）")
    p.add_argument("--host", default=DEFAULT_HOST,
                   help="待ち受ける場所（既定 127.0.0.1＝この PC だけ。0.0.0.0 は学内公開向けで、認証が無い）")


def run_cli(a: argparse.Namespace) -> int:
    root = Path.cwd()
    if not (root / "web" / "staff.html").exists():
        print(f"web/staff.html がありません。リポジトリ直下で起動してください（今の場所: {root}）")
        return 2
    srv = make_server(a.host, a.port, root)
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        log(f"注意：{a.host} で待ち受けます。このサーバには認証がありません。学内に出すなら認証付きの中継（リバースプロキシ）を前に置いてください")
    log(f"職員の画面: http://{'localhost' if a.host in ('127.0.0.1', '0.0.0.0') else a.host}:{srv.server_address[1]}/web/staff.html"
        f"（API キー {'あり' if os.environ.get('ANTHROPIC_API_KEY') else 'なし＝dry-run'}）。止めるには Ctrl+C")
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
