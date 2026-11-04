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

Web 用の焼き込み（11/4。blender_build が glb 書き出しの直前に使う）
    UV_NAME = "UV"        面の実寸（m）の UV。壁は (面に沿った水平距離, 高さ)、上向き・下向きの面は (x, y)
    face_uv(p, n)         1 点の m 単位の UV（bpy 不要。テストあり）
    needs_bake(types)     素材のノードの種類から「焼く必要があるか」（手続き的な模様があり、画像が無い）
    brick_period(...)     タイル割りが継ぎ目なく繰り返す周期（m）。目地の周期の整数倍
    ensure_meter_uv(ob)   m 単位の UV を持たない物に UV_NAME を作る
"""

from __future__ import annotations

import math

try:
    from . import assets  # recorder パッケージとして（pytest）
except ImportError:  # Blender の中で recorder/ を sys.path に入れて import されたとき
    import assets  # type: ignore[no-redef]

UV_NAME = "UV"
BAKE_PX = 1024        # --fast では 512
BAKE_PX_FAST = 512
BAKE_TARGET_M = 3.0   # 焼く板の大きさの目安（m）。周期はこれに近い、模様の周期の整数倍
# 画像を使わない手続き的な模様のノード（glb に出ない）。画像・環境の画像は除く
_PROCEDURAL = {"ShaderNodeTexBrick", "ShaderNodeTexNoise", "ShaderNodeTexVoronoi", "ShaderNodeTexWave",
               "ShaderNodeTexChecker", "ShaderNodeTexMagic", "ShaderNodeTexGradient", "ShaderNodeTexWhiteNoise",
               "ShaderNodeTexGabor", "ShaderNodeTexMusgrave", "ShaderNodeTexSky"}
_IMAGE = {"ShaderNodeTexImage", "ShaderNodeTexEnvironment"}


def needs_bake(node_types) -> bool:
    """素材のノードの種類の一覧から、焼き込みが要るか。画像テクスチャがある素材（CC0 の質感）は glb にそのまま入るので焼かない。"""
    t = set(node_types)
    return bool(t & _PROCEDURAL) and not (t & _IMAGE)


def brick_period(unit_w: float, unit_h: float, joint: float, target_m: float = BAKE_TARGET_M) -> tuple[float, float]:
    """タイル割り（1 段おきに半分ずらす）が継ぎ目なく繰り返す周期 (横, 縦) m。
    横は (タイル幅＋目地) の整数倍、縦は 2 段分 (タイル高さ＋目地)×2 の整数倍で、target_m に一番近い物。"""
    cw, ch = unit_w + joint, 2.0 * (unit_h + joint)
    if cw <= 0 or ch <= 0:
        raise ValueError("タイルの寸法が 0 以下")
    return max(1, round(target_m / cw)) * cw, max(1, round(target_m / ch)) * ch


def face_uv(p, n) -> tuple[float, float]:
    """点 p（世界座標 m）の、面の法線 n に応じた m 単位の UV。
    ほぼ水平な面（|n.z| > 0.7）は (x, y)。それ以外は (水平の接線方向の距離, z)。接線は法線を z 軸まわりに +90°。"""
    nx, ny, nz = n
    if abs(nz) > 0.7:
        return float(p[0]), float(p[1])
    L = math.hypot(nx, ny) or 1.0
    tx, ty = -ny / L, nx / L
    return float(p[0] * tx + p[1] * ty), float(p[2])


def ensure_meter_uv(ob) -> bool:
    """m 単位の UV（UV_NAME）が無ければ作る。作ったら True。既に UV_NAME があればそれを使う（作り直さない）。"""
    me = ob.data
    if me is None or UV_NAME in me.uv_layers:
        return False
    uv = me.uv_layers.new(name=UV_NAME)
    mw = ob.matrix_world
    nm = mw.to_3x3().inverted_safe().transposed()
    verts = me.vertices
    for pg in me.polygons:
        n = (nm @ pg.normal).normalized()
        for li in pg.loop_indices:
            p = mw @ verts[me.loops[li].vertex_index].co
            uv.data[li].uv = face_uv(p, n)
    return True


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

    m 単位の UV（UV_NAME。ensure_meter_uv）を tile_m × scale で割って貼る（壁も床も実寸）。
    UV と割り算（Mapping）は glTF の UV と KHR_texture_transform にそのまま出るので、Web でも同じ貼り方になる。"""
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
    coord = nt.nodes.new("ShaderNodeUVMap")
    coord.uv_map = UV_NAME
    mapping = nt.nodes.new("ShaderNodeMapping")
    mapping.inputs["Scale"].default_value = (1.0 / (tw * scale), 1.0 / (th * scale), 1.0)
    nt.links.new(coord.outputs["UV"], mapping.inputs["Vector"])

    def img(path, non_color: bool):
        n = nt.nodes.new("ShaderNodeTexImage")
        n.image = bpy.data.images.load(str(path), check_existing=True)
        if non_color:
            n.image.colorspace_settings.name = "Non-Color"
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
