"""Cached-candle abnormal-body protection without REST or scan-lock waits."""
import copy
import math
import time
import pandas as pd

from core.services.candle_data import closed_entry_candles
from core.services.exits.peak_trailing_exit import (
    STATE_KEY, STATE_KEYS, RETIRED_KEYS, migrate_peak_state, position_identity, DOJI_TRIGGER,
    channel_initial_stop_disabled, CHANNEL_SWING_EXIT_TRIGGERS,
    PIVOT_ONLY_CHANNEL_EXIT_TRIGGERS, PIVOT_ONLY_CHANNEL_SYMBOLS,
    is_pivot_only_channel_symbol,
    EMERGENCY_DUMP_CUT_TRIGGER,
    emergency_long_dump_cut_trigger,
    estimated_display_net_pnl,
)
from core.services.exits.hard_stop_service import enforce_hard_stop
from core.services.exits.entry_atr_protection import (
    channel_strategy_exit_grace_active,
    clear_channel_strategy_exit_pending,
)
from core.services.exits.net_roe_profit_lock import (
    has_reached_net_roe_floor,
    tiered_net_roe_floor,
)
from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2

PROFIT_LOCK_STATE_KEY = 'three_tier_net_roe_lock_state'
PROFIT_LOCK_TRIGGER = 'NET_ROE_THREE_TIER_GIVEBACK'
SHORT_FALSE_BREAKOUT_TRIGGER = 'EXIT_SHORT_FAILED_KC_BREAKOUT'
SAME_BAR_MIN_NET_PROFIT_RATE = 0.003


def same_bar_net_profit_exit(position, price, stamp, snapshot, fee_rate, slippage_pct):
    """Require a fee-adjusted return threshold before exiting in the entry candle."""
    try:
        side = str(position.get('side') or '').upper()
        entry = float(position.get('entry_price') or 0.0)
        opened = float(position.get('open_timestamp') or 0.0)
        live_bar_ms = float(snapshot.get('live_bar_ms') or 0.0)
        quote_ms = float(stamp)
        timeframe_ms = float(snapshot.get('timeframe_ms') or 60_000.0)
        current_price = float(price)
        fee_rate = float(fee_rate)
        slippage_pct = float(slippage_pct)
        if opened < 1e11:
            opened *= 1000.0
        values = (
            entry, opened, live_bar_ms, quote_ms, timeframe_ms, current_price,
            fee_rate, slippage_pct,
        )
        if (
            side not in ('LONG', 'SHORT')
            or not all(math.isfinite(value) for value in values)
            or min(entry, opened, live_bar_ms, quote_ms, timeframe_ms, current_price) <= 0
            or fee_rate < 0
            or slippage_pct < 0
            or not live_bar_ms <= opened < live_bar_ms + timeframe_ms
            or not live_bar_ms <= quote_ms < live_bar_ms + timeframe_ms
        ):
            return False, None
        direction = 1.0 if side == 'LONG' else -1.0
        gross_rate = direction * (current_price - entry) / entry
        estimated_cost_rate = 2.0 * (fee_rate + slippage_pct)
        net_rate = gross_rate - estimated_cost_rate
        return net_rate >= SAME_BAR_MIN_NET_PROFIT_RATE, net_rate
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False, None


