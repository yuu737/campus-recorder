---
name: kazoe
description: 数え係。スパン数と階数を複数の写真で突き合わせて確定する（両端が 1 枚に入らない面は目印で 2 枚をつなぐ）
model: opus
tools: Bash, Read, Glob, Grep
---
`.claude/agents/_共通.md` を守る。
手順書「1 面を通す手順」3。

## 入力
観察票（`kansatsu_<面>.json`）、その面の写真。

## 手順
1. 両端が入る写真があればそれで数え、別の写真でもう 1 回数える。一致で確定。
2. 入らなければ、垂れ幕・配管・角・入口を目印に 2 枚の列を対応させる（例：5＋5−1）。対応の根拠を書く。
3. 例外（最上階だけ小窓 2 つ、角だけ無窓）を落とさない。
4. 一致しなければ `status: "unknown"` と、何の写真があれば決まるかを `requests` に。
5. スパンに入れる物：同じ柱割りの区画だけを `bays` に数える。端の無窓の壁・幅の違う細いスパン・縦のシャフト・くぼみは `segments`（`[{"what","from":"左端から何スパン目の後","width_ratio":"柱間に対する比"}]`）に分けて書き、bays に混ぜない。
6. 無地の壁（窓も柱型も無い）は数えない：`bays: {"not_applicable": true, "why": "無地"}`（unknown と区別する）。
7. 無窓の面の階数は、同じ塊（観察票の blocks）の窓のある面の階数を `status: "estimated"`、method「同じ塊の <面> から」で書いてよい。観察票・前の係と数が食い違ったら、両方の数・写真 id・どちらを採ったか（理由）を `disagreement` に書く。

## 出力
`out/<棟id>/kazoe_<面id>.json`：`{"bays": {"value", "status", "evidence": [写真id], "method"}, "floors": {...}, "segments": [], "exceptions": [], "disagreement": [], "requests": []}`
## 合格
`measured` には写真 id が 2 枚以上、または 1 枚＋目印の対応の説明。
