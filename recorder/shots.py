"""進捗報告の絵を道具で撮る（手で撮らない・窓を一切出さない）。

  3 段目の証拠ビューア：out/<棟>/evidence_<種>.png（種は three／overview／photo）
      Rerun の Web 版（rerun-sdk の serve_web_viewer を窓なしの子プロセスで。ブラウザは開かない）に「データ＋その種の画面（.rbl）」を ?url= で読ませ、headless の Edge で撮る。
  5 段目の歩く画面：out/<棟>/walk.png
      web/index.html を headless の Edge で撮る。構図は model_report.json の web_cameras.street.query、
      無ければ棟の足元の中心から南西 40 m・目の高さ 1.6 m で中心を向く既定。読み込みの終わりは #loading が消えた事で判定。

  python -m recorder shots data/buildings/honbu.json [--evidence] [--walk]   （どちらも付けなければ両方）

窓を出さない決まり（11/6 利用者の苦情「画面が開いたり閉じたりでちかちかする」）：
  - `rerun … --screenshot-to` はビューアの窓が開くので使わない。`rerun --serve-web` もいつものブラウザにタブを開くので使わない。
  - 子プロセスは全部 CREATE_NO_WINDOW。Edge は --headless=new ＋ 画面の外（--window-position=-32000,-32000）。
  - Edge の操作は DevTools の口（CDP、標準ライブラリだけの小さな WebSocket）で行い、撮れたら Browser.close。
手順書の決まり：headless の Edge は使い終えたら必ず終了する。専用の --user-data-dir で起動し、終了を待ったあと、
その印を持つ msedge.exe が残っていないことを確かめる（残っていれば止める）。絵は描くだけで、値は作らない。
"""
from __future__ import annotations

import argparse
import base64
import functools
import http.server
import json
import math
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .schema import load_building

OUT_ROOT = Path("out")
RERUN = Path(".venv/Scripts/rerun.exe")
BROWSERS = [Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe")]
EVIDENCE_KINDS = ("three", "overview", "photo")
EVIDENCE_SIZE = (1920, 1080)
EVIDENCE_SETTLE = (8.0, 16.0, 30.0)      # Web 版が wasm を読み、.rrd を読み終えるまでの待ち（秒）。空なら次の待ちで撮り直す
WALK_SIZE = (1400, 1000)
WALK_READY_TIMEOUT = 90.0                # #loading が消えるまでの上限（秒）
WALK_SETTLE = (2.5, 8.0)                 # 消えてから影・後処理が落ち着くまで（秒）。真っ白なら 1 回だけ伸ばす
DEFAULT_BACK_M, DEFAULT_EYE_M, DEFAULT_FOV = 40.0, 1.6, 60.0
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_browser() -> Path | None:
    for p in BROWSERS:
        if p.exists():
            return p
    return None


def rerun_exe(root: Path = Path(".")) -> Path | None:
    p = root / RERUN
    if p.exists():
        return p
    w = shutil.which("rerun")
    return Path(w) if w else None


def _run(cmd: list[str], timeout: float = 120, **kw) -> subprocess.CompletedProcess:
    """窓を出さずに子プロセスを走らせて終了を待つ。"""
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=NO_WINDOW, **kw)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# --- 絵の判定 -------------------------------------------------------------------------------

def _png_size(png: Path) -> list[int] | None:
    try:
        import struct
        with open(png, "rb") as f:
            head = f.read(24)
        return list(struct.unpack(">II", head[16:24]))
    except Exception:  # noqa: BLE001
        return None


