"""進捗報告の絵を撮る道具（recorder/shots.py）：構図の計算、絵の判定、裏で走らせる仕組み、報告の 2 段目のシート。
Edge／rerun が無い環境では、実際に撮るテストは skip。"""
from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path

import pytest

from recorder import shots as S

ROOT = Path(__file__).resolve().parents[1]


def test_polygon_centroid_square_and_degenerate():
    assert S.polygon_centroid([[0, 0], [10, 0], [10, 10], [0, 10]]) == pytest.approx((5, 5))
    assert S.polygon_centroid([[0, 0], [2, 0], [4, 0]]) == pytest.approx((2, 0))      # 面積 0 は頂点の平均


def test_default_cam_southwest_40m_eye_1_6():
    b = {"footprint": {"polygon": [[-10, -10], [10, -10], [10, 10], [-10, 10]]},
         "ground_z": {"value": 40.0}, "parapet_top": {"value": 20.0}}
    x, y, z, yaw, pitch, fov = map(float, S.default_cam(b).split(","))
    d = 40 / math.sqrt(2)
    assert (x, y) == pytest.approx((-d, -d), abs=0.01)
    assert z == pytest.approx(41.6)
    assert math.hypot(x, y) == pytest.approx(40, abs=0.01)
    assert yaw == 45.0                                                     # 北から時計回り＝北東を向く
    assert pitch == pytest.approx(math.degrees(math.atan2(10 - 1.6, 40)), abs=0.01)   # 高さの半分を向く
    assert fov == 60.0


def test_default_cam_without_height_or_ground():
    b = {"footprint": {"polygon": [[0, 0], [4, 0], [4, 4], [0, 4]]}, "ground_z": {"value": 0.0}}
    pitch = float(S.default_cam(b).split(",")[4])
    assert pitch == pytest.approx(math.degrees(math.atan2(10 - 1.6, 40)), abs=0.01)   # 高さが無ければ 10 m
    assert S.default_cam({"footprint": {"polygon": [[0, 0], [1, 0], [1, 1]]}, "ground_z": {"value": None}}) is None


def test_walk_cam_prefers_model_report(tmp_path: Path):
    b = {"footprint": {"polygon": [[0, 0], [4, 0], [4, 4], [0, 4]]}, "ground_z": {"value": 0.0}}
    cam, src = S.walk_cam(b, tmp_path)
    assert src.startswith("既定") and cam.count(",") == 5
    (tmp_path / "model_report.json").write_text(json.dumps({"web_cameras": {"street": {"query": "cam=1,2,3,4,5,6"}}}), encoding="utf-8")
    assert S.walk_cam(b, tmp_path) == ("1,2,3,4,5,6", "model_report.web_cameras.street")


def _png(path: Path, img) -> Path:
    import cv2
    cv2.imencode(".png", img)[1].tofile(str(path))
    return path


def test_blank_and_empty_viewer(tmp_path: Path):
    import numpy as np
    white = _png(tmp_path / "w.png", np.full((200, 300, 3), 255, np.uint8))
    dark = _png(tmp_path / "d.png", np.full((200, 300, 3), 14, np.uint8))
    rng = np.random.default_rng(0)
    busy = _png(tmp_path / "b.png", rng.integers(0, 255, (200, 300, 3), dtype=np.uint8))
    assert S.is_blank(white) and S.is_blank(dark) and not S.is_blank(busy)
    assert S.viewer_empty(white) and S.viewer_empty(dark) and not S.viewer_empty(busy)
    side = np.full((200, 300, 3), 255, np.uint8)
    side[:, 250:] = rng.integers(0, 255, (200, 50, 3), dtype=np.uint8)      # 右の欄だけ字がある空のビューア
    assert S.viewer_empty(_png(tmp_path / "s.png", side))


