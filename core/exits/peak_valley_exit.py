"""Peak and Valley Exit Module: Millisecond-level fractal and MA3 bend exit decisions."""
import math
from typing import Tuple, Optional, Dict, Any
import pandas as pd
from core.intelligence.spatial_brain import SpatialBrain
from core.gates.holding_protection_gate import (
    closed_peak_valley_reversal_evidence,
    confirmed_short_bottom_evidence,
    intrabar_peak_valley_reversal_evidence,
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

        # Classify a confirmed peak/valley before the generic pressure exit.
        # The same strong reversal can satisfy both; preserve the structural
        # reason when a pivot has actually formed.
        if side in ('LONG', 'SHORT') and math.isfinite(atr) and atr > 0:
            favorable_price = highest_price if side == 'LONG' else lowest_price
            favorable_move = (
                favorable_price - entry_price if side == 'LONG'
                else entry_price - favorable_price
            )
            meaningful_profit = (
                ((highest_price - entry_price) / entry_price >= 0.03
                 or favorable_move >= 0.5 * atr) if side == 'LONG'
                else ((entry_price - lowest_price) / entry_price >= 0.03
                      or favorable_move >= 0.5 * atr)
            )
            if meaningful_profit and current_bar_closed:
                reversal = closed_peak_valley_reversal_evidence(frame, side, atr)
                if reversal is not None:
                    reason = ('EXIT_LONG_ON_FRACTAL_PEAK' if side == 'LONG'
                              else 'EXIT_SHORT_ON_FRACTAL_VALLEY')
                    return reason, {
                        **reversal,
                        'fractal_top': side == 'LONG',
                        'fractal_bottom': side == 'SHORT',
                        'closed_reversal_confirmation': True,
                        'peak_gain_atr': favorable_move / atr,
                        'peak_roe': ((highest_price - entry_price) / entry_price
                                     if side == 'LONG' else 0.0),
                        'valley_roe': ((entry_price - lowest_price) / entry_price
                                       if side == 'SHORT' else 0.0),
                        'quote': quote,
                    }
            elif meaningful_profit and not current_bar_closed:
                pivot_price = (
                    max(highest_price, float(curr.get('high', quote)), quote)
                    if side == 'LONG'
                    else min(lowest_price, float(curr.get('low', quote)), quote)
                )
                evidence = intrabar_peak_valley_reversal_evidence(
                    position, frame, side, quote, atr, pivot_price, favorable_move,
                )
                if evidence is not None:
                    reason = ('EXIT_LONG_ON_FRACTAL_PEAK' if side == 'LONG'
                              else 'EXIT_SHORT_ON_FRACTAL_VALLEY')
                    return reason, {
                        **evidence,
                        'intrabar_pivot_confirmation': True,
                        'peak_gain_atr': favorable_move / atr,
                        'quote': quote,
                    }

            pressure = intrabar_adverse_pressure_evidence(frame, side, quote, atr)
            if pressure is not None:
                reason = (
                    'EXIT_LONG_ON_SELLING_PRESSURE'
                    if side == 'LONG' else 'EXIT_SHORT_ON_BUYING_PRESSURE'
                )
                return reason, pressure

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
            bottom = confirmed_short_bottom_evidence(frame)
            if bottom is not None:
                return 'EXIT_SHORT_ON_CONFIRMED_TROUGH', bottom

        return None, {}
