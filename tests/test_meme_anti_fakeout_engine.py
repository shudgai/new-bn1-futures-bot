"""
Unit tests for Meme Coin Anti-Fakeout Engine (妖幣專用 3 大風控與籌碼濾網)
1. 動態 ATR 波動度與插針過濾器 (ATR Dynamics & Pinbar Filter)
2. 槓桿與籌碼情緒濾網 (Funding Rate & OI Filter)
3. 大趨勢錨定與右側確認 (EMA 200 Anchor Filter)
"""

import pytest
from core.services.execution_manager import (
    is_meme_trade_valid,
    evaluate_meme_trade_valid,
)


def test_meme_trade_valid_success_buy_and_sell():
    """驗證正常放量順勢突破，且籌碼情緒正常時，多空皆放行通過"""
    # 🟢 正常做多
    curr_k_buy = {
        "open": 100.0,
        "close": 102.5,  # body = 2.5
        "high": 102.8,
        "low": 99.8,
        "volume": 5000.0,
    }
    valid_buy, reason_buy = evaluate_meme_trade_valid(
        side="BUY",
        curr_k=curr_k_buy,
        ma3_curr=101.5,
        ma15_curr=100.2,
        ema200_curr=95.0,     # close > ema200
        current_atr=1.5,      # 1.2 * atr = 1.8, body=2.5 >= 1.8
        volume_ma20=3000.0,   # volume=5000 > 3000
        funding_rate=0.0005,  # 0.05% <= 0.1%
        oi_change_pct=0.05,   # +5% > 0
    )
    assert valid_buy is True
    assert reason_buy == "MEME_TRADE_VALID_PASS"
    assert is_meme_trade_valid("BUY", curr_k_buy, 101.5, 100.2, 95.0, 1.5, 3000.0, 0.0005, 0.05) is True

    # 🔴 正常做空
    curr_k_sell = {
        "open": 90.0,
        "close": 87.5,  # body = 2.5
        "high": 90.2,
        "low": 87.3,
        "volume": 5000.0,
    }
    valid_sell, reason_sell = evaluate_meme_trade_valid(
        side="SELL",
        curr_k=curr_k_sell,
        ma3_curr=88.5,
        ma15_curr=89.8,
        ema200_curr=95.0,      # close < ema200
        current_atr=1.5,       # 1.2 * atr = 1.8, body=2.5 >= 1.8
        volume_ma20=3000.0,    # volume=5000 > 3000
        funding_rate=-0.0005,  # -0.05% >= -0.1%
        oi_change_pct=0.05,    # +5% > 0
    )
    assert valid_sell is True
    assert reason_sell == "MEME_TRADE_VALID_PASS"
    assert is_meme_trade_valid("SELL", curr_k_sell, 88.5, 89.8, 95.0, 1.5, 3000.0, -0.0005, 0.05) is True


def test_ema200_anchor_filter():
    """驗證大趨勢錨定：價格在 EMA200 下方禁止做多，在 EMA200 上方禁止做空"""
    curr_k_low = {"open": 90.0, "close": 92.0, "volume": 5000.0}
    # 價格 92.0 < EMA200 (100.0)，禁止做多逆勢摸底
    valid, reason = evaluate_meme_trade_valid(
        side="BUY",
        curr_k=curr_k_low,
        ma3_curr=91.0,
        ma15_curr=90.0,
        ema200_curr=100.0,
        current_atr=1.0,
        volume_ma20=2000.0,
        funding_rate=0.0001,
        oi_change_pct=0.02,
    )
    assert valid is False
    assert "PRICE_BELOW_EMA200" in reason

    curr_k_high = {"open": 110.0, "close": 108.0, "volume": 5000.0}
    # 價格 108.0 > EMA200 (100.0)，禁止做空摸頂
    valid_short, reason_short = evaluate_meme_trade_valid(
        side="SELL",
        curr_k=curr_k_high,
        ma3_curr=109.0,
        ma15_curr=110.0,
        ema200_curr=100.0,
        current_atr=1.0,
        volume_ma20=2000.0,
        funding_rate=-0.0001,
        oi_change_pct=0.02,
    )
    assert valid_short is False
    assert "PRICE_ABOVE_EMA200" in reason_short


