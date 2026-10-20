"""妖幣專用：KC 破軌暴利與延續補倉閘門（MemeCoinGate）。

針對妖幣（如龍蝦、CAP 等）暴漲暴跌、莊家洗盤兇悍的特性，專門制定極速捕捉與外軌鎖利規則。

【核心原則】：
1. 初始破軌與極速捕捉：
   - 妖幣啟動主升浪極快，大陽線爆出 KC 上軌（實體 >= 0.20 ATR，MA5 向上），極速捕捉進場。
2. 錯過初次破軌的延續開倉（強制上車）：
   - 只要價格維持在 KC 上軌外狂奔，一旦短暫停頓且 MA5 依然昂揚向上，強制觸發延續進場（追隨強勢動能）。
3. KC 外軌鎖定（不被洗盤震下車）：
   - 狂拉過程中的小陰線或影線洗盤，只要實體/最新價維持在 KC 上軌外且守穩 MA5，一律不平倉、死抱利潤！
4. 極短線動能防護（MA5 嚴格禁令）：
   - 多單：MA5 只要下拐走跌（live_ma5 <= prev_ma5），一律嚴禁開多（防止接在瀑布頂端）。
   - 空單：MA5 只要上拐走揚（live_ma5 >= prev_ma5），一律嚴禁開空（防止被垂直軋空爆倉）。
5. 妖幣出場止盈點：
   - 實體明確砸回 KC 軌道內（close <= kc_upper）或盤中跌破 MA5 關鍵防守線（quote < live_ma5），果斷止盈了結。
"""
from typing import Tuple, Optional, Dict, Any
import math
import pandas as pd
from core.symbol_aliases import is_cap_symbol, is_meme_breakout_symbol

MEME_BREAKOUT_BODY_ATR = 0.20


MEME_ENTRY_CODES = frozenset((
    "MEME_INITIAL_KC_BREAKOUT_LONG",
    "MEME_INITIAL_KC_BREAKOUT_SHORT",
    "MEME_FORCED_CONTINUATION_ENTRY_LONG",
    "MEME_FORCED_CONTINUATION_ENTRY_SHORT",
    "MEME_AMBUSH_ENTRY_LONG",
    "MEME_AMBUSH_ENTRY_SHORT",
    "MEME_PYRAMID_ADDITION_LONG",
    "MEME_PYRAMID_ADDITION_SHORT",
))


