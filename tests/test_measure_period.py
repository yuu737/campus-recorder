"""柱間隔・階高の周期取り：遮蔽、2 倍周期の罠、目地の細かい繰り返し、候補・警告の出力。"""
import numpy as np

from recorder import measure


def stripes(width=1600, height=700, pitch=120, win=44, amp=None, occlude=None, seed=0):
    img = np.full((height, width), 200, np.uint8)
    n = width // pitch
    for i in range(n):
        v = 60 if amp is None else int(200 - amp[i % len(amp)])
        img[:, i * pitch + 30:i * pitch + 30 + win] = v
    if occlude:
        a, b = occlude
        rng = np.random.default_rng(seed)
        img[:, a:b] = 140 + rng.integers(-6, 6, size=(height, b - a))
    return img


def test_period_found_with_thick_occlusion():
    img = stripes(occlude=(500, 880))                  # 3 本分を太い遮蔽（木）で隠す
    r = measure.measure(img)
    assert r.bays.period_px and abs(r.bays.period_px - 120) < 4
    assert r.bays.candidates and r.bays.candidates[0]["period_px"] == r.bays.period_px
    assert len(r.bays.candidates) <= 3 and r.bays.confidence in ("high", "mid", "low")


def test_double_period_trap_prefers_fundamental():
    # 本当は 100 px 周期。1 本おきに少し濃さが違うので 200 px の山のほうが高くなる
    img = stripes(pitch=100, win=36, amp=[140, 120])
    r = measure.measure(img)
    assert abs(r.bays.period_px - 100) < 4, (r.bays.period_px, r.bays.candidates)


def test_double_period_is_reported_as_candidate_and_warning():
    img = stripes(pitch=100, win=36, amp=[140, 60])    # 濃さが大きく違う＝200 が本当の周期に近い
    r = measure.measure(img)
    periods = [c["period_px"] for c in r.bays.candidates]
    assert any(abs(p - 200) < 6 for p in periods) and any(abs(p - 100) < 6 for p in periods)
    assert r.bays.period_px in periods
    assert r.bays.warnings or r.bays.confidence != "high"


def test_joint_texture_does_not_hide_structure():
    # 細かい目地（20 px）の上に 400 px 周期の柱。目地の整数倍（60・80 …）に引きずられない
    w, h = 2400, 800
    img = np.full((h, w), 190, np.uint8)
    for x in range(0, w, 20):
        img[:, x:x + 2] = 150
    for i in range(w // 400):
        img[:, i * 400 + 100:i * 400 + 220] = 70
    r = measure.measure(img)
    assert abs(r.bays.period_px - 400) < 12, (r.bays.period_px, r.bays.candidates)


def test_weak_signal_is_flagged():
    rng = np.random.default_rng(1)
    img = (rng.normal(128, 20, (700, 1600))).clip(0, 255).astype(np.uint8)
    r = measure.measure(img)
    assert r.bays.confidence == "low"
