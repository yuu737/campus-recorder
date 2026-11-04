"""CC0 アセットを Blender に取り込む（Blender の中で動く）。bpy は関数の中でだけ import する（pytest で落ちないように）。

blender_build.py からの呼び方（1 行）:
    import assets_blender; assets_blender.build_asset_parts(b, col)

関数
    import_model(aid, at=(x, y, z), yaw=0, size_m=(w, d, h), collection=None)
        glb / gltf / obj を取り込み、台帳の size_m と部品の w×d×h から拡大率を決めて置く。
        模型は「底面の中心」を原点に揃えてから、拡大 → yaw で回す → at に動かす。
        拡大率が 0.5〜2 の外なら warning を出す（返り値の 2 つ目、標準出力にも）。
        2 個目からは同じメッシュを共有する（取り込みは 1 回）。
    material_from_texture(aid, scale=1.0)
        PBR の基本色・法線・粗さを画像ノードにつないだ素材。物体座標（1 単位＝1 m）を tile_m で割って
        箱投影するので、壁でも床でも実寸どおりに貼れる。
    material_for(name)
        "asset:<id>" なら material_from_texture(id)、それ以外は None（既存の素材名はそのまま blender_build が扱う）。
    set_world_hdri(aid, strength=1.0)
        空と光を HDRI にする（ガラスの映り込み用）。
    build_asset_parts(b, col)
        棟 JSON の parts[type=asset] を全部置く。置いた物と警告の一覧を返す。
"""

from __future__ import annotations

import math

try:
    from . import assets  # recorder パッケージとして（pytest）
except ImportError:  # Blender の中で recorder/ を sys.path に入れて import されたとき
    import assets  # type: ignore[no-redef]

_TEMPLATES: dict[str, dict] = {}
_MATERIALS: dict[str, object] = {}
_CAT: dict | None = None


def _catalog(cat: dict | None = None) -> dict:
    global _CAT
    if cat is not None:
        return cat
    if _CAT is None:
        _CAT = assets.load_catalog()
    return _CAT


def _base_name(name: str) -> str:
    """Blender が重複に付ける .001 を外す。"""
    head, dot, tail = name.rpartition(".")
    return head if dot and tail.isdigit() else name


def _template(aid: str, cat: dict) -> dict:
    """1 回だけ取り込み、メッシュと「底面の中心が原点」になる変換を覚える。取り込んだ物体は消す（メッシュは残す）。"""
    if aid in _TEMPLATES:
        return _TEMPLATES[aid]
    import bpy
    from mathutils import Matrix, Vector

    it = assets.item(cat, aid)
    if it.get("kind") != "model":
        raise ValueError(f"{aid} は model ではありません（{it.get('kind')}）")
    path = assets.resolve(aid, cat)
    before = set(bpy.data.objects)
    suf = path.suffix.lower()
    if suf in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif suf == ".obj":
        bpy.ops.wm.obj_import(filepath=str(path), forward_axis="NEGATIVE_Z", up_axis="Y")
    else:
        raise ValueError(f"{aid}: {suf} は取り込めません")
    new = [o for o in bpy.data.objects if o not in before]
    node = it.get("node")
    if node:
        roots = [o for o in new if o.parent is None and _base_name(o.name) == node]
        if not roots:
            raise KeyError(f"{aid}: 節 {node} が取り込んだ物にありません")
        keep = set()
        for r in roots:
            keep.add(r)
            keep.update(r.children_recursive)
    else:
        keep = set(new)
    bpy.context.view_layer.update()
    meshes = [o for o in keep if o.type == "MESH"]
    if not meshes:
        raise ValueError(f"{aid}: メッシュがありません")
    lo, hi = Vector((math.inf,) * 3), Vector((-math.inf,) * 3)
    for o in meshes:
        for c in o.bound_box:
            p = o.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, p))
            hi = Vector(map(max, hi, p))
    shift = Matrix.Translation(-Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z)))
    parts = []
    for o in meshes:
        o.data.use_fake_user = True
        parts.append((o.data, shift @ o.matrix_world.copy()))
    for o in new:
        bpy.data.objects.remove(o, do_unlink=True)
    size = [round(hi.x - lo.x, 3), round(hi.y - lo.y, 3), round(hi.z - lo.z, 3)]
    t = {"parts": parts, "size": size}
    if any(abs(a - b) > max(0.05, 0.05 * b) for a, b in zip(size, it["size_m"])):
        print(f"[asset] {aid}: Blender で測った外形 {size} が台帳の size_m {it['size_m']} と違う")
    _TEMPLATES[aid] = t
    return t


