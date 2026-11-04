"""被覆図：面ごとに、登録済みの写真が覆う範囲を立面図に重ね、覆われていない所を灰色にする。

立面図（recorder draw の elev_<面>.svg）と同じ座標（drawing.SCALE・LEFT・TOP）に、写真ごとの覆う範囲（registry の H_plane を
今の面の矩形で切った多角形）を半透明で重ね、覆われていない格子を灰色で塗る。位置が未確定（unknown）の面は全面灰色。
木・他の棟で隠れた所は数えない（写真の枠に入るかだけ）。

出力 out/<棟>/coverage_<面>.svg、out/<棟>/coverage.json（面ごとの被覆率 %、覆われていない区間 m、撮影依頼の文）
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np

from . import drawing
from .registry import (compass8, cover_polygon, entries_for, face_frame, face_top, load_registry, plane_to_world,
                       world_to_plane)
from .schema import Measurement, load_building

CELL_M = 0.25
PALETTE = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#17becf", "#8c564b", "#e377c2"]


def _known_len(f: dict) -> float | None:
    m = f.get("length")
    if isinstance(m, (int, float)):
        return float(m)
    if isinstance(m, dict):
        mm = Measurement.from_json(m)
        if mm.known:
            return float(mm.value)
    if f.get("from") and f.get("to"):
        return face_frame(f)[3]
    return None


def entry_cover_now(e: dict, f: dict, rect: tuple[float, float, float, float]) -> list[tuple[float, float]]:
    """登録の H_plane を、今の面の (s, z) に直して覆う範囲を切る。面が登録の後に動いても（同じ直線の上なら）合う。"""
    now = {"from": f["from"], "to": f["to"]}
    # 今の面の s → 登録の面の s は 1 次式（同じ直線の上なら s_reg = a·s + c）。3 点で H に合成する
    P = plane_to_world(now, np.array([0.0, 1.0, 0.0]), np.array([0.0, 0.0, 1.0]))
    q = world_to_plane(e["face_frame"], P)
    a, c = q[1, 0] - q[0, 0], q[0, 0]
    T = np.array([[a, 0, c], [0, 1, 0], [0, 0, 1]], float)
    H = np.array(e["H_plane"], float) @ T
    w, h = e["image_size"]
    return cover_polygon(H, w, h, rect)


def raster(polys: list[list[tuple[float, float]]], rect, cell: float = CELL_M) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """面の矩形を cell m の格子にし、どれかの多角形に入る格子を True。返り値 (mask[z, s], s の中心, z の中心)。"""
    s0, s1, z0, z1 = rect
    sc = np.arange(s0 + cell / 2, s1, cell)
    zc = np.arange(z0 + cell / 2, z1, cell)
    S, Z = np.meshgrid(sc, zc)
    mask = np.zeros(S.shape, bool)
    for poly in polys:
        if len(poly) < 3:
            continue
        inside = np.ones(S.shape, bool)
        n = len(poly)
        area2 = sum(poly[i][0] * poly[(i + 1) % n][1] - poly[(i + 1) % n][0] * poly[i][1] for i in range(n))
        sgn = 1.0 if area2 >= 0 else -1.0
        for i in range(n):            # 凸多角形（射影で切った矩形は凸）：全部の辺の内側
            (xa, ya), (xb, yb) = poly[i], poly[(i + 1) % n]
            inside &= sgn * ((xb - xa) * (Z - ya) - (yb - ya) * (S - xa)) >= -1e-9
        mask |= inside
    return mask, sc, zc


def uncovered_rects(mask: np.ndarray, sc: np.ndarray, zc: np.ndarray, cell: float = CELL_M, max_n: int = 4,
                    min_area: float = 4.0, min_side: float = 1.0) -> list[dict]:
    """覆われていない格子を、大きい長方形から順に取り出す（撮影依頼の区間）。"""
    free = ~mask
    out = []
    for _ in range(max_n):
        best = None
        H, W = free.shape
        hist = np.zeros(W, int)
        for r in range(H):
            hist = np.where(free[r], hist + 1, 0)
            stack: list[int] = []
            for c in range(W + 1):
                hc = hist[c] if c < W else 0
                while stack and hist[stack[-1]] >= hc:
                    top = stack.pop()
                    left = stack[-1] + 1 if stack else 0
                    area = hist[top] * (c - left)
                    if best is None or area > best[0]:
                        best = (area, r - hist[top] + 1, r, left, c - 1)
                stack.append(c)
        if not best or best[0] == 0:
            break
        _, r0, r1, c0, c1 = best
        a = (r1 - r0 + 1) * cell * (c1 - c0 + 1) * cell
        ws, hz = (c1 - c0 + 1) * cell, (r1 - r0 + 1) * cell
        if a < min_area or ws < min_side or hz < min_side:
            break
        out.append({"s0": round(float(sc[c0] - cell / 2), 2), "s1": round(float(sc[c1] + cell / 2), 2),
                    "z0": round(float(zc[r0] - cell / 2), 2), "z1": round(float(zc[r1] + cell / 2), 2), "area_m2": round(a, 1)})
        free[r0:r1 + 1, c0:c1 + 1] = False
    return out


def request_text(b: dict, f: dict, r: dict, ground: float, L: float) -> str:
    p0, p1 = np.array(f["from"], float), np.array(f["to"], float)
    end = compass8(*(p0 - p1))
    name = f.get("name") or f["id"]
    return (f"{f['id']} 面（{name}）の{end}端（from）から {r['s0']:.0f}〜{r['s1']:.0f} m・高さ（地面から）"
            f"{max(0.0, r['z0'] - ground):.0f}〜{r['z1'] - ground:.0f} m が未撮影")


def coverage(building_json: Path, registry: dict | None = None, out_dir: Path | None = None, cell: float = CELL_M) -> tuple[dict, Path]:
    b = load_building(building_json)
    reg = registry if registry is not None else load_registry()
    ents = entries_for(reg, b["id"])
    out = out_dir or Path("out") / b["id"]
    out.mkdir(parents=True, exist_ok=True)
    res = {"building": b["id"], "made_by": "coverage（recorder）", "made_at": datetime.now().isoformat(timespec="seconds"),
           "cell_m": cell, "note": "写真の枠に入るかだけを数える（木・他の棟で隠れた所も覆ったと数える）", "faces": [], "requests": []}
    _, Hb, _ = drawing._face_height(b)
    for f in b.get("faces", []):
        fid = f["id"]
        L = _known_len(f)
        g, _src = drawing.face_ground_line(b, f) if f.get("from") and f.get("to") else (0.0, "none")
        top = face_top(b, f)
        unknown = L is None or not (f.get("from") and f.get("to"))
        row = {"face": fid, "name": f.get("name"), "unknown": unknown, "photos": [], "coverage_pct": 0.0,
               "uncovered": [], "length_m": round(L, 2) if L else None, "ground_z": round(g, 2), "top_z": top}
        polys = []
        if not unknown:
            rect = (0.0, L, g, top)
            for e in ents:
                if e["face"] != fid:
                    continue
                poly = entry_cover_now(e, f, rect)
                if len(poly) >= 3:
                    polys.append(poly)
                    row["photos"].append({"photo": e["photo"], "period": e.get("period"), "polygon": [list(p) for p in poly]})
            mask, sc, zc = raster(polys, rect, cell)
            row["coverage_pct"] = round(100.0 * float(mask.mean()), 1) if mask.size else 0.0
            row["uncovered"] = uncovered_rects(mask, sc, zc, cell)
            for r in (row["uncovered"] if polys else []):
                r["text"] = request_text(b, f, r, g, L)
                res["requests"].append({"face": fid, **r})
            if not polys:
                res["requests"].append({"face": fid, "s0": 0.0, "s1": round(L, 2), "z0": g, "z1": top,
                                        "text": f"{fid} 面（{f.get('name') or fid}）は登録済みの写真が 1 枚も無い（全面が未撮影）"})
        else:
            mask, sc, zc = None, None, None
            res["requests"].append({"face": fid, "text": f"{fid} 面（{f.get('name') or fid}）は位置か長さが未確定（unknown）。面の両端が入る写真が要る"})
        try:
            svg = drawing.elevation_svg(b, fid)
        except Exception as ex:          # 立面が描けない面（unknown）は被覆だけの小さい図
            svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="600" height="200"><text x="20" y="40">{fid}: 立面図を描けません（{ex}）</text></svg>'
        (out / f"coverage_{fid}.svg").write_text(overlay_svg(svg, row, mask, sc, zc, Hb, cell), encoding="utf-8")
        row["svg"] = str(out / f"coverage_{fid}.svg")
        res["faces"].append(row)
    jp = out / "coverage.json"
    jp.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res, jp


def overlay_svg(svg: str, row: dict, mask, sc, zc, H_draw: float, cell: float) -> str:
    """立面図の SVG に、覆う範囲（写真ごとに色）と覆われていない格子（灰色）を重ねる。座標は drawing と同じ。"""
    S, LEFT, TOP = drawing.SCALE, drawing.LEFT, drawing.TOP

    def X(s):
        return LEFT + s * S

    def Y(z):
        return TOP + H_draw * S - z * S

    g = ['<g id="coverage">']
    if row["unknown"] or mask is None:
        g.append(f'<rect x="0" y="0" width="100%" height="100%" fill="#888" fill-opacity="0.55"/>')
        g.append(f'<text x="{LEFT}" y="{TOP - 12}" font-size="14" fill="#c8322b">被覆 0 %（面の位置か長さが unknown）</text>')
    else:
        for zi in range(mask.shape[0]):           # 覆われていない格子を行ごとにまとめて灰色
            c = 0
            while c < mask.shape[1]:
                if mask[zi, c]:
                    c += 1
                    continue
                c0 = c
                while c < mask.shape[1] and not mask[zi, c]:
                    c += 1
                s0, s1 = sc[c0] - cell / 2, sc[c - 1] + cell / 2
                z1 = zc[zi] + cell / 2
                g.append(f'<rect x="{X(s0):.1f}" y="{Y(z1):.1f}" width="{(s1 - s0) * S:.1f}" height="{cell * S:.2f}" fill="#777" fill-opacity="0.6" stroke="none"/>')
        for i, p in enumerate(row["photos"]):
            col = PALETTE[i % len(PALETTE)]
            pts = " ".join(f"{X(a):.1f},{Y(z):.1f}" for a, z in p["polygon"])
            g.append(f'<polygon points="{pts}" fill="{col}" fill-opacity="0.18" stroke="{col}" stroke-width="1.5"/>')
            cx = sum(a for a, _ in p["polygon"]) / len(p["polygon"])
            cz = sum(z for _, z in p["polygon"]) / len(p["polygon"])
            g.append(f'<text x="{X(cx):.1f}" y="{Y(cz):.1f}" font-size="11" fill="{col}" text-anchor="middle">{p["photo"][:24]}</text>')
        g.append(f'<text x="{LEFT}" y="{TOP - 12}" font-size="14" fill="#1f4e79">被覆 {row["coverage_pct"]} %（写真 {len(row["photos"])} 枚。灰色＝未撮影）</text>')
    g.append("</g>")
    i = svg.rfind("</svg>")
    return svg[:i] + "\n".join(g) + svg[i:] if i >= 0 else svg
