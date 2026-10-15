"""HighPositionChaseAndDefenseFilter - 高位追多過濾與分級防守機制

解決問題：
1. 單邊拉升後高位平多，間隔僅 2~3 根 K 棒在 KC 上軌外浮空追高 (天花板接刀)
2. 追高後動能衰竭收陰，卻死等跌破 KC 中軌才平倉，導致小虧演變成深度虧損

兩大核心機能：
【改進 A：開倉端防護 (Anti-Top Chase Gate)】
1. 平倉冷卻期 (Cooldown)：高位平倉後強制凍結 N 根 K 棒 (預設 5 根 K 棒)，避免在同一個頂部連續進場。
2. 回踩健康度檢驗 (Pullback Health Check)：
   - 價格距離 KC 中軌乖離過大時禁止開多 ((Close - KC_Middle) / ATR > max_bias_atr)。
   - 禁止浮空進場：必須滿足「至少回踩測試極短期均線或 KC 中軌 (Low <= EMA3/EMA5 或 Low <= KC_Middle) 且獲得支撐反彈」，不可在空中追單。
3. 前 1~3 根長上影線/滯漲避險：若近 3 根 K 棒內出現上影線佔比 > 50% 的避雷針，判定主力出貨滯漲，一票否決開多。

【改進 B：出場端分級防守 (Tiered Position Defense)】
1. 起爆點進場單 (BREAKOUT_INITIAL)：可寬鬆防守至 KC 中軌。
2. 高位順勢追單 (HIGH_CHASE)：嚴禁等待 KC 中軌！
   - ① 跌破進場當根 K 棒的最低點 (Low) 即刻離場 (EXIT_HIGH_CHASE_PIERCE_ENTRY_LOW)。
   - ② 或收盤跌破 3 EMA / 5 EMA 即刻硬止損 (EXIT_HIGH_CHASE_CLOSE_BREAK_EMA)。
"""
import math
import logging
from typing import Dict, Any, Optional, Tuple, List
import pandas as pd

logger = logging.getLogger(__name__)