def short_false_breakout_exit(position, meta, price, snapshot):
    """Cut a live short on the first green bar reclaiming its breakout open."""
    try:
        entry_snapshot = (
            position.get('entry_snapshot') or meta.get('entry_snapshot') or {}
        )
        if (position.get('side') != 'SHORT'
                or str(position.get('entry_mode') or meta.get('entry_mode') or '').upper()
                != 'CHANNEL_SWING'
                or entry_snapshot.get('signal_code') != 'AUTHORIZED_REALTIME_BREAKOUT'
                or not isinstance(snapshot, dict)):
            return False
        entry_bar_ms = float(entry_snapshot.get('entry_bar_id') or 0.)
        entry_bar_open = float(entry_snapshot.get('entry_bar_open') or 0.)
        live_bar_ms = float(snapshot.get('live_bar_ms') or 0.)
        closed_bar_ms = float(snapshot.get('closed_bar_ms') or 0.)
        live_open = float(snapshot.get('live_open') or 0.)
        quote = float(price)
        values = (entry_bar_ms, entry_bar_open, live_bar_ms,
                  closed_bar_ms, live_open, quote)
        if (not all(math.isfinite(value) and value > 0 for value in values)
                or live_bar_ms != entry_bar_ms + 60000
                or closed_bar_ms != entry_bar_ms
                or quote <= live_open or quote <= entry_bar_open):
            return False
        return True
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return False


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
    return tiered_net_roe_floor(peak_net_roe_pct)


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
        emergency_dump = emergency_long_dump_cut_trigger(position, quote, snapshot)
        if emergency_dump is not None:
            return EMERGENCY_DUMP_CUT_TRIGGER, False
        if short_false_breakout_exit(position, meta, quote, snapshot):
            return SHORT_FALSE_BREAKOUT_TRIGGER, False
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
        
        if lifecycle_stage == 2 and live_bar_ms > entry_bar_ms:
            # A single adverse close after entry is ordinary pullback noise.
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
                # 一般 MA5 回踩不授權平倉；反側 KC 外軌破位仍立即退出。
                if quote <= lower:
                    return 'EXIT_LONG_ON_KC_LOWER_BREACH', state_updated

                # 大瀑布只看逆向實體幅度，不以長影線或 0.5% 小回踩觸發。
                if quote < live_open and live_open - quote >= 1.5 * atr and quote < mid:
                    return 'CATASTROPHIC_DUMP_EXIT', False
            elif side == 'SHORT' and not short_upper_wick_hold:
                # 一般 MA5 回抽不授權平倉；反側 KC 外軌破位仍立即退出。
                if quote >= upper:
                    return 'EXIT_SHORT_ON_KC_UPPER_BREACH', state_updated

                # 大拉升與大瀑布使用相同的 1.5 ATR 逆向實體門檻。
                if quote > live_open and quote - live_open >= 1.5 * atr and quote > mid:
                    return 'CATASTROPHIC_PUMP_EXIT', False
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
                            if (quote > mid
                                    and quote - live_open >= 0.5 * atr
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

        # ──【鐵律一：極端避雷針動態收割（Spike Reversal Harvest）】──
        # 觸發條件：持倉浮盈峰值 >= 1.5 ATR 的高檔衝刺中：
        # 1. 盤中自最高點回撤 >= 1.2 ATR，或峰值浮盈回吐幅度 >= 40%
        # 2. 或當前 1m K 棒上影線長度佔比 >= (High - Low) * 0.50（明顯衝高受阻大跳水）
        # 執行動作：豁免所有均線防守等待，盤中當根立即市價平倉落袋（EXIT_BY_SPIKE_REVERSAL）！
        peak_p = max(float(position.get('highest_price') or position.get('peak_price') or entry), quote if side == 'LONG' else entry)
        trough_p = min(float(position.get('lowest_price') or position.get('trough_price') or entry), quote if side == 'SHORT' else entry)
        position['highest_price'] = peak_p
        position['lowest_price'] = trough_p
        meta['highest_price'] = peak_p
        meta['lowest_price'] = trough_p

        c_range = max(live_high - live_low, 1e-9)
        live_ma5 = float(snapshot.get('live_ma5') or snapshot.get('ma5') or 0.)
        live_ma15 = float(snapshot.get('live_ma15') or snapshot.get('ma15') or 0.)
        channel_state = str(snapshot.get('channel_state', '')).strip().upper()

        # ──【第一道鐵律：利潤保本移損線（Breakeven Floor - 嚴禁大賺放成虧損）】──
        # 浮盈曾達 >= 1.0 ATR 或淨 ROE >= 8.0% 時，強制鎖定底線在 entry_price + 0.05%（空單 - 0.05%）
        max_pnl_atr = (peak_p - entry) / atr if (side == 'LONG' and atr > 0) else (entry - trough_p) / atr if (side == 'SHORT' and atr > 0) else 0.0
        is_be_locked = position.get('breakeven_locked', False) or meta.get('breakeven_locked', False) or (max_pnl_atr >= 1.0 or state['peak_net_roe_pct'] >= 8.0)
        if is_be_locked:
            position['breakeven_locked'] = True
            meta['breakeven_locked'] = True
            if side == 'LONG':
                be_price = entry * 1.0005
                curr_sl = float(position.get('sl') or position.get('stop_loss') or 0.0)
                position['sl'] = max(curr_sl, be_price)
                position['stop_loss'] = max(curr_sl, be_price)
                if quote <= be_price:
                    return 'EXIT_BY_BREAKEVEN_LOCK', True
            elif side == 'SHORT':
                be_price = entry * 0.9995
                curr_sl = float(position.get('sl') or position.get('stop_loss') or 999999999.0)
                position['sl'] = min(curr_sl, be_price)
                position['stop_loss'] = min(curr_sl, be_price)
                if quote >= be_price:
                    return 'EXIT_BY_BREAKEVEN_LOCK', True

        # ──【鐵律一：極端避雷針動態收割（Spike Reversal Harvest）】──
        if side == 'LONG':
            max_gain = peak_p - entry
            if atr > 0 and max_gain >= 1.5 * atr:
                rebound_drop = peak_p - quote
                giveback = rebound_drop / max_gain if max_gain > 0 else 0.0
                if (rebound_drop >= 1.2 * atr or giveback >= 0.40 or (upper_wick / c_range >= 0.50 and quote < live_high - 0.4 * atr)) and quote > entry:
                    return 'EXIT_BY_SPIKE_REVERSAL', True
        elif side == 'SHORT':
            max_gain = entry - trough_p
            if atr > 0 and max_gain >= 1.5 * atr:
                rebound_rise = quote - trough_p
                giveback = rebound_rise / max_gain if max_gain > 0 else 0.0
                if (rebound_rise >= 1.2 * atr or giveback >= 0.40 or (lower_wick / c_range >= 0.50 and quote > live_low + 0.4 * atr)) and quote < entry:
                    return 'EXIT_BY_SPIKE_REVERSAL', True

        # ──【開倉當根防洗鐵律】開倉後不要當根平倉，除非剛好是真峰谷，否則除了鎖利外都不平倉──
        entry_bar_ms = float((position.get('entry_snapshot') or meta.get('entry_snapshot') or {}).get('entry_bar_id') or 0.)
        open_ts = float(position.get('open_timestamp') or meta.get('open_timestamp') or 0.)
        open_ms = open_ts * 1000.0 if (0 < open_ts < 1e11) else open_ts
        live_bar_ms = float(snapshot.get('live_bar_ms') or 0.)
        is_same_bar = False
        if live_bar_ms > 0:
            if entry_bar_ms > 0 and entry_bar_ms == live_bar_ms:
                is_same_bar = True
            elif open_ms > 0 and (math.floor(open_ms / 60000.0) * 60000.0 == live_bar_ms or open_ms >= live_bar_ms):
                is_same_bar = True

        from core.gates.holding_protection_gate import has_confirmed_true_pivot
        has_true_pivot = False
        if isinstance(snapshot.get('frame'), pd.DataFrame):
            has_true_pivot, _, _ = has_confirmed_true_pivot(position, snapshot.get('frame'), quote)

        # 若在開倉當根，且未確認真峰谷，直接跳過一般回撤判定（除了上方已執行的鎖利與避雷針收割外，當根絕不平倉）
        if not is_same_bar or has_true_pivot:
            # ──【峰頂/谷底敏銳判定（Fast Top/Valley Exit: EXIT_BY_FAST_PEAK_REVERSAL）】──
            # 浮盈顯著時（曾達 >= 0.8 ATR 或 ROE >= 5.0%）：
            # 多單：高檔長上影線滯漲 + 實體陰線（body >= 0.4 ATR）摜穿 MA5，或 MA5 急速向下彎頭跌破 MA5 立即平多
            if side == 'LONG':
                max_gain = peak_p - entry
                if (max_gain >= 0.8 * atr or state['peak_net_roe_pct'] >= 5.0):
                    c_is_bearish = (live_open > quote) and ((live_open - quote) >= 0.4 * atr)
                    if (upper_wick >= 0.35 * atr and c_is_bearish and quote < live_ma5):
                        return 'EXIT_BY_FAST_PEAK_REVERSAL', True
                    if len(history_5) >= 2:
                        last_ma5 = float(history_5[-1].get('ma5', live_ma5))
                        prev_ma5 = float(history_5[-2].get('ma5', last_ma5))
                        if (last_ma5 - prev_ma5 <= -0.02 * atr) and quote < live_ma5:
                            return 'EXIT_BY_FAST_PEAK_REVERSAL', True
            elif side == 'SHORT':
                max_gain = entry - trough_p
                if (max_gain >= 0.8 * atr or state['peak_net_roe_pct'] >= 5.0):
                    c_is_bullish = (quote > live_open) and ((quote - live_open) >= 0.4 * atr)
                    if (lower_wick >= 0.35 * atr and c_is_bullish and quote > live_ma5):
                        return 'EXIT_BY_FAST_VALLEY_REVERSAL', True
                    if len(history_5) >= 2:
                        last_ma5 = float(history_5[-1].get('ma5', live_ma5))
                        prev_ma5 = float(history_5[-2].get('ma5', last_ma5))
                        if (last_ma5 - prev_ma5 >= 0.02 * atr) and quote > live_ma5:
                            return 'EXIT_BY_FAST_VALLEY_REVERSAL', True

            # ──【第二道防線：真峰頂/真谷底趨勢逆轉即時檢查】──
            channel_state = str(snapshot.get('channel_state', '')).strip().upper()
            if side == 'LONG':
                is_kc_down = channel_state in ('KC向下', 'DOWN', 'BEARISH', 'DOWNTREND', 'KC_TREND_DOWN')
                if has_true_pivot or ((mid > 0 and quote < mid) and not is_same_bar):
                    return 'EXIT_BY_CONFIRMED_TRUE_PEAK', True
            elif side == 'SHORT':
                is_kc_up = channel_state in ('KC向上', 'UP', 'BULLISH', 'UPTREND', 'KC_TREND_UP')
                if has_true_pivot or ((mid > 0 and quote > mid) and not is_same_bar):
                    return 'EXIT_BY_CONFIRMED_TRUE_VALLEY', True

        # ──【多頭/空頭初生期強制抱緊鎖定（Initial Holding Protection）】──
        # 只要 1m K 線收盤/最新價仍踩在 MA5 之上（或未跌破 MA15），一票否決所有神經質平多
        if side == 'LONG':
            is_bullish_struct = (live_ma5 >= live_ma15) and ('DOWN' not in channel_state)
            if is_bullish_struct and (quote >= live_ma5 or (live_ma15 > 0 and quote >= live_ma15)):
                return None, True
        elif side == 'SHORT':
            is_bearish_struct = (live_ma5 <= live_ma15) and ('UP' not in channel_state)
            if is_bearish_struct and (quote <= live_ma5 or (live_ma15 > 0 and quote <= live_ma15)):
                return None, True

        if floor is not None and has_reached_net_roe_floor(current_roe, floor):
            return f'{PROFIT_LOCK_TRIGGER}_TIER_{tier}', True
        return None, True
    except (KeyError, TypeError, ValueError, OverflowError):
        return None, False


def _evaluate_profit_lock_only(position, meta, price, stamp, snapshot, fee, slippage):
    """Update and evaluate only the persisted net-ROE profit-lock floor."""
    if not isinstance(snapshot, dict) or snapshot.get('reason') is not None:
        return None, False
    try:
        side = str(position.get('side') or '').upper()
        sign = 1 if side == 'LONG' else -1 if side == 'SHORT' else 0
        quote = float(price)
        entry = float(position.get('entry_price') or 0.0)
        qty = float(position.get('qty') or 0.0)
        margin = float(position.get('margin') or 0.0)
        if margin <= 0:
            leverage = float(position.get('leverage') or 0.0)
            if leverage > 0:
                margin = entry * qty / leverage
        if (
            not sign
            or not all(math.isfinite(value) and value > 0
                       for value in (quote, entry, qty, margin))
        ):
            return None, False

        current_roe = estimated_display_net_pnl(
            entry, quote, qty, sign, fee, slippage,
        ) / margin * 100.0
        if not math.isfinite(current_roe):
            return None, False

        identity = _profit_lock_identity(position)
        state = position.get(PROFIT_LOCK_STATE_KEY) or meta.get(PROFIT_LOCK_STATE_KEY) or {}
        if state.get('identity') != identity:
            state = {
                'identity': identity,
                'peak_net_roe_pct': current_roe,
                'last_ms': stamp,
                'tier': 0,
                'floor_net_roe_pct': None,
            }
        elif stamp < float(state.get('last_ms') or 0.0):
            return None, False
        else:
            state = copy.deepcopy(state)
            state['peak_net_roe_pct'] = max(
                current_roe, float(state.get('peak_net_roe_pct', current_roe)),
            )
            state['last_ms'] = stamp

        floor, tier = _tiered_net_roe_floor(state['peak_net_roe_pct'])
        state.update(
            tier=tier,
            floor_net_roe_pct=floor,
            current_net_roe_pct=current_roe,
        )
        position[PROFIT_LOCK_STATE_KEY] = copy.deepcopy(state)
        meta[PROFIT_LOCK_STATE_KEY] = copy.deepcopy(state)

        atr = float(snapshot.get('atr') or 0.0)
        peak_price = max(
            float(position.get('highest_price') or entry), quote,
        ) if side == 'LONG' else float(position.get('highest_price') or entry)
        trough_price = min(
            float(position.get('lowest_price') or entry), quote,
        ) if side == 'SHORT' else float(position.get('lowest_price') or entry)
        position['highest_price'] = peak_price
        position['lowest_price'] = trough_price
        meta['highest_price'] = peak_price
        meta['lowest_price'] = trough_price
        favorable_atr = (
            (peak_price - entry) / atr if side == 'LONG' and atr > 0
            else (entry - trough_price) / atr if side == 'SHORT' and atr > 0
            else 0.0
        )
        if (
            position.get('breakeven_locked')
            or meta.get('breakeven_locked')
            or favorable_atr >= 1.0
            or state['peak_net_roe_pct'] >= 8.0
        ):
            position['breakeven_locked'] = True
            meta['breakeven_locked'] = True
            be_price = entry * (1.0005 if side == 'LONG' else 0.9995)
            if side == 'LONG':
                stop = max(
                    float(position.get('sl') or position.get('stop_loss') or 0.0),
                    be_price,
                )
                position['sl'] = position['stop_loss'] = stop
                if quote <= be_price:
                    return 'EXIT_BY_BREAKEVEN_LOCK', True
            else:
                stop = min(
                    float(position.get('sl') or position.get('stop_loss') or math.inf),
                    be_price,
                )
                position['sl'] = position['stop_loss'] = stop
                if quote >= be_price:
                    return 'EXIT_BY_BREAKEVEN_LOCK', True

        if floor is not None and has_reached_net_roe_floor(current_roe, floor):
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
        closed_bar_is_closed=True,
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
    from core.symbol_aliases import is_auto_entry_symbol
    restricted_exit_policy = is_auto_entry_symbol(symbol)
    if not position or (
        entry_m in ('EXHAUSTION_SNIPER', 'PIVOT_TURN')
        and not restricted_exit_policy
    ):
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

        if restricted_exit_policy:
            frame = getattr(engine, '_channel_exit_frames', {}).get(symbol)
            from core.services.exits.kc_true_extreme_exit import (
                evaluate_kc_true_extreme_exit,
            )
            kc_exit, state_changed = evaluate_kc_true_extreme_exit(
                position, frame, price, stamp, meta,
            )
            if state_changed:
                account.save_state()
            if kc_exit is not None:
                exit_reason, exit_details = kc_exit
                account.log(
                    f'KC_STRUCTURE_EXIT symbol={symbol} reason={exit_reason} '
                    f'quote_ms={stamp} price={price} details={exit_details}',
                    'WARNING',
                )
                closed = await account.close_position(
                    symbol, price, f'{entry_m} {exit_reason}', is_manual=True,
                )
                if not closed:
                    account.log(
                        f'KC_STRUCTURE_EXIT_RETRY_PENDING symbol={symbol} '
                        f'reason={exit_reason} quote_ms={stamp}',
                        'WARNING',
                    )
                return True

        if restricted_exit_policy:
            frame = getattr(engine, '_channel_exit_frames', {}).get(symbol)
            if frame is None or frame.empty:
                account.log(
                    f'REALTIME_EXIT_HOLD symbol={symbol} '
                    'reason=EXIT_SNAPSHOT_UNAVAILABLE',
                    'WARNING',
                )
                return False

            snapshot, _ = cached_tick_indicators(frame, price, stamp)
            if entry_m == 'CHANNEL_SWING' and position.get('side') == 'SHORT':
                from core.services.exits.short_oversold_exit import (
                    REVERSAL_PARTIAL_TRIGGER,
                    RATCHET_EXIT_TRIGGER,
                    TREND_HOLD_STATE_KEY,
                    evaluate_short_oversold_exit,
                    short_bearish_trend_active,
                )
                from core.services.exits.kc_true_extreme_exit import (
                    short_kc_bearish_trend_active,
                )
                kc_bearish_trend = short_kc_bearish_trend_active(frame, stamp)
                trend_hold_active = kc_bearish_trend or (
                    short_bearish_trend_active(snapshot) is not False
                )
                if (
                    position.get(TREND_HOLD_STATE_KEY) is not trend_hold_active
                    or meta.get(TREND_HOLD_STATE_KEY) is not trend_hold_active
                ):
                    position[TREND_HOLD_STATE_KEY] = trend_hold_active
                    meta[TREND_HOLD_STATE_KEY] = trend_hold_active
                    account.save_state()
                oversold_trigger, oversold_state_changed = (
                    evaluate_short_oversold_exit(
                        position,
                        meta,
                        price,
                        snapshot,
                        kc_bearish_trend=kc_bearish_trend,
                    )
                )
                if oversold_state_changed:
                    account.save_state()
                if oversold_trigger == REVERSAL_PARTIAL_TRIGGER:
                    account.log(
                        f'CHANNEL_SHORT_OVERSOLD_PARTIAL symbol={symbol} '
                        f'quote_ms={stamp} price={price}',
                        'WARNING',
                    )
                    return await account.partial_close_position(
                        symbol,
                        price,
                        f'Channel Swing {REVERSAL_PARTIAL_TRIGGER}',
                        fraction=0.5,
                        is_manual=True,
                    )
                if oversold_trigger == RATCHET_EXIT_TRIGGER:
                    account.log(
                        f'CHANNEL_SHORT_OVERSOLD_RATCHET symbol={symbol} '
                        f'quote_ms={stamp} price={price}',
                        'WARNING',
                    )
                    return await account.close_position(
                        symbol,
                        price,
                        f'Channel Swing {RATCHET_EXIT_TRIGGER}',
                        is_manual=True,
                    )

            from core.config import SLIPPAGE_PCT, TAKER_FEE_RATE
            lock_trigger, state_changed = _evaluate_profit_lock_only(
                position, meta, price, stamp, snapshot,
                TAKER_FEE_RATE, SLIPPAGE_PCT,
            )
            if state_changed:
                account.save_state()
            if lock_trigger and lock_trigger.startswith(PROFIT_LOCK_TRIGGER):
                closed = await account.close_position(
                    symbol, price, f'{entry_m} {lock_trigger}', is_manual=True,
                )
                if not closed:
                    account.log(
                        f'REALTIME_EXIT_RETRY_PENDING symbol={symbol} '
                        f'reason={lock_trigger} quote_ms={stamp}',
                        'WARNING',
                    )
                return bool(closed)

            if entry_m == 'CHANNEL_SWING' and position.get('side') == 'SHORT':
                from core.services.exits.short_oversold_exit import (
                    short_bearish_trend_active,
                )
                if short_bearish_trend_active(snapshot) is not False:
                    account.log(
                        f'CHANNEL_SHORT_TREND_HOLD symbol={symbol} '
                        'reason=BEARISH_MA_TREND_OR_INVALID_DATA',
                        'INFO',
                    )
                    return False

            return False

        if entry_m in ('CHANNEL_SWING', 'WILD_SWING'):
            frame = getattr(engine, '_channel_exit_frames', {}).get(symbol)
            if frame is not None and not frame.empty:
                snapshot, atr = cached_tick_indicators(frame, price, stamp)
                from core.config import SLIPPAGE_PCT, TAKER_FEE_RATE
                same_bar_exit, net_rate = same_bar_net_profit_exit(
                    position, price, stamp, snapshot,
                    TAKER_FEE_RATE, SLIPPAGE_PCT,
                )
                if same_bar_exit:
                    account.log(
                        f'REALTIME_EXIT symbol={symbol} '
                        f'reason=SAME_BAR_NET_PROFIT net_rate={net_rate:.6%} '
                        f'quote_ms={stamp} price={price}',
                        'WARNING',
                    )
                    closed = await account.close_position(
                        symbol, price, f'{entry_m} SAME_BAR_NET_PROFIT_EXIT',
                        is_manual=True,
                    )
                    if not closed:
                        account.log(
                            f'REALTIME_EXIT_RETRY_PENDING symbol={symbol} '
                            f'reason=SAME_BAR_NET_PROFIT_EXIT quote_ms={stamp}',
                            'WARNING',
                        )
                    return bool(closed)
                lock_trigger, state_changed = _evaluate_profit_lock_only(
                    position, meta, price, stamp, snapshot,
                    TAKER_FEE_RATE, SLIPPAGE_PCT,
                )
                if state_changed:
                    account.save_state()
                if (
                    lock_trigger == 'EXIT_BY_BREAKEVEN_LOCK'
                    or (lock_trigger and lock_trigger.startswith(PROFIT_LOCK_TRIGGER))
                ):
                    account.log(
                        f'REALTIME_EXIT symbol={symbol} reason={lock_trigger} '
                        f'quote_ms={stamp} price={price}',
                        'WARNING',
                    )
                    await account.close_position(
                        symbol, price, f'{entry_m} {lock_trigger}',
                        is_manual=True,
                    )
                    return True

            if not restricted_exit_policy:
                from core.services.exits.strict_true_pivot import (
                    confirmed_true_pivot_exit,
                    store_confirmed_pivot_exit,
                    CONFIRMED_PIVOT_PENDING_KEY,
                )
                pivot = confirmed_true_pivot_exit(position, frame)
                if pivot is None:
                    return False
                pivot_reason, pivot_details = pivot
                if not store_confirmed_pivot_exit(position, pivot):
                    account.log(
                        f'REALTIME_EXIT_BLOCK symbol={symbol} '
                        'reason=INVALID_CONFIRMED_PIVOT_TICKET',
                        'ERROR',
                    )
                    return False
                meta[CONFIRMED_PIVOT_PENDING_KEY] = copy.deepcopy(
                    position[CONFIRMED_PIVOT_PENDING_KEY]
                )
                account.save_state()
                account.log(
                    f'REALTIME_EXIT symbol={symbol} reason={pivot_reason} '
                    f'quote_ms={stamp} price={price} details={pivot_details}',
                    'WARNING',
                )
                closed = await account.close_position(
                    symbol, price, f'{entry_m} {pivot_reason}', is_manual=True,
                )
                if not closed:
                    account.log(
                        f'REALTIME_EXIT_RETRY_PENDING symbol={symbol} '
                        f'reason={pivot_reason} quote_ms={stamp}',
                        'WARNING',
                    )
                return bool(closed)
            return False

        try:
            frame = getattr(engine, '_channel_exit_frames', {}).get(symbol)
            if frame is not None and not frame.empty:
                snapshot, atr = cached_tick_indicators(frame, price, stamp)
                if entry_m == 'CHANNEL_SWING':
                    from core.gates.meme_coin_gate import MemeCoinGate
                    if MemeCoinGate.is_meme_coin(symbol):
                        side_m = str(position.get('side', '')).upper()
                        amb_fail, amb_fail_reason = MemeCoinGate.evaluate_ambush_failure_stop(
                            frame, price, side_m, symbol=symbol,
                        )
                        if amb_fail and amb_fail_reason:
                            account.log(
                                f'MEME_AMBUSH_FAIL_EXIT symbol={symbol} reason={amb_fail_reason} price={price}',
                                'WARNING',
                            )
                            await account.close_position(
                                symbol, price, f'Channel Swing {amb_fail_reason}',
                                is_manual=True,
                            )
                            return True
                        m_exit, m_exit_reason = MemeCoinGate.evaluate_meme_exit(
                            frame, price, side_m, symbol=symbol, position=position,
                        )
                        if m_exit and m_exit_reason:
                            from core.gates.holding_protection_gate import HoldingProtectionExitGate
                            is_allowed, valid_reason, valid_details = HoldingProtectionExitGate.validate_exit(
                                position, frame, price, m_exit_reason,
                                channel_swing=True,
                            )
                            if is_allowed:
                                account.log(
                                    f'MEME_EXIT symbol={symbol} reason={valid_reason} price={price}',
                                    'WARNING',
                                )
                                await account.close_position(
                                    symbol, price, f'Channel Swing {valid_reason}',
                                    is_manual=True,
                                )
                                return True
                            else:
                                account.log(
                                    f'HOLDING_PROTECTION_BLOCKED symbol={symbol} reason={m_exit_reason} '
                                    f'gate_reason={valid_reason}', 'INFO',
                                )

                    from core.gates.holding_protection_gate import (
                        CHANNEL_PEAK_EXIT_STATE_KEY,
                        INTRABAR_SPIKE_STATE_KEY,
                        HoldingProtectionExitGate,
                    )
                    from core.services.exits.peak_trailing_exit import channel_swing_exit_allowed
                    if INTRABAR_SPIKE_STATE_KEY in meta:
                        position[INTRABAR_SPIKE_STATE_KEY] = meta[INTRABAR_SPIKE_STATE_KEY]
                    if CHANNEL_PEAK_EXIT_STATE_KEY in meta:
                        position[CHANNEL_PEAK_EXIT_STATE_KEY] = copy.deepcopy(
                            meta[CHANNEL_PEAK_EXIT_STATE_KEY]
                        )
                    prior_spike_state = position.get(INTRABAR_SPIKE_STATE_KEY)
                    prior_peak_state = copy.deepcopy(
                        position.get(CHANNEL_PEAK_EXIT_STATE_KEY)
                    )
                    prot_exit, prot_details = HoldingProtectionExitGate.evaluate(
                        position, frame, price, True,
                    )
                    current_spike_state = position.get(INTRABAR_SPIKE_STATE_KEY)
                    current_peak_state = position.get(CHANNEL_PEAK_EXIT_STATE_KEY)
                    if (current_spike_state != prior_spike_state
                            or meta.get(INTRABAR_SPIKE_STATE_KEY) != current_spike_state):
                        if current_spike_state is not None:
                            meta[INTRABAR_SPIKE_STATE_KEY] = current_spike_state
                        account.save_state()
                    if current_peak_state != prior_peak_state:
                        if current_peak_state is not None:
                            meta[CHANNEL_PEAK_EXIT_STATE_KEY] = copy.deepcopy(
                                current_peak_state
                            )
                        account.save_state()
                    if prot_exit and channel_swing_exit_allowed(prot_exit):
                        is_allowed, valid_reason, valid_details = (
                            HoldingProtectionExitGate.validate_exit(
                                position, frame, price, prot_exit, prot_details,
                                channel_swing=True,
                            )
                        )
                        if is_allowed:
                            account.log(
                                f'HOLDING_PROTECTION_EXIT symbol={symbol} reason={valid_reason} '
                                f'quote_ms={stamp} price={price} details={valid_details}',
                                'WARNING',
                            )
                            await account.close_position(
                                symbol, price, f'Channel Swing {valid_reason}',
                                is_manual=True,
                            )
                            return True
                        account.log(
                            f'HOLDING_PROTECTION_BLOCKED symbol={symbol} reason={prot_exit} '
                            f'gate_reason={valid_reason}', 'INFO',
                        )
                    if prot_exit:
                        account.log(
                            f'HOLDING_PROTECTION_BLOCKED symbol={symbol} reason={prot_exit} '
                            'gate_reason=EXIT_NOT_IN_CHANNEL_ALLOWLIST', 'INFO',
                        )

                    from core.exits.peak_valley_exit import PeakValleyExit
                    peak_exit, peak_details = PeakValleyExit.evaluate(
                        position, frame, price,
                    )
                    if peak_exit and channel_swing_exit_allowed(peak_exit):
                        is_allowed, valid_reason, valid_details = HoldingProtectionExitGate.validate_exit(
                            position, frame, price, peak_exit, peak_details,
                            channel_swing=True,
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
                    elif peak_exit:
                        account.log(
                            f'HOLDING_PROTECTION_BLOCKED symbol={symbol} reason={peak_exit} '
                            'gate_reason=EXIT_NOT_IN_CHANNEL_ALLOWLIST', 'INFO',
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
                    from core.services.exits.peak_trailing_exit import channel_swing_exit_allowed
                    if (entry_m == 'CHANNEL_SWING' and lobster_hold
                            and not (gate_trigger == 'HARD_STOP_LOSS'
                                     or gate_trigger.startswith(PROFIT_LOCK_TRIGGER))):
                        account.log(
                            f'LOBSTER_PULLBACK_HOLD symbol={symbol} reason={gate_trigger} '
                            f'price={price} rule=WAIT_FOR_10PCT_PEAK_ROE_GIVEBACK', 'INFO',
                        )
                        return False
                    if (entry_m == 'CHANNEL_SWING'
                            and not channel_swing_exit_allowed(gate_trigger)
                            and not (lobster_hold and gate_trigger == 'HARD_STOP_LOSS')):
                        account.log(
                            f'HOLDING_PROTECTION_BLOCKED symbol={symbol} reason={gate_trigger} '
                            'gate_reason=EXIT_NOT_IN_CHANNEL_ALLOWLIST', 'INFO',
                        )
                        return False
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
                    # Lobster keeps running through every ordinary retracement.
                    # Its only strategy exit is the staged net-ROE giveback,
                    # which cannot arm until peak net ROE reaches 10%.
                    if lobster_hold:
                        return False
                    # 1. Hybrid Dynamic Exit (Stage 1 ATR trailing 1.3 ATR / 20% giveback, Stage 2 Ratchet 20%, KC Midline breach)
                    from core.services.exits.hybrid_dynamic_exit import evaluate_hybrid_dynamic_exit
                    hybrid_trigger, hybrid_state_changed, hybrid_diag = evaluate_hybrid_dynamic_exit(
                        position, meta, price, stamp, snapshot,
                        fee=TAKER_FEE_RATE, slippage=SLIPPAGE_PCT,
                    )
                    if hybrid_state_changed:
                        account.save_state()
                    if hybrid_trigger:
                        is_allowed, valid_reason, valid_details = HoldingProtectionExitGate.validate_exit(
                            position, frame, price, hybrid_trigger,
                            channel_swing=True,
                        )
                        if is_allowed:
                            account.log(
                                f'REALTIME_EXIT symbol={symbol} reason={valid_reason} '
                                f'quote_ms={stamp} price={price} diag={hybrid_diag}',
                                'WARNING',
                            )
                            await account.close_position(
                                symbol, price, f'Channel Swing {valid_reason}',
                                is_manual=True,
                            )
                            return True
                        else:
                            account.log(
                                f'HOLDING_PROTECTION_BLOCKED symbol={symbol} reason={hybrid_trigger} '
                                f'gate_reason={valid_reason}', 'INFO',
                            )

                    # 2. Holding Protection Veto on THREE_POINT_PIVOT:
                    # One-vote veto: If latest candle or price holds on favorable side of KC middle
                    # (close >= kc_middle for LONG, close <= kc_middle for SHORT), all regular pullback/pivot exits are vetoed!
                    from core.services.exits.peak_trailing_exit import three_point_pivot_exit
                    pivot = three_point_pivot_exit(position, snapshot)
                    if pivot is not None:
                        account.log(
                            f'REALTIME_EXIT symbol={symbol} reason=THREE_POINT_PIVOT '
                            f'quote_ms={stamp} price={price} evidence={pivot}',
                            'WARNING',
                        )
                        await account.close_position(
                            symbol, price, 'Channel Swing THREE_POINT_PIVOT',
                            is_manual=True,
                        )
                        return True
                    if meta.get('trend_exit_last_audit_bar') != snapshot.get('snapshot_bar_id'):
                        account.log(
                            f'TREND_EXIT_GATE symbol={symbol} side={position.get("side")} '
                            'rule=HYBRID_DYNAMIC_OR_MIDLINE_BREACH_ONLY '
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
                   'ma5_reversal_outside_seen','ma5_profit_lock_armed',
                   'ma5_reversal_rule_version','momentum_stall_bars'))
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
            if is_pivot_only_channel_symbol(symbol)
            else (frozenset() if entry_m == 'CHANNEL_SWING' else CHANNEL_SWING_EXIT_TRIGGERS)
        )
        if (entry_m == 'CHANNEL_SWING'
                and channel_strategy_exit_grace_active(position, meta, stamp / 1000)
                and trigger not in ('BEARISH_INSTANT_BREAKOUT', 'BULLISH_INSTANT_BREAKOUT')
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
                           'LIVE_FLASH_DUMP_EXIT',
                           'THREE_POINT_PIVOT', 'TWO_CLOSED_ADVERSE_ABNORMAL',
                           'WATERFALL_DROP', 'BEARISH_INSTANT_BREAKOUT',
                           'BULLISH_INSTANT_BREAKOUT',
                           'EXIT_DOJI_BEARISH_CONFIRMATION',
                           'EXIT_DOJI_BULLISH_CONFIRMATION',
                           SHORT_FALSE_BREAKOUT_TRIGGER,
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
        return False