def is_blank(png: Path, frac: float = 0.97) -> bool:
    """ほとんど 1 色（読み込み中の画面・真っ白）なら True。"""
    import cv2
    import numpy as np

    img = cv2.imdecode(np.fromfile(str(png), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return True
    small = cv2.resize(img, (200, 140), interpolation=cv2.INTER_AREA).reshape(-1, 3) // 8
    _, counts = np.unique(small, axis=0, return_counts=True)
    return counts.max() / len(small) >= frac


def viewer_empty(png: Path) -> bool:
    """Rerun の画面が出る前に撮れた（読み込み中の黒一色・中央が白一色の空のビューア）なら True。"""
    import cv2
    import numpy as np

    if is_blank(png):
        return True
    img = cv2.imdecode(np.fromfile(str(png), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    h, w = img.shape
    c = img[int(h * 0.08):int(h * 0.7), int(w * 0.08):int(w * 0.7)]
    return float((c > 235).mean()) > 0.9 or float((c < 30).mean()) > 0.97


# --- 一時の配信 ------------------------------------------------------------------------------

class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):  # noqa: D401 — 配信の記録は出さない
        pass

    def end_headers(self):      # Rerun の Web 版（別のポート）から .rrd を読めるように
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()


def serve(root: Path):
    """root を空きポートで一時配信する。返り値は（server, port）。止めるのは呼んだ側（shutdown・server_close）。"""
    h = functools.partial(_Quiet, directory=str(Path(root).resolve()))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


# --- headless の Edge（CDP） ----------------------------------------------------------------

class CDP:
    """DevTools の口への小さな WebSocket（標準ライブラリだけ。送る枠はマスク付き、受ける枠は断片も扱う）。"""

    def __init__(self, ws_url: str, timeout: float = 60):
        rest = ws_url.split("://", 1)[1]
        hostport, path = rest.split("/", 1)
        host, port = hostport.rsplit(":", 1)
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("DevTools の口が閉じた")
            buf += chunk
        head, self._buf = buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head[:200].decode(errors="replace"))
        self._id = 0

    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self._buf)))
            if not chunk:
                raise ConnectionError("DevTools の口が閉じた")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send_frame(self, payload: bytes, opcode: int = 1) -> None:
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + n.to_bytes(2, "big")
        else:
            head += bytes([0x80 | 127]) + n.to_bytes(8, "big")
        mask = os.urandom(4)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def _recv_msg(self) -> dict | None:
        data = b""
        while True:
            b0, b1 = self._read(2)
            op, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = int.from_bytes(self._read(2), "big")
            elif n == 127:
                n = int.from_bytes(self._read(8), "big")
            if b1 & 0x80:
                mask = self._read(4)
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(self._read(n)))
            else:
                payload = self._read(n)
            if op == 8:
                raise ConnectionError("DevTools の口が閉じた")
            if op == 9:
                self._send_frame(payload, 10)
                continue
            if op in (0, 1, 2):
                data += payload
                if b0 & 0x80:
                    return json.loads(data.decode("utf-8"))

    def call(self, method: str, params: dict | None = None, timeout: float = 60) -> dict:
        self._id += 1
        mid = self._id
        self.sock.settimeout(timeout)
        self._send_frame(json.dumps({"id": mid, "method": method, "params": params or {}}).encode())
        while True:
            m = self._recv_msg()
            if m and m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})

    def evaluate(self, expr: str):
        r = self.call("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        return (r.get("result") or {}).get("value")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def _procs_with(mark: str, name: str) -> list[int]:
    """コマンドラインに mark を含む name のプロセス id（Windows：Get-CimInstance。窓なし）。"""
    if os.name != "nt":
        return []
    ps = (f"Get-CimInstance Win32_Process -Filter \"Name='{name}'\" | "
          f"Where-Object {{ $_.CommandLine -like '*{mark}*' }} | ForEach-Object {{ $_.ProcessId }}")
    try:
        r = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], timeout=30)
        return [int(s) for s in r.stdout.split() if s.strip().isdigit()]
    except Exception:  # noqa: BLE001
        return []


@contextmanager
def headless_browser(size: tuple[int, int], info: dict | None = None):
    """headless の Edge（無ければ Chrome）を窓なし・画面の外で立て、ページの CDP を渡す。抜ける時に必ず終了させ、
    残りが無いことを確かめる（info に leftover_killed／leftover_after を書く）。
    GPU は 1 枚なので、Blender と同じ鍵（ledger.lock("gpu")）を持って 1 つずつ走る。"""
    from .ledger import lock
    with lock("gpu", label="headless browser"):
        with _headless_browser(size, info) as cdp:
            yield cdp


DEVTOOLS_WAIT_S = 60.0     # DevTools の口（DevToolsActivePort と /json/list）を待つ上限（秒）。J-030 で 30 → 60
LAUNCH_TRIES = 2           # 口が開かなければ、新しいプロファイルでもう 1 回だけ立て直す（J-030）
STDERR_TAIL = 1500         # 報告に残す標準エラーの末尾（文字）