class HighPositionChaseAndDefenseFilter:
    """高位追單過濾與分級防守器"""

    def __init__(
        self,
        cooldown_bars: int = 5,          # 平多後強制冷凍根數 (預設 5 根)
        max_bias_atr: float = 3.0,       # 距 KC 中軌乖離上限 (超過 3.0 ATR 極限延伸才禁追)
        max_bias_pct: float = 0.05,      # 距 KC 中軌百分比上限 (超過 5.0% 禁追)
        wick_exhaustion_ratio: float = 0.50, # 上影線長度佔比上限 (超過 50% 判定滯漲出貨)
    ):
        self.cooldown_bars = cooldown_bars
        self.max_bias_atr = max_bias_atr
        self.max_bias_pct = max_bias_pct
        self.wick_exhaustion_ratio = wick_exhaustion_ratio

    # ══════════════════════════════════════════════════════════════════════
    # 【改進 A：開倉過濾（禁止動能竭盡時追高）】
    # ══════════════════════════════════════════════════════════════════════
    def validate_long_entry(
        self,
        df: pd.DataFrame,
        quote: float,
        last_closed_action: str = "",
        last_closed_ts: float = 0.0,
        last_closed_price: float = 0.0,
        timeframe_ms: float = 60_000.0,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """開多前置審查：嚴格過濾天花板追多、浮空進場與頂部滯漲。
        
        Returns:
            (can_enter, reason, details)
        """
        if df is None or len(df) < 1:
            return False, "REJECT_ENTRY: INSUFFICIENT_DATA", {}

        curr = df.iloc[-1]
        c = float(curr.get('close', quote))
        o = float(curr.get('open', quote))
        h = float(curr.get('high', quote))
        l = float(curr.get('low', quote))
        p = float(quote) if quote is not None else c

        atr = float(curr.get('atr', 0.0))
        if atr <= 0:
            atr = max(c * 0.01, 1e-8)

        kc_middle = float(curr.get('kc_middle', curr.get('kc_basis', 0.0)))
        kc_upper = float(curr.get('kc_upper', 0.0))
        ma5 = float(curr.get('ma5', c))
        ema3 = float(curr.get('ema3', curr.get('ma3', ma5)))

        details = {
            'quote': p,
            'close': c,
            'kc_middle': kc_middle,
            'kc_upper': kc_upper,
            'ema3': ema3,
            'ma5': ma5,
            'atr': atr,
        }

        # ── 1. 平倉冷卻期 (Cooldown) ──
        # 若前一筆多單是在高位平多 (CLOSE_LONG)，且平倉距今不足 N 根 K 棒，禁止在同一個頂部連續進場！
        if last_closed_action in ('CLOSE_LONG', 'EXIT_LONG'):
            curr_ts = float(curr.get('timestamp', 0.0))
            if 0 < curr_ts < 1e11:
                curr_ts *= 1000.0
            if 0 < last_closed_ts < 1e11:
                last_closed_ts *= 1000.0

            if curr_ts > 0 and last_closed_ts > 0:
                bars_since_close = (curr_ts - last_closed_ts) / timeframe_ms
                details['bars_since_close'] = round(bars_since_close, 2)
                if bars_since_close < self.cooldown_bars:
                    reason = (f"VETO: LONG_COOLDOWN_ACTIVE "
                              f"(Only {bars_since_close:.1f} bars since LastClose, must wait >= {self.cooldown_bars} bars)")
                    return False, reason, details

        # ── 2. 中軌正乖離過大檢驗 (KC Bias Check) ──
        # 價格懸空在 KC 上軌之外且遠離中軌，嚴禁無腦追多（初始起爆單除外：開盤於軌道內突破）
        is_initial_breakout = (o < kc_upper - 0.2 * atr) and (c > o) and (not last_closed_action)
        if not is_initial_breakout and kc_middle > 0:
            bias_atr = (p - kc_middle) / atr
            bias_pct = (p - kc_middle) / kc_middle
            details['bias_atr'] = round(bias_atr, 2)
            details['bias_pct'] = round(bias_pct, 4)

            if bias_atr > self.max_bias_atr or bias_pct > self.max_bias_pct:
                reason = (f"VETO: OVEREXTENDED_BIAS_TOO_HIGH "
                          f"(Price: {p:.6g} is {bias_atr:.2f} ATR / {bias_pct:.2%} above KC Mid, max: {self.max_bias_atr} ATR)")
                return False, reason, details

        # ── 3. 回踩健康度檢驗（禁止浮空進場）──
        # 鐵律：普通通道內進場必須回踩均線或中軌；但若處於破軌突破或外軌強勢延續（Breakout Flow），依授權手冊執行極速進場與延續開倉！
        is_breakout_flow = (c > kc_upper and c > o and (c - o) >= 0.25 * atr) or (p > kc_upper and p > ma5)
        recent_3 = df.tail(3)
        min_low_recent = float(recent_3['low'].min()) if 'low' in recent_3.columns else l
        has_tested_support = (min_low_recent <= ema3) or (min_low_recent <= ma5) or (kc_middle > 0 and min_low_recent <= kc_middle)
        details['min_low_recent_3'] = min_low_recent
        details['has_tested_support'] = has_tested_support

        if not is_breakout_flow and not has_tested_support and (p > kc_upper or p > ma5 + 0.3 * atr):
            reason = (f"VETO: FLOATING_CHASE_WITHOUT_PULLBACK "
                      f"(Recent low {min_low_recent:.6g} never tested EMA3 {ema3:.6g} / MA5 {ma5:.6g} - Floating in the air)")
            return False, reason, details

        # ── 4. 近 1~3 根長上影線/滯漲避險 ──
        # 若前 1~3 根 K 棒出現長上影線（上影線佔比 > 50%），判定頂部主力出貨滯漲，禁止立即接刀！
        for idx in range(1, min(len(df), 4)):
            bar = df.iloc[-idx]
            b_h = float(bar.get('high', 0.0))
            b_l = float(bar.get('low', 0.0))
            b_o = float(bar.get('open', 0.0))
            b_c = float(bar.get('close', 0.0))
            b_range = max(b_h - b_l, 1e-9)
            b_upper_wick = max(0.0, b_h - max(b_o, b_c))
            wick_ratio = b_upper_wick / b_range

            if wick_ratio >= self.wick_exhaustion_ratio and b_h >= (kc_upper - 0.2 * atr):
                reason = (f"VETO: TOP_STALLING_UPPER_WICK "
                          f"(Bar[-{idx}] has {wick_ratio:.1%} upper wick at {b_h:.6g} >= KC_Upper - Exhaustion selling pressure)")
                details['stalling_bar_idx'] = idx
                details['stalling_wick_ratio'] = round(wick_ratio, 3)
                return False, reason, details
        # ── 5. 順勢鐵律：KC 中軌下方或中軌下行嚴禁開多（嚴防陰跌接飛刀）──
        if kc_middle > 0:
            if p < kc_middle:
                reason = (f"VETO: BLOCKED_LONG_BELOW_KC_MIDDLE "
                          f"(Price {p:.6g} < KC_Middle {kc_middle:.6g} - No catching falling knives below basis)")
                return False, reason, details
            if len(df) >= 2:
                prev_kc_middle = float(df.iloc[-2].get('kc_middle', 0.0))
                if prev_kc_middle > 0 and kc_middle < prev_kc_middle:
                    reason = (f"VETO: BLOCKED_LONG_ON_FALLING_KC_MIDDLE "
                              f"(KC_Middle falling from {prev_kc_middle:.6g} to {kc_middle:.6g})")
                    return False, reason, details

        return True, "PASS: HEALTHY_LONG_ENTRY", details

    def validate_short_entry(
        self,
        df: pd.DataFrame,
        quote: float,
        last_closed_action: str = "",
        last_closed_ts: float = 0.0,
        last_closed_price: float = 0.0,
        timeframe_ms: float = 60_000.0,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """開空前置審查：嚴格過濾地板追空、長下影線力竭與平空後連續追單。

        三大硬性過濾條件：
        1. KC 下軌外負乖離過大（禁止地板追空）：
           - 價格位於 KC 下軌外側，且 (KC_Basis - Close) / KC_Basis > 負乖離閾值時，視為超賣恐慌區，嚴禁新開空單。
           - 合法的順勢空單進場，必須等待價格「回抽測試 KC 中軌或 3/5 EMA 均線受阻收陰」後，才可進場。
        2. 長下影線/針尖力竭過濾（Climax Pinbar Filter）：
           - 若當前或前一根 K 棒出現長下影線（下影線長度 > 整根 K 棒的 50%），判定為底部主力吃單/空頭衰竭，強制凍結賣空信號至少 5 根 K 棒。
        3. 平空後強制冷卻（Cooldown Protection）：
           - 當系統剛觸發「平空」離場後，必須強制等待 N 根 K 棒（預設 5 根 1M K 棒），期間嚴禁再次發出開空信號，徹底避免在同一個底部連續被巴。
        """
        if df is None or len(df) < 1:
            return False, "REJECT_ENTRY: INSUFFICIENT_DATA", {}

        curr = df.iloc[-1]
        c = float(curr.get('close', quote))
        o = float(curr.get('open', quote))
        h = float(curr.get('high', quote))
        l = float(curr.get('low', quote))
        p = float(quote) if quote is not None else c

        atr = float(curr.get('atr', 0.0))
        if atr <= 0:
            atr = max(c * 0.01, 1e-8)

        kc_middle = float(curr.get('kc_middle', curr.get('kc_basis', 0.0)))
        kc_lower = float(curr.get('kc_lower', 0.0))
        ma5 = float(curr.get('ma5', c))
        ema3 = float(curr.get('ema3', curr.get('ma3', ma5)))

        details = {
            'quote': p,
            'close': c,
            'kc_middle': kc_middle,
            'kc_lower': kc_lower,
            'ema3': ema3,
            'ma5': ma5,
            'atr': atr,
        }

        # ── 1. 平空後強制冷卻期 (Cooldown Protection) ──
        # 當系統剛觸發「平空」離場後 (CLOSE_SHORT / EXIT_SHORT)，必須強制等待 N 根 K 棒 (預設 5 根)，
        # 期間嚴禁再次發出開空信號，徹底避免在同一個底部連續被巴！
        if last_closed_action in ('CLOSE_SHORT', 'EXIT_SHORT'):
            curr_ts = float(curr.get('timestamp', 0.0))
            if 0 < curr_ts < 1e11:
                curr_ts *= 1000.0
            if 0 < last_closed_ts < 1e11:
                last_closed_ts *= 1000.0

            if curr_ts > 0 and last_closed_ts > 0:
                bars_since_close = (curr_ts - last_closed_ts) / timeframe_ms
                details['bars_since_close'] = round(bars_since_close, 2)
                if bars_since_close < self.cooldown_bars:
                    reason = (f"VETO: SHORT_COOLDOWN_ACTIVE "
                              f"(Only {bars_since_close:.1f} bars since LastCloseShort, must wait >= {self.cooldown_bars} bars to prevent whipsaw)")
                    return False, reason, details

        # ── 2. 長下影線/針尖力竭過濾 (Climax Pinbar Filter) ──
        # 若當前或前一根 K 棒出現長下影線（下影線長度 > 整根 K 棒的 50%），判定為底部主力吃單/空頭衰竭，
        # 強制凍結賣空信號至少 5 根 K 棒！
        # 回溯檢查最近 5 根 K 棒，若曾出現長下影線 Pinbar，強制阻斷賣空！
        max_lookback = min(len(df) + 1, self.cooldown_bars + 1)
        for idx in range(1, max_lookback):
            bar = df.iloc[-idx]
            b_h = float(bar.get('high', 0.0))
            b_l = float(bar.get('low', 0.0))
            b_o = float(bar.get('open', 0.0))
            b_c = float(bar.get('close', 0.0))
            b_range = max(b_h - b_l, 1e-9)
            b_lower_wick = max(0.0, min(b_o, b_c) - b_l)
            wick_ratio = b_lower_wick / b_range

            if wick_ratio >= self.wick_exhaustion_ratio:
                bars_ago = idx - 1
                reason = (f"VETO: BOTTOM_CLIMAX_PINBAR_EXHAUSTION "
                          f"(Bar[-{idx}] has {wick_ratio:.1%} lower wick pinbar at low {b_l:.6g} - Whale bottom absorption, shorting frozen for {self.cooldown_bars} bars)")
                details['climax_bar_idx'] = idx
                details['climax_wick_ratio'] = round(wick_ratio, 3)
                return False, reason, details

        # ── 3. KC 下軌外負乖離過大（禁止地板追空）──
        # 價格位於 KC 下軌外側，且 (KC_Basis - Close) / KC_Basis > 負乖離閾值時，視為超賣恐慌區，一律嚴禁新開空單。
        # 初始跌破單（開在通道內跌破下軌）除外
        is_initial_breakdown = (o > kc_lower + 0.2 * atr) and (c < o) and (not last_closed_action)
        if not is_initial_breakdown and kc_middle > 0:
            bias_atr = (kc_middle - p) / atr
            bias_pct = (kc_middle - p) / kc_middle
            details['bias_atr'] = round(bias_atr, 2)
            details['bias_pct'] = round(bias_pct, 4)

            # 當價格在下軌外側或已深度超賣
            is_outside_lower = (kc_lower > 0 and p <= kc_lower) or (c <= kc_lower)
            if is_outside_lower and (bias_atr > self.max_bias_atr or bias_pct > self.max_bias_pct):
                reason = (f"VETO: OVEREXTENDED_NEGATIVE_BIAS_TOO_HIGH "
                          f"(Price: {p:.6g} is outside KC Lower with {bias_atr:.2f} ATR / {bias_pct:.2%} negative bias below KC Mid, max: {self.max_bias_atr} ATR - Panic oversold floor, no chasing shorts)")
                return False, reason, details

        # ── 4. 回抽測試受阻收陰檢驗（禁止浮空追空）──
        # 鐵律：普通通道內進場必須等待回抽受阻；但若處於破軌突破或外軌強勢延續（Breakout Flow），依授權手冊執行極速進場與延續開空！
        is_breakout_flow = (c < kc_lower and c < o and (o - c) >= 0.25 * atr) or (p < kc_lower and p < ma5)
        recent_3 = df.tail(3)
        max_high_recent = float(recent_3['high'].max()) if 'high' in recent_3.columns else h
        has_tested_resistance = (max_high_recent >= ema3) or (max_high_recent >= ma5) or (kc_middle > 0 and max_high_recent >= kc_middle)
        details['max_high_recent_3'] = max_high_recent
        details['has_tested_resistance'] = has_tested_resistance

        if not is_breakout_flow and not has_tested_resistance and (p < kc_lower or p < ma5 - 0.3 * atr):
            reason = (f"VETO: FLOATING_SHORT_CHASE_WITHOUT_PULLBACK "
                      f"(Recent high {max_high_recent:.6g} never tested EMA3 {ema3:.6g} / MA5 {ma5:.6g} - Floating in the air without resistance test)")
            return False, reason, details

        # ── 5. 順勢鐵律：KC 中軌上方或中軌上行嚴禁開空（嚴防大陽線反轉摸頂被軋空）──
        if kc_middle > 0:
            if p > kc_middle:
                reason = (f"VETO: BLOCKED_SHORT_ABOVE_KC_MIDDLE "
                          f"(Price {p:.6g} > KC_Middle {kc_middle:.6g} - No shorting above basis)")
                return False, reason, details
            if len(df) >= 2:
                prev_kc_middle = float(df.iloc[-2].get('kc_middle', 0.0))
                if prev_kc_middle > 0 and kc_middle > prev_kc_middle:
                    reason = (f"VETO: BLOCKED_SHORT_ON_RISING_KC_MIDDLE "
                              f"(KC_Middle rising from {prev_kc_middle:.6g} to {kc_middle:.6g})")
                    return False, reason, details

        return True, "PASS: HEALTHY_SHORT_ENTRY", details

    def validate_entry(
        self,
        side: str,
        df: pd.DataFrame,
        quote: float,
        last_closed_action: str = "",
        last_closed_ts: float = 0.0,
        last_closed_price: float = 0.0,
        timeframe_ms: float = 60_000.0,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """通用開倉審查入口（依據多/空方向分流）"""
        side_upper = str(side).upper()
        if side_upper == 'LONG':
            return self.validate_long_entry(
                df=df, quote=quote, last_closed_action=last_closed_action,
                last_closed_ts=last_closed_ts, last_closed_price=last_closed_price,
                timeframe_ms=timeframe_ms
            )
        elif side_upper == 'SHORT':
            return self.validate_short_entry(
                df=df, quote=quote, last_closed_action=last_closed_action,
                last_closed_ts=last_closed_ts, last_closed_price=last_closed_price,
                timeframe_ms=timeframe_ms
            )
        return False, f"REJECT_ENTRY: INVALID_SIDE ({side})", {}
    def evaluate_tiered_defense_exit(
        self,
        position: Dict[str, Any],
        df: pd.DataFrame,
        quote: float,
    ) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """高位單分級防守審查：
        - 起爆底倉 (BREAKOUT_INITIAL)：可寬鬆防守至 KC 中軌。
        - 高位追單 (HIGH_CHASE)：嚴禁死扛至中軌！
          ① 跌破進場當根 K 棒最低點 (Low) 立即止損。
          ② 或收盤跌破 3 EMA / 5 EMA 立即硬止損。
        
        Returns:
            (should_exit, exit_reason, exit_details)
        """
        if not position or df is None or len(df) == 0:
            return False, None, {}

        side = str(position.get('side', '')).upper()
        if side != 'LONG':
            # 空單對稱邏輯：高位追空突破進場棒 High 或收盤站上 3 EMA / 5 EMA 立即砍倉
            return self._evaluate_tiered_defense_short(position, df, quote)

        entry_price = float(position.get('entry_price', 0.0))
        if entry_price <= 0:
            return False, None, {}

        p = float(quote) if quote is not None else entry_price
        curr = df.iloc[-1]
        c = float(curr.get('close', p))
        o = float(curr.get('open', p))
        ma5 = float(curr.get('ma5', c))
        ema3 = float(curr.get('ema3', curr.get('ma3', ma5)))
        kc_middle = float(curr.get('kc_middle', curr.get('kc_basis', 0.0)))
        kc_upper = float(curr.get('kc_upper', 0.0))
        atr = float(curr.get('atr', entry_price * 0.01))

        # 取得進場時的特徵 (是否為高位追單)
        # 若進場時開在 KC 上軌外或進場價遠高於 KC 中軌 (> 1.0 ATR)，歸類為 HIGH_CHASE
        is_high_chase = bool(position.get('is_high_chase', False))
        entry_snapshot = position.get('entry_snapshot') or {}
        entry_bar_low = float(position.get('entry_bar_low') or entry_snapshot.get('low') or 0.0)

        # 動態補足判定：若進場時 entry_price > kc_upper 或 (entry_price - kc_middle) >= 1.0 * atr，判定為高位追單
        if not is_high_chase and (kc_upper > 0 and entry_price >= kc_upper or (kc_middle > 0 and (entry_price - kc_middle) >= 1.0 * atr)):
            is_high_chase = True
            position['is_high_chase'] = True

        details = {
            'is_high_chase': is_high_chase,
            'entry_price': entry_price,
            'entry_bar_low': entry_bar_low,
            'quote': p,
            'ema3': ema3,
            'ma5': ma5,
            'kc_middle': kc_middle,
        }

        # ──【高位追單專用防守機制 (HIGH_CHASE Strict Exit)】──
        if is_high_chase:
            # ① 跌破進場當根 K 棒的最低點 (Low) 即刻離場，嚴防假突破反殺！
            if entry_bar_low > 0 and p < entry_bar_low:
                return True, "EXIT_HIGH_CHASE_PIERCE_ENTRY_LOW", {
                    'desc': '高位追單跌破進場棒最低點，嚴格超敏捷止損，拒絕深扛！',
                    'entry_bar_low': entry_bar_low,
                    'trigger_price': p,
                }

            # ② 實體收盤跌破極短期攻擊線 (3 EMA / 5 EMA) 即刻硬止損！
            is_closed = bool(curr.get('is_closed', True))
            if is_closed:
                # 實體收跌破 3 EMA 且收陰
                if c < ema3 and c < o:
                    return True, "EXIT_HIGH_CHASE_CLOSE_BREAK_EMA3", {
                        'desc': '高位追單收盤摜破 3 EMA，動能熄火即刻離場！',
                        'close': c,
                        'ema3': ema3,
                    }
                # 收盤跌破 5 EMA
                if c < ma5:
                    return True, "EXIT_HIGH_CHASE_CLOSE_BREAK_MA5", {
                        'desc': '高位追單收盤失守 5 EMA 關鍵防守線，立即止損！',
                        'close': c,
                        'ma5': ma5,
                    }

            # 高位追單絕不死扛至中軌！
            return False, None, details

        # ──【起爆點進場單防守機制 (BREAKOUT_INITIAL)】──
        # 起爆單具備成本優勢，可寬鬆防守至 KC 中軌收盤確認
        is_closed = bool(curr.get('is_closed', True))
        if is_closed and kc_middle > 0 and c < kc_middle:
            return True, "EXIT_INITIAL_BREAKOUT_BREACH_KC_MID", {
                'desc': '起爆底倉跌破 KC 中軌，結構破位離場',
                'close': c,
                'kc_middle': kc_middle,
            }

        return False, None, details

    def _evaluate_tiered_defense_short(
        self,
        position: Dict[str, Any],
        df: pd.DataFrame,
        quote: float,
    ) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """空單對稱分級防守審查"""
        entry_price = float(position.get('entry_price', 0.0))
        p = float(quote) if quote is not None else entry_price
        curr = df.iloc[-1]
        c = float(curr.get('close', p))
        o = float(curr.get('open', p))
        ma5 = float(curr.get('ma5', c))
        ema3 = float(curr.get('ema3', curr.get('ma3', ma5)))
        kc_middle = float(curr.get('kc_middle', curr.get('kc_basis', 0.0)))
        kc_lower = float(curr.get('kc_lower', 0.0))
        atr = float(curr.get('atr', entry_price * 0.01))

        is_high_chase = bool(position.get('is_high_chase', False))
        entry_snapshot = position.get('entry_snapshot') or {}
        entry_bar_high = float(position.get('entry_bar_high') or entry_snapshot.get('high') or 0.0)

        if not is_high_chase and (kc_lower > 0 and entry_price <= kc_lower or (kc_middle > 0 and (kc_middle - entry_price) >= 1.0 * atr)):
            is_high_chase = True
            position['is_high_chase'] = True

        if is_high_chase:
            # ① 突破進場當根 K 棒最高點 (High) 即刻砍倉
            if entry_bar_high > 0 and p > entry_bar_high:
                return True, "EXIT_HIGH_CHASE_PIERCE_ENTRY_HIGH", {
                    'desc': '低位追空突破進場棒最高點，超敏捷止損！',
                    'entry_bar_high': entry_bar_high,
                    'trigger_price': p,
                }
            # ② 收盤站上 3 EMA / 5 EMA 立即硬止損
            is_closed = bool(curr.get('is_closed', True))
            if is_closed:
                if c > ema3 and c > o:
                    return True, "EXIT_HIGH_CHASE_CLOSE_BREAK_EMA3", {
                        'desc': '低位追空收盤突破 3 EMA，動能熄火離場！',
                        'close': c,
                        'ema3': ema3,
                    }
                if c > ma5:
                    return True, "EXIT_HIGH_CHASE_CLOSE_BREAK_MA5", {
                        'desc': '低位追空收盤失守 5 EMA，立即止損！',
                        'close': c,
                        'ma5': ma5,
                    }
            return False, None, {}

        is_closed = bool(curr.get('is_closed', True))
        if is_closed and kc_middle > 0 and c > kc_middle:
            return True, "EXIT_INITIAL_BREAKOUT_BREACH_KC_MID", {
                'desc': '起爆空單突破 KC 中軌，結構破位離場',
                'close': c,
                'kc_middle': kc_middle,
            }

        return False, None, {}
