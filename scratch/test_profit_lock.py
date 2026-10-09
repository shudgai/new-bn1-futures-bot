import json
import core.services.exits.peak_trailing_exit as peak_exit
import sys

def mock_position(side, price, entry_atr):
    return {
        'side': side,
        'open_timestamp': 900,
        'entry_price': price,
        'qty': 1.0,
        'entry_atr': entry_atr,
        'initial_sl': price - 2*entry_atr if side == 'LONG' else price + 2*entry_atr
    }

def mock_snapshot(price, o, h, l, c, atr, ms):
    return {
        'quote_ms': ms,
        'live_bar_ms': ms,
        'closed_bar_ms': ms - 60000,
        'live_open': o,
        'last_open': o,
        'last_high': h,
        'last_low': l,
        'last_close': c,
        'atr': atr,
    }

def run_test():
    failures = []
    
    # Enable Model T
    peak_exit.PROFIT_FLOOR_ENABLED = True
    peak_exit.LOCK_ARM_ATR = 0.5
    peak_exit.TRAILING_DISTANCE_ATR = 0.25
    
    print("--- LONG TESTS ---")
    pos = mock_position('LONG', 100.0, 1.0)
    
    # 1. before ARM -> existing pullback behavior
    snap = mock_snapshot(100.2, 100.0, 100.2, 100.0, 100.2, 1.0, 960000)
    res = peak_exit.evaluate_peak_trailing(pos, 100.2, snap, 1.0)
    if pos.get('peak_trailing_state', {}).get('profit_floor_armed'): failures.append("L1: Armed too early")
    
    # Pullback before arm (drawdown > 0.60 ATR threshold at peak_gain 0.49 -> Wait, threshold for 0.49 is not activated because net > 0 and peak_gain_atr >= 0.5 is required)
    snap = mock_snapshot(100.4, 100.0, 100.4, 100.0, 100.4, 1.0, 1060000)
    res = peak_exit.evaluate_peak_trailing(pos, 100.4, snap, 1.0)
    
    # 2. ARM threshold reached -> armed
    snap = mock_snapshot(100.5, 100.4, 100.5, 100.4, 100.5, 1.0, 1120000)
    res = peak_exit.evaluate_peak_trailing(pos, 100.5, snap, 1.0)
    if not pos.get('peak_trailing_state', {}).get('profit_floor_armed'): failures.append("L2: Failed to arm")
    expected_floor = 100.5 - 0.25 * 1.0
    if pos.get('peak_trailing_state', {}).get('profit_floor_price') != expected_floor: failures.append("L2: Floor incorrect")
    
    # 3. new favorable high -> floor rises
    snap = mock_snapshot(101.0, 100.5, 101.0, 100.5, 101.0, 1.0, 1180000)
    res = peak_exit.evaluate_peak_trailing(pos, 101.0, snap, 1.0)
    expected_floor = 101.0 - 0.25 * 1.0
    if pos.get('peak_trailing_state', {}).get('profit_floor_price') != expected_floor: failures.append("L3: Floor failed to rise")
    
    # 4. Pullback above floor -> HOLD
    snap = mock_snapshot(100.9, 101.0, 101.0, 100.9, 100.9, 1.0, 1240000)
    res = peak_exit.evaluate_peak_trailing(pos, 100.9, snap, 1.0)
    if res is not None: failures.append("L4: Exited prematurely on pullback")
    
    # 5. new favorable high -> floor rises again
    snap = mock_snapshot(101.5, 100.9, 101.5, 100.9, 101.5, 1.0, 1300000)
    res = peak_exit.evaluate_peak_trailing(pos, 101.5, snap, 1.0)
    expected_floor = 101.5 - 0.25 * 1.0
    if pos.get('peak_trailing_state', {}).get('profit_floor_price') != expected_floor: failures.append("L5: Floor failed to rise again")
    
    # 6. Floor hit -> CLOSE
    snap = mock_snapshot(101.2, 101.5, 101.5, 101.2, 101.2, 1.0, 1360000) # 101.25 is floor
    res = peak_exit.evaluate_peak_trailing(pos, 101.2, snap, 1.0)
    if not res or res['trigger'] != 'EXIT_PROFIT_LOCK_FLOOR': failures.append(f"L6: Failed to close on floor hit, got {res}")
    
    
    print("--- SHORT TESTS ---")
    pos = mock_position('SHORT', 100.0, 1.0)
    
    # 1. before ARM
    snap = mock_snapshot(99.8, 100.0, 100.0, 99.8, 99.8, 1.0, 960000)
    res = peak_exit.evaluate_peak_trailing(pos, 99.8, snap, 1.0)
    if pos.get('peak_trailing_state', {}).get('profit_floor_armed'): failures.append("S1: Armed too early")
    
    # 2. ARM threshold reached
    snap = mock_snapshot(99.5, 99.8, 99.8, 99.5, 99.5, 1.0, 1060000)
    res = peak_exit.evaluate_peak_trailing(pos, 99.5, snap, 1.0)
    if not pos.get('peak_trailing_state', {}).get('profit_floor_armed'): failures.append("S2: Failed to arm")
    expected_floor = 99.5 + 0.25 * 1.0
    if pos.get('peak_trailing_state', {}).get('profit_floor_price') != expected_floor: failures.append("S2: Floor incorrect")
    
    # 3. new favorable low -> floor decreases
    snap = mock_snapshot(99.0, 99.5, 99.5, 99.0, 99.0, 1.0, 1120000)
    res = peak_exit.evaluate_peak_trailing(pos, 99.0, snap, 1.0)
    expected_floor = 99.0 + 0.25 * 1.0
    if pos.get('peak_trailing_state', {}).get('profit_floor_price') != expected_floor: failures.append("S3: Floor failed to decrease")
    
    # 4. Pullback before floor hit -> HOLD
    snap = mock_snapshot(99.1, 99.0, 99.1, 99.0, 99.1, 1.0, 1180000)
    res = peak_exit.evaluate_peak_trailing(pos, 99.1, snap, 1.0)
    if res is not None: failures.append("S4: Exited prematurely on pullback")
    
    # 5. floor hit -> CLOSE
    snap = mock_snapshot(99.3, 99.1, 99.3, 99.1, 99.3, 1.0, 1240000) # Floor is 99.25
    res = peak_exit.evaluate_peak_trailing(pos, 99.3, snap, 1.0)
    if not res or res['trigger'] != 'EXIT_PROFIT_LOCK_FLOOR': failures.append(f"S5: Failed to close on floor hit, got {res}")
    
    print("--- HARD STOP REGRESSION ---")
    pos = mock_position('LONG', 100.0, 1.0)
    snap = mock_snapshot(97.0, 100.0, 100.0, 97.0, 97.0, 1.0, 960000)
    res = peak_exit.evaluate_peak_trailing(pos, 97.0, snap, 1.0)
    if not res or res['reason'] != 'EXIT_INITIAL_ATR_HARD_STOP': failures.append(f"HR1: Failed hard stop, got {res}")
    
    print("--- WATERFALL REGRESSION ---")
    pos = mock_position('LONG', 100.0, 1.0)
    # Huge single candle drop
    snap = mock_snapshot(99.0, 100.0, 100.0, 99.0, 99.0, 0.5, 960000)
    # waterfall trigger requires positive(prior_atr) from 'atr' property and body >= threshold
    res = peak_exit.evaluate_peak_trailing(pos, 99.0, snap, 1.0)
    if not res or res['trigger'] != 'WATERFALL_DROP': failures.append(f"WR1: Failed waterfall, got {res}")
    
    if failures:
        print("FAILURES:")
        for f in failures: print(f)
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")

if __name__ == '__main__':
    run_test()
