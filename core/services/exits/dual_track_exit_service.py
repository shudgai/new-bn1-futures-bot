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
    """2. 強趨勢斜率盾牌（MA5 Slope Shield）：
    若當前 MA5 斜率強勢順向（多單 MA5 斜率 > 0.04 ATR 向上，空單 < -0.04 ATR 向下），
    代表主升浪動能強勁，價格盤中回踩觸碰 MA5 視為正常抖動，強制禁止觸發平倉。
    """
    try:
        if frame is None or len(frame) < 2 or atr <= 0:
            return False
        curr_ma5 = float(frame.iloc[-1].get('ma5', 0.0))
        prev_ma5 = float(frame.iloc[-2].get('ma5', 0.0))
        if curr_ma5 <= 0 or prev_ma5 <= 0:
            return False
        slope_atr = (curr_ma5 - prev_ma5) / atr
        if side == 'LONG' and slope_atr > 0.04:
            return True
        elif side == 'SHORT' and slope_atr < -0.04:
            return True
    except (TypeError, ValueError, IndexError):
        pass
    return False


def is_candle_closed_ma5_break(frame, side: str, is_closed: bool = False) -> bool:
    """3. 平倉必須收線確認（Candle-Close Only）：
    一般均線防守必須等待當根 1m K 棒收盤確認跌破 MA5（is_closed=True 且 close < ma5 for LONG）才執行離場。
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


def detect_extreme_bar_emergency_exit(frame, quote: float, side: str, atr: float) -> bool:
    """4. 極端大陰棒反殺緊急避險（允許盤中即時砍倉）：
    - 當根陰線實體 >= 0.8 ATR 且摜破 MA5
    - 或出現長度 >= 1.5 倍實體之極端長影避雷針（且影線長度 >= 0.5 ATR）
    其餘普通回調一律抱緊！
    """
    if frame is None or frame.empty or atr <= 0 or quote <= 0:
        return False
    try:
        curr = frame.iloc[-1]
        open_p = float(curr.get('open', quote))
        high_p = max(float(curr.get('high', quote)), quote)
        low_p = min(float(curr.get('low', quote)), quote)
        ma5_p = float(curr.get('ma5', quote))
        body = abs(quote - open_p)

        if side == 'LONG':
            # 條件 A: 當根陰線實體 >= 0.8 ATR 且摜破 MA5
            if quote < open_p and body >= 0.8 * atr and quote < ma5_p:
                return True
            # 條件 B: 極端長上影避雷針 (上影線 >= 1.5 倍實體 且 上影線 >= 0.5 ATR)
            upper_wick = high_p - max(open_p, quote)
            if upper_wick >= 0.5 * atr and upper_wick >= 1.5 * max(body, 1e-9):
                return True
        elif side == 'SHORT':
            # 條件 A: 當根陽線實體 >= 0.8 ATR 且摜破 MA5
            if quote > open_p and body >= 0.8 * atr and quote > ma5_p:
                return True
            # 條件 B: 極端長下影避雷針 (下影線 >= 1.5 倍實體 且 下影線 >= 0.5 ATR)
            lower_wick = min(open_p, quote) - low_p
            if lower_wick >= 0.5 * atr and lower_wick >= 1.5 * max(body, 1e-9):
                return True
    except (TypeError, ValueError):
        pass
    return False


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

            # 2. 極端大陰棒反殺緊急避險（允許盤中即時砍倉）
            if detect_extreme_bar_emergency_exit(frame, current_price, side, atr):
                return 'EMERGENCY_EXTREME_ADVERSE_BAR'

            # 3. 強趨勢斜率盾牌（MA5 Slope Shield）：
            # 若當前 MA5 斜率強勢順向（多單 > 0.04 ATR 向上），禁止觸發平倉
            if is_ma5_slope_shield_active(frame, side, atr):
                return None

            # 4. 平倉必須收線確認（Candle-Close Only）：
            # 一般均線防守必須等待當根 1m K 棒收盤確認跌破 MA5
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
        position['cooldown_mode'] = 'NONE'
