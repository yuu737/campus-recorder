"""職員の画面の受付 recorder serve のテスト（cwd は tmp_path。本物の索引・写真・係は触らない）。"""
import io
import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from recorder import agents, serve


def _jpeg(color):
    from PIL import Image

    b = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(b, "JPEG")
    return b.getvalue()


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / "web" / "data").mkdir(parents=True)
    (tmp_path / "web" / "staff.html").write_text("<!doctype html><title>t</title>", encoding="utf-8")
    (tmp_path / "web" / "data" / "site.json").write_text(json.dumps(
        {"campus": "test", "university": "テスト大学", "scope": {"targets": ["honbu", "tower75"]}}), encoding="utf-8")
    (tmp_path / "data" / "site").mkdir(parents=True)
    (tmp_path / "data" / "site" / "test.json").write_text(json.dumps(
        {"campus": "test", "aliases": {"本部棟": "honbu", "honbu": "honbu", "タワー75": "tower75", "tower75": "tower75"}},
        ensure_ascii=False), encoding="utf-8")
    serve.RUNS.clear()
    serve._last_folder.clear()
    s = serve.make_server("127.0.0.1", 0, tmp_path)
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{s.server_address[1]}", tmp_path
    s.shutdown()
    s.server_close()


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, json.loads(r.read())


def _post(url, data, ctype="application/json"):
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _multipart(files, fields):
    bd = "----crtest"
    out = b""
    for k, v in fields.items():
        out += f'--{bd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n'.encode() + v.encode("utf-8") + b"\r\n"
    for name, data in files:
        out += (f'--{bd}\r\nContent-Disposition: form-data; name="photos"; filename="{name}"\r\n'
                f"Content-Type: image/jpeg\r\n\r\n").encode("utf-8") + data + b"\r\n"
    out += f"--{bd}--\r\n".encode()
    return out, f"multipart/form-data; boundary={bd}"


def test_site_and_static(srv):
    base, _root = srv
    st, j = _get(base + "/api/site")
    assert st == 200 and j["ready"] is True and j["targets"] == ["honbu", "tower75"] and j["api_key"] is False
    with urllib.request.urlopen(base + "/web/staff.html", timeout=10) as r:
        assert r.status == 200
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(base + "/data/site/test.json", timeout=10)
    assert e.value.code == 404   # web/ out/ data/photos/ 以外は配信しない


def test_photos_import_and_assign(srv):
    base, root = srv
    body, ctype = _multipart([("IMG_0001.jpg", _jpeg((200, 0, 0))), ("IMG_0002.jpg", _jpeg((0, 200, 0)))],
                             {"assignments": json.dumps([
                                 {"file": "IMG_0001.jpg", "building": "honbu", "interior": False, "floor": None},
                                 {"file": "IMG_0002.jpg", "building": "タワー75", "interior": True, "floor": 1}], ensure_ascii=False)})
    st, j = _post(base + "/api/photos", body, ctype)
    assert st == 200, j
    assert j["saved"] == 2 and j["imported"] == 2 and j["by_building"] == {"honbu": 1, "tower75": 1}
    assert j["assign"]["assigned"] == 2 and j["assign"]["interior"] == 1 and not j["assign"]["errors"]
    day = j["folder"].split("/")[-1]
    assert (root / "data" / "photos" / day / "honbu" / "IMG_0001.jpg").exists()
    idx = json.loads((root / "data" / "photos" / "index.json").read_text(encoding="utf-8"))["photos"]
    by = {p["id"]: p for p in idx}
    assert by["IMG_0001"]["building"] == "honbu" and by["IMG_0001"]["period"] == "during"
    assert by["IMG_0002"]["building"] == "tower75" and by["IMG_0002"]["interior"] is True and by["IMG_0002"]["floor"] == 1
    assert by["IMG_0001"]["file"] == f"{day}/honbu/IMG_0001.jpg"
    # 二度押し：同じ中身は置き直さない。同名で中身が違えば連番
    body2, ctype2 = _multipart([("IMG_0001.jpg", _jpeg((200, 0, 0)))],
                               {"assignments": json.dumps([{"file": "IMG_0001.jpg", "building": "honbu"}])})
    st, j2 = _post(base + "/api/photos", body2, ctype2)
    assert st == 200 and j2["saved"] == 0 and j2["reused"] == 1
    body3, ctype3 = _multipart([("IMG_0001.jpg", _jpeg((0, 0, 200)))],
                               {"assignments": json.dumps([{"file": "IMG_0001.jpg", "building": "honbu"}])})
    st, j3 = _post(base + "/api/photos", body3, ctype3)
    assert st == 200 and j3["saved"] == 1 and (root / "data" / "photos" / day / "honbu" / "IMG_0001_2.jpg").exists()


