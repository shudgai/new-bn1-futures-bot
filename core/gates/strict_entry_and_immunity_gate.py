"""StrictEntryAndImmunityGate
整合「3 根平滑斜率防走平開倉」、「逆向大趨勢與震盪一票否決」與「未破中軌/MA15 趨勢護航全程死抱否決權」之核心閘門。
"""
import logging
from typing import Tuple, Dict, Any, Optional
import pandas as pd

logger = logging.getLogger("strategy.entry_gate")


def resolve_kc_trend(df: Any) -> str:
    """以最近兩根已收線 KC 中軌斜率判斷方向；派生標籤不覆蓋數值。"""
    if df is None or len(df) < 2:
        return 'RANGE'
    try:
        if 'is_closed' in df.columns:
            closed_df = df[df['is_closed'] == True]
            if len(closed_df) < 2:
                closed_df = df.iloc[:-1] if len(df) >= 3 else df
        else:
            closed_df = df.iloc[:-1] if len(df) >= 3 else df

        if len(closed_df) < 2:
            return 'RANGE'

        last_closed = closed_df.iloc[-1]
        prev_closed = closed_df.iloc[-2]
        c_mid = float(last_closed.get('kc_middle', last_closed.get('kc_basis', 0.0)))
        p_mid = float(prev_closed.get('kc_middle', prev_closed.get('kc_basis', 0.0)))

        # 對齊前端圖表 getChartCkTrend（三棒邏輯）
        if all(pd.notna(value) and value > 0 for value in (c_mid, p_mid)):
            tolerance = max(abs(p_mid) * 1e-12, 1e-12)
            if c_mid < p_mid - tolerance:
                return 'DOWN'
            if c_mid > p_mid + tolerance:
                return 'UP'

        return 'RANGE'
    except Exception:
        return 'RANGE'


def check_kc_flat_or_squeezing(df: Any, quote: Optional[float] = None) -> bool:
    """當通道寬度 (KC Upper - KC Lower) / Price 小於一定比例，判定為死亡纏繞震盪期。"""
    if df is None or len(df) < 2:
        return False
    try:
        curr = df.iloc[-1]
        p = float(quote) if quote is not None else float(curr.get('close', 0.0))
        if p <= 0:
            return False
        kc_u = float(curr.get('kc_upper', 0.0))
        kc_l = float(curr.get('kc_lower', 0.0))
        if kc_u <= 0 or kc_l <= 0 or kc_u <= kc_l:
            return False

        kc_width_ratio = (kc_u - kc_l) / p

        # 1. 帶寬佔價格比例過小（小於 0.6%）
        if kc_width_ratio < 0.006:
            return True

        # 2. 過去 20 根帶寬收縮至峰值 25% 或以下
        if len(df) >= 10:
            hist_widths = (df['kc_upper'] - df['kc_lower']).dropna()
            if len(hist_widths) >= 5:
                max_w = float(hist_widths.tail(20).max())
                curr_w = kc_u - kc_l
                if max_w > 0 and (curr_w / max_w) <= 0.25:
                    return True

        # 3. 帶寬小於 1.0 ATR
        atr = float(curr.get('atr', 0.0))
        if atr > 0 and (kc_u - kc_l) / atr < 1.0:
            return True

        return False
    except Exception:
        return False


