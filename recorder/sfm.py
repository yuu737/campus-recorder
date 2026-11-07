"""SfM の登録：壁の平面を介した鎖（recorder.chain）では載らない写真（中庭・ピロティ・段・デッキ）の視点を、
写真どうしの対応だけでまとめて解き（視点の復元のみ。点群の形は使わない）、登録済みの写真に合わせて世界座標にする。

やり方
  1. 特徴点：鎖の登録と同じ RootSIFT（chain.FeatureCache。写真は元の場所から読むだけで写さない）。
     features="aliked-lightglue" では ALIKED（BSD-3）の特徴点を DB に書き、照合は LightGlue（Apache 2.0）で自前に回して
     COLMAP の幾何の検証（verify_matches、同じ閾値）に渡す。組の候補（2.）は RootSIFT のままで揃える。
  2. 組の選び方：各写真の特徴点を 2000 点に間引いて全部の組で突き合わせ、良い対応の多い上位 K 枚（＋撮影順で隣の写真）。
  3. COLMAP（pycolmap）：自分の特徴点をデータベースに書き、選んだ組だけを照合・幾何の検証 → 増分 SfM（初期の 2 枚 →
     三角測量 → PnP で 1 枚ずつ追加 → バンドル調整）。カメラは写真ごと、SIMPLE_PINHOLE（歪み無し。登録の形と同じ）、
     焦点距離は EXIF の 35mm 換算を初期値にしてバンドル調整で直す。主点は写真の中心に固定。
  4. 合わせ：SfM の模型ごとに、登録済みの写真（camera が世界座標）のうち模型にも入った物のカメラ位置で相似変換
     （並進・回転・縮尺。Umeyama の最小二乗）を求める。残差 > MAX_ALIGN_RESID_M の登録済み写真は外して再推定。
  5. 品質の止め：写真ごとに SfM の再投影誤差（RMS）、三角測量された点の数、世界座標でのカメラ高さ（足元は国土地理院
     5m 標高 − ground_z。無ければ 0）、見えている面があるか。落ちた物は理由を not_registered に。

登録の形は鎖と同じ kind "pose"（camera と visible_faces。H_plane は registry.expand_entries が camera から作る）、
source.method に "sfm"。登録済みの写真（plane・pose）は書き換えない。
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from . import registry as R_
from .chain import FeatureCache
from .photos import INDEX_PATH, load_index
from .schema import load_building

MAX_REPROJ_PX = 3.0          # 写真ごとの SfM の再投影誤差（RMS、元の写真の px）
MIN_POINTS3D = 50            # 写真ごとの三角測量された点の数（これ以下は登録しない）
MIN_CAM_HEIGHT = 0.5         # 世界座標でのカメラ高さ（足元の地面から m）
MAX_CAM_HEIGHT = 10.0
MAX_ALIGN_RESID_M = 0.5      # 合わせの残差（m）。これより大きい登録済み写真は外して再推定
MAX_ALIGN_ROT_DEG = 30.0     # 合わせた後の向きの差（度。本部棟の正しい塊で最大 15.6°、裏返しは 100〜178°）。位置だけの合わせは基準が固まっていると向きが決まらず、
                             # 裏返し（11/6 の実験で 170° 前後）でも残差が小さく出るので、向きでも止める
MIN_ANCHORS = 3              # 合わせに要る登録済み写真の数
RETRIEVAL_N = 2000           # 組を選ぶ時に使う特徴点の数（間引き）
RETRIEVAL_K = 12             # 写真ごとに選ぶ相手の数
RETRIEVAL_MIN = 20           # 良い対応がこれ未満の組は選ばない
SEQ_NEIGHBORS = 2            # 撮影順で前後この枚数も組にする（同じ期間の中で）
MATCH_CHUNK = 40             # 照合を何組ずつ COLMAP に渡すか（時間の打ち切りの単位）
VERIFY_PX = 6.0              # 幾何の検証・三角測量の閾値（元の写真の px。特徴点は長辺 2000 px で取っている）
MIN_PAIR_INLIERS = 15        # 幾何で「通った」と数える組の内点の数（COLMAP の TwoViewGeometryOptions.min_num_inliers の既定と同じ）

# 特徴点の方式。rootsift は鎖の登録と同じ（既定）。aliked-lightglue は学習済みの特徴点 ALIKED（BSD-3）と
# 照合器 LightGlue（Apache 2.0）。どちらも組の選び方（retrieval_pairs）は RootSIFT のままにして、候補の組を揃える。
FEATURES = ("rootsift", "aliked-lightglue")
ALIKED_MAX_KP = 2048         # 写真ごとの ALIKED の特徴点の数（GPU 6GB を他の係と分けるので控えめ）
ALIKED_MAX_SIDE = 1600       # ALIKED に渡す画像の長辺（px）
ALIKED_MODEL = "aliked-n16"
FEATURE_LABEL = {"rootsift": "RootSIFT（OpenCV SIFT）→ COLMAP の照合",
                 "aliked-lightglue": "ALIKED（BSD-3）の特徴点 → LightGlue（Apache 2.0）の照合"}


class SfmFail(ValueError):
    def __init__(self, reason: str, code: str):
        super().__init__(reason)
        self.code = code


# --- 相似変換（Umeyama）---------------------------------------------------------------------

def umeyama(src: np.ndarray, dst: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """dst ≈ s·R·src + T の最小二乗（src・dst は N×3、N ≥ 3 で一直線でない）。返り値 (s, R, T)。"""
    src, dst = np.asarray(src, float), np.asarray(dst, float)
    ms, md = src.mean(0), dst.mean(0)
    a, b = src - ms, dst - md
    cov = b.T @ a / len(src)
    U, S, Vt = np.linalg.svd(cov)
    D = np.eye(3)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        D[2, 2] = -1
    R = U @ D @ Vt
    var = (a ** 2).sum() / len(src)
    s = float(np.trace(np.diag(S) @ D) / var) if var > 0 else 1.0
    return s, R, md - s * R @ ms


def center(R, t) -> np.ndarray:
    return -np.asarray(R, float).T @ np.asarray(t, float).reshape(3)


def apply_sim(sim: dict, R, t) -> tuple[np.ndarray, np.ndarray]:
    """SfM の視点（世界→カメラの R, t）を世界座標の視点に。"""
    s, Rs, T = sim["s"], np.asarray(sim["R"], float), np.asarray(sim["T"], float)
    Cw = s * Rs @ center(R, t) + T
    Rw = np.asarray(R, float) @ Rs.T
    return Rw, -Rw @ Cw


def rot_deg(Ra, Rb) -> float:
    c = (np.trace(np.asarray(Ra, float) @ np.asarray(Rb, float).T) - 1) / 2
    return float(np.degrees(np.arccos(np.clip(c, -1, 1))))


def align(sfm: dict[str, dict], anchors: dict[str, dict], max_resid: float = MAX_ALIGN_RESID_M,
          min_anchors: int = MIN_ANCHORS, max_rot: float = MAX_ALIGN_ROT_DEG) -> dict:
    """SfM の模型を世界座標に合わせる。sfm: {写真 id: {R, t}}（SfM の座標）、anchors: {写真 id: 登録の camera}。
    残差（カメラ位置の差 m）が max_resid を超える登録済み写真を 1 枚ずつ外して再推定する。
    残った基準の向きの差が max_rot 度を超えたら合わせない（code "rot"）。
    返り値 {ok, s, R, T, used, dropped{id: 残差}, residual_m{id}, rot_deg{id}, rms_m} か {ok False, reason}。"""
    common = sorted(k for k in sfm if k in anchors)
    used = list(common)
    dropped: dict[str, float] = {}
    while True:
        if len(used) < min_anchors:
            return {"ok": False, "code": "few_anchors", "used": used, "dropped": dropped,
                    "reason": f"模型に入った登録済みの写真が {len(used)} 枚（合わせに {min_anchors} 枚要る）"}
        src = np.array([center(sfm[k]["R"], sfm[k]["t"]) for k in used])
        dst = np.array([center(anchors[k]["R_cv"], anchors[k]["t_cv"]) for k in used])
        if np.linalg.matrix_rank(src - src.mean(0), tol=1e-6 * max(1.0, np.abs(src).max())) < 2:
            return {"ok": False, "code": "degenerate", "used": used, "dropped": dropped,
                    "reason": "登録済みの写真のカメラ位置が一直線に並んでいて向きが決まらない"}
        s, Rs, T = umeyama(src, dst)
        res = np.linalg.norm((s * (Rs @ src.T)).T + T - dst, axis=1)
        worst = int(np.argmax(res))
        if res[worst] > max_resid and len(used) > min_anchors:
            dropped[used[worst]] = round(float(res[worst]), 3)
            used.pop(worst)
            continue
        sim = {"s": float(s), "R": Rs, "T": T}
        rd = {k: round(rot_deg(apply_sim(sim, sfm[k]["R"], sfm[k]["t"])[0], anchors[k]["R_cv"]), 2) for k in used}
        ok = bool(res.max() <= max_resid)
        rot_ok = max(rd.values()) <= max_rot
        out = {"ok": ok and rot_ok, "s": float(s), "R": Rs, "T": T, "used": used, "dropped": dropped,
               "residual_m": {k: round(float(r), 3) for k, r in zip(used, res)}, "rot_deg": rd,
               "rms_m": round(float(np.sqrt(np.mean(res ** 2))), 3)}
        if not ok:
            out.update(code="resid", reason=f"残りの登録済み写真でも合わせの残差が {res.max():.2f} m（{max_resid} m 超）")
        elif not rot_ok:
            out.update(code="rot", reason=f"位置は合うが向きが登録済みの写真と {max(rd.values()):.0f}° 違う（{max_rot}° 超。"
                                          "基準の位置が固まっていて向きが決まらないか、塊が裏返しにつながっている）")
        return out


# --- 品質の止めと登録 ------------------------------------------------------------------------

def ground_at(x: float, y: float, b: dict, dem: dict | None) -> tuple[float, str]:
    """カメラの足元の地面（棟の z=0 からの m）。5m 標高があればそれ − ground_z、無ければ 0。"""
    if dem is not None:
        from .schema import Measurement
        from .site import dem_at

        gz = Measurement.from_json(b.get("ground_z"))
        z = dem_at(dem, x, y)
        if z is not None and gz.known:
            return float(z - gz.value), "gsi:dem5a − ground_z"
    return 0.0, "棟の z=0（5m 標高が無い）"


def judge(item: dict, Rw, tw, b: dict, dem: dict | None) -> tuple[dict | None, str | None, str | None]:
    """写真 1 枚の品質の止め。返り値 (camera, code, reason)。通れば code は None。"""
    if item["reproj_rms_px"] > MAX_REPROJ_PX:
        return None, "reproj", f"SfM の再投影誤差 {item['reproj_rms_px']:.1f} px（{MAX_REPROJ_PX} px 超）"
    if item["n_points3d"] <= MIN_POINTS3D:
        return None, "few_points", f"三角測量された点が {item['n_points3d']} 点（{MIN_POINTS3D} 点以下）"
    C = center(Rw, tw)
    g, _ = ground_at(float(C[0]), float(C[1]), b, dem)
    hgt = float(C[2] - g)
    cam = R_.camera_dict(Rw, tw, item["focal_px"], item["w"], item["h"], g)
    if not (MIN_CAM_HEIGHT <= hgt <= MAX_CAM_HEIGHT):
        return cam, "camera_height", f"カメラの高さが地面から {hgt:.1f} m（{MIN_CAM_HEIGHT}〜{MAX_CAM_HEIGHT} m の外）"
    return cam, None, None


def make_entry(rec: dict, b: dict, item: dict, cam: dict, vis: list[dict], sim: dict, model_id, by: str) -> dict:
    return {
        "photo": rec["id"], "building": b["id"], "face": rec.get("face") or "(pose)", "kind": "pose",
        "period": rec.get("period"), "file": rec.get("file"), "image_size": [int(item["w"]), int(item["h"])],
        "camera": cam, "visible_faces": vis, "via": sim["used"],
        "source": {"method": f"sfm：{FEATURE_LABEL[sim.get('features', 'rootsift')]} → COLMAP（pycolmap）の増分 SfM"
                             "（幾何の検証・三角測量・PnP・バンドル調整） → 登録済みの写真のカメラ位置で相似変換（Umeyama）",
                   "features": sim.get("features", "rootsift"),
                   "kind": "sfm", "focal_source": "exif_35mm を初期値にバンドル調整", "focal_exif_px": item.get("focal_exif_px"),
                   "model": model_id, "anchors": sim["used"], "anchors_dropped": sim["dropped"],
                   "align_rms_m": sim["rms_m"], "scale": round(sim["s"], 6)},
        "check": {"reproj_rms_px": round(item["reproj_rms_px"], 2), "n_points3d": int(item["n_points3d"]),
                  "camera_height_m": cam["camera_height_m"], "align_rms_m": sim["rms_m"],
                  "align_max_m": max(sim["residual_m"].values()) if sim["residual_m"] else None},
        "registered_at": datetime.now().isoformat(timespec="seconds"),
        "registered_by": by,
    }


def register_models(models: list[dict], b: dict, reg: dict, index: dict, dem: dict | None = None,
                    targets: set[str] | None = None, by: str = "register-sfm", features: str = "rootsift") -> dict:
    """SfM の結果（模型の一覧。各模型は {id, images: {写真 id: {R, t, focal_px, w, h, n_points3d, reproj_rms_px}}}）を
    世界座標に合わせて台帳に登録する（reg を書き換える。保存は呼ぶ側）。登録済みの写真は書き換えない。
    targets：登録を試す写真 id（省略で未登録の全部）。返り値は報告用の集計。"""
    ents = R_.entries_for(reg, b["id"])
    done = {e["photo"] for e in ents}
    anchors: dict[str, dict] = {}
    for e in ents:
        cam = e.get("camera") or {}
        if cam.get("R_cv") and e["photo"] not in anchors:
            anchors[e["photo"]] = cam
    want = {r["id"] for r in index.get("photos", []) if r.get("building") == b["id"] and r["id"] not in done}
    if targets is not None:
        want &= targets
    seen: set[str] = set()
    added, failed, aligns = [], {}, []
    for m in sorted(models, key=lambda m: -len(m["images"])):
        imgs = {k: v for k, v in m["images"].items() if k not in seen}
        if not any(k in want for k in imgs):
            continue
        sim = align(m["images"], anchors)
        sim["features"] = features
        aligns.append({"model": m["id"], "n_images": len(m["images"]),
                       **{k: v for k, v in sim.items() if k not in ("R", "T", "features")}})
        for pid in imgs:
            if pid not in want:
                continue
            seen.add(pid)
            if not sim["ok"]:
                failed[pid] = (sim["code"], f"SfM の模型 {m['id']}（{len(m['images'])} 枚）が世界座標に合わない：{sim['reason']}")
                continue
            it = imgs[pid]
            Rw, tw = apply_sim(sim, it["R"], it["t"])
            cam, code, why = judge(it, Rw, tw, b, dem)
            if code:
                failed[pid] = (code, why)
                continue
            vis = R_.visible_faces(b, cam)
            if not vis:
                failed[pid] = ("no_visible_face", f"解いた視点（{cam['camera_xyz']}）から見える棟の面が無い（面の前に出た部品だけを写した写真）")
                continue
            rec = R_.photo_record(pid, index) or {"id": pid}
            e = make_entry(rec, b, it, cam, vis, sim, m["id"], by)
            R_.upsert(reg, e)
            added.append(e)
    for pid in want - seen - set(failed):
        failed[pid] = ("not_in_model", "SfM の模型に入らなかった（他の写真との対応が足りない）")
    for pid, (code, why) in failed.items():
        R_.note_not_registered(reg, b["id"], pid, index, why, code, prefix="sfm")
    dist: dict[str, int] = {}
    for code, _ in failed.values():
        dist[code] = dist.get(code, 0) + 1
    return {"added": added, "failed": {k: v for k, v in failed.items()}, "reasons": dist, "aligns": aligns}


# --- ALIKED ＋ LightGlue（features="aliked-lightglue"）---------------------------------------

def aliked_available() -> bool:
    try:
        import lightglue  # noqa: F401
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


class AlikedLightGlue:
    """ALIKED の特徴点（写真ごとに npz でキャッシュ）と LightGlue の照合。写真は元の場所から読むだけで写さない。
    GPU が使えれば GPU（推論ごとに empty_cache）、メモリが足りなければ CPU に落とす。"""

    def __init__(self, cache_dir: Path | None = None, device: str | None = None, loader=None,
                 max_kp: int = ALIKED_MAX_KP, max_side: int = ALIKED_MAX_SIDE, log=print):
        import torch

        self.dir = Path(cache_dir) if cache_dir else None
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.loader = loader
        self.max_kp, self.max_side = max_kp, max_side
        self.mem: dict[str, dict] = {}
        self._ext = self._lg = None
        self.log = log

    # 模型は使う時に読む（重みは初回に torch.hub が落とす）
    def _models(self):
        if self._ext is None:
            from lightglue import ALIKED, LightGlue

            self._ext = ALIKED(model_name=ALIKED_MODEL, max_num_keypoints=self.max_kp, detection_threshold=-1).eval()
            self._lg = LightGlue(features="aliked").eval()
        self._ext.to(self.device)
        self._lg.to(self.device)
        return self._ext, self._lg

    def _to_cpu(self, why: str):
        import torch

        self.log(f"  GPU が使えないので CPU に落とす（{why}）")
        self.device = "cpu"
        torch.cuda.empty_cache()

    def _run(self, fn):
        import torch

        try:
            with torch.inference_mode():
                return fn()
        except (torch.cuda.OutOfMemoryError, RuntimeError) as ex:
            if self.device == "cpu" or "out of memory" not in str(ex).lower() and "CUDA" not in str(ex):
                raise
            self._to_cpu(str(ex).splitlines()[0][:80])
            with torch.inference_mode():
                return fn()
        finally:
            if self.device == "cuda":
                torch.cuda.empty_cache()

    def _load_image(self, path: Path) -> np.ndarray:
        if self.loader is not None:
            return self.loader(path)
        from .rectify import read_image

        return read_image(path)

    def get(self, key: str, path: Path) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
        """FeatureCache.get と同じ形：(特徴点 元の写真の px N×2, 記述子 N×128, (w, h))。"""
        d = self.feats(key, path)
        return d["pts"], d["des"], d["size"]

    def feats(self, key: str, path: Path) -> dict:
        if key in self.mem:
            return self.mem[key]
        npz = self.dir / f"{Path(key).stem}.npz" if self.dir else None
        if npz is not None and npz.exists():
            z = np.load(npz)
            d = {"kp": z["kp"], "des": z["des"], "scores": z["scores"], "size_small": tuple(int(v) for v in z["size_small"]),
                 "scale": float(z["scale"]), "size": (int(z["size"][0]), int(z["size"][1]))}
        else:
            import cv2
            from lightglue.utils import numpy_image_to_torch

            img = self._load_image(path)
            h, w = img.shape[:2]
            s = min(1.0, self.max_side / max(h, w))
            small = cv2.resize(img, (int(round(w * s)), int(round(h * s))), interpolation=cv2.INTER_AREA) if s < 1 else img
            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB) if small.ndim == 3 else cv2.cvtColor(small, cv2.COLOR_GRAY2RGB)

            def go():
                ext, _ = self._models()
                f = ext.extract(numpy_image_to_torch(rgb).to(self.device), resize=None)
                return (f["keypoints"][0].float().cpu().numpy(), f["descriptors"][0].float().cpu().numpy(),
                        f["keypoint_scores"][0].float().cpu().numpy())

            kp, des, sc = self._run(go)
            d = {"kp": kp.astype(np.float32), "des": des.astype(np.float32), "scores": sc.astype(np.float32),
                 "size_small": (small.shape[1], small.shape[0]), "scale": s, "size": (w, h)}
            if npz is not None:
                npz.parent.mkdir(parents=True, exist_ok=True)
                np.savez_compressed(npz, kp=d["kp"], des=d["des"], scores=d["scores"], size_small=np.array(d["size_small"]),
                                    scale=np.array(s), size=np.array(d["size"]))
        # 元の写真の px（画素の中心が整数。RootSIFT の features_of_image と同じ決まり）
        d["pts"] = (d["kp"].astype(float) + 0.5) / d["scale"] - 0.5
        self.mem[key] = d
        return d

    def match(self, a: dict, b: dict) -> np.ndarray:
        """LightGlue の対応（K×2 の添字 [a, b]）。"""
        import torch

        def go():
            _, lg = self._models()

            def pack(d):
                return {"keypoints": torch.from_numpy(d["kp"])[None].to(self.device),
                        "descriptors": torch.from_numpy(d["des"])[None].to(self.device),
                        "image_size": torch.tensor([d["size_small"]], dtype=torch.float32, device=self.device)}

            out = lg({"image0": pack(a), "image1": pack(b)})
            return out["matches"][0].cpu().numpy()

        if len(a["kp"]) < 2 or len(b["kp"]) < 2:
            return np.zeros((0, 2), np.uint32)
        return self._run(go).astype(np.uint32).reshape(-1, 2)


def match_pairs_lightglue(db_path: Path, pairs: list[tuple[str, str]], names: dict[str, str], ids: dict[str, int],
                          work: Path, budget_s: float | None, alg: AlikedLightGlue, feats: dict[str, dict],
                          log=print) -> int:
    """match_pairs と同じ出口：LightGlue で照合して matches を書き、COLMAP の幾何の検証（verify_matches、閾値は同じ
    VERIFY_PX）で two_view_geometries を書く。済んだ組（検証まで）は飛ばす。返り値は残りの組の数。"""
    import pycolmap as pc

    db = pc.Database.open(str(db_path))
    try:
        todo = [(a, b) for a, b in pairs if not db.exists_two_view_geometry(ids[a], ids[b])]
    finally:
        db.close()
    if not todo:
        return 0
    log(f"  照合（LightGlue）：{len(todo)} 組（全 {len(pairs)} 組）")
    vo = pc.TwoViewGeometryOptions()
    vo.ransac.max_error = VERIFY_PX
    t0 = time.time()
    done = 0
    for i in range(0, len(todo), MATCH_CHUNK):
        chunk = todo[i:i + MATCH_CHUNK]
        db = pc.Database.open(str(db_path))
        try:
            for a, b in chunk:
                if db.exists_matches(ids[a], ids[b]):
                    continue
                db.write_matches(ids[a], ids[b], alg.match(feats[a], feats[b]))
        finally:
            db.close()
        pf = work / "pairs_chunk.txt"
        pf.write_text("\n".join(f"{names[a]} {names[b]}" for a, b in chunk) + "\n", encoding="utf-8")
        pc.verify_matches(str(db_path), str(pf), options=vo)
        done += len(chunk)
        el = time.time() - t0
        log(f"    {done}/{len(todo)} 組（{el:.0f} 秒）")
        if budget_s is not None and el > budget_s and done < len(todo):
            return len(todo) - done
    return 0


def pair_stats(db_path: Path, pairs: list[tuple[str, str]], ids: dict[str, int], min_inliers: int = MIN_PAIR_INLIERS) -> dict:
    """候補の組のうち、照合の対応の数と幾何で通った（内点 ≥ min_inliers）組の数。"""
    import pycolmap as pc

    db = pc.Database.open(str(db_path))
    try:
        raw, inl = [], []
        for a, b in pairs:
            ia, ib = ids[a], ids[b]
            raw.append(int(db.read_matches(ia, ib).shape[0]) if db.exists_matches(ia, ib) else 0)
            if db.exists_two_view_geometry(ia, ib):
                g = db.read_two_view_geometry(ia, ib)
                inl.append(int(np.asarray(g.inlier_matches).shape[0]))
            else:
                inl.append(0)
    finally:
        db.close()
    ok = [n for n in inl if n >= min_inliers]
    return {"n_candidates": len(pairs), "n_verified": len(ok), "min_inliers": min_inliers,
            "median_matches": int(np.median(raw)) if raw else 0,
            "median_inliers_verified": int(np.median(ok)) if ok else 0}


# --- COLMAP（pycolmap）を回す ----------------------------------------------------------------

def _ascii_name(pid: str) -> str:
    """COLMAP に渡す写真の名前（日本語の id は COLMAP が読めないので、id の要約に）。"""
    if pid.isascii():
        return pid + ".jpg"
    return "p_" + hashlib.sha1(pid.encode("utf-8")).hexdigest()[:12] + ".jpg"


def retrieval_pairs(keys: list[str], feats: dict, k: int = RETRIEVAL_K, seq: list[list[str]] | None = None,
                    cache_json: Path | None = None, log=print) -> list[tuple[str, str]]:
    """照合する組：間引いた特徴点で全部の組の良い対応を数え、写真ごとに上位 k 枚。seq（撮影順の列）の前後も足す。"""
    import cv2

    scores: dict[str, int] = {}
    if cache_json and cache_json.exists():
        scores = json.loads(cache_json.read_text(encoding="utf-8"))
    rng = np.random.default_rng(0)
    sub = {}
    for key in keys:
        des = feats[key][1]
        sel = rng.choice(len(des), size=min(RETRIEVAL_N, len(des)), replace=False) if len(des) else np.zeros(0, int)
        sub[key] = np.ascontiguousarray(des[np.sort(sel)])
    bf = cv2.BFMatcher(cv2.NORM_L2)
    t0, n_new = time.time(), 0
    for i, a in enumerate(keys):
        for bkey in keys[i + 1:]:
            ck = f"{a}|{bkey}"
            if ck in scores:
                continue
            if len(sub[a]) < 2 or len(sub[bkey]) < 2:
                scores[ck] = 0
                continue
            knn = bf.knnMatch(sub[a], sub[bkey], k=2)
            scores[ck] = int(sum(1 for m in knn if len(m) == 2 and m[0].distance < 0.8 * m[1].distance))
            n_new += 1
    if n_new:
        log(f"  組の選び方：{n_new} 組を数えた（{time.time() - t0:.0f} 秒）")
    if cache_json:
        cache_json.parent.mkdir(parents=True, exist_ok=True)
        cache_json.write_text(json.dumps(scores), encoding="utf-8")
    pairs: set[tuple[str, str]] = set()
    for a in keys:
        cand = []
        for bkey in keys:
            if bkey == a:
                continue
            ck = f"{a}|{bkey}" if f"{a}|{bkey}" in scores else f"{bkey}|{a}"
            cand.append((scores.get(ck, 0), bkey))
        cand.sort(reverse=True)
        for sc, bkey in cand[:k]:
            if sc >= RETRIEVAL_MIN:
                pairs.add(tuple(sorted((a, bkey))))
    for row in seq or []:
        for i, a in enumerate(row):
            for bkey in row[i + 1:i + 1 + SEQ_NEIGHBORS]:
                pairs.add(tuple(sorted((a, bkey))))
    return sorted(pairs)


def build_database(db_path: Path, items: list[dict], feats: dict, write_descriptors: bool = True) -> dict[str, int]:
    """自分の特徴点を COLMAP のデータベースに書く（既にある写真は飛ばす）。items: {key, name, w, h, focal}。
    返り値 {key: image_id}。特徴点は COLMAP の画素の決まり（画素の中心が +0.5）に直し、主点も +0.5 する
    （そうすると解いた視点が台帳の K（主点 w/2・h/2、画素の中心が整数）とそのまま合う）。"""
    import pycolmap as pc

    db = pc.Database.open(str(db_path))
    try:
        have = {im.name: im.image_id for im in db.read_all_images()}
        ids = {}
        for it in items:
            if it["name"] in have:
                ids[it["key"]] = have[it["name"]]
                continue
            w, h = it["w"], it["h"]
            cam = pc.Camera(model="SIMPLE_PINHOLE", width=w, height=h, params=[it["focal"], w / 2 + 0.5, h / 2 + 0.5])
            cam.has_prior_focal_length = True
            cid = db.write_camera(cam)
            sensor = pc.sensor_t(type=pc.SensorType.CAMERA, id=cid)
            rig = pc.Rig()
            rig.add_ref_sensor(sensor)
            rid = db.write_rig(rig)
            iid = db.write_image(pc.Image(name=it["name"], camera_id=cid))
            fr = pc.Frame()
            fr.rig_id = rid
            fr.add_data_id(pc.data_t(sensor_id=sensor, id=iid))
            db.write_frame(fr)
            pts, des, _ = feats[it["key"]]
            db.write_keypoints(iid, (np.asarray(pts, np.float32) + 0.5).astype(np.float32))
            if write_descriptors:       # RootSIFT は COLMAP の SIFT 形式で照合する。ALIKED は自前の LightGlue で照合するので書かない
                d8 = np.clip(np.round(np.asarray(des, np.float32) * 512.0), 0, 255).astype(np.uint8)
                db.write_descriptors(iid, pc.FeatureDescriptors(pc.FeatureExtractorType.SIFT, d8))
            ids[it["key"]] = iid
        return ids
    finally:
        db.close()


def match_pairs(db_path: Path, pairs: list[tuple[str, str]], names: dict[str, str], ids: dict[str, int],
                work: Path, budget_s: float | None, log=print) -> int:
    """選んだ組を照合・幾何の検証（済んだ組は飛ばす）。時間の上限を超えたら途中で止める。返り値は残りの組の数。"""
    import pycolmap as pc

    db = pc.Database.open(str(db_path))
    try:
        todo = [(a, b) for a, b in pairs if not db.exists_matches(ids[a], ids[b])]
    finally:
        db.close()
    if not todo:
        return 0
    log(f"  照合：{len(todo)} 組（全 {len(pairs)} 組）")
    mo = pc.FeatureMatchingOptions()
    mo.max_num_matches = 16384
    vo = pc.TwoViewGeometryOptions()
    vo.ransac.max_error = VERIFY_PX
    t0 = time.time()
    done = 0
    for i in range(0, len(todo), MATCH_CHUNK):
        chunk = todo[i:i + MATCH_CHUNK]
        pf = work / "pairs_chunk.txt"
        pf.write_text("\n".join(f"{names[a]} {names[b]}" for a, b in chunk) + "\n", encoding="utf-8")
        po = pc.ImportedPairingOptions()
        po.match_list_path = str(pf)
        pc.match_image_pairs(str(db_path), matching_options=mo, pairing_options=po, verification_options=vo)
        done += len(chunk)
        el = time.time() - t0
        log(f"    {done}/{len(todo)} 組（{el:.0f} 秒）")
        if budget_s is not None and el > budget_s and done < len(todo):
            return len(todo) - done
    return 0


def run_mapping(db_path: Path, work: Path, sparse_name: str = "sparse") -> list:
    import pycolmap as pc

    (work / "images_none").mkdir(parents=True, exist_ok=True)
    sp = work / sparse_name
    sp.mkdir(parents=True, exist_ok=True)
    o = pc.IncrementalPipelineOptions()
    o.extract_colors = False
    o.min_model_size = 3
    o.multiple_models = True
    o.mapper.filter_max_reproj_error = VERIFY_PX
    o.mapper.abs_pose_max_error = 2 * VERIFY_PX
    o.mapper.init_max_error = VERIFY_PX
    o.triangulation.merge_max_reproj_error = VERIFY_PX
    o.triangulation.complete_max_reproj_error = VERIFY_PX
    o.ba_refine_extra_params = False
    o.random_seed = 0
    recs = pc.incremental_mapping(str(db_path), str(work / "images_none"), str(sp), o)
    return list(recs.values())


def models_from_reconstructions(recs: list, name_to_key: dict[str, str]) -> list[dict]:
    """pycolmap の Reconstruction → 模型の一覧（写真ごとの R, t, 焦点距離、点の数、再投影誤差）。"""
    out = []
    for mi, r in enumerate(recs):
        imgs = {}
        for iid, im in r.images.items():
            key = name_to_key.get(im.name)
            if key is None or not im.has_pose:
                continue
            cam = r.cameras[im.camera_id]
            pose = im.cam_from_world() if callable(im.cam_from_world) else im.cam_from_world
            Rm = np.asarray(pose.rotation.matrix(), float)
            t = np.asarray(pose.translation, float).reshape(3)
            errs = []
            for p2 in im.points2D:
                if p2.has_point3D():
                    X = r.points3D[p2.point3D_id].xyz
                    q = Rm @ X + t
                    if q[2] <= 0:
                        continue
                    uv = cam.params[0] * q[:2] / q[2] + np.asarray(cam.params[1:3], float)
                    errs.append(float(np.hypot(*(uv - np.asarray(p2.xy, float)))))
            imgs[key] = {"R": Rm, "t": t, "focal_px": float(cam.params[0]), "w": int(cam.width), "h": int(cam.height),
                         "n_points3d": int(im.num_points3D),
                         "reproj_rms_px": float(np.sqrt(np.mean(np.square(errs)))) if errs else float("inf")}
        out.append({"id": mi, "images": imgs, "n_points3d": int(r.num_points3D())})
    return out


def collect(b: dict, index: dict, photos: list[str] | None, max_n: int | None, reg: dict) -> tuple[list[dict], dict]:
    """SfM に入れる写真：棟の写真全部（期間中・期間前。登録済みは合わせの基準として必ず入れる）。"""
    recs = [r for r in index.get("photos", []) if r.get("building") == b["id"] and r["id"] not in R_.excluded_ids(reg, b["id"])]
    done = {e["photo"] for e in R_.entries_for(reg, b["id"])}
    skipped = {}
    out = []
    for r in recs:
        p = R_.photo_path(r)
        if not p.exists():
            skipped[r["id"]] = ("no_file", f"写真が見つからない（{p}）")
            continue
        if not r.get("focal_35mm"):
            skipped[r["id"]] = ("no_focal", "焦点距離（35mm 換算）が索引に無い")
            continue
        out.append(r)
    anchors = [r for r in out if r["id"] in done]
    rest = [r for r in out if r["id"] not in done]
    if photos:
        rest = [r for r in rest if any(r["id"].startswith(p) for p in photos)]
    if max_n:
        rest = rest[:max_n]
    return anchors + rest, skipped


def register_sfm(building_json: Path, reg: dict, index: dict | None = None, photos: list[str] | None = None,
                 max_n: int | None = None, work: Path | None = None, budget_s: float | None = None,
                 cache: FeatureCache | None = None, features: str = "rootsift", aliked_cache: Path | None = None,
                 log=print) -> dict:
    """棟の写真で SfM を回し、未登録の写真を世界座標で登録する（reg を書き換える。保存は呼ぶ側）。
    照合が時間の上限で途中になったら {"pending": 残りの組} を返す（同じコマンドで続きから）。
    features：特徴点と照合の方式（"rootsift" 既定／"aliked-lightglue"）。組の候補はどちらも RootSIFT で選ぶ。
    aliked-lightglue のデータベース・模型・集計は rootsift と別の名前（database_aliked.db・sparse_aliked・
    register_sfm_aliked.json）、特徴のキャッシュは out/<棟>/sfm/features_aliked/。"""
    from .pose import focal_px_from_35mm

    if features not in FEATURES:
        raise ValueError(f"features は {FEATURES} のどれか（{features!r}）")
    idx = index or load_index(INDEX_PATH)
    b = load_building(building_json)
    work = work or Path("out") / b["id"] / "sfm"
    work.mkdir(parents=True, exist_ok=True)
    cache = cache or FeatureCache(Path("out") / b["id"] / "chain_feats")
    tag = "" if features == "rootsift" else "_aliked"
    alg = None
    if features == "aliked-lightglue":
        alg = AlikedLightGlue(aliked_cache or Path("out") / b["id"] / "sfm" / "features_aliked", log=log)
    recs, skipped = collect(b, idx, photos, max_n, reg)
    timing: dict[str, float] = {}
    t0 = time.time()
    feats, items, names = {}, [], {}
    for r in recs:
        f = cache.get(r["id"], R_.photo_path(r))
        feats[r["id"]] = f
        names[r["id"]] = _ascii_name(r["id"])
    timing["features_rootsift_s"] = round(time.time() - t0, 1)
    db_feats = feats
    if alg is not None:
        t0 = time.time()
        al = {r["id"]: alg.feats(r["id"], R_.photo_path(r)) for r in recs}
        db_feats = {k: (d["pts"], d["des"], d["size"]) for k, d in al.items()}
        timing["features_aliked_s"] = round(time.time() - t0, 1)
        log(f"  ALIKED：{len(al)} 枚（{alg.device}、{timing['features_aliked_s']:.0f} 秒）")
    for r in recs:
        w, h = db_feats[r["id"]][2]
        items.append({"key": r["id"], "name": names[r["id"]], "w": w, "h": h,
                      "focal": focal_px_from_35mm(float(r["focal_35mm"]), w, h)})
    log(f"写真 {len(recs)} 枚（登録済み {sum(1 for r in recs if r['id'] in {e['photo'] for e in R_.entries_for(reg, b['id'])})}）"
        f"・特徴点 {sum(timing.values()):.0f} 秒・方式 {features}")
    keys = [r["id"] for r in recs]
    seq: dict[str, list[tuple[str, str]]] = {}
    for r in recs:
        seq.setdefault(r.get("period") or "", []).append((r.get("taken_at") or "", r["id"]))
    seq_rows = [[k for _, k in sorted(v)] for v in seq.values()]
    t0 = time.time()
    pairs = retrieval_pairs(keys, feats, seq=seq_rows, cache_json=work / "retrieval.json", log=log)
    timing["retrieval_s"] = round(time.time() - t0, 1)
    db_path = work / f"database{tag}.db"
    ids = build_database(db_path, items, db_feats, write_descriptors=alg is None)
    (work / "names.json").write_text(json.dumps({names[k]: k for k in keys}, ensure_ascii=False, indent=1), encoding="utf-8")
    t0 = time.time()
    if alg is None:
        left = match_pairs(db_path, pairs, names, ids, work, budget_s, log)
    else:
        left = match_pairs_lightglue(db_path, pairs, names, ids, work, budget_s, alg, al, log)
    timing["matching_s"] = round(time.time() - t0, 1)
    if left:
        return {"pending": left, "n_pairs": len(pairs), "added": [], "failed": {}, "reasons": {}, "aligns": []}
    pst = pair_stats(db_path, pairs, ids)
    log(f"  幾何で通った組 {pst['n_verified']} / 候補 {pst['n_candidates']}（内点 {pst['min_inliers']} 以上）")
    t1 = time.time()
    recons = run_mapping(db_path, work, sparse_name=f"sparse{tag}")
    timing["mapping_s"] = round(time.time() - t1, 1)
    models = models_from_reconstructions(recons, {names[k]: k for k in keys})
    log(f"SfM：模型 {len(models)} 個（{', '.join(str(len(m['images'])) + ' 枚' for m in models)}）・{time.time() - t1:.0f} 秒")
    dem = None
    try:
        from .site import load_dem

        dem = load_dem()
    except (OSError, FileNotFoundError, KeyError):
        dem = None
    targets = {r["id"] for r in recs}
    res = register_models(models, b, reg, idx, dem, targets=targets, features=features)
    for pid, (code, why) in skipped.items():
        if pid not in {e["photo"] for e in R_.entries_for(reg, b["id"])}:
            res["failed"][pid] = (code, why)
            res["reasons"][code] = res["reasons"].get(code, 0) + 1
            R_.note_not_registered(reg, b["id"], pid, idx, why, code, prefix="sfm")
    res.update(n_pairs=len(pairs), models=[{"id": m["id"], "n_images": len(m["images"]), "n_points3d": m["n_points3d"]}
                                           for m in models], pending=0, features=features, pair_stats=pst,
               timing=timing, models_full=models, device=getattr(alg, "device", "cpu"))
    summ = {"made_by": "register-sfm（recorder）", "made_at": datetime.now().isoformat(timespec="seconds"),
            "building": b["id"], "features": features, "n_photos": len(recs), "n_pairs": len(pairs), "pair_stats": pst,
            "timing": timing, "models": res["models"],
            "aligns": res["aligns"], "added": [e["photo"] for e in res["added"]],
            "failed": {k: {"code": c, "reason": w} for k, (c, w) in res["failed"].items()}, "reasons": res["reasons"]}
    (work / f"register_sfm{tag}.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return res
