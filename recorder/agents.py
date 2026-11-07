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
from datetime import datetime, timedelta
from pathlib import Path

MODEL_MAP = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5",
             "fable": "claude-fable-5-1"}
DEFAULT_DIR = ".claude/agents"
REPORTS_DIR = "data/reports"     # 係の報告 JSON と実行記録の置き場（git に入る。画像は .gitignore で除外）
COMMON_NAME = "_共通"

# 1 回の係の呼び出しの上限（超えたら止めて記録する）
LIMITS = {
    "haiku": {"max_turns": 12, "max_budget_usd": 0.5},
    "sonnet": {"max_turns": 20, "max_budget_usd": 1.5},
    "opus": {"max_turns": 30, "max_budget_usd": 4.0},
    "fable": {"max_turns": 20, "max_budget_usd": 6.0},      # 進行役。事件が起きた時だけ呼ぶ
}

# 工程の順。seizu・seizu_final・web はコード（recorder.run / site.export_web を直接呼ぶ。API は使わない）
# mejirushi（目印係）は模型ができた後（seizu の後）に、写真の視点を目印の対応で解いて登録する。検収係はその登録を使う
PIPELINE_STEPS = ["satsuei", "shiwake", "kansatsu", "kazoe", "sokuryo", "sekkei", "seizu", "mejirushi", "kenshu",
                  "seizu_final", "web"]
CODE_STEPS = {"seizu", "seizu_final", "web"}
DEFAULT_MAX_USD = 20.0   # 棟ごとの費用の上限の既定（USD。CLI の --max-usd と職員の画面の既定。0 か None で上限なし）
STEP_AGENT = {"seizu_final": "seizu", "web": "seizu"}     # 工程名 → 係の定義（記録の agent 名）


class AgentError(RuntimeError):
    pass


# ---- どのキャンパスか（11/8、F2）：依頼文に敷地 JSON の道と範囲の棟を実行時に差し込む ----
# 既定：環境変数 RECORDER_CAMPUS（recorder serve は画面で選んだキャンパスをここに入れる）→
# data/site/ の敷地 JSON が 1 つならそれ（複数なら default_campus.txt。site.default_campus）。決まらなければ None。
CAMPUS_ENV = "RECORDER_CAMPUS"


def campus_slug(cwd: str | Path | None = None) -> str | None:
    """今のキャンパスの slug（敷地 JSON の名前）。決まらなければ None（依頼文は「決まっていない」と書く）。"""
    v = (os.environ.get(CAMPUS_ENV) or "").strip()
    if v:
        return v
    try:
        from . import site
        d = Path(cwd) / "data" / "site" if cwd is not None else None
        return site.default_campus(d)
    except Exception:  # noqa: BLE001  敷地 JSON が無い・複数で既定なし
        return None


def campus_brief(campus: str | None = None, cwd: str | Path | None = None) -> dict:
    """依頼文に入れる敷地の情報 {"campus", "path", "targets", "name"}。敷地 JSON の scope.targets を実行時に読む。"""
    slug = campus or campus_slug(cwd)
    if not slug:
        return {"campus": None, "path": None, "targets": [], "name": None}
    base = Path(cwd) if cwd is not None else Path(".")
    rel = f"data/site/{slug}.json"
    doc = _jload(base / rel) or {}
    targets = [str(t) for t in ((doc.get("scope") or {}).get("targets") or [])]
    name = " ".join(x for x in (doc.get("university"), doc.get("campus_name")) if x) or None
    return {"campus": slug, "path": rel, "targets": targets, "name": name}


def _site_ref(brief: dict) -> str:
    if not brief.get("path"):
        return ("敷地 JSON（data/site/<campus>.json）がまだ決まっていない。`recorder campus init` で作るか、"
                f"複数あるなら環境変数 {CAMPUS_ENV} か data/site/default_campus.txt で指す。決まるまで敷地を推測しない")
    t = "・".join(brief["targets"]) if brief["targets"] else "未設定（scope.targets が空。進行役の立ち上げ事件）"
    return f"敷地 JSON {brief['path']}（キャンパス {brief['campus']}、今回の範囲 scope.targets：{t}）"


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
            warnings.warn(f"{name}: model {model} は API の対応表に無いので opus に落とします")
            model = "opus"
        tools = [t.strip() for t in meta.get("tools", "").split(",") if t.strip()]
        prompt = (common + "\n\n" if common else "") + body.strip()
        defs[name] = {"name": name, "description": meta.get("description", ""), "model": model,
                      "model_id": MODEL_MAP[model], "tools": tools, "system_prompt": prompt, "path": str(p)}
    return defs


def _outputs(cwd: Path, building_id: str | None, name: str) -> list[str]:
    if not building_id:
        return []
    cwd = Path(cwd)
    pats = [f"{name}_*.json"] + ([f"{name}.md"] if name == "kansatsu" else []) + (["mejirushi/picks_*.json"] if name == "mejirushi" else [])
    found: set[str] = set()
    for d in (cwd / REPORTS_DIR / building_id, cwd / "out" / building_id):   # 係の報告は data/reports/、古い物と撮影係の物は out/
        if d.is_dir():
            found |= {str(p.relative_to(cwd)).replace("\\", "/") for pat in pats for p in d.glob(pat) if not p.name.startswith("pipeline_")}
    return sorted(found)


def _guess_building(task: str) -> str | None:
    m = re.search(r"data/(?:reports|buildings)/([A-Za-z0-9_\-]+)[/.]", task) or re.search(r"out/(?!photos/|site/)([A-Za-z0-9_\-]+)/", task)
    return m.group(1) if m else None


TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
_MODEL_USAGE_KEYS = {"input_tokens": "inputTokens", "output_tokens": "outputTokens",
                     "cache_creation_input_tokens": "cacheCreationInputTokens", "cache_read_input_tokens": "cacheReadInputTokens"}
PRICES_ENV = "RECORDER_AGENT_PRICES"     # 単価（USD / 100 万トークン）の JSON。例 {"opus": {"input": 5, "output": 25}, "*": {...}}


def _usage_tokens(usage: dict | None, model_usage: dict | None) -> tuple[dict, str | None]:
    """SDK の ResultMessage からトークン数を取る。usage（集計）→ 無ければ model_usage（モデルごと、camelCase）の合計。
    どちらにも無い数は None にし、理由を返す。"""
    usage = usage if isinstance(usage, dict) else {}
    mus = [m for m in (model_usage or {}).values() if isinstance(m, dict)]
    out: dict = {}
    missing = []
    for k in TOKEN_KEYS:
        v = usage.get(k)
        if v is None and any(_MODEL_USAGE_KEYS[k] in m for m in mus):
            v = sum(int(m.get(_MODEL_USAGE_KEYS[k]) or 0) for m in mus)
        out[k] = int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        if out[k] is None and k in ("input_tokens", "output_tokens"):
            missing.append(k)
    note = f"SDK の結果（usage・model_usage）に {'・'.join(missing)} が無いので null" if missing else None
    return out, note


class InvokeError(AgentError):
    """SDK の呼び出しが途中で落ちた。session_id（分かれば）を持つので、同じ会話の続きから再開できる。
    rate_limited は 429・使用量の上限・混雑（529）の時 True（指数で待ってやり直す対象）。"""

    def __init__(self, msg: str, *, session_id: str | None = None, rate_limited: bool = False, resets_at=None):
        super().__init__(msg)
        self.session_id = session_id
        self.rate_limited = rate_limited
        self.resets_at = resets_at


_RATE_RE = re.compile(r"\b429\b|\b529\b|rate[_ ]?limit|overloaded|usage limit|5-hour limit|hit your .*limit", re.I)


def is_rate_limit(err) -> bool:
    """例外や文が 429・使用量の上限・混雑か（指数で待ってやり直す対象）。"""
    if isinstance(err, InvokeError):
        return err.rate_limited
    return bool(_RATE_RE.search(str(err)))


def _invoke(spec: dict, task: str, cwd: Path, max_turns: int, max_budget_usd: float, resume: str | None = None) -> dict:
    """SDK を実際に呼ぶ唯一の場所（試験ではここをモックする）。resume に前の session_id を渡すと、その会話の続きから。
    途中で落ちたら InvokeError（session_id と 429 かどうか付き）を投げる。"""
    import asyncio

    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query

    kw = {"resume": resume} if resume else {}
    opts = ClaudeAgentOptions(
        system_prompt=spec["system_prompt"], model=spec["model_id"], tools=spec["tools"], allowed_tools=spec["tools"],
        cwd=str(cwd), max_turns=max_turns, max_budget_usd=max_budget_usd, permission_mode="acceptEdits",
        setting_sources=[], **kw,
    )
    seen: dict = {"session_id": resume, "rejected": None}

    async def go() -> dict:
        text, res = "", None
        async for m in query(prompt=task, options=opts):
            sid = getattr(m, "session_id", None)
            if not sid and isinstance(getattr(m, "data", None), dict):      # SystemMessage（init）の data に入る
                sid = m.data.get("session_id")
            if sid:
                seen["session_id"] = sid
            info = getattr(m, "rate_limit_info", None)
            if info is not None and str(getattr(info, "status", "")).endswith("rejected"):
                seen["rejected"] = getattr(info, "resets_at", None) or True
            if isinstance(m, AssistantMessage):
                t = "".join(b.text for b in m.content if isinstance(b, TextBlock))
                if t:
                    text = t
            elif isinstance(m, ResultMessage):
                res = m
        if res is None:
            raise InvokeError("結果が返りませんでした", session_id=seen["session_id"], rate_limited=bool(seen["rejected"]),
                              resets_at=seen["rejected"] if seen["rejected"] is not True else None)
        toks, note = _usage_tokens(res.usage, getattr(res, "model_usage", None))
        status = getattr(res, "api_error_status", None)
        return {"text": res.result or text, **toks, "token_note": note,
                "cost_usd": res.total_cost_usd, "turns": res.num_turns, "is_error": res.is_error,
                "stop": res.terminal_reason or res.subtype, "session_id": res.session_id or seen["session_id"],
                "api_error_status": status, "rate_limited": status in (429, 529) or bool(seen["rejected"])}

    try:
        return asyncio.run(go())
    except InvokeError:
        raise
    except Exception as e:  # noqa: BLE001  落ちた理由と会話の id を持って上へ
        raise InvokeError(f"{type(e).__name__}: {e}", session_id=seen["session_id"],
                          rate_limited=is_rate_limit(e) or bool(seen["rejected"])) from e


