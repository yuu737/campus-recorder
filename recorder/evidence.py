"""証拠ビューア：写真が模型のどこに立ち、どの目印が対応し、再投影がどれだけ合っているかを Rerun の記録（.rrd）に出す。

写真は模型に貼らない。写真はカメラの位置に小さく置く「資料」として記録する（規約：模型に写真は貼らない）。

  python -m recorder evidence data/buildings/honbu.json [--photos id,...] [--out out/honbu/evidence.rrd]

記録する物（座標は棟の世界座標：x 東・y 北・z は棟の ground_z からの高さ m。右手系・z 上）
  world/model                 模型（web/data/models/<棟>.glb。glTF の y 上 → z 上に回して置く）
  world/faces/<面>            面の縁と柱・窓・帯の線（compare.face_lines_detail から、薄い灰）
  world/evidence/<面>         面の外周を根拠の状態で色分け（measured 緑・estimated 黄・unknown 灰。面の中で一番多い物）
  world/landmarks/points|lines  目印の一覧（landmarks.catalog(lines=True)。面ごとに色）
  world/cams/<方法>/<写真>    視点（Transform3D）・写真（Pinhole＋縮めた Image）。方法で色分け
      …/image/picks           目印係が指した位置（2D）
      …/image/reproj          解いた視点で目印を写した位置（2D）と、指した位置との線（残差 px のラベル）
      …/image/model           模型の面の線を写真に写した物（重なりの見本）
  world/rays/<写真>           カメラから対応した目印への線（3D）
  evidence/summary            根拠の件数（TextDocument）。evidence/photo は時刻ごとの写真の説明
時間軸：「撮影日時」（索引の taken_at、JST）と「撮影順」。写真は撮った時刻から現れる。

依存：rerun-sdk（Apache-2.0／MIT）。rerun が入っていなくても、記録の中身を作る関数（plan_*）は動く（テスト用）。
"""

from __future__ import annotations

import argparse
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from . import registry as R_
from .photos import INDEX_PATH, load_index
from .schema import load_building

MODEL_DIR = Path("web/data/models")
LONG_SIDE = 640
JST = timezone(timedelta(hours=9))

# glTF（three.js と同じ y 上：x 東・y 上・z = −北）→ 世界（x 東・y 北・z 上）。world = M @ gltf
GLTF_TO_WORLD = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]])

METHOD_COLORS = {"landmarks": (240, 120, 40), "sfm": (60, 140, 235), "chain": (160, 90, 210), "rectify": (30, 170, 120)}
METHOD_TEXT = {"landmarks": "目印の対応から PnP", "sfm": "写真どうしの一括復元（SfM）", "chain": "登録済みの写真からの鎖",
               "rectify": "正面化と検収の原点から"}
STATUSES = ("measured", "estimated", "unknown")
STATUS_COLORS = {"measured": (50, 180, 80), "estimated": (235, 190, 40), "unknown": (150, 150, 150)}
STATUS_JA = {"measured": "実測", "estimated": "推定", "unknown": "不明"}
PICK_COLOR = (255, 60, 60)
REPROJ_COLOR = (80, 220, 255)
MODEL_LINE_COLOR = (120, 255, 120, 120)
FACE_PALETTE = [(230, 25, 75), (60, 180, 75), (255, 225, 25), (0, 130, 200), (245, 130, 48), (145, 30, 180),
                (70, 240, 240), (240, 50, 230), (210, 245, 60), (250, 190, 212), (0, 128, 128), (220, 190, 255),
                (170, 110, 40), (255, 250, 200), (128, 0, 0), (170, 255, 195), (128, 128, 0), (255, 215, 180)]


# --- 座標 ---------------------------------------------------------------------------------

def gltf_to_world(P) -> np.ndarray:
    """glTF の点（x 東・y 上・z = −北）→ 世界（x 東・y 北・z 上）。"""
    return np.asarray(P, float) @ GLTF_TO_WORLD.T


def camera_pose(cam: dict) -> tuple[np.ndarray, np.ndarray]:
    """台帳の camera（R_cv・t_cv。世界→カメラ、OpenCV の向き：x 右・y 下・z 前）→ (カメラの位置 C, カメラ→世界の回転)。"""
    R = np.asarray(cam["R_cv"], float)
    t = np.asarray(cam["t_cv"], float).reshape(3)
    return -R.T @ t, R.T


def method_of(e: dict) -> str:
    """登録の方法：landmarks（目印）／sfm（一括復元）／chain（鎖）／rectify（正面化と検収）。"""
    k = (e.get("source") or {}).get("kind")
    if k in METHOD_COLORS:
        return k
    if e.get("via"):
        return "chain"
    return "rectify" if R_.entry_kind(e) == "plane" else "chain"


