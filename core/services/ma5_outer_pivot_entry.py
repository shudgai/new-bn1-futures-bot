"""Sustained outer run, closed price pivot and MA5 reversal back inside KC."""
import math

from core.services.candle_data import closed_entry_candles
from core.services.exits.confirmed_pivot_exit import closed_price_pivot

PHASE = 'KC_OUTER_RUN_PRICE_PIVOT_RETURN'
MIN_OUTER_RUN_BARS = 10
CODES = frozenset(PHASE + '_' + side for side in ('LONG', 'SHORT'))
EVIDENCE_KEYS = ('ma5_entry_pivot_ms', 'ma5_entry_confirmed_ms', 'ma5_entry_pivot',
                 'ma5_entry_pivot_rail', 'ma5_entry_confirmation', 'ma5_entry_boundary',
                 'pivot_entry_price', 'outer_run_start_ms', 'outer_run_bars',
                 'pivot_return_lower', 'pivot_return_upper', 'outer_run_candles')


def evaluate_ma5_outer_pivot_entry(frame, quote, symbol=''):
    """Use only the latest closed turn and its immediately following live candle."""
    try:
        closed = closed_entry_candles(frame)
        if len(closed) < MIN_OUTER_RUN_BARS+1 or len(frame) != len(closed) + 1:
            return None
        rows = closed.iloc[-3:]
        stamps = [float(v) for v in rows.timestamp]
        live = float(frame.iloc[-1].timestamp)
        values = rows[['open', 'high', 'low', 'close', 'ma5', 'kc_lower', 'kc_upper']].astype(float).to_numpy()
        quote = float(quote)
        scale = float(closed.iloc[-1].atr)
        lower, upper = map(float, (frame.iloc[-1].kc_lower, frame.iloc[-1].kc_upper))
        if (not all(math.isfinite(v) and v > 0 for v in
                    [quote, scale, live, lower, upper, *stamps, *values.flat])
                or any(v % 60000 != 0 for v in [live, *stamps])
                or any(b-a != 60000 for a, b in zip(stamps, stamps[1:]))
                or live-stamps[-1] != 60000):
            return None
        if not lower < quote < upper:
            return None
        for opened, high, low, close, _, lower, upper in values:
            if not low <= min(opened, close) <= max(opened, close) <= high or high <= low or lower >= upper:
                return None
        for side, pivot_sign in (('SHORT', 1), ('LONG', -1)):
            pivot_price = closed_price_pivot(values, pivot_sign)
            if pivot_price is None:
                continue
            if pivot_sign*(values[1][4]-values[2][4]) <= max(values[1][4], values[2][4])*1e-12:
                continue
            run = closed.iloc[-(MIN_OUTER_RUN_BARS+1):-1]
            run_values = run[['timestamp', 'open', 'high', 'low', 'close', 'kc_lower', 'kc_upper']].astype(float).to_numpy()
            if not all(math.isfinite(v) and v > 0 for v in run_values.flat):
                continue
            if (any(row[0] % 60000 != 0 for row in run_values)
                    or any(b[0]-a[0] != 60000 for a, b in zip(run_values, run_values[1:]))):
                continue
            if any(not row[3] <= min(row[1], row[4]) <= max(row[1], row[4]) <= row[2]
                   or row[2] <= row[3] or row[5] >= row[6] for row in run_values):
                continue
            rail_key = 6 if pivot_sign == 1 else 5
            if (any(pivot_sign*(row[4]-row[rail_key]) <= 0 for row in run_values)
                    or any(pivot_sign*(b[4]-a[4]) <= 0
                           for a, b in zip(run_values, run_values[1:]))):
                continue
            boundary = float(values[1][2 if side == 'SHORT' else 1])
            code = PHASE + '_' + side
            return dict(action='ENTER', side=side, type=code, reason=code, price=quote,
                        entry_atr=scale, confirmation_bar_id=live,
                        close_price=float(closed.iloc[-1].close), intrabar=True,
                        entry_phase=PHASE, breakout_bar_id=stamps[1],
                        pair_confirmation_bar_id=stamps[2], third_bar_id=live,
                        pending_signal_id=f'{symbol}_{PHASE}_{int(stamps[1])}_{int(stamps[2])}_{int(live)}_{side}',
                        ma5_entry_pivot_ms=stamps[1], ma5_entry_confirmed_ms=stamps[2],
                        ma5_entry_pivot=float(values[1][4]),
                        ma5_entry_pivot_rail=float(values[1][6 if pivot_sign == 1 else 5]),
                        ma5_entry_confirmation=float(values[2][4]),
                        ma5_entry_boundary=boundary, pivot_entry_price=pivot_price,
                        outer_run_start_ms=float(run_values[0][0]),
                        outer_run_bars=MIN_OUTER_RUN_BARS,
                        outer_run_candles=[dict(timestamp=float(row[0]), close=float(row[4]),
                                                kc_lower=float(row[5]), kc_upper=float(row[6]))
                                           for row in run_values],
                        pivot_return_lower=lower, pivot_return_upper=upper)
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None
