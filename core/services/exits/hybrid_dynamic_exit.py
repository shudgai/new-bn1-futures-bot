"""Hybrid Dynamic Trailing Exit: Two-stage exit state machine.

Stage 1: ATR dynamic breathing stage (1.5% <= net_profit_pct < 4.0%)
         Uses 1.3 * ATR trailing distance from peak price to absorb small wicks
         without premature exit on thin initial profits.

Stage 2: Percentage ratchet harvest stage (net_profit_pct >= 4.0%)
         Switches off ATR trailing completely. Steps up locked floor every 1%
         profit gain and exits immediately upon a 20% pullback from the locked tier.

Baseline protection:
         KC midline defence: a confirmed closed body breaching KC midline
         triggers immediate structural liquidation.
"""
import copy
import math
from typing import Any, Dict, Optional, Tuple

from core.services.exits.peak_trailing_exit import (
    estimated_display_net_pnl,
    position_identity,
)

HYBRID_DYNAMIC_STATE_KEY = 'hybrid_dynamic_exit_state'

HYBRID_STAGE1_MIN_NET_PROFIT_PCT = 1.5
HYBRID_STAGE2_MIN_NET_PROFIT_PCT = 4.0
HYBRID_STAGE1_ATR_MULT = 1.3
HYBRID_STAGE2_GIVEBACK_RATIO = 0.35

HYBRID_DYNAMIC_ATR_TRAILING_TRIGGER = 'HYBRID_DYNAMIC_ATR_TRAILING'
HYBRID_DYNAMIC_RATCHET_TRIGGER = 'HYBRID_DYNAMIC_RATCHET_GIVEBACK'
HYBRID_MIDLINE_BREACH_TRIGGER = 'KC_MIDLINE_CLOSE_BREACH'


def calculate_chart_net_profit_pct(
    entry: float,
    price: float,
    qty: float,
    side: str,
    fee: float = 0.0,
    slippage: float = 0.0,
) -> float:
    """Calculate the chart net profit percentage (unleveraged price move minus costs)."""
    if entry <= 0 or qty <= 0:
        return 0.0
    sign = 1 if side == 'LONG' else -1 if side == 'SHORT' else 0
    net_pnl = estimated_display_net_pnl(entry, price, qty, sign, fee, slippage)
    notional = entry * qty
    return (net_pnl / notional) * 100.0


def check_kc_midline_breach(position: Dict[str, Any], snapshot: Dict[str, Any]) -> bool:
    """Check if the latest closed bar breached and closed beyond KC midline adversely."""
    if not isinstance(snapshot, dict):
        return False
    history = snapshot.get('history_5') or []
    if not history:
        return False
    last_closed = history[-1]
    side = position.get('side')
    try:
        close_p = float(last_closed.get('c', 0.0))
        open_p = float(last_closed.get('o', 0.0))
        kc_mid = float(last_closed.get('kc_middle', 0.0))
        if kc_mid <= 0 or close_p <= 0:
            return False
        if side == 'LONG':
            # Adverse red candle closing below KC middle
            return close_p < kc_mid and close_p <= open_p
        elif side == 'SHORT':
            # Adverse green candle closing above KC middle
            return close_p > kc_mid and close_p >= open_p
    except (TypeError, ValueError):
        return False
    return False


