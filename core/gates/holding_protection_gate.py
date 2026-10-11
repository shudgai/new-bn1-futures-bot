"""Holding Protection Exit Gate: Protects active positions from premature pullback exits.

Enforces an explicit exit whitelist:
1. EXIT_BY_EXTREME_WATERFALL: Extreme adverse move >= 1.2*ATR through the relevant KC boundary.
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
        'EXIT_LONG_ON_REAL_TOP_DUMP',
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

        # ── 1. 極端異常：大瀑布反轉 / 極速反撲 (EXIT_BY_EXTREME_WATERFALL) ──
        if frame is not None and len(frame) >= 1 and atr > 0:
            curr_open = float(curr.get('open', quote))
            kc_upper = float(curr.get('kc_upper', 0.0))
            kc_lower = float(curr.get('kc_lower', 0.0))
            kc_middle = float(curr.get('kc_middle', curr.get('kc_basis', 0.0)))

            if side == 'LONG':
                live_high = max(float(curr.get('high', quote)), quote)
                live_low = min(float(curr.get('low', quote)), quote)
                candle_range = live_high - live_low
                if (not candle_closed and candle_range >= 1.2 * atr
                        and kc_middle > 0 and quote < kc_middle <= live_high):
                    return 'EXIT_BY_EXTREME_WATERFALL', {
                        'candle_range': candle_range, 'atr': atr,
                        'kc_middle': kc_middle,
                        'quote': quote, 'side': side,
                    }
            elif side == 'SHORT':
                live_high = max(float(curr.get('high', quote)), quote)
                live_low = min(float(curr.get('low', quote)), quote)
                candle_range = live_high - live_low
                if (not candle_closed and candle_range >= 1.2 * atr
                        and kc_middle > 0 and live_low <= kc_middle < quote):
                    return 'EXIT_BY_EXTREME_WATERFALL', {
                        'candle_range': candle_range, 'atr': atr,
                        'kc_middle': kc_middle, 'live_low': live_low,
                        'quote': quote, 'side': side,
                    }

        # ── 1.1 頂部斷頭鍘特例 (Top Waterfall Flip) 授權「平多 + 秒反手開空」──
        if side == 'LONG' and candle_closed and frame is not None and len(frame) >= 2 and atr > 0:
            try:
                c_open = float(curr.get('open', quote))
                c_close = float(curr.get('close', quote))
                c_ma5 = float(curr.get('ma5', quote))
                p_low = float(prev.get('low', c_close))
                # 1. 當根為強勢實體陰線（Open - Close >= 0.45 * ATR）
                # 2. 收盤價強勢貫穿跌破 MA5，並吞噬前棒低點 (Close < MA5 且 Close < prev_low)
                if (c_open - c_close) >= 0.45 * atr and c_close < c_ma5 and c_close < p_low:
                    position['flip_enter_short'] = True
                    return 'EXIT_LONG_ON_TOP_WATERFALL_DUMP', {
                        'open': c_open, 'close': c_close, 'ma5': c_ma5,
                        'prev_low': p_low, 'atr': atr, 'quote': quote, 'side': side,
                        'flip_enter_short': True, 'authorized_action': 'FLIP_ENTER_SHORT',
                    }
            except Exception:
                pass

        exhaustion = exhaustion_reversal_evidence(frame, side)
        if exhaustion is not None:
            exhaustion['quote'] = quote
            return f'EXIT_{side}_ON_EXHAUSTION_REVERSAL', exhaustion

        # ── 2. 真實大賣壓當根收線立斬 (EXIT_LONG_ON_REAL_TOP_DUMP) ──
        if side == 'LONG' and candle_closed and frame is not None and len(frame) >= 2 and atr > 0:
            try:
                c_open = float(curr.get('open', quote))
                c_close = float(curr.get('close', quote))
                c_ma5 = float(curr.get('ma5', quote))
                c_ma15 = float(curr.get('ma15', quote))
                entry_p = float(position.get('entry_price', 0.0))
                is_above_ma15_context = c_open > c_ma15 or entry_p < c_open

                # 價格脫離 MA15 後的高位，當根為實體陰線 (Open - Close >= 0.35 * ATR) 且實體跌破 MA5 (Close < ma5)
                if is_above_ma15_context and (c_open - c_close) >= 0.35 * atr and c_close < c_ma5:
                    return 'EXIT_LONG_ON_REAL_TOP_DUMP', {
                        'open': c_open, 'close': c_close, 'ma5': c_ma5,
                        'atr': atr, 'quote': quote, 'side': side,
                    }
            except Exception:
                pass

        # ── 3. 階梯鎖利：以淨 ROE 峰值計算保護底線 ──
        ratchet = _ratchet_lock_details(position, quote)
        if ratchet is not None:
            ratchet.update(
                highest_price=highest_price, lowest_price=lowest_price,
                entry_price=entry_price, quote=quote, side=side,
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

        # Every intrabar strategy exit waits for candle finality except the
        # verified extreme V-reversal and the ROE ratchet lock.
        if frame is not None and not frame.empty and not _current_bar_is_closed(frame):
            if clean_reason in (
                'EXIT_BY_EXTREME_WATERFALL',
                'CATASTROPHIC_DUMP_EXIT',
                'CATASTROPHIC_PUMP_EXIT',
            ):
                try:
                    live = frame.iloc[-1]
                    previous = frame.iloc[-2] if len(frame) > 1 else live
                    atr = float(previous.get('atr', live.get('atr', 0.0)))
                    opening = float(live.get('open', quote))
                    high = max(float(live.get('high', quote)), float(quote))
                    low = min(float(live.get('low', quote)), float(quote))
                    middle = float(live.get('kc_middle', live.get('kc_basis', 0.0)))
                    candle_range = high - low
                    if side == 'LONG':
                        extreme = float(quote) < middle <= high
                    else:
                        extreme = low <= middle < float(quote)
                    if (math.isfinite(atr) and math.isfinite(middle)
                            and math.isfinite(candle_range) and atr > 0 and middle > 0
                            and candle_range >= 1.2 * atr and extreme):
                        return _auth('EXIT_BY_EXTREME_WATERFALL')
                except (KeyError, TypeError, ValueError, OverflowError, IndexError):
                    pass
                return _reject(wait_for_close=True)

            if clean_reason == 'EXIT_BY_RATCHET_PROFIT_LOCK' or clean_reason.startswith('PROFIT_LOCK'):
                lock = _ratchet_lock_details(position, quote)
                if lock is not None:
                    details.update(lock)
                    return _auth('EXIT_BY_RATCHET_PROFIT_LOCK')
                return _reject(wait_for_close=True)

            return _reject(wait_for_close=True)

        # ── 2. 極端異常大瀑布 (WHITELIST SCENARIO 1) ──
        if clean_reason in ('EXIT_BY_EXTREME_WATERFALL', 'CATASTROPHIC_DUMP_EXIT', 'CATASTROPHIC_PUMP_EXIT'):
            return _auth('EXIT_BY_EXTREME_WATERFALL')

        # ── 3. 階梯鎖利回吐 (WHITELIST SCENARIO 3) ──
        if clean_reason == 'EXIT_BY_RATCHET_PROFIT_LOCK' or clean_reason.startswith('PROFIT_LOCK'):
            lock = _ratchet_lock_details(position, quote)
            if lock is not None:
                details.update(lock)
                return _auth('EXIT_BY_RATCHET_PROFIT_LOCK')
            return _reject()

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
                if peak_roe < 0.03 and details.get('peak_roe', 0.0) < 0.03:
                    return _reject()
                # 多單下影線防護
                if details.get('lower_wick_hold'):
                    return _reject()
                if _long_reversal_body_evidence(frame, quote, float(details.get('atr') or 0.0)) is None:
                    return _reject()
                authorized_code = 'EXIT_BY_VERIFIED_FRACTAL_PEAK'
            else:
                peak_roe = (entry_price - lowest_price) / entry_price if entry_price > 0 else 0.0
                peak_gain_atr = float(details.get('peak_gain_atr') or 0.0)
                if (peak_roe < 0.03 and details.get('valley_roe', 0.0) < 0.03
                        and peak_gain_atr < 0.5):
                    return _reject()
                body_evidence = _short_reversal_body_evidence(
                    frame, quote, float(details.get('atr') or 0.0),
                )
                if body_evidence is None:
                    return _reject()
                authorized_code = 'EXIT_BY_VERIFIED_FRACTAL_VALLEY'

            return _auth(authorized_code)

        # ── 5. 真實大賣壓當根收線立斬 / 頂部斷頭鍘特例 ──
        if clean_reason in ('EXIT_LONG_ON_REAL_TOP_DUMP', 'EXIT_LONG_ON_TOP_WATERFALL_DUMP'):
            if side != 'LONG':
                return _reject()
            if frame is not None and len(frame) >= 2:
                try:
                    c = frame.iloc[-1]
                    p = frame.iloc[-2]
                    a = float(p.get('atr', c.get('atr', 0.0)))
                    o_p = float(c.get('open', quote))
                    cl_p = float(c.get('close', quote))
                    ma5_val = float(c.get('ma5', quote))
                    p_l = float(p.get('low', cl_p))
                    if clean_reason == 'EXIT_LONG_ON_TOP_WATERFALL_DUMP':
                        if a > 0 and (o_p - cl_p) >= 0.45 * a and cl_p < ma5_val and cl_p < p_l:
                            return _auth('EXIT_LONG_ON_TOP_WATERFALL_DUMP')
                    elif a > 0 and (o_p - cl_p) >= 0.35 * a and cl_p < ma5_val:
                        return _auth('EXIT_LONG_ON_REAL_TOP_DUMP')
                except Exception:
                    pass
            return _auth(clean_reason)

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
