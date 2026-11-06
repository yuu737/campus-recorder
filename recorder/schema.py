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
import re
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
    # 11/5 語彙に無い形の組み立て（parts_geom.composite）。寸法は素片ごとの Measurement か、全体に 1 つの status/evidence
    "composite": ("name", "primitives"),
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
    if t == "composite":
        return _check_composite(pt, where)
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


# 組み立て部品（composite）の形ごとの必須の鍵。世界座標の別名 x / y / polygon_xy は s / d / polygon_sd と同じ。
COMPOSITE_SHAPE_KEYS = {"box": ("s", "d", "z"), "wedge": ("s", "d", "z"), "cylinder": ("at", "r", "z"),
                        "prism": ("polygon_sd", "z"), "arc_wall": ("center", "r", "deg", "z", "t")}
_COMPOSITE_ALIASES = {"x": "s", "y": "d", "polygon_xy": "polygon_sd"}
_COMPOSITE_PAIRS = ("s", "d", "z", "at", "center", "deg")
_COMPOSITE_WORDS = ("shape", "name", "material", "cut", "rise", "seg")      # 寸法でない鍵


def _check_composite(pt: dict, where: str) -> list[str]:
    """組み立て部品の検査。全体に status があれば（まとめの形）素片の裸の数を許し、その status の規則を全体に当てる。
    無ければ素片の数は全部 Measurement（根拠付き）。どちらも部品なので仮説（basis）は不可。"""
    errs: list[str] = []
    if not isinstance(pt.get("name"), str) or not pt.get("name"):
        errs.append(f"{where}: name（何の組み立てか。デッキ・ピロティの天井 など）がありません")
    prims = pt.get("primitives")
    if not isinstance(prims, list) or not prims:
        errs.append(f"{where}: primitives（素片の並び）がありません")
        prims = []
    if "basis" in pt or "hypothesis" in pt:
        errs.append(f"{where}: 部品は仮説で作らない（basis/hypothesis は使えません。写真に写っている物だけ置く）")
    grouped = "status" in pt
    if grouped:
        st = pt.get("status")
        if st not in STATUSES:
            errs.append(f"{where}: status が不正です（{st}）")
        elif st == "measured" and not pt.get("evidence"):
            errs.append(f"{where}: measured なのに根拠（evidence）がありません")
        elif st == "estimated":
            if not pt.get("evidence"):
                errs.append(f"{where}: estimated の組み立ても根拠の写真 id（evidence）が要ります（写っている物だけ置く）")
            if not (pt.get("method") or pt.get("note")):
                errs.append(f"{where}: estimated なのに推定の仕方（method か note）がありません")
        tol = pt.get("tol")
        if tol is not None and not (isinstance(tol, (int, float)) and not isinstance(tol, bool) and tol >= 0):
            errs.append(f"{where}: tol は 0 以上の数です（{tol!r}）")
    if "along" in pt:
        if not pt.get("face"):
            errs.append(f"{where}: along は face に付ける時だけ使えます")
        if not grouped:
            errs += check_values(pt["along"], f"{where}.along")
        elif isinstance(pt["along"], dict):
            errs += Measurement.from_json(pt["along"]).problems(f"{where}.along")
    for i, raw in enumerate(prims):
        w = f"{where}.primitives[{i}]"
        if not isinstance(raw, dict):
            errs.append(f"{w}: 素片は {{\"shape\", …}} の形です")
            continue
        pr = {_COMPOSITE_ALIASES.get(k, k): v for k, v in raw.items()}
        sh = pr.get("shape")
        if sh not in COMPOSITE_SHAPE_KEYS:
            errs.append(f"{w}: shape は {' / '.join(COMPOSITE_SHAPE_KEYS)} のどれかです（{sh!r}）")
            continue
        w = f"{where}.primitives[{i}:{sh}]"
        for k in COMPOSITE_SHAPE_KEYS[sh]:
            if k not in pr:
                errs.append(f"{w}: {k} がありません")
        for k in _COMPOSITE_PAIRS:
            if k in pr and not (isinstance(pr[k], list) and len(pr[k]) == 2):
                errs.append(f"{w}: {k} は 2 つの値の並びです")
        if "polygon_sd" in pr and not (isinstance(pr["polygon_sd"], list) and len(pr["polygon_sd"]) >= 3
                                       and all(isinstance(q, list) and len(q) == 2 for q in pr["polygon_sd"])):
            errs.append(f"{w}: polygon_sd は [s, d] の 3 点以上の並びです")
        if sh == "wedge" and pr.get("rise", "+s") not in ("+s", "-s", "+d", "-d"):
            errs.append(f"{w}: rise は +s / -s / +d / -d のどれかです（{pr.get('rise')!r}）")
        if "material" in pr and not isinstance(pr["material"], str):
            errs.append(f"{w}: material は素材の名前か asset:<id> です")
        if "cut" in pr and not isinstance(pr["cut"], bool):
            errs.append(f"{w}: cut は true / false です")
        dims = {k: v for k, v in pr.items() if k not in _COMPOSITE_WORDS}
        if grouped:
            # まとめの形：裸の数は可。中に Measurement があればその規則で
            def walk(o, ww):
                if isinstance(o, dict):
                    if "status" in o and "value" in o:
                        errs.extend(Measurement.from_json(o).problems(ww))
                        if "basis" in o:
                            errs.append(f"{ww}: 部品は仮説で作らない（basis は使えません）")
                elif isinstance(o, list):
                    for j, v in enumerate(o):
                        walk(v, f"{ww}[{j}]")
            for k, v in dims.items():
                walk(v, f"{w}.{k}")
        else:
            # 素片ごとの形：座標の鍵（center など）も寸法として調べる
            errs += check_values({("_" + k if k in COORD_KEYS else k): v for k, v in dims.items()}, w)
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


