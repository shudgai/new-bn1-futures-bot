"""Per-symbol cycle: fetch candles, then delegate every entry/exit decision to core.swing."""
import math
import logging

logger = logging.getLogger("uvicorn.error")


async def process_single_symbol_runner(engine, symbol, now_time, btc_1m_turn, daily_halt,
                                       exit_frame=None, exit_quote=None, exit_only=False):
    from core.swing.runner import process_symbol

    # 1m everywhere: the entry firewall re-validates on settled 1m candles.
    frame = exit_frame
    if frame is None:
        frame = await engine.fetch_klines(symbol, timeframe="1m", limit=150, keep_live=True)
        if frame is not None and not frame.empty:
            frame = engine.strategy.compute_indicators(frame.copy())
    if frame is None or frame.empty:
        return [], []

    quote = float(
        exit_quote if exit_quote is not None
        else (getattr(engine, "tickers", {}).get(symbol) or frame.iloc[-1]["close"])
    )
    if not math.isfinite(quote) or quote <= 0:
        return [], []
    if daily_halt and symbol not in engine.account.positions:
        return [], []
    return await process_symbol(engine, symbol, frame, quote, daily_halt, exit_only)
