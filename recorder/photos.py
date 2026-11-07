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

写真 id（11/8、F3）：
    ファイル名の幹（拡張子を除いた名前）をそのまま id にする。機種に依らない（Pixel の PXL_…、iPhone の IMG_0001、
    Galaxy の 20261107_150551、利用者が付けた日本語名）。索引に同じ id があれば、内容の sha256 で同じ写真か確かめ、
    違う写真なら `<幹>_<sha256 の先頭 8 文字>` にして分ける（iPhone の IMG_0001 は職員・端末ごとに重なるため）。
    既にある id は変えない（棟 JSON・台帳が参照している）。新しい記録には `sha256` を持たせる。

決まり：
    GPS と方位から「どの棟のどの面か」を**候補**として並べるだけで、確定しない。
    候補は、カメラ位置から 120 m 以内で、向いている方向 ±45° に足元がある棟。
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import sys
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

DECLINATION_DEG = -8.0   # 緯度経度も敷地の値も無い時だけの既定（名古屋付近。磁北は真北の約 8° 西）


def declination_gsi(lat: float, lon: float) -> float:
    """磁気偏角（度。西偏を負）の概算。国土地理院の 2020.0 年値の概算式（二次式）。
    D = 8°15.822′ + 18.462′Δφ − 7.726′Δλ + 0.007′Δφ² − 0.007′ΔφΔλ − 0.655′Δλ²（Δφ = φ − 37°、Δλ = λ − 138°、D は西偏）。
    日本の中で −5°（沖縄）〜 −10°（北海道）。精度は 0.1〜0.3° 程度で、棟の候補（目安）には足りる。"""
    dp, dl = lat - 37.0, lon - 138.0
    d_min = 495.822 + 18.462 * dp - 7.726 * dl + 0.007 * dp * dp - 0.007 * dp * dl - 0.655 * dl * dl
    return round(-d_min / 60.0, 2)


def declination_for(info: dict | None = None, site_doc: dict | None = None) -> float:
    """方位を真北に直す偏角。順に：敷地 JSON の declination_deg → 写真の GPS の緯度経度 → 敷地の原点 → 既定。"""
    sd = site_doc or {}
    if sd.get("declination_deg") is not None:
        return float(sd["declination_deg"])
    g = (info or {}).get("gps") or {}
    if g.get("lat") is not None and g.get("lon") is not None:
        return declination_gsi(float(g["lat"]), float(g["lon"]))
    o = sd.get("crs") if isinstance(sd.get("crs"), dict) else sd
    if o.get("origin_lat") is not None and o.get("origin_lon") is not None:
        return declination_gsi(float(o["origin_lat"]), float(o["origin_lon"]))
    return DECLINATION_DEG


def true_heading(info: dict, site_doc: dict | None = None) -> float | None:
    """EXIF の方位を真北基準に直す。磁北基準なら偏角（declination_for）を足す。"""
    hd = info.get("heading_deg")
    if hd is None:
        return None
    if info.get("heading_ref", "magnetic") == "magnetic":
        hd = hd + declination_for(info, site_doc)
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
    heading = true_heading(info, site)
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
    """索引を書く（一時ファイルに書いてから置き換える）。読む所から書く所までの鍵は呼ぶ側（CLI・serve）が持つ（ledger.py）。"""
    from .ledger import write_text_atomic
    write_text_atomic(path, json.dumps(idx, ensure_ascii=False, indent=1))


# --- 期間（期間前 / 期間中）と台帳 -----------------------------------------------------

# 期間の決まりは敷地 JSON の任意の項目 `photo_policy.period_start`（例 "2026-11-02T12:00:00"、撮影日時はスマホの
# 現地時刻なのでそのまま比べる）。無ければ「期間の決まり無し」で全部 during（11/8、監査 A 22）。
PERIOD_START = "2026-11-02T12:00:00"   # 名城（ハッカソンの開始）の値。参考で、既定には使わない


def period_start_of(site_doc: dict | None) -> str | None:
    """敷地 JSON の photo_policy.period_start（無ければ None＝期間の決まり無し）。"""
    return (((site_doc or {}).get("photo_policy")) or {}).get("period_start") or None
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


def period_of(taken_at: str | None, start: str | None = None) -> str | None:
    """撮影日時（"YYYY-MM-DDTHH:MM:SS"）から期間前 / 期間中を決める。日時が無ければ None。
    start（期間の始まり。period_start_of）が無ければ期間の決まり無しで during。"""
    if not taken_at or len(taken_at) < 10:
        return None
    if not start:
        return "during"
    t = taken_at.strip().replace(" ", "T")
    # EXIF の生の形（"2026:09:07 17:36:24"）が来ても日付の区切りを直してから比べる（文字列の比較なので ":" のままだと誤る）
    if len(t) >= 10 and t[4] == ":" and t[7] == ":":
        t = t[:4] + "-" + t[5:7] + "-" + t[8:]
    if len(t) == 10:
        t += "T00:00:00"
    return "before" if t < start else "during"


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


# --- 写真 id（機種に依らない）と短い名前 -----------------------------------------------------

ID_HASH_LEN = 8          # id が重なった時に付ける sha256 の先頭の文字数


def file_sha256(path: Path) -> str:
    """写真の中身の sha256（16 進）。同じ名前の別の写真を見分けるのに使う。"""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _same_photo(rec: dict, sha: str, rel: str, info: dict, root: Path) -> bool:
    """索引の記録 rec と、今の写真（sha・rel・EXIF）が同じ写真か。
    記録に sha256 があればそれで決める。無ければ（11/8 より前の記録）同じ file か、元の写真の sha256 を計算して比べる。
    元の写真が見つからない時は、撮影日時（秒まで）・機種・大きさが全部そろう時だけ同じとみなす。"""
    if rec.get("sha256"):
        return rec["sha256"] == sha
    if rec.get("file") and rec["file"] == rel:
        return True
    old = source_path(rec, root)
    if rec.get("file") and old.is_file():
        try:
            return file_sha256(old) == sha
        except OSError:
            pass
    cam = rec.get("camera") or {}
    return bool(info.get("taken_at")) and rec.get("taken_at") == info["taken_at"] \
        and cam.get("model") == info.get("model") \
        and (rec.get("width"), rec.get("height")) == (info.get("width"), info.get("height"))


def resolve_photo_id(stem: str, by_id: dict, sha: str, rel: str, info: dict, root: Path) -> tuple[str, dict | None]:
    """写真 id を決める。幹（stem）が索引に無いか、同じ写真ならそのまま。別の写真なら `<幹>_<sha256 の先頭 8>`。
    返り値 (id, 重なりの記録 or None)。接尾辞つきの id が既にあれば、それは同じ中身の写真（名前に hash が入っている）。"""
    rec = by_id.get(stem)
    if rec is None or _same_photo(rec, sha, rel, info, root):
        return stem, None
    return f"{stem}_{sha[:ID_HASH_LEN]}", {"stem": stem, "other_file": rec.get("file"),
                                            "why": "同じ名前の別の写真が索引にあるので、中身の sha256 の頭を付けた"}


_SHORT_PIXEL = re.compile(r"^PXL_\d{8}_(\d+)")


def short_name(pid: str, ascii_only: bool = False, max_bytes: int = 40) -> str:
    """写真 id の短い名前（Blender の視点名・描画のファイル名・rerun の名前）。機種に依らない。
    - Pixel の PXL_<日付>_<時刻ミリ秒>… は時刻の数字（前からの形。既にある出力の名前と合う）
    - それ以外（IMG_0001・20261107_150551・日本語名）は id をそのまま（ファイル名に使えない文字は - に）
    - ascii_only（rerun の名前）：英数字と _ 以外を落とし、落とした時は id の sha1 の頭 6 文字を付ける
    - max_bytes（UTF-8）を超えたら切って sha1 の頭 6 文字を付ける
    一意にするのは short_names（同じ短い名前になった写真には sha1 の頭を付ける）。"""
    pid = str(pid)
    tag = hashlib.sha1(pid.encode("utf-8")).hexdigest()[:6]
    m = _SHORT_PIXEL.match(pid)
    if m:
        return m[1]
    if ascii_only:
        s = re.sub(r"[^A-Za-z0-9_]+", "", pid).strip("_")
        if s != pid:
            s = f"{s}_{tag}" if s else f"photo_{tag}"
    else:
        s = re.sub(r'[\\/:*?"<>|\s.]+', "-", pid).strip("-") or f"photo_{tag}"
    if len(s.encode("utf-8")) > max_bytes:
        cut = s
        while len(cut.encode("utf-8")) > max_bytes - 7:
            cut = cut[:-1]
        s = f"{cut}_{tag}"
    return s


