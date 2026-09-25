import math
import time

class PositionDefenseState:
    def __init__(self, entry_price: float, qty: float, side: str, snapshot_atr: float, mode: str = "TREND"):
        self.entry_price = entry_price
        self.qty = qty
        self.side = side
        self.snapshot_atr = snapshot_atr
        self.mode = mode  # "EXPLOSIVE" or "TREND"
        
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
                 stage3_trigger_atr=2.5, 
                 fee_buffer_pct=0.001): # 預設千分之一的保本手續費緩衝
        
        # 共通觸發條件
        self.stage1_trigger_atr = stage1_trigger_atr
        self.stage3_trigger_atr = stage3_trigger_atr
        self.fee_buffer_pct = fee_buffer_pct
        
        # 動態模式參數
        self.modes = {
            "EXPLOSIVE": {
                "stage2_trigger_atr": 1.5,
                "stage2_buffer_atr": 0.4,   # 極速反應模式：緩衝縮小至 0.4 ATR
                "stage3_giveback_ratio": 0.15 # 緊縮回吐 15%
            },
            "TREND": {
                "stage2_trigger_atr": 1.5,
                "stage2_buffer_atr": 1.0,   # 寬容呼吸模式：緩衝放寬至 1.0 ATR
                "stage3_giveback_ratio": 0.15 # 趨勢模式同樣保留極端崩盤防護
            }
        }

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
        已廢除「階梯鎖利/回撤平倉」邏輯。
        現在一律透過 entry_atr_protection 的 2.0 ATR 止盈 / 1.5 ATR 止損 / 中軌防守。
        """
        return {"action": "NONE"}
