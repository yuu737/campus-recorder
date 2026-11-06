"""目印の対応で写真の視点を解く（登録の本線。11/6）。

流れ
    1. 棟 JSON から目印の一覧を出す（面ごと：面の端・柱型の左縁・部品の縁 × 地面・パラペット・一番下の窓の下端・一番上の窓の上端）。
       `out/<棟>/landmarks/<面>.png`（番号付きの立面の絵）と `landmarks.json`（id・名前・3D）。
    2. 写真に 10×10 の升目（列 A〜J・行 1〜10）を引いた絵 `<写真>_grid.jpg` を出す。目印係（係か人）が「目印 id → 升」を指す。
    3. 升を各側に 0.25 升広げた 1.5 升四方で切り出し、10×10 の升目を引いた寄りの絵 `<写真>_<升>.jpg` を出す。係が寄りの升を指す（幅の約 1.5 %）。
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
MIN_CAM_HEIGHT, MAX_CAM_HEIGHT = 0.3, 12.0   # 0.3：しゃがんで撮った写真（本部棟の登録済みは 0.52 m が最低）
PITCH_WARN_DEG = 8.0        # 消失点の仰角との差がこれを超えたら警告
MIN_BBOX_FRAC = 0.25        # 対応の点が写真の幅・高さのこの割合以上に散っていること（一直線・一塊では解が暴れる）
EDGE_MIN_INLIER = 0.15      # 検算：解いた視点で模型の線の点が写真の縁 3 px 以内に乗る割合（本部棟の正しい登録は 0.17〜0.46、偽の合格は 0.09）
EDGE_MIN_GRAD = 0.25        # 検算：線に直角な勾配の平均（正しい登録 0.25〜0.44。どちらか一方を満たせば通す）
MIN_INLIERS = 5             # 合格：PnP に使われた（外れでない）対応の数。4 点は解が点に合わせきりで検算にならない
PITCH_FAIL_DEG = 15.0       # 消失点の仰角との差がこれを超え、消失点の当てはめが確か（残差 < PITCH_FAIL_RESID）なら不合格
PITCH_FAIL_RESID = 200.0
FACE_GROUND_DIST = 40.0     # カメラの高さの地面：面の前（法線の側）この距離以内なら、その面の ground_offset も地面として認める
                            # （5m 標高は沈んだ広場を表せず、タワー75 の西の広場で 3〜5 m 食い違った）
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
    got = _face_verts_horz(b, face_id)
    if got is None:
        return []
    fr, verts, horz, _ = got
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


def face_line_marks(b: dict, face_id: str) -> list[dict]:
    """1 つの面の「線」の目印（11/6 追加。PoseLib の点＋線の PnP 用）。
    縦の線 `<面>-V01…`：面の端・柱型の左縁・部品の縁（点の目印と同じ縦線）の地面〜上端。
    横の線 `<面>-H01…`：地面・パラペット上端・帯・窓の列の上下端（全段）の面の端〜端。
    返り値 [{"id", "face", "kind": "line", "dir": "v"|"h", "name", "s"|"z", "xyz1", "xyz2"}]。
    写真の中では線上のどこを指してもよい（端点でなくてよい）。点の目印の id（`<面>-NN`）とは V/H の字で区別する。"""
    got = _face_verts_horz(b, face_id)
    if got is None:
        return []
    fr, verts, _, hlines = got
    g, top, L = fr["ground_offset"], fr["top_z"], fr["length_m"]
    out = []
    for k, (s, sname) in enumerate(sorted(verts)):
        P1, P2 = plane_to_world(fr, s, g).reshape(3), plane_to_world(fr, s, top).reshape(3)
        out.append({"id": f"{face_id}-V{k + 1:02d}", "face": face_id, "kind": "line", "dir": "v", "name": f"縦 {sname}",
                    "s": round(s, 3), "xyz1": [round(float(v), 3) for v in P1], "xyz2": [round(float(v), 3) for v in P2]})
    for k, (z, zname) in enumerate(hlines):
        P1, P2 = plane_to_world(fr, 0.0, z).reshape(3), plane_to_world(fr, L, z).reshape(3)
        out.append({"id": f"{face_id}-H{k + 1:02d}", "face": face_id, "kind": "line", "dir": "h", "name": f"横 {zname}",
                    "z": round(z, 3), "xyz1": [round(float(v), 3) for v in P1], "xyz2": [round(float(v), 3) for v in P2]})
    return out


GROUND_PART_TYPES = ("steps", "podium", "plaza_steps", "plinth", "terrace")   # 足元の段・基壇・広場の段（上端を地面の線に出す）


def face_ground_marks(b: dict, face_id: str) -> list[dict]:
    """1 つの面の「地面の線」の目印（11/7 追加。上端の目印だけでは高さが決まらないため）。
    `<面>-G01`：面の足元（面の前の地面＝ground_offset。省略は棟の z=0）の面の端〜端。ground_offset が unknown と書かれた面は出さない。
    `<面>-G02…`：棟 JSON の parts のうち、その面の段・基壇（GROUND_PART_TYPES）の上端の線（along〜along+width、
    高さは z0（無ければ面の足元）＋count×rise、または height）。値が unknown の部品は出さない。
    返り値は face_line_marks と同じ形（"kind": "line", "dir": "h", "ground": True）。"""
    f = next((f for f in b.get("faces", []) if f["id"] == face_id and f.get("from") and f.get("to")), None)
    if f is None:
        return []
    fr = face_rect_frame(b, f)
    g, L = fr["ground_offset"], fr["length_m"]
    out: list[dict] = []

    def add(s0, s1, z, name, src):
        P1, P2 = plane_to_world(fr, s0, z).reshape(3), plane_to_world(fr, s1, z).reshape(3)
        out.append({"id": f"{face_id}-G{len(out) + 1:02d}", "face": face_id, "kind": "line", "dir": "h", "ground": True,
                    "name": f"地面 {name}", "z": round(z, 3), "s0": round(s0, 3), "s1": round(s1, 3), "source": src,
                    "xyz1": [round(float(v), 3) for v in P1], "xyz2": [round(float(v), 3) for v in P2]})

    go = f.get("ground_offset")
    if not (isinstance(go, dict) and (go.get("status") == "unknown" or go.get("value") is None)):
        add(0.0, L, g, "面の足元" + ("（面の前の地面 ground_offset）" if go is not None else "（棟の z=0）"), "faces.ground_offset")
    for i, pt in enumerate(b.get("parts") or []):
        if pt.get("type") not in GROUND_PART_TYPES or pt.get("face") != face_id:
            continue
        a, wd = _v(pt.get("along")), _v(pt.get("width"))
        z0 = _v(pt.get("z0")) if pt.get("z0") is not None else g
        cnt, rise, hgt = _v(pt.get("count")), _v(pt.get("rise")), _v(pt.get("height"))
        top = None
        if z0 is not None and cnt is not None and rise is not None:
            top = z0 + cnt * rise
        elif z0 is not None and hgt is not None:
            top = z0 + hgt
        if a is None or wd is None or top is None:
            continue
        s0, s1 = max(0.0, a), min(L, a + wd)
        if s1 - s0 < 0.3:
            continue
        add(s0, s1, top, f"{pt.get('name') or pt['type']} の上端", f"parts[{i}]")
    return out


def _face_verts_horz(b: dict, face_id: str):
    """面の frame・縦線（0.7 m 以内はまとめた物）・点に使う横線・線の目印に使う横線（全段）。面が無ければ None。"""
    f = next((f for f in b.get("faces", []) if f["id"] == face_id and f.get("from") and f.get("to")), None)
    if f is None:
        return None
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
    # 線の目印の横線：地面・上端・帯・窓の上下端（全段）。0.15 m 以内は 1 本に（帯と窓の下端が接する所）
    hl = [(fr["ground_offset"], "地面"), (fr["top_z"], "上端（パラペット）")]
    hl += [(float(l.m), l.name) for l in zs if l.in_plane and fr["ground_offset"] < l.m < fr["top_z"]
           and (("窓" in l.name and (l.name.endswith("下端") or l.name.endswith("上端"))) or "帯" in l.name)]
    hlines: list[tuple[float, str]] = []
    for z, zname in sorted(hl):
        if hlines and abs(z - hlines[-1][0]) < 0.15:
            continue
        hlines.append((z, zname))
    return fr, verts, horz, hlines


def catalog(b: dict, lines: bool = False) -> list[dict]:
    """棟の目印の一覧（点）。lines=True で線の目印（face_line_marks）と地面の線（face_ground_marks、`<面>-G01…`）も足す。"""
    out = []
    for f in b.get("faces", []):
        out += face_landmarks(b, f["id"])
        if lines:
            out += face_line_marks(b, f["id"])
            out += face_ground_marks(b, f["id"])
    return out


def draw_face(b: dict, face_id: str, marks: list[dict], out_png: Path, ppm: int = ELEV_PPM,
              line_marks: list[dict] | None = None) -> Path | None:
    """面の立面の絵（柱割りの線・窓の横線・目印の番号）。写真と見比べて目印の id を選ぶための物。
    line_marks があれば線の目印の番号（縦線 V は上の余白、横線 H は右の余白）を青で書く。"""
    import cv2

    f = next((f for f in b.get("faces", []) if f["id"] == face_id), None)
    if f is None or not marks:
        return None
    fr = face_rect_frame(b, f)
    L, g, top = fr["length_m"], fr["ground_offset"], fr["top_z"]
    pad = 40
    rpad = 40 if line_marks else 0
    W, H = int(L * ppm) + 2 * pad + rpad, int((top - g) * ppm) + 2 * pad + 30
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
    jp_labels = []
    for m in line_marks or []:
        n = m["id"].split("-")[-1]
        if m.get("ground"):
            s0, s1 = m.get("s0", 0.0), m.get("s1", L)
            p1, p2 = px(s0, m["z"]), px(s1, m["z"])
            p1, p2 = (p1[0], p1[1] + 3), (p2[0], p2[1] + 3)          # 面の枠の線と重ならないよう少し下に
            cv2.line(img, p1, p2, (150, 200, 230), 3)
            if m.get("source") == "faces.ground_offset":
                jp_labels.append((f"地面 {n}", (p1[0] + 2, p1[1] + 2)))           # 足元は線の下
            else:
                jp_labels.append((f"段 {n}", (p1[0] + 2, p1[1] - 20)))            # 段の上端は線の上
            continue
        if m["dir"] == "v":
            u, _ = px(m["s"], top)
            cv2.putText(img, n, (u - 10, pad - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 80, 0), 1, cv2.LINE_AA)
        else:
            _, v = px(L, m["z"])
            cv2.putText(img, n, (int(pad + L * ppm) + 4, v + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 80, 0), 1, cv2.LINE_AA)
    if jp_labels:
        img = _put_jp(img, jp_labels, (40, 110, 170))
    cv2.putText(img, f"{b.get('id')} face {face_id}  from(left) -> to(right)  {L:.1f} m x {top - g:.1f} m", (pad, H - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".png", img)[1].tofile(str(out_png))
    return out_png


def _put_jp(img: np.ndarray, labels: list[tuple[str, tuple[int, int]]], bgr=(40, 110, 170), size: int = 13) -> np.ndarray:
    """日本語の文字を書く（cv2 は日本語を書けないので PIL。字体が無ければ ASCII の G01 等だけ）。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return img
    font = None
    for name in ("YuGothM.ttc", "meiryo.ttc", "msgothic.ttc", "NotoSansCJK-Regular.ttc"):
        try:
            font = ImageFont.truetype(name, size)
            break
        except OSError:
            continue
    pil = Image.fromarray(img[:, :, ::-1].copy())
    d = ImageDraw.Draw(pil)
    for text, xy in labels:
        if font is None:
            text = text.split()[-1]
        d.text(xy, text, fill=(bgr[2], bgr[1], bgr[0]), font=font or ImageFont.load_default())
    return np.ascontiguousarray(np.array(pil)[:, :, ::-1])


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