def short_names(pids, ascii_only: bool = False) -> dict[str, str]:
    """写真 id の並び → 短い名前（重ならない）。同じ短い名前になる写真が 2 枚以上あれば、全部に sha1 の頭 6 文字を付ける
    （並びの順に依らず同じ名前になる）。"""
    ids = list(dict.fromkeys(str(p) for p in pids))
    base = {p: short_name(p, ascii_only) for p in ids}
    count: dict[str, int] = {}
    for v in base.values():
        count[v] = count.get(v, 0) + 1
    out = {}
    for p in ids:
        v = base[p]
        if count[v] > 1:
            v = f"{v}_{hashlib.sha1(p.encode('utf-8')).hexdigest()[:6]}"
        out[p] = v
    return out


# --- 索引の差分だけを鍵の中で書く ---------------------------------------------------------------

_DROP = object()


def record_diff(before: dict | None, after: dict) -> dict:
    """1 件の記録の変わった項目だけ（消えた項目は _DROP）。"""
    before = before or {}
    d = {k: copy.deepcopy(v) for k, v in after.items() if k not in before or before[k] != v}
    d.update({k: _DROP for k in before if k not in after})
    return d


def _apply_diff(rec: dict, diff: dict) -> None:
    for k, v in diff.items():
        if v is _DROP:
            rec.pop(k, None)
        elif k == "masks":   # 縮小版・公開が書くのは自動（by auto）の箱だけ。人・係の箱は今の索引の物を残す
            rec["masks"] = [m for m in v or [] if m.get("by") == "auto"] + \
                [m for m in rec.get("masks") or [] if m.get("by") != "auto"]
        else:
            rec[k] = v


def patch_index(index_path: Path, diffs: dict[str, dict], label: str = "photos patch") -> dict:
    """鍵（ledger）を取って索引を読み直し、id ごとの差分だけ書く。重い仕事（顔の検出・縮小）は呼ぶ側が鍵の外で済ませる
    （11/8 監査 B 3、J-012 の再発を防ぐ。registry.save_registry と同じ形）。返り値は書いた後の索引。"""
    from .ledger import lock
    diffs = {k: v for k, v in diffs.items() if v}
    with lock("ledger", label=label):
        idx = load_index(index_path)
        by_id = {p["id"]: p for p in idx["photos"]}
        for pid, d in diffs.items():
            if pid in by_id:
                _apply_diff(by_id[pid], d)
        if diffs:
            save_index(idx, index_path)
    return idx


def import_photos(folder: Path, site: dict | None = None, index_path: Path = INDEX_PATH,
                  root: Path = PHOTO_DIR, period: str | None = None,
                  ledger: dict[str, dict] | None = None, send: bool = True) -> tuple[dict, list[dict]]:
    """フォルダ（下位フォルダも）の写真を索引に足す。既にある id（同じ写真）は EXIF と checks だけ更新し、人が入れた
    building / face は残す。同じ名前の別の写真は `<幹>_<sha256 の先頭 8>` の id にして分ける（resolve_photo_id、
    記録に id_collision を残す）。

    鍵：EXIF・sha256・縮小版（顔の検出）は鍵の外で済ませ、最後に鍵（ledger）を取って索引を読み直し、差分だけ書く。

    send: 係に見せる縮小版 `send_file`（索引のあるフォルダの send/<id>.jpg、長辺 1600 px、顔に灰色の箱）を作る（11/8、K-104）。
    元の写真は読むだけ。箱は索引の masks に by "auto" で残す。顔のモデルが無ければ箱なしで作り、rec["send_warning"] に残す。

    period: "before" / "during"。省略すると撮影日時（EXIF、無ければ台帳、ファイル名）と敷地 JSON の
    photo_policy.period_start から決める（決まりが無ければ during）。
    ledger: load_ledger の結果。台帳にある写真には building / face の候補を source 付きで入れる。
    フォルダ外の絶対パスの写真は file に絶対パスで書く（data/photos/ にはコピーしない）。"""
    if period not in (None, "before", "during"):
        raise ValueError("period は before か during")
    start = period_start_of(site)
    snap = load_index(index_path)
    by_id = {p["id"]: p for p in snap["photos"]}
    new: dict[str, dict] = {}       # 今回足す記録（id → 記録）
    bases: dict[str, dict] = {}     # 既にある記録の、手を付ける前の写し
    for f in _iter_images(Path(folder), ledger):
        info = read_exif(f)
        try:
            sha = file_sha256(f)
        except OSError as e:
            info["error"] = info.get("error") or f"読めない: {e}"
            sha = ""
        try:
            rel = str(f.relative_to(root)).replace("\\", "/")
        except ValueError:
            rel = str(f.resolve()).replace("\\", "/")
        pid, coll = resolve_photo_id(f.stem, by_id, sha, rel, info, Path(root)) if sha else (f.stem, None)
        row = (ledger or {}).get(f.relative_to(folder).as_posix())
        old = by_id.get(pid)
        if old is not None and pid not in new and pid not in bases:
            bases[pid] = copy.deepcopy(old)
        rec = old if old is not None else {"id": pid, "building": None, "face": None, "notes": ""}
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
            "period": period or period_of(taken, start),
            "camera": {"make": info["make"], "model": info["model"]},
            "width": info["width"], "height": info["height"], "orientation": info["orientation"],
            "focal_mm": info["focal_mm"], "focal_35mm": info["focal_35mm"],
            "gps": info["gps"], "heading_deg": info["heading_deg"], "heading_ref": info.get("heading_ref"),
            "checks": quick_checks(f, info),
            "candidates": building_candidates(info, site) if site else [],
            "imported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        if sha:
            rec["sha256"] = sha
        if coll:
            rec["id_collision"] = coll
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
        if send and not info["error"]:
            make_send_for(rec, f, Path(index_path).parent, root)
        if old is None:
            new[pid] = rec
            by_id[pid] = rec
    diffs = {pid: record_diff(base, by_id[pid]) for pid, base in bases.items()}

    # ここから鍵の中：読み直して差分だけ書く（鍵の外にいる間に他の道具が書いた項目を消さない）
    from .ledger import lock
    added: list[dict] = []
    with lock("ledger", label=f"photos import {Path(folder).name}"):
        idx = load_index(index_path)
        cur = {p["id"]: p for p in idx["photos"]}
        for pid, d in diffs.items():
            if pid in cur:
                _apply_diff(cur[pid], d)
        for pid, rec in new.items():
            ex = cur.get(pid)
            if ex is not None and ex.get("sha256") and rec.get("sha256") and ex["sha256"] != rec["sha256"]:
                # 鍵の外にいる間に、同じ名前の別の写真が入った：接尾辞の id に付け替える
                nid = f"{pid}_{rec['sha256'][:ID_HASH_LEN]}"
                _rename_send(rec, nid, Path(index_path).parent, Path(root))
                rec["id_collision"] = {"stem": pid, "other_file": ex.get("file"),
                                       "why": "同じ名前の別の写真が索引にあるので、中身の sha256 の頭を付けた"}
                rec["id"] = pid = nid
                ex = cur.get(pid)
            if ex is not None:   # 同じ写真が先に入っていた：人の値は残し、取り込みの値だけ足す
                d = record_diff({"id": pid, "building": None, "face": None, "notes": ""}, rec)
                for k in ("building", "face", "notes"):
                    if ex.get(k):
                        d.pop(k, None)
                _apply_diff(ex, d)
                continue
            idx["photos"].append(rec)
            cur[pid] = rec
            added.append(rec)
        save_index(idx, index_path)
    for r in added:
        if r.get("id_collision"):
            print(f"写真 id の重なり：{r['id_collision']['stem']} は別の写真（{r['id_collision']['other_file']}）が使っているので "
                  f"{r['id']} にした（{r['file']}）", file=sys.stderr)
    return idx, added


def _rename_send(rec: dict, nid: str, send_root: Path, photo_root: Path) -> None:
    """縮小版のファイル名を新しい id に合わせる。"""
    if not rec.get("send_file"):
        return
    old = source_path({"file": rec["send_file"]}, photo_root)
    dst = Path(send_root) / SEND_DIRNAME / f"{nid}.jpg"
    try:
        if old.exists():
            old.replace(dst)
        rec["send_file"] = _rel_or_abs(dst, photo_root)
    except OSError:
        rec.pop("send_file", None)
        rec["send_warning"] = "縮小版の名前を付け替えられなかった（photos send で作り直す）"


def period_counts(idx: dict, start: str | None = None) -> dict:
    """索引の件数（期間別、棟別、台帳あり）。記録に period が無ければ撮影日時と start（period_start_of）から。"""
    out: dict[str, Any] = {"before": 0, "during": 0, "unknown": 0, "ledger": 0, "by_building": {}}
    for r in idx["photos"]:
        pr = r.get("period") or period_of(r.get("taken_at"), start) or "unknown"
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


