"""Confirmed CK trend entries; legacy rail helpers remain for exit compatibility.
Implements IEntryStrategy interface.
"""
import math
import os
from typing import Dict, Any, Tuple
import pandas as pd
from core.interfaces.entry_interface import IEntryStrategy
from core.config import (
    CHANNEL_TAIL_MAX_TREND_BARS, CHANNEL_ENTRY_MAX_BODY_ATR, CHANNEL_ENTRY_MAX_PREV_BODY_ATR,
    CHANNEL_FLAT_MIDDLE_RATIO, CHANNEL_LIVE_BODY_BREAKOUT_ENABLED, CHANNEL_LIVE_BREAKOUT_BODY_ATR,
    CHANNEL_SPECIAL_K_REQUIRES_CONFIRMATION,
    CHANNEL_MIN_DIRECTION_EFFICIENCY, CHANNEL_LONG_BODY_ENTRY_ATR,
    CHANNEL_STRONG_TREND_RATIO, CHANNEL_MIN_ATR_PCT,
)

LIVE_BODY_BREAKOUT_CODES = {"KC_LIVE_BODY_BREAKOUT_LONG", "KC_LIVE_BODY_BREAKOUT_SHORT"}
LIVE_OUTER_CODES = {"KC_LIVE_OUTER_LONG", "KC_LIVE_OUTER_SHORT"} | LIVE_BODY_BREAKOUT_CODES
LIVE_BREAKOUT_BODY_ATR = CHANNEL_LIVE_BREAKOUT_BODY_ATR
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
        tolerance = max(a[1], b[1]) * 1e-12
        if b[1] - a[1] > tolerance:
            return 'LONG'
        if a[1] - b[1] > tolerance:
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
    """Entry direction uses last two completed CK middle values, with a fast-track for V-shape reversals."""
    try:
        if frame is None or len(frame) < 3:
            return None
            
        # Fast-track for strong V-shape reversals (overrides slow CK)
        try:
            ma3 = float(frame['ma3'].iloc[-1])
            ma15 = float(frame['ma15'].iloc[-1])
            ma15_prev = float(frame['ma15'].iloc[-2])
            upper = float(frame['kc_upper'].iloc[-1])
            lower = float(frame['kc_lower'].iloc[-1])
            upper_prev = float(frame['kc_upper'].iloc[-2])
            lower_prev = float(frame['kc_lower'].iloc[-2])
            close_price = float(frame['close'].iloc[-1])
            open_price = float(frame['open'].iloc[-1])
            
            body = abs(close_price - open_price)
            atr = float(frame['atr'].iloc[-2]) if 'atr' in frame.columns else 0.0
            is_significant = atr > 0 and body >= 0.5 * atr
            
            if is_significant and close_price > open_price and ma3 > ma15 and ma15 > ma15_prev and upper > upper_prev:
                return 'LONG'
            if is_significant and close_price < open_price and ma3 < ma15 and ma15 < ma15_prev and lower < lower_prev:
                return 'SHORT'
        except (KeyError, IndexError, TypeError, ValueError):
            pass

        key = 'kc_middle' if 'kc_middle' in frame.columns else 'ema_20'
        previous, latest = [float(v) for v in frame[key].iloc[-3:-1]]
        if not all(math.isfinite(v) and v > 0 for v in (previous, latest)):
            return None
        return 'LONG' if latest > previous else 'SHORT' if latest < previous else None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def special_volume_surge_ok(frame, index, ratio=1.5):
    """特例K量能確認：該根成交量 ≥ ratio × 近20根均量（當根未收線按已過時間比例換算）。

    2026-09-14 使用者：假突破＝沒量 → 量能沒放大的特例K不開。資料不足（暖機／合成框架）時不做過濾。
    """
    try:
        import time as _time
        if frame is None or "volume" not in frame.columns or "high" not in frame.columns:
            return True
        if len(frame) < 22:
            return True
        vols = [float(v) for v in frame["volume"]]
        base = vols[index - 20:index]
        if len(base) < 20:
            return True
        mean = sum(base) / len(base)
        if not (math.isfinite(mean) and mean > 0):
            return True
        volume = vols[index]
        if not math.isfinite(volume) or volume < 0:
            return False
        if index == -1 and "timestamp" in frame.columns:
            try:
                opened = float(frame["timestamp"].iloc[-1]) / 1000.0
                elapsed = min(max((_time.time() - opened) / 60.0, 0.15), 1.0)
                volume = volume / elapsed
            except (TypeError, ValueError, IndexError):
                pass
        return volume >= mean * ratio
    except (AttributeError, KeyError, IndexError, TypeError, ValueError):
        return True


