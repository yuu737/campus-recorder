"""目印の対応で写真の視点を解く（登録の本線。11/6）。

流れ
    1. 棟 JSON から目印の一覧を出す（面ごと：面の端・柱型の左縁・部品の縁 × 地面・パラペット・一番下の窓の下端・一番上の窓の上端）。
       `out/<棟>/landmarks/<面>.png`（番号付きの立面の絵）と `landmarks.json`（id・名前・3D）。
    2. 写真に 10×10 の升目（列 A〜J・行 1〜10）を引いた絵 `<写真>_grid.jpg` を出す。目印係（係か人）が「目印 id → 升」を指す。
    3. 升を 1.25 倍に広げて切り出し、10×10 の升目を引いた寄りの絵 `<写真>_<升>.jpg` を出す。係が寄りの升を指す（幅の約 1.5 %）。
    4. 対応 4 点以上 → PnP（cv2.solvePnP、焦点は EXIF を初期値に探索）→ 再投影の残差・カメラ高さ・見える面で合否 → 台帳に kind "pose"。

根拠（docs/日誌.md 11/6、本部棟の登録済み 17 枚）：対応の誤差が幅の 1.5 % なら位置の誤差の中央値 1.0 m（8 枚中 7 枚が 2 m 以内）、3 % で 1.5 m。
GPS だけの探索（recorder/locate.py）は使えなかった。消失点の仰角（locate.EdgeDT.estimate_tilt）は検算に使う。
写真どうしの重なりは要らない。登録が 3 枚できた塊は一括復元（sfm）の基準になる。
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path

import numpy as np

from . import registry as R_
from .compare import face_lines_detail
from .photos import INDEX_PATH, load_index
from .registry import face_frame, face_rect_frame, plane_to_world
from .schema import face_ground_offset, load_building

GRID_N = 10                 # 升目の数（縦横）
COLS = "ABCDEFGHIJ"
CROP_EXPAND = 0.25          # 寄りの切り出しで升を各側にこの割合だけ広げる（1.5 升 → 寄りの 1 升＝元の 0.15 升＝幅の 1.5 %）
GRID_LONG = 1600            # 升目の絵の長辺
MAX_REPROJ_FRAC = 0.02      # 合格：再投影の RMS が幅のこの割合まで
MIN_POINTS = 4
MIN_CAM_HEIGHT, MAX_CAM_HEIGHT = 0.5, 12.0
PITCH_WARN_DEG = 8.0        # 消失点の仰角との差がこれを超えたら警告
MIN_BBOX_FRAC = 0.25        # 対応の点が写真の幅・高さのこの割合以上に散っていること（一直線・一塊では解が暴れる）
EDGE_MIN_INLIER = 0.15      # 検算：解いた視点で模型の線の点が写真の縁 3 px 以内に乗る割合（本部棟の正しい登録は 0.17〜0.46、偽の合格は 0.09）
EDGE_MIN_GRAD = 0.25        # 検算：線に直角な勾配の平均（正しい登録 0.25〜0.44。どちらか一方を満たせば通す）
ELEV_PPM = 24               # 立面の絵の px/m
MAX_WINDOW_ROWS = 4         # 目印に使う窓の下端の列の数（等間隔に選ぶ）


# --- 目印の一覧 ------------------------------------------------------------------------------

def _v(m):
    if isinstance(m, (int, float)) and not isinstance(m, bool):
        return float(m)
    if isinstance(m, dict) and m.get("status") != "unknown" and m.get("value") is not None:
        return float(m["value"])
    return None


def face_landmarks(b: dict, face_id: str) -> list[dict]:
    """1 つの面の目印：縦の線（面の端・柱型の左縁・部品の縁）× 横の線（地面・パラペット・一番下の窓の下端・一番上の窓の上端）。
    返り値 [{"id": "SW-03", "face", "name", "s", "z", "xyz": [x, y, z]}]（面の平面上の点）。"""
    f = next((f for f in b.get("faces", []) if f["id"] == face_id and f.get("from") and f.get("to")), None)
    if f is None:
        return []
    fr = face_rect_frame(b, f)
    xs, zs, _ = face_lines_detail(b, face_id)
    verts: list[tuple[float, str]] = []
    for l in xs:
        if not l.in_plane:
            continue
        if l.kind == "landmark" or re.match(r"^柱型\d+左$", l.name) or (l.kind == "part" and "縁" in l.name):
            verts.append((float(l.m), l.name))
    horz: list[tuple[float, str]] = [(fr["ground_offset"], "地面"), (fr["top_z"], "上端（パラペット）")]
    lows = sorted((float(l.m), l.name) for l in zs if l.in_plane and l.name.endswith("下端") and "窓" in l.name)
    highs = sorted((float(l.m), l.name) for l in zs if l.in_plane and l.name.endswith("上端") and "窓" in l.name)
    # 窓の下端の列：一番下と、高い棟でも写真の縦の広がりが出るように、等間隔に最大 MAX_WINDOW_ROWS 本
    if lows:
        k = min(MAX_WINDOW_ROWS, len(lows))
        for i in sorted({int(round(j * (len(lows) - 1) / max(1, k - 1))) for j in range(k)}):
            horz.append(lows[i])
    if highs:
        horz.append(highs[-1])
    # 0.7 m 以内に並ぶ縦線（面の端と端の柱型の縁など）は 1 本にまとめる（面の端を優先）
    merged: list[tuple[float, str]] = []
    for s, sname in sorted(verts, key=lambda t: (t[0], 0 if "面の端" in t[1] else 1)):
        if merged and abs(s - merged[-1][0]) < 0.7:
            if "面の端" in sname and "面の端" not in merged[-1][1]:
                merged[-1] = (s, sname)
            continue
        merged.append((s, sname))
    verts = merged
    seen = set()
    out = []
    for s, sname in sorted(verts):
        for z, zname in horz:
            key = (round(s, 2), round(z, 2))
            if key in seen:
                continue
            seen.add(key)
            P = plane_to_world(fr, s, z).reshape(3)
            out.append({"id": f"{face_id}-{len(out) + 1:02d}", "face": face_id, "name": f"{sname} × {zname}",
                        "s": round(s, 3), "z": round(z, 3), "xyz": [round(float(v), 3) for v in P]})
    return out


def catalog(b: dict) -> list[dict]:
    out = []
    for f in b.get("faces", []):
        out += face_landmarks(b, f["id"])
    return out


def draw_face(b: dict, face_id: str, marks: list[dict], out_png: Path, ppm: int = ELEV_PPM) -> Path | None:
    """面の立面の絵（柱割りの線・窓の横線・目印の番号）。写真と見比べて目印の id を選ぶための物。"""
    import cv2

    f = next((f for f in b.get("faces", []) if f["id"] == face_id), None)
    if f is None or not marks:
        return None
    fr = face_rect_frame(b, f)
    L, g, top = fr["length_m"], fr["ground_offset"], fr["top_z"]
    pad = 40
    W, H = int(L * ppm) + 2 * pad, int((top - g) * ppm) + 2 * pad + 30
    img = np.full((H, W, 3), 255, np.uint8)

    def px(s, z):
        return int(pad + s * ppm), int(pad + (top - z) * ppm)

    cv2.rectangle(img, px(0, g), px(L, top), (60, 60, 60), 2)
    xs, zs, _ = face_lines_detail(b, face_id)
    for l in xs:
        if l.in_plane and 0 <= l.m <= L:
            cv2.line(img, px(l.m, g), px(l.m, top), (190, 190, 190), 1)
    for l in zs:
        if l.in_plane and g <= l.m <= top:
            cv2.line(img, px(0, l.m), px(L, l.m), (210, 180, 150), 1)
    for m in marks:
        u, v = px(m["s"], m["z"])
        cv2.circle(img, (u, v), 5, (0, 0, 230), -1)
        n = m["id"].split("-")[1]
        cv2.putText(img, n, (u + 4, v - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 180), 1, cv2.LINE_AA)
    cv2.putText(img, f"{b.get('id')} face {face_id}  from(left) -> to(right)  {L:.1f} m x {top - g:.1f} m", (pad, H - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".png", img)[1].tofile(str(out_png))
    return out_png


# --- 升目と寄り ------------------------------------------------------------------------------

def _grid_overlay(img: np.ndarray, label_prefix: str = "") -> np.ndarray:
    import cv2

    h, w = img.shape[:2]
    out = img.copy()
    for i in range(GRID_N + 1):
        x = int(round(i * w / GRID_N))
        y = int(round(i * h / GRID_N))
        cv2.line(out, (x, 0), (x, h - 1), (0, 255, 255), 1)
        cv2.line(out, (0, y), (w - 1, y), (0, 255, 255), 1)
    for i in range(GRID_N):
        x = int((i + 0.5) * w / GRID_N)
        y = int((i + 0.5) * h / GRID_N)
        cv2.putText(out, COLS[i], (x - 6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(out, str(i + 1), (3, y + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2, cv2.LINE_AA)
    if label_prefix:
        cv2.putText(out, label_prefix, (w // 2 - 40, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2, cv2.LINE_AA)
    return out


def parse_cell(cell: str) -> tuple[int, int]:
    """"C7" → (列 2, 行 6)。"""
    m = re.match(r"^\s*([A-Ja-j])\s*(\d{1,2})\s*$", cell or "")
    if not m:
        raise ValueError(f"升の書き方が違う: {cell!r}（例 C7）")
    col = COLS.index(m.group(1).upper())
    row = int(m.group(2)) - 1
    if not 0 <= row < GRID_N:
        raise ValueError(f"行は 1〜{GRID_N}: {cell!r}")
    return col, row


def cell_rect(cell: str, w: int, h: int, expand: float = 0.0) -> tuple[float, float, float, float]:
    """升の矩形（元の写真の px）。expand で各側に升の割合だけ広げる（枠の中に収める）。"""
    col, row = parse_cell(cell)
    cw, ch = w / GRID_N, h / GRID_N
    x0, y0 = col * cw - expand * cw, row * ch - expand * ch
    x1, y1 = (col + 1) * cw + expand * cw, (row + 1) * ch + expand * ch
    return max(0.0, x0), max(0.0, y0), min(float(w), x1), min(float(h), y1)


def pick_to_px(pick: dict, w: int, h: int) -> tuple[float, float, str]:
    """対応 1 件 → 元の写真の px。{"u","v"} ならそのまま、{"cell"} は升の中心、{"cell","sub"} は寄りの升の中心。
    返り値 (u, v, 精度の説明)。"""
    if pick.get("u") is not None and pick.get("v") is not None:
        return float(pick["u"]), float(pick["v"]), "px"
    x0, y0, x1, y1 = cell_rect(pick["cell"], w, h, expand=0.0)
    if pick.get("sub"):
        cx0, cy0, cx1, cy1 = cell_rect(pick["cell"], w, h, expand=CROP_EXPAND)
        sc, sr = parse_cell(pick["sub"])
        u = cx0 + (sc + 0.5) * (cx1 - cx0) / GRID_N
        v = cy0 + (sr + 0.5) * (cy1 - cy0) / GRID_N
        return u, v, "cell+sub"
    return (x0 + x1) / 2, (y0 + y1) / 2, "cell"


def grid_image(img: np.ndarray, out_jpg: Path, long_side: int = GRID_LONG) -> Path:
    import cv2

    h, w = img.shape[:2]
    s = long_side / max(w, h)
    small = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    out = _grid_overlay(small)
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tofile(str(out_jpg))
    return out_jpg


def crop_image(img: np.ndarray, cell: str, out_jpg: Path, long_side: int = 800) -> Path:
    """升を CROP_EXPAND だけ広げて切り出し、10×10 の升目を引く（寄りの絵）。"""
    import cv2

    h, w = img.shape[:2]
    x0, y0, x1, y1 = cell_rect(cell, w, h, expand=CROP_EXPAND)
    crop = img[int(y0):int(y1), int(x0):int(x1)]
    s = long_side / max(crop.shape[:2])
    big = cv2.resize(crop, (int(crop.shape[1] * s), int(crop.shape[0] * s)), interpolation=cv2.INTER_CUBIC)
    out = _grid_overlay(big, label_prefix=f"cell {cell.upper()}")
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tofile(str(out_jpg))
    return out_jpg


# --- PnP ------------------------------------------------------------------------------

def focal_px_from_exif(rec: dict) -> float:
    f35 = rec.get("focal_35mm") or 26.0
    w, h = int(rec.get("width") or 4000), int(rec.get("height") or 3000)
    return float(f35) / 36.0 * max(w, h)


def solve(P3: np.ndarray, uv: np.ndarray, w: int, h: int, focal0: float, search_focal: bool = True) -> dict:
    """PnP。P3 (N,3) 世界、uv (N,2) 写真 px。焦点は focal0 の 0.85〜1.15 倍を探して再投影が最小の物。
    返り値 {"R", "t", "focal", "reproj_px": 1 点ごと, "rms_px", "inliers": [...]}。"""
    import cv2

    P3 = np.asarray(P3, np.float64).reshape(-1, 3)
    uv = np.asarray(uv, np.float64).reshape(-1, 2)
    best = None
    base = None
    facs = np.linspace(0.85, 1.15, 13) if search_focal else [1.0]
    for fac in facs:
        f = focal0 * fac
        K = R_.K_matrix(f, w, h)
        if len(P3) >= 6:
            ok, rvec, tvec, inl = cv2.solvePnPRansac(P3, uv, K, None, reprojectionError=MAX_REPROJ_FRAC * w,
                                                     iterationsCount=200, flags=cv2.SOLVEPNP_SQPNP)
            inl = inl.ravel().tolist() if (ok and inl is not None) else []
            if ok and len(inl) >= MIN_POINTS:
                ok, rvec, tvec = cv2.solvePnP(P3[inl], uv[inl], K, None, flags=cv2.SOLVEPNP_SQPNP)
        else:
            ok, rvec, tvec = cv2.solvePnP(P3, uv, K, None, flags=cv2.SOLVEPNP_SQPNP)
            inl = list(range(len(P3)))
        if not ok:
            continue
        Rm, _ = cv2.Rodrigues(rvec)
        proj, _ = cv2.projectPoints(P3, rvec, tvec, K, None)
        err = np.linalg.norm(proj.reshape(-1, 2) - uv, axis=1)
        rms = float(np.sqrt(np.mean(err[inl] ** 2))) if inl else float("inf")
        cand = {"R": Rm, "t": tvec.ravel(), "focal": float(f), "reproj_px": [round(float(e), 1) for e in err],
                "rms_px": round(rms, 2), "inliers": inl, "fac": float(fac)}
        if abs(fac - 1.0) < 1e-9:
            base = cand
        if best is None or rms < best["rms_px"]:
            best = cand
    # 平らな面だけの対応では焦点と距離が入れ替わる（焦点を伸ばして下がっても同じ絵）。EXIF の焦点を基準にし、
    # 残差が 2 割以上減る時だけ別の焦点を採る
    if best and base and best["rms_px"] >= 0.8 * base["rms_px"]:
        best = base
    return best or {}


def register_picks(building_json: Path, picks_doc: dict, index: dict | None = None, reg: dict | None = None,
                   registry_path: Path | None = None, write: bool = True, by: str = "mejirushi") -> dict:
    """対応（picks_doc = {"photo", "face"?, "picks": [{"id", "cell", "sub"?} | {"id", "u", "v"}]}）から視点を解き、合否を出して台帳に書く。
    返り値 {"status": "ok"|"fail", "code", "reason", "camera", "check", "picks_px": [...]}。"""
    b = load_building(building_json)
    idx = index or load_index(INDEX_PATH)
    rec = R_.photo_record(picks_doc["photo"], idx)
    if rec is None:
        return {"status": "fail", "code": "no_photo", "reason": f"索引に無い: {picks_doc['photo']}"}
    w, h = int(rec["width"]), int(rec["height"])
    cat = {m["id"]: m for m in catalog(b)}
    P3, uv, used, problems = [], [], [], []
    for p in picks_doc.get("picks", []):
        m = cat.get(p.get("id"))
        if m is None:
            problems.append(f"目印 id が一覧に無い: {p.get('id')}")
            continue
        try:
            u, v, how = pick_to_px(p, w, h)
        except (ValueError, KeyError) as ex:
            problems.append(f"{p.get('id')}: {ex}")
            continue
        P3.append(m["xyz"])
        uv.append((u, v))
        used.append({**p, "u": round(u, 1), "v": round(v, 1), "how": how, "name": m["name"]})
    if len(P3) < MIN_POINTS:
        return _fail(rec, b, idx, reg, registry_path, write, "few_points", f"対応が {len(P3)} 点（{MIN_POINTS} 点要る）", problems)
    # 散らばり：同じ面で縦の線 2 本以上・横の線 2 本以上、写真の中で幅・高さの 25 % 以上
    ss = {round(cat[k["id"]]["s"], 1) for k in used}
    zs = {round(cat[k["id"]]["z"], 1) for k in used}
    uu = np.array([k["u"] for k in used]); vv = np.array([k["v"] for k in used])
    bw, bh = (uu.max() - uu.min()) / w, (vv.max() - vv.min()) / h
    if len(ss) < 2 or len(zs) < 2 or bw < MIN_BBOX_FRAC or bh < MIN_BBOX_FRAC:
        why = f"対応が偏っている（縦の線 {len(ss)} 本・横の線 {len(zs)} 本、写真の中の広がり 幅 {bw:.0%}・高さ {bh:.0%}。縦横 2 本以上・{MIN_BBOX_FRAC:.0%} 以上要る）"
        return _fail(rec, b, idx, reg, registry_path, write, "picks_degenerate", why, problems)
    focal0 = focal_px_from_exif(rec)
    sol = solve(np.array(P3), np.array(uv), w, h, focal0)
    if not sol:
        return _fail(rec, b, idx, reg, registry_path, write, "pnp_failed", "PnP が解けない（点が一直線か、対応の取り違え）", problems)
    for k, e in zip(used, sol["reproj_px"]):
        k["reproj_px"] = e
        k["inlier"] = used.index(k) in sol["inliers"]
    Rm, t = sol["R"], sol["t"]
    C = (-Rm.T @ t).ravel()
    dem = None
    try:
        from .site import load_dem
        dem = load_dem()
    except (OSError, FileNotFoundError, KeyError):
        dem = None
    from .sfm import ground_at
    g, _ = ground_at(float(C[0]), float(C[1]), b, dem)
    cam = R_.camera_dict(Rm, t, sol["focal"], w, h, g)
    hgt = float(C[2] - g)
    check = {"n_points": len(P3), "n_inliers": len(sol["inliers"]), "rms_px": sol["rms_px"],
             "rms_frac_w": round(sol["rms_px"] / w, 4), "focal_px": sol["focal"], "focal_vs_exif": round(sol["focal"] / focal0, 3),
             "camera_height_m": round(hgt, 2), "warnings": []}
    # 検算（係の指し方に依らない独立の物差し）：消失点の仰角、模型の線が写真の縁に乗る割合、線に直角な勾配
    edge_ok = True
    try:
        from .locate import EdgeDT, angles_of, model_points, pose_stats
        from .rectify import read_image
        img = read_image(R_.photo_path(rec))
        dt = EdgeDT(img)
        tilt = dt.estimate_tilt(sol["focal"])
        yaw, pitch, roll = angles_of(Rm)
        check.update({"yaw": round(yaw, 1), "pitch": round(pitch, 1), "roll": round(roll, 1), "tilt_from_vp": tilt})
        if tilt and abs(tilt["pitch"] - pitch) > PITCH_WARN_DEG:
            check["warnings"].append(f"消失点の仰角 {tilt['pitch']}° と PnP の仰角 {pitch:.1f}° が {abs(tilt['pitch'] - pitch):.0f}° 違う")
        st = pose_stats(C, Rm, sol["focal"], model_points(b), dt)
        check.update({"edge_inlier": st["inlier"], "edge_grad": st["grad"], "edge_coverage": st["coverage"]})
        edge_ok = st["inlier"] >= EDGE_MIN_INLIER or st["grad"] >= EDGE_MIN_GRAD
    except Exception as ex:                                  # 写真が読めない時は検算を飛ばす
        check["warnings"].append(f"縁の検算を飛ばした: {ex}")
    gps = rec.get("gps") or {}
    if gps.get("x") is not None:
        check["gps_dist_m"] = round(math.hypot(C[0] - gps["x"], C[1] - gps["y"]), 1)
    status, code, reason = "ok", None, None
    if sol["rms_px"] > MAX_REPROJ_FRAC * w:
        status, code, reason = "fail", "reproj", f"再投影の RMS {sol['rms_px']} px（幅の {MAX_REPROJ_FRAC:.0%}＝{MAX_REPROJ_FRAC * w:.0f} px まで）"
    elif not (MIN_CAM_HEIGHT <= hgt <= MAX_CAM_HEIGHT):
        status, code, reason = "fail", "camera_height", f"カメラの高さが地面から {hgt:.1f} m（{MIN_CAM_HEIGHT}〜{MAX_CAM_HEIGHT} m の外）"
    elif not edge_ok:
        status, code = "fail", "edge_check"
        reason = (f"解いた視点で模型の線が写真の縁に乗らない（乗った割合 {check.get('edge_inlier')}、勾配 {check.get('edge_grad')}。"
                  f"{EDGE_MIN_INLIER} 以上か {EDGE_MIN_GRAD} 以上要る）。対応の取り違え（隣の柱型・別の窓の列・延長した点）を疑う")
    vis = R_.visible_faces(b, cam) if status == "ok" else []
    if status == "ok" and not vis:
        status, code, reason = "fail", "no_visible_face", "解いた視点から見える棟の面が無い"
    out = {"photo": rec["id"], "building": b["id"], "status": status, "code": code, "reason": reason,
           "camera": cam, "check": check, "picks_px": used, "problems": problems, "visible_faces": vis}
    if write:
        reg = reg or R_.load_registry(registry_path or R_.REGISTRY_PATH)
        if status == "ok":
            entry = {"photo": rec["id"], "building": b["id"], "face": rec.get("face") or picks_doc.get("face") or "(pose)",
                     "kind": "pose", "period": rec.get("period"), "file": rec.get("file"), "image_size": [w, h],
                     "camera": cam, "visible_faces": vis,
                     "source": {"method": "landmarks：目印係が指した目印（面の角・柱型・部品の縁）の対応から PnP（cv2.solvePnP、焦点は EXIF の 0.85〜1.15 倍を探索）",
                                "kind": "landmarks", "picks": used, "focal_source": "exif_35mm を初期値に探索"},
                     "check": check, "registered_at": datetime.now().isoformat(timespec="seconds"), "registered_by": by}
            R_.upsert(reg, entry)
        else:
            _drop_landmark_entry(reg, b["id"], rec["id"])
            R_.note_not_registered(reg, b["id"], rec["id"], idx, reason or "", str(code), "landmarks")
        R_.save_registry(reg, registry_path or R_.REGISTRY_PATH)
    return out


def _drop_landmark_entry(reg: dict, building_id: str, photo_id: str) -> None:
    """同じ写真の目印による登録（前の試し）を外す。他の方法の登録は触らない。"""
    reg["entries"] = [e for e in reg.get("entries", [])
                      if not (e["building"] == building_id and e["photo"] == photo_id and (e.get("source") or {}).get("kind") == "landmarks")]


def _fail(rec, b, idx, reg, registry_path, write, code, reason, problems) -> dict:
    out = {"photo": rec["id"], "building": b["id"], "status": "fail", "code": code, "reason": reason, "problems": problems}
    if write:
        reg = reg or R_.load_registry(registry_path or R_.REGISTRY_PATH)
        _drop_landmark_entry(reg, b["id"], rec["id"])
        R_.note_not_registered(reg, b["id"], rec["id"], idx, reason, str(code), "landmarks")
        R_.save_registry(reg, registry_path or R_.REGISTRY_PATH)
    return out


# --- まとめて出す ------------------------------------------------------------------------------

def prepare(building_json: Path, photos: list[str] | None = None, out_dir: Path | None = None,
            include_registered: bool = False, log=print) -> dict:
    """目印の一覧・面の絵・写真の升目の絵を出し、目印係への依頼（tasks.json）を書く。"""
    from .rectify import read_image

    b = load_building(building_json)
    idx = load_index(INDEX_PATH)
    reg = R_.load_registry()
    done = {e["photo"] for e in R_.entries_for(reg, b["id"])}
    out = out_dir or Path("out") / b["id"] / "landmarks"
    out.mkdir(parents=True, exist_ok=True)
    cat = catalog(b)
    (out / "landmarks.json").write_text(json.dumps({"building": b["id"], "landmarks": cat}, ensure_ascii=False, indent=1), encoding="utf-8")
    faces = sorted({m["face"] for m in cat})
    for fid in faces:
        draw_face(b, fid, [m for m in cat if m["face"] == fid], out / f"{fid}.png")
    recs = [r for r in idx.get("photos", []) if r.get("building") == b["id"] and not r.get("interior")]
    if photos:
        recs = [r for r in recs if any(p in r["id"] for p in photos)]
    if not include_registered:
        recs = [r for r in recs if r["id"] not in done]
    tasks = []
    for r in recs:
        try:
            img = read_image(R_.photo_path(r))
        except (OSError, FileNotFoundError):
            continue
        grid_image(img, out / f"{r['id']}_grid.jpg")
        tasks.append({"photo": r["id"], "face": r.get("face"), "grid": str(out / f"{r['id']}_grid.jpg"),
                      "face_png": str(out / f"{r['face']}.png") if r.get("face") in faces else None,
                      "candidates": [c.get("building") for c in (r.get("candidates") or [])][:3]})
    (out / "tasks.json").write_text(json.dumps({"building": b["id"], "made_at": datetime.now().isoformat(timespec="seconds"),
                                                 "tasks": tasks}, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"目印 {len(cat)}（面 {len(faces)}）、写真 {len(tasks)} 枚 → {out}")
    return {"catalog": cat, "tasks": tasks, "out": out}