class LaunchFail(RuntimeError):
    """headless の Edge が DevTools の口を開かなかった（1 回分）。"""


def _stderr_tail(errf: Path | None, n: int = STDERR_TAIL) -> str:
    try:
        return errf.read_bytes().decode("utf-8", "replace")[-n:].strip() if errf and errf.exists() else ""
    except OSError:
        return ""


def _launch(exe: Path, size: tuple[int, int], wait: float, h: dict) -> str:
    """専用のプロファイルで Edge を立て、ページの WebSocket の URL を返す。標準エラーはプロファイルの外のファイルへ
    （h["errf"]。報告に残すため）。口が開かなければ LaunchFail（片付けは呼ぶ側。h に proc・prof・errf を入れておく）。"""
    prof = Path(tempfile.mkdtemp(prefix="recorder_shot_"))
    errf = prof.parent / f"{prof.name}.stderr.txt"
    h.update(prof=prof, errf=errf)
    cmd = [str(exe), "--headless=new", "--no-first-run", "--no-default-browser-check", "--hide-scrollbars",
           "--disable-extensions", "--mute-audio", "--ignore-gpu-blocklist", f"--user-data-dir={prof}",
           f"--window-size={size[0]},{size[1]}", "--window-position=-32000,-32000",
           "--remote-debugging-port=0", "--remote-allow-origins=*", "about:blank"]
    with open(errf, "wb") as eh:
        h["proc"] = proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=eh, stdin=subprocess.DEVNULL,
                                            creationflags=NO_WINDOW)
    port_file = prof / "DevToolsActivePort"
    t0 = time.time()
    port = None
    while port is None:
        try:                                  # Edge が書いている途中は読めない（PermissionError）・空のことがある
            txt = port_file.read_text().split()
            port = int(txt[0]) if txt else None
        except (OSError, ValueError):
            port = None
        if port is None:
            if proc.poll() is not None:
                raise LaunchFail(f"Edge が口を開く前に終わった（終了コード {proc.returncode}、{time.time() - t0:.1f} 秒）")
            if time.time() - t0 > wait:
                raise LaunchFail(f"{wait:.0f} 秒待っても DevToolsActivePort が書かれない")
            time.sleep(0.1)
    ws, last = None, ""
    while ws is None:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as r:
                pages = [t for t in json.loads(r.read()) if t.get("type") == "page"]
            ws = pages[0]["webSocketDebuggerUrl"] if pages else None
        except (OSError, ValueError) as e:
            last = f"{type(e).__name__}: {e}"
        if ws is None:
            if proc.poll() is not None or time.time() - t0 > wait:
                raise LaunchFail(f"口（{port}）にページが出ない（{time.time() - t0:.0f} 秒{'、' + last if last else ''}）")
            time.sleep(0.2)
    return ws


def _stop(exe: Path, h: dict, info: dict, cdp=None) -> None:
    """Edge を閉じ、同じプロファイルの印を持つ msedge.exe が残っていれば止め、プロファイルと標準エラーのファイルを消す。"""
    if cdp is not None:
        try:
            cdp.call("Browser.close", timeout=5)
        except Exception:  # noqa: BLE001 — 閉じる時に口が切れるのは正常
            pass
        cdp.close()
    proc, prof = h.get("proc"), h.get("prof")
    if proc is not None:
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        time.sleep(0.5)
    if prof is not None:
        left = _procs_with(prof.name, exe.name)
        for pid in left:
            _run(["taskkill", "/F", "/T", "/PID", str(pid)], timeout=30)
        if left:
            time.sleep(1.0)
        info["leftover_killed"] = info.get("leftover_killed", 0) + len(left)
        info["leftover_after"] = info.get("leftover_after", 0) + len(_procs_with(prof.name, exe.name))
        shutil.rmtree(prof, ignore_errors=True)
    if h.get("errf") is not None:
        h["stderr_tail"] = _stderr_tail(h["errf"])
        try:
            h["errf"].unlink(missing_ok=True)
        except OSError:
            pass


