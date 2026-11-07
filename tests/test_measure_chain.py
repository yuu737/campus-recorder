"""K-101 の根拠の鎖：タイルの周期（メジャー）→ 面の周期の一様 → 枚数で長さ、tol の積み方、縮尺の付け替え。

本部棟の『本部棟正面柱92cm』（f6）を例にするが、目盛りの読みは仮の値（本当の読みは測量係が拡大画像で読む）。
棟 JSON・物差し台帳の本物は書かない（一時フォルダの写しだけ）。
"""
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest

from recorder import __main__ as cli
from recorder import measure

REPO = Path(__file__).resolve().parent.parent
TAPE = "本部棟正面柱92cm"
F6 = "PXL_20261103_042900775"
READ = "0→94.4"          # 仮の読み（小口 8 枚。本当の値は係が読む）


def tiles(width=3000, height=600, pitch=38.25, chirp=0.0):
    """縦の目地が pitch px ごとにある壁（chirp > 0 なら左から右へ周期が伸びる＝一様でない）。"""
    img = np.full((height, width), 180, np.uint8)
    x = 0.0
    while x < width:
        i = int(round(x))
        img[:, i:i + 3] = 110
        x += pitch * (1 + chirp * x / width)
    return img


def test_tape_link_matches_the_example_in_the_instructions():
    ln = measure.tape_link(TAPE, "0→91.0", 4, "tile_pitch_h")
    assert ln == {"link": "tape", "photo": TAPE, "read": "0→91.0 cm", "n": 4, "what": "tile_pitch_h",
                  "value": 0.2275, "tol": 0.00125}
    assert measure.parse_read("12.5 -> 30 cm")[:2] == (12.5, 30.0)
    for bad in ("91.0", "30→12", "a→b"):
        with pytest.raises(ValueError):
            measure.parse_read(bad)
    with pytest.raises(ValueError):
        measure.tape_link(TAPE, "0→91", 0, "tile_pitch_h")


def test_chain_tol_formula():
    chain = [
        {"link": "tape", "photo": TAPE, "read": "0→91.0 cm", "n": 4, "what": "tile_pitch_h", "value": 0.2275, "tol": 0.00125},
        {"link": "uniform_period", "photo": F6, "px": [38.1, 38.4, 38.2, 38.3], "what": "一様"},
        {"link": "count", "photo": F6, "n": 24, "what": "枚数", "tol": 1},
        {"link": "px_ratio", "photo": F6, "px": [180, 910], "what": "比"},
    ]
    v = 0.2275 * 24 * 180 / 910
    rel = 0.00125 / 0.2275 + 0.3 / (2 * 38.25) + 1 / 24 + 1 / 180 + 1 / 910
    assert measure.chain_tol(chain, v) == pytest.approx(v * rel)
    # tape 1 輪だけ＝周期そのものの ±
    assert measure.chain_tol(chain[:1], 0.2275) == pytest.approx(0.00125)
    # count の ± は ±1 枚 × 周期
    assert measure.chain_tol([chain[2]], 0.2275 * 24) == pytest.approx(0.2275)
    # 測っていない輪は ± に足さない
    assert measure.chain_tol([{"link": "claim", "what": "申告"}], 1.0) == 0


def test_uniform_period_accepts_uniform_and_rejects_chirp():
    r = measure.uniform_period(tiles(), axis="x", parts=4)
    assert r["ok"], r
    assert r["period_px"] == pytest.approx(38.25, abs=0.15)
    assert len(r["px"]) == 4 and all(abs(p - 38.25) < 0.4 for p in r["px"])
    ln = measure.uniform_link(r, photo=F6, face="f6", what="tile_pitch_h")
    assert ln["link"] == "uniform_period" and ln["photo"] == F6 and ln["px"] == r["px"]
    bad = measure.uniform_period(tiles(chirp=0.08), axis="x", parts=4)
    assert not bad["ok"] and bad["spread"] > 0.01 and "estimated" in bad["reason"]
    assert measure.uniform_link(bad, photo=F6, face="f6", what="tile_pitch_h") is None