def parse_cell(cell: str) -> tuple[float, float]:
    """"C7" → (列 2, 行 6)。升の半分の位置も書ける（11/7）：
    "F3.5" → 行の半分（F3 と F4 の境）＝(5, 2.5)、"F+3" か "F.5 3" → 列の半分（F と G の境）＝(5.5, 2)。
    小数の升は「その位置を中心にした 1 升」として扱う（cell_rect・寄りの切り出しも同じ）。整数の升は従来と同じ int。"""
    m = re.match(r"^\s*([A-Ja-j])\s*(\+|\.5)?\s*(\d{1,2}(?:\.\d+)?)\s*$", cell or "")
    if not m:
        raise ValueError(f"升の書き方が違う: {cell!r}（例 C7、行の半分 C7.5、列の半分 C+7）")
    col = COLS.index(m.group(1).upper()) + (0.5 if m.group(2) else 0)
    rv = float(m.group(3))
    row = int(rv) - 1 if rv.is_integer() else rv - 1
    if rv.is_integer() and not m.group(2):
        if not 0 <= row < GRID_N:
            raise ValueError(f"行は 1〜{GRID_N}: {cell!r}")
        return col, row
    # 小数：升の中心が写真の枠の中（端の半升まで）
    if not (-0.5 <= row <= GRID_N - 0.5 and col <= GRID_N - 0.5):
        raise ValueError(f"升の位置が枠の外: {cell!r}（行 0.5〜{GRID_N}.5、列は J まで）")
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
        sc, sr = parse_cell(pick["sub"])                     # 寄りの升も小数を許す（"E3.5"＝寄りの E3 と E4 の境）
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


