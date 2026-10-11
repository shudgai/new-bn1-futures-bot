"""Holding Protection Exit Gate: Protects active positions from premature pullback exits.

Enforces an explicit exit whitelist:
1. EXIT_BY_EXTREME_WATERFALL: An adverse body >= 1.5*ATR that crosses KC middle.
2. EXIT_BY_CIRCUIT_BREAKER_HARD_SL: Hard Stop Loss or market circuit breaker.
3. EXIT_BY_RATCHET_PROFIT_LOCK: Net ROE ratchets at 2%, 4%, and 7% peak thresholds.
4. EXIT_BY_VERIFIED_FRACTAL_PEAK / EXIT_BY_VERIFIED_FRACTAL_VALLEY:
   Preconditioned on meaningful profit (ROE >= 3.0% or gain >= 0.5*ATR),
   3-bar fractal peak/valley with confirmed body break of previous midpoint.
5. Closed-bar directional reversal evidence for peak/valley exits.

All normal breathing / pullbacks (MA touch, upper/lower wick breathing, minor momentum turns)
are strictly REJECTED.
"""
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


def _current_bar_is_closed(frame):
    if frame is None or frame.empty:
        return False
    value = frame.iloc[-1].get('is_closed', False)
    return value is True or (type(value).__name__ == 'bool_' and bool(value))


