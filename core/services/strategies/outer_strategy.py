"""Confirmed CK trend entries; legacy rail helpers remain for exit compatibility.
Implements IEntryStrategy interface.
"""
import math
from typing import Dict, Any, Tuple
import pandas as pd
from core.interfaces.entry_interface import IEntryStrategy

LIVE_BODY_BREAKOUT_CODES = {"KC_LIVE_BODY_BREAKOUT_LONG", "KC_LIVE_BODY_BREAKOUT_SHORT"}
LIVE_OUTER_CODES = {"KC_LIVE_OUTER_LONG", "KC_LIVE_OUTER_SHORT"} | LIVE_BODY_BREAKOUT_CODES
LIVE_BREAKOUT_BODY_ATR = 0.5
OUTER_CODES = {"KC_OUTSIDE_LONG", "KC_OUTSIDE_SHORT"}
ENTRY_TREND_CODES = {"KC_TREND_LONG", "KC_TREND_SHORT"}
TREND_CODES = {"KC_MIDDLE_TREND_LONG", "KC_MIDDLE_TREND_SHORT"} | ENTRY_TREND_CODES


def ck_momentum_fading(frame, side):
    """Return closed-bar directional fading, or None for invalid data."""
    try:
        if side not in ('LONG', 'SHORT') or frame is None or len(frame) < 5:
            return None
        key = 'kc_middle' if 'kc_middle' in frame.columns else 'ema_20'
        values = [float(v) for v in frame.iloc[-5:-1][key]]
        if not all(math.isfinite(v) and v > 0 for v in values):
            return None
        sign = 1 if side == 'LONG' else -1
        steps = [sign * (b - a) for a, b in zip(values, values[1:])]
        tolerance = max(values) * 1e-12
        fading = (steps[2] > 0 and steps[0] - steps[1] > tolerance
                  and steps[1] - steps[2] > tolerance)
        return fading
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return None


def ck_entry_momentum_ready(frame, side):
    """Only strengthening confirmed directional CK momentum permits entries."""
    try:
        if ck_momentum_fading(frame, side) is not False:
            return False
        key = 'kc_middle' if 'kc_middle' in frame.columns else 'ema_20'
        a, b, c = [float(v) for v in frame.iloc[-4:-1][key]]
        sign = 1 if side == 'LONG' else -1
        previous, latest = sign * (b - a), sign * (c - b)
        tolerance = max(a, b, c) * 1e-12
        return previous >= 0 and latest > 0 and latest - previous > tolerance
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def aligned_direction(frame, side):
    """CK sets direction without waiting for moving-average alignment."""
    return side in ('LONG', 'SHORT') and ck_direction(frame) == side


def ck_direction(frame):
    """Latest closed middle slope, confirmed by the directional outer slope."""
    try:
        if frame is None or len(frame) < 3:
            return None
        key = 'kc_middle' if 'kc_middle' in frame.columns else 'ema_20'
        rows = [[float(row[k]) for k in ('kc_lower', key, 'kc_upper')]
                for _, row in frame.iloc[-3:-1].iterrows()]
        if any(not all(math.isfinite(v) and v > 0 for v in row)
               or not row[0] < row[1] < row[2] for row in rows):
            return None
        a, b = rows
        if b[1] > a[1] and b[2] >= a[2]:
            return 'LONG'
        if b[1] < a[1] and b[0] <= a[0]:
            return 'SHORT'
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return None
    return None


