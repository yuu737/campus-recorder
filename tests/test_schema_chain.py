"""根拠の鎖（K-101、11/8）：chain・measured_kind の検証、chain_tol の式、counts の直接／鎖／個数。"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from recorder import schema

ROOT = Path(__file__).resolve().parent.parent

TAPE = {"link": "tape", "photo": "本部棟正面柱92cm", "read": "0→91.0 cm", "n": 4, "what": "tile_pitch_h",
        "value": 0.2275, "tol": 0.00125}
UNIFORM = {"link": "uniform_period", "photo": "PXL_20261103_042900775", "px": [38.1, 38.4, 38.2, 38.3],
           "what": "正面化した f6 の全体で横の周期が一様"}
COUNT = {"link": "count", "photo": "PXL_20261103_042900775", "n": 24, "what": "柱型の縁から縁のタイル枚数", "tol": 1}
PXR = {"link": "px_ratio", "photo": "PXL_20261103_042900775", "px": [180, 910], "what": "パラペット÷階高"}


def mv(value, tol, chain, kind="chain", status="measured", unit="m"):
    d = {"value": value, "unit": unit, "tol": tol, "status": status, "evidence": ["本部棟正面柱92cm"],
         "method": "タイルの周期（メジャー）× 枚数", "chain": chain}
    if kind is not None:
        d["measured_kind"] = kind
    return d


def probs(d):
    return schema.Measurement.from_json(d).problems("x")


# --- chain_tol の式 -------------------------------------------------------------
def test_chain_tol_tape_direct_is_half_cm():
    # direct：区間 0.91 m をそのまま値にすると ±0.5 cm
    assert schema.chain_tol([TAPE], 0.91) == pytest.approx(0.005)
    # 周期（0.2275 m）に直すと ±0.5 cm ÷ 4
    assert schema.chain_tol([TAPE], 0.2275) == pytest.approx(0.00125)


def test_chain_tol_sums_each_link():
    v = 24 * 0.2275
    got = schema.chain_tol([TAPE, UNIFORM, COUNT], v)
    want = v * (0.005 / 0.91 + (38.4 - 38.1) / 2 / 38.25 + 1 / 24)
    assert got == pytest.approx(want)
    # 数え違い ±1 枚 × 周期 が入る
    assert got > 0.2275
    # px_ratio：±1 px を比で移す
    assert schema.chain_tol([PXR], 2.0) == pytest.approx(2.0 * (1 / 180 + 1 / 910))


def test_chain_tol_unreadable_link_is_inf():
    assert schema.chain_tol([{"link": "tape", "photo": "P", "read": "読めない", "n": 4}], 1.0) == float("inf")
    assert schema.chain_tol([{"link": "count", "photo": "P"}], 1.0) == float("inf")
    # 4 種以外の輪は数えない
    assert schema.chain_tol([TAPE, {"link": "claim", "what": "同じタイル"}], 0.91) == pytest.approx(0.005)


def test_parse_tape_read_units():
    assert schema.parse_tape_read("0→91.0 cm") == pytest.approx(0.91)
    assert schema.parse_tape_read("12.0→103.5") == pytest.approx(0.915)
    assert schema.parse_tape_read("0->910 mm") == pytest.approx(0.91)
    assert schema.parse_tape_read("0→0.91 m") == pytest.approx(0.91)
    assert schema.parse_tape_read("91") == pytest.approx(0.91)
    assert schema.parse_tape_read("") is None


# --- 検証 -----------------------------------------------------------------------
def test_good_chain_and_direct_pass():
    v = 24 * 0.2275
    need = schema.chain_tol([TAPE, UNIFORM, COUNT], v)
    assert probs(mv(v, round(need + 0.005, 3), [TAPE, UNIFORM, COUNT])) == []
    assert probs(mv(0.91, 0.005, [TAPE], kind="direct")) == []


def test_tol_below_chain_tol_fails():
    p = probs(mv(5.46, 0.035, [TAPE, UNIFORM, COUNT]))
    assert any("鎖から積んだ" in x for x in p), p


def test_measured_without_tol_fails_when_chain():
    p = probs(mv(0.91, None, [TAPE], kind="direct"))
    assert any("± (tol) がありません" in x for x in p), p


def test_non_four_link_needs_estimated():
    for bad in ("eye", "claim", "plateau", "typical", "assume"):
        p = probs(mv(5.46, 0.5, [TAPE, {"link": bad, "photo": "P", "what": "…"}, COUNT]))
        assert any(f"輪 {bad} は measured に使えません" in x for x in p), (bad, p)
        # estimated なら通る（方法は書く）
        e = mv(5.46, 0.5, [TAPE, {"link": bad, "what": "…"}, COUNT], kind=None, status="estimated")
        assert probs(e) == [], (bad, probs(e))


def test_each_link_needs_photo():
    p = probs(mv(5.46, 0.5, [TAPE, {**COUNT, "photo": ""}]))
    assert any("count の輪に写真 id" in x for x in p), p


def test_measured_kind_consistency():
    assert any("measured_kind=chain は鎖と合いません" in x for x in probs(mv(0.91, 0.005, [TAPE], kind="chain")))
    assert any("measured_kind=direct は鎖と合いません" in x for x in probs(mv(5.46, 0.5, [TAPE, COUNT], kind="direct")))
    assert any("measured_kind がありません" in x for x in probs(mv(5.46, 0.5, [TAPE, COUNT], kind=None)))
    assert any("direct か chain" in x for x in probs(mv(5.46, 0.5, [TAPE, COUNT], kind="derived")))
    # estimated に measured_kind は書かない
    assert any("measured の値だけ" in x for x in probs(mv(5.46, 0.5, [TAPE, COUNT], status="estimated")))


def test_length_chain_needs_tape():
    p = probs(mv(2.0, 1.0, [COUNT, PXR]))
    assert any("tape（メジャーの読み）の輪がありません" in x for x in p), p


def test_bad_link_fields():
    p = probs(mv(2.0, 1.0, [TAPE, {"link": "px_ratio", "photo": "P", "px": [180]}]))
    assert any("[分子, 分母]" in x for x in p), p
    p = probs(mv(2.0, 1.0, [TAPE, {"link": "uniform_period", "photo": "P", "px": [38.0]}]))
    assert any("uniform_period の輪の px" in x for x in p), p
    p = probs(mv(2.0, 1.0, "tape"))
    assert any("chain は輪（dict）の並び" in x for x in p), p


def test_counts_need_no_chain():
    d = {"value": 9, "unit": "個", "tol": None, "status": "measured", "evidence": ["P1", "P2"], "method": "数えた"}
    assert probs(d) == []
    assert schema.warn_chain({"faces": [{"id": "f1", "bays": {"count": d}}]}) == []


def test_measured_length_without_chain_is_warning_not_error():
    legacy = {"value": 3.9, "unit": "m", "status": "measured", "evidence": ["P1"], "method": "正面化"}
    assert probs(legacy) == []                          # 既存の見本は落とさない
    w = schema.warn_chain({"floor_pitch": legacy})
    assert any("根拠の鎖（chain）がありません" in x for x in w) and any("± (tol) がありません" in x for x in w)


def test_validate_building_walks_chain(tmp_path):
    b = json.loads((ROOT / "tests/fixtures/sample_building.json").read_text(encoding="utf-8"))
    assert schema.validate_building(b) == []
    b["faces"][0]["length"] = mv(5.46, 0.035, [TAPE, UNIFORM, COUNT])
    errs = schema.validate_building(b)
    assert any("faces[" in e and "鎖から積んだ" in e for e in errs), errs


def test_chain_photos_are_checked_as_evidence():
    b = {"floor_pitch": mv(0.91, 0.005, [{**TAPE, "photo": "無い写真"}], kind="direct")}
    w = schema.warn_evidence_ids(b, photo_ids={"本部棟正面柱92cm"}, root=ROOT)
    assert any("無い写真" in x and "chain[0]" in x for x in w), w


def test_to_json_roundtrip_keeps_chain():
    d = mv(0.91, 0.005, [TAPE], kind="direct")
    j = schema.Measurement.from_json(d).to_json()
    assert j["chain"] == [TAPE] and j["measured_kind"] == "direct"
    # 鎖の無い値は鍵を増やさない
    assert "chain" not in schema.Measurement(1.0, status="estimated", method="m").to_json()


# --- 集計 -----------------------------------------------------------------------
def building_with_kinds():
    v = 24 * 0.2275
    return {"id": "t", "faces": [{"id": "f6", "length": mv(v, 0.3, [TAPE, UNIFORM, COUNT]),
                                  "bays": {"count": {"value": 9, "unit": "個", "status": "measured", "evidence": ["P1"]},
                                           "pitch": mv(0.91, 0.005, [TAPE], kind="direct")}}],
            "floor_pitch": {"value": 3.9, "unit": "m", "tol": 0.1, "status": "measured", "evidence": ["P"], "method": "正面化"},
            "parapet_top": {"value": 20.0, "unit": "m", "tol": 1.0, "status": "estimated", "method": "PLATEAU"},
            "requests": [{"face": "f6", "what": "x", "why": "y",
                          "v": {"value": 1.0, "unit": "m", "status": "measured", "evidence": ["P"]}}]}


def test_measured_kinds_split():
    k = schema.measured_kinds(building_with_kinds())
    assert k == {"direct": 1, "chain": 1, "count": 1, "no_chain": 1, "measured": 4}
    assert "直接 1／鎖 1／個数 1" in schema.measured_kinds_line(k)


def test_counts_cli_json(tmp_path):
    f = tmp_path / "t.json"
    f.write_text(json.dumps(building_with_kinds(), ensure_ascii=False), encoding="utf-8")
    r = subprocess.run([sys.executable, "-I", "-X", "utf8", "-m", "recorder", "counts", str(f), "--json"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    rows = json.loads(r.stdout)
    assert rows[0]["measured_kinds"]["direct"] == 1 and rows[0]["measured_kinds"]["chain"] == 1
    r2 = subprocess.run([sys.executable, "-I", "-X", "utf8", "-m", "recorder", "counts", str(f)],
                        cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert r2.returncode == 0 and "直接 1／鎖 1／個数 1" in r2.stdout


# --- 物差し台帳 -------------------------------------------------------------------
def test_rulers_have_tile_pitch_per_building():
    doc = json.loads((ROOT / "data/rulers.json").read_text(encoding="utf-8"))
    ids = {r["id"]: r for r in doc["rulers"]}
    for bid in ("honbu", "library", "tower75", "kyotsu-minami"):
        for ax in ("h", "v"):
            r = ids[f"tile_pitch_{ax}_{bid}"]
            assert r["value"] is None and r["status"] == "unknown" and r["building"] == bid
            assert bid in r["family"]["members"] and "根拠にしない" in r["family"]["use"]
    assert "K-101" in ids["tape_measure"]["note"]
    assert "伸縮目地" in ids["tile_pitch_h_honbu"]["note"] and "シーリング目地" in ids["tile_pitch_h_tower75"]["note"]


def test_buildings_stay_clean():
    for f in sorted((ROOT / "data/buildings").glob("*.json")):
        b = schema.load_building(f)
        assert schema.validate_building(b) == [], f
        assert schema.warn_chain(b) == [], f


# --- 追記（Fable の決定 11/8）：2 枚で一致した枚数・px_unit mm ---------------------------
def test_count_confirmed_by_second_photo_is_zero():
    # K-202：0 にするのは 1 枚ずつ数えた輪（counted: true と途中の印）だけ
    c2 = {**COUNT, "confirmed_by": "PXL_20261103_043000000", "counted": True, "marks": [102.0, 140.2, 178.5],
          "confirmed_marks": [55.0, 93.1, 131.4]}
    assert schema.chain_tol([c2], 5.46) == 0.0
    v = 5.46
    need = schema.chain_tol([TAPE, UNIFORM, c2], v)
    assert need == pytest.approx(v * (0.005 / 0.91 + 0.15 / 38.25))
    assert probs(mv(v, round(need + 0.0005, 4), [TAPE, UNIFORM, c2])) == []


def test_count_confirmed_by_same_photo_fails():
    bad = {**COUNT, "confirmed_by": COUNT["photo"]}
    p = probs(mv(5.46, 0.5, [TAPE, bad]))
    assert any("2 枚で一致した枚数は数え違いを 0 と見なす（根拠に 2 枚目の id）" in x for x in p), p
    p = probs(mv(5.46, 0.5, [TAPE, {**COUNT, "confirmed_by": ""}]))
    assert any("confirmed_by" in x for x in p), p


def test_uniform_period_in_mm_passes():
    u2 = {"link": "uniform_period", "photo": "本部棟柱2本目", "px": [227.0, 228.0], "px_unit": "mm",
          "what": "2 枚目のメジャーで周期を検算"}
    v = 5.46
    need = schema.chain_tol([TAPE, u2, COUNT], v)
    assert need == pytest.approx(v * (0.005 / 0.91 + 0.5 / 227.5 + 1 / 24))
    assert probs(mv(v, round(need + 0.001, 3), [TAPE, u2, COUNT])) == []
    p = probs(mv(v, 1.0, [TAPE, {**u2, "px_unit": "cm"}, COUNT]))
    assert any("px_unit" in x for x in p), p


# --- F17（11/8）：本部棟 f6 の柱間の形で、count の輪の ±（tol 枚 × 周期）がどこで入る／入らないか ------------
F6_TAPE = {"link": "tape", "photo": "本部棟正面柱92cm", "read": "5.4→86.2 cm", "n": 7, "what": "tile_pitch_h",
           "value": 0.115429, "tol": 0.000715}
F6_UNIFORM = {"link": "uniform_period", "photo": "PXL_20261103_042900775", "px": [16.02, 15.99, 16.04, 16.0],
              "what": "正面化した f6 の中で横の周期が一様"}
F6_COUNT = {"link": "count", "photo": "PXL_20261103_042900775", "n": 46, "what": "柱型の芯から芯のタイル枚数", "tol": 1}


def test_f6_count_tol_times_period_is_stacked_without_second_photo():
    v = 0.115429 * 46
    base = schema.chain_tol([F6_TAPE, F6_UNIFORM], v)
    got = schema.chain_tol([F6_TAPE, F6_UNIFORM, F6_COUNT], v)
    # 数え違い ±1 枚 × その時の周期（value ÷ n ＝ tape の輪の周期）がそのまま足される
    assert got - base == pytest.approx(1 * v / 46)
    assert got - base == pytest.approx(0.115429, abs=1e-6)
    assert got == pytest.approx(0.1566, abs=5e-4)
    # tol 2 枚なら 2 倍
    assert schema.chain_tol([F6_TAPE, F6_UNIFORM, {**F6_COUNT, "tol": 2}], v) - base == pytest.approx(2 * 0.115429, abs=1e-6)


def test_f6_count_confirmed_by_drops_only_the_count_term_when_counted():
    v = 0.115429 * 46
    c2 = {**F6_COUNT, "confirmed_by": "PXL_20261103_042909968", "counted": True,
          "method": "正面化した 4 階の帯で、柱型の芯から 1 枚ずつ目地に印を付けて数えた"}
    got = schema.chain_tol([F6_TAPE, F6_UNIFORM, c2], v)
    # 2 枚で一致・1 枚ずつ数えた枚数は count の項だけ 0、tape と一様の項は残る
    assert got == pytest.approx(schema.chain_tol([F6_TAPE, F6_UNIFORM], v))
    assert got == pytest.approx(0.0411, abs=5e-4)
    # marks で印を残した形も同じ
    c3 = {**F6_COUNT, "confirmed_by": "PXL_20261103_042909968", "counted": True, "marks": [760, 776, 792],
          "confirmed_marks": [100, 118, 136]}
    assert schema.chain_tol([F6_TAPE, F6_UNIFORM, c3], v) == pytest.approx(got)


@pytest.mark.parametrize("extra", [
    {},                                                    # 今の本部棟 f6 の形（px ÷ 周期の割り算）
    {"counted": True},                                     # 印が無い
    {"counted": True, "marks": [], "method": "  "},        # 印が空
    {"marks": [760, 776], "method": "1 枚ずつ数えた"},       # counted: true が無い
    {"counted": "yes", "marks": [760, 776], "confirmed_marks": [1, 2]},  # true でない
    {"counted": True, "marks": [760, 776]},                # 2 枚目の印（confirmed_marks）が無い
])
def test_k202_confirmed_by_without_counting_still_stacks(extra):
    v = 0.115429 * 46
    c2 = {**F6_COUNT, "confirmed_by": "PXL_20261103_042909968", **extra}
    got = schema.chain_tol([F6_TAPE, F6_UNIFORM, c2], v)
    assert got == pytest.approx(schema.chain_tol([F6_TAPE, F6_UNIFORM, F6_COUNT], v))
    assert got == pytest.approx(0.1566, abs=5e-4)


def test_k202_validate_says_why_when_tol_too_small():
    v = 5.30973
    c2 = {**F6_COUNT, "confirmed_by": "PXL_20261103_042909968"}
    p = probs(mv(v, 0.054, [F6_TAPE, F6_UNIFORM, c2]))
    assert any("chain_tol 以上にする" in x and "K-202" in x and "counted: true" in x and "chain[2]" in x for x in p), p
    # tol が足りていれば落ちない（confirmed_by が付いていること自体はエラーにしない）
    assert probs(mv(v, 0.157, [F6_TAPE, F6_UNIFORM, c2])) == []
    # 数えた印のある輪は今の tol で通り、理由の文も出ない
    ok = {**c2, "counted": True, "marks": [760, 776, 792], "confirmed_marks": [100, 118, 136]}
    assert probs(mv(v, 0.054, [F6_TAPE, F6_UNIFORM, ok])) == []


def test_validate_walks_face_top_z_chain():
    # F17：面の上端 faces[].top_z も検証の対象（前は見ていなかった）
    b = json.loads((ROOT / "tests/fixtures/sample_building.json").read_text(encoding="utf-8"))
    v = 22.66414
    b["faces"][0]["top_z"] = mv(v, 0.227, [F6_TAPE, F6_UNIFORM, {**F6_COUNT, "confirmed_by": "PXL_20261103_042909968"},
                                           {"link": "px_ratio", "photo": "PXL_20261103_042909968", "px": [3616.2, 847.2]}])
    errs = schema.validate_building(b)
    assert any(e.startswith("faces[") and ".top_z" in e and "K-202" in e for e in errs), errs
    b["faces"][0]["top_z"]["tol"] = 0.702
    assert schema.validate_building(b) == []
