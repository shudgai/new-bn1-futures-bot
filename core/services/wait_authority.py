"""Independent WAIT lifecycle. This module never submits or closes an order."""
import copy
from decimal import Decimal
import hashlib
import json
import math
import time
import uuid
import numpy as np

from core.services.candle_data import closed_entry_candles

STATE_KEY = "_independent_wait_authority"
SYMBOLS = frozenset(("龙虾/USDT", "CAP/USDT"))
CODES = frozenset(("WAIT_LIVE_BIG_LONG", "WAIT_LIVE_BIG_SHORT"))
SESSION = uuid.uuid4().hex
SMALL_BODY_MAX = Decimal("0.25")
BIG_BODY_MIN = Decimal("0.50")


def completed_classification(row, reference_atr):
    from core.services.entry_contract import is_entry_doji
    try:
        opened, high, low, close, atr = map(float, (
            row.open, row.high, row.low, row.close, reference_atr))
        if (not all(math.isfinite(v) and v > 0 for v in (opened, high, low, close, atr))
                or high <= low or not low <= min(opened, close) <= max(opened, close) <= high):
            return "INVALID_CANDLE"
        if is_entry_doji(row):
            return "DOJI"
        body = abs(Decimal(str(close))-Decimal(str(opened)))
        if body <= SMALL_BODY_MAX*Decimal(str(atr)):
            return "SMALL_GREEN" if close > opened else "SMALL_RED"
        if body >= BIG_BODY_MIN*Decimal(str(atr)):
            return "BIG_GREEN" if close > opened else "BIG_RED"
        return "MEDIUM"
    except (AttributeError, TypeError, ValueError, ArithmeticError):
        return "INVALID_CANDLE"


