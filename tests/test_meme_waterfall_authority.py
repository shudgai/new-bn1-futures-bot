"""Only verified live waterfalls bypass the shared profitable-pivot holding lock."""

import asyncio
import math
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from core.gates.unified_pipeline_gates import ExitGate
from core.services.exits.abnormal_candle_exit import live_adverse_waterfall_evidence
from core.services.exits.authorized_position_exit import UltimateExitGatekeeper
from core.services.execution_manager import ExecutionManager
from core.services.math_execution_gate import validate_exit_math

SYMBOLS = ["龙虾/USDT", "CAP/USDT", "1000PEPE/USDT"]


def scenario(side, symbol, monkeypatch, body=3):
    now = math.floor(time.time() / 60) * 60
    monkeypatch.setattr(time, "time", lambda: now + 30)
    sign = 1 if side == "LONG" else -1
    quote = 100 - sign * body
    frame = pd.DataFrame([
        dict(timestamp=(now - 60) * 1000, open=100, high=101, low=99, close=100,
             atr=2, kc_middle=100, kc_upper=110, kc_lower=90, is_closed=True),
        dict(timestamp=now * 1000, open=100, high=max(100, quote) + .1,
             low=min(100, quote) - .1, close=quote, atr=100,
             kc_middle=100, kc_upper=110, kc_lower=90, is_closed=False),
    ])
    frame.attrs.update(timeframe_ms=60_000, snapshot_ms=(now + 30) * 1000)
    pos = dict(symbol=symbol, side=side, entry_price=100, qty=1, margin=20,
               leverage=5, open_timestamp=now + 1)
    return pos, frame, quote


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("body,allowed", [(2.999999, False), (3, True), (3.000001, True)])
def test_inclusive_waterfall_uses_previous_atr_not_live_or_entry_price(
    symbol, side, body, allowed, monkeypatch,
):
    pos, frame, quote = scenario(side, symbol, monkeypatch, body)
    assert (live_adverse_waterfall_evidence(pos, frame, quote) is not None) is allowed
    assert validate_exit_math(pos, frame, quote, close_reason="EXIT_BY_EXTREME_WATERFALL")[0] is allowed
    assert UltimateExitGatekeeper.can_execute_exit(
        symbol, pos, {}, "EXIT_BY_EXTREME_WATERFALL", quote,
        frame=frame, is_bar_closed=False,
    ) is allowed
    pos["entry_price"] = quote
    assert (live_adverse_waterfall_evidence(pos, frame, quote) is not None) is allowed


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("fault", [
    "middle_touch", "aligned_price", "invalid_atr", "missing_atr",
    "unclosed_previous", "gap", "future", "stale", "stale_snapshot",
    "future_snapshot", "unknown_finality", "bad_ohlc", "pre_entry_bar",
    "premature_closed", "rollover",
])
def test_invalid_or_non_waterfall_cannot_claim_emergency(symbol, side, fault, monkeypatch):
    pos, frame, quote = scenario(side, symbol, monkeypatch)
    sign = 1 if side == "LONG" else -1
    if fault == "middle_touch":
        frame.loc[1, "kc_middle"] = quote
    elif fault == "aligned_price":
        quote = 100 + sign * 3
    elif fault == "invalid_atr":
        frame.loc[0, "atr"] = math.nan
    elif fault == "missing_atr":
        frame.loc[0, "atr"] = None
    elif fault == "unclosed_previous":
        frame.loc[0, "is_closed"] = False
    elif fault == "gap":
        frame.loc[0, "timestamp"] -= 1
    elif fault == "future":
        frame["timestamp"] += 60_000
    elif fault == "stale":
        frame["timestamp"] -= 240_000
    elif fault == "stale_snapshot":
        frame.attrs["snapshot_ms"] -= 180_001
    elif fault == "future_snapshot":
        frame.attrs["snapshot_ms"] += 1
    elif fault == "unknown_finality":
        frame = frame.drop(columns=["is_closed"])
    elif fault == "bad_ohlc":
        frame.loc[1, "high"] = frame.loc[1, "low"] - 1
    elif fault == "pre_entry_bar":
        frame["timestamp"] -= 60_000
    elif fault == "premature_closed":
        frame.loc[1, "is_closed"] = True
    elif fault == "rollover":
        old_now = time.time()
        monkeypatch.setattr(time, "time", lambda: old_now + 60)
    assert not validate_exit_math(pos, frame, quote, close_reason="EXIT_BY_EXTREME_WATERFALL")[0]
    account = SimpleNamespace(positions={symbol: pos}, position_meta={}, close_position=AsyncMock())
    assert not asyncio.run(ExecutionManager.close_position(
        account, symbol, quote, "EXIT_BY_EXTREME_WATERFALL", meta={"raw_df": frame},
    ))
    account.close_position.assert_not_awaited()


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("macro", ["BULLISH", "BEARISH", "NEUTRAL"])
def test_outer_rail_without_waterfall_or_pivot_holds_regardless_of_macro(
    symbol, side, macro, monkeypatch,
):
    pos, frame, quote = scenario(side, symbol, monkeypatch, body=1)
    # A narrow band breach is not a 1.5-ATR waterfall.
    frame["kc_upper"], frame["kc_lower"] = 100.5, 99.5
    md = dict(symbol=symbol, raw_df=frame, current_price=quote,
              kc_middle=100, kc_upper=100.5, kc_lower=99.5, atr=2, macro_trend=macro)
    assert not ExitGate.evaluate(pos, md)[0]
    assert not UltimateExitGatekeeper.can_execute_exit(
        symbol, pos, {}, "OPPOSITE_KC_BAND_BREACH", quote, frame=frame,
    )


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("account_type", ["paper", "testnet"])
@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("waterfall", [False, True])
def test_real_full_partial_boundaries_and_main_obey_shared_authority(
    symbol, side, account_type, partial, waterfall, monkeypatch, tmp_path,
):
    from core.paper_account import PaperAccount
    from core.testnet_account import BinanceTestnetAccount
    from test_testnet_account import FakeTestnetExchange

    pos, frame, quote = scenario(side, symbol, monkeypatch, body=3 if waterfall else 1)
    if account_type == "paper":
        monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
        account = PaperAccount()
    else:
        exchange = FakeTestnetExchange()
        account = BinanceTestnetAccount(exchange, state_file=str(tmp_path / "testnet.json"))
        account.refresh = AsyncMock()
    account.positions[symbol] = pos
    account.position_meta[symbol] = dict(pos)
    account._channel_exit_frames = {symbol: frame}
    close = account.partial_close_position if partial else account.close_position
    assert asyncio.run(close(symbol, quote, "EXIT_BY_EXTREME_WATERFALL")) is waterfall
    if not waterfall:
        assert account.positions[symbol]["qty"] == 1
        assert not account.trades
        if account_type == "testnet":
            assert not exchange.orders
    else:
        assert account.trades


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("account_type", ["paper", "testnet"])
@pytest.mark.parametrize("partial", [False, True])
def test_fresh_boundary_reclamation_cancels_cached_waterfall(
    symbol, side, account_type, partial, monkeypatch, tmp_path,
):
    from core.paper_account import PaperAccount
    from core.testnet_account import BinanceTestnetAccount
    from test_testnet_account import FakeTestnetExchange

    pos, frame, quote = scenario(side, symbol, monkeypatch)
    if account_type == "paper":
        monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
        account = PaperAccount()
    else:
        exchange = FakeTestnetExchange()
        account = BinanceTestnetAccount(exchange, state_file=str(tmp_path / "testnet.json"))
        account.refresh = AsyncMock()
    refreshed = frame.copy()
    refreshed.loc[1, "kc_middle"] = quote
    account.positions[symbol] = pos
    account.position_meta[symbol] = dict(pos)
    account._channel_exit_frames = {symbol: frame}
    account.channel_exit_frame_provider = lambda _symbol: refreshed
    close = account.partial_close_position if partial else account.close_position
    assert not asyncio.run(close(symbol, quote, "EXIT_BY_EXTREME_WATERFALL"))
    assert account.positions[symbol]["qty"] == 1
    assert not account.trades
    if account_type == "testnet":
        assert not exchange.orders


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_master_public_gate_requires_live_proof_and_rejects_rail_only(symbol, side, monkeypatch):
    from core.gates.master_gate_funnel import MasterGateFunnel, PositionSnapshot
    from test_meme_master_funnel import market

    pos, frame, quote = scenario(side, symbol, monkeypatch)
    p = PositionSnapshot(symbol, side, 100, pos["open_timestamp"] * 1000, 0, 0, False)
    m = market(symbol=symbol)._replace(
        quote=quote, open_price=100, close_price=quote, kc_middle=100,
        kc_upper=110, kc_lower=90, is_closed=False,
    )
    for proof in (None, frame):
        decision = MasterGateFunnel.evaluate_position_exit(
            p, m, "EXIT_BY_EXTREME_WATERFALL", time.time() * 1000, frame=proof,
        )
        assert decision.can_execute is (proof is not None)
    rail_m = m._replace(quote=88 if side == "LONG" else 112)
    assert not MasterGateFunnel.evaluate_position_exit(
        p, rail_m, "OPPOSITE_KC_BAND_BREACH", time.time() * 1000,
    ).can_execute


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_previous_waterfall_does_not_backfill_a_new_ordinary_bar(symbol, side, monkeypatch):
    pos, frame, quote = scenario(side, symbol, monkeypatch, body=1)
    frame.loc[0, ["open", "high", "low", "close"]] = [100, 111, 89, 90 if side == "LONG" else 110]
    assert not validate_exit_math(pos, frame, quote, close_reason="EXIT_BY_EXTREME_WATERFALL")[0]