def test_funding_rate_filter():
    """驗證資金費率極端過熱/過冷避險"""
    curr_k = {"open": 100.0, "close": 103.0, "volume": 5000.0}

    # 多頭極端過熱 (funding_rate > 0.001，即 > 0.1%)
    valid_buy, reason_buy = evaluate_meme_trade_valid(
        side="BUY",
        curr_k=curr_k,
        ma3_curr=102.0,
        ma15_curr=101.0,
        ema200_curr=95.0,
        current_atr=1.0,
        volume_ma20=2000.0,
        funding_rate=0.0015,  # 0.15% 極端多頭過熱
        oi_change_pct=0.03,
    )
    assert valid_buy is False
    assert "OVERHEATED_FUNDING_RATE" in reason_buy

    # 空頭極端過冷 (funding_rate < -0.001，即 < -0.1%)
    curr_k_down = {"open": 90.0, "close": 87.0, "volume": 5000.0}
    valid_sell, reason_sell = evaluate_meme_trade_valid(
        side="SELL",
        curr_k=curr_k_down,
        ma3_curr=88.0,
        ma15_curr=89.0,
        ema200_curr=95.0,
        current_atr=1.0,
        volume_ma20=2000.0,
        funding_rate=-0.0015,  # -0.15% 極端空頭過冷
        oi_change_pct=0.03,
    )
    assert valid_sell is False
    assert "OVERCOOLED_FUNDING_RATE" in reason_sell


def test_open_interest_oi_filter():
    """驗證持倉量 (OI) 濾網：價格上漲但 OI 未增加視為誘多假突破，價格下跌但 OI 未增加視為誘空"""
    curr_k_buy = {"open": 100.0, "close": 103.0, "volume": 5000.0}

    # OI 持平或減少 (oi_change_pct <= 0)
    valid_buy, reason_buy = evaluate_meme_trade_valid(
        side="BUY",
        curr_k=curr_k_buy,
        ma3_curr=102.0,
        ma15_curr=101.0,
        ema200_curr=95.0,
        current_atr=1.0,
        volume_ma20=2000.0,
        funding_rate=0.0002,
        oi_change_pct=0.0,  # OI 未增加
    )
    assert valid_buy is False
    assert "NON_EXPANDING_OI_BULL_TRAP" in reason_buy

    curr_k_sell = {"open": 90.0, "close": 87.0, "volume": 5000.0}
    valid_sell, reason_sell = evaluate_meme_trade_valid(
        side="SELL",
        curr_k=curr_k_sell,
        ma3_curr=88.0,
        ma15_curr=89.0,
        ema200_curr=95.0,
        current_atr=1.0,
        volume_ma20=2000.0,
        funding_rate=-0.0002,
        oi_change_pct=-0.02,  # OI 減少
    )
    assert valid_sell is False
    assert "NON_EXPANDING_OI_BEAR_TRAP" in reason_sell


def test_atr_body_dynamics_and_volume_filter():
    """驗證動態 ATR 與成交量：實體不足 1.2 ATR 判定為插針，未放大過 20 期均量判定為無量假突破"""
    # 1. 實體幅度過小 (例如長影線插針，實體只有 0.5，ATR=1.0，1.2*ATR=1.2)
    curr_k_pinbar = {"open": 100.0, "close": 100.5, "high": 103.0, "low": 99.8, "volume": 5000.0}
    valid_pin, reason_pin = evaluate_meme_trade_valid(
        side="BUY",
        curr_k=curr_k_pinbar,
        ma3_curr=101.0,
        ma15_curr=100.0,
        ema200_curr=95.0,
        current_atr=1.0,
        volume_ma20=2000.0,
        funding_rate=0.0001,
        oi_change_pct=0.05,
    )
    assert valid_pin is False
    assert "WEAK_CANDLE_BODY_PINBAR" in reason_pin

    # 2. 成交量未放量 (volume 1500 <= volume_ma20 2000)
    curr_k_low_vol = {"open": 100.0, "close": 102.5, "volume": 1500.0}
    valid_vol, reason_vol = evaluate_meme_trade_valid(
        side="BUY",
        curr_k=curr_k_low_vol,
        ma3_curr=101.0,
        ma15_curr=100.0,
        ema200_curr=95.0,
        current_atr=1.0,
        volume_ma20=2000.0,
        funding_rate=0.0001,
        oi_change_pct=0.05,
    )
    assert valid_vol is False
    assert "INSUFFICIENT_VOLUME" in reason_vol
