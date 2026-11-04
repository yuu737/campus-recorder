"""写真の登録：写真 1 枚が「棟のどの面の、どこを、どう写しているか」を 1 回だけ記録しておく。

登録するのは、面の平面（面の始点 from から面に沿った s m、高さ z m）→ 写真（px）の射影変換 H_plane と、
写真の視点（カメラの位置と向き。面の外に出た部品の線を写真に投影するため）。どちらも、検収係がすでに決めた
正面化（rectify の H）と原点（compare の x_origin_px・z_origin_px・px_per_m）から作る。

一度登録すれば、棟 JSON を直して組み直すたびに recheck が全部の写真に自動で重ね直す（手で線を指すのは未登録の写真だけ）。

台帳 data/photos/registry.json（git に入れる。写真そのものは入れない）
    entries[]: photo（索引の id）、building、face、period、file（写真の場所）、image_size、
               H_plane（3×3。(s, z, 1) → 写真の (u, v, w)。w は写真の手前向きの奥行き m になるよう揃えてある）、
               face_frame（登録した時の面の from / to。後で面の位置が変わっても登録時の座標に直して使う）、
               cover（写真が覆う面の範囲。面の (s, z) m の多角形。登録した時の面の矩形で切った物）、
               camera（focal_px・R_cv・t_cv・camera_xyz・blender。Blender で同じ視点を描くため）、
               source（使った正面化・compare・pose・線の JSON、原点の値）、check（再投影の誤差 px）、registered_at
    not_registered[]: 面 id が付いているのに登録できなかった写真と理由
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import numpy as np

from .photos import INDEX_PATH, PHOTO_DIR, load_index
from .schema import face_ground_offset, load_building

REGISTRY_PATH = PHOTO_DIR / "registry.json"
SCHEMA = "campus-recorder/registry/1"
RESOLVE_PX = 2.0         # 既存の pose と最新の compare の射影がこれ以上違えば、最新の compare から視点を解き直す


# --- 台帳の読み書き -----------------------------------------------------------------

def load_registry(path: Path = REGISTRY_PATH) -> dict:
    if Path(path).exists():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return {"schema": SCHEMA, "note": "写真の登録（面の平面→写真の射影と視点）。recheck・coverage が読む。写真は git に入れない",
            "entries": [], "not_registered": []}


def save_registry(reg: dict, path: Path = REGISTRY_PATH) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    reg["entries"].sort(key=lambda e: (e["building"], e["face"], e["photo"]))
    Path(path).write_text(json.dumps(reg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def upsert(reg: dict, entry: dict) -> None:
    """同じ写真・同じ面の登録は置き換える。登録できたら not_registered から外す。"""
    key = (entry["building"], entry["photo"], entry["face"])
    reg["entries"] = [e for e in reg["entries"] if (e["building"], e["photo"], e["face"]) != key]
    reg["entries"].append(entry)
    reg["not_registered"] = [n for n in reg.get("not_registered", [])
                             if (n.get("building"), n.get("photo"), n.get("face")) != key]


def entries_for(reg: dict, building_id: str, photos: list[str] | None = None) -> list[dict]:
    out = [e for e in reg.get("entries", []) if e["building"] == building_id]
    if photos:
        want = set(photos)
        out = [e for e in out if e["photo"] in want or Path(e["photo"]).stem in want
               or any(e["photo"].startswith(p) for p in want)]
    return out


# --- 面の座標と射影 -------------------------------------------------------------------

def face_frame(f: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """(始点 p0, 沿う向き u, 外向き n, 長さ L)。外向きは (u.y, −u.x)（compare・parts_geom と同じ）。"""
    p0 = np.array(f["from"], float)
    d = np.array(f["to"], float) - p0
    L = float(np.linalg.norm(d))
    u = d / L
    return p0, u, np.array([u[1], -u[0]]), L


def plane_to_world(frame: dict, s, z, n=0.0) -> np.ndarray:
    """面の (s, n, z) → 世界 (x, y, z)。frame は {"from", "to"}。配列可。"""
    p0, u, nv, _ = face_frame(frame)
    s, z, n = np.broadcast_arrays(np.asarray(s, float), np.asarray(z, float), np.asarray(n, float))
    return np.stack([p0[0] + u[0] * s + nv[0] * n, p0[1] + u[1] * s + nv[1] * n, z], axis=-1)


def world_to_plane(frame: dict, P) -> np.ndarray:
    """世界 (x, y, z) → 面の (s, n, z)。"""
    p0, u, nv, _ = face_frame(frame)
    P = np.asarray(P, float)
    q = P[..., :2] - p0
    return np.stack([q @ u, q @ nv, P[..., 2]], axis=-1)


def K_matrix(focal_px: float, w: int, h: int) -> np.ndarray:
    return np.array([[focal_px, 0, w / 2.0], [0, focal_px, h / 2.0], [0, 0, 1]], float)


def H_from_pose(R, t, focal_px: float, w: int, h: int, frame: dict) -> np.ndarray:
    """視点（R, t。世界→カメラ）と面から、面の平面 (s, z) → 写真 px の射影変換。w はカメラの奥行き（m）。"""
    p0, u, _, _ = face_frame(frame)
    R, t = np.asarray(R, float), np.asarray(t, float).ravel()
    M = np.column_stack([R @ np.array([u[0], u[1], 0.0]), R @ np.array([0, 0, 1.0]), R @ np.array([p0[0], p0[1], 0.0]) + t])
    return K_matrix(focal_px, w, h) @ M


def H_from_rectify(front: dict, cmp: dict) -> np.ndarray:
    """正面化の H（写真→正面化画像）と compare の原点・縮尺から、面の平面 (s, z) → 写真 px（pose.from_rectification と同じ式）。"""
    ppm, xo, zo = float(cmp["px_per_m"]), float(cmp["x_origin_px"]), float(cmp["z_origin_px"])
    A = np.array([[ppm, 0, xo], [0, -ppm, zo], [0, 0, 1]], float)
    return np.linalg.inv(np.array(front["H"], float)) @ A


def apply_H(H, s, z) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(s, z) → (u, v, w)。w ≤ 0 は写真の後ろ。"""
    H = np.asarray(H, float)
    s, z = np.broadcast_arrays(np.asarray(s, float), np.asarray(z, float))
    q = H @ np.stack([s.ravel(), z.ravel(), np.ones(s.size)])
    w = q[2]
    with np.errstate(divide="ignore", invalid="ignore"):
        return (q[0] / w).reshape(s.shape), (q[1] / w).reshape(s.shape), w.reshape(s.shape)


