"""Persist successful profit-exit evidence and reject premature same-side reentry."""
import math
import time

LOCK_TRIGGERS = frozenset({
    'PROFIT_LOCK_T1', 'PROFIT_LOCK_T2', 'PROFIT_LOCK_T3',
    'PROFIT_LOCK_SELL_PRESSURE', 'EXIT_DOJI_BEARISH_CONFIRMATION',
    'EXIT_DOJI_BULLISH_CONFIRMATION', 'LONG_EXIT_OVERBOUGHT_EXTREME_RATCHET',
    'LONG_EXIT_STRUCTURE_DEFENSE', 'SHORT_EXIT_OVERSOLD_WICK_REJECTION',
    'SHORT_EXIT_MID_BREAKOUT_STOP', 'BAND_WALK_LONG_RISK_EXIT',
    'TIERED_RATCHET_TP_STAGE_1',
    'TIERED_RATCHET_TP_STAGE_2', 'TIERED_RATCHET_TP_STAGE_3',
})


def profit_exit_fields(position, reason, timestamp_ms, net_pnl=None):
    state = position.get('peak_trailing_state') or {}
    trigger = state.get('trigger', '')
    is_lock = trigger in LOCK_TRIGGERS and trigger in str(reason)
    is_pivot_profit = ('THREE_POINT_PIVOT' in str(reason)
                       and float(position.get('current_net_pnl_usd') or 0 if net_pnl is None else net_pnl) > 0)
    if not (is_lock or is_pivot_profit):
        return {}
    peak = float(state.get('peak_price') or 0)
    if not math.isfinite(peak) or peak <= 0:
        peak = 0.  # Persist invalid evidence so reentry fails closed.
    return dict(last_profit_exit_timestamp=float(timestamp_ms),
                last_profit_exit_side=position['side'],last_profit_exit_peak_price=peak)


def post_profit_lock_reason(account, symbol, closed, side, now_ms=None):
    events = [t for t in getattr(account,'trades',[]) if t.get('symbol') == symbol
              and t.get('status') == 'CLOSED' and t.get('action') == 'CLOSE_'+side
              and t.get('last_profit_exit_side') == side]
    # Older profitable pivot closes lacked state fields. Do not silently reopen.
    for trade in getattr(account, 'trades', []):
        if (trade.get('symbol') == symbol and trade.get('status') == 'CLOSED'
                and trade.get('action') == 'CLOSE_'+side
                and not trade.get('last_profit_exit_side')
                and 'THREE_POINT_PIVOT' in str(trade.get('reason', ''))
                and float(trade.get('pnl') or 0) > 0):
            events.append(dict(trade, last_profit_exit_timestamp=trade.get('id'),
                               last_profit_exit_peak_price=0))
    if not events:
        return None
    try:
        event=max(events,key=lambda t:float(t['last_profit_exit_timestamp']))
        exited=float(event['last_profit_exit_timestamp'])
        peak=float(event['last_profit_exit_peak_price'])
        now=float(time.time()*1000 if now_ms is None else now_ms)
        if not all(math.isfinite(v) and v > 0 for v in (exited,now)):
            return 'BLOCKED_BY_POST_PROFIT_INVALID_STATE'
        elapsed=now-exited
        if elapsed < 240000:
            return 'BLOCKED_BY_POST_PROFIT_COOLDOWN'
        if elapsed >= 900000:
            return None
        if not math.isfinite(peak) or peak <= 0:
            return 'BLOCKED_BY_POST_PROFIT_INVALID_STATE'
        if closed is None or closed.empty:
            return 'BLOCKED_BY_POST_PROFIT_INVALID_STATE'
        for _, bar in closed.iterrows():
            if not all(math.isfinite(float(bar[k])) and float(bar[k]) > 0
                       for k in ('timestamp','close','kc_middle')):
                return 'BLOCKED_BY_POST_PROFIT_INVALID_STATE'
        sign=1 if side=='LONG' else -1
        for i in range(1,len(closed)):
            prior,curr=closed.iloc[i-1],closed.iloc[i]
            if (float(curr['timestamp']) >= exited
                    and sign*(float(prior['close'])-float(prior['kc_middle'])) >= 0
                    and sign*(float(curr['close'])-float(curr['kc_middle'])) < 0):
                return None
        curr=closed.iloc[-1]
        if sign*(float(curr['close'])-peak) <= 0:
            return 'BLOCKED_BY_PEAK_EXHAUSTION_GATE' if side=='LONG' else 'BLOCKED_BY_TROUGH_EXHAUSTION_GATE'
        return None
    except (KeyError,TypeError,ValueError,IndexError,OverflowError):
        return 'BLOCKED_BY_POST_PROFIT_INVALID_STATE'


def confirmed_full_close(order, quantity):
    try:
        status = str(order.get('status') or (order.get('info') or {}).get('status') or '').upper()
        filled = float(order.get('filled') if order.get('filled') is not None else (order.get('info') or {}).get('executedQty', 0))
        return status in ('CLOSED','FILLED') and math.isfinite(filled) and filled >= float(quantity) * (1-1e-9)
    except (AttributeError,TypeError,ValueError,OverflowError):
        return False
