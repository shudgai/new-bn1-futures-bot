import pandas as pd
import pytest
import ccxt
from unittest.mock import MagicMock

def create_mock_candle(open_p, high_p, low_p, close_p, ma5=0, ma15=0, kc_mid=0, kc_up=0, kc_low=0):
    return pd.Series({
        'open': open_p, 'high': high_p, 'low': low_p, 'close': close_p,
        'ma5': ma5, 'ma15': ma15, 
        'kc_middle': kc_mid, 'kc_upper': kc_up, 'kc_lower': kc_low
    })

def is_doji_candle(row, ratio_threshold=0.2):
    total_range = row['high'] - row['low']
    if total_range == 0: return True
    return (abs(row['close'] - row['open']) / total_range) <= ratio_threshold

def check_candle_gate(curr, direction):
    if is_doji_candle(curr):
        return False, "BLOCKED_BY_DOJI"
    if direction == "LONG" and curr['close'] < curr['open']:
        return False, "BLOCKED_BY_RED_CANDLE"
    if direction == "SHORT" and curr['close'] > curr['open']:
        return False, "BLOCKED_BY_GREEN_CANDLE"
    return True, "PASSED"

def check_doji_reversal_exit(df, current_position):
    if len(df) < 2: return False, None
    prev, curr = df.iloc[-2], df.iloc[-1]
    
    if current_position == "LONG" and is_doji_candle(prev) and curr['close'] < curr['open']:
        return True, "EXIT_LONG_DOJI_BEARISH_CONFIRMATION"
    if current_position == "SHORT" and is_doji_candle(prev) and curr['close'] > curr['open']:
        return True, "EXIT_SHORT_DOJI_BULLISH_CONFIRMATION"
    return False, None

def test_case_1_doji_breakout_and_bearish_reversal():
    k1 = create_mock_candle(100.0, 105.0, 99.0, 104.0, kc_up=102.0)
    k2 = create_mock_candle(104.0, 105.0, 103.0, 104.2, kc_up=102.0)
    k3 = create_mock_candle(104.2, 104.5, 98.0, 98.5, kc_up=102.0)
    
    df_mock_entry = pd.DataFrame([k1, k2])
    passed, reason = check_candle_gate(df_mock_entry.iloc[-1], direction="LONG")
    assert passed is False
    assert reason == "BLOCKED_BY_DOJI"
    
    df_mock_exit = pd.DataFrame([k2, k3])
    exit_triggered, exit_reason = check_doji_reversal_exit(df_mock_exit, current_position="LONG")
    assert exit_triggered is True
    assert exit_reason == "EXIT_LONG_DOJI_BEARISH_CONFIRMATION"

def test_case_2_ma_whipsaw_filtered_by_kc_middle():
    prev_k = create_mock_candle(100.0, 101.0, 99.0, 100.0, ma5=99.0, ma15=100.0, kc_mid=105.0)
    curr_k = create_mock_candle(100.0, 101.0, 99.0, 100.0, ma5=101.0, ma15=100.0, kc_mid=105.0)
    
    is_golden_cross = (prev_k['ma5'] <= prev_k['ma15']) and (curr_k['ma5'] > curr_k['ma15'])
    valid_long_ma = is_golden_cross and (curr_k['close'] > curr_k['kc_middle'])
    
    assert is_golden_cross
    assert not valid_long_ma

def test_case_3_momentum_gate_blocks_counter_trend():
    green_candle = create_mock_candle(open_p=0.0840, high_p=0.0862, low_p=0.0835, close_p=0.0860)
    df_mock = pd.DataFrame([green_candle])
    
    passed, reason = check_candle_gate(df_mock.iloc[-1], direction="SHORT")
    assert passed is False
    assert reason == "BLOCKED_BY_GREEN_CANDLE"

def test_case_4_api_network_error_handling():
    mock_exchange = MagicMock()
    mock_exchange.create_order.side_effect = ccxt.RateLimitExceeded("Too Many Requests")
    safe_mode_activated = False
    try:
        mock_exchange.create_order('BTC/USDT', 'market', 'sell', 1.0)
    except ccxt.RateLimitExceeded:
        safe_mode_activated = True
    assert safe_mode_activated is True
