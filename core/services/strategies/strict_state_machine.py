from enum import Enum
import math

from core.services.entry_contract import LONG_ENTRY_CODE, SHORT_ENTRY_CODE, ENTRY_CODES as STRICT_ENTRY_CODES

class PositionState(Enum):
    IDLE = "IDLE"
    LONG = "LONG"
    SHORT = "SHORT"

class StrictStateMachineStrategy:
    def __init__(self):
        # 記錄各幣種的當前狀態，預設為空手
        self.states = {}
        # 記錄持倉資訊（如進場價、最高浮盈）
        self.positions = {}
        # 記錄最後平倉的 timestamp，用於冷卻機制
        self.last_exits = {}

    def get_state(self, symbol: str) -> PositionState:
        return self.states.get(symbol, PositionState.IDLE)

    def set_state(self, symbol: str, state: PositionState, position_data: dict = None):
        self.states[symbol] = state
        if state == PositionState.IDLE:
            self.positions.pop(symbol, None)
        else:
            self.positions[symbol] = position_data or {}

    def _evaluate_tick_internal(self, symbol: str, frame, live_price: float, unrealized_pnl: float = 0.0):
        if frame is None or len(frame) < 3:
            return {"action": "WAIT", "reason": "DATA_INSUFFICIENT"}
        state = self.get_state(symbol)
        curr_bar = frame.iloc[-1]
        prev_bar = frame.iloc[-2]
        prev_bar2 = frame.iloc[-3]
        close_price = float(curr_bar.close)
        open_price = float(curr_bar.open)
        kc_upper = float(curr_bar.kc_upper)
        kc_lower = float(curr_bar.kc_lower)
        kc_middle = float(curr_bar.kc_middle)
        ma5 = float(curr_bar.ma5) if 'ma5' in curr_bar else float(curr_bar.ma3)
        atr = float(curr_bar.atr)
        ck_direction = "UP" if float(curr_bar.kc_middle) > float(prev_bar.kc_middle) else "DOWN"
        is_green_candle = close_price > open_price
        is_red_candle = close_price < open_price
        candle_body = abs(close_price - open_price)

        if state == PositionState.IDLE:
            def is_doji(bar, atr_val: float) -> bool:
                body = abs(float(bar.close) - float(bar.open))
                full_range = float(bar.high) - float(bar.low)
                if full_range == 0:
                    return True
                # 實體佔全棒長度小於 25%，或實體小於 0.3 * ATR
                return (body / full_range < 0.25) or (body < 0.3 * atr_val)

            # 多單破軌開倉規則（LONG ENTRY）
            if ck_direction == "UP" and close_price > kc_upper:
                # 狀況 A（標準破軌開多）
                is_first_break = (float(prev_bar.close) > kc_upper) and (float(prev_bar2.close) <= kc_upper)
                is_curr_solid_green = (not is_doji(curr_bar, atr)) and (close_price > open_price)
                if is_first_break and is_curr_solid_green:
                    return {"action": "ENTER_LONG", "reason": "VALID_OUTSIDE_KC_LONG_A"}

                # 狀況 B（第二根為十字星，第三根延續開多）
                is_prev_doji = is_doji(prev_bar, atr) and (float(prev_bar.close) > kc_upper)
                is_curr_green = close_price > open_price
                is_break_high = close_price > float(prev_bar.high)
                if is_prev_doji and is_curr_green and is_break_high:
                    return {"action": "ENTER_LONG", "reason": "VALID_OUTSIDE_KC_LONG_B"}

                # 其餘情況（當根為十字星、或未過前高）一律 RETURN WAIT，禁止開倉！
                return {"action": "WAIT", "reason": "WAIT_DOJI_CONFIRMATION_LONG"}

            # 空單破軌開倉規則（SHORT ENTRY）
            if ck_direction == "DOWN" and close_price < kc_lower:
                # 狀況 A（標準破軌開空）
                is_first_break = (float(prev_bar.close) < kc_lower) and (float(prev_bar2.close) >= kc_lower)
                is_curr_solid_red = (not is_doji(curr_bar, atr)) and (close_price < open_price)
                if is_first_break and is_curr_solid_red:
                    return {"action": "ENTER_SHORT", "reason": "VALID_OUTSIDE_KC_SHORT_A"}

                # 狀況 B（第二根為十字星，第三根延續開空）
                is_prev_doji = is_doji(prev_bar, atr) and (float(prev_bar.close) < kc_lower)
                is_curr_red = close_price < open_price
                is_break_low = close_price < float(prev_bar.low)
                if is_prev_doji and is_curr_red and is_break_low:
                    return {"action": "ENTER_SHORT", "reason": "VALID_OUTSIDE_KC_SHORT_B"}

                # 其餘情況（當根為十字星、或未破前低）一律 RETURN WAIT，禁止開倉！
                return {"action": "WAIT", "reason": "WAIT_DOJI_CONFIRMATION_SHORT"}

            return {"action": "WAIT", "reason": "NO_VALID_ENTRY"}

        # ==========================================
        # 狀態 2：多單持倉 (LONG) - 只監控出場，嚴禁開倉
        # ==========================================
        elif state == PositionState.LONG:
            pos = self.positions.get(symbol, {})
            
            # 維護最高利潤
            max_pnl = max(pos.get("max_pnl", 0.0), unrealized_pnl)
            self.positions[symbol]["max_pnl"] = max_pnl

            # 【強制續抱 (白名單)】：優先級最高，封鎖平倉 (只要滿足任一條件)
            if live_price >= kc_upper:
                return {"action": "WAIT", "reason": "HOLD_ABOVE_UPPER"}
            if is_red_candle and candle_body < 0.8 * atr:
                return {"action": "WAIT", "reason": "HOLD_SMALL_RED_BODY"}
            if live_price > kc_middle:
                # 若未觸發頂部反轉且還在安全空間，則續抱
                pass 

            # 【平多觸發 (黑名單)】：觸發即刻市價全平
            # 1. 頂部反轉 (前棒為長上影十字星 且 當前盤中實質跌破前棒最低點)
            prev_upper_shadow = float(prev_bar.high) - max(float(prev_bar.open), float(prev_bar.close))
            prev_body = abs(float(prev_bar.close) - float(prev_bar.open))
            if prev_upper_shadow > (2 * prev_body) and live_price < float(prev_bar.low):
                return {"action": "EXIT_LONG", "reason": "TOP_REVERSAL_BROKEN_LOW"}
            
            # 若在 kc_middle 之上但未觸發頂部反轉，依用戶指示安全續抱
            if live_price > kc_middle:
                return {"action": "WAIT", "reason": "HOLD_SAFE_ABOVE_MIDDLE"}
            
            # 2. 結構走壞 (只看 1m 收盤價，嚴禁盤中毛刺即時平倉)
            # 若已經跌到中軌以下，且為實體陰線
            if is_red_candle and close_price < ma5:
                return {"action": "EXIT_LONG", "reason": "STRUCTURE_BROKEN_BELOW_MA5"}
                
            # 3. 生命線破位 (無條件底線)
            if live_price < kc_middle or close_price < kc_middle:
                return {"action": "EXIT_LONG", "reason": "LIFELINE_BROKEN_KC_MID"}
                
            # 4. 利潤保護
            if max_pnl >= 15.0:
                drawdown = (max_pnl - unrealized_pnl) / max_pnl
                if drawdown >= 0.25:
                    return {"action": "EXIT_LONG", "reason": "PROFIT_PROTECT_25PCT_DRAWDOWN"}

            return {"action": "WAIT", "reason": "HOLDING_LONG"}

        # ==========================================
        # 狀態 3：空單持倉 (SHORT) - 只監控出場，嚴禁開倉
        # ==========================================
        elif state == PositionState.SHORT:
            pos = self.positions.get(symbol, {})
            
            # 維護最高利潤
            max_pnl = max(pos.get("max_pnl", 0.0), unrealized_pnl)
            self.positions[symbol]["max_pnl"] = max_pnl

            # 【空單持倉強制白名單 (封鎖平倉)】：優先級最高，嚴禁一見綠 K 就平倉
            # 條件：若即時價或收盤價仍在中軌下方、且為小綠 K (實體 < 1.0 ATR)、且未突破前一根高點，強制續抱！
            is_below_middle = live_price < kc_middle or close_price < kc_middle
            is_small_green = is_green_candle and candle_body < 1.0 * atr
            not_break_prev_high = float(curr_bar.high) < float(prev_bar.high)
            
            if is_below_middle and is_small_green and not_break_prev_high:
                return {"action": "WAIT", "reason": "MANDATORY_HOLD_SHORT_GREEN_PULLBACK"}
                
            # 原本合理的紅 K 順勢續抱保留
            if live_price <= ma5 and is_red_candle:
                return {"action": "WAIT", "reason": "MANDATORY_HOLD_SHORT_RED"}

            # 【嚴格定義「真谷底平空條件」（滿足其一才准平倉）】
            # 1. 底部反轉 (前一根為極長下影十字星，當前盤中突破前高)
            prev_lower_shadow = min(float(prev_bar.open), float(prev_bar.close)) - float(prev_bar.low)
            prev_body = abs(float(prev_bar.close) - float(prev_bar.open))
            if prev_lower_shadow > (2 * prev_body) and live_price > float(prev_bar.high):
                return {"action": "EXIT_SHORT", "reason": "TRUE_BOTTOM_REVERSAL_BROKEN_HIGH"}
                
            # 2. 生命線逆轉 (1m K 棒收盤價實質站上 KC 中軌)
            if close_price > kc_middle:
                return {"action": "EXIT_SHORT", "reason": "LIFELINE_REVERSED_KC_MID"}
                
            # 3. 利潤保護 (最高浮盈 >= 15U，回吐 >= 25%)
            if max_pnl >= 15.0:
                drawdown = (max_pnl - unrealized_pnl) / max_pnl
                if drawdown >= 0.25:
                    return {"action": "EXIT_SHORT", "reason": "PROFIT_PROTECT_25PCT_DRAWDOWN"}

            return {"action": "WAIT", "reason": "HOLDING_SHORT"}

        return {"action": "WAIT", "reason": "UNKNOWN_STATE"}

    def evaluate_tick(self, symbol: str, frame, live_price: float, unrealized_pnl: float = 0.0):
        decision = self._evaluate_tick_internal(symbol, frame, live_price, unrealized_pnl)
        
        # 紀錄平倉時間
        if decision["action"] in ("EXIT_LONG", "EXIT_SHORT"):
            curr_bar = frame.iloc[-1]
            curr_time = float(curr_bar.name if getattr(curr_bar, 'name', None) else curr_bar.get('timestamp', 0))
            self.last_exits[symbol] = curr_time
            
        return decision
