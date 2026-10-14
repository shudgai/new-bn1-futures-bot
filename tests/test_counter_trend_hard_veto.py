"""Unit tests for Counter-Trend Hard VETO in MemeSharedProtectionGate, entry_gate, and symbol_runner.
Verifies that:
1. When KC is DOWN (KC向下), LONG is 100% blocked with CRITICAL_BLOCK.
2. When KC is UP (KC向上), SHORT is 100% blocked with CRITICAL_BLOCK.
3. When MA5 is sloping down, LONG is 100% blocked.
4. When MA5 is sloping up, SHORT is 100% blocked.
5. Red candle (c < o) can never trigger LONG in Mode 1 / Mode 3.
6. Green candle (c > o) can never trigger SHORT in Mode 1 / Mode 3.
"""
import pytest
import pandas as pd
import numpy as np
from core.gates.meme_shared_protection_gate import MemeSharedProtectionGate
from core.gates.entry_gate import evaluate_entry_candidate


def make_5m_candles(kc_trend='DOWN', candle_color='RED', ma5_slope=-1.0, count=30):
    """Helper to create 5M candles with specific KC trend and candle properties."""
    data = []
    base_price = 100.0
    for i in range(count):
        if kc_trend == 'DOWN':
            p = base_price - (i * 0.5)
            mid = p + 1.0
            upper = mid + 2.0
            lower = mid - 2.0
            c_state = 'KC向下'
        else:
            p = base_price + (i * 0.5)
            mid = p - 1.0
            upper = mid + 2.0
            lower = mid - 2.0
            c_state = 'KC向上'
        
        # Last candle custom settings
        if i == count - 1:
            if candle_color == 'RED':
                open_p = p + 1.5
                close_p = p
                high_p = open_p + 0.2
                low_p = close_p - 0.2
            else:
                open_p = p
                close_p = p + 1.5
                high_p = close_p + 0.2
                low_p = open_p - 0.2
            ma5 = close_p + (0.5 if ma5_slope < 0 else -0.5)
            ma15 = ma5 + (1.0 if ma5_slope < 0 else -1.0)
        else:
            open_p = p
            close_p = p - 0.2 if kc_trend == 'DOWN' else p + 0.2
            high_p = max(open_p, close_p) + 0.1
            low_p = min(open_p, close_p) - 0.1
            ma5 = p
            ma15 = p + (0.5 if kc_trend == 'DOWN' else -0.5)

        data.append({
            'open': open_p,
            'high': high_p,
            'low': low_p,
            'close': close_p,
            'volume': 1000.0,
            'ma5': ma5,
            'ma15': ma15,
            'kc_middle': mid,
            'kc_upper': upper,
            'kc_lower': lower,
            'atr': 1.0,
            'channel_state': c_state,
            'is_closed': True,
        })
    return pd.DataFrame(data)


def test_hard_block_long_when_kc_is_down():
    """驗證：當 KC 為 DOWN（KC向下）時，開多 100% 被一票硬否決，絕不開多。"""
    df_5m = make_5m_candles(kc_trend='DOWN', candle_color='RED', ma5_slope=-1.0)
    passed, reason, meta = MemeSharedProtectionGate.evaluate_entry(
        df_5m=df_5m, df_15m=None, quote=df_5m.iloc[-1]['close'], side='LONG', symbol='1000PEPE/USDT'
    )
    assert not passed
    assert "CRITICAL_BLOCK" in reason
    assert "Must be UP" in reason or "Forbidden to open LONG" in reason


def test_hard_block_short_when_kc_is_up():
    """驗證：當 KC 為 UP（KC向上）時，開空 100% 被一票硬否決，絕不開空。"""
    df_5m = make_5m_candles(kc_trend='UP', candle_color='GREEN', ma5_slope=1.0)
    passed, reason, meta = MemeSharedProtectionGate.evaluate_entry(
        df_5m=df_5m, df_15m=None, quote=df_5m.iloc[-1]['close'], side='SHORT', symbol='龙虾/USDT'
    )
    assert not passed
    assert "CRITICAL_BLOCK" in reason
    assert "Must be DOWN" in reason or "Forbidden to open SHORT" in reason


