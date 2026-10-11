"""Peak and Valley Exit Module: Millisecond-level fractal and MA3 bend exit decisions."""
import math
from typing import Tuple, Optional, Dict, Any
import pandas as pd
from core.intelligence.spatial_brain import SpatialBrain
from core.gates.holding_protection_gate import (
    closed_peak_valley_reversal_evidence,
    intrabar_adverse_pressure_evidence,
)


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

        if frame is None or len(frame) < 2:
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

        if side in ('LONG', 'SHORT') and math.isfinite(atr) and atr > 0:
            pressure = intrabar_adverse_pressure_evidence(frame, side, quote, atr)
            if pressure is not None:
                reason = (
                    'EXIT_LONG_ON_SELLING_PRESSURE'
                    if side == 'LONG' else 'EXIT_SHORT_ON_BUYING_PRESSURE'
                )
                return reason, pressure

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
        current_bar_closed = (
            curr.get('is_closed', False) is True
            or (type(curr.get('is_closed', False)).__name__ == 'bool_'
                and bool(curr.get('is_closed', False)))
        )

        # 1. LONG PEAK EXIT
        if side == 'LONG':
            # 若部位從未達到衝高利潤門檻，100% 禁用頂峰轉折平倉
            if not has_meaningful_profit_long:
                return None, {}

            curr_o = float(curr.get('open', quote))
            live_high = max(curr_high, quote)
            live_body = abs(quote - curr_o)
            upper_wick = live_high - max(curr_o, quote)
            if (current_bar_closed and math.isfinite(atr) and atr > 0 and upper_wick >= 0.5 * atr
                    and upper_wick > 2.0 * live_body):
                return 'EXIT_LONG_ON_UPPER_WICK_REJECTION', {
                    'upper_wick': upper_wick, 'body': live_body, 'atr': atr,
                    'quote': quote, 'peak_roe': peak_roe_long,
                    'peak_gain_atr': peak_gain_atr_long,
                }

            # A. 3-Bar 分形見頂且即時價格跌破前棒實體中點/低點，或當根衝頂後盤中跌破中點
            fractal_top = is_fractal_peak(frame)
            curr_h = max(float(curr.get('high', quote)), quote)
            pivot_price = max(highest_price, curr_h)
            favorable_move = pivot_price - entry_price
            retracement = pivot_price - quote
            decisive_reversal_body = curr_o - quote >= 0.35 * atr
            is_intrabar_peak = (
                favorable_move >= 0.5 * atr
                and decisive_reversal_body
                and retracement >= max(0.35 * atr, 0.2 * favorable_move)
            )

            if is_intrabar_peak:
                return 'EXIT_LONG_ON_FRACTAL_PEAK', {
                    'intrabar_pivot_confirmation': True,
                    'pivot_price': pivot_price, 'favorable_move': favorable_move,
                    'atr': atr, 'peak_roe': peak_roe_long,
                    'peak_gain_atr': peak_gain_atr_long, 'quote': quote,
                }
            
            if (fractal_top and curr_o - quote >= 0.35 * atr
                    and (quote < prev_mid or quote < prev_low)):
                pivot_price = float(prev.get('high', curr_h))
                return 'EXIT_LONG_ON_FRACTAL_PEAK', {
                    'fractal_top': True, 'intrabar_pivot_confirmation': True,
                    'pivot_price': pivot_price,
                    'favorable_move': pivot_price - entry_price,
                    'atr': atr, 'peak_gain_atr': peak_gain_atr_long,
                    'peak_roe': peak_roe_long, 'quote': quote,
                }

            if current_bar_closed:
                reversal = closed_peak_valley_reversal_evidence(frame, side, atr)
                if reversal is not None:
                    return 'EXIT_LONG_ON_FRACTAL_PEAK', {
                        **reversal, 'closed_reversal_confirmation': True,
                        'peak_roe': peak_roe_long,
                        'peak_gain_atr': peak_gain_atr_long,
                    }

            # A small MA3 flattening is ordinary pullback noise, not a peak.
            # Keep only the independent large-body structural reversal below.
            is_peak_reversal = (curr_o - quote >= 0.35 * atr and quote < kc_mid)
            if is_peak_reversal:
                return 'EXIT_LONG_ON_FRACTAL_PEAK', {
                    'ma3_slope': ma3_slope, 'quote': quote,
                    'peak_roe': peak_roe_long, 'peak_gain_atr': peak_gain_atr_long,
                    'atr': atr,
                }

        # 2. SHORT VALLEY EXIT
        elif side == 'SHORT':
            # 若部位從未達到底谷利潤門檻，100% 禁用底谷轉折平倉
            if not has_meaningful_profit_short:
                return None, {}

            curr_o = float(curr.get('open', quote))
            candle_closed = current_bar_closed
            reversal_price = float(curr.get('close', quote)) if candle_closed else quote
            candle_low = float(curr.get('low', reversal_price))
            candle_high = float(curr.get('high', reversal_price))
            if not candle_closed:
                candle_low = min(candle_low, reversal_price)
                candle_high = max(candle_high, reversal_price)
            body = abs(reversal_price - curr_o)
            upper_wick = candle_high - max(curr_o, reversal_price)
            pivot_price = min(lowest_price, candle_low)
            favorable_move = entry_price - pivot_price
            upward_retracement = quote - pivot_price
            decisive_reversal_body = quote - curr_o >= 0.35 * atr
            if (not candle_closed and atr > 0 and favorable_move >= 0.5 * atr
                    and decisive_reversal_body
                    and upward_retracement >= max(0.35 * atr, 0.2 * favorable_move)):
                return 'EXIT_SHORT_ON_FRACTAL_VALLEY', {
                    'intrabar_pivot_confirmation': True,
                    'pivot_price': pivot_price,
                    'favorable_move': favorable_move,
                    'atr': atr, 'valley_roe': peak_roe_short,
                    'peak_gain_atr': peak_gain_atr_short, 'quote': quote,
                }
            if (math.isfinite(atr) and atr > 0 and upper_wick >= 0.4 * atr):
                return None, {
                    'upper_wick_hold': True, 'upper_wick': upper_wick,
                    'body': body, 'atr': atr, 'quote': quote,
                }
            lower_wick = min(curr_o, reversal_price) - candle_low
            reversal_ma5 = float(curr.get('ma5', 0.0))
            strong_closed_reversal = (
                candle_closed and reversal_price > curr_o
                and math.isfinite(atr) and atr > 0
                and reversal_price - curr_o >= 0.35 * atr
                and math.isfinite(reversal_ma5) and reversal_ma5 > 0
                and reversal_price > reversal_ma5
                and upper_wick < 0.4 * atr
            )
            if (candle_closed and math.isfinite(atr) and atr > 0
                    and strong_closed_reversal
                    and lower_wick >= 0.5 * atr and lower_wick > 2.0 * body):
                return 'EXIT_SHORT_ON_LOWER_WICK_REJECTION', {
                    'lower_wick': lower_wick, 'body': body, 'atr': atr,
                    'quote': quote, 'valley_roe': peak_roe_short,
                    'peak_gain_atr': peak_gain_atr_short,
                    'upper_wick': upper_wick,
                }

            if candle_closed:
                reversal = closed_peak_valley_reversal_evidence(frame, side, atr)
                if reversal is not None:
                    return 'EXIT_SHORT_ON_FRACTAL_VALLEY', {
                        **reversal, 'closed_reversal_confirmation': True,
                        'valley_roe': peak_roe_short,
                        'peak_gain_atr': peak_gain_atr_short,
                        'quote': quote,
                    }

            # A valley reversal is confirmed only by the completed candle body.
            fractal_bottom = candle_closed and is_fractal_valley(frame)
            reversal_ma5 = float(curr.get('ma5', 0.0))
            reversal_body = reversal_price - curr_o
            candle_range = candle_high - candle_low
            valid_body_geometry = (
                candle_range > 0 and candle_high >= max(curr_o, reversal_price)
                and candle_low <= min(curr_o, reversal_price)
            )
            body_ratio = reversal_body / candle_range if valid_body_geometry else 0.0
            if (fractal_bottom and valid_body_geometry
                    and math.isfinite(atr) and atr > 0
                    and reversal_body >= 0.35 * atr
                    and math.isfinite(reversal_ma5) and reversal_ma5 > 0
                    and reversal_price > curr_o and reversal_price > reversal_ma5
                    and upper_wick < 0.4 * atr):
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
