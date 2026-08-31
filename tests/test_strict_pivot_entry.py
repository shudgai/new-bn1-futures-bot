import pandas as pd

from core.engine import TradingEngine
from core.strategy import detect_simple_ma5_signal


def _frame(side: str) -> pd.DataFrame:
    if side == "LONG":
        return pd.DataFrame({
            "open": [100.0, 99.0, 97.9, 98.8],
            "close": [99.5, 98.5, 98.2, 99.4],
            "high": [102.5, 99.2, 98.7, 99.6],
            "low": [99.3, 98.2, 97.8, 98.7],
            "ma3": [100.0, 97.0, 98.0, 99.0],
            "ma15": [101.0] * 4,
            "ema_20": [100.0] * 4,
            "kc_upper": [102.0] * 4,
            "kc_lower": [98.0] * 4,
            "atr": [1.0] * 4,
        })
    return pd.DataFrame({
        "open": [100.0, 101.0, 102.0, 101.2],
        "close": [100.5, 101.5, 101.8, 100.6],
        "high": [100.7, 101.8, 102.2, 101.3],
        "low": [97.5, 100.8, 101.3, 100.4],
        "ma3": [100.0, 103.0, 102.0, 101.0],
        "ma15": [99.0] * 4,
        "ema_20": [100.0] * 4,
        "kc_upper": [102.0] * 4,
        "kc_lower": [98.0] * 4,
        "atr": [1.0] * 4,
    })


def test_long_requires_green_second_k_in_lower_half_channel():
    ok, reason, offset = TradingEngine._validate_strict_pivot_entry(
        _frame("LONG"), "LONG",
    )
    assert ok is True
    assert "confirmation passed" in reason
    assert offset == -3


def test_short_requires_red_second_k_in_upper_half_channel():
    ok, reason, offset = TradingEngine._validate_strict_pivot_entry(
        _frame("SHORT"), "SHORT",
    )
    assert ok is True
    assert "confirmation passed" in reason
    assert offset == -3


def test_second_k_crossing_far_beyond_required_rail_is_accepted():
    frame = _frame("SHORT")
    frame.loc[frame.index[-1], ["open", "close", "low"]] = [100.0, 97.8, 97.5]
    ok, reason, _ = TradingEngine._validate_strict_pivot_entry(frame, "SHORT")
    assert ok is True
    assert "confirmation passed" in reason


def test_peak_red_k_crossing_below_lower_rail_confirms_strong_short():
    frame = _frame("SHORT")
    frame.loc[frame.index[-1], ["open", "close", "low"]] = [100.0, 97.8, 97.5]
    ok, _, offset = TradingEngine._validate_strict_pivot_entry(frame, "SHORT")
    assert ok is True
    assert offset == -3


def test_trough_green_k_crossing_above_upper_rail_confirms_strong_long():
    frame = _frame("LONG")
    frame.loc[frame.index[-1], ["open", "close", "high"]] = [99.0, 102.2, 102.4]
    ok, _, offset = TradingEngine._validate_strict_pivot_entry(frame, "LONG")
    assert ok is True
    assert offset == -3


def test_entry_atr_uses_candle_atr_when_signal_payload_is_missing_it():
    frame = _frame("SHORT")
    atr = TradingEngine._resolve_entry_atr({}, frame, live_price=100.0)
    assert atr == 1.0


def test_entry_atr_falls_back_to_kc_width_before_price_epsilon():
    frame = _frame("SHORT").drop(columns=["atr"])
    atr = TradingEngine._resolve_entry_atr({}, frame, live_price=100.0)
    assert atr == 2.0


def test_entry_atr_has_sane_price_fallback_without_indicators():
    frame = pd.DataFrame({"close": [100.0]})
    atr = TradingEngine._resolve_entry_atr({}, frame, live_price=100.0)
    assert atr == 0.1


def test_short_prealert_is_suppressed_near_lower_rail():
    frame = pd.DataFrame({
        "open": [101.0, 100.2, 98.4],
        "close": [100.5, 99.2, 97.9],
        "ma3": [102.3, 102.2, 98.2],
        "ema_20": [100.0] * 3,
        "kc_upper": [102.0] * 3,
        "kc_lower": [98.0] * 3,
        "atr": [1.0] * 3,
    })
    assert TradingEngine._detect_strict_pivot_prealert(frame) is None


def test_prealert_waits_for_ma3_turn_not_only_candle_color():
    frame = pd.DataFrame({
        "open": [101.0, 101.4, 101.5],
        "close": [101.2, 101.6, 101.3],
        "ma3": [102.3, 102.3, 102.3],
        "ema_20": [100.0] * 3,
        "kc_upper": [102.0] * 3,
        "kc_lower": [98.0] * 3,
        "atr": [1.0] * 3,
    })
    assert TradingEngine._detect_strict_pivot_prealert(frame) is None


def test_prealert_appears_after_confirmed_ma3_turn_in_correct_half():
    frame = pd.DataFrame({
        "open": [101.0, 101.4, 101.5],
        "close": [101.2, 101.6, 101.2],
        "ma3": [102.3, 102.2, 102.1],
        "ema_20": [100.0] * 3,
        "kc_upper": [102.0] * 3,
        "kc_lower": [98.0] * 3,
        "atr": [1.0] * 3,
    })
    assert TradingEngine._detect_strict_pivot_prealert(frame) == "SHORT"


