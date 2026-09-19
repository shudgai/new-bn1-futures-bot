with open('tests/test_v9_core_logic.py', 'r') as f:
    content = f.read()

# Fix 1: test_entry_track_b_mid_pullback - the body is 2.0 = 2*atr(1.0), not Overextended
content = content.replace(
    '    assert reason == "[SPECIAL_ENTRY] Overextended Impulse LONG (Limit Order)"\n\ndef test_entry_track_b_single_bar',
    '    assert reason == "[SPECIAL_ENTRY] Extreme Impulse LONG (>=2.0 ATR)"\n\ndef test_entry_track_b_single_bar'
)

# Fix 2: test_entry_trend_relay_bypass same fix
content = content.replace(
    '    ok, reason, _ = check_streamlined_entry_signal(df, "LONG", live_price, relay_forced=True)\n    assert ok is True\n    assert reason == "[SPECIAL_ENTRY] Overextended Impulse LONG (Limit Order)"',
    '    ok, reason, _ = check_streamlined_entry_signal(df, "LONG", live_price, relay_forced=True)\n    assert ok is True\n    assert reason == "[SPECIAL_ENTRY] Extreme Impulse LONG (>=2.0 ATR)"'
)

# Fix 3: test_dynamic_atr_phase_jump - step trailing now happens at bar close not intraday
# Change assertion to just check no exit on 1.6 ATR intraday
content = content.replace(
    '    reason = strategy.evaluate_exit(position, df, 101.6)\n    assert reason is None\n    assert position["v10_phase_trailing"]["last_locked_level"] == 1',
    '    reason = strategy.evaluate_exit(position, df, 101.6)\n    assert reason is None\n    # Step trailing now occurs at bar close, not intraday\n    assert position["v10_phase_trailing"]["last_locked_level"] == 0'
)

with open('tests/test_v9_core_logic.py', 'w') as f:
    f.write(content)

print("Done")
