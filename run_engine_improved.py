#!/usr/bin/env python3
import asyncio
import sys
import os
import signal
from core.engine import engine

async def main():
    try:
        await engine.start()
        # 等待引擎主任務持續運行
        if hasattr(engine, 'task') and engine.task:
            try:
                await engine.task
            except asyncio.CancelledError:
                pass
    except KeyboardInterrupt:
        await engine.stop()
    except Exception as e:
        engine.account.log(f"❌ 引擎致命錯誤: {e}", "ERROR")
        import traceback
        traceback.print_exc()
    finally:
        await engine.stop()

if __name__ == "__main__":
    # 確保日誌立即寫入（無緩衝）
    asyncio.run(main())
