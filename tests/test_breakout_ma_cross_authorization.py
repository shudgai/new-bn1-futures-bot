import pandas as pd

from core.services.strict_ma3_ma15_entry_gate import evaluate_ma3_ma15_entry
from core.services.market_consolidation_filter import evaluate_market_blacklist


def falling_breakout_frame():
    rows = []
    for index in range(20):
        close = 100.0 - index * 0.2
        middle = close + 0.2
        rows.append({
            "timestamp": (index + 1) * 60_000,
            "open": close + 0.2,
            "high": close + 0.22,
            "low": close - 0.02,
            "close": close,
            "ma3": close - 0.15,
            "ma5": close - 0.10,
            "ma15": close + 0.15,
            "kc_upper": middle + 0.2,
            "kc_middle": middle,
            "kc_lower": middle - 0.1,
            "is_closed": True,
        })
    return pd.DataFrame(rows)


def test_confirmed_kc_breakdown_does_not_wait_for_ma_cross():
    frame = falling_breakout_frame()

    result = evaluate_ma3_ma15_entry(
        frame,
        "SHORT",
        current_position="IDLE",
        last_trade_bar_time=None,
        stop_loss_price=float(frame.iloc[-1].close) + 0.5,
        execution_price=float(frame.iloc[-1].close),
        confirmed_breakout=True,
    )

    assert result.action == "OPEN_SHORT"
    assert result.checklist["CONFIRMED_BREAKOUT_DIRECTION"] is True
    assert result.checklist["STRICT_MA3_MA15_CROSS"] is False


def test_non_breakout_candidate_still_requires_ma_cross():
    frame = falling_breakout_frame()

    result = evaluate_ma3_ma15_entry(
        frame,
        "SHORT",
        current_position="IDLE",
        last_trade_bar_time=None,
        stop_loss_price=float(frame.iloc[-1].close) + 0.5,
        execution_price=float(frame.iloc[-1].close),
    )

    assert result.action == "HOLD / PASS"
    assert "NO_STRICT_MA3_MA15_CROSS" in result.violations


def ranging_low_atr_frame(adx):
    rows = []
    for index in range(21):
        close = 1.0 - index * 0.0001
        middle = close + 0.0003
        is_current = index == 20
        rows.append({
            "timestamp": (index + 1) * 60_000,
            "open": close + 0.0001 if is_current else close,
            "high": close + 0.00012 if is_current else close + 0.005,
            "low": close - 0.00002 if is_current else close - 0.005,
            "close": close,
            "ma3": close + 0.0002,
            "ma15": close + 0.0004,
            "atr": close * 0.0028,
            "kc_upper": middle + 0.002,
            "kc_middle": middle,
            "kc_lower": middle - 0.002,
            "volume": 100.0,
            "adx": adx,
            "is_closed": True,
        })
    return pd.DataFrame(rows)


def test_low_atr_does_not_veto_in_range_market_when_adx_confirms_trend():
    blocked, reason, evidence = evaluate_market_blacklist(
        ranging_low_atr_frame(adx=28.0)
    )

    assert evidence["atr_pct"] < 0.003
    assert evidence["adx"] >= 20.0
    assert blocked is False
    assert reason == "PASS_MARKET_BLACKLIST"


def test_low_atr_and_weak_adx_still_veto_in_range_market():
    blocked, reason, _ = evaluate_market_blacklist(
        ranging_low_atr_frame(adx=15.0)
    )

    assert blocked is True
    assert reason == "VETO_DONCHIAN_RANGE_LOW_TREND"
