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
    4. 收盤平倉：若無極端熔斷，持倉抱到收線；若滿足「平倉1/平倉2/平倉3」任一標準，收盤立即平倉！
    5. 未觸發平倉前，嚴格抱牢波段，一股都不賣！
    """
    def __init__(self):
        pass

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
        if closed is None or len(closed) < 3:
            return None

        bar_curr = closed.iloc[-1].to_dict()
        bar_prev = closed.iloc[-2].to_dict()
        side = position['side']
        unrealized_pnl = float(position.get('unrealized_pnl', 0.0))

        c_open = float(bar_curr['open'])
        c_close = float(bar_curr['close'])
        c_high = float(bar_curr['high'])
        c_low = float(bar_curr['low'])
        p_open = float(bar_prev['open'])
        p_close = float(bar_prev['close'])

        kc_upper = float(bar_curr['kc_upper'])
        kc_lower = float(bar_curr['kc_lower'])
        ma3 = float(bar_curr['ma3'])
        prev_ma3 = float(bar_curr.get('prev_ma3', float(bar_prev.get('ma3', ma3))))
        ma15 = float(bar_curr['ma15'])
        atr = float(bar_curr.get('atr', 0.0001))

        curr_body = abs(c_close - c_open)
        upper_wick = c_high - max(c_open, c_close)
        lower_wick = min(c_open, c_close) - c_low

        # 尋找最近的真實峰頂/谷底 (左右各一根確認)
        def find_swing_low():
            lows = closed['low'].values
            for i in range(len(lows)-2, 0, -1):
                if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
                    return float(lows[i])
            return None
            
        def find_swing_high():
            highs = closed['high'].values
            for i in range(len(highs)-2, 0, -1):
                if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
                    return float(highs[i])
            return None

        # -------------------------------------------------------------
        # 多單常規平倉 (LONG EXIT)
        # -------------------------------------------------------------
        if side == 'LONG':
            recent_swing_low = find_swing_low()
            # 如果找不到谷底，或是已經跌破谷底，才允許平倉
            broke_swing_low = (recent_swing_low is None) or (c_close < recent_swing_low)

            # 平倉 1：外軌 MA3 轉向 + 連續 2 根收紅陰線 + 跌破谷底
            if ma3 > kc_upper and ma3 < prev_ma3:
                if p_close < p_open and c_close < c_open and broke_swing_low:
                    return 'EXIT_1_OUTSIDE_MA3_TURN_DOWN_2_RED'

            # 平倉 2：MA3 回到通道內 + 收盤跌破 MA15 生命線 + 跌破谷底
            if ma3 <= kc_upper and c_close < ma15 and broke_swing_low:
                return 'EXIT_2_INSIDE_BREAK_MA15'

            # 平倉 3：脫離成本區後的實質賣壓
            if unrealized_pnl >= 2.0 or (c_close - kc_upper) >= 1.2 * atr:
                if upper_wick >= 2.0 * curr_body:
                    return 'EXIT_3_EXTREME_PIN_BAR_SELL'
                if c_close < c_open and curr_body >= 0.8 * atr:
                    return 'EXIT_3_ENGULFING_BEAR_SELL'

        # -------------------------------------------------------------
        # 空單常規平倉 (SHORT EXIT)
        # -------------------------------------------------------------
        if side == 'SHORT':
            recent_swing_high = find_swing_high()
            # 如果找不到峰頂，或是已經突破峰頂，才允許平倉
            broke_swing_high = (recent_swing_high is None) or (c_close > recent_swing_high)

            # 平倉 1：外軌 MA3 轉向 + 連續 2 根收綠陽線 + 突破峰頂
            if ma3 < kc_lower and ma3 > prev_ma3:
                if p_close > p_open and c_close > c_open and broke_swing_high:
                    return 'EXIT_1_OUTSIDE_MA3_TURN_UP_2_GREEN'

            # 平倉 2：MA3 回到通道內 + 收盤突破 MA15 生命線 + 突破峰頂
            if ma3 >= kc_lower and c_close > ma15 and broke_swing_high:
                return 'EXIT_2_INSIDE_BREAK_MA15'

            # 平倉 3：脫離成本區後的實質買盤承接
            if unrealized_pnl >= 2.0 or (kc_lower - c_close) >= 1.2 * atr:
                if lower_wick >= 2.0 * curr_body:
                    return 'EXIT_3_EXTREME_PIN_BAR_BUY'
                if c_close > c_open and curr_body >= 0.8 * atr:
                    return 'EXIT_3_ENGULFING_BULL_BUY'

        # 若未命中三種平倉：【繼續一股都不賣，嚴格抱單讓利潤奔跑】！
        return None

    def evaluate_third_bar_open_entry(self, symbol: str, current_bar: Dict[str, Any], bar_prev1: Dict[str, Any], bar_prev2: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return self.evaluate_entry(symbol, current_bar, bar_prev1, bar_prev2)


V2_ENTRY_CODES = frozenset(
    f"{rule}_{side}"
    for rule in ("THIRD_BAR_CONFIRMED",)
    for side in ("LONG", "SHORT")
)


def evaluate_v2_frame(frame, price=None, code=None):
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
    if code is not None and code not in V2_ENTRY_CODES:
        return None
    # Never fall back to a previous closed signal when a live third bar fails.
    row['close'] = quote
    if live:
        prev1, prev2 = closed.iloc[-1].to_dict(), closed.iloc[-2].to_dict()
    else:
        prev1, prev2 = closed.iloc[-2].to_dict(), closed.iloc[-3].to_dict()
    decision = strategy.evaluate_entry('', row, prev1, prev2)
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
