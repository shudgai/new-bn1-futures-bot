"""Existing and new positions opt out of numeric stops, not account emergencies."""

import pytest

from core.gates.unified_pipeline_gates import ExitGate
from core.services.data_pipeline import DataPipeline
from core.services.exits.hard_stop_service import (
    NEW_POSITION_LOSS_POLICY,
    price_atr_stop_disabled,
)
from core.services.exits.trend_lock import KC_WAVE_EXIT_REASON
from core.services.math_execution_gate import validate_exit_math
from test_kc_wave_exit import position, wave_frame


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("symbol", ["1000PEPE/USDT", "龙虾/USDT", "CAP/USDT"])
@pytest.mark.parametrize("distance,atr", [(2.1, 10.0), (0.9, 0.25)])
def test_existing_and_new_positions_both_skip_numeric_stops(side, symbol, distance, atr):
    frame = wave_frame(side, reversed_direction=False)
    pos = position(side, frame)
    pos["symbol"] = symbol
    quote = 100.0 - distance if side == "LONG" else 100.0 + distance
    frame[["open", "close"]] = quote
    frame["high"], frame["low"] = quote + 0.1, quote - 0.1
    frame["atr"] = atr
    snapshot = DataPipeline.calculate_snapshot(
        symbol, frame, current_price=quote, position=pos,
    )
    assert snapshot is not None
    snapshot.raw_df = frame
    snapshot.curr_k, snapshot.prev_k = frame.iloc[-1].to_dict(), frame.iloc[-2].to_dict()
    snapshot.kc_upper, snapshot.kc_lower = 110.0, 90.0
    snapshot.atr = atr
    assert not ExitGate.evaluate(pos, snapshot)[0]
    assert not validate_exit_math(pos, frame, quote, close_reason="CIRCUIT_BREAKER_HARD_SL")[0]
    new_pos = {**pos, "position_loss_policy": NEW_POSITION_LOSS_POLICY}
    should_exit, reason, _ = ExitGate.evaluate(new_pos, snapshot)
    assert not should_exit, reason
    allowed, reason, _ = validate_exit_math(
        new_pos, frame, quote, close_reason="CIRCUIT_BREAKER_HARD_SL",
    )
    assert not allowed and reason == "BLOCKED_MEME_STRATEGY_DISASTER_DISABLED"
    assert price_atr_stop_disabled(pos)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("reason,expected_code", [
    ("HARD_STOP_LOSS", "BLOCKED_EXIT_POSITION_PRICE_ATR_STOP_DISABLED"),
    ("EXIT_INITIAL_ATR_HARD_STOP", "BLOCKED_EXIT_POSITION_PRICE_ATR_STOP_DISABLED"),
    ("CIRCUIT_BREAKER_HARD_SL", "BLOCKED_EXIT_POSITION_PRICE_ATR_STOP_DISABLED"),
    ("MAX_MARGIN_LOSS", "BLOCKED_EXIT_POSITION_PRICE_ATR_STOP_DISABLED"),
    ("STOP_LOSS", "BLOCKED_EXIT_NO_MATH_TRIGGER"),
])
def test_metadata_policy_blocks_numeric_aliases_at_final_boundary(side, reason, expected_code):
    frame = wave_frame(side)
    pos = position(side, frame)
    allowed, code, _ = validate_exit_math(
        pos, frame, 95.0 if side == "LONG" else 105.0,
        {"position_loss_policy": NEW_POSITION_LOSS_POLICY}, close_reason=reason,
    )
    assert not allowed
    expected = (
        "BLOCKED_MEME_STRATEGY_DISASTER_DISABLED"
        if reason in {"HARD_STOP_LOSS", "EXIT_INITIAL_ATR_HARD_STOP", "CIRCUIT_BREAKER_HARD_SL"}
        else expected_code
    )
    assert code == expected


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_policy_rejects_plain_kc_reversal_but_keeps_manual_close(side):
    from core.services.exits.authorized_position_exit import automated_position_exit_allowed

    frame = wave_frame(side)
    pos = {**position(side, frame), "position_loss_policy": NEW_POSITION_LOSS_POLICY}
    assert not automated_position_exit_allowed(
        "CAP/USDT", pos, {}, KC_WAVE_EXIT_REASON, 100.2, frame=frame,
    )
    assert automated_position_exit_allowed(
        "CAP/USDT", pos, {}, "手動平倉", 100.2, is_manual=True, frame=frame,
    )


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_policy_does_not_disable_data_outage_disaster_exit(side):
    pos = {
        "side": side, "entry_price": 100.0, "entry_atr": 1.0, "leverage": 5,
        "position_loss_policy": NEW_POSITION_LOSS_POLICY,
    }
    allowed, reason, _ = validate_exit_math(
        pos, None, 95.0 if side == "LONG" else 105.0,
        close_reason="DATA_OUTAGE_HARD_STOP",
    )
    assert allowed and reason == "EMERGENCY_EXIT_DATA_OUTAGE_LOSS_CONFIRMED"


