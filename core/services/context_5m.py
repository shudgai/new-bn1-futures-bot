"""Canonical CLOSED 5M context for Entry v2 (veto-only authority).

Timestamp domain (all integers, milliseconds since epoch, UTC):

* 1M bar id      = 1M OPEN timestamp. A 1M bar is CLOSED at
                   ``open + 60_000`` and is only usable when
                   ``open + 60_000 <= decision_timestamp``.
* 5M open        = ``floor(1m_open / 300_000) * 300_000``.
* 5M close       = ``5m_open + 300_000``.
* Visible 5M     = the latest bucket whose ``close_timestamp <=
                   decision_timestamp``. A forming bucket is never returned.

Indicator math is identical to the frozen research script
(``scratch/run_oos_study.py``): simple rolling MA5 / MA15 over the 5M closes
of all non-empty buckets, ``prev_ma5`` = MA5 of the previous non-empty bucket.

Only 1M bars that are themselves closed at ``decision_timestamp`` are
aggregated, so no forming 1M or 5M data can contaminate the result.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import Optional, Sequence

import math

import pandas as pd

ONE_MINUTE_MS = 60_000
FIVE_MINUTES_MS = 300_000
BARS_PER_5M = FIVE_MINUTES_MS // ONE_MINUTE_MS

LONG = "LONG"
SHORT = "SHORT"


@dataclass(frozen=True)
class Closed5mContext:
    """Latest fully CLOSED 5M candle visible at ``decision_timestamp``."""

    decision_timestamp: int
    open_timestamp: int          # 5m_open_timestamp
    close_timestamp: int         # 5m_close_timestamp (= open + 300_000)
    open: float
    high: float
    low: float
    close: float
    bar_count: int               # number of 1M bars aggregated (5 = complete)
    ma5: float
    ma15: float
    prev_open_timestamp: Optional[int]
    prev_close_timestamp: Optional[int]
    prev_ma5: float

    @property
    def complete(self) -> bool:
        return self.bar_count == BARS_PER_5M

    @property
    def usable(self) -> bool:
        """True only if every value needed by the veto is finite and the
        candle is complete and causally closed."""
        return (
            self.complete
            and self.close_timestamp <= self.decision_timestamp
            and self.prev_close_timestamp is not None
            and all(math.isfinite(v) for v in (self.ma5, self.ma15, self.prev_ma5))
        )


@dataclass(frozen=True)
class Closed5mTable:
    """Pre-aggregated closed 5M buckets (sorted by close timestamp).

    Built once per evaluation so that per-1M-bar selection is O(log n);
    selection semantics are identical to :func:`get_closed_5m_context`.
    """

    open_ts: Sequence[int]
    close_ts: Sequence[int]
    open: Sequence[float]
    high: Sequence[float]
    low: Sequence[float]
    close: Sequence[float]
    bar_count: Sequence[int]
    ma5: Sequence[float]
    ma15: Sequence[float]
    prev_ma5: Sequence[float]
    built_as_of: int


def _closed_1m(frame: pd.DataFrame, decision_timestamp: int) -> pd.DataFrame:
    if frame is None or len(frame) == 0:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close"])
    ts = frame["timestamp"].astype("int64")
    mask = (ts + ONE_MINUTE_MS) <= int(decision_timestamp)
    out = frame.loc[mask, ["timestamp", "open", "high", "low", "close"]].copy()
    out["timestamp"] = out["timestamp"].astype("int64")
    out = out.drop_duplicates(subset="timestamp", keep="last").sort_values("timestamp")
    return out


def build_closed_5m_table(frame: pd.DataFrame, decision_timestamp: int) -> Closed5mTable:
    """Aggregate CLOSED 1M bars (as of ``decision_timestamp``) into 5M buckets."""
    decision_timestamp = int(decision_timestamp)
    closed = _closed_1m(frame, decision_timestamp)
    if closed.empty:
        empty: list = []
        return Closed5mTable(empty, empty, empty, empty, empty, empty, empty,
                             empty, empty, empty, decision_timestamp)

    closed["open_5m"] = (closed["timestamp"] // FIVE_MINUTES_MS) * FIVE_MINUTES_MS
    grouped = closed.groupby("open_5m", sort=True).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        bar_count=("timestamp", "count"),
    )
    grouped["ma5"] = grouped["close"].rolling(5).mean()
    grouped["ma15"] = grouped["close"].rolling(15).mean()
    grouped["prev_ma5"] = grouped["ma5"].shift(1)

    open_ts = [int(v) for v in grouped.index]
    return Closed5mTable(
        open_ts=open_ts,
        close_ts=[v + FIVE_MINUTES_MS for v in open_ts],
        open=grouped["open"].astype(float).tolist(),
        high=grouped["high"].astype(float).tolist(),
        low=grouped["low"].astype(float).tolist(),
        close=grouped["close"].astype(float).tolist(),
        bar_count=grouped["bar_count"].astype(int).tolist(),
        ma5=grouped["ma5"].astype(float).tolist(),
        ma15=grouped["ma15"].astype(float).tolist(),
        prev_ma5=grouped["prev_ma5"].astype(float).tolist(),
        built_as_of=decision_timestamp,
    )


def select_closed_5m(table: Closed5mTable, decision_timestamp: int) -> Optional[Closed5mContext]:
    """Latest bucket with ``close_timestamp <= decision_timestamp``."""
    decision_timestamp = int(decision_timestamp)
    if decision_timestamp > table.built_as_of:
        # A table built earlier cannot prove later buckets are complete.
        raise ValueError("decision_timestamp is later than the table build time")
    idx = bisect_right(table.close_ts, decision_timestamp) - 1
    if idx < 0:
        return None
    close_ts = table.close_ts[idx]
    if close_ts > decision_timestamp:  # defensive; bisect guarantees otherwise
        return None
    prev_open = table.open_ts[idx - 1] if idx >= 1 else None
    return Closed5mContext(
        decision_timestamp=decision_timestamp,
        open_timestamp=table.open_ts[idx],
        close_timestamp=close_ts,
        open=table.open[idx],
        high=table.high[idx],
        low=table.low[idx],
        close=table.close[idx],
        bar_count=table.bar_count[idx],
        ma5=table.ma5[idx],
        ma15=table.ma15[idx],
        prev_open_timestamp=prev_open,
        prev_close_timestamp=(prev_open + FIVE_MINUTES_MS) if prev_open is not None else None,
        prev_ma5=table.prev_ma5[idx],
    )


def get_closed_5m_context(frame: pd.DataFrame, decision_timestamp: int) -> Optional[Closed5mContext]:
    """Canonical helper: latest fully CLOSED 5M candle at ``decision_timestamp``."""
    table = build_closed_5m_table(frame, decision_timestamp)
    return select_closed_5m(table, decision_timestamp)


def is_opposite_slope_veto(side: str, ctx: Optional[Closed5mContext]) -> Optional[bool]:
    """Frozen 5M opposite-slope veto.

    LONG  veto: MA5 < MA15 AND MA5 < previous closed MA5
    SHORT veto: MA5 > MA15 AND MA5 > previous closed MA5

    Returns ``None`` when the context is missing/incomplete/non-finite; the
    caller must treat ``None`` as NOT cleared (fail closed).
    """
    if ctx is None or not ctx.usable:
        return None
    if side == LONG:
        return ctx.ma5 < ctx.ma15 and ctx.ma5 < ctx.prev_ma5
    if side == SHORT:
        return ctx.ma5 > ctx.ma15 and ctx.ma5 > ctx.prev_ma5
    raise ValueError(f"unknown side: {side!r}")
