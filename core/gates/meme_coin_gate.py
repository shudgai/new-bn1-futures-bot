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
from core.symbol_aliases import is_meme_breakout_symbol

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
    def evaluate_meme_entry(
        cls, frame: pd.DataFrame, quote: float, side: str, symbol: Optional[str] = None,
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

            # ── 1. 極短線動能防護（MA5 嚴格禁令）──
            if side == 'LONG':
                if live_ma5 <= prev_ma5:
                    return False, 'BLOCKED_BY_MA5_DOWNWARD_SLOPE', None
                if quote <= live_ma5:
                    return False, 'BLOCKED_BY_PRICE_BELOW_MA5', None

                # ── 2. 初始破軌極速捕捉（大陽線爆出 KC 上軌）──
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

                # ── 3. 錯過初次破軌的延續開倉（強制上車）──
                # 價格維持在 KC 上軌外狂奔，短暫整固且 MA5 昂揚向上，強制上車！
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

                # 初始破軌極速捕捉（大陰線摜破 KC 下軌）
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

                # 錯過初次破軌的延續開倉（強制上車做空）
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
                # 空單外軌鎖定：價格維持在 KC 下軌之外且壓制在 MA5 之下
                if kc_lower > 0 and quote < kc_lower and quote <= live_ma5:
                    return True, 'VETO_MEME_OUTER_RAIL_LOCK_HOLDING'
                if quote <= live_ma5:
                    return True, 'VETO_MEME_MA5_DEFENSE_HOLDING'

            return False, 'ALLOW_EXIT_EVALUATION'
        except Exception:
            return False, 'ERROR'

    @classmethod
    def evaluate_meme_exit(
        cls, frame: pd.DataFrame, quote: float, side: str, symbol: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """妖幣出場止盈點審查。
        
        出場條件（觸發平倉獲利了結）：
        1. 實體收盤/最新價明確砸回 KC 軌道內（多單 quote <= kc_upper，空單 quote >= kc_lower）
        2. 跌破 MA5 關鍵防守線（多單 quote < live_ma5，空單 quote > live_ma5）
        """
        if not cls.is_meme_coin(symbol) or frame is None or len(frame) < 1:
            return False, None

        try:
            curr = frame.iloc[-1]
            quote = float(quote)
            live_ma5, _ = cls.calculate_live_ma5(frame, quote)
            kc_upper = float(curr.get('kc_upper', 0.0))
            kc_lower = float(curr.get('kc_lower', 0.0))

            if side == 'LONG':
                # 條件 1：跌破 MA5 關鍵防守線
                if quote < live_ma5:
                    return True, 'EXIT_MEME_MA5_DEFENSE_BREACH'
                # 條件 2：實體明確砸回 KC 軌道內
                if kc_upper > 0 and quote <= kc_upper:
                    return True, 'EXIT_MEME_RETURN_INSIDE_KC_RAIL'

            elif side == 'SHORT':
                # 條件 1：突破 MA5 關鍵防守線
                if quote > live_ma5:
                    return True, 'EXIT_MEME_MA5_DEFENSE_BREACH'
                # 條件 2：反彈回 KC 軌道內
                if kc_lower > 0 and quote >= kc_lower:
                    return True, 'EXIT_MEME_RETURN_INSIDE_KC_RAIL'

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
            kc_middle = float(curr.get('kc_middle', 0.0))
            kc_upper = float(curr.get('kc_upper', 0.0))
            kc_lower = float(curr.get('kc_lower', 0.0))

            if not (math.isfinite(live_ma5) and math.isfinite(prev_ma5) and math.isfinite(kc_middle) and kc_middle > 0):
                return False, 'INVALID_MA_KC_DATA', None

            # 檢驗是否在軌道內（尚未突破外軌，方為提前埋伏）
            if side == 'LONG':
                # 尚未突破 KC 上軌
                if quote >= kc_upper:
                    return False, 'ALREADY_BROKE_KC_OUTER_RAIL', None
                # MA5 必須絕對向上
                if live_ma5 <= prev_ma5:
                    return False, 'BLOCKED_BY_MA5_DOWNWARD_SLOPE', None
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
            kc_upper = float(curr.get('kc_upper', 0.0))
            kc_lower = float(curr.get('kc_lower', 0.0))

            if side == 'LONG':
                # 實體衝出 KC 上軌且 MA5 昂揚發散
                if quote > kc_upper and quote > live_ma5 and live_ma5 > prev_ma5 and quote >= open_p:
                    return True, 'MEME_PYRAMID_ADDITION_LONG', {
                        'entry_type': 'MEME_PYRAMID_ADD',
                        'side': 'LONG',
                        'price': quote,
                        'suggested_size_ratio': 0.65,  # 加滿剩餘 65%~70%
                    }

            elif side == 'SHORT':
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
