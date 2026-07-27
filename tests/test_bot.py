import pytest
import pandas as pd
import numpy as np
import os
import json
import core.paper_account as pa_module
import core.strategy as strategy_module
from core.config import (
    DEFAULT_SYMBOLS, get_position_multiplier, get_signal_leverage,
    RSI_LONG_THRESHOLD, SUPERTREND_MAX_FLIP_AGE_BARS, MIN_SCORE_THRESHOLD,
)
from core.ai_advisor import LocalAIAdvisor
from core.trade_history_analysis import TradeHistoryAnalyzer
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
    close_notifications = []
    account.on_trade_closed = lambda: close_notifications.append("closed")
    initial_bal = account.balance
    success = account.open_position("BTC/USDT", "LONG", 50000.0, 50.0, 49000.0, 52000.0, "Test Entry")
    assert success is True
    assert "BTC/USDT" in account.positions

    close_success = account.close_position("BTC/USDT", 51000.0, "Test Exit")
    assert close_success is True
    assert "BTC/USDT" not in account.positions
    assert close_notifications == ["closed"]

def test_low_score_signal_caps_eth_leverage():
    assert get_position_multiplier(69) == 0.0
    assert get_position_multiplier(70) == 0.6
    assert get_position_multiplier(80) == 1.0
    assert get_position_multiplier(90) == 1.5
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

def test_atr_range_filter_is_mandatory(monkeypatch):
    """1h 大趨勢之外，ATR 波動率範圍是目前唯一還會直接 HOLD 的強制門檻
    （KC 突破、量能、RSI、新鮮度都已改成評分制，見下面
    test_kc_breakout_and_freshness_lower_score_not_mandatory）。"""
    strategy = SuperTrendKeltnerStrategy()
    df = pd.DataFrame({
        "close": [100.0] * 50,
        "close_price_spike_filtered": [100.0] * 50,
        "atr": [1.0] * 50,  # atr/price = 1% > MAX_ATR_PCT(0.6%)
        "rsi": [60.0] * 50,
        "volume": [1000.0] * 50,
        "vol_ma_20": [900.0] * 50,
        "kc_upper": [101.0] * 50,
        "kc_lower": [99.0] * 50,
        "kc_width": [2.0] * 50,
        "ema_20": [101.0] * 50,
        "ema_50": [100.0] * 50,
        "st_direction": [1] * 50,
    })
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 1)
    result = strategy.evaluate_signal(df, ema_200_1h=90.0)
    assert result["action"] == "HOLD"
    assert "Mandatory_Fail: ATR_Too_High" in result["reason"]

    df.loc[:, "atr"] = 0.001  # atr/price = 0.001% < MIN_ATR_PCT(0.15%)
    result = strategy.evaluate_signal(df, ema_200_1h=90.0)
    assert result["action"] == "HOLD"
    assert "Mandatory_Fail: ATR_Too_Low" in result["reason"]


def _entry_score_frame(volume=700.0, rsi=49.0):
    return pd.DataFrame({
        "close": [100.05] * 50,
        "close_price_spike_filtered": [100.05] * 50,
        "atr": [0.3] * 50,  # atr/price = 0.3%，落在 MIN/MAX_ATR_PCT 之間，不會被強制門檻擋掉
        "rsi": [rsi] * 50,
        "volume": [volume] * 50,
        "vol_ma_20": [1000.0] * 50,
        "kc_upper": [100.0] * 50,
        "kc_lower": [98.0] * 50,
        "kc_width": [2.0] * 50,
        "ema_20": [101.0] * 50,
        "ema_50": [100.0] * 50,
        "st_direction": [1] * 50,
    })


def test_kc_breakout_and_freshness_lower_score_not_mandatory(monkeypatch):
    """KC 突破/訊號新鮮度沒過，不再是強制擋單（Mandatory_Fail），
    而是評分制底下的扣分，分數不夠門檻時走 HOLD + Score_Low。"""
    strategy = SuperTrendKeltnerStrategy()
    # 量能、RSI 也刻意不過，確保不管品質加分怎麼算都遠低於 MIN_SCORE_THRESHOLD
    frame = _entry_score_frame(volume=100.0, rsi=RSI_LONG_THRESHOLD - 5)
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: SUPERTREND_MAX_FLIP_AGE_BARS + 1)

    result = strategy.evaluate_signal(frame, ema_200_1h=95.0)

    assert result["action"] == "HOLD"
    # Score_Low 分支的 dict 沒有獨立的 "score" 欄位，分數只嵌在 reason 文字裡
    assert "Score_Low" in result["reason"]


