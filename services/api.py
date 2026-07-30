import os
import csv
import io
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from core.config import (
    PORT, PAPER_TRADING, DEFAULT_SYMBOLS, LEVERAGE, SIGNAL_LEVERAGE_CAPS, TRADE_AMOUNT_USDT,
    API_TOKEN, TAKER_FEE_RATE,
)
from core.engine import engine
from core.paper_account import get_taipei_now_str

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

def trade_date_str(trade: dict) -> str:
    """交易 id 是毫秒級 unix timestamp，trade['time'] 沒有年份無法拿來篩選日期，故用 id 換算台北時區日期"""
    return datetime.fromtimestamp(trade.get("id", 0) / 1000, TAIPEI_TZ).strftime("%Y-%m-%d")

app = FastAPI(title="Binance Futures Bot 2.0")

def visible_tickers():
    """只回傳目前牌面與持倉，避免輪替後的舊價格快取留在介面。"""
    symbols = list(dict.fromkeys([*DEFAULT_SYMBOLS, *engine.account.positions.keys()]))
    result = {}
    for symbol in symbols:
        price = engine.tickers.get(symbol) or engine.tickers.get(f"{symbol}:USDT")
        if price is not None:
            result[symbol] = price
    return result

def positions_with_triggers():
    """持倉列表附加手動平倉參考指標（跌破/站上均線、跌破前低/站上前高），
    純參考用途，不影響自動止損止利。"""
    result = []
    for symbol, pos in engine.account.positions.items():
        merged = dict(pos)
        merged["trigger"] = engine.position_triggers.get(symbol, {"active": False, "reasons": []})
        result.append(merged)
    return result

def active_leverage_by_score():
    return {
        str(score): {
            symbol: engine.symbol_rotation.get_dynamic_leverage(symbol, score)
            for symbol in DEFAULT_SYMBOLS
        }
        for score in (70, 80, 100)
    }

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

class ManualOrderRequest(BaseModel):
    symbol: str
    side: str # LONG or SHORT
    amount: float

class ManualCloseRequest(BaseModel):
    symbol: str

def require_auth(authorization: str = Header(default="")) -> None:
    """異動端點（開倉/平倉/開關機器人）的身份驗證。
    未設定 API_TOKEN 時（本機開發階段）不強制驗證，只在啟動時印出警告。"""
    if not API_TOKEN:
        return
    if authorization != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="未授權：缺少或錯誤的 Authorization Header")

@app.on_event("startup")
async def startup_event():
    if not API_TOKEN:
        print("⚠️ API_TOKEN 未設定：/api/toggle、/api/manual_order、/api/manual_close 目前不驗證身份，對外開放前請在 .env 設定 API_TOKEN")
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


def visible_system_logs():
    """保留一般歷史日誌，但12幣訊號進度只顯示最新一組。"""
    logs = engine.account.logs[-200:]
    progress_prefix = "📊 [12幣訊號進度]"
    latest_progress_index = next(
        (index for index in range(len(logs) - 1, -1, -1)
         if logs[index].get("text", "").startswith(progress_prefix)),
        None,
    )
    return [
        log for index, log in enumerate(logs)
        if not log.get("text", "").startswith(progress_prefix)
        or index == latest_progress_index
    ][-50:]

