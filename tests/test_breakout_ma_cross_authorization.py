import pandas as pd

from core.services.strict_ma3_ma15_entry_gate import evaluate_ma3_ma15_entry


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
