"""
Master Gate Funnel & Holding Protection Module (妖幣專用版)
專門設計「高波動/妖幣（Meme Coin / High Volatility Altcoins）」高頻與波段自動交易策略的系統架構模組
核心使命：精準捕捉頂部轉折、死抱波段平倉、爆發暴漲不漏單、封殺地板追空。
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
    """妖幣專用開平倉審查與波段控管總閘門 (Meme & Wild Coin Gate Funnel)"""

    # ─────────────────────────────────────────────────────────────
    # 🚫 二、妖幣無效訊號與黑名單阻斷 (防止在地板/天花板亂開槍)
    # ─────────────────────────────────────────────────────────────
    @classmethod
    def evaluate_market_blacklist(cls, m: MarketSnapshot) -> Tuple[bool, str]:
        if m.quote <= 0 or m.atr <= 0 or m.kc_middle <= 0:
            return True, "VETO_INVALID_MARKET_DATA"

        # 1. 垃圾時間震盪與均線糾纏 (Chop Filter: 妖幣近 8 根交錯 >= 3 次視為死水洗盤)
        if m.ma_cross_count_8 >= 3:
            spread_ratio = abs(m.ma3_curr - m.ma15_curr) / m.quote
            if spread_ratio < 0.0018 or abs(m.ma3_curr - m.ma15_curr) < 0.10 * m.atr:
                return True, f"VETO_CHOP_ENTANGLEMENT (cross_8={m.ma_cross_count_8} >= 3, spread={spread_ratio:.4%})"

        # 2. KC 通道壓縮縮口與爆發例外 (Squeeze Filter: 寬度 < 1.0%)
        # 遇妖幣拉大陽線暴漲 (實體 >= 0.8 ATR) 直接豁免
        kc_width_ratio = (m.kc_upper - m.kc_lower) / m.quote
        if kc_width_ratio < 0.010:
            is_huge_bull = (m.close_price > m.open_price) and ((m.close_price - m.open_price) >= 0.80 * m.atr)
            is_huge_bear = (m.close_price < m.open_price) and ((m.open_price - m.close_price) >= 0.80 * m.atr)
            if not (is_huge_bull or is_huge_bear):
                return True, f"VETO_NARROW_SQUEEZE_RANGE (width={kc_width_ratio:.2%})"

        # 3. 走平通道過濾 (Flat KC Filter: 斜率 < 0.05 ATR)
        kc_slope = m.kc_middle - m.kc_middle_prev
        ma15_slope = m.ma15_curr - m.ma15_prev
        if abs(kc_slope) < 0.05 * m.atr and abs(ma15_slope) < 0.05 * m.atr:
            is_burst = (
                ((m.close_price - m.open_price) >= 0.80 * m.atr)
                or (m.close_price > m.kc_upper)
                or (m.close_price < m.kc_lower)
            )
            if not is_burst:
                return True, f"VETO_FLAT_KC_MIDLINE_CHOP (kc_slope={kc_slope:.6f}, ma15_slope={ma15_slope:.6f})"

        return False, "PASS_MARKET_HEALTHY"

    # ─────────────────────────────────────────────────────────────
    # 🟢 一、妖幣做多開倉審查門檻 (Open Long Gate)
    # ─────────────────────────────────────────────────────────────
    @classmethod
    def evaluate_open_long(cls, m: MarketSnapshot, is_in_cooldown: bool = False) -> GateDecision:
        details = {"symbol": m.symbol, "quote": m.quote, "macro": m.macro_trend}

        # 0. 平倉冷卻檢查 (妖幣防搶跑冷卻時間為 2 根 K 棒)
        if is_in_cooldown:
            return GateDecision(False, "HOLD", "BLOCKED_BY_POST_EXIT_COOLDOWN", details)

        # 1. 執行黑名單檢驗
        is_blacklisted, bl_reason = cls.evaluate_market_blacklist(m)
        if is_blacklisted:
            logger.info(f"🛑 [GATE_LONG_FAIL] {m.symbol} 黑名單阻斷: {bl_reason}")
            return GateDecision(False, "HOLD", bl_reason, details)

        # 2. 大方向過濾 (一票否決)
        # 大週期絕不可為明確空頭 (not is_zoomed_out_bearish)
        if m.macro_trend == "BEARISH":
            return GateDecision(False, "HOLD", "BLOCKED_LONG_ON_BEARISH_MACRO_TREND", details)

        # 最新報價與 1m 收盤價必須站穩 KC 中軌與 MA15 之上
        if not (m.quote > m.kc_middle and m.close_price > m.kc_middle):
            return GateDecision(False, "HOLD", "BLOCKED_LONG_NOT_ABOVE_KC_MIDDLE", details)
        if not (m.quote > m.ma15_curr and m.close_price > m.ma15_curr):
            return GateDecision(False, "HOLD", "BLOCKED_LONG_NOT_ABOVE_MA15", details)

        # 3. 均線發散：MA3 斜率向上且大於 MA15
        if m.ma3_curr <= m.ma3_prev:
            return GateDecision(False, "HOLD", "BLOCKED_LONG_MA3_SLOPE_NOT_UP", details)
        if m.ma3_curr <= m.ma15_curr:
            return GateDecision(False, "HOLD", "BLOCKED_LONG_MA3_NOT_ABOVE_MA15", details)

        # 4. 妖幣防追高條款 (Squeeze Expansion: 遇到拉大陽線暴漲時，允許 Quote 距離 KC 上軌達 1.2 ATR)
        candle_body = m.close_price - m.open_price
        is_burst_candle = (candle_body >= 0.80 * m.atr)
        upper_extension_limit = 1.20 * m.atr if is_burst_candle else 0.50 * m.atr
        if (m.quote - m.kc_upper) > upper_extension_limit:
            return GateDecision(False, "HOLD", f"BLOCKED_LONG_CHASE_KC_UPPER_EXTENSION (limit={upper_extension_limit:.6f})", details)

        # 5. K 棒形態與爆發確認 (滿足其一即可)
        trigger_hit = None
        # 模式 A（縮口帶量噴發）：實體大陽線/綠 K (Close > Open 且實體 > 0.8 ATR)，直接授權進場
        if (m.close_price > m.open_price) and (candle_body >= 0.80 * m.atr):
            trigger_hit = "TRIGGER_A_BURST_GREEN_CANDLE"
        # 模式 B（回踩中軌轉強）：MA3 回踩 KC 中軌附近不破，重新拐頭向上金叉 MA15
        elif (
            abs(m.low_price - m.kc_middle) <= 0.35 * m.atr
            and m.ma3_curr > m.ma3_prev
            and m.close_price > m.ma5_curr
            and m.ma3_curr >= m.ma15_curr
        ):
            trigger_hit = "TRIGGER_B_PULLBACK_KC_MIDDLE_BOUNCE"
        # 模式 C（早期轉強）：1m K 棒強勢收綠實體突破 MA15
        elif (m.close_price > m.open_price) and (m.open_price <= m.ma15_curr) and (m.close_price > m.ma15_curr):
            trigger_hit = "TRIGGER_C_EARLY_MA15_BREAKOUT"

        if not trigger_hit:
            return GateDecision(False, "HOLD", "WAIT_VALID_LONG_TRIGGER", details)

        logger.info(f"🚀 [GATE_LONG_PASS] {m.symbol} 妖幣做多全條件通過！授權開多 ({trigger_hit})")
        return GateDecision(True, "OPEN_LONG", f"AUTHORIZED_LONG:{trigger_hit}", {**details, "trigger": trigger_hit})

    # ─────────────────────────────────────────────────────────────
    # 🔴 一、妖幣做空開倉審查門檻 (Open Short Gate)
    # ─────────────────────────────────────────────────────────────
    @classmethod
    def evaluate_open_short(cls, m: MarketSnapshot, is_in_cooldown: bool = False) -> GateDecision:
        details = {"symbol": m.symbol, "quote": m.quote, "macro": m.macro_trend}

        # 0. 平倉冷卻檢查 (妖幣防搶跑冷卻時間為 2 根 K 棒)
        if is_in_cooldown:
            return GateDecision(False, "HOLD", "BLOCKED_BY_POST_EXIT_COOLDOWN", details)

        # 1. 執行黑名單檢驗
        is_blacklisted, bl_reason = cls.evaluate_market_blacklist(m)
        if is_blacklisted:
            logger.info(f"🛑 [GATE_SHORT_FAIL] {m.symbol} 黑名單阻斷: {bl_reason}")
            return GateDecision(False, "HOLD", bl_reason, details)

        # 2. 大方向過濾 (一票否決)
        # 大週期絕不可為明確多頭 (not is_zoomed_out_bullish)。若大週期為牛市，100% 阻斷做空
        if m.macro_trend == "BULLISH":
            return GateDecision(False, "HOLD", "BLOCKED_SHORT_ON_BULLISH_MACRO_TREND", details)

        # 最新價格與 1m 收盤價必須嚴格壓制在 KC 中軌與 MA15 之下
        if not (m.quote < m.kc_middle and m.close_price < m.kc_middle):
            return GateDecision(False, "HOLD", "BLOCKED_SHORT_NOT_BELOW_KC_MIDDLE", details)
        if not (m.quote < m.ma15_curr and m.close_price < m.ma15_curr):
            return GateDecision(False, "HOLD", "BLOCKED_SHORT_NOT_BELOW_MA15", details)

        # 3. 均線發散：MA3 斜率向下且小於 MA15
        if m.ma3_curr >= m.ma3_prev:
            return GateDecision(False, "HOLD", "BLOCKED_SHORT_MA3_SLOPE_NOT_DOWN", details)
        if m.ma3_curr >= m.ma15_curr:
            return GateDecision(False, "HOLD", "BLOCKED_SHORT_MA3_NOT_BELOW_MA15", details)

        # 4. 妖幣地板追空黑名單 (Anti-Bottom Short Filter)
        # 價格跌穿 KC 下軌超過 0.8 ATR，或距離 MA15 乖離過大 (> 1.2 ATR)，100% 嚴禁開空！
        if (m.kc_lower - m.close_price) > 0.80 * m.atr:
            return GateDecision(False, "HOLD", "BLOCKED_SHORT_EXHAUSTION_BELOW_KC_LOWER_0_8_ATR", details)
        if (m.ma15_curr - m.close_price) > 1.20 * m.atr:
            return GateDecision(False, "HOLD", "BLOCKED_SHORT_OVEREXTENDED_MA15_GT_1_2_ATR", details)

        # 5. 頂部轉折與跌破確認 (必須滿足，抓 5:53 類型的頂部)
        trigger_hit = None
        kc_slope = m.kc_middle - m.kc_middle_prev

        # 模式 A（頂部轉折死叉）：MA3 剛向下死叉 KC 中軌（或 MA15），且當前 K 棒為實體陰線/紅 K，KC 中軌斜率向下下拐
        is_ma3_cross_mid_or_ma15 = (
            ((m.ma3_prev >= m.kc_middle_prev) and (m.ma3_curr < m.kc_middle))
            or ((m.ma3_prev >= m.ma15_prev) and (m.ma3_curr < m.ma15_curr))
        )
        if is_ma3_cross_mid_or_ma15 and (m.close_price < m.open_price) and (kc_slope <= 0):
            trigger_hit = "TRIGGER_A_TOP_REVERSAL_DEAD_CROSS"
        # 模式 B（破軌跌破）：價格跌破 KC 下軌，MA5 與 MA3 均向下傾斜發散
        elif (m.close_price < m.kc_lower) and (m.ma5_curr < m.ma5_prev) and (m.ma3_curr < m.ma3_prev):
            trigger_hit = "TRIGGER_B_BREAKDOWN_LOWER_RAIL"

        if not trigger_hit:
            return GateDecision(False, "HOLD", "WAIT_VALID_SHORT_TRIGGER", details)

        logger.info(f"🚀 [GATE_SHORT_PASS] {m.symbol} 妖幣做空全條件通過！授權開空 ({trigger_hit})")
        return GateDecision(True, "OPEN_SHORT", f"AUTHORIZED_SHORT:{trigger_hit}", {**details, "trigger": trigger_hit})

    # ─────────────────────────────────────────────────────────────
    # 🛡️ 三、妖幣死抱波段與防插針平倉機制 (解決當根/回踩秒平)
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

        # 1. 最高優先：極端市場硬停損 (Hard Stop Loss：多單跌破 KC 下軌 / 空單突破 KC 上軌 或 3.0 ATR)
        is_hard_stop = False
        if p.side == "LONG":
            if (m.close_price < m.kc_lower) or ((p.entry_price - m.quote) >= 3.0 * m.atr):
                is_hard_stop = True
        elif p.side == "SHORT":
            if (m.close_price > m.kc_upper) or ((m.quote - p.entry_price) >= 3.0 * m.atr):
                is_hard_stop = True

        if is_hard_stop:
            logger.warning(f"🚨 [HARD_STOP_LOSS] {p.symbol} 觸發外軌擊穿或 3.0 ATR 硬停損，市價避險離場！")
            return GateDecision(True, "CLOSE_POSITION", "HARD_STOP_LOSS", details)

        # 1.1 階梯式保本防線 (若曾賺超過 5.0% ROE，保底鎖在 1.5% 防過山車)
        if p.peak_roe_pct >= 5.0:
            current_roe = ((m.quote - p.entry_price) / p.entry_price * 100.0) if p.side == "LONG" else ((p.entry_price - m.quote) / p.entry_price * 100.0)
            if current_roe <= 1.5:
                logger.info(
                    f"🎯 [LOCK_PROFIT_PROTECTION] {p.symbol} 曾達浮盈 {p.peak_roe_pct:.2f}% ROE，"
                    f"現利縮水至 {current_roe:.2f}% <= 1.5% 保底防線，強制落袋保利！"
                )
                return GateDecision(True, "CLOSE_POSITION", "EXIT_BY_BREAKEVEN_PROFIT_PROTECTION", details)

        # 2. 妖幣持倉抗震期 (Noise Protection Period: 前 3~5 根 1m K 棒)
        # 保護期內，除非觸發硬停損，否則無視任何微幅回踩與下影線插針
        is_same_bar = bool(current_time_ms - p.open_timestamp_ms < m.timeframe_ms)
        if is_same_bar or p.bars_held < 4:
            if not p.has_confirmed_pivot:
                logger.info(
                    f"🛡️ [VETO_NOISE_PROTECTION] {p.symbol} 妖幣持倉抗震期內 (bars_held={p.bars_held} < 4, same_bar={is_same_bar})，"
                    f"一票否決插針回踩平倉請求 ({candidate_exit_reason})！"
                )
                return GateDecision(False, "HOLD", "VETO_NOISE_PROTECTION_PERIOD", details)

        # 3. 動態移動止盈 (Trailing Take-Profit: 最大浮盈 > 2.0 ATR 時啟動，回撤超過 1.2 ATR 觸發)
        favorable_gain_atr = 0.0
        if p.side == "SHORT" and p.lowest_price_since_entry > 0 and p.entry_price > 0:
            favorable_gain_atr = (p.entry_price - p.lowest_price_since_entry) / m.atr
            rebound_from_trough = m.close_price - p.lowest_price_since_entry
            if favorable_gain_atr >= 2.0 and rebound_from_trough >= 1.20 * m.atr:
                logger.info(
                    f"🎯 [TRAILING_TP_EXIT] {p.symbol} 空單波段浮盈 {favorable_gain_atr:.2f} ATR >= 2.0 ATR，"
                    f"自最低點反彈 {rebound_from_trough/m.atr:.2f} ATR >= 1.2 ATR，動態移動止盈出場！"
                )
                return GateDecision(True, "CLOSE_POSITION", "EXIT_SHORT_TRAILING_TP_1_2_ATR", details)
        elif p.side == "LONG" and p.highest_price_since_entry > 0 and p.entry_price > 0:
            favorable_gain_atr = (p.highest_price_since_entry - p.entry_price) / m.atr
            pullback_from_peak = p.highest_price_since_entry - m.close_price
            if favorable_gain_atr >= 2.0 and pullback_from_peak >= 1.20 * m.atr:
                logger.info(
                    f"🎯 [TRAILING_TP_EXIT] {p.symbol} 多單波段浮盈 {favorable_gain_atr:.2f} ATR >= 2.0 ATR，"
                    f"自最高點回撤 {pullback_from_peak/m.atr:.2f} ATR >= 1.2 ATR，動態移動止盈出場！"
                )
                return GateDecision(True, "CLOSE_POSITION", "EXIT_LONG_TRAILING_TP_1_2_ATR", details)

        # 4. 妖幣波段平倉硬指標 (Exit Gates)
        # 空單平倉：必須 1m 收盤價實質突破並站穩 KC 中軌 + 0.15 ATR 緩衝，且 MA3 > MA15 金叉
        if p.side == "SHORT":
            kc_mid_buffer = m.kc_middle + 0.15 * m.atr
            is_mid_reclaimed = (m.close_price > kc_mid_buffer)
            is_ma3_golden_cross = (m.ma3_curr > m.ma15_curr)
            if not (is_mid_reclaimed and is_ma3_golden_cross):
                logger.info(
                    f"🛡️ [VETO_HOLD_SHORT] {p.symbol} 未滿足平空硬指標 (close={m.close_price:.6f} <= mid_buffer={kc_mid_buffer:.6f} "
                    f"或 MA3 未金叉 MA15)，堅決死抱空單！"
                )
                return GateDecision(False, "HOLD", "VETO_HOLD_SHORT_BAND_AND_MA_UNCONFIRMED", details)
        # 多單平倉：必須 1m 收盤價實質跌破 KC 中軌 - 0.15 ATR 緩衝，且 MA3 < MA15 死叉
        elif p.side == "LONG":
            kc_mid_buffer = m.kc_middle - 0.15 * m.atr
            is_mid_broken = (m.close_price < kc_mid_buffer)
            is_ma3_dead_cross = (m.ma3_curr < m.ma15_curr)
            if not (is_mid_broken and is_ma3_dead_cross):
                logger.info(
                    f"🛡️ [VETO_HOLD_LONG] {p.symbol} 未滿足平多硬指標 (close={m.close_price:.6f} >= mid_buffer={kc_mid_buffer:.6f} "
                    f"或 MA3 未死叉 MA15)，堅決死抱多單！"
                )
                return GateDecision(False, "HOLD", "VETO_HOLD_LONG_BAND_AND_MA_UNCONFIRMED", details)

        # 5. 陰陽 K 棒同向禁令：下殺紅 K 絕不平空，上攻綠 K 絕不平多
        if p.side == "SHORT" and m.close_price < m.open_price:
            logger.info(f"🛑 [VETO_EXIT] {p.symbol} 當前為下跌陰線/紅 K，順向推力中，嚴禁平空！")
            return GateDecision(False, "HOLD", "VETO_EXIT_SHORT_ON_RED_CANDLE", details)

        if p.side == "LONG" and m.close_price > m.open_price:
            logger.info(f"🛑 [VETO_EXIT] {p.symbol} 當前為上漲陽線/綠 K，順向上攻中，嚴禁平多！")
            return GateDecision(False, "HOLD", "VETO_EXIT_LONG_ON_GREEN_CANDLE", details)

        # 6. 授權正式平倉離場
        logger.info(f"🚀 [EXIT_AUTHORIZED] {p.symbol} 妖幣通過平倉審查 Gate，執行出場: {candidate_exit_reason}")
        return GateDecision(True, "CLOSE_POSITION", candidate_exit_reason, details)
