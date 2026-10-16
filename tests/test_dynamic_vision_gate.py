import asyncio
from types import SimpleNamespace

from core.vision import chart_guard as cg
from core.vision.chart_guard import DynamicVisionGatekeeper, evaluate_breakout_vision


def make_test_bar(o=100.0, c=100.0, h=101.0, l=99.0):
    return SimpleNamespace(
        open=o, high=h, low=l, close=c, volume=100.0, ma5=100.0,
        ma15=100.0, kc_upper=105.0, kc_lower=95.0, atr=1.0,
    )


def test_dynamic_gate_clean_state_passes():
    gate = DynamicVisionGatekeeper(min_bars_interval=2)
    ok, reason = gate.can_submit_for_vision("BTC", make_test_bar(), "LONG")
    assert ok is True
    assert reason == "CLEAN_STATE"


def test_dynamic_gate_blocks_within_min_interval():
    gate = DynamicVisionGatekeeper(min_bars_interval=2)
    b0 = make_test_bar(h=105.0, l=95.0, c=100.0)
    gate.record_rejection("BTC", b0, "LONG")

    # 0 bars since reject
    ok, reason = gate.can_submit_for_vision("BTC", make_test_bar(c=106.0), "LONG")
    assert ok is False
    assert "BLOCKED_INTERVAL" in reason

    # 1 bar since reject
    gate.on_bar_closed("BTC")
    ok, reason = gate.can_submit_for_vision("BTC", make_test_bar(c=106.0), "LONG")
    assert ok is False
    assert "BLOCKED_INTERVAL" in reason


def test_dynamic_gate_blocks_if_structure_not_broken_after_interval():
    gate = DynamicVisionGatekeeper(min_bars_interval=2)
    b0 = make_test_bar(h=105.0, l=95.0, c=100.0)
    gate.record_rejection("BTC", b0, "LONG")

    gate.on_bar_closed("BTC")
    gate.on_bar_closed("BTC")  # now 2 bars elapsed

    # Price close 104.5 <= rejected high 105.0 -> Blocked
    ok, reason = gate.can_submit_for_vision("BTC", make_test_bar(c=104.5), "LONG")
    assert ok is False
    assert "BLOCKED_STRUCTURE_LONG" in reason


def test_dynamic_gate_unlocks_when_high_broken():
    gate = DynamicVisionGatekeeper(min_bars_interval=2)
    b0 = make_test_bar(h=105.0, l=95.0, c=100.0)
    gate.record_rejection("BTC", b0, "LONG")

    gate.on_bar_closed("BTC")
    gate.on_bar_closed("BTC")

    # Price close 105.2 > rejected high 105.0 -> Unlocked!
    ok, reason = gate.can_submit_for_vision("BTC", make_test_bar(c=105.2), "LONG")
    assert ok is True
    assert "UNLOCKED_BREAK_HIGH" in reason


def test_dynamic_gate_short_unlocks_when_low_broken():
    gate = DynamicVisionGatekeeper(min_bars_interval=2)
    b0 = make_test_bar(h=105.0, l=95.0, c=100.0)
    gate.record_rejection("BTC", b0, "SHORT")

    gate.on_bar_closed("BTC")
    gate.on_bar_closed("BTC")

    # Price close 96.0 >= rejected low 95.0 -> Blocked
    ok, reason = gate.can_submit_for_vision("BTC", make_test_bar(c=96.0), "SHORT")
    assert ok is False
    assert "BLOCKED_STRUCTURE_SHORT" in reason

    # Price close 94.8 < rejected low 95.0 -> Unlocked!
    ok, reason = gate.can_submit_for_vision("BTC", make_test_bar(c=94.8), "SHORT")
    assert ok is True
    assert "UNLOCKED_BREAK_LOW" in reason


def test_confirmed_reversal_cross_bypasses_old_price_lock_and_clears_it():
    gate = DynamicVisionGatekeeper(min_bars_interval=2)
    gate.record_rejection("BTC", make_test_bar(h=105.0, l=95.0), "SHORT")

    ok, reason = gate.can_submit_for_vision(
        "BTC",
        make_test_bar(c=100.0),
        "SHORT",
        entry_pattern="REVERSAL",
        structure_confirmation="CONFIRMED_KC_OUTER_PIVOT_THEN_MA5_MA15_CROSS",
        structure_confirmed=True,
    )

    assert ok is True
    assert reason == "UNLOCKED_CONFIRMED_MA_TURN"
    assert "BTC" not in gate._rejections


def test_same_side_reversal_cross_bypasses_prior_qwen_rejection_low():
    gate = DynamicVisionGatekeeper(min_bars_interval=2)
    gate.record_rejection(
        "LOBSTER/USDT",
        make_test_bar(h=0.02820, l=0.02801, c=0.02808),
        "SHORT",
    )

    ok, reason = gate.can_submit_for_vision(
        "LOBSTER/USDT",
        make_test_bar(o=0.02810, c=0.02806, h=0.02812, l=0.02802),
        "SHORT",
        entry_pattern="REVERSAL",
        structure_confirmation="CONFIRMED_KC_OUTER_PIVOT_THEN_MA5_MA15_CROSS",
        structure_confirmed=True,
    )

    assert ok is True
    assert reason == "UNLOCKED_CONFIRMED_MA_TURN"
    assert "LOBSTER/USDT" not in gate._rejections


