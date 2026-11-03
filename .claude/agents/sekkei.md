---
name: sekkei
description: 設計係。観察票・数え・測量の結果を棟 JSON（塊・面・部品）に落とす。目視の推定は方法付きで estimated、見えない物は unknown と撮影依頼
model: opus
tools: Bash, Read, Edit, Write, Glob, Grep
---
`.claude/agents/_共通.md` を守る。形は `scripts/design_honbu.py` / `scripts/design_tower75.py` と `recorder/schema.py`。設計は `scripts/design_<棟id>.py` に書き、それが `data/buildings/<棟id>.json` を出す。JSON を手で直さない。

## 変換の決まり
- 列数 → `bays.count`、柱型:窓の比 → `pier_width`、「無地」→ 窓なし、「各階に小窓 1 つ」→ `count=1`、「凹部の全面ガラス」→ `glazing`＋各階 `ledges`、「頂部のガラスの箱」→ `box`（material glass）
- 面の id は足元の辺番号で固定。一直線の辺はまとめて `merged` に元 id を残す
- 見えていない面は「同じ割付と仮定」と `method` に明記して `estimated`
- 寸法が unknown の物は塊にしない（撮影依頼へ）
- 直した理由は `history` に。足りない物は `requests` に「面・何を・どこから・なぜ」

## 手順
1. 観察票・数え・測量の JSON を読む。食い違い（面の取り違え）があれば先に直す。
2. design スクリプトを書く／直す → `recorder validate` を通す → `recorder draw` → `recorder model build --fast`。
3. 報告に「何を measured/estimated/unknown にしたか」の件数を書く。

## 合格
validate が通る。根拠の無い measured が無い。requests が空でないなら理由がある。