def invert_H(H, u, v) -> tuple[np.ndarray, np.ndarray]:
    """写真 px → 面の平面 (s, z)。"""
    Hi = np.linalg.inv(np.asarray(H, float))
    s, z, w = apply_H(Hi, u, v)
    return s, z


def project_world(cam: dict, P) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """世界の点 → 写真 (u, v, 奥行き)。cam は登録の camera。"""
    P = np.asarray(P, float).reshape(-1, 3)
    R, t = np.array(cam["R_cv"], float), np.array(cam["t_cv"], float).ravel()
    w, h = cam["image_size"]
    X = P @ R.T + t
    K = K_matrix(cam["focal_px"], w, h)
    q = X @ K.T
    with np.errstate(divide="ignore", invalid="ignore"):
        return q[:, 0] / q[:, 2], q[:, 1] / q[:, 2], X[:, 2]


def normalize_like(H: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """射影変換の定数倍（符号を含む）を ref に揃える。"""
    lam = float(np.sum(H * ref) / np.sum(ref * ref))
    return H / lam


def H_discrepancy_px(Ha, Hb, frame_L: float, z0: float, z1: float, w: int, h: int) -> float | None:
    """2 つの射影変換が、面の矩形の格子（写真に入る所）で何 px 違うか（最大）。"""
    S, Z = np.meshgrid(np.linspace(0, frame_L, 13), np.linspace(z0, z1, 7))
    ua, va, wa = apply_H(Ha, S, Z)
    ub, vb, wb = apply_H(Hb, S, Z)
    ok = (wa > 0) & (wb > 0) & (ua >= 0) & (ua < w) & (va >= 0) & (va < h)
    if not ok.any():
        return None
    return round(float(np.hypot(ua - ub, va - vb)[ok].max()), 2)


# --- 覆う範囲（面の平面の多角形） ------------------------------------------------------

def clip_halfplane(poly: list[tuple[float, float]], a: float, b: float, c: float) -> list[tuple[float, float]]:
    """a·s + b·z + c ≥ 0 の側を残す（Sutherland–Hodgman）。"""
    out: list[tuple[float, float]] = []
    n = len(poly)
    for i in range(n):
        P, Q = poly[i], poly[(i + 1) % n]
        fp, fq = a * P[0] + b * P[1] + c, a * Q[0] + b * Q[1] + c
        if fp >= 0:
            out.append(P)
        if (fp >= 0) != (fq >= 0):
            tt = fp / (fp - fq)
            out.append((P[0] + tt * (Q[0] - P[0]), P[1] + tt * (Q[1] - P[1])))
    return out


def cover_polygon(H, w: int, h: int, rect: tuple[float, float, float, float], margin_px: float = 0.0,
                  min_depth: float = 0.5) -> list[tuple[float, float]]:
    """写真の枠に入る面の範囲を、面の矩形 rect=(s0, s1, z0, z1) を切って求める。
    (u, v, w) = H (s, z, 1) は (s, z) の 1 次式なので、「写真の中」「カメラの前」はどれも面の平面の半平面になる。"""
    H = np.asarray(H, float)
    s0, s1, z0, z1 = rect
    poly = [(s0, z0), (s1, z0), (s1, z1), (s0, z1)]
    r0, r1, r2 = H
    m = margin_px
    halfs = [r2 - np.array([0, 0, min_depth]),            # w ≥ min_depth（カメラの前）
             r0 - m * r2,                                 # u ≥ m
             (w - m) * r2 - r0,                           # u ≤ w − m
             r1 - m * r2,                                 # v ≥ m
             (h - m) * r2 - r1]                           # v ≤ h − m
    for a, b, c in halfs:
        poly = clip_halfplane(poly, float(a), float(b), float(c))
        if len(poly) < 3:
            return []
    return [(round(float(p[0]), 3), round(float(p[1]), 3)) for p in poly]


def poly_area(pts) -> float:
    n = len(pts)
    return abs(sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))) / 2 if n >= 3 else 0.0


