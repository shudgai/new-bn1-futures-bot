import math
import pandas as pd
from typing import Dict, Any, Optional
from core.services.strategies.outer_strategy import (
    aligned_entry, ck_direction, v_bottom_shape, ma3_middle_cross_reset,
)

def significant_ma3_turn(position, frame, price):
    key = 'channel_significant_ma3_turn'
    try:
        side = position['side']
        opened = float(position['open_timestamp'])
        entry = float(position['entry_price'])
        identity = [side, opened, entry]
        state = position.get(key)
        if state and (state.get('identity') != identity or state.get('version') not in (2, 3)):
            position.pop(key, None)
            state = None
        if (position.get('channel_profit_protection') or {}).get('armed'):
            position.pop(key, None)
            return False
        if state and state.get('version') == 2:
            state.update(version=3, pending=False)
        if state and state.get('pending'):
            return True
        price = float(price)
        closes = [float(v) for v in frame['close'].iloc[-4:-1]]
        atr = float(frame.iloc[-2]['atr'])
        bar = float(frame.iloc[-1]['timestamp']) / 1000.
        if (side not in ('LONG', 'SHORT') or len(closes) != 3
                or not all(math.isfinite(v) and v > 0 for v in [opened, entry, price, atr, bar, *closes])
                or opened >= bar + 60):
            position.pop(key, None)
            return False
        closed_ma = sum(closes) / 3.
        ma = (sum(closes[-2:]) + price) / 3.
        if not state:
            position[key] = dict(identity=identity, version=3, extreme=ma, threshold=atr * .10,
                                 favorable=False, last_bar=bar, pending=False)
            return False
        if bar < state['last_bar']:
            return False
        state['last_bar'] = bar
        sign = 1 if side == 'LONG' else -1
        advance = sign * (ma - state['extreme'])
        if advance > 0:
            state['extreme'] = ma
            state['favorable'] = state['favorable'] or sign * (ma - closed_ma) > 0
        elif (state['favorable']
              and -sign * (price - closes[0]) / 3. >= state['threshold'] - abs(ma) * 1e-12
              and -advance >= state['threshold'] - abs(ma) * 1e-12):
            state['pending'] = True
            return True
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        position.pop(key, None)
    return False

def channel_macro_market_mode(symbol: str) -> str:
    return "TRENDING"

def channel_mature_outer_trend_is_weak(
    frame: pd.DataFrame, side: str,
) -> bool:
    """Reject a first outer touch when the directional run is already mature and weak."""
    required = {
        "close", "ma3", "ma15", "atr", "volume",
        "kc_upper", "kc_lower",
    }
    if frame is None or len(frame) < 5 or not required.issubset(frame.columns):
        return False
    requested = str(side or "").upper()
    if requested not in ("LONG", "SHORT"):
        return False
    try:
        closed = frame.iloc[:-1].tail(4)
        ma3 = closed["ma3"].astype(float).tolist()
        ma15 = closed["ma15"].astype(float).tolist()
        upper = closed["kc_upper"].astype(float).tolist()
        lower = closed["kc_lower"].astype(float).tolist()
    except (TypeError, ValueError, IndexError, KeyError):
        return False
    values = ma3 + ma15 + upper + lower
    if len(ma3) < 4 or not all(math.isfinite(value) for value in values):
        return False

    if requested == "LONG":
        aligned = all(fast > slow for fast, slow in zip(ma3, ma15))
        rail_steps = sum(
            now_upper > prior_upper and now_lower > prior_lower
            for prior_upper, now_upper, prior_lower, now_lower in zip(
                upper, upper[1:], lower, lower[1:],
            )
        )
        mature = aligned and rail_steps >= 2 and upper[-1] > upper[0]
    else:
        aligned = all(fast < slow for fast, slow in zip(ma3, ma15))
        rail_steps = sum(
            now_upper < prior_upper and now_lower < prior_lower
            for prior_upper, now_upper, prior_lower, now_lower in zip(
                upper, upper[1:], lower, lower[1:],
            )
        )
        mature = aligned and rail_steps >= 2 and lower[-1] < lower[0]
    if not mature:
        return False

    confirmed_frame = frame.iloc[:-1]
    # Invalid volume is not evidence of a terminal market.
    try:
        volumes = confirmed_frame["volume"].astype(float)
        mean = (float(confirmed_frame["vol_ma_20"].iloc[-1])
                if "vol_ma_20" in confirmed_frame else float(volumes.iloc[:-1].tail(20).mean()))
        # 2026-09-13 使用者：末端判定不可被「單根爆量」騙過（LAB 13:31 就是這樣溜進去的），
        # 改用最近 3 根已收線量能的中位數。
        recent = [float(v) for v in volumes.iloc[-3:]]
        if not all(math.isfinite(v) and v >= 0 for v in recent) or len(recent) < 3:
            return False
        latest = sorted(recent)[1]
        if not all(math.isfinite(v) for v in (latest, mean)) or latest < 0 or mean <= 0:
            return False
    except (TypeError, ValueError, KeyError, IndexError):
        return False
    return latest / mean < 1.50

