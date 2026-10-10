"""Cached-candle abnormal-body protection without REST or scan-lock waits."""
import copy
import math
import time

from core.services.candle_data import closed_entry_candles
from core.services.exits.peak_trailing_exit import (
    STATE_KEY, STATE_KEYS, RETIRED_KEYS, migrate_peak_state, position_identity, DOJI_TRIGGER,
    channel_initial_stop_disabled, CHANNEL_SWING_EXIT_TRIGGERS,
    PIVOT_ONLY_CHANNEL_EXIT_TRIGGERS,
    estimated_display_net_pnl,
)
from core.services.exits.hard_stop_service import enforce_hard_stop
from core.services.exits.entry_atr_protection import (
    channel_strategy_exit_grace_active,
    clear_channel_strategy_exit_pending,
)
from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2

PROFIT_LOCK_STATE_KEY = 'three_tier_net_roe_lock_state'
PROFIT_LOCK_TRIGGER = 'NET_ROE_THREE_TIER_GIVEBACK'


def _closed_ma5_reclaim(position, snapshot):
    """A channel position exits on structure only after a completed 1m MA5 break."""
    history = snapshot.get('history_5') if isinstance(snapshot, dict) else None
    if not isinstance(history, list) or not history:
        return False
    bar = history[-1]
    try:
        close = float(bar.get('c') or 0.)
        ma5 = float(bar.get('ma5') or 0.)
        return close > ma5 if position.get('side') == 'SHORT' else close < ma5
    except (TypeError, ValueError):
        return False


def _same_color_ma5_pressure(position, snapshot):
    """Detect three consecutive directional bodies with a monotone MA5 slope."""
    history = snapshot.get('history_5') if isinstance(snapshot, dict) else None
    if not isinstance(history, list) or len(history) < 3:
        return False
    bars = history[-3:]
    try:
        short = position.get('side') == 'SHORT'
        directional_bodies = all(
            float(bar.get('c') or 0.) < float(bar.get('o') or 0.)
            if short else
            float(bar.get('c') or 0.) > float(bar.get('o') or 0.)
            for bar in bars
        )
        ma5 = [float(bar.get('ma5') or 0.) for bar in bars]
        monotone_ma5 = all(
            right < left for left, right in zip(ma5, ma5[1:])
        ) if short else all(
            right > left for left, right in zip(ma5, ma5[1:])
        )
        return directional_bodies and monotone_ma5 and all(value > 0 for value in ma5)
    except (TypeError, ValueError):
        return False


def _profit_lock_identity(position):
    side, opened, entry, qty = position_identity(position)
    return f'{side}|{opened:.9f}|{entry:.12g}|{qty:.12g}'


def _tiered_net_roe_floor(peak_net_roe_pct):
    peak = float(peak_net_roe_pct)
    reached = lambda value, edge: value >= edge or math.isclose(value, edge, rel_tol=1e-12)
    if reached(peak, 20.0):
        return peak * 0.85, 3
    if reached(peak, 15.0):
        return max(peak * 0.80, 12.0), 2
    if reached(peak, 10.0):
        return max(peak * 0.60, 6.0), 1
    return None, 0


