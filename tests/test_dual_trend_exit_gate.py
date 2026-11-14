import time

import pandas as pd
import pytest

from core.services.exits.authorized_position_exit import (
    UltimateExitGatekeeper,
    dual_trend_exit_block_reason,
    is_dual_trend_emergency_exit,
)


def _frame(side, *, kc_reversed, macro_reversed, opened=None):
    latest_start = (int(time.time() * 1000) // 60_000) * 60_000 - 120_000
    latest_close = latest_start + 60_000
    if opened is None:
        opened = (latest_start - 30_000) / 1000
    direction = -1 if (side == "LONG") == kc_reversed else 1
    middle = [100.0, 100.0 + direction]
    macro = (
        "BEARISH" if (side == "LONG") == macro_reversed else "BULLISH"
    )
    frame = pd.DataFrame({
        "timestamp": [latest_start, latest_close],
        "open": [100.0, 100.0],
        "high": [101.0, 102.0],
        "low": [99.0, 100.0],
        "close": [100.0, 101.0],
        "kc_middle": middle,
        "is_closed": [True, True],
    })
    frame.attrs.update({
        "timeframe_ms": 60_000,
        "macro_trend": "NEUTRAL",
        "macro_trend_htf": macro,
    })
    return frame, opened


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize(
    ("kc_reversed", "macro_reversed", "allowed"),
    [(False, False, False), (True, False, False), (False, True, False), (True, True, True)],
)
def test_ordinary_exit_requires_both_post_entry_trends_to_reverse(
    side, kc_reversed, macro_reversed, allowed,
):
    frame, opened = _frame(
        side, kc_reversed=kc_reversed, macro_reversed=macro_reversed,
    )
    position = {
        "side": side,
        "open_timestamp": opened,
        "entry_price": 100.0,
        "qty": 1.0,
    }

    block_reason = dual_trend_exit_block_reason(position, frame)
    if allowed:
        assert block_reason is None
        assert UltimateExitGatekeeper.can_execute_exit(
            "CAP/USDT", position, {}, "ACTIVE_EXIT_TEST", 99.0, frame=frame,
        )
    else:
        assert block_reason is not None
        assert not UltimateExitGatekeeper.can_execute_exit(
            "CAP/USDT", position, {}, "ACTIVE_EXIT_TEST", 99.0, frame=frame,
        )


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_ordinary_exit_fails_closed_when_trend_evidence_is_missing(side):
    position = {
        "side": side,
        "open_timestamp": time.time() - 600,
        "entry_price": 100.0,
        "qty": 1.0,
    }

    assert not UltimateExitGatekeeper.can_execute_exit(
        "CAP/USDT", position, {}, "ACTIVE_EXIT_TEST", 99.0,
    )


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_confirmed_dual_reversal_is_exit_candidate_without_pivot_signal(monkeypatch, side):
    from core.services.execution_manager import ExecutionManager
    from core.gates.ultimate_short_exit_gatekeeper import UltimateShortExitGatekeeper

    frame, opened = _frame(
        side, kc_reversed=True, macro_reversed=True,
    )
    position = {
        "side": side,
        "open_timestamp": opened,
        "entry_price": 100.0,
        "qty": 1.0,
    }
    monkeypatch.setattr(
        UltimateShortExitGatekeeper,
        "structure_exit_candidate",
        lambda *_args, **_kwargs: None,
    )

    assert ExecutionManager._confirmed_dual_trend_exit_candidate(
        position, {}, frame,
    ) == "ACTIVE_EXIT_DUAL_TREND_REVERSAL"


@pytest.mark.parametrize(("side", "symbol"), [
    ("LONG", "BTC/USDT"),
    ("SHORT", "1000PEPE/USDT"),
])
def test_dual_trend_exit_authority_passes_account_math_without_pivot(side, symbol):
    from types import SimpleNamespace

    from core.services.math_execution_gate import validate_exit_at_execution

    frame, opened = _frame(
        side, kc_reversed=True, macro_reversed=True,
    )
    position = {
        "side": side,
        "open_timestamp": opened,
        "entry_price": 100.0,
        "qty": 1.0,
    }
    account = SimpleNamespace(
        channel_exit_frame_provider=lambda _symbol: frame,
        position_meta={},
    )

    allowed, reason, details = validate_exit_at_execution(
        account, symbol, position, 101.0, "ACTIVE_EXIT_DUAL_TREND_REVERSAL",
    )

    assert allowed
    assert reason == "DUAL_TREND_REVERSAL_CONFIRMED"
    assert details["exit_type"] == "DUAL_TREND_REVERSAL"
    assert UltimateExitGatekeeper.can_execute_exit(
        symbol, position, {}, "ACTIVE_EXIT_DUAL_TREND_REVERSAL", 101.0, frame=frame,
    )


def test_dual_trend_exit_authority_is_blocked_if_macro_did_not_reverse():
    from types import SimpleNamespace

    from core.services.math_execution_gate import validate_exit_at_execution

    frame, opened = _frame(
        "SHORT", kc_reversed=True, macro_reversed=False,
    )
    position = {
        "side": "SHORT",
        "open_timestamp": opened,
        "entry_price": 100.0,
        "qty": 1.0,
    }
    account = SimpleNamespace(
        channel_exit_frame_provider=lambda _symbol: frame,
        position_meta={},
    )

    allowed, reason, details = validate_exit_at_execution(
        account, "1000PEPE/USDT", position, 101.0,
        "ACTIVE_EXIT_DUAL_TREND_REVERSAL",
    )

    assert not allowed
    assert reason == "VETO_EXIT_MACRO_TREND_NOT_REVERSED"
    assert details["gate"] == reason


def test_dual_trend_exit_requires_post_entry_closed_kc_confirmation():
    frame, _ = _frame(
        "LONG", kc_reversed=True, macro_reversed=True,
    )
    position = {
        "side": "LONG",
        "open_timestamp": (int(frame.iloc[-1]["timestamp"]) + 60_000) / 1000,
        "entry_price": 100.0,
        "qty": 1.0,
    }

    assert dual_trend_exit_block_reason(position, frame) == (
        "VETO_EXIT_NO_POST_ENTRY_KC_CONFIRMATION"
    )


def test_dual_trend_exit_fails_closed_when_closed_market_data_is_stale():
    frame, opened = _frame(
        "LONG", kc_reversed=True, macro_reversed=True,
    )
    latest_close = float(frame.iloc[-1]["timestamp"]) + 60_000
    frame.attrs["snapshot_ms"] = latest_close + 180_001
    position = {
        "side": "LONG",
        "open_timestamp": opened,
        "entry_price": 100.0,
        "qty": 1.0,
    }

    assert dual_trend_exit_block_reason(position, frame) == "VETO_EXIT_TREND_DATA_STALE"


def test_hard_emergency_exit_bypasses_dual_trend_gate(monkeypatch):
    from types import SimpleNamespace

    from core.gates.ultimate_short_exit_gatekeeper import (
        DEFER, UltimateShortExitGatekeeper,
    )
    import core.services.math_execution_gate as math_gate

    monkeypatch.setattr(
        UltimateShortExitGatekeeper,
        "evaluate",
        lambda *_args, **_kwargs: SimpleNamespace(verdict=DEFER, allowed=False),
    )
    monkeypatch.setattr(
        math_gate, "validate_exit_math",
        lambda *_args, **_kwargs: (True, "ACCOUNT_HARD_STOP_CONFIRMED", {}),
    )
    monkeypatch.setattr(
        "core.services.exits.authorized_position_exit.meme_strategy_disaster_disabled",
        lambda *_args, **_kwargs: False,
    )
    position = {
        "side": "LONG",
        "open_timestamp": time.time() - 600,
        "entry_price": 100.0,
        "qty": 1.0,
    }

    assert UltimateExitGatekeeper.can_execute_exit(
        "DOGE/USDT", position, {}, "ACCOUNT_HARD_STOP", 95.0,
    )


def test_non_emergency_exit_reasons_do_not_bypass_dual_trend_gate():
    assert is_dual_trend_emergency_exit("CIRCUIT_BREAKER_HARD_SL")
    assert is_dual_trend_emergency_exit("EXIT_BY_EXTREME_WATERFALL")
    assert not is_dual_trend_emergency_exit("EXIT_BY_PULLBACK")


def test_manual_close_remains_available_without_trend_data():
    position = {"side": "LONG", "entry_price": 100.0, "qty": 1.0}

    assert UltimateExitGatekeeper.can_execute_exit(
        "DOGE/USDT", position, {}, "手動平倉", 99.0, is_manual=True,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("macro_reversed", "expected_side"),
    [(True, "LONG"), (False, None)],
)
async def test_confirmed_dual_reversal_opens_opposite_after_flat(
    monkeypatch, macro_reversed, expected_side,
):
    from types import SimpleNamespace

    from core.gates.master_gate_funnel import MasterGateFunnel
    import core.services.entry_methods as entry_methods
    from core.services.execution_manager import ExecutionManager

    frame, opened = _frame(
        "SHORT", kc_reversed=True, macro_reversed=macro_reversed,
    )
    position = {
        "side": "SHORT",
        "open_timestamp": opened,
        "entry_price": 100.0,
        "qty": 1.0,
    }
    last_closed_at = float(frame.iloc[-1]["timestamp"]) / 1000 + 30
    account = SimpleNamespace(
        positions={},
        position_meta={},
        last_closed_at={"CAP/USDT": last_closed_at},
    )
    snapshot = SimpleNamespace(
        kc_middle=101.0,
        kc_middle_prev=100.0,
        macro_trend="BULLISH" if macro_reversed else "BEARISH",
        is_closed=True,
        close_price=101.0,
        open_price=100.0,
        ma5_curr=101.0,
        ma5_prev=100.0,
        kc_upper=110.0,
        kc_lower=90.0,
        atr=1.0,
    )
    monkeypatch.setattr(
        "core.gates.master_gate_funnel.market_snapshot_from_frame",
        lambda *_args, **_kwargs: snapshot,
    )
    monkeypatch.setattr(
        MasterGateFunnel, "evaluate_position_flip",
        staticmethod(lambda *_args, **_kwargs: None),
    )
    monkeypatch.setattr(
        MasterGateFunnel, "pivot_kc_location_veto",
        staticmethod(lambda *_args, **_kwargs: None),
    )
    allow = SimpleNamespace(can_execute=True, reason="PASS")
    monkeypatch.setattr(
        MasterGateFunnel, "evaluate_open_long",
        staticmethod(lambda *_args, **_kwargs: allow),
    )
    signal = {
        "method": "KC_DUAL_TREND_LOCATION",
        "side": "LONG",
        "event_id": "dual-trend-flip",
        "confirmation_bar_id": int(frame.iloc[-1]["timestamp"]),
        "entry_pattern": "REVERSAL",
        "structure_confirmation": "KC_DUAL_TREND_LOCATION",
        "structure_confirmed": True,
        "channel_expanding": False,
        "entry_atr": 1.0,
    }
    monkeypatch.setattr(
        entry_methods, "evaluate_entry_method",
        lambda *_args, **_kwargs: signal,
    )
    monkeypatch.setattr(
        entry_methods, "revalidate_entry_method",
        lambda *_args, **_kwargs: (True, "ENTRY_METHOD_REVALIDATED"),
    )

    async def fake_open_position(
        _cls, engine_or_account, symbol, side, price, reason, meta=None,
    ):
        assert symbol not in engine_or_account.positions
        assert meta["entry_method_authority"] == "KC_DUAL_TREND_LOCATION"
        assert meta["is_flip"] is True
        engine_or_account.positions[symbol] = {"side": side}
        return True

    monkeypatch.setattr(
        ExecutionManager, "open_position",
        classmethod(fake_open_position),
    )

    result = await ExecutionManager._try_structure_position_flip(
        account=account,
        symbol="CAP/USDT",
        position_side="SHORT",
        frame=frame,
        quote=101.0,
        macro_trend=snapshot.macro_trend,
        exit_reason="ACTIVE_EXIT_DUAL_TREND_REVERSAL",
        position=position,
    )

    if expected_side is None:
        assert result is None
        assert not account.positions
    else:
        assert result["success"] is True
        assert result["side"] == expected_side
        assert account.positions["CAP/USDT"]["side"] == expected_side
