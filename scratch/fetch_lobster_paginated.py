import ccxt
import pandas as pd
import time
import sys
from datetime import datetime

ex = ccxt.binanceusdm()
markets = ex.load_markets()
sym = '龙虾/USDT:USDT'

if sym not in markets:
    print(f"{sym} not found in markets")
    sys.exit(1)

market_info = markets[sym]
print("=== MARKET IDENTITY ===")
print("CCXT_SYMBOL =", market_info.get('symbol'))
print("EXCHANGE_MARKET_ID =", market_info.get('id'))
print("BASE =", market_info.get('base'))
print("QUOTE =", market_info.get('quote'))
print("SETTLE =", market_info.get('settle'))
print("MARKET_TYPE =", market_info.get('type'))
print("TESTNET_OR_MOCK = Expected Mock (has Chinese characters)")

print("\n=== STARTING PAGINATION ===")
all_klines = []
limit = 1500

# Fetch the most recent to get the latest timestamp
latest_data = ex.fetch_ohlcv(sym, '1m', limit=limit)
if not latest_data:
    print("No data available at all.")
    sys.exit(1)

all_klines.extend(latest_data)
earliest_ts = latest_data[0][0]
print(f"Initial batch fetched {len(latest_data)} bars. Earliest ts: {earliest_ts}")

batches = 1
# Paginate backwards
while True:
    # Attempt to fetch data ending before earliest_ts.
    # In binance futures, we can use `endTime` via params or `since` (but we want backwards from an end time)
    # The ccxt binance implementation allows `params={'endTime': earliest_ts - 1}`
    try:
        batch = ex.fetch_ohlcv(sym, '1m', limit=limit, params={'endTime': earliest_ts - 1})
    except Exception as e:
        print(f"Pagination error: {e}")
        break

    if not batch:
        print("No earlier data returned by exchange.")
        break
        
    all_klines.extend(batch)
    batches += 1
    new_earliest_ts = batch[0][0]
    
    print(f"Batch {batches} fetched {len(batch)} bars. Earliest ts: {new_earliest_ts}")
    
    if new_earliest_ts >= earliest_ts:
        print("Pagination stalled (returned timestamps not earlier).")
        break
        
    earliest_ts = new_earliest_ts
    
    # We don't want to paginate forever in this test environment.
    # We will let it fetch up to 40,000 bars if it has them.
    if len(all_klines) > 100000:
        print("Reached max script collection limit (100k).")
        break
        
print("\n=== SAVING AND DEDUPLICATING ===")
df = pd.DataFrame(all_klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
initial_count = len(df)
df = df.drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)
final_count = len(df)

df.to_csv('/home/shudgai999/project/new bn/scratch/lobster_real_1m_history.csv', index=False)

print("\n=== DATA INTEGRITY ===")
print("TOTAL_BATCHES =", batches)
print("TOTAL_RAW_BARS =", initial_count)
print("TOTAL_UNIQUE_BARS =", final_count)
print("EARLIEST_TIMESTAMP =", df['timestamp'].iloc[0])
print("LATEST_TIMESTAMP =", df['timestamp'].iloc[-1])
print("TOTAL_DAYS =", (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]) / (1000 * 60 * 60 * 24))

# Check gaps
df['diff'] = df['timestamp'].diff()
gaps = df[df['diff'] > 60000]
print("MISSING_1M_INTERVALS =", sum(df['diff'] // 60000 - 1) if not gaps.empty else 0)
print("DUPLICATE_TIMESTAMPS =", initial_count - final_count)

print("\nFIRST_10_BARS:")
print(df.head(10).to_string())

print("\nLAST_10_BARS:")
print(df.tail(10).to_string())