def _evaluate_realtime_core_exit_gates(position, meta, price, stamp, snapshot, fee, slippage):
    """Evaluate closed doji reversal, KC baseline, MA5 guard, then net-ROE."""
    if not isinstance(snapshot, dict) or snapshot.get('reason') is not None:
        return None, False
    side = position.get('side')
    sign = 1 if side == 'LONG' else -1 if side == 'SHORT' else 0
    try:
        quote = float(price)
        mid = float(snapshot.get('live_kc_middle') or snapshot.get('kc_middle') or 0.)
        if not sign or not math.isfinite(quote) or quote <= 0:
            return None, False
        
        # Min Hold Bars: Do not allow soft structural exits in the first 1-2 bars
        # (This prevents intraday jitters from instantly closing a new position, 
        # unless it hits the hard stop-loss handled elsewhere).
        open_ms = float(position.get('open_timestamp') or 0.0) * 1000.0
        time_held_ms = stamp - open_ms
        is_early_hold_period = (time_held_ms < 120000)

        history = snapshot.get('history_5')
        closed_ms = float(snapshot.get('closed_bar_ms') or 0.)
        live_bar_ms = float(snapshot.get('live_bar_ms') or 0.)
        if (isinstance(history, list) and history
                and live_bar_ms > 0 and closed_ms == live_bar_ms - 60000
                and float(history[-1].get('ms') or 0.) == closed_ms):
            prev_bar = history[-1]
            po, ph, pl, pc = (float(prev_bar.get(k) or 0.) for k in ('o', 'h', 'l', 'c'))
            live_open = float(snapshot.get('live_open') or 0.)
            if po > 0 and ph > 0 and pl > 0 and pc > 0 and ph > pl and live_open > 0:
                doji_body_ratio = abs(pc - po) / (ph - pl + 1e-9)
                if doji_body_ratio <= 0.35 and not is_early_hold_period:
                    if side == 'LONG' and quote < live_open:
                        return 'REALTIME_DOJI_REVERSAL_TRIGGERED', False
                    if side == 'SHORT' and quote > live_open:
                        return 'REALTIME_DOJI_REVERSAL_TRIGGERED', False

        # TRUE SELLING / BUYING PRESSURE EXIT (Intra-bar)
        live_open = float(snapshot.get('live_open') or 0.)
        live_high = float(snapshot.get('live_high') or 0.)
        live_low = float(snapshot.get('live_low') or 0.)
        live_kc_upper = float(snapshot.get('live_kc_upper') or 0.)
        live_kc_lower = float(snapshot.get('live_kc_lower') or 0.)
        live_ma3 = float(snapshot.get('live_ma3') or 0.)
        if not is_early_hold_period and live_high > 0 and live_low > 0 and live_ma3 > 0 and live_kc_upper > 0 and live_kc_lower > 0:
            if side == 'LONG':
                cond_a = live_high >= live_kc_upper
                cond_b = (live_high - quote) / (live_high - live_low + 1e-9) >= 0.45
                cond_c = quote < live_ma3
                if (cond_a and cond_b) or cond_c:
                    return 'REALTIME_TRUE_SELLING_PRESSURE_EXIT', False
            elif side == 'SHORT':
                cond_a = live_low <= live_kc_lower
                cond_b = (quote - live_low) / (live_high - live_low + 1e-9) >= 0.45
                cond_c = quote > live_ma3
                if (cond_a and cond_b) or cond_c:
                    return 'REALTIME_TRUE_BUYING_PRESSURE_EXIT', False

        if not is_early_hold_period and math.isfinite(mid) and mid > 0 and ((sign > 0 and quote < mid) or (sign < 0 and quote > mid)):
            return 'GLOBAL_KC_MIDDLE_CROSS', False

        if isinstance(history, list) and history:
            bar = history[-1]
            bar_ms = float(bar.get('ms') or 0.)
            close = float(bar.get('c') or 0.)
            ma5 = float(bar.get('ma5') or 0.)
            if (bar_ms == closed_ms and live_bar_ms > 0
                    and closed_ms == live_bar_ms - 60000
                    and bar_ms > 0 and close > 0 and ma5 > 0
                    and math.isfinite(close) and math.isfinite(ma5)
                    and ((sign > 0 and close < ma5) or (sign < 0 and close > ma5))):
                return 'ONE_MINUTE_CLOSED_MA5_TREND_GUARD', False

        entry = float(position.get('entry_price') or 0.)
        qty = float(position.get('qty') or 0.)
        margin = float(position.get('margin') or 0.)
        if margin <= 0:
            leverage = float(position.get('leverage') or 0.)
            if leverage > 0:
                margin = entry * qty / leverage
        if not all(math.isfinite(value) and value > 0 for value in (entry, qty, margin)):
            return None, False
        net_pnl = estimated_display_net_pnl(entry, quote, qty, sign, fee, slippage)
        current_roe = net_pnl / margin * 100.0
        if not math.isfinite(current_roe):
            return None, False

        identity = _profit_lock_identity(position)
        state = position.get(PROFIT_LOCK_STATE_KEY) or meta.get(PROFIT_LOCK_STATE_KEY) or {}
        if state.get('identity') != identity:
            state = {'identity': identity, 'peak_net_roe_pct': current_roe,
                     'last_ms': stamp, 'tier': 0, 'floor_net_roe_pct': None}
        elif stamp < float(state.get('last_ms') or 0.):
            return None, False
        else:
            state = copy.deepcopy(state)
            state['peak_net_roe_pct'] = max(
                current_roe, float(state.get('peak_net_roe_pct', current_roe)),
            )
            state['last_ms'] = stamp
        floor, tier = _tiered_net_roe_floor(state['peak_net_roe_pct'])
        state.update(tier=tier, floor_net_roe_pct=floor,
                     current_net_roe_pct=current_roe)
        position[PROFIT_LOCK_STATE_KEY] = copy.deepcopy(state)
        meta[PROFIT_LOCK_STATE_KEY] = copy.deepcopy(state)
        if floor is not None and (
                current_roe <= floor
                or math.isclose(current_roe, floor, rel_tol=1e-12)):
            return f'{PROFIT_LOCK_TRIGGER}_TIER_{tier}', True
        return None, True
    except (KeyError, TypeError, ValueError, OverflowError):
        return None, False


