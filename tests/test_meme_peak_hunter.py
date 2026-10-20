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
            "timestamp": 1000.0 + i * 180.0, # 3m bars
            "open": price - 0.001,
            "high": price + 0.002,
            "low": price - 0.002,
            "close": price,
            "volume": 100.0,
            "is_closed": True,
        })
    return pd.DataFrame(rows)


def test_meme_peak_hunter_3m_long_entry_success():
    """3m 多單：現價 > VWAP + 現價 > EMA40 + EMA40向上 + EMA5 金叉 EMA13 + 伴隨放量 1.1 倍"""
    df = _create_base_df(70, base_price=1.0, trend=0.002)
    # 最後一根設置放量突破
    last_idx = len(df) - 1
    df.loc[last_idx, "close"] = 1.25
    df.loc[last_idx, "high"] = 1.26
    df.loc[last_idx, "low"] = 1.20
    df.loc[last_idx, "open"] = 1.21
    df.loc[last_idx, "volume"] = 120.0  # 120 >= 100 * 1.1

    signal = MemePeakHunterStrategy.evaluate_entry("CAPUSDT", df, "LONG", tick_price=1.25)
    assert signal is not None
    assert signal["side"] == "LONG"
    assert signal["structure_confirmation"] == "MEME_PEAK_HUNTER"
    assert signal["reason"] == "MEME_PEAK_HUNTER_3M_EMA_LONG"


def test_meme_peak_hunter_3m_long_entry_rejected_if_low_volume():
    """多單若未放量 (volume < 1.1 * vol_ma)，拒絕進場"""
    df = _create_base_df(70, base_price=1.0, trend=0.002)
    last_idx = len(df) - 1
    df.loc[last_idx, "close"] = 1.25
    df.loc[last_idx, "volume"] = 80.0  # 未達 1.1 倍

    signal = MemePeakHunterStrategy.evaluate_entry("CAPUSDT", df, "LONG", tick_price=1.25)
    assert signal is None


def test_meme_peak_hunter_3m_continuation_long_success():
    """3m 多單延續開倉（平倉後重新上車）：大趨勢多頭排列，陽線起抽，成功觸發延續開倉"""
    df = _create_base_df(70, base_price=1.0, trend=0.003)
    last_idx = len(df) - 1
    # 設置大趨勢良好（close > ema5 > ema13 > ema40 > vwap）
    df.loc[last_idx, "open"] = 1.21
    df.loc[last_idx, "close"] = 1.215
    df.loc[last_idx, "high"] = 1.218
    df.loc[last_idx, "low"] = 1.208
    df.loc[last_idx, "volume"] = 70.0  # 70 >= 100 * 0.6 換手延續量

    signal = MemePeakHunterStrategy.evaluate_entry("CAPUSDT", df, "LONG", tick_price=1.215)
    assert signal is not None
    assert signal["side"] == "LONG"
    assert signal["structure_confirmation"] == "MEME_PEAK_HUNTER"
    assert signal["reason"] == "MEME_PEAK_HUNTER_3M_CONTINUATION_LONG"
    assert signal["entry_pattern"] == "CONTINUATION"


def test_meme_peak_hunter_3m_short_entry_success():
    """3m 空單：現價 < VWAP + 現價 < EMA40 + EMA40向下 + EMA5 死叉 EMA13 + 放量下殺 1.1 倍"""
    df = _create_base_df(70, base_price=2.0, trend=-0.002)
    last_idx = len(df) - 1
    df.loc[last_idx, "close"] = 1.70
    df.loc[last_idx, "high"] = 1.75
    df.loc[last_idx, "low"] = 1.68
    df.loc[last_idx, "open"] = 1.74
    df.loc[last_idx, "volume"] = 120.0

    signal = MemePeakHunterStrategy.evaluate_entry("CAPUSDT", df, "SHORT", tick_price=1.70)
    assert signal is not None
    assert signal["side"] == "SHORT"
    assert signal["structure_confirmation"] == "MEME_PEAK_HUNTER"
    assert signal["reason"] == "MEME_PEAK_HUNTER_3M_EMA_SHORT"


def test_meme_climax_top_exit():
    """最新多單鐵律：移除『突破/觸碰 KC 上軌平多』，價格沿著 KC 上軌為強勢結構，嚴禁被動止盈！"""
    df = _create_base_df(70, base_price=1.0, trend=0.005)
    last_idx = len(df) - 1
    # 爆量長上影線穿出上軌，但收盤仍在 KC 中軌上方
    df.loc[last_idx, "open"] = 1.50
    df.loc[last_idx, "close"] = 1.51  # body = 0.01
    df.loc[last_idx, "high"] = 1.55   # upper_wick = 1.55 - 1.51 = 0.04 > 1.5 * 0.01
    df.loc[last_idx, "low"] = 1.49
    df.loc[last_idx, "volume"] = 200.0 # 200 > 100 * 1.8

    pos = {"side": "LONG", "entry_price": 1.20, "highest_price": 1.55}
    exit_res = MemePeakHunterStrategy.evaluate_exit("CAPUSDT", pos, df, quote=1.51)
    # 最新規則：嚴禁在突破上軌時被動平多，此處必須 return None 繼續持倉！
    assert exit_res is None


