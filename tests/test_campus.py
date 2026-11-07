"""敷地の立ち上げ（別名の表・既定のキャンパス・campus init / check・build_site の別名）のテスト。"""
import json

import pytest

from recorder import site
from recorder.__main__ import main


def _write(d, name, doc):
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_campus_aliases(tmp_path):
    _write(tmp_path, "a.json", {"aliases": {"本部棟": "honbu"}})
    _write(tmp_path, "b.json", {})
    assert site.campus_aliases("a", tmp_path) == {"本部棟": "honbu"}
    assert site.campus_aliases("b", tmp_path) == {}
    assert site.campus_aliases("nothing", tmp_path) == {}


def test_tempaku_aliases_in_json():
    al = site.campus_aliases("tempaku")
    if not al:
        pytest.skip("tempaku.json が無い")
    assert al["本部棟"] == "honbu" and al["タワー"] == "tower75" and al["図書館"] == "library" and al["11号館"] == "b11"
    assert not hasattr(site, "SLUGS")


def test_default_campus(tmp_path):
    with pytest.raises(ValueError, match="敷地 JSON"):
        site.default_campus(tmp_path)
    _write(tmp_path, "plateau.json", {})
    _write(tmp_path, "one.json", {})
    assert site.default_campus(tmp_path) == "one"
    _write(tmp_path, "two.json", {})
    with pytest.raises(ValueError, match="複数"):
        site.default_campus(tmp_path)
    (tmp_path / "default_campus.txt").write_text("two\n", encoding="utf-8")
    assert site.default_campus(tmp_path) == "two"


def test_campus_init_makes_skeleton_and_does_not_overwrite(tmp_path):
    path, made = site.campus_init("x", "架空大学", "本キャンパス", (35.0, 136.0, 35.2, 136.2), data_site=tmp_path)
    assert made
    d = json.loads(path.read_text(encoding="utf-8"))
    assert d["university"] == "架空大学" and d["buildings"] == [] and d["crs"]["epsg"] == 6674   # 中心 東経136.1° は VI 系の原点（136.0°）に近い（11/8 F1：系は原点から選ぶ）
    assert d["crs"]["origin_lat"] == pytest.approx(35.1) and d["crs"]["origin_lon"] == pytest.approx(136.1)
    assert d["sources"]["campus_map"]["permission"]["status"] is None
    assert {"aliases", "scope", "name_links", "questions"} <= set(d)
    d["university"] = "書き換え済み"
    path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    path2, made2 = site.campus_init("x", "別", "別", data_site=tmp_path)
    assert not made2 and json.loads(path2.read_text(encoding="utf-8"))["university"] == "書き換え済み"


def _doc():
    return {"campus": "x", "buildings": [{"id": "a"}],
            "scope": {"targets": ["a", "zzz"]},
            "sources": {"campus_map": {"permission": {"status": "granted"}}},
            "name_links": [{"map_name": "A", "building_id": "a", "status": "linked"},
                           {"map_name": "B", "building_id": None, "status": "map_only"},
                           {"map_name": None, "footprint": "plateau g", "status": "footprint_only"}],
            "questions": [{"q": "Q1", "status": "open"}, {"q": "Q2", "status": "answered", "answer": "はい"}]}


def test_campus_check_counts(tmp_path):
    _write(tmp_path, "x.json", _doc())
    out = tmp_path / "out" / "campus_check.json"
    r = site.campus_check("x", tmp_path, out)
    assert len(r["map_only"]) == 1 and len(r["footprint_only"]) == 1 and r["targets_missing"] == ["zzz"]
    assert len(r["questions_open"]) == 1 and r["permission_ok"]
    assert r["problem_count"] == 4 and not r["ok"]
    assert json.loads(out.read_text(encoding="utf-8"))["problem_count"] == 4
    assert any("Q1" in l for l in site.campus_check_lines(r))


def test_campus_check_clean_and_permission(tmp_path):
    d = _doc()
    d["name_links"] = d["name_links"][:1]
    d["scope"]["targets"] = ["a"]
    d["questions"] = d["questions"][1:]
    _write(tmp_path, "x.json", d)
    assert site.campus_check("x", tmp_path)["ok"]
    d["sources"]["campus_map"]["permission"]["status"] = "asked"
    _write(tmp_path, "x.json", d)
    assert site.campus_check("x", tmp_path)["problem_count"] == 1


def test_cli_campus_check_exit_code(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(site, "DATA_SITE", tmp_path / "data" / "site")
    monkeypatch.setattr(site, "OUT_SITE", tmp_path / "out" / "site")
    assert main(["campus", "init", "x", "--university", "U", "--campus-name", "C"]) == 0
    assert main(["campus", "init", "x", "--university", "U", "--campus-name", "C"]) == 1
    assert main(["campus", "check"]) == 1          # 許可が granted でない
    assert (tmp_path / "out" / "site" / "campus_check.json").exists()


def test_build_site_uses_json_aliases(tmp_path):
    ring = [[0, 0], [10, 0], [10, 10], [0, 10]]
    osm = {"boundary": [ring], "fetched_at": None,
           "buildings": [{"osm": "way/1", "name": "本館", "building": "yes", "levels_osm": None, "rings": [ring]},
                         {"osm": "way/2", "name": "別館", "building": "yes", "levels_osm": None, "rings": [ring]}]}
    out = tmp_path / "x.json"
    out.write_text(json.dumps({"aliases": {"本館": "honkan"}, "university": "U", "scope": {"targets": []}}, ensure_ascii=False),
                   encoding="utf-8")
    doc = site.build_site("x", osm, None, out=out)
    assert [b["id"] for b in doc["buildings"]] == ["honkan", "osm-way-2"]
    assert doc["aliases"] == {"本館": "honkan"} and doc["university"] == "U"     # 作り直しても立ち上げの欄は残る
