"""根拠の層：面・部品の状態（measured / estimated / hypothesis / unknown）、2 つの割合、glb の extras、歩く画面の切替。"""
import json
import struct
from pathlib import Path

import pytest

from recorder import schema, site

ROOT = Path(__file__).resolve().parent.parent


def M(v, st, **kw):
    return {"value": v, "unit": "m", "tol": None, "status": st, "evidence": ["P1"] if st == "measured" else [],
            "method": "" if st != "estimated" else "目視", "note": "", **kw}


def U():
    return M(None, "unknown")


def building(f1_bays, parts=(), floor_pitch=None, top=None):
    return {
        "id": "t", "floor_pitch": floor_pitch or M(4.0, "measured"), "parapet_top": top or M(20.0, "measured"),
        "faces": [
            {"id": "f0", "from": [0, 0], "to": [10, 0], "length": M(10.0, "estimated"), "bays": {"count": U(), "pitch": U()}},
            {"id": "f1", "from": [10, 0], "to": [10, 10], "length": M(10.0, "estimated"), "bays": f1_bays,
             "windows": [{"z_bottom": M(1.0, "measured"), "z_top": M(3.0, "measured")}]},
        ],
        "parts": list(parts),
    }


def test_state_measured_when_all_measured():
    b = building({"count": M(5, "measured"), "pitch": M(2.0, "measured")})
    s = schema.evidence_summary(b)
    assert s["faces"]["f1"]["state"] == "measured"
    assert s["faces"]["f0"]["state"] == "unknown"          # 柱の数も窓も分からない面


def test_state_estimated_when_mixed_or_detail_unknown():
    b = building({"count": M(5, "measured"), "pitch": M(2.0, "estimated")})
    assert schema.evidence_summary(b)["faces"]["f1"]["state"] == "estimated"
    b = building({"count": M(5, "measured"), "pitch": M(2.0, "measured"), "pier_width": U()})
    f = schema.evidence_summary(b)["faces"]["f1"]
    assert f["state"] == "estimated" and "bays.pier_width" in f["not_measured"]   # 細部が unknown なら measured と言わない
    b = building({"count": M(5, "measured"), "pitch": M(2.0, "measured")}, top=M(20.0, "estimated"))
    assert schema.evidence_summary(b)["faces"]["f1"]["state"] == "estimated"     # 面の上端（parapet_top）も形の値


def test_state_hypothesis_wins_over_measured():
    b = building({"count": M(5, "measured"), "pitch": M(2.0, "estimated", basis="hypothesis")})
    assert schema.evidence_summary(b)["faces"]["f1"]["state"] == "hypothesis"


def test_state_unknown_and_parts():
    b = building({"count": U(), "pitch": U()},
                 parts=[{"type": "box", "polygon": [[0, 0], [1, 0], [1, 1]], "z0": M(0.0, "measured"), "z1": M(3.0, "measured")},
                        {"type": "box", "polygon": [[0, 0], [1, 0], [1, 1]], "z0": U(), "z1": U()},
                        {"type": "steps", "face": "f1", "count": M(3, "estimated"), "rise": M(0.15, "measured")}])
    b["faces"][1]["windows"] = []
    s = schema.evidence_summary(b)
    assert s["faces"]["f1"]["state"] == "unknown"
    assert s["parts"] == {"box_0": "measured", "box_1": "unknown", "steps_2": "estimated"}


def test_ratios():
    b = building({"count": M(5, "measured"), "pitch": M(2.0, "measured")},
                 parts=[{"type": "box", "polygon": [[0, 0], [1, 0], [1, 1]], "z0": U(), "z1": U()}])
    s = schema.evidence_summary(b)
    # 面 2（f0 unknown, f1 measured）＋部品 1（unknown）→ 形がある 1/3
    assert s["shape_ratio"] == round(1 / 3, 3)
    # 測定値：floor_pitch・parapet_top・f0(length,count,pitch)・f1(length,count,pitch,z_bottom,z_top)・box(z0,z1) = 12、measured 6
    assert s["values"] == 12 and s["measured_values"] == 6 and s["evidence_ratio"] == 0.5
    assert s["state_counts"]["unknown"] == 2


def test_library_faces():
    p = ROOT / "data" / "buildings" / "library.json"
    if not p.exists():
        pytest.skip("図書館の JSON が無い")
    s = schema.evidence_summary(json.loads(p.read_text(encoding="utf-8")))
    st = {k: v["state"] for k, v in s["faces"].items()}
    assert st["f0"] == st["f2"] == st["f3"] == "unknown"
    assert st["f1"] in ("measured", "estimated")
    assert 0 < s["shape_ratio"] < 1 and 0 < s["evidence_ratio"] < 1


# --- glb の extras ---

def test_blender_build_exports_extras():
    src = (ROOT / "recorder" / "blender_build.py").read_text(encoding="utf-8")
    assert "export_extras=True" in src
    assert 'tag(body, ev["body"], part="body")' in src and "tag_face_materials(" in src
    assert "gray_faces=" in src            # unknown の面の壁は灰色


def _glb_json(path: Path) -> dict:
    d = path.read_bytes()
    n = struct.unpack("<I", d[12:16])[0]
    return json.loads(d[20:20 + n])


def test_library_glb_extras():
    glb = ROOT / "out" / "library" / "library.glb"
    if not glb.exists():
        pytest.skip("図書館の glb が無い（recorder model build data/buildings/library.json）")
    g = _glb_json(glb)
    mats = {m["extras"]["face"]: m["extras"]["evidence"] for m in g.get("materials", []) if "face" in (m.get("extras") or {})}
    if not mats:
        pytest.skip("glb が根拠の層より前に作られている")
    s = schema.evidence_summary(json.loads((ROOT / "data" / "buildings" / "library.json").read_text(encoding="utf-8")))
    for fid, d in s["faces"].items():
        assert mats.get(fid) == d["state"], fid
    nodes = [n.get("extras") or {} for n in g["nodes"]]
    assert any(e.get("part") == "body" for e in nodes)
    assert all(e["evidence"] in schema.EVIDENCE_STATES for e in nodes if "evidence" in e)


def test_export_web_has_ratios(tmp_path):
    if not (site.DATA_SITE / "tempaku.json").exists() or not (site.OUT_SITE / "dem5a.npz").exists():
        pytest.skip("敷地 JSON か標高が無い")
    site.export_web(web_dir=tmp_path)
    d = json.loads((tmp_path / "data" / "site.json").read_text(encoding="utf-8"))
    for v in d["designed"].values():
        assert 0 <= v["shape_ratio"] <= 1 and 0 <= v["evidence_ratio"] <= 1
        assert set(v["faces"].values()) <= set(schema.EVIDENCE_STATES)
    for b in d["boxes"]:
        if b["id"] not in d["designed"]:
            assert b["shape_ratio"] is None and not b["grounded"]      # PLATEAU だけの箱は今のまま灰色


def test_viewer_evidence_toggle():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    for s in ("根拠で見る", "userData.evidence", "evlegend", "hypothesis", "shape_ratio", "evidence_ratio", "origMat"):
        assert s in html, s
