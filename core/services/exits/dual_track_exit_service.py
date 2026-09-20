import logging
from typing import Dict, Any, Optional
import pandas as pd
from core.interfaces.exit_interface import IExitStrategy

DUAL_TRACK_STATE_KEYS = ["trade_phase", "v8_reason", "v10_phase_trailing", "has_warning_partial_close",
                          "last_evaluated_closed_bar_id", "super_trend_mode", "super_trend_trailing_stop",
                          "active_stop_price", "max_profit_atr", "sl", "defense_line", "touched_kc_outer",
                          "structural_breakdown_barrier_price", "structural_breakdown_side",
                          "profit_protection_active", "profit_anchor_price", "guaranteed_exit_price"]

logger = logging.getLogger("DualTrackExit")

HARD_STOP_ATR        = 2.0   # 絕對保命線 (層級一)
SPECIAL_K_ATR        = 2.0   # 特例 K 反向實體 (層級二)
HIGH_PROFIT_ATR      = 2.0   # 盤中高利潤逃生門檻 (層級二)

class DualTrackExitStrategy(IExitStrategy):
    """
    雙軌平倉策略 v9 — 獲利即鎖定 & 結構防線拿捏
    
    1. 獲利即鎖定：只要未實現盈虧 > 0，立刻記錄 Profit_Anchor_Price，進入單向鎖定狀態。
    2. 結構防線：只要 KC 中軌未破，死死抱住；只有當價格跌破 KC 中軌且動能衰竭時，才判定結構崩壞。
    3. 錨點保全：結構崩壞時，若當前價格劣於錨點價格，系統強制以錨點價格結算，確保帶走鎖定的獲利。
    """

    def initialize_position(self, position: Dict[str, Any], entry_price: float, atr: float) -> None:
        side = position.get("side", "LONG")
        defense_line = (entry_price - HARD_STOP_ATR * atr) if side == "LONG" else (entry_price + HARD_STOP_ATR * atr)
        
        position["defense_line"] = defense_line
        position["active_stop_price"] = defense_line
        position["sl"] = defense_line  # 給 UI 與核心系統看的通用欄位
        
        # 實作「開倉即錨定」機制 (Instant Anchor Point)
        # 錨點初始化：開倉瞬間立刻計算並紀錄預設獲利錨點
        anchor_atr = 0.7
        if side == "LONG":
            profit_anchor_price = entry_price + (anchor_atr * atr)
        else:
            profit_anchor_price = entry_price - (anchor_atr * atr)
            
        position["profit_anchor_price"] = profit_anchor_price
        position["profit_lock_display_sl"] = profit_anchor_price
        
        position["highest_price"] = entry_price
        position["lowest_price"] = entry_price
        position["is_trailing_active"] = False
        position["last_evaluated_closed_bar_id"] = None
        
        logger.info(f"[STATE_PURGE] Position initialized. Hard Stop: {defense_line:.6f}, Profit Anchor: {profit_anchor_price:.6f}")

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

        if "active_stop_price" not in position:
            self.initialize_position(position, entry_price, atr)
        
        active_stop = position.get("active_stop_price", position.get("defense_line", entry_price))
        
        # ══════════════════════════════════════════════════════════════
        # 廢除動態啟動門檻：開倉即錨定
        # (這裡不需要再更新 profit_anchor_price，因為已在 initialize_position 鎖定)
        # ══════════════════════════════════════════════════════════════
        # 確保舊倉位也有錨點
        if "profit_anchor_price" not in position:
            anchor_atr = 0.7
            if side == "LONG":
                position["profit_anchor_price"] = entry_price + (anchor_atr * atr)
            else:
                position["profit_anchor_price"] = entry_price - (anchor_atr * atr)
            position["profit_lock_display_sl"] = position["profit_anchor_price"]

        # =====================================================================
        # 以下所有邏輯，僅在「有新的 K 棒收盤時」才進行評估 (Close-only Check)
        # =====================================================================
        bar_id = prev_1.get("timestamp")
        if not bar_id:
            bar_id = prev_1.name if hasattr(prev_1, "name") else str(prev_1.to_dict())
            
        if position.get("last_evaluated_closed_bar_id") == bar_id:
            return None  # 盤中跳動，忽略
            
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
        # 高警覺狀態：追蹤是否曾觸及 KC 外軌 (KC Upper Rail Breakout)
        # ══════════════════════════════════════════════════════════════
        touched_kc = position.get("touched_kc_outer", False)
        if not touched_kc:
            if side == "LONG" and curr_close >= kc_upper:
                touched_kc = True
            elif side == "SHORT" and curr_close <= kc_lower:
                touched_kc = True
            if touched_kc:
                position["touched_kc_outer"] = True

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
        # [緊急救命] 最高優先級：CK 翻向即刻平倉 (Trend Reversal)
        # ══════════════════════════════════════════════════════════════
        try:
            kc_mid_prev1 = float(prev_1.get("kc_middle", prev_1.get("ema_20", 0.0)))
            kc_mid_prev2 = float(prev_2.get("kc_middle", prev_2.get("ema_20", 0.0)))
            # 兼容舊版：如果 frame 長度不夠就用 0
            kc_mid_prev3 = float(prev_3.get("kc_middle", prev_3.get("ema_20", 0.0))) if len(frame) >= 4 else 0.0
            
            ui_ck_trend = None
            if kc_mid_prev1 > kc_mid_prev2 and kc_mid_prev2 > kc_mid_prev3:
                ui_ck_trend = "LONG"
            elif kc_mid_prev1 < kc_mid_prev2 and kc_mid_prev2 < kc_mid_prev3:
                ui_ck_trend = "SHORT"
                
            is_spatial_break = False
            if side == "LONG" and curr_close < kc_mid_prev1:
                is_spatial_break = True
            elif side == "SHORT" and curr_close > kc_mid_prev1:
                is_spatial_break = True
                
            if ui_ck_trend and ui_ck_trend != side and is_spatial_break:
                logger.critical(f"[EMERGENCY_EXIT_CK_REVERSAL] {side} position EMERGENCY CLOSED due to CK reversing to {ui_ck_trend} and breaking KC Middle @ {curr_close:.6f}")
                return "EXIT_CK_REVERSAL"
        except Exception as e:
            logger.error(f"Error evaluating emergency CK reversal: {e}")

        # 提前計算均線以供後續邏輯使用
        ma3_prev1 = float(prev_1.get("ma3", prev_1.get("ema_3", 0.0)))
        ma3_prev2 = float(prev_2.get("ma3", prev_2.get("ema_3", 0.0)))
        ma15_prev1 = float(prev_1.get("ma15", 0.0))
        ma15_prev2 = float(prev_2.get("ma15", 0.0))
        kc_mid_prev1 = float(prev_1.get("kc_middle", prev_1.get("ema_20", 0.0)))
        
        min_turn_threshold = atr * 0.10
        ma3_turning_down = (ma3_prev2 - ma3_prev1) > min_turn_threshold
        ma3_turning_up = (ma3_prev1 - ma3_prev2) > min_turn_threshold
        ma15_turning_down = (ma15_prev2 - ma15_prev1) > min_turn_threshold
        ma15_turning_up = (ma15_prev1 - ma15_prev2) > min_turn_threshold

        # ══════════════════════════════════════════════════════════════
        # 優先級 3：結構防線 (EXIT_STRUCTURE_BREAKDOWN)
        # 核心哲學：只要中軌沒破，就死死抱住；一旦結構崩潰，就帶走鎖定的錨點獲利。
        # ══════════════════════════════════════════════════════════════
        def trigger_harvest():
            # 錨點保全結算 (The Final Harvest)
            anchor = position.get("profit_anchor_price")
            if anchor:
                # 如果當前價格劣於錨點價格，強制結算在錨點
                if (side == "LONG" and curr_close < anchor) or (side == "SHORT" and curr_close > anchor):
                    position["guaranteed_exit_price"] = anchor
                    logger.info(f"[FINAL_HARVEST] 結構崩潰，當前價 {curr_close:.6f} 劣於錨點 {anchor:.6f}。強制保全錨點獲利！")
            
            # 記錄 Breakdown_High/Low 以供後續進場過濾 (Structural Space Cooling)
            position["structural_breakdown_barrier_price"] = curr_close
            position["structural_breakdown_side"] = side
            return "EXIT_STRUCTURE_BREAKDOWN"

        if side == "LONG":
            if curr_close < kc_mid_prev1:
                if ma3_turning_down or ma15_turning_down or (touched_kc and curr_close < ma3_prev1):
                    logger.warning(f"[EXIT_STRUCTURE_BREAKDOWN] LONG structural breakdown + momentum loss @ {curr_close:.6f}")
                    return trigger_harvest()
        elif side == "SHORT":
            if curr_close > kc_mid_prev1:
                if ma3_turning_up or ma15_turning_up or (touched_kc and curr_close > ma3_prev1):
                    logger.warning(f"[EXIT_STRUCTURE_BREAKDOWN] SHORT structural breakdown + momentum loss @ {curr_close:.6f}")
                    return trigger_harvest()

        return None

    def handle_post_exit_cleanup(self, position: Dict[str, Any], exit_reason: str):
        symbol = position.get("symbol", "UNKNOWN")
        logger.info(f"[Post-Exit] {exit_reason} ({symbol})")
        
        if exit_reason and exit_reason.startswith("EXIT_HARD_STOP"):
            position["cooldown_mode"]     = "WAIT_FOR_STABLE_KC"
            position["cooldown_kc_count"] = 2
        else:
            position["cooldown_mode"] = "NONE"
            
        position["force_space_reevaluation"] = True
