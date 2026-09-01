import asyncio
import os
import csv
import io
import time
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from core.config import (
    PORT, PAPER_TRADING, DEFAULT_SYMBOLS, LEVERAGE, SIGNAL_LEVERAGE_CAPS, TRADE_AMOUNT_USDT,
    TAKER_FEE_RATE, SLIPPAGE_PCT, MAX_SLOTS, CONTINUOUS_PIVOT_ONLY, PIVOT_LONG_ONLY, get_effective_slot_count
)
from core.engine import engine
from core.paper_account import get_taipei_now_str
from core.trade_history_analysis import TradeHistoryAnalyzer
from services.ma3_pivot_analysis import analyze_ma3_pivots

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

def estimated_net_unrealized_pnl() -> float:
    """估算全部持倉此刻市價平倉後，扣雙邊手續費與平倉滑價的淨利。"""
    total = 0.0
    for pos in engine.account.positions.values():
        entry = float(pos.get("entry_price") or 0.0)
        mark = float(pos.get("mark_price") or entry)
        qty = float(pos.get("qty") or 0.0)
        raw = (mark - entry) * qty if pos.get("side") == "LONG" else (entry - mark) * qty
        total += raw - (entry + mark) * qty * TAKER_FEE_RATE - mark * qty * SLIPPAGE_PCT
    return total


def positions_with_triggers():
    """持倉列表附加手動平倉參考指標（跌破/站上均線、跌破前低/站上前高），
    純參考用途，不影響自動止損止利。"""
    result = []
    for symbol, pos in engine.account.positions.items():
        merged = dict(pos)
        merged["trigger"] = engine.position_triggers.get(symbol, {"active": False, "reasons": []})
        entry = float(merged.get("entry_price") or 0.0)
        mark = float(merged.get("mark_price") or entry)
        qty = float(merged.get("qty") or 0.0)
        raw = (mark - entry) * qty if merged.get("side") == "LONG" else (entry - mark) * qty
        merged["estimated_net_unrealized_pnl"] = raw - (entry + mark) * qty * TAKER_FEE_RATE - mark * qty * SLIPPAGE_PCT
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
BOT_PAUSED_FILE = os.path.join(os.path.dirname(WEB_DIR), "data", "bot_paused.flag")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="web-static")

# 圖表資料只供顯示，不能讓瀏覽器每秒的請求與交易循環爭用 Binance 額度。
# 成功資料短暫快取；外部行情暫時失敗時則回傳最後一份資料，讓介面保持可用。
KLINE_CACHE_TTL_SECONDS = 5.0
_kline_cache = {}
_kline_inflight = {}

class ManualOrderRequest(BaseModel):
    symbol: str
    side: str # LONG or SHORT
    amount: float

class ManualCloseRequest(BaseModel):
    symbol: str

@app.on_event("startup")
async def startup_event():
    if os.path.exists(BOT_PAUSED_FILE):
        await engine.account.initialize()
        engine.account.log("⏸️ 機器人維持暫停；按下啟動後才接管持倉", "INFO")
        return
    await engine.start()

@app.on_event("shutdown")
async def shutdown_event():
    # 程序真正關閉時才釋放交易所連線；網頁暫停機器人仍需保留行情連線，
    # 否則再次啟動會重用已關閉的 ccxt instance。
    await engine.stop(close_exchanges=True)

