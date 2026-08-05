#!/usr/bin/env python3
"""診斷引擎啟動問題"""
import asyncio
import sys
from core.engine import engine

async def main():
    try:
        print("🤖 引擎啟動中...", flush=True)
        await engine.start()
        print("✅ 引擎啟動成功", flush=True)
        print(f"   任務 ID: {engine.task.get_name() if engine.task else '無'}", flush=True)
        
        # 運行 10 分鐘後停止
        await asyncio.sleep(600)
        print("⏹️  10 分鐘已到，停止引擎...", flush=True)
        await engine.stop()
        print("✅ 引擎已停止", flush=True)
    except asyncio.TimeoutError:
        print("❌ 初始化超時", flush=True)
        sys.exit(1)
    except Exception as e:
        print(f"❌ 錯誤: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
