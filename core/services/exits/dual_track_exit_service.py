import logging
from typing import Dict, Any, Optional
import pandas as pd
from core.interfaces.exit_interface import IExitStrategy
from core.config import TAKER_FEE_RATE

DUAL_TRACK_STATE_KEYS = ["trade_phase", "v8_reason", "v10_phase_trailing", "has_warning_partial_close",
                          "last_evaluated_closed_bar_id", "super_trend_mode", "super_trend_trailing_stop",
                          "active_stop_price", "max_profit_atr", "sl", "defense_line", "touched_kc_outer",
                          "structural_breakdown_barrier_price", "structural_breakdown_side",
                          "profit_protection_active", "profit_anchor_price", "guaranteed_exit_price",
                          "entry_atr",
                          "dynamic_shield_peak",    # ← 追蹤歷史最優即時價格（峰值）
                          "dynamic_shield_price",   # ← 動態護城河防線（觸點即平）
                          "profit_lock_display_sl", # ← UI 鎖利顯示
                          "ma3_peak_value"]         # ← MA3 峰谷追蹤

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
        
        position["entry_atr"] = atr  # ← 開倉瞬間 ATR 快照，全交易週期不變
        
        # ══════════════════════════════════════════════════════════════
        # 【動態錨點核心】開倉即初始化
        # 錨點初始化：開倉時設定為開倉價，隨後將動態追蹤最高獲利點
        # ══════════════════════════════════════════════════════════════
        position["profit_anchor_price"] = entry_price
        position["profit_lock_display_sl"] = entry_price  # UI 顯示專用
        
        position["highest_price"] = entry_price
        position["lowest_price"] = entry_price
        position["is_trailing_active"] = False
        position["last_evaluated_closed_bar_id"] = None
        
        logger.info(
            f"[ANCHOR_INITIALIZED] Position initialized. "
            f"Entry: {entry_price:.6f} | ATR(snapshot): {atr:.6f} | "
            f"Hard Stop: {defense_line:.6f} | "
            f"Initial Anchor: {entry_price:.6f}"
        )

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
        
        curr_close = float(prev_1["close"])
        kc_upper = float(prev_1.get("kc_upper", curr_close))
        kc_lower = float(prev_1.get("kc_lower", curr_close))
        
        # ══════════════════════════════════════════════════════════════
        # 【MA3 峰谷反轉出口】MA3 Peak/Valley Reversal (0.10 ATR)
        # 邏輯：觀察 MA3 順向推進，從峰頂/谷底反向至少 0.10 ATR 則平倉
        # ══════════════════════════════════════════════════════════════
        MA3_REVERSAL_ATR = 0.10
        live_ma3 = float(curr.get("ma3", curr.get("ema_3", 0.0)))
        
        if live_ma3 > 0:
            ma3_peak = position.get("ma3_peak_value", None)
            
            if side == "LONG":
                if ma3_peak is None or live_ma3 > ma3_peak:
                    position["ma3_peak_value"] = live_ma3
                elif ma3_peak is not None:
                    if live_ma3 <= ma3_peak - (MA3_REVERSAL_ATR * atr):
                        logger.warning(f"[EXIT_MA3_REVERSAL] LONG MA3 從峰值 {ma3_peak:.6f} 反向回落 > {MA3_REVERSAL_ATR} ATR, 即時 MA3={live_ma3:.6f}")
                        return "EXIT_MA3_REVERSAL"
            else:
                if ma3_peak is None or live_ma3 < ma3_peak:
                    position["ma3_peak_value"] = live_ma3
                elif ma3_peak is not None:
                    if live_ma3 >= ma3_peak + (MA3_REVERSAL_ATR * atr):
                        logger.warning(f"[EXIT_MA3_REVERSAL] SHORT MA3 從谷底 {ma3_peak:.6f} 反向回升 > {MA3_REVERSAL_ATR} ATR, 即時 MA3={live_ma3:.6f}")
                        return "EXIT_MA3_REVERSAL"
                        
        estimated_fees = entry_price * TAKER_FEE_RATE * 2.0

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
        # 【中軌防禦 — KC Body Cross Exit】
        # 邏輯：
        #   - 在外軌外側持倉時，不以「開倉價 ± 2.0 ATR」硬性停損
        #   - 只有當最後一根已收盤 K 線的「實體 (Close)」穿越過 KC 中軌時才平倉
        #   - 使用已收盤的 prev_1 close，避免盤中影線誤觸發
        #   - 緊急情況（未曾觸及外軌、或即時價跌穿 2.0 ATR 硬底線）仍保留保命線
        # ══════════════════════════════════════════════════════════════
        kc_mid = float(prev_1.get("kc_middle", prev_1.get("ema_20", 0.0)))
        
        # 緊急保命線：未曾站上外軌的情況下，仍用 2.0 ATR 硬停損保命
        # （避免開倉後行情從未突破外軌就反轉，失去所有保護）
        if not touched_kc:
            if side == "LONG" and current_price <= active_stop:
                logger.warning(f"[EXIT_HARD_STOP] LONG (未觸外軌) 2.0 ATR 保命線觸發 @ {current_price:.6f}")
                return "EXIT_HARD_STOP_2.0_ATR"
            if side == "SHORT" and current_price >= active_stop:
                logger.warning(f"[EXIT_HARD_STOP] SHORT (未觸外軌) 2.0 ATR 保命線觸發 @ {current_price:.6f}")
                return "EXIT_HARD_STOP_2.0_ATR"

        # KC 中軌實體穿越平倉（使用已收盤 K 線的 close，不用影線）
        if kc_mid > 0:
            if side == "LONG" and curr_close < kc_mid:
                logger.warning(f"[EXIT_KC_MID_CROSS] LONG 已收線實體收盤穿越 KC 中軌 {kc_mid:.6f} @ close {curr_close:.6f}")
                return "EXIT_KC_MID_BODY_CROSS"
            if side == "SHORT" and curr_close > kc_mid:
                logger.warning(f"[EXIT_KC_MID_CROSS] SHORT 已收線實體收盤穿越 KC 中軌 {kc_mid:.6f} @ close {curr_close:.6f}")
                return "EXIT_KC_MID_BODY_CROSS"

        # 戰術獲利區 (Tactical Profit - Pivot): 1.0 ATR
        is_pivot_entry = position.get("v8_reason", "").find("PIVOT_TURN") != -1
        if is_pivot_entry:
            pivot_stop = (entry_price - 1.0 * atr) if side == "LONG" else (entry_price + 1.0 * atr)
            if side == "LONG" and current_price <= pivot_stop:
                logger.warning(f"[EXIT_PIVOT_SHIELD] LONG hit Tactical Profit Shield (1.0 ATR) Instant Tick Exit @ {current_price:.6f}")
                return "EXIT_PIVOT_SHIELD_1.0_ATR"
            if side == "SHORT" and current_price >= pivot_stop:
                logger.warning(f"[EXIT_PIVOT_SHIELD] SHORT hit Tactical Profit Shield (1.0 ATR) Instant Tick Exit @ {current_price:.6f}")
                return "EXIT_PIVOT_SHIELD_1.0_ATR"

        # ══════════════════════════════════════════════════════════════
        # 優先級 1.5：極端風險防禦 (大瀑布 / 連續異常)
        # ══════════════════════════════════════════════════════════════
        is_waterfall = prev1_body >= 2.0 * atr
        
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
        def trigger_harvest(exec_price):
            # ══════════════════════════════════════════════════════════════
            # 錨點保全結算 (The Final Harvest)
            # 哲學：「中軌沒破就死死抱著，結構崩潰就帶走最高點」
            # ══════════════════════════════════════════════════════════════
            anchor = position.get("profit_anchor_price")
            entry_p = float(position.get("entry_price", entry_price))
            
            if anchor:
                if side == "LONG":
                    anchor_profit = anchor - entry_p
                else:
                    anchor_profit = entry_p - anchor
                
                # 如果當前價格劣於錨點價格，強制記錄在錨點 (確保帶走高度)
                if (side == "LONG" and exec_price < anchor) or (side == "SHORT" and exec_price > anchor):
                    position["guaranteed_exit_price"] = anchor
                    logger.info(
                        f"[FINAL_HARVEST] 結構崩潰！即時市價 {exec_price:.6f} 劣於動態錨點 {anchor:.6f}。"
                        f"發送市價平倉！(保證金純利潤防線: {anchor_profit:.6f})"
                    )
                else:
                    logger.info(
                        f"[FINAL_HARVEST] 結構崩潰，即時市價 {exec_price:.6f} 優於或等於動態錨點 {anchor:.6f}。"
                        f"立即發送市價結算。"
                    )
            else:
                logger.warning(f"[FINAL_HARVEST_WARN] {symbol} 結構崩潰但無錨點資料，直接市價結算。")
            
            # 記錄 Breakdown_High/Low 以供後續進場過濾 (Structural Space Cooling)
            position["structural_breakdown_barrier_price"] = exec_price
            position["structural_breakdown_side"] = side
            return "EXIT_STRUCTURE_BREAKDOWN"

        # 優先級 3: 結構防線使用已收盤 K 線 (prev_1) 判定，避免被下影線/上影線掃出
        if side == "LONG":
            prev1_is_red = curr_close < float(prev_1["open"])
            if prev1_is_red and curr_close < kc_mid_prev1:
                if ma3_turning_down or ma15_turning_down or (touched_kc and curr_close < ma3_prev1):
                    logger.warning(f"[EXIT_STRUCTURE_BREAKDOWN] LONG structural breakdown + momentum loss @ {curr_close:.6f}")
                    return trigger_harvest(current_price)
        elif side == "SHORT":
            prev1_is_green = curr_close > float(prev_1["open"])
            if prev1_is_green and curr_close > kc_mid_prev1:
                if ma3_turning_up or ma15_turning_up or (touched_kc and curr_close > ma3_prev1):
                    logger.warning(f"[EXIT_STRUCTURE_BREAKDOWN] SHORT structural breakdown + momentum loss @ {curr_close:.6f}")
                    return trigger_harvest(current_price)

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
