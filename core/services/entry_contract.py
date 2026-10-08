"""Shared signal contract for scan, execution and account revalidation."""
import math

import numpy as np
from core.services.ma5_chop_gate import ma5_chop_problem, ma5_ma15_entanglement_problem

from core.services.candle_data import closed_entry_candles
from core.services.kc_pending_entry import (KC_PENDING_CODES, KC_PENDING_EVIDENCE_KEYS,
                                            evaluate_kc_pending_entry)
from core.services.strategies.outer_strategy import (live_body_breakout_side, ck_direction,
                                                    live_ma3_direction_ready, live_candle_color_ready,
                                                    live_adverse_entry_safe, ma3_outer_continuation_ready,
                                                    OUTER_CODES)

LONG_ENTRY_CODE = "CLOSED_BODY_BREAKOUT_LONG"
SHORT_ENTRY_CODE = "CLOSED_BODY_BREAKOUT_SHORT"
TURN_CODES = {'KC_CHANNEL_TURN_LONG', 'KC_CHANNEL_TURN_SHORT'}
CROSS_CODES = {"MA5_MA15_LIVE_CROSS_LONG", "MA5_MA15_LIVE_CROSS_SHORT"}
ENTRY_CODES = {"KC_2BAR_CONFIRM_LONG", "KC_2BAR_CONFIRM_SHORT",
               } | CROSS_CODES | OUTER_CODES
MAX_THIRD_OPEN_CHASE_ATR = 0.10
CHASE_EVIDENCE_KEYS = ('third_bar_id', 'third_open', 'third_reference_atr',
                       'max_chase_atr', 'chase_atr', 'chase_bar_id',
                       'chase_open', 'chase_reference_atr')

LIVE_PATTERN_KEYS = ("small_first_bar_id", "small_second_bar_id",
                     "small_first_reference_atr", "small_second_reference_atr",
                     "small_first_body_atr", "small_second_body_atr",
                     "live_body_reference_atr", "live_body_atr",
                     "small_body_max_atr", "live_body_min_atr",
                     "small_bridge_count", "small_bridge_bar_ids")
CROSS_EVIDENCE_KEYS = ("cross_previous_ma5", "cross_previous_ma15", "cross_live_ma5",
                       "cross_live_ma15", "cross_reference_bar_id")
ENTRY_EVIDENCE_KEYS = CHASE_EVIDENCE_KEYS + KC_PENDING_EVIDENCE_KEYS + CROSS_EVIDENCE_KEYS + ("continuation_pair_id",)


MA5_MIN_ENTRY_SLOPE_ATR = 0.05

def ma5_entry_ready(frame, quote, side):
    """Require MA5 movement in entry direction of at least 0.05 prior closed ATR."""
    try:
        if side not in ('LONG', 'SHORT') or frame is None or len(frame) < 5:
            return False
        closed = closed_entry_candles(frame)
        if len(closed) < 2:
            return False
        closed_previous, closed_current = [float(v) for v in closed.ma5.iloc[-2:]]
        if not all(math.isfinite(v) and v > 0 for v in (closed_previous, closed_current)):
            return False
        closed_movement = (1 if side == 'LONG' else -1) * (closed_current - closed_previous)
        if closed_movement <= max(closed_previous, closed_current) * 1e-12:
            return False
        if len(frame) == len(closed) + 1:
            atr = float(closed.iloc[-1]['atr'])
            previous = float(closed.iloc[-1]['ma5'])
            prices = [float(v) for v in closed['close'].iloc[-4:]] + [float(quote)]
            if len(prices) != 5 or not all(math.isfinite(v) and v > 0 for v in prices):
                return False
            current = sum(prices) / 5.
        elif len(frame) == len(closed) and len(closed) >= 2:
            atr = float(closed.iloc[-1]['atr'])
            previous = float(closed.iloc[-2]['ma5'])
            current = float(closed.iloc[-1]['ma5'])
        else:
            return False
        if not all(math.isfinite(v) and v > 0 for v in (atr, previous, current)):
            return False
        movement = (1 if side == 'LONG' else -1) * (current - previous)
        return movement > 0 and (movement >= MA5_MIN_ENTRY_SLOPE_ATR * atr or math.isclose(movement, MA5_MIN_ENTRY_SLOPE_ATR * atr, rel_tol=1e-10))
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False


