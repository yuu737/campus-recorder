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
     "building": null, "face": null}       # 確定は人か測量係のエージェントが入れる。ここでは決めつけない

決まり：
    GPS と方位から「どの棟のどの面か」を**候補**として並べるだけで、確定しない。
    候補は、カメラ位置から 120 m 以内で、向いている方向 ±45° に足元がある棟。
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

from . import geo

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
        if lat is not None and lon is not None and (lat, lon) != (0.0, 0.0):
            x, y = geo.lonlat_to_xy(lon, lat)
            alt = _rational(g.get(G.GPSAltitude))
            info["gps"] = {"lat": round(lat, 7), "lon": round(lon, 7), "alt": alt,
                           "x": round(x, 1), "y": round(y, 1),
                           "hdop": _rational(g.get(G.GPSDOP))}
        hd = _rational(g.get(G.GPSImgDirection))
        if hd is not None:
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

def building_candidates(info: dict, site: dict, max_dist: float = 120.0, half_fov: float = 45.0) -> list[dict]:
    """GPS と方位から、写っていそうな棟を並べる（確定しない）。"""
    if not info.get("gps"):
        return []
    from shapely.geometry import MultiPolygon, Point, Polygon

    cam = Point(info["gps"]["x"], info["gps"]["y"])
    heading = info.get("heading_deg")
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
        bearing = (math.degrees(math.atan2(c.x - cam.x, c.y - cam.y)) + 360) % 360
        diff = None
        if heading is not None:
            diff = abs((bearing - heading + 180) % 360 - 180)
            if diff > half_fov and dist > 5:
                continue
        out.append({"building": b["id"], "name": b.get("name"), "distance_m": round(dist, 1),
                    "bearing_deg": round(bearing, 1), "bearing_diff_deg": round(diff, 1) if diff is not None else None})
    out.sort(key=lambda r: (r["bearing_diff_deg"] if r["bearing_diff_deg"] is not None else 999, r["distance_m"]))
    return out


# --- 索引 -----------------------------------------------------------------------------

def load_index(path: Path = INDEX_PATH) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"schema": INDEX_SCHEMA, "note": "写真本体は git に入れない。確定した棟・面は人か測量係が書く", "photos": []}


def save_index(idx: dict, path: Path = INDEX_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")


def import_photos(folder: Path, site: dict | None = None, index_path: Path = INDEX_PATH,
                  root: Path = PHOTO_DIR) -> tuple[dict, list[dict]]:
    """フォルダの写真を索引に足す。既にある id は EXIF と checks だけ更新し、人が入れた building / face は残す。"""
    idx = load_index(index_path)
    by_id = {p["id"]: p for p in idx["photos"]}
    added = []
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() not in EXTS:
            continue
        info = read_exif(f)
        pid = f.stem
        try:
            rel = str(f.relative_to(root)).replace("\\", "/")
        except ValueError:
            rel = str(f).replace("\\", "/")
        rec = by_id.get(pid) or {"id": pid, "building": None, "face": None, "notes": ""}
        rec.update({
            "file": rel,
            "taken_at": info["taken_at"],
            "camera": {"make": info["make"], "model": info["model"]},
            "width": info["width"], "height": info["height"], "orientation": info["orientation"],
            "focal_mm": info["focal_mm"], "focal_35mm": info["focal_35mm"],
            "gps": info["gps"], "heading_deg": info["heading_deg"], "heading_ref": info.get("heading_ref"),
            "checks": quick_checks(f, info),
            "candidates": building_candidates(info, site) if site else [],
            "imported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })
        if info["error"]:
            rec["error"] = info["error"]
        if pid not in by_id:
            idx["photos"].append(rec)
            by_id[pid] = rec
            added.append(rec)
    save_index(idx, index_path)
    return idx, added


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
