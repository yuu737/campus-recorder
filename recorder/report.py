"""進捗報告：棟ごとに 1 ページの HTML（out/<棟>/report.html）。手順書「進捗報告の見せ方」の 6 段をそのまま並べる。

  1. 写真（職員のスマホ写真と撮影係の返事）      索引 data/photos/index.json、out/<棟>/satsuei_checks.json
  2. 立面図（根拠の色付き）                      out/<棟>/elev_<面>.svg（draw の生成物をそのまま埋める）
  3. 証拠（写真がどこに立ち、目印がどう対応したか） 台帳 registry.json、out/<棟>/evidence_*.png
  4. 模型（四面図・街路視点・検収の重ね）        out/<棟>/view_*.png、persp_street*.png、overlay_*.png、model_report.json
  5. 地形に載せて歩く                            web/data/site.json、out/web_*.png
  6. まだ無い物（灰色の面・撮影依頼・未確定の値） 棟 JSON の requests、schema.unknowns、coverage.json

絵は全部 生成物。手で直した絵は使わない。数字は棟 JSON と台帳から数える（この道具は値を作らない）。
見た目の基準：デジタル庁デザインシステム（Noto Sans JP、本文 17 px・行間 1.7、文字の灰色は白に対し 4.5:1 以上）、
工程の縦の並びは Vercel のデプロイ画面、図＋短い説明の組み方は Distill、写真の帯＋3D は Luma の capture ページ。
根拠の 3 色は立面図（drawing.COL）と同じ：黒 measured・琥珀 estimated・赤 unknown。
"""
from __future__ import annotations

import argparse
import html
import json
import re
import statistics
from datetime import datetime
from pathlib import Path

from . import registry as R_
from .drawing import COL as BASIS_COL
from .photos import INDEX_PATH, load_index
from .schema import evidence_summary, load_building, unknowns

OUT_ROOT = Path("out")
WEB_SITE = Path("web/data/site.json")
THUMB_LONG = 480
STATUS_JA = {"measured": "実測", "estimated": "推定", "unknown": "不明"}
METHOD_JA = {"landmarks": "目印の対応", "sfm": "一括復元", "chain": "鎖", "rectify": "正面化"}
STAGES = [("photos", "写真", "職員のスマホ写真と、撮影係の返事"),
          ("elev", "立面図", "寸法に根拠の色が付いた図面"),
          ("evidence", "証拠", "写真がどこに立ち、目印がどう対応したか"),
          ("model", "模型", "図面から組んだ模型と、写真との重ね"),
          ("walk", "歩ける 3D", "地形に載せ、受験生が歩く画面"),
          ("rest", "まだ無い物", "灰色のまま残っている面と、撮影依頼")]


# --- 数える --------------------------------------------------------------------------------

def _fmt_time(s: str | None) -> str:
    if not s:
        return "—"
    try:
        return datetime.fromisoformat(s).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return s


def quick_reply(rec: dict) -> dict:
    """撮影係の返事の下書き（道具の自動判定。係の判断の代わりではない）。索引の checks から 使える／問題 を出す。"""
    c = rec.get("checks") or {}
    problems = []
    if not c.get("resolution_ok", True):
        problems.append("解像度が足りない")
    if c.get("blur_suspected"):
        problems.append("ぶれの疑い")
    if not c.get("has_gps", True):
        problems.append("GPS 無し（棟は前後の写真から）")
    for side, ja in (("left", "左"), ("right", "右"), ("top", "上")):
        if (c.get(f"edge_touch_{side}") or 0) > 0.5:
            problems.append(f"{ja}の端が切れている")
    return {"usable": not any(p in ("解像度が足りない", "ぶれの疑い") for p in problems), "problems": problems}


def _street_cam(mr: dict) -> str | None:
    """model_report の web_cameras（{"street": {"query": "cam=…"}}）から歩く画面の構図の引数を取る。"""
    wc = (mr or {}).get("web_cameras") or {}
    cams = list(wc.values()) if isinstance(wc, dict) else list(wc)
    for c in cams:
        if isinstance(c, dict) and c.get("query"):
            return str(c["query"]).replace("cam=", "", 1)
    return None


