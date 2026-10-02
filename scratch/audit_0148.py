import sys, os, time
from datetime import datetime, timezone
sys.path.append(os.getcwd())
from core.indicators import calculate_keltner_channels, calculate_ma, calculate_atr
import requests

symbol = "1000PEPEUSDT"
# We need data up to 01:54 UTC + historical data for indicators
# 100 bars before 01:54
end_time = int(datetime(2026, 10, 2, 1, 55, tzinfo=timezone.utc).timestamp() * 1000)
url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1m&endTime={end_time}&limit=200"
r = requests.get(url)
klines = r.json()

# Reconstruct pandas dataframe as system does
import pandas as pd
df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
df['timestamp'] = pd.to_numeric(df['timestamp'])
df['open'] = pd.to_numeric(df['open'])
df['high'] = pd.to_numeric(df['high'])
df['low'] = pd.to_numeric(df['low'])
df['close'] = pd.to_numeric(df['close'])

df['ma5'] = calculate_ma(df, 5)
df['ma15'] = calculate_ma(df, 15)
df['atr'] = calculate_atr(df, 14)
kc = calculate_keltner_channels(df, 20, 2.0)
df['kc_upper'] = kc['upper']
df['kc_mid'] = kc['mid']
df['kc_lower'] = kc['lower']
df['ma5_slope'] = df['ma5'].diff()

for idx, row in df.tail(10).iterrows():
    dt = datetime.fromtimestamp(row['timestamp']/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    dist = (row['close'] - row['kc_upper']) / row['atr'] if pd.notnull(row['atr']) and row['atr'] > 0 else 0
    print(f"Time: {dt} BarID: {row['timestamp']} Open: {row['open']:.8f} Close: {row['close']:.8f} KC_upper: {row['kc_upper']:.8f} MA5: {row['ma5']:.8f} MA15: {row['ma15']:.8f} MA5_slope: {row['ma5_slope']:.8f} ATR: {row['atr']:.8f} dist_atr: {dist:.4f}")
