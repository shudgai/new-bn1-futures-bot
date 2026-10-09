import re

with open('tests/test_profit_exit_telemetry.py', 'r') as f:
    content = f.read()

# Replace test_mature_equivalence
mature_test = """def test_mature_equivalence():
    def setup():
        pos = create_pos()
        snap = {'closed_bar_ms': 5, 'history_5': [
            {'ms': 1, 'o': 100, 'h': 105, 'l': 95, 'c': 101, 'ma3': 100, 'ma5': 99},
            {'ms': 2, 'o': 100, 'h': 105, 'l': 95, 'c': 101, 'ma3': 100, 'ma5': 99},
            {'ms': 3, 'o': 100, 'h': 105, 'l': 95, 'c': 101, 'ma3': 100, 'ma5': 99},
            {'ms': 4, 'o': 100, 'h': 105, 'l': 95, 'c': 101, 'ma3': 100, 'ma5': 99},
            {'ms': 5, 'o': 100, 'h': 105, 'l': 90, 'c': 99, 'ma3': 100, 'ma5': 100} # pinbar doji reversal
        ]}
        return pos, snap
    def eval(pos, snap):
        return evaluate_peak_trailing(pos, 102, snap, atr=1.0)
    d, e = evaluate_both(setup, eval)
    assert d == e
    assert d is not None
    assert d['action'] == 'FULL_CLOSE'
    assert d['trigger'] in ('MATURE_REVERSAL_PINBAR', 'MATURE_REVERSAL_DOJI', 'MATURE_REVERSAL_PINBAR_DOJI')"""

# Replace test_waterfall_equivalence
waterfall_test = """def test_waterfall_equivalence():
    def setup():
        return create_pos(), {
            'closed_bar_ms': 60000, 'live_bar_ms': 120000,
            'live_open': 100.0, 'atr': 1.0, 
            'last_open': 100, 'last_close': 50, 'quote_ms': 120000
        }
    def eval(pos, snap):
        # Massive drop (waterfall condition requires snapshot dict correctly mocked)
        return evaluate_peak_trailing(pos, 50, snap, atr=1.0)
    d, e = evaluate_both(setup, eval)
    assert d == e
    assert d is not None
    assert d['action'] == 'FULL_CLOSE'
    assert d['trigger'] == 'WATERFALL_DROP'"""

content = re.sub(r'def test_mature_equivalence\(\).*?assert d == e', mature_test, content, flags=re.DOTALL)
content = re.sub(r'def test_waterfall_equivalence\(\).*?assert d and d\[\'trigger\'\] == \'WATERFALL_DROP\'\s*', waterfall_test + "\n", content, flags=re.DOTALL)

with open('tests/test_profit_exit_telemetry.py', 'w') as f:
    f.write(content)
