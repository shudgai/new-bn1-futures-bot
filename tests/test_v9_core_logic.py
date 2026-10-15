import pytest
import pandas as pd
import math
from core.services.strategies.unified_entry_strategy import check_streamlined_entry_signal
from core.services.exits.dual_track_exit_service import (
    DualTrackExitStrategy
)

def _default_row(updates=None):
    row = {
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
        "volume": 100.0, "vol_ma_5": 50.0,
        "kc_upper": 102.0, "kc_middle": 100.0, "kc_lower": 98.0,
        "ma3": 100.0, "ma15": 100.0, "ma15_slope": 0.0, "atr": 1.0
    }
    if updates:
        row.update(updates)
    return row





def test_ratchet_lock_no_retreat():
    # 棘輪機制：止損不會往下退
    position = {
        "side": "LONG",
        "entry_price": 100.0,
        "size": 1.0,
        "v10_phase_trailing": {
            "last_locked_level": 1,
            "active_stop_price": 100.3,
            "defense_line": 98.5
        }
    }
    data = [_default_row(), _default_row({"atr": 1.0, "kc_upper": 105.0})]
    df = pd.DataFrame(data)
    
    strategy = DualTrackExitStrategy()
    strategy.evaluate_exit(position, df, 100.8)
    assert position["v10_phase_trailing"]["active_stop_price"] == 100.3






