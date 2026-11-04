"""棟 JSON の読み込みと検証。

唯一の元データは `data/buildings/<id>.json`。図面・模型・重ね合わせはすべてここから生成する。

値はすべて「測定値（Measurement）」として書く。
    {"value": 3.99, "unit": "m", "tol": 0.25,
     "status": "measured", "evidence": ["F217", "F210"], "method": "正面化", "note": ""}

status の決まり（CLAUDE.md の 1）
    measured   根拠（evidence）が1つ以上あり、value がある
    estimated  value があり、method か note に推定の仕方が書いてある。根拠は無くてもよい
    unknown    value は null。撮影依頼（requests）に理由を書く
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STATUSES = ("measured", "estimated", "unknown")
SCHEMA_ID = "campus-recorder/building/1"


@dataclass
class Measurement:
    value: float | int | None
    unit: str = "m"
    tol: float | None = None
    status: str = "unknown"
    evidence: list[str] = field(default_factory=list)
    method: str = ""
    note: str = ""

    @classmethod
    def from_json(cls, d: Any) -> "Measurement":
        if d is None:
            return cls(None)
        if isinstance(d, (int, float)):
            # 裸の数値は「根拠なし」として扱い、検証で落とす
            return cls(d, status="measured", evidence=[])
        return cls(
            value=d.get("value"),
            unit=d.get("unit", "m"),
            tol=d.get("tol"),
            status=d.get("status", "unknown"),
            evidence=list(d.get("evidence", [])),
            method=d.get("method", ""),
            note=d.get("note", ""),
        )

    def to_json(self) -> dict:
        return {
            "value": self.value,
            "unit": self.unit,
            "tol": self.tol,
            "status": self.status,
            "evidence": self.evidence,
            "method": self.method,
            "note": self.note,
        }

    @property
    def known(self) -> bool:
        return self.value is not None and self.status != "unknown"

    def label(self) -> str:
        """図面に書く短い表記。例: 3.99 ±0.25 [F217,F210]"""
        if not self.known:
            return "未確定"
        s = f"{self.value:.2f}" if isinstance(self.value, float) else str(self.value)
        if self.tol:
            s += f" ±{self.tol:g}"
        if self.status == "estimated":
            s += "（推定）"
        if self.evidence:
            s += " [" + ",".join(self.evidence) + "]"
        return s

    def problems(self, where: str) -> list[str]:
        out: list[str] = []
        if self.status not in STATUSES:
            out.append(f"{where}: status が不正です（{self.status}）")
            return out
        if self.status == "measured":
            if self.value is None:
                out.append(f"{where}: measured なのに value がありません")
            if not self.evidence:
                out.append(f"{where}: measured なのに根拠（evidence）がありません")
        elif self.status == "estimated":
            if self.value is None:
                out.append(f"{where}: estimated なのに value がありません")
            if not (self.method or self.note):
                out.append(f"{where}: estimated なのに推定の仕方（method か note）がありません")
        else:  # unknown
            if self.value is not None:
                out.append(f"{where}: unknown なのに value が入っています（値を消すか status を直す）")
        return out


# --- 棟 JSON の中で Measurement が入る場所 ---------------------------------
# 「どの鍵が測定値か」をここに列挙する。検証と図面生成はこの表を見る。
MEASUREMENT_KEYS_BUILDING = ("ground_z", "parapet_top", "floor_count", "floor_pitch")
MEASUREMENT_KEYS_FACE = ("length", "ground_offset")
MEASUREMENT_KEYS_BAYS = ("count", "pitch", "pier_width", "end_margin", "margin_from", "margin_to")
MEASUREMENT_KEYS_LEVEL = ("z",)
MEASUREMENT_KEYS_WINDOW = ("z_bottom", "z_top", "width", "recess")
MEASUREMENT_KEYS_BAND = ("z_bottom", "z_top", "depth")
MEASUREMENT_KEYS_OPENING = ("z_top",)
MEASUREMENT_KEYS_MATERIAL = ("unit_w", "unit_h", "joint_w")


def _m(d: dict, key: str) -> Measurement:
    return Measurement.from_json(d.get(key))


def face_ground_offset(f: dict) -> float:
    """面の前の地面の高さ（棟の z=0 からの m。沈んだ広場は負、高い広場は正）。省略・unknown は 0。
    faces[].ground_offset は寸法項目（status / evidence / method つき）。図面（地面の線）・模型（面の前の地面の板）・
    検収（高さの原点）が同じ値を使うための共通関数。"""
    m = Measurement.from_json(f.get("ground_offset"))
    return float(m.value) if m.known else 0.0


MARGIN_TOL = 0.05   # 余白の合計が合わないと見なす差（m）。各項目の tol があればそれも足す


def bay_margins(bays: dict | None, length: float | None) -> tuple[float | None, float | None, list[str]]:
    """面の両端の余白 (from 側, to 側, 警告) を m で返す。length が無ければ余りは決まらず None。

    margin_from（無ければ従来の end_margin）と margin_to のうち値のある方をその側に置き、
    無い側に余り（length − count×pitch − 既知の余白）を置く。両方 unknown なら余りを半分ずつ（仮）にして警告する。
    unknown を 0 とは見なさない。
    """
    bays = bays or {}
    mf = _m(bays, "margin_from" if "margin_from" in bays else "end_margin")
    mt = _m(bays, "margin_to")
    cnt, pitch = _m(bays, "count"), _m(bays, "pitch")
    warns: list[str] = []
    f = float(mf.value) if mf.known else None
    t = float(mt.value) if mt.known else None
    if not (cnt.known and pitch.known) or length is None:
        return f, t, warns
    rest = float(length) - float(cnt.value) * float(pitch.value)
    if f is not None and t is not None:
        return f, t, warns
    if f is not None:
        return f, rest - f, warns
    if t is not None:
        return rest - t, t, warns
    if abs(rest) > 0.005:
        warns.append("端の余白 unknown、仮に等分（余り %.2f m を両端に半分ずつ）" % rest)
    return rest / 2, rest / 2, warns


def bay_edges(bays: dict | None, length: float | None) -> tuple[list[float], list[str]]:
    """柱の中心線（面の始点から m）を左から並べる。count / pitch が未確定なら空。
    余白の規則は bay_margins と同じ（図面・検収・模型で同じ並びを使うための共通関数）。"""
    bays = bays or {}
    cnt, pitch = _m(bays, "count"), _m(bays, "pitch")
    if not (cnt.known and pitch.known):
        return [], []
    m0, _mt, warns = bay_margins(bays, length)
    if m0 is None:
        m0 = 0.0
    return [m0 + i * float(pitch.value) for i in range(int(cnt.value) + 1)], warns


def load_building(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_building(b: dict, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(b, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _check_margins(bays: dict, length: Measurement, where: str) -> list[str]:
    mf = _m(bays, "margin_from" if "margin_from" in bays else "end_margin")
    mt = _m(bays, "margin_to")
    cnt, pitch = _m(bays, "count"), _m(bays, "pitch")
    if not (mf.known and mt.known and cnt.known and pitch.known and length.known):
        return []
    rest = float(length.value) - float(cnt.value) * float(pitch.value)
    tol = MARGIN_TOL + (length.tol or 0.0) + (pitch.tol or 0.0) * float(cnt.value)
    if abs(rest - (mf.value + mt.value)) > tol:
        return [f"{where}.bays: 余白の合計が合いません（length − count×pitch = {rest:.2f} m、margin_from + margin_to = {mf.value + mt.value:.2f} m、許容 {tol:.2f} m）"]
    return []


# --- 部品の新しい語彙（11/4。観察係の差分表から）---------------------------------
# 必須の鍵（値は unknown でもよいが、鍵は書く）。形は recorder/parts_geom.py。
PART_REQUIRED = {
    "stair_switchback": ("face", "along", "width", "depth"),
    "porch": ("face", "along", "width", "depth", "height"),
    "steps": ("face", "along", "width", "depth", "count", "rise"),
    "rail": ("face", "along0", "along1", "z", "height"),
    "column": ("x", "y", "z1"),
    "rooftop_units": ("source", "z0", "units"),
    "asset": ("asset", "size"),
    # 11/4 午後（本部棟で見送られた物）
    "ceiling_beams": ("face", "along0", "along1", "depth", "z", "pitch"),
    "louver": ("face", "along", "width", "z0", "z1", "blade_pitch"),
    "planter": ("height",),
    "nameplate": ("width", "height", "thickness", "z0"),
    "patch": ("face", "along0", "along1", "z0", "z1", "material"),
    "vestibule": ("polygon", "z0", "height", "mullion_pitch"),
    "pergola": ("polygon", "z0", "height", "post_pitch", "beam_pitch"),
    "bridge": ("from", "to", "z_from", "z_to", "width", "height"),
    "curved_glass": ("face", "along0", "along1", "radius", "z0", "z1", "mullion_pitch"),
}
# 文字の選択肢（鍵: 許す値）。省略時は先頭の値
PART_ENUMS = {
    "ceiling_beams": {"side": ("in", "out"), "direction": ("across", "along", "grid")},
    "louver": {"orientation": ("horizontal", "vertical")},
    "bridge": {"walls": ("full", "glass", "parapet", "none")},
}
# 裸の数を許す鍵（向きの角度・辺の番号。寸法ではない）
PART_BARE_OK = {"asset": ("asset", "yaw"), "planter": ("yaw",), "nameplate": ("yaw",), "vestibule": ("wall_edges",),
                "stair_switchback": ("end_parapet",)}
# CC0 アセット（data/assets/catalog.json）。位置・個数・大きさは根拠付きの値、見た目だけアセット。
#   {"type": "asset", "asset": "<catalog id>", "face", "along", "offset", "z0", "yaw", "count", "pitch",
#    "size": {"w", "d", "h"}}       面に付く物（単独の物は face/along の代わりに x, y）
#   yaw は置き方の角度（度）で寸法ではないので裸の数を許す。size の各値は Measurement（unknown 可）。
#   既存の部品の "material": "asset:<id>" は catalog の texture を指す。
ASSET_PREFIX = "asset:"
# 裸の数を書いてよい鍵（座標。寸法ではない）。それ以外の裸の数は「根拠の無い値」として落とす
COORD_KEYS = {"x", "y", "polygon", "center", "from", "to"}


def check_values(obj, where: str) -> list[str]:
    """部品の中の値を全部調べる。{"value","status",...} は Measurement の規則で、裸の数は根拠が無いので落とす。"""
    errs: list[str] = []
    if isinstance(obj, dict):
        if "status" in obj and "value" in obj:
            return Measurement.from_json(obj).problems(where)
        for k, v in obj.items():
            if k in COORD_KEYS:
                continue
            errs += check_values(v, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            errs += check_values(v, f"{where}[{i}]")
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        errs.append(f"{where}: 根拠の無い裸の数値です（{{\"value\", \"status\", \"evidence\", \"method\"}} の形で書く）")
    return errs


def check_new_part(pt: dict, where: str) -> list[str]:
    t = pt.get("type")
    errs: list[str] = []
    for k in PART_REQUIRED.get(t, ()):
        if k not in pt:
            errs.append(f"{where}: {k} がありません")
    skip = ("type", "face", "name", "material", "note") + PART_BARE_OK.get(t, ())
    errs += check_values({k: v for k, v in pt.items() if k not in skip}, where)
    for k, allowed in PART_ENUMS.get(t, {}).items():
        if k in pt and pt[k] not in allowed:
            errs.append(f"{where}: {k} は {' / '.join(allowed)} のどれかです（{pt[k]}）")
    if t in ("planter", "nameplate"):
        if pt.get("face"):
            if "along" not in pt:
                errs.append(f"{where}: face に付けるなら along がありません")
        elif not (("x" in pt and "y" in pt) or (t == "planter" and pt.get("polygon"))):
            errs.append(f"{where}: face＋along か x＋y{'（か polygon）' if t == 'planter' else ''}のどれかが要ります")
        if not pt.get("polygon"):
            for k in ("width", "depth") if t == "planter" else ("width",):
                if k not in pt:
                    errs.append(f"{where}: {k} がありません")
    if t in ("vestibule", "pergola") and len(pt.get("polygon") or []) < 3:
        errs.append(f"{where}: polygon は 3 点以上必要です")
    if t == "bridge":
        for k in ("from", "to"):
            if not (isinstance(pt.get(k), list) and len(pt[k]) == 2):
                errs.append(f"{where}: {k} は [x, y] です")
    if t == "patch" and not isinstance(pt.get("material"), str):
        errs.append(f"{where}: material（素材の名前か asset:<id>）がありません")
    if t == "curved_glass":
        a0, a1, r = (Measurement.from_json(pt.get(k)) for k in ("along0", "along1", "radius"))
        if a0.known and a1.known and r.known and float(r.value) < abs(float(a1.value) - float(a0.value)) / 2 - 1e-6:
            errs.append(f"{where}: 半径 {r.value} m が弦の半分 {abs(float(a1.value) - float(a0.value)) / 2:.2f} m より小さい")
    if t == "stair_switchback":
        if "landing_len" not in pt and ("landing_len_from" in pt) != ("landing_len_to" in pt):
            errs.append(f"{where}: landing_len が無いなら landing_len_from と landing_len_to を両方書く")
        if any(e not in ("from", "to") for e in pt.get("end_parapet", [])):
            errs.append(f"{where}: end_parapet は \"from\" / \"to\" の並びです")
    if t == "asset":
        errs += _check_asset_part(pt, where)
    if t == "stair_switchback" and not (pt.get("landing_z") or ("floors" in pt and "floor_pitch" in pt)):
        errs.append(f"{where}: 踊り場の高さ（landing_z か floors＋floor_pitch）がありません")
    if t == "steps":
        if pt.get("edge", "straight") not in ("straight", "curved"):
            errs.append(f"{where}: edge は straight か curved です（{pt.get('edge')}）")
        if pt.get("edge") == "curved" and "radius" not in pt:
            errs.append(f"{where}: edge=curved なのに radius がありません")
    if t == "rooftop_units":
        if pt.get("source") == "aerial" and not isinstance(pt.get("lean_correction_m"), dict):
            errs.append(f"{where}: source=aerial なら lean_correction_m（倒れ込みの補正 [dx,dy] m、根拠つき）を書いてください")
        for i, u in enumerate(pt.get("units") or []):
            if not (u.get("polygon") or u.get("center")):
                errs.append(f"{where}.units[{i}]: polygon か center がありません")
            if u.get("center") and not ("w" in u and "d" in u):
                errs.append(f"{where}.units[{i}]: center の箱には w と d が要ります")
    if t == "column" and pt.get("section", "round") not in ("round", "square"):
        errs.append(f"{where}: section は round か square です")
    return errs


def _catalog_or_error() -> tuple[dict | None, str | None]:
    try:
        from . import assets
    except ImportError:  # recorder/ を sys.path に入れて読んだとき
        import assets  # type: ignore[no-redef]
    try:
        return assets.load_catalog(), None
    except (OSError, ValueError) as e:
        return None, f"アセットの台帳 data/assets/catalog.json が読めません（{e}）"


def _catalog_kind(aid: str, want: str, where: str) -> list[str]:
    cat, err = _catalog_or_error()
    if err:
        return [f"{where}: {err}"]
    it = next((x for x in cat.get("items", []) if x.get("id") == aid), None)
    if it is None:
        return [f"{where}: アセット {aid} は台帳 data/assets/catalog.json にありません"]
    out = []
    if it.get("kind") != want:
        out.append(f"{where}: アセット {aid} は {it.get('kind')} です（{want} が要ります）")
    if it.get("license") != "CC0-1.0":
        out.append(f"{where}: アセット {aid} は CC0 ではないので使えません")
    return out


def _check_asset_part(pt: dict, where: str) -> list[str]:
    errs: list[str] = []
    aid = pt.get("asset")
    if not isinstance(aid, str) or not aid:
        errs.append(f"{where}: asset（台帳の id）がありません")
    else:
        errs += _catalog_kind(aid, "model", where)
    size = pt.get("size")
    if not isinstance(size, dict):
        errs.append(f"{where}: size は {{w, d, h}} です")
    else:
        for k in ("w", "d", "h"):
            if k not in size:
                errs.append(f"{where}.size: {k} がありません（unknown でも鍵は書く）")
    if pt.get("face"):
        if "along" not in pt:
            errs.append(f"{where}: face に付けるなら along がありません")
    elif not ("x" in pt and "y" in pt):
        errs.append(f"{where}: face＋along か x＋y のどちらかが要ります")
    yaw = pt.get("yaw")
    if yaw is not None and not (isinstance(yaw, (int, float)) and not isinstance(yaw, bool)):
        if not (isinstance(yaw, dict) and "value" in yaw):
            errs.append(f"{where}: yaw は度の数か測定値です")
        else:
            errs += Measurement.from_json(yaw).problems(f"{where}.yaw")
    cnt = Measurement.from_json(pt.get("count")) if pt.get("count") is not None else None
    if cnt and cnt.known and float(cnt.value) > 1 and "pitch" not in pt:
        errs.append(f"{where}: count が 2 以上なら pitch が要ります")
    return errs


def _asset_materials(obj, where: str) -> list[str]:
    """"material": "asset:<id>" を全部探し、台帳の texture か調べる。"""
    errs: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "material" and isinstance(v, str) and v.startswith(ASSET_PREFIX):
                errs += _catalog_kind(v[len(ASSET_PREFIX):], "texture", f"{where}.material")
            else:
                errs += _asset_materials(v, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            errs += _asset_materials(v, f"{where}[{i}]")
    return errs


def validate_building(b: dict) -> list[str]:
    """規約違反の一覧を返す。空なら合格。"""
    errs: list[str] = []
    if b.get("schema") != SCHEMA_ID:
        errs.append(f"schema は {SCHEMA_ID} にしてください")
    for k in ("id", "name"):
        if not b.get(k):
            errs.append(f"{k} がありません")

    for k in MEASUREMENT_KEYS_BUILDING:
        if k in b:
            errs += _m(b, k).problems(k)

    fp = b.get("footprint")
    if fp is not None:
        st = fp.get("status", "unknown")
        if st not in STATUSES:
            errs.append(f"footprint.status が不正です（{st}）")
        if st == "measured" and not fp.get("evidence"):
            errs.append("footprint: measured なのに根拠がありません")
        poly = fp.get("polygon") or []
        if st != "unknown" and len(poly) < 3:
            errs.append("footprint: polygon は3点以上必要です")

    for lv in b.get("levels", []):
        where = f"levels[{lv.get('id', '?')}]"
        if not lv.get("id"):
            errs.append(f"{where}: id がありません")
        errs += _m(lv, "z").problems(f"{where}.z")

    face_ids: set[str] = set()
    for fc in b.get("faces", []):
        fid = fc.get("id", "?")
        where = f"faces[{fid}]"
        if fid in face_ids:
            errs.append(f"{where}: id が重複しています")
        face_ids.add(fid)
        if not fc.get("name"):
            errs.append(f"{where}: name がありません")
        for k in MEASUREMENT_KEYS_FACE:
            if k in fc:
                errs += _m(fc, k).problems(f"{where}.{k}")
        bays = fc.get("bays")
        if bays:
            for k in MEASUREMENT_KEYS_BAYS:
                if k in bays:
                    errs += _m(bays, k).problems(f"{where}.bays.{k}")
            errs += _check_margins(bays, _m(fc, "length"), where)
        for i, w in enumerate(fc.get("windows", [])):
            for k in MEASUREMENT_KEYS_WINDOW:
                if k in w:
                    errs += _m(w, k).problems(f"{where}.windows[{i}].{k}")
        for i, bd in enumerate(fc.get("bands", [])):
            for k in MEASUREMENT_KEYS_BAND:
                if k in bd:
                    errs += _m(bd, k).problems(f"{where}.bands[{i}].{k}")
        for i, op in enumerate(fc.get("openings", [])):
            for k in MEASUREMENT_KEYS_OPENING:
                if k in op:
                    errs += _m(op, k).problems(f"{where}.openings[{i}].{k}")
        for i, mt in enumerate(fc.get("materials", [])):
            for k in MEASUREMENT_KEYS_MATERIAL:
                if k in mt:
                    errs += _m(mt, k).problems(f"{where}.materials[{i}].{k}")

    # 設計の部品（blocks / parts / faces[].glazing）：中に入っている測定値を全部検証する
    def walk(obj, where: str) -> None:
        if isinstance(obj, dict):
            if "status" in obj and "value" in obj:
                errs.extend(Measurement.from_json(obj).problems(where))
                return
            for k, v in obj.items():
                walk(v, f"{where}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{where}[{i}]")

    for i, blk in enumerate(b.get("blocks", [])):
        if not blk.get("id"):
            errs.append(f"blocks[{i}]: id がありません")
        if len(blk.get("polygon") or []) < 3:
            errs.append(f"blocks[{i}]: polygon は3点以上必要です")
        walk({k: v for k, v in blk.items() if k != "polygon"}, f"blocks[{blk.get('id', i)}]")
    for i, pt in enumerate(b.get("parts", [])):
        if not pt.get("type"):
            errs.append(f"parts[{i}]: type がありません")
        if pt.get("face") and pt["face"] not in face_ids:
            errs.append(f"parts[{i}]: face={pt['face']} は faces にありません")
        if pt.get("type") in PART_REQUIRED:
            errs.extend(check_new_part(pt, f"parts[{i}:{pt.get('type')}]"))
        else:
            walk({k: v for k, v in pt.items() if k not in ("polygon",)}, f"parts[{i}:{pt.get('type')}]")
            if pt.get("type") == "ac_units" and isinstance(pt.get("z0"), (int, float)):
                errs.extend(check_values(pt["z0"], f"parts[{i}:ac_units].z0"))   # 取付け高さ（壁付け）。裸の数は落とす
    for fc in b.get("faces", []):
        walk(fc.get("glazing", []), f"faces[{fc.get('id')}].glazing")
        for i, w in enumerate(fc.get("windows", [])):
            if "spandrel" in w:
                errs.extend(check_values({k: v for k, v in w["spandrel"].items() if k != "material"}, f"faces[{fc.get('id')}].windows[{i}].spandrel"))
                if "height" not in w["spandrel"]:
                    errs.append(f"faces[{fc.get('id')}].windows[{i}].spandrel: height がありません")
        for i, bd in enumerate(fc.get("bands", [])):
            if "rail" in bd:
                errs.extend(check_values(bd["rail"], f"faces[{fc.get('id')}].bands[{i}].rail"))

    errs += _asset_materials({"faces": b.get("faces", []), "parts": b.get("parts", []), "blocks": b.get("blocks", [])}, "")

    for i, rq in enumerate(b.get("requests", [])):
        where = f"requests[{i}]"
        for k in ("face", "what", "why"):
            if not rq.get(k):
                errs.append(f"{where}: {k} がありません（撮影依頼には面・何を・なぜ が必要）")
        if rq.get("face") and rq["face"] not in face_ids and rq["face"] != "*":
            errs.append(f"{where}: face={rq['face']} は faces にありません")

    return errs


def unknowns(b: dict) -> list[str]:
    """未確定の項目の一覧（図面の赤字と撮影依頼の元）。"""
    out: list[str] = []

    def chk(m: Measurement, where: str) -> None:
        if not m.known:
            out.append(where)

    for k in MEASUREMENT_KEYS_BUILDING:
        if k in b:
            chk(_m(b, k), k)
    fp = b.get("footprint")
    if fp is None or fp.get("status", "unknown") == "unknown":
        out.append("footprint")
    for lv in b.get("levels", []):
        chk(_m(lv, "z"), f"levels[{lv.get('id')}].z")
    for fc in b.get("faces", []):
        fid = fc.get("id")
        for k in MEASUREMENT_KEYS_FACE:
            if k in fc:
                chk(_m(fc, k), f"faces[{fid}].{k}")
        for k in MEASUREMENT_KEYS_BAYS:
            if fc.get("bays") and k in fc["bays"]:
                chk(_m(fc["bays"], k), f"faces[{fid}].bays.{k}")
        for i, w in enumerate(fc.get("windows", [])):
            for k in MEASUREMENT_KEYS_WINDOW:
                if k in w:
                    chk(_m(w, k), f"faces[{fid}].windows[{i}].{k}")
    return out


def new_building(bid: str, name: str, campus: str = "meijo-tempaku") -> dict:
    """空の棟 JSON。全部 unknown から始める。"""
    unk = Measurement(None).to_json()
    return {
        "schema": SCHEMA_ID,
        "id": bid,
        "name": name,
        "campus": campus,
        "crs": {"epsg": 6675, "origin_lat": 35.1355, "origin_lon": 136.9755,
                "note": "原点からの m。x=東, y=北。z は標高 m"},
        "footprint": {"status": "unknown", "evidence": [], "method": "", "polygon": []},
        "ground_z": dict(unk),
        "floor_count": {**unk, "unit": "階"},
        "floor_pitch": dict(unk),
        "parapet_top": dict(unk),
        "levels": [],
        "faces": [],
        "photos": [],
        "requests": [],
        "history": [],
    }


# --- 根拠の層（歩く画面の「根拠で見る」と棟の札の割合） --------------------------------
# 面・部品ごとに「その形を決めている値」の status を集めて、状態を 1 つに決める。
#   unknown     形を決める芯の値が無い（面：柱の数も窓・ガラス・開口・帯も分からない／部品：値が 1 つも分からない）
#   hypothesis  分かっている値のどれかに "basis": "hypothesis"（仮説として置いた値）がある
#   measured    集めた値が全部 measured（unknown の値が 1 つでも混じれば measured ではない）
#   estimated   それ以外（measured と estimated の混在、細部に unknown が残る）
# 面の値：bays.count・pitch・pier_width、窓・帯・開口・ガラス・水切りの値、floor_pitch、面の上端（top_z か塊の top_z か parapet_top）。
#        length（足元の辺）と仕上げ（materials）は形の根拠に数えない。
EVIDENCE_STATES = ("measured", "estimated", "hypothesis", "unknown")
_FACE_DETAIL_KEYS = ("windows", "bands", "openings", "glazing", "ledges")
_SUMMARY_SKIP = {"photos", "requests", "history", "crs"}


def is_value(d) -> bool:
    """{"value", "status"} の測定値か。"""
    return isinstance(d, dict) and "value" in d and d.get("status") in STATUSES


def iter_values(obj, skip: set | frozenset = frozenset()):
    """obj の中の測定値（dict）を全部返す。座標（COORD_KEYS）と skip の鍵の下は見ない。"""
    if is_value(obj):
        yield obj
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in COORD_KEYS or k in skip:
                continue
            yield from iter_values(v, skip)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_values(v, skip)


def _known(m: dict) -> bool:
    return m.get("value") is not None and m.get("status") != "unknown"


def combine_state(values: list[dict], core_known: bool = True) -> str:
    """値の status から状態を 1 つに決める（規則は上の表）。"""
    known = [m for m in values if _known(m)]
    if not core_known or not known:
        return "unknown"
    if any(m.get("basis") == "hypothesis" for m in known):
        return "hypothesis"
    if all(m.get("status") == "measured" for m in values):
        return "measured"
    return "estimated"


def _face_top_values(b: dict, f: dict) -> list[dict]:
    if is_value(f.get("top_z")):
        return [f["top_z"]]
    ends = [f.get("from"), f.get("to")]
    out = []
    for blk in b.get("blocks") or []:
        pts = blk.get("polygon") or []
        hit = all(e and any(abs(p[0] - e[0]) < 0.5 and abs(p[1] - e[1]) < 0.5 for p in pts) for e in ends)
        if hit and is_value(blk.get("top_z")):
            out.append(blk["top_z"])
    if out:
        return out
    return [b["parapet_top"]] if is_value(b.get("parapet_top")) else []


def face_state(b: dict, f: dict) -> dict:
    """面の状態。{"state", "values": 数, "measured": 数, "not_measured": [どこが measured でないか]}。"""
    bays = f.get("bays") or {}
    named: list[tuple[str, dict]] = [(f"bays.{k}", bays[k]) for k in ("count", "pitch", "pier_width") if is_value(bays.get(k))]
    detail = []
    for k in _FACE_DETAIL_KEYS:
        for i, it in enumerate(f.get(k) or []):
            for j, m in enumerate(iter_values(it)):
                detail.append((f"{k}[{i}]#{j}", m))
    core_known = (is_value(bays.get("count")) and _known(bays["count"])) or any(_known(m) for _, m in detail)
    named += detail
    if core_known:
        if is_value(b.get("floor_pitch")):
            named.append(("floor_pitch", b["floor_pitch"]))
        named += [("top_z", m) for m in _face_top_values(b, f)]
    vals = [m for _, m in named]
    st = combine_state(vals, core_known)
    return {"state": st, "values": len(vals), "measured": sum(m.get("status") == "measured" for m in vals),
            "not_measured": [w for w, m in named if m.get("status") != "measured"]}


def part_name(pt: dict, i: int) -> str:
    """部品の名前（blender_build の物の名前の頭と同じ <type>_<番号>）。"""
    return f"{pt.get('type')}_{i}"


def part_state(pt: dict) -> str:
    vals = list(iter_values(pt))
    return combine_state(vals, any(_known(m) for m in vals))


def evidence_summary(b: dict) -> dict:
    """棟の根拠の層。faces（面 id → 状態の詳細）・parts（部品名 → 状態）・body（塊の高さ）と 2 つの割合：
        shape_ratio     形がある割合 = unknown でない面・部品の数 ÷ 面・部品の数
        evidence_ratio  根拠がある割合 = measured の値の数 ÷ 棟 JSON の全部の測定値の数（photos・requests・history は除く）"""
    faces = {f["id"]: face_state(b, f) for f in b.get("faces") or [] if f.get("from") and f.get("to")}
    parts = {part_name(pt, i): part_state(pt) for i, pt in enumerate(b.get("parts") or [])}
    tops = [blk["top_z"] for blk in b.get("blocks") or [] if is_value(blk.get("top_z"))]
    if not tops and is_value(b.get("parapet_top")):
        tops = [b["parapet_top"]]
    states = [v["state"] for v in faces.values()] + list(parts.values())
    allv = list(iter_values(b, _SUMMARY_SKIP))
    nm = sum(m.get("status") == "measured" for m in allv)
    count = {s: states.count(s) for s in EVIDENCE_STATES}
    return {"faces": faces, "parts": parts, "body": combine_state(tops),
            "shape_ratio": round((len(states) - count["unknown"]) / len(states), 3) if states else 0.0,
            "evidence_ratio": round(nm / len(allv), 3) if allv else 0.0,
            "state_counts": count, "values": len(allv), "measured_values": nm}
