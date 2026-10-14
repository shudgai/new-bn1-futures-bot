"""StrictEntryAndImmunityGate
整合「3 根平滑斜率防走平開倉」、「逆向大趨勢與震盪一票否決」與「未破中軌/MA15 趨勢護航全程死抱否決權」之核心閘門。
"""
from typing import Tuple, Dict, Any, Optional
import pandas as pd


def resolve_kc_trend(df: Any) -> str:
    """判斷當前 KC 趨勢方向：'UP', 'DOWN', 或 'RANGE'。
    完全對齊前端圖表 getChartCkTrend 及大趨勢標準：
    - 若 channel_state/kc_trend 包含 'KC向下'/'DOWN'/'DOWNTREND'/'BEARISH'/'SHORT' -> 'DOWN'
    - 若 channel_state/kc_trend 包含 'KC向上'/'UP'/'UPTREND'/'BULLISH'/'LONG' -> 'UP'
    - 若最近已收線 bar 的 KC 中軌三連降或 (third < second 且 ma5 < ma15) -> 'DOWN'
    - 若最近已收線 bar 的 KC 中軌三連升或 (third > second 且 ma5 > ma15) -> 'UP'
    - 否則根據最近已收線兩根中軌斜率判定，無法判斷時返回 'RANGE'
    """
    if df is None or len(df) < 2:
        return 'RANGE'
    try:
        curr = df.iloc[-1]
        c_state = str(curr.get('channel_state', '') or curr.get('kc_trend', '')).strip().upper()
        if any(w in c_state for w in ('KC向下', 'DOWN', 'DOWNTREND', 'BEARISH', 'SHORT', 'KC_TREND_DOWN')):
            return 'DOWN'
        if any(w in c_state for w in ('KC向上', 'UP', 'UPTREND', 'BULLISH', 'LONG', 'KC_TREND_UP')):
            return 'UP'

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
        if len(closed_df) >= 3:
            first_closed = closed_df.iloc[-3]
            f_mid = float(first_closed.get('kc_middle', first_closed.get('kc_basis', 0.0)))
            ma5 = float(last_closed.get('ma5', 0.0))
            ma15 = float(last_closed.get('ma15', 0.0))
            if c_mid < p_mid and ma5 < ma15:
                return 'DOWN'
            if c_mid > p_mid and ma5 > ma15:
                return 'UP'
            if f_mid > p_mid and p_mid > c_mid:
                return 'DOWN'
            if f_mid < p_mid and p_mid < c_mid:
                return 'UP'

        if c_mid > 0 and p_mid > 0:
            if c_mid < p_mid - 1e-6:
                return 'DOWN'
            if c_mid > p_mid + 1e-6:
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
        # 1. 冷卻期檢核 (防連環刷單)
        if current_bar_index - self.last_exit_bar_index <= self.cooldown_bars:
            return False, f"VETO: 平倉冷卻中 (已過 {current_bar_index - self.last_exit_bar_index}/{self.cooldown_bars} 根)，禁止開倉"

        if df is None or len(df) < 2:
            return False, "VETO: K 棒數量不足，拒絕開倉"

        norm_side = 'LONG' if side in ('LONG', 'OPEN_LONG') else ('SHORT' if side in ('SHORT', 'OPEN_SHORT') else side)

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

