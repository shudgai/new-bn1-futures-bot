import pytest
from core.strategies.symmetric_meme_strategy import (
    SymmetricMemeTrendStrategy,
    StrategyConfig,
    Bar,
    TradeState,
    OrderAction,
)


def create_bar(timestamp: float, open_: float, high: float, low: float, close: float, volume: float = 100.0) -> Bar:
    return Bar(timestamp=timestamp, open=open_, high=high, low=low, close=close, volume=volume)


def test_symmetric_meme_strategy_long_and_veto_hold():
    strategy = SymmetricMemeTrendStrategy(StrategyConfig())

    # 1. 灌入 25 根平緩 K 棒建立基礎指標
    price = 100.0
    for i in range(25):
        bar = create_bar(i * 60, price, price + 0.5, price - 0.5, price)
        action = strategy.on_kline(bar, is_kline_closed=True)
        assert action is None

    # 2. 灌入一根大陽線突破前 N 高，且 KC 向上
    breakout_bar = create_bar(26 * 60, 100.0, 103.0, 99.8, 102.8)
    action = strategy.on_kline(breakout_bar, is_kline_closed=True)
    assert action is not None
    order_action, reason = action
    assert order_action == OrderAction.OPEN_LONG
    assert "LONG_MOMENTUM_BREAKOUT_CONFIRMED" in reason
    assert strategy.state == TradeState.HOLD_LONG
    assert strategy.position is not None

    # 3. 測試「正常回踩續抱 (Veto Hold)」：
    # 出現小陰線回踩，但價格仍在 KC 中軌之上且 KC 中軌向上
    pullback_bar = create_bar(27 * 60, 102.8, 102.9, 101.8, 102.0)
    action = strategy.on_kline(pullback_bar, is_kline_closed=True)
    # 嚴格續抱，絕不平倉
    assert action is None
    assert strategy.state == TradeState.HOLD_LONG

    # 4. 測試「平多雙重確認」：收盤跌破中軌或動能衰退
    dump_bar = create_bar(28 * 60, 102.0, 102.0, 98.0, 98.5)
    action = strategy.on_kline(dump_bar, is_kline_closed=True)
    assert action is not None
    order_action, reason = action
    assert order_action == OrderAction.CLOSE_LONG
    assert "DOUBLE_CONFIRMATION" in reason or "HARD_SL" in reason
    assert strategy.state == TradeState.IDLE


def test_symmetric_meme_strategy_short_and_veto_hold():
    strategy = SymmetricMemeTrendStrategy(StrategyConfig())

    # 1. 灌入 25 根微跌 K 棒建立空頭趨勢
    price = 100.0
    for i in range(25):
        price -= 0.1
        bar = create_bar(i * 60, price + 0.1, price + 0.3, price - 0.3, price)
        strategy.on_kline(bar, is_kline_closed=True)

    # 2. 灌入一根適度大陰線跌破前 N 低 (約 2.2 ATR 乖離，在 max_mid_bias_atr 2.8 內)
    breakdown_bar = create_bar(26 * 60, price, price + 0.1, price - 0.6, price - 0.5)
    action = strategy.on_kline(breakdown_bar, is_kline_closed=True)
    assert action is not None
    order_action, reason = action
    assert order_action == OrderAction.OPEN_SHORT
    assert "SHORT_MOMENTUM_BREAKDOWN_CONFIRMED" in reason
    assert strategy.state == TradeState.HOLD_SHORT

    # 3. 測試「正常回抽續抱 (Veto Hold)」：小陽線反抽，仍在 KC 中軌之下且 KC 向下
    curr_close = price - 1.2
    rebound_bar = create_bar(27 * 60, curr_close, curr_close + 0.3, curr_close - 0.1, curr_close + 0.2)
    action = strategy.on_kline(rebound_bar, is_kline_closed=True)
    # 空方蓄力續抱，嚴禁平空
    assert action is None
    assert strategy.state == TradeState.HOLD_SHORT


def test_intrabar_ignored():
    strategy = SymmetricMemeTrendStrategy(StrategyConfig())
    bar = create_bar(1000, 100.0, 105.0, 99.0, 104.5)
    # is_kline_closed=False 盤中一律不觸發
    action = strategy.on_kline(bar, is_kline_closed=False)
    assert action is None
