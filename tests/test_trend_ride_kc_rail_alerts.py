# -*- coding: utf-8 -*-
"""
Tests for MA5 Trend Holding, KC Rail Direction Alert, and Net ROE Profit Lock Prioritization.
Verifies rules from user directive:
1. MA5 up, KC upper down: deceleration alert, holds (LONG).
2. MA5 down, KC lower up: deceleration alert, holds (SHORT).
3. Normal pullback to MA5 / MA13: holds, no premature exit.
4. MA5 turns flat/opposite without confirmed structural breakdown: holds.
5. Candidate pivot unconfirmed: no pivot exit.
6. True 3-point peak/valley confirmed: exits early with profit lock.
7. ROE +10% arms lock, gives back 3.0% -> exits.
8. Special reminder: Profit lock triggers even when MA5 is still trending in favorable direction!
9. Hard SL (-3 ATR) has absolute highest priority.
"""

import pytest
import pandas as pd
from core.gates.unified_pipeline_gates import ExitGate


def make_candle_df(candles):
    """Helper to create a DataFrame with required columns for ExitGate."""
    rows = []
    for c in candles:
        rows.append({
            "timestamp": c.get("timestamp", 1000),
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
            "volume": float(c.get("volume", 100)),
            "atr": float(c.get("atr", 1.0)),
            "ma_slow": float(c.get("ma_slow", 90.0)),
            "ma5": float(c.get("ma5", c["close"])),
            "kc_upper": float(c.get("kc_upper", 110.0)),
            "kc_lower": float(c.get("kc_lower", 90.0)),
            "kc_middle": float(c.get("kc_middle", 100.0)),
        })
    return pd.DataFrame(rows)


# =====================================================================
# Scenario 1 & 2: MA5 Trending with KC Rail Turning (Deceleration Alert)
# =====================================================================

def test_long_ma5_up_kc_upper_down_holds_with_alert():
    """MA5 向上、KC 上軌向下：為減速警訊，不得單獨平多，維持續抱 (VETO_HOLD)。"""
    raw_df = make_candle_df([
        {"open": 100.0, "high": 102.0, "low": 99.5, "close": 101.5, "ma_slow": 98.0, "ma5": 100.5},
        {"open": 101.5, "high": 103.5, "low": 101.0, "close": 103.0, "ma_slow": 98.5, "ma5": 102.0},
        {"open": 103.0, "high": 104.5, "low": 102.5, "close": 104.0, "ma_slow": 99.0, "ma5": 103.5},
    ])
    prev_k = {
        "ma5": 102.0,
        "kc_upper": 106.0,   # 前一根上軌較高
        "kc_middle": 100.0,
        "kc_lower": 94.0,
        "atr": 1.0,
    }
    curr_k = {
        "ma5": 103.5,        # MA5 向上 (103.5 > 102.0)
        "kc_upper": 105.2,   # KC 上軌向下 (105.2 < 106.0) -> 減速警訊
        "kc_middle": 100.2,
        "kc_lower": 95.2,
        "atr": 1.0,
        "ema_fast": 102.0,
        "ema_slow": 99.0,
        "slope": 0.001,
        "slow_slope": 0.001,
        "ma15": 101.0,
    }

    market_data = {
        "symbol": "BTC/USDT",
        "current_price": 104.0,
        "atr": 1.0,
        "kc_upper": 105.2,
        "kc_middle": 100.2,
        "kc_lower": 95.2,
        "kc_middle_slope": 0.001,
        "ema_fast": 102.0,
        "ema_slow": 99.0,
        "slow_slope": 0.001,
        "ma5": 103.5,
        "ma15": 101.0,
        "curr_k": curr_k,
        "prev_k": prev_k,
        "raw_df": raw_df,
        "macro_trend": "BULLISH",
    }

    should_exit, reason, meta = ExitGate.evaluate(
        pos={"side": "LONG", "entry_price": 100.0, "amount": 10.0, "leverage": 10},
        market_data=market_data,
    )

    assert not should_exit, f"Expected hold, got exit with {reason}"
    assert reason == "VETO_HOLD"
    assert meta.get("kc_rail_alert") == "MA5_UP_KC_UPPER_DOWN_DECELERATION_HOLD"