SOLVE_METHODS = ("cv2", "poselib", "poselib+geocalib")
GC_ANGLE_TOL_DEG = 10.0     # poselib+geocalib：解いた R の傾き・仰角が GeoCalib とこれ以上違う候補は落とす
GC_FEW_POINTS = 4           # poselib+geocalib：対応（点＋線）がこれ以下なら傾き・仰角・焦点を固定して 方位と t の 4 自由度で解く


def solve(P3: np.ndarray, uv: np.ndarray, w: int, h: int, focal0: float, search_focal: bool = True,
          method: str = "cv2", lines: list[dict] | None = None, prior: dict | None = None) -> dict:
    """PnP。P3 (N,3) 世界、uv (N,2) 写真 px。焦点は focal0 の 0.85〜1.15 倍を探して再投影が最小の物。
    method："cv2"（従来。cv2.solvePnP SQPNP、6 点以上は RANSAC）／"poselib"（PoseLib の RANSAC＋refine。lines があれば点＋線）／
    "poselib+geocalib"（prior＝GeoCalib の {"focal", "roll", "pitch"}（度、rotation_cv の向き）で焦点を絞り、
    傾き・仰角が GC_ANGLE_TOL_DEG 以上違う候補を落とし、対応が GC_FEW_POINTS 以下なら 4 自由度で解く）。
    lines：[{"X1", "X2"（3D の 2 点）, "x1", "x2"（写真の線上の 2 点 px）}]。cv2 では使わない。
    返り値 {"R", "t", "focal", "reproj_px": 1 点ごと, "rms_px", "inliers": [...], "method", ...}。"""
    if method not in SOLVE_METHODS:
        raise ValueError(f"method は {SOLVE_METHODS} のどれか: {method!r}")
    if method != "cv2":
        return _solve_poselib(P3, uv, w, h, focal0, search_focal, lines or [], prior if method == "poselib+geocalib" else None)
    out = _solve_cv2(P3, uv, w, h, focal0, search_focal)
    if out:
        out["method"] = "cv2"
    return out


def _solve_cv2(P3: np.ndarray, uv: np.ndarray, w: int, h: int, focal0: float, search_focal: bool = True) -> dict:
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


# --- PoseLib（点・点＋線）と GeoCalib の事前値 -------------------------------------------------------

def _project(R, t, f: float, w: int, h: int, P) -> tuple[np.ndarray, np.ndarray]:
    X = np.asarray(P, float).reshape(-1, 3) @ np.asarray(R, float).T + np.asarray(t, float).reshape(3)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.c_[f * X[:, 0] / X[:, 2] + w / 2.0, f * X[:, 1] / X[:, 2] + h / 2.0], X[:, 2]


def _line_dists(R, t, f: float, w: int, h: int, lines: list[dict]) -> np.ndarray:
    """線ごとに、写真で指した 2 点から、3D の線を写した直線までの符号付きの距離（px）。(M, 2)。"""
    out = np.zeros((len(lines), 2))
    for k, ln in enumerate(lines):
        q, _ = _project(R, t, f, w, h, np.array([ln["X1"], ln["X2"]], float))
        d = q[1] - q[0]
        n = np.array([-d[1], d[0]]) / max(1e-9, float(np.hypot(*d)))
        out[k] = [(np.asarray(ln["x1"], float) - q[0]) @ n, (np.asarray(ln["x2"], float) - q[0]) @ n]
    return out


def _cand(R, t, f, w, h, P3, uv, lines, inl, line_inl, method, **extra) -> dict:
    """解の候補を solve の返り値の形にする。rms は使われた点の誤差と、使われた線の 2 点の距離から。"""
    R, t = np.asarray(R, float), np.asarray(t, float).reshape(3)
    proj, depth = _project(R, t, f, w, h, P3)
    err = np.linalg.norm(proj - uv, axis=1) if len(P3) else np.zeros(0)
    ld = np.abs(_line_dists(R, t, f, w, h, lines)) if lines else np.zeros((0, 2))
    res = np.r_[err[inl], ld[line_inl].ravel()] if (len(inl) or len(line_inl)) else np.array([np.inf])
    rms = float(np.sqrt(np.mean(res ** 2)))
    if len(P3) and np.any(depth[inl] <= 0):
        rms = float("inf")                                   # 点がカメラの後ろ
    return {"R": R, "t": t, "focal": float(f), "reproj_px": [round(float(e), 1) for e in err], "rms_px": round(rms, 2),
            "inliers": [int(i) for i in inl], "line_inliers": [int(i) for i in line_inl],
            "line_reproj_px": [round(float(v), 1) for v in ld.max(axis=1)] if len(ld) else [], "method": method, **extra}


def _poselib_once(P3, uv, w, h, f, lines) -> dict | None:
    import poselib

    cam = {"model": "SIMPLE_PINHOLE", "width": int(w), "height": int(h), "params": [float(f), w / 2.0, h / 2.0]}
    thr = MAX_REPROJ_FRAC * w
    ropt = {"max_reproj_error": thr, "max_epipolar_error": thr, "seed": 0}
    try:
        if lines:
            l1 = np.array([ln["x1"] for ln in lines], float); l2 = np.array([ln["x2"] for ln in lines], float)
            L1 = np.array([ln["X1"] for ln in lines], float); L2 = np.array([ln["X2"] for ln in lines], float)
            pose, info = poselib.estimate_absolute_pose_pnpl(uv.reshape(-1, 2), P3.reshape(-1, 3), l1, l2, L1, L2, cam, ropt, {})
            line_inl = [i for i, ok in enumerate(info.get("inliers_lines", [True] * len(lines))) if ok]
        else:
            pose, info = poselib.estimate_absolute_pose(uv, P3, cam, ropt, {})
            line_inl = []
    except Exception:                                        # 点が足りない・縮退
        return None
    inl = [i for i, ok in enumerate(info.get("inliers", [])) if ok]
    if len(inl) + len(line_inl) < 3:
        return None
    return _cand(pose.R, pose.t, f, w, h, P3, uv, lines, inl, line_inl, "poselib",
                 ransac={"iterations": int(info.get("iterations", 0)), "refinements": int(info.get("refinements", 0))})


