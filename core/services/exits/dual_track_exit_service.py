import logging
from typing import Dict, Any, Optional
import pandas as pd
from core.interfaces.exit_interface import IExitStrategy

DUAL_TRACK_STATE_KEYS = ["trade_phase", "v8_reason", "v10_phase_trailing", "has_warning_partial_close",
                          "last_evaluated_closed_bar_id", "super_trend_mode", "super_trend_trailing_stop"]

logger = logging.getLogger("DualTrackExit")

# 統一 ATR 門檻常數
HARD_STOP_ATR        = 2.0   # 絕對保命線 (層級一)
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
        if frame is None or len(frame) < 4:
            return None

        side        = position.get("side", "LONG")
        entry_price = float(position.get("entry_price", 0.0))
        symbol      = position.get("symbol", "")
        if entry_price <= 0:
            return None

        curr   = frame.iloc[-1]
        prev_1 = frame.iloc[-2]
        prev_2 = frame.iloc[-3]
        prev_3 = frame.iloc[-4]

        atr = float(prev_1.get("atr", 1.0))
        if atr <= 0:
            atr = entry_price * 0.01

        # 若尚未初始化，則進行初始化
        if "active_stop_price" not in position:
            self.initialize_position(position, entry_price, atr)
        
        active_stop = position.get("active_stop_price", position.get("defense_line", entry_price))
        
        # (原盤中即時觸發的硬停損 1.5 ATR 已移除，改至下方進行 2.0 ATR 收盤確認)

        # =====================================================================
        # 以下所有邏輯，僅在「有新的 K 棒收盤時」才進行評估 (Close-only Check)
        # =====================================================================
        bar_id = prev_1.name if hasattr(prev_1, "name") else str(prev_1.to_dict())
        if position.get("last_evaluated_closed_bar_id") == bar_id:
            return None  # 盤中跳動，忽略
            
        # 記錄已評估的收盤 K 棒
        position["last_evaluated_closed_bar_id"] = bar_id

        curr_close = float(prev_1["close"])
        kc_upper = float(prev_1.get("kc_upper", curr_close))
        kc_lower = float(prev_1.get("kc_lower", curr_close))
        
        prev1_open = float(prev_1["open"])
        prev2_open = float(prev_2["open"])
        prev2_close = float(prev_2["close"])
        
        prev1_is_green = curr_close > prev1_open
        prev1_is_red = curr_close < prev1_open
        prev2_is_green = prev2_close > prev2_open
        prev2_is_red = prev2_close < prev2_open
        
        prev1_body = abs(curr_close - prev1_open)
        prev2_body = abs(prev2_close - prev2_open)

        # ══════════════════════════════════════════════════════════════
        # 優先級 1：極端風險防禦 (硬停損 2.0 ATR - 收盤價確認)
        # ══════════════════════════════════════════════════════════════
        if side == "LONG" and curr_close <= active_stop:
            logger.warning(f"[EXIT_HARD_STOP] LONG hit 2.0 ATR stop (Close Confirmed) @ {curr_close:.6f}")
            return "EXIT_HARD_STOP_2.0_ATR"
        if side == "SHORT" and curr_close >= active_stop:
            logger.warning(f"[EXIT_HARD_STOP] SHORT hit 2.0 ATR stop (Close Confirmed) @ {curr_close:.6f}")
            return "EXIT_HARD_STOP_2.0_ATR"

        # ══════════════════════════════════════════════════════════════
        # 優先級 1.5：極端風險防禦 (大瀑布 / 連續異常)
        # ══════════════════════════════════════════════════════════════
        is_waterfall = prev1_body >= 3.0 * atr
        
        # 判斷是否為反向的連續異常
        if side == "LONG":
            prev1_is_reverse = prev1_is_red
            prev2_is_reverse = prev2_is_red
        else:
            prev1_is_reverse = prev1_is_green
            prev2_is_reverse = prev2_is_green
            
        is_consecutive_extreme = (prev1_body >= 1.5 * atr) and (prev2_body >= 1.5 * atr) and prev1_is_reverse and prev2_is_reverse
        
        if is_waterfall or is_consecutive_extreme:
            logger.warning(f"[EXIT_EXTREME_RISK_MELTDOWN] {side} hit meltdown protection @ {curr_close:.6f}")
            return "EXIT_EXTREME_RISK_MELTDOWN"

        # ══════════════════════════════════════════════════════════════
        # 優先級 2：動態獲利收割 (移動止盈)
        # ══════════════════════════════════════════════════════════════
        if side == "LONG":
            unrealized_profit_atr = (curr_close - entry_price) / atr
        else:
            unrealized_profit_atr = (entry_price - curr_close) / atr
            
        max_profit_atr = position.get("max_profit_atr", 0.0)
        max_profit_atr = max(max_profit_atr, unrealized_profit_atr)
        position["max_profit_atr"] = max_profit_atr

        # ══════════════════════════════════════════════════════════════
        # 優先級 2：固定鎖利 (硬性保底 3.0 ATR)
        # ══════════════════════════════════════════════════════════════
        FIXED_TP_ATR = 3.0
        if unrealized_profit_atr >= FIXED_TP_ATR:
            logger.warning(f"[EXIT_FIXED_TAKE_PROFIT] {side} hit fixed take profit ({FIXED_TP_ATR} ATR) @ {curr_close:.6f}")
            return "EXIT_FIXED_TAKE_PROFIT"

        # ══════════════════════════════════════════════════════════════
        # 優先級 2.5：動態獲利收割 (移動止盈 0.75 ATR 啟動 / 0.75 ATR 回撤)
        # ══════════════════════════════════════════════════════════════
        TRAILING_STOP_ATR = 0.75
        if max_profit_atr >= TRAILING_STOP_ATR:
            locked_profit_atr = max_profit_atr - TRAILING_STOP_ATR
            
            # 實時更新實體與視覺止損線 (Trailing Stop Line Update)
            if side == "LONG":
                new_stop = entry_price + (locked_profit_atr * atr)
                if new_stop > position.get("sl", 0.0):
                    position["sl"] = new_stop
                    position["active_stop_price"] = new_stop
            else:
                new_stop = entry_price - (locked_profit_atr * atr)
                current_sl = position.get("sl", float('inf'))
                if current_sl <= 0.0: current_sl = float('inf')
                if new_stop < current_sl:
                    position["sl"] = new_stop
                    position["active_stop_price"] = new_stop
            
            if unrealized_profit_atr <= locked_profit_atr:
                logger.warning(f"[EXIT_DYNAMIC_PROFIT_HARVEST] {side} profit dropped to {unrealized_profit_atr:.2f} ATR (locked: {locked_profit_atr:.2f} ATR) @ {curr_close:.6f}")
                return "EXIT_DYNAMIC_PROFIT_HARVEST"

        # ══════════════════════════════════════════════════════════════
        # 優先級 3：結構性反轉 (站回異側軌道內 + 連續反向K)
        # ══════════════════════════════════════════════════════════════
        if side == "LONG":
            # 多單：跌破下軌內部 + 連2紅
            if curr_close < kc_lower and (prev1_is_red and prev2_is_red):
                logger.warning(f"[EXIT_STRUCTURAL_REVERSAL] LONG structural reversal @ {curr_close:.6f}")
                return "EXIT_STRUCTURAL_REVERSAL"
        elif side == "SHORT":
            # 空單：站上上軌內部 + 連2綠
            if curr_close > kc_upper and (prev1_is_green and prev2_is_green):
                logger.warning(f"[EXIT_STRUCTURAL_REVERSAL] SHORT structural reversal @ {curr_close:.6f}")
                return "EXIT_STRUCTURAL_REVERSAL"

        # ══════════════════════════════════════════════════════════════
        # 優先級 4：結構性峰值收割 (MA3轉向 + 站回同側軌道內 + MA15轉向)
        # ══════════════════════════════════════════════════════════════
        ma3_prev1 = float(prev_1.get("ma3", prev_1.get("ema_3", 0.0)))
        ma3_prev2 = float(prev_2.get("ma3", prev_2.get("ema_3", 0.0)))
        ma15_prev1 = float(prev_1.get("ma15", 0.0))
        ma15_prev2 = float(prev_2.get("ma15", 0.0))
        
        min_turn_threshold = atr * 0.10
        ma3_turning_down = (ma3_prev2 - ma3_prev1) > min_turn_threshold
        ma3_turning_up = (ma3_prev1 - ma3_prev2) > min_turn_threshold
        ma15_turning_down = (ma15_prev2 - ma15_prev1) > min_turn_threshold
        ma15_turning_up = (ma15_prev1 - ma15_prev2) > min_turn_threshold
        
        if side == "LONG":
            # 站回上軌內 = curr_close <= kc_upper
            if ma3_turning_down and (curr_close <= kc_upper) and ma15_turning_down:
                logger.warning(f"[EXIT_TRUE_PEAK_REVERSAL] LONG true peak reversal @ {curr_close:.6f}")
                return "EXIT_TRUE_PEAK_REVERSAL"
        elif side == "SHORT":
            # 站回下軌內 = curr_close >= kc_lower
            if ma3_turning_up and (curr_close >= kc_lower) and ma15_turning_up:
                logger.warning(f"[EXIT_TRUE_PEAK_REVERSAL] SHORT true peak reversal @ {curr_close:.6f}")
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