def live_body_breakout_side(frame, price):
    """Classify an active special K from directional body size alone."""
    try:
        if frame is None or len(frame) < 2:
            return None
        row = frame.iloc[-1]
        opened, upper, lower, atr, price = (
            float(row['open']),
            float(row['kc_upper']), float(row['kc_lower']),
            float(frame.iloc[-2]['atr']), float(price))
        if (not all(math.isfinite(v) and v > 0 for v in (opened, upper, lower, atr, price))
                or lower >= upper):
            return None
        threshold = atr * LIVE_BREAKOUT_BODY_ATR
        body = price - opened
        if threshold > 0 and body >= threshold:
            return 'LONG'
        if threshold > 0 and -body >= threshold:
            return 'SHORT'
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        pass
    return None


def anti_fakeout_breakout_ready(frame, price, level, side, atr_mult=0.25):
    """Reject ordinary rail breaks with excessive rejection wicks.

    This gate is for confirmed ordinary breakouts only; ATR special-K entries
    deliberately bypass it and use their dedicated body-size rule.
    """
    try:
        if side not in ("LONG", "SHORT") or frame is None or len(frame) < 2:
            return False
        row = frame.iloc[-1]
        opened = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        price = float(price)
        level = float(level)
        atr = float(frame.iloc[-2]["atr"])
        body = abs(price - opened)
        if not all(math.isfinite(v) and v > 0 for v in (opened, high, low, price, level, atr)):
            return False
        if body <= 0 or high < max(opened, price) or low > min(opened, price):
            return False
        if side == "LONG":
            breakout_valid = price > level + atr * atr_mult
            wick = high - max(price, opened)
        else:
            breakout_valid = price < level - atr * atr_mult
            wick = min(price, opened) - low
        return breakout_valid and wick <= body * 0.8
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False


FLAT_MIDDLE_REASON = "KC_FLAT_MIDDLE_WAIT"


def direction_efficiency(frame, lookback: int = 20) -> float:
    """最近 lookback 根已收線的「淨位移 ÷ 總路徑」；越高代表單向推進。"""
    try:
        if frame is None or len(frame) < lookback + 2:
            return 0.0
        closes = [float(v) for v in frame["close"].iloc[-(lookback + 1):-1]]
        if not all(math.isfinite(v) for v in closes):
            return 0.0
        moves = [b - a for a, b in zip(closes, closes[1:])]
        path = sum(abs(v) for v in moves)
        return abs(closes[-1] - closes[0]) / path if path > 0 else 0.0
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return 0.0


def long_body_side(frame, atr_mult: float):
    """長K特例：最後一根已收線順向實體只需達 ATR 門檻。"""
    if atr_mult <= 0:
        return None
    try:
        row = frame.iloc[-2]
        # The body and ATR must come from the same closed candle. Using the
        # preceding candle's ATR can understate the threshold after volatility
        # changes and falsely classify an ordinary candle as special K.
        atr = float(row["atr"])
        opened, close = float(row["open"]), float(row["close"])
        body = abs(close - opened)
        if atr <= 0 or body < atr * atr_mult:
            return None
        if close > opened:
            return "LONG"
        if close < opened:
            return "SHORT"
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return None
    return None


def trend_continuation(frame, price, side) -> bool:
    """趨勢延續：中軌方向順向，且最新價在持倉側外軌之外（不限斜率大小）。"""
    if side not in ("LONG", "SHORT"):
        return False
    try:
        key = "kc_middle" if "kc_middle" in frame.columns else "ema_20"
        previous, latest = float(frame[key].iloc[-3]), float(frame[key].iloc[-2])
        price = float(price)
        rail = float(frame["kc_upper"].iloc[-1] if side == "LONG" else frame["kc_lower"].iloc[-1])
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False
    if not all(math.isfinite(v) and v > 0 for v in (previous, latest, price, rail)):
        return False
    direction_ok = latest > previous if side == "LONG" else latest < previous
    outside = price > rail if side == "LONG" else price < rail
    return direction_ok and outside


