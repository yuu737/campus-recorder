"""締め C1 の撮り直し：歩く画面を「目の高さのまま前へ歩く」カメラで 1/30 秒ずつ撮る（縦、連番 PNG）。
前の capture_walk.py（out/video/closing_assets/）は終わりが空から見下ろす構図で「宙に浮く」ので、
高さはページ自身の地面追従（groundAt + EYE）に任せ、x・y と向きだけを動かす。ページは書き換えない。
使い方：python -I -X utf8 scripts/video/capture_walk_eye.py test | run [frames]
"""
import base64, json, math, sys, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from recorder.shots import headless_browser, serve, WALK_READY_JS  # noqa: E402

OUT = REPO / "out" / "video" / "closing_assets"
START = [-33.21, -67.00, 43.66, 4.72, 15.59, 77.10]   # web_cameras.street（本部棟 f6 の正面）
SPEED = 1.6      # m/s（人の歩き）
FPS = 30
W, H, DPR = 540, 960, 2

def cam_at(t):
    """t 秒後：yaw の向きへ歩く。見上げは 15.6°→9°、向きは +3° ゆっくり振る。高さはページ任せ（0）。"""
    yaw0 = START[3]
    yaw = yaw0 + 3.0 * (t * t * (3 - 2 * t) if t <= 1 else 1) if False else yaw0 + 3.0 * math.sin(t / 8.0 * math.pi / 2)
    d = SPEED * t
    x = START[0] + math.sin(math.radians(yaw0)) * d
    y = START[1] + math.cos(math.radians(yaw0)) * d
    pitch = START[4] - 3.0 * (t / 8.0)
    return [x, y, 0, yaw, pitch, START[5]]

HOOK = r"""(() => { const C = window.__campus; if (C.__hooked) return 'again';
 const apply = () => { const v = window.__vc; if (!v) return; const cam = C.camera;
   cam.position.x = v[0]; cam.position.z = -v[1];
   cam.rotation.set(v[4] * Math.PI / 180, -v[3] * Math.PI / 180, 0);
   if (cam.fov !== v[5]) { cam.fov = v[5]; cam.updateProjectionMatrix(); } };
 const fx = C.look();
 if (fx) { const o = fx.render; fx.render = () => { apply(); o(); }; }
 else { const r = C.renderer, o = r.render.bind(r); r.render = (s, c) => { if (c === C.camera) apply(); o(s, c); }; }
 C.__hooked = true; return fx ? 'look' : 'plain'; })()"""

def ev(cdp, expr, await_=False):
    r = cdp.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": await_}, timeout=120)
    return (r.get("result") or {}).get("value")

def set_cam(cdp, v):
    ev(cdp, f"(() => {{ window.__vc = {json.dumps(v)}; window.__campus.player.set({v[0]}, 0, {-v[1]}); }})()")
    ev(cdp, "new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => requestAnimationFrame(() => r(1)))))", True)

def shot(cdp, png):
    r = cdp.call("Page.captureScreenshot", {"format": "png"}, timeout=120)
    png.parent.mkdir(parents=True, exist_ok=True)
    png.write_bytes(base64.b64decode(r["data"]))

def run(frames, test=False):
    srv, port = serve(REPO)
    info = {}
    log = {"mode": "portrait_eye", "css": [W, H], "dpr": DPR, "start": START, "speed_mps": SPEED, "frames": frames, "fps": FPS,
           "height": "ページの地面追従（groundAt + EYE）に任せた"}
    t0 = time.time()
    try:
        with headless_browser((W, H), info) as cdp:
            cdp.call("Emulation.setDeviceMetricsOverride", {"width": W, "height": H, "deviceScaleFactor": DPR, "mobile": True})
            cam = ",".join(f"{x:.2f}" for x in START)
            url = f"http://127.0.0.1:{port}/web/index.html?cam={cam}&ui=0&boxes=0"
            log["url"] = url.replace(f"127.0.0.1:{port}", "127.0.0.1:<port>")
            cdp.call("Page.navigate", {"url": url})
            t1 = time.time()
            while time.time() - t1 < 120:
                try:
                    if ev(cdp, WALK_READY_JS):
                        break
                except RuntimeError:
                    pass
                time.sleep(0.5)
            time.sleep(4)
            log["hook"] = ev(cdp, HOOK)
            # ?cam= の固定視点を外す（W を 1 コマ押す→歩き出すと fixed が外れ、高さが groundAt + EYE になる）
            ev(cdp, "dispatchEvent(new KeyboardEvent('keydown', {code: 'KeyW'}))")
            ev(cdp, "new Promise(r => requestAnimationFrame(() => requestAnimationFrame(() => r(1))))", True)
            ev(cdp, "dispatchEvent(new KeyboardEvent('keyup', {code: 'KeyW'}))")
            if test:
                for name, t in (("start", 0.0), ("end", (frames - 1) / FPS)):
                    set_cam(cdp, cam_at(t)); time.sleep(1.5); shot(cdp, OUT / f"test_eye_{name}.png")
                log["cam_y"] = ev(cdp, "window.__campus.camera.position.y")
                return log
            d = OUT / "walk_portrait_eye"
            for i in range(frames):
                set_cam(cdp, cam_at(i / FPS))
                if i == 0:
                    time.sleep(2)
                shot(cdp, d / f"f{i:04d}.png")
            log["cam_y_end"] = ev(cdp, "window.__campus.camera.position.y")
    finally:
        srv.shutdown(); srv.server_close()
        log["seconds"] = round(time.time() - t0, 1)
        log.update({k: v for k, v in info.items() if k != "launch_errors"} if info else {})
    (OUT / ("walk_portrait_eye_test.json" if test else "walk_portrait_eye.json")).write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    return log

if __name__ == "__main__":
    if sys.argv[1] == "test":
        print(run(240, test=True))
    else:
        print(run(int(sys.argv[2]) if len(sys.argv) > 2 else 240))
