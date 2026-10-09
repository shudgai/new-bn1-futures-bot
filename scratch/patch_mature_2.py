import re

with open('tests/test_profit_exit_telemetry.py', 'r') as f:
    content = f.read()

# Replace test_mature_equivalence
mature_test = """def test_mature_equivalence():
    def setup():
        pos = create_pos()
        snap = {'quote_ms': 1005, 'closed_bar_ms': 1005, 'history_5': [
            {'ms': 1001, 'o': 100, 'h': 105, 'l': 95, 'c': 101, 'ma3': 100, 'ma5': 99},
            {'ms': 1002, 'o': 100, 'h': 105, 'l': 95, 'c': 101, 'ma3': 100, 'ma5': 99},
            {'ms': 1003, 'o': 100, 'h': 105, 'l': 95, 'c': 101, 'ma3': 100, 'ma5': 99},
            {'ms': 1004, 'o': 100, 'h': 105, 'l': 95, 'c': 101, 'ma3': 100, 'ma5': 99},
            {'ms': 1005, 'o': 100, 'h': 105, 'l': 90, 'c': 99, 'ma3': 100, 'ma5': 100} # pinbar doji reversal
        ]}
        return pos, snap
    def eval(pos, snap):
        return evaluate_peak_trailing(pos, 102, snap, atr=1.0)
    d, e = evaluate_both(setup, eval)
    assert d == e
    assert d is not None
    assert d['action'] == 'FULL_CLOSE'
    assert d['trigger'] in ('MATURE_REVERSAL_PINBAR', 'MATURE_REVERSAL_DOJI', 'MATURE_REVERSAL_PINBAR_DOJI')"""

content = re.sub(r'def test_mature_equivalence\(\).*?assert d\[\'trigger\'\] in \(\'MATURE_REVERSAL_PINBAR\', \'MATURE_REVERSAL_DOJI\', \'MATURE_REVERSAL_PINBAR_DOJI\'\)', mature_test, content, flags=re.DOTALL)

with open('tests/test_profit_exit_telemetry.py', 'w') as f:
    f.write(content)
