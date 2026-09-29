import logging
import math
from typing import Dict, Any, Optional

logger = logging.getLogger("PureTrendV2_Meme")

class PureTrendStrategyV2:
    """
    妖幣純淨趨勢追蹤引擎：
    1. 開倉：前根已收線同向實體站外，下一根盤中站外且 MA3／MA15 同向；反向實體不超過前根50%。
    2. 物理禁區：KC 中軌上方嚴禁開空！KC 中軌下方嚴禁開多！
    3. 盤中熔斷：一股都不賣，但遭遇大瀑布、反向巨型異常K、BTC熔斷時，盤中0.1秒秒平！
    4. 常規平倉：只在1M收線嚴格突破最近已確認峰谷時全平；無峰谷續抱。
    5. 未觸發平倉前，嚴格抱牢波段，一股都不賣！
    """
    def __init__(self):
        self.cooldown_tracker = {}

    def record_exit(self, symbol: str, side: str, current_bar_index: int):
        self.cooldown_tracker[symbol] = dict(exit_bar_index=int(current_bar_index), side=side)

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
            side, adverse = 'LONG', c_open - price
        elif p_close < p_open and p_close < p_lower and price < lower and ma3 < ma15:
            side, adverse = 'SHORT', price - c_open
        if side is None:
            return None
        limit = 0.5 * abs(p_close - p_open)
        if adverse > limit and not math.isclose(adverse, limit, rel_tol=1e-12, abs_tol=0.):
            logger.info('%s 第2根反向實體超過第1根50%%，放棄%s', symbol, side)
            return None
        return dict(side=side, type='SECOND_BAR_OUTSIDE_' + side, price=price,
                    reason='第1根已收線同向實體站外，第2根盤中站外且均線同向即開倉')

    def evaluate_entry(self, symbol, bar_curr, bar_prev1, bar_prev2=None):
        """Compatibility entry point; all callers use the second-bar rule."""
        return self.evaluate_second_bar_outside_entry(symbol, bar_curr, bar_prev1)

    # =================================================================
    # 二、 盤中即時極端熔斷（每一秒檢查，不看收盤，立刻秒平逃命）
    # =================================================================
    def check_intra_bar_emergency_exit(self, position: Dict[str, Any], current_price: float, bar_snapshot: Dict[str, Any], btc_status: Dict[str, Any]) -> Optional[str]:
        side = position['side']
        bar_open = float(bar_snapshot['open'])
        atr = float(bar_snapshot.get('atr', 0.0001))

        # 1. BTC 突發急跌大跳水熔斷
        if side == 'LONG' and btc_status.get('is_crashing', False):
            return 'EMERGENCY_BTC_CRASH'

        # 2. 盤中向下/向上突發大瀑布 (超過 1.5 ATR)
        if side == 'LONG' and (bar_open - current_price) >= 1.5 * atr:
            return 'EMERGENCY_FLASH_CRASH_LONG'
        if side == 'SHORT' and (current_price - bar_open) >= 1.5 * atr:
            return 'EMERGENCY_FLASH_SURGE_SHORT'

        # 3. 盤中反向巨型異常 K 棒 (實體超過 1.2 ATR)
        if side == 'LONG' and current_price < bar_open and (bar_open - current_price) >= 1.2 * atr:
            return 'EMERGENCY_GIANT_REVERSE_CANDLE'
        if side == 'SHORT' and current_price > bar_open and (current_price - bar_open) >= 1.2 * atr:
            return 'EMERGENCY_GIANT_REVERSE_CANDLE'

        # 未觸發極端情況：【一股都不賣，繼續持倉】
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
    row['close'] = quote
    previous = closed.iloc[-1].to_dict()
    decision = strategy.evaluate_second_bar_outside_entry(symbol, row, previous)
    if not decision or (code is not None and decision['type'] != code):
        return None
    atr = float(closed.iloc[-1]['atr'])
    if not math.isfinite(atr) or atr <= 0:
        return None
    return dict(decision, type=code or decision['type'], price=quote, entry_atr=atr,
                confirmation_bar_id=stamp, breakout_bar_id=float(previous['timestamp']),
                close_price=float(previous['close']), intrabar=True)
