import asyncio
import ccxt.async_support as ccxt
import pandas as pd
from core.services.strategies.three_patterns import compute_pattern_indicators

async def main():
    exchange = ccxt.binanceusdm()
    
    for symbol in ["1000PEPE/USDT", "1000LUNC/USDT"]:
        try:
            klines = await exchange.fetch_ohlcv(symbol, '1m', limit=200)
            df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df = compute_pattern_indicators(df)
            
            # 12:00 to 12:20 UTC is 04:00 to 04:20 UTC
            # timestamp is ms
            df_target = df[(df.timestamp >= 1790222400000) & (df.timestamp <= 1790223600000)]
            
            print(f"\n=== {symbol} ===")
            for i in range(len(df_target)):
                idx = df_target.index[i]
                row = df.iloc[idx]
                
                atr = row.atr
                body = row.close - row.open
                body_atr = abs(body) / atr if atr > 0 else 0
                
                baseline = df.iloc[idx-21:idx-1]['volume'].astype(float).mean() if idx >= 21 else 1
                vol_ratio = row.volume / baseline if baseline > 0 else 0
                
                kc_upper = row.kc_upper
                kc_lower = row.kc_lower
                
                if body > 0:
                    dist_out = row.close - kc_upper
                    dist_out_str = f"{dist_out:.6f} above Upper" if dist_out > 0 else f"{dist_out:.6f} below Upper"
                else:
                    dist_out = kc_lower - row.close
                    dist_out_str = f"{dist_out:.6f} below Lower" if dist_out > 0 else f"{dist_out:.6f} above Lower"
                    
                print(f"Bar {row.timestamp}: Close={row.close:.6f}, ATR={atr:.6f}, VolRatio={vol_ratio:.2f}, Body={body:.6f} ({body_atr:.2f} ATR), {dist_out_str}")
                
        except Exception as e:
            print(f"Error {symbol}: {e}")
            
    await exchange.close()

asyncio.run(main())