def ma5_kc_trend_ready(frame, quote, side):
    """MA5 must stay outside without approaching the same-side KC outer rail."""
    try:
        if not ma5_entry_ready(frame, quote, side):
            return False
        closed = closed_entry_candles(frame)
        if len(frame) == len(closed) + 1:
            previous = float(closed.iloc[-1]['ma5'])
            current = (sum(float(v) for v in closed.close.iloc[-4:]) + float(quote)) / 5.
            previous_middle = float(closed.iloc[-1]['kc_upper' if side == 'LONG' else 'kc_lower'])
            current_middle = float(frame.iloc[-1]['kc_upper' if side == 'LONG' else 'kc_lower'])
        else:
            previous = float(closed.iloc[-2]['ma5'])
            current = float(closed.iloc[-1]['ma5'])
            previous_middle = float(closed.iloc[-2]['kc_upper' if side == 'LONG' else 'kc_lower'])
            current_middle = float(closed.iloc[-1]['kc_upper' if side == 'LONG' else 'kc_lower'])
        if not all(math.isfinite(v) and v > 0 for v in (previous,current,previous_middle,current_middle)):
            return False
        sign = 1 if side == 'LONG' else -1
        prior_gap = sign * (previous - previous_middle)
        gap = sign * (current - current_middle)
        tolerance = max(current, current_middle) * 1e-12
        return gap > tolerance and gap - prior_gap >= -tolerance
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False


def evaluate_channel_turn(frame, quote, code=None, symbol=''):
    """Observed closed swing plus a live reversal body; no future wick inference."""
    try:
        closed = closed_entry_candles(frame)
        if len(closed) < 4 or len(frame) != len(closed)+1:
            return None
        live = frame.iloc[-1];quote=float(quote);opened=float(live['open'])
        atr=float(closed.iloc[-1]['atr'])
        lower,upper=float(live.kc_lower),float(live.kc_upper)
        if not all(math.isfinite(v) and v>0 for v in (quote,opened,atr,lower,upper)) or not lower < quote < upper:
            return None
        side='LONG' if quote-opened>=.5*atr else 'SHORT' if opened-quote>=.5*atr else None
        if side is None or not ma5_entry_ready(frame,quote,side):
            return None
        ma15 = float(live['ma15'])
        if not math.isfinite(ma15) or ma15 <= 0 or (1 if side == 'LONG' else -1) * (quote-ma15) <= 0:
            return None
        signal='KC_CHANNEL_TURN_'+side
        if code not in (None,signal):
            return None
        sign=1 if side=='LONG' else -1
        anchor=None
        # A peak/valley near the corresponding rail can be followed by up to two small reversal candles.
        for offset in (1,2,3):
            row=closed.iloc[-offset]
            opposite_body=sign*(float(row.open)-float(row.close))>0
            near_rail=(float(row.high)>=float(row.kc_upper) if side=='SHORT' else float(row.low)<=float(row.kc_lower))
            following=closed.iloc[len(closed)-offset+1:]
            if opposite_body and near_rail and all(sign*(float(r.close)-float(r.open))>=0 for _,r in following.iterrows()):
                anchor=row;break
        if anchor is None or sign*(quote-float(closed.iloc[-1].close))<=0:
            return None
        stamp=float(live.timestamp);prev=float(closed.iloc[-1].timestamp)
        return dict(action='ENTER',side=side,type=signal,reason=signal,price=quote,entry_atr=atr,
                    confirmation_bar_id=stamp,close_price=float(closed.iloc[-1].close),intrabar=True,
                    entry_phase='KC_CHANNEL_TURN',breakout_bar_id=stamp,pair_confirmation_bar_id=prev,
                    third_bar_id=stamp,pending_signal_id=f'{symbol}_TURN_{int(anchor.timestamp)}_{int(stamp)}_{side}',
                    pending_second_bar_id=prev,pending_wait_bars=1,pending_max_wait_bars=1)
    except (AttributeError,KeyError,TypeError,ValueError,IndexError,OverflowError):
        return None


