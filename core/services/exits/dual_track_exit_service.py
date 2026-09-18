import pandas as pd
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

# V10 狀態追蹤鍵列，持久化結構追蹤狀態
# v10_phase_trailing 取代 v10_ladder：改用 KC 三階段移動止損
DUAL_TRACK_STATE_KEYS = ["trade_phase", "v8_reason", "v10_phase_trailing", "has_warning_partial_close"]

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

        # 1. 帳戶硬止損與斷路器 (最高優先級：保命防禦)
        hard_stop_reason = check_hard_stop_exit(position, frame, price)
        if hard_stop_reason:
            return hard_stop_reason
            
        emergency_reason = check_emergency_exit(position, frame, price)
        if emergency_reason:
            return emergency_reason

        # 2. 峰谷全平 (結構瓦解點：結構性收割)
        peak_reason = check_peak_exhaustion_exit(position, frame, price)
        if peak_reason:
            size = float(position.get("size") or position.get("qty") or 0)
            entry_price = float(position.get("entry_price") or 0)
            if size > 0 and entry_price > 0:
                gross_pnl = (price - entry_price) * size if side == "LONG" else (entry_price - price) * size
                net_pnl = gross_pnl - (price * size * (2 * self.fee + self.slippage))
                if net_pnl > 0:
                    return peak_reason
                else:
                    return None
            else:
                return peak_reason

        # 3. 動態 ATR 階梯鎖利 (ATR Step Trailing Stop)
        ladder_reason = check_atr_step_trailing_stop(
            position, frame, price
        )
        if ladder_reason:
            return ladder_reason

        return None


def is_momentum_strong(frame: pd.DataFrame, side: str, current_price: float) -> bool:
    """
    評估趨勢慣性：
    1. 價格斜率：最近 3 根已收線 K 棒收盤價呈現明確順向。
    2. 震盪頻率：最近 3 根 K 棒未反向穿越中軌。
    3. 價格位置：目前價格靠近甚至超過外軌 (距離中軌的距離大於 0.5 倍半通道寬)。
    """
    if frame is None or len(frame) < 4:
        return False
        
    closed_rows = frame.iloc[-4:-1]
    
    closes = []
    for _, r in closed_rows.iterrows():
        c_close = float(r.get('close', 0))
        c_low = float(r.get('low', 0))
        c_high = float(r.get('high', 0))
        c_mid = float(r.get('kc_middle', r.get('ema_20', 0)))
        
        if side == 'LONG' and c_low <= c_mid:
            return False
        if side == 'SHORT' and c_high >= c_mid:
            return False
            
        closes.append(c_close)
        
    if side == 'LONG':
        if not (closes[0] <= closes[1] <= closes[2]):
            return False
    else:
        if not (closes[0] >= closes[1] >= closes[2]):
            return False
            
    last_row = frame.iloc[-1]
    c_mid = float(last_row.get('kc_middle', last_row.get('ema_20', 0)))
    c_upper = float(last_row.get('kc_upper', 0))
    c_lower = float(last_row.get('kc_lower', 0))
    
    if side == 'LONG':
        half_width = c_upper - c_mid
        if current_price < c_mid + half_width * 0.5:
            return False
    else:
        half_width = c_mid - c_lower
        if current_price > c_mid - half_width * 0.5:
            return False
            
    return True

