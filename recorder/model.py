"""棟 JSON → Blender で模型（描画・glb）→ 四面図の一枚物（sheet.png）。

Blender は `BLENDER` 環境変数のパス（5.2）で起動する。中で動くのは blender_build.py。
ここは外側：起動、出来上がりの確認、四面図の板組み。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VIEWS = [("view_S", "南立面"), ("view_E", "東立面"), ("view_N", "北立面"), ("view_W", "西立面")]


def blender_path(explicit: str | None = None) -> Path:
    p = explicit or os.environ.get("BLENDER")
    if not p:
        raise SystemExit("Blender 5.2 の場所が分かりません。環境変数 BLENDER にパスを入れるか --blender で指定してください")
    p = Path(p)
    if not p.exists():
        raise SystemExit(f"Blender がありません: {p}")
    return p


def build(building_json: Path, out_dir: Path | None = None, blender: str | None = None, fast: bool = False,
          cameras: list[Path] | None = None) -> Path:
    b = json.loads(building_json.read_text(encoding="utf-8"))
    out = out_dir or Path("out") / b["id"]
    out.mkdir(parents=True, exist_ok=True)
    cmd = [str(blender_path(blender)), "-b", "-P", str(HERE / "blender_build.py"), "--", str(building_json.resolve()), str(out.resolve())]
    if fast:
        cmd.append("--fast")
    if cameras:
        lst = out / "cameras.txt"
        lst.write_text(chr(10).join(str(Path(c).resolve()) for c in cameras), encoding="utf-8")
        cmd.append(f"--cameras={lst.resolve()}")
    from .ledger import lock   # GPU は 1 枚。Blender・headless の Edge は 1 つずつ（4 棟を同時に回すため。ledger.py）
    with lock("gpu", label=f"model build {b['id']}{' --fast' if fast else ''}"):
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = [ln for ln in r.stdout.splitlines() if "Traceback" in ln or "Error" in ln or "模型" in ln or ln.startswith("  File")]
    if r.returncode != 0 or "Traceback" in r.stdout or not (out / "persp_shaded.png").exists():
        print(r.stdout[-3000:], file=sys.stderr)
        raise SystemExit(f"Blender が失敗しました（終了コード {r.returncode}）")
    for ln in tail:
        if "strokes set empty" not in ln:
            print("  " + ln)
    return out


def _autocrop(im, margin: int = 40, thr: int = 245):
    """白い余白を落とす（線画の描画は余白が広い）。"""
    from PIL import ImageChops, Image

    g = im.convert("L").point(lambda v: 0 if v > thr else 255)
    bbox = g.getbbox()
    if not bbox:
        return im
    x0, y0, x1, y1 = bbox
    return im.crop((max(0, x0 - margin), max(0, y0 - margin), min(im.width, x1 + margin), min(im.height, y1 + margin)))


def sheet(out_dir: Path, b: dict) -> Path:
    """四面図＋屋根伏せ＋鳥瞰を 1 枚に。A3 横の比率。"""
    from PIL import Image, ImageDraw, ImageFont

    W, H = 3508, 2480
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/meiryo.ttc", 44)
        small = ImageFont.truetype("C:/Windows/Fonts/meiryo.ttc", 30)
        big = ImageFont.truetype("C:/Windows/Fonts/meiryob.ttc", 72)
    except OSError:
        font = small = big = ImageFont.load_default()

    def place(name: str, box: tuple[int, int, int, int], title: str):
        p = out_dir / f"{name}.png"
        if not p.exists():
            return
        x0, y0, x1, y1 = box
        pic = _autocrop(Image.open(p).convert("RGB"))
        pic.thumbnail((x1 - x0 - 20, y1 - y0 - 70))
        im.paste(pic, (x0 + (x1 - x0 - pic.width) // 2, y0 + 60 + (y1 - y0 - 70 - pic.height) // 2))
        d.rectangle(box, outline="#444", width=3)
        d.text((x0 + 16, y0 + 8), title, fill="#222", font=font)

    margin = 60
    d.text((margin, 30), f"{b['name']}　四面図・屋根伏せ・鳥瞰（棟 JSON から自動生成）", fill="#111", font=big)
    top = 140
    colw = (W - 2 * margin) // 4
    rowh = 820
    vj = out_dir / "views.json"
    views = [(v["file"][:-4], v["label"]) for v in json.loads(vj.read_text(encoding="utf-8"))] if vj.exists() else VIEWS
    for i, (name, title) in enumerate(views):
        place(name, (margin + i * colw, top, margin + (i + 1) * colw - 10, top + rowh), title)
    y2 = top + rowh + 30
    place("view_top", (margin, y2, margin + colw * 2 - 10, H - 160), "屋根伏せ（真上）")
    place("persp_sketch", (margin + colw * 2, y2, W - margin, H - 160), "鳥瞰（南西の上空）")
    # 脚注：根拠の要約と仮の値の数
    rep = out_dir / "model_report.json"
    n_assumed = len(json.loads(rep.read_text(encoding="utf-8")).get("assumed", [])) if rep.exists() else "?"
    ev = sorted({e for f in b.get("faces", []) for k in ("length",) for e in (f.get(k) or {}).get("evidence", [])})
    photos = sorted({e for f in b.get("faces", []) for e in ((f.get("bays") or {}).get("count") or {}).get("evidence", [])})
    d.text((margin, H - 120), f"縮尺の根拠: {', '.join(ev)[:120]}　／　寸法の根拠写真: {', '.join(photos)}　／　模型のための仮の値: {n_assumed} 件（model_report.json）",
           fill="#444", font=small)
    d.text((margin, H - 70), "実線＝JSON の値（measured / estimated）。仮の値の部分は模型を立てるための置き値で、棟 JSON には書いていない。", fill="#444", font=small)
    p = out_dir / "sheet.png"
    im.save(p, quality=92)
    return p
