import pandas as pd
import pytest
from core.services.entry_contract import evaluate_entry_contract

def test_weak_body_ratio_is_rejected():
    closed_bars = [{'timestamp': 1000 + i*60000, 'open': 10.0, 'high': 10.5, 'low': 9.5, 'close': 10.0, 'atr': 1.0, 'kc_upper': 11.0, 'kc_lower': 9.0, 'kc_middle': 10.0, 'ma3': 10.0, 'ma5': 10.0, 'ma15': 10.0, 'is_closed': True} for i in range(15)]
    frame = pd.DataFrame(closed_bars + [
        {'timestamp': 1000 + 15*60000, 'open': 9.0, 'high': 9.5, 'low': 8.0, 'close': 8.65, 'atr': 1.0, 'kc_upper': 11.0, 'kc_lower': 9.0, 'kc_middle': 9.5, 'ma3': 8.8, 'ma5': 9.0, 'ma15': 10.0, 'is_closed': False}
    ])
    # Body = 9.0 - 8.65 = 0.35 (passes 0.3 min). Range = 9.5 - 8.0 = 1.5. Ratio = 0.35/1.5 = 0.23 < 0.5.
    diagnostics = {}
    result = evaluate_entry_contract(frame, 8.65, 'TRIGGER_A_KC_BREAKOUT', diagnostics=diagnostics)
    assert result is None
    assert diagnostics.get('reason') == 'BLOCKED_BY_WEAK_BODY_RATIO'

def test_insufficient_body_atr_is_rejected():
    closed_bars = [{'timestamp': 1000 + i*60000, 'open': 10.0, 'high': 10.5, 'low': 9.5, 'close': 10.0, 'atr': 1.0, 'kc_upper': 11.0, 'kc_lower': 9.0, 'kc_middle': 10.0, 'ma3': 10.0, 'ma5': 10.0, 'ma15': 10.0, 'is_closed': True} for i in range(15)]
    frame = pd.DataFrame(closed_bars + [
        {'timestamp': 1000 + 15*60000, 'open': 9.0, 'high': 9.0, 'low': 8.7, 'close': 8.7, 'atr': 1.0, 'kc_upper': 11.0, 'kc_lower': 9.0, 'kc_middle': 9.5, 'ma3': 8.8, 'ma5': 9.0, 'ma15': 10.0, 'is_closed': False}
    ])
    # Perfect body ratio (no shadows), but body size is only 0.3. Needs to be >= 0.35 * 1.0
    diagnostics = {}
    result = evaluate_entry_contract(frame, 8.7, 'TRIGGER_A_KC_BREAKOUT', diagnostics=diagnostics)
    assert result is None
    assert diagnostics.get('reason') == 'BLOCKED_BY_INSUFFICIENT_BODY_ATR'