RULER_MARKS = {"tape_measure"}   # 索引の写真に付けられる物差しの印（data/rulers.json の id）。メジャーが壁に当てて写っている写真


def set_faces(index_path: Path, doc: dict, by: str, now: str | None = None, building: str | None = None) -> dict:
    """仕分け係の結果（out/photos/shiwake_*.json の "photos"：写真 id → {building, face, faces, interior_views,
    candidate_faces, confidence, evidence}）を索引に書く。係が自分のスクリプトで索引を書かないための道具（11/7。
    4 棟を同時に回すので、鍵は CLI が持つ。ledger.py）。

    - 書くのは face・faces・interior_views・candidate_faces・confidence と notes の 1 行（「<by>：<evidence>」）。
      メジャーが写っていれば ruler（"tape_measure"）と ruler_note（どこに当てたか・見える目盛りの範囲）も書く。
    - 棟は、索引で棟が決まっていない（null）写真にだけ書き、assigned_by を "shiwake" にする。
      職員の振り分けや、他の棟の仕分け係が先に決めた棟は変えない（skipped に理由）。staff_choices があればその中だけ。
    - 職員が内装と決めた写真（interior: true）には面を書かない。
    - J-004（11/8）：行の building が null の時は、呼び出した棟（引数 building、無ければ出力の "building"）の写真として扱う。
      索引の棟がそれと違う写真は face・faces も書かない（skipped）。呼び出した棟が分からない時は従来どおり書き、warnings に残す。
    - 職員がメジャーの枠から送った写真（ruler_by "staff"）の職員のメモは ruler_note_staff に残し、係の ruler_note はその後ろに足す（K-102）。
    戻り値：{"set", "building_set", "skipped", "not_found", "warnings"}"""
    now = now or time.strftime("%Y-%m-%dT%H:%M:%S")
    items = doc.get("photos", doc) if isinstance(doc, dict) else {}
    target = building or (doc.get("building") if isinstance(doc, dict) and isinstance(doc.get("building"), str) else None)
    idx = load_index(index_path)
    by_id = {p["id"]: p for p in idx["photos"]}
    out: dict = {"set": 0, "building_set": 0, "skipped": [], "not_found": [], "warnings": []}
    for pid, r in items.items():
        if not isinstance(r, dict):
            continue
        p = by_id.get(pid)
        if p is None:
            out["not_found"].append(pid)
            continue
        b_new = r.get("building")
        if p.get("building") and b_new and b_new != p["building"]:
            out["skipped"].append({"id": pid, "reason": f"索引の棟は {p['building']}（{p.get('assigned_by') or '?'}）。{b_new} には変えない"})
            continue
        if b_new is None and p.get("building"):
            if target and target != p["building"]:
                out["skipped"].append({"id": pid, "reason": f"行の棟が null で、呼び出した棟 {target} と索引の棟 {p['building']} が違う。面を書かない（J-004）"})
                continue
            if not target:
                out["warnings"].append({"id": pid, "warning": f"行の棟が null で呼び出した棟も無い。索引の棟 {p['building']} の写真として面を書いた（--building を付けてください）"})
        if not p.get("building") and b_new:
            ch = p.get("staff_choices")
            if ch and b_new not in ch:
                out["skipped"].append({"id": pid, "reason": f"職員が示した棟 {ch} の外（{b_new}）"})
                continue
            p["building"], p["assigned_by"], p["assigned_at"] = b_new, "shiwake", now
            out["building_set"] += 1
        if p.get("interior") is True:
            p["face"], p["interior_views"] = None, True
        else:
            for k in ("face", "faces", "interior_views", "candidate_faces"):
                if k in r:
                    p[k] = r[k]
        if "confidence" in r:
            p["confidence"] = r["confidence"]
        if r.get("ruler"):                                 # 物差しの写った写真の印（今はメジャー tape_measure）
            if r["ruler"] not in RULER_MARKS:
                out["skipped"].append({"id": pid, "reason": f"物差しの印 {r['ruler']} は {sorted(RULER_MARKS)} にない（面は書いた）"})
            else:
                p["ruler"] = r["ruler"]
                if r.get("ruler_note"):
                    staff_note = p.get("ruler_note_staff")
                    p["ruler_note"] = f"職員：{staff_note} ／ 係：{r['ruler_note']}" if staff_note else r["ruler_note"]
        if r.get("evidence"):
            note = f"{by}：{r['evidence']}"
            p["notes"] = f"{p['notes']} | {note}" if p.get("notes") else note
        out["set"] += 1
    save_index(idx, index_path)
    return out


def write_to_confirm(b: dict, root: Path = Path(".")) -> dict:
    """根拠の門で外れた写真（schema.evidence_checks）を out/<棟>/to_confirm.json に書く。職員の画面と事件の検出が読む。
    問題 1 つの写真は 1 件、複数の問題がある写真は 1 件にまとめる。"""
    from . import schema
    checks = schema.evidence_checks(b, schema.load_photo_records(root), root)
    by_photo: dict[str, dict] = {}
    for it in checks:
        cur = by_photo.setdefault(it["photo"], {**it, "problems": []})
        cur["problems"].append(it["problem"])
        for u in it["used_at"]:
            if u not in cur["used_at"]:
                cur["used_at"].append(u)
        if it["detail"] not in cur["detail"]:
            cur["detail"] = f"{cur['detail']}／{it['detail']}"
    items = list(by_photo.values())
    out = root / "out" / str(b.get("id")) / "to_confirm.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"building": b.get("id"), "made_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                               "note": "measured の根拠の写真のうち、仕分けと合わない物・確からしさ low で職員が確かめていない物。"
                                       "職員が recorder photos confirm（職員の画面の「確かめてほしい写真」）で確かめるまで、measured の根拠にしない",
                               "items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"path": str(out), "items": items}


def confirm_photo(index_path: Path, pid: str, *, by: str = "staff", building: str | None = None,
                  face: str | None = None, faces: list[str] | None = None, now: str | None = None) -> dict:
    """職員が仕分けを確かめる（11/7。根拠の門 schema.evidence_checks で「確かめてほしい」と出た写真）。
    棟・面を渡せば直してから確かめた印を付ける（直す前の値は confirm_change に残す）。渡さなければ「合っている」。
    確かめた写真は確からしさ low でも measured の根拠に使える。戻り値：{"photo", "changed", "record"}"""
    now = now or time.strftime("%Y-%m-%dT%H:%M:%S")
    idx = load_index(index_path)
    p = next((r for r in idx["photos"] if r.get("id") == pid), None)
    if p is None:
        raise KeyError(f"索引に無い写真: {pid}")
    before = {k: p.get(k) for k in ("building", "face", "faces")}
    if building is not None:
        p["building"] = building or None
        p["assigned_by"], p["assigned_at"] = by, now
    if face is not None:
        p["face"] = face or None
        p["faces"] = faces if faces is not None else ([face] if face else [])
    elif faces is not None:
        p["faces"] = faces
    after = {k: p.get(k) for k in ("building", "face", "faces")}
    changed = before != after
    p["confirmed_by"], p["confirmed_at"] = by, now
    if changed:
        p["confirm_change"] = {"from": before, "to": after, "at": now, "by": by}
    note = f"{'職員が直して確かめた' if changed else '職員が確かめた'}（{now}）"
    p["notes"] = f"{p['notes']} | {note}" if p.get("notes") else note
    save_index(idx, index_path)
    return {"photo": pid, "changed": changed, "record": p}


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


# --- 11/8（T4）：係に見せる縮小版・relink・メジャーの枠・保護係の升 ----------------------------------------

SEND_DIRNAME = "send"
REPO_ROOT = Path(__file__).resolve().parents[1]


def source_path(rec: dict, photo_root: Path = PHOTO_DIR) -> Path:
    """索引の file → 元の写真の道（絶対ならそのまま、相対なら photo_root から）。"""
    f = Path(str(rec.get("file") or ""))
    return f if f.is_absolute() else Path(photo_root) / f


def _rel_or_abs(f: Path, root: Path) -> str:
    try:
        return f.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return f.resolve().as_posix()


