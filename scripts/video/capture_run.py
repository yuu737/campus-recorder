"""実走（API で 1 棟を 0 から立てる）の待機画面を、走っている間ずっと撮る（11/8 午後）。

前提：別の作業ツリー（例 D:\\BuiltwithClaude_run0）で `recorder serve --port 8768` が立っていて、職員の操作で記録が始まる。
この道具はページを開いて撮るだけ。serve には GET しかしない（記録を始めたり止めたりしない）。

使い方（作業ツリーを cwd にすると、--out の相対パスは作業ツリーの out/ の下になる）:
    python -I -X utf8 D:\\BuiltwithClaude\\scripts\\video\\capture_run.py --port 8768 --building honbu --out out/video/run0/
    python -I -X utf8 ... --until 17:30          # この時刻で打ち切る（既定なし。打ち切りは人が決めて Ctrl+C）
    python -I -X utf8 ... --probe                # 1 回だけ：staff と待機画面を 1 枚ずつ撮って終わる（serve の確認用）

撮る物（--out の下）:
  staff_<時刻>.jpg            開始時に職員の画面（web/staff.html）を 1 枚
  stills/progress_<時刻>.jpg  30 秒おきに待機画面（web/progress.html?building=<棟>&live=1）、1920×1080
  clips/<時刻>_<工程>.mp4     工程が変わった瞬間（/api/pipeline の state.current か、/api/progress の run.last_step が変わった時）
                              から 10 秒間、10 fps（コマは clips/<時刻>_<工程>/f000.jpg …。ffmpeg が無ければコマだけ）
  walk_<時刻>.jpg             終了時に歩く画面（web/index.html?building=<棟>）を 1 枚
  log.jsonl                   1 分おきに /api/pipeline の値（status・current・費用・上限・済んだ工程・経過）と、工程の変わり目
終わる時：state.status が done、Ctrl+C（終わりの静止画と歩く画面を撮ってから終わる）、--until を渡した時はその時刻、
--stop-grace を渡した時は stopped のままその秒数。既定では stopped になっても撮り続ける（再開するか打ち切るかは人が決める）。

GPU の鍵（recorder.ledger の lock("gpu")）は取らない。Edge は --disable-gpu で別に起動する（Blender と取り合わないため。
静止画と短い動画だけ）。8767（本物の data の serve）には繋がない（--port 8767 は断る）。
"""
from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "video"))

from recorder.shots import CDP, _stop  # noqa: E402
from render_html import launch  # noqa: E402

FORBIDDEN_PORT = 8767   # 本物の data の serve


def _ts() -> str:
    return datetime.now().strftime("%H%M%S")


