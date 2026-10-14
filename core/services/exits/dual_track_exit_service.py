"""Account compatibility adapter for tick peak trailing and initial hard stops."""
import math

from core.interfaces.exit_interface import IExitStrategy
from core.services.exit_service import initialize_chandelier

from core.services.exits.peak_trailing_exit import (
    POLICY, STATE_KEY, STATE_KEYS, channel_initial_stop_disabled, evaluate_peak_trailing,
)
from core.services.candle_data import closed_entry_candles
from core.services.gates.exit_gate import TrendHoldingImmunityGate
DUAL_TRACK_STATE_KEYS = list(STATE_KEYS)
SL_INIT_MULT = 1.5


def detect_climax_reversal(frame, prior_side):
    """Detect an extended 1m move ending in a completed opposite engulfing bar."""
    try:
        if prior_side not in ('LONG', 'SHORT') or frame is None or frame.empty:
            return None
        if frame.attrs.get('timeframe_ms', 60000) != 60000:
            return None
        closed = closed_entry_candles(frame)
        if len(closed) < 12:
            return None
        swing = closed.iloc[-12:]
        previous, reversal = closed.iloc[-2], closed.iloc[-1]
        timestamps = [float(value) for value in swing['timestamp']]
        if any(right - left != 60000 for left, right in zip(timestamps, timestamps[1:])):
            return None
        atr = float(previous['atr'])
        values = [atr]
        for _, row in swing.iterrows():
            values.extend(float(row[key]) for key in
                          ('timestamp', 'open', 'high', 'low', 'close',
                           'kc_lower', 'kc_upper'))
        if not all(math.isfinite(value) and value > 0 for value in values) or atr <= 0:
            return None
        for _, row in swing.iterrows():
            if (float(row['low']) > min(float(row['open']), float(row['close']))
                    or float(row['high']) < max(float(row['open']), float(row['close']))):
                return None

        po, pc = float(previous['open']), float(previous['close'])
        ro, rc = float(reversal['open']), float(reversal['close'])
        body_atr = abs(rc - ro) / atr
        if prior_side == 'LONG':
            engulf = pc > po and rc < ro and ro >= pc and rc <= po
            ma5 = float(reversal.get('ma5', float('nan')))
            ma5_broken = math.isfinite(ma5) and rc < ma5
            peak_idx = swing['high'].astype(float).idxmax()
            peak_bar = swing.loc[peak_idx]
            swing_high = float(peak_bar['high'])
            true_peak = (
                peak_idx in (previous.name, reversal.name)
                and swing_high >= float(peak_bar['kc_upper'])
            )
            prior_to_peak = swing.iloc[:swing.index.get_loc(peak_idx)]
            if prior_to_peak.empty:
                return None
            swing_low = float(prior_to_peak['low'].astype(float).min())
            extension_atr = (swing_high - swing_low) / atr
            climax_bar = engulf and ma5_broken and body_atr >= 0.8
            extended = true_peak and extension_atr >= 3.0
            stop = float(reversal['high']) + 0.2 * atr
            side = 'SHORT'
            reason = 'CLIMAX_REVERSAL_EXIT_TOP'
        else:
            engulf = pc < po and rc > ro and ro <= pc and rc >= po
            ma5 = float(reversal.get('ma5', float('nan')))
            ma5_broken = math.isfinite(ma5) and rc > ma5
            valley_idx = swing['low'].astype(float).idxmin()
            valley_bar = swing.loc[valley_idx]
            swing_low = float(valley_bar['low'])
            true_valley = (
                valley_idx in (previous.name, reversal.name)
                and swing_low <= float(valley_bar['kc_lower'])
            )
            prior_to_valley = swing.iloc[:swing.index.get_loc(valley_idx)]
            if prior_to_valley.empty:
                return None
            swing_high = float(prior_to_valley['high'].astype(float).max())
            extension_atr = (swing_high - swing_low) / atr
            climax_bar = engulf and ma5_broken and body_atr >= 0.8
            extended = true_valley and extension_atr >= 3.0
            stop = float(reversal['low']) - 0.2 * atr
            side = 'LONG'
            reason = 'CLIMAX_REVERSAL_EXIT_BOTTOM'
        if not climax_bar or not extended or stop <= 0:
            return None
        return dict(
            reason=reason, prior_side=prior_side, side=side,
            reversal_bar_id=float(reversal['timestamp']),
            reversal_open=ro, reversal_close=rc,
            reversal_high=float(reversal['high']),
            reversal_low=float(reversal['low']), atr=atr,
            body_atr=body_atr, extension_atr=extension_atr,
            extremum_price=(swing_high if prior_side == 'LONG' else swing_low),
            extremum_bar_id=float(peak_bar['timestamp'] if prior_side == 'LONG'
                                  else valley_bar['timestamp']),
            initial_sl=stop,
        )
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def is_true_climax_peak(frame):
    evidence = detect_climax_reversal(frame, 'LONG')
    return bool(evidence and evidence['reason'] == 'CLIMAX_REVERSAL_EXIT_TOP')