def test_start_progress_and_409(srv, monkeypatch):
    base, root = srv
    gate = threading.Event()
    calls = []

    def fake(bid, photos_folder=None, *, steps=None, dry_run=False, cwd=".", agents_dir=None):
        calls.append((bid, dry_run, str(cwd)))
        gate.wait(10)
        return {"building": bid, "dry_run": dry_run, "steps": [{"step": "satsuei"}, {"step": "shiwake"}], "stopped_early": False}

    monkeypatch.setattr(agents, "run_pipeline", fake)
    (root / "out" / "honbu").mkdir(parents=True)
    (root / "out" / "honbu" / "report.json").write_text(json.dumps(
        {"stages": {"photos": {"status": "done", "result": "12 枚"}}, "counts": {"requests": 2}}), encoding="utf-8")

    st, j = _post(base + "/api/start", json.dumps({"buildings": ["honbu"]}).encode())
    assert st == 200 and j["started"] == ["honbu"] and j["dry_run"] is True and "note" in j   # キーが無いので dry-run
    st, j = _post(base + "/api/start", json.dumps({"buildings": ["honbu"]}).encode())
    assert st == 409 and j["busy"] == ["honbu"]

    st, p = _get(base + "/api/progress?buildings=honbu,tower75")
    assert st == 200 and p["running"] is True
    assert p["buildings"]["honbu"]["run"]["state"] == "running"
    assert p["buildings"]["honbu"]["report"]["counts"]["requests"] == 2
    assert p["buildings"]["tower75"] == {"report": None, "run": None}

    gate.set()
    for _ in range(100):
        st, p = _get(base + "/api/progress?buildings=honbu")
        if p["buildings"]["honbu"]["run"]["state"] != "running":
            break
        time.sleep(0.05)
    run = p["buildings"]["honbu"]["run"]
    assert run["state"] == "done" and run["last_step"] == "shiwake" and p["running"] is False
    assert calls == [("honbu", True, str(root.resolve()))]
    rec = json.loads((root / run["record"]).read_text(encoding="utf-8"))
    assert rec["state"] == "done" and rec["building"] == "honbu"


def test_bad_requests_do_not_kill_server(srv):
    base, _root = srv
    st, j = _post(base + "/api/start", b"{not json")
    assert st == 400 and "error" in j
    st, j = _post(base + "/api/start", json.dumps({"buildings": ["../x"]}).encode())
    assert st == 400
    st, j = _post(base + "/api/campus", json.dumps({"university": "u", "campus_name": "c"}).encode())
    assert st == 200 and j["made"] is False   # 敷地 JSON は既にある
    st, j = _get(base + "/api/site")
    assert st == 200


def test_campus_init_when_missing(srv):
    base, root = srv
    (root / "data" / "site" / "test.json").unlink()
    st, j = _post(base + "/api/campus", json.dumps({"university": "Meijo Univ", "campus_name": "Tempaku"}).encode())
    assert st == 200 and j["made"] is True and j["campus"] == "tempaku"
    doc = json.loads((root / "data" / "site" / "tempaku.json").read_text(encoding="utf-8"))
    assert doc["university"] == "Meijo Univ" and doc["scope"]["targets"] == []
