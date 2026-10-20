"""
測試 EMA5 / EMA13 交叉獨立開倉服務 (EmaCrossEntryService)
驗證使用者要求的六大規則與最低限度過濾。
"""

import pandas as pd
import pytest
from core.services.ema_cross_entry_service import EmaCrossEntryService, _ema_cross_tracker


def _make_dummy_frame(rows: int = 40, base_price: float = 100.0, trend: str = "bullish") -> pd.DataFrame:
    data = []
    price = base_price
    for i in range(rows):
        if trend == "bullish":
            price += 0.5
            ma5 = price + 0.2
            ma13 = price - 0.2
        elif trend == "bearish":
            price -= 0.5
            ma5 = price - 0.2
            ma13 = price + 0.2
        else:
            ma5 = price
            ma13 = price

        data.append({
            "timestamp": 1000 + i * 180_000,
            "open": price - 0.2,
            "high": price + 0.5,
            "low": price - 0.5,
            "close": price,
            "ema5": ma5,
            "ma5": ma5,
            "ema13": ma13,
            "ma15": ma13,
            "kc_middle": ma13,
            "kc_upper": ma13 + 2.0,
            "kc_lower": ma13 - 2.0,
            "atr": 1.0,
            "adx": 25.0,
        })
    df = pd.DataFrame(data)
    df.attrs["timeframe_ms"] = 180_000.0
    return df


def test_warmup_not_ready():
    df = _make_dummy_frame(rows=20)
    sig, reason = EmaCrossEntryService.evaluate_entry("TESTUSDT", df, "LONG")
    assert sig is None
    assert reason == "WARMUP_NOT_READY"


def test_fresh_cross_long_success():
    _ema_cross_tracker.clear()
    df = _make_dummy_frame(rows=40, base_price=100.0)
    # 前一根 ma5 <= ma13 (例如 100.0 <= 100.1)
    df.loc[df.index[-2], "ema5"] = df.loc[df.index[-2], "ma5"] = 100.0
    df.loc[df.index[-2], "ema13"] = df.loc[df.index[-2], "ma15"] = 100.1
    # 當前根 ma5 > ma13 (例如 101.0 > 100.2, 距離 0.8 >= 0.07 * 1.0)
    df.loc[df.index[-1], "open"] = 100.8
    df.loc[df.index[-1], "close"] = 101.5  # close > ma13 且 close > open 陽線
    df.loc[df.index[-1], "ema5"] = df.loc[df.index[-1], "ma5"] = 101.0
    df.loc[df.index[-1], "ema13"] = df.loc[df.index[-1], "ma15"] = 100.2

    sig, reason = EmaCrossEntryService.evaluate_entry("TESTUSDT", df, "LONG")
    assert sig is not None
    assert reason == "PASSED"
    assert sig["side"] == "LONG"
    assert sig["entry_source"] == "EMA_CROSS"
    assert sig["structure_confirmation"] == "MA_CROSSOVER"
    assert sig["size_ratio"] >= 0.30


def test_cross_filter_distance_too_small():
    _ema_cross_tracker.clear()
    df = _make_dummy_frame(rows=40, base_price=100.0)
    # 前一根 ma5 <= ma13
    df.loc[df.index[-2], "ema5"] = df.loc[df.index[-2], "ma5"] = 100.0
    df.loc[df.index[-2], "ema13"] = df.loc[df.index[-2], "ma15"] = 100.0
    # 當前根 ma5 剛好略大於 ma13, 但距離 0.02 < 0.07 * ATR(1.0)
    df.loc[df.index[-1], "open"] = 100.1
    df.loc[df.index[-1], "close"] = 100.5
    df.loc[df.index[-1], "ema5"] = df.loc[df.index[-1], "ma5"] = 100.03
    df.loc[df.index[-1], "ema13"] = df.loc[df.index[-1], "ma15"] = 100.01

    sig, reason = EmaCrossEntryService.evaluate_entry("TESTUSDT", df, "LONG")
    assert sig is None
    assert "EMA_SPREAD_TOO_SMALL" in reason


