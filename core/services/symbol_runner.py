"""Live entries and indicator refresh; held exits use the shared tick policy."""
import math
from core.services.strategies.unified_entry_strategy import confirmed
from core.services.candle_data import log_entry_gate, entry_frame_evidence


async def process_single_symbol_runner(engine, symbol, now_time, btc_1m_turn, daily_halt,
                                      exit_frame=None, exit_quote=None, exit_only=False):
    # Try the current held quote before REST or any entry candle validation.
    position = engine.account.positions.get(symbol)
    if position:
        from core.services.exits.realtime_profit_exit import enforce_realtime_profit_exit
        engine._take_over_manual_position(symbol, position)
        if exit_frame is not None and not exit_frame.empty:
            if not hasattr(engine, '_channel_exit_frames'):
                engine._channel_exit_frames = {}
            engine._channel_exit_frames[symbol] = exit_frame.copy()
        held_quote = exit_quote if exit_quote is not None else getattr(engine,'tickers',{}).get(symbol)
        if held_quote is not None and await enforce_realtime_profit_exit(engine,symbol,held_quote,now_time*1000):
            return [], []
        if exit_only:
            return [], []
    frame = exit_frame
    if frame is None:
        frame = await engine.fetch_klines(symbol,timeframe='1m',limit=200,keep_live=True)
        if frame is not None and not frame.empty:
            frame = engine.strategy.compute_indicators(frame.copy())
    if frame is not None and not frame.empty:
        if not hasattr(engine,'_channel_exit_frames'):
            engine._channel_exit_frames = {}
        engine._channel_exit_frames[symbol] = frame.copy()
    if position or symbol in engine.account.positions:
        # This scan refreshes indicator data only; it cannot authorize another exit.
        return [], []
    if exit_only:
        return [], []
    closed = confirmed(frame)
    if closed is None:
        return [], []
    quote = float(exit_quote if exit_quote is not None else
                  (getattr(engine,'tickers',{}).get(symbol) or frame.iloc[-1]['close']))
    if not math.isfinite(quote) or quote <= 0:
        return [], []
    if daily_halt:
        return [], []
    from core.services.strategies.pure_trend_v2 import successful_exit_ticket
    ticket = successful_exit_ticket(engine.account, symbol)
    # Delegate cooldown and continuation logic entirely to pure_trend_v2.py
    if ticket:
        pass
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
        diagnostics = {}
        decision = evaluate_v2_frame(frame, quote, account=engine.account, symbol=symbol, diagnostics=diagnostics)

        if decision and decision['side'] == side:
            decision['rule'] = decision['type']
            log_entry_gate(engine,symbol,side,'CLOSED_SIGNAL',decision['reason'],decision['confirmation_bar_id'], snapshot=entry_frame_evidence(frame))
            candidates.append(decision)
        else:
            log_entry_gate(engine,symbol,side,'CLOSED_SIGNAL',('訊號方向為' + decision['side'] if decision else diagnostics['reason']),float(frame.iloc[-1].timestamp), snapshot=entry_frame_evidence(frame))

    if candidates:
        decision = min(candidates,key=lambda d:d['rule'])
        await engine._execute_confirmed_channel_break(symbol,frame,quote,decision['side'],
                                                      daily_halt,v8_reason=decision['type'],
                                                      candidate_bar_id=decision['confirmation_bar_id'])
    return [], []
