import asyncio
import time
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from core.services.entry_contract import (
    KC_OUTER_PIVOT_LONG,
    KC_OUTER_PIVOT_SHORT,
    evaluate_entry_contract,
)
from core.services.entry_firewall import validate_entry_frame
from core.services.exits.peak_trailing_exit import (
    ABNORMAL_REASON,
    evaluate_peak_trailing,
)


def entry_frame(side="LONG"):
    rows = [
        dict(timestamp=60_000, open=99., high=101., low=95., close=100.,
             atr=1., ma5=97., ma15=95.,
             kc_upper=110., kc_middle=100., kc_lower=90., is_closed=True),
        dict(timestamp=120_000, open=96., high=99., low=89., close=94.,
             atr=1., ma5=98., ma15=96.,
             kc_upper=108., kc_middle=99., kc_lower=90., is_closed=True),
        dict(timestamp=180_000, open=94., high=100., low=92., close=98.,
             atr=1., ma5=99., ma15=97.,
             kc_upper=107., kc_middle=99., kc_lower=91., is_closed=True),
    ]
    frame = pd.DataFrame(rows)
    if side == "SHORT":
        source = frame.copy()
        for target, original in (
            ("open", "open"), ("close", "close"),
            ("high", "low"), ("low", "high"),
            ("kc_upper", "kc_lower"), ("kc_lower", "kc_upper"),
            ("kc_middle", "kc_middle"), ("ma5", "ma5"), ("ma15", "ma15"),
        ):
            frame[target] = 200. - source[original]
    frame.attrs["timeframe_ms"] = 60_000
    return frame


@pytest.mark.parametrize(
    ("side", "signal"),
    [("LONG", KC_OUTER_PIVOT_LONG), ("SHORT", KC_OUTER_PIVOT_SHORT)],
)
def test_entry_requires_new_price_pivot_outside_kc(side, signal):
    frame = entry_frame(side)

    decision = evaluate_entry_contract(frame, symbol="TEST")
    verified = validate_entry_frame(frame, side, signal)

    assert decision is not None
    assert verified["pivot_bar_id"] == decision["pivot_bar_id"]
    assert decision["side"] == side
    assert decision["type"] == signal
    assert decision["pivot_bar_id"] == 120_000.
    assert decision["confirmation_bar_id"] == 180_000.
    assert decision["ma_trend_confirmation_bar_id"] == 180_000.
    assert decision["ma5_trend_values"] == (
        [97., 98., 99.] if side == "LONG" else [103., 102., 101.]
    )
    assert decision["ma15_trend_values"] == (
        [95., 96., 97.] if side == "LONG" else [105., 104., 103.]
    )


@pytest.mark.parametrize(
    ("side", "ma5", "ma15"),
    [
        ("LONG", [99., 98., 97.], [97., 96., 95.]),
        ("LONG", [97., 98., 99.], [95., 95., 97.]),
        ("LONG", [97., 98., 99.], [95., 96., 95.]),
        ("SHORT", [101., 102., 103.], [103., 104., 105.]),
        ("SHORT", [103., 102., 101.], [105., 105., 103.]),
        ("SHORT", [103., 102., 101.], [105., 104., 105.]),
    ],
)
def test_entry_rejects_mixed_flat_or_opposite_ma_trend(side, ma5, ma15):
    frame = entry_frame(side)
    frame["ma5"] = ma5
    frame["ma15"] = ma15
    diagnostics = {}

    assert evaluate_entry_contract(frame, symbol="TEST", diagnostics=diagnostics) is None
    assert diagnostics["reason"] == "WAIT_MA5_MA15_TREND"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_unclosed_ma_trend_cannot_rescue_closed_trend(side):
    frame = entry_frame(side)
    frame["ma5"] = [99., 98., 97.] if side == "LONG" else [101., 102., 103.]
    frame["ma15"] = [97., 96., 95.] if side == "LONG" else [99., 100., 101.]
    live = frame.iloc[-1].copy()
    live["timestamp"] += 60_000
    live["is_closed"] = False
    live["open"] = live["close"] - 0.2 if side == "LONG" else live["close"] + 0.2
    live["high"] = max(live["open"], live["close"]) + 0.1
    live["low"] = min(live["open"], live["close"]) - 0.1
    live["ma5"] = 100. if side == "LONG" else 100.
    live["ma15"] = 98. if side == "LONG" else 98.
    frame = pd.concat([frame, live.to_frame().T], ignore_index=True)
    frame.attrs["timeframe_ms"] = 60_000
    diagnostics = {}

    assert evaluate_entry_contract(frame, symbol="TEST", diagnostics=diagnostics) is None
    assert diagnostics["reason"] == "WAIT_MA5_MA15_TREND"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize(
    "legacy_code",
    [
        "KC_2BAR_CONFIRM_LONG",
        "KC_LIVE_REVERSAL_BODY_LONG",
        "FAST_CLOSE_REENTRY_LONG",
        "FAST_CLOSE_REENTRY_SHORT",
    ],
)
def test_legacy_entry_authorities_cannot_bypass_pivot_entry(side, legacy_code):
    frame = entry_frame(side)

    assert evaluate_entry_contract(frame, code=legacy_code) is None
    with pytest.raises(ValueError, match="已停用"):
        validate_entry_frame(frame, side, legacy_code)

    from core.services.entry_firewall import validate_account_entry
    account = type("Account", (), {"positions": {}})()
    with pytest.raises(ValueError, match="合法入口白名單"):
        asyncio.run(validate_account_entry(
            account, "TEST/USDT", side, {"entry_signal_code": legacy_code}
        ))


