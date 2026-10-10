import pandas as pd
import pytest

from core.services.entry_contract import (
    PULLBACK_LONG_CODE,
    PULLBACK_SHORT_CODE,
    evaluate_entry_contract,
    execution_band_problem,
)
from core.services.strict_entry_gates import validate_strict_entry


def pullback_frame(side):
    rows = []
    start = 1_800_000_000_000
    for index in range(58):
        stamp = start + index * 60_000
        rows.append(dict(
            timestamp=stamp, open=100., high=101., low=99., close=100.5,
            atr=2., ma5=100., ma15=99., kc_lower=95., kc_middle=100.,
            kc_upper=105., is_closed=True,
        ))

    if side == "SHORT":
        rows.extend([
            dict(timestamp=start + 58 * 60_000, open=100., high=101.,
                 low=97., close=98., atr=2., ma5=99., ma15=100.,
                 kc_lower=99., kc_middle=100., kc_upper=101., is_closed=True),
            dict(timestamp=start + 59 * 60_000, open=99., high=100.,
                 low=96., close=97., atr=2., ma5=98., ma15=100.,
                 kc_lower=95., kc_middle=100., kc_upper=105., is_closed=True),
            dict(timestamp=start + 60 * 60_000, open=98., high=98.5,
                 low=97., close=98., atr=2., ma5=98., ma15=100.,
                 kc_lower=95., kc_middle=100., kc_upper=105., is_closed=False),
        ])
    else:
        rows.extend([
            dict(timestamp=start + 58 * 60_000, open=100., high=103.,
                 low=99., close=102., atr=2., ma5=101., ma15=100.,
                 kc_lower=99., kc_middle=100., kc_upper=101., is_closed=True),
            dict(timestamp=start + 59 * 60_000, open=101., high=104.,
                 low=100., close=103., atr=2., ma5=102., ma15=100.,
                 kc_lower=95., kc_middle=100., kc_upper=105., is_closed=True),
            dict(timestamp=start + 60 * 60_000, open=102., high=103.,
                 low=101.5, close=102., atr=2., ma5=102., ma15=100.,
                 kc_lower=95., kc_middle=100., kc_upper=105., is_closed=False),
        ])

    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000
    return frame


@pytest.mark.parametrize(
    ("side", "quote", "expected"),
    [
        ("SHORT", 95., "BLOCK_OVERSOLD_OUTSIDE_KC"),
        ("SHORT", 94., "BLOCK_OVERSOLD_OUTSIDE_KC"),
        ("LONG", 105., "BLOCK_OVERBOUGHT_OUTSIDE_KC"),
        ("LONG", 106., "BLOCK_OVERBOUGHT_OUTSIDE_KC"),
    ],
)
def test_execution_guard_blocks_outer_rail_and_beyond(side, quote, expected):
    frame = pullback_frame(side)
    assert execution_band_problem(frame, side, quote) == expected


@pytest.mark.parametrize(
    ("side", "code"),
    [("LONG", PULLBACK_LONG_CODE), ("SHORT", PULLBACK_SHORT_CODE)],
)
def test_pullback_entry_requires_rejection_and_inside_kc(side, code, monkeypatch):
    import core.services.entry_contract as contract

    monkeypatch.setattr(
        contract, "check_entry_gates",
        lambda *args, **kwargs: (True, "STRICT_ENTRY_GATES_PASSED"),
    )
    monkeypatch.setattr(
        contract, "validate_strict_entry",
        lambda *args, **kwargs: (True, "STRICT_ENTRY_GATES_PASSED", {"passed": True}),
    )
    frame = pullback_frame(side)
    quote = 97.5 if side == "SHORT" else 102.5
    if side == "LONG":
        frame.loc[60, "high"] = quote
    diagnostics = {}

    decision = evaluate_entry_contract(
        frame, quote, code, symbol="TEST/USDT", diagnostics=diagnostics,
    )

    assert decision is not None
    assert decision["side"] == side
    assert decision["entry_phase"] == "KC_PULLBACK_CONFIRMED"
    assert decision["pullback_reference"] == "ma5"
    assert diagnostics["type"] == code