class WaitAuthority:
    def __init__(self, account):
        self.account = account

    def _save(self, symbol, state, previous):
        if state != previous:
            self.account.position_meta.setdefault(STATE_KEY, {})[symbol] = state
            durable_keys = ("side", "setup", "live", "frozen_live", "claims", "session",
                            "suspended_for_position", "status")
            candidate_id = (state.get("candidate") or {}).get("wait_trigger_id")
            previous_id = (previous.get("candidate") or {}).get("wait_trigger_id")
            if (candidate_id != previous_id
                    or any(state.get(key) != previous.get(key) for key in durable_keys)):
                self.account.save_state()
            if state.get("status") != previous.get("status"):
                self.account.log("WAIT_AUTHORITY "+json.dumps({
                    "symbol": symbol, "side": state.get("side"),
                    "status": state.get("status")}, ensure_ascii=False), "INFO")

    def observe(self, symbol, frame, price, quote_ms, *, flat_confirmed):
        if symbol not in SYMBOLS:
            return None
        previous = copy.deepcopy(self.account.position_meta.get(STATE_KEY, {}).get(symbol, {}))
        state = copy.deepcopy(previous)
        candidate = None
        try:
            if symbol in self.account.positions or not flat_confirmed:
                position = self.account.positions.get(symbol) or {}
                trigger = (position.get("entry_snapshot") or {}).get("wait_trigger_id")
                if float(position.get("qty", 0)) > 0:
                    for claim in state.get("claims", {}).values():
                        if (claim.get("trigger_id") == trigger
                                and claim.get("side") == position.get("side")
                                and claim.get("phase") in ("CLAIMED", "UNKNOWN", "PARTIAL")):
                            claim["phase"] = "FILLED"
                state.pop("side", None)
                state.pop("setup", None)
                state.pop("live", None)
                state.pop("frozen_live", None)
                state.pop("candidate", None)
                state["suspended_for_position"] = True
                state["status"] = "WAIT_POSITION_SUSPENDED"
                return None
            price, quote_ms = float(price), float(quote_ms)
            if (not all(math.isfinite(v) and v > 0 for v in (price, quote_ms))
                    or not 0 <= time.time()*1000-quote_ms <= 5000
                    or frame is None or frame.empty
                    or frame.attrs.get("timeframe_ms", 60000) != 60000
                    or "is_closed" not in frame):
                raise ValueError("WAIT_DATA_SUSPENDED")
            closed = closed_entry_candles(frame)
            if len(closed) < 2 or len(frame) != len(closed)+1:
                raise ValueError("WAIT_DATA_SUSPENDED")
            row, last, prior = frame.iloc[-1], closed.iloc[-1], closed.iloc[-2]
            bar = float(row.timestamp)
            opened, atr = float(row.open), float(last.atr)
            if (bar != math.floor(quote_ms/60000)*60000
                    or not isinstance(row.is_closed, (bool, np.bool_)) or bool(row.is_closed)
                    or float(last.timestamp) != bar-60000
                    or float(prior.timestamp) != bar-120000
                    or not all(math.isfinite(v) and v > 0 for v in (opened, atr))
                    or completed_classification(last, prior.atr) == "INVALID_CANDLE"):
                raise ValueError("WAIT_DATA_SUSPENDED")
            if quote_ms <= state.get("last_quote_ms", 0):
                return None
            resumed = (state.get("session") != SESSION
                       or state.pop("suspended_for_position", False)
                       or state.get("status") == "WAIT_DATA_SUSPENDED")
            live = state.get("live", {})
            frozen = state.get("frozen_live") or live
            frozen = frozen if frozen.get("bar") == bar else None
            if resumed:
                state.pop("candidate", None)
                live = {}
            if live.get("bar") != bar:
                if live.get("bar") == bar-60000:
                    kind = completed_classification(last, live["atr"])
                    color = kind.removeprefix("SMALL_")
                    setup_side = "LONG" if color == "RED" else "SHORT"
                    if kind.startswith("SMALL_"):
                        if state.get("side") in (None, setup_side):
                            state.update(side=setup_side, setup={
                                "bar": float(last.timestamp), "open": float(last.open),
                                "close": float(last.close), "atr": live["atr"]})
                            state["status"] = "WAIT_SETUP_REFRESHED"
                        else:
                            state["status"] = "WAIT_BRIDGE_PRESERVED"
                    elif kind.startswith("BIG_") and state.get("side"):
                        same_side = (kind == "BIG_GREEN" and state["side"] == "LONG"
                                     or kind == "BIG_RED" and state["side"] == "SHORT")
                        state["status"] = ("MISSED_LIVE_TRIGGER" if same_side
                                           and not state.get("candidate")
                                           else "WAIT_COMPLETED_BIG_KEEP")
                    else:
                        state["status"] = "WAIT_DOJI_KEEP" if kind == "DOJI" else "WAIT_MEDIUM_KEEP"
                elif live:
                    state["status"] = "WAIT_DATA_SUSPENDED"
                live = frozen or {"bar": bar, "open": opened, "atr": atr}
                state.pop("candidate", None)
            if live["open"] != opened:
                raise ValueError("WAIT_DATA_SUSPENDED")
            state.update(session=SESSION, live=live, frozen_live=copy.deepcopy(live),
                         last_quote_ms=quote_ms)
            if not state.get("side") or not state.get("setup"):
                return None
            sign = 1 if state["side"] == "LONG" else -1
            body = sign*(Decimal(str(price))-Decimal(str(opened)))
            if body < BIG_BODY_MIN*Decimal(str(live["atr"])):
                state.pop("candidate", None)
                return None
            provenance = json.dumps(state["setup"], sort_keys=True, separators=(",", ":"))
            trigger_id = hashlib.sha256(
                f"{symbol}:{state['side']}:{int(bar)}:{provenance}".encode()).hexdigest()
            claims = state.get("claims", {})
            if str(int(bar)) in claims or any(
                    claim.get("phase") in ("CLAIMED", "UNKNOWN", "PARTIAL")
                    for claim in claims.values()):
                state["status"] = "WAIT_CLAIM_OR_UNKNOWN_BLOCK"
                return None
            candidate = dict(action="ENTER", side=state["side"], symbol=symbol,
                             type="WAIT_LIVE_BIG_"+state["side"], entry_phase="INDEPENDENT_WAIT",
                             reason="WAIT_LIVE_BIG_"+state["side"], price=price,
                             quote_ms=quote_ms, entry_atr=live["atr"],
                             confirmation_bar_id=bar, breakout_bar_id=state["setup"]["bar"],
                             pair_confirmation_bar_id=state["setup"]["bar"],
                             close_price=float(last.close), intrabar=True,
                             pending_signal_id=trigger_id, wait_trigger_id=trigger_id,
                             wait_setup=copy.deepcopy(state["setup"]), wait_live_open=opened,
                             wait_fixed_atr=live["atr"])
            state.update(candidate=candidate, status="WAIT_LIVE_TRIGGER_OBSERVED")
            return copy.deepcopy(candidate)
        except (AttributeError, KeyError, TypeError, ValueError, ArithmeticError) as exc:
            state.pop("live", None)
            state.pop("candidate", None)
            state["status"] = "WAIT_DATA_SUSPENDED"
            self.account.log(f"WAIT_DATA_SUSPENDED symbol={symbol} reason={exc}", "WARNING")
            return None
        finally:
            self._save(symbol, state, previous)

    def candidate(self, symbol, price, quote_ms):
        """Read-only diagnostics cannot arm, refresh, claim or consume WAIT."""
        try:
            state = getattr(self.account, "position_meta", {}).get(STATE_KEY, {}).get(symbol, {})
            candidate = state.get("candidate") or {}
            live = state.get("live") or {}
            price, quote_ms = float(price), float(quote_ms)
            if (symbol not in SYMBOLS or state.get("session") != SESSION
                    or symbol in self.account.positions
                    or not candidate or not math.isfinite(price) or price <= 0
                    or not 0 <= time.time()*1000-quote_ms <= 5000
                    or not 0 <= time.time()*1000-state.get("last_quote_ms", 0) <= 5000
                    or live.get("bar") != math.floor(quote_ms/60000)*60000
                    or str(int(live["bar"])) in state.get("claims", {})
                    or any(c.get("phase") in ("CLAIMED", "UNKNOWN", "PARTIAL")
                           for c in state.get("claims", {}).values())):
                return None
            sign = 1 if candidate["side"] == "LONG" else -1
            if sign*(Decimal(str(price))-Decimal(str(live["open"]))) < BIG_BODY_MIN*Decimal(str(live["atr"])):
                return None
            return dict(copy.deepcopy(candidate), price=price, quote_ms=quote_ms)
        except (KeyError, TypeError, ValueError, ArithmeticError):
            return None

    def claim(self, symbol, trigger_id):
        state = self.account.position_meta.get(STATE_KEY, {}).get(symbol, {})
        candidate = state.get("candidate") or {}
        previous = copy.deepcopy(state)
        if (candidate.get("wait_trigger_id") != trigger_id
                or not 0 <= time.time()*1000-candidate.get("quote_ms", 0) <= 5000
                or candidate.get("confirmation_bar_id") != math.floor(time.time()/60)*60000
                or symbol in self.account.positions
                or symbol in getattr(self.account, "pending_limit_orders", {})
                or symbol in getattr(self.account, "closing_lock", set())):
            raise ValueError("WAIT_CLAIM_SAFETY_BLOCK")
        claims = state.setdefault("claims", {})
        bar = str(int(candidate["confirmation_bar_id"]))
        if bar in claims or any(c.get("phase") in ("CLAIMED", "UNKNOWN", "PARTIAL")
                                for c in claims.values()):
            raise ValueError("WAIT_DUPLICATE_OR_UNKNOWN_CLAIM")
        claims[bar] = {"trigger_id": trigger_id, "phase": "CLAIMED",
                       "side": candidate["side"], "claimed_ms": time.time()*1000}
        state["status"] = "WAIT_ORDER_CLAIMED"
        self._save(symbol, state, previous)

    def settle(self, symbol, trigger_id, *, outcome, filled_qty, position_confirmed):
        state = self.account.position_meta.get(STATE_KEY, {}).get(symbol, {})
        previous = copy.deepcopy(state)
        claim = next((c for c in state.get("claims", {}).values()
                      if c["trigger_id"] == trigger_id), None)
        if claim is None or claim["phase"] != "CLAIMED":
            raise ValueError("WAIT_SETTLEMENT_WITHOUT_CLAIM")
        filled_qty = float(filled_qty)
        if not math.isfinite(filled_qty) or filled_qty < 0:
            raise ValueError("WAIT_INVALID_FILL_QUANTITY")
        position = self.account.positions.get(symbol) or {}
        if (filled_qty > 0 and position_confirmed and position.get("side") == claim["side"]
                and float(position.get("qty", 0)) > 0):
            claim["phase"] = "FILLED"
            state.pop("side", None)
            state.pop("setup", None)
            state.pop("candidate", None)
            state.pop("live", None)
            state.pop("frozen_live", None)
            state.update(suspended_for_position=True, status="WAIT_CONSUMED_BY_POSITION")
        elif outcome == "FAILED" and filled_qty == 0 and not position:
            claim["phase"] = "FAILED"
            state["status"] = "WAIT_FAILED_TRIGGER_NO_RETRY"
        else:
            claim["phase"] = "UNKNOWN"
            state["status"] = "WAIT_ORDER_QUARANTINED"
        self._save(symbol, state, previous)
