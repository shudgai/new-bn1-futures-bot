"""The entry 1H trend cache must never use the forming hourly candle."""
import time

import pandas as pd
import pytest

import core.engine as engine_module
from core.engine import TradingEngine


class _HourlyStrategy:
    @staticmethod
    def compute_indicators(frame):
        result = frame.copy()
        result["st_direction"] = result["direction"]
        result["adx"] = 20.0
        return result


@pytest.mark.anyio
async def test_one_hour_cache_ignores_the_forming_candle(monkeypatch):
    now_ms = int(time.time() * 1000)
    hour_ms = 60 * 60 * 1000
    current_hour = now_ms // hour_ms * hour_ms
    rows = [
        {"timestamp": current_hour - (31 - index) * hour_ms, "close": 100.0 + index,
         "direction": 1}
        for index in range(30)
    ]
    rows.append({"timestamp": current_hour, "close": 50.0, "direction": -1})
    frame = pd.DataFrame(rows)
    engine = object.__new__(TradingEngine)
    engine.last_1h_cache_time = 0.0
    engine.ema_50_1h_cache = {}
    engine.st_direction_1h_cache = {}
    engine.adx_1h_declining_cache = {}
    engine.account = type("Account", (), {"positions": {}})()
    engine.strategy = _HourlyStrategy()

    async def fetch_klines(*_args, **_kwargs):
        return frame.copy()

    engine.fetch_klines = fetch_klines
    monkeypatch.setattr(engine_module, "DEFAULT_SYMBOLS", ["SOL/USDT"])
    await engine.update_1h_trend_cache()
    assert engine.st_direction_1h_cache["SOL/USDT"] == 1
    assert engine.ema_50_1h_cache["SOL/USDT"] > 100.0