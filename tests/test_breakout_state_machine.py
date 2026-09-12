"""CK 通道突破確認與延遲開倉狀態機（使用者 2026-09-14 最終規格）的回歸測試。"""
from core.services.strategies.breakout_state_machine import Bar, BreakoutState, on_bar_closed

UP, DN = 100.5, 99.0


def bar(o, h, l, c):
    return Bar(open=o, high=h, low=l, close=c, kc_upper=UP, kc_lower=DN)


def test_state_machine_full_cycle():
    state = BreakoutState()
    # 1) 多頭突破第 1 根（強實體陽線收在上軌外）→ 只記錄，不開倉
    assert on_bar_closed(state, bar(100, 101.05, 99.95, 101.0)) is None
    assert state.pending_breakout == "LONG"
    # 2) 弱體陽線 → 順延等待
    assert on_bar_closed(state, bar(101.0, 101.2, 100.9, 101.05)) is None
    assert state.pending_breakout == "LONG"
    # 3) 強實體陽線 → 開多
    assert on_bar_closed(state, bar(101.05, 102.05, 101.0, 102.0)) == "OPEN_LONG"
    # 4) 持多遇空頭強突破 → 先平多，並把該根當作做空的第 1 根
    assert on_bar_closed(state, bar(102.0, 102.1, 98.5, 98.8)) == "CLOSE_LONG"
    assert state.pending_breakout == "SHORT"
    # 5) 陽線 → 空頭信號作廢
    assert on_bar_closed(state, bar(98.8, 99.2, 98.6, 99.1)) is None
    assert state.pending_breakout is None
    # 6) 空頭突破 → 弱體陰線順延 → 強實體陰線開空
    assert on_bar_closed(state, bar(99.1, 99.2, 98.4, 98.5)) is None
    assert state.pending_breakout == "SHORT"
    assert on_bar_closed(state, bar(98.5, 98.7, 98.3, 98.45)) is None
    assert state.pending_breakout == "SHORT"
    assert on_bar_closed(state, bar(98.45, 98.6, 97.8, 97.9)) == "OPEN_SHORT"


def test_weak_body_threshold_parameterised():
    state = BreakoutState()
    # 實體佔比 0.4：預設門檻 0.5 下屬弱體 → 不開；門檻放寬到 0.3 則算強實體 → 開倉
    weak = bar(100, 101.0, 99.8, 100.4)  # 實體 0.4 / 全長 1.2 = 0.333
    assert on_bar_closed(BreakoutState(), weak, 0.5) is None
    assert on_bar_closed(state, bar(100, 101.05, 99.95, 101.0), 0.3) is None
    assert state.pending_breakout == "LONG"
    assert on_bar_closed(state, weak, 0.3) == "OPEN_LONG"
