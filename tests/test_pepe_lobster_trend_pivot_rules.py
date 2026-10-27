import math
import pandas as pd
import pytest

from core.services.swing_pivot_rules import (
    evaluate_swing_pivot_entry,
    evaluate_outer_rail_reversal_exit,
)
from core.gates.central_entry_gate import CentralEntryGate


def test_swing_pivot_blocked_against_macro_trend():
    """
    驗證：
    1. 空頭趨勢下 (macro_trend == 'BEARISH')，即便 3 根 K 棒出現谷底 Valley，
       CentralEntryGate 必須一票否決做多 (BLOCKED_LONG_ON_BEARISH_TREND)！
    2. 多頭趨勢下 (macro_trend == 'BULLISH')，即便 3 根 K 棒出現峰頂 Peak，
       CentralEntryGate 必須一票否決做空 (BLOCKED_SHORT_ON_BULLISH_TREND)！
    """
    # 構造 Valley (谷底) 數據
    valley_bars = [
        dict(timestamp=60_000, open=100.0, high=102.0, low=99.0, close=101.0, atr=1.0,
             ma5=100.5, ma15=100.0, kc_upper=105.0, kc_middle=100.0, kc_lower=95.0, is_closed=True),
        dict(timestamp=120_000, open=100.5, high=101.0, low=97.0, close=98.0, atr=1.0,
             ma5=99.0, ma15=99.5, kc_upper=105.0, kc_middle=100.0, kc_lower=95.0, is_closed=True),
        dict(timestamp=180_000, open=98.5, high=101.5, low=98.0, close=101.0, atr=1.0,
             ma5=100.0, ma15=99.5, kc_upper=105.0, kc_middle=100.0, kc_lower=95.0, is_closed=True),
    ]
    df_valley = pd.DataFrame(valley_bars)
    df_valley.attrs["timeframe_ms"] = 60_000.0
    df_valley.attrs["macro_trend"] = "BEARISH"

    # 在空頭趨勢中嘗試以 SWING_PIVOT 開多
    res_long = CentralEntryGate.verify_and_authorize(
        symbol="1000PEPE/USDT",
        side="LONG",
        price=101.0,
        klines_or_snapshot=df_valley,
        source="UNIFIED_PIPELINE|SWING_PIVOT",
    )
    assert res_long.passed is False
    assert res_long.reason in ("BLOCKED_LONG_ON_BEARISH_TREND", "BLOCKED_DIRECTION_LOCK_LONG_BELOW_MID")

    # 構造 Peak (峰頂) 數據
    peak_bars = [
        dict(timestamp=60_000, open=100.0, high=101.0, low=98.0, close=99.0, atr=1.0,
             ma5=99.5, ma15=100.0, kc_upper=105.0, kc_middle=100.0, kc_lower=95.0, is_closed=True),
        dict(timestamp=120_000, open=99.5, high=103.0, low=99.0, close=102.0, atr=1.0,
             ma5=101.0, ma15=100.5, kc_upper=105.0, kc_middle=100.0, kc_lower=95.0, is_closed=True),
        dict(timestamp=180_000, open=101.5, high=102.0, low=98.5, close=99.0, atr=1.0,
             ma5=100.0, ma15=100.5, kc_upper=105.0, kc_middle=100.0, kc_lower=95.0, is_closed=True),
    ]
    df_peak = pd.DataFrame(peak_bars)
    df_peak.attrs["timeframe_ms"] = 60_000.0
    df_peak.attrs["macro_trend"] = "BULLISH"

    # 在多頭趨勢中嘗試以 SWING_PIVOT 開空
    res_short = CentralEntryGate.verify_and_authorize(
        symbol="1000PEPE/USDT",
        side="SHORT",
        price=99.0,
        klines_or_snapshot=df_peak,
        source="UNIFIED_PIPELINE|SWING_PIVOT",
    )
    assert res_short.passed is False
    assert res_short.reason in ("BLOCKED_SHORT_ON_BULLISH_TREND", "BLOCKED_DIRECTION_LOCK_SHORT_ABOVE_MID")


