"""妖幣專用：KC 破軌暴利與延續補倉閘門（MemeCoinGate）。

針對妖幣（如龍蝦、CAP 等）暴漲暴跌、莊家洗盤兇悍的特性，專門制定極速捕捉與外軌鎖利規則。

【核心原則】：
1. 初始破軌與極速捕捉：
   - 妖幣啟動主升浪極快，大陽線爆出 KC 上軌（實體 >= 0.30 ATR，MA5 向上），極速捕捉進場。
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
                    quote > kc_upper and body >= 0.30 * atr
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
                    and body >= 0.30 * atr and open_p >= kc_lower
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
