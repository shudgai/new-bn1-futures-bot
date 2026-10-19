import pytest
import pandas as pd
import numpy as np
import time
from core.gates.meme_coin_gate import MemeCoinGate
from core.services.strategies.wild_swing_strategy import WildSwingStrategy
from core.gates.holding_protection_gate import HoldingProtectionExitGate


def test_5u_dynamic_trailing_profit_lock():
    """驗證：未實現浮盈曾達到 5U 以上，回跌到 75% 立即觸發市價動態鎖利。"""
    strategy = WildSwingStrategy()
    position = {
        "symbol": "LOBSTER/USDT",
        "side": "SHORT",
        "entry_price": 0.0280,
        "amount": 1000.0,  # 1000 顆
        "max_profit": 0.0,
    }
    
    # 建立基本 frame
    df = pd.DataFrame({
        "timestamp": [1700000000000, 1700000060000],
        "open": [0.0280, 0.0230],
        "high": [0.0280, 0.0230],
        "low": [0.0220, 0.0210],
        "close": [0.0230, 0.0220],
        "ma5": [0.0250, 0.0230],
        "ma15": [0.0270, 0.0260],
        "kc_middle": [0.0260, 0.0250],
        "kc_upper": [0.0280, 0.0270],
        "kc_lower": [0.0240, 0.0230],
        "atr": [0.0010, 0.0010],
        "is_closed": [True, False],
    })

    # 1. 價格暴跌至 0.0220，浮盈 = (0.0280 - 0.0220) * 1000 = 6.0 USDT (> 5U)
    res1 = strategy.evaluate_exit("LOBSTER/USDT", position, df, quote=0.0220)
    assert res1 is None  # 利潤正奔跑中，未回撤不平倉
    assert position.get("max_profit") == pytest.approx(6.0)

    # 2. 價格插針回彈至 0.0236，浮盈 = (0.0280 - 0.0236) * 1000 = 4.4 USDT
    # 4.4 USDT <= 6.0 * 0.75 (4.5 USDT)，回撤超過 25%！
    res2 = strategy.evaluate_exit("LOBSTER/USDT", position, df, quote=0.0236)
    assert res2 is not None
    assert res2["type"] == "EXIT_BY_5U_DYNAMIC_TRAILING_LOCK"
    assert res2["reason"] == "EXIT_BY_5U_DYNAMIC_TRAILING_LOCK"

    # 3. 驗證 HoldingProtectionExitGate 放行此 5U 動態鎖利
    auth_ok, auth_code, _ = HoldingProtectionExitGate.validate_exit(
        position, df, quote=0.0236, candidate_reason="EXIT_BY_5U_DYNAMIC_TRAILING_LOCK"
    )
    assert auth_ok is True
    assert auth_code == "EXIT_BY_5U_DYNAMIC_TRAILING_LOCK"


def test_green_candle_pullback_not_prematurely_closed():
    """驗證：空單在空頭走勢中，正常回踩小綠棒（壓制在 MA5 與中軌下方）嚴禁被洗盤平倉。"""
    strategy = WildSwingStrategy()
    position = {
        "symbol": "LOBSTER/USDT",
        "side": "SHORT",
        "entry_price": 0.0280,
        "amount": 100.0,
        "max_profit": 1.0,  # 未達 5U
    }

    # 構造三根 K 棒，第 2 根低點，第 3 根收微幅小綠棒 (open=0.0240, close=0.0242)，但仍被 MA5 (0.0250) 壓制
    df = pd.DataFrame({
        "timestamp": [1700000000000, 1700000060000, 1700000120000],
        "open": [0.0260, 0.0250, 0.0240],
        "high": [0.0260, 0.0250, 0.0244],
        "low": [0.0248, 0.0238, 0.0239],
        "close": [0.0250, 0.0239, 0.0242], # 第 3 根是小綠棒
        "ma5": [0.0260, 0.0255, 0.0250],  # 壓制收盤價 0.0242
        "kc_middle": [0.0265, 0.0260, 0.0255],
        "kc_upper": [0.0280, 0.0275, 0.0270],
        "kc_lower": [0.0250, 0.0245, 0.0240],
        "atr": [0.0010, 0.0010, 0.0010],
        "is_closed": [True, True, True],
    })

    # evaluate_exit 應判定空頭結構未壞，小綠棒次級回踩嚴禁平倉
    res = strategy.evaluate_exit("LOBSTER/USDT", position, df, quote=0.0242)
    assert res is None, f"小綠棒回踩不該被平倉，但被平倉了: {res}"


def test_dynamic_anti_chop_breakout_entry():
    """驗證：不鎖死時間，同 K 棒禁止秒開，但價格跌破上次平倉價後隨時可開倉。"""
    frame = pd.DataFrame({
        "timestamp": [1700000000000, 1700000060000],
        "open": [0.0260, 0.0250],
        "high": [0.0260, 0.0250],
        "low": [0.0250, 0.0235],
        "close": [0.0250, 0.0238],
        "ma5": [0.0258, 0.0245],
        "ma15": [0.0265, 0.0260],
        "kc_middle": [0.0260, 0.0255],
        "kc_upper": [0.0275, 0.0270],
        "kc_lower": [0.0245, 0.0240],
        "atr": [0.0010, 0.0010],
        "is_closed": [True, False],
    })

    # 1. 同一根 K 棒內剛平倉 (last_closed_ts = 1700000060000)，開空被阻斷
    passed1, reason1, _ = MemeCoinGate.evaluate_meme_entry(
        frame, quote=0.0235, side="SHORT", symbol="LOBSTER/USDT",
        last_closed_price=0.0238, last_closed_side="SHORT", last_closed_ts=1700000060000
    )
    assert passed1 is False
    assert reason1 == "BLOCKED_SAME_CANDLE_ANTI_CHOP"

    # 2. 到了新一根 K 棒 (ts = 1700000120000)，但最新價 0.0239 >= 上次平倉價 0.0238 (原地追空)，被阻斷
    frame2 = pd.DataFrame({
        "timestamp": [1700000060000, 1700000120000],
        "open": [0.0250, 0.0240],
        "high": [0.0250, 0.0240],
        "low": [0.0235, 0.0230],
        "close": [0.0238, 0.0232],
        "ma5": [0.0245, 0.0235],
        "ma15": [0.0260, 0.0255],
        "kc_middle": [0.0255, 0.0250],
        "kc_upper": [0.0270, 0.0265],
        "kc_lower": [0.0240, 0.0235],
        "atr": [0.0010, 0.0010],
        "is_closed": [True, False],
    })
    passed2, reason2, _ = MemeCoinGate.evaluate_meme_entry(
        frame2, quote=0.0239, side="SHORT", symbol="LOBSTER/USDT",
        last_closed_price=0.0238, last_closed_side="SHORT", last_closed_ts=1700000060000
    )
    assert passed2 is False
    assert reason2 == "BLOCKED_REQUIRES_NEW_LOW_BELOW_LAST_EXIT"

    # 3. 最新價跌破上次平倉價 (quote = 0.0232 < 0.0238)，立即解封開空！不卡死冷卻時間！
    passed3, reason3, _ = MemeCoinGate.evaluate_meme_entry(
        frame2, quote=0.0232, side="SHORT", symbol="LOBSTER/USDT",
        last_closed_price=0.0238, last_closed_side="SHORT", last_closed_ts=1700000060000
    )
    assert passed3 is True
    assert "MEME_" in reason3
