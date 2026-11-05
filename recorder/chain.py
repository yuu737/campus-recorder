"""鎖の登録：平らな壁でない写真（階段・デッキ・ピロティ・天井・角）を、登録済みの写真とのつながりで登録する。

やり方
  1. 新しい写真と登録済みの写真（--via）の特徴点（SIFT）を突き合わせる（比の検定 → 2 枚の幾何（基礎行列）の RANSAC）。
  2. 登録済みの写真の側の点を、その写真に写っている面の平面に戻す（plane の登録は H_plane の逆、pose の登録は
     camera から作った各面の H の逆。面の矩形の内側・exclude の外の点だけ。複数の面に当たる点は手前の面）。
     これで「新しい写真の px ↔ 世界の (x, y, z)」の対応ができる。
  3. solvePnPRansac（焦点距離は EXIF の 35mm 換算、既存の pose と同じ）で新しい写真の視点を解き、内点で仕上げる。
     違う面の点（--via を複数）を混ぜれば、角を写した写真でも点が一直線・一平面に偏らない。
  4. 品質：内点の再投影誤差（RMS）が MAX_REPROJ_PX 以下、内点が MIN_POINTS 以上でなければ登録しない（理由を返す）。

登録の形は registry の kind "pose"（camera と見えている面の一覧だけ。H_plane は読む側が camera から作る）。

注意：登録済みの写真の点は「面の平面の上にある」とみなす。面の手前の物（木・階段・人）に付いた点は 3D の位置が
間違うが、新しい写真の視点とは合わないので RANSAC で外れる（外れ値の割合は check に残す）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np

from . import registry as R_
from .photos import INDEX_PATH, load_index
from .schema import load_building

MAX_REPROJ_PX = 3.0     # 内点の再投影誤差（RMS、元の写真の px）。これより大きければ登録しない
MIN_POINTS = 12          # PnP の内点の数。これ未満なら登録しない
WEAK_POINTS = 30         # 内点がこれ未満の時は、候補のうち内点の割合が MIN_RATIO_WEAK 以上を求める（少ない点の偶然の一致を避ける）
MIN_RATIO_WEAK = 0.25
RANSAC_PX = 6.0          # PnP の RANSAC の閾値（元の写真の px）
F_RANSAC_PX = 6.0        # 2 枚の幾何（基礎行列）の閾値（元の写真の px）
RATIO = 0.8              # 特徴点の比の検定（RootSIFT）
MAX_SIDE = 2000          # 特徴点を取る時に縮める長辺（px）
N_FEATURES = 12000
MIN_SPREAD = 0.04        # 内点が新しい写真の枠のこの割合（外接矩形の面積）未満に固まっていたら登録しない（タイルの繰り返しの誤対応）
MIN_OVERLAP = 30         # register-chain：登録済みとの良い対応がこれ未満の写真は試さない


class ChainFail(ValueError):
    def __init__(self, reason: str, code: str, stats: dict | None = None):
        super().__init__(reason)
        self.code = code
        self.stats = stats or {}


# --- 特徴点 ------------------------------------------------------------------------------

def _sift():
    import cv2

    return cv2.SIFT_create(nfeatures=N_FEATURES)


def features_of_image(img: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """画像（BGR か灰色）→ 特徴点（元の画像の px、N×2）と記述子（N×128 float32）。"""
    import cv2

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    h, w = gray.shape[:2]
    s = min(1.0, MAX_SIDE / max(h, w))
    small = cv2.resize(gray, (int(round(w * s)), int(round(h * s))), interpolation=cv2.INTER_AREA) if s < 1 else gray
    kp, des = _sift().detectAndCompute(small, None)
    if des is None or not kp:
        return np.zeros((0, 2), float), np.zeros((0, 128), np.float32)
    pts = np.array([k.pt for k in kp], float)
    pts = (pts + 0.5) / s - 0.5
    des = des / (des.sum(axis=1, keepdims=True) + 1e-7)       # RootSIFT（明るさの違いに強い）
    return pts, np.sqrt(des).astype(np.float32)


class FeatureCache:
    """写真ごとの特徴点（メモリと、あれば out/<棟>/chain_feats/*.npz）。写真は元の場所から読むだけで写さない。"""

    def __init__(self, cache_dir: Path | None = None, loader=None):
        self.dir = Path(cache_dir) if cache_dir else None
        self.mem: dict[str, tuple[np.ndarray, np.ndarray, tuple[int, int]]] = {}
        self.loader = loader

    def _load_image(self, path: Path) -> np.ndarray:
        if self.loader is not None:
            return self.loader(path)
        from .rectify import read_image

        return read_image(path)

    def get(self, key: str, path: Path) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
        if key in self.mem:
            return self.mem[key]
        npz = self.dir / f"{Path(key).stem}.npz" if self.dir else None
        if npz is not None and npz.exists():
            d = np.load(npz)
            r = (d["pts"], d["des"], (int(d["size"][0]), int(d["size"][1])))
        else:
            img = self._load_image(path)
            pts, des = features_of_image(img)
            r = (pts, des, (img.shape[1], img.shape[0]))
            if npz is not None:
                npz.parent.mkdir(parents=True, exist_ok=True)
                np.savez_compressed(npz, pts=pts.astype(np.float32), des=des, size=np.array(r[2]))
        self.mem[key] = r
        return r


def match(desA: np.ndarray, desB: np.ndarray, ratio: float = RATIO) -> np.ndarray:
    """比の検定を通った対応（K×2 の添字 [A, B]）。B の同じ点を 2 回使わない。"""
    import cv2

    if len(desA) < 2 or len(desB) < 2:
        return np.zeros((0, 2), int)
    fl = cv2.FlannBasedMatcher({"algorithm": 1, "trees": 5}, {"checks": 64})
    knn = fl.knnMatch(desA, desB, k=2)
    best: dict[int, tuple[float, int]] = {}
    for m in knn:
        if len(m) == 2 and m[0].distance < ratio * m[1].distance:
            j = m[0].trainIdx
            if j not in best or m[0].distance < best[j][0]:
                best[j] = (m[0].distance, m[0].queryIdx)
    if not best:
        return np.zeros((0, 2), int)
    return np.array([[qi, j] for j, (_, qi) in best.items()], int)


# --- 登録済みの写真の点 → 世界 --------------------------------------------------------------

def plane_points(via_faces: list[dict], uv: np.ndarray) -> tuple[np.ndarray, np.ndarray, list]:
    """登録済みの写真の px（N×2）を、その写真の面の平面（面ごとの plane の形の entry）に戻して世界の点に。
    面の矩形の内側・exclude の外・カメラの前の点だけ有効。複数の面に当たれば手前（奥行き w が小さい）の面。
    返り値 (xyz N×3, 有効 N, 面 id の一覧)。"""
    N = len(uv)
    xyz = np.full((N, 3), np.nan)
    best_w = np.full(N, np.inf)
    face_of: list = [None] * N
    for fe in via_faces:
        H = np.asarray(fe["H_plane"], float)
        fr = fe["face_frame"]
        L = fr.get("length_m") or R_.face_frame(fr)[3]
        g = fr.get("ground_offset", 0.0) or 0.0
        top = fr.get("top_z") or 20.0
        s, z = R_.invert_H(H, uv[:, 0], uv[:, 1])
        _, _, w = R_.apply_H(H, s, z)
        ok = np.isfinite(s) & np.isfinite(z) & (w > 0.3) & (s >= 0) & (s <= L) & (z >= g) & (z <= top)
        for x in fe.get("exclude") or []:
            rect = x.get("rect") if isinstance(x, dict) else x
            s0, s1, z0, z1 = (float(v) for v in rect)
            ok &= ~((s >= min(s0, s1)) & (s <= max(s0, s1)) & (z >= min(z0, z1)) & (z <= max(z0, z1)))
        take = ok & (w < best_w)
        if take.any():
            P = R_.plane_to_world(fr, s[take], z[take])
            xyz[take] = P
            best_w[take] = w[take]
            for i in np.nonzero(take)[0]:
                face_of[i] = fe["face"]
    return xyz, np.isfinite(xyz).all(axis=1), face_of


# --- 視点を解く ----------------------------------------------------------------------------

def solve_pose(img_pts: np.ndarray, obj_pts: np.ndarray, focal_px: float, w: int, h: int,
               ransac_px: float = RANSAC_PX) -> dict:
    """2D–3D の対応から視点（RANSAC → 内点で LM 仕上げ）。返り値 {ok, R, t, inliers, err(内点の誤差 px)} か {ok False, reason}。"""
    import cv2

    n = len(img_pts)
    if n < 6:
        return {"ok": False, "reason": f"2D–3D の対応が {n} 点しかない（6 点未満）", "code": "few_matches"}
    K = R_.K_matrix(focal_px, w, h)
    obj = np.ascontiguousarray(obj_pts, np.float64).reshape(-1, 1, 3)
    img = np.ascontiguousarray(img_pts, np.float64).reshape(-1, 1, 2)
    try:
        ok, rvec, tvec, inl = cv2.solvePnPRansac(obj, img, K, None, reprojectionError=ransac_px, iterationsCount=3000,
                                                 confidence=0.999, flags=cv2.SOLVEPNP_SQPNP)
    except cv2.error as ex:
        return {"ok": False, "reason": f"PnP が解けない（{str(ex).splitlines()[-1][:60]}）", "code": "pnp_fail"}
    if not ok or inl is None or len(inl) < 4:
        return {"ok": False, "reason": "PnP が解けない（つじつまの合う対応の組が無い）", "code": "pnp_fail"}
    idx = inl.ravel()
    for _ in range(3):        # 内点で仕上げ → 全点で内点を取り直す
        rvec, tvec = cv2.solvePnPRefineLM(obj[idx], img[idx], K, None, rvec, tvec)
        proj, _ = cv2.projectPoints(obj, rvec, tvec, K, None)
        err = np.linalg.norm(proj.reshape(-1, 2) - img.reshape(-1, 2), axis=1)
        Rm, _ = cv2.Rodrigues(rvec)
        depth = (obj.reshape(-1, 3) @ Rm.T + tvec.reshape(1, 3))[:, 2]
        new = np.nonzero((err < ransac_px) & (depth > 0.3))[0]
        if len(new) < 4 or np.array_equal(new, idx):
            break
        idx = new
    Rm, _ = cv2.Rodrigues(rvec)
    return {"ok": True, "R": Rm, "t": tvec.reshape(3), "inliers": idx, "err": err[idx], "err_all": err}


# --- 1 枚の鎖の登録 --------------------------------------------------------------------------

def _via_entries(reg: dict, b: dict, via_id: str) -> list[dict]:
    es = [e for e in R_.entries_for(reg, b["id"]) if e["photo"] == via_id]
    if not es:
        es = R_.entries_for(reg, b["id"], [via_id])
    return R_.expand_entries(es, b)


def _focal(rec: dict, w: int, h: int) -> float:
    from .pose import focal_px_from_35mm

    f35 = rec.get("focal_35mm")
    if not f35:
        raise ChainFail("焦点距離（35mm 換算）が索引に無い", "no_focal")
    return focal_px_from_35mm(float(f35), w, h)


def correspondences(new_key: str, new_path: Path, via_ids: list[str], reg: dict, b: dict, cache: FeatureCache,
                    index: dict) -> tuple[np.ndarray, np.ndarray, list, dict]:
    """新しい写真の px と世界の点の対応（--via の全部から集める）。"""
    import cv2

    ptsN, desN, sizeN = cache.get(new_key, new_path)
    img_all, obj_all, faces_all, per_via = [], [], [], {}
    for vid in via_ids:
        vents = _via_entries(reg, b, vid)
        if not vents:
            per_via[vid] = {"error": "登録が無い"}
            continue
        vrec = R_.photo_record(vid, index) or {"file": vents[0]["file"]}
        ptsV, desV, sizeV = cache.get(vents[0]["photo"], R_.photo_path(vents[0]))
        if list(sizeV) != list(vents[0]["image_size"]):
            per_via[vid] = {"error": f"写真の大きさが登録と違う {sizeV}"}
            continue
        m = match(desN, desV)
        st = {"matches": int(len(m))}
        if len(m) >= 8:
            a, c = ptsN[m[:, 0]], ptsV[m[:, 1]]
            F, msk = cv2.findFundamentalMat(a.astype(np.float64), c.astype(np.float64), cv2.USAC_MAGSAC, F_RANSAC_PX, 0.999, 10000)
            if F is not None and msk is not None:
                m = m[msk.ravel().astype(bool)]
        st["geometric"] = int(len(m))
        xyz, ok, fo = plane_points(vents, ptsV[m[:, 1]]) if len(m) else (np.zeros((0, 3)), np.zeros(0, bool), [])
        st["on_plane"] = int(ok.sum())
        per_via[vid] = st
        _ = vrec
        if ok.any():
            img_all.append(ptsN[m[ok, 0]])
            obj_all.append(xyz[ok])
            faces_all += [f"{vid}:{f}" for f, k in zip(fo, ok) if k]
    if not img_all:
        return np.zeros((0, 2)), np.zeros((0, 3)), [], per_via
    img = np.vstack(img_all)
    obj = np.vstack(obj_all)
    # 同じ新しい写真の点が別の --via から 2 回来たら、最初の物だけ
    _, first = np.unique(np.round(img, 1), axis=0, return_index=True)
    first = np.sort(first)
    return img[first], obj[first], [faces_all[i] for i in first], per_via


def register_via(photo_id: str, building_json: Path, via_ids: list[str], reg: dict, index: dict | None = None,
                 cache: FeatureCache | None = None, by: str = "chain") -> dict:
    """写真 1 枚を --via の登録済み写真とのつながりで登録する（台帳へは書かない。返り値の entry を upsert する）。
    登録できなければ ChainFail（理由と code）。"""
    idx = index or load_index(INDEX_PATH)
    b = load_building(building_json)
    rec = R_.photo_record(photo_id, idx)
    if rec is None:
        raise ChainFail(f"索引に写真 {photo_id} が無い", "no_record")
    path = R_.photo_path(rec)
    if not path.exists():
        raise ChainFail(f"写真が見つからない（{path}）", "no_file")
    cache = cache or FeatureCache(Path("out") / b["id"] / "chain_feats")
    via_ids = [v for v in via_ids if v and v != rec["id"]]
    if not via_ids:
        raise ChainFail("--via に登録済みの写真が無い", "no_via")
    img, obj, faces, per_via = correspondences(rec["id"], path, via_ids, reg, b, cache, idx)
    _, _, (w, h) = cache.get(rec["id"], path)
    stats = {"via": per_via, "n_candidates": int(len(img))}
    if len(img) < MIN_POINTS:
        raise ChainFail(f"面の上の対応点が {len(img)} 点（{MIN_POINTS} 点未満）", "few_matches", stats)
    focal = _focal(rec, w, h)
    sol = solve_pose(img, obj, focal, w, h)
    if not sol["ok"]:
        raise ChainFail(sol["reason"], sol.get("code", "pnp_fail"), stats)
    n_in = int(len(sol["inliers"]))
    rms = float(np.sqrt(np.mean(sol["err"] ** 2))) if n_in else float("inf")
    stats.update({"n_inliers": n_in, "reproj_rms_px": round(rms, 2)})
    if n_in < MIN_POINTS:
        raise ChainFail(f"視点に合う対応点が {n_in} 点（{MIN_POINTS} 点未満）", "few_inliers", stats)
    ratio = n_in / len(img)
    if n_in < WEAK_POINTS and ratio < MIN_RATIO_WEAK:
        raise ChainFail(f"視点に合う点が {n_in} 点で候補の {ratio * 100:.0f}%（{WEAK_POINTS} 点未満なら {MIN_RATIO_WEAK * 100:.0f}% 以上が要る。偶然の一致の疑い）",
                        "weak", stats)
    if rms > MAX_REPROJ_PX:
        raise ChainFail(f"再投影の誤差 {rms:.1f} px（{MAX_REPROJ_PX} px 超）", "reproj", stats)
    ip = img[sol["inliers"]]
    frac = float(np.ptp(ip[:, 0]) * np.ptp(ip[:, 1]) / (w * h))
    stats["inlier_box_frac"] = round(frac, 3)
    if frac < MIN_SPREAD:
        raise ChainFail(f"視点に合う点が写真の {frac * 100:.1f}% の範囲に固まっている（{MIN_SPREAD * 100:.0f}% 未満。誤対応の疑い）", "clustered", stats)
    C = -sol["R"].T @ sol["t"]
    used_faces: dict[str, int] = {}
    for i in sol["inliers"]:
        used_faces[faces[i]] = used_faces.get(faces[i], 0) + 1
    # カメラの足元の地面：内点が一番多い面の前の地面（ground_offset）
    fids = {}
    for k, n in used_faces.items():
        fid = k.split(":", 1)[1]
        fids[fid] = fids.get(fid, 0) + n
    fmap = {f["id"]: f for f in b.get("faces", [])}
    main = max(fids, key=fids.get)
    from .schema import face_ground_offset

    ground = face_ground_offset(fmap[main]) if main in fmap else 0.0
    cam = R_.camera_dict(sol["R"], sol["t"], focal, w, h, ground)
    if C[2] - ground < -3 or C[2] - ground > 60:
        raise ChainFail(f"カメラの高さが変（地面から {C[2] - ground:.1f} m）", "camera_height", stats)
    vis = R_.visible_faces(b, cam)
    if not vis:
        raise ChainFail("解いた視点から見える面が無い", "no_visible_face", stats)
    inl_obj = obj[sol["inliers"]]
    spread = np.ptp(inl_obj, axis=0)
    entry = {
        "photo": rec["id"], "building": b["id"], "face": rec.get("face") or "(pose)", "kind": "pose",
        "period": rec.get("period"), "file": rec.get("file"), "image_size": [int(w), int(h)],
        "camera": cam,
        "visible_faces": vis,
        "via": via_ids,
        "source": {"method": "chain：SIFT（比の検定・基礎行列の RANSAC）→ 登録済みの写真の面の平面に戻した点 → solvePnPRansac（SQPnP）→ 内点で LM",
                   "focal_source": "exif_35mm", "ground_face": main},
        "check": {"reproj_rms_px": round(rms, 2), "reproj_median_px": round(float(np.median(sol["err"])), 2),
                  "reproj_max_px": round(float(sol["err"].max()), 2), "n_points": n_in, "n_candidates": int(len(img)),
                  "inlier_ratio": round(n_in / len(img), 3), "inlier_box_frac": stats["inlier_box_frac"], "points_by_face": used_faces,
                  "points_spread_m": [round(float(v), 2) for v in spread],
                  "camera_height_m": cam["camera_height_m"], "via_stats": per_via},
        "registered_at": datetime.now().isoformat(timespec="seconds"),
        "registered_by": by,
    }
    return entry


# --- 自動の鎖（穴埋めパズル）-------------------------------------------------------------

def _overlap(cache: FeatureCache, pairs: dict, a: str, pa: Path, bkey: str, pb: Path) -> int:
    k = (a, bkey)
    if k not in pairs:
        _, da, _ = cache.get(a, pa)
        _, db, _ = cache.get(bkey, pb)
        pairs[k] = int(len(match(da, db)))
    return pairs[k]


def register_chain(building_json: Path, reg: dict, index: dict | None = None, cache: FeatureCache | None = None,
                   n_via: int = 3, max_rounds: int = 10, log=print, photos: list[str] | None = None) -> dict:
    """未登録の写真を、登録済みと重なりが多い順に --via（重なりの多い登録済み n_via 枚）で試し、通った物を登録済みに
    加えて繰り返す。増えなくなったら止める。reg を書き換える（保存は呼ぶ側）。"""
    idx = index or load_index(INDEX_PATH)
    b = load_building(building_json)
    cache = cache or FeatureCache(Path("out") / b["id"] / "chain_feats")
    recs = [r for r in idx.get("photos", []) if r.get("building") == b["id"]]
    if photos:
        recs = [r for r in recs if any(r["id"].startswith(p) for p in photos)]
    pairs: dict = {}
    rounds, failed = [], {}
    for rnd in range(1, max_rounds + 1):
        done_ids = {e["photo"] for e in R_.entries_for(reg, b["id"])}
        regd = [e for e in R_.entries_for(reg, b["id"]) if Path(R_.photo_path(e)).exists()]
        reg_photos = {}
        for e in regd:
            reg_photos.setdefault(e["photo"], e)
        todo = [r for r in recs if r["id"] not in done_ids]
        if not todo or not reg_photos:
            break
        scored = []
        for r in todo:
            p = R_.photo_path(r)
            if not p.exists():
                failed[r["id"]] = ("no_file", f"写真が見つからない（{p}）")
                continue
            ov = sorted(((_overlap(cache, pairs, r["id"], p, pid, R_.photo_path(e)), pid) for pid, e in reg_photos.items()),
                        reverse=True)
            scored.append((ov[0][0] if ov else 0, r, ov))
        scored.sort(key=lambda x: -x[0])
        added = []
        for best, r, ov in scored:
            if best < MIN_OVERLAP:
                failed[r["id"]] = ("no_overlap", f"登録済みの写真との良い対応が最大 {best} 点（{MIN_OVERLAP} 点未満）。重なる写真が無い")
                continue
            via = [pid for n, pid in ov[:n_via] if n >= MIN_OVERLAP]
            try:
                e = register_via(r["id"], building_json, via, reg, idx, cache, by="register-chain")
            except ChainFail as ex:
                failed[r["id"]] = (ex.code, str(ex))
                continue
            R_.upsert(reg, e)
            failed.pop(r["id"], None)
            added.append(e)
            reg_photos[e["photo"]] = e
            log(f"  登録: {e['photo'][:40]:<40} via {','.join(v[-9:] for v in via)}  {e['check']['reproj_rms_px']} px  "
                f"{e['check']['n_points']} 点  高さ {e['camera']['camera_height_m']} m  面 {','.join(v['face'] for v in e['visible_faces'])}")
        rounds.append({"round": rnd, "added": len(added), "photos": [e["photo"] for e in added]})
        log(f"{rnd} 周目：{len(added)} 枚登録")
        if not added:
            break
    # 理由を台帳に
    regd_keys = {(e["building"], e["photo"]) for e in reg["entries"]}
    nr = [n for n in reg.get("not_registered", []) if (n.get("building"), n.get("photo")) not in regd_keys]
    by_photo = {n["photo"]: n for n in nr if n.get("building") == b["id"]}
    for pid, (code, why) in failed.items():
        if (b["id"], pid) in regd_keys:
            continue
        rec = R_.photo_record(pid, idx) or {}
        n = by_photo.get(pid) or {"photo": pid, "building": b["id"], "face": rec.get("face"), "period": rec.get("period")}
        n["chain_reason"] = why
        n["chain_code"] = code
        if n not in nr:
            nr.append(n)
    reg["not_registered"] = nr
    dist: dict[str, int] = {}
    for code, _ in failed.values():
        dist[code] = dist.get(code, 0) + 1
    return {"rounds": rounds, "added": sum(r["added"] for r in rounds), "failed": {k: v[1] for k, v in failed.items()},
            "reasons": dist}
