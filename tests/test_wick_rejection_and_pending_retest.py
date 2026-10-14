"""Unit tests for Wick Rejection Gate & Pending Retest Entry mechanism."""
import pytest
import pandas as pd
from unittest.mock import Mock

from core.services.entry_contract import (
    detect_raw_triggers,
    check_entry_gates,
    _PENDING_WICK_REENTRY_STATE,
)
from core.services.strategies.unified_entry_strategy import evaluate_closed_entry


def make_kline_df(rows, timeframe_ms=60000):
    """
    rows: list of dicts with keys:
    timestamp, open, high, low, close, kc_upper, kc_lower, kc_middle, ma5, ma15, atr, ma3
    """
    data = []
    for i, r in enumerate(rows):
        d = {
            'timestamp': r.get('timestamp', (i + 1) * timeframe_ms),
            'open': float(r['open']),
            'high': float(r['high']),
            'low': float(r['low']),
            'close': float(r['close']),
            'kc_upper': float(r.get('kc_upper', 110.0)),
            'kc_lower': float(r.get('kc_lower', 90.0)),
            'kc_middle': float(r.get('kc_middle', 100.0)),
            'ma3': float(r.get('ma3', r.get('ma5', 102.0))),
            'ma5': float(r.get('ma5', 102.0)),
            'ma15': float(r.get('ma15', 100.0)),
            'atr': float(r.get('atr', 2.0)),
            'kc_trend': r.get('kc_trend', 'UP'),
            'is_closed': True,
        }
        data.append(d)
    df = pd.DataFrame(data)
    df.attrs['timeframe_ms'] = timeframe_ms
    return df


def test_long_wick_rejection_gate():
    """多單開倉檢核：當根上影線過長（避雷針），一票否決開多並記錄 pending_long_reentry_high。"""
    _PENDING_WICK_REENTRY_STATE.clear()
    symbol = "TEST_LONG_REJECT"

    # 突破 KC 上軌（kc_upper=110），但上影線極長：
    # open=109, close=111, high=114, low=108.5, body = 2, upper_wick = 3 (> 0.6 * 2 = 1.2)
    # candle_range = 5.5, body/range = 2/5.5 = 0.3636 < 0.40
    # 提供足夠 K 棒（>= 6 根）滿足 MA 歷史長度
    rows = [
        {'open': 99, 'high': 100, 'low': 98.5, 'close': 99.5, 'kc_middle': 99, 'ma5': 99.5, 'ma15': 98},
        {'open': 99.5, 'high': 100.5, 'low': 99, 'close': 100, 'kc_middle': 99.5, 'ma5': 100, 'ma15': 98.5},
        {'open': 100, 'high': 101, 'low': 99, 'close': 100.5, 'kc_middle': 100, 'ma5': 100.5, 'ma15': 99},
        {'open': 100.5, 'high': 103, 'low': 100, 'close': 102, 'kc_middle': 100.5, 'ma5': 101, 'ma15': 99.5},
        {'open': 102, 'high': 106, 'low': 101.5, 'close': 105, 'kc_middle': 101, 'ma5': 103.0, 'ma15': 100},
        {'open': 105, 'high': 108, 'low': 104.5, 'close': 107, 'kc_middle': 101.2, 'ma5': 105.5, 'ma15': 100.8},
        {
            'open': 109, 'high': 114, 'low': 108.5, 'close': 111,
            'kc_upper': 110.0, 'kc_lower': 90.0, 'kc_middle': 101.5,
            'ma3': 110.5, 'ma5': 110.2, 'ma15': 102.0, 'atr': 2.0, 'kc_trend': 'UP',
        }
    ]
    df = make_kline_df(rows)

    # 1. 檢測 detect_raw_triggers
    side, trigger = detect_raw_triggers(df, symbol=symbol)
    assert side is None
    assert trigger == "EXCESSIVE_UPPER_WICK"

    # 2. 驗證 pending 狀態已記錄
    assert symbol in _PENDING_WICK_REENTRY_STATE
    p_state = _PENDING_WICK_REENTRY_STATE[symbol]
    assert p_state['side'] == 'LONG'
    assert p_state['extreme_price'] == 114.0

    # 3. 檢測 evaluate_closed_entry
    allowed, reason, data = evaluate_closed_entry(df, side='LONG', price=111.0, symbol=symbol)
    assert not allowed
    assert "EXCESSIVE_UPPER_WICK" in reason