def strong_trend_continuation(frame, price, side) -> bool:
    """強趨勢延續：已收線中軌位移 ÷ 軌寬 達門檻，且最新價在持倉側外軌之外。"""
    ratio = CHANNEL_STRONG_TREND_RATIO
    if ratio <= 0 or side not in ("LONG", "SHORT"):
        return False
    try:
        key = "kc_middle" if "kc_middle" in frame.columns else "ema_20"
        previous, latest = float(frame[key].iloc[-3]), float(frame[key].iloc[-2])
        width = float(frame["kc_upper"].iloc[-2]) - float(frame["kc_lower"].iloc[-2])
        price = float(price)
        rail = float(frame["kc_upper"].iloc[-1] if side == "LONG" else frame["kc_lower"].iloc[-1])
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False
    if not all(math.isfinite(v) and v > 0 for v in (previous, latest, width, price, rail)) or width <= 0:
        return False
    direction_ok = latest > previous if side == "LONG" else latest < previous
    outside = price > rail if side == "LONG" else price < rail
    return direction_ok and outside and abs(latest - previous) / width >= ratio


def channel_middle_is_flat(frame, ratio=None):
    """Parallel closed KC middle blocks new entries on both sides.

    Authorised 2026-09-11 (09-11 live window): entries whose closed-middle
    displacement was under 5% of the channel width lost 55.85 USDT over 15
    trades, while the rest made 31.06 USDT, and the same split held in both
    halves of the session. Width-relative so low-priced symbols are not blocked
    by a fixed percentage.
    """
    if ratio is None:
        ratio = CHANNEL_FLAT_MIDDLE_RATIO
    try:
        if ratio <= 0 or frame is None or len(frame) < 4:
            return False
        key = "kc_middle" if "kc_middle" in frame.columns else "ema_20"
        previous, latest = (float(v) for v in frame[key].iloc[-3:-1])
        upper = float(frame["kc_upper"].iloc[-2])
        lower = float(frame["kc_lower"].iloc[-2])
        if (not all(math.isfinite(v) and v > 0 for v in (previous, latest, upper, lower))
                or upper <= lower):
            return False
        return abs(latest - previous) / (upper - lower) < ratio
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False


def channel_tail_entry_blocked(frame, side) -> bool:
    """Block fresh entries once the CK middle has already run the same way too long.

    Authorised 2026-09-11: skipping entries taken after a long one-way run moved the
    live replay from +9.95 to +25.70 USDT and the worst trade from -11.34 to -9.17
    (8, 10, 12 and 14 bars were all better than no filter; 12 sits mid-plateau).
    """
    if side not in ("LONG", "SHORT") or CHANNEL_TAIL_MAX_TREND_BARS <= 0:
        return False
    # Fail closed: without enough warmup bars the run length cannot be measured,
    # so refuse the entry instead of letting a tail entry through.
    if frame is None or len(frame) < CHANNEL_TAIL_MAX_TREND_BARS + 2:
        return True
    key = "kc_middle" if "kc_middle" in frame.columns else "ema_20"
    try:
        closed = frame.iloc[:-1]
        values = [float(v) for v in closed[key].iloc[-(CHANNEL_TAIL_MAX_TREND_BARS + 1):]]
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return True
    if (len(values) < CHANNEL_TAIL_MAX_TREND_BARS + 1
            or not all(math.isfinite(v) and v > 0 for v in values)):
        return True
    sign = 1 if side == "LONG" else -1
    run = 0
    for previous, latest in zip(values, values[1:]):
        run = run + 1 if sign * (latest - previous) > 0 else 0
    return run >= CHANNEL_TAIL_MAX_TREND_BARS


def v_bottom_shape(frame, lookback=10):
    """最近區間是否形成 V 型谷底（低點在中段、兩側都有更高的收盤價）。

    使用者 2026-09-13：跌下來形成谷底後，再往上突破視為新突破，特例K也要等收線後
    有漲勢（兩根實體K）才能開。
    """
    try:
        if frame is None or len(frame) < lookback + 1:
            return False
        seg = frame.iloc[-(lookback + 1):-1]
        lows = [float(v) for v in seg["low"]]
        closes = [float(v) for v in seg["close"]]
        if not all(math.isfinite(v) for v in lows + closes):
            return False
        pos = lows.index(min(lows))
        if pos < 1 or pos > len(lows) - 2:
            return False
        low = lows[pos]
        return max(closes[:pos]) > low and max(closes[pos + 1:]) > low
    except (AttributeError, KeyError, IndexError, TypeError, ValueError):
        return False


