import sys
import os
sys.path.append(os.getcwd())
import asyncio
from core.engine import TradingEngine
from core.indicators import detect_ma5_ma25_cross_and_turn

async def main():
    engine = TradingEngine()
    df = await engine.fetch_klines("1000PEPE/USDT")
    df['symbol'] = "1000PEPE/USDT"
    
    # We must calculate MA3!
    df['ma3'] = df['close'].rolling(window=3).mean()
    df['ma5'] = df['close'].rolling(window=5).mean()
    df['ma25'] = df['close'].rolling(window=25).mean()
    df['atr'] = df['high'].rolling(14).max() - df['low'].rolling(14).min() # mock atr
    df = df.dropna()
    
    # We will slice df to simulate different points in time (last 10 candles)
    for i in range(10, 0, -1):
        if i == 1:
            sub_df = df.copy()
        else:
            sub_df = df.iloc[:-i+1].copy()
            
        recent_ma3 = sub_df['ma3'].iloc[-12:].values if len(sub_df) >= 12 else sub_df['ma3'].values
        ma3_curr = recent_ma3[-1]
        ma3_prev = recent_ma3[-2]
        
        history = recent_ma3[:-1]
        recent_max = max(history)
        recent_min = min(history)
        
        idx_max = len(history) - 1 - history[::-1].tolist().index(recent_max)
        idx_min = len(history) - 1 - history[::-1].tolist().index(recent_min)
        
        climb_before_peak = recent_max - (min(history[:idx_max+1]) if idx_max >= 0 else recent_max)
        drop_from_peak = recent_max - ma3_curr
        atr = float(sub_df['atr'].iloc[-1])
        
        is_peak_forming = (ma3_curr < ma3_prev) and (drop_from_peak >= atr * 0.05) and (climb_before_peak >= atr * 0.05)
        
        print(f"Candle -{i}: close={sub_df['close'].iloc[-1]:.6f}, ma3_curr={ma3_curr:.6f}, ma3_prev={ma3_prev:.6f}, "
              f"drop={drop_from_peak:.6f}, atr*0.05={atr*0.05:.6f}, climb={climb_before_peak:.6f} -> is_peak_forming: {is_peak_forming}")

if __name__ == "__main__":
    asyncio.run(main())
