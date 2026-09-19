import math
import time

class PositionDefenseState:
    def __init__(self, entry_price: float, qty: float, side: str, snapshot_atr: float):
        self.entry_price = entry_price
        self.qty = qty
        self.side = side
        self.snapshot_atr = snapshot_atr
        
        # 0: 未啟動, 1: 保本, 2: 階梯鎖利, 3: 極限防禦
        self.stage = 0
        
        # 當前的止損線 (預設為0，代表未設定)
        self.current_sl_price = 0.0
        
        # 歷史最高利潤 (單位: ATR 倍數)
        self.highest_pnl_atr = 0.0
        
        # 歷史最高淨利 (單位: 百分比)
        self.highest_pnl_pct = 0.0

class TieredExitManager:
    def __init__(self, 
                 stage1_trigger_atr=0.75, 
                 stage2_trigger_atr=1.5, 
                 stage2_buffer_atr=0.7, 
                 stage3_trigger_atr=2.5, 
                 stage3_giveback_ratio=0.15,
                 fee_buffer_pct=0.001): # 預設千分之一的保本手續費緩衝
        
        self.stage1_trigger_atr = stage1_trigger_atr
        self.stage2_trigger_atr = stage2_trigger_atr
        self.stage2_buffer_atr = stage2_buffer_atr
        self.stage3_trigger_atr = stage3_trigger_atr
        self.stage3_giveback_ratio = stage3_giveback_ratio
        self.fee_buffer_pct = fee_buffer_pct

    def _ratchet_sl(self, state: PositionDefenseState, new_sl: float) -> bool:
        """
        棘輪機制 (Ratchet Invariant): 強制多單 SL 只升不降，空單 SL 只降不升。
        如果新的 SL 更有利，則更新 state 並回傳 True；否則回傳 False。
        """
        if state.current_sl_price <= 0:
            state.current_sl_price = new_sl
            return True
            
        if state.side == "LONG":
            if new_sl > state.current_sl_price + state.entry_price * 1e-6:
                state.current_sl_price = new_sl
                return True
        elif state.side == "SHORT":
            if new_sl < state.current_sl_price - state.entry_price * 1e-6:
                state.current_sl_price = new_sl
                return True
                
        return False

    def update_tick(self, state: PositionDefenseState, live_price: float) -> dict:
        """
        快反應通道 (Fast Path): 每秒/每 Tick 呼叫。
        回傳值: {"action": "NONE" | "UPDATE_SL" | "MARKET_EXIT", "sl_price": float, "reason": str}
        """
        if state.snapshot_atr <= 0:
            return {"action": "NONE"}

        # 計算當前利潤 (價格差)
        if state.side == "LONG":
            pnl_price = live_price - state.entry_price
        else:
            pnl_price = state.entry_price - live_price
            
        current_pnl_atr = pnl_price / state.snapshot_atr
        current_pnl_pct = pnl_price / state.entry_price
        
        if current_pnl_atr > state.highest_pnl_atr:
            state.highest_pnl_atr = current_pnl_atr
            
        if current_pnl_pct > state.highest_pnl_pct:
            state.highest_pnl_pct = current_pnl_pct

        # ---------------------------------------------------------
        # Stage 3: 極限防禦 (Highest PnL >= 2.5 ATR)
        # ---------------------------------------------------------
        if state.highest_pnl_atr >= self.stage3_trigger_atr:
            if state.stage < 3:
                state.stage = 3
            
            # 動態比例回吐
            giveback_ratio = (state.highest_pnl_pct - current_pnl_pct) / state.highest_pnl_pct if state.highest_pnl_pct > 0 else 0.0
            if current_pnl_pct > 0 and giveback_ratio >= self.stage3_giveback_ratio:
                return {
                    "action": "MARKET_EXIT", 
                    "reason": f"極限防禦觸發 (峰值縮水 >= {self.stage3_giveback_ratio:.0%})"
                }

        # ---------------------------------------------------------
        # Stage 2: 階梯式鎖利 (Highest PnL >= 1.5 ATR)
        # ---------------------------------------------------------
        if state.highest_pnl_atr >= self.stage2_trigger_atr:
            if state.stage < 2:
                state.stage = 2
            
            # 防禦線 = 最高價 - 0.7 ATR
            if state.side == "LONG":
                highest_price = state.entry_price + (state.highest_pnl_atr * state.snapshot_atr)
                new_sl = highest_price - (self.stage2_buffer_atr * state.snapshot_atr)
            else:
                lowest_price = state.entry_price - (state.highest_pnl_atr * state.snapshot_atr)
                new_sl = lowest_price + (self.stage2_buffer_atr * state.snapshot_atr)
                
            if self._ratchet_sl(state, new_sl):
                return {
                    "action": "UPDATE_SL",
                    "sl_price": state.current_sl_price,
                    "reason": "第二階段階梯鎖利更新"
                }

        # ---------------------------------------------------------
        # Stage 1: 保本鎖利 (Highest PnL >= 0.75 ATR)
        # ---------------------------------------------------------
        if state.highest_pnl_atr >= self.stage1_trigger_atr and state.stage < 2:
            if state.stage < 1:
                state.stage = 1
                
            # 防禦線 = 開倉價 + 手續費緩衝 (保本)
            if state.side == "LONG":
                new_sl = state.entry_price * (1.0 + self.fee_buffer_pct)
            else:
                new_sl = state.entry_price * (1.0 - self.fee_buffer_pct)
                
            if self._ratchet_sl(state, new_sl):
                return {
                    "action": "UPDATE_SL",
                    "sl_price": state.current_sl_price,
                    "reason": "第一階段保本鎖利"
                }

        return {"action": "NONE"}
