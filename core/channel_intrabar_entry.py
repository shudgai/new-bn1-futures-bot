"""Observe an adverse leg and a small recovery within one live minute candle."""
import math


PULLBACK_ATR = .10
RECOVERY_ATR = .05
MAX_RECOVERY_ATR = .15


class IntrabarEntry:
    def __init__(self):
        self.states = {}

    def reset(self, symbol):
        self.states.pop(symbol, None)

    def prepare(self, symbol, side, frame, price, now):
        try:
            bar = float(frame.iloc[-1]["timestamp"]) / 1000
            opened = float(frame.iloc[-1]["open"])
            atr = float(frame.iloc[-2]["atr"])
            price, now = float(price), float(now)
            if (side not in ("LONG", "SHORT")
                    or not all(math.isfinite(v) and v > 0 for v in (bar, opened, atr, price, now))
                    or bar % 60 != 0 or not bar <= now < bar + 60):
                self.reset(symbol)
                return False
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            self.reset(symbol)
            return False
        identity = (bar, side, opened, atr)
        state = self.states.get(symbol)
        if state is None or state["identity"] != identity:
            signed = price if side == "LONG" else -price
            self.states[symbol] = dict(identity=identity, anchor=signed, extreme=signed,
                                       last=signed, at=now, armed=False, ready=False)
            return False
        return self.observe(symbol, price, now)

    def observe(self, symbol, price, now):
        state = self.states.get(symbol)
        if state is None:
            return False
        try:
            price, now = float(price), float(now)
            bar, side, _, atr = state["identity"]
            if not (math.isfinite(price) and price > 0 and math.isfinite(now) and bar <= now < bar + 60):
                self.reset(symbol)
                return False
            if now < state["at"]:
                return False
            signed = price if side == "LONG" else -price
            previous = state["last"]
            state.update(last=signed, at=now)
            if not state["armed"]:
                state["anchor"] = max(state["anchor"], signed)
                state["extreme"] = signed
                state["armed"] = state["anchor"] - signed >= PULLBACK_ATR * atr
                state["ready"] = False
                return False
            state["extreme"] = min(state["extreme"], signed)
            recovery = signed - state["extreme"]
            # A missed turn cannot be revived by falling back into its old window.
            if recovery > MAX_RECOVERY_ATR * atr or signed >= state["anchor"]:
                state.update(anchor=signed, extreme=signed, armed=False, ready=False)
                return False
            if signed != previous:
                state["ready"] = signed > previous and recovery >= RECOVERY_ATR * atr
            return state["ready"]
        except (TypeError, ValueError):
            self.reset(symbol)
            return False