def solve_4dof(P3, uv, w: int, h: int, f: float, roll: float, pitch: float, lines: list[dict] | None = None,
               yaw_step: float = 2.0, iters: int = 30) -> dict | None:
    """傾き・仰角（度、rotation_cv の向き）と焦点を固定し、方位と t（4 自由度）を解く。
    方位を yaw_step 刻みで回し、各方位で t を線形の最小二乗（点：u'(r3·X + t3) = r1·X + t1、線：l·(R X + t) = 0）で出し、
    再投影の二乗和が最小の方位から Gauss-Newton（数値微分、LM の減衰つき）で詰める。"""
    from .locate import rotation_cv

    P3 = np.asarray(P3, float).reshape(-1, 3)
    uv = np.asarray(uv, float).reshape(-1, 2)
    lines = lines or []
    cx, cy = w / 2.0, h / 2.0
    un, vn = (uv[:, 0] - cx) / f, (uv[:, 1] - cy) / f
    lnorm = []
    for ln in lines:
        a = np.array([(ln["x1"][0] - cx) / f, (ln["x1"][1] - cy) / f, 1.0])
        b = np.array([(ln["x2"][0] - cx) / f, (ln["x2"][1] - cy) / f, 1.0])
        l = np.cross(a, b)
        lnorm.append(l / max(1e-12, float(np.hypot(l[0], l[1]))))
    if len(P3) * 2 + len(lines) * 2 < 4:
        return None

    def lin_t(R):
        Y = P3 @ R.T
        A, rhs = [], []
        for k in range(len(P3)):
            A.append([1.0, 0.0, -un[k]]); rhs.append(un[k] * Y[k, 2] - Y[k, 0])
            A.append([0.0, 1.0, -vn[k]]); rhs.append(vn[k] * Y[k, 2] - Y[k, 1])
        for ln, l in zip(lines, lnorm):
            for X in (ln["X1"], ln["X2"]):
                A.append(list(l)); rhs.append(-float(l @ (R @ np.asarray(X, float))))
        t, *_ = np.linalg.lstsq(np.array(A), np.array(rhs), rcond=None)
        return t

    def resid(p):
        R = rotation_cv(math.degrees(p[0]), pitch, roll)
        t = p[1:]
        r = []
        if len(P3):
            q, d = _project(R, t, f, w, h, P3)
            if np.any(d <= 0.1):
                return None
            r.append((q - uv).ravel())
        if lines:
            r.append(_line_dists(R, t, f, w, h, lines).ravel())
        return np.concatenate(r)

    best = None
    for yaw in np.arange(0.0, 360.0, yaw_step):
        R = rotation_cv(float(yaw), pitch, roll)
        t = lin_t(R)
        p = np.r_[math.radians(yaw), t]
        r = resid(p)
        if r is None or not np.all(np.isfinite(r)):
            continue
        c = float(r @ r)
        if best is None or c < best[0]:
            best = (c, p)
    if best is None:
        return None
    c, p = best
    lam = 1e-3
    for _ in range(iters):
        r = resid(p)
        if r is None:
            break
        J = np.zeros((len(r), 4))
        for j, eps in enumerate((1e-5, 1e-4, 1e-4, 1e-4)):
            dp = np.zeros(4); dp[j] = eps
            r2 = resid(p + dp)
            if r2 is None:
                break
            J[:, j] = (r2 - r) / eps
        A = J.T @ J
        step = np.linalg.solve(A + lam * np.diag(np.diag(A) + 1e-12), -J.T @ r)
        r_new = resid(p + step)
        if r_new is not None and float(r_new @ r_new) < float(r @ r):
            p, lam = p + step, lam * 0.3
            if np.linalg.norm(step) < 1e-8:
                break
        else:
            lam *= 10
            if lam > 1e8:
                break
    R = rotation_cv(math.degrees(p[0]), pitch, roll)
    return _cand(R, p[1:], f, w, h, P3, uv, lines, list(range(len(P3))), list(range(len(lines))), "4dof",
                 dof=4, fixed={"roll": roll, "pitch": pitch, "focal": f})