def entity_name(photo_id: str) -> str:
    """エンティティ名に使う写真の短い名前（ASCII の英数字と _ だけ。P223_本部棟… → P223）。"""
    s = re.sub(r"[^A-Za-z0-9_]+", "", photo_id).strip("_")
    if s:
        return s
    # 英数字が 1 つも無い id（利用者が付けた日本語名など）は、全部 "photo" になって重なるので id の hash の頭を付ける（11/8 F3）
    from .photos import short_name
    return short_name(photo_id, ascii_only=True)


def face_color(face_id: str, faces: list[str]) -> tuple[int, int, int]:
    i = faces.index(face_id) if face_id in faces else abs(hash(face_id))
    return FACE_PALETTE[i % len(FACE_PALETTE)]


# --- 根拠の件数 ---------------------------------------------------------------------------

def _count(o, c: Counter) -> None:
    if isinstance(o, dict):
        if o.get("status") in STATUSES:
            c[o["status"]] += 1
        for v in o.values():
            _count(v, c)
    elif isinstance(o, list):
        for v in o:
            _count(v, c)


def status_counts(b: dict) -> dict:
    """棟 JSON の値の状態（measured／estimated／unknown）の件数。{"total", "faces": {面: Counter}, "other"}。
    other は面の外（棟全体の値・塊・部品）。撮影依頼（requests）の status（open 等）は数えない。"""
    total, other, faces = Counter(), Counter(), {}
    for k, v in b.items():
        if k in ("requests", "history"):
            continue
        if k == "faces":
            for f in v:
                c = Counter()
                _count(f, c)
                faces[f["id"]] = c
                total.update(c)
        else:
            c = Counter()
            _count(v, c)
            other.update(c)
            total.update(c)
    return {"total": total, "faces": faces, "other": other}


# --- 独立した観察の件数（11/6 追加）----------------------------------------------------------
# 値の件数は「模型をどれだけ細かく割ったか」で増える（帯を階ごと・柱型ごとに置けば、1 回見た帯の見付けが何百回も書かれる）。
# そこで、同じ (状態, 方法の文, 根拠の写真・資料, 値の種類) の値は「1 つの観察の複製」とみなして束ねる。
# 値の種類は JSON のパスから [番号] を取った物（faces[].ledges[].depth）。面をまたいだ同じ鍵も 1 つに束ねる。

EYEBALL = "目視"


def _hashable(x):
    if isinstance(x, (list, tuple)):
        return tuple(_hashable(v) for v in x)
    if isinstance(x, dict):
        return tuple(sorted((k, _hashable(v)) for k, v in x.items()))
    return x


def _walk_values(o, path: str, out: list, face: str | None, values_only: bool) -> None:
    """status 付きの値を (パス, 面, 値の dict) で集める。values_only は run.count_statuses と同じ数え方
    （status と value の両方を持つ dict だけ、その中には降りない）。既定は status_counts と同じ（status を持つ dict 全部）。"""
    if isinstance(o, dict):
        st = o.get("status")
        if values_only:
            if "status" in o and "value" in o:
                if st in STATUSES:
                    out.append((path, face, o))
                return
        elif st in STATUSES:
            out.append((path, face, o))
        for k, v in o.items():
            _walk_values(v, f"{path}.{k}" if path else k, out, face, values_only)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            _walk_values(v, f"{path}[{i}]", out, face, values_only)


def value_kind(path: str) -> str:
    """値の種類：パスから [番号] を取った物。faces[3].ledges[12].depth → faces[].ledges[].depth"""
    return re.sub(r"\[\d+\]", "[]", path)


def observation_key(path: str, v: dict) -> tuple:
    """観察の鍵 (状態, 方法の文, 根拠のタプル, 値の種類)。"""
    ev = v.get("evidence") or []
    if not isinstance(ev, (list, tuple)):
        ev = [ev]
    return (v["status"], (v.get("method") or "").strip(), _hashable(list(ev)), value_kind(path))


def _tally(items: list) -> dict:
    keys = Counter(observation_key(p, v) for p, _, v in items)
    by = {s: {"values": 0, "observations": 0} for s in STATUSES}
    for (st, *_), n in keys.items():
        by[st]["values"] += n
        by[st]["observations"] += 1
    eye = [k for k in keys if k[1] == EYEBALL]
    return {"values": len(items), "observations": len(keys), "by_status": by,
            "derived": len(items) - len(keys),
            "eyeball": {"values": sum(keys[k] for k in eye), "observations": len(eye)}, "_keys": keys}