# --- 依頼（requests）の宛先 ------------------------------------------------------------------
# requests には職員への撮影依頼と、道具・係への作業依頼（語彙・欄・再計算・調べ物）が混ざる。
# 各項目は任意の欄 to（"staff"＝職員に撮ってもらう／"tool"＝道具・係の作業）を持てる。
# 無い物は文面から決める（request_to）。報告と職員の画面の「撮影依頼」は staff 宛てだけを数える。
REQUEST_TO = ("staff", "tool")
_REQ_TOOL_RE = re.compile(r"recorder/|scripts/|\.py\b|語彙|欄|調べ物|撮影でなく")
_REQ_STAFF_RE = re.compile(r"撮|写真|枚")
_REQ_ID_PATH_RE = re.compile(r"^[A-Za-z0-9_.\[\]:/-]+$")     # where が JSON の道（interior.rooms[x].floor など）
_REQ_VIEW_RE = re.compile(r"から(?:[、（(]|$)|\d+\s*°|正面|真横|斜め")      # 「45° から」「正面で」など撮る向きの言い方


def request_to(rq: dict) -> str:
    """依頼の宛先。to が書いてあればそれ。次に既存の kind（agents.detect_events が使う）："tool"・"asset" → tool、
    "photo" → staff。どちらも無ければ（kind が "data"・"check" も）文面から：
    1. 何を（what）・どこから（where）にコードの場所・語彙・欄・調べ物、または where が JSON の道 → tool
    2. what・where・why に「撮」「写真」「枚」 → staff
    3. where が撮る場所（「…から」を含む）か、what が撮る向き（「45° から」「正面で」「真横」「斜め」） → staff
       （「北東の角を 45° から」のように「撮る」と書かない撮影依頼があるため）
    4. それ以外 → tool"""
    to = rq.get("to")
    if to in REQUEST_TO:
        return to
    kind = rq.get("kind")
    if kind in ("tool", "asset"):
        return "tool"
    if kind == "photo":
        return "staff"
    what, where, why = (str(rq.get(k) or "") for k in ("what", "where", "why"))
    if _REQ_TOOL_RE.search(what + "\n" + where) or (where and _REQ_ID_PATH_RE.match(where.strip())):
        return "tool"
    if _REQ_STAFF_RE.search("\n".join((what, where, why))):
        return "staff"
    if "から" in where or _REQ_VIEW_RE.search(what.strip()):
        return "staff"
    return "tool"


