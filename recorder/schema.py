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
MEASUREMENT_KEYS_FACE = ("length",)
MEASUREMENT_KEYS_BAYS = ("count", "pitch", "pier_width", "end_margin")
MEASUREMENT_KEYS_LEVEL = ("z",)
MEASUREMENT_KEYS_WINDOW = ("z_bottom", "z_top", "width", "recess")
MEASUREMENT_KEYS_BAND = ("z_bottom", "z_top", "depth")
MEASUREMENT_KEYS_OPENING = ("z_top",)
MEASUREMENT_KEYS_MATERIAL = ("unit_w", "unit_h", "joint_w")


def _m(d: dict, key: str) -> Measurement:
    return Measurement.from_json(d.get(key))


def load_building(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_building(b: dict, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(b, f, ensure_ascii=False, indent=2)
        f.write("\n")


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
        if "length" in fc:
            chk(_m(fc, "length"), f"faces[{fid}].length")
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
