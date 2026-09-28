import asyncio
from core.engine import Engine
async def main():
    try:
        e = Engine()
        await e.initialize()
        frame = await e._entry_boundary_frame('1000PEPEUSDT')
        print(frame.columns)
    except Exception as exc:
        print(f"Error: {exc}")
asyncio.run(main())
