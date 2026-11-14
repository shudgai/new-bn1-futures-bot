"""Pre-close-API absolute veto for meme (1000PEPE / 龍蝦) positions.

Any automated close request on a meme symbol must pass this gate; ``HOLD`` means
the request is discarded and no buy-back/sell-back order may be sent.
LONG mirrors SHORT exactly.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Dict, Mapping, NamedTuple, Optional, Tuple

import pandas as pd

from core.services.candle_data import closed_entry_candles

logger = logging.getLogger("UltimateShortExitGatekeeper")

MIN_BARS_HELD = 5
STRUCTURE_BUFFER_ATR = 0.50
DISASTER_LOSS_ATR = 3.0
# Owner decision 2026-11-03: while the quote stays within this many ATR of the
# entry price (either side), structure/waterfall/rail exits hold the position.
# Profitable confirmed pivots keep their own structure-locked authority.
COST_SHIELD_ATR = 1.0
COST_SHIELD_EXEMPT_TOKENS = ("HARD_S", "CIRCUIT_BREAKER", "STOP_LOSS", "DISASTER_LOSS")
ACCOUNT_LEVEL_TOKENS = (
    "LIQUIDATION", "DATA_OUTAGE", "ACCOUNT_CIRCUIT_BREAKER", "ACCOUNT_HARD_STOP",
    "帳戶硬止損", "MARGIN_LOSS",
)
STRUCTURE_EXIT_REASONS = {
    "SHORT": "EXIT_SHORT_STRUCTURE_BREAK_050ATR",
    "LONG": "EXIT_LONG_STRUCTURE_BREAK_050ATR",
}
TREND_REVERSAL_EXIT_REASONS = {
    "SHORT": "EXIT_SHORT_TREND_REVERSAL",
    "LONG": "EXIT_LONG_TREND_REVERSAL",
}
KC_MIDDLE_MA15_BREAK_REASONS = {
    "SHORT": "EXIT_SHORT_KC_MIDDLE_MA15_BREAK",
    "LONG": "EXIT_LONG_KC_MIDDLE_MA15_BREAK",
}
DISASTER_LOSS_REASONS = {
    "SHORT": "EXIT_SHORT_DISASTER_LOSS_3ATR",
    "LONG": "EXIT_LONG_DISASTER_LOSS_3ATR",
}
DISASTER_RAIL_REASONS = {
    "SHORT": "EXIT_SHORT_DISASTER_CLOSED_ABOVE_KC_UPPER",
    "LONG": "EXIT_LONG_DISASTER_CLOSED_BELOW_KC_LOWER",
}
# Owner decision 2026-11-03: inside the cost shield, a freshly completed bar whose
# KC middle turned against the position and closed across that middle releases the shield.
KC_FLIP_TURN_BARS = 3
KC_FLIP_EXIT_REASONS = {
    "SHORT": "EXIT_SHORT_KC_FLIP_CLOSED_ABOVE_MIDDLE",
    "LONG": "EXIT_LONG_KC_FLIP_CLOSED_BELOW_MIDDLE",
}
TRUE_OUTER_PIVOT_EXIT_REASONS = {
    "SHORT": "EXIT_SHORT_TRUE_VALLEY_BOTTOM",
    "LONG": "EXIT_LONG_TRUE_PEAK_TOP",
}
SHARP_REVERSAL_EXIT_REASONS = {
    "LONG": "EXIT_LONG_SHARP_REVERSAL_15ATR",
    "SHORT": "EXIT_SHORT_SHARP_REVERSAL_15ATR",
}
SHARP_REVERSAL_BODY_ATR = 1.5
MA5_ANGLE_REVERSAL_DEGREES = 75.0
MA5_ANGLE_SCALE = 1000.0
MA5_ANGLE_REVERSAL_EXIT_REASONS = {
    "LONG": "EXIT_LONG_MA5_75_DEGREE_REVERSAL",
    "SHORT": "EXIT_SHORT_MA5_75_DEGREE_REVERSAL",
}
KC_MIDDLE_SLOPE_EXIT_REASONS = {
    "LONG": "EXIT_LONG_KC_MIDDLE_SLOPE_DOWN",
    "SHORT": "EXIT_SHORT_KC_MIDDLE_SLOPE_UP",
}
FAST_TOP_EXIT_REASONS = {
    "SHORT": "EXIT_SHORT_FAST_BOTTOM_BREAK_MA15_MID",
    "LONG": "EXIT_LONG_FAST_TOP_BREAK_MA15_MID",
}
PROFIT_LOCK_GATE_REASONS = {
    "SHORT": "EXIT_BY_STAGED_PROFIT_LOCK_TIER1",
    "LONG": "EXIT_BY_STAGED_PROFIT_LOCK_TIER1",
}
PROFIT_LOCK_TOKENS = (
    "STAGED_PROFIT_LOCK", "PROFIT_LOCK", "RATCHET_PROFIT_LOCK",
    "TRAILING_PROFIT_LOCK", "EXIT_BY_SCHEME1_PROFIT_LOCK",
    "STAGE1_LADDER_STOP", "STAGE2_LADDER_STOP", "STAGE3_LADDER_STOP", "STAGE4_LADDER_STOP",
    "TRAILING_STOP", "BREAKEVEN_PROTECTION", "LADDER_STOP",
)

BOTTOM_REVERSAL_EXIT_REASONS = {
    "SHORT": "EXIT_SHORT_BOTTOM_GREEN_CANDLE_MA5",
    "LONG": "EXIT_LONG_TOP_RED_CANDLE_MA5",
}
EMERGENCY_CIRCUIT_BREAKER_REASONS = {
    "SHORT": "EXIT_SHORT_EMERGENCY_SURGE",
    "LONG": "EXIT_LONG_EMERGENCY_WATERFALL",
}

ALLOW, HOLD, DEFER = "ALLOW", "HOLD", "DEFER"


class ExitVerdict(NamedTuple):
    verdict: str            # ALLOW / HOLD / DEFER (DEFER = not governed, use existing chain)
    code: str
    evidence: dict

    @property
    def allowed(self) -> bool:
        return self.verdict == ALLOW


def _float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _normalized_exit_reason(reason: Any) -> str:
    normalized = str(reason or "").strip().upper()
    prefixes = ("EXITGATE:", "CHANNEL_SWING ")
    changed = True
    while changed:
        changed = False
        for prefix in prefixes:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):].strip()
                changed = True
    return normalized


def _minute(value: float) -> int:
    seconds = value / 1000.0 if value >= 1e11 else value
    return int(seconds // 60)


def _ma5_angle_signal(frame: Any, quote: float, current_time: float) -> Optional[dict]:
    if not isinstance(frame, pd.DataFrame) or len(frame) < 2:
        return None
    current, previous = frame.iloc[-1], frame.iloc[-2]
    if current.get("is_closed") is not False and not (
        type(current.get("is_closed")).__name__ == "bool_" and not bool(current.get("is_closed"))
    ):
        return None
    try:
        closed = closed_entry_candles(frame)
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError, IndexError):
        return None
    if len(closed) < 1:
        return None

    ma5_current = _float(current.get("ma5"))
    ma5_previous = _float(previous.get("ma5"))
    current_close = _float(current.get("close"))
    previous_close = _float(previous.get("close"))
    timestamp = _float(current.get("timestamp"))
    quote_value = _float(quote)
    timeframe_ms = _float(getattr(frame, "attrs", {}).get("timeframe_ms"))
    snapshot_ms = _float(getattr(frame, "attrs", {}).get("snapshot_ms"))
    if any(value is None or value <= 0 for value in (
        ma5_current, ma5_previous, current_close, previous_close,
        timestamp, quote_value, timeframe_ms, snapshot_ms,
    )):
        return None
    if timeframe_ms not in (60_000.0, 60.0):
        return None
    interval_seconds = timeframe_ms / 1000.0 if timeframe_ms >= 1000 else timeframe_ms
    timestamp_seconds = timestamp / 1000.0 if timestamp >= 1e11 else timestamp
    snapshot_seconds = snapshot_ms / 1000.0 if snapshot_ms >= 1e11 else snapshot_ms
    previous_timestamp = _float(previous.get("timestamp"))
    if previous_timestamp is None:
        return None
    timestamp_delta = timestamp - previous_timestamp
    if timestamp_delta not in (timeframe_ms, interval_seconds):
        return None
    if (
        int(timestamp_seconds // interval_seconds) != int(current_time // interval_seconds)
        or snapshot_seconds < timestamp_seconds
        or snapshot_seconds >= timestamp_seconds + interval_seconds
        or snapshot_seconds > current_time + 2.0
    ):
        return None

    live_ma5 = ma5_current + (quote_value - current_close) / 5.0
    if not math.isfinite(live_ma5) or live_ma5 <= 0:
        return None
    angle = math.degrees(math.atan(
        (live_ma5 - ma5_previous) / previous_close * MA5_ANGLE_SCALE,
    ))
    if angle >= MA5_ANGLE_REVERSAL_DEGREES:
        side = "LONG"
    elif angle <= -MA5_ANGLE_REVERSAL_DEGREES:
        side = "SHORT"
    else:
        return None
    if side:
        return {
            "side": side,
            "angle": angle,
            "timestamp": timestamp,
            "ma5": live_ma5,
            "previous_ma5": ma5_previous,
            "candle_timestamp_ms": int(timestamp_seconds * 1000),
        }


def _sharp_reversal_signal(side: str, closed: pd.DataFrame) -> Optional[dict]:
    if side not in ("LONG", "SHORT") or closed is None or len(closed) < 2:
        return None
    current, previous = closed.iloc[-1], closed.iloc[-2]
    required = (
        "timestamp", "open", "high", "low", "close", "ma5", "ma15", "kc_middle",
    )
    values = {key: _float(current.get(key)) for key in required}
    previous_values = {
        key: _float(previous.get(key))
        for key in ("timestamp", "atr", "ma5", "ma15", "kc_middle")
    }
    if any(value is None or value <= 0 for value in values.values()):
        return None
    if any(value is None or value <= 0 for value in previous_values.values()):
        return None

    opened, high, low, close = (
        values["open"], values["high"], values["low"], values["close"],
    )
    if high < max(opened, close, low) or low > min(opened, close, high) or high <= low:
        return None
    body = (opened - close) if side == "LONG" else (close - opened)
    threshold = SHARP_REVERSAL_BODY_ATR * previous_values["atr"]
    if body <= 0 or (body < threshold and not math.isclose(
        body, threshold, rel_tol=1e-12, abs_tol=0,
    )):
        return None

    ma5, ma15, middle = values["ma5"], values["ma15"], values["kc_middle"]
    ma5_prev, ma15_prev = previous_values["ma5"], previous_values["ma15"]
    middle_prev = previous_values["kc_middle"]
    if side == "LONG":
        confirmed = (
            close < ma5
            and ma5 < ma5_prev
            and close < ma15
            and ma5 < ma15
            and close < middle
            and middle <= middle_prev
        )
    else:
        confirmed = (
            close > ma5
            and ma5 > ma5_prev
            and close > ma15
            and ma5 > ma15
            and close > middle
            and middle >= middle_prev
        )
    if not confirmed:
        return None
    return {
        "side": side,
        "body": body,
        "atr": previous_values["atr"],
        "timestamp": values["timestamp"],
        "close": close,
    }


OWN_STRUCTURE_REASONS = frozenset(
    {"STRUCTURE_EXIT_PROBE", *(r.upper() for r in STRUCTURE_EXIT_REASONS.values()),
     *(r.upper() for r in KC_MIDDLE_MA15_BREAK_REASONS.values()),
     *(r.upper() for r in KC_FLIP_EXIT_REASONS.values()),
     *(r.upper() for r in TRUE_OUTER_PIVOT_EXIT_REASONS.values()),
     *(r.upper() for r in FAST_TOP_EXIT_REASONS.values()),
     *(r.upper() for r in BOTTOM_REVERSAL_EXIT_REASONS.values()),
     *(r.upper() for r in TREND_REVERSAL_EXIT_REASONS.values()),
     *(r.upper() for r in SHARP_REVERSAL_EXIT_REASONS.values()),
     *(r.upper() for r in MA5_ANGLE_REVERSAL_EXIT_REASONS.values()),
     *(r.upper() for r in KC_MIDDLE_SLOPE_EXIT_REASONS.values()),
     "EXIT_BY_TRUE_PEAK_EXTREME_REVERSAL",
     "EXIT_BY_TRUE_VALLEY_EXTREME_REVERSAL",
     "EXIT_LONG_CONSECUTIVE_BEAR_CANDLES_PIVOT_TURN",
     "EXIT_SHORT_CONSECUTIVE_BULL_CANDLES_PIVOT_TURN",
     "EXIT_BY_STAGED_PROFIT_LOCK_TIER1",
     "EXIT_BY_STAGED_PROFIT_LOCK_TIER2",
     "EXIT_BY_SCHEME1_PROFIT_LOCK_MA3_MA15",
     "PROFIT_LOCK"}
)
STRICT_TRUE_PIVOT_EXIT_CANDIDATES = {
    "LONG": frozenset({
        "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT",
        "EXIT_LONG_CONSECUTIVE_BEAR_CANDLES_PIVOT_TURN",
        TRUE_OUTER_PIVOT_EXIT_REASONS["LONG"],
    }),
    "SHORT": frozenset({
        "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT",
        "EXIT_SHORT_CONSECUTIVE_BULL_CANDLES_PIVOT_TURN",
        TRUE_OUTER_PIVOT_EXIT_REASONS["SHORT"],
    }),
}
WATERFALL_EXIT_TOKENS = (
    "EXIT_BY_EXTREME_WATERFALL", "WATERFALL_DROP", "LIVE_ADVERSE_WATERFALL",
)
OWN_DISASTER_REASONS = frozenset(
    r.upper() for r in (
        *DISASTER_LOSS_REASONS.values(),
        *DISASTER_RAIL_REASONS.values(),
        *EMERGENCY_CIRCUIT_BREAKER_REASONS.values(),
    )
)
ORDINARY_PULLBACK_EXIT_TOKENS = ("PULLBACK", "RETRACE", "RETRACEMENT")


def _kc_flip_turn_candidate(symbol: str, side: str, closed: Any, current_time: float) -> bool:
    """Track the preconditions that a valid KC turn is underway without requiring full release."""
    if closed is None or len(closed) < KC_FLIP_TURN_BARS + 1:
        return False
    current = closed.iloc[-1]
    previous = closed.iloc[-2]
    stamp = _float(current.get("timestamp"))
    atr = _float(previous.get("atr"))
    middle = _float(current.get("kc_middle"))
    if stamp is None or atr is None or middle is None or atr <= 0:
        return False
    bar_minute = _minute(stamp)
    if bar_minute != int(current_time // 60) - 1:
        return False
    mids = [_float(closed.iloc[i].get("kc_middle")) for i in range(-KC_FLIP_TURN_BARS - 1, 0)]
    if any(v is None for v in mids) or len(mids) < KC_FLIP_TURN_BARS:
        return False
    sign = 1 if side == "LONG" else -1
    return all(-sign * (later - earlier) > 0 for earlier, later in zip(mids, mids[1:]))


def _kc_flip_release(
    symbol: str, side: str, closed: Any, opened: float, is_bar_closed: Any,
    current_time: float,
) -> bool:
    """Confirmed turn on a fresh completed bar (Owner 2026-11-03, pullbacks keep holding):
    KC middle moved against the side on each of the last KC_FLIP_TURN_BARS completed bars,
    and the normal 0.50 ATR / fresh MA5-MA15 / opposite-body exit lock also passes."""
    if is_bar_closed is False or closed is None or len(closed) < KC_FLIP_TURN_BARS + 1:
        return False
    current = closed.iloc[-1]
    previous = closed.iloc[-2]
    stamp = _float(current.get("timestamp"))
    opened_bar = _float(current.get("open"))
    close = _float(current.get("close"))
    ma5, ma15 = _float(current.get("ma5")), _float(current.get("ma15"))
    ma5_prev, ma15_prev = _float(previous.get("ma5")), _float(previous.get("ma15"))
    atr = _float(previous.get("atr"))
    mids = [_float(closed.iloc[i].get("kc_middle")) for i in range(-KC_FLIP_TURN_BARS - 1, 0)]
    middle = _float(current.get("kc_middle"))
    values = [stamp, opened_bar, close, ma5, ma15, ma5_prev, ma15_prev, atr, middle, *mids]
    if any(v is None for v in values) or min(values) <= 0:
        return False
    bar_minute = _minute(stamp)
    if bar_minute != int(current_time // 60) - 1 or bar_minute - _minute(opened) < MIN_BARS_HELD:
        return False
    from core.gates.master_gate_funnel import meme_distance_atr

    sign = 1 if side == "LONG" else -1
    buffer_line = middle - sign * STRUCTURE_BUFFER_ATR * meme_distance_atr(symbol, atr)
    return (
        all(-sign * (later - earlier) > 0 for earlier, later in zip(mids, mids[1:]))
        and -sign * (close - buffer_line) > 0
        and -sign * (close - opened_bar) > 0
        and -sign * (ma5_prev - ma15_prev) <= 0
        and -sign * (ma5 - ma15) > 0
        and -sign * (ma5 - ma5_prev) > 0
    )


def _outer_rail_true_pivot_release(
    symbol: str, side: str, closed: Any, opened: float, entry: float, quote: float,
    is_bar_closed: Any, current_time: float,
) -> bool:
    """Confirm a 3-bar true valley/peak bottom (真谷底/真峰頂) in profit or reversal."""
    if is_bar_closed is False or closed is None or len(closed) < 3:
        return False
    b1, b2, b3 = closed.iloc[-3], closed.iloc[-2], closed.iloc[-1]
    stamp1 = _float(b1.get("timestamp"))
    stamp3 = _float(b3.get("timestamp"))
    if stamp1 is None or stamp3 is None:
        return False
    bar_minute = _minute(stamp3)
    opened_minute = _minute(opened)
    if bar_minute != int(current_time // 60) - 1:
        return False
    o1, h1, l1, c1 = (_float(b1.get(k)) for k in ("open", "high", "low", "close"))
    o2, h2, l2, c2 = (_float(b2.get(k)) for k in ("open", "high", "low", "close"))
    o3, h3, l3, c3 = (_float(b3.get(k)) for k in ("open", "high", "low", "close"))
    kc_u1, kc_l1 = _float(b1.get("kc_upper")), _float(b1.get("kc_lower"))
    kc_u2, kc_l2 = _float(b2.get("kc_upper")), _float(b2.get("kc_lower"))
    ma5_2, ma5_3 = _float(b2.get("ma5")), _float(b3.get("ma5"))
    atr2 = _float(b2.get("atr"))
    values = [
        o1, h1, l1, c1, o2, h2, l2, c2, o3, h3, l3, c3,
        kc_u1, kc_l1, kc_u2, kc_l2, ma5_2, ma5_3, atr2,
    ]
    if any(v is None for v in values) or min(values) <= 0:
        return False
    mid2 = (o2 + c2) / 2.0
    # True Pivot Check (AGENTS.md 核心授權規範):
    # 1. 持倉呼吸防洗鐵律：開倉後若尚未實質獲利展開（多單最高浮動漲幅 < 0.5 ATR 且 ROE < 5%；空單同理），
    #    價格在成本價附近的 1m 小陰小陽或均線盤中抖動回踩，100% 判定為「成本區間正常呼吸回踩（PULLBACK）」，一票否決平倉！
    # 2. 方案 A 真峰谷獲利了結（True Pivot Exit）：
    #    - 前提條件：開倉後已實質獲利展開（多單 peak_gain >= 0.5 ATR 或 peak_roe >= 5% 且出場仍保有淨利 c3 > entry 與 quote > entry；空單同理）。
    #    - 峰值位置：必須衝破或觸碰同側 KC 外軌（多單 h2 >= kc_u2 或 h1 >= kc_u1；空單 l2 <= kc_l2 或 l1 <= kc_l1），或實體遠離中軌確立波段頂/底部。
    #    - 確認棒反轉：多單三點頂部樞紐（h2 > h1 且 h2 > h3，第 3 棒收陰跌破中點且跌破 MA5）；空單三點底部樞紐（l2 < l1 且 l2 < l3，第 3 棒收陽突破中點且站上 MA5）。
    if side == "SHORT":
        ma15_2, ma15_3 = _float(b2.get("ma15")), _float(b3.get("ma15"))
        kc_l3 = _float(b3.get("kc_lower"))
        if (
            ma15_2 is not None
            and ma15_3 is not None
            and c3 <= ma15_3
            and ma5_3 <= ma15_3
            and (
                (entry - c3) < 1.0 * atr2
                or ma5_3 <= ma5_2
                or (ma15_3 - ma5_3) >= (ma15_2 - ma5_2)
            )
        ):
            return False
        valley_low = min(l1, l2)
        gain_atr = (entry - valley_low) / atr2
        gain_roe = (entry - valley_low) / entry * 5.0
        profit_expanded = gain_atr >= 0.5 or gain_roe >= 0.05
        at_outer_rail = (l2 <= kc_l2 or l1 <= kc_l1)
        in_profit = c3 < entry and quote < entry

        # 空單真谷底平倉鐵律（True Valley Exit）：
        # 1. 若 1m 收盤價仍位於 MA15 下方、MA5 未金叉 MA15 且仍在 1.0 ATR 成本保護區內，或 MA5 仍持續下探 (ma5_3 <= ma5_2) / 均線空頭張口擴大，
        #    100% 認定為主跌浪反彈回踩洗盤 (PULLBACK)，嚴禁平空！
        # 2. 必須收出具有明確向上動能的陽線 (c3 - o3 >= 0.35 * atr2) 且站上 MA5、MA5 明確上彎
        # 3. 谷底探底顯著性（Prominence）與防回踩過敏：
        #    - 中間棒 Low 必須明確探底：(l1 - l2 >= 0.45 * atr2) 或實體下殺 (o2 - c2 >= 0.45 * atr2) 或衝出下軌深度 (kc_l2 - l2 >= 0.45 * atr2)。
        #    - 若中間棒僅為扁平十字線 (Doji) 或探底幅度微弱 (< 0.45 * atr2)，確認棒 c3 必須實質突破左棒起跌高點 (c3 > h1)，
        #      否則 100% 判定為「下跌途中的箱體震盪或回踩確認 (PULLBACK)」，絕非真谷底！
        is_prominent_valley = (
            (l1 - l2) >= 0.45 * atr2
            or (o2 - c2) >= 0.45 * atr2
            or (kc_l2 - l2) >= 0.45 * atr2
            or c3 > h1
        )
        is_strong_rebound_candle = (c3 - o3) >= 0.35 * atr2
        is_true_valley = (
            at_outer_rail
            and l2 < l1
            and l2 < l3
            and is_prominent_valley
            and c3 > o3
            and is_strong_rebound_candle
            and c3 > mid2
            and c3 > ma5_3
            and ma5_3 > ma5_2
            and profit_expanded
            and in_profit
        )
        # 二波加速探底「雙下影線 / 跌勢衰竭收回下軌」真谷底平空 (對稱於二波衝頂真峰頂)
        prior_outer_valley = False
        if len(closed) >= 5:
            for idx in range(max(0, len(closed) - 12), len(closed) - 3):
                row_prev = closed.iloc[idx]
                lp = _float(row_prev.get("low"))
                kclp = _float(row_prev.get("kc_lower"))
                if lp is not None and kclp is not None and lp <= kclp:
                    prior_outer_valley = True
                    break
        body2_s = abs(c2 - o2)
        body3_s = abs(c3 - o3)
        lwick2 = min(o2, c2) - l2
        lwick3 = min(o3, c3) - l3
        is_second_wave_climax_valley = (
            prior_outer_valley
            and l2 < kc_l2
            and l2 < l1
            and l3 > l2
            and lwick2 >= max(body2_s, 0.8 * atr2)
            and lwick3 >= body3_s
            and c3 > o3
            and (kc_l3 is not None and c3 > kc_l3)
            and profit_expanded
            and in_profit
        )
        is_two_bar_climax_break_ma15_short = (
            at_outer_rail
            and l1 < l2 < l3
            and c2 > o2
            and c3 > o3
            and c3 > ma5_3
            and (ma15_3 is not None and c3 > ma15_3)
            and profit_expanded
            and in_profit
        )
        return is_true_valley or is_second_wave_climax_valley or is_two_bar_climax_break_ma15_short

    if side == "LONG":
        ma15_2, ma15_3 = _float(b2.get("ma15")), _float(b3.get("ma15"))
        kc_u3 = _float(b3.get("kc_upper"))
        peak_high = max(h1, h2)
        gain_atr = (peak_high - entry) / atr2
        gain_roe = (peak_high - entry) / entry * 5.0
        profit_expanded = gain_atr >= 0.5 or gain_roe >= 0.05
        at_outer_rail = (h2 >= kc_u2 or h1 >= kc_u1)
        in_profit = c3 > entry and quote > entry

        # 二波加速衝頂「雙流星上影線 + 實體跌回上軌內」真峰頂平多 (Box 2 2號高點 09:14-09:15 實戰特徵)：
        # 前波已出現過一次破上軌脈衝波，回踩不破後展開第 2 波創高衝出 KC 上軌 (h2 > kc_u2)，
        # 但 b2 留長上影線 (uwick2 >= max(body2, 0.8 * atr2))，緊接著 b3 無法再創高 (h3 < h2)、
        # 再度留長上影線 (uwick3 >= body3) 且收紅(陰)跌回 KC 上軌之內 (c3 < o3 and c3 < kc_u3)，立即在 2 號高點獲利了結！
        prior_outer_peak = False
        if len(closed) >= 5:
            for idx in range(max(0, len(closed) - 12), len(closed) - 3):
                row_prev = closed.iloc[idx]
                hp = _float(row_prev.get("high"))
                kcup = _float(row_prev.get("kc_upper"))
                if hp is not None and kcup is not None and hp >= kcup:
                    prior_outer_peak = True
                    break
        body2_l = abs(c2 - o2)
        body3_l = abs(c3 - o3)
        uwick2 = h2 - max(o2, c2)
        uwick3 = h3 - max(o3, c3)
        is_second_wave_climax_peak = (
            prior_outer_peak
            and h2 > kc_u2
            and h2 > h1
            and h3 < h2
            and uwick2 >= max(body2_l, 0.8 * atr2)
            and uwick3 >= body3_l
            and c3 < o3
            and (kc_u3 is not None and c3 < kc_u3)
            and profit_expanded
            and in_profit
        )
        is_two_bar_climax_break_ma15_long = (
            at_outer_rail
            and h1 > h2 > h3
            and c2 < o2
            and c3 < o3
            and c3 < ma5_3
            and (ma15_3 is not None and c3 < ma15_3)
            and profit_expanded
            and in_profit
        )
        if is_second_wave_climax_peak or is_two_bar_climax_break_ma15_long:
            return True

        # 多單真峰頂平倉鐵律（True Peak Exit）與主升浪回踩防洗保護（Box 1 09:09 回踩不平倉）：
        # 1. 若 1m 收盤價仍位於 MA15 上方、MA5 未死叉 MA15，且滿足以下任一條件：
        #    (a) 仍在 1.0 ATR 成本保護區內 ((c3 - entry) < 1.0 * atr2)
        #    (b) MA5 斜率仍在向上推進 (ma5_3 >= ma5_2，如 09:08 之 0.017644 > 0.017634)
        #    (c) MA5 與 MA15 多頭張口仍在擴大 ((ma5_3 - ma15_3) >= (ma5_2 - ma15_2))
        #    100% 認定為主升浪正常回踩洗盤 (PULLBACK)，嚴禁平多！必須繼續抱到第 2 波高點！
        if (
            ma15_2 is not None
            and ma15_3 is not None
            and c3 >= ma15_3
            and ma5_3 >= ma15_3
            and (
                (c3 - entry) < 1.0 * atr2
                or ma5_3 >= ma5_2
                or (ma5_3 - ma15_3) >= (ma5_2 - ma15_2)
            )
        ):
            return False

        # 2. 必須收出具有明確向下下殺動能的陰線 (o3 - c3 >= 0.35 * atr2) 且跌破 MA5、MA5 明確下彎 (ma5_3 < ma5_2)
        # 3. 峰頂衝頂顯著性（Prominence）與防回踩過敏：
        #    - 中間棒 High 必須明確衝頂：(h2 - h1 >= 0.45 * atr2) 或實體拉升 (c2 - o2 >= 0.45 * atr2) 或衝出上軌深度 (h2 - kc_u2 >= 0.45 * atr2)。
        #    - 若中間棒僅為扁平十字線 (Doji) 或衝頂幅度微弱 (< 0.45 * atr2)，確認棒 c3 必須實質跌破左棒起漲低點 (c3 < l1)，
        #      否則 100% 判定為「上漲途中的箱體震盪或回踩確認 (PULLBACK)」，絕非真峰頂！
        is_prominent_peak = (
            (h2 - h1) >= 0.45 * atr2
            or (c2 - o2) >= 0.45 * atr2
            or (h2 - kc_u2) >= 0.45 * atr2
            or c3 < l1
        )
        is_strong_drop_candle = (o3 - c3) >= 0.35 * atr2
        is_true_peak = (
            at_outer_rail
            and h2 > h1
            and h2 > h3
            and is_prominent_peak
            and c3 < o3
            and is_strong_drop_candle
            and c3 < mid2
            and c3 < ma5_3
            and ma5_3 < ma5_2
            and profit_expanded
            and in_profit
        )
        return is_true_peak
    return False


def _has_matching_confirmed_kc_pivot(
    side: str,
    normalized_reason: str,
    position: Mapping[str, Any],
    metadata: Mapping[str, Any] | None,
) -> bool:
    expected_reason = {
        "LONG": "EXIT_BY_TRUE_PEAK_EXTREME_REVERSAL",
        "SHORT": "EXIT_BY_TRUE_VALLEY_EXTREME_REVERSAL",
    }.get(side)
    if normalized_reason != expected_reason:
        return False

    from core.services.exits.kc_true_extreme_exit import (
        pending_kc_true_extreme_exit,
    )

    pending = pending_kc_true_extreme_exit(
        dict(position),
        dict(metadata) if isinstance(metadata, Mapping) else None,
    )
    if pending is None or pending[0] != expected_reason:
        return False
    details = pending[1]
    expected_pivot_reason = (
        "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT"
        if side == "LONG"
        else "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT"
    )
    pivot_bar_id = _float(details.get("pivot_bar_id"))
    confirm_bar_id = _float(details.get("confirm_bar_id"))
    return (
        details.get("cause") == "THREE_POINT_CONFIRMED_PIVOT"
        and details.get("confirmed_pivot_reason") == expected_pivot_reason
        and pivot_bar_id is not None
        and confirm_bar_id is not None
        and confirm_bar_id > pivot_bar_id
    )


class UltimateShortExitGatekeeper:
    """Highest-priority exit veto; also mirrors the identical rules for LONG."""

    @classmethod
    def evaluate(
        cls,
        symbol: str,
        position: Mapping[str, Any],
        metadata: Mapping[str, Any] | None,
        reason: str,
        price: float,
        *,
        frame: Any = None,
        is_bar_closed: bool | None = None,
        now: float | None = None,
    ) -> ExitVerdict:
        from core.symbol_aliases import is_meme_breakout_symbol

        normalized = _normalized_exit_reason(reason)
        if not is_meme_breakout_symbol(symbol):
            return ExitVerdict(DEFER, "NOT_MEME_SYMBOL", {})
        if str(reason or "").strip() == "手動平倉":
            return ExitVerdict(DEFER, "MANUAL_EXIT", {})
        if any(token in normalized for token in ACCOUNT_LEVEL_TOKENS):
            return ExitVerdict(DEFER, "ACCOUNT_LEVEL_EXIT", {})
        if "VIRTUAL_PROFIT_LOCK" in normalized:
            return ExitVerdict(DEFER, "VIRTUAL_PROFIT_LOCK_EXIT", {})
        if "ACTIVE_EXIT_PV_" in normalized or "ACTIVE_EXIT_KC_" in normalized:
            return ExitVerdict(DEFER, "PEAK_VALLEY_LOW_LATENCY_EXIT", {})
        side = str(position.get("side") or "").upper()
        evidence: dict = {"symbol": symbol, "side": side, "reason": reason}

        def hold(code: str) -> ExitVerdict:
            logger.info("[VETO_HOLD_%s] ULTIMATE_EXIT_GATE symbol=%s code=%s reason=%s",
                        side, symbol, code, reason)
            if side == "SHORT":
                logger.info("[UI_DISPLAY: 續抱] symbol=%s reason=%s", symbol, code)
            return ExitVerdict(HOLD, code, evidence)

        if any(token in normalized for token in WATERFALL_EXIT_TOKENS):
            from core.services.exits.abnormal_candle_exit import live_adverse_waterfall_evidence

            waterfall_evidence = live_adverse_waterfall_evidence(
                position, frame, price,
            )
            if waterfall_evidence is None:
                return hold("VETO_WATERFALL_LIVE_BODY_OR_STRUCTURE_NOT_CONFIRMED")
            waterfall_evidence.update({"symbol": symbol, "reason": reason})
            logger.warning(
                "[ULTIMATE_EXIT_ALLOW] symbol=%s side=%s code=EXIT_BY_EXTREME_WATERFALL "
                "body=%s atr=%s quote=%s kc_middle=%s",
                symbol, side, waterfall_evidence["body"], waterfall_evidence["atr"],
                waterfall_evidence["quote"], waterfall_evidence["kc_middle"],
            )
            return ExitVerdict(ALLOW, "EXIT_BY_EXTREME_WATERFALL", waterfall_evidence)

        if normalized in EMERGENCY_CIRCUIT_BREAKER_REASONS.values() or any(
            token in normalized for token in ("EMERGENCY_WATERFALL", "EMERGENCY_SURGE", "WATERFALL_CIRCUIT_BREAKER")
        ):
            is_emerg_wf, emerg_wf_reason, emerg_wf_ev = cls.check_waterfall_emergency_exit(
                symbol, position, metadata, price, frame=frame
            )
            if is_emerg_wf:
                logger.warning(
                    "[ULTIMATE_EXIT_ALLOW] symbol=%s side=%s code=%s (intra-bar emergency circuit breaker) evidence=%s",
                    symbol, side, emerg_wf_reason, emerg_wf_ev,
                )
                return ExitVerdict(ALLOW, emerg_wf_reason, emerg_wf_ev)
            return hold("VETO_EMERGENCY_CIRCUIT_BREAKER_NOT_CONFIRMED")

        if any(token in normalized for token in ORDINARY_PULLBACK_EXIT_TOKENS):
            return hold("VETO_HOLD_ORDINARY_PULLBACK_UNTIL_STRUCTURE_BREAK")

        def allow(code: str) -> ExitVerdict:
            if (
                normalized not in OWN_STRUCTURE_REASONS | OWN_DISASTER_REASONS
                and not any(token in normalized for token in PROFIT_LOCK_TOKENS)
            ):
                # Gate-only authority: foreign labels (e.g. numeric hard SL) keep their own policy.
                return ExitVerdict(DEFER, "VETO_PASSED_LEGACY_AUTHORITY_REQUIRED", evidence)
            evidence["exit_type"] = code
            logger.warning("[ULTIMATE_EXIT_ALLOW] symbol=%s side=%s code=%s reason=%s",
                           symbol, side, code, reason)
            return ExitVerdict(ALLOW, code, evidence)

        if side not in ("LONG", "SHORT"):
            return hold("VETO_EXIT_INVALID_SIDE")
        sign = 1 if side == "LONG" else -1
        quote = _float(price)
        entry = _float(position.get("entry_price"))
        opened = _float(position.get("open_timestamp"))
        if opened is None:
            opened = _float((metadata or {}).get("open_timestamp"))
        if not quote or quote <= 0 or not entry or entry <= 0 or not opened or opened <= 0:
            return hold("VETO_EXIT_INVALID_POSITION_DATA")

        try:
            closed = closed_entry_candles(frame) if frame is not None else None
        except Exception as exc:  # malformed frame must fail closed
            logger.error("ULTIMATE_EXIT_FRAME_ERROR symbol=%s error=%s", symbol, exc)
            closed = None
        if closed is None or len(closed) < 1:
            from core.services.exits.authorized_position_exit import strategy_exit_bar_block_reason
            entry_bar = strategy_exit_bar_block_reason(position, metadata or {})
            return hold(entry_bar or "VETO_EXIT_CLOSED_DATA_UNAVAILABLE")
        from core.gates.master_gate_funnel import meme_distance_atr

        # 單筆部位固定 3.0 ATR 硬止損與反側外軌硬止損已依「不要硬止損，只留帳戶硬止損，大瀑布及市場熔斷！」全面停用
        live_atr = _float(closed.iloc[-1].get("atr"))
        loss_atr = 0.0
        if live_atr is not None and live_atr > 0:
            disaster_atr = meme_distance_atr(symbol, live_atr)
            loss_atr = -sign * (quote - entry) / disaster_atr
            evidence["loss_atr"] = round(loss_atr, 4)

        if len(closed) < 2:
            return hold("VETO_EXIT_CLOSED_DATA_UNAVAILABLE")
        if _has_matching_confirmed_kc_pivot(
            side, normalized, position, metadata,
        ):
            return allow(TRUE_OUTER_PIVOT_EXIT_REASONS[side])
        if normalized in STRICT_TRUE_PIVOT_EXIT_CANDIDATES[side]:
            from core.services.exits.strict_true_pivot import profitable_true_pivot_exit

            pivot = profitable_true_pivot_exit(
                {**position, "symbol": symbol},
                frame,
                dict(metadata or {}),
                quote=quote,
            )
            if pivot is None:
                return hold("VETO_PIVOT_CONFIRMATION_OR_1ATR_NET_PROFIT")
            evidence.update(pivot[1])
            evidence["candidate_reason"] = reason
            code = "PROFIT_CONFIRMED_TRUE_PIVOT_APPROVED"
            logger.warning(
                "[ULTIMATE_EXIT_ALLOW] symbol=%s side=%s code=%s candidate_reason=%s",
                symbol, side, code, reason,
            )
            return ExitVerdict(ALLOW, code, evidence)
        current, previous = closed.iloc[-1], closed.iloc[-2]
        fields = {}
        for key in ("timestamp", "open", "high", "low", "close", "ma5", "ma15",
                    "kc_upper", "kc_middle", "kc_lower"):
            fields[key] = _float(current.get(key))
        fields["ma5_prev"] = _float(previous.get("ma5"))
        fields["ma15_prev"] = _float(previous.get("ma15"))
        fields["kc_middle_prev"] = _float(previous.get("kc_middle"))
        km_prev2 = _float(closed.iloc[-3].get("kc_middle")) if len(closed) >= 3 else None
        # Same ATR convention as the entry snapshot: the previous completed bar.
        fields["atr"] = _float(previous.get("atr"))
        if any(v is None or v <= 0 for v in fields.values()):
            return hold("VETO_EXIT_INVALID_CLOSED_DATA")
        fields["kc_middle_prev2"] = km_prev2
        atr = meme_distance_atr(symbol, fields["atr"])
        evidence.update({k: fields[k] for k in ("close", "open", "ma5", "ma15", "kc_middle",
                                                 "kc_upper", "kc_lower", "atr")})
        evidence["distance_atr"] = atr

        closed_flag = current.get("is_closed")
        frame_finality = (
            getattr(frame, "attrs", {}).get("entry_finality_verified") is True
        )
        explicitly_closed = (
            closed_flag is True
            or (type(closed_flag).__name__ == "bool_" and bool(closed_flag))
            or frame_finality
        )
        current_time = time.time() if now is None else float(now)
        bar_minute = _minute(fields["timestamp"])
        angle_reason = MA5_ANGLE_REVERSAL_EXIT_REASONS.get(side)
        if normalized == angle_reason:
            signal = _ma5_angle_signal(frame, quote, current_time)
            expected_side = "SHORT" if side == "LONG" else "LONG"
            if (
                signal is not None
                and signal["side"] == expected_side
                and _minute(signal["timestamp"]) > _minute(opened)
            ):
                evidence.update(signal)
                evidence["threshold_degrees"] = MA5_ANGLE_REVERSAL_DEGREES
                return allow(angle_reason)
            return hold("VETO_MA5_ANGLE_REVERSAL_NOT_CONFIRMED")

        if is_bar_closed is False or not (is_bar_closed is True or explicitly_closed):
            return hold("VETO_EXIT_BAR_NOT_CLOSED")
        if bar_minute != int(current_time // 60) - 1:
            # Only the freshly completed 1m bar can authorize a trend exit.
            return hold("VETO_EXIT_BAR_NOT_CLOSED")

        close, kc_mid = fields["close"], fields["kc_middle"]
        sharp_reason = SHARP_REVERSAL_EXIT_REASONS.get(side)
        if normalized == sharp_reason:
            signal = _sharp_reversal_signal(side, closed)
            if (
                signal is not None
                and bar_minute > _minute(opened)
            ):
                evidence.update(signal)
                evidence["threshold_atr"] = SHARP_REVERSAL_BODY_ATR
                return allow(sharp_reason)
            return hold("VETO_SHARP_REVERSAL_NOT_CONFIRMED")

        slope_reason = KC_MIDDLE_SLOPE_EXIT_REASONS.get(side)
        if normalized == slope_reason:
            current_middle = _float(current.get("kc_middle"))
            previous_middle = _float(previous.get("kc_middle"))
            slope = (
                current_middle - previous_middle
                if current_middle is not None and previous_middle is not None
                else None
            )
            min_slope = 0.05 * atr if (atr is not None and atr > 0) else 0.0
            is_rebound_or_break = (
                (side == "LONG" and (close > fields["open"] or close < current_middle))
                or (side == "SHORT" and (close < fields["open"] or close > current_middle))
            )
            confirms = (
                slope is not None
                and ((side == "LONG" and slope < 0) or (side == "SHORT" and slope > 0))
                and (abs(slope) >= min_slope or math.isclose(abs(slope), min_slope, rel_tol=1e-9))
                and is_rebound_or_break
                and bar_minute > _minute(opened)
            )
            if confirms:
                evidence["kc_middle_slope"] = slope
                evidence["min_kc_slope"] = min_slope
                return allow(slope_reason)
            return hold("VETO_KC_MIDDLE_SLOPE_EXIT_NOT_CONFIRMED")

        # Exits are only authorized on a fresh, opposite-signal bar. A position-direction
        # completed bar is a hard hold even when the quote is near the entry.
        if side == "SHORT" and close <= fields["open"]:
            turn_candidate = _kc_flip_turn_candidate(symbol, side, closed, current_time)
            if live_atr is not None and live_atr > 0 and abs(loss_atr) <= COST_SHIELD_ATR and turn_candidate:
                return hold(f"VETO_HOLD_{side}_COST_SHIELD")
            return hold(f"VETO_HOLD_{side}_OPPOSITE_BODY_REQUIRED")
        if side == "LONG" and close >= fields["open"]:
            turn_candidate = _kc_flip_turn_candidate(symbol, side, closed, current_time)
            if live_atr is not None and live_atr > 0 and abs(loss_atr) <= COST_SHIELD_ATR and turn_candidate:
                return hold(f"VETO_HOLD_{side}_COST_SHIELD")
            return hold(f"VETO_HOLD_{side}_OPPOSITE_BODY_REQUIRED")

        if normalized in OWN_STRUCTURE_REASONS and _outer_rail_true_pivot_release(
            symbol, side, closed, opened, entry, quote, is_bar_closed, current_time,
        ):
            return allow(TRUE_OUTER_PIVOT_EXIT_REASONS[side])

        # 🚨【使用者最高指示：K線若不是大瀑布、異常K，就要讓他走完，不要一開始就平倉；變回同向K線就可以不平倉（多空都要改）】
        # 若當前未收線 live K 棒已形成，且即時最新價/K 棒已變回順向同向 K（做空變回紅 K 陰線 quote <= live_open；做多變回綠 K 陽線 quote >= live_open），
        # 且非反向巨型瀑布（> 1.5 ATR 貫穿中軌）或異常K，100% 一票否決平倉 (VETO_HOLD)，讓部位走完、避免在反彈回踩變回同向時被提前洗下車！
        if frame is not None and isinstance(frame, pd.DataFrame) and len(frame) >= 1:
            last_raw_candle = frame.iloc[-1]
            last_is_closed = last_raw_candle.get("is_closed")
            if last_is_closed is False or (type(last_is_closed).__name__ == "bool_" and not bool(last_is_closed)):
                live_open = _float(last_raw_candle.get("open"))
                if live_open is not None and live_open > 0:
                    if side == "SHORT" and quote <= live_open:
                        return hold("VETO_HOLD_SHORT_ON_FAVORABLE_RED_CANDLE")
                    elif side == "LONG" and quote >= live_open:
                        return hold("VETO_HOLD_LONG_ON_FAVORABLE_GREEN_CANDLE")
        # 單筆部位反側外軌硬止損 (DISASTER_RAIL_REASONS) 已停用：只留帳戶硬止損、大瀑布及市場熔斷！

        # ⚡ 通道 A：真峰谷/V轉「最高優先級極速平倉與反手」(True Peak/Valley Override)
        # 核心鐵律：持倉防洗！開倉後不要隨便平倉，只有當波段實質展開獲利 (peak_gain >= 0.5 ATR 或 peak_roe >= 5%)，
        # 且當前確實處於正淨利獲利區 (空單 quote < entry，多單 quote > entry)，才放行谷底陽線/頂部陰線極速平倉！
        # 100% 豁免 MA15 / KC 中軌到達限制與持倉滿 5 根限制！
        raw_atr = fields["atr"]
        pos_trough = _float(position.get("trough_price") or position.get("lowest_price"))
        pos_peak = _float(position.get("highest_price") or position.get("peak_price"))
        try:
            open_min = _minute(opened)
            held_bars = closed[closed["timestamp"].apply(_minute) >= open_min]
            bars_low = float(held_bars["low"].min()) if len(held_bars) > 0 else fields["low"]
            bars_high = float(held_bars["high"].max()) if len(held_bars) > 0 else fields["high"]
        except Exception:
            bars_low = fields["low"]
            bars_high = fields["high"]
        trough_candidates = [v for v in (pos_trough, bars_low, fields["low"], close, quote) if v is not None and v > 0]
        peak_candidates = [v for v in (pos_peak, bars_high, fields["high"], close, quote) if v is not None and v > 0]
        trough_val = min(trough_candidates) if trough_candidates else entry
        peak_val = max(peak_candidates) if peak_candidates else entry

        buffer_line = kc_mid - sign * STRUCTURE_BUFFER_ATR * atr
        evidence["structure_line"] = buffer_line
        beyond_buffer = -sign * (close - buffer_line) > 0
        fresh_ma_cross = -sign * (fields["ma5_prev"] - fields["ma15_prev"]) <= 0 and -sign * (fields["ma5"] - fields["ma15"]) > 0
        full_structure_break_ready = (
            beyond_buffer
            and fresh_ma_cross
            and -sign * (close - fields["ma15"]) > 0
            and -sign * (quote - fields["ma15"]) > 0
            and abs(loss_atr) > COST_SHIELD_ATR
            and (bar_minute - _minute(opened)) >= MIN_BARS_HELD
        )

        # ──【通道 T：結構性趨勢反轉平倉與反手機制 (Trend Reversal Exit Gate)】──
        # 依據最新授權：若持有多單時，行情出現「KC 中軌明確向下 + MA5/15 死叉跌破中軌 + 峰頂第3點確立 (大陰線跌破前低 c0 < l1)」，
        # 判定多頭結構已徹底破壞，允許及時認錯平多，並無縫切換為空頭審查！
        # 空單同理：若持空單時，行情出現「KC 中軌明確向上 + MA5/15 金叉突破中軌 + 谷底第3點確立 (大陽線突破前高 c0 > h1)」，及時認錯平空！
        # 🛡️ 成本保護盾：在成本區間 (< 0.50 ATR 且未實質開展波段) 嚴禁因微小晃動誤判為趨勢反轉平倉！
        # 🚨【非峰頂一票否決平多】：若未形成峰頂吞噬（未跌破前一根低點 c0 < l1 或實體比例 < 55%），嚴禁因微幅跌破中軌誤殺多單！
        bars_held_reversal = bar_minute - _minute(opened)
        close_loss_atr_val = -sign * (close - entry) / (disaster_atr if disaster_atr > 0 else 1.0)
        in_cost_zone = abs(loss_atr) <= 0.50 or abs(close_loss_atr_val) <= 0.50
        prev_low = _float(previous.get("low"))
        prev_high = _float(previous.get("high"))
        candle_range = float(current.get("high", 0.0)) - float(current.get("low", 0.0))
        body_size = abs(close - float(current.get("open", 0.0)))
        is_solid_candle = (body_size / candle_range >= 0.55) if candle_range > 0 else False

        if bars_held_reversal >= 1 and not in_cost_zone:
            if side == "LONG":
                is_kc_down = (fields["kc_middle"] < fields["kc_middle_prev"]) or (str(fields.get("macro_trend", "")).upper() == "BEARISH")
                is_ma_bearish = (fields["ma5"] < fields["ma15"])
                is_below_mid = (close < fields["kc_middle"])
                is_momentum_bearish = (close < fields["ma5"]) or (fields["ma5"] < fields["ma5_prev"]) or (close < fields["open"])
                # 峰頂第 3 點確認：收陰線且收盤價實體跌破前一根最低價 (c0 < l1) 且實體比例 >= 55%
                is_peak_engulf = (close < float(current.get("open", 0.0))) and (prev_low is not None and close < prev_low) and is_solid_candle
                if is_kc_down and is_ma_bearish and is_below_mid and is_momentum_bearish and is_peak_engulf:
                    return allow(TREND_REVERSAL_EXIT_REASONS[side])
            elif side == "SHORT":
                is_kc_up = (fields["kc_middle"] > fields["kc_middle_prev"]) or (str(fields.get("macro_trend", "")).upper() == "BULLISH")
                is_ma_bullish = (fields["ma5"] > fields["ma15"])
                is_above_mid = (close > fields["kc_middle"])
                is_momentum_bullish = (close > fields["ma5"]) or (fields["ma5"] > fields["ma5_prev"]) or (close > fields["open"])
                # 谷底第 3 點確認：收陽線且收盤價實體突破前一根最高價 (c0 > h1) 且實體比例 >= 55%
                is_trough_engulf = (close > float(current.get("open", 0.0))) and (prev_high is not None and close > prev_high) and is_solid_candle
                if is_kc_up and is_ma_bullish and is_above_mid and is_momentum_bullish and is_trough_engulf:
                    return allow(TREND_REVERSAL_EXIT_REASONS[side])

        # 1. 【真谷底確診 ➔ 100% 瞬間平空 (True Bottom Exit)】：
        #    - 審查條件（必須同時成立）：
        #            (1) 當根 1m 為實體綠 K (close_price > open_price)
        #            (2) 1m 收盤價實質站上 MA5 (close_price > ma5_curr)
        #            (3) MA5 明確向上拐頭 (ma5_curr > ma5_prev) 且 (ma5_curr > ma15_curr 或金叉穿越)
        #            (4) 避開成本區洗盤：已展開波段獲利 (gain_atr >= 0.5 或波段極值探出 KC 軌外且利潤 > 0.6 ATR)
        #    🚨【空頭主降浪一票否決】：若 1m 收盤價仍位於 MA5 下方或 MA15 下方，或 MA5 未金叉/未高於 MA15，
        #       100% 判定為「空頭順勢壓制 / 正常反彈洗盤」，100% 傳回 False (HOLD) 阻斷提前平空！
        if side == "SHORT" and not full_structure_break_ready:
            gain_atr_short = (entry - trough_val) / raw_atr if raw_atr > 0 else 0.0
            has_bottom_extremum = (pos_trough is not None and pos_trough < fields["kc_lower"]) or (trough_val < fields["kc_lower"])
            profit_expanded_short = gain_atr_short >= 0.5 or (has_bottom_extremum and gain_atr_short >= 0.8)
            in_profit_short = (quote < entry and close < entry) or (has_bottom_extremum and gain_atr_short >= 0.8 and loss_atr <= 0.6)
            is_bottom_confirmed = (
                profit_expanded_short
                and in_profit_short
                and close > fields["open"]
                and close > fields["ma5"]
                and fields["ma5"] > fields["ma5_prev"]
                and (fields["ma5"] > fields["ma15"] or (fields["ma5"] >= fields["ma15"] and fields["ma5_prev"] < fields["ma15_prev"]))
            )
            if is_bottom_confirmed and (
                normalized in OWN_STRUCTURE_REASONS
                or "BOTTOM" in normalized
                or "VALLEY" in normalized
                or "REVERSAL" in normalized
                or normalized in TRUE_OUTER_PIVOT_EXIT_REASONS.values()
            ):
                return allow(BOTTOM_REVERSAL_EXIT_REASONS[side])

        # 2. 【真峰頂確診 ➔ 100% 瞬間平多 (True Top Exit)】：
        #    - 審查條件（必須同時成立）：
        #            (1) 當根 1m 為實體紅 K (陰線, close_price < open_price)
        #            (2) 1m 收盤價實質跌破 MA5 (close_price < ma5_curr)
        #            (3) MA5 明確向下拐頭 (ma5_curr < ma5_prev) 且 (ma5_curr < ma15_curr 或死叉穿越)
        #            (4) 避開成本區洗盤：已展開波段獲利 (gain_atr >= 0.5 或波段極值探出 KC 軌外且利潤 > 0.6 ATR)
        #    🚨【多頭主升浪一票否決】：若 1m 收盤價仍位於 MA5 上方或 MA15 上方，或 MA5 未死叉/未低於 MA15，
        #       100% 判定為「多頭順勢支撐 / 正常回踩洗盤」，100% 傳回 False (HOLD) 阻斷提前平多！
        if side == "LONG" and not full_structure_break_ready:
            gain_atr_long = (peak_val - entry) / raw_atr if raw_atr > 0 else 0.0
            has_top_extremum = (pos_peak is not None and pos_peak > fields["kc_upper"]) or (peak_val > fields["kc_upper"])
            profit_expanded_long = gain_atr_long >= 0.5 or (has_top_extremum and gain_atr_long >= 0.8)
            in_profit_long = (quote > entry and close > entry) or (has_top_extremum and gain_atr_long >= 0.8 and loss_atr <= 0.6)
            is_top_confirmed = (
                profit_expanded_long
                and in_profit_long
                and close < fields["open"]
                and close < fields["ma5"]
                and fields["ma5"] < fields["ma5_prev"]
                and (fields["ma5"] < fields["ma15"] or (fields["ma5"] <= fields["ma15"] and fields["ma5_prev"] > fields["ma15_prev"]))
            )
            if is_top_confirmed and (
                normalized in OWN_STRUCTURE_REASONS
                or "TOP" in normalized
                or "PEAK" in normalized
                or "REVERSAL" in normalized
                or normalized in TRUE_OUTER_PIVOT_EXIT_REASONS.values()
            ):
                return allow(BOTTOM_REVERSAL_EXIT_REASONS[side])

        # 3. 💥【峰谷轉彎出現反向 K 線及時平倉（不用等到中軌才平倉）】
        #    核心鐵律：
        #    (1) 當峰谷轉折已發生（MA5 拐頭逆向）：
        #        多單持倉時 MA5 向下拐頭 (ma5 < ma5_prev)；空單持倉時 MA5 向上拐頭 (ma5 > ma5_prev)。
        #    (2) 當根收線為明確的反向 K 線（多單收紅 K 陰線 close < open 且跌破 MA5；空單收綠 K 陽線 close > open 且突破 MA5）。
        #    (3) 🚨【同向 K 線絕不平倉】：若當根為同向 K 線（多單仍收綠 K 陽線 close >= open，空單仍收紅 K 陰線 close <= open），
        #        100% 傳回 False (HOLD) 絕不平倉繼續持有！
        #    (4) 不用等到跌回或漲回 KC 中軌才平倉，及時在峰谷反轉處落袋為安！
        try:
            open_min_k = _minute(opened)
            recent_held_bars = closed[closed["timestamp"].apply(_minute) >= open_min_k]
            if len(recent_held_bars) >= 2:
                last_2_bars = recent_held_bars.tail(2)
                if side == "LONG":
                    # 多單持倉：MA5 向下拐頭且最近兩根皆收陰線 (紅 K) 且跌破 MA5，且已跌破 MA15 防守
                    is_consecutive_bear_bars = all(
                        float(r.close) < float(r.open) for r in last_2_bars.itertuples()
                    )
                    ma5_turning_down = fields["ma5"] < fields["ma5_prev"]
                    in_profit_or_protect = quote > entry or (peak_val - entry) >= 0.5 * raw_atr
                    # 若依然站在 MA15 之上且 MA5 >= MA15 則屬於正常回踩，唯有破位或連陰跌破 MA15 才是真轉向
                    is_structure_lost = close < fields["ma15"] or fields["ma5"] < fields["ma15"]
                    if is_consecutive_bear_bars and ma5_turning_down and close < fields["ma5"] and is_structure_lost and in_profit_or_protect:
                        return allow("EXIT_LONG_CONSECUTIVE_BEAR_CANDLES_PIVOT_TURN")
                elif side == "SHORT":
                    # 空單持倉：MA5 向上拐頭且最近兩根皆收陽線 (綠 K) 且站上 MA5，且已突破 MA15 防守
                    is_consecutive_bull_bars = all(
                        float(r.close) > float(r.open) for r in last_2_bars.itertuples()
                    )
                    ma5_turning_up = fields["ma5"] > fields["ma5_prev"]
                    in_profit_or_protect = quote < entry or (entry - trough_val) >= 0.5 * raw_atr
                    is_structure_lost = close > fields["ma15"] or fields["ma5"] > fields["ma15"]
                    if is_consecutive_bull_bars and ma5_turning_up and close > fields["ma5"] and is_structure_lost and in_profit_or_protect:
                        return allow("EXIT_SHORT_CONSECUTIVE_BULL_CANDLES_PIVOT_TURN")
        except Exception as e:
            logger.debug("CONSECUTIVE_PIVOT_TURN_CHECK_ERROR symbol=%s error=%s", symbol, e)

        # 🛡️ 通道 B：多頭/空頭順勢走勢死抱鐵律 (Trend-Holding Immunity & Anti-Churn Lock)
        # 🚨【核心硬鎖：開多單中軌向下，開空單中軌向上；不用等到呈 U 型！】
        km_curr = fields["kc_middle"]
        km_prev = fields["kc_middle_prev"]

        if side == "LONG":
            is_true_peak_exit = (normalized in TRUE_OUTER_PIVOT_EXIT_REASONS.values()) or (
                normalized in BOTTOM_REVERSAL_EXIT_REASONS.values() and quote > entry
            )
            is_account_safety = any(token in normalized for token in ACCOUNT_LEVEL_TOKENS)
            # 若為真峰頂或帳戶風控，不被普通續抱阻擋
            if not (is_true_peak_exit or is_account_safety):
                if close >= fields["ma15"] and fields["ma5"] >= fields["ma15"]:
                    return hold("VETO_HOLD_LONG_ABOVE_MA15")
                # 鐵律：開多單中軌沒向下 (km_curr < km_prev)，100% 一票否決平多！不用等 U 型
                is_kc_down = (km_curr < km_prev) or str(fields.get("macro_trend", "")).upper() == "BEARISH"
                if not is_kc_down:
                    return hold("VETO_HOLD_LONG_KC_MIDDLE_NOT_DOWN")
            is_kc_up_header = fields["kc_middle"] >= fields["kc_middle_prev"] or str(fields.get("macro_trend", "")).upper() == "BULLISH"
            if is_kc_up_header:
                if not (is_true_peak_exit or is_account_safety):
                    return hold("VETO_HOLD_LONG_UPWARD_TREND_NO_CHURN")

        if side == "SHORT":
            is_true_valley_exit = (normalized in TRUE_OUTER_PIVOT_EXIT_REASONS.values()) or (
                normalized in BOTTOM_REVERSAL_EXIT_REASONS.values() and quote < entry
            )
            is_account_safety = any(token in normalized for token in ACCOUNT_LEVEL_TOKENS)
            # 若為真谷底或帳戶風控，不被普通續抱阻擋
            if not (is_true_valley_exit or is_account_safety):
                if close <= fields["ma15"] and fields["ma5"] <= fields["ma15"]:
                    return hold("VETO_HOLD_SHORT_BELOW_MA15")
                # 鐵律：開空單中軌沒向上 (km_curr > km_prev)，100% 一票否決平空！不用等 U 型
                is_kc_up = (km_curr > km_prev) or str(fields.get("macro_trend", "")).upper() == "BULLISH"
                if not is_kc_up:
                    return hold("VETO_HOLD_SHORT_KC_MIDDLE_NOT_UP")
            is_kc_down_header = fields["kc_middle"] <= fields["kc_middle_prev"] or str(fields.get("macro_trend", "")).upper() == "BEARISH"
            if is_kc_down_header:
                if not (is_true_valley_exit or is_account_safety):
                    return hold("VETO_HOLD_SHORT_DOWNWARD_TREND_NO_CHURN")

        # 🎯 階梯鎖利與移動止盈放行（僅在已跌破/突破 MA15 且脫離多頭/空頭主升浪保護後才允許執行）
        is_profit_lock_reason = any(token in normalized for token in PROFIT_LOCK_TOKENS)
        is_net_profit_favorable = (side == "LONG" and quote >= entry * 1.001) or (side == "SHORT" and quote <= entry * 0.999)
        if is_profit_lock_reason and is_net_profit_favorable:
            return allow(normalized)

        candle_body = abs(close - fields["open"])
        fresh_ma_cross = -sign * (fields["ma5_prev"] - fields["ma15_prev"]) <= 0 and -sign * (fields["ma5"] - fields["ma15"]) > 0
        ma_reversed = -sign * (fields["ma5"] - fields["ma15"]) > 0 and -sign * (fields["ma5"] - fields["ma5_prev"]) > 0
        wave_favorable_atr = (
            (entry - trough_val) / atr if (side == "SHORT" and atr > 0)
            else ((peak_val - entry) / atr if (side == "LONG" and atr > 0) else 0.0)
        )
        wave_favorable_roe = (
            (entry - trough_val) / entry * 5.0 if (side == "SHORT" and entry > 0)
            else ((peak_val - entry) / entry * 5.0 if (side == "LONG" and entry > 0) else 0.0)
        )
        in_profit_for_fast_exit = (side == "LONG" and close > entry) or (side == "SHORT" and close < entry)
        wave_expanded_for_fast_exit = (wave_favorable_atr >= 1.5 or wave_favorable_roe >= 0.05) and in_profit_for_fast_exit
        fast_reversal = (
            -sign * (close - fields["open"]) > 0
            and candle_body >= 0.35 * raw_atr
            and -sign * (close - fields["kc_middle"]) > 0
            and -sign * (close - fields["ma15"]) > 0
            and -sign * (quote - fields["kc_middle"]) > 0
            and -sign * (quote - fields["ma15"]) > 0
            and (fresh_ma_cross or (ma_reversed and candle_body >= 1.50 * raw_atr))
            and wave_expanded_for_fast_exit
        )

        bars_held = bar_minute - _minute(opened)
        evidence["bars_held"] = bars_held
        if bars_held < MIN_BARS_HELD:
            if fast_reversal and normalized in OWN_STRUCTURE_REASONS and loss_atr >= 0.5:
                return allow(FAST_TOP_EXIT_REASONS[side])
            return hold("VETO_HOLD_MIN_5_COMPLETED_BARS")

        if live_atr is not None and live_atr > 0:
            from core.services.exits.strict_true_pivot import PROFIT_PIVOT_REASONS

            close_loss_atr = -sign * (close - entry) / disaster_atr
            in_cost_shield = abs(loss_atr) <= COST_SHIELD_ATR or (
                abs(close_loss_atr) <= COST_SHIELD_ATR and loss_atr <= COST_SHIELD_ATR
            )
            if in_cost_shield and normalized not in PROFIT_PIVOT_REASONS and not any(
                token in normalized for token in COST_SHIELD_EXEMPT_TOKENS
            ):
                if normalized in OWN_STRUCTURE_REASONS and _kc_flip_release(
                    symbol, side, closed, opened, is_bar_closed, current_time,
                ):
                    return allow(KC_FLIP_EXIT_REASONS[side])
                return hold(f"VETO_HOLD_{side}_COST_SHIELD")

        buffer_line = kc_mid - sign * STRUCTURE_BUFFER_ATR * atr
        evidence["structure_line"] = buffer_line
        beyond_buffer = -sign * (close - buffer_line) > 0

        # 🚨【核心硬鎖：順勢走勢 KC 沒翻向，中間 100% 一票否決中途割肉/提前平倉，死抱到峰谷】
        # 做多時：只要 KC 中軌維持向上 (kc_middle >= kc_middle_prev 或 macro_trend == BULLISH)，
        # 中間的任何 1m 回踩或陰線下殺（未構成 > 1.0 ATR 完整結構死叉破位），絕不允許被偽頂部平倉提前洗出場！
        if side == "LONG" and is_kc_up_header and not in_profit_for_fast_exit and not full_structure_break_ready:
            return hold("VETO_HOLD_LONG_UPWARD_KC_UNTIL_TRUE_PEAK")

        # 做空時：只要 KC 中軌維持向下 (kc_middle <= kc_middle_prev 或 macro_trend == BEARISH)，
        # 中間的任何 1m 反抽或陽線拉升（未構成 > 1.0 ATR 完整結構金叉破位），絕不允許被偽谷底平倉提前洗出場！
        if side == "SHORT" and is_kc_down_header and not in_profit_for_fast_exit and not full_structure_break_ready:
            return hold("VETO_HOLD_SHORT_DOWNWARD_KC_UNTIL_TRUE_VALLEY")

        # 🚀 頂部/底部大實體 K 線貫穿極速平倉通道 (Fast Top / Bottom Exit Channel)
        # 解決龍蝦/PEPE峰頂滯後平多：脫離成本保護盾(Cost Shield)後，當收線時出現大實體逆向K線(陰線下殺 Body >= 0.35 ATR 貫穿 KC 中軌與 MA15 且 MA5 死叉 MA15)，
        # 100% 豁免後續 0.50 ATR 緩衝區與 fresh_cross 限制，直接在頂部/底部第一時間授權平倉！
        if fast_reversal and (
            not beyond_buffer
            or not fresh_ma_cross
            or normalized in FAST_TOP_EXIT_REASONS.values()
            or "FAST_TOP" in normalized
            or "FAST_BOTTOM" in normalized
        ) and normalized in OWN_STRUCTURE_REASONS:
            return allow(FAST_TOP_EXIT_REASONS[side])

        if not beyond_buffer:
            return hold(f"VETO_HOLD_{side}_WITHIN_050_ATR_BUFFER")
        ma5, ma15, ma5_prev = fields["ma5"], fields["ma15"], fields["ma5_prev"]
        # Fresh cross only: MA5 must have been on the position side of MA15 on the previous bar.
        fresh_cross = -sign * (ma5_prev - fields["ma15_prev"]) <= 0
        if not (fresh_cross and -sign * (ma5 - ma15) > 0 and -sign * (ma5 - ma5_prev) > 0):
            return hold(f"VETO_HOLD_{side}_MA5_MA15_NOT_CROSSED")
        if not -sign * (close - fields["open"]) > 0:
            return hold(f"VETO_HOLD_{side}_OPPOSITE_BODY_REQUIRED")
        return allow(STRUCTURE_EXIT_REASONS[side])

    @classmethod
    def can_execute_exit(cls, symbol, position, metadata, reason, price, *,
                         frame=None, is_bar_closed=None, now=None) -> bool:
        """True only for an authoritative ALLOW; HOLD and DEFER both return False."""
        return cls.evaluate(symbol, position, metadata, reason, price, frame=frame,
                            is_bar_closed=is_bar_closed, now=now).allowed

    @classmethod
    def check_waterfall_emergency_exit(
        cls,
        symbol: str,
        position: Mapping[str, Any],
        metadata: Mapping[str, Any] | None,
        price: float,
        *,
        frame: Any = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Authorize a live 1.5-ATR adverse body crossing the KC middleline,
        regardless of KC or macro trend alignment.
        """
        from core.services.exits.abnormal_candle_exit import live_adverse_waterfall_evidence

        evidence = live_adverse_waterfall_evidence(position, frame, price)
        if evidence is None:
            return False, "NO_WATERFALL_CRITERIA_MET", {"symbol": symbol, "quote": price}
        evidence["symbol"] = symbol
        return True, "EXIT_BY_EXTREME_WATERFALL", evidence

    @classmethod
    def structure_exit_candidate(cls, symbol, position, metadata, price, frame, *, now=None):
        """Exit producer for the main loop: reason string when the gate itself authorizes."""
        verdict = cls.evaluate(symbol, position, metadata, "STRUCTURE_EXIT_PROBE", price,
                               frame=frame, now=now)
        return verdict.code if verdict.allowed else None

    @classmethod
    def sharp_reversal_exit_candidate(cls, position, frame, metadata=None, *, now=None):
        """Return a close reason for a fresh, completed 1m reversal body of at least 1.5 ATR."""
        try:
            closed = closed_entry_candles(frame)
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError, IndexError):
            return None
        if len(closed) < 2:
            return None
        current_time = time.time() if now is None else float(now)
        signal = _sharp_reversal_signal(
            str((position or {}).get("side") or "").upper(), closed,
        )
        opened = _float((position or {}).get("open_timestamp"))
        if opened is None:
            opened = _float((metadata or {}).get("open_timestamp"))
        stamp = _float(closed.iloc[-1].get("timestamp"))
        if signal is None or opened is None or stamp is None:
            return None
        bar_minute = _minute(stamp)
        if (
            bar_minute != int(current_time // 60) - 1
            or bar_minute <= _minute(opened)
        ):
            return None
        return SHARP_REVERSAL_EXIT_REASONS[signal["side"]]

    @classmethod
    def ma5_angle_entry_candidate(cls, frame, quote, *, now=None):
        """Return the side, angle and candle identity for a fresh live 1m MA5 impulse."""
        current_time = time.time() if now is None else float(now)
        signal = _ma5_angle_signal(frame, quote, current_time)
        if signal is None:
            return None
        return signal["side"], signal["angle"], signal["candle_timestamp_ms"]

    @classmethod
    def ma5_angle_exit_candidate(cls, position, frame, metadata=None, *, quote, now=None):
        """Return an opposite-side MA5 angle exit only after the position opened."""
        angle_candidate = cls.ma5_angle_entry_candidate(frame, quote, now=now)
        if angle_candidate is None:
            return None
        opened = _float((position or {}).get("open_timestamp"))
        if opened is None:
            opened = _float((metadata or {}).get("open_timestamp"))
        stamp = _float(frame.iloc[-1].get("timestamp")) if isinstance(frame, pd.DataFrame) and len(frame) else None
        if opened is None or stamp is None or _minute(stamp) <= _minute(opened):
            return None
        side = str((position or {}).get("side") or "").upper()
        if angle_candidate[0] == ("SHORT" if side == "LONG" else "LONG"):
            return MA5_ANGLE_REVERSAL_EXIT_REASONS.get(side)
        return None

    @classmethod
    def kc_middle_slope_exit_candidate(cls, position, frame, metadata=None, *, now=None):
        """Return a slope-reversal exit candidate based on the latest two closed KC values."""
        try:
            closed = closed_entry_candles(frame)
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError, IndexError):
            return None
        if len(closed) < 2:
            return None
        current_time = time.time() if now is None else float(now)
        current, previous = closed.iloc[-1], closed.iloc[-2]
        stamp = _float(current.get("timestamp"))
        middle = _float(current.get("kc_middle"))
        previous_middle = _float(previous.get("kc_middle"))
        close_px = _float(current.get("close"))
        open_px = _float(current.get("open"))
        atr_val = _float(previous.get("atr")) or _float(current.get("atr")) or 0.0
        opened = _float((position or {}).get("open_timestamp"))
        if opened is None:
            opened = _float((metadata or {}).get("open_timestamp"))
        if (
            stamp is None or middle is None or previous_middle is None or opened is None
            or close_px is None or open_px is None
            or _minute(stamp) != int(current_time // 60) - 1
            or _minute(stamp) <= _minute(opened)
        ):
            return None
        side = str((position or {}).get("side") or "").upper()
        slope = middle - previous_middle
        min_slope = 0.05 * atr_val if atr_val > 0 else 0.0
        if abs(slope) < min_slope and not math.isclose(abs(slope), min_slope, rel_tol=1e-9):
            return None
        if side == "LONG" and slope < 0 and (close_px > open_px or close_px < middle):
            return KC_MIDDLE_SLOPE_EXIT_REASONS["LONG"]
        if side == "SHORT" and slope > 0 and (close_px < open_px or close_px > middle):
            return KC_MIDDLE_SLOPE_EXIT_REASONS["SHORT"]
        return None

    @classmethod
    def ma5_angle_opportunity(cls, frame, quote, *, now=None):
        """Return a fresh live-bar direction/angle candidate for the shared entry funnel."""
        return cls.ma5_angle_entry_candidate(frame, quote, now=now)

    @classmethod
    def evaluate_two_point_pivot_flip(
        cls,
        position_side: Optional[str],
        close_price: float,
        ma5_curr: float,
        ma5_prev: float,
        *,
        is_bar_closed: bool = True,
    ) -> Tuple[bool, str, Optional[str]]:
        """
        ⚡ 真峰谷轉彎與 MA5/MA15 交叉 2 點秒平＋秒開/反手通道 (True Peak/Valley & MA Cross 2-Point Override):
        當 1m K 棒確定收線 (is_bar_closed == True) 時，100% 豁免大趨勢標頭與超賣/走平限制：
        1. 谷底轉彎 / 金叉成立 (close_price > ma5_curr 且 ma5_curr > ma5_prev):
           - 若持空單 (SHORT): 回傳 (True, "CLOSE_SHORT", "OPEN_LONG")
           - 若為空倉 (FLAT/NONE/""): 回傳 (True, "OPEN_LONG", "OPEN_LONG")
        2. 頂部轉彎 / 死叉成立 (close_price < ma5_curr 且 ma5_curr < ma5_prev):
           - 若持多單 (LONG): 回傳 (True, "CLOSE_LONG", "OPEN_SHORT")
           - 若為空倉 (FLAT/NONE/""): 回傳 (True, "OPEN_SHORT", "OPEN_SHORT")
        """
        if not is_bar_closed:
            return False, "HOLD", None
        side = str(position_side or "").upper()
        if side == "SHORT":
            if close_price > ma5_curr and ma5_curr > ma5_prev:
                return True, "CLOSE_SHORT", "OPEN_LONG"
        elif side == "LONG":
            if close_price < ma5_curr and ma5_curr < ma5_prev:
                return True, "CLOSE_LONG", "OPEN_SHORT"
        elif side in ("", "FLAT", "NONE"):
            if close_price > ma5_curr and ma5_curr > ma5_prev:
                return True, "OPEN_LONG", "OPEN_LONG"
            if close_price < ma5_curr and ma5_curr < ma5_prev:
                return True, "OPEN_SHORT", "OPEN_SHORT"
        return False, "HOLD", None


UltimateExitGatekeeper = UltimateShortExitGatekeeper
