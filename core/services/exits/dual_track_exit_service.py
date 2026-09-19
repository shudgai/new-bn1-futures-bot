import logging
from typing import Dict, Any, Optional
import pandas as pd
from core.interfaces.exit_interface import IExitStrategy

DUAL_TRACK_STATE_KEYS = ["trade_phase", "v8_reason", "v10_phase_trailing", "has_warning_partial_close",
                          "last_evaluated_closed_bar_id", "super_trend_mode", "super_trend_trailing_stop"]

logger = logging.getLogger("DualTrackExit")

# 統一 ATR 門檻常數
HARD_STOP_ATR        = 1.5   # 絕對保命線 (層級一)
SPECIAL_K_ATR        = 2.0   # 特例 K 反向實體 (層級二)
HIGH_PROFIT_ATR      = 2.0   # 盤中高利潤逃生門檻 (層級二)

class DualTrackExitStrategy(IExitStrategy):
    """
    雙軌平倉策略 v8 — 龍蝦武裝防禦架構
    
    層級一 (絕對保命): 1.5 ATR 硬停損。
    層級二 (移動止盈): 依據品種動態給予啟動與追蹤距離 (PEPE 3/2 ATR，標準 2/1.5 ATR)。
    層級三 (真實峰谷平倉): MA3 轉彎，且 (MA15 同步轉彎 或 RSI 進入極端區) 時市價平倉。
    """

    def initialize_position(self, position: Dict[str, Any], entry_price: float, atr: float) -> None:
        """
        強制狀態清空 (State Purge):
        確保新開倉的交易絕對不受舊交易殘留狀態干擾。
        """
        side = position.get("side", "LONG")
        defense_line = (entry_price - HARD_STOP_ATR * atr) if side == "LONG" else (entry_price + HARD_STOP_ATR * atr)
        
        position["defense_line"] = defense_line
        position["active_stop_price"] = defense_line
        position["highest_price"] = entry_price
        position["lowest_price"] = entry_price
        position["is_trailing_active"] = False
        position["last_evaluated_closed_bar_id"] = None
        
        logger.info(f"[STATE_PURGE] Position initialized. Hard Stop: {defense_line:.6f}")

    def evaluate_exit(self, position: Dict[str, Any], frame: pd.DataFrame,
                      current_price: float, **kwargs) -> Optional[str]:
        if frame is None or len(frame) < 3:
            return None

        side        = position.get("side", "LONG")
        entry_price = float(position.get("entry_price", 0.0))
        symbol      = position.get("symbol", "")
        if entry_price <= 0:
            return None

        curr   = frame.iloc[-1]
        prev_1 = frame.iloc[-2]

        atr = float(prev_1.get("atr", 1.0))
        if atr <= 0:
            atr = entry_price * 0.01

        # 若尚未初始化，則進行初始化
        if "active_stop_price" not in position:
            self.initialize_position(position, entry_price, atr)
        
        active_stop = position.get("active_stop_price", position.get("defense_line", entry_price))
        
        # ══════════════════════════════════════════════════════════════
        # 層級一：絕對保命 (1.5 ATR 硬停損)
        # ══════════════════════════════════════════════════════════════
        if side == "LONG" and current_price <= active_stop:
            logger.warning(f"[EXIT_HARD_STOP] LONG hit 1.5 ATR stop @ {current_price:.6f}")
            return "EXIT_HARD_STOP_1.5_ATR"
        if side == "SHORT" and current_price >= active_stop:
            logger.warning(f"[EXIT_HARD_STOP] SHORT hit 1.5 ATR stop @ {current_price:.6f}")
            return "EXIT_HARD_STOP_1.5_ATR"

        # ══════════════════════════════════════════════════════════════
        # 層級二：移動止盈 (Trailing Stop) - 品種動態適應
        # ══════════════════════════════════════════════════════════════
        # 定義品種參數
        if "1000PEPE" in symbol:
            activation_atr = 3.0
            callback_atr = 2.0
        else:
            activation_atr = 2.0
            callback_atr = 1.5

        if side == "LONG":
            unrealized_profit_atr = (current_price - entry_price) / atr
            position["highest_price"] = max(position.get("highest_price", entry_price), current_price)
            
            # 判斷是否啟動
            if not position.get("is_trailing_active", False) and unrealized_profit_atr >= activation_atr:
                position["is_trailing_active"] = True
                logger.info(f"[TRAILING_ACTIVATED] LONG profit reached {activation_atr} ATR. Tracking highest price.")
                
            if position.get("is_trailing_active", False):
                trailing_stop = position["highest_price"] - (callback_atr * atr)
                if current_price <= trailing_stop:
                    logger.warning(f"[EXIT_TRAILING_STOP] LONG hit callback {callback_atr} ATR from peak {position['highest_price']:.6f} @ {current_price:.6f}")
                    return "EXIT_TRAILING_STOP"
                    
        if side == "SHORT":
            unrealized_profit_atr = (entry_price - current_price) / atr
            position["lowest_price"] = min(position.get("lowest_price", entry_price), current_price)
            
            # 判斷是否啟動
            if not position.get("is_trailing_active", False) and unrealized_profit_atr >= activation_atr:
                position["is_trailing_active"] = True
                logger.info(f"[TRAILING_ACTIVATED] SHORT profit reached {activation_atr} ATR. Tracking lowest price.")
                
            if position.get("is_trailing_active", False):
                trailing_stop = position["lowest_price"] + (callback_atr * atr)
                if current_price >= trailing_stop:
                    logger.warning(f"[EXIT_TRAILING_STOP] SHORT hit callback {callback_atr} ATR from peak {position['lowest_price']:.6f} @ {current_price:.6f}")
                    return "EXIT_TRAILING_STOP"

        # ══════════════════════════════════════════════════════════════
        # 層級三：真實峰谷平倉 (True Peak Exit) - 多重過濾機制
        # ══════════════════════════════════════════════════════════════
        if len(frame) >= 3:
            prev_2 = frame.iloc[-3]
            ma3_prev1 = float(prev_1.get("ma3", 0.0))
            ma3_prev2 = float(prev_2.get("ma3", 0.0))
            ma15_prev1 = float(prev_1.get("ma15", 0.0))
            ma15_prev2 = float(prev_2.get("ma15", 0.0))
            rsi_prev1 = float(prev_1.get("rsi", 50.0))
            
            # 加入最小轉彎幅度門檻 (防止微小抖動)，依據規則設為 0.10 * ATR
            min_turn_threshold = atr * 0.10

            ma3_turning_down = (ma3_prev2 - ma3_prev1) > min_turn_threshold
            ma3_turning_up = (ma3_prev1 - ma3_prev2) > min_turn_threshold
            ma15_turning_down = (ma15_prev2 - ma15_prev1) > min_turn_threshold
            ma15_turning_up = (ma15_prev1 - ma15_prev2) > min_turn_threshold

            if side == "LONG" and ma3_turning_down:
                if ma15_turning_down or rsi_prev1 > 75:
                    profit_pct = (current_price - entry_price) / entry_price * 100
                    logger.warning(f"[LOG]: Exit Type: TRUE_PEAK | MA3_Turn: Yes | MA15_Turn: {'Yes' if ma15_turning_down else 'No'} | RSI: {rsi_prev1:.1f} | Entry: {entry_price:.6f} | Exit: {current_price:.6f} | Profit: +{profit_pct:.2f}%")
                    return "EXIT_TRUE_PEAK_REVERSAL"

            if side == "SHORT" and ma3_turning_up:
                if ma15_turning_up or rsi_prev1 < 25:
                    profit_pct = (entry_price - current_price) / entry_price * 100
                    logger.warning(f"[LOG]: Exit Type: TRUE_PEAK | MA3_Turn: Yes | MA15_Turn: {'Yes' if ma15_turning_up else 'No'} | RSI: {rsi_prev1:.1f} | Entry: {entry_price:.6f} | Exit: {current_price:.6f} | Profit: +{profit_pct:.2f}%")
                    return "EXIT_TRUE_PEAK_REVERSAL"

        return None

    def handle_post_exit_cleanup(self, position: Dict[str, Any], exit_reason: str):
        symbol = position.get("symbol", "UNKNOWN")
        logger.info(f"[Post-Exit] {exit_reason} ({symbol})")
        
        # 統一處理所有出場後的冷卻邏輯
        if exit_reason and exit_reason.startswith("EXIT_HARD_STOP"):
            position["cooldown_mode"]     = "WAIT_FOR_STABLE_KC"
            position["cooldown_kc_count"] = 2
        else:
            position["cooldown_mode"] = "NONE"
            
        position["force_space_reevaluation"] = True
