import asyncio
from core.engine import TradingEngine
from core.services.strategies.three_patterns import compute_pattern_indicators
import pandas as pd

async def main():
    engine = TradingEngine(paper_trading=True)
    await engine.exchange.load_markets()
    
    for symbol in ["1000PEPE/USDT", "龙虾/USDT"]:
        klines = await engine.exchange.watch_ohlcv(symbol, '1m')
        df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # filter between 12:00 and 12:20 (04:00 to 04:20 UTC)
        # 04:00 UTC = 1790222400000 approx. wait, let's just do the last 100 bars.
        # current time is 04:26 UTC -> 1790223960000
        # 04:00 UTC -> 1790222400000
        # 04:20 UTC -> 1790223600000
        df = compute_pattern_indicators(df)
        df_target = df[(df.timestamp >= 1790222400000) & (df.timestamp <= 1790223600000)]
        
        print(f"=== {symbol} ===")
        for i in range(len(df_target)):
            idx = df_target.index[i]
            row = df.iloc[idx]
            
            # calculate metrics
            atr = row.atr
            body = row.close - row.open
            body_atr = abs(body) / atr if atr > 0 else 0
            
            baseline = df.iloc[idx-21:idx-1]['volume'].mean() if idx >= 21 else 1
            vol_ratio = row.volume / baseline if baseline > 0 else 0
            
            kc_upper = row.kc_upper
            kc_lower = row.kc_lower
            
            # find closest to breakout
            # Breakout requires: body_atr >= 0.8, vol_ratio >= 1.5, close > kc_upper or close < kc_lower
            if body > 0:
                dist_out = row.close - kc_upper
            else:
                dist_out = kc_lower - row.close
                
            print(f"Bar {row.timestamp}: VolRatio={vol_ratio:.2f}, BodyATR={body_atr:.2f}, DistOut={dist_out:.6f}, Close={row.close}, Upper={kc_upper}, Lower={kc_lower}")
            
    await engine.exchange.close()

if __name__ == "__main__":
    asyncio.run(main())
