"""Entry v2 — isolated strategy (Phase 1, NOT wired into the engine).

Authority model
---------------
* 1M C2 is the sole entry authority.
* 5M has veto authority only (``context_5m.is_opposite_slope_veto``).
* A vetoed resume is destroyed; it is never stored as an executable ticket.
  Recovery requires a NEW closed 1M structural resume strictly after veto
  release.

Frozen C2 (LONG; SHORT is the exact mirror)
-------------------------------------------
Indicators (research math, ``scratch/simulate_entry_v2.py`` /
``scratch/run_oos_study.py``): SMA5, SMA15, KC middle = SMA20 of 1M closes.

1. Trend context  : MA5/MA15 cross bar —
                    prev MA5 <= prev MA15 AND MA5 > MA15.
2. Pullback       : later bar with MA5 > MA15, MA5 > prev MA5,
                    MA15 >= prev MA15, close > KC middle AND low <= MA5.
                    The most recent qualifying bar is the pullback candle.
3. Resume         : later CLOSED bar (not itself a pullback) with
                    close > pullback candle HIGH.
Structure invalid : MA5 <= MA15 on any closed bar after the context.
Pullback invalid  : (recovery only) low < the vetoed formation's pullback low.

Bar ids are 1M OPEN timestamps in ms; a bar is evaluated at its close
(``bar_id + 60_000``) which is also the 5M decision timestamp.

Restart policy (重整不開倉)
--------------------------
A new instance holds no state. The first evaluation of a symbol only sets
``startup_watermark_bar_id`` = open timestamp of the last fully CLOSED 1M bar
present at startup and returns nothing. Historical bars are context only.
Every candidate requires ``trend_context_bar_id > startup_watermark_bar_id``
and ``trend_context_bar_id > last_exit_bar_id``.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

import math

import pandas as pd

from core.services.context_5m import (
    LONG,
    ONE_MINUTE_MS,
    SHORT,
    Closed5mContext,
    build_closed_5m_table,
    is_opposite_slope_veto,
    select_closed_5m,
)

ENTRY_MODE = "ENTRY_V2_C2"

SEARCH = "SEARCH"
VETO_WAIT = "VETO_WAIT"
WAIT_FRESH_CONFIRM = "WAIT_FRESH_CONFIRM"

MIN_1M_BARS = 21  # SMA20 + one previous bar for slopes / cross


# --------------------------------------------------------------------------
# Data objects
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ClosedBar1m:
    """One CLOSED 1M bar with the frozen research indicators."""

    bar_id: int          # 1M open timestamp (ms)
    open: float
    high: float
    low: float
    close: float
    ma5: float
    ma15: float
    prev_ma5: float
    prev_ma15: float
    kc_mid: float
    atr14: float
    ma15_prev5: float

    @property
    def close_timestamp(self) -> int:
        return self.bar_id + ONE_MINUTE_MS

    @property
    def finite(self) -> bool:
        return all(
            math.isfinite(v)
            for v in (self.open, self.high, self.low, self.close, self.ma5,
                      self.ma15, self.prev_ma5, self.prev_ma15, self.kc_mid,
                      self.atr14, self.ma15_prev5)
        )


@dataclass(frozen=True)
class EntryV2Candidate:
    """Immutable Strategy -> Contract -> Execution candidate."""

    symbol: str
    side: str
    entry_mode: str
    trend_context_bar_id: int
    pullback_bar_id: int
    resume_bar_id: int
    candidate_created_bar_id: int
    decision_timestamp: int
    resume_close: float
    pullback_high: float
    pullback_low: float
    veto_5m_open_timestamp: int
    veto_5m_close_timestamp: int
    veto_release_bar_id: Optional[int] = None
    recovery_origin_bar_id: Optional[int] = None

    def __post_init__(self):
        if self.side not in (LONG, SHORT):
            raise ValueError("invalid side")
        if self.entry_mode != ENTRY_MODE:
            raise ValueError("invalid entry_mode")
        if not (self.resume_bar_id > self.pullback_bar_id > self.trend_context_bar_id):
            raise ValueError("causal order violated")
        if self.candidate_created_bar_id != self.resume_bar_id:
            raise ValueError("candidate must be created on its resume bar")
        if self.veto_5m_close_timestamp > self.decision_timestamp:
            raise ValueError("5M context not closed at decision time")
        recovered = self.recovery_origin_bar_id is not None
        if recovered != (self.veto_release_bar_id is not None):
            raise ValueError("recovery fields must be set together")
        if recovered:
            if not self.resume_bar_id > self.veto_release_bar_id:
                raise ValueError("recovered resume must follow veto release")
            if self.resume_bar_id == self.recovery_origin_bar_id:
                raise ValueError("recovered resume must not reuse origin")

    @property
    def is_recovered(self) -> bool:
        return self.recovery_origin_bar_id is not None


@dataclass
class _Formation:
    side: str
    trend_context_bar_id: int
    pullback_bar_id: Optional[int] = None
    pullback_high: float = math.nan
    pullback_low: float = math.nan


@dataclass
class _Recovery:
    """Non-executable recovery bookkeeping. Holds NO ticket/candidate."""

    origin_bar_id: int          # bar id of the destroyed (vetoed) resume
    origin_pullback_bar_id: int
    origin_pullback_extreme: float  # LONG: pullback low / SHORT: pullback high
    veto_release_bar_id: Optional[int] = None


@dataclass
class _SymbolState:
    startup_watermark_bar_id: int
    last_processed_bar_id: int
    last_exit_bar_id: Optional[int] = None
    state: str = SEARCH
    formation: Optional[_Formation] = None
    recovery: Optional[_Recovery] = None
    events: Deque[tuple] = field(default_factory=lambda: deque(maxlen=512))


# --------------------------------------------------------------------------
# Indicators (frozen research math)
# --------------------------------------------------------------------------
def compute_closed_1m_bars(frame: pd.DataFrame, decision_timestamp: int) -> List[ClosedBar1m]:
    """Closed 1M bars (``open + 60s <= decision``) with SMA5/15/20 indicators."""
    if frame is None or len(frame) == 0:
        return []
    df = frame[["timestamp", "open", "high", "low", "close"]].copy()
    df["timestamp"] = df["timestamp"].astype("int64")
    df = df[(df["timestamp"] + ONE_MINUTE_MS) <= int(decision_timestamp)]
    df = df.drop_duplicates(subset="timestamp", keep="last").sort_values("timestamp")
    if df.empty:
        return []
    close = df["close"].astype(float)
    ma5 = close.rolling(5).mean()
    ma15 = close.rolling(15).mean()
    kc_mid = close.rolling(20).mean()
    prev_ma5 = ma5.shift(1)
    prev_ma15 = ma15.shift(1)
    
    df["prev_close"] = close.shift(1)
    df["tr1"] = df["high"] - df["low"]
    df["tr2"] = (df["high"] - df["prev_close"]).abs()
    df["tr3"] = (df["low"] - df["prev_close"]).abs()
    df["tr"] = df[["tr1", "tr2", "tr3"]].max(axis=1)
    atr14 = df["tr"].rolling(14).mean()
    ma15_prev5 = ma15.shift(5)
    
    return [
        ClosedBar1m(int(t), float(o), float(h), float(l), float(c),
                    float(a), float(b), float(pa), float(pb), float(k), float(atr), float(m15_5))
        for t, o, h, l, c, a, b, pa, pb, k, atr, m15_5 in zip(
            df["timestamp"], df["open"], df["high"], df["low"], close,
            ma5, ma15, prev_ma5, prev_ma15, kc_mid, atr14, ma15_prev5)
    ]


def _is_cross(side: str, bar: ClosedBar1m) -> bool:
    if side == LONG:
        return bar.prev_ma5 <= bar.prev_ma15 and bar.ma5 > bar.ma15
    return bar.prev_ma5 >= bar.prev_ma15 and bar.ma5 < bar.ma15


def _structure_invalid(side: str, bar: ClosedBar1m) -> bool:
    return bar.ma5 <= bar.ma15 if side == LONG else bar.ma5 >= bar.ma15


def _is_pullback(side: str, bar: ClosedBar1m) -> bool:
    if side == LONG:
        return (bar.ma5 > bar.ma15 and bar.ma5 > bar.prev_ma5
                and bar.ma15 >= bar.prev_ma15 and bar.close > bar.kc_mid
                and bar.low <= bar.ma5)
    return (bar.ma5 < bar.ma15 and bar.ma5 < bar.prev_ma5
            and bar.ma15 <= bar.prev_ma15 and bar.close < bar.kc_mid
            and bar.high >= bar.ma5)


def _is_range(bar: ClosedBar1m) -> bool:
    if not (math.isfinite(bar.atr14) and math.isfinite(bar.ma15_prev5) and bar.atr14 > 0):
        return True
    sep = round(abs(bar.ma5 - bar.ma15) / bar.atr14, 6)
    slope = round(abs(bar.ma15 - bar.ma15_prev5) / bar.atr14, 6)
    return (sep < 0.30) and (slope < 0.20)


def _is_resume(side: str, bar: ClosedBar1m, f: _Formation) -> bool:
    if f.pullback_bar_id is None or bar.bar_id <= f.pullback_bar_id:
        return False
    return bar.close > f.pullback_high if side == LONG else bar.close < f.pullback_low


def _pullback_extreme_broken(side: str, bar: ClosedBar1m, r: _Recovery) -> bool:
    if side == LONG:
        return bar.low < r.origin_pullback_extreme
    return bar.high > r.origin_pullback_extreme


# --------------------------------------------------------------------------
# Strategy
# --------------------------------------------------------------------------
class EntryV2Strategy:
    """Deterministic, engine-free Entry v2 state machine."""

    def __init__(self):
        self._symbols: Dict[str, _SymbolState] = {}
        self.counters: Dict[str, int] = {
            "CANDIDATES": 0,
            "ORIGINAL_VETOED": 0,
            "RECOVERED_ENTRIES": 0,
            "CANCELLED_STRUCTURE_BREAK": 0,
            "CANCELLED_PULLBACK_BREAK": 0,
            "VETO_REACTIVATED": 0,
            "CONFIRM_DISCARDED_WHILE_VETO": 0,
            "STALE_CATCHUP_DISCARDED": 0,
        }

    # ---------------- lifecycle ----------------
    def initialize_symbol(self, symbol: str, frame: pd.DataFrame, startup_timestamp: int) -> Optional[int]:
        """Startup: watermark = last fully CLOSED 1M bar id. No entry possible."""
        bars = compute_closed_1m_bars(frame, startup_timestamp)
        if not bars:
            return None
        wm = bars[-1].bar_id
        self._symbols[symbol] = _SymbolState(startup_watermark_bar_id=wm,
                                             last_processed_bar_id=wm)
        self._log(symbol, wm, "STARTUP_WATERMARK", wm)
        return wm

    def on_exit(self, symbol: str, exit_timestamp: int) -> None:
        """Any exit destroys formation + recovery; exit bar id = containing 1M bar."""
        st = self._symbols.get(symbol)
        if st is None:
            return
        exit_bar_id = (int(exit_timestamp) // ONE_MINUTE_MS) * ONE_MINUTE_MS
        st.last_exit_bar_id = max(exit_bar_id, st.last_exit_bar_id or exit_bar_id)
        self._clear(st)
        self._log(symbol, exit_bar_id, "EXIT_RESET", exit_bar_id)

    def get_state(self, symbol: str) -> str:
        st = self._symbols.get(symbol)
        return st.state if st else SEARCH

    def startup_watermark(self, symbol: str) -> Optional[int]:
        st = self._symbols.get(symbol)
        return st.startup_watermark_bar_id if st else None

    def snapshot(self, symbol: str) -> dict:
        """Read-only diagnostic copy (no executable content exists to copy)."""
        st = self._symbols.get(symbol)
        if st is None:
            return {"state": SEARCH, "formation": None, "recovery": None}
        return {
            "state": st.state,
            "startup_watermark_bar_id": st.startup_watermark_bar_id,
            "last_exit_bar_id": st.last_exit_bar_id,
            "formation": dict(vars(st.formation)) if st.formation else None,
            "recovery": dict(vars(st.recovery)) if st.recovery else None,
        }

    def events(self, symbol: str) -> List[tuple]:
        st = self._symbols.get(symbol)
        return list(st.events) if st else []

    # ---------------- evaluation ----------------
    def evaluate(self, symbol: str, frame: pd.DataFrame, decision_timestamp: int) -> Optional[EntryV2Candidate]:
        """Process every newly CLOSED 1M bar in order.

        Returns a candidate only if it was created on the latest closed bar
        at ``decision_timestamp``; catch-up resumes are discarded as stale.
        """
        decision_timestamp = int(decision_timestamp)
        if symbol not in self._symbols:
            self.initialize_symbol(symbol, frame, decision_timestamp)
            return None
        st = self._symbols[symbol]
        bars = compute_closed_1m_bars(frame, decision_timestamp)
        if not bars:
            return None
        table = build_closed_5m_table(frame, decision_timestamp)
        latest_id = bars[-1].bar_id
        result: Optional[EntryV2Candidate] = None
        for bar in bars:
            if bar.bar_id <= st.last_processed_bar_id:
                continue
            ctx = select_closed_5m(table, bar.close_timestamp)
            cand = self.on_closed_bar(symbol, bar, ctx)
            if cand is not None:
                if bar.bar_id == latest_id:
                    result = cand
                else:
                    self.counters["STALE_CATCHUP_DISCARDED"] += 1
                    self._log(symbol, bar.bar_id, "STALE_CATCHUP_DISCARDED", cand.side)
        return result

    def on_closed_bar(self, symbol: str, bar: ClosedBar1m,
                      ctx: Optional[Closed5mContext]) -> Optional[EntryV2Candidate]:
        """Core transition for ONE closed 1M bar (deterministic)."""
        st = self._symbols.get(symbol)
        if st is None:
            raise RuntimeError("symbol not initialized; restart requires startup watermark")
        if bar.bar_id <= st.last_processed_bar_id:
            return None
        st.last_processed_bar_id = bar.bar_id
        if not bar.finite:
            return None
        if ctx is not None and ctx.close_timestamp > bar.close_timestamp:
            raise ValueError("5M context is not closed at this 1M decision time")

        f = st.formation

        # 1. Structure invalidation (any state).
        if f is not None and _structure_invalid(f.side, bar):
            if st.state != SEARCH:
                self.counters["CANCELLED_STRUCTURE_BREAK"] += 1
            self._log(symbol, bar.bar_id, "CANCEL_STRUCTURE", st.state)
            self._clear(st)
            f = None

        # 2. Pullback-extreme invalidation (recovery only).
        if f is not None and st.recovery is not None and _pullback_extreme_broken(f.side, bar, st.recovery):
            self.counters["CANCELLED_PULLBACK_BREAK"] += 1
            self._log(symbol, bar.bar_id, "CANCEL_PULLBACK", st.state)
            self._clear(st)
            f = None

        # 3. New trend context (only when no formation is active).
        if f is None:
            for side in (LONG, SHORT):
                if _is_cross(side, bar) and self._fresh_context(st, bar.bar_id):
                    st.formation = _Formation(side=side, trend_context_bar_id=bar.bar_id)
                    self._log(symbol, bar.bar_id, "TREND_CONTEXT", side)
                    break
            return None  # the context bar cannot also be pullback/resume


        side = f.side
        veto = is_opposite_slope_veto(side, ctx)
        blocked = veto is not False  # None (unavailable) fails closed

        # RANGE FILTER V1
        range_blocked = _is_range(bar)

        # 4. Veto transitions for recovery states.
        if st.state == VETO_WAIT and not blocked:
            st.state = WAIT_FRESH_CONFIRM
            st.recovery.veto_release_bar_id = bar.bar_id
            self._log(symbol, bar.bar_id, "VETO_RELEASE", side)
        elif st.state == WAIT_FRESH_CONFIRM and blocked:
            st.state = VETO_WAIT
            st.recovery.veto_release_bar_id = None
            self.counters["VETO_REACTIVATED"] += 1
            self._log(symbol, bar.bar_id, "VETO_REACTIVATED", side)

        # 5. Formation progression.
        if bar.bar_id > f.trend_context_bar_id and _is_pullback(side, bar):
            f.pullback_bar_id = bar.bar_id
            f.pullback_high = bar.high
            f.pullback_low = bar.low
            self._log(symbol, bar.bar_id, "PULLBACK", side)
            return None

        if not _is_resume(side, bar, f):
            return None

        # RANGE FILTER EVALUATION
        if range_blocked:
            self.counters.setdefault("RANGE_BLOCKED", 0)
            self.counters["RANGE_BLOCKED"] += 1
            self._log(symbol, bar.bar_id, "RANGE_BLOCKED", side)
            if st.state == WAIT_FRESH_CONFIRM:
                # Discard fresh confirmation, fallback to VETO_WAIT
                st.state = VETO_WAIT
                st.recovery.veto_release_bar_id = None
                self._log(symbol, bar.bar_id, "RANGE_BLOCKED_RECOVERY", side)
            else:
                # If SEARCH, discard candidate and wait for new pullback.
                # If VETO_WAIT, it was already discarded by veto, but we log RANGE_BLOCKED.
                f.pullback_bar_id = None
            return None

        if st.state == SEARCH:
            if blocked:
                self.counters["ORIGINAL_VETOED"] += 1
                extreme = f.pullback_low if side == LONG else f.pullback_high
                st.recovery = _Recovery(origin_bar_id=bar.bar_id,
                                        origin_pullback_bar_id=f.pullback_bar_id,
                                        origin_pullback_extreme=extreme)
                st.state = VETO_WAIT
                self._log(symbol, bar.bar_id, "VETO_DESTROY", side, veto)
                return None
            return self._emit(symbol, st, bar, ctx, recovered=False)

        if st.state == VETO_WAIT:
            # Confirmation while veto active: discarded, nothing stored.
            self.counters["CONFIRM_DISCARDED_WHILE_VETO"] += 1
            self._log(symbol, bar.bar_id, "CONFIRM_DISCARDED_WHILE_VETO", side)
            return None

        # WAIT_FRESH_CONFIRM
        if bar.bar_id <= st.recovery.veto_release_bar_id:
            self._log(symbol, bar.bar_id, "CONFIRM_ON_RELEASE_BAR_IGNORED", side)
            return None
        return self._emit(symbol, st, bar, ctx, recovered=True)

    # ---------------- internals ----------------
    def _fresh_context(self, st: _SymbolState, bar_id: int) -> bool:
        if bar_id <= st.startup_watermark_bar_id:
            return False
        # [2026-10-10] 移除同根平倉後鎖定，允許即時反手或重新進場 (Flip)
        # if st.last_exit_bar_id is not None and bar_id <= st.last_exit_bar_id:
        #     return False
        return True

    def _emit(self, symbol, st, bar, ctx, recovered: bool) -> Optional[EntryV2Candidate]:
        f = st.formation
        if not self._fresh_context(st, f.trend_context_bar_id):
            self._clear(st)
            return None
        rec = st.recovery
        cand = EntryV2Candidate(
            symbol=symbol,
            side=f.side,
            entry_mode=ENTRY_MODE,
            trend_context_bar_id=f.trend_context_bar_id,
            pullback_bar_id=f.pullback_bar_id,
            resume_bar_id=bar.bar_id,
            candidate_created_bar_id=bar.bar_id,
            decision_timestamp=bar.close_timestamp,
            resume_close=bar.close,
            pullback_high=f.pullback_high,
            pullback_low=f.pullback_low,
            veto_5m_open_timestamp=ctx.open_timestamp,
            veto_5m_close_timestamp=ctx.close_timestamp,
            veto_release_bar_id=rec.veto_release_bar_id if recovered else None,
            recovery_origin_bar_id=rec.origin_bar_id if recovered else None,
        )
        self.counters["CANDIDATES"] += 1
        if recovered:
            self.counters["RECOVERED_ENTRIES"] += 1
        # Consume the pullback; trend context stays (a new pullback is needed).
        st.state = SEARCH
        st.recovery = None
        f.pullback_bar_id = None
        f.pullback_high = math.nan
        f.pullback_low = math.nan
        self._log(symbol, bar.bar_id, "CANDIDATE_RECOVERED" if recovered else "CANDIDATE", f.side)
        return cand

    @staticmethod
    def _clear(st: _SymbolState) -> None:
        st.state = SEARCH
        st.formation = None
        st.recovery = None

    def _log(self, symbol: str, bar_id: int, event: str, *detail) -> None:
        st = self._symbols.get(symbol)
        if st is not None:
            st.events.append((bar_id, event) + tuple(detail))
