import pytest
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing

def test_debug():
    pos = {'id': 't1', 'symbol': 'BTCUSDT', 'side': 'LONG', 'entry_price': 100, 'qty': 1, 'entry_atr': 1, 'sl': 90, 'open_timestamp': 1}
    try:
        dec = evaluate_peak_trailing(pos, 80, {}, atr=1.0)
        print("DEC=", dec)
    except Exception as e:
        import traceback
        traceback.print_exc()

