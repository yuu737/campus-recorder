"""C_seika の values.json を、0 から立てた実走の記録（別の作業ツリー D:/BuiltwithClaude_run0、読むだけ）から作り直す。
    python -I -X utf8 scripts/video/app2/C_seika/make_values.py
記録が走っている間は今の値。終わったらもう一度回すだけで差し替わる。"""
import json
from pathlib import Path

RUN = Path(r"D:\BuiltwithClaude_run0")
HERE = Path(__file__).resolve().parent
YEN_PER_USD = 150
import sys
DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 9.0  # 暫定。利用者が決める（ここ 1 か所）

st = json.loads((RUN / "out/honbu/pipeline_state.json").read_text(encoding="utf-8"))
rp = json.loads((RUN / "out/honbu/report.json").read_text(encoding="utf-8"))
R = st.get("results", [])
sec = sum(r.get("seconds") or 0 for r in R)
usd = float(st.get("spent_usd") or 0)
runs = st.get("runs", [])
starts = sum(1 for r in runs if r.get("kind") == "start")
resumes = sum(1 for r in runs if r.get("kind") == "resume")
reviews = sum(1 for x in (st.get("shinko") or []) if x.get("review"))
photos_dir = RUN / st["photos"]
n_photos = sum(1 for p in photos_dir.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
c = rp["counts"]
mk = c.get("measured_kinds", {})
h, m = int(sec // 3600), int(sec % 3600 // 60)
ops = starts + resumes + reviews
if resumes == 0:
    tag = "人がしたのは開始と承認だけ"
else:
    tag = f"人の操作 {ops} 回：開始 {starts}・続きから {resumes}" + (f"・承認 {reviews}" if reviews else "")
v = {
    "note": "C_seika このツールの成果（締め、静止画だけ）。make_values.py が作る。手で直さない",
    "dur": DUR,
    "head": "何が[どう変わった]か",
    "before": {
        "k": "外注すると",
        "rows": [
            {"t": "費用", "n": "1 棟 約 4 万〜100 万円", "src": "scripts/video/opening_v7/values.json cost（個人の出品と制作会社の公開価格、11/8 取得）"},
            {"t": "期間", "n": "数週間〜", "src": "scripts/video/opening_v7/values.json period（同上）"},
        ],
        "src": "docs/動画_数字の出典_3D相場_11-08.md（図面がある前提。測る作業は別）",
    },
    "after": {
        "k": "この道具（本部棟を写真だけから 0 から立てた実走）",
        "rows": [
            {"t": "係が動いた時間", "n": f"{h} 時間 {m:02d} 分", "src": f"pipeline_state.json results[].seconds の和 {sec:.1f} 秒（{len(R)} 件）"},
            {"t": "費用", "n": f"{usd:.2f} USD", "sub": f"約 {round(usd * YEN_PER_USD / 10) * 10:,} 円、1 USD＝{YEN_PER_USD} 円で換算（10 円単位）", "src": "pipeline_state.json spent_usd"},
        ],
    },
    "ops": {"start": starts, "resume": resumes, "review": reviews, "note": "画面には出さない（利用者の直しで行を消した）。記録として残す"},
    "photos": n_photos, "requests": c.get("requests"),
    "foot": f"長さの実測はまだ無く、推定の模型と撮影依頼 {c.get('requests')} 件まで。製図で 1 回、人が決定を手で入れた",
    "foot_manual_src": "D:/BuiltwithClaude_run0/data/decisions.json の D-0001（17:34 に利用者が手で入れた。台帳の書いた人は shinko と記録）",
    "foot_src": f"report.json counts（measured_kinds：direct {mk.get('direct')}・chain {mk.get('chain')}・count {mk.get('count')}）",
    "yen_per_usd": YEN_PER_USD,
    "as_of": st.get("updated_at"),
    "status": st.get("status"),
    "cap": f"D:/BuiltwithClaude_run0/out/honbu/pipeline_state.json・report.json（{st.get('updated_at')} の値、status {st.get('status')}）／README.ja.md／docs/動画_数字の出典_3D相場_11-08.md",
}
(HERE / "values.json").write_text(json.dumps(v, ensure_ascii=False, indent=1), encoding="utf-8")
if False: (HERE / "timeline.json").write_text(json.dumps({"note": "係 U（無音）", "fps": 30, "size": [1920, 1080], "total": v["dur"]}, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({k: v[k] for k in ("foot", "as_of", "status")}, ensure_ascii=False))
print([r["n"] for r in v["after"]["rows"]])
