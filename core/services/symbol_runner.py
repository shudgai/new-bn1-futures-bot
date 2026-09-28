"""One close-only strategy lifecycle, serialized by the engine's symbol lock."""
import copy
import math
from core.services.strategies.unified_entry_strategy import confirmed, evaluate_closed_entry, had_close
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy, DUAL_TRACK_STATE_KEYS
from core.services.candle_data import log_entry_gate, entry_frame_evidence


async def process_single_symbol_runner(engine, symbol, now_time, btc_1m_turn, daily_halt,
                                      exit_frame=None, exit_quote=None, exit_only=False):
    frame = exit_frame
    if frame is None:
        frame = await engine.fetch_klines(symbol,timeframe='1m',limit=200,keep_live=True)
        if frame is not None and not frame.empty:
            frame = engine.strategy.compute_indicators(frame.copy())
    closed = confirmed(frame)
    if closed is None:
        return [], []
    cache = getattr(engine,'_channel_exit_frames',None)
    if cache is None:
        cache = engine._channel_exit_frames = {}
    cache[symbol] = frame.copy()
    quote = float(exit_quote if exit_quote is not None else
                  (getattr(engine,'tickers',{}).get(symbol) or frame.iloc[-1]['close']))
    if not math.isfinite(quote) or quote <= 0:
        return [], []
    position = engine.account.positions.get(symbol)
    preferred = None
    if position:
        engine._take_over_manual_position(symbol,position)
        meta = engine.account.position_meta.setdefault(symbol,{})
        for key in DUAL_TRACK_STATE_KEYS:
            if key not in position and key in meta:
                position[key] = copy.deepcopy(meta[key])
        reason = DualTrackExitStrategy().evaluate_exit(position,frame,current_price=quote)
        observed = {k:copy.deepcopy(position[k]) for k in DUAL_TRACK_STATE_KEYS if k in position}
        if any(meta.get(k) != v for k,v in observed.items()):
            meta.update(observed)
            engine.account.save_state()
        if not reason:
            return [], []
        old_side = position['side']

        # ── 全倉平倉 ─────────────────        # ── 全倉平倉（第三階段各種出場訊號）─────────────────
        filled = await engine.account.close_position(symbol, quote, 'Closed1M ' + reason, is_manual=True)
        if not filled or symbol in engine.account.positions:
            return [], []  # pending state is persisted and retried
        engine.account.position_meta.pop(symbol, None)
        for name in ('_channel_outer_reentry_after_exit', '_channel_pending_reverse_bar', '_closed_bear_reverse_tickets'):
            getattr(engine, name, {}).pop(symbol, None)
        getattr(engine.account, 'channel_profit_reentries', {}).pop(symbol, None)
        engine.account.save_state()
        return [], []  # Closing never initiates an entry in the same lifecycle pass.
    elif exit_only:
        return [], []
    if daily_halt:
        return [], []
    # New entries are independently evaluated against the whitelist.
    sides = ('LONG', 'SHORT')
    candidates = []
    for side in sides:
        # ── BTC 聯動熔斷審查 (BTC Market Circuit Breaker) ──
        if btc_1m_turn == "SHORT" and side == "LONG":
            reason = "BLOCKED_BTC_DUMPING_FORBID_LONG"
            log_entry_gate(engine, symbol, side, 'CLOSED_SIGNAL', reason, float(closed.iloc[-1].timestamp))
            continue
        if btc_1m_turn == "LONG" and side == "SHORT":
            reason = "BLOCKED_BTC_PUMPING_FORBID_SHORT"
            log_entry_gate(engine, symbol, side, 'CLOSED_SIGNAL', reason, float(closed.iloc[-1].timestamp))
            continue
            
        ok, reason, decision = evaluate_closed_entry(frame,side,after_close=had_close(engine.account,symbol))
        log_entry_gate(engine,symbol,side,'CLOSED_SIGNAL',reason,float(closed.iloc[-1].timestamp), snapshot=entry_frame_evidence(frame))
        if ok:
            candidates.append(decision)
    if candidates:
        decision = min(candidates,key=lambda d:d['rule'])
        await engine._execute_confirmed_channel_break(symbol,frame,quote,decision['side'],
                                                      daily_halt,v8_reason=decision['reason'])
    return [], []
