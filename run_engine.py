#!/usr/bin/env python3
"""
交易引擎主程序 - 後台掃描訊號並執行交易
"""
import asyncio
import sys
import os
import signal
from core.engine import engine

async def main():
    print("🤖 交易引擎啟動中...")
    try:
        await engine.start()
        # 等待引擎主任務持續運行
        if hasattr(engine, 'task') and engine.task:
            await engine.task
    except KeyboardInterrupt:
        print("\n⏸️  接收到中斷信號，正在停止引擎...")
        await engine.stop()
    except asyncio.CancelledError:
        print("\n⏸️  引擎已停止")
    except Exception as e:
        print(f"❌ 引擎錯誤: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