def face_top(b: dict, f: dict) -> float:
    def v(m):
        if isinstance(m, (int, float)):
            return float(m)
        if isinstance(m, dict) and m.get("status") != "unknown" and m.get("value") is not None:
            return float(m["value"])
        return None
    return v(f.get("top_z")) or v(b.get("parapet_top")) or 20.0


# --- 写真の場所 -------------------------------------------------------------------------

def photo_record(photo_id: str, index: dict | None = None) -> dict | None:
    idx = index or load_index(INDEX_PATH)
    for r in idx.get("photos", []):
        if r["id"] == photo_id:
            return r
    for r in idx.get("photos", []):
        if r["id"].startswith(photo_id) or Path(r.get("file", "")).stem == photo_id:
            return r
    return None


def photo_path(rec_or_entry: dict) -> Path:
    f = rec_or_entry.get("file") or ""
    p = Path(f)
    return p if p.is_absolute() else PHOTO_DIR / p


# --- 登録 -------------------------------------------------------------------------------

def register(photo_id: str, building_json: Path, face_id: str, front_json: Path | None = None,
             compare_json: Path | None = None, pose_json: Path | None = None, lines_json: Path | None = None,
             index: dict | None = None, pose_out_dir: Path | None = None, by: str = "registry") -> dict:
    """写真 1 枚を登録する（台帳へは書かない。返り値の entry を upsert する）。
    手がかりの優先：正面化＋compare があれば H_plane はそこから（検収 A の原点）。視点は pose があればそれを使い、
    H_plane と RESOLVE_PX 以上違えば H_plane から解き直す（pose.from_rectification）。pose だけなら H_plane は pose から。
    どれも無ければ ValueError（理由つき）。"""
    from . import pose as pose_mod

    b = load_building(building_json)
    faces = {f["id"]: f for f in b.get("faces", [])}
    if face_id not in faces:
        raise ValueError(f"面 {face_id} は棟 {b['id']} にありません")
    f = faces[face_id]
    if not (f.get("from") and f.get("to")):
        raise ValueError(f"面 {face_id} の位置（from / to）が未確定です")
    rec = photo_record(photo_id, index) or {}
    frame = {"from": [float(v) for v in f["from"]], "to": [float(v) for v in f["to"]]}
    _, _, _, L = face_frame(frame)
    g = face_ground_offset(f)
    top = face_top(b, f)

    H_rect, fj, cj = None, None, None
    if front_json and compare_json:
        fj = json.loads(Path(front_json).read_text(encoding="utf-8"))
        cj = json.loads(Path(compare_json).read_text(encoding="utf-8"))
        if cj.get("x_origin_px") is None or cj.get("z_origin_px") is None or not cj.get("px_per_m"):
            raise ValueError(f"compare に原点か縮尺がありません: {compare_json}")
        H_rect = H_from_rectify(fj, cj)
    pj = json.loads(Path(pose_json).read_text(encoding="utf-8")) if pose_json else None
    if pj is not None and not (pj.get("ok") and pj.get("R_cv")):
        pj = None
    if H_rect is None and pj is None:
        raise ValueError("正面化＋compare も、使える pose もありません")

    resolved, disc = False, None
    if H_rect is not None:
        w, h = int(fj["width"]), int(fj["height"])
        if pj is not None:
            Hp = H_from_pose(pj["R_cv"], pj["t_cv"], pj["focal_px"], w, h, frame)
            disc = H_discrepancy_px(normalize_like(H_rect, Hp), Hp, L, g, top, w, h)
        if pj is None or disc is None or disc > RESOLVE_PX:
            outd = pose_out_dir or Path("out") / b["id"] / "registry_poses"
            outd.mkdir(parents=True, exist_ok=True)
            ph = photo_path(rec) if rec else Path(photo_id)
            res = pose_mod.from_rectification(ph, Path(front_json), Path(compare_json), Path(building_json), face_id,
                                              out_json=outd / f"{Path(rec.get('file') or photo_id).stem}_pose.json")
            if not res.ok:
                raise ValueError(f"正面化と compare から視点が解けません: {res.reason}")
            old_disc = disc
            pj = res.to_json()
            pose_json = outd / f"{Path(rec.get('file') or photo_id).stem}_pose.json"
            resolved = True
            disc = old_disc
        Hp = H_from_pose(pj["R_cv"], pj["t_cv"], pj["focal_px"], w, h, frame)
        H_plane = normalize_like(H_rect, Hp)
        plane_vs_pose = H_discrepancy_px(H_plane, Hp, L, g, top, w, h)
    else:
        w, h = pj["image_size"]
        H_plane = H_from_pose(pj["R_cv"], pj["t_cv"], pj["focal_px"], w, h, frame)
        plane_vs_pose = 0.0

    cover = cover_polygon(H_plane, w, h, (0.0, L, g, top))
    cam_xyz = pj.get("camera_xyz")
    entry = {
        "photo": rec.get("id", photo_id),
        "building": b["id"],
        "face": face_id,
        "period": rec.get("period"),
        "file": rec.get("file") or str(pj.get("photo") or ""),
        "image_size": [w, h],
        "H_plane": [[float(v) for v in row] for row in H_plane],
        "H_plane_note": "(s, z, 1) → 写真 (u, v, w)。s は面の始点 from から面に沿った m、z は棟の z=0 からの m。w はカメラの奥行き m",
        "face_frame": {**frame, "length_m": round(L, 3), "ground_offset": g, "top_z": top},
        "cover": {"type": "polygon", "pts": [list(p) for p in cover], "area_m2": round(poly_area(cover), 2)},
        "camera": {"focal_px": float(pj["focal_px"]), "image_size": [w, h], "R_cv": pj["R_cv"], "t_cv": pj["t_cv"],
                   "camera_xyz": cam_xyz, "camera_height_m": pj.get("camera_height_m"),
                   "distance_to_face_m": pj.get("distance_to_face_m"), "blender": pj.get("blender")},
        "source": {"front_json": str(front_json) if front_json else None, "compare_json": str(compare_json) if compare_json else None,
                   "pose_json": str(pose_json) if pose_json else None, "lines_json": str(lines_json) if lines_json else None,
                   "origin": ({"px_per_m": cj["px_per_m"], "x_origin_px": cj["x_origin_px"], "z_origin_px": cj["z_origin_px"]} if cj else None),
                   "pose_resolved": resolved},
        "check": {"pose_holdout_px": pj.get("holdout_reproj_px"), "pose_reproj_max_px": pj.get("reproj_max_px"),
                  "plane_vs_pose_px": plane_vs_pose, "existing_pose_vs_compare_px": disc},
        "registered_at": datetime.now().isoformat(timespec="seconds"),
        "registered_by": by,
    }
    return entry


