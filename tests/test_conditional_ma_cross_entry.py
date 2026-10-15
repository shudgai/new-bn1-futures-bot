import math
import pandas as pd
import pytest

from core.services.strategies.unified_entry_strategy import check_ma_cross_entry
from core.gates.pipeline import fast_cross_evidence


def _build_test_frame(trend='UP', n=30, base_price=100.0, atr=1.0):
    rows = []
    price = base_price
    for i in range(n):
        if trend == 'UP':
            price += 0.2
            o = price - 0.15
            c = price + 0.15
        elif trend == 'DOWN':
            price -= 0.2
            o = price + 0.15
            c = price - 0.15
        else:
            o = price
            c = price
        h = max(o, c) + 0.05
        l = min(o, c) - 0.05
        rows.append({
            'timestamp': 1700000000000 + i * 60000,
            'open': o,
            'high': h,
            'low': l,
            'close': c,
            'volume': 100.0,
            'is_closed': True,
            'atr': atr,
            'kc_middle': price,
            'kc_upper': price + 2.0 * atr,
            'kc_lower': price - 2.0 * atr,
            'ma5': price,
            'ma15': price,
        })
    return pd.DataFrame(rows)


def test_golden_cross_blocked_on_bearish_trend():
    """1. 空頭趨勢中嚴禁金叉開多（順勢鐵律）：空頭下跌時的金叉摸底一票否決。"""
    df = _build_test_frame(trend='DOWN', n=30, base_price=100.0)
    # 人工在最後構造一個金叉反彈
    df.loc[df.index[-2], 'ma5'] = 94.0
    df.loc[df.index[-2], 'ma15'] = 94.2
    df.loc[df.index[-1], 'ma5'] = 94.6
    df.loc[df.index[-1], 'ma15'] = 94.4
    df.loc[df.index[-1], 'open'] = 94.3
    df.loc[df.index[-1], 'close'] = 94.8
    df.loc[df.index[-1], 'high'] = 94.9
    df.loc[df.index[-1], 'low'] = 94.2

    # 由於主趨勢為 DOWN/BEARISH，金叉嚴禁開多
    decision = check_ma_cross_entry(df, live_price=94.8)
    assert decision is None, "空頭趨勢中出現金叉應被一票否決，嚴禁摸底開多"


def test_death_cross_blocked_on_bullish_trend():
    """2. 多頭趨勢中嚴禁死叉開空（順勢鐵律）：多頭主升浪中的死叉摸頂一票否決。"""
    df = _build_test_frame(trend='UP', n=30, base_price=100.0)
    # 人工在最後構造一個死叉插針
    df.loc[df.index[-2], 'ma5'] = 106.2
    df.loc[df.index[-2], 'ma15'] = 106.0
    df.loc[df.index[-1], 'ma5'] = 105.8
    df.loc[df.index[-1], 'ma15'] = 105.9
    df.loc[df.index[-1], 'open'] = 106.1
    df.loc[df.index[-1], 'close'] = 105.5
    df.loc[df.index[-1], 'high'] = 106.2
    df.loc[df.index[-1], 'low'] = 105.4

    # 由於主趨勢為 UP/BULLISH，死叉嚴禁開空
    decision = check_ma_cross_entry(df, live_price=105.5)
    assert decision is None, "多頭趨勢中出現死叉應被一票否決，嚴禁摸頂開空"


def test_golden_cross_blocked_when_kc_middle_sloping_down():
    """3. 金叉但 KC 中軌下拐時嚴禁開多：通道趨勢未同步一票否決。"""
    df = _build_test_frame(trend='UP', n=30, base_price=100.0)
    # 讓 KC 中軌下行
    df.loc[df.index[-1], 'kc_middle'] = df.loc[df.index[-2], 'kc_middle'] - 0.2
    df.loc[df.index[-2], 'ma5'] = 105.8
    df.loc[df.index[-2], 'ma15'] = 106.0
    df.loc[df.index[-1], 'ma5'] = 106.4
    df.loc[df.index[-1], 'ma15'] = 106.1
    df.loc[df.index[-1], 'open'] = 106.0
    df.loc[df.index[-1], 'close'] = 106.6
    df.loc[df.index[-1], 'high'] = 106.7
    df.loc[df.index[-1], 'low'] = 105.9

    decision = check_ma_cross_entry(df, live_price=106.6)
    assert decision is None, "KC 中軌向下傾斜時，金叉嚴禁開多"


def test_death_cross_blocked_when_kc_middle_sloping_up():
    """4. 死叉但 KC 中軌上拐時嚴禁開空：通道趨勢未同步一票否決。"""
    df = _build_test_frame(trend='DOWN', n=30, base_price=100.0)
    # 讓 KC 中軌上行
    df.loc[df.index[-1], 'kc_middle'] = df.loc[df.index[-2], 'kc_middle'] + 0.2
    df.loc[df.index[-2], 'ma5'] = 94.2
    df.loc[df.index[-2], 'ma15'] = 94.0
    df.loc[df.index[-1], 'ma5'] = 93.6
    df.loc[df.index[-1], 'ma15'] = 93.9
    df.loc[df.index[-1], 'open'] = 93.8
    df.loc[df.index[-1], 'close'] = 93.3
    df.loc[df.index[-1], 'high'] = 93.9
    df.loc[df.index[-1], 'low'] = 93.2

    decision = check_ma_cross_entry(df, live_price=93.3)
    assert decision is None, "KC 中軌向上傾斜時，死叉嚴禁開空"


def test_golden_cross_blocked_when_ma15_sloping_down():
    """5. 金叉但 MA15 仍向下拐頭時嚴禁開多：均線動能未發散。"""
    df = _build_test_frame(trend='UP', n=30, base_price=100.0)
    df.loc[df.index[-2], 'ma5'] = 105.8
    df.loc[df.index[-2], 'ma15'] = 106.0
    # MA5 向上，但 MA15 向下拐頭
    df.loc[df.index[-1], 'ma5'] = 106.2
    df.loc[df.index[-1], 'ma15'] = 105.7  # 105.7 < 106.0
    df.loc[df.index[-1], 'open'] = 106.0
    df.loc[df.index[-1], 'close'] = 106.5
    df.loc[df.index[-1], 'high'] = 106.6
    df.loc[df.index[-1], 'low'] = 105.9

    decision = check_ma_cross_entry(df, live_price=106.5)
    assert decision is None, "MA15 向下拐頭時，金叉嚴禁開多"


def test_golden_cross_blocked_when_body_insufficient():
    """6. 金叉但 K 棒實體微弱無動能（十字星）時拒絕開多。"""
    df = _build_test_frame(trend='UP', n=30, base_price=100.0, atr=1.0)
    df.loc[df.index[-2], 'ma5'] = 105.8
    df.loc[df.index[-2], 'ma15'] = 106.0
    df.loc[df.index[-1], 'ma5'] = 106.2
    df.loc[df.index[-1], 'ma15'] = 106.1
    # 實體只有 0.1 ATR (小於 0.3 ATR 門檻)
    df.loc[df.index[-1], 'open'] = 106.21
    df.loc[df.index[-1], 'close'] = 106.25
    df.loc[df.index[-1], 'high'] = 106.4
    df.loc[df.index[-1], 'low'] = 106.1

    decision = check_ma_cross_entry(df, live_price=106.25)
    assert decision is None, "微弱無動能實體十字星金叉，拒絕開多"




