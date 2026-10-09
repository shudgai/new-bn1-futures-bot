"""Shared signal contract for scan, execution and account revalidation."""
import math

import numpy as np

from core.services.candle_data import closed_entry_candles
from core.services.strategies.outer_strategy import (
    LIVE_BREAKOUT_BODY_ATR,
    LIVE_BREAKOUT_MAX_DISTANCE_ATR,
    ck_direction,
    live_body_breakout_side,
    ma5_ma15_trend_confirmed,
    live_ma3_direction_ready,
    live_candle_color_ready,
    live_adverse_entry_safe,
    ma3_outer_continuation_ready,
    outer_body_breakout_side,
)
from core.services.kc_pending_entry import (
    KC_PENDING_CODES,
    evaluate_kc_pending_entry,
)

LONG_ENTRY_CODE = "CLOSED_BODY_BREAKOUT_LONG"
SHORT_ENTRY_CODE = "CLOSED_BODY_BREAKOUT_SHORT"
CONTINUATION_CODES = frozenset(('KC_OUTSIDE_LONG', 'KC_OUTSIDE_SHORT'))
LIVE_BODY_BREAKOUT_CODES = frozenset((
    "KC_LIVE_BODY_BREAKOUT_LONG", "KC_LIVE_BODY_BREAKOUT_SHORT",
))
# Continuation requires a persisted, previously observed outer-rail breakout.
ENTRY_CODES = KC_PENDING_CODES | LIVE_BODY_BREAKOUT_CODES | CONTINUATION_CODES
SUI_BREAKOUT_ONLY_SYMBOL = "SUI/USDT"
CHOP_FILTER_SYMBOLS = frozenset(("SUI/USDT", "龙虾/USDT", "LOBSTER/USDT"))
CHOP_MA_OVERLAP_ATR = 0.1
CHOP_FLAT_MOVE_ATR = 0.1
CHOP_MA5_RANGE_ATR = 1.0
ENTRY_EVIDENCE_KEYS = (
    "kc_confirmation_edge", "pending_signal_id", "pending_second_bar_id",
    "pending_wait_bars", "pending_max_wait_bars", "breakout_bar_id",
    "pair_confirmation_bar_id", "third_bar_id", "live_pattern_start_bar_id",
    "live_pattern_pullback_bars", "live_pattern_body_atr", "kc_distance_atr",
    "kc_max_distance_atr", "qualification_signal_id", "live_opening_context",
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
    """Continue only a qualified breakout while live trend and price stay outside."""
    try:
        side = ck_direction(frame)
        if not side:
            return None
        signal = 'KC_OUTSIDE_' + side
        if code is not None and code != signal:
            return None

        qualification = getattr(account, 'breakout_qualification', {}).get(symbol)
        if not qualification or qualification.get('side') != side:
            return None
        live = frame.iloc[-1]
        stamp = float(live['timestamp'])
        qualified_bar = float(qualification['breakout_bar_id'])
        quote = float(quote)
        atr = float(frame.iloc[-2]['atr'])
        if (not all(math.isfinite(value) and value > 0
                    for value in (stamp, qualified_bar, quote, atr))
                or stamp <= qualified_bar):
            return None

        sign = 1 if side == 'LONG' else -1
        opening = float(live['open'])
        if sign * (quote - opening) < LIVE_BREAKOUT_BODY_ATR * atr:
            return None
        closes = [float(value) for value in frame['close'].iloc[-5:-1]]
        last_ma5 = float(frame.iloc[-2]['ma5'])
        if (len(closes) != 4 or not all(math.isfinite(value) and value > 0 for value in closes)
                or not math.isfinite(last_ma5) or last_ma5 <= 0):
            return None
        live_ma5 = (sum(closes) + quote) / 5.0
        if sign * (live_ma5 - last_ma5) < 0.01 * atr:
            return None
        if (not live_candle_color_ready(frame, quote, side)
                or not live_adverse_entry_safe(frame, quote, side)
                or not live_ma3_direction_ready(frame, quote, side)
                or not ma3_outer_continuation_ready(frame, quote, side)):
            return None

        edge = float(live['kc_upper' if side == 'LONG' else 'kc_lower'])
        distance = sign * (quote - edge) / atr
        if (distance <= 0 or distance > LIVE_BREAKOUT_MAX_DISTANCE_ATR
                or sign * (quote - live_ma5) <= 0):
            return None
        previous_stamp = float(frame.iloc[-2]['timestamp'])
        return dict(
            action='ENTER', side=side, type=signal, reason=signal,
            price=quote, entry_atr=atr, confirmation_bar_id=stamp,
            close_price=float(frame.iloc[-2]['close']), intrabar=True,
            entry_phase='KC_CONTINUATION_ENTRY', breakout_bar_id=stamp,
            pair_confirmation_bar_id=previous_stamp, third_bar_id=stamp,
            pending_signal_id=f'{symbol}:CONTINUATION:{int(stamp)}:{side}',
            pending_second_bar_id=previous_stamp, pending_wait_bars=1,
            pending_max_wait_bars=1, kc_confirmation_edge=edge,
            kc_distance_atr=distance,
            kc_max_distance_atr=LIVE_BREAKOUT_MAX_DISTANCE_ATR,
            qualification_signal_id=qualification['pending_signal_id'],
        )
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
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


def evaluate_closed_outer_body_breakout(frame, quote, symbol="", requested_side=None):
    """Allow one fresh live bar to enter from the immediately preceding outer-body break."""
    try:
        if frame is None or len(frame) < 3 or 'is_closed' not in frame:
            return None
        previous, breakout, live = frame.iloc[-3], frame.iloc[-2], frame.iloc[-1]
        if not (bool(previous['is_closed']) and bool(breakout['is_closed'])
                and not bool(live['is_closed'])):
            return None

        breakout_stamp = float(breakout['timestamp'])
        live_stamp = float(live['timestamp'])
        atr = float(previous['atr'])
        if (not all(math.isfinite(value) and value > 0
                    for value in (breakout_stamp, live_stamp, atr))
                or live_stamp != breakout_stamp + 60000):
            return None
        side = outer_body_breakout_side(
            breakout['open'], breakout['close'], breakout['kc_lower'],
            breakout['kc_middle'], breakout['kc_upper'], atr,
        )
        if side is None or (requested_side is not None and requested_side != side):
            return None
        quote = float(quote)
        if not quote_beyond_side_outer_rail(frame, side, quote):
            return None

        sign = 1 if side == 'LONG' else -1
        edge = float(live['kc_upper' if side == 'LONG' else 'kc_lower'])
        distance = sign * (quote - edge) / atr
        if (not math.isfinite(distance) or distance <= 0
                or distance > LIVE_BREAKOUT_MAX_DISTANCE_ATR):
            return None

        code = f"KC_LIVE_BODY_BREAKOUT_{side}"
        return dict(
            action="ENTER", side=side, type=code, reason=code,
            price=quote, entry_atr=atr, confirmation_bar_id=breakout_stamp,
            close_price=float(breakout['close']), intrabar=False,
            entry_phase='KC_LIVE_OUTER_BREAKOUT',
            breakout_bar_id=breakout_stamp, pair_confirmation_bar_id=None,
            third_bar_id=live_stamp, live_opening_context='CLOSED_OUTER_FORMATION',
            pending_signal_id=f"{symbol}:CLOSED_LIVE_BODY:{int(breakout_stamp)}:{side}",
            pending_second_bar_id=live_stamp, pending_wait_bars=0,
            pending_max_wait_bars=0, kc_confirmation_edge=edge,
            kc_distance_atr=distance,
            kc_max_distance_atr=LIVE_BREAKOUT_MAX_DISTANCE_ATR,
        )
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def evaluate_live_body_breakout(frame, quote, symbol="", requested_side=None):
    """Authorize a first live breakout or same-side outer continuation."""
    try:
        if frame is None or len(frame) < 2:
            return None
        live = frame.iloc[-1]
        opened = float(live["open"])
        lower, upper = float(live["kc_lower"]), float(live["kc_upper"])
        quote = float(quote)
        if (not all(math.isfinite(value) and value > 0
                    for value in (opened, lower, upper, quote))
                or lower >= upper):
            return None

        side = live_body_breakout_side(frame, quote)
        if side is None:
            return evaluate_closed_outer_body_breakout(
                frame, quote, symbol=symbol, requested_side=requested_side
            )
        if requested_side is not None and requested_side != side:
            return None
        stamp = float(live["timestamp"])
        atr = float(frame.iloc[-2]["atr"])
        edge = upper if side == "LONG" else lower
        distance = (quote - edge) / atr if side == "LONG" else (edge - quote) / atr
        if (not math.isfinite(stamp) or stamp <= 0 or not math.isfinite(distance)
                or distance <= 0 or distance > LIVE_BREAKOUT_MAX_DISTANCE_ATR):
            return None

        code = f"KC_LIVE_BODY_BREAKOUT_{side}"
        opening_context = (
            'IN_CHANNEL' if lower <= opened <= upper else 'SAME_SIDE_OUTER'
        )
        return dict(
            action="ENTER", side=side, type=code, reason=code,
            price=quote, entry_atr=atr, confirmation_bar_id=stamp,
            close_price=float(frame.iloc[-2]["close"]), intrabar=True,
            entry_phase='KC_LIVE_OUTER_BREAKOUT',
            breakout_bar_id=stamp, pair_confirmation_bar_id=None,
            third_bar_id=stamp,
            live_opening_context=opening_context,
            pending_signal_id=f"{symbol}:LIVE_BODY:{int(stamp)}:{side}",
            pending_second_bar_id=stamp, pending_wait_bars=0,
            pending_max_wait_bars=0, kc_confirmation_edge=edge,
            kc_distance_atr=distance,
            kc_max_distance_atr=LIVE_BREAKOUT_MAX_DISTANCE_ATR,
        )
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None


def entry_trend_alignment_ready(frame, side):
    """Require closed KC, MA5 and MA15 trends to agree with every entry side."""
    try:
        if side not in ("LONG", "SHORT") or ck_direction(frame) != side:
            return False
        closed = closed_entry_candles(frame)
        if len(closed) < 3 or not {"ma5", "ma15"}.issubset(closed.columns):
            return False
        recent = closed.tail(3)
        return ma5_ma15_trend_confirmed(recent["ma5"], recent["ma15"], side)
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False


def entry_consolidation_problem(frame, quote):
    """Reject MA overlap, jointly flat MA15/KC, or bounded MA5 oscillation."""
    try:
        closed = closed_entry_candles(frame)
        required = {"ma5", "ma15", "kc_middle", "close", "atr"}
        if len(closed) < 6 or not required.issubset(closed.columns):
            return "WAIT_CONSOLIDATION_GATE_DATA"
        latest = closed.iloc[-1]
        ma5, ma15, close, atr = (
            float(latest[key]) for key in ("ma5", "ma15", "close", "atr")
        )
        quote = float(quote)
        if not all(math.isfinite(value) and value > 0 for value in
                   (ma5, ma15, close, atr, quote)):
            return "WAIT_CONSOLIDATION_GATE_DATA"
        live_ma5 = ma5 + (quote - close) / 5.0
        live_ma15 = ma15 + (quote - close) / 15.0
        if not all(math.isfinite(value) and value > 0 for value in (live_ma5, live_ma15)):
            return "WAIT_CONSOLIDATION_GATE_DATA"
        if abs(live_ma5 - live_ma15) <= CHOP_MA_OVERLAP_ATR * atr:
            return "BLOCKED_MA5_MA15_OVERLAP"

        recent = closed.tail(6)
        ma5_values = [float(value) for value in recent["ma5"]]
        ma15_values = [float(value) for value in recent["ma15"]]
        kc_values = [float(value) for value in recent["kc_middle"]]
        if not all(math.isfinite(value) and value > 0
                   for value in ma5_values + ma15_values + kc_values):
            return "WAIT_CONSOLIDATION_GATE_DATA"

        if (abs(ma15_values[-1] - ma15_values[0]) <= CHOP_FLAT_MOVE_ATR * atr
                and abs(kc_values[-1] - kc_values[0]) <= CHOP_FLAT_MOVE_ATR * atr):
            return "BLOCKED_FLAT_MA15_KC"

        changes = [right - left for left, right in zip(ma5_values, ma5_values[1:])]
        directions = [1 if change > 0 else -1 if change < 0 else 0 for change in changes]
        nonzero_directions = [direction for direction in directions if direction]
        reversals = sum(
            previous != current
            for previous, current in zip(nonzero_directions, nonzero_directions[1:])
        )
        if (reversals >= 2
                and max(ma5_values) - min(ma5_values) <= CHOP_MA5_RANGE_ATR * atr):
            return "BLOCKED_MA5_REGULAR_OSCILLATION"
        return None
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return "WAIT_CONSOLIDATION_GATE_DATA"


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
    if (symbol == SUI_BREAKOUT_ONLY_SYMBOL and code is not None
            and code not in (KC_PENDING_CODES | LIVE_BODY_BREAKOUT_CODES)):
        return reject("BLOCKED_SUI_REQUIRES_CLOSED_BREAKOUT")
    if account is not None and symbol in getattr(account, "positions", {}):
        return reject("WAIT_EXISTING_POSITION")
    try:
        if frame is None or frame.empty or frame.attrs.get('timeframe_ms', 60000) != 60000:
            return None
        if 'is_closed' not in frame or not all(isinstance(v, (bool, np.bool_)) for v in frame.is_closed):
            return None
        # Rolling indicators legitimately have an unavailable leading prefix.
        # Trim only that prefix; never bridge missing data inside valid history.
        indicator_keys = ['atr', 'kc_upper', 'kc_middle', 'kc_lower']
        ready = frame[indicator_keys].notna().all(axis=1).to_numpy()
        valid_indices = np.flatnonzero(ready)
        if not len(valid_indices):
            return None
        frame = frame.iloc[int(valid_indices[0]):].copy()
        closed = closed_entry_candles(frame)
        if len(closed) < 2 or len(frame)-len(closed) != 1:
            return reject('WAIT_LIVE_THIRD_CANDLE')
        live = frame.iloc[-1]
        if (bool(live.is_closed)
                or float(live.timestamp) != float(closed.iloc[-1].timestamp) + 60000):
            return reject('WAIT_LIVE_THIRD_CANDLE')
        keys = [
            'timestamp','open','high','low','close','kc_upper','kc_middle',
            'kc_lower','atr',
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
        if symbol == SUI_BREAKOUT_ONLY_SYMBOL:
            # For SUI allow a live-body breakout only when the live decision exists
            # and the closed KC/MA5/MA15 trend alignment gate passes. Otherwise fall
            # back to the closed (KC pending) path.
            live_requested_side = (
                code.rsplit("_", 1)[-1] if code in LIVE_BODY_BREAKOUT_CODES else None
            )
            live_decision = None
            if code is None or live_requested_side is not None:
                live_decision = evaluate_live_body_breakout(
                    frame, quote, symbol=symbol, requested_side=live_requested_side
                )
            if live_decision is not None:
                # Only accept live breakout for SUI when closed KC/MA5/MA15 trend aligns
                side_candidate = live_decision.get('side')
                if entry_trend_alignment_ready(frame, side_candidate):
                    decision = live_decision
                else:
                    decision = evaluate_kc_pending_entry(
                        closed, quote, code=code, symbol=symbol, live=live
                    )
            else:
                decision = evaluate_kc_pending_entry(
                    closed, quote, code=code, symbol=symbol, live=live
                )
        else:
            live_code_side = (
                code.rsplit("_", 1)[-1] if code in LIVE_BODY_BREAKOUT_CODES else None
            )
            live_decision = None
            if code is None or live_code_side is not None:
                live_decision = evaluate_live_body_breakout(
                    frame, quote, symbol=symbol, requested_side=live_code_side
                )
            if live_code_side is not None:
                decision = live_decision or {
                    "action": "WAIT", "reason": "WAIT_LIVE_BODY_BREAKOUT",
                }
            elif live_decision is not None:
                decision = live_decision
            elif code in CONTINUATION_CODES:
                decision = evaluate_continuation_entry(
                    frame, quote, code=code, symbol=symbol, account=account
                ) or {'action': 'WAIT', 'reason': 'WAIT_KC_CONTINUATION'}
            else:
                decision = evaluate_kc_pending_entry(
                    closed, quote, code=code, symbol=symbol, live=live
                )
                if code is None and decision.get('action') != 'ENTER':
                    continuation = evaluate_continuation_entry(
                        frame, quote, symbol=symbol, account=account
                    )
                    if continuation:
                        decision = continuation
        if decision.get("action") != "ENTER":
            return reject(decision.get("reason", "WAIT_KC_2BAR_BREAKOUT"))
        side = decision["side"]
        if (symbol == SUI_BREAKOUT_ONLY_SYMBOL
                and decision.get("entry_phase") not in ('KC_2BAR_CLOSED_CONFIRM','KC_LIVE_OUTER_BREAKOUT','KC_LIVE_BODY_BREAKOUT')):
            return reject("BLOCKED_SUI_REQUIRES_CLOSED_BREAKOUT")
        if decision.get("entry_phase") not in (
                'KC_LIVE_OUTER_BREAKOUT', 'KC_2BAR_CLOSED_CONFIRM',
                'KC_CONTINUATION_ENTRY', 'KC_LIVE_BODY_BREAKOUT'):
            return reject("BLOCKED_ENTRY_REQUIRES_CONFIRMED_KC_BREAKOUT")
        if not entry_trend_alignment_ready(frame, side):
            return reject("BLOCKED_KC_MA5_MA15_TREND_MISMATCH")
        if symbol == SUI_BREAKOUT_ONLY_SYMBOL:
            prior, confirmation = closed.iloc[-2], closed.iloc[-1]
            prior_ma5, prior_ma15 = float(prior["ma5"]), float(prior["ma15"])
            confirmation_ma5 = float(confirmation["ma5"])
            confirmation_ma15 = float(confirmation["ma15"])
            crossed = (
                prior_ma5 <= prior_ma15 and confirmation_ma5 > confirmation_ma15
                if side == "LONG" else
                prior_ma5 >= prior_ma15 and confirmation_ma5 < confirmation_ma15
            )
            if not crossed:
                return reject("BLOCKED_SUI_MA5_MA15_CROSS")
        if evaluate_live_ma5_direction(frame, quote, side) is None:
            return reject("BLOCKED_LIVE_MA5_DIRECTION")
        if not quote_beyond_side_outer_rail(frame, side, quote):
            return reject("WAIT_LIVE_PRICE_OUTSIDE_KC_RAIL")
        # Post-exit formation verification:
        if exit_bar is not None and float(live.timestamp) <= exit_bar:
            return reject('WAIT_POST_EXIT_NEW_FORMATION')
        if exit_bar is not None and decision.get('breakout_bar_id', 0) <= exit_bar:
            return reject('WAIT_POST_EXIT_NEW_FORMATION')
        if symbol in CHOP_FILTER_SYMBOLS:
            consolidation_problem = entry_consolidation_problem(frame, quote)
            if consolidation_problem:
                return reject(consolidation_problem)
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