def observations(b: dict, values_only: bool = False, top: int = 10) -> dict:
    """値の個数と、独立した観察の個数を分けて数える。

    返す物：values（値の総数＝今までの数え方）、observations（観察の鍵の数）、by_status（状態ごとの値と観察）、
    derived（複製で増えた数＝values − observations）、eyeball（方法が「目視」だけの値と観察）、
    top_copies（複製の多い鍵の上位。{status, method, evidence, kind, copies}）、
    faces（面ごとの values・observations・by_status。面の中で束ねる）、other（面の外）。
    values_only=False は status_counts と同じ範囲（requests・history を除く status 付きの dict 全部）、
    True は run.count_statuses と同じ範囲（requests を除き、status と value を両方持つ値だけ）。"""
    skip = ("requests",) if values_only else ("requests", "history")
    items: list = []
    face_items: dict[str, list] = {}
    other: list = []
    for k, v in b.items():
        if k in skip:
            continue
        if k == "faces" and isinstance(v, list):
            for i, f in enumerate(v):
                got: list = []
                _walk_values(f, f"faces[{i}]", got, f.get("id") if isinstance(f, dict) else None, values_only)
                face_items[f.get("id", f"faces[{i}]") if isinstance(f, dict) else f"faces[{i}]"] = got
                items += got
        else:
            got = []
            _walk_values(v, k, got, None, values_only)
            other += got
            items += got
    res = _tally(items)
    keys = res.pop("_keys")
    res["top_copies"] = [{"status": k[0], "method": k[1], "evidence": list(k[2]), "kind": k[3], "copies": n}
                         for k, n in sorted(keys.items(), key=lambda kv: (-kv[1], kv[0][3], kv[0][1]))[:top]]
    res["faces"] = {}
    for fid, got in face_items.items():
        t = _tally(got)
        t.pop("_keys")
        res["faces"][fid] = t
    t = _tally(other)
    t.pop("_keys")
    res["other"] = t
    return res


def observations_line(o: dict) -> str:
    """1 行の表示：値 measured N / estimated N / unknown N　｜　独立した観察 measured N / … （複製 N）"""
    by = o["by_status"]
    v = " / ".join(f"{s} {by[s]['values']}" for s in STATUSES)
    ob = " / ".join(f"{s} {by[s]['observations']}" for s in STATUSES)
    return f"値 {v}　｜　独立した観察 {ob}（複製 {o['derived']}）"


OBS_NOTE = "件数は部品ごとに数えるので、同じ観察が複製される。観察の列が実際に見た回数（同じ状態・方法・根拠・値の種類を 1 つと数える）。"


def dominant_status(c: Counter) -> str:
    """面の色：一番多い状態（同数は measured > estimated > unknown の順）。何も無ければ unknown。"""
    if not c:
        return "unknown"
    return max(STATUSES, key=lambda s: (c.get(s, 0), -STATUSES.index(s)))


# --- 3D の線 ------------------------------------------------------------------------------

def face_segments(b: dict, f: dict) -> tuple[list[np.ndarray], np.ndarray | None]:
    """面の線（柱・窓・帯の縦横の線）の 3D の線分と、面の外周（閉じた 5 点）。面の平面上の線だけ。"""
    from .compare import face_lines_detail

    if not (f.get("from") and f.get("to")):
        return [], None
    fr = R_.face_rect_frame(b, f)
    g, top, L = fr["ground_offset"] or 0.0, fr["top_z"], fr["length_m"]
    if top is None:
        return [], None
    outline = R_.plane_to_world(fr, [0, L, L, 0, 0], [g, g, top, top, g])
    segs = []
    try:
        xs, zs, _ = face_lines_detail(b, f["id"])
    except Exception:          # 線が出せない面（値が unknown など）は外周だけ
        xs, zs = [], []
    for l in xs:
        if l.in_plane and -0.01 <= l.m <= L + 0.01:
            segs.append(R_.plane_to_world(fr, [l.m, l.m], [g, top]))
    for l in zs:
        if l.in_plane and g - 0.01 <= l.m <= top + 0.01:
            segs.append(R_.plane_to_world(fr, [0.0, L], [l.m, l.m]))
    return segs, outline


def project_polyline(cam: dict, P, n: int = 24, min_depth: float = 0.5, margin: float = 0.5) -> list[np.ndarray]:
    """3D の折れ線を写真へ写す。辺を n 分割し、カメラの後ろ（奥行き < min_depth）と枠の大きく外は切って、続きごとの 2D 折れ線に。"""
    P = np.asarray(P, float).reshape(-1, 3)
    if len(P) < 2:
        return []
    pts = [P[0]]
    for a, c in zip(P[:-1], P[1:]):
        for k in range(1, n + 1):
            pts.append(a + (c - a) * k / n)
    u, v, d = R_.project_world(cam, np.array(pts))
    w, h = cam["image_size"]
    ok = (d > min_depth) & (u > -margin * w) & (u < (1 + margin) * w) & (v > -margin * h) & (v < (1 + margin) * h)
    runs, cur = [], []
    for i in range(len(pts)):
        if ok[i]:
            cur.append((u[i], v[i]))
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return [np.array(r) for r in runs if len(r) >= 2]


