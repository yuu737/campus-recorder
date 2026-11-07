import json

from recorder import agents


def w(p, o):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8")


def mk(tmp_path):
    from recorder import schema
    b = schema.new_building("b1", "テスト棟")
    b.setdefault("requests", [])
    w(tmp_path / "data/buildings/b1.json", b)
    w(tmp_path / "data/assets/catalog.json", {"items": [{"id": "brick"}]})
    w(tmp_path / "data/photos/index.json", {"photos": []})
    w(tmp_path / "data/photos/registry.json", {"entries": []})
    return b


def put(tmp_path, b):
    w(tmp_path / "data/buildings/b1.json", b)


def summ(tmp_path, name, ng=False, photos=("p1", "p2")):
    """再検収の summary。既定は登録の組が 2 つ（p1・p2 は台帳に無いので、それぞれが自分の組の根）。
    J-025：面ごと・組ごとの |ずれ| の中央値が許容（0.15 m）を超える組が 2 組以上かつ過半の線だけを数える。"""
    d = 0.40 if ng else 0.02
    w(tmp_path / f"out/b1/recheck/{name}/summary.json", {"tol_m": 0.15, "photos": [
        {"photo": pid, "face": "f1", "n_ng": 1 if ng else 0, "pass_face": not ng, "pass_parts": None,
         "lines": [{"what": "柱", "ok": not ng, "diff_m": d}]} for pid in photos]})


def kinds(r):
    return [e["kind"] for e in r["events"]]


def test_vocab(tmp_path):
    b = mk(tmp_path)
    b["requests"] = [{"kind": "tool", "status": "open", "what": "x", "need": "vocab"}]   # J-011：vocab は need か文で分ける
    put(tmp_path, b)
    r = agents.detect_events("b1", tmp_path, now="t")
    assert kinds(r) == ["vocab"] and r["events"][0]["call"] == "fable" and r["summary"]["vocab"] == 1
    assert (tmp_path / "out/b1/events.json").exists()


def test_asset_missing_id(tmp_path):
    b = mk(tmp_path)
    b["parts"] = [{"type": "box", "material": "asset:nothing"}, {"type": "box", "material": "asset:brick"}]
    put(tmp_path, b)
    r = agents.detect_events("b1", tmp_path)
    assert kinds(r) == ["asset"] and r["events"][0]["evidence"]["missing_ids"] == ["nothing"]


def test_human_photo_and_site_question(tmp_path):
    b = mk(tmp_path)
    b["requests"] = [{"kind": "photo", "status": "open"}]
    put(tmp_path, b)
    r = agents.detect_events("b1", tmp_path)
    assert kinds(r) == ["human"] and r["events"][0]["call"] == "human"
    w(tmp_path / "data/site/tempaku.json", {"scope": {"targets": ["b1"]},
                                           "questions": [{"status": "open", "q": "?"}, {"status": "closed"}],
                                           "sources": {"campus_map": {"permission": {"status": "granted"}}}})
    r = agents.detect_events(None, tmp_path)
    assert kinds(r) == ["human"] and r["events"][0]["why"].startswith("敷地の質問")
    assert (tmp_path / "out/site/events.json").exists()


def test_stuck_recheck_twice(tmp_path):
    mk(tmp_path)
    summ(tmp_path, "20261104_100000", ng=True)
    assert "stuck" not in kinds(agents.detect_events("b1", tmp_path))
    summ(tmp_path, "20261104_110000", ng=True)
    r = agents.detect_events("b1", tmp_path)
    assert kinds(r) == ["stuck"] and r["events"][0]["evidence"]["faces"]["f1"][0]["what"] == "柱"


def test_stuck_not_counted_for_one_group(tmp_path):
    """1 組の登録（鎖でつながった p1・p2）だけが外れる線は、その組の登録を疑う物で面の不合格にしない（J-025）。"""
    mk(tmp_path)
    w(tmp_path / "data/photos/registry.json", {"entries": [{"building": "b1", "photo": "p1"},
                                                          {"building": "b1", "photo": "p2", "via": ["p1"]}]})
    summ(tmp_path, "20261104_100000", ng=True)
    summ(tmp_path, "20261104_110000", ng=True)
    assert "stuck" not in kinds(agents.detect_events("b1", tmp_path))


def test_stuck_soten_and_zero_registered(tmp_path):
    mk(tmp_path)
    w(tmp_path / "out/b1/soten.json", {"items": [{"part": "a", "rule": None, "status": "open"}]})
    w(tmp_path / "data/photos/index.json", {"photos": [{"id": "p1", "building": "b1"}]})
    assert kinds(agents.detect_events("b1", tmp_path)) == ["stuck", "stuck"]
    assert kinds(agents.detect_events("b1", tmp_path, after_step="satsuei")) == ["stuck"]


def test_done(tmp_path):
    mk(tmp_path)
    summ(tmp_path, "20261104_110000")
    r = agents.detect_events("b1", tmp_path)
    assert kinds(r) == ["done"], r["events"]
    w(tmp_path / "out/b1/soten.json", {"items": [{"part": "a", "rule": "r", "status": "open"}]})
    assert "done" not in kinds(agents.detect_events("b1", tmp_path))


def test_setup(tmp_path):
    w(tmp_path / "data/site/tempaku.json", {"name_links": [{"id": "x", "status": "map_only"}],
                                           "sources": {"campus_map": {"permission": {"status": "granted"}}}})
    r = agents.detect_events(None, tmp_path)
    assert kinds(r) == ["setup", "setup"] and r["events"][1]["evidence"]["names"] == ["x"]


def test_no_events(tmp_path):
    mk(tmp_path)
    r = agents.detect_events("b1", tmp_path)
    assert r["events"] == [] and "事件なし。係で続ける" in r["markdown"]


def test_pipeline_stops_at_vocab(tmp_path):
    b = mk(tmp_path)
    b["requests"] = [{"kind": "tool", "status": "open", "need": "vocab"}]
    put(tmp_path, b)
    r = agents.run_pipeline("b1", steps=["shiwake", "seizu"], dry_run=True, cwd=tmp_path, agents_dir=".claude/agents")
    assert r["stopped_at"] == "shiwake" and r["events"]["summary"]["vocab"] == 1
    assert not (tmp_path / "out").exists()
