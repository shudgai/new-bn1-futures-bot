import pytest
import pandas as pd
import numpy as np
import os
import core.paper_account as pa_module
import core.strategy as strategy_module
from core.config import DEFAULT_SYMBOLS, get_position_multiplier, get_signal_leverage
from core.ai_advisor import LocalAIAdvisor
from core.strategy import SuperTrendKeltnerStrategy
from core.paper_account import PaperAccount
from core.symbol_rotation import SymbolRotation

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

def test_paper_account_open_close(tmp_path, monkeypatch):
    # 隔離測試：使用臨時空白狀態檔，不受真實持倉影響
    monkeypatch.setattr(pa_module, "STATE_FILE", str(tmp_path / "test_account.json"))
    account = PaperAccount()
    initial_bal = account.balance
    success = account.open_position("BTC/USDT", "LONG", 50000.0, 50.0, 49000.0, 52000.0, "Test Entry")
    assert success is True
    assert "BTC/USDT" in account.positions

    close_success = account.close_position("BTC/USDT", 51000.0, "Test Exit")
    assert close_success is True
    assert "BTC/USDT" not in account.positions

def test_low_score_signal_caps_eth_leverage():
    assert get_position_multiplier(69) == 0.0
    assert get_position_multiplier(70) == 0.5
    assert get_position_multiplier(80) == 1.0
    assert get_position_multiplier(90) == 1.0
    assert get_signal_leverage("ETH/USDT", 70) == 3
    assert get_signal_leverage("ETH/USDT", 80) == 6
    assert get_signal_leverage("ETH/USDT", 90) == 10
    assert get_signal_leverage("APT/USDT", 70) == 3

def test_open_trade_persists_score_reason_and_dynamic_leverage(tmp_path, monkeypatch):
    monkeypatch.setattr(pa_module, "STATE_FILE", str(tmp_path / "test_account.json"))
    account = PaperAccount()
    assert account.open_position(
        "ETH/USDT", "LONG", 1900.0, 30.0, 1890.0, 1920.0,
        "Score70 test", signal_score=70
    )
    assert account.positions["ETH/USDT"]["leverage"] == 3
    trade = account.trades[0]
    assert trade["leverage"] == 3
    assert trade["signal_score"] == 70
    assert trade["reason"] == "Score70 test"

def test_keltner_breakout_and_freshness_are_mandatory(monkeypatch):
    strategy = SuperTrendKeltnerStrategy()
    df = pd.DataFrame({
        "close": [100.0] * 50,
        "close_price_spike_filtered": [100.0] * 50,
        "atr": [1.0] * 50,
        "rsi": [60.0] * 50,
        "volume": [1000.0] * 50,
        "vol_ma_20": [900.0] * 50,
        "kc_upper": [101.0] * 50,
        "kc_lower": [99.0] * 50,
        "kc_width": [2.0] * 50,
        "st_direction": [1] * 50,
    })
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 1)
    result = strategy.evaluate_signal(df, ema_200_1h=90.0)
    assert result["action"] == "HOLD"
    assert "Mandatory_Fail: KC_Breakout" in result["reason"]

    df.loc[df.index[-1], "close"] = 102.0
    df.loc[df.index[-1], "close_price_spike_filtered"] = 102.0
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 21)
    result = strategy.evaluate_signal(df, ema_200_1h=90.0)
    assert result["action"] == "HOLD"
    assert "Mandatory_Fail: SuperTrend_Stale" in result["reason"]

def _entry_score_frame(volume=700.0, rsi=49.0):
    return pd.DataFrame({
        "close": [100.05] * 50,
        "close_price_spike_filtered": [100.05] * 50,
        "atr": [1.0] * 50,
        "rsi": [rsi] * 50,
        "volume": [volume] * 50,
        "vol_ma_20": [1000.0] * 50,
        "kc_upper": [100.0] * 50,
        "kc_lower": [98.0] * 50,
        "kc_width": [2.0] * 50,
        "st_direction": [1] * 50,
    })


def test_score_70_waits_for_pullback_even_when_breakout_is_close(monkeypatch):
    strategy = SuperTrendKeltnerStrategy()
    frame = _entry_score_frame(volume=700.0, rsi=49.0)
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 1)

    result = strategy.evaluate_signal(frame, ema_200_1h=95.0)

    assert result["score"] == 70
    assert result["action"] == "WAIT_PULLBACK"
    assert "Pullback_WAIT_LOW_SCORE(70)" in result["reason"]
    assert "Volume_Partial" in result["reason"]