def _facing(f: dict, C: np.ndarray) -> bool:
    if not (f.get("from") and f.get("to")):
        return False
    p0, _, nv, _ = R_.face_frame({"from": f["from"], "to": f["to"]})
    return float(np.dot(C[:2] - p0, nv)) > 0


# --- 写真ごとの中身 ------------------------------------------------------------------------

def taken_at(rec: dict | None) -> datetime | None:
    s = (rec or {}).get("taken_at")
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=JST)


def match_mark(pick: dict, marks: dict) -> dict | None:
    """対応の目印を一覧から探す。目印の番号は一覧が増えると振り直される（本部棟の 11/6 の登録は窓の段を足す前の番号）ので、
    同じ面の同じ名前（「柱型1左 × 地面」など）を優先し、名前が無い時だけ番号で引く。"""
    pid = pick.get("id") or ""
    name = pick.get("name")
    if name:
        face = re.sub(r"-[VH]?\d+$", "", pid)
        for m in marks.values():
            if m["face"] == face and m["name"] == name and (m.get("kind") == "line") == bool(re.search(r"-[VH]\d+$", pid)):
                return m
        return None
    return marks.get(pid)


def plan_photo(e: dict, b: dict, marks: dict, rec: dict | None = None, long_side: int = LONG_SIDE) -> dict:
    """1 枚の登録から、記録する数字（画像は読まない）。2D の座標はすべて縮めた写真の px。"""
    from .landmarks import _line_pick_px

    cam = e["camera"]
    w, h = cam["image_size"]
    sc = long_side / max(w, h)
    C, Rwc = camera_pose(cam)
    method = method_of(e)
    picks = []
    for p in (e.get("source") or {}).get("picks") or []:
        m = match_mark(p, marks)
        if m is None:
            continue
        if m.get("kind") == "line":
            try:
                uv = np.array(_line_pick_px(p, w, h), float) if p.get("cells") else None
            except ValueError:
                uv = None
            proj = project_polyline(cam, [m["xyz1"], m["xyz2"]], n=8)
            picks.append({"id": p["id"], "line": True, "uv": None if uv is None else uv * sc,
                          "proj": [q * sc for q in proj], "resid": p.get("reproj_px"), "inlier": bool(p.get("inlier", True)),
                          "xyz": [m["xyz1"], m["xyz2"]]})
            continue
        if p.get("u") is None:
            continue
        u, v, d = R_.project_world(cam, [m["xyz"]])
        uv = np.array([p["u"], p["v"]], float)
        pr = np.array([u[0], v[0]], float) if d[0] > 0 else None
        # 残差は今の棟 JSON の目印で測り直す（登録の後に JSON を直していれば台帳の reproj_px と違う。両方残す）
        resid = float(np.hypot(*(uv - pr))) if pr is not None else p.get("reproj_px")
        picks.append({"id": p["id"], "line": False, "uv": uv * sc, "proj": None if pr is None else pr * sc,
                      "resid": resid, "resid_registry": p.get("reproj_px"), "inlier": bool(p.get("inlier", True)),
                      "xyz": m["xyz"], "mark": m["id"]})
    # 写真に写す模型の線：見えている面（台帳の visible_faces・face）か、カメラに向いた面
    vis = {v["face"] for v in e.get("visible_faces") or []}
    if e.get("face"):
        vis.add(e["face"])
    overlay = []
    for f in b.get("faces", []):
        if (f["id"] in vis) if vis else _facing(f, C):
            segs, outline = face_segments(b, f)
            for P in ([outline] if outline is not None else []) + segs:
                overlay += [q * sc for q in project_polyline(cam, P, margin=0.02)]
    chk = e.get("check") or {}
    rms = chk.get("rms_px", chk.get("reproj_rms_px"))
    return {"photo": e["photo"], "name": entity_name(e["photo"]), "method": method, "color": METHOD_COLORS[method],
            "face": e.get("face"), "period": e.get("period") or (rec or {}).get("period"),
            "C": C, "R_wc": Rwc, "focal": float(cam["focal_px"]) * sc, "size": (round(w * sc), round(h * sc)),
            "scale": sc, "orig_size": (int(w), int(h)), "picks": picks, "overlay": overlay, "rms_px": rms,
            "n_points": chk.get("n_points"), "n_inliers": chk.get("n_inliers"),
            "time": taken_at(rec), "file": (rec or {}).get("file") or e.get("file")}