def check_atr_step_trailing_stop(
    position: dict,
    frame: pd.DataFrame,
    price: float,
) -> Optional[str]:
    """
    動態 ATR 階梯鎖利 (ATR Step Trailing Stop)
    當最高價格比進入點高出 1.0 ATR 時，止損上移。
    """
    try:
        import math
        side = position.get("side")
        entry_price = float(position.get("entry_price") or 0)
        
        if not side or entry_price <= 0 or frame is None or len(frame) == 0:
            return None
            
        last_row = frame.iloc[-1]
        atr = float(last_row.get("atr", 0))
        if atr <= 0:
            return None
            
        state = position.setdefault("v10_phase_trailing", {})
        
        if side == "LONG":
            distance = price - entry_price
        else:
            distance = entry_price - price
            
        highest_dist = state.get("highest_distance", 0)
        if distance > highest_dist:
            highest_dist = distance
            state["highest_distance"] = highest_dist
            
        current_step = state.get("atr_step", 0)
        current_stop = state.get("stop_price", None)
        
        target_step = 0
        if highest_dist >= 1.0 * atr:
            target_step = math.floor(highest_dist / atr)
            
        if target_step > current_step:
            if not is_momentum_strong(frame, side, price):
                logger.info(f"[LOCK_PROFIT] Momentum slowing, moving SL to ATR step {target_step}.")
                state["atr_step"] = target_step
                
                # 新的止損 = 進入點 + (階梯數 - 1) * 1.0 ATR
                if side == "LONG":
                    new_stop = entry_price + (target_step - 1) * atr
                else:
                    new_stop = entry_price - (target_step - 1) * atr
                    
                if current_stop is None:
                    state["stop_price"] = new_stop
                else:
                    if side == "LONG":
                        state["stop_price"] = max(current_stop, new_stop)
                    else:
                        state["stop_price"] = min(current_stop, new_stop)
            else:
                if state.get("last_skip_log") != target_step:
                    logger.info(f"[SKIP_LOCK] Strong momentum detected, skipping lock at ATR step {target_step}.")
                    state["last_skip_log"] = target_step
                    
        current_stop = state.get("stop_price")
        if current_stop is None or state.get("atr_step", 0) == 0:
            return None
            
        if side == "LONG" and price <= current_stop:
            return "EXIT_ATR_TRAIL"
        if side == "SHORT" and price >= current_stop:
            return "EXIT_ATR_TRAIL"
            
    except Exception as e:
        logger.error(f"Error in check_atr_step_trailing_stop: {e}")
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
    峰谷全平 (結構瓦解點)
    觸發條件：價格收回軌道內 -> 大實體反轉 K 線 -> 破 MA3。
    動作：執行全平(剩餘倉位)。
    目的：在趨勢結構徹底瓦解時，收割最後的獲利。
    """
    try:
        side = position.get("side")
        if not side or len(frame) < 3:
            return None
            
        last_closed = frame.iloc[-2]
        c_open  = float(last_closed["open"])
        c_close = float(last_closed["close"])
        c_high  = float(last_closed["high"])
        c_low   = float(last_closed["low"])

        c_height   = c_high - c_low
        c_body     = abs(c_close - c_open)
        body_ratio = c_body / c_height if c_height > 0 else 0

        prev_closed = frame.iloc[-3]
        p_open  = float(prev_closed["open"])
        p_close = float(prev_closed["close"])
        p_high  = float(prev_closed["high"])
        p_low   = float(prev_closed["low"])
        p_body  = abs(p_close - p_open)

        kc_upper_closed = float(last_closed.get("kc_upper", price))
        kc_lower_closed = float(last_closed.get("kc_lower", price))
        kc_mid_closed   = float(last_closed.get("kc_middle", (kc_upper_closed + kc_lower_closed) / 2.0))
        ma3_closed      = float(last_closed.get("ma3", price))
        
        # 空間門檻：確保這是「結構性瓦解」而非「微幅震盪」
        # 條件：回調深度 >= 0.5 * ATR
        atr = float(last_closed.get("atr", price * 0.01))
        if side == "LONG":
            space_filter_ok = (c_high - c_close) >= (0.5 * atr)
        else:
            space_filter_ok = (c_close - c_low) >= (0.5 * atr)

        try:
            recent_vols = [float(frame.iloc[i].get("volume", 0)) for i in range(-7, -2)]
            vol_ma5 = sum(recent_vols[-5:]) / 5.0 if len(recent_vols) >= 5 else 0
            c_vol = float(last_closed.get("volume", 0))
            volume_filter_ok = (vol_ma5 <= 0) or (c_vol >= 1.2 * vol_ma5)
        except Exception:
            volume_filter_ok = True  # 資料缺失時放行

        # ────────────────────────────────────────────────────────────

        # 例外：極端大實體反轉 (超過 70% 比例的吞噬K) -> 繞過中軌防禦
        is_huge_reversal = body_ratio >= 0.7 and c_body > p_body * 1.5

        if side == "LONG":
            # 多單防禦線：取 MA3 與 KC中軌 兩者中較低的作為結構防線
            defense_line = min(ma3_closed, kc_mid_closed)
            
            back_inside = c_close < kc_upper_closed
            is_bearish  = c_close < c_open and body_ratio >= 0.5
            
            # 嚴格反轉確認：陰線實體>=前陽線實體 且 破前低 且 帶量
            strict_reversal = (c_body >= p_body) and (c_close < p_low) and volume_filter_ok
            
            break_defense = c_close < defense_line

            if back_inside and is_bearish and space_filter_ok:
                if (break_defense and strict_reversal) or is_huge_reversal:
                    return "PEAK_EXHAUSTION_EXIT_LONG"

        elif side == "SHORT":
            # 空單防禦線：取 MA3 與 KC中軌 兩者中較高的作為結構防線
            defense_line = max(ma3_closed, kc_mid_closed)
            
            back_inside = c_close > kc_lower_closed
            is_bullish  = c_close > c_open and body_ratio >= 0.5
            
            # 嚴格反轉確認：陽線實體>=前陰線實體 且 破前高 且 帶量
            strict_reversal = (c_body >= p_body) and (c_close > p_high) and volume_filter_ok
            
            break_defense = c_close > defense_line

            if back_inside and is_bullish and space_filter_ok:
                if (break_defense and strict_reversal) or is_huge_reversal:
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

