"""
Master Gate Funnel & Holding Protection Module
極度嚴謹的自動化交易系統審查專家與程式架構模組
"""

import math
import logging
from typing import Dict, Any, Optional, Tuple, NamedTuple

logger = logging.getLogger("MasterGateFunnel")


class MarketSnapshot(NamedTuple):
    symbol: str
    quote: float                # 最新報價
    open_price: float           # 當前 K 棒開盤價
    close_price: float          # 當前 K 棒最新收盤價/現價
    high_price: float           # 當前 K 棒最高價
    low_price: float            # 當前 K 棒最低價
    ma3_curr: float             # 當前 MA3
    ma3_prev: float             # 前一根 MA3
    ma5_curr: float             # 當前 MA5
    ma5_prev: float             # 前一根 MA5
    ma15_curr: float            # 當前 MA15
    ma15_prev: float            # 前一根 MA15
    kc_upper: float             # KC 上軌
    kc_middle: float            # KC 中軌
    kc_middle_prev: float       # 前一根 KC 中軌
    kc_lower: float             # KC 下軌
    atr: float                  # ATR 波動值
    macro_trend: str            # 大週期趨勢: "BULLISH", "BEARISH", "RANGE"
    ma_cross_count_8: int       # 近 8 根 K 棒 MA3 與 MA15 交叉次數
    timeframe_ms: float = 60000.0


class PositionSnapshot(NamedTuple):
    symbol: str
    side: str                   # "LONG" 或 "SHORT"
    entry_price: float          # 開倉成本價
    open_timestamp_ms: float    # 開倉時間戳 (毫秒)
    bars_held: int              # 已持倉 K 棒數
    peak_roe_pct: float         # 歷史最高 ROE 百分比 (如 15.0 代表 +15%)
    has_confirmed_pivot: bool   # 是否已收線確認三點真峰谷
    lowest_price_since_entry: float = 0.0   # 空單波段最低價
    highest_price_since_entry: float = 0.0  # 多單波段最高價


class GateDecision(NamedTuple):
    can_execute: bool
    action: str                 # "OPEN_LONG", "OPEN_SHORT", "HOLD", "CLOSE_POSITION"
    reason: str
    details: Dict[str, Any]