def _angle_diff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _solve_poselib(P3, uv, w, h, focal0, search_focal, lines, prior) -> dict:
    """PoseLib で解く（prior があれば GeoCalib の焦点・傾き・仰角を使う）。返り値は solve と同じ形。"""
    from .locate import angles_of

    P3 = np.asarray(P3, np.float64).reshape(-1, 3)
    uv = np.asarray(uv, np.float64).reshape(-1, 2)
    n = len(P3) + len(lines)
    if prior and prior.get("focal"):
        focals = [focal0] + ([float(prior["focal"])] if abs(float(prior["focal"]) / focal0 - 1) > 0.01 else [])
    else:
        focals = list(focal0 * np.linspace(0.85, 1.15, 13)) if search_focal else [focal0]
    cands, n_rejected = [], 0
    for f in focals:
        sols = []
        if not (prior and n <= GC_FEW_POINTS):
            c = _poselib_once(P3, uv, w, h, f, lines)
            if c:
                sols.append(c)
        if prior:
            kept = []
            for c in sols:
                _, pitch, roll = angles_of(c["R"])
                c["gravity_diff_deg"] = {"roll": round(_angle_diff(roll, prior["roll"]), 1),
                                         "pitch": round(_angle_diff(pitch, prior["pitch"]), 1)}
                if max(c["gravity_diff_deg"].values()) <= GC_ANGLE_TOL_DEG:
                    kept.append(c)
                else:
                    n_rejected += 1
            sols = kept
            if not sols:
                c = solve_4dof(P3, uv, w, h, f, prior["roll"], prior["pitch"], lines)
                if c and n > GC_FEW_POINTS:
                    # 外れた対応を 1 回だけ外して解き直す（閾値は RANSAC と同じ）
                    thr = MAX_REPROJ_FRAC * w
                    keep = [i for i, e in enumerate(c["reproj_px"]) if e <= thr]
                    lkeep = [i for i, e in enumerate(c["line_reproj_px"]) if e <= thr]
                    if 3 <= len(keep) + len(lkeep) < n:
                        c2 = solve_4dof(P3[keep], uv[keep], w, h, f, prior["roll"], prior["pitch"], [lines[i] for i in lkeep])
                        if c2:
                            c = _cand(c2["R"], c2["t"], f, w, h, P3, uv, lines, keep, lkeep, "4dof",
                                      dof=4, fixed=c2["fixed"])
                if c:
                    sols.append(c)
        for c in sols:
            c["fac"] = float(f / focal0)
            cands.append(c)
    if not cands:
        return {}
    # 使われた対応の多い解を先に（4〜5 点では RANSAC が 3 点にぴたり合わせて 1 点を外した解の RMS が 0 になり、
    # 焦点の探索でそれが選ばれて位置が数 m 狂った。本部棟の 4 点で中央値 2.7 m → この並べ方で cv2 並み）
    nmax = max(len(c["inliers"]) + len(c.get("line_inliers", [])) for c in cands)
    cands = [c for c in cands if len(c["inliers"]) + len(c.get("line_inliers", [])) == nmax]
    best = min(cands, key=lambda c: c["rms_px"])
    base_l = [c for c in cands if abs(c["fac"] - 1.0) < 1e-9]
    base = min(base_l, key=lambda c: c["rms_px"]) if base_l else None
    if base and best["rms_px"] >= 0.8 * base["rms_px"]:
        best = base
    best = dict(best)
    best["method"] = ("poselib+geocalib" if prior else "poselib") + ("(4dof)" if best.get("dof") == 4 else "")
    best["n_rejected_by_gravity"] = n_rejected
    if prior:
        best["prior"] = {k: prior.get(k) for k in ("focal", "roll", "pitch")}
    return best


# GeoCalib（Apache 2.0、https://github.com/cvg/GeoCalib）の出す傾き・仰角を rotation_cv の向きに直す符号。
# 本部棟の登録済みの写真（台帳の R_cv を angles_of で読んだ値）と比べて決めた（docs/実験_登録の3条件_11-06.md）。
GC_ROLL_SIGN = 1.0
GC_PITCH_SIGN = 1.0
_GC: dict = {}


def geocalib_prior(rec: dict, building_id: str, out_dir: Path | None = None, use_cache: bool = True) -> dict | None:
    """写真 1 枚から GeoCalib で焦点（px）と重力の向き（傾き・仰角、度）を出す。
    結果は out/<棟>/landmarks/geocalib_<写真id>.json に保存し、2 回目からは読む。GeoCalib が無ければ None。"""
    cache = (out_dir or Path("out") / building_id / "landmarks") / f"geocalib_{rec['id']}.json"
    if use_cache and cache.exists():
        d = json.loads(cache.read_text(encoding="utf-8"))
        return _gc_doc_to_prior(d)
    try:
        import torch
        from geocalib import GeoCalib
    except ImportError:
        return None
    from .rectify import read_image

    img = read_image(R_.photo_path(rec))
    rgb = np.ascontiguousarray(img[:, :, ::-1]).astype(np.float32) / 255.0
    ten = torch.from_numpy(rgb).permute(2, 0, 1)
    res, dev = None, None
    for dev in (["cuda"] if torch.cuda.is_available() else []) + ["cpu"]:
        try:
            if _GC.get("device") != dev:
                release_geocalib()
                _GC["model"] = GeoCalib().to(dev)
                _GC["device"] = dev
            res = _GC["model"].calibrate(ten.to(dev))
            break
        except RuntimeError:                                 # GPU の容量切れ等 → CPU で
            release_geocalib()
            continue
    if res is None:
        return None
    cam, grav = res["camera"], res["gravity"]
    fxy = cam.f[0].detach().cpu().numpy().astype(float)
    rp = grav.rp[0].detach().cpu().numpy().astype(float)
    unc = {k: float(res[k][0].detach().cpu()) for k in ("roll_uncertainty", "pitch_uncertainty", "focal_uncertainty")
           if k in res and res[k] is not None}
    h, w = img.shape[:2]
    d = {"photo": rec["id"], "building": building_id, "image_size": [int(w), int(h)],
         "focal_px": round(float(fxy.mean()), 2), "raw_roll_deg": round(math.degrees(rp[0]), 3),
         "raw_pitch_deg": round(math.degrees(rp[1]), 3),
         "uncertainty": {"roll_deg": round(math.degrees(unc["roll_uncertainty"]), 3) if "roll_uncertainty" in unc else None,
                         "pitch_deg": round(math.degrees(unc["pitch_uncertainty"]), 3) if "pitch_uncertainty" in unc else None,
                         "focal_px": round(unc["focal_uncertainty"], 2) if "focal_uncertainty" in unc else None},
         "device": dev, "made_by": "recorder.landmarks.geocalib_prior（GeoCalib 1.0 pinhole、Apache 2.0）",
         "made_at": datetime.now().isoformat(timespec="seconds"),
         "note": "raw_* は GeoCalib の gravity.rp（度）そのまま。roll/pitch は GC_*_SIGN を掛けた rotation_cv の向き（読む時に計算）"}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return _gc_doc_to_prior(d)


def _gc_doc_to_prior(d: dict) -> dict:
    return {"focal": float(d["focal_px"]), "roll": GC_ROLL_SIGN * float(d["raw_roll_deg"]),
            "pitch": GC_PITCH_SIGN * float(d["raw_pitch_deg"]), "uncertainty": d.get("uncertainty"), "image_size": d.get("image_size")}


def release_geocalib() -> None:
    """GeoCalib の模型を解放する（GPU を他の係と共有しているため、使い終わったら呼ぶ）。"""
    m = _GC.pop("model", None)
    _GC.pop("device", None)
    if m is not None:
        del m
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass


