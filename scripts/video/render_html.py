"""自作の HTML の場面を、時刻ごとに止めて 1 枚ずつ撮り、ffmpeg で mp4 にする（音なし）。

ページは window.seek(t)（Promise）を持ち、window.READY が真になったら撮り始める。repo の根を一時配信して開く。

使い方:
    python -I -X utf8 scripts/video/render_html.py                          # 導入 v2 を全部撮って mp4 に
    python -I -X utf8 scripts/video/render_html.py --from 9 --to 15         # 9〜15 秒だけ撮り直して mp4 を作り直す
    python -I -X utf8 scripts/video/render_html.py --stills 1               # 1 秒おきの確認用 PNG と montage.png だけ
    python -I -X utf8 scripts/video/render_html.py --at 14.6 --at 14.7      # 指定の時刻の 1 枚（めくれの途中の確認）

GPU の鍵（recorder.ledger の lock("gpu")）は取らない。代わりに Edge を --disable-gpu で別に起動する
（本部棟の API 追記が Blender で GPU を使っている間も撮れるように）。Edge の起動・片付けは recorder.shots の流儀
（専用のプロファイル、窓なし・画面の外、終わったら同じプロファイルの msedge.exe が残っていないか確かめる）。
"""
from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from recorder.shots import CDP, NO_WINDOW, _stop, find_browser, serve  # noqa: E402

PAGE = "scripts/video/opening/index.html"
OUT = ROOT / "out" / "video" / "opening_v2"
MP4 = ROOT / "out" / "video" / "opening_v2.mp4"


def launch(size: tuple[int, int], h: dict, wait: float = 60.0) -> str:
    exe = find_browser()
    if exe is None:
        raise FileNotFoundError("Edge も Chrome も無い")
    h["exe"] = exe
    prof = Path(tempfile.mkdtemp(prefix="recorder_video_"))
    errf = prof.parent / f"{prof.name}.stderr.txt"
    h.update(prof=prof, errf=errf)
    cmd = [str(exe), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--hide-scrollbars", "--disable-extensions", "--mute-audio", f"--user-data-dir={prof}",
           f"--window-size={size[0]},{size[1]}", "--window-position=-32000,-32000",
           "--force-device-scale-factor=1", "--font-render-hinting=none",
           "--remote-debugging-port=0", "--remote-allow-origins=*", "about:blank"]
    with open(errf, "wb") as eh:
        h["proc"] = proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=eh, stdin=subprocess.DEVNULL,
                                            creationflags=NO_WINDOW)
    t0 = time.time()
    port = None
    while port is None:
        try:
            txt = (prof / "DevToolsActivePort").read_text().split()
            port = int(txt[0]) if txt else None
        except (OSError, ValueError):
            port = None
        if port is None:
            if proc.poll() is not None or time.time() - t0 > wait:
                raise RuntimeError("headless の Edge が口を開かなかった")
            time.sleep(0.1)
    while True:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as r:
                pages = [t for t in json.loads(r.read()) if t.get("type") == "page"]
            if pages:
                return pages[0]["webSocketDebuggerUrl"]
        except (OSError, ValueError):
            pass
        if time.time() - t0 > wait:
            raise RuntimeError("Edge のページが出ない")
        time.sleep(0.2)