def evaluate_hybrid_dynamic_exit(
    position: Dict[str, Any],
    meta: Dict[str, Any],
    price: float,
    stamp: float,
    snapshot: Dict[str, Any],
    fee: float = 0.0,
    slippage: float = 0.0,
) -> Tuple[Optional[str], bool, Dict[str, Any]]:
    """Evaluate two-stage hybrid dynamic exit for active position.

    Returns:
        (trigger_code, state_changed, diagnostics)
    """
    if not position or price <= 0:
        return None, False, {}

    side = position.get('side')
    if side not in ('LONG', 'SHORT'):
        return None, False, {}

    entry = float(position.get('entry_price') or 0.0)
    qty = float(position.get('qty') or 1.0)
    if entry <= 0 or qty <= 0:
        return None, False, {}

    atr = float(snapshot.get('atr', 0.0)) if isinstance(snapshot, dict) else 0.0
    net_profit_pct = calculate_chart_net_profit_pct(entry, price, qty, side, fee, slippage)

    # Manage state persistence
    side_val, opened_val, entry_val, qty_val = position_identity(position)
    identity = f"{side_val}|{opened_val:.9f}|{entry_val:.12g}|{qty_val:.12g}"

    state = position.get(HYBRID_DYNAMIC_STATE_KEY) or meta.get(HYBRID_DYNAMIC_STATE_KEY) or {}
    if state.get('identity') != identity:
        state = {
            'identity': identity,
            'stage': 0,
            'peak_high': price if side == 'LONG' else entry,
            'peak_low': price if side == 'SHORT' else entry,
            'locked_tier': 0.0,
            'peak_net_profit_pct': net_profit_pct,
            'last_ms': stamp,
        }
    else:
        state = copy.deepcopy(state)

    state_changed = False
    current_stage = state.get('stage', 0)

    # ── Update extremes ──
    if side == 'LONG':
        new_peak_high = max(float(state.get('peak_high', price)), price)
        if new_peak_high != state.get('peak_high'):
            state['peak_high'] = new_peak_high
            state_changed = True
    else:
        new_peak_low = min(float(state.get('peak_low', price)), price)
        if new_peak_low != state.get('peak_low'):
            state['peak_low'] = new_peak_low
            state_changed = True

    peak_profit = max(float(state.get('peak_net_profit_pct', net_profit_pct)), net_profit_pct)
    if peak_profit != state.get('peak_net_profit_pct'):
        state['peak_net_profit_pct'] = peak_profit
        state_changed = True

    # ── Stage transitions ──
    kc_upper = float(snapshot.get('live_kc_upper') or snapshot.get('kc_upper') or 0.0) if isinstance(snapshot, dict) else 0.0
    kc_lower = float(snapshot.get('live_kc_lower') or snapshot.get('kc_lower') or 0.0) if isinstance(snapshot, dict) else 0.0
    outer_breakout = (
        (side == 'LONG' and kc_upper > 0 and price > kc_upper)
        or (side == 'SHORT' and kc_lower > 0 and price < kc_lower)
    )
    gain_atr = ((price - entry) / atr if side == 'LONG' else (entry - price) / atr) if atr > 0 else 0.0

    # Stage 2: Net profit >= 4.0%
    if net_profit_pct >= HYBRID_STAGE2_MIN_NET_PROFIT_PCT or state.get('stage') == 2:
        if state.get('stage') != 2:
            state['stage'] = 2
            state_changed = True
        # Ratchet step-up: floor to nearest 1.0% (e.g. 4.0, 5.0, 6.0...)
        current_tier = math.floor(net_profit_pct + 1e-12)
        old_tier = float(state.get('locked_tier', 0.0))
        if current_tier > old_tier:
            state['locked_tier'] = float(current_tier)
            state_changed = True
    # Stage 1: Armed on KC outer rail breakout, favorable excursion >= 1.0 ATR, or net_profit >= 1.5%
    elif state.get('stage') == 0 and (
        outer_breakout
        or gain_atr >= 1.0
        or net_profit_pct >= HYBRID_STAGE1_MIN_NET_PROFIT_PCT
    ):
        state['stage'] = 1
        state_changed = True

    state['last_ms'] = stamp
    current_stage = state.get('stage', 0)

    # Persist state back to position and meta
    position[HYBRID_DYNAMIC_STATE_KEY] = copy.deepcopy(state)
    meta[HYBRID_DYNAMIC_STATE_KEY] = copy.deepcopy(state)

    diagnostics = {
        'stage': current_stage,
        'net_profit_pct': net_profit_pct,
        'locked_tier': state.get('locked_tier', 0.0),
        'peak_high': state.get('peak_high'),
        'peak_low': state.get('peak_low'),
    }

    # ── Protection Baseline: KC Midline Structural Breach ──
    if check_kc_midline_breach(position, snapshot):
        return HYBRID_MIDLINE_BREACH_TRIGGER, state_changed, diagnostics

    # ── Stage 2: Percentage Ratchet Take Profit ──
    if current_stage == 2:
        locked_tier = float(state.get('locked_tier', 0.0))
        if locked_tier >= HYBRID_STAGE2_MIN_NET_PROFIT_PCT:
            # 20% giveback from locked tier (e.g. 4.0% -> 3.2%, 5.0% -> 4.0%, 6.0% -> 4.8%)
            giveback_threshold = locked_tier * (1.0 - HYBRID_STAGE2_GIVEBACK_RATIO)
            if net_profit_pct <= giveback_threshold or math.isclose(
                net_profit_pct, giveback_threshold, rel_tol=1e-12, abs_tol=1e-12,
            ):
                return HYBRID_DYNAMIC_RATCHET_TRIGGER, state_changed, diagnostics
        return None, state_changed, diagnostics

    # ── Stage 1: ATR Dynamic Trailing Exit (1.3 ATR or 20% profit giveback) ──
    if current_stage == 1:
        peak_profit_pct = float(state.get('peak_net_profit_pct', 0.0))
        if peak_profit_pct >= 1.5 and net_profit_pct <= peak_profit_pct * (1.0 - HYBRID_STAGE2_GIVEBACK_RATIO):
            return HYBRID_DYNAMIC_RATCHET_TRIGGER, state_changed, diagnostics

        if atr > 0:
            if side == 'LONG':
                peak_high = float(state.get('peak_high', price))
                stop_threshold = peak_high - HYBRID_STAGE1_ATR_MULT * atr
                if price <= stop_threshold or math.isclose(
                    price, stop_threshold, rel_tol=1e-12, abs_tol=1e-12,
                ):
                    return HYBRID_DYNAMIC_ATR_TRAILING_TRIGGER, state_changed, diagnostics
            elif side == 'SHORT':
                peak_low = float(state.get('peak_low', price))
                stop_threshold = peak_low + HYBRID_STAGE1_ATR_MULT * atr
                if price >= stop_threshold or math.isclose(
                    price, stop_threshold, rel_tol=1e-12, abs_tol=1e-12,
                ):
                    return HYBRID_DYNAMIC_ATR_TRAILING_TRIGGER, state_changed, diagnostics

    return None, state_changed, diagnostics