def test_short_ma5_down_kc_lower_up_holds_with_alert():
    """MA5 向下、KC 下軌向上：為減速警訊，不得單獨平空，維持續抱 (VETO_HOLD)。"""
    raw_df = make_candle_df([
        {"open": 100.0, "high": 100.5, "low": 98.0, "close": 98.5, "ma_slow": 102.0, "ma5": 99.5},
        {"open": 98.5, "high": 99.0, "low": 96.5, "close": 97.0, "ma_slow": 101.5, "ma5": 98.0},
        {"open": 97.0, "high": 97.5, "low": 95.5, "close": 96.0, "ma_slow": 101.0, "ma5": 96.5},
    ])
    prev_k = {
        "ma5": 98.0,
        "kc_lower": 94.0,    # 前一根下軌較低
        "kc_middle": 100.0,
        "kc_upper": 106.0,
        "atr": 1.0,
    }
    curr_k = {
        "ma5": 96.5,         # MA5 向下 (96.5 < 98.0)
        "kc_lower": 94.8,    # KC 下軌向上 (94.8 > 94.0) -> 減速警訊
        "kc_middle": 99.8,
        "kc_upper": 104.8,
        "atr": 1.0,
        "ema_fast": 98.0,
        "ema_slow": 101.0,
        "slope": -0.001,
        "slow_slope": -0.001,
        "ma15": 99.0,
    }

    market_data = {
        "symbol": "BTC/USDT",
        "current_price": 96.0,
        "atr": 1.0,
        "kc_lower": 94.8,
        "kc_middle": 99.8,
        "kc_upper": 104.8,
        "kc_middle_slope": -0.001,
        "ema_fast": 98.0,
        "ema_slow": 101.0,
        "slow_slope": -0.001,
        "ma5": 96.5,
        "ma15": 99.0,
        "curr_k": curr_k,
        "prev_k": prev_k,
        "raw_df": raw_df,
        "macro_trend": "BEARISH",
    }

    should_exit, reason, meta = ExitGate.evaluate(
        pos={"side": "SHORT", "entry_price": 100.0, "amount": 10.0, "leverage": 10},
        market_data=market_data,
    )

    assert not should_exit, f"Expected hold, got exit with {reason}"
    assert reason == "VETO_HOLD"
    assert meta.get("kc_rail_alert") == "MA5_DOWN_KC_LOWER_UP_DECELERATION_HOLD"


# =====================================================================
# Scenario 3: Normal Pullback to MA5 / MA13 Holds
# =====================================================================

def test_normal_pullback_to_ma5_ma13_holds_long():
    """多單正常回踩 MA5/MA13，趨勢多頭排列且未見真峰頂，不得提前平多。"""
    raw_df = make_candle_df([
        {"open": 100.0, "high": 103.0, "low": 99.8, "close": 102.5, "ma_slow": 98.0, "ma5": 101.0},
        {"open": 102.5, "high": 104.0, "low": 102.0, "close": 103.8, "ma_slow": 98.5, "ma5": 102.5},
        # 第三根微幅回踩收小黑，但守在 MA5/MA13 之上
        {"open": 103.8, "high": 104.0, "low": 102.8, "close": 103.0, "ma_slow": 99.0, "ma5": 103.0},
    ])
    prev_k = {"ma5": 102.5, "kc_upper": 105.0, "kc_middle": 100.0, "kc_lower": 95.0, "atr": 1.0}
    curr_k = {
        "ma5": 103.0,  # 稍微向上或持平
        "kc_upper": 105.5,
        "kc_middle": 100.2,
        "kc_lower": 95.0,
        "atr": 1.0,
        "ema_fast": 102.0,
        "ema_slow": 98.8,
        "slope": 0.001,
        "slow_slope": 0.001,
        "ma15": 101.5,
    }

    market_data = {
        "symbol": "BTC/USDT",
        "current_price": 103.0,
        "atr": 1.0,
        "kc_upper": 105.5,
        "kc_middle": 100.2,
        "kc_lower": 95.0,
        "kc_middle_slope": 0.001,
        "ema_fast": 102.0,
        "ema_slow": 98.8,
        "slow_slope": 0.001,
        "ma5": 103.0,
        "ma15": 101.5,
        "curr_k": curr_k,
        "prev_k": prev_k,
        "raw_df": raw_df,
        "macro_trend": "BULLISH",
    }

    should_exit, reason, _ = ExitGate.evaluate(
        pos={"side": "LONG", "entry_price": 100.0, "amount": 10.0, "leverage": 10},
        market_data=market_data,
    )

    assert not should_exit
    assert reason == "VETO_HOLD"


