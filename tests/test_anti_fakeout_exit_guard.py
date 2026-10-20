import pytest
import pandas as pd

from core.services.exits.anti_fakeout_exit_guard import (
    check_exit_with_anti_fakeout,
    AntiFakeoutExitGuard,
)


def test_long_pullback_hold():
    """多單防護 A：回踩有守，收盤仍在 EMA13 之上，強制不平倉"""
    candle = {"open": 100.0, "high": 101.0, "low": 99.8, "close": 100.2}
    kc = {"mid": 99.5, "upper": 102.0, "lower": 97.0}
    ema5 = 100.3
    ema13 = 100.0
    position = {"side": "long", "entry_price": 99.0, "amount": 10.0, "max_profit": 1.5}

    action, reason = check_exit_with_anti_fakeout(candle, kc, ema5, ema13, position)
    assert action == "HOLD"
    assert "多頭回踩有守" in reason


def test_long_fakeout_wick_and_buffer_hold():
    """多單防護 B：盤中下影線跌破但收盤在 KC 中軌緩衝帶內（未達 0.15% 跌幅），判定為假跌破震盪，維持持倉"""
    ema13 = 100.0
    # KC 中軌 = 100.0, 0.15% 緩衝帶 = 100.0 * (1 - 0.0015) = 99.85
    # 收盤 99.90 (僅微破 0.10% < 0.15%)
    candle = {"open": 100.1, "high": 100.2, "low": 99.5, "close": 99.90}
    kc = {"mid": 100.0, "upper": 102.0, "lower": 97.0}
    ema5 = 99.95
    position = {"side": "long", "entry_price": 99.0, "amount": 10.0, "max_profit": 1.0}

    action, reason = check_exit_with_anti_fakeout(candle, kc, ema5, ema13, position)
    assert action == "HOLD"
    assert "疑似假跌破" in reason


def test_long_structural_break_close():
    """多單真正平倉：收盤實體明確跌破 KC 中軌且超過 0.15% 緩衝帶，多頭結構瓦解"""
    ema13 = 100.0
    # KC 中軌 = 100.0, 收盤 99.80 (< 99.85 緩衝防守線)
    candle = {"open": 99.9, "high": 100.0, "low": 99.7, "close": 99.80}
    kc = {"mid": 100.0, "upper": 102.0, "lower": 97.0}
    ema5 = 99.82
    position = {"side": "long", "entry_price": 99.0, "amount": 10.0, "max_profit": 1.0}

    action, reason = check_exit_with_anti_fakeout(candle, kc, ema5, ema13, position)
    assert action == "CLOSE_LONG"
    assert "實質跌破離場" in reason


def test_short_pullback_hold():
    """空單防護 A：反彈遇阻，收盤仍在 EMA13 之下，強制不平倉"""
    candle = {"open": 99.8, "high": 100.2, "low": 99.0, "close": 99.7}
    kc = {"mid": 100.5, "upper": 103.0, "lower": 98.0}
    ema5 = 99.6
    ema13 = 100.0
    position = {"side": "short", "entry_price": 101.0, "amount": 10.0, "max_profit": 1.5}

    action, reason = check_exit_with_anti_fakeout(candle, kc, ema5, ema13, position)
    assert action == "HOLD"
    assert "空頭反彈遇阻" in reason


def test_short_fakeout_wick_and_buffer_hold():
    """空單防護 B：盤中上影線上衝但收盤在緩衝帶內（未達 0.15% 漲幅），判定為假突破誘多，維持持倉"""
    ema13 = 100.0
    # 0.15% 緩衝帶 = 100.0 * (1 + 0.0015) = 100.15
    # 收盤 100.10 (僅微破 0.10% < 0.15%)
    candle = {"open": 99.9, "high": 100.5, "low": 99.8, "close": 100.10}
    kc = {"mid": 100.5, "upper": 103.0, "lower": 98.0}
    ema5 = 100.05
    position = {"side": "short", "entry_price": 101.0, "amount": 10.0, "max_profit": 1.0}

    action, reason = check_exit_with_anti_fakeout(candle, kc, ema5, ema13, position)
    assert action == "HOLD"
    assert "疑似假突破" in reason


def test_short_structural_break_close():
    """空單真正平倉：收盤實體明確衝破 EMA13 且超過 0.15% 緩衝帶，空頭結構瓦解"""
    ema13 = 100.0
    # 收盤 100.20 (> 100.15 緩衝防守線)
    candle = {"open": 100.1, "high": 100.3, "low": 100.0, "close": 100.20}
    kc = {"mid": 100.5, "upper": 103.0, "lower": 98.0}
    ema5 = 100.15
    position = {"side": "short", "entry_price": 101.0, "amount": 10.0, "max_profit": 1.0}

    action, reason = check_exit_with_anti_fakeout(candle, kc, ema5, ema13, position)
    assert action == "CLOSE_SHORT"
    assert "實質突破離場" in reason


def test_extreme_profit_dynamic_trailing_lock():
    """極端暴利鎖利：浮盈曾達 5U 以上，動態回撤至 <= 70% 立即鎖利平倉"""
    candle = {"open": 100.0, "high": 100.5, "low": 99.8, "close": 100.0}
    kc = {"mid": 100.0, "upper": 102.0, "lower": 98.0}
    ema5 = 100.0
    ema13 = 100.0
    # 峰值利潤 6.0U，當前利潤回撤至 4.0U (< 6.0 * 0.70 = 4.2U)
    position = {"side": "long", "entry_price": 95.0, "amount": 1.0, "max_profit": 6.0}

    action, reason = check_exit_with_anti_fakeout(
        candle, kc, ema5, ema13, position, current_pnl_usdt=4.0
    )
    assert action == "CLOSE_POSITION"
    assert "動態回撤鎖利" in reason


def test_anti_fakeout_guard_evaluate_class():
    """測試 AntiFakeoutExitGuard.evaluate 整合流程"""
    df = pd.DataFrame([
        {"close": 100.2, "low": 99.8, "high": 101.0, "ema13": 100.0, "ema5": 100.3, "is_closed": True},
    ])
    position = {"side": "long", "entry_price": 99.0, "amount": 10.0}
    should_exit, reason, details = AntiFakeoutExitGuard.evaluate(position, df)
    assert not should_exit
    assert "多頭回踩有守" in reason