def make_send_for(rec: dict, src: Path, send_root: Path, photo_root: Path, force: bool = False) -> dict | None:
    """1 枚の send_file を作り、rec に send_file・auto_mask・masks（by auto だけ置き換え）を書く（索引の保存は呼ぶ側）。
    既に作ってあれば（ファイルがあり auto_mask の記録もある）何もしない。失敗しても取り込みは止めない。"""
    from . import privacy

    dst = Path(send_root) / SEND_DIRNAME / f"{rec['id']}.jpg"
    if not force and dst.exists() and rec.get("auto_mask") and rec.get("send_file"):
        return None
    others = [m for m in rec.get("masks") or [] if m.get("by") != "auto"]
    try:
        res = privacy.make_send(Path(src), dst, extra_masks=others)
    except Exception as e:  # noqa: BLE001
        rec["send_warning"] = f"縮小版を作れません：{type(e).__name__}: {e}"
        return None
    rec["send_file"] = _rel_or_abs(dst, photo_root)
    rec["auto_mask"] = res["auto_mask"]
    rec["masks"] = res["masks"] + others
    if res["warnings"]:
        rec["send_warning"] = " / ".join(res["warnings"])
    else:
        rec.pop("send_warning", None)
    return res


def redraw_send_for(rec: dict, src: Path, send_root: Path, photo_root: Path, now: str) -> None:
    """顔を探さずに send_file を作り直す（11/8 F11 追記 3）。索引の masks のうち描く物（確かめていない by: auto 以外）
    だけを描く。masks・auto_mask は触らない（古い縮小版に焼き込まれた誤検出の「顔」を消すため）。"""
    from . import privacy

    dst = Path(send_root) / SEND_DIRNAME / f"{rec['id']}.jpg"
    im = privacy.resize_long(privacy.load_oriented(Path(src)), privacy.SEND_LONG)
    privacy.save_jpeg(privacy.paint_masks(im, privacy.drawn_masks(rec.get("masks") or [])), dst, privacy.SEND_QUALITY)
    rec["send_file"] = _rel_or_abs(dst, photo_root)
    rec["send_redrawn_at"] = now
    rec.pop("send_warning", None)


def make_sends(index_path: Path = INDEX_PATH, ids: list[str] | None = None, force: bool = False,
               photo_root: Path | None = None, no_detect: bool = False) -> dict:
    """索引の写真の send_file を作る（無い物だけ。--force で全部）。取り込み済みの写真に後から作る道具。
    no_detect（--no-detect）：顔を探さず、索引の確かめた箱だけを描いて全部作り直す（masks は書かない）。
    縮小と顔の検出は鍵の外。最後に鍵を取って索引を読み直し、変わった項目だけ書く（patch_index）。"""
    photo_root = Path(photo_root or Path(index_path).parent)
    idx = load_index(index_path)
    out: dict = {"made": 0, "kept": 0, "missing": [], "faces": 0, "warnings": [], "no_detect": no_detect}
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    diffs: dict[str, dict] = {}
    for rec in idx["photos"]:
        if ids and not any(rec["id"].startswith(i) for i in ids):
            continue
        src = source_path(rec, photo_root)
        if not src.exists():
            out["missing"].append(rec["id"])
            continue
        base = copy.deepcopy(rec)
        if no_detect:
            try:
                redraw_send_for(rec, src, Path(index_path).parent, photo_root, now)
            except Exception as e:  # noqa: BLE001
                out["warnings"].append(f"{rec['id']}: 縮小版を作れません：{type(e).__name__}: {e}")
                continue
            diffs[rec["id"]] = record_diff(base, rec)
            out["made"] += 1
            continue
        res = make_send_for(rec, src, Path(index_path).parent, photo_root, force=force)
        diffs[rec["id"]] = record_diff(base, rec)
        if res is None:
            out["kept"] += 1
            if rec.get("send_warning"):
                out["warnings"].append(rec["send_warning"])
            continue
        out["made"] += 1
        out["faces"] += len(res["masks"])
        out["warnings"] += res["warnings"]
    patch_index(index_path, diffs, label="photos send")
    return out


def photo_folder(rec: dict, repo_root: Path | None = None) -> str:
    """写真の「元のフォルダ」（公開版・保護係の置き場の名前）。相対の file はその親、リポジトリの中の絶対パスは
    リポジトリからの相対の親（例 メジャー/本部棟_honbu）、それ以外は親の名前。"""
    f = Path(str(rec.get("file") or ""))
    if not f.is_absolute():
        parent = f.parent.as_posix()
        return parent if parent not in ("", ".") else "_"
    try:
        return f.parent.resolve().relative_to(Path(repo_root or REPO_ROOT).resolve()).as_posix()
    except ValueError:
        return f.parent.name or "_"


def _default_aliases(campus: str | None) -> dict[str, str]:
    """別名表（campus が無ければ既定のキャンパス）。敷地 JSON が無ければ空。"""
    if campus is None:
        try:
            campus = site.default_campus()
        except Exception:  # noqa: BLE001
            return {}
    return campus_aliases(campus)


def _image_size(f: Path) -> tuple[int, int] | None:
    from PIL import Image

    try:
        with Image.open(f) as im:
            return im.size
    except Exception:  # noqa: BLE001
        return None


def relink(folder: Path, index_path: Path = INDEX_PATH, *, assign_from_folder: bool = False,
           campus: str | None = None, aliases: dict[str, str] | None = None, photo_root: Path | None = None,
           now: str | None = None, dry_run: bool = False) -> dict:
    """索引で file が見つからない写真を、folder の下から名前で探してパスを付け直す（K-105）。

    - 名前は file の basename（無ければ id ＋拡張子）。同じ名前が 2 つ以上あれば決めずに ambiguous。
      索引に幅・高さがあれば、見つけた写真の幅・高さと合うかを見る（違えば mismatch、付け直さない）。
    - assign_from_folder：写真が入っているフォルダ名（例 本部棟_honbu）を別名表で棟 id にし、職員の申告
      （assigned_by "staff"、building_source "staff_folder"）として書く。既に職員の振り分けがあれば変えない
      （違えば conflicts）。仕分け係の棟と違えば職員の棟にし、前の面は棟が変わったので消す（changed に残す）。
      フォルダ名から棟が 1 つに決まらなければ書かない（undecided）。folder の下の写真は file が切れていなくても対象。
    戻り値：{"relinked", "ambiguous", "mismatch", "still_missing", "assigned", "unchanged", "conflicts", "changed", "undecided"}"""
    now = now or time.strftime("%Y-%m-%dT%H:%M:%S")
    folder = Path(folder)
    photo_root = Path(photo_root or Path(index_path).parent)
    if aliases is None:
        aliases = _default_aliases(campus)
    found: dict[str, list[Path]] = {}
    for f in sorted(folder.rglob("*")):
        if f.is_file() and f.suffix.lower() in EXTS:
            found.setdefault(f.name.lower(), []).append(f)
    idx = load_index(index_path)
    rep: dict = {"folder": folder.as_posix(), "relinked": [], "ambiguous": [], "mismatch": [], "still_missing": [],
                 "assigned": [], "unchanged": 0, "conflicts": [], "changed": [], "undecided": [], "dry_run": dry_run}
    here: dict[str, Path] = {}   # 写真 id → folder の下の道（assign の対象）
    for rec in idx["photos"]:
        cur = source_path(rec, photo_root)
        names = [Path(str(rec.get("file") or "")).name.lower()] if rec.get("file") else []
        names += [f"{rec['id']}{e}".lower() for e in sorted(EXTS)]
        hits: list[Path] = []
        for n in names:
            if n and n in found:
                hits = found[n]
                break
        if cur.exists():
            try:
                cur.resolve().relative_to(folder.resolve())
                here[rec["id"]] = cur
            except ValueError:
                pass
            continue
        if not hits:
            rep["still_missing"].append(rec["id"])
            continue
        if len(hits) > 1:
            rep["ambiguous"].append({"id": rec["id"], "candidates": [h.as_posix() for h in hits]})
            continue
        f = hits[0]
        if rec.get("width") and rec.get("height"):
            sz = _image_size(f)
            if sz is not None and sorted(sz) != sorted((rec["width"], rec["height"])):
                rep["mismatch"].append({"id": rec["id"], "file": f.as_posix(), "index": [rec["width"], rec["height"]], "found": list(sz)})
                continue
        new = _rel_or_abs(f, photo_root)
        rep["relinked"].append({"id": rec["id"], "from": rec.get("file"), "to": new})
        rec.setdefault("relinked_from", rec.get("file"))
        rec["file"] = new
        rec["relinked_at"] = now
        here[rec["id"]] = f
    if assign_from_folder:
        by_id = {r["id"]: r for r in idx["photos"]}
        for pid, f in here.items():
            rec = by_id[pid]
            bid, label = None, None
            for d in [f.parent, *f.parent.parents]:
                try:
                    d.resolve().relative_to(folder.resolve())
                except ValueError:
                    break
                ids = parse_folder_label(d.name, aliases)["ids"]
                if len(ids) == 1:
                    bid, label = ids[0], d.name
                    break
                if len(ids) > 1:
                    label = d.name
                    break
            if bid is None:
                rep["undecided"].append({"id": pid, "folder": f.parent.name})
                continue
            if rec.get("assigned_by") == "staff" and rec.get("building"):
                if rec["building"] == bid:
                    rep["unchanged"] += 1
                else:
                    rep["conflicts"].append({"id": pid, "index": rec["building"], "folder": bid, "note": "職員の振り分けがあるので変えない"})
                continue
            prev = {k: rec.get(k) for k in ("building", "assigned_by", "face", "faces")}
            if prev["building"] and prev["building"] != bid:
                rep["changed"].append({"id": pid, "from": prev, "to": bid})
                rec["face"], rec["faces"] = None, []
            rec["building"], rec["assigned_by"], rec["assigned_at"] = bid, "staff", now
            rec["building_source"] = "staff_folder"
            rec["staff_label"] = label
            note = f"職員のフォルダ「{label}」から棟 {bid}（relink、{now}）"
            rec["notes"] = f"{rec['notes']} / {note}" if rec.get("notes") else note
            rep["assigned"].append(pid)
    if not dry_run:
        save_index(idx, index_path)
    return rep