def plan(b: dict, entries: list[dict], index: dict | None = None, long_side: int = LONG_SIDE) -> dict:
    """棟 1 つの記録の中身（画像以外）。"""
    from .landmarks import catalog

    cat = catalog(b, lines=True)
    marks = {m["id"]: m for m in cat}
    idx = {r["id"]: r for r in (index or {}).get("photos", [])}
    photos = [plan_photo(e, b, marks, idx.get(e["photo"]), long_side) for e in entries if e.get("camera")]
    photos.sort(key=lambda p: (p["time"] is None, p["time"] or datetime.min.replace(tzinfo=JST), p["photo"]))
    faces = [f["id"] for f in b.get("faces", [])]
    face_geo = {}
    for f in b.get("faces", []):
        segs, outline = face_segments(b, f)
        if outline is not None:
            face_geo[f["id"]] = {"segs": segs, "outline": outline}
    return {"building": b["id"], "name": b.get("name", b["id"]), "faces": faces, "face_geo": face_geo,
            "points": [m for m in cat if m.get("kind") != "line"], "lines": [m for m in cat if m.get("kind") == "line"],
            "counts": status_counts(b), "observations": observations(b), "photos": photos}


def pick_best(photos: list[dict]) -> dict | None:
    """2D の画面に出す写真：目印の対応のある登録から選ぶ。無ければ最初の写真。"""
    if not photos:
        return None
    lm = [p for p in photos if p["picks"]]
    if lm:
        # 使われた対応が 6 点以上の中で再投影の一番小さい物（無ければ対応の多い物）
        return max(lm, key=lambda p: (sum(q["inlier"] for q in p["picks"]) >= 6, -(p["rms_px"] or 1e9)))
    return photos[0]


def summary_text(pl: dict) -> str:
    c = pl["counts"]
    t = c["total"]
    by = Counter(p["method"] for p in pl["photos"])
    picks = [q for p in pl["photos"] for q in p["picks"]]
    res = [q["resid"] for q in picks if q["inlier"] and q["resid"] is not None]
    ob = pl.get("observations")

    def _o(d):               # 観察の数（plan に observations が無い古い呼び方では —）
        return d["observations"] if d else "—"

    lines = [f"# {pl['name']}（{pl['building']}）の根拠", "",
             f"**値の状態**　実測 measured **{t.get('measured', 0)}**　・　推定 estimated **{t.get('estimated', 0)}**"
             f"　・　不明 unknown **{t.get('unknown', 0)}**", ""]
    if ob:
        obs = ob["by_status"]
        lines += [f"**独立した観察**　実測 **{obs['measured']['observations']}**　・　推定 **{obs['estimated']['observations']}**"
                  f"　・　不明 **{obs['unknown']['observations']}**（複製 {ob['derived']}、方法が「目視」だけの値 "
                  f"{ob['eyeball']['values']}・観察 {ob['eyeball']['observations']}）", "", OBS_NOTE, ""]
    lines += [f"**登録済みの写真 {len(pl['photos'])} 枚**　" + "・".join(f"{METHOD_TEXT[k]} {v}" for k, v in by.most_common()), "",
              f"**目印の対応 {len(picks)} 点**（使われた {len(res)} 点、再投影の中央値 "
              f"{(f'{float(np.median(res)):.1f} px' if res else '—')}）", "",
              "| 面 | 実測 | 推定 | 不明 | 観察 | 色 |", "|---|---:|---:|---:|---:|---|"]
    for fid in pl["faces"]:
        fc = c["faces"].get(fid, Counter())
        fo = (ob or {}).get("faces", {}).get(fid)
        lines.append(f"| {fid} | {fc.get('measured', 0)} | {fc.get('estimated', 0)} | {fc.get('unknown', 0)} | "
                     f"{_o(fo)} | {STATUS_JA[dominant_status(fc)]} |")
    o = c["other"]
    lines.append(f"| 棟全体・塊・部品 | {o.get('measured', 0)} | {o.get('estimated', 0)} | {o.get('unknown', 0)} | "
                 f"{_o((ob or {}).get('other'))} | |")
    lines += ["", "色：面の外周＝その面で一番多い状態（緑 実測・黄 推定・灰 不明）。カメラ＝登録の方法（橙 目印・青 SfM・紫 鎖・緑 正面化）。",
              "写真の中：赤＝目印係が指した位置、水色＝解いた視点で目印を写した位置、線とラベル＝残差 px、薄緑＝模型の線を写した物。",
              "", "座標：x 東・y 北・z は棟の ground_z からの高さ m（JGD2011 VII 系の原点 北緯35.1355° 東経136.9755° からの m）。"]
    return "\n".join(lines)


def photo_text(p: dict) -> str:
    res = [q["resid"] for q in p["picks"] if q["inlier"] and q["resid"] is not None]
    t = p["time"].strftime("%Y-%m-%d %H:%M:%S") if p["time"] else "不明"
    s = [f"## {p['photo']}", "", f"- 撮影：{t}（{'期間前' if p['period'] == 'before' else '期間中'}）",
         f"- 登録の方法：{METHOD_TEXT[p['method']]}（{p['method']}）",
         f"- カメラの位置：x {p['C'][0]:.1f}・y {p['C'][1]:.1f}・高さ {p['C'][2]:.1f} m"]
    if p["picks"]:
        s.append(f"- 目印の対応 {len(p['picks'])} 点（使われた {len(res)}）、残差の中央値 {float(np.median(res)):.1f} px" if res
                 else f"- 目印の対応 {len(p['picks'])} 点")
    if p["rms_px"] is not None:
        s.append(f"- 再投影の RMS {p['rms_px']:.1f} px（元の写真の px）")
    return "\n".join(s)


