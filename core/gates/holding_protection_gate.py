"""Holding Protection Exit Gate: Protects active positions from premature pullback exits.

Enforces an explicit exit whitelist:
1. EXIT_BY_EXTREME_WATERFALL: Extreme vertical adverse bar >= 1.2*ATR breaching opposite KC rail.
2. EXIT_BY_CIRCUIT_BREAKER_HARD_SL: Hard Stop Loss or market circuit breaker.
3. EXIT_BY_RATCHET_PROFIT_LOCK: Peak ROE >= 3.5%, giveback > 25% from peak (locking 75% profit).
4. EXIT_BY_VERIFIED_FRACTAL_PEAK / EXIT_BY_VERIFIED_FRACTAL_VALLEY:
   Preconditioned on meaningful profit (ROE >= 3.0% or gain >= 0.5*ATR),
   3-bar fractal peak/valley with confirmed body break of previous midpoint.
5. Directional wick rejection: profitable LONG upper-wick exhaustion or SHORT
   lower-wick exhaustion, each >= 0.5*ATR and > 2x the real body.

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


def _short_reversal_body_evidence(frame, quote, atr):
    if frame is None or len(frame) < 2 or not math.isfinite(atr) or atr <= 0:
        return None
    try:
        live = frame.iloc[-1]
        previous = frame.iloc[-2]
        opening = float(live['open'])
        raw_close = float(live['close'])
        closed = bool(live.get('is_closed', False))
        effective = raw_close if closed else float(quote)
        high = max(float(live['high']), effective)
        low = min(float(live['low']), effective)
        body = effective - opening
        candle_range = high - low
        upper_wick = high - max(opening, effective)
        ma5 = float(live['ma5'])
        if not closed:
            ma5 += (effective - raw_close) / 5.0
        middle = float(live.get('kc_middle', live.get('kc_basis', 0.0)))
        previous_mid = (
            float(previous['open']) + float(previous['close'])
        ) / 2.0
        values = (opening, raw_close, effective, high, low, body, candle_range,
                  upper_wick, ma5, middle, previous_mid)
        if (not all(math.isfinite(value) for value in values)
                or min(opening, raw_close, effective, high, low, ma5, middle, previous_mid) <= 0
                or candle_range <= 0):
            return None
        body_ratio = body / candle_range
        body_threshold_failed = body <= 0.35 * atr if not closed else body < 0.35 * atr
        if (upper_wick >= 0.4 * atr or body_threshold_failed
                or body_ratio < 0.60 or effective <= previous_mid
                or effective <= ma5 or effective <= middle):
            return None
        return {
            'effective_price': effective, 'prev_mid': previous_mid,
            'ma5': ma5, 'kc_middle': middle, 'atr': atr,
            'body': body, 'body_ratio': body_ratio,
            'upper_wick': upper_wick,
        }
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


class HoldingProtectionExitGate:
    """Gatekeeper ensuring position is never closed on normal pullbacks."""

    REJECT_REASON = "REJECT_EXIT: BLOCKED_BY_HOLDING_PROTECTION_GATE (Normal Pullback Breathing)"

    WHITELIST_CODES = {
        'EXIT_BY_EXTREME_WATERFALL',
        'EXIT_BY_CIRCUIT_BREAKER_HARD_SL',
        'EXIT_BY_RATCHET_PROFIT_LOCK',
        'EXIT_BY_VERIFIED_FRACTAL_PEAK',
        'EXIT_BY_VERIFIED_FRACTAL_VALLEY',
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

        # ── 1. 極端異常：大瀑布反轉 / 極速反撲 (EXIT_BY_EXTREME_WATERFALL) ──
        if frame is not None and len(frame) >= 1 and atr > 0:
            curr_open = float(curr.get('open', quote))
            kc_upper = float(curr.get('kc_upper', 0.0))
            kc_lower = float(curr.get('kc_lower', 0.0))

            if side == 'LONG':
                # 多單持倉：垂直暴跌單棒跌幅 >= 1.2 * ATR 且實體跌穿 KC 下軌
                bar_drop = curr_open - quote
                if bar_drop >= 1.2 * atr and kc_lower > 0 and quote < kc_lower:
                    return 'EXIT_BY_EXTREME_WATERFALL', {
                        'bar_drop': bar_drop, 'atr': atr, 'kc_lower': kc_lower,
                        'quote': quote, 'side': side,
                    }
            elif side == 'SHORT':
                # 空單持倉：垂直暴漲單棒漲幅 >= 1.2 * ATR 且實體漲穿 KC 上軌
                bar_pump = quote - curr_open
                if bar_pump >= 1.2 * atr and kc_upper > 0 and quote > kc_upper:
                    return 'EXIT_BY_EXTREME_WATERFALL', {
                        'bar_pump': bar_pump, 'atr': atr, 'kc_upper': kc_upper,
                        'quote': quote, 'side': side,
                    }

        # ── 3. 階梯鎖利：利潤回吐鎖利 (EXIT_BY_RATCHET_PROFIT_LOCK) ──
        # 浮盈拉開後（峰值 ROE >= 3.5%），從最高浮盈回吐超過 25% 觸發鎖利平倉
        if side == 'LONG':
            peak_roe = (highest_price - entry_price) / entry_price
            if peak_roe >= 0.035:
                peak_gain = highest_price - entry_price
                giveback_amount = highest_price - quote
                giveback_ratio = giveback_amount / peak_gain if peak_gain > 0 else 0.0
                if giveback_ratio >= 0.25:
                    return 'EXIT_BY_RATCHET_PROFIT_LOCK', {
                        'peak_roe': peak_roe, 'giveback_ratio': giveback_ratio,
                        'highest_price': highest_price, 'entry_price': entry_price,
                        'quote': quote, 'side': side,
                    }
        elif side == 'SHORT':
            peak_roe = (entry_price - lowest_price) / entry_price
            if peak_roe >= 0.035:
                peak_gain = entry_price - lowest_price
                giveback_amount = quote - lowest_price
                giveback_ratio = giveback_amount / peak_gain if peak_gain > 0 else 0.0
                if giveback_ratio >= 0.25:
                    return 'EXIT_BY_RATCHET_PROFIT_LOCK', {
                        'peak_roe': peak_roe, 'giveback_ratio': giveback_ratio,
                        'lowest_price': lowest_price, 'entry_price': entry_price,
                        'quote': quote, 'side': side,
                    }

        # ── 4. 結構確認：真實波段峰頂 / 谷底生成 (EXIT_BY_VERIFIED_FRACTAL_PEAK / VALLEY) ──
        if frame is not None and len(frame) >= 3:
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
                    candle_closed = bool(curr.get('is_closed', False))
                    effective_price = curr_close if candle_closed else quote

                    # 多單遭遇長下影線下探回升，嚴禁平倉
                    curr_low = float(curr.get('low', quote))
                    lower_wick = min(curr_open, effective_price) - curr_low
                    if atr > 0 and lower_wick >= 0.4 * atr:
                        return None, {'lower_wick_hold': True}

                    if effective_price < prev_mid:
                        return 'EXIT_BY_VERIFIED_FRACTAL_PEAK', {
                            'peak_roe': peak_roe, 'prev_mid': prev_mid,
                            'effective_price': effective_price, 'quote': quote,
                        }

            elif side == 'SHORT':
                peak_roe = (entry_price - lowest_price) / entry_price
                peak_gain_atr = (entry_price - lowest_price) / atr if atr > 0 else 0.0
                has_meaningful_profit = (peak_roe >= 0.03) or (peak_gain_atr >= 0.5)

                if has_meaningful_profit and is_fractal_valley(frame):
                    body_evidence = _short_reversal_body_evidence(frame, quote, atr)
                    if body_evidence is not None:
                        body_evidence.update(
                            valley_roe=peak_roe, peak_gain_atr=peak_gain_atr,
                            quote=quote,
                        )
                        return 'EXIT_BY_VERIFIED_FRACTAL_VALLEY', body_evidence
                    live = frame.iloc[-1]
                    effective = (
                        float(live.get('close', quote))
                        if bool(live.get('is_closed', False)) else float(quote)
                    )
                    upper_wick = max(float(live.get('high', effective)), effective) - max(
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

        def _reject():
            reject_msg = f"[HOLDING_PROTECTION] Rejected exit for {symbol}: Normal Pullback Breathing"
            logger.info(reject_msg)
            print(reject_msg, flush=True)
            return False, cls.REJECT_REASON, details

        # ── 1. 硬止損 / 市場熔斷 (WHITELIST SCENARIO 2) ──
        if any(marker in clean_reason.upper() for marker in (
            'HARD_STOP', 'CIRCUIT_BREAKER', 'HARD_SL', 'ACCOUNT_HARD_STOP',
            'EMERGENCY_STOP', 'EXIT_BY_CIRCUIT_BREAKER_HARD_SL'
        )):
            return _auth('EXIT_BY_CIRCUIT_BREAKER_HARD_SL')

        # ── 2. 極端異常大瀑布 (WHITELIST SCENARIO 1) ──
        if clean_reason in ('EXIT_BY_EXTREME_WATERFALL', 'CATASTROPHIC_DUMP_EXIT', 'CATASTROPHIC_PUMP_EXIT'):
            return _auth('EXIT_BY_EXTREME_WATERFALL')

        # ── 3. 階梯鎖利回吐 (WHITELIST SCENARIO 3) ──
        if clean_reason == 'EXIT_BY_RATCHET_PROFIT_LOCK' or clean_reason.startswith('PROFIT_LOCK'):
            return _auth('EXIT_BY_RATCHET_PROFIT_LOCK')

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

        # ── 5. 其餘常規回踩（MA 回碰、影線抖動、未達標轉向等）一律一票否決 ──
        return _reject()
