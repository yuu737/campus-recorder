"""根拠の門（schema.evidence_checks）と職員の確認（photos.confirm_photo・serve の /api/confirm）。11/7。"""
import json
from pathlib import Path

from recorder import photos, schema


def m(value, evidence, status="measured"):
    return {"value": value, "unit": "m", "status": status, "evidence": evidence, "method": "試し"}


def building():
    return {"id": "b1", "faces": [
        {"id": "f1", "from": [0, 0], "to": [10, 0], "floor_pitch": m(3.5, ["P1", "plateau:bldg_x"])},
        {"id": "f2", "from": [10, 0], "to": [10, 8], "floor_pitch": m(3.5, ["P2", "P3", "P4", "P5", "P6", "P7", "P8"])},
    ], "floor_count": m(4, ["P9"]),
        "parts": [{"type": "door", "face": "f1", "w": m(1.8, ["P1"]), "h": m(2.1, ["P2"], status="estimated")}]}


RECORDS = {
    "P1": {"id": "P1", "building": "b1", "face": "f1", "confidence": "high", "file": "d/P1.jpg"},
    "P2": {"id": "P2", "building": "b1", "face": "f1", "confidence": "mid", "notes": "職員が振り分け | 仕分け係：f1 の正面"},
    "P3": {"id": "P3", "building": "b2", "face": "f2"},
    "P4": {"id": "P4", "building": None, "face": None},
    "P5": {"id": "P5", "building": "b1", "face": "f2", "confidence": "low"},
    "P6": {"id": "P6", "building": "b1", "face": "f2", "confidence": "low", "confirmed_by": "staff"},
    "P7": {"id": "P7", "building": "b1", "face": None, "interior_views": True},
    "P8": {"id": "P8", "building": "b1", "face": "f1", "faces": ["f1", "f2"], "confidence": "mid"},
    "P9": {"id": "P9", "building": "b1", "face": None, "confidence": "low"},
}


def test_evidence_checks_finds_each_problem():
    got = {(c["photo"], c["problem"]) for c in schema.evidence_checks(building(), RECORDS)}
    assert got == {("P2", "other_face"), ("P3", "other_building"), ("P4", "no_building"), ("P5", "low_unconfirmed"),
                   ("P9", "low_unconfirmed")}
    # 通る物：P1（同じ面）・P6（low だが確かめた）・P7（内装の写真）・P8（faces に f2）・plateau の根拠・estimated の値（P2 の部品の高さ）
    c = next(c for c in schema.evidence_checks(building(), RECORDS) if c["photo"] == "P2")
    assert c["used_at"] == ["faces[f2].floor_pitch"] and c["value_faces"] == ["f2"]
    p9 = next(c for c in schema.evidence_checks(building(), RECORDS) if c["photo"] == "P9")
    assert p9["value_faces"] == []                                # 棟全体の値は面を照合しない（low だけ）


def test_part_face_is_checked():
    b = building()
    b["parts"][0]["w"] = m(1.8, ["P5"])                           # 部品は f1。P5 は面 f2・low
    probs = {(c["photo"], c["problem"]) for c in schema.evidence_checks(b, RECORDS)}
    assert ("P5", "other_face") in probs and ("P5", "low_unconfirmed") in probs


def test_warn_building_includes_gate():
    ws = schema.warn_building(building(), photo_ids=set(RECORDS), records=RECORDS)
    gate = [w for w in ws if "measured の根拠の写真" in w]
    assert len(gate) == 5 and any("P3" in w and "索引では棟 b2" in w for w in gate)


def _repo(tmp_path):
    (tmp_path / "data/buildings").mkdir(parents=True)
    (tmp_path / "data/photos").mkdir(parents=True)
    (tmp_path / "data/buildings/b1.json").write_text(json.dumps(building(), ensure_ascii=False), encoding="utf-8")
    (tmp_path / "data/photos/index.json").write_text(json.dumps({"photos": list(RECORDS.values())}, ensure_ascii=False),
                                                     encoding="utf-8")
    return tmp_path


def test_confirm_clears_the_gate(tmp_path):
    root = _repo(tmp_path)
    ip = root / "data/photos/index.json"
    r = photos.confirm_photo(ip, "P5", now="T")                    # 合っている
    assert r["changed"] is False and r["record"]["confirmed_by"] == "staff" and r["record"]["confirmed_at"] == "T"
    r2 = photos.confirm_photo(ip, "P2", face="f2", now="T2")       # 直してから確かめる
    assert r2["changed"] and r2["record"]["faces"] == ["f2"] and r2["record"]["confirm_change"]["from"]["face"] == "f1"
    probs = {(c["photo"], c["problem"]) for c in schema.evidence_checks(building(), schema.load_photo_records(root))}
    assert ("P5", "low_unconfirmed") not in probs and ("P2", "other_face") not in probs


def test_write_to_confirm_merges_per_photo(tmp_path):
    root = _repo(tmp_path)
    b = building()
    b["parts"][0]["w"] = m(1.8, ["P5"])
    res = photos.write_to_confirm(b, root)
    p5 = next(it for it in res["items"] if it["photo"] == "P5")
    assert sorted(p5["problems"]) == ["low_unconfirmed", "other_face"] and len(p5["used_at"]) == 2
    assert json.loads(Path(res["path"]).read_text(encoding="utf-8"))["building"] == "b1"


def test_serve_confirm_api(tmp_path):
    from recorder import serve
    root = _repo(tmp_path)
    lst = serve.api_confirm_list(root, ["b1"])["buildings"]["b1"]
    assert lst["faces"] == ["f1", "f2"] and {it["photo"] for it in lst["items"]} == {"P2", "P3", "P4", "P5", "P9"}
    p2 = next(it for it in lst["items"] if it["photo"] == "P2")
    assert p2["why"] == "仕分け係：f1 の正面"
    assert serve.api_confirm(root, {"photo": "P5"}) == {"photo": "P5", "changed": False}
    assert serve.api_confirm(root, {"photo": "P2", "face": "f2"})["changed"] is True
    left = {it["photo"] for it in serve.api_confirm_list(root, ["b1"])["buildings"]["b1"]["items"]}
    assert left == {"P3", "P4", "P9"}


def test_events_ask_staff_to_confirm(tmp_path):
    from recorder import agents
    root = _repo(tmp_path)
    ev = agents.detect_events("b1", root, now="T")
    hum = [e for e in ev["events"] if e["kind"] == "human" and "根拠の門" in e["why"]]
    assert hum and len({c["photo"] for c in hum[0]["evidence"]["to_confirm"]}) == 5
