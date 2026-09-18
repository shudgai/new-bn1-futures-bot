import pandas as pd
from typing import Dict, Any, Optional

# V10 狀態追蹤鍵列，持久化結構追蹤狀態
DUAL_TRACK_STATE_KEYS = ["trade_phase", "v8_reason", "v10_ladder"]

class DualTrackExitStrategy:
    def __init__(self, account=None, fee: float = 0.0004, slippage: float = 0.0005):
        self.account = account
        self.fee = fee
        self.slippage = slippage

    def evaluate_exit(self, position: Dict[str, Any], frame: pd.DataFrame, price: float, velocity_drop_ratio: float = 0.0, **kwargs) -> Optional[str]:
        if frame is None or len(frame) < 3:
            return None
            
        side = position.get("side")
        if not side:
            return None

        # 追蹤 EXHAUSTION_ZONE
        trade_phase = position.get("trade_phase", "TRENDING")
        kc_upper = float(frame.iloc[-1].get("kc_upper", price))
        kc_lower = float(frame.iloc[-1].get("kc_lower", price))
        
        if trade_phase == "TRENDING":
            if side == "LONG" and price > kc_upper:
                position["trade_phase"] = "EXHAUSTION_ZONE"
            elif side == "SHORT" and price < kc_lower:
                position["trade_phase"] = "EXHAUSTION_ZONE"

        # 0. 災難斷路器 (最高優先)
        emergency_reason = check_emergency_exit(position, frame, price)
        if emergency_reason:
            return emergency_reason
            
        # 1. 固定階梯鎖利 (4U 鎖 2U，之後每 +2U 再鎖 2U)
        ladder_reason = check_ladder_profit_lock(position, price, self.fee, self.slippage)
        if ladder_reason:
            return ladder_reason

        # 2. 唯一主動平倉點：峰谷三點結構瓦解
        exhaustion_reason = check_peak_exhaustion_exit(position, frame, price)
        if exhaustion_reason:
            return exhaustion_reason
            
        # 3. 帳戶硬止損
        return check_hard_stop_exit(position, frame, price)


