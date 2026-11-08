"""職員の画面（web/staff.html）を、人が操作しているような速さで操作しながら 1/30 秒ずつ撮る。

前提：本物の data/ を汚さないよう、**複製の作業フォルダ**で `recorder serve`（API キーなし＝試走 dry-run）を
立てておく（例：作業フォルダで `python -X utf8 -m recorder serve --port 8767`）。この道具はページを開いて操作し、撮るだけ。

使い方:
    python -I -X utf8 scripts/video/capture_staff.py --probe                 # 1 枚撮って、欄とボタンの位置を出す
    python -I -X utf8 scripts/video/capture_staff.py --photos <id> <id> …    # 操作を撮る → out/video/flow/staff_cap/f00000.jpg … と
                                                                               # out/video/flow_staff.mp4、押した後の文言を staff_cap/log.json に

操作（仮想の時刻で進める。1 コマごとに状態を進めてから撮るので、カーソルは滑らか、打鍵は 1 字 0.1 秒）：
  ① 大学名の欄があれば「名城大学」と打つ ② 公開版の写真をドロップ ③ 棟の欄に「本部棟」と打って「選んだ写真に付ける」
  ④「記録を始める」（#export）を押す ⑤ そのまま数秒撮る。
カーソルは本物のマウスが headless で映らないので、ページに重ねた絵（CSS）。クリックは CDP の本物のマウスの出来事で押す。
GPU の鍵は取らない（Edge は --disable-gpu、render_html の起動を使う）。
"""
from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "video"))

from recorder.shots import CDP, _stop  # noqa: E402
from render_html import launch  # noqa: E402

OUT = ROOT / "out" / "video" / "flow" / "staff_cap"
FPS = 30

OVERLAY_JS = r"""
(() => {
 if (window.__cur) return;
 const c=document.createElement('div');c.id='__cur';
 c.style.cssText='position:fixed;left:0;top:0;z-index:2147483647;pointer-events:none;width:40px;height:56px';
 c.innerHTML='<svg viewBox="0 0 40 56" width="40" height="56"><path d="M3 3L3 44L14 34L22 52L30 48L22 31L37 31Z" fill="#fff" stroke="#1a1a1c" stroke-width="3" stroke-linejoin="round"/></svg>';
 const r=document.createElement('div');r.id='__rip';
 r.style.cssText='position:fixed;z-index:2147483646;pointer-events:none;border-radius:50%;border:5px solid #0031d8;opacity:0;box-sizing:border-box';
 const g=document.createElement('div');g.id='__ghost';
 g.style.cssText='position:fixed;z-index:2147483645;pointer-events:none;display:none';
 document.body.append(g,r,c);
 window.__cur={c,r,g};
 window.__set=(x,y,press,rip,ripx,ripy,ghost)=>{
   c.style.transform=`translate(${x}px,${y}px)`+(press?' scale(.88)':'');
   if(rip>0&&rip<1){const s=20+70*(1-Math.pow(1-rip,3));Object.assign(r.style,{left:(ripx-s/2)+'px',top:(ripy-s/2)+'px',width:s+'px',height:s+'px',opacity:1-rip})}else r.style.opacity=0;
   g.style.display=ghost?'block':'none';g.style.transform=`translate(${x+26}px,${y+30}px)`;
 };
})();
"""


def rect(cdp: CDP, sel: str):
    return cdp.evaluate(f"(()=>{{const e=document.querySelector({json.dumps(sel)});if(!e)return null;const r=e.getBoundingClientRect();"
                        f"const s=getComputedStyle(e);return {{x:r.x,y:r.y,w:r.width,h:r.height,vis:s.display!=='none'&&s.visibility!=='hidden'&&r.width>0,"
                        f"dis:!!e.disabled,text:(e.innerText||e.value||'').slice(0,80)}}}})()")


def click(cdp: CDP, x: float, y: float):
    for t in ("mousePressed", "mouseReleased"):
        cdp.call("Input.dispatchMouseEvent", {"type": t, "x": x, "y": y, "button": "left", "clickCount": 1})