def mark_tape(index_path: Path, ids: list[str], building: str, *, face: str | None = None, note: str | None = None,
              campus: str | None = None, aliases: dict[str, str] | None = None, now: str | None = None) -> dict:
    """メジャーの枠（K-102）：職員が送ったメジャーの写真に、職員の申告として棟（と面）と ruler を付ける。
    棟は別名でもよい（敷地 JSON の aliases で id にする）。仕分け係は棟を変えず、面と ruler_note だけ足す（set_faces）。"""
    now = now or time.strftime("%Y-%m-%dT%H:%M:%S")
    if aliases is None:
        aliases = _default_aliases(campus)
    raw = str(building or "").strip()
    bid = aliases.get(raw) or (raw if raw in set(aliases.values()) or (not aliases and raw) else None)
    if not bid:
        raise ValueError(f"棟の名前を id に直せません: {raw!r}")
    idx = load_index(index_path)
    by_id = {p["id"]: p for p in idx["photos"]}
    out: dict = {"building": bid, "marked": [], "not_found": [], "changed": []}
    for pid in ids:
        p = by_id.get(pid)
        if p is None:
            out["not_found"].append(pid)
            continue
        if p.get("building") and p["building"] != bid:
            out["changed"].append({"id": pid, "from": p["building"], "to": bid})
            p["face"], p["faces"] = None, []
        p["building"], p["assigned_by"], p["assigned_at"], p["building_source"] = bid, "staff", now, "staff_tape"
        p["interior"], p["floor"] = False, None
        p["ruler"], p["ruler_by"] = "tape_measure", "staff"
        memo = str(note or "").strip()
        p["ruler_note_staff"] = memo or None
        p["ruler_note"] = memo or "職員がメジャーの枠から送った（メモなし）"
        if face:
            p["face_staff"] = face
            if not p.get("face"):
                p["face"], p["faces"] = face, [face]
        n = f"職員がメジャーの枠から送った（{bid}{'・' + face if face else ''}、{now}）"
        p["notes"] = f"{p['notes']} / {n}" if p.get("notes") else n
        out["marked"].append(pid)
    save_index(idx, index_path)
    return out


def set_masks(index_path: Path, doc: dict, *, by: str | None = None, now: str | None = None,
              mark_seen: list[str] | None = None) -> dict:
    """保護係（hogo、Haiku）か見直し（Sonnet）の升（out/photos/hogo_<フォルダ>.json）を索引に書く道具。

    doc: {"pass": "hogo"|"review", "made_by", "photos": {写真 id: {"masks": [{"kind", "cells" | "box", "note"}],
          "drop": ["C3:E5", ...]（見直しで外す升）, "crop": "A1:H10"（残す範囲）| null, "exclude": "理由" | null,
          "include": true（見直しで除外を取り消す）, "ok": true}},
          "seen": [写真 id, ...]（見たが変えなかった写真。この pass の記録だけ付け、箱・crop・除外は触らない）}
    - mark_seen（--mark-seen / --seen-from）も同じ扱い。photos に書いた写真は seen より photos が優先。
    - 同じ pass（hogo か review）の前の箱は置き換える（やり直しても増えない）。auto の箱はそのまま。
    - exclude があれば public: false と public_reason（研究・研究室に関する物。塗りつぶしで済ませない）。
    - kind は face / plate / poster / name / phone だけ。升の書き方が違う写真は errors に出し、その写真は書かない。"""
    from . import privacy

    now = now or time.strftime("%Y-%m-%dT%H:%M:%S")
    by = by or str(doc.get("pass") or "hogo")
    if by not in ("hogo", "review"):
        raise ValueError(f"pass は hogo か review: {by!r}")
    items = doc.get("photos") or {}
    idx = load_index(index_path)
    by_id = {p["id"]: p for p in idx["photos"]}
    out: dict = {"pass": by, "photos": 0, "masks": 0, "dropped": 0, "crops": 0, "excluded": [], "errors": [], "not_found": [],
                 "seen": 0, "written": [], "skipped": []}
    done_seen: set[str] = set()
    for pid in list(doc.get("seen") or []) + list(mark_seen or []):
        pid = str(pid)
        if pid in items:
            continue
        p = by_id.get(pid)
        if p is None:
            if pid not in out["not_found"]:
                out["not_found"].append(pid)
            continue
        if pid in done_seen:
            continue   # 同じ呼び出しで重複して来た id
        done_seen.add(pid)
        prev = p.get(by) if isinstance(p.get(by), dict) else {}
        p[by] = {"by": doc.get("made_by") or prev.get("by") or by, "at": now, "ok": True, "seen": True, "seen_at": now}
        out["seen"] += 1
    for pid, r in items.items():
        p = by_id.get(pid)
        if p is None:
            out["not_found"].append(pid)
            continue
        if not isinstance(r, dict):
            continue
        new, bad = [], False
        for m in r.get("masks") or []:
            try:
                kind = m.get("kind")
                if kind not in privacy.KINDS:
                    raise ValueError(f"kind は {sorted(privacy.KINDS)} のどれか: {kind!r}")
                box = privacy.cells_to_box(m["cells"]) if m.get("cells") else privacy.norm_box(m["box"])
                e = {"kind": kind, "box": box, "by": by}
                if m.get("cells"):
                    e["cells"] = m["cells"]
                if m.get("note"):
                    e["note"] = str(m["note"])[:200]
                new.append(e)
            except (KeyError, ValueError, TypeError, AttributeError) as ex:
                out["errors"].append({"id": pid, "mask": m, "error": str(ex)})
                bad = True
        crop = None
        if r.get("crop"):
            try:
                crop = privacy.cells_to_box(r["crop"])
            except ValueError as ex:
                out["errors"].append({"id": pid, "crop": r["crop"], "error": str(ex)})
                bad = True
        drops, drop_n = set(), set()
        n_masks = len(p.get("masks") or [])
        for c in r.get("drop") or []:   # 元の升（"C3:E5"）か、箱の番号（3 か {"n": 3}）。寄せた後の箱は番号で
            try:
                if isinstance(c, dict) or isinstance(c, int):
                    n = int(c["n"] if isinstance(c, dict) else c)
                    if not 0 <= n < n_masks:
                        raise ValueError(f"masks[{n}] が無い")
                    drop_n.add(n)
                else:
                    drops.add(tuple(privacy.cells_to_box(c)))
            except (KeyError, ValueError, TypeError) as ex:
                out["errors"].append({"id": pid, "drop": c, "error": str(ex)})
                bad = True
        if bad:
            out["skipped"].append(pid)
            continue
        cur = list(p.get("masks") or [])
        auto_items = list(r.get("auto") or []) + [{"n": c["n"] if isinstance(c, dict) else c, "confirm": True}
                                                  for c in r.get("confirm") or []]   # "confirm": [n, …] でも
        if auto_items and by != "review":
            out["errors"].append({"id": pid, "auto": auto_items, "error": "自動の顔の confirm / drop は見直し（review）か寄せ（refine）で"})
            out["skipped"].append(pid)
            continue
        auto_bad = False
        for it in auto_items:
            try:
                n = int(it["n"])
                if not 0 <= n < len(cur) or cur[n] is None or cur[n].get("by") != "auto":
                    raise ValueError(f"masks[{it.get('n')}] は自動の箱ではない")
                if it.get("box") is not None and privacy.norm_box(it["box"]) != privacy.norm_box(cur[n]["box"]):
                    raise ValueError(f"masks[{n}] の箱が依頼の時と違う")
                dec = _auto_decision(cur[n], it, doc.get("made_by") or by, now)
                if dec is None:
                    p.setdefault("dropped_auto", []).append({"box": cur[n]["box"], "score": cur[n].get("score"), "at": now,
                                                             "by": doc.get("made_by") or by})
                    out["dropped_auto"] = out.get("dropped_auto", 0) + 1
                else:
                    out["confirmed"] = out.get("confirmed", 0) + 1
                cur[n] = dec
            except (KeyError, ValueError, TypeError) as ex:
                out["errors"].append({"id": pid, "auto": it, "error": str(ex)})
                auto_bad = True
        if auto_bad:
            out["skipped"].append(pid)
            continue
        keep = []
        for i, m in enumerate(cur):
            if m is None:
                continue
            if i in drop_n:   # 番号で外す（auto の箱も。どちらも写真に記録を残す）
                key = "dropped_auto" if m.get("by") == "auto" else "dropped"
                p.setdefault(key, []).append({"kind": m.get("kind"), "box": m.get("box"), "box_before": m.get("box_before"),
                                              "by_was": m.get("by"), "dropped_by": by, "made_by": doc.get("made_by") or by,
                                              "at": now})
                out["dropped"] += 1
                continue
            if m.get("by") == by and not m.get("confirmed"):   # 確かめた顔はやり直しで消さない
                continue
            if drops and m.get("by") != "auto" and (tuple(m.get("box") or ()) in drops
                                                   or tuple(m.get("box_before") or ()) in drops):
                out["dropped"] += 1
                continue
            keep.append(m)
        p["masks"] = keep + new
        out["masks"] += len(new)
        if crop is not None:
            p["crop"] = {"box": crop, "cells": r["crop"], "by": by}
            out["crops"] += 1
        elif "crop" in r and by == "review":
            p.pop("crop", None)
        if r.get("exclude"):
            p["public"], p["public_reason"] = False, str(r["exclude"])[:200]
            out["excluded"].append(pid)
        elif by == "review" and r.get("include") and p.get("public") is False:
            p["public"], p["public_reason"] = None, None
        p[by] = {"by": doc.get("made_by") or by, "at": now, "ok": bool(r.get("ok", True))}
        out["photos"] += 1
        out["written"].append(pid)
    save_index(idx, index_path)
    return out