def register_picks(building_json: Path, picks_doc: dict, index: dict | None = None, reg: dict | None = None,
                   registry_path: Path | None = None, write: bool = True, by: str = "mejirushi",
                   method: str = "cv2", prior: dict | None = None, b: dict | None = None) -> dict:
    """対応（picks_doc = {"photo", "face"?, "ground"?（カメラの高さの基準の地面。ground_reference）, "picks": [{"id", "cell", "sub"?} | {"id", "u", "v"} | {"id": "<面>-V01", "cells": ["C3", "C8"]}]}）
    から視点を解き、合否を出して台帳に書く。線の対応は線上の 2 升（"cells"。要素は "C3" か {"cell", "sub"}、または "subs": [..] を併記）。
    method は solve と同じ。"poselib+geocalib" で prior が無ければ geocalib_prior で出す（GeoCalib が無ければ "poselib" に落とす）。
    返り値 {"status": "ok"|"fail", "code", "reason", "camera", "check", "picks_px": [...]}。"""
    b = b or load_building(building_json)
    idx = index or load_index(INDEX_PATH)
    rec = R_.photo_record(picks_doc["photo"], idx)
    if rec is None:
        return {"status": "fail", "code": "no_photo", "reason": f"索引に無い: {picks_doc['photo']}"}
    w, h = int(rec["width"]), int(rec["height"])
    cat = {m["id"]: m for m in catalog(b, lines=True)}
    P3, uv, used, problems = [], [], [], []
    lines, used_lines = [], []
    for p in picks_doc.get("picks", []):
        m = cat.get(p.get("id"))
        if m is None:
            problems.append(f"目印 id が一覧に無い: {p.get('id')}")
            continue
        try:
            if m.get("kind") == "line":
                pts = _line_pick_px(p, w, h)
                lines.append({"X1": m["xyz1"], "X2": m["xyz2"], "x1": pts[0], "x2": pts[1]})
                used_lines.append({**p, "uv": [[round(x, 1) for x in q] for q in pts], "name": m["name"]})
                continue
            u, v, how = pick_to_px(p, w, h)
        except (ValueError, KeyError, TypeError) as ex:
            problems.append(f"{p.get('id')}: {ex}")
            continue
        P3.append(m["xyz"])
        uv.append((u, v))
        used.append({**p, "u": round(u, 1), "v": round(v, 1), "how": how, "name": m["name"]})
    if method == "cv2" and lines:
        problems.append(f"線の対応 {len(lines)} 本は cv2 では使わない（--method poselib で使う）")
        lines, used_lines = [], []
    n_corr = len(P3) + len(lines)
    if n_corr < MIN_POINTS:
        return _fail(rec, b, idx, reg, registry_path, write, "few_points", f"対応が {n_corr} 点（{MIN_POINTS} 点要る。線は 1 本を 1 点と数える）", problems)
    # 散らばり：同じ面で縦の線 2 本以上・横の線 2 本以上、写真の中で幅・高さの 25 % 以上（線は縦線なら s、横線なら z を数える）
    ss = {round(cat[k["id"]]["s"], 1) for k in used} | {round(cat[k["id"]]["s"], 1) for k in used_lines if "s" in cat[k["id"]]}
    zs = {round(cat[k["id"]]["z"], 1) for k in used} | {round(cat[k["id"]]["z"], 1) for k in used_lines if "z" in cat[k["id"]]}
    uu = np.array([k["u"] for k in used] + [q[0] for k in used_lines for q in k["uv"]])
    vv = np.array([k["v"] for k in used] + [q[1] for k in used_lines for q in k["uv"]])
    bw, bh = (uu.max() - uu.min()) / w, (vv.max() - vv.min()) / h
    if len(ss) < 2 or len(zs) < 2 or bw < MIN_BBOX_FRAC or bh < MIN_BBOX_FRAC:
        why = f"対応が偏っている（縦の線 {len(ss)} 本・横の線 {len(zs)} 本、写真の中の広がり 幅 {bw:.0%}・高さ {bh:.0%}。縦横 2 本以上・{MIN_BBOX_FRAC:.0%} 以上要る）"
        return _fail(rec, b, idx, reg, registry_path, write, "picks_degenerate", why, problems)
    focal0 = focal_px_from_exif(rec)
    if method == "poselib+geocalib" and prior is None:
        prior = geocalib_prior(rec, b["id"])
        if prior is None:
            problems.append("GeoCalib が無いので poselib だけで解いた")
            method = "poselib"
    sol = solve(np.array(P3).reshape(-1, 3), np.array(uv).reshape(-1, 2), w, h, focal0, method=method, lines=lines, prior=prior)
    if not sol:
        return _fail(rec, b, idx, reg, registry_path, write, "pnp_failed", "PnP が解けない（点が一直線か、対応の取り違え）", problems)
    for i, (k, e) in enumerate(zip(used, sol["reproj_px"])):
        k["reproj_px"] = e
        k["inlier"] = i in sol["inliers"]
    for i, k in enumerate(used_lines):
        k["reproj_px"] = sol.get("line_reproj_px", [None] * len(used_lines))[i]
        k["inlier"] = i in sol.get("line_inliers", [])
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
    hgt = float(C[2] - g)
    g_face, face_near = _face_ground(b, C)
    hgt_face = None if g_face is None else float(C[2] - g_face)
    gref = ground_reference(b, picks_doc.get("ground"), g, g_face, face_near, cat)
    if gref.get("problem"):
        problems.append(gref["problem"])
    if gref["mode"] == "auto":
        height_ok = (MIN_CAM_HEIGHT <= hgt <= MAX_CAM_HEIGHT) or (hgt_face is not None and MIN_CAM_HEIGHT <= hgt_face <= MAX_CAM_HEIGHT)
        g_use = g if MIN_CAM_HEIGHT <= hgt <= MAX_CAM_HEIGHT or g_face is None else g_face
    else:
        g_use = gref["z"]
        height_ok = MIN_CAM_HEIGHT <= float(C[2] - g_use) <= MAX_CAM_HEIGHT
    gref["camera_height_m"] = round(float(C[2] - g_use), 2)
    cam = R_.camera_dict(Rm, t, sol["focal"], w, h, g_use)
    n_inl = len(sol["inliers"]) + len(sol.get("line_inliers", []))
    check = {"n_points": len(P3), "n_lines": len(lines), "n_inliers": n_inl, "rms_px": sol["rms_px"], "solver": sol.get("method"),
             "rms_frac_w": round(sol["rms_px"] / w, 4), "focal_px": sol["focal"], "focal_vs_exif": round(sol["focal"] / focal0, 3),
             "camera_height_m": round(hgt, 2), "camera_height_face_m": None if hgt_face is None else round(hgt_face, 2),
             "ground_face": face_near, "ground_ref": gref, "warnings": []}
    if prior:
        check["gravity_prior"] = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in prior.items() if k in ("focal", "roll", "pitch")}
        check["gravity_diff_deg"] = sol.get("gravity_diff_deg")
        check["n_rejected_by_gravity"] = sol.get("n_rejected_by_gravity")
    # 検算（係の指し方に依らない独立の物差し）：消失点の仰角、模型の線が写真の縁に乗る割合、線に直角な勾配
    edge_ok = True
    pitch_bad = False
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
        pitch_bad = bool(tilt and abs(tilt["pitch"] - pitch) > PITCH_FAIL_DEG and tilt.get("resid_px", 1e9) < PITCH_FAIL_RESID)
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
    elif n_inl < MIN_INLIERS:
        status, code, reason = "fail", "few_inliers", f"使われた対応が {n_inl} 点（{MIN_INLIERS} 点要る。外れた点を直すか足す）"
    elif not height_ok:
        status, code = "fail", "camera_height"
        if gref["mode"] == "auto":
            reason = (f"カメラの高さが地面から {hgt:.1f} m（5m 標高）"
                      + (f"／{hgt_face:.1f} m（面 {face_near} の前の地面）" if hgt_face is not None else "")
                      + f"（{MIN_CAM_HEIGHT}〜{MAX_CAM_HEIGHT} m の外）")
        else:
            reason = (f"カメラの高さが指定の地面（{gref['label']}）から {gref['camera_height_m']:.1f} m"
                      f"（{MIN_CAM_HEIGHT}〜{MAX_CAM_HEIGHT} m の外）")
    elif pitch_bad:
        status, code = "fail", "pitch_check"
        reason = f"消失点の仰角 {check['tilt_from_vp']['pitch']}° と PnP の仰角 {check['pitch']}° が {PITCH_FAIL_DEG}° 以上違う（対応の取り違えか、点が一直線に近い）"
    elif not edge_ok:
        status, code = "fail", "edge_check"
        reason = (f"解いた視点で模型の線が写真の縁に乗らない（乗った割合 {check.get('edge_inlier')}、勾配 {check.get('edge_grad')}。"
                  f"{EDGE_MIN_INLIER} 以上か {EDGE_MIN_GRAD} 以上要る）。対応の取り違え（隣の柱型・別の窓の列・延長した点）を疑う")
    vis = R_.visible_faces(b, cam) if status == "ok" else []
    if status == "ok" and not vis:
        status, code, reason = "fail", "no_visible_face", "解いた視点から見える棟の面が無い"
    out = {"photo": rec["id"], "building": b["id"], "status": status, "code": code, "reason": reason,
           "camera": cam, "check": check, "picks_px": used, "lines_px": used_lines, "problems": problems, "visible_faces": vis}
    if write:
        reg = reg or R_.load_registry(registry_path or R_.REGISTRY_PATH)
        if status == "ok":
            entry = {"photo": rec["id"], "building": b["id"], "face": rec.get("face") or picks_doc.get("face") or "(pose)",
                     "kind": "pose", "period": rec.get("period"), "file": rec.get("file"), "image_size": [w, h],
                     "camera": cam, "visible_faces": vis,
                     "source": {"method": "landmarks：目印係が指した目印（面の角・柱型・部品の縁）の対応から PnP（"
                                          + _METHOD_TEXT.get(method, method) + "）",
                                "kind": "landmarks", "solver": sol.get("method"), "picks": used, "lines": used_lines,
                                "focal_source": ("GeoCalib と EXIF の 2 候補" if prior else "exif_35mm を初期値に探索")},
                     "check": check, "registered_at": datetime.now().isoformat(timespec="seconds"), "registered_by": by}
            R_.upsert(reg, entry)
        else:
            _drop_landmark_entry(reg, b["id"], rec["id"])
            R_.note_not_registered(reg, b["id"], rec["id"], idx, reason or "", str(code), "landmarks")
        R_.save_registry(reg, registry_path or R_.REGISTRY_PATH)
    return out


