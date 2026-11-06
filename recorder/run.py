"""棟 1 つについて、判断なしで回せる工程を 1 コマンドにまとめる。

  validate → draw → model build → （poses があれば）photos overlay → run_report.json
既存のコマンドが呼ぶ関数を直接呼ぶ。落ちた工程は報告に理由を残し、後続の工程は止める。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from . import drawing, schema


def count_statuses(b: dict) -> dict:
    """棟 JSON の中の測定値（status と value を持つ物）を状態別に数える。"""
    c = {"measured": 0, "estimated": 0, "unknown": 0}

    def walk(o):
        if isinstance(o, dict):
            if "status" in o and "value" in o:
                s = o.get("status")
                if s in c:
                    c[s] += 1
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk({k: v for k, v in b.items() if k != "requests"})
    return c


def run(building_json: Path, out: Path | None = None, fast: bool = False, skip_model: bool = False,
        blender: str | None = None) -> dict:
    building_json = Path(building_json)
    b = schema.load_building(building_json)
    out = Path(out) if out else Path("out") / b["id"]
    out.mkdir(parents=True, exist_ok=True)
    t_all = time.time()
    steps: list[dict] = []
    report = {
        "building": b["id"], "source": str(building_json), "out": str(out), "fast": fast,
        "ok": False, "steps": steps, "validate_warnings": [],
        "counts": count_statuses(b),
        "requests_open": sum(1 for r in b.get("requests", []) if r.get("status", "open") == "open"),
        "requests_total": len(b.get("requests", [])),
    }

    def step(name: str, fn):
        t = time.time()
        s = {"name": name, "ok": False, "seconds": 0.0, "outputs": [], "error": ""}
        steps.append(s)
        try:
            res = fn(s)
            s["ok"] = res is not False
        except SystemExit as e:
            s["error"] = str(e.code) if e.code not in (None, 0) else "終了しました"
        except Exception as e:  # noqa: BLE001  工程の失敗は報告に残す
            s["error"] = f"{type(e).__name__}: {e}"
        s["seconds"] = round(time.time() - t, 2)
        return s["ok"]

    def do_validate(s):
        errs = schema.validate_building(b)
        s["errors"] = errs
        if errs:
            s["error"] = f"規約違反 {len(errs)} 件"
            return False
        return True

    def do_draw(s):
        for name, svg in drawing.all_drawings(b).items():
            p = out / f"{name}.svg"
            p.write_text(svg, encoding="utf-8")
            s["outputs"].append(str(p))

    # poses は出力先の poses/ を優先し、無ければ既定の out/<id>/poses/ を使う
    pdir = next((d for d in (out / "poses", Path("out") / b["id"] / "poses") if d.exists()), None)
    poses = sorted(pdir.glob("*_pose.json")) if pdir else []

    def do_model(s):
        from . import model

        model.build(building_json, out, blender, fast, poses or None)
        for n in ("persp_shaded.png", "sheet.png", f"{b['id']}.glb", f"{b['id']}.blend", "views.json"):
            if (out / n).exists():
                s["outputs"].append(str(out / n))
        sh = model.sheet(out, b)
        s["outputs"].append(str(sh))
        s["cameras"] = [str(p) for p in poses]

    def do_overlay(s):
        from . import pose

        bad = []
        for pj in poses:
            d = json.loads(pj.read_text(encoding="utf-8"))
            photo = Path(d.get("photo", ""))
            stem = photo.stem
            render = out / f"view_photo_{stem}.png"
            if not photo.exists() or not render.exists():
                bad.append(f"{stem}: 写真または描画が無い（{photo} / {render.name}）")
                continue
            s["outputs"].append(str(pose.overlay(photo, render, out / f"overlay_{stem}.png")))
        if bad:
            s["error"] = "; ".join(bad)
            return False

    ok = step("validate", do_validate)
    if ok:
        ok = step("draw", do_draw)
    if ok and not skip_model:
        ok = step("model", do_model)
        if ok and poses:
            ok = step("overlay", do_overlay)
    report["ok"] = ok
    # 進捗報告（out/<棟>/report.html）。段の状態は生成物の有無で決まるので、途中で止まっても出す。失敗しても run の合否は変えない
    try:
        from . import report as _report

        rp = _report.run(building_json, out / "report.html")
        report["progress_report"] = {"html": rp["html"], "stages": {k: v["status"] for k, v in rp["stages"].items()}}
    except Exception as e:  # noqa: BLE001
        report["progress_report"] = {"error": f"{type(e).__name__}: {e}"}
    report["seconds_total"] = round(time.time() - t_all, 2)
    (out / "run_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report
