"""
core/gates/trend_riding_strategy.py
策略核心架構最終整合規範：通道收窄中繼再進場、多空趨勢鎖倉與防滑價機制

本模組嚴格遵循 2026-10-15 最新授權規範：
一、 市場趨勢大結構判定 (Regime Filter)
    - 大格局多頭 (MACRO_BULL): 基準線 (15 MA / 20 EMA / KC中軌) 斜率持續向上，價格持續在基準線上方或回踩未實體跌破。
    - 大格局空頭 (MACRO_BEAR): 基準線斜率持續向下，價格持續在基準線下方。
二、 通道變窄時的「中繼再開倉機制」 (Squeeze Re-entry)
    - 大格局多頭 + 通道收斂: 視為「高位中繼蓄勢」，滿足回踩確認或重新放量擴張突破 KC 上軌時，立即追入並解除冷卻限制。
    - 大格局空頭 + 通道收斂: 視為「下跌中繼蓄勢」，滿足回抽未破或重新跌破 KC 下軌時，立即追空並解除冷卻限制。
三、 持倉中的「趨勢鎖倉」防洗盤規則 (Trend-Riding Mode)
    - 取消微幅回吐平倉 (禁止盤中 2%~3% 即時止盈)。
    - 多單: 即使從 KC 上軌拉回通道內，只要實體 K 棒未收盤跌破基準線，一律強制 HOLD。
    - 空單: 即使從 KC 下軌反抽回通道內，只要實體 K 棒未收盤突破站上基準線，一律強制 HOLD。
四、 合法平倉條件 (Only Close When)
    - 結構被破 (Trend Invalidation): 多單收盤跌破基準線；空單收盤突破基準線。
    - 爆量高位滯漲力竭 (Climax Exit): 價格在通道外嚴重乖離 + 2 倍以上巨量 + 長影線 (多單長上影 >= 50%、空單長下影 >= 50%)。
五、 出場執行防滑價機制 (No Market Orders)
    - 廢棄 Stop Market，改以帶 0.2%~0.3% 讓步緩衝之 Limit Order (reduceOnly=True)。
    - 內建 3 秒監控 Watchdog: 超時未完全成交則自動撤單改抓最新對手價重掛。
"""

