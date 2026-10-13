"""Account compatibility adapter for tick peak trailing and initial hard stops."""
import math

from core.interfaces.exit_interface import IExitStrategy
from core.services.exit_service import initialize_chandelier

from core.services.exits.peak_trailing_exit import (
    POLICY, STATE_KEY, STATE_KEYS, channel_initial_stop_disabled, evaluate_peak_trailing,
)
from core.services.candle_data import closed_entry_candles
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
    """Return the bar-close reversal exit for two consecutive completed 1m bars."""
    if side not in ('LONG', 'SHORT') or not isinstance(bars, (list, tuple)) or len(bars) < 2:
        return None
    previous, current = bars[-2:]
    try:
        def value(bar, short, long):
            return float(bar.get(short, bar.get(long)))

        prev_ms = value(previous, 'ms', 'timestamp')
        curr_ms = value(current, 'ms', 'timestamp')
        po, ph, pl, pc = (value(previous, short, long) for short, long in
                          (('o', 'open'), ('h', 'high'), ('l', 'low'), ('c', 'close')))
        co, cc = (value(current, short, long) for short, long in
                  (('o', 'open'), ('c', 'close')))
        values = (prev_ms, curr_ms, po, ph, pl, pc, co, cc)
        if (not all(math.isfinite(item) for item in values)
                or any(item <= 0 for item in values)
                or curr_ms - prev_ms != 60_000
                or ph < max(po, pc) or pl > min(po, pc) or ph <= pl):
            return None
        doji_body_ratio = abs(pc - po) / (ph - pl + 1e-9)
        if doji_body_ratio > 0.35:
            return None
        if (side == 'LONG' and cc < co) or (side == 'SHORT' and cc > co):
            return 'EXIT_DOJI_REVERSAL_CONFIRMED'
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
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
    """二、 十字星出場必須「雙棒確認」（Two-Bar Top Confirmation）：
    1. 嚴禁「單根十字星立即平倉」：
       - 盤中或收線出現 Doji / 十字星時，當根僅作內部標記（awaiting_doji_confirmation = True），持倉強制繼續抱住。
    2. 必須等「次根出現反現實體確認」才准平多：
       - 只有在次根 1m K 線收線時（is_closed=True），同時滿足：
         * 次根 K 棒收為「明確實體陰線（close < open for LONG, close > open for SHORT）」。
         * 次根陰線收盤價「實體跌破前一根十字星的最低點（close < doji_low for LONG, close > doji_high for SHORT）」。
       - 若次根 K 棒繼續收陽、收綠十字、或向上創新高，立即「作廢並清除」十字星警示，繼續順勢抱緊讓利潤奔跑！
    """
    if not position or frame is None or len(frame) < 2:
        return False
    try:
        curr = frame.iloc[-1]
        prev = frame.iloc[-2]

        def _is_doji_bar(bar):
            o = float(bar.get('open', 0.0))
            c = float(bar.get('close', 0.0))
            h = float(bar.get('high', 0.0))
            l = float(bar.get('low', 0.0))
            span = h - l
            body = abs(c - o)
            if span <= 0:
                return False
            return (body / span <= 0.35) or (atr > 0 and body <= 0.25 * atr)

        # 若已在等待次根確認
        if position.get('awaiting_doji_confirmation'):
            if not is_closed:
                return False  # 盤中未收線，繼續抱住

            curr_open = float(curr.get('open', 0.0))
            curr_close = float(curr.get('close', 0.0))

            if side == 'LONG':
                doji_low = float(position.get('doji_low', curr_open))
                if curr_close < curr_open and curr_close < doji_low:
                    position.pop('awaiting_doji_confirmation', None)
                    position.pop('doji_low', None)
                    return True
                else:
                    position.pop('awaiting_doji_confirmation', None)
                    position.pop('doji_low', None)
                    return False
            elif side == 'SHORT':
                doji_high = float(position.get('doji_high', curr_open))
                if curr_close > curr_open and curr_close > doji_high:
                    position.pop('awaiting_doji_confirmation', None)
                    position.pop('doji_high', None)
                    return True
                else:
                    position.pop('awaiting_doji_confirmation', None)
                    position.pop('doji_high', None)
                    return False

        # 若目前未在等待確認，檢查最新收線之 K 線是否為十字星
        check_bar = curr if is_closed else prev
        if _is_doji_bar(check_bar):
            position['awaiting_doji_confirmation'] = True
            if side == 'LONG':
                position['doji_low'] = float(check_bar.get('low', 0.0))
            else:
                position['doji_high'] = float(check_bar.get('high', 0.0))
            return False

    except (TypeError, ValueError, IndexError):
        pass
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
            body = max(drop, 0.0)
            upper_wick = high_p - max(open_p, quote)
            prev_bull_body = max(prev_close - prev_open, 0.0)

            # 條件 A【實體大反殺】：跌幅 >= 0.7 ATR 且 吞沒前一根陽線實體 70% 以上
            if drop >= 0.7 * atr and prev_bull_body > 0 and drop >= 0.70 * prev_bull_body:
                return True

            # 條件 B【巨陰貫穿均線】：盤中價格直接摜破 MA5，且當根下跌幅度 >= 0.8 ATR
            if quote < ma5_p and drop >= 0.8 * atr:
                return True

            # 條件 C【天地針避雷針】：高點衝高後劇烈回吐，上影線 >= 1.5 倍實體 且 上影線 >= 0.5 ATR
            if upper_wick >= 0.5 * atr and upper_wick >= 1.5 * max(body, 1e-9):
                return True

        elif side == 'SHORT':
            rise = quote - open_p
            body = max(rise, 0.0)
            lower_wick = min(open_p, quote) - low_p
            prev_bear_body = max(prev_open - prev_close, 0.0)

            # 條件 A【實體大反殺】：漲幅 >= 0.7 ATR 且 吞沒前一根陰線實體 70% 以上
            if rise >= 0.7 * atr and prev_bear_body > 0 and rise >= 0.70 * prev_bear_body:
                return True

            # 條件 B【巨陽貫穿均線】：盤中價格直接摜破 MA5，且當根上漲幅度 >= 0.8 ATR
            if quote > ma5_p and rise >= 0.8 * atr:
                return True

            # 條件 C【天地針避雷針】：低點探底後劇烈回吐，下影線 >= 1.5 倍實體 且 下影線 >= 0.5 ATR
            if lower_wick >= 0.5 * atr and lower_wick >= 1.5 * max(body, 1e-9):
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

    def evaluate_exit(self, position, frame=None, current_price=None, is_closed=False, **kwargs):
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

            # 1. 開倉初期生命週期保護（前 90 秒內）：
            # 只要未擊穿 1.2 ATR 硬停損線，嚴禁僅因盤中價格波動穿過 MA5 平倉
            if is_early_lifecycle_protected(position, current_price, atr):
                return None

            # 2. 軌道二：突發大賣壓緊急避險（Emergency Dump Exit - 盤中即刻秒平，不等收線！）
            # 條件 A 大反殺吞沒、條件 B 巨陰摜破 MA5、條件 C 天地針
            # 豁免所有斜率保護與收線等待，在當根盤中當下立即以市價平倉！
            if detect_emergency_dump_exit(frame, current_price, side, atr):
                return 'EMERGENCY_DUMP_EXIT'

            # 3. 階梯利潤鎖定（Ratchet）：浮盈達 1.5 ATR 後，硬止損自動上推至保本位以上，鎖定贏面！
            if atr > 0:
                entry_p = float(position.get('entry_price', current_price))
                if side == 'LONG':
                    peak_p = max(float(position.get('highest_price') or position.get('peak_price') or entry_p), current_price)
                    position['highest_price'] = peak_p
                    if (peak_p - entry_p) >= 1.5 * atr:
                        be_level = entry_p + 0.1 * atr
                        if current_price <= be_level:
                            return 'EXIT_BY_RATCHET_PROFIT_LOCK'
                elif side == 'SHORT':
                    trough_p = min(float(position.get('lowest_price') or position.get('trough_price') or entry_p), current_price)
                    position['lowest_price'] = trough_p
                    if (entry_p - trough_p) >= 1.5 * atr:
                        be_level = entry_p - 0.1 * atr
                        if current_price >= be_level:
                            return 'EXIT_BY_RATCHET_PROFIT_LOCK'

            # 4. 十字星出場必須「雙棒確認」（Two-Bar Top Confirmation）：
            # 嚴禁單根十字星立即平倉！當根僅標記，次根收陰跌破十字星最低點才平倉；否則清除警示抱緊！
            if evaluate_doji_two_bar_confirmation(position, frame, side, is_closed=is_closed, atr=atr):
                return 'TWO_BAR_DOJI_REVERSAL_EXIT'

            # 5. 衝高滯漲主動平倉（Momentum Stalling Exit）：
            # 連續 2 根無法創新高 + 避雷針上影線 + MA5 斜率走平下彎
            if detect_momentum_stalling_exit(position, frame, current_price, side, atr):
                return 'MOMENTUM_STALLING_EXIT'

            # 6. MA5 斜率護航（MA Slope Shield - 回踩強制抱緊）：
            # 當 MA5 斜率強勢順向（多單 MA5 斜率 > 0.03 ATR 向上，空單 < -0.03 ATR 向下）時，
            # 盤中回踩觸碰 MA5 視為正常抖動，強制「禁止觸發平倉」。
            if is_ma5_slope_shield_active(frame, side, atr):
                return None

            # 7. 常規波段防守基準線移至 MA15（健康回踩強制抱緊）：
            # 在多頭趨勢中，只要 1m K 線收盤未實體摜破 MA15，一律視為健康回測蓄力，強制禁止平多！
            if is_healthy_ma15_pullback_protected(frame, side):
                return None

            # 8. 大實體破位果斷砍倉（Candle-Close Dump Exit）：
            # 當遭遇實體陰線收盤確認跌破 MA15/MA5，於當根收線當下立即砍倉！
            if is_closed and is_candle_closed_dump_exit(frame, side, is_closed=True):
                return 'CANDLE_CLOSE_DUMP_EXIT'

            # 9. 平倉必須收線確認（Candle-Close Only）：
            # 均線防守必須等 1m K 線收盤確認跌破 MA5 才執行離場。
            if is_closed and is_candle_closed_ma5_break(frame, side, is_closed=True):
                return 'CONFIRMED_MA5_CLOSE_BREAK'

            climax = detect_climax_reversal(frame, position.get('side'))
            if climax:
                return climax['reason']

            result = evaluate_peak_trailing(position, current_price, kwargs.get('quote_ms', time.time()*1000),
                                            fee=TAKER_FEE_RATE, slippage=SLIPPAGE_PCT)
            return result['type'] if result else None
        except Exception as e:
            logging.getLogger(__name__).error(f"FAIL_SAFE_EXIT_ERROR in DualTrackExitStrategy: {e}", exc_info=True)
            return "FAIL_SAFE_EMERGENCY_EXIT"

    def handle_post_exit_cleanup(self, position, exit_reason):
        position.pop(STATE_KEY, None)
        position.pop('has_broken_outer_band', None)
        position.pop('awaiting_doji_confirmation', None)
        position.pop('doji_low', None)
        position.pop('doji_high', None)
        position['cooldown_mode'] = 'NONE'
