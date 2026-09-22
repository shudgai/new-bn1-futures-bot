"""Channel Swing profit floor and retracement calculations, independent of orders.
Implements IExitStrategy interface.
"""
import math
from typing import Dict, Any, Optional
import pandas as pd
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


def protection(position, price, fee, slippage, frame=None):
    """Real-time Peak Exit & Trailing Lock"""
    entry = float(position.get('entry_price') or 0)
    qty = float(position.get('qty') or 0)
    margin = float(position.get('margin') or 0)
    side = position.get('side')
    
    if side not in ('LONG', 'SHORT') or not all(math.isfinite(x) and x > 0 for x in (entry, qty, price)):
        return None
        
    sign = 1 if side == 'LONG' else -1
    execution = price * (1 - sign * slippage)
    gross = sign * (price - entry) * qty
    net = sign * (execution - entry) * qty - (entry + execution) * qty * fee
    
    state = position.setdefault('channel_profit_protection', {})
    identity = [side, position.get('open_timestamp'), entry, qty]
    if state.get('identity') != identity:
        state.clear()
        state['identity'] = identity
        
    policy = 'realtime_peak_exit_v1'
    if state.get('policy') != policy:
        state.clear()
        state.update(identity=identity, policy=policy, peak_net=net, locked_net=0.0, pending=False)
        
    # 1. 動態刷新歷史極值 (Peak Tracking)
    state['peak_net'] = max(float(state.get('peak_net', net)), net)
    state['peak_gross'] = max(float(state.get('peak_gross', gross)), gross)
    
    peak_net = state['peak_net']
    triggered = False
    exit_reason = ""
    stop_price = 0.0

    # 0. 絕對安全網：實時最大虧損硬止損與破中軌熔斷 (不等收盤，Ticker 秒平)
    max_allowed_loss_u = -3.0
    if net <= max_allowed_loss_u:
        triggered = True
        exit_reason = f'EMERGENCY_STOP_LOSS: 觸及單筆最大虧損限制 ({net:.2f}U <= {max_allowed_loss_u}U)，即刻市價全平止損！'

    if not triggered and frame is not None and len(frame) > 0:
        live_candle = frame.iloc[-1]
        ema_base = float(live_candle.get('ema_20', live_candle.get('kc_middle', price)))
        if side == 'LONG' and price < ema_base:
            triggered = True
            exit_reason = 'EMERGENCY_TREND_BREAK: 多單即時現價跌破基準中軌，趨勢破位即刻止損！'
        elif side == 'SHORT' and price > ema_base:
            triggered = True
            exit_reason = 'EMERGENCY_TREND_BREAK: 空單即時現價站上基準中軌，趨勢破位即刻止損！'

    # 2. 絕對保本防線：早鳥微利防守 (Break-Even Guard)
    fee_buffer = 0.5
    if not triggered and peak_net >= 2.5:
        if net <= fee_buffer:
            triggered = True
            exit_reason = 'EXIT_BREAK_EVEN: 微利回吐保本觸發，市價全平'

    # 3. 峰谷轉折即時搶跑：峰值回撤截流 (Peak Drawdown Exit)
    if not triggered and peak_net >= 3.0:
        profit_drawdown_ratio = (peak_net - net) / peak_net
        if profit_drawdown_ratio >= 0.25:
            triggered = True
            exit_reason = f'EXIT_PEAK_DRAWDOWN: 峰值回撤達 {profit_drawdown_ratio*100:.1f}%，搶先鎖利全平'

    # 4. 短均線實時破位與 KC 保護 (MA3 & KC Protection)
    if not triggered and peak_net >= 2.5 and frame is not None and len(frame) > 0:
        live_candle = frame.iloc[-1]
        live_close = float(live_candle['close'])
        ma_3 = float(live_candle.get('ma3', live_candle.get('ema_3', live_close)))
        kc_upper = float(live_candle.get('kc_upper', 0))
        kc_lower = float(live_candle.get('kc_lower', 0))
        atr = float(live_candle.get('atr', 0))
        ema_base = float(live_candle.get('ema_20', live_candle.get('kc_middle', live_close)))
        
        bias_atr = abs(price - ema_base) / atr if atr > 0 else 0
        
        # 1. 【核心保護層】：KC 軌外保護期
        is_in_kc_breakout_zone = False
        if side == 'LONG' and price > kc_upper and bias_atr < 2.0:
            is_in_kc_breakout_zone = True
        elif side == 'SHORT' and price < kc_lower and bias_atr < 2.0:
            is_in_kc_breakout_zone = True

        if not is_in_kc_breakout_zone:
            # 2. 【收割層】：極限乖離時 (Bias >= 2.0 ATR) 才允許 MA3 搶跑
            if bias_atr >= 2.0:
                if side == 'LONG' and price < ma_3:
                    triggered = True
                    exit_reason = 'EXIT_EXTREME_MA3: 乖離過熱且跌破 MA3，搶先全平'
                elif side == 'SHORT' and price > ma_3:
                    triggered = True
                    exit_reason = 'EXIT_EXTREME_MA3: 負乖離過熱且站上 MA3，搶先全平'
            else:
                # 3. 【常規離場】：跌回 KC 軌道內且跌破短均線
                if side == 'LONG' and live_close < kc_upper and live_close < ma_3:
                    triggered = True
                    exit_reason = 'EXIT_FALL_BACK_KC: 跌回軌道內且失守短均線，全平離場'
                elif side == 'SHORT' and live_close > kc_lower and live_close > ma_3:
                    triggered = True
                    exit_reason = 'EXIT_FALL_BACK_KC: 漲回軌道內且站上短均線，全平離場'

    # 5. 動態資金階梯鎖利 (放大本金時的波段防線)
    locked_net = float(state.get('locked_net', 0.0))
    step_unit = max(2.0, margin * 0.015)
    
    if peak_net >= step_unit * 2:
        locked_level = int(peak_net / step_unit) - 1
        lock_profit_u = locked_level * step_unit
        locked_net = max(locked_net, lock_profit_u)
        state['locked_net'] = locked_net
        
        # 逆推鎖定淨利對應的價格 (用於 UI 顯示)
        if side == 'LONG':
            stop = (entry * (1 + fee) + locked_net / qty) / ((1 - slippage) * (1 - fee))
        else:
            stop = (entry * (1 - fee) - locked_net / qty) / ((1 + slippage) * (1 + fee))
        stop_price = stop
        
        if not triggered and net <= locked_net:
            triggered = True
            exit_reason = f'EXIT_TIER_LOCK: 觸及動態階梯鎖利線 ({locked_net}U)，全平落袋'

    state['pending'] = bool(state.get('pending')) or triggered
    
    if stop_price > 0:
        state['stop_price'] = stop_price
        state['net_floor_price'] = stop_price
        position['profit_lock_display_sl'] = stop_price

    if not state.get('pending') and locked_net <= 0 and peak_net < 2.5:
        return None

    # 將 exit_reason 寫入 position，方便後續 log
    if triggered and exit_reason:
        position['exit_reason_override'] = exit_reason

    return {'triggered': state['pending'], 'stop_price': stop_price,
            'peak_gross': state['peak_gross'], 'net_pnl': net,
            'locked_net': locked_net, 'peak_net': peak_net, 'retracement_fraction': 0.0,
            'reason': exit_reason}


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