def ma3_middle_cross_reset(frame, lookback=10):
    """MA3 走到中軌（穿越）後又回到順向側＝趨勢重置，之後突破視同新突破。

    使用者 2026-09-13：「MA3 走到中軌再回去也是視同新破軌」。
    """
    try:
        if frame is None or len(frame) < lookback + 1:
            return False


        seg = frame.iloc[-(lookback + 1):-1]
        mid = [float(v) for v in seg["kc_middle"]]
        ma3 = [float(v) for v in seg["ma3"]]
        if len(mid) < 3 or not all(math.isfinite(v) for v in mid + ma3):
            return False
        side = [1 if m > c else -1 if m < c else 0 for m, c in zip(ma3, mid)]
        changed = any(a != b for a, b in zip(side, side[1:]) if a != 0 and b != 0)
        return bool(changed and side[-1] != 0)
    except (AttributeError, KeyError, IndexError, TypeError, ValueError):
        return False


def ma3_pivot_reset(frame, side):
    """Return true when the last three closed MA3 values form a reversal pivot."""
    try:
        if side not in ("LONG", "SHORT") or frame is None or len(frame) < 4:
            return False
        values = [float(v) for v in frame["ma3"].iloc[-4:-1]]
        if not all(math.isfinite(v) and v > 0 for v in values):
            return False
        if side == "LONG":
            return values[1] < values[0] and values[1] < values[2]
        return values[1] > values[0] and values[1] > values[2]
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def aligned_entry(frame, price, require_second_body=True, special_k_exempt=True,
                  continuation_exempt=True):
    """Live long-body breaks may precede CK confirmation; retain trend entries.

    require_second_body：2026-09-13 使用者要求「一般漲勢破軌後第二根也要同色綠K才開」，
    只套用在全新第一筆；獲利重開與破軌延續由呼叫端帶 False。
    """
    wait = {"action": "WAIT", "side": None, "reason": "KC_DIRECTION_WAIT"}
    try:
        price = float(price)
        if not math.isfinite(price) or price <= 0 or frame is None or len(frame) < 4:
            return wait
        for _, row in frame.iloc[-4:].iterrows():
            opened, high, low, closed = (float(row[k]) for k in ("open", "high", "low", "close"))
            if (not all(math.isfinite(v) and v > 0 for v in (opened, high, low, closed))
                    or not low <= min(opened, closed) <= max(opened, closed) <= high):
                return wait
        ck_side = entry_trend_direction(frame)
        continuation_candidate = bool(
            continuation_exempt and ck_side in ("LONG", "SHORT")
            and outside_continuation_ready(frame, ck_side, price)
        )
        if CHANNEL_MIN_ATR_PCT > 0 and "atr" in frame.columns and not continuation_candidate:
            try:
                atr_now = float(frame.iloc[-2]["atr"])
                if math.isfinite(atr_now) and atr_now > 0 and price > 0:
                    if atr_now / price * 100 < CHANNEL_MIN_ATR_PCT:
                        return {**wait, "reason": "KC_LOW_VOLATILITY_WAIT"}
            except (KeyError, IndexError, TypeError, ValueError):
                pass
        breakout_side = live_body_breakout_side(frame, price) if CHANNEL_LIVE_BODY_BREAKOUT_ENABLED else None
        # A previously closed long candle cannot authorize a delayed special-K
        # entry. It may, however, establish a completed outside-rail leg for
        # the separate continuation route.
        body_side = None
        side = breakout_side or ck_side
        if side is None:
            return wait
        special_entry = bool(
            breakout_side == side
        )
        # 2026-09-13 使用者：MA3 已經轉彎（轉入軌內／前面已是峰頂）就先不要買；
        # 要等 MA3 確實往上、KC 也往上再做。以下兩關對所有入口（含特例長K）都適用。
        if not special_entry and ck_side is not None and ck_side != side:
            return {**wait, "reason": "KC_MIDDLE_OPPOSITE_WAIT"}
        try:
            live_ma3 = float(frame.iloc[-1]["ma3"])
            closed_ma3 = float(frame.iloc[-2]["ma3"])
        except (KeyError, IndexError, TypeError, ValueError):
            live_ma3 = math.nan
            closed_ma3 = math.nan
        if not special_entry and math.isfinite(live_ma3) and math.isfinite(closed_ma3):
            valid_trend = ck_side == side
            strong_side = (
                (side == "LONG" and price > float(frame.iloc[-1]["kc_upper"]))
                or (side == "SHORT" and price < float(frame.iloc[-1]["kc_lower"]))
            )
            if (side == "LONG" and not live_ma3 > closed_ma3 and not (valid_trend and strong_side)) or (
                side == "SHORT" and not live_ma3 < closed_ma3 and not (valid_trend and strong_side)
            ):
                return {**wait, "reason": "KC_MA3_TURN_WAIT"}
        # 僅有中軌方向不代表已完成「破軌根＋第二根確認根」；不得在這裡早退開倉。
        # 由長實體驅動的進場（即時破軌／長K特例）不吃「走平禁開」與「實體過熱」：
        # 這兩個過濾是為了避免在沒有趨勢時追價，但長K本身就是訊號，否則會互相矛盾、
        # 讓長K入口永遠不會觸發（實體 ≥1 ATR 一定大於 0.8 ATR 的過熱門檻）。
        body_driven = bool(breakout_side)
        special_long_body = False
        # 2026-09-12 使用者：「沒有兩根實體K也開倉，以上都不能開倉」——
        # 特例K不再有特權：把 body_driven 關掉，讓它走一般單的完整關卡
        #（破軌根＋同色實體確認根、實體過熱、方向效率、末端禁開、動能衰退、
        # MA3 安全距離…）。要回到舊行為把 CHANNEL_SPECIAL_K_REQUIRES_CONFIRMATION
        # 設成 false 即可。
        if CHANNEL_SPECIAL_K_REQUIRES_CONFIRMATION and not special_long_body and not breakout_side:
            body_driven = False
        # Special K still uses its dedicated volume check, but it cannot bypass
        # the closed-candle confirmation required by the current entry policy.
        if body_driven and not special_entry and not special_volume_surge_ok(frame, -1):
            return {**wait, "reason": "KC_SPECIAL_LOW_VOLUME_WAIT"}
        # 2026-09-14 使用者：延續（連續三根已收線在軌外、平倉後重開）不受效率門檻限制，
        # 單根同色K即可延續開倉。
        # A completed outside-rail leg may continue on one same-colour live
        # candle. The explicit flag keeps a first breakout from bypassing its
        # closed confirmation; reentry callers enable this route deliberately.
        continuation_ready = bool(
            continuation_exempt and outside_continuation_ready(frame, side, price)
        )
        special_entry = bool(breakout_side)
        # A qualifying special K is allowed to enter on the current bar. All
        # ordinary entries must wait for the closed breakout/confirmation pair.
        if not special_entry and not continuation_ready and not breakout_two_bodies_ready(frame, side):
            print(
                f"[Audit Funnel] {side} KC_SECOND_BODY_WAIT: "
                "第一根破軌已記錄，但第二根同色實體尚未收線確認"
            )
            return {**wait, "reason": "KC_SECOND_BODY_WAIT"}
        if side is None:
            return wait
        if not special_entry and channel_tail_entry_blocked(frame, side):
            return {**wait, "reason": "KC_TREND_TAIL_WAIT"}
        if (CHANNEL_MIN_DIRECTION_EFFICIENCY > 0 and not body_driven and not continuation_ready
                and direction_efficiency(frame) < CHANNEL_MIN_DIRECTION_EFFICIENCY):
            return {**wait, "reason": "KC_LOW_EFFICIENCY_WAIT"}
        if not body_driven and channel_middle_is_flat(frame) and ck_side != side:
            return {**wait, "reason": FLAT_MIDDLE_REASON}
            
        upper = float(frame.iloc[-1]['kc_upper'])
        lower = float(frame.iloc[-1]['kc_lower'])
        
        # Strict momentum safety check
        open_price = float(frame.iloc[-1]['open'])
        body = abs(price - open_price)
        atr = float(frame.iloc[-2]['atr']) if 'atr' in frame.columns else 0.0
        
        if side == 'LONG' and not special_entry:
            if price < open_price and atr > 0 and body > atr * 0.5:
                return {**wait, "reason": "MOMENTUM_BLOCK_MASSIVE_RED"}
            if price <= upper:
                return {**wait, "reason": "KC_INSIDE_CHANNEL_WAIT"}
        if side == 'SHORT' and not special_entry:
            if price > open_price and atr > 0 and body > atr * 0.5:
                return {**wait, "reason": "MOMENTUM_BLOCK_MASSIVE_GREEN"}
            if price >= lower:
                return {**wait, "reason": "KC_INSIDE_CHANNEL_WAIT"}

        # 進場當根順向實體過熱就別追：這種大K之後最容易接反向異常K。
        # 但長K／即時破軌入口本身就是靠大實體成立，不套用此過濾。
        if not special_entry and not body_driven and atr > 0 and body > atr * CHANNEL_ENTRY_MAX_BODY_ATR:
            return {**wait, "reason": "KC_ENTRY_BODY_OVERHEAT_WAIT"}

        if not body_driven:
            try:
                live_ma3 = float(frame.iloc[-1]['ma3'])
                live_ma15 = float(frame.iloc[-1]['ma15'])
                if side == 'LONG' and (price < live_ma3 or price < live_ma15):
                    return {**wait, "reason": "KC_MA_SAFETY_WAIT"}
                if side == 'SHORT' and (price > live_ma3 or price > live_ma15):
                    return {**wait, "reason": "KC_MA_SAFETY_WAIT"}
            except (KeyError, ValueError, TypeError, IndexError):
                pass

            if ck_side is not None and ck_side == side:
                # trend-validated entries are permitted even when the latest live candle is
                # still red/green against the current bar; the flat trend direction remains valid.
                pass
            elif not live_candle_color_ready(frame, price, side):
                return {**wait, "reason": "KC_LIVE_COLOR_WAIT"}

            # 純趨勢進場（當根不是長K破軌）：前一根已是大K就不追。
            # 例外：強趨勢延續（中軌位移÷軌寬 ≥ 門檻且價格在持倉側軌外）允許續追，
            # 否則一波強漲/強跌中的每根大K都會把延續訊號全部擋掉（2026-09-13 使用者反映）。
            if atr > 0 and not (strong_trend_continuation(frame, price, side)
                                or trend_continuation(frame, price, side)):
                prev_body = abs(float(frame.iloc[-2]["close"]) - float(frame.iloc[-2]["open"]))
                if prev_body > atr * CHANNEL_ENTRY_MAX_PREV_BODY_ATR:
                    return {**wait, "reason": "KC_ENTRY_PREV_BODY_WAIT"}

        if not special_entry and ck_momentum_fading(frame, side):
            return {**wait, "reason": "KC_MOMENTUM_FADING_WAIT"}
        if not special_entry and ck_side != side and not live_adverse_entry_safe(frame, price, side):
            return {**wait, "reason": "KC_LIVE_ADVERSE_ENTRY_WAIT"}
        if special_entry:
            reason = 'KC_LIVE_BODY_BREAKOUT_' + side
        elif continuation_ready:
            reason = "KC_OUTSIDE_CONTINUATION_" + side
        elif ck_side == side:
            if side == "SHORT" and not special_entry:
                return {**wait, "reason": "KC_MIDDLE_TREND_SHORT_DISABLED"}
            return {**wait, "reason": "KC_MIDDLE_TREND_DISABLED"}
        else:
            return {**wait, "reason": "KC_TREND_ENTRY_DISABLED"}
        return {"action": "ENTER", "side": side, "reason": reason}
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return wait