def cached_tick_indicators(frame, price, stamp):
    """Require the quote minute and its preceding closed ATR for body exits."""
    snapshot = {'quote_ms': stamp, 'reason': 'UNKNOWN'}
    if frame is None or frame.empty or frame.attrs.get('timeframe_ms', 60000) != 60000:
        snapshot['reason'] = 'NO_DATA'
        return snapshot, 0.
    closed = closed_entry_candles(frame)
    if len(closed) < 2:
        snapshot['reason'] = 'NO_DATA'
        return snapshot, 0.

    # Extract up to 5 last closed bars for mature reversal rehydration
    history_bars = []
    for _, b in closed.iloc[-5:].iterrows():
        history_bars.append({
            'ms': float(b.get('timestamp', 0)),
            'o': float(b.get('open', 0)),
            'h': float(b.get('high', 0)),
            'l': float(b.get('low', 0)),
            'c': float(b.get('close', 0)),
            'ma3': float(b.get('ma3', 0)),
            'ma5': float(b.get('ma5', b.get('ma3', 0))),
            'atr': float(b.get('atr', 0)),
            'kc_upper': float(b.get('kc_upper', 0)),
            'kc_middle': float(b.get('kc_middle', 0)),
            'kc_lower': float(b.get('kc_lower', 0)),
        })
    snapshot['history_5'] = history_bars
    snapshot['ma5_history'] = [
        float(bar.get('ma5') or 0.) for _, bar in closed.tail(3).iterrows()
    ]
    snapshot['ma15_history'] = [
        float(bar.get('ma15') or 0.) for _, bar in closed.tail(3).iterrows()
    ]
    snapshot['history_outer_pivots'] = [
        {
            'ms': float(b.get('timestamp', 0)),
            'o': float(b.get('open', 0)),
            'h': float(b.get('high', 0)),
            'l': float(b.get('low', 0)),
            'c': float(b.get('close', 0)),
            'ma5': float(b.get('ma5', 0)),
            'volume': float(b.get('volume', 0)),
            'atr': float(b.get('atr', 0)),
            'kc_upper': float(b.get('kc_upper', 0)),
            'kc_middle': float(b.get('kc_middle', 0)),
            'kc_lower': float(b.get('kc_lower', 0)),
        }
        for _, b in closed.tail(120).iterrows()
    ]

    last = closed.iloc[-1]
    prev = closed.iloc[-2]
    last_ms = float(last.get('timestamp', 0))
    bar = math.floor(stamp / 60000) * 60000

    snapshot['snapshot_age'] = stamp - last_ms

    if stamp - last_ms > 300000:  # 5 minutes freshness
        snapshot['reason'] = 'STALE_SNAPSHOT'
        return snapshot, 0.

    snapshot.update(
        snapshot_bar_id=last_ms,
        live_bar_id=bar,
        ma3=float(last.get('ma3', 0.)),
        ma5=float(last.get('ma5', last.get('ma3', 0.))),
        ma15=float(last.get('ma15', 0.)),
        last_ma3=float(prev.get('ma3', 0.)),
        last_ma5=float(prev.get('ma5', prev.get('ma3', 0.))),
        last_ma15=float(prev.get('ma15', 0.)),
        last_close=float(last.get('close', 0.))
    )
    snapshot['reason'] = None

    if last_ms < bar - 60000:
        snapshot['snapshot_source'] = 'CLOSED_BAR_FALLBACK'
        snapshot['fallback_used'] = True
    else:
        snapshot['snapshot_source'] = 'CLOSED_BAR_SYNCED'
        snapshot['fallback_used'] = False

    live = frame.iloc[-1]
    live_ms = float(live.get('timestamp', 0))

    if live_ms == bar and not bool(live.get('is_closed', True)) and last_ms == bar - 60000:
        raw_close = float(live.get('close') or 0.)
        snapshot.update(
            live_bar_ms=bar,
            closed_bar_ms=last_ms,
            atr=float(last.get('atr') or 0.),
            live_open=float(live.get('open') or 0.),
            live_high=max(float(live.get('high') or 0.), float(price)),
            live_low=min(float(live.get('low') or 0.), float(price)),
            live_price=float(price),
            live_ma3=(float(last.get('ma3') or 0.) + (float(price) - float(last.get('close') or 0.)) / 3.0),
            live_kc_upper=float(live.get('kc_upper') or 0.),
            live_kc_middle=float(live.get('kc_middle') or last.get('kc_middle') or 0.),
            live_kc_lower=float(live.get('kc_lower') or 0.),
            last_open=float(last.get('open') or 0.),
            last_high=float(last.get('high') or 0.),
            last_low=float(last.get('low') or 0.)
        )
        ma5_bars = closed.tail(5)
        ma5_closes = [float(value) for value in ma5_bars['close']]
        ma5_timestamps = [float(value) for value in ma5_bars['timestamp']]
        if (len(ma5_closes) == 5
                and all(math.isfinite(value) and value > 0 for value in ma5_closes)
                and all(math.isfinite(value) and value > 0 for value in ma5_timestamps)
                and all(right - left == 60000
                        for left, right in zip(ma5_timestamps, ma5_timestamps[1:]))
                and math.isfinite(float(price)) and float(price) > 0):
            snapshot.update(
                closed_ma5=sum(ma5_closes) / 5.0,
                live_ma5=(sum(ma5_closes[-4:]) + float(price)) / 5.0,
            )
    return snapshot, float(last.get('atr') or 0.)


