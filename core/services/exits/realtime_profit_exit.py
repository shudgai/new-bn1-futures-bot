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
    if reached(peak, 7.0):
        return peak * 0.75, 3
    if reached(peak, 4.0):
        return 2.5, 2
    if reached(peak, 2.0):
        return 0.0, 1
    return None, 0


def _evaluate_realtime_core_exit_gates(position, meta, price, stamp, snapshot, fee, slippage):
    """Evaluate completed-bar structure and the net-ROE ratchet lock."""
    if not isinstance(snapshot, dict) or snapshot.get('reason') is not None:
        return None, False
    side = position.get('side')
    sign = 1 if side == 'LONG' else -1 if side == 'SHORT' else 0
    try:
        quote = float(price)
        mid = float(snapshot.get('live_kc_middle') or snapshot.get('kc_middle') or 0.)
        if not sign or not math.isfinite(quote) or quote <= 0:
            return None, False
        open_ms = float(position.get('open_timestamp') or 0.0) * 1000.0
        time_held_ms = stamp - open_ms
        is_early_hold_period = (time_held_ms < 120000)
        
        entry = float(position.get('entry_price') or 0.)
        lifecycle_stage = meta.get('lifecycle_stage', 1)
        entry_bar_ms = math.floor(open_ms / 60000) * 60000
        live_bar_ms = float(snapshot.get('live_bar_ms') or 0.0)
        closed_bar_ms = float(snapshot.get('closed_bar_ms') or 0.0)
        lower = float(snapshot.get('live_kc_lower') or snapshot.get('kc_lower') or 0.)
        upper = float(snapshot.get('live_kc_upper') or snapshot.get('kc_upper') or 0.)
        atr = float(snapshot.get('atr') or 0.0)
        profit_cushion = sign * (quote - entry)
        state_updated = False
        
        if lifecycle_stage < 3:
            if profit_cushion >= 1.0 * atr and atr > 0:
                lifecycle_stage = 3
                meta['lifecycle_stage'] = 3
                state_updated = True
            elif live_bar_ms > entry_bar_ms + 60000:
                lifecycle_stage = 3
                meta['lifecycle_stage'] = 3
                state_updated = True
            elif live_bar_ms > entry_bar_ms and lifecycle_stage == 1:
                lifecycle_stage = 2
                meta['lifecycle_stage'] = 2
                state_updated = True

        # [2026-10-11] 禁用開倉未拉開利潤就秒平 (LIFECYCLE_STAGE1_INSTANT_RETRACE_EXIT)，初動期部位保留呼吸空間
        if lifecycle_stage == 1:
            pass
        
        if lifecycle_stage == 2:
            if live_bar_ms > entry_bar_ms and closed_bar_ms == entry_bar_ms:
                closed_open = float(snapshot.get('last_open') or 0.)
                closed_close = float(snapshot.get('last_close') or 0.)
                if closed_open > 0 and closed_close > 0:
                    if side == 'SHORT' and closed_close > closed_open:
                        return 'LIFECYCLE_STAGE2_ADVERSE_CLOSE_EXIT', state_updated
                    elif side == 'LONG' and closed_close < closed_open:
                        return 'LIFECYCLE_STAGE2_ADVERSE_CLOSE_EXIT', state_updated
                lifecycle_stage = 3
                meta['lifecycle_stage'] = 3
                state_updated = True

        # 2. 谷底與波峰真實反轉避險 (Catastrophic Dump / Pump & Shadow Rejection)
        # 徹底拔除「微幅跌破就出場」的延遲邏輯，只在實質跌破關鍵大支撐/阻力，或出現爆量長影線時才避險
        mid = float(snapshot.get('live_kc_middle') or snapshot.get('kc_middle') or 0.)
        live_open = float(snapshot.get('live_open') or 0.)
        live_high = float(snapshot.get('live_high') or 0.)
        live_low = float(snapshot.get('live_low') or 0.)
        live_body = abs(quote - live_open)
        upper_wick = live_high - max(live_open, quote)
        lower_wick = min(live_open, quote) - live_low
        short_upper_wick_hold = (
            side == 'SHORT' and atr > 0 and upper_wick >= 0.4 * atr
        )
        history_5 = snapshot.get('history_5') or []
        is_trend_following = False
        is_counter_trend = False
        
        if len(history_5) >= 2:
            last_closed = history_5[-1]
            prev_closed = history_5[-2]
            kc_slope = float(last_closed.get('kc_middle', 0)) - float(prev_closed.get('kc_middle', 0))
            slope_threshold = 0.005 * float(last_closed.get('atr', atr))
            
            if side == 'SHORT':
                is_trend_following = (kc_slope < -slope_threshold)
                is_counter_trend = not is_trend_following
            elif side == 'LONG':
                is_trend_following = (kc_slope > slope_threshold)
                is_counter_trend = not is_trend_following

        # [2026-10-10] 移除 2 分鐘 (120000ms) 防抖延時 (is_early_hold_period)，允許首棒即時平倉
        if mid > 0 and live_open > 0:
            candle_range = live_high - live_low + 1e-9
            if side == 'LONG':
                # 1. [第一順位硬性執行] 多單結構徹底破壞平倉 (Exit Long on First MA5 Breach)
                # 嚴格規定：首根實體跌破 MA5 當根收線立即平多，第一順位無條件執行
                closed_close = float(snapshot.get('last_close') or 0.)
                closed_ma5 = float(snapshot.get('ma5') or 0.)
                if closed_close < closed_ma5 and closed_ma5 > 0:
                    return 'EXIT_LONG_ON_MA5_CLOSE_BREACH', state_updated
                elif quote <= lower:
                    return 'EXIT_LONG_ON_KC_LOWER_BREACH', state_updated

                # 條件 B: 實體長黑K且實質跌破中軌 (大瀑布)
                if quote < live_open and quote < mid and (live_open - quote) / live_open > 0.005:
                    return 'CATASTROPHIC_DUMP_EXIT', False
                # 條件 A: 高檔實時回踩平倉 (見頂真賣壓，盤中即時偵測)
                # 若突破上軌或累積漲幅 > 1.5 ATR，且實時從高點回落超過當根波幅的 35%
                if (atr > 0 and upper_wick >= 0.5 * atr
                        and upper_wick > 2.0 * live_body
                        and profit_cushion >= 0.5 * atr):
                    return 'LIVE_PEAK_REJECTION_EXIT', False
                    
                # 3. 大嘴巴見頂防護 (Wide Mouth Rejection)
                live_bandwidth = (upper - lower) / (mid + 1e-9)
                if live_bandwidth > 0.015: # 假設 1.5% 以上為擴口
                    # 一旦盤中出現反向陰棒(quote < live_open)或站回上軌之內(quote < upper)
                    if quote < live_open or quote < upper:
                        # 確保這是一個獲利的單子，避免過早停損
                        if profit_cushion > 0.5 * atr:
                            return 'EXIT_LONG_WIDE_MOUTH_REJECTION', state_updated
            elif side == 'SHORT' and not short_upper_wick_hold:
                # 1. [第一順位硬性執行] 空單結構徹底破壞平倉 (Exit Short on First MA5 Breach)
                # 嚴格規定：空單遇陽線站上 MA5 立即秒平，第一順位無條件執行
                closed_close = float(snapshot.get('last_close') or 0.)
                closed_ma5 = float(snapshot.get('ma5') or 0.)
                if closed_close > closed_ma5 and closed_ma5 > 0:
                    return 'EXIT_SHORT_ON_MA5_CLOSE_BREACH', state_updated
                elif quote >= upper:
                    return 'EXIT_SHORT_ON_KC_UPPER_BREACH', state_updated

                # 條件 B: 實體長紅K且實質突破中軌 (大拉升)
                if quote > live_open and quote > mid and (quote - live_open) / live_open > 0.005:
                    return 'CATASTROPHIC_PUMP_EXIT', False
                # 條件 A: 低檔實時回抽平倉 (見底真買盤，盤中即時偵測)
                # 若跌破下軌或累積跌幅 > 1.5 ATR，且實時從低點回抽超過當根波幅的 35%
                if (atr > 0 and lower_wick >= 0.5 * atr
                        and lower_wick > 2.0 * live_body
                        and profit_cushion >= 0.5 * atr):
                    return 'LIVE_BOTTOM_REJECTION_EXIT', False
                    
                # 04:59 回踩當棒即時平空（Intra-bar Pullback Exit - Zero Lag）
                history = snapshot.get('history_5') or []
                if len(history) >= 1 and atr > 0:
                    prev_b = history[-1]
                    prev_o, prev_c, prev_l, prev_h = prev_b['o'], prev_b['c'], prev_b['l'], prev_b['h']
                    prev_drop = prev_o - prev_c
                    prev_range = prev_h - prev_l + 1e-9
                    prev_lower_shadow = min(prev_o, prev_c) - prev_l
                    
                    # 暴跌後（前棒實體跌幅 >= 2.0*ATR 或極端下影線）
                    is_massive_drop = (prev_drop >= 2.0 * atr) or (prev_range >= 1.5 * atr and (prev_lower_shadow / prev_range) >= 0.5)
                    if is_massive_drop:
                        # 當前棒出現向上回踩：站上開盤價，或回彈幅度 >= 0.5 * 前棒實體
                        if (lower_wick >= 0.5 * atr
                                and lower_wick > 2.0 * live_body):
                            return 'REALTIME_SHORT_PULLBACK_EXIT', False


                # 4. 雙底探針/止跌反陽保護
                history = snapshot.get('history_5') or []
                if len(history) >= 1 and atr > 0:
                    prev_b = history[-1]
                    prev_o, prev_c, prev_l, prev_h = prev_b['o'], prev_b['c'], prev_b['l'], prev_b['h']
                    prev_range = prev_h - prev_l + 1e-9
                    prev_lower_shadow = min(prev_o, prev_c) - prev_l
                    lowest_point = min(live_low, prev_l)
                    
                    if (entry - lowest_point) >= 2.5 * atr:
                        # 底部收出止跌十字/長下影線 (下影線 >= 45%)
                        if prev_lower_shadow / prev_range >= 0.45:
                            # 緊接著出現「反向綠K且收在當棒高檔」
                            live_ma5 = float(snapshot.get('live_ma5') or snapshot.get('ma5') or 0.)
                            if (quote - live_open >= 0.35 * atr
                                    and quote > live_ma5
                                    and live_body / candle_range >= 0.60
                                    and (quote - live_low) / candle_range >= 0.70):
                                return 'EXIT_SHORT_TROUGH_REVERSAL_CONFIRMED', state_updated

        qty = float(position.get('qty') or 0.)
        margin = float(position.get('margin') or 0.)
        if margin <= 0:
            leverage = float(position.get('leverage') or 0.)
            if leverage > 0:
                margin = entry * qty / leverage
        if not all(math.isfinite(value) and value > 0 for value in (entry, qty, margin)):
            return None, state_updated
        net_pnl = estimated_display_net_pnl(entry, quote, qty, sign, fee, slippage)
        current_roe = net_pnl / margin * 100.0
        if not math.isfinite(current_roe):
            return None, state_updated

        # --- HARD_STOP_LOSS / 緊急防爆 ---
        if side == 'SHORT' and (current_roe <= -2.0 or (quote - entry) >= 1.5 * atr):
            return 'HARD_STOP_LOSS', state_updated
            
        identity = _profit_lock_identity(position)
        state = position.get(PROFIT_LOCK_STATE_KEY) or meta.get(PROFIT_LOCK_STATE_KEY) or {}
        if state.get('identity') != identity:
            state = {'identity': identity, 'peak_net_roe_pct': current_roe,
                     'last_ms': stamp, 'tier': 0, 'floor_net_roe_pct': None}
        elif stamp < float(state.get('last_ms') or 0.):
            return None, state_updated
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
        if floor is not None and current_roe < floor:
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
    snapshot['current_bar_is_closed'] = (
        live.get('is_closed') is True
        or (type(live.get('is_closed')).__name__ == 'bool_'
            and bool(live.get('is_closed')))
    )

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
                    side = position.get('side')
                    live_open = float(snapshot.get('live_open') or 0.)
                    live_high = float(snapshot.get('live_high') or 0.)
                    upper_wick = live_high - max(live_open, price)
                    short_upper_wick_hold = (
                        side == 'SHORT' and atr > 0 and upper_wick >= 0.4 * atr
                    )
                    from core.services.exits.dual_track_exit_service import detect_climax_reversal
                    climax = (
                        None if (
                            short_upper_wick_hold
                            or not snapshot.get('current_bar_is_closed')
                        )
                        else detect_climax_reversal(frame, side)
                    )
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
                    from core.gates.holding_protection_gate import HoldingProtectionExitGate
                    prot_exit, prot_details = HoldingProtectionExitGate.evaluate(position, frame, price)
                    if prot_exit:
                        account.log(
                            f'HOLDING_PROTECTION_EXIT symbol={symbol} reason={prot_exit} '
                            f'quote_ms={stamp} price={price} details={prot_details}',
                            'WARNING',
                        )
                        if prot_details.get('authorized_action') == 'ACTION_FLIP_LONG_TO_SHORT':
                            from core.services.order_execution import OrderExecutionService
                            await OrderExecutionService.execute_top_engulfing_flip(
                                engine, symbol, frame, price, position,
                                prot_exit, prot_details,
                            )
                        else:
                            await account.close_position(
                                symbol, price, f'Channel Swing {prot_exit}',
                                is_manual=True,
                            )
                        return True

                    from core.exits.peak_valley_exit import PeakValleyExit
                    peak_exit, peak_details = PeakValleyExit.evaluate(
                        position, frame, price,
                    )
                    if peak_exit:
                        is_allowed, valid_reason, valid_details = HoldingProtectionExitGate.validate_exit(
                            position, frame, price, peak_exit, peak_details,
                        )
                        if is_allowed:
                            account.log(
                                f'REALTIME_EXIT symbol={symbol} reason={valid_reason} '
                                f'quote_ms={stamp} price={price} details={valid_details}',
                                'WARNING',
                            )
                            await account.close_position(
                                symbol, price, f'Channel Swing {valid_reason}',
                                is_manual=True,
                            )
                            return True
                        else:
                            account.log(
                                f'HOLDING_PROTECTION_BLOCKED symbol={symbol} reason={peak_exit} '
                                f'gate_reason={valid_reason}', 'INFO',
                            )
                from core.config import SLIPPAGE_PCT, TAKER_FEE_RATE
                gate_trigger, state_changed = _evaluate_realtime_core_exit_gates(
                    position, meta, price, stamp, snapshot,
                    TAKER_FEE_RATE, SLIPPAGE_PCT,
                )
                if state_changed:
                    account.save_state()
                if gate_trigger:
                    from core.gates.holding_protection_gate import HoldingProtectionExitGate
                    lock_details = {}
                    if (gate_trigger == PROFIT_LOCK_TRIGGER
                            or gate_trigger.startswith(PROFIT_LOCK_TRIGGER)):
                        lock_state = (
                            position.get(PROFIT_LOCK_STATE_KEY)
                            or meta.get(PROFIT_LOCK_STATE_KEY)
                            or {}
                        )
                        peak_net_roe = float(lock_state.get('peak_net_roe_pct') or 0.0)
                        current_net_roe = float(lock_state.get('current_net_roe_pct') or 0.0)
                        lock_details = {
                            'peak_roe': peak_net_roe / 100.0,
                            'giveback_ratio': (
                                (peak_net_roe - current_net_roe) / peak_net_roe
                                if peak_net_roe > 0 else 0.0
                            ),
                        }
                    is_allowed, valid_reason, valid_details = HoldingProtectionExitGate.validate_exit(
                        position, frame, price, gate_trigger, details=lock_details,
                    )
                    if is_allowed:
                        account.log(
                            f'REALTIME_EXIT symbol={symbol} reason={valid_reason} '
                            f'quote_ms={stamp} price={price}', 'WARNING',
                        )
                        await account.close_position(
                            symbol, price, f'Channel Swing {valid_reason}', is_manual=True,
                        )
                        return True
                    else:
                        account.log(
                            f'HOLDING_PROTECTION_BLOCKED symbol={symbol} reason={gate_trigger} '
                            f'gate_reason={valid_reason}', 'INFO',
                        )
                if entry_m == 'CHANNEL_SWING':
                    # On a forming SHORT bar, the holding gate admits only a
                    # verified V-reversal or ratchet; structure exits wait for close.
                    if (gate_trigger in (
                                'CATASTROPHIC_DUMP_EXIT', 'CATASTROPHIC_PUMP_EXIT',
                                'PEAK_REJECTION_EXIT', 'TROUGH_REJECTION_EXIT',
                                'LIFECYCLE_STAGE1_INSTANT_RETRACE_EXIT',
                                'LIFECYCLE_STAGE2_ADVERSE_CLOSE_EXIT',
                            )
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
            else (frozenset() if entry_m == 'CHANNEL_SWING' else CHANNEL_SWING_EXIT_TRIGGERS)
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
    except Exception as e:
        import traceback
        account.log(f'CRITICAL_EXIT_EVALUATION_ERROR {symbol}: {e}\n{traceback.format_exc()}', 'ERROR')
        # 防裝死：發生非預期例外時，執行保守緊急平倉
        try:
            await account.close_position(
                symbol, price, f'Channel Swing FAIL_SAFE_EMERGENCY_EXIT ({type(e).__name__})', is_manual=True
            )
            return True
        except Exception:
            return False