def _pitch(tmp_path, building="honbu", face="f6", read=READ, n=8, photo=TAPE):
    ln = measure.tape_link(photo, read, n, "tile_pitch_h")
    measure.write_tape(ln, building=building, face=face, reports=tmp_path)
    return measure.pitch_summary(building, "tile_pitch_h", tmp_path)


def test_honbu_f6_tape_to_bay_pitch_chain_is_measured(tmp_path):
    s = _pitch(tmp_path)
    assert s["value"] == pytest.approx(0.118) and s["tol"] == pytest.approx(0.000625)
    uni = measure.uniform_link(measure.uniform_period(tiles(), axis="x"), photo=F6, face="f6", what="tile_pitch_h")
    m = measure.build_chain(building="honbu", face="f6", pitch=s, uniform=uni,
                            count={"photo": F6, "n": 46, "what": "柱型の芯から芯のタイル枚数"})
    assert m["status"] == "measured" and m["measured_kind"] == "chain"
    assert [ln["link"] for ln in m["chain"]] == ["tape", "uniform_period", "count"]
    assert all(ln.get("photo") for ln in m["chain"])
    assert m["value"] == pytest.approx(0.118 * 46, abs=1e-4)
    assert m["tol"] >= measure.chain_tol(m["chain"], m["value"]) - 1e-12
    assert m["evidence"] == [TAPE, F6]
    # 第 1 部の形：value / unit / tol / status / measured_kind / evidence / method / chain
    assert set(m) == {"value", "unit", "tol", "status", "measured_kind", "evidence", "method", "chain"}


def test_direct_when_only_tape(tmp_path):
    m = measure.build_chain(building="honbu", face="f6", pitch=_pitch(tmp_path))
    assert m["status"] == "measured" and m["measured_kind"] == "direct" and len(m["chain"]) == 1
    assert m["tol"] >= 0.000625


def test_estimated_cases(tmp_path):
    s = _pitch(tmp_path)
    cnt = {"photo": F6, "n": 46, "what": "枚数"}
    # 一様の根拠が無い
    m = measure.build_chain(building="honbu", face="f6", pitch=s, count=cnt)
    assert m["status"] == "estimated" and "measured_kind" not in m
    assert any(ln["link"] == "assume" for ln in m["chain"])
    # 別の面
    uni = {"link": "uniform_period", "photo": "x", "px": [38.2, 38.3, 38.25]}
    m = measure.build_chain(building="honbu", face="f4", pitch=s, count=cnt, uniform=uni)
    assert m["status"] == "estimated"
    # 別の棟の周期（申告の輪）
    m = measure.build_chain(building="library", face="f2", pitch=s, count=cnt, uniform=uni)
    assert m["status"] == "estimated" and m["chain"][1]["link"] == "claim"
    assert m["tol"] >= measure.chain_tol(m["chain"], m["value"]) - 1e-12


def test_second_tape_on_same_face_stands_in_for_uniform(tmp_path):
    _pitch(tmp_path)
    s = _pitch(tmp_path, read="10→57.3", n=4, photo="PXL_20261107_999999999")   # 仮の 2 枚目（差 +0.2 %）
    assert s["photo"] == TAPE and len(s["checks"]) == 1 and s["checks"][0]["ok"] and not s["warnings"]
    m = measure.build_chain(building="honbu", face="f6", pitch=s, count={"photo": F6, "n": 46})
    assert m["status"] == "measured" and m["chain"][1]["link"] == "uniform_period"
    assert m["chain"][1]["photo"] == "PXL_20261107_999999999"


def test_pitch_disagreement_warns(tmp_path):
    _pitch(tmp_path)
    s = _pitch(tmp_path, read="0→50.0", n=4, photo="PXL_bad")       # 0.125 m：± の和を超える
    assert s["warnings"] and not s["checks"][0]["ok"]


