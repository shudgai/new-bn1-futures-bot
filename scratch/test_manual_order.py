import asyncio
from core.engine import engine
from core.services.manual_order_service import process_manual_order

async def main():
    res = await process_manual_order("1000PEPE/USDT", "LONG", amount=100)
    print(res)

asyncio.run(main())
