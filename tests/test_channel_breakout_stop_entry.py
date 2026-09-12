"""破軌預掛觸價單：觸發價計畫（2026-09-12 使用者核准 A 方案）。"""
import time

import pandas as pd
import pytest

from core import config
from core.engine import TradingEngine
from core.services.strategies import outer_strategy as outer


def _frame(price, *, open_price, rail, atr, volume_ratio=3.0):
    """30 根上升趨勢K；最後一根是未收線的當根。"""
    now_ms = int(time.time() * 1000)
    rows = []
    base = rail - 5 * atr
    for i in range(29):
        close = base + i * 0.15 * atr
        rows.append({
            "timestamp": now_ms - (30 - i) * 60_000,
            "open": close - 0.05 * atr, "high": close + 0.1 * atr,
            "low": close - 0.1 * atr, "close": close,
            "volume": 1000.0, "atr": atr,
            "ema_20": close, "kc_middle": close,
            "kc_upper": rail, "kc_lower": rail - 4 * atr,
            "ma3": close, "ma15": close,
        })
    rows.append({
        "timestamp": now_ms,
        "open": open_price, "high": max(open_price, price), "low": min(open_price, price),
        "close": price, "volume": 1000.0 * volume_ratio, "atr": atr,
        "ema_20": rows[-1]["close"], "kc_middle": rows[-1]["close"],
        "kc_upper": rail, "kc_lower": rail - 4 * atr,
        "ma3": rows[-1]["close"], "ma15": rows[-1]["close"],
    })
    return pd.DataFrame(rows)


def _engine():
    engine = object.__new__(TradingEngine)
    engine._channel_candidate_bar_id = lambda frame: float(frame.iloc[-1]["timestamp"])
    return engine


def _plan(price, **kwargs):
    frame = _frame(price, **kwargs)
    return _engine()._channel_breakout_stop_plan(frame, price, "TEST/USDT")


def _trigger(rail, opened, atr):
    """測試環境（conftest）會覆寫長K門檻，一律以執行時的值計算。"""
    return max(rail, opened + outer.LIVE_BREAKOUT_BODY_ATR * atr)


def test_trigger_uses_long_body_level_when_close_to_rail():
    atr, rail, opened = 0.001, 0.1000, 0.0990
    trigger = _trigger(rail, opened, atr)
    plan = _plan(trigger - 0.4 * atr, open_price=opened, rail=rail, atr=atr)
    assert plan is not None
    assert plan["side"] == "LONG"
    assert plan["trigger"] == pytest.approx(trigger)


def test_no_plan_when_price_already_broke_rail():
    atr, rail, opened = 0.001, 0.1000, 0.0990
    trigger = _trigger(rail, opened, atr)
    assert _plan(trigger + atr, open_price=opened, rail=rail, atr=atr) is None


def test_no_plan_when_price_too_far_from_trigger():
    atr, rail, opened = 0.001, 0.1000, 0.0990
    trigger = _trigger(rail, opened, atr)
    far = trigger - (config.CHANNEL_BREAKOUT_STOP_MAX_DISTANCE_ATR + 2.0) * atr
    assert _plan(far, open_price=opened, rail=rail, atr=atr) is None


def test_no_plan_when_volume_missing():
    atr, rail, opened = 0.001, 0.1000, 0.0990
    trigger = _trigger(rail, opened, atr)
    assert _plan(trigger - 0.4 * atr, open_price=opened, rail=rail, atr=atr,
                 volume_ratio=0.05) is None