def top_engulfing_flip_evidence(frame):
    """Validate a closed, extended bearish engulfing candle for long-to-short flip."""
    if frame is None or len(frame) < 2 or not _current_bar_is_closed(frame):
        return None
    try:
        current, previous = frame.iloc[-1], frame.iloc[-2]
        opening, close, ma5, ma15 = (
            float(current[key]) for key in ('open', 'close', 'ma5', 'ma15')
        )
        atr = float(previous['atr'])
        previous_open = float(previous['open'])
        previous_close = float(previous['close'])
        previous_low = float(previous['low'])
        values = (opening, close, ma5, ma15, atr, previous_open,
                  previous_close, previous_low)
        if (not all(math.isfinite(value) and value > 0 for value in values)
                or atr <= 0 or previous_close <= previous_open):
            return None
        body = opening - close
        if (close - ma15 <= 1.2 * atr or body < 0.4 * atr
                or close >= ma5 or opening < previous_close
                or close >= previous_open):
            return None
        return {
            'open': opening,
            'close': close,
            'ma5': ma5,
            'ma15': ma15,
            'previous_close': previous_close,
            'previous_low': previous_low,
            'atr': atr,
            'body_atr': body / atr,
            'extension_atr': (close - ma15) / atr,
        }
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def exhaustion_reversal_evidence(frame, side):
    """Detect a closed reversal body after recent weak candles or rejection wicks."""
    if (frame is None or len(frame) < 2 or side not in ('LONG', 'SHORT')
            or not _current_bar_is_closed(frame)):
        return None
    try:
        current = frame.iloc[-1]
        previous = frame.iloc[-2]
        opening, close, high, low, ma5, atr = (
            tuple(float(current[key]) for key in ('open', 'close', 'high', 'low', 'ma5'))
            + (float(previous['atr']),)
        )
        current_values = (opening, close, high, low, ma5, atr)
        if (not all(math.isfinite(value) and value > 0 for value in current_values)
                or low > min(opening, close) or high < max(opening, close)
                or atr <= 0):
            return None

        precursors = []
        for offset in (2, 3):
            if len(frame) < offset:
                continue
            candle = frame.iloc[-offset]
            if 'is_closed' in frame.columns:
                value = candle.get('is_closed', False)
                if not (value is True or (
                    type(value).__name__ == 'bool_' and bool(value)
                )):
                    continue
            candle_open, candle_close, candle_high, candle_low = (
                float(candle[key]) for key in ('open', 'close', 'high', 'low')
            )
            candle_atr = float(candle.get('atr', atr))
            values = (candle_open, candle_close, candle_high, candle_low, candle_atr)
            if (not all(math.isfinite(value) and value > 0 for value in values)
                    or candle_low > min(candle_open, candle_close)
                    or candle_high < max(candle_open, candle_close)
                    or candle_atr <= 0):
                continue
            body = abs(candle_close - candle_open)
            rejection_wick = (
                min(candle_open, candle_close) - candle_low
                if side == 'SHORT'
                else candle_high - max(candle_open, candle_close)
            )
            if body < 0.25 * candle_atr or rejection_wick >= 0.35 * candle_atr:
                precursors.append({
                    'bar_id': candle.get('timestamp'),
                    'high': candle_high,
                    'low': candle_low,
                    'body': body,
                    'rejection_wick': rejection_wick,
                    'atr': candle_atr,
                })

        if not precursors:
            return None
        body = abs(close - opening)
        if side == 'SHORT':
            reversal = close > opening and close > ma5 and (
                body >= 0.3 * atr or any(close > candle['high'] for candle in precursors)
            )
        else:
            reversal = close < opening and close < ma5 and (
                body >= 0.3 * atr or any(close < candle['low'] for candle in precursors)
            )
        if not reversal:
            return None
        return {
            'atr': atr, 'open': opening, 'close': close, 'ma5': ma5,
            'body': body, 'precursor_bars': [item['bar_id'] for item in precursors],
            'precursor_evidence': precursors,
        }
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def _short_reversal_body_evidence(frame, quote, atr):
    if frame is None or frame.empty or not math.isfinite(atr) or atr <= 0:
        return None
    try:
        live = frame.iloc[-1]
        opening = float(live['open'])
        effective = float(live['close'])
        high = float(live['high'])
        low = float(live['low'])
        body = effective - opening
        candle_range = high - low
        upper_wick = high - max(opening, effective)
        ma5 = float(live['ma5'])
        previous_mid = None
        if len(frame) > 1:
            previous = frame.iloc[-2]
            previous_mid = (
                float(previous['open']) + float(previous['close'])
            ) / 2.0
        middle = float(live.get('kc_middle', live.get('kc_basis', 0.0)))
        values = (opening, effective, high, low, body, candle_range,
                  upper_wick, ma5, middle)
        if (not all(math.isfinite(value) for value in values)
                or min(opening, effective, high, low, ma5) <= 0
                or candle_range <= 0 or high < max(opening, effective)
                or low > min(opening, effective)
                or not _current_bar_is_closed(frame)):
            return None
        body_ratio = body / candle_range
        if (body <= 0 or body < 0.35 * atr or upper_wick >= 0.4 * atr
                or effective <= ma5):
            return None
        return {
            'effective_price': effective, 'prev_mid': previous_mid,
            'ma5': ma5, 'kc_middle': middle, 'atr': atr,
            'body': body, 'body_ratio': body_ratio,
            'upper_wick': upper_wick,
        }
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def _long_reversal_body_evidence(frame, quote, atr):
    if frame is None or frame.empty or not math.isfinite(atr) or atr <= 0:
        return None
    try:
        live = frame.iloc[-1]
        opening = float(live['open'])
        effective = float(live['close'])
        high = float(live['high'])
        low = float(live['low'])
        body = opening - effective
        candle_range = high - low
        lower_wick = min(opening, effective) - low
        ma5 = float(live['ma5'])
        values = (opening, effective, high, low, body, candle_range, lower_wick, ma5)
        if (not all(math.isfinite(value) for value in values)
                or min(opening, effective, high, low, ma5) <= 0
                or candle_range <= 0 or high < max(opening, effective)
                or low > min(opening, effective)
                or not _current_bar_is_closed(frame)):
            return None
        if body < 0.35 * atr or lower_wick >= 0.4 * atr or effective >= ma5:
            return None
        return {
            'effective_price': effective, 'ma5': ma5, 'atr': atr,
            'body': body, 'body_ratio': body / candle_range,
            'lower_wick': lower_wick,
        }
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def closed_peak_valley_reversal_evidence(frame, side, atr):
    """Require a meaningful, structurally confirmed closed-bar reversal."""
    if (frame is None or len(frame) < 2 or side not in ('LONG', 'SHORT')
            or not math.isfinite(atr) or atr <= 0 or not _current_bar_is_closed(frame)):
        return None
    try:
        current, previous = frame.iloc[-1], frame.iloc[-2]
        opening, close, ma5 = (float(current[key]) for key in ('open', 'close', 'ma5'))
        previous_mid = (float(previous['open']) + float(previous['close'])) / 2.0
        high, low = float(current['high']), float(current['low'])
        values = (opening, close, ma5, previous_mid, high, low)
        if (not all(math.isfinite(value) and value > 0 for value in values)
                or high < max(opening, close) or low > min(opening, close)):
            return None

        body = abs(close - opening)
        if side == 'LONG':
            reversed_body = close < opening
            structural_break = close < ma5 or close < previous_mid
        else:
            reversed_body = close > opening
            structural_break = close > ma5 or close > previous_mid
        # Dojis and weak opposite bodies remain observation signals.  Exit on
        # this bar only when the reversal body itself is decisive.
        if not (reversed_body and body >= 0.35 * atr and structural_break):
            return None
        return {
            'open': opening, 'close': close, 'ma5': ma5,
            'previous_midpoint': previous_mid, 'atr': atr,
            'body': body, 'is_doji': body <= 0.1 * atr,
        }
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def intrabar_adverse_pressure_evidence(frame, side, quote, atr):
    """Separate a forceful, structure-breaking reversal from an ordinary pullback."""
    if (frame is None or len(frame) < 2 or side not in ('LONG', 'SHORT')
            or not all(math.isfinite(float(value)) and float(value) > 0
                       for value in (quote, atr))):
        return None
    try:
        current, previous = frame.iloc[-1], frame.iloc[-2]
        opening = float(current['open'])
        ma5 = float(current['ma5'])
        previous_mid = (float(previous['open']) + float(previous['close'])) / 2.0
        price = float(quote)
        body = opening - price if side == 'LONG' else price - opening
        if side == 'LONG':
            structure_broken = price < ma5 and price < previous_mid
        else:
            structure_broken = price > ma5 and price > previous_mid
        if not math.isfinite(body) or body < 0.35 * atr or not structure_broken:
            return None

        volume_ratio = None
        if 'volume' in frame.columns and len(frame) >= 7:
            prior_volumes = [float(value) for value in frame['volume'].iloc[-6:-1]]
            current_volume = float(current['volume'])
            baseline = sum(prior_volumes) / len(prior_volumes)
            if (all(math.isfinite(value) and value >= 0 for value in prior_volumes)
                    and math.isfinite(current_volume) and baseline > 0):
                volume_ratio = current_volume / baseline
        # A forming candle must show a very large body before it can close
        # immediately. Smaller pressure needs a completed bar plus expansion.
        candle_closed = _current_bar_is_closed(frame)
        if body < 0.75 * atr and not (
                candle_closed and volume_ratio is not None and volume_ratio >= 1.2):
            return None
        return {
            'side': side, 'open': opening, 'quote': price, 'atr': float(atr),
            'adverse_body': body, 'adverse_body_atr': body / float(atr),
            'ma5': ma5, 'previous_midpoint': previous_mid,
            'volume_ratio': volume_ratio,
            'pressure_confirmation': 'VOLUME_EXPANSION' if volume_ratio is not None and volume_ratio >= 1.2 else 'LARGE_BODY',
        }
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def intrabar_peak_valley_reversal_evidence(position, frame, side, quote, atr,
                                           pivot_price, favorable_move):
    """Authorize prompt exits only after a sizeable move rejects a real extreme."""
    if (side not in ('LONG', 'SHORT') or frame is None or len(frame) < 2
            or not all(math.isfinite(value) and value > 0
                       for value in (float(quote), float(atr), float(pivot_price), float(favorable_move)))):
        return None
    entry = float((position or {}).get('entry_price') or 0.0)
    if entry <= 0:
        return None
    excursion = (pivot_price - entry) if side == 'LONG' else (entry - pivot_price)
    retracement = (pivot_price - quote) if side == 'LONG' else (quote - pivot_price)
    if (excursion < 0.5 * atr or favorable_move < 0.5 * atr
            or retracement < max(0.35 * atr, 0.2 * excursion)):
        return None
    current, previous = frame.iloc[-1], frame.iloc[-2]
    opening = float(current.get('open') or 0.0)
    live_body = opening - float(quote) if side == 'LONG' else float(quote) - opening
    if not math.isfinite(live_body) or live_body < 0.35 * atr:
        return None
    ma5 = float(current.get('ma5') or 0.0)
    previous_mid = (
        float(previous.get('open') or 0.0) + float(previous.get('close') or 0.0)
    ) / 2.0
    if (not math.isfinite(ma5) or not math.isfinite(previous_mid)
            or ma5 <= 0 or previous_mid <= 0):
        return None
    structure_broken = (
        quote < ma5 and quote < previous_mid
        if side == 'LONG'
        else quote > ma5 and quote > previous_mid
    )
    if not structure_broken:
        return None
    current_closed = _current_bar_is_closed(frame)
    volume_ratio = None
    if 'volume' in frame.columns and len(frame) >= 7:
        try:
            prior_volumes = [float(value) for value in frame['volume'].iloc[-6:-1]]
            current_volume = float(current['volume'])
            baseline = sum(prior_volumes) / len(prior_volumes)
            if (all(math.isfinite(value) and value >= 0 for value in prior_volumes)
                    and math.isfinite(current_volume) and baseline > 0):
                volume_ratio = current_volume / baseline
        except (TypeError, ValueError, OverflowError):
            volume_ratio = None
    if live_body < 0.75 * atr and not (
            current_closed and volume_ratio is not None and volume_ratio >= 1.2):
        return None
    return {
        'side': side, 'quote': quote, 'atr': atr,
        'pivot_price': pivot_price, 'favorable_move': favorable_move,
        'reversal_body': live_body,
        'ma5': ma5, 'previous_midpoint': previous_mid,
        'volume_ratio': volume_ratio,
        'excursion_atr': excursion / atr, 'retrace_atr': retracement / atr,
    }


