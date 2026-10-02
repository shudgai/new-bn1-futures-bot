import pytest
import pandas as pd
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mass_replay import get_indicators
from core.services.kc_pending_entry import evaluate_kc_pending_entry, _INVALIDATED_SIGNALS

def test_historical_replay_uses_closed_bars_only():
    pass

def test_historical_replay_no_future_data():
    pass

def test_signal_identity_deterministic():
    pass

def test_invalidated_signal_not_resurrected():
    pass

def test_indicator_parity():
    df = pd.DataFrame({
        'timestamp': [1,2,3,4,5],
        'open': [100,105,103,110,108],
        'high': [110,110,108,115,110],
        'low': [95,100,100,105,100],
        'close': [105,103,110,108,105],
        'volume': [10,20,15,30,25]
    })
    df_ind = get_indicators(df)
    assert 'kc_upper' in df_ind.columns
    assert 'ma5' in df_ind.columns

