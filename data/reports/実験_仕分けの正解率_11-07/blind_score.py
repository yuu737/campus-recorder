"""仕分けの正解率：out_1〜3.json を正解・GPS だけ・方位だけと比べる。使い方: python blind_score.py <blind フォルダ>"""
import collections
import json
import sys
from pathlib import Path

d = Path(sys.argv[1])
A = json.load(open(d / "answer_DO_NOT_SHOW.json", encoding="utf-8"))
B = json.load(open(d / "baseline.json", encoding="utf-8"))
C = {}
for i in (1, 2, 3):
    p = d / f"out_{i}.json"
    if p.exists():
        C.update(json.load(open(p, encoding="utf-8"))["photos"])
ids = [x for x in A if x in C]
print(f"答えた写真 {len(ids)} / {len(A)}")

# 棟
rows = collections.Counter()
conf_wrong = collections.Counter()
for x in ids:
    t, c = A[x]["building"], C[x].get("building")
    k = "正解" if c == t else ("決められない" if c in (None, "null") else "間違い")
    rows[k] += 1
    if k == "間違い":
        conf_wrong[C[x].get("confidence")] += 1
gps = sum(1 for x in ids if B[x]["gps_building"] == A[x]["building"])
print(f"棟：Claude 正解 {rows['正解']}・決められない {rows['決められない']}・間違い {rows['間違い']}（間違いの確からしさ {dict(conf_wrong)}）／ GPS だけ 正解 {gps}")
for b in ("honbu", "tower75", "kyotsu-minami"):
    s = [x for x in ids if A[x]["building"] == b]
    print(f"  {b}: Claude {sum(1 for x in s if C[x].get('building') == b)}/{len(s)}  GPS だけ {sum(1 for x in s if B[x]['gps_building'] == b)}/{len(s)}")
print("  棟の間違い:", [(x, A[x]["building"], C[x].get("building"), C[x].get("confidence")) for x in ids
                    if C[x].get("building") not in (None, "null", A[x]["building"])])

# 面（視点を解いて登録した写真の主な面だけ）
fx = [x for x in ids if A[x]["face"]]
fr = collections.Counter()
fconf = collections.Counter()
for x in fx:
    t, c = A[x]["face"], C[x].get("face")
    if C[x].get("building") != A[x]["building"]:
        k = "棟が違う"
    elif c in (None, "null"):
        k = "決められない"
    else:
        k = "正解" if c == t else "間違い"
    fr[k] += 1
    fconf[(k, C[x].get("confidence"))] += 1
hb = sum(1 for x in fx if B[x]["heading_face"] == A[x]["face"])
print(f"面（{len(fx)} 枚）：Claude {dict(fr)} ／ 方位だけ（棟は正解を渡して）正解 {hb}")
print("  確からしさ別:", dict(fconf))
print("  面の間違い:", [(x, A[x]["building"], A[x]["face"], C[x].get("face"), C[x].get("confidence")) for x in fx
                    if C[x].get("building") == A[x]["building"] and C[x].get("face") not in (None, "null", A[x]["face"])])
print("確からしさの内訳（全体）:", dict(collections.Counter(C[x].get("confidence") for x in ids)))