def test_qualifying_score_waits_for_pullback_never_buys_immediately(monkeypatch):
    """一律回踩機制：分數再高也不會有 BUY（立即進場），只會是 WAIT_PULLBACK。
    用 config 常數而非寫死的分數，避免每次調參又要改測試。"""
    strategy = SuperTrendKeltnerStrategy()
    # 量能、RSI、新鮮度都給足以通過的條件，KC 突破在 _entry_score_frame 裡本來就成立
    frame = _entry_score_frame(volume=1200.0, rsi=RSI_LONG_THRESHOLD + 5)
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)
    monkeypatch.setattr(strategy_module, "bars_since_supertrend_flip", lambda value: 1)

    result = strategy.evaluate_signal(frame, ema_200_1h=95.0)

    assert result["action"] == "WAIT_PULLBACK"
    assert result["score"] >= MIN_SCORE_THRESHOLD
    assert "target_zone" in result
    assert f"Pullback_WAIT({result['score']})" in result["reason"]


def _reconfirm_frame(side="LONG", st_direction=None, volume=1000.0, rsi=None, atr=0.3):
    """confirm_pullback_entry() 用的最小 K 線快照：atr 落在 MIN/MAX_ATR_PCT
    之間，避免被 ATR 範圍門檻誤擋，方便單獨測試量能/RSI/趨勢反轉的判斷。"""
    direction = 1 if side == "LONG" else -1
    if st_direction is None:
        st_direction = direction
    if rsi is None:
        rsi = 60.0 if side == "LONG" else 40.0
    price = 100.0
    return pd.DataFrame({
        "close": [price] * 50,
        "close_price_spike_filtered": [price] * 50,
        "atr": [atr] * 50,
        "rsi": [rsi] * 50,
        "volume": [volume] * 50,
        "vol_ma_20": [900.0] * 50,
        "kc_upper": [101.0] * 50,
        "kc_lower": [99.0] * 50,
        "ema_20": [price] * 50,
        "st_direction": [st_direction] * 50,
    })


def test_pullback_reconfirmation_passes_when_conditions_still_hold(monkeypatch):
    strategy = SuperTrendKeltnerStrategy()
    frame = _reconfirm_frame("LONG")
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)

    result = strategy.confirm_pullback_entry(frame, side="LONG", ema_1h=95.0)
    assert result["status"] == "PASS"


def test_pullback_reconfirmation_cancels_when_supertrend_reversed(monkeypatch):
    """假突破最典型的樣貌：登記回調待命之後，SuperTrend 方向其實已經反轉了。"""
    strategy = SuperTrendKeltnerStrategy()
    frame = _reconfirm_frame("LONG", st_direction=-1)
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)

    result = strategy.confirm_pullback_entry(frame, side="LONG", ema_1h=95.0)
    assert result["status"] == "CANCEL"
    assert "SuperTrend 方向已反轉" in result["reason"]


def test_pullback_reconfirmation_cancels_when_momentum_faded(monkeypatch):
    """量能萎縮到門檻以下：原本的突破已經沒有真實成交量支撐，回踩進場前取消。"""
    strategy = SuperTrendKeltnerStrategy()
    frame = _reconfirm_frame("LONG", volume=100.0)
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)

    result = strategy.confirm_pullback_entry(frame, side="LONG", ema_1h=95.0)
    assert result["status"] == "CANCEL"
    assert "量能轉弱" in result["reason"]


def test_pullback_reconfirmation_cancels_when_1h_trend_flipped(monkeypatch):
    strategy = SuperTrendKeltnerStrategy()
    frame = _reconfirm_frame("LONG")
    monkeypatch.setattr(strategy, "compute_indicators", lambda value: value)

    result = strategy.confirm_pullback_entry(frame, side="LONG", ema_1h=105.0)
    assert result["status"] == "CANCEL"
    assert "1h 大趨勢已轉空" in result["reason"]


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


