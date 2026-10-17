import pandas as pd
import pytest
import time

from core.services.strategies.defensive_trend_strategy import (
    check_long_entry,
    check_short_entry,
    check_long_exit,
    check_short_exit,
)


def _make_base_df(side="SHORT", count=10):
    rows = []
    direction = -1 if side == "SHORT" else 1
    base_price = 100.0
    for i in range(count):
        mid = base_price + direction * i * 0.2
        ma15 = mid + (0.1 if side == "SHORT" else -0.1)
        ma5 = ma15 + (-0.1 if side == "SHORT" else 0.1) - (i * 0.05 * direction)
        open_p = mid + direction * 0.05
        close_p = open_p + direction * 0.1
        high_p = max(open_p, close_p) + 0.05
        low_p = min(open_p, close_p) - 0.05
        rows.append({
            "timestamp": (1700000000 + i * 60) * 1000,
            "open": open_p,
            "high": high_p,
            "low": low_p,
            "close": close_p,
            "volume": 100.0,
            "kc_upper": mid + 1.0,
            "kc_middle": mid,
            "kc_lower": mid - 1.0,
            "kc_trend": "DOWN" if side == "SHORT" else "UP",
            "ma5": ma5,
            "ma15": ma15,
            "atr": 0.5,
            "is_closed": True,
        })
    return pd.DataFrame(rows)


def test_short_entry_all_passed():
    df = _make_base_df("SHORT")
    # 確保最後一根完全符合：KC 向下、MA5 < MA15、MA5 斜率 < 0、實體陰線
    df.loc[8, ["kc_middle", "ma5", "ma15"]] = [98.2, 97.5, 98.0]
    df.loc[9, ["open", "high", "low", "close", "kc_middle", "kc_trend", "ma5", "ma15"]] = [
        97.4, 97.45, 96.8, 97.0, 98.0, "DOWN", 97.2, 97.8
    ]
    # open 97.4, close 97.0 (body 0.4, 陰線), low 96.8 (lower_wick 0.2 < 1.5 * body 0.6)
    # ma5_slope = 97.2 - 97.5 = -0.3 < 0
    passed, reason, details = check_short_entry(df, symbol="TEST/USDT", bars_since_last_exit=5)
    assert passed is True
    assert reason == "PERMIT_SHORT_ENTRY"


def test_short_entry_blocks_bullish_candle():
    """反向實體 K 線不要開空！上一根收盤必須為實體陰線"""
    df = _make_base_df("SHORT")
    df.loc[8, ["kc_middle", "ma5", "ma15"]] = [98.2, 97.5, 98.0]
    # 反向收陽線 (open 97.0, close 97.3)
    df.loc[9, ["open", "high", "low", "close", "kc_middle", "kc_trend", "ma5", "ma15"]] = [
        97.0, 97.35, 96.9, 97.3, 98.0, "DOWN", 97.2, 97.8
    ]
    passed, reason, details = check_short_entry(df, symbol="TEST/USDT")
    assert passed is False
    assert reason == "BLOCKED_SHORT_ON_BULLISH_CANDLE"


def test_short_entry_blocks_ma5_sloping_up():
    """均線斜率門禁：MA5 向上絕對不開空！必須滿足 MA5 實質向下傾斜"""
    df = _make_base_df("SHORT")
    df.loc[8, ["kc_middle", "ma5", "ma15"]] = [98.2, 97.1, 98.0]
    # MA5 向上勾頭 (97.1 -> 97.3)
    df.loc[9, ["open", "high", "low", "close", "kc_middle", "kc_trend", "ma5", "ma15"]] = [
        97.4, 97.45, 96.8, 97.0, 98.0, "DOWN", 97.3, 97.8
    ]
    passed, reason, details = check_short_entry(df, symbol="TEST/USDT")
    assert passed is False
    assert reason == "BLOCKED_SHORT_ON_MA5_SLOPE"


def test_short_entry_blocks_long_lower_wick_rebound():
    """嚴禁在長下影線 (下影線 > 實體 1.5 倍) 的反彈時開空"""
    df = _make_base_df("SHORT")
    df.loc[8, ["kc_middle", "ma5", "ma15"]] = [98.2, 97.5, 98.0]
    # open 97.2, close 97.0 (body 0.2), low 96.4 (lower_wick = 0.6 > 1.5 * 0.2 = 0.3)
    df.loc[9, ["open", "high", "low", "close", "kc_middle", "kc_trend", "ma5", "ma15"]] = [
        97.2, 97.25, 96.4, 97.0, 98.0, "DOWN", 97.2, 97.8
    ]
    passed, reason, details = check_short_entry(df, symbol="TEST/USDT")
    assert passed is False
    assert reason == "BLOCKED_SHORT_ON_REBOUND_WICK"


def test_short_entry_blocks_on_exit_cooldown():
    """冷卻機制：同一方向平倉後，至少間隔 3 根 K 棒才允許再次評估同方向進場"""
    df = _make_base_df("SHORT")
    df.loc[8, ["kc_middle", "ma5", "ma15"]] = [98.2, 97.5, 98.0]
    df.loc[9, ["open", "high", "low", "close", "kc_middle", "kc_trend", "ma5", "ma15"]] = [
        97.4, 97.45, 96.8, 97.0, 98.0, "DOWN", 97.2, 97.8
    ]
    passed, reason, details = check_short_entry(df, symbol="TEST/USDT", bars_since_last_exit=1)
    assert passed is False
    assert reason == "BLOCKED_BY_EXIT_COOLDOWN"


