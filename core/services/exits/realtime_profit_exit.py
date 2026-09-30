"""Observed-tick profit protection; no candle fetch or scan-lock dependency."""
import copy
import math
import time

from core.services.candle_data import closed_entry_candles
from core.services.exits.peak_trailing_exit import (
    STATE_KEY, STATE_KEYS, RETIRED_KEYS, migrate_peak_state, position_identity,
)
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
    last = closed.iloc[-1]
    snapshot.update({
        'atr': float(last.get('atr') or 0.),
        'kc_upper': float(last.get('kc_upper') or 0.),
        'kc_lower': float(last.get('kc_lower') or 0.),
        'kc_middle': float(last.get('ema_20', last.get('kc_middle', 0.)) or 0.),
        'ma3': float(last.get('ma3') or 0.),
        'ma5': float(last.get('ma5', last.get('ma3', 0)) or 0.),
        'ma15': float(last.get('ma15') or 0.),
        'close': float(last.get('close') or 0.),
        'open': float(last.get('open') or 0.),
        'high': float(last.get('high') or 0.),
        'low': float(last.get('low') or 0.)
    })
    
    if len(closed) >= 3:
        prev = closed.iloc[-2]
        prev2 = closed.iloc[-3]
        snapshot.update({
            'prev_close': float(prev.get('close') or 0.),
            'prev_open': float(prev.get('open') or 0.),
            'prev_high': float(prev.get('high') or 0.),
            'prev_low': float(prev.get('low') or 0.),
            'prev_kc_middle': float(prev.get('kc_middle', 0.)),
            'prev_ma5': float(prev.get('ma5', prev.get('ma3', 0)) or 0.),
            'prev2_close': float(prev2.get('close') or 0.),
            'prev2_open': float(prev2.get('open') or 0.)
        })
    elif len(closed) >= 2:
        prev = closed.iloc[-2]
        snapshot.update({
            'prev_close': float(prev.get('close') or 0.),
            'prev_open': float(prev.get('open') or 0.),
            'prev_high': float(prev.get('high') or 0.),
            'prev_low': float(prev.get('low') or 0.),
            'prev_kc_middle': float(prev.get('kc_middle', 0.)),
            'prev_ma5': float(prev.get('ma5', prev.get('ma3', 0)) or 0.),
            'prev2_close': 0.,
            'prev2_open': 0.
        })
    else:
        snapshot.update({
            'prev_close': 0.,
            'prev_open': 0.,
            'prev_high': 0.,
            'prev_low': 0.,
            'prev_kc_middle': float(prev.get('kc_middle', 0.)),
            'prev_ma5': 0.,
            'prev2_close': 0.,
            'prev2_open': 0.
        })
        
    return snapshot, snapshot['atr']


def migrate_account_peak_exits(account):
    """Run before trading tasks, and clean both persisted copies of held state."""
    changed = False
    for symbol, position in account.positions.items():
        meta = account.position_meta.setdefault(symbol, {})
        if (position.get('entry_mode') or meta.get('entry_mode')) != 'CHANNEL_SWING':
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
    if not position or position.get('entry_mode') != 'CHANNEL_SWING':
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
        old = copy.deepcopy(meta.get(STATE_KEY) or {})
        retired = any(key in source for source in (position,meta) for key in RETIRED_KEYS)
        migrate_peak_state(position, meta)
        try:
            if position[STATE_KEY].get('atr', 0.) > 0:
                snapshot, atr = {'quote_ms':stamp}, 0.
            else:
                snapshot, atr = cached_tick_indicators(getattr(engine,'_channel_exit_frames',{}).get(symbol),price,stamp)
        except (KeyError,TypeError,ValueError,OverflowError,IndexError):
            snapshot, atr = {'quote_ms':stamp}, 0.
        decision = PureTrendStrategyV2().evaluate_anti_whipsaw_profit_lock(position,price,snapshot,atr)
        current = position[STATE_KEY]
        changed = retired or any(old.get(k) != current.get(k) for k in
                  ('identity','peak_price','peak_net_pnl','atr','armed','pending'))
        for key in STATE_KEYS:
            if key in position:
                meta[key] = copy.deepcopy(position[key])
        if changed:
            account.save_state()
        if await enforce_hard_stop(account,symbol,price):
            return True
        if not decision or account.positions.get(symbol) is not position:
            return False
        reason = decision['type']
        account.log(f'REALTIME_EXIT symbol={symbol} reason={reason} trigger={decision["trigger"]} '
                    f'quote_ms={stamp} price={price} peak_price={current["peak_price"]} '
                    f'peak_net_pnl={current["peak_net_pnl"]} latency_ms={time.time()*1000-stamp:.1f}', 'INFO')
        await account.close_position(symbol,price,reason,is_manual=True)
        return True
    except (KeyError,TypeError,ValueError,OverflowError):
        return False
