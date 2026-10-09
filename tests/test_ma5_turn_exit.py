import copy
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pandas as pd
import pytest

from core.services.exits.peak_trailing_exit import (
    ABNORMAL_REASON,
    PEAK_REASON,
    evaluate_peak_trailing,
    live_ma5_reversal_exit,
)
from core.services.exits.realtime_profit_exit import cached_tick_indicators


def position(side):
    return dict(
        symbol="TEST/USDT",
        side=side,
        entry_price=100.,
        qty=1.,
        margin=100.,
        leverage=1.,
        open_timestamp=60.,
        entry_mode="CHANNEL_SWING",
        entry_atr=1.,
    )


def snapshot(side, **overrides):
    closed_ma5 = 100.
    live_ma5 = 101. if side == "SHORT" else 99.
    result = dict(
        quote_ms=181_000.,
        snapshot_bar_id=120_000.,
        closed_bar_ms=120_000.,
        live_bar_ms=180_000.,
        closed_ma5=closed_ma5,
        live_ma5=live_ma5,
        live_kc_upper=100.5 if side == "LONG" else 101.5,
        live_kc_lower=99.5 if side == "SHORT" else 98.5,
        reason=None,
    )
    result.update(overrides)
    return result


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_observed_ma5_peak_or_trough_reversal_exits_both_sides(side):
    sign = 1 if side == "LONG" else -1
    position_data = position(side)
    # First establish a quote-derived extreme, then observe favorable MA5
    # progress, then close on the first MA5 retreat confirmed by price.
    for index, (ma5, price) in enumerate(((100., 100.), (101. * sign + 100. * (1-sign), 101. if sign == 1 else 99.))):
        result = evaluate_peak_trailing(
            position_data, price,
            snapshot(side, quote_ms=181_000. + index * 1_000, live_ma5=ma5),
            fee=0., slippage=0.,
        )
        assert result is None

    reversal_ma5 = 100.8 if sign == 1 else 99.2
    reversal_price = 100.9 if sign == 1 else 99.1
    result = evaluate_peak_trailing(
        position_data, reversal_price,
        snapshot(side, quote_ms=183_000., live_ma5=reversal_ma5),
        fee=0., slippage=0.,
    )

    assert result is not None
    assert result["type"] == ABNORMAL_REASON
    assert result["trigger"] == "MA5_TRUE_PEAK_REVERSAL"
    assert position_data["peak_trailing_state"]["pending"] == ABNORMAL_REASON
    assert position_data["peak_trailing_state"]["trigger"] == "MA5_TRUE_PEAK_REVERSAL"


@pytest.mark.parametrize(
    ("side", "live_ma5"),
    [("LONG", 100.), ("LONG", 101.), ("SHORT", 100.), ("SHORT", 99.)],
)
def test_flat_or_favorable_ma5_does_not_trigger_exit(side, live_ma5):
    evidence = live_ma5_reversal_exit(
        position(side), snapshot(side, live_ma5=live_ma5),
        1 if side == "LONG" else -1,
    )

    assert evidence is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_ma5_reversal_inside_outer_rail_is_not_a_peak_exit(side):
    sign = 1 if side == "LONG" else -1
    state = {}
    sequence = (
        (100., 100.),
        (101. if sign == 1 else 99., 101. if sign == 1 else 99.),
        (100.8 if sign == 1 else 99.2, 100.9 if sign == 1 else 99.1),
    )

    results = []
    for index, (ma5, price) in enumerate(sequence):
        result = snapshot(
            side,
            quote_ms=181_000. + index * 1_000,
            live_ma5=ma5,
            live_kc_upper=102. if side == "LONG" else 101.5,
            live_kc_lower=98.5,
            quote_price=price,
        )
        results.append(
            live_ma5_reversal_exit(position(side), result, sign, state)
        )

    assert results == [None, None, None]
    assert state["ma5_reversal_outside_seen"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {"reason": "STALE_SNAPSHOT"},
        {"live_bar_ms": 120_000.},
        {"closed_bar_ms": 60_000.},
        {"snapshot_bar_id": 60_000.},
        {"quote_ms": 300_001.},
        {"live_ma5": float("nan")},
        {"closed_ma5": 0.},
    ],
)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_invalid_or_stale_ma5_snapshot_cannot_create_a_close(side, changes):
    result = live_ma5_reversal_exit(
        position(side), snapshot(side, **changes), 1 if side == "LONG" else -1
    )

    assert result is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_triggered_ma5_exit_remains_pending_for_failed_close_retry(side):
    position_data = position(side)
    first = None
    sign = 1 if side == "LONG" else -1
    for index, (ma5, price) in enumerate(((100., 100.), (101. if sign == 1 else 99., 101. if sign == 1 else 99.),
                                          (100.8 if sign == 1 else 99.2, 100.9 if sign == 1 else 99.1))):
        first = evaluate_peak_trailing(
            position_data, price,
            snapshot(side, quote_ms=181_000. + index * 1_000, live_ma5=ma5),
            fee=0., slippage=0.,
        )
    assert first["trigger"] == "MA5_TRUE_PEAK_REVERSAL"

    restored = copy.deepcopy(position_data)
    retry = evaluate_peak_trailing(
        restored, 100., {"quote_ms": 241_000.}, fee=0., slippage=0.
    )

    assert retry["trigger"] == "MA5_TRUE_PEAK_REVERSAL"


