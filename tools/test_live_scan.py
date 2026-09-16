import asyncio
import logging
from core.engine import TradingEngine
from core.services.symbol_runner import process_single_symbol_runner

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

async def main():
    print("�� 啟動模擬掃描 (Live Scan Test)...")
    engine = TradingEngine(mode="paper")
    await engine.initialize()
    
    # 測試一個常見幣種
    symbol = "BTCUSDT"
    print(f"📡 正在拉取 {symbol} 最新行情並通過 UnifiedEntryStrategy 評估...")
    
    # 執行一次空倉狀態掃描
    await process_single_symbol_runner(
        engine, symbol, now_time=0, btc_1m_turn=None, daily_halt=False,
        exit_frame=None, exit_quote=None, exit_only=False
    )
    
    # 模擬一次強制平倉與狀態重置
    print("\n⚠️ 模擬持倉並觸發平倉 (DualTrackExitStrategy)...")
    engine.account.positions[symbol] = {
        "side": "LONG", "entry_price": 60000, "qty": 0.1, "open_timestamp": 0
    }
    
    await process_single_symbol_runner(
        engine, symbol, now_time=0, btc_1m_turn=None, daily_halt=False,
        exit_frame=None, exit_quote=None, exit_only=False
    )
    
    print("\n✅ 測試完成！")
    
if __name__ == "__main__":
    asyncio.run(main())
