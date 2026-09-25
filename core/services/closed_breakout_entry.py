"""Closed KC breakout confirmation and observed outside-rail pullback entries."""
import math
import time

from core.services.candle_data import closed_entry_candles, closed_entry_problem

CODES = {f'{prefix}_{side}' for prefix in ('KC_TWO_BAR_BREAKOUT', 'KC_BREAKOUT_PULLBACK')
         for side in ('LONG', 'SHORT')}


def evaluate_closed_breakout(frame, price, side):
    def wait(reason):
        return False, reason, {'action': 'WAIT'}

    if side not in ('LONG', 'SHORT'):
        return wait('WAIT_INVALID_SIDE')
    closed = closed_entry_candles(frame)
    if len(closed) < 2:
        return wait('WAIT_TWO_CLOSED_BREAKOUT_BARS')
    problem = closed_entry_problem(closed)
    if problem:
        return wait(problem)
    first, second = closed.iloc[-2], closed.iloc[-1]
    sign = 1 if side == 'LONG' else -1
    rail = 'kc_upper' if side == 'LONG' else 'kc_lower'
    try:
        for candle in (first, second):
            opening, close, high, low = (float(candle[k]) for k in ('open', 'close', 'high', 'low'))
            if not low <= min(opening, close) <= max(opening, close) <= high or high <= low:
                return wait('WAIT_INVALID_MARKET_DATA')
            if sign * (close - opening) <= 0 or abs(close - opening) / (high - low) < 0.20:
                return wait('WAIT_SAME_COLOR_BREAKOUT_BODIES')
        if sign * (float(first['open']) - float(first[rail])) > 0:
            return wait('WAIT_FIRST_BODY_CROSS')
        if any(sign * (float(candle['close']) - float(candle[rail])) <= 0 for candle in (first, second)):
            return wait('WAIT_TWO_CLOSES_OUTSIDE_RAIL')
        price = float(price)
        upper, lower = (float(frame.iloc[-1][k]) for k in ('kc_upper', 'kc_lower'))
        if not all(math.isfinite(v) and v > 0 for v in (price, upper, lower)) or lower >= upper:
            return wait('WAIT_INVALID_MARKET_DATA')
        if sign * (price - (upper if side == 'LONG' else lower)) <= 0:
            return wait('WAIT_BREAKOUT_QUOTE_OUTSIDE')
        key = 'kc_middle' if 'kc_middle' in closed else 'ema_20'
        previous, latest = float(first[key]), float(second[key])
        ma3, ma15 = float(second['ma3']), float(second['ma15'])
        if not all(math.isfinite(v) and v > 0 for v in (previous, latest, ma3, ma15)):
            return wait('WAIT_INVALID_MARKET_DATA')
        if sign * (latest - previous) <= 0:
            return wait('WAIT_KC_MID_DIRECTION')
        if sign * (ma3 - ma15) <= 0:
            return wait('WAIT_MA3_MA15_ALIGNMENT')
    except (KeyError, TypeError, ValueError, OverflowError):
        return wait('WAIT_INVALID_MARKET_DATA')
    reason = f'KC_TWO_BAR_BREAKOUT_{side}'
    return True, reason, dict(action='ENTER', side=side, reason=reason,
                             entry_atr=float(second['atr']), entry_type='TWO_BAR_BREAKOUT', is_breakout=True)


def close_identity(engine, symbol):
    account = getattr(engine, 'account', None)
    stamps = [str(getattr(account, 'last_closed_at', {}).get(symbol, ''))]
    for trade in reversed(getattr(account, 'trades', [])):
        if trade.get('symbol') == symbol and str(trade.get('action', '')).startswith('CLOSE_'):
            stamps.append(str(trade.get('id')))
            break
    return tuple(stamps)


def clear_pullback(observations, symbol, side):
    if observations is not None:
        observations.pop(('breakout_pullback', symbol, side), None)


