"""仕分けの正解率の試験：棟と面を隠した写真（X01.jpg…）と、足元の図・課題・正解・GPS と方位だけの当て方を作る。
使い方（リポジトリの直下で）: python blind_prep.py <出力フォルダ>"""
import json
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageOps

sys.path.insert(0, ".")
from recorder import geo, skeleton  # noqa: E402

OUT = Path(sys.argv[1])
(OUT / "img").mkdir(parents=True, exist_ok=True)
random.seed(7)

idx = {p["id"]: p for p in json.load(open("data/photos/index.json", encoding="utf-8"))["photos"]}
reg = json.load(open("data/photos/registry.json", encoding="utf-8"))["entries"]

# 面（足元の辺）：本部棟・タワー75・図書館は今の棟 JSON、共通講義棟南は敷地からの土台
faces = {}
for b in ("honbu", "tower75", "library"):
    faces[b] = [(f["id"], f["from"], f["to"]) for f in json.load(open(f"data/buildings/{b}.json", encoding="utf-8"))["faces"]]
faces["kyotsu-minami"] = [(f["id"], f["from"], f["to"]) for f in skeleton.from_site_cli("kyotsu-minami")["faces"]]
MAIN = {"honbu": {"SW", "E", "NE", "NE2", "NW_n", "NW_s", "SE", "N_jog1", "N_jog2"},
        "tower75": {f[0] for f in faces["tower75"]}}

# 正解のある写真
truth = {}
for e in reg:                                          # 視点を解いて登録した写真：棟と面（主な面だけ面の正解に使う）
    truth[e["photo"]] = {"building": e["building"],
                         "face": e["face"] if e["face"] in MAIN.get(e["building"], set()) else None,
                         "kind": "registered"}
staff = [p for p in idx.values() if p.get("assigned_by") == "staff" and not p.get("interior") and p["id"] not in truth]
km = [p for p in staff if p["building"] == "kyotsu-minami"]
tw = [p for p in staff if p["building"] == "tower75"]
for p in random.sample(km, 12) + random.sample(tw, 10):     # 職員の振り分け：棟だけ
    truth[p["id"]] = {"building": p["building"], "face": None, "kind": "staff"}

ids = list(truth)
random.shuffle(ids)
DECL = -7.4    # 名古屋の磁気の偏角（西に約 7.4°）。真北の方位 = 磁北の方位 + DECL


def cam_xy(p):
    g = p.get("gps") or {}
    lat, lon = g.get("lat"), g.get("lon")
    if lat is None or lon is None:
        return None
    x, y = geo.lonlat_to_xy(lon, lat)
    return round(x, 1), round(y, 1)


tasks, answer, base = [], {}, {}
for i, pid in enumerate(ids, 1):
    p = idx[pid]
    xid = f"X{i:02d}"
    im = ImageOps.exif_transpose(Image.open(Path("data/photos") / p["file"])).convert("RGB")
    im.thumbnail((1600, 1600))
    im.save(OUT / "img" / f"{xid}.jpg", quality=85)            # EXIF を付けずに保存（ファイル名にも棟を出さない）
    cands = sorted(p.get("candidates") or [], key=lambda c: c.get("distance_m", 1e9))[:3]
    xy = cam_xy(p)
    tasks.append({"id": xid, "image": f"img/{xid}.jpg", "camera_xy_m": xy,
                  "heading_deg_magnetic": p.get("heading_deg"),
                  "gps_candidates": [{"building": c.get("building"), "distance_m": round(c.get("distance_m", 0), 1)} for c in cands],
                  "taken_at": p.get("taken_at")})
    answer[xid] = {"photo": pid, **truth[pid]}

    # 比べる相手 1：GPS だけ（いちばん近い候補の棟）
    gb = cands[0]["building"] if cands else None
    # 比べる相手 2：方位だけで面（正解の棟の面のうち、向いている方向の線が最初に当たる面。当たらなければ向き合う面）
    gf = None
    t = truth[pid]
    if t["face"] and xy and p.get("heading_deg") is not None:
        h = math.radians((p["heading_deg"] + DECL) % 360)
        dx, dy = math.sin(h), math.cos(h)
        best = None
        for fid, a, b2 in faces[t["building"]]:
            ex, ey = b2[0] - a[0], b2[1] - a[1]
            den = dx * ey - dy * ex
            if abs(den) < 1e-9:
                continue
            tt = ((a[0] - xy[0]) * ey - (a[1] - xy[1]) * ex) / den
            u = ((a[0] - xy[0]) * dy - (a[1] - xy[1]) * dx) / den
            if tt > 0 and 0 <= u <= 1 and (best is None or tt < best[0]):
                best = (tt, fid)
        if best:
            gf = best[1]
        else:
            sc = []
            for fid, a, b2 in faces[t["building"]]:
                L = math.hypot(b2[0] - a[0], b2[1] - a[1]) or 1
                nx, ny = (b2[1] - a[1]) / L, -(b2[0] - a[0]) / L
                sc.append((-(nx * dx + ny * dy), fid))
            gf = max(sc)[1]
    base[xid] = {"gps_building": gb, "heading_face": gf}

