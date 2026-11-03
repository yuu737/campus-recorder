"""模型（Blender の外側）のテスト。Blender 本体は要らない。"""
import json
from pathlib import Path

from PIL import Image

from recorder import model
from recorder.__main__ import main as cli_main


def test_sheet_composes_from_views(tmp_path: Path):
    out = tmp_path / "x"
    out.mkdir()
    for n in ("view_A", "view_B", "view_C", "view_D", "view_top", "persp_sketch"):
        im = Image.new("RGB", (400, 300), "white")
        im.paste((0, 0, 0), (100, 100, 300, 200))
        im.save(out / f"{n}.png")
    (out / "views.json").write_text(json.dumps([{"file": f"view_{k}.png", "label": f"{k}立面"} for k in "ABCD"]), encoding="utf-8")
    (out / "model_report.json").write_text(json.dumps({"assumed": [{"what": "a", "value": 1, "why": "b"}]}), encoding="utf-8")
    b = {"id": "x", "name": "試し", "faces": [{"id": "S", "length": {"value": 10, "evidence": ["plateau:1"]},
                                               "bays": {"count": {"value": 3, "evidence": ["P1"]}}}]}
    p = model.sheet(out, b)
    assert p.exists() and Image.open(p).size == (3508, 2480)


def test_autocrop_trims_white():
    im = Image.new("RGB", (400, 300), "white")
    im.paste((0, 0, 0), (100, 100, 300, 200))
    c = model._autocrop(im, margin=10)
    assert c.size == (220, 120)


def test_cli_model_refuses_invalid(tmp_path: Path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "campus-recorder/building/1", "id": "b", "name": "b",
                               "floor_pitch": {"value": 3.0, "status": "measured", "evidence": []}}), encoding="utf-8")
    assert cli_main(["model", "build", str(bad)]) == 1
    assert "規約違反" in capsys.readouterr().out