def aligned_entry_ready(frame, price, side, require_second_body=True):
    return side in ('LONG', 'SHORT') and aligned_entry(
        frame, price, require_second_body=require_second_body).get('side') == side


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


def _outside_continuation(frame, side):
    """前一根已收線也收在持倉側外軌之外 → 屬延續，單根同色K即可開倉。"""
    try:
        rail = "kc_upper" if side == "LONG" else "kc_lower"
        if rail not in frame.columns:
            return False
        prev = frame.iloc[-2]
        close = float(prev["close"])
        limit = float(prev[rail])
        if not (math.isfinite(close) and math.isfinite(limit) and close > 0 and limit > 0):
            return False
        return close > limit if side == "LONG" else close < limit
    except (AttributeError, KeyError, IndexError, TypeError, ValueError):
        return False


def outside_continuation_ready(frame, side, price=None):
    """延續開倉：最近兩根已收線都收在持倉側外軌之外 → 單根同色K即可開倉。

    使用者 2026-09-14：「後續K線都站在外軌外時，那些綠K可1根延續開；空單也要做。」
    （僅適用於價格已連續站在軌外的延續段，不是首次破軌。）
    """
    try:
        if side not in ("LONG", "SHORT") or frame is None or len(frame) < 4:
            return False
        if ma3_pivot_reset(frame, side) or ma3_middle_cross_reset(frame, lookback=4):
            return False
        try:
            previous_ma3 = float(frame.iloc[-2]["ma3"])
            live_ma3 = float(frame.iloc[-1]["ma3"])
            if not all(math.isfinite(v) and v > 0 for v in (previous_ma3, live_ma3)):
                return False
            if side == "LONG" and live_ma3 <= previous_ma3:
                return False
            if side == "SHORT" and live_ma3 >= previous_ma3:
                return False
        except (AttributeError, KeyError, TypeError, ValueError, IndexError):
            return False
        rail = "kc_upper" if side == "LONG" else "kc_lower"
        if rail not in frame.columns:
            return False
        # 2026-09-14 使用者：破軌那根不算延續——「破軌後第二根」必須是同一色實體K，
        # 破軌才算完成；完成之後（前兩根已收線都在軌外）才可用單根同色K延續進場。
        # 2026-09-14 使用者：平倉後不用等兩根——前一根已收線站在外軌外且是同色實體K即可，
        # 但「當根」也必須同色（避免在反向K上進場，例如破軌後第二根是紅K的情況）。
        row = frame.iloc[-2]
        live = frame.iloc[-1]
        live_open = float(live["open"])
        live_price = float(price) if price is not None else float(live["close"])
        if not (math.isfinite(live_open) and live_open > 0 and math.isfinite(live_price)):
            return False
        opened = float(row["open"])
        close = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])
        limit = float(row[rail])
        if not all(math.isfinite(v) and v > 0 for v in (opened, close, high, low, limit)):
            return False
        span = high - low
        if span <= 0:
            return False
        sign = 1 if side == "LONG" else -1
        # 2026-09-14 使用者：「只要在外軌，不要看前一根是什麼」→ 前一根只要是「收盤在軌外」
        # 即可，不再檢查它的顏色或實體大小；當根必須同色（下方檢查）。
        if sign * (live_price - live_open) <= 0:
            return False
        return close > limit if side == "LONG" else close < limit
    except (AttributeError, KeyError, IndexError, TypeError, ValueError):
        return False