def hogo_tasks(index_path: Path = INDEX_PATH, *, folder: str | None = None, ids: list[str] | None = None,
               out_dir: Path = Path("out/photos/hogo"), photo_root: Path | None = None) -> dict:
    """保護係に見せる升目の絵（send_file＝顔の箱つきの縮小版に 10×10 の升）と依頼の一覧を out/photos/hogo/ に出す。
    send_file が無い写真は、元の写真から同じ縮小版を作って使う（索引は書かない）。"""
    from . import privacy

    photo_root = Path(photo_root or Path(index_path).parent)
    idx = load_index(index_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, missing = [], []
    for rec in idx["photos"]:
        if rec.get("period") == "before":
            continue
        if folder and photo_folder(rec) != folder:
            continue
        if ids and not any(rec["id"].startswith(i) for i in ids):
            continue
        send = source_path({"file": rec.get("send_file")}, photo_root) if rec.get("send_file") else None
        if send is not None and send.exists():
            im = privacy.load_oriented(send)
        else:
            src = source_path(rec, photo_root)
            if not src.exists():
                missing.append(rec["id"])
                continue
            big = privacy.load_oriented(src)
            m, _ = privacy.auto_masks(big)
            others = [x for x in rec.get("masks") or [] if x.get("by") != "auto"]
            im = privacy.paint_masks(privacy.resize_long(big, privacy.SEND_LONG), others)   # 自動の顔は描かない
        g = out_dir / f"{rec['id']}_grid.jpg"
        privacy.save_jpeg(privacy.grid_image(im), g, 85)
        rows.append({"photo": rec["id"], "folder": photo_folder(rec), "grid": g.as_posix(),
                     "auto": [m["kind"] for m in rec.get("masks") or [] if m.get("by") == "auto"]})
    name = re.sub(r"[^\w-]+", "_", folder or "all").strip("_") or "all"
    doc = {"folder": folder, "made_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "photos": rows, "missing": missing,
           "write_to": f"out/photos/hogo_{name}.json",
           "note": "保護係（.claude/agents/hogo.md）が grid を見て升を指す。書き戻しは recorder photos masks --from <write_to>"}
    tp = out_dir / f"tasks_{name}.json"
    tp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"tasks": tp.as_posix(), "n": len(rows), "missing": missing, "write_to": doc["write_to"]}


# --- 寄せ（refine）：保護係の粗い箱を細かい升で締める（11/8 F11） ------------------------------------------
# 10×10 の升で指した箱は粗く、垂れ幕 3 本が壁の大きな一塊を隠す。箱ごとに各側 25 % 広げて切り出し、
# その中に細かい 10×10 の升を引いた絵を係に見せ、「本当に隠す範囲」を指してもらう（目印係の寄りと同じ考え）。
# 置き換える箱は元の箱の中に限る（広げない）。複数の物に分かれる時は分けてよい。

REFINE_PAD = 0.25          # 寄せの切り出しで箱を各側にこの割合（箱の幅・高さに対して）だけ広げる
REFINE_PASSES = ("hogo", "review")
REFINE_LONG = 1200         # 寄せの絵の長辺（小さな箱は拡大して見せる。拡大は 8 倍まで）


def refine_region(box) -> list[float]:
    """箱 → 寄せの切り出しの範囲（割合）。各側に箱の幅・高さの REFINE_PAD だけ広げ、写真の中に収める。"""
    from . import privacy

    x0, y0, x1, y1 = privacy.norm_box(box)
    px, py = (x1 - x0) * REFINE_PAD, (y1 - y0) * REFINE_PAD
    return [round(max(0.0, x0 - px), 6), round(max(0.0, y0 - py), 6), round(min(1.0, x1 + px), 6), round(min(1.0, y1 + py), 6)]


def refine_cells_to_box(region, box, cells: str) -> list[float]:
    """寄せの絵の升（"C3:E5"）→ 写真全体の割合の箱。元の箱の中に切り詰める（広げない）。何も残らなければ ValueError。"""
    from . import privacy

    rx0, ry0, rx1, ry1 = region
    cx0, cy0, cx1, cy1 = privacy.cells_to_box(cells)
    rw, rh = rx1 - rx0, ry1 - ry0
    fx0, fy0, fx1, fy1 = rx0 + cx0 * rw, ry0 + cy0 * rh, rx0 + cx1 * rw, ry0 + cy1 * rh
    bx0, by0, bx1, by1 = privacy.norm_box(box)
    nx0, ny0, nx1, ny1 = max(fx0, bx0), max(fy0, by0), min(fx1, bx1), min(fy1, by1)
    if nx1 - nx0 <= 1e-6 or ny1 - ny0 <= 1e-6:
        raise ValueError(f"升 {cells} は元の箱の外（元の箱より広げない）")
    return privacy.norm_box([nx0, ny0, nx1, ny1])


def _refine_targets(rec: dict, auto: bool = False) -> list[tuple[int, dict]]:
    return [(i, m) for i, m in enumerate(rec.get("masks") or []) if m.get("box")
            and (m.get("by") in REFINE_PASSES or (auto and m.get("by") == "auto" and m.get("kind") == "face"))]


