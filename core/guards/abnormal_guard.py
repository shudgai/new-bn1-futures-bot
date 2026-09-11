"""Abnormal market guard and emergency adverse waterfall exit helpers.
OOP AbnormalMarketGuard implementing IGuardRule.
"""

import math
import pandas as pd
from typing import Dict, Any, Optional, Tuple
from core.interfaces.guard_interface import IGuardRule

from core.config import (
    RAPID_PIVOT_IMMEDIATE_REVERSE_BODY_ATR, CHANNEL_WATERFALL_BODY_ATR,
    CHANNEL_ADVERSE_TWO_CANDLE_BODY_ATR, CHANNEL_SINGLE_ADVERSE_EXIT_ENABLED,
    CHANNEL_SINGLE_ADVERSE_EXIT_BODY_ATR,
)
from core.services.strategies.outer_strategy import (
    ck_direction, ck_entry_momentum_ready, live_ma3_direction_ready,
    live_adverse_entry_safe,
)

def channel_adverse_exit_reason(
    frame: pd.DataFrame, side: str, price: float, atr: float
) -> Optional[str]:
    """Crash-only waterfall plus a two-bar confirmation; ordinary single bars hold.

    Authorised 2026-09-11. The single-bar threshold moved from 1.5 to 2.5 ATR and
    the two-bar floor from 0.5 to 1.0 ATR: on the live 1m sample the old values
    cost about 63 USDT and pushed the worst trade to -20.25, while these values
    keep a crash guard for about 10 USDT of upside instead of about 25.
    """
    if side not in ("LONG", "SHORT") or frame is None or len(frame) < 3:
        return None
    try:
        threshold = float(atr) * CHANNEL_ADVERSE_TWO_CANDLE_BODY_ATR
        waterfall = float(atr) * CHANNEL_WATERFALL_BODY_ATR
        if (not math.isfinite(threshold) or threshold <= 0
                or not math.isfinite(waterfall) or waterfall <= 0):
            return None
        direction = 1.0 if side == "SHORT" else -1.0
        adverse_live = direction * (float(price) - float(frame.iloc[-1]["open"]))
        bodies = [direction * (float(row["close"]) - float(row["open"]))
                  for _, row in frame.iloc[-3:-1].iterrows()]
        if not all(math.isfinite(body) for body in [adverse_live, *bodies]):
            return None
        if adverse_live >= waterfall:
            return "EMERGENCY_EXIT_LIVE_ADVERSE_WATERFALL"
        if bodies[-1] >= waterfall:
            return "EMERGENCY_EXIT_CLOSED_ADVERSE_WATERFALL"
        if all(body >= threshold for body in bodies):
            return "EMERGENCY_EXIT_2_CANDLE_ADVERSE"
        if CHANNEL_SINGLE_ADVERSE_EXIT_ENABLED and len(frame) >= 3:
            rail_key = "kc_upper" if side == "LONG" else "kc_lower"
            if "ma3" in frame.columns and rail_key in frame.columns:
                ma3_before = float(frame.iloc[-3]["ma3"])
                rail_before = float(frame.iloc[-3][rail_key])
                ma3_last = float(frame.iloc[-2]["ma3"])
                rail_last = float(frame.iloc[-2][rail_key])
                # MA3 由持倉側外軌之外「轉進軌內」：趨勢已破，出現反向異常K立即平倉。
                was_outside = ma3_before < rail_before if side == "SHORT" else ma3_before > rail_before
                now_inside = ma3_last >= rail_last if side == "SHORT" else ma3_last <= rail_last
                single = float(atr) * CHANNEL_SINGLE_ADVERSE_EXIT_BODY_ATR
                finite = all(math.isfinite(v) for v in (ma3_before, rail_before, ma3_last, rail_last))
                if (was_outside and now_inside and finite
                        and max(adverse_live, bodies[-1]) >= single > 0):
                    return "EMERGENCY_EXIT_MA3_ENTERED_RAIL_ADVERSE_BAR"
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


class AbnormalMarketGuard(IGuardRule):
    """OOP Guard implementing IGuardRule for emergency adverse waterfall detection."""

    def check_permission(
        self,
        account: Any,
        symbol: str,
        side: str,
        frame: Optional[pd.DataFrame] = None,
        price: float = 0.0,
        **kwargs: Any
    ) -> Tuple[bool, str]:
        if frame is None or frame.empty or len(frame) < 3:
            return True, "DATA_INSUFFICIENT"
        atr = float(frame.iloc[-2].get("atr", 0.0))
        reason = channel_adverse_exit_reason(frame, side, price, atr)
        if reason:
            return False, reason
        return True, "PERMISSION_GRANTED"