class StrictEntryAndImmunityGate:
    def __init__(self, min_slope_pct: float = 0.0010, cooldown_bars: int = 3):
        self.min_slope_pct = min_slope_pct
        self.cooldown_bars = cooldown_bars
        self.last_exit_bar_index = -999

    def record_exit(self, current_bar_index: int):
        """記錄平倉時的 K 棒索引，用於觸發冷卻防線"""
        self.last_exit_bar_index = current_bar_index

    # =========================================================================
    # 一、 進場前置審查：逆向大趨勢一票否決、通道扁平震盪過濾、MA5 走平/弧頂 100% 一票否決 (VETO)
    # =========================================================================
    def can_open(self, side: str, df: Any, current_bar_index: int = 0, quote: Optional[float] = None) -> Tuple[bool, str]:
        if df is None or len(df) < 2:
            return False, "VETO: K 棒數量不足，拒絕開倉"

        norm_side = 'LONG' if side in ('LONG', 'OPEN_LONG') else ('SHORT' if side in ('SHORT', 'OPEN_SHORT') else side)
        bars_since_exit = current_bar_index - self.last_exit_bar_index
        in_cooldown = bars_since_exit <= self.cooldown_bars

        # 1. 冷卻期檢核 (防連環刷單，支援強動能大陽/大陰線豁免)
        if in_cooldown:
            can_bypass = False
            bar_t = df.iloc[-1]
            prev_bar = df.iloc[-2]
            atr = float(bar_t.get('atr', 0.0) if hasattr(bar_t, 'get') else bar_t['atr'])
            if atr > 0:
                kc_trend = resolve_kc_trend(df)
                if norm_side == 'LONG':
                    body_size = float(bar_t['close']) - float(bar_t['open'])
                    upper_wick = float(bar_t['high']) - max(float(bar_t['close']), float(bar_t['open']))
                    upper_band = float(bar_t.get('kc_upper', bar_t.get('upper_band', 0.0)) if hasattr(bar_t, 'get') else bar_t['kc_upper'])
                    ma5_up = float(bar_t['ma5']) > float(prev_bar['ma5']) and float(bar_t['close']) >= float(bar_t['ma5'])
                    if (body_size >= 1.0 * atr and float(bar_t['close']) > upper_band and upper_wick <= 0.4 * body_size and kc_trend != 'DOWN' and ma5_up):
                        logger.info(f"⚡ [冷卻豁免] 觸發強動能大陽線突破 (實體={body_size:.4f} >= 1.0 ATR)，打斷冷卻期強制放行開多！")
                        can_bypass = True
                elif norm_side == 'SHORT':
                    body_size = float(bar_t['open']) - float(bar_t['close'])
                    lower_wick = min(float(bar_t['close']), float(bar_t['open'])) - float(bar_t['low'])
                    lower_band = float(bar_t.get('kc_lower', bar_t.get('lower_band', 0.0)) if hasattr(bar_t, 'get') else bar_t['kc_lower'])
                    ma5_down = float(bar_t['ma5']) < float(prev_bar['ma5']) and float(bar_t['close']) <= float(bar_t['ma5'])
                    if (body_size >= 1.0 * atr and float(bar_t['close']) < lower_band and lower_wick <= 0.4 * body_size and kc_trend != 'UP' and ma5_down):
                        logger.info(f"⚡ [冷卻豁免] 觸發強動能大陰線貫穿 (實體={body_size:.4f} >= 1.0 ATR)，打斷冷卻期強制放行開空！")
                        can_bypass = True

            if not can_bypass:
                logger.info(f"⛔ [開{norm_side}攔截] 原因: 處於平倉冷卻期內 (已過 {bars_since_exit}/{self.cooldown_bars} 根，剩餘 {self.cooldown_bars - bars_since_exit + 1} 根 K 棒)")
                return False, f"VETO: 平倉冷卻中 (已過 {bars_since_exit}/{self.cooldown_bars} 根)，禁止開倉"

        # 2. 逆向大趨勢一票否決（順勢原則）
        kc_trend = resolve_kc_trend(df)
        if norm_side == 'LONG' and kc_trend == 'DOWN':
            return False, "VETO_CANNOT_LONG_WHEN_KC_DOWN"
        if norm_side == 'SHORT' and kc_trend == 'UP':
            return False, "VETO_CANNOT_SHORT_WHEN_KC_UP"

        # 3. 通道扁平震盪過濾（震盪嚴禁開倉）
        if check_kc_flat_or_squeezing(df, quote=quote):
            return False, "VETO_CHOPPY_MARKET_NO_ENTRY"

        if len(df) < 5:
            return True, "PASSED_INITIAL_DATA"

        # 4. 必須採用已收盤 K 棒 (iloc[-2] 剛收盤, iloc[-4] 前兩根) 審查 MA5 平滑斜率
        curr_bar = df.iloc[-2]
        prev_bar_2 = df.iloc[-4]
        price = float(curr_bar['close'])

        ma5_curr = float(curr_bar['ma5'])
        ma5_prev2 = float(prev_bar_2['ma5'])
        ma15_curr = float(curr_bar['ma15'])

        # 3 根平滑斜率計算: (MA5_curr - MA5_prev2) / Close
        ma5_slope = (ma5_curr - ma5_prev2) / (price + 1e-9)

        # 多單審查
        if norm_side == 'LONG':
            if ma5_curr <= ma15_curr:
                return False, "VETO_LONG: MA5 尚未大於 MA15"
            # 斜率未達拉升標準 -> 判定走平或弧頂
            if ma5_slope <= self.min_slope_pct:
                return False, f"VETO_LONG: MA5 走平或傾斜不足 (Slope: {ma5_slope:.6f} <= {self.min_slope_pct})，嚴禁頂部接盤"
            return True, "PASSED_ENTRY_LONG"

        # 空單審查
        elif norm_side == 'SHORT':
            if ma5_curr >= ma15_curr:
                return False, "VETO_SHORT: MA5 尚未小於 MA15"
            # 斜率未達下殺標準 -> 判定走平或築底
            if ma5_slope >= -self.min_slope_pct:
                return False, f"VETO_SHORT: MA5 走平或傾斜不足 (Slope: {ma5_slope:.6f} >= {-self.min_slope_pct})，嚴禁底部追空"
            return True, "PASSED_ENTRY_SHORT"

        return False, "VETO: UNKNOWN_SIDE"

    # =========================================================================
    # 二、 出場全程護航：非收盤不判定，未破中軌/MA15 任何常規平倉 100% 一票否決 (VETO)
    # =========================================================================
    def can_exit(
        self,
        side: str,
        closed_bar: Dict[str, Any],
        net_profit_pct: float = 0.0,
        is_hard_stop: bool = False,
        is_bar_closed: bool = True
    ) -> Tuple[bool, str]:
        # 1. 硬停損保護 (最高級別放行: 包含 -1.5% 絕對硬止損)
        if is_hard_stop or net_profit_pct <= -0.015:
            return True, "PASSED: HARD_STOP_TRIGGERED"

        # 2. 淨利鎖利放行 (利潤達 10% 啟動且觸發回吐 20% 防線)
        if net_profit_pct >= 0.10:
            return True, "PASSED: AUTHORIZED_NET_PROFIT_LOCK"

        # 3. 未達 10% 浮盈階段：嚴禁盤中即時價平倉，必須以收盤確認為準
        if not is_bar_closed:
            return False, "VETO_HOLDING_UNTIL_BAR_CLOSED"

        close = float(closed_bar.get('close', 0.0))
        ma15 = float(closed_bar.get('ma15', close))
        kc_mid = float(closed_bar.get('kc_middle', closed_bar.get('kc_basis', ma15)))
        norm_side = 'LONG' if side in ('LONG', 'CLOSE_LONG', 'EXIT_LONG') else 'SHORT'

        # 4. 多單趨勢全程護航：未跌破 KC 中軌與 MA15 嚴禁平多 (全部視為健康回踩)
        if norm_side == 'LONG':
            protect_mid = min(kc_mid, ma15) if (kc_mid > 0 and ma15 > 0) else (kc_mid or ma15)
            if protect_mid > 0 and close >= protect_mid:
                return False, "VETO_HOLDING_HEALTHY_PULLBACK_ABOVE_MID: TREND_INTACT_ABOVE_MA15 (VETO)"
            return True, "PASSED: TREND_BROKEN_BELOW_MA15"

        # 5. 空單趨勢全程護航：未突破 KC 中軌與 MA15 嚴禁平空 (全部視為健康反抽)
        elif norm_side == 'SHORT':
            protect_mid = max(kc_mid, ma15) if (kc_mid > 0 and ma15 > 0) else (kc_mid or ma15)
            if protect_mid > 0 and close <= protect_mid:
                return False, "VETO_HOLDING_HEALTHY_PULLBACK_BELOW_MID: TREND_INTACT_BELOW_MA15 (VETO)"
            return True, "PASSED: TREND_BROKEN_ABOVE_MA15"

        return True, "PASSED"