@app.get("/", response_class=HTMLResponse)
async def get_index():
    index_file = os.path.join(WEB_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return HTMLResponse("<h1>Binance Bot Web Dashboard Not Found</h1>")


def visible_system_logs():
    """保留一般歷史日誌，但幣訊號進度只顯示最新一組。"""
    logs = engine.account.logs[-200:]
    # 改用通用包含關係，支持 12, 16, 18 幣動態更新
    latest_progress_index = next(
        (index for index in range(len(logs) - 1, -1, -1)
         if "幣訊號進度]" in logs[index].get("text", "")),
        None,
    )
    return [
        log for index, log in enumerate(logs)
        if "幣訊號進度]" not in log.get("text", "")
        or index == latest_progress_index
    ][-50:]

@app.get("/api/status")
async def get_status(response: Response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    unrealized = await engine.account.update_positions(engine.tickers)
    return {
        "is_running": engine.is_running,
        "strategy": (f"KC {os.getenv('CONTINUOUS_REVERSE_TIMEFRAME', '1m')} MA3須越外軌 + 兩根K半通道 + 0.15ATR線距 ({len(DEFAULT_SYMBOLS)}幣順勢)" if CONTINUOUS_PIVOT_ONLY and not PIVOT_LONG_ONLY else f"KC 3m下軌 + 1m谷底提早買入・3m上軌平多 ({len(DEFAULT_SYMBOLS)}幣只做多)" if CONTINUOUS_PIVOT_ONLY else f"Keltner + SuperTrend 混合模式 ({len(DEFAULT_SYMBOLS)}幣順勢)"),
        "environment": "binance_testnet",
        "paper_trading": PAPER_TRADING,
        "available_balance": round(engine.account.available_balance, 2),
        "port": PORT,
        "balance": round(engine.account.balance + sum(p.get("margin", 0.0) for p in engine.account.positions.values()), 2),
        "realized_pnl": round(engine.account.realized_pnl, 2),
        "unrealized_pnl": round(unrealized, 2),
        "estimated_net_unrealized_pnl": round(estimated_net_unrealized_pnl(), 2),
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
        "max_slots": MAX_SLOTS,
        "effective_slots": get_effective_slot_count(engine.account.get_wallet_balance()),
        "trade_amount": round(
            engine.account.get_wallet_balance() / max(
                get_effective_slot_count(engine.account.get_wallet_balance()), 1
            ), 2
        ) if MAX_SLOTS > 0 else TRADE_AMOUNT_USDT,
        "pullback_outcome_stats": dict(engine.account.pullback_outcome_stats),
        "entry_filter_stats": dict(engine.account.entry_filter_stats),
        "entry_filter_last": dict(engine.account.entry_filter_last),
        "shadow_parameter_stats": dict(engine.account.shadow_parameter_stats),
        "shadow_parameter_last": dict(engine.account.shadow_parameter_last),
        "taker_fee_rate": TAKER_FEE_RATE,
        "slippage_pct": SLIPPAGE_PCT,
        "symbols": list(dict.fromkeys([*DEFAULT_SYMBOLS, *engine.account.positions.keys()])),
        "symbol_directions": {
            symbol: engine.symbol_rotation.direction_map.get(symbol, "WAIT")
            for symbol in DEFAULT_SYMBOLS
        },
        "market_modes": {
            symbol: engine._continuous_market_mode.get(symbol, "WAIT")
            for symbol in dict.fromkeys([*DEFAULT_SYMBOLS, *engine.account.positions.keys()])
        },
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
        "ticker_updated_at": engine.last_ticker_success_ts,
        "positions": positions_with_triggers(),
        "trades": engine.account.trades[:50],
        "total_trades": len(engine.account.trades),
        "trade_dates": sorted({trade_date_str(t) for t in engine.account.trades}, reverse=True),
        "logs": visible_system_logs()
    }

@app.get("/api/prices")
async def get_prices(response: Response):
    """輕量即時價格端點 — 前端每秒輪詢，只更新 tickers 與 positions"""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    unrealized = await engine.account.update_positions(engine.tickers)
    return {
        "symbols": list(dict.fromkeys([*DEFAULT_SYMBOLS, *engine.account.positions.keys()])),
        "symbol_directions": {
            symbol: engine.symbol_rotation.direction_map.get(symbol, "WAIT")
            for symbol in DEFAULT_SYMBOLS
        },
        "market_modes": {
            symbol: engine._continuous_market_mode.get(symbol, "WAIT")
            for symbol in dict.fromkeys([*DEFAULT_SYMBOLS, *engine.account.positions.keys()])
        },
        "tickers": visible_tickers(),
        "ticker_updated_at": engine.last_ticker_success_ts,
        "positions": positions_with_triggers(),
        "unrealized_pnl": round(unrealized, 2),
        "estimated_net_unrealized_pnl": round(estimated_net_unrealized_pnl(), 2),
        "balance": round(engine.account.balance + sum(p.get("margin", 0.0) for p in engine.account.positions.values()), 2),
    }

@app.get("/api/quant-analysis")
async def get_quant_analysis():
    """唯讀量化報表：比較目前進場方式的實際淨績效。"""
    return TradeHistoryAnalyzer.build_quant_report(engine.account.trades)


@app.get("/api/ma3-pivot-analysis")
async def get_ma3_pivot_analysis(
    symbol: str, timeframe: str = "1m", limit: int = 1000,
    horizon_bars: int = 5, target_atr: float = 0.30, stop_atr: float = 0.25,
):
    """分析 MA3 V/倒V 的尖銳度，不影響任何下單決策。"""
    if timeframe not in {"1m", "5m", "15m", "1h"}:
        raise HTTPException(status_code=400, detail="timeframe 僅支援 1m、5m、15m、1h")
    if not 50 <= limit <= 1500:
        raise HTTPException(status_code=400, detail="limit 必須介於 50 到 1500")
    try:
        frame = await engine.fetch_klines(symbol.strip(), timeframe=timeframe, limit=limit)
        if frame.empty:
            raise HTTPException(status_code=400, detail="無法獲取 K 線資料")
        return {
            "symbol": symbol.strip(), "timeframe": timeframe,
            **analyze_ma3_pivots(frame, horizon_bars, target_atr, stop_atr),
        }
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))



