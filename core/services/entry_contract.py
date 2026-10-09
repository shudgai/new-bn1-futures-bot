"""Shared signal contract for scan, execution and account revalidation."""
import math

import numpy as np

from core.services.candle_data import closed_entry_candles
from core.services.strategies.outer_strategy import (
    ck_direction,
    live_ma3_direction_ready,
    live_candle_color_ready,
    live_adverse_entry_safe,
    ma3_outer_continuation_ready,
)
from core.services.kc_pending_entry import KC_PENDING_CODES, evaluate_kc_pending_entry

LONG_ENTRY_CODE = "CLOSED_BODY_BREAKOUT_LONG"
SHORT_ENTRY_CODE = "CLOSED_BODY_BREAKOUT_SHORT"
ENTRY_CODES = KC_PENDING_CODES
ENTRY_EVIDENCE_KEYS = (
    "kc_confirmation_edge", "pending_signal_id", "pending_second_bar_id",
    "pending_wait_bars", "pending_max_wait_bars", "breakout_bar_id",
    "pair_confirmation_bar_id", "third_bar_id",
)


def quote_beyond_side_outer_rail(frame, side, quote):
    """Fail closed unless the quote is strictly beyond its own KC entry rail."""
    try:
        if side not in ("LONG", "SHORT") or frame is None or frame.empty:
            return False
        lower = float(frame.iloc[-1]["kc_lower"])
        upper = float(frame.iloc[-1]["kc_upper"])
        quote = float(quote)
        if not all(math.isfinite(value) and value > 0 for value in (lower, upper, quote)):
            return False
        if lower >= upper:
            return False
        return quote > upper if side == "LONG" else quote < lower
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False


def evaluate_live_ma5_direction(frame, quote, side):
    """Require the quote-adjusted MA5 to move strictly in the entry direction."""
    try:
        if side not in ("LONG", "SHORT") or frame is None or len(frame) < 2:
            return None
        previous, latest = frame.iloc[-2], frame.iloc[-1]
        previous_ma5 = float(previous["ma5"])
        latest_ma5 = float(latest["ma5"])
        latest_close = float(latest["close"])
        quote = float(quote)
        values = (previous_ma5, latest_ma5, latest_close, quote)
        if not all(math.isfinite(value) and value > 0 for value in values):
            return None
        live_ma5 = latest_ma5 + (quote - latest_close) / 5.0
        if not math.isfinite(live_ma5):
            return None
        if (side == "LONG" and live_ma5 <= previous_ma5) or (
            side == "SHORT" and live_ma5 >= previous_ma5
        ):
            return None
        return [previous_ma5, live_ma5]
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def evaluate_continuation_entry(frame, quote, code=None, symbol: str = '', account=None):
    """Continuation entry for sustained trend outside the outer rail.

    Permits opening when a prior breakout was missed or after a position was closed,
    provided that the KC direction, live MA3 direction, live candle color, and outer band position
    remain consistently in favor of the trend.
    """
    try:
        side = ck_direction(frame)
        if not side:
            return None
        signal = 'KC_OUTSIDE_' + side
        if code is not None and code != signal:
            return None

        qual = getattr(account, 'breakout_qualification', {}).get(symbol)
        if not qual or qual.get('side') != side:
            return None

        stamp = float(frame.iloc[-1]['timestamp'])
        qualified_bar = float(qual['breakout_bar_id'])
        if not math.isfinite(qualified_bar) or qualified_bar <= 0 or stamp <= qualified_bar:
            return None
        quote = float(quote)
        atr = float(frame.iloc[-2]['atr'])
        if not all(math.isfinite(v) and v > 0 for v in (quote, atr, stamp)):
            return None
        sign = 1 if side == 'LONG' else -1

        # Check MA5 direction & non-flat slope (漲勢/跌勢)
        closes = [float(v) for v in frame['close'].iloc[-5:-1]]
        if len(closes) != 4 or not all(math.isfinite(v) and v > 0 for v in closes):
            return None
        if len(closes) == 4:
            live_ma5 = (sum(closes[-4:]) + quote) / 5.0
            last_ma5 = float(frame.iloc[-2]['ma5'])
            if not math.isfinite(last_ma5) or last_ma5 <= 0:
                return None
            ma5_slope = sign * (live_ma5 - last_ma5)
            if ma5_slope <= 0 or (atr > 0 and ma5_slope / atr < 0.01):
                return None

        if not live_candle_color_ready(frame, quote, side):
            return None
        if not live_adverse_entry_safe(frame, quote, side):
            return None
        if not live_ma3_direction_ready(frame, quote, side):
            return None
        if not ma3_outer_continuation_ready(frame, quote, side):
            return None

        edge = float(frame.iloc[-1]['kc_upper' if side == 'LONG' else 'kc_lower'])
        
        # [NEW GATE RULE: 必須在 kc 線及 ma5 外，若在 kc 內或 ma5 內就不要開倉]
        if sign * (quote - edge) <= 0:
            return None
        if sign * (quote - live_ma5) <= 0:
            return None

        distance = sign * (quote - edge) / atr
        if distance <= 0 or distance > 3.0:
            return None

        live = frame.iloc[-1]
        stamp = float(live['timestamp'])
        prev_stamp = float(frame.iloc[-2]['timestamp'])

        return dict(action='ENTER', side=side, type=signal, reason=signal,
                    price=quote, entry_atr=atr, confirmation_bar_id=stamp,
                    close_price=float(frame.iloc[-2]['close']), intrabar=True,
                    entry_phase='KC_CONTINUATION_ENTRY',
                    breakout_bar_id=stamp,
                    pair_confirmation_bar_id=prev_stamp,
                    third_bar_id=stamp,
                    pending_signal_id=f"{symbol}_CONTINUATION_{int(stamp)}_{side}",
                    pending_second_bar_id=prev_stamp,
                    pending_wait_bars=1, pending_max_wait_bars=1,
                    kc_confirmation_edge=edge,  # FIXED NameError
                    kc_distance_atr=distance,
                    qualification_signal_id=qual['pending_signal_id'],
                    kc_max_distance_atr=3.0)
    except Exception:
        return None