def load_prices(prices: dict | None = None) -> dict | None:
    """単価表（USD / 100 万トークン）。引数が優先、無ければ環境変数 RECORDER_AGENT_PRICES（JSON）。どちらも無ければ None。"""
    if prices is not None:
        return prices
    raw = os.environ.get(PRICES_ENV)
    if not raw:
        return None
    try:
        v = json.loads(raw)
    except ValueError as e:
        warnings.warn(f"{PRICES_ENV} が JSON として読めないので費用の見積もりは null にします: {e}")
        return None
    return v if isinstance(v, dict) else None


def estimate_cost(model: str | None, model_id: str | None, tokens: dict, prices: dict | None) -> tuple[float | None, str | None]:
    """トークン数 × 単価で費用を見積もる。単価表は {係のモデル名 or モデル id or "*": {"input","output"[,"cache_write","cache_read"]}}
    （USD / 100 万トークン）。単価・トークンが無い時は (None, 理由)。cache の単価が無ければ input の 1.25 倍（書き込み）・0.1 倍（読み込み）。"""
    if not prices:
        return None, f"単価が渡されていない（引数 prices か環境変数 {PRICES_ENV}）ので費用の見積もりは null"
    pr = prices.get(model or "") or prices.get(model_id or "") or prices.get("*")
    if not isinstance(pr, dict) or pr.get("input") is None or pr.get("output") is None:
        return None, f"単価表に {model or model_id} の input・output が無いので費用の見積もりは null"
    tin, tout = tokens.get("input_tokens"), tokens.get("output_tokens")
    if tin is None or tout is None:
        return None, "トークン数が取れないので費用の見積もりは null"
    pin = float(pr["input"])
    cw = float(pr["cache_write"]) if pr.get("cache_write") is not None else pin * 1.25
    cr = float(pr["cache_read"]) if pr.get("cache_read") is not None else pin * 0.1
    usd = (tin * pin + tout * float(pr["output"]) + (tokens.get("cache_creation_input_tokens") or 0) * cw
           + (tokens.get("cache_read_input_tokens") or 0) * cr) / 1e6
    return round(usd, 6), None


def summary_line(text: str | None, limit: int = 120) -> str:
    """結果の要約 1 行（係の返事の最初の空でない行。長ければ切る）。"""
    for ln in (text or "").splitlines():
        ln = ln.strip().lstrip("#").strip()
        if ln:
            return ln if len(ln) <= limit else ln[: limit - 1] + "…"
    return ""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


RETRY_MAX = 3            # 429・使用量の上限・混雑のやり直しの回数（これを超えたら止めて記録。resume で続ける）
RETRY_BASE_S = 60.0      # 待ちは 60・120・240 秒（指数）
_sleep = time.sleep      # 試験ではモックする
RESUME_PROMPT = ("前の呼び出しは「{why}」で途中で止まりました。同じ依頼の続きから進めてください。"
                 "済んだ作業はやり直さず、出力のファイルを確かめてから足りない所だけ。依頼（もう一度）：{task}")


def _sum_or_none(vals) -> float | int | None:
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def run_agent(name: str, task: str, *, cwd: str | Path = ".", dry_run: bool = False, agents_dir: str | Path | None = None,
              building_id: str | None = None, prices: dict | None = None, resume: str | None = None,
              resume_why: str | None = None, max_budget_usd: float | None = None) -> dict:
    """係を 1 回呼ぶ。429・使用量の上限・混雑は 60・120・240 秒待って同じ会話（session）の続きからやり直す（RETRY_MAX 回まで）。
    resume に前の session_id を渡すと、その会話の続きから始める（resume_why は止まった理由。続きの依頼文に入る）。
    max_budget_usd は 1 回の上限（棟の費用の上限の残りで小さくする時）。記録には呼び出しごとの使用量（calls）と、その合計が入る。"""
    cwd = Path(cwd)
    defs = load_agent_defs(agents_dir or cwd / DEFAULT_DIR)
    if name not in defs:
        raise AgentError(f"係 {name} は定義にありません。ある係: {', '.join(defs)}")
    spec = defs[name]
    lim = dict(LIMITS[spec["model"]])
    if max_budget_usd is not None:
        lim["max_budget_usd"] = round(min(lim["max_budget_usd"], max_budget_usd), 4)
    if dry_run:
        return {"dry_run": True, "agent": name, "model": spec["model_id"], "tools": spec["tools"], "task": task,
                "max_turns": lim["max_turns"], "max_budget_usd": lim["max_budget_usd"], "system_prompt": spec["system_prompt"],
                "resume": resume}
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise AgentError("ANTHROPIC_API_KEY が環境変数にありません（リポジトリには書かない）。--dry-run なら呼ばずに内容だけ出せます")
    started, t0 = _now(), time.time()
    prc = load_prices(prices)
    sid, why = resume, resume_why
    calls: list[dict] = []
    r: dict | None = None
    err: str | None = None
    rate_limited = False
    for i in range(RETRY_MAX + 1):
        prompt = RESUME_PROMPT.format(why=why or "途中で止まった", task=task) if sid else task
        at = _now()
        try:
            r = _invoke(spec, prompt, cwd, lim["max_turns"], lim["max_budget_usd"], **({"resume": sid} if sid else {}))
        except Exception as e:  # noqa: BLE001  落ちても記録を残す（resume で続ける）
            sid = getattr(e, "session_id", None) or sid
            rate_limited, err, r = is_rate_limit(e), f"{type(e).__name__}: {e}", None
            calls.append({"at": at, "resume": prompt is not task, "session_id": sid, "error": err, "rate_limited": rate_limited,
                          **{k: None for k in TOKEN_KEYS}, "cost_usd": None, "cost_usd_estimate": None,
                          "note": "落ちた呼び出しは SDK が使用量を返さないので null"})
        else:
            sid = r.get("session_id") or sid
            toks = {k: r.get(k) for k in TOKEN_KEYS}
            est, _n = estimate_cost(spec["model"], spec["model_id"], toks, prc)
            rate_limited = bool(r.get("rate_limited")) and bool(r.get("is_error"))
            err = f"API {r.get('api_error_status')}" if rate_limited else None
            calls.append({"at": at, "resume": prompt is not task, "session_id": sid, "error": err, "rate_limited": rate_limited,
                          **toks, "cost_usd": r.get("cost_usd"), "cost_usd_estimate": est})
        if not rate_limited or i == RETRY_MAX:
            break
        wait = RETRY_BASE_S * (2 ** i)
        calls[-1]["waited_s"] = wait
        why = f"429・使用量の上限（{err}）。{wait:.0f} 秒待った"
        _sleep(wait)
    seconds, ended = round(time.time() - t0, 1), _now()
    bid = building_id or _guess_building(task)
    r = r or {}
    stop = "rate_limit" if rate_limited else ("crash" if err else str(r.get("stop") or ""))
    toks = {k: _sum_or_none(c.get(k) for c in calls) for k in TOKEN_KEYS}
    notes = []
    if r.get("token_note"):
        notes.append(r["token_note"])
    elif toks["input_tokens"] is None or toks["output_tokens"] is None:
        notes.append("SDK の返り値にトークン数が無いので null")
    est, cost_note = estimate_cost(spec["model"], spec["model_id"], toks, prc)
    if cost_note:
        notes.append(cost_note)
    if len(calls) > 1:
        notes.append(f"呼び出し {len(calls)} 回（429・上限のやり直しを含む）の使用量の合計")
    if err:
        notes.append(f"止まった：{err}" + (f"（{RETRY_MAX} 回やり直しても同じ。recorder agent resume で続ける）" if rate_limited else ""))
    return {"agent": name, "model": spec["model_id"], "started_at": started, "ended_at": ended, "seconds": seconds,
            "text": r.get("text", ""), "summary": summary_line(r.get("text")),
            "outputs": _outputs(cwd, bid, name), **toks,
            "cost_usd": _sum_or_none(c.get("cost_usd") for c in calls), "cost_usd_estimate": est, "turns": r.get("turns"),
            "is_error": bool(r.get("is_error")) or bool(err),
            "stopped_by_limit": ("max" in stop) or ("budget" in stop), "stop": stop, "session_id": sid,
            "rate_limited": rate_limited, "error": err, "calls": calls, "max_budget_usd": lim["max_budget_usd"],
            "note": "。".join(notes)}


