"""デモ版の場面の設定（values.json）と仮の文（narration.txt）を書く。座標・時刻・出典はここが元。

    python -I -X utf8 scripts/video/demo/scenes.py      # 全部の場面の values.json・narration.txt・index.html を書き、時刻表も作る

座標は録画の座標（1920×1080）。待機画面の再生は 1600×900 の録画を 1920×1080 に拡大した連番（out/video/demo/replay_f、
ffmpeg の lanczos。f00001 が 0 秒）。数字は録画の画面に出ている物だけを、出典（録画の秒）付きで values に書く。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RP = "out/video/demo/replay_f"
RCAP = "待機画面の再生（web/progress.html?replay=1）本部棟の記録・11/8 12:24 録画・等速"
RSRC = "out/video/replay_honbu.mp4"


def clip(t0, t1, frm, d=RP, fps=30, base=1):
    return {"t0": t0, "t1": t1, "dir": d, "fps": fps, "from": frm, "rate": 1, "base": base}


def who(t0, t1, name, model, step=""):
    return {"t0": t0, "t1": t1, "name": name, "model": model, "step": step}


def spot(t0, t1, x, y, w, h, tag=None, tx=None, ty=None, dark=0.5):
    s = {"t0": t0, "t1": t1, "x": x, "y": y, "w": w, "h": h, "dark": dark}
    if tag:
        s.update(tag=tag)
    if tx is not None:
        s.update(tx=tx)
    if ty is not None:
        s.update(ty=ty)
    return s


SCENES = {}

# 01 職員の画面：撮った連番（scripts/video/capture_staff.py、作業用の複製の serve 8791、API キーなし）。カーソルは撮った時に描いた物
SCENES["01_staff"] = dict(
    note="B デモ 01 職員の画面。out/video/flow/staff_cap（30 fps、f00000 が 0 秒）を等速で。クリックの時刻と位置は log.json",
    dur=14.0,
    values={"clicks": {"value": "1.3・3.65（写真 4 枚を落とす）・7.65・9.4・12.2（記録を始める）秒", "source": "out/video/flow/staff_cap/log.json steps"},
            "photos": {"value": "PXL_20261103_043026097 ほか 3 枚（公開版）", "source": "out/video/flow/staff_cap/log.json steps[1].drop"}},
    narration=["職員は、撮った写真を画面に落として、", "「記録を始める」を押すだけです。"],
    scene=dict(
        clips=[clip(0, 12.6, 0.4, "out/video/flow/staff_cap", 30, 0), clip(12.6, 14.0, 14.6, "out/video/flow/staff_cap", 30, 0)],
        cam=[[0, 1.0, 960, 540], [0.5, 1.22, 620, 380], [2.6, 1.22, 620, 380], [3.4, 1.2, 768, 640], [5.6, 1.2, 768, 640],
             [6.6, 1.3, 720, 500], [10.0, 1.3, 660, 520], [11.0, 1.4, 1050, 590], [12.59, 1.4, 1050, 590], [12.6, 1.0, 960, 540]],
        who=[who(0.2, 14.1, "職員の画面", "web/staff.html", "大学の職員")],
        fuda=[{"t0": 2.4, "t1": 9.9, "text": "撮った写真を落として、棟の名前を付ける"},
              {"t0": 10.6, "t1": 14.1, "text": "「記録を始める」を押すだけ", "small": "→ 待機画面へ"}],
        spots=[spot(0.6, 1.9, 388, 320, 380, 54, "大学名", dark=0.4),
               spot(3.0, 5.6, 386, 600, 764, 220, "写真を落とす（4 枚）", dark=0.4),
               spot(6.9, 8.6, 668, 461, 350, 52, "棟の名前", dark=0.4),
               spot(8.8, 10.3, 402, 513, 174, 56, "写真に付ける", dark=0.4),
               spot(11.0, 12.3, 1010, 564, 142, 59, "記録を始める", 900, 490, dark=0.45)],
        clicks=[[0.9, 517.5, 346.4], [6.05, 543.0, 490.4], [7.25, 763.1, 490.4], [9.0, 488.7, 540.8], [11.8, 1081.9, 597.6]],
        caption=[{"t0": 0.2, "t1": 14.1, "text": "職員の画面（web/staff.html）を作業用の複製で操作して撮影・API キーなしの試走・等速（押した後の読み込み 2 秒を切った）"}],
        flash=[0, 12.6],
    ),
)

SCENES["02_photo"] = dict(
    note="B デモ 02 写真→仕分け。待機画面の再生の録画をそのまま全面に",
    dur=11.5,
    values={"photos_new": {"value": "33 枚のうち 30 枚は使える", "source": f"{RSRC} t=10s 右下の係の一言（撮影係 Claude Haiku・1:43）"},
            "photos_total": {"value": "121", "source": f"{RSRC} t=10s 右の『写真』"},
            "faces": {"value": "f6 41・f8 24・f0 18・面なし 16・f1 9・f5 5・f4 4・f7 4", "source": f"{RSRC} t=10s 仕分けの欄の下"},
            "models": {"value": "撮影係 Claude Haiku／仕分け係 Claude Opus", "source": f"{RSRC} の見出し（1/11・2/11）"}},
    narration=["撮影係が写真を確かめ、", "仕分け係が、どの面の写真かを決めます。"],
    scene=dict(
        clips=[clip(0, 11.5, 0.3)],
        cam=[[0, 1.0, 960, 540], [0.8, 1.0, 960, 540], [2.6, 1.25, 1500, 620], [5.4, 1.25, 1500, 620], [6.2, 1.0, 960, 540],
             [7.4, 1.18, 760, 300], [9.2, 1.18, 760, 300], [10.6, 1.25, 600, 760]],
        who=[who(0.3, 5.6, "撮影係", "Claude Haiku", "1/11"), who(5.9, 11.6, "仕分け係", "Claude Opus", "2/11")],
        fuda=[{"t0": 0.9, "t1": 5.4, "text": "届いた写真が使えるかを数秒で返す"},
              {"t0": 6.4, "t1": 11.6, "text": "どの棟の、どの面かを目で見て決める", "small": "面に f6・f1… の印"}],
        spots=[spot(3.0, 5.3, 1505, 636, 392, 128, "33 枚のうち 30 枚は使える", 1180, 570, 0.45),
               spot(7.6, 9.2, 28, 186, 1450, 160, "面の印", 40, 120, 0.45),
               spot(9.4, 11.4, 26, 822, 560, 40, "121 枚を面ごとに", 30, 760, 0.5)],
        caption=[{"t0": 0.2, "t1": 11.6, "text": RCAP}],
        flash=[0],
    ),
)

SCENES["03_sokuryo"] = dict(
    note="B デモ 03 正面化→設計。再生の 12.3〜19.7 秒と 20.3〜26.1 秒",
    dur=13.0,
    values={"models": {"value": "測量係 Claude Opus（5/11）／設計係 Claude Opus（6/11）", "source": f"{RSRC} の見出し"},
            "counts": {"value": "measured 7・estimated 456・unknown 2", "source": f"{RSRC} t=23s 設計の『値の状態（棟 JSON の全部の値）』"},
            "chain": {"value": "本部棟正面柱92cm → uniform_period → count → 5.30973 m", "source": f"{RSRC} t=23s 『根拠の鎖』faces[f6].bays.pitch"}},
    narration=["測量係が写真を真正面に直して寸法を測り、", "設計係が、根拠付きの値だけを棟の記録に書きます。"],
    scene=dict(
        clips=[clip(0, 7.4, 12.3), clip(7.4, 13.0, 20.3)],
        cam=[[0, 1.0, 960, 540], [2.2, 1.18, 1100, 500], [7.3, 1.2, 1100, 500], [7.4, 1.0, 960, 540], [8.6, 1.22, 520, 360],
             [10.4, 1.22, 520, 360], [11.4, 1.25, 1080, 420], [13, 1.25, 1080, 420]],
        who=[who(0.2, 7.3, "測量係", "Claude Opus", "5/11"), who(7.5, 13.1, "設計係", "Claude Opus", "6/11")],
        fuda=[{"t0": 0.6, "t1": 7.1, "text": "写真を真正面に直して、柱の間隔・階の高さを測る"},
              {"t0": 7.9, "t1": 13.1, "text": "値は全部、根拠付き", "small": "測った・推定・分からない を分けて書く"}],
        spots=[spot(3.0, 7.0, 718, 318, 756, 376, "正面化した絵", 730, 254, 0.45),
               spot(8.6, 10.6, 36, 194, 708, 232, "measured 7 ／ estimated 456 ／ unknown 2", 40, 440, 0.5),
               spot(10.9, 12.9, 766, 194, 710, 412, "根拠の鎖：メジャー → 周期 → 個数 → 5.30973 m", 560, 620, 0.5)],
        caption=[{"t0": 0.2, "t1": 13.1, "text": RCAP + "（2 か所を切って繋いだ）"}],
        flash=[0, 7.4],
    ),
)

SCENES["04_seizu"] = dict(
    note="B デモ 04 立面図。再生の 27.3〜32.8 秒と、37.0 秒（f4 立面図）の 1 枚で止めた 6.5 秒。製図・模型係はコード（Claude ではない）",
    dur=12.0,
    values={"model": {"value": "製図・模型係（コード）", "source": f"{RSRC} t=30s の見出し（7/11）"},
            "label": {"value": "線ごとに（推定）と写真番号", "source": f"{RSRC} t=37s f4 立面図の注記"}},
    narration=["図面は、棟の記録からコードが自動で描きます。", "線の一本ずつに、根拠の写真番号が付きます。"],
    scene=dict(
        clips=[clip(0, 5.5, 27.3), dict(clip(5.5, 12.0, 37.0), rate=0)],
        cam=[[0, 1.0, 960, 540], [1.4, 1.15, 760, 480], [5.4, 1.15, 760, 480], [5.5, 1.0, 960, 540], [6.8, 1.3, 900, 440], [11.8, 1.36, 960, 450]],
        who=[who(0.2, 12.1, "製図・模型係", "コード", "7/11")],
        fuda=[{"t0": 0.6, "t1": 5.3, "text": "棟の記録から図面を自動で描く", "small": "手で直さない"},
              {"t0": 6.0, "t1": 12.1, "text": "線の 1 本ずつに、根拠の写真番号"}],
        spots=[spot(1.6, 5.3, 342, 200, 836, 554, "四面図・屋根伏せ・鳥瞰", 350, 136, 0.45),
               spot(7.0, 11.8, 600, 284, 708, 346, "推定の値 ±誤差 と 写真番号", 610, 220, 0.45)],
        caption=[{"t0": 0.2, "t1": 12.1, "text": RCAP.replace("・等速", "") + "（前半は等速、後半は f4 の立面図の 1 枚で止めた）"}],
        flash=[0, 5.5],
    ),
)

SCENES["05_kenshu"] = dict(
    note="B デモ 05 製図板→検収。再生の 42.4〜48.3 秒と 49.4〜57.4 秒",
    dur=14.0,
    values={"models": {"value": "測量係 Claude Opus（5/11 製図板）／検収係 Claude Opus（9/11）", "source": f"{RSRC} の見出し"},
            "kenshu": {"value": "許容 ±0.15 m に 39 / 49 本", "source": f"{RSRC} t=56s 検収の欄"},
            "dev": {"value": "+0.43・+0.28・+0.27・+0.19・+0.16（m、写真の上で測ったずれ）", "source": f"{RSRC} t=56s 検収の欄"}},
    narration=["写真を面に貼って製図板にし、", "検収係が、模型と写真を重ねてずれを測ります。"],
    scene=dict(
        clips=[clip(0, 6.0, 42.4), clip(6.0, 14.0, 49.4)],
        cam=[[0, 1.0, 960, 540], [1.4, 1.12, 760, 520], [5.9, 1.12, 760, 520], [6.0, 1.0, 960, 540], [8.8, 1.12, 700, 520],
             [11.4, 1.12, 700, 520], [12.3, 1.3, 1250, 430], [14, 1.3, 1250, 430]],
        who=[who(0.2, 5.9, "測量係", "Claude Opus", "5/11 製図板"), who(6.1, 14.1, "検収係", "Claude Opus", "9/11")],
        fuda=[{"t0": 0.6, "t1": 5.8, "text": "写真を面に貼って、面の写真（製図板）に", "small": "掲示物は灰色の箱"},
              {"t0": 6.4, "t1": 14.1, "text": "模型と写真を重ねて、ずれを測る"}],
        spots=[spot(1.5, 5.7, 118, 196, 1288, 624, "面ごとの写真（製図板）", 130, 132, 0.4),
               spot(6.8, 8.6, 32, 266, 1444, 498, "左：写真　右：同じ視点から描いた模型", 40, 202, 0.4),
               spot(12.2, 13.9, 1014, 202, 460, 448, "許容 ±0.15 m に 39 / 49 本", 1000, 660, 0.5)],
        caption=[{"t0": 0.2, "t1": 14.1, "text": RCAP + "（2 か所を切って繋いだ）"}],
        flash=[0, 6.0],
    ),
)

SCENES["06_web3d"] = dict(
    note="B デモ 06 証拠→歩ける 3D。再生の 58.6〜63.1 秒と 64.4〜69.9 秒",
    dur=10.0,
    values={"models": {"value": "目印係 Claude Opus（8/11）／製図・模型係 コード（11/11）", "source": f"{RSRC} の見出し"},
            "registered": {"value": "登録した写真 42", "source": f"{RSRC} t=68s 『今の記録』"},
            "cost": {"value": "費用 13.43 / 40 USD", "source": f"{RSRC} 見出しの右（本部棟の記録の費用と上限）"},
            "agent_time": {"value": "係の秒の合計 2:29:11", "source": f"{RSRC} 見出しの右"}},
    narration=["写真と模型を重ねて、歩ける 3D に書き出します。", "本部棟の記録にかかった費用は 13 ドルほどです。"],
    scene=dict(
        clips=[clip(0, 4.5, 58.6), clip(4.5, 10.0, 64.4)],
        cam=[[0, 1.0, 960, 540], [1.0, 1.15, 560, 450], [4.4, 1.15, 560, 450], [4.5, 1.0, 960, 540], [5.8, 1.15, 700, 450],
             [7.6, 1.15, 700, 450], [8.4, 1.25, 1500, 240], [10, 1.25, 1500, 240]],
        who=[who(0.2, 4.4, "目印係", "Claude Opus", "8/11"), who(4.6, 10.1, "製図・模型係", "コード", "11/11")],
        fuda=[{"t0": 0.5, "t1": 4.3, "text": "写真の目印から視点を解いて、模型に重ねる"},
              {"t0": 4.9, "t1": 10.1, "text": "歩ける 3D（glb）に書き出す"}],
        spots=[spot(1.2, 4.2, 194, 202, 672, 482, "写真と模型の位置合わせ", 200, 690, 0.45),
               spot(5.8, 7.9, 930, 202, 544, 280, "写真 121・登録 42・推定 456・不明 2", 900, 490, 0.45),
               spot(8.4, 9.9, 1500, 14, 192, 46, "費用 13.43 USD（上限 40）", 1260, 76, 0.5)],
        caption=[{"t0": 0.2, "t1": 10.1, "text": RCAP + "（2 か所を切って繋いだ）"}],
        flash=[0, 4.5],
    ),
)


def walk_scene() -> dict:
    log = json.loads((HERE.parents[2] / "out/video/demo/walk_cap/log.json").read_text(encoding="utf-8"))
    fps = log["fps"]
    b = log["evbtn"]
    bx, by = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
    tev = log["ev_frame"] / fps
    tcard = log.get("card_frame", 10 ** 6) / fps
    cx, cy = log.get("card_click", [900, 420])
    dur = round(log["frames"] / fps, 2)
    cur = [[tev - 1.6, 760, 640], [tev - 0.1, bx, by], [tev, bx, by, "click"]]
    if tcard < dur:
        cur += [[tcard - 1.2, bx + 40, by + 60], [tcard - 0.1, cx, cy], [tcard, cx, cy, "click"], [dur, cx + 10, cy + 10]]
    return dict(
        note="B デモ 07 歩く画面。web/index.html を読むだけの一時配信で開き、1 コマずつ撮った（scripts/video/demo/capture_walk.py）。"
             "「根拠で見る」と棟の札は本物のクリック（CDP のマウス）。偽のカーソルはその位置に描いた",
        dur=dur,
        values={"ev_click": {"value": f"{tev:.2f} 秒に『根拠で見る』", "source": "out/video/demo/walk_cap/log.json ev_frame・evbtn"},
                "card_click": {"value": f"{tcard:.2f} 秒に棟を押して札", "source": "out/video/demo/walk_cap/log.json card_frame・card_click"}},
        narration=["受験生は、スマホで本部棟の前を歩けます。", "「根拠で見る」を押すと、推定の所と、まだ分からない所が色で分かります。"],
        scene=dict(
            clips=[clip(0, dur, 0, "out/video/demo/walk_cap", fps, 0)],
            cam=[[0, 1.0, 960, 540], [tev - 0.4, 1.0, 960, 540], [tev + 0.6, 1.12, 700, 420], [dur, 1.12, 760, 420]],
            who=[who(0.2, dur + 0.1, "歩く画面", "web/index.html", "受験生のスマホ")],
            fuda=[{"t0": 0.6, "t1": tev - 0.4, "text": "本部棟の前を歩ける"},
                  {"t0": tev + 0.4, "t1": (tcard - 0.3 if tcard < dur else dur + 0.1), "text": "根拠で見る", "small": "橙＝推定　灰＝まだ分からない"}]
                 + ([{"t0": tcard + 0.4, "t1": dur + 0.1, "text": "棟を押すと札", "small": "measured・estimated・unknown の数"}] if tcard < dur else []),
            spots=[spot(tev - 0.9, tev + 0.5, b["x"] - 8, b["y"] - 8, b["width"] + 16, b["height"] + 16, "根拠で見る", b["x"], b["y"] + b["height"] + 16, 0.35)],
            cursor=cur,
            caption=[{"t0": 0.2, "t1": dur + 0.1, "text": f"歩く画面（web/index.html）を 1 コマずつ撮影・headless Edge のソフト描画・{fps} コマ/秒"}],
            flash=[0],
        ),
    )


def main() -> None:
    names = sys.argv[1:]
    sc = dict(SCENES)
    if (HERE.parents[2] / "out/video/demo/walk_cap/log.json").exists():
        try:
            sc["07_walk"] = walk_scene()
        except KeyError as e:
            print("07_walk は log.json が途中（", e, "）")
    for name, s in sc.items():
        if names and name not in names:
            continue
        d = HERE / name
        d.mkdir(exist_ok=True)
        narr = s.pop("narration")
        (d / "values.json").write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
        (d / "narration.txt").write_text("\n".join(narr) + "\n", encoding="utf-8")
        shutil.copy(HERE / "_shell" / "index.html", d / "index.html")
        subprocess.run([sys.executable, "-I", "-X", "utf8", str(HERE / "make_demo.py"), name], check=True)


if __name__ == "__main__":
    main()