def check_ladder_profit_lock(position: dict, price: float, fee: float = 0.0004, slippage: float = 0.0005) -> Optional[str]:
    """
    固定階梯鎖利 (Ladder Profit Lock)：
    - 淨利 >= 4U → 鎖 2U（跌破 2U 就平）
    - 淨利 >= 6U → 鎖 4U，之後每 +2U 上移一階
    - 單向棘輪：地板只升不降
    """
    try:
        side = position.get("side")
        entry_price = float(position.get("entry_price") or 0)
        size = float(position.get("size") or 0)

        if entry_price <= 0 or size <= 0:
            return None

        # 計算當前毛利 (USDT)
        if side == "LONG":
            gross_pnl = (price - entry_price) * size
        elif side == "SHORT":
            gross_pnl = (entry_price - price) * size
        else:
            return None

        # 扣除雙邊手續費與滑點
        notional = price * size
        cost = notional * (2 * fee + slippage)
        net_pnl = gross_pnl - cost

        state = position.setdefault("v10_ladder", {})

        # 更新峰值淨利 (只升不降)
        peak = state.get("peak_net_usdt", 0.0)
        if net_pnl > peak:
            state["peak_net_usdt"] = net_pnl
            peak = net_pnl

        # 計算鎖利地板 (階梯：4U→鎖2U，6U→鎖4U，每+2U再+2U)
        # 公式：floor = int(peak // 2) * 2 - 2，且 peak 必須 >= 4
        if peak < 4.0:
            return None

        locked_floor = (int(peak // 2) * 2) - 2  # e.g. peak=4.x→2, peak=6.x→4
        locked_floor = max(locked_floor, 2.0)     # 最低地板為 2U

        # 更新最高地板紀錄 (只升不降)
        current_floor = state.get("locked_floor_usdt", 0.0)
        if locked_floor > current_floor:
            state["locked_floor_usdt"] = locked_floor

        # 若當前淨利跌破鎖利地板 → 平倉
        if net_pnl < state["locked_floor_usdt"]:
            return f"EXIT_LADDER_LOCK_{side}"

    except Exception:
        pass
    return None


def check_emergency_exit(position: Dict[str, Any], frame: pd.DataFrame, price: float) -> Optional[str]:
    """大瀑布與雙重異常 K 線 (斷路器)"""
    try:
        if len(frame) < 2:
            return None
        side = position.get("side")
        last = frame.iloc[-1]
        prev = frame.iloc[-2]
        
        atr_multiplier = 1.5
        waterfall_percent = 0.05
        
        last_close = float(last["close"])
        last_open = float(last["open"])
        last_atr = float(last.get("atr", price * 0.01))
        
        prev_close = float(prev["close"])
        prev_open = float(prev["open"])
        prev_atr = float(prev.get("atr", price * 0.01))
        
        v8_reason = position.get("v8_reason", "")
        is_breakout_entry = "TRACK_A" in v8_reason or "TRACK_C" in v8_reason
        
        if side == "LONG":
            def is_abnormal_bearish(o, c, atr):
                return (o - c) > (atr_multiplier * atr) and c < o
                
            is_double_crash = is_abnormal_bearish(prev_open, prev_close, prev_atr) and is_abnormal_bearish(last_open, last_close, last_atr)
            single_waterfall = (prev_close - last_close) > (prev_close * waterfall_percent)
            
            if single_waterfall:
                return "EXIT_EMERGENCY_WATERFALL_LONG"
            if is_double_crash and is_breakout_entry:
                return "EXIT_EMERGENCY_DOUBLE_ABNORMAL_LONG"
                
        elif side == "SHORT":
            def is_abnormal_bullish(o, c, atr):
                return (c - o) > (atr_multiplier * atr) and c > o
                
            is_double_crash = is_abnormal_bullish(prev_open, prev_close, prev_atr) and is_abnormal_bullish(last_open, last_close, last_atr)
            single_waterfall = (last_close - prev_close) > (prev_close * waterfall_percent)
            
            if single_waterfall:
                return "EXIT_EMERGENCY_WATERFALL_SHORT"
            if is_double_crash and is_breakout_entry:
                return "EXIT_EMERGENCY_DOUBLE_ABNORMAL_SHORT"
                
    except Exception:
        pass
    return None


def check_peak_exhaustion_exit(position: dict, frame: pd.DataFrame, price: float) -> Optional[str]:
    """
    峰谷三點結構瓦解 (V10.2 三維結構過濾版)

    基礎三點 (AND)：
      條件一 (空間)：從軌道外收回軌道內
      條件二 (動能)：大實體反向 K (>= 0.5)
      條件三 (防線)：收盤跌破 / 突破 MA3

    結構過濾層 (AND，三點均通過才過濾)：
      過濾一 (空間深度)：收回幅度 >= 0.5 ATR — 過濾掉「剛碰軌就小回」的假訊號
      過濾二 (成交量)  ：反轉 K 成交量 >= 1.2x 近 5 根均量 — 確保主力真實介入
      過濾三 (斜率反轉)：MA15 斜率已由正轉負 (多 → 空) 或由負轉正 (空 → 多) — 確認動能真正換手
    """
    try:
        trade_phase = position.get("trade_phase", "TRENDING")
        if trade_phase != "EXHAUSTION_ZONE":
            return None

        side = position.get("side")
        last_closed = frame.iloc[-2]      # 最近一根已收線 K 棒
        prev_closed  = frame.iloc[-3]      # 前一根，用於計算斜率變化

        c_open  = float(last_closed["open"])
        c_close = float(last_closed["close"])
        c_high  = float(last_closed["high"])
        c_low   = float(last_closed["low"])

        c_height   = c_high - c_low
        c_body     = abs(c_close - c_open)
        body_ratio = c_body / c_height if c_height > 0 else 0

        kc_upper_closed = float(last_closed.get("kc_upper", price))
        kc_lower_closed = float(last_closed.get("kc_lower", price))
        ma3_closed      = float(last_closed.get("ma3", price))
        atr_closed      = float(last_closed.get("atr", (c_high - c_low) or price * 0.01))

        # ── 結構過濾層 ──────────────────────────────────────────────
        # 過濾一：空間深度 >= 0.5 ATR
        if side == "LONG":
            retrace_depth = kc_upper_closed - c_close  # 收回了多深
        else:
            retrace_depth = c_close - kc_lower_closed
        space_filter_ok = retrace_depth >= 0.5 * atr_closed

        # 過濾二：成交量 >= 1.2x 近 5 根均量
        try:
            recent_vols = [float(frame.iloc[i].get("volume", 0)) for i in range(-7, -2)]
            vol_ma5 = sum(recent_vols[-5:]) / 5.0 if len(recent_vols) >= 5 else 0
            c_vol = float(last_closed.get("volume", 0))
            volume_filter_ok = (vol_ma5 <= 0) or (c_vol >= 1.2 * vol_ma5)
        except Exception:
            volume_filter_ok = True  # 資料缺失時放行，讓基礎三點判斷

        # 過濾三：MA15 斜率反轉 (正 → 負 或 負 → 正)
        try:
            ma15_now  = float(last_closed.get("ma15", 0))
            ma15_prev = float(prev_closed.get("ma15", 0))
            ma15_slope_now  = float(last_closed.get("ma15_slope",  ma15_now  - ma15_prev))
            ma15_slope_prev = float(prev_closed.get("ma15_slope",  0))
            # 只要斜率方向真的對調就算通過 (正→負 或 負→正)
            slope_filter_ok = (ma15_slope_prev > 0 and ma15_slope_now < 0) or \
                               (ma15_slope_prev < 0 and ma15_slope_now > 0)
        except Exception:
            slope_filter_ok = True

        structural_collapse = space_filter_ok and volume_filter_ok and slope_filter_ok
        # ────────────────────────────────────────────────────────────

        if side == "LONG":
            back_inside = c_close < kc_upper_closed        # 條件一
            is_bearish  = c_close < c_open and body_ratio >= 0.5  # 條件二
            break_ma3   = c_close < ma3_closed              # 條件三

            if back_inside and is_bearish and break_ma3 and structural_collapse:
                return "PEAK_EXHAUSTION_EXIT_LONG"

        elif side == "SHORT":
            back_inside = c_close > kc_lower_closed
            is_bullish  = c_close > c_open and body_ratio >= 0.5
            break_ma3   = c_close > ma3_closed

            if back_inside and is_bullish and break_ma3 and structural_collapse:
                return "PEAK_EXHAUSTION_EXIT_SHORT"

    except Exception:
        pass
    return None


def check_swing_trailing_exit(position: dict, frame: pd.DataFrame, price: float) -> Optional[str]:
    """
    結構式移動鎖利 (Swing Trailing)：
    獲利 1R 後拉保本，接著追蹤波段極值(Swing High/Low)。
    """
    try:
        side = position.get("side")
        entry = float(position.get("entry_price") or 0)
        initial_sl = float(position.get("initial_sl") or 0)
        
        if entry <= 0 or initial_sl <= 0:
            return None
            
        initial_risk = abs(entry - initial_sl)
        if initial_risk <= 0:
            return None
            
        sign = 1 if side == "LONG" else -1
        current_profit = sign * (price - entry)
        
        state = position.setdefault("v10_swing_trailing", {})
        
        # 1. 1R 保本機制
        is_be_locked = state.get("is_break_even_locked", False)
        if not is_be_locked and current_profit >= initial_risk:
            state["is_break_even_locked"] = True
            state["trailing_sl"] = entry # 保本點
            is_be_locked = True
            
        # 2. 追蹤波段極值 (Swing High/Low)
        if is_be_locked:
            last = frame.iloc[-1]
            atr = float(last.get("atr", price * 0.01))
            
            # 以歷史最高/最低價作為 Swing Anchor
            highest_since = state.get("highest_price", entry)
            lowest_since = state.get("lowest_price", entry)
            
            if price > highest_since:
                state["highest_price"] = price
            if price < lowest_since:
                state["lowest_price"] = price
                
            current_trailing_sl = state.get("trailing_sl", entry)
            
            if side == "LONG":
                # 多單：隨著最高點上升，將止損點上移到「最高點 - 1.5 ATR」
                new_sl = state["highest_price"] - (1.5 * atr)
                if new_sl > current_trailing_sl:
                    state["trailing_sl"] = new_sl
                    
                if price <= state["trailing_sl"]:
                    return "EXIT_SWING_TRAILING_LONG"
                    
            elif side == "SHORT":
                # 空單：隨著最低點下降，將止損點下移到「最低點 + 1.5 ATR」
                new_sl = state["lowest_price"] + (1.5 * atr)
                if new_sl < current_trailing_sl or current_trailing_sl == entry:
                    state["trailing_sl"] = new_sl
                    
                if price >= state["trailing_sl"]:
                    return "EXIT_SWING_TRAILING_SHORT"
                
    except Exception:
        pass
    return None


def check_hard_stop_exit(position: dict, frame: pd.DataFrame, price: float) -> Optional[str]:
    """硬止損"""
    try:
        side = position.get("side")
        initial_sl = float(position.get("initial_sl") or 0)
        if initial_sl > 0:
            if side == "LONG" and price <= initial_sl:
                return "EXIT_HARD_STOP_LONG"
            elif side == "SHORT" and price >= initial_sl:
                return "EXIT_HARD_STOP_SHORT"
    except Exception:
        pass
    return None
