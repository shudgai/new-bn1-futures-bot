"""One close-only strategy lifecycle, serialized by the engine's symbol lock."""
import copy
import math
from core.services.strategies.unified_entry_strategy import confirmed, evaluate_closed_entry, had_close
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy, DUAL_TRACK_STATE_KEYS
from core.services.candle_data import log_entry_gate


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
        reason = DualTrackExitStrategy().evaluate_exit(position,frame)
        observed = {k:copy.deepcopy(position[k]) for k in DUAL_TRACK_STATE_KEYS if k in position}
        if any(meta.get(k) != v for k,v in observed.items()):
            meta.update(observed)
            engine.account.save_state()
        if not reason:
            return [], []
        old_side = position['side']

        # ── 方案A TP1：部分平倉 50%，剩餘倉位繼續持有 ──────
        if reason == 'TP1_PARTIAL_CLOSE_50PCT':
            partial_ok = await engine.account.partial_close_position(
                symbol, quote, 'Closed1M TP1_PARTIAL_CLOSE_50PCT', fraction=0.50
            )
            if partial_ok:
                # 標記 state 已執行 TP1（position 仍在，closed_exit_state 會在下一根更新）
                pos_now = engine.account.positions.get(symbol, {})
                exit_state = pos_now.get('closed_exit_state', {})
                exit_state['tp1_executed'] = True
                exit_state['pending'] = None
                if 'closed_exit_state' in pos_now:
                    pos_now['closed_exit_state'] = exit_state
                engine.account.position_meta.setdefault(symbol, {}).update(
                    {'closed_exit_state': exit_state}
                )
                engine.account.save_state()
                engine.account.log(
                    f"💰 [TP1-50%] {symbol} {old_side} 已鎖利 50%，剩餘半倉移至保本線繼續運行",
                    "SUCCESS"
                )
            return [], []

        # ── 全倉平倉（第三階段各種出場訊號）─────────────────
        filled = await engine.account.close_position(symbol, quote, 'Closed1M ' + reason, is_manual=True)
        if not filled or symbol in engine.account.positions:
            return [], []  # pending state is persisted and retried
        engine.account.position_meta.pop(symbol, None)
        for name in ('_channel_outer_reentry_after_exit', '_channel_pending_reverse_bar', '_closed_bear_reverse_tickets'):
            getattr(engine, name, {}).pop(symbol, None)
        getattr(engine.account, 'channel_profit_reentries', {}).pop(symbol, None)
        engine.account.save_state()
        preferred = 'SHORT' if old_side == 'LONG' else 'LONG'
    elif exit_only:
        return [], []
    if daily_halt:
        return [], []
    # Highest rule priority wins across both directions. Opposite-side tie first after a close.
    sides = (preferred,'LONG' if preferred == 'SHORT' else 'SHORT') if preferred else ('LONG','SHORT')
    candidates = []
    for side in sides:
        ok, reason, decision = evaluate_closed_entry(frame,side,after_close=had_close(engine.account,symbol))
        log_entry_gate(engine,symbol,side,'CLOSED_SIGNAL',reason,float(closed.iloc[-1].timestamp))
        if ok:
            candidates.append(decision)
    if candidates:
        decision = min(candidates,key=lambda d:d['rule'])
        await engine._execute_confirmed_channel_break(symbol,frame,quote,decision['side'],
                                                      daily_halt,v8_reason=decision['reason'])
    return [], []
