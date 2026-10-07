"""Position-bound continuous protection of observed executable net profit."""
import math
import copy

REASON = 'EXIT_SWING_ATR_PROFIT_LOCK'
GIVEBACK_RATIO = 0.20
ARM_GAIN_ATR = 2.0
POLICY = 'verified_entry_atr_2_net_peak_v3'


def revoke_lock(state):
    if state.get('pending') == REASON:
        state.pop('pending', None)
        state.pop('trigger', None)
    for key in ('profit_stop_price', 'profit_stop_net', 'profit_stop_source', 'profit_stop_triggered'):
        state.pop(key, None)


def close_evidence(position):
    """Account-level authority requires an actual qualifying observed quote."""
    try:
        lock = position['peak_trailing_state']['swing_atr_profit_lock']
        proof = lock['trigger_evidence']
        entry, qty, atr = map(float, (position['entry_price'], position['qty'], position['entry_atr']))
        sign = 1 if position['side'] == 'LONG' else -1
        identity = [entry, qty, sign, position.get('open_timestamp')]
        peak = float(proof['peak_price'])
        arming_peak = float(lock['arming_peak_price'])
        armed_ms, trigger_ms = float(lock['armed_at_ms']), float(proof['quote_ms'])
        net, floor = float(proof['net_usdt']), float(proof['floor_net_usdt'])
        if (position['side'] not in ('LONG', 'SHORT')
                or lock.get('policy') != POLICY or lock.get('identity') != identity
                or not lock.get('triggered') or proof.get('identity') != identity
                or proof.get('entry_atr') != atr
                or not all(math.isfinite(v) and v > 0 for v in (entry, qty, atr, peak, floor))
                or not math.isfinite(net)
                or not all(math.isfinite(v) for v in (arming_peak, armed_ms, trigger_ms))
                or armed_ms < float(position['open_timestamp'])*1000 or trigger_ms < armed_ms
                or sign * (arming_peak - (entry + sign * ARM_GAIN_ATR * atr)) < 0
                or sign * (peak - (entry + sign * ARM_GAIN_ATR * atr)) < 0
                or net > floor + 1e-12):
            return None
        return dict(copy.deepcopy(lock), reason=REASON, side=position['side'], entry_price=entry,
                    entry_atr=atr, arm_price=entry+sign*ARM_GAIN_ATR*atr,
                    peak_gain_atr=sign*(peak-entry)/atr)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def prepare_close(account, symbol, price, reason):
    from core.services.exits.structural_holding_exit import RETIRED_CLOSE_REASONS
    if reason in {'Channel Swing ' + retired for retired in RETIRED_CLOSE_REASONS}:
        account.log(f'RETIRED_CLOSE_BLOCK symbol={symbol} reason={reason}', 'INFO')
        return False
    return True


def triggered(position, state, price, entry, qty, sign, fee, slippage):
    """Retired by the explicit 2026-10-07 no-profit-lock instruction."""
    return False