@pytest.mark.parametrize(
    ("side", "signal"),
    [("LONG", KC_OUTER_PIVOT_LONG), ("SHORT", KC_OUTER_PIVOT_SHORT)],
)
def test_account_order_revalidation_uses_same_outer_pivot(side, signal):
    from core.services.entry_firewall import validate_account_entry

    active_bar = int(time.time() * 1000 // 60_000) * 60_000
    frame = entry_frame(side)
    frame["timestamp"] += active_bar - 240_000
    live = dict(
        timestamp=active_bar, open=98.5, high=99.2, low=98.4, close=99.,
        ma5=100., ma15=98.,
        atr=1., kc_upper=107., kc_middle=99., kc_lower=91., is_closed=False,
    )
    if side == "SHORT":
        live.update(
            open=101.5, high=101.6, low=100.8, close=101.,
            ma5=100., ma15=98.,
            kc_upper=109., kc_middle=101., kc_lower=93.,
        )
    frame = pd.concat([frame, pd.DataFrame([live])], ignore_index=True)
    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["entry_finality_verified"] = True
    decision = evaluate_entry_contract(frame, symbol="DOGE/USDT")
    assert decision is not None and decision["type"] == signal

    account = type("Account", (), {})()
    account.positions = {}
    account.trades = []
    account.last_closed_at = {}
    account.entry_frame_provider = AsyncMock(return_value=frame)
    context = {
        "entry_signal_code": signal,
        "channel_confirmation_bar_id": decision["confirmation_bar_id"],
        "signal_id": decision["pending_signal_id"],
        "candidate_bar_id": decision["confirmation_bar_id"],
        "entry_snapshot": {
            "symbol": "DOGE/USDT",
            "side": side,
            "signal_code": signal,
            "signal_id": decision["pending_signal_id"],
            "candidate_bar_id": decision["confirmation_bar_id"],
            "closed_bar": decision["confirmation_bar_id"],
            "pending_signal_id": decision["pending_signal_id"],
        },
    }
    verified = asyncio.run(
        validate_account_entry(account, "DOGE/USDT", side, context)
    )

    assert verified["pivot_bar_id"] == decision["pivot_bar_id"]

    frame["ma5"] = (
        [99., 98., 97., 98.] if side == "LONG"
        else [101., 102., 103., 102.]
    )
    frame["ma15"] = (
        [97., 96., 95., 96.] if side == "LONG"
        else [99., 100., 101., 100.]
    )
    with pytest.raises(ValueError, match="WAIT_MA5_MA15_TREND"):
        asyncio.run(validate_account_entry(account, "DOGE/USDT", side, context))


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_entry_rejects_inside_or_no_longer_new_outer_pivot(side):
    frame = entry_frame(side)
    low_or_high = "low" if side == "LONG" else "high"
    rail = "kc_lower" if side == "LONG" else "kc_upper"

    frame.loc[1, rail] = (
        frame.loc[1, low_or_high] - 1.
        if side == "LONG" else frame.loc[1, low_or_high] + 1.
    )
    assert evaluate_entry_contract(frame, symbol="TEST") is None

    frame = entry_frame(side)
    later = frame.iloc[-1].copy()
    later["timestamp"] += 60_000
    later["open"] = later["close"] - (0.5 if side == "LONG" else -0.5)
    later["close"] = later["close"] + (0.5 if side == "LONG" else -0.5)
    later["high"] = max(later["open"], later["close"]) + 0.1
    later["low"] = min(later["open"], later["close"]) - 0.1
    frame = pd.concat([frame, later.to_frame().T], ignore_index=True)
    frame["is_closed"] = True
    frame.attrs["timeframe_ms"] = 60_000

    assert evaluate_entry_contract(frame, symbol="TEST") is None


def pivot_history(side):
    rows = [
        dict(ms=120_000., h=104., l=98., c=102., kc_upper=105., kc_lower=95.),
        dict(ms=180_000., h=110., l=99., c=108., kc_upper=106., kc_lower=96.),
        dict(ms=240_000., h=108., l=100., c=107., kc_upper=107., kc_lower=97.),
    ]
    if side == "SHORT":
        mirrored = []
        for row in rows:
            mirrored.append(dict(
                ms=row["ms"], h=200. - row["l"], l=200. - row["h"],
                c=200. - row["c"], kc_upper=200. - row["kc_lower"],
                kc_lower=200. - row["kc_upper"],
            ))
        return mirrored
    return rows


def position(side):
    return dict(side=side, entry_price=100., qty=1., open_timestamp=60.,
                entry_mode="CHANNEL_SWING", leverage=1.)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_position_exits_on_confirmed_opposite_kc_outer_pivot(side):
    position_data = position(side)
    snapshot = dict(quote_ms=300_000., history_outer_pivots=pivot_history(side))

    result = evaluate_peak_trailing(position_data, 101., snapshot, fee=0., slippage=0.)

    assert result is not None
    assert result["type"] == ABNORMAL_REASON
    assert result["trigger"] == "KC_OUTER_PIVOT"
    assert position_data["peak_trailing_state"]["pending"] == ABNORMAL_REASON
    retry = evaluate_peak_trailing(
        position_data, 101., dict(quote_ms=360_000.), fee=0., slippage=0.
    )
    assert retry["trigger"] == "KC_OUTER_PIVOT"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_confirmed_ma_trend_holds_position_through_kc_outer_pivot(side):
    position_data = position(side)
    snapshot = {
        "quote_ms": 300_000.,
        "reason": None,
        "history_outer_pivots": pivot_history(side),
        "ma5_history": [97., 98., 99.] if side == "LONG" else [103., 102., 101.],
        "ma15_history": [95., 96., 97.] if side == "LONG" else [105., 104., 103.],
    }

    result = evaluate_peak_trailing(position_data, 101., snapshot, fee=0., slippage=0.)

    assert result is None
    assert position_data["peak_trailing_state"].get("pending") is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_confirmed_ma_trend_cancels_pending_kc_pivot_close(side):
    position_data = position(side)
    snapshot = dict(quote_ms=300_000., history_outer_pivots=pivot_history(side))
    first = evaluate_peak_trailing(position_data, 101., snapshot, fee=0., slippage=0.)
    assert first is not None and first["trigger"] == "KC_OUTER_PIVOT"

    snapshot.update(
        quote_ms=360_000.,
        reason=None,
        ma5_history=[97., 98., 99.] if side == "LONG" else [103., 102., 101.],
        ma15_history=[95., 96., 97.] if side == "LONG" else [105., 104., 103.],
    )
    result = evaluate_peak_trailing(position_data, 101., snapshot, fee=0., slippage=0.)

    assert result is None
    assert position_data["peak_trailing_state"].get("pending") is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_channel_swing_does_not_exit_on_profit_pullback_without_outer_pivot(side):
    position_data = position(side)
    sign = 1 if side == "LONG" else -1
    first = dict(quote_ms=300_000., live_bar_ms=300_000., closed_bar_ms=240_000.,
                 live_open=100. + sign * 2., atr=1., history_outer_pivots=[])
    evaluate_peak_trailing(position_data, 100. + sign * 2., first, 1., fee=0., slippage=0.)

    second = dict(quote_ms=360_000., live_bar_ms=360_000., closed_bar_ms=300_000.,
                  live_open=100. + sign * 2., atr=1., history_outer_pivots=[])
    result = evaluate_peak_trailing(
        position_data, 100. + sign * 1., second, 1., fee=0., slippage=0.
    )

    assert result is None
