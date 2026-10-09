"""Cached-candle abnormal-body protection without REST or scan-lock waits."""
import copy
import math
import time

from core.services.candle_data import closed_entry_candles
from core.services.exits.peak_trailing_exit import (
    STATE_KEY, STATE_KEYS, RETIRED_KEYS, migrate_peak_state, position_identity, DOJI_TRIGGER,
    channel_initial_stop_disabled, CHANNEL_SWING_EXIT_TRIGGERS,
    PIVOT_ONLY_CHANNEL_EXIT_TRIGGERS, PIVOT_ONLY_CHANNEL_SYMBOLS,
)
from core.services.exits.hard_stop_service import enforce_hard_stop
from core.services.exits.entry_atr_protection import (
    channel_strategy_exit_grace_active,
    clear_channel_strategy_exit_pending,
)
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
            'ma5': float(b.get('ma5', b.get('ma3', 0))),
            'atr': float(b.get('atr', 0)),
            'kc_upper': float(b.get('kc_upper', 0)),
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
        kc_middle=float(last.get('kc_middle', 0.)),
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
        snapshot.update(
            live_bar_ms=bar,
            closed_bar_ms=last_ms,
            atr=float(last.get('atr') or 0.),
            live_open=float(live.get('open') or 0.),
            live_high=max(float(live.get('high') or 0.), float(price)),
            live_low=min(float(live.get('low') or 0.), float(price)),
            live_kc_upper=float(live.get('kc_upper') or 0.),
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
            from core.services.entry_contract import ck_direction
            frame = getattr(engine, '_channel_exit_frames', {}).get(symbol)
            if frame is not None and not frame.empty:
                ck_dir = ck_direction(frame)
                from core.services.exits.peak_trailing_exit import lifeline_held
                if ck_dir and ck_dir != ident[0] and not lifeline_held(ident[0], price, frame.iloc[-1].to_dict()):
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
                   'net_roe_lock_peak','net_roe_lock_armed','tiered_price_peak','profit_lock_basis','lifeline_policy_version',
                   'trigger','trigger_bar_ms','trigger_price',
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
            if symbol in PIVOT_ONLY_CHANNEL_SYMBOLS
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
                current, meta.get(STATE_KEY, {}),
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
                           'THREE_POINT_PIVOT', 'TWO_CLOSED_ADVERSE_ABNORMAL',
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
