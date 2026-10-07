"""Independent closed outer-MA5 turn plus live price-boundary confirmation."""
import math

from core.services.candle_data import closed_entry_candles
from core.services.exits.ma5_outer_pivot_exit import closed_outer_ma5_turn

PHASE = 'MA5_OUTER_PIVOT_BREAK'
CODES = frozenset(PHASE + '_' + side for side in ('LONG', 'SHORT'))
EVIDENCE_KEYS = ('ma5_entry_pivot_ms', 'ma5_entry_confirmed_ms', 'ma5_entry_pivot',
                 'ma5_entry_pivot_rail', 'ma5_entry_confirmation', 'ma5_entry_boundary')


def evaluate_ma5_outer_pivot_entry(frame, quote, symbol=''):
    """Use only the latest closed turn and its immediately following live candle."""
    try:
        closed = closed_entry_candles(frame)
        if len(closed) < 3 or len(frame) != len(closed) + 1:
            return None
        rows = closed.iloc[-3:]
        stamps = [float(v) for v in rows.timestamp]
        live = float(frame.iloc[-1].timestamp)
        values = rows[['open', 'high', 'low', 'close', 'ma5', 'kc_lower', 'kc_upper']].astype(float).to_numpy()
        quote = float(quote)
        scale = float(closed.iloc[-1].atr)
        if (not all(math.isfinite(v) and v > 0 for v in
                    [quote, scale, live, *stamps, *values.flat])
                or any(v % 60000 != 0 for v in [live, *stamps])
                or any(b-a != 60000 for a, b in zip(stamps, stamps[1:]))
                or live-stamps[-1] != 60000):
            return None
        for opened, high, low, close, _, lower, upper in values:
            if not low <= min(opened, close) <= max(opened, close) <= high or high <= low or lower >= upper:
                return None
        for side, pivot_sign in (('SHORT', 1), ('LONG', -1)):
            _, evidence = closed_outer_ma5_turn(values, pivot_sign)
            if evidence is None:
                continue
            sign = -pivot_sign
            boundary = float(values[1][2 if side == 'SHORT' else 1])
            if sign*(quote-boundary) <= max(quote, boundary)*1e-12:
                continue
            code = PHASE + '_' + side
            return dict(action='ENTER', side=side, type=code, reason=code, price=quote,
                        entry_atr=scale, confirmation_bar_id=live,
                        close_price=float(closed.iloc[-1].close), intrabar=True,
                        entry_phase=PHASE, breakout_bar_id=stamps[1],
                        pair_confirmation_bar_id=stamps[2], third_bar_id=live,
                        pending_signal_id=f'{symbol}_{PHASE}_{int(stamps[1])}_{int(stamps[2])}_{int(live)}_{side}',
                        ma5_entry_pivot_ms=stamps[1], ma5_entry_confirmed_ms=stamps[2],
                        ma5_entry_pivot=evidence['pivot_ma5'],
                        ma5_entry_pivot_rail=evidence['pivot_rail'],
                        ma5_entry_confirmation=evidence['confirmation_ma5'],
                        ma5_entry_boundary=boundary)
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None
