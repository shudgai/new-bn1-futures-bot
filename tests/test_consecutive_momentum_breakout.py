import math
import pytest
import pandas as pd

from core.gates.pipeline import (
    EntryGatePipeline,
    has_consecutive_momentum_breakout,
)
from core.services.entry_contract import evaluate_entry_contract


def make_consecutive_breakout_frame(side='LONG', *, ma_tangled=True, weak_body=False):
    """Construct candles mimicking CAP 11:06 breakout + 11:07 follow-through."""
    rows = []
    # 20 bars of base
    for i in range(20):
        mid = 99.9 if side == 'LONG' else 100.1
        rows.append({
            'timestamp': (i + 1) * 60_000,
            'open': mid,
            'high': mid + 0.2,
            'low': mid - 0.2,
            'close': mid,
            'kc_upper': mid + 1.0,
            'kc_middle': mid,
            'kc_lower': mid - 1.0,
            'atr': 1.0,
            'ma3': mid,
            'ma5': mid,
            'ma15': mid,
            'volume': 100.0,
            'is_closed': True,
        })

    # Bar 21 (e.g. 11:06): First breakout bar breaching KC
    if side == 'LONG':
        body1 = 0.20 if weak_body else 0.80
        rows.append({
            'timestamp': 21 * 60_000,
            'open': 100.5,
            'high': 100.5 + body1 + 0.1,
            'low': 100.4,
            'close': 100.5 + body1,  # 101.3 > kc_upper 101.0
            'kc_upper': 101.0,
            'kc_middle': 100.0,
            'kc_lower': 99.0,
            'atr': 1.0,
            'ma3': 100.5,
            'ma5': 100.2,
            'ma15': 100.05,
            'volume': 150.0,
            'is_closed': True,
        })
    else:
        body1 = 0.20 if weak_body else 0.80
        rows.append({
            'timestamp': 21 * 60_000,
            'open': 99.5,
            'high': 99.6,
            'low': 99.5 - body1 - 0.1,
            'close': 99.5 - body1,  # 98.7 < kc_lower 99.0
            'kc_upper': 101.0,
            'kc_middle': 100.0,
            'kc_lower': 99.0,
            'atr': 1.0,
            'ma3': 99.5,
            'ma5': 99.8,
            'ma15': 99.95,
            'volume': 150.0,
            'is_closed': True,
        })

    # Bar 22 (e.g. 11:07): Second breakout bar continuing same color
    if side == 'LONG':
        body2 = 0.20 if weak_body else 0.90
        open2 = 101.3
        close2 = open2 + body2
        # If ma_tangled=True, ma5 and ma15 are deliberately very close (lagging), spacing < 25% width
        ma5_val = 100.5 if ma_tangled else 101.5
        ma15_val = 100.4 if ma_tangled else 100.1
        rows.append({
            'timestamp': 22 * 60_000,
            'open': open2,
            'high': close2 + 0.1,
            'low': open2 - 0.05,
            'close': close2,
            'kc_upper': 101.2,
            'kc_middle': 100.1,
            'kc_lower': 99.0,
            'atr': 1.0,
            'ma3': 101.2,
            'ma5': ma5_val,
            'ma15': ma15_val,
            'volume': 200.0,
            'is_closed': True,
        })
        quote = close2 + 0.1
    else:
        body2 = 0.20 if weak_body else 0.90
        open2 = 98.7
        close2 = open2 - body2
        ma5_val = 99.5 if ma_tangled else 98.5
        ma15_val = 99.6 if ma_tangled else 99.9
        rows.append({
            'timestamp': 22 * 60_000,
            'open': open2,
            'high': open2 + 0.05,
            'low': close2 - 0.1,
            'close': close2,
            'kc_upper': 101.0,
            'kc_middle': 99.9,
            'kc_lower': 98.8,
            'atr': 1.0,
            'ma3': 98.8,
            'ma5': ma5_val,
            'ma15': ma15_val,
            'volume': 200.0,
            'is_closed': True,
        })
        quote = close2 - 0.1

    frame = pd.DataFrame(rows)
    frame.attrs['timeframe_ms'] = 60_000
    return frame, quote


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_consecutive_momentum_breakout_bypasses_ma_spacing_and_ai_chop(side):
    frame, quote = make_consecutive_breakout_frame(side, ma_tangled=True, weak_body=False)
    
    # 1. Detection helper detects consecutive momentum breakout
    assert has_consecutive_momentum_breakout(frame, quote, side=side) is True

    # 2. Pipeline market regime problem is bypassed (None instead of BLOCKED_BY_AI_CHOP_REGIME)
    pipeline = EntryGatePipeline()
    pipeline.market_regime_provider = lambda s: 'CHOPPY'
    assert pipeline.market_regime_problem('CAP/USDT', frame, quote) is None

    # 3. Entry contract does NOT block with BLOCKED_STRICT_MA_SPACING
    diagnostics = {}
    decision = evaluate_entry_contract(
        frame, quote, symbol='CAP/USDT', diagnostics=diagnostics,
    )
    assert diagnostics.get('reason') != 'BLOCKED_STRICT_MA_SPACING'
    assert diagnostics.get('reason') != 'BLOCKED_KC_MA5_MA15_TREND_MISMATCH'
    assert diagnostics.get('reason') != 'BLOCKED_BY_AI_CHOP_REGIME'
    assert decision is not None
    assert decision['side'] == side
    assert decision['action'] == 'ENTER'


def test_weak_candle_retains_strict_spacing_protection():
    # If bodies are too weak (< 0.35 ATR), consecutive momentum does not apply
    frame, quote = make_consecutive_breakout_frame('LONG', ma_tangled=True, weak_body=True)
    assert has_consecutive_momentum_breakout(frame, quote, side='LONG') is False