def live_adverse_entry_safe(frame, price, side):
    """Reject adverse entry bodies at the existing ATR abnormality threshold."""
    from core.config import RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR
    try:
        opened = float(frame.iloc[-1]['open'])
        atr = float(frame.iloc[-2]['atr'])
        price = float(price)
        threshold = atr * RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR
        if (side not in ('LONG', 'SHORT')
                or not all(math.isfinite(v) and v > 0 for v in (opened, price, atr, threshold))):
            return False
        adverse_body = (1 if side == 'SHORT' else -1) * (price - opened)
        return adverse_body < threshold
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def sustained_trend_ready(frame, side):
    """Six closed candles must show persistent progress rather than oscillation."""
    try:
        if side not in ('LONG', 'SHORT') or frame is None or len(frame) < 7:
            return False
        rows = frame.iloc[-7:-1]
        values = [[float(row[k]) for k in ('open', 'high', 'low', 'close', 'kc_middle')]
                  for _, row in rows.iterrows()]
        if not all(math.isfinite(v) and v > 0 for row in values for v in row):
            return False
        if any(not low <= min(o, c) <= max(o, c) <= high for o, high, low, c, m in values):
            return False
        sign = 1 if side == 'LONG' else -1
        if not all(sign * (b[4] - a[4]) > 0 for a, b in zip(values, values[1:])):
            return False
        progress = [sign * (b[1] - a[1]) > 0 and sign * (b[2] - a[2]) > 0
                    for a, b in zip(values, values[1:])]
        if sum(progress) < 4 or not all(progress[-2:]):
            return False
        moves = [b[3] - a[3] for a, b in zip(values, values[1:])]
        distance = sum(abs(v) for v in moves)
        if distance <= 0 or sign * sum(moves) / distance < .70:
            return False
        sides = [1 if c > m else -1 if c < m else 0 for o, h, l, c, m in values]
        nonzero = [v for v in sides if v]
        if sum(a != b for a, b in zip(nonzero, nonzero[1:])) > 1:
            return False
        overlaps = []
        for a, b in zip(values, values[1:]):
            lo_a, hi_a = sorted((a[0], a[3]))
            lo_b, hi_b = sorted((b[0], b[3]))
            smaller = min(hi_a - lo_a, hi_b - lo_b)
            overlaps.append(max(0., min(hi_a, hi_b) - max(lo_a, lo_b)) / smaller if smaller > 0 else 1.)
        return sum(overlaps) / len(overlaps) <= .50
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def ma3_outer_cross_ready(frame, price, side):
    """Cross from the last closed MA3/rail to quote-derived live MA3/rail."""
    try:
        if side not in ('LONG', 'SHORT') or frame is None or len(frame) < 4:
            return False
        closes = [float(v) for v in frame['close'].iloc[-4:-1]]
        price = float(price)
        rail = 'kc_upper' if side == 'LONG' else 'kc_lower'
        previous = float(frame.iloc[-2][rail])
        current = float(frame.iloc[-1][rail])
        if not all(math.isfinite(v) and v > 0 for v in [price, previous, current, *closes]):
            return False
        closed_ma = sum(closes) / 3.
        live_ma = (sum(closes[-2:]) + price) / 3.
        sign = 1 if side == 'LONG' else -1
        return (sign * (closed_ma - previous) <= 0
                and sign * (live_ma - current) > 0
                and sign * (price - current) > 0)
    except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
        return False


def ma3_outer_continuation_ready(frame, price, side):
    """A missed cross stays eligible while live MA3 and price remain outside."""
    try:
        if side not in ('LONG', 'SHORT') or frame is None or len(frame) < 4:
            return False
        closes = [float(v) for v in frame['close'].iloc[-3:-1]]
        rail = float(frame.iloc[-1]['kc_upper' if side == 'LONG' else 'kc_lower'])
        price = float(price)
        if not all(math.isfinite(v) and v > 0 for v in [price, rail, *closes]):
            return False
        sign = 1 if side == 'LONG' else -1
        ma3 = (sum(closes) + price) / 3.
        return sign * (ma3 - rail) > 0 and sign * (price - rail) > 0
    except (AttributeError, KeyError, IndexError, TypeError, ValueError, OverflowError):
        return False


def entry_trend_direction(frame):
    """Entry direction uses only the last two completed CK middle values."""
    try:
        if frame is None or len(frame) < 3:
            return None
        key = 'kc_middle' if 'kc_middle' in frame.columns else 'ema_20'
        previous, latest = [float(v) for v in frame[key].iloc[-3:-1]]
        if not all(math.isfinite(v) and v > 0 for v in (previous, latest)):
            return None
        return 'LONG' if latest > previous else 'SHORT' if latest < previous else None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def live_body_breakout_side(frame, price):
    """Current real body crosses an outer rail by quote, sized on closed ATR."""
    try:
        if frame is None or len(frame) < 2:
            return None
        row = frame.iloc[-1]
        opened, upper, lower, atr, price = (
            float(row['open']), float(row['kc_upper']), float(row['kc_lower']),
            float(frame.iloc[-2]['atr']), float(price))
        if (not all(math.isfinite(v) and v > 0 for v in (opened, upper, lower, atr, price))
                or lower >= upper):
            return None
        threshold = atr * LIVE_BREAKOUT_BODY_ATR
        if opened <= upper < price and price - opened >= threshold:
            return 'LONG'
        if price < lower <= opened and opened - price >= threshold:
            return 'SHORT'
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        pass
    return None


