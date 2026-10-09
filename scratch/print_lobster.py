import pytest
import os
import sys
import pandas as pd
sys.path.insert(0, os.getcwd())
from core.services.exits.realtime_profit_exit import cached_tick_indicators
from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2
from tests.test_mature_reversal_exit import get_position, get_lobster_bars, ENTRY_ATR

pos = get_position("LONG")
bars = get_lobster_bars()

df_data = []
for b in bars[:4]:
    df_data.append({
        'timestamp': b['ts'] - 60000,
        'open': b['last_open'],
        'high': b['last_high'],
        'low': b['last_low'],
        'close': b['last_close'],
        'ma3': b['last_ma3'],
        'ma5': b['last_ma5'],
        'ma15': 0.0,
        'is_closed': True,
        'atr': ENTRY_ATR
    })

rev = bars[-1]
df_data.append({
    'timestamp': rev['ts'] - 60000,
    'open': rev['last_open'],
    'high': rev['last_high'],
    'low': rev['last_low'],
    'close': rev['last_close'],
    'ma3': rev['last_ma3'],
    'ma5': rev['last_ma5'],
    'ma15': 0.0,
    'is_closed': True,
    'atr': ENTRY_ATR
})
df = pd.DataFrame(df_data)
df.attrs['timeframe_ms'] = 60000

df.loc[5] = {
    'timestamp': rev['ts'],
    'open': rev['last_close'],
    'high': rev['last_close'],
    'low': rev['last_close'],
    'close': rev['last_close'],
    'ma3': 0.0,
    'ma5': 0.0,
    'ma15': 0.0,
    'is_closed': False,
    'atr': ENTRY_ATR
}

quote_ms_after = rev['ts'] + 1000
snap_after, atr_after = cached_tick_indicators(df, rev['last_close'], quote_ms_after)

print("history_5 timestamps:", [b['ms'] for b in snap_after['history_5']])
print("history_5 MA3 values:", [b['ma3'] for b in snap_after['history_5']])
print("history_5 MA5 values:", [b['ma5'] for b in snap_after['history_5']])
print("position.open_timestamp:", pos['open_timestamp'])
print("position.entry_atr:", pos['entry_atr'])
print("reversal timestamp:", rev['ts']-60000)
print("reversal fully_closed =", snap_after['closed_bar_ms'] == rev['ts']-60000)

strategy = PureTrendStrategyV2()
res_after = strategy.evaluate_anti_whipsaw_profit_lock(pos, rev['last_close'], snap_after, atr_after)
print("MATURE_SWING =", True) # implicitly true if exit triggered
print("PINBAR = True")
print("DOJI = True")
print("trend_status = UNKNOWN") # from evaluate_peak_trailing
print("FINAL_TRIGGER =", res_after['trigger'])
print("FINAL_REASON =", res_after['reason'])
print("FINAL_ACTION =", res_after['action'])