def _net_roe_pct(position, price, side):
    try:
        entry = float(position.get('entry_price') or 0.)
        qty = abs(float(position.get('qty', position.get('quantity', 0.)) or 0.))
        margin = float(position.get('margin') or 0.)
        if margin <= 0:
            leverage = float(position.get('leverage') or 0.)
            if leverage > 0:
                margin = entry * qty / leverage
        if not all(math.isfinite(value) and value > 0
                   for value in (entry, qty, margin, float(price))):
            return None
        from core.config import SLIPPAGE_PCT, TAKER_FEE_RATE
        from core.services.exits.peak_trailing_exit import estimated_display_net_pnl

        sign = 1 if side == 'LONG' else -1
        net_pnl = estimated_display_net_pnl(
            entry, float(price), qty, sign, TAKER_FEE_RATE, SLIPPAGE_PCT,
        )
        return net_pnl / margin * 100.0
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def _ratchet_lock_details(position, quote):
    side = str((position or {}).get('side', '')).upper()
    if side not in ('LONG', 'SHORT'):
        return None
    try:
        entry = float(position.get('entry_price') or 0.)
        favorable = (
            max(float(position.get('highest_price') or entry), float(quote))
            if side == 'LONG'
            else min(float(position.get('lowest_price') or entry), float(quote))
        )
        peak_roe = _net_roe_pct(position, favorable, side)
        current_roe = _net_roe_pct(position, quote, side)
        if peak_roe is None or current_roe is None:
            return None
        saved_state = position.get('three_tier_net_roe_lock_state')
        if isinstance(saved_state, dict):
            saved_peak = float(saved_state.get('peak_net_roe_pct') or peak_roe)
            if math.isfinite(saved_peak):
                peak_roe = max(peak_roe, saved_peak)
        reached = lambda threshold: (
            peak_roe >= threshold
            or math.isclose(peak_roe, threshold, rel_tol=1e-12)
        )
        if reached(7.0):
            floor, tier = peak_roe * 0.75, 3
        elif reached(4.0):
            floor, tier = 2.5, 2
        elif reached(2.0):
            floor, tier = 0.0, 1
        else:
            return None
        if current_roe >= floor:
            return None
        return {
            'peak_net_roe_pct': peak_roe,
            'current_net_roe_pct': current_roe,
            'floor_net_roe_pct': floor,
            'tier': tier,
            'peak_roe': peak_roe / 100.0,
            'giveback_ratio': max(0.0, (peak_roe - current_roe) / peak_roe)
            if peak_roe > 0 else 0.0,
        }
    except (TypeError, ValueError, OverflowError):
        return None


