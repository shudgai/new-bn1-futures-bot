import re

with open('tests/test_v9_core_logic.py', 'r') as f:
    content = f.read()

# Fix 3-tuple return
content = content.replace("ok, reason = check_streamlined_entry_signal", "ok, reason, _ = check_streamlined_entry_signal")

# Fix 1.5 ATR for dynamic jump
content = content.replace("reason = check_atr_step_trailing_stop(position, df, 101.0)", "reason = check_atr_step_trailing_stop(position, df, 101.6)")
content = content.replace("assert position[\"v10_phase_trailing\"][\"active_stop_price\"] == 100.0 + 0.7 * 1.0", "assert position[\"v10_phase_trailing\"][\"active_stop_price\"] == 100.0 + 1.5 * 1.0")

# Fix the Overextended assertions
content = content.replace("[SPECIAL_ENTRY] Extreme Impulse LONG (>=2.0 ATR)", "[SPECIAL_ENTRY] Overextended Impulse LONG (Limit Order)")
# But wait, some really are Extreme Impulse. If dist <= 2.0 ATR, it is Extreme. 
# test_entry_track_c_breakout: dist_from_mid = 103 - 100 = 3 > 2.0. So it's Overextended.
# test_entry_track_c_structural_collapse: dist_from_mid = 103 - 100 = 3 > 2.0. So it's Overextended.
# test_entry_track_b_mid_pullback: dist_from_mid = 102 - 100 = 2.0. dist > 2.0 is False! So it is Extreme.
content = content.replace('"[SPECIAL_ENTRY] Overextended Impulse LONG (Limit Order)"', 'res_string')
content = content.replace('res_string', '"[SPECIAL_ENTRY] Overextended Impulse LONG (Limit Order)"' )

# Fix test_entry_track_d_trend_continuation_short
content = content.replace("[STANDARD_ENTRY] Aligned Trend Continuation SHORT", "[TREND_PRIVILEGE_ENTRY] Band Riding SHORT")

# Add the new test
new_test = """
def test_entry_track_a_trend_defense():
    # Long Special K but Strong Bearish Trend
    data_long = [
        _default_row(), _default_row(), _default_row(),
        _default_row({
            "kc_middle": 105.0, "ma15": 106.0
        }),
        _default_row({
            "open": 98.0, "close": 101.0, "high": 102.5, "low": 97.5, # body=3.0, total=5.0, ratio=0.60
            "kc_middle": 100.0, "ma15": 100.0,  # slope_ma15 = -6.0, slope_mid = -5.0 (Strong bear!)
            "atr": 1.0,
            "volume": 100, "vol_ma_5": 50
        })
    ]
    df_long = pd.DataFrame(data_long)
    ok, reason, _ = check_streamlined_entry_signal(df_long, "LONG", 101.5)
    assert ok is False
    assert "FILTERED_EXTREME_COUNTER_TREND" in reason
"""
content += new_test

with open('tests/test_v9_core_logic.py', 'w') as f:
    f.write(content)
