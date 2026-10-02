import time
import pandas as pd
from unittest.mock import MagicMock, patch

# Import the strategy
from core.services.strategies.unified_entry_strategy import UnifiedEntryStrategy
import core.services.strategies.unified_entry_strategy as ues

class MockEngine:
    def __init__(self):
        self._market_crash_entry_cooldown_until = 0.0
        self.account = MagicMock()
        self.account.has_closed.return_value = False
        
    def _market_crash_entries_paused(self, now=None):
        return time.time() < self._market_crash_entry_cooldown_until

def build_test_frame(bar_timestamps):
    records = []
    for ts in bar_timestamps:
        records.append({
            'timestamp': ts,
            'open': 100.0,
            'close': 110.0,
            'high': 111.0,
            'low': 99.0,
            'kc_upper': 105.0,
            'kc_lower': 95.0,
            'ma3': 106.0,
            'ma15': 102.0,
            'atr': 2.0,
        })
    return pd.DataFrame(records)

@patch('core.services.strategies.unified_entry_strategy.evaluate_closed_entry', return_value=(True, "MOCK_SUCCESS", {}))
def run_tests(mock_eval):
    strategy = UnifiedEntryStrategy()
    engine = MockEngine()
    
    now_ms = int(time.time() * 1000)
    now_sec = now_ms / 1000.0
    
    # Case 1: no crash
    engine._market_crash_entry_cooldown_until = 0.0
    df_no_crash = build_test_frame([now_ms - 120000, now_ms - 60000, now_ms])
    allowed, code, dec = strategy.evaluate_entry(df_no_crash, 115.0, 'LONG', engine=engine)
    assert allowed, f"Case 1 failed: Expected True, got {code}"
    
    # Case 2: active crash, LONG blocked
    engine._market_crash_entry_cooldown_until = now_sec + 180
    allowed, code, dec = strategy.evaluate_entry(df_no_crash, 115.0, 'LONG', engine=engine)
    assert not allowed and code == "BTC_FLASH_CRASH_COOLDOWN", f"Case 2 failed: {code}"
    
    # Case 3: active crash, SHORT blocked
    df_short = build_test_frame([now_ms - 120000, now_ms - 60000, now_ms])
    df_short['open'] = 100.0; df_short['close'] = 90.0
    df_short['kc_lower'] = 95.0; df_short['ma3'] = 94.0; df_short['ma15'] = 98.0
    allowed, code, dec = strategy.evaluate_entry(df_short, 85.0, 'SHORT', engine=engine)
    assert not allowed and code == "BTC_FLASH_CRASH_COOLDOWN", f"Case 3 failed: {code}"
    
    # Case 4 & 5: Existing position check
    allowed, code, dec = strategy.evaluate_entry(df_no_crash, 115.0, 'LONG', engine=engine, existing_pos=True)
    assert not allowed and code == 'WAIT_EXISTING_POSITION', "Case 4 failed"
    
    # Case 8: cooldown expiry -> NEW signal can enter normally
    engine._market_crash_entry_cooldown_until = now_sec - 10
    df_new = build_test_frame([now_ms - 5000, now_ms, now_ms + 5000])
    allowed, code, dec = strategy.evaluate_entry(df_new, 115.0, 'LONG', engine=engine)
    assert allowed, f"Case 8 failed: {code}"
    
    # Case 9: signal generated DURING cooldown cannot be reused
    df_stale = build_test_frame([now_ms - 75000, now_ms - 15000, now_ms])
    allowed, code, dec = strategy.evaluate_entry(df_stale, 115.0, 'LONG', engine=engine)
    assert not allowed and code == "STALE_CRASH_SIGNAL_REJECTED", f"Case 9 failed: {code}"
    
    print("ALL TESTS PASSED")

if __name__ == "__main__":
    run_tests()