@app.post("/api/ai-trade-analysis")
async def run_ai_trade_analysis():
    """要求自建 AI 立即重新分析已平倉交易；僅產生建議，不影響交易設定。"""
    analyzer = engine.symbol_rotation.trade_analysis
    analyzer.analysis["history_digest"] = ""
    analyzer.analysis["updated_at"] = 0.0

    if engine.is_running:
        engine.request_trade_analysis()
        return {
            "status": "queued",
            "message": "已交由自建 AI 重新分析，完成後會自動更新面板。",
            "analysis": analyzer.status(),
        }

    await analyzer.analyze_if_changed(engine.account.trades)
    return {
        "status": analyzer.status().get("status", "completed"),
        "message": "自建 AI 分析已完成。",
        "analysis": analyzer.status(),
    }

@app.post("/api/toggle")
async def toggle_bot():
    if engine.is_running:
        await engine.stop()
        os.makedirs(os.path.dirname(BOT_PAUSED_FILE), exist_ok=True)
        with open(BOT_PAUSED_FILE, "w", encoding="utf-8") as pause_file:
            pause_file.write("paused\n")
    else:
        if os.path.exists(BOT_PAUSED_FILE):
            os.remove(BOT_PAUSED_FILE)
        await engine.start()
    return {"is_running": engine.is_running}

