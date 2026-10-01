from enum import Enum
import math

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

    def get_state(self, symbol: str) -> PositionState:
        return self.states.get(symbol, PositionState.IDLE)

    def set_state(self, symbol: str, state: PositionState, position_data: dict = None):
        self.states[symbol] = state
        if state == PositionState.IDLE:
            self.positions.pop(symbol, None)
        else:
            self.positions[symbol] = position_data or {}

    def evaluate_tick(self, symbol: str, frame, live_price: float, unrealized_pnl: float = 0.0):
        """
        全域唯一評估入口：每次 K 線更新或逐筆報價 (Tick) 時呼叫。
        完全杜絕狀態重疊，IDLE 絕不平倉，LONG/SHORT 絕不開倉。
        """
        if frame is None or len(frame) < 2:
            return {"action": "WAIT", "reason": "DATA_INSUFFICIENT"}

        state = self.get_state(symbol)
        
        # 取得當前（未完全收盤或剛收盤）與上一根 K 棒
        curr_bar = frame.iloc[-1]
        prev_bar = frame.iloc[-2]
        
        close_price = float(curr_bar.close)
        open_price = float(curr_bar.open)
        kc_upper = float(curr_bar.kc_upper)
        kc_lower = float(curr_bar.kc_lower)
        kc_middle = float(curr_bar.kc_middle)
        
        # 相容不同 MA 命名，以 MA5 為主
        ma5 = float(curr_bar.ma5) if 'ma5' in curr_bar else float(curr_bar.ma3)
        atr = float(curr_bar.atr)

        # 趨勢與實體判定
        ck_direction = "UP" if float(curr_bar.kc_middle) > float(prev_bar.kc_middle) else "DOWN"
        is_green_candle = close_price > open_price
        is_red_candle = close_price < open_price
        candle_body = abs(close_price - open_price)

        # ==========================================
        # 狀態 1：空手 (IDLE) - 監控首發突破 與 順勢延續開倉
        # ==========================================
        if state == PositionState.IDLE:
            # 1. 通道內部絕對靜默 (以絕對數值比對)
            if (kc_lower <= close_price <= kc_upper) and (kc_lower <= live_price <= kc_upper):
                return {"action": "WAIT", "reason": "SILENCE_INSIDE_CHANNEL"}

            # 2. 多單【首發突破】或【順勢延續補單】
            if ck_direction == "UP" and is_green_candle:
                is_above_upper = (close_price > kc_upper) or (live_price > kc_upper)
                if is_above_upper:
                    # A. 首發破軌（前一根還在軌內，這根突破）
                    if float(prev_bar.close) <= float(prev_bar.kc_upper):
                        return {"action": "ENTER_LONG", "reason": "LONG_BREAKOUT_FIRST"}
                    # B. 順勢延續補多（前段已在軌外，當前價 > MA5 且突破前高）
                    elif live_price > ma5 and live_price > float(prev_bar.high):
                        return {"action": "ENTER_LONG", "reason": "LONG_CONTINUATION"}

            # 3. 空單【首發突破】或【順勢延續補單】
            if ck_direction == "DOWN" and is_red_candle:
                is_below_lower = (close_price < kc_lower) or (live_price < kc_lower)
                if is_below_lower:
                    # A. 首發破軌（前一根還在軌內，這根跌破）
                    if float(prev_bar.close) >= float(prev_bar.kc_lower):
                        return {"action": "ENTER_SHORT", "reason": "SHORT_BREAKOUT_FIRST"}
                    # B. 順勢延續補空（前段已在軌外，當前價 < MA5 且跌破前低）
                    elif live_price < ma5 and live_price < float(prev_bar.low):
                        return {"action": "ENTER_SHORT", "reason": "SHORT_CONTINUATION"}

            return {"action": "WAIT", "reason": "NO_VALID_ENTRY"}

        # ==========================================
        # 狀態 2：多單持倉 (LONG) - 只監控出場，嚴禁開倉
        # ==========================================
        elif state == PositionState.LONG:
            pos = self.positions.get(symbol, {})
            
            # 維護最高利潤
            max_pnl = max(pos.get("max_pnl", 0.0), unrealized_pnl)
            self.positions[symbol]["max_pnl"] = max_pnl

            # 【強制續抱 (白名單)】：優先級最高，封鎖平倉
            if live_price >= ma5:
                if is_green_candle or (is_red_candle and candle_body < 0.8 * atr):
                    return {"action": "WAIT", "reason": "MANDATORY_HOLD_LONG"}

            # 【平多觸發 (黑名單)】：觸發即刻市價全平
            # 1. 頂部反轉
            prev_upper_shadow = float(prev_bar.high) - max(float(prev_bar.open), float(prev_bar.close))
            prev_body = abs(float(prev_bar.close) - float(prev_bar.open))
            if prev_upper_shadow > (2 * prev_body) and live_price < float(prev_bar.low):
                return {"action": "EXIT_LONG", "reason": "TOP_REVERSAL_BROKEN_LOW"}
            
            # 2. 結構走壞
            if close_price < ma5:
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
