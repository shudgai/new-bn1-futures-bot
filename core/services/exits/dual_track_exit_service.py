"""Account compatibility adapter for tick peak trailing and initial hard stops."""
import math

from core.interfaces.exit_interface import IExitStrategy
from core.services.exit_service import initialize_chandelier

from core.services.exits.peak_trailing_exit import (
    POLICY, STATE_KEY, STATE_KEYS, channel_initial_stop_disabled, evaluate_peak_trailing,
)
from core.services.candle_data import closed_entry_candles
DUAL_TRACK_STATE_KEYS = list(STATE_KEYS)
SL_INIT_MULT = 1.5


def detect_climax_reversal(frame, prior_side):
    """Detect an extended 1m move ending in a completed opposite engulfing bar."""
    try:
        if prior_side not in ('LONG', 'SHORT') or frame is None or frame.empty:
            return None
        if frame.attrs.get('timeframe_ms', 60000) != 60000:
            return None
        closed = closed_entry_candles(frame)
        if len(closed) < 12:
            return None
        swing = closed.iloc[-12:]
        previous, reversal = closed.iloc[-2], closed.iloc[-1]
        timestamps = [float(value) for value in swing['timestamp']]
        if any(right - left != 60000 for left, right in zip(timestamps, timestamps[1:])):
            return None
        atr = float(previous['atr'])
        values = [atr]
        for _, row in swing.iterrows():
            values.extend(float(row[key]) for key in
                          ('timestamp', 'open', 'high', 'low', 'close',
                           'kc_lower', 'kc_upper'))
        if not all(math.isfinite(value) and value > 0 for value in values) or atr <= 0:
            return None
        for _, row in swing.iterrows():
            if (float(row['low']) > min(float(row['open']), float(row['close']))
                    or float(row['high']) < max(float(row['open']), float(row['close']))):
                return None

        po, pc = float(previous['open']), float(previous['close'])
        ro, rc = float(reversal['open']), float(reversal['close'])
        body_atr = abs(rc - ro) / atr
        if prior_side == 'LONG':
            engulf = pc > po and rc < ro and ro >= pc and rc <= po
            ma5 = float(reversal.get('ma5', float('nan')))
            ma5_broken = math.isfinite(ma5) and rc < ma5
            peak_idx = swing['high'].astype(float).idxmax()
            peak_bar = swing.loc[peak_idx]
            swing_high = float(peak_bar['high'])
            true_peak = (
                peak_idx in (previous.name, reversal.name)
                and swing_high >= float(peak_bar['kc_upper'])
            )
            prior_to_peak = swing.iloc[:swing.index.get_loc(peak_idx)]
            if prior_to_peak.empty:
                return None
            swing_low = float(prior_to_peak['low'].astype(float).min())
            extension_atr = (swing_high - swing_low) / atr
            climax_bar = engulf and ma5_broken and body_atr >= 0.8
            extended = true_peak and extension_atr >= 3.0
            stop = float(reversal['high']) + 0.2 * atr
            side = 'SHORT'
            reason = 'CLIMAX_REVERSAL_EXIT_TOP'
        else:
            engulf = pc < po and rc > ro and ro <= pc and rc >= po
            ma5 = float(reversal.get('ma5', float('nan')))
            ma5_broken = math.isfinite(ma5) and rc > ma5
            valley_idx = swing['low'].astype(float).idxmin()
            valley_bar = swing.loc[valley_idx]
            swing_low = float(valley_bar['low'])
            true_valley = (
                valley_idx in (previous.name, reversal.name)
                and swing_low <= float(valley_bar['kc_lower'])
            )
            prior_to_valley = swing.iloc[:swing.index.get_loc(valley_idx)]
            if prior_to_valley.empty:
                return None
            swing_high = float(prior_to_valley['high'].astype(float).max())
            extension_atr = (swing_high - swing_low) / atr
            climax_bar = engulf and ma5_broken and body_atr >= 0.8
            extended = true_valley and extension_atr >= 3.0
            stop = float(reversal['low']) - 0.2 * atr
            side = 'LONG'
            reason = 'CLIMAX_REVERSAL_EXIT_BOTTOM'
        if not climax_bar or not extended or stop <= 0:
            return None
        return dict(
            reason=reason, prior_side=prior_side, side=side,
            reversal_bar_id=float(reversal['timestamp']),
            reversal_open=ro, reversal_close=rc,
            reversal_high=float(reversal['high']),
            reversal_low=float(reversal['low']), atr=atr,
            body_atr=body_atr, extension_atr=extension_atr,
            extremum_price=(swing_high if prior_side == 'LONG' else swing_low),
            extremum_bar_id=float(peak_bar['timestamp'] if prior_side == 'LONG'
                                  else valley_bar['timestamp']),
            initial_sl=stop,
        )
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def is_true_climax_peak(frame):
    evidence = detect_climax_reversal(frame, 'LONG')
    return bool(evidence and evidence['reason'] == 'CLIMAX_REVERSAL_EXIT_TOP')