def gather(b: dict, index: dict, reg: dict, out_dir: Path, site: dict | None) -> dict:
    bid = b["id"]
    recs = [r for r in index.get("photos", []) if r.get("building") == bid]
    by_id = {r["id"]: r for r in recs}
    entries = [e for e in reg.get("entries", []) if e.get("building") == bid and e.get("camera")]
    held = [e for e in reg.get("not_registered", []) if e.get("building") == bid and e.get("landmarks_code") == "visual_reject"]

    # 1. 写真
    satsuei_path = out_dir / "satsuei_checks.json"
    satsuei = json.loads(satsuei_path.read_text(encoding="utf-8")) if satsuei_path.exists() else None
    faces_of_photos: dict[str, int] = {}
    for r in recs:
        faces_of_photos[r.get("face") or "（面 未定）"] = faces_of_photos.get(r.get("face") or "（面 未定）", 0) + 1
    photos = {"total": len(recs), "during": sum(r.get("period") == "during" for r in recs),
              "before": sum(r.get("period") == "before" for r in recs),
              "faces": sorted(faces_of_photos.items(), key=lambda kv: -kv[1]),
              "first": _fmt_time(min((r.get("taken_at") for r in recs if r.get("taken_at")), default=None)),
              "last": _fmt_time(max((r.get("taken_at") for r in recs if r.get("taken_at")), default=None)),
              "satsuei": satsuei, "replies": []}
    sample = sorted(recs, key=lambda r: r.get("taken_at") or "", reverse=True)[:3]
    for r in sample:
        q = (satsuei or {}).get(r["id"]) if isinstance(satsuei, dict) else None
        photos["replies"].append({"id": r["id"], "taken_at": _fmt_time(r.get("taken_at")), "face": r.get("face"),
                                  "reply": q or quick_reply(r), "from_agent": q is not None})

    # 2. 立面図
    es = evidence_summary(b)
    elevs = []
    for f in b.get("faces", []):
        p = out_dir / f"elev_{f['id']}.svg"
        st = es["faces"].get(f["id"]) or {}
        elevs.append({"face": f["id"], "name": f.get("name") or f["id"], "svg": p if p.exists() else None,
                      "state": st.get("state", "unknown"), "values": st.get("values", 0), "measured": st.get("measured", 0)})
    order = {"measured": 0, "estimated": 1, "hypothesis": 2, "unknown": 3}
    elevs.sort(key=lambda d: (d["svg"] is None, order.get(d["state"], 9), -d["measured"]))

    # 3. 証拠
    from .evidence import plan, status_counts
    pl = plan(b, entries, index)
    counts = status_counts(b)
    per_face = {}
    for p in pl["photos"]:
        fid = p["face"] or "—"
        d = per_face.setdefault(fid, {"photos": 0, "resid": [], "methods": {}})
        d["photos"] += 1
        d["methods"][p["method"]] = d["methods"].get(p["method"], 0) + 1
        d["resid"] += [q["resid"] for q in p["picks"] if q["inlier"] and q["resid"] is not None]
    face_rows = []
    for f in b.get("faces", []):
        c = counts["faces"].get(f["id"], {})
        d = per_face.get(f["id"], {"photos": 0, "resid": [], "methods": {}})
        face_rows.append({"face": f["id"], "name": f.get("name") or f["id"], "measured": c.get("measured", 0),
                          "estimated": c.get("estimated", 0), "unknown": c.get("unknown", 0), "photos": d["photos"],
                          "resid": round(statistics.median(d["resid"]), 1) if d["resid"] else None})
    all_res = [q["resid"] for p in pl["photos"] for q in p["picks"] if q["inlier"] and q["resid"] is not None]
    methods = {}
    for p in pl["photos"]:
        methods[p["method"]] = methods.get(p["method"], 0) + 1
    evidence = {"registered": len(pl["photos"]), "methods": methods, "picks": len(all_res),
                "resid_median": round(statistics.median(all_res), 1) if all_res else None,
                "held": len(held), "total": counts["total"], "faces": face_rows,
                "images": [p for p in (out_dir / "evidence_three.png", out_dir / "evidence_photo.png") if p.exists()],
                "rrd": out_dir / "evidence.rrd" if (out_dir / "evidence.rrd").exists() else None,
                "photos": [{"id": p["photo"], "face": p["face"], "method": p["method"], "period": p["period"],
                            "time": p["time"].strftime("%m-%d %H:%M") if p["time"] else "—",
                            "n": len([q for q in p["picks"] if q["inlier"]]), "rms": p["rms_px"],
                            "file": p["file"]} for p in pl["photos"]]}

    # 4. 模型
    views_path = out_dir / "views.json"
    views = json.loads(views_path.read_text(encoding="utf-8")) if views_path.exists() else []
    views = [v for v in views if (out_dir / v["file"]).exists()]
    streets = sorted(out_dir.glob("persp_street*.png"))
    overlays = sorted(out_dir.glob("overlay_v2_*.png")) or sorted(p for p in out_dir.glob("overlay_*.png")
                                                                   if "_before" not in p.name and "edges" not in p.name)
    mr_path = out_dir / "model_report.json"
    mr = json.loads(mr_path.read_text(encoding="utf-8")) if mr_path.exists() else {}
    assumed = []
    seen = set()
    for a in mr.get("assumed", []):
        key = (a.get("what"), a.get("value"))
        if key not in seen:
            seen.add(key)
            assumed.append(a)
    model = {"glb": out_dir / f"{bid}.glb" if (out_dir / f"{bid}.glb").exists() else None, "views": views,
             "streets": streets[:2], "overlays": overlays[:3], "height_m": mr.get("height_m"), "assumed": assumed,
             "warnings": mr.get("warnings") or []}

    # 5. 歩ける 3D
    walk_img = None
    for cand in (OUT_ROOT / f"web_look_after_{bid}_pc.png", OUT_ROOT / f"web_check_{bid}.jpg",
                 OUT_ROOT / f"web_check_{bid}_street.png"):
        if cand.exists():
            walk_img = cand
            break
    models = (site or {}).get("models") or {}
    boxes = (site or {}).get("boxes") or []
    walk = {"image": walk_img, "on_site": bid in models, "colored": len(models), "gray": len(boxes) if isinstance(boxes, list) else 0,
            "cam": _street_cam(mr)}

    # 6. まだ無い物
    reqs = [r for r in b.get("requests", []) if (r.get("status") or "open") == "open"]
    cov_path = out_dir / "coverage.json"
    cov = json.loads(cov_path.read_text(encoding="utf-8")) if cov_path.exists() else None
    cov_rows = []
    if cov:
        for f in cov.get("faces", []):
            frame = sum(p.get("frame_m2", 0) for p in f.get("photos", []))
            used = sum(p.get("used_m2", 0) for p in f.get("photos", []))
            cov_rows.append({"face": f["face"], "photos": len(f.get("photos", [])), "used_m2": round(used),
                             "ratio": round(used / frame, 2) if frame else None})
    rest = {"requests": reqs, "unknowns": unknowns(b), "coverage": cov_rows,
            "gray_faces": [e["face"] for e in elevs if e["state"] == "unknown"], "summary": es}

    return {"id": bid, "name": b.get("name") or bid, "campus": b.get("campus"), "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "photos": photos, "elev": elevs, "evidence": evidence, "model": model, "walk": walk, "rest": rest,
            "summary": es, "history_n": len(b.get("history", []))}


