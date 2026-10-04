import math
from dataclasses import dataclass
from typing import Optional, Dict

_TIMEFRAME_MS = {'1m': 60_000, '3m': 180_000, '5m': 300_000, '15m': 900_000}
# Canonical entry strategy timeframe (single source for the expected candle interval).
CANONICAL_ENTRY_TIMEFRAME = '1m'
CANONICAL_ENTRY_INTERVAL_MS = _TIMEFRAME_MS[CANONICAL_ENTRY_TIMEFRAME]

@dataclass
class CanonicalSignal:
    symbol: str
    direction: str  # 'LONG' or 'SHORT'
    k2_timestamp: float  # Signal identity
    signal_code: str
    phase: str = 'FIRST'  # FIRST, MISSED_INITIAL, POST_EXIT_REENTRY
    state: str = 'NEW'  # NEW, WAIT_K4, READY, EXPIRED, CONSUMED


def evaluate_two_closed_kc_breakout(first_bar, second_bar, side: str,
                                    interval_ms: float = CANONICAL_ENTRY_INTERVAL_MS) -> str:
    """Canonical formation owner for two closed KC breakout.

    K1/K2 must be two CONSECUTIVE closed candles:
    K2.timestamp - K1.timestamp == interval_ms (gap, reversed, equal -> NO_SIGNAL).
    """
    if not first_bar.get('is_closed', True) or not second_bar.get('is_closed', True):
        return 'NO_SIGNAL'
    try:
        k1_ts, k2_ts = float(first_bar['timestamp']), float(second_bar['timestamp'])
    except (KeyError, TypeError, ValueError):
        return 'NO_SIGNAL'
    if not (math.isfinite(k1_ts) and math.isfinite(k2_ts)) or k2_ts - k1_ts != float(interval_ms):
        return 'NO_SIGNAL'
        
    for bar in (first_bar, second_bar):
        # Body and Span calculation
        open_p, close_p, high_p, low_p = bar['open'], bar['close'], bar['high'], bar['low']
        span = high_p - low_p
        
        # Check body/full_range >= 20%
        if span <= 0:
            return 'NO_SIGNAL'
        body_ratio = abs(close_p - open_p) / span
        if body_ratio < 0.20:
            return 'NO_SIGNAL'
            
        # Direction and Rail Breakout
        if side == 'LONG':
            if close_p <= open_p:
                return 'NO_SIGNAL'
            if close_p <= bar['kc_upper']:
                return 'NO_SIGNAL'
        elif side == 'SHORT':
            if close_p >= open_p:
                return 'NO_SIGNAL'
            if close_p >= bar['kc_lower']:
                return 'NO_SIGNAL'
                
    # Signal Confirmed
    if side == 'LONG':
        return 'TWO_CLOSED_KC_BREAKOUT_LONG'
    else:
        return 'TWO_CLOSED_KC_BREAKOUT_SHORT'