def step_agent(step: str) -> str:
    return STEP_AGENT.get(step, step)


def step_command(step: str, building_id: str) -> str | None:
    """コードの工程が実行するコマンド（サブエージェントの試走では指揮役が同じ物を打つ）。"""
    j = f"data/buildings/{building_id}.json"
    return {"seizu": f"recorder run {j} --fast",
            "seizu_final": f"recorder run {j}",
            "web": "recorder site export-web --with-coverage"}.get(step)


def step_task(step: str, building_id: str, photos_folder: str | None = None, cwd: Path | None = None,
              campus: str | None = None) -> str:
    """工程ごとの依頼文。対象・入力・出力・守る規約だけを書き、やり方は係の定義（system）と手順書に任せる。
    依頼文は (工程, 棟 id, 写真フォルダ, キャンパス) だけで決まる（索引の中身などの状態を入れない。
    キャンパスからは敷地 JSON の道と scope.targets だけを読む）。サブエージェントの試走と API（run_pipeline）で
    1 文字も違わないようにするため。campus を省くと campus_slug()（環境変数 RECORDER_CAMPUS → data/site/ の既定）。
    cwd は敷地 JSON を読む場所（省けば今の場所）。"""
    b, out, j = building_id, f"out/{building_id}", f"data/buildings/{building_id}.json"
    brief = campus_brief(campus, cwd)
    site_json = brief["path"] or "data/site/<campus>.json"
    rep = f"{REPORTS_DIR}/{building_id}"        # 係の報告 JSON（git に入る）。画像と道具の出力は out/
    design = f"scripts/design_{b}.py"
    if step in CODE_STEPS:
        return f"コード（判断なし）：{step_command(step, b)}。生成物を手で直さない。失敗したらログの該当行を報告する。"
    head = (f"対象の棟は {b}（{_site_ref(brief)} の id）。やり方は係の定義と docs/手順_エージェント.md の該当節に従う。"
            f"道具は `.venv/Scripts/python -I -X utf8 -m recorder …`。出力は JSON（made_by・made_at 付き）と短い Markdown。"
            "根拠の無い寸法は書かない（measured/estimated/unknown）。分からない事は questions・requests に。"
            f"観察・数え・測量・目印・設計・検収の報告 JSON は {rep}/ に書く（out/ ではない。画像と道具の出力は out/ のまま）。")
    if photos_folder:
        src = (f"写真フォルダ {photos_folder} を `recorder photos import {photos_folder} --period during` で索引に取り込む"
               "（取り込み済みの写真は重複しない）")
    else:
        src = "写真は索引 data/photos/index.json に取り込み済みの物を使う（新しい取り込みはしない）"
    tasks = {
        "satsuei": f"{head} {src}。CLAUDE.md の規約で使わないと決めた写真（期間前の写真・持ち込み禁止の場所）は取り込まない・写さない。"
                   f"{b} に当たりそうな写真の checks・candidates を見て、使えるか（GPS・ぶれ・解像度）と足りない面・角度を返す。"
                   f"出力 {out}/satsuei_checks.json（写真 id ごとの usable・problems を 1 つにまとめる）と職員向け 3 行。",
        "shiwake": f"{head} 索引の写真のうち building が {b} の物と、building が null で candidates に {b} がある物を目視で面に分ける。"
                   f"面 id は足元の辺番号 f0〜（{site_json}）。写真は期間中の物だけ（期間前の写真と分類台帳は使わない）。職員が振り分けた building は変えず、GPS との食い違いは disagreement に。"
                   f"メジャー（巻き尺）を壁に当てた写真には ruler: \"tape_measure\" と ruler_note（どの面の何に当てたか・見える目盛りの範囲）を付ける。"
                   f"出力 out/photos/shiwake_{b}.json（\"photos\": 写真 id → {{building, face, faces, interior_views, candidate_faces, confidence, evidence, ruler, ruler_note}}）。"
                   f"索引へは `recorder photos set-face --from out/photos/shiwake_{b}.json --building {b} --by \"仕分け係（<モデル>）\"` だけで書き戻す（索引のファイルを自分で書かない。他の棟と同時に回るので道具が鍵を持つ）。",
        "kansatsu": f"{head} 入力：out/photos/shiwake_{b}.json と索引の face、足元 {site_json}、屋上 out/site/aerial_z18.png、"
                    f"あれば {j}。面ごとの観察票（比率と個数だけ。寸法は書かない）。出力 {rep}/kansatsu_<面id>.json と {rep}/kansatsu.md。",
        "kazoe": f"{head} 入力：{rep}/kansatsu_*.json とその面の写真。面ごとのスパン数・階数を 2 枚以上で突き合わせて確定する"
                 f"（両端が入らない面は目印で 2 枚をつなぐ）。決まらない面は unknown と requests。出力 {rep}/kazoe_<面id>.json。",
        "sokuryo": f"{head} 入力：{rep}/kazoe_*.json、物差し台帳 data/rulers.json、足元（PLATEAU、無ければ OSM の輪郭）{site_json}。"
                   f"最初に `recorder photos list --building {b} --ruler tape_measure` でメジャーの写真を探す。あれば縮尺の先頭に使い、"
                   "measured はメジャーと同じ正面化画像・同じ面の値だけ（別の面へ当てはめた値は仮定で estimated）、± に目盛りの読み取りと傾きを積み、"
                   "2 か所目は検算だけ（差 % を method に）。無ければ PLATEAU 以下で換算して estimated、止めずに撮影依頼を 1 件だけ足す。"
                   "`photos rectify`（複数面は --roi）→ `photos measure`（片端ずつの面は `photos stitch`）で階高・柱間隔を m に。"
                   f"必ず 2 つ目の物差しで検算し差 % を method に。最後に登録と面の展開：棟 JSON が無ければ `recorder new {b} --from-site`、"
                   f"正面化した写真を `photos compare`（--px-per-m に測った縮尺）→ `photos register`（平らでない写真は `photos register-chain {j}`）、"
                   f"`recorder coverage {j}` で {out}/coverage_<面>_photo.png（設計係の製図板）を出す。"
                   f"出力 {rep}/sokuryo_<面id>.json（正面化画像は out/rectified/、registered・not_registered・face_photo 付き）。",
        "mejirushi": f"{head} 入力：{j}（seizu の後の模型）と、`recorder photos landmarks {j}` が出す {out}/landmarks/"
                     f"（landmarks.json・<面>.png・<写真id>_grid.jpg・tasks.json）。写真ごとに面の絵と升目の絵を見て、"
                     f"模型の目印（面の角・柱型の縁・窓の列の端）4〜6 点がどの升にあるかを指し、`recorder photos landmarks-crop {j}` の寄りで升を詰める。"
                     f"{rep}/mejirushi/picks_<写真id>.json を書き、`recorder photos pnp {j} --picks {rep}/mejirushi/picks_<写真id>.json --check-jpg` "
                     f"で道具が視点を解いて登録する。"
                     f"升だけを指し、寸法・px は読まない。合否（再投影の残差）は道具の物で緩めない。台帳を手で直さない。"
                     f"出力 {rep}/mejirushi/picks_<写真id>.json と台帳 data/photos/registry.json の登録（kind pose・source.kind landmarks）。",
        "sekkei": f"{head} 入力：{rep}/kansatsu_*・kazoe_*・sokuryo_*.json、out/photos/shiwake_{b}.json、"
                  f"面の写真 {out}/coverage_<面>_photo.png と {out}/coverage.json（製図板。px を数えて m に）。"
                  f"{design} を新しく作り（書き方は recorder/schema.py。既存の scripts/design_*.py は別の棟の設計なので値・柱割り・階高は写さない。土台は recorder.skeleton.from_site_cli(\"{b}\")＝"
                  f"`recorder new {b} --from-site` と同じ物）、実行して {j} を出す。JSON を手で書かない。"
                  f"部品は手順書「生成器の教訓」の語彙（見本 tests/fixtures/parts_building.json）、屋上は `recorder photos roof {j}`。"
                  f"語彙に無い形は composite で組む。観察票の 1 m 以上の物で写真に写っている物は 1 枚からでも estimated で必ず置く"
                  f"（tol は値の 20〜30%、根拠の写真 id）。写っていない面は仮説か unknown と requests、部品は仮説で作らない。"
                  f"`recorder validate {j}` → `recorder run {j} --fast` が通るまで。"
                  f"出力 {rep}/sekkei_summary.json（measured/estimated/unknown の件数と requests。design スクリプトの外で書く）。",
        "kenshu": f"{head} 入力：{j}、{design}、{out}/ の生成物、data/photos/registry.json。"
                  f"写真の登録と面の展開は測量係と目印係（{rep}/mejirushi/）が済ませている。`recorder recheck {j} --fast` から始め、どちらも登録できなかった写真・"
                  f"後から届いた写真だけ `photos register-chain` か `photos compare` → `photos register` で登録し直して "
                  f"`recorder photoproj {j} --fast` → 検収 C（屋上は航空写真）でずれと意味の不足を見る。写真にあって模型に無い 1 m 以上の物は自分で足す。"
                  f"直すのは {design} だけ（再実行 → validate → recheck）。2 回で収束しなければ止めて撮影依頼。"
                  f"最後に `recorder coverage {j}`。出力 {rep}/kenshu_<写真id>.json と {rep}/kenshu_summary.json（面ごとの合否・直した値・requests）。"
                  f"争点は out/{b}/soten.json。",
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


# ---- 事件の検出（進行役 Fable を呼ぶ判断。読むだけ。API は呼ばない） ----
# 11/8（J-011・J-015・J-025・J-028）：
# - requests の kind "tool" を 3 つに分ける。vocab（部品の型が足りない）だけが進行役を呼んで止める。
#   tool（道具の不具合・道具が無い）と site（敷地に置く物。K-107 の敷地の語彙）は記録だけで止めない。
# - 棟 JSON の requests の kind "check"（規則で決まらない争点）も事件に出す（check。止めずに進行役に回す。職員宛ては human）。
# - soten.json の open は rule の欄が埋まっていても stuck（rule は decided の時の物。検収係の止めた理由が入っていても open は未決）。
# - 再検収の stuck は「写真×線」の件数でなく面ごと：同じ面の写真を登録の組（鎖の根）でまとめ、組ごとの |ずれ| の中央値、
#   組の中央値が許容（既定 0.15 m）を超える組が 2 組以上かつ過半の線だけを数え、2 回続けて残る線がある面を「合格していない面」で出す。
#   1 組の登録（甲板の 10 枚など）だけが外れる線（1 組しか見ていない線も）と、弱い縁（weak）は数えない。
STOP_EVENTS = ("stuck", "vocab", "asset")                 # この事件があれば工程を止める（決定済みの物を除く）
EVENT_CALL = {"human": "human", "done": "fable", "stuck": "fable", "vocab": "fable", "asset": "fable", "setup": "fable",
              "check": "fable", "tool": "record", "site": "record"}
EVENT_KINDS = ["stuck", "vocab", "asset", "check", "done", "human", "setup", "tool", "site"]
_SETUP_BAD = ("map_only", "footprint_only", "ambiguous")
TOOL_CLASSES = ("vocab", "tool", "site")
_SITE_RE = re.compile(r"敷地に置く物|敷地に置く|敷地\s*JSON|敷地の部品")
_VOCAB_RE = re.compile(r"部品の型|語彙に無い|語彙で表せない|composite\s*でも|新しい(?:部品の)?型")


def tool_request_class(r: dict) -> str:
    """requests の kind "tool" の 1 件を vocab（部品の型が足りない）／site（敷地に置く物）／tool（道具の不具合・道具が無い）に分ける。
    1. 欄 need（または tool_kind）が vocab・site・tool（tool_bug）ならそれ。
    2. what・where に「敷地に置く物」「敷地 JSON」→ site（文中に「語彙」があっても敷地の語彙なので site）。
    3. what・why に「部品の型」「語彙に無い／で表せない」「composite でも」「新しい型」→ vocab。
    4. それ以外 → tool（道具の不具合。記録だけで止めない。決めつけて止めるより流れを止めない方を取る）。"""
    need = str(r.get("need") or r.get("tool_kind") or "").lower()
    if need in ("tool_bug", "bug"):
        return "tool"
    if need in TOOL_CLASSES:
        return need
    what, where, why = (str(r.get(k) or "") for k in ("what", "where", "why"))
    if _SITE_RE.search(what + "\n" + where):
        return "site"
    if _VOCAB_RE.search(what + "\n" + why):
        return "vocab"
    return "tool"


def _reg_roots(cwd: Path, bid: str) -> dict[str, str]:
    """写真 id → 登録の組の根（鎖の登録は via の先頭を辿った先の、via の無い登録）。台帳に無い写真は自分が根。"""
    reg = _jload(cwd / "data" / "photos" / "registry.json") or {}
    via: dict[str, str | None] = {}
    for e in reg.get("entries", []):
        if isinstance(e, dict) and e.get("building") == bid and e.get("photo") and not e.get("exclude"):
            v = e.get("via")
            via.setdefault(e["photo"], v[0] if isinstance(v, list) and v else (v if isinstance(v, str) else None))
    roots: dict[str, str] = {}
    for p in via:
        cur, seen = p, set()
        while via.get(cur) and cur not in seen:
            seen.add(cur)
            cur = via[cur]
        roots[p] = cur
    return roots


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def face_ng_lines(summary: dict, roots: dict[str, str]) -> dict[str, dict]:
    """再検収の summary 1 つから、面ごとに「登録の組ごとの |ずれ| の中央値が許容を超える組が 2 組以上かつ過半の線」を出す。
    戻り値 {面: {線の名前: {"median_m", "groups", "photos"}}}。weak・ずれの無い線（diff_m が null）は数えない。"""
    tol = float(summary.get("tol_m") or 0.15)
    by: dict[tuple, dict[str, list[float]]] = {}
    for p in summary.get("photos", []) or []:
        if not isinstance(p, dict):
            continue
        face, pid = p.get("face"), p.get("photo")
        for ln in p.get("lines", []) or []:
            d = ln.get("diff_m")
            if ln.get("weak") or d is None or ln.get("ok") is None:
                continue
            by.setdefault((face, ln.get("what")), {}).setdefault(roots.get(pid, pid), []).append(abs(float(d)))
    out: dict[str, dict] = {}
    for (face, what), groups in by.items():
        gmed = [_median(v) for v in groups.values()]
        n_bad = sum(1 for g in gmed if g > tol)
        # 2 組以上・組の過半で外れる線だけ。1 組だけの外れ（その組しか見ていない線を含む）は、その組の登録を疑う物で面の不合格にしない
        if n_bad >= 2 and n_bad * 2 > len(gmed):
            out.setdefault(str(face), {})[str(what)] = {"median_m": round(_median(gmed), 3), "groups": len(groups),
                                                       "groups_ng": n_bad, "photos": sum(len(v) for v in groups.values())}
    return out


def _jload(p: Path):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _walk_materials(obj):
    if isinstance(obj, dict):
        m = obj.get("material")
        if isinstance(m, str):
            yield m
        for v in obj.values():
            yield from _walk_materials(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_materials(v)


def _site_json(cwd: Path) -> dict | None:
    """今のキャンパスの敷地 JSON（campus_slug：環境変数 → data/site/ の既定）。無ければ None。
    （11/8 まで：default_campus() の文字列を dict と取り違え、data/site/*.json が 1 つの時しか読めていなかった。監査 A 18）"""
    slug = campus_slug(cwd)
    if not slug:
        return None
    doc = _jload(Path(cwd) / "data" / "site" / f"{slug}.json")
    return doc if isinstance(doc, dict) else None


def _name_link_bad(nl) -> list[str]:
    items = nl.items() if isinstance(nl, dict) else enumerate(nl or [])
    bad = []
    for k, v in items:
        vals = [v] if isinstance(v, str) else [x for x in (v.values() if isinstance(v, dict) else []) if isinstance(x, str)]
        if any(x in _SETUP_BAD for x in vals):
            bad.append(str((v.get("id") or v.get("name") or k) if isinstance(v, dict) else k))
    return bad


PERMISSION_SETTLED = ("granted", "none")   # 公式図の扱いが決まった状態（使う許可がある／使わないと決めた）


def _site_events(cwd: Path) -> list[dict]:
    ev: list[dict] = []
    s = _site_json(cwd)
    if s is None:
        return ev
    if not (s.get("scope") or {}).get("targets"):
        ev.append({"kind": "setup", "why": "敷地 JSON に scope.targets（今回記録する棟の範囲）がありません", "evidence": {"scope": s.get("scope")}})
    bad = _name_link_bad(s.get("name_links"))
    if bad:
        ev.append({"kind": "setup", "why": f"name_links に map_only / footprint_only / ambiguous の棟が {len(bad)} 件あります", "evidence": {"names": bad}})
    qs = [q for q in (s.get("questions") or []) if isinstance(q, dict) and q.get("status") == "open"]
    if qs:
        ev.append({"kind": "human", "why": f"敷地の質問（questions）が open で {len(qs)} 件あります", "evidence": {"questions": qs}})
    perm = (((s.get("sources") or {}).get("campus_map") or {}).get("permission") or {}).get("status")
    if perm not in PERMISSION_SETTLED:   # none＝公式図を使わないと決めた（OSM の名前だけで進める。shinko.md）も解決済み（監査 A 17）
        ev.append({"kind": "human", "why": f"公式キャンパス図を使うかが決まっていません（許可の状態 {perm}。"
                   "許可を得たら granted、使わないなら none）", "evidence": {"permission": perm}})
    return ev


def kenshu_passed_faces(cwd: Path, bid: str, after: str | None = None) -> set[str]:
    """検収係の data/reports/<棟>/kenshu_summary.json で pass が true の面。after（再検収のフォルダ名 YYYYMMDD_HHMMSS）
    より後に書かれた（made_at）時だけ使う。検収係が線を見て合格にした面は、道具の線の数で進行役を呼び直さない。"""
    j = _jload(cwd / REPORTS_DIR / bid / "kenshu_summary.json") or {}
    made = str(j.get("made_at") or "")[:19]
    if after:
        try:
            if not made or made < datetime.strptime(after[:15], "%Y%m%d_%H%M%S").isoformat():
                return set()
        except ValueError:
            return set()
    f = j.get("faces")
    items = f.items() if isinstance(f, dict) else ((x.get("face"), x) for x in (f or []) if isinstance(x, dict))
    return {str(k) for k, v in items if isinstance(v, dict) and v.get("pass") is True}


def _building_events(bid: str, cwd: Path, after_step: str | None) -> list[dict]:
    ev: list[dict] = []
    bpath = cwd / "data" / "buildings" / f"{bid}.json"
    b = _jload(bpath) or {}
    reqs = [r for r in (b.get("requests") or []) if isinstance(r, dict) and r.get("status") == "open"]
    tool_reqs = [r for r in reqs if r.get("kind") == "tool"]
    groups = [("vocab", "tool", [r for r in tool_reqs if tool_request_class(r) == "vocab"], "語彙（部品の型）が足りない"),
              ("asset", "asset", [r for r in reqs if r.get("kind") == "asset"], "アセットが要る")]
    for ekind, kind, rs, label in groups:
        if rs:
            from . import decisions

            ddoc = _jload(cwd / "data" / "decisions.json") or {}
            decided = [(r, decisions.match_request(ddoc, bid, r)) for r in rs]
            ids_ = sorted({d["id"] for _r, d in decided if d})
            ev1 = {"kind": ekind, "why": f"requests に kind \"{kind}\" の open が {len(rs)} 件（{label}）", "evidence": {"requests": rs}}
            if ids_ and all(d for _r, d in decided):
                ev1["why"] = f"決定済み（実装待ち）：requests に kind \"{kind}\" の open が {len(rs)} 件（{label}）。決定 {', '.join(ids_)}"
                ev1["evidence"]["decision"] = ", ".join(ids_)
                ev1["decided"] = True            # 進行役が決める事は残っていない → 止めない
            elif ids_:
                n = sum(1 for _r, d in decided if d)
                ev1["why"] += f"。うち {n} 件は決定済み（実装待ち）、決定 {', '.join(ids_)}"
                ev1["evidence"]["decision"] = ", ".join(ids_)
            ev.append(ev1)
    for cls, label in (("tool", "道具の不具合・道具が無い。記録だけで止めない（実装係へ）"),
                       ("site", "敷地に置く物。記録だけで止めない（敷地 JSON の係へ、K-107）")):
        rs = [r for r in tool_reqs if tool_request_class(r) == cls]
        if rs:
            ev.append({"kind": cls, "why": f"requests に kind \"tool\" の open が {len(rs)} 件（{label}）", "evidence": {"requests": rs}})
    # 規則で決まらない争点（棟 JSON の requests の kind check）：職員宛ては human、それ以外は進行役に回す（止めない）
    checks_open = [r for r in reqs if r.get("kind") == "check"]
    if checks_open:
        try:
            from .schema import request_to
        except Exception:  # noqa: BLE001
            def request_to(r):
                return r.get("to") or "tool"
        cs = [r for r in checks_open if request_to(r) == "staff"]
        ct = [r for r in checks_open if request_to(r) != "staff"]
        if ct:
            ev.append({"kind": "check", "why": f"requests に kind \"check\"（規則で決まらない争点）の open が {len(ct)} 件。"
                                               "工程は止めずに進行役が決める", "evidence": {"requests": ct}})
        if cs:
            ev.append({"kind": "human", "why": f"requests に kind \"check\" の職員宛て（確かめてほしい事）の open が {len(cs)} 件",
                       "evidence": {"requests": cs}})
    cat = _jload(cwd / "data" / "assets" / "catalog.json") or {}
    ids = {x.get("id") for x in cat.get("items", []) if isinstance(x, dict)}
    missing = sorted({m[len("asset:"):] for m in _walk_materials(b.get("parts")) if m.startswith("asset:")} - ids)
    if missing:
        ev.append({"kind": "asset", "why": f"部品の material が台帳 data/assets/catalog.json に無い asset id を指しています（{', '.join(missing)}）",
                   "evidence": {"missing_ids": missing}})
    photo_reqs = [r for r in reqs if r.get("kind") == "photo"]
    if photo_reqs:
        ev.append({"kind": "human", "why": f"撮影の依頼（kind \"photo\"）が open で {len(photo_reqs)} 件あります", "evidence": {"requests": photo_reqs}})
    # 根拠の門（11/7）：measured の根拠の写真のうち、仕分けと合わない・low で未確認の物 → 職員に確かめてもらう
    if b:
        try:
            from . import schema as _schema
            checks = _schema.evidence_checks(b, _schema.load_photo_records(cwd), cwd)
        except Exception:  # noqa: BLE001  事件の検出は止めない
            checks = []
        if checks:
            pids = sorted({c["photo"] for c in checks})
            ev.append({"kind": "human", "why": f"measured の根拠の写真 {len(pids)} 枚を職員に確かめてもらう必要があります（根拠の門。"
                                               f"職員の画面の「確かめてほしい写真」か recorder photos confirm。確かめるまで measured にしない）",
                       "evidence": {"to_confirm": [{k: c.get(k) for k in ("photo", "problem", "detail", "used_at")} for c in checks]}})

    # 争点（規則で決まらない物）
    soten = (_jload(cwd / "out" / bid / "soten.json") or {}).get("items") or []
    open_soten = [x for x in soten if isinstance(x, dict) and x.get("status") == "open"]
    if open_soten:      # rule の欄が埋まっていても open は未決（J-028。検収係が rule に止めた理由を書くことがある）
        ev.append({"kind": "stuck", "why": f"設計係と検収係の争点 {len(open_soten)} 件が open のまま（規則で決まりません）",
                   "evidence": {"soten": open_soten}})

    # 再検収（新しい順に 2 つ）
    rdir = cwd / "out" / bid / "recheck"
    sums = []
    if rdir.is_dir():
        for d in sorted((x for x in rdir.iterdir() if (x / "summary.json").is_file()), key=lambda x: x.name, reverse=True)[:2]:
            j = _jload(d / "summary.json")
            if isinstance(j, dict):
                sums.append((d.name, j))

    if len(sums) == 2:
        roots = _reg_roots(cwd, bid)
        new_, old_ = face_ng_lines(sums[0][1], roots), face_ng_lines(sums[1][1], roots)
        passed = kenshu_passed_faces(cwd, bid, after=sums[0][0])
        faces, by_kenshu = {}, {}
        for f, lines in new_.items():
            same = sorted(set(lines) & set(old_.get(f, {})))
            if same:
                (by_kenshu if f in passed else faces)[f] = [{"what": w, **lines[w]} for w in same][:20]
        if faces:
            n = sum(len(v) for v in faces.values())
            ev.append({"kind": "stuck", "why": f"再検収を 2 回回しても合格していない面が {len(faces)} 面（{', '.join(sorted(faces))}。"
                                               f"2 組以上の登録で外れる線 {n} 本）",
                       "evidence": {"recheck": [sums[1][0], sums[0][0]], "faces": faces, "passed_by_kenshu": sorted(by_kenshu),
                                    "rule": "面ごと・登録の組ごとの |ずれ| の中央値が許容を超える組が 2 組以上かつ過半の線。1 組だけの外れと弱い縁は数えない（J-025）"}})

    # 登録 0 枚（登録は測量係の最後なので、測量より前の工程では見ない）
    check_reg = after_step is None or (after_step in PIPELINE_STEPS and PIPELINE_STEPS.index(after_step) >= PIPELINE_STEPS.index("sokuryo"))
    idx = _jload(cwd / "data" / "photos" / "index.json") or {}
    n_photos = sum(1 for p in idx.get("photos", []) if isinstance(p, dict) and p.get("building") == bid)
    reg = _jload(cwd / "data" / "photos" / "registry.json") or {}
    n_reg = sum(1 for e in reg.get("entries", []) if isinstance(e, dict) and e.get("building") == bid and not e.get("exclude"))
    if check_reg and n_photos > 0 and n_reg == 0 and bpath.exists():
        ev.append({"kind": "stuck", "why": f"写真 {n_photos} 枚に対し登録が 0 枚で、設計に進めません", "evidence": {"photos": n_photos, "registered": 0}})

    # 完成判定の候補
    if bpath.exists() and sums:
        try:
            from . import schema
            errs = schema.validate_building(b)
        except Exception as e:  # noqa: BLE001
            errs = [f"検証できません: {e}"]
        latest = sums[0][1]
        all_pass = bool(latest.get("photos")) and all(
            p.get("pass_face") is not False and p.get("pass_parts") is not False and not p.get("n_ng") for p in latest["photos"])
        only_photo = all(r.get("kind") == "photo" for r in reqs)
        if not errs and all_pass and not open_soten and only_photo:
            ev.append({"kind": "done", "why": "validate OK・最新の再検収が全面合格・争点ゼロ・open の依頼は撮影のみ（完成判定の候補）",
                       "evidence": {"recheck": sums[0][0], "open_photo_requests": len(photo_reqs)}})
    return ev


def detect_events(building_id: str | None, cwd: str | Path = ".", now: str | None = None, *,
                  after_step: str | None = None, write: bool = True) -> dict:
    """進行役（Fable）や人を呼ぶ「事件」を、棟 JSON・再検収・争点・登録などから検出する（読むだけ。結果を out/<id>/events.json に書く）。
    building_id が None なら敷地の事件（setup・human）だけ。after_step は run_pipeline が渡す（登録 0 枚を見る時期）。"""
    cwd = Path(cwd)
    now = now or datetime.now().isoformat(timespec="seconds")
    events = _building_events(building_id, cwd, after_step) if building_id else _site_events(cwd)
    for e in events:
        e["call"] = EVENT_CALL[e["kind"]]
    events.sort(key=lambda e: EVENT_KINDS.index(e["kind"]))
    summary = {k: sum(1 for e in events if e["kind"] == k) for k in EVENT_KINDS}
    lines = [f"# 事件 {building_id or '敷地'}（{now}）", ""]
    if not events:
        lines.append("事件なし。係で続ける。")
    lines += [f"- [{e['kind']}]（{e['call']}）{e['why']}" for e in events]
    doc = {"building": building_id, "made_at": now, "events": events, "summary": summary, "markdown": "\n".join(lines) + "\n"}
    if write:
        out = cwd / "out" / (building_id or "site")
        out.mkdir(parents=True, exist_ok=True)
        (out / "events.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc


def refresh_report(building_id: str, cwd: Path) -> dict:
    """進捗報告（out/<棟>/report.html）を今の生成物で出し直す。工程が 1 つ終わるごとに呼ぶので、段が todo → partial → done と埋まっていく。
    棟 JSON が無い（まだ設計前）か失敗した時は理由を返すだけで、工程は止めない。"""
    j = cwd / "data" / "buildings" / f"{building_id}.json"
    if not j.exists():
        return {"skipped": "棟 JSON がまだ無い"}
    try:
        from . import report as _report

        r = _report.run(j, cwd / "out" / building_id / "report.html", root=cwd)
        return {"html": r["html"], "stages": {k: v["status"] for k, v in r["stages"].items()}}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def _run_code_step(step: str, building_id: str, cwd: Path) -> dict:
    rec = {"agent": step_agent(step), "step": step, "model": "code", "task": step_command(step, building_id),
           "started_at": _now(), **{k: None for k in TOKEN_KEYS}, "cost_usd": 0.0, "cost_usd_estimate": 0.0,
           "note": "コードの工程（API を使わない）。トークンは無いので null"}
    t = time.time()
    if step == "web":
        from . import site

        r = site.export_web(campus_slug(cwd) or site.default_campus(), with_coverage=True)
        rec.update(ok=True, outputs=[str(r["path"])])
    else:
        from . import run as runner

        j = cwd / "data/buildings" / f"{building_id}.json"
        r = runner.run(j, cwd / "out" / building_id, fast=True) if step == "seizu" else runner.run(j, cwd / "out" / building_id)
        rec.update(ok=bool(r.get("ok")), outputs=[str(Path(r["out"]) / "run_report.json")])
    rec["seconds"] = round(time.time() - t, 1)
    rec["ended_at"] = _now()
    rec["summary"] = f"{rec['task']} → {'ok' if rec.get('ok') else '失敗'}"
    return rec


def _pipeline_totals(steps: list[dict]) -> dict:
    """記録の合計。費用は単価の見積もり（cost_usd_estimate）がある工程はそれ、無い工程は SDK の報告（cost_usd）を足す。"""
    costs, basis = [], set()
    for r in steps:
        if r.get("cost_usd_estimate") is not None:
            costs.append(r["cost_usd_estimate"])
            if r.get("model") != "code":
                basis.add("単価")
        elif r.get("cost_usd") is not None:
            costs.append(r["cost_usd"])
            basis.add("SDK の total_cost_usd")
    toks = {f"{k}_total": (sum(r.get(k) or 0 for r in steps) if any(r.get(k) is not None for r in steps) else None)
            for k in ("input_tokens", "output_tokens")}
    return {"seconds_total": round(sum(r.get("seconds") or 0 for r in steps), 1), **toks,
            "cost_usd_estimate": round(sum(costs), 4) if costs else None,
            "cost_basis": "・".join(sorted(basis)) if basis else None}


def write_pipeline_record(building_id: str, doc: dict, cwd: Path, name: str | None = None) -> dict:
    """実行記録を out/<棟>/pipeline_<日時>.json と data/reports/<棟>/pipeline_<日時>.json（git に入る）に同じ中身で書く。"""
    name = name or f"pipeline_{datetime.now():%Y%m%d_%H%M%S}.json"
    paths = []
    for d in (Path(cwd) / "out" / building_id, Path(cwd) / REPORTS_DIR / building_id):
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        paths.append(d / name)
    return {"record": str(paths[0]), "report_record": str(paths[1])}


# ---- 本番の流れ（API）：状態の保存・再開（resume）・追記（continue）・費用の上限（11/8、J-014・J-029） ----
STATE_NAME = "pipeline_state.json"           # out/<棟>/pipeline_state.json（工程ごとの会話 id と記録。git に入れない）
# 追記（新しい写真で前回の棟 JSON に足す）の工程。観察・数え・目印は回さない（関係する面だけ測量・設計・検収）
CONTINUE_STEPS = ["satsuei", "shiwake", "sokuryo", "sekkei", "seizu", "kenshu", "seizu_final", "web"]
STOP_REASONS = {"rate_limit": "429・使用量の上限（3 回待っても解けない）", "limit": "1 回の呼び出しの上限（turns・費用）",
                "error": "係がエラーで終わった", "crash": "落ちた（例外・プロセスの終了）", "code": "コードの工程が失敗",
                "max_usd": "棟の費用の上限", "event": "進行役か人を呼ぶ事件"}
RESUME_SAME_SESSION = ("rate_limit", "limit", "crash")    # この理由で止まった工程は、同じ会話（session）の続きから


def continue_task(step: str, building_id: str, photos_folder: str | None = None, faces: list[str] | None = None) -> str:
    """追記の依頼文：step_task（いつもの文）＋前回の値を保つ決まり。faces が無ければ撮影係の答えと仕分けで決まる面。"""
    b = building_id
    base = step_task(step, b, photos_folder)
    if step in CODE_STEPS:
        return base
    rep, j, design = f"{REPORTS_DIR}/{b}", f"data/buildings/{b}.json", f"scripts/design_{b}.py"
    ftxt = (f"面 {', '.join(faces)}" if faces else
            f"撮影係の {rep}/satsuei_answers.json の faces と、仕分けで新しい写真が入った面")
    note = (f" 【追記】前回の棟 JSON {j} と {design} を保つ（作り直さない・--zero にしない・`recorder new` をしない）。"
            "前回の値を消さない。新しい写真の根拠で値を上書きする時は、前の値と差を method に書く"
            "（例「前 5.40 m（estimated、PLATEAU の辺）→ 5.46 m（measured、メジャー）、差 +1.1 %」）。"
            f"対象は新しい写真が当たる面だけ（{ftxt}）。他の面の値と報告 JSON は触らない。")
    extra = {
        "satsuei": (f"新しい写真を棟 JSON の open の撮影依頼（kind photo）に当てる。当たった依頼を {rep}/satsuei_answers.json に "
                    "{\"answers\": [{\"request\": <依頼の what>, \"face\", \"photos\": [写真 id], \"status\": \"answered\", \"why\"}], "
                    "\"faces\": [面 id]} で書く（棟 JSON は書かない。閉じるのは設計係が design スクリプトで）。"
                    "メジャーの写真の棟は職員の申告のまま。"
                    "職員の申告（索引の assigned_by: staff）が無い写真（画面を通さずフォルダで渡した物）は、"
                    "始める前に `recorder photos relink <フォルダ> --assign-from-folder` で付いているはず。"
                    "付いていなければ棟を推測で書かず、止めて questions に「relink が要る」と書く。"),
        "shiwake": f"新しい写真だけを分ける（前回の仕分けの写真の面は変えない。out/photos/shiwake_{b}.json の前の行は消さない）。",
        "sekkei": (f"{rep}/satsuei_answers.json の依頼は、新しい写真で値か形が決まった物だけ design スクリプトの requests で "
                   "status: \"answered\"・answered_by（写真 id）にする。決まらなければ open のまま why に足す。"),
    }.get(step, "")
    return base + note + extra


def _task_for(state: dict, step: str) -> str:
    if state.get("mode") == "continue":
        return continue_task(step, state["building"], state.get("photos"), state.get("faces"))
    return step_task(step, state["building"], state.get("photos"))


def state_path(building_id: str, cwd: str | Path = ".") -> Path:
    return Path(cwd) / "out" / building_id / STATE_NAME


def load_state(building_id: str, cwd: str | Path = ".") -> dict | None:
    j = _jload(state_path(building_id, cwd))
    return j if isinstance(j, dict) else None


def _save_state(state: dict, cwd: Path) -> None:
    state["updated_at"] = _now()
    p = state_path(state["building"], cwd)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _archive_state(building_id: str, cwd: Path) -> str | None:
    """新しく始める時、前の状態（止まったままの物も）を pipeline_state_<前の記録の日時>.json に退避する（消さない）。"""
    p = state_path(building_id, cwd)
    old = load_state(building_id, cwd)
    if not old:
        return None
    stem = Path(str(old.get("record_name") or "pipeline_unknown.json")).stem.replace("pipeline_", "")
    dst = p.with_name(f"pipeline_state_{stem}.json")
    os.replace(p, dst)
    return str(dst)


def _event_sig(e: dict) -> str:
    return f"{e['kind']}|{e['why']}"


def _stop_events(ev: dict, acknowledged: list[str]) -> list[dict]:
    """工程を止める事件：STOP_EVENTS のうち、決定済み（decided）でなく、再開の時に人・進行役が見て続けると決めた物（acknowledged）でない物。"""
    return [e for e in ev["events"] if e["kind"] in STOP_EVENTS and not e.get("decided") and _event_sig(e) not in acknowledged]


def _new_state(building_id: str, mode: str, photos: str | None, steps: list[str], faces: list[str] | None,
               max_usd: float | None) -> dict:
    now = _now()
    return {"building": building_id, "runner": "api", "mode": mode, "photos": photos, "faces": faces, "steps": steps,
            "done": [], "status": "new", "current": None, "stopped": None, "sessions": {}, "attempts": {},
            "spent_usd": 0.0, "max_usd": max_usd, "record_name": f"pipeline_{datetime.now():%Y%m%d_%H%M%S}.json",
            "acknowledged_events": [], "results": [], "usage_calls": [], "pending_events": [],
            "runs": [{"kind": "start", "at": now}], "started_at": now, "updated_at": now}


def _rec_cost(rec: dict) -> float:
    v = rec.get("cost_usd_estimate")
    if v is None:
        v = rec.get("cost_usd")
    return float(v or 0.0)


def _run_agent_step(state: dict, s: str, cwd: Path, *, dry_run: bool, agents_dir, prices, resume_sid, resume_why):
    """係の工程を 1 つ回し、(記録, 止まった理由 or None) を返す。費用の上限の残りを 1 回の上限に渡す。"""
    cap = state.get("max_usd")
    remaining = None if cap is None else round(float(cap) - float(state.get("spent_usd") or 0.0), 4)
    if remaining is not None and remaining <= 0 and not dry_run:
        return None, {"reason": "max_usd",
                      "why": f"棟の費用の上限 {cap} USD に達した（使った {state['spent_usd']:.4f} USD）。工程 {s} の前で止めた"}
    rec = run_agent(s, _task_for(state, s), cwd=cwd, dry_run=dry_run, agents_dir=agents_dir, building_id=state["building"],
                    prices=prices, resume=resume_sid, resume_why=resume_why, max_budget_usd=remaining)
    rec["step"] = s
    if dry_run:
        return rec, None
    state["attempts"][s] = int(state["attempts"].get(s) or 0) + 1
    rec["attempt"] = state["attempts"][s]
    if rec.get("session_id"):
        state["sessions"][s] = rec["session_id"]
    state["spent_usd"] = round(float(state.get("spent_usd") or 0.0) + _rec_cost(rec), 6)
    state["usage_calls"] += [{"step": s, "attempt": rec["attempt"], **c} for c in rec.get("calls") or []]
    if rec.get("rate_limited"):
        return rec, {"reason": "rate_limit", "why": rec.get("error")}
    if rec.get("stopped_by_limit"):
        return rec, {"reason": "limit", "why": rec.get("stop")}
    if rec.get("error"):
        return rec, {"reason": "crash", "why": rec.get("error")}
    if rec.get("is_error"):
        return rec, {"reason": "error", "why": rec.get("stop") or "is_error"}
    return rec, None


def _drive(state: dict, cwd: Path, *, dry_run: bool, agents_dir, prices) -> dict:
    """state の steps のうち done に無い物を順に回す。工程の前後で state を out/<棟>/pipeline_state.json に保存する（dry_run は保存しない）。
    止まった時（事件・429・上限・落ち・費用の上限）は stopped に {step, reason, why, at, session_id} を書いて返す。"""
    b = state["building"]
    n_before = len(state["results"])
    prev_stop = state.get("stopped") or {}
    state["stopped"] = None
    extra: dict = {}
    for s in [x for x in state["steps"] if x not in state["done"]]:
        same = prev_stop.get("step") == s and prev_stop.get("reason") in RESUME_SAME_SESSION
        resume_sid = state["sessions"].get(s) if same else None
        state.update(status="running", current=s)
        if not dry_run:
            _save_state(state, cwd)       # ここで落ちれば、resume は current の工程を「落ちた」として同じ会話から続ける
        stop: dict | None = None
        try:
            if s in CODE_STEPS:
                if dry_run:
                    rec = {"agent": step_agent(s), "step": s, "model": "code", "task": step_command(s, b), "dry_run": True}
                else:
                    rec = _run_code_step(s, b, cwd)
                    if not rec.get("ok"):
                        stop = {"reason": "code", "why": rec.get("summary")}
            else:
                rec, stop = _run_agent_step(state, s, cwd, dry_run=dry_run, agents_dir=agents_dir, prices=prices,
                                            resume_sid=resume_sid, resume_why=prev_stop.get("why") if resume_sid else None)
        except Exception as e:  # noqa: BLE001  落ちても状態と記録を残して止める
            rec = {"agent": step_agent(s), "step": s, "error": f"{type(e).__name__}: {e}", "ended_at": _now()}
            stop = {"reason": "crash", "why": rec["error"]}
        if rec is not None:
            if not dry_run:
                rec["progress_report"] = refresh_report(b, cwd)
                if s == "web" and not stop:
                    # 歩く画面が出た後に、報告の絵（証拠ビューア・歩く画面）を窓を出さずに裏で撮る（待たない）
                    try:
                        from . import shots as _shots
                        _shots.run_in_background(b)
                        rec["shots"] = "background"
                    except Exception as e:  # noqa: BLE001
                        rec["shots"] = f"skipped: {type(e).__name__}: {e}"
            state["results"].append(rec)
        if stop is None:
            state["done"].append(s)
            cap = state.get("max_usd")
            left = [x for x in state["steps"] if x not in state["done"] and x not in CODE_STEPS]
            if not dry_run and cap is not None and float(state.get("spent_usd") or 0.0) >= float(cap) and left:
                stop = {"reason": "max_usd",
                        "why": f"棟の費用の上限 {cap} USD に達した（使った {state['spent_usd']:.4f} USD）。工程 {s} の後で止めた"}
        if stop is None:
            ev = detect_events(b, cwd, after_step=s, write=not dry_run)
            state["pending_events"] = [{"kind": e["kind"], "call": e["call"], "why": e["why"]} for e in ev["events"]
                                       if e["kind"] not in STOP_EVENTS or e.get("decided")]
            hit = _stop_events(ev, state.get("acknowledged_events") or [])
            if hit:
                stop = {"reason": "event", "why": "；".join(e["why"] for e in hit)[:500], "events": [_event_sig(e) for e in hit]}
                extra = {"stopped_at": s, "events": ev}
        if stop is not None:
            stop.update(step=s, at=_now(), session_id=state["sessions"].get(s), label=STOP_REASONS.get(stop["reason"]))
            state.update(status="stopped", stopped=stop, current=None)
            break
        state["current"] = None
        if not dry_run:
            _save_state(state, cwd)
    if state["stopped"] is None:
        state.update(status="done", current=None)
    state["runs"][-1]["ended_at"] = _now()
    summary = {"building": b, "runner": "api", "mode": state["mode"], "dry_run": dry_run, **extra,
               "steps": state["results"], "steps_this_run": len(state["results"]) - n_before,
               "stopped_early": state["stopped"] is not None, "stopped": state["stopped"], "done": state["done"],
               "spent_usd": state["spent_usd"], "max_usd": state["max_usd"], "pending_events": state["pending_events"],
               "runs": state["runs"], "started_at": state["started_at"], "ended_at": _now(), **_pipeline_totals(state["results"]),
               "usage_calls": len(state["usage_calls"]),
               "usage_calls_total": {k: _sum_or_none(c.get(k) for c in state["usage_calls"]) for k in TOKEN_KEYS},
               "made_at": _now()}
    if not dry_run:
        _save_state(state, cwd)
        summary.update(write_pipeline_record(b, summary, cwd, state["record_name"]))
        summary["state"] = str(state_path(b, cwd))
    else:
        summary["state_preview"] = {k: state[k] for k in ("mode", "photos", "faces", "steps", "done", "status", "stopped",
                                                          "sessions", "spent_usd", "max_usd", "record_name")}
    return summary


def run_pipeline(building_id: str, photos_folder: str | None = None, *, steps: list[str] | None = None, dry_run: bool = False,
                 cwd: str | Path = ".", agents_dir: str | Path | None = None, prices: dict | None = None,
                 max_usd: float | None = None) -> dict:
    """工程を順に回す。dry_run でなければ係ごとの実行記録（係名・モデル・開始/終了時刻・秒数・トークン・費用・要約 1 行・note）を
    out/<棟>/pipeline_<日時>.json と data/reports/<棟>/pipeline_<日時>.json に残す（事件や上限で止まった時も）。
    状態（工程ごとの会話 id・済んだ工程・使った費用）は out/<棟>/pipeline_state.json。止まったら resume_pipeline で続ける。
    max_usd は棟ごとの費用の上限（USD。単価の見積もり、無ければ SDK の報告で数える）。
    prices は単価表（USD / 100 万トークン）。省けば環境変数 RECORDER_AGENT_PRICES、それも無ければ単価の見積もりは null。"""
    cwd = Path(cwd)
    steps = list(steps or PIPELINE_STEPS)
    bad = [s for s in steps if s not in PIPELINE_STEPS]
    if bad:
        raise AgentError(f"知らない工程: {bad}。ある工程: {PIPELINE_STEPS}")
    state = _new_state(building_id, "full", photos_folder, steps, None, max_usd)
    if not dry_run:
        arch = _archive_state(building_id, cwd)
        if arch:
            state["runs"][0]["previous_state"] = arch
    return _drive(state, cwd, dry_run=dry_run, agents_dir=agents_dir, prices=prices)


def continue_pipeline(building_id: str, photos_folder: str | None = None, *, faces: list[str] | None = None,
                      steps: list[str] | None = None, dry_run: bool = False, cwd: str | Path = ".",
                      agents_dir: str | Path | None = None, prices: dict | None = None, max_usd: float | None = None) -> dict:
    """追記：前回の棟 JSON を保ったまま、新しい写真で 取り込み → 撮影係が依頼に当てる → 仕分け → 関係する面だけ測量・設計・検収
    → 製図・web。依頼文は continue_task（前回の値を消さない・上書きは前の値と差を method に）。棟 JSON が無ければ断る。
    始める時に既に在る止める事件（前回の進行役が見た物）は既知として止めず、runs[0].known_events に残す。"""
    cwd = Path(cwd)
    if not (cwd / "data" / "buildings" / f"{building_id}.json").exists():
        raise AgentError(f"追記には前回の棟 JSON data/buildings/{building_id}.json が要ります（無い棟は recorder agent pipeline で最初から）")
    steps = list(steps or CONTINUE_STEPS)
    bad = [s for s in steps if s not in PIPELINE_STEPS]
    if bad:
        raise AgentError(f"知らない工程: {bad}。ある工程: {PIPELINE_STEPS}")
    state = _new_state(building_id, "continue", photos_folder, steps, list(faces) if faces else None, max_usd)
    # 始める前から在る止める事件（前回の実行で進行役が見た物）は「既知」として止めない。追記で新しく起きた事件だけで止める
    base = _stop_events(detect_events(building_id, cwd, after_step=None, write=False), [])
    state["acknowledged_events"] = sorted({_event_sig(e) for e in base})
    state["runs"][0]["known_events"] = [e["why"] for e in base]
    if not dry_run:
        arch = _archive_state(building_id, cwd)
        if arch:
            state["runs"][0]["previous_state"] = arch
    return _drive(state, cwd, dry_run=dry_run, agents_dir=agents_dir, prices=prices)


def resume_pipeline(building_id: str, *, dry_run: bool = False, cwd: str | Path = ".", agents_dir: str | Path | None = None,
                    prices: dict | None = None, max_usd: float | None = None) -> dict:
    """止まった所から続ける（out/<棟>/pipeline_state.json を読む）。
    - 429・上限・落ちで止まった工程は、同じ会話（session_id）の続きから（RESUME_PROMPT）。
    - 事件で止まった時は、その工程は済んでいるので次の工程から。止めた事件は「見て続けると決めた」として、同じ事件では止めない。
    - 費用の上限で止まった時は max_usd で上限を上げないと続けない。
    - 実行中のまま（status running）の状態は、落ちたと見て current の工程から。dry_run は状態を書かずに計画だけ返す。"""
    cwd = Path(cwd)
    state = load_state(building_id, cwd)
    if not state:
        raise AgentError(f"再開する記録がありません（{state_path(building_id, cwd)}）。recorder agent pipeline か continue で始める")
    if state.get("status") == "done":
        return {"building": building_id, "nothing": "前の実行は最後まで済んでいます", "state": str(state_path(building_id, cwd)),
                "done": state.get("done")}
    if state.get("status") == "running" and state.get("current"):
        state["stopped"] = {"step": state["current"], "reason": "crash", "why": "実行中のまま止まった（プロセスの終了か電源）",
                            "at": state.get("updated_at"), "session_id": state["sessions"].get(state["current"])}
    st = state.get("stopped") or {}
    if max_usd is not None:
        state["max_usd"] = max_usd
    if st.get("reason") == "max_usd" and state.get("max_usd") is not None \
            and float(state.get("spent_usd") or 0) >= float(state["max_usd"]):
        raise AgentError(f"棟の費用の上限 {state['max_usd']} USD に達しています（使った {state['spent_usd']} USD）。"
                         "--max-usd で上げてから resume する")
    if st.get("reason") == "event":
        state["acknowledged_events"] = sorted(set(state.get("acknowledged_events") or []) | set(st.get("events") or []))
    state["runs"].append({"kind": "resume", "at": _now(), "from_step": st.get("step"), "reason": st.get("reason"),
                          "why": st.get("why"), "session_id": st.get("session_id")})
    return _drive(state, cwd, dry_run=dry_run, agents_dir=agents_dir, prices=prices)


def lock_waits(building_id: str, start: str | None, end: str | None, path: Path | None = None) -> dict:
    """鍵を待った秒（out/.locks/waits.jsonl。ledger.py が書く）のうち、この棟のコマンド（label に棟 id が入る）で、
    start〜end（ISO の時刻）の間に取れた物を、鍵の名前ごとに足す。{"ledger": 秒, "gpu": 秒, "n": 件数}"""
    from .ledger import lock_dir
    p = path or (lock_dir() / "waits.jsonl")
    out: dict = {"ledger": 0.0, "gpu": 0.0, "n": 0}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            w = json.loads(line)
        except ValueError:
            continue
        at = str(w.get("at") or "")
        if building_id not in str(w.get("label") or ""):
            continue
        if (start and at < start[:19]) or (end and at > end[:19]):
            continue
        out[w.get("lock", "?")] = round(out.get(w.get("lock", "?"), 0.0) + float(w.get("waited_s") or 0), 2)
        out["n"] += 1
    return out


def log_step(building_id: str, step: str, *, model: str = "opus", seconds: float | None = None, tokens: int | None = None,
             note: str = "", record: str | Path | None = None, new: bool = False, cwd: str | Path = ".",
             started_at: str | None = None, ended_at: str | None = None, attempt: int | None = None,
             result: str | None = None, tool_uses: int | None = None) -> dict:
    """サブエージェントの試走の 1 工程を、run_pipeline と同じ形の記録（out/<id>/pipeline_<日時>.json、runner "subagent"）に追記する。
    record を省くと、同じ棟の runner "subagent" の最新の記録に足す（無いか new なら新しく作る）。
    11/7：始めと終わりの時刻・何回目か・結果・道具の回数と、その間にこの棟のコマンドが鍵を待った秒（lock_wait_s）も残す。
    始めの時刻が無ければ、終わり − seconds とする。"""
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
    now = datetime.now()
    end = ended_at or now.isoformat(timespec="seconds")
    start = started_at
    if start is None and seconds is not None:
        try:
            start = (datetime.fromisoformat(end) - timedelta(seconds=float(seconds))).isoformat(timespec="seconds")
        except ValueError:
            start = None
    rec = {"agent": agent, "step": step, "model": MODEL_MAP.get(m, m), "seconds": seconds, "tokens": tokens,
           "input_tokens": None, "output_tokens": None, "cost_usd": None, "cost_usd_estimate": None,
           "started_at": start, "ended_at": end, "attempt": attempt, "result": result, "tool_uses": tool_uses,
           "lock_wait_s": lock_waits(building_id, start, end),
           "outputs": _outputs(cwd, building_id, agent), "note": note,
           "logged_at": now.isoformat(timespec="seconds")}
    doc["steps"].append(rec)
    doc["seconds_total"] = round(sum(s.get("seconds") or 0 for s in doc["steps"]), 1)
    doc["tokens_total"] = sum(s.get("tokens") or 0 for s in doc["steps"])
    doc["lock_wait_total_s"] = {k: round(sum((s.get("lock_wait_s") or {}).get(k, 0) for s in doc["steps"]), 2)
                                for k in ("ledger", "gpu")}
    starts = [s["started_at"] for s in doc["steps"] if s.get("started_at")]
    ends = [s["ended_at"] for s in doc["steps"] if s.get("ended_at")]
    if starts and ends:                                   # 経過時間（最初の工程の始め〜最後の工程の終わり）
        doc["wall_seconds"] = round((datetime.fromisoformat(max(ends)) - datetime.fromisoformat(min(starts))).total_seconds(), 1)
    if path.parent.resolve() == out.resolve():
        # 既定の置き場なら data/reports/<棟>/ にも同じ名前で写す（git に入る記録）
        w = write_pipeline_record(building_id, doc, cwd, path.name)
        return {"record": str(path), "report_record": w["report_record"], "steps": len(doc["steps"]), "added": rec}
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"record": str(path), "steps": len(doc["steps"]), "added": rec}
