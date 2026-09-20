import logging
from typing import Dict, Any, Optional
import pandas as pd
from core.interfaces.exit_interface import IExitStrategy

DUAL_TRACK_STATE_KEYS = ["trade_phase", "v8_reason", "v10_phase_trailing", "has_warning_partial_close",
                          "last_evaluated_closed_bar_id", "super_trend_mode", "super_trend_trailing_stop",
                          "active_stop_price", "max_profit_atr", "sl", "defense_line", "touched_kc_outer",
                          "structural_breakdown_barrier_price", "structural_breakdown_side",
                          "profit_protection_active"]

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
        position["sl"] = defense_line  # 給 UI 與核心系統看的通用欄位
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
        
        # ══════════════════════════════════════════════════════════════
        # [即時鎖利線更新] 每個 Tick 均執行 — 不受 K 棒收線屏障限制
        # 只更新 SL 保險箱位置，不觸發任何平倉
        # ══════════════════════════════════════════════════════════════
        if position.get("profit_protection_active"):
            max_profit_atr_now = float(position.get("max_profit_atr", 0.0))
            lock_ratio = 0.5
            locked_profit_atr_now = max(0.0, max_profit_atr_now * lock_ratio)
            if side == "LONG":
                new_lock_price_now = entry_price + (locked_profit_atr_now * atr)
                current_lock = float(position.get("profit_lock_display_sl") or 0.0)
                # 防退機制：只往上走
                new_lock_now = max(current_lock, new_lock_price_now)
            else:
                new_lock_price_now = entry_price - (locked_profit_atr_now * atr)
                current_lock = float(position.get("profit_lock_display_sl") or float("inf"))
                if current_lock <= 0 or current_lock >= float("inf"):
                    current_lock = entry_price + (2.0 * atr)
                # 防退機制：只往下走
                new_lock_now = min(current_lock, new_lock_price_now)
            if position.get("profit_lock_display_sl") != new_lock_now:
                # 【重要】只更新展示用欄位，不觸碰真正的 sl / active_stop_price
                # paper_account.py 使用的硬停損防線不受影響
                position["profit_lock_display_sl"] = new_lock_now
                logger.info(f"[PROFIT_LOCK_INSTANT] {side} 鎖利顯示線更新 → {new_lock_now:.6f} (locked {locked_profit_atr_now:.2f} ATR, 不觸碰硬停損)")

        # =====================================================================
        # 以下所有邏輯，僅在「有新的 K 棒收盤時」才進行評估 (Close-only Check)
        # =====================================================================
        bar_id = prev_1.get("timestamp")
        if not bar_id:
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
        # [緊急救命] 最高優先級：CK 翻向即刻平倉 (Trend Reversal)
        # ══════════════════════════════════════════════════════════════
        # 為了確保與 UI 標籤的完全一致，此處獨立抓取 kc_middle 的趨勢
        try:
            kc_mid_prev1 = float(prev_1.get("kc_middle", prev_1.get("ema_20", 0.0)))
            kc_mid_prev2 = float(prev_2.get("kc_middle", prev_2.get("ema_20", 0.0)))
            kc_mid_prev3 = float(df.iloc[-4].get("kc_middle", df.iloc[-4].get("ema_20", 0.0))) if len(df) >= 4 else 0.0
            
            # 判斷標籤是否連續兩根翻轉 (即連續兩段斜率皆反轉)
            ui_ck_trend = None
            if kc_mid_prev1 > kc_mid_prev2 and kc_mid_prev2 > kc_mid_prev3:
                ui_ck_trend = "LONG"
            elif kc_mid_prev1 < kc_mid_prev2 and kc_mid_prev2 < kc_mid_prev3:
                ui_ck_trend = "SHORT"
                
            # 增加空間緩衝 (Spatial Buffer):
            # 若為多單，不僅標籤要翻轉為空頭，價格還必須跌破 KC 中軌。
            # 若為空單，不僅標籤要翻轉為多頭，價格還必須突破 KC 中軌。
            is_spatial_break = False
            if side == "LONG" and curr_close < kc_mid_prev1:
                is_spatial_break = True
            elif side == "SHORT" and curr_close > kc_mid_prev1:
                is_spatial_break = True
                
            logger.info(f"[{symbol}] 目前讀取的 CK 方向為：{ui_ck_trend} (側邊: {side}), 空間破位: {is_spatial_break}")
            
            if ui_ck_trend and ui_ck_trend != side and is_spatial_break:
                logger.critical(f"[EMERGENCY_EXIT_CK_REVERSAL] {side} position EMERGENCY CLOSED due to CK reversing to {ui_ck_trend} and breaking KC Middle @ {curr_close:.6f}")
                return "EXIT_CK_REVERSAL"
        except Exception as e:
            logger.error(f"Error evaluating emergency CK reversal: {e}")

        # ══════════════════════════════════════════════════════════════
        # 優先級 2：蒸發德的平倉控制 (獲利保護狀態標記)
        # ══════════════════════════════════════════════════════════════
        # 當獲利達到 0.7 ATR，系統進入「獲利保護狀態」。
        # 此後【平倉權歸歸 KC 中軌防線（Priority 3）】，移動止盈排除在外。
        PROFIT_PROTECTION_ACTIVATION_ATR = 0.7  # 啟動門檻
        if side == "LONG":
            unrealized_profit_atr = (curr_close - entry_price) / atr
        else:
            unrealized_profit_atr = (entry_price - curr_close) / atr

        max_profit_atr = position.get("max_profit_atr", 0.0)
        max_profit_atr = max(max_profit_atr, unrealized_profit_atr)
        position["max_profit_atr"] = max_profit_atr

        if max_profit_atr >= PROFIT_PROTECTION_ACTIVATION_ATR and not position.get("profit_protection_active"):
            position["profit_protection_active"] = True
            logger.info(f"[PROFIT_PROTECTION] {side} entered protection state @ max_profit {max_profit_atr:.2f} ATR. KC Middle is now the SOLE exit judge.")
        
        if position.get("profit_protection_active"):
            # 鎖利線 = 最高獲利的 50%（最少保本）
            # 作用：只更新 SL/active_stop_price 作為保險箱，不觸發平倉
            # 平倉決策權 100% 留給 Priority 3 KC 中軌結構防線
            lock_ratio = 0.5  # 鎖住最高利潤的 50%
            locked_profit_atr = max(0.0, max_profit_atr * lock_ratio)
            
            if side == "LONG":
                new_lock_price = entry_price + (locked_profit_atr * atr)
                current_sl = float(position.get("active_stop_price") or position.get("sl") or 0.0)
                # 防退機制：鎖利線只能往上走，不能後退
                new_sl = max(current_sl, new_lock_price)
            else:
                new_lock_price = entry_price - (locked_profit_atr * atr)
                current_sl = float(position.get("active_stop_price") or position.get("sl") or float("inf"))
                if current_sl <= 0 or current_sl == float("inf"):
                    current_sl = entry_price + (2.0 * atr)
                # 防退機制：空單鎖利線只能往下走，不能後退
                new_sl = min(current_sl, new_lock_price)

            # 更新 SL（只更新，不觸發平倉）
            position["sl"] = new_sl
            position["active_stop_price"] = new_sl
            logger.info(
                f"[PROFIT_PROTECTION] {side} 鎖利線已更新 → {new_sl:.6f} "
                f"(最高利潤: {max_profit_atr:.2f} ATR, 鎖住: {locked_profit_atr:.2f} ATR, 當前: {unrealized_profit_atr:.2f} ATR)"
            )

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
        # 優先級 3：結構損壞平倉 (EXIT_STRUCTURE_BREAKDOWN)
        # ══════════════════════════════════════════════════════════════
        # 合併原先的 EXIT_HIGH_ALERT_MA3 與 EXIT_TRUE_PEAK_REVERSAL，消除冗餘。
        # 核心哲學：只要「價格跌破 KC 中軌」，且「均線(MA3/MA15) 顯示動能衰竭」，即判定結構損壞。
        if side == "LONG":
            if curr_close < kc_mid_prev1:
                if ma3_turning_down or ma15_turning_down or (touched_kc and curr_close < ma3_prev1):
                    logger.warning(f"[EXIT_STRUCTURE_BREAKDOWN] LONG structural breakdown + momentum loss @ {curr_close:.6f}")
                    # 記錄 Breakdown_High 以供後續進場過濾 (Structural Space Cooling)
                    position["structural_breakdown_barrier_price"] = entry_price + (max_profit_atr * atr)
                    position["structural_breakdown_side"] = "LONG"
                    return "EXIT_STRUCTURE_BREAKDOWN"
        elif side == "SHORT":
            if curr_close > kc_mid_prev1:
                if ma3_turning_up or ma15_turning_up or (touched_kc and curr_close > ma3_prev1):
                    logger.warning(f"[EXIT_STRUCTURE_BREAKDOWN] SHORT structural breakdown + momentum loss @ {curr_close:.6f}")
                    # 記錄 Breakdown_Low 以供後續進場過濾 (Structural Space Cooling)
                    position["structural_breakdown_barrier_price"] = entry_price - (max_profit_atr * atr)
                    position["structural_breakdown_side"] = "SHORT"
                    return "EXIT_STRUCTURE_BREAKDOWN"

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
