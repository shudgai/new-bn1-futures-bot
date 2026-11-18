import time
import math
import pandas as pd
import pytest

from core.services.exits.abnormal_candle_exit import live_adverse_waterfall_evidence
from core.services.exits.hard_stop_service import hard_stop_reason


def test_entry_bar_waterfall_evidence_requires_piercing_and_loss():
    now = 1731900000.0  # reference time
    now_ms = now * 1000.0

    # 1. 空單：開盤價 100 在中軌 100.2 下方，最新價 103 衝上中軌上方（貫穿中軌）
    # 但如果開盤價本來就在中軌上方（例如 middle=99.5, open=100），非貫穿中軌
    pos_short = {
        "symbol": "龙虾/USDT",
        "side": "SHORT",
        "entry_price": 100.0,
        "entry_atr": 1.0,
        "open_timestamp": now - 120,  # 已持倉 2 分鐘（非當根）
    }

    # 構造 DataFrame
    frame = pd.DataFrame([
        {
            "timestamp": (now - 60) * 1000,
            "open": 99.5,
            "high": 100.5,
            "low": 99.0,
            "close": 100.0,
            "atr": 1.0,
            "kc_middle": 100.0,
            "is_closed": True,
        },
        {
            "timestamp": now * 1000,
            "open": 100.0,  # 空單 opening <= middle (100.0 <= 100.0)
            "high": 103.5,
            "low": 99.8,
            "close": 103.0,  # price > middle (103.0 > 100.0) -> 貫穿中軌！
            "atr": 1.0,
            "kc_middle": 100.0,
            "is_closed": False,
        }
    ])
    frame.attrs["timeframe_ms"] = 60_000.0
    frame.attrs["snapshot_ms"] = now_ms + 10_000.0

    # 測試正常貫穿中軌且實體 3.0 >= 1.5 * 1.0，且虧損 3.0 >= 0.8 * 1.0
    evidence = live_adverse_waterfall_evidence(pos_short, frame, 103.0, now_ms=now_ms + 10_000.0)
    assert evidence is not None
    assert evidence["trigger"] == "LIVE_ADVERSE_WATERFALL"

    # 若未貫穿中軌（例如開盤價本來就已經在中軌反側 100.5 > middle 100.0）
    frame_no_traverse = frame.copy()
    frame_no_traverse.at[1, "open"] = 100.5
    evidence_no_traverse = live_adverse_waterfall_evidence(pos_short, frame_no_traverse, 103.0, now_ms=now_ms + 10_000.0)
    assert evidence_no_traverse is None

    # 若持倉未有實質虧損（例如 entry_price=104.0，當前價格 103.0，空單仍是浮盈）
    pos_profit = dict(pos_short)
    pos_profit["entry_price"] = 104.0
    evidence_profit = live_adverse_waterfall_evidence(pos_profit, frame, 103.0, now_ms=now_ms + 10_000.0)
    assert evidence_profit is None


def test_ordinary_single_position_loss_is_not_relabelled_as_circuit_breaker():
    pos_long = {
        "symbol": "龙虾/USDT",
        "side": "LONG",
        "entry_price": 100.0,
        "entry_atr": 1.0,
    }

    # Percentage and ATR losses alone are not market-level emergency evidence.
    assert hard_stop_reason(pos_long, 99.15) is None
    assert hard_stop_reason(pos_long, 98.0) is None

    pos_long_atr = {
        "symbol": "龙虾/USDT",
        "side": "LONG",
        "entry_price": 100.0,
        "entry_atr": 0.5,
    }
    assert hard_stop_reason(pos_long_atr, 98.5) is None