DOJI_BODY_RATIO = 0.10


def is_entry_doji(row, quote=None):
    """Strictly below 10% doji boundary; no standalone long-wick veto."""
    try:
        opening = float(row.open)
        closing = float(row.close if quote is None else quote)
        high, low = float(row.high), float(row.low)
        if not all(math.isfinite(v) and v > 0 for v in (opening, closing, high, low)):
            return True
        high, low = max(high, closing), min(low, closing)
        body, span = abs(closing-opening), high-low
        if span <= 0:
            return True
        threshold = DOJI_BODY_RATIO*span
        return body < threshold and not math.isclose(body, threshold, rel_tol=1e-12)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return True


def entry_doji_problem(closed, live, quote):
    # 無論漲勢或跌勢，只要出現十字線（走到後面時）就不要再開倉
    if is_entry_doji(live, quote):
        return 'BLOCKED_LIVE_DOJI'
    bars = [row for _, row in closed.tail(2).iterrows()]
    for row in bars:
        if is_entry_doji(row):
            return 'BLOCKED_CLOSED_DOJI'
    return None


def is_solid_push(row, side):
    """Require a finite directional body, independent of wick length."""
    try:
        opening, closing = float(row.open), float(row.close)
        if not all(math.isfinite(v) and v > 0 for v in (opening, closing)):
            return False
        if side == 'LONG':
            return closing > opening
        if side == 'SHORT':
            return closing < opening
        return False
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


