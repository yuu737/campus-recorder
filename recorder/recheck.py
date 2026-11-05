"""自動の再検収：登録済みの写真（data/photos/registry.json）を全部、今の棟 JSON に重ね直してずれを一覧にする。

(a) 線のずれ    棟 JSON の面の線（compare と同じ線の集合：柱型の縁・窓・帯・段・開口・パラペット・塊と箱の縁）と、
                新しい部品の縁（parts_geom の素片：外階段・柱・段・玄関・室外機… と、窓下パネル・帯の上の手すり）を、
                登録済みの H_plane（面の上の線）か視点（面の外に出た線）で写真に投影する。線の上の点ごとに、線に直角な向き
                （面の上で横の線なら高さ、縦の線なら面に沿う向き）に ±SEARCH_M m ずらしながら写真の縁の強さ（勾配）を読み、
                全部の点で足し合わせた山のうち 0 に一番近い強い山を「写真の縁」とする。ずれは m で直接出る（写真 − JSON）。
                原点は登録済みなので、compare の「横の原点が柱の何本目か」の問題は起きない。
(b) 重ね画像    model build --cameras で写真の視点の描画を作り、写真に重ねる（--no-render で省略）。

出力 out/<棟>/recheck/<日時>/summary.json・summary.md・lines_<写真>_<面>.png・overlay_<写真>.png
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np

from . import parts_geom
from .compare import face_lines_detail
from .registry import (apply_H, entries_for, expand_entries, face_frame, face_top, load_registry, photo_path, plane_to_world,
                       project_world, world_to_plane)
from .schema import Measurement, bay_edges, face_ground_offset, load_building

TOL_M = 0.15
SEARCH_M = 0.5          # 線から ±この範囲で縁を探す（0.5 m 以上のずれは対応の取り違えを疑う：検収係の目安）
STEP_M = 0.01
PEAK_REL = 0.6          # 足し合わせた縁の強さの最大のこの割合以上の山を「強い山」とする
MIN_CONTRAST = 1.3      # 山の高さ ÷ 中央値 がこれ未満なら「見つからず」
MIN_SAMPLES = 3
STRONG_CONTRAST = 2.0   # これ未満の縁は「弱い」：一覧に出すが合否と最大のずれには数えない


def _v(m):
    if isinstance(m, (int, float)) and not isinstance(m, bool):
        return float(m)
    mm = Measurement.from_json(m) if isinstance(m, dict) else None
    return float(mm.value) if mm is not None and mm.known else None


def _span_pts(a0: float, a1: float, step: float = 0.25, nmax: int = 60, inset: float = 0.1) -> np.ndarray:
    a0, a1 = min(a0, a1) + inset, max(a0, a1) - inset
    if a1 <= a0:
        return np.array([(a0 + a1) / 2])
    n = int(min(nmax, max(3, (a1 - a0) / step + 1)))
    return np.linspace(a0, a1, n)


def _line(what, group, kind, d, pos, n, ranges, src=""):
    """d: "s"（縦の線。面に沿う向きにずれを測る）か "z"（横の線。高さのずれ）。ranges: もう一方の座標の区間の並び。"""
    pts = []
    for a0, a1 in ranges:
        for t in _span_pts(a0, a1, nmax=max(8, 60 // max(1, len(ranges)))):
            pts.append((pos, n, t) if d == "s" else (t, n, pos))
    return {"what": what, "group": group, "kind": kind, "dir": d, "json_m": round(float(pos), 3), "depth_m": round(float(n), 3),
            "samples": pts, "src": src}


def window_spans(f: dict, L: float) -> list[tuple[float, float]]:
    """窓のある区間（柱型と柱型の間）。柱割りが無ければ面の全長。"""
    bays = f.get("bays") or {}
    edges, _ = bay_edges(bays, L)
    pier = _v(bays.get("pier_width")) or 0.0
    if len(edges) >= 2:
        return [(edges[i] + pier / 2, edges[i + 1] - pier / 2) for i in range(len(edges) - 1)]
    return [(0.0, L)]


def face_check_lines(b: dict, face_id: str) -> list[dict]:
    """面の線（compare と同じ集合）＋新しい部品の縁。座標は今の棟 JSON の面の (s, n, z)。"""
    faces = {f["id"]: f for f in b.get("faces", [])}
    f = faces[face_id]
    _, _, _, Lg = face_frame(f)
    L = _v(f.get("length")) or Lg
    g = face_ground_offset(f)
    top = face_top(b, f)
    bays = f.get("bays") or {}
    xs, zs, _info = face_lines_detail(b, face_id)
    wspans = window_spans(f, L)
    edges, _ = bay_edges(bays, L)
    pier = _v(bays.get("pier_width")) or 0.0

    def bay_span(i: int):
        if 1 <= i <= len(edges) - 1:
            return (edges[i - 1] + pier / 2, edges[i] - pier / 2)
        return None

    zinfo: dict[str, tuple[list, float]] = {}       # 横の線の名前 → (区間, 出)
    for w in f.get("windows") or []:
        for nm in ("下端", "上端"):
            zinfo[f"{w.get('name', '窓')} {nm}"] = (wspans, 0.0)
    for bd in f.get("bands") or []:
        dep = _v(bd.get("depth")) or 0.0
        a0, a1 = _v(bd.get("along0")) or 0.0, _v(bd.get("along1")) or L
        for nm in ("下端", "上端"):
            zinfo[f"{bd.get('name', '帯')} {nm}"] = ([(a0, a1)], max(dep, 0.0))     # 手前に出た帯は前の縁
    for op in f.get("openings") or []:
        sp = [s for s in (bay_span(i) for i in op.get("bays") or []) if s]
        if sp:
            zinfo[f"{op.get('name', '開口')} 天井"] = (sp, 0.0)
    parts = b.get("parts") or []
    p0, u, nv, _ = face_frame(f)

    def proj(P):
        q = np.array(P[:2], float) - p0
        return float(q @ u), float(q @ nv)

    out: list[dict] = []
    for l in xs:
        if l.kind == "landmark" and pier > 0 and any(abs(c - l.m) <= pier / 2 + 0.05 for c in edges):
            continue        # 面の端が柱型の中に入る時、端そのものの縁は写らない（compare は重み 0.25）
        zr = (g, top)
        m = re.match(r"^部品(\d+)（", l.name)
        if m and int(m.group(1)) < len(parts):
            pt = parts[int(m.group(1))]
            zr = (_v(pt.get("z0")) or g, _v(pt.get("z1")) or top)
        mb = re.match(r"^塊(.+?) ", l.name)
        if mb:
            blk = next((k for k in b.get("blocks") or [] if str(k.get("id")) == mb.group(1)), None)
            if blk and _v(blk.get("top_z")):
                zr = (g, _v(blk.get("top_z")))
        n = l.depth_m if not l.in_plane else 0.0      # 柱型の縁も compare と同じく面の平面（H_plane の平面＝正面化で合わせた平面）
        out.append(_line(l.name, "面", l.kind, "s", l.m, n, [zr], "compare"))
    for l in zs:
        rng, n = zinfo.get(l.name, ([(0.0, L)], 0.0))
        m = re.match(r"^部品(\d+)（", l.name)
        if m and int(m.group(1)) < len(parts):
            pt = parts[int(m.group(1))]
            if pt.get("type") == "balcony":
                rng = [(_v(pt.get("along0")) or 0.0, _v(pt.get("along1")) or L)]
            elif pt.get("polygon"):
                ss = [proj(P)[0] for P in pt["polygon"]]
                rng = [(max(0.0, min(ss)), min(L, max(ss)))]
            n = l.depth_m
        mb = re.match(r"^塊(.+?) ", l.name)
        if mb:
            blk = next((k for k in b.get("blocks") or [] if str(k.get("id")) == mb.group(1)), None)
            if blk:
                ss = [proj(P)[0] for P in blk.get("polygon") or [] if abs(proj(P)[1]) <= 0.5]
                if len(ss) >= 2:
                    rng = [(max(0.0, min(ss)), min(L, max(ss)))]
        out.append(_line(l.name, "面", l.kind, "z", l.m, n, rng, "compare"))
    out += new_part_lines(b, face_id, L, g, wspans)
    return out


def new_part_lines(b: dict, face_id: str, L: float, g: float, wspans) -> list[dict]:
    """compare が出さない新しい語彙の縁：parts_geom の素片（外階段・柱・段・玄関・室外機…）、窓下パネル、帯の上の手すり。"""
    faces = {f["id"]: f for f in b.get("faces", [])}
    f = faces[face_id]
    fax = parts_geom.frame_axes(face_id, faces)
    out: list[dict] = []
    # 窓下パネル（windows[].spandrel）の下端
    for w in f.get("windows") or []:
        sp, zb = w.get("spandrel"), _v(w.get("z_bottom"))
        h = _v((sp or {}).get("height"))
        if sp and h and zb is not None:
            n = -(_v(w.get("recess")) or 0.0) if sp.get("in_recess") else 0.0
            out.append(_line(f"窓下パネル（{_short(w.get('name', '窓'))}）下端", "部品", "spandrel", "z", zb - h, n, wspans, "windows[].spandrel"))
    # 帯の上の手すり（bands[].rail）
    for bd in f.get("bands") or []:
        rl, zt = bd.get("rail"), _v(bd.get("z_top"))
        if rl and zt is not None:
            h = _v(rl.get("height"))
            if h is None:
                continue
            dep = _v(bd.get("depth")) or 0.0
            a0, a1 = _v(bd.get("along0")) or 0.0, _v(bd.get("along1")) or L
            out.append(_line(f"帯の手すり（{_short(bd.get('name', '帯'))}）上端", "部品", "rail", "z", zt + h, max(dep, 0.03) / 2,
                             [(a0, a1)], "bands[].rail"))
    # parts の素片
    for i, pt in enumerate(b.get("parts") or []):
        t = pt.get("type")
        if t not in parts_geom.ALL_PART_TYPES or t in ("rooftop_units",):
            continue
        own = pt.get("face") == face_id
        if pt.get("face") and not own:
            continue
        got = parts_geom.part_prims(pt, faces)
        if not got or not got[2]:
            continue
        fr, _cut, prims = got
        pax = parts_geom.frame_axes(fr, faces)
        tag = f"部品{i}（{_short(pt.get('name', t))}）"
        merged: dict[tuple, list] = {}
        for pr in prims:
            vw = parts_geom.prim_vertices(pr, pax)
            v = parts_geom.to_frame(vw, fax)
            a = [q[0] for q in v]
            nn = [q[1] for q in v]
            zz = [q[2] for q in v]
            nf = max(nn)
            if nf < -0.3 or nf > 25 or max(a) <= 0 or min(a) >= L:
                continue
            if not own and (nf <= 0.05 or nearest_face(b, vw) != face_id):
                continue
            a0, a1, z0, z1 = max(min(a), 0.0), min(max(a), L), min(zz), max(zz)
            cand = []
            if a1 - a0 >= 0.15:
                cand += [("z", z1, (a0, a1), "上端")]
                if z0 > g + 0.05:
                    cand += [("z", z0, (a0, a1), "下端")]
            if z1 - z0 >= 0.3:
                if min(a) >= 0:
                    cand += [("s", a0, (z0, z1), "左端")]
                if max(a) <= L:
                    cand += [("s", a1, (z0, z1), "右端")]
            for d, pos, rng, nm in cand:
                key = (d, round(pos / 0.05), round(nf, 0))
                if key in merged:
                    old = merged[key]
                    old[2] = (min(old[2][0], rng[0]), max(old[2][1], rng[1]))
                else:
                    merged[key] = [d, pos, rng, nm, pr.get("name", ""), nf]
        items = sorted(merged.values(), key=lambda m: -(m[2][1] - m[2][0]))[:16]
        for d, pos, rng, nm, pn, nf in items:
            if rng[1] - rng[0] < 0.3:
                continue
            out.append(_line(f"{tag} {pn} {nm}".replace("  ", " "), "部品", t, d, pos, nf, [rng], "parts_geom"))
    return out


def nearest_face(b: dict, verts) -> str | None:
    """単独の部品（世界座標の柱・風除室など）が、どの面の前にあるか：中心が面の幅に入り、面の外側で一番近い面。"""
    c = np.mean(np.array(verts, float), axis=0)
    best = None
    for f in b.get("faces", []):
        if not (f.get("from") and f.get("to")):
            continue
        p0, u, nv, L = face_frame(f)
        q = c[:2] - p0
        a, n = float(q @ u), float(q @ nv)
        if -0.5 <= a <= L + 0.5 and n >= -0.3 and (best is None or n < best[0]):
            best = (n, f["id"])
    return best[1] if best else None


def _short(s: str, n: int = 12) -> str:
    return (s or "").split("（")[0][:n]


# --- 写真の縁の読み取り -------------------------------------------------------------------

class EdgeImage:
    """写真の勾配（x・y）。線の向きに直角な縁の強さを、写真の任意の点で読む。"""

    def __init__(self, img: np.ndarray, blur: float = 1.5):
        import cv2

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
        g = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), blur)
        self.gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
        self.gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
        mag = np.hypot(self.gx[::4, ::4], self.gy[::4, ::4])
        self.noise = float(np.percentile(mag, 60))
        self.h, self.w = gray.shape[:2]

    def directional(self, u: np.ndarray, v: np.ndarray, dx: np.ndarray, dy: np.ndarray) -> np.ndarray:
        import cv2

        mu, mv = u.astype(np.float32).reshape(1, -1), v.astype(np.float32).reshape(1, -1)
        gx = cv2.remap(self.gx, mu, mv, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0).ravel()
        gy = cv2.remap(self.gy, mu, mv, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0).ravel()
        return np.abs(gx * dx.ravel() + gy * dy.ravel()).reshape(u.shape)


class Projector:
    """登録の 1 件：今の面の (s, n, z) を写真の px に。面の上（|n| が小さい）は H_plane、外は視点。"""

    def __init__(self, entry: dict, face_now: dict):
        self.e = entry
        self.H = np.array(entry["H_plane"], float)
        self.reg_frame = entry["face_frame"]
        self.now = {"from": face_now["from"], "to": face_now["to"]}
        self.cam = entry.get("camera") if entry.get("camera", {}).get("R_cv") else None
        self.w, self.h = entry["image_size"]

    def __call__(self, s, n, z) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        s, n, z = np.broadcast_arrays(np.asarray(s, float), np.asarray(n, float), np.asarray(z, float))
        P = plane_to_world(self.now, s, z, n)                 # 今の面 → 世界
        q = world_to_plane(self.reg_frame, P)                  # 世界 → 登録した時の面の (s, n, z)
        U, V, W = apply_H(self.H, q[..., 0], q[..., 2])
        off = np.abs(q[..., 1]) > 0.05
        if off.any():
            if self.cam is None:
                U, V, W = U.copy(), V.copy(), W.copy()
                W[off] = -1.0                                  # 視点が無ければ面の外の線は測らない
            else:
                uu, vv, ww = project_world(self.cam, P[off])
                U, V, W = U.copy(), V.copy(), W.copy()
                U[off], V[off], W[off] = uu, vv, ww
        return U, V, W


def measure_line(edge: EdgeImage, proj: Projector, line: dict, search_m: float = SEARCH_M, step_m: float = STEP_M) -> dict:
    """線 1 本のずれ（m）。返り値: {diff_m, found, n_samples, contrast, support, uv（描画用の端点）}。"""
    S = np.array(line["samples"], float).reshape(-1, 3)        # (s, n, z)
    deltas = np.arange(-search_m, search_m + step_m / 2, step_m)
    k = 0 if line["dir"] == "s" else 2
    base_u, base_v, base_w = proj(S[:, 0], S[:, 1], S[:, 2])
    S2 = S.copy()
    S2[:, k] += 0.05
    u2, v2, _ = proj(S2[:, 0], S2[:, 1], S2[:, 2])
    dx, dy = u2 - base_u, v2 - base_v
    nrm = np.hypot(dx, dy)
    margin = 4
    vis = (base_w > 0) & np.isfinite(base_u) & (base_u >= margin) & (base_u < edge.w - margin) & (base_v >= margin) & (base_v < edge.h - margin) & (nrm > 1e-6)
    res = {"diff_m": None, "found": False, "n_samples": int(vis.sum()), "contrast": None, "support": None,
           "px_per_m": round(float(np.median(nrm[vis]) / 0.05), 1) if vis.any() else None, "uv": None}
    if vis.sum() < MIN_SAMPLES:
        res["reason"] = "写真に入らない" if vis.sum() == 0 else "写真に入る点が少ない"
        return res
    S, dx, dy, nrm = S[vis], dx[vis] / nrm[vis], dy[vis] / nrm[vis], nrm[vis]
    res["uv"] = [[round(float(base_u[vis][0]), 1), round(float(base_v[vis][0]), 1)], [round(float(base_u[vis][-1]), 1), round(float(base_v[vis][-1]), 1)]]
    G = np.repeat(S[:, None, :], len(deltas), axis=1)
    G[:, :, k] += deltas[None, :]
    U, V, W = proj(G[..., 0], G[..., 1], G[..., 2])
    inside = (W > 0) & np.isfinite(U) & (U >= 1) & (U < edge.w - 2) & (V >= 1) & (V < edge.h - 2)
    U, V = np.where(inside, U, 0), np.where(inside, V, 0)
    r = edge.directional(U, V, np.repeat(dx[:, None], len(deltas), 1), np.repeat(dy[:, None], len(deltas), 1)) * inside
    mx = r.max(axis=1)
    good = mx > edge.noise
    if good.sum() < MIN_SAMPLES:
        res["reason"] = "縁が弱い（木・影・空）"
        return res
    rn = r[good] / mx[good, None]
    prof = rn.mean(axis=0)
    prof = np.convolve(prof, np.ones(3) / 3, mode="same")
    pk = [j for j in range(1, len(prof) - 1) if prof[j] >= prof[j - 1] and prof[j] >= prof[j + 1] and prof[j] >= PEAK_REL * prof.max()]
    if not pk:
        res["reason"] = "山が無い"
        return res
    j = min(pk, key=lambda j: abs(deltas[j]))
    med = float(np.median(prof)) or 1e-6
    res["contrast"] = round(float(prof[j] / med), 2)
    arg = deltas[rn.argmax(axis=1)]
    res["support"] = round(float(np.mean(np.abs(arg - deltas[j]) <= 0.05)), 2)
    if j <= 2 or j >= len(prof) - 3:
        res["reason"] = f"探す範囲（±{search_m} m）の端：対応の取り違えか大きなずれ"
        return res
    if res["contrast"] < MIN_CONTRAST:
        res["reason"] = "縁がはっきりしない"
        return res
    # 山の頂を放物線で細かく
    y0, y1, y2 = prof[j - 1], prof[j], prof[j + 1]
    den = y0 - 2 * y1 + y2
    off = 0.5 * (y0 - y2) / den if abs(den) > 1e-9 else 0.0
    res["diff_m"] = round(float(deltas[j] + np.clip(off, -1, 1) * step_m), 3)
    res["found"] = True
    return res


# --- 実行 -------------------------------------------------------------------------------

def _prev_summary(root: Path, now_dir: Path) -> dict | None:
    if not root.exists():
        return None
    runs = sorted(d for d in root.iterdir() if d.is_dir() and d != now_dir and (d / "summary.json").exists())
    return json.loads((runs[-1] / "summary.json").read_text(encoding="utf-8")) if runs else None


def draw_lines(img: np.ndarray, proj: Projector, lines: list[dict], out_png: Path) -> Path:
    import cv2

    canvas = img.copy()
    for ln in lines:
        S = np.array(ln["samples"], float).reshape(-1, 3)
        u, v, w = proj(S[:, 0], S[:, 1], S[:, 2])
        ok = (w > 0) & np.isfinite(u)
        if ok.sum() < 2:
            continue
        r = ln["result"]
        col = (255, 120, 0)
        if r["found"]:
            col = (0, 200, 0) if abs(r["diff_m"]) <= TOL_M else (0, 0, 255)
        pts = np.stack([u[ok], v[ok]], 1).astype(np.int32)
        cv2.polylines(canvas, [pts], False, (255, 120, 0), 3)
        if r["found"]:
            k = 0 if ln["dir"] == "s" else 2
            S2 = S.copy()
            S2[:, k] += r["diff_m"]
            u2, v2, w2 = proj(S2[:, 0], S2[:, 1], S2[:, 2])
            ok2 = (w2 > 0) & np.isfinite(u2)
            if ok2.sum() >= 2:
                cv2.polylines(canvas, [np.stack([u2[ok2], v2[ok2]], 1).astype(np.int32)], False, col, 2)
            if abs(r["diff_m"]) > TOL_M:
                cv2.putText(canvas, f"{r['diff_m']:+.2f}", tuple(int(x) for x in pts[len(pts) // 2]), cv2.FONT_HERSHEY_SIMPLEX, 1.4, col, 3)
    h, w = canvas.shape[:2]
    s = min(1.0, 2000 / max(h, w))
    if s < 1:
        canvas = cv2.resize(canvas, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".png", canvas)[1].tofile(str(out_png))
    return out_png


def recheck(building_json: Path, photos: list[str] | None = None, fast: bool = False, render: bool = True,
            out_root: Path | None = None, registry: dict | None = None, blender: str | None = None,
            stamp: str | None = None, draw: bool = True) -> tuple[dict, Path]:
    from .rectify import read_image

    b = load_building(building_json)
    reg = registry if registry is not None else load_registry()
    ents = expand_entries(entries_for(reg, b["id"], photos), b)        # pose の登録は見えている面ごとに
    root = out_root or Path("out") / b["id"] / "recheck"
    out = root / (stamp or datetime.now().strftime("%Y%m%d_%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)
    prev = _prev_summary(root, out)
    prev_map = {}
    for ph in (prev or {}).get("photos", []):
        for ln in ph.get("lines", []):
            prev_map[(ph["photo"], ph["face"], ln["what"])] = ln.get("diff_m")
    faces = {f["id"]: f for f in b.get("faces", [])}
    summary = {"building": b["id"], "building_json": str(building_json), "made_by": "recheck（recorder）",
               "made_at": datetime.now().isoformat(timespec="seconds"), "tol_m": TOL_M, "search_m": SEARCH_M,
               "previous": (prev or {}).get("made_at"), "photos": [], "renders": [], "warnings": []}
    for e in ents:
        f = faces.get(e["face"])
        if f is None or not (f.get("from") and f.get("to")):
            summary["warnings"].append(f"{e['photo']}: 面 {e['face']} が今の棟 JSON に無いか位置が未確定")
            continue
        ph = photo_path(e)
        if not ph.exists():
            summary["warnings"].append(f"{e['photo']}: 写真が見つからない（{ph}）")
            continue
        img = read_image(ph)
        if [img.shape[1], img.shape[0]] != list(e["image_size"]):
            summary["warnings"].append(f"{e['photo']}: 写真の大きさが登録と違う {img.shape[1]}x{img.shape[0]}")
            continue
        edge = EdgeImage(img)
        proj = Projector(e, f)
        lines = face_check_lines(b, e["face"])
        rows = []
        for ln in lines:
            r = measure_line(edge, proj, ln)
            ln["result"] = r
            if r["n_samples"] == 0:
                continue
            d = r["diff_m"]
            pd = prev_map.get((e["photo"], e["face"], ln["what"]))
            rows.append({"what": ln["what"], "group": ln["group"], "kind": ln["kind"], "axis": "x" if ln["dir"] == "s" else "z",
                         "json_m": ln["json_m"], "depth_m": ln["depth_m"], "diff_m": d,
                         "ok": (abs(d) <= TOL_M) if d is not None else None, "contrast": r["contrast"], "support": r["support"],
                         "px_per_m": r["px_per_m"], "reason": r.get("reason", ""),
                         "prev_diff_m": pd, "change_m": round(d - pd, 3) if (d is not None and pd is not None) else None})
        for r in rows:
            r["weak"] = r["diff_m"] is not None and (r["contrast"] or 0) < STRONG_CONTRAST
        found = [r for r in rows if r["diff_m"] is not None and not r["weak"]]

        def worst(rs):
            return max(rs, key=lambda r: abs(r["diff_m"])) if rs else None
        wf = worst([r for r in found if r["group"] == "面"])
        wp = worst([r for r in found if r["group"] == "部品"])
        item = {"photo": e["photo"], "face": e["face"], "period": e.get("period"),
                "n_lines": len(rows), "n_found": len(found), "n_weak": sum(1 for r in rows if r["weak"]), "n_ok": sum(1 for r in found if r["ok"]),
                "n_ng": sum(1 for r in found if not r["ok"]),
                "worst_face": ({"what": wf["what"], "diff_m": wf["diff_m"]} if wf else None),
                "worst_parts": ({"what": wp["what"], "diff_m": wp["diff_m"]} if wp else None),
                "pass_face": all(r["ok"] for r in found if r["group"] == "面") if any(r["group"] == "面" for r in found) else None,
                "pass_parts": all(r["ok"] for r in found if r["group"] == "部品") if any(r["group"] == "部品" for r in found) else None,
                "lines": rows}
        if draw:
            item["lines_png"] = str(draw_lines(img, proj, [l for l in lines if l["result"]["n_samples"]],
                                               out / f"lines_{Path(ph).stem}_{e['face']}.png"))
        summary["photos"].append(item)
    if render and ents:
        summary["renders"] = render_overlays(building_json, ents, out, fast, blender)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "summary.md").write_text(summary_md(summary), encoding="utf-8")
    return summary, out


def render_overlays(building_json: Path, ents: list[dict], out: Path, fast: bool, blender: str | None = None) -> list[dict]:
    """(b) 登録の視点で模型を描き、写真に重ねる。"""
    from . import model
    from .pose import overlay

    pdir = out / "poses"
    pdir.mkdir(parents=True, exist_ok=True)
    cams, res = [], []
    seen: set = set()
    ents = [e for e in ents if not (e["photo"] in seen or seen.add(e["photo"]))]     # 写真ごとに 1 回（pose の登録は面ごとに並ぶ）
    for e in ents:
        bl = (e.get("camera") or {}).get("blender")
        if not bl:
            res.append({"photo": e["photo"], "ok": False, "reason": "視点（blender）が登録に無い"})
            continue
        p = pdir / f"{Path(e['file']).stem}_{e['face']}_pose.json"
        p.write_text(json.dumps({"ok": True, "photo": str(photo_path(e)), "blender": bl}, ensure_ascii=False), encoding="utf-8")
        cams.append(p)
    if not cams:
        return res
    b = load_building(building_json)
    mdir = model.build(building_json, None, blender, fast, cams)
    for e in ents:
        stem = Path(e["file"]).stem
        ren = Path(mdir) / f"view_photo_{stem}.png"
        if not ren.exists():
            res.append({"photo": e["photo"], "ok": False, "reason": f"描画が無い {ren}"})
            continue
        o = overlay(photo_path(e), ren, out / f"overlay_{stem}.png")
        res.append({"photo": e["photo"], "ok": True, "render": str(ren), "overlay": str(o)})
    return res


def summary_md(s: dict) -> str:
    L = [f"# 再検収 {s['building']}（{s['made_at']}）", "",
         f"合否の目安 {s['tol_m']} m。ずれ＝写真 − JSON（m）。横の線は高さ、縦の線は面に沿う向き。線から ±{s['search_m']} m で写真の縁を探した。"
         f"前回 {s.get('previous') or 'なし'}。", "",
         f"縁の強さ（山の高さ÷中央値）が {STRONG_CONTRAST} 未満の線は「弱い縁」として参考に出し、合否と最大のずれには数えない。", "",
         "「面」＝compare と同じ線（柱型・窓・帯・段・開口・パラペット・塊と箱の縁）、「部品」＝新しい部品の縁（外階段・柱・段・玄関・窓下パネル・帯の手すり…）。", "",
         "| 写真 | 面 | 面の線 合否 | 面の最大のずれ | 部品の線 合否 | 部品の最大のずれ | 見つかった線 / 全部（弱い縁） |", "|---|---|---|---|---|---|---|"]

    def pf(v):
        return "合格" if v else ("不合格" if v is False else "—")

    def w(x):
        return f"{x['diff_m']:+.2f} m（{x['what']}）" if x else "—"
    for p in s["photos"]:
        L.append(f"| {p['photo'][:40]} | {p['face']} | {pf(p['pass_face'])} | {w(p['worst_face'])} | {pf(p['pass_parts'])} | {w(p['worst_parts'])} | {p['n_found']} / {p['n_lines']}（{p['n_weak']}） |")
    for p in s["photos"]:
        L += ["", f"## {p['photo']}（{p['face']}）", "", "| 線 | 種類 | JSON m | ずれ m | 合否 | 前回との差 | 縁の強さ | 備考 |", "|---|---|---|---|---|---|---|---|"]
        rows = sorted(p["lines"], key=lambda r: (r["diff_m"] is None, r["group"], -abs(r["diff_m"] or 0)))
        for r in rows:
            d = f"{r['diff_m']:+.3f}" if r["diff_m"] is not None else "見つからず"
            ch = f"{r['change_m']:+.3f}" if r.get("change_m") is not None else ""
            ok = "OK" if r["ok"] else ("NG" if r["ok"] is False else "—")
            if r.get("weak"):
                ok += "（弱い縁・参考）"
            L.append(f"| {r['what']} | {r['group']}/{r['kind']} | {r['json_m']:.2f} | {d} | {ok} | {ch} | {r['contrast'] or ''} | {r['reason']} |")
        if p.get("lines_png"):
            L.append(f"\n線の画像: `{p['lines_png']}`（青＝JSON、緑／赤＝写真の縁の位置）")
    if s.get("renders"):
        L += ["", "## 重ね画像", ""]
        for r in s["renders"]:
            L.append(f"- {r['photo']}: " + (f"`{r['overlay']}`" if r["ok"] else f"できず（{r['reason']}）"))
    if s.get("warnings"):
        L += ["", "## 注意", ""] + [f"- {x}" for x in s["warnings"]]
    return "\n".join(L) + "\n"