def test_waterfall_candidate_is_filtered_by_execution_adapters():
    position_data = position("SHORT")
    live = snapshot("SHORT", live_open=100., atr=1.)

    result = evaluate_peak_trailing(
        position_data, 102., live, fee=0., slippage=0.
    )

    assert result["trigger"] == "WATERFALL_DROP"


@pytest.mark.parametrize(
    ("side", "closes", "quote"),
    [
        ("SHORT", [99., 100., 101., 102., 103.], 110.),
        ("LONG", [103., 102., 101., 100., 99.], 90.),
        ("SHORT", [99., 100., 101., 102., 103.], 90.),
        ("LONG", [103., 102., 101., 100., 99.], 110.),
    ],
)
def test_cached_snapshot_calculates_quote_adjusted_ma5(side, closes, quote):
    timestamps = [60_000., 120_000., 180_000., 240_000., 300_000.]
    rows = [
        dict(
            timestamp=stamp,
            is_closed=True,
            open=close,
            high=close + 1.,
            low=close - 1.,
            close=close,
            atr=1.,
            ma3=close,
            ma5=sum(closes[max(0, index - 4):index + 1]) / min(index + 1, 5),
            ma15=close,
            kc_upper=close + 2.,
            kc_lower=close - 2.,
        )
        for index, (stamp, close) in enumerate(zip(timestamps, closes))
    ]
    rows.append(dict(
        timestamp=360_000.,
        is_closed=False,
        open=closes[-1],
        high=max(closes[-1], quote),
        low=min(closes[-1], quote),
        close=closes[-1],
        atr=999.,
        ma3=999.,
        ma5=999.,
        ma15=999.,
        kc_upper=999.,
        kc_lower=1.,
    ))
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000

    result, _ = cached_tick_indicators(frame, quote, 361_000.)

    expected_closed = sum(closes[-5:]) / 5.
    expected_live = (sum(closes[-4:]) + quote) / 5.
    assert result["closed_ma5"] == pytest.approx(expected_closed)
    assert result["live_ma5"] == pytest.approx(expected_live)
    evidence = live_ma5_reversal_exit(
        position(side), result, 1 if side == "LONG" else -1
    )
    # One snapshot cannot establish an observed in-position peak/trough.
    assert evidence is None


@pytest.mark.parametrize(
    ("exit_reason", "trigger"),
    [
        (ABNORMAL_REASON, "MA5_TRUE_PEAK_REVERSAL"),
        (PEAK_REASON, "EXIT_PROFIT_LOCK_FLOOR"),
    ],
)
def test_authorized_peak_exit_bypasses_trend_hold_and_closes(exit_reason, trigger):
    position_data = position("SHORT")
    account = SimpleNamespace(
        positions={"TEST/USDT": position_data},
        position_meta={},
        save_state=Mock(),
        log=Mock(),
        close_position=AsyncMock(return_value=True),
    )
    engine = SimpleNamespace(
        is_running=True,
        account=account,
        _channel_exit_frames={},
    )
    from core.services.exits.realtime_profit_exit import enforce_realtime_profit_exit

    def ma5_exit_decision(current_position, *_):
        current_position["peak_trailing_state"]["peak_net_pnl"] = 0.
        return dict(type=exit_reason, trigger=trigger)

    async def run():
        with (
            patch(
                "core.services.exits.realtime_profit_exit.enforce_hard_stop",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "core.services.exits.realtime_profit_exit.PureTrendStrategyV2."
                "evaluate_anti_whipsaw_profit_lock",
                side_effect=ma5_exit_decision,
            ),
            patch(
                "core.services.exits.trend_hold_evaluator.evaluate_trend_hold"
            ) as trend_hold,
        ):
            assert await enforce_realtime_profit_exit(
                engine, "TEST/USDT", 101., time.time() * 1000 - 10
            )
        trend_hold.assert_not_called()

    asyncio.run(run())
    assert account.close_position.await_count == 1
    assert trigger in account.close_position.await_args.args[2]