class MemeCoinGate:
    """妖幣專用：KC 破軌暴利與延續補倉閘門。"""

    @staticmethod
    def is_meme_coin(symbol: Optional[str]) -> bool:
        """判定是否為妖幣/Meme 標的。"""
        return is_meme_breakout_symbol(symbol)

    @staticmethod
    def calculate_live_ma5(frame: pd.DataFrame, quote: float) -> Tuple[float, float]:
        """計算最新即時 MA5 與前一根 MA5。"""
        if frame is None or len(frame) < 1:
            return float('nan'), float('nan')
        curr = frame.iloc[-1]
        prev = frame.iloc[-2] if len(frame) >= 2 else curr
        live_close = float(curr.get('close', quote))
        is_closed = bool(curr.get('is_closed', False))
        prev_ma5 = float(prev.get('ma5', 0.0))
        curr_ma5 = float(curr.get('ma5', 0.0))

        if prev_ma5 <= 0 and len(frame) >= 6:
            prev_ma5 = float(frame['close'].iloc[-6:-1].mean())
        if curr_ma5 <= 0 and len(frame) >= 5:
            curr_ma5 = float(frame['close'].iloc[-5:].mean())
        elif curr_ma5 <= 0:
            curr_ma5 = float(quote)

        live_ma5 = curr_ma5 if is_closed else curr_ma5 + (float(quote) - live_close) / 5.0
        return live_ma5, prev_ma5

    @classmethod
    def calculate_kc_middle(cls, row: pd.Series) -> float:
        """取得或推算 K 棒之 KC 中軌。"""
        mid = float(row.get('kc_middle', row.get('ema_20', 0.0)))
        if mid > 0 and math.isfinite(mid):
            return mid
        upper = float(row.get('kc_upper', 0.0))
        lower = float(row.get('kc_lower', 0.0))
        if upper > 0 and lower > 0 and upper > lower:
            return (upper + lower) / 2.0
        return 0.0

    @classmethod
    def get_market_trend(
        cls, frame: pd.DataFrame, quote: Optional[float] = None,
    ) -> str:
        """判定市場主趨勢方向：
        
        Returns:
            'BULLISH': 明確多頭趨勢（大趨勢向上、KC向上、MA15向上或多頭排列）
            'BEARISH': 明確空頭趨勢（大趨勢向下、KC向下、MA15向下或空頭排列）
            'NEUTRAL': 無單邊明確趨勢（震盪/未定）
        """
        if frame is None or len(frame) < 2:
            return 'NEUTRAL'
        try:
            curr = frame.iloc[-1]
            prev = frame.iloc[-2]
            p = float(quote) if quote is not None else float(curr.get('close', 0.0))
            is_closed = bool(curr.get('is_closed', True))
            raw_close = float(curr.get('close', p))
            adj = (p - raw_close) if not is_closed else 0.0

            curr_mid = cls.calculate_kc_middle(curr)
            prev_mid = cls.calculate_kc_middle(prev)
            kc_slope = curr_mid - prev_mid if (curr_mid > 0 and prev_mid > 0) else 0.0

            live_ma5, _ = cls.calculate_live_ma5(frame, p)
            curr_ma15 = float(curr.get('ma15', p)) + adj / 15.0 if 'ma15' in curr else p
            prev_ma15 = float(prev.get('ma15', curr_ma15)) if 'ma15' in prev else curr_ma15
            ma15_slope = curr_ma15 - prev_ma15 if (curr_ma15 > 0 and prev_ma15 > 0) else 0.0

            is_bearish = False
            is_bullish = False

            if ma15_slope < 0 and live_ma5 <= curr_ma15:
                is_bearish = True
            elif kc_slope < 0 and ma15_slope < 0:
                is_bearish = True
            elif kc_slope < 0 and p < curr_mid and live_ma5 <= curr_ma15:
                is_bearish = True

            if ma15_slope > 0 and live_ma5 >= curr_ma15:
                is_bullish = True
            elif kc_slope > 0 and ma15_slope > 0:
                is_bullish = True
            elif kc_slope > 0 and p > curr_mid and live_ma5 >= curr_ma15:
                is_bullish = True

            if len(frame) >= 3:
                try:
                    from core.services.strategies.outer_strategy import ck_direction
                    closed_df = frame.iloc[:-1] if not is_closed and len(frame) >= 3 else frame
                    ck_dir = ck_direction(closed_df, has_forming_bar=False)
                    if ck_dir in ('SHORT', 'DOWN'):
                        is_bearish = True
                    elif ck_dir in ('LONG', 'UP'):
                        is_bullish = True
                except Exception:
                    pass

            if is_bearish and not is_bullish:
                return 'BEARISH'
            if is_bullish and not is_bearish:
                return 'BULLISH'
            return 'NEUTRAL'
        except Exception:
            return 'NEUTRAL'

    @classmethod
    def evaluate_meme_entry(
        cls, frame: pd.DataFrame, quote: float, side: str, symbol: Optional[str] = None,
        last_closed_price: Optional[float] = None,
        last_closed_side: Optional[str] = None,
        last_closed_ts: Optional[float] = None,
        **kwargs: Any,
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """妖幣進場審查（含極速捕捉與強制延續上車）。"""
        if not cls.is_meme_coin(symbol):
            return False, 'NOT_MEME_COIN', None

        if frame is None or len(frame) < 2 or side not in ('LONG', 'SHORT'):
            return False, 'INVALID_DATA', None

        curr = frame.iloc[-1]
        prev = frame.iloc[-2]

        try:
            quote = float(quote)
            open_p = float(curr.get('open', quote))
            live_ma5, prev_ma5 = cls.calculate_live_ma5(frame, quote)
            kc_upper = float(curr.get('kc_upper', 0.0))
            kc_lower = float(curr.get('kc_lower', 0.0))
            atr = float(prev.get('atr', curr.get('atr', 1.0)))
            if atr <= 0:
                atr = 1.0

            curr_kc_middle = cls.calculate_kc_middle(curr)
            prev_kc_middle = cls.calculate_kc_middle(prev)
            curr_kc_upper = kc_upper
            prev_kc_upper = float(prev.get('kc_upper', 0.0))
            curr_kc_lower = kc_lower
            prev_kc_lower = float(prev.get('kc_lower', 0.0))

            # ── 0. 動態開倉防刷保護（不鎖死時間，以突破與新 K 棒結構為準）──
            if last_closed_side and str(last_closed_side).upper() == side:
                curr_bar_ts = float(curr.get("timestamp") or 0.0)
                if last_closed_ts and float(last_closed_ts) > 0:
                    l_ts = float(last_closed_ts)
                    if l_ts > 1e11 and curr_bar_ts < 1e11:
                        l_ts /= 1000.0
                    elif l_ts < 1e11 and curr_bar_ts > 1e11:
                        l_ts *= 1000.0
                    # 1. 同一根 1m K 棒內平倉後，禁止同向立即反覆秒開
                    if abs(curr_bar_ts - l_ts) < 60000.0 or (curr_bar_ts > 0 and curr_bar_ts == l_ts):
                        return False, 'BLOCKED_SAME_CANDLE_ANTI_CHOP', None

                if last_closed_price and float(last_closed_price) > 0:
                    l_price = float(last_closed_price)
                    # 2. 如果上次平倉方向相同，必須等價格跌破上次平倉價或創近期新低，才准再度進場追空
                    if side == 'SHORT' and quote >= l_price:
                        return False, 'BLOCKED_REQUIRES_NEW_LOW_BELOW_LAST_EXIT', None
                    elif side == 'LONG' and quote <= l_price:
                        return False, 'BLOCKED_REQUIRES_NEW_HIGH_ABOVE_LAST_EXIT', None

            # ── 0. 順勢交易鐵律：如果趨勢是空頭不要開多，趨勢是多頭不要開空 ──
            from core.gates.strict_entry_and_immunity_gate import resolve_kc_trend, check_kc_flat_or_squeezing
            kc_trend = resolve_kc_trend(frame)
            if side == 'LONG' and kc_trend == 'DOWN':
                return False, 'VETO_CANNOT_LONG_WHEN_KC_DOWN', None
            if side == 'SHORT' and kc_trend == 'UP':
                return False, 'VETO_CANNOT_SHORT_WHEN_KC_UP', None

            is_outside = (side == 'LONG' and quote > kc_upper) or (side == 'SHORT' and quote < kc_lower)
            if not is_outside and check_kc_flat_or_squeezing(frame, quote=quote):
                return False, 'VETO_CHOPPY_MARKET_NO_ENTRY', None

            market_trend = cls.get_market_trend(frame, quote)
            if side == 'LONG' and market_trend == 'BEARISH':
                return False, 'BLOCKED_LONG_ON_BEARISH_TREND', None
            if side == 'SHORT' and market_trend == 'BULLISH':
                return False, 'BLOCKED_SHORT_ON_BULLISH_TREND', None

            # ── 1. 極短線動能防護（MA5 嚴格禁令）與 KC 通道趨勢防護 ──
            if side == 'LONG':
                if live_ma5 <= prev_ma5:
                    return False, 'BLOCKED_BY_MA5_SLOPE_FLAT_FOR_LONG', None
                if quote <= live_ma5:
                    return False, 'BLOCKED_BY_PRICE_BELOW_MA5', None

                # MA15 斜率防護：MA15 往下不可開多
                curr_ma15 = float(curr.get('ma15', quote))
                prev_ma15 = float(prev.get('ma15', curr_ma15))
                if curr_ma15 > 0 and prev_ma15 > 0 and curr_ma15 < prev_ma15:
                    return False, 'BLOCKED_BY_MA15_DOWNWARD_SLOPE', None

                # KC 沒往上不可開多（KC 中軌必須向上，上軌不可逆向）
                if curr_kc_middle > 0 and prev_kc_middle > 0:
                    if curr_kc_middle <= prev_kc_middle:
                        return False, 'BLOCKED_BY_KC_NOT_UPWARD', None
                if curr_kc_upper > 0 and prev_kc_upper > 0:
                    if curr_kc_upper < prev_kc_upper:
                        return False, 'BLOCKED_BY_KC_UPPER_DOWNWARD', None
                if len(frame) >= 3:
                    closed = frame.iloc[:-1] if not bool(curr.get('is_closed', False)) else frame
                    if len(closed) >= 2:
                        c_mid_now = cls.calculate_kc_middle(closed.iloc[-1])
                        c_mid_prev = cls.calculate_kc_middle(closed.iloc[-2])
                        if c_mid_now > 0 and c_mid_prev > 0 and c_mid_now < c_mid_prev:
                            return False, 'BLOCKED_BY_KC_CLOSED_DOWNWARD', None

                # ── 2. 初始破軌極速捕捉（大陽線爆出 KC 上軌，開盤在軌內或碰軌）──
                body = quote - open_p
                is_fresh_breakout = (
                    quote > kc_upper and body >= MEME_BREAKOUT_BODY_ATR * atr
                    and open_p <= kc_upper
                )
                if is_fresh_breakout:
                    return True, 'MEME_INITIAL_KC_BREAKOUT_LONG', {
                        'entry_type': 'MEME_INITIAL_BREAKOUT',
                        'side': 'LONG',
                        'price': quote,
                    }

                # ── 3. 錯過初次破軌的延續開倉（強制上車，妖幣專屬無限制解封）──
                # 價格維持在 KC 上軌外狂奔，且 MA5 昂揚向上，強制上車！
                is_outer_continuation = (
                    quote > kc_upper and quote > live_ma5
                    and live_ma5 > prev_ma5
                )
                if is_outer_continuation:
                    return True, 'MEME_FORCED_CONTINUATION_ENTRY_LONG', {
                        'entry_type': 'MEME_FORCED_CONTINUATION',
                        'side': 'LONG',
                        'price': quote,
                    }

            elif side == 'SHORT':
                if live_ma5 >= prev_ma5:
                    return False, 'BLOCKED_BY_MA5_UPWARD_SLOPE', None
                if quote >= live_ma5:
                    return False, 'BLOCKED_BY_PRICE_ABOVE_MA5', None

                # MA15 斜率防護：MA15 往上不可開空
                curr_ma15 = float(curr.get('ma15', quote))
                prev_ma15 = float(prev.get('ma15', curr_ma15))
                if curr_ma15 > 0 and prev_ma15 > 0 and curr_ma15 > prev_ma15:
                    return False, 'BLOCKED_BY_MA15_UPWARD_SLOPE', None

                # KC 沒往下不可開空（KC 中軌必須向下，下軌不可逆向向上翹）
                # 鐵律：即便只有 MA5 往下，KC 沒往下（走平或向上）一律嚴格禁止開空！
                if curr_kc_middle > 0 and prev_kc_middle > 0:
                    if curr_kc_middle >= prev_kc_middle:
                        return False, 'BLOCKED_BY_KC_NOT_DOWNWARD', None
                if curr_kc_lower > 0 and prev_kc_lower > 0:
                    if curr_kc_lower > prev_kc_lower:
                        return False, 'BLOCKED_BY_KC_LOWER_UPWARD', None
                if len(frame) >= 3:
                    closed = frame.iloc[:-1] if not bool(curr.get('is_closed', False)) else frame
                    if len(closed) >= 2:
                        c_mid_now = cls.calculate_kc_middle(closed.iloc[-1])
                        c_mid_prev = cls.calculate_kc_middle(closed.iloc[-2])
                        if c_mid_now > 0 and c_mid_prev > 0 and c_mid_now > c_mid_prev:
                            return False, 'BLOCKED_BY_KC_CLOSED_UPWARD', None

                # 初始破軌極速捕捉（大陰線摜破 KC 下軌，開盤在軌內或碰軌）
                body = open_p - quote
                is_fresh_breakout = (
                    kc_lower > 0 and quote < kc_lower
                    and body >= MEME_BREAKOUT_BODY_ATR * atr and open_p >= kc_lower
                )
                if is_fresh_breakout:
                    return True, 'MEME_INITIAL_KC_BREAKOUT_SHORT', {
                        'entry_type': 'MEME_INITIAL_BREAKOUT',
                        'side': 'SHORT',
                        'price': quote,
                    }

                # ── 錯過初次破軌的延續開倉（強制上車做空，妖幣專屬無限制解封）──
                is_outer_continuation = (
                    kc_lower > 0 and quote < kc_lower and quote < live_ma5
                    and live_ma5 < prev_ma5
                )
                if is_outer_continuation:
                    return True, 'MEME_FORCED_CONTINUATION_ENTRY_SHORT', {
                        'entry_type': 'MEME_FORCED_CONTINUATION',
                        'side': 'SHORT',
                        'price': quote,
                    }

            return False, 'BLOCKED_MEME_ENTRY_CONDITIONS_NOT_MET', None
        except Exception:
            return False, 'BLOCKED_BY_ERROR', None

    @classmethod
    def should_hold_outer_rail(
        cls, frame: pd.DataFrame, quote: float, side: str, symbol: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """KC 外軌鎖定：判斷妖幣是否應死抱利潤，拒絕被洗盤震下車。
        
        Returns:
            (should_hold: bool, reason: str)
            若 should_hold=True，表示價格仍在庫外或 MA5 防守線上方，應一律死抱，拒絕平倉。
        """
        if not cls.is_meme_coin(symbol) or frame is None or len(frame) < 1:
            return False, 'NOT_MEME_OR_INVALID_DATA'

        try:
            curr = frame.iloc[-1]
            quote = float(quote)
            live_ma5, _ = cls.calculate_live_ma5(frame, quote)
            kc_upper = float(curr.get('kc_upper', 0.0))
            kc_lower = float(curr.get('kc_lower', 0.0))

            if side == 'LONG':
                # 多單外軌鎖定：價格維持在 KC 上軌之外且守住 MA5
                if quote > kc_upper and quote >= live_ma5:
                    return True, 'VETO_MEME_OUTER_RAIL_LOCK_HOLDING'
                # 即使短暫刺穿 KC 上軌，只要未跌破 MA5，依然死抱
                if quote >= live_ma5:
                    return True, 'VETO_MEME_MA5_DEFENSE_HOLDING'

            elif side == 'SHORT':
                # ── 空單持倉防洗鐵律（龍蝦 / CAP 專用：空頭走勢不要平倉，要到真谷底才能平倉）──
                # 1. 價格維持在 KC 下軌之外
                if kc_lower > 0 and quote < kc_lower:
                    return True, 'VETO_MEME_OUTER_RAIL_LOCK_HOLDING'
                # 2. 價格壓制在 MA5 之下
                if quote <= live_ma5:
                    return True, 'VETO_MEME_MA5_DEFENSE_HOLDING'

                # 3. 空頭走勢護航：只要價格在 KC 中軌下方（quote <= kc_middle），且尚未收線確認形成真谷底，一票否決！
                kc_middle = cls.calculate_kc_middle(curr)
                from core.gates.holding_protection_gate import has_confirmed_true_pivot
                has_valley, _, _ = has_confirmed_true_pivot({'side': 'SHORT'}, frame, quote)
                if kc_middle > 0 and quote <= kc_middle and not has_valley:
                    return True, 'VETO_MEME_SHORT_HOLDING_UNTIL_TRUE_VALLEY'

            return False, 'ALLOW_EXIT_EVALUATION'
        except Exception:
            return False, 'ERROR'

    @classmethod
    def evaluate_meme_exit(
        cls, frame: pd.DataFrame, quote: float, side: str, symbol: Optional[str] = None,
        position: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[str]]:
        """妖幣出場止盈點審查。
        
        出場鐵律：
        - 龍蝦 / 1000PEPE 開空後，若都是空頭走勢（價格在 KC 中軌下方）不要平倉，除非鎖利或真谷底出現，要不然一律不平倉！
        - 註：未成真谷底前絕不放行平空，堅決吃滿主跌浪。
        """
        if not cls.is_meme_coin(symbol) or frame is None or len(frame) < 1:
            return False, None

        try:
            curr = frame.iloc[-1]
            quote = float(quote)
            live_ma5, _ = cls.calculate_live_ma5(frame, quote)
            kc_upper = float(curr.get('kc_upper', 0.0))
            kc_lower = float(curr.get('kc_lower', 0.0))
            pos = position or {'side': side}

            if side == 'LONG':
                # ── 妖幣多單出場鐵律：遇真峰頂要馬上平倉，不要等 1m 跑完才平倉！──
                from core.gates.holding_protection_gate import has_confirmed_true_pivot
                has_peak, _, _ = has_confirmed_true_pivot(pos, frame, quote)
                if has_peak:
                    return True, 'EXIT_MEME_TRUE_PEAK_CONFIRMED'

                # ── 徹底禁止碰上軌就平多！衝破上軌要穩穩抱住 ──
                # 唯一平多條件：收盤實體跌破 KC 中軌（或連續跌破中軌）才出場！
                kc_middle = cls.calculate_kc_middle(curr)
                if len(frame) >= 2 and kc_middle > 0:
                    c1 = frame.iloc[-1]
                    c2 = frame.iloc[-2]
                    mid1 = cls.calculate_kc_middle(c1)
                    mid2 = cls.calculate_kc_middle(c2)
                    c1_close = float(c1.get('close', quote))
                    c2_close = float(c2.get('close', c1_close))
                    if (c1_close < mid1 and c2_close < mid2) or quote < mid1 * 0.9985:
                        return True, 'EXIT_MEME_KC_MIDDLE_REVERSAL'

                # 其餘情況（在 KC 中軌上方、碰觸上軌、沿上軌走、稍觸 MA5 等）：
                # 100% 嚴禁平多，堅決沿著上軌抱緊主升浪！
                return False, None

            elif side == 'SHORT':
                # ── 妖幣空單出場鐵律：開空後不要平倉，除非鎖利或真谷底出現，要不然一律不平倉 ──
                from core.gates.holding_protection_gate import has_confirmed_true_pivot
                has_valley, _, _ = has_confirmed_true_pivot(pos, frame, quote)
                if has_valley:
                    return True, 'EXIT_MEME_TRUE_VALLEY_CONFIRMED'

                # ── 徹底禁止碰下軌就平空！衝破下軌要穩穩抱住 ──
                # 唯一平空條件：收盤實體站上 KC 中軌（或連續站上中軌）才出場！
                kc_middle = cls.calculate_kc_middle(curr)
                if len(frame) >= 2 and kc_middle > 0:
                    c1 = frame.iloc[-1]
                    c2 = frame.iloc[-2]
                    mid1 = cls.calculate_kc_middle(c1)
                    mid2 = cls.calculate_kc_middle(c2)
                    c1_close = float(c1.get('close', quote))
                    c2_close = float(c2.get('close', c1_close))
                    if (c1_close > mid1 and c2_close > mid2) or quote > mid1 * 1.0015:
                        return True, 'EXIT_MEME_KC_MIDDLE_REVERSAL'
                    mid2 = cls.calculate_kc_middle(c2)
                    c1_close = float(c1.get('close', quote))
                    c2_close = float(c2.get('close', c1_close))
                    if c1_close > mid1 and c2_close > mid2 and quote > mid1:
                        return True, 'EXIT_MEME_KC_MIDDLE_REVERSAL'

                # 其餘情況（在 KC 中軌下方、觸碰 MA5、回到下軌內等普通回踩雜訊），100% 嚴禁平空！
                return False, None

            return False, None
        except Exception:
            return False, None

    @classmethod
    def evaluate_ambush_entry(
        cls, frame: pd.DataFrame, quote: float, side: str, symbol: Optional[str] = None,
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """模組一：提前開倉（30% 底倉埋伏）條件審查。
        
        觸發條件（在突破 KC 外軌之前提早埋伏）：
        1. KC 縮口擠壓狀態：通道處於收斂蓄勢。
        2. MA5 陡峭向上金叉 KC 中軌（多單）或死叉貫穿中軌（空單）。
        3. 1m K 棒收盤/即時價實體站穩在 KC 中軌與 MA5 上方（多單）或下方（空單）。
        4. 量能異動：若有成交量，量能大於前 5 根均量的 1.3 倍以上。
        5. 倉位比例：底倉 30%～35%。
        """
        if not cls.is_meme_coin(symbol) or frame is None or len(frame) < 2 or side not in ('LONG', 'SHORT'):
            return False, 'NOT_MEME_OR_INVALID_DATA', None

        curr = frame.iloc[-1]
        prev = frame.iloc[-2]

        try:
            quote = float(quote)
            open_p = float(curr.get('open', quote))
            live_ma5, prev_ma5 = cls.calculate_live_ma5(frame, quote)
            kc_middle = cls.calculate_kc_middle(curr)
            prev_kc_middle = cls.calculate_kc_middle(prev)
            kc_upper = float(curr.get('kc_upper', 0.0))
            kc_lower = float(curr.get('kc_lower', 0.0))

            if not (math.isfinite(live_ma5) and math.isfinite(prev_ma5) and math.isfinite(kc_middle) and kc_middle > 0):
                return False, 'INVALID_MA_KC_DATA', None

            # ── 0. 順勢交易鐵律：如果趨勢是空頭不要開多，趨勢是多頭不要開空 ──
            market_trend = cls.get_market_trend(frame, quote)
            if side == 'LONG' and market_trend == 'BEARISH':
                return False, 'BLOCKED_LONG_ON_BEARISH_TREND', None
            if side == 'SHORT' and market_trend == 'BULLISH':
                return False, 'BLOCKED_SHORT_ON_BULLISH_TREND', None

            # 檢驗是否在軌道內（尚未突破外軌，方為提前埋伏）
            if side == 'LONG':
                # 尚未突破 KC 上軌
                if quote >= kc_upper:
                    return False, 'ALREADY_BROKE_KC_OUTER_RAIL', None
                # MA5 必須絕對向上
                if live_ma5 <= prev_ma5:
                    return False, 'BLOCKED_BY_MA5_DOWNWARD_SLOPE', None
                # MA15 往下不可開多
                curr_ma15 = float(curr.get('ma15', quote))
                prev_ma15 = float(prev.get('ma15', curr_ma15))
                if curr_ma15 > 0 and prev_ma15 > 0 and curr_ma15 < prev_ma15:
                    return False, 'BLOCKED_BY_MA15_DOWNWARD_SLOPE', None
                # KC 必須向上
                if prev_kc_middle > 0 and kc_middle <= prev_kc_middle:
                    return False, 'BLOCKED_BY_KC_NOT_UPWARD', None
                # 價格站穩在中軌與 MA5 之上
                if not (quote > live_ma5 and quote > kc_middle and quote >= open_p):
                    return False, 'BLOCKED_PRICE_NOT_ABOVE_MID_AND_MA5', None

                # 量能異動檢驗（若有 volume 欄位）
                if 'volume' in frame.columns and len(frame) >= 6:
                    vol = float(curr.get('volume', 0.0))
                    avg_vol = float(frame['volume'].iloc[-6:-1].mean())
                    if avg_vol > 0 and vol < 1.2 * avg_vol:
                        return False, 'BLOCKED_INSUFFICIENT_AMBUSH_VOLUME', None

                return True, 'MEME_AMBUSH_ENTRY_LONG', {
                    'entry_type': 'MEME_AMBUSH',
                    'side': 'LONG',
                    'price': quote,
                    'suggested_size_ratio': 0.35,  # 30%~35% 底倉
                }

            elif side == 'SHORT':
                if kc_lower > 0 and quote <= kc_lower:
                    return False, 'ALREADY_BROKE_KC_OUTER_RAIL', None
                if live_ma5 >= prev_ma5:
                    return False, 'BLOCKED_BY_MA5_UPWARD_SLOPE', None
                # MA15 往上不可開空
                curr_ma15 = float(curr.get('ma15', quote))
                prev_ma15 = float(prev.get('ma15', curr_ma15))
                if curr_ma15 > 0 and prev_ma15 > 0 and curr_ma15 > prev_ma15:
                    return False, 'BLOCKED_BY_MA15_UPWARD_SLOPE', None
                # KC 必須向下（KC 沒往下不可開空）
                if prev_kc_middle > 0 and kc_middle >= prev_kc_middle:
                    return False, 'BLOCKED_BY_KC_NOT_DOWNWARD', None
                if not (quote < live_ma5 and quote < kc_middle and quote <= open_p):
                    return False, 'BLOCKED_PRICE_NOT_BELOW_MID_AND_MA5', None

                if 'volume' in frame.columns and len(frame) >= 6:
                    vol = float(curr.get('volume', 0.0))
                    avg_vol = float(frame['volume'].iloc[-6:-1].mean())
                    if avg_vol > 0 and vol < 1.2 * avg_vol:
                        return False, 'BLOCKED_INSUFFICIENT_AMBUSH_VOLUME', None

                return True, 'MEME_AMBUSH_ENTRY_SHORT', {
                    'entry_type': 'MEME_AMBUSH',
                    'side': 'SHORT',
                    'price': quote,
                    'suggested_size_ratio': 0.35,  # 30%~35% 底倉
                }

            return False, 'CONDITIONS_NOT_MET', None
        except Exception:
            return False, 'ERROR', None

    @classmethod
    def evaluate_pyramid_addition(
        cls, frame: pd.DataFrame, quote: float, side: str, symbol: Optional[str] = None,
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """模組二：破軌確認（70% 加倉做滿）聯動機制。
        
        當底倉建立後，隨後 K 棒順利衝破 KC 外軌且兩根流確認延續，打入剩餘 65%~70% 倉位。
        """
        if not cls.is_meme_coin(symbol) or frame is None or len(frame) < 2 or side not in ('LONG', 'SHORT'):
            return False, 'NOT_MEME_OR_INVALID_DATA', None

        curr = frame.iloc[-1]
        prev = frame.iloc[-2]

        try:
            quote = float(quote)
            open_p = float(curr.get('open', quote))
            live_ma5, prev_ma5 = cls.calculate_live_ma5(frame, quote)
            curr_kc_middle = cls.calculate_kc_middle(curr)
            prev_kc_middle = cls.calculate_kc_middle(prev)
            kc_upper = float(curr.get('kc_upper', 0.0))
            kc_lower = float(curr.get('kc_lower', 0.0))

            # ── 0. 順勢交易鐵律：如果趨勢是空頭不要開多，趨勢是多頭不要開空 ──
            market_trend = cls.get_market_trend(frame, quote)
            if side == 'LONG' and market_trend == 'BEARISH':
                return False, 'BLOCKED_LONG_ON_BEARISH_TREND', None
            if side == 'SHORT' and market_trend == 'BULLISH':
                return False, 'BLOCKED_SHORT_ON_BULLISH_TREND', None

            if side == 'LONG':
                # 實體衝出 KC 上軌且 MA5 與 KC 中軌雙雙昂揚向上
                if (curr_kc_middle > 0 and prev_kc_middle > 0 and curr_kc_middle <= prev_kc_middle):
                    return False, 'BLOCKED_BY_KC_NOT_UPWARD', None
                if quote > kc_upper and quote > live_ma5 and live_ma5 > prev_ma5 and quote >= open_p:
                    return True, 'MEME_PYRAMID_ADDITION_LONG', {
                        'entry_type': 'MEME_PYRAMID_ADD',
                        'side': 'LONG',
                        'price': quote,
                        'suggested_size_ratio': 0.65,  # 加滿剩餘 65%~70%
                    }

            elif side == 'SHORT':
                if (curr_kc_middle > 0 and prev_kc_middle > 0 and curr_kc_middle >= prev_kc_middle):
                    return False, 'BLOCKED_BY_KC_NOT_DOWNWARD', None
                if kc_lower > 0 and quote < kc_lower and quote < live_ma5 and live_ma5 < prev_ma5 and quote <= open_p:
                    return True, 'MEME_PYRAMID_ADDITION_SHORT', {
                        'entry_type': 'MEME_PYRAMID_ADD',
                        'side': 'SHORT',
                        'price': quote,
                        'suggested_size_ratio': 0.65,
                    }

            return False, 'CONDITIONS_NOT_MET', None
        except Exception:
            return False, 'ERROR', None

    @classmethod
    def evaluate_ambush_failure_stop(
        cls, frame: pd.DataFrame, quote: float, side: str, symbol: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """模組三：提前埋伏失敗的「超敏捷認錯止損」。
        
        認錯停損條件：
        1. 盤中價格跌破 MA5 且 MA5 出現下拐/走平。
        2. 收盤價/即時價跌回 KC 中軌之下。
        """
        if not cls.is_meme_coin(symbol) or frame is None or len(frame) < 1:
            return False, None

        curr = frame.iloc[-1]
        try:
            quote = float(quote)
            live_ma5, prev_ma5 = cls.calculate_live_ma5(frame, quote)
            kc_middle = float(curr.get('kc_middle', 0.0))

            if side == 'LONG':
                # 認錯條件 A：價格跌破中軌
                if kc_middle > 0 and quote < kc_middle:
                    return True, 'EXIT_MEME_AMBUSH_FAIL_KC_MID_BREACH'
                # 認錯條件 B：MA5 下拐且跌破均線
                if live_ma5 <= prev_ma5 and quote < live_ma5:
                    return True, 'EXIT_MEME_AMBUSH_FAIL_MA5_DOWNWARD'

            elif side == 'SHORT':
                if kc_middle > 0 and quote > kc_middle:
                    return True, 'EXIT_MEME_AMBUSH_FAIL_KC_MID_BREACH'
                if live_ma5 >= prev_ma5 and quote > live_ma5:
                    return True, 'EXIT_MEME_AMBUSH_FAIL_MA5_UPWARD'

            return False, None
        except Exception:
            return False, None
