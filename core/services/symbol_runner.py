"""Live entries and indicator refresh; held exits use the shared tick policy."""
import math
import time
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

    # ── [SPATIAL_DIAGNOSTIC] 前導空間感知與微結構診斷 ──
    try:
        from core.intelligence.spatial_brain import SpatialBrain
        import logging
        logger = logging.getLogger("uvicorn.error")
        spatial_ctx, spatial_act = SpatialBrain.diagnose(symbol, frame, quote)
        from core.gates.pipeline import EntryGatePipeline
        explosive_direction_override = (
            EntryGatePipeline.has_explosive_bullish_kc_breakout(frame, quote)
            or EntryGatePipeline.has_explosive_bearish_kc_breakout(frame, quote)
        )
        bearish_chop_breakout_override = (
            EntryGatePipeline.has_bearish_chop_breakout_override(frame, quote)
        )
        ai_chop_breakout_override = (
            EntryGatePipeline.has_strong_bullish_breakout(frame, quote)
            or EntryGatePipeline.has_strong_bearish_breakout(frame, quote)
            or bearish_chop_breakout_override
        )
        if spatial_ctx.state == 'CHOP_COMPRESSION' and ai_chop_breakout_override:
            spatial_act = 'ALLOW_CHOP_BREAKOUT_OVERRIDE'
        spatial_log = (
            f"[SPATIAL_DIAGNOSTIC] Symbol: {symbol} | "
            f"Context: state={spatial_ctx.state}, bw_ratio={spatial_ctx.bandwidth_ratio:.2f}, "
            f"chop={spatial_ctx.chop_index:.2f}, solidity={spatial_ctx.solidity_ratio:.2f}, "
            f"ma15_slope={spatial_ctx.ma15_slope:.4f} | Action: {spatial_act} | "
            f"ai_chop_breakout_override={ai_chop_breakout_override} | "
            f"explosive_unknown_direction_override={explosive_direction_override}"
        )
        logger.info(spatial_log)
        print(spatial_log, flush=True)
    except Exception as e:
        pass

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
            
        position["unrealized_pnl"] = unrealized_pnl
        if unrealized_pnl > position.get("max_pnl_usdt", 0.0):
            position["max_pnl_usdt"] = unrealized_pnl
    if not position:
        if exit_only:
            return [], []

        # ──【冷卻期檢查 (平倉後至少間隔 3 根 K 棒 / 180 秒才能開新倉，防止原地再開)】──
        curr_ts = float(frame.iloc[-1].get('timestamp', time.time() * 1000))
        last_closed_ts = 0.0
        last_closed_raw = getattr(engine.account, 'last_closed_at', {}).get(symbol)
        if last_closed_raw:
            last_closed_ts = float(last_closed_raw) * 1000.0 if float(last_closed_raw) < 1e11 else float(last_closed_raw)
        for tr in reversed(getattr(engine.account, 'trades', [])):
            if tr.get('symbol') == symbol and str(tr.get('action', '')).startswith('CLOSE_'):
                tr_ts = float(tr.get('timestamp') or tr.get('id') or 0.0)
                if tr_ts > 0:
                    if tr_ts < 1e11:
                        tr_ts *= 1000.0
                    if tr_ts > last_closed_ts:
                        last_closed_ts = tr_ts
                break

        COOLDOWN_MS = 180_000.0  # 3 根 1m K 棒 = 180,000 ms
        if last_closed_ts > 0 and (curr_ts - last_closed_ts) < COOLDOWN_MS:
            cooldown_left_sec = max(0, int((COOLDOWN_MS - (curr_ts - last_closed_ts)) / 1000))
            from core.services.candle_data import log_entry_gate
            log_entry_gate(engine, symbol, 'NONE', 'SIGNAL', f'WAIT_EXIT_COOLDOWN_3_BARS_{cooldown_left_sec}S_LEFT',
                           float(frame.iloc[-1]['timestamp']))
            return [], []

        from core.services.entry_contract import evaluate_entry_contract
        from core.services.candle_data import log_entry_gate
        diagnostics = {}
        entry = evaluate_entry_contract(
            frame, quote, account=engine.account, symbol=symbol,
            diagnostics=diagnostics,
        )
        if entry:
            auth_log = f"[AUTHORIZED_BY_ENTRY_CONTRACT] Symbol: {symbol} | Type: {entry['type']} | Side: {entry['side']} | Price: {quote}"
            logger.info(auth_log)
            print(auth_log, flush=True)
            await engine._execute_confirmed_channel_break(
                symbol, frame, quote, entry['side'], daily_halt,
                v8_reason=entry['type'], candidate_bar_id=entry['confirmation_bar_id'],
                qualification_signal_id=entry.get('qualification_signal_id'))
        else:
            log_entry_gate(engine, symbol, 'NONE', 'SIGNAL', diagnostics['reason'],
                           float(frame.iloc[-1]['timestamp']))
        return [], []

    # ══════════════════════════════════════════════════════════════════════
    # 【情況 B / C：當前「有持倉」 (IN_LONG / IN_SHORT) - 只判斷鎖利與真峰谷平倉】
    # ══════════════════════════════════════════════════════════════════════
    # 嚴格狀態機：持倉中所有開倉函數全部直接略過，絕不重複發送開倉訊號！
    from core.services.exits.realtime_profit_exit import enforce_realtime_profit_exit
    await enforce_realtime_profit_exit(engine, symbol, quote)

    if symbol not in engine.account.positions:
        strict_strategy.set_state(symbol, PositionState.IDLE)
        # 平倉完成後，直接結束本次 tick 處理，進入嚴格冷卻期，絕對禁止同一根 K 棒內原地反手或重開！
        if hasattr(engine.account, 'last_closed_at'):
            engine.account.last_closed_at[symbol] = time.time()
        return [], []

    return [], []