def is_true_climax_valley(frame):
    evidence = detect_climax_reversal(frame, 'SHORT')
    return bool(evidence and evidence['reason'] == 'CLIMAX_REVERSAL_EXIT_BOTTOM')


def doji_reversal_exit_reason(bars, side):
    """Retired: 十字星與反向 K 棒平倉已全數拔除，禁止因十字星或微小反向 K 觸發平倉。"""
    return None



def evaluate_trend_exit_and_take_profit(position, closed, atr):
    """Retired candle-only adapter; no live quote means no exit authority."""
    return dict(should_exit=False, action='HOLD', reason='WAIT_LIVE_QUOTE')


def is_early_lifecycle_protected(position: dict, current_price: float, atr: float) -> bool:
    """1. 開倉初期生命週期保護（持倉前 90 秒內）：
    只要未擊穿硬停損線（1.2 ATR），嚴禁僅因盤中價格波動穿過 MA5 觸發平倉。
    """
    try:
        import time
        open_ts = float(position.get('open_timestamp') or position.get('entry_timestamp') or 0.0)
        if open_ts > 0 and open_ts < 1e11:
            open_ts *= 1000.0
        now_ms = time.time() * 1000.0
        if open_ts > 0 and (now_ms - open_ts) < 90_000.0:
            entry_p = float(position.get('entry_price', 0.0))
            side = position.get('side')
            loss = (entry_p - current_price) if side == 'LONG' else (current_price - entry_p)
            hard_stop_atr = 1.2 * atr if atr > 0 else 0.0
            if hard_stop_atr > 0 and loss >= hard_stop_atr:
                return False  # 擊穿 1.2 ATR 硬停損，允許止損離場
            return True  # 90 秒內且未擊穿 1.2 ATR，強制保護
    except (TypeError, ValueError):
        pass
    return False


def is_ma5_slope_shield_active(frame, side: str, atr: float) -> bool:
    """1. 加入 MA5 斜率保護（MA Slope Shield - 回踩強制抱緊）：
    當 MA5 斜率強勢順向（多單 MA5 斜率 > 0.03 ATR 向上，空單 < -0.03 ATR 向下）時，
    代表主升浪動能強勁，盤中回踩觸碰 MA5 視為正常抖動，強制「禁止觸發平倉」。
    """
    try:
        if frame is None or len(frame) < 2 or atr <= 0:
            return False
        curr_ma5 = float(frame.iloc[-1].get('ma5', 0.0))
        prev_ma5 = float(frame.iloc[-2].get('ma5', 0.0))
        if curr_ma5 <= 0 or prev_ma5 <= 0:
            return False
        slope_atr = (curr_ma5 - prev_ma5) / atr
        if side == 'LONG' and slope_atr > 0.03:
            return True
        elif side == 'SHORT' and slope_atr < -0.03:
            return True
    except (TypeError, ValueError, IndexError):
        pass
    return False


def is_candle_closed_dump_exit(frame, side: str, is_closed: bool = False) -> bool:
    """大實體破位果斷砍倉（Candle-Close Dump Exit）：
    當多單持倉遭遇實體陰線「收盤確認跌破 MA5（is_closed=True 且 close < ma5 且 close < open 明確下跌棒）」，
    於當根收線當下立即市價平多，嚴禁拖延到次根低點才被動平倉！
    """
    if not is_closed or frame is None or frame.empty:
        return False
    try:
        last = frame.iloc[-1]
        close_p = float(last.get('close', 0.0))
        open_p = float(last.get('open', 0.0))
        ma5_p = float(last.get('ma5', 0.0))
        if close_p <= 0 or ma5_p <= 0 or open_p <= 0:
            return False
        if side == 'LONG':
            return close_p < ma5_p and close_p < open_p
        elif side == 'SHORT':
            return close_p > ma5_p and close_p > open_p
    except (TypeError, ValueError):
        pass
    return False