def test_outer_rail_reversal_does_not_exit_on_normal_pullback():
    """
    驗證龍蝦破軌開倉後，普通 MA3 回踩不會被判定為 EXIT_SHORT_AT_BOTTOM 或 EXIT_LONG_AT_PEAK！
    必須具備確定性吞沒或長影線頂底反轉才放行。
    """
    # 構造做空破軌後，行情僅僅小幅收陽回踩 MA3 (未發生吞沒與長下影線)
    bars = [
        # 破軌 K 線 (觸碰下軌)
        dict(timestamp=60_000, open=100.0, high=100.2, low=94.5, close=95.0,
             kc_upper=105.0, kc_lower=96.0, ma3=97.0, atr=2.0, is_closed=True),
        # 普通回踩 K 線 (close 96.5 > ma3 96.0, 但無吞沒，下影線微小)
        dict(timestamp=120_000, open=95.0, high=97.0, low=94.8, close=96.5,
             kc_upper=105.0, kc_lower=96.0, ma3=96.0, atr=2.0, is_closed=True),
    ]
    df = pd.DataFrame(bars)
    df.attrs["timeframe_ms"] = 60_000.0

    pos = {
        "symbol": "龙虾/USDT",
        "side": "SHORT",
        "open_timestamp": 50.0,  # 50 秒開倉
        "entry_price": 95.0,
    }

    # 驗證 evaluate_outer_rail_reversal_exit 絕不能放行普通回踩！
    evidence = evaluate_outer_rail_reversal_exit(pos, df)
    assert evidence is None, f"普通 MA3 回踩不應觸發平倉，實際返回了: {evidence}"


def test_breakout_holding_protection_against_adverse_abnormal():
    """
    驗證 1000PEPE 破軌做空開倉後，小幅回踩兩根但收盤價仍在 KC 中軌下方，
    HoldingProtectionExitGate 必須一票否決回踩平倉 (VETO_HOLDING_UNTIL_TRUE_PIVOT)！
    """
    from core.gates.holding_protection_gate import HoldingProtectionExitGate

    # 構造 3 根 K 棒，前兩根反向收陽 (每根 0.6 ATR)，但收盤價仍在 KC 中軌 100.0 下方 (例如 98.0)
    bars = [
        dict(timestamp=60_000, open=95.0, high=96.0, low=94.5, close=95.5, atr=1.0,
             kc_middle=100.0, kc_upper=105.0, kc_lower=95.0, is_closed=True),
        dict(timestamp=120_000, open=95.5, high=97.0, low=95.5, close=96.5, atr=1.0,
             kc_middle=100.0, kc_upper=105.0, kc_lower=95.0, is_closed=True),
        dict(timestamp=180_000, open=96.5, high=98.0, low=96.5, close=97.5, atr=1.0,
             kc_middle=100.0, kc_upper=105.0, kc_lower=95.0, is_closed=True),
    ]
    df = pd.DataFrame(bars)
    df.attrs["timeframe_ms"] = 60_000.0

    pos = {
        "symbol": "1000PEPE/USDT",
        "side": "SHORT",
        "open_timestamp": 50.0,
        "entry_price": 95.0,
        "entry_mode": "BREAKOUT",
    }

    should_exit, reason, _ = HoldingProtectionExitGate.validate_exit(
        position=pos,
        frame=df,
        quote=97.5,
        candidate_reason="EXIT_BY_TWO_CLOSED_ADVERSE_ABNORMAL",
    )
    assert should_exit is False, f"未破中軌且未到真峰谷時回踩不應平倉！"
    assert reason == "VETO_HOLDING_UNTIL_TRUE_PIVOT"