def test_rescale_against_previous_plateau_value(tmp_path):
    (tmp_path / "honbu").mkdir()
    shutil.copy(REPO / "data" / "reports" / "honbu" / "sokuryo_f6.json", tmp_path / "honbu" / "sokuryo_f6.json")
    s = _pitch(tmp_path)
    m = measure.build_chain(building="honbu", face="f6", pitch=s, count={"photo": F6, "n": 46})
    prev = measure._prev_value("honbu", "f6", "bay_pitch", tmp_path)
    assert prev["value"] == 5.418 and prev["ruler"] == "plateau_wall"
    rs = measure.rescale_record(m, prev)
    assert rs["diff_pct"] == pytest.approx((m["value"] - 5.418) / 5.418 * 100, abs=0.01)
    p = measure.write_chain("honbu", "f6", "bay_pitch", m, rescale=rs, reports=tmp_path)
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["values"]["bay_pitch"] == m and d["rescale"]["bay_pitch"]["prev_value"] == 5.418


def test_write_ruler_on_a_copy_keeps_format_and_other_rulers(tmp_path, monkeypatch):
    monkeypatch.setenv("RECORDER_LOCK_DIR", str(tmp_path / "locks"))
    rul = tmp_path / "rulers.json"
    shutil.copy(REPO / "data" / "rulers.json", rul)
    before = json.loads(rul.read_text(encoding="utf-8"))
    s = _pitch(tmp_path / "rep")
    r = measure.write_ruler(s, rul)
    after = json.loads(rul.read_text(encoding="utf-8"))
    assert r["id"] == "tile_pitch_h_honbu" and r["value"] == pytest.approx(0.118) and r["status"] == "measured"
    assert r["evidence"] == [TAPE] and r["chain"][0]["link"] == "tape"
    old = {x["id"]: x for x in before["rulers"]}
    for x in after["rulers"]:
        if x["id"] != "tile_pitch_h_honbu":
            assert x == old[x["id"]]
    # 2 回書いても項目は 1 つ
    measure.write_ruler(s, rul)
    assert sum(x["id"] == "tile_pitch_h_honbu" for x in json.loads(rul.read_text(encoding="utf-8"))["rulers"]) == 1
    assert rul.read_text(encoding="utf-8").count("\n    {") == len(after["rulers"])   # 1 物差し 1 行