def test_short_wick_rejection_gate():
    """空單開倉檢核：當根下影線過長（下影托底），一票否決開空並記錄 pending_short_reentry_low。"""
    _PENDING_WICK_REENTRY_STATE.clear()
    symbol = "TEST_SHORT_REJECT"

    # 跌破 KC 下軌（kc_lower=90），但下影線極長：
    # open=91, close=89, low=86, high=91.5, body = 2, lower_wick = 3 (> 0.6 * 2 = 1.2)
    # candle_range = 5.5, body/range = 2/5.5 = 0.3636 < 0.40
    # 提供足夠 K 棒（>= 6 根）滿足 MA 歷史長度
    rows = [
        {'open': 101, 'high': 101.5, 'low': 100, 'close': 100.5, 'kc_middle': 101, 'ma5': 100.5, 'ma15': 102},
        {'open': 100.5, 'high': 101, 'low': 99.5, 'close': 100, 'kc_middle': 100.5, 'ma5': 100, 'ma15': 101.5},
        {'open': 100, 'high': 101, 'low': 99, 'close': 99.5, 'kc_middle': 100, 'ma5': 99.5, 'ma15': 101},
        {'open': 99.5, 'high': 100, 'low': 97, 'close': 98, 'kc_middle': 99.5, 'ma5': 98.5, 'ma15': 100.5},
        {'open': 98, 'high': 98.5, 'low': 94, 'close': 95, 'kc_middle': 99, 'ma5': 96.5, 'ma15': 100},
        {'open': 95, 'high': 95.5, 'low': 92, 'close': 93, 'kc_middle': 98.8, 'ma5': 94.0, 'ma15': 99.0},
        {
            'open': 91, 'high': 91.5, 'low': 86, 'close': 89,
            'kc_upper': 110.0, 'kc_lower': 90.0, 'kc_middle': 98.5,
            'ma3': 89.5, 'ma5': 89.8, 'ma15': 98.0, 'atr': 2.0, 'kc_trend': 'DOWN',
        }
    ]
    df = make_kline_df(rows)

    # 1. 檢測 detect_raw_triggers
    side, trigger = detect_raw_triggers(df, symbol=symbol)
    assert side is None
    assert trigger == "EXCESSIVE_LOWER_WICK"

    # 2. 驗證 pending 狀態已記錄
    assert symbol in _PENDING_WICK_REENTRY_STATE
    p_state = _PENDING_WICK_REENTRY_STATE[symbol]
    assert p_state['side'] == 'SHORT'
    assert p_state['extreme_price'] == 86.0

    # 3. 檢測 evaluate_closed_entry
    allowed, reason, data = evaluate_closed_entry(df, side='SHORT', price=89.0, symbol=symbol)
    assert not allowed
    assert "EXCESSIVE_LOWER_WICK" in reason


def test_long_pending_retest_structure_1_pullback_held():
    """多單【結構 1 - 回踩均線踩穩】：價格回踩 MA5 或 MA15 獲支撐未跌破，且當根 1m 收陽站回 MA5 上方。"""
    _PENDING_WICK_REENTRY_STATE.clear()
    symbol = "TEST_LONG_RETEST_1"

    # 先前一根觸發長上影否決
    base_time = 1000000000
    _PENDING_WICK_REENTRY_STATE[symbol] = {
        'side': 'LONG',
        'bar_id': base_time,
        'extreme_price': 115.0,
    }

    # 次根 K 線：回踩 MA5 (ma5=110, ma15=105)，最低點 109.5 <= 110，收盤 110.8 站回 MA5 上方收陽
    rows = [
        {'timestamp': base_time - 120000, 'open': 105, 'high': 107, 'low': 104, 'close': 106, 'ma5': 106, 'ma15': 104, 'kc_middle': 103},
        {'timestamp': base_time - 60000, 'open': 106, 'high': 108, 'low': 105, 'close': 107.5, 'ma5': 107, 'ma15': 104.5, 'kc_middle': 103.5},
        {'timestamp': base_time, 'open': 107.5, 'high': 115, 'low': 107, 'close': 110, 'ma5': 108.5, 'ma15': 105, 'kc_middle': 104},
        {
            'timestamp': base_time + 60000,
            'open': 110.0, 'high': 111.5, 'low': 109.5, 'close': 110.8,
            'kc_upper': 112.0, 'kc_lower': 92.0, 'kc_middle': 104.5,
            'ma5': 110.0, 'ma15': 105.5, 'atr': 2.0, 'kc_trend': 'UP',
        }
    ]
    df = make_kline_df(rows)

    side, trigger = detect_raw_triggers(df, symbol=symbol)
    assert side == "LONG"
    assert trigger == "TRIGGER_PENDING_RETEST_ENTRY"
    # 狀態應被消耗清空
    assert symbol not in _PENDING_WICK_REENTRY_STATE


