import asyncio
from core.engine import engine
from core.services.entry_service import check_entry_signals

async def main():
    await engine.initialize_data()
    df = engine.data_frames.get("1000PEPE/USDT")
    if df is None or df.empty:
        print("No data")
        return
    # find 09:45 and 09:46
    import pandas as pd
    df['dt'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True).dt.tz_convert('Asia/Taipei')
    # Filter up to 09:46 to see what c1 and c2 were
    sub = df[df['dt'] <= '2026-09-25 09:46:59']
    if sub.empty: return
    c1 = sub.iloc[-2]
    c2 = sub.iloc[-1]
    print(f"c1 (09:45): open={c1['open']}, close={c1['close']}, kc_upper={c1['kc_upper']}, kc_mid={c1['kc_middle']}")
    print(f"c2 (09:46): open={c2['open']}, close={c2['close']}, kc_upper={c2['kc_upper']}, kc_mid={c2['kc_middle']}, atr={c2['atr']}")
    
    res = check_entry_signals(sub, "LONG", 0.0)
    print("Result:", res)

asyncio.run(main())