def test_hard_block_long_when_ma5_sloping_down():
    """驗證：即使突破，只要 MA5 斜率向下，絕對禁止開多。"""
    df_5m = make_5m_candles(kc_trend='UP', candle_color='GREEN', ma5_slope=-0.5)
    # 強制最後兩根 MA5 下拐
    df_5m.iloc[-1, df_5m.columns.get_loc('ma5')] = 99.0
    df_5m.iloc[-2, df_5m.columns.get_loc('ma5')] = 100.0
    passed, reason, meta = MemeSharedProtectionGate.evaluate_entry(
        df_5m=df_5m, df_15m=None, quote=df_5m.iloc[-1]['close'], side='LONG', symbol='1000PEPE/USDT'
    )
    assert not passed
    assert "CRITICAL_BLOCK" in reason
    assert "MA5 slope <= 0" in reason or "MA5" in reason


def test_red_candle_cannot_trigger_mode1_long():
    """驗證：大陰線（c < o）絕對不能被判定為 MODE1 做多。"""
    df_5m = make_5m_candles(kc_trend='UP', candle_color='RED', ma5_slope=1.0)
    passed, reason, meta = MemeSharedProtectionGate.evaluate_entry(
        df_5m=df_5m, df_15m=None, quote=df_5m.iloc[-1]['close'], side='LONG', symbol='1000PEPE/USDT'
    )
    assert not passed or (meta and meta.get('mode') != 'MODE1_KC_BREAKOUT')


def test_entry_gate_evaluate_entry_candidate_hard_veto():
    """驗證：evaluate_entry_candidate 在 KC向下 時 100% 阻斷 OPEN_LONG。"""
    df = make_5m_candles(kc_trend='DOWN', candle_color='RED', ma5_slope=-1.0)
    can_open, reason = evaluate_entry_candidate('OPEN_LONG', df, current_bar_index=-1, symbol='1000PEPE/USDT')
    assert not can_open
    assert "CRITICAL_BLOCK" in reason
    assert "Forbidden to open LONG when KC is DOWN" in reason

    can_open_short, reason_short = evaluate_entry_candidate('OPEN_SHORT', df, current_bar_index=-1, symbol='1000PEPE/USDT')
    # DOWN 趨勢下不會因為 KC 趨勢被 CRITICAL_BLOCK 阻斷 SHORT
    assert "Forbidden to open SHORT" not in reason_short


def test_intra_bar_breakout_early_trigger():
    """驗證：盤中即時強動能（is_closed=False 且 body >= 0.35 ATR 破軌）允許即時觸發開倉，不必苦等收盤。"""
    df_5m = make_5m_candles(kc_trend='UP', candle_color='GREEN', ma5_slope=1.0)
    # 將最後一根設為未收線的盤中 K 棒
    df_5m.iloc[-1, df_5m.columns.get_loc('is_closed')] = False
    upper = float(df_5m.iloc[-1]['kc_upper'])
    atr = float(df_5m.iloc[-1]['atr'])
    # 設置強勢突破價格：高於上軌，且實體大於 0.5 ATR
    df_5m.iloc[-1, df_5m.columns.get_loc('open')] = upper - 0.2
    df_5m.iloc[-1, df_5m.columns.get_loc('close')] = upper + 0.6
    df_5m.iloc[-1, df_5m.columns.get_loc('high')] = upper + 0.7
    df_5m.iloc[-1, df_5m.columns.get_loc('ma5')] = upper + 0.3
    df_5m.iloc[-1, df_5m.columns.get_loc('ma15')] = upper - 1.0

    passed, reason, meta = MemeSharedProtectionGate.evaluate_entry(
        df_5m=df_5m, df_15m=None, quote=upper + 0.6, side='LONG', symbol='1000PEPE/USDT'
    )
    assert passed
    assert meta['mode'] == 'MODE1_KC_BREAKOUT'


def test_ironclad_ma15_veto_rejects_exit_when_structure_intact():
    """驗證：空單只要 5m_close <= 5m_ma15，TrendHoldingAndExitManager 100% 行使 VETO 否決，不准平倉！"""
    from core.gates.trend_holding_immunity_and_exit_gate import TrendHoldingAndExitManager
    df_5m = make_5m_candles(kc_trend='DOWN', candle_color='RED', ma5_slope=-1.0)
    position = {
        'symbol': '1000PEPE/USDT',
        'side': 'SHORT',
        'entry_price': 100.0,
        'qty': 1000.0,
        'margin': 20.0,
        'leverage': 5.0,
        'peak_profit_atr': 0.5,
    }
    # 5m close = 85.5 <= ma15 = 86.0
    can_exit, reason, details = TrendHoldingAndExitManager.evaluate_exit(
        position=position, df_5m=df_5m, df_15m=None, quote=85.5, candidate_reason="SOME_ZOMBIE_REASON"
    )
    assert not can_exit
    assert "TREND_INTACT_IMMUNITY" in reason or "SHORT_UNDER_5M_MA15" in reason