def breakout_two_bodies_ready(frame, side, lookback=4):
    """破軌確認：有效破軌根＋同色弱體順延＋第一根有效確認K。

    2026-09-14 使用者：「若第二根是同色弱實體，第三根是同色實體，後面就可以開倉，以此類推。」
    → 破軌根之後若出現同色弱實體（實體 < 全長 20%），不取消資格，繼續往後等第一根同色實體K。
    """
    try:
        if side not in ("LONG", "SHORT") or frame is None or len(frame) < 4:
            return False
        rail = "kc_upper" if side == "LONG" else "kc_lower"
        if rail not in frame.columns:
            return False
        sign = 1 if side == "LONG" else -1
        rows = list(frame.iloc[-1 - lookback:-1].iterrows())
        if not rows:
            return False
        # 最後一根已收線必須是同色實體K（確認根）
        _, last = rows[-1]
        l_open, l_high, l_low, l_close = (float(last[k]) for k in ("open", "high", "low", "close"))
        if not all(math.isfinite(v) and v > 0 for v in (l_open, l_high, l_low, l_close)):
            return False
        span = l_high - l_low
        if span <= 0 or sign * (l_close - l_open) <= 0 or abs(l_close - l_open) / span < 0.20:
            return False
        last_limit = float(last[rail])
        if not math.isfinite(last_limit) or not (
            l_close > last_limit if side == "LONG" else l_close < last_limit
        ):
            return False
        # 找最早的有效破軌根；其後同色弱體可以順延，即使暫時收回軌內。
        # 只有反色K會使這段破軌序列失效。
        root_found = False
        for _, row in rows[:-1]:
            opened, high, low, closed = (float(row[k]) for k in ("open", "high", "low", "close"))
            limit = float(row[rail])
            if not all(math.isfinite(v) and v > 0 for v in (opened, high, low, closed, limit)):
                return False
            if not low <= min(opened, closed) <= max(opened, closed) <= high:
                return False
            if sign * (closed - opened) <= 0:
                return False
            body_ratio = abs(closed - opened) / (high - low) if high > low else 0.0
            outside = closed > limit if side == "LONG" else closed < limit
            if not root_found:
                if outside and body_ratio >= 0.20:
                    root_found = True
                continue
        return root_found
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def breakout_confirmation_pending(frame, side, lookback=4):
    """Detect a valid closed breakout root still waiting for confirmation."""
    try:
        if side not in ("LONG", "SHORT") or frame is None or len(frame) < 4:
            return False
        rail = "kc_upper" if side == "LONG" else "kc_lower"
        sign = 1 if side == "LONG" else -1
        rows = list(frame.iloc[-1 - lookback:-1].iterrows())
        root_found = False
        for _, row in rows:
            opened, high, low, closed, limit = [float(row[k]) for k in ("open", "high", "low", "close", rail)]
            if not all(math.isfinite(v) and v > 0 for v in (opened, high, low, closed, limit)) or high <= low:
                return False
            same_color = sign * (closed - opened) > 0
            if not same_color:
                if root_found:
                    return False
                continue
            outside = closed > limit if side == "LONG" else closed < limit
            body_ratio = abs(closed - opened) / (high - low)
            if not root_found:
                if outside and body_ratio >= 0.20:
                    root_found = True
                continue
            if outside and body_ratio >= 0.20:
                return False
        return root_found
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def two_closed_bodies_ready(frame, side):
    """Require two completed directional bodies; the live candle never counts."""
    try:
        if side not in ("LONG", "SHORT") or frame is None or len(frame) < 3:
            return False
        sign = 1 if side == "LONG" else -1
        for _, row in frame.iloc[-3:-1].iterrows():
            opened, high, low, closed = (float(row[k]) for k in ("open", "high", "low", "close"))
            if not all(math.isfinite(v) and v > 0 for v in (opened, high, low, closed)):
                return False
            if not low <= min(opened, closed) <= max(opened, closed) <= high or high <= low:
                return False
            if sign * (closed - opened) <= 0 or abs(closed - opened) / (high - low) < .20:
                return False
        return True
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def three_closed_short_breakout_ready(frame, price):
    """Allow a small red middle candle between solid closed breakout/confirmation."""
    try:
        if frame is None or len(frame) < 4:
            return False
        rows = [frame.iloc[i] for i in (-4, -3, -2)]
        for index, row in enumerate(rows):
            opened, high, low, closed = (float(row[k]) for k in ("open", "high", "low", "close"))
            lower, upper = float(row["kc_lower"]), float(row["kc_upper"])
            if not all(math.isfinite(v) and v > 0 for v in (opened, high, low, closed, lower, upper)):
                return False
            if not (low <= closed < opened <= high and lower < upper and closed < lower):
                return False
            if index != 1 and (opened - closed) / (high - low) < .20:
                return False
        first = rows[0]
        live = frame.iloc[-1]
        lower, upper, price = float(live["kc_lower"]), float(live["kc_upper"]), float(price)
        return (float(first["open"]) >= float(first["kc_lower"])
                and all(math.isfinite(v) for v in (lower, upper, price))
                and 0 < price < lower < upper)
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def confirmed_outer_continuation_ready(frame, price, side):
    """A missed breakout may continue; both closed bodies must finish outside."""
    if not two_closed_bodies_ready(frame, side):
        return False
    try:
        sign = 1 if side == "LONG" else -1
        rail = "kc_upper" if side == "LONG" else "kc_lower"
        price = float(price)
        if not math.isfinite(price) or price <= 0:
            return False
        for offset in (-3, -2, -1):
            row = frame.iloc[offset]
            lower, upper = float(row["kc_lower"]), float(row["kc_upper"])
            if not all(math.isfinite(v) for v in (lower, upper)) or not 0 < lower < upper:
                return False
            quoted = price if offset == -1 else float(row["close"])
            if sign * (quoted - float(row[rail])) <= 0:
                return False
        return True
    except (AttributeError, TypeError, ValueError, KeyError, IndexError):
        return False