def _special_long_body_aligned(frame, price):
    """當根是否為順向特例長K（即時破軌或長實體收在軌外）——特例K一律可開。"""
    try:
        from core.services.strategies.outer_strategy import live_body_breakout_side, long_body_side
        from core import config as _config
        if live_body_breakout_side(frame, price) in ("LONG", "SHORT"):
            return True
        limit = float(getattr(_config, "CHANNEL_LONG_BODY_ENTRY_ATR", 0.0) or 0.0)
        return bool(limit > 0 and long_body_side(frame, limit) in ("LONG", "SHORT"))
    except (AttributeError, KeyError, IndexError, TypeError, ValueError):
        return False


def channel_terminal_market(frame):
    """A mature weak run blocks entries on BOTH sides."""
    return any(channel_mature_outer_trend_is_weak(frame, side)
               for side in ("LONG", "SHORT"))

def record_channel_chop_event(symbol: str, chop_info: dict) -> None:
    pass

def record_channel_signal_event(symbol: str, reason: str, frame: pd.DataFrame) -> None:
    pass

def channel_chop_state(frame: pd.DataFrame) -> dict:
    if frame is None or len(frame) < 20:
        return {"detected": False, "clear_direction": True}
    kc_upper = float(frame["kc_upper"].iloc[-1])
    kc_lower = float(frame["kc_lower"].iloc[-1])
    bandwidth = (kc_upper - kc_lower) / float(frame["close"].iloc[-1]) if float(frame["close"].iloc[-1]) > 0 else 0
    detected = bandwidth < 0.015
    return {"detected": detected, "clear_direction": not detected}

def channel_chop_breakout_action(frame: pd.DataFrame, price: float) -> dict:
    if frame is None or frame.empty:
        return {"action": "WAIT", "side": None, "reason": "EMPTY_FRAME"}
    curr = frame.iloc[-1]
    if price > float(curr["kc_upper"]):
        return {"action": "ENTER", "side": "LONG", "reason": "KC_CHOP_BREAKOUT_LONG"}
    elif price < float(curr["kc_lower"]):
        return {"action": "ENTER", "side": "SHORT", "reason": "KC_CHOP_BREAKOUT_SHORT"}
    return {"action": "WAIT", "side": None, "reason": "CHOP_WAIT"}

def channel_entry_reuses_exit_bar(position: dict, frame: pd.DataFrame) -> bool:
    return False

def channel_peak_exit_reentry_blocked(symbol: str, side: str, frame: pd.DataFrame) -> bool:
    return False

def channel_peak_reversal_action(frame: pd.DataFrame, price: float, side: str) -> dict:
    return {"action": "HOLD", "side": side, "reason": "NO_PEAK_REVERSAL"}

def channel_entry_min_profit_ok(frame: pd.DataFrame, price: float, side: str) -> bool:
    return True

def channel_peak_exit_entry_gate(symbol: str, side: str, frame: pd.DataFrame, price: float) -> bool:
    return True

def channel_upper_red_short_reversal_allowed(position: dict, frame: pd.DataFrame, price: float) -> bool:
    return True

def channel_is_upper_red_peak_short(position: dict) -> bool:
    return False

def channel_exit_requests_rotation(reason: Optional[str]) -> bool:
    return False

def channel_slope_entry_gate(frame: pd.DataFrame, side: str) -> bool:
    return True

