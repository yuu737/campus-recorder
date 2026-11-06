"""写真の取り込み。

スマホの写真から EXIF（撮影日時・GPS・方位・焦点距離）を読み、`data/photos/index.json` に索引を作る。
写真そのものは git に入れない（CLAUDE.md の 6）。索引だけ入れる。

索引の1件：
    {"id": "IMG_1234", "file": "20261103/IMG_1234.jpg", "taken_at": "...",
     "gps": {"lat":..,"lon":..,"alt":..,"x":..,"y":..} | null,
     "heading_deg": 123.0 | null,          # 真北から時計回り（EXIF の GPSImgDirection）
     "focal_mm": 5.1, "focal_35mm": 24, "width": 4032, "height": 3024,
     "checks": {...},                       # 速い返事の材料（使えるか、何が足りないか）
     "candidates": [{"building": "honbu", "distance_m": 32.1, "bearing_diff_deg": 8.0}, ...],
     "building": null, "face": null,       # 確定は職員（棟）と仕分け係（面）。ここでは決めつけない
     # 職員の振り分け（assign_photos）。どれも無くても読める
     "assigned_by": "staff"|"shiwake"|"gps"|"台帳"|null,
     "interior": false, "floor": null,     # 内装の時だけ floor（int）
     "assigned_at": "2026-11-05T10:00:00"}

決まり：
    GPS と方位から「どの棟のどの面か」を**候補**として並べるだけで、確定しない。
    候補は、カメラ位置から 120 m 以内で、向いている方向 ±45° に足元がある棟。
"""

from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any

from . import geo, site

INDEX_SCHEMA = "campus-recorder/photos/1"
PHOTO_DIR = Path("data/photos")
INDEX_PATH = PHOTO_DIR / "index.json"
EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp"}


# --- EXIF ---------------------------------------------------------------------

