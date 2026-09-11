import math
import pandas as pd
from typing import Dict, Any, Optional
from core.services.strategies.outer_strategy import aligned_entry, ck_direction

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

def channel_mature_outer_trend_is_weak(frame: pd.DataFrame, side: str) -> bool:
    if frame is None or len(frame) < 5:
        return False
    recent = frame.iloc[-5:]
    vols = recent["volume"].astype(float).tolist()
    return vols[-1] < vols[-2] < vols[-3]

def channel_terminal_market(frame: pd.DataFrame) -> bool:
    return False

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
    **kwargs
) -> dict:
    """Use one MA3 outer-cross entry and position-aware execution exits."""
    if str(current_side or "").upper() in ("LONG", "SHORT"):
        return {"action": "HOLD", "side": None, "reason": "KC_POSITION_EXITS_MANAGED"}
    if channel_terminal_market(frame):
        return {
            "action": "WAIT",
            "side": None,
            "reason": "KC_TREND_END_WAIT",
        }
    decision = aligned_entry(frame, live_price)
    if check_profit_room and decision.get("action") == "ENTER":
        from core.services.rank_service import channel_profit_room
        room = channel_profit_room(frame, live_price, decision["side"])
        if not room["allowed"]:
            return {"action": "WAIT", "side": None, "reason": room["reason"]}
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