class CanonicalExecutionStateMachine:
    """Canonical execution state owner for K3/K4 lifecycle.

    State identity:
      slot key            = (symbol, direction)
      SIGNAL_CONFIRMATION_ID = CanonicalSignal.k2_timestamp (K1 = K2 - interval)
      EXECUTION_CANDLE_ID    = live bar timestamp; must equal K2 + 1*interval (K3)
                               or K2 + 2*interval (K4, only from WAIT_K4).
      These are two distinct identities.
    Any other live timestamp fails closed (EXPIRE), so an old K1/K2 can never
    authorize K5+ even if K3/K4 close observations were missed.
    """

    def __init__(self, interval_ms: float = CANONICAL_ENTRY_INTERVAL_MS):
        # Maps (symbol, direction) -> CanonicalSignal (one live slot per side)
        self.active_signals: Dict[tuple, CanonicalSignal] = {}
        self.interval_ms = float(interval_ms)

    def register_signal(self, symbol: str, direction: str, k2_timestamp: float, signal_code: str, phase: str = 'FIRST'):
        key = (symbol, direction)
        # Dedup: If the exact same k2_timestamp signal is already active/consumed, don't overwrite
        existing = self.active_signals.get(key)
        if existing and existing.k2_timestamp == k2_timestamp:
            return existing
        # Monotonic identity: an older K1/K2 can never replace a newer one (no resurrection)
        if existing and float(k2_timestamp) < float(existing.k2_timestamp):
            return None

        new_signal = CanonicalSignal(symbol, direction, k2_timestamp, signal_code, phase)
        self.active_signals[key] = new_signal
        return new_signal

    def consume_signal(self, symbol: str, direction: str, k2_timestamp: Optional[float] = None) -> bool:
        key = (symbol, direction)
        signal = self.active_signals.get(key)
        if signal is None:
            return False
        if k2_timestamp is not None and float(signal.k2_timestamp) != float(k2_timestamp):
            return False  # identity mismatch: never consume a different K1/K2
        signal.state = 'CONSUMED'
        return True

    def evaluate_live_candle(self, symbol: str, direction: str, live_bar,
                             k2_timestamp: Optional[float] = None) -> str:
        """
        Evaluates the K3/K4 live execution state.
        Returns the action ('READY', 'WAIT', 'EXPIRE').
        """
        key = (symbol, direction)
        if key not in self.active_signals:
            return 'WAIT'

        signal = self.active_signals[key]
        if k2_timestamp is not None and float(signal.k2_timestamp) != float(k2_timestamp):
            return 'WAIT'  # caller refers to a different K1/K2 identity
        if signal.state in ('EXPIRED', 'CONSUMED'):
            return 'WAIT'

        # Candle binding: live bar must be exactly K3 (NEW) or K4 (WAIT_K4)
        try:
            bar_ts = float(live_bar['timestamp'])
        except (KeyError, TypeError, ValueError):
            return 'WAIT'
        k3_ts = float(signal.k2_timestamp) + self.interval_ms
        k4_ts = k3_ts + self.interval_ms
        expected_ts = k3_ts if signal.state == 'NEW' else k4_ts if signal.state == 'WAIT_K4' else None
        if expected_ts is None:
            return 'WAIT'
        if bar_ts < expected_ts:
            return 'WAIT'
        if bar_ts > expected_ts:
            signal.state = 'EXPIRED'  # missed K3/K4 window: old signal cannot authorize later bars
            return 'EXPIRE'

        # Live Bar Math
        open_p, close_p, high_p, low_p = live_bar['open'], live_bar['close'], live_bar['high'], live_bar['low']
        span = high_p - low_p
        body_ratio = abs(close_p - open_p) / span if span > 0 else 0
        is_doji = body_ratio <= 0.10
        
        is_same_direction = False
        is_opposite = False
        if direction == 'LONG':
            is_same_direction = close_p > open_p
            is_opposite = close_p < open_p
        else:
            is_same_direction = close_p < open_p
            is_opposite = close_p > open_p
            
        is_closed = live_bar.get('is_closed', False)

        if signal.state == 'NEW':
            # This is K3
            if is_closed:
                if is_doji:
                    signal.state = 'WAIT_K4'
                    return 'WAIT'
                elif is_opposite:
                    signal.state = 'EXPIRED'
                    return 'EXPIRE'
                else: # same-direction non-doji but closed (meaning not executed during live)
                    signal.state = 'EXPIRED'
                    return 'EXPIRE'
            else:
                # Live K3
                if is_same_direction and not is_doji:
                    return 'READY'
                else: # entry doji or opposite
                    return 'WAIT'
                    
        elif signal.state == 'WAIT_K4':
            # This is K4
            if is_closed:
                signal.state = 'EXPIRED'
                return 'EXPIRE'
            else:
                if is_same_direction and not is_doji:
                    return 'READY'
                else:
                    return 'WAIT'
                    
        return 'WAIT'
