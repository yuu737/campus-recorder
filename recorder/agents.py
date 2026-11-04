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

# 工程の順。seizu・seizu_final・web はコード（recorder.run / site.export_web を直接呼ぶ。API は使わない）
PIPELINE_STEPS = ["satsuei", "shiwake", "kansatsu", "kazoe", "sokuryo", "sekkei", "seizu", "kenshu", "seizu_final", "web"]
CODE_STEPS = {"seizu", "seizu_final", "web"}
STEP_AGENT = {"seizu_final": "seizu", "web": "seizu"}     # 工程名 → 係の定義（記録の agent 名）


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


def step_agent(step: str) -> str:
    return STEP_AGENT.get(step, step)


def step_command(step: str, building_id: str) -> str | None:
    """コードの工程が実行するコマンド（サブエージェントの試走では指揮役が同じ物を打つ）。"""
    j = f"data/buildings/{building_id}.json"
    return {"seizu": f"recorder run {j} --fast",
            "seizu_final": f"recorder run {j}",
            "web": "recorder site export-web --with-coverage"}.get(step)


def step_task(step: str, building_id: str, photos_folder: str | None = None, cwd: Path | None = None) -> str:
    """工程ごとの依頼文。対象・入力・出力・守る規約だけを書き、やり方は係の定義（system）と手順書に任せる。
    依頼文は (工程, 棟 id, 写真フォルダ) だけで決まる（索引の中身などの状態を入れない）。
    サブエージェントの試走と API（run_pipeline）で 1 文字も違わないようにするため。cwd は互換のための引数で使わない。"""
    b, out, j = building_id, f"out/{building_id}", f"data/buildings/{building_id}.json"
    design = f"scripts/design_{b}.py"
    if step in CODE_STEPS:
        return f"コード（判断なし）：{step_command(step, b)}。生成物を手で直さない。失敗したらログの該当行を報告する。"
    head = (f"対象の棟は {b}（敷地 data/site/tempaku.json の id）。やり方は係の定義と docs/手順_エージェント.md の該当節に従う。"
            f"道具は `.venv/Scripts/python -I -X utf8 -m recorder …`。出力は JSON（made_by・made_at 付き）と短い Markdown。"
            "根拠の無い寸法は書かない（measured/estimated/unknown）。分からない事は questions・requests に。")
    if photos_folder:
        src = (f"写真フォルダ {photos_folder} を `recorder photos import {photos_folder} --period during` で索引に取り込む"
               "（取り込み済みの写真は重複しない）")
    else:
        src = "写真は索引 data/photos/index.json に取り込み済みの物を使う（新しい取り込みはしない）"
    tasks = {
        "satsuei": f"{head} {src}。期間前の写真（D:\\名城大学\\写真資料_*）は取り込まない・写さない。"
                   f"{b} に当たりそうな写真の checks・candidates を見て、使えるか（GPS・ぶれ・解像度）と足りない面・角度を返す。"
                   f"出力 {out}/satsuei_checks.json（写真 id ごとの usable・problems を 1 つにまとめる）と職員向け 3 行。",
        "shiwake": f"{head} 索引の写真のうち building が {b} の物と、building が null で candidates に {b} がある物を目視で面に分ける。"
                   f"面 id は足元の辺番号 f0〜（tempaku.json）。period と台帳の building（source「台帳(期間前)」）は参考で、食い違いは disagreement に。"
                   f"出力 out/photos/shiwake_{b}.json。索引へは face・faces だけを python で書き戻す（他の項目・他の棟は触らない）。",
        "kansatsu": f"{head} 入力：out/photos/shiwake_{b}.json と索引の face、足元 data/site/tempaku.json、屋上 out/site/aerial_z18.png、"
                    f"あれば {j}。面ごとの観察票（比率と個数だけ。寸法は書かない）。出力 {out}/kansatsu_<面id>.json と {out}/kansatsu.md。",
        "kazoe": f"{head} 入力：{out}/kansatsu_*.json とその面の写真。面ごとのスパン数・階数を 2 枚以上で突き合わせて確定する"
                 f"（両端が入らない面は目印で 2 枚をつなぐ）。決まらない面は unknown と requests。出力 {out}/kazoe_<面id>.json。",
        "sokuryo": f"{head} 入力：{out}/kazoe_*.json、物差し台帳 data/rulers.json、足元（PLATEAU）data/site/tempaku.json。"
                   "`photos rectify`（複数面は --roi）→ `photos measure`（片端ずつの面は `photos stitch`）で階高・柱間隔を m に。"
                   f"必ず 2 つ目の物差しで検算し差 % を method に。出力 {out}/sokuryo_<面id>.json（正面化画像は out/rectified/）。",
        "sekkei": f"{head} 入力：{out}/kansatsu_*・kazoe_*・sokuryo_*.json と out/photos/shiwake_{b}.json。"
                  f"{design} を新しく作り（形は既存の scripts/design_*.py、土台は recorder.skeleton.from_site_cli(\"{b}\")＝"
                  f"`recorder new {b} --from-site` と同じ物）、実行して {j} を出す。JSON を手で書かない。"
                  f"部品は手順書「生成器の教訓」の語彙（見本 tests/fixtures/parts_building.json）、屋上は `recorder photos roof {j}`。"
                  f"`recorder validate {j}` → `recorder run {j} --fast` が通るまで。寸法の無い物は塊にせず requests。"
                  f"出力 {out}/sekkei_summary.json（measured/estimated/unknown の件数と requests）。",
        "kenshu": f"{head} 入力：{j}、{design}、{out}/ の生成物、data/photos/registry.json。"
                  f"未登録の写真は `photos compare` → `photos register` で登録し、`recorder recheck {j} --fast` → "
                  f"`recorder photoproj {j} --fast` → 検収 C（屋上は航空写真）でずれと意味の不足を見る。"
                  f"直すのは {design} だけ（再実行 → validate → recheck）。2 回で収束しなければ止めて撮影依頼。"
                  f"最後に `recorder coverage {j}`。出力 {out}/kenshu_<写真id>.json と {out}/kenshu_summary.json（面ごとの合否・直した値・requests）。",
    }
    return tasks[step]