def test_long_pending_retest_structure_2_breakout_reclaim():
    """多單【結構 2 - 飽滿實體突破影線極值】：出現實體陽線（body >= 0.5 ATR 且上影線 <= 0.3 body），收盤突破 pending_long_reentry_high。"""
    _PENDING_WICK_REENTRY_STATE.clear()
    symbol = "TEST_LONG_RETEST_2"

    base_time = 1000000000
    pending_high = 114.0
    _PENDING_WICK_REENTRY_STATE[symbol] = {
        'side': 'LONG',
        'bar_id': base_time,
        'extreme_price': pending_high,
    }

    # 次根 K 線：實體飽滿陽線直接突破 114.0！
    # open=112.0, close=115.5, high=116.0, low=111.8
    # body = 3.5 >= 0.5 * 2 = 1.0; upper_wick = 116.0 - 115.5 = 0.5 <= 0.3 * 3.5 = 1.05
    # close = 115.5 > 114.0
    rows = [
        {'timestamp': base_time - 120000, 'open': 105, 'high': 107, 'low': 104, 'close': 106, 'ma5': 106, 'ma15': 104, 'kc_middle': 103},
        {'timestamp': base_time - 60000, 'open': 106, 'high': 108, 'low': 105, 'close': 107.5, 'ma5': 107, 'ma15': 104.5, 'kc_middle': 103.5},
        {'timestamp': base_time, 'open': 107.5, 'high': 114, 'low': 107, 'close': 111, 'ma5': 108.5, 'ma15': 105, 'kc_middle': 104},
        {
            'timestamp': base_time + 60000,
            'open': 112.0, 'high': 116.0, 'low': 111.8, 'close': 115.5,
            'kc_upper': 113.0, 'kc_lower': 93.0, 'kc_middle': 104.5,
            'ma5': 114.5, 'ma15': 106.0, 'atr': 2.0, 'kc_trend': 'UP',
        }
    ]
    df = make_kline_df(rows)

    side, trigger = detect_raw_triggers(df, symbol=symbol)
    assert side == "LONG"
    assert trigger == "TRIGGER_PENDING_RETEST_ENTRY"
    assert symbol not in _PENDING_WICK_REENTRY_STATE


def test_short_pending_retest_structure_1_pullback_rejected():
    """空單【結構 1 - 反彈回抽均線受阻】：價格反彈回抽 MA5 或 MA15 受阻未突破，且當根 1m 收陰壓制在 MA5 下方。"""
    _PENDING_WICK_REENTRY_STATE.clear()
    symbol = "TEST_SHORT_RETEST_1"

    base_time = 1000000000
    _PENDING_WICK_REENTRY_STATE[symbol] = {
        'side': 'SHORT',
        'bar_id': base_time,
        'extreme_price': 85.0,
    }

    # 次根 K 線：回抽 MA5 (ma5=90, ma15=95)，最高點 90.5 >= 90，收盤 89.2 壓制在 MA5 下方收陰
    rows = [
        {'timestamp': base_time - 120000, 'open': 95, 'high': 96, 'low': 93, 'close': 94, 'ma5': 94, 'ma15': 96, 'kc_middle': 97},
        {'timestamp': base_time - 60000, 'open': 94, 'high': 95, 'low': 92, 'close': 92.5, 'ma5': 93, 'ma15': 95.5, 'kc_middle': 96.5},
        {'timestamp': base_time, 'open': 92.5, 'high': 93, 'low': 85, 'close': 89, 'ma5': 91.5, 'ma15': 95, 'kc_middle': 96},
        {
            'timestamp': base_time + 60000,
            'open': 89.5, 'high': 90.5, 'low': 88.5, 'close': 89.2,
            'kc_upper': 108.0, 'kc_lower': 88.0, 'kc_middle': 95.5,
            'ma5': 90.0, 'ma15': 94.5, 'atr': 2.0, 'kc_trend': 'DOWN',
        }
    ]
    df = make_kline_df(rows)

    side, trigger = detect_raw_triggers(df, symbol=symbol)
    assert side == "SHORT"
    assert trigger == "TRIGGER_PENDING_RETEST_ENTRY"
    assert symbol not in _PENDING_WICK_REENTRY_STATE