def split_requests(b: dict, status: str | None = "open") -> dict[str, list[dict]]:
    """requests を宛先で分ける {"staff": [...], "tool": [...]}。status=None なら全部（既定は open だけ）。"""
    out: dict[str, list[dict]] = {"staff": [], "tool": []}
    for rq in b.get("requests", []) or []:
        if status is not None and (rq.get("status") or "open") != status:
            continue
        out[request_to(rq)].append(rq)
    return out


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
    errs += check_hypotheses(b)

    for i, rq in enumerate(b.get("requests", [])):
        where = f"requests[{i}]"
        for k in ("face", "what", "why"):
            if not rq.get(k):
                errs.append(f"{where}: {k} がありません（撮影依頼には面・何を・なぜ が必要）")
        if rq.get("face") and rq["face"] not in face_ids and rq["face"] != "*":
            errs.append(f"{where}: face={rq['face']} は faces にありません")
        if "to" in rq and rq["to"] not in REQUEST_TO:
            errs.append(f"{where}: to={rq['to']!r} は {' / '.join(REQUEST_TO)} のどちらか（職員への撮影依頼か、道具・係の作業か）")

    # 差し込み口：進行役の決定（語彙・アセットの許可）と内装の検証。モジュールが無ければ素通り
    import importlib
    for _hook in ("decisions", "interior"):
        try:
            _mod = importlib.import_module("." + _hook, __package__)
        except ImportError:
            continue
        _fn = getattr(_mod, "validate_building_hook", None)
        if _fn:
            errs += list(_fn(b))

    return errs


# --- 警告（審査員役の指摘 11/6。落とさずに知らせる。recorder validate --strict でエラー扱い） ----------
# 1. ± の下限      物差し（PLATEAU・OSM・タイル・tape）の許容 × value より tol が小さい長さの値
# 2. 採用値の照合  method の「採用 4.19 m」と value が 1% 以上違う
# 3. 根拠 id の実在 写真の索引・plateau:bldg_…・osm:way|relation/<数字>・gsi:…・data/ のパス のどれでもない。
#                  out/ のパスは git 管理外（コミットされない根拠）なのでまとめて 1 件
ROOT = Path(__file__).resolve().parent.parent
RULER_DEFAULT_RATIO = {"plateau": 0.03, "osm": 0.05, "tile": 0.005 / 0.070, "tape": 0.01}
_RULER_WORDS = {"plateau": ("plateau",), "osm": ("osm",), "tile": ("タイル",), "tape": ("tape", "メジャー")}
_RULER_LABEL = {"plateau": "PLATEAU", "osm": "OSM", "tile": "タイル", "tape": "tape"}
_ADOPT_RE = re.compile(r"採用[^0-9]{0,6}")
_ADOPT_NUM = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*(mm|px|つ|枚|か所|本|倍|%)?")
_ADOPT_SKIP_UNITS = {"px", "つ", "枚", "か所", "本", "倍", "%"}     # 採用値でない数（画素・個数・割合）
_OSM_RE = re.compile(r"^osm:(way|relation)/\d+$")
ADOPT_TOL = 0.01
_WARN_SKIP = {"history", "crs"}