def stage_status(d: dict) -> dict[str, tuple[str, str]]:
    """段ごとの状態（done／partial／todo）と 1 行の結果。"""
    s = {}
    p = d["photos"]
    s["photos"] = ("done" if p["total"] else "todo", f"{p['total']} 枚（期間中 {p['during']}・期間前 {p['before']}）")
    e = d["elev"]
    n_svg = sum(x["svg"] is not None for x in e)
    n_m = sum(x["state"] == "measured" for x in e)
    s["elev"] = ("done" if n_svg and n_m else "partial" if n_svg else "todo",
                 f"{n_svg} 面の図面。実測で形が決まった面 {n_m}、推定 {sum(x['state'] in ('estimated', 'hypothesis') for x in e)}、不明 {sum(x['state'] == 'unknown' for x in e)}")
    v = d["evidence"]
    s["evidence"] = ("done" if v["registered"] >= 3 else "partial" if v["registered"] else "todo",
                     f"登録済みの写真 {v['registered']} 枚、目印の対応 {v['picks']} 点"
                     + (f"、再投影の中央値 {v['resid_median']} px" if v["resid_median"] is not None else ""))
    m = d["model"]
    s["model"] = ("done" if m["glb"] and m["overlays"] else "partial" if m["glb"] else "todo",
                  (f"高さ {m['height_m']} m、" if m.get("height_m") else "") + f"仮置き {len(m['assumed'])} 件、検収の重ね {len(m['overlays'])} 枚")
    w = d["walk"]
    s["walk"] = ("done" if w["on_site"] else "todo", f"色付きの棟 {w['colored']}、灰色の箱（PLATEAU）{w['gray']}")
    r = d["rest"]
    s["rest"] = ("partial" if r["requests"] or r["unknowns"] else "done",
                 f"撮影依頼 {len(r['requests'])} 件、未確定の値 {len(r['unknowns'])}、灰色の面 {len(r['gray_faces'])}")
    return s


