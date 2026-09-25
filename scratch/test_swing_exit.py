import pandas as pd
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy

strategy = DualTrackExitStrategy()

# Test LONG with fake breakdown (no confirmed peak)
df_fake_peak = pd.DataFrame([
    {'open': 100, 'high': 110, 'low': 95, 'close': 105}, # -3
    {'open': 105, 'high': 120, 'low': 100, 'close': 110}, # -2 (highest, but next bar won't break its low)
    {'open': 110, 'high': 115, 'low': 105, 'close': 108}, # -1 (didn't break min(open, close) which is 105)
])

# Add kc bands to simulate inside band
for i in range(len(df_fake_peak)):
    df_fake_peak.loc[i, 'kc_upper'] = 130
    df_fake_peak.loc[i, 'kc_lower'] = 80
    df_fake_peak.loc[i, 'atr'] = 5

position = {
    'side': 'LONG',
    'symbol': 'BTCUSDT',
    'best_price': 110,
    'current_tp_price': 102.5 # 110 - 1.5 * 5 = 102.5
}
indicators = {'atr': 5, 'kc_upper': 130, 'kc_lower': 80, 'kc_trend': 'DOWN'}

res = strategy.check_exit_and_manage_tp(position, df_fake_peak.iloc[-1], indicators, df_fake_peak)
assert res is None
assert not position.get('has_confirmed_swing', False)
print("✅ Test Passed: Fake peak correctly ignored, position held.")

# Test LONG with confirmed peak
df_true_peak = pd.DataFrame([
    {'open': 100, 'high': 110, 'low': 95, 'close': 105}, # -3
    {'open': 105, 'high': 120, 'low': 100, 'close': 110}, # -2 (highest, open=105, close=110)
    {'open': 110, 'high': 115, 'low': 100, 'close': 102}, # -1 (breaks 105!)
])

for i in range(len(df_true_peak)):
    df_true_peak.loc[i, 'kc_upper'] = 130
    df_true_peak.loc[i, 'kc_lower'] = 80
    df_true_peak.loc[i, 'atr'] = 5

# Set eval price to hit TP
current_kline = df_true_peak.iloc[-1].copy()
current_kline['close'] = 102
res2 = strategy.check_exit_and_manage_tp(position, current_kline, indicators, df_true_peak)
assert res2 == "EXIT_TRAILING_STOP" or res2 == "EXIT_KC_REVERSAL"
assert position.get('has_confirmed_swing', False)
print("✅ Test Passed: True peak confirmed, exit triggered.")

