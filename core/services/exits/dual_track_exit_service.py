import logging
from typing import Dict, Any, Optional
import pandas as pd
from core.interfaces.exit_interface import IExitStrategy
from core.config import TAKER_FEE_RATE

DUAL_TRACK_STATE_KEYS = ["channel_significant_ma3_turn", "channel_peak_abnormal", "ratchet_floor", "trade_phase", "v8_reason", "v10_phase_trailing", "has_warning_partial_close", "channel_profit_protection",
                          "last_evaluated_closed_bar_id", "super_trend_mode", "super_trend_trailing_stop",
                          "active_stop_price", "max_profit_atr", "sl", "defense_line", "touched_kc_outer",
                          "structural_breakdown_barrier_price", "structural_breakdown_side",
                          "profit_protection_active", "profit_anchor_price", "guaranteed_exit_price",
                          "entry_atr", "atr_sl", "atr_tp", "atr_protection_version", "chandelier_state", "tp",
                          "price_peak_value",         # 即時動態錨點（追蹤最高/最低點）
                          "profit_lock_display_sl",   # UI 鎖利顯示
                          "channel_profit_protection"]

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

        position["trailing_stop_price"]   = None
        position["max_price_since_entry"] = entry_price
        position["min_price_since_entry"] = entry_price

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
        if frame is None or len(frame) < 3:
            return None

        side = position.get("side", "LONG")
        entry_price = float(position.get("entry_price", 0.0))
        if entry_price <= 0:
            return None

        from core.services.candle_data import closed_entry_candles
        closed = closed_entry_candles(frame)
        if len(closed) < 2:
            return None

        c1 = closed.iloc[-2]
        c2 = closed.iloc[-1]
        c2_time = c2.name if hasattr(c2, 'name') else c2.get("timestamp", 0)
        c2_close = float(c2["close"])
        c2_open = float(c2["open"])
        c1_low = float(c1["low"])
        c1_high = float(c1["high"])
        kc_mid = float(c2.get("kc_middle", c2.get("ema_20", 0.0)))
        atr = float(c2.get("atr", entry_price * 0.01))

        # Update dynamic extremes
        if side == "LONG":
            position["highest"] = max(position.get("highest", entry_price), current_price)
            stop_price = max(entry_price - 1.5 * atr, position["highest"] - 1.5 * atr)
        else:
            position["lowest"] = min(position.get("lowest", entry_price), current_price)
            stop_price = min(entry_price + 1.5 * atr, position["lowest"] + 1.5 * atr)

        position["trailing_stop_price"] = stop_price

        # 必須是已收盤的 K 棒才能判定出場 (Closed Bar Only)
        bar_is_closed = (position.get("last_evaluated_closed_bar_id") != c2_time)
        if not bar_is_closed:
            return None  # 盤中未收盤，嚴禁任何動能或反轉出場判定！
            
        position["last_evaluated_closed_bar_id"] = c2_time

        # 1. 跌破動態 Trailing ATR 防守線
        if side == "LONG" and c2_close < stop_price:
            logger.warning(f"🛑 [EXIT_TRAILING_ATR_STOP] LONG {position.get('symbol')} c2_close={c2_close} < stop_price={stop_price}")
            return "EXIT_TRAILING_ATR_STOP"
        if side == "SHORT" and c2_close > stop_price:
            logger.warning(f"🛑 [EXIT_TRAILING_ATR_STOP] SHORT {position.get('symbol')} c2_close={c2_close} > stop_price={stop_price}")
            return "EXIT_TRAILING_ATR_STOP"

        # 3. 跌破 KC 中軌
        if kc_mid > 0:
            if side == "LONG" and c2_close < kc_mid:
                logger.warning(f"🛡️ [EXIT_KC_MID_BODY_CROSS] LONG {position.get('symbol')} c2_close={c2_close} < kc_mid={kc_mid}")
                return "EXIT_KC_MID_BODY_CROSS"
            if side == "SHORT" and c2_close > kc_mid:
                logger.warning(f"🛡️ [EXIT_KC_MID_BODY_CROSS] SHORT {position.get('symbol')} c2_close={c2_close} > kc_mid={kc_mid}")
                return "EXIT_KC_MID_BODY_CROSS"

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


