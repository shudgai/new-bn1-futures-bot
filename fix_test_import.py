import re

with open('tests/test_v9_core_logic.py', 'r') as f:
    content = f.read()

# Remove check_atr_step_trailing_stop from imports
content = re.sub(r',\s*check_atr_step_trailing_stop', '', content)
content = re.sub(r'check_atr_step_trailing_stop,\s*', '', content)
content = re.sub(r'from core\.services\.exits\.dual_track_exit_service import \(\n\s*check_atr_step_trailing_stop\n\)', '', content) # if it's the only one

# We also need to import DualTrackExitStrategy if not already imported
if 'DualTrackExitStrategy' not in content:
    content = content.replace('from core.services.strategies.unified_entry_strategy', 'from core.services.exits.dual_track_exit_service import DualTrackExitStrategy\nfrom core.services.strategies.unified_entry_strategy')

# Rewrite test_dynamic_atr_phase_jump
new_test = """
def test_dynamic_atr_phase_jump():
    strategy = DualTrackExitStrategy()
    position = {
        "side": "LONG",
        "entry_price": 100.0,
        "size": 1.0,
        "v10_phase_trailing": {}
    }
    # df needs at least 3 rows
    data = [_default_row(), _default_row(), _default_row({"atr": 1.0})]
    df = pd.DataFrame(data)

    reason = strategy.evaluate_exit(position, df, 101.6)
    assert reason is None
    assert position["v10_phase_trailing"]["last_locked_level"] == 1
    assert position["v10_phase_trailing"]["active_stop_price"] == 100.0 # entry_price

    reason2 = strategy.evaluate_exit(position, df, 103.1)
    assert reason2 is None
    assert position["v10_phase_trailing"]["last_locked_level"] == 2
    assert position["v10_phase_trailing"]["active_stop_price"] == 100.0 + 1.5 * 1.0 
"""

content = re.sub(r'def test_dynamic_atr_phase_jump\(\):.*?(?=def test_)', new_test, content, flags=re.DOTALL)

with open('tests/test_v9_core_logic.py', 'w') as f:
    f.write(content)

