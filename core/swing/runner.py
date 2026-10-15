"""Glue between the pure swing core and the live engine (frames, positions, orders)."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional

import pandas as pd

from .entry import evaluate_entry
from .exits import evaluate_exit
from .models import Bar, Position, SwingState
from .reversal import evaluate_dump_reversal

logger = logging.getLogger("uvicorn.error")

HISTORY_BARS = 30
_REQUIRED = ("open", "high", "low", "close", "volume", "ma5", "ma15", "kc_upper", "kc_lower", "atr")


def frame_to_bars(closed: pd.DataFrame, limit: int = HISTORY_BARS) -> List[Bar]:
    """Closed candles -> Bars; any invalid row aborts (fail closed)."""
    if closed is None or closed.empty or any(c not in closed for c in _REQUIRED):
        return []
    rows = closed.tail(limit)
    bars: List[Bar] = []
    for _, r in rows.iterrows():
        try:
            vals = [float(r[c]) for c in _REQUIRED]
        except (TypeError, ValueError):
            return []
        if not all(math.isfinite(v) for v in vals) or vals[3] <= 0 or vals[9] < 0:
            return []
        bars.append(Bar(*vals, is_closed=True))
    return bars


def live_bar_from(frame: pd.DataFrame, quote: float) -> Optional[Bar]:
    """The still-forming candle with the latest quote folded in (None if unusable)."""
    try:
        if frame is None or frame.empty or bool(frame.iloc[-1].get("is_closed", True)):
            return None
        bars = frame_to_bars(frame.tail(1), limit=1)
        if not bars or not math.isfinite(quote) or quote <= 0:
            return None
        b = bars[0]
        return replace(b, close=quote, high=max(b.high, quote), low=min(b.low, quote), is_closed=False)
    except (KeyError, TypeError, ValueError):
        return None


def bars_held(position: Dict[str, Any], closed: pd.DataFrame) -> int:
    """Closed bars since the entry bar began (the entry bar itself counts once it closes)."""
    try:
        open_ts = float(position.get("open_timestamp"))
        tf_ms = float(closed.attrs.get("timeframe_ms", 60_000) or 60_000)
        if "timestamp" not in closed or not math.isfinite(open_ts):
            return 0
        open_ms = open_ts * 1000.0 if open_ts < 1e11 else open_ts
        entry_bar_start = open_ms - (open_ms % tf_ms)
        return int((pd.to_numeric(closed["timestamp"], errors="coerce") >= entry_bar_start).sum())
    except (TypeError, ValueError):
        return 0


def net_roes(position: Dict[str, Any], quote: float) -> Optional[tuple]:
    """(peak_net_roe_pct, current_net_roe_pct); peak persisted on the position."""
    from core.gates.holding_protection_gate import _net_roe_pct

    side = str(position.get("side", "")).upper()
    current = _net_roe_pct(position, quote, side)
    if current is None:
        return None
    entry = float(position.get("entry_price") or quote)
    best = (max(float(position.get("highest_price") or entry), quote) if side == "LONG"
            else min(float(position.get("lowest_price") or entry), quote))
    extreme = _net_roe_pct(position, best, side)
    peak = max(current, extreme if extreme is not None else current,
               float(position.get("swing_peak_roe") or current))
    position["swing_peak_roe"] = peak
    return peak, current


@dataclass
class SwingRunner:
    states: Dict[str, SwingState] = field(default_factory=dict)
    _last_close_ts: Dict[str, float] = field(default_factory=dict)
    _attempted_bar: Dict[str, float] = field(default_factory=dict)

    def state_for(self, symbol: str, closed: pd.DataFrame) -> SwingState:
        st = self.states.setdefault(symbol, SwingState())
        ts = self._last_close_ts.get(symbol)
        if ts is not None and "timestamp" in closed:
            st.bars_since_close = int((pd.to_numeric(closed["timestamp"], errors="coerce") > ts).sum())
        return st

    def record_close(self, symbol: str, side: str, bar_ts: float) -> None:
        self.states[symbol] = SwingState(last_close_side=side, bars_since_close=0)
        self._last_close_ts[symbol] = bar_ts

    def first_attempt_on_bar(self, symbol: str, bar_ts: float) -> bool:
        if self._attempted_bar.get(symbol) == bar_ts:
            return False
        self._attempted_bar[symbol] = bar_ts
        return True


async def process_symbol(engine, symbol, frame, quote, daily_halt, exit_only):
    from core.services.candle_data import closed_entry_candles

    runner = getattr(engine, "_swing_runner", None)
    if runner is None:
        runner = engine._swing_runner = SwingRunner()
    engine.account._swing_runner = runner

    if not hasattr(engine, "_channel_exit_frames"):
        engine._channel_exit_frames = {}
    engine._channel_exit_frames[symbol] = frame.copy()
    engine.account._channel_exit_frames = engine._channel_exit_frames

    closed = closed_entry_candles(frame)
    bars = frame_to_bars(closed)
    if not bars or "timestamp" not in closed:
        return [], []
    bar_ts = float(closed.iloc[-1]["timestamp"])
    account = engine.account

    position = account.positions.get(symbol)
    if position:
        side = str(position.get("side", "")).upper()
        held = bars_held(position, closed)
        roes = net_roes(position, quote)
        if roes is None:
            return [], []
        pos = Position(side, held, roes[0], roes[1])

        flip = evaluate_dump_reversal(side, bars) if held >= 1 else None
        decision = None if flip else evaluate_exit(
            pos, bars[-1], quote, live_bar=live_bar_from(frame, quote), prior_bars=bars,
        )
        if flip is None and decision is None:
            return [], []

        is_limit = bool(decision and decision.order_type == "LIMIT")
        reason = "Swing " + (decision.reason if decision else flip.mode)
        ok = await account.close_position(
            symbol, quote, reason, is_manual=True, is_limit=is_limit,
            limit_price=decision.limit_price if decision else None,
        )
        if ok and symbol not in account.positions:
            runner.record_close(symbol, side, bar_ts)
            if flip and not daily_halt and runner.first_attempt_on_bar(symbol, bar_ts):
                await engine.execute_dual_tf_entry(
                    symbol=symbol, frame=closed, price=quote, side=flip.side,
                    mode_name=f"SWING_{flip.mode}",
                    meta_dict={"side": flip.side, "reason": flip.mode, "bar_id": bar_ts},
                    entry_mode="WILD_SWING",
                )
        return [], []

    if exit_only or daily_halt or not math.isfinite(quote) or quote <= 0:
        return [], []
    if symbol in getattr(account, "closing_lock", set()) or symbol in getattr(account, "pending_limit_orders", {}):
        return [], []

    signal = evaluate_entry(bars, runner.state_for(symbol, closed))
    if signal is None or not runner.first_attempt_on_bar(symbol, bar_ts):
        return [], []
    if signal.mode == "BREAKOUT_MOMENTUM":
        from core.vision.chart_guard import evaluate_breakout_vision
        approved, reason = await evaluate_breakout_vision(bars, signal.side)
        if not approved:
            logger.info("VISION_VETO %s %s: %s", symbol, signal.side, reason)
            return [], []
    await engine.execute_dual_tf_entry(
        symbol=symbol, frame=closed, price=quote, side=signal.side,
        mode_name=f"SWING_{signal.mode}",
        meta_dict={"side": signal.side, "reason": signal.mode, "bar_id": bar_ts},
        entry_mode="WILD_SWING",
    )
    return [], []