@app.post("/api/manual_order")
async def manual_order(req: ManualOrderRequest):
    symbol = req.symbol.strip()
    side = req.side.upper()

    if symbol not in engine.tickers:
        raise HTTPException(status_code=400, detail="幣種價格尚未載入")
    if symbol in engine.account.positions:
        raise HTTPException(status_code=400, detail=f"{symbol} 已有持倉")

    price = engine.tickers[symbol]

    # 從最新 K 線計算真實 ATR 以便與自動下單規則一致
    df = await engine.fetch_klines(symbol, timeframe="5m", limit=30)
    if not df.empty and "atr" in df.columns:
        atr = float(df["atr"].iloc[-1])
    else:
        atr = price * 0.015

    from core.strategy import compute_sl_tp_distance, build_sl_tp_for_side
    from core.config import get_leverage
    leverage = get_leverage(symbol)
    if req.amount > 0:
        amount = req.amount
    else:
        slot_amount = max(0.0, float(engine._continuous_entry_amount()))
        available = max(0.0, float(engine.account.get_available_balance()))
        fee_safe_available = available / (
            1.0 + leverage * max(TAKER_FEE_RATE, 0.0)
        )
        amount = min(slot_amount, fee_safe_available)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="沒有可用交易槽位或餘額不足")

    sl_dist, tp_dist = compute_sl_tp_distance(price, atr)
    sl, tp = build_sl_tp_for_side(price, side, sl_dist, tp_dist)

    success = await engine.account.open_position(
        symbol=symbol,
        side=side,
        price=price,
        amount_usdt=amount,
        sl=sl,
        tp=tp,
        reason=f"手動開倉_{side}",
        atr=atr,
        leverage=leverage,
        signal_score=100,
        entry_context={
            "entry_mode": "CHANNEL_SWING",
            "wave_regime": "RANGE",
            "market_mode": "RANGE",
        },
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
async def manual_close(req: ManualCloseRequest):
    symbol = req.symbol.strip()
    if not symbol:
        raise HTTPException(status_code=422, detail="缺少幣種")

    # Closing is idempotent: a repeated click or an auto-exit racing this
    # request must never submit a second reduce-only order or look like a
    # server failure to the UI.
    account = engine.account
    if symbol in account.closing_lock:
        return {"status": "closing", "message": f"{symbol} 平倉處理中"}
    position = account.positions.get(symbol)
    if position is None:
        return {"status": "already_closed", "message": f"{symbol} 已平倉"}

    price = engine.tickers.get(symbol, position["entry_price"])
    success = await account.close_position(symbol, price, "手動平倉", is_manual=True)
    if success:
        return {"status": "success", "message": f"手動平倉 {symbol}"}
    if symbol in account.closing_lock or symbol not in account.positions:
        return {"status": "closing", "message": f"{symbol} 平倉處理中"}
    raise HTTPException(status_code=502, detail="平倉委託失敗，請稍後再試")


@app.post("/api/reset_account")
async def reset_account():
    """清空帳戶所有狀態（損益、交易記錄、持倉），重新從初始餘額開始。"""
    if hasattr(engine.account, "reset_state"):
        engine.account.reset_state()
        return {"status": "success", "message": "帳戶已重置，損益與交易記錄已清空"}
    raise HTTPException(status_code=501, detail="此帳戶類型不支援重置")


async def _load_klines(symbol: str, timeframe: str, limit: int, include_live: bool):
    """取得K線、均線與策略使用的 Keltner Channel 提供給前端圖表"""
    try:
        df = await engine.fetch_klines(
            symbol, timeframe=timeframe, limit=limit, keep_live=include_live
        )
        if df.empty:
            raise HTTPException(status_code=400, detail="無法獲取 K 線資料")
            
        # 計算 MA
        df['MA3'] = df['close'].rolling(window=3).mean()
        df['MA15'] = df['close'].rolling(window=15).mean()
        df['MA99'] = df['close'].rolling(window=99).mean()
        indicators = engine.strategy.compute_indicators(df)
        trade_markers = {}
        for trade in engine.account.trades:
            if trade.get("symbol") != symbol:
                continue
            action = str(trade.get("action") or "")
            if action not in ("OPEN_LONG", "OPEN_SHORT", "CLOSE_LONG", "CLOSE_SHORT"):
                continue
            trade_timestamp = int(trade.get("id") or 0)
            matching_bars = df.index[df["timestamp"] <= trade_timestamp]
            if len(matching_bars) == 0:
                continue
            marker_index = matching_bars[-1]
            trade_markers.setdefault(marker_index, []).append({
                "action": action,
                "reason": trade.get("reason") or "",
                "price": trade.get("price"),
            })
        
        prealert = engine.pivot_prealerts.get(symbol, {})
        if prealert and time.time() - float(prealert.get("updated_at") or 0.0) < 180.0:
            matching_bars = df.index[df["timestamp"] <= int(prealert.get("timestamp") or 0)]
            if len(matching_bars) > 0:
                trade_markers.setdefault(matching_bars[-1], []).append({
                    "action": prealert.get("action"),
                    "reason": "1m pivot pre-alert; no order sent",
                    "price": None,
                })

        chop_markers = {}
        for event in engine._channel_chop_events.get(symbol, []):
            event_timestamp = int(event.get("timestamp") or 0)
            matching_bars = df.index[df["timestamp"] <= event_timestamp]
            if len(matching_bars) == 0:
                continue
            chop_markers.setdefault(matching_bars[-1], []).append({
                "action": event.get("action"),
                "reason": event.get("reason") or "",
                "timestamp": event_timestamp,
            })

        channel_markers = {}
        for event in engine._channel_signal_events.get(symbol, []):
            event_timestamp = int(event.get("timestamp") or 0)
            matching_bars = df.index[df["timestamp"] <= event_timestamp]
            if len(matching_bars) == 0:
                continue
            channel_markers.setdefault(matching_bars[-1], []).append({
                "action": event.get("action"),
                "reason": event.get("reason") or "",
                "label": event.get("label") or event.get("reason") or "",
                "timestamp": event_timestamp,
            })

        # 準備資料
        result = []
        for index, row in df.iterrows():
            # TradingView 需要的 time 是 unix timestamp (seconds)
            time_sec = int(row['timestamp'] / 1000)
            result.append({
                "time": time_sec,
                "open": row['open'],
                "high": row['high'],
                "low": row['low'],
                "close": row['close'],
                "ma3": None if pd.isna(row['MA3']) else row['MA3'],
                "ma15": None if pd.isna(row['MA15']) else row['MA15'],
                "ma99": None if pd.isna(row['MA99']) else row['MA99'],
                "kc_upper": None if pd.isna(indicators.loc[index, 'kc_upper']) else indicators.loc[index, 'kc_upper'],
                "kc_middle": None if pd.isna(indicators.loc[index, 'ema_20']) else indicators.loc[index, 'ema_20'],
                "kc_lower": None if pd.isna(indicators.loc[index, 'kc_lower']) else indicators.loc[index, 'kc_lower'],
                "trade_markers": trade_markers.get(index, []),
                "chop_markers": chop_markers.get(index, []),
                "channel_markers": channel_markers.get(index, []),
                "is_live": bool(include_live and index == df.index[-1]),
            })
            
        return {"symbol": symbol, "timeframe": timeframe, "data": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/klines")
async def get_klines(
    response: Response,
    symbol: str,
    timeframe: str = "5m",
    limit: int = 200,
    include_live: bool = False,
):
    """圖表 K 線快取與 single-flight；圖表故障不得拖慢交易資料流。"""
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    if timeframe not in {"1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"}:
        raise HTTPException(status_code=400, detail="不支援的 K 線週期")
    limit = max(20, min(int(limit), 500))
    key = (symbol, timeframe, limit, bool(include_live))
    now = time.monotonic()
    cached = _kline_cache.get(key)
    if cached and now - cached["saved_at"] < KLINE_CACHE_TTL_SECONDS:
        return {
            **cached["payload"],
            "cache": {"source": "cache", "age_seconds": round(now - cached["saved_at"], 2)},
        }

    task = _kline_inflight.get(key)
    if task is None:
        task = asyncio.create_task(_load_klines(symbol, timeframe, limit, include_live))
        _kline_inflight[key] = task
    try:
        payload = await asyncio.shield(task)
        saved_at = time.monotonic()
        _kline_cache[key] = {"payload": payload, "saved_at": saved_at}
        return {**payload, "cache": {"source": "live", "age_seconds": 0.0}}
    except Exception:
        cached = _kline_cache.get(key)
        if cached:
            age = time.monotonic() - cached["saved_at"]
            return {
                **cached["payload"],
                "cache": {"source": "stale", "age_seconds": round(age, 2)},
            }
        raise
    finally:
        if task.done() and _kline_inflight.get(key) is task:
            _kline_inflight.pop(key, None)
