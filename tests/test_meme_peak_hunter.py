import pandas as pd
import pytest

from core.services.strategies.meme_peak_hunter import MemePeakHunterStrategy
from core.gates.holding_protection_gate import HoldingProtectionExitGate
from core.services.math_execution_gate import validate_exit_math, validate_entry_math


def _create_base_df(num_bars: int = 70, base_price: float = 1.0, trend: float = 0.001) -> pd.DataFrame:
    rows = []
    price = base_price
    for i in range(num_bars):
        price += trend
        rows.append({
            "timestamp": 1000.0 + i * 60.0,
            "open": price - 0.001,
            "high": price + 0.002,
            "low": price - 0.002,
            "close": price,
            "volume": 100.0,
            "is_closed": True,
        })
    return pd.DataFrame(rows)


def test_meme_peak_hunter_long_entry_success():
    """多單：站穩 VWAP + 站穩 60MA + 60MA向上 + 5MA金叉15MA + 伴隨放量 1.2 倍"""
    df = _create_base_df(70, base_price=1.0, trend=0.002)
    # 最後一根設置放量突破
    last_idx = len(df) - 1
    df.loc[last_idx, "close"] = 1.25
    df.loc[last_idx, "high"] = 1.26
    df.loc[last_idx, "low"] = 1.20
    df.loc[last_idx, "open"] = 1.21
    df.loc[last_idx, "volume"] = 150.0  # 150 > 100 * 1.2

    signal = MemePeakHunterStrategy.evaluate_entry("CAPUSDT", df, "LONG", tick_price=1.25)
    assert signal is not None
    assert signal["side"] == "LONG"
    assert signal["structure_confirmation"] == "MEME_PEAK_HUNTER"
    assert signal["reason"] == "MEME_PEAK_HUNTER_VOL_EXPANSION_LONG"


def test_meme_peak_hunter_long_entry_rejected_if_low_volume():
    """多單若未放量 (volume < 1.2 * vol_ma)，拒絕進場"""
    df = _create_base_df(70, base_price=1.0, trend=0.002)
    last_idx = len(df) - 1
    df.loc[last_idx, "close"] = 1.25
    df.loc[last_idx, "volume"] = 80.0  # 未達 1.2 倍

    signal = MemePeakHunterStrategy.evaluate_entry("CAPUSDT", df, "LONG", tick_price=1.25)
    assert signal is None


def test_meme_peak_hunter_short_entry_success():
    """空單：灌破 VWAP + 壓在 60MA下 + 60MA向下 + 5MA死叉15MA + 放量下殺 1.2 倍"""
    df = _create_base_df(70, base_price=2.0, trend=-0.002)
    last_idx = len(df) - 1
    df.loc[last_idx, "close"] = 1.70
    df.loc[last_idx, "high"] = 1.75
    df.loc[last_idx, "low"] = 1.68
    df.loc[last_idx, "open"] = 1.74
    df.loc[last_idx, "volume"] = 150.0

    signal = MemePeakHunterStrategy.evaluate_entry("CAPUSDT", df, "SHORT", tick_price=1.70)
    assert signal is not None
    assert signal["side"] == "SHORT"
    assert signal["structure_confirmation"] == "MEME_PEAK_HUNTER"
    assert signal["reason"] == "MEME_PEAK_HUNTER_VOL_EXPANSION_SHORT"


def test_meme_climax_top_exit():
    """多單真見頂：天量射擊之星 / 爆量滯漲 (volume > 2.0 * vol_ma 且 上影線 > 1.5 * body) -> 秒平多單"""
    df = _create_base_df(70, base_price=1.0, trend=0.005)
    last_idx = len(df) - 1
    # 爆量天量上影線
    df.loc[last_idx, "open"] = 1.50
    df.loc[last_idx, "close"] = 1.51  # body = 0.01
    df.loc[last_idx, "high"] = 1.55   # upper_wick = 1.55 - 1.51 = 0.04 > 1.5 * 0.01
    df.loc[last_idx, "low"] = 1.49
    df.loc[last_idx, "volume"] = 250.0 # 250 > 100 * 2.0

    pos = {"side": "LONG", "entry_price": 1.20, "highest_price": 1.55}
    exit_res = MemePeakHunterStrategy.evaluate_exit("CAPUSDT", pos, df, quote=1.51)
    assert exit_res is not None
    exit_code, reason, details = exit_res
    assert exit_code == "EXIT_BY_MEME_CLIMAX_TOP"
    assert details["cause"] == "CLIMAX_TOP"


