"""Status responses use the real engine methods and an isolated paper account."""
import json

import pytest
from fastapi import Response

import core.paper_account as paper_module
from core.engine import TradingEngine


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_status_response_preserves_shadow_contract(tmp_path, monkeypatch):
    from services import api

    monkeypatch.setattr(paper_module, "STATE_FILE", str(tmp_path / "status-account.json"))
    monkeypatch.setattr(api.engine, "account", paper_module.PaperAccount())
    monkeypatch.setattr(api.engine, "_btc_lead_shadow_active", {"direction": "LONG"})
    monkeypatch.setattr(api.engine, "_btc_lead_shadow_events", [
        {"delay_sec": 2., "chop_locked": False},
        {"delay_sec": 4., "chop_locked": False},
        {"delay_sec": 50., "chop_locked": True},
    ])
    response = Response()
    result = await api.get_status(response)
    assert result["btc_lead_shadow"] == {
        "active": {"direction": "LONG"},
        "events": api.engine._btc_lead_shadow_events,
        "total_events": 3, "eligible_events": 2, "average_delay_sec": 3.,
    }
    assert response.headers["Cache-Control"].startswith("no-store")
    json.dumps(result)


def test_empty_shadow_status_is_read_only():
    engine = object.__new__(TradingEngine)
    assert engine.btc_lead_shadow_status() == {
        "active": {}, "events": [], "total_events": 0,
        "eligible_events": 0, "average_delay_sec": None,
    }
    assert vars(engine) == {}


def test_visible_tickers_resolves_lobster_exchange_symbol_alias(monkeypatch):
    from services import api

    monkeypatch.setattr(api, "DEFAULT_SYMBOLS", ["LOBSTER/USDT", "CAP/USDT"])
    monkeypatch.setattr(
        api.engine,
        "tickers",
        {
            "龙虾/USDT:USDT": 0.02832,
            "CAP/USDT:USDT": 0.07386,
        },
        raising=False,
    )
    monkeypatch.setattr(
        api.engine,
        "account",
        type("Account", (), {"positions": {}})(),
    )

    assert api.visible_tickers() == {
        "LOBSTER/USDT": 0.02832,
        "CAP/USDT": 0.07386,
    }


def test_visible_tickers_keeps_position_price_fallback(monkeypatch):
    from services import api

    monkeypatch.setattr(api, "DEFAULT_SYMBOLS", ["LOBSTER/USDT"])
    monkeypatch.setattr(api.engine, "tickers", {}, raising=False)
    monkeypatch.setattr(
        api.engine,
        "account",
        type(
            "Account",
            (),
            {
                "positions": {
                    "LOBSTER/USDT": {"mark_price": 0.0284, "entry_price": 0.0280},
                },
            },
        )(),
    )

    assert api.visible_tickers() == {"LOBSTER/USDT": 0.0284}


@pytest.mark.parametrize(
    "symbol,ticker_key,side,entry,current",
    [
        ("LOBSTER/USDT", "龍蝦/USDT:USDT", "SHORT", 0.0281, 0.0278),
        ("LOBSTER/USDT", "龍蝦/USDT:USDT", "LONG", 0.0278, 0.0281),
        ("CAP/USDT", "CAP/USDT:USDT", "LONG", 0.0750, 0.0753),
        ("CAP/USDT", "CAP/USDT:USDT", "SHORT", 0.0753, 0.0750),
    ],
)
def test_position_pnl_uses_current_ticker_for_lobster_and_cap(
    monkeypatch, symbol, ticker_key, side, entry, current,
):
    from services import api

    position = {
        "symbol": symbol,
        "side": side,
        "entry_price": entry,
        "qty": 1_000.0,
        "margin": 20.0,
    }
    account = type(
        "Account",
        (),
        {
            "positions": {symbol: position},
            "position_meta": {},
        },
    )()
    monkeypatch.setattr(api, "DEFAULT_SYMBOLS", [symbol])
    monkeypatch.setattr(api.engine, "tickers", {ticker_key: current}, raising=False)
    monkeypatch.setattr(api.engine, "account", account)
    monkeypatch.setattr(api.engine, "position_triggers", {}, raising=False)

    row = api.positions_with_triggers()[0]
    expected_gross = (
        (current - entry) * position["qty"]
        if side == "LONG"
        else (entry - current) * position["qty"]
    )
    expected_net = (
        expected_gross
        - (entry + current) * position["qty"] * api.TAKER_FEE_RATE
        - current * position["qty"] * api.SLIPPAGE_PCT
    )

    assert row["unrealized_pnl"] == pytest.approx(expected_gross)
    assert row["estimated_net_unrealized_pnl"] == pytest.approx(expected_net)
    assert api.current_unrealized_pnl() == pytest.approx(expected_gross)
    assert api.estimated_net_unrealized_pnl() == pytest.approx(expected_net)


@pytest.mark.parametrize(
    "symbol,side,entry,mark,last",
    [
        ("CAP/USDT", "LONG", 0.0750, 0.0752, 0.0748),
        ("CAP/USDT", "SHORT", 0.0750, 0.0748, 0.0752),
        ("LOBSTER/USDT", "LONG", 0.0280, 0.0282, 0.0278),
        ("LOBSTER/USDT", "SHORT", 0.0280, 0.0278, 0.0282),
    ],
)
def test_position_pnl_uses_mark_price_not_last_trade(
    monkeypatch, symbol, side, entry, mark, last,
):
    from services import api

    position = {
        "symbol": symbol,
        "side": side,
        "entry_price": entry,
        "mark_price": mark,
        "qty": 1_000.0,
        "margin": 20.0,
    }
    account = type(
        "Account",
        (),
        {
            "positions": {symbol: position},
            "position_meta": {},
        },
    )()
    monkeypatch.setattr(api, "DEFAULT_SYMBOLS", [symbol])
    monkeypatch.setattr(api.engine, "tickers", {symbol: last}, raising=False)
    monkeypatch.setattr(api.engine, "account", account)
    monkeypatch.setattr(api.engine, "position_triggers", {}, raising=False)

    row = api.positions_with_triggers()[0]
    expected_gross = (
        (mark - entry) * position["qty"]
        if side == "LONG"
        else (entry - mark) * position["qty"]
    )

    assert row["pnl_price"] == mark
    assert row["unrealized_pnl"] == pytest.approx(expected_gross)
    assert api.current_unrealized_pnl() == pytest.approx(expected_gross)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_position_pnl_uses_ticker_when_mark_is_entry_price_fallback(
    monkeypatch, side,
):
    from services import api

    symbol = "LOBSTER/USDT"
    entry = 0.028
    current = 0.0278
    position = {
        "symbol": symbol,
        "side": side,
        "entry_price": entry,
        "mark_price": entry,
        "qty": 1_000.0,
        "margin": 20.0,
    }
    account = type(
        "Account",
        (),
        {
            "positions": {symbol: position},
            "position_meta": {},
        },
    )()
    monkeypatch.setattr(api, "DEFAULT_SYMBOLS", [symbol])
    monkeypatch.setattr(api.engine, "tickers", {symbol: current}, raising=False)
    monkeypatch.setattr(api.engine, "account", account)
    monkeypatch.setattr(api.engine, "position_triggers", {}, raising=False)

    row = api.positions_with_triggers()[0]
    expected_gross = (
        (current - entry) * position["qty"]
        if side == "LONG"
        else (entry - current) * position["qty"]
    )

    assert row["pnl_price"] == current
    assert row["unrealized_pnl"] == pytest.approx(expected_gross)
