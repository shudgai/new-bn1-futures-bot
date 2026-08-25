import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.getcwd())

from core.engine import TradingEngine

async def test():
    engine = TradingEngine()
    df_cr = await engine.fetch_klines("1000PEPE/USDT", timeframe="1m", limit=100)
    from core.indicators import detect_ma5_ma25_cross_and_turn
    import pandas as pd
    
    # Calculate exactly what indicators.py does
    cr_info = detect_ma5_ma25_cross_and_turn(df_cr)
    
    adx_curr = float(df_cr['adx'].iloc[-1]) if 'adx' in df_cr.columns else 0
    ma5_curr = float(df_cr['ma5'].iloc[-1]) if 'ma5' in df_cr.columns else 0
    ma25_curr = float(df_cr['ma25'].iloc[-1]) if 'ma25' in df_cr.columns else 0
    
    print(f"MA5: {ma5_curr:.6f}, MA25: {ma25_curr:.6f}")
    print(f"MA5 < MA25: {ma5_curr < ma25_curr}")
    print(f"ADX: {adx_curr:.2f}")
    print(f"Signal Result: {cr_info}")

asyncio.run(test())
