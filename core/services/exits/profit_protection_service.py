"""Channel Swing profit floor and retracement calculations, independent of orders.
Implements IExitStrategy interface.
"""
import math
from typing import Dict, Any, Optional
import pandas as pd
from core import config
from core.interfaces.exit_interface import IExitStrategy


def trend_style(frame, side, opened_at=None):
    """Classify closed bars, using KC width to compare different price scales.

    STACKED: two favorable bodies, each >=15% of channel width, total
    >=50%, with rising/falling closes and <=25% adjacent body overlap.
    SMOOTH: six bars with >=75% directional efficiency, <=25% width
    close drawdown, favorable channel slope and closes in its outer half.
    Stacked evidence must start after entry; smooth context can precede it.
    """
    required = {'open', 'close', 'kc_upper', 'kc_lower'}
    if frame is None or len(frame) < 3 or not required.issubset(frame.columns):
        return 'UNKNOWN'
    try:
        rows = frame.iloc[-7:-1]
        values = rows[list(sorted(required))].astype(float)
        if not all(math.isfinite(v) and v > 0 for v in values.to_numpy().flat):
            return 'UNKNOWN'
        widths = (rows['kc_upper'].astype(float) - rows['kc_lower'].astype(float)).tolist()
        if min(widths) <= 0:
            return 'UNKNOWN'
        sign = 1 if side == 'LONG' else -1
        opens = [sign * float(v) for v in rows['open']]
        closes = [sign * float(v) for v in rows['close']]
        bodies = [c - o for o, c in zip(opens, closes)]
        recent = rows.iloc[-2:]
        post_entry = (not opened_at or ('timestamp' in recent and
                      all(float(v) >= float(opened_at) * 1000 for v in recent['timestamp'])))
        stacked = post_entry and all(b >= .15 * w for b, w in zip(bodies[-2:], widths[-2:]))
        if stacked and sum(bodies[-2:]) >= .50 * sum(widths[-2:]) / 2:
            if all(closes[i] > closes[i-1] and
                   max(0., closes[i-1] - opens[i]) <= .25 * min(bodies[i-1], bodies[i])
                   for i in range(len(closes)-1, len(closes))):
                return 'STACKED'
        if len(rows) < 6:
            return 'UNKNOWN'
        moves = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        distance = sum(abs(v) for v in moves)
        efficiency = sum(moves) / distance if distance else 0.
        drawdown = max(max(closes[:i+1]) - c for i, c in enumerate(closes))
        middles = [sign * (float(u) + float(l)) / 2
                   for u, l in zip(rows['kc_upper'], rows['kc_lower'])]
        if (efficiency >= .75 and drawdown <= .25 * min(widths)
                and all(middles[i] > middles[i-1] for i in range(1, len(middles)))
                and all(c > m for c, m in zip(closes, middles))):
            return 'SMOOTH'
        return 'CHOPPY'
    except (TypeError, ValueError, KeyError, OverflowError):
        return 'UNKNOWN'


def locked_stop_price(entry, side, qty, locked_net, fee, slippage):
    """Price at which the locked net amount is realised (shared with the exchange stop)."""
    if side == "LONG":
        return (entry * (1 + fee) + locked_net / qty) / ((1 - slippage) * (1 - fee))
    return (entry * (1 - fee) - locked_net / qty) / ((1 + slippage) * (1 + fee))


