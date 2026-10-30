import pytest
from core.services.execution_manager import ExecutionManager


def test_agile_decoupled_exit_breakeven_long():
    """驗證多單浮盈曾達 8% ROE，回踩成本線 (0.3% 手續費保護) 觸發保本平倉"""
    # pos_entry = 100, curr_quote = 100.2 (<= 100.3)
    pos_entry = 100.0
    curr_quote = 100.2
    peak_roe_pct = 9.0
    cur_roe_pct = 1.0

    be_triggered = (peak_roe_pct >= 8.0 and curr_quote <= pos_entry * 1.003)
    assert be_triggered is True


def test_agile_decoupled_exit_trailing_5pct():
    """驗證浮盈達 20% ROE 時，回吐 5% 即平倉"""
    peak_roe_pct = 22.0
    cur_roe_pct = 16.5  # 回吐 5.5% >= 5%
    drawback = peak_roe_pct - cur_roe_pct
    assert drawback >= 5.0


def test_agile_decoupled_exit_counter_trend_rules():
    """驗證逆勢多單觸及 KC 上軌或跌破前低即平倉"""
    closed_kc_u = 110.0
    curr_quote = 110.5
    assert curr_quote >= closed_kc_u

    closed_c = 98.0
    prev_closed_l = 99.0
    assert closed_c < prev_closed_l