def test_low_volume_third_red_candle_still_confirms_short():
    frame = pd.DataFrame({
        "open": [100.0, 101.0, 102.0, 101.7, 101.2],
        "close": [100.5, 101.5, 101.8, 101.6, 100.6],
        "high": [100.7, 101.8, 102.2, 101.8, 101.3],
        "low": [97.5, 100.8, 101.3, 101.5, 100.4],
        "ma3": [100.0, 103.0, 102.0, 101.5, 101.0],
        "ma15": [99.0] * 5,
        "ema_20": [100.0] * 5,
        "kc_upper": [102.0] * 5,
        "kc_lower": [98.0] * 5,
        "atr": [1.0] * 5,
        "volume": [1000.0, 1000.0, 0.001, 0.001, 0.001],
    })
    ok, _, offset = TradingEngine._validate_strict_pivot_entry(frame, "SHORT")
    assert ok is True
    assert offset == -4


def test_doji_is_skipped_and_next_red_can_confirm_short():
    frame = pd.DataFrame({
        "open": [100.0, 101.0, 102.0, 101.70, 101.2],
        "close": [100.5, 101.5, 101.8, 101.69, 100.6],
        "high": [100.7, 101.8, 102.2, 101.9, 101.3],
        "low": [97.5, 100.8, 101.3, 101.5, 100.4],
        "ma3": [100.0, 103.0, 102.0, 101.5, 101.0],
        "ma15": [99.0] * 5,
        "ema_20": [100.0] * 5,
        "kc_upper": [102.0] * 5,
        "kc_lower": [98.0] * 5,
        "atr": [1.0] * 5,
    })
    ok, _, offset = TradingEngine._validate_strict_pivot_entry(frame, "SHORT")
    assert ok is True
    assert offset == -4


def test_doji_alone_does_not_confirm_short():
    frame = _frame("SHORT")
    frame.loc[frame.index[-1], ["open", "close", "high", "low"]] = [100.60, 100.59, 100.8, 100.4]
    ok, reason, _ = TradingEngine._validate_strict_pivot_entry(frame, "SHORT")
    assert ok is False
    assert "two non-doji" in reason


def test_long_confirmation_candle_requires_pullback_only_at_one_atr():
    frame = _frame("LONG")
    frame.loc[frame.index[-1], ["open", "close"]] = [98.0, 99.2]
    assert abs(TradingEngine._pivot_confirmation_body_atr(frame, 1.0) - 1.2) < 1e-9
    assert TradingEngine._pivot_pullback_ready("LONG", 99.0, 99.1, 1.0, 99.3) is True
    assert TradingEngine._pivot_pullback_ready("LONG", 99.2, 99.1, 1.0, 99.3) is False


def test_short_long_candle_waits_for_rebound_near_ma3():
    assert TradingEngine._pivot_pullback_ready("SHORT", 99.2, 99.3, 1.0, 99.0) is True
    assert TradingEngine._pivot_pullback_ready("SHORT", 99.05, 99.3, 1.0, 99.0) is False


def test_price_wick_outside_without_ma3_outside_is_rejected():
    frame = _frame("SHORT")
    frame.loc[frame.index[-3], "ma3"] = 101.5
    frame.loc[frame.index[-3], "high"] = 103.0
    ok, reason, _ = TradingEngine._validate_strict_pivot_entry(frame, "SHORT")
    assert ok is False
    assert "MA3尚未越過KC上軌外" in reason
    assert "不轉向、不開倉" in reason


def test_pivot_near_ma15_is_rejected():
    frame = _frame("LONG")
    frame.loc[frame.index[-3], "ma15"] = 97.1
    ok, reason, _ = TradingEngine._validate_strict_pivot_entry(frame, "LONG")
    assert ok is False
    assert "距MA15" in reason
    assert "不轉向、不開倉" in reason


def test_pivot_near_any_kc_line_is_rejected():
    frame = _frame("SHORT")
    frame.loc[frame.index[-3], "ma3"] = 102.1
    ok, reason, _ = TradingEngine._validate_strict_pivot_entry(frame, "SHORT")
    assert ok is False
    assert "距KC上軌" in reason
    assert "不轉向、不開倉" in reason


def test_original_5887ffa_first_confirmation_then_strict_second_k():
    frame = pd.DataFrame({
        "open": [101.2, 100.2, 99.2, 98.2, 96.8, 97.8, 98.8],
        "close": [101.0, 100.0, 99.0, 98.0, 97.0, 98.0, 99.2],
        "high": [101.4, 100.4, 99.4, 98.4, 97.2, 98.2, 99.4],
        "low": [100.8, 99.8, 98.8, 97.8, 96.6, 97.6, 98.6],
        "ma3": [101.0, 100.0, 99.0, 98.0, 97.0, 98.0, 99.0],
        "ma15": [100.0] * 7,
        "ema_20": [100.0] * 7,
        "kc_upper": [102.0] * 7,
        "kc_lower": [97.5] * 7,
        "atr": [1.0] * 7,
    })

    # 原版函式在第一根綠 K 收盤後辨認 MA3 V 型谷底。
    first = detect_simple_ma5_signal(
        frame.iloc[:-1], live_price=float(frame["close"].iloc[-2]),
    )
    assert first["detected"] is True
    assert first["side"] == "LONG"

    # 第二根綠 K 收在下軌～中軌後，完整嚴格入口才准許開多。
    ok, reason, offset = TradingEngine._validate_strict_pivot_entry(
        frame, first["side"],
    )
    assert ok is True
    assert offset == -3
    assert "confirmation passed" in reason

    # 跨越所需下軌後即為有效；一次衝過更多軌道不能反而判成失敗。
    frame.loc[frame.index[-1], "close"] = 100.5
    frame.loc[frame.index[-1], "high"] = 100.7
    accepted, accepted_reason, _ = TradingEngine._validate_strict_pivot_entry(
        frame, first["side"],
    )
    assert accepted is True
    assert "confirmation passed" in accepted_reason