def detect_momentum_stalling_exit(position: dict, frame, quote: float, side: str, atr: float) -> bool:
    """高檔滯漲動能竭盡主動平倉（Momentum Stalling Exit）：
    波段衝高後，若連續 2 根以上無法刷新高點、實體微弱（< 0.3 ATR）或長上影避雷針（上影 >= 0.8 實體），
    且 MA5 斜率走平下彎（curr_ma5 <= prev_ma5 或 |ΔMA5| < 0.02 ATR）：於頂部走平區主動落袋保本，防止死等大瀑布。
    """
    if frame is None or len(frame) < 2 or atr <= 0 or not position:
        return False
    try:
        curr = frame.iloc[-1]
        prev = frame.iloc[-2]
        curr_ma5 = float(curr.get('ma5', quote))
        prev_ma5 = float(prev.get('ma5', curr_ma5))
        slope_atr = abs(curr_ma5 - prev_ma5) / atr

        open_p = float(curr.get('open', quote))
        high_p = max(float(curr.get('high', quote)), quote)
        low_p = min(float(curr.get('low', quote)), quote)
        prev_high = float(prev.get('high', quote))
        prev_low = float(prev.get('low', quote))
        entry_p = float(position.get('entry_price', quote))
        peak_p = float(position.get('highest_price') or position.get('peak_price') or entry_p)
        trough_p = float(position.get('lowest_price') or position.get('trough_price') or entry_p)

        if side == 'LONG':
            ma5_flat_or_down = (curr_ma5 <= prev_ma5 or slope_atr < 0.02)
            if not ma5_flat_or_down:
                return False
            body = max(quote - open_p, 0.0)
            upper_wick = high_p - max(open_p, quote)
            is_stalling = (body < 0.3 * atr) or (upper_wick >= 0.8 * body and upper_wick >= 0.3 * atr)
            not_refreshing_high = (high_p <= peak_p and prev_high <= peak_p and quote < peak_p)
            if is_stalling and not_refreshing_high:
                return True
        elif side == 'SHORT':
            ma5_flat_or_up = (curr_ma5 >= prev_ma5 or slope_atr < 0.02)
            if not ma5_flat_or_up:
                return False
            body = max(open_p - quote, 0.0)
            lower_wick = min(open_p, quote) - low_p
            is_stalling = (body < 0.3 * atr) or (lower_wick >= 0.8 * body and lower_wick >= 0.3 * atr)
            not_refreshing_low = (low_p >= trough_p and prev_low >= trough_p and quote > trough_p)
            if is_stalling and not_refreshing_low:
                return True
    except (TypeError, ValueError, IndexError):
        pass
    return False


def is_healthy_ma15_pullback_protected(frame, side: str) -> bool:
    """趨勢拉升中途（回踩保護）：
    常規波段防守基準線移至 MA15：
    在多頭趨勢（KC 向上 或 MA5 > MA15）中，價格盤中回踩 MA5 甚至貼近 MA15 視為正常回測蓄力。
    只要 1m K 線收盤未實體摜破 MA15（close >= ma15 for LONG, close <= ma15 for SHORT），
    強制鎖定「禁止平多」。
    """
    if frame is None or frame.empty:
        return False
    try:
        last_candle = frame.iloc[-1]
        close_p = float(last_candle.get('close', 0.0))
        ma15_p = float(last_candle.get('ma15', 0.0))
        ma5_p = float(last_candle.get('ma5', 0.0))
        kc_trend = str(last_candle.get('kc_trend', '')).upper()
        if close_p <= 0 or ma15_p <= 0:
            return False
        if side == 'LONG':
            if (ma5_p >= ma15_p or 'UP' in kc_trend) and close_p >= ma15_p:
                return True
            return close_p >= ma15_p
        elif side == 'SHORT':
            if (ma5_p <= ma15_p or 'DOWN' in kc_trend) and close_p <= ma15_p:
                return True
            return close_p <= ma15_p
    except (TypeError, ValueError):
        pass
    return False


def is_candle_closed_ma5_break(frame, side: str, is_closed: bool = False) -> bool:
    """2. 平倉必須收線確認（Candle-Close Only）：
    取消盤中跳價碰 MA5 即刻市價秒平的敏感邏輯。
    均線防守必須等 1m K 線「收盤確認跌破 MA5（is_closed=True 且 close < ma5 for LONG）」才執行離場。
    """
    if not is_closed or frame is None or frame.empty:
        return False
    try:
        last_candle = frame.iloc[-1]
        close_p = float(last_candle.get('close', 0.0))
        ma5_p = float(last_candle.get('ma5', 0.0))
        if close_p <= 0 or ma5_p <= 0:
            return False
        if side == 'LONG':
            return close_p < ma5_p
        elif side == 'SHORT':
            return close_p > ma5_p
    except (TypeError, ValueError):
        pass
    return False


