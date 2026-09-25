import pandas as pd
from core.services.strategies.unified_entry_strategy import check_two_stage_entry
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy

def test_entry_inside_rail():
    # Long setup: close inside upper band
    close = [100]; open_p = [98]; high = [101]; low = [97]
    ema20 = [90]; kc_upper = [105]; kc_lower = [85]
    ma3 = [100]; ma15 = [95]; atr = [5]
    
    ok, reason = check_two_stage_entry("LONG", close, open_p, high, low, ema20, kc_upper, kc_lower, ma3, ma15, atr)
    assert not ok and "未突破" in reason, f"Should reject inside rail LONG: {reason}"
    
    # Short setup: close inside lower band
    ok, reason = check_two_stage_entry("SHORT", [90], open_p, high, low, ema20, [105], [80], ma3, ma15, atr)
    assert not ok and "未跌破" in reason, f"Should reject inside rail SHORT: {reason}"
    print("✓ Entry Test: Inside rail 100% rejected")

def test_exit_outer_shield():
    # Long position
    exit_service = DualTrackExitStrategy()
    position = {"symbol": "TEST", "side": "LONG", "entry_price": 100, "highest_price": 110, "lowest_price": 100}
    # Latest close is 108, which is above kc_upper of 105
    frame = pd.DataFrame([
        {'atr': 2, 'kc_upper': 105, 'kc_lower': 95, 'high': 106, 'low': 100},
        {'atr': 2, 'kc_upper': 105, 'kc_lower': 95, 'high': 110, 'low': 105}, # Peak
        {'atr': 2, 'kc_upper': 105, 'kc_lower': 95, 'high': 109, 'low': 107}  # Fake dip
    ])
    result = exit_service.evaluate_exit(position, frame, current_price=108)
    assert result is None, f"Should shield fake peak outside rail, got {result}"
    print("✓ Exit Test: Outer shield protects fake peaks")

if __name__ == "__main__":
    test_entry_inside_rail()
    test_exit_outer_shield()
    print("All static tests passed!")
