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
    monkeypatch.delenv(serve.CAMPUS_ENV, raising=False)   # 画面で選んだ敷地は環境変数にも入る。テストの外に漏らさない
    monkeypatch.setitem(serve.SELECTED, "campus", None)
    serve.SITE_RUN.clear()
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
    import os
    os.environ.pop(serve.CAMPUS_ENV, None)   # _select が入れた物を消す（monkeypatch は元から無い変数を戻さない）


def _get(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.status, json.loads(r.read())


def _post(url, data, ctype="application/json"):
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
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
    with urllib.request.urlopen(base + "/web/staff.html", timeout=60) as r:
        assert r.status == 200
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(base + "/data/site/test.json", timeout=60)
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

    def fake(bid, photos_folder=None, *, steps=None, dry_run=False, cwd=".", agents_dir=None, max_usd=None):
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


def test_start_continues_buildings_that_have_json_and_passes_max_usd(srv, monkeypatch):
    """棟 JSON がある棟は追記（continue_pipeline）、無い棟は run_pipeline。max_usd は両方に渡す（11/8、K-106）。"""
    base, root = srv
    calls = []

    def fake_new(bid, photos_folder=None, *, steps=None, dry_run=False, cwd=".", agents_dir=None, prices=None, max_usd=None):
        calls.append(("new", bid, max_usd))
        return {"building": bid, "steps": [{"step": "satsuei"}]}

    def fake_cont(bid, photos_folder=None, *, faces=None, steps=None, dry_run=False, cwd=".", agents_dir=None, prices=None, max_usd=None):
        calls.append(("continue", bid, max_usd))
        return {"building": bid, "steps": [{"step": "sokuryo"}], "spent_usd": 0.0,
                "stopped": {"step": "sokuryo", "reason": "max_usd", "why": "上限"}}

    monkeypatch.setattr(agents, "run_pipeline", fake_new)
    monkeypatch.setattr(agents, "continue_pipeline", fake_cont)
    (root / "data" / "buildings").mkdir(parents=True)
    (root / "data" / "buildings" / "honbu.json").write_text("{}", encoding="utf-8")
    st, j = _post(base + "/api/start", json.dumps({"buildings": ["honbu", "tower75"], "max_usd": 3.5}).encode())
    assert st == 200 and j["modes"] == {"honbu": "continue", "tower75": "new"} and j["max_usd"] == 3.5
    for _ in range(100):
        st, p = _get(base + "/api/progress?buildings=honbu,tower75")
        if all(p["buildings"][b]["run"]["state"] != "running" for b in ("honbu", "tower75")):
            break
        time.sleep(0.05)
    h, t = p["buildings"]["honbu"]["run"], p["buildings"]["tower75"]["run"]
    assert h["continuing"] is True and t["continuing"] is False
    assert h["state"] == "stopped" and h["stopped_at"] == "sokuryo" and h["stopped"]["reason"] == "max_usd"
    assert sorted(calls) == [("continue", "honbu", 3.5), ("new", "tower75", 3.5)]
    st, j = _post(base + "/api/start", json.dumps({"buildings": ["kyotsu-minami"], "max_usd": -1}).encode())
    assert st == 400


def test_tape_frame_marks_staff_building_and_ruler(srv):
    """メジャーの枠（K-102）：/api/tape で送った写真は同じ取り込みに乗り、職員の申告の棟と ruler・ruler_note が付く。"""
    base, root = srv
    body, ctype = _multipart([("TAPE_1.jpg", _jpeg((10, 200, 10)))],
                             {"building": "honbu", "face": "f6", "note": "正面の柱型に水平。0〜92 cm"})
    st, j = _post(base + "/api/tape", body, ctype)
    assert st == 200 and j["tape"]["marked"] == ["TAPE_1"] and j["buildings"] == ["honbu"]
    rec = next(r for r in json.loads((root / "data" / "photos" / "index.json").read_text(encoding="utf-8"))["photos"] if r["id"] == "TAPE_1")
    assert (rec["building"], rec["assigned_by"], rec["ruler"], rec["face"]) == ("honbu", "staff", "tape_measure", "f6")
    assert rec["ruler_note"] == "正面の柱型に水平。0〜92 cm" and rec["send_file"] == "send/TAPE_1.jpg"
    st, j = _post(base + "/api/tape", *_multipart([("X.jpg", _jpeg((0, 0, 0)))], {"building": ""}))
    assert st == 400


def test_bad_requests_do_not_kill_server(srv):
    base, _root = srv
    st, j = _post(base + "/api/start", b"{not json")
    assert st == 400 and "error" in j
    st, j = _post(base + "/api/start", json.dumps({"buildings": ["../x"]}).encode())
    assert st == 400
    st, j = _post(base + "/api/campus", json.dumps({"university": "u", "campus_name": "c", "slug": "test"}).encode())
    assert st == 200 and j["made"] is False   # 敷地 JSON は既にある（作り直さず、その敷地を選ぶ）
    st, j = _post(base + "/api/campus", json.dumps({"university": "u", "campus_name": "c"}).encode())
    assert st == 400 and "範囲" in j["error"]   # 範囲（bbox）か住所が無いと作らない（原点が別の大学になるため）
    st, j = _get(base + "/api/site")
    assert st == 200 and j["campus_json"] == "test" and j["max_usd_default"] == 20.0


def test_campus_init_when_missing(srv):
    base, root = srv
    (root / "data" / "site" / "test.json").unlink()
    st, j = _post(base + "/api/campus", json.dumps({"university": "Sample Univ", "campus_name": "North",
                                                    "bbox": "35.000, 137.000, 35.010, 137.012", "osm_relation": "123"}).encode())
    assert st == 200 and j["made"] is True and j["campus"] == "north" and j["bbox"] == [35.0, 137.0, 35.01, 137.012]
    doc = json.loads((root / "data" / "site" / "north.json").read_text(encoding="utf-8"))
    assert doc["university"] == "Sample Univ" and doc["scope"]["targets"] == []
    assert abs(doc["crs"]["origin_lat"] - 35.005) < 1e-6 and doc["sources"]["osm"]["relation"] == 123   # 原点は範囲の中心
    assert doc["sources"]["osm"]["way"] is None
    st, j = _get(base + "/api/site")
    assert j["campus_json"] == "north" and j["selected"]["built"] is False and j["web_matches"] is False


def test_campus_passes_osm_way_and_relation_to_campus_init(srv, monkeypatch):
    """OSM の敷地の番号（relation・way）は call_campus_init から campus_init にそのまま渡る（F1 の引数）。数字でなければ 400。"""
    base, root = srv
    seen = {}
    from recorder import site
    real = site.campus_init

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(site, "campus_init", spy)
    body = {"university": "Sample Univ", "campus_name": "West", "bbox": [35.0, 137.0, 35.01, 137.01], "osm_way": "456789"}
    st, j = _post(base + "/api/campus", json.dumps(body).encode())
    assert st == 200 and j["made"] is True and seen["osm_way"] == 456789 and seen["osm_relation"] is None
    doc = json.loads((root / "data" / "site" / "west.json").read_text(encoding="utf-8"))
    assert doc["sources"]["osm"]["way"] == 456789 and doc["sources"]["osm"]["relation"] is None
    st, j = _post(base + "/api/campus", json.dumps({**body, "slug": "west2", "osm_relation": "abc"}).encode())
    assert st == 400 and "relation" in j["error"]


def test_campus_needs_ascii_slug_for_japanese_names(srv):
    base, root = srv
    body = {"university": "○○大学", "campus_name": "北キャンパス", "bbox": [35.0, 137.0, 35.01, 137.01]}
    st, j = _post(base + "/api/campus", json.dumps(body, ensure_ascii=False).encode())
    assert st == 400 and "短い名前" in j["error"]
    st, j = _post(base + "/api/campus", json.dumps({**body, "slug": "kita"}, ensure_ascii=False).encode())
    assert st == 200 and j["made"] is True and j["campus"] == "kita"
    # 敷地 JSON が 2 つになったので、選んだ物を default_campus.txt にも書く（CLI と係が同じ物を使う）
    assert (root / "data" / "site" / "default_campus.txt").read_text(encoding="utf-8").strip() == "kita"
    st, j = _post(base + "/api/campus/select", json.dumps({"campus": "test"}).encode())
    assert st == 200 and serve._default_campus() == "test"
    st, j = _post(base + "/api/campus/select", json.dumps({"campus": "nothing"}).encode())
    assert st == 400


def test_site_build_runs_commands_in_thread(srv, monkeypatch):
    """敷地の取得（POST /api/site/build）：CLI のコマンドを別スレッドで順に打ち、進捗は /api/progress の site。"""
    import sys
    base, root = srv
    seen = []

    def fake_cmds(campus):
        seen.append(campus)
        return [[sys.executable, "-c", f"print('build {campus}')"], [sys.executable, "-c", "print('export')"]]

    monkeypatch.setattr(serve, "site_build_commands", fake_cmds)
    st, j = _post(base + "/api/site/build", b"{}")
    assert st == 200 and j["started"] and j["campus"] == "test"
    for _ in range(200):
        st, p = _get(base + "/api/progress?buildings=honbu")
        if p["site"]["state"] != "running":
            break
        time.sleep(0.05)
    assert p["site"]["state"] == "done" and p["site"]["log"] == ["build test", "export"] and set(seen) == {"test"}
    monkeypatch.setattr(serve, "site_build_commands", lambda c: [[sys.executable, "-c", "import sys; print('x'); sys.exit(3)"]])
    st, j = _post(base + "/api/site/build", json.dumps({"campus": "test"}).encode())
    for _ in range(200):
        st, p = _get(base + "/api/progress?buildings=honbu")
        if p["site"]["state"] != "running":
            break
        time.sleep(0.05)
    assert p["site"]["state"] == "error" and "3" in p["site"]["error"]
    st, j = _post(base + "/api/site/build", json.dumps({"campus": "nothing"}).encode())
    assert st == 400


def test_start_default_max_usd(srv, monkeypatch):
    base, root = srv
    calls = []
    monkeypatch.setattr(agents, "run_pipeline", lambda bid, pf=None, **kw: calls.append(kw.get("max_usd")) or {"steps": []})
    st, j = _post(base + "/api/start", json.dumps({"buildings": ["honbu"]}).encode())
    assert st == 200 and j["max_usd"] == agents.DEFAULT_MAX_USD == 20.0
    for _ in range(100):
        if calls:
            break
        time.sleep(0.05)
    assert calls == [20.0]


# --- 待機画面（web/progress.html）の API（11/8、F8） ---------------------------------------------

def _agent_md(root, name, desc, model):
    d = root / ".claude" / "agents"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(f"---\nname: {name}\ndescription: {desc}\nmodel: {model}\ntools: Read\n---\n本文", encoding="utf-8")


def test_steps_reads_agent_frontmatter(srv):
    url, root = srv
    _agent_md(root, "sokuryo", "試し係。一言の説明。二つ目の文", "opus")
    _agent_md(root, "seizu", "絵の係。コードの代行", "sonnet")
    code, j = _get(url + "/api/steps")
    assert code == 200
    by = {s["step"]: s for s in j["steps"]}
    assert [s["step"] for s in j["steps"]][: len(agents.PIPELINE_STEPS)] == agents.PIPELINE_STEPS
    assert by["sokuryo"]["name"] == "試し係" and by["sokuryo"]["line"].startswith("一言の説明")
    assert by["sokuryo"]["model_ja"] == "Claude Opus" and by["sokuryo"]["code"] is False
    assert by["web"]["agent"] == "seizu" and by["web"]["name"] == "絵の係" and by["web"]["model"] == "code"
    assert by["kenshu"]["name"] == "kenshu"          # 定義が無い係は id のまま（固定の名前を足さない）


def test_pipeline_summarizes_latest_record_and_state(srv):
    url, root = srv
    rep = root / "data" / "reports" / "honbu"
    rep.mkdir(parents=True)
    old = {"steps": [{"step": "satsuei", "agent": "satsuei", "model": "m", "seconds": 1, "note": "古い"}]}
    new = {"made_at": "2026-11-08T01:00:00", "runner": "api", "spent_usd": 1.5, "max_usd": 20,
           "steps": [{"step": "satsuei", "agent": "satsuei", "model": "claude-haiku-5-5", "seconds": 12.0,
                      "cost_usd_estimate": 0.1, "note": "x" * 300}]}
    (rep / "pipeline_20261107_000000.json").write_text(json.dumps(old), encoding="utf-8")
    (rep / "pipeline_20261108_000000.json").write_text(json.dumps(new), encoding="utf-8")
    (root / "out" / "honbu").mkdir(parents=True)
    (root / "out" / "honbu" / agents.STATE_NAME).write_text(json.dumps(
        {"building": "honbu", "status": "stopped", "current": None, "steps": ["satsuei", "shiwake"], "done": ["satsuei"],
         "stopped": {"step": "shiwake", "reason": "rate_limit", "label": "429"}, "spent_usd": 1.5, "max_usd": 20,
         "record_name": "pipeline_20261108_000000.json"}), encoding="utf-8")
    code, j = _get(url + "/api/pipeline?building=honbu")
    assert code == 200
    assert j["record"]["file"] == "data/reports/honbu/pipeline_20261108_000000.json" and j["records"] == 2
    s = j["record"]["steps"][0]
    assert s["cost_usd"] == 0.1 and s["seconds"] == 12.0 and len(s["summary"]) <= 140
    assert j["state"]["status"] == "stopped" and j["state"]["stopped"]["reason"] == "rate_limit"
    code, j = _get(url + "/api/pipeline?building=nothing")
    assert code == 200 and j["record"] is None and j["state"] is None
    try:
        _get(url + "/api/pipeline?building=..%2Fx")
        raise AssertionError("棟 id でない名前は断る")
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_artifacts_lists_only_existing_files(srv):
    url, root = srv
    ob = root / "out" / "honbu"
    ob.mkdir(parents=True)
    (ob / "elev_fa.svg").write_text("<svg/>", encoding="utf-8")
    (ob / "walk.png").write_bytes(b"x")
    (root / "data" / "photos" / "send").mkdir(parents=True)
    (root / "data" / "photos" / "send" / "p1.jpg").write_bytes(_jpeg("red"))
    (root / "data" / "photos" / "index.json").write_text(json.dumps({"photos": [
        {"id": "p1", "building": "honbu", "face": "fa", "send_file": "send/p1.jpg", "period": "during",
         "masks": [{"kind": "face", "box": [0.1, 0.1, 0.2, 0.2], "by": "auto"},        # 確かめていない自動の顔 → 出さない
                   {"kind": "face", "box": [0.5, 0.5, 0.6, 0.6], "by": "review"}]},     # 見直しで確かめた顔 → 出す
        {"id": "p2", "building": "honbu", "send_file": "send/p2.jpg", "period": "during"},      # 縮小版が無い → 出さない
        {"id": "p3", "building": "honbu", "send_file": "send/p1.jpg", "public": False}]}), encoding="utf-8")   # 公開しない
    code, j = _get(url + "/api/artifacts?building=honbu")
    assert code == 200
    assert [p["id"] for p in j["photos"]] == ["p1"]
    assert [(m["label"], m["box"]) for m in j["photos"][0]["masks"]] == [("顔", [0.5, 0.5, 0.6, 0.6])]   # by: auto は出ない（F11）
    assert j["elev"] == [{"face": "fa", "name": None, "svg": "/out/honbu/elev_fa.svg"}]
    assert j["walk"] == "/out/honbu/walk.png" and j["evidence"]["photo"] is None and j["kenshu"] == []
    st, _ = _get_raw(url + j["photos"][0]["src"])
    assert st == 200


def _get_raw(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.status, r.read()


def test_artifacts_maps_masks_onto_rectified_and_hides_unmappable(srv):
    """元の写真から作った絵にも箱：正面化は rectify の H で四隅を写す。H の無い正面化・登録の無い製図板は見せない（masks_ok）。"""
    url, root = srv
    ob = root / "out" / "honbu"
    (ob / "compare").mkdir(parents=True)
    rect = root / "out" / "rectified" / "honbu"
    rect.mkdir(parents=True)
    for pid in ("p1", "p2"):
        (rect / f"{pid}_front.jpg").write_bytes(b"x")
        (ob / "compare" / f"{pid}_fa_compare.json").write_text(json.dumps(
            {"ok": True, "source": f"out/rectified/honbu/{pid}_front.jpg", "px_per_m": 10, "lines": []}), encoding="utf-8")
    # p1 は H あり（2 倍に拡大して右へ 10 px）、p2 は H 無し
    (rect / "p1_front.json").write_text(json.dumps({"width": 100, "height": 50, "H": [[2, 0, 10], [0, 2, 0], [0, 0, 1]]}), encoding="utf-8")
    (rect / "p2_front.json").write_text(json.dumps({"width": 100, "height": 50}), encoding="utf-8")
    (ob / "coverage_fa_photo.png").write_bytes(b"x")
    (ob / "coverage.json").write_text(json.dumps({"faces": [{"face": "fa", "length_m": 10, "ground_z": 0, "top_z": 5,
        "photos": [{"photo": "p1", "outline": [[[0, 0], [5, 0], [5, 5]]]}]}]}), encoding="utf-8")
    (root / "data" / "photos").mkdir(parents=True)
    box = [{"kind": "poster", "box": [0.1, 0.2, 0.3, 0.4], "by": "review"}]
    (root / "data" / "photos" / "index.json").write_text(json.dumps({"photos": [
        {"id": "p1", "building": "honbu", "masks": box}, {"id": "p2", "building": "honbu", "masks": box}]}), encoding="utf-8")
    code, j = _get(url + "/api/artifacts?building=honbu")
    assert code == 200
    by = {c["photo"]: c for c in j["compare"]}
    assert by["p1"]["masks_ok"] and by["p1"]["rect_masks"][0]["label"] == "掲示物"
    assert by["p1"]["rect_masks"][0]["poly"] == [[30.0, 20.0], [70.0, 20.0], [70.0, 40.0], [30.0, 40.0]]
    assert by["p2"]["masks_ok"] is False and by["p2"]["rect_masks"] is None
    assert j["compare"][0]["photo"] == "p1"                      # 箱を写せた物が先
    assert j["coverage"][0]["masks_ok"] is False and j["coverage"][0]["masks"] is None   # 登録（H）が無い → その面は見せない


# --- 止まった所から続ける POST /api/resume（11/8、F15） -------------------------------------------------

def _write_state(root, bid, **kw):
    st = {"building": bid, "mode": "full", "steps": ["satsuei", "shiwake", "sokuryo"], "done": ["satsuei"],
          "status": "stopped", "current": None, "spent_usd": 1.25, "max_usd": 20.0, "sessions": {}, "runs": [],
          "stopped": {"step": "shiwake", "reason": "limit", "why": "手数の上限", "label": "1 回の呼び出しの上限"}}
    st.update(kw)
    p = agents.state_path(bid, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def _fake_resume(monkeypatch, calls, gate=None, result=None):
    def fake(bid, *, dry_run=False, cwd=".", agents_dir=None, prices=None, max_usd=None):
        calls.append((bid, dry_run, str(cwd), max_usd))
        if gate is not None:
            gate.wait(10)
        return result or {"building": bid, "steps": [{"step": "shiwake"}, {"step": "sokuryo"}], "stopped_early": False}
    monkeypatch.setattr(agents, "resume_pipeline", fake)


def _wait_run(base, bid):
    for _ in range(100):
        _st, p = _get(base + f"/api/progress?buildings={bid}")
        run = p["buildings"][bid]["run"]
        if run and run["state"] != "running":
            return p["buildings"][bid]
        time.sleep(0.05)
    raise AssertionError("終わらない")


def test_resume_refuses_without_api_key(srv, monkeypatch):
    base, root = srv
    calls = []
    _fake_resume(monkeypatch, calls)
    _write_state(root, "honbu")
    st, j = _post(base + "/api/resume", json.dumps({"building": "honbu"}).encode())
    assert st == 409 and j["code"] == "no_key" and "API キー" in j["error"]
    assert calls == []
    _st, p = _get(base + "/api/progress?buildings=honbu,tower75")
    rs = p["buildings"]["honbu"]["resume"]
    assert rs["stopped"] is True and rs["ok"] is False and rs["code"] == "no_key" and rs["step"] == "shiwake"
    assert "resume" not in p["buildings"]["tower75"]   # 状態ファイルが無い棟には出さない


def test_resume_calls_resume_pipeline_in_thread(srv, monkeypatch):
    base, root = srv
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-key")
    calls = []
    _fake_resume(monkeypatch, calls)
    _write_state(root, "honbu")
    _st, pj = _get(base + "/api/pipeline?building=honbu")
    assert pj["resume"]["ok"] is True and pj["resume"]["code"] == "ok" and pj["resume"]["need_max_usd"] is False
    st, j = _post(base + "/api/resume", json.dumps({"building": "honbu"}).encode())
    assert st == 200 and j["started"] == "honbu" and j["resumed_from"] == {"step": "shiwake", "reason": "limit"}
    b = _wait_run(base, "honbu")
    assert b["run"]["state"] == "done" and b["run"]["resumed"] is True and b["run"]["last_step"] == "sokuryo"
    assert calls == [("honbu", False, str(root.resolve()), None)]   # 上限は変えない（None）、dry_run にしない


def test_resume_refuses_while_running(srv, monkeypatch):
    base, root = srv
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-key")
    gate = threading.Event()
    calls = []
    _fake_resume(monkeypatch, calls, gate=gate)
    _write_state(root, "honbu")
    st, _j = _post(base + "/api/resume", json.dumps({"building": "honbu"}).encode())
    assert st == 200
    st, j = _post(base + "/api/resume", json.dumps({"building": "honbu"}).encode())   # 同じ棟がまだ動いている
    assert st == 409 and j["busy"] == ["honbu"]
    st, j = _post(base + "/api/start", json.dumps({"buildings": ["honbu"]}).encode())   # start も同じ RUNS で断る
    assert st == 409
    gate.set()
    _wait_run(base, "honbu")
    assert len(calls) == 1


def test_resume_refuses_without_state_or_when_not_stopped(srv, monkeypatch):
    base, root = srv
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-key")
    calls = []
    _fake_resume(monkeypatch, calls)
    st, j = _post(base + "/api/resume", json.dumps({"building": "honbu"}).encode())
    assert st == 409 and j["code"] == "no_state"
    _write_state(root, "honbu", status="done", stopped=None)
    st, j = _post(base + "/api/resume", json.dumps({"building": "honbu"}).encode())
    assert st == 409 and j["code"] == "done"
    _write_state(root, "honbu", status="running", current="shiwake", stopped=None)   # 別の窓で動いているかもしれない
    st, j = _post(base + "/api/resume", json.dumps({"building": "honbu"}).encode())
    assert st == 409 and j["code"] == "not_stopped"
    st, j = _post(base + "/api/resume", json.dumps({"building": "../x"}).encode())
    assert st == 400
    assert calls == []


def test_resume_after_max_usd_needs_a_higher_cap(srv, monkeypatch):
    base, root = srv
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-key")
    calls = []
    _fake_resume(monkeypatch, calls)
    _write_state(root, "honbu", spent_usd=5.0, max_usd=5.0,
                 stopped={"step": "sokuryo", "reason": "max_usd", "why": "上限", "label": "棟の費用の上限"})
    _st, p = _get(base + "/api/progress?buildings=honbu")
    rs = p["buildings"]["honbu"]["resume"]
    assert rs["code"] == "need_max_usd" and rs["ok"] is True and rs["need_max_usd"] is True and rs["spent_usd"] == 5.0
    st, j = _post(base + "/api/resume", json.dumps({"building": "honbu"}).encode())
    assert st == 409 and j["code"] == "need_max_usd"
    st, j = _post(base + "/api/resume", json.dumps({"building": "honbu", "max_usd": 4}).encode())
    assert st == 409 and j["code"] == "need_max_usd"
    st, j = _post(base + "/api/resume", json.dumps({"building": "honbu", "max_usd": 8}).encode())
    assert st == 200 and j["max_usd"] == 8
    _wait_run(base, "honbu")
    assert calls == [("honbu", False, str(root.resolve()), 8.0)]


def test_progress_counts_unused_staff_photos_for_buildings_with_json(srv):
    """棟 JSON がある棟だけ unused_staff_photos {n, tape, name}：職員が送って（assigned_by: staff）棟 JSON に id が出ない写真（F16）。"""
    base, root = srv
    (root / "data" / "photos").mkdir(parents=True, exist_ok=True)
    (root / "data" / "photos" / "index.json").write_text(json.dumps({"photos": [
        {"id": "IMG_A", "building": "honbu", "assigned_by": "staff"},                            # 棟 JSON に出る（使用済み）
        {"id": "IMG_A1", "building": "honbu", "assigned_by": "staff"},                           # 頭が同じでも別の写真（未使用）
        {"id": "IMG_T", "building": "honbu", "assigned_by": "staff", "ruler": "tape_measure"},   # 未使用のメジャー
        {"id": "IMG_OLD", "building": "honbu"},                                                  # assigned_by 無し（数えない）
        {"id": "IMG_X", "building": "tower75", "assigned_by": "staff"},                          # 棟 JSON が無い棟
    ]}), encoding="utf-8")
    (root / "data" / "buildings").mkdir(parents=True, exist_ok=True)
    (root / "data" / "buildings" / "honbu.json").write_text(json.dumps(
        {"id": "honbu", "name": "テスト棟", "faces": [{"id": "f0", "h": {"status": "measured", "evidence": ["IMG_A"]}}]},
        ensure_ascii=False), encoding="utf-8")
    st, p = _get(base + "/api/progress?buildings=honbu,tower75")
    assert st == 200
    u = p["buildings"]["honbu"]["unused_staff_photos"]
    assert u == {"n": 2, "tape": 1, "building_photos": 3, "name": "テスト棟"}
    assert "unused_staff_photos" not in p["buildings"]["tower75"]   # 棟 JSON が無い棟は出さない
    assert serve.unused_staff_photos(root, "tower75") is None
    # 全部使い終わったら 0（ボタンの候補にならない）
    (root / "data" / "buildings" / "honbu.json").write_text(json.dumps(
        {"id": "honbu", "name": "テスト棟", "ev": ["IMG_A", "IMG_A1", "IMG_T"]}, ensure_ascii=False), encoding="utf-8")
    assert serve.unused_staff_photos(root, "honbu")["n"] == 0


def test_staff_html_can_start_from_unused_staff_photos():
    """職員の画面：写真を入れずに、前に送った未使用の写真で③を押せる（unused_staff_photos を読み、1 棟だけ /api/start）。"""
    from pathlib import Path

    html = (Path(__file__).resolve().parents[1] / "web" / "staff.html").read_text(encoding="utf-8")
    assert "unused_staff_photos" in html and "前に送った写真のうち未使用" in html
    assert "startUnused()" in html and "buildings: [id], max_usd: maxUsd()" in html
    assert '$("#export").disabled = ready === 0 && !cands.length' in html
