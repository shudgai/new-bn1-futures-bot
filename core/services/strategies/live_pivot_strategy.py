"""Confirm a live turn from ordered quotes, never from a completed candle wick.

峰谷開倉邏輯：
- 多頭（LONG）：觀察逐報價先下跌（創出低點）後反彈上升，確認為谷底，立即開多
- 空頭（SHORT）：觀察逐報價先上漲（創出高點）後回落下跌，確認為峰頂，立即開空
"""
import math


class LivePivot:
    def __init__(self):
        self.states = {}

    def reset(self, symbol):
        self.states.pop(symbol, None)

    def observe(self, symbol, frame, price, side, now):
        """
        觀察報價轉向，判斷是否到達峰/谷並轉向。
        side 是 CK 趨勢方向（多頭趨勢 → 找谷底做多；空頭趨勢 → 找峰頂做空）。
        """
        try:
            price, now = float(price), float(now)
            bar = float(frame.iloc[-1]['timestamp']) / 1000
            if (side not in ('LONG', 'SHORT')
                    or not all(math.isfinite(v) and v > 0 for v in (price, now, bar))
                    or bar % 60 or not bar <= now < bar + 60):
                self.reset(symbol)
                return False

            identity = (bar, side)
            state = self.states.get(symbol)

            # 新 bar 或方向變了，重置觀察
            if state is None or state['identity'] != identity:
                self.states[symbol] = dict(
                    identity=identity,
                    extreme=price,   # 觀察到的最極端價格（谷底或峰頂）
                    adverse=False,   # 是否已觀察到逆向回調
                    ready=False,
                    at=now
                )
                return False

            state['at'] = now
            prev_extreme = state['extreme']

            if side == 'LONG':
                # 找谷底：先跌到最低點（更新 extreme），然後反彈
                if price < prev_extreme:
                    # 繼續創新低，更新谷底
                    state['extreme'] = price
                    state['adverse'] = True
                    state['ready'] = False
                elif price > prev_extreme and state['adverse']:
                    # 已有谷底且價格開始反彈 → 谷底確認，可以做多
                    state['ready'] = True
            else:  # SHORT
                # 找峰頂：先漲到最高點（更新 extreme），然後回落
                if price > prev_extreme:
                    # 繼續創新高，更新峰頂
                    state['extreme'] = price
                    state['adverse'] = True
                    state['ready'] = False
                elif price < prev_extreme and state['adverse']:
                    # 已有峰頂且價格開始下跌 → 峰頂確認，可以做空
                    state['ready'] = True

            return state['ready']
        except (AttributeError, KeyError, IndexError, TypeError, ValueError):
            self.reset(symbol)
            return False