@contextmanager
def _headless_browser(size: tuple[int, int], info: dict | None = None):
    """口が開かなければ（J-030）、その回の標準エラーの末尾・終了コードを info["launch_errors"] に残し、新しいプロファイルで
    もう 1 回だけ立て直す。2 回とも駄目なら、標準エラーの末尾を付けた RuntimeError（呼ぶ側が報告の errors に書く）。"""
    exe = find_browser()
    if exe is None:
        raise FileNotFoundError("Edge も Chrome も無い")
    info = info if info is not None else {}
    h: dict = {}
    ws = None
    for k in range(1, LAUNCH_TRIES + 1):
        h = {}
        t0 = time.time()
        try:
            ws = _launch(exe, size, DEVTOOLS_WAIT_S, h)
            break
        except LaunchFail as e:
            proc = h.get("proc")
            rec = {"try": k, "reason": str(e), "exit_code": proc.poll() if proc is not None else None,
                   "seconds": round(time.time() - t0, 1), "profile": h["prof"].name if h.get("prof") else None}
            _stop(exe, h, info)                       # 標準エラーは止めてから読む（書き終わっている）
            rec["stderr"] = h.pop("stderr_tail", "")
            info.setdefault("launch_errors", []).append(rec)
        except BaseException:
            _stop(exe, h, info)
            raise
    if ws is None:
        last = info["launch_errors"][-1]
        raise RuntimeError(f"headless の Edge が DevTools の口を開かなかった（{LAUNCH_TRIES} 回、毎回新しいプロファイル。"
                           f"最後：{last['reason']}。標準エラーの末尾：{last['stderr'] or '（空）'}）")
    cdp = None
    try:
        cdp = CDP(ws)
        cdp.call("Page.enable")
        cdp.call("Emulation.setDeviceMetricsOverride", {"width": size[0], "height": size[1], "deviceScaleFactor": 1, "mobile": False})
        yield cdp
    finally:
        _stop(exe, h, info, cdp)


def capture(cdp: CDP, url: str, png: Path, ready_js: str | None = None, ready_timeout: float = 60.0,
            settle: float = 2.0) -> dict:
    """url を開き、ready_js が真になる（または ready_timeout）まで待ち、さらに settle 秒待って png に撮る。"""
    t0 = time.time()
    cdp.call("Page.navigate", {"url": url})
    ready = ready_js is None
    while not ready and time.time() - t0 < ready_timeout:
        try:
            ready = bool(cdp.evaluate(ready_js))
        except RuntimeError:
            ready = False
        if not ready:
            time.sleep(0.5)
    time.sleep(settle)
    r = cdp.call("Page.captureScreenshot", {"format": "png"}, timeout=120)
    png.parent.mkdir(parents=True, exist_ok=True)
    png.write_bytes(base64.b64decode(r["data"]))
    return {"png": str(png), "ready": ready, "seconds": round(time.time() - t0, 1)}


# --- 証拠ビューア ---------------------------------------------------------------------------

def strip_blueprint(exe: Path, rrd: Path, dst: Path) -> Path | None:
    """rrd から画面（blueprint）の entity を全部落とした写しを作る（rerun rrd filter --drop-entity）。
    .rrd に入っている既定の画面（three）が .rbl より勝ってしまうため。"""
    import re

    try:
        pr = _run([str(exe), "rrd", "print", str(rrd)], timeout=60)
        ents = sorted({m.group(1) for m in re.finditer(r" - (/\S+) - data columns: \[([^\]]*)\]", pr.stdout)
                       if "Blueprint" in m.group(2)})
        if not ents:
            return None
        args = [a for e in ents for a in ("--drop-entity", e)]
        _run([str(exe), "rrd", "filter", *args, "-o", str(dst), str(rrd)], timeout=120)
        return dst if dst.exists() and dst.stat().st_size > 0 else None
    except Exception:  # noqa: BLE001
        return None


_WEB_VIEWER_PY = "; ".join([
    "import sys, time, rerun as rr",
    "rr.serve_web_viewer(web_port=int(sys.argv[1]), open_browser=False)",
    "print('ready', flush=True)",
    "time.sleep(float(sys.argv[2]))"])


