"""Closed-candle Channel Swing exits from confirmed three-bar pivots."""
import copy
import math

STATE_KEY = "channel_confirmed_structure_exit_state"
EXIT_REASON = "EXIT_CONFIRMED_3BAR_STRUCTURE_BREAK"
BAR_MS = 60_000
GRACE_PERIOD_BARS = 2


def _positive(value):
    try:
        value = float(value)
        return math.isfinite(value) and value > 0
    except (TypeError, ValueError, OverflowError):
        return False


def _position_identity(position):
    try:
        side = position.get("side")
        opened_at = float(position.get("open_timestamp") or 0.0)
        entry = float(position.get("entry_price") or 0.0)
        quantity = abs(float(position.get("qty") or position.get("quantity") or 0.0))
        if opened_at > 1e11:
            opened_at /= 1000.0
        if side not in ("LONG", "SHORT") or not all(
            _positive(value) for value in (opened_at, entry, quantity)
        ):
            return None
        return [side, opened_at, entry, quantity]
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def _closed_history(snapshot, quote_ms):
    if not isinstance(snapshot, dict) or snapshot.get("reason") is not None:
        return None
    history = snapshot.get("history_5")
    if not isinstance(history, list) or len(history) < 3:
        return None
    try:
        snapshot_quote_ms = float(snapshot["quote_ms"])
        live_bar_ms = float(snapshot["live_bar_ms"])
        closed_bar_ms = float(snapshot["closed_bar_ms"])
        snapshot_bar_ms = float(snapshot["snapshot_bar_id"])
        current_bar_ms = math.floor(quote_ms / BAR_MS) * BAR_MS
        if (not math.isclose(snapshot_quote_ms, quote_ms, rel_tol=0.0, abs_tol=1e-6)
                or live_bar_ms != current_bar_ms
                or closed_bar_ms != current_bar_ms - BAR_MS
                or snapshot_bar_ms != closed_bar_ms):
            return None

        parsed = []
        for bar in history:
            values = [float(bar[key]) for key in
                      ("ms", "o", "h", "l", "c", "kc_middle")]
            stamp, opening, high, low, close, middle = values
            if (not math.isfinite(stamp)
                    or stamp <= 0
                    or not all(_positive(value) for value in values[1:])
                    or high < max(opening, close)
                    or low > min(opening, close)
                    or low > high):
                return None
            parsed.append({
                "ms": stamp,
                "o": opening,
                "h": high,
                "l": low,
                "c": close,
                "kc_middle": middle,
            })
        if (parsed[-1]["ms"] != closed_bar_ms
                or any(right["ms"] - left["ms"] != BAR_MS
                       for left, right in zip(parsed, parsed[1:]))):
            return None
        return parsed
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def evaluate_confirmed_structure_exit(position, meta, snapshot, quote_ms, price):
    """Return a close decision only for a fresh completed-bar pivot breach."""
    identity = _position_identity(position)
    try:
        quote_ms, price = float(quote_ms), float(price)
        if (identity is None or not math.isfinite(quote_ms)
                or not _positive(price) or not isinstance(meta, dict)):
            return None, False
        opened_ms = identity[1] * 1000.0
        if quote_ms < opened_ms:
            return None, False

        existing = meta.get(STATE_KEY) or position.get(STATE_KEY)
        if not isinstance(existing, dict) or existing.get("identity") != identity:
            state = {
                "identity": identity,
                "line": None,
                "line_bar_ms": None,
                "last_closed_bar_ms": 0.0,
            }
        else:
            state = copy.deepcopy(existing)

        if state.get("pending") == EXIT_REASON:
            changed = (
                meta.get(STATE_KEY) != state
                or position.get(STATE_KEY) != state
            )
            if changed:
                meta[STATE_KEY] = copy.deepcopy(state)
                position[STATE_KEY] = copy.deepcopy(state)
            return {
                "type": EXIT_REASON,
                "trigger": EXIT_REASON,
                "price": price,
                "line": state.get("line"),
                "trigger_bar_ms": state.get("trigger_bar_ms"),
            }, changed

        history = _closed_history(snapshot, quote_ms)
        if history is None:
            return None, False

        closed_bar_ms = history[-1]["ms"]
        if closed_bar_ms <= float(state.get("last_closed_bar_ms") or 0.0):
            return None, False

        latest_pivot = None
        first_full_bar_ms = math.ceil(opened_ms / BAR_MS) * BAR_MS
        for index in range(1, len(history) - 1):
            left, pivot, right = history[index - 1:index + 2]
            if pivot["ms"] < first_full_bar_ms:
                continue
            if identity[0] == "LONG":
                is_pivot = pivot["l"] < left["l"] and pivot["l"] < right["l"]
                candidate = pivot["l"]
            else:
                is_pivot = pivot["h"] > left["h"] and pivot["h"] > right["h"]
                candidate = pivot["h"]
            if is_pivot:
                latest_pivot = (pivot["ms"], candidate)

        previous_line = state.get("line")
        if latest_pivot is not None:
            pivot_ms, candidate = latest_pivot
            if (not _positive(previous_line)
                    or (identity[0] == "LONG" and candidate > float(previous_line))
                    or (identity[0] == "SHORT" and candidate < float(previous_line))):
                state["line"] = candidate
                state["line_bar_ms"] = pivot_ms

        elapsed_ms = history[-1]["ms"] + BAR_MS - opened_ms
        bars_held = (
            max(0, math.ceil(elapsed_ms / BAR_MS) - 1)
            if elapsed_ms > 0 else 0
        )
        line = state.get("line")
        close = history[-1]["c"]
        opening = history[-1]["o"]
        adverse_body = (
            close < opening if identity[0] == "LONG"
            else close > opening
        )
        breached = (
            _positive(line)
            and bars_held > GRACE_PERIOD_BARS
            and adverse_body
            and (close < float(line) if identity[0] == "LONG"
                 else close > float(line))
            and (close < history[-1]["kc_middle"] if identity[0] == "LONG"
                 else close > history[-1]["kc_middle"])
        )
        state["last_closed_bar_ms"] = closed_bar_ms
        if breached:
            state.update(
                pending=EXIT_REASON,
                trigger_bar_ms=closed_bar_ms,
                trigger_close=close,
            )

        changed = meta.get(STATE_KEY) != state
        if changed:
            meta[STATE_KEY] = copy.deepcopy(state)
            position[STATE_KEY] = copy.deepcopy(state)
        if not breached:
            return None, changed
        return {
            "type": EXIT_REASON,
            "trigger": EXIT_REASON,
            "price": price,
            "line": line,
            "trigger_bar_ms": closed_bar_ms,
            "trigger_close": close,
        }, changed
    except (KeyError, TypeError, ValueError, OverflowError):
        return None, False