def evaluate_breakout_pullback(frame, price, side, observations=None, symbol='', now=None, closed_at=None):
    """Observe a new adverse quote after a confirmed breakout, never OHLC order."""
    key = ('breakout_pullback', symbol, side)
    def wait(reason, reset=True):
        if reset:
            clear_pullback(observations, symbol, side)
        return False, reason, {'action': 'WAIT'}

    if side not in ('LONG', 'SHORT') or observations is None or not symbol:
        return wait('WAIT_BREAKOUT_PULLBACK_OBSERVATION')
    closed = closed_entry_candles(frame)
    if len(closed) < 3 or closed_entry_problem(closed):
        return wait('WAIT_CONFIRMED_BREAKOUT_HISTORY')
    sign = 1 if side == 'LONG' else -1
    rail = 'kc_upper' if side == 'LONG' else 'kc_lower'
    try:
        # Current direction/alignment remain mandatory. Historical confirmation
        # is evaluated against its own rails and closed indicators only.
        middle = 'kc_middle' if 'kc_middle' in closed else 'ema_20'
        prev, latest = (float(v) for v in closed[middle].iloc[-2:])
        ma3, ma15 = (float(closed.iloc[-1][k]) for k in ('ma3', 'ma15'))
        price, live_rail = float(price), float(frame.iloc[-1][rail])
        upper, lower = (float(frame.iloc[-1][k]) for k in ('kc_upper','kc_lower'))
        bar = float(frame.iloc[-1]['timestamp'])
        if not all(math.isfinite(v) and v > 0 for v in (prev,latest,ma3,ma15,price,upper,lower,bar)) or lower >= upper:
            return wait('WAIT_INVALID_MARKET_DATA')
        if sign*(latest-prev) <= 0 or sign*(ma3-ma15) <= 0:
            return wait('WAIT_BREAKOUT_PULLBACK_DIRECTION')
        origin = None
        # The newest completed candle must follow the confirmation pair.
        for i in range(len(closed)-1, 0, -1):
            row = closed.iloc[i]
            value, boundary = float(row['close']), float(row[rail])
            if not all(math.isfinite(v) and v > 0 for v in (value,boundary)) or sign*(value-boundary) <= 0:
                break
            if i < len(closed)-1:
                historical = closed.iloc[:i+1]
                if evaluate_closed_breakout(historical, float(row['close']), side)[0]:
                    origin = float(row['timestamp'])
                    break
            if i > 0:
                interval = frame.attrs.get('timeframe_ms', 60000)
                if float(row['timestamp'])-float(closed.iloc[i-1]['timestamp']) != interval:
                    break
        if origin is None:
            return wait('WAIT_CONFIRMED_BREAKOUT_HISTORY')
        state = observations.get(key)
        if sign*(price-live_rail) <= 0:
            return wait('WAIT_BREAKOUT_QUOTE_OUTSIDE')
        now = time.monotonic() if now is None else float(now)
        if not math.isfinite(now):
            return wait('WAIT_INVALID_MARKET_DATA')
        identity = (origin, bar, closed_at)
        if (not state or state.get('identity') != identity
                or not 0 <= now-state.get('seen', -math.inf) <= 5):
            observations[key] = dict(identity=identity, seen=now, price=price, ready=False)
            return wait('WAIT_BREAKOUT_PULLBACK_OBSERVATION', reset=False)
        move = sign*(price-state['price'])
        if move != 0:
            state['ready'] = move < 0
        state.update(price=price, seen=now)
        if not state['ready']:
            return wait('WAIT_BREAKOUT_PULLBACK', reset=False)
        reason = f'KC_BREAKOUT_PULLBACK_{side}'
        return True, reason, dict(action='ENTER', side=side, reason=reason,
                                 entry_atr=float(closed.iloc[-1]['atr']),
                                 entry_type='BREAKOUT_PULLBACK', is_breakout=True)
    except (KeyError, TypeError, ValueError, OverflowError):
        return wait('WAIT_INVALID_MARKET_DATA')


def check_pivot_reversal_entry(df):
    """峰谷反轉開倉判斷 (Keltner 通道外側收回 + 假突破反轉)

    硬性單倉約束：
    - 多单：c_curr 收盤價 > kc_upper（必須突破上軌）
    - 空单：c_curr 收盤價 < kc_lower（必須突破下軌）
    - 2.0 ATR 居離防追價：离軌已超過 2.0 ATR 則禁止進場
    """
    if len(df) < 4:
        return None

    c_pivot = df.iloc[-2]  # 形成峰谷的極値棒（倒數第二根已收線）
    c_curr  = df.iloc[-1]  # 確認棒（倒數第一根已收線）
    atr = float(c_pivot.get("atr", 0))
    if atr <= 0:
        return None

    try:
        curr_close  = float(c_curr["close"])
        curr_open   = float(c_curr["open"])
        curr_high   = float(c_curr["high"])
        curr_low    = float(c_curr["low"])
        curr_kc_upper = float(c_curr["kc_upper"])
        curr_kc_lower = float(c_curr["kc_lower"])
        pivot_high  = float(c_pivot["high"])
        pivot_low   = float(c_pivot["low"])
        pivot_kc_upper = float(c_pivot["kc_upper"])
        pivot_kc_lower = float(c_pivot["kc_lower"])

        # ==================== 1. 谷底開多判斷 (Pivot Low) ====================
        # 硬性門溺 1：c_curr 收盤必須單破 KC 上軌（不允許在通道內開多）
        if curr_close <= curr_kc_upper:
            pass  # 多单硬性門溺未過，不往下判定
        else:
            # 硬性門溺 2：2.0 ATR 乳離防追價
            long_extension = curr_close - curr_kc_upper
            if long_extension > 2.0 * atr:
                pass  # WAIT_OVEREXTENDED_LONG
            # 硬性門溺通過，判定峰谷形態
            elif (
                pivot_low <= pivot_kc_lower      # c_pivot 影線曾穿破下軌
                and curr_close > curr_open        # c_curr 收陽確認
                and curr_low >= pivot_low          # 未再破底
            ):
                return {
                    "action": "ENTER", "side": "LONG",
                    "reason": "PIVOT_LOW_REVERSAL_LONG", "entry_atr": atr,
                }

        # ==================== 2. 頂峰開空判斷 (Pivot High) ====================
        # 硬性門溺 1：c_curr 收盤必須隨破 KC 下軌（不允許在通道內開空）
        if curr_close >= curr_kc_lower:
            pass  # 空单硬性門溺未過，不判定
        else:
            # 硬性門溺 2：2.0 ATR 乃離防追空
            short_extension = curr_kc_lower - curr_close
            if short_extension > 2.0 * atr:
                pass  # WAIT_OVEREXTENDED_SHORT
            # 硬性門溺通過，判定峰谷形態
            elif (
                pivot_high >= pivot_kc_upper     # c_pivot 影線曾穿破上軌
                and curr_close < curr_open        # c_curr 收陰確認
                and curr_high <= pivot_high        # 未再破高
            ):
                return {
                    "action": "ENTER", "side": "SHORT",
                    "reason": "PIVOT_HIGH_REVERSAL_SHORT", "entry_atr": atr,
                }
    except (KeyError, TypeError, ValueError):
        pass

    return None