@contextmanager
def rerun_web(python: str | None = None, life_s: float = 900):
    """Rerun の Web 版の配信だけを窓なしの子プロセスで立てる（rerun-sdk の serve_web_viewer、open_browser=False）。
    `rerun --serve-web` は利用者のいつものブラウザにタブを開いてしまうので使わない（11/6 に発生）。
    返り値は Web 版のポート。抜ける時に必ず止める（terminate → wait）。life_s 秒で自分でも終わる。"""
    import sys

    wp = _free_port()
    proc = subprocess.Popen([python or sys.executable, "-I", "-c", _WEB_VIEWER_PY, str(wp), str(life_s)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    threading.Thread(target=lambda: [None for _ in proc.stdout], daemon=True).start()   # 出力を読み捨てて詰まらせない
    try:
        t0 = time.time()
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{wp}/", timeout=1).close()
                break
            except Exception:  # noqa: BLE001
                if proc.poll() is not None or time.time() - t0 > 30:
                    raise RuntimeError("Rerun の Web 版の配信が立たなかった")
                time.sleep(0.2)
        yield wp
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def evidence_shots(building_id: str, out_dir: Path | None = None, building_json: Path | None = None,
                   root: Path = Path(".")) -> dict:
    """evidence.rrd（無ければ evidence.run で作る）を .rbl 3 種の画面で、窓なしで撮る。失敗は理由を返して続ける。"""
    out_dir = out_dir or root / OUT_ROOT / building_id
    exe = rerun_exe(root)
    res = {"kind": "evidence", "shots": {}, "errors": {}}
    if exe is None:
        res["errors"]["rerun"] = "rerun が無い（.venv/Scripts/rerun.exe も PATH も）"
        return res
    if find_browser() is None:
        res["errors"]["browser"] = "Edge も Chrome も無い"
        return res
    rrd = out_dir / "evidence.rrd"
    rbls = {k: out_dir / f"evidence_{k}.rbl" for k in EVIDENCE_KINDS}
    if not rrd.exists() or not all(p.exists() for p in rbls.values()):
        bj = building_json or root / "data" / "buildings" / f"{building_id}.json"
        try:
            from . import evidence
            evidence.run(bj, out=rrd)
        except Exception as e:  # noqa: BLE001 — 撮れない理由を返す
            res["errors"]["evidence.run"] = f"{type(e).__name__}: {e}"
            return res
    tmp = Path(tempfile.mkdtemp(prefix="recorder_rrd_"))
    srv = None
    info: dict = {}            # Edge の起動の記録（launch_errors・leftover_*）。失敗で抜けても報告に残す（J-030）
    try:
        data = strip_blueprint(exe, rrd, tmp / "data.rrd")
        if data is None:
            res["errors"]["strip"] = "既定の画面を抜けなかった"
            return res
        for k, rbl in rbls.items():
            shutil.copyfile(rbl, tmp / rbl.name)
        srv, port = serve(tmp)
        with rerun_web() as wp:
            for k, rbl in rbls.items():
                png = out_dir / f"evidence_{k}.png"
                t0 = time.time()
                # 画面を抜いたデータ＋その種の .rbl を ?url= で 2 つ渡す。ブラウザは種ごとに新しく立てる
                # （Web 版は最後の画面をブラウザの保存領域に覚えるので、前の種の画面が残らないように）
                url = (f"http://127.0.0.1:{wp}/?url=http://127.0.0.1:{port}/{data.name}"
                       f"&url=http://127.0.0.1:{port}/{rbl.name}")
                with headless_browser(EVIDENCE_SIZE, info) as cdp:
                    for n, settle in enumerate(EVIDENCE_SETTLE, 1):
                        try:
                            capture(cdp, url, png, "!!document.querySelector('canvas')", 30, settle)
                        except Exception as e:  # noqa: BLE001
                            res["errors"][k] = f"{type(e).__name__}: {e}"
                            break
                        if not viewer_empty(png):
                            res["errors"].pop(k, None)
                            res["shots"][k] = {"png": str(png), "size": _png_size(png), "seconds": round(time.time() - t0, 1), "tries": n}
                            break
                        res["errors"][k] = f"画面が出る前に撮れた（{n} 回、最後の待ち {settle} 秒）"
    except Exception as e:  # noqa: BLE001
        res["errors"]["evidence"] = f"{type(e).__name__}: {e}"
    finally:
        res.update(info)
        if srv is not None:
            srv.shutdown()
            srv.server_close()
        shutil.rmtree(tmp, ignore_errors=True)
    return res


# --- 歩く画面 -----------------------------------------------------------------------------

def polygon_centroid(poly: list[list[float]]) -> tuple[float, float]:
    """多角形の面積の重心（面積 0 なら頂点の平均）。"""
    a = cx = cy = 0.0
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i][:2]
        x1, y1 = poly[(i + 1) % n][:2]
        c = x0 * y1 - x1 * y0
        a += c
        cx += (x0 + x1) * c
        cy += (y0 + y1) * c
    if abs(a) < 1e-9:
        return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)
    return (cx / (3 * a), cy / (3 * a))


