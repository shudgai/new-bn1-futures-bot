import sys, os
sys.path.append(os.getcwd())
from core.services.exits.peak_trailing_exit import evaluate_peak_trailing, position_identity
position = {'side': 'LONG', 'entry_price': 100, 'qty': 1, 'state': {'peak_price': 110, 'peak_net_pnl': 10.0, 'identity': ['LONG', 60.0, 100.0, 1.0]}, 'initial_sl': 90, 'open_timestamp': 60.0}
snapshot = {
    'quote_ms': 60000,
    'live_bar_ms': 60000,
    'closed_bar_ms': 0,
    'live_open': 105,
    'atr': 2.0,
    'ma5': 80,
    'ma15': 90,
    'kc_middle': 80,
    'last_open': 104,
    'last_high': 106,
    'last_low': 103,
    'last_close': 105,
    'last_ma5': 82,
    'last_ma15': 90,
    'last_kc_middle': 80
}
ident = position_identity(position)
print("ident:", ident)
stamp = float(snapshot.get('quote_ms', 0))
print("stamp:", stamp, "ident[1]*1000:", ident[1]*1000)
import math
print("condition 1:", not (math.isfinite(105.0) and 105.0 > 0))
fee=0.0005; slippage=0.0001
print("condition 2:", not all(math.isfinite(v) and 0 <= v < 1 for v in (fee, slippage)))

from core.services.exits.peak_trailing_exit import migrate_peak_state
state = migrate_peak_state(position)
print("migrated state peak_net_pnl:", state.get('peak_net_pnl'))
print("migrated state peak_price:", state.get('peak_price'))

from core.services.exits.peak_trailing_exit import estimated_net_pnl
net = estimated_net_pnl(100.0, 105.0, 1.0, 1, fee, slippage)
print("net:", net)
locked_net = math.floor((state.get('peak_net_pnl', 0) - 4.0) / 2.0) * 2.0 + 2.0
print("locked_net:", locked_net)