def test_run_in_background_one_per_building(tmp_path: Path, monkeypatch):
    gate = threading.Event()

    def fake(bid, **kw):
        gate.wait(5)
        return {"walk": {"shots": {"walk": {"png": "x"}}, "errors": {}}}

    monkeypatch.setattr(S, "all_shots", fake)
    t = S.run_in_background("bgtest", root=tmp_path)
    assert t is not None
    assert S.run_in_background("bgtest", root=tmp_path) is None              # 同じ棟は同時に 1 つ
    status = tmp_path / "out" / "bgtest" / "shots_status.json"
    for _ in range(50):
        if status.exists():
            break
        time.sleep(0.05)
    assert json.loads(status.read_text(encoding="utf-8"))["state"] == "running"
    gate.set()
    t.join(5)
    d = json.loads(status.read_text(encoding="utf-8"))
    assert d["state"] == "done" and d["finished"] and d["result"]["walk"]["shots"]


@pytest.mark.skipif(S.find_browser() is None, reason="Edge も Chrome も無い")
def test_headless_browser_capture_and_exit(tmp_path: Path):
    html = tmp_path / "p.html"
    html.write_text("<body style='margin:0;background:#c33'><div id='x' style='width:50px;height:50px;background:#3c3'></div>"
                    "<script>setTimeout(()=>document.title='ok',300)</script></body>", encoding="utf-8")
    info: dict = {}
    with S.headless_browser((320, 200), info) as cdp:
        r = S.capture(cdp, html.as_uri(), tmp_path / "p.png", "document.title === 'ok'", 10, 0.2)
    assert r["ready"] and S._png_size(tmp_path / "p.png") == [320, 200]
    assert not S.is_blank(tmp_path / "p.png", frac=0.99)
    assert info["leftover_after"] == 0                                       # 撮り終えたら Edge は残らない


@pytest.mark.skipif(S.rerun_exe(ROOT) is None or S.find_browser() is None
                    or not (ROOT / "out" / "honbu" / "evidence.rrd").exists(), reason="rerun／Edge／本部棟の evidence.rrd が無い")
def test_strip_blueprint_removes_default_view(tmp_path: Path):
    exe = S.rerun_exe(ROOT)
    out = S.strip_blueprint(exe, ROOT / "out" / "honbu" / "evidence.rrd", tmp_path / "d.rrd")
    assert out is not None
    pr = S._run([str(exe), "rrd", "print", str(out)])
    assert "/world/" in pr.stdout and "ContainerBlueprint" not in pr.stdout


@pytest.mark.skipif(not (ROOT / "out" / "honbu" / "sheet.png").exists(), reason="本部棟の四面図のシートが無い")
def test_report_puts_sheet_first_in_stage2(tmp_path: Path, monkeypatch):
    from recorder import report as rp
    monkeypatch.chdir(ROOT)
    s = rp.run(Path("data/buildings/honbu.json"), tmp_path / "r.html")
    h = (tmp_path / "r.html").read_text(encoding="utf-8")
    sec = h[h.index('<section id="elev">'):h.index('<section id="evidence">')]
    assert "sheet.png" in sec and "面ごとの根拠（色付き立面）" in sec
    assert sec.index("sheet.png") < sec.index("面ごとの根拠") < sec.index("<svg")
    assert "線＝JSON の値" in sec
    assert s["stages"]["elev"]["result"]                                      # 段の 1 行はそのまま


# --- Edge の起動に失敗した時（J-030）：標準エラーを残す・新しいプロファイルで 1 回やり直す・待ち 60 秒 -------------

def test_launch_wait_is_60s_and_two_tries():
    assert S.DEVTOOLS_WAIT_S == 60.0 and S.LAUNCH_TRIES == 2


