import pytest
import pandas as pd
import numpy as np
from core.strategy import SuperTrendKeltnerStrategy
from core.paper_account import PaperAccount

def test_strategy_indicators():
    strategy = SuperTrendKeltnerStrategy()
    dates = pd.date_range(start="2026-01-01", periods=100, freq="15min")
    prices = np.linspace(100, 200, 100)
    df = pd.DataFrame({
        "timestamp": dates,
        "open": prices,
        "high": prices + 1.0,
        "low": prices - 1.0,
        "close": prices,
        "volume": 1000
    })
    res = strategy.compute_indicators(df)
    assert "supertrend" in res.columns
    assert "st_direction" in res.columns
    assert "atr" in res.columns

def test_paper_account_open_close():
    account = PaperAccount()
    initial_bal = account.balance
    success = account.open_position("BTC/USDT", "LONG", 50000.0, 50.0, 49000.0, 52000.0, "Test Entry")
    assert success is True
    assert "BTC/USDT" in account.positions

    close_success = account.close_position("BTC/USDT", 51000.0, "Test Exit")
    assert close_success is True
    assert "BTC/USDT" not in account.positions