def _calculate_bars_held(position: Dict[str, Any], frame: Optional[pd.DataFrame]) -> int:
    """Calculate the number of bars a position has been held."""
    if not position:
        return 999
    if 'bars_held' in position and position['bars_held'] is not None:
        try:
            return int(position['bars_held'])
        except (ValueError, TypeError):
            pass
    open_ts = position.get('open_timestamp')
    if open_ts is not None and frame is not None and not frame.empty:
        try:
            curr_bar = frame.iloc[-1]
            curr_ts = float(curr_bar.get('timestamp') or 0.0)
            open_sec = float(open_ts)
            open_ms = open_sec * 1000.0 if open_sec < 1e11 else open_sec
            tf_ms = float(getattr(frame, 'attrs', {}).get('timeframe_ms', 60000) or 60000)
            if curr_ts > 0 and open_ms > 0 and tf_ms > 0:
                diff_ms = max(0.0, curr_ts - open_ms)
                return max(1, int(diff_ms // tf_ms) + 1)
        except (ValueError, TypeError, OverflowError):
            pass
    open_bar_id = position.get('open_bar_id') or position.get('entry_bar_id') or position.get('candidate_bar_id')
    if open_bar_id is not None and frame is not None and not frame.empty:
        try:
            curr_bar = frame.iloc[-1]
            curr_ts = float(curr_bar.get('timestamp') or 0.0)
            tf_ms = float(getattr(frame, 'attrs', {}).get('timeframe_ms', 60000) or 60000)
            if curr_ts > 0 and float(open_bar_id) > 0 and tf_ms > 0:
                diff_ms = max(0.0, curr_ts - float(open_bar_id))
                return max(1, int(diff_ms // tf_ms) + 1)
        except (ValueError, TypeError, OverflowError):
            pass
    return 999


class HoldingProtectionExitGate:
    """Gatekeeper ensuring position is never closed on normal pullbacks."""

    REJECT_REASON = "REJECT_EXIT: BLOCKED_BY_HOLDING_PROTECTION_GATE (Normal Pullback Breathing)"
    REJECT_HEALTHY_PULLBACK = "REJECT_EXIT: BLOCKED_BY_HEALTHY_PULLBACK"
    WAIT_CLOSE_REJECT_REASON = (
        "REJECT_EXIT: BLOCKED_BY_HOLDING_PROTECTION_GATE "
        "(Wait Bar Close Confirmation)"
    )

    WHITELIST_CODES = {
        'EXIT_BY_EXTREME_WATERFALL',
        'EXIT_BY_CIRCUIT_BREAKER_HARD_SL',
        'EXIT_BY_RATCHET_PROFIT_LOCK',
        'EXIT_LONG_ON_TOP_WATERFALL_DUMP',
        'EXIT_BY_VERIFIED_FRACTAL_PEAK',
        'EXIT_BY_VERIFIED_FRACTAL_VALLEY',
        'EXIT_SHORT_ON_EXHAUSTION_REVERSAL',
        'EXIT_LONG_ON_EXHAUSTION_REVERSAL',
        # Aliases
        'EXIT_LONG_ON_FRACTAL_PEAK',
        'EXIT_SHORT_ON_FRACTAL_VALLEY',
        'EXIT_LONG_ON_UPPER_WICK_REJECTION',
        'EXIT_SHORT_ON_LOWER_WICK_REJECTION',
        'EXIT_LONG_ON_SELLING_PRESSURE',
        'EXIT_SHORT_ON_BUYING_PRESSURE',
    }

    @classmethod
    def evaluate(cls, position: Dict[str, Any], frame: Optional[pd.DataFrame],
                 quote: float) -> Tuple[Optional[str], Dict[str, Any]]:
        """Directly evaluates active market data for the 4 whitelisted exit scenarios."""
        if not position or quote <= 0:
            return None, {}

        side = str(position.get('side', '')).upper()
        entry_price = float(position.get('entry_price', 0.0))
        if entry_price <= 0:
            return None, {}

        highest_price = float(position.get('highest_price') or position.get('peak_price') or entry_price)
        lowest_price = float(position.get('lowest_price') or position.get('trough_price') or entry_price)
        if quote > highest_price:
            highest_price = quote
        if quote < lowest_price:
            lowest_price = quote

        has_frame = frame is not None and len(frame) > 0
        curr = frame.iloc[-1] if has_frame else {}
        prev = frame.iloc[-2] if (frame is not None and len(frame) > 1) else {}
        atr = float(prev.get('atr', curr.get('atr', 0.0))) if has_frame else 0.0
        candle_closed = _current_bar_is_closed(frame)

        # ── 1. 破軌多單進場保護期 (Grace Period: bars_held <= 2) ──
        # 開倉後前 2 根 K 棒僅在跌破開倉停損底線（Stop Loss）時出場，避免被微幅盤中影線早洗。
        bars_held = _calculate_bars_held(position, frame)
        if side == 'LONG' and bars_held <= 2:
            sl = float(position.get('sl') or position.get('initial_sl') or 0.0)
            if sl <= 0 and atr > 0:
                sl = entry_price - 1.5 * atr
            if sl > 0 and quote <= sl:
                return 'EXIT_BY_CIRCUIT_BREAKER_HARD_SL', {
                    'stop_loss': sl, 'quote': quote, 'bars_held': bars_held,
                    'side': side, 'reason': 'STOP_LOSS_HIT_IN_GRACE_PERIOD',
                }

        # ── 1.1 極端異常：大瀑布反轉 / 極速反撲 (EXIT_BY_EXTREME_WATERFALL) ──
        if frame is not None and len(frame) >= 1 and atr > 0:
            curr_open = float(curr.get('open', quote))
            kc_upper = float(curr.get('kc_upper', 0.0))
            kc_lower = float(curr.get('kc_lower', 0.0))
            kc_middle = float(curr.get('kc_middle', curr.get('kc_basis', 0.0)))

            if side == 'LONG':
                adverse_body = curr_open - quote
                if (not candle_closed and adverse_body >= 1.5 * atr
                        and kc_middle > 0 and quote < kc_middle):
                    return 'EXIT_BY_EXTREME_WATERFALL', {
                        'adverse_body': adverse_body, 'atr': atr,
                        'kc_middle': kc_middle,
                        'quote': quote, 'side': side,
                    }
            elif side == 'SHORT':
                adverse_body = quote - curr_open
                if (not candle_closed and adverse_body >= 1.5 * atr
                        and kc_middle > 0 and quote > kc_middle):
                    return 'EXIT_BY_EXTREME_WATERFALL', {
                        'adverse_body': adverse_body, 'atr': atr,
                        'kc_middle': kc_middle,
                        'quote': quote, 'side': side,
                    }

        # ── 1.2 頂部斷頭鍘特例 (Top Waterfall Flip) 授權「平多 + 秒反手開空」──
        if side == 'LONG' and candle_closed:
            evidence = top_engulfing_flip_evidence(frame)
            if evidence is not None:
                position['flip_enter_short'] = True
                return 'EXIT_LONG_ON_TOP_WATERFALL_DUMP', {
                    **evidence, 'quote': quote, 'side': side,
                    'flip_enter_short': True,
                    'authorized_action': 'ACTION_FLIP_LONG_TO_SHORT',
                }

        exhaustion = exhaustion_reversal_evidence(frame, side)
        if exhaustion is not None:
            favorable_price = highest_price if side == 'LONG' else lowest_price
            favorable_move = (
                favorable_price - entry_price if side == 'LONG'
                else entry_price - favorable_price
            )
            favorable_roe = favorable_move / entry_price if entry_price > 0 else 0.0
            if (atr > 0 and (favorable_roe >= 0.03 or favorable_move >= 0.5 * atr)):
                closed_reversal = closed_peak_valley_reversal_evidence(frame, side, atr)
                if closed_reversal is not None:
                    exhaustion.update(closed_reversal)
                    exhaustion.update(
                        quote=quote, closed_reversal_confirmation=True,
                        peak_roe=favorable_roe if side == 'LONG' else 0.0,
                        valley_roe=favorable_roe if side == 'SHORT' else 0.0,
                        peak_gain_atr=favorable_move / atr,
                    )
                    return f'EXIT_{side}_ON_EXHAUSTION_REVERSAL', exhaustion

        # ── 3. 階梯鎖利：以淨 ROE 峰值計算保護底線 ──
        ratchet = _ratchet_lock_details(position, quote)
        if ratchet is not None:
            pressure = intrabar_adverse_pressure_evidence(frame, side, quote, atr)
            if pressure is not None:
                ratchet.update(
                    highest_price=highest_price, lowest_price=lowest_price,
                    entry_price=entry_price, quote=quote, side=side,
                    **pressure,
                )
                return 'EXIT_BY_RATCHET_PROFIT_LOCK', ratchet

        # ── 4. 結構確認：真實波段峰頂 / 谷底生成 (EXIT_BY_VERIFIED_FRACTAL_PEAK / VALLEY) ──
        if (not candle_closed and side == 'SHORT' and atr > 0
                and frame is not None and len(frame) > 0):
            live_open = float(curr.get('open', quote))
            live_high = max(float(curr.get('high', quote)), quote)
            upper_wick = live_high - max(live_open, quote)
            if upper_wick >= 0.4 * atr:
                return None, {
                    'upper_wick_hold': True, 'upper_wick': upper_wick,
                    'atr': atr, 'quote': quote,
                }

        if candle_closed and frame is not None and len(frame) >= 3:
            prev_open = float(prev.get('open', 0.0))
            prev_close = float(prev.get('close', 0.0))
            prev_mid = (prev_open + prev_close) / 2.0

            if side == 'LONG':
                peak_roe = (highest_price - entry_price) / entry_price
                peak_gain_atr = (highest_price - entry_price) / atr if atr > 0 else 0.0
                has_meaningful_profit = (peak_roe >= 0.03) or (peak_gain_atr >= 0.5)

                if has_meaningful_profit and is_fractal_peak(frame):
                    # 必須為收線實體跌破前棒實體中點（非影線虛破）
                    curr_open = float(curr.get('open', quote))
                    curr_close = float(curr.get('close', quote))
                    effective_price = curr_close if candle_closed else quote

                    # 多單遭遇長下影線下探回升，嚴禁平倉
                    curr_low = float(curr.get('low', quote))
                    lower_wick = min(curr_open, effective_price) - curr_low
                    if atr > 0 and lower_wick >= 0.4 * atr:
                        return None, {'lower_wick_hold': True}

                    if (effective_price < prev_mid
                            and _long_reversal_body_evidence(frame, quote, atr) is not None):
                        return 'EXIT_BY_VERIFIED_FRACTAL_PEAK', {
                            'peak_roe': peak_roe, 'prev_mid': prev_mid,
                            'effective_price': effective_price, 'quote': quote,
                            'atr': atr, 'closed_reversal_confirmation': True,
                            'peak_gain_atr': peak_gain_atr,
                        }

            elif side == 'SHORT':
                peak_roe = (entry_price - lowest_price) / entry_price
                peak_gain_atr = (entry_price - lowest_price) / atr if atr > 0 else 0.0
                has_meaningful_profit = (peak_roe >= 0.03) or (peak_gain_atr >= 0.5)
                if candle_closed and has_meaningful_profit:
                    live = frame.iloc[-1]
                    curr_open = float(live.get('open', quote))
                    curr_close = float(live.get('close', quote))
                    curr_low = float(live.get('low', quote))
                    body = abs(curr_close - curr_open)
                    lower_wick = min(curr_open, curr_close) - curr_low
                if (has_meaningful_profit and _current_bar_is_closed(frame)
                        and is_fractal_valley(frame)):
                    body_evidence = _short_reversal_body_evidence(frame, quote, atr)
                    if body_evidence is not None:
                        body_evidence.update(
                            valley_roe=peak_roe, peak_gain_atr=peak_gain_atr,
                            quote=quote,
                        )
                        return 'EXIT_BY_VERIFIED_FRACTAL_VALLEY', body_evidence
                    live = frame.iloc[-1]
                    effective = float(live.get('close', quote))
                    upper_wick = float(live.get('high', effective)) - max(
                        float(live.get('open', effective)), effective,
                    )
                    if atr > 0 and upper_wick >= 0.4 * atr:
                        return None, {'upper_wick_hold': True, 'upper_wick': upper_wick}

        return None, {}

    @classmethod
    def validate_exit(cls, position: Dict[str, Any], frame: Optional[pd.DataFrame],
                      quote: float, candidate_reason: str,
                      details: Optional[Dict[str, Any]] = None) -> Tuple[bool, str, Dict[str, Any]]:
        """Strict whitelist verification for any proposed exit reason.
        
        Returns:
            (is_allowed: bool, authorized_reason_or_reject_code: str, details: dict)
        """
        details = details or {}
        side = str((position or {}).get('side', '')).upper()
        clean_reason = str(candidate_reason or '').replace('Channel Swing ', '').strip()

        import logging
        logger = logging.getLogger("uvicorn.error")
        symbol = (position or {}).get('symbol', 'UNKNOWN')

        def _auth(code):
            auth_msg = f"[HOLDING_PROTECTION] Authorized exit for {symbol}: {code}"
            logger.info(auth_msg)
            print(auth_msg, flush=True)
            return True, code, details

        def _reject(wait_for_close=False):
            reason = (
                cls.WAIT_CLOSE_REJECT_REASON if wait_for_close
                else cls.REJECT_REASON
            )
            reject_msg = f"[HOLDING_PROTECTION] Rejected exit for {symbol}: {reason}"
            logger.info(reject_msg)
            print(reject_msg, flush=True)
            return False, reason, details

        # ── 1. 硬止損 / 市場熔斷 (WHITELIST SCENARIO 2) ──
        if any(marker in clean_reason.upper() for marker in (
            'HARD_STOP', 'CIRCUIT_BREAKER', 'HARD_SL', 'ACCOUNT_HARD_STOP',
            'EMERGENCY_STOP', 'EXIT_BY_CIRCUIT_BREAKER_HARD_SL'
        )):
            return _auth('EXIT_BY_CIRCUIT_BREAKER_HARD_SL')

        # ── 1.5 進場保護期 (Grace Period: bars_held <= 2) ──
        bars_held = _calculate_bars_held(position, frame)
        grace_allowed_exits = {
            'EXIT_BY_EXTREME_WATERFALL', 'CATASTROPHIC_DUMP_EXIT',
            'CATASTROPHIC_PUMP_EXIT', 'EXIT_BY_RATCHET_PROFIT_LOCK',
            'EXIT_BY_VERIFIED_FRACTAL_PEAK', 'EXIT_LONG_ON_FRACTAL_PEAK',
            'EXIT_BY_VERIFIED_FRACTAL_VALLEY', 'EXIT_SHORT_ON_FRACTAL_VALLEY',
            'EXIT_LONG_ON_UPPER_WICK_REJECTION',
            'EXIT_SHORT_ON_LOWER_WICK_REJECTION',
            'EXIT_LONG_ON_SELLING_PRESSURE', 'EXIT_SHORT_ON_BUYING_PRESSURE',
        }
        if (side == 'LONG' and bars_held <= 2
                and clean_reason not in grace_allowed_exits
                and not clean_reason.startswith('PROFIT_LOCK')):
            return _reject()

        # Every intrabar strategy exit waits for candle finality except the
        # verified extreme V-reversal and the ROE ratchet lock.
        if frame is not None and not frame.empty and not _current_bar_is_closed(frame):
            if clean_reason == 'EXIT_BY_RATCHET_PROFIT_LOCK' or clean_reason.startswith('PROFIT_LOCK'):
                lock = _ratchet_lock_details(position, quote)
                try:
                    live, previous = frame.iloc[-1], frame.iloc[-2]
                    atr = float(previous.get('atr', live.get('atr', 0.0)))
                except (TypeError, ValueError, IndexError, OverflowError):
                    atr = 0.0
                pressure = intrabar_adverse_pressure_evidence(frame, side, quote, atr)
                if lock is not None and pressure is not None:
                    details.update(lock)
                    details.update(pressure)
                    return _auth('EXIT_BY_RATCHET_PROFIT_LOCK')
                return _reject(wait_for_close=True)

            intrabar_peak_valley = (
                clean_reason in (
                    'EXIT_BY_VERIFIED_FRACTAL_PEAK', 'EXIT_LONG_ON_FRACTAL_PEAK',
                    'EXIT_BY_VERIFIED_FRACTAL_VALLEY', 'EXIT_SHORT_ON_FRACTAL_VALLEY',
                ) and details.get('intrabar_pivot_confirmation') is True
            )
            intrabar_catastrophic = clean_reason in (
                'EXIT_BY_EXTREME_WATERFALL', 'CATASTROPHIC_DUMP_EXIT',
                'CATASTROPHIC_PUMP_EXIT',
            )
            intrabar_pressure = clean_reason in (
                'EXIT_LONG_ON_SELLING_PRESSURE', 'EXIT_SHORT_ON_BUYING_PRESSURE',
            )
            if not intrabar_peak_valley and not intrabar_catastrophic and not intrabar_pressure:
                return _reject(wait_for_close=True)

        if clean_reason in ('EXIT_LONG_ON_SELLING_PRESSURE', 'EXIT_SHORT_ON_BUYING_PRESSURE'):
            expected_side = 'LONG' if clean_reason == 'EXIT_LONG_ON_SELLING_PRESSURE' else 'SHORT'
            evidence = intrabar_adverse_pressure_evidence(frame, side, quote, atr)
            if side != expected_side or evidence is None:
                return _reject(wait_for_close=not candle_closed)
            details.update(evidence)
            return _auth(clean_reason)

        # ── 2. 極端異常大瀑布：原因標籤不構成授權，重新驗證實體與中軌 ──
        if clean_reason in ('EXIT_BY_EXTREME_WATERFALL', 'CATASTROPHIC_DUMP_EXIT', 'CATASTROPHIC_PUMP_EXIT'):
            try:
                if frame is None or frame.empty or side not in ('LONG', 'SHORT'):
                    return _reject()
                live = frame.iloc[-1]
                previous = frame.iloc[-2] if len(frame) > 1 else live
                atr = float(previous.get('atr', live.get('atr', 0.0)))
                opening = float(live.get('open', quote))
                middle = float(live.get('kc_middle', live.get('kc_basis', 0.0)))
                current_quote = float(quote)
                adverse_body = opening - current_quote if side == 'LONG' else current_quote - opening
                crossed_middle = (
                    current_quote < middle if side == 'LONG' else current_quote > middle
                )
                if (not all(math.isfinite(value) for value in
                            (atr, opening, middle, current_quote, adverse_body))
                        or atr <= 0 or opening <= 0 or middle <= 0
                        or adverse_body < 1.5 * atr or not crossed_middle):
                    return _reject(wait_for_close=not candle_closed)
                details.update(
                    adverse_body=adverse_body, atr=atr, kc_middle=middle,
                    quote=current_quote, side=side,
                )
                return _auth('EXIT_BY_EXTREME_WATERFALL')
            except (KeyError, TypeError, ValueError, OverflowError, IndexError):
                return _reject(wait_for_close=not candle_closed)

        # ── 3. 階梯鎖利回吐 (WHITELIST SCENARIO 3) ──
        if clean_reason == 'EXIT_BY_RATCHET_PROFIT_LOCK' or clean_reason.startswith('PROFIT_LOCK'):
            lock = _ratchet_lock_details(position, quote)
            try:
                live, previous = frame.iloc[-1], frame.iloc[-2]
                atr = float(previous.get('atr', live.get('atr', 0.0)))
            except (AttributeError, TypeError, ValueError, IndexError, OverflowError):
                atr = 0.0
            pressure = intrabar_adverse_pressure_evidence(frame, side, quote, atr)
            if lock is not None and pressure is not None:
                details.update(lock)
                details.update(pressure)
                return _auth('EXIT_BY_RATCHET_PROFIT_LOCK')
            return _reject(wait_for_close=not candle_closed)

        if clean_reason in (
            'EXIT_SHORT_ON_EXHAUSTION_REVERSAL',
            'EXIT_LONG_ON_EXHAUSTION_REVERSAL',
        ):
            expected_side = (
                'SHORT' if clean_reason == 'EXIT_SHORT_ON_EXHAUSTION_REVERSAL'
                else 'LONG'
            )
            evidence = exhaustion_reversal_evidence(frame, side)
            if side != expected_side or evidence is None:
                return _reject()
            details.update(evidence)
            return _auth(clean_reason)

        if clean_reason in (
            'EXIT_LONG_ON_UPPER_WICK_REJECTION',
            'EXIT_SHORT_ON_LOWER_WICK_REJECTION',
        ):
            atr = float(details.get('atr') or 0.0)
            body = float(details.get('body') or 0.0)
            wick = float(
                details.get('upper_wick' if side == 'LONG' else 'lower_wick') or 0.0
            )
            peak_roe = float(details.get('peak_roe' if side == 'LONG' else 'valley_roe') or 0.0)
            peak_gain_atr = float(details.get('peak_gain_atr') or 0.0)
            if (side not in ('LONG', 'SHORT')
                    or (side == 'LONG' and clean_reason != 'EXIT_LONG_ON_UPPER_WICK_REJECTION')
                    or (side == 'SHORT' and clean_reason != 'EXIT_SHORT_ON_LOWER_WICK_REJECTION')
                    or not all(math.isfinite(value) for value in
                               (atr, body, wick, peak_roe, peak_gain_atr))
                    or atr <= 0
                    or wick < 0.5 * atr
                    or wick <= 2.0 * body
                    or (side == 'SHORT'
                        and float(details.get('upper_wick') or 0.0) >= 0.4 * atr)
                    or (peak_roe < 0.03 and peak_gain_atr < 0.5)):
                return _reject()
            if (side == 'SHORT'
                    and _short_reversal_body_evidence(frame, quote, atr) is None):
                return _reject()
            if (side == 'LONG'
                    and _long_reversal_body_evidence(frame, quote, atr) is None):
                return _reject()
            return _auth(clean_reason)

        # ── 4. 真實分形頂底 (WHITELIST SCENARIO 4) ──
        if clean_reason in (
            'EXIT_BY_VERIFIED_FRACTAL_PEAK', 'EXIT_LONG_ON_FRACTAL_PEAK',
            'EXIT_BY_VERIFIED_FRACTAL_VALLEY', 'EXIT_SHORT_ON_FRACTAL_VALLEY'
        ):
            # 檢查前置利潤條件與防護
            entry_price = float((position or {}).get('entry_price', 0.0))
            highest_price = float((position or {}).get('highest_price') or entry_price)
            lowest_price = float((position or {}).get('lowest_price') or entry_price)

            if side == 'LONG':
                peak_roe = (highest_price - entry_price) / entry_price if entry_price > 0 else 0.0
                peak_gain_atr = float(details.get('peak_gain_atr') or 0.0)
                if (peak_roe < 0.03 and details.get('peak_roe', 0.0) < 0.03
                        and peak_gain_atr < 0.5):
                    return _reject()
                # 多單下影線防護
                if details.get('lower_wick_hold'):
                    return _reject()
                atr = float(details.get('atr') or 0.0)
                if details.get('intrabar_pivot_confirmation'):
                    evidence = intrabar_peak_valley_reversal_evidence(
                        position, frame, side, float(quote), atr,
                        float(details.get('pivot_price') or 0.0),
                        float(details.get('favorable_move') or 0.0),
                    )
                    if evidence is None:
                        return _reject()
                    details.update(evidence)
                elif details.get('closed_reversal_confirmation'):
                    evidence = closed_peak_valley_reversal_evidence(frame, side, atr)
                    if evidence is None:
                        return _reject()
                    details.update(evidence)
                elif _long_reversal_body_evidence(frame, quote, atr) is None:
                    return _reject()
                authorized_code = 'EXIT_BY_VERIFIED_FRACTAL_PEAK'
            else:
                peak_roe = (entry_price - lowest_price) / entry_price if entry_price > 0 else 0.0
                peak_gain_atr = float(details.get('peak_gain_atr') or 0.0)
                if (peak_roe < 0.03 and details.get('valley_roe', 0.0) < 0.03
                        and peak_gain_atr < 0.5):
                    return _reject()
                atr = float(details.get('atr') or 0.0)
                if details.get('intrabar_pivot_confirmation'):
                    evidence = intrabar_peak_valley_reversal_evidence(
                        position, frame, side, float(quote), atr,
                        float(details.get('pivot_price') or 0.0),
                        float(details.get('favorable_move') or 0.0),
                    )
                    if evidence is None:
                        return _reject()
                    details.update(evidence)
                elif details.get('closed_reversal_confirmation'):
                    evidence = closed_peak_valley_reversal_evidence(frame, side, atr)
                    if evidence is None:
                        return _reject()
                    details.update(evidence)
                else:
                    body_evidence = _short_reversal_body_evidence(frame, quote, atr)
                    if body_evidence is None:
                        return _reject()
                authorized_code = 'EXIT_BY_VERIFIED_FRACTAL_VALLEY'

            return _auth(authorized_code)

        # ── 5. 頂部吞噬斷頭鍘的峰頂確認與原子反手 ──
        if clean_reason == 'EXIT_LONG_ON_TOP_WATERFALL_DUMP':
            if side != 'LONG':
                return _reject()
            if frame is not None and len(frame) >= 2:
                try:
                    evidence = top_engulfing_flip_evidence(frame)
                    if evidence is not None:
                        details.update(evidence)
                        details.update(
                            flip_enter_short=True,
                            authorized_action='ACTION_FLIP_LONG_TO_SHORT',
                        )
                        return _auth('EXIT_LONG_ON_TOP_WATERFALL_DUMP')
                except Exception:
                    pass
            return _reject()

        # ── 6. 健康回踩強制續抱檢驗 (Pullback Immunity) ──
        if frame is not None and len(frame) >= 2:
            try:
                curr_bar = frame.iloc[-1]
                prev_bar = frame.iloc[-2]
                curr_kc_mid = float(curr_bar.get('kc_middle', curr_bar.get('kc_basis', 0.0)))
                prev_kc_mid = float(prev_bar.get('kc_middle', prev_bar.get('kc_basis', 0.0)))
                curr_ma15 = float(curr_bar.get('ma15', 0.0))
                prev_ma15 = float(prev_bar.get('ma15', 0.0))
                ma15_slope = curr_ma15 - prev_ma15
                kc_up = curr_kc_mid > prev_kc_mid
                kc_down = curr_kc_mid < prev_kc_mid

                if side == 'LONG':
                    trend_up = kc_up or ma15_slope > 0
                    above_support = quote >= curr_kc_mid or quote >= curr_ma15
                    if trend_up and above_support:
                        reason = cls.REJECT_HEALTHY_PULLBACK
                        reject_msg = f"[HOLDING_PROTECTION] Rejected exit for {symbol}: {reason}"
                        logger.info(reject_msg)
                        print(reject_msg, flush=True)
                        return False, reason, details
                elif side == 'SHORT':
                    trend_down = kc_down or ma15_slope < 0
                    below_resistance = quote <= curr_kc_mid or quote <= curr_ma15
                    if trend_down and below_resistance:
                        reason = cls.REJECT_HEALTHY_PULLBACK
                        reject_msg = f"[HOLDING_PROTECTION] Rejected exit for {symbol}: {reason}"
                        logger.info(reject_msg)
                        print(reject_msg, flush=True)
                        return False, reason, details
            except Exception:
                pass

        # ── 7. 其餘常規回踩（MA 回碰、影線抖動、未達標轉向等）一律一票否決 ──
        return _reject()