def is_true_climax_valley(frame):
    evidence = detect_climax_reversal(frame, 'SHORT')
    return bool(evidence and evidence['reason'] == 'CLIMAX_REVERSAL_EXIT_BOTTOM')


def doji_reversal_exit_reason(bars, side):
    """Return the bar-close reversal exit for two consecutive completed 1m bars."""
    if side not in ('LONG', 'SHORT') or not isinstance(bars, (list, tuple)) or len(bars) < 2:
        return None
    previous, current = bars[-2:]
    try:
        def value(bar, short, long):
            return float(bar.get(short, bar.get(long)))

        prev_ms = value(previous, 'ms', 'timestamp')
        curr_ms = value(current, 'ms', 'timestamp')
        po, ph, pl, pc = (value(previous, short, long) for short, long in
                          (('o', 'open'), ('h', 'high'), ('l', 'low'), ('c', 'close')))
        co, cc = (value(current, short, long) for short, long in
                  (('o', 'open'), ('c', 'close')))
        values = (prev_ms, curr_ms, po, ph, pl, pc, co, cc)
        if (not all(math.isfinite(item) for item in values)
                or any(item <= 0 for item in values)
                or curr_ms - prev_ms != 60_000
                or ph < max(po, pc) or pl > min(po, pc) or ph <= pl):
            return None
        doji_body_ratio = abs(pc - po) / (ph - pl + 1e-9)
        if doji_body_ratio > 0.35:
            return None
        if (side == 'LONG' and cc < co) or (side == 'SHORT' and cc > co):
            return 'EXIT_DOJI_REVERSAL_CONFIRMED'
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    return None


def evaluate_trend_exit_and_take_profit(position, closed, atr):
    """Retired candle-only adapter; no live quote means no exit authority."""
    return dict(should_exit=False, action='HOLD', reason='WAIT_LIVE_QUOTE')


def observe_breakeven(position: dict, price: float) -> dict:
    """Legacy helper to detect breakeven.

    The current implementation delegates to ``DualTrackExitStrategy`` which
    encapsulates the peak‑trailing logic.  If the strategy reports a result type
    of ``"BREAKEVEN"`` we consider the breakeven condition met.
    """
    # Use the existing strategy to evaluate the exit type.
    strategy = DualTrackExitStrategy()
    result_type = strategy.evaluate_exit(position, current_price=price)
    if result_type == "BREAKEVEN":
        return {"should_exit": True, "action": "BREAKEVEN", "reason": "BREAKEVEN_REACHED"}
    # No breakeven reached – keep holding.
    return {"should_exit": False, "action": "HOLD", "reason": "NO_BREAKEVEN"}



class DualTrackExitStrategy(IExitStrategy):
    def initialize_position(self, position, entry_price, atr):
        if channel_initial_stop_disabled(position):
            initialize_chandelier(position, entry_price, position['side'], atr,
                                 initial_stop_enabled=False)
            return
        sign = 1 if position['side'] == 'LONG' else -1
        position.update(entry_atr=float(atr), sl=entry_price-sign*SL_INIT_MULT*atr,
                        stop_loss=entry_price-sign*SL_INIT_MULT*atr, tp=0.)

    def evaluate_exit(self, position, frame=None, current_price=None, **kwargs):
        import time
        from core.config import TAKER_FEE_RATE, SLIPPAGE_PCT
        climax = detect_climax_reversal(frame, position.get('side'))
        if climax:
            return climax['reason']
        if current_price is None:
            return None
        result = evaluate_peak_trailing(position, current_price, kwargs.get('quote_ms', time.time()*1000),
                                        fee=TAKER_FEE_RATE, slippage=SLIPPAGE_PCT)
        return result['type'] if result else None

    def handle_post_exit_cleanup(self, position, exit_reason):
        position.pop(STATE_KEY, None)
        position.pop('has_broken_outer_band', None)
        position['cooldown_mode'] = 'NONE'