def protection(position, price, fee, slippage, frame=None):
    """Step ladder: arm at the configured net peak, lock peak minus the offset."""
    entry = float(position.get('entry_price') or 0)
    qty = float(position.get('qty') or 0)
    side = position.get('side')
    if side not in ('LONG', 'SHORT') or not all(math.isfinite(x) and x > 0 for x in (entry, qty, price)):
        return None
    sign = 1 if side == 'LONG' else -1
    execution = price * (1 - sign * slippage)
    gross = sign * (price - entry) * qty
    net = sign * (execution - entry) * qty - (entry + execution) * qty * fee
    # 2026-09-14 使用者：特例K不走 ATR 括號，改回固定鎖利（階梯：4U 啟動、鎖峰值−2U）。
    special_k_entry = bool(position.get("entry_special_k") or position.get("special_k_entry"))
    if config.CHANNEL_ATR_EXIT_ENABLED and not special_k_entry:
        try:
            atr = float(position.get("atr") or 0.0)
        except (TypeError, ValueError):
            atr = 0.0
        if atr > 0:
            # ATR 括號模式：固定停損／目標之外，併用階梯鎖利保護既有獲利
            # （峰值 ≥ ARM 就把停損抬到 峰值 − OFFSET），讓介面能顯示「已鎖利」。
            state_atr = position.setdefault("channel_atr_bracket", {})
            state_atr["peak_net"] = max(float(state_atr.get("peak_net") or net), net)
            peak_net = float(state_atr["peak_net"])
            arm = float(config.CHANNEL_SWING_PROFIT_LADDER_ARM_NET_USDT)
            step = float(config.CHANNEL_SWING_PROFIT_LADDER_LOCK_OFFSET_USDT)
            lock = (float(math.floor(peak_net / step) * step - step)
                    if peak_net >= arm and step > 0 else 0.0)
            reason = str(position.get("reason") or "")
            long_body = "KC_LIVE_BODY_BREAKOUT" in reason or "LONG_BODY" in reason
            target_mult = (config.CHANNEL_ATR_LONG_BODY_TARGET_MULT if long_body
                           else config.CHANNEL_ATR_TARGET_MULT)
            stop_offset = atr * config.CHANNEL_ATR_STOP_MULT
            target_offset = atr * target_mult
            stop = entry - sign * stop_offset
            target = entry + sign * target_offset
            if lock > 0:
                locked_price = locked_stop_price(entry, side, qty, lock, fee, slippage)
                stop = max(stop, locked_price) if sign > 0 else min(stop, locked_price)
            state_atr.update(stop_price=stop, target_price=target, atr=atr, locked_net=lock)
            hit_stop = price <= stop if sign > 0 else price >= stop
            hit_target = price >= target if sign > 0 else price <= target
            return {"triggered": bool(hit_stop or hit_target), "stop_price": stop,
                    "target_price": target,
                    # 2026-09-14 使用者：出場標籤要正確——ATR 停損不該寫成「獲利保護」。
                    "exit_kind": "ATR_STOP" if hit_stop else "ATR_TARGET",
                    "peak_gross": float(state_atr.get("peak_gross") or 0.0),
                    "net_pnl": net, "locked_net": lock, "peak_net": peak_net,
                    "retracement_fraction": 0.0}
    state = position.setdefault("channel_profit_protection", {})
    identity = [side, position.get('open_timestamp'), entry, qty]
    if state.get('identity') != identity:
        state.clear()
        state['identity'] = identity
    policy = 'fixed_net_steps_2u'
    if state.get('policy') != policy:
        # Rebuild the ladder from the first observation under the new policy.
        state.clear()
        state.update(identity=identity, policy=policy, peak_net=net,
                     locked_net=0., pending=False)
    state['peak_net'] = max(float(state.get('peak_net', net)), net)
    state['peak_gross'] = max(float(state.get('peak_gross', gross)), gross)

    # 淨利峰值達 ARM 才啟動，鎖住「峰值 − LOCK_OFFSET」，之後每上升一個 LOCK_OFFSET 再上移一階。
    peak = float(state['peak_net'])
    if special_k_entry:
        # 特例K：早點入袋（2U 啟動、回吐 1U）
        arm = float(getattr(config, "CHANNEL_SWING_PROFIT_LADDER_ARM_SPECIAL_K_USDT", 2.0))
        step = float(getattr(config, "CHANNEL_SWING_PROFIT_LADDER_LOCK_OFFSET_SPECIAL_K_USDT", 1.0))
    else:
        arm = float(config.CHANNEL_SWING_PROFIT_LADDER_ARM_NET_USDT)
        step = float(config.CHANNEL_SWING_PROFIT_LADDER_LOCK_OFFSET_USDT)
    ladder_lock = (float(math.floor(peak / step) * step - step) if peak >= arm and step > 0 else 0.0)
    floor_arm = float(config.CHANNEL_SWING_PROFIT_FLOOR_ARM_NET_USDT)
    floor_net = float(config.CHANNEL_SWING_PROFIT_FLOOR_NET_USDT)
    floor_lock = floor_net if (floor_net > 0 and peak >= floor_arm > 0) else 0.0
    locked = max(float(state.get('locked_net', 0.0)), ladder_lock, floor_lock)
    # 2026-09-14 使用者：進場那一根K還沒收線前，不准因「鎖利回吐」平倉——
    # 特例K常在同一根內劇烈震盪，等該根收線後才啟用鎖利（ATR 停損不受影響）。
    if locked > 0.0 and frame is not None:
        try:
            import time as _time
            live_bar = float(frame.iloc[-1]["timestamp"]) / 1000.0
            opened_at = float(position.get("open_timestamp") or 0.0)
            # 僅針對「正在形成中的當根」（與現在時間相差 3 分鐘內）才抑制鎖利；
            # 回測／測試用的歷史框架不受影響。
            forming = live_bar > 0 and abs(_time.time() - live_bar) <= 180.0
            if forming and opened_at >= live_bar:
                locked = 0.0
                state['locked_net'] = 0.0
        except (AttributeError, KeyError, IndexError, TypeError, ValueError):
            pass
    state['locked_net'] = locked
    state['armed'] = locked > 0.0
    # 2026-09-14 使用者：特例K也要有 ATR 停損（虧損底線）；一般進場本來就有。
    atr_stop = None
    try:
        atr_value = float(position.get("atr") or 0.0)
    except (TypeError, ValueError):
        atr_value = 0.0
    if config.CHANNEL_ATR_EXIT_ENABLED and atr_value > 0:
        atr_stop = entry - sign * atr_value * float(config.CHANNEL_ATR_STOP_MULT)
    if not state['armed']:
        if atr_stop is None:
            return None
        hit_atr = price <= atr_stop if sign > 0 else price >= atr_stop
        state['stop_price'] = atr_stop
        return {'triggered': bool(hit_atr), 'stop_price': atr_stop, 'target_price': None,
                'exit_kind': 'ATR_STOP', 'peak_gross': float(state.get('peak_gross') or 0.0),
                'net_pnl': net, 'locked_net': 0.0, 'peak_net': peak,
                'retracement_fraction': 0.0}

    state['retracement_fraction'] = 0.0
    stop = locked_stop_price(entry, side, qty, locked, fee, slippage)
    state['stop_price'] = stop
    state['net_floor_price'] = stop
    state['pending'] = bool(state.get('pending')) or net <= locked
    return {'triggered': state['pending'], 'stop_price': stop,
            'peak_gross': state['peak_gross'], 'net_pnl': net,
            'locked_net': locked, 'peak_net': state['peak_net'], 'retracement_fraction': 0.0}