def test_score_80_can_enter_when_breakout_is_close(monkeypatch):
    strategy = SuperTrendKeltnerStrategy()
    frame = _entry_score_frame(volume=900.0, rsi=49.0)
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 1)

    result = strategy.evaluate_signal(frame, ema_200_1h=95.0)

    assert result["score"] == 80
    assert result["action"] == "BUY"


def _pullback_frame(side="LONG"):
    prices = np.linspace(99.0, 101.0, 50)
    direction = 1 if side == "LONG" else -1
    frame = pd.DataFrame({
        "close": prices if side == "LONG" else prices[::-1],
        "close_price_spike_filtered": prices if side == "LONG" else prices[::-1],
        "atr": [1.0] * 50,
        "rsi": [60.0 if side == "LONG" else 40.0] * 50,
        "volume": [1000.0] * 50,
        "vol_ma_20": [900.0] * 50,
        "kc_upper": [100.0] * 50,
        "kc_lower": [100.0] * 50,
        "ema_20": prices if side == "LONG" else prices[::-1],
        "st_direction": [direction] * 49 + [-direction],
    })
    # 最後一根是未收 K；最近已收 K 必須維持原方向。
    frame.loc[frame.index[-2], "st_direction"] = direction
    return frame

def test_pullback_confirmation_passes_only_after_reclaim(monkeypatch):
    strategy = SuperTrendKeltnerStrategy()
    frame = _pullback_frame("LONG")
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 2)

    waiting = strategy.validate_pullback_entry(
        frame, side="LONG", live_price=99.9, ema_1h=95.0
    )
    assert waiting["status"] == "WAIT"

    passed = strategy.validate_pullback_entry(
        frame, side="LONG", live_price=100.1, ema_1h=95.0
    )
    assert passed["status"] == "PASS"

def test_pullback_confirmation_cancels_stale_or_weak_signal(monkeypatch):
    strategy = SuperTrendKeltnerStrategy()
    frame = _pullback_frame("LONG")
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 9)

    stale = strategy.validate_pullback_entry(
        frame, side="LONG", live_price=100.1, ema_1h=95.0
    )
    assert stale["status"] == "CANCEL"
    assert "SuperTrend 已過期" in stale["reason"]

    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 2)
    frame.loc[frame.index[-2], "volume"] = 100.0
    weak = strategy.validate_pullback_entry(
        frame, side="LONG", live_price=100.1, ema_1h=95.0
    )
    assert weak["status"] == "CANCEL"
    assert "量能低於" in weak["reason"]


def test_local_ai_advisor_accepts_only_allowed_symbols():
    def fake_request(payload):
        assert payload["response_format"]["type"] == "json_object"
        return {
            "model": "test-local",
            "choices": [{"message": {"content": (
                "{\"ranked_symbols\":[\"BTC/USDT\",\"BAD/USDT\","
                "\"BTC/USDT\",\"DOGE/USDT\"],\"summary\":\"測試\"}"
            )}}],
        }

    advisor = LocalAIAdvisor(
        "http://127.0.0.1:8888/v1/chat/completions",
        request_fn=fake_request,
    )
    metrics = [
        {
            "symbol": symbol, "quant_score": 0.5, "trades": 0,
            "avg_pnl": 0.0, "win_rate": 0.5, "stop_rate": 0.0,
            "quote_volume": 1000.0, "change_pct": 1.0,
        }
        for symbol in ["BTC/USDT", "DOGE/USDT"]
    ]
    import asyncio
    ranked = asyncio.run(advisor.rank_symbols(metrics))
    assert ranked == ["BTC/USDT", "DOGE/USDT"]
    assert advisor.status()["status"] == "ok"


def test_symbol_rotation_never_replaces_held_symbol():
    current = list(DEFAULT_SYMBOLS)
    held_symbol = current[-1]
    candidates = current + ["DOT/USDT"]
    scores = {symbol: 0.5 for symbol in candidates}
    scores[held_symbol] = 0.0
    scores[current[-2]] = 0.1
    scores["DOT/USDT"] = 1.0

    selected, changes = SymbolRotation.choose_symbols(
        current, [held_symbol], scores
    )
    assert held_symbol in selected
    assert any(change["in"] == "DOT/USDT" for change in changes)
    assert all(change["out"] != held_symbol for change in changes)


def test_trade_amount_multiplier_uses_half_size_for_score_70():
    assert get_position_multiplier(70) == 0.5
    assert get_position_multiplier(80) == 1.0
    assert get_position_multiplier(100) == 1.0
