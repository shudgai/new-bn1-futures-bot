import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("PureTrendV2_Meme")

class PureTrendStrategyV2:
    """
    妖幣純淨趨勢追蹤引擎：
    1. 開倉：破軌前兩根同色確立，第三根收盤同色才開倉（第三根若反向則作廢，絕對不開倉）。
    2. 物理禁區：KC 中軌上方嚴禁開空！KC 中軌下方嚴禁開多！
    3. 盤中熔斷：一股都不賣，但遭遇大瀑布、反向巨型異常K、BTC熔斷時，盤中0.1秒秒平！
    4. 收盤平倉：若無極端熔斷，持倉抱到收線；若滿足「平倉1/平倉2/平倉3」任一標準，收盤立即平倉！
    5. 未觸發平倉前，嚴格抱牢波段，一股都不賣！
    """
    def __init__(self):
        pass

    # =================================================================
    # 一、 開倉主入口（只在 1M 收線確定時評估）
    # =================================================================
    def evaluate_entry(self, symbol: str, bar_curr: Dict[str, Any], bar_prev1: Dict[str, Any], bar_prev2: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        # 1. 物理收線校驗：未收盤絕不開倉
        if not bar_curr.get('x', False) and not bar_curr.get('is_closed', False):
            return None

        c_open = float(bar_curr['open'])
        c_close = float(bar_curr['close'])
        c_high = float(bar_curr['high'])
        c_low = float(bar_curr['low'])
        kc_upper = float(bar_curr['kc_upper'])
        kc_lower = float(bar_curr['kc_lower'])
        kc_mid = float(bar_curr['kc_middle'])
        ma3 = float(bar_curr['ma3'])
        prev_ma3 = float(bar_curr.get('prev_ma3', ma3))
        ma15 = float(bar_curr['ma15'])

        p1_open = float(bar_prev1['open'])
        p1_close = float(bar_prev1['close'])
        p1_kc_upper = float(bar_prev1['kc_upper'])
        p1_kc_lower = float(bar_prev1['kc_lower'])

        p2_open = float(bar_prev2['open'])
        p2_close = float(bar_prev2['close'])
        p2_kc_upper = float(bar_prev2['kc_upper'])
        p2_kc_lower = float(bar_prev2['kc_lower'])

        curr_body = abs(c_close - c_open)
        upper_wick = c_high - max(c_open, c_close)
        lower_wick = min(c_open, c_close) - c_low

        # -------------------------------------------------------------
        # 多單開倉判定 (LONG ENTRY)
        # -------------------------------------------------------------
        if c_close > kc_mid:  # 物理禁區：中軌上方才考慮開多
            p2_is_green_break = (p2_close > p2_open) and (p2_close > p2_kc_upper)
            p1_is_green_break = (p1_close > p1_open) and (p1_close > p1_kc_upper)

            # 【A. 破軌雙同色開多：前兩根陽線破軌，第三根收盤確認開多】
            if p2_is_green_break and p1_is_green_break:
                # 第三根收紅陰線：徹底作廢，嚴禁開倉！
                if c_close <= c_open:
                    logger.info(f"{symbol} 破軌第三根收紅K陰線，起爆作廢！")
                    return None
                if c_close <= kc_upper:
                    logger.info(f"{symbol} 破軌第三根跌回上軌內，作廢開多！")
                    return None
                if upper_wick > 1.5 * curr_body:
                    logger.info(f"{symbol} 破軌第三根上影線過長，防墓碑針，作廢開多！")
                    return None

                return {'side': 'LONG', 'type': 'THREE_BAR_BREAKOUT_LONG', 'price': c_close, 'reason': '雙陽破上軌，第三根收陽確認開多'}

            # 【B. 延續開多 (CONTINUATION_LONG)】
            # 條件：收盤確立、收在 KC 上軌外側、當根為陽線、MA3 順勢向上
            if c_close > kc_upper:
                if c_close > c_open:  # 當根收陽
                    if ma3 > ma15 and upper_wick <= 1.2 * curr_body:
                        return {
                            'side': 'LONG',
                            'type': 'CONTINUATION_LONG',
                            'price': c_close,
                            'reason': '上軌外順勢暴漲，陽線延續開多'
                        }

        # -------------------------------------------------------------
        # 空單開倉判定 (SHORT ENTRY)
        # -------------------------------------------------------------
        if c_close < kc_mid:  # 物理禁區：中軌下方才考慮開空
            p2_is_red_break = (p2_close < p2_open) and (p2_close < p2_kc_lower)
            p1_is_red_break = (p1_close < p1_open) and (p1_close < p1_kc_lower)

            # 【A. 破軌雙同色開空：前兩根陰線破軌，第三根收盤確認開空】
            if p2_is_red_break and p1_is_red_break:
                # 第三根收綠陽線：徹底作廢，嚴禁開倉！
                if c_close >= c_open:
                    logger.info(f"{symbol} 破軌第三根收綠K陽線，起爆作廢！")
                    return None
                if c_close >= kc_lower:
                    logger.info(f"{symbol} 破軌第三根彈回下軌內，作廢開空！")
                    return None
                if lower_wick > 1.5 * curr_body:
                    logger.info(f"{symbol} 破軌第三根下影線過長，防插針反彈，作廢開空！")
                    return None

                return {'side': 'SHORT', 'type': 'THREE_BAR_BREAKOUT_SHORT', 'price': c_close, 'reason': '雙陰破下軌，第三根收陰確認開空'}

            # 【B. 延續開空 (CONTINUATION_SHORT)】
            # 條件：收盤確立、收在 KC 下軌外側、當根為陰線、MA3 順勢向下
            if c_close < kc_lower:
                if c_close < c_open:  # 當根收陰
                    if ma3 < ma15 and lower_wick <= 1.2 * curr_body:
                        return {
                            'side': 'SHORT',
                            'type': 'CONTINUATION_SHORT',
                            'price': c_close,
                            'reason': '下軌外順勢暴跌，陰線延續開空'
                        }

        return None

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
    def evaluate_bar_closed_exit(self, position: Dict[str, Any], bar_curr: Dict[str, Any], bar_prev: Dict[str, Any]) -> Optional[str]:
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
        prev_ma3 = float(bar_curr.get('prev_ma3', ma3))
        ma15 = float(bar_curr['ma15'])
        atr = float(bar_curr.get('atr', 0.0001))

        curr_body = abs(c_close - c_open)
        upper_wick = c_high - max(c_open, c_close)
        lower_wick = min(c_open, c_close) - c_low

        # -------------------------------------------------------------
        # 多單常規平倉 (LONG EXIT)
        # -------------------------------------------------------------
        if side == 'LONG':
            # 平倉 1：外軌 MA3 轉向 + 連續 2 根收紅陰線
            if ma3 > kc_upper and ma3 < prev_ma3:
                if p_close < p_open and c_close < c_open:
                    return 'EXIT_1_OUTSIDE_MA3_TURN_DOWN_2_RED'

            # 平倉 2：MA3 回到通道內 + 收盤跌破 MA15 生命線
            if ma3 <= kc_upper and c_close < ma15:
                return 'EXIT_2_INSIDE_BREAK_MA15'

            # 平倉 3：脫離成本區後的實質賣壓（浮盈充足時，出現 >= 2.0 倍超長上影或實體大陰線吞沒）
            if unrealized_pnl >= 2.0 or (c_close - kc_upper) >= 1.2 * atr:
                if upper_wick >= 2.0 * curr_body:
                    return 'EXIT_3_EXTREME_PIN_BAR_SELL'
                if c_close < c_open and curr_body >= 0.8 * atr:
                    return 'EXIT_3_ENGULFING_BEAR_SELL'

        # -------------------------------------------------------------
        # 空單常規平倉 (SHORT EXIT)
        # -------------------------------------------------------------
        if side == 'SHORT':
            # 平倉 1：外軌 MA3 轉向 + 連續 2 根收綠陽線
            if ma3 < kc_lower and ma3 > prev_ma3:
                if p_close > p_open and c_close > c_open:
                    return 'EXIT_1_OUTSIDE_MA3_TURN_UP_2_GREEN'

            # 平倉 2：MA3 回到通道內 + 收盤突破 MA15 生命線
            if ma3 >= kc_lower and c_close > ma15:
                return 'EXIT_2_INSIDE_BREAK_MA15'

            # 平倉 3：脫離成本區後的實質買盤承接（浮盈充足時，出現 >= 2.0 倍超長下影或實體大陽線反包）
            if unrealized_pnl >= 2.0 or (kc_lower - c_close) >= 1.2 * atr:
                if lower_wick >= 2.0 * curr_body:
                    return 'EXIT_3_EXTREME_PIN_BAR_BUY'
                if c_close > c_open and curr_body >= 0.8 * atr:
                    return 'EXIT_3_ENGULFING_BULL_BUY'

        # 若未命中三種平倉：【繼續一股都不賣，嚴格抱單讓利潤奔跑】！
        return None

    def evaluate_third_bar_intra_entry(self, symbol: str, current_bar: Dict[str, Any], bar_prev1: Dict[str, Any], bar_prev2: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        前兩根 (Bar 1, Bar 2) 必須是已經【完全收盤】的破軌實體棒。
        當前根 (Bar 3) 處於【盤中即時狀態 (kline['x'] 可以為 False)】：
        只要盤中確認同色 (多單現價>開盤價，空單現價<開盤價) 且在軌道外，立刻開倉！
        """
        # 確保前兩根是已收盤且數據齊全
        p1_open = float(bar_prev1['open'])
        p1_close = float(bar_prev1['close'])
        p1_upper = float(bar_prev1['kc_upper'])
        p1_lower = float(bar_prev1['kc_lower'])

        p2_open = float(bar_prev2['open'])
        p2_close = float(bar_prev2['close'])
        p2_upper = float(bar_prev2['kc_upper'])
        p2_lower = float(bar_prev2['kc_lower'])

        # 當前第三根即時盤中數值
        c_open = float(current_bar['open'])
        c_price = float(current_bar['close'])  # 即時 Tick 現價
        kc_upper = float(current_bar['kc_upper'])
        kc_lower = float(current_bar['kc_lower'])
        kc_mid = float(current_bar['kc_middle'])

        # -------------------------------------------------------------
        # 多單判定 (LONG)：前兩根收陽破上軌，第三根盤中只要是陽線立刻開多
        # -------------------------------------------------------------
        if c_price > kc_mid:
            p2_is_green_break = (p2_close > p2_open) and (p2_close > p2_upper)
            p1_is_green_break = (p1_close > p1_open) and (p1_close > p1_upper)

            if p2_is_green_break and p1_is_green_break:
                # 第三根盤中：現價大於開盤價 (即時綠陽) 且 在上軌外側
                if c_price > c_open and c_price > kc_upper:
                    return {
                        'side': 'LONG',
                        'type': 'THIRD_BAR_INTRA_LONG',
                        'price': c_price,
                        'reason': '前雙陽破上軌確立，第三根盤中綠陽即刻開多'
                    }

        # -------------------------------------------------------------
        # 空單判定 (SHORT)：前兩根收陰破下軌，第三根盤中只要是陰線立刻開空
        # -------------------------------------------------------------
        if c_price < kc_mid:
            p2_is_red_break = (p2_close < p2_open) and (p2_close < p2_lower)
            p1_is_red_break = (p1_close < p1_open) and (p1_close < p1_lower)

            if p2_is_red_break and p1_is_red_break:
                # 第三根盤中：現價小於開盤價 (即時紅陰) 且 在下軌外側
                if c_price < c_open and c_price < kc_lower:
                    return {
                        'side': 'SHORT',
                        'type': 'THIRD_BAR_INTRA_SHORT',
                        'price': c_price,
                        'reason': '前雙陰破下軌確立，第三根盤中紅陰即刻開空'
                    }

        return None
