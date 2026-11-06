"""目印の登録の 3 条件の実験（11/6）：cv2（従来）／PoseLib／PoseLib＋GeoCalib。

(A) 真値あり：本部棟の登録済み写真（台帳の pose・landmarks 以外）で、正しい目印を升＋寄りの升に丸めた対応（手順書の 4 項と同じ）を
    6・4・3 点（＋「3 点＋線 2 本」）で解き、位置の誤差と合否の門を数える。
(B) 本番：out/tower75/landmarks/picks_*.json を 3 条件で解き直す（台帳には書かない）。
(C) GeoCalib 単体：登録済み写真の傾き・仰角・焦点を台帳の視点・EXIF と比べる（消失点の推定と並べる）。

使い方：.venv/Scripts/python -I -X utf8 -m recorder.landmarks_eval [--only A,B,C]
結果：out/experiments/landmarks_3cond.json（docs/実験_登録の3条件_11-06.md に表）。
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from . import landmarks as LM
from . import registry as R_
from .locate import EdgeDT, angles_of
from .photos import INDEX_PATH, load_index
from .rectify import read_image
from .schema import load_building

METHODS = ("cv2", "poselib", "poselib+geocalib")
OUT = Path("out/experiments/landmarks_3cond.json")


def truth_entries(reg: dict, building: str) -> list[dict]:
    """台帳の登録済み（視点あり、目印による登録は除く＝真値として独立）。"""
    return [e for e in reg.get("entries", []) if e["building"] == building and (e.get("camera") or {}).get("R_cv")
            and (e.get("source") or {}).get("kind") != "landmarks"]


def _round_pick(u: float, v: float, w: int, h: int) -> dict:
    """写真の点 → 升＋寄りの升（目印係が正しく指した時の精度）。"""
    col, row = min(9, max(0, int(u / w * 10))), min(9, max(0, int(v / h * 10)))
    cell = f"{LM.COLS[col]}{row + 1}"
    x0, y0, x1, y1 = LM.cell_rect(cell, w, h, expand=LM.CROP_EXPAND)
    sc = min(9, max(0, int((u - x0) / (x1 - x0) * 10)))
    sr = min(9, max(0, int((v - y0) / (y1 - y0) * 10)))
    return {"cell": cell, "sub": f"{LM.COLS[sc]}{sr + 1}"}


def _rects(b: dict) -> list[tuple]:
    out = []
    for f in b.get("faces", []):
        if f.get("from") and f.get("to"):
            fr = R_.face_rect_frame(b, f)
            p0, u, _, L = R_.face_frame(fr)
            out.append((f["id"], p0, u, L, fr["ground_offset"], fr["top_z"]))
    return out


def _facing(b: dict, C) -> dict:
    out = {}
    for f in b.get("faces", []):
        if f.get("from") and f.get("to"):
            p0, _, nv, _ = R_.face_frame(R_.face_rect_frame(b, f))
            out[f["id"]] = float((np.asarray(C[:2]) - p0) @ nv) > 0.3
    return out


def synth_picks(b: dict, cam: dict, w: int, h: int, n: int = 6) -> tuple[list[dict], list[dict]]:
    """真の視点で見える目印を n 点（写真の中で遠い順に散らす）と、見える線を縦横 1 本ずつ。升＋寄りに丸める。"""
    R, t, f = np.array(cam["R_cv"]), np.array(cam["t_cv"]), cam["focal_px"]
    C = -R.T @ t
    cat = LM.catalog(b, lines=True)
    rects, facing = _rects(b), _facing(b, C)
    pts = [m for m in cat if m.get("kind") != "line" and facing.get(m["face"])]
    P = np.array([m["xyz"] for m in pts]).reshape(-1, 3)
    q, d = LM._project(R, t, f, w, h, P)
    occ = np.array([R_._occluded(C, P[i:i + 1], rects, pts[i]["face"])[0] for i in range(len(P))]) if len(P) else np.zeros(0, bool)
    ok = (d > 1) & (q[:, 0] > 0.03 * w) & (q[:, 0] < 0.97 * w) & (q[:, 1] > 0.03 * h) & (q[:, 1] < 0.97 * h) & ~occ
    idx = np.nonzero(ok)[0]
    picks = []
    if len(idx):
        # 遠い点から順に（写真の中で散らす）。1 点目は左上の角に一番近い点
        order = [int(idx[np.argmin(q[idx, 0] / w + q[idx, 1] / h)])]
        while len(order) < min(n, len(idx)):
            dist = np.min(np.linalg.norm((q[idx][:, None, :] - q[order][None, :, :]) / [w, h], axis=2), axis=1)
            order.append(int(idx[int(np.argmax(dist))]))
        for i in order:
            picks.append({"id": pts[i]["id"], **_round_pick(q[i, 0], q[i, 1], w, h), "_uv_true": [float(q[i, 0]), float(q[i, 1])]})
    lines = []
    for want in ("v", "h"):
        best = None
        for m in cat:
            if m.get("kind") != "line" or m["dir"] != want or not facing.get(m["face"]):
                continue
            X = np.linspace(m["xyz1"], m["xyz2"], 40)
            qq, dd = LM._project(R, t, f, w, h, X)
            vis = (dd > 1) & (qq[:, 0] > 0.03 * w) & (qq[:, 0] < 0.97 * w) & (qq[:, 1] > 0.03 * h) & (qq[:, 1] < 0.97 * h) \
                & ~R_._occluded(C, X, rects, m["face"])
            k = np.nonzero(vis)[0]
            if len(k) < 4:
                continue
            a, c = qq[k[len(k) // 5]], qq[k[(4 * len(k)) // 5]]
            ext = float(np.hypot(*(c - a)) / max(w, h))
            if ext > 0.2 and (best is None or ext > best[0]):
                best = (ext, m, a, c)
        if best:
            _, m, a, c = best
            lines.append({"id": m["id"], "cells": [_round_pick(a[0], a[1], w, h), _round_pick(c[0], c[1], w, h)]})
    return picks, lines


def _pose_err(sol: dict | None, C_true) -> float | None:
    if not sol:
        return None
    C = -np.asarray(sol["R"]).T @ np.asarray(sol["t"])
    return float(np.linalg.norm(C - C_true))


def _summ(errs: list, n_total: int) -> dict:
    e = [x for x in errs if x is not None]
    return {"n": n_total, "solved": len(e), "median_m": round(float(np.median(e)), 2) if e else None,
            "within_2m": sum(1 for x in e if x <= 2.0), "within_2m_frac": round(sum(1 for x in e if x <= 2.0) / n_total, 2) if n_total else None}


# --- (C) GeoCalib 単体 ------------------------------------------------------------------------------

def exp_c(buildings=("honbu", "tower75"), log=print) -> dict:
    reg = R_.load_registry()
    idx = load_index(INDEX_PATH)
    rows = []
    t0 = time.time()
    for bid in buildings:
        ents = [e for e in reg.get("entries", []) if e["building"] == bid and (e.get("camera") or {}).get("R_cv")]
        for e in ents:
            rec = R_.photo_record(e["photo"], idx)
            if rec is None:
                continue
            cam = e["camera"]
            w, h = cam["image_size"]
            if [int(rec["width"]), int(rec["height"])] != [w, h]:
                continue
            ts = time.time()
            pr = LM.geocalib_prior(rec, bid)
            tg = time.time() - ts
            if pr is None:
                continue
            gc = json.loads((Path("out") / bid / "landmarks" / f"geocalib_{rec['id']}.json").read_text(encoding="utf-8"))
            yaw, pitch, roll = angles_of(np.array(cam["R_cv"]))
            try:
                tilt = EdgeDT(read_image(R_.photo_path(rec))).estimate_tilt(cam["focal_px"])
            except Exception:
                tilt = None
            rows.append({"building": bid, "photo": rec["id"], "truth_source": (e.get("source") or {}).get("kind") or "plane/pose",
                         "truth": {"pitch": round(pitch, 2), "roll": round(roll, 2), "focal": round(cam["focal_px"], 1)},
                         "exif_focal": round(LM.focal_px_from_exif(rec), 1),
                         "gc_raw": {"pitch": gc["raw_pitch_deg"], "roll": gc["raw_roll_deg"], "focal": gc["focal_px"]},
                         "gc_unc": gc.get("uncertainty"), "vp": tilt, "sec": round(tg, 2)})
    LM.release_geocalib()
    # 符号の約束：生の値と符号を反転した値のどちらが台帳に合うか（本部棟の真値で決める）
    hb = [r for r in rows if r["building"] == "honbu" and r["truth_source"] != "landmarks"]
    sign = {}
    for k in ("pitch", "roll"):
        e_pos = np.median([abs(r["gc_raw"][k] - r["truth"][k]) for r in hb]) if hb else 0
        e_neg = np.median([abs(-r["gc_raw"][k] - r["truth"][k]) for r in hb]) if hb else 0
        sign[k] = 1.0 if e_pos <= e_neg else -1.0
        log(f"GeoCalib {k}: 符号そのまま 中央値 {e_pos:.2f}°、反転 {e_neg:.2f}° → 符号 {sign[k]:+.0f}")

    def stats(sel):
        if not sel:
            return {}
        dp = [abs(sign["pitch"] * r["gc_raw"]["pitch"] - r["truth"]["pitch"]) for r in sel]
        dr = [abs(sign["roll"] * r["gc_raw"]["roll"] - r["truth"]["roll"]) for r in sel]
        df = [abs(r["gc_raw"]["focal"] / r["truth"]["focal"] - 1) for r in sel]
        dfe = [abs(r["gc_raw"]["focal"] / r["exif_focal"] - 1) for r in sel]
        dte = [abs(r["truth"]["focal"] / r["exif_focal"] - 1) for r in sel]
        vp = [r for r in sel if r["vp"]]
        out = {"n": len(sel), "gc_pitch_med_deg": round(float(np.median(dp)), 2), "gc_roll_med_deg": round(float(np.median(dr)), 2),
               "gc_pitch_max_deg": round(float(np.max(dp)), 2), "gc_roll_max_deg": round(float(np.max(dr)), 2),
               "gc_focal_vs_truth_med": round(float(np.median(df)), 3), "gc_focal_vs_exif_med": round(float(np.median(dfe)), 3),
               "truth_focal_vs_exif_med": round(float(np.median(dte)), 3),
               "sec_per_photo_med": round(float(np.median([r["sec"] for r in sel])), 2)}
        if vp:
            out.update({"vp_n": len(vp), "vp_pitch_med_deg": round(float(np.median([abs(r["vp"]["pitch"] - r["truth"]["pitch"]) for r in vp])), 2),
                        "vp_roll_med_deg": round(float(np.median([abs(r["vp"]["roll"] - r["truth"]["roll"]) for r in vp])), 2)})
        return out

    res = {"sign": sign, "honbu_truth": stats(hb),
           "tower75_landmarks": stats([r for r in rows if r["building"] == "tower75"]),
           "rows": rows, "sec": round(time.time() - t0, 1)}
    log(json.dumps({k: v for k, v in res.items() if k != "rows"}, ensure_ascii=False))
    return res


# --- (A) 真値あり ------------------------------------------------------------------------------

def exp_a(building: str = "honbu", log=print) -> dict:
    reg = R_.load_registry()
    idx = load_index(INDEX_PATH)
    bpath = Path("data/buildings") / f"{building}.json"
    b = load_building(bpath)
    ents = truth_entries(reg, building)
    conds = [(m, k, False) for k in (6, 4, 3) for m in METHODS] + [(m, 3, True) for m in ("poselib", "poselib+geocalib")]
    rows = []
    timing = {f"{m}|{k}{'+L' if L else ''}": 0.0 for m, k, L in conds}
    for e in ents:
        rec = R_.photo_record(e["photo"], idx)
        cam = e["camera"]
        w, h = cam["image_size"]
        if rec is None or [int(rec["width"]), int(rec["height"])] != [w, h]:
            continue
        C_true = -np.array(cam["R_cv"]).T @ np.array(cam["t_cv"])
        picks, lines = synth_picks(b, cam, w, h, 6)
        prior = LM.geocalib_prior(rec, building)
        row = {"photo": rec["id"], "face": e.get("face"), "n_visible_picks": len(picks), "n_lines": len(lines),
               "truth_focal_vs_exif": round(cam["focal_px"] / LM.focal_px_from_exif(rec), 3), "results": {}}
        for m, k, use_l in conds:
            key = f"{m}|{k}{'+L' if use_l else ''}"
            pk = [{kk: vv for kk, vv in p.items() if not kk.startswith("_")} for p in picks[:k]]
            if use_l:
                if len(lines) < 2:
                    row["results"][key] = None
                    continue
                pk = pk + lines
            if len(pk) < 3:
                row["results"][key] = None
                continue
            ts = time.time()
            # 位置の誤差：門を通さずに solve を直接（3 点は門の手前で落ちるため）
            P3 = np.array([next(c for c in LM.catalog(b) if c["id"] == p["id"])["xyz"] for p in pk if "cell" in p])
            uv = np.array([LM.pick_to_px(p, w, h)[:2] for p in pk if "cell" in p])
            lns = []
            if use_l:
                lcat = {c["id"]: c for c in LM.catalog(b, lines=True) if c.get("kind") == "line"}
                for p in lines:
                    x1, x2 = LM._line_pick_px(p, w, h)
                    lns.append({"X1": lcat[p["id"]]["xyz1"], "X2": lcat[p["id"]]["xyz2"], "x1": x1, "x2": x2})
            sol = LM.solve(P3, uv, w, h, LM.focal_px_from_exif(rec), method=m, lines=lns,
                           prior=prior if m == "poselib+geocalib" else None) if (m != "cv2" or len(P3) >= 4) else {}
            err = _pose_err(sol, C_true)
            # 合否の門（register_picks そのもの。台帳には書かない）
            res = LM.register_picks(bpath, {"photo": rec["id"], "picks": pk}, index=idx, write=False, method=m,
                                    prior=prior if m == "poselib+geocalib" else None, b=b)
            timing[key] += time.time() - ts
            row["results"][key] = {"err_m": None if err is None else round(err, 2), "solver": (sol or {}).get("method"),
                                   "rms_px": (sol or {}).get("rms_px"), "status": res["status"], "code": res.get("code"),
                                   "gate_err_m": (round(float(np.linalg.norm(np.array(res["camera"]["camera_xyz"]) - C_true)), 2)
                                                  if res.get("camera") else None)}
        rows.append(row)
        log(f"A {rec['id'][:30]:30s} " + " ".join(f"{k}:{(v or {}).get('err_m')}/{(v or {}).get('status', '-')[:2]}" for k, v in row["results"].items()))
    LM.release_geocalib()
    summ = {}
    for m, k, use_l in conds:
        key = f"{m}|{k}{'+L' if use_l else ''}"
        rs = [r["results"].get(key) for r in rows]
        s = _summ([(x or {}).get("err_m") for x in rs], len(rows))
        s["gate_ok"] = sum(1 for x in rs if x and x["status"] == "ok")
        s["gate_ok_within_2m"] = sum(1 for x in rs if x and x["status"] == "ok" and (x.get("gate_err_m") or 99) <= 2.0)
        s["sec_total"] = round(timing[key], 1)
        summ[key] = s
    log(json.dumps(summ, ensure_ascii=False, indent=1))
    return {"building": building, "n_photos": len(rows), "summary": summ, "rows": rows}


# --- (B) 本番 ------------------------------------------------------------------------------

def exp_b(building: str = "tower75", draw: bool = True, log=print) -> dict:
    idx = load_index(INDEX_PATH)
    bpath = Path("data/buildings") / f"{building}.json"
    b = load_building(bpath)
    d = Path("out") / building / "landmarks"
    rows = []
    timing = {m: 0.0 for m in METHODS}
    for pf in sorted(d.glob("picks_*.json")):
        doc = json.loads(pf.read_text(encoding="utf-8"))
        row = {"photo": doc["photo"], "face": doc.get("face"), "n_picks": len(doc.get("picks", [])), "results": {}}
        full = {}
        for m in METHODS:
            ts = time.time()
            res = LM.register_picks(bpath, doc, index=idx, write=False, method=m, b=b)
            timing[m] += time.time() - ts
            full[m] = res
            ck = res.get("check", {})
            row["results"][m] = {"status": res["status"], "code": res.get("code"), "reason": res.get("reason"),
                                 "solver": ck.get("solver"), "rms_px": ck.get("rms_px"), "n_inliers": ck.get("n_inliers"),
                                 "camera_xyz": (res.get("camera") or {}).get("camera_xyz"), "camera_height_m": ck.get("camera_height_m"),
                                 "camera_height_face_m": ck.get("camera_height_face_m"), "yaw": ck.get("yaw"), "pitch": ck.get("pitch"),
                                 "roll": ck.get("roll"), "tilt_from_vp": ck.get("tilt_from_vp"), "edge_inlier": ck.get("edge_inlier"),
                                 "edge_grad": ck.get("edge_grad"), "gravity_prior": ck.get("gravity_prior"),
                                 "gravity_diff_deg": ck.get("gravity_diff_deg"), "focal_vs_exif": ck.get("focal_vs_exif")}
        cv_ok = row["results"]["cv2"]["status"] == "ok"
        newly = [m for m in METHODS[1:] if row["results"][m]["status"] == "ok" and not cv_ok]
        lost = [m for m in METHODS[1:] if row["results"][m]["status"] != "ok" and cv_ok]
        row["newly_ok"], row["lost"] = newly, lost
        if draw:
            for m in newly:
                p = LM.draw_check(b, full[m], d / f"check_{doc['photo']}{'' if m == newly[-1] else '_' + m}.jpg")
                row.setdefault("check_jpg", []).append(str(p))
        rows.append(row)
        log(f"B {doc['photo'][:24]:24s} " + " ".join(f"{m}:{row['results'][m]['status']}/{row['results'][m]['code']}" for m in METHODS)
            + (f"  新たに合格 {newly}" if newly else "") + (f"  落ちた {lost}" if lost else ""))
    LM.release_geocalib()
    summ = {m: {"ok": sum(1 for r in rows if r["results"][m]["status"] == "ok"),
                "codes": _count(r["results"][m]["code"] for r in rows if r["results"][m]["status"] != "ok"),
                "sec_total": round(timing[m], 1)} for m in METHODS}
    summ["newly_ok"] = {m: [r["photo"] for r in rows if m in r["newly_ok"]] for m in METHODS[1:]}
    summ["lost"] = {m: [r["photo"] for r in rows if m in r["lost"]] for m in METHODS[1:]}
    log(json.dumps(summ, ensure_ascii=False, indent=1))
    return {"building": building, "n_picks_files": len(rows), "summary": summ, "rows": rows}


def _count(it) -> dict:
    out: dict = {}
    for x in it:
        out[str(x)] = out.get(str(x), 0) + 1
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="C,A,B")
    a = ap.parse_args(argv)
    doc = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    doc.update({"made_by": "recorder.landmarks_eval（実装係、Claude Opus 5.5）", "made_at": datetime.now().isoformat(timespec="seconds"),
                "libraries": {"poselib": "PoseLib 2.0.5（BSD-3-Clause）", "geocalib": "GeoCalib 1.0 pinhole weights（Apache-2.0、github.com/cvg/GeoCalib）",
                              "opencv": "OpenCV（Apache-2.0）"}})
    for part in [p.strip().upper() for p in a.only.split(",") if p.strip()]:
        t0 = time.time()
        r = {"A": exp_a, "B": exp_b, "C": exp_c}[part]()
        r["sec"] = round(time.time() - t0, 1)
        doc[part] = r
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