import math
import time
import asyncio
import logging
from typing import Dict, Any, Tuple, Optional, Union
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class TrendRidingStrategy:
    """多空趨勢鎖倉、通道收窄中繼再進場與對稱防洗盤策略控制器"""

    def __init__(
        self,
        cooldown_bars: int = 5,
        max_bias_atr: float = 2.0,
        climax_volume_multiplier: float = 2.0,
        exhaustion_wick_ratio: float = 0.50,
        offset_pct: float = 0.002,
        watchdog_timeout_sec: float = 3.0,
    ):
        """
        :param cooldown_bars: 平倉後同向常規開倉冷卻 K 棒數 (預設 5 根，擴張突破可豁免)
        :param max_bias_atr: 距離基準線最大允許進場正負乖離 (超過 2.0 ATR 禁止追單)
        :param climax_volume_multiplier: 頂/底力竭爆量乘數 (預設 2.0 倍均量)
        :param exhaustion_wick_ratio: 頂部長上影線 / 底部長下影線力竭閾值 (預設 50%)
        :param offset_pct: 限價防滑平倉讓步緩衝比例 (預設 0.2% = 0.002)
        :param watchdog_timeout_sec: 限價平倉 Watchdog 撮合超時秒數 (預設 3.0 秒)
        """
        self.cooldown_bars = cooldown_bars
        self.max_bias_atr = max_bias_atr
        self.climax_volume_multiplier = climax_volume_multiplier
        self.exhaustion_wick_ratio = exhaustion_wick_ratio
        self.offset_pct = offset_pct
        self.watchdog_timeout_sec = watchdog_timeout_sec

    # ══════════════════════════════════════════════════════════════════════
    # 【一、 市場趨勢大結構判定 (Regime Filter)】
    # ══════════════════════════════════════════════════════════════════════
    def evaluate_regime(self, df: pd.DataFrame) -> Tuple[str, Dict[str, Any]]:
        """大結構趨勢與通道收窄狀態評估
        
        回傳:
            (macro_regime, details):
            macro_regime 取值為 'MACRO_BULL' | 'MACRO_BEAR' | 'RANGE_CHOP'
            details 包含 is_squeeze, squeeze_regime, bandwidth 等詳細數據
        """
        if df is None or len(df) < 5:
            return "RANGE_CHOP", {"reason": "INSUFFICIENT_DATA", "is_squeeze": False}

        recent = df.tail(10)
        curr = recent.iloc[-1]
        prev = recent.iloc[-2]

        atr = float(curr.get('atr', 0.0))
        if atr <= 0:
            atr = max(float(curr.get('close', 1.0)) * 0.01, 1e-8)

        # 基準線 (優先取 kc_middle，次取 ma15，再次取 ema20)
        curr_basis = float(curr.get('kc_middle', curr.get('ma15', curr.get('ema20', curr.get('close', 0.0)))))
        prev_basis = float(prev.get('kc_middle', prev.get('ma15', prev.get('ema20', curr_basis))))
        prev_3_basis = float(recent.iloc[-3].get('kc_middle', recent.iloc[-3].get('ma15', curr_basis)))

        curr_upper = float(curr.get('kc_upper', curr_basis + 2 * atr))
        prev_upper = float(prev.get('kc_upper', prev_basis + 2 * atr))
        curr_lower = float(curr.get('kc_lower', curr_basis - 2 * atr))
        prev_lower = float(prev.get('kc_lower', prev_basis - 2 * atr))

        # 基準線斜率 (標準化為 ATR)
        basis_slope_atr = (curr_basis - prev_3_basis) / (atr * 2.0) if atr > 0 else 0.0
        basis_slope = curr_basis - prev_basis

        # 通道帶寬與收窄 (Bandwidth & Squeeze)
        bandwidth = (curr_upper - curr_lower) / curr_basis if curr_basis > 0 else 0.0
        prev_bandwidth = (prev_upper - prev_lower) / prev_basis if prev_basis > 0 else 0.0

        # 計算近期平均帶寬 (以最近 10 根為基準)
        if 'kc_upper' in df.columns and 'kc_lower' in df.columns and 'kc_middle' in df.columns:
            bws = ((df['kc_upper'] - df['kc_lower']) / df['kc_middle']).replace(0, np.nan).dropna()
            avg_bandwidth = float(bws.tail(20).mean()) if len(bws) >= 5 else bandwidth
            max_bandwidth = float(bws.tail(50).max()) if len(bws) >= 5 else bandwidth
        else:
            avg_bandwidth = bandwidth
            max_bandwidth = bandwidth

        # 通道收窄判定：帶寬小於均值的 0.85 倍或帶寬收斂縮窄中
        is_squeeze = bool(
            (bandwidth < avg_bandwidth * 0.85) or
            (max_bandwidth > 0 and bandwidth <= max_bandwidth * 0.35) or
            (bandwidth <= prev_bandwidth and bandwidth < avg_bandwidth)
        )
        is_expanding = bool(bandwidth > prev_bandwidth and bandwidth >= avg_bandwidth * 0.90)

        # 價格在基準線上的表現 (過去 5 根收盤)
        last_5 = recent.tail(5)
        closes = last_5['close'].astype(float).values
        bases = last_5['kc_middle'].astype(float).values if 'kc_middle' in last_5.columns else np.full(len(closes), curr_basis)
        above_count = sum(c >= b for c, b in zip(closes, bases))
        below_count = sum(c <= b for c, b in zip(closes, bases))

        c = float(curr.get('close', 0.0))
        l = float(curr.get('low', 0.0))
        h = float(curr.get('high', 0.0))

        # 判定大結構趨勢 (要求中長天期基準線具備明確正負斜率)
        is_macro_bull = (basis_slope > 0.0001 or basis_slope_atr > 0.015) and (above_count >= 3 or c >= curr_basis)
        is_macro_bear = (basis_slope < -0.0001 or basis_slope_atr < -0.015) and (below_count >= 3 or c <= curr_basis)

        if is_macro_bull and not is_macro_bear:
            macro_regime = "MACRO_BULL"
        elif is_macro_bear and not is_macro_bull:
            macro_regime = "MACRO_BEAR"
        else:
            macro_regime = "RANGE_CHOP"

        # 中繼蓄勢判定
        squeeze_regime = "NONE"
        if is_squeeze:
            if macro_regime == "MACRO_BULL" and c >= curr_basis:
                squeeze_regime = "BULL_SQUEEZE_CONSOLIDATION"  # 多頭高位中繼蓄勢
            elif macro_regime == "MACRO_BEAR" and c <= curr_basis:
                squeeze_regime = "BEAR_SQUEEZE_CONSOLIDATION"  # 空頭下跌中繼蓄勢
            else:
                squeeze_regime = "CHOP_SQUEEZE"

        details = {
            'macro_regime': macro_regime,
            'curr_basis': curr_basis,
            'basis_slope': basis_slope,
            'basis_slope_atr': round(basis_slope_atr, 4),
            'bandwidth': round(bandwidth, 6),
            'prev_bandwidth': round(prev_bandwidth, 6),
            'avg_bandwidth': round(avg_bandwidth, 6),
            'is_squeeze': is_squeeze,
            'is_expanding': is_expanding,
            'squeeze_regime': squeeze_regime,
            'above_count': above_count,
            'below_count': below_count,
            'atr': atr,
        }
        return macro_regime, details

    def evaluate_market_regime(self, df: pd.DataFrame) -> Tuple[str, Dict[str, Any]]:
        """向後相容接口：將 MACRO_BULL 映為 BULL_TREND，MACRO_BEAR 映為 BEAR_TREND"""
        macro, details = self.evaluate_regime(df)
        if macro == "MACRO_BULL":
            return "BULL_TREND", details
        elif macro == "MACRO_BEAR":
            return "BEAR_TREND", details
        return "RANGE_CHOP", details

    # ══════════════════════════════════════════════════════════════════════
    # 【二、 通道變窄時的「中繼再開倉機制」(Squeeze Re-entry)】
    # ══════════════════════════════════════════════════════════════════════
    def can_reenter_position(
        self,
        side: str,
        df: pd.DataFrame,
        quote: Optional[float] = None,
        last_closed_action: str = "",
        last_closed_ts: float = 0.0,
        timeframe_ms: float = 60_000.0,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """通道收窄時的二次進場審查 (Squeeze Re-entry)
        
        核心規範：
        1. 若大格局為多頭 (MACRO_BULL == True):
           即使通道收窄，只要價格守在基準線之上，視為「高位中繼蓄勢」，允許重新開多。
           觸發條件 (滿足任一):
             ① 回踩支撐確認: 回踩 3/5 EMA 或 KC 中軌未破並收出確認陽線 (Low >= Basis 且 Close > Open)。
             ② 重新放量擴張突破: 通道由窄開始放大 (Expansion)，實體陽線突破 KC 上軌 (Close > KC_Upper)，
                立即無條件追入，【解除任何冷卻期限制】！
        2. 若大格局為空頭 (MACRO_BEAR == True，完全對稱):
           即使通道收窄，只要價格壓制在基準線之下，視為「下跌中繼蓄勢」，允許重新開空。
           觸發條件 (滿足任一):
             ① 回抽阻力確認: 回抽 3/5 EMA 或 KC 中軌未破並收陰線 (Close < Open 且收在基準線下)。
             ② 重新放量擴張突破: 通道放大且實體陰線跌破 KC 下軌 (Close < KC_Lower)，
                立即無條件追空，【解除任何冷卻期限制】！
        """
        if df is None or len(df) < 5:
            return False, "VETO: INSUFFICIENT_DATA", {}

        side = str(side).upper()
        if side not in ('LONG', 'SHORT'):
            return False, "VETO: INVALID_SIDE", {}

        macro_regime, regime_details = self.evaluate_regime(df)
        curr = df.iloc[-1]
        prev = df.iloc[-2]

        c = float(curr.get('close', 0.0))
        o = float(curr.get('open', 0.0))
        h = float(curr.get('high', 0.0))
        l = float(curr.get('low', 0.0))
        p = float(quote) if quote is not None else c

        atr = float(curr.get('atr', 0.0))
        if atr <= 0:
            atr = max(c * 0.01, 1e-8)

        basis = float(curr.get('kc_middle', curr.get('ma15', c)))
        kc_upper = float(curr.get('kc_upper', basis + 2 * atr))
        kc_lower = float(curr.get('kc_lower', basis - 2 * atr))
        ma5 = float(curr.get('ma5', c))
        ema3 = float(curr.get('ema3', curr.get('ma3', ma5)))

        is_squeeze = regime_details.get('is_squeeze', False)
        is_expanding = regime_details.get('is_expanding', False)
        bandwidth = regime_details.get('bandwidth', 0.0)
        prev_bandwidth = regime_details.get('prev_bandwidth', 0.0)

        details = {
            'side': side,
            'macro_regime': macro_regime,
            'is_squeeze': is_squeeze,
            'is_expanding': is_expanding,
            'basis': basis,
            'close': c,
            'quote': p,
            'kc_upper': kc_upper,
            'kc_lower': kc_lower,
            'bandwidth': bandwidth,
            'atr': atr,
        }

        # ──【A. 多單中繼再進場 (LONG RE-ENTRY)】──
        if side == 'LONG':
            # 必須在大格局多頭結構下或中軌上方
            if macro_regime == 'MACRO_BEAR' or c < basis:
                return False, f"VETO: BLOCKED_LONG_ON_BEAR_TREND / UNFAVORABLE_HALF (大格局非多頭且收盤 {c:.6g} < 基準線 {basis:.6g})", details

            # 條件 ②：重新放量擴張突破 KC 上軌 (通道由窄開始放大且實體陽線突破 KC 上軌，優先權最高，直接豁免冷卻)
            bw_expanding = (bandwidth > prev_bandwidth) or is_expanding
            is_breakout = ((c > kc_upper and c > o) or (p > kc_upper and c >= kc_upper * 0.998)) and bw_expanding
            if is_breakout:
                details['reentry_type'] = 'EXPANSION_BREAKOUT'
                details['bypass_cooldown'] = True
                return True, "PASS: SQUEEZE_EXPANSION_BREAKOUT_LONG (通道重新放大且突破上軌，無條件追多解除冷卻)", details

            # 條件 ①：回踩支撐確認 (收窄期間回踩 3/5 EMA 或 KC 中軌未破並收陽線)
            # Low 碰觸/靠近 EMA3/MA5/KC中軌，收盤仍守在中軌之上，且當根為紅陽棒
            tested_support = (l <= max(ma5, ema3) or l <= basis + 0.3 * atr) and (l >= basis - 0.1 * atr)
            candle_is_bullish = (c > o)
            closed_above_support = (c >= min(ma5, ema3, basis))

            if tested_support and candle_is_bullish and closed_above_support:
                # 檢查冷卻保護 (常規回踩需受平倉冷卻限制，若已過冷卻則放行)
                if last_closed_action in ('CLOSE_LONG', 'EXIT_LONG') and last_closed_ts > 0:
                    curr_ts = float(curr.get('timestamp', time.time() * 1000))
                    bars_since_close = (curr_ts - last_closed_ts) / timeframe_ms
                    if bars_since_close < self.cooldown_bars:
                        return False, f"VETO: COOLDOWN_ACTIVE (回踩確認但距上次平倉僅 {bars_since_close:.1f} 根 < {self.cooldown_bars} 根)", details

                details['reentry_type'] = 'PULLBACK_SUPPORT_CONFIRMED'
                return True, "PASS: SQUEEZE_PULLBACK_CONFIRMED_LONG (高位中繼回踩支撐未破收陽，確認再開多)", details

            return False, "VETO: SQUEEZE_WAIT_TRIGGER (通道收窄中繼蓄勢中，等待回踩確認或破軌擴張突破)", details

        # ──【B. 空單中繼再進場 (SHORT RE-ENTRY，完全對稱)】──
        elif side == 'SHORT':
            if macro_regime == 'MACRO_BULL' or c > basis:
                return False, f"VETO: BLOCKED_SHORT_ON_BULL_TREND / UNFAVORABLE_HALF (大格局非空頭且收盤 {c:.6g} > 基準線 {basis:.6g})", details

            # 條件 ②：重新向下放量突破 KC 下軌 (通道由窄開始放大且實體陰線跌破 KC 下軌，直接豁免冷卻)
            bw_expanding = (bandwidth > prev_bandwidth) or is_expanding
            is_breakdown = ((c < kc_lower and c < o) or (p < kc_lower and c <= kc_lower * 1.002)) and bw_expanding
            if is_breakdown:
                details['reentry_type'] = 'EXPANSION_BREAKDOWN'
                details['bypass_cooldown'] = True
                return True, "PASS: SQUEEZE_EXPANSION_BREAKDOWN_SHORT (通道重新放大且跌破下軌，無條件追空解除冷卻)", details

            # 條件 ①：回抽阻力確認 (收窄期間回抽 3/5 EMA 或 KC 中軌未破並收黑陰棒)
            tested_resistance = (h >= min(ma5, ema3) or h >= basis - 0.3 * atr) and (h <= basis + 0.1 * atr)
            candle_is_bearish = (c < o)
            closed_below_resistance = (c <= max(ma5, ema3, basis))

            if tested_resistance and candle_is_bearish and closed_below_resistance:
                if last_closed_action in ('CLOSE_SHORT', 'EXIT_SHORT') and last_closed_ts > 0:
                    curr_ts = float(curr.get('timestamp', time.time() * 1000))
                    bars_since_close = (curr_ts - last_closed_ts) / timeframe_ms
                    if bars_since_close < self.cooldown_bars:
                        return False, f"VETO: COOLDOWN_ACTIVE (回抽確認但距上次平倉僅 {bars_since_close:.1f} 根 < {self.cooldown_bars} 根)", details

                details['reentry_type'] = 'PULLBACK_RESISTANCE_CONFIRMED'
                return True, "PASS: SQUEEZE_PULLBACK_CONFIRMED_SHORT (下跌中繼回抽阻力未破收陰，確認再開空)", details

            return False, "VETO: SQUEEZE_WAIT_TRIGGER (通道收窄中繼蓄勢中，等待回抽確認或下軌擴張跌破)", details

        return False, "VETO: UNKNOWN_SIDE", details

    # ══════════════════════════════════════════════════════════════════════
    # 【通盤開倉審查入口 can_open_position (整合常規與中繼再開倉)】
    # ══════════════════════════════════════════════════════════════════════
    def can_open_position(
        self,
        df: pd.DataFrame,
        side: str,
        quote: Optional[float] = None,
        last_closed_action: str = "",
        last_closed_ts: float = 0.0,
        timeframe_ms: float = 60_000.0,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """開倉審查：通道收窄時智能切換至 Squeeze Re-entry 機制，通道張開時走常規趨勢突破"""
        if df is None or len(df) < 5:
            return False, "VETO: INSUFFICIENT_DATA", {}

        macro_regime, regime_details = self.evaluate_regime(df)
        is_squeeze = regime_details.get('is_squeeze', False)

        # 若當前通道處於收窄狀態，優先以「中繼再進場機制」進行評估
        if is_squeeze:
            can_reenter, reason, details = self.can_reenter_position(
                side=side,
                df=df,
                quote=quote,
                last_closed_action=last_closed_action,
                last_closed_ts=last_closed_ts,
                timeframe_ms=timeframe_ms,
            )
            if can_reenter:
                return True, reason, details
            # 若中繼進場尚未觸發，返回阻斷理由
            return False, reason, details

        # ── 通道正常 / 擴張狀態下的常規審查 ──
        curr = df.iloc[-1]
        c = float(curr.get('close', 0.0))
        atr = float(curr.get('atr', 0.0))
        if atr <= 0:
            atr = max(c * 0.01, 1e-8)
        basis = float(curr.get('kc_middle', curr.get('ma15', c)))
        kc_upper = float(curr.get('kc_upper', basis + 2 * atr))
        kc_lower = float(curr.get('kc_lower', basis - 2 * atr))
        ma5 = float(curr.get('ma5', c))
        ema3 = float(curr.get('ema3', curr.get('ma3', ma5)))

        details = {
            'side': side,
            'macro_regime': macro_regime,
            'basis': basis,
            'close': c,
            'quote': quote or c,
            'atr': atr,
        }

        # 1. 冷卻保護
        target_close_actions = ('CLOSE_LONG', 'EXIT_LONG') if side == 'LONG' else ('CLOSE_SHORT', 'EXIT_SHORT')
        if last_closed_action in target_close_actions and last_closed_ts > 0:
            curr_ts = float(curr.get('timestamp', time.time() * 1000))
            bars_since = (curr_ts - last_closed_ts) / timeframe_ms
            if bars_since < self.cooldown_bars:
                return False, f"VETO: COOLDOWN_ACTIVE (平倉後僅 {bars_since:.1f} 根，未滿 {self.cooldown_bars} 根)", details

        # 2. 順勢鐵律
        if side == 'LONG':
            if macro_regime == 'MACRO_BEAR' or c < basis:
                return False, "VETO: BLOCKED_LONG_ON_BEAR_TREND (空頭大格局或中軌下方嚴禁開多)", details
        elif side == 'SHORT':
            if macro_regime == 'MACRO_BULL' or c > basis:
                return False, "VETO: BLOCKED_SHORT_ON_BULL_TREND (多頭大格局或中軌上方嚴禁開空)", details

        # 3. 乖離率限制
        bias_atr = abs(c - basis) / atr
        details['bias_atr'] = round(bias_atr, 2)
        if bias_atr > self.max_bias_atr:
            return False, f"VETO: OVEREXTENDED_BIAS (乖離 {bias_atr:.2f} ATR > {self.max_bias_atr} ATR，嚴禁懸空追單)", details

        return True, "PASS: TREND_RIDING_ENTRY_AUTHORIZED", details

    # ══════════════════════════════════════════════════════════════════════
    # 【三、 持倉中的「趨勢鎖倉」防洗盤規則 & 四、合法平倉條件】
    # ══════════════════════════════════════════════════════════════════════
    def should_close_position(
        self,
        *args,
        **kwargs
    ) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """持倉防洗盤與合法出場審查 (支援雙重傳參模式)
        
        用法:
            should_close_position(side, position, df, quote=None)
            或 should_close_position(position, df, quote=None)
            
        核心鐵律：
        1. 趨勢鎖倉防洗盤 (Trend-Riding Mode)：
           - 嚴禁微幅回吐平倉 (取消 2%~3% 即時止盈)。
           - 多單持倉時：即使價格跌回收進通道內，只要實體 K 棒未收盤跌破基準線 (KC 中軌/15 MA)，一律強制 HOLD！
           - 空單持倉時：即使價格反抽收進通道內，只要實體 K 棒未收盤突破站上基準線，一律強制 HOLD！
        2. 合法平倉條件 (Only Close When，嚴格只有這兩種)：
           ① 結構被破 (Trend Invalidation)：多單實體收盤跌破基準線；空單實體收盤突破基準線。
           ② 爆量高位滯漲力竭 (Climax Exit)：價格在通道外嚴重乖離 + 2 倍以上巨量 + 長影線 (多單上影線 >= 50%、空單下影線 >= 50%)。
        3. 所有判定嚴格要求以已收線 K 棒 (Candle Close) 為準，杜絕盤中 Tick 毛刺！
        """
        # 兼容參數解析
        if len(args) == 4:
            side, position, df, quote = args
        elif len(args) == 3:
            if isinstance(args[0], str):
                side, position, df = args
                quote = kwargs.get('quote')
            else:
                position, df, quote = args
                side = position.get('side', 'LONG')
        elif len(args) == 2:
            position, df = args
            quote = kwargs.get('quote')
            side = position.get('side', 'LONG')
        else:
            position = kwargs.get('position', {})
            df = kwargs.get('df')
            quote = kwargs.get('quote')
            side = kwargs.get('side') or position.get('side', 'LONG')

        side = str(side).upper()
        if df is None or len(df) < 3:
            return False, "HOLD: INSUFFICIENT_DATA", {}

        curr = df.iloc[-1]
        c = float(curr.get('close', 0.0))
        o = float(curr.get('open', 0.0))
        h = float(curr.get('high', 0.0))
        l = float(curr.get('low', 0.0))
        p = float(quote) if quote is not None else c
        is_candle_closed = bool(curr.get('is_closed', True))

        atr = float(curr.get('atr', 0.0))
        if atr <= 0:
            atr = max(c * 0.01, 1e-8)

        basis = float(curr.get('kc_middle', curr.get('ma15', c)))
        kc_upper = float(curr.get('kc_upper', basis + 2 * atr))
        kc_lower = float(curr.get('kc_lower', basis - 2 * atr))

        macro_regime, regime_details = self.evaluate_regime(df)

        details = {
            'side': side,
            'close': c,
            'quote': p,
            'basis': basis,
            'kc_upper': kc_upper,
            'kc_lower': kc_lower,
            'macro_regime': macro_regime,
            'is_candle_closed': is_candle_closed,
            'atr': atr,
        }

        # 鐵律：平倉判定嚴格要求以已收線 K 棒實體為準，盤中 Tick 微秒毛刺一律不平倉！
        if not is_candle_closed:
            return False, "HOLD: CANDLE_NOT_CLOSED (等待 1M K 棒收線確認，防盤中插針毛刺)", details

        # ──【A. 多單持倉審查 (LONG)】──
        if side == 'LONG':
            # 條件 1：結構被破 (Trend Invalidation)
            # 實體收盤正式跌破基準線 (KC 中軌 / 15 MA)
            if c < basis:
                return True, "EXIT_LONG_STRUCTURE_BROKEN (收盤實體摜破基準線，趨勢終結離場)", details

            # 條件 2：爆量高位滯漲力竭 (Climax Exit)
            # 條件：價格在 KC 上軌外嚴重超買 + 爆出 2 倍以上巨量 + 長上影線佔比 >= 50%
            is_at_upper = (h >= kc_upper)
            bar_range = max(h - l, 1e-9)
            upper_wick = max(0.0, h - max(o, c))
            wick_ratio = upper_wick / bar_range

            curr_vol = float(curr.get('volume', 0.0))
            avg_vol = float(df['volume'].tail(10).mean()) if 'volume' in df.columns and len(df) >= 10 else 0.0
            is_volume_climax = (avg_vol > 0 and curr_vol >= self.climax_volume_multiplier * avg_vol)

            if is_at_upper and wick_ratio >= self.exhaustion_wick_ratio and is_volume_climax:
                details['wick_ratio'] = round(wick_ratio, 3)
                details['volume_ratio'] = round(curr_vol / avg_vol, 2)
                return True, "EXIT_LONG_BLOW_OFF_TOP / CLIMAX_EXHAUSTION (KC上軌外爆2倍巨量且長上影線>=50%，高位滯漲力竭)", details

            # 趨勢鎖倉防洗盤規則 (Trend-Riding Mode)：
            # 只要收盤價守在基準線之上，即便價格跌回收進通道內、或盤中微幅回吐，一律強制 HOLD，不准平多！
            return False, "HOLD: TREND_RIDING_LOCK_BULL_INTACT (基準線未跌破，趨勢鎖倉堅定續抱)", details

        # ──【B. 空單持倉審查 (SHORT，調用專用空頭趨勢鎖倉平倉審查)】──
        elif side == 'SHORT':
            return self.should_close_short(
                position=position,
                df=df,
                quote=p,
                is_candle_closed=is_candle_closed,
            )

        return False, "HOLD: DEFAULT_POSITION_PROTECTION", details

    def should_close_short(
        self,
        position: Dict[str, Any],
        df: pd.DataFrame,
        quote: Optional[float] = None,
        is_candle_closed: Optional[bool] = None,
    ) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """空頭趨勢中持倉鎖定 (Bear Trend-Riding) 專用平空審查
        
        嚴格執行 2026-10-15 最新授權規範：
        一、 空頭趨勢狀態鎖定 (BEAR_TREND == True):
            1. KC 中軌 (或趨勢基準線) 斜率向下 (Basis_Slope < 0)
            2. KC 通道三軌 (上軌、中軌、下軌) 同步向下發散
            3. 價格多數收在 KC 中軌下方 (最近 5 根有 >= 3 根收在中軌下，且當前 Close < KC_Basis)

        二、 空頭趨勢中的【平倉禁令 (禁止中途平空)】:
            只要 BEAR_TREND == True:
            1. 禁用微幅反彈鎖利：嚴禁 2%~3% 的敏感反抽平空，盤中小綠棒或下影線視為下跌中繼正常呼吸，強制 HOLD！
            2. 允許通道內弱勢反抽：即使從 KC 下軌外反抽回通道內，只要實體收盤未站上 KC 中軌 (Close < KC_Basis)，一律不准平空！
            3. 短期均線未拐頭不平倉：短均線 (3 EMA / 5 EMA) 只要依然壓制在 K 棒上方且方向向下，堅決持倉！

        三、 空單在空頭趨勢下的【唯二平倉條件】:
            1. 結構被破 (Trend Invalidation): K 棒收盤價實體突破並站穩 KC 中軌 (Close > KC_Basis)。
            2. 極端恐慌天量插針 (Panic Climax Bottom):
               價格嚴重跌破 KC 下軌 (超賣負乖離)，且當根爆出 2.5 倍以上巨量，並留下超過實體 2 倍長度的超長下影線 (確認針尖放量換手)。

        四、 收盤價判定鐵律：
            所有條件均以「K 棒收盤價 (is_candle_closed == True)」判斷，未收盤 100% 駁回平倉！
        """
        if df is None or len(df) < 1:
            return False, "HOLD: INSUFFICIENT_DATA", {}

        curr = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else curr
        c = float(curr.get('close', 0.0))
        o = float(curr.get('open', 0.0))
        h = float(curr.get('high', 0.0))
        l = float(curr.get('low', 0.0))
        p = float(quote) if quote is not None else c

        if is_candle_closed is None:
            is_candle_closed = bool(curr.get('is_closed', True))

        atr = float(curr.get('atr', 0.0))
        if atr <= 0:
            atr = max(c * 0.01, 1e-8)

        basis = float(curr.get('kc_middle', curr.get('ma15', c)))
        prev_basis = float(prev.get('kc_middle', prev.get('ma15', basis)))
        basis_slope = basis - prev_basis

        kc_upper = float(curr.get('kc_upper', basis + 2 * atr))
        prev_upper = float(prev.get('kc_upper', prev_basis + 2 * atr))
        kc_lower = float(curr.get('kc_lower', basis - 2 * atr))
        prev_lower = float(prev.get('kc_lower', prev_basis - 2 * atr))

        ma5 = float(curr.get('ma5', c))
        prev_ma5 = float(prev.get('ma5', ma5))
        ema3 = float(curr.get('ema3', curr.get('ma3', ma5)))
        prev_ema3 = float(prev.get('ema3', prev.get('ma3', ema3)))

        details = {
            'side': 'SHORT',
            'close': c,
            'quote': p,
            'basis': basis,
            'basis_slope': basis_slope,
            'kc_upper': kc_upper,
            'kc_lower': kc_lower,
            'is_candle_closed': is_candle_closed,
            'atr': atr,
            'ma5': ma5,
            'ema3': ema3,
        }

        # ──【鐵律四：收盤價判定鐵律】──
        # 徹底避免被盤中跳動洗出場，未收線一律 100% HOLD！
        if not is_candle_closed:
            return False, "HOLD: CANDLE_NOT_CLOSED (等待 1M K 棒收線確認，徹底防範盤中毛刺與插針)", details

        # ──【一、 空頭趨勢狀態鎖定 (BEAR_TREND)】──
        # 1. KC 中軌 (或趨勢基準線) 斜率向下 (Basis_Slope < 0)
        # 2. KC 通道三軌 (上軌、中軌、下軌) 同步向下發散
        # 3. 價格多數收在 KC 中軌下方 (最近 5 根有 >= 3 根收在中軌下，且當前 Close < KC_Basis)
        recent_5 = df.tail(5)
        below_count = sum(float(r.get('close', 0)) <= float(r.get('kc_middle', basis)) for _, r in recent_5.iterrows())
        three_bands_down = (kc_upper <= prev_upper * 1.0005) and (basis <= prev_basis) and (kc_lower <= prev_lower * 1.0005)
        is_bear_trend = (basis_slope < 0 or three_bands_down) and (c < basis) and (below_count >= 3)
        details['is_bear_trend'] = is_bear_trend
        details['below_count'] = below_count

        # ──【二、 非空頭趨勢時 (已反轉或站上中軌)】──
        if not is_bear_trend:
            if c > basis:
                return True, "EXIT_SHORT_STRUCTURE_BROKEN (收盤價已實體突破站上基準線，非空頭趨勢離場)", details

        # ──【三、 空單在空頭趨勢下的【唯二平倉條件】】──
        # 條件 1：結構被破 (Trend Invalidation)
        # K 棒收盤價 (Close) 實體突破並站穩 KC 中軌 (Close > KC_Basis)
        if c > basis:
            return True, "EXIT_SHORT_STRUCTURE_BROKEN (K棒收盤實體突破並站穩 KC 中軌，空頭動能衰竭離場)", details

        # 條件 2：極端恐慌天量插針 (Panic Climax Bottom)
        # ① 價格嚴重跌破 KC 下軌 (超賣負乖離過大)
        # ② 該根 K 棒爆出 2.0~2.5 倍以上巨量 (確認天量換手，以過去 K 棒均量為基準)
        # ③ 留下超過實體 2 倍長度的超長下影線 (確認針尖放量主力吃單)
        body = abs(c - o)
        lower_wick = max(0.0, min(c, o) - l)
        bar_range = max(h - l, 1e-9)

        curr_vol = float(curr.get('volume', 0.0))
        # 計算前 10 根（不包含當前這根爆量棒）的平均成交量
        prev_bars = df.iloc[:-1] if len(df) > 1 else df
        avg_vol = float(prev_bars['volume'].tail(10).mean()) if 'volume' in prev_bars.columns and len(prev_bars) > 0 else 0.0
        is_climax_volume = (avg_vol > 0 and curr_vol >= min(2.5, self.climax_volume_multiplier) * avg_vol)
        is_climax_wick = (lower_wick >= 2.0 * max(body, 0.1 * atr)) or (lower_wick / bar_range >= self.exhaustion_wick_ratio)
        is_severe_oversold = (l <= kc_lower) or ((basis - l) / atr >= 1.2)

        if is_severe_oversold and is_climax_volume and is_climax_wick:
            details['wick_ratio'] = round(lower_wick / bar_range, 3)
            details['wick_to_body_ratio'] = round(lower_wick / max(body, 1e-9), 2)
            details['volume_ratio'] = round(curr_vol / avg_vol, 2) if avg_vol > 0 else 0.0
            return True, "EXIT_SHORT_PANIC_CLIMAX_BOTTOM (KC下軌外超賣 + 爆量換手 + 超長下影線，恐慌針尖換手獲利離場)", details

        # ──【平倉禁令：強制 HOLD，嚴禁中途被反彈洗出場】──
        # 1. 禁用微幅反彈鎖利：嚴禁 2%~3% 敏感反抽平空，盤中小綠棒/下影線一律視為「下跌中繼的正常呼吸」
        # 2. 允許通道內弱勢反抽：價格即使從 KC 下軌外反抽回通道內部，只要收盤未站上 KC 中軌 (Close < KC_Basis)，一律不准平空
        # 3. 短期均線未拐頭不平倉：短均線 (3 EMA / 5 EMA) 只要依然壓制在 K 棒上方且方向向下，堅決持倉
        short_ma_holding = (c <= ema3 or c <= ma5 or ema3 <= prev_ema3 or ma5 <= prev_ma5)
        details['short_ma_holding'] = short_ma_holding

        return False, "HOLD: BEAR_TREND_LOCK_INTACT / TREND_RIDING_LOCK_BEAR_INTACT (空頭主跌浪趨勢鎖定中，未破中軌且無天量插針，嚴禁中途被反彈洗下車)", details


# ══════════════════════════════════════════════════════════════════════
# 【五、 出場執行防滑價機制 (No Market Orders + 3s Watchdog)】
# ══════════════════════════════════════════════════════════════════════
class AntiSlippageOrderExecutor:
    """防滑價限價單平倉執行器 (全面廢棄 Stop Market)"""

    @staticmethod
    def calculate_limit_close_price(
        side: str,
        live_price: float,
        offset_pct: float = 0.002,
    ) -> float:
        """計算帶讓步緩衝之平倉限價 (多單賣出折讓 0.2%~0.3%，空單買入上浮 0.2%~0.3%)"""
        side = side.upper()
        if side in ('LONG', 'BUY'):
            # 多單平倉 (賣出)：以略低於當前價 0.2% 掛出積極限價單，鎖死滑價底線
            return live_price * (1.0 - max(0.001, offset_pct))
        else:
            # 空單平倉 (買入)：以略高於當前價 0.2% 掛出積極限價單，鎖死滑價上限
            return live_price * (1.0 + max(0.001, offset_pct))

    @classmethod
    async def execute_anti_slippage_close(
        cls,
        account: Any,
        symbol: str,
        side: str,
        qty: float,
        live_price: float,
        offset_pct: float = 0.002,
        watchdog_timeout_sec: float = 3.0,
        close_reason: str = "TREND_STRUCTURE_EXIT",
    ) -> Tuple[bool, Dict[str, Any]]:
        """以帶讓步限價單平倉，並由 3 秒 Watchdog 監控成交；超時未撮合則自動撤單抓最新買一/賣一家重掛"""
        if account is None or qty <= 0 or live_price <= 0:
            return False, {"error": "INVALID_PARAMS"}

        close_side = "sell" if side.upper() in ('LONG', 'BUY') else "buy"
        order_price = cls.calculate_limit_close_price(side, live_price, offset_pct)

        # 精度格式化
        if hasattr(account, "exchange") and hasattr(account.exchange, "price_to_precision"):
            order_price = float(account.exchange.price_to_precision(symbol, order_price))
        elif hasattr(account, "price_to_precision"):
            order_price = float(account.price_to_precision(symbol, order_price))

        account.log(
            f"🛡️ [防滑價限價平倉] {symbol} {close_side.upper()} 數量={qty} 限價={order_price:.6g} "
            f"(帶 {offset_pct*100:.1f}% 讓步緩衝，死守底線杜絕市價滑穿)",
            "INFO"
        )

        # 送出帶 reduceOnly 的限價平倉單
        order = await account._send_order(
            symbol,
            "limit",
            close_side,
            qty,
            order_price,
            {"reduceOnly": True, "newOrderRespType": "RESULT"},
        )

        order_id = (order or {}).get("id") or (order or {}).get("orderId")
        if not order_id:
            # 若為模擬盤直接全成
            if getattr(account, "is_paper", False) or "Paper" in type(account).__name__:
                return True, {"order_id": "PAPER_FILL", "price": order_price, "status": "FILLED"}
            account.log(f"🛑 [平倉下單失敗] 未返回有效 order_id", "ERROR")
            return False, {"error": "NO_ORDER_ID"}

        # ──【3 秒監控 Watchdog】──
        start_time = time.time()
        poll_interval = 0.5

        while time.time() - start_time < watchdog_timeout_sec:
            await asyncio.sleep(poll_interval)
            try:
                check_order = await account.exchange.fetch_order(str(order_id), symbol)
                status = str(check_order.get("status", "")).upper()
                filled = float(check_order.get("filled", 0.0))
                remaining = float(check_order.get("remaining", qty))

                if status == "FILLED" or remaining <= 0:
                    account.log(f"✅ [Watchdog 撮合成交] {symbol} 限價單 {order_id} 已在 {time.time()-start_time:.1f} 秒內完全撮合", "INFO")
                    return True, {"order_id": str(order_id), "status": "FILLED", "filled": filled}

                if status in ("CANCELED", "EXPIRED", "REJECTED"):
                    account.log(f"⚠️ [Watchdog 委託異常] 訂單狀態={status}，將以最新最佳報價重送", "WARNING")
                    break
            except Exception as e:
                logger.warning(f"Watchdog fetch_order failed: {e}")

        # ──【3 秒超時處置：撤單並改抓最新最佳對手價重掛】──
        account.log(f"⏱️ [Watchdog 3秒超時] 限價平倉單 {order_id} 未完全成交，立即撤單重新掛單！", "WARNING")
        try:
            await account._cancel_order(symbol, str(order_id))
        except Exception as e:
            logger.warning(f"Cancel order on watchdog timeout failed: {e}")

        # 抓取最新對手價重掛
        latest_price = live_price
        if hasattr(account, "tickers") and symbol in account.tickers:
            latest_price = float(account.tickers[symbol])
        elif hasattr(account, "exchange") and hasattr(account.exchange, "fetch_ticker"):
            try:
                t = await account.exchange.fetch_ticker(symbol)
                latest_price = float(t.get("last") or t.get("close") or latest_price)
            except Exception:
                pass

        # 以更積極之讓步價格重掛
        aggressive_price = cls.calculate_limit_close_price(side, latest_price, offset_pct=0.003)
        if hasattr(account, "exchange") and hasattr(account.exchange, "price_to_precision"):
            aggressive_price = float(account.exchange.price_to_precision(symbol, aggressive_price))

        account.log(f"🔄 [Watchdog 重掛限價] {symbol} 最新報價={latest_price} 重掛限價={aggressive_price:.6g}", "INFO")
        re_order = await account._send_order(
            symbol,
            "limit",
            close_side,
            qty,
            aggressive_price,
            {"reduceOnly": True, "newOrderRespType": "RESULT"},
        )
        return True, {"order_id": (re_order or {}).get("id"), "status": "RE_SUBMITTED", "price": aggressive_price}