def aligned_entry(frame, price, **kwargs):
    """Closed KC direction with live MA3 outside; missed crosses may continue."""
    wait = {"action": "WAIT", "side": None, "reason": "KC_DIRECTION_WAIT"}
    try:
        price = float(price)
        if not math.isfinite(price) or price <= 0 or frame is None or len(frame) < 4:
            return wait
            
        # --- V5.1 防禦性冷卻機制 (The Safety Net) ---
        is_system_halted = kwargs.get("is_system_halted", False)
        if is_system_halted:
            return {**wait, "reason": "WAIT_SYSTEM_HALT"}
            
        last_exit_bar = kwargs.get("last_exit_bar")
        if last_exit_bar is not None:
            if 'timestamp' in frame.columns:
                matches = frame.index[frame['timestamp'] == last_exit_bar].tolist()
            else:
                matches = frame.index[frame.index == last_exit_bar].tolist()
            if matches:
                last_idx = frame.index.get_loc(matches[0])
                curr_idx = len(frame) - 1
                if curr_idx - last_idx < 3:
                    return {**wait, "reason": "WAIT_COOL_DOWN"}
        
        # --- V5.0 Environment Filters ---
        from core.config import ENV_MIN_KC_BANDWIDTH, ENV_MIN_KC_SLOPE, ENV_ATR_EXPANSION_RATIO
        row = frame.iloc[-1]
        kc_upper = float(row.get('kc_upper', 0))
        kc_lower = float(row.get('kc_lower', 0))
        kc_middle = float(row.get('kc_middle', row.get('ema_20', 0)))
        # --- V5.2 Dynamic Bandwidth Filter (動態寬度過濾) ---
        is_bandwidth_ok = (kc_middle > 0 and (kc_upper - kc_lower) / kc_middle >= ENV_MIN_KC_BANDWIDTH)
        atr_expanding = False
        if 'atr' in frame.columns and len(frame) > 6:
            recent_atr = frame['atr'].iloc[-3:].mean()
            prev_atr = frame['atr'].iloc[-6:-3].mean()
            if prev_atr > 0 and (recent_atr / prev_atr - 1) >= 0.20:
                atr_expanding = True
                
        if not is_bandwidth_ok and not atr_expanding:
            return {**wait, "reason": "ENV_FILTER_BANDWIDTH_REJECTED"}
        
        # Slope Filter (middle slope)
        prev_row = frame.iloc[-2]
        prev_middle = float(prev_row.get('kc_middle', prev_row.get('ema_20', 0)))
        if abs(kc_middle - prev_middle) < ENV_MIN_KC_SLOPE:
            return {**wait, "reason": "ENV_FILTER_SLOPE_REJECTED"}
            
        # Volatility Filter (Short ATR vs Long ATR average if available)
        if 'atr' in frame.columns and len(frame) > 4:
            short_atr = frame['atr'].iloc[-3:].mean()
            long_atr = frame['atr'].mean()
            if long_atr > 0 and (short_atr / long_atr) < ENV_ATR_EXPANSION_RATIO:
                return {**wait, "reason": "ENV_FILTER_VOLATILITY_REJECTED"}
        # --------------------------------
        
        for _, row in frame.iloc[-4:].iterrows():
            opened, high, low, closed = (float(row[k]) for k in ("open", "high", "low", "close"))
            if (not all(math.isfinite(v) and v > 0 for v in (opened, high, low, closed))
                    or not low <= min(opened, closed) <= max(opened, closed) <= high):
                return wait
        side = ck_direction(frame)
        if not aligned_direction(frame, side):
            return wait
            
        # --- V5.1 Final Final Update (極致防禦與無狀態回顧) ---
        curr_k = frame.iloc[-1]
        prev_k = frame.iloc[-2]
        kc_lower = float(curr_k.get('kc_lower', 0))
        kc_upper = float(curr_k.get('kc_upper', 0))
        kc_middle = float(curr_k.get('kc_middle', curr_k.get('ema_20', 0)))
        atr = float(curr_k.get('atr', 1.0))
        
        c_open = float(curr_k.get('open', price))
        c_close = float(curr_k.get('close', price))
        c_low = float(curr_k.get('low', price))
        c_high = float(curr_k.get('high', price))
        c_body = abs(c_close - c_open)
        
        p_open = float(prev_k.get('open', price))
        p_close = float(prev_k.get('close', price))
        
        if side == "SHORT":
            # 1. 邊界過濾 (防地板空): 若 Price <= LowerBand 或 Prev_Close <= LowerBand -> 檢查緩衝帶
            if price <= kc_lower or p_close <= kc_lower:
                velocity_slowdown = kwargs.get("velocity_slowdown", False)
                if (kc_lower - price) <= 0.3 * atr and velocity_slowdown:
                    pass
                else:
                    return {**wait, "reason": "REJECTED_OUTSIDE_BAND_OVEREXTENDED"}
                
            # 2. 乖離限制: (kc_middle - Price) / ATR > 1.5 -> 拒絕
            if atr > 0 and (kc_middle - price) / atr > 1.5:
                return {**wait, "reason": "ANTI_CHASE_DISTANCE_LIMIT"}
                
            # 3. 反轉過濾 (Bullish Reversal Filter): 陽線或長下影線 -> 拒絕
            if c_close > c_open:
                return {**wait, "reason": "ANTI_CHASE_GREEN_CANDLE"}
            lower_wick = min(c_open, c_close) - c_low
            if c_body > 0 and (lower_wick / c_body) > 1.0:
                return {**wait, "reason": "ANTI_CHASE_REJECTION_CANDLE"}
                
        elif side == "LONG":
            # 1. 邊界過濾 (防天花板多): 若 Price >= UpperBand -> 檢查緩衝帶
            if price >= kc_upper:
                velocity_slowdown = kwargs.get("velocity_slowdown", False)
                if (price - kc_upper) <= 0.3 * atr and velocity_slowdown:
                    pass
                else:
                    return {**wait, "reason": "REJECTED_OUTSIDE_BAND_OVEREXTENDED"}
                
            # 2. 乖離限制: (Price - kc_middle) / ATR > 1.5 -> 拒絕
            if atr > 0 and (price - kc_middle) / atr > 1.5:
                return {**wait, "reason": "ANTI_CHASE_DISTANCE_LIMIT"}
                
            # 3. 趨勢對齊: ema_50 斜率若向下則限制
            if len(frame) > 2:
                ema50_curr = float(curr_k.get('ema_50', 0))
                ema50_prev = float(prev_k.get('ema_50', 0))
                if ema50_curr > 0 and ema50_prev > 0 and (ema50_curr - ema50_prev) <= 0:
                    return {**wait, "reason": "ANTI_CHASE_TREND_MISALIGNMENT"}
                    
            # 4. 過度延伸: 連續 >= 2 根收盤價 > kc_upper 且實體 > 1.2 ATR -> 拒絕
            if len(frame) >= 2:
                is_consecutive_overextended = True
                for i in range(1, 3):
                    k_row = frame.iloc[-i]
                    k_close, k_open, k_upper = float(k_row['close']), float(k_row['open']), float(k_row['kc_upper'])
                    k_body = abs(k_close - k_open)
                    if not (k_close > k_upper and k_body > 1.2 * atr):
                        is_consecutive_overextended = False
                        break
                if is_consecutive_overextended:
                    return {**wait, "reason": "ANTI_CHASE_OVEREXTENDED"}
        # ----------------------------------------
        if not live_ma3_direction_ready(frame, price, side):
            return {**wait, "reason": "KC_LIVE_MA3_DIRECTION_WAIT"}
        if not live_candle_color_ready(frame, price, side):
            return {**wait, "reason": "KC_LIVE_CANDLE_DIRECTION_WAIT"}
        if not live_adverse_entry_safe(frame, price, side):
            return {**wait, "reason": "KC_LIVE_ADVERSE_ENTRY_WAIT"}
        if ma3_outer_cross_ready(frame, price, side):
            return {"action": "ENTER", "side": side, "reason": "KC_LIVE_OUTER_" + side}
        if ma3_outer_continuation_ready(frame, price, side):
            return {"action": "ENTER", "side": side, "reason": "KC_OUTSIDE_" + side}
        return {**wait, "reason": "KC_MA3_OUTSIDE_WAIT"}
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return wait


