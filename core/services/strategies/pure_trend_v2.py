import logging
import math
from typing import Dict, Any, Optional

logger = logging.getLogger("PureTrendV2_Meme")

class PureTrendStrategyV2:
    """
    妖幣純淨趨勢追蹤引擎：
    1. 開倉：前根已收線同向實體站外，下一根盤中站外且 MA3／MA15 同向；當根同向實體至少全長25%，末端影線不超過實體。
    2. 物理禁區：KC 中軌上方嚴禁開空！KC 中軌下方嚴禁開多！
    3. 盤中策略出口：嚴格穿越KC中軌，或曾達10U/3ATR後回吐超過25%；不等收線。
    4. 常規平倉：只在1M收線嚴格突破最近已確認峰谷時全平；無峰谷續抱。
    5. 未觸發平倉前，嚴格抱牢波段，一股都不賣！
    """
    def __init__(self):
        self.cooldown_tracker = {}

    def record_exit(self, symbol: str, side: str, current_bar_index: int):
        self.cooldown_tracker[symbol] = dict(exit_bar_index=int(current_bar_index), side=side)

    def is_valid_directional_entry_bar(self, bar_curr, side):
        """Strict live color, >=25% body, and adverse-side wick <= body."""
        try:
            opened, close, high, low = (float(bar_curr[k]) for k in ('open', 'close', 'high', 'low'))
            if side not in ('LONG', 'SHORT') or not all(
                    math.isfinite(v) and v > 0 for v in (opened, close, high, low)):
                return False
            if not low <= min(opened, close) <= max(opened, close) <= high or high <= low:
                return False
            signed_body = (close-opened) * (1 if side == 'LONG' else -1)
            if signed_body <= 0:
                return False
            minimum = .25*(high-low)
            if signed_body < minimum and not math.isclose(signed_body, minimum, rel_tol=1e-12):
                return False
            wick = high-close if side == 'LONG' else close-low
            return wick <= signed_body or math.isclose(wick, signed_body, rel_tol=1e-12)
        except (KeyError, TypeError, ValueError, OverflowError):
            return False

    def evaluate_second_bar_outside_entry(
        self, symbol: str, bar_curr: Dict[str, Any], bar_prev: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """One closed directional outside body followed by its live next bar.

        Evaluation is read-only. The engine locks successful fills, not signals.
        """
        import numpy as np

        previous_closed = bar_prev.get('is_closed')
        current_closed = bar_curr.get('is_closed')
        if (not isinstance(previous_closed, (bool, np.bool_)) or not previous_closed
                or not isinstance(current_closed, (bool, np.bool_)) or current_closed):
            return None
        try:
            p_open, p_close, p_upper, p_lower = (
                float(bar_prev[k]) for k in ('open', 'close', 'kc_upper', 'kc_lower'))
            c_open, price, upper, lower, mid, ma3, ma15 = (
                float(bar_curr[k]) for k in
                ('open', 'close', 'kc_upper', 'kc_lower', 'kc_middle', 'ma3', 'ma15'))
            stamps = [float(bar['timestamp']) for bar in (bar_prev, bar_curr)]
            values = (p_open, p_close, p_upper, p_lower, c_open, price,
                      upper, lower, mid, ma3, ma15, *stamps)
            if not all(math.isfinite(v) and v > 0 for v in values):
                return None
            if not p_lower < p_upper or not lower < mid < upper:
                return None
            if stamps[1] - stamps[0] != 60000:
                return None
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        side = None
        if p_close > p_open and p_close > p_upper and price > upper and ma3 > ma15:
            side = 'LONG'
        elif p_close < p_open and p_close < p_lower and price < lower and ma3 < ma15:
            side = 'SHORT'
        if side is None:
            return None
        if not self.is_valid_directional_entry_bar(bar_curr, side):
            return None
        return dict(side=side, type='SECOND_BAR_OUTSIDE_' + side, price=price,
                    reason='第1根已收線同向實體站外，第2根盤中站外且均線同向即開倉')

    def evaluate_anti_whipsaw_profit_lock(self, position, current_price, bar_curr, atr):
        """Tick protection; no breakeven or fixed-profit tier exits."""
        try:
            price = float(current_price)
            entry = float(position['entry_price'])
            qty = abs(float(position.get('qty', position.get('quantity', 0.))))
            stamp, opened = float(bar_curr['quote_ms']), float(position['open_timestamp'])
            side = position['side']
            if side not in ('LONG', 'SHORT') or not all(
                    math.isfinite(v) and v > 0 for v in (price, entry, qty, stamp, opened)):
                return None
            if stamp < opened*1000:
                return None
            identity = [side, opened, entry, qty]
            state = position.get('instant_exit_state') or {}
            if state.get('identity') != identity:
                state = dict(identity=identity, peak=0.)
            if stamp < state.get('last_ms', 0):
                return None
            # Removed exits cannot survive a restart as pending closes.
            retired = {'EXIT_PROFIT_TIER2_LOCK', 'EXIT_BREAKEVEN_LOCK',
                       'EXIT_INTRADAY_PROFIT_DRAWDOWN_20PCT', 'EXIT_INTRADAY_ANOMALY_SPIKE',
                       'EMERGENCY_FLASH_CRASH_LONG', 'EMERGENCY_FLASH_SURGE_SHORT',
                       'EMERGENCY_GIANT_REVERSE_CANDLE', 'EMERGENCY_BTC_CRASH'}
            if state.get('pending') not in {None, 'EXIT_KC_MID_BREACH',
                                            'EXIT_INTRADAY_KC_MID_BREACH', 'EXIT_PEAK_DRAWDOWN_25PCT'}:
                state.pop('pending', None)
            if (position.get('closed_exit_state') or {}).get('reason') in retired:
                position['closed_exit_state'] = {}
            state.pop('breakeven_line', None)
            state.pop('tier2_line', None)
            if state.get('pending'):
                return dict(action='CLOSE_POSITION', type=state['pending'], price=price,
                            reason=state['pending'])
            sign = 1 if side == 'LONG' else -1
            pnl = sign*(price-entry)*qty
            state.update(last_ms=stamp, peak=max(float(state.get('peak', 0.)), pnl),
                         version=3)
            scale = float(atr or 0.)
            peak_gain = float(state.get('peak_gain_atr', 0.))
            if math.isfinite(scale) and scale > 0:
                peak_gain = max(peak_gain, sign*(price-entry)/scale)
            state['peak_gain_atr'] = peak_gain
            reached = lambda value, limit: value >= limit or math.isclose(value, limit, rel_tol=1e-12)
            position['instant_exit_state'] = state
            position['peak_pnl_usd'] = state['peak']
            position['peak_gain_atr'] = peak_gain
            position['peak_unrealized_profit_usd'] = state['peak']
            position['current_unrealized_pnl_usd'] = pnl
            reason = None
            mid = float(bar_curr.get('kc_middle') or 0.)
            if math.isfinite(mid) and mid > 0 and sign*(price-mid) < 0:
                reason = 'EXIT_KC_MID_BREACH'
            threshold = state['peak']*.75
            if reason is None and (reached(state['peak'], 10.) or reached(peak_gain, 3.)) and (
                    pnl < threshold and not math.isclose(pnl, threshold, rel_tol=1e-12)):
                reason = 'EXIT_PEAK_DRAWDOWN_25PCT'
            if reason:
                from core.services.exits.dual_track_exit_service import POLICY
                state['pending'] = reason
                position['closed_exit_state'] = dict(policy=POLICY, pending=True, reason=reason)
                return dict(action='CLOSE_POSITION', type=reason, price=price, reason=reason)
            return None
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    def check_intraday_instant_exit(self, position, current_tick_price, bar_curr, atr):
        """Compatibility adapter; only midpoint and large-profit drawdown remain."""
        decision = self.evaluate_anti_whipsaw_profit_lock(position, current_tick_price, bar_curr, atr)
        return decision['type'] if decision else None

    def evaluate_entry(self, symbol, bar_curr, bar_prev1, bar_prev2=None):
        """Compatibility entry point; all callers use the second-bar rule."""
        return self.evaluate_second_bar_outside_entry(symbol, bar_curr, bar_prev1)

    # =================================================================
    # 二、 盤中即時極端熔斷（每一秒檢查，不看收盤，立刻秒平逃命）
    # =================================================================
    def check_intra_bar_emergency_exit(self, position: Dict[str, Any], current_price: float, bar_snapshot: Dict[str, Any], btc_status: Dict[str, Any]) -> Optional[str]:
        # Legacy candle-body/BTC strategy exits are retired. Live exits use
        # observed ticks in evaluate_anti_whipsaw_profit_lock instead.
        return None

    # =================================================================
    # 三、 收盤平倉三部曲（若盤中無極端熔斷，抱到收線；滿足任一標準也必須平倉！）
    # =================================================================
    def evaluate_bar_closed_exit(self, position: Dict[str, Any], closed: Any) -> Optional[str]:
        from core.services.candle_data import closed_entry_candles
        if closed is None or 'is_closed' not in closed:
            return None
        closed = closed_entry_candles(closed)
        if len(closed) < 4 or position.get('side') not in ('LONG', 'SHORT'):
            return None
        try:
            rows = closed[['timestamp','high','low','close']].astype(float)
            if not all(math.isfinite(v) and v > 0 for v in rows.to_numpy().flat):
                return None
            if not (rows.timestamp.diff().dropna() == 60000).all():
                return None
            if not ((rows.low <= rows.close) & (rows.close <= rows.high)).all():
                return None
            current = rows.iloc[-1]
            opened = float(position.get('open_timestamp') or 0) * 1000
            if not math.isfinite(opened) or current.timestamp < opened:
                return None
            # The pivot and its right-hand confirmation precede the break bar.
            key = 'low' if position['side'] == 'LONG' else 'high'
            values = rows[key].tolist()
            for i in range(len(values)-3, 0, -1):
                pivot = values[i]
                found = (pivot < values[i-1] and pivot < values[i+1]) if key == 'low' else (pivot > values[i-1] and pivot > values[i+1])
                if found:
                    broken = current.close < pivot if key == 'low' else current.close > pivot
                    return ('EXIT_SWING_LOW_BREAK_CLOSED' if key == 'low' else 'EXIT_SWING_HIGH_BREAK_CLOSED') if broken else None
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        return None

    def evaluate_third_bar_open_entry(self, symbol: str, current_bar: Dict[str, Any], bar_prev1: Dict[str, Any], bar_prev2: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return self.evaluate_entry(symbol, current_bar, bar_prev1, bar_prev2)


V2_ENTRY_CODES = frozenset(
    f"{rule}_{side}"
    for rule in ("SECOND_BAR_OUTSIDE",)
    for side in ("LONG", "SHORT")
)


def successful_exit_ticket(account, symbol):
    """Rebuild one-use re-entry permission from persisted successful fills."""
    if account is None:
        return None
    events = []
    for trade in getattr(account, 'trades', []):
        if trade.get('symbol') != symbol or trade.get('action') not in ('OPEN_LONG','OPEN_SHORT','CLOSE_LONG','CLOSE_SHORT'):
            continue
        try:
            stamp = float(trade.get('id'))
            if math.isfinite(stamp) and stamp > 0:
                events.append((stamp, trade))
        except (TypeError,ValueError):
            continue
    if not events:
        return None
    stamp, latest = max(events, key=lambda item:(item[0], item[1]['action'].startswith('OPEN_')))
    if not latest['action'].startswith('CLOSE_'):
        return None
    return dict(exit_bar_index=int(stamp // 60000), side=latest['action'][6:])


def evaluate_v2_frame(frame, price=None, code=None, *, account=None, symbol=''):
    """Recompute V2 at every boundary; return a complete execution contract."""
    from core.services.strategies.unified_entry_strategy import confirmed
    closed = confirmed(frame)
    if closed is None:
        return None
    import numpy as np
    row = frame.iloc[-1].to_dict()
    flag = row.get('is_closed')
    if not isinstance(flag, (bool, np.bool_)) or flag:
        return None  # Never turn a closed-only snapshot into a live second bar.
    try:
        quote = float(price if price is not None else row['close'])
        original_price = float(row['close'])
        stamp = float(row['timestamp'])
        if not all(math.isfinite(v) and v > 0 for v in (quote, original_price, stamp)):
            return None
        if stamp != float(closed.iloc[-1]['timestamp']) + 60000:
            return None
        # Keep live simple moving averages consistent with the latest quote.
        for key, period in (('ma3', 3), ('ma15', 15)):
            row[key] = float(row[key]) + (quote - original_price) / period
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    strategy = PureTrendStrategyV2()
    ticket = successful_exit_ticket(account, symbol)
    bar_index = int(float(row['timestamp']) // 60000)
    if ticket:
        strategy.record_exit(symbol, ticket['side'], ticket['exit_bar_index'])
        if bar_index - ticket['exit_bar_index'] < 2:
            return None
    if code is not None and code not in V2_ENTRY_CODES:
        return None
    # New ticks extend the live extremes without mutating the source frame.
    try:
        row['high'] = max(float(row['high']), quote)
        row['low'] = min(float(row['low']), quote)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    row['close'] = quote
    previous = closed.iloc[-1].to_dict()
    # The first bar of a re-entry pair must start after the successful close bar.
    if ticket and float(previous['timestamp']) // 60000 <= ticket['exit_bar_index']:
        return None
    decision = strategy.evaluate_second_bar_outside_entry(symbol, row, previous)
    if not decision or (code is not None and decision['type'] != code):
        return None
    if not ticket:
        # The prior closed candle must be the first outside close of this episode.
        # Already-outside third/fourth bars cannot relabel themselves as bar two.
        try:
            before = closed.iloc[-2]
            edge = 'kc_upper' if decision['side'] == 'LONG' else 'kc_lower'
            before_close, before_edge = float(before['close']), float(before[edge])
            if not all(math.isfinite(v) and v > 0 for v in (before_close, before_edge)):
                return None
            if float(previous['timestamp']) - float(before['timestamp']) != 60000:
                return None
            if (before_close > before_edge if decision['side'] == 'LONG'
                    else before_close < before_edge):
                return None
        except (IndexError, KeyError, TypeError, ValueError, OverflowError):
            return None
    atr = float(closed.iloc[-1]['atr'])
    if not math.isfinite(atr) or atr <= 0:
        return None
    return dict(decision, type=code or decision['type'], price=quote, entry_atr=atr,
                confirmation_bar_id=stamp, breakout_bar_id=float(previous['timestamp']),
                close_price=float(previous['close']), intrabar=True,
                entry_phase='CONTINUATION_REENTRY' if ticket else 'INITIAL_BREAKOUT',
                exit_bar_id=ticket['exit_bar_index']*60000 if ticket else None)