def test_long_entry_all_passed():
    df = _make_base_df("LONG")
    df.loc[8, ["kc_middle", "ma5", "ma15"]] = [101.8, 102.2, 101.9]
    # open 102.0, close 102.5 (body 0.5, 陽線), high 102.7 (upper_wick 0.2 < 1.5 * 0.5 = 0.75)
    # ma5_slope = 102.5 - 102.2 = +0.3 > 0, ma5 102.5 > ma15 102.0
    df.loc[9, ["open", "high", "low", "close", "kc_middle", "kc_trend", "ma5", "ma15"]] = [
        102.0, 102.7, 101.9, 102.5, 102.1, "UP", 102.5, 102.0
    ]
    passed, reason, details = check_long_entry(df, symbol="TEST/USDT", bars_since_last_exit=4)
    assert passed is True
    assert reason == "PERMIT_LONG_ENTRY"


def test_long_entry_blocks_bearish_candle_or_exhaustion_wick():
    df = _make_base_df("LONG")
    df.loc[8, ["kc_middle", "ma5", "ma15"]] = [101.8, 102.2, 101.9]
    # 收陰線
    df.loc[9, ["open", "high", "low", "close", "kc_middle", "kc_trend", "ma5", "ma15"]] = [
        102.5, 102.7, 101.9, 102.2, 102.1, "UP", 102.5, 102.0
    ]
    passed, reason, details = check_long_entry(df, symbol="TEST/USDT")
    assert passed is False
    assert reason == "BLOCKED_LONG_ON_BEARISH_CANDLE"


def test_short_exit_holds_on_healthy_pullback():
    """空單持倉時，若價格僅是正常回踩 MA5 或 MA15，但收盤價尚未實質站穩 MA15 上方且未形成均線金叉，強制 HOLD"""
    df = _make_base_df("SHORT")
    # 設置情境：價格微幅回踩到 MA5 97.4，但仍低於 MA15 97.8，且 MA5 < MA15 (無金叉)
    df.loc[8, ["ma5", "ma15", "close"]] = [97.3, 97.9, 97.0]
    df.loc[9, ["ma5", "ma15", "close", "kc_middle", "kc_trend"]] = [97.4, 97.8, 97.4, 98.0, "DOWN"]

    pos = {
        "symbol": "TEST/USDT",
        "side": "SHORT",
        "entry_price": 98.0,
        "lowest_price": 97.0,
    }
    # 當前價 97.4 (仍在持倉安全區，利潤有回吐但未達 0.8% 的 1.2% 高點回撤)
    should_exit, reason, details = check_short_exit(pos, df, quote=97.4)
    assert should_exit is False
    assert "HOLD: HEALTHY_PULLBACK_TOLERANCE" in reason


def test_short_exit_on_kc_trend_reversal():
    """趨勢逆轉強制全平：若 kc_trend 翻為 'UP'，立即市價全平空單"""
    df = _make_base_df("SHORT")
    df.loc[8, "kc_middle"] = 98.0
    df.loc[9, ["kc_middle", "kc_trend"]] = [98.5, "UP"]
    pos = {
        "symbol": "TEST/USDT",
        "side": "SHORT",
        "entry_price": 98.0,
    }
    should_exit, reason, details = check_short_exit(pos, df, quote=98.1)
    assert should_exit is True
    assert reason == "EXIT_BY_KC_TREND_REVERSAL"


def test_short_exit_on_two_bars_above_ma15():
    """均線破線離場：空單連續 2 根收盤站上 MA15，立即平空"""
    df = _make_base_df("SHORT")
    df.loc[8, ["ma15", "close"]] = [97.5, 97.8]
    df.loc[9, ["ma15", "close"]] = [97.5, 97.9]
    pos = {
        "symbol": "TEST/USDT",
        "side": "SHORT",
        "entry_price": 98.0,
    }
    should_exit, reason, details = check_short_exit(pos, df, quote=97.9)
    assert should_exit is True
    assert reason == "EXIT_BY_MA15_BREAKOUT"


def test_short_exit_on_golden_cross():
    """均線金叉離場：MA5 向上突破 MA15 (金叉) 立即平空"""
    df = _make_base_df("SHORT")
    df.loc[8, ["ma5", "ma15"]] = [97.4, 97.5]
    df.loc[9, ["ma5", "ma15"]] = [97.7, 97.6]
    pos = {
        "symbol": "TEST/USDT",
        "side": "SHORT",
        "entry_price": 98.0,
    }
    should_exit, reason, details = check_short_exit(pos, df, quote=97.7)
    assert should_exit is True
    assert reason == "EXIT_BY_MA_GOLDEN_CROSS"


def test_short_exit_on_trailing_profit_lock():
    """動態回撤止盈：達到浮盈高點後回撤超過門檻，立即止盈"""
    df = _make_base_df("SHORT")
    pos = {
        "symbol": "TEST/USDT",
        "side": "SHORT",
        "entry_price": 100.0,
        "lowest_price": 98.0,  # 浮盈一度達 +2.0%
    }
    # 現價反彈至 99.0 (回撤 1.0% >= 0.8%)
    should_exit, reason, details = check_short_exit(pos, df, quote=99.0)
    assert should_exit is True
    assert reason == "EXIT_BY_TRAILING_PROFIT_LOCK"
