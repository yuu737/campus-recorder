"""歩く画面（web/index.html）を 1 コマずつ撮る（デモ版の動画の素材。音なし）。

web/ は静的なファイルだけで動くので、recorder serve は使わず、repo の根を読むだけの一時配信（recorder.shots.serve、
空きポート）で開く。8767・8768 の serve には繋がない。Edge は --disable-gpu（GPU の鍵は取らない。描画はソフト）。

歩く道は、ページが動作確認用に出している window.__campus.place(x, z, yaw) で 1 コマごとに置く（キーを押した事にはしない）。
「根拠で見る」は本物のボタンを JS で click() し、その位置を log.json に書く（動画の偽のカーソルと波紋の位置に使う）。

    python -I -X utf8 scripts/video/demo/capture_walk.py --test       # 1 枚だけ（速さと絵の確認）
    python -I -X utf8 scripts/video/demo/capture_walk.py              # 本撮り（out/video/demo/walk_cap/f00000.jpg…）
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "video"))

from recorder.shots import CDP, _stop, serve  # noqa: E402
from render_html import launch  # noqa: E402

OUT = ROOT / "out" / "video" / "demo" / "walk_cap"


def path_at(t: float, P: dict) -> tuple[float, float, float]:
    """時刻 t（秒）の立つ位置（three.js の x, z）と向き yaw（ラジアン）。P は道の端点。"""
    a, b, tw = P["a"], P["b"], P["walk"]
    p = min(1.0, max(0.0, t / tw))
    p = p * p * (3 - 2 * p)
    x = a[0] + (b[0] - a[0]) * p
    n = a[1] + (b[1] - a[1]) * p
    yaw_deg = a[2] + (b[2] - a[2]) * p
    return x, -n, -yaw_deg * math.pi / 180


def mouse(cdp: CDP, kind: str, x: float, y: float) -> None:
    cdp.call("Input.dispatchMouseEvent", {"type": kind, "x": x, "y": y, "button": "left",
                                          "buttons": 1 if kind != "mouseReleased" else 0, "clickCount": 1})


def drag(cdp: CDP, x0: float, y0: float, x1: float, y1: float) -> None:
    mouse(cdp, "mousePressed", x0, y0)
    mouse(cdp, "mouseMoved", x1, y1)
    mouse(cdp, "mouseReleased", x1, y1)


def click(cdp: CDP, x: float, y: float) -> None:
    mouse(cdp, "mousePressed", x, y)
    mouse(cdp, "mouseReleased", x, y)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--dur", type=float, default=12.0)
    ap.add_argument("--ev-at", type=float, default=6.0, help="「根拠で見る」を押す秒")
    ap.add_argument("--card-at", type=float, default=99.0, help="棟をクリックして札を出す秒")
    ap.add_argument("--card-xy", type=float, nargs=2, default=(930.0, 300.0))
    ap.add_argument("--drag", type=float, default=34.0, help="上を向くドラッグの px（0.005 rad/px）")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    # 本部棟の街路視点（web/data/site.json の models.honbu.street：東 -33.21, 北 -67.0, yaw 4.72°）の少し後ろから近づく
    P = {"a": [-36.0, -92.0, 10.0], "b": [-33.2, -70.0, 2.0], "walk": a.ev_at - 0.6}
    srv, port = serve(ROOT)
    h: dict = {}
    info: dict = {}
    cdp = None
    log = {"page": "web/index.html", "fps": a.fps, "size": [1920, 1080], "ev_at": a.ev_at, "path": P,
           "note": "window.__campus.place で 1 コマずつ置いて撮った。描画は headless Edge のソフト描画"}
    try:
        ws = launch((1920, 1080), h, wait=90)
        cdp = CDP(ws, timeout=180)
        cdp.call("Page.enable")
        cdp.call("Emulation.setDeviceMetricsOverride", {"width": 1920, "height": 1080, "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Page.navigate", {"url": f"http://127.0.0.1:{port}/web/index.html"})
        t0 = time.time()
        while True:
            try:
                if cdp.evaluate("!!window.__campus && document.getElementById('loading').style.display==='none'"):
                    break
            except RuntimeError:
                pass
            if time.time() - t0 > 240:
                raise RuntimeError("歩く画面が読み込まれない")
            time.sleep(0.5)
        print(f"読み込み {time.time() - t0:.0f} 秒", flush=True)
        r = cdp.evaluate("JSON.stringify(document.getElementById('evbtn').getBoundingClientRect())")
        log["evbtn"] = json.loads(r)
        n = 1 if a.test else int(round(a.dur * a.fps))
        a.out.mkdir(parents=True, exist_ok=True)
        ev_done = card_done = False
        ts = time.time()
        for i in range(n):
            t = i / a.fps
            x, z, yaw = path_at(t, P)
            js = f"window.__campus.place({x:.4f},{z:.4f},{yaw:.5f});"
            js += "1"
            cdp.call("Runtime.evaluate", {"expression": js, "returnByValue": True}, timeout=180)
            # 上を少し向く：place が毎回 pitch を -0.05 に戻すので、毎コマ同じだけ本物のマウスで縦にドラッグする（8 px 以上動かすので札は出ない）
            drag(cdp, 960, 900, 960, 900 - a.drag)
            if not ev_done and t >= a.ev_at:
                b = log["evbtn"]
                click(cdp, b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
                ev_done = True
                log["ev_frame"] = i
                log["ev_click"] = [b["x"] + b["width"] / 2, b["y"] + b["height"] / 2]
            if not card_done and t >= a.card_at:
                click(cdp, *a.card_xy)
                card_done = True
                log["card_frame"] = i
                log["card_click"] = list(a.card_xy)
            js = "new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>requestAnimationFrame(()=>r(1)))))"
            cdp.call("Runtime.evaluate", {"expression": js, "awaitPromise": True, "returnByValue": True}, timeout=180)
            res = cdp.call("Page.captureScreenshot", {"format": "jpeg", "quality": 92,
                                                      "clip": {"x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1}}, timeout=180)
            (a.out / f"f{i:05d}.jpg").write_bytes(base64.b64decode(res["data"]))
            if i % 15 == 0:
                print(f"  {i}/{n}（{(time.time() - ts) / (i + 1):.2f} 秒/枚）", flush=True)
        log["frames"] = n
    finally:
        if h.get("proc") is not None:
            _stop(h["exe"], h, info, cdp)
        srv.shutdown()
        srv.server_close()
    (a.out / "log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    print("→", a.out, "残り Edge", info.get("leftover_after", 0))


if __name__ == "__main__":
    main()