def evaluate_entry_contract(frame, price=None, code=None, *, account=None,
                            symbol="", diagnostics=None):
    def reject(reason):
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics["reason"] = reason
        return None
    reject("WAIT_VALID_ENTRY_DATA")
    if code is not None and code not in ENTRY_CODES:
        return reject("BLOCKED_OBSOLETE_ENTRY_SIGNAL")
    if account is not None and symbol in getattr(account, "positions", {}):
        return reject("WAIT_EXISTING_POSITION")
    try:
        if frame is None or frame.empty or frame.attrs.get('timeframe_ms', 60000) != 60000:
            return None
        if 'is_closed' not in frame or not all(isinstance(v, (bool, np.bool_)) for v in frame.is_closed):
            return None
        # Rolling indicators legitimately have an unavailable leading prefix.
        # Trim only that prefix; never bridge missing data inside valid history.
        indicator_keys = ['atr', 'kc_upper', 'kc_middle', 'kc_lower', 'ma5', 'ma15']
        ready = frame[indicator_keys].notna().all(axis=1).to_numpy()
        valid_indices = np.flatnonzero(ready)
        if not len(valid_indices):
            return None
        frame = frame.iloc[int(valid_indices[0]):].copy()
        closed = closed_entry_candles(frame)
        if len(closed) < 2 or len(frame)-len(closed) not in (0, 1):
            return None
        keys = [
            'timestamp','open','high','low','close','kc_upper','kc_middle',
            'kc_lower','atr','ma5','ma15',
        ]
        values = frame[keys].astype(float)
        if not np.isfinite(values.to_numpy()).all() or not values.gt(0).all().all():
            return None
        if not values.timestamp.diff().dropna().eq(60000).all():
            return None
        if not ((values.low <= values[['open','close']].min(axis=1)) &
                (values.high >= values[['open','close']].max(axis=1)) &
                (values.kc_lower < values.kc_middle) & (values.kc_middle < values.kc_upper)).all():
            return None
        quote = float(frame.iloc[-1].close if price is None else price)
        if not math.isfinite(quote) or quote <= 0:
            return None
        live = frame.iloc[-1]
        latest = closed.iloc[-1]
        # Do not reopen in the candle of a successful close, even after restart.
        exit_bar = None
        for trade in getattr(account, 'trades', []):
            if trade.get('symbol') == symbol and trade.get('action') in ('CLOSE_LONG','CLOSE_SHORT'):
                stamp = float(trade['id'])
                if not math.isfinite(stamp) or stamp <= 0:
                    return reject('WAIT_VALID_CLOSE_HISTORY')
                bar = math.floor(stamp/60000)*60000
                exit_bar = max(exit_bar or bar, bar)
        saved_close = getattr(account, 'last_closed_at', {}).get(symbol)
        if saved_close is not None:
            saved_close = float(saved_close)
            if not math.isfinite(saved_close) or saved_close <= 0:
                return reject('WAIT_VALID_CLOSE_HISTORY')
            saved_bar = math.floor(saved_close/60)*60000
            exit_bar = max(exit_bar or saved_bar, saved_bar)
        decision = evaluate_kc_pending_entry(
            closed, quote, code=code, symbol=symbol, live=live
        )
        if decision.get("action") != "ENTER":
            return reject(decision.get("reason", "WAIT_KC_2BAR_BREAKOUT"))
        side = decision["side"]
        if not quote_beyond_side_outer_rail(frame, side, quote):
            return reject("WAIT_LIVE_PRICE_OUTSIDE_KC")
        if not live_candle_color_ready(frame, quote, side):
            return reject("BLOCKED_OPPOSITE_LIVE_CANDLE_COLOR")
        live_ma5_trend = evaluate_live_ma5_direction(
            frame, quote, side
        )
        if live_ma5_trend is None:
            return reject("WAIT_LIVE_MA5_DIRECTION")
        decision["live_ma5_trend_values"] = live_ma5_trend

        # [EMERGENCY GUARD: 無論漲勢或跌勢，出現十字線不要再開倉]
        doji_reject = entry_doji_problem(closed, live, quote)
        if doji_reject:
            return reject(doji_reject)

        # Strict live candle color guard: Never open Long on a red live candle, never open Short on a green live candle
        # Post-exit formation verification:
        if exit_bar is not None and float(live.timestamp) <= exit_bar:
            return reject('WAIT_POST_EXIT_NEW_FORMATION')
        if exit_bar is not None and decision.get('breakout_bar_id', 0) <= exit_bar:
            return reject('WAIT_POST_EXIT_NEW_FORMATION')
        # Persisted successful fills own deduplication, including after restart.
        for trade in getattr(account, 'trades', []):
            if (trade.get('symbol') == symbol and trade.get('action') in ('OPEN_LONG', 'OPEN_SHORT')
                    and (trade.get('entry_snapshot') or {}).get('pending_signal_id') == decision['pending_signal_id']):
                return reject('BLOCKED_KC_BREAKOUT_ALREADY_FILLED')
        decision['exit_bar_id'] = exit_bar
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics['reason'] = decision['type']
        return decision
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return reject('WAIT_VALID_ENTRY_DATA')