@pytest.mark.parametrize("policy", [None, "UNKNOWN_POLICY"])
def test_paper_restart_does_not_retrofit_existing_positions(policy, tmp_path, monkeypatch):
    from core.paper_account import PaperAccount

    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
    account = PaperAccount()
    pos = position("LONG", wave_frame("LONG"))
    if policy is not None:
        pos["position_loss_policy"] = policy
    account.positions["CAP/USDT"] = pos
    account.position_meta["CAP/USDT"] = dict(pos)
    account.save_state()
    restored = PaperAccount()
    assert restored.positions["CAP/USDT"].get("position_loss_policy") == policy
    assert price_atr_stop_disabled(
        restored.positions["CAP/USDT"], restored.position_meta["CAP/USDT"],
    )


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_live_account_circuit_breaker_still_flattens_new_policy_positions(
    side, tmp_path, monkeypatch,
):
    import asyncio
    from unittest.mock import AsyncMock

    from core.testnet_account import BinanceTestnetAccount
    from test_circuit_breaker import _make_exchange

    monkeypatch.setattr("core.config.PAPER_TRADING", False)
    monkeypatch.setattr("core.config.USE_DEMO_TRADING", False)
    ex = _make_exchange()
    row = {
        "symbol": "CAPUSDT", "positionAmt": "1" if side == "LONG" else "-1",
        "entryPrice": "100", "markPrice": "99",
    }
    ex.fapiPrivateV2GetPositionRisk = AsyncMock(side_effect=[[row], []])
    account = BinanceTestnetAccount(ex, state_file=str(tmp_path / "live.json"))
    account.live_session_start_equity = "10000"
    account.live_session_equity_available = True
    ex.fapiPrivateV2GetAccount.return_value = {"totalMarginBalance": "8500"}
    account.positions["CAP/USDT"] = {
        "side": side, "entry_price": 100.0, "qty": 1.0,
        "position_loss_policy": NEW_POSITION_LOSS_POLICY,
    }
    asyncio.run(account._check_live_circuit_breaker())
    assert account.circuit_breaker_latched
    assert account.circuit_breaker_position_flattened
    account._raw_create_order.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("account_kind", ["paper", "testnet"])
async def test_real_account_rejects_stale_numeric_exit_intent(
    side, account_kind, tmp_path, monkeypatch,
):
    from core.paper_account import PaperAccount
    from core.testnet_account import BinanceTestnetAccount
    from test_testnet_account import FakeTestnetExchange

    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
    monkeypatch.setattr(BinanceTestnetAccount, "credentials_configured", staticmethod(lambda: True))
    frame = wave_frame(side, reversed_direction=False)
    pos = {**position(side, frame), "position_loss_policy": NEW_POSITION_LOSS_POLICY}
    if account_kind == "paper":
        account = PaperAccount()
    else:
        account = BinanceTestnetAccount(
            FakeTestnetExchange(), state_file=str(tmp_path / "testnet.json"),
        )
    account.positions["CAP/USDT"] = pos
    account._channel_exit_frames = {"CAP/USDT": frame}
    assert not await account.close_position(
        "CAP/USDT", 95.0 if side == "LONG" else 105.0,
        "CIRCUIT_BREAKER_HARD_SL", True,
    )
    assert account.positions["CAP/USDT"] is pos
    assert not account.trades
    if account_kind == "testnet":
        assert not account.exchange.orders


@pytest.fixture
def anyio_backend():
    return "asyncio"