def test_confirmed_crossover_bypasses_lock_for_opposite_swing():
    gate = DynamicVisionGatekeeper(min_bars_interval=2)
    gate.record_rejection("BTC", make_test_bar(h=105.0, l=95.0), "LONG")

    ok, reason = gate.can_submit_for_vision(
        "BTC",
        make_test_bar(c=100.0),
        "SHORT",
        entry_pattern="CROSSOVER",
        structure_confirmation="MA5_MA15_1M_CROSS",
        structure_confirmed=True,
    )

    assert ok is True
    assert reason == "UNLOCKED_CONFIRMED_MA_TURN"
    assert "BTC" not in gate._rejections


def test_opposite_direction_candidate_clears_rejection_lock():
    gate = DynamicVisionGatekeeper(min_bars_interval=2)
    gate.record_rejection("BTC", make_test_bar(h=105.0, l=95.0), "LONG")

    ok, reason = gate.can_submit_for_vision("BTC", make_test_bar(), "SHORT")

    assert ok is True
    assert reason == "OPPOSITE_SIDE_RESET"
    assert "BTC" not in gate._rejections


def test_unconfirmed_or_unrelated_pattern_does_not_bypass_lock():
    gate = DynamicVisionGatekeeper(min_bars_interval=2)
    gate.record_rejection("BTC", make_test_bar(h=105.0, l=95.0), "SHORT")

    ok, reason = gate.can_submit_for_vision(
        "BTC",
        make_test_bar(c=100.0),
        "SHORT",
        entry_pattern="REVERSAL",
        structure_confirmation="TOP_BOTTOM_DIVERGENCE",
        structure_confirmed=True,
    )

    assert ok is False
    assert "BLOCKED_INTERVAL" in reason
    assert "BTC" in gate._rejections


def test_dynamic_lock_expires_after_more_than_five_closed_bars():
    gate = DynamicVisionGatekeeper(min_bars_interval=2)
    gate.record_rejection("BTC", make_test_bar(h=105.0, l=95.0), "SHORT")

    for _ in range(5):
        gate.on_bar_closed("BTC")
    assert "BTC" in gate._rejections
    ok, reason = gate.can_submit_for_vision("BTC", make_test_bar(c=100.0), "SHORT")
    assert ok is False
    assert "BLOCKED_STRUCTURE_SHORT" in reason

    gate.on_bar_closed("BTC")
    assert "BTC" not in gate._rejections
    ok, reason = gate.can_submit_for_vision("BTC", make_test_bar(c=100.0), "SHORT")
    assert ok is True
    assert reason == "CLEAN_STATE"


def test_dynamic_gate_cleared_on_approved():
    gate = DynamicVisionGatekeeper(min_bars_interval=2)
    b0 = make_test_bar(h=105.0, l=95.0, c=100.0)
    gate.record_rejection("BTC", b0, "LONG")
    assert "BTC" in gate._rejections

    gate.record_approved("BTC")
    assert "BTC" not in gate._rejections
    ok, reason = gate.can_submit_for_vision("BTC", make_test_bar(), "LONG")
    assert ok is True
    assert reason == "CLEAN_STATE"


def test_local_qwen_priority_and_fallback_to_gemini(monkeypatch):
    monkeypatch.setenv("USE_LOCAL_QWEN", "true")
    monkeypatch.setenv("LOCAL_QWEN_URL", "http://127.0.0.1:8000/v1")
    monkeypatch.setenv("GEMINI_API_KEY", "test_gemini_key")

    bars = [make_test_bar() for _ in range(40)]

    # 1. Local Qwen responds successfully
    async def fake_local_qwen(img, side, mode):
        return '{"verdict":"REAL","reason":"本地放量起漲"}'

    monkeypatch.setattr(cg, "_call_local_qwen", fake_local_qwen)
    ok, reason = asyncio.run(evaluate_breakout_vision(bars, "LONG"))
    assert ok is True
    assert reason == "本地放量起漲"

    # 2. Local Qwen fails -> Fallback to Gemini
    async def fake_local_fail(img, side, mode):
        return None  # Connection refused on all ports

    monkeypatch.setattr(cg, "_call_local_qwen", fake_local_fail)
    monkeypatch.setattr(cg, "_call_gemini", lambda img, side, key, mode=None: '{"verdict":"REAL","reason":"雲端Gemini備援通過"}')

    ok, reason = asyncio.run(evaluate_breakout_vision(bars, "LONG"))
    assert ok is True
    assert reason == "雲端Gemini備援通過"
