"""写真の視点（カメラの位置と向き）を、写真の中の点と模型の点の対応から逆算する（PnP）。

GPS と方位は 10〜20 m / 30° ずれるので使わない。判断役（Claude か人）が写真の中で
「建物の角」を 4 点以上指し、それぞれが棟 JSON のどの頂点（x, y, z）かを言う。
焦点距離は EXIF の 35mm 換算から。あとは OpenCV の solvePnP。

出てくる物（pose JSON）
    camera_xyz      カメラの位置（棟と同じ座標系、z は地面からの高さ）
    R_cv            OpenCV の回転（3×3。世界→カメラ）
    blender         Blender のカメラ（location, rotation_euler, lens_mm, sensor_fit, shift）
    reproj_px       各対応点の再投影誤差（px）と最大値。大きい点は対応の間違い
この pose を blender_build に渡すと、写真と同じ視点で模型を描ける（検収の重ね合わせ用）。
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class Pose:
    ok: bool
    reason: str = ""
    photo: str = ""
    image_size: list[int] = field(default_factory=list)
    focal_px: float | None = None
    focal_source: str = ""
    camera_xyz: list[float] | None = None
    R_cv: list[list[float]] | None = None
    t_cv: list[float] | None = None
    blender: dict | None = None
    reproj_px: list[float] = field(default_factory=list)
    reproj_max_px: float | None = None
    n_points: int = 0
    points_used: list[dict] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)


def focal_px_from_35mm(f35: float, width: int, height: int) -> float:
    """35mm 換算の焦点距離 → 画素。対角 43.27mm の換算なので対角で合わせる。"""
    return f35 / 43.27 * math.hypot(width, height)


def cv_to_blender(R: np.ndarray, t: np.ndarray, focal_px: float, width: int, height: int) -> dict:
    """OpenCV のカメラ（+Z が前、+Y が下）→ Blender のカメラ（-Z が前、+Y が上）。"""
    from mathutils import Matrix  # Blender 外でも使えるよう遅延（無ければ数値で返す）

    cam_pos = (-R.T @ t).ravel()
    flip = np.diag([1.0, -1.0, -1.0])
    R_bl = R.T @ flip                      # カメラ→世界（Blender の向き）
    m = Matrix([[float(v) for v in row] for row in R_bl])
    eul = m.to_euler("XYZ")
    sensor = 36.0
    lens = focal_px * sensor / max(width, height)
    return {"location": [float(v) for v in cam_pos], "rotation_euler": [eul.x, eul.y, eul.z],
            "lens_mm": lens, "sensor_width_mm": sensor, "sensor_fit": "AUTO", "resolution": [width, height]}


def cv_to_blender_numeric(R: np.ndarray, t: np.ndarray, focal_px: float, width: int, height: int) -> dict:
    """mathutils が無い環境用（Euler XYZ を自前で）。"""
    cam_pos = (-R.T @ t).ravel()
    flip = np.diag([1.0, -1.0, -1.0])
    M = R.T @ flip
    # Euler XYZ（Blender と同じ順）: M = Rz * Ry * Rx
    sy = -M[2, 0]
    sy = max(-1.0, min(1.0, sy))
    y = math.asin(sy)
    if abs(math.cos(y)) > 1e-6:
        x = math.atan2(M[2, 1], M[2, 2])
        z = math.atan2(M[1, 0], M[0, 0])
    else:
        x = math.atan2(-M[1, 2], M[1, 1])
        z = 0.0
    sensor = 36.0
    return {"location": [float(v) for v in cam_pos], "rotation_euler": [x, y, z],
            "lens_mm": focal_px * sensor / max(width, height), "sensor_width_mm": sensor, "sensor_fit": "AUTO", "resolution": [width, height]}


def solve(points_px: list[list[float]], points_xyz: list[list[float]], width: int, height: int,
          focal_px: float, focal_source: str = "exif_35mm") -> Pose:
    import cv2

    res = Pose(ok=False, image_size=[width, height], focal_px=focal_px, focal_source=focal_source, n_points=len(points_px))
    if len(points_px) < 4 or len(points_px) != len(points_xyz):
        res.reason = "対応点が 4 点未満か、数が合いません"
        return res
    obj = np.asarray(points_xyz, dtype=np.float64).reshape(-1, 1, 3)
    img = np.asarray(points_px, dtype=np.float64).reshape(-1, 1, 2)
    K = np.array([[focal_px, 0, width / 2.0], [0, focal_px, height / 2.0], [0, 0, 1]], dtype=np.float64)
    dist = np.zeros(5)
    if len(points_px) >= 6:
        ok, rvec, tvec, inl = cv2.solvePnPRansac(obj, img, K, dist, reprojectionError=25.0, iterationsCount=500, flags=cv2.SOLVEPNP_ITERATIVE)
    else:
        ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist, flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok:
        res.reason = "PnP が解けません（点が一直線か、対応が間違っています）"
        return res
    # 全点で仕上げ
    ok, rvec, tvec = cv2.solvePnP(obj, img, K, dist, rvec=rvec, tvec=tvec, useExtrinsicGuess=True, flags=cv2.SOLVEPNP_ITERATIVE)
    R, _ = cv2.Rodrigues(rvec)
    proj, _ = cv2.projectPoints(obj, rvec, tvec, K, dist)
    err = np.linalg.norm(proj.reshape(-1, 2) - img.reshape(-1, 2), axis=1)
    cam = (-R.T @ tvec).ravel()
    if cam[2] < -5 or cam[2] > 300:
        res.reason = f"カメラの高さが変です（z={cam[2]:.1f} m）。対応点の z か順番を確かめてください"
    res.camera_xyz = [round(float(v), 2) for v in cam]
    res.R_cv = [[float(v) for v in row] for row in R]
    res.t_cv = [float(v) for v in tvec.ravel()]
    res.blender = cv_to_blender_numeric(R, tvec, focal_px, width, height)
    res.reproj_px = [round(float(e), 1) for e in err]
    res.reproj_max_px = round(float(err.max()), 1)
    res.ok = not res.reason
    return res


def snap_corners(img, points_px: list[list[float]], radius: int = 18) -> list[list[float]]:
    """人（や判断役）が指した画素を、近くの一番はっきりした角に寄せる（±radius px）。角が無ければそのまま。"""
    import cv2

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    out = []
    H, W = gray.shape[:2]
    for u, v in points_px:
        x0, y0 = int(max(0, u - radius)), int(max(0, v - radius))
        x1, y1 = int(min(W, u + radius)), int(min(H, v + radius))
        patch = gray[y0:y1, x0:x1]
        c = cv2.goodFeaturesToTrack(patch, maxCorners=5, qualityLevel=0.05, minDistance=4, blockSize=5)
        if c is None:
            out.append([float(u), float(v)])
            continue
        c = c.reshape(-1, 2) + np.array([x0, y0], dtype=np.float32)
        c = cv2.cornerSubPix(gray, c.reshape(-1, 1, 2).astype(np.float32), (5, 5), (-1, -1),
                             (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 0.01)).reshape(-1, 2)
        j = int(np.argmin(np.linalg.norm(c - np.array([u, v]), axis=1)))
        out.append([float(c[j][0]), float(c[j][1])])
    return out


def solve_file(photo: Path, points_json: Path, out_json: Path | None = None) -> Pose:
    """points_json: {"points": [{"px": [u, v], "xyz": [x, y, z], "what": "南西面 左上の角"}, ...]}
    px は EXIF の向きを直した後の画像（rectify.read_image と同じ）での画素。"""
    from .photos import read_exif

    info = read_exif(photo)
    w, h = info["width"], info["height"]
    if info.get("orientation") in (5, 6, 7, 8):
        w, h = h, w
    d = json.loads(points_json.read_text(encoding="utf-8"))
    pts = d["points"]
    f35 = d.get("focal_35mm") or info.get("focal_35mm")
    if not f35:
        res = Pose(ok=False, reason="焦点距離（35mm 換算）が EXIF にも points JSON にもありません")
        return res
    px = [p["px"] for p in pts]
    if d.get("snap", True):
        from .rectify import read_image

        px = snap_corners(read_image(photo), px)
    res = solve(px, [p["xyz"] for p in pts], w, h, focal_px_from_35mm(float(f35), w, h))
    res.photo = str(photo)
    res.points_used = [{"what": p.get("what", ""), "px": [round(v, 1) for v in q], "xyz": p["xyz"]} for p, q in zip(pts, px)]
    out_json = out_json or points_json.with_name(points_json.stem.replace("_points", "") + "_pose.json")
    out_json.write_text(json.dumps(res.to_json(), ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def from_rectification(photo: Path, front_json: Path, compare_json: Path, building_json: Path, face_id: str,
                       out_json: Path | None = None) -> Pose:
    """手で点を指さずに視点を出す：正面化の射影変換 H（写真→正面化画像）と、検収で合わせた原点
    （正面化画像のどこが面の s=0 / z=0 か、1 m が何 px か）をつなぐと、面の平面（m）→写真（px）の射影変換になる。
    面の平面上の格子点を世界座標に起こし、その写真上の位置と対応させて PnP（平面用の IPPE）。"""
    import cv2

    from .schema import load_building

    fj = json.loads(front_json.read_text(encoding="utf-8"))
    cj = json.loads(compare_json.read_text(encoding="utf-8"))
    b = load_building(building_json)
    f = next(f for f in b["faces"] if f["id"] == face_id)
    p0, p1 = np.array(f["from"], float), np.array(f["to"], float)
    d = p1 - p0
    L = float(np.linalg.norm(d))
    u = d / L
    n = np.array([u[1], -u[0]])
    top = (f.get("top_z") or {}).get("value") or (b.get("parapet_top") or {}).get("value") or 20.0
    H_img2rect = np.array(fj["H"], float)
    ppm, xo, zo = float(cj["px_per_m"]), float(cj["x_origin_px"]), float(cj["z_origin_px"])
    A = np.array([[ppm, 0, xo], [0, -ppm, zo], [0, 0, 1]], float)          # 面 (s, z) → 正面化 px
    H_plane2img = np.linalg.inv(H_img2rect) @ A
    w, h = fj["width"], fj["height"]
    obj, img = [], []
    for s_ in np.linspace(0, L, 7):
        for z_ in np.linspace(0, top, 5):
            q = H_plane2img @ np.array([s_, z_, 1.0])
            if q[2] <= 1e-9:
                continue
            uu, vv = q[0] / q[2], q[1] / q[2]
            if -0.2 * w <= uu <= 1.2 * w and -0.2 * h <= vv <= 1.2 * h:
                obj.append([p0[0] + u[0] * s_, p0[1] + u[1] * s_, z_])
                img.append([uu, vv])
    res = Pose(ok=False, photo=str(photo), image_size=[w, h], focal_px=float(fj["focal_px"]), focal_source=fj.get("focal_source", ""), n_points=len(obj))
    if len(obj) < 6:
        res.reason = "面の格子点が写真にほとんど入りません"
        return res
    K = np.array([[res.focal_px, 0, w / 2.0], [0, res.focal_px, h / 2.0], [0, 0, 1]], float)
    objA, imgA = np.asarray(obj, float).reshape(-1, 1, 3), np.asarray(img, float).reshape(-1, 1, 2)
    try:
        cnt, rvecs, tvecs, errs = cv2.solvePnPGeneric(objA, imgA, K, None, flags=cv2.SOLVEPNP_IPPE)
    except cv2.error:
        cnt, rvecs, tvecs, errs = 0, [], [], []
    if not cnt:
        ok, rvec, tvec = cv2.solvePnP(objA, imgA, K, None, flags=cv2.SOLVEPNP_ITERATIVE)
        rvecs, tvecs = [rvec], [tvec]
    best = None
    for rvec, tvec in zip(rvecs, tvecs):
        R, _ = cv2.Rodrigues(rvec)
        cam = (-R.T @ tvec).ravel()
        front = float(np.dot(cam[:2] - p0, n))          # 面の外側にいるか
        proj, _ = cv2.projectPoints(objA, rvec, tvec, K, None)
        err = np.linalg.norm(proj.reshape(-1, 2) - imgA.reshape(-1, 2), axis=1)
        score = (front <= 0, float(err.max()))
        if best is None or score < best[0]:
            best = (score, R, tvec, cam, err)
    _, R, tvec, cam, err = best
    res.camera_xyz = [round(float(v), 2) for v in cam]
    res.R_cv = [[float(v) for v in row] for row in R]
    res.t_cv = [float(v) for v in tvec.ravel()]
    res.blender = cv_to_blender_numeric(R, tvec, res.focal_px, w, h)
    res.reproj_px = [round(float(e), 1) for e in err]
    res.reproj_max_px = round(float(err.max()), 1)
    res.ok = True
    if cam[2] < -3 or cam[2] > 60:
        res.reason = f"カメラの高さが変です（z={cam[2]:.1f} m）。原点や縮尺を疑ってください"
    out_json = out_json or Path("out") / b["id"] / "poses" / f"{photo.stem}_pose.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(res.to_json(), ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def overlay(photo: Path, render_png: Path, out_png: Path, alpha: float = 0.5) -> Path:
    """写真と同じ視点の描画を写真に重ねる（半透明＋描画のエッジを赤で）。"""
    import cv2

    from .rectify import read_image

    img = read_image(photo)
    ren = cv2.imdecode(np.fromfile(str(render_png), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if ren.ndim == 3 and ren.shape[2] == 4:
        ren = cv2.cvtColor(ren, cv2.COLOR_BGRA2BGR)
    ren = cv2.resize(ren, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_AREA)
    blend = cv2.addWeighted(img, 1 - alpha, ren, alpha, 0)
    edges = cv2.Canny(cv2.cvtColor(ren, cv2.COLOR_BGR2GRAY), 60, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    blend[edges > 0] = (0, 0, 255)
    h, w = blend.shape[:2]
    s = min(1.0, 2400 / max(h, w))
    if s < 1:
        blend = cv2.resize(blend, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".png", blend)[1].tofile(str(out_png))
    return out_png