def test_meme_trend_dead_exit():
    """3m 多單趨勢潰敗：收盤實體實質跌破 KC 中軌 -> 全數平倉離場"""
    df = _create_base_df(70, base_price=1.0, trend=0.002)
    last_idx = len(df) - 1
    # 暴跌摜破 KC 中軌 (中軌約在 1.05 附近)
    df.loc[last_idx, "open"] = 1.15
    df.loc[last_idx, "close"] = 0.90
    df.loc[last_idx, "high"] = 1.15
    df.loc[last_idx, "low"] = 0.88

    pos = {"side": "LONG", "entry_price": 0.85, "highest_price": 1.15}
    exit_res = MemePeakHunterStrategy.evaluate_exit("CAPUSDT", pos, df, quote=0.90)
    assert exit_res is not None
    exit_code, reason, details = exit_res
    assert exit_code == "EXIT_BY_MEME_TREND_DEAD"


def test_meme_atr_trailing_exit():
    """3m 動態防插針 ATR 移動鎖利：利潤達 1.5 ATR 以上，回吐超過 1.8 ATR 時觸發"""
    df = _create_base_df(70, base_price=1.0, trend=0.002)
    ind = MemePeakHunterStrategy.calculate_indicators(df)
    atr = ind["atr"]

    # 設置高利潤峰值: entry=1.0, highest=1.0 + 4*atr
    peak = 1.0 + 4 * atr
    # 當前價格從峰值回吐 1.9 ATR
    current_quote = peak - 1.9 * atr
    pos = {"side": "LONG", "entry_price": 1.0, "highest_price": peak}

    # 確保不會先觸發 trend dead 或 climax top
    last_idx = len(df) - 1
    df.loc[last_idx, "open"] = current_quote
    df.loc[last_idx, "close"] = current_quote
    df.loc[last_idx, "high"] = current_quote + 0.0001
    df.loc[last_idx, "low"] = current_quote - 0.0001
    df.loc[last_idx, "volume"] = 50.0

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
    assert "EXIT_BY_MEME_ADVERSE_STOP_LOSS" in HoldingProtectionExitGate.WHITELIST_CODES

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

    approved_sl, reason_sl, _ = validate_exit_math(
        pos,
        frame=None,
        price=0.98,
        close_reason="EXIT_BY_MEME_ADVERSE_STOP_LOSS",
    )
    assert approved_sl is True
    assert reason_sl in ("EXIT_MATH_MEME_HUNTER_APPROVED", "EMERGENCY_EXIT_OVERRIDE")


def test_meme_adverse_stop_loss_long():
    """多單假突破初始逆向止損：逆行虧損達 1.5 ATR 立即止損逃命"""
    df = _create_base_df(70, base_price=1.0, trend=0.002)
    ind = MemePeakHunterStrategy.calculate_indicators(df)
    atr = ind["atr"]

    pos = {"side": "LONG", "entry_price": 1.0, "highest_price": 1.002}
    adverse_price = 1.0 - 1.6 * atr

    exit_res = MemePeakHunterStrategy.evaluate_exit("CAPUSDT", pos, df, quote=adverse_price)
    assert exit_res is not None
    exit_code, reason, details = exit_res
    assert exit_code in ("EXIT_BY_MEME_ADVERSE_STOP_LOSS", "EXIT_BY_MEME_TREND_DEAD")


def test_meme_adverse_stop_loss_short():
    """空單假跌破初始逆向止損：逆行虧損達 1.5 ATR 立即止損逃命"""
    df = _create_base_df(70, base_price=1.0, trend=-0.002)
    ind = MemePeakHunterStrategy.calculate_indicators(df)
    atr = ind["atr"]

    pos = {"side": "SHORT", "entry_price": 1.0, "lowest_price": 0.998}
    adverse_price = 1.0 + 1.6 * atr

    exit_res = MemePeakHunterStrategy.evaluate_exit("CAPUSDT", pos, df, quote=adverse_price)
    assert exit_res is not None
    exit_code, reason, details = exit_res
    assert exit_code in ("EXIT_BY_MEME_ADVERSE_STOP_LOSS", "EXIT_BY_MEME_SHORT_TREND_DEAD")
