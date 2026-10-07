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
    first = await engine.fetch_klines(symbol, timeframe='1m', limit=200, keep_live=True)
    server_ms = float(await engine.exchange.fetch_time())
    cache = getattr(engine, '_settled_closed_entry_evidence', None)
    if cache is None:
        cache = engine._settled_closed_entry_evidence = {}
    prior = cache.get(symbol)
    # Reuse only recently independently confirmed closed OHLC. The current
    # response and server time are fresh; live price/proof is never cached.
    if prior is not None:
        verified_ms = float(prior.attrs.get('entry_finality_server_ms') or 0)
        if (0 <= server_ms-verified_ms <= 5000 and settled_pair(prior,first,server_ms)
                and float(prior.iloc[-1].open) == float(first.iloc[-1].open)):
            first=first.copy()
            first.attrs['entry_finality_verified']=True
            first.attrs['entry_finality_server_ms']=server_ms
            first.attrs['entry_finality_closed_reused']=True
            # Keep the original confirmation time: reuse cannot extend its TTL.
            return first
    await asyncio.sleep(READ_INTERVAL_SECONDS)
    second = await engine.fetch_klines(symbol, timeframe='1m', limit=200, keep_live=True)
    if not settled_pair(first, second, server_ms):
        return None
    # Recheck after the second REST read, which may cross a minute boundary.
    completed_ms = float(await engine.exchange.fetch_time())
    if completed_ms < server_ms or not settled_pair(first, second, completed_ms):
        return None
    second = second.copy()
    second.attrs['entry_finality_verified'] = True
    second.attrs['entry_finality_server_ms'] = completed_ms
    cache[symbol] = second.copy()
    return second
