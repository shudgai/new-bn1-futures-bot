"""MemeSharedProtectionGate
實現 5M（操作級別）+ 15M（大趨勢級別）跨週期共振開倉防護閘門。
涵蓋三大開倉模式與開倉前置強制審查。
"""
import logging
from typing import Dict, Any, Optional, Tuple
import pandas as pd
from core.indicators.dual_timeframe_provider import DualTimeframeProvider
from core.gates.strict_entry_and_immunity_gate import resolve_kc_trend

logger = logging.getLogger(__name__)


class MemeSharedProtectionGate:
    @classmethod
    def _fail_entry(
        cls,
        symbol: Optional[str],
        mode: str,
        reason: str,
        m15: Dict[str, Any],
        dev: float,
    ) -> Tuple[bool, str, None]:
        sym = symbol or "UNKNOWN"
        log_msg = f"[{sym}] [開倉攔截 VETO] 模式: {mode} | 原因: {reason} | 15M_trend: {m15.get('15m_big_trend')} | Dev: {dev:.4f}"
        logger.info(log_msg)
        print(log_msg, flush=True)
        return False, reason, None

    @classmethod
    def validate_short_ma_condition(
        cls, df: pd.DataFrame, min_gap_atr: float = 0.20, min_down_slope_atr: float = -0.05
    ) -> Tuple[bool, str]:
        """驗證做空時均線是否有實質下行趨勢且已拉開間距，嚴防 MA5 走平或兩線黏合誘空 (Chop Zone)"""
        if df is None or len(df) < 2:
            return False, "REJECT_ENTRY: INSUFFICIENT_DATA"

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        current_ma5 = float(curr.get('ma5', 0.0))
        prev_ma5 = float(prev.get('ma5', current_ma5))
        current_ma15 = float(curr.get('ma15', 0.0))
        current_atr = float(curr.get('atr', 0.0))
        if current_atr <= 0:
            c = float(curr.get('close', 1.0))
            current_atr = c * 0.01

        # 1. 增設「MA5 與 MA15 均線黏合糾纏過濾 (MA Convergence Gate)」
        # 僅在均線未拉開實質間距 (gap_ratio_atr < min_gap_atr) 且 相對間距極度狹窄 (< 0.15%) 時，判定為無趨勢整理盤
        ma_gap = current_ma15 - current_ma5
        gap_ratio_atr = ma_gap / current_atr
        if current_ma15 > 0:
            ma_spread = abs(current_ma5 - current_ma15) / current_ma15
            if gap_ratio_atr < min_gap_atr and ma_spread < 0.0015:
                return False, f"REJECT: MA5_MA15_ENTANGLED (Spread: {ma_spread:.5f} < 0.0015)"

        # 2. 增設「MA5 斜率俯衝硬檢核 (Slope Deadlock)」
        # 做空硬條件：MA5 必須呈現絕對向下斜率。若 MA5 走平或轉升 (ma5_slope >= 0)，100% 一票否決開空
        if prev_ma5 > 0:
            ma5_slope = (current_ma5 - prev_ma5) / prev_ma5
            if ma5_slope >= 0.0:
                return False, f"REJECT: MA5_SLOPE_FLAT_FOR_SHORT (Slope: {ma5_slope:.6f} >= -0.0001, must be < -0.0003)"

        # 3. 均線間距（空頭排列下 ma15 應大於 ma5，ATR 標準化）
        if gap_ratio_atr < min_gap_atr:
            return False, f"REJECT_ENTRY: MA_CONVERGENCE (Gap: {gap_ratio_atr:.2f} ATR < {min_gap_atr:.2f} ATR)"

        # 4. MA5 斜率（以 ATR 標準化）
        ma5_slope_atr = (current_ma5 - prev_ma5) / current_atr
        if ma5_slope_atr >= min_down_slope_atr:
            return False, f"REJECT_ENTRY: MA5_FLAT_OR_RISING (Slope: {ma5_slope_atr:.3f} ATR >= {min_down_slope_atr:.3f} ATR)"

        return True, "PASS"

    @classmethod
    def validate_long_ma_condition(
        cls, df: pd.DataFrame, min_gap_atr: float = 0.20, min_up_slope_atr: float = 0.05
    ) -> Tuple[bool, str]:
        """驗證做多時均線是否有實質上行趨勢且已拉開間距，嚴防 MA5 走平或兩線黏合誘多 (Chop Zone)"""
        if df is None or len(df) < 2:
            return False, "REJECT_ENTRY: INSUFFICIENT_DATA"

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        current_ma5 = float(curr.get('ma5', 0.0))
        prev_ma5 = float(prev.get('ma5', current_ma5))
        current_ma15 = float(curr.get('ma15', 0.0))
        current_atr = float(curr.get('atr', 0.0))
        if current_atr <= 0:
            c = float(curr.get('close', 1.0))
            current_atr = c * 0.01

        # 1. 增設「MA5 與 MA15 均線黏合糾纏過濾 (MA Convergence Gate)」
        # 僅在均線未拉開實質間距 (gap_ratio_atr < min_gap_atr) 且 相對間距極度狹窄 (< 0.15%) 時，判定為無趨勢整理盤
        ma_gap = current_ma5 - current_ma15
        gap_ratio_atr = ma_gap / current_atr
        if current_ma15 > 0:
            ma_spread = abs(current_ma5 - current_ma15) / current_ma15
            if gap_ratio_atr < min_gap_atr and ma_spread < 0.0015:
                return False, f"REJECT: MA5_MA15_ENTANGLED (Spread: {ma_spread:.5f} < 0.0015)"

        # 2. 增設「MA5 斜率俯衝硬檢核 (Slope Deadlock)」
        # 做多硬條件：MA5 必須呈現絕對向上斜率。若 MA5 走平或轉跌 (ma5_slope <= 0)，100% 一票否決開多
        if prev_ma5 > 0:
            ma5_slope = (current_ma5 - prev_ma5) / prev_ma5
            if ma5_slope <= 0.0:
                return False, f"REJECT: MA5_SLOPE_FLAT_FOR_LONG (Slope: {ma5_slope:.6f} <= +0.0001, must be > +0.0003)"

        # 3. 均線間距（多頭排列下 ma5 應大於 ma15，ATR 標準化）
        if gap_ratio_atr < min_gap_atr:
            return False, f"REJECT_ENTRY: MA_CONVERGENCE (Gap: {gap_ratio_atr:.2f} ATR < {min_gap_atr:.2f} ATR)"

        # 4. MA5 斜率（以 ATR 標準化）
        ma5_slope_atr = (current_ma5 - prev_ma5) / current_atr
        if ma5_slope_atr <= min_up_slope_atr:
            return False, f"REJECT_ENTRY: MA5_FLAT_OR_FALLING (Slope: {ma5_slope_atr:.3f} ATR <= {min_up_slope_atr:.3f} ATR)"

        return True, "PASS"

    @classmethod
    def validate_ma_condition(
        cls, df: pd.DataFrame, side: str, min_gap_atr: float = 0.20, min_down_slope_atr: float = -0.05, min_up_slope_atr: float = 0.05
    ) -> Tuple[bool, str]:
        """統一驗證入口"""
        side = side.upper()
        if side == 'SHORT':
            return cls.validate_short_ma_condition(df, min_gap_atr=min_gap_atr, min_down_slope_atr=min_down_slope_atr)
        elif side == 'LONG':
            return cls.validate_long_ma_condition(df, min_gap_atr=min_gap_atr, min_up_slope_atr=min_up_slope_atr)
        return False, "REJECT_ENTRY: INVALID_SIDE"

    @classmethod
    def validate_pullback_reentry(
        cls,
        df: pd.DataFrame,
        side: str,
        quote: float,
        last_closed_action: str = "",
        last_closed_price: float = 0.0,
        last_closed_ts: float = 0.0,
        curr_ts: float = 0.0,
        atr: float = 0.0,
        ma5: float = 0.0,
    ) -> Tuple[bool, str]:
        """驗證平倉後的二次開倉是否具備合格的回抽/回踩 (Pullback Re-entry Filter)。
        
        鐵律防護：
        - 空單剛平倉 (CLOSE_SHORT)：嚴禁在地板無反彈原地追空！
          必須在平倉後至少出現過小反彈（摸到 MA5 或反彈 >= 0.20 ATR 或最高價 >= 平倉價 * 1.002）。
          若平倉後從未反彈且最新價趴在平倉價下方，一票否決。
        - 多單剛平倉 (CLOSE_LONG)：嚴禁在天花板無回調原地追多！
          必須在平倉後至少出現過小回踩（回踩 MA5 或回調 <= -0.20 ATR 或最低價 <= 平倉價 * 0.998）。
          若平倉後從未回踩且最新價頂在平倉價上方，一票否決。
        """
        if not last_closed_action or last_closed_price <= 0 or last_closed_ts <= 0:
            return True, "PASS: NO_RECENT_CLOSE_INFO"

        if 0 < curr_ts < 1e11:
            curr_ts *= 1000.0
        if 0 < last_closed_ts < 1e11:
            last_closed_ts *= 1000.0

        elapsed_ms = (curr_ts - last_closed_ts) if curr_ts > 0 else 0.0
        # 僅在平倉後 30 分鐘 (1,800,000 ms) 內進行嚴格回抽審查；超過 30 分鐘市場結構已重置
        if elapsed_ms <= 0 or elapsed_ms > 1_800_000.0:
            return True, "PASS: EXCEEDS_REENTRY_WINDOW"

        side = side.upper()
        # 1. 空單平倉後的二次開空 (Short Re-entry)
        if side == 'SHORT' and last_closed_action == 'CLOSE_SHORT':
            highs = [quote]
            if df is not None and len(df) > 0:
                ts_col = 'timestamp' if 'timestamp' in df.columns else None
                if ts_col:
                    first_ts = float(df.iloc[0][ts_col])
                    scale = 1000.0 if first_ts < 1e11 else 1.0
                    sub = df[df[ts_col] * scale >= last_closed_ts]
                else:
                    sub = df.tail(6)
                if len(sub) > 0 and 'high' in sub.columns:
                    highs.extend(sub['high'].dropna().astype(float).tolist())
            max_high_since = max(highs)

            # 判定是否有小反彈：
            # A. 反彈幅度達到 0.20 ATR
            bounced_by_atr = (max_high_since >= last_closed_price + 0.20 * atr) if atr > 0 else False
            # B. 摸到或穿過 MA5 均線壓力位
            bounced_to_ma5 = (max_high_since >= ma5) if ma5 > 0 else False
            # C. 價格比平倉價高出 0.3%
            bounced_by_pct = (max_high_since >= last_closed_price * 1.003)

            has_bounced = bounced_by_atr or bounced_to_ma5 or bounced_by_pct
            # 若平倉後完全沒有出現小反彈，且最新價格趴在平倉價下方地板追空，一票否決！
            if not has_bounced and quote <= last_closed_price:
                return False, (
                    f"REJECT_REENTRY: NO_PULLBACK_AFTER_EXIT ("
                    f"LastCloseShort: {last_closed_price:.6f}, MaxHighSince: {max_high_since:.6f}, "
                    f"Quote: {quote:.6f}, MA5: {ma5:.6f}, Elapsed: {elapsed_ms/1000:.1f}s - Floor Chase Blocked)"
                )

        # 2. 多單平倉後的二次開多 (Long Re-entry)
        elif side == 'LONG' and last_closed_action == 'CLOSE_LONG':
            lows = [quote]
            if df is not None and len(df) > 0:
                ts_col = 'timestamp' if 'timestamp' in df.columns else None
                if ts_col:
                    first_ts = float(df.iloc[0][ts_col])
                    scale = 1000.0 if first_ts < 1e11 else 1.0
                    sub = df[df[ts_col] * scale >= last_closed_ts]
                else:
                    sub = df.tail(6)
                if len(sub) > 0 and 'low' in sub.columns:
                    lows.extend(sub['low'].dropna().astype(float).tolist())
            min_low_since = min(lows)

            # 判定是否有小回踩：
            # A. 回調幅度達到 0.20 ATR
            pulled_by_atr = (min_low_since <= last_closed_price - 0.20 * atr) if atr > 0 else False
            # B. 回踩碰觸或跌穿 MA5 均線支撐位
            pulled_to_ma5 = (min_low_since <= ma5) if ma5 > 0 else False
            # C. 價格比平倉價低出 0.3%
            pulled_by_pct = (min_low_since <= last_closed_price * 0.997)

            has_pulled_back = pulled_by_atr or pulled_to_ma5 or pulled_by_pct
            # 若平倉後完全沒有出現小回踩，且最新價格在平倉價上方天花板追多，一票否決！
            if not has_pulled_back and quote >= last_closed_price:
                return False, (
                    f"REJECT_REENTRY: NO_PULLBACK_AFTER_EXIT ("
                    f"LastCloseLong: {last_closed_price:.6f}, MinLowSince: {min_low_since:.6f}, "
                    f"Quote: {quote:.6f}, MA5: {ma5:.6f}, Elapsed: {elapsed_ms/1000:.1f}s - Ceiling Chase Blocked)"
                )

        return True, "PASS"

    @classmethod
    def validate_profit_room(
        cls,
        df: pd.DataFrame,
        side: str,
        quote: float,
        min_room_ratio: float = 0.20,
        min_room_atr: float = 0.35,
    ) -> Tuple[bool, str]:
        """驗證開倉是否具備足夠的獲利空間 (Profit Room Filter)。
        
        使用者鐵律：「開倉平倉後冷靜一下有利潤在開倉」！
        - 做多：當前價格到 KC 上軌必須至少有 20% 通道寬度或 0.35 ATR 剩餘空間。
          若價格在通道內貼近上軌（剩餘空間不足）無利可圖嚴禁追多；若已突破上軌，距上軌超過 3.0 ATR 嚴禁追價！
        - 做空：當前價格到 KC 下軌必須至少有 20% 通道寬度或 0.35 ATR 剩餘空間。
          若價格在通道內貼近下軌（剩餘空間不足）無利可圖嚴禁追空；若已跌破下軌，距下軌超過 3.0 ATR 嚴禁追價！
        """
        if df is None or len(df) < 2:
            return True, "PASS: INSUFFICIENT_DATA"

        curr = df.iloc[-1]
        kc_upper = float(curr.get('kc_upper', 0.0))
        kc_lower = float(curr.get('kc_lower', 0.0))
        atr = float(curr.get('atr', 0.0))
        if atr <= 0:
            c = float(curr.get('close', quote))
            atr = max(c * 0.01, 1e-8)

        kc_range = kc_upper - kc_lower
        if kc_range <= 0:
            return True, "PASS: INVALID_KC_RANGE"

        side = side.upper()
        if side == 'LONG':
            room_price = kc_upper - quote
            room_ratio = room_price / kc_range
            # 如果在軌道內，但距上軌空間不足 20% 且不足 0.35 ATR，判定為貼軌追多無利潤
            if quote < kc_upper:
                if room_ratio < min_room_ratio and room_price < min_room_atr * atr:
                    return False, f"REJECT_ENTRY: INSUFFICIENT_PROFIT_ROOM_FOR_LONG (Room: {room_ratio:.1%} < {min_room_ratio:.0%}, {room_price:.4f} < {min_room_atr} ATR)"
            else:
                # 若已突破上軌，但超出上軌過度延伸 (> 3.0 ATR)，天花板利潤空間耗盡且超過 3 ATR 追價上限
                if (quote - kc_upper) > 3.0 * atr:
                    return False, f"REJECT_ENTRY: LONG_OVEREXTENDED_PAST_UPPER_RAIL ({(quote - kc_upper)/atr:.2f} ATR > 3.0 ATR - Exceeds 3 ATR Chase Limit)"
            return True, "PASS"

        elif side == 'SHORT':
            room_price = quote - kc_lower
            room_ratio = room_price / kc_range
            # 如果在軌道內，但距下軌空間不足 20% 且不足 0.35 ATR，判定為貼軌追空無利潤
            if quote > kc_lower:
                if room_ratio < min_room_ratio and room_price < min_room_atr * atr:
                    return False, f"REJECT_ENTRY: INSUFFICIENT_PROFIT_ROOM_FOR_SHORT (Room: {room_ratio:.1%} < {min_room_ratio:.0%}, {room_price:.4f} < {min_room_atr} ATR)"
            else:
                # 若已跌破下軌，但跌出下軌過度延伸 (> 3.0 ATR)，地板利潤空間耗盡且超過 3 ATR 追價上限
                if (kc_lower - quote) > 3.0 * atr:
                    return False, f"REJECT_ENTRY: SHORT_OVEREXTENDED_PAST_LOWER_RAIL ({(kc_lower - quote)/atr:.2f} ATR > 3.0 ATR - Exceeds 3 ATR Chase Limit)"
            return True, "PASS"

        return False, "REJECT_ENTRY: INVALID_SIDE"

    @classmethod
    def validate_trend_exhaustion(
        cls,
        df: pd.DataFrame,
        side: str,
        quote: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """驗證是否已處於趨勢末端 (Trend Exhaustion Gate)。
        
        使用者鐵律：「若已在漲勢末端就不要再開倉」！
        多空對稱：
        - 漲勢末端 (BULLISH_EXHAUSTION)：100% 嚴禁開多，一票否決！
        - 跌勢末端 (BEARISH_EXHAUSTION)：100% 嚴禁開空，一票否決！
        """
        if df is None or len(df) < 3:
            return True, "PASS: INSUFFICIENT_DATA"

        curr = df.iloc[-1]
        prev = df.iloc[-2]
        c = float(curr.get('close', 0.0))
        o = float(curr.get('open', 0.0))
        h = float(curr.get('high', 0.0))
        l = float(curr.get('low', 0.0))
        price = float(quote) if quote is not None else c

        ma5 = float(curr.get('ma5', 0.0))
        prev_ma5 = float(prev.get('ma5', ma5))
        ma15 = float(curr.get('ma15', 0.0))
        atr = float(curr.get('atr', 0.0))
        if atr <= 0:
            atr = max(c * 0.01, 1e-8)

        kc_upper = float(curr.get('kc_upper', 0.0))
        prev_kc_upper = float(prev.get('kc_upper', kc_upper))
        kc_lower = float(curr.get('kc_lower', 0.0))
        prev_kc_lower = float(prev.get('kc_lower', kc_lower))
        kc_middle = float(curr.get('kc_middle', curr.get('kc_basis', 0.0)))

        candle_range = max(h - l, 1e-9)
        body = abs(c - o)
        upper_wick = h - max(c, o)
        lower_wick = min(c, o) - l

        side = side.upper()

        # =====================================================================
        # 1. 漲勢末端檢核 (Bullish Trend Exhaustion Gate - 禁止開多)
        # =====================================================================
        if side == 'LONG':
            # 特徵 A：極端超買與均線嚴重過度延伸 (Extreme Overextension)
            bias_atr = (price - ma15) / atr if ma15 > 0 else 0.0
            dev_ma5 = (price - ma5) / ma5 if ma5 > 0 else 0.0
            is_initial_breakout = (o <= kc_upper + 0.1 * atr) and (c > o) and (upper_wick <= 0.4 * body)

            if (price - kc_upper) > 2.8 * atr:
                return False, f"VETO_LONG_BULLISH_EXHAUSTION: 衝出上軌過遠 ({(price - kc_upper)/atr:.2f} ATR > 2.8 ATR)，已在漲勢極限末端，嚴禁追多"

            if not is_initial_breakout:
                if bias_atr > 2.2:
                    return False, f"VETO_LONG_BULLISH_EXHAUSTION: 均線乖離過大已達極限 ({bias_atr:.2f}x ATR > 2.2 ATR)，已在漲勢極限末端，嚴禁追多"
                if dev_ma5 > 0.025:
                    return False, f"VETO_LONG_BULLISH_EXHAUSTION: 偏離 MA5 過大 ({dev_ma5:.2%} > 2.5%)，已在漲勢極限末端，嚴禁追多"
            else:
                if bias_atr > 2.8:
                    return False, f"VETO_LONG_BULLISH_EXHAUSTION: 均線乖離極度過大 ({bias_atr:.2f}x ATR > 2.8 ATR)，已在漲勢極限末端，嚴禁追多"

            # 特徵 B：頂部高檔滯漲長上影線 / 避雷針遇阻 (Stalling Upper Wick)
            if price >= min(kc_upper, ma5) and upper_wick > 0.7 * body and upper_wick > 0.35 * candle_range:
                return False, f"VETO_LONG_BULLISH_EXHAUSTION: 頂部留長上影線受阻滯漲 (Wick: {upper_wick:.4f} > 0.7*Body)，已在漲勢末端，嚴禁追多"

            # 特徵 C：連續推進後動能顯著枯竭 (Diminishing Thrust)
            lookback = min(len(df), 6)
            recent = df.tail(lookback)
            recent_closes = recent['close'].astype(float).tolist()
            recent_opens = recent['open'].astype(float).tolist()
            recent_bodies = [abs(rc - ro) for rc, ro in zip(recent_closes, recent_opens)]
            bull_bars = sum(1 for rc, ro in zip(recent_closes, recent_opens) if rc > ro)
            total_gain = recent_closes[-1] - recent_opens[0]
            if bull_bars >= 4 and total_gain >= 2.0 * atr:
                avg_prior_body = sum(recent_bodies[:-1]) / max(len(recent_bodies) - 1, 1)
                if recent_bodies[-1] < 0.4 * avg_prior_body or recent_bodies[-1] < 0.2 * atr:
                    return False, f"VETO_LONG_BULLISH_EXHAUSTION: 連續大幅推升後多頭動能耗盡衰竭 (LastBody: {recent_bodies[-1]:.4f} < 0.4*AvgPrior)，已在漲勢末端，嚴禁追多"

            # 特徵 D：高檔 MA5 斜率走平衰竭 (MA5 Deceleration at Highs)
            # 特徵 D：高檔 MA5 斜率向下拐頭衰竭 (MA5 Reversal at Highs)
            if price > kc_middle and prev_ma5 > 0:
                ma5_slope = (ma5 - prev_ma5) / prev_ma5
                if ma5_slope <= 0.0:
                    return False, f"VETO_LONG_BULLISH_EXHAUSTION: 高檔 MA5 斜率向下拐頭 (Slope: {ma5_slope:.6f} <= 0)，已在漲勢末端，嚴禁追多"

            return True, "PASS"

        # =====================================================================
        # 2. 跌勢末端檢核 (Bearish Trend Exhaustion Gate - 禁止開空)
        # =====================================================================
        elif side == 'SHORT':
            # 特徵 A：極端超賣與均線嚴重過度延伸 (Extreme Overextension)
            bias_atr = (ma15 - price) / atr if ma15 > 0 else 0.0
            dev_ma5 = (ma5 - price) / ma5 if ma5 > 0 else 0.0
            is_initial_breakout = (o >= kc_lower - 0.1 * atr) and (c < o) and (lower_wick <= 0.4 * body)

            if (kc_lower - price) > 2.8 * atr:
                return False, f"VETO_SHORT_BEARISH_EXHAUSTION: 跌破下軌過遠 ({(kc_lower - price)/atr:.2f} ATR > 2.8 ATR)，已在跌勢極限末端，嚴禁追空"

            if not is_initial_breakout:
                if bias_atr > 2.2:
                    return False, f"VETO_SHORT_BEARISH_EXHAUSTION: 均線乖離過大已達極限 ({bias_atr:.2f}x ATR > 2.2 ATR)，已在跌勢極限末端，嚴禁追空"
                if dev_ma5 > 0.025:
                    return False, f"VETO_SHORT_BEARISH_EXHAUSTION: 偏離 MA5 過大 ({dev_ma5:.2%} > 2.5%)，已在跌勢極限末端，嚴禁追空"
            else:
                if bias_atr > 2.8:
                    return False, f"VETO_SHORT_BEARISH_EXHAUSTION: 均線乖離極度過大 ({bias_atr:.2f}x ATR > 2.8 ATR)，已在跌勢極限末端，嚴禁追空"

            # 特徵 B：底部低檔長下影線托底買盤抵抗 (Stalling Lower Wick)
            if price <= max(kc_lower, ma5) and lower_wick > 0.7 * body and lower_wick > 0.35 * candle_range:
                return False, f"VETO_SHORT_BEARISH_EXHAUSTION: 底部留長下影線托底抵抗 (Wick: {lower_wick:.4f} > 0.7*Body)，已在跌勢末端，嚴禁追空"

            # 特徵 C：連續下跌趕底後動能顯著枯竭 (Diminishing Downward Thrust)
            lookback = min(len(df), 6)
            recent = df.tail(lookback)
            recent_closes = recent['close'].astype(float).tolist()
            recent_opens = recent['open'].astype(float).tolist()
            recent_bodies = [abs(rc - ro) for rc, ro in zip(recent_closes, recent_opens)]
            bear_bars = sum(1 for rc, ro in zip(recent_closes, recent_opens) if rc < ro)
            total_drop = recent_opens[0] - recent_closes[-1]
            if bear_bars >= 4 and total_drop >= 2.0 * atr:
                avg_prior_body = sum(recent_bodies[:-1]) / max(len(recent_bodies) - 1, 1)
                if recent_bodies[-1] < 0.4 * avg_prior_body or recent_bodies[-1] < 0.2 * atr:
                    return False, f"VETO_SHORT_BEARISH_EXHAUSTION: 連續大幅下殺後空頭動能耗盡衰竭 (LastBody: {recent_bodies[-1]:.4f} < 0.4*AvgPrior)，已在跌勢末端，嚴禁追空"

            # 特徵 D：低檔 MA5 斜率向上拐頭 (MA5 Reversal at Lows)
            if price < kc_middle and prev_ma5 > 0:
                ma5_slope = (ma5 - prev_ma5) / prev_ma5
                if ma5_slope >= 0.0:
                    return False, f"VETO_SHORT_BEARISH_EXHAUSTION: 低檔 MA5 斜率向上拐頭 (Slope: {ma5_slope:.6f} >= 0)，已在跌勢末端，嚴禁追空"

            return True, "PASS"

        return False, "REJECT_ENTRY: INVALID_SIDE"

    @classmethod
    def evaluate(
        cls,
        frame: pd.DataFrame,
        side: str,
        quote: Optional[float] = None,
        symbol: Optional[str] = None,
        bypass_spatial_check: bool = False,
        mode: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """相容接口：妖幣共用防禦審查 (Anti-Wick & Max Slippage / Price Deviation Cap)。
        當 bypass_spatial_check == True 時，100% 豁免乖離率與影線過濾。
        """
        if frame is None or len(frame) < 1:
            return False, "REJECT_ENTRY: INSUFFICIENT_DATA"
        side = side.upper()
        if side not in ('LONG', 'SHORT'):
            return False, "REJECT_ENTRY: INVALID_SIDE"

        curr = frame.iloc[-1]
        c_open = float(curr.get('open', 0.0))
        c_close = float(curr.get('close', 0.0))
        c_high = float(curr.get('high', 0.0))
        c_low = float(curr.get('low', 0.0))
        price = float(quote) if quote is not None else c_close

        candle_range = max(c_high - c_low, 1e-9)
        body = abs(c_close - c_open)
        body_ratio = body / candle_range

        curr_ma5 = float(curr.get('ma5', price))
        is_closed = bool(curr.get('is_closed', True))
        if not is_closed and quote is not None and c_close > 0:
            curr_ma5 += (price - c_close) / 5.0

        dev = abs(price - curr_ma5) / curr_ma5 if curr_ma5 > 0 else 0.0

        # 當 bypass_spatial_check == True 時，100% 略過乖離率與影線檢核
        if not bypass_spatial_check:
            # 1. 影線與實體過濾（依據鐵律：影線不得超過實體的 70%）
            if body_ratio < 0.25:
                reason = "REJECT_ENTRY: DOMINATED_BY_WICKS"
                logger.info(f"[{symbol or 'UNKNOWN'}] [開倉攔截 VETO] 模式: {mode or 'MODE2'} | 原因: {reason} | 15M_trend: UNKNOWN | Dev: {dev:.4f}")
                return False, reason

            if side == 'LONG':
                upper_wick = c_high - max(c_open, c_close)
                if upper_wick > 0.7 * body:
                    reason = "REJECT_ENTRY: EXCESSIVE_UPPER_WICK"
                    logger.info(f"[{symbol or 'UNKNOWN'}] [開倉攔截 VETO] 模式: {mode or 'MODE2'} | 原因: {reason} | 15M_trend: UNKNOWN | Dev: {dev:.4f}")
                    return False, reason
            elif side == 'SHORT':
                lower_wick = min(c_open, c_close) - c_low
                if lower_wick > 0.7 * body:
                    reason = "REJECT_ENTRY: EXCESSIVE_LOWER_WICK"
                    logger.info(f"[{symbol or 'UNKNOWN'}] [開倉攔截 VETO] 模式: {mode or 'MODE2'} | 原因: {reason} | 15M_trend: UNKNOWN | Dev: {dev:.4f}")
                    return False, reason

            # 2. 1.2% MA5 乖離率檢核
            if curr_ma5 > 0:
                if side == 'LONG' and dev > 0.012:
                    reason = "REJECT_ENTRY: LONG_MA5_DEVIATION_CAP"
                    logger.info(f"[{symbol or 'UNKNOWN'}] [開倉攔截 VETO] 模式: {mode or 'MODE2'} | 原因: {reason} | 15M_trend: UNKNOWN | Dev: {dev:.4f}")
                    return False, reason
                elif side == 'SHORT' and dev > 0.012:
                    reason = "REJECT_ENTRY: SHORT_MA5_DEVIATION_CAP"
                    logger.info(f"[{symbol or 'UNKNOWN'}] [開倉攔截 VETO] 模式: {mode or 'MODE2'} | 原因: {reason} | 15M_trend: UNKNOWN | Dev: {dev:.4f}")
                    return False, reason

        return True, "PASSED"

    @classmethod
    def evaluate_entry(
        cls,
        df_5m: pd.DataFrame,
        df_15m: Optional[pd.DataFrame] = None,
        quote: Optional[float] = None,
        side: str = 'LONG',
        symbol: Optional[str] = None,
        preferred_mode: Optional[str] = None,
        last_closed_action: str = "",
        last_closed_price: float = 0.0,
        last_closed_ts: float = 0.0,
        curr_ts: float = 0.0,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """審查跨週期共振開倉。
        
        Returns:
            (passed, reason, meta_dict)
        """
        if df_5m is None or len(df_5m) < 2:
            return False, "REJECT_ENTRY: INSUFFICIENT_5M_DATA", None

        side = side.upper()
        if side not in ('LONG', 'SHORT'):
            return False, f"REJECT_ENTRY: INVALID_SIDE_{side}", None

        # 1. 取得 15M 大趨勢指標 (若未傳入 df_15m，嘗試從 5m 重採樣或內部計算)
        if df_15m is None or len(df_15m) < 2:
            trend_15m_info = cls._resample_or_derive_15m(df_5m)
        else:
            trend_15m_info = DualTimeframeProvider.get_15m_trend(df_15m)

        big_trend = trend_15m_info.get('15m_big_trend', 'RANGE')
        kc_trend_15m = trend_15m_info.get('15m_kc_trend', 'RANGE')
        close_15m = float(trend_15m_info.get('15m_close', 0.0))
        ma15_15m = float(trend_15m_info.get('15m_ma15', 0.0))
        mid_15m = float(trend_15m_info.get('15m_kc_middle', 0.0))

        # 2. 取得 5M 操作週期指標
        m5 = DualTimeframeProvider.get_5m_metrics(df_5m)
        kc_trend_5m = resolve_kc_trend(df_5m)
        p = float(quote) if quote is not None else m5['5m_close']
        c = m5['5m_close']
        o = m5['5m_open']
        h = m5['5m_high']
        l = m5['5m_low']
        body = m5['5m_body']
        atr = m5['5m_atr']
        ma5 = m5['5m_ma5']
        ma15 = m5['5m_ma15']
        ma5_slope = m5['5m_ma5_slope']
        upper = m5['5m_kc_upper']
        lower = m5['5m_kc_lower']
        mid = m5['5m_kc_middle']
        candle_range = max(h - l, 1e-9)
        solid_ratio = body / candle_range
        upper_wick = h - max(c, o)
        lower_wick = min(c, o) - l
        dev = abs(p - ma5) / ma5 if ma5 > 0 else 0.0

        # 推定當前候選模式
        if preferred_mode:
            mode_candidate = preferred_mode
        elif side == 'LONG' and c > upper:
            mode_candidate = 'MODE1_KC_BREAKOUT'
        elif side == 'SHORT' and c < lower:
            mode_candidate = 'MODE1_KC_BREAKOUT'
        elif body >= 0.85 * atr and solid_ratio >= 0.70:
            mode_candidate = 'MODE3_GUILLOTINE_CUT'
        else:
            mode_candidate = 'MODE2_TRUE_CROSS'

        # 模式一、模式三與模式四：放寬 15M 大趨勢認定（解決反轉初期滯後問題）
        # 多單放行：只要 15m_kc_trend != "DOWN" 或 15M 收盤價站穩 MA15 與 KC 中軌（突破確立），且 15m_close 未實質跌破 15m_ma15，即判定大氣候允許，放行開多。
        live_c_15m = float(df_15m.iloc[-1]['close']) if df_15m is not None and len(df_15m) > 0 else close_15m
        live_mid_15m = float(df_15m.iloc[-1].get('kc_middle', mid_15m)) if df_15m is not None and len(df_15m) > 0 else mid_15m
        live_ma15_15m = float(df_15m.iloc[-1].get('ma15', ma15_15m)) if df_15m is not None and len(df_15m) > 0 else ma15_15m

        is_reclaim_15m_long = (live_c_15m >= live_mid_15m and live_c_15m >= live_ma15_15m) if live_ma15_15m > 0 else False
        is_reclaim_15m_short = (live_c_15m <= live_mid_15m and live_c_15m <= live_ma15_15m) if live_ma15_15m > 0 else False

        allow_15m_long = (kc_trend_15m != "DOWN" or is_reclaim_15m_long) and (ma15_15m <= 0 or close_15m >= ma15_15m * 0.995 or live_c_15m >= ma15_15m)
        allow_15m_short = (kc_trend_15m != "UP" or is_reclaim_15m_short) and (ma15_15m <= 0 or close_15m <= ma15_15m * 1.005 or live_c_15m <= ma15_15m)

        # ──【全域強制 KC 大趨勢與 MA5 走向硬阻斷 (Hard VETO)】──
        if side == 'LONG':
            # 多單絕對禁止逆勢開倉 (15M 明確下行 或 5M 中軌明確下行)
            if not allow_15m_long:
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open LONG when 15M KC is {kc_trend_15m} (15M_TREND_NOT_UP - Must be UP)", trend_15m_info, dev)
            if kc_trend_5m == 'DOWN':
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open LONG when 5M KC is DOWN", trend_15m_info, dev)
            # 均線間距 (>= 0.20 ATR) 與斜率動能 (>= +0.05 ATR，且 ma5_slope > +0.0003，若 <= +0.0001 走平微跌一票否決) 檢核
            # 模式三（斷頭鍘刀）允許大陽線一棒貫穿，此時 MA5 尚未完全穿越 MA15，豁免兩線相對順序與間距檢核
            if mode_candidate != 'MODE3_GUILLOTINE_CUT':
                ma_pass, ma_fail_reason = cls.validate_long_ma_condition(df_5m, min_gap_atr=0.20, min_up_slope_atr=0.05)
                if not ma_pass:
                    return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: {ma_fail_reason}", trend_15m_info, dev)
                if ma5 <= ma15:
                    return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open LONG when MA5 <= MA15 ({ma5:.6f} <= {ma15:.6f})", trend_15m_info, dev)
            if ma5_slope <= 0:
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open LONG when MA5 slope <= 0 ({ma5_slope:.6f})", trend_15m_info, dev)
            if quote is not None and quote < ma5 and mode_candidate != 'MODE3_GUILLOTINE_CUT':
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open LONG when price < MA5 ({quote:.6f} < {ma5:.6f})", trend_15m_info, dev)
            if quote is not None and quote < mid and mode_candidate != 'MODE3_GUILLOTINE_CUT':
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open LONG when price < KC Middle ({quote:.6f} < {mid:.6f})", trend_15m_info, dev)
        elif side == 'SHORT':
            # 空單絕對禁止逆勢開倉 (15M 明確上行 或 5M 中軌明確上行)
            if not allow_15m_short:
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open SHORT when 15M KC is {kc_trend_15m} (15M_TREND_NOT_DOWN - Must be DOWN)", trend_15m_info, dev)
            if kc_trend_5m == 'UP':
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open SHORT when 5M KC is UP", trend_15m_info, dev)
            # 均線間距 (>= 0.20 ATR) 與斜率動能 (<= -0.05 ATR，且 ma5_slope < -0.0003，若 >= -0.0001 走平翹頭一票否決) 檢核
            if mode_candidate != 'MODE3_GUILLOTINE_CUT':
                ma_pass, ma_fail_reason = cls.validate_short_ma_condition(df_5m, min_gap_atr=0.20, min_down_slope_atr=-0.05)
                if not ma_pass:
                    return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: {ma_fail_reason}", trend_15m_info, dev)
                if ma5 >= ma15:
                    return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open SHORT when MA5 >= MA15 ({ma5:.6f} >= {ma15:.6f})", trend_15m_info, dev)
            if ma5_slope >= 0:
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open SHORT when MA5 slope >= 0 ({ma5_slope:.6f})", trend_15m_info, dev)
            if quote is not None and quote > ma5 and mode_candidate != 'MODE3_GUILLOTINE_CUT':
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open SHORT when price > MA5 ({quote:.6f} > {ma5:.6f})", trend_15m_info, dev)
            if quote is not None and quote > mid and mode_candidate != 'MODE3_GUILLOTINE_CUT':
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open SHORT when price > KC Middle ({quote:.6f} > {mid:.6f})", trend_15m_info, dev)

        # ──【全域趨勢末端一票否決 (Trend Exhaustion VETO Gate)】──
        # 使用者鐵律：「若已在漲勢末端就不要再開倉」
        exh_ok, exh_reason = cls.validate_trend_exhaustion(df_5m, side, p)
        if not exh_ok:
            return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: {exh_reason}", trend_15m_info, dev)

        # ──【全域獲利空間一票否決 (Profit Room VETO Gate)】──
        # 使用者鐵律：「開倉平倉後冷靜一下有利潤在開倉」
        room_ok, room_reason = cls.validate_profit_room(df_5m, side, p)
        if not room_ok:
            return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: {room_reason}", trend_15m_info, dev)

        # ──【全域通道波動率過濾器一票否決 (Channel Volatility Filter VETO Gate)】──
        # 使用者鐵律：「通道過窄停止開倉，通道打開允許開倉」
        from core.gates.channel_volatility_filter import ChannelVolatilityFilter
        vol_filter = ChannelVolatilityFilter()
        vol_ok, vol_reason, vol_details = vol_filter.evaluate(df_5m)
        if not vol_ok:
            return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: {vol_reason}", trend_15m_info, dev)

        # ──【全域高低位追單與浮空進場一票否決 (Anti-Chase Gate)】──
        # 使用者鐵律：
        # 做多：高位平倉後冷卻 N 根、正乖離過大禁追、未回踩支撐禁追、長上影線滯漲禁追
        # 做空：平空後強制冷卻 5 根、KC 下軌負乖離過大超賣禁追、未回抽受阻收陰禁追、長下影線針尖力竭凍結 5 根
        from core.gates.high_position_chase_and_defense_filter import HighPositionChaseAndDefenseFilter
        chase_filter = HighPositionChaseAndDefenseFilter()
        if side == 'LONG':
            top_ok, top_reason, top_details = chase_filter.validate_long_entry(
                df=df_5m,
                quote=p,
                last_closed_action=last_closed_action,
                last_closed_ts=last_closed_ts,
                last_closed_price=last_closed_price,
            )
            if not top_ok:
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: {top_reason}", trend_15m_info, dev)
        elif side == 'SHORT':
            short_ok, short_reason, short_details = chase_filter.validate_short_entry(
                df=df_5m,
                quote=p,
                last_closed_action=last_closed_action,
                last_closed_ts=last_closed_ts,
                last_closed_price=last_closed_price,
            )
            if not short_ok:
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: {short_reason}", trend_15m_info, dev)

        # ──【平倉後回抽二次進場審查 (Pullback Re-entry Filter)】──
        # 嚴防平空後無反彈地板追空，或平多後無回踩天花板追多！
        if last_closed_action and last_closed_price > 0 and last_closed_ts > 0:
            pb_pass, pb_fail_reason = cls.validate_pullback_reentry(
                df=df_5m,
                side=side,
                quote=p,
                last_closed_action=last_closed_action,
                last_closed_price=last_closed_price,
                last_closed_ts=last_closed_ts,
                curr_ts=curr_ts,
                atr=atr,
                ma5=ma5,
            )
            if not pb_pass:
                return cls._fail_entry(symbol, mode_candidate, pb_fail_reason, trend_15m_info, dev)

        # ──【四大開倉模式判定】──
        # 模式一（5M KC 破軌開倉 / 盤中即時破軌）：
        # 5m 實體 >= 0.25 ATR（盤中未收線即時 >= 0.35 ATR）突破上軌/下軌且均線方向一致
        # 嚴格確認：做多必須為紅陽線 (c > o) 破上軌，做空必須為黑陰線 (c < o) 跌破下軌
        is_mode1 = False
        is_live_intra_bar = not bool(df_5m.iloc[-1].get('is_closed', True))
        min_body_atr = 0.35 if is_live_intra_bar else 0.25
        if body >= min_body_atr * atr:
            if side == 'LONG' and c > upper and c > o and ma5 > ma15 and ma5_slope > 0:
                quote_valid = (quote is None) or (quote >= min(upper, c) * 0.998 and quote >= ma5)
                if quote_valid and allow_15m_long and kc_trend_5m != 'DOWN':
                    is_mode1 = True
            elif side == 'SHORT' and c < lower and c < o and ma5 < ma15 and ma5_slope < 0:
                quote_valid = (quote is None) or (quote <= max(lower, c) * 1.002 and quote <= ma5)
                if quote_valid and allow_15m_short and kc_trend_5m != 'UP':
                    is_mode1 = True

        # 模式三（5M 斷頭鍘刀開倉）：
        # 實體 >= 0.85 ATR，佔比 >= 70%，無長影線強勢切穿均線或 KC 中軌
        is_mode3 = False
        if body >= 0.85 * atr and solid_ratio >= 0.70:
            if side == 'LONG':
                cut_ma15 = (o <= ma15 and c > ma15) or (o <= mid and c > mid)
                if cut_ma15 and c > o and upper_wick <= 0.25 * body and ma5_slope > 0:
                    quote_valid = (quote is None) or (quote >= c or quote >= ma15)
                    if quote_valid and allow_15m_long and kc_trend_5m != 'DOWN':
                        is_mode3 = True
            elif side == 'SHORT':
                cut_ma15 = (o >= ma15 and c < ma15) or (o >= mid and c < mid)
                if cut_ma15 and c < o and lower_wick <= 0.25 * body and ma5_slope < 0:
                    quote_valid = (quote is None) or (quote <= c or quote <= ma15)
                    if quote_valid and allow_15m_short and kc_trend_5m != 'UP':
                        is_mode3 = True

        # 模式四（5M KC 外軌延續開倉 / 強制上車）：
        # 上一根或當根實體在 KC 外軌外，最新報價嚴格位於外軌與 MA5 之外，順向發散不等待交叉
        is_mode4 = False
        prev_5m = df_5m.iloc[-2] if len(df_5m) >= 2 else df_5m.iloc[-1]
        prev_c = float(prev_5m.get('close', c))
        if side == 'LONG':
            if (c > upper or prev_c > upper) and (quote is not None and quote > upper and quote > ma5):
                if ma5 > ma15 and ma5_slope > 0 and kc_trend_5m != 'DOWN' and allow_15m_long:
                    is_mode4 = True
        elif side == 'SHORT':
            if (c < lower or prev_c < lower) and (quote is not None and quote < lower and quote < ma5):
                if ma5 < ma15 and ma5_slope < 0 and kc_trend_5m != 'UP' and allow_15m_short:
                    is_mode4 = True

        # ──【豁免破軌/斷頭/延續模式的乖離率與影線限制】──
        # 若觸發「模式一（KC 破軌）」、「模式三（斷頭鍘刀）」或「模式四（KC 延續）」，設定 bypass_spatial_check = True
        # 當 bypass_spatial_check == True 時，100% 豁免「1.2% 均線乖離率檢核」與「影線佔比檢核」，直接放行突破進場
        bypass_spatial_check = is_mode1 or is_mode3 or is_mode4

        meta = {
            'side': side,
            'price': p,
            '15m_big_trend': big_trend,
            '15m_kc_trend': kc_trend_15m,
            '5m_ma5_slope': ma5_slope,
            '5m_atr': atr,
            'bypass_spatial_check': bypass_spatial_check,
        }

        if is_mode1:
            meta['mode'] = 'MODE1_KC_BREAKOUT'
            return True, "AUTHORIZED_ENTRY_MODE1_KC_BREAKOUT", meta

        if is_mode3:
            meta['mode'] = 'MODE3_GUILLOTINE_CUT'
            return True, "AUTHORIZED_ENTRY_MODE3_GUILLOTINE", meta

        if is_mode4:
            meta['mode'] = 'MODE4_KC_CONTINUATION'
            return True, "AUTHORIZED_ENTRY_MODE4_KC_CONTINUATION", meta

        # ──【模式二：5M MA5/MA15 交叉震盪進場】──
        # 「15M 大趨勢順勢過濾」、「均線乖離率 > 1.2% 限制」與「影線比例限制」對模式二生效
        if side == 'LONG' and big_trend != 'UP':
            return cls._fail_entry(symbol, 'MODE2_TRUE_CROSS', f"REJECT_ENTRY: 15M_TREND_NOT_UP (15M={big_trend})", trend_15m_info, dev)
        elif side == 'SHORT' and big_trend != 'DOWN':
            return cls._fail_entry(symbol, 'MODE2_TRUE_CROSS', f"REJECT_ENTRY: 15M_TREND_NOT_DOWN (15M={big_trend})", trend_15m_info, dev)

        # 模式二：5M 影線實體過濾（放寬門檻：body_ratio >= 0.25，只擋極端十字星）
        if solid_ratio < 0.25:
            return cls._fail_entry(symbol, 'MODE2_TRUE_CROSS', f"REJECT_ENTRY: WEAK_CANDLE_SOLIDITY ({solid_ratio:.2f} < 0.25)", trend_15m_info, dev)

        if side == 'LONG':
            if upper_wick > 0.8 * body:
                return cls._fail_entry(symbol, 'MODE2_TRUE_CROSS', f"REJECT_ENTRY: UPPER_WICK_TOO_LONG ({upper_wick:.4f} > 0.8 * {body:.4f})", trend_15m_info, dev)
        elif side == 'SHORT':
            if lower_wick > 0.8 * body:
                return cls._fail_entry(symbol, 'MODE2_TRUE_CROSS', f"REJECT_ENTRY: LOWER_WICK_TOO_LONG ({lower_wick:.4f} > 0.8 * {body:.4f})", trend_15m_info, dev)

        # 模式二：5M 均線乖離率檢核 (> 1.2% 一票否決)
        if ma5 > 0 and dev > 0.012:
            return cls._fail_entry(symbol, 'MODE2_TRUE_CROSS', f"REJECT_ENTRY: MA5_BIAS_EXCEEDED ({dev:.4f} > 1.2%)", trend_15m_info, dev)

        # 模式二：判定過去 1~6 根真交叉且維持順向發散
        is_mode2 = False
        cross = m5['ma_cross']
        bars_ago = m5['cross_bars_ago']
        if bars_ago <= 6:
            if side == 'LONG' and cross == 'GOLDEN':
                if ma5_slope >= -1e-5 and ma5 > ma15:
                    is_mode2 = True
            elif side == 'SHORT' and cross == 'DEATH':
                if ma5_slope <= 1e-5 and ma5 < ma15:
                    is_mode2 = True

        if is_mode2:
            meta['mode'] = 'MODE2_TRUE_CROSS'
            return True, "AUTHORIZED_ENTRY_MODE2_TRUE_CROSS", meta

        return cls._fail_entry(symbol, mode_candidate, "REJECT_ENTRY: NO_VALID_5M_PATTERN_TRIGGERED", trend_15m_info, dev)

    @classmethod
    def _resample_or_derive_15m(cls, df_5m: pd.DataFrame) -> Dict[str, Any]:
        """若無獨立 15M 資料，由 5M K 棒合成或衍生 15M 趨勢指標"""
        try:
            if len(df_5m) < 6:
                return {'15m_big_trend': 'RANGE', '15m_kc_trend': 'RANGE'}

            # 簡易聚合為 15m (每 3 根 5m = 1 根 15m)
            df = df_5m.copy()
            # 若已有 15m 均線指標直接取用
            if 'ma15_15m' in df.columns or 'kc_trend_15m' in df.columns:
                c_last = df.iloc[-1]
                return {
                    '15m_big_trend': str(c_last.get('15m_big_trend', 'RANGE')),
                    '15m_kc_trend': str(c_last.get('15m_kc_trend', 'RANGE')),
                }

            # 模擬 15m 窗口 (長度為 3 倍 5m)
            c = float(df['close'].iloc[-1])
            ma15_proxy = float(df['close'].rolling(45, min_periods=5).mean().iloc[-1])
            prev_ma15_proxy = float(df['close'].rolling(45, min_periods=5).mean().iloc[-4])
            slope = ma15_proxy - prev_ma15_proxy

            kc_mid_proxy = float(df['close'].rolling(60, min_periods=5).mean().iloc[-1])
            prev_kc_mid = float(df['close'].rolling(60, min_periods=5).mean().iloc[-4])

            kc_trend = 'UP' if kc_mid_proxy > prev_kc_mid else ('DOWN' if kc_mid_proxy < prev_kc_mid else 'RANGE')

            if c >= ma15_proxy and kc_trend == 'UP' and slope >= 0:
                big_trend = 'UP'
            elif c <= ma15_proxy and kc_trend == 'DOWN' and slope <= 0:
                big_trend = 'DOWN'
            else:
                big_trend = 'RANGE'

            return {
                '15m_big_trend': big_trend,
                '15m_kc_trend': kc_trend,
                '15m_close': c,
                '15m_ma15': ma15_proxy,
                '15m_ma15_slope': slope,
            }
        except Exception:
            return {'15m_big_trend': 'RANGE', '15m_kc_trend': 'RANGE'}

    # Alias for compatibility
    evaluate_entry_modes = evaluate_entry