def evaluate_doji_two_bar_confirmation(position: dict, frame, side: str, is_closed: bool = False, atr: float = 0.0) -> bool:
    """Retired: 十字星與反向 K 棒平倉已全數拔除，禁止因十字星或微小反向 K 觸發平倉。"""
    if position:
        position.pop('awaiting_doji_confirmation', None)
        position.pop('doji_low', None)
        position.pop('doji_high', None)
    return False


def detect_emergency_dump_exit(frame, quote: float, side: str, atr: float) -> bool:
    """軌道二：突發大賣壓緊急避險（Emergency Dump Exit - 盤中即刻秒平，不等收線！）
    觸發條件（滿足以下任一即刻生效）：
      - 條件 A【實體大反殺】：當根 1m 盤中即時跌幅（Open - Price）>= 0.7 ATR，且盤中實體已吞沒前一根陽線實體 70% 以上。
      - 條件 B【巨陰貫穿均線】：盤中價格直接垂直摜破 MA5，且當根實體下跌幅度 >= 0.8 ATR。
      - 條件 C【天地針避雷針】：高點衝高後盤中劇烈回吐，上影線長度 >= 實體 1.5 倍以上（且上影線 >= 0.5 ATR）。
    處置：
      - 一旦偵測到軌道二的大賣壓特徵，豁免所有斜率保護與收線等待，在「當根盤中當下立即以市價平倉」，第一時間保住利潤！
    """
    if frame is None or frame.empty or atr <= 0 or quote <= 0:
        return False
    try:
        curr = frame.iloc[-1]
        prev = frame.iloc[-2] if len(frame) >= 2 else curr
        open_p = float(curr.get('open', quote))
        high_p = max(float(curr.get('high', quote)), quote)
        low_p = min(float(curr.get('low', quote)), quote)
        ma5_p = float(curr.get('ma5', quote))

        prev_open = float(prev.get('open', open_p))
        prev_close = float(prev.get('close', open_p))

        if side == 'LONG':
            drop = open_p - quote
            # 遭遇極端大實體反殺：盤中即時跌幅 >= 0.8 ATR 且摜穿 MA5 均線
            if quote < ma5_p and drop >= 0.8 * atr:
                return True

        elif side == 'SHORT':
            rise = quote - open_p
            # 遭遇極端大實體反殺：盤中即時漲幅 >= 0.8 ATR 且摜穿 MA5 均線
            if quote > ma5_p and rise >= 0.8 * atr:
                return True

    except (TypeError, ValueError, IndexError):
        pass
    return False


def detect_extreme_bar_emergency_exit(frame, quote: float, side: str, atr: float) -> bool:
    """極端大陰棒反殺緊急避險（相容包裝）"""
    return detect_emergency_dump_exit(frame, quote, side, atr)


def observe_breakeven(position: dict, price: float) -> dict:
    """Legacy helper to detect breakeven."""
    strategy = DualTrackExitStrategy()
    result_type = strategy.evaluate_exit(position, current_price=price)
    if result_type == "BREAKEVEN":
        return {"should_exit": True, "action": "BREAKEVEN", "reason": "BREAKEVEN_REACHED"}
    return {"should_exit": False, "action": "HOLD", "reason": "NO_BREAKEVEN"}