def _defs_sha(spec: dict) -> str:
    import hashlib
    return hashlib.sha256(spec["system_prompt"].encode("utf-8")).hexdigest()[:16]


def build_tasks(building_id: str, photos_folder: str | None = None, *, cwd: str | Path = ".",
                agents_dir: str | Path | None = None) -> dict:
    """各工程の依頼文（system に入る係の定義のパス＋task）。指揮役がサブエージェントに同じ文をそのまま渡すための物。"""
    cwd = Path(cwd)
    defs = load_agent_defs(agents_dir or cwd / DEFAULT_DIR)
    steps = []
    for s in PIPELINE_STEPS:
        name = step_agent(s)
        spec = defs.get(name)
        code = s in CODE_STEPS
        steps.append({
            "step": s, "agent": name, "kind": "code" if code else "agent",
            "model": "code" if code else (spec["model"] if spec else None),
            "model_id": None if code or not spec else spec["model_id"],
            "tools": [] if code or not spec else spec["tools"],
            "system_files": [] if code else [f"{DEFAULT_DIR}/{COMMON_NAME}.md", f"{DEFAULT_DIR}/{name}.md"],
            "system_sha256_16": None if code or not spec else _defs_sha(spec),
            "command": step_command(s, building_id),
            "task": step_task(s, building_id, photos_folder),
        })
    return {"building": building_id, "photos": photos_folder, "steps": steps,
            "note": "task は API（recorder agent pipeline）と同じ文。サブエージェントには task をそのまま渡す。"
                    "system は API では _共通.md＋係の定義、Claude Code のサブエージェントでは係の定義（冒頭で _共通.md を守るよう指す）",
            "made_at": datetime.now().isoformat(timespec="seconds")}


def tasks_markdown(doc: dict) -> str:
    lines = [f"# 依頼文 {doc['building']}（写真 {doc['photos'] or '索引の既存'}）", "", doc["note"], ""]
    for i, s in enumerate(doc["steps"], 1):
        lines.append(f"## {i}. {s['step']}（{s['agent']}、{s['model_id'] or s['model']}）")
        if s["system_files"]:
            lines.append(f"- system: {' ＋ '.join(s['system_files'])}（sha256 {s['system_sha256_16']}）")
        if s["command"]:
            lines.append(f"- command: `{s['command']}`")
        lines += ["", "```text", s["task"], "```", ""]
    return "\n".join(lines)


def write_tasks(building_id: str, photos_folder: str | None = None, *, cwd: str | Path = ".") -> dict:
    cwd = Path(cwd)
    doc = build_tasks(building_id, photos_folder, cwd=cwd)
    out = cwd / "out" / building_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "agent_tasks.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "agent_tasks.md").write_text(tasks_markdown(doc), encoding="utf-8")
    return {"json": str(out / "agent_tasks.json"), "md": str(out / "agent_tasks.md"), "steps": len(doc["steps"])}