# 全域單例
default_strict_gate = StrictEntryAndImmunityGate()


def check_long_entry_gate(
    df: Any,
    current_bar_idx: int,
    last_exit_bar_idx: int,
    min_cooldown_bars: int = 3
) -> Tuple[bool, str]:
    """
    多單進場綜合檢驗門禁 (含強動能豁免冷卻與顯式攔截日誌)
    回傳: (allow_entry: bool, reason: str)
    """
    if df is None or len(df) < 2:
        return False, "INVALID_DATA_LENGTH"

    bar_t = df.iloc[-1]      # 剛收盤定型的 K 棒
    prev_bar = df.iloc[-2]   # 前一根收盤 K 棒

    atr = float(bar_t.get('atr', 0.0) if hasattr(bar_t, 'get') else bar_t['atr'])
    if atr <= 0:
        return False, "INVALID_ATR"

    body_size = float(bar_t['close']) - float(bar_t['open'])
    upper_wick = float(bar_t['high']) - max(float(bar_t['close']), float(bar_t['open']))
    bars_since_exit = current_bar_idx - last_exit_bar_idx
    in_cooldown = bars_since_exit <= min_cooldown_bars

    upper_band = float(bar_t.get('kc_upper', bar_t.get('upper_band', 0.0)) if hasattr(bar_t, 'get') else bar_t['kc_upper'])
    prev_upper = float(prev_bar.get('kc_upper', prev_bar.get('upper_band', 0.0)) if hasattr(prev_bar, 'get') else prev_bar['kc_upper'])

    # -------------------------------------------------------------
    # 防線 1：大趨勢方向一票否決 (禁止在 KC 向下時逆勢做多)
    # -------------------------------------------------------------
    kc_slope = float(bar_t.get('kc_middle_slope', 0.0) if hasattr(bar_t, 'get') else 0.0)
    if kc_slope == 0.0 and ('kc_middle' in bar_t or hasattr(bar_t, 'get') and bar_t.get('kc_middle') is not None):
        c_mid = float(bar_t.get('kc_middle', 0.0) if hasattr(bar_t, 'get') else bar_t['kc_middle'])
        p_mid = float(prev_bar.get('kc_middle', 0.0) if hasattr(prev_bar, 'get') else prev_bar['kc_middle'])
        kc_slope = c_mid - p_mid

    if kc_slope < 0:
        logger.info(f"⛔ [開多攔截] 原因: KC中軌下行 (Slope={kc_slope:+.5f})，嚴禁逆勢做多")
        return False, "BLOCKED_KC_MIDDLE_SLOPE_DOWN"

    # -------------------------------------------------------------
    # 防線 2：MA5 短期動能檢驗
    # -------------------------------------------------------------
    ma5_t = float(bar_t['ma5'])
    ma5_prev = float(prev_bar['ma5'])
    close_t = float(bar_t['close'])
    ma5_trending_up = ma5_t > ma5_prev
    if not ma5_trending_up or (close_t < ma5_t):
        logger.info(f"⛔ [開多攔截] 原因: MA5未抬頭或收盤未站上 MA5 (MA5_t={ma5_t:.4f}, Prev={ma5_prev:.4f}, Close={close_t:.4f})")
        return False, "BLOCKED_MA5_NOT_UPWARD"

    # -------------------------------------------------------------
    # 防線 3：高檔插針出貨檢驗 (長上影線攔截)
    # -------------------------------------------------------------
    if body_size > 0 and upper_wick > (0.6 * body_size) and upper_wick > (0.3 * atr):
        logger.info(f"⛔ [開多攔截] 原因: 上影線過長存在出貨賣壓 (上影線={upper_wick:.4f}, 實體={body_size:.4f})")
        return False, "BLOCKED_BY_EXCESSIVE_UPPER_WICK"

    # -------------------------------------------------------------
    # 判定：是否觸發「強動能脈衝 (Super Impulse)」
    # -------------------------------------------------------------
    is_super_impulse = (
        (body_size >= 1.0 * atr) and                   # 實體大陽線
        (close_t > upper_band) and                     # 收盤實體站上外軌
        (upper_wick <= 0.4 * body_size)                 # 收在相對高點
    )

    # -------------------------------------------------------------
    # 防線 4：冷卻期檢查 (強動能可直接豁免)
    # -------------------------------------------------------------
    if in_cooldown:
        if is_super_impulse:
            logger.info(f"⚡ [冷卻豁免] 觸發強動能大陽線突破 (實體={body_size:.4f} >= 1.0 ATR)，打斷冷卻期強制放行！")
        else:
            logger.info(f"⛔ [開多攔截] 原因: 處於平倉冷卻期內 (已過 {bars_since_exit}/{min_cooldown_bars} 根，剩餘 {min_cooldown_bars - bars_since_exit + 1} 根 K 棒)")
            return False, "BLOCKED_BY_COOLDOWN"

    # -------------------------------------------------------------
    # 防線 5：兩根破軌確認 (收盤價比對，不卡長影線高點)
    # -------------------------------------------------------------
    prev_close = float(prev_bar['close'])
    two_bars_break = (close_t > upper_band) and (prev_close > prev_upper)

    if not two_bars_break and not is_super_impulse:
        logger.info(f"⛔ [開多攔截] 原因: 未滿足兩根收盤破軌確認 (當前收={close_t:.4f}, 上軌={upper_band:.4f})")
        return False, "BLOCKED_BREAKOUT_NOT_CONFIRMED"

    # -------------------------------------------------------------
    # 全數通過
    # -------------------------------------------------------------
    logger.info(f"✅ [開多放行] 條件全數滿足 (強動能豁免={is_super_impulse}, KC斜率={kc_slope:+.5f})")
    return True, "ENTRY_APPROVED"


