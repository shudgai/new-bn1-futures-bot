import pytest
from core.services.strategies.pure_trend_v2 import PureTrendStrategyV2

@pytest.fixture
def strategy():
    return PureTrendStrategyV2()

def base_bar():
    return {
        'open': 100.0, 'close': 102.0, 'high': 103.0, 'low': 99.0,
        'kc_upper': 101.0, 'kc_lower': 95.0, 'kc_middle': 98.0,
        'ma3': 102.0, 'prev_ma3': 101.0, 'ma15': 97.0, 'atr': 1.0,
        'x': True, 'is_closed': True
    }

# -------------------------------------------------------------
# 測試 1：未收盤偷跑測試 (Intra-bar Leakage Test)
# -------------------------------------------------------------
def test_reject_unclosed_bar(strategy):
    bar_curr = base_bar()
    bar_curr['x'] = False
    bar_curr['is_closed'] = False
    
    signal = strategy.evaluate_entry('TEST/USDT', bar_curr, base_bar(), base_bar())
    assert signal is None, "❌ 失敗：盤中未收線竟然放行了開倉訊號！"

# -------------------------------------------------------------
# 測試 2：第三根反向 K 阻斷測試 (The Infamous 3rd Bar Red Test)
# -------------------------------------------------------------
def test_reject_3rd_bar_red_candle(strategy):
    bar_p2 = base_bar()  # Bar 1: 綠陽破上軌
    bar_p1 = base_bar()  # Bar 2: 綠陽續推
    
    # Bar 3: 衝高回落收大紅黑陰線 (close <= open)
    bar_curr = base_bar()
    bar_curr['open'] = 105.0
    bar_curr['close'] = 103.0  # 收陰！
    bar_curr['high'] = 106.0
    bar_curr['low'] = 102.0
    
    signal = strategy.evaluate_entry('TEST/USDT', bar_curr, bar_p1, bar_p2)
    assert signal is None, "❌ 失敗：第三根收紅K陰線竟然開了多單！接盤天花板慘劇重演！"

# -------------------------------------------------------------
# 測試 3：物理方向禁區測試 (No Shorting on Upper Track)
# -------------------------------------------------------------
def test_direction_forbidden_zones(strategy):
    # 價格在中軌上方（102 > 98），測試是否可能吐出 SHORT 訊號
    bar_curr = base_bar()
    signal = strategy.evaluate_entry('TEST/USDT', bar_curr, base_bar(), base_bar())
    if signal:
        assert signal['side'] != 'SHORT', "❌ 失敗：在中軌/上軌上方竟然放行開空！"

# -------------------------------------------------------------
# 測試 4：盤中黑天鵝大瀑布秒殺測試 (Flash Crash Intra-bar Exit)
# -------------------------------------------------------------
def test_retired_emergency_helper_does_not_authorize_exit(strategy):
    position = {'side': 'LONG', 'entry_price': 100.0}
    snapshot = {'open': 100.0, 'atr': 1.0}
    btc_status = {'is_crashing': False}
    
    # 盤中直接暴跌 2 ATR (現價 98.0)
    current_price = 98.0
    exit_reason = strategy.check_intra_bar_emergency_exit(position, current_price, snapshot, btc_status)
    # Actual staged flash-gap handling is exercised in test_red_eye_staged_contract.
    assert exit_reason is None

# -------------------------------------------------------------
# 測試 5：常規行情一股不賣測試 (Hold Tight Test)
# -------------------------------------------------------------
def test_hold_tight_when_normal(strategy):
    position = {'side': 'LONG', 'entry_price': 100.0, 'unrealized_pnl': 0.5}
    snapshot = {'open': 100.0, 'atr': 1.0}
    btc_status = {'is_crashing': False}
    
    # 正常小幅跳動 (現價 100.2)
    current_price = 100.2
    exit_reason = strategy.check_intra_bar_emergency_exit(position, current_price, snapshot, btc_status)
    assert exit_reason is None, "❌ 失敗：正常微小跳動竟然觸發了平倉！沒有做到一股不賣！"


def test_short_rejection_diagnostics_do_not_raise_name_error(strategy):
    current = base_bar()
    current.update(open=97.0, close=97.2, high=97.4, low=96.8,
                   ma3=97.0, ma15=98.0, kc_middle=98.0)
    previous = base_bar()
    previous.update(kc_middle=99.0, ma3=97.5)
    assert strategy.evaluate_entry('TEST/USDT', current, previous, previous) is None
    assert strategy.entry_rejection