def test_launch_failure_keeps_stderr_and_retries_with_new_profile(tmp_path: Path, monkeypatch):
    """口を開かずに落ちる「ブラウザ」（Python に Edge の引数を渡すと、知らない引数で標準エラーに書いて終わる）。"""
    import sys

    monkeypatch.setenv("RECORDER_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setattr(S, "find_browser", lambda: Path(sys.executable))
    monkeypatch.setattr(S, "_procs_with", lambda mark, name: [])
    monkeypatch.setattr(S, "DEVTOOLS_WAIT_S", 10.0)
    info: dict = {}
    with pytest.raises(RuntimeError, match="2 回") as ei:
        with S.headless_browser((320, 240), info):
            pass
    errs = info["launch_errors"]
    assert [e["try"] for e in errs] == [1, 2]
    assert errs[0]["profile"] != errs[1]["profile"]                 # 毎回新しいプロファイル
    assert all(e["exit_code"] not in (None, 0) for e in errs)
    assert all("headless" in e["stderr"] for e in errs)             # 標準エラーの中身が残る
    assert "標準エラーの末尾" in str(ei.value) and "headless" in str(ei.value)
    import tempfile
    left = [p for p in Path(tempfile.gettempdir()).glob("recorder_shot_*") if any(e["profile"] in p.name for e in errs)]
    assert left == []                                                # プロファイルも標準エラーのファイルも消える


def test_launch_recovers_on_second_try(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RECORDER_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setattr(S, "find_browser", lambda: Path("msedge.exe"))
    monkeypatch.setattr(S, "_procs_with", lambda mark, name: [])
    calls = []

    def fake_launch(exe, size, wait, h):
        calls.append(wait)
        if len(calls) == 1:
            raise S.LaunchFail("60 秒待っても DevToolsActivePort が書かれない")
        return "ws://127.0.0.1:1/devtools/page/x"

    class FakeCDP:
        def __init__(self, ws):
            self.ws, self.sent = ws, []

        def call(self, method, params=None, timeout=60):
            self.sent.append(method)
            return {}

        def close(self):
            pass

    monkeypatch.setattr(S, "_launch", fake_launch)
    monkeypatch.setattr(S, "CDP", FakeCDP)
    info: dict = {}
    with S.headless_browser((320, 240), info) as cdp:
        assert cdp.ws.startswith("ws://")
    assert calls == [60.0, 60.0]
    assert len(info["launch_errors"]) == 1 and info["launch_errors"][0]["reason"].startswith("60 秒")
    assert "Browser.close" in cdp.sent


def _fake_walk_env(monkeypatch, tmp_path: Path, urls: list):
    from contextlib import contextmanager

    class Srv:
        def shutdown(self): pass
        def server_close(self): pass

    @contextmanager
    def hb(size, info=None):
        yield object()

    def cap(cdp, url, png, *a, **k):
        urls.append(url)
        return {"ready": True}

    monkeypatch.setattr(S, "find_browser", lambda: Path("x"))
    monkeypatch.setattr(S, "load_building", lambda p: {"id": "t"})
    monkeypatch.setattr(S, "walk_cam", lambda b, o: ("1,2,3,4,5,6", "test"))
    monkeypatch.setattr(S, "serve", lambda root: (Srv(), 1))
    monkeypatch.setattr(S, "headless_browser", hb)
    monkeypatch.setattr(S, "capture", cap)
    monkeypatch.setattr(S, "is_blank", lambda p: False)
    monkeypatch.setattr(S, "_png_size", lambda p: [1, 1])


def test_walk_shot_default_has_boxes_0(tmp_path: Path, monkeypatch):
    urls: list = []
    _fake_walk_env(monkeypatch, tmp_path, urls)
    r = S.walk_shot("t", out_dir=tmp_path, root=tmp_path)
    assert "&boxes=0" in urls[0] and r["boxes"] is False and r["shots"]["walk"]["boxes"] is False


def test_walk_shot_with_boxes_has_no_boxes_0(tmp_path: Path, monkeypatch):
    urls: list = []
    _fake_walk_env(monkeypatch, tmp_path, urls)
    r = S.walk_shot("t", out_dir=tmp_path, root=tmp_path, boxes=True)
    assert "boxes=0" not in urls[0] and r["boxes"] is True