def check_short_entry_gate(
    df: Any,
    current_bar_idx: int,
    last_exit_bar_idx: int,
    min_cooldown_bars: int = 3
) -> Tuple[bool, str]:
    """
    空單進場綜合檢驗門禁 (含強動能豁免冷卻與顯式攔截日誌)
    回傳: (allow_entry: bool, reason: str)
    """
    if df is None or len(df) < 2:
        return False, "INVALID_DATA_LENGTH"

    bar_t = df.iloc[-1]      # 剛收盤定型的 K 棒
    prev_bar = df.iloc[-2]   # 前一根收盤 K 棒

    atr = float(bar_t.get('atr', 0.0) if hasattr(bar_t, 'get') else bar_t['atr'])
    if atr <= 0:
        return False, "INVALID_ATR"

    body_size = float(bar_t['open']) - float(bar_t['close'])  # 陰線實體
    lower_wick = min(float(bar_t['close']), float(bar_t['open'])) - float(bar_t['low'])
    bars_since_exit = current_bar_idx - last_exit_bar_idx
    in_cooldown = bars_since_exit <= min_cooldown_bars

    lower_band = float(bar_t.get('kc_lower', bar_t.get('lower_band', 0.0)) if hasattr(bar_t, 'get') else bar_t['kc_lower'])
    prev_lower = float(prev_bar.get('kc_lower', prev_bar.get('lower_band', 0.0)) if hasattr(prev_bar, 'get') else prev_bar['kc_lower'])

    # -------------------------------------------------------------
    # 防線 1：大趨勢方向一票否決 (禁止在 KC 向上時逆勢做空)
    # -------------------------------------------------------------
    kc_slope = float(bar_t.get('kc_middle_slope', 0.0) if hasattr(bar_t, 'get') else 0.0)
    if kc_slope == 0.0 and ('kc_middle' in bar_t or hasattr(bar_t, 'get') and bar_t.get('kc_middle') is not None):
        c_mid = float(bar_t.get('kc_middle', 0.0) if hasattr(bar_t, 'get') else bar_t['kc_middle'])
        p_mid = float(prev_bar.get('kc_middle', 0.0) if hasattr(prev_bar, 'get') else prev_bar['kc_middle'])
        kc_slope = c_mid - p_mid

    if kc_slope > 0:
        logger.info(f"⛔ [開空攔截] 原因: KC中軌上行 (Slope={kc_slope:+.5f})，嚴禁逆勢做空")
        return False, "BLOCKED_KC_MIDDLE_SLOPE_UP"

    # -------------------------------------------------------------
    # 防線 2：MA5 短期動能檢驗
    # -------------------------------------------------------------
    ma5_t = float(bar_t['ma5'])
    ma5_prev = float(prev_bar['ma5'])
    close_t = float(bar_t['close'])
    ma5_trending_down = ma5_t < ma5_prev
    if not ma5_trending_down or (close_t > ma5_t):
        logger.info(f"⛔ [開空攔截] 原因: MA5未下探或收盤未壓制在 MA5 之下 (MA5_t={ma5_t:.4f}, Prev={ma5_prev:.4f}, Close={close_t:.4f})")
        return False, "BLOCKED_MA5_NOT_DOWNWARD"

    # -------------------------------------------------------------
    # 防線 3：低檔插針反彈檢驗 (長下影線攔截)
    # -------------------------------------------------------------
    if body_size > 0 and lower_wick > (0.6 * body_size) and lower_wick > (0.3 * atr):
        logger.info(f"⛔ [開空攔截] 原因: 下影線過長存在支撐買盤 (下影線={lower_wick:.4f}, 實體={body_size:.4f})")
        return False, "BLOCKED_BY_EXCESSIVE_LOWER_WICK"

    # -------------------------------------------------------------
    # 判定：是否觸發「強動能脈衝 (Super Impulse)」
    # -------------------------------------------------------------
    is_super_impulse = (
        (body_size >= 1.0 * atr) and                   # 實體大陰線
        (close_t < lower_band) and                     # 收盤實體摜破下軌
        (lower_wick <= 0.4 * body_size)                 # 收在相對低點
    )

    # -------------------------------------------------------------
    # 防線 4：冷卻期檢查 (強動能可直接豁免)
    # -------------------------------------------------------------
    if in_cooldown:
        if is_super_impulse:
            logger.info(f"⚡ [冷卻豁免] 觸發強動能大陰線貫穿 (實體={body_size:.4f} >= 1.0 ATR)，打斷冷卻期強制放行開空！")
        else:
            logger.info(f"⛔ [開空攔截] 原因: 處於平倉冷卻期內 (已過 {bars_since_exit}/{min_cooldown_bars} 根，剩餘 {min_cooldown_bars - bars_since_exit + 1} 根 K 棒)")
            return False, "BLOCKED_BY_COOLDOWN"

    # -------------------------------------------------------------
    # 防線 5：兩根破軌確認 (收盤價比對，不卡長影線低點)
    # -------------------------------------------------------------
    prev_close = float(prev_bar['close'])
    two_bars_break = (close_t < lower_band) and (prev_close < prev_lower)

    if not two_bars_break and not is_super_impulse:
        logger.info(f"⛔ [開空攔截] 原因: 未滿足兩根收盤破軌確認 (當前收={close_t:.4f}, 下軌={lower_band:.4f})")
        return False, "BLOCKED_BREAKOUT_NOT_CONFIRMED"

    # -------------------------------------------------------------
    # 全數通過
    # -------------------------------------------------------------
    logger.info(f"✅ [開空放行] 條件全數滿足 (強動能豁免={is_super_impulse}, KC斜率={kc_slope:+.5f})")
    return True, "ENTRY_APPROVED"


__all__ = [
    "StrictEntryAndImmunityGate",
    "default_strict_gate",
    "resolve_kc_trend",
    "check_kc_flat_or_squeezing",
    "check_long_entry_gate",
    "check_short_entry_gate",
]