def test_trade_history_ai_analysis_is_sanitized_cached_and_persisted(tmp_path):
    calls = []

    def fake_request(payload):
        calls.append(payload)
        user_payload = json.loads(payload["messages"][1]["content"])
        assert user_payload["task"] == "analyze_trade_history"
        history = user_payload["history"]
        assert history["overview"]["closed_trades"] == 1
        serialized = json.dumps(history)
        assert "BINANCE_API_KEY" not in serialized
        assert "must-not-leak" not in serialized
        return {
            "model": "test-local",
            "choices": [{"message": {"content": json.dumps({
                "summary": "一筆交易樣本，暫以風控觀察為主。",
                "strengths": ["有記錄訊號分數"],
                "weaknesses": ["樣本不足"],
                "recommendations": ["累積更多樣本後再調整"],
                "risk_flags": ["單筆結果不具代表性"],
            }, ensure_ascii=False)}}],
        }

    advisor = LocalAIAdvisor(
        "http://127.0.0.1:8888/v1/chat/completions",
        request_fn=fake_request,
    )
    analyzer = TradeHistoryAnalyzer(
        advisor,
        analysis_file=str(tmp_path / "ai_trade_analysis.json"),
    )
    trades = [
        {
            "id": 2,
            "time": "07/26 01:05:00",
            "symbol": "BTC/USDT",
            "action": "CLOSE_LONG",
            "side": "LONG",
            "price": 101.0,
            "amount": 25.0,
            "fee": 0.1,
            "pnl": 0.9,
            "status": "CLOSED",
            "reason": "觸發止盈 (Take-Profit)",
        },
        {
            "id": 1,
            "time": "07/26 01:00:00",
            "symbol": "BTC/USDT",
            "action": "OPEN_LONG",
            "side": "LONG",
            "price": 100.0,
            "amount": 25.0,
            "fee": 0.05,
            "pnl": 0.0,
            "status": "OPEN",
            "leverage": 3,
            "signal_score": 70,
            "reason": "Pullback_Confirmed",
            "api_secret": "must-not-leak",
        },
    ]

    import asyncio
    assert asyncio.run(analyzer.analyze_if_changed(trades)) is True
    assert asyncio.run(analyzer.analyze_if_changed(trades)) is False
    assert len(calls) == 1
    assert analyzer.status()["status"] == "ok"
    assert analyzer.status()["trade_count"] == 1
    assert (tmp_path / "ai_trade_analysis.json").exists()

    restored = TradeHistoryAnalyzer(
        advisor,
        analysis_file=str(tmp_path / "ai_trade_analysis.json"),
    )
    assert restored.status()["summary"] == "一筆交易樣本，暫以風控觀察為主。"


def test_market_candidates_only_keeps_liquid_crypto_perpetuals(monkeypatch):
    monkeypatch.setattr("core.symbol_rotation.SYMBOL_MIN_QUOTE_VOLUME", 20_000_000.0)
    monkeypatch.setattr("core.symbol_rotation.SYMBOL_MARKET_SCAN_LIMIT", 40)
    tickers = {
        "BTC/USDT:USDT": {"quoteVolume": 100_000_000.0},
        "SKHY/USDT:USDT": {"quoteVolume": 200_000_000.0},
        "LOW/USDT:USDT": {"quoteVolume": 10_000_000.0},
    }
    markets = {
        "BTC/USDT:USDT": {
            "symbol": "BTC/USDT:USDT", "active": True, "swap": True, "quote": "USDT",
            "info": {"contractType": "PERPETUAL", "underlyingType": "COIN"},
        },
        "SKHY/USDT:USDT": {
            "symbol": "SKHY/USDT:USDT", "active": True, "swap": True, "quote": "USDT",
            "info": {"contractType": "TRADIFI_PERPETUAL", "underlyingType": "EQUITY"},
        },
        "LOW/USDT:USDT": {
            "symbol": "LOW/USDT:USDT", "active": True, "swap": True, "quote": "USDT",
            "info": {"contractType": "PERPETUAL", "underlyingType": "COIN"},
        },
    }
    assert SymbolRotation.market_candidates(tickers, markets) == ["BTC/USDT"]