def _val(o):
    return o.get("value") if isinstance(o, dict) else o


def default_cam(b: dict, back: float = DEFAULT_BACK_M, eye: float = DEFAULT_EYE_M, fov: float = DEFAULT_FOV) -> str | None:
    """棟の足元の中心から南西に back m、地面から eye m に立ち、中心を向く（北東 45°）。
    見上げは棟の高さの半分（高さが無ければ 10 m）の所を向く角度。返り値は ?cam= の値（x,y,z,yaw,pitch,fov）。"""
    fp = b.get("footprint")
    poly = fp.get("polygon") if isinstance(fp, dict) else fp
    g = _val(b.get("ground_z"))
    if not poly or g is None:
        return None
    cx, cy = polygon_centroid(poly)
    d = back / math.sqrt(2)
    x, y, z = cx - d, cy - d, float(g) + eye
    h = _val(b.get("parapet_top"))
    target = (float(h) / 2) if h else 10.0
    pitch = math.degrees(math.atan2(target - eye, back))
    return f"{x:.2f},{y:.2f},{z:.2f},45.00,{pitch:.2f},{fov:.2f}"


def walk_cam(b: dict, out_dir: Path) -> tuple[str | None, str]:
    """model_report.json の web_cameras.street.query を優先。返り値は（cam の値, 出所）。"""
    mr_path = out_dir / "model_report.json"
    if mr_path.exists():
        mr = json.loads(mr_path.read_text(encoding="utf-8"))
        q = (((mr.get("web_cameras") or {}).get("street") or {}).get("query"))
        if q:
            return str(q).replace("cam=", "", 1), "model_report.web_cameras.street"
    return default_cam(b), "既定（足元の中心から南西 40 m・目の高さ 1.6 m）"


WALK_READY_JS = "(() => { const l = document.getElementById('loading'); return !!l && l.style.display === 'none'; })()"


def walk_shot(building_id: str, out_dir: Path | None = None, building_json: Path | None = None,
              root: Path = Path(".")) -> dict:
    """web/index.html を ?cam= の構図で撮って out/<棟>/walk.png に。真っ白なら 1 回だけ待ちを伸ばして撮り直す。"""
    out_dir = out_dir or root / OUT_ROOT / building_id
    bj = building_json or root / "data" / "buildings" / f"{building_id}.json"
    res = {"kind": "walk", "shots": {}, "errors": {}}
    if find_browser() is None:
        res["errors"]["browser"] = "Edge も Chrome も無い"
        return res
    try:
        b = load_building(bj)
    except Exception as e:  # noqa: BLE001
        res["errors"]["building"] = f"{type(e).__name__}: {e}"
        return res
    cam, src = walk_cam(b, out_dir)
    if not cam:
        res["errors"]["cam"] = "構図が決まらない（model_report の street も、足元と地面の高さも無い）"
        return res
    png = out_dir / "walk.png"
    srv, port = serve(root)
    info: dict = {}
    t0 = time.time()
    try:
        url = f"http://127.0.0.1:{port}/web/index.html?cam={cam}&size={WALK_SIZE[0]}x{WALK_SIZE[1]}&ui=0"
        with headless_browser(WALK_SIZE, info) as cdp:
            for n, settle in enumerate(WALK_SETTLE, 1):
                r = capture(cdp, url, png, WALK_READY_JS, WALK_READY_TIMEOUT, settle)
                if r["ready"] and not is_blank(png):
                    res["shots"]["walk"] = {"png": str(png), "cam": cam, "cam_from": src, "size": _png_size(png),
                                            "seconds": round(time.time() - t0, 1), "tries": n}
                    res["errors"].pop("walk", None)
                    break
                res["errors"]["walk"] = ("読み込みが終わらない" if not r["ready"] else "真っ白") + f"（{n} 回）"
    except Exception as e:  # noqa: BLE001
        res["errors"]["walk"] = f"{type(e).__name__}: {e}"
    finally:
        srv.shutdown()
        srv.server_close()
    res.update(info)
    return res


