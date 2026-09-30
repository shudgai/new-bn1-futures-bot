"""Observed-tick profit protection; no candle fetch or scan-lock dependency."""
import copy
import math
import time

from core.services.candle_data import closed_entry_candles
from core.services.exits.dual_track_exit_service import DUAL_TRACK_STATE_KEYS, POLICY
from core.services.exits.hard_stop_service import enforce_hard_stop
from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2


def cached_tick_indicators(frame, price, stamp):
    """Only fresh confirmed inputs; missing candles never stop peak protection."""
    snapshot = {'quote_ms': stamp}
    if frame is None or frame.empty or frame.attrs.get('timeframe_ms', 60000) != 60000:
        return snapshot, 0.
    closed = closed_entry_candles(frame)
    bar = math.floor(stamp / 60000) * 60000
    if closed.empty or float(closed.iloc[-1]['timestamp']) != bar - 60000:
        return snapshot, 0.
    previous = closed.iloc[-1]
    for key in ('kc_middle', 'kc_upper', 'kc_lower'):
        snapshot[key] = previous.get(key, 0.)
    if len(closed) >= 2 and float(closed.iloc[-2]['timestamp']) == bar - 120000:
        snapshot['live_ma3'] = (float(closed.iloc[-2]['close']) + float(previous['close']) + price) / 3
    return snapshot, float(previous.get('atr') or 0.)


async def enforce_realtime_profit_exit(engine, symbol, price, quote_ms=None):
    if not getattr(engine, 'is_running', False):
        return False
    account = engine.account
    position = account.positions.get(symbol)
    if not position or position.get('entry_mode') != 'CHANNEL_SWING':
        return False
    try:
        price = float(price)
        stamp = float(quote_ms) if quote_ms is not None else time.time() * 1000
        if (not math.isfinite(price) or price <= 0 or not math.isfinite(stamp)
                or not 0 <= time.time() - stamp / 1000 <= 5):
            return False
        identity = [position['side'], float(position['open_timestamp']),
                    float(position['entry_price']), abs(float(position.get('qty', position.get('quantity', 0))))]
        meta = account.position_meta.setdefault(symbol, {})
        saved = meta.get('instant_exit_state') or {}
        if 'instant_exit_state' not in position and saved.get('identity') == identity:
            position['instant_exit_state'] = copy.deepcopy(saved)
            if 'closed_exit_state' not in position and 'closed_exit_state' in meta:
                position['closed_exit_state'] = copy.deepcopy(meta['closed_exit_state'])
        state = position.get('instant_exit_state') or {}
        if stamp < identity[1] * 1000 or (state.get('identity') == identity and stamp < state.get('last_ms', 0)):
            return False
        try:
            snapshot, atr = cached_tick_indicators(getattr(engine, '_channel_exit_frames', {}).get(symbol), price, stamp)
        except (KeyError, TypeError, ValueError, OverflowError, IndexError):
            snapshot, atr = {'quote_ms': stamp}, 0.
        decision = PureTrendStrategyV2().evaluate_anti_whipsaw_profit_lock(position, price, snapshot, atr)
        reason = decision['type'] if decision else None
        # Initial ATR stop also runs on aggTrade, even while the scan is blocked.
        stop = float(position.get('stop_loss') or position.get('sl') or 0.)
        if not stop and float(position.get('entry_atr') or 0.) > 0:
            stop = identity[2] - (1 if identity[0] == 'LONG' else -1) * 1.5 * float(position['entry_atr'])
        if math.isfinite(stop) and stop > 0 and (price-stop)*(1 if identity[0]=='LONG' else -1) <= 0:
            reason = 'EXIT_INITIAL_ATR_HARD_STOP'
        pending = position.get('closed_exit_state') or {}
        if pending.get('pending') and pending.get('reason') == 'EXIT_INITIAL_ATR_HARD_STOP':
            reason = pending['reason']
        if reason:
            position['closed_exit_state'] = dict(policy=POLICY, pending=True, reason=reason)
        # Save only changed observations, not every timestamp in the feed.
        old = meta.get('instant_exit_state') or {}
        current = position.get('instant_exit_state') or {}
        changed = any(old.get(k) != current.get(k) for k in
                      ('identity', 'peak', 'trail_atr', 'outer_seen', 'pending'))
        for key in DUAL_TRACK_STATE_KEYS:
            if key in position:
                meta[key] = copy.deepcopy(position[key])
        if changed or reason:
            account.save_state()
        if await enforce_hard_stop(account, symbol, price):
            return True
        if not reason or account.positions.get(symbol) is not position:
            return False
        # Account-level closing_lock serializes competing ticker/trade/scan closes.
        account.log(f'REALTIME_EXIT symbol={symbol} reason={reason} quote_ms={stamp} '
                    f'price={price} peak_price={current.get("peak_price")} '
                    f'peak_pnl_usd={current.get("peak")} latency_ms={time.time()*1000-stamp:.1f}', 'INFO')
        await account.close_position(symbol, price, reason, is_manual=True)
        return True
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
