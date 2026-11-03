"""blender_build.py（Blender の中）のテスト。BLENDER 環境変数が無ければ飛ばす。

11/4：タワー75 は「塔」と「基壇」の 2 つの塊が重なっている。塊を join で結合しただけだと
自己交差した形になり、最初の窓の彫り込み（EXACT）で塔が丸ごと消えて薄い殻だけが残った
（鳥瞰ではガラスと柱型だけが立ち、街路視点では窓が小さな印にしか見えなかった）。
塊は足し算のブーリアンで 1 つにし、彫り込みは体積で確かめる。その再発防止。
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent / "recorder"
BLENDER = os.environ.get("BLENDER")
needs_blender = pytest.mark.skipif(not BLENDER or not Path(BLENDER).exists(), reason="Blender 5.2 が無い")

INNER = r'''
import sys, json, bpy
sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import blender_build as bb
mode, src = sys.argv[-2], sys.argv[-1]
if mode == "build":
    b = json.loads(open(src, encoding="utf-8").read())
    bpy.ops.wm.read_factory_settings(use_empty=True)
    col = bpy.data.collections.new(b["id"])
    bpy.context.scene.collection.children.link(col)
    info = bb.build_building(b, col)
    print("RESULT", json.dumps({"volume": bb.mesh_volume(info["body"]),
                                "zmax": max(v.co.z for v in info["body"].data.vertices)}))
elif mode == "ground":
    b = json.loads(open(src, encoding="utf-8").read())
    bpy.ops.wm.read_factory_settings(use_empty=True)
    col = bpy.data.collections.new(b["id"])
    bpy.context.scene.collection.children.link(col)
    vol0 = None
    info = bb.build_building(b, col)
    names = sorted(o.name for o in bpy.data.objects)
    def zr(prefix):
        ob = next(o for o in bpy.data.objects if o.name.startswith(prefix))
        zs = [v.co.z for v in ob.data.vertices]
        return [min(zs), max(zs)]
    out = {"ground_volume": bb.mesh_volume(info["ground"]), "names": names,
           "assumed": [a["what"] for a in bb.ASSUMED]}
    for pre in ("ground_E_raised", "ground_S_floor", "ground_S_skirt"):
        if any(o.name.startswith(pre) for o in bpy.data.objects):
            out[pre] = zr(pre)
    print("RESULT", json.dumps(out))
else:
    out = {}
    for args in ([1000.0, 990.0, 10.0], [1000.0, 100.0, 10.0], [1000.0, 1010.0, 10.0]):
        try:
            bb.check_cut(*args, "t")
            out[str(args)] = "ok"
        except RuntimeError:
            out[str(args)] = "error"
    print("RESULT", json.dumps(out))
'''


def run_inner(tmp_path: Path, mode: str, src: str = "-") -> dict:
    script = tmp_path / "inner.py"
    script.write_text(INNER, encoding="utf-8")
    r = subprocess.run([BLENDER, "-b", "-P", str(script), "--", str(HERE), mode, src],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")), None)
    assert line, r.stdout[-3000:] + r.stderr[-2000:]
    return json.loads(line[len("RESULT "):])


def m(v):
    return {"value": v, "status": "estimated", "evidence": ["t"], "method": "テスト"}


@needs_blender
def test_overlapping_blocks_keep_tower_after_window_cut(tmp_path: Path):
    # 塔 10×10×30 が、基壇 20×20×5 の中に立つ（平面が重なる）。塔の 1 面に窓 3 列 × 2 段
    tower = [[0, 0], [10, 0], [10, 10], [0, 10]]
    podium = [[-5, -5], [15, -5], [15, 15], [-5, 15]]
    b = {
        "id": "t", "name": "t", "footprint": {"polygon": tower}, "parapet_top": m(30.0),
        "blocks": [{"id": "A", "polygon": tower, "top_z": m(30.0)},
                   {"id": "C", "polygon": podium, "top_z": m(5.0)}],
        "faces": [{"id": "S", "from": [0, 0], "to": [10, 0], "length": m(10.0), "top_z": m(30.0),
                   "bays": {"count": m(3), "pier_width": m(1.0), "pier_depth": m(0.15)},
                   "windows": [{"z_bottom": m(6.0), "z_top": m(8.0), "width": m(2.0), "recess": m(0.2), "rail": False},
                               {"z_bottom": m(10.0), "z_top": m(12.0), "width": m(2.0), "recess": m(0.2), "rail": False}]}],
    }
    src = tmp_path / "t.json"
    src.write_text(json.dumps(b), encoding="utf-8")
    res = run_inner(tmp_path, "build", str(src))
    union = 10 * 10 * 30 + 20 * 20 * 5 - 10 * 10 * 5      # 重なりは 1 回だけ数える
    assert res["zmax"] == pytest.approx(30.0, abs=0.01)
    # 窓の彫り込みは 1 か所あたり高々 (2-0.1)×2×(0.2+0.25) ≒ 1.7 m³、6 か所で 10 m³ 程度
    assert union - 15 < res["volume"] < union - 1


@needs_blender
def test_check_cut_flags_impossible_loss(tmp_path: Path):
    res = run_inner(tmp_path, "check")
    assert res["[1000.0, 990.0, 10.0]"] == "ok"        # 彫る量だけ減った
    assert res["[1000.0, 100.0, 10.0]"] == "error"     # 彫る量よりずっと減った＝形が壊れた
    assert res["[1000.0, 1010.0, 10.0]"] == "error"    # 引き算で増えた


def _ground_building(offset_s, offset_e):
    sq = [[0, 0], [10, 0], [10, 10], [0, 10]]
    faces = [{"id": "S", "from": [0, 0], "to": [10, 0], "length": m(10.0)},
             {"id": "E", "from": [10, 0], "to": [10, 10], "length": m(10.0)}]
    if offset_s is not None:
        faces[0]["ground_offset"] = {"value": offset_s, "unit": "m", "status": "estimated", "evidence": [], "method": "テスト"}
    if offset_e is not None:
        faces[1]["ground_offset"] = {"value": offset_e, "unit": "m", "status": "estimated", "evidence": [], "method": "テスト"}
    return {"id": "t", "name": "t", "footprint": {"polygon": sq}, "parapet_top": m(10.0), "faces": faces}


@needs_blender
def test_ground_offset_sunken_and_raised(tmp_path: Path):
    """沈んだ広場（−3.0）は地面の 1 枚から長方形を抜き、底の板と足元の壁を置く。高い広場（+1.4）は板を積む。"""
    src = tmp_path / "g.json"
    src.write_text(json.dumps(_ground_building(-3.0, 1.4)), encoding="utf-8")
    res = run_inner(tmp_path, "ground", str(src))
    # 地面 3000×3000×0.3 から 面の幅 10 × 奥行き 12 × 厚み 0.3 = 36 m³ が抜けた
    assert res["ground_volume"] == pytest.approx(3000 * 3000 * 0.3 - 36.0, abs=0.5)
    assert res["ground_S_floor"][1] == pytest.approx(-3.0, abs=1e-6)          # 底の板の上面が ground_offset
    assert res["ground_S_skirt"] == pytest.approx([-3.0, 0.0], abs=1e-6)       # 足元の壁は z=0 から地面まで
    assert res["ground_E_raised"][1] == pytest.approx(1.4, abs=1e-6)           # 高い広場の板の上面
    assert any("奥行き" in w for w in res["assumed"])                          # 奥行きは仮の値として report に出る


@needs_blender
def test_ground_offset_absent_changes_nothing(tmp_path: Path):
    src = tmp_path / "g0.json"
    src.write_text(json.dumps(_ground_building(None, None)), encoding="utf-8")
    res = run_inner(tmp_path, "ground", str(src))
    assert res["ground_volume"] == pytest.approx(3000 * 3000 * 0.3, abs=0.5)
    assert not [n for n in res["names"] if n.startswith("ground_")]
