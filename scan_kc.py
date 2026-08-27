import pandas as pd
import ccxt
from core.strategy import SuperTrendKeltnerStrategy
import json

def main():
    exchange = ccxt.binance({'options': {'defaultType': 'future'}})
    
    with open('data/symbol_selection.json', 'r') as f:
        data = json.load(f)
    symbols = data.get('symbols', [])[:10]
    
    strategy = SuperTrendKeltnerStrategy()
    
    for symbol in symbols:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe='3m', limit=50)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df = strategy.compute_indicators(df)
            
            curr = df.iloc[-1]
            price = curr['close']
            kc_upper = curr['kc_upper']
            kc_lower = curr['kc_lower']
            
            dist_lower = (price - kc_lower) / kc_lower * 100
            dist_upper = (kc_upper - price) / kc_upper * 100
            
            print(f"{symbol:10} | Price: {price:<8.4f} | KC Lower: {kc_lower:<8.4f} ({dist_lower:>5.2f}%) | KC Upper: {kc_upper:<8.4f} ({dist_upper:>5.2f}%)")
        except Exception as e:
            print(f"{symbol} error: {e}")

main()
