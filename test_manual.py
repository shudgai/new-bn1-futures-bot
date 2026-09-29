import asyncio
from core.engine import TradingEngine

async def run():
    engine = TradingEngine()
    
    symbols = ["1000PEPEUSDT", "LOBSTERUSDT"]
    for sym in symbols:
        price = float(engine.tickers.get(sym, 0.0) or 0.0)
        if price == 0:
            frame = await engine.fetch_klines(sym, '1m', 1)
            price = float(frame.iloc[-1]['close'])
        
        # open LONG 5 USDT with manual context
        print(f"[{sym}] 嘗試發送微型手動開多信號...")
        try:
            res = await engine.account.open_position(
                symbol=sym,
                side="LONG",
                price=price,
                amount_usdt=6.0, # Minimum notional usually 5 for binance testnet
                sl=0.0,
                tp=0.0,
                reason="MANUAL_TEST",
                entry_context={'is_manual': True, 'source': 'MANUAL'}
            )
            print(f"[{sym}] 測試單回報: {res}")
        except Exception as e:
            print(f"[{sym}] 開倉錯誤: {e}")
            
    print("\n當前持倉狀態:")
    for sym, pos in engine.account.positions.items():
        print(f"[{sym}] {pos}")

if __name__ == "__main__":
    asyncio.run(run())