def shoot(cdp: CDP, png: Path, fmt: str = "jpeg"):
    r = cdp.call("Page.captureScreenshot", {"format": fmt, "quality": 92} if fmt == "jpeg" else {"format": fmt}, timeout=60)
    png.parent.mkdir(parents=True, exist_ok=True)
    png.write_bytes(base64.b64decode(r["data"]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8767/web/staff.html")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--photos", nargs="*", default=[])
    ap.add_argument("--univ", default="名城大学")
    ap.add_argument("--building", default="本部棟")
    ap.add_argument("--tail", type=float, default=5.0, help="押した後に撮る秒")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--mp4", type=Path, default=ROOT / "out" / "video" / "flow_staff.mp4")
    a = ap.parse_args()

    h: dict = {}
    info: dict = {}
    cdp = None
    log = {"url": a.url, "steps": []}
    try:
        ws = launch((1920, 1080), h)
        cdp = CDP(ws)
        cdp.call("Page.enable")
        cdp.call("Emulation.setDeviceMetricsOverride", {"width": 1920, "height": 1080, "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Page.navigate", {"url": a.url})
        time.sleep(4.0)
        sels = ["#univ", "#campus", "#campusSel", "#drop", "#bulkBuilding", "#apply", "#export", "#status", "#p1", "#p2", "#p3"]
        if a.probe:
            shoot(cdp, a.out.parent / "staff_probe.png", "png")
            print(json.dumps({s: rect(cdp, s) for s in sels}, ensure_ascii=False, indent=1))
            return

        if a.out.exists():
            shutil.rmtree(a.out)
        a.out.mkdir(parents=True)
        cdp.evaluate(OVERLAY_JS)
        # 写真の札（ドラッグ中にカーソルに付く小さな束）
        ids = a.photos
        idx = json.loads((ROOT / "data" / "photos" / "index.json").read_text(encoding="utf-8"))["photos"]
        files = []
        for pid in ids:
            p = next(x for x in idx if x["id"] == pid)
            assert p.get("public_file"), pid
            files.append("/data/photos/" + p["public_file"])
        cdp.evaluate("window.__cur.g.innerHTML=" + json.dumps("".join(
            f'<img src="{f}" style="position:absolute;left:{i*10}px;top:{i*8}px;width:120px;height:90px;object-fit:cover;border:4px solid #fff;box-shadow:0 4px 10px rgba(0,0,0,.35)">'
            for i, f in enumerate(files))))

        # 位置（画面の px）
        def center(sel):
            r = rect(cdp, sel)
            return (r["x"] + r["w"] / 2, r["y"] + r["h"] / 2) if r and r["vis"] else None

        univ = rect(cdp, "#univ")
        has_univ = bool(univ and univ["vis"])
        plan = []          # (t, kind, arg)
        t = 0.5
        pos = (1500.0, 760.0)
        track = [(0.0, *pos)]

        def move(to, dur):
            nonlocal t
            track.append((t, *track[-1][1:]))
            t += dur
            track.append((t, *to))

        if has_univ:
            c = center("#univ")
            move((c[0] - 60, c[1] + 4), 0.8)
            plan.append((t, "click", (c[0] - 60, c[1] + 4)))
            t += 0.1
            plan.append((t, "clear", "#univ"))
            t += 0.25
            for ch in a.univ:
                plan.append((t, "type", ch))
                t += 0.1
            t += 0.6
        # ドロップ
        d = center("#drop")
        plan.append((t, "ghost", True))
        move((d[0] - 20, d[1] - 10), 1.0)
        plan.append((t, "drop", None))
        plan.append((t, "ghost", False))
        t += 1.2
        plan.append((t, "relayout", None))
        tmax_before = t
        log["plan_head"] = {"has_univ": has_univ, "drop": d}

        # 撮りながら進める（後半の位置は、ドロップの後のレイアウトで決める）
        frame = 0
        rip = None
        scrolls = []        # (t0, dur, y0, y1)
        done = set()
        state = {"ghost": False}

        def cur_at(tt):
            pts = track
            x, y = pts[0][1], pts[0][2]
            for i in range(len(pts) - 1):
                (ta, xa, ya), (tb, xb, yb) = pts[i], pts[i + 1]
                if ta <= tt <= tb:
                    p = (tt - ta) / max(1e-3, tb - ta)
                    p = 4 * p ** 3 if p < .5 else 1 - (-2 * p + 2) ** 3 / 2
                    return xa + (xb - xa) * p, ya + (yb - ya) * p
            if tt > pts[-1][0]:
                return pts[-1][1], pts[-1][2]
            return x, y

        def run_until(tend):
            nonlocal frame, rip
            while frame / FPS <= tend:
                tt = frame / FPS
                for k, (ta, kind, arg) in enumerate(plan):
                    if k in done or ta > tt:
                        continue
                    done.add(k)
                    if kind == "click":
                        click(cdp, *arg)
                        rip = (ta, *arg)
                        log["steps"].append({"t": round(ta, 2), "click": arg})
                    elif kind == "type":
                        cdp.call("Input.insertText", {"text": arg})
                    elif kind == "clear":
                        cdp.evaluate("(()=>{const e=document.querySelector(%s);e.focus();e.select();})()" % json.dumps(arg))
                    elif kind == "ghost":
                        state["ghost"] = arg
                    elif kind == "drop":
                        n = cdp.evaluate("""(async()=>{const urls=%s;const dt=new DataTransfer();
                          for(const u of urls){const b=await (await fetch(u)).blob();dt.items.add(new File([b],decodeURIComponent(u.split('/').pop()),{type:'image/jpeg'}))}
                          const z=document.querySelector('#drop');for(const ty of ['dragenter','dragover','drop']){z.dispatchEvent(new DragEvent(ty,{bubbles:true,cancelable:true,dataTransfer:dt}))}
                          return dt.files.length})()""" % json.dumps(files))
                        cdp.call("Runtime.evaluate", {"expression": "1", "awaitPromise": True})
                        log["steps"].append({"t": round(ta, 2), "drop": files})
                for (s0, sd, y0, y1) in scrolls:
                    if s0 <= tt <= s0 + sd + 1 / FPS:
                        q = min(1.0, (tt - s0) / sd)
                        q = 4 * q ** 3 if q < .5 else 1 - (-2 * q + 2) ** 3 / 2
                        cdp.evaluate(f"window.scrollTo(0,{y0 + (y1 - y0) * q:.1f})")
                x, y = cur_at(tt)
                rp = (tt - rip[0]) / .5 if rip else 0
                pressed = bool(rip and -0.05 <= tt - rip[0] < 0.12)
                cdp.evaluate(f"window.__set({x:.1f},{y:.1f},{str(pressed).lower()},{rp:.3f},{rip[1] if rip else 0},{rip[2] if rip else 0},{str(state['ghost']).lower()})")
                shoot(cdp, a.out / f"f{frame:05d}.jpg")
                frame += 1

        run_until(tmax_before)
        time.sleep(1.5)
        # 後半：棟の欄に打って「付ける」→「記録を始める」
        def scroll_to(sel, dur=0.7, frac=0.45):
            nonlocal t
            r = rect(cdp, sel)
            y0 = cdp.evaluate("window.scrollY")
            y1 = max(0, y0 + r["y"] + r["h"] / 2 - 1080 * frac)
            scrolls.append((t, dur, y0, y1))
            t += dur + 0.1
            run_until(t)
            time.sleep(0.3)

        b = rect(cdp, "#bulkBuilding")
        if b and b["vis"]:
            scroll_to("#bulkBuilding")
            c = center("#selAll")
            move((c[0], c[1] + 4), 0.8)
            plan.append((t, "click", (c[0], c[1] + 4)))
            t += 0.6
            c = center("#bulkBuilding")
            move((c[0] - 80, c[1] + 4), 0.6)
            plan.append((t, "click", (c[0] - 80, c[1] + 4)))
            t += 0.25
            for ch in a.building:
                plan.append((t, "type", ch))
                t += 0.1
            t += 0.5
            c = center("#apply")
            move((c[0], c[1] + 4), 0.7)
            plan.append((t, "click", (c[0], c[1] + 4)))
            t += 0.9
        run_until(t)
        time.sleep(1.0)
        scroll_to("#export", 0.9, 0.55)
        ex = rect(cdp, "#export")
        log["export_before"] = ex
        c = center("#export")
        move((c[0], c[1] + 4), 0.9)
        plan.append((t, "click", (c[0], c[1] + 4)))
        t_press = t
        run_until(t + 0.2)
        # 押した後：本物の返事を待ちながら撮る（仮想の時刻は 1 コマ 1/30 秒、実時間はもっとかかる）
        run_until(t_press + a.tail)
        log["status_after"] = cdp.evaluate("document.querySelector('#status')&&document.querySelector('#status').innerText")
        log["export_after"] = rect(cdp, "#export")
        log["progress_after"] = cdp.evaluate("document.querySelector('#rows')&&document.querySelector('#rows').innerText.slice(0,600)")
        log["t_press"] = round(t_press, 2)
        log["frames"] = frame
    finally:
        if cdp is not None or h.get("proc") is not None:
            _stop(h["exe"], h, info, cdp)
    (a.out / "log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    ff = shutil.which("ffmpeg") or "ffmpeg"
    subprocess.run([ff, "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(a.out / "f%05d.jpg"), "-c:v", "libx264",
                    "-crf", "17", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(a.mp4)], check=True)
    print(json.dumps({k: log.get(k) for k in ("t_press", "frames", "status_after")}, ensure_ascii=False))
    print("mp4 →", a.mp4)


if __name__ == "__main__":
    main()