json.dump({"photos": tasks}, open(OUT / "tasks.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
json.dump(answer, open(OUT / "answer_DO_NOT_SHOW.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
json.dump(base, open(OUT / "baseline.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

# 足元の図：4 棟まとめて 1 枚（棟の位置関係）＋棟ごとに面の番号と外向きの矢印
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
plt.rcParams["font.family"] = ["Yu Gothic", "Meiryo", "MS Gothic", "sans-serif"]


def draw(ax, b, label_faces=True):
    for fid, a, b2 in faces[b]:
        ax.plot([a[0], b2[0]], [a[1], b2[1]], "k-", lw=1.5)
        if label_faces:
            mx, my = (a[0] + b2[0]) / 2, (a[1] + b2[1]) / 2
            L = math.hypot(b2[0] - a[0], b2[1] - a[1]) or 1
            nx, ny = (b2[1] - a[1]) / L, -(b2[0] - a[0]) / L
            ax.annotate("", xy=(mx + nx * 4, my + ny * 4), xytext=(mx, my), arrowprops=dict(arrowstyle="->", color="tab:red"))
            ax.text(mx + nx * 7, my + ny * 7, fid, color="tab:red", fontsize=8, ha="center", va="center")


fig, ax = plt.subplots(figsize=(9, 9))
for b in faces:
    draw(ax, b, label_faces=False)
    xs = [a[0] for _, a, _ in faces[b]]
    ys = [a[1] for _, a, _ in faces[b]]
    ax.text(sum(xs) / len(xs), sum(ys) / len(ys), b, fontsize=11, ha="center", color="tab:blue")
for t in tasks:
    if t["camera_xy_m"]:
        ax.plot(*t["camera_xy_m"], ".", color="gray", ms=3)
ax.set_aspect("equal"); ax.grid(alpha=0.3); ax.set_title("4 棟の足元（x=東, y=北, m）。灰色の点＝試験の写真の GPS の位置")
fig.savefig(OUT / "plan_all.png", dpi=110, bbox_inches="tight"); plt.close(fig)
for b in faces:
    fig, ax = plt.subplots(figsize=(8, 8))
    draw(ax, b)
    ax.set_aspect("equal"); ax.grid(alpha=0.3); ax.set_title(f"{b} の足元の面（赤い矢印＝面の外向き。上が北、m）")
    fig.savefig(OUT / f"plan_{b}.png", dpi=110, bbox_inches="tight"); plt.close(fig)

print(json.dumps({"n": len(ids), "registered": sum(1 for v in answer.values() if v["kind"] == "registered"),
                  "face_truth": sum(1 for v in answer.values() if v["face"]),
                  "by_building": {b: sum(1 for v in answer.values() if v["building"] == b) for b in faces}}, ensure_ascii=False))