def channel_macro_continuation_entry_gate(frame: pd.DataFrame, side: str) -> bool:
    return True

def channel_closed_body_volume_gate(frame: pd.DataFrame) -> bool:
    return True

def channel_near_chop_entry_gate(frame: pd.DataFrame) -> bool:
    return True

def channel_chop_gate(frame: pd.DataFrame) -> bool:
    return True

def channel_ma3_outside(row: pd.Series, side: str) -> bool:
    ma3 = float(row["ma3"])
    if side == "LONG":
        return ma3 > float(row["kc_upper"])
    return ma3 < float(row["kc_lower"])

def channel_outer_half_space_hold(previous: pd.Series, current: pd.Series, side: str) -> bool:
    return True

def check_parabolic_reversal_exit(frame: pd.DataFrame, side: str, live_price: float) -> Optional[str]:
    return None

def channel_impulse_turn_allowed(frame: pd.DataFrame, side: str) -> bool:
    return True

def channel_ma15_convergence_is_gradual(frame: pd.DataFrame, side: str) -> bool:
    return True

def channel_outer_gap_expanding(frame: pd.DataFrame, side: str) -> bool:
    return True

def channel_trend_exit_reason(frame: pd.DataFrame, side: str, entry_width: float = 0.0) -> Optional[str]:
    return None

def channel_position_path(frame: pd.DataFrame, side: str, opened_at: float, state: dict = None) -> dict:
    return state or {}

def channel_impulse_first_turn(frame: pd.DataFrame, side: str, price: float) -> bool:
    return False

def channel_all_same_color_inside(frame: pd.DataFrame, side: str) -> bool:
    return False

def channel_closed_waves_falling(frame: pd.DataFrame, side: str = "LONG") -> bool:
    return False

def channel_swing_action(
    frame: pd.DataFrame, live_price: float, current_side: str | None = None,
    entry_turn_low: float | None = None, entry_turn_high: float | None = None,
    market_mode: str | None = None, position_open_timestamp: float | None = None,
    exit_net_profitable: bool = True, entry_kc_upper: float | None = None,
    entry_kc_lower: float | None = None, peak_pnl_pct: float = 0.0, bars_held: int = 0, entry_outer_chase: bool = False,
    profit_locked: bool = False, cross_timer_start: float = 0.0,
    allow_live_entry: bool = False,
    position_path: dict | None = None,
    outer_entry_only: bool = False,
    check_profit_room: bool = True,
    profit_reentry: bool = False,
    **kwargs
) -> dict:
    """Use one MA3 outer-cross entry and position-aware execution exits."""
    if str(current_side or "").upper() in ("LONG", "SHORT"):
        return {"action": "HOLD", "side": None, "reason": "KC_POSITION_EXITS_MANAGED"}
    # 2026-09-14 使用者：漲勢末端不能開倉（看了好幾筆都在末端開倉然後虧損）——特例K也一樣。
    if channel_terminal_market(frame):
        return {
            "action": "WAIT",
            "side": None,
            "reason": "KC_TREND_END_WAIT",
        }
    # 兩根同色確認只套用在全新第一筆；獲利重開與延續不套用。
    # 2026-09-13 使用者：特例K在谷底也要開倉（特例K一律直接進場）；
    # 重置規則（V 型谷底／MA3 中軌折返）只約束一般（非特例）的重開與延續。
    decision = aligned_entry(frame, live_price, require_second_body=not profit_reentry)
    return decision

def channel_ck_exit_reason(frame: pd.DataFrame, side: str) -> str | None:
    """Close a position when valid closed CK data is no longer clear for it."""
    if side not in ("LONG", "SHORT") or frame is None or len(frame) < 3:
        return None
    middle_key = "kc_middle" if "kc_middle" in frame.columns else "ema_20"
    required = {"kc_lower", middle_key, "kc_upper"}
    if not required.issubset(frame.columns):
        return None
    try:
        rows = frame.iloc[-3:-1]
        values = [
            (float(row["kc_lower"]), float(row[middle_key]), float(row["kc_upper"]))
            for _, row in rows.iterrows()
        ]
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return None
    if len(values) != 2 or any(
        not all(math.isfinite(value) and value > 0 for value in row)
        or not row[0] < row[1] < row[2]
        for row in values
    ):
        return None
    direction = ck_direction(frame)
    if direction == side:
        return None
    if direction in ("LONG", "SHORT"):
        return "KC_CK_DIRECTION_REVERSED_EXIT"
    return "KC_CK_DIRECTION_UNCLEAR_EXIT"