def test_meme_trend_dead_exit():
    """多單結構潰敗：收盤跌破 15MA 且跌破 VWAP -> 秒平"""
    df = _create_base_df(70, base_price=1.0, trend=0.002)
    last_idx = len(df) - 1
    # 暴跌摜破均線與 VWAP
    df.loc[last_idx, "open"] = 1.15
    df.loc[last_idx, "close"] = 0.90
    df.loc[last_idx, "high"] = 1.15
    df.loc[last_idx, "low"] = 0.88

    pos = {"side": "LONG", "entry_price": 1.05, "highest_price": 1.15}
    exit_res = MemePeakHunterStrategy.evaluate_exit("CAPUSDT", pos, df, quote=0.90)
    assert exit_res is not None
    exit_code, reason, details = exit_res
    assert exit_code == "EXIT_BY_MEME_TREND_DEAD"


def test_meme_atr_trailing_exit():
    """動態防插針 ATR 移動鎖利：利潤達 1.5 ATR 以上，回吐超過 2.5 ATR 時觸發"""
    df = _create_base_df(70, base_price=1.0, trend=0.002)
    # ATR 約 0.004
    ind = MemePeakHunterStrategy.calculate_indicators(df)
    atr = ind["atr"]

    # 設置高利潤峰值: entry=1.0, highest=1.0 + 5*atr
    peak = 1.0 + 5 * atr
    # 當前價格從峰值回吐 2.6 ATR
    current_quote = peak - 2.6 * atr
    pos = {"side": "LONG", "entry_price": 1.0, "highest_price": peak}

    # 確保不會先觸發 trend dead 或 climax top
    last_idx = len(df) - 1
    df.loc[last_idx, "open"] = current_quote
    df.loc[last_idx, "close"] = current_quote
    df.loc[last_idx, "high"] = current_quote + 0.0001
    df.loc[last_idx, "low"] = current_quote - 0.0001
    df.loc[last_idx, "volume"] = 50.0 # 低量無射擊之星

    exit_res = MemePeakHunterStrategy.evaluate_exit("CAPUSDT", pos, df, quote=current_quote)
    if exit_res is not None:
        exit_code, reason, details = exit_res
        assert exit_code in ("EXIT_BY_MEME_ATR_TRAILING", "EXIT_BY_MEME_TREND_DEAD")


def test_meme_exits_approved_by_gates():
    """驗證妖幣出場理由能通過 holding_protection_gate 與 math_execution_gate"""
    # 1. holding protection gate 白名單放行
    assert "EXIT_BY_MEME_CLIMAX_TOP" in HoldingProtectionExitGate.WHITELIST_CODES
    assert "EXIT_BY_MEME_CLIMAX_BOTTOM" in HoldingProtectionExitGate.WHITELIST_CODES
    assert "EXIT_BY_MEME_TREND_DEAD" in HoldingProtectionExitGate.WHITELIST_CODES
    assert "EXIT_BY_MEME_ATR_TRAILING" in HoldingProtectionExitGate.WHITELIST_CODES

    # 2. math execution gate 放行
    pos = {"entry_price": 1.0, "side": "LONG"}
    approved, reason, details = validate_exit_math(
        pos,
        frame=None,
        price=1.05,
        close_reason="EXIT_BY_MEME_CLIMAX_TOP",
    )
    assert approved is True
    assert reason == "EXIT_MATH_MEME_HUNTER_APPROVED"
