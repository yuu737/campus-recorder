"""scripts/trial_reset.py：退避 → 試走の結果ができる → 戻す、で元に戻り、試走の結果は after/ に残る。"""
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("trial_reset", Path(__file__).parents[1] / "scripts" / "trial_reset.py")
tr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tr)


def _w(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _snapshot(root: Path) -> dict:
    return {str(p.relative_to(root)).replace("\\", "/"): p.read_bytes() for p in sorted(root.rglob("*"))
            if p.is_file() and not str(p.relative_to(root)).startswith("trial")}


@pytest.fixture
def repo(tmp_path):
    r = tmp_path
    _w(r / "scripts/design_b1.py", "# 人の設計")
    _w(r / "scripts/design_b2.py", "# 他の棟")
    _w(r / "data/buildings/b1.json", '{"id": "b1"}')
    _w(r / "data/buildings/b2.json", '{"id": "b2"}')
    _w(r / "out/b1/kansatsu_f0.json", "{}")
    _w(r / "out/b1/recheck/x/summary.md", "前の検収")
    _w(r / "out/b1_dbg/a.txt", "作業の残り")
    _w(r / "out/b2/kansatsu_f0.json", "{}")
    _w(r / "out/site/aerial_z18.png", "png")
    _w(r / "out/rectified/20261103/P1_front.jpg", "jpg")
    _w(r / "out/rectified/20261103/P9_front.jpg", "他の棟")
    _w(r / "out/photos/shiwake_b1.json", "{}")
    _w(r / "web/data/models/b1.glb", "glb")
    _w(r / "web/data/models/b2.glb", "glb")
    _w(r / "data/rulers.json", "{}")
    reg = {"schema": "campus-recorder/registry/1",
           "entries": [{"photo": "P9", "building": "b2", "face": "f0"}, {"photo": "P1", "building": "b1", "face": "f1"},
                       {"photo": "P8", "building": "b2", "face": "f1"}],
           "not_registered": [{"photo": "P2", "building": "b1", "reason": "x"}]}
    idx = {"schema": "x", "photos": [
        {"id": "P1", "building": "b1", "face": "f1", "faces": ["f1", "f2"], "notes": "n"},
        {"id": "P2", "building": "b1", "face": "f0", "source": "台帳(期間前)", "period": "before"},
        {"id": "P3", "building": None, "face": None, "candidates": [{"building": "b1"}]},
        {"id": "P9", "building": "b2", "face": "f0"}]}
    _w(r / "data/photos/registry.json", json.dumps(reg, ensure_ascii=False, indent=1))
    _w(r / "data/photos/index.json", json.dumps(idx, ensure_ascii=False, indent=1))
    return r


def test_reset_then_restore(repo):
    before = _snapshot(repo)
    r = tr.reset("b1", repo, stamp="t1")
    man = json.loads((repo / "trial/b1/t1/manifest.json").read_text(encoding="utf-8"))
    assert set(man["moved"]) == {"scripts/design_b1.py", "data/buildings/b1.json", "out/b1", "out/b1_dbg",
                                 "web/data/models/b1.glb", "out/photos/shiwake_b1.json", "out/rectified/20261103/P1_front.jpg"}
    assert r["registry"] == {"entries": 1, "not_registered": 1}
    # 残す物
    for keep in ("scripts/design_b2.py", "data/buildings/b2.json", "out/b2/kansatsu_f0.json", "out/site/aerial_z18.png",
                 "web/data/models/b2.glb", "data/rulers.json", "out/rectified/20261103/P9_front.jpg"):
        assert (repo / keep).exists()
    assert not (repo / "out/b1").exists() and not (repo / "scripts/design_b1.py").exists()
    reg = json.loads((repo / "data/photos/registry.json").read_text(encoding="utf-8"))
    assert [e["photo"] for e in reg["entries"]] == ["P9", "P8"] and reg["not_registered"] == []
    idx = {p["id"]: p for p in json.loads((repo / "data/photos/index.json").read_text(encoding="utf-8"))["photos"]}
    assert idx["P1"]["face"] is None and "faces" not in idx["P1"] and idx["P1"]["building"] == "b1"   # building は残す
    assert idx["P9"]["face"] == "f0"
    with pytest.raises(SystemExit):        # 戻す前に 2 回目の退避はしない
        tr.reset("b1", repo, stamp="t2")

    # 試走：新しい設計・仕分け・登録・取り込み
    _w(repo / "scripts/design_b1.py", "# 係の設計")
    _w(repo / "data/buildings/b1.json", '{"id": "b1", "trial": true}')
    _w(repo / "out/b1/sekkei_summary.json", "{}")
    _w(repo / "out/rectified/b1_f0/x_front.jpg", "jpg")
    idx_doc = json.loads((repo / "data/photos/index.json").read_text(encoding="utf-8"))
    for p in idx_doc["photos"]:
        if p["id"] == "P1":
            p["face"] = "f3"
        if p["id"] == "P3":
            p["building"], p["face"] = "b1", "f0"
    idx_doc["photos"].append({"id": "N1", "building": "b1", "face": "f0"})
    _w(repo / "data/photos/index.json", json.dumps(idx_doc, ensure_ascii=False, indent=1))
    reg["entries"].append({"photo": "P1", "building": "b1", "face": "f3"})
    _w(repo / "data/photos/registry.json", json.dumps(reg, ensure_ascii=False, indent=1))

    rr = tr.restore("b1", repo)
    assert rr["index_removed"] == 1
    after = _snapshot(repo)
    assert after.keys() == before.keys()
    for k in before:
        if k.endswith(".json") and k.startswith("data/photos/"):
            assert json.loads(after[k]) == json.loads(before[k]), k
        else:
            assert after[k] == before[k], k
    a = repo / "trial/b1/t1/after"
    assert (a / "files/scripts/design_b1.py").read_text(encoding="utf-8") == "# 係の設計"
    assert (a / "files/out/rectified/b1_f0/x_front.jpg").exists() and (a / "files/out/b1/sekkei_summary.json").exists()
    assert any(p["id"] == "N1" for p in json.loads((a / "index_after.json").read_text(encoding="utf-8"))["photos"])
    assert json.loads((repo / "trial/b1/t1/manifest.json").read_text(encoding="utf-8"))["restored"]
    with pytest.raises(SystemExit):
        tr.restore("b1", repo)


def test_clear_building_keeps_ledger(repo):
    tr.reset("b1", repo, stamp="t1", clear_building=True)
    idx = {p["id"]: p for p in json.loads((repo / "data/photos/index.json").read_text(encoding="utf-8"))["photos"]}
    assert idx["P1"]["building"] is None and idx["P2"]["building"] == "b1"
    tr.restore("b1", repo)
    idx = {p["id"]: p for p in json.loads((repo / "data/photos/index.json").read_text(encoding="utf-8"))["photos"]}
    assert idx["P1"]["building"] == "b1" and idx["P1"]["faces"] == ["f1", "f2"]
