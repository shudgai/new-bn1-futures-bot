import asyncio
import sys

from core.engine import TradingEngine
from core.services.strategies.unified_entry_strategy import validate_channel_expansion

async def check():
    engine = TradingEngine()
    await engine.initialize()
    for symbol in ["1000PEPEUSDT", "LOBSTERUSDT"]:
        try:
            frame = await engine.fetch_klines(symbol, timeframe='1m', limit=200, keep_live=True)
            if frame is None or frame.empty:
                print(f"{symbol}: 無法獲取 K 線資料")
                continue
                
            frame = engine.strategy.compute_indicators(frame.copy())
            if len(frame) < 3:
                print(f"{symbol}: K 線數量不足")
                continue
                
            c0, c1, c = frame.iloc[-3], frame.iloc[-2], frame.iloc[-1]
            
            from core.services.strategies.outer_strategy import ck_direction
            ck = ck_direction(frame)
            # Default to test BOTH side if possible, or just the one matching CK
            for side in ["LONG", "SHORT"]:
                ck_status = '連續同向' if ck == side else '尚未連續同向'
                indicators = {
                    'kc_upper': [float(c0.kc_upper), float(c1.kc_upper), float(c.kc_upper)],
                    'kc_lower': [float(c0.kc_lower), float(c1.kc_lower), float(c.kc_lower)],
                    'kc_middle': [float(c0.kc_middle), float(c1.kc_middle), float(c.kc_middle)],
                    'atr': [float(c0.atr), float(c1.atr), float(c.atr)],
                    'ck_status': ck_status
                }
                
                passed, reason = validate_channel_expansion(indicators, side)
                print(f"[{symbol}] [{side}] validate_channel_expansion: {passed} (Reason: {reason})")
        except Exception as e:
            print(f"[{symbol}] 發生錯誤: {e}")
            
    await engine.close()

if __name__ == "__main__":
    asyncio.run(check())