def ruler_ratios(root: Path | None = None) -> dict[str, float]:
    """物差しごとの許容の割合。data/rulers.json（tol_ratio か tol/value）が優先、無ければ RULER_DEFAULT_RATIO。"""
    out = dict(RULER_DEFAULT_RATIO)
    try:
        doc = json.loads(((root or ROOT) / "data" / "rulers.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    pick = {"plateau_wall": "plateau", "tile_course": "tile"}
    for r in doc.get("rulers", []):
        rid = r.get("id", "")
        key = pick.get(rid) or ("osm" if rid.startswith("osm") else "tape" if rid.startswith("tape") else None)
        if not key:
            continue
        if r.get("tol_ratio"):
            out[key] = float(r["tol_ratio"])
        elif r.get("tol") and r.get("value"):
            out[key] = float(r["tol"]) / float(r["value"])
    return out


def load_photo_ids(root: Path | None = None) -> set[str]:
    """写真の索引（data/photos/*.json）にある写真 id（id と photo の鍵）を全部。索引が無ければ空。"""
    ids: set[str] = set()
    for f in sorted(((root or ROOT) / "data" / "photos").glob("*.json")):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        lists = [doc] if isinstance(doc, list) else [v for v in doc.values() if isinstance(v, list)] if isinstance(doc, dict) else []
        for lst in lists:
            for it in lst:
                if isinstance(it, dict):
                    ids.update(str(it[k]) for k in ("id", "photo") if isinstance(it.get(k), str))
    return ids


def _seg(i: int, v) -> str:
    """場所の書き方：id のある物は id（faces[E]）、無ければ番号。"""
    return v["id"] if isinstance(v, dict) and isinstance(v.get("id"), str) and v["id"] else str(i)


def _walk_values(obj, where: str):
    """(場所, 測定値 dict) を全部。history・crs の下は見ない。"""
    if isinstance(obj, dict):
        if "value" in obj and "status" in obj:
            yield where, obj
            return
        for k, v in obj.items():
            if k in _WARN_SKIP:
                continue
            yield from _walk_values(v, f"{where}.{k}" if where else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_values(v, f"{where}[{_seg(i, v)}]")


def rulers_of(m: dict) -> list[str]:
    """method と evidence から縮尺の物差しを判定する（語の有無だけの簡単な規則）。"""
    text = str(m.get("method") or "").lower()
    ev = [str(e).lower() for e in m.get("evidence") or []]
    out = []
    for key, words in _RULER_WORDS.items():
        if any(w.lower() in text for w in words) or any(e.startswith(key + ":") for e in ev):
            out.append(key)
    return out


def warn_tol_floor(b: dict, ratios: dict[str, float] | None = None) -> list[str]:
    """1. ± の下限。長さ（unit m、標高の ground_z は除く）で tol < 物差しの許容 × |value| なら警告。
    物差しが複数あれば一番細かい物（割合の小さい物）を下限にする。tol が無い値は調べない。"""
    ratios = ratios or ruler_ratios()
    out: list[str] = []
    for where, m in _walk_values(b, ""):
        if m.get("unit", "m") != "m" or where.endswith("ground_z") or m.get("status") == "unknown":
            continue
        v, tol = m.get("value"), m.get("tol")
        if not (isinstance(v, (int, float)) and isinstance(tol, (int, float))) or isinstance(v, bool):
            continue
        rs = rulers_of(m)
        if not rs:
            continue
        key = min(rs, key=lambda k: ratios.get(k, 1.0))
        floor = ratios[key] * abs(float(v))
        if tol < floor - 1e-9:
            out.append(f"{where}: ±{tol:g} m は物差し（{_RULER_LABEL[key]} {ratios[key] * 100:.1f}%）の下限 "
                       f"±{floor:.3f} m より小さい（value {v:g} m）")
    return out


def adopted_values(method: str, unit: str = "m") -> list[float]:
    """method の「採用 4.19 m」「採用は平均 4.52」の採用値。採用の後ろ（句読点・括弧まで、20 字以内）で
    最初の、画素・個数・割合でない数を取る（「採用 552.5 px」「採用は 2 つの平均 4.52 m」は 4.52 だけ）。mm は m に直す。"""
    out: list[float] = []
    for mt in _ADOPT_RE.finditer(method):
        win = re.split(r"[。、，,（(；;]", method[mt.end():mt.end() + 20])[0]
        for nm in _ADOPT_NUM.finditer(win):
            u = nm.group(2)
            if u in _ADOPT_SKIP_UNITS:
                continue
            out.append(float(nm.group(1)) * (0.001 if u == "mm" and unit == "m" else 1.0))
            break
    return out


def warn_adopted(b: dict) -> list[str]:
    """2. method に書いた採用値（「採用 4.19 m」「採用は平均 4.52」）と value が 1% 以上違えば警告。"""
    out: list[str] = []
    for where, m in _walk_values(b, ""):
        v = m.get("value")
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        nums = adopted_values(str(m.get("method") or ""), m.get("unit", "m"))
        if not nums:
            continue
        if not any(abs(n - float(v)) <= ADOPT_TOL * max(abs(float(v)), 1e-9) for n in nums):
            out.append(f"{where}: method の採用値 {'・'.join(f'{n:g}' for n in nums)} と value {v:g} が 1% 以上違う")
    return out


def _walk_evidence(obj, where: str):
    if isinstance(obj, dict):
        ev = obj.get("evidence")
        if isinstance(ev, list):
            for e in ev:
                yield where, e
        for k, v in obj.items():
            if k in _WARN_SKIP or k == "evidence":
                continue
            yield from _walk_evidence(v, f"{where}.{k}" if where else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_evidence(v, f"{where}[{_seg(i, v)}]")


def warn_evidence_ids(b: dict, photo_ids: set[str] | None = None, root: Path | None = None) -> list[str]:
    """3. 根拠 id の実在。同じ id の警告は 1 件にまとめ、か所数と最初の場所を書く。
    photo_ids が None なら索引から読む。索引が空（無い）なら PXL_ で始まる id は通す。"""
    root = root or ROOT
    if photo_ids is None:
        photo_ids = load_photo_ids(root)
    bad: dict[str, tuple[str, int, str]] = {}       # id → (理由, か所数, 最初の場所)
    outs: dict[str, int] = {}

    def note(e: str, why: str, where: str) -> None:
        r, n, w = bad.get(e, (why, 0, where))
        bad[e] = (r, n + 1, w)

    for where, e in _walk_evidence(b, ""):
        if not isinstance(e, str) or not e:
            note(repr(e), "文字の id ではない", where)
            continue
        if e in photo_ids or (not photo_ids and e.startswith("PXL_")):
            continue
        if e.startswith("plateau:bldg_") or e.startswith("gsi:"):
            continue
        if e.startswith("osm:"):
            if not _OSM_RE.match(e):
                note(e, "OSM の id が osm:way/<数字> か osm:relation/<数字> ではない", where)
            continue
        if e.startswith("out/"):
            outs[e] = outs.get(e, 0) + 1
            continue
        if e.startswith("data/"):
            if not (root / e).exists():
                note(e, "根拠のファイルがありません", where)
            continue
        note(e, "写真の索引・plateau:bldg_…・osm:…・gsi:…・data/ のパスのどれでもない", where)
    out = [f"{w}: 根拠 {e} — {r}（{n} か所）" for e, (r, n, w) in bad.items()]
    if outs:
        missing = [p for p in outs if not (root / p).exists()]
        ex = "、".join(list(outs)[:3]) + (" ほか" if len(outs) > 3 else "")
        out.append(f"out/ の根拠 {sum(outs.values())} か所（{len(outs)} ファイル、うち今ここに無い {len(missing)}）は "
                   f"git 管理外でコミットされない根拠です（{ex}）。data/reports/ に移すか写真 id に置き換える")
    return out


# 4. 根拠の門（11/7。模擬採点で 8 人全員が突いた「仕分けの誤りが根拠付きの measured として素通りする」への答え）
#    measured の根拠に使った写真が、写真の索引で ①この棟に仕分けられているか ②値の面（faces[<id>] の下・"face" を持つ部品）
#    に仕分けられているか ③仕分けの確からしさが low なら職員が確かめたか（confirmed_by）を照合する。
#    外れた写真は「職員に確かめてもらう一覧」（evidence_checks）にもなる（recorder photos to-confirm）。
def load_photo_records(root: Path | None = None) -> dict[str, dict]:
    """写真の索引 data/photos/index.json の写真 id → 記録。無ければ空。"""
    p = (root or ROOT) / "data" / "photos" / "index.json"
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(r["id"]): r for r in doc.get("photos", []) if isinstance(r, dict) and r.get("id")}


def _walk_measured_ctx(obj, where: str, face: str | None):
    """(場所, 面 id か None, 測定値) を、measured の値だけ返す。faces[<id>] の下と "face" を持つ dict は面を引き継ぐ。"""
    if isinstance(obj, dict):
        if "value" in obj and "status" in obj:
            if obj.get("status") == "measured":
                yield where, face, obj
            return
        f2 = obj.get("face") if isinstance(obj.get("face"), str) and obj.get("face") else face
        for k, v in obj.items():
            if k in _WARN_SKIP:
                continue
            if k == "faces" and isinstance(v, list) and not where:
                for fc in v:
                    if isinstance(fc, dict):
                        yield from _walk_measured_ctx({kk: vv for kk, vv in fc.items() if kk not in ("id", "name")},
                                                      f"faces[{fc.get('id')}]", fc.get("id"))
                continue
            yield from _walk_measured_ctx(v, f"{where}.{k}" if where else k, f2)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_measured_ctx(v, f"{where}[{_seg(i, v)}]", face)


def evidence_checks(b: dict, records: dict[str, dict] | None = None, root: Path | None = None) -> list[dict]:
    """measured の根拠の写真のうち、仕分けと合わない物・確からしさ low で職員が確かめていない物。
    1 件 = {"photo", "problem"（other_building／no_building／other_face／no_face／low_unconfirmed）, "detail",
    "building", "face", "faces", "confidence", "used_at"（値の場所の一覧）, "value_faces"（値の面）}"""
    records = load_photo_records(root) if records is None else records
    bid = b.get("id")
    found: dict[tuple[str, str], dict] = {}

    def add(pid: str, problem: str, detail: str, where: str, vface: str | None, r: dict) -> None:
        it = found.setdefault((pid, problem), {
            "photo": pid, "problem": problem, "detail": detail, "building": r.get("building"), "face": r.get("face"),
            "faces": r.get("faces"), "confidence": r.get("confidence"), "notes": r.get("notes"), "file": r.get("file"),
            "used_at": [], "value_faces": []})
        if where not in it["used_at"]:
            it["used_at"].append(where)
        if vface and vface not in it["value_faces"]:
            it["value_faces"].append(vface)

    for where, vface, m in _walk_measured_ctx(b, "", None):
        for e in m.get("evidence") or []:
            r = records.get(e) if isinstance(e, str) else None
            if r is None:
                continue                                   # 写真でない根拠（plateau・osm・data/…）は対象外
            pb = r.get("building")
            if pb is None:
                add(e, "no_building", "棟が決まっていない写真", where, vface, r)
                continue
            if pb != bid:
                add(e, "other_building", f"索引では棟 {pb}", where, vface, r)
                continue
            pf = {f for f in ([r.get("face")] + list(r.get("faces") or [])) if f}
            if vface and not r.get("interior_views"):
                if not pf:
                    add(e, "no_face", "面が決まっていない写真", where, vface, r)
                elif vface not in pf:
                    add(e, "other_face", f"索引では面 {'・'.join(sorted(pf))}", where, vface, r)
            if r.get("confidence") == "low" and not r.get("confirmed_by"):
                add(e, "low_unconfirmed", "仕分けの確からしさ low で、職員の確認が無い", where, vface, r)
    return list(found.values())


def warn_evidence_sorting(b: dict, records: dict[str, dict] | None = None, root: Path | None = None) -> list[str]:
    """4. 根拠の門の警告（--strict でエラー）。写真 1 枚・問題 1 種で 1 件にまとめる。"""
    out = []
    for it in evidence_checks(b, records, root):
        w = it["used_at"]
        vf = f"（値の面 {'・'.join(it['value_faces'])}）" if it["value_faces"] else ""
        out.append(f"{w[0]}: measured の根拠の写真 {it['photo']} — {it['detail']}{vf}（{len(w)} か所）。"
                   "仕分けを直すか職員に確かめてもらう（recorder photos to-confirm）まで、この写真を measured の根拠にしない")
    return out


def warn_building(b: dict, photo_ids: set[str] | None = None, root: Path | None = None,
                  records: dict[str, dict] | None = None) -> list[str]:
    """警告の一覧（validate_building のエラーとは別。既存の棟を落とさない）。"""
    return (warn_tol_floor(b, ruler_ratios(root)) + warn_adopted(b)
            + warn_evidence_ids(b, photo_ids, root) + warn_evidence_sorting(b, records, root))


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
    if pt.get("type") == "composite" and pt.get("status") in STATUSES:
        # 組み立て部品のまとめの形：全体の status を 1 つの値として数える
        vals.append({"value": 0 if pt["status"] != "unknown" else None, "status": pt["status"]})
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


# --- 仮説（未確定の所に「仮に置いた」値。写真で矛盾したら外す） ------------------------------
# 書き方（design スクリプトでは hypothesis() で作る。JSON を手で書かない）：
#   {"value": …, "status": "estimated", "basis": "hypothesis",
#    "hypothesis": {"rule": "symmetry|same_block|footprint", "from": "<面 id か塊 id>", "note": "…"},
#    "evidence": [元の値の根拠], "method": "…"}
# 規則は 3 つだけ：
#   symmetry    対面・対称の面と同じ割付
#   same_block  同じ塊は同じ階高・同じ窓の形
#   footprint   柱割りは足元の辺の長さに収まる整数の列数
# 置いてよい所は面の割付・階高・窓の形まで（faces[].bays / windows / bands、floor_pitch・floor_count、levels、blocks の階高）。
# 部品（parts：階段・庇・箱…）と土台（footprint・ground_z）には置かない（見えない物を足さない）。
# 外す：drop_hypotheses()（値を unknown に戻し history に理由）。design スクリプトは HYPOTHESES_OFF に書いて再生成で反映
#       （recorder hypotheses --drop が JSON と design スクリプトの両方に書く）。
HYPOTHESIS_RULES = ("symmetry", "same_block", "footprint")
_HYP_ALLOWED_TOP = ("faces", "floor_pitch", "floor_count", "levels", "blocks")
_HYP_FACE_KEYS = ("bays", "windows", "bands")


def hypothesis(value, rule: str, from_: str, note: str, evidence=(), method: str = "", unit: str = "m",
               tol: float | None = None) -> dict:
    """仮説の値。rule は HYPOTHESIS_RULES、from_ は写した元（面 id か塊 id）、note は何をどう写したか。"""
    return {"value": value, "unit": unit, "tol": tol, "status": "estimated", "evidence": list(evidence),
            "method": method or f"仮説（{rule}）：{from_} から", "note": "",
            "basis": "hypothesis", "hypothesis": {"rule": rule, "from": from_, "note": note}}


def hypothesis_from(src: dict, rule: str, from_: str, note: str) -> dict:
    """既にある値 src（対の面の値など）をそのまま仮説として写す。src が分かっていない値なら unknown のまま返す。"""
    if not (is_value(src) and _known(src)):
        return Measurement(None, src.get("unit", "m") if isinstance(src, dict) else "m", None, "unknown", [],
                           f"仮説の元 {from_} の値が unknown なので写さない").to_json()
    return hypothesis(src["value"], rule, from_, note, src.get("evidence", []),
                      f"仮説（{rule}）：{from_} の値を写した。元の方法：{src.get('method', '')}", src.get("unit", "m"), src.get("tol"))


def is_hypothesis(d) -> bool:
    return is_value(d) and d.get("basis") == "hypothesis"


def iter_hypotheses(b: dict):
    """(場所, 面 id か None, 値) を全部返す。場所は 'faces[f3].bays.count' の形。"""
    def walk(obj, where, face):
        if is_value(obj):
            if is_hypothesis(obj):
                yield where, face, obj
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in COORD_KEYS:
                    continue
                yield from walk(v, f"{where}.{k}" if where else k, face)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                yield from walk(v, f"{where}[{i}]", face)

    for k, v in b.items():
        if k in _SUMMARY_SKIP:
            continue
        if k == "faces" and isinstance(v, list):
            for f in v:
                yield from walk({kk: vv for kk, vv in f.items() if kk not in ("id", "name")}, f"faces[{f.get('id')}]", f.get("id"))
        else:
            yield from walk(v, k, None)


def _basis_walk(obj, where: str):
    """basis か hypothesis の鍵を持つ測定値を (場所, 値) で全部返す（規則の検査用）。"""
    if isinstance(obj, dict):
        if "value" in obj and "status" in obj:
            if "basis" in obj or "hypothesis" in obj:
                yield where, obj
            return
        for k, v in obj.items():
            yield from _basis_walk(v, f"{where}.{k}" if where else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _basis_walk(v, f"{where}[{i}]")


def check_hypotheses(b: dict) -> list[str]:
    """仮説の決まり：basis は hypothesis だけ、rule は 3 つ、from は必須で実在の面か塊、置き場所は割付・階高・窓の形まで。"""
    errs: list[str] = []
    ids = {f.get("id") for f in b.get("faces") or []} | {blk.get("id") for blk in b.get("blocks") or []}
    for k, v in b.items():
        if k in _SUMMARY_SKIP:
            continue
        for where, m in _basis_walk(v, k):
            if m.get("basis") != "hypothesis":
                errs.append(f"{where}: basis は \"hypothesis\" だけ使えます（{m.get('basis')!r}）")
                continue
            h = m.get("hypothesis")
            if not isinstance(h, dict):
                errs.append(f"{where}: 仮説なのに hypothesis（rule・from・note）がありません")
                continue
            if h.get("rule") not in HYPOTHESIS_RULES:
                errs.append(f"{where}: 仮説の rule は {'/'.join(HYPOTHESIS_RULES)} のどれか（{h.get('rule')!r}）")
            if not h.get("from"):
                errs.append(f"{where}: 仮説の出所（hypothesis.from）がありません")
            elif h["from"] not in ids:
                errs.append(f"{where}: 仮説の出所 {h['from']} は面にも塊にもありません")
            if m.get("status") != "estimated" or m.get("value") is None:
                errs.append(f"{where}: 仮説は status estimated で value が要ります（外すなら unknown にして basis を消す）")
            top = where.split(".")[0].split("[")[0]
            if top not in _HYP_ALLOWED_TOP:
                errs.append(f"{where}: 仮説は面の割付・階高・窓の形まで（{top} には置けない。部品は仮説で作らない）")
            elif top == "faces":
                sect = where.split(".")[1].split("[")[0] if "." in where else ""
                if sect not in _HYP_FACE_KEYS:
                    errs.append(f"{where}: 面の仮説は bays・windows・bands だけ（{sect}）")
    return errs


def drop_hypotheses(b: dict, face: str | None = None, reason: str = "", at: str = "", by: str = "recorder hypotheses --drop") -> tuple[dict, list[str]]:
    """仮説の値を unknown に戻した写しと、戻した場所の一覧を返す（b は変えない）。face を渡すとその面だけ（棟全体の値は face=None の時だけ）。
    値が全部仮説だった窓・帯は項目ごと外す（unknown の窓を既定値で描かない）。reason があれば history に書く。"""
    import copy

    out = copy.deepcopy(b)
    dropped: list[str] = []
    for where, fid, m in list(iter_hypotheses(out)):
        if face is not None and fid != face:
            continue
        h, unit = m.get("hypothesis") or {}, m.get("unit", "m")
        m.clear()
        m.update(Measurement(None, unit, None, "unknown", [],
                             f"仮説を外した（{h.get('rule')}、{h.get('from')} から）" + (f"：{reason}" if reason else "")).to_json())
        dropped.append(where)
    removed = []
    for f in out.get("faces") or []:
        if face is not None and f.get("id") != face:
            continue
        for key in ("windows", "bands"):
            keep = []
            for i, it in enumerate(f.get(key) or []):
                vals = list(iter_values(it))
                was_hyp = any(w.startswith(f"faces[{f.get('id')}].{key}[{i}].") for w in dropped)
                if was_hyp and not any(_known(v) for v in vals):
                    removed.append(f"faces[{f.get('id')}].{key}[{i}]（{it.get('name', '')}）")
                    continue
                keep.append(it)
            if key in f:
                f[key] = keep
    if dropped and reason:
        what = f"仮説を外した（{face or '棟全体'}、{len(dropped)} 値）：{reason}"
        if removed:
            what += "。項目ごと外した：" + "、".join(removed)
        out.setdefault("history", []).append({"at": at, "by": by, "what": what})
    return out, dropped


def apply_hypotheses_off(b: dict, off: dict) -> dict:
    """design スクリプトの HYPOTHESES_OFF（{面 id か "*": {"at", "reason"}}）を当てる。再生成でも外した状態が残る。"""
    for key, d in (off or {}).items():
        b, _ = drop_hypotheses(b, None if key == "*" else key, d.get("reason", ""), d.get("at", ""),
                               d.get("by", "recorder hypotheses --drop"))
    return b
