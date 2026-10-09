import json
import os
from core.paper_account import PaperAccount
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing

def test_entry_atr_persistence():
    # Use the default paper account path for tests, but back it up first
    orig_file = "data/paper_account.json"
    backup = None
    if os.path.exists(orig_file):
        with open(orig_file, 'r') as f:
            backup = f.read()

    try:
        account = PaperAccount()
        account.positions = []
        X = 1.234
        
        position = {
            "symbol": "1000LUNC/USDT",
            "side": "LONG",
            "entry_price": 100.0,
            "qty": 100,
            "open_timestamp": 1600000000000,
            "entry_atr": X,
            "initial_sl": 90.0,
            "margin": 10.0,
            "leverage": 10
        }
        account.positions.append(position)
        account.save_state()

        account2 = PaperAccount()
        account2.load_state()
        restored_position = account2.positions[0]

        Y = 5.678
        snapshot = {
            "quote_ms": 1600000060000,
            "live_bar_ms": 1600000060000,
            "closed_bar_ms": 1600000000000,
            "live_open": 101.0,
            "atr": Y,
            "kc_middle": 99.0
        }
        
        evaluate_peak_trailing(restored_position, 102.0, snapshot, atr=Y)

        peak_state = restored_position.get('peak_trailing_state', {})

        print(f"restored position['entry_atr'] = {restored_position.get('entry_atr')}")
        print(f"peak state['atr'] = {peak_state.get('atr')}")
        print(f"LIVE_ATR passed (Y) = {Y}")

        assert restored_position.get('entry_atr') == X, "Restored entry_atr mismatch"
        assert peak_state.get('atr') == X, "Peak state atr did not freeze X"
        assert peak_state.get('atr') != Y, "Peak state atr was overwritten by Y"

        print("SUCCESS: FROZEN_ENTRY_ATR_RESTART_SAFE = YES")
    finally:
        if backup is not None:
            with open(orig_file, 'w') as f:
                f.write(backup)

if __name__ == "__main__":
    test_entry_atr_persistence()