def hogo_refine_tasks(index_path: Path = INDEX_PATH, *, ids: list[str] | None = None, building: str | None = None,
                      out_dir: Path = Path("out/photos/hogo_refine"), photo_root: Path | None = None,
                      auto: bool = False) -> dict:
    """寄せの絵 `<id>_<n>_grid.jpg`（n は索引の masks の番号）と依頼 `tasks_refine[_<棟>].json` を出す。索引は書かない。
    絵は元の写真から切り出す（細かく見るため）。顔の自動の箱は塗ってから切り出す。元の箱の範囲を赤の線で示す。"""
    from PIL import ImageDraw

    from . import privacy

    photo_root = Path(photo_root or Path(index_path).parent)
    idx = load_index(index_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, missing = [], []
    for rec in idx["photos"]:
        if rec.get("period") == "before" or rec.get("public") is False:
            continue
        if ids and not any(rec["id"].startswith(i) for i in ids):
            continue
        if building and rec.get("building") != building:
            continue
        targets = _refine_targets(rec, auto)
        if not targets:
            continue
        src = source_path(rec, photo_root)
        if not src.exists():
            missing.append(rec["id"])
            continue
        big = privacy.resize_long(privacy.load_oriented(src), privacy.DETECT_LONG)
        # 自動の顔の箱は塗らない（見直しが「顔か」を確かめるため。この絵は out/ の係用で公開しない）
        W, H = big.size
        for n, m in targets:
            region = refine_region(m["box"])
            x0, y0 = int(region[0] * W), int(region[1] * H)
            px = (x0, y0, max(x0 + 1, round(region[2] * W)), max(y0 + 1, round(region[3] * H)))
            crop = big.crop(px)
            s = min(8.0, REFINE_LONG / max(crop.size))
            crop = crop.resize((max(1, round(crop.size[0] * s)), max(1, round(crop.size[1] * s))))
            cw, ch = crop.size
            d = ImageDraw.Draw(crop)   # 元の箱（これより広げない）を赤の線で
            rw, rh = region[2] - region[0], region[3] - region[1]
            b = privacy.norm_box(m["box"])
            d.rectangle((round((b[0] - region[0]) / rw * cw), round((b[1] - region[1]) / rh * ch),
                         round((b[2] - region[0]) / rw * cw) - 1, round((b[3] - region[1]) / rh * ch) - 1),
                        outline=(255, 40, 40), width=2)
            g = out_dir / f"{rec['id']}_{n}_grid.jpg"
            privacy.save_jpeg(privacy.grid_image(crop), g, 88)
            rows.append({"photo": rec["id"], "n": n, "kind": m.get("kind"), "by": m.get("by"), "cells": m.get("cells"),
                         "note": m.get("note"), "box": b, "region": region, "grid": g.as_posix()}
                        | ({"score": m.get("score"), "ask": "顔か確かめて confirm（cells で締めてよい）か drop"}
                           if m.get("by") == "auto" else {}))
    tag = re.sub(r"[^\w-]+", "_", building) if building else ""
    name = "tasks_refine" + (f"_{tag}" if tag else "")
    write_to = "out/photos/hogo_refine" + (f"_{tag}" if tag else "") + ".json"
    doc = {"made_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "building": building, "boxes": rows, "missing": missing,
           "write_to": write_to,
           "note": "保護係（.claude/agents/hogo.md の「寄せの時」）が grid を見て、赤の線の中で本当に隠す升を指す。"
                   "書き戻しは recorder photos masks --pass refine --from <write_to>"}
    tp = out_dir / f"{name}.json"
    tp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"tasks": tp.as_posix(), "n": len(rows), "photos": len({r["photo"] for r in rows}), "missing": missing,
            "write_to": write_to}


def refine_masks(index_path: Path, doc: dict, *, now: str | None = None) -> dict:
    """寄せの結果を索引の masks に書く（箱を細かい範囲に置き換える）。
    doc: {"pass": "refine", "made_by", "photos": {写真 id: [{"n": 索引の masks の番号, "box": [元の箱]（あれば照合）,
          "cells": ["C3:E5", "G2:H4"]（寄せの絵の升。1 つ以上）, "note"}]}}
    - {"n", "drop": true, "note": 理由} → その箱を外す（写真の "dropped" に元の箱・dropped_by: refine・note を残す）。
    - 自動の顔の箱（by: auto）は {"n", "confirm": true}（cells で締めてもよい）→ by: review にして残す（confirmed を記録）、
      {"n", "drop": true} → 外す（dropped_auto に元の箱を残す）。auto の箱に cells だけ → その項目は errors。
    - 置き換えた箱は元の pass（hogo / review）のまま、"refined": {by, at, cells}・"box_before"（元の箱）を残す。
    - 新しい箱は元の箱の中に切り詰める（広げない）。分けた時は同じ kind・note で並べる。
      元の箱の外だけの升は捨てて warnings に残し、中の升で書く（全部が外ならその項目は errors）。
    - 項目ごとに確かめ、**だめな項目だけ飛ばして**同じ写真の他の箱は書く（11/8 追記 2）。
      結果に written（書いた写真）・skipped（何も書かなかった写真）・errors（項目ごと）・warnings。"""
    from . import privacy

    now = now or time.strftime("%Y-%m-%dT%H:%M:%S")
    made_by = doc.get("made_by") or "refine"
    idx = load_index(index_path)
    by_id = {p["id"]: p for p in idx["photos"]}
    out: dict = {"pass": "refine", "photos": 0, "boxes": 0, "new_boxes": 0, "area_before": 0.0, "area_after": 0.0,
                 "confirmed": 0, "dropped": 0, "dropped_auto": 0, "written": [], "skipped": [], "errors": [],
                 "warnings": [], "not_found": []}
    for pid, items in (doc.get("photos") or {}).items():
        p = by_id.get(pid)
        if p is None:
            out["not_found"].append(pid)
            out["skipped"].append(pid)
            continue
        masks = list(p.get("masks") or [])
        repl: dict[int, list[dict]] = {}
        logs: list[tuple[str, dict]] = []   # 書くと決まった項目の記録（dropped / dropped_auto）
        for it in items if isinstance(items, list) else []:
            try:
                n = int(it["n"])
                if n in repl:
                    raise ValueError(f"masks[{n}] が 2 回出ている")
                if not 0 <= n < len(masks) or masks[n].get("by") not in REFINE_PASSES + ("auto",):
                    raise ValueError(f"masks[{it.get('n')}] は保護係・見直し・自動の箱ではない")
                m = masks[n]
                if it.get("box") is not None and privacy.norm_box(it["box"]) != privacy.norm_box(m["box"]):
                    raise ValueError(f"masks[{n}] の箱が依頼の時と違う（hogo-grid --refine をやり直す）")
                if m.get("by") == "auto":
                    r = _auto_decision(m, it, made_by, now)
                    if r is None:
                        repl[n] = []
                        logs.append(("dropped_auto", {"box": m["box"], "score": m.get("score"), "at": now, "by": made_by}))
                        continue
                    if not it.get("cells"):
                        repl[n] = [r]
                        logs.append(("confirmed", {}))
                        continue
                    m = r   # confirm＋cells：cells が書けた時だけ confirmed を数える（下）
                elif it.get("drop"):
                    if it.get("cells"):
                        raise ValueError("drop と cells の両方は書けない")
                    repl[n] = []
                    logs.append(("dropped", {"kind": m.get("kind"), "box": m["box"], "box_before": m.get("box_before"),
                                             "by_was": m.get("by"), "cells": m.get("cells"), "dropped_by": "refine",
                                             "note": str(it.get("note") or "")[:200], "made_by": made_by, "at": now}))
                    continue
                cells = it.get("cells")
                cells = [cells] if isinstance(cells, str) else list(cells or [])
                if not cells:
                    raise ValueError("cells が空（隠す物が無いなら {\"n\": …, \"drop\": true, \"note\": 理由}）")
                region = refine_region(m["box"])
                news = []
                for c in cells:
                    try:
                        nb = refine_cells_to_box(region, m["box"], c)
                    except ValueError as ex:
                        if "元の箱の外" not in str(ex):
                            raise
                        out["warnings"].append({"id": pid, "n": n, "cells": c, "warning": f"{ex}。この升は捨てた"})
                        continue
                    e = {k: v for k, v in m.items() if k not in ("box", "refined", "box_before")}
                    e["box"] = nb
                    e["box_before"] = m.get("box_before") or m["box"]
                    e["refined"] = {"by": made_by, "at": now, "cells": c}
                    if it.get("note"):
                        e["note"] = str(it["note"])[:200]
                    news.append(e)
                if not news:
                    raise ValueError(f"升 {cells} はどれも元の箱の外（元の箱より広げない）")
                repl[n] = news
                if masks[n].get("by") == "auto":
                    logs.append(("confirmed", {}))
            except (KeyError, ValueError, TypeError) as ex:
                out["errors"].append({"id": pid, "n": it.get("n") if isinstance(it, dict) else None, "item": it,
                                      "error": str(ex)})
        if not repl:
            out["skipped"].append(pid)
            continue
        for k, rec in logs:
            if k == "confirmed":
                out["confirmed"] += 1
            else:
                out[k] += 1
                p.setdefault(k, []).append(rec)
        new_masks = []
        for i, m in enumerate(masks):
            if i in repl:
                out["boxes"] += 1
                out["new_boxes"] += len(repl[i])
                out["area_before"] += _area(m["box"])
                out["area_after"] += sum(_area(e["box"]) for e in repl[i])
                new_masks += repl[i]
            else:
                new_masks.append(m)
        p["masks"] = new_masks
        p["refine"] = {"by": made_by, "at": now, "boxes": len(repl)}
        out["photos"] += 1
        out["written"].append(pid)
    out["area_before"], out["area_after"] = round(out["area_before"], 4), round(out["area_after"], 4)
    save_index(idx, index_path)
    return out


def _auto_decision(m: dict, it: dict, by: str, now: str) -> dict | None:
    """自動の顔の箱への見直しの答え：confirm → by: review の写し（confirmed つき）、drop → None。どちらも無ければ ValueError。"""
    if it.get("drop") and it.get("confirm"):
        raise ValueError("confirm と drop の両方は書けない")
    if it.get("drop"):
        return None
    if not it.get("confirm"):
        raise ValueError("自動の顔の箱には confirm: true か drop: true を書く")
    e = dict(m)
    e["by"] = "review"
    e["confirmed"] = {"by": by, "at": now, "from": "auto"}
    return e


