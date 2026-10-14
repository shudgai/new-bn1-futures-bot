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

        # 1. 均線間距（空頭排列下 ma15 應大於 ma5）
        ma_gap = current_ma15 - current_ma5
        gap_ratio_atr = ma_gap / current_atr
        if gap_ratio_atr < min_gap_atr:
            return False, f"REJECT_ENTRY: MA_CONVERGENCE (Gap: {gap_ratio_atr:.2f} ATR < {min_gap_atr:.2f} ATR)"

        # 2. MA5 斜率（以 ATR 標準化）
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

        # 1. 均線間距（多頭排列下 ma5 應大於 ma15）
        ma_gap = current_ma5 - current_ma15
        gap_ratio_atr = ma_gap / current_atr
        if gap_ratio_atr < min_gap_atr:
            return False, f"REJECT_ENTRY: MA_CONVERGENCE (Gap: {gap_ratio_atr:.2f} ATR < {min_gap_atr:.2f} ATR)"

        # 2. MA5 斜率（以 ATR 標準化）
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
            # 若平倉後完全沒有出現小反彈，且最新價格趴在平倉價下方或原地追空，一票否決！
            if not has_bounced and (quote <= last_closed_price or (ma5 > 0 and quote < ma5)):
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
            if not has_pulled_back and (quote >= last_closed_price or (ma5 > 0 and quote > ma5)):
                return False, (
                    f"REJECT_REENTRY: NO_PULLBACK_AFTER_EXIT ("
                    f"LastCloseLong: {last_closed_price:.6f}, MinLowSince: {min_low_since:.6f}, "
                    f"Quote: {quote:.6f}, MA5: {ma5:.6f}, Elapsed: {elapsed_ms/1000:.1f}s - Ceiling Chase Blocked)"
                )

        return True, "PASS"

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

        # 模式一與模式三：放寬 15M 大趨勢認定（解決反轉初期滯後問題）
        # 多單放行：只要 15m_kc_trend != "DOWN" 且 15m_close 未低於 15m_ma15 * 0.995，即判定大氣候允許，放行開多。
        allow_15m_long = (kc_trend_15m != "DOWN") and (ma15_15m <= 0 or close_15m >= ma15_15m * 0.995)
        # 空單放行：只要 15m_kc_trend != "UP" 且 15m_close 未高於 15m_ma15 * 1.005，即判定大氣候允許，放行開空。
        allow_15m_short = (kc_trend_15m != "UP") and (ma15_15m <= 0 or close_15m <= ma15_15m * 1.005)

        # ──【全域強制 KC 大趨勢與 MA5 走向硬阻斷 (Hard VETO)】──
        if side == 'LONG':
            # 多單絕對禁止逆勢開倉
            if not allow_15m_long:
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open LONG when 15M KC is {kc_trend_15m} (15M_TREND_NOT_UP - Must be UP)", trend_15m_info, dev)
            if kc_trend_5m != 'UP':
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open LONG when 5M KC is {kc_trend_5m} (Must be UP)", trend_15m_info, dev)
            if ma5_slope <= 0:
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open LONG when MA5 slope <= 0 ({ma5_slope:.6f})", trend_15m_info, dev)
            if ma5 <= ma15:
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open LONG when MA5 <= MA15 ({ma5:.6f} <= {ma15:.6f})", trend_15m_info, dev)
            # 均線間距 (>= 0.20 ATR) 與斜率動能 (>= +0.05 ATR) 檢核
            ma_pass, ma_fail_reason = cls.validate_long_ma_condition(df_5m, min_gap_atr=0.20, min_up_slope_atr=0.05)
            if not ma_pass:
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: {ma_fail_reason}", trend_15m_info, dev)
            if quote is not None and quote < ma5:
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open LONG when price < MA5 ({quote:.6f} < {ma5:.6f})", trend_15m_info, dev)
            if quote is not None and quote < mid:
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open LONG when price < KC Middle ({quote:.6f} < {mid:.6f})", trend_15m_info, dev)
        elif side == 'SHORT':
            # 空單絕對禁止逆勢開倉
            if not allow_15m_short:
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open SHORT when 15M KC is {kc_trend_15m} (15M_TREND_NOT_DOWN - Must be DOWN)", trend_15m_info, dev)
            if kc_trend_5m != 'DOWN':
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open SHORT when 5M KC is {kc_trend_5m} (Must be DOWN)", trend_15m_info, dev)
            if ma5_slope >= 0:
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open SHORT when MA5 slope >= 0 ({ma5_slope:.6f})", trend_15m_info, dev)
            if ma5 >= ma15:
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open SHORT when MA5 >= MA15 ({ma5:.6f} >= {ma15:.6f})", trend_15m_info, dev)
            # 均線間距 (>= 0.20 ATR) 與斜率動能 (<= -0.05 ATR) 檢核
            ma_pass, ma_fail_reason = cls.validate_short_ma_condition(df_5m, min_gap_atr=0.20, min_down_slope_atr=-0.05)
            if not ma_pass:
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: {ma_fail_reason}", trend_15m_info, dev)
            if quote is not None and quote > ma5:
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open SHORT when price > MA5 ({quote:.6f} > {ma5:.6f})", trend_15m_info, dev)
            if quote is not None and quote > mid:
                return cls._fail_entry(symbol, mode_candidate, f"REJECT_ENTRY: CRITICAL_BLOCK: Forbidden to open SHORT when price > KC Middle ({quote:.6f} > {mid:.6f})", trend_15m_info, dev)

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

        # ──【三大開倉模式判定】──
        # 模式一（5M KC 破軌開倉 / 盤中即時破軌）：
        # 5m 實體 >= 0.25 ATR（盤中未收線即時 >= 0.35 ATR）突破上軌/下軌且均線方向一致
        # 嚴格確認：做多必須為紅陽線 (c > o) 破上軌，做空必須為黑陰線 (c < o) 跌破下軌
        is_mode1 = False
        is_live_intra_bar = not bool(df_5m.iloc[-1].get('is_closed', True))
        min_body_atr = 0.35 if is_live_intra_bar else 0.25
        if body >= min_body_atr * atr:
            if side == 'LONG' and c > upper and c > o and ma5 > ma15 and ma5_slope > 0:
                quote_valid = (quote is None) or (quote >= min(upper, c) * 0.998 and quote >= ma5)
                if quote_valid and allow_15m_long and kc_trend_5m == 'UP':
                    is_mode1 = True
            elif side == 'SHORT' and c < lower and c < o and ma5 < ma15 and ma5_slope < 0:
                quote_valid = (quote is None) or (quote <= max(lower, c) * 1.002 and quote <= ma5)
                if quote_valid and allow_15m_short and kc_trend_5m == 'DOWN':
                    is_mode1 = True

        # 模式三（5M 斷頭鍘刀開倉）：
        # 實體 >= 0.85 ATR，佔比 >= 70%，無長影線強勢切穿均線或 KC 中軌
        is_mode3 = False
        if body >= 0.85 * atr and solid_ratio >= 0.70:
            if side == 'LONG':
                cut_ma15 = (o <= ma15 and c > ma15) or (o <= mid and c > mid)
                if cut_ma15 and c > o and upper_wick <= 0.25 * body and ma5_slope > 0:
                    quote_valid = (quote is None) or (quote >= c or quote >= ma15)
                    if quote_valid and allow_15m_long and kc_trend_5m == 'UP':
                        is_mode3 = True
            elif side == 'SHORT':
                cut_ma15 = (o >= ma15 and c < ma15) or (o >= mid and c < mid)
                if cut_ma15 and c < o and lower_wick <= 0.25 * body and ma5_slope < 0:
                    quote_valid = (quote is None) or (quote <= c or quote <= ma15)
                    if quote_valid and allow_15m_short and kc_trend_5m == 'DOWN':
                        is_mode3 = True

        # ──【豁免破軌/斷頭模式的乖離率與影線限制】──
        # 若觸發「模式一（KC 破軌）」或「模式三（斷頭鍘刀）」，設定 bypass_spatial_check = True
        # 當 bypass_spatial_check == True 時，100% 豁免「1.2% 均線乖離率檢核」與「影線佔比檢核」，直接放行突破進場
        bypass_spatial_check = is_mode1 or is_mode3

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