# --- 既存の検収の結果から一括登録 ----------------------------------------------------------

def _compare_candidates(out_dir: Path, stem: str, face: str) -> list[Path]:
    """新しい順（compare_v2 → 検収係の *_kenshu → compare）。"""
    short = stem.split("_")[0] if not stem.startswith("PXL") else stem
    c = [out_dir / "compare_v2" / f"{stem}_{face}_compare.json",
         out_dir / "compare" / f"{stem}_{face}_compare_kenshu.json",
         out_dir / "compare" / f"{short}_{face}_compare_kenshu.json",
         out_dir / "compare" / f"{stem}_{face}_compare.json"]
    return [p for p in c if p.exists()]


def _front_of(compare_json: Path) -> Path | None:
    src = json.loads(compare_json.read_text(encoding="utf-8")).get("source") or ""
    p = Path(src.replace("\\", "/")).with_suffix(".json")
    return p if p.exists() else None


def _lines_of(out_dir: Path, compare_json: Path, stem: str, face: str) -> Path | None:
    tail = stem.split("_")[-1]
    for d in (compare_json.parent, out_dir / "compare_v2", out_dir / "compare"):
        p = d / f"lines_{face}_{tail}.json"
        if p.exists():
            return p
    return None


def find_rectify(stem: str) -> list[Path]:
    root = Path("out") / "rectified"
    return sorted(root.rglob(f"{stem}_front.json")) if root.exists() else []


