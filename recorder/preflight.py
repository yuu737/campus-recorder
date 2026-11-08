"""再開の前の点検（11/8 昼）：`recorder agent preflight <棟> [--call-models]`。

人が職員の画面で「進行役に決めさせて続ける」（POST /api/resume、shinko: true）を押す前に 1 回走らせ、
途中で落ちる要素（道具の不具合・形式の取りこぼし・環境）を先に並べる。人でないと決められない物（費用の上限・大学の許可）は
「注意」で出し、それで止まるのは良いとする。1 つでも「落ちる」（ng）があれば終了コード 1。
- 環境：API キー、BLENDER（製図の工程が使う）、.venv の python、out/<棟>/ の書き込み、ディスクの空き
- 記録：pipeline_state.json、status と stopped の整合、done の順、記録ファイル、費用の残り、進行役の回数の残り
- 事件：detect_events（done の末尾の後）と、進行役を呼べるか（_shinko_preflight）
- 係の定義：.claude/agents の全部、model が MODEL_MAP にあるか、_共通.md
- データ：棟 JSON の検証、写真の索引と登録台帳、決定ファイルの置き場
- 鍵：out/.locks と記録の鍵を、動いているプロセスが持っていないか（消すのは人）
- serve：/api/progress に届くか（届かなければ「立ててから押す」。落ちる要素ではなく注意）。届けば serve 側のキーの有無と続けられるか
- --call-models の時だけ：係の定義に出るモデルを 1 つずつ最小の呼び出し（_invoke、max_turns 1、0.05 USD）で確かめる
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.request
import warnings
from datetime import datetime
from pathlib import Path

from . import agents

NG, WARN, OK = "ng", "warn", "ok"
MARK = {NG: "落ちる", WARN: "注意", OK: "OK"}
DEFAULT_SERVE_PORT = 8767           # .claude/launch.json の本番の serve
DISK_NG_GB, DISK_WARN_GB = 1.0, 5.0
PING_TASK = "OK とだけ返事してください。道具は使わない。"
PING_TURNS, PING_USD = 1, 0.05


def _item(out: list, level: str, what: str, why: str, fix: str | None = None, **kw) -> None:
    out.append({"level": level, "what": what, "why": why, **({"fix": fix} if fix else {}), **kw})


def _check_env(cwd: Path, bid: str, todo: list[str], out: list, call_models: bool) -> None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        _item(out, OK, "API キー", "ANTHROPIC_API_KEY がこのタブにある")
    else:
        _item(out, NG if call_models else WARN, "API キー",
              "ANTHROPIC_API_KEY がこのタブに無い（serve を立てるタブに要る。--call-models は確かめられない）",
              "キーのあるタブで serve を立て、そのタブで preflight --call-models を走らせる")
    if any(s in ("seizu", "seizu_final") for s in todo):
        b = os.environ.get("BLENDER")
        if not b:
            _item(out, NG, "BLENDER", "環境変数 BLENDER が無い（製図の工程 seizu・seizu_final が Blender 5.2 を起動できない）",
                  "serve を立てるタブで BLENDER に Blender 5.2 の blender.exe のパスを入れる")
        elif not Path(b).exists():
            _item(out, NG, "BLENDER", f"BLENDER のパス {b} にファイルが無い", "Blender 5.2 の blender.exe のパスに直す")
        else:
            _item(out, OK, "BLENDER", f"{b}")
    venv = (cwd / ".venv").resolve()
    exe = Path(sys.executable).resolve()
    if venv.exists() and venv not in exe.parents:
        _item(out, WARN, "python", f"今の python {exe} は .venv の物ではない（serve も .venv の python で立てる）")
    else:
        _item(out, OK, "python", f"{exe}（{sys.version.split()[0]}）")
    d = cwd / "out" / bid
    try:
        d.mkdir(parents=True, exist_ok=True)
        t = d / f".preflight_{os.getpid()}.tmp"
        t.write_text("x", encoding="utf-8")
        t.unlink()
        _item(out, OK, "out の書き込み", f"out/{bid}/ に書ける")
    except OSError as e:
        _item(out, NG, "out の書き込み", f"out/{bid}/ に書けない：{e}")
    try:
        free = shutil.disk_usage(cwd).free / 1e9
        lv = NG if free < DISK_NG_GB else (WARN if free < DISK_WARN_GB else OK)
        _item(out, lv, "ディスクの空き", f"{free:.1f} GB", "要らない out/ の描画を外に移す" if lv != OK else None)
    except OSError as e:
        _item(out, WARN, "ディスクの空き", f"測れない：{e}")


def _check_state(cwd: Path, bid: str, out: list) -> dict | None:
    p = agents.state_path(bid, cwd)
    if not p.exists():
        _item(out, NG, "記録", f"{p.relative_to(cwd).as_posix()} が無い（続ける記録が無い）", "記録を始める（pipeline か continue）")
        return None
    st = agents.load_state(bid, cwd)
    if not isinstance(st, dict):
        _item(out, NG, "記録", f"{p.relative_to(cwd).as_posix()} が JSON として読めない（壊れているか書きかけ）",
              "写し（pipeline_state.before_*.json）から戻す")
        return None
    steps, done = list(st.get("steps") or []), list(st.get("done") or [])
    status, cur, stop = st.get("status"), st.get("current"), st.get("stopped")
    _item(out, OK, "記録", f"{st.get('record_name')}・状態 {status}・済み {', '.join(done) or 'なし'}")
    if status == "stopped" and not isinstance(stop, dict):
        _item(out, NG, "記録の整合", "status は stopped なのに stopped（止まった理由）が無い")
    if status == "stopped" and cur:
        _item(out, WARN, "記録の整合", f"status は stopped なのに current が {cur} のまま（resume は困らないが、止まり方を確かめる）")
    if status == "running":
        _item(out, WARN, "記録の整合", f"status が running（current {cur}、最後の更新 {st.get('updated_at')}）。"
                                    "別の窓で動いていないか確かめる（画面は 10 分以上更新が無ければ落ちたと見て続ける）")
    if status == "done":
        _item(out, WARN, "記録の整合", "status は done（続ける工程が無い。進行役の事件が無ければ何もしない）")
    bad = [s for s in done if s not in steps]
    order = [s for s in steps if s in done]
    if bad or order != done:
        _item(out, NG, "done の順", f"done {done} が steps {steps} の順になっていない" + (f"（知らない工程 {bad}）" if bad else ""))
    elif done and done != steps[:len(done)]:
        _item(out, WARN, "done の順", f"done {done} が steps の頭から続いていない（抜けた工程は次に回る）")
    rn = st.get("record_name")
    if rn and not (cwd / "out" / bid / rn).exists():
        _item(out, WARN, "記録ファイル", f"out/{bid}/{rn} が無い（次の保存で作られる）")
    n = len(st.get("shinko") or [])
    _item(out, OK if n < agents.SHINKO_AUTO_MAX else WARN, "進行役の回数",
          f"この記録で {n} 回、残り {max(agents.SHINKO_AUTO_MAX - n, 0)} 回（{agents.SHINKO_AUTO_MAX} 回を超えると人を呼ぶ）")
    if isinstance(st.get("halt"), dict):
        _item(out, WARN, "止める合図", f"halt が残っている（{st['halt'].get('why')}）。画面から続けると消える")
    return st


def _model_of(step: str, cwd: Path) -> str:
    d = (_DEFS_CACHE.get(str(cwd)) or {}).get(agents.step_agent(step)) or {}
    return d.get("model") if d.get("model") in agents.LIMITS else "opus"


_DEFS_CACHE: dict[str, dict] = {}


def _check_defs(cwd: Path, todo: list[str], out: list) -> dict:
    d = cwd / agents.DEFAULT_DIR
    if not (d / f"{agents.COMMON_NAME}.md").exists():
        _item(out, NG, "係の定義", f"{agents.DEFAULT_DIR}/{agents.COMMON_NAME}.md が無い")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        try:
            defs = agents.load_agent_defs(d)
        except Exception as e:  # noqa: BLE001
            _item(out, NG, "係の定義", f"読めない：{type(e).__name__}: {e}")
            return {}
    for x in w:
        _item(out, NG, "係の定義", f"{x.message}（モデルの対応表 MODEL_MAP に無い）")
    _DEFS_CACHE[str(cwd)] = defs
    need = {agents.step_agent(s) for s in todo if s not in agents.CODE_STEPS} | {agents.SHINKO_AGENT}
    miss = sorted(need - set(defs))
    if miss:
        _item(out, NG, "係の定義", f"残りの工程と進行役に要る係の定義が無い：{miss}")
    else:
        _item(out, OK, "係の定義", f"{len(defs)} 係（{', '.join(sorted(defs))}）。要る係 {sorted(need)} はある")
    sh = defs.get(agents.SHINKO_AGENT)
    if sh and not {"Write", "Edit"} <= set(sh["tools"]):
        _item(out, NG, "進行役の道具", f"shinko の tools {sh['tools']} に Write・Edit が無い（決定ファイルと手順書を書けない）")
    return defs


def _check_events(cwd: Path, bid: str, st: dict, out: list) -> dict:
    after = (st.get("done") or [None])[-1]
    try:
        ev = agents.detect_events(bid, cwd, after_step=after, write=False)
    except Exception as e:  # noqa: BLE001
        _item(out, NG, "事件", f"detect_events が落ちる：{type(e).__name__}: {e}")
        return {}
    hit = [e for e in ev["events"] if e["kind"] in agents.STOP_EVENTS and not e.get("decided")]
    ack = set(st.get("acknowledged_events") or [])
    lines = [f"[{e['kind']}]{'（既に acknowledged）' if agents._event_sig(e) in ack else ''} {e['why']}" for e in hit]
    ok, why_not = agents._shinko_preflight(st)
    plan = {"after_step": after, "stop_events": [agents._event_sig(e) for e in hit], "shinko_ok": ok, "why_not": why_not,
            "other": [f"[{e['kind']}] {e['why']}" for e in ev["events"] if e not in hit]}
    if not hit:
        _item(out, OK, "事件", f"{after} の後に止める事件は無い（進行役は呼ばれず、普通の resume）", **plan)
    elif ok:
        _item(out, OK, "事件", f"{after} の後の止める事件 {len(hit)} 件を進行役に出す：" + "／".join(lines), **plan)
    else:
        why = {"max": f"進行役の回数 {agents.SHINKO_AUTO_MAX} を使い切った（人が決める）",
               "no_cap": "記録に費用の上限が無い（進行役を自動で呼ばない）",
               "max_usd": f"棟の上限の残りが進行役の 1 回分（{agents.LIMITS['fable']['max_budget_usd']:g} USD）に足りない"}.get(
            why_not, why_not)
        _item(out, WARN, "事件", f"止める事件 {len(hit)} 件があるが進行役を呼べない：{why}（人が決める事）",
              "上限を上げる・人が決定ファイルを書いて --decision で続ける", **plan)
    return plan


def _check_data(cwd: Path, bid: str, out: list) -> None:
    bj = cwd / "data" / "buildings" / f"{bid}.json"
    if not bj.exists():
        _item(out, WARN, "棟 JSON", f"{bj.relative_to(cwd).as_posix()} が無い（最初からの記録なら設計の後にできる）")
    else:
        try:
            from . import schema
            errs = schema.validate_building(schema.load_building(bj))
            if errs:
                _item(out, NG, "棟 JSON の検証", f"エラー {len(errs)} 件（{'；'.join(map(str, errs[:3]))}）。製図の工程が落ちる",
                      "設計係のやり直しか design スクリプトで直す（手で直さない）", errors=errs[:20])
            else:
                _item(out, OK, "棟 JSON の検証", "validate が通る")
        except Exception as e:  # noqa: BLE001
            _item(out, NG, "棟 JSON の検証", f"検証が落ちる：{type(e).__name__}: {e}")
    for rel in ("data/photos/index.json", "data/photos/registry.json"):
        p = cwd / rel
        if not p.exists():
            _item(out, WARN, "台帳", f"{rel} が無い")
            continue
        try:
            json.loads(p.read_text(encoding="utf-8"))
            _item(out, OK, "台帳", f"{rel} が読める")
        except (OSError, ValueError) as e:
            _item(out, NG, "台帳", f"{rel} が読めない：{e}")
    d = cwd / agents.REPORTS_DIR / f"指示_{datetime.now():%m-%d}"
    try:
        d.mkdir(parents=True, exist_ok=True)
        t = d / f".preflight_{os.getpid()}.tmp"
        t.write_text("x", encoding="utf-8")
        t.unlink()
        _item(out, OK, "決定の置き場", f"{d.relative_to(cwd).as_posix()}/ に書ける")
    except OSError as e:
        _item(out, NG, "決定の置き場", f"{d.relative_to(cwd).as_posix()}/ に書けない：{e}")
    for rel in ("docs/手順_エージェント.md",):
        p = cwd / rel
        if not p.exists():
            _item(out, WARN, "手順書", f"{rel} が無い（進行役が規則を 1 行足す先）")
        elif not os.access(p, os.W_OK):
            _item(out, NG, "手順書", f"{rel} に書けない（読み取り専用）")


def _lock_held(p: Path) -> bool | None:
    """ファイルの鍵を、動いているプロセスが持っているか（持っていれば True）。調べられなければ None。鍵は取ったらすぐ外す。"""
    from .ledger import _try_lock, _unlock
    try:
        fh = open(p, "a+b")
    except OSError:
        return None
    try:
        fh.seek(0, 2)
        if fh.tell() == 0:
            return False
        try:
            _try_lock(fh)
        except OSError:
            return True
        _unlock(fh)
        return False
    finally:
        fh.close()


def _check_locks(cwd: Path, bid: str, out: list) -> None:
    from .ledger import lock_dir
    paths = sorted(lock_dir().glob("*.lock")) + [agents.state_path(bid, cwd).with_suffix(".lock")]
    held = []
    for p in paths:
        if p.exists() and _lock_held(p):
            held.append(p)
    if held:
        _item(out, WARN, "鍵", "動いているプロセスが鍵を持っている：" + "、".join(str(p) for p in held)
              + "（鍵はファイルの鍵で、プロセスが終われば OS が外す。残ったファイル自体は害が無い。持っている間は待つ）",
              "別の窓の recorder（pipeline・resume・photos など）が終わるのを待つ。消すのは人が確かめてから")
    else:
        _item(out, OK, "鍵", f"{lock_dir()} と記録の鍵を、動いているプロセスは持っていない（ファイルが残っていても害は無い）")


def _check_serve(bid: str, port: int, out: list, timeout: float = 2.0) -> dict | None:
    url = f"http://127.0.0.1:{port}/api/progress?buildings={bid}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:      # noqa: S310  ローカルの serve だけ
            doc = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        _item(out, WARN, "serve", f"127.0.0.1:{port} の serve に届かない（{type(e).__name__}）。立ててから再開ボタンを押す",
              f"キーと BLENDER のあるタブで python -I -X utf8 -m recorder serve --port {port}")
        return None
    b = (doc.get("buildings") or {}).get(bid) or {}
    rs = b.get("resume")
    run = b.get("run") or {}
    if run.get("state") == "running":
        _item(out, WARN, "serve", f"serve の中で {bid} が動いている（終わるまで再開ボタンは押せない）")
    if not isinstance(rs, dict):
        _item(out, WARN, "serve", f"serve は動いているが {bid} の記録を見ていない（root が違う？）")
        return doc
    if not rs.get("api_key"):
        _item(out, NG, "serve のキー", "serve を立てたタブに ANTHROPIC_API_KEY が無い（押しても no_key で断られる）",
              "キーのあるタブで serve を立て直す")
    lv = OK if rs.get("ok") else WARN
    _item(out, lv, "serve", f"127.0.0.1:{port} の serve が動いている。画面から続けられるか：{rs.get('code')}（{rs.get('why')}）")
    return doc


def _classify(err: str) -> str:
    t = err.lower()
    if agents._RATE_RE.search(err):
        return "429（混雑・使用量の上限。時間を置く）"
    if "not_found" in t or "404" in t or ("model" in t and ("not found" in t or "does not exist" in t or "invalid" in t)):
        return "モデルが無い（モデル id か、このキーで使えない）"
    if "401" in t or "403" in t or "permission" in t or "authentication" in t or "unauthorized" in t or "forbidden" in t:
        return "権限が無い（キーが違うか、このモデルの権限が無い）"
    return "その他の失敗"


def _call_models(cwd: Path, defs: dict, out: list) -> None:
    models = sorted({d["model"] for d in defs.values()})
    for m in models:
        spec = {"name": f"preflight_{m}", "system_prompt": "点検の呼び出し。OK とだけ返事する。", "model": m,
                "model_id": agents.MODEL_MAP[m], "tools": []}
        try:
            r = agents._invoke(spec, PING_TASK, cwd, PING_TURNS, PING_USD)
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {e}"
            if isinstance(e, agents.InvokeError) and e.rate_limited:
                kind = "429（混雑・使用量の上限。時間を置く）"
            else:
                kind = _classify(msg)
            _item(out, NG, f"モデル {m}", f"{agents.MODEL_MAP[m]}：{kind}。{msg[:300]}", model=m, kind=kind)
            continue
        if r.get("is_error") and r.get("rate_limited"):
            kind = "429（混雑・使用量の上限。時間を置く）"
            _item(out, NG, f"モデル {m}", f"{agents.MODEL_MAP[m]}：{kind}", model=m, kind=kind)
        elif r.get("is_error") and "budget" not in str(r.get("stop") or "") and "max_turns" not in str(r.get("stop") or ""):
            st = r.get("api_error_status")
            kind = _classify(f"{st} {r.get('stop')} {r.get('text')}")
            _item(out, NG, f"モデル {m}", f"{agents.MODEL_MAP[m]}：{kind}（{st} {r.get('stop')}）", model=m, kind=kind)
        else:
            _item(out, OK, f"モデル {m}", f"{agents.MODEL_MAP[m]} が返事した（{(r.get('text') or '').strip()[:20]!r}、"
                                          f"{r.get('cost_usd')} USD）", model=m, cost_usd=r.get("cost_usd"))


def run(building_id: str, *, cwd: str | Path = ".", call_models: bool = False, port: int = DEFAULT_SERVE_PORT,
        check_serve: bool = True) -> dict:
    """点検して {"building", "ok", "ng", "warn", "items", "plan", "made_at"} を返す（out/<棟>/preflight.json にも書く）。"""
    cwd = Path(cwd).resolve()
    items: list[dict] = []
    st = _check_state(cwd, building_id, items)
    todo = [s for s in (st or {}).get("steps") or agents.PIPELINE_STEPS if s not in ((st or {}).get("done") or [])]
    _check_env(cwd, building_id, todo, items, call_models)
    defs = _check_defs(cwd, todo, items)
    if st is not None:      # 費用の見込みは係の定義のモデルで数える（定義を読んだ後）
        _check_state_fee(cwd, st, items)
    plan = _check_events(cwd, building_id, st, items) if st is not None else {}
    _check_data(cwd, building_id, items)
    _check_locks(cwd, building_id, items)
    if check_serve:
        _check_serve(building_id, port, items)
    if call_models:
        if os.environ.get("ANTHROPIC_API_KEY"):
            _call_models(cwd, defs, items)
    ng = [i for i in items if i["level"] == NG]
    doc = {"building": building_id, "ok": not ng, "ng": len(ng), "warn": sum(1 for i in items if i["level"] == WARN),
           "items": items, "plan": plan, "call_models": call_models, "made_at": agents._now()}
    try:
        p = cwd / "out" / building_id / "preflight.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        doc["path"] = str(p)
    except OSError:
        pass
    return doc


def _check_state_fee(cwd: Path, st: dict, out: list) -> None:
    cap, spent = st.get("max_usd"), float(st.get("spent_usd") or 0.0)
    if cap is None:
        _item(out, WARN, "費用", f"上限なし（使った {spent:.2f} USD）。進行役は上限の無い記録では自動で呼ばない",
              "画面か --max-usd で上限を付けて続ける")
        return
    steps, done = list(st.get("steps") or []), list(st.get("done") or [])
    left = float(cap) - spent
    todo_agents = [s for s in steps if s not in done and s not in agents.CODE_STEPS]
    need = agents.LIMITS["fable"]["max_budget_usd"] + sum(
        agents.LIMITS[_model_of(s, cwd)]["max_budget_usd"] for s in todo_agents)
    lv = NG if left <= 0 else (WARN if left < need else OK)
    _item(out, lv, "費用", f"上限 {cap} USD・使った {spent:.2f} USD・残り {left:.2f} USD"
          + ("" if lv == OK else f"（進行役 1 回 {agents.LIMITS['fable']['max_budget_usd']:g} USD と残りの係 {todo_agents} の"
                                 f" 1 回の上限の合計 {need:.1f} USD より少ない。進行役が前の工程からやり直すと費用の上限で止まる見込み。"
                                 "上限は人が決める事）"),
          "画面の上限の欄で上げてから押す" if lv != OK else None, left_usd=round(left, 4), need_usd=round(need, 2))


def lines(doc: dict) -> list[str]:
    out = [f"再開の前の点検 {doc['building']}（{doc['made_at']}）：落ちる {doc['ng']}・注意 {doc['warn']}"]
    for i in doc["items"]:
        out.append(f"[{MARK[i['level']]}] {i['what']}：{i['why']}" + (f" → {i['fix']}" if i.get("fix") else ""))
    out.append("→ 落ちる要素はありません" if doc["ok"] else "→ 落ちる要素を直してから再開ボタンを押す")
    return out