def evaluate_continuation_entry(frame, quote, code=None, symbol: str = '', *, account=None):
    """Continuation entry for sustained trend outside the outer rail.

    Permits opening when a prior breakout was missed or after a position was closed,
    Requires a persisted observed general two-bar breakout, KC/MA5 direction,
    and quote strictly beyond both the same-side KC rail and live MA5.
    """
    try:
        if frame is None or len(closed_entry_candles(frame)) != len(frame)-1:
            return None
        side = ck_direction(frame)
        if not side:
            return None
        from core.services.continuation_qualification import qualification
        origin = qualification(account, symbol, side, frame, quote)
        if origin is None:
            return None
        signal = 'KC_OUTSIDE_' + side
        if code is not None and code != signal:
            return None

        quote = float(quote)
        atr = float(frame.iloc[-2]['atr'])
        sign = 1 if side == 'LONG' else -1

        if len(frame) < 5 or not math.isfinite(atr) or atr <= 0:
            return None
        # Check MA5 direction & non-flat slope (漲勢/跌勢)
        closes = [float(v) for v in frame['close'].iloc[-5:-1]]
        if len(closes) >= 4:
            live_ma5 = (sum(closes[-4:]) + quote) / 5.0
            last_ma5 = float(frame.iloc[-2]['ma5'])
            if not all(math.isfinite(v) and v > 0 for v in (live_ma5, last_ma5)):
                return None
            ma5_slope = sign * (live_ma5 - last_ma5)
            if ma5_slope <= 0 or (atr > 0 and ma5_slope / atr < MA5_MIN_ENTRY_SLOPE_ATR):
                return None

        edge = float(frame.iloc[-1]['kc_upper' if side == 'LONG' else 'kc_lower'])
        lower, upper = float(frame.iloc[-1]['kc_lower']), float(frame.iloc[-1]['kc_upper'])
        if not all(math.isfinite(v) and v > 0 for v in (quote, lower, upper)) or lower >= upper:
            return None
        
        # [NEW GATE RULE: 必須在 kc 線及 ma5 外，若在 kc 內或 ma5 內就不要開倉]
        if sign * (quote - edge) <= 0:
            return None
        if sign * (quote - live_ma5) <= 0:
            return None

        distance = sign * (quote - edge) / atr
        if distance <= 0:
            return None

        live = frame.iloc[-1]
        stamp = float(live['timestamp'])
        prev_stamp = float(frame.iloc[-2]['timestamp'])
        if not all(math.isfinite(v) and v > 0 for v in (stamp, prev_stamp)) or stamp-prev_stamp != 60000:
            return None

        return dict(action='ENTER', side=side, type=signal, reason=signal,
                    price=quote, entry_atr=atr, confirmation_bar_id=stamp,
                    close_price=float(frame.iloc[-2]['close']), intrabar=True,
                    entry_phase='KC_CONTINUATION_ENTRY',
                    breakout_bar_id=stamp,
                    pair_confirmation_bar_id=prev_stamp,
                    third_bar_id=stamp,
                    pending_signal_id=f"{symbol}_CONTINUATION_{origin['pair_id']}_{int(stamp)}_{side}",
                    continuation_pair_id=origin["pair_id"],
                    pending_second_bar_id=prev_stamp,
                    pending_wait_bars=1, pending_max_wait_bars=1,
                    kc_confirmation_edge=edge,
                    kc_distance_atr=distance,
                    kc_max_distance_atr=None)
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


