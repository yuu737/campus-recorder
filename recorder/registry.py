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

登録の形 kind：plane（既定。上の通り）と pose（鎖の登録 recorder.chain が作る。H_plane は無く、camera と visible_faces と
via・check（再投影 px・点の数・面ごとの点）だけ）。読む側は expand_entries で面ごとの H_plane（camera から）にそろえる。
"""

from __future__ import annotations

import copy
import json
import math
import os
from datetime import datetime
from pathlib import Path

import numpy as np

from .photos import INDEX_PATH, PHOTO_DIR, load_index
from .schema import face_ground_offset, load_building

REGISTRY_PATH = PHOTO_DIR / "registry.json"
BUILDINGS_DIR = Path(__file__).resolve().parent.parent / "data" / "buildings"
SCHEMA = "campus-recorder/registry/1"
RESOLVE_PX = 2.0         # 既存の pose と最新の compare の射影がこれ以上違えば、最新の compare から視点を解き直す


# --- 台帳の読み書き -----------------------------------------------------------------

# 鍵の持ち方（11/8 J-012 の直し）：「読む → 計算 → 鍵を取って読み直し → 差分を書く」。
#   load_registry は読んだ時の台帳の写し（土台）を覚えておく。計算（特徴点の照合・SfM・PnP）は鍵の外で、手元の台帳を
#   書き換えるだけ。save_registry が ledger の鍵を取り、ファイルを読み直し、「土台 → 手元」で変わった所だけを今の
#   ファイルに当てて書く。鍵を持つのは読み直しと書き込みの間だけ（計算の時間を含まない）。
#   差分の単位：entries と not_registered は (棟, 写真, 面)、excluded は (棟, 写真)、それ以外の一覧は要素そのもの、
#   それ以外の値はキーごと。同じ単位を自分も他のプロセスも変えていたら、自分の値を書く（後勝ち。数を返す）。
#   load_registry を通らない台帳（手で作った dict）は、従来どおり丸ごと書く。

_BASE: dict[int, tuple[dict, str, dict]] = {}     # id(reg) → (reg, 台帳のパス, 読んだ時の写し)


def _resolved(path) -> str:
    return str(Path(path).resolve())


def _empty_registry() -> dict:
    return {"schema": SCHEMA, "note": "写真の登録（面の平面→写真の射影と視点）。recheck・coverage が読む。写真は git に入れない",
            "entries": [], "not_registered": []}


def _read_registry(path) -> dict | None:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def load_registry(path: Path = REGISTRY_PATH) -> dict:
    reg = _read_registry(path) or _empty_registry()
    _BASE[id(reg)] = (reg, _resolved(path), copy.deepcopy(reg))
    return reg


def _key_bpf(x: dict) -> tuple:
    return (x.get("building"), x.get("photo"), x.get("face"))


def _key_bp(x: dict) -> tuple:
    return (x.get("building"), x.get("photo"))


_LIST_KEYS = {"entries": _key_bpf, "not_registered": _key_bpf, "excluded": _key_bp}


def _js(v) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


def _merge_list(base: list, mine: list, cur: list, kf) -> tuple[list, int, list]:
    def group(lst):
        d: dict = {}
        for it in lst or []:
            d.setdefault(kf(it), []).append(it)
        return d
    B, M, C = group(base), group(mine), group(cur)
    decided, conflicts = {}, []
    for k in list(B) + [k for k in M if k not in B]:
        if _js(B.get(k)) != _js(M.get(k)):                     # 自分が変えた単位
            if _js(C.get(k)) != _js(B.get(k)) and _js(C.get(k)) != _js(M.get(k)):
                conflicts.append(list(k) if isinstance(k, tuple) else k)
            decided[k] = M.get(k)
    out, seen = [], set()
    for it in cur or []:
        k = kf(it)
        if k in seen:
            continue
        seen.add(k)
        out += (decided[k] or []) if k in decided else C[k]
    for k, v in decided.items():
        if k not in seen and v:
            out += v
    return out, len(decided), conflicts


def merge_registry(base: dict, mine: dict, cur: dict) -> tuple[dict, dict]:
    """3 方向の合わせ：cur（今のファイル）に、base → mine の差分だけを当てる。返り値 (合わせた台帳, 数)。"""
    out = copy.deepcopy(cur)
    stats = {"changed": 0, "conflicts": []}
    for k in set(base) | set(mine):
        bv, mv = base.get(k), mine.get(k)
        if isinstance(mv, list) and (bv is None or isinstance(bv, list)) and isinstance(out.get(k, []), list):
            kf = _LIST_KEYS.get(k, _js)
            merged, n, conf = _merge_list(bv or [], mv, out.get(k) or [], kf)
            out[k] = merged
            stats["changed"] += n
            stats["conflicts"] += [[k, c] for c in conf]
        elif _js(bv) != _js(mv):
            if k in mine:
                out[k] = copy.deepcopy(mv)
            else:
                out.pop(k, None)
            stats["changed"] += 1
    return out, stats


def save_registry(reg: dict, path: Path = REGISTRY_PATH, label: str | None = None) -> dict:
    """登録台帳を書く。ledger の鍵を取ってファイルを読み直し、load_registry の時からの差分だけを当てて書く
    （一時ファイルに書いてから置き換える）。手元の reg も書いた中身にそろえる。返り値 {"changed", "conflicts", "mode"}。"""
    from .ledger import lock, write_text_atomic
    rp = _resolved(path)
    base_rec = _BASE.get(id(reg))
    base = base_rec[2] if base_rec and base_rec[0] is reg and base_rec[1] == rp else None
    with lock("ledger", label=label or f"registry save {Path(path).name}"):
        cur = _read_registry(path)
        if base is None or cur is None:
            merged, stats = reg, {"changed": None, "conflicts": [], "mode": "whole"}
        else:
            merged, stats = merge_registry(base, reg, cur)
            stats["mode"] = "diff"
        merged.setdefault("entries", []).sort(key=lambda e: (e["building"], e["face"], e["photo"]))
        write_text_atomic(path, json.dumps(merged, ensure_ascii=False, indent=1) + "\n")
    if merged is not reg:
        reg.clear()
        reg.update(merged)
    _BASE[id(reg)] = (reg, rp, copy.deepcopy(reg))
    return stats


ANCHOR_ENV = "RECORDER_ANCHOR_CHECK"       # "0" で周期の面の原点の確かめを切る（試しの時だけ）


def upsert(reg: dict, entry: dict, b: dict | None = None) -> bool:
    """同じ写真・同じ面の登録は置き換える。登録できたら not_registered から外す。返り値：台帳に入れたか。
    入れない時（台帳の excluded にある写真／周期の面で原点が曖昧 J-027）は、前の登録はそのまま残し、理由を
    not_registered に書く（excluded_reason・anchor_reason）。b（棟 JSON）が無ければ data/buildings/<棟>.json を読む。"""
    key = (entry["building"], entry["photo"], entry["face"])
    ex = next((x for x in reg.get("excluded", []) if x.get("building") == entry["building"]
               and _same_photo(entry["photo"], x.get("photo") or "")), None)
    if ex is not None:
        _note_refused(reg, entry, f"photos exclude で外した写真（{ex.get('why', '')}）。登録し直すには photos exclude --lift",
                      "excluded", "excluded")
        return False
    if os.environ.get(ANCHOR_ENV, "1") != "0":
        bj = b if b is not None else _building_or_none(entry["building"])
        if bj is not None:
            chk = anchor_check(entry, bj)
            entry.setdefault("check", {})
            if isinstance(entry["check"], dict):
                entry["check"]["anchor"] = {k: v for k, v in chk.items() if k != "ok"}
            if not chk["ok"]:
                _note_refused(reg, entry, chk["reason"], chk["code"], "anchor")
                return False
    reg["entries"] = [e for e in reg["entries"] if (e["building"], e["photo"], e["face"]) != key]
    reg["entries"].append(entry)
    reg["not_registered"] = [n for n in reg.get("not_registered", [])
                             if (n.get("building"), n.get("photo"), n.get("face")) != key]
    return True


def _same_photo(full: str, q: str) -> bool:
    """写真 id が同じか（q は頭か末尾だけでも可）。"""
    return bool(q) and (full == q or full.startswith(q) or full.endswith(q))


def _note_refused(reg: dict, entry: dict, reason: str, code: str, prefix: str) -> None:
    nr = reg.setdefault("not_registered", [])
    n = next((x for x in nr if _key_bp(x) == (entry["building"], entry["photo"])), None)
    if n is None:
        n = {"photo": entry["photo"], "building": entry["building"], "face": entry.get("face"), "period": entry.get("period")}
        nr.append(n)
    n[f"{prefix}_reason"] = reason
    n[f"{prefix}_code"] = code
    n[f"{prefix}_at"] = datetime.now().isoformat(timespec="seconds")


def _building_or_none(building_id: str) -> dict | None:
    p = BUILDINGS_DIR / f"{building_id}.json"
    if not p.exists():
        return None
    try:
        return load_building(p)
    except Exception:  # noqa: BLE001 — 読めない棟 JSON では確かめない（登録の道具を止めない）
        return None


def note_not_registered(reg: dict, building_id: str, photo_id: str, index: dict | None, reason: str, code: str,
                        prefix: str) -> None:
    """登録できなかった写真の理由を not_registered に書く（鎖・SfM の共用）。既にあれば <prefix>_reason・<prefix>_code を足す。
    登録済みの写真には書かない。"""
    if any(e["building"] == building_id and e["photo"] == photo_id for e in reg.get("entries", [])):
        return
    nr = reg.setdefault("not_registered", [])
    n = next((x for x in nr if x.get("building") == building_id and x.get("photo") == photo_id), None)
    if n is None:
        rec = photo_record(photo_id, index) or {}
        n = {"photo": photo_id, "building": building_id, "face": rec.get("face"), "period": rec.get("period")}
        nr.append(n)
    n[f"{prefix}_reason"] = reason
    n[f"{prefix}_code"] = code


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


FACE_TOP_FALLBACK_M = 20.0     # 棟 JSON に上端の手がかりが 1 つも無い時だけの仮の値（警告を出す）
_BLOCK_END_TOL_M = 0.5         # 面の両端が塊の頂点とこの距離以内なら、その塊の面と見なす（schema._face_top_values と同じ）


def _mval(m) -> float | None:
    """寸法項目（{value, status}）か数から値。unknown・値なしは None。"""
    if isinstance(m, bool):
        return None
    if isinstance(m, (int, float)):
        return float(m)
    if isinstance(m, dict) and m.get("status") != "unknown" and m.get("value") is not None:
        try:
            return float(m["value"])
        except (TypeError, ValueError):
            return None
    return None


def face_top_source(b: dict, f: dict) -> tuple[float, str]:
    """面の上端の高さ（棟の z=0＝ground_z からの m）と、どこから引いたか。
    優先：面の top_z → 面の両端を頂点に持つ塊（blocks）の top_z（複数なら高い方）→ 棟の parapet_top
    → floor_count × floor_pitch → どれも無ければ 20 m（仮。警告を出す）。
    高さは棟の z=0 からなので ground_z は足さない（parapet_top・塊の top_z と同じ基準）。"""
    v = _mval(f.get("top_z"))
    if v is not None:
        return v, "face.top_z"
    ends = [f.get("from"), f.get("to")]
    if all(e and len(e) >= 2 for e in ends):
        tops = []
        for blk in b.get("blocks") or []:
            pts = blk.get("polygon") or []
            hit = all(any(abs(float(p[0]) - float(e[0])) < _BLOCK_END_TOL_M and abs(float(p[1]) - float(e[1])) < _BLOCK_END_TOL_M
                          for p in pts) for e in ends)
            t = _mval(blk.get("top_z")) if hit else None
            if t is not None:
                tops.append((t, blk.get("id")))
        if tops:
            t, bid = max(tops, key=lambda x: x[0])
            return t, f"blocks[{bid}].top_z"
    v = _mval(b.get("parapet_top"))
    if v is not None:
        return v, "parapet_top"
    n, pitch = _mval(b.get("floor_count")), _mval(b.get("floor_pitch"))
    if n is not None and pitch is not None and n > 0 and pitch > 0:
        return n * pitch, "floor_count×floor_pitch"
    import warnings
    warnings.warn(f"棟 {b.get('id')} の面 {f.get('id')}：上端の高さ（面・塊の top_z、parapet_top、floor_count×floor_pitch）が無いので "
                  f"仮に {FACE_TOP_FALLBACK_M:g} m で描きます。目印の高さはずれます", stacklevel=3)
    return FACE_TOP_FALLBACK_M, "fallback"


def face_top(b: dict, f: dict) -> float:
    """面の上端の高さ（棟の z=0 からの m）。引き方は face_top_source。"""
    return face_top_source(b, f)[0]


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
    from .photos import short_name
    c = [out_dir / "compare_v2" / f"{stem}_{face}_compare.json",
         out_dir / "compare" / f"{stem}_{face}_compare_kenshu.json",
         out_dir / "compare" / f"{short_name(stem)}_{face}_compare_kenshu.json",
         out_dir / "compare" / f"{stem}_{face}_compare.json"]
    # 前の形（日本語名の写真の頭の語「本部棟正面_…」→「本部棟正面」）。英数字だけの頭（IMG・DSC・日付）は
    # 別の写真と同じ名前になるので使わない（11/8 F3）
    head = stem.split("_")[0]
    if head != stem and not head.isascii():
        c.insert(3, out_dir / "compare" / f"{head}_{face}_compare_kenshu.json")
    return list(dict.fromkeys(p for p in c if p.exists()))


def _front_of(compare_json: Path) -> Path | None:
    src = json.loads(compare_json.read_text(encoding="utf-8")).get("source") or ""
    p = Path(src.replace("\\", "/")).with_suffix(".json")
    return p if p.exists() else None


def _lines_of(out_dir: Path, compare_json: Path, stem: str, face: str) -> Path | None:
    from .photos import short_name
    # lines_<面>_<短い名前>.json（Pixel は時刻の数字。前からの形）。IMG_0001 の「0001」のような末尾だけでは
    # 別の機種の写真と重なるので、Pixel 以外は id のまま探す（11/8 F3）
    names = list(dict.fromkeys([short_name(stem), stem]))
    for d in (compare_json.parent, out_dir / "compare_v2", out_dir / "compare"):
        for nm in names:
            p = d / f"lines_{face}_{nm}.json"
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


# --- 登録の 2 つの形（plane と pose）-------------------------------------------------------
#
# kind "plane"（既定。旧い entry もこれ）：1 つの面の H_plane（正面化と compare から）＋camera。
# kind "pose" ：H_plane を持たない。camera（R_cv・t_cv・focal_px）と「見えている面の一覧」visible_faces だけ。
#               鎖の登録（recorder.chain）が作る。面の平面と camera があれば H は一意（H_from_pose）なので、
#               読む側（coverage・photoproj・recheck）は expand_entries で面ごとの plane の形に直してから使う。

MIN_VISIBLE_M2 = 2.0      # 写真の枠に入る面の範囲がこれ未満の面は「見えている面」に数えない


def entry_kind(e: dict) -> str:
    return e.get("kind") or "plane"


def face_rect_frame(b: dict, f: dict) -> dict:
    """今の面の frame（from・to・長さ・面の前の地面・上端）。"""
    frame = {"from": [float(v) for v in f["from"]], "to": [float(v) for v in f["to"]]}
    _, _, _, L = face_frame(frame)
    return {**frame, "length_m": round(L, 3), "ground_offset": face_ground_offset(f), "top_z": face_top(b, f)}


def camera_dict(R, t, focal_px: float, w: int, h: int, ground: float | None = None) -> dict:
    from .pose import cv_to_blender_numeric

    R, t = np.asarray(R, float), np.asarray(t, float).reshape(3)
    C = -R.T @ t
    return {"focal_px": float(focal_px), "image_size": [int(w), int(h)], "R_cv": [[float(v) for v in row] for row in R],
            "t_cv": [float(v) for v in t], "camera_xyz": [round(float(v), 2) for v in C],
            "camera_height_m": (round(float(C[2] - ground), 2) if ground is not None else None),
            "distance_to_face_m": None, "blender": cv_to_blender_numeric(R, t.reshape(3, 1), float(focal_px), int(w), int(h))}


def _occluded(C: np.ndarray, P: np.ndarray, rects: list[tuple], skip: str) -> np.ndarray:
    """カメラ C から点 P（N×3）への線分が、他の面の矩形（鉛直の板）を先に横切るか。"""
    occ = np.zeros(len(P), bool)
    d = P - C
    for fid, p0, u, L, z0, z1 in rects:
        if fid == skip:
            continue
        nrm = np.array([u[1], -u[0]])
        den = d[:, :2] @ nrm
        num = (p0 - C[:2]) @ nrm
        with np.errstate(divide="ignore", invalid="ignore"):
            lam = num / den
        X = C + lam[:, None] * d
        s = (X[:, :2] - p0) @ u
        occ |= (lam > 1e-3) & (lam < 1 - 1e-3) & (s > 0.05) & (s < L - 0.05) & (X[:, 2] >= z0) & (X[:, 2] <= z1)
    return occ


def _face_rects(b: dict) -> tuple[list[dict], list[tuple]]:
    faces = [f for f in b.get("faces", []) if f.get("from") and f.get("to")]
    rects = []
    for f in faces:
        fr = face_rect_frame(b, f)
        p0, u, _, L = face_frame(fr)
        rects.append((f["id"], p0, u, L, fr["ground_offset"], fr["top_z"]))
    return faces, rects


GRID_S, GRID_Z = 41, 21     # 面の見え方を調べる格子（沿う向き × 高さ）


def _face_view(b: dict, cam: dict, f: dict, rects: list[tuple]) -> dict | None:
    """camera から面 f を見た時の格子（s 41 × z 21 の点）：写真の枠の中で前にある点 inside と、そのうち棟の他の面
    （鉛直の板）に隠れない点 seen。カメラが面の裏か面から 0.3 m 以内なら None。"""
    w, h = cam["image_size"]
    R, t = np.array(cam["R_cv"], float), np.array(cam["t_cv"], float).ravel()
    C = -R.T @ t
    fr = face_rect_frame(b, f)
    p0, _, nv, L = face_frame(fr)
    dist = float((C[:2] - p0) @ nv)
    if dist <= 0.3:
        return None
    H = H_from_pose(R, t, cam["focal_px"], w, h, fr)
    poly = cover_polygon(H, w, h, (0.0, L, fr["ground_offset"], fr["top_z"]))
    S, Z = np.meshgrid(np.linspace(0, L, GRID_S), np.linspace(fr["ground_offset"], fr["top_z"], GRID_Z))
    S, Z = S.ravel(), Z.ravel()
    uu, vv, ww = apply_H(H, S, Z)
    inside = (ww > 0.5) & (uu >= 0) & (uu < w) & (vv >= 0) & (vv < h)
    occ = np.zeros(len(S), bool)
    if inside.any():
        occ[inside] = _occluded(C, plane_to_world(fr, S[inside], Z[inside]), rects, f["id"])
    return {"fr": fr, "L": L, "dist": dist, "H": H, "poly": poly, "area": poly_area(poly), "S": S, "Z": Z,
            "inside": inside, "seen": inside & ~occ}


def _visible(v: dict | None, min_m2: float) -> bool:
    if v is None or v["area"] < min_m2 or not v["inside"].any():
        return False
    return v["area"] * float(v["seen"].sum() / v["inside"].sum()) >= min_m2


def visible_faces(b: dict, cam: dict, min_m2: float = MIN_VISIBLE_M2) -> list[dict]:
    """camera から見える面：カメラが面の外側にいて、写真の枠に入り、棟の他の面（鉛直の板とみなす）に隠れない範囲が
    min_m2 以上の面。部品（階段・庇）や木による隠れは見ていない。"""
    faces, rects = _face_rects(b)
    out = []
    for f in faces:
        v = _face_view(b, cam, f, rects)
        if not _visible(v, min_m2):
            continue
        free = float(v["seen"].sum() / v["inside"].sum())
        out.append({"face": f["id"], "area_m2": round(v["area"] * free, 2), "distance_m": round(v["dist"], 2),
                    "unoccluded": round(free, 2)})
    return out


def hidden_rects(v: dict) -> list[dict]:
    """面の格子のうち、写真の枠の中なのに棟の他の面に隠れる点を、面の m の矩形 {"rect": [s0, s1, z0, z1], "why"} に
    まとめる（縦の列ごとに続く所を 1 つにし、隣の列と同じ高さなら横につなぐ）。"""
    occ = (v["inside"] & ~v["seen"]).reshape(GRID_Z, GRID_S)
    fr = v["fr"]
    z0f, z1f = fr["ground_offset"], fr["top_z"]
    ds, dz = v["L"] / (GRID_S - 1), (z1f - z0f) / (GRID_Z - 1)
    runs_by_col = []
    for i in range(GRID_S):
        col, runs, j = occ[:, i], [], 0
        while j < GRID_Z:
            if col[j]:
                k = j
                while k + 1 < GRID_Z and col[k + 1]:
                    k += 1
                runs.append((j, k))
                j = k + 1
            else:
                j += 1
        runs_by_col.append(runs)
    out, open_ = [], {}
    for i, runs in enumerate(runs_by_col + [[]]):
        for r in list(open_):
            if r not in runs:
                i0 = open_.pop(r)
                out.append({"rect": [round(max(0.0, (i0 - 0.5) * ds), 2), round(min(v["L"], (i - 0.5) * ds), 2),
                                     round(max(z0f, z0f + (r[0] - 0.5) * dz), 2), round(min(z1f, z0f + (r[1] + 0.5) * dz), 2)],
                            "why": "隠れ：棟の他の面の陰（registry の判定、カメラから面への線が先に他の面を横切る）"})
        for r in runs:
            open_.setdefault(r, i)
    return out


def expand_entries(ents: list[dict], b: dict) -> list[dict]:
    """登録を面ごとの plane の形にそろえる。plane の entry はそのまま（H_plane は正面化から作った精しい物）。
    pose の entry は、今の棟 JSON の面で camera から見え直した面（visible_faces と同じ判定：枠に入り、棟の他の面に
    隠れない所が 2 m² 以上）の各面について、camera から H_plane を計算した仮の entry にする（kind "pose_face"、
    derived_from に元の entry の面）。登録の時の visible_faces は使わない（面が後で動くと、見えない面に貼られる。J-013）。
    隠れた所は "hidden"（面の m の矩形）と "unoccluded"（隠れない割合）に入れる。exclude（人が書いた手前の物）とは
    分ける（exclude は写真の画素のマスクにも使われるので、隠した側の面の画素まで消さないため）。"""
    faces, rects = _face_rects(b)
    out = []
    for e in ents:
        if entry_kind(e) != "pose":
            out.append(e)
            continue
        cam = dict(e.get("camera") or {})
        if not cam.get("R_cv"):
            continue
        cam["image_size"] = cam.get("image_size") or e["image_size"]
        for f in faces:
            v = _face_view(b, cam, f, rects)
            if not _visible(v, MIN_VISIBLE_M2) or len(v["poly"]) < 3:
                continue
            fid = f["id"]
            out.append({**{k: x for k, x in e.items() if k not in ("visible_faces", "exclude", "exclude_note", "exclude_by_face")},
                        "kind": "pose_face", "face": fid, "derived_from": e["face"],
                        "H_plane": [[float(x) for x in row] for row in v["H"]], "face_frame": v["fr"],
                        "cover": {"type": "polygon", "pts": [list(p) for p in v["poly"]], "area_m2": round(v["area"], 2)},
                        "exclude": (e.get("exclude_by_face") or {}).get(fid, []),
                        "hidden": hidden_rects(v),
                        "unoccluded": round(float(v["seen"].sum() / v["inside"].sum()), 2)})
    return out


# --- 周期の面の原点（J-027）-----------------------------------------------------------------
# 柱割りがくり返す面では、柱型の線が合っても「何本目の柱か」は決まらない（本部棟 f6 の 3 枚が柱割り 4 つ分ずれた）。
# 登録を台帳に入れる前に、面の端（角・妻）か、その面に 1 つしか無い部品（玄関・通り抜けなど）が対応に入っているかを見る。
#   目印の登録（landmarks）：目印係が指した点・線（inlier）の面の上の位置 s で見る（対応そのもの）。
#   それ以外（鎖・SfM・正面化）：写真に写る面の範囲（他の面に隠れない格子の点、plane は cover）の s で見る。
#     これは「写っている」までしか言えない（必要条件）。鎖の内点の位置が台帳に無いため（chain.py が持てば対応で見られる）。
# 周期の面が 1 つも写らないか、周期でない面も見えている（その面が位置を決める）なら確かめは通る。

ANCHOR_MIN_BAYS = 3             # 柱割りがこの数以上の面を「周期の面」とする


def periodic_faces(b: dict) -> dict[str, dict]:
    """柱割り（bays.count）が ANCHOR_MIN_BAYS 以上の面 → {"bays", "length_m", "pitch_m"}。"""
    out = {}
    for f in b.get("faces", []):
        if not (f.get("from") and f.get("to")):
            continue
        bays = f.get("bays")
        n = _mval(bays.get("count")) if isinstance(bays, dict) else (bays if isinstance(bays, (int, float)) else None)
        if n is None or n < ANCHOR_MIN_BAYS:
            continue
        _, _, _, L = face_frame(f)
        out[f["id"]] = {"bays": int(n), "length_m": round(L, 2), "pitch_m": round(L / n, 2)}
    return out


def unique_marks(b: dict, face_id: str) -> list[tuple[float, float, str]]:
    """面にその種類が 1 つしか無い部品・開口の範囲 (s0, s1, 名前)。along が無い物は入れない。"""
    parts = [p for p in b.get("parts", []) if p.get("face") == face_id]
    f = next((x for x in b.get("faces", []) if x.get("id") == face_id), {})
    ops = [dict(o, type="opening:" + str(o.get("type") or o.get("kind") or o.get("name") or "")) for o in f.get("openings") or []
           if isinstance(o, dict)]
    by_type: dict = {}
    for p in parts + ops:
        by_type.setdefault(p.get("type"), []).append(p)
    out = []
    for typ, ps in by_type.items():
        if len(ps) != 1:
            continue
        p = ps[0]
        s0 = _mval(p.get("along"))
        if s0 is None:
            continue
        wv = _mval(p.get("width")) or 0.0
        out.append((float(s0), float(s0 + wv), str(p.get("name") or typ)))
    return out


def _anchored_s(svals, L: float, tol: float, marks: list[tuple]) -> str | None:
    svals = [float(x) for x in svals]
    if not svals:
        return None
    if min(svals) <= tol:
        return "面の始点の端"
    if max(svals) >= L - tol:
        return "面の終点の端"
    lo, hi = min(svals), max(svals)
    for s0, s1, name in marks:                 # 部品の中心が写る範囲（s の最小〜最大）に入る
        if lo - tol <= (s0 + s1) / 2 <= hi + tol:
            return f"唯一の部品「{name}」"
    return None


def _landmark_s(b: dict, entry: dict) -> dict[str, list[float]] | None:
    src = entry.get("source") or {}
    if src.get("kind") != "landmarks":
        return None
    try:
        from .landmarks import catalog
        cat = {m["id"]: m for m in catalog(b) + catalog(b, lines=True)}
    except Exception:  # noqa: BLE001
        return None
    out: dict[str, list[float]] = {}
    for p in (src.get("picks") or []) + (src.get("lines") or []):
        if p.get("inlier") is False:
            continue
        m = cat.get(p.get("id"))
        if m and m.get("s") is not None:
            out.setdefault(m["face"], []).append(float(m["s"]))
    return out


def anchor_check(entry: dict, b: dict) -> dict:
    """登録が周期の面の「何本目」を決められるか。返り値 {"ok", "code", "reason", "by"}。"""
    per = periodic_faces(b)
    fmap = {f["id"]: f for f in b.get("faces", []) if f.get("from") and f.get("to")}
    if not per:
        return {"ok": True, "code": "no_periodic_face", "reason": "棟に周期の面が無い", "by": None}
    cover_s: dict[str, list[float]] = {}
    if entry_kind(entry) == "pose" and (entry.get("camera") or {}).get("R_cv"):
        cam = dict(entry["camera"])
        cam["image_size"] = cam.get("image_size") or entry.get("image_size")
        faces, rects = _face_rects(b)
        for f in faces:
            v = _face_view(b, cam, f, rects)
            if _visible(v, MIN_VISIBLE_M2):
                cover_s[f["id"]] = [float(x) for x in v["S"][v["seen"]]]
    elif entry.get("face") in fmap:
        pts = (entry.get("cover") or {}).get("pts") or []
        cover_s[entry["face"]] = [float(q[0]) for q in pts]
    seen = list(cover_s)
    if not any(fid in per for fid in seen):
        return {"ok": True, "code": "not_periodic", "reason": f"写る面 {seen} に周期の面が無い", "by": None}
    other = [fid for fid in seen if fid not in per]
    if other:
        return {"ok": True, "code": "non_periodic_face_seen", "reason": f"周期でない面 {other} も写る", "by": other[0]}
    lm = _landmark_s(b, entry)
    inl = (entry.get("check") or {}).get("inlier_s_by_face") if isinstance(entry.get("check"), dict) else None
    if lm is not None:
        use, how = lm, "目印の対応"
    elif inl:                                  # 鎖の登録：視点に合った点の面の上の位置（chain.register_via が書く）
        use, how = inl, "鎖の内点"
    else:
        use, how = cover_s, "写る範囲"
    for fid in seen:
        L, pitch = per[fid]["length_m"], per[fid]["pitch_m"]
        tol = max(0.5, 0.25 * pitch)
        got = _anchored_s(use.get(fid, []), L, tol, unique_marks(b, fid))
        if got:
            return {"ok": True, "code": "anchored", "reason": f"{fid} の{got}が{how}に入る", "by": fid}
    desc = "・".join(f"{fid}（柱割り {per[fid]['bays']}、{per[fid]['pitch_m']} m）" for fid in seen)
    return {"ok": False, "code": "origin_ambiguous",
            "reason": (f"原点が曖昧：周期の面 {desc} で、面の端（角・妻）もその面に 1 つしか無い部品も{how}に入らない。"
                       "柱割りの何本目に当たるか決められない（J-027）。端か玄関などが写る写真と一緒に登録する"),
            "by": None}


# --- 登録を外す・写真を使わない（J-013・J-027）----------------------------------------------

def _match_entries(reg: dict, photo: str, building: str | None = None, face: str | None = None) -> list[dict]:
    hit = [e for e in reg.get("entries", []) if (building is None or e["building"] == building)
           and (face is None or e["face"] == face)]
    exact = [e for e in hit if e["photo"] == photo]
    if exact:
        return exact
    part = [e for e in hit if e["photo"].startswith(photo) or e["photo"].endswith(photo)]
    names = sorted({e["photo"] for e in part})
    if len(names) > 1:
        raise ValueError(f"写真 id {photo} が {len(names)} 枚に当たる：{', '.join(names[:6])}")
    return part


def unregister(reg: dict, photo: str, why: str, building: str | None = None, face: str | None = None,
               by: str = "") -> list[dict]:
    """登録を台帳から外す（写真・棟・面で絞る）。外した登録は reg["unregistered"] に理由・日時と一緒に残す
    （元に戻す時の手がかり。recheck・coverage は読まない）。写真を使わなくはしない（次の register-chain で試し直す）。"""
    if not why:
        raise ValueError("外す理由（--why）が要る")
    gone = _match_entries(reg, photo, building, face)
    if not gone:
        return []
    ids = {id(e) for e in gone}
    reg["entries"] = [e for e in reg["entries"] if id(e) not in ids]
    at = datetime.now().isoformat(timespec="seconds")
    hist = reg.setdefault("unregistered", [])
    for e in gone:
        hist.append({"photo": e["photo"], "building": e["building"], "face": e["face"], "kind": entry_kind(e),
                     "why": why, "at": at, "by": by, "entry": e})
    return gone


def exclude(reg: dict, photo: str, why: str, building: str | None = None, face: str | None = None,
            rect: list[float] | None = None, by: str = "") -> dict:
    """写真を使わない。
      rect なし：写真ごと使わない。登録を外し（unregistered に残す）、reg["excluded"] に書く。以後 upsert は
                 この写真の登録を入れない（鎖・SfM・目印で試し直しても台帳に入らない。理由は not_registered に）。
      rect あり（--face が要る）：その面の m の矩形（[s0, s1, z0, z1]、木・車・手前の物）だけを使わない。plane の登録は
                 exclude に、pose の登録は exclude_by_face[面] に {"rect", "why"} で足す（coverage・photoproj が読む）。"""
    if not why:
        raise ValueError("使わない理由（--why）が要る")
    at = datetime.now().isoformat(timespec="seconds")
    if rect is not None:
        if not face:
            raise ValueError("矩形で外すには --face が要る")
        if len(rect) != 4:
            raise ValueError("--rect は s0,s1,z0,z1（面の m）の 4 つ")
        ents = _match_entries(reg, photo, building)
        if not ents:
            raise ValueError(f"写真 {photo} の登録が無い")
        item = {"rect": [float(v) for v in rect], "why": why, "at": at, "by": by}
        done = []
        for e in ents:
            if entry_kind(e) == "pose":
                e.setdefault("exclude_by_face", {}).setdefault(face, []).append(item)
                done.append(e)
            elif e["face"] == face:
                e.setdefault("exclude", []).append(item)
                done.append(e)
        if not done:
            raise ValueError(f"写真 {photo} に面 {face} の登録が無い（plane の登録は面ごと）")
        return {"mode": "rect", "photo": done[0]["photo"], "face": face, "entries": len(done)}
    ents = _match_entries(reg, photo, building)
    blds = sorted({e["building"] for e in ents}) or ([building] if building else [])
    if not blds:
        raise ValueError(f"写真 {photo} の登録が無い。登録の前に外すなら --building を付ける")
    if ents:
        pid = ents[0]["photo"]
    else:                                      # 登録が無い写真は、索引か外した記録で id を全部にそろえる
        try:
            rec = photo_record(photo) or {}
        except Exception:  # noqa: BLE001
            rec = {}
        hist = [h["photo"] for h in reg.get("unregistered", []) if _same_photo(h["photo"], photo)]
        pid = rec.get("id") or (hist[-1] if hist else photo)
    if ents:
        unregister(reg, pid, f"exclude: {why}", building, None, by)
    ex = reg.setdefault("excluded", [])
    for bid in blds:
        ex[:] = [x for x in ex if _key_bp(x) != (bid, pid)]
        ex.append({"photo": pid, "building": bid, "why": why, "at": at, "by": by})
    return {"mode": "photo", "photo": pid, "buildings": blds, "unregistered": len(ents)}


def excluded_ids(reg: dict, building_id: str) -> set[str]:
    """写真ごと使わない写真の id（鎖・SfM・locate の候補選びが外す）。"""
    return {x["photo"] for x in reg.get("excluded", []) if x.get("building") == building_id}


def lift_exclude(reg: dict, photo: str, building: str | None = None) -> int:
    """写真ごとの除外をやめる（登録は戻さない。次の登録で入る）。"""
    ex = reg.get("excluded", [])
    keep = [x for x in ex if not ((x["photo"] == photo or x["photo"].startswith(photo) or x["photo"].endswith(photo))
                                  and (building is None or x["building"] == building))]
    n = len(ex) - len(keep)
    reg["excluded"] = keep
    return n


def add_cli(psub) -> None:
    """recorder photos unregister／exclude の引数（__main__ の photos のサブコマンドに足す）。"""
    q = psub.add_parser("unregister", help="登録を台帳から外す（理由は unregistered[] に残る。写真は次の登録で試し直す）")
    q.add_argument("photo", help="写真 id（頭か末尾だけでも可）")
    q.add_argument("--why", required=True, help="外す理由")
    q.add_argument("--building", default=None)
    q.add_argument("--face", default=None)
    q.add_argument("--by", default=None, help="外した係")
    q.add_argument("--registry", default=None)
    q = psub.add_parser("exclude", help="写真を使わない：写真ごと（以後登録しない）か、--face と --rect で面の矩形だけ")
    q.add_argument("photo", help="写真 id（頭か末尾だけでも可）")
    q.add_argument("--why", default=None, help="使わない理由（--lift 以外は必須）")
    q.add_argument("--building", default=None)
    q.add_argument("--face", default=None)
    q.add_argument("--rect", default=None, help="面の m の矩形 s0,s1,z0,z1（--face と一緒に）")
    q.add_argument("--lift", action="store_true", help="写真ごとの除外をやめる")
    q.add_argument("--by", default=None)
    q.add_argument("--registry", default=None)


def run_cli(a) -> int:
    """recorder photos unregister／exclude（__main__ から呼ぶ）。台帳は save_registry が鍵を取って差分で書く。"""
    rp = Path(a.registry) if getattr(a, "registry", None) else REGISTRY_PATH
    reg = load_registry(rp)
    try:
        if a.photos_cmd == "unregister":
            gone = unregister(reg, a.photo, a.why, a.building, a.face, by=a.by or "")
            if not gone:
                print(f"外す登録が無い（写真 {a.photo}" + (f"・棟 {a.building}" if a.building else "")
                      + (f"・面 {a.face}" if a.face else "") + "）")
                return 1
            save_registry(reg, rp, label=f"recorder photos unregister {a.photo}")
            for e in gone:
                print(f"外した: {e['photo']}  {e['building']} {e['face']}（{entry_kind(e)}）")
            print(f"理由: {a.why}  記録: unregistered[]  台帳: {rp}")
            return 0
        if a.lift:
            n = lift_exclude(reg, a.photo, a.building)
            save_registry(reg, rp, label=f"recorder photos exclude --lift {a.photo}")
            print(f"除外をやめた: {n} 件（登録は戻していない。次の register-chain などで入る）  台帳: {rp}")
            return 0 if n else 1
        rect = [float(v) for v in a.rect.split(",")] if a.rect else None
        r = exclude(reg, a.photo, a.why or "", a.building, a.face, rect, by=a.by or "")
        save_registry(reg, rp, label=f"recorder photos exclude {a.photo}")
        if r["mode"] == "rect":
            print(f"使わない範囲を足した: {r['photo']} 面 {r['face']} {rect}（登録 {r['entries']} 件）  台帳: {rp}")
        else:
            print(f"写真ごと使わない: {r['photo']} 棟 {', '.join(r['buildings'])}（外した登録 {r['unregistered']}）  台帳: {rp}")
        return 0
    except ValueError as ex:
        print(f"できません: {ex}")
        return 1