def test_cross_delayed_reentry_within_2_bars():
    _ema_cross_tracker.clear()
    df = _make_dummy_frame(rows=40, base_price=100.0)
    # 模擬交叉當根被擋 (例如 close 稍微低於 ma13)
    df.loc[df.index[-2], "ema5"] = df.loc[df.index[-2], "ma5"] = 100.0
    df.loc[df.index[-2], "ema13"] = df.loc[df.index[-2], "ma15"] = 100.0
    df.loc[df.index[-1], "open"] = 100.0
    df.loc[df.index[-1], "close"] = 100.1  # 暫時低於 ma13
    df.loc[df.index[-1], "ema5"] = df.loc[df.index[-1], "ma5"] = 100.5
    df.loc[df.index[-1], "ema13"] = df.loc[df.index[-1], "ma15"] = 100.2

    sig, reason = EmaCrossEntryService.evaluate_entry("TESTUSDT", df, "LONG")
    assert sig is None
    assert reason == "PRICE_BELOW_EMA13"

    # 下一根 K 棒，價格回升站穩 EMA13，價格距交叉點 <= 0.5 * ATR
    new_bar = df.iloc[-1].to_dict()
    new_bar["timestamp"] += 180_000
    new_bar["open"] = 100.1
    new_bar["close"] = 100.35  # abs(100.35 - 100.1) = 0.25 <= 0.5 * ATR 且為陽線
    new_bar["ema5"] = new_bar["ma5"] = 100.6
    new_bar["ema13"] = new_bar["ma15"] = 100.25
    df2 = pd.concat([df, pd.DataFrame([new_bar])], ignore_index=True)
    df2.attrs["timeframe_ms"] = 180_000.0

    sig2, reason2 = EmaCrossEntryService.evaluate_entry("TESTUSDT", df2, "LONG")
    assert sig2 is not None
    assert reason2 == "PASSED"
    assert sig2["entry_source"] == "EMA_CROSS_DELAYED"


def test_flip_when_holding_opposite_position():
    _ema_cross_tracker.clear()
    df = _make_dummy_frame(rows=40, base_price=100.0)
    # 空頭轉多頭交叉
    df.loc[df.index[-2], "ema5"] = df.loc[df.index[-2], "ma5"] = 99.8
    df.loc[df.index[-2], "ema13"] = df.loc[df.index[-2], "ma15"] = 100.0
    df.loc[df.index[-1], "open"] = 100.1
    df.loc[df.index[-1], "close"] = 100.5
    df.loc[df.index[-1], "ema5"] = df.loc[df.index[-1], "ma5"] = 100.3
    df.loc[df.index[-1], "ema13"] = df.loc[df.index[-1], "ma15"] = 100.1

    # 當前持有空單
    pos = {"side": "SHORT", "amount": 10.0}
    sig, reason = EmaCrossEntryService.evaluate_entry("TESTUSDT", df, "LONG", position=pos)
    assert sig is not None
    assert sig["needs_flip"] is True
    assert sig["entry_source"] == "EMA_CROSS_FLIP"


def test_same_bar_no_duplicate():
    _ema_cross_tracker.clear()
    df = _make_dummy_frame(rows=40, base_price=100.0)
    df.loc[df.index[-2], "ema5"] = df.loc[df.index[-2], "ma5"] = 100.0
    df.loc[df.index[-2], "ema13"] = df.loc[df.index[-2], "ma15"] = 100.1
    df.loc[df.index[-1], "open"] = 100.8
    df.loc[df.index[-1], "close"] = 101.5
    df.loc[df.index[-1], "ema5"] = df.loc[df.index[-1], "ma5"] = 101.0
    df.loc[df.index[-1], "ema13"] = df.loc[df.index[-1], "ma15"] = 100.2

    sig1, _ = EmaCrossEntryService.evaluate_entry("TESTUSDT", df, "LONG")
    assert sig1 is not None

    # 同一根 K 棒再次呼叫
    sig2, reason2 = EmaCrossEntryService.evaluate_entry("TESTUSDT", df, "LONG")
    assert sig2 is None
    assert reason2 == "BLOCKED_SAME_BAR_DUPLICATE"
