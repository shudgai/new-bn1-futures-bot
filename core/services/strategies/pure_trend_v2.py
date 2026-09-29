import logging
import math
from typing import Dict, Any, Optional

logger = logging.getLogger("PureTrendV2_Meme")

class PureTrendStrategyV2:
    """
    妖幣純淨趨勢追蹤引擎：
    1. 開倉：前兩根已收線同色站外，第三根不分顏色站外；反向實體不超過第二根50%。
    2. 物理禁區：KC 中軌上方嚴禁開空！KC 中軌下方嚴禁開多！
    3. 盤中熔斷：一股都不賣，但遭遇大瀑布、反向巨型異常K、BTC熔斷時，盤中0.1秒秒平！
    4. 常規平倉：只在1M收線嚴格突破最近已確認峰谷時全平；無峰谷續抱。
    5. 未觸發平倉前，嚴格抱牢波段，一股都不賣！
    """
    def __init__(self):
        self.cooldown_tracker = {}

    def record_exit(self, symbol: str, side: str, current_bar_index: int):
        self.cooldown_tracker[symbol] = dict(exit_bar_index=int(current_bar_index), side=side)

    def evaluate_continuation_entry(self, symbol, current_bar_index, bar_curr, bar_prev):
        ticket = self.cooldown_tracker.get(symbol)
        if not ticket or current_bar_index - ticket['exit_bar_index'] < 2:
            return None
        try:
            price, mid, upper, lower, ma3, ma15 = (float(bar_curr[k]) for k in
                ('close','kc_middle','kc_upper','kc_lower','ma3','ma15'))
            previous = float(bar_prev['close'])
            if not all(math.isfinite(v) and v > 0 for v in (price,mid,upper,lower,ma3,ma15,previous)):
                return None
            if not lower < mid < upper or not bar_prev.get('is_closed', False):
                return None
            side = None
            if price > upper and price > mid and ma3 > ma15 and price >= previous:
                side = 'LONG'
            elif price < lower and price < mid and ma3 < ma15 and price <= previous:
                side = 'SHORT'
            atr = bar_curr.get('atr') if bar_curr.get('is_closed', False) else bar_prev.get('atr')
            if side and self.verify_profitable_expectation(side,price,bar_curr,atr):
                return dict(side=side,type='CONTINUATION_RE_ENTRY_'+side,price=price,
                            reason='平倉冷卻2根後，軌外強勢延續開倉')
        except (KeyError,TypeError,ValueError,OverflowError):
            return None
        return None

    def verify_profitable_expectation(self, side: str, entry_price: float, bar_curr: Dict[str, Any], atr: float) -> bool:
        """Estimate net reward/risk; this does not change the actual exit policy."""
        try:
            entry_price, atr = float(entry_price), float(atr)
            kc_upper = float(bar_curr['kc_upper'])
            kc_lower = float(bar_curr['kc_lower'])
            ma15 = float(bar_curr['ma15'])
        except (KeyError, TypeError, ValueError, OverflowError):
            logger.info('🚫 [預估虧損攔截] 盈虧估算資料無效')
            return False
        if (side not in ('LONG', 'SHORT') or
                not all(math.isfinite(v) and v > 0 for v in
                        (entry_price, atr, kc_upper, kc_lower, ma15)) or
                kc_lower >= kc_upper):
            logger.info('🚫 [預估虧損攔截] 方向、價格、ATR 或通道資料無效')
            return False
        fee_and_slippage_cost = entry_price * 0.003
        if side == 'LONG':
            stop_price = max(kc_upper - 0.2 * atr, ma15)
            distance = entry_price - stop_price
        else:
            stop_price = min(kc_lower + 0.2 * atr, ma15)
            distance = stop_price - entry_price
        risk = max(distance, fee_and_slippage_cost) + fee_and_slippage_cost
        # Algebraically equal to target minus entry; avoids low-price cancellation.
        reward = 1.5 * atr - fee_and_slippage_cost
        if not all(math.isfinite(v) for v in (risk, reward)) or risk <= 0:
            return False
        ratio = reward / risk
        if reward <= 0 or ratio < 1.2:
            logger.info('🚫 [預估虧損攔截] %s 預期空間不足 (Reward=%.12g, Risk=%.12g, R:R=%.6f)，放棄開倉！',
                        side, reward, risk, ratio)
            return False
        return True

    def evaluate_entry(self, symbol: str, bar_curr: Dict[str, Any], bar_prev1: Dict[str, Any], bar_prev2: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Two closed same-color outside bars, then the third bar only."""
        import numpy as np
        for bar in (bar_prev2, bar_prev1):
            flag = bar.get('is_closed', bar.get('x', False))
            if not isinstance(flag, (bool, np.bool_)) or not flag:
                return None
        try:
            p2_open, p2_close = float(bar_prev2['open']), float(bar_prev2['close'])
            p1_open, p1_close = float(bar_prev1['open']), float(bar_prev1['close'])
            c_open, price = float(bar_curr['open']), float(bar_curr['close'])
            upper, lower, mid = (float(bar_curr[k]) for k in ('kc_upper','kc_lower','kc_middle'))
            values = (p2_open,p2_close,p1_open,p1_close,c_open,price,upper,lower,mid,
                      float(bar_prev2['kc_upper']),float(bar_prev2['kc_lower']),
                      float(bar_prev1['kc_upper']),float(bar_prev1['kc_lower']))
            if not all(math.isfinite(v) and v > 0 for v in values) or not lower < mid < upper:
                return None
            stamps = [float(bar['timestamp']) for bar in (bar_prev2,bar_prev1,bar_curr)]
            if not all(math.isfinite(v) for v in stamps) or stamps[1]-stamps[0] != 60000 or stamps[2]-stamps[1] != 60000:
                return None
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        side = None
        if (price > upper and price > mid and
                p2_close > p2_open and p2_close > float(bar_prev2['kc_upper']) and
                p1_close > p1_open and p1_close > float(bar_prev1['kc_upper'])):
            side = 'LONG'
            adverse_body = c_open - price
        elif (price < lower and price < mid and
                p2_close < p2_open and p2_close < float(bar_prev2['kc_lower']) and
                p1_close < p1_open and p1_close < float(bar_prev1['kc_lower'])):
            side = 'SHORT'
            adverse_body = price - c_open
        if side is None:
            return None
        if adverse_body > 0.5 * abs(p1_close-p1_open):
            logger.info('%s 第3根反向實體超過第2根實體50%%，放棄%s', symbol, side)
            return None
        atr = bar_curr.get('atr') if bar_curr.get('is_closed', bar_curr.get('x',False)) else bar_prev1.get('atr')
        if not self.verify_profitable_expectation(side, price, bar_curr, atr):
            return None
        return dict(side=side, type='THIRD_BAR_CONFIRMED_'+side, price=price,
                    reason='第1根破軌+第2根同色站外+第3根不分顏色站外開倉')

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
    for rule in ("THIRD_BAR_CONFIRMED", "CONTINUATION_RE_ENTRY")
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
    live = not bool(frame.iloc[-1].get('is_closed', False))
    row = frame.iloc[-1].to_dict()
    quote = float(price if price is not None else row['close'])
    if not math.isfinite(quote) or quote <= 0:
        return None
    for key in ('open', 'close', 'kc_upper', 'kc_middle', 'kc_lower'):
        if not math.isfinite(float(row[key])) or float(row[key]) <= 0:
            return None
    if not float(row['kc_lower']) < float(row['kc_middle']) < float(row['kc_upper']):
        return None
    if live and float(row['timestamp']) != float(closed.iloc[-1]['timestamp']) + 60000:
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
    # Never fall back to a previous closed signal when a live third bar fails.
    row['close'] = quote
    if live:
        prev1, prev2 = closed.iloc[-1].to_dict(), closed.iloc[-2].to_dict()
    else:
        prev1, prev2 = closed.iloc[-2].to_dict(), closed.iloc[-3].to_dict()
    decision = strategy.evaluate_entry(symbol, row, prev1, prev2)
    if decision is None:
        decision = strategy.evaluate_continuation_entry(symbol, bar_index, row, prev1)
    intrabar = live
    if not decision or (code is not None and decision['type'] != code):
        return None
    if decision['side'] == 'LONG' and quote <= float(row['kc_upper']):
        return None
    if decision['side'] == 'SHORT' and quote >= float(row['kc_lower']):
        return None
    atr = float(closed.iloc[-1]['atr'])
    if not math.isfinite(atr) or atr <= 0:
        return None
    if not strategy.verify_profitable_expectation(decision['side'], quote, row, atr):
        return None
    return dict(decision, type=code or decision['type'], entry_atr=atr,
                confirmation_bar_id=float(row['timestamp'] if intrabar else closed.iloc[-1]['timestamp']),
                close_price=float(closed.iloc[-1]['close']), intrabar=intrabar)
