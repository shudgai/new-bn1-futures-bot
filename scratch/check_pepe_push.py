import asyncio
from core.engine import Engine
from core.services.entry_contract import is_solid_push

async def main():
    engine = Engine()
    await engine.initialize()
    klines = await engine.fetch_klines('1000PEPE/USDT', '1m', 30)
    for idx, row in klines.iterrows():
        span = float(row.high) - float(row.low)
        if span == 0: span = 1e-9
        body = float(row.close) - float(row.open)
        ratio = abs(body) / span
        
        is_break = float(row.close) > float(row.kc_upper)
        print(f"Time: {row.timestamp}, Open: {row.open:.6f}, Close: {row.close:.6f}, High: {row.high:.6f}, Low: {row.low:.6f}")
        print(f"  Green: {body>0}, Body Ratio: {ratio:.3f}, Is Solid: {ratio >= 0.35}, Breakout: {is_break}")
        
asyncio.run(main())
