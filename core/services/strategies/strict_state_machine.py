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

        def has_insufficient_body(bar, atr_val: float) -> bool:
            body = abs(float(bar.close) - float(bar.open))
            # Retain the existing absolute body threshold, not a wick ratio.
            return body == 0 or (body < 0.30 * atr_val)

        if state == PositionState.IDLE:
            from core.services.candle_data import closed_entry_candles
            from core.services.entry_contract import entry_doji_problem
            doji_problem = entry_doji_problem(closed_entry_candles(frame), curr_bar, live_price)
            if doji_problem:
                return {"action": "WAIT", "reason": doji_problem}
            # 順勢延續開多（Re-entry）
            if ck_direction == "UP" and close_price > kc_upper:
                # 條件 1：均線強勢貼軌（Close > MA5 且 MA5 > KC_Upper），當根收實體陽棒
                trend_strong_long = (close_price > ma5) and (ma5 > kc_upper) and is_green_candle
                # 條件 2：突破前一根高點（突破新高推進行情）
                break_prev_high = (close_price > float(prev_bar.high)) and is_green_candle
                # 排除十字星：當根非十字星（動能充足）
                if (trend_strong_long or break_prev_high) and not has_insufficient_body(curr_bar, atr):
                    return {"action": "ENTER_LONG", "reason": "STRONG_TREND_RE_ENTRY"}

            # 順勢延續開空（Re-entry）
            if ck_direction == "DOWN" and close_price < kc_lower:
                # 條件 1：均線強勢貼軌
                trend_strong_short = (close_price < ma5) and (ma5 < kc_lower) and is_red_candle
                # 條件 2：突破前一根低點
                break_prev_low = (close_price < float(prev_bar.low)) and is_red_candle
                if (trend_strong_short or break_prev_low) and not has_insufficient_body(curr_bar, atr):
                    return {"action": "ENTER_SHORT", "reason": "STRONG_TREND_RE_ENTRY"}

            from core.services.entry_contract import evaluate_entry_contract
            diagnostics = {}
            entry = evaluate_entry_contract(frame, live_price, symbol=symbol, diagnostics=diagnostics)
            if entry:
                return {"action": "ENTER_" + entry['side'], "reason": entry['type']}
            return {"action": "WAIT", "reason": diagnostics.get('reason', 'UNKNOWN_WAIT')}

        elif state == PositionState.LONG:
            # 持多續抱硬性白名單
            if close_price >= ma5:
                return {"action": "WAIT", "reason": "HOLD_ABOVE_MA5"}
            # 只有當收盤價實質跌破 MA5，且當根為實體陰線時，才准執行平多
            if is_red_candle and close_price < ma5:
                return {"action": "EXIT_LONG", "reason": "CLOSED_BELOW_MA5_RED_BODY"}
            return {"action": "WAIT", "reason": "HOLDING_LONG"}

        elif state == PositionState.SHORT:
            # 持空續抱硬性白名單
            if close_price <= ma5:
                return {"action": "WAIT", "reason": "HOLD_BELOW_MA5"}
            # 只有當收盤價實質突破 MA5，且當根為實體陽線時，才准執行平空
            if is_green_candle and close_price > ma5:
                return {"action": "EXIT_SHORT", "reason": "CLOSED_ABOVE_MA5_GREEN_BODY"}
            return {"action": "WAIT", "reason": "HOLDING_SHORT"}

        return {"action": "WAIT", "reason": "UNKNOWN_STATE"}

    def evaluate_tick(self, symbol: str, frame, live_price: float, unrealized_pnl: float = 0.0):
        \"\"\"
        DEPRECATED / UNUSED: 
        This method is historically disconnected from the main loop and does not act as an exit authority.
        Exits are exclusively managed by evaluate_peak_trailing() and update_trailing_stops().
        \"\"\"
        decision = self._evaluate_tick_internal(symbol, frame, live_price, unrealized_pnl)
        
        # 紀錄平倉時間
        if decision["action"] in ("EXIT_LONG", "EXIT_SHORT"):
            curr_bar = frame.iloc[-1]
            curr_time = float(curr_bar.name if getattr(curr_bar, 'name', None) else curr_bar.get('timestamp', 0))
            self.last_exits[symbol] = curr_time
            
        return decision