def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


# --- CLI（recorder/__main__.py は登録の 2 行だけ） -----------------------------------------------------

T4_CMDS = ("relink", "send", "ruler-mark", "tape", "hogo-grid", "masks", "publish")
# photos tape は measure tape（メジャーの目盛りから周期）と同じ名前で別の仕事だったので ruler-mark に改めた（11/8 F3）。
# 古い名前は警告を出して同じ処理に流す。関数名 mark_tape は変えていない（serve.py の /api/tape が呼ぶ）。
OLD_CMD_NAMES = {"tape": "ruler-mark"}


def _add_ruler_mark_args(q) -> None:
    q.add_argument("ids", nargs="+")
    q.add_argument("--building", required=True)
    q.add_argument("--face", default=None)
    q.add_argument("--note", default=None, help="職員のメモ（どこに当てたか）")
    q.add_argument("--campus", default=None)
    q.add_argument("--index", default=None)


def add_t4_args(psub) -> None:
    q = psub.add_parser("relink", help="索引で file が切れた写真を、フォルダの下から名前で探して付け直す（K-105）")
    q.add_argument("folder")
    q.add_argument("--assign-from-folder", action="store_true", help="棟名のフォルダ（例 <棟名>_<棟id>）の写真に職員の申告として棟を書く（既に staff なら変えない）")
    q.add_argument("--campus", default=None)
    q.add_argument("--index", default=None, help="索引の場所（既定 data/photos/index.json。試す時は写しを渡す）")
    q.add_argument("--dry-run", action="store_true", help="書かずに結果だけ出す")
    q = psub.add_parser("send", help="係に見せる縮小版 send_file（顔に灰色の箱）を、まだ無い写真に作る（K-104）")
    q.add_argument("--photos", default=None, help="写真 id（カンマ区切り。頭だけでも可）")
    q.add_argument("--force", action="store_true")
    q.add_argument("--no-detect", action="store_true",
                   help="顔を探さず、索引の確かめた箱だけを描いて縮小版を全部作り直す（未確認の自動の箱を足さない）")
    q.add_argument("--index", default=None)
    q = psub.add_parser("ruler-mark", help="メジャーの枠（K-102）：写真に職員の申告として棟・面と ruler: tape_measure の印を付ける"
                                            "（メジャーの目盛りを読むのは measure tape）")
    _add_ruler_mark_args(q)
    q = psub.add_parser("tape", help="古い名前（ruler-mark を使う）")
    _add_ruler_mark_args(q)
    q = psub.add_parser("hogo-grid", help="保護係に見せる升目の絵と依頼の一覧（out/photos/hogo/）")
    q.add_argument("--folder", default=None, help="元のフォルダ（例 <日付>/<棟id>）")
    q.add_argument("--photos", default=None)
    q.add_argument("--out", default=None, help="既定 out/photos/hogo（--refine は out/photos/hogo_refine）")
    q.add_argument("--refine", action="store_true",
                   help="寄せ：索引の保護係・見直しの箱ごとに、各側 25%% 広げた切り出しに細かい升の絵を出す")
    q.add_argument("--building", default=None, help="--refine の時、この棟の写真だけ")
    q.add_argument("--auto", action="store_true", help="--refine の時、自動（YuNet）の顔の箱も切り出す（見直しが confirm か drop）")
    q.add_argument("--index", default=None)
    q = psub.add_parser("masks", help="保護係・見直しの升（out/photos/hogo_<フォルダ>.json）を索引の masks に書く")
    q.add_argument("--from", dest="from_json", default=None, help="hogo_<フォルダ>.json（--mark-seen / --seen-from だけの時は省ける）")
    q.add_argument("--pass", dest="pass_", choices=["hogo", "review", "refine"], default=None)
    q.add_argument("--mark-seen", default=None, help="見たが変えなかった写真の id（カンマ区切り）。この pass の見直し済みにする")
    q.add_argument("--seen-from", default=None, help="hogo-grid の tasks JSON。中の写真すべてを見直し済みにする（箱のある物も、記録だけ付く）")
    q.add_argument("--index", default=None)
    q = psub.add_parser("publish", help="提出用の公開版：研究室が分かる物以外を縮小し、箱を塗って data/photos/public/ に（K-103）")
    q.add_argument("--photos", default=None)
    q.add_argument("--out", default=None, help="出す場所（既定 索引のフォルダの public/）")
    q.add_argument("--long", type=int, default=1600)
    q.add_argument("--quality", type=int, default=80)
    q.add_argument("--dry-run", action="store_true")
    q.add_argument("--allow-unreviewed", action="store_true", help="保護係と見直しの両方を通っていない写真も出す（既定は出さない）")
    q.add_argument("--index", default=None)


def run_t4(a) -> int:
    from .ledger import lock

    index_path = Path(a.index) if getattr(a, "index", None) else INDEX_PATH
    ids = [x.strip() for x in a.photos.split(",") if x.strip()] if getattr(a, "photos", None) else None
    cmd = a.photos_cmd
    if cmd in OLD_CMD_NAMES:
        print(f"注意：photos {cmd} は古い名前です。photos {OLD_CMD_NAMES[cmd]} を使ってください（同じ処理をします）",
              file=sys.stderr)
        cmd = OLD_CMD_NAMES[cmd]
    if cmd == "hogo-grid":
        if a.refine:
            res = hogo_refine_tasks(index_path, ids=ids, building=a.building, out_dir=Path(a.out or "out/photos/hogo_refine"),
                                    auto=a.auto)
            print(json.dumps(res, ensure_ascii=False, indent=1))
            return 0
        res = hogo_tasks(index_path, folder=a.folder, ids=ids, out_dir=Path(a.out or "out/photos/hogo"))
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    # send・publish は縮小と顔の検出を鍵の外で回し、索引には鍵の中で差分だけ書く（各関数が鍵を取る。11/8 監査 B 3）
    if cmd == "send":
        res = make_sends(index_path, ids, force=a.force, no_detect=a.no_detect)
        print(json.dumps({**res, "missing": res["missing"][:20], "n_missing": len(res["missing"])}, ensure_ascii=False, indent=1))
        return 0
    if cmd == "publish":
        from . import publish

        res = publish.publish(index_path, out_dir=Path(a.out) if a.out else None, ids=ids, long=a.long,
                              quality=a.quality, dry_run=a.dry_run,
                              allow_unreviewed=a.allow_unreviewed)
        print(publish.summary_text(res))
        return 0
    with lock("ledger", label=f"recorder photos {cmd}"):
        if cmd == "relink":
            res = relink(Path(a.folder), index_path, assign_from_folder=a.assign_from_folder, campus=a.campus, dry_run=a.dry_run)
            print(f"付け直し {len(res['relinked'])}・同名が複数 {len(res['ambiguous'])}・大きさ違い {len(res['mismatch'])}・"
                  f"まだ無い {len(res['still_missing'])}" + (f"／棟を書いた {len(res['assigned'])}・同じ {res['unchanged']}・"
                  f"職員と違う {len(res['conflicts'])}・棟が変わった {len(res['changed'])}・決まらない {len(res['undecided'])}"
                  if a.assign_from_folder else "") + ("（dry-run：書いていない）" if a.dry_run else ""))
            print(json.dumps({k: v for k, v in res.items() if k != "still_missing"}, ensure_ascii=False, indent=1))
            return 0
        if cmd == "ruler-mark":
            res = mark_tape(index_path, a.ids, a.building, face=a.face, note=a.note, campus=a.campus)
            print(json.dumps(res, ensure_ascii=False, indent=1))
            return 0 if not res["not_found"] else 1
        if cmd == "masks":
            doc = json.loads(Path(a.from_json).read_text(encoding="utf-8")) if a.from_json else {"pass": a.pass_ or "review"}
            if (a.pass_ or doc.get("pass")) == "refine":
                if not a.from_json:
                    raise SystemExit("--pass refine には --from（out/photos/hogo_refine*.json）が要る")
                res = refine_masks(index_path, doc)
                print(json.dumps(res, ensure_ascii=False, indent=1))
                return 0 if not res["errors"] else 1
            seen = [x.strip() for x in (a.mark_seen or "").split(",") if x.strip()]
            if a.seen_from:
                tj = json.loads(Path(a.seen_from).read_text(encoding="utf-8"))
                seen += [r["photo"] for r in tj.get("photos") or []]
            res = set_masks(index_path, doc, by=a.pass_, mark_seen=seen)
            print(json.dumps(res, ensure_ascii=False, indent=1))
            return 0 if not res["errors"] else 1
    raise ValueError(cmd)