def test_continuation_outside_kc_exempt_from_wave_reset_lock():
    """
    驗證龍蝦平倉後，價格依然在外軌強勢區狂飆時，
    CONTINUATION_OUTSIDE_KC_SHORT 必須豁免回踩中軌重置鎖 (VETO_SAME_WAVE_REENTRY)！
    """
    from core.gates.unified_pipeline_gates import EntryGate

    # 構造單邊強跌、已在下軌外的行情
    bars = [
        dict(timestamp=60_000, open=100.0, high=100.2, low=94.0, close=94.5,
             kc_upper=103.0, kc_middle=99.0, kc_lower=95.0, ma3=95.0, ma5=95.0, ma15=98.0, atr=2.0, is_closed=True),
        dict(timestamp=120_000, open=94.5, high=94.6, low=92.0, close=92.5,
             kc_upper=103.0, kc_middle=99.0, kc_lower=95.0, ma3=93.5, ma5=94.0, ma15=98.0, atr=2.0, is_closed=True),
    ]
    df = pd.DataFrame(bars)
    df.attrs["timeframe_ms"] = 60_000.0

    market_data = {
        "current_price": 92.5,
        "atr": 2.0,
        "kc_upper": 103.0,
        "kc_middle": 99.0,
        "kc_lower": 95.0,
        "ma3": 93.5,
        "prev_ma3": 95.0,
        "ma5": 94.0,
        "ma15": 98.0,
        "kc_middle_slope": -0.05,
        "curr_k": df.iloc[-1].to_dict(),
        "prev_k": df.iloc[-2].to_dict(),
        "raw_df": df,
        "macro_trend": "BEARISH",
        "min_bandwidth_threshold": 0.005,
    }

    last_exit_context = {
        "side": "SHORT",
        "last_exit_direction": "SHORT",
        "wave_reset_confirmed": False,  # 未曾回碰中軌
        "timestamp": 10.0,
        "current_time": 300.0,  # 已過 290 秒
        "hourly_entry_count": 1,
        "exit_low": 93.0,  # 當前 92.5 已破前次平倉低點
    }

    should_entry, reason, _ = EntryGate.evaluate(
        signal_type="CONTINUATION_OUTSIDE_KC_SHORT",
        market_data=market_data,
        last_exit_context=last_exit_context,
    )
    assert "VETO_SAME_WAVE_REENTRY" not in reason, f"延續開倉不應被回踩中軌重置鎖阻斷，實際: {reason}"


def test_staged_profit_lock_waits_for_confirmed_short_valley(monkeypatch):
    """空頭階梯鎖利不能在已收線真谷底確認前提前平倉。"""
    from core.gates import unified_pipeline_gates as gates

    pos = {
        "symbol": "1000PEPE/USDT", "side": "SHORT",
        "entry_price": 100.0, "qty": 10.0, "leverage": 5.0,
    }
    market_data = {
        "current_price": 90.0, "raw_df": pd.DataFrame(),
        "macro_trend": "BEARISH",
    }
    monkeypatch.setattr(
        gates.ExitGate, "_evaluate_with_holding_rules",
        staticmethod(lambda *args, **kwargs: (
            True, "EXIT_BY_STAGED_PROFIT_LOCK_TIER2", {"stage": 2},
        )),
    )
    monkeypatch.setattr(
        "core.gates.holding_protection_gate.has_confirmed_true_pivot",
        lambda *args, **kwargs: (False, "HOLD", {}),
    )

    should_exit, reason, meta = gates.ExitGate.evaluate(pos, market_data)
    assert should_exit is False
    assert reason == "VETO_HOLDING_UNTIL_TRUE_PIVOT"
    assert meta["required_pivot"] == "CONFIRMED_TRUE_VALLEY"


def test_staged_profit_lock_exits_on_confirmed_short_valley(monkeypatch):
    """空頭真谷底一經收線確認，階梯鎖利候選可執行平倉。"""
    from core.gates import unified_pipeline_gates as gates

    pos = {
        "symbol": "1000PEPE/USDT", "side": "SHORT",
        "entry_price": 100.0, "qty": 10.0, "leverage": 5.0,
    }
    market_data = {
        "current_price": 90.0, "raw_df": pd.DataFrame(),
        "macro_trend": "BEARISH",
    }
    monkeypatch.setattr(
        gates.ExitGate, "_evaluate_with_holding_rules",
        staticmethod(lambda *args, **kwargs: (
            True, "EXIT_BY_STAGED_PROFIT_LOCK_TIER2", {"stage": 2},
        )),
    )
    monkeypatch.setattr(
        "core.gates.holding_protection_gate.has_confirmed_true_pivot",
        lambda *args, **kwargs: (True, "TRUE_VALLEY", {"pivot_low": 88.0}),
    )
    monkeypatch.setattr(
        "core.gates.holding_protection_gate.HoldingProtectionExitGate.validate_exit",
        lambda position, frame, quote, candidate_reason, details=None: (
            candidate_reason == "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT",
            candidate_reason, details or {},
        ),
    )

    should_exit, reason, meta = gates.ExitGate.evaluate(pos, market_data)
    assert should_exit is True
    assert reason == "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT"
    assert meta["pivot_low"] == 88.0

