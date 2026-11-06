"""証拠ビューア（recorder/evidence.py）：座標の変換・方法の判定・根拠の件数・記録する点の数（模型・写真なし）。"""

import json
import struct
from collections import Counter

import numpy as np
import pytest

from recorder import evidence as E


def _building():
    st = lambda v, s="estimated": {"value": v, "status": s, "evidence": ["x"], "method": "t"}  # noqa: E731
    return {"id": "t", "name": "試し棟", "ground_z": st(40.0, "measured"),
            "faces": [{"id": "S", "from": [0.0, 0.0], "to": [20.0, 0.0], "length": st(20.0, "measured"),
                       "top_z": 10.0, "ground_offset": 0.0, "bays": {}},
                      {"id": "E", "from": [20.0, 0.0], "to": [20.0, 10.0], "length": {"value": None, "status": "unknown"},
                       "top_z": 10.0, "ground_offset": 0.0}],
            "requests": [{"status": "open"}]}


def _cam_looking_north(C=(10.0, -30.0, 1.5), f=1000.0, w=1600, h=1200):
    # OpenCV のカメラ（x 右・y 下・z 前）が北（+y）を向く：カメラ→世界 = 列 (東, 下=-z, 北)
    Rwc = np.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]])
    R = Rwc.T
    t = -R @ np.array(C)
    return {"focal_px": f, "image_size": [w, h], "R_cv": R.tolist(), "t_cv": t.tolist()}


def test_gltf_to_world():
    # glTF の (x, y=上, z=-北) → 世界 (x, 北, 上)
    assert np.allclose(E.gltf_to_world([1.0, 2.0, -3.0]), [1.0, 3.0, 2.0])
    assert np.isclose(np.linalg.det(E.GLTF_TO_WORLD), 1.0)          # 回転（右手系のまま）


def test_camera_pose_roundtrip():
    cam = _cam_looking_north()
    C, Rwc = E.camera_pose(cam)
    assert np.allclose(C, [10.0, -30.0, 1.5])
    assert np.allclose(Rwc @ [0, 0, 1], [0, 1, 0])                  # 前は北


def test_method_of():
    assert E.method_of({"source": {"kind": "landmarks"}}) == "landmarks"
    assert E.method_of({"source": {"kind": "sfm"}, "kind": "pose", "via": ["a"]}) == "sfm"
    assert E.method_of({"kind": "pose", "via": ["a"], "source": {}}) == "chain"
    assert E.method_of({"source": {"front_json": "x"}}) == "rectify"
    assert E.entity_name("P223_本部棟の長辺") == "P223"
    assert E.entity_name("PXL_20261103_043259125") == "PXL_20261103_043259125"


def test_status_counts():
    c = E.status_counts(_building())
    assert c["total"] == Counter({"measured": 2, "unknown": 1})
    assert c["faces"]["S"] == Counter({"measured": 1})
    assert c["faces"]["E"] == Counter({"unknown": 1})
    assert c["other"] == Counter({"measured": 1})                     # ground_z。requests の open は数えない
    assert E.dominant_status(Counter()) == "unknown"
    assert E.dominant_status(Counter({"estimated": 3, "measured": 3})) == "measured"


def test_project_polyline_clips_behind():
    cam = _cam_looking_north()
    runs = E.project_polyline(cam, [[0, 0, 0], [20, 0, 0]])
    assert len(runs) == 1 and runs[0].shape[1] == 2
    u, v = runs[0][:, 0], runs[0][:, 1]
    assert np.all(np.diff(u) > 0) and np.allclose(v, v[0])           # 東へ進む地面の線は横一直線
    assert E.project_polyline(cam, [[0, -40, 0], [20, -40, 0]]) == []  # カメラの後ろは出さない


def test_plan_counts_and_picks():
    b = _building()
    from recorder.landmarks import catalog

    cat = catalog(b, lines=True)
    pts = [m for m in cat if m.get("kind") != "line"]
    assert pts, "目印が 1 つも無い"
    cam = _cam_looking_north()
    from recorder.registry import project_world

    m0 = pts[0]
    u, v, _ = project_world({**cam}, [m0["xyz"]])
    pick = {"id": "S-99", "name": m0["name"], "u": float(u[0]) + 3.0, "v": float(v[0]) + 4.0, "inlier": True}
    e = {"photo": "PXL_1", "building": "t", "face": "S", "kind": "pose", "camera": cam,
         "source": {"kind": "landmarks", "picks": [pick, {"id": "S-98", "name": "無い目印", "u": 1, "v": 1}]},
         "check": {"rms_px": 5.0}}
    idx = {"photos": [{"id": "PXL_1", "taken_at": "2026-11-05T15:00:00", "period": "during", "file": "x.jpg"}]}
    pl = E.plan(b, [e], idx, long_side=800)
    assert len(pl["photos"]) == 1 and len(pl["points"]) == len(pts)
    p = pl["photos"][0]
    assert p["method"] == "landmarks" and p["size"] == (800, 600) and np.isclose(p["focal"], 500.0)
    assert len(p["picks"]) == 1                                         # 番号でなく面と名前で照合。無い目印は落とす
    q = p["picks"][0]
    assert q["mark"] == m0["id"] and np.isclose(q["resid"], 5.0)        # 3-4-5
    assert np.allclose(q["uv"] - q["proj"], [1.5, 2.0])                 # 縮めた写真の px
    assert p["overlay"]                                                 # 見えている面 S の線が写る
    assert p["time"].utcoffset().total_seconds() == 9 * 3600
    assert E.pick_best(pl["photos"]) is p
    txt = E.summary_text(pl)
    assert "実測 measured **2**" in txt and "| S | 1 | 0 | 0 | 実測 |" in txt


def test_strip_gltf_extensions():
    doc = {"asset": {"version": "2.0"}, "extensionsUsed": ["KHR_texture_transform", "KHR_materials_emissive_strength"],
           "extensionsRequired": ["KHR_texture_transform"],
           "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0, "extensions": {
               "KHR_texture_transform": {"scale": [2, 2]}}}}}]}
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    binc = struct.pack("<II", 4, 0x004E4942) + b"\0\0\0\0"
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + binc
    glb = struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body
    out = E.strip_gltf_extensions(glb)
    assert struct.unpack("<I", out[8:12])[0] == len(out)
    jl = struct.unpack("<I", out[12:16])[0]
    d = json.loads(out[20:20 + jl])
    assert "extensionsRequired" not in d and d["extensionsUsed"] == ["KHR_materials_emissive_strength"]
    assert "extensions" not in d["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"]
    assert out.endswith(binc)


def test_record_without_model(tmp_path):
    pytest.importorskip("rerun")
    b = _building()
    e = {"photo": "PXL_1", "building": "t", "face": "S", "kind": "pose", "camera": _cam_looking_north(),
         "source": {"kind": "chain"}, "via": ["x"]}
    pl = E.plan(b, [e], {"photos": []})
    n = E.record(pl, tmp_path / "t.rrd", None, images=False)
    assert n["photos"] == 1 and n["landmarks"] == len(pl["points"]) and "model" not in n
    assert (tmp_path / "t.rrd").stat().st_size > 0
    bps = E.save_blueprints(pl, tmp_path)
    assert all(p.exists() for p in bps.values())
