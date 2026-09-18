import pandas as pd
from typing import Dict, Any, Optional

# V10 狀態追蹤鍵列，持久化結構追蹤狀態
# v10_phase_trailing 取代 v10_ladder：改用 KC 三階段移動止損
DUAL_TRACK_STATE_KEYS = ["trade_phase", "v8_reason", "v10_phase_trailing"]

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
            
        # 1. KC 三階段移動止損（Phase Trailing Stop）
        #    每觸及一個 KC 里程碑，止損點往有利方向推移 1.5 ATR
        phase_trail_reason = check_kc_phase_trailing_stop(
            position, frame, price, self.fee, self.slippage
        )
        if phase_trail_reason:
            return phase_trail_reason

        # 2. 唯一主動平倉點：峰谷三點結構瓦解
        exhaustion_reason = check_peak_exhaustion_exit(position, frame, price)
        if exhaustion_reason:
            return exhaustion_reason
            
        # 3. 帳戶硬止損
        return check_hard_stop_exit(position, frame, price)


def check_kc_phase_trailing_stop(
    position: dict,
    frame: pd.DataFrame,
    price: float,
    fee: float = 0.0004,
    slippage: float = 0.0005,
    phase_atr_mult: float = 0.7,
) -> Optional[str]:
    """
    KC 三階段移動止損（Step-wise Phase Trailing Stop）：

    設計哲學：
    - 倉位始終保持完整，不做任何減倉動作
    - 每到達一個 KC 里程碑，止損點往有利方向推移（棘輪機制）
    - 確保每個 Phase 的「保底獲利」遞增，鎖住已走過的波段獲利

    Phase 定義（多單 LONG 為例）：
    - Phase 1：現價首次觸及 KC 中軌（kc_middle）AND 淨利 > 0
      → 止損點設定為「觸發時現價 - 1 × phase_atr_mult × ATR」
    - Phase 2：現價觸及 KC 對側外軌（kc_upper for LONG）
      → 止損點推移為「觸發時現價 - 2 × phase_atr_mult × ATR」
    - Phase 3：進入 EXHAUSTION_ZONE（超越外軌明顯距離）
      → 止損點推移為「觸發時現價 - 3 × phase_atr_mult × ATR」

    棘輪機制：止損點只往有利方向移動（多單只升、空單只降）

    Args:
        position:       持倉字典（含 side、entry_price、size 等）
        frame:          最新 K 線資料 DataFrame
        price:          當前最新報價
        fee:            單邊手續費率（預設 0.04%）
        slippage:       預估滑點（預設 0.05%）
        phase_atr_mult: 每個 Phase 的 ATR 止損距離倍數（預設 1.5）
    """
    try:
        side = position.get("side")
        entry_price = float(position.get("entry_price") or 0)
        # position dict 可能用 qty 或 size，兩者都嘗試
        size = float(position.get("size") or position.get("qty") or 0)

        if not side or entry_price <= 0 or size <= 0:
            return None
        if frame is None or len(frame) < 2:
            return None

        # ── 取 KC 通道值與 ATR ──────────────────────────────────────
        last = frame.iloc[-1]
        kc_upper  = float(last.get("kc_upper",  price))
        kc_lower  = float(last.get("kc_lower",  price))
        kc_middle = float(last.get("kc_middle", (kc_upper + kc_lower) / 2.0))
        # kc_middle 備援：部分 frame 可能用不同欄位名稱
        if kc_middle == price:
            kc_middle = float(last.get("kc_basis", (kc_upper + kc_lower) / 2.0))
        atr = float(last.get("atr", price * 0.01))
        if atr <= 0:
            atr = price * 0.01

        # ── 計算當前淨利（僅用於 Phase 1 的 net_pnl > 0 門檻）────────
        if side == "LONG":
            gross_pnl = (price - entry_price) * size
        elif side == "SHORT":
            gross_pnl = (entry_price - price) * size
        else:
            return None
        notional = price * size
        cost = notional * (2 * fee + slippage)
        net_pnl = gross_pnl - cost

        # ── 取/初始化狀態 ──────────────────────────────────────────
        state = position.setdefault("v10_phase_trailing", {})
        current_phase = state.get("phase", 0)
        current_stop  = state.get("stop_price", None)
        trade_phase   = position.get("trade_phase", "TRENDING")

        # ── 追蹤進場以來最有利極值（Swing Extreme）────────────────
        # LONG：最高價（多單越高越有利）
        # SHORT：最低價（空單越低越有利）
        if side == "LONG":
            swing_extreme = max(state.get("swing_extreme", entry_price), price)
        else:
            swing_extreme = min(state.get("swing_extreme", entry_price), price)
        state["swing_extreme"] = swing_extreme

        # ── Phase 升級判斷（只升不降）─────────────────────────────
        # Phase 3：進入 EXHAUSTION_ZONE（超越外軌）
        # Phase 2：現價觸及持倉側外軌
        # Phase 1：現價首次觸及 KC 中軌 AND 淨利 > 0
        new_phase = current_phase
        if trade_phase == "EXHAUSTION_ZONE" and current_phase < 3:
            new_phase = 3
        elif current_phase < 2:
            if side == "LONG" and price >= kc_upper:
                new_phase = 2
            elif side == "SHORT" and price <= kc_lower:
                new_phase = 2
        if current_phase < 1 and new_phase < 2:
            if side == "LONG" and price >= kc_middle and net_pnl > 0:
                new_phase = 1
            elif side == "SHORT" and price <= kc_middle and net_pnl > 0:
                new_phase = 1

        if new_phase > current_phase:
            state["phase"] = new_phase
            current_phase = new_phase

        # ── 每 tick 以最新極值重算止損點（棘輪：只往有利方向移動）─
        # 止損距離 = phase × phase_atr_mult × ATR
        # 錨點 = swing_extreme（保證止損點永遠在獲利側）
        if current_phase > 0:
            new_stop = _calc_phase_stop(side, swing_extreme, current_phase, phase_atr_mult, atr)
            if new_stop is not None:
                current_stop = _update_ratchet_stop(side, current_stop, new_stop)
                state["stop_price"] = current_stop

        # ── 止損觸發判斷 ───────────────────────────────────────────
        if current_stop is None or current_phase == 0:
            return None

        if side == "LONG" and price <= current_stop:
            return f"EXIT_PHASE_TRAIL_LONG_P{current_phase}"
        if side == "SHORT" and price >= current_stop:
            return f"EXIT_PHASE_TRAIL_SHORT_P{current_phase}"

    except Exception:
        pass
    return None


def _calc_phase_stop(
    side: str,
    trigger_price: float,
    phase: int,
    atr_mult: float,
    atr: float,
) -> Optional[float]:
    """計算指定 Phase 的止損價格。

    止損距離 = phase × atr_mult × ATR
    多單：stop = trigger_price - distance
    空單：stop = trigger_price + distance
    """
    distance = phase * atr_mult * atr
    if distance <= 0:
        return None
    if side == "LONG":
        return trigger_price - distance
    elif side == "SHORT":
        return trigger_price + distance
    return None


def _update_ratchet_stop(
    side: str,
    current_stop: Optional[float],
    new_stop: float,
) -> float:
    """棘輪機制：止損點只往有利方向移動。

    多單：只升不降（取較大值）
    空單：只降不升（取較小值）
    """
    if current_stop is None:
        return new_stop
    if side == "LONG":
        return max(current_stop, new_stop)
    elif side == "SHORT":
        return min(current_stop, new_stop)
    return new_stop


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
