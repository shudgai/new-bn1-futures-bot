import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest
from fastapi.responses import Response

from core.engine import TradingEngine
from core.services.symbol_runner import process_single_symbol_runner
from services import api


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_scan_valuation_uses_contract_quantity_and_matching_mark(monkeypatch, symbol, side):
    position = dict(side=side, entry_price=100.0, qty=7.0, amount=999.0,
                    mark_price=100.0, unrealized_pnl=0.0)
    engine = SimpleNamespace(
        account=SimpleNamespace(positions={symbol: position}),
        _try_channel_turn_reverse=AsyncMock(return_value=False),
        _reevaluate_after_close=AsyncMock(),
    )
    exit_check = AsyncMock(return_value=False)
    monkeypatch.setattr("core.services.cap_breakout_entry.observe_cap_breakout", Mock())
    monkeypatch.setattr("core.services.reversal_shadow_logger.record_reversal_shadow_candidates", Mock())
    monkeypatch.setattr("core.services.exits.realtime_profit_exit.enforce_realtime_profit_exit", exit_check)
    frame = pd.DataFrame([dict(timestamp=60000, close=98.0)])

    asyncio.run(process_single_symbol_runner(
        engine, symbol, time.time(), None, False, exit_frame=frame, exit_quote=98.0,
    ))

    assert position["mark_price"] == 98.0
    assert position["unrealized_pnl"] == (-14.0 if side == "LONG" else 14.0)
    exit_check.assert_awaited_once_with(engine, symbol, 98.0)
    engine._reevaluate_after_close.assert_not_awaited()


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "CAP/USDT"])
def test_fresh_trade_updates_display_without_entry_observation(monkeypatch, symbol):
    now = time.time()
    engine = object.__new__(TradingEngine)
    engine.is_running = True
    engine.account = SimpleNamespace(positions={symbol: {}}, log=Mock())
    engine._observe_channel_entry_quote = Mock()
    monkeypatch.setattr("core.services.cap_breakout_entry.observe_cap_breakout", Mock())
    monkeypatch.setattr("core.services.exits.realtime_profit_exit.enforce_realtime_profit_exit",
                        AsyncMock(return_value=False))

    asyncio.run(engine._instant_quote_exit(symbol, 98.0, now * 1000))

    assert engine.tickers[symbol] == 98.0
    assert engine._market_quote_times[symbol] == now
    assert engine.last_ticker_success_ts >= now
    engine._observe_channel_entry_quote.assert_not_called()
    assert not engine._record_market_quote(symbol, 97.0, (now - 1) * 1000)
    assert engine.tickers[symbol] == 98.0


@pytest.mark.parametrize("price,age", [(0.0, 0), (float("nan"), 0), (99.0, 6), (99.0, -2)])
def test_invalid_stale_or_future_quotes_cannot_refresh_display(price, age):
    engine = object.__new__(TradingEngine)
    engine.account = SimpleNamespace(log=Mock())
    engine.tickers = {"CAP/USDT": 100.0}
    engine.last_ticker_success_ts = 123.0

    assert not engine._record_market_quote("CAP/USDT", price, (time.time() - age) * 1000)
    assert engine.tickers["CAP/USDT"] == 100.0
    assert engine.last_ticker_success_ts == 123.0


def test_silent_ticker_timeout_uses_rest_and_cancellation_propagates(monkeypatch):
    async def run():
        engine = object.__new__(TradingEngine)
        engine.account = SimpleNamespace(log=Mock())
        engine.ws_exchange = SimpleNamespace(watch_tickers=AsyncMock())
        engine.update_market_prices = AsyncMock(side_effect=asyncio.CancelledError)
        wait = AsyncMock(side_effect=asyncio.TimeoutError)
        monkeypatch.setattr("core.engine.asyncio.wait_for", wait)
        with pytest.raises(asyncio.CancelledError):
            await engine._ticker_loop()
        assert wait.await_args.kwargs["timeout"] == 5.0
        # The substituted wait_for does not consume its coroutine.
        wait.await_args.args[0].close()
        engine.update_market_prices.assert_awaited_once()
        assert "TimeoutError" in engine.account.log.call_args.args[0]

    asyncio.run(run())


def test_supervisor_restores_market_task_even_when_trading_is_healthy(monkeypatch, tmp_path):
    market_start = Mock()
    monkeypatch.setattr(api, "WEB_READ_ONLY", False)
    monkeypatch.setattr(api, "BOT_PAUSED_FILE", str(tmp_path / "no-pause"))
    monkeypatch.setattr(api, "_bot_control_lock", asyncio.Lock())
    monkeypatch.setattr(api.engine, "start_market_data", market_start)
    monkeypatch.setattr(api.engine, "is_running", True)
    monkeypatch.setattr(api.engine, "task", SimpleNamespace(done=lambda: False))

    assert asyncio.run(api.recover_bot_if_needed()) is False
    market_start.assert_called_once()


def test_quote_age_is_per_symbol_and_read_only_does_not_claim_live(monkeypatch):
    monkeypatch.setattr(api, "visible_symbols", lambda: ["龙虾/USDT", "CAP/USDT"])
    monkeypatch.setattr(api.engine, "_market_quote_times",
                        {"龙虾/USDT": time.time(), "CAP/USDT": time.time() - 10}, raising=False)
    monkeypatch.setattr(api, "WEB_READ_ONLY", False)
    status = api.market_data_status()
    assert status["market_data_stale"] is True
    assert status["ticker_age_seconds"]["CAP/USDT"] >= 10.0
    assert status["ticker_age_seconds"]["龙虾/USDT"] < 1.0
    monkeypatch.setattr(api, "WEB_READ_ONLY", True)
    assert api.market_data_status()["market_data_stale"] is False


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_both_api_endpoints_match_independent_gross_and_net_valuation(monkeypatch, side):
    position = dict(symbol="CAP/USDT", side=side, entry_price=0.072437243,
                    mark_price=0.07214, qty=6278.536401176552, margin=90.0,
                    unrealized_pnl=0.0)
    expected = (position["mark_price"] - position["entry_price"]) * position["qty"]
    if side == "SHORT":
        expected = -expected

    async def update(tickers):
        position["unrealized_pnl"] = expected
        return expected

    monkeypatch.setattr(api, "WEB_READ_ONLY", False)
    monkeypatch.setattr(api.engine.account, "positions", {"CAP/USDT": position})
    monkeypatch.setattr(api.engine.account, "position_meta", {})
    monkeypatch.setattr(api.engine.account, "update_positions", update)
    net = expected - (
        (position["entry_price"] + position["mark_price"]) * position["qty"] * api.TAKER_FEE_RATE
        + position["mark_price"] * position["qty"] * api.SLIPPAGE_PCT
    )
    for endpoint in (api.get_status, api.get_prices):
        payload = json.loads(asyncio.run(endpoint(Response())).body)
        assert payload["unrealized_pnl"] == round(expected, 2)
        assert payload["estimated_net_unrealized_pnl"] == round(net, 2)
        assert payload["positions"][0]["unrealized_pnl"] == pytest.approx(expected)
        assert payload["positions"][0]["estimated_net_unrealized_pnl"] == pytest.approx(net)
