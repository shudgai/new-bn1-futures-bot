import pytest
import copy

content = """
# Append prints
def test_debug_hard_safety():
    from core.services.exits.peak_trailing_exit import evaluate_peak_trailing
    pos = {'id': 't1', 'symbol': 'BTCUSDT', 'side': 'LONG', 'entry_price': 100, 'qty': 1, 'entry_atr': 1, 'sl': 90, 'open_timestamp': 1}
    dec = evaluate_peak_trailing(pos, 80, {}, atr=1.0)
    print("dec =", dec)

"""
with open('tests/test_debug.py', 'w') as f:
    f.write(content)
