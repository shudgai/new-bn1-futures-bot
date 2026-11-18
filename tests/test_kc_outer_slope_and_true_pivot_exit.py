import pandas as pd
import pytest

from core.gates.meme_shared_protection_gate import MemeSharedProtectionGate
from core.gates.trend_holding_immunity_and_exit_gate import TrendHoldingAndExitManager


def _make_df(rows):
    return pd.DataFrame(rows)


def test_kc_upper_slope_up_and_macro_up_allows_long_entry():
    df_5m = _make_df([
        {
            "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5,
            "atr": 1.0, "kc_upper": 102.0, "kc_middle": 100.0, "kc_lower": 98.0,
            "ma5": 100.2, "ma15": 100.3,
        },
        {
            "open": 100.5, "high": 101.5, "low": 100.0, "close": 101.0,
            "atr": 1.0, "kc_upper": 102.5, "kc_middle": 100.2, "kc_lower": 97.9,
            "ma5": 100.4, "ma15": 100.4,
        },
    ])
    df_15m = _make_df([
        {"open": 99.0, "high": 101.0, "low": 98.5, "close": 100.5, "kc_middle": 99.5, "kc_upper": 102.0, "kc_lower": 97.0, "ma15": 99.8, "atr": 1.2},
        {"open": 100.5, "high": 102.0, "low": 100.0, "close": 101.5, "kc_middle": 100.0, "kc_upper": 102.8, "kc_lower": 97.2, "ma15": 100.0, "atr": 1.2},
    ])

    passed, reason, meta = MemeSharedProtectionGate.evaluate_entry(
        df_5m=df_5m,
        df_15m=df_15m,
        quote=101.0,
        side="LONG",
        symbol="TEST/USDT",
    )
    assert passed is True
    assert "MODE_KC_OUTER_SLOPE_MACRO" in reason
    assert meta["mode"] == "MODE_KC_OUTER_SLOPE_MACRO"


def test_kc_lower_slope_down_and_macro_down_allows_short_entry():
    df_5m = _make_df([
        {
            "open": 100.0, "high": 100.5, "low": 99.0, "close": 99.5,
            "atr": 1.0, "kc_upper": 102.0, "kc_middle": 100.0, "kc_lower": 98.0,
            "ma5": 99.8, "ma15": 99.7,
        },
        {
            "open": 99.5, "high": 100.0, "low": 98.5, "close": 99.0,
            "atr": 1.0, "kc_upper": 102.1, "kc_middle": 99.8, "kc_lower": 97.5,
            "ma5": 99.6, "ma15": 99.6,
        },
    ])
    df_15m = _make_df([
        {"open": 101.0, "high": 101.5, "low": 99.0, "close": 99.5, "kc_middle": 100.5, "kc_upper": 103.0, "kc_lower": 98.0, "ma15": 100.2, "atr": 1.2},
        {"open": 99.5, "high": 100.0, "low": 98.0, "close": 98.5, "kc_middle": 100.0, "kc_upper": 102.8, "kc_lower": 97.2, "ma15": 100.0, "atr": 1.2},
    ])

    passed, reason, meta = MemeSharedProtectionGate.evaluate_entry(
        df_5m=df_5m,
        df_15m=df_15m,
        quote=99.0,
        side="SHORT",
        symbol="TEST/USDT",
    )
    assert passed is True
    assert "MODE_KC_OUTER_SLOPE_MACRO" in reason
    assert meta["mode"] == "MODE_KC_OUTER_SLOPE_MACRO"


def test_exit_holds_until_true_pivot_and_exits_on_true_peak():
    pos = {
        "side": "LONG",
        "entry_price": 100.0,
        "qty": 1.0,
        "leverage": 5.0,
        "open_timestamp": 1_700_000_000_000,
    }

    # 尚未形成真峰頂：持續走高或無三點包夾
    df_no_pivot = _make_df([
        {"timestamp": 1_700_000_060_000, "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.8, "atr": 1.0, "kc_middle": 100.0, "kc_upper": 102.0, "kc_lower": 98.0, "ma5": 100.5, "ma15": 100.0},
        {"timestamp": 1_700_000_120_000, "open": 100.8, "high": 102.0, "low": 100.5, "close": 101.8, "atr": 1.0, "kc_middle": 100.3, "kc_upper": 102.3, "kc_lower": 98.3, "ma5": 101.0, "ma15": 100.2},
        {"timestamp": 1_700_000_180_000, "open": 101.8, "high": 102.5, "low": 101.2, "close": 102.2, "atr": 1.0, "kc_middle": 100.6, "kc_upper": 102.6, "kc_lower": 98.6, "ma5": 101.5, "ma15": 100.4},
    ])
    can_exit, reason, _ = TrendHoldingAndExitManager.evaluate_exit(
        pos, df_no_pivot, quote=102.0, candidate_reason="EXIT_BY_RATCHET_PROFIT_LOCK"
    )
    assert can_exit is False
    assert reason == "REJECT_EXIT: HOLDING_UNTIL_TRUE_PIVOT"

    # 已收線形成三點真峰頂（中間棒 High 104.0 鶴立雞群，第三棒收黑跌破實體中點）
    df_peak = _make_df([
        {"timestamp": 1_700_000_060_000, "open": 100.5, "high": 102.0, "low": 100.2, "close": 101.8, "atr": 1.0, "kc_middle": 100.5, "kc_upper": 102.5, "kc_lower": 98.5, "ma5": 101.2, "ma15": 100.3},
        {"timestamp": 1_700_000_120_000, "open": 101.8, "high": 104.0, "low": 101.6, "close": 103.6, "atr": 1.0, "kc_middle": 101.0, "kc_upper": 103.0, "kc_lower": 99.0, "ma5": 102.2, "ma15": 100.6},
        {"timestamp": 1_700_000_180_000, "open": 103.5, "high": 103.6, "low": 102.0, "close": 102.2, "atr": 1.0, "kc_middle": 101.2, "kc_upper": 103.2, "kc_lower": 99.2, "ma5": 102.5, "ma15": 100.8},
        {"timestamp": 1_700_000_240_000, "open": 102.2, "high": 102.4, "low": 101.8, "close": 102.0, "atr": 1.0, "kc_middle": 101.3, "kc_upper": 103.3, "kc_lower": 99.3, "ma5": 102.4, "ma15": 100.9},
    ])
    can_exit2, reason2, _ = TrendHoldingAndExitManager.evaluate_exit(
        pos, df_peak, quote=102.0
    )
    assert can_exit2 is True
    assert reason2 == "EARLY_PIVOT_PEAK_EXIT"