_METHOD_TEXT = {"cv2": "cv2.solvePnP、焦点は EXIF の 0.85〜1.15 倍を探索",
                "poselib": "PoseLib の RANSAC＋refine（点、線があれば点＋線）、焦点は EXIF の 0.85〜1.15 倍を探索",
                "poselib+geocalib": "PoseLib＋GeoCalib：焦点は GeoCalib と EXIF の 2 候補、傾き・仰角が GeoCalib と 10° 超違う解を落とす、対応 4 以下は 4 自由度"}


def _line_pick_px(p: dict, w: int, h: int) -> list[tuple[float, float]]:
    """線の対応 {"id", "cells": ["C3", "C8"] | [{"cell", "sub"}, ..], "subs"?: [..]} → 線上の 2 点（px）。"""
    cells = p.get("cells") or []
    if len(cells) != 2:
        raise ValueError(f"線は線上の 2 升（cells）を指す: {cells!r}")
    subs = p.get("subs") or [None, None]
    out = []
    for c, s in zip(cells, subs):
        q = dict(c) if isinstance(c, dict) else {"cell": c}
        if s and not q.get("sub"):
            q["sub"] = s
        u, v, _ = pick_to_px(q, w, h)
        out.append((u, v))
    if math.hypot(out[0][0] - out[1][0], out[0][1] - out[1][1]) < 1.0:
        raise ValueError("線の 2 升が同じ所")
    return out