@app.get("/api/status")
async def get_status():
    unrealized = await engine.account.update_positions(engine.tickers)
    return {
        "is_running": engine.is_running,
        "strategy": "Keltner + SuperTrend 混合模式 (12幣雙向)",
        "environment": "binance_testnet",
        "paper_trading": PAPER_TRADING,
        "available_balance": round(engine.account.available_balance, 2),
        "port": PORT,
        "balance": round(engine.account.balance, 2),
        "realized_pnl": round(engine.account.realized_pnl, 2),
        "unrealized_pnl": round(unrealized, 2),
        "leverage": LEVERAGE,
        "leverage_map": {
            symbol: engine.symbol_rotation.get_dynamic_leverage(symbol, 100)
            for symbol in DEFAULT_SYMBOLS
        },
        "leverage_by_score": active_leverage_by_score(),
        "signal_leverage_caps": {
            str(score): ("symbol_max" if cap is None else cap)
            for score, cap in SIGNAL_LEVERAGE_CAPS
        },
        "trade_amount": TRADE_AMOUNT_USDT,
        "pullback_outcome_stats": dict(engine.account.pullback_outcome_stats),
        "entry_filter_stats": dict(engine.account.entry_filter_stats),
        "entry_filter_last": dict(engine.account.entry_filter_last),
        "shadow_parameter_stats": dict(engine.account.shadow_parameter_stats),
        "shadow_parameter_last": dict(engine.account.shadow_parameter_last),
        "taker_fee_rate": TAKER_FEE_RATE,
        "symbols": list(dict.fromkeys([*DEFAULT_SYMBOLS, *engine.account.positions.keys()])),
        "symbol_directions": {symbol: "BOTH" for symbol in DEFAULT_SYMBOLS},
        "symbol_rotation": engine.symbol_rotation.status(),
        "volatility_stats": {
            symbol: engine.symbol_rotation.volatility_stats[symbol]
            for symbol in DEFAULT_SYMBOLS
            if symbol in engine.symbol_rotation.volatility_stats
        },
        "trade_ai_analysis": engine.symbol_rotation.trade_analysis.status(),
        "trade_ai_worker": {
            "mode": "event_driven",
            "running": bool(
                engine.analysis_task and not engine.analysis_task.done()
            ),
            "retry_after_sec": engine.symbol_rotation.trade_analysis.retry_after_sec,
        },
        "tickers": visible_tickers(),
        "positions": positions_with_triggers(),
        "trades": engine.account.trades[:50],
        "total_trades": len(engine.account.trades),
        "trade_dates": sorted({trade_date_str(t) for t in engine.account.trades}, reverse=True),
        "logs": visible_system_logs()
    }

@app.get("/api/prices")
async def get_prices():
    """輕量即時價格端點 — 前端每秒輪詢，只更新 tickers 與 positions"""
    return {
        "symbols": list(dict.fromkeys([*DEFAULT_SYMBOLS, *engine.account.positions.keys()])),
        "tickers": visible_tickers(),
        "positions": positions_with_triggers(),
        "unrealized_pnl": round(await engine.account.update_positions(engine.tickers), 2),
        "balance": round(engine.account.balance, 2),
    }

@app.post("/api/toggle")
async def toggle_bot(_auth: None = Depends(require_auth)):
    if engine.is_running:
        await engine.stop()
    else:
        await engine.start()
    return {"is_running": engine.is_running}

@app.post("/api/manual_order")
async def manual_order(req: ManualOrderRequest, _auth: None = Depends(require_auth)):
    symbol = req.symbol.strip()
    side = req.side.upper()
    amount = req.amount if req.amount > 0 else TRADE_AMOUNT_USDT

    if symbol not in engine.tickers:
        raise HTTPException(status_code=400, detail="幣種價格尚未載入")

    price = engine.tickers[symbol]
    atr = price * 0.015
    sl = price - (atr * 1.5) if side == "LONG" else price + (atr * 1.5)
    tp = price + (atr * 3.0) if side == "LONG" else price - (atr * 3.0)

    success = await engine.account.open_position(
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

@app.get("/api/export_trades")
async def export_trades(date: str = None):
    """匯出交易明細 (CSV)。date 格式 YYYY-MM-DD（台北時區），不帶則匯出全部交易紀錄"""
    fieldnames = [
        "id", "time", "symbol", "action", "side", "price", "qty", "amount",
        "fee", "pnl", "status", "leverage", "signal_score", "reason", "sl", "tp"
    ]
    # trades 是新到舊排列 (insert(0, ...))，匯出時改成舊到新，方便照時間順序閱讀
    all_trades = list(reversed(engine.account.trades))

    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="date 格式須為 YYYY-MM-DD")
        selected_trades = [t for t in all_trades if trade_date_str(t) == date]
    else:
        selected_trades = all_trades

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for t in selected_trades:
        writer.writerow({k: t.get(k, "") for k in fieldnames})

    label = date if date else "all"
    filename = f"trade_history_{label}_{get_taipei_now_str('%Y%m%d_%H%M%S')}.csv"
    return Response(
        content=output.getvalue().encode("utf-8-sig"),  # BOM 確保 Excel 開啟中文不亂碼
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Total-Records": str(len(selected_trades)),
        }
    )

@app.post("/api/manual_close")
async def manual_close(req: ManualCloseRequest, _auth: None = Depends(require_auth)):
    symbol = req.symbol.strip()
    if symbol not in engine.account.positions:
        raise HTTPException(status_code=400, detail="查無此持倉")
    price = engine.tickers.get(symbol, engine.account.positions[symbol]["entry_price"])
    success = await engine.account.close_position(symbol, price, "手動平倉")
    if not success:
        raise HTTPException(status_code=502, detail="Binance Testnet 平倉失敗")
    return {"status": "success", "message": f"手動平倉 {symbol}"}