def migrate_account_peak_exits(account):
    """Run before trading tasks, and clean both persisted copies of held state."""
    changed = False
    for symbol, position in account.positions.items():
        meta = account.position_meta.setdefault(symbol, {})
        entry_m = str(position.get('entry_mode') or meta.get('entry_mode') or '').upper()
        if entry_m in ('EXHAUSTION_SNIPER', 'PIVOT_TURN'):
            continue
        try:
            migrate_peak_state(position, meta)
            for key in STATE_KEYS:
                if key in position:
                    meta[key] = copy.deepcopy(position[key])
            changed = True
        except (KeyError, TypeError, ValueError, OverflowError):
            account.log(f'PEAK_EXIT_MIGRATION_INVALID symbol={symbol}', 'WARNING')
    if changed:
        account.save_state()


async def enforce_realtime_profit_exit(engine, symbol, price, quote_ms=None):
    if not getattr(engine, 'is_running', False):
        return False
    account = engine.account
    position = account.positions.get(symbol)
    entry_m = str((position or {}).get('entry_mode', '')).upper()
    if not position or entry_m in ('EXHAUSTION_SNIPER', 'PIVOT_TURN'):
        return False
    try:
        price = float(price)
        stamp = float(quote_ms) if quote_ms is not None else time.time()*1000
        if (not math.isfinite(price) or price <= 0 or not math.isfinite(stamp)
                or not 0 <= time.time()-stamp/1000 <= 5):
            return False
        ident = position_identity(position)
        meta = account.position_meta.setdefault(symbol, {})
        saved = position.get(STATE_KEY) or meta.get(STATE_KEY) or {}
        if stamp < ident[1]*1000 or (saved.get('identity') == ident and stamp < saved.get('last_ms',0)):
            return False
        if await enforce_hard_stop(account, symbol, price):
            return True

        try:
            frame = getattr(engine, '_channel_exit_frames', {}).get(symbol)
            if frame is not None and not frame.empty:
                snapshot, atr = cached_tick_indicators(frame, price, stamp)
                if entry_m == 'CHANNEL_SWING':
                    from core.services.exits.dual_track_exit_service import detect_climax_reversal
                    climax = detect_climax_reversal(frame, position.get('side'))
                    if climax:
                        account.log(
                            f"REALTIME_EXIT symbol={symbol} reason={climax['reason']} "
                            f"bar={climax['reversal_bar_id']} price={price} "
                            f"extension_atr={climax['extension_atr']:.3f} "
                            f"body_atr={climax['body_atr']:.3f}", 'WARNING',
                        )
                        await account.close_position(
                            symbol, price, 'Channel Swing ' + climax['reason'],
                            is_manual=True,
                        )
                        return True
                from core.config import SLIPPAGE_PCT, TAKER_FEE_RATE
                gate_trigger, state_changed = _evaluate_realtime_core_exit_gates(
                    position, meta, price, stamp, snapshot,
                    TAKER_FEE_RATE, SLIPPAGE_PCT,
                )
                if state_changed:
                    account.save_state()
                if gate_trigger:
                    if (entry_m != 'CHANNEL_SWING'
                            or gate_trigger == 'REALTIME_DOJI_REVERSAL_TRIGGERED'
                            or gate_trigger == 'ONE_MINUTE_CLOSED_MA5_TREND_GUARD'
                            or gate_trigger == 'REALTIME_TRUE_SELLING_PRESSURE_EXIT'
                            or gate_trigger == 'REALTIME_TRUE_BUYING_PRESSURE_EXIT'
                            or gate_trigger.startswith(PROFIT_LOCK_TRIGGER)):
                        account.log(
                            f'REALTIME_EXIT symbol={symbol} reason={gate_trigger} '
                            f'quote_ms={stamp} price={price}', 'WARNING',
                        )
                        await account.close_position(
                            symbol, price, f'Channel Swing {gate_trigger}', is_manual=True,
                        )
                        return True
                if entry_m == 'CHANNEL_SWING':
                    # Strategy exits are deliberately limited to a completed 1m
                    # MA5 reclaim/break or the existing net-ROE profit lock.
                    # This keeps doji, deceleration, live V-reversal and CK-side
                    # heuristics from closing a trend trade mid-wave.
                    if (gate_trigger in ('REALTIME_DOJI_REVERSAL_TRIGGERED',
                                         'ONE_MINUTE_CLOSED_MA5_TREND_GUARD',
                                         'REALTIME_TRUE_SELLING_PRESSURE_EXIT',
                                         'REALTIME_TRUE_BUYING_PRESSURE_EXIT')
                            or (gate_trigger and gate_trigger.startswith(PROFIT_LOCK_TRIGGER))):
                        pass
                    else:
                        guard = (
                            'HOLD_SAME_COLOR_MA5_PRESSURE'
                            if _same_color_ma5_pressure(position, snapshot)
                            else 'HOLD_WAIT_CLOSED_MA5_OR_NET_ROE_LOCK'
                        )
                        if meta.get('trend_exit_last_audit_bar') != snapshot.get('snapshot_bar_id'):
                            account.log(
                                f'TREND_EXIT_GATE symbol={symbol} side={position.get("side")} '
                                f'rule=STRUCTURE_OR_NET_ROE_ONLY reason={guard} '
                                f'closed_bar={snapshot.get("snapshot_bar_id")}', 'INFO',
                            )
                            meta['trend_exit_last_audit_bar'] = snapshot.get('snapshot_bar_id')
                            account.save_state()
                        return False
                if entry_m == 'CHANNEL_SWING':
                    from core.services.exits.peak_trailing_exit import v_reversal_short_exit_trigger
                    trigger = v_reversal_short_exit_trigger(position, price, snapshot)
                    if trigger:
                        account.log(
                            f'REALTIME_EXIT symbol={symbol} reason=V_REVERSAL_SHORT_FAST_CUT '
                            f'trigger={trigger} quote_ms={stamp} price={price} atr={atr}',
                            'WARNING',
                        )
                        await account.close_position(
                            symbol, price, 'Channel Swing V_REVERSAL_SHORT_FAST_CUT ' + trigger,
                            is_manual=True,
                        )
                        return True
        except (KeyError, TypeError, ValueError, OverflowError, IndexError):
            pass

        # Account hard stops ran before this section.  With a missing or
        # invalid 1m snapshot, wait for structure data to recover instead of
        # falling through to legacy CK, doji, or peak-trailing strategy exits.
        if entry_m == 'CHANNEL_SWING':
            account.log(
                f'TREND_EXIT_GATE symbol={symbol} side={position.get("side")} '
                'rule=STRUCTURE_OR_NET_ROE_ONLY reason=HOLD_EXIT_SNAPSHOT_UNAVAILABLE',
                'INFO',
            )
            return False
            
        try:
            from core.services.entry_contract import ck_direction
            frame = getattr(engine, '_channel_exit_frames', {}).get(symbol)
            if frame is not None and not frame.empty:
                ck_dir = ck_direction(frame)
                if ck_dir and ck_dir != ident[0]:
                    await account.close_position(
                        symbol, price, f'WRONG_DIRECTION_CORRECTION (CK={ck_dir}, POS={ident[0]})', is_manual=True
                    )
                    return True
        except Exception as e:
            account.log(f'CK direction check failed: {e}', 'DEBUG')
        old = copy.deepcopy(meta.get(STATE_KEY) or {})
        retired_atr_stop = channel_initial_stop_disabled(position, meta) and any(
            source.get(key) for source in (position, meta)
            for key in ('sl', 'stop_loss', 'atr_sl', 'initial_sl', 'initial_risk'))
        retired = any(key in source for source in (position,meta) for key in RETIRED_KEYS)
        migrate_peak_state(position, meta)
        try:
            snapshot, atr = cached_tick_indicators(getattr(engine,'_channel_exit_frames',{}).get(symbol),price,stamp)
        except (KeyError,TypeError,ValueError,OverflowError,IndexError):
            snapshot, atr = {'quote_ms':stamp}, 0.
        decision = PureTrendStrategyV2().evaluate_anti_whipsaw_profit_lock(position,price,snapshot,atr)
        current = position[STATE_KEY]
        changed = retired or retired_atr_stop or any(old.get(k) != current.get(k) for k in
                  ('identity','peak_price','peak_net_pnl','atr','armed','pending',
                   'trigger','trigger_bar_ms','trigger_price',
                   'net_roe_peak_pct','net_roe_lock_armed','net_roe_lock_floor_pct',
                   'ma5_reversal_extreme','ma5_reversal_last_value',
                   'ma5_reversal_last_price','ma5_reversal_favorable_seen',
                   'ma5_reversal_outside_seen'))
        for key in STATE_KEYS:
            if key in position:
                meta[key] = copy.deepcopy(position[key])
        if changed:
            account.save_state()
        if not decision or account.positions.get(symbol) is not position:
            return False
        reason = decision['type']
        trigger = decision.get('trigger', '')
        allowed_triggers = (
            PIVOT_ONLY_CHANNEL_EXIT_TRIGGERS
            if symbol in ('SUI/USDT', '龙虾/USDT', 'LOBSTER/USDT')
            else CHANNEL_SWING_EXIT_TRIGGERS
        )
        if (entry_m == 'CHANNEL_SWING'
                and channel_strategy_exit_grace_active(position, meta, stamp / 1000)
                and trigger not in allowed_triggers):
            if clear_channel_strategy_exit_pending(position, meta):
                for key in STATE_KEYS:
                    if key in position:
                        meta[key] = copy.deepcopy(position[key])
                account.save_state()
            return False
        
        if (entry_m == 'CHANNEL_SWING'
                and trigger not in allowed_triggers):
            for state in (
                current[STATE_KEY], meta.get(STATE_KEY, {}),
            ):
                for key in ('pending', 'trigger', 'trigger_bar_ms', 'trigger_open',
                            'trigger_atr', 'trigger_price', 'trigger_confirmed_ms'):
                    state.pop(key, None)
            for key in ('pending', 'trigger', 'trigger_bar_ms', 'trigger_open',
                        'trigger_atr', 'trigger_price', 'trigger_confirmed_ms'):
                position.pop(key, None)
                meta.pop(key, None)
            account.save_state()
            return False

        account.log(f'REALTIME_EXIT symbol={symbol} reason={reason} trigger={trigger} '
                    f'quote_ms={stamp} price={price} peak_price={current["peak_price"]} '
                    f'peak_net_pnl={current["peak_net_pnl"]} latency_ms={time.time()*1000-stamp:.1f}', 'INFO')
        trigger_detail = (
            ' ' + trigger
            if trigger in (DOJI_TRIGGER, 'EXIT_PROFIT_LOCK_FLOOR',
                           'NET_ROE_STAGED_GIVEBACK',
                           'EXIT_CONTINUATION_FAILED',
                           'EXIT_CONSECUTIVE_DOJI_STALL',
                           'LIVE_MA5_BREAKDOWN_EXIT', 'LIVE_FLASH_DUMP_EXIT',
                           'THREE_POINT_PIVOT', 'TWO_CLOSED_ADVERSE_ABNORMAL',
                           'WATERFALL_DROP', 'BEARISH_INSTANT_BREAKOUT',
                           'EXIT_DOJI_BEARISH_CONFIRMATION',
                           'EXIT_DOJI_BULLISH_CONFIRMATION',
                           'MA5_TURN_REVERSAL',
                           'MA5_TRUE_PEAK_REVERSAL',
                           'CHANNEL_PEAK_PULLBACK_REVERSAL', 'KC_CHANNEL_RETURN')
            else ''
        )
        await account.close_position(
            symbol, price, 'Channel Swing ' + reason + trigger_detail, is_manual=True
        )
        return True
    except (KeyError,TypeError,ValueError,OverflowError):
        return False