def shoot(cdp: CDP, t: float, png: Path) -> None:
    cdp.call("Runtime.evaluate", {"expression": f"window.seek({t:.4f})", "awaitPromise": True, "returnByValue": True})
    r = cdp.call("Page.captureScreenshot", {"format": "png", "clip": {"x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1}},
                 timeout=120)
    png.parent.mkdir(parents=True, exist_ok=True)
    png.write_bytes(base64.b64decode(r["data"]))


def montage(pngs: list[Path], dst: Path, cols: int = 5) -> None:
    from PIL import Image, ImageDraw
    tw, th = 384, 216
    rows = (len(pngs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * (th + 24)), "white")
    d = ImageDraw.Draw(sheet)
    for i, p in enumerate(pngs):
        im = Image.open(p).convert("RGB").resize((tw, th))
        x, y = (i % cols) * tw, (i // cols) * (th + 24)
        sheet.paste(im, (x, y + 24))
        d.text((x + 6, y + 4), p.stem, fill="black")
    sheet.save(dst)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", default=PAGE)
    ap.add_argument("--from", dest="t0", type=float, default=None)
    ap.add_argument("--to", dest="t1", type=float, default=None)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--stills", type=float, default=None, help="この秒おきの確認用 PNG だけ撮る")
    ap.add_argument("--at", type=float, action="append", default=[], help="この時刻の 1 枚だけ（何度でも）")
    ap.add_argument("--query", default="", help="ページに付ける問い合わせ（例 values=/out/video/x.json で板の値を試す）")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--mp4", type=Path, default=MP4)
    a = ap.parse_args()

    tl = json.loads((ROOT / Path(a.page).parent / "timeline.json").read_text(encoding="utf-8"))
    total = tl["total"]
    nframes = int(round(total * a.fps))
    allf = a.out / "all_frames"

    if a.at:
        jobs = [(t, a.out / "frames" / f"at_{t:06.2f}.png") for t in a.at]
    elif a.stills:
        jobs, t = [], 0.0
        while t <= total + 1e-6:
            jobs.append((t, a.out / "frames" / f"t{t:05.1f}.png"))
            t += a.stills
    else:
        f0 = 0 if a.t0 is None else max(0, int(a.t0 * a.fps))
        f1 = nframes if a.t1 is None else min(nframes, int(round(a.t1 * a.fps)) + 1)
        jobs = [(i / a.fps, allf / f"f{i:05d}.png") for i in range(f0, f1)]

    srv, port = serve(ROOT)
    h: dict = {}
    info: dict = {}
    cdp = None
    t_start = time.time()
    try:
        ws = launch((1920, 1080), h)
        cdp = CDP(ws)
        cdp.call("Page.enable")
        cdp.call("Emulation.setDeviceMetricsOverride", {"width": 1920, "height": 1080, "deviceScaleFactor": 1, "mobile": False})
        url = f"http://127.0.0.1:{port}/" + urllib.parse.quote(a.page) + (f"?{a.query}" if a.query else "")
        cdp.call("Page.navigate", {"url": url})
        for _ in range(240):
            try:
                if cdp.evaluate("window.READY===true"):
                    break
            except RuntimeError:
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError(f"ページが READY にならない（{cdp.evaluate('JSON.stringify(window.ERR||[])')}）")
        for k, (t, png) in enumerate(jobs):
            shoot(cdp, t, png)
            if k % 60 == 0:
                print(f"  {k}/{len(jobs)} 枚（{t:.2f} 秒）", flush=True)
    finally:
        if h.get("proc") is not None:
            _stop(h["exe"], h, info, cdp)
        srv.shutdown()
        srv.server_close()
    print(f"撮影 {len(jobs)} 枚 {time.time() - t_start:.0f} 秒  残り Edge {info.get('leftover_after', 0)}")

    if a.stills:
        pngs = [p for _, p in jobs]
        montage(pngs, a.out / "frames" / "montage.png")
        print("montage →", a.out / "frames" / "montage.png")
        return
    if a.at:
        return
    missing = [i for i in range(nframes) if not (allf / f"f{i:05d}.png").exists()]
    if missing:
        print(f"まだ撮っていない枚 {len(missing)}（最初 {missing[0]}）。mp4 は作らない")
        return
    ff = shutil.which("ffmpeg") or "ffmpeg"
    cmd = [ff, "-y", "-loglevel", "error", "-framerate", str(a.fps), "-i", str(allf / "f%05d.png"),
           "-frames:v", str(nframes), "-c:v", "libx264", "-preset", "slow", "-crf", "16",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(a.mp4)]
    subprocess.run(cmd, check=True)
    print("mp4 →", a.mp4, f"（{nframes} 枚、{nframes / a.fps:.2f} 秒、音なし）")


if __name__ == "__main__":
    main()