def get_json(base: str, path: str, timeout: float = 10) -> dict | None:
    try:
        with urllib.request.urlopen(base + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 — serve が一時的に答えなくても撮り続ける
        return None


def shoot(cdp: CDP, path: Path, quality: int = 90) -> bool:
    try:
        r = cdp.call("Page.captureScreenshot", {"format": "jpeg", "quality": quality}, timeout=60)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 撮れない {path.name}: {e}", flush=True)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(r["data"]))
    return True


def goto(cdp: CDP, url: str, settle: float) -> None:
    cdp.call("Page.navigate", {"url": url})
    time.sleep(settle)


def snapshot(base: str, b: str) -> dict:
    """/api/pipeline と /api/progress から、工程の変わり目と記録に要る値だけ。"""
    p = get_json(base, f"/api/pipeline?building={b}") or {}
    g = get_json(base, f"/api/progress?buildings={b}") or {}
    st = p.get("state") or {}
    rec = p.get("record") or {}
    run = ((g.get("buildings") or {}).get(b) or {}).get("run") or {}
    live = p.get("live") or {}
    return {"reachable": bool(p), "status": st.get("status"), "current": st.get("current"),
            "spent_usd": st.get("spent_usd"), "max_usd": st.get("max_usd"), "done": st.get("done"),
            "started_at": st.get("started_at"), "updated_at": st.get("updated_at"),
            "auto_resumes": st.get("auto_resumes"), "stopped": st.get("stopped"),
            "record_cost_usd": rec.get("cost_usd"), "record_wall_seconds": rec.get("wall_seconds"),
            "record_file": rec.get("file"), "live_phase": live.get("phase"), "live_label": live.get("label"),
            "run_state": run.get("state"), "run_last_step": run.get("last_step")}


def step_key(s: dict) -> tuple:
    return (s.get("current"), s.get("run_last_step"), s.get("status"))


def clip(cdp: CDP, out_dir: Path, name: str, seconds: float, fps: int) -> Path | None:
    d = out_dir / "clips" / name
    d.mkdir(parents=True, exist_ok=True)
    n = int(seconds * fps)
    t0 = time.monotonic()
    got = 0
    for i in range(n):
        target = t0 + i / fps
        while time.monotonic() < target:
            time.sleep(0.005)
        if shoot(cdp, d / f"f{got:03d}.jpg", quality=85):
            got += 1
    real = time.monotonic() - t0
    eff = max(1.0, got / real) if real > 0 else fps   # 撮れた速さ（遅ければ実時間に合わせて詰める）
    ff = shutil.which("ffmpeg")
    if not ff or got == 0:
        return None
    mp4 = out_dir / "clips" / f"{name}.mp4"
    subprocess.Popen([ff, "-y", "-loglevel", "error", "-framerate", f"{eff:.3f}", "-i", str(d / "f%03d.jpg"),
                      "-vf", f"fps={fps},format=yuv420p", "-c:v", "libx264", "-crf", "20", str(mp4)],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return mp4


def main() -> int:
    ap = argparse.ArgumentParser(prog="capture_run")
    ap.add_argument("--port", type=int, default=8768)
    ap.add_argument("--building", default="honbu")
    ap.add_argument("--out", type=Path, default=Path("out/video/run0"))
    ap.add_argument("--until", default=None, help="打ち切る時刻 HH:MM（今日）。既定なし")
    ap.add_argument("--still-every", type=float, default=30.0)
    ap.add_argument("--log-every", type=float, default=60.0)
    ap.add_argument("--poll", type=float, default=2.0, help="工程の変わり目を見る間隔（秒）")
    ap.add_argument("--clip-seconds", type=float, default=10.0)
    ap.add_argument("--clip-fps", type=int, default=10)
    ap.add_argument("--stop-grace", type=float, default=0.0,
                    help="stopped のまま何秒で終わるか。0（既定）は終わらずに撮り続ける")
    ap.add_argument("--probe", action="store_true")
    a = ap.parse_args()
    if a.port == FORBIDDEN_PORT:
        print("8767 は本物の data の serve です。繋ぎません（--port 8768）")
        return 2

    base = f"http://127.0.0.1:{a.port}"
    b = a.building
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    until = None
    if a.until:
        hh, mm = (int(x) for x in a.until.split(":"))
        until = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
    logf = out / "log.jsonl"

    def log(kind: str, **kw) -> None:
        row = {"at": datetime.now().isoformat(timespec="seconds"), "kind": kind, **kw}
        with logf.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(json.dumps(row, ensure_ascii=False), flush=True)

    if get_json(base, "/api/site") is None:
        print(f"{base} の serve に届きません。作業ツリーで serve --port {a.port} を立ててから")
        return 2

    progress_url = f"{base}/web/progress.html?building={b}&live=1"
    h: dict = {}
    info: dict = {}
    cdp = None
    end_reason = None
    try:
        ws = launch((1920, 1080), h)
        cdp = CDP(ws)
        cdp.call("Page.enable")
        cdp.call("Emulation.setDeviceMetricsOverride", {"width": 1920, "height": 1080, "deviceScaleFactor": 1, "mobile": False})
        goto(cdp, f"{base}/web/staff.html", 5.0)
        shoot(cdp, out / f"staff_{_ts()}.jpg")
        goto(cdp, progress_url, 6.0)
        shoot(cdp, out / "stills" / f"progress_{_ts()}.jpg")
        s = snapshot(base, b)
        log("start", port=a.port, building=b, until=until.isoformat(timespec="minutes") if until else None, **s)
        if a.probe:
            end_reason = "probe"
            return 0

        last_key = step_key(s)
        seen_running = s.get("status") == "running" or s.get("run_state") == "running"
        stopped_since = None
        t_still = t_log = time.monotonic()
        while True:
            now = time.monotonic()
            if until and datetime.now() >= until:
                end_reason = f"until {a.until}"
                break
            s = snapshot(base, b)
            if s.get("status") == "running" or s.get("run_state") == "running":
                seen_running = True
            k = step_key(s)
            if s["reachable"] and k != last_key:
                name = f"{_ts()}_{s.get('current') or s.get('run_last_step') or s.get('status') or 'none'}"
                log("step", prev=list(last_key), **s)
                last_key = k
                mp4 = clip(cdp, out, name, a.clip_seconds, a.clip_fps)
                log("clip", name=name, mp4=str(mp4) if mp4 else None)
                now = time.monotonic()
            if now - t_still >= a.still_every:
                shoot(cdp, out / "stills" / f"progress_{_ts()}.jpg")
                t_still = now
            if now - t_log >= a.log_every:
                log("tick", **s)
                t_log = now
            if seen_running and s.get("status") == "done" and s.get("run_state") != "running":
                end_reason = "done"
                break
            if a.stop_grace > 0 and seen_running and s.get("status") == "stopped" and s.get("run_state") != "running":
                stopped_since = stopped_since or now
                if now - stopped_since >= a.stop_grace:
                    end_reason = "stopped"
                    break
            else:
                stopped_since = None
            time.sleep(a.poll)

        shoot(cdp, out / "stills" / f"progress_{_ts()}.jpg")
        goto(cdp, f"{base}/web/index.html?building={b}", 20.0)
        shoot(cdp, out / f"walk_{_ts()}.jpg")
        log("end", reason=end_reason, **snapshot(base, b))
        return 0
    except KeyboardInterrupt:
        end_reason = "ctrl+c"
        try:   # 人が打ち切った時も、終わりの静止画と歩く画面は残す
            if cdp is not None:
                shoot(cdp, out / "stills" / f"progress_{_ts()}.jpg")
                goto(cdp, f"{base}/web/index.html?building={b}", 20.0)
                shoot(cdp, out / f"walk_{_ts()}.jpg")
        except BaseException:  # noqa: BLE001 — 2 回目の Ctrl+C ならすぐ終わる
            pass
        try:
            log("end", reason=end_reason, **snapshot(base, b))
        except Exception:  # noqa: BLE001
            pass
        return 130
    finally:
        if h.get("exe"):
            _stop(Path(h["exe"]), h, info, cdp)


if __name__ == "__main__":
    sys.exit(main())
