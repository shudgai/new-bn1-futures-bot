"""Exchange market-data access and snapshot normalization."""

import asyncio
import time
from typing import Any

import pandas as pd

from core.services.candle_data import mark_candle_closure
from core.symbol_aliases import to_binance_usdm_symbol


def frame_from_ohlcv_snapshot(
    ohlcv: list,
    timeframe: str,
    keep_live: bool = False,
    snapshot_ms: float | None = None,
) -> pd.DataFrame:
    if not ohlcv:
        return pd.DataFrame()
    if snapshot_ms is None:
        snapshot_ms = time.time() * 1000
    frame = pd.DataFrame(
        ohlcv,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    frame = mark_candle_closure(frame, timeframe, snapshot_ms)
    if keep_live:
        return frame
    return frame.loc[frame["is_closed"]].reset_index(drop=True)


async def fetch_klines(
    exchange: Any,
    symbol: str,
    timeframe: str = "3m",
    limit: int = 100,
    keep_live: bool = False,
) -> pd.DataFrame:
    """Fetch OHLCV and attach candle finality at the pre-request snapshot time."""
    try:
        # Finality is fixed before I/O, including requests spanning a bar close.
        snapshot_ms = time.time() * 1000
        ohlcv = await asyncio.wait_for(
            exchange.fetch_ohlcv(
                to_binance_usdm_symbol(symbol),
                timeframe=timeframe,
                limit=limit,
            ),
            timeout=12.0,
        )
        return frame_from_ohlcv_snapshot(
            ohlcv, timeframe, keep_live=keep_live, snapshot_ms=snapshot_ms,
        )
    except Exception as exc:
        print(f"fetch_klines ERROR: {exc}")
        return pd.DataFrame()