def abnormal_long_bar(frame, price):
    """Reuse KC spike sizes: range >1.25 widths, body >=.8 width or 3x average."""
    abnormal = None
    for offset in (-2, -1):
        row = frame.iloc[offset]
        width = float(row['kc_upper']) - float(row['kc_lower'])
        close = price if offset == -1 else float(row['close'])
        body = abs(close - float(row['open']))
        prior = frame.iloc[max(0, len(frame) + offset - 9):len(frame) + offset]
        average = (prior['close'].astype(float) - prior['open'].astype(float)).abs().mean()
        span = max(float(row['high']), close) - min(float(row['low']), close)
        if width > 0 and (span > width * 1.25 or body >= width * .8 or (average > 0 and body >= average * 3)):
            abnormal = float(row.get('timestamp', row.name))
    return abnormal


def directional_entry_ready(frame, price, side="LONG"):
    """Require a solid directional live body and non-adverse outer/middle KC."""
    if side not in ("LONG", "SHORT"):
        return False
    sign = 1 if side == "LONG" else -1
    try:
        rows = frame.iloc[-3:]
        if len(rows) != 3:
            return False
        upper = [float(v) for v in rows['kc_upper']]
        lower = [float(v) for v in rows['kc_lower']]
        middle = []
        for (_, row), top, bottom in zip(rows.iterrows(), upper, lower):
            value = row.get('ema_20', float('nan'))
            if not math.isfinite(float(value)):
                value = row.get('kc_middle', float('nan'))
            middle.append(float(value) if math.isfinite(float(value)) else (top + bottom) / 2)
        live = rows.iloc[-1]
        opened, high, low = (float(live[k]) for k in ('open', 'high', 'low'))
        if not all(math.isfinite(v) and v > 0 for v in upper + lower + middle + [opened, high, low, price]):
            return False
        span = max(high, price) - min(low, price)
        return bool(all(b < t for b, t in zip(lower, upper))
                    and all(sign * values[0] <= sign * values[1] <= sign * values[2]
                            for values in (upper if side == "LONG" else lower, middle))
                    and sign * (price - opened) > 0
                    and sign * (price - (upper[-1] if side == "LONG" else lower[-1])) > 0
                    and span > 0 and sign * (price - opened) / span >= .20)
    except (TypeError, ValueError, KeyError, IndexError):
        return False


