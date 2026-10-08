"""Position-bound fixed-entry-ATR half-step profit floors."""
import copy
import math

from core.services.exits.peak_trailing_exit import estimated_net_pnl, position_identity

STEPS = {"龙虾/USDT": 1.0, "CAP/USDT": 2.0}
REASON = "EXIT_FIXED_ATR_HALF_STEP_PROFIT"


def evaluate(position, state, symbol, price, stamp, *, fee, slippage):
    step = STEPS.get(symbol)
    if step is None:
        return None, None, {}
    try:
        side, _, entry, qty = position_identity(position)
        sign = 1 if side == "LONG" else -1
        atr = float(state.get("reference_atr", position.get("entry_atr")))
        price, stamp, fee, slippage = map(float, (price, stamp, fee, slippage))
        if (side not in ("LONG", "SHORT")
                or not all(math.isfinite(v) and v > 0 for v in (entry, qty, atr, price, stamp))
                or not all(math.isfinite(v) and 0 <= v < 1 for v in (fee, slippage))):
            return None, "WAIT_ATR_STEP_VALID_DATA", {}
        ladder = copy.deepcopy(state.get("atr_step_profit", {}))
        if ladder and (ladder.get("symbol") != symbol or ladder.get("step_atr") != step
                       or ladder.get("reference_atr") != atr):
            return None, "WAIT_ATR_STEP_STATE", {}
        peak = float(ladder.get("peak_gain_atr", 0.))
        level = ladder.get("level", 0)
        floor = float(ladder.get("floor_price", entry))
        if (not math.isfinite(peak) or peak < 0 or type(level) is not int or level < 0
                or not math.isfinite(floor) or floor <= 0):
            return None, "WAIT_ATR_STEP_STATE", {}
        gain = sign*(price-entry)/atr
        peak = max(peak, gain)
        units = peak/step
        nearest = round(units)
        if math.isclose(units, nearest, rel_tol=1e-12, abs_tol=1e-12):
            units = float(nearest)
        reached = max(level, math.floor(units))
        if reached:
            # Solve the shared estimated-net formula for a zero-net quote.
            breakeven = entry*(sign+fee)/((1-sign*slippage)*(sign-fee))
            target = entry+sign*(reached-.5)*step*atr
            candidates = (target, breakeven, floor) if level else (target, breakeven)
            floor = max(candidates) if sign == 1 else min(candidates)
            if not math.isfinite(floor) or floor <= 0:
                return None, "WAIT_ATR_STEP_VALID_FLOOR", {}
        ladder.update(symbol=symbol, step_atr=step, reference_atr=atr,
                      peak_gain_atr=peak, level=reached, floor_price=floor)
        patch = {"atr_step_profit": ladder}
        if reached and sign*(price-floor) <= max(price, floor)*1e-12:
            return dict(reason=REASON, quote_ms=stamp, trigger_price=price,
                        step_atr=step, fixed_entry_atr=atr, level=reached,
                        peak_gain_atr=peak, protection_price=floor,
                        estimated_net_pnl=estimated_net_pnl(entry, price, qty, sign, fee, slippage)), None, patch
        return None, None, patch
    except (KeyError, TypeError, ValueError, OverflowError):
        return None, "WAIT_ATR_STEP_VALID_DATA", {}
