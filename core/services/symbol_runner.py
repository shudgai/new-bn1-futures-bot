"""Live entries and indicator refresh; held exits use the shared tick policy."""
import math
from core.services.strategies.strict_state_machine import StrictStateMachineStrategy, PositionState

# 全域單一策略實例
strict_strategy = StrictStateMachineStrategy()

async def process_single_symbol_runner(engine, symbol, now_time, btc_1m_turn, daily_halt,
                                      exit_frame=None, exit_quote=None, exit_only=False):
    # 1. 狀態與交易所同步（防重啟失步）
    position = engine.account.positions.get(symbol)
    if position:
        side = position.get("side")
        entry_price = float(position.get("entry_price", 0.0))
        # 維持帳戶中的最大利潤
        max_profit = float(position.get("max_pnl_usdt", 0.0))
        if side == "LONG":
            strict_strategy.set_state(symbol, PositionState.LONG, {"entry_price": entry_price, "max_pnl": max_profit})
        elif side == "SHORT":
            strict_strategy.set_state(symbol, PositionState.SHORT, {"entry_price": entry_price, "max_pnl": max_profit})
    else:
        strict_strategy.set_state(symbol, PositionState.IDLE)
        
    frame = exit_frame
    if frame is None:
        frame = await engine.fetch_klines(symbol, timeframe='1m', limit=200, keep_live=True)
        if frame is not None and not frame.empty:
            frame = engine.strategy.compute_indicators(frame.copy())
            
    if frame is None or frame.empty:
        return [], []
        
    if not hasattr(engine, '_channel_exit_frames'):
        engine._channel_exit_frames = {}
    engine._channel_exit_frames[symbol] = frame.copy()

    # ── Reversal shadow (read-only, 100% fail-open) ──────────────────────────
    try:
        from core.services.reversal_shadow_logger import record_reversal_shadow_candidates
        record_reversal_shadow_candidates(engine, symbol, frame)
    except Exception:
        pass
    # ────────────────────────────────────────────────────────────────────────

    quote = float(exit_quote if exit_quote is not None else
                  (getattr(engine, 'tickers', {}).get(symbol) or frame.iloc[-1]['close']))
                  
    if not math.isfinite(quote) or quote <= 0:
        return [], []
        
    if daily_halt and not position:
        return [], []

    unrealized_pnl = 0.0
    if position:
        entry_price = float(position.get("entry_price", 0.0))
        amount = float(position.get("amount", 0.0))
        if position.get("side") == "LONG":
            unrealized_pnl = (quote - entry_price) * amount
        elif position.get("side") == "SHORT":
            unrealized_pnl = (entry_price - quote) * amount
            
        # 同步更新引擎中的 pnl，方便後續讀取
        position["unrealized_pnl"] = unrealized_pnl
        if unrealized_pnl > position.get("max_pnl_usdt", 0.0):
            position["max_pnl_usdt"] = unrealized_pnl

    if not position:
        if exit_only:
            return [], []
        from core.services.entry_contract import evaluate_entry_contract
        from core.services.candle_data import log_entry_gate
        diagnostics = {}
        entry = evaluate_entry_contract(frame, quote, account=engine.account,
                                        symbol=symbol, diagnostics=diagnostics)
        if entry:
            await engine._execute_confirmed_channel_break(
                symbol, frame, quote, entry['side'], daily_halt,
                v8_reason=entry['type'], candidate_bar_id=entry['confirmation_bar_id'])
        else:
            log_entry_gate(engine, symbol, 'NONE', 'SIGNAL', diagnostics['reason'],
                           float(frame.iloc[-1]['timestamp']))
        return [], []

    # Scan and aggTrade/ticker share the same persistent exit authority.
    from core.services.exits.realtime_profit_exit import enforce_realtime_profit_exit
    await enforce_realtime_profit_exit(engine, symbol, quote)
    if symbol not in engine.account.positions:
        strict_strategy.set_state(symbol, PositionState.IDLE)
    return [], []
