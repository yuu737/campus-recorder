"""待機画面の「再生」を mp4 に撮る（11/8、動画の素材。音なし、既定 1600×900）。

  python -I -X utf8 scripts/record_replay.py --building <棟 id>
  python -I -X utf8 scripts/record_replay.py --building <棟 id> --speed 1.5 --theme dark --max-seconds 200

ページは `recorder serve` が配る web/progress.html?building=<棟>&replay=1 をそのまま撮るだけ（絵も数も作らない）。
headless の Edge（recorder.shots.headless_browser。GPU の鍵を持つ）で開き、Page.captureScreenshot を約 10 fps で
繰り返して枠を集め、撮った時刻に合わせて枠を複製して一定の fps の mp4 に書く（動画の長さ＝実時間）。
書き手は OpenCV の VideoWriter（Windows は MSMF の avc1、無ければ mp4v）。ffmpeg があれば libx264 で小さく書き直す。
終わりの判定はページの印 window.__replay（progress.js が場面ごとに書く。最後の場面の時間が過ぎると ended: true、
見せる生成物が無ければ empty: true）。--max-seconds は安全の上限（そこで打ち切る）。
GPU の鍵は録画の間ずっと持つ。Blender が待つので、長い本撮りは他の工程が止まっている時に。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from recorder.ledger import lock  # noqa: E402
from recorder.shots import headless_browser  # noqa: E402

READY_JS = "JSON.stringify(window.__replay || null)"


def parse() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="待機画面の再生を mp4 に撮る")
    ap.add_argument("--building", required=True, help="棟 id（?building= に渡す）")
    ap.add_argument("--out", default=None, help="出力（既定 out/video/replay_<棟>.mp4）")
    ap.add_argument("--speed", type=float, default=1.0, help="再生の速さ（&speed=。2 で倍速）")
    ap.add_argument("--theme", choices=["light", "dark"], default="light")
    ap.add_argument("--max-seconds", type=float, default=300.0, help="録画の上限（秒）。再生が終わらなくてもここで止める")
    ap.add_argument("--base", default="http://localhost:8767", help="recorder serve の URL")
    ap.add_argument("--size", default="1600x900", help="画面と動画の大きさ（幅x高さ）")
    ap.add_argument("--fps", type=int, default=30, help="動画の fps（撮る速さは --capture-fps、間は枠の複製で埋める）")
    ap.add_argument("--capture-fps", type=float, default=10.0, help="枠を撮る速さの目安")
    ap.add_argument("--tail", type=float, default=1.0, help="再生が終わった後に最後の絵を残す秒数")
    ap.add_argument("--wait-gpu", type=float, default=600.0, help="GPU の鍵を待つ上限（秒）")
    ap.add_argument("--ready-timeout", type=float, default=60.0, help="最初の場面が出るのを待つ上限（秒）")
    ap.add_argument("--no-shrink", action="store_true", help="ffmpeg での書き直し（小さくする）をしない")
    return ap.parse_args()


def open_writer(path: Path, fps: int, size: tuple[int, int]):
    """avc1（H.264、Windows の Media Foundation 経由）が使えればそれ、だめなら mp4v（FFMPEG 経由）。
    OpenCV の FFMPEG 側の avc1 は OpenH264 の DLL が無いと失敗して騒ぐので、avc1 は MSMF に限って試す。"""
    tries = ([(cv2.CAP_MSMF, "avc1")] if os.name == "nt" else []) + [(cv2.CAP_FFMPEG, "mp4v"), (cv2.CAP_ANY, "mp4v")]
    for api, cc in tries:
        w = cv2.VideoWriter(str(path), api, cv2.VideoWriter_fourcc(*cc), fps, size)
        if w.isOpened():
            return w, cc
        w.release()
    raise RuntimeError("mp4 の書き手（avc1 / mp4v）が開けない")


def shrink(path: Path) -> str | None:
    """ffmpeg（libx264）があれば H.264 の crf 18 で書き直して小さくする（MSMF の avc1 は約 40 Mbps と大きい）。
    中身は同じ枠。ffmpeg が無い・失敗した時は元のまま（None）。"""
    exe = shutil.which("ffmpeg")
    if not exe:
        return None
    tmp = path.with_suffix(".x264.mp4")
    r = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-c:v", "libx264", "-crf", "18",
                        "-preset", "medium", "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", str(tmp)],
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return None
    os.replace(tmp, path)
    return "libx264 crf 18"


def record(cdp, url: str, a: argparse.Namespace) -> dict:
    """ページを開き、最初の場面が出た時を 0 秒として枠（JPEG のバイト列と時刻）を集める。"""
    cdp.call("Page.navigate", {"url": url})
    t0 = time.time()
    state = None
    while time.time() - t0 < a.ready_timeout:
        try:
            state = json.loads(cdp.evaluate(READY_JS) or "null")
        except (RuntimeError, ValueError):
            state = None
        if state:
            break
        time.sleep(0.05)
    if not state:
        raise RuntimeError(f"{a.ready_timeout:.0f} 秒待っても再生が始まらない（window.__replay が無い。serve と棟 id を確かめる）")
    if state.get("empty"):
        raise RuntimeError("見せる生成物が無い（ページが空の表示）。撮らない")

    frames: list[tuple[float, bytes]] = []
    scenes: list[tuple[float, str]] = []
    step = 1.0 / a.capture_fps
    start = time.time()
    ended_at = None
    reason = "max-seconds"
    while True:
        tc = time.time()
        r = cdp.call("Page.captureScreenshot", {"format": "jpeg", "quality": 92}, timeout=60)
        tm = (tc + time.time()) / 2 - start            # 撮った時刻は呼んだ前後の中ほど
        frames.append((max(0.0, tm), base64.b64decode(r["data"])))
        try:
            st = json.loads(cdp.evaluate(READY_JS) or "null") or {}
        except (RuntimeError, ValueError):
            st = {}
        if st.get("scene") and (not scenes or scenes[-1][1] != st["scene"]):
            scenes.append((round(time.time() - start, 2), st["scene"]))
        now = time.time() - start
        if st.get("ended") and ended_at is None:
            ended_at = now
        if ended_at is not None and now >= ended_at + a.tail:
            reason = "ended"
            break
        if now >= a.max_seconds:
            break
        rest = step - (time.time() - tc)
        if rest > 0:
            time.sleep(rest)
    return {"frames": frames, "scenes": scenes, "length": time.time() - start, "ended_at": ended_at, "reason": reason}


def write(rec: dict, out: Path, fps: int, size: tuple[int, int]) -> dict:
    """時刻に合わせて枠を複製し、長さ＝実時間の mp4 を書く。"""
    frames = rec["frames"]
    n_out = max(1, round(rec["length"] * fps))
    out.parent.mkdir(parents=True, exist_ok=True)
    w, cc = open_writer(out, fps, size)
    j, cur_j, img = 0, -1, None
    try:
        for k in range(n_out):
            t = k / fps
            while j + 1 < len(frames) and frames[j + 1][0] <= t:
                j += 1
            if j != cur_j:
                img = cv2.imdecode(np.frombuffer(frames[j][1], np.uint8), cv2.IMREAD_COLOR)
                if img.shape[1] != size[0] or img.shape[0] != size[1]:
                    img = cv2.resize(img, size, interpolation=cv2.INTER_AREA)
                cur_j = j
            w.write(img)
    finally:
        w.release()
    return {"codec": cc, "frames_out": n_out}


def main() -> int:
    a = parse()
    W, H = (int(v) for v in a.size.lower().split("x"))
    out = Path(a.out) if a.out else ROOT / "out" / "video" / f"replay_{a.building}.mp4"
    q = f"building={a.building}&replay=1&speed={a.speed:g}" + ("&theme=dark" if a.theme == "dark" else "")
    url = f"{a.base.rstrip('/')}/web/progress.html?{q}"
    print(f"撮る：{url}（{W}×{H}、上限 {a.max_seconds:g} 秒）", flush=True)
    info: dict = {}
    t_lock = time.time()
    with lock("gpu", label="record_replay", timeout=a.wait_gpu):   # 待つ上限つきで先に取る（中の headless_browser は同じ鍵に入り直す）
        waited = time.time() - t_lock
        with headless_browser((W, H), info) as cdp:
            rec = record(cdp, url, a)
    wr = write(rec, out, a.fps, (W, H))
    raw_mb = out.stat().st_size / 1e6
    if not a.no_shrink:
        sh = shrink(out)
        if sh:
            wr["codec"] += f" → {sh}（{raw_mb:.1f} MB から）"
    cap = len(rec["frames"])
    size_mb = out.stat().st_size / 1e6
    if rec["reason"] == "ended":
        print(f"終わり方：再生が最後まで行った（再生の全長 {rec['ended_at']:.1f} 秒、その後 {a.tail:g} 秒残した）")
    else:
        print(f"終わり方：上限 {a.max_seconds:g} 秒で打ち切り（再生は終わっていない）")
    print(f"動画：{out}")
    print(f"  長さ {wr['frames_out'] / a.fps:.1f} 秒（実時間 {rec['length']:.1f} 秒）、枠 {wr['frames_out']}（{a.fps} fps、{wr['codec']}）、"
          f"撮った枠 {cap}（平均 {cap / max(rec['length'], 1e-6):.1f} fps）、大きさ {size_mb:.1f} MB")
    print("  場面：" + "、".join(f"{k}@{t:g}s" for t, k in rec["scenes"]))
    print(f"  GPU の鍵を待った {waited:.1f} 秒" + (f"、残ったブラウザを止めた {info['leftover_killed']}" if info.get("leftover_killed") else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
