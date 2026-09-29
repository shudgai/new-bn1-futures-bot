import asyncio
import pandas as pd
from core.engine import TradingEngine

async def main():
    try:
        engine = TradingEngine()
        
        symbols = ["1000PEPE/USDT", "龙虾/USDT"]
        
        for sym in symbols:
            print(f"\n[{sym}]")
            df = await engine.fetch_klines(sym, timeframe='1m', limit=200, keep_live=True)
            if df is None or df.empty:
                print("Failed to fetch klines")
                continue
                
            df = engine.strategy.compute_indicators(df)
            last = df.iloc[-1]
            
            close = float(last['close'])
            kc_upper = float(last['kc_upper'])
            kc_lower = float(last['kc_lower'])
            atr = float(last['atr'])
            
            diff_long = kc_upper - close
            pct_long = (diff_long / close) * 100
            
            diff_short = close - kc_lower
            pct_short = (diff_short / close) * 100
            
            print(f"Close: {close:.6f}")
            print(f"KC_Upper: {kc_upper:.6f}")
            print(f"KC_Lower: {kc_lower:.6f}")
            print(f"ATR: {atr:.6f} (起爆門檻 0.8*ATR = {0.8*atr:.6f})")
            
            print(f"做多還需拉升: {diff_long:.6f} (+{pct_long:.2f}%)")
            print(f"做空還需砸盤: {diff_short:.6f} (-{pct_short:.2f}%)")
            
            # 通道寬度是否張嘴
            kc_width = kc_upper - kc_lower
            print(f"KC Width (ATR倍數): {kc_width/atr:.2f} ATR (通常>3才算明顯張嘴)")
            
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(main())
