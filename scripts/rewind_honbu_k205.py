"""本部棟の本番 2 回目の記録（out/honbu/pipeline_state.json）を、09:35 の「事件 2 で止まった」状態に戻す（11/8、1 回きり）。

09:36 に指示役が利用者にも進行役にも相談せずに K-205（決定_本番_2.md）で測量から再開し、利用者が途中で止めた。
その再開は取り下げ（決定_本番_3.md の K-301〜302）。戻すのは記録の欄だけ：
- done を 09:34 までに済んだ工程（satsuei, shiwake, sokuryo, sekkei, seizu, kenshu）に戻す
- status stopped・current None・stopped = 09:35 の事件（検収の後、理由 event）
- redo（K-205 の物）を外し、popped された会話 id を sessions に戻す
- decisions の K-205 の項目に withdrawn を付ける（消さない）。acknowledged から事件 2 の印を外す
- runs に kind "rewind" を足す
棟 JSON・写真の索引・登録台帳（09:37 に 2 枚が新しい縮尺で登録し直された物）は触らない。進行役がその扱いを決める（K-302）。
実行：python -I -X utf8 scripts/rewind_honbu_k205.py [--write]（--write が無ければ差分を出すだけ）
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "out" / "honbu" / "pipeline_state.json"
DONE_0935 = ["satsuei", "shiwake", "sokuryo", "sekkei", "seizu", "kenshu"]
K205 = "data/reports/指示_11-08/決定_本番_2.md"
EVENT2 = "stuck|再検収を 2 回回しても合格していない面が 4 面（f4, f5, f6, f8。2 組以上の登録で外れる線 28 本）"


def main() -> int:
    write = "--write" in sys.argv
    s = json.loads(STATE.read_text(encoding="utf-8"))
    if s.get("status") != "running" or s.get("current") != "sokuryo" or (s.get("redo") or {}).get("file") != K205:
        print("想定の状態（running・current sokuryo・redo が K-205）ではありません。何もしません")
        print({k: s.get(k) for k in ("status", "current", "done")}, (s.get("redo") or {}).get("file"))
        return 1
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    before = {k: s.get(k) for k in ("status", "current", "done", "stopped", "acknowledged_events")}
    redo = s.pop("redo")
    for step, sid in (redo.get("previous_sessions") or {}).items():
        s.setdefault("sessions", {}).setdefault(step, sid)
    s["done"] = list(DONE_0935)
    s["status"] = "stopped"
    s["current"] = None
    s["stopped"] = {"step": "kenshu", "reason": "event", "why": EVENT2.split("|", 1)[1], "events": [EVENT2],
                    "at": "2026-11-08T09:35:00", "session_id": s["sessions"].get("kenshu"),
                    "label": "進行役か人を呼ぶ事件", "restored_by": "rewind"}
    s["acknowledged_events"] = [a for a in s.get("acknowledged_events") or [] if a != EVENT2]
    for d in s.get("decisions") or []:
        if d.get("file") == K205:
            d["withdrawn"] = {"at": now, "why": "利用者にも進行役にも相談せずに入れた決定。案に戻した（決定_本番_3.md）"}
    s["runs"].append({"kind": "rewind", "at": now, "to": "kenshu の後の事件（09:35）",
                      "why": "09:36 の相談なしの再開（K-205）を取り下げ、09:35 に止まった状態へ戻した（利用者の指示、決定_本番_3.md K-301〜302）。"
                             "09:36〜09:37 の測量係の呼び出しと登録台帳の 2 枚の付け替えは記録と台帳に残したまま", "by": "staff+shinko"})
    s["updated_at"] = now
    after = {k: s.get(k) for k in ("status", "current", "done", "stopped", "acknowledged_events")}
    print("before:", json.dumps(before, ensure_ascii=False, indent=1))
    print("after:", json.dumps(after, ensure_ascii=False, indent=1))
    if write:
        bak = STATE.with_name(f"pipeline_state.before_rewind_{now.replace(':', '').replace('-', '')}.json")
        bak.write_text(STATE.read_text(encoding="utf-8"), encoding="utf-8")
        STATE.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
        print("書きました。写し:", bak.name)
    else:
        print("（--write が無いので書いていません）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