def long_entry_ready(frame, price):
    return directional_entry_ready(frame, price, "LONG")


def reentry_gate(ticket, frame, price):
    """Pull back, then form two new closed directional bodies before reentry."""
    try:
        side = ticket['side']
        if side not in ('LONG', 'SHORT') or frame is None or len(frame) < 4:
            return 'wait'
        sign = 1 if side == 'LONG' else -1
        rail_key = 'kc_upper' if side == 'LONG' else 'kc_lower'
        live = frame.iloc[-1]
        rail = float(live[rail_key])
        bar = float(live.get('timestamp', live.name))
        if not all(math.isfinite(v) and v > 0 for v in (price, rail)) or not math.isfinite(bar):
            return 'wait'
        if sign * (price - rail) <= 0:
            ticket['pulled_back_inside'] = True
            ticket['pullback_bar'] = bar
            return 'wait'
        if not ticket.get('pulled_back_inside') or 'pullback_bar' not in ticket:
            return 'wait'
        breakout, confirmation = frame.iloc[-3], frame.iloc[-2]
        times = [float(row.get('timestamp', row.name)) for row in (breakout, confirmation)]
        pullback = float(ticket['pullback_bar'])
        if not all(math.isfinite(v) for v in times + [pullback]) or not pullback <= times[0] < times[1] < bar:
            return 'wait'
        for row in (breakout, confirmation):
            o, c, h, l, u, d = [float(row[k]) for k in ('open','close','high','low','kc_upper','kc_lower')]
            if (not all(math.isfinite(v) and v > 0 for v in (o,c,h,l,u,d))
                    or not l <= min(o,c) < max(o,c) <= h or d >= u
                    or sign * (c-o) / (h-l) < .20
                    or sign * (c-float(row[rail_key])) <= 0):
                return 'wait'
        o = float(breakout['open'])
        if not float(breakout['kc_lower']) <= o <= float(breakout['kc_upper']):
            return 'wait'
        body = abs(float(breakout['close']) - o)
        if (abs(float(confirmation['open']) - float(breakout['close'])) > .25 * body
                or sign * (float(confirmation['close']) - float(breakout['close'])) <= 0):
            return 'wait'
        return 'ready' if directional_entry_ready(frame, price, side) else 'wait'
    except (TypeError, ValueError, KeyError, IndexError, ZeroDivisionError):
        return 'wait'


class ProfitProtectionExitStrategy(IExitStrategy):
    """OOP Strategy class implementing IExitStrategy for profit protection exit evaluation."""

    def __init__(self, fee: float = 0.0005, slippage: float = 0.0005):
        self.fee = fee
        self.slippage = slippage

    def evaluate_exit(
        self,
        position: Dict[str, Any],
        frame: pd.DataFrame,
        price: float,
        **kwargs: Any
    ) -> Optional[str]:
        result = protection(position, price, self.fee, self.slippage, frame)
        if result and result.get('triggered'):
            return "PROFIT_PROTECTION_DRAWDOWN_EXIT"
        return None