# --- 絵 ---------------------------------------------------------------------------------

def thumbnail(src: Path, dst: Path, long_side: int = THUMB_LONG) -> Path | None:
    """写真を縮めて out/<棟>/report/ に置く（元の写真は写さない・git 管理外）。"""
    import cv2
    import numpy as np

    if dst.exists():
        return dst
    buf = np.fromfile(str(src), dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    sc = long_side / max(h, w)
    if sc < 1:
        img = cv2.resize(img, (round(w * sc), round(h * sc)), interpolation=cv2.INTER_AREA)
    dst.parent.mkdir(parents=True, exist_ok=True)
    ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if ok:
        enc.tofile(str(dst))
        return dst
    return None


def inline_svg(path: Path) -> str:
    """draw の SVG をそのまま埋める（幅はページに合わせ、xml 宣言と固定の width/height を外す）。"""
    t = path.read_text(encoding="utf-8")
    t = re.sub(r"<\?xml[^>]*\?>", "", t)
    t = re.sub(r"<svg([^>]*)\swidth=\"[^\"]*\"", r"<svg\1", t, count=1)
    t = re.sub(r"<svg([^>]*)\sheight=\"[^\"]*\"", r"<svg\1", t, count=1)
    return t.strip()


def rel(p: Path | None, base: Path) -> str | None:
    if p is None:
        return None
    try:
        return Path(p).resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return "../../" + Path(p).as_posix()


# --- HTML --------------------------------------------------------------------------------

CSS = """
:root{--ink:#1a1a1a;--ink2:#595959;--ink3:#767676;--line:#d9d9d9;--line2:#b3b3b3;--bg:#fff;--bg2:#f5f5f7;--accent:#0017c1;
--measured:%(measured)s;--estimated:%(estimated)s;--unknown:%(unknown)s;--done:#1f7a3f;--partial:#b07a10;--todo:#8c8c8c;}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%%}
body{margin:0;background:var(--bg);color:var(--ink);font-family:"Noto Sans JP","Noto Sans","Yu Gothic UI","Hiragino Sans",sans-serif;
font-size:17px;line-height:1.7;font-feature-settings:"palt"}
a{color:var(--accent)}img,svg{max-width:100%%;height:auto;display:block}
.wrap{max-width:1180px;margin:0 auto;padding:0 24px}
header.top{border-bottom:1px solid var(--line);padding:28px 0 20px}
header.top .kicker{color:var(--ink3);font-size:14px;letter-spacing:.04em}
header.top h1{font-size:34px;line-height:1.3;margin:4px 0 8px;font-weight:700}
header.top .meta{color:var(--ink2);font-size:14px}
.chips{display:flex;flex-wrap:wrap;gap:8px 14px;margin-top:14px}
.chip{border:1px solid var(--line);border-radius:6px;padding:4px 10px;font-size:14px;background:var(--bg)}
.chip b{font-size:17px;margin-left:4px}
.chip.m b{color:var(--measured)}.chip.e b{color:var(--estimated)}.chip.u b{color:var(--unknown)}
.layout{display:grid;grid-template-columns:260px minmax(0,1fr);gap:40px;padding:28px 0 80px}
nav.rail{position:sticky;top:16px;align-self:start}
nav.rail ol{list-style:none;margin:0;padding:0;border-left:2px solid var(--line)}
nav.rail li{position:relative;padding:6px 0 14px 22px}
nav.rail li::before{content:"";position:absolute;left:-7px;top:14px;width:12px;height:12px;border-radius:50%%;background:var(--todo);border:2px solid #fff;box-shadow:0 0 0 1px var(--line2)}
nav.rail li.done::before{background:var(--done)}nav.rail li.partial::before{background:var(--partial)}
nav.rail a{color:var(--ink);text-decoration:none;font-weight:700;font-size:15px}
nav.rail .r{display:block;color:var(--ink2);font-size:13px;line-height:1.5;margin-top:2px}
main section{padding:8px 0 44px;border-bottom:1px solid var(--line)}
main section:last-child{border-bottom:0}
h2{font-size:24px;margin:0 0 4px;display:flex;align-items:baseline;gap:12px}
h2 .n{color:var(--ink3);font-size:15px;font-weight:400;font-variant-numeric:tabular-nums}
.sub{color:var(--ink2);margin:0 0 18px;font-size:15px}
.status{display:inline-block;font-size:12px;padding:1px 8px;border-radius:999px;color:#fff;vertical-align:middle;margin-left:6px}
.status.done{background:var(--done)}.status.partial{background:var(--partial)}.status.todo{background:var(--todo)}
figure{margin:0 0 20px}figcaption{color:var(--ink2);font-size:14px;line-height:1.6;margin-top:6px}
.fig-wide{margin-left:-24px;margin-right:-24px}@media(min-width:1240px){.fig-wide{margin-left:0;margin-right:-120px}}
.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.grid3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
.grid4{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.strip{display:flex;gap:10px;overflow-x:auto;padding-bottom:6px;scroll-snap-type:x mandatory}
.strip figure{flex:0 0 220px;scroll-snap-align:start;margin:0}.strip img{aspect-ratio:3/4;object-fit:cover;border-radius:4px;border:1px solid var(--line)}
.strip figcaption{font-size:12px}
table{border-collapse:collapse;width:100%%;font-size:15px;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--ink2);font-weight:700;font-size:13px}
td.num,th.num{text-align:right}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%%;margin-right:6px;vertical-align:-1px}
.dot.measured{background:var(--measured)}.dot.estimated,.dot.hypothesis{background:var(--estimated)}.dot.unknown{background:var(--unknown)}
.card{border:1px solid var(--line);border-radius:8px;padding:14px 16px;background:var(--bg2)}
.card h3{margin:0 0 6px;font-size:15px}.card p{margin:0;font-size:15px}
.reply{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.reply h3{word-break:break-all}
.ok{color:var(--done);font-weight:700}.ng{color:var(--unknown);font-weight:700}
.legend{display:flex;gap:18px;font-size:14px;color:var(--ink2);margin:8px 0 14px}
.elev{border:1px solid var(--line);border-radius:6px;padding:8px;background:#fbfaf7}.elev svg{width:100%%;height:auto}.elev.small svg{width:100%%}
.facechips{display:flex;flex-wrap:wrap;gap:6px 10px;margin-top:14px}.facechips span{font-size:14px;color:var(--ink2)}.facechips b{color:var(--ink);font-weight:700}
.small{font-size:14px;color:var(--ink2)}
.note{border-left:3px solid var(--line2);padding:6px 14px;color:var(--ink2);font-size:14px;margin:12px 0}
code{font-family:ui-monospace,Consolas,monospace;font-size:.92em;background:var(--bg2);padding:1px 5px;border-radius:3px}
footer{color:var(--ink3);font-size:13px;padding:20px 0 40px;border-top:1px solid var(--line)}
@media(max-width:900px){.layout{grid-template-columns:minmax(0,1fr);gap:12px}nav.rail{position:static}nav.rail ol{display:grid;grid-template-columns:1fr 1fr;border-left:0;gap:0 12px}
nav.rail li{padding-left:18px;border-left:2px solid var(--line)}.grid2,.grid3,.grid4,.reply{grid-template-columns:repeat(2,minmax(0,1fr))}header.top h1{font-size:26px}body{font-size:16px}}
@media(max-width:560px){.grid2,.grid3,.grid4,.reply{grid-template-columns:minmax(0,1fr)}nav.rail ol{grid-template-columns:1fr}.wrap{padding:0 16px}}
""" % BASIS_COL


def _e(s) -> str:
    return html.escape("" if s is None else str(s))


def _img(p: Path | None, base: Path, alt: str = "", cls: str = "") -> str:
    r = rel(p, base)
    return f'<img src="{_e(r)}" alt="{_e(alt)}" loading="lazy"{f" class={chr(34)}{cls}{chr(34)}" if cls else ""}>' if r else ""


def render(d: dict, out_html: Path) -> str:
    base = out_html.parent
    st = stage_status(d)
    es = d["summary"]
    tot = d["evidence"]["total"]
    parts = [f"""<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(d['name'])} 進捗報告</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;700&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body>
<header class="top"><div class="wrap">
<div class="kicker">campus-recorder　進捗報告　·　{_e(d['campus'] or '')}</div>
<h1>{_e(d['name'])}<span style="color:var(--ink3);font-weight:400;font-size:18px;margin-left:10px">{_e(d['id'])}</span></h1>
<div class="meta">写真 {d['photos']['first']} 〜 {d['photos']['last']}　·　この報告は {_e(d['generated'])} に棟 JSON と台帳から生成（手で直した絵や数字は無い）</div>
<div class="chips">
<span class="chip">写真<b>{d['photos']['total']}</b></span>
<span class="chip">登録済み<b>{d['evidence']['registered']}</b></span>
<span class="chip m">実測<b>{tot.get('measured', 0)}</b></span>
<span class="chip e">推定<b>{tot.get('estimated', 0)}</b></span>
<span class="chip u">不明<b>{tot.get('unknown', 0)}</b></span>
<span class="chip">形がある面・部品<b>{round(es['shape_ratio'] * 100)}%</b></span>
<span class="chip">撮影依頼<b>{len(d['rest']['requests'])}</b></span>
</div></div></header>
<div class="wrap layout"><nav class="rail"><ol>"""]
    for i, (key, title, _) in enumerate(STAGES, 1):
        s, r = st[key]
        parts.append(f'<li class="{s}"><a href="#{key}">{i}. {_e(title)}</a><span class="r">{_e(r)}</span></li>')
    parts.append("</ol></nav><main>")

    def head(i, key, title, sub):
        s, r = st[key]
        return (f'<section id="{key}"><h2><span class="n">{i} / 6</span>{_e(title)}<span class="status {s}">'
                f'{ {"done": "済", "partial": "途中", "todo": "未"}[s] }</span></h2><p class="sub">{_e(sub)}　—　{_e(r)}</p>')

    # 1 写真
    p = d["photos"]
    parts.append(head(1, "photos", "写真", "職員はスマホで撮るだけ。図面も 3D も触らない"))
    thumbs = []
    for ph in d["evidence"]["photos"][:10]:
        if ph.get("file"):
            src = R_.photo_path({"file": ph["file"]}) if hasattr(R_, "photo_path") else Path("data/photos") / ph["file"]
            if Path(src).exists():
                t = thumbnail(Path(src), base / "report" / f"thumb_{ph['id']}.jpg")
                if t:
                    thumbs.append((t, ph))
    if thumbs:
        parts.append('<div class="strip">')
        for t, ph in thumbs:
            parts.append(f'<figure>{_img(t, base, ph["id"])}<figcaption>{_e(ph["time"])}　面 {_e(ph["face"] or "—")}　{_e(METHOD_JA.get(ph["method"], ph["method"]))}で登録</figcaption></figure>')
        parts.append("</div><p class='small'>帯の写真は登録済みの物（縮小。元の写真はリポジトリに入れない）。</p>")
    parts.append("<div class='reply'>")
    for r in p["replies"]:
        q = r["reply"]
        ok = q.get("usable")
        probs = q.get("problems") or []
        parts.append(f"<div class='card'><h3>{_e(r['id'])}<span class='small'>　{_e(r['taken_at'])}</span></h3>"
                     f"<p><span class='{'ok' if ok else 'ng'}'>{'使えます' if ok else '使えません'}</span>"
                     + (f"　{_e('・'.join(probs))}" if probs else "　問題なし")
                     + f"</p><p class='small'>{'撮影係の返事' if r['from_agent'] else '道具の自動判定（撮影係の返事の下書き）'}</p></div>")
    parts.append("</div>")
    parts.append("<div class='facechips'>面ごとの枚数：" + " ".join(f"<span>{_e(k)} <b>{v}</b></span>" for k, v in p["faces"][:14]) + "</div></section>")

    # 2 立面図
    parts.append(head(2, "elev", "立面図（根拠の色付き）", "黒＝実測（根拠の写真番号あり）、琥珀の破線＝推定（方法を書いた）、赤の破線＝不明（値は無い）"))
    parts.append('<div class="legend"><span><i class="dot measured"></i>実測 measured</span><span><i class="dot estimated"></i>推定 estimated</span><span><i class="dot unknown"></i>不明 unknown</span></div>')
    shown = [e for e in d["elev"] if e["svg"] is not None]
    for e in shown[:2]:
        parts.append(f'<figure class="elev fig-wide">{inline_svg(e["svg"])}<figcaption><i class="dot {e["state"]}"></i>{_e(e["face"])}　{_e(e["name"])}'
                     f'　—　値 {e["values"]}（実測 {e["measured"]}）</figcaption></figure>')
    if shown[2:]:
        parts.append('<div class="grid3">')
        for e in shown[2:]:
            parts.append(f'<figure class="elev small"><a href="{_e(rel(e["svg"], base))}">{inline_svg(e["svg"])}</a><figcaption><i class="dot {e["state"]}"></i>{_e(e["face"])}　{_e(e["name"])}'
                         f'　—　値 {e["values"]}（実測 {e["measured"]}）</figcaption></figure>')
        parts.append("</div><p class='small'>小さい図は押すと元の大きさで開く。</p>")
    missing = [e for e in d["elev"] if e["svg"] is None]
    if missing:
        parts.append(f"<p class='small'>図面の無い面：{_e('、'.join(e['face'] for e in missing))}（面の範囲が未定）</p>")
    parts.append("</section>")

    # 3 証拠
    v = d["evidence"]
    parts.append(head(3, "evidence", "証拠", "立面図を信じてよい理由。登録済みの写真は模型のどこに立っているか、指した目印は何 px ずれたか"))
    if v["images"]:
        parts.append('<div class="grid2 fig-wide">')
        caps = ["模型と、登録済みの写真の視点。扇が立っている面に写真がある", "選んだ写真の上の、指した目印（赤）と解いた視点で写した位置（水色）。ラベルは残差 px"]
        for im, cap in zip(v["images"], caps):
            parts.append(f"<figure>{_img(im, base, cap)}<figcaption>{_e(cap)}</figcaption></figure>")
        parts.append("</div>")
    parts.append("<table><tr><th>面</th><th class='num'>実測</th><th class='num'>推定</th><th class='num'>不明</th><th class='num'>登録済みの写真</th><th class='num'>残差の中央値 px</th></tr>")
    for r in v["faces"]:
        parts.append(f"<tr><td>{_e(r['face'])}<span class='small'>　{_e(r['name'])}</span></td><td class='num'>{r['measured']}</td><td class='num'>{r['estimated']}</td>"
                     f"<td class='num'>{r['unknown']}</td><td class='num'>{r['photos']}</td><td class='num'>{'—' if r['resid'] is None else r['resid']}</td></tr>")
    parts.append("</table>")
    meth = "・".join(f"{METHOD_JA.get(k, k)} {n}" for k, n in sorted(v["methods"].items(), key=lambda kv: -kv[1]))
    parts.append(f"<p class='small'>登録の方法：{_e(meth) or '—'}。目視や整合で保留にした登録 {v['held']} 件（台帳の <code>not_registered</code> に理由つき）。"
                 + (f"ビューアで開く：<code>rerun {_e(rel(v['rrd'], base))}</code>（Rerun は描くだけで、形も数字も棟 JSON の物）" if v["rrd"] else "") + "</p></section>")

    # 4 模型
    m = d["model"]
    parts.append(head(4, "model", "模型", "図面から Blender が組む。絵を描いた物ではなく、根拠の結果"))
    if m["views"]:
        parts.append('<div class="grid4">')
        for vw in m["views"]:
            parts.append(f"<figure>{_img(base / vw['file'], base, vw['label'])}<figcaption>{_e(vw['label'])}</figcaption></figure>")
        parts.append("</div>")
    if m["streets"] or m["overlays"]:
        parts.append('<div class="grid3">')
        for s in m["streets"]:
            parts.append(f"<figure>{_img(s, base, '街路視点')}<figcaption>街路視点（模型だけ）</figcaption></figure>")
        for o in m["overlays"]:
            parts.append(f"<figure>{_img(o, base, '検収の重ね')}<figcaption>検収：写真の視点に模型の線を重ねた物（{_e(o.stem.replace('overlay_v2_', '').replace('overlay_', ''))}）</figcaption></figure>")
        parts.append("</div>")
    if m["assumed"]:
        parts.append("<div class='note'>仮置き（未測。模型を組むために置いた値）：" + "、".join(f"{_e(a.get('what'))} {_e(a.get('value'))}" for a in m["assumed"][:8])
                     + ("　ほか" if len(m["assumed"]) > 8 else "") + "</div>")
    parts.append("</section>")

    # 5 歩ける 3D
    w = d["walk"]
    parts.append(head(5, "walk", "地形に載せて歩く", "根拠のある棟から色が付く。未測の棟は PLATEAU の灰色の箱のまま"))
    if w["image"]:
        parts.append(f"<figure class='fig-wide'>{_img(w['image'], base, '歩く画面')}<figcaption>歩く画面（<code>web/index.html</code>）。PC は WASD とドラッグ、スマホは左のスティック"
                     + (f"。この構図：<code>?cam={_e(w['cam'])}</code>" if w.get("cam") else "") + "</figcaption></figure>")
    parts.append(f"<p>{'この棟は地形に載っています。' if w['on_site'] else 'この棟はまだ地形に載っていません。'}敷地には色付きの棟 {w['colored']}、灰色の箱 {w['gray']}。</p></section>")

    # 6 まだ無い物
    r = d["rest"]
    parts.append(head(6, "rest", "まだ無い物", "決めつけない。測れていない所は灰色のまま残し、次の職員はここから撮る"))
    if r["requests"]:
        parts.append("<table><tr><th>面</th><th>何を</th><th>どこから</th><th>なぜ</th></tr>")
        for q in r["requests"][:20]:
            parts.append(f"<tr><td>{_e(q.get('face'))}</td><td>{_e(q.get('what'))}</td><td class='small'>{_e(q.get('where'))}</td><td class='small'>{_e(q.get('why'))}</td></tr>")
        parts.append("</table>")
        if len(r["requests"]) > 20:
            parts.append(f"<p class='small'>ほか {len(r['requests']) - 20} 件</p>")
    if r["coverage"]:
        parts.append("<h3 style='font-size:16px;margin:18px 0 6px'>面の写真の覆い</h3><table><tr><th>面</th><th class='num'>写真</th><th class='num'>使えた面積 m²</th><th class='num'>使えた割合</th></tr>")
        for c in r["coverage"]:
            parts.append(f"<tr><td>{_e(c['face'])}</td><td class='num'>{c['photos']}</td><td class='num'>{c['used_m2']}</td><td class='num'>{'—' if c['ratio'] is None else c['ratio']}</td></tr>")
        parts.append("</table>")
    if r["unknowns"]:
        parts.append(f"<p class='small' style='margin-top:14px'>未確定の値 {len(r['unknowns'])}：<code>" + "</code> <code>".join(_e(u) for u in r["unknowns"][:30]) + "</code>"
                     + ("　ほか" if len(r["unknowns"]) > 30 else "") + "</p>")
    parts.append("</section></main></div>")
    parts.append(f"<footer><div class='wrap'>生成：<code>recorder report data/buildings/{_e(d['id'])}.json</code>　·　元データは棟 JSON 1 つ。図面・模型・重ね・この報告は全部そこから生成。"
                 f"　·　履歴 {d['history_n']} 件　·　座標 JGD2011 平面直角 VII 系</div></footer></body></html>")
    return "".join(parts)


# --- 入口 ---------------------------------------------------------------------------------

def run(building_json: Path, out: Path | None = None) -> dict:
    b = load_building(building_json)
    out_dir = OUT_ROOT / b["id"]
    out_html = out or (out_dir / "report.html")
    index = load_index(INDEX_PATH) if INDEX_PATH.exists() else {"photos": []}
    reg = R_.load_registry() if R_.REGISTRY_PATH.exists() else {"entries": [], "not_registered": []}
    site = json.loads(WEB_SITE.read_text(encoding="utf-8")) if WEB_SITE.exists() else None
    d = gather(b, index, reg, out_dir, site)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(render(d, out_html), encoding="utf-8")
    st = stage_status(d)
    summ = {"html": str(out_html), "made_by": "report（recorder）", "made_at": datetime.now().isoformat(timespec="seconds"),
            "stages": {k: {"status": s, "result": r} for k, (s, r) in st.items()},
            "counts": {"photos": d["photos"]["total"], "registered": d["evidence"]["registered"], **{k: d["evidence"]["total"].get(k, 0) for k in STATUS_JA},
                       "requests": len(d["rest"]["requests"]), "unknowns": len(d["rest"]["unknowns"])}}
    (out_dir / "report.json").write_text(json.dumps(summ, ensure_ascii=False, indent=1), encoding="utf-8")
    return summ


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("file", help="棟 JSON（data/buildings/<棟>.json）")
    p.add_argument("--out", default=None, help="出力 HTML（既定 out/<棟>/report.html）")


def run_cli(a: argparse.Namespace) -> int:
    s = run(Path(a.file), Path(a.out) if a.out else None)
    print(s["html"])
    for k, title, _ in STAGES:
        st = s["stages"][k]
        print(f"  {title}：{st['status']}　{st['result']}")
    return 0