def aligned_entry_ready(frame, price, side, **kwargs):
    return side in ('LONG', 'SHORT') and aligned_entry(frame, price, **kwargs).get('side') == side


def live_ma3_direction_ready(frame, price, side):
    """Compare quote-derived live MA3 with closed MA3, ignoring stale live rows."""
    try:
        closes = [float(v) for v in frame["close"].iloc[-4:-1]]
        price = float(price)
        if (side not in ("LONG", "SHORT") or len(closes) != 3
                or not all(math.isfinite(v) and v > 0 for v in [price, *closes])):
            return False
        return (1 if side == "LONG" else -1) * (price - closes[0]) > 0
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def live_candle_color_ready(frame, price, side):
    """Require a green live body for longs and a red live body for shorts."""
    try:
        if side not in ("LONG", "SHORT") or frame is None or frame.empty:
            return False
        opened = float(frame.iloc[-1]["open"])
        price = float(price)
        if not all(math.isfinite(value) and value > 0 for value in (opened, price)):
            return False
        return (1 if side == "LONG" else -1) * (price - opened) >= 0
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def confirmed_outer_breakout_ready(frame, price, side, allow_three_short=False):
    """V5.1 Breakout Validation: K1 breakout + K2 solid confirmation."""
    try:
        from core.config import SOLID_BODY_RATIO
        if side not in ("LONG", "SHORT") or frame is None or len(frame) < 4:
            return False
            
        sign = 1 if side == "LONG" else -1
        rail = "kc_upper" if side == "LONG" else "kc_lower"
        
        # K3 (iloc[-4]), K2 (iloc[-3]), K1 (iloc[-2]), Live (iloc[-1])
        # Wait, the user defined K2 as the most recently closed candle and K1 as the previous.
        # In our frame, iloc[-2] is the most recently closed, and iloc[-3] is the one before it.
        # Let's call them prev_closed (K1) and latest_closed (K2).
        k1 = frame.iloc[-3]
        k2 = frame.iloc[-2]
        live = frame.iloc[-1]
        
        # Check K2 (latest closed candle) is a solid directional body
        k2_open, k2_close, k2_high, k2_low = float(k2['open']), float(k2['close']), float(k2['high']), float(k2['low'])
        k2_solid = False
        if (k2_high - k2_low) > 0 and sign * (k2_close - k2_open) > 0:
            if abs(k2_close - k2_open) / (k2_high - k2_low) >= SOLID_BODY_RATIO:
                k2_solid = True
                
        # Check K1 (previous closed candle) broke the band
        k1_rail = float(k1[rail])
        k1_open, k1_close = float(k1['open']), float(k1['close'])
        k1_broke = (sign * (k1_open - k1_rail) <= 0) and (sign * (k1_close - k1_rail) > 0)
        
        # Condition A: K1 broke and K2 is solid
        if k1_broke and k2_solid:
            return True
            
        # Condition B: K2 is weak body, look back to K3 and K1
        k3 = frame.iloc[-4]
        k1_open2, k1_close2, k1_high, k1_low = float(k1['open']), float(k1['close']), float(k1['high']), float(k1['low'])
        k1_solid = False
        if (k1_high - k1_low) > 0 and sign * (k1_close2 - k1_open2) > 0:
            if abs(k1_close2 - k1_open2) / (k1_high - k1_low) >= SOLID_BODY_RATIO:
                k1_solid = True
                
        k3_rail = float(k3[rail])
        k3_open, k3_close = float(k3['open']), float(k3['close'])
        k3_broke = (sign * (k3_open - k3_rail) <= 0) and (sign * (k3_close - k3_rail) > 0)
        
        if k3_broke and k1_solid and not k2_solid:
            return True

        return False
    except (AttributeError, TypeError, ValueError, KeyError, IndexError):
        return False


