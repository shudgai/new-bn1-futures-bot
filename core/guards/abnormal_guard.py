"""Abnormal market guard and emergency adverse waterfall exit helpers."""

import math
import pandas as pd
from typing import Dict, Any, Optional

from core.config import RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR, CHANNEL_WATERFALL_BODY_ATR
from core.services.strategies.outer_strategy import (
    ck_direction, ck_entry_momentum_ready, live_ma3_direction_ready,
    live_adverse_entry_safe,
)

def channel_adverse_exit_reason(
    frame: pd.DataFrame, side: str, price: float, atr: float
) -> Optional[str]:
    """Keep waterfalls and two closed adverse bodies; a single abnormal body holds."""
    if side not in ("LONG", "SHORT") or frame is None or len(frame) < 3:
        return None
    try:
        threshold = float(atr) * RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR
        if not math.isfinite(threshold) or threshold <= 0:
            return None
        direction = 1.0 if side == "SHORT" else -1.0
        adverse_live = direction * (float(price) - float(frame.iloc[-1]["open"]))
        bodies = [direction * (float(row["close"]) - float(row["open"]))
                  for _, row in frame.iloc[-3:-1].iterrows()]
        if not all(math.isfinite(body) for body in [adverse_live, *bodies]):
            return None
        waterfall_threshold = float(atr) * CHANNEL_WATERFALL_BODY_ATR
        if adverse_live >= waterfall_threshold:
            return "EMERGENCY_EXIT_LIVE_ADVERSE_WATERFALL"
        if bodies[-1] >= waterfall_threshold:
            return "EMERGENCY_EXIT_CLOSED_ADVERSE_WATERFALL"
        if all(body >= threshold for body in bodies):
            return "EMERGENCY_EXIT_2_CANDLE_ADVERSE"
    except (TypeError, ValueError, KeyError, IndexError):
        return None
    return None

def channel_live_ma3_turn_exit(
    position: dict, frame: pd.DataFrame, price: float
) -> bool:
    """Exit after an in-position MA3 impulse turns against the holding side."""
    if not isinstance(position, dict):
        return False
    if position.get("channel_live_ma3_turn_exit_pending"):
        return True
    try:
        side = str(position["side"]).upper()
        opened_at = float(position.get("open_timestamp") or 0.0)
        closes = [float(v) for v in frame["close"].iloc[-5:-1]]
        price = float(price)
        live_bar = float(frame.iloc[-1]["timestamp"]) / 1000.0
        if (side not in ("LONG", "SHORT") or len(closes) != 4
                or not all(math.isfinite(v) and v > 0 for v in [opened_at, live_bar, price, *closes])
                or opened_at >= live_bar + 60):
            return False
        sign = 1 if side == "LONG" else -1
        live_slope = sign * (price - closes[-3])
        observed = position.get("channel_ma3_turn_observed_bar") == live_bar
        if live_slope > 0:
            position["channel_ma3_turn_observed_bar"] = live_bar
        favorable = observed or (opened_at < live_bar and sign * (closes[-1] - closes[0]) > 0)
        return favorable and live_slope < 0
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False

def opposite_entry_releases(account: Any, symbol: str, frame: pd.DataFrame, price: float) -> bool:
    ticket = getattr(account, 'channel_profit_reentries', {}).get(symbol, {})
    if (symbol in account.positions or ticket.get('phase') != 'closed'
            or ticket.get('mode') != 'outer_cycle' or not ticket.get('requires_pullback')
            or ticket.get('side') not in ('LONG', 'SHORT')
            or ticket.get('close_reason') not in {
                'Channel Swing KC_LONG_LIVE_RED_LONG_EXIT',
                'Channel Swing KC_SHORT_LIVE_GREEN_LONG_EXIT',
                'Channel Swing EMERGENCY_EXIT_LIVE_ADVERSE_WATERFALL',
                'Channel Swing EMERGENCY_EXIT_CLOSED_ADVERSE_WATERFALL',
                'Channel Swing EMERGENCY_EXIT_2_CANDLE_ADVERSE',
                'Channel Swing EMERGENCY_EXIT_LIVE_ADVERSE_ABNORMAL',
            }):
        return False
    try:
        requested = float(ticket['close_requested_at_ms'])
        exited = float(ticket['exit_bar_id'])
        live = float(frame.iloc[-1]['timestamp'])
        confirmed = float(frame.iloc[-2]['timestamp'])
        if not all(math.isfinite(v) and v > 0 for v in (requested, exited, live, confirmed)):
            return False
        fills = [float(t.get('id') or 0) for t in getattr(account, 'trades', [])
                 if t.get('symbol') == symbol and t.get('action') == 'CLOSE_' + ticket['side']
                 and t.get('reason') == ticket['close_reason']]
        fills = [v for v in fills if math.isfinite(v) and v >= requested and v >= exited]
        if not fills:
            return False
        close_bar = math.floor(min(fills) / 60_000) * 60_000
        if live <= close_bar or not close_bar <= confirmed < live:
            return False
        side = 'SHORT' if ticket['side'] == 'LONG' else 'LONG'
        return (ck_direction(frame) == side and ck_entry_momentum_ready(frame, side)
                and live_ma3_direction_ready(frame, price, side)
                and live_adverse_entry_safe(frame, price, side))
    except (AttributeError, TypeError, ValueError, KeyError, IndexError, OverflowError):
        return False
