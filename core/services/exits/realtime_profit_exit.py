from core.config import CHANNEL_WATERFALL_BODY_ATR
"""Cached-candle abnormal-body protection without REST or scan-lock waits."""
import copy
import math
import time

from core.services.candle_data import closed_entry_candles
from core.services.exits.peak_trailing_exit import (
    STATE_KEY, STATE_KEYS, RETIRED_KEYS, migrate_peak_state, position_identity, DOJI_TRIGGER, TERMINAL_DOJI_TRIGGER,
)
from core.services.exits.hard_stop_service import enforce_hard_stop
from core.services.exits.ma5_outer_pivot_exit import REASON as MA5_EXIT
from core.services.exits.trade_pressure_exit import REASON as PRESSURE_EXIT
from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2


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
            'ma5': float(b.get('ma5', b.get('ma3', 0)))
        })
    from core.services.exits.moving_profit_stop import confirmed_profit_pivots
    snapshot['profit_pivot_candidates'] = confirmed_profit_pivots(frame)
    from core.services.early_swing_reversal import evaluate_early_swing
    snapshot['early_swing_reversal'] = evaluate_early_swing(frame,price)
    snapshot['history_5'] = history_bars
    from core.services.closed_ma_cross import closed_cross_evidence
    snapshot['closed_ma_cross'] = closed_cross_evidence(frame)
    snapshot['kc_closed_history'] = [dict(timestamp=float(row['timestamp']), middle=float(row['kc_middle']))
                                     for _, row in closed.iloc[-4:].iterrows()] if 'kc_middle' in closed else []
    snapshot['closed_trend_history'] = [dict(timestamp=float(row['timestamp']),
            ma5=float(row.get('ma5') or 0.), close=float(row['close']), atr=float(row.get('atr') or 0.))
            for _, row in closed.iloc[-2:].iterrows()]
    snapshot['pivot_exit_history'] = [dict(timestamp=float(row['timestamp']),
            **{key: float(row[key]) for key in ('open','high','low','close')})
            for _, row in closed.iloc[-3:].iterrows()]
    ma5_keys = ('timestamp', 'open', 'high', 'low', 'close', 'ma5', 'kc_lower', 'kc_upper')
    snapshot['ma5_pivot_history'] = (
        [{key: float(row[key]) for key in ma5_keys} for _, row in closed.iloc[-3:].iterrows()]
        if all(key in closed for key in ma5_keys) else [])

    from core.services.exits.trend_hold_evaluator import confirmed_swing_structure
    snapshot['swing_structure_long'] = confirmed_swing_structure({'side': 'LONG'}, closed, price)
    snapshot['swing_structure_short'] = confirmed_swing_structure({'side': 'SHORT'}, closed, price)

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
        last_close=float(last.get('close', 0.)),
        kc_middle=float(last.get('kc_middle', 0.))
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
        # Reprice live moving averages; closed snapshots alone can mislabel a rebound.
        if len(closed) >= 3:
            snapshot['ma3'] = (sum(float(v) for v in closed.close.iloc[-2:]) + float(price)) / 3.
            snapshot['last_ma3'] = float(last.get('ma3', 0.))
        if len(closed) >= 5:
            snapshot['ma5'] = (sum(float(v) for v in closed.close.iloc[-4:]) + float(price)) / 5.
            snapshot['last_ma5'] = float(last.get('ma5', 0.))
        if len(closed) >= 15:
            snapshot['ma15'] = (sum(float(v) for v in closed.close.iloc[-14:]) + float(price)) / 15.
            snapshot['last_ma15'] = float(last.get('ma15', 0.))
        snapshot['kc_middle'] = float(live.get('kc_middle', last.get('kc_middle', 0.)))
        snapshot.update(
            kc_upper=float(live.get('kc_upper') or 0.),
            kc_lower=float(live.get('kc_lower') or 0.),
            live_bar_ms=bar,
            closed_bar_ms=last_ms,
            atr=float(last.get('atr') or 0.),
            live_open=float(live.get('open') or 0.),
            live_high=max(float(live.get('high') or 0.), float(price)),
            live_low=min(float(live.get('low') or 0.), float(price)),
            last_open=float(last.get('open') or 0.),
            last_high=float(last.get('high') or 0.),
            last_low=float(last.get('low') or 0.)
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
        if await enforce_hard_stop(account,symbol,price):
            return True
        evaluation_started = time.perf_counter()
        quote_age_at_start_ms = time.time()*1000-stamp
        old = copy.deepcopy(meta.get(STATE_KEY) or {})
        retired = any(key in source for source in (position,meta) for key in RETIRED_KEYS)
        migrate_peak_state(position, meta)
        try:
            snapshot, atr = cached_tick_indicators(getattr(engine,'_channel_exit_frames',{}).get(symbol),price,stamp)
        except (KeyError,TypeError,ValueError,OverflowError,IndexError):
            snapshot, atr = {'quote_ms':stamp}, 0.
        feed = getattr(engine, '_trade_pressure_feed', None)
        if entry_m == 'CHANNEL_SWING' and feed is not None:
            snapshot['trade_pressure'] = feed.evidence(symbol, position, position[STATE_KEY], snapshot, time.time()*1000)
        decision = PureTrendStrategyV2().evaluate_anti_whipsaw_profit_lock(position,price,snapshot,atr)
        current = position[STATE_KEY]
        changed = retired or old != current
        for key in STATE_KEYS:
            if key in position:
                meta[key] = copy.deepcopy(position[key])
        if changed:
            account.save_state()
        if hasattr(account, 'sync_moving_profit_stop'):
            position['exchange_managed'] = True
            if entry_m == 'CHANNEL_SWING' and (position.get('moving_profit_order') or meta.get('moving_profit_order')):
                if hasattr(engine, '_schedule_exit_followup'):
                    engine._schedule_exit_followup(symbol, lambda: account.sync_moving_profit_stop(symbol, price))
        if not decision and str(position.get('entry_mode','')).upper() != 'CHANNEL_SWING' and hasattr(account, 'sync_moving_profit_stop'):
            await account.sync_moving_profit_stop(symbol, price)
        if not decision or account.positions.get(symbol) is not position:
            return False
        
        reason = decision['type']
        trigger = decision.get('trigger', '')
        bypass_trend_hold = reason in (MA5_EXIT, PRESSURE_EXIT, 'EXIT_ACCOUNT_HARD_STOP')
        
        if entry_m == 'CHANNEL_SWING' and reason not in ('EXIT_INITIAL_ATR_HARD_STOP','EXIT_CONFIRMED_TREND_REVERSAL','EXIT_CONFIRMED_PIVOT_TURN') and trigger not in ('WATERFALL_DROP', 'EXIT_CATASTROPHIC_PROFIT_FLOOR', DOJI_TRIGGER, TERMINAL_DOJI_TRIGGER, 'EXIT_PEAK_PULLBACK_PRESSURE', 'MA3_CONFIRMED_TURN', 'EXIT_MOVING_PROFIT_STOP', 'CLOSED_MA5_MA15_REVERSE_CROSS', 'EXIT_EARLY_PROFIT_REVERSAL', 'EXIT_NO_PROFIT_ADVERSE_PRESSURE', 'EXIT_CONFIRMED_SWING_STRUCTURE', 'EXIT_FAILED_BREAKOUT_RECLAIM', 'EXIT_EARLY_SWING_REVERSAL', 'LIVE_STRUCTURE_BREAK', 'EXIT_CHANNEL_SAME_BAR_END', 'EXIT_CHANNEL_SAME_BAR_NET_PROFIT_LOCK', 'EXIT_SWING_ATR_PROFIT_LOCK', 'EXIT_EXHAUSTED_OUTER_SWING_REVERSAL'):
            try:
                from core.services.exits.trend_hold_evaluator import evaluate_trend_hold
                trend_status, _ = evaluate_trend_hold(position, snapshot, price)
            except Exception:
                trend_status = 'UNKNOWN'
                
            if not bypass_trend_hold and trend_status in ('HOLD', 'WARNING', 'UNKNOWN'):
                return False

        account.log(f'REALTIME_EXIT symbol={symbol} reason={reason} trigger={trigger} '
                    f'quote_ms={stamp} price={price} peak_price={current["peak_price"]} '
                    f'peak_net_pnl={current["peak_net_pnl"]} latency_ms={time.time()*1000-stamp:.1f} '
                    f'quote_age_at_start_ms={quote_age_at_start_ms:.1f} evaluation_ms={(time.perf_counter()-evaluation_started)*1000:.1f}', 'INFO')
        audit=dict(trade_pressure_exit=current.get('trade_pressure_exit'),ma5_outer_pivot=current.get('ma5_outer_pivot'),intact_trend_pullback=current.get('intact_trend_pullback'),pivot_guard_version=current.get('pivot_guard_version'),confirmed_pivot_exit=current.get('confirmed_pivot_exit'),confirmed_trend_exit=current.get('confirmed_trend_exit'),quote_age_at_start_ms=quote_age_at_start_ms,
                   evaluation_ms=(time.perf_counter()-evaluation_started)*1000,swing_atr_profit_lock=current.get('swing_atr_profit_lock'),structure_break_warning=current.get('structure_break_warning'),
                   structure_trend_aligned=current.get('structure_trend_aligned'),same_bar_profit_lock=current.get('same_bar_profit_lock'),entry_phase=(position.get('entry_snapshot') or {}).get('entry_phase'),
                   same_bar_exit_deadline_ms=(position.get('entry_snapshot') or {}).get('same_bar_exit_deadline_ms'),
                   reason=reason,quote_ms=stamp,price=price,peak_price=current.get('peak_price'),
                   peak_net_pnl=current.get('peak_net_pnl'),profit_stop_price=current.get('profit_stop_price'),
                   profit_stop_source=current.get('profit_stop_source'),entry_failure_level=position.get('entry_failure_level'),
                   early_swing_reversal=snapshot.get('early_swing_reversal'),
                   live_open=snapshot.get('live_open'), live_bar_ms=snapshot.get('live_bar_ms'),
                   closed_bar_ms=snapshot.get('closed_bar_ms'), atr=snapshot.get('atr'),
                   waterfall_body_atr=CHANNEL_WATERFALL_BODY_ATR,
                   kc_closed_history=snapshot.get('kc_closed_history'),
                   swing_structure=snapshot.get('swing_structure_'+position['side'].lower()),
                   side=position['side'], entry_price=position.get('entry_price'),
                   entry_atr=position.get('entry_atr'), initial_sl=position.get('initial_sl'),
                   ma5=snapshot.get('ma5'), last_ma5=snapshot.get('last_ma5'),
                   holding_exit_policy=position.get(STATE_KEY,{}).get('holding_exit_policy'),
                   structure_break_confirmation=position.get(STATE_KEY,{}).get('structure_break_confirmation'),
                   structure_break_threshold=position.get(STATE_KEY,{}).get('structure_break_threshold'),
                   trend_exhaustion_warning=position.get(STATE_KEY,{}).get('trend_exhaustion_warning',False),
                   single_body_exit_enabled=True if entry_m=='CHANNEL_SWING' else None)
        position['exit_protection_snapshot']=audit
        meta['exit_protection_snapshot']=audit
        closed = await account.close_position(symbol,price,'Channel Swing ' + reason + (' ' + trigger if trigger == DOJI_TRIGGER else ''),is_manual=True)
        return bool(closed) if reason in ('EXIT_CONFIRMED_SWING_STRUCTURE', MA5_EXIT, PRESSURE_EXIT) else True
    except (KeyError,TypeError,ValueError,OverflowError):
        return False