def import_model(aid: str, at=(0.0, 0.0, 0.0), yaw: float = 0.0, size_m=None, collection=None,
                 name: str | None = None, cat: dict | None = None):
    """アセットの模型を 1 つ置く。返り値 (根の空オブジェクト, 警告の list)。

    at      置く点（底面の中心）。棟の座標（x＝東、y＝北、z＝棟の z=0 から）m
    yaw     z 軸まわりの回転（ラジアン）。0 で模型の正面（glTF の +Z）が南（-y）を向く
    size_m  部品の (w, d, h) m。unknown の軸は None。None 全体なら台帳の実寸のまま（警告）
    """
    import bpy

    cat = _catalog(cat)
    it = assets.item(cat, aid)
    t = _template(aid, cat)
    factors, warns = assets.scale_factors(it["size_m"], list(size_m) if size_m else [None, None, None])
    col = collection or bpy.context.scene.collection
    root = bpy.data.objects.new(name or f"asset_{aid}", None)
    root.empty_display_size = 0.3
    col.objects.link(root)
    for i, (mesh, M) in enumerate(t["parts"]):
        o = bpy.data.objects.new(f"{root.name}_{i}", mesh)
        col.objects.link(o)
        o.parent = root
        o.matrix_basis = M
    root.scale = factors
    root.rotation_euler = (0.0, 0.0, float(yaw))
    root.location = tuple(float(v) for v in at)
    root["asset"] = aid
    root["license"] = it["license"]
    root["source_url"] = it.get("page") or it["url"]
    for w in warns:
        print(f"[asset] {root.name}: {w}")
    return root, warns


def material_from_texture(aid: str, scale: float = 1.0, cat: dict | None = None):
    """texture の画像（基本色・法線・粗さ）を Principled BSDF につないだ素材。名前は "asset:<id>"。

    物体座標（1 単位＝1 m）を tile_m × scale で割り、箱投影で貼る（壁も床も実寸）。"""
    key = f"asset:{aid}" + ("" if scale == 1.0 else f"@{scale:g}")
    if key in _MATERIALS:
        return _MATERIALS[key]
    import bpy

    cat = _catalog(cat)
    it = assets.item(cat, aid)
    maps = assets.texture_maps(aid, cat)
    for k, p in maps.items():
        if not p.exists():
            raise FileNotFoundError(f"{aid}: {p} がありません（recorder assets fetch）")
    tw, th = it["tile_m"]
    mat = bpy.data.materials.new(key)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    coord = nt.nodes.new("ShaderNodeTexCoord")
    mapping = nt.nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (1.0 / (tw * scale), 1.0 / (th * scale), 1.0 / (th * scale))
    nt.links.new(coord.outputs["Object"], mapping.inputs["Vector"])

    def img(path, non_color: bool):
        n = nt.nodes.new("ShaderNodeTexImage")
        n.image = bpy.data.images.load(str(path), check_existing=True)
        if non_color:
            n.image.colorspace_settings.name = "Non-Color"
        n.projection = "BOX"
        n.projection_blend = 0.2
        nt.links.new(mapping.outputs["Vector"], n.inputs["Vector"])
        return n

    col = img(maps["color"], False)
    nt.links.new(col.outputs["Color"], bsdf.inputs["Base Color"])
    if "rough" in maps:
        r = img(maps["rough"], True)
        nt.links.new(r.outputs["Color"], bsdf.inputs["Roughness"])
    if "normal" in maps:
        n = img(maps["normal"], True)
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nt.links.new(n.outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    mat["asset"] = aid
    mat["license"] = it["license"]
    _MATERIALS[key] = mat
    return mat


def material_for(name, cat: dict | None = None):
    """素材名が "asset:<id>" なら CC0 の質感の素材、そうでなければ None。"""
    if isinstance(name, str) and name.startswith("asset:"):
        return material_from_texture(name.split(":", 1)[1], cat=cat)
    return None


def set_world_hdri(aid: str, strength: float = 1.0, cat: dict | None = None):
    """空と光を HDRI にする。"""
    import bpy

    cat = _catalog(cat)
    it = assets.item(cat, aid)
    if it.get("kind") != "hdri":
        raise ValueError(f"{aid} は hdri ではありません")
    path = assets.resolve(aid, cat)
    world = bpy.context.scene.world or bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    env = nt.nodes.new("ShaderNodeTexEnvironment")
    env.image = bpy.data.images.load(str(path), check_existing=True)
    bg = nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Strength"].default_value = strength
    out = nt.nodes.new("ShaderNodeOutputWorld")
    nt.links.new(env.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    return world


def build_asset_parts(b: dict, col=None, cat: dict | None = None) -> list[dict]:
    """棟 JSON の parts[type=asset] を全部置く。返り値は 1 部品ごとの {"index", "asset", "placed", "warnings"}。
    置けない物（along が unknown など）は置かずに warnings に書く（決めつけない）。"""
    cat = _catalog(cat)
    faces = {f.get("id"): f for f in b.get("faces", [])}
    report = []
    for i, pt in enumerate(b.get("parts", [])):
        if pt.get("type") != "asset":
            continue
        aid = pt.get("asset")
        rec = {"index": i, "asset": aid, "placed": 0, "warnings": []}
        try:
            spots, w = assets.placements(pt, faces.get(pt.get("face")))
            rec["warnings"] += w
            size = assets.part_size(pt)
            for k, (x, y, z, yaw) in enumerate(spots):
                _root, w2 = import_model(aid, (x, y, z), yaw, size, col, name=f"{pt.get('name') or aid}_{i}_{k}", cat=cat)
                if k == 0:
                    rec["warnings"] += w2
                rec["placed"] += 1
        except (KeyError, ValueError, FileNotFoundError) as e:
            rec["warnings"].append(str(e))
        for w in rec["warnings"]:
            print(f"[asset] parts[{i}] {aid}: {w}")
        report.append(rec)
    return report