def why_not(rec: dict, b: dict, out_dir: Path) -> str:
    """面 id が付いているのに登録できない理由。"""
    face = rec.get("face")
    faces = {f["id"] for f in b.get("faces", [])}
    stem = Path(rec.get("file") or rec["id"]).stem
    if face not in faces:
        return f"面 id「{face}」は棟の面（{'・'.join(sorted(faces))}）ではない（角・中庭・通路など）。面に割り当て直せば登録できる"
    cmps = _compare_candidates(out_dir, stem, face)
    fronts = find_rectify(stem)
    if not fronts and not cmps:
        return "正面化（photos rectify）が無い"
    if not cmps:
        return "正面化はあるが検収 A（photos compare）が無いので原点（面の s=0・z=0 が写真のどこか）が決まっていない"
    cj = json.loads(cmps[0].read_text(encoding="utf-8"))
    if not cj.get("ok"):
        return f"compare が失敗している（{cj.get('reason', '')}）"
    manual = any(not l.get("auto", True) for l in cj.get("lines", []))
    if "ambiguous" not in cj and not manual:
        return "compare が古い自動の結果で、横の原点（柱の何本目か）を検収係が確かめていない。--lines で柱の番号を与えて compare し直せば登録できる"
    if cj.get("ambiguous") and not manual:
        return "compare の横の原点が曖昧（柱の何本目か決められない）。--lines で柱の番号を与えれば登録できる"
    if not _front_of(cmps[0]):
        return "compare はあるが元の正面化の JSON（H）が見つからない"
    return ""