def test_short_pending_retest_structure_2_breakdown_reclaim():
    """空單【結構 2 - 飽滿實體跌破影線極值】：出現實體陰線（body >= 0.5 ATR 且下影線 <= 0.3 body），收盤跌破 pending_short_reentry_low。"""
    _PENDING_WICK_REENTRY_STATE.clear()
    symbol = "TEST_SHORT_RETEST_2"

    base_time = 1000000000
    pending_low = 86.0
    _PENDING_WICK_REENTRY_STATE[symbol] = {
        'side': 'SHORT',
        'bar_id': base_time,
        'extreme_price': pending_low,
    }

    # 次根 K 線：實體飽滿陰線直接跌破 86.0！
    # open=88.0, close=84.5, low=84.0, high=88.2
    # body = 3.5 >= 0.5 * 2 = 1.0; lower_wick = 84.5 - 84.0 = 0.5 <= 0.3 * 3.5 = 1.05
    # close = 84.5 < 86.0
    rows = [
        {'timestamp': base_time - 120000, 'open': 95, 'high': 96, 'low': 93, 'close': 94, 'ma5': 94, 'ma15': 96, 'kc_middle': 97},
        {'timestamp': base_time - 60000, 'open': 94, 'high': 95, 'low': 92, 'close': 92.5, 'ma5': 93, 'ma15': 95.5, 'kc_middle': 96.5},
        {'timestamp': base_time, 'open': 92.5, 'high': 93, 'low': 86, 'close': 89, 'ma5': 91.5, 'ma15': 95, 'kc_middle': 96},
        {
            'timestamp': base_time + 60000,
            'open': 88.0, 'high': 88.2, 'low': 84.0, 'close': 84.5,
            'kc_upper': 107.0, 'kc_lower': 87.0, 'kc_middle': 95.5,
            'ma5': 85.5, 'ma15': 94.0, 'atr': 2.0, 'kc_trend': 'DOWN',
        }
    ]
    df = make_kline_df(rows)

    side, trigger = detect_raw_triggers(df, symbol=symbol)
    assert side == "SHORT"
    assert trigger == "TRIGGER_PENDING_RETEST_ENTRY"
    assert symbol not in _PENDING_WICK_REENTRY_STATE


def test_pending_retest_timeout_and_trend_invalidation():
    """超過 3 根 K 棒或趨勢反轉時，自動清除 pending 狀態。"""
    _PENDING_WICK_REENTRY_STATE.clear()
    symbol = "TEST_TIMEOUT"

    base_time = 1000000000
    _PENDING_WICK_REENTRY_STATE[symbol] = {
        'side': 'LONG',
        'bar_id': base_time,
        'extreme_price': 115.0,
    }

    # 4 根 K 棒之後（4 * 60000 = 240000）
    rows = [
        {'timestamp': base_time, 'open': 107.5, 'high': 115, 'low': 107, 'close': 110, 'ma5': 108.5, 'ma15': 105, 'kc_middle': 104},
        {'timestamp': base_time + 60000, 'open': 110, 'high': 111, 'low': 109, 'close': 110, 'ma5': 108.5, 'ma15': 105, 'kc_middle': 104},
        {'timestamp': base_time + 120000, 'open': 110, 'high': 111, 'low': 109, 'close': 110, 'ma5': 108.5, 'ma15': 105, 'kc_middle': 104},
        {
            'timestamp': base_time + 240000,
            'open': 110.0, 'high': 111.5, 'low': 109.5, 'close': 110.8,
            'kc_upper': 112.0, 'kc_lower': 92.0, 'kc_middle': 104.5,
            'ma5': 110.0, 'ma15': 105.5, 'atr': 2.0, 'kc_trend': 'UP',
        }
    ]
    df = make_kline_df(rows)

    side, trigger = detect_raw_triggers(df, symbol=symbol)
    # 超過 3 根，狀態被清除
    assert symbol not in _PENDING_WICK_REENTRY_STATE
    assert trigger != "TRIGGER_PENDING_RETEST_ENTRY"
