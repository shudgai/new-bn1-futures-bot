"""Exchange market-data access and snapshot normalization."""

import asyncio
import time
from typing import Any

import pandas as pd

from core.services.candle_data import mark_candle_closure


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
            exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit),
            timeout=12.0,
        )
        frame = pd.DataFrame(
            ohlcv,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        frame = mark_candle_closure(frame, timeframe, snapshot_ms)
        if keep_live:
            return frame
        return frame.loc[frame["is_closed"]].reset_index(drop=True)
    except Exception as exc:
        print(f"fetch_klines ERROR: {exc}")
        return pd.DataFrame()
