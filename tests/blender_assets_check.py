"""Blender の中で assets_blender を試す（tests/test_assets.py の Blender 版から呼ぶ）。結果を JSON で標準出力へ。"""
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "recorder"))
import assets  # noqa: E402
import assets_blender  # noqa: E402

bpy.ops.wm.read_factory_settings(use_empty=True)
out = {}
for aid, size in (("ac_outdoor", (1.0, 0.4, 0.9)), ("street_lamp", (None, None, 4.5)), ("bench", None), ("tree_small", (None, None, 6.0))):
    root, warns = assets_blender.import_model(aid, at=(10.0, 5.0, 0.2), yaw=math.pi / 2, size_m=size)
    bpy.context.view_layer.update()
    lo, hi = Vector((1e9,) * 3), Vector((-1e9,) * 3)
    for o in root.children:
        for c in o.bound_box:
            p = o.matrix_world @ Vector(c)
            lo, hi = Vector(map(min, lo, p)), Vector(map(max, hi, p))
    out[aid] = {"size_xyz": [round(v, 3) for v in hi - lo], "bottom": round(lo.z, 3),
                "center": [round((lo.x + hi.x) / 2, 3), round((lo.y + hi.y) / 2, 3)], "warns": warns}
root2, _ = assets_blender.import_model("ac_outdoor", at=(0, 0, 0))
out["shared_mesh"] = root2.children[0].data == [r for r in bpy.data.objects if r.name.startswith("asset_ac_outdoor_0")][0].data
mats = {}
for aid in assets.ids(assets.load_catalog(), "texture"):
    m = assets_blender.material_from_texture(aid)
    mats[aid] = sorted(n.bl_idname for n in m.node_tree.nodes)
out["materials"] = mats
out["material_for"] = assets_blender.material_for("asset:brick_ochre").name
assets_blender.set_world_hdri("hdri_overcast")
out["world"] = bpy.context.scene.world.node_tree.nodes[0].image.name
print("RESULT " + json.dumps(out, ensure_ascii=False))
