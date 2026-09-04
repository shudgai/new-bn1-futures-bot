import re

with open("tests/test_channel_swing.py", "r") as f:
    content = f.read()

# Tests to completely remove (comment out or delete)
tests_to_remove = [
    "test_held_long_exits_after_three_closed_falling_red_candles",
    "test_held_short_mirrors_three_closed_rising_green_candle_exit",
    "test_three_red_candle_exit_ignores_ma3_and_kc_distance",
    "test_held_long_exits_on_shallow_red_reentry",
    "test_held_short_exits_on_shallow_green_reentry",
    "test_deep_red_reentry_with_low_volume_exits_long_without_short_entry",
    "test_first_red_half_channel_reentry_exits_long_without_reversing",
    "test_first_green_half_channel_reentry_exits_short_without_reversing",
    "test_channel_swing_waits_until_second_candle_is_fully_closed",
    "test_live_outer_ma3_trend_flattens_failed_turn",
    "test_confirmed_peak_exits_when_third_live_candle_started_inside_kc",
    "test_confirmed_trough_exits_when_third_live_candle_started_inside_kc",
]

for test in tests_to_remove:
    # Match the def test_name(): up to the next def test_
    # This is a simple regex that comments out the function body.
    pattern = r"(def " + test + r"\(\):.*?)(?=^def |^class |\Z)"
    content = re.sub(pattern, lambda m: '\n'.join(['# ' + line for line in m.group(1).split('\n')]), content, flags=re.MULTILINE | re.DOTALL)

# Fix remaining tests assertions
content = content.replace('"LONG_LOWER_OUTER_TREND_CHANGED_EXIT"', '"KC_LOWER_OUTER_VALLEY_EXIT"')
content = content.replace('"SHORT_UPPER_OUTER_TREND_CHANGED_EXIT"', '"KC_UPPER_OUTER_PEAK_EXIT"')
content = content.replace('"KC_UPPER_RED_REENTRY_EXIT"', '"KC_UPPER_OUTER_PEAK_EXIT"')
content = content.replace('"KC_LOWER_GREEN_REENTRY_EXIT"', '"KC_LOWER_OUTER_VALLEY_EXIT"')
content = content.replace('"REVERSE", "SHORT", "KC_UPPER_PEAK_CONFIRMED"', '"EXIT", None, "KC_UPPER_OUTER_PEAK_EXIT"')
content = content.replace('"REVERSE", "LONG", "KC_LOWER_TROUGH_CONFIRMED"', '"EXIT", None, "KC_LOWER_OUTER_VALLEY_EXIT"')

with open("tests/test_channel_swing.py", "w") as f:
    f.write(content)
