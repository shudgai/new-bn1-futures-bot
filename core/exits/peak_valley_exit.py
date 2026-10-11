"""Peak and Valley Exit Module: Millisecond-level fractal and MA3 bend exit decisions."""
import math
from typing import Tuple, Optional, Dict, Any
import pandas as pd
from core.intelligence.spatial_brain import SpatialBrain


def is_fractal_peak(bars: pd.DataFrame) -> bool:
    """Check if the previous bar forms a 3-bar fractal peak."""
    if len(bars) < 3:
        return False
    b1, b2, b3 = bars.iloc[-3], bars.iloc[-2], bars.iloc[-1]
    h1, h2, h3 = float(b1['high']), float(b2['high']), float(b3['high'])
    return h2 > h1 and h2 >= h3


def is_fractal_valley(bars: pd.DataFrame) -> bool:
    """Check if the previous bar forms a 3-bar fractal valley."""
    if len(bars) < 3:
        return False
    b1, b2, b3 = bars.iloc[-3], bars.iloc[-2], bars.iloc[-1]
    l1, l2, l3 = float(b1['low']), float(b2['low']), float(b3['low'])
    return l2 < l1 and l2 <= l3


class PeakValleyExit:
    """Evaluates peak and valley exits with zero-lag fractal breach and micro-MA3 bends."""

    @staticmethod
    def evaluate(position: Dict[str, Any], frame: pd.DataFrame,
                 quote: float) -> Tuple[Optional[str], Dict[str, Any]]:
        """Evaluates exit condition for a position given current live quote and 1m frame."""
        side = position.get('side')
        entry_price = float(position.get('entry_price', 0.0))
        meta = position.get('metadata') or {}

        if frame is None or len(frame) < 3:
            return None, {}

        # Spatial perception for MA3 slope and bandwidth
        context = SpatialBrain.analyze(frame, quote)
        ma3_slope = context.ma3_slope

        curr = frame.iloc[-1]
        prev = frame.iloc[-2]
        
        prev_open = float(prev.get('open', 0.0))
        prev_close = float(prev.get('close', 0.0))
        prev_high = float(prev.get('high', 0.0))
        prev_low = float(prev.get('low', 0.0))
        prev_mid = (prev_open + prev_close) / 2.0
        
        kc_mid = float(curr.get('kc_middle', curr.get('kc_basis', 0.0)))
        atr = float(prev.get('atr', curr.get('atr', 0.0)))

        # 0. PROFIT GATE: 必須已有顯著利潤，嚴禁開倉未拉開利潤就秒平！
        # 多單最高浮盈 (Peak ROE) 須達至少 3.0% 或最高價距開倉價 >= 0.5 * ATR
        # 空單最高浮盈 (Valley ROE) 須達至少 3.0% 或最低價距開倉價 >= 0.5 * ATR
        highest_price = float(position.get('highest_price') or position.get('peak_price') or entry_price)
        lowest_price = float(position.get('lowest_price') or position.get('trough_price') or entry_price)
        curr_high = float(curr.get('high', quote))
        curr_low = float(curr.get('low', quote))
        if quote > highest_price or curr_high > highest_price:
            highest_price = max(quote, curr_high)
        if quote < lowest_price or curr_low < lowest_price:
            lowest_price = min(quote, curr_low)

        peak_roe_long = (highest_price - entry_price) / entry_price if entry_price > 0 else 0.0
        peak_gain_atr_long = (highest_price - entry_price) / atr if atr > 0 else 0.0
        has_meaningful_profit_long = (peak_roe_long >= 0.03) or (peak_gain_atr_long >= 0.5)

        peak_roe_short = (entry_price - lowest_price) / entry_price if entry_price > 0 else 0.0
        peak_gain_atr_short = (entry_price - lowest_price) / atr if atr > 0 else 0.0
        has_meaningful_profit_short = (peak_roe_short >= 0.03) or (peak_gain_atr_short >= 0.5)

        # 1. LONG PEAK EXIT
        if side == 'LONG':
            # 若部位從未達到衝高利潤門檻，100% 禁用頂峰轉折平倉
            if not has_meaningful_profit_long:
                return None, {}

            curr_o = float(curr.get('open', quote))
            live_high = max(curr_high, quote)
            live_body = abs(quote - curr_o)
            upper_wick = live_high - max(curr_o, quote)
            if (math.isfinite(atr) and atr > 0 and upper_wick >= 0.5 * atr
                    and upper_wick > 2.0 * live_body):
                return 'EXIT_LONG_ON_UPPER_WICK_REJECTION', {
                    'upper_wick': upper_wick, 'body': live_body, 'atr': atr,
                    'quote': quote, 'peak_roe': peak_roe_long,
                    'peak_gain_atr': peak_gain_atr_long,
                }

            # A. 3-Bar 分形見頂且即時價格跌破前棒實體中點/低點，或當根衝頂後盤中跌破中點
            fractal_top = is_fractal_peak(frame)
            curr_h = float(curr.get('high', quote))
            curr_rise = curr_h - curr_o
            is_intrabar_peak = (curr_rise >= 1.5 * atr) and (quote <= (curr_o + curr_h) / 2.0 or (curr_h - quote) >= 0.35 * curr_rise)
            
            if (fractal_top and (quote < prev_mid or quote < prev_low)) or is_intrabar_peak:
                return 'EXIT_LONG_ON_FRACTAL_PEAK', {'fractal_top': True, 'quote': quote, 'peak_roe': peak_roe_long}

            # B. 多單浮盈時 MA3 微斜率首度轉平/下勾 (Slope_MA3 <= 0)，或大陰線貫穿跌破中軌
            is_in_profit = (quote > entry_price)
            is_peak_reversal = (curr_o - quote >= 0.35 * atr and quote < kc_mid)
            if (is_in_profit and ma3_slope <= 0) or is_peak_reversal:
                reason = 'EXIT_LONG_ON_FRACTAL_PEAK' if is_peak_reversal else 'EXIT_LONG_ON_MA3_BEND_DOWN'
                return reason, {'ma3_slope': ma3_slope, 'quote': quote, 'peak_roe': peak_roe_long}

        # 2. SHORT VALLEY EXIT
        elif side == 'SHORT':
            # 若部位從未達到底谷利潤門檻，100% 禁用底谷轉折平倉
            if not has_meaningful_profit_short:
                return None, {}

            curr_o = float(curr.get('open', quote))
            live_low = min(curr_low, quote)
            live_body = abs(quote - curr_o)
            live_high = max(curr_high, quote)
            upper_wick = live_high - max(curr_o, quote)
            if math.isfinite(atr) and atr > 0 and upper_wick >= 0.4 * atr:
                return None, {
                    'upper_wick_hold': True, 'upper_wick': upper_wick,
                    'body': live_body, 'atr': atr, 'quote': quote,
                }
            lower_wick = min(curr_o, quote) - live_low
            if (math.isfinite(atr) and atr > 0 and lower_wick >= 0.5 * atr
                    and lower_wick > 2.0 * live_body):
                return 'EXIT_SHORT_ON_LOWER_WICK_REJECTION', {
                    'lower_wick': lower_wick, 'body': live_body, 'atr': atr,
                    'quote': quote, 'valley_roe': peak_roe_short,
                    'peak_gain_atr': peak_gain_atr_short,
                    'upper_wick': upper_wick,
                }

            # A. A confirmed valley needs a strong bullish body, not a wick-only quote spike.
            fractal_bottom = is_fractal_valley(frame)
            candle_closed = bool(curr.get('is_closed', False))
            reversal_price = float(curr.get('close', quote)) if candle_closed else quote
            candle_high = max(float(curr.get('high', reversal_price)), reversal_price)
            candle_low = min(float(curr.get('low', reversal_price)), reversal_price)
            reversal_ma5 = float(curr.get('ma5', 0.0))
            reversal_body = reversal_price - curr_o
            candle_range = candle_high - candle_low
            valid_body_geometry = (
                candle_range > 0 and candle_high >= max(curr_o, reversal_price)
                and candle_low <= min(curr_o, reversal_price)
            )
            body_ratio = reversal_body / candle_range if valid_body_geometry else 0.0
            closed_body_reversal = (
                candle_closed and reversal_body >= 0.35 * atr
                and reversal_price > prev_mid
            )
            live_body_reversal = (
                not candle_closed and math.isfinite(atr) and atr > 0
                and reversal_body > 0.35 * atr and reversal_price > prev_mid
            )
            above_reversal_levels = (
                math.isfinite(reversal_ma5) and reversal_ma5 > 0
                and math.isfinite(kc_mid) and kc_mid > 0
                and reversal_price > reversal_ma5 and reversal_price > kc_mid
            )

            if (fractal_bottom and (closed_body_reversal or live_body_reversal)
                    and body_ratio >= 0.60 and above_reversal_levels):
                info = {
                    'fractal_bottom': True, 'quote': quote,
                    'valley_roe': peak_roe_short, 'body_ratio': body_ratio,
                    'prev_mid': prev_mid, 'ma5': reversal_ma5,
                    'kc_middle': kc_mid, 'atr': atr,
                    'body': reversal_body,
                    'upper_wick': candle_high - max(curr_o, reversal_price),
                    'peak_gain_atr': peak_gain_atr_short,
                }
                info['flip_to_long_authorized'] = True
                info['clear_cooldown'] = True
                return 'EXIT_SHORT_ON_FRACTAL_VALLEY', info

        return None, {}