def outside_entry(frame, price):
    wait = {"action": "WAIT", "side": None, "reason": "KC_INSIDE_CHANNEL"}
    try:
        row = frame.iloc[-1]
        lower, upper, price = float(row['kc_lower']), float(row['kc_upper']), float(price)
        if not all(math.isfinite(v) and v > 0 for v in (lower, upper, price)) or lower >= upper:
            return {**wait, "reason": "KC_DATA_INVALID"}
        side = 'LONG' if price > upper else 'SHORT' if price < lower else None
        if side:
            return {**wait, "reason": "KC_OUTSIDE_WAIT_NEXT_CANDLE"}

    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return {**wait, "reason": "KC_DATA_UNAVAILABLE"}
    return wait


def continuation_entry(frame, price):
    """Enter after closed breakout confirmation; shorts may bridge one small red."""
    wait = {"action": "WAIT", "side": None, "reason": "KC_CONTINUATION_WAIT"}
    required = {"open", "high", "low", "close", "ma3", "ma15", "kc_upper", "kc_lower"}
    if frame is None or len(frame) < 5 or not required.issubset(frame.columns):
        return {**wait, "reason": "KC_CONTINUATION_DATA_UNAVAILABLE"}
    try:
        breakout, confirmation, live = frame.iloc[-3], frame.iloc[-2], frame.iloc[-1]
        values = [float(row[key]) for row in (breakout, confirmation, live) for key in required]
        values.append(float(price))
        if not all(math.isfinite(value) and value > 0 for value in values):
            return {**wait, "reason": "KC_CONTINUATION_DATA_INVALID"}
        body = abs(float(confirmation["close"]) - float(confirmation["open"]))
        candle_range = float(confirmation["high"]) - float(confirmation["low"])
        if candle_range <= 0 or body / candle_range < 0.20:
            return wait
        ma3 = [float(row["ma3"]) for row in (breakout, confirmation, live)]
        if not all(math.isfinite(value) and value > 0 for value in ma3):
            return {**wait, "reason": "KC_CONTINUATION_DATA_INVALID"}
        long_signal = (
            float(breakout["open"]) <= float(breakout["kc_upper"]) < float(breakout["close"])
            and float(confirmation["close"]) > float(confirmation["open"])
            and float(confirmation["close"]) > float(confirmation["kc_upper"])
            and float(price) > float(live["kc_upper"])
            and float(confirmation["kc_upper"]) >= float(breakout["kc_upper"])
            and ma3[0] < ma3[1] < ma3[2]
        )
        short_signal = (
            (float(breakout["open"]) >= float(breakout["kc_lower"]) > float(breakout["close"])
             or three_closed_short_breakout_ready(frame, price))
            and float(confirmation["close"]) < float(confirmation["open"])
            and float(confirmation["close"]) < float(confirmation["kc_lower"])
            and float(price) < float(live["kc_lower"])
            and float(confirmation["kc_lower"]) <= float(breakout["kc_lower"])
            and ma3[0] > ma3[1] > ma3[2]
        )
        if long_signal and aligned_direction(frame, "LONG") and confirmed_outer_breakout_ready(frame, price, "LONG"):
            return {"action": "ENTER", "side": "LONG", "reason": "KC_CONTINUATION_LONG"}
        if short_signal and aligned_direction(frame, "SHORT") and confirmed_outer_breakout_ready(frame, price, "SHORT"):
            return {"action": "ENTER", "side": "SHORT", "reason": "KC_CONTINUATION_SHORT"}
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return {**wait, "reason": "KC_CONTINUATION_DATA_INVALID"}
    return wait