def _rational(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _dms(v, ref: str | None) -> float | None:
    try:
        d, m, s = (float(x) for x in v)
    except (TypeError, ValueError):
        return None
    deg = d + m / 60.0 + s / 3600.0
    if ref in ("S", "W"):
        deg = -deg
    return deg


def read_exif(path: Path) -> dict:
    """1枚の写真から撮影情報を読む。無い項目は None。"""
    from PIL import ExifTags, Image

    try:
        import pillow_heif  # noqa: F401

        pillow_heif.register_heif_opener()
    except ImportError:
        pass

    info: dict[str, Any] = {"width": None, "height": None, "taken_at": None, "make": None, "model": None,
                            "focal_mm": None, "focal_35mm": None, "gps": None, "heading_deg": None,
                            "orientation": 1, "error": None}
    try:
        im = Image.open(path)
    except Exception as e:  # noqa: BLE001
        info["error"] = f"開けません: {e}"
        return info
    info["width"], info["height"] = im.size
    try:
        ex = im.getexif()
    except Exception as e:  # noqa: BLE001
        info["error"] = f"EXIF が読めません: {e}"
        return info
    base = ExifTags.Base
    info["make"] = ex.get(base.Make)
    info["model"] = ex.get(base.Model)
    info["orientation"] = int(ex.get(base.Orientation, 1) or 1)
    sub = ex.get_ifd(ExifTags.IFD.Exif)
    dt = sub.get(base.DateTimeOriginal) or ex.get(base.DateTime)
    if dt:
        try:
            info["taken_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.strptime(str(dt), "%Y:%m:%d %H:%M:%S"))
        except ValueError:
            info["taken_at"] = str(dt)
    info["focal_mm"] = _rational(sub.get(base.FocalLength))
    f35 = sub.get(base.FocalLengthIn35mmFilm)
    info["focal_35mm"] = float(f35) if f35 else None
    g = ex.get_ifd(ExifTags.IFD.GPSInfo)
    if g:
        G = ExifTags.GPS
        lat = _dms(g.get(G.GPSLatitude), g.get(G.GPSLatitudeRef))
        lon = _dms(g.get(G.GPSLongitude), g.get(G.GPSLongitudeRef))
        if lat is not None and lon is not None and lat == lat and lon == lon and (lat, lon) != (0.0, 0.0):  # NaN は未測位
            x, y = geo.lonlat_to_xy(lon, lat)
            alt = _rational(g.get(G.GPSAltitude))
            info["gps"] = {"lat": round(lat, 7), "lon": round(lon, 7), "alt": alt,
                           "x": round(x, 1), "y": round(y, 1),
                           "hdop": _rational(g.get(G.GPSDOP))}
        hd = _rational(g.get(G.GPSImgDirection))
        if hd is not None and hd == hd:  # NaN（Pixel は位置情報オフでも空の GPS 欄を書く）は無し扱い
            ref = g.get(G.GPSImgDirectionRef)
            info["heading_deg"] = round(hd, 1)
            info["heading_ref"] = "magnetic" if ref == "M" else "true"
    return info


# --- 速い返事の材料 -----------------------------------------------------------------

def quick_checks(path: Path, info: dict) -> dict:
    """数秒で分かる「この写真は使えるか」。判定は材料だけ出し、文章は Claude（か人）が作る。"""
    import numpy as np

    c: dict[str, Any] = {}
    c["has_gps"] = info["gps"] is not None
    c["has_heading"] = info["heading_deg"] is not None
    c["has_focal"] = info["focal_35mm"] is not None or info["focal_mm"] is not None
    c["resolution_ok"] = bool(info["width"] and info["height"] and min(info["width"], info["height"]) >= 1500)
    try:
        import cv2

        from PIL import Image, ImageOps

        im = np.asarray(ImageOps.exif_transpose(Image.open(path)).convert("L"))
        h, w = im.shape[:2]
        s = 1200.0 / max(h, w)
        small = cv2.resize(im, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else im
        c["sharpness"] = round(float(cv2.Laplacian(small, cv2.CV_64F).var()), 1)  # 100 未満はぶれの疑い
        c["brightness"] = round(float(small.mean()), 1)                            # 0-255
        c["dark_ratio"] = round(float((small < 20).mean()), 3)
        c["bright_ratio"] = round(float((small > 235).mean()), 3)
        # 端が切れていないかの目安：上端・左右端に強いエッジがどれだけ張り付いているか
        edges = cv2.Canny(small, 80, 160)
        c["edge_touch_top"] = round(float(edges[:8, :].mean() / 255), 3)
        c["edge_touch_left"] = round(float(edges[:, :8].mean() / 255), 3)
        c["edge_touch_right"] = round(float(edges[:, -8:].mean() / 255), 3)
    except Exception as e:  # noqa: BLE001
        c["error"] = str(e)
    c["blur_suspected"] = c.get("sharpness") is not None and c["sharpness"] < 100
    return c


# --- 棟の候補 -----------------------------------------------------------------------

DECLINATION_DEG = -8.0   # 名古屋付近の磁気偏角（磁北は真北の約8°西）。スマホの方位は磁北基準が多い


def true_heading(info: dict) -> float | None:
    """EXIF の方位を真北基準に直す。磁北基準なら偏角を足す。"""
    hd = info.get("heading_deg")
    if hd is None:
        return None
    if info.get("heading_ref", "magnetic") == "magnetic":
        hd = hd + DECLINATION_DEG
    return hd % 360


def half_fov_deg(info: dict, margin: float = 5.0) -> float:
    """横方向の画角の半分（度）。35mm 換算の焦点距離から。無ければ 45°。
    Pixel の超広角 14mm なら約 52°、標準 25mm なら 36°。少し余裕を足す。"""
    f35 = info.get("focal_35mm")
    if not f35:
        return 45.0
    w, h = info.get("width") or 4, info.get("height") or 3
    long_side = 36.0 if w >= h else 24.0   # 横長なら 36mm 側、縦長なら 24mm 側が横方向
    return math.degrees(math.atan(long_side / 2 / f35)) + margin


def _bearing(dx: float, dy: float) -> float:
    return (math.degrees(math.atan2(dx, dy)) + 360) % 360


def _ang_diff(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def building_candidates(info: dict, site: dict, max_dist: float = 120.0, half_fov: float | None = None) -> list[dict]:
    """GPS と方位から、写っていそうな棟を並べる（確定しない）。

    足元の頂点のどれかが画角（方位 ± 半画角）に入っていれば候補。中心の方位だけで判定すると、
    近くの大きな棟（画面いっぱいに写る物）を落としてしまうため。"""
    if not info.get("gps"):
        return []
    from shapely.geometry import MultiPolygon, Point, Polygon

    cam = Point(info["gps"]["x"], info["gps"]["y"])
    heading = true_heading(info)
    hf = half_fov if half_fov is not None else half_fov_deg(info)
    out = []
    for b in site.get("buildings", []):
        rings = b.get("footprint_osm") or []
        polys = [Polygon(r) for r in rings if len(r) >= 3]
        if not polys:
            continue
        shape = MultiPolygon(polys).buffer(0)
        dist = shape.distance(cam)
        if dist > max_dist:
            continue
        c = shape.centroid
        bearing = _bearing(c.x - cam.x, c.y - cam.y)
        diff = None
        in_view = None
        if heading is not None:
            diff = _ang_diff(bearing, heading)
            verts = [v for r in rings for v in r]
            in_view = any(_ang_diff(_bearing(vx - cam.x, vy - cam.y), heading) <= hf for vx, vy in verts) or dist <= 5
            if not in_view:
                continue
        out.append({"building": b["id"], "name": b.get("name"), "distance_m": round(dist, 1),
                    "bearing_deg": round(bearing, 1), "bearing_diff_deg": round(diff, 1) if diff is not None else None})
    out.sort(key=lambda r: (r["distance_m"], r["bearing_diff_deg"] if r["bearing_diff_deg"] is not None else 999))
    return out


def footprint_faces(building: dict) -> list[dict]:
    """足元の輪郭の辺を「面」として並べる。外向きの法線の方位・長さ・両端の座標。
    面 id は f0, f1, …（輪郭の順）。棟 JSON の面 id はこれを元に人か測量係が付ける。"""
    from shapely.geometry import Polygon
    from shapely.geometry.polygon import orient

    faces = []
    for ri, ring in enumerate(building.get("footprint_osm") or []):
        if len(ring) < 3:
            continue
        poly = orient(Polygon(ring), sign=1.0)   # 反時計回りにそろえる（外向き法線＝進行方向の右）
        pts = list(poly.exterior.coords)[:-1]
        for i in range(len(pts)):
            (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % len(pts)]
            dx, dy = x1 - x0, y1 - y0
            L = math.hypot(dx, dy)
            if L < 1.0:
                continue
            normal = _bearing(dy, -dx)   # 反時計回りの辺の右手＝外側
            faces.append({"face": f"f{len(faces)}", "ring": ri, "p0": [round(x0, 1), round(y0, 1)], "p1": [round(x1, 1), round(y1, 1)],
                          "length_m": round(L, 1), "normal_deg": round(normal, 1)})
    return faces


def face_candidates(info: dict, building: dict, max_dist: float = 80.0) -> list[dict]:
    """1枚の写真に写っていそうな面を並べる（確定しない）。
    条件：面の中点がカメラから max_dist 以内、面がカメラ側を向いている（法線とカメラ方向の角 < 80°）、
    面の中点が画角に入っている。斜めから見る角度（obliquity）も出す。0° が真正面。"""
    if not info.get("gps"):
        return []
    cx, cy = info["gps"]["x"], info["gps"]["y"]
    heading = true_heading(info)
    hf = half_fov_deg(info)
    out = []
    for f in footprint_faces(building):
        mx, my = (f["p0"][0] + f["p1"][0]) / 2, (f["p0"][1] + f["p1"][1]) / 2
        d = math.hypot(mx - cx, my - cy)
        if d > max_dist:
            continue
        to_cam = _bearing(cx - mx, cy - my)
        obliq = _ang_diff(to_cam, f["normal_deg"])
        if obliq >= 80:
            continue
        rec = {**f, "distance_m": round(d, 1), "obliquity_deg": round(obliq, 1), "in_view": None}
        if heading is not None:
            rec["in_view"] = _ang_diff(_bearing(mx - cx, my - cy), heading) <= hf
            if not rec["in_view"]:
                continue
        out.append(rec)
    out.sort(key=lambda r: (r["obliquity_deg"], r["distance_m"]))
    return out


# --- 索引 -----------------------------------------------------------------------------

def load_index(path: Path = INDEX_PATH) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"schema": INDEX_SCHEMA, "note": "写真本体は git に入れない。確定した棟・面は人か測量係が書く", "photos": []}


def save_index(idx: dict, path: Path = INDEX_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")


# --- 期間（期間前 / 期間中）と台帳 -----------------------------------------------------

PERIOD_START = "2026-11-02T12:00:00"   # ハッカソン開始（JST）。撮影日時はスマホの現地時刻なのでそのまま比べる
LEDGER_SOURCE = "台帳(期間前)"

def campus_aliases(campus: str | None = None) -> dict[str, str]:
    """棟の呼び名（別名）→ 棟 id。表は site.campus_aliases が持つ（無ければ空）。"""
    fn = getattr(site, "campus_aliases", None)
    if fn is None:
        return {}
    try:
        return dict(fn(campus) or {})
    except Exception:
        return {}


def period_of(taken_at: str | None) -> str | None:
    """撮影日時（"YYYY-MM-DDTHH:MM:SS"）から期間前 / 期間中を決める。日時が無ければ None。"""
    if not taken_at or len(taken_at) < 10:
        return None
    t = taken_at.strip().replace(" ", "T")
    # EXIF の生の形（"2026:09:07 17:36:24"）が来ても日付の区切りを直してから比べる（文字列の比較なので ":" のままだと誤る）
    if len(t) >= 10 and t[4] == ":" and t[7] == ":":
        t = t[:4] + "-" + t[5:7] + "-" + t[8:]
    if len(t) == 10:
        t += "T00:00:00"
    return "before" if t < PERIOD_START else "during"


def _norm_date(s: str | None) -> str | None:
    """台帳の "2026:09:07 17:36:24" を "2026-09-07T17:36:24" にする。"""
    if not s:
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.strptime(str(s), "%Y:%m:%d %H:%M:%S"))
    except ValueError:
        return None


def ledger_building(name: str | None, campus: str | None = None) -> str | None:
    """場所名から棟 id。対応表の語が 2 つ以上別の棟に当たる（「図書館・11号館間」など）なら決めず None。"""
    if not name:
        return None
    hits = {b for k, b in campus_aliases(campus).items() if k in name}
    return hits.pop() if len(hits) == 1 else None


def load_ledger(path: Path) -> dict[str, dict]:
    """写真台帳.json を読み、台帳のあるフォルダからの相対パス（/ 区切り）→ 台帳の1件 の辞書にする。"""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(rows, dict):
        rows = next((v for v in rows.values() if isinstance(v, list)), [])
    return {str(r["path"]).replace("\\", "/"): r for r in rows if r.get("path")}


def ledger_fields(row: dict) -> dict:
    """台帳の1件から索引に入れる項目。値に source を付け、台帳に無い面は null。"""
    folder = str(row.get("folder") or "")
    place = row.get("location") or folder.rsplit("/", 1)[-1] or None
    return {
        "building": ledger_building(f"{folder} {row.get('location') or ''}"),
        "face": row.get("face"),   # 台帳には面が無い。あれば使う
        "ledger_subject": place,
        "ledger": {
            "id": row.get("id"), "subject": row.get("subject"), "folder": folder or None,
            "classification": row.get("classification"), "evidence": row.get("evidence"),
            "sha256": row.get("sha256"), "taken_at": _norm_date(row.get("takenAt")),
            "source": LEDGER_SOURCE,
        },
    }


def _iter_images(folder: Path, ledger: dict[str, dict] | None = None):
    """台帳があれば台帳に載っている写真だけ（閲覧用のサムネイル等を拾わない）。無ければ下位フォルダも含めて全部。"""
    if ledger is not None:
        for rel in sorted(ledger):
            f = folder / rel
            if f.is_file() and f.suffix.lower() in EXTS:
                yield f
        return
    for f in sorted(folder.rglob("*")):
        if f.is_file() and f.suffix.lower() in EXTS:
            yield f


def import_photos(folder: Path, site: dict | None = None, index_path: Path = INDEX_PATH,
                  root: Path = PHOTO_DIR, period: str | None = None,
                  ledger: dict[str, dict] | None = None) -> tuple[dict, list[dict]]:
    """フォルダ（下位フォルダも）の写真を索引に足す。既にある id は EXIF と checks だけ更新し、人が入れた building / face は残す。

    period: "before" / "during"。省略すると撮影日時（EXIF、無ければ台帳、ファイル名）から決める。
    ledger: load_ledger の結果。台帳にある写真には building / face の候補を source 付きで入れる。
    フォルダ外の絶対パスの写真は file に絶対パスで書く（data/photos/ にはコピーしない）。"""
    if period not in (None, "before", "during"):
        raise ValueError("period は before か during")
    idx = load_index(index_path)
    by_id = {p["id"]: p for p in idx["photos"]}
    added = []
    for f in _iter_images(Path(folder), ledger):
        info = read_exif(f)
        pid = f.stem
        try:
            rel = str(f.relative_to(root)).replace("\\", "/")
        except ValueError:
            rel = str(f.resolve()).replace("\\", "/")
        row = (ledger or {}).get(f.relative_to(folder).as_posix())
        rec = by_id.get(pid) or {"id": pid, "building": None, "face": None, "notes": ""}
        taken = info["taken_at"] or (_norm_date(row.get("takenAt")) if row else None)
        if taken is None:
            m = re.search(r"(20\d{2})(\d{2})(\d{2})", f.name)
            taken = f"{m[1]}-{m[2]}-{m[3]}" if m else None
        if taken is None:   # 日付の無い画像（利用者の指定図など）は置いてあるフォルダ名の日付
            m = re.search(r"_(20\d{2})(\d{2})(\d{2})", str(Path(folder).name))
            taken = f"{m[1]}-{m[2]}-{m[3]}" if m else None
        rec.update({
            "file": rel,
            "taken_at": info["taken_at"] or taken,
            "period": period or period_of(taken),
            "camera": {"make": info["make"], "model": info["model"]},
            "width": info["width"], "height": info["height"], "orientation": info["orientation"],
            "focal_mm": info["focal_mm"], "focal_35mm": info["focal_35mm"],
            "gps": info["gps"], "heading_deg": info["heading_deg"], "heading_ref": info.get("heading_ref"),
            "checks": quick_checks(f, info),
            "candidates": building_candidates(info, site) if site else [],
            "imported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        if row:
            lf = ledger_fields(row)
            rec["ledger_subject"], rec["ledger"] = lf["ledger_subject"], lf["ledger"]
            # 人や測量係が入れた値は上書きしない。台帳由来の値だけ source を付けて入れる
            if rec.get("building") is None and lf["building"]:
                rec["building"], rec["building_source"] = lf["building"], LEDGER_SOURCE
            if rec.get("face") is None and lf["face"]:
                rec["face"], rec["face_source"] = lf["face"], LEDGER_SOURCE
            rec["source"] = LEDGER_SOURCE
        if info["error"]:
            rec["error"] = info["error"]
        if pid not in by_id:
            idx["photos"].append(rec)
            by_id[pid] = rec
            added.append(rec)
    save_index(idx, index_path)
    return idx, added


def period_counts(idx: dict) -> dict:
    """索引の件数（期間別、棟別、台帳あり）。"""
    out: dict[str, Any] = {"before": 0, "during": 0, "unknown": 0, "ledger": 0, "by_building": {}}
    for r in idx["photos"]:
        pr = r.get("period") or period_of(r.get("taken_at")) or "unknown"
        out[pr] += 1
        out["ledger"] += 1 if r.get("ledger") else 0
        k = f"{pr}/{r.get('building')}"
        out["by_building"][k] = out["by_building"].get(k, 0) + 1
    return out


def summary_line(rec: dict) -> str:
    """1枚ぶんの短い報告（速い返事の下書き。最終文は Claude が整える）。"""
    c = rec.get("checks", {})
    parts = [rec["id"]]
    parts.append("GPS あり" if c.get("has_gps") else "GPS なし")
    parts.append("方位あり" if c.get("has_heading") else "方位なし")
    if c.get("blur_suspected"):
        parts.append(f"ぶれの疑い(鮮明度 {c.get('sharpness')})")
    if not c.get("resolution_ok"):
        parts.append("解像度が低い")
    if rec.get("candidates"):
        top = rec["candidates"][0]
        parts.append(f"候補: {top.get('name') or top['building']}（{top['distance_m']} m）")
    return "  ".join(parts)


# --- 職員の振り分け -------------------------------------------------------------------

GPS_MISMATCH_M = 15.0   # 職員の棟と最も近い GPS 候補の棟が違い、その候補との距離差がこれ以上なら警告


def assign_photos(index_path: Path, assignments: list[dict], campus: str | None = None,
                  now: str | None = None) -> dict:
    """職員が選んだ「棟・外観/内装・階」を索引に書く。職員の値が勝つ。GPS は検算（警告だけ、上書きしない）。

    assignments の1件：{"id" か "file"(basename), "building"(棟 id か別名), "interior": bool, "floor": int|None}
    戻り値：{"assigned", "interior", "warnings", "errors", "not_found"}"""
    now = now or time.strftime("%Y-%m-%dT%H:%M:%S")
    idx = load_index(index_path)
    photos = idx["photos"]
    by_id = {p["id"]: p for p in photos}
    by_base: dict[str, list[dict]] = {}
    for p in photos:
        by_base.setdefault(Path(str(p.get("file") or "")).name, []).append(p)
    aliases = campus_aliases(campus)
    known = set(aliases.values())
    out = {"assigned": 0, "interior": 0, "unchanged": 0, "warnings": [], "errors": [], "not_found": []}
    for a in assignments:
        key = a.get("id") or a.get("file")
        if a.get("id"):
            targets = [by_id[a["id"]]] if a["id"] in by_id else []
        elif a.get("file"):
            targets = by_base.get(Path(str(a["file"]).replace("\\", "/")).name, [])
        else:
            out["errors"].append({"item": a, "error": "id か file が無い"})
            continue
        if not targets:
            out["not_found"].append(key)
            continue
        raw = str(a.get("building") or "").strip()
        bid = aliases.get(raw) or (raw if raw in known else None)
        if bid is None:
            out["errors"].append({"id": key, "building": raw, "error": "棟の名前を id に直せない"})
            continue
        interior = bool(a.get("interior"))
        floor = a.get("floor")
        if interior and floor is not None:
            try:
                floor = int(floor)
            except (TypeError, ValueError):
                out["errors"].append({"id": key, "floor": floor, "error": "階が整数でない"})
                continue
        for p in targets:
            if (p.get("assigned_by") == "staff" and p.get("building") == bid
                    and bool(p.get("interior")) == interior and (p.get("floor") if interior else None) == floor):
                out["unchanged"] += 1
                continue
            cands = p.get("candidates") or []
            if cands:
                top = min(cands, key=lambda c: c.get("distance_m", 1e9))
                if top.get("building") != bid:
                    mine = next((c for c in cands if c.get("building") == bid), None)
                    diff = (mine["distance_m"] - top["distance_m"]) if mine else None
                    if diff is None or diff >= GPS_MISMATCH_M:
                        out["warnings"].append({"id": p["id"], "staff": bid, "gps": top.get("building"),
                                                "distance_m": top.get("distance_m")})
            p["building"] = bid
            p["assigned_by"] = "staff"
            p["assigned_at"] = now
            p["building_source"] = "staff"
            p["interior"] = interior
            if interior:
                p["floor"] = floor
                p["face"] = None
                p["interior_views"] = True
                out["interior"] += 1
            else:
                p["floor"] = None
            note = f"職員が振り分け（{now}）"
            p["notes"] = f"{p['notes']} / {note}" if p.get("notes") else note
            out["assigned"] += 1
    save_index(idx, index_path)
    return out


# --- 職員がフォルダで振り分ける ---------------------------------------------------------

_FLOOR_RE = re.compile(r"(?:^|[^0-9])(\d{1,2})\s*(?:階|[fF]\b)")


def parse_folder_label(label: str, aliases: dict[str, str]) -> dict:
    """職員が付けたフォルダ名（例「タワー」「共通講義棟南」「主に図書館and本部棟と図書館の間」）を読む。

    別名を長い物から順に探し、見つかった位置の順に棟 id を返す（同じ id は 1 回）。
    「内装」か「n 階 / nF」があれば内装。棟が 2 つ以上なら決めず、候補として残す。
    戻り値：{"ids": [...], "interior": bool, "floor": int|None}"""
    rest = label
    found: list[tuple[int, str]] = []
    table = dict(aliases)
    for bid in list(aliases.values()):
        table.setdefault(bid, bid)          # 棟 id そのもの（改名後の ASCII 名 "library-honbu" も読める）
    for name in sorted(table, key=len, reverse=True):
        if not name:
            continue
        pos = rest.find(name)
        while pos >= 0:
            found.append((pos, table[name]))
            rest = rest[:pos] + " " * len(name) + rest[pos + len(name):]
            pos = rest.find(name)
    ids: list[str] = []
    for _, bid in sorted(found):
        if bid not in ids:
            ids.append(bid)
    m = _FLOOR_RE.search(label)
    floor = int(m.group(1)) if m else None
    interior = ("内装" in label) or floor is not None
    return {"ids": ids, "interior": interior, "floor": floor}


def _ascii_folder(parsed: dict) -> str | None:
    """フォルダの ASCII 名（COLMAP 等が日本語の道を嫌うため）。棟が決まらなければ None（名前は変えない）。"""
    if not parsed["ids"]:
        return None
    name = "-".join(parsed["ids"])
    if parsed["interior"]:
        name += f"_{parsed['floor']}f" if parsed["floor"] is not None else "_interior"
    return name


def assign_from_folders(day_dir: Path, index_path: Path = INDEX_PATH, campus: str | None = None,
                        now: str | None = None, rename: bool = True) -> dict:
    """職員が `data/photos/<日付>/<棟名>/` に手で分けた写真を索引に反映する（staff.html を使わない振り分け方）。

    - フォルダ名を別名表で棟 id にする。1 棟 → assign_photos と同じ書き方（職員の値が勝つ）。
      2 棟以上（「主に図書館and本部棟と図書館の間」）→ 決めつけず `staff_choices` に候補を残し、棟は仕分け係が選ぶ。
      読めない名前 → 警告（そのまま）。どの場合も元の名前を `staff_label` に残す。
    - フォルダは ASCII 名（棟 id）に改名し、索引の `file` を今ある場所に合わせる（動かした写真も basename で追う）。
    戻り値：{"folders": [...], "paths_updated", "assigned", "unchanged", "choices", "warnings", "errors", "unmatched"}"""
    now = now or time.strftime("%Y-%m-%dT%H:%M:%S")
    day_dir = Path(day_dir)
    aliases = campus_aliases(campus)
    report: dict = {"folders": [], "paths_updated": 0, "assigned": 0, "unchanged": 0, "choices": 0,
                    "warnings": [], "errors": [], "unmatched": []}
    plans = []
    for d in sorted(p for p in day_dir.iterdir() if p.is_dir()):
        files = sorted(f for f in d.iterdir() if f.is_file() and f.suffix.lower() in EXTS)
        parsed = parse_folder_label(d.name, aliases)
        new_name = _ascii_folder(parsed) if rename else None
        plans.append((d, files, parsed, new_name))
        if not parsed["ids"]:
            report["warnings"].append({"folder": d.name, "warning": "棟の名前が別名表に無い（索引の staff_label にだけ残す）"})
    # 1 棟のフォルダは assign_photos で（GPS の検算もそこで）
    assignments = []
    for d, files, parsed, _ in plans:
        if len(parsed["ids"]) == 1:
            for f in files:
                assignments.append({"file": f.name, "building": parsed["ids"][0],
                                    "interior": parsed["interior"], "floor": parsed["floor"]})
    if assignments:
        r = assign_photos(index_path, assignments, campus=campus, now=now)
        report["assigned"] += r["assigned"]
        report["unchanged"] += r.get("unchanged", 0)
        report["warnings"] += r["warnings"]
        report["errors"] += r["errors"]
        report["unmatched"] += r["not_found"]
    idx = load_index(index_path)
    by_base: dict[str, list[dict]] = {}
    for p in idx["photos"]:
        by_base.setdefault(Path(str(p.get("file") or "")).name, []).append(p)
    # 候補だけ残す（2 棟以上）とフォルダ名の記録
    for d, files, parsed, new_name in plans:
        for f in files:
            for p in by_base.get(f.name, []):
                p["staff_label"] = d.name
                if len(parsed["ids"]) >= 2 and p.get("staff_choices") != parsed["ids"]:
                    p["staff_choices"] = list(parsed["ids"])
                    p["interior"] = parsed["interior"]
                    if parsed["interior"]:
                        p["floor"] = parsed["floor"]
                    note = f"職員のフォルダ「{d.name}」（棟は {'/'.join(parsed['ids'])} のどれか。仕分け係が選ぶ）"
                    p["notes"] = f"{p['notes']} / {note}" if p.get("notes") else note
                    report["choices"] += 1
        report["folders"].append({"folder": d.name, "files": len(files), "ids": parsed["ids"],
                                  "interior": parsed["interior"], "floor": parsed["floor"],
                                  "renamed_to": new_name if new_name and new_name != d.name else None})
    # 改名（同名があれば中身を合流）
    if rename:
        for d, files, parsed, new_name in plans:
            if not new_name or new_name == d.name:
                continue
            dst = day_dir / new_name
            if dst.exists():
                for f in files:
                    f.rename(dst / f.name)
                if not any(d.iterdir()):
                    d.rmdir()
            else:
                d.rename(dst)
    # 索引の file を今ある場所に合わせる（日付フォルダの下を basename で探す）
    root = index_path.parent
    where = {f.name: f for f in day_dir.rglob("*") if f.is_file() and f.suffix.lower() in EXTS}
    try:
        prefix = day_dir.relative_to(root).as_posix() + "/"
    except ValueError:
        prefix = None
    for p in idx["photos"]:
        fp = str(p.get("file") or "")
        if prefix is None or not fp.startswith(prefix):
            continue
        f = where.get(Path(fp).name)
        if f is None:
            report["unmatched"].append(p["id"])
            continue
        rel = f.relative_to(root).as_posix()
        if rel != fp:
            p["file"] = rel
            report["paths_updated"] += 1
    save_index(idx, index_path)
    return report
