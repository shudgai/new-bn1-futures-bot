import pandas as pd
import pytest

from core.services.key_level_dip_sniper import KeyLevelDipSniperService


def _create_frame(
    open_p: float, high_p: float, low_p: float, close_p: float,
    ma5: float, prev_ma5: float, ma15: float, prev_ma15: float,
    kc_mid: float, prev_kc_mid: float, atr: float = 0.0005
) -> pd.DataFrame:
    records = [
        {
            "timestamp": 1000.0,
            "open": open_p, "high": high_p, "low": low_p, "close": close_p,
            "ma5": prev_ma5, "ma15": prev_ma15, "kc_middle": prev_kc_mid,
            "atr": atr, "is_closed": True,
        },
        {
            "timestamp": 2000.0,
            "open": open_p, "high": high_p, "low": low_p, "close": close_p,
            "ma5": prev_ma5, "ma15": prev_ma15, "kc_middle": prev_kc_mid,
            "atr": atr, "is_closed": True,
        },
        {
            "timestamp": 3000.0,
            "open": open_p, "high": high_p, "low": low_p, "close": close_p,
            "ma5": ma5, "ma15": ma15, "kc_middle": kc_mid,
            "atr": atr, "is_closed": False,
        },
    ]
    return pd.DataFrame(records)


def test_dip_sniper_long_success():
    """多單回踩 MA5/KC中軌有守，位於K線低檔區 (pos_ratio <= 40%) 且起抽收陽，成功發出訊號"""
    # 趨勢向上：kc_mid 0.0700 -> 0.0702, ma15 0.0698 -> 0.0700, ma5 0.0701 -> 0.0703
    # 當前棒：open=0.07015, low=0.07000 (回踩到 kc_mid/ma5), high=0.07050
    # tick_price=0.07018 (pos_ratio = (0.07018 - 0.07000) / 0.00050 = 36% <= 40%)
    # 現價 >= open (0.07018 >= 0.07015)，且下影線 = 0.07015 - 0.07000 = 0.00015 (>= 0.15*atr)
    frame = _create_frame(
        open_p=0.07015, high_p=0.07050, low_p=0.07000, close_p=0.07018,
        ma5=0.07030, prev_ma5=0.07010,
        ma15=0.07000, prev_ma15=0.06980,
        kc_mid=0.07020, prev_kc_mid=0.07000,
        atr=0.00050,
    )
    signal = KeyLevelDipSniperService.evaluate_entry("CAP/USDT", frame, "LONG", tick_price=0.07018)
    assert signal is not None
    assert signal["side"] == "LONG"
    assert signal["structure_confirmation"] == "DIP_SNIPER"
    assert signal["entry_pattern"] == "DIP_SNIPER"
    assert signal["pos_ratio"] <= 0.40
    assert signal["stop_loss"] < 0.07000


def test_dip_sniper_long_rejects_chase():
    """多單若追高 (pos_ratio > 40%)，即使回踩過，也嚴格禁止追價進場"""
    # tick_price 漲到 0.07035 (pos_ratio = 70% > 40%)
    frame = _create_frame(
        open_p=0.07015, high_p=0.07050, low_p=0.07000, close_p=0.07035,
        ma5=0.07030, prev_ma5=0.07010,
        ma15=0.07000, prev_ma15=0.06980,
        kc_mid=0.07020, prev_kc_mid=0.07000,
        atr=0.00050,
    )
    signal = KeyLevelDipSniperService.evaluate_entry("CAP/USDT", frame, "LONG", tick_price=0.07035)
    assert signal is None


def test_dip_sniper_long_rejects_on_bearish_trend():
    """順勢鐵律：空頭趨勢 (KC中軌下行) 嚴禁開多，即使回踩打針也一票否決"""
    # kc_mid 下行：0.0705 -> 0.0700
    frame = _create_frame(
        open_p=0.07015, high_p=0.07050, low_p=0.07000, close_p=0.07018,
        ma5=0.07030, prev_ma5=0.07010,
        ma15=0.07000, prev_ma15=0.06980,
        kc_mid=0.07000, prev_kc_mid=0.07050,  # 下行！
        atr=0.00050,
    )
    signal = KeyLevelDipSniperService.evaluate_entry("CAP/USDT", frame, "LONG", tick_price=0.07018)
    assert signal is None


def test_dip_sniper_short_success():
    """空單反彈摸高遇阻 MA5/KC中軌，位於K線高檔區 (pos_ratio >= 60%) 且回落收陰，成功發出訊號"""
    # 趨勢向下：kc_mid 0.0705 -> 0.0700, ma15 0.0708 -> 0.0702, ma5 0.0704 -> 0.0699
    # 當前棒：open=0.07010, high=0.07035 (摸到 kc_mid/ma5), low=0.06980 (總長 0.00055)
    # tick_price=0.07005 (pos_ratio = (0.07005 - 0.06980) / 0.00055 = 45.4%... 調整為 65%)
    # 取 low=0.06950, high=0.07030, open=0.07010, tick_price=0.07005
    # range = 0.00080, pos_ratio = (0.07005 - 0.06950) / 0.00080 = 68.75% >= 60%
    # 現價 <= open (0.07005 <= 0.07010)，且上影線 = 0.07030 - 0.07010 = 0.00020 (>= 0.15*atr)
    frame = _create_frame(
        open_p=0.07010, high_p=0.07030, low_p=0.06950, close_p=0.07005,
        ma5=0.06990, prev_ma5=0.07040,
        ma15=0.07020, prev_ma15=0.07080,
        kc_mid=0.07000, prev_kc_mid=0.07050,
        atr=0.00050,
    )
    signal = KeyLevelDipSniperService.evaluate_entry("CAP/USDT", frame, "SHORT", tick_price=0.07005)
    assert signal is not None
    assert signal["side"] == "SHORT"
    assert signal["structure_confirmation"] == "DIP_SNIPER"
    assert signal["entry_pattern"] == "DIP_SNIPER"
    assert signal["pos_ratio"] >= 0.60
    assert signal["stop_loss"] > 0.07030


def test_dip_sniper_short_rejects_chase():
    """空單若殺跌 (pos_ratio < 60%)，即使摸過高點，也嚴格禁止在地板追空"""
    # tick_price 跌到 0.06960 (pos_ratio = (0.06960 - 0.06950) / 0.00080 = 12.5% < 60%)
    frame = _create_frame(
        open_p=0.07010, high_p=0.07030, low_p=0.06950, close_p=0.06960,
        ma5=0.06990, prev_ma5=0.07040,
        ma15=0.07020, prev_ma15=0.07080,
        kc_mid=0.07000, prev_kc_mid=0.07050,
        atr=0.00050,
    )
    signal = KeyLevelDipSniperService.evaluate_entry("CAP/USDT", frame, "SHORT", tick_price=0.06960)
    assert signal is None


def test_dip_sniper_short_rejects_on_bullish_trend():
    """順勢鐵律：多頭趨勢 (KC中軌上行) 嚴禁摸頂開空，一票否決"""
    # kc_mid 上行：0.0700 -> 0.0705
    frame = _create_frame(
        open_p=0.07010, high_p=0.07030, low_p=0.06950, close_p=0.07005,
        ma5=0.06990, prev_ma5=0.07040,
        ma15=0.07020, prev_ma15=0.07080,
        kc_mid=0.07050, prev_kc_mid=0.07000,  # 上行！
        atr=0.00050,
    )
    signal = KeyLevelDipSniperService.evaluate_entry("CAP/USDT", frame, "SHORT", tick_price=0.07005)
    assert signal is None
