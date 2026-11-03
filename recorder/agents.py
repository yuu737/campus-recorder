"""係（エージェント）の定義 `.claude/agents/*.md` を、API（Claude Agent SDK）で動かす実行役。

同じ定義ファイルを、人が Claude Code のサブエージェントとして起動するときも、ここから API で動かすときも使う。
キーは環境変数 `ANTHROPIC_API_KEY` だけ（リポジトリには書かない）。キーが無い間は dry-run（呼ばずに送る内容を出す）まで。
"""

from __future__ import annotations

import json
import os
import re
import time
import warnings
from datetime import datetime
from pathlib import Path

MODEL_MAP = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}
DEFAULT_DIR = ".claude/agents"
COMMON_NAME = "_共通"

# 1 回の係の呼び出しの上限（超えたら止めて記録する）
LIMITS = {
    "haiku": {"max_turns": 12, "max_budget_usd": 0.5},
    "sonnet": {"max_turns": 20, "max_budget_usd": 1.5},
    "opus": {"max_turns": 30, "max_budget_usd": 4.0},
}

# 工程の順（seizu はコード = recorder.run を直接呼ぶ。API は使わない）
PIPELINE_STEPS = ["satsuei", "shiwake", "kansatsu", "kazoe", "sokuryo", "sekkei", "seizu", "kenshu"]


class AgentError(RuntimeError):
    pass


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    meta: dict = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, m.group(2)


def load_agent_defs(dir: str | Path = DEFAULT_DIR) -> dict[str, dict]:
    """名前 -> {name, description, model, model_id, tools, system_prompt, path}。`_共通.md` は各 system prompt の先頭に付く。"""
    d = Path(dir)
    common_path = d / f"{COMMON_NAME}.md"
    common = common_path.read_text(encoding="utf-8").strip() if common_path.exists() else ""
    defs: dict[str, dict] = {}
    for p in sorted(d.glob("*.md")):
        if p.stem == COMMON_NAME:
            continue
        meta, body = _parse_frontmatter(p.read_text(encoding="utf-8"))
        name = meta.get("name") or p.stem
        model = meta.get("model", "opus").lower()
        if model not in MODEL_MAP:
            warnings.warn(f"{name}: model {model} は API の対応表に無いので opus に落とします（Fable は人の役）")
            model = "opus"
        tools = [t.strip() for t in meta.get("tools", "").split(",") if t.strip()]
        prompt = (common + "\n\n" if common else "") + body.strip()
        defs[name] = {"name": name, "description": meta.get("description", ""), "model": model,
                      "model_id": MODEL_MAP[model], "tools": tools, "system_prompt": prompt, "path": str(p)}
    return defs


def _outputs(cwd: Path, building_id: str | None, name: str) -> list[str]:
    if not building_id:
        return []
    d = Path(cwd) / "out" / building_id
    return sorted(str(p.relative_to(cwd)).replace("\\", "/") for p in d.glob(f"{name}_*.json")) if d.is_dir() else []


def _guess_building(task: str) -> str | None:
    m = re.search(r"out/([A-Za-z0-9_\-]+)/", task)
    return m.group(1) if m else None


def _invoke(spec: dict, task: str, cwd: Path, max_turns: int, max_budget_usd: float) -> dict:
    """SDK を実際に呼ぶ唯一の場所（試験ではここをモックする）。"""
    import asyncio

    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query

    opts = ClaudeAgentOptions(
        system_prompt=spec["system_prompt"], model=spec["model_id"], tools=spec["tools"], allowed_tools=spec["tools"],
        cwd=str(cwd), max_turns=max_turns, max_budget_usd=max_budget_usd, permission_mode="acceptEdits",
        setting_sources=[],
    )

    async def go() -> dict:
        text, res = "", None
        async for m in query(prompt=task, options=opts):
            if isinstance(m, AssistantMessage):
                t = "".join(b.text for b in m.content if isinstance(b, TextBlock))
                if t:
                    text = t
            elif isinstance(m, ResultMessage):
                res = m
        if res is None:
            raise AgentError("結果が返りませんでした")
        u = res.usage or {}
        return {"text": res.result or text, "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
                "cost_usd": res.total_cost_usd, "turns": res.num_turns, "is_error": res.is_error,
                "stop": res.terminal_reason or res.subtype}

    return asyncio.run(go())


def run_agent(name: str, task: str, *, cwd: str | Path = ".", dry_run: bool = False, agents_dir: str | Path | None = None,
              building_id: str | None = None) -> dict:
    cwd = Path(cwd)
    defs = load_agent_defs(agents_dir or cwd / DEFAULT_DIR)
    if name not in defs:
        raise AgentError(f"係 {name} は定義にありません。ある係: {', '.join(defs)}")
    spec = defs[name]
    lim = LIMITS[spec["model"]]
    if dry_run:
        return {"dry_run": True, "agent": name, "model": spec["model_id"], "tools": spec["tools"], "task": task,
                "max_turns": lim["max_turns"], "max_budget_usd": lim["max_budget_usd"], "system_prompt": spec["system_prompt"]}
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise AgentError("ANTHROPIC_API_KEY が環境変数にありません（リポジトリには書かない）。--dry-run なら呼ばずに内容だけ出せます")
    t0 = time.time()
    r = _invoke(spec, task, cwd, lim["max_turns"], lim["max_budget_usd"])
    bid = building_id or _guess_building(task)
    stop = str(r.get("stop") or "")
    return {"agent": name, "model": spec["model_id"], "seconds": round(time.time() - t0, 1), "text": r.get("text", ""),
            "outputs": _outputs(cwd, bid, name), "input_tokens": r.get("input_tokens"), "output_tokens": r.get("output_tokens"),
            "cost_usd": r.get("cost_usd"), "turns": r.get("turns"), "is_error": bool(r.get("is_error")),
            "stopped_by_limit": ("max" in stop) or ("budget" in stop), "stop": stop}