# --- 記録（rerun）-------------------------------------------------------------------------

def load_small(path: Path, long_side: int = LONG_SIDE) -> np.ndarray | None:
    from PIL import Image, ImageOps

    if not Path(path).exists():
        return None
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    k = long_side / max(im.size)
    im = im.resize((max(1, round(im.size[0] * k)), max(1, round(im.size[1] * k))), Image.LANCZOS)
    return np.asarray(im)


def scene_center(pl: dict) -> tuple[np.ndarray, float]:
    pts = [g["outline"] for g in pl["face_geo"].values()]
    if not pts:
        return np.zeros(3), 30.0
    P = np.vstack(pts)
    lo, hi = P.min(0), P.max(0)
    return (lo + hi) / 2, float(np.linalg.norm(hi - lo))


def eye_for(pl: dict, kind: str = "main") -> tuple[np.ndarray, np.ndarray]:
    """3D の画面の視点（位置, 見る先）。カメラが多い側の斜め上から。"""
    c, diag = scene_center(pl)
    Cs = np.array([p["C"] for p in pl["photos"]]) if pl["photos"] else None
    d = (Cs.mean(0) - c)[:2] if Cs is not None else np.array([-1.0, -1.0])
    if np.linalg.norm(d) < 1e-6:
        d = np.array([-1.0, -1.0])
    d = d / np.linalg.norm(d)
    if kind == "side":                     # 2 枚目：90° 回した側から、やや低く
        d = np.array([-d[1], d[0]])
        pos = c + np.array([d[0], d[1], 0.0]) * diag * 0.95 + np.array([0, 0, diag * 0.30])
    else:
        pos = c + np.array([d[0], d[1], 0.0]) * diag * 0.85 + np.array([0, 0, diag * 0.55])
    return pos, c


UNSUPPORTED_GLTF_EXT = ("KHR_texture_transform",)   # Rerun 0.42 の glTF 読み込みが対応していない拡張


def strip_gltf_extensions(glb: bytes, drop: tuple[str, ...] = UNSUPPORTED_GLTF_EXT) -> bytes:
    """glb の JSON から Rerun が読めない拡張を外す（模型の形はそのまま。質感の繰り返しの倍率だけが Web と違う）。"""
    import json
    import struct

    magic, ver, _ = struct.unpack("<4sII", glb[:12])
    if magic != b"glTF":
        raise ValueError("glb ではありません")
    jlen, jtype = struct.unpack("<II", glb[12:20])
    doc = json.loads(glb[20:20 + jlen])
    rest = glb[20 + jlen:]

    def walk(o):
        if isinstance(o, dict):
            ext = o.get("extensions")
            if isinstance(ext, dict):
                for k in drop:
                    ext.pop(k, None)
                if not ext:
                    o.pop("extensions")
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(doc)
    for key in ("extensionsUsed", "extensionsRequired"):
        if key in doc:
            doc[key] = [e for e in doc[key] if e not in drop]
            if not doc[key]:
                doc.pop(key)
    js = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    js += b" " * (-len(js) % 4)
    body = struct.pack("<II", len(js), jtype) + js + rest
    return struct.pack("<4sII", b"glTF", ver, 12 + len(body)) + body


def blueprint(pl: dict, layout: str = "three"):
    import rerun.blueprint as rrb

    best = pick_best(pl["photos"])
    pos, tgt = eye_for(pl, "side" if layout == "overview" else "main")
    v3 = rrb.Spatial3DView(origin="/world", name="模型と写真の視点",
                           eye_controls=rrb.EyeControls3D(position=pos, look_target=tgt, eye_up=[0, 0, 1]),
                           background=[24, 28, 34], line_grid=rrb.LineGrid3D(visible=False))
    txt = rrb.TextDocumentView(origin="/evidence/summary", name="根拠の件数")
    # 時刻の印は最後（全部の写真が出た状態）から。再生すると撮った順に写真が増える
    tp = rrb.TimePanel(timeline="撮影日時", state="collapsed", play_state="following")
    if best is None or layout == "overview":
        return rrb.Blueprint(rrb.Horizontal(v3, txt, column_shares=[3, 1]), tp, collapse_panels=True)
    cam_img = f"/world/cams/{best['method']}/{best['name']}/image"
    w, h = best["size"]
    v2 = rrb.Spatial2DView(origin=cam_img, name=f"写真 {best['photo']}：目印と再投影",
                           visual_bounds=rrb.VisualBounds2D(x_range=[0, w], y_range=[0, h]))
    if layout == "photo":
        return rrb.Blueprint(rrb.Horizontal(v2, txt, column_shares=[3, 1]), tp, collapse_panels=True)
    return rrb.Blueprint(rrb.Horizontal(v3, rrb.Vertical(v2, txt, row_shares=[3, 2]), column_shares=[3, 2]),
                         rrb.TimePanel(timeline="撮影日時", state="expanded", play_state="following"), collapse_panels=True)


