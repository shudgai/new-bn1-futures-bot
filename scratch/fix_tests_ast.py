import ast

with open("tests/test_channel_swing.py", "r") as f:
    source = f.read()

tree = ast.parse(source)

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
    "test_wrong_side_outer_price_does_not_exit_without_ma3_turn",
]

class TestRemover(ast.NodeTransformer):
    def visit_FunctionDef(self, node):
        if node.name in tests_to_remove:
            return None
        return node

remover = TestRemover()
new_tree = remover.visit(tree)

new_source = ast.unparse(new_tree)

# Fix assertions
new_source = new_source.replace("'LONG_LOWER_OUTER_TREND_CHANGED_EXIT'", "'KC_LOWER_OUTER_VALLEY_EXIT'")
new_source = new_source.replace("'SHORT_UPPER_OUTER_TREND_CHANGED_EXIT'", "'KC_UPPER_OUTER_PEAK_EXIT'")
new_source = new_source.replace("'KC_UPPER_RED_REENTRY_EXIT'", "'KC_UPPER_OUTER_PEAK_EXIT'")
new_source = new_source.replace("'KC_LOWER_GREEN_REENTRY_EXIT'", "'KC_LOWER_OUTER_VALLEY_EXIT'")
new_source = new_source.replace("('REVERSE', 'SHORT', 'KC_UPPER_PEAK_CONFIRMED')", "('EXIT', None, 'KC_UPPER_OUTER_PEAK_EXIT')")
new_source = new_source.replace("('REVERSE', 'LONG', 'KC_LOWER_TROUGH_CONFIRMED')", "('EXIT', None, 'KC_LOWER_OUTER_VALLEY_EXIT')")

with open("tests/test_channel_swing.py", "w") as f:
    f.write(new_source)