_UNCLEAR_BAR_KEY = "channel_ck_unclear_bar"


def channel_ck_exit_with_tolerance(
    frame: pd.DataFrame, side: str, position: dict
) -> str | None:
    """Disabled 2026-09-11: one flip of the CK middle rail no longer closes a position.

    Live sample: the CK reversal exit closed seven positions for -8.78 USDT, while the
    step ladder plus the crash guard would have ended the same seven at -5.12 USDT.
    Adding a two-bar confirmation was worse again (-19.16), so the exit is removed
    rather than tightened. Positions now end on the step ladder, the crash guard, the
    two-candle confirmation or the account hard stop. ``channel_ck_exit_reason`` is
    kept for diagnostics only.
    """
    if isinstance(position, dict):
        position.pop(_UNCLEAR_BAR_KEY, None)
    return None


def two_bar_structure_failure_exit(frame: pd.DataFrame, side: str) -> bool:
    return False

def adverse_kc_outer_breached(frame: pd.DataFrame, side: str, price: float) -> bool:
    return False

def confirmed_outer_reversal(frame: pd.DataFrame, side: str) -> bool:
    return False

def range_swing_reverse_side(side: str) -> str:
    return "SHORT" if side == "LONG" else "LONG"

def pivot_pullback_ready(frame: pd.DataFrame, side: str) -> bool:
    return True

def detect_strict_pivot_prealert(live_frame: pd.DataFrame) -> Optional[str]:
    return None


def volume_decay_exit_ready(
    frame: pd.DataFrame, side: str, net_profitable: bool = True,
    require_profit: bool = False,
) -> bool:
    """量能衰退＋MA3 一轉彎即平倉，避免讓虧損擴大。

    2026-09-12 使用者指定：不要求獲利，否則只是把虧損擴大。
    設 require_profit=True 可回復「只在獲利中才平倉」的舊行為。
    """
    try:
        if side not in ("LONG", "SHORT") or frame is None or len(frame) < 3:
            return False
        if require_profit and not net_profitable:
            return False
        values = [float(v) for v in frame["ma3"].iloc[-3:-1]]
        if not all(math.isfinite(v) for v in values):
            return False
        turned = values[-1] < values[-2] if side == "LONG" else values[-1] > values[-2]
        if not turned:
            return False
        from core.strategy import has_real_volume_decay
        return bool(has_real_volume_decay(frame, -1 if side == "LONG" else 1))
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


def ma3_middle_cross_against(frame: pd.DataFrame, side: str) -> bool:
    """MA3 穿越 KC 中軌且方向對持倉不利 → 趨勢反轉，平倉。

    2026-09-12 使用者要求：空單抱到趨勢翻多還不平，應該在 MA3 穿越中軌
    （圖上黑圈）時平倉，而不是一路抱下去。多單對稱（MA3 由上往下穿越中軌）。
    用已收線K比較，並要求價格也站在中軌的順向側，避免單根雜訊。
    """
    try:
        if side not in ("LONG", "SHORT") or frame is None or len(frame) < 3:
            return False
        key = "kc_middle" if "kc_middle" in frame.columns else "ema_20"
        required = {"ma3", key, "close"}
        if not required.issubset(frame.columns):
            return False
        ma3_previous = float(frame["ma3"].iloc[-3])
        ma3_latest = float(frame["ma3"].iloc[-2])
        middle_previous = float(frame[key].iloc[-3])
        middle_latest = float(frame[key].iloc[-2])
        price = float(frame["close"].iloc[-2])
        if not all(math.isfinite(v) for v in
                   (ma3_previous, ma3_latest, middle_previous, middle_latest, price)):
            return False
        if side == "SHORT":
            return (ma3_previous <= middle_previous and ma3_latest > middle_latest
                    and price > middle_latest)
        return (ma3_previous >= middle_previous and ma3_latest < middle_latest
                and price < middle_latest)
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False