def record(pl: dict, out_rrd: Path, model: Path | None, images: bool = True, long_side: int = LONG_SIDE) -> dict:
    """plan の中身を Rerun に記録して .rrd に保存する。返り値は記録した数。"""
    import rerun as rr

    app = f"campus_recorder_evidence_{pl['building']}"
    rec = rr.RecordingStream(app, recording_id=f"evidence-{pl['building']}")
    out_rrd.parent.mkdir(parents=True, exist_ok=True)
    rec.save(str(out_rrd), default_blueprint=blueprint(pl, "three"))
    log = rec.log
    n = Counter()
    _, diag = scene_center(pl)
    plane_d = max(2.5, 0.05 * diag)

    log("world", rr.ViewCoordinates.RIGHT_HAND_Z_UP, static=True)
    if model is not None and model.exists():
        log("world/model", rr.Asset3D(contents=strip_gltf_extensions(model.read_bytes()), media_type="model/gltf-binary"),
            static=True)
        log("world/model", rr.Transform3D(mat3x3=GLTF_TO_WORLD), static=True)
        n["model"] = 1
    for fid, g in pl["face_geo"].items():
        if g["segs"]:
            log(f"world/faces/{entity_name(fid)}", rr.LineStrips3D([s for s in g["segs"]], colors=[(200, 205, 210, 90)],
                                                                   radii=0.03), static=True)
        st = dominant_status(pl["counts"]["faces"].get(fid, Counter()))
        fc = pl["counts"]["faces"].get(fid, Counter())
        lab = f"{fid}：実{fc.get('measured', 0)}・推{fc.get('estimated', 0)}・不{fc.get('unknown', 0)}"
        log(f"world/evidence/{entity_name(fid)}", rr.LineStrips3D([g["outline"]], colors=[STATUS_COLORS[st]], radii=0.12,
                                                                  labels=[lab], show_labels=False), static=True)
        n["face_lines"] += len(g["segs"]) + 1
    if pl["points"]:
        log("world/landmarks/points", rr.Points3D([m["xyz"] for m in pl["points"]], radii=0.18,
                                                  colors=[face_color(m["face"], pl["faces"]) for m in pl["points"]],
                                                  labels=[m["id"] for m in pl["points"]], show_labels=False), static=True)
        n["landmarks"] = len(pl["points"])
    if pl["lines"]:
        log("world/landmarks/lines", rr.LineStrips3D([[m["xyz1"], m["xyz2"]] for m in pl["lines"]], radii=0.04,
                                                     colors=[face_color(m["face"], pl["faces"]) + (110,) for m in pl["lines"]],
                                                     labels=[m["id"] for m in pl["lines"]], show_labels=False), static=True)
        n["landmark_lines"] = len(pl["lines"])
    log("evidence/summary", rr.TextDocument(summary_text(pl), media_type=rr.MediaType.MARKDOWN), static=True)

    for i, p in enumerate(pl["photos"]):
        rec.set_time("撮影順", sequence=i + 1)
        if p["time"] is not None:
            rec.set_time("撮影日時", timestamp=p["time"])
        base = f"world/cams/{p['method']}/{p['name']}"
        log(base, rr.Transform3D(translation=p["C"], mat3x3=p["R_wc"]))
        w, h = p["size"]
        log(f"{base}/image", rr.Pinhole(focal_length=p["focal"], width=w, height=h, principal_point=[w / 2, h / 2],
                                        camera_xyz=rr.ViewCoordinates.RDF, image_plane_distance=plane_d,
                                        color=p["color"], line_width=0.05))
        if images:
            img = load_small(R_.photo_path({"file": p["file"]}), long_side) if p["file"] else None
            if img is not None and abs(img.shape[1] / img.shape[0] - w / h) < 0.02:
                log(f"{base}/image", rr.Image(img).compress(jpeg_quality=80))
                n["images"] += 1
            else:
                n["images_missing"] += 1
        if p["overlay"]:
            log(f"{base}/image/model", rr.LineStrips2D(p["overlay"], colors=[MODEL_LINE_COLOR], radii=0.4, draw_order=10))
        pts = [q for q in p["picks"] if not q["line"]]
        if pts:
            log(f"{base}/image/picks", rr.Points2D([q["uv"] for q in pts], radii=3.5, colors=[PICK_COLOR],
                                                   labels=[q["id"] for q in pts], show_labels=False, draw_order=30))
            pr = [q for q in pts if q["proj"] is not None]
            if pr:
                log(f"{base}/image/reproj", rr.Points2D([q["proj"] for q in pr], radii=2.5, colors=[REPROJ_COLOR],
                                                        draw_order=31))
                log(f"{base}/image/residual",
                    rr.LineStrips2D([[q["uv"], q["proj"]] for q in pr], radii=1.0, draw_order=29,
                                    colors=[(255, 230, 80) if q["inlier"] else (255, 80, 200) for q in pr],
                                    labels=[f"{q['id']} {q['resid']:.0f}px" + ("" if q["inlier"] else "（外れ）")
                                            if q["resid"] is not None else q["id"] for q in pr]))
            n["picks"] += len(pts)
            inl = [q for q in pts if q["inlier"]]
            if inl:
                log(f"world/rays/{p['name']}", rr.LineStrips3D([[p["C"], q["xyz"]] for q in inl], radii=0.02,
                                                               colors=[p["color"] + (80,)]))
        lns = [q for q in p["picks"] if q["line"]]
        if lns:
            if any(q["uv"] is not None for q in lns):
                log(f"{base}/image/picks_lines", rr.LineStrips2D([q["uv"] for q in lns if q["uv"] is not None],
                                                                 colors=[PICK_COLOR], radii=1.5, draw_order=30))
            proj = [s for q in lns for s in q["proj"]]
            if proj:
                log(f"{base}/image/reproj_lines", rr.LineStrips2D(proj, colors=[REPROJ_COLOR], radii=1.2, draw_order=31))
            n["line_picks"] += len(lns)
        log("evidence/photo", rr.TextDocument(photo_text(p), media_type=rr.MediaType.MARKDOWN))
        n["photos"] += 1
    rec.flush()
    return dict(n)


