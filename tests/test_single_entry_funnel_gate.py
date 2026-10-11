"""Unit test for Single Entry Funnel Gate: weak-body breakout rejection & authorize bypass detection."""
import pytest
import pandas as pd
from unittest.mock import MagicMock
from core.services.entry_contract import evaluate_entry_contract
from core.engine import TradingEngine


def create_sample_bars(body_ratio=0.3, body_atr=0.2):
    # Construct a 5-bar dataframe representing 1M candles
    atr = 1.0
    data = []
    base_price = 100.0
    for i in range(5):
        data.append({
            'timestamp': 1700000000000 + i * 60000,
            'open': base_price,
            'high': base_price + 1.0,
            'low': base_price - 1.0,
            'close': base_price,
            'atr': atr,
            'kc_middle': base_price,
            'kc_upper': base_price + 2.0,
            'kc_lower': base_price - 2.0,
            'ma5': base_price,
            'ma15': base_price,
            'is_closed': True,
        })
    # Last bar: forming or live candle that breaks lower rail (e.g. at 07:16)
    # But has weak body!
    # e.g., Open = 97.9, Close = 97.8 (body = 0.1, which is 0.1 ATR < 0.35 ATR)
    # High = 98.2, Low = 97.7 (range = 0.5, body_ratio = 0.1 / 0.5 = 20% < 50%)
    live_open = 97.9
    live_close = live_open - (atr * body_atr)
    # Adjust high and low to achieve requested body_ratio
    total_range = (abs(live_close - live_open)) / body_ratio
    live_high = live_open + total_range * 0.4
    live_low = live_high - total_range

    data.append({
        'timestamp': 1700000000000 + 5 * 60000,
        'open': live_open,
        'high': live_high,
        'low': live_low,
        'close': live_close,
        'atr': atr,
        'kc_middle': base_price,
        'kc_upper': base_price + 2.0,
        'kc_lower': 98.0, # kc_lower breached!
        'ma5': base_price - 0.5,
        'ma15': base_price,
        'is_closed': False,
    })
    df = pd.DataFrame(data)
    df.attrs['timeframe_ms'] = 60000
    return df, live_close


def test_weak_body_breakout_rejected():
    frame, quote = create_sample_bars(body_ratio=0.3, body_atr=0.2)
    diagnostics = {}
    decision = evaluate_entry_contract(frame, quote, symbol="LOBSTER/USDT", diagnostics=diagnostics)
    
    # Assert system returns None, strictly blocking the weak-body breakout
    assert decision is None
    assert diagnostics.get('reason') in (
        'BLOCKED_BY_WEAK_BODY_RATIO',
        'BLOCKED_BY_INSUFFICIENT_BODY_ATR',
        'BLOCKED_BY_INSUFFICIENT_BREAKOUT_CONFIRMATION'
    )


def test_unauthorized_entry_bypass_raises_exception():
    import asyncio
    engine = TradingEngine.__new__(TradingEngine)
    engine._channel_entry_locks = {}
    engine.account = MagicMock()
    
    # An entry signal lacking '_is_authorized': True must raise Exception
    unauthorized_signal = {
        'side': 'SHORT',
        'score': 100,
        'entry_mode': 'CHANNEL_SWING',
        'action': 'ENTER_MARKET',
        'signal_code': 'BEARISH_INSTANT_BREAKOUT',
        'candidate_bar_id': 1700000000000,
        # '_is_authorized' is omitted or False
    }
    
    with pytest.raises(Exception, match="CRITICAL: Bypass of authorize\\(\\) detected"):
        asyncio.run(engine._place_structured_entry("LOBSTER/USDT", unauthorized_signal, 97.8))