@pytest.mark.parametrize(
    ("side", "quote", "expected"),
    [
        ("SHORT", 95., "BLOCK_OVERSOLD_OUTSIDE_KC"),
        ("LONG", 105., "BLOCK_OVERBOUGHT_OUTSIDE_KC"),
    ],
)
def test_pullback_code_cannot_bypass_outer_rail_execution_guard(side, quote, expected):
    frame = pullback_frame(side)
    diagnostics = {}

    decision = evaluate_entry_contract(
        frame, quote,
        PULLBACK_SHORT_CODE if side == "SHORT" else PULLBACK_LONG_CODE,
        symbol="TEST/USDT", diagnostics=diagnostics,
    )

    assert decision is None
    assert diagnostics["reason"] == expected


def test_pullback_without_rejected_ma_or_midline_is_not_authorized(monkeypatch):
    import core.services.entry_contract as contract

    monkeypatch.setattr(
        contract, "check_entry_gates",
        lambda *args, **kwargs: (True, "STRICT_ENTRY_GATES_PASSED"),
    )
    monkeypatch.setattr(
        contract, "validate_strict_entry",
        lambda *args, **kwargs: (True, "STRICT_ENTRY_GATES_PASSED", {"passed": True}),
    )
    frame = pullback_frame("SHORT")
    frame.loc[59, ["open", "high", "low", "close"]] = [97., 97.5, 96.5, 96.5]
    diagnostics = {}

    decision = evaluate_entry_contract(
        frame, 97.5, PULLBACK_SHORT_CODE, symbol="TEST/USDT",
        diagnostics=diagnostics,
    )

    assert decision is None
    assert diagnostics["reason"] == "WAIT_PULLBACK_MA5_OR_MIDLINE_REJECTION"


@pytest.mark.parametrize(("side", "quote", "pivot_column", "pivot_value"), [
    ("LONG", 102.5, "high", 110.),
    ("SHORT", 97.5, "low", 90.),
])
def test_strict_gate_allows_only_confirmed_inside_band_pullback(
    side, quote, pivot_column, pivot_value,
):
    frame = pullback_frame(side)
    frame.loc[20, pivot_column] = pivot_value
    if side == "LONG":
        frame.loc[60, ["ma5", "ma15"]] = [102.2, 99.]
    else:
        frame.loc[60, ["ma5", "ma15"]] = [97.8, 100.5]

    passed, reason, _ = validate_strict_entry(
        frame, quote, side, entry_mode="PULLBACK",
    )

    assert passed, reason
    passed, reason, _ = validate_strict_entry(frame, quote, side)
    assert not passed
    assert reason == "BLOCKED_STRICT_QUOTE_INSIDE_KC"


@pytest.mark.parametrize(
    ("side", "code", "quote"),
    [
        ("LONG", PULLBACK_LONG_CODE, 102.5),
        ("SHORT", PULLBACK_SHORT_CODE, 97.5),
    ],
)
def test_account_firewall_revalidates_confirmed_pullback(side, code, quote):
    import asyncio
    import time
    from types import SimpleNamespace

    from core.services.entry_firewall import validate_account_entry

    frame = pullback_frame(side)
    if side == "LONG":
        frame.loc[20, "high"] = 110.
    else:
        frame.loc[20, "low"] = 90.
    current_bar = int(time.time() // 60) * 60000
    frame["timestamp"] += current_bar - float(frame.iloc[-1]["timestamp"])
    frame.loc[60, ["kc_lower", "kc_upper"]] = [94., 106.]
    frame.loc[60, "close"] = quote
    frame.loc[60, "high"] = max(float(frame.loc[60, "high"]), quote)
    frame.loc[60, "low"] = min(float(frame.loc[60, "low"]), quote)
    if side == "LONG":
        frame.loc[60, "high"] = quote
        frame.loc[60, ["ma5", "ma15"]] = [102.2, 99.]
    else:
        frame.loc[60, ["ma5", "ma15"]] = [97.8, 101.5]
    frame.attrs["entry_finality_verified"] = True
    frame.attrs["entry_finality_server_ms"] = current_bar

    async def provide_frame(_symbol):
        return frame

    account = SimpleNamespace(
        entry_frame_provider=provide_frame, positions={}, trades=[],
    )
    passed, reason, _ = validate_strict_entry(
        frame, quote, side, entry_mode="PULLBACK",
    )
    assert passed, reason
    context = {
        "entry_signal_code": code,
        "channel_confirmation_bar_id": float(frame.iloc[-2]["timestamp"]),
    }
    decision = asyncio.run(
        validate_account_entry(account, "TEST/USDT", side, context),
    )

    assert decision["type"] == code
    assert decision["price"] == quote
