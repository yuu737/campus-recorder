"""進行役の決定の台帳と語彙の台帳。台帳の場所は monkeypatch で tmp_path に差し替える（ネットは使わない）。"""
import json

import pytest

from recorder import agents, assets, decisions, schema


def w(p, o):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(o, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def env(tmp_path, monkeypatch):
    dec = {"schema": decisions.SCHEMA, "decisions": [
        {"id": "D-0001", "kind": "vocab", "target": "x", "decision": "approve", "by": "t", "at": "t", "why": "w"},
        {"id": "D-0002", "kind": "vocab", "target": "y", "decision": "deny", "by": "t", "at": "t", "why": "w"}]}
    vocab = {"types": [{"type": "box", "decision": "D-0001"}, {"type": "column", "decision": "D-0001"},
                       {"type": "louver", "decision": "D-0002"}, {"type": "composite", "decision": "D-0001"},
                       {"type": "asset", "decision": "D-0001"}]}
    cat = {"items": [{"id": "ok", "decision": "D-0001"}, {"id": "nodec"}, {"id": "denied", "decision": "D-0002"}]}
    w(tmp_path / "decisions.json", dec)
    w(tmp_path / "vocab.json", vocab)
    w(tmp_path / "catalog.json", cat)
    monkeypatch.setattr(decisions, "DECISIONS", tmp_path / "decisions.json")
    monkeypatch.setattr(decisions, "VOCAB", tmp_path / "vocab.json")
    monkeypatch.setattr(assets, "CATALOG", tmp_path / "catalog.json")
    return tmp_path


def hook(parts, **extra):
    return decisions.validate_building_hook({"parts": parts, **extra})


def test_approved_type_passes(env):
    assert hook([{"type": "box", "name": "a"}, {"type": "column"}]) == []


def test_unknown_type_fails(env):
    e = hook([{"type": "gazebo", "name": "東屋"}])
    assert len(e) == 1 and "東屋" in e[0] and "gazebo" in e[0] and "語彙の台帳に" not in e[0] or "台帳" in e[0]
    assert "進行役の決定が要る" in e[0]


def test_deny_fails(env):
    e = hook([{"type": "louver", "name": "L"}])
    assert e and "D-0002" in e[0] and "進行役の決定が要る" in e[0]


def test_nested_and_block_parts(env):
    b = {"blocks": [{"id": "k", "parts": [{"type": "nope"}]}], "parts": [{"type": "box", "sub": {"type": "nope2"}}]}
    e = decisions.validate_building_hook(b)
    assert len(e) == 2


def test_composite_primitives(env):
    ok = {"type": "composite", "name": "c", "primitives": [{"shape": "box"}]}
    bad = {"type": "composite", "name": "c", "primitives": [{"shape": "wedge"}, {"shape": "box", "cut": True}]}
    assert hook([ok]) == []
    e = hook([bad])
    assert len(e) == 2 and any("wedge" in x for x in e) and any("cut" in x for x in e)


def test_asset_decision(env):
    assert hook([{"type": "asset", "asset": "ok"}, {"type": "box", "material": "asset:ok"}]) == []
    e = hook([{"type": "asset", "asset": "nodec"}])
    assert e and "決定（decision）がありません" in e[0]
    assert hook([{"type": "box", "material": "asset:denied"}])
    assert "台帳" in hook([{"type": "box", "material": "asset:ghost"}])[0]


def test_interior(env):
    e = decisions.validate_building_hook({"interior": {"rooms": []}})
    assert e and "interior" in e[0]
    v = json.loads((env / "vocab.json").read_text(encoding="utf-8"))
    v["types"].append({"type": "interior", "decision": "D-0001"})
    w(env / "vocab.json", v)
    assert decisions.validate_building_hook({"interior": {"rooms": []}}) == []


def test_validate_building_calls_hook(env):
    b = schema.new_building("b1", "テスト棟")
    b["parts"] = [{"type": "gazebo", "name": "g"}]
    assert any("gazebo" in e for e in schema.validate_building(b))


def test_add_decision_numbering(env):
    d = decisions.add_decision("vocab", "t1", "approve", "理由", building="b1", request="r")
    assert d["id"] == "D-0003" and d["by"] == "shinko(fable)" and d["at"]
    d2 = decisions.add_decision("asset", "t2", "deny", "理由", scope="s")
    assert d2["id"] == "D-0004" and d2["scope"] == "s"
    assert decisions.find("D-0003")["building"] == "b1"
    assert [x["id"] for x in decisions.approved("vocab", "t1")] == ["D-0003"]
    with pytest.raises(ValueError):
        decisions.add_decision("vocab", "t", "maybe", "w")


def test_vocab_list_lines(env):
    lines = decisions.vocab_list_lines()
    assert len(lines) == 5 and any("louver" in x and "承認されていない" in x for x in lines)


def test_assets_fetch_refuses_without_approval(env, monkeypatch):
    def no_net(*a, **k):
        raise AssertionError("ネットに出た")
    monkeypatch.setattr(assets, "_get", no_net)
    cat = {"items": [
        {"id": "nodec", "kind": "texture", "what": "w", "source": "polyhaven", "url": "u", "license": "CC0-1.0",
         "license_url": "u", "file": "polyhaven/a", "maps": {"color": "c.jpg"}, "tile_m": [1, 1]},
        {"id": "denied", "decision": "D-0002", "kind": "texture", "what": "w", "source": "polyhaven", "url": "u", "license": "CC0-1.0",
         "license_url": "u", "file": "polyhaven/b", "maps": {"color": "c.jpg"}, "tile_m": [1, 1]},
        {"id": "ghost", "decision": "D-9999", "kind": "texture", "what": "w", "source": "polyhaven", "url": "u", "license": "CC0-1.0",
         "license_url": "u", "file": "polyhaven/c", "maps": {"color": "c.jpg"}, "tile_m": [1, 1]}]}
    r = assets.fetch(cat, store=env / "store", log=lambda *_: None)
    assert r["ok"] == [] and set(r["fail"]) == {"nodec", "denied", "ghost"}
    assert all("進行役の決定が要る" in v for v in r["fail"].values())


def test_add_item(env):
    item = {"id": "new", "kind": "texture", "what": "w", "source": "polyhaven", "url": "u", "license": "CC0-1.0",
            "license_url": "u", "file": "polyhaven/n", "maps": {"color": "c.jpg"}, "tile_m": [1, 1]}
    with pytest.raises(ValueError, match="進行役の決定が要る"):
        assets.add_item(dict(item), cat={"items": []}, save=False)
    cat = {"items": []}
    assets.add_item({**item, "decision": "D-0001"}, cat=cat, save=False)
    assert assets.item(cat, "new")["decision"] == "D-0001"


def test_detect_events_marks_decided(tmp_path):
    from recorder import schema as sc
    b = sc.new_building("b1", "テスト棟")
    b["requests"] = [{"kind": "tool", "status": "open", "what": "内装の語彙", "need": "vocab"},
                     {"kind": "asset", "status": "open", "what": "机・椅子"}]
    w(tmp_path / "data/buildings/b1.json", b)
    w(tmp_path / "data/assets/catalog.json", {"items": []})
    w(tmp_path / "data/photos/index.json", {"photos": []})
    w(tmp_path / "data/photos/registry.json", {"entries": []})
    w(tmp_path / "data/decisions.json", {"decisions": [
        {"id": "D-0002", "kind": "vocab", "decision": "approve", "building": "b1", "request": "kind:tool 内装の語彙"},
        {"id": "D-0003", "kind": "asset", "decision": "deny", "building": "b1", "request": "kind:asset 机・椅子"}]})
    r = agents.detect_events("b1", tmp_path, now="t")
    by = {e["kind"]: e for e in r["events"]}
    assert by["vocab"]["evidence"]["decision"] == "D-0002" and "決定済み（実装待ち）" in by["vocab"]["why"]
    assert "decision" not in by["asset"]["evidence"] and "決定済み" not in by["asset"]["why"]