def outside_reentry(frame, price, side, **kwargs):
    """Use the same confirmed CK trend for normal reentries."""
    decision = aligned_entry(frame, price, **kwargs)
    if side not in ("LONG", "SHORT") or decision.get("side") != side:
        return {"action": "WAIT", "side": None, "reason": "KC_REENTRY_WAIT"}
    return decision


def abnormal_pullback_ready(ticket, frame, price):
    """After a close, observe a later candle inside CK before a fresh reclaim."""
    try:
        row = frame.iloc[-1]
        bar = float(row.get("timestamp", row.name))
        exited = float(ticket["exit_bar_id"])
        upper, lower = float(row["kc_upper"]), float(row["kc_lower"])
        if (not all(math.isfinite(v) for v in (bar, exited, upper, lower, price))
                or not 0 < lower < upper or price <= 0 or bar <= exited):
            return False
        if lower <= price <= upper:
            ticket["pullback_bar"] = bar
            return False
        pulled = float(ticket.get("pullback_bar", float("nan")))
        return math.isfinite(pulled) and exited < pulled <= bar
    except (AttributeError, TypeError, ValueError, KeyError, IndexError):
        return False


class OuterChannelEntryStrategy(IEntryStrategy):
    """OOP Strategy class implementing IEntryStrategy for outer channel entry evaluation."""

    def evaluate_entry(
        self,
        frame: pd.DataFrame,
        price: float,
        side: str,
        **kwargs: Any
    ) -> Tuple[bool, str, Dict[str, Any]]:
        decision = aligned_entry(frame, price, **kwargs)
        if decision.get("action") == "ENTER" and decision.get("side") == side:
            return True, decision.get("reason", "OK"), decision
        return False, decision.get("reason", "WAIT"), decision
