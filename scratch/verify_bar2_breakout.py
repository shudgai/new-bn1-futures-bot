import sys, os, time, pandas as pd, requests
from datetime import datetime, timezone
sys.path.append(os.getcwd())
from core.strategy import SuperTrendKeltnerStrategy
from core.services.kc_pending_entry import evaluate_kc_pending_entry

symbol = "1000PEPEUSDT"
end_time = int(datetime(2026, 10, 2, 1, 55, tzinfo=timezone.utc).timestamp() * 1000)
url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1m&endTime={end_time}&limit=200"
r = requests.get(url)
klines = r.json()

df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
df['timestamp'] = pd.to_numeric(df['timestamp'])
df['open'] = pd.to_numeric(df['open'])
df['high'] = pd.to_numeric(df['high'])
df['low'] = pd.to_numeric(df['low'])
df['close'] = pd.to_numeric(df['close'])

strat = SuperTrendKeltnerStrategy()
df = strat.compute_indicators(df)

bar2_ts = 1790905920000.0  # 09:52
df_until_bar2 = df[df['timestamp'] <= bar2_ts].copy()
res2 = evaluate_kc_pending_entry(df_until_bar2, df_until_bar2['close'].iloc[-1], symbol='1000PEPE/USDT')
print("09:52 RES =", res2)

bar3_ts = 1790905980000.0  # 09:53
df_until_bar3 = df[df['timestamp'] <= bar3_ts].copy()
res3 = evaluate_kc_pending_entry(df_until_bar3, df_until_bar3['close'].iloc[-1], symbol='1000PEPE/USDT')
print("09:53 RES =", res3)