def save_blueprints(pl: dict, out_dir: Path) -> dict[str, Path]:
    """スクリーンショット・動画用の画面（.rbl）を 3 つ：three（3 枚組・既定）／overview（3D 全体）／photo（写真の 2D）。"""
    out = {}
    for name in ("three", "overview", "photo"):
        p = out_dir / f"evidence_{name}.rbl"
        blueprint(pl, name).save(f"campus_recorder_evidence_{pl['building']}", str(p))
        out[name] = p
    return out


def run(building_json: Path, photos: list[str] | None = None, out: Path | None = None, images: bool = True,
        long_side: int = LONG_SIDE) -> dict:
    b = load_building(building_json)
    reg = R_.load_registry()
    ents = R_.entries_for(reg, b["id"], photos)
    idx = load_index(INDEX_PATH) if INDEX_PATH.exists() else {"photos": []}
    pl = plan(b, ents, idx, long_side)
    out = out or Path("out") / b["id"] / "evidence.rrd"
    model = MODEL_DIR / f"{b['id']}.glb"
    n = record(pl, out, model if model.exists() else None, images=images, long_side=long_side)
    bps = save_blueprints(pl, out.parent)
    best = pick_best(pl["photos"])
    return {"rrd": str(out), "blueprints": {k: str(v) for k, v in bps.items()}, "counts": n,
            "best_photo": best["photo"] if best else None,
            "methods": dict(Counter(p["method"] for p in pl["photos"]))}


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("file", help="棟 JSON（data/buildings/<棟>.json）")
    p.add_argument("--photos", default=None, help="写真 id をカンマ区切りで（既定は登録済み全部）")
    p.add_argument("--out", default=None, help="出力 .rrd（既定 out/<棟>/evidence.rrd）")
    p.add_argument("--no-images", action="store_true", help="写真の画像を入れない（視点と目印だけ）")
    p.add_argument("--long-side", type=int, default=LONG_SIDE, help="写真を縮める長辺 px")


def run_cli(a: argparse.Namespace) -> int:
    photos = [s for s in a.photos.split(",") if s] if a.photos else None
    res = run(Path(a.file), photos, Path(a.out) if a.out else None, images=not a.no_images, long_side=a.long_side)
    c = res["counts"]
    print(f"{res['rrd']}：写真 {c.get('photos', 0)} 枚（画像 {c.get('images', 0)}）、方法 {res['methods']}、"
          f"目印 {c.get('landmarks', 0)} 点・線 {c.get('landmark_lines', 0)} 本、対応 {c.get('picks', 0)} 点、面の線 {c.get('face_lines', 0)}")
    print(f"画面：{', '.join(res['blueprints'].values())}（2D は {res['best_photo']}）")
    print(f"開く：.venv/Scripts/rerun {res['rrd']}  （画面を替える：rerun {res['rrd']} {res['blueprints']['overview']}）")
    return 0
