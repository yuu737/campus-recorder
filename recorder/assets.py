"""CC0 の公開アセット（見た目だけ）の台帳 `data/assets/catalog.json` を扱う。bpy を使わない純 Python。

分担
    位置・個数・大きさ   棟 JSON の根拠付きの値（部品 {"type": "asset", ...}）
    見た目              アセット（形の定型な部品の模型、質感の写真）

台帳の項目（items[]）
    {"id", "kind": "model"|"texture"|"hdri", "what", "source": "polyhaven"|"kenney",
     "url", "license": "CC0-1.0", "license_url", "fetched_at", "file", "size_m": [w, d, h], "note",
     "polyhaven": {"id", "res"},           Poly Haven の API で取る物（url は https://api.polyhaven.com/files/<id>）
     "kenney": {"zip", "member"},          Kenney の zip（手で落とした zip を assets/kenney/ に置く。下の注）
     "tile_m": [w, h],                     texture の実寸（1 枚が何 m 四方か）
     "node": 名前,                         model の gltf に色違いが並ぶとき、使う最上位の節の名前
     "maps": {"color", "normal", "rough"}, texture の各画像（file からの相対）
     "sha256": {相対パス: 値}}             取得物の指紋（fetch が書く）
    CC0 以外（CC-BY も）は入れない。validate_catalog が落とす。
    該当が無かった物は not_found[] に {"what", "searched", "fallback"} で書く（例：手すりの支柱 → 箱のまま）。

取得物は `assets/`（git 管理外）の下の source ごとのフォルダにだけ置く。そこからスクリプトは実行しない。

Blender の中からも `import assets` で読めるよう、相対 import はしない。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data" / "assets" / "catalog.json"
STORE = ROOT / "assets"

ALLOWED_LICENSES = {"CC0-1.0"}
KINDS = ("model", "texture", "hdri")
SOURCES = ("polyhaven", "kenney")
MODEL_EXT = (".glb", ".gltf", ".obj")
SCALE_WARN = (0.5, 2.0)     # 拡大率がこの外なら warning（アセットの選び方が違う疑い）
UA = "campus-recorder/0.1 (Built with Claude Japan 2026)"


# --- 台帳 ------------------------------------------------------------------------

def load_catalog(path: str | Path | None = None) -> dict:
    p = Path(path) if path else CATALOG
    return json.loads(p.read_text(encoding="utf-8"))


def save_catalog(cat: dict, path: str | Path | None = None) -> None:
    p = Path(path) if path else CATALOG
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cat, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def decision_problem(it: dict, doc: dict | None = None) -> str | None:
    """台帳の項目に進行役の承認（decision が決定の台帳にあり approve）があるか。無ければ理由。"""
    try:
        from . import decisions
    except ImportError:   # Blender の中など、パッケージ外で読まれた時
        import decisions  # type: ignore
    p = decisions.problem(it.get("decision"), f"アセット {it.get('id')}", doc)
    return None if p is None else f"{p}（進行役の決定が要る：recorder agent decide --kind asset …）"


def add_item(item: dict, cat: dict | None = None, path: str | Path | None = None, save: bool = True) -> dict:
    """台帳に項目を足す。decision が無い・決定の台帳に無い・deny なら拒否（ValueError）。規約違反も拒否。"""
    cat = cat if cat is not None else load_catalog(path)
    pr = decision_problem(item)
    if pr:
        raise ValueError(pr)
    if any(it.get("id") == item.get("id") for it in cat.get("items", [])):
        raise ValueError(f"アセット {item.get('id')} は台帳にすでにあります")
    trial = {**cat, "items": [*cat.get("items", []), item]}
    errs = validate_catalog(trial)
    if errs:
        raise ValueError("台帳の規約違反: " + " / ".join(errs))
    cat.setdefault("items", []).append(item)
    if save:
        save_catalog(cat, path)
    return cat


def _safe_rel(s: str) -> bool:
    """assets/ の外に出ない相対パスか（.. や絶対パスを落とす）。"""
    if not s or "\\" in s:
        return False
    pp = PurePosixPath(s)
    return not pp.is_absolute() and ".." not in pp.parts and ":" not in s


def validate_catalog(cat: dict) -> list[str]:
    """台帳の規約違反の一覧。空なら合格。CC0 以外は必ず落とす。"""
    errs: list[str] = []
    seen: set[str] = set()
    for i, it in enumerate(cat.get("items", [])):
        iid = it.get("id") or f"#{i}"
        where = f"items[{iid}]"
        if not it.get("id"):
            errs.append(f"{where}: id がありません")
        elif iid in seen:
            errs.append(f"{where}: id が重複しています")
        seen.add(iid)
        if it.get("license") not in ALLOWED_LICENSES:
            errs.append(f"{where}: license が CC0-1.0 ではありません（{it.get('license')}）。CC0 以外（CC-BY も）は入れない")
        for k in ("what", "url", "license_url", "file"):
            if not it.get(k):
                errs.append(f"{where}: {k} がありません")
        if it.get("kind") not in KINDS:
            errs.append(f"{where}: kind は {'/'.join(KINDS)} です（{it.get('kind')}）")
        if it.get("source") not in SOURCES:
            errs.append(f"{where}: source は {'/'.join(SOURCES)} です（{it.get('source')}）")
        if it.get("file") and not _safe_rel(it["file"]):
            errs.append(f"{where}: file は assets/ の中の相対パスにしてください（{it['file']}）")
        if it.get("file") and it.get("source") and not str(it["file"]).startswith(it["source"] + "/"):
            errs.append(f"{where}: file は source のフォルダ（{it['source']}/）の下に置きます")
        if it.get("kind") == "model":
            s = it.get("size_m")
            if not (isinstance(s, list) and len(s) == 3 and all(isinstance(x, (int, float)) and x > 0 for x in s)):
                errs.append(f"{where}: model には size_m [w, d, h]（m、正の数）が要ります")
            if it.get("file") and not str(it["file"]).lower().endswith(MODEL_EXT):
                errs.append(f"{where}: model の file は {'/'.join(MODEL_EXT)} です")
        if it.get("kind") == "texture":
            maps = it.get("maps") or {}
            if not maps.get("color"):
                errs.append(f"{where}: texture には maps.color が要ります")
            for k, v in maps.items():
                if not _safe_rel(v):
                    errs.append(f"{where}: maps.{k} は相対パスにしてください")
            t = it.get("tile_m")
            if not (isinstance(t, list) and len(t) == 2 and all(isinstance(x, (int, float)) and x > 0 for x in t)):
                errs.append(f"{where}: texture には tile_m [w, h]（1 枚の実寸 m）が要ります")
        for k in (it.get("sha256") or {}):
            if not _safe_rel(k):
                errs.append(f"{where}: sha256 の鍵 {k} が相対パスではありません")
    for i, nf in enumerate(cat.get("not_found", [])):
        if not (nf.get("what") and nf.get("fallback")):
            errs.append(f"not_found[{i}]: what と fallback が要ります")
    return errs


def item(cat: dict, aid: str) -> dict:
    for it in cat.get("items", []):
        if it.get("id") == aid:
            return it
    raise KeyError(f"アセット {aid} は台帳にありません")


def ids(cat: dict, kind: str | None = None) -> list[str]:
    return [it["id"] for it in cat.get("items", []) if kind is None or it.get("kind") == kind]


def resolve(aid: str, cat: dict | None = None, store: str | Path | None = None, must_exist: bool = True) -> Path:
    """アセット id → ローカルの主ファイル（model は glb/gltf/obj、texture はフォルダ、hdri は .hdr）。

    台帳の license が CC0 でなければ ValueError（取り込まない）。取得していなければ FileNotFoundError。"""
    cat = cat if cat is not None else load_catalog()
    it = item(cat, aid)
    if it.get("license") not in ALLOWED_LICENSES:
        raise ValueError(f"{aid}: license が CC0 ではないので使えません（{it.get('license')}）")
    if not _safe_rel(it.get("file", "")):
        raise ValueError(f"{aid}: file が不正です（{it.get('file')}）")
    p = (Path(store) if store else STORE) / it["file"]
    if must_exist and not p.exists():
        raise FileNotFoundError(f"{aid}: {p} がありません（python -m recorder assets fetch で取得）")
    return p


def texture_maps(aid: str, cat: dict | None = None, store: str | Path | None = None) -> dict[str, Path]:
    """texture の各画像（color / normal / rough）のパス。"""
    cat = cat if cat is not None else load_catalog()
    it = item(cat, aid)
    if it.get("kind") != "texture":
        raise ValueError(f"{aid} は texture ではありません（{it.get('kind')}）")
    folder = resolve(aid, cat, store, must_exist=False)
    return {k: folder / v for k, v in (it.get("maps") or {}).items()}


# --- 拡大率 ----------------------------------------------------------------------

def scale_factors(asset_size: list[float], part_size: list[float | None]) -> tuple[tuple[float, float, float], list[str]]:
    """アセットの実寸 [w, d, h] と部品の大きさ [w, d, h]（unknown は None）から、軸ごとの拡大率と警告。

    分かっている軸はその比。分からない軸は、分かっている軸の比の相乗平均（形を崩さない）。
    全部分からなければ 1（アセットの実寸のまま。仮なので警告）。
    拡大率が 0.5〜2 の外なら警告（アセットの選び方が違う疑い）。"""
    warns: list[str] = []
    known = [(i, float(p) / float(a)) for i, (a, p) in enumerate(zip(asset_size, part_size)) if p is not None and a]
    if not known:
        warns.append("部品の大きさが全部 unknown。アセットの実寸のまま置いた（仮）")
        fill = 1.0
    else:
        fill = math.exp(sum(math.log(f) for _, f in known) / len(known))
        if len(known) < 3:
            warns.append("大きさの一部が unknown。その軸は他の軸の比（%.2f）で揃えた（仮）" % fill)
    f = [fill, fill, fill]
    for i, v in known:
        f[i] = v
    for ax, v in zip("wdh", f):
        if not (SCALE_WARN[0] <= v <= SCALE_WARN[1]):
            warns.append(f"拡大率 {ax}={v:.2f} が {SCALE_WARN[0]}〜{SCALE_WARN[1]} の外（アセットが部品に合っていない疑い）")
    return (f[0], f[1], f[2]), warns


# --- 位置 ------------------------------------------------------------------------

def _v(m):
    if isinstance(m, (int, float)) and not isinstance(m, bool):
        return float(m)
    if isinstance(m, dict) and m.get("value") is not None and m.get("status") != "unknown":
        return float(m["value"])
    return None


def part_size(pt: dict) -> list[float | None]:
    s = pt.get("size") or {}
    return [_v(s.get(k)) for k in ("w", "d", "h")]


def placements(pt: dict, face: dict | None) -> tuple[list[tuple[float, float, float, float]], list[str]]:
    """asset 部品の置き場所の並び [(x, y, z, yaw_rad)] と警告。

    面に付く物：face の from→to に沿って along（m）、外向きに offset（m）、z0（棟の z=0 から m。無ければ面の前の地面）。
    count 個を pitch（m）おきに along の向きへ。yaw（度）は面の外向きから反時計回り（0＝外を向く）。
    単独の物：x, y（原点からの m）と z0。yaw 0 で模型の正面が南（-y）、反時計回りに度。
    along が unknown なら置かない（決めつけない）。"""
    warns: list[str] = []
    cnt = _v(pt.get("count"))
    n = int(cnt) if cnt is not None else 1
    if pt.get("count") is not None and cnt is None:
        warns.append("count が unknown。1 個だけ置いた（仮）")
    pitch = _v(pt.get("pitch"))
    if n > 1 and pitch is None:
        warns.append("count が 2 以上なのに pitch が unknown。置かない")
        return [], warns
    yaw = math.radians(_v(pt.get("yaw")) or 0.0)
    z0 = _v(pt.get("z0"))
    if pt.get("face"):
        if not face or not face.get("from") or not face.get("to"):
            return [], [f"face={pt.get('face')} の from/to がありません"]
        a = _v(pt.get("along"))
        if a is None:
            return [], ["along が unknown。置かない（撮影依頼へ）"]
        off = _v(pt.get("offset")) or 0.0
        (x0, y0), (x1, y1) = face["from"], face["to"]
        L = math.hypot(x1 - x0, y1 - y0)
        ux, uy = (x1 - x0) / L, (y1 - y0) / L
        nx, ny = uy, -ux                         # 外向き（blender_build.face_frame と同じ）
        base = math.atan2(nx, -ny)               # 模型の正面（glTF の +Z → Blender の -Y）を外向き n へ回す角
        if z0 is None:
            z0 = _v(face.get("ground_offset")) or 0.0
        out = []
        for i in range(n):
            ai = a + i * (pitch or 0.0)
            if ai < -0.01 or ai > L + 0.01:
                warns.append(f"{i} 個目の along={ai:.2f} m が面の長さ {L:.2f} m の外")
            out.append((x0 + ux * ai + nx * off, y0 + uy * ai + ny * off, z0, base + yaw))
        return out, warns
    x, y = _v(pt.get("x")), _v(pt.get("y"))
    if x is None or y is None:
        return [], ["face も x/y も無いので置けない"]
    return [(x + i * (pitch or 0.0), y, z0 or 0.0, yaw) for i in range(n)], warns


# --- glTF の実寸を測る ------------------------------------------------------------

def _mat_mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _node_matrix(nd: dict):
    if "matrix" in nd:
        m = nd["matrix"]
        return [[m[c * 4 + r] for c in range(4)] for r in range(4)]   # 列優先
    t = nd.get("translation", [0, 0, 0])
    qx, qy, qz, qw = nd.get("rotation", [0, 0, 0, 1])
    s = nd.get("scale", [1, 1, 1])
    r = [[1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
         [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
         [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)]]
    return [[r[i][0] * s[0], r[i][1] * s[1], r[i][2] * s[2], t[i]] for i in range(3)] + [[0, 0, 0, 1]]


def gltf_size(path: str | Path, node: str | None = None) -> list[float]:
    """.gltf（JSON）の外形 [w, d, h] m。accessor の min/max の 8 隅を節の変換で動かして測る。
    node を渡すとその名前の最上位の節だけ（1 つの gltf に色違いが並んでいる物の 1 つ分）。
    glTF は Y が上なので w=x、d=z、h=y（Blender に入れると x, -z→y, y→z）。"""
    g = json.loads(Path(path).read_text(encoding="utf-8"))
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    I = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]

    def visit(ni: int, parent):
        nd = g["nodes"][ni]
        m = _mat_mul(parent, _node_matrix(nd))
        if "mesh" in nd:
            for prim in g["meshes"][nd["mesh"]]["primitives"]:
                acc = g["accessors"][prim["attributes"]["POSITION"]]
                a0, a1 = acc["min"], acc["max"]
                for cx in (a0[0], a1[0]):
                    for cy in (a0[1], a1[1]):
                        for cz in (a0[2], a1[2]):
                            p = [sum(m[i][k] * v for k, v in enumerate((cx, cy, cz, 1.0))) for i in range(3)]
                            for i in range(3):
                                lo[i], hi[i] = min(lo[i], p[i]), max(hi[i], p[i])
        for c in nd.get("children", []):
            visit(c, m)

    scene = g["scenes"][g.get("scene", 0)]
    roots = [ni for ni in scene["nodes"] if node is None or g["nodes"][ni].get("name") == node]
    if not roots:
        raise KeyError(f"節 {node} がありません")
    for ni in roots:
        visit(ni, I)
    return [round(hi[0] - lo[0], 3), round(hi[2] - lo[2], 3), round(hi[1] - lo[1], 3)]


# --- 取得 ------------------------------------------------------------------------

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _get(url: str, timeout: int = 120) -> bytes:
    if not url.startswith("https://"):
        raise ValueError(f"https 以外は取りに行きません: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _download(url: str, dest: Path, md5: str | None = None) -> None:
    data = _get(url)
    if md5 and hashlib.md5(data).hexdigest() != md5:
        raise ValueError(f"md5 が合いません: {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, dest)


TEXTURE_MAPS = {"color": "Diffuse", "normal": "nor_gl", "rough": "Rough"}


def polyhaven_plan(it: dict, files: dict) -> list[tuple[str, str, str | None]]:
    """Poly Haven の /files の答えから、取る物 [(url, 置き場所 assets/ からの相対, md5)]。"""
    res = (it.get("polyhaven") or {}).get("res", "1k")
    folder = str(PurePosixPath(it["file"]).parent) if it["kind"] != "texture" else it["file"]
    plan: list[tuple[str, str, str | None]] = []
    if it["kind"] == "model":
        g = files["gltf"][res]["gltf"]
        plan.append((g["url"], it["file"], g.get("md5")))
        for rel, inc in (g.get("include") or {}).items():
            plan.append((inc["url"], f"{folder}/{rel}", inc.get("md5")))
    elif it["kind"] == "texture":
        for k, key in TEXTURE_MAPS.items():
            f = files[key][res]["jpg"]
            plan.append((f["url"], f"{folder}/{it['maps'][k]}", f.get("md5")))
    elif it["kind"] == "hdri":
        f = files["hdri"][res]["hdr"]
        plan.append((f["url"], it["file"], f.get("md5")))
    for _u, rel, _m in plan:
        if not _safe_rel(rel) or not rel.startswith("polyhaven/"):
            raise ValueError(f"置き場所が不正です: {rel}")
    return plan


def _extract_member(zpath: Path, member: str, dest_dir: Path) -> list[Path]:
    """zip から member（フォルダの接頭辞でも可）だけを取り出す。.. や絶対パスは無視する。"""
    out = []
    with zipfile.ZipFile(zpath) as z:
        for n in z.namelist():
            if n.endswith("/") or not (n == member or n.startswith(member.rstrip("/") + "/")):
                continue
            if not _safe_rel(n):
                continue
            target = dest_dir / PurePosixPath(n).name
            with z.open(n) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            out.append(target)
    return out


def fetch(cat: dict, only: list[str] | None = None, store: str | Path | None = None, log=print) -> dict:
    """台帳の物を assets/ に取る。取れた物の sha256 と fetched_at と（model なら）測った実寸を台帳に書く。
    すでにあって sha256 が台帳と同じ物は取り直さない（差分だけ）。返り値 {"ok": [...], "skip": [...], "fail": {id: 理由}}。"""
    store = Path(store) if store else STORE
    res = {"ok": [], "skip": [], "fail": {}}
    errs = validate_catalog(cat)
    if errs:
        raise ValueError("台帳の規約違反: " + " / ".join(errs))
    for it in cat.get("items", []):
        aid = it["id"]
        if only and aid not in only:
            continue
        try:
            pr = decision_problem(it)
            if pr:
                raise PermissionError(pr)   # 取りに行く前に拒否
            have = it.get("sha256") or {}
            if have and all((store / rel).exists() and sha256(store / rel) == h for rel, h in have.items()):
                res["skip"].append(aid)
                continue
            if it["source"] == "polyhaven":
                ph = it.get("polyhaven") or {}
                files = json.loads(_get(f"https://api.polyhaven.com/files/{ph['id']}"))
                got = {}
                for url, rel, md5 in polyhaven_plan(it, files):
                    p = store / rel
                    if not (p.exists() and md5 and hashlib.md5(p.read_bytes()).hexdigest() == md5):
                        _download(url, p, md5)
                    got[rel] = sha256(p)
            elif it["source"] == "kenney":
                kz = it.get("kenney") or {}
                zpath = store / "kenney" / kz["zip"]
                if not zpath.exists():
                    raise FileNotFoundError(f"{zpath} がありません。{it['url']} から zip を手で落として置いてください"
                                            "（Kenney は署名つきの一時リンクなので自動では取りません）")
                dest = (store / it["file"]).parent
                dest.mkdir(parents=True, exist_ok=True)
                got = {str(p.relative_to(store)).replace("\\", "/"): sha256(p) for p in _extract_member(zpath, kz["member"], dest)}
                if not got:
                    raise FileNotFoundError(f"zip の中に {kz['member']} がありません")
            else:
                raise ValueError(f"source {it['source']} の取り方を知りません")
            it["sha256"] = got
            it["fetched_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            main = store / it["file"]
            if it["kind"] == "model" and main.suffix.lower() == ".gltf":
                measured = gltf_size(main, it.get("node"))
                if any(abs(m - s) > max(0.05, 0.05 * s) for m, s in zip(measured, it["size_m"])):
                    log(f"   注意 {aid}: 測った外形 {measured} が台帳の size_m {it['size_m']} と違う（測った方を size_m にする）")
                it["size_m"] = measured
                it["size_from"] = "取得した gltf の外形を測った値（recorder.assets.gltf_size）"
            res["ok"].append(aid)
            log(f"取得 {aid}: {len(got)} ファイル")
        except Exception as e:  # noqa: BLE001  1 件の失敗で全体を止めない
            res["fail"][aid] = str(e)
            log(f"失敗 {aid}: {e}")
    return res