def test_normal_bounce_to_ma5_ma13_holds_short():
    """空單正常反抽 MA5/MA13，趨勢空頭排列且未見真谷底，不得提前平空。"""
    raw_df = make_candle_df([
        {"open": 100.0, "high": 100.2, "low": 97.0, "close": 97.5, "ma_slow": 102.0, "ma5": 99.0},
        {"open": 97.5, "high": 98.0, "low": 96.0, "close": 96.2, "ma_slow": 101.5, "ma5": 97.5},
        # 第三根微幅反抽收小紅，但壓在 MA5/MA13 之下
        {"open": 96.2, "high": 97.2, "low": 96.0, "close": 97.0, "ma_slow": 101.0, "ma5": 97.0},
    ])
    prev_k = {"ma5": 97.5, "kc_lower": 95.0, "kc_middle": 100.0, "kc_upper": 105.0, "atr": 1.0}
    curr_k = {
        "ma5": 97.0,  # 稍微向下或持平
        "kc_lower": 94.5,
        "kc_middle": 99.8,
        "kc_upper": 105.0,
        "atr": 1.0,
        "ema_fast": 98.0,
        "ema_slow": 101.2,
        "slope": -0.001,
        "slow_slope": -0.001,
        "ma15": 98.5,
    }

    market_data = {
        "symbol": "BTC/USDT",
        "current_price": 97.0,
        "atr": 1.0,
        "kc_lower": 94.5,
        "kc_middle": 99.8,
        "kc_upper": 105.0,
        "kc_middle_slope": -0.001,
        "ema_fast": 98.0,
        "ema_slow": 101.2,
        "slow_slope": -0.001,
        "ma5": 97.0,
        "ma15": 98.5,
        "curr_k": curr_k,
        "prev_k": prev_k,
        "raw_df": raw_df,
        "macro_trend": "BEARISH",
    }

    should_exit, reason, _ = ExitGate.evaluate(
        pos={"side": "SHORT", "entry_price": 100.0, "amount": 10.0, "leverage": 10},
        market_data=market_data,
    )

    assert not should_exit
    assert reason == "VETO_HOLD"


# =====================================================================
# Scenario 4: MA5 Turns Flat/Opposite without Confirmed Structural Reversal
# =====================================================================

def test_ma5_flat_without_confirmed_reversal_holds_long():
    """多單 MA5 轉平但慢線依然強勢向上且守穩中軌，未確認反轉前不得平多。"""
    raw_df = make_candle_df([
        {"open": 100.0, "high": 102.0, "low": 99.5, "close": 101.8, "ma_slow": 97.5, "ma5": 101.0},
        {"open": 101.8, "high": 103.0, "low": 101.5, "close": 102.5, "ma_slow": 98.0, "ma5": 102.2},
        # MA5 走平 (102.2 == 102.2)，但價格守在慢線與中軌之上
        {"open": 102.5, "high": 102.8, "low": 101.8, "close": 102.2, "ma_slow": 98.5, "ma5": 102.2},
    ])
    prev_k = {"ma5": 102.2, "kc_upper": 104.0, "kc_middle": 100.0, "kc_lower": 96.0, "atr": 1.0}
    curr_k = {
        "ma5": 102.2,
        "kc_upper": 104.0,
        "kc_middle": 100.2,
        "kc_lower": 96.2,
        "atr": 1.0,
        "ema_fast": 101.5,
        "ema_slow": 98.5,
        "slope": 0.001,
        "slow_slope": 0.001,
        "ma15": 101.0,
    }

    market_data = {
        "symbol": "BTC/USDT",
        "current_price": 102.2,
        "atr": 1.0,
        "kc_upper": 104.0,
        "kc_middle": 100.2,
        "kc_lower": 96.2,
        "kc_middle_slope": 0.001,
        "ema_fast": 101.5,
        "ema_slow": 98.5,
        "slow_slope": 0.001,
        "ma5": 102.2,
        "ma15": 101.0,
        "curr_k": curr_k,
        "prev_k": prev_k,
        "raw_df": raw_df,
        "macro_trend": "BULLISH",
    }

    should_exit, reason, meta = ExitGate.evaluate(
        pos={"side": "LONG", "entry_price": 100.0, "amount": 10.0, "leverage": 10},
        market_data=market_data,
    )

    assert not should_exit
    assert reason == "VETO_HOLD"