def evaluate_small_bridge_breakout(frame, quote):
    """SMALL setup, one or more consecutive opposite SMALL bridges, live breakout."""
    try:
        closed = closed_entry_candles(frame)
        if len(closed) < 3 or len(frame) != len(closed) + 1:
            return None
        live = frame.iloc[-1]
        rows = [row for _, row in closed.iterrows()] + [live]
        stamps = [float(row.timestamp) for row in rows]
        if any(b - a != 60000 for a, b in zip(stamps, stamps[1:])):
            return None
        for row in rows:
            o, h, l, c = [float(row[key]) for key in ("open", "high", "low", "close")]
            if (not all(math.isfinite(v) and v > 0 for v in (o, h, l, c))
                    or not l <= min(o, c) <= max(o, c) <= h or h <= l):
                return None
        trigger_atr = float(closed.iloc[-1].atr)
        if not math.isfinite(trigger_atr) or trigger_atr <= 0:
            return None
        minimum_body = .5 * trigger_atr
        price, opened = float(quote), float(live.open)
        lower, upper = float(live.kc_lower), float(live.kc_upper)
        if (not all(math.isfinite(v) and v > 0 for v in (price, opened, lower, upper))
                or not lower < upper):
            return None
        for side, sign, edge in (("LONG", 1, upper), ("SHORT", -1, lower)):
            body = sign * (price - opened)
            if (sign * (price - edge) <= 0 or body <= 0
                    or (body < minimum_body and not math.isclose(body, minimum_body, rel_tol=1e-12))):
                continue
            bridges = []
            setup_index = None
            for index in range(len(closed) - 1, 0, -1):
                row = closed.iloc[index]
                atr = float(closed.iloc[index - 1].atr)
                small_body = float(row.close) - float(row.open)
                if (not math.isfinite(atr) or atr <= 0 or is_entry_doji(row)
                        or (abs(small_body) > .25 * atr
                            and not math.isclose(abs(small_body), .25 * atr, rel_tol=1e-12))):
                    break
                if sign * small_body < 0:
                    bridges.append(float(row.timestamp))
                elif sign * small_body > 0:
                    if bridges:
                        setup_index = index
                    break
                else:
                    break
            if setup_index is None:
                continue
            first, second = closed.iloc[setup_index], closed.iloc[-1]
            first_atr = float(closed.iloc[setup_index - 1].atr)
            second_atr = float(closed.iloc[-2].atr)
            return {
                "side": side,
                "small_first_bar_id": float(first.timestamp), "small_second_bar_id": float(second.timestamp),
                "small_first_reference_atr": first_atr, "small_second_reference_atr": second_atr,
                "small_first_body_atr": abs(float(first.close) - float(first.open)) / first_atr,
                "small_second_body_atr": abs(float(second.close) - float(second.open)) / second_atr,
                "live_body_reference_atr": trigger_atr, "live_body_atr": body / trigger_atr,
                "small_body_max_atr": .25, "live_body_min_atr": .5,
                "small_bridge_count": len(bridges), "small_bridge_bar_ids": list(reversed(bridges)),
            }
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None
    return None


