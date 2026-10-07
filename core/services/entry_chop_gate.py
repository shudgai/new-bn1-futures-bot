"""Direction-independent completed-candle screening for every entry authority."""
import math
from decimal import Decimal

from core.services.candle_data import closed_entry_candles

EVIDENCE_KEYS = ('chop_start_ms', 'chop_end_ms', 'chop_bars',
                 'chop_efficiency', 'chop_middle_crosses', 'chop_mean_overlap')


def evaluate_entry_chop(frame):
    try:
        closed = closed_entry_candles(frame)
        if len(closed) < 6:
            return 'BLOCKED_CHOP_HISTORY', None
        rows = closed.iloc[-6:]
        keys = ('timestamp', 'open', 'high', 'low', 'close', 'kc_middle')
        values = [[float(row[key]) for key in keys] for _, row in rows.iterrows()]
        if not all(math.isfinite(v) and v > 0 for row in values for v in row):
            return 'BLOCKED_CHOP_MARKET_DATA', None
        stamps = [row[0] for row in values]
        live = float(frame.iloc[-1].timestamp)
        if (len(frame) != len(closed)+1 or not math.isfinite(live)
                or any(t % 60000 != 0 for t in [*stamps, live])
                or live-stamps[-1] != 60000
                or any(b-a != 60000 for a, b in zip(stamps, stamps[1:]))):
            return 'BLOCKED_CHOP_CANDLE_IDENTITY', None
        if any(not low <= min(o, c) <= max(o, c) <= high or high <= low
               for _, o, high, low, c, _ in values):
            return 'BLOCKED_CHOP_MARKET_DATA', None
        prices = [Decimal(str(row[4])) for row in values]
        path = sum(abs(b-a) for a, b in zip(prices, prices[1:]))
        net = abs(prices[-1]-prices[0])
        if path == 0 or net < Decimal('0.70')*path:
            return 'BLOCKED_CHOP_LOW_EFFICIENCY', None
        sides = [1 if c > middle else -1 if c < middle else 0
                 for _, _, _, _, c, middle in values]
        nonzero = [side for side in sides if side]
        crosses = sum(a != b for a, b in zip(nonzero, nonzero[1:]))
        if crosses > 1:
            return 'BLOCKED_CHOP_MIDDLE_CROSSES', None
        overlaps = []
        for a, b in zip(values, values[1:]):
            lo_a, hi_a = sorted(Decimal(str(v)) for v in (a[1], a[4]))
            lo_b, hi_b = sorted(Decimal(str(v)) for v in (b[1], b[4]))
            smaller = min(hi_a-lo_a, hi_b-lo_b)
            overlaps.append(max(Decimal(0), min(hi_a, hi_b)-max(lo_a, lo_b))/smaller
                            if smaller > 0 else Decimal(1))
        mean_overlap = sum(overlaps)/len(overlaps)
        if mean_overlap > Decimal('0.50'):
            return 'BLOCKED_CHOP_BODY_OVERLAP', None
        return 'PASS', dict(chop_start_ms=stamps[0], chop_end_ms=stamps[-1],
                            chop_bars=6, chop_efficiency=float(net/path),
                            chop_middle_crosses=crosses, chop_mean_overlap=float(mean_overlap))
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return 'BLOCKED_CHOP_MARKET_DATA', None
