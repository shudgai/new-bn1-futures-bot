import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class PureTrendV2Strategy:
    def evaluate_entry(self, symbol: str, bar_curr: Dict[str, Any], bar_prev1: Dict[str, Any], bar_prev2: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        # 1. 物理收盤校驗：盤中未收線絕不開倉
        if not bar_curr.get('x', False) and not bar_curr.get('is_closed', False):
            return None

        c_open = float(bar_curr['open'])
        c_close = float(bar_curr['close'])
        c_high = float(bar_curr['high'])
        c_low = float(bar_curr['low'])
        kc_upper = float(bar_curr['kc_upper'])
        kc_lower = float(bar_curr['kc_lower'])
        kc_mid = float(bar_curr['kc_middle'])

        # 前兩根的開收盤價
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
        # 多單開倉：連續兩根陽線破上軌，第三根收盤開多
        # -------------------------------------------------------------
        if c_close > kc_mid:  # 物理禁區：KC 中軌上方才評估開多
            # 檢查前兩根是否為連續破上軌的陽線實體
            p2_is_green_break = (p2_close > p2_open) and (p2_close > p2_kc_upper)
            p1_is_green_break = (p1_close > p1_open) and (p1_close > p1_kc_upper)

            if p2_is_green_break and p1_is_green_break:
                # 來到第三根收盤點 (bar_curr)：
                # 1. 第三根若為反向陰線 (close <= open)：徹底作廢拒絕開倉！
                if c_close <= c_open:
                    logger.info(f"{symbol} 破軌第三根收紅陰線，動能遇阻，作廢開多！")
                    return None

                # 2. 第三根若收盤落回 KC 上軌以內：突破力度不足，作廢開多！
                if c_close <= kc_upper:
                    logger.info(f"{symbol} 破軌第三根收盤跌回上軌內，作廢開多！")
                    return None

                # 3. 妖幣防高位天線：上影線不超過實體 1.5 倍
                if upper_wick > 1.5 * curr_body:
                    logger.info(f"{symbol} 破軌第三根上影線過長，防墓碑針，作廢開多！")
                    return None

                # 通過驗證：第三根收盤瞬間立即開多！
                return {
                    'side': 'LONG',
                    'type': 'THREE_BAR_BREAKOUT_LONG',
                    'price': c_close,
                    'reason': '前兩根陽線破軌確立，第三根收陽確認開多'
                }

        # -------------------------------------------------------------
        # 空單開倉：連續兩根陰線破下軌，第三根收盤開空
        # -------------------------------------------------------------
        if c_close < kc_mid:  # 物理禁區：KC 中軌下方才評估開空
            # 檢查前兩根是否為連續破下軌的陰線實體
            p2_is_red_break = (p2_close < p2_open) and (p2_close < p2_kc_lower)
            p1_is_red_break = (p1_close < p1_open) and (p1_close < p1_kc_lower)

            if p2_is_red_break and p1_is_red_break:
                # 來到第三根收盤點 (bar_curr)：
                # 1. 第三根若為反向陽線 (close >= open)：徹底作廢拒絕開空！
                if c_close >= c_open:
                    logger.info(f"{symbol} 破軌第三根收綠陽線，買盤承接，作廢開空！")
                    return None

                # 2. 第三根若收盤彈回 KC 下軌以內：突破力度不足，作廢開空！
                if c_close >= kc_lower:
                    logger.info(f"{symbol} 破軌第三根收盤回彈至下軌內，作廢開空！")
                    return None

                # 3. 妖幣防低位長針：下影線不超過實體 1.5 倍
                if lower_wick > 1.5 * curr_body:
                    logger.info(f"{symbol} 破軌第三根下影線過長，防插針反彈，作廢開空！")
                    return None

                # 通過驗證：第三根收盤瞬間立即開空！
                return {
                    'side': 'SHORT',
                    'type': 'THREE_BAR_BREAKOUT_SHORT',
                    'price': c_close,
                    'reason': '前兩根陰線破軌確立，第三根收陰確認開空'
                }

        return None
