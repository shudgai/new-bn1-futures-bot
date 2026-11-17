import math
import time
from types import SimpleNamespace

import pandas as pd
import pytest

from core.gates.kc_overall_structure_gate import evaluate_entry_signal
from core.services.entry_contract import (
    automatic_entry_kc_structure_problem,
    dual_timeframe_entry_problem,
)


def _frames(side, *, htf_slope=0.1):
    now = time.time()
    current_minute = math.floor(now * 1000 / 60_000) * 60_000
    direction = 1 if side == "LONG" else -1
    rows = []
    for index in range(16):
        middle = 100.0 + direction * index * 0.2
        opening = middle + (0.1 if side == "SHORT" else 0.2)
        close = opening - 0.2 if side == "SHORT" else opening + 0.1
        rows.append({
            "timestamp": current_minute - (15 - index) * 60_000,
            "open": opening,
            "high": max(opening, close) + 0.05,
            "low": min(opening, close) - 0.05,
            "close": close,
            "kc_middle": middle,
            "kc_upper": 110.0 + direction * index * 0.2,
            "kc_lower": 90.0 + direction * index * 0.2,
            "atr": 1.0,
            "ma5": middle + (0.4 if side == "LONG" else -0.4),
            "ma15": middle + (0.2 if side == "LONG" else -0.2),
            "is_closed": index < 15,
        })
    ltf = pd.DataFrame(rows)
    ltf.attrs.update({
        "timeframe_ms": 60_000,
        "snapshot_ms": now * 1000,
    })

    current_quarter_hour = (
        math.floor(now * 1000 / 900_000) * 900_000
    )
    htf = pd.DataFrame([
        {
            "timestamp": current_quarter_hour - 900_000,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "kc_middle": 100.0,
            "atr": 1.0,
            "is_closed": True,
        },
        {
            "timestamp": current_quarter_hour,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0 + direction * htf_slope,
            "kc_middle": 100.0 + direction * htf_slope,
            "atr": 1.0,
            "is_closed": False,
        },
    ])
    htf.attrs.update({
        "timeframe_ms": 900_000,
        "snapshot_ms": now * 1000,
    })
    quote = float(ltf.iloc[-1]["close"])
    return ltf, htf, quote, now


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_dual_timeframe_gate_accepts_aligned_live_entry_and_location_override(side):
    ltf, htf, quote, now = _frames(side)

    result = evaluate_entry_signal(side, ltf, htf, quote, now=now)

    assert result["allowed"]
    assert result["reason"] == "PASSED_DUAL_TIMEFRAME_ENTRY_GATE"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_dual_timeframe_gate_rejects_flat_htf(side):
    ltf, htf, quote, now = _frames(side, htf_slope=0.02)

    result = evaluate_entry_signal(side, ltf, htf, quote, now=now)

    assert not result["allowed"]
    assert result["reason"] == "BLOCKED_HTF_FLAT"


def test_dual_timeframe_gate_rejects_tick_not_matching_candle_color():
    ltf, htf, quote, now = _frames("LONG")
    current = ltf.index[-1]
    ltf.loc[current, "open"] = quote

    result = evaluate_entry_signal("LONG", ltf, htf, quote, now=now)

    assert not result["allowed"]
    assert result["reason"] == "BLOCKED_ENTRY_LIVE_CANDLE_COLOR"


def test_dual_timeframe_gate_rejects_missing_live_frame_metadata():
    ltf, htf, quote, now = _frames("SHORT")
    ltf.attrs.pop("snapshot_ms")

    result = evaluate_entry_signal("SHORT", ltf, htf, quote, now=now)

    assert not result["allowed"]
    assert result["reason"] == "BLOCKED_ENTRY_SNAPSHOT_INVALID"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_automatic_entry_gate_uses_latest_quote_and_cached_15m_frame(side):
    ltf, htf, quote, now = _frames(side)
    symbol = "LOBSTER/USDT"
    account = SimpleNamespace(
        positions={},
        entry_filter_last={},
        _channel_exit_frames={symbol: ltf},
        _macro_trend_frame_15m={symbol: htf},
        wait_quote_provider=lambda _symbol: {
            "price": quote,
            "observed_at": time.monotonic(),
        },
    )

    problem = automatic_entry_kc_structure_problem(
        side,
        ltf,
        {},
        "AUTOMATIC_ENTRY",
        account=account,
        symbol=symbol,
    )

    assert problem is None


def test_final_entry_boundary_rejects_quote_that_no_longer_matches_live_candle():
    ltf, htf, quote, _now = _frames("LONG")
    symbol = "LOBSTER/USDT"
    live_open = float(ltf.iloc[-1]["open"])
    account = SimpleNamespace(
        positions={},
        _channel_exit_frames={symbol: ltf},
        _macro_trend_frame_15m={symbol: htf},
        wait_quote_provider=lambda _symbol: {
            "price": live_open,
            "observed_at": time.monotonic(),
        },
    )

    problem = dual_timeframe_entry_problem(
        "LONG", account, symbol, {"source": "AUTOMATIC_ENTRY"},
    )

    assert problem == "BLOCKED_ENTRY_LIVE_CANDLE_COLOR"


@pytest.mark.anyio
async def test_testnet_order_boundary_rechecks_latest_tick_before_exchange_submit(tmp_path):
    from core.testnet_account import BinanceTestnetAccount

    ltf, htf, _quote, _now = _frames("LONG")
    symbol = "LOBSTER/USDT"

    class Exchange:
        def __init__(self):
            self.submitted = []

        async def create_order(self, *args, **kwargs):
            self.submitted.append((args, kwargs))

    exchange = Exchange()
    account = BinanceTestnetAccount(exchange, state_file=str(tmp_path / "state.json"))
    live_open = float(ltf.iloc[-1]["open"])
    account._channel_exit_frames = {symbol: ltf}
    account._macro_trend_frame_15m = {symbol: htf}
    account.wait_quote_provider = lambda _symbol: {
        "price": live_open,
        "observed_at": time.monotonic(),
    }

    with pytest.raises(ValueError, match="BLOCKED_ENTRY_LIVE_CANDLE_COLOR"):
        await account._send_order(
            symbol,
            "market",
            "buy",
            1.0,
            entry_context={"source": "AUTOMATIC_ENTRY"},
        )

    assert exchange.submitted == []