def evaluate_live_ma_cross(frame, quote, code=None, symbol=""):
    try:
        closed = closed_entry_candles(frame)
        if len(closed) < 14 or len(frame) != len(closed)+1:
            return None
        prices = [float(v) for v in closed.close.tail(14)] + [float(quote)]
        previous5, previous15 = float(closed.iloc[-1].ma5), float(closed.iloc[-1].ma15)
        if not all(math.isfinite(v) and v > 0 for v in prices+[previous5, previous15]):
            return None
        current5, current15 = sum(prices[-5:])/5., sum(prices)/15.
        tolerance = max(previous5, previous15, current5, current15)*1e-12
        for side, sign in (("LONG", 1), ("SHORT", -1)):
            signal = "MA5_MA15_LIVE_CROSS_"+side
            if code not in (None, signal):
                continue
            if (sign*(previous5-previous15) > tolerance
                    or sign*(current5-current15) <= tolerance
                    or sign*(current5-previous5) <= tolerance
                    or sign*(current15-previous15) <= tolerance):
                continue
            stamp = float(frame.iloc[-1].timestamp)
            prev_stamp = float(closed.iloc[-1].timestamp)
            return dict(action="ENTER", side=side, type=signal, reason=signal,
                        price=float(quote), entry_atr=float(closed.iloc[-1].atr),
                        confirmation_bar_id=stamp, close_price=float(closed.iloc[-1].close),
                        intrabar=True, entry_phase="MA5_MA15_LIVE_CROSS",
                        breakout_bar_id=stamp, pair_confirmation_bar_id=prev_stamp,
                        third_bar_id=stamp, pending_second_bar_id=prev_stamp,
                        pending_wait_bars=1, pending_max_wait_bars=1,
                        pending_signal_id=f"{symbol}_MA_CROSS_{int(prev_stamp)}_{int(stamp)}_{side}",
                        cross_previous_ma5=previous5, cross_previous_ma15=previous15,
                        cross_live_ma5=current5, cross_live_ma15=current15,
                        cross_reference_bar_id=prev_stamp)
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None
    return None


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
        ma5_frame = frame
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
            return None
        keys = ['timestamp','open','high','low','close','kc_upper','kc_middle','kc_lower','atr']
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
        # A persisted successful close can authorize one fresh entry in its candle.
        exit_bar = None
        close_fill = None
        for trade in getattr(account, 'trades', []):
            if trade.get('symbol') == symbol and trade.get('action') in ('CLOSE_LONG','CLOSE_SHORT'):
                stamp = float(trade['id'])
                if not math.isfinite(stamp) or stamp <= 0:
                    return reject('WAIT_VALID_CLOSE_HISTORY')
                if trade.get('status') not in (None, 'CLOSED'):
                    continue
                if close_fill is None or stamp > float(close_fill['id']):
                    close_fill = trade
                bar = math.floor(stamp/60000)*60000
                exit_bar = max(exit_bar or bar, bar)
        saved_close = getattr(account, 'last_closed_at', {}).get(symbol)
        if saved_close is not None:
            saved_close = float(saved_close)
            if not math.isfinite(saved_close) or saved_close <= 0:
                return reject('WAIT_VALID_CLOSE_HISTORY')
            saved_bar = math.floor(saved_close/60)*60000
            exit_bar = max(exit_bar or saved_bar, saved_bar)
        decision = evaluate_live_ma_cross(ma5_frame, quote, code, symbol)
        if decision is None:
            decision = evaluate_kc_pending_entry(closed, quote, code, symbol=symbol,
                                                 live=live if len(frame) > len(closed) else None)
        same_bar_close = (close_fill is not None and float(live.timestamp) == exit_bar
                          and math.floor(float(close_fill['id'])/60000)*60000 == exit_bar)
        if decision['action'] != 'ENTER' or (not same_bar_close and exit_bar is not None and decision.get('breakout_bar_id', 0) <= exit_bar):
            continuation = evaluate_continuation_entry(frame, quote, code, symbol=symbol, account=account)
            if continuation:
                decision = continuation
        if decision['action'] != 'ENTER':
            if diagnostics is not None:
                diagnostics.clear()
                diagnostics.update(decision)
            return None
        if decision["entry_phase"] == "MA5_MA15_LIVE_CROSS":
            for trade in getattr(account, "trades", []):
                snapshot = trade.get("entry_snapshot") or {}
                if (trade.get("symbol") == symbol and trade.get("action") in ("OPEN_LONG", "OPEN_SHORT")
                        and snapshot.get("entry_phase") == "MA5_MA15_LIVE_CROSS"
                        and snapshot.get("candidate_bar_id") == decision["confirmation_bar_id"]):
                    return reject("BLOCKED_MA_CROSS_ALREADY_FILLED")

        # [EMERGENCY GUARD: 無論漲勢或跌勢，出現十字線不要再開倉]
        doji_reject = (entry_doji_problem(closed, live, quote)
                       if decision["type"] not in OUTER_CODES | CROSS_CODES else None)
        if doji_reject:
            return reject(doji_reject)

        # Strict live candle color guard: Never open Long on a red live candle, never open Short on a green live candle
        live_open = float(live['open'])
        if decision["type"] not in OUTER_CODES | CROSS_CODES and decision['side'] == 'LONG' and quote < live_open:
            return reject('BLOCKED_OPPOSITE_LIVE_CANDLE_COLOR')
        if decision["type"] not in OUTER_CODES | CROSS_CODES and decision['side'] == 'SHORT' and quote > live_open:
            return reject('BLOCKED_OPPOSITE_LIVE_CANDLE_COLOR')

        if not ma5_entry_ready(ma5_frame, quote, decision['side']):
            return reject('BLOCKED_MA5_FLAT_OPPOSITE_OR_INVALID')
        chop_problem = ma5_chop_problem(ma5_frame)
        if chop_problem:
            return reject(chop_problem)
        entanglement = ma5_ma15_entanglement_problem(ma5_frame, quote, decision["side"])
        if entanglement:
            return reject(entanglement)

        if decision["type"] not in OUTER_CODES | CROSS_CODES and not ma5_kc_trend_ready(ma5_frame, quote, decision['side']):
            return reject('BLOCKED_MA5_RETURNING_TO_KC')

        # Post-exit formation verification:
        if exit_bar is not None and float(live.timestamp) <= exit_bar and not same_bar_close:
            return reject('WAIT_POST_EXIT_NEW_FORMATION')
        if not same_bar_close and exit_bar is not None and decision.get('entry_phase') != 'KC_CONTINUATION_ENTRY' and decision.get('breakout_bar_id', 0) <= exit_bar:
            return reject('WAIT_POST_EXIT_NEW_FORMATION')
        if same_bar_close:
            close_id = float(close_fill['id'])
            if any(t.get('symbol') == symbol and t.get('action') in ('OPEN_LONG', 'OPEN_SHORT')
                   and float(t.get('id') or 0) >= close_id for t in getattr(account, 'trades', [])):
                return reject('BLOCKED_CLOSE_ALREADY_REOPENED')
            decision['same_bar_close_id'] = close_id
            decision['pending_signal_id'] += f'_AFTER_CLOSE_{close_id}'
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