class MasterGateFunnel:
    """交易系統開平倉審查與防誤觸總閘門"""

    # ─────────────────────────────────────────────────────────────
    # 🚫 黑名單與無效訊號過濾 (符合任一即強制 HOLD)
    # ─────────────────────────────────────────────────────────────
    @classmethod
    def evaluate_market_blacklist(cls, m: MarketSnapshot) -> Tuple[bool, str]:
        if m.quote <= 0 or m.atr <= 0 or m.kc_middle <= 0:
            return True, "VETO_INVALID_MARKET_DATA"

        # 1. 垃圾時間震盪與均線糾纏 (Chop Filter)
        if m.ma_cross_count_8 >= 2:
            spread_ratio = abs(m.ma3_curr - m.ma15_curr) / m.quote
            if spread_ratio < 0.0018 or abs(m.ma3_curr - m.ma15_curr) < 0.10 * m.atr:
                return True, f"VETO_CHOP_ENTANGLEMENT (cross_8={m.ma_cross_count_8}, spread={spread_ratio:.4%})"

        # 2. KC 通道壓縮縮口 (Squeeze Filter: 寬度 < 1.0%)
        kc_width_ratio = (m.kc_upper - m.kc_lower) / m.quote
        if kc_width_ratio < 0.010:
            # 破軌爆發豁免：唯有實體 >= 0.7 ATR 且破軌才豁免
            is_breakout_long = m.close_price > m.kc_upper and (m.close_price - m.open_price) >= 0.70 * m.atr
            is_breakout_short = m.close_price < m.kc_lower and (m.open_price - m.close_price) >= 0.70 * m.atr
            if not (is_breakout_long or is_breakout_short):
                return True, f"VETO_NARROW_SQUEEZE_RANGE (width={kc_width_ratio:.2%})"

        # 3. 走平通道過濾 (Flat KC Filter: 斜率 < 0.05 ATR)
        kc_slope = m.kc_middle - m.kc_middle_prev
        ma15_slope = m.ma15_curr - m.ma15_prev
        if abs(kc_slope) < 0.05 * m.atr and abs(ma15_slope) < 0.05 * m.atr:
            is_rail_break = (m.close_price > m.kc_upper) or (m.close_price < m.kc_lower)
            if not is_rail_break:
                return True, f"VETO_FLAT_KC_MIDLINE_CHOP (kc_slope={kc_slope:.6f}, ma15_slope={ma15_slope:.6f})"

        return False, "PASS_MARKET_HEALTHY"

    # ─────────────────────────────────────────────────────────────
    # 🟢 開多審查門檻 (Open Long Gate)
    # ─────────────────────────────────────────────────────────────
    @classmethod
    def evaluate_open_long(cls, m: MarketSnapshot, is_in_cooldown: bool = False) -> GateDecision:
        details = {"symbol": m.symbol, "quote": m.quote, "macro": m.macro_trend}

        # 0. 冷卻檢查
        if is_in_cooldown:
            return GateDecision(False, "HOLD", "BLOCKED_BY_POST_EXIT_COOLDOWN", details)

        # 1. 執行黑名單檢驗
        is_blacklisted, bl_reason = cls.evaluate_market_blacklist(m)
        if is_blacklisted:
            logger.info(f"🛑 [GATE_LONG_FAIL] {m.symbol} 黑名單阻斷: {bl_reason}")
            return GateDecision(False, "HOLD", bl_reason, details)

        # 2. 大方向與通道過濾 (Filter 鐵律，一票否決)
        if m.macro_trend == "BEARISH":
            return GateDecision(False, "HOLD", "BLOCKED_LONG_ON_BEARISH_MACRO_TREND", details)

        kc_slope = m.kc_middle - m.kc_middle_prev
        if kc_slope <= 0:
            return GateDecision(False, "HOLD", "BLOCKED_LONG_KC_SLOPE_NOT_UP", details)

        if not (m.quote > m.kc_middle and m.close_price > m.kc_middle and m.ma3_curr > m.kc_middle):
            return GateDecision(False, "HOLD", "BLOCKED_LONG_NOT_ABOVE_KC_MIDDLE", details)

        if not (m.quote > m.ma15_curr and m.close_price > m.ma15_curr and m.ma3_curr > m.ma15_curr):
            return GateDecision(False, "HOLD", "BLOCKED_LONG_NOT_ABOVE_MA15", details)

        # 3. K 棒動能形態 (Candle Lock: 必須是實體綠 K)
        if not (m.close_price > m.open_price):
            return GateDecision(False, "HOLD", "BLOCKED_LONG_NOT_GREEN_CANDLE", details)

        # 4. 均線斜率與發散 (MA3 必須向上傾角且拉開間距)
        if m.ma3_curr <= m.ma3_prev:
            return GateDecision(False, "HOLD", "BLOCKED_LONG_MA3_SLOPE_NOT_UP", details)

        if (m.ma3_curr - m.ma15_curr) < 0.10 * m.atr:
            return GateDecision(False, "HOLD", "BLOCKED_LONG_MA3_MA15_SPREAD_TOO_NARROW", details)

        # 5. 防追高過濾 (Anti-Chase: 距 KC 上軌 <= 0.5 ATR, 距 MA5 <= 1.0 ATR)
        if (m.quote - m.kc_upper) > 0.50 * m.atr:
            return GateDecision(False, "HOLD", "BLOCKED_LONG_CHASE_KC_UPPER_EXTENSION", details)
        if (m.quote - m.ma5_curr) > 1.00 * m.atr:
            return GateDecision(False, "HOLD", "BLOCKED_LONG_CHASE_MA5_OVEREXTENDED", details)

        # 6. 進場觸發模式 (滿足其一即可)
        trigger_hit = None
        # 觸發 A: 破軌突破
        if m.close_price > m.kc_upper and m.ma5_curr > m.ma5_prev:
            trigger_hit = "TRIGGER_A_BREAKOUT_UPPER_RAIL"
        # 觸發 B: 回踩中軌轉強
        elif abs(m.low_price - m.kc_middle) <= 0.35 * m.atr and m.ma3_curr > m.ma3_prev and m.close_price > m.ma5_curr:
            trigger_hit = "TRIGGER_B_PULLBACK_KC_MIDDLE_BOUNCE"
        # 觸發 C: 大週期為牛市且實體突破 MA15
        elif m.macro_trend == "BULLISH" and m.open_price <= m.ma15_curr and m.close_price > m.ma15_curr:
            trigger_hit = "TRIGGER_C_EARLY_MA15_BREAKOUT"
        # 觸發 D: 順勢 MA5/MA15 金叉動能（通道有足夠獲利空間 >= 20% 通道寬度）
        elif (
            m.ma5_curr > m.ma5_prev
            and m.ma15_curr >= m.ma15_prev
            and (m.kc_upper - m.close_price) >= 0.20 * (m.kc_upper - m.kc_lower)
            and (m.close_price - m.open_price) >= 0.35 * m.atr
        ):
            trigger_hit = "TRIGGER_D_MA5_MA15_MOMENTUM_EXPANSION"

        if not trigger_hit:
            return GateDecision(False, "HOLD", "WAIT_VALID_LONG_TRIGGER", details)

        logger.info(f"🚀 [GATE_LONG_PASS] {m.symbol} 全條件審查通過！授權開多 ({trigger_hit})")
        return GateDecision(True, "OPEN_LONG", f"AUTHORIZED_LONG:{trigger_hit}", {**details, "trigger": trigger_hit})

    # ─────────────────────────────────────────────────────────────
    # 🔴 開空審查門檻 (Open Short Gate)
    # ─────────────────────────────────────────────────────────────
    @classmethod
    def evaluate_open_short(cls, m: MarketSnapshot, is_in_cooldown: bool = False) -> GateDecision:
        details = {"symbol": m.symbol, "quote": m.quote, "macro": m.macro_trend}

        # 0. 冷卻檢查
        if is_in_cooldown:
            return GateDecision(False, "HOLD", "BLOCKED_BY_POST_EXIT_COOLDOWN", details)

        # 1. 執行黑名單檢驗
        is_blacklisted, bl_reason = cls.evaluate_market_blacklist(m)
        if is_blacklisted:
            logger.info(f"🛑 [GATE_SHORT_FAIL] {m.symbol} 黑名單阻斷: {bl_reason}")
            return GateDecision(False, "HOLD", bl_reason, details)

        # 2. 大方向與通道過濾 (Filter 鐵律，一票否決)
        if m.macro_trend == "BULLISH":
            return GateDecision(False, "HOLD", "BLOCKED_SHORT_ON_BULLISH_MACRO_TREND", details)

        kc_slope = m.kc_middle - m.kc_middle_prev
        if kc_slope >= 0:
            return GateDecision(False, "HOLD", "BLOCKED_SHORT_KC_SLOPE_NOT_DOWN", details)

        if not (m.quote < m.kc_middle and m.close_price < m.kc_middle and m.ma3_curr < m.kc_middle):
            return GateDecision(False, "HOLD", "BLOCKED_SHORT_NOT_BELOW_KC_MIDDLE", details)

        if not (m.quote < m.ma15_curr and m.close_price < m.ma15_curr and m.ma3_curr < m.ma15_curr):
            return GateDecision(False, "HOLD", "BLOCKED_SHORT_NOT_BELOW_MA15", details)

        # 3. K 棒動能形態 (Candle Lock: 必須是實體紅 K)
        if not (m.close_price < m.open_price):
            return GateDecision(False, "HOLD", "BLOCKED_SHORT_NOT_RED_CANDLE", details)

        # 4. 均線斜率與發散 (MA3 必須向下傾角且拉開間距)
        if m.ma3_curr >= m.ma3_prev:
            return GateDecision(False, "HOLD", "BLOCKED_SHORT_MA3_SLOPE_NOT_DOWN", details)

        if (m.ma15_curr - m.ma3_curr) < 0.10 * m.atr:
            return GateDecision(False, "HOLD", "BLOCKED_SHORT_MA3_MA15_SPREAD_TOO_NARROW", details)

        # 5. 防追低過濾 (Anti-Chase: 距 KC 下軌 <= 0.5 ATR, 距 MA5 <= 1.0 ATR)
        # 嚴禁地板追空：若收盤價已經低於 KC 下軌，或距離 MA15 已超過 0.5 ATR，100% 嚴禁開空！
        if m.close_price < m.kc_lower:
            return GateDecision(False, "HOLD", "BLOCKED_SHORT_CHASE_BELOW_KC_LOWER", details)
        if (m.ma15_curr - m.close_price) > 0.50 * m.atr:
            return GateDecision(False, "HOLD", "BLOCKED_SHORT_CHASE_MA15_EXTENSION_GT_0_5_ATR", details)
        if (m.kc_lower - m.quote) > 0.50 * m.atr:
            return GateDecision(False, "HOLD", "BLOCKED_SHORT_CHASE_KC_LOWER_EXTENSION", details)
        if (m.ma5_curr - m.quote) > 1.00 * m.atr:
            return GateDecision(False, "HOLD", "BLOCKED_SHORT_CHASE_MA5_OVEREXTENDED", details)

        # 6. 進場觸發模式 (滿足其一即可)
        trigger_hit = None
        # 觸發 A: 頂部金叉轉死叉 / 破中軌即刻進場 (5:53 頂部型態精準開空)
        is_top_reversal = (
            (m.ma3_prev >= m.kc_middle_prev) and (m.ma3_curr < m.kc_middle)
            and (m.close_price < m.open_price)
            and (kc_slope < 0)
        )
        if is_top_reversal:
            trigger_hit = "TRIGGER_A_TOP_REVERSAL_CROSS_MID"
        # 觸發 B: 破軌加速跌破
        elif m.close_price < m.kc_lower and m.ma5_curr < m.ma5_prev:
            trigger_hit = "TRIGGER_B_BREAKDOWN_LOWER_RAIL"
        # 觸發 C: 反彈中軌受阻
        elif abs(m.high_price - m.kc_middle) <= 0.35 * m.atr and m.ma3_curr < m.ma3_prev and m.close_price < m.ma5_curr:
            trigger_hit = "TRIGGER_C_REBOUND_KC_MIDDLE_REJECTION"
        # 觸發 D: 大週期為熊市且實體跌破 MA15
        elif m.macro_trend == "BEARISH" and m.open_price >= m.ma15_curr and m.close_price < m.ma15_curr:
            trigger_hit = "TRIGGER_D_EARLY_MA15_BREAKDOWN"
        # 觸發 E: 順勢 MA5/MA15 死叉動能（通道有足夠獲利空間 >= 20% 通道寬度）
        elif (
            m.ma5_curr < m.ma5_prev
            and m.ma15_curr <= m.ma15_prev
            and (m.close_price - m.kc_lower) >= 0.20 * (m.kc_upper - m.kc_lower)
            and (m.open_price - m.close_price) >= 0.35 * m.atr
        ):
            trigger_hit = "TRIGGER_E_MA5_MA15_MOMENTUM_EXPANSION"

        if not trigger_hit:
            return GateDecision(False, "HOLD", "WAIT_VALID_SHORT_TRIGGER", details)

        logger.info(f"🚀 [GATE_SHORT_PASS] {m.symbol} 全條件審查通過！授權開空 ({trigger_hit})")
        return GateDecision(True, "OPEN_SHORT", f"AUTHORIZED_SHORT:{trigger_hit}", {**details, "trigger": trigger_hit})

    # ─────────────────────────────────────────────────────────────
    # 🛡️ 持倉呼吸與平倉保護審查 (Holding Protection Gate)
    # ─────────────────────────────────────────────────────────────
    @classmethod
    def evaluate_position_exit(
        cls,
        p: PositionSnapshot,
        m: MarketSnapshot,
        candidate_exit_reason: str,
        current_time_ms: float,
    ) -> GateDecision:
        details = {
            "symbol": p.symbol, "side": p.side, "bars_held": p.bars_held,
            "peak_roe": p.peak_roe_pct, "reason": candidate_exit_reason,
        }

        # 1. 最高優先：極端市場硬止損 / 穿倉熔斷 (Hard Stop 3.0 ATR)
        is_hard_sl = False
        if p.side == "LONG" and (p.entry_price - m.quote) >= 3.0 * m.atr:
            is_hard_sl = True
        elif p.side == "SHORT" and (m.quote - p.entry_price) >= 3.0 * m.atr:
            is_hard_sl = True

        if is_hard_sl:
            logger.warning(f"🚨 [CIRCUIT_BREAKER_HARD_SL] {p.symbol} 觸發 3.0 ATR 市場硬停損，無條件市價平倉！")
            return GateDecision(True, "CLOSE_POSITION", "CIRCUIT_BREAKER_HARD_SL", details)

        # 1.1 階梯式保本鎖利防線 (若曾賺超過 5.0% ROE，嚴禁回吐變虧損)
        if p.peak_roe_pct >= 5.0:
            current_roe = ((m.quote - p.entry_price) / p.entry_price * 100.0) if p.side == "LONG" else ((p.entry_price - m.quote) / p.entry_price * 100.0)
            if current_roe <= 1.5:
                logger.info(
                    f"🎯 [LOCK_PROFIT_PROTECTION] {p.symbol} 曾達最高浮盈 {p.peak_roe_pct:.2f}% ROE，"
                    f"現利縮水至 {current_roe:.2f}% <= 1.5% 保底防線，強制落袋保利，消滅過山車！"
                )
                return GateDecision(True, "CLOSE_POSITION", "EXIT_BY_BREAKEVEN_PROFIT_PROTECTION", details)

        # 2. 核心鐵律：持倉前 5 根 K 棒防震盪過敏保護 (Min Hold Duration)
        is_same_bar = bool(current_time_ms - p.open_timestamp_ms < m.timeframe_ms)
        if is_same_bar or p.bars_held < 5:
            # 前 5 根 K 棒內：只要空單價格沒有反向突破 KC 中軌 (或多單跌破中軌)，無視所有微幅回踩與未確認雜訊！
            is_early_hold = (
                (p.side == "SHORT" and m.close_price < m.kc_middle)
                or (p.side == "LONG" and m.close_price > m.kc_middle)
            )
            if is_early_hold and not p.has_confirmed_pivot:
                logger.info(
                    f"🛡️ [VETO_EARLY_HOLD] {p.symbol} 波段持倉初期防震盪保護 (bars_held={p.bars_held} < 5)，"
                    f"價格守於中軌順勢側，一票否決微幅回踩平倉請求 ({candidate_exit_reason})！"
                )
                return GateDecision(False, "HOLD", "VETO_EARLY_HOLD_MIN_5_BARS_PROTECTION", details)

        # 2.1 移動止盈 (Trailing Stop: 自波段最有利極值反彈超過 1.2 ATR 出場)
        if p.side == "SHORT" and p.lowest_price_since_entry > 0:
            rebound_from_trough = m.close_price - p.lowest_price_since_entry
            if rebound_from_trough >= 1.20 * m.atr and p.peak_roe_pct >= 5.0:
                logger.info(
                    f"🎯 [TRAILING_STOP_EXIT] {p.symbol} 空單自波段最低點 {p.lowest_price_since_entry:.6f} "
                    f"大幅反彈 {rebound_from_trough:.6f} >= 1.2 ATR ({1.2 * m.atr:.6f})，觸發移動止盈！"
                )
                return GateDecision(True, "CLOSE_POSITION", "EXIT_SHORT_TRAILING_STOP_1_2_ATR", details)
        elif p.side == "LONG" and p.highest_price_since_entry > 0:
            pullback_from_peak = p.highest_price_since_entry - m.close_price
            if pullback_from_peak >= 1.20 * m.atr and p.peak_roe_pct >= 5.0:
                logger.info(
                    f"🎯 [TRAILING_STOP_EXIT] {p.symbol} 多單自波段最高點 {p.highest_price_since_entry:.6f} "
                    f"大幅回撤 {pullback_from_peak:.6f} >= 1.2 ATR ({1.2 * m.atr:.6f})，觸發移動止盈！"
                )
                return GateDecision(True, "CLOSE_POSITION", "EXIT_LONG_TRAILING_STOP_1_2_ATR", details)

        # 3. 平倉動能寬容度與真谷底/真峰頂平倉線 (Exit Buffer)
        # 空單真谷底平倉線：收盤價實質突破 KC 中軌 + 0.1 ATR 且 MA3 > MA15
        if p.side == "SHORT":
            kc_middle_buffer = m.kc_middle + 0.10 * m.atr
            if m.close_price <= kc_middle_buffer:
                logger.info(f"🛡️ [VETO_HOLD] {p.symbol} 空單壓在 (KC中軌 + 0.1 ATR={kc_middle_buffer:.6f}) 之下，波段下跌未破壞，死抱空單！")
                return GateDecision(False, "HOLD", "VETO_HOLD_SHORT_UNDER_KC_MID_BUFFER", details)
        elif p.side == "LONG":
            kc_middle_buffer = m.kc_middle - 0.10 * m.atr
            if m.close_price >= kc_middle_buffer:
                logger.info(f"🛡️ [VETO_HOLD] {p.symbol} 多單守在 (KC中軌 - 0.1 ATR={kc_middle_buffer:.6f}) 之上，波段上漲未破壞，死抱多單！")
                return GateDecision(False, "HOLD", "VETO_HOLD_LONG_ABOVE_KC_MID_BUFFER", details)

        # 4. 陰陽 K 棒同向禁令：下殺紅 K 絕不平空，上攻綠 K 絕不平多
        if p.side == "SHORT" and m.close_price < m.open_price:
            logger.info(f"🛑 [VETO_EXIT] {p.symbol} 當前為下跌陰線/紅 K，順向推力中，嚴禁平空！")
            return GateDecision(False, "HOLD", "VETO_EXIT_SHORT_ON_RED_CANDLE", details)

        if p.side == "LONG" and m.close_price > m.open_price:
            logger.info(f"🛑 [VETO_EXIT] {p.symbol} 當前為上漲陽線/綠 K，順向上攻中，嚴禁平多！")
            return GateDecision(False, "HOLD", "VETO_EXIT_LONG_ON_GREEN_CANDLE", details)

        # 5. 授權正式平倉離場
        logger.info(f"🚀 [EXIT_AUTHORIZED] {p.symbol} 通過平倉審查 Gate，執行出場: {candidate_exit_reason}")
        return GateDecision(True, "CLOSE_POSITION", candidate_exit_reason, details)

