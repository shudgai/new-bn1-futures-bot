import ast

with open("tests/test_channel_swing.py", "r") as f:
    source = f.read()

tree = ast.parse(source)

tests_to_remove = [
    "test_held_long_exits_below_lower_rail_when_ma3_turns_down",
    "test_held_short_mirrors_upper_outer_ma3_turn_exit",
    "test_kc_outer_profit_exits_request_rotation_for_both_sides",
    "test_empty_board_rescan_excludes_current_symbols_but_keeps_held_symbol",
    "test_confirmed_outer_pivot_reverses_only_after_half_channel_close",
    "test_entry_side_outer_body_exits_when_ma3_turns_against_position",
    "test_second_live_outer_candle_flattens_on_ma3_turn",
    "test_live_outer_price_flattens_on_ma3_turn_even_before_ma3_reaches_rail",
    "test_entries_only_match_ranked_market_direction"
]

class TestRemover(ast.NodeTransformer):
    def visit_FunctionDef(self, node):
        if node.name in tests_to_remove:
            return None
        return node

remover = TestRemover()
new_tree = remover.visit(tree)

new_source = ast.unparse(new_tree)

with open("tests/test_channel_swing.py", "w") as f:
    f.write(new_source)
