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
                          "price_peak_value",         # 即時動態錨點（追蹤最高/最低點）
                          "profit_lock_display_sl"]   # UI 鎖利顯示

logger = logging.getLogger("DualTrackExit")

HARD_STOP_ATR = 2.0  # 未曾站上外軌的緊急保命線


class DualTrackExitStrategy(IExitStrategy):
    """
    峰谷瞬間鎖利戰略 v10 (Pivot Instant Lock)

    核心原則（最高憲法）：
    1. 動態錨點 (Dynamic Anchor)：每一 Tick 追蹤最優價格，鎖死最高利潤至 UI。
    2. 點位即平 (Pivot Instant Exit)：觸及 TP 點位，不論 K 線長相，零猶豫秒平，以錨點結算。
    3. 大瀑布保險 (Meltdown Shield)：2.0 ATR 反向 K 結構崩潰，以錨點保底，確保帶走最高點。
    4. KC 中軌結構防線：已收盤 K 線穿越 KC 中軌則結構崩壞，帶走錨點結算。
    5. 絕對耐壓：路程中的任何回調都不平倉，只有以上條件才下車。
    """

    def initialize_position(self, position: Dict[str, Any], entry_price: float, atr: float) -> None:
        side = position.get("side", "LONG")
        defense_line = (entry_price - HARD_STOP_ATR * atr) if side == "LONG" else (entry_price + HARD_STOP_ATR * atr)

        position["defense_line"]          = defense_line
        position["active_stop_price"]     = defense_line
        position["sl"]                    = defense_line   # UI 通用欄位
        position["entry_atr"]             = atr            # 開倉快照 ATR，全程不變

        # 動態錨點初始化：開倉即設為開倉價，隨後逐 Tick 推高/推低
        position["price_peak_value"]      = entry_price
        position["profit_anchor_price"]   = entry_price
        position["profit_lock_display_sl"] = entry_price   # UI 顯示

        position["touched_kc_outer"]      = False
        position["last_evaluated_closed_bar_id"] = None

        logger.info(
            f"[ANCHOR_INITIALIZED] Entry={entry_price:.6f} | ATR={atr:.6f} | "
            f"HardStop={defense_line:.6f} | InitAnchor={entry_price:.6f}"
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

        prev_1 = frame.iloc[-2]  # 最後一根已收線
        prev_2 = frame.iloc[-3]

        atr = float(prev_1.get("atr", 1.0))
        if atr <= 0:
            atr = entry_price * 0.01

        if "active_stop_price" not in position:
            self.initialize_position(position, entry_price, atr)

        active_stop = position.get("active_stop_price", position.get("defense_line", entry_price))

        curr_close = float(prev_1["close"])
        kc_upper   = float(prev_1.get("kc_upper", curr_close))
        kc_lower   = float(prev_1.get("kc_lower", curr_close))
        kc_mid     = float(prev_1.get("kc_middle", prev_1.get("ema_20", 0.0)))

        # ══════════════════════════════════════════════════════════════
        # 【第一優先】即時動態錨點更新 (Real-time Dynamic Anchor)
        # 每一個 Tick 追蹤最高/最低點，鎖死動態錨點，同步 UI 顯示
        # ══════════════════════════════════════════════════════════════
        if current_price > 0:
            price_peak = position.get("price_peak_value")
            if side == "LONG":
                if price_peak is None or current_price > price_peak:
                    position["price_peak_value"]      = current_price
                    position["profit_anchor_price"]   = current_price
                    position["profit_lock_display_sl"] = current_price  # UI 即時同步
            else:  # SHORT
                if price_peak is None or current_price < price_peak:
                    position["price_peak_value"]      = current_price
                    position["profit_anchor_price"]   = current_price
                    position["profit_lock_display_sl"] = current_price  # UI 即時同步

        # ══════════════════════════════════════════════════════════════
        # 【強制保本鎖利】 (Forced Break-Even Lock)
        # ══════════════════════════════════════════════════════════════
        taker_fee = 0.0004
        if side == "LONG":
            be_price = entry_price * (1 + taker_fee)
            current_profit = current_price - entry_price
        else:
            be_price = entry_price * (1 - taker_fee)
            current_profit = entry_price - current_price
            
        break_even_locked = position.get("break_even_locked", False)
        
        # 加倉導致均價變化時，如果新的保本價比舊的還高（更嚴格），則更新鎖定線
        if break_even_locked:
            old_be = position.get("be_price", 0.0)
            if side == "LONG" and be_price > old_be:
                position["be_price"] = be_price
                logger.info(f"🔒 [BE LOCK UPDATE] Pyramiding raised BE to {be_price:.6f}")
            elif side == "SHORT" and be_price < old_be and old_be > 0:
                position["be_price"] = be_price
                logger.info(f"🔒 [BE LOCK UPDATE] Pyramiding lowered BE to {be_price:.6f}")
                
        if not break_even_locked and current_profit > 0:
            position["break_even_locked"] = True
            position["be_price"] = be_price
            break_even_locked = True
            logger.info(f"🔒 [BE LOCK] {side} Profit > 0, Locking Break-Even at {be_price:.6f}")
            
        be_price_stored = position.get("be_price", 0.0)

        # ══════════════════════════════════════════════════════════════
        # 【新增】峰谷與量能衰竭平倉 (Peak/Valley + Volume Weakness Exit)
        # ══════════════════════════════════════════════════════════════
        from core.config import ENABLE_PEAK_VOLUME_EXIT, PEAK_FALLBACK_ATR_MULTIPLIER, VOLUME_WEAKNESS_AVG_PERIOD, VOLUME_WEAKNESS_THRESHOLD
        if ENABLE_PEAK_VOLUME_EXIT and current_price > 0:
            peak = position.get("price_peak_value")
            if peak is not None and atr > 0:
                fallback = peak - current_price if side == "LONG" else current_price - peak
                threshold = atr * PEAK_FALLBACK_ATR_MULTIPLIER
                
                # 條件 A：幅度過濾 (回落超過 0.75 ATR)
                is_significant_fallback = fallback > threshold
                
                # 條件 B：結構破壞 (收盤價跌破前3根最低/最高點)
                if len(frame) >= 4:
                    recent_3_k = frame.iloc[-4:-1]
                    structural_low = float(recent_3_k['low'].min())
                    structural_high = float(recent_3_k['high'].max())
                else:
                    structural_low = current_price - atr
                    structural_high = current_price + atr
                    
                is_structure_broken = current_price < structural_low if side == "LONG" else current_price > structural_high
                
                # 條件 C：量能衰竭 (當前已收線量 < 前3根均量 * 0.8)
                try:
                    current_volume = float(prev_1.get("volume", 0))
                    if len(frame) >= 5:
                        recent_3_vol = frame['volume'].iloc[-5:-2]
                        volume_avg = float(recent_3_vol.mean())
                    else:
                        volume_avg = float(frame['volume'].iloc[:-2].mean())
                        
                    is_momentum_exhausted = current_volume < (volume_avg * 0.8)
                except Exception:
                    is_momentum_exhausted = False
                    volume_avg = 0.0
                    current_volume = 0.0
                
                # 最終判定：三重共振 or 跌破保本線 (只進不退)
                is_triple_confirmed = is_significant_fallback and is_structure_broken and is_momentum_exhausted
                
                if side == "LONG":
                    is_be_triggered = break_even_locked and current_price < be_price_stored
                else:
                    is_be_triggered = break_even_locked and current_price > be_price_stored
                    
                is_triggered = is_triple_confirmed or is_be_triggered
                    
                if is_triggered:
                    position["guaranteed_exit_price"] = peak
                    # 標記平倉後進入量能冷卻期
                    position["cooldown_mode"] = "WAIT_FOR_VOLUME_RECOVERY"
                    
                    if is_be_triggered:
                        logger.warning(
                            f"🔒 [BE_LOCK_EXIT] {side} 觸發強制保本防線退出！"
                            f"Current: {current_price:.6f}, BE_Lock: {be_price_stored:.6f}"
                        )
                    else:
                        logger.warning(
                            f"📉 [TRUE_PEAK_EXIT] {side} 真峰谷三重共振確認！"
                            f"Current: {current_price:.6f}, Peak: {peak:.6f}, "
                            f"Structure Broken: {is_structure_broken} (Ref: {structural_low:.6f}/{structural_high:.6f}), "
                            f"Vol Exhausted: {is_momentum_exhausted} (Vol: {current_volume:.2f} < Avg: {volume_avg*0.8:.2f})"
                        )
                    return "EXIT_TRUE_PEAK_STRUCTURE_BREAK"

        # ══════════════════════════════════════════════════════════════
        # 【第二優先】點位即平：觸及預設 TP 點位，不論 K 線，零猶豫秒平
        # 以動態錨點作為結算依據（拿走路程中的最高點利潤）
        # ══════════════════════════════════════════════════════════════
        tp_price = position.get("take_profit_price") or position.get("tp")
        if tp_price:
            if (side == "LONG" and current_price >= tp_price) or \
               (side == "SHORT" and current_price <= tp_price):
                dynamic_anchor = position.get("price_peak_value")
                if dynamic_anchor:
                    position["guaranteed_exit_price"] = dynamic_anchor
                logger.warning(
                    f"[EXIT_PIVOT_INSTANT_LOCK] {side} 觸及 TP 點位 {tp_price:.6f}！"
                    f"零猶豫秒平，錨點={dynamic_anchor}，即時價={current_price:.6f}"
                )
                return "EXIT_TAKE_PROFIT_TARGET"

        # ══════════════════════════════════════════════════════════════
        # 【第三優先】未曾站上外軌的緊急保命線（2.0 ATR 硬止損）
        # 開倉後若行情從未突破外軌即反轉，仍須有保底防線
        # ══════════════════════════════════════════════════════════════
        touched_kc = position.get("touched_kc_outer", False)
        if not touched_kc:
            if side == "LONG" and curr_close >= kc_upper:
                touched_kc = True
                position["touched_kc_outer"] = True
            elif side == "SHORT" and curr_close <= kc_lower:
                touched_kc = True
                position["touched_kc_outer"] = True

        if not touched_kc:
            if side == "LONG" and current_price <= active_stop:
                logger.warning(f"[EXIT_HARD_STOP] LONG (未觸外軌) 2.0 ATR 保命線觸發 @ {current_price:.6f}")
                return "EXIT_HARD_STOP_2.0_ATR"
            if side == "SHORT" and current_price >= active_stop:
                logger.warning(f"[EXIT_HARD_STOP] SHORT (未觸外軌) 2.0 ATR 保命線觸發 @ {current_price:.6f}")
                return "EXIT_HARD_STOP_2.0_ATR"

        # ══════════════════════════════════════════════════════════════
        # 【第四優先】大瀑布保險 (Meltdown Shield)
        # 觸發條件：已收線 2.0 ATR 反向大 K，或連續兩根 1.5 ATR 反向 K
        # 結算方式：以動態錨點保底，確保帶走最高點利潤
        # ══════════════════════════════════════════════════════════════
        prev1_open   = float(prev_1["open"])
        prev2_open   = float(prev_2["open"])
        prev2_close  = float(prev_2["close"])
        prev1_body   = abs(curr_close - prev1_open)
        prev2_body   = abs(prev2_close - prev2_open)

        prev1_is_reverse = (curr_close < prev1_open) if side == "LONG" else (curr_close > prev1_open)
        prev2_is_reverse = (prev2_close < prev2_open) if side == "LONG" else (prev2_close > prev2_open)

        is_waterfall           = prev1_body >= 2.0 * atr and prev1_is_reverse
        is_consecutive_extreme = (prev1_body >= 1.5 * atr and prev2_body >= 1.5 * atr
                                  and prev1_is_reverse and prev2_is_reverse)

        if is_waterfall or is_consecutive_extreme:
            dynamic_anchor = position.get("price_peak_value")
            if dynamic_anchor:
                position["guaranteed_exit_price"] = dynamic_anchor
                logger.warning(
                    f"[EXIT_MELTDOWN_ANCHOR] {side} 大瀑布觸發！"
                    f"以動態錨點 {dynamic_anchor:.6f} 結算，即時價={current_price:.6f}"
                )
            else:
                logger.warning(f"[EXIT_MELTDOWN] {side} 大瀑布觸發 @ {curr_close:.6f}（無錨點，直接市價結算）")
            return "EXIT_EXTREME_RISK_MELTDOWN"

        # ══════════════════════════════════════════════════════════════
        # 【第五優先】KC 中軌實體穿越（結構防線）
        # 已收盤 K 線實體穿越 KC 中軌 = 趨勢結構崩壞
        # 若市價劣於動態錨點，強制記錄錨點作為結算依據
        # ══════════════════════════════════════════════════════════════
        if kc_mid > 0:
            if side == "LONG" and curr_close < kc_mid:
                dynamic_anchor = position.get("price_peak_value")
                if dynamic_anchor and current_price < dynamic_anchor:
                    position["guaranteed_exit_price"] = dynamic_anchor
                logger.warning(
                    f"[EXIT_KC_MID_CROSS] LONG 已收線穿越 KC 中軌 {kc_mid:.6f}，"
                    f"帶走錨點={dynamic_anchor} @ close {curr_close:.6f}"
                )
                return "EXIT_KC_MID_BODY_CROSS"
            if side == "SHORT" and curr_close > kc_mid:
                dynamic_anchor = position.get("price_peak_value")
                if dynamic_anchor and current_price > dynamic_anchor:
                    position["guaranteed_exit_price"] = dynamic_anchor
                logger.warning(
                    f"[EXIT_KC_MID_CROSS] SHORT 已收線穿越 KC 中軌 {kc_mid:.6f}，"
                    f"帶走錨點={dynamic_anchor} @ close {curr_close:.6f}"
                )
                return "EXIT_KC_MID_BODY_CROSS"

        # 路程中：絕對耐壓，不因任何回調平倉
        return None

    def handle_post_exit_cleanup(self, position: Dict[str, Any], exit_reason: str):
        symbol = position.get("symbol", "UNKNOWN")
        logger.info(f"[Post-Exit] {exit_reason} ({symbol})")

        # 保留原有的硬止損冷卻
        if exit_reason and exit_reason.startswith("EXIT_HARD_STOP"):
            position["cooldown_mode"]     = "WAIT_FOR_STABLE_KC"
            position["cooldown_kc_count"] = 2
        # 若是 Peak Volume Exit，保留在 evaluate_exit 中設置的 WAIT_FOR_VOLUME_RECOVERY
        elif position.get("cooldown_mode") != "WAIT_FOR_VOLUME_RECOVERY":
            position["cooldown_mode"] = "NONE"

        position["force_space_reevaluation"] = True