def evaluate_channel_entry(frame, price, side, observations=None, symbol='', now=None, closed_at=None):
    if frame is None or len(frame) == 0:
        return False, "EMPTY_FRAME", {}

    # ════════════════════════════════════════════════════════════
    # 【硬性防火牆】位置閘門 — 通道內部絕對禁止開倉
    # 此閘門優先於一切，不可被任何入口邏輯繞過
    # ════════════════════════════════════════════════════════════
    curr = frame.iloc[-1]
    kc_upper = float(curr["kc_upper"])
    kc_lower = float(curr["kc_lower"])
    live_price = float(price)

    if side == "LONG" and live_price <= kc_upper:
        return False, "WAIT_MUST_BREAK_UPPER_KC", {"action": "WAIT"}
    if side == "SHORT" and live_price >= kc_lower:
        return False, "WAIT_MUST_BREAK_LOWER_KC", {"action": "WAIT"}

    # 【硬性防火牆】2.0 ATR 乖離防追價護欄
    try:
        atr_live = float(frame.iloc[-2]["atr"])
        if atr_live > 0:
            if side == "LONG" and (live_price - kc_upper) > 2.0 * atr_live:
                return False, "WAIT_OVEREXTENDED_LONG", {"action": "WAIT"}
            if side == "SHORT" and (kc_lower - live_price) > 2.0 * atr_live:
                return False, "WAIT_OVEREXTENDED_SHORT", {"action": "WAIT"}
    except (KeyError, TypeError, ValueError, IndexError):
        pass

    # ── 入口 1：峰谷反轉（需 c_curr 收盤已突破外軌，否則函式內的硬閘會擋下）──
    closed = closed_entry_candles(frame)
    if len(closed) >= 2:
        pivot_signal = check_pivot_reversal_entry(closed)
        if pivot_signal and pivot_signal.get("side") == side:
            reason = pivot_signal["reason"]
            return True, reason, dict(action='ENTER', side=side, reason=reason,
                                     entry_atr=pivot_signal["entry_atr"],
                                     entry_type='PIVOT_REVERSAL', is_breakout=False)

    # ── 入口 2：嚴格雙破軌──
    breakout = evaluate_closed_breakout(frame, price, side)
    if breakout[0]:
        return breakout

    return False, breakout[1], {}


def matched_reentry_close(account, symbol, ticket):
    """Return the matched fill timestamp; a ticket alone cannot prove a close."""
    try:
        requested = float(ticket.get('close_requested_at_ms') or 0)
        reason = ticket.get('close_reason') or 'Channel Swing PROFIT_PROTECTION ' + ticket['token']
        side = ticket.get('old_side', ticket['side'])
        exit_bar = float(ticket['exit_bar_id'])
        if not math.isfinite(exit_bar) or not math.isfinite(requested) or exit_bar <= 0:
            return False
        fills = [float(t.get('id') or 0) for t in getattr(account, 'trades', [])
                 if t.get('symbol') == symbol and t.get('action') == 'CLOSE_' + side
                 and t.get('reason') == reason
                 and math.isfinite(float(t.get('id') or 0))
                 and float(t.get('id') or 0) >= max(requested, exit_bar)]
        return max(fills) if fills else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