# --- まとめ・裏で走らせる ------------------------------------------------------------------

def all_shots(building_id: str, evidence: bool = True, walk: bool = True, building_json: Path | None = None,
              root: Path = Path(".")) -> dict:
    out = {}
    if evidence:
        out["evidence"] = evidence_shots(building_id, building_json=building_json, root=root)
    if walk:
        out["walk"] = walk_shot(building_id, building_json=building_json, root=root)
    return out


_RUNNING: dict[str, threading.Thread] = {}
_RUN_LOCK = threading.Lock()


def _write_status(path: Path, d: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def run_in_background(building_id: str, evidence: bool = True, walk: bool = True, building_json: Path | None = None,
                      root: Path = Path("."), on_done=None) -> threading.Thread | None:
    """all_shots を裏のスレッドで走らせる（呼んだ側を待たせない）。同じ棟は同時に 1 つだけ（走っていれば None）。
    状態は out/<棟>/shots_status.json：state＝running／done／failed、started・finished、result（all_shots の返り値）。
    on_done(result) があれば終わった後に呼ぶ（例外は status に書いて飲み込む）。"""
    status = root / OUT_ROOT / building_id / "shots_status.json"
    with _RUN_LOCK:
        t = _RUNNING.get(building_id)
        if t is not None and t.is_alive():
            return None
        started = datetime.now().isoformat(timespec="seconds")

        def work():
            d = {"building": building_id, "state": "running", "started": started}
            _write_status(status, d)
            try:
                r = all_shots(building_id, evidence=evidence, walk=walk, building_json=building_json, root=root)
                failed = any(part.get("errors") for part in r.values())
                d.update(state="failed" if failed and not any(part.get("shots") for part in r.values()) else "done", result=r)
                if on_done is not None:
                    on_done(r)
            except Exception as e:  # noqa: BLE001
                d.update(state="failed", error=f"{type(e).__name__}: {e}")
            d["finished"] = datetime.now().isoformat(timespec="seconds")
            _write_status(status, d)

        t = threading.Thread(target=work, name=f"shots-{building_id}", daemon=True)
        _RUNNING[building_id] = t
        t.start()
        return t


# --- CLI ---------------------------------------------------------------------------------

def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("file", help="棟 JSON（data/buildings/<棟>.json）")
    p.add_argument("--evidence", action="store_true", help="証拠ビューアの絵だけ（evidence_<種>.png）")
    p.add_argument("--walk", action="store_true", help="歩く画面の絵だけ（walk.png）")


def print_result(res: dict) -> None:
    for part in res.values():
        for k, s in part.get("shots", {}).items():
            print(f"  撮れた {k}：{s['png']}  {s.get('size')}  {s.get('seconds')} 秒" + (f"  cam={s['cam']}（{s['cam_from']}）" if s.get("cam") else ""))
        for k, e in part.get("errors", {}).items():
            print(f"  撮れない {k}：{e}")
        for le in part.get("launch_errors", []):
            print(f"  Edge の起動 {le['try']} 回目：{le['reason']}" + (f"（標準エラー：{le['stderr'][-300:]}）" if le.get("stderr") else ""))
        if part.get("leftover_after"):
            print(f"  注意：headless の Edge が {part['leftover_after']} 個 残った")


def run_cli(a: argparse.Namespace) -> int:
    bj = Path(a.file)
    b = load_building(bj)
    both = not a.evidence and not a.walk
    res = all_shots(b["id"], evidence=both or a.evidence, walk=both or a.walk, building_json=bj)
    print_result(res)
    return 0 if not any(part.get("errors") for part in res.values()) else 1