def test_cli_tape_uniform_chain(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RECORDER_LOCK_DIR", str(tmp_path / "locks"))
    rep = tmp_path / "rep"
    assert cli.main(["measure", "tape", TAPE, "--read", READ, "--n", "8", "--what", "tile_pitch_h",
                     "--building", "honbu", "--face", "f6", "--reports", str(rep)]) == 0
    t = json.loads((rep / "honbu" / f"sokuryo_tape_{TAPE}.json").read_text(encoding="utf-8"))
    assert t["link"]["value"] == pytest.approx(0.118)
    jpg = tmp_path / "f6_front.jpg"
    cv2.imwrite(str(jpg), cv2.cvtColor(tiles(), cv2.COLOR_GRAY2BGR))
    assert cli.main(["measure", "uniform", str(jpg), "--what", "tile_pitch_h", "--photo", F6, "--face", "f6"]) == 0
    uj = tmp_path / "f6_front_uniform_tile_pitch_h.json"
    assert json.loads(uj.read_text(encoding="utf-8"))["link"]["link"] == "uniform_period"
    assert cli.main(["measure", "chain", "honbu", "--face", "f6", "--key", "bay_pitch", "--what", "tile_pitch_h",
                     "--count", "46", "--count-photo", F6, "--uniform", str(uj), "--reports", str(rep)]) == 0
    d = json.loads((rep / "honbu" / "sokuryo_chain_f6.json").read_text(encoding="utf-8"))
    m = d["values"]["bay_pitch"]
    assert m["status"] == "measured" and m["measured_kind"] == "chain"
    assert d["scale"]["bay_pitch"]["px_per_m"] == pytest.approx(38.25 / 0.118, rel=0.01)
    # 別の棟の周期は、その棟にメジャーが無い時だけ（estimated）
    assert cli.main(["measure", "chain", "library", "--face", "f2", "--key", "x", "--what", "tile_pitch_h",
                     "--pitch-from", "honbu", "--count", "10", "--count-photo", "L1", "--reports", str(rep)]) == 0
    lib = json.loads((rep / "library" / "sokuryo_chain_f2.json").read_text(encoding="utf-8"))
    assert lib["values"]["x"]["status"] == "estimated"
    # 台帳（写し）に書く
    rul = tmp_path / "rulers.json"
    shutil.copy(REPO / "data" / "rulers.json", rul)
    assert cli.main(["measure", "pitch", "honbu", "--what", "tile_pitch_h", "--write-rulers", "--rulers", str(rul),
                     "--reports", str(rep)]) == 0
    assert any(x["id"] == "tile_pitch_h_honbu" and x["value"] for x in json.loads(rul.read_text(encoding="utf-8"))["rulers"])
    assert cli.main(["measure", "tape", TAPE, "--read", "91", "--n", "4", "--what", "tile_pitch_h",
                     "--building", "honbu", "--reports", str(rep)]) == 2


def test_matches_the_checker_in_schema_if_present(tmp_path):
    """T1 の検証（recorder/schema.py）が鎖を読めれば、道具の出す値がそのまま通ること（式の突き合わせ）。"""
    from recorder import schema

    if not hasattr(schema, "chain_tol") or not hasattr(schema, "chain_problems"):
        pytest.skip("schema に鎖の検証がまだ無い")
    _pitch(tmp_path)
    s = _pitch(tmp_path, read="10→57.3", n=4, photo="PXL_20261107_999999999")
    uni = measure.uniform_link(measure.uniform_period(tiles(), axis="x"), photo=F6, face="f6", what="tile_pitch_h")
    cases = [
        measure.build_chain(building="honbu", face="f6", pitch=s),
        measure.build_chain(building="honbu", face="f6", pitch=s, uniform=uni, count={"photo": F6, "n": 46},
                            ratios=[{"photo": F6, "px": [180, 910], "what": "比"}]),
        measure.build_chain(building="honbu", face="f6", pitch=s, count={"photo": F6, "n": 46}),   # 2 枚目のメジャーで一様
        measure.build_chain(building="library", face="f2", pitch=s, count={"photo": F6, "n": 46}, uniform=uni),
    ]
    for m in cases:
        assert schema.chain_tol(m["chain"], m["value"]) <= m["tol"] + 1e-12
        assert measure.chain_tol(m["chain"], m["value"]) == pytest.approx(schema.chain_tol(m["chain"], m["value"]), rel=1e-3)
        assert schema.chain_problems(schema.Measurement.from_json(m), "t") == [], m


def test_threshold_3pct_and_drift_note():
    assert measure.UNIFORM_THR == 0.03
    r = measure.uniform_period(tiles(chirp=0.02), axis="x", parts=4)     # 左から右へ 2 % ほど伸びる
    assert r["ok"] and 0.01 < r["spread"] <= 0.03, r
    assert r["drift"]["direction"].startswith("左から右") and r["drift"]["percent"] > 0
    assert "歪み" in r["note"]
    ln = measure.uniform_link(r, photo=F6, face="f6", what="tile_pitch_h")
    assert ln["drift"] == r["drift"]
    # ばらつきの半幅は ± に入る
    assert measure.chain_tol([ln], 1.0) == pytest.approx((max(r["px"]) - min(r["px"])) / 2 / (sum(r["px"]) / 4))
    flat = measure.uniform_period(tiles(), axis="x", parts=4)
    assert flat["drift"] is None or abs(flat["drift"]["percent"]) < 0.5


def test_rescale_baseline_prefers_building_json(tmp_path):
    (tmp_path / "honbu").mkdir()
    shutil.copy(REPO / "data" / "reports" / "honbu" / "sokuryo_f6.json", tmp_path / "honbu" / "sokuryo_f6.json")
    bdir = tmp_path / "buildings"
    bdir.mkdir()
    b = json.loads((REPO / "data" / "buildings" / "honbu.json").read_text(encoding="utf-8"))   # 本物は読むだけ
    f6 = next(f for f in b["faces"] if f["id"] == "f6")
    f6["bays"]["pitch"]["value"] = 5.5                                       # 写しだけ変える
    (bdir / "honbu.json").write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
    pb = measure._baseline("honbu", "f6", "bay_pitch", tmp_path, bdir)
    assert pb["baseline"] == "building_json" and pb["value"] == 5.5 and pb["ref"].endswith("#faces[f6].bays.pitch")
    assert measure._baseline("honbu", "f6", "length", tmp_path, bdir)["baseline"] == "building_json"
    assert measure._baseline("honbu", "f6", "floor_pitch", tmp_path, bdir)["ref"].endswith("#floor_pitch")
    # 棟 JSON に無ければ sokuryo_<面>.json
    pr = measure._baseline("honbu", "f6", "first_floor", tmp_path, bdir)
    assert pr["baseline"] == "sokuryo_json" and pr["value"] == 4.8
    rs = measure.rescale_record({"value": 5.428, "status": "measured", "tol": 0.16}, pb)
    assert rs["baseline"] == "building_json" and rs["diff_pct"] == pytest.approx((5.428 - 5.5) / 5.5 * 100, abs=0.01)


def test_count_confirmed_by_second_photo(tmp_path, monkeypatch):
    from recorder import schema

    s = _pitch(tmp_path)
    uni = {"link": "uniform_period", "photo": F6, "px": [38.2, 38.3, 38.25]}
    one = measure.build_chain(building="honbu", face="f6", pitch=s, uniform=uni, count={"photo": F6, "n": 46})
    two = measure.build_chain(building="honbu", face="f6", pitch=s, uniform=uni,
                              count={"photo": F6, "n": 46, "confirmed_by": "PXL_20261103_042734715"})
    cl = two["chain"][-1]
    assert cl["confirmed_by"] == "PXL_20261103_042734715" and "PXL_20261103_042734715" in two["evidence"]
    assert measure.chain_tol([cl], two["value"]) == 0
    assert two["tol"] < one["tol"] and two["value"] == one["value"]
    if hasattr(schema, "chain_problems"):
        assert schema.chain_problems(schema.Measurement.from_json(two), "t") == []
    with pytest.raises(ValueError):
        measure.build_chain(building="honbu", face="f6", pitch=s, uniform=uni,
                            count={"photo": F6, "n": 46, "confirmed_by": F6})
    rep = tmp_path / "rep"
    monkeypatch.setenv("RECORDER_LOCK_DIR", str(tmp_path / "locks"))
    _pitch(rep)
    args = ["measure", "chain", "honbu", "--face", "f6", "--key", "bay_pitch", "--what", "tile_pitch_h",
            "--count", "46", "--count-photo", F6, "--reports", str(rep)]
    assert cli.main(args + ["--count-photo2", F6]) == 2
    assert cli.main(args + ["--count-photo2", "PXL_20261103_042734715"]) == 0
    d = json.loads((rep / "honbu" / "sokuryo_chain_f6.json").read_text(encoding="utf-8"))
    assert d["values"]["bay_pitch"]["chain"][-1]["confirmed_by"] == "PXL_20261103_042734715"


def bond(width=2400, height=2400, h=16.0, v=9.4):
    """馬踏み（段ごとに半枚ずれる）のタイル：横の周期 h px、縦の周期 v px。"""
    img = np.full((height, width), 180, np.uint8)
    for k in range(int(height / v) + 1):
        y0 = int(round(k * v))
        img[y0:y0 + 2, :] = 110
        off = 0.0 if k % 2 == 0 else h / 2
        x = off
        while x < width:
            img[y0:int(round((k + 1) * v)), int(round(x)):int(round(x)) + 2] = 110
            x += h
    return img


def test_ratio_from_h_gives_vertical_pitch(tmp_path, monkeypatch):
    from recorder import schema

    img = bond()
    rh = measure.uniform_period(img, axis="x", wall_box=[0, 1000, 2400, 1150], lo=6, hi=40, rowwise=True)
    rv = measure.uniform_period(img, axis="y", wall_box=[1000, 0, 1150, 2400], lo=6, hi=40, rowwise=True)
    assert rh["ok"] and rv["ok"] and rh["period_px"] == pytest.approx(16.0, abs=0.05) and rv["period_px"] == pytest.approx(9.4, abs=0.05)
    # 列ごとに足した並びでは馬踏みの横の周期が半分に出る（だから 1 行ずつ）
    assert measure.uniform_period(img, axis="x", wall_box=[0, 1000, 2400, 1150], lo=4, hi=40)["period_px"] == pytest.approx(8.0, abs=0.1)
    s = _pitch(tmp_path, read="5.4→86.2", n=7)                     # 0.115429 m
    q = {"photo": F6, "h_px": rh["span"]["px"], "n_h": rh["span"]["m"], "v_px": rv["span"]["px"], "n_v": rv["span"]["m"],
         "uniform_h": measure.uniform_link(rh, photo=F6, face="f6", what="tile_pitch_h"),
         "uniform_v": measure.uniform_link(rv, photo=F6, face="f6", what="tile_pitch_v")}
    m = measure.build_chain(building="honbu", face="f6", pitch=s, ratio_from_h=q)
    assert m["value"] == pytest.approx(0.115429 * 9.4 / 16.0, rel=0.003)
    assert m["status"] == "measured" and [ln["link"] for ln in m["chain"]] == \
        ["tape", "uniform_period", "uniform_period", "count", "count", "px_ratio"]
    assert m["tol"] >= measure.chain_tol(m["chain"], m["value"]) - 1e-12
    if hasattr(schema, "chain_problems"):
        assert schema.chain_tol(m["chain"], m["value"]) <= m["tol"] + 1e-12
        assert schema.chain_problems(schema.Measurement.from_json(m), "t") == []
    # コマンド
    monkeypatch.setenv("RECORDER_LOCK_DIR", str(tmp_path / "locks"))
    jpg = tmp_path / "b_front.jpg"
    cv2.imwrite(str(jpg), cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))
    rep = tmp_path / "rep"
    _pitch(rep, read="5.4→86.2", n=7)
    base = ["measure", "chain", "honbu", "--face", "f6", "--key", "tile_pitch_v", "--what", "tile_pitch_v",
            "--ratio-from-h", str(jpg), "--photo", F6, "--reports", str(rep)]
    assert cli.main(base) == 2                                       # 箱が無い
    assert cli.main(base + ["--h-box", "0", "1000", "2400", "1150", "--v-box", "1000", "0", "1150", "2400"]) == 0
    d = json.loads((rep / "honbu" / "sokuryo_chain_f6.json").read_text(encoding="utf-8"))
    assert d["values"]["tile_pitch_v"]["value"] == pytest.approx(0.115429 * 9.4 / 16.0, rel=0.003)
    assert d["scale"]["tile_pitch_v"]["v"]["span"]["m"] >= 40


def test_chain_tol_is_schema_and_edges_raise():
    from recorder import schema

    ln = measure.tape_link(TAPE, "0→91.0", 4, "tile_pitch_h")
    assert measure.chain_tol([ln], 0.2275) == schema.chain_tol([ln], 0.2275)
    # 単位付きの読みは cm にそろえてから schema に渡す（同じ区間なら同じ ±）
    for r in ("0→910 mm", "0→0.91 m", "0→91.0 cm", "0->91"):
        assert measure.parse_read(r)[1] == pytest.approx(91.0)
        assert measure.chain_tol([{**ln, "read": r}], 0.2275) == pytest.approx(0.00125)
    # 端の入力は例外（黙って 0 にしない）
    for bad in ([{"link": "count", "photo": F6, "n": 0}], [{**ln, "read": "91"}], [{"link": "px_ratio", "photo": F6, "px": [5]}],
                [{"link": "uniform_period", "photo": F6, "px": [9.4]}], ["tape"]):
        with pytest.raises(ValueError):
            measure.chain_tol(bad, 1.0)
    with pytest.raises(ValueError):
        measure.chain_tol([ln], None)
