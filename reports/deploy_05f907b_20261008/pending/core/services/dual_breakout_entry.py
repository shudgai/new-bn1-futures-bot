"""Independent live crossing and completed two-body breakout authorities."""
import math

LIVE_CODES = frozenset(("KC_LIVE_CROSS_LONG", "KC_LIVE_CROSS_SHORT"))
PAIR_CODES = frozenset(("KC_2BAR_CONFIRM_LONG", "KC_2BAR_CONFIRM_SHORT"))
CODES = LIVE_CODES | PAIR_CODES
POLICY = "LIVE_CROSS_OR_TWO_CLOSED_KC_MA_CHOP_V2"


def evaluate_breakout(frame, quote, account, symbol, diagnostics, requested=None):
    live, closed = frame.iloc[-1], frame.iloc[:-1]
    bar = float(live.timestamp)
    last_close = getattr(account, "last_closed_at", {}).get(symbol)
    if last_close is not None:
        stamp = float(last_close)*1000
        if not math.isfinite(stamp) or stamp <= 0:
            diagnostics["reason"] = "BLOCKED_INVALID_CLOSE_TIME"
            return None
        if stamp >= bar:
            diagnostics["reason"] = "BLOCKED_CLOSE_THIS_CANDLE"
            return None
    for trade in getattr(account, "trades", []):
        if trade.get("symbol") != symbol:
            continue
        stamp = float(trade.get("id") or 0)
        if trade.get("action") in ("CLOSE_LONG", "CLOSE_SHORT", "OPEN_LONG", "OPEN_SHORT") and stamp >= bar:
            diagnostics["reason"] = "BLOCKED_CANDLE_ALREADY_TRADED"
            return None
    ticket = getattr(account, "channel_profit_reentries", {}).get(symbol, {})
    if ticket.get("phase") == "closing":
        diagnostics["reason"] = "BLOCKED_CLOSE_NOT_CONFIRMED"
        return None
    side = "LONG" if quote > float(live.kc_upper) else "SHORT" if quote < float(live.kc_lower) else None
    if side is None:
        diagnostics["reason"] = "WAIT_QUOTE_NOT_STRICTLY_OUTSIDE_KC"
        return None
    sign = 1 if side == "LONG" else -1
    live_ready = float(live.kc_lower) <= float(live.open) <= float(live.kc_upper)
    from core.services.closed_breakout_entry import evaluate_closed_breakout
    pair_ready, pair_reason, _ = evaluate_closed_breakout(
        frame, quote, side, require_ma_alignment=False)
    first = closed.iloc[-2]
    pair_ready = pair_ready and float(first.kc_lower) <= float(first.open) <= float(first.kc_upper)
    live_code, pair_code = "KC_LIVE_CROSS_"+side, "KC_2BAR_CONFIRM_"+side
    code = live_code if live_ready else pair_code if pair_ready else None
    if requested in LIVE_CODES:
        code = live_code if live_ready and requested == live_code else None
    elif requested in PAIR_CODES:
        code = pair_code if pair_ready and requested == pair_code else None
    if code is None:
        diagnostics["reason"] = pair_reason if requested in PAIR_CODES else "WAIT_VALID_BREAKOUT_FORMATION"
        return None
    from core.services.entry_contract import live_ma_direction_evidence
    averages = live_ma_direction_evidence(frame, quote, side, diagnostics)
    if averages is None:
        return None
    from core.services.entry_chop_gate import evaluate_entry_chop
    chop_status, chop = evaluate_entry_chop(frame, check_ma5_turns=True)
    if chop is None:
        diagnostics["reason"] = chop_status
        return None
    atr = float(closed.iloc[-1].atr)
    defensive = closed["low" if sign == 1 else "high"].tail(5)
    level = float(defensive.min() if sign == 1 else defensive.max())
    stop = level-sign*.1*atr
    if stop <= 0:
        diagnostics["reason"] = "WAIT_INVALID_DEFENSIVE_STOP"
        return None
    pair = code in PAIR_CODES
    k1, k2 = float(first.timestamp), float(closed.iloc[-1].timestamp)
    signal_id = f"{symbol}:{side}:{int(k1)}:{int(k2)}" if pair else f"{symbol}:{side}:{int(bar)}"
    if any(t.get("symbol") == symbol and t.get("action") in ("OPEN_LONG", "OPEN_SHORT")
           and (t.get("entry_snapshot") or {}).get("pending_signal_id") == signal_id
           for t in getattr(account, "trades", [])):
        diagnostics["reason"] = "BLOCKED_BREAKOUT_ALREADY_FILLED"
        return None
    diagnostics["reason"] = code
    return dict(action="ENTER", side=side, type=code, reason=code, price=quote,
                entry_atr=atr, confirmation_bar_id=bar,
                close_price=float(closed.iloc[-1].close), intrabar=True,
                entry_phase="KC_2BAR_CLOSED_CONFIRM" if pair else "KC_LIVE_CROSS",
                breakout_bar_id=k1 if pair else bar,
                pair_confirmation_bar_id=k2 if pair else None, third_bar_id=bar,
                pending_signal_id=signal_id,
                structure_risk_stop=stop,
                entry_failure_level=float(live.kc_upper if sign == 1 else live.kc_lower),
                exit_bar_id=None, entry_qualification_policy=POLICY, **averages, **chop)