def draw_check(b: dict, res: dict, out_jpg: Path, long_side: int = 1600) -> Path | None:
    """登録の重ね絵：写真に模型の面の線（緑の点）、指した対応（赤）、解いた視点で写した目印（水色）と線（水色の線）を描く。"""
    import cv2

    from .locate import model_points
    from .rectify import read_image

    cam = res.get("camera")
    if not cam:
        return None
    rec = R_.photo_record(res["photo"])
    img = read_image(R_.photo_path(rec))
    h, w = img.shape[:2]
    s = long_side / max(w, h)
    small = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    R, t, f = np.array(cam["R_cv"]), np.array(cam["t_cv"]), cam["focal_px"]
    C = -R.T @ t
    mp = model_points(b)
    facing = np.einsum("fk,fk->f", C[None, :2] - mp["p0"], mp["n"]) > 0.3
    q, d = _project(R, t, f, w, h, mp["P"])
    for (uu, vv), ok in zip(q * s, facing[mp["face"]] & (d > 0.5)):
        if ok and 0 <= uu < small.shape[1] and 0 <= vv < small.shape[0]:
            cv2.circle(small, (int(uu), int(vv)), 2, (60, 220, 60), -1)
    cat = {m["id"]: m for m in catalog(b, lines=True)}
    for k in res.get("picks_px", []):
        pu, pv = k["u"] * s, k["v"] * s
        (ru, rv), = _project(R, t, f, w, h, [cat[k["id"]]["xyz"]])[0] * s
        cv2.line(small, (int(pu), int(pv)), (int(ru), int(rv)), (0, 0, 255), 2)
        cv2.circle(small, (int(pu), int(pv)), 7, (0, 0, 255), 2)
        cv2.circle(small, (int(ru), int(rv)), 4, (255, 220, 0), -1)
        cv2.putText(small, k["id"], (int(pu) + 8, int(pv) - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1, cv2.LINE_AA)
    for k in res.get("lines_px", []):
        m = cat[k["id"]]
        q2, _ = _project(R, t, f, w, h, [m["xyz1"], m["xyz2"]])
        cv2.line(small, tuple(int(x) for x in q2[0] * s), tuple(int(x) for x in q2[1] * s), (255, 220, 0), 2)
        for a in k["uv"]:
            cv2.circle(small, (int(a[0] * s), int(a[1] * s)), 7, (0, 0, 255), 2)
    ck = res.get("check", {})
    gr = ck.get("ground_ref") or {}
    hs = f"h {ck.get('camera_height_m')}m" + (f" (ground {gr.get('spec')}: {gr.get('camera_height_m')}m)" if gr.get("mode") not in (None, "auto") else "")
    txt = f"{res['photo']} {res['status']} {res.get('code') or ''} {ck.get('solver')} rms {ck.get('rms_px')}px {hs} edge {ck.get('edge_inlier')}/{ck.get('edge_grad')}"
    cv2.putText(small, txt, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(small, txt, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1, cv2.LINE_AA)
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 88])[1].tofile(str(out_jpg))
    return out_jpg


GROUND_MODES = ("auto", "dem", "face", "plaza")


def ground_reference(b: dict, spec, g_dem: float, g_face: float | None, face_near: str | None,
                     cat: dict | None = None) -> dict:
    """カメラの高さの門（MIN_CAM_HEIGHT〜MAX_CAM_HEIGHT）の基準の地面を picks の "ground" で選ぶ（11/7）。
    spec：省略・"auto"＝従来（5m 標高か、近い面の前の地面のどちらかで門に入れば通す）／"dem"＝5m 標高だけ／
    "face" か "plaza"＝カメラに一番近い面の前の地面（faces[].ground_offset。沈んだ広場）だけ／"face:<面 id>"＝その面の ground_offset／
    "<面>-Gnn"＝地面の線の目印の高さ（G01 は面の足元、G02… は段・基壇の上端）。
    返り値 {"spec", "mode", "z"（棟の z=0 からの m。auto は None）, "label", "problem"?}。解けない指定は auto に落とし problem に書く。"""
    if spec in (None, "", "auto"):
        return {"spec": spec, "mode": "auto", "z": None, "label": "5m 標高か面の前の地面（従来）"}
    sp = str(spec).strip()
    if sp == "dem":
        return {"spec": sp, "mode": "dem", "z": round(float(g_dem), 3), "label": "5m 標高"}
    if sp in ("face", "plaza"):
        if g_face is None:
            return {"spec": sp, "mode": "auto", "z": None, "label": "5m 標高か面の前の地面（従来）",
                    "problem": f"ground={sp!r}：カメラの前 {FACE_GROUND_DIST:.0f} m 以内に面が無いので従来の基準で見た"}
        return {"spec": sp, "mode": "face", "z": round(float(g_face), 3), "label": f"面 {face_near} の前の地面（ground_offset）"}
    if sp.startswith("face:"):
        fid = sp.split(":", 1)[1].strip()
        f = next((f for f in b.get("faces", []) if f["id"] == fid), None)
        if f is None:
            return {"spec": sp, "mode": "auto", "z": None, "label": "5m 標高か面の前の地面（従来）",
                    "problem": f"ground={sp!r}：面 {fid} が無いので従来の基準で見た"}
        return {"spec": sp, "mode": "face", "z": round(face_ground_offset(f), 3), "label": f"面 {fid} の前の地面（ground_offset）"}
    if re.match(r"^.+-G\d{2}$", sp):
        cat = cat if cat is not None else {m["id"]: m for m in catalog(b, lines=True)}
        m = cat.get(sp)
        if m is None or not m.get("ground"):
            return {"spec": sp, "mode": "auto", "z": None, "label": "5m 標高か面の前の地面（従来）",
                    "problem": f"ground={sp!r}：地面の線の目印に無いので従来の基準で見た"}
        return {"spec": sp, "mode": "line", "z": round(float(m["z"]), 3), "label": f"{sp}（{m['name']}）"}
    return {"spec": sp, "mode": "auto", "z": None, "label": "5m 標高か面の前の地面（従来）",
            "problem": f"ground={sp!r} は分からない（auto / dem / face / plaza / face:<面> / <面>-G01 のどれか）。従来の基準で見た"}


def _face_ground(b: dict, C) -> tuple[float | None, str | None]:
    """カメラに一番近い面（法線の側、FACE_GROUND_DIST 以内、面の両端 ±10 m）の前の地面の高さと面 id。無ければ (None, None)。"""
    best = None
    for f in b.get("faces", []):
        if not (f.get("from") and f.get("to")):
            continue
        fr = face_rect_frame(b, f)
        p0, u, nv, L = face_frame(fr)
        q = np.array(C[:2], float) - p0
        d, sv = float(q @ nv), float(q @ u)
        if 0.3 <= d <= FACE_GROUND_DIST and -10.0 <= sv <= L + 10.0 and (best is None or d < best[0]):
            best = (d, fr["ground_offset"], f["id"])
    return (best[1], best[2]) if best else (None, None)


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
    lns = [m for m in catalog(b, lines=True) if m.get("kind") == "line"]
    (out / "landmarks.json").write_text(json.dumps({"building": b["id"], "landmarks": cat, "lines": lns}, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    faces = sorted({m["face"] for m in cat})
    for fid in faces:
        draw_face(b, fid, [m for m in cat if m["face"] == fid], out / f"{fid}.png",
                  line_marks=[m for m in lns if m["face"] == fid])
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
