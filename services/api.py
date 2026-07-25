import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from core.config import PORT, DEFAULT_SYMBOLS, LEVERAGE, TRADE_AMOUNT_USDT
from core.engine import engine

app = FastAPI(title="Binance Futures Bot 2.0")

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

class ManualOrderRequest(BaseModel):
    symbol: str
    side: str # LONG or SHORT
    amount: float

class ManualCloseRequest(BaseModel):
    symbol: str

@app.on_event("startup")
async def startup_event():
    await engine.start()

@app.on_event("shutdown")
async def shutdown_event():
    await engine.stop()

@app.get("/", response_class=HTMLResponse)
async def get_index():
    index_file = os.path.join(WEB_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return HTMLResponse("<h1>Binance Bot Web Dashboard Not Found</h1>")

@app.get("/api/status")
async def get_status():
    unrealized = engine.account.update_positions(engine.tickers)
    return {
        "is_running": engine.is_running,
        "strategy": "SuperTrend + Keltner Breakout",
        "port": PORT,
        "balance": round(engine.account.balance, 2),
        "realized_pnl": round(engine.account.realized_pnl, 2),
        "unrealized_pnl": round(unrealized, 2),
        "leverage": LEVERAGE,
        "trade_amount": TRADE_AMOUNT_USDT,
        "symbols": DEFAULT_SYMBOLS,
        "tickers": engine.tickers,
        "positions": list(engine.account.positions.values()),
        "trades": engine.account.trades[:50],
        "logs": engine.account.logs[-50:]
    }

@app.post("/api/toggle")
async def toggle_bot():
    if engine.is_running:
        await engine.stop()
    else:
        await engine.start()
    return {"is_running": engine.is_running}

@app.post("/api/manual_order")
async def manual_order(req: ManualOrderRequest):
    symbol = req.symbol.strip()
    side = req.side.upper()
    amount = req.amount if req.amount > 0 else TRADE_AMOUNT_USDT

    if symbol not in engine.tickers:
        raise HTTPException(status_code=400, detail="幣種價格尚未載入")

    price = engine.tickers[symbol]
    atr = price * 0.015
    sl = price - (atr * 1.5) if side == "LONG" else price + (atr * 1.5)
    tp = price + (atr * 3.0) if side == "LONG" else price - (atr * 3.0)

    success = engine.account.open_position(
        symbol=symbol,
        side=side,
        price=price,
        amount_usdt=amount,
        sl=sl,
        tp=tp,
        reason="手動下單"
    )
    if not success:
        raise HTTPException(status_code=400, detail="已有該幣種持倉或系統異常")
    return {"status": "success", "message": f"手動開倉 {side} {symbol}"}

@app.post("/api/manual_close")
async def manual_close(req: ManualCloseRequest):
    symbol = req.symbol.strip()
    if symbol not in engine.account.positions:
        raise HTTPException(status_code=400, detail="查無此持倉")
    price = engine.tickers.get(symbol, engine.account.positions[symbol]["entry_price"])
    engine.account.close_position(symbol, price, "手動平倉")
    return {"status": "success", "message": f"手動平倉 {symbol}"}