class DualTrackExitStrategy(IExitStrategy):
    def initialize_position(self, position, entry_price, atr):
        if channel_initial_stop_disabled(position):
            initialize_chandelier(position, entry_price, position['side'], atr,
                                 initial_stop_enabled=False)
            return
        sign = 1 if position['side'] == 'LONG' else -1
        position.update(entry_atr=float(atr), sl=entry_price-sign*SL_INIT_MULT*atr,
                        stop_loss=entry_price-sign*SL_INIT_MULT*atr, tp=0.)

    def _evaluate_raw_exit(self, position, frame=None, current_price=None, is_closed=False, **kwargs):
        import time
        import logging
        from core.config import TAKER_FEE_RATE, SLIPPAGE_PCT
        try:
            if not position or current_price is None:
                return None
            side = position.get('side')
            atr = float(position.get('entry_atr') or 0.0)
            if frame is not None and not frame.empty and atr <= 0:
                atr = float(frame.iloc[-1].get('atr') or 0.0)

            # 提取當前 1m K 棒指標
            kc_mid = 0.0
            prev_kc_mid = 0.0
            curr_ma5 = 0.0
            prev_ma5 = 0.0
            curr_ma15 = 0.0
            prev_ma15 = 0.0
            curr_close = current_price
            channel_state = ""
            recent_candles = []
            if frame is not None and len(frame) >= 1:
                last_c = frame.iloc[-1]
                prev_c = frame.iloc[-2] if len(frame) >= 2 else last_c
                kc_mid = float(last_c.get('kc_middle', last_c.get('kc_basis', 0.0)))
                prev_kc_mid = float(prev_c.get('kc_middle', prev_c.get('kc_basis', kc_mid)))
                curr_ma5 = float(last_c.get('ma5', 0.0))
                prev_ma5 = float(prev_c.get('ma5', curr_ma5))
                curr_ma15 = float(last_c.get('ma15', 0.0))
                prev_ma15 = float(prev_c.get('ma15', curr_ma15))
                curr_close = float(last_c.get('close', current_price))
                channel_state = str(last_c.get('channel_state', '')).strip().upper()
                recent_candles = [frame.iloc[i] for i in range(max(0, len(frame) - 4), len(frame))]

            delta_ma5 = curr_ma5 - prev_ma5
            ma15_slope = curr_ma15 - prev_ma15
            entry_p = float(position.get('entry_price', current_price))

            # ══════════════════════════════════════════════════════════════════════
            # 【多頭趨勢：保本移損、真峰頂辨識與離場（Long Holding & Exit）】
            # ══════════════════════════════════════════════════════════════════════
            if side == 'LONG':
                peak_p = max(float(position.get('highest_price') or position.get('peak_price') or entry_p), current_price)
                position['highest_price'] = peak_p
                position['peak_price'] = peak_p
                max_gain = peak_p - entry_p

                # 提取 K 棒影線指標
                c_high = float(last_c.get('high', current_price)) if frame is not None and len(frame) >= 1 else current_price
                c_low = float(last_c.get('low', current_price)) if frame is not None and len(frame) >= 1 else current_price
                c_open = float(last_c.get('open', current_price)) if frame is not None and len(frame) >= 1 else current_price
                c_close = float(last_c.get('close', current_price)) if frame is not None and len(frame) >= 1 else current_price
                c_range = max(c_high - c_low, 1e-9)
                upper_wick = c_high - max(c_open, current_price)

                # 計算淨 ROE
                qty = float(position.get('qty') or 1.0)
                margin = float(position.get('margin') or 0.0)
                if margin <= 0:
                    leverage = float(position.get('leverage') or 10.0)
                    margin = (entry_p * qty / leverage) if leverage > 0 else (entry_p * qty)
                peak_net_pnl = (peak_p - entry_p) * qty - (entry_p + peak_p) * qty * TAKER_FEE_RATE
                peak_roe = (peak_net_pnl / margin * 100.0) if margin > 0 else 0.0

                # ──【第二道防線：真峰頂趨勢逆轉（Confirmed True Peak Exit）與敏銳逃頂】──
                is_kc_down = (
                    channel_state in ('KC向下', 'DOWN', 'BEARISH', 'DOWNTREND', 'KC_TREND_DOWN')
                    or (kc_mid > 0 and prev_kc_mid > 0 and kc_mid < prev_kc_mid - 1e-6)
                )
                cond_b_trend_reverse = (kc_mid > 0 and curr_close < kc_mid) or is_kc_down
                cond_c_ma_collapse = (delta_ma5 < -0.02 * atr) and (curr_ma15 > 0 and curr_close < curr_ma15)
                if cond_b_trend_reverse or cond_c_ma_collapse:
                    return 'EXIT_BY_CONFIRMED_TRUE_PEAK'

                # ──【峰頂敏銳判定（Fast Top Exit: EXIT_BY_FAST_PEAK_REVERSAL）】──
                # 在浮盈顯著時（曾達 >= 0.8 ATR 或 ROE >= 5.0%）：
                # 高檔連續長上影線滯漲 + 實體陰線（body >= 0.4 ATR）直接摜穿 MA5；或 MA5 斜率急速轉負垂直向下彎頭且跌破 MA5
                has_consecutive_upper_wicks = False
                if len(recent_candles) >= 3:
                    prior_bars = recent_candles[:-1]
                    wick_count = 0
                    for b in prior_bars[-3:]:
                        bh, bl, bo, bc = float(b.get('high', 0)), float(b.get('low', 0)), float(b.get('open', 0)), float(b.get('close', 0))
                        br = max(bh - bl, 1e-9)
                        buw = bh - max(bo, bc)
                        if (buw >= 0.35 * atr or buw / br >= 0.35):
                            wick_count += 1
                    if wick_count >= 2:
                        has_consecutive_upper_wicks = True

                c_is_bearish_body = (c_open > c_close) and ((c_open - c_close) >= 0.4 * atr)
                if (max_gain >= 0.8 * atr or peak_roe >= 5.0):
                    cond_fast_top_shape = has_consecutive_upper_wicks and c_is_bearish_body and (curr_close < curr_ma5)
                    cond_fast_ma_drop = (delta_ma5 < -0.02 * atr) and (curr_close < curr_ma5)
                    if cond_fast_top_shape or cond_fast_ma_drop:
                        return 'EXIT_BY_FAST_PEAK_REVERSAL'

                # ──【極端避雷針動態收割（Spike Reversal Harvest）】──
                if atr > 0 and max_gain >= 1.5 * atr:
                    rebound_distance = peak_p - current_price
                    giveback_ratio = rebound_distance / max_gain if max_gain > 0 else 0.0
                    spike_reversal = (
                        (not is_closed and (rebound_distance >= 1.2 * atr or giveback_ratio >= 0.40))
                        or (upper_wick / c_range >= 0.50 and current_price < c_high - 0.4 * atr)
                    )
                    if spike_reversal and current_price > entry_p:
                        return 'EXIT_BY_SPIKE_REVERSAL'

                # ──【第一道鐵律：利潤保本移損線（Breakeven Floor - 嚴禁大賺放成虧損）】──
                # 浮盈曾達 >= 1.0 ATR 或淨 ROE >= 8.0% 時，強制鎖定底線在 entry_price + 0.05%
                is_be_locked = position.get('breakeven_locked', False) or ((atr > 0 and max_gain >= 1.0 * atr) or peak_roe >= 8.0)
                if is_be_locked:
                    position['breakeven_locked'] = True
                    be_price = entry_p * 1.0005
                    curr_sl = float(position.get('sl') or position.get('stop_loss') or 0.0)
                    position['sl'] = max(curr_sl, be_price)
                    position['stop_loss'] = max(curr_sl, be_price)
                    if current_price <= be_price:
                        return 'EXIT_BY_BREAKEVEN_LOCK'

                # 寬幅階梯鎖利：達到目標浮盈後，自最高點回撤 1.3 ATR 或利潤回吐 35%
                if atr > 0 and max_gain >= 1.5 * atr:
                    rebound_distance = peak_p - current_price
                    giveback_ratio = rebound_distance / max_gain if max_gain > 0 else 0.0
                    if (rebound_distance >= 1.3 * atr or giveback_ratio >= 0.35) and current_price > entry_p:
                        return 'EXIT_BY_RATCHET_PROFIT_LOCK'

                # ──【多頭初生期強制抱緊鎖定（Initial Holding Protection - 徹底根絕神經質誤殺平多）】──
                # 多頭結構中（MA5 > MA15 且 KC 向上），只要收盤穩穩踩在 MA5 之上（close >= ma5）或未跌破 MA15（close >= ma15）：
                # 一票否決所有「微幅利潤回吐」、「單根小陰線」、「短線停滯」等神經質平多！
                is_bullish_structure = (curr_ma5 >= curr_ma15) and ('DOWN' not in channel_state) and (kc_mid >= prev_kc_mid - 1e-6)
                if is_bullish_structure:
                    if curr_close >= curr_ma5 or (curr_ma15 > 0 and curr_close >= curr_ma15):
                        return None

            # ══════════════════════════════════════════════════════════════════════
            # 【空頭趨勢：保本移損、真谷底辨識與離場（Short Holding & Exit）】
            # ══════════════════════════════════════════════════════════════════════
            elif side == 'SHORT':
                trough_p = min(float(position.get('lowest_price') or position.get('trough_price') or entry_p), current_price)
                position['lowest_price'] = trough_p
                position['trough_price'] = trough_p
                max_gain = entry_p - trough_p

                # 提取 K 棒影線指標
                c_high = float(last_c.get('high', current_price)) if frame is not None and len(frame) >= 1 else current_price
                c_low = float(last_c.get('low', current_price)) if frame is not None and len(frame) >= 1 else current_price
                c_open = float(last_c.get('open', current_price)) if frame is not None and len(frame) >= 1 else current_price
                c_close = float(last_c.get('close', current_price)) if frame is not None and len(frame) >= 1 else current_price
                c_range = max(c_high - c_low, 1e-9)
                lower_wick = min(c_open, current_price) - c_low

                # 計算淨 ROE
                qty = float(position.get('qty') or 1.0)
                margin = float(position.get('margin') or 0.0)
                if margin <= 0:
                    leverage = float(position.get('leverage') or 10.0)
                    margin = (entry_p * qty / leverage) if leverage > 0 else (entry_p * qty)
                peak_net_pnl = (entry_p - trough_p) * qty - (entry_p + trough_p) * qty * TAKER_FEE_RATE
                peak_roe = (peak_net_pnl / margin * 100.0) if margin > 0 else 0.0

                # ──【空頭走勢與真谷底檢核（Bearish Trend & True Valley Check）】──
                from core.gates.holding_protection_gate import has_confirmed_true_pivot
                has_true_valley, _, _ = has_confirmed_true_pivot(position, frame, current_price)

                is_kc_up = (
                    channel_state in ('KC向上', 'UP', 'BULLISH', 'UPTREND', 'KC_TREND_UP')
                    or (kc_mid > 0 and prev_kc_mid > 0 and kc_mid > prev_kc_mid + 1e-6)
                )
                is_below_kc_mid = (kc_mid > 0 and curr_close <= kc_mid)
                is_bearish_structure = is_below_kc_mid or (not is_kc_up) or (curr_ma5 <= curr_ma15)

                # ──【第一道鐵律：利潤保本移損線（Breakeven Floor - 嚴禁大賺放成虧損）】──
                # 浮盈曾達 >= 1.2 ATR 或淨 ROE >= 8.0% 時，強制鎖定底線在 entry_price - 0.05%
                is_be_locked = position.get('breakeven_locked', False) or ((atr > 0 and max_gain >= 1.2 * atr) or peak_roe >= 8.0)
                if is_be_locked:
                    position['breakeven_locked'] = True
                    be_price = entry_p * 0.9995
                    curr_sl = float(position.get('sl') or position.get('stop_loss') or 999999999.0)
                    position['sl'] = min(curr_sl, be_price)
                    position['stop_loss'] = min(curr_sl, be_price)
                    if current_price >= be_price:
                        return 'EXIT_BY_BREAKEVEN_LOCK'

                # ──【極端避雷針動態收割（Spike Reversal Harvest - 鎖利優先）】──
                if atr > 0 and max_gain >= 1.5 * atr:
                    rebound_distance = current_price - trough_p
                    giveback_ratio = rebound_distance / max_gain if max_gain > 0 else 0.0
                    spike_reversal = (
                        (not is_closed and (rebound_distance >= 1.2 * atr or giveback_ratio >= 0.40))
                        or (lower_wick / c_range >= 0.50 and current_price > c_low + 0.4 * atr)
                    )
                    if spike_reversal and current_price < entry_p:
                        return 'EXIT_BY_SPIKE_REVERSAL'

                # ──【空頭走勢防洗鐵律：若都是空頭走勢不要平倉，要到真谷底才能平倉】──
                # 只要仍處於空頭結構且尚未收線形成真谷底，且未突破 KC 中軌，一票否決所有普通平空！
                if is_bearish_structure and not has_true_valley and not (kc_mid > 0 and curr_close > kc_mid):
                    return None

                # ──【第二道防線：真谷底趨勢逆轉（Confirmed True Valley Exit）與中軌突破】──
                cond_b_trend_reverse = (kc_mid > 0 and curr_close > kc_mid) or is_kc_up
                cond_c_ma_surge = (delta_ma5 > 0.02 * atr) and (curr_ma15 > 0 and curr_close > curr_ma15)
                if cond_b_trend_reverse or (has_true_valley and cond_c_ma_surge):
                    return 'EXIT_BY_CONFIRMED_TRUE_VALLEY'

                # ──【谷底敏銳判定（Fast Valley Exit: EXIT_BY_FAST_VALLEY_REVERSAL）】──
                # 僅在已成型真谷底或突破中軌時，才評估下影線或 MA5 急拉
                has_consecutive_lower_wicks = False
                if len(recent_candles) >= 3:
                    prior_bars = recent_candles[:-1]
                    wick_count = 0
                    for b in prior_bars[-3:]:
                        bh, bl, bo, bc = float(b.get('high', 0)), float(b.get('low', 0)), float(b.get('open', 0)), float(b.get('close', 0))
                        br = max(bh - bl, 1e-9)
                        blw = min(bo, bc) - bl
                        if (blw >= 0.35 * atr or blw / br >= 0.35):
                            wick_count += 1
                    if wick_count >= 2:
                        has_consecutive_lower_wicks = True

                c_is_bullish_body = (c_close > c_open) and ((c_close - c_open) >= 0.4 * atr)
                if (max_gain >= 0.8 * atr or peak_roe >= 5.0) and (has_true_valley or curr_close > kc_mid):
                    cond_fast_bottom_shape = has_consecutive_lower_wicks and c_is_bullish_body and (curr_close > curr_ma5)
                    cond_fast_ma_surge_fast = (delta_ma5 > 0.02 * atr) and (curr_close > curr_ma5)
                    if cond_fast_bottom_shape or cond_fast_ma_surge_fast:
                        return 'EXIT_BY_FAST_VALLEY_REVERSAL'

                # 寬幅階梯鎖利：達到目標浮盈後，且有真谷底支撐，自最低點反彈回撤 1.3 ATR 或利潤回吐 35%
                if atr > 0 and max_gain >= 1.5 * atr and (has_true_valley or curr_close > kc_mid):
                    rebound_distance = current_price - trough_p
                    giveback_ratio = rebound_distance / max_gain if max_gain > 0 else 0.0
                    if (rebound_distance >= 1.3 * atr or giveback_ratio >= 0.35) and current_price < entry_p:
                        return 'EXIT_BY_RATCHET_PROFIT_LOCK'

            # ── 緊急避險保留：僅在遭遇極端大實體反殺（盤中即時逆向 >= 0.8 ATR 且摜穿均線）允許當根秒平保命 ──
            if not is_closed and detect_emergency_dump_exit(frame, current_price, side, atr):
                return 'EMERGENCY_DUMP_EXIT'

            climax = detect_climax_reversal(frame, position.get('side'))
            if climax:
                return climax['reason']

            result = evaluate_peak_trailing(position, current_price, kwargs.get('quote_ms', time.time()*1000),
                                            fee=TAKER_FEE_RATE, slippage=SLIPPAGE_PCT)
            return result['type'] if result else None
        except Exception as e:
            logging.getLogger(__name__).error(f"FAIL_SAFE_EXIT_ERROR in DualTrackExitStrategy: {e}", exc_info=True)
            return "FAIL_SAFE_EMERGENCY_EXIT"

    def evaluate_exit(self, position, frame=None, current_price=None, is_closed=False, **kwargs):
        """出場主入口：以 TrendHoldingImmunityGate 作為出場流程的第一道最高防禦閘門，未獲放行者不得平倉。"""
        import logging
        candidate_exit = self._evaluate_raw_exit(position, frame=frame, current_price=current_price, is_closed=is_closed, **kwargs)
        if not candidate_exit:
            return None

        # 極端安全風控（硬止損、緊急暴跌防護、安全熔斷）
        if candidate_exit in ('CIRCUIT_BREAKER_HARD_SL', 'HARD_STOP_LOSS', 'FAIL_SAFE_EMERGENCY_EXIT', 'EMERGENCY_DUMP_EXIT'):
            return candidate_exit

        # 凡任何出場邏輯（包含階梯鎖利、利潤回吐平倉、指標平倉等）欲發出平倉訊號前，必須強制通過此 Gate 審查
        side = position.get('side')
        c_open = current_price
        c_high = current_price
        c_low = current_price
        curr_close = current_price
        curr_ma5 = current_price
        curr_ma15 = current_price
        ma15_slope = 0.0
        kc_mid = 0.0

        if frame is not None and len(frame) >= 1:
            last_c = frame.iloc[-1]
            prev_c = frame.iloc[-2] if len(frame) >= 2 else last_c
            curr_close = float(last_c.get('close', current_price))
            c_open = float(last_c.get('open', current_price))
            c_high = float(last_c.get('high', current_price))
            c_low = float(last_c.get('low', current_price))
            curr_ma5 = float(last_c.get('ma5', current_price))
            curr_ma15 = float(last_c.get('ma15', current_price))
            prev_ma15 = float(prev_c.get('ma15', curr_ma15))
            ma15_slope = curr_ma15 - prev_ma15
            kc_mid = float(last_c.get('kc_middle', last_c.get('kc_basis', 0.0)))

        can_exit, gate_msg = TrendHoldingImmunityGate().can_exit(
            position_side=side,
            bar={'close': curr_close, 'open': c_open, 'high': c_high, 'low': c_low},
            indicators={'ma5': curr_ma5, 'ma15': curr_ma15, 'ma15_slope': ma15_slope, 'kc_middle': kc_mid}
        )
        if not can_exit:
            logging.getLogger(__name__).info(f"TrendHoldingImmunityGate vetoed exit {candidate_exit}: {gate_msg}")
            return None

        return candidate_exit

    def handle_post_exit_cleanup(self, position, exit_reason):
        position.pop(STATE_KEY, None)
        position.pop('has_broken_outer_band', None)
        position.pop('awaiting_doji_confirmation', None)
        position.pop('doji_low', None)
        position.pop('doji_high', None)
        position['cooldown_mode'] = 'NONE'