def _faces_note(cwd: Path, building_id: str) -> str:
    idx = Path(cwd) / "data/photos/index.json"
    if not idx.exists():
        return "索引なし"
    ps = [p for p in json.loads(idx.read_text(encoding="utf-8")).get("photos", []) if p.get("building") == building_id]
    faces = sorted({p.get("face") for p in ps if p.get("face")})
    return f"仕分け済み {len(ps)} 枚、面: {', '.join(faces) if faces else '未仕分け'}"


def step_task(step: str, building_id: str, photos_folder: str, cwd: Path) -> str:
    b, out = building_id, f"out/{building_id}"
    common = f"対象の棟は {b}（{_faces_note(cwd, b)}）。出力は {out}/ に書く。"
    return {
        "satsuei": f"{common} 写真フォルダ {photos_folder} を `photos import` で索引に取り込み、各写真が使えるか（ぶれ・GPS・足りない面）を短く返す。出力 {out}/satsuei_checks.json",
        "shiwake": f"{common} data/photos/index.json の写真を一覧画像で見て、{b} の面（building / face）を目視で確定する。方位は 180° ずれうる。出力 {out}/shiwake_faces.json",
        "kansatsu": f"{common} 仕分け済み写真（根拠: {out}/shiwake_faces.json）から、面ごとの観察票を書く。比率と個数だけで寸法は書かない。足元の辺は data/site/tempaku.json。出力 {out}/kansatsu_<面>.json",
        "kazoe": f"{common} 観察票（{out}/kansatsu_*.json）を根拠に、面ごとのスパン数・階数を複数の写真で突き合わせて確定する。出力 {out}/kazoe_<面>.json",
        "sokuryo": f"{common} 数え結果（{out}/kazoe_*.json）と物差し台帳 data/rulers.json を根拠に、`photos rectify` → `photos measure` で縮尺を選び m に換算する。出力 {out}/sokuryo_<面>.json",
        "sekkei": f"{common} 測量結果（{out}/sokuryo_*.json）と観察票・数え結果から棟 JSON data/buildings/{b}.json を書き、`recorder validate` を通す。根拠の無い寸法は unknown にして撮影依頼を返す。出力 {out}/sekkei_summary.json",
        "kenshu": f"{common} 棟 JSON data/buildings/{b}.json の模型と写真の重ね合わせ（`photos compare` / `photos pose --from-rectify` / `model build --cameras` / `photos overlay`）で検収し、直すのは JSON だけ。出力 {out}/kenshu_report.json",
    }[step]


def run_pipeline(building_id: str, photos_folder: str = "data/photos", *, steps: list[str] | None = None, dry_run: bool = False,
                 cwd: str | Path = ".", agents_dir: str | Path | None = None) -> dict:
    cwd = Path(cwd)
    steps = list(steps or PIPELINE_STEPS)
    bad = [s for s in steps if s not in PIPELINE_STEPS]
    if bad:
        raise AgentError(f"知らない工程: {bad}。ある工程: {PIPELINE_STEPS}")
    results: list[dict] = []
    t_all = time.time()
    stopped = False
    for s in steps:
        if s == "seizu":    # 製図はコード（API を呼ばない）
            rec = {"agent": "seizu", "model": "code", "task": f"recorder run data/buildings/{building_id}.json"}
            if dry_run:
                rec["dry_run"] = True
            else:
                from . import run as runner

                t = time.time()
                r = runner.run(cwd / "data/buildings" / f"{building_id}.json", cwd / "out" / building_id)
                rec.update(seconds=round(time.time() - t, 1), ok=bool(r.get("ok")),
                           outputs=[str(Path(r["out"]) / "run_report.json")], input_tokens=None, output_tokens=None, cost_usd=0.0)
        else:
            rec = run_agent(s, step_task(s, building_id, photos_folder, cwd), cwd=cwd, dry_run=dry_run,
                            agents_dir=agents_dir, building_id=building_id)
            if not dry_run and (rec.get("stopped_by_limit") or rec.get("is_error")):
                stopped = True
        results.append(rec)
        if stopped:
            break
    costs = [r.get("cost_usd") for r in results if r.get("cost_usd") is not None]
    summary = {"building": building_id, "dry_run": dry_run, "steps": results, "stopped_early": stopped,
               "seconds_total": round(time.time() - t_all, 1), "cost_usd_estimate": round(sum(costs), 4) if costs else None,
               "made_at": datetime.now().isoformat(timespec="seconds")}
    if not dry_run:
        out = cwd / "out" / building_id
        out.mkdir(parents=True, exist_ok=True)
        p = out / f"pipeline_{datetime.now():%Y%m%d_%H%M%S}.json"
        p.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        summary["record"] = str(p)
    return summary