def test_directional_rotation_selects_six_each_and_protects_position(monkeypatch):
    monkeypatch.setattr("core.symbol_rotation.DIRECTIONAL_MIN_SCORE", 60.0)
    monkeypatch.setattr("core.symbol_rotation.DIRECTIONAL_SIDE_COUNT", 6)
    monkeypatch.setattr("core.symbol_rotation.SYMBOL_ROTATION_COUNT", 12)
    monkeypatch.setattr("core.symbol_rotation.SYMBOL_ROTATION_MAX_CHANGES", 12)
    monkeypatch.setattr("core.symbol_rotation.SYMBOL_ROTATION_MIN_SCORE_GAP", 0.0)
    current = [f"OLD{i}/USDT" for i in range(12)]
    metrics = []
    for index in range(7):
        metrics.append({
            "symbol": f"L{index}/USDT",
            "direction": "LONG",
            "eligible": True,
            "final_score": 90.0 - index,
        })
        metrics.append({
            "symbol": f"S{index}/USDT",
            "direction": "SHORT",
            "eligible": True,
            "final_score": 89.0 - index,
        })
    held_symbol = "OLD11/USDT"
    selected, directions, changes = SymbolRotation.choose_directional_symbols(
        current,
        {held_symbol: {"side": "SHORT"}},
        metrics,
    )
    assert held_symbol in selected
    assert directions[held_symbol] == "SHORT"
    assert sum(side == "LONG" for side in directions.values()) == 6
    assert sum(side == "SHORT" for side in directions.values()) == 6
    assert all(change["out"] != held_symbol for change in changes)


def test_directional_rotation_limits_each_scan_to_three_changes(monkeypatch):
    monkeypatch.setattr("core.symbol_rotation.DIRECTIONAL_MIN_SCORE", 60.0)
    monkeypatch.setattr("core.symbol_rotation.DIRECTIONAL_SIDE_COUNT", 6)
    monkeypatch.setattr("core.symbol_rotation.SYMBOL_ROTATION_COUNT", 12)
    monkeypatch.setattr("core.symbol_rotation.SYMBOL_ROTATION_MAX_CHANGES", 3)
    monkeypatch.setattr("core.symbol_rotation.SYMBOL_ROTATION_MIN_SCORE_GAP", 5.0)
    current = [f"OLD{index}/USDT" for index in range(12)]
    metrics = [
        {
            "symbol": f"NEW{index}/USDT",
            "direction": "LONG" if index < 6 else "SHORT",
            "eligible": True,
            "final_score": 90.0 - index,
        }
        for index in range(12)
    ]

    selected, _, changes = SymbolRotation.choose_directional_symbols(
        current, {}, metrics
    )

    assert len(selected) == 12
    assert len(changes) == 3
    assert sum(symbol.startswith("NEW") for symbol in selected) == 3


def test_directional_rotation_backfills_missing_shorts_with_longs(monkeypatch):
    monkeypatch.setattr("core.symbol_rotation.DIRECTIONAL_MIN_SCORE", 60.0)
    monkeypatch.setattr("core.symbol_rotation.DIRECTIONAL_SIDE_COUNT", 6)
    monkeypatch.setattr("core.symbol_rotation.SYMBOL_ROTATION_COUNT", 12)
    metrics = [
        {
            "symbol": f"L{index}/USDT",
            "direction": "LONG",
            "eligible": True,
            "final_score": 90.0 - index,
        }
        for index in range(12)
    ]
    metrics.extend([
        {
            "symbol": f"S{index}/USDT",
            "direction": "SHORT",
            "eligible": True,
            "final_score": 80.0 - index,
        }
        for index in range(2)
    ])

    selected, directions, _ = SymbolRotation.choose_directional_symbols([], {}, metrics)

    assert len(selected) == 12
    assert sum(side == "SHORT" for side in directions.values()) == 2
    assert sum(side == "LONG" for side in directions.values()) == 10


def test_directional_rotation_uses_lower_score_longs_only_to_fill_display(monkeypatch):
    monkeypatch.setattr("core.symbol_rotation.DIRECTIONAL_MIN_SCORE", 60.0)
    monkeypatch.setattr("core.symbol_rotation.SYMBOL_ROTATION_COUNT", 12)
    metrics = [
        {
            "symbol": f"L{index}/USDT",
            "direction": "LONG",
            "eligible": False,
            "final_score": 59.0 - index,
        }
        for index in range(12)
    ]
    metrics.extend([
        {
            "symbol": f"S{index}/USDT",
            "direction": "SHORT",
            "eligible": True,
            "final_score": 80.0 - index,
        }
        for index in range(2)
    ])

    selected, directions, _ = SymbolRotation.choose_directional_symbols([], {}, metrics)

    assert len(selected) == 12
    assert sum(side == "SHORT" for side in directions.values()) == 2
    assert sum(side == "LONG" for side in directions.values()) == 10


def test_trade_amount_multiplier_uses_tiered_size_for_score_70():
    assert get_position_multiplier(70) == 0.6
    assert get_position_multiplier(80) == 1.0
    assert get_position_multiplier(100) == 1.5
