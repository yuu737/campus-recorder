"""棟 JSON から Blender で模型を組み立て、描画と glb 書き出しをする（Blender の中で動く）。

使い方（recorder の CLI から呼ぶ）:
    blender -b -P recorder/blender_build.py -- data/buildings/honbu.json out/honbu [--fast]

出てくる物（out/<id>/）:
    persp_sketch.png   鳥瞰のスケッチ風（線画）
    persp_shaded.png   鳥瞰の着色（JSON の素材から作ったタイル、空、影）
    persp_shaded2.png  別の向きの鳥瞰（着色）
    view_A〜D.png      四面図（棟の一番長い面に正対した 4 方向、正射影の線画）
    view_top.png       屋根伏せ（真上の線画）
    <id>.glb           Web ビューア用
    model_report.json  模型を作るときに置いた仮の値（JSON に無い物）。全部ここに出す

設計の語彙（棟 JSON）
    blocks[]            棟を高さの違う塊に分ける。{id, polygon, top_z}。無ければ footprint 全体を parapet_top まで
    faces[]             面。bays（柱割り）、windows（階ごとの窓）、bands（帯）、openings（ピロティ）、glazing（ガラス張りの範囲）、materials
    parts[]             部品。type で分ける:
        box             {polygon, z0, z1, material}            塔屋・階段塔・設備の箱
        balcony         {face, along0, along1, z, depth, parapet_h, material}   踊り場・テラス・渡り廊下
        coping          {height, overhang}                     パラペットの笠木（全部の塊の上端）
        ac_units        {face, along, count, w, h, d, gap}     室外機の列（地面置き）
        pipes           {face, along, count, radius, z0, z1}   縦の配管・雨樋
        tree            {x, y, height, radius}                 木（丸い樹冠。種類は後で CC0 素材に差し替え）
        pavement        {polygon, z, material}                 舗装（煉瓦など）
        ac_units.z0     室外機の下端（壁付け）。省略は面の前の地形＋0.1
    新しい語彙（11/4。形は parts_geom.py。立面図も同じ形を使う）:
        stair_switchback {face, along, width, depth, landing_z[] | floors+floor_pitch, landing_len, parapet{height,material},
                          rail{height,post_pitch}, pilotis{count|along[],size,shape}, exit_box{height}, canopy{shape:"barrel",rise}}
        porch           {face, along, width, depth, height, glass{along0,along1,depth,mullion_pitch,transom_pitch}, columns{width}}
                        壁を面ごとのブーリアンでくり抜く（check_cut で体積を検査）
        steps           {face, along, width, depth, count, rise, tread, sides[], edge:"straight"|"curved", radius}
        rail            {face, along0, along1, z, height, post_pitch}   帯・腰壁の上の手すり（bands[].rail でも書ける）
        column          {x, y, diameter, z0, z1, section:"round"|"square"}
        rooftop_units   {source:"aerial", lean_correction_m:[dx,dy], z0, units:[{polygon|center+w+d, h}]}
        faces[].windows[].spandrel {height, depth, material, in_recess}   窓の下の濃いパネル（in_recess で窓の奥まりの中）
    語彙 その 2（11/4 午後）: ceiling_beams・louver・planter・nameplate・patch・vestibule・pergola・bridge・curved_glass
        （書き方は parts_geom.py の各関数と docs/手順_エージェント.md）。ガラスは透ける（material_glass、Alpha 0.35）

地面（11/4）:
    国土地理院 5m 標高（out/site/dem5a.npz）から ground_z を引いた地形の面 1 枚（足元の外側 20 m、1 m 格子。recorder.site.terrain_grid）。
    建物は地形を切り欠かず、地形の一番低い所の 1 m 下まで伸ばす（footing_*）。
    11/5：faces[].ground_offset で面の前の帯（既定 12 m）の地形を上書きする（site.apply_ground_overrides。5 m 標高と 4 m 超
    食い違う値は上書きせず report に理由）。検算（ground_offset_checks / warnings）は元の 5 m 標高で続ける。
    地表の種類（OSM の車道・歩道・芝…）は out/site/ground_mask.npz（site export-web が書く）で地形の四角ごとに色分けする。室外機・段・玄関・外階段の足元は面の前の地形から。
    四面図・屋根伏せでは地形を描かない。鳥瞰・街路視点・glb では描く（背景の粗い地形 terrain_far は描画だけ）。

決まり:
    形は全部 JSON から。値が unknown の所は、模型として立てるための「仮の値」を使い、report に列挙する。
    仮の値は JSON には書かない（規約1）。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import bpy
import bmesh
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import parts_geom  # noqa: E402  （部品の形。bpy を使わない共通の幾何）
import numpy as np  # noqa: E402  （Blender 同梱）

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from recorder import site  # noqa: E402  （地形：5m 標高の格子。numpy だけで動く関数を使う）
from recorder import schema  # noqa: E402  （根拠の層：面・部品の状態。glb の extras に出す）
from recorder import daylight  # noqa: E402  （太陽と空：data/daylight.json。歩く画面と同じ物を読む）

ASSUMED: list[dict] = []
UNKNOWN_COLOR = (0.62, 0.62, 0.62, 1.0)
UP = Vector((0, 0, 1))


def assume(what: str, value, why: str):
    ASSUMED.append({"what": what, "value": value, "why": why})
    return value


def val(m, default=None, what: str = "", why: str = ""):
    """Measurement（か裸の数）から値を取る。無ければ仮の値（記録する）。"""
    if isinstance(m, (int, float)):
        return m
    if m and m.get("value") is not None and m.get("status") != "unknown":
        return m["value"]
    if what:
        return assume(what, default, why)
    return default


# --- 形を作る道具 -----------------------------------------------------------------

def new_object(name: str, mesh, col):
    ob = bpy.data.objects.new(name, mesh)
    col.objects.link(ob)
    return ob


def ccw(pts):
    a2 = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
    return list(pts) if a2 >= 0 else list(reversed(pts))


def prism(name: str, pts, z0: float, z1: float, col):
    """平面多角形を z0〜z1 に持ち上げた柱体。"""
    pts = ccw([tuple(p) for p in pts])
    bm = bmesh.new()
    bot = [bm.verts.new((x, y, z0)) for x, y in pts]
    top = [bm.verts.new((x, y, z1)) for x, y in pts]
    n = len(pts)
    bm.faces.new(list(reversed(bot)))
    bm.faces.new(top)
    for i in range(n):
        bm.faces.new((bot[i], bot[(i + 1) % n], top[(i + 1) % n], top[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return new_object(name, me, col)


def box(name: str, center: Vector, u: Vector, v: Vector, w: Vector, su: float, sv: float, sw: float, col):
    bm = bmesh.new()
    vs = []
    for a in (-0.5, 0.5):
        for b in (-0.5, 0.5):
            for c in (-0.5, 0.5):
                vs.append(bm.verts.new(center + u * (a * su) + v * (b * sv) + w * (c * sw)))
    for f in [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]:
        bm.faces.new([vs[i] for i in f])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return new_object(name, me, col)


def cylinder(name: str, center: Vector, radius: float, z0: float, z1: float, col, seg: int = 12):
    pts = [(center.x + radius * math.cos(2 * math.pi * i / seg), center.y + radius * math.sin(2 * math.pi * i / seg)) for i in range(seg)]
    return prism(name, pts, z0, z1, col)


def sphere(name: str, center: Vector, radius: float, col):
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=2, radius=radius)
    for v in bm.verts:
        v.co = Vector((v.co.x, v.co.y, v.co.z * 0.8)) + center
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = new_object(name, me, col)
    for p in me.polygons:
        p.use_smooth = True
    return ob


def solid(name: str, ring0, ring1, col):
    """同じ形の 2 つの輪（Vector の並び）を結んだ閉じた形。"""
    bm = bmesh.new()
    v0 = [bm.verts.new(p) for p in ring0]
    v1 = [bm.verts.new(p) for p in ring1]
    n = len(v0)
    bm.faces.new(list(reversed(v0)))
    bm.faces.new(v1)
    for i in range(n):
        bm.faces.new((v0[i], v0[(i + 1) % n], v1[(i + 1) % n], v1[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return new_object(name, me, col)


def prim_object(name: str, pr: dict, frame, col):
    """parts_geom の素片 1 つを Blender の形に。frame = (始点, 面に沿う向き, 外向き)。"""
    p0, u, nv = frame

    def W(a, n, z):
        return p0 + u * a + nv * n + UP * z

    k = pr["k"]
    if k == "prism":
        r0 = [W(a, n, pr["z0"]) for a, n in pr["pts"]]
        r1 = [W(a, n, pr["z1"]) for a, n in pr["pts"]]
    elif k == "ext_a":
        r0 = [W(a, pr["n0"], z) for a, z in pr["pts"]]
        r1 = [W(a, pr["n1"], z) for a, z in pr["pts"]]
    else:
        r0 = [W(pr["a0"], n, z) for n, z in pr["pts"]]
        r1 = [W(pr["a1"], n, z) for n, z in pr["pts"]]
    return solid(name, r0, r1, col)


def emit_prims(prefix: str, prims, frame, col, mats, fmat=None) -> list:
    """素片の並びを形にして素材を付ける。mat が "tile"（か無し）なら面の仕上げ fmat。"""
    obs = []
    for pr in prims:
        ob = prim_object(f"{prefix}_{pr['name']}", pr, frame, col)
        ob.data.materials.append(fmat if (pr.get("mat") in (None, "tile") and fmat is not None) else mats.named(pr.get("mat")))
        obs.append(ob)
    return obs


def mesh_volume(ob) -> float:
    """閉じた形の体積（m³）。形は世界座標で作っている（オブジェクトの変換は単位）ので data のままでよい。"""
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    v = abs(bm.calc_volume())
    bm.free()
    return v


def check_cut(before: float, after: float, cutter: float, name: str, tol: float = 0.5) -> None:
    """引き算のブーリアンの結果が筋の通る体積か確かめる。

    引き算は「彫る物の体積より多くは減らない」「増えない」。これを外れたら形が壊れている
    （重なった塊を結合しただけの本体に EXACT を当てると、塔が丸ごと消えて薄い殻だけ残った。11/4）。
    黙って描画に進まず、ここで止める。
    """
    lost = before - after
    if lost > cutter * 1.01 + tol or after > before + tol:
        raise RuntimeError(
            f"ブーリアンが壊れました（{name}）：体積 {before:.1f} → {after:.1f} m³、彫る物は {cutter:.1f} m³。"
            "本体が自己交差していないか（塊が重なったまま結合されていないか）を確かめてください")


def union_bodies(bodies):
    """塊（blocks）を 1 つの閉じた形にまとめる。

    結合（join）だけだと、重なった塊（基壇の中に塔が入る等）が自己交差した形になり、
    後の窓の彫り込み（EXACT）で塔が消える。足し算のブーリアンで本当に 1 つにする。
    """
    body = bodies[0]
    for ob in bpy.context.scene.objects:
        ob.select_set(False)
    bpy.context.view_layer.objects.active = body
    for other in bodies[1:]:
        before, add = mesh_volume(body), mesh_volume(other)
        mod = body.modifiers.new("union", "BOOLEAN")
        mod.operation = "UNION"
        mod.object = other
        mod.solver = "EXACT"
        bpy.ops.object.modifier_apply(modifier=mod.name)
        after = mesh_volume(body)
        if after < max(before, add) - 0.5 or after > before + add + 0.5:
            raise RuntimeError(f"塊の足し算が壊れました（{other.name}）：{before:.1f} ＋ {add:.1f} → {after:.1f} m³")
        bpy.data.objects.remove(other, do_unlink=True)
    return body


def boolean_cut(target, cutters):
    """彫る箱を 1 つに結合してから 1 回だけブーリアン（200 個を順に当てると数分かかる）。"""
    import time
    t0 = time.time()
    # 窓（互いに離れている）は 1 つに結合。ピロティ・ガラスなど面が接する物は別々に当てる
    # 窓は面ごとに結合（別の面の窓は角で互いに食い込むことがあり、1 つに結合すると自己交差で失敗する）
    by_face: dict[str, list] = {}
    others = []
    for c in cutters:
        if c.name.startswith("cut_win|"):
            by_face.setdefault(c.name.split("|")[1], []).append(c)
        else:
            others.append(c)
    groups = []
    for fid, wins in by_face.items():
        for ob in bpy.context.scene.objects:
            ob.select_set(False)
        for c in wins:
            c.select_set(True)
        bpy.context.view_layer.objects.active = wins[0]
        if len(wins) > 1:
            bpy.ops.object.join()
        groups.append(wins[0])
    groups += others
    for ob in bpy.context.scene.objects:
        ob.select_set(False)
    bpy.context.view_layer.objects.active = target
    for cutter in groups:
        name, before, cv = cutter.name, mesh_volume(target), mesh_volume(cutter)
        mod = target.modifiers.new("cut", "BOOLEAN")
        mod.operation = "DIFFERENCE"
        mod.object = cutter
        mod.solver = "EXACT"
        bpy.ops.object.modifier_apply(modifier=mod.name)
        check_cut(before, mesh_volume(target), cv, name)
        bpy.data.objects.remove(cutter, do_unlink=True)
    print(f"ブーリアン: {time.time() - t0:.1f} 秒（{len(groups)} 回）")


def face_frame(f: dict):
    """面の始点・単位方向・外向き法線・長さ。"""
    p0, p1 = Vector((*f["from"], 0.0)), Vector((*f["to"], 0.0))
    d = p1 - p0
    L = d.length
    u = d / L
    n = Vector((u.y, -u.x, 0.0))
    return p0, u, n, L


# --- 素材 ---------------------------------------------------------------------------

def hex_to_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def material_brick(name: str, color: str, unit_w: float, unit_h: float, joint: float):
    """JSON の寸法どおりのタイル割り。m 単位の UV（assets_blender.UV_NAME：壁は面に沿った水平距離と高さ）で貼る。
    11/4 まで物体座標の x, y で貼っていた（Brick は 2 次元なので、壁では縦縞になっていた）。
    Web 用には同じ模様を周期 brick_period の板に焼いて glb に入れる（export_web_materials）。"""
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    brick = nt.nodes.new("ShaderNodeTexBrick")
    noise = nt.nodes.new("ShaderNodeTexNoise")
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    coord = nt.nodes.new("ShaderNodeUVMap")
    coord.uv_map = "UV"
    r, g, b = hex_to_rgb(color)
    brick.inputs["Color1"].default_value = (r, g, b, 1)
    brick.inputs["Color2"].default_value = (r * 0.92, g * 0.9, b * 0.88, 1)
    brick.inputs["Mortar"].default_value = (r * 0.72, g * 0.7, b * 0.68, 1)
    brick.inputs["Scale"].default_value = 1.0
    brick.inputs["Mortar Size"].default_value = joint
    brick.inputs["Mortar Smooth"].default_value = 0.0
    brick.inputs["Bias"].default_value = 0.0
    brick.inputs["Brick Width"].default_value = unit_w + joint
    brick.inputs["Row Height"].default_value = unit_h + joint
    brick.offset = 0.5
    brick.offset_frequency = 2
    noise.inputs["Scale"].default_value = 0.4
    noise.inputs["Detail"].default_value = 2.0
    nt.links.new(coord.outputs["UV"], brick.inputs["Vector"])
    nt.links.new(coord.outputs["UV"], noise.inputs["Vector"])
    nt.links.new(brick.outputs["Color"], mix.inputs[6])
    mix.inputs[7].default_value = (r * 0.85, g * 0.85, b * 0.85, 1)
    nt.links.new(noise.outputs["Fac"], mix.inputs[0])    # 明るさのムラだけ（色相は変えない）
    mix.blend_type = "MIX"
    nt.links.new(mix.outputs[2], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.8
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    try:
        import assets_blender
        mat["bake_period_m"] = list(assets_blender.brick_period(unit_w, unit_h, joint))
    except Exception as e:  # noqa: BLE001
        print(f"[warn] bake period {name}: {e}", file=sys.stderr)
    return mat


HYPOTHESIS_RGBA = (0.95, 0.76, 0.10, 1.0)     # 仮説の面の縞の黄（Web の根拠モードの縞と同じ系統）
HYPOTHESIS_STRIPE_M = 1.2                     # 縞の周期（m、斜め）


HYPOTHESIS_ATTR = "hypothesis"                # 本体の面の属性（1＝仮説の面の壁）。線画の白い上書き素材もこれで縞を描く


def material_hypothesis(name: str, base_rgba, by_attr: bool = False):
    """仮説の面の壁：元の色と黄の斜めの縞（物体座標 (x+y+z) / 周期 の小数部が 0.65 以上を黄）。
    by_attr=True は線画の上書き素材用（面の属性 HYPOTHESIS_ATTR が 1 の所だけ縞）。描画だけに使い、glb の前に外す。"""
    mat = material_plain(name, base_rgba, roughness=1.0 if by_attr else 0.8)
    nt = mat.node_tree
    bsdf = nt.nodes["Principled BSDF"]
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")

    def math(op, a=None, b=None):
        n = nt.nodes.new("ShaderNodeMath")
        n.operation = op
        for i, v in ((0, a), (1, b)):
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = v
            elif v is not None:
                nt.links.new(v, n.inputs[i])
        return n.outputs[0]

    nt.links.new(tc.outputs["Object"], sep.inputs[0])
    xyz = math("ADD", math("ADD", sep.outputs[0], sep.outputs[1]), sep.outputs[2])
    fac = math("GREATER_THAN", math("FRACT", math("DIVIDE", xyz, HYPOTHESIS_STRIPE_M)), 0.65)
    if by_attr:
        at = nt.nodes.new("ShaderNodeAttribute")
        at.attribute_name = HYPOTHESIS_ATTR
        fac = math("MULTIPLY", fac, at.outputs["Fac"])
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.inputs[6].default_value = base_rgba
    mix.inputs[7].default_value = HYPOTHESIS_RGBA
    nt.links.new(fac, mix.inputs[0])
    nt.links.new(mix.outputs[2], bsdf.inputs["Base Color"])
    return mat


def material_plain(name: str, rgba, roughness=0.8, metallic=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = rgba
    bsdf.inputs["Roughness"].default_value = roughness
    bsdf.inputs["Metallic"].default_value = metallic
    return mat


GLASS_RGBA = (0.62, 0.76, 0.74, 0.35)     # 淡い青緑、不透明度 0.35（Web も glb の alpha から同じ値を読む）


def material_glass(name: str = "glass"):
    """透けるガラス。Principled の Alpha（EEVEE は BLENDED で描く）、IOR 1.5、粗さ 0.05。
    glTF には alphaMode BLEND で出る（KHR_materials_transmission は three.js で重いので使わない）。"""
    mat = material_plain(name, (*GLASS_RGBA[:3], 1.0), roughness=0.05, metallic=0.0)
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Alpha"].default_value = GLASS_RGBA[3]
    bsdf.inputs["IOR"].default_value = 1.5
    for attr, v in (("surface_render_method", "BLENDED"), ("blend_method", "BLEND"), ("use_transparent_shadow", True),
                    ("use_transparency_overlap", True), ("shadow_method", "HASHED"), ("use_backface_culling", False)):
        try:
            setattr(mat, attr, v)
        except (AttributeError, TypeError):
            pass
    return mat


class Mats:
    def __init__(self, b: dict, gray_faces=()):
        self.by_face: dict[str, bpy.types.Material] = {}
        cache: dict[str, bpy.types.Material] = {}
        for f in b.get("faces", []):
            m = (f.get("materials") or [None])[0]
            if m and m.get("color"):
                key = json.dumps(m, sort_keys=True)
                if key not in cache:
                    cache[key] = material_brick(
                        f"tile_{f['id']}", m["color"],
                        val(m.get("unit_w"), 0.227, f"{f['id']} タイル幅", "JIS 二丁掛"),
                        val(m.get("unit_h"), 0.06, f"{f['id']} タイル高さ", "JIS 二丁掛"),
                        val(m.get("joint_w"), 0.01, f"{f['id']} 目地", "JIS 標準"))
                self.by_face[f["id"]] = cache[key]
        self.unknown = material_plain("unknown_face", UNKNOWN_COLOR)
        self.main = next(iter(self.by_face.values()), self.unknown)
        if self.main is not self.unknown:
            for f in b.get("faces", []):
                if f["id"] not in self.by_face and f["id"] not in gray_faces:
                    assume(f"{f['id']} の仕上げ", "主材料と同じ", "この面の材料は未測。主材料のタイルと同じと置いた")
        self.glass = material_glass("glass")
        self.interior = material_plain("interior", (0.07, 0.07, 0.08, 1.0), roughness=0.9)     # ガラスの奥の室内の暗さ
        self.concrete = material_plain("concrete", (0.80, 0.79, 0.76, 1.0))
        self.dark = material_plain("dark_band", (0.22, 0.17, 0.13, 1.0), roughness=0.6)
        self.metal = material_plain("metal", (0.75, 0.76, 0.78, 1.0), roughness=0.4, metallic=0.6)
        self.ground = material_plain("ground", (0.50, 0.49, 0.46, 1.0), roughness=0.95)
        self._ground_kinds: dict = {}
        self.pavement = material_plain("pavement", (0.46, 0.33, 0.28, 1.0), roughness=0.95)
        self.leaf = material_plain("leaf", (0.20, 0.33, 0.14, 1.0), roughness=0.95)
        self.trunk = material_plain("trunk", (0.30, 0.22, 0.15, 1.0))
        self.coping = material_plain("coping", (0.25, 0.25, 0.26, 1.0), roughness=0.5)
        self.curtain = material_plain("curtain", (0.20, 0.20, 0.19, 1.0), roughness=1.0)   # 透けるガラスの奥の室内（暗め。11/4 午後 0.42→0.20）
        self.roof = material_plain("roof", (0.52, 0.51, 0.49, 1.0), roughness=0.95)
        self.stone = material_plain("stone", (0.36, 0.30, 0.30, 1.0), roughness=0.5)          # 御影石の段（赤みの灰）
        self.white = material_plain("white", (0.88, 0.88, 0.86, 1.0), roughness=0.8)          # 軒裏・段の裏
        self.canopy = material_plain("canopy", (0.80, 0.84, 0.80, 1.0), roughness=0.3)        # かまぼこ庇（透ける板。色だけ）

    def ground_kind(self, kind: str):
        """地表の種類（OSM）の素材。色は site.GROUND_KIND_COLOR（sRGB）を線形にした単色。"""
        if kind not in self._ground_kinds:
            rgb = [(c / 255.0) ** 2.2 for c in site.GROUND_KIND_COLOR[kind]]
            self._ground_kinds[kind] = material_plain(f"ground_{kind}", (*rgb, 1.0), roughness=0.95 if kind != "grass" else 1.0)
        return self._ground_kinds[kind]

    def ground_mask_image(self, mask: dict, png: Path):
        """背景の粗い地形（10 m 格子）用：地表の種類の色の画像（out/site/ground_mask_color.png、1 m／画素）を
        物の座標（＝世界の x, y）から貼る。四角ごとの色分けでは 10 m の段になり道が消えるので。glb には入らない（terrain_far）。"""
        mat = bpy.data.materials.new("ground_mask_far")
        mat.use_nodes = True
        nt = mat.node_tree
        bsdf = nt.nodes["Principled BSDF"]
        bsdf.inputs["Roughness"].default_value = 0.95
        img = bpy.data.images.load(str(png), check_existing=True)
        h, w = mask["mask"].shape
        W, H = w * mask["step"], h * mask["step"]
        tc = nt.nodes.new("ShaderNodeTexCoord")
        mp = nt.nodes.new("ShaderNodeMapping")
        mp.vector_type = "POINT"
        mp.inputs["Scale"].default_value = (1.0 / W, 1.0 / H, 1.0)
        mp.inputs["Location"].default_value = (-mask["x0"] / W, -(mask["y_top"] - H) / H, 0.0)
        tx = nt.nodes.new("ShaderNodeTexImage")
        tx.image = img
        tx.extension = "EXTEND"
        nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
        nt.links.new(mp.outputs["Vector"], tx.inputs["Vector"])
        nt.links.new(tx.outputs["Color"], bsdf.inputs["Base Color"])
        return mat

    def named(self, name: str | None):
        if isinstance(name, str) and name.startswith("asset:"):      # CC0 の質感（data/assets/catalog.json）
            try:
                import assets_blender
                m = assets_blender.material_for(name)
                if m is not None:
                    return m
            except Exception as e:  # noqa: BLE001  取れなければ既定の素材に落として続ける
                print(f"[warn] asset material {name}: {e}", file=sys.stderr)
        return {"glass": self.glass, "concrete": self.concrete, "dark": self.dark, "metal": self.metal,
                "tile": self.main, "coping": self.coping, "brick": self.pavement, "stone": self.stone,
                "white": self.white, "canopy": self.canopy, "leaf": self.leaf, "interior": self.interior,
                "roof": self.roof, "curtain": self.curtain}.get(name or "tile", self.main)


def _has_texture(mat) -> bool:
    return bool(mat and mat.use_nodes and any(n.bl_idname.startswith("ShaderNodeTex") for n in mat.node_tree.nodes))


def add_meter_uvs(col) -> int:
    """模様か画像のある素材を使う物に m 単位の UV（"UV"）を作る。描画も Web も同じ UV で貼る。"""
    import assets_blender
    k = 0
    for ob in col.all_objects:
        if ob.type == "MESH" and any(_has_texture(m) for m in ob.data.materials):
            k += assets_blender.ensure_meter_uv(ob)
    return k


# --- Web 用の焼き込み（glb には手続き的な模様が出ないので、画像にして入れる） -----------------------

def _bake_plate(mat, pw: float, ph: float):
    """周期 pw×ph m の板。UV "UV" は m（素材が読む）、UV "bake" は 0〜1（焼く先の画像）。"""
    me = bpy.data.meshes.new("bake_plate")
    me.from_pydata([(0, 0, 0), (pw, 0, 0), (pw, ph, 0), (0, ph, 0)], [], [(0, 1, 2, 3)])
    uvm = me.uv_layers.new(name="UV")
    uvb = me.uv_layers.new(name="bake")
    for li, (a, c) in enumerate(((0, 0), (1, 0), (1, 1), (0, 1))):
        uvm.data[li].uv = (a * pw, c * ph)
        uvb.data[li].uv = (a, c)
    me.uv_layers.active = uvb
    ob = bpy.data.objects.new("bake_plate", me)
    bpy.context.scene.collection.objects.link(ob)
    ob.data.materials.append(mat)
    return ob


def _principled(mat):
    return next((n for n in mat.node_tree.nodes if n.bl_idname == "ShaderNodeBsdfPrincipled"), None)


def _bake_one(mat, kind: str, img, pw: float, ph: float) -> None:
    """kind = "color"（Base Color を Emission に付け替えて EMIT）か "normal"（接空間の NORMAL）。"""
    tmp = mat.copy()
    nt = tmp.node_tree
    bsdf = _principled(tmp)
    if kind == "color":
        em = nt.nodes.new("ShaderNodeEmission")
        src = bsdf.inputs["Base Color"]
        if src.is_linked:
            nt.links.new(src.links[0].from_socket, em.inputs["Color"])
        else:
            em.inputs["Color"].default_value = src.default_value
        outn = next(n for n in nt.nodes if n.bl_idname == "ShaderNodeOutputMaterial")
        nt.links.new(em.outputs["Emission"], outn.inputs["Surface"])
    tn = nt.nodes.new("ShaderNodeTexImage")
    tn.image = img
    nt.nodes.active = tn
    ob = _bake_plate(tmp, pw, ph)
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    if kind == "color":
        bpy.ops.object.bake(type="EMIT", use_clear=True, margin=0)
    else:
        bpy.ops.object.bake(type="NORMAL", normal_space="TANGENT", use_clear=True, margin=0)
    bpy.data.objects.remove(ob, do_unlink=True)
    bpy.data.materials.remove(tmp)


def export_web_materials(objs, tex_dir: Path, fast: bool) -> tuple[dict, list[dict]]:
    """glb 用の素材の差し替え表 {元の素材: 書き出し用の素材} と、焼いた記録を返す。描画用の素材はそのまま。

    焼く物：画像を持たない手続き的な素材（assets_blender.needs_bake）。周期（bake_period_m、無ければ 3 m 四方）の板に
    基本色を EMIT で、法線（素材に凹凸がある時だけ）を NORMAL で焼き、tex_dir/<素材>_color.png・_normal.png に保存。
    書き出し用の素材は UV "UV"（m）→ Mapping（1/周期。glTF の KHR_texture_transform）→ 画像 → Principled。
    画像の素材（CC0 の質感）は UV "UV" で貼ってあるので、そのまま glb に入る（焼かない）。"""
    import time
    import assets_blender
    sc = bpy.context.scene
    used = []
    for ob in objs:
        for m in ob.data.materials:
            if m and m not in used:
                used.append(m)
    todo = [m for m in used if m.use_nodes and assets_blender.needs_bake([n.bl_idname for n in m.node_tree.nodes])]
    swap, log = {}, []
    if not todo:
        return swap, log
    tex_dir.mkdir(parents=True, exist_ok=True)
    px = assets_blender.BAKE_PX_FAST if fast else assets_blender.BAKE_PX
    prev_engine = sc.render.engine
    sc.render.engine = "CYCLES"
    sc.cycles.samples = 4
    sc.cycles.device = "CPU"
    try:
        for m in todo:
            t0 = time.time()
            pw, ph = (list(m["bake_period_m"]) if "bake_period_m" in m else [assets_blender.BAKE_TARGET_M] * 2)
            bsdf = _principled(m)
            if bsdf is None:
                log.append({"material": m.name, "skipped": "Principled BSDF が無い"})
                continue
            safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in m.name)
            files = {}
            kinds = ["color"] + (["normal"] if bsdf.inputs["Normal"].is_linked else [])
            for kind in kinds:
                img = bpy.data.images.new(f"{safe}_{kind}", px, px, alpha=False)
                if kind == "normal":
                    img.colorspace_settings.name = "Non-Color"
                _bake_one(m, kind, img, pw, ph)
                path = tex_dir / f"{safe}_{kind}.png"
                img.filepath_raw = str(path)
                img.file_format = "PNG"
                img.save()
                files[kind] = img
            wm = m.copy()
            wm.name = f"{m.name}_web"
            nt = wm.node_tree
            nt.nodes.clear()
            outn = nt.nodes.new("ShaderNodeOutputMaterial")
            wb = nt.nodes.new("ShaderNodeBsdfPrincipled")
            for k in ("Roughness", "Metallic"):
                wb.inputs[k].default_value = bsdf.inputs[k].default_value
            uvn = nt.nodes.new("ShaderNodeUVMap")
            uvn.uv_map = assets_blender.UV_NAME
            mp = nt.nodes.new("ShaderNodeMapping")
            mp.inputs["Scale"].default_value = (1.0 / pw, 1.0 / ph, 1.0)
            nt.links.new(uvn.outputs["UV"], mp.inputs["Vector"])
            ci = nt.nodes.new("ShaderNodeTexImage")
            ci.image = files["color"]
            nt.links.new(mp.outputs["Vector"], ci.inputs["Vector"])
            nt.links.new(ci.outputs["Color"], wb.inputs["Base Color"])
            if "normal" in files:
                ni = nt.nodes.new("ShaderNodeTexImage")
                ni.image = files["normal"]
                nmap = nt.nodes.new("ShaderNodeNormalMap")
                nmap.uv_map = assets_blender.UV_NAME
                nt.links.new(mp.outputs["Vector"], ni.inputs["Vector"])
                nt.links.new(ni.outputs["Color"], nmap.inputs["Color"])
                nt.links.new(nmap.outputs["Normal"], wb.inputs["Normal"])
            nt.links.new(wb.outputs["BSDF"], outn.inputs["Surface"])
            swap[m] = wm
            log.append({"material": m.name, "period_m": [round(pw, 4), round(ph, 4)], "px": px,
                        "files": [f"tex/{safe}_{k}.png" for k in files],
                        "normal": "焼いた" if "normal" in files else "無し（描画用の素材に凹凸が無い）",
                        "seconds": round(time.time() - t0, 2)})
    finally:
        sc.render.engine = prev_engine
    return swap, log


def street_camera_web(eye: Vector, target: Vector, lens: float, sensor_w: float, res: tuple[int, int], ground_z: float) -> dict:
    """Blender の街路視点を、歩く画面の ?cam=x,y,z,yaw,pitch,fov（x=東, y=北, z=標高 m、yaw は北から時計回り°、
    pitch は上向き +°、fov は縦の画角°）に直す。Blender の z は ground_z からの高さ。"""
    d = target - eye
    yaw = math.degrees(math.atan2(d.x, d.y)) % 360
    pitch = math.degrees(math.atan2(d.z, math.hypot(d.x, d.y)))
    w, h = res
    half_long = sensor_w / 2                     # sensor_fit AUTO：長い辺に sensor_w
    half_h = half_long * h / w if w >= h else half_long
    vfov = math.degrees(2 * math.atan(half_h / lens))
    z = eye.z + (ground_z or 0.0)
    q = f"{eye.x:.2f},{eye.y:.2f},{z:.2f},{yaw:.2f},{pitch:.2f},{vfov:.2f}"
    return {"eye": [round(eye.x, 3), round(eye.y, 3), round(z, 3)], "yaw_deg": round(yaw, 2), "pitch_deg": round(pitch, 2),
            "vfov_deg": round(vfov, 2), "lens_mm": lens, "sensor_w_mm": sensor_w, "resolution": [w, h],
            "aspect": round(w / h, 4), "query": f"cam={q}"}


# --- 棟を組む -----------------------------------------------------------------------

TERRAIN_FAR_M = 200.0     # 鳥瞰・街路視点の背景用の粗い地形（5 m 格子）の広がり。描画だけで glb には入れない
TERRAIN_FAR_STEP_M = 10.0   # 5m 標高の画素より粗く（5 m 格子は画素の段がそのまま尖りになる）
TERRAIN_FAR_SMOOTH_PX = 15  # 遠景は 5m 標高 3 画素分の窓で均してから間引く
TERRAIN_FAR_DROP_M = 0.15  # 粗い地形は細かい地形の下に少し沈める（重なりのちらつき防止）
FOOTING_DEPTH_M = 1.0      # 建物を地形の下まで伸ばす深さ（地形は切り欠かない）


class Terrain:
    """模型の地面。国土地理院 5m 標高（out/site/dem5a.npz）から ground_z を引いた 1 枚の面。

    faces[].ground_offset は面の前の帯の地形を上書きする（site.apply_ground_overrides。一覧は report の ground_overrides）。
    検算（site.check_ground_offsets）は上書き前の 5 m 標高で。地表の種類は OSM のマスク（out/site/ground_mask.npz）で色分け。
    5m 標高か ground_z が無い時だけ z=0 の平面（仮の値として report に出す）。"""

    def __init__(self, b: dict, poly):
        self.grid = self.far = None
        self.checks, self.warnings, self.info = [], [], {}
        self.overrides = []
        self.mask = site.load_ground_mask(REPO / "out" / "site" / "ground_mask.npz")
        gz = val(b.get("ground_z"))
        npz = REPO / "out" / "site" / "dem5a.npz"
        if gz is None or not npz.exists():
            assume("地形", "z=0 の平面", "ground_z が unknown か 5m 標高（out/site/dem5a.npz）が無い。平らな地面と置いた")
            return
        dem = site.load_dem(npz)
        self.grid = site.terrain_grid(dem, poly, gz)
        try:
            self.far = site.terrain_grid(dem, poly, gz, margin_m=TERRAIN_FAR_M, step_m=TERRAIN_FAR_STEP_M,
                                     smooth_px=TERRAIN_FAR_SMOOTH_PX)
        except ValueError:
            self.far = None
        self.checks, self.warnings = site.check_ground_offsets(dem, b)
        GX, GY = np.meshgrid(self.grid["x"], self.grid["y"])
        self.grid["z"], self.overrides = site.apply_ground_overrides(GX, GY, self.grid["z"], site.ground_override_specs(b), z_ref=gz)
        for o in self.overrides:
            if not o["applied"]:
                self.warnings.append(f"{o['face']} の ground_offset で地形を上書きしなかった：{o['skipped']}")
        zs = self.grid["z"][self.grid["mask"]]
        self.info = {"source": "国土地理院 5m 標高（DEM5A）out/site/dem5a.npz − ground_z", "ground_z": gz,
                     "margin_m": site.TERRAIN_MARGIN_M, "step_m": 1.0, "smooth_px": site.TERRAIN_SMOOTH_PX,
                     "nan_ratio_filled": round(self.grid["nan_ratio"], 3),
                     "z_range": [round(float(zs.min()), 2), round(float(zs.max()), 2)]}

    def z(self, x: float, y: float) -> float:
        return site.grid_z(self.grid, x, y) if self.grid is not None else 0.0

    def min_near(self, poly, dist: float = 1.0) -> float:
        """多角形から dist m 以内の地形の一番低い所（地形が無ければ 0）。"""
        if self.grid is None:
            return 0.0
        GX, GY = np.meshgrid(self.grid["x"], self.grid["y"])
        m = site.polygon_distance(GX, GY, poly) <= dist
        return float(self.grid["z"][m].min()) if m.any() else 0.0

    def front(self, f: dict, a0: float, a1: float) -> float:
        """面 f の前（面から 0.5〜3 m）、面に沿って a0〜a1 の地形の高さの中央値。部品の足元に使う。"""
        if self.grid is None:
            return 0.0
        p0, u, n, L = face_frame(f)
        a0, a1 = max(0.0, min(a0, a1)), min(L, max(a0, a1))
        k = max(2, int((a1 - a0) / 0.5) + 1)
        zs = [self.z(*(p0 + u * (a0 + (a1 - a0) * i / (k - 1)) + n * d).xy) for i in range(k) for d in (0.5, 1.5, 3.0)]
        return float(np.median(zs))

    @staticmethod
    def _mesh(name, g, mask, drop, col, mat):
        gx, gy, Z = g["x"], g["y"], g["z"]
        nx = len(gx)
        verts = [(float(x), float(y), float(Z[j, i]) - drop) for j, y in enumerate(gy) for i, x in enumerate(gx)]
        faces = [(j * nx + i, j * nx + i + 1, (j + 1) * nx + i + 1, (j + 1) * nx + i)
                 for j in range(len(gy) - 1) for i in range(nx - 1)
                 if mask[j, i] and mask[j, i + 1] and mask[j + 1, i + 1] and mask[j + 1, i]]
        used = sorted({v for fc in faces for v in fc})
        remap = {v: k for k, v in enumerate(used)}
        me = bpy.data.meshes.new(name)
        me.from_pydata([verts[v] for v in used], [], [tuple(remap[v] for v in fc) for fc in faces])
        me.update()
        for p in me.polygons:
            p.use_smooth = True
        ob = new_object(name, me, col)
        ob.data.materials.append(mat)
        return ob

    def build(self, poly, mats, col) -> list:
        """地形の面（terrain：足元の外接長方形の外側 20 m、1 m 格子）と背景の粗い地形（terrain_far）を作る。"""
        if self.grid is None:
            xs, ys = [p[0] for p in poly], [p[1] for p in poly]
            cx, cy, R = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, 1500.0
            g = prism("terrain", [(cx - R, cy - R), (cx + R, cy - R), (cx + R, cy + R), (cx - R, cy + R)], -0.3, 0.0, col)
            g.data.materials.append(mats.ground)
            return [g]
        obs = [self._mesh("terrain", self.grid, self.grid["mask"], 0.0, col, mats.ground)]
        self.paint_counts = self.paint(obs[0], mats)
        if self.far is not None:
            # 細かい地形（長方形）の内側に全部入る四角は抜く。高さは同じ均した格子から取るので継ぎ目は合う
            gx, gy = self.grid["x"], self.grid["y"]
            FX, FY = np.meshgrid(self.far["x"], self.far["y"])
            inner = (FX >= gx[0]) & (FX <= gx[-1]) & (FY >= gy[0]) & (FY <= gy[-1])
            inside = inner[:-1, :-1] & inner[1:, :-1] & inner[:-1, 1:] & inner[1:, 1:]
            png = REPO / "out" / "site" / "ground_mask_color.png"
            far_mat = mats.ground_mask_image(self.mask, png) if self.mask is not None and png.exists() else mats.ground
            obs.append(self._mesh_far("terrain_far", self.far, inside, col, far_mat))
        return obs

    @staticmethod
    def _mesh_far(name, g, drop_quads, col, mat):
        gx, gy, Z = g["x"], g["y"], g["z"]
        nx = len(gx)
        verts = [(float(x), float(y), float(Z[j, i]) - TERRAIN_FAR_DROP_M) for j, y in enumerate(gy) for i, x in enumerate(gx)]
        faces = [(j * nx + i, j * nx + i + 1, (j + 1) * nx + i + 1, (j + 1) * nx + i)
                 for j in range(len(gy) - 1) for i in range(nx - 1) if not drop_quads[j, i]]
        me = bpy.data.meshes.new(name)
        me.from_pydata(verts, [], faces)
        me.update()
        ob = new_object(name, me, col)
        ob.data.materials.append(mat)
        return ob

    def paint(self, ob, mats) -> dict:
        """地形の四角ごとに、中心の地表の種類（OSM のマスク）で素材を分ける。マスクが無ければ 1 色のまま。返り値は種類ごとの四角の数。"""
        if self.mask is None or self.grid is None:
            return {}
        me = ob.data
        cs = np.array([p.center[:2] for p in me.polygons]) if len(me.polygons) else np.zeros((0, 2))
        if not len(cs):
            return {}
        ks = site.mask_kind_at(self.mask, cs[:, 0], cs[:, 1])
        slot, by_mat = {}, {}
        for kd in sorted(set(int(k) for k in ks)):
            name = site.GROUND_KINDS[kd]
            if name == "building":
                name = "unknown"                 # 建物の足元は根拠の無い所と同じ灰色（背景の地形の画像と揃える）
            m = mats.ground_kind(name)
            if m.name not in by_mat:
                me.materials.append(m)
                by_mat[m.name] = len(me.materials) - 1
            slot[kd] = by_mat[m.name]
        for p, k in zip(me.polygons, ks):
            p.material_index = slot[int(k)]
        return {site.GROUND_KINDS[int(k)]: int((ks == k).sum()) for k in set(ks.tolist())}

    def report(self) -> dict:
        return {"terrain": self.info, "ground_offset_checks": self.checks, "ground_overrides": self.overrides,
                "ground_mask": self.paint_counts if hasattr(self, "paint_counts") else None}


def face_part_span(pt: dict, f: dict) -> tuple[float, float]:
    """面に付く部品が面に沿って占める範囲（地面の高さを読む所）。"""
    a = val(pt.get("along"), 0.0) or 0.0
    if pt.get("type") == "ac_units":
        cnt = int(val(pt.get("count"), 1) or 1)
        w, gap = val(pt.get("w"), 0.9) or 0.9, val(pt.get("gap"), 0.15) or 0.15
        return a, a + cnt * (w + gap)
    w = val(pt.get("width")) or 1.0
    return a, a + w


def with_front_ground(f: dict, terrain: "Terrain", a0: float, a1: float) -> dict:
    """部品の足元の高さを地形から取るため、面の写しの ground_offset を地形の値にした物（棟 JSON は変えない）。"""
    if terrain.grid is None:
        return f
    return {**f, "ground_offset": round(terrain.front(f, a0, a1), 3)}


def tag(ob, evidence: str, face: str | None = None, part: str | None = None) -> None:
    """根拠の層の印（custom property。glb では node の extras）。evidence は schema.EVIDENCE_STATES のどれか。"""
    ob["evidence"] = evidence
    if face:
        ob["face"] = face
    if part:
        ob["part"] = part


FACE_POLY_DEPTH_M = 3.0      # 面の線からこの奥行きまでの壁（窓の彫り込みの内側を含む）をその面の物と見る


def face_polygons(body, faces: dict) -> dict:
    """本体の縦の面（|法線 z| < 0.7）を、一番近い面の線（from→to）に振り分ける。{面 id: [polygon の番号]}。
    線の外向きに 0.3 m、奥に FACE_POLY_DEPTH_M まで、線の長さの範囲（±0.3 m）にある物だけ。どの線にも入らない物は振り分けない。"""
    lines = {fid: face_frame(f) for fid, f in faces.items()}
    out: dict = {}
    for pg in body.data.polygons:
        if abs(pg.normal.z) >= 0.7:
            continue
        c = body.matrix_world @ pg.center
        best = None
        for fid, (p0, u, n, L) in lines.items():
            d = c - p0
            t, h = d.dot(u), d.dot(n)
            if -0.3 <= t <= L + 0.3 and -FACE_POLY_DEPTH_M <= h <= 0.3 and (best is None or abs(h) < best[0]):
                best = (abs(h), fid)
        if best:
            out.setdefault(best[1], []).append(pg.index)
    return out


def tag_face_materials(body, face_polys: dict, states: dict) -> tuple:
    """書き出しの間だけ、面ごとに素材を複製して印（face・evidence）を付け、その面の polygon に当てる。
    glTF では素材の extras に出る（three.js の material.userData）。戻すための (元の番号, 元の数, 複製) を返す。"""
    me = body.data
    orig = [pg.material_index for pg in me.polygons]
    n0 = len(me.materials)
    made: dict = {}
    for fid, idxs in face_polys.items():
        for pi in idxs:
            src_i = me.polygons[pi].material_index
            key = (fid, src_i)
            if key not in made:
                m = me.materials[src_i].copy()
                m.name = f"{me.materials[src_i].name}|{fid}"
                m["face"] = fid
                m["evidence"] = states.get(fid, "unknown")
                me.materials.append(m)
                made[key] = len(me.materials) - 1
            me.polygons[pi].material_index = made[key]
    return orig, n0, [me.materials[i] for i in made.values()]


def untag_face_materials(body, saved: tuple) -> None:
    orig, n0, copies = saved
    me = body.data
    for pg, i in zip(me.polygons, orig):
        pg.material_index = i
    while len(me.materials) > n0:
        me.materials.pop()
    for m in copies:
        bpy.data.materials.remove(m)


def _part_face(pt: dict) -> str | None:
    f = pt.get("face")
    return f if isinstance(f, str) else None


# --- 内装（interior.rooms。決定 D-0002） -----------------------------------------------------------
# 順序：①塊を足し算（union_bodies）→ ②窓（面ごとに結合）・部品のくり抜き（boolean_cut）→ ③部屋のくり抜き（床〜天井、
# 外壁の線から wall_t 内側に寄せた多角形）→ ④ガラスの壁・扉の開口（外壁を貫く箱）。③④は窓と別の回に 1 つずつ当て、
# 体積を check_cut で確かめる（窓の彫り込みと重なっても「彫る物より多くは減らない」は成り立つ）。
INTERIOR_EVIDENCE_PREFIX = "room_"


def _interior_mod():
    from recorder import interior
    return interior


def _int_mat(mats, name: str | None, kind: str):
    """内装の素材：asset:<id> は台帳の質感、名前は interior.MATERIALS の色。"""
    I = _interior_mod()
    name = name or I.MATERIAL_DEFAULT.get(kind, "plaster")
    if name.startswith("asset:"):
        return mats.named(name)
    key = f"int_{name}"
    m = bpy.data.materials.get(key)
    if m is None:
        m = material_plain(key, I.MATERIALS.get(name, I.MATERIALS["plaster"]), roughness=0.7)
    return m


def material_light(name: str = "int_light", strength: float = 6.0):
    """天井の照明（光る板）。glTF では emissive（強さ > 1 は KHR_materials_emissive_strength）で出る。"""
    m = bpy.data.materials.get(name)
    if m is None:
        m = material_plain(name, (1.0, 0.98, 0.92, 1.0), roughness=0.5)
        bsdf = m.node_tree.nodes["Principled BSDF"]
        for k, v in (("Emission Color", (1.0, 0.97, 0.9, 1.0)), ("Emission", (1.0, 0.97, 0.9, 1.0)), ("Emission Strength", strength)):
            if k in bsdf.inputs:
                bsdf.inputs[k].default_value = v
    return m


def _ob_center(ob) -> Vector:
    vs = ob.data.vertices
    return sum((ob.matrix_world @ v.co for v in vs), Vector()) / max(len(vs), 1)


def _room_tag(ob, room: dict, state: str, kind: str, evidence: list[str]) -> None:
    rid = room.get("id", "room")
    tag(ob, state, part=f"{INTERIOR_EVIDENCE_PREFIX}{rid}_{kind}")
    ob["room"] = rid
    ob["room_name"] = str(room.get("name", rid))
    if evidence:
        ob["part_evidence"] = ",".join(evidence)


def build_interior(b: dict, faces: dict, mats, col, extras: list) -> dict:
    """部屋ごとに、くり抜く箱（cav）・開口の箱（open）・中の物（objs）を作る。extras の中で部屋に埋もれる物
    （窓の奥のカーテン・ガラス張りの奥の暗い箱、ガラスの壁の区間の窓のガラス・サッシ）は消す。"""
    I = _interior_mod()
    cav_cutters, open_cutters, objs, cavities = [], [], [], []
    for room in I.rooms(b):
        rid = room.get("id", "room")
        pts = I.room_points(room)
        z0, z1 = I.room_z(room, b, val)
        if pts is None or z0 is None:
            print(f"[interior] {rid}: 輪郭か高さが unknown なので作らない（撮影依頼へ）")
            continue
        cav = I.cavity_polygon(room, b, val)
        wt = I.wall_thickness(room)                # 仮の値の記録は cavity_polygon で 1 回だけ
        st = I.room_state(room)
        evid = I.room_evidence(room)
        cavities.append((rid, cav, z0, z1))
        mset = room.get("materials") or {}
        # ③ 部屋のくり抜き（床と天井は板で覆うので、くり抜きは床 z〜天井 z ちょうど）
        cav_cutters.append(prism(f"cut_room_{rid}", cav, z0, z1, col))
        # 床・天井の板
        fl = prism(f"room_{rid}_floor", cav, z0, z0 + 0.03, col)
        fl.data.materials.append(_int_mat(mats, mset.get("floor"), "floor"))
        cl = prism(f"room_{rid}_ceiling", cav, z1 - 0.03, z1, col)
        cl.data.materials.append(_int_mat(mats, mset.get("ceiling"), "ceiling"))
        for ob, kind in ((fl, "floor"), (cl, "ceiling")):
            _room_tag(ob, room, st, kind, evid)
            objs.append(ob)
        # 柱
        for c in I.column_positions(room, b, val):
            ux = Vector((c["ux"][0], c["ux"][1], 0))
            uy = Vector((-ux.y, ux.x, 0))
            ob = box(f"room_{rid}_column_{c['i']}_{c['j']}", Vector((c["x"], c["y"], (z0 + z1) / 2)), ux, uy, UP, c["size"], c["size"], z1 - z0, col)
            ob.data.materials.append(_int_mat(mats, "white", "wall"))
            _room_tag(ob, room, st, "columns", evid)
            objs.append(ob)
        # 照明（見た目だけ）
        for k, (x, y) in enumerate(I.light_positions(room, b)):
            ob = box(f"room_{rid}_light_{k}", Vector((x, y, z1 - 0.045)), Vector((1, 0, 0)), Vector((0, 1, 0)), UP, 1.2, 0.3, 0.02, col)
            ob.data.materials.append(material_light())
            _room_tag(ob, room, st, "lights", [])
            objs.append(ob)
        # ④ ガラスの壁（外壁の該当区間を床〜天井で貫いて、ガラスの板と縦枠）
        doors = [(dr, I.door_span(dr, b)) for dr in room.get("doors") or []]
        for k, gw in enumerate(room.get("glass_walls") or []):
            seg = I.glass_segment(gw, b)
            if seg is None:
                continue
            f = faces[gw["face"]]
            p0, u, n, L = face_frame(f)
            a0, a1 = seg
            gst = "measured" if all((gw.get(x) or {}).get("status") == "measured" for x in ("from", "to")) else "estimated"
            open_cutters.append(box(f"cut_roomglass_{rid}_{k}", p0 + u * ((a0 + a1) / 2) - n * (wt / 2) + UP * ((z0 + z1) / 2),
                                    u, n, UP, a1 - a0, wt + 0.6, z1 - z0, col))
            # 既存の窓（ガラス・サッシ・カーテン・窓台・手すり）のうち、この区間・この高さの物は消す
            for e in list(extras):
                nm = e.name
                if not any(nm.startswith(p + f"_{f['id']}_") for p in ("glass", "sash", "curtain", "sill", "rail", "spandrel")):
                    continue
                c = _ob_center(e)
                t = (c - p0).dot(u)
                if a0 - 0.05 <= t <= a1 + 0.05 and z0 - 0.3 <= c.z <= z1 + 0.3:
                    extras.remove(e)
                    bpy.data.objects.remove(e, do_unlink=True)
            # ガラスの板（扉の所は抜く）
            gaps = sorted((sp[0], sp[1]) for dr, sp in doors if sp and dr.get("face") == gw["face"])
            spans, s = [], a0
            for g0, g1 in gaps:
                if g1 <= a0 or g0 >= a1:
                    continue
                if g0 > s:
                    spans.append((s, g0))
                s = max(s, g1)
            if s < a1:
                spans.append((s, a1))
            for j, (s0, s1) in enumerate(spans):
                if s1 - s0 < 0.05:
                    continue
                gb = box(f"room_{rid}_glass_{k}_{j}", p0 + u * ((s0 + s1) / 2) - n * (wt / 2) + UP * ((z0 + z1) / 2), u, n, UP, s1 - s0, 0.02, z1 - z0, col)
                gb.data.materials.append(mats.glass)
                _room_tag(gb, room, gst, "glass", evid)
                objs.append(gb)
            for j, a in enumerate(I.mullions(gw, b)):
                fr = box(f"room_{rid}_mullion_{k}_{j}", p0 + u * a - n * (wt / 2) + UP * ((z0 + z1) / 2), u, n, UP, 0.06, 0.12, z1 - z0, col)
                fr.data.materials.append(mats.metal)
                _room_tag(fr, room, gst, "glass", evid)
                objs.append(fr)
            for j, zz in enumerate((z0 + 0.04, z1 - 0.04)):
                fr = box(f"room_{rid}_transom_{k}_{j}", p0 + u * ((a0 + a1) / 2) - n * (wt / 2) + UP * zz, u, n, UP, a1 - a0, 0.12, 0.08, col)
                fr.data.materials.append(mats.metal)
                _room_tag(fr, room, gst, "glass", evid)
                objs.append(fr)
        # 扉（開口＋薄い板。自動の引き戸はガラス 2 枚、開き戸は 1 枚の板）
        for k, (dr, sp) in enumerate(doors):
            if sp is None:
                continue
            f = faces[dr["face"]]
            p0, u, n, L = face_frame(f)
            a0, a1, h = sp
            dst = "measured" if all((dr.get(x) or {}).get("status") == "measured" for x in ("at", "width", "height")) else "estimated"
            open_cutters.append(box(f"cut_roomdoor_{rid}_{k}", p0 + u * ((a0 + a1) / 2) - n * (wt / 2) + UP * (z0 + h / 2 + 0.01),
                                    u, n, UP, a1 - a0, wt + 0.6, h + 0.02, col))
            leaves = [(a0, a1)] if dr.get("kind") == "swing" else [(a0, (a0 + a1) / 2), ((a0 + a1) / 2, a1)]
            for j, (s0, s1) in enumerate(leaves):
                lf = box(f"room_{rid}_door_{k}_{j}", p0 + u * ((s0 + s1) / 2) - n * (wt / 2 + (0.03 if j else -0.03)) + UP * (z0 + h / 2),
                         u, n, UP, s1 - s0 - 0.02, 0.04, h, col)
                lf.data.materials.append(mats.glass if dr.get("kind") == "auto_sliding" else _int_mat(mats, "wood", "wall"))
                _room_tag(lf, room, dst, "door", evid)
                objs.append(lf)
            hd = box(f"room_{rid}_doorhead_{k}", p0 + u * ((a0 + a1) / 2) - n * (wt / 2) + UP * (z0 + h + 0.04), u, n, UP, a1 - a0, 0.14, 0.08, col)
            hd.data.materials.append(mats.metal)
            _room_tag(hd, room, dst, "door", evid)
            objs.append(hd)
        # 置物（type: asset は CC0 の模型、無い・読めない時は箱。type: box は箱）
        for k, pt in enumerate(room.get("parts") or []):
            w, d, h = I.part_size(pt)
            label = pt.get("name") or pt.get("asset") or "box"
            z_base = z0 + 0.03 + float(I.v(pt.get("z0"), 0.0))
            for j, (x, y, yaw) in enumerate(I.part_spots(pt)):
                made = []
                if pt.get("type") == "asset":
                    try:
                        import assets_blender
                        root, _w = assets_blender.import_model(pt["asset"], (x, y, z_base), yaw, [w, d, h], col,
                                                               name=f"room_{rid}_part_{k}_{j}")
                        made = [root] + list(root.children_recursive)
                    except Exception as e:  # noqa: BLE001  アセットが取れない時は箱で代える（大きさが無ければ 0.5 m 角と仮置き）
                        print(f"[interior] {rid} parts[{k}] {pt.get('asset')}: {e}。箱で代えた", file=sys.stderr)
                if not made:
                    bw = w or assume(f"interior.{rid}.parts[{k}].size.w", 0.5, "置物の大きさ未測。0.5 m と置いた")
                    bd = d or assume(f"interior.{rid}.parts[{k}].size.d", 0.5, "置物の大きさ未測。0.5 m と置いた")
                    bh = h or assume(f"interior.{rid}.parts[{k}].size.h", 0.75, "置物の高さ未測。0.75 m と置いた")
                    ux = Vector((math.cos(yaw), math.sin(yaw), 0))
                    ob = box(f"room_{rid}_part_{k}_{j}", Vector((x, y, z_base + bh / 2)), ux, Vector((-ux.y, ux.x, 0)), UP, bw, bd, bh, col)
                    ob.data.materials.append(_int_mat(mats, pt.get("material") or "wood", "wall"))
                    made = [ob]
                for ob in made:
                    _room_tag(ob, room, pt.get("status", "unknown"), "parts", [str(e) for e in pt.get("evidence") or []])
                    ob["part_label"] = str(label)
                objs += [o for o in made if o.type == "MESH"]
        # 部屋に埋もれる物（窓の奥のカーテン・ガラス張りの奥の暗い箱）を消す
        I_pip = I.point_in_polygon
        for e in list(extras):
            if not (e.name.startswith("curtain_") or e.name.startswith("glazing_room_")):
                continue
            c = _ob_center(e)
            if z0 - 0.05 <= c.z <= z1 + 0.05 and I_pip((c.x, c.y), cav):
                extras.remove(e)
                bpy.data.objects.remove(e, do_unlink=True)
    return {"cav_cutters": cav_cutters, "open_cutters": open_cutters, "objs": objs, "cavities": cavities}


def room_polygons(body, cavities) -> dict:
    """くり抜いた後の本体の面のうち、部屋の中に面する物 {polygon の番号: 部屋 id}（内壁の素材・photoproj の id 用）。"""
    I = _interior_mod()
    out = {}
    for pg in body.data.polygons:
        c = body.matrix_world @ pg.center
        for rid, cav, z0, z1 in cavities:
            if z0 - 0.02 <= c.z <= z1 + 0.02 and I.inside_or_on((c.x, c.y), cav, 0.02):
                # 部屋の中を向く面だけ（法線が部屋の内側か、床・天井）
                nrm = pg.normal
                probe = c + nrm * 0.05
                if abs(nrm.z) > 0.7 or I.point_in_polygon((probe.x, probe.y), cav):
                    out[pg.index] = rid
                    break
    return out


def build_building(b: dict, col) -> dict:
    poly = ccw([tuple(p) for p in b["footprint"]["polygon"]])
    ev = schema.evidence_summary(b)           # 根拠の層（面・部品の状態）
    face_ev = {fid: d["state"] for fid, d in ev["faces"].items()}
    H = val(b.get("parapet_top"))
    if H is None:
        cnt, pitch = val(b.get("floor_count")), val(b.get("floor_pitch"))
        H = assume("parapet_top", (cnt or 4) * (pitch or 3.6), "パラペット未確定。階数×階高（無ければ 4×3.6）")
    mats = Mats(b, gray_faces={fid for fid, st in face_ev.items() if st == "unknown"})
    faces = {f["id"]: f for f in b.get("faces", []) if f.get("from") and f.get("to")}
    terrain = Terrain(b, poly)

    blocks = b.get("blocks") or [{"id": "main", "polygon": poly, "top_z": H}]
    bodies = []
    tops = {}
    for blk in blocks:
        top = val(blk.get("top_z"), H, f"blocks[{blk['id']}].top_z", "高さ未確定。parapet_top と同じと置いた")
        tops[blk["id"]] = top
        bottom = val(blk.get("bottom_z"), 0.0)          # 上だけの塊（角の引っ込みなど）は bottom_z を持つ
        ob = prism(f"{b['id']}_{blk['id']}", blk["polygon"], bottom, top, col)
        ob.data.materials.append(mats.main)
        bodies.append(ob)
    body = union_bodies(bodies)
    # 地面に立つ塊は、地形を切り欠かずに建物の方を地形の下まで伸ばす（別の物 footing_*。立面図・屋根伏せでは隠す）
    footings = []
    for blk in blocks:
        if val(blk.get("bottom_z"), 0.0) > 0.0:
            continue
        zb = min(0.0, terrain.min_near(blk["polygon"])) - FOOTING_DEPTH_M
        ob = prism(f"footing_{blk['id']}", blk["polygon"], zb, 0.0, col)
        ob.data.materials.append(mats.main)
        footings.append(ob)          # 重なる塊は足し算で 1 つに（join だと自己交差して後の彫り込みで塔が消える）

    cutters, extras = [], []

    for fid, f in faces.items():
        n_extras = len(extras)
        p0, u, n, L = face_frame(f)
        fmat = mats.by_face.get(fid, mats.main)
        bays = f.get("bays") or {}
        count = val(bays.get("count"))
        pitch = val(bays.get("pitch"))
        pier = val(bays.get("pier_width"), 0.6, f"{fid} 柱型の幅", "未測。0.6 m と置いた") if count else None
        margin = 0.0          # from 側の余白（柱割りの始まり）。to 側は余り
        if count:
            mf_m = bays.get("margin_from" if "margin_from" in bays else "end_margin")
            mf, mt = val(mf_m), val(bays.get("margin_to"))
            if pitch:
                rest = L - count * pitch
                if mf is not None:
                    margin = mf
                elif mt is not None:
                    margin = rest - mt
                else:
                    margin = rest / 2        # unknown を 0 とは見なさない。半分ずつと仮置き
                    if abs(rest) > 0.005:
                        assume(f"{fid} 端の余白", round(margin, 3), "端の余白 unknown、仮に等分（余り %.2f m を両端に半分ずつ）" % rest)
            else:
                margin = mf or 0.0
                pitch = (L - (mf or 0.0) - (mt or 0.0)) / count
        face_top = val(f.get("top_z"), H)
        pier_depth = val(bays.get("pier_depth"), 0.0) if count else 0.0
        pier_z0 = max([val(op.get("z_top"), 0.0) for op in (f.get("openings") or [])] + [0.0])

        # 帯の引っ込み（depth が負）：壁を帯の範囲だけ彫り、奥に濃い面を置く
        band_recess = []   # (zb, zt, 引っ込み m)
        for bd in f.get("bands") or []:
            zb, zt, dep = val(bd.get("z_bottom")), val(bd.get("z_top")), val(bd.get("depth"), 0.0)
            if zb is None or zt is None or dep >= 0:
                continue
            a0, a1 = val(bd.get("along0"), 0.0), val(bd.get("along1"), L)
            r = -dep
            band_recess.append((zb, zt, r))
            cutters.append(box(f"cut_band_{fid}_{int(zb)}", p0 + u * ((a0 + a1) / 2) - n * (r / 2 - 0.25) + UP * ((zb + zt) / 2), u, n, UP, a1 - a0 + 0.02, r + 0.5, zt - zb, col))
            bb = box(f"band_{fid}_{int(zb)}", p0 + u * ((a0 + a1) / 2) - n * (r - 0.015) + UP * ((zb + zt) / 2), u, n, UP, a1 - a0, 0.03, zt - zb, col)
            bb.data.materials.append(material_plain(f"band_{fid}_{int(zb)}", (*hex_to_rgb(bd["color"]), 1.0), roughness=0.6) if bd.get("color") else mats.dark)
            extras.append(bb)

        def recess_at(z: float) -> float:
            return max([r for zb, zt, r in band_recess if zb - 0.05 <= z <= zt + 0.05] + [0.0])

        # 柱型が壁から出る（全高。ピロティの上から）
        if count and pier_depth > 0:
            for i in range(int(count) + 1):
                c = p0 + u * (margin + i * pitch) + n * (pier_depth / 2 - 0.01) + UP * ((pier_z0 + face_top) / 2)
                pc = box(f"pierx_{fid}_{i}", c, u, n, UP, pier, pier_depth + 0.01, face_top - pier_z0, col)
                pc.data.materials.append(fmat)
                extras.append(pc)
        # 水切り・庇の線（面に沿って出る横の段）
        for lg in f.get("ledges") or []:
            z = val(lg.get("z"))
            if z is None:
                continue
            dep, hgt = val(lg.get("depth"), 0.12), val(lg.get("height"), 0.15)
            a0, a1 = val(lg.get("along0"), 0.0), val(lg.get("along1"), L)
            lb = box(f"ledge_{fid}_{int(z * 10)}", p0 + u * ((a0 + a1) / 2) + n * (dep / 2 - 0.01) + UP * (z + hgt / 2), u, n, UP, a1 - a0, dep + 0.01, hgt, col)
            lb.data.materials.append(mats.named(lg.get("material", "tile")))
            extras.append(lb)

        for op in f.get("openings") or []:
            zt = val(op.get("z_top"))
            if zt is None:
                continue
            depth = val(op.get("depth"), 8.0, f"{fid} ピロティの奥行き", "未測。8 m と置いた")
            op_bays = op.get("bays") or list(range(1, int(count or 1) + 1))
            bw = pitch if count else L
            s0 = margin + (min(op_bays) - 1) * bw
            s1 = margin + max(op_bays) * bw
            c = p0 + u * ((s0 + s1) / 2) - n * (depth / 2) + UP * (zt / 2)
            cutters.append(box(f"cut_pilotis_{fid}_{int(s0)}", c, u, n, UP, s1 - s0 - 0.04, depth + 0.02, zt + 0.02, col))
            if count:
                for i in range(int(min(op_bays)) - 1, int(max(op_bays)) + 1):
                    for k, dd in enumerate((pier / 2, depth - pier / 2)):
                        c = p0 + u * (margin + i * bw) - n * dd + UP * (zt / 2)
                        pc = box(f"pier_{fid}_{i}_{k}", c, u, n, UP, pier, pier, zt, col)
                        pc.data.materials.append(fmat)
                        extras.append(pc)
            beam = box(f"beam_{fid}", p0 + u * ((s0 + s1) / 2) - n * (depth / 2) + UP * (zt - 0.25), u, n, UP, s1 - s0, depth, 0.5, col)
            beam.data.materials.append(mats.concrete)
            extras.append(beam)

        for gz in f.get("glazing") or []:
            zb, zt = val(gz.get("z_bottom"), 0.0), val(gz.get("z_top"))
            a0, a1 = val(gz.get("along0"), 0.0), val(gz.get("along1"), L)
            if zt is None:
                continue
            inset = val(gz.get("inset"), 0.3, f"{fid} ガラスの引っ込み", "未測。0.3 m と置いた")
            depth = val(gz.get("depth"), 6.0, f"{fid} ガラス部分の奥行き", "未測。6 m と置いた")
            # 彫り込みはガラスの面より 0.1 m 奥まで。ガラスは薄い板、その奥に室内の暗い箱（透けるガラスの向こうが壁にならないように）
            cutters.append(box(f"cut_glazing_{fid}_{int(a0)}", p0 + u * ((a0 + a1) / 2) - n * ((inset + 0.1) / 2 - 0.25) + UP * ((zb + zt) / 2 + 0.01), u, n, UP, a1 - a0 - 0.04, inset + 0.6, zt - zb + 0.02, col))
            gb = box(f"glazing_{fid}_{int(a0)}", p0 + u * ((a0 + a1) / 2) - n * inset + UP * ((zb + zt) / 2), u, n, UP, a1 - a0 - 0.05, 0.02, zt - zb - 0.05, col)
            gb.data.materials.append(mats.glass)
            extras.append(gb)
            ib = box(f"glazing_room_{fid}_{int(a0)}", p0 + u * ((a0 + a1) / 2) - n * (inset + 0.05 + (depth - 0.05) / 2) + UP * ((zb + zt) / 2), u, n, UP, a1 - a0 - 0.05, max(depth - 0.05, 0.05), zt - zb - 0.05, col)
            ib.data.materials.append(mats.interior)
            extras.append(ib)
            # ガラスの縦枠
            for k in range(1, int((a1 - a0) / 1.8)):
                fr = box(f"glazing_frame_{fid}_{int(a0)}_{k}", p0 + u * (a0 + k * 1.8) - n * (inset + 0.03) + UP * ((zb + zt) / 2), u, n, UP, 0.06, 0.06, zt - zb, col)
                fr.data.materials.append(mats.metal)
                extras.append(fr)

        if count and pitch:
            for w in f.get("windows") or []:
                zb, zt = val(w.get("z_bottom")), val(w.get("z_top"))
                if zb is None or zt is None:
                    continue
                ww = val(w.get("width"), pitch - pier, f"{fid} 窓幅", "柱間隔−柱型")
                rec = val(w.get("recess"), 0.35, f"{fid} 窓の奥行き", "未測。0.35 m と置いた")
                per = int(w.get("per_bay") or 1)
                sub = ww / per
                bays_list = w.get("bays") or list(range(1, int(count) + 1))
                base = recess_at((zb + zt) / 2)            # 帯の中なら帯の面から
                gap = val(w.get("gap"), 0.15) if per > 1 else 0.1
                zc = parts_geom.spandrel_cut_bottom(w.get("spandrel"), zb, val)   # 奥まりの中のパネルがあれば、その下端まで彫る
                for i in bays_list:
                    for k in range(per):
                        s = margin + (i - 1) * pitch + pitch / 2 + (k - (per - 1) / 2) * sub
                        wsub = sub - gap
                        total = base + rec
                        c = p0 + u * s - n * (total / 2 - 0.25) + UP * ((zc + zt) / 2)
                        cutters.append(box(f"cut_win|{fid}|{i}|{k}", c, u, n, UP, wsub, total + 0.5, zt - zc, col))
                        g = box(f"glass_{fid}_{i}_{k}", p0 + u * s - n * (total - 0.02) + UP * ((zb + zt) / 2), u, n, UP, wsub, 0.02, zt - zb, col)
                        g.data.materials.append(mats.glass)
                        extras.append(g)
                        # カーテン（ガラスの奥の明るい面。写真の窓はほぼ全部カーテンが見える）
                        # 彫り込みの奥の面とガラスの間に置く（透けるガラスの向こうに見える室内）
                        cur = box(f"curtain_{fid}_{i}_{k}", p0 + u * s - n * (total - 0.004) + UP * ((zb + zt) / 2), u, n, UP, wsub, 0.004, zt - zb, col)
                        cur.data.materials.append(mats.curtain)
                        extras.append(cur)
                        # サッシ：外枠（上下左右）と中央の縦桟
                        fr_d = total - 0.05
                        hh = zt - zb
                        frames = [(0.0, hh / 2 - 0.03, wsub, 0.06), (0.0, -(hh / 2 - 0.03), wsub, 0.06),
                                  (wsub / 2 - 0.03, 0.0, 0.06, hh), (-(wsub / 2 - 0.03), 0.0, 0.06, hh), (0.0, 0.0, 0.05, hh)]
                        for j, (dx, dz, fw, fh) in enumerate(frames):
                            fr = box(f"sash_{fid}_{i}_{k}_{j}", p0 + u * (s + dx) - n * fr_d + UP * ((zb + zt) / 2 + dz), u, n, UP, fw, 0.05, fh, col)
                            fr.data.materials.append(mats.metal)
                            extras.append(fr)
                        # 手すり（腰の高さの細い横棒）
                        if w.get("rail", True) and hh > 1.4:
                            rl = box(f"rail_{fid}_{i}_{k}", p0 + u * s - n * (base + 0.08) + UP * (zb + 1.1), u, n, UP, wsub, 0.03, 0.03, col)
                            rl.data.materials.append(mats.metal)
                            extras.append(rl)
                        sill = box(f"sill_{fid}_{i}_{k}", p0 + u * s - n * (base + rec / 2) + UP * (zc - 0.04), u, n, UP, wsub, rec, 0.08, col)
                        sill.data.materials.append(mats.concrete)
                        extras.append(sill)
                        # 窓の下の濃いパネル（windows[].spandrel）
                        if w.get("spandrel"):
                            sp = parts_geom.spandrel(w["spandrel"], s, wsub, zb, base, val, f"{fid} 窓の下のパネル", rec)
                            if sp:
                                extras += emit_prims(f"spandrel_{fid}_{i}_{k}", [sp], (p0, u, n), col, mats, fmat)

        for bd in f.get("bands") or []:
            zb, zt = val(bd.get("z_bottom")), val(bd.get("z_top"))
            if zb is None or zt is None or val(bd.get("depth"), 0.0) < 0:
                continue
            dep = val(bd.get("depth"), 0.0)
            a0, a1 = val(bd.get("along0"), 0.0), val(bd.get("along1"), L)
            c = p0 + u * ((a0 + a1) / 2) + n * (dep / 2 + 0.01) + UP * ((zb + zt) / 2)
            bb = box(f"band_{fid}_{int(zb)}", c, u, n, UP, a1 - a0, max(dep, 0.03), zt - zb, col)
            bb.data.materials.append(material_plain(f"band_{fid}_{int(zb)}", (*hex_to_rgb(bd["color"]), 1.0), roughness=0.6) if bd.get("color") else mats.dark)
            extras.append(bb)
            # 帯（腰壁）の上の手すり（bands[].rail）
            rl = bd.get("rail")
            if rl:
                rh = val(rl.get("height"), 0.9, f"{fid} 帯の上の手すりの高さ", "未測。0.9 m と置いた")
                extras += emit_prims(f"bandrail_{fid}_{int(zb)}", parts_geom.rail_prims("rail", a0, a1, zt, rh, val(rl.get("post_pitch"), 1.5), max(dep, 0.03) / 2),
                                     (p0, u, n), col, mats)
        for e in extras[n_extras:]:
            tag(e, face_ev.get(fid, "unknown"), face=fid)

    part_marks = []           # (部品名, 状態, 面, 増えた物の始まり)
    for i, pt in enumerate(b.get("parts") or []):
        part_marks.append((schema.part_name(pt, i), ev["parts"].get(schema.part_name(pt, i), "unknown"), _part_face(pt), len(extras)))
        t = pt.get("type")
        name = schema.part_name(pt, i)
        if t == "box":
            z0, z1 = val(pt.get("z0"), 0.0), val(pt.get("z1"))
            if z1 is None:
                continue
            ob = prism(name, pt["polygon"], z0, z1, col)
            ob.data.materials.append(mats.named(pt.get("material")))
            extras.append(ob)
        elif t == "balcony":
            f = faces.get(pt.get("face"))
            if not f:
                continue
            p0, u, n, L = face_frame(f)
            a0, a1 = val(pt.get("along0"), 0.0), val(pt.get("along1"), L)
            z = val(pt.get("z"))
            if z is None:
                continue
            depth = val(pt.get("depth"), 2.0, f"{name} の張り出し", "未測。2 m と置いた")
            slab = val(pt.get("slab"), 0.3)
            ph = val(pt.get("parapet_h"), 1.1, f"{name} の手すり高さ", "建築基準法の最低 1.1 m")
            pth = val(pt.get("parapet_t"), 0.15)
            mat = mats.named(pt.get("material"))
            sl = box(f"{name}_slab", p0 + u * ((a0 + a1) / 2) + n * (depth / 2) + UP * (z - slab / 2), u, n, UP, a1 - a0, depth, slab, col)
            sl.data.materials.append(mat)
            extras.append(sl)
            pw = box(f"{name}_par", p0 + u * ((a0 + a1) / 2) + n * (depth - pth / 2) + UP * (z + ph / 2), u, n, UP, a1 - a0, pth, ph, col)
            pw.data.materials.append(mat)
            extras.append(pw)
            if not pt.get("open_ends"):
                for a in (a0, a1):
                    pe = box(f"{name}_end{int(a)}", p0 + u * a + n * (depth / 2) + UP * (z + ph / 2), u, n, UP, pth, depth, ph, col)
                    pe.data.materials.append(mat)
                    extras.append(pe)
        elif t == "coping":
            h = val(pt.get("height"), 0.3)
            o = val(pt.get("overhang"), 0.08)
            for blk in blocks:
                pts = ccw([tuple(p) for p in blk["polygon"]])
                top = tops[blk["id"]]
                for j in range(len(pts)):
                    q0, q1 = Vector((*pts[j], 0)), Vector((*pts[(j + 1) % len(pts)], 0))
                    d = q1 - q0
                    Lj = d.length
                    if Lj < 0.5:
                        continue
                    uj = d / Lj
                    nj = Vector((uj.y, -uj.x, 0))
                    cb = box(f"coping_{blk['id']}_{j}", (q0 + q1) / 2 + nj * (o / 2 - 0.15) + UP * (top + h / 2 - 0.02), uj, nj, UP, Lj + 2 * o, 0.3 + o, h, col)
                    cb.data.materials.append(mats.coping)
                    extras.append(cb)
        elif t == "ac_units":
            f = faces.get(pt.get("face"))
            if not f:
                continue
            p0, u, n, L = face_frame(f)
            a = val(pt.get("along"), 0.0)
            cnt = int(val(pt.get("count"), 1))
            w, h, d, gap = val(pt.get("w"), 0.9), val(pt.get("h"), 0.8), val(pt.get("d"), 0.35), val(pt.get("gap"), 0.15)
            zb = parts_geom.ac_units_base(pt, with_front_ground(f, terrain, *face_part_span(pt, f)), val)   # z0（壁付け）か、前の地形＋0.1
            for k in range(cnt):
                ob = box(f"{name}_{k}", p0 + u * (a + k * (w + gap) + w / 2) + n * (d / 2 + 0.3) + UP * (zb + h / 2), u, n, UP, w, d, h, col)
                ob.data.materials.append(mats.metal)
                extras.append(ob)
        elif t == "pipes":
            f = faces.get(pt.get("face"))
            if not f:
                continue
            p0, u, n, L = face_frame(f)
            a = val(pt.get("along"), 0.0)
            cnt = int(val(pt.get("count"), 1))
            r = val(pt.get("radius"), 0.06)
            z0, z1 = val(pt.get("z0"), 0.0), val(pt.get("z1"), H)
            for k in range(cnt):
                ob = cylinder(f"{name}_{k}", p0 + u * (a + k * 0.3) + n * (r + 0.05), r, z0, z1, col, seg=8)
                ob.data.materials.append(mats.metal)
                extras.append(ob)
        elif t == "tree":
            x, y = pt["x"], pt["y"]
            hgt = val(pt.get("height"), 7.0)
            rad = val(pt.get("radius"), 3.0)
            tz = terrain.z(x, y)          # 木の根元は地形の上
            tr = cylinder(f"{name}_trunk", Vector((x, y, tz)), 0.18, tz, tz + hgt - rad * 0.6, col, seg=8)
            tr.data.materials.append(mats.trunk)
            cn = sphere(f"{name}_crown", Vector((x, y, tz + hgt - rad * 0.5)), rad, col)
            cn.data.materials.append(mats.leaf)
            extras += [tr, cn]
        elif t == "pavement":
            z = val(pt.get("z"), 0.0)
            ob = prism(name, pt["polygon"], z - 0.1, z + 0.02, col)
            ob.data.materials.append(mats.pavement if pt.get("material", "brick") == "brick" else mats.ground)
            extras.append(ob)
        elif t in parts_geom.ALL_PART_TYPES:
            pf = faces
            if t in ("steps", "porch", "stair_switchback", "planter") and pt.get("face") in faces:
                f = faces[pt["face"]]          # 足元の高さは面の前の地形から（ground_offset は検算だけ）
                pf = {**faces, pt["face"]: with_front_ground(f, terrain, *face_part_span(pt, f))}
            elif t == "planter" and not pt.get("face") and "z0" not in pt and terrain.grid is not None:
                pts = pt.get("polygon") or [[pt["x"], pt["y"]]]
                zg = min(terrain.z(float(q[0]), float(q[1])) for q in pts)       # 単独の植え込みの足元は地形から
                pt = {**pt, "z0": {"value": round(zg, 3), "status": "estimated", "method": "地形（5m 標高）"}}
            got = parts_geom.part_prims(pt, pf, val, f"parts[{i}:{t}]")
            if not got:
                continue
            fr, cut, prims = got
            if fr == "world":
                frame, fm = (Vector((0, 0, 0)), Vector((1, 0, 0)), Vector((0, 1, 0))), None
            elif isinstance(fr, dict):                    # 渡り廊下などの局所の線
                fp0, fu, fn, _L = face_frame(fr)
                frame, fm = (fp0, fu, fn), None
            else:
                fp0, fu, fn, _L = face_frame(faces[fr])
                frame, fm = (fp0, fu, fn), mats.by_face.get(fr, mats.main)
            for k, c in enumerate(parts_geom.cut_list(cut)):
                # くり抜き（玄関の軒下・組み立て部品の cut: true）。窓とは別に 1 回ずつ当てる（boolean_cut が体積を検査する）
                cutters.append(prim_object(f"cut_{t}_{i}" + (f"_{k}" if k else ""), c, frame, col))
            extras += emit_prims(name, prims, frame, col, mats, fm)

    for k, (pname, pst, pface, start) in enumerate(part_marks):       # 部品ごとに増えた物へ印
        end = part_marks[k + 1][3] if k + 1 < len(part_marks) else len(extras)
        pt = (b.get("parts") or [])[k]
        for e in extras[start:end]:
            tag(e, pst, face=pface, part=pname)
            if pt.get("type") == "composite":
                # 組み立て部品は glb の extras に名前と根拠の写真も出す（歩く画面の「根拠で見る」）
                e["part_label"] = str(pt.get("name", ""))
                if pt.get("evidence"):
                    e["part_evidence"] = ",".join(map(str, pt["evidence"]))

    inter = build_interior(b, faces, mats, col, extras) if (b.get("interior") or {}).get("rooms") else None
    if cutters:
        boolean_cut(body, cutters)
    if inter and inter["cav_cutters"]:
        # 内装：窓・部品の彫り込みの後に、部屋のくり抜き → ガラスの壁・扉の開口（1 つずつ、体積を検査）
        boolean_cut(body, inter["cav_cutters"] + inter["open_cutters"])
    # 上向きの面（屋上・庇の上）は防水のコンクリート色
    body.data.materials.append(mats.roof)
    for pg in body.data.polygons:
        if pg.normal.z > 0.9:
            pg.material_index = 1
    room_polys = room_polygons(body, inter["cavities"]) if inter else {}
    if room_polys:                     # 部屋の内壁（くり抜いた面）は内装の壁の素材
        for rid in set(room_polys.values()):
            room = next(r for r in b["interior"]["rooms"] if r.get("id") == rid)
            body.data.materials.append(_int_mat(mats, (room.get("materials") or {}).get("wall"), "wall"))
            wi = len(body.data.materials) - 1
            for pi, r2 in room_polys.items():
                if r2 == rid:
                    body.data.polygons[pi].material_index = wi
    # 根拠の層：本体（塊）の印と、壁を面ごとに振り分けた表。unknown の面の壁は灰色（タイルの無地の壁だと「窓が無い」と決めつけて見える）
    tag(body, ev["body"], part="body")
    face_polys = face_polygons(body, faces)
    if room_polys:                     # 部屋の内壁は外観の面に数えない（灰色・縞・面の印を付けない）
        face_polys = {fid: [pi for pi in idxs if pi not in room_polys] for fid, idxs in face_polys.items()}
    gray =[fid for fid in face_polys if face_ev.get(fid) == "unknown"]
    if gray:
        body.data.materials.append(mats.unknown)
        ui = len(body.data.materials) - 1
        for fid in gray:
            for pi in face_polys[fid]:
                body.data.polygons[pi].material_index = ui
    # 仮説の面の壁は黄の縞（描画だけ。glb の前に unstripe_hypotheses で元に戻し、glb は素材の extras の evidence で区別する）
    hyp_saved = []
    hyp = [fid for fid in face_polys if face_ev.get(fid) == "hypothesis"]
    if hyp:
        base = next((m.get("color") for f in b.get("faces", []) if f["id"] in hyp for m in (f.get("materials") or [])[:1] if m.get("color")), None)
        if base is None:
            base = next((m.get("color") for f in b.get("faces", []) for m in (f.get("materials") or [])[:1] if m.get("color")), None)
        rgba = (*hex_to_rgb(base), 1.0) if base else UNKNOWN_COLOR
        body.data.materials.append(material_hypothesis("hypothesis_face", rgba))
        hi = len(body.data.materials) - 1
        attr = body.data.attributes.new(HYPOTHESIS_ATTR, "FLOAT", "FACE")
        for fid in hyp:
            for pi in face_polys[fid]:
                hyp_saved.append((pi, body.data.polygons[pi].material_index))
                body.data.polygons[pi].material_index = hi
                attr.data[pi].value = 1.0
    for e in extras:
        e.parent = body
    for e in (inter or {}).get("objs", []):
        if e.parent is None:
            e.parent = body
        elif e.parent.parent is None and e.parent.type == "EMPTY":     # アセットの根（空オブジェクト）
            e.parent.parent = body
    try:                                   # CC0 アセットの部品（parts[type=asset]。位置・個数・大きさは JSON、見た目はアセット）
        import assets_blender
        asset_report = assets_blender.build_asset_parts(b, col)
        for r in asset_report:
            for w in r.get("warnings", []):
                print(f"[warn] asset parts[{r['index']}:{r['asset']}] {w}", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] asset parts: {e}", file=sys.stderr)

    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    grounds = terrain.build(poly, mats, col)
    for ob in footings:
        ob.parent = body
    add_meter_uvs(col)
    return {"center": Vector((cx, cy, H / 2)), "height": H, "extent": max(max(xs) - min(xs), max(ys) - min(ys)), "body": body,
            "ground": grounds[0], "grounds": grounds, "footings": footings, "terrain": terrain,
            "face_polys": face_polys, "face_ev": face_ev, "evidence": ev, "hyp_saved": hyp_saved,
            "room_polys": room_polys, "rooms": (inter or {}).get("cavities", [])}


def unstripe_hypotheses(body, saved: list) -> None:
    """仮説の縞を外して元の素材に戻す（glb の前。縞の素材は最後に足したので末尾から外す）。"""
    if not saved:
        return
    for pi, mi in saved:
        body.data.polygons[pi].material_index = mi
    if HYPOTHESIS_ATTR in body.data.attributes:
        body.data.attributes.remove(body.data.attributes[HYPOTHESIS_ATTR])
    m = body.data.materials[len(body.data.materials) - 1]
    if m and m.name.startswith("hypothesis_face"):
        body.data.materials.pop()


# --- 描画 -------------------------------------------------------------------------------

def setup_scene(sketch: bool, fast: bool):
    sc = bpy.context.scene
    engines = [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
    sc.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else ("BLENDER_EEVEE" if "BLENDER_EEVEE" in engines else "CYCLES")
    sc.render.resolution_x, sc.render.resolution_y = (1400, 1000) if fast else (2400, 1700)
    sc.render.film_transparent = False
    vts = [i.identifier for i in bpy.types.ColorManagedViewSettings.bl_rna.properties["view_transform"].enum_items]
    sc.view_settings.view_transform = "Khronos PBR Neutral" if "Khronos PBR Neutral" in vts else ("AgX" if "AgX" in vts else "Standard")
    for attr, v in (("use_shadows", True), ("use_raytracing", True), ("taa_render_samples", 16 if fast else 48), ("shadow_ray_count", 2)):
        try:
            setattr(sc.eevee, attr, v)
        except (AttributeError, TypeError):
            pass
    world = bpy.data.worlds.new("w") if sc.world is None else sc.world
    sc.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    if sketch:
        bg.inputs["Color"].default_value = (1, 1, 1, 1)
        bg.inputs["Strength"].default_value = 1.0
    # 太陽と空は data/daylight.json（歩く画面と同じ物）。位置は日付・時刻・緯度経度から recorder/daylight.py の式で
    DL = daylight.load()
    bl = DL.get("blender", {})
    alt, az = daylight.sun_of(DL)
    if not sketch:
        sky = nt.nodes.new("ShaderNodeTexSky")
        kinds = [e.identifier for e in sky.bl_rna.properties["sky_type"].enum_items]
        sky.sky_type = "MULTIPLE_SCATTERING" if "MULTIPLE_SCATTERING" in kinds else ("NISHITA" if "NISHITA" in kinds else kinds[0])
        sky.sun_elevation = math.radians(alt)
        sky.sun_rotation = daylight.blender_sky_rotation(az)
        sky.sun_intensity = float(bl.get("sky_sun_intensity", 0.25))
        sky.altitude = float(bl.get("sky_altitude_m", 50))
        nt.links.new(sky.outputs["Color"], bg.inputs["Color"])
        bg.inputs["Strength"].default_value = float(bl.get("sky_strength", 0.035))   # 物理の空は明るいので弱く（青かぶりを防ぐ）
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    for o in [o for o in sc.objects if o.type == "LIGHT"]:
        bpy.data.objects.remove(o, do_unlink=True)
    ld = bpy.data.lights.new("sun", "SUN")
    ld.energy = float(bl.get("sun_energy_sketch", 1.8) if sketch else bl.get("sun_energy", 2.2))
    ld.angle = math.radians(float(bl.get("sun_angle_deg", 2.5)))
    ld.color = tuple(bl.get("sun_color", (1.0, 0.97, 0.9)))
    sun = bpy.data.objects.new("sun", ld)
    sc.collection.objects.link(sun)
    sun.rotation_euler = daylight.blender_sun_euler(alt, az)
    sc["daylight"] = json.dumps({"date": DL["date"], "time": DL["time"], "sun_alt_deg": round(alt, 2), "sun_az_deg": round(az, 2)})
    sc.render.use_freestyle = sketch
    sc.render.line_thickness = 2.0
    vl = bpy.context.view_layer
    vl.use_freestyle = sketch
    if sketch:
        fs = vl.freestyle_settings
        while fs.linesets:
            fs.linesets.remove(fs.linesets[0])
        ls = fs.linesets.new("lines")
        ls.select_silhouette = True
        ls.select_border = True
        ls.select_crease = True
        ls.linestyle.color = (0.08, 0.08, 0.08)
        ls.linestyle.thickness = 2.0
        gm = ls.linestyle.geometry_modifiers.new("wobble", "PERLIN_NOISE_1D")
        gm.amplitude, gm.frequency, gm.octaves = 1.2, 12.0, 2
        fs.crease_angle = math.radians(120)
        vl.material_override = material_hypothesis("sketch_white", (0.96, 0.96, 0.95, 1.0), by_attr=True)   # 仮説の面だけ黄の縞
    else:
        vl.material_override = None


def camera(name: str, loc: Vector, target: Vector, ortho_scale: float | None = None, lens: float = 32):
    sc = bpy.context.scene
    cd = bpy.data.cameras.new(name)
    cam = bpy.data.objects.new(name, cd)
    sc.collection.objects.link(cam)
    cam.location = loc
    cam.rotation_euler = (target - loc).to_track_quat("-Z", "Y").to_euler()
    if ortho_scale:
        cd.type = "ORTHO"
        cd.ortho_scale = ortho_scale
    else:
        cd.lens = lens
    cd.clip_end = 4000
    sc.camera = cam
    return cam


def render(path: Path):
    sc = bpy.context.scene
    sc.render.filepath = str(path)
    sc.render.image_settings.file_format = "PNG"
    bpy.ops.render.render(write_still=True)


# --- 写真の投影（photoproj）：写真の視点から世界座標と面・部品の id を描く -------------------------
# 模型そのもの・glb・通常の描画は変えない。--photoproj=<spec.json> の時だけ、組み立てた後にここへ来て、他の描画をせずに終わる。
# 写真を貼る計算（投影・見えるかどうか・食い違い）は外側の recorder/photoproj.py が numpy でする。ここは 1 視点ごとに
#   P  : 画素に写る模型の点の世界座標 (x, y, z) m（emission で出して EXR 32bit で読む。何も無い所は nan）
#   ID : 画素に写る面・部品の番号（pp_ids.json の表）
# を出すだけ（遅延シェーディングの G バッファと同じ考え）。

PP_RECESS_M = 0.8           # 面の線からこの奥行きまでの上向き・下向きの小面（窓の彫り込みの上下）は面そのものに数える


def _pp_nearest_face(c, faces: dict) -> tuple[str | None, float]:
    """一番近い面の線と、線からの外向きの距離 h（負＝奥）。"""
    best = None
    for fid, f in faces.items():
        p0, u, n, L = face_frame(f)
        d = Vector((c.x, c.y, 0)) - p0
        t = max(0.0, min(L, d.dot(u)))
        dist = (d - u * t).length
        if best is None or dist < best[0]:
            best = (dist, fid, d.dot(n))
    return (best[1], best[2]) if best else (None, 0.0)


def _pp_label(ob) -> str:
    part = ob.get("part")
    if part and part != "body":
        return f"part:{part}"
    if ob.get("face"):
        return f"face:{ob['face']}"
    nm = ob.name.split(".")[0]
    if nm.startswith("terrain"):
        return "ground"
    if nm.startswith("footing_"):
        return "body:footing"
    if nm.startswith("tree_"):
        return "tree"
    return "other:" + nm.rstrip("0123456789_")


def photoproj_ids(info: dict, b: dict) -> dict:
    """全部の物の polygon に整数の属性 pp_id を付ける。本体の壁は面ごと（face_polygons）、本体のそれ以外は一番近い面＋向き。"""
    faces = {f["id"]: f for f in b.get("faces", []) if f.get("from") and f.get("to")}
    labels: dict = {}

    def lid(label: str) -> int:
        if label not in labels:
            labels[label] = len(labels) + 1
        return labels[label]

    body = info["body"]
    by_poly = {}
    for fid, idxs in info["face_polys"].items():
        for pi in idxs:
            by_poly[pi] = f"face:{fid}"
    for pi, rid in (info.get("room_polys") or {}).items():        # 部屋の内壁・床・天井（くり抜いた面）
        by_poly[pi] = f"part:{INTERIOR_EVIDENCE_PREFIX}{rid}_wall"
    seen = set()
    for ob in bpy.context.scene.objects:
        if ob.type != "MESH" or ob.data is None or ob.data.name in seen:
            continue
        me = ob.data
        seen.add(me.name)
        if "pp_id" in me.attributes:
            me.attributes.remove(me.attributes["pp_id"])
        at = me.attributes.new("pp_id", "INT", "FACE")
        if ob == body:
            vals = []
            for pg in me.polygons:
                lab = by_poly.get(pg.index)
                if lab is None:
                    c = ob.matrix_world @ pg.center
                    nz = pg.normal.z
                    fid, h = _pp_nearest_face(c, faces)
                    kind = "上向き" if nz > 0.7 else ("下向き" if nz < -0.7 else "奥の壁")
                    if fid and kind != "奥の壁" and -PP_RECESS_M <= h <= 0.3:
                        lab = f"face:{fid}"            # 窓の彫り込みの上下・帯の上面は面の一部
                    else:
                        lab = f"face:{fid}:{kind}" if fid else f"body:{kind}"
                vals.append(lid(lab))
        else:
            vals = [lid(_pp_label(ob))] * len(me.polygons)
        if vals:
            at.data.foreach_set("value", vals)
    return {v: k for k, v in labels.items()}


def _pp_material(kind: str, origin):
    m = bpy.data.materials.new(f"pp_{kind}")
    try:
        m.use_nodes = True
    except (AttributeError, TypeError):
        pass
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = 1.0
    if kind == "pos":
        g = nt.nodes.new("ShaderNodeNewGeometry")
        sub = nt.nodes.new("ShaderNodeVectorMath")
        sub.operation = "SUBTRACT"
        sub.inputs[1].default_value = origin            # 負の値の光は避ける（全部正になるように原点をずらす）
        nt.links.new(g.outputs["Position"], sub.inputs[0])
        nt.links.new(sub.outputs["Vector"], em.inputs["Color"])
    else:
        at = nt.nodes.new("ShaderNodeAttribute")
        at.attribute_type = "GEOMETRY"
        at.attribute_name = "pp_id"
        nt.links.new(at.outputs["Fac"], em.inputs["Color"])
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    return m


def _pp_render_exr(path: Path):
    sc = bpy.context.scene
    sc.render.filepath = str(path)
    ims = sc.render.image_settings
    ims.file_format = "OPEN_EXR"
    ims.color_depth = "32"
    ims.color_mode = "RGBA"
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(str(path))
    try:
        img.colorspace_settings.name = "Non-Color"
    except TypeError:
        pass
    w, h = img.size
    a = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(a)
    bpy.data.images.remove(img)
    path.unlink(missing_ok=True)
    return a.reshape(h, w, 4)[::-1]


def photoproj_render(info: dict, b: dict, spec_path: Path, out: Path) -> None:
    """spec の視点ごとに P（世界座標）と ID を描いて <name>.npz に。表は pp_ids.json。"""
    from mathutils import Matrix

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    ids = photoproj_ids(info, b)
    (out / "pp_ids.json").write_text(json.dumps({str(k): v for k, v in ids.items()}, ensure_ascii=False, indent=1), encoding="utf-8")
    sc = bpy.context.scene
    lo = [1e9, 1e9, 1e9]
    for ob in sc.objects:
        if ob.type == "MESH":
            for c in ob.bound_box:
                w = ob.matrix_world @ Vector(c)
                lo = [min(lo[0], w.x), min(lo[1], w.y), min(lo[2], w.z)]
    origin = Vector(lo) - Vector((1.0, 1.0, 1.0))
    sc.render.engine = "CYCLES"
    cy = sc.cycles
    for attr, v in (("samples", 1), ("use_denoising", False), ("pixel_filter_type", "BOX"), ("filter_width", 0.01),
                    ("max_bounces", 0), ("use_adaptive_sampling", False), ("sample_clamp_direct", 0.0), ("sample_clamp_indirect", 0.0)):
        try:
            setattr(cy, attr, v)
        except (AttributeError, TypeError):
            pass
    sc.render.film_transparent = True
    sc.render.use_freestyle = False
    sc.view_settings.view_transform = "Standard"
    sc.render.resolution_percentage = 100
    sc.world = bpy.data.worlds.new("pp_w")
    vl = bpy.context.view_layer
    m_pos, m_id = _pp_material("pos", origin), _pp_material("id", origin)
    for v in spec["views"]:
        w, h = v["resolution"]
        sc.render.resolution_x, sc.render.resolution_y = int(w), int(h)
        cd = bpy.data.cameras.new(f"pp_{v['name']}")
        cd.lens = v["lens_mm"]
        cd.sensor_width = v["sensor_width_mm"]
        cd.sensor_fit = "AUTO"
        cd.clip_start, cd.clip_end = 0.1, 4000
        cam = bpy.data.objects.new(f"pp_{v['name']}", cd)
        sc.collection.objects.link(cam)
        cam.matrix_world = Matrix(v["matrix_world"])
        sc.camera = cam
        vl.material_override = m_pos
        pa = _pp_render_exr(out / f"_pp_{v['name']}_pos.exr")
        vl.material_override = m_id
        ia = _pp_render_exr(out / f"_pp_{v['name']}_id.exr")
        hit = pa[..., 3] > 0.5
        P = pa[..., :3] + np.array(origin, np.float32)
        P[~hit] = np.nan
        ID = np.where(ia[..., 3] > 0.5, np.rint(ia[..., 0]), 0).astype(np.int32)
        np.savez_compressed(out / f"{v['name']}.npz", P=P.astype(np.float32), ID=ID)
        print("photoproj 描画:", v["name"], int(hit.sum()), "px")
    vl.material_override = None


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if len(argv) < 2:
        raise SystemExit("usage: blender -b -P blender_build.py -- building.json out_dir [--fast]")
    src, out = Path(argv[0]).resolve(), Path(argv[1]).resolve()
    fast = "--fast" in argv
    out.mkdir(parents=True, exist_ok=True)
    b = json.loads(src.read_text(encoding="utf-8"))

    bpy.ops.wm.read_factory_settings(use_empty=True)
    col = bpy.data.collections.new(b["id"])
    bpy.context.scene.collection.children.link(col)
    info = build_building(b, col)
    pp = [a for a in argv if a.startswith("--photoproj=")]
    if pp:                               # 写真の投影用の描画だけ（photoproj）。通常の描画・glb は出さない
        photoproj_render(info, b, Path(pp[0].split("=", 1)[1]), out)
        return
    c, H, E = info["center"], info["height"], max(info["extent"], 20.0, info["height"] * 0.9)   # 高い棟はカメラを引く
    site_obs = info["grounds"] + info["footings"]       # 地形と地形の下に伸ばした足元
    far = [o for o in info["grounds"] if o.name == "terrain_far"]

    def show_site(on: bool, far_on: bool = True):
        for o in site_obs:
            o.hide_render = not on or (o in far and not far_on)

    tall = H > info["extent"]          # 高い棟は見上げ気味に、少し引く
    look = Vector((c.x, c.y, H * (0.45 if tall else 0.3)))
    persp_sw = Vector((c.x, c.y, 0)) + Vector((-0.25, -0.97, 0)) * (E * (1.7 if tall else 1.45)) + Vector((0, 0, E * (0.6 if tall else 0.85)))
    persp_se = Vector((c.x, c.y, 0)) + Vector((0.55, -0.83, 0)) * (E * (1.6 if tall else 1.3)) + Vector((0, 0, E * (0.55 if tall else 0.7)))

    lens = 22 if tall else 32    # 高い棟は広角で（上が切れないように）
    web_cams: dict = {}
    setup_scene(sketch=True, fast=fast)
    show_site(True, far_on=False)        # 線画の鳥瞰は足元の地形（20 m）だけ。背景の粗い地形は線がうるさい
    camera("persp", persp_sw, look, lens=lens)
    render(out / "persp_sketch.png")

    faces = sorted([f for f in b.get("faces", []) if f.get("from")], key=lambda f: -((f.get("length") or {}).get("value") or 0))
    if faces:
        p0, p1 = Vector((*faces[0]["from"], 0)), Vector((*faces[0]["to"], 0))
        u = (p1 - p0).normalized()
        n0 = Vector((u.y, -u.x, 0))
    else:
        n0 = Vector((0, -1, 0))
    names = []
    show_site(False)                     # 四面図・屋根伏せは建物だけ（地形を描かない）
    dist = E * 3
    scale = max(E, H) * 1.25
    for k in range(4):
        d = Vector((n0.x * math.cos(k * math.pi / 2) - n0.y * math.sin(k * math.pi / 2),
                    n0.x * math.sin(k * math.pi / 2) + n0.y * math.cos(k * math.pi / 2), 0))
        az = (math.degrees(math.atan2(d.x, d.y)) + 360) % 360
        comp = ["北", "北北東", "北東", "東北東", "東", "東南東", "南東", "南南東", "南", "南南西", "南西", "西南西", "西", "西北西", "北西", "北北西"][int((az + 11.25) // 22.5) % 16]
        name = "ABCD"[k]
        names.append({"file": f"view_{name}.png", "label": f"{comp}立面（方位 {az:.0f}°）"})
        camera(f"view_{name}", Vector((c.x, c.y, H / 2)) + d * dist, Vector((c.x, c.y, H / 2)), ortho_scale=scale)
        render(out / f"view_{name}.png")
    (out / "views.json").write_text(json.dumps(names, ensure_ascii=False, indent=1), encoding="utf-8")
    cam = camera("view_top", Vector((c.x, c.y, dist)), Vector((c.x, c.y, 0)), ortho_scale=scale)
    cam.rotation_euler = (0, 0, 0)
    render(out / "view_top.png")

    show_site(True)
    setup_scene(sketch=False, fast=fast)
    camera("persp2", persp_se, look, lens=lens + 3)
    render(out / "persp_shaded.png")
    camera("persp3", persp_sw, look, lens=lens + 3)
    render(out / "persp_shaded2.png")
    if faces:
        mid = (p0 + p1) / 2
        eye = mid + n0 * 38.0 + Vector((0, 0, 1.6))
        cam = camera("street", eye, Vector((mid.x, mid.y, H * 0.45)), lens=16)
        render(out / "persp_street.png")
        sc = bpy.context.scene
        web_cams["street"] = street_camera_web(eye, Vector((mid.x, mid.y, H * 0.45)), 16, cam.data.sensor_width,
                                               (sc.render.resolution_x, sc.render.resolution_y), val(b.get("ground_z"), 0.0))
    # 内装：部屋の中から見た描画（くり抜き・柱・ガラスの壁の確かめ用）。interior_<部屋 id>.png
    I_mod = _interior_mod() if info.get("rooms") else None
    for rid, cav, z0, z1 in info.get("rooms", []):
        room = next(r for r in b["interior"]["rooms"] if r.get("id") == rid)
        cxy = Vector((*I_mod.centroid(cav), 0))
        gw = next((g for g in room.get("glass_walls") or [] if I_mod.glass_segment(g, b)), None)
        if gw:
            a0, a1 = I_mod.glass_segment(gw, b)
            tgt = Vector((*I_mod.face_point(next(f for f in b["faces"] if f["id"] == gw["face"]), (a0 + a1) / 2), 0))
        else:
            tgt = Vector((*max(cav, key=lambda q: (Vector((*q, 0)) - cxy).length), 0))
        d = (tgt - cxy)
        far = Vector((*max(cav, key=lambda q: -(Vector((*q, 0)) - cxy).dot(d)), 0))
        eye = cxy + (far - cxy) * 0.85
        eye.z = z0 + 1.6
        tgt.z = z0 + 1.3
        camera(f"interior_{rid}", eye, tgt, lens=14)
        render(out / f"interior_{rid}.png")
    for f in b.get("faces", []):
        if not f.get("render_view") or not f.get("from"):
            continue
        q0, q1 = Vector((*f["from"], 0)), Vector((*f["to"], 0))
        uu = (q1 - q0).normalized()
        nn = Vector((uu.y, -uu.x, 0))
        mid = (q0 + q1) / 2
        top = val(f.get("top_z"), H)
        eye = mid + nn * max(22.0, (q1 - q0).length * 0.85, top * 0.9) + Vector((0, 0, 1.6))
        # 検収用：この面に斜めから日が当たるように太陽を回す（写真と凹凸を比べるため）
        sun = bpy.context.scene.objects.get("sun")
        if sun:
            # 太陽の光は局所 -Z に進む。Euler (rx, 0, rz) で進行方向の xy は (-sin rz, cos rz)。
            # 面の外向き法線 nn と逆向きに進ませたいので sin rz = nn.x, cos rz = -nn.y。横から 30° ずらす
            rz = math.degrees(math.atan2(nn.x, -nn.y)) - 30.0
            sun.rotation_euler = (math.radians(50), 0, math.radians(rz))
        for o in bpy.context.scene.objects:
            if o.name.startswith("tree_"):
                o.hide_render = True
        camera(f"street_{f['id']}", eye, Vector((mid.x, mid.y, top * 0.45)), lens=18)
        render(out / f"persp_street_{f['id']}.png")

    # 写真の視点（pose JSON）からの描画：検収の重ね合わせ用
    cams = [a for a in argv if a.startswith("--cameras=")]
    if cams:
        for cj in Path(cams[0].split("=", 1)[1]).read_text(encoding="utf-8").splitlines():
            cj = cj.strip()
            if not cj:
                continue
            pj = json.loads(Path(cj).read_text(encoding="utf-8"))
            if not pj.get("ok") or not pj.get("blender"):
                continue
            bl = pj["blender"]
            sc = bpy.context.scene
            w, h = bl["resolution"]
            sc.render.resolution_x, sc.render.resolution_y = (w // 2, h // 2) if fast else (w, h)
            cd = bpy.data.cameras.new("photo_cam")
            cd.lens = bl["lens_mm"]
            cd.sensor_width = bl["sensor_width_mm"]
            cd.sensor_fit = "AUTO"
            cd.clip_end = 4000
            cam = bpy.data.objects.new("photo_cam", cd)
            sc.collection.objects.link(cam)
            cam.location = Vector(bl["location"])
            cam.rotation_euler = tuple(bl["rotation_euler"])
            sc.camera = cam
            render(out / f"view_photo_{Path(pj['photo']).stem}.png")
    import time
    unstripe_hypotheses(info["body"], info.get("hyp_saved"))   # glb は縞なし（Web が evidence の印で縞を描く）
    t_bake = time.time()
    objs = [o for o in bpy.context.scene.objects if o.type == "MESH" and o.name != "terrain_far"]   # glb は足元の地形（20 m）まで
    swap, bake_log = export_web_materials(objs, out / "tex", fast)
    restore = []
    for o in objs:
        for i, slot in enumerate(o.material_slots):
            if slot.material in swap:
                restore.append((o, i, slot.material))
                slot.material = swap[slot.material]
    bake_s = time.time() - t_bake
    for o in bpy.context.scene.objects:
        o.select_set(o in objs)
    face_saved = tag_face_materials(info["body"], info["face_polys"], info["face_ev"])   # 面ごとの印（書き出しの間だけ）
    bpy.ops.export_scene.gltf(filepath=str(out / f"{b['id']}.glb"), use_selection=True, export_apply=True, export_extras=True)
    untag_face_materials(info["body"], face_saved)
    for o, i, m in restore:                               # 描画用の素材に戻してから .blend を保存
        o.material_slots[i].material = m
    _sun = bpy.context.scene.objects.get("sun")           # 検収の描画で面ごとに回した太陽を data/daylight.json の向きに戻して保存
    if _sun:
        _sun.rotation_euler = daylight.blender_sun_euler(*daylight.sun_of(daylight.load()))
    bpy.ops.wm.save_as_mainfile(filepath=str(out / f"{b['id']}.blend"))

    (out / "model_report.json").write_text(json.dumps({
        "building": b["id"], "source": str(src), "height_m": H, "assumed": ASSUMED,
        **info["terrain"].report(), "warnings": info["terrain"].warnings,
        "web_bake": {"seconds": round(bake_s, 2), "materials": bake_log,
                     "note": "glb に出ない手続き的な模様を、周期の板に焼いた画像（tex/）。描画用の素材はそのまま"},
        "web_cameras": web_cams,
        "daylight": json.loads(bpy.context.scene.get("daylight", "null")),   # 描画に使った太陽（data/daylight.json から）
        "interior": [{"room": rid, "floor_z": round(z0, 3), "ceiling_z": round(z1, 3),
                      "cavity": [[round(x, 3), round(y, 3)] for x, y in cav],
                      "cut_volume_est_m3": round(abs(_interior_mod().area(cav)) * (z1 - z0), 2),
                      "inner_polygons": sum(1 for r in info.get("room_polys", {}).values() if r == rid),
                      "render": f"interior_{rid}.png"} for rid, cav, z0, z1 in info.get("rooms", [])],
        "evidence": {"faces": info["face_ev"],
                     "wall_polygons": {fid: len(v) for fid, v in info["face_polys"].items()},
                     "shape_ratio": info["evidence"]["shape_ratio"], "evidence_ratio": info["evidence"]["evidence_ratio"],
                     "note": "glb の node と素材の extras に evidence（measured/estimated/hypothesis/unknown）・face・part。unknown の面の壁は灰色、hypothesis の面の壁は描画だけ黄の縞"},
        "note": "assumed は模型を立てるために置いた仮の値。棟 JSON には書かない。撮影依頼の元にする",
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("模型:", out, "仮の値", len(ASSUMED), "件")
    for w in info["terrain"].warnings:
        print("  模型 warning:", w)


if __name__ == "__main__":
    main()
