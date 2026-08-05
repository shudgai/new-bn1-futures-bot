#!/usr/bin/env python3
"""診斷當前訊號狀態 - 檢查為什麼無法開倉"""
import asyncio
import json
from datetime import datetime
from core.engine import engine
from core.config import DEFAULT_SYMBOLS

async def diagnose():
    """檢查當前訊號"""
    print("🔍 正在診斷訊號狀態...\n")
    
    # 初始化引擎連接
    await engine.update_market_prices()
    
    print(f"⏰ 當前時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📊 掃描幣種: {len(DEFAULT_SYMBOLS)} 個\n")
    
    # 檢查帳戶狀態
    print("💾 帳戶狀態:")
    print(f"   持倉: {len(engine.account.positions)} 個")
    print(f"   待單: {len(engine.account.pending_limit_orders)} 個")
    print(f"   餘額: {engine.account.get_available_balance():.2f} USDT\n")
    
    # 檢查每個幣種的訊號
    print("📈 訊號掃描結果:")
    signal_count = 0
    for symbol in DEFAULT_SYMBOLS[:5]:  # 只查前5個幣種示例
        if symbol in engine.account.positions:
            print(f"   {symbol}: 已持倉，跳過")
            continue
        
        # 抓取K線數據
        df = await engine.fetch_klines(symbol, timeframe="1m", limit=100)
        if df.empty or len(df) < 50:
            print(f"   {symbol}: K線資料不足")
            continue
        
        # 檢查5分鐘週期 
        df_5m = await engine.fetch_klines(symbol, timeframe="5m", limit=30)
        
        price = float(df.iloc[-1]['close'])
        print(f"   {symbol}: 價格 {price:.6g}")
        
        signal_count += 1
    
    print(f"\n   掃描完成，共檢查 {signal_count} 個未持倉幣種")
    
    # 檢查日誌
    print("\n📝 最近日誌:")
    logs = engine.account.logs[-5:] if engine.account.logs else []
    for log in logs:
        print(f"   [{log.get('level', 'INFO')}] {log.get('message', '')}")

if __name__ == "__main__":
    asyncio.run(diagnose())
