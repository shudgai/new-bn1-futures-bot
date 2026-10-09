from core.services.exits.peak_trailing_exit import evaluate_peak_trailing

def create_pos():
    return {'id': 't1', 'symbol': 'BTCUSDT', 'side': 'LONG', 'entry_price': 100, 'qty': 1, 'entry_atr': 1, 'sl': 90, 'open_timestamp': 1}

pos = create_pos()
dec = evaluate_peak_trailing(pos, 80, {'quote_ms': 1000}, atr=1.0)
print("Return:", dec)
print("Position State:", pos.get('state'))