def verified_compare(out_dir: Path, rec: dict) -> tuple[Path, Path] | None:
    """原点が確かな compare（判断役の --lines がある、か曖昧でない）と、その正面化の JSON。"""
    stem = Path(rec.get("file") or rec["id"]).stem
    for c in _compare_candidates(out_dir, stem, rec.get("face") or ""):
        cj = json.loads(c.read_text(encoding="utf-8"))
        manual = any(not l.get("auto", True) for l in cj.get("lines", []))
        if cj.get("ok") and (manual or cj.get("ambiguous") is False):
            fr = _front_of(c)
            if fr:
                return c, fr
    return None


def register_existing(out_dir: Path, building_json: Path, reg: dict, index: dict | None = None) -> tuple[list[dict], list[dict]]:
    """out/<棟>/poses の pose と compare から一括登録し、面 id がある残りの写真に登録できない理由を付ける。"""
    idx = index or load_index(INDEX_PATH)
    b = load_building(building_json)
    stems: dict[str, Path | None] = {}
    for p in sorted((out_dir / "poses").glob("*_pose.json")):
        stems[p.name[: -len("_pose.json")]] = p
    done, failed = [], []
    for stem, pose_p in stems.items():
        rec = photo_record(stem, idx)
        if not rec or rec.get("building") != b["id"] or not rec.get("face"):
            failed.append({"photo": stem, "building": b["id"], "face": (rec or {}).get("face"), "reason": "索引に棟・面の割り当てが無い"})
            continue
        face = rec["face"]
        cmps = _compare_candidates(out_dir, stem, face)
        cmp = cmps[0] if cmps else None
        front = _front_of(cmp) if cmp else None
        try:
            e = register(rec["id"], building_json, face, front, cmp if front else None, pose_p,
                         _lines_of(out_dir, cmp, stem, face) if cmp else None, idx)
        except ValueError as ex:
            failed.append({"photo": rec["id"], "building": b["id"], "face": face, "reason": str(ex)})
            continue
        upsert(reg, e)
        done.append(e)
    registered = {(e["photo"], e["face"]) for e in reg["entries"] if e["building"] == b["id"]}
    nr = [n for n in reg.get("not_registered", []) if n.get("building") != b["id"]]
    for rec in idx.get("photos", []):
        if rec.get("building") != b["id"] or not rec.get("face") or (rec["id"], rec["face"]) in registered:
            continue
        reason = next((f["reason"] for f in failed if f["photo"] == rec["id"]), None) or why_not(rec, b, out_dir)
        if not reason:          # pose は無いが原点の確かな compare がある：そこから視点も解いて登録
            vc = verified_compare(out_dir, rec)
            try:
                if not vc:
                    raise ValueError("原点の確かな compare が見つからない")
                stem = Path(rec.get("file") or rec["id"]).stem
                e = register(rec["id"], building_json, rec["face"], vc[1], vc[0], None, _lines_of(out_dir, vc[0], stem, rec["face"]), idx)
                upsert(reg, e)
                done.append(e)
                continue
            except ValueError as ex:
                reason = str(ex)
        nr.append({"photo": rec["id"], "building": b["id"], "face": rec["face"], "period": rec.get("period"), "reason": reason})
    reg["not_registered"] = [n for n in nr if (n.get("building"), n.get("photo"), n.get("face")) not in
                             {(e["building"], e["photo"], e["face"]) for e in reg["entries"]}]
    return done, [n for n in reg["not_registered"] if n["building"] == b["id"]]


def compass8(dx: float, dy: float) -> str:
    """向き（x 東・y 北）→ 8 方位の名前。"""
    ang = (math.degrees(math.atan2(dx, dy)) + 360) % 360       # 北から時計回り
    names = ["北", "北東", "東", "南東", "南", "南西", "西", "北西"]
    return names[int((ang + 22.5) // 45) % 8]