def test_ma5_flat_without_confirmed_reversal_holds_short():
    """空單 MA5 轉平但慢線依然強勢向下且受壓中軌，未確認反轉前不得平空。"""
    raw_df = make_candle_df([
        {"open": 100.0, "high": 100.5, "low": 98.0, "close": 98.2, "ma_slow": 102.5, "ma5": 99.0},
        {"open": 98.2, "high": 98.5, "low": 97.0, "close": 97.5, "ma_slow": 102.0, "ma5": 97.8},
        # MA5 走平 (97.8 == 97.8)，但價格受壓於慢線與中軌之下
        {"open": 97.5, "high": 98.2, "low": 97.2, "close": 97.8, "ma_slow": 101.5, "ma5": 97.8},
    ])
    prev_k = {"ma5": 97.8, "kc_lower": 96.0, "kc_middle": 100.0, "kc_upper": 104.0, "atr": 1.0}
    curr_k = {
        "ma5": 97.8,
        "kc_lower": 96.0,
        "kc_middle": 99.8,
        "kc_upper": 103.8,
        "atr": 1.0,
        "ema_fast": 98.5,
        "ema_slow": 101.5,
        "slope": -0.001,
        "slow_slope": -0.001,
        "ma15": 99.0,
    }

    market_data = {
        "symbol": "BTC/USDT",
        "current_price": 97.8,
        "atr": 1.0,
        "kc_lower": 96.0,
        "kc_middle": 99.8,
        "kc_upper": 103.8,
        "kc_middle_slope": -0.001,
        "ema_fast": 98.5,
        "ema_slow": 101.5,
        "slow_slope": -0.001,
        "ma5": 97.8,
        "ma15": 99.0,
        "curr_k": curr_k,
        "prev_k": prev_k,
        "raw_df": raw_df,
        "macro_trend": "BEARISH",
    }

    should_exit, reason, meta = ExitGate.evaluate(
        pos={"side": "SHORT", "entry_price": 100.0, "amount": 10.0, "leverage": 10},
        market_data=market_data,
    )

    assert not should_exit
    assert reason == "VETO_HOLD"


# =====================================================================
# Scenario 5 & 6: Candidate Pivot vs Confirmed True Pivot
# =====================================================================

