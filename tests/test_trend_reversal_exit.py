import asyncio
import math
import time
from types import SimpleNamespace

import pandas as pd
import pytest

import core.gates.direction_master_lock as direction_master_lock
import core.gates.master_gate_funnel as master_gate_funnel
from core.gates.ultimate_short_exit_gatekeeper import (
    KC_MIDDLE_SLOPE_EXIT_REASONS,
    MA5_ANGLE_REVERSAL_EXIT_REASONS,
    SHARP_REVERSAL_EXIT_REASONS,
    UltimateShortExitGatekeeper,
)
from core.services.math_execution_gate import validate_exit_at_execution, validate_exit_math
from core.gates.unified_pipeline_gates import ExitGate
from core.gates.holding_protection_gate import HoldingProtectionExitGate
from core.services.execution_manager import ExecutionManager


@pytest.mark.parametrize(
    ("side", "rows", "reason"),
    [
        (
            "LONG",
            [
                {"open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5,
                 "ma5": 100.8, "ma15": 101.0, "kc_middle": 100.6, "atr": 1.0,
                 "kc_upper": 102.0, "kc_lower": 99.0},
                {"open": 100.5, "high": 100.6, "low": 98.9, "close": 99.0,
                 "ma5": 99.8, "ma15": 100.0, "kc_middle": 100.0, "atr": 1.0,
                 "kc_upper": 101.0, "kc_lower": 98.0},
            ],
            SHARP_REVERSAL_EXIT_REASONS["LONG"],
        ),
        (
            "SHORT",
            [
                {"open": 100.0, "high": 100.5, "low": 99.0, "close": 99.5,
                 "ma5": 99.2, "ma15": 99.0, "kc_middle": 99.4, "atr": 1.0,
                 "kc_upper": 101.0, "kc_lower": 98.0},
                {"open": 99.5, "high": 101.1, "low": 99.4, "close": 101.0,
                 "ma5": 100.2, "ma15": 100.0, "kc_middle": 100.0, "atr": 1.0,
                 "kc_upper": 102.0, "kc_lower": 99.0},
            ],
            SHARP_REVERSAL_EXIT_REASONS["SHORT"],
        ),
    ],
)
def test_sharp_reversal_exit_requires_closed_15_atr_bar(side, rows, reason):
    now = time.time()
    latest_minute = int(now // 60)
    timestamps = [
        (latest_minute - 2) * 60_000,
        (latest_minute - 1) * 60_000,
    ]
    frame = pd.DataFrame([
        {**row, "timestamp": stamp, "is_closed": True}
        for row, stamp in zip(rows, timestamps)
    ])
    frame.attrs["timeframe_ms"] = 60_000
    position = {
        "symbol": "龙虾/USDT",
        "side": side,
        "entry_price": 100.0,
        "qty": 1.0,
        "open_timestamp": now - 300,
    }

    candidate = UltimateShortExitGatekeeper.sharp_reversal_exit_candidate(
        position, frame, now=now,
    )
    verdict = UltimateShortExitGatekeeper.evaluate(
        "龙虾/USDT", position, {}, reason, rows[-1]["close"], frame=frame, now=now,
    )

    assert candidate == reason
    assert verdict.allowed is True
    assert verdict.code == reason
    assert verdict.evidence["body"] == pytest.approx(1.5)
    assert verdict.evidence["threshold_atr"] == 1.5


def test_sharp_reversal_exit_rejects_small_or_unclosed_bar():
    now = time.time()
    latest_minute = int(now // 60)
    rows = [
        {"open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5,
         "ma5": 100.8, "ma15": 101.0, "kc_middle": 100.6, "atr": 1.0,
         "kc_upper": 102.0, "kc_lower": 99.0},
        {"open": 100.5, "high": 100.6, "low": 99.1, "close": 99.1,
         "ma5": 99.8, "ma15": 100.0, "kc_middle": 100.0, "atr": 1.0,
         "kc_upper": 101.0, "kc_lower": 98.0},
    ]
    frame = pd.DataFrame([
        {**row, "timestamp": (latest_minute - 2 + i) * 60_000, "is_closed": True}
        for i, row in enumerate(rows)
    ])
    frame.attrs["timeframe_ms"] = 60_000
    position = {
        "side": "LONG", "entry_price": 100.0, "qty": 1.0,
        "open_timestamp": now - 300,
    }

    assert UltimateShortExitGatekeeper.sharp_reversal_exit_candidate(
        position, frame, now=now,
    ) is None

    frame.loc[1, "is_closed"] = False
    assert UltimateShortExitGatekeeper.sharp_reversal_exit_candidate(
        position, frame, now=now,
    ) is None


@pytest.mark.parametrize(
    ("side", "ma5_step", "expected_side"),
    [
        ("LONG", 100.0 * math.tan(math.radians(75.1)) / 1000.0, "LONG"),
        ("SHORT", -100.0 * math.tan(math.radians(75.1)) / 1000.0, "SHORT"),
    ],
)
def test_ma5_angle_entry_candidate_uses_live_quote_adjusted_ma5(
    side, ma5_step, expected_side,
):
    now = time.time()
    latest_minute = int(now // 60)
    frame = pd.DataFrame([
        {
            "timestamp": (latest_minute - 2) * 60_000,
            "open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0,
            "ma5": 100.0, "is_closed": True,
        },
        {
            "timestamp": (latest_minute - 1) * 60_000,
            "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0,
            "ma5": 100.0, "is_closed": True,
        },
        {
            "timestamp": latest_minute * 60_000,
            "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0,
            "ma5": 100.0, "is_closed": False,
        },
    ])
    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["snapshot_ms"] = now * 1000

    quote = 100.0 + 5.0 * ma5_step
    assert UltimateShortExitGatekeeper.ma5_angle_opportunity(
        frame, quote, now=now,
    )[0] == expected_side


def test_ma5_angle_reversal_exit_is_live_and_does_not_require_large_body():
    now = time.time()
    latest_minute = int(now // 60)
    step = 100.0 * math.tan(math.radians(75.1)) / 1000.0
    frame = pd.DataFrame([
        {
            "timestamp": (latest_minute - 2) * 60_000,
            "open": 100.0, "high": 100.3, "low": 99.7, "close": 100.0,
            "ma5": 100.0, "ma15": 100.5, "atr": 1.0,
            "kc_upper": 102.0, "kc_middle": 100.0, "kc_lower": 98.0,
            "is_closed": True,
        },
        {
            "timestamp": (latest_minute - 1) * 60_000,
            "open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0,
            "ma5": 100.0, "ma15": 100.5, "atr": 1.0,
            "kc_upper": 102.0, "kc_middle": 100.1, "kc_lower": 98.0,
            "is_closed": True,
        },
        {
            "timestamp": latest_minute * 60_000,
            "open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0,
            "ma5": 100.0, "ma15": 100.5, "atr": 1.0,
            "kc_upper": 102.0, "kc_middle": 100.1, "kc_lower": 98.0,
            "is_closed": False,
        },
    ])
    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["snapshot_ms"] = now * 1000
    position = {
        "side": "LONG", "entry_price": 100.0, "qty": 1.0,
        "open_timestamp": now - 300,
    }

    quote = 100.0 - 5.0 * step
    reason = UltimateShortExitGatekeeper.ma5_angle_exit_candidate(
        position, frame, quote=quote, now=now,
    )
    verdict = UltimateShortExitGatekeeper.evaluate(
        "龙虾/USDT", position, {}, reason, quote, frame=frame,
        is_bar_closed=False, now=now,
    )

    assert reason == MA5_ANGLE_REVERSAL_EXIT_REASONS["LONG"]
    assert verdict.allowed is True
    assert verdict.evidence["angle"] <= -75.0


def test_ma5_angle_requires_fresh_live_minute_and_snapshot():
    now = time.time()
    latest_minute = int(now // 60)
    step = 100.0 * math.tan(math.radians(75.1)) / 1000.0
    frame = pd.DataFrame([
        {
            "timestamp": (latest_minute - 1) * 60_000,
            "open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0,
            "ma5": 100.0, "is_closed": True,
        },
        {
            "timestamp": latest_minute * 60_000,
            "open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0,
            "ma5": 100.0, "is_closed": False,
        },
    ])
    frame.attrs.update(timeframe_ms=60_000, snapshot_ms=now * 1000)
    quote = 100.0 + 5.0 * step
    assert UltimateShortExitGatekeeper.ma5_angle_opportunity(
        frame, quote, now=now,
    ) is not None

    frame.attrs["snapshot_ms"] = (latest_minute + 1) * 60_000
    assert UltimateShortExitGatekeeper.ma5_angle_opportunity(
        frame, quote, now=now,
    ) is None

    frame.attrs.update(timeframe_ms=300_000, snapshot_ms=now * 1000)
    assert UltimateShortExitGatekeeper.ma5_angle_opportunity(
        frame, quote, now=now,
    ) is None


def test_ma5_angle_latch_is_persisted_and_blocks_repeat_for_same_candle():
    persisted = {}
    account = SimpleNamespace(account_id="test-account", entry_filter_last={})

    def save_state():
        persisted.update(account.entry_filter_last)

    account.save_state = save_state
    candle_id = 1_800_000_000_000

    assert asyncio.run(ExecutionManager._claim_ma5_angle_latch(
        account, "龍蝦/USDT", "SHORT", candle_id,
    ))
    assert not asyncio.run(ExecutionManager._claim_ma5_angle_latch(
        account, "龍蝦/USDT", "LONG", candle_id,
    ))

    restored = SimpleNamespace(
        account_id="test-account",
        entry_filter_last=persisted,
        save_state=lambda: None,
    )
    assert ExecutionManager._ma5_angle_latched(
        restored, "龍蝦/USDT", candle_id,
    )
    assert asyncio.run(ExecutionManager._claim_ma5_angle_latch(
        restored, "龍蝦/USDT", "LONG", candle_id + 60_000,
    ))


@pytest.mark.parametrize("macro_allowed", [True, False])
def test_live_angle_reverse_keeps_macro_and_master_entry_gates(monkeypatch, macro_allowed):
    now = time.time()
    latest_minute = int(now // 60)
    frame = pd.DataFrame([
        {
            "timestamp": (latest_minute - 1) * 60_000,
            "open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0,
            "ma5": 100.0, "ma15": 100.1, "atr": 1.0,
            "kc_upper": 102.0, "kc_middle": 100.0, "kc_lower": 98.0,
            "is_closed": True,
        },
        {
            "timestamp": latest_minute * 60_000,
            "open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0,
            "ma5": 100.0, "ma15": 100.1, "atr": 1.0,
            "kc_upper": 102.0, "kc_middle": 100.0, "kc_lower": 98.0,
            "is_closed": False,
        },
    ])
    frame.attrs.update(timeframe_ms=60_000, snapshot_ms=now * 1000)
    snapshot = SimpleNamespace(
        kc_middle=99.0, kc_middle_prev=100.0, macro_trend="BEARISH",
        ma5_curr=99.0, ma5_prev=100.0, ma15_curr=101.0, ma15_prev=101.0,
        close_price=100.0, open_price=100.0, kc_upper=102.0, kc_lower=98.0,
        atr=1.0, is_closed=False,
    )
    symbol = "龍蝦/USDT"
    account = SimpleNamespace(
        account_id=f"angle-flip-{macro_allowed}",
        positions={},
        position_meta={},
    )
    macro_calls = []
    open_calls = []

    monkeypatch.setattr(
        master_gate_funnel,
        "market_snapshot_from_frame",
        lambda *_args, **_kwargs: snapshot,
    )
    monkeypatch.setattr(
        UltimateShortExitGatekeeper,
        "ma5_angle_opportunity",
        classmethod(lambda cls, *_args, **_kwargs: ("SHORT", -80.0, latest_minute * 60_000)),
    )
    monkeypatch.setattr(
        direction_master_lock,
        "evaluate_direction_master_lock",
        lambda *_args, **_kwargs: (True, "PASS", {}),
    )
    monkeypatch.setattr(
        master_gate_funnel.MasterGateFunnel,
        "pivot_kc_location_veto",
        staticmethod(lambda *_args, **_kwargs: None),
    )
    monkeypatch.setattr(
        master_gate_funnel.MasterGateFunnel,
        "evaluate_open_short",
        classmethod(lambda cls, *_args, **_kwargs: SimpleNamespace(
            can_execute=True, reason="PASS",
        )),
    )

    from core.gates.macro_candle_structure_gate import MacroCandleStructureGate

    def macro_gate(self, side, *_args, **_kwargs):
        macro_calls.append(side)
        return macro_allowed, "PASS" if macro_allowed else "MACRO_VETO"

    monkeypatch.setattr(MacroCandleStructureGate, "can_open", macro_gate)

    async def open_position(cls, *, engine_or_account, symbol, side, price, reason, meta):
        open_calls.append((side, meta))
        account.positions[symbol] = {"side": side}
        return True

    monkeypatch.setattr(ExecutionManager, "open_position", classmethod(open_position))
    result = asyncio.run(ExecutionManager._try_structure_position_flip(
        account=account,
        symbol=symbol,
        position_side="LONG",
        frame=frame,
        quote=99.0,
        macro_trend="BEARISH",
        exit_reason=MA5_ANGLE_REVERSAL_EXIT_REASONS["LONG"],
    ))

    assert macro_calls == ["SHORT"]
    assert bool(open_calls) is macro_allowed
    if macro_allowed:
        assert result["success"] is True
        assert open_calls[0][1]["entry_pattern"] == "REVERSAL"
        assert not open_calls[0][1].get("direction_override", False)
    else:
        assert result["reason"] == "MACRO_VETO"


@pytest.mark.parametrize(
    ("side", "rows", "reason"),
    [
        (
            "LONG",
            [
                {"open": 100.0, "high": 100.3, "low": 99.7, "close": 100.1,
                 "ma5": 100.0, "ma15": 100.0, "kc_middle": 100.0, "atr": 1.0,
                 "kc_upper": 102.0, "kc_lower": 98.0},
                {"open": 100.1, "high": 100.4, "low": 99.8, "close": 100.2,
                 "ma5": 100.1, "ma15": 100.0, "kc_middle": 99.9, "atr": 1.0,
                 "kc_upper": 102.0, "kc_lower": 98.0},
            ],
            KC_MIDDLE_SLOPE_EXIT_REASONS["LONG"],
        ),
        (
            "SHORT",
            [
                {"open": 100.0, "high": 100.3, "low": 99.7, "close": 99.9,
                 "ma5": 100.0, "ma15": 100.0, "kc_middle": 100.0, "atr": 1.0,
                 "kc_upper": 102.0, "kc_lower": 98.0},
                {"open": 99.9, "high": 100.2, "low": 99.6, "close": 99.8,
                 "ma5": 99.9, "ma15": 100.0, "kc_middle": 100.1, "atr": 1.0,
                 "kc_upper": 102.0, "kc_lower": 98.0},
            ],
            KC_MIDDLE_SLOPE_EXIT_REASONS["SHORT"],
        ),
    ],
)
def test_kc_middle_slope_exit_is_authorized_from_closed_pair(side, rows, reason):
    now = time.time()
    latest_minute = int(now // 60)
    frame = pd.DataFrame([
        {**row, "timestamp": (latest_minute - 2 + i) * 60_000, "is_closed": True}
        for i, row in enumerate(rows)
    ])
    frame.attrs["timeframe_ms"] = 60_000
    position = {
        "side": side, "entry_price": 100.0, "qty": 1.0,
        "open_timestamp": now - 300,
    }

    assert UltimateShortExitGatekeeper.kc_middle_slope_exit_candidate(
        position, frame, now=now,
    ) == reason
    verdict = UltimateShortExitGatekeeper.evaluate(
        "龙虾/USDT", position, {}, reason, rows[-1]["close"], frame=frame, now=now,
    )
    assert verdict.allowed is True
    assert verdict.code == reason


def test_ultimate_short_exit_gatekeeper_trend_reversal_long():
    """Verify holding LONG exits on confirmed KC downward + MA5 < MA15 dead cross below mid."""
    now = time.time()
    t1 = (int(now // 60) - 2) * 60_000
    t2 = (int(now // 60) - 1) * 60_000

    rows = [
        {
            "timestamp": t1, "open": 0.01760, "high": 0.01765, "low": 0.01750,
            "close": 0.01752, "ma5": 0.01755, "ma15": 0.01750, "atr": 0.00010,
            "kc_upper": 0.01770, "kc_middle": 0.01755, "kc_lower": 0.01740,
            "is_closed": True,
        },
        {
            "timestamp": t2, "open": 0.01750, "high": 0.01750, "low": 0.01735,
            "close": 0.01738, "ma5": 0.01742, "ma15": 0.01748, "atr": 0.00010,
            "kc_upper": 0.01765, "kc_middle": 0.01750, "kc_lower": 0.01735,
            "is_closed": True,
        },
    ]
    df = pd.DataFrame(rows)
    df.attrs["timeframe_ms"] = 60_000
    df.attrs["snapshot_ms"] = t2 + 5000
    df.attrs["macro_trend"] = "BEARISH"

    position = {
        "symbol": "龙虾/USDT",
        "side": "LONG",
        "entry_price": 0.01755,
        "qty": 1000.0,
        "open_timestamp": (now - 300),
    }

    verdict = UltimateShortExitGatekeeper.evaluate(
        "龙虾/USDT", position, {}, "STRUCTURE_EXIT_PROBE", 0.01738, frame=df, now=now,
    )
    assert verdict.allowed is True
    assert verdict.code == "EXIT_LONG_TREND_REVERSAL"


def test_ultimate_short_exit_gatekeeper_trend_reversal_short():
    """Verify holding SHORT exits on confirmed KC upward + MA5 > MA15 golden cross above mid."""
    now = time.time()
    t1 = (int(now // 60) - 2) * 60_000
    t2 = (int(now // 60) - 1) * 60_000

    rows = [
        {
            "timestamp": t1, "open": 0.01740, "high": 0.01745, "low": 0.01735,
            "close": 0.01742, "ma5": 0.01740, "ma15": 0.01745, "atr": 0.00010,
            "kc_upper": 0.01760, "kc_middle": 0.01745, "kc_lower": 0.01730,
            "is_closed": True,
        },
        {
            "timestamp": t2, "open": 0.01745, "high": 0.01765, "low": 0.01745,
            "close": 0.01760, "ma5": 0.01755, "ma15": 0.01748, "atr": 0.00010,
            "kc_upper": 0.01765, "kc_middle": 0.01750, "kc_lower": 0.01735,
            "is_closed": True,
        },
    ]
    df = pd.DataFrame(rows)
    df.attrs["timeframe_ms"] = 60_000
    df.attrs["snapshot_ms"] = t2 + 5000
    df.attrs["macro_trend"] = "BULLISH"

    position = {
        "symbol": "1000PEPE/USDT",
        "side": "SHORT",
        "entry_price": 0.01745,
        "qty": 1000.0,
        "open_timestamp": (now - 300),
    }

    verdict = UltimateShortExitGatekeeper.evaluate(
        "1000PEPE/USDT", position, {}, "STRUCTURE_EXIT_PROBE", 0.01760, frame=df, now=now,
    )
    assert verdict.allowed is True
    assert verdict.code == "EXIT_SHORT_TREND_REVERSAL"


@pytest.mark.parametrize(
    ("side", "reason", "entry", "quote", "peak_or_trough", "rows"),
    [
        (
            "LONG",
            "EXIT_LONG_CONSECUTIVE_BEAR_CANDLES_PIVOT_TURN",
            110.0,
            99.0,
            120.0,
            [
                {"open": 101.0, "high": 102.0, "low": 100.0, "close": 100.5,
                 "ma5": 100.5, "ma15": 100.8, "kc_middle": 100.0},
                {"open": 100.5, "high": 100.6, "low": 98.5, "close": 99.0,
                 "ma5": 99.5, "ma15": 100.0, "kc_middle": 99.5},
            ],
        ),
        (
            "SHORT",
            "EXIT_SHORT_CONSECUTIVE_BULL_CANDLES_PIVOT_TURN",
            90.0,
            101.0,
            80.0,
            [
                {"open": 99.0, "high": 100.0, "low": 98.0, "close": 99.5,
                 "ma5": 99.5, "ma15": 99.2, "kc_middle": 100.0},
                {"open": 99.5, "high": 101.5, "low": 99.4, "close": 101.0,
                 "ma5": 100.5, "ma15": 100.0, "kc_middle": 100.5},
            ],
        ),
    ],
)
def test_consecutive_pivot_exit_reasons_are_vetoed_without_profit(
    side, reason, entry, quote, peak_or_trough, rows,
):
    now = time.time()
    latest_minute = int(now // 60)
    timestamps = [
        (latest_minute - 2) * 60_000,
        (latest_minute - 1) * 60_000,
    ]
    frame = pd.DataFrame([
        {
            **row,
            "timestamp": timestamp,
            "atr": 1.0,
            "kc_upper": row["kc_middle"] + 2.0,
            "kc_lower": row["kc_middle"] - 2.0,
            "is_closed": True,
        }
        for row, timestamp in zip(rows, timestamps)
    ])
    position = {
        "symbol": "龙虾/USDT",
        "side": side,
        "entry_price": entry,
        "qty": 1.0,
        "open_timestamp": now - 300,
        "highest_price" if side == "LONG" else "lowest_price": peak_or_trough,
    }

    verdict = UltimateShortExitGatekeeper.evaluate(
        "龙虾/USDT",
        position,
        {},
        reason,
        quote,
        frame=frame,
        now=now,
    )

    assert verdict.allowed is False
    account = SimpleNamespace(
        position_meta={"龙虾/USDT": {}},
        _channel_exit_frames={"龙虾/USDT": frame},
    )
    allowed, _code, _details = validate_exit_at_execution(
        account, "龙虾/USDT", position, quote, reason,
    )
    assert allowed is False


def test_validate_exit_math_trend_reversal():
    """Verify validate_exit_math authorizes trend reversal exits."""
    rows = [
        {"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 99, "ma5": 99.5, "ma15": 100, "atr": 1.0, "kc_upper": 102, "kc_middle": 100, "kc_lower": 98},
        {"timestamp": 2000, "open": 99, "high": 99.5, "low": 97, "close": 97.5, "ma5": 98, "ma15": 99.5, "atr": 1.0, "kc_upper": 101, "kc_middle": 99, "kc_lower": 97},
    ]
    df = pd.DataFrame(rows)
    pos = {"side": "LONG", "entry_price": 100.0, "open_timestamp": 0}

    allowed, code, details = validate_exit_math(
        pos, df, 97.5, close_reason="EXIT_LONG_TREND_REVERSAL",
    )
    assert allowed is True
    assert "TREND_REVERSAL" in code


def test_exit_gate_trend_reversal_safety_reason():
    """Verify EXIT_LONG_TREND_REVERSAL and EXIT_SHORT_TREND_REVERSAL are in ABSOLUTE_SAFETY_REASONS."""
    assert "EXIT_LONG_TREND_REVERSAL" in ExitGate.ABSOLUTE_SAFETY_REASONS
    assert "EXIT_SHORT_TREND_REVERSAL" in ExitGate.ABSOLUTE_SAFETY_REASONS
    assert "EXIT_LONG_TREND_REVERSAL" in HoldingProtectionExitGate.WHITELIST_CODES
    assert "EXIT_SHORT_TREND_REVERSAL" in HoldingProtectionExitGate.WHITELIST_CODES
    for reason in SHARP_REVERSAL_EXIT_REASONS.values():
        assert reason in ExitGate.ABSOLUTE_SAFETY_REASONS
        assert reason in HoldingProtectionExitGate.WHITELIST_CODES
    for reason in (
        *MA5_ANGLE_REVERSAL_EXIT_REASONS.values(),
        *KC_MIDDLE_SLOPE_EXIT_REASONS.values(),
    ):
        assert reason in ExitGate.ABSOLUTE_SAFETY_REASONS
        assert reason in HoldingProtectionExitGate.WHITELIST_CODES