def _run_code_step(step: str, building_id: str, cwd: Path) -> dict:
    rec = {"agent": step_agent(step), "step": step, "model": "code", "task": step_command(step, building_id),
           "input_tokens": None, "output_tokens": None, "cost_usd": 0.0}
    t = time.time()
    if step == "web":
        from . import site

        r = site.export_web("tempaku", with_coverage=True)
        rec.update(ok=True, outputs=[str(r["path"])])
    else:
        from . import run as runner

        j = cwd / "data/buildings" / f"{building_id}.json"
        r = runner.run(j, cwd / "out" / building_id, fast=True) if step == "seizu" else runner.run(j, cwd / "out" / building_id)
        rec.update(ok=bool(r.get("ok")), outputs=[str(Path(r["out"]) / "run_report.json")])
    rec["seconds"] = round(time.time() - t, 1)
    return rec


def run_pipeline(building_id: str, photos_folder: str | None = None, *, steps: list[str] | None = None, dry_run: bool = False,
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
        if s in CODE_STEPS:
            if dry_run:
                rec = {"agent": step_agent(s), "step": s, "model": "code", "task": step_command(s, building_id), "dry_run": True}
            else:
                rec = _run_code_step(s, building_id, cwd)
                if not rec.get("ok"):
                    stopped = True
        else:
            rec = run_agent(s, step_task(s, building_id, photos_folder), cwd=cwd, dry_run=dry_run,
                            agents_dir=agents_dir, building_id=building_id)
            rec["step"] = s
            if not dry_run and (rec.get("stopped_by_limit") or rec.get("is_error")):
                stopped = True
        results.append(rec)
        if stopped:
            break
    costs = [r.get("cost_usd") for r in results if r.get("cost_usd") is not None]
    summary = {"building": building_id, "runner": "api", "dry_run": dry_run, "steps": results, "stopped_early": stopped,
               "seconds_total": round(time.time() - t_all, 1), "cost_usd_estimate": round(sum(costs), 4) if costs else None,
               "made_at": datetime.now().isoformat(timespec="seconds")}
    if not dry_run:
        out = cwd / "out" / building_id
        out.mkdir(parents=True, exist_ok=True)
        p = out / f"pipeline_{datetime.now():%Y%m%d_%H%M%S}.json"
        p.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        summary["record"] = str(p)
    return summary


def log_step(building_id: str, step: str, *, model: str = "opus", seconds: float | None = None, tokens: int | None = None,
             note: str = "", record: str | Path | None = None, new: bool = False, cwd: str | Path = ".") -> dict:
    """サブエージェントの試走の 1 工程を、run_pipeline と同じ形の記録（out/<id>/pipeline_<日時>.json、runner "subagent"）に追記する。
    record を省くと、同じ棟の runner "subagent" の最新の記録に足す（無いか new なら新しく作る）。"""
    cwd = Path(cwd)
    if step not in PIPELINE_STEPS:
        raise AgentError(f"知らない工程: {step}。ある工程: {PIPELINE_STEPS}")
    out = cwd / "out" / building_id
    out.mkdir(parents=True, exist_ok=True)
    path = Path(record) if record else None
    if path is None and not new:
        for p in sorted(out.glob("pipeline_*.json"), reverse=True):
            try:
                if json.loads(p.read_text(encoding="utf-8")).get("runner") == "subagent":
                    path = p
                    break
            except (OSError, ValueError):
                continue
    if path is None:
        stamp, i = f"{datetime.now():%Y%m%d_%H%M%S}", 1
        path = out / f"pipeline_{stamp}.json"
        while path.exists():
            path, i = out / f"pipeline_{stamp}_{i}.json", i + 1
    if not path.exists():
        doc = {"building": building_id, "runner": "subagent", "dry_run": False, "steps": [], "stopped_early": False,
               "seconds_total": 0.0, "cost_usd_estimate": None, "made_at": datetime.now().isoformat(timespec="seconds")}
    else:
        doc = json.loads(path.read_text(encoding="utf-8"))
    m = model.lower()
    agent = step_agent(step)
    rec = {"agent": agent, "step": step, "model": MODEL_MAP.get(m, m), "seconds": seconds, "tokens": tokens,
           "input_tokens": None, "output_tokens": None, "cost_usd": None,
           "outputs": _outputs(cwd, building_id, agent), "note": note,
           "logged_at": datetime.now().isoformat(timespec="seconds")}
    doc["steps"].append(rec)
    doc["seconds_total"] = round(sum(s.get("seconds") or 0 for s in doc["steps"]), 1)
    doc["tokens_total"] = sum(s.get("tokens") or 0 for s in doc["steps"])
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"record": str(path), "steps": len(doc["steps"]), "added": rec}