def test_unconfirmed_candidate_pivot_does_not_exit():
    """候選峰頂（如中間棒最高，但第三棒仍收紅陽線且未跌破中點）未確認，不得依峰頂平倉。"""
    raw_df = make_candle_df([
        {"open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5, "ma_slow": 98.0, "ma5": 100.2},
        {"open": 100.5, "high": 105.0, "low": 100.2, "close": 104.0, "ma_slow": 98.5, "ma5": 102.5},
        # 第三棒繼續收紅陽線，未跌破中間棒實體中點 (102.25)
        {"open": 104.0, "high": 104.8, "low": 103.5, "close": 104.5, "ma_slow": 99.0, "ma5": 103.8},
    ])
    prev_k = {"ma5": 102.5, "kc_upper": 105.0, "kc_middle": 100.0, "kc_lower": 95.0, "atr": 1.0}
    curr_k = {
        "ma5": 103.8,
        "kc_upper": 105.5,
        "kc_middle": 100.2,
        "kc_lower": 95.0,
        "atr": 1.0,
        "ema_fast": 102.0,
        "ema_slow": 99.0,
        "slope": 0.001,
        "slow_slope": 0.001,
        "ma15": 101.0,
    }

    market_data = {
        "symbol": "BTC/USDT",
        "current_price": 104.5,
        "atr": 1.0,
        "kc_upper": 105.5,
        "kc_middle": 100.2,
        "kc_lower": 95.0,
        "kc_middle_slope": 0.001,
        "ema_fast": 102.0,
        "ema_slow": 99.0,
        "slow_slope": 0.001,
        "ma5": 103.8,
        "ma15": 101.0,
        "curr_k": curr_k,
        "prev_k": prev_k,
        "raw_df": raw_df,
        "macro_trend": "BULLISH",
    }

    should_exit, reason, _ = ExitGate.evaluate(
        pos={"side": "LONG", "entry_price": 100.0, "amount": 10.0, "leverage": 10},
        market_data=market_data,
    )

    assert not should_exit
    assert reason != "EARLY_PIVOT_PEAK_EXIT"


def test_confirmed_true_pivot_peak_exits_long():
    """已確認多頭真峰頂：中間棒衝頂，第三棒收黑跌破實體中點且破 MA5，放行 EARLY_PIVOT_PEAK_EXIT。"""
    raw_df = make_candle_df([
        {"open": 100.0, "high": 102.0, "low": 99.8, "close": 101.5, "ma_slow": 98.0, "ma5": 100.5},
        {"open": 101.5, "high": 106.0, "low": 101.2, "close": 105.0, "ma_slow": 98.5, "ma5": 103.0},
        # 第三棒收黑跌破中點 (103.25) 且跌破 MA5 (102.8)
        {"open": 105.0, "high": 105.2, "low": 101.5, "close": 102.0, "ma_slow": 99.0, "ma5": 102.8},
    ])
    prev_k = {"ma5": 103.0, "kc_upper": 105.5, "kc_middle": 100.0, "kc_lower": 95.0, "atr": 1.0}
    curr_k = {
        "ma5": 102.8,  # 下拐 (102.8 < 103.0)
        "kc_upper": 105.5,
        "kc_middle": 100.0,
        "kc_lower": 95.0,
        "atr": 1.0,
        "ema_fast": 102.0,
        "ema_slow": 99.0,
        "slope": 0.001,
        "slow_slope": 0.001,
        "ma15": 101.0,
    }

    market_data = {
        "symbol": "BTC/USDT",
        "current_price": 102.0,
        "atr": 1.0,
        "kc_upper": 105.5,
        "kc_middle": 100.0,
        "kc_lower": 95.0,
        "kc_middle_slope": 0.001,
        "ema_fast": 102.0,
        "ema_slow": 99.0,
        "slow_slope": 0.001,
        "ma5": 102.8,
        "ma15": 101.0,
        "curr_k": curr_k,
        "prev_k": prev_k,
        "raw_df": raw_df,
        "macro_trend": "BULLISH",
    }

    should_exit, reason, _ = ExitGate.evaluate(
        pos={"side": "LONG", "entry_price": 100.0, "amount": 10.0, "leverage": 10, "peak_price": 106.0},
        market_data=market_data,
    )

    assert should_exit
    assert reason == "EARLY_PIVOT_PEAK_EXIT"


def test_confirmed_true_pivot_valley_exits_short():
    """已確認空頭真谷底：中間棒探底，第三棒收紅突破實體中點且突破 MA5，放行 EARLY_PIVOT_VALLEY_EXIT。"""
    raw_df = make_candle_df([
        {"open": 100.0, "high": 100.2, "low": 98.0, "close": 98.5, "ma_slow": 102.0, "ma5": 99.5},
        {"open": 98.5, "high": 98.8, "low": 94.0, "close": 95.0, "ma_slow": 101.5, "ma5": 97.0},
        # 第三棒收紅突破中點 (96.75) 且突破 MA5 (97.2)
        {"open": 95.0, "high": 98.5, "low": 94.8, "close": 98.0, "ma_slow": 101.0, "ma5": 97.2},
    ])
    prev_k = {"ma5": 97.0, "kc_lower": 94.5, "kc_middle": 100.0, "kc_upper": 105.0, "atr": 1.0}
    curr_k = {
        "ma5": 97.2,  # 上勾 (97.2 > 97.0)
        "kc_lower": 94.5,
        "kc_middle": 100.0,
        "kc_upper": 105.0,
        "atr": 1.0,
        "ema_fast": 98.0,
        "ema_slow": 101.0,
        "slope": -0.001,
        "slow_slope": -0.001,
        "ma15": 99.0,
    }

    market_data = {
        "symbol": "BTC/USDT",
        "current_price": 98.0,
        "atr": 1.0,
        "kc_lower": 94.5,
        "kc_middle": 100.0,
        "kc_upper": 105.0,
        "kc_middle_slope": -0.001,
        "ema_fast": 98.0,
        "ema_slow": 101.0,
        "slow_slope": -0.001,
        "ma5": 97.2,
        "ma15": 99.0,
        "curr_k": curr_k,
        "prev_k": prev_k,
        "raw_df": raw_df,
        "macro_trend": "BEARISH",
    }

    should_exit, reason, _ = ExitGate.evaluate(
        pos={"side": "SHORT", "entry_price": 100.0, "amount": 10.0, "leverage": 10, "peak_price": 94.0},
        market_data=market_data,
    )

    assert should_exit
    assert reason == "EARLY_PIVOT_VALLEY_EXIT"


# =====================================================================
# Scenario 7 & 8: Special Reminder - Fixed 3% ROE Giveback Overrides MA5 Direction
# =====================================================================

def test_special_reminder_profit_lock_overrides_ma5_up_long():
    """
    特別提醒驗證：
    即使 MA5 還在向上 (curr_ma5 > prev_ma5)，但淨 ROE 已從最高 +18% 回落至 +15%（回吐 3.0 個百分點），
    仍然必須依固定鎖利規則平倉！不能因為 MA5 向上就繼續持有！
    """
    pos = {
        "side": "LONG",
        "entry_price": 100.0,
        "amount": 10.0,
        "leverage": 10,
        "profit_lock_armed": True,
        "highest_net_roe": 18.0,  # 歷史最高淨 ROE = +18%
    }
    raw_df = make_candle_df([
        {"open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5, "ma_slow": 98.0, "ma5": 100.2},
        {"open": 100.5, "high": 102.0, "low": 100.2, "close": 101.5, "ma_slow": 98.5, "ma5": 101.0},
        {"open": 101.5, "high": 102.5, "low": 101.0, "close": 102.0, "ma_slow": 99.0, "ma5": 101.8},
    ])
    prev_k = {"ma5": 101.0, "kc_upper": 104.0, "kc_middle": 100.0, "kc_lower": 96.0, "atr": 1.0}
    curr_k = {
        "ma5": 101.8,  # MA5 明顯向上 (101.8 > 101.0)
        "kc_upper": 104.5,
        "kc_middle": 100.2,
        "kc_lower": 96.0,
        "atr": 1.0,
        "ema_fast": 101.5,
        "ema_slow": 98.5,
        "slope": 0.001,
        "slow_slope": 0.001,
        "ma15": 100.5,
    }

    # 入場 100, 槓桿 10x, 報價 101.514 -> 當前淨 ROE 約 +15.0% (回吐 3.0%)
    quote = 100.0 * (1.0 + (15.0 / 100.0 + 0.0014) / 10.0)

    market_data = {
        "symbol": "BTC/USDT",
        "current_price": quote,
        "atr": 1.0,
        "kc_upper": 104.5,
        "kc_middle": 100.2,
        "kc_lower": 96.0,
        "kc_middle_slope": 0.001,
        "ema_fast": 101.5,
        "ema_slow": 98.5,
        "slow_slope": 0.001,
        "ma5": 101.8,
        "ma15": 100.5,
        "curr_k": curr_k,
        "prev_k": prev_k,
        "raw_df": raw_df,
        "macro_trend": "BULLISH",
    }

    should_exit, reason, meta = ExitGate.evaluate(
        pos=pos,
        market_data=market_data,
    )

    assert should_exit, "Profit lock must override MA5 upward momentum!"
    assert reason == "EXIT_BY_RATCHET_PROFIT_LOCK"
    assert meta.get("profit_lock_rule") == "FIXED_3PCT_GIVEBACK_ARMED_AT_10PCT"


def test_special_reminder_profit_lock_overrides_ma5_down_short():
    """
    空單特別提醒驗證：
    即使 MA5 還在向下 (curr_ma5 < prev_ma5)，但淨 ROE 已從最高 +20% 回落至 +17%（回吐 3.0 個百分點），
    仍然必須依固定鎖利規則平倉！不能因為 MA5 向下就繼續持有！
    """
    pos = {
        "side": "SHORT",
        "entry_price": 100.0,
        "amount": 10.0,
        "leverage": 10,
        "profit_lock_armed": True,
        "highest_net_roe": 20.0,  # 歷史最高淨 ROE = +20%
    }
    raw_df = make_candle_df([
        {"open": 100.0, "high": 100.5, "low": 99.0, "close": 99.5, "ma_slow": 102.0, "ma5": 99.8},
        {"open": 99.5, "high": 99.8, "low": 98.0, "close": 98.5, "ma_slow": 101.5, "ma5": 99.0},
        {"open": 98.5, "high": 98.8, "low": 97.5, "close": 98.0, "ma_slow": 101.0, "ma5": 98.2},
    ])
    prev_k = {"ma5": 99.0, "kc_lower": 96.0, "kc_middle": 100.0, "kc_upper": 104.0, "atr": 1.0}
    curr_k = {
        "ma5": 98.2,  # MA5 明顯向下 (98.2 < 99.0)
        "kc_lower": 95.5,
        "kc_middle": 99.8,
        "kc_upper": 104.0,
        "atr": 1.0,
        "ema_fast": 98.5,
        "ema_slow": 101.5,
        "slope": -0.001,
        "slow_slope": -0.001,
        "ma15": 99.5,
    }

    # 入場 100, 槓桿 10x, 報價使淨 ROE 約 +17.0% (回吐 3.0%)
    quote = 100.0 * (1.0 - (17.0 / 100.0 + 0.0014) / 10.0)

    market_data = {
        "symbol": "BTC/USDT",
        "current_price": quote,
        "atr": 1.0,
        "kc_lower": 95.5,
        "kc_middle": 99.8,
        "kc_upper": 104.0,
        "kc_middle_slope": -0.001,
        "ema_fast": 98.5,
        "ema_slow": 101.5,
        "slow_slope": -0.001,
        "ma5": 98.2,
        "ma15": 99.5,
        "curr_k": curr_k,
        "prev_k": prev_k,
        "raw_df": raw_df,
        "macro_trend": "BEARISH",
    }

    should_exit, reason, meta = ExitGate.evaluate(
        pos=pos,
        market_data=market_data,
    )

    assert should_exit, "Profit lock must override MA5 downward momentum!"
    assert reason == "EXIT_BY_RATCHET_PROFIT_LOCK"
    assert meta.get("profit_lock_rule") == "FIXED_3PCT_GIVEBACK_ARMED_AT_10PCT"


# =====================================================================
# Scenario 9: Hard Stop Loss Absolute Priority
# =====================================================================

def test_hard_stop_loss_overrides_everything():
    """硬止損 (-3 ATR) 具備最高第一優先級，MA5 順向或多頭排列皆不得攔截！"""
    raw_df = make_candle_df([
        {"open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5, "ma_slow": 98.0, "ma5": 100.2},
        {"open": 100.5, "high": 102.0, "low": 100.2, "close": 101.5, "ma_slow": 98.5, "ma5": 101.0},
        {"open": 101.5, "high": 102.5, "low": 96.0, "close": 96.5, "ma_slow": 99.0, "ma5": 101.5},
    ])
    prev_k = {"ma5": 101.0, "kc_upper": 104.0, "kc_middle": 100.0, "kc_lower": 96.0, "atr": 1.0}
    curr_k = {
        "ma5": 101.5,  # 均線向上
        "kc_upper": 104.0,
        "kc_middle": 100.0,
        "kc_lower": 96.0,
        "atr": 1.0,    # ATR = 1.0 -> 3 ATR = 3.0 -> 跌破 97.0
        "ema_fast": 101.0,
        "ema_slow": 98.0,
        "slope": 0.001,
        "slow_slope": 0.001,
        "ma15": 100.0,
    }

    market_data = {
        "symbol": "BTC/USDT",
        "current_price": 96.5,  # -3.5 ATR 觸發 Hard SL
        "atr": 1.0,
        "kc_upper": 104.0,
        "kc_middle": 100.0,
        "kc_lower": 96.0,
        "kc_middle_slope": 0.001,
        "ema_fast": 101.0,
        "ema_slow": 98.0,
        "slow_slope": 0.001,
        "ma5": 101.5,
        "ma15": 100.0,
        "curr_k": curr_k,
        "prev_k": prev_k,
        "raw_df": raw_df,
        "macro_trend": "BULLISH",
    }

    should_exit, reason, _ = ExitGate.evaluate(
        pos={"side": "LONG", "entry_price": 100.0, "amount": 10.0, "leverage": 10},
        market_data=market_data,
    )

    assert should_exit
    assert reason == "CIRCUIT_BREAKER_HARD_SL"
