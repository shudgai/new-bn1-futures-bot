"""Conservative REST settlement checks at automatic order boundaries."""
import asyncio
import math

from core.services.candle_data import closed_entry_candles

SETTLEMENT_MS = 3000
READ_INTERVAL_SECONDS = 1.0


def settled_pair(first, second, server_ms):
    if first is None or second is None or first.empty or second.empty:
        return False
    try:
        a, b = closed_entry_candles(first), closed_entry_candles(second)
        if len(a) < 3 or len(b) < 3 or a.iloc[-1].timestamp != b.iloc[-1].timestamp:
            return False
        stamp = float(b.iloc[-1].timestamp)
        if not math.isfinite(server_ms) or not SETTLEMENT_MS <= server_ms-stamp-60000 < 60000:
            return False
        # Require an actual following candle in both responses, not a stale tail
        # relabelled closed merely because the local clock crossed a minute.
        if float(first.iloc[-1].timestamp) != stamp+60000 or float(second.iloc[-1].timestamp) != stamp+60000:
            return False
        keys = ['timestamp','open','high','low','close']
        return a[keys].reset_index(drop=True).equals(b[keys].reset_index(drop=True))
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        return False


async def fetch_settled_entry_frame(engine, symbol):
    # A pair can straddle a candle boundary, or the first server-time sample
    # can precede the 3s settlement threshold even though the second sample is
    # already settled. Retry a fresh pair once, measuring settlement after the
    # two independent candle reads. Never relabel an unsettled candle.
    for attempt in range(2):
        try:
            first = await engine.fetch_klines(
                symbol, timeframe='1m', limit=200, keep_live=True,
            )
            await asyncio.sleep(READ_INTERVAL_SECONDS)
            second = await engine.fetch_klines(
                symbol, timeframe='1m', limit=200, keep_live=True,
            )
            server_ms = float(await engine.exchange.fetch_time())
            if not settled_pair(first, second, server_ms):
                continue
            # Recheck after the second REST read, which may cross a minute
            # boundary. If so, retry instead of rejecting this scan outright.
            completed_ms = float(await engine.exchange.fetch_time())
            if (completed_ms < server_ms
                    or not settled_pair(first, second, completed_ms)):
                continue
            second = second.copy()
            second.attrs['entry_finality_verified'] = True
            second.attrs['entry_finality_server_ms'] = completed_ms
            return second
        except Exception:
            if attempt == 1:
                return None
    return None
