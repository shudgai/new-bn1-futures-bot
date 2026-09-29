"""One close-only strategy lifecycle, serialized by the engine's symbol lock."""
import copy
import math
from core.services.strategies.unified_entry_strategy import confirmed
from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2
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
        
        from core.services.exits.hard_stop_service import enforce_hard_stop
        from core.services.exits.dual_track_exit_service import DualTrackExitStrategy, POLICY
        if await enforce_hard_stop(engine.account, symbol, quote):
            return [], []
        # Shared adapter enforces hard stops and retries valid persisted exits.
        adapter = DualTrackExitStrategy()
        exit_reason = adapter.evaluate_exit(position, current_price=quote)
        if exit_reason is None:
            snapshot = frame.iloc[-1].to_dict()
            snapshot['atr'] = float(closed.iloc[-1]['atr'])
            btc_status = {'is_crashing': now_time < getattr(engine, '_btc_swing_crash_until', 0.)}
            exit_reason = PureTrendStrategyV2().check_intra_bar_emergency_exit(position, quote, snapshot, btc_status)
        if exit_reason is None:
            exit_reason = adapter.evaluate_exit(position, closed, current_price=quote)
        if exit_reason:
            position['closed_exit_state'] = dict(policy=POLICY, pending=True, reason=exit_reason)
        state = position.get('closed_exit_state')
        if state is not None and engine.account.position_meta.setdefault(symbol, {}).get('closed_exit_state') != state:
            engine.account.position_meta[symbol]['closed_exit_state'] = copy.deepcopy(state)
            engine.account.save_state()

        if not exit_reason:
            return [], []
            
        old_side = position['side']
        # ── 全倉平倉 ─────────────────        # ── 全倉平倉（第三階段各種出場訊號）─────────────────
        filled = await engine.account.close_position(symbol, quote, 'Closed1M ' + exit_reason, is_manual=True)
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
    from core.services.strategies.pure_trend_v2 import successful_exit_ticket
    ticket = successful_exit_ticket(engine.account, symbol)
    if ticket and int(float(frame.iloc[-1]['timestamp']) // 60000) - ticket['exit_bar_index'] < 2:
        for side in ('LONG', 'SHORT'):
            log_entry_gate(engine, symbol, side, 'CLOSED_SIGNAL', 'WAIT_POST_EXIT_2_BAR_COOLDOWN', float(frame.iloc[-1]['timestamp']))
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
            
        from core.services.strategies.pure_trend_v2 import evaluate_v2_frame
        decision = evaluate_v2_frame(frame, quote, account=engine.account, symbol=symbol)

        if decision and decision['side'] == side:
            decision['rule'] = decision['type']
            log_entry_gate(engine,symbol,side,'CLOSED_SIGNAL',decision['reason'],decision['confirmation_bar_id'], snapshot=entry_frame_evidence(frame))
            candidates.append(decision)
        else:
            log_entry_gate(engine,symbol,side,'CLOSED_SIGNAL',"WAIT_PURE_TREND_V2",float(closed.iloc[-1].timestamp), snapshot=entry_frame_evidence(frame))

    if candidates:
        decision = min(candidates,key=lambda d:d['rule'])
        await engine._execute_confirmed_channel_break(symbol,frame,quote,decision['side'],
                                                      daily_halt,v8_reason=decision['type'],
                                                      candidate_bar_id=decision['confirmation_bar_id'])
    return [], []
