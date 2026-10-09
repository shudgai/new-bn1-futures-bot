import asyncio
from core.services.candle_data import get_latest_candles
async def main():
    frame = await get_latest_candles("NEIRO/USDT")
    if frame is not None:
        print(frame[['timestamp', 'open', 'high', 'low', 'close', 'kc_upper', 'kc_lower', 'kc_middle', 'ma5', 'ma15', 'atr']].tail(10))
    else:
        print("No frame found")
asyncio.run(main())