def confirmed_outer_breakout_ready(frame, price, side, allow_three_short=False):
    """Require a closed breakout/confirmation, with the three-red short option."""
    if allow_three_short and side == "SHORT" and three_closed_short_breakout_ready(frame, price):
        return True
    if not two_closed_bodies_ready(frame, side):
        return False
    try:
        sign = 1 if side == "LONG" else -1
        rail = "kc_upper" if side == "LONG" else "kc_lower"
        breakout, confirmation, live = (frame.iloc[i] for i in (-3, -2, -1))
        for row in (breakout, confirmation, live):
            lower, upper = float(row["kc_lower"]), float(row["kc_upper"])
            if not all(math.isfinite(v) for v in (lower, upper)) or not 0 < lower < upper:
                return False
        price = float(price)
        return (math.isfinite(price) and price > 0
                and sign * (float(breakout["open"]) - float(breakout[rail])) <= 0
                and sign * (float(breakout["close"]) - float(breakout[rail])) > 0
                and sign * (float(confirmation["close"]) - float(confirmation[rail])) > 0
                and sign * (price - float(live[rail])) > 0)
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


def outside_reentry(frame, price, side, require_second_body=True, continuation_exempt=False):
    """Use the same confirmed CK trend for normal reentries.

    Current policy permits only a fresh confirmed breakout or current live ATR
    special K; prior outside candles do not authorize a reentry.
    """
    decision = aligned_entry(
        frame, price, require_second_body=require_second_body,
        continuation_exempt=continuation_exempt,
    )
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
        decision = aligned_entry(frame, price)
        if decision.get("action") == "ENTER" and decision.get("side") == side:
            return True, decision.get("reason", "OK"), decision
        return False, decision.get("reason", "WAIT"), decision
