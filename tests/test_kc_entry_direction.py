import asyncio
import json
import math
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from fastapi import Response

from core.gates.central_entry_gate import CentralEntryGate
from core.gates.master_gate_funnel import MasterGateFunnel
from core.gates.unified_pipeline_gates import EntryGate
from core.services.candle_data import confirmed_kc_midline_slope
from core.services.data_pipeline import DataPipeline
from core.services.execution_manager import ExecutionManager
from core.services.math_execution_gate import validate_entry_at_execution, validate_entry_math
from test_chart_kline_freshness import chart_api, chart_frame
from test_entry_methods import (
    KC_TWO_BAR_BREAKOUT, evaluate_entry_method, method_context, post_close_market,
)
from test_master_gate_funnel import short_breakout_snapshot


SYMBOLS = ["1000PEPE/USDT", "龙虾/USDT", "CAP/USDT"]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_completed_direction_ignores_live_candle_and_macro_label(side):
    frame = post_close_market(side)
    expected = confirmed_kc_midline_slope(frame)
    live = frame.iloc[-1].copy()
    live["timestamp"] += 60_000
    live["is_closed"] = False
    live["kc_middle"] = 1 if side == "LONG" else 10_000
    frame.loc[len(frame)] = live
    frame.attrs["macro_trend"] = "BEARISH" if side == "LONG" else "BULLISH"
    assert confirmed_kc_midline_slope(frame) == expected


@pytest.mark.parametrize("fault", [
    "flat", "missing_finality", "unknown_finality", "nan", "inf", "zero",
    "negative", "gap", "duplicate", "reversed", "short",
])
def test_invalid_or_flat_direction_cannot_become_a_direction(fault):
    frame = post_close_market("LONG")
    if fault == "flat":
        frame.loc[frame.index[-1], "kc_middle"] = frame.iloc[-2]["kc_middle"]
        assert confirmed_kc_midline_slope(frame) == 0
        return
    if fault == "missing_finality":
        frame = frame.drop(columns=["is_closed"])
    elif fault == "unknown_finality":
        frame["is_closed"] = "true"
    elif fault in {"nan", "inf", "zero", "negative"}:
        value = {"nan": math.nan, "inf": math.inf, "zero": 0, "negative": -1}[fault]
        frame.loc[frame.index[-1], "kc_middle"] = value
    elif fault in {"gap", "duplicate", "reversed"}:
        frame.loc[frame.index[-1], "timestamp"] += {
            "gap": 60_000, "duplicate": -60_000, "reversed": -120_000,
        }[fault]
    else:
        frame = frame.tail(1)
    assert confirmed_kc_midline_slope(frame) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_snapshot_kc_direction_is_not_overruled_by_opposite_htf_regime(side):
    prices = [120 - index * 0.2 for index in range(100)] + [102, 103, 104]
    if side == "SHORT":
        prices = [200 - value for value in prices]
    frame = pd.DataFrame([
        dict(
            timestamp=60_000 * (index + 1), open=value - 0.1,
            high=value + 0.2, low=value - 0.2, close=value,
            volume=100, is_closed=True,
        )
        for index, value in enumerate(prices)
    ])
    frame.attrs["timeframe_ms"] = 60_000
    snapshot = DataPipeline.calculate_snapshot("CAP/USDT", frame, prices[-1])
    assert snapshot is not None
    assert snapshot.kc_trend == ("BULLISH" if side == "LONG" else "BEARISH")
    assert snapshot.macro_trend == ("BEARISH" if side == "LONG" else "BULLISH")
    assert snapshot.kc_middle_slope == pytest.approx(
        snapshot.curr_k["kc_middle"] - snapshot.prev_k["kc_middle"],
    )


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("with_method", [False, True])
def test_scan_and_final_submission_use_kc_not_old_regime(symbol, side, with_method):
    frame = post_close_market(side)
    quote = float(frame.iloc[-1]["close"])
    frame.attrs["macro_trend"] = "BEARISH" if side == "LONG" else "BULLISH"
    frame["macro_trend"] = frame.attrs["macro_trend"]
    frame["macro_kc_middle_slope"] = -100 if side == "LONG" else 100
    frame.loc[frame.index[-1], "volume"] = 200
    frame.attrs["snapshot_ms"] = float(frame.iloc[-1]["timestamp"]) + 60_000
    meta = {"entry_gate_frame": frame}
    if with_method:
        signal = evaluate_entry_method(
            frame, symbol=symbol, quote=quote, expected_method=KC_TWO_BAR_BREAKOUT,
        )
        assert signal
        context = method_context(signal)
        frame.attrs.update(
            entry_method_context={"symbol": symbol, **context},
            entry_method_quote=quote,
        )
        meta.update(context)
    allowed, reason = validate_entry_math(frame, side, is_outer_entry=True, symbol=symbol)
    assert allowed, reason
    central = CentralEntryGate.verify_and_authorize(symbol, side, quote, frame)
    assert central.passed, central.reason
    allowed, reason = asyncio.run(validate_entry_at_execution(
        SimpleNamespace(), symbol, side, meta, execution_price=quote,
    ))
    assert allowed, reason


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("slope", [0, -0.1, math.nan])
def test_final_math_never_waives_kc_direction_for_override(side, slope):
    frame = post_close_market(side)
    sign = 1 if side == "LONG" else -1
    frame.loc[frame.index[-1], "kc_middle"] = frame.iloc[-2]["kc_middle"] + sign * slope
    frame.attrs["direction_override"] = True
    allowed, _ = validate_entry_math(frame, side, is_outer_entry=True)
    assert not allowed


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_entry_gate_requires_completed_direction_even_when_label_and_slope_claim_alignment(side):
    allowed, reason, _ = EntryGate.evaluate("BREAKOUT_" + side, {
        "current_price": 100, "atr": 1, "kc_middle": 99 if side == "LONG" else 101,
        "kc_middle_slope": 1 if side == "LONG" else -1,
        "macro_trend": "BULLISH" if side == "LONG" else "BEARISH",
        "curr_k": {"open": 99.5 if side == "LONG" else 100.5,
                   "close": 100, "high": 100.1 if side == "LONG" else 100.6,
                   "low": 99.4 if side == "LONG" else 99.9},
    })
    assert not allowed and reason == "VETO_KC_DIRECTION_UNAVAILABLE"


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_master_funnel_accepts_kc_alignment_despite_opposite_regime(symbol, side):
    market = short_breakout_snapshot()._replace(symbol=symbol, macro_trend="BULLISH")
    if side == "LONG":
        changes = {
            key: 200 - value
            for key, value in market._asdict().items()
            if key not in {"atr", "ma_cross_count_8", "timeframe_ms"}
            and isinstance(value, (int, float))
        }
        changes.update(
            high_price=200 - market.low_price, low_price=200 - market.high_price,
            kc_upper=200 - market.kc_lower, kc_lower=200 - market.kc_upper,
            macro_trend="BEARISH",
        )
        market = market._replace(**changes)
    decision = (
        MasterGateFunnel.evaluate_open_long(market) if side == "LONG"
        else MasterGateFunnel.evaluate_open_short(market)
    )
    assert decision.can_execute, decision.reason


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_pipeline_ignores_old_state_and_opposing_15m_trend(monkeypatch, symbol, side):
    frame = post_close_market(side)
    quote = float(frame.iloc[-1]["close"])
    snapshot = DataPipeline.calculate_snapshot(symbol, frame, quote)
    assert snapshot
    snapshot.raw_df = frame
    snapshot.curr_k = frame.iloc[-1].to_dict()
    snapshot.prev_k = frame.iloc[-2].to_dict()
    snapshot.kc_middle_slope = confirmed_kc_midline_slope(frame)
    snapshot.macro_trend = "BEARISH" if side == "LONG" else "BULLISH"
    monkeypatch.setattr(ExecutionManager, "_symbol_macro_trend", {symbol: snapshot.macro_trend})
    monkeypatch.setattr(DataPipeline, "calculate_snapshot", staticmethod(lambda **kwargs: snapshot))
    entry = AsyncMock(return_value={"status": "DIRECTION_REACHED"})
    monkeypatch.setattr(ExecutionManager, "_evaluate_closed_entry_methods", entry)
    engine = SimpleNamespace(account=SimpleNamespace(positions={}))
    result = asyncio.run(ExecutionManager.process_symbol_pipeline(
        symbol, engine, klines_df=frame, quote=quote,
        frame_15m=post_close_market("SHORT" if side == "LONG" else "LONG"),
    ))
    assert result["status"] == "DIRECTION_REACHED", result
    assert entry.await_args.kwargs["macro_trend"] == ("BULLISH" if side == "LONG" else "BEARISH")


@pytest.mark.parametrize("symbol", ["1000PEPE/USDT", "龙虾/USDT"])
@pytest.mark.parametrize("direction", ["UP", "DOWN", "FLAT"])
def test_chart_reports_closed_kc_and_never_mutates_trading_state(chart_api, monkeypatch, symbol, direction):
    frame = chart_frame()
    if direction == "DOWN":
        original_high = frame["high"].copy()
        frame[["open", "close"]] = 200 - frame[["open", "close"]]
        frame["high"], frame["low"] = 200 - frame["low"], 200 - original_high
    elif direction == "FLAT":
        frame[["open", "close"]] = 100
        frame["high"], frame["low"] = 101, 99
    frame.loc[frame.index[-1], ["open", "high", "low", "close"]] = [10, 11, 9, 10]
    monkeypatch.setattr(chart_api, "fetch_chart_klines", AsyncMock(return_value=frame))
    monkeypatch.setattr(chart_api.engine, "_entry_gate_diagnostics", {}, raising=False)
    original = {symbol: "BEARISH"}
    monkeypatch.setattr(ExecutionManager, "_symbol_macro_trend", original.copy())
    result = asyncio.run(chart_api.get_klines(Response(), symbol, "1m", 200, True))
    assert result["kc_direction"] == direction
    assert result["macro_trend"] == {"UP": "BULLISH", "DOWN": "BEARISH", "FLAT": "NEUTRAL"}[direction]
    if direction in {"UP", "FLAT"}:
        assert result["entry_block"]["reason"] == "BLOCKED_SHORT_ON_BULLISH_TREND"
    assert ExecutionManager._symbol_macro_trend == original


def test_ui_uses_two_closed_kc_values_not_live_price_or_backend_macro():
    root = Path(__file__).resolve().parents[1]
    script, = re.findall(r"<script>(.*?)</script>", (root / "web/index.html").read_text(), re.DOTALL)
    harness = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
let config;
vm.runInNewContext(SOURCE, {
    Vue: {createApp(value) {config = value; return {mount() {}};}},
    localStorage: {getItem() {return null;}},
});
const trend = config.methods.getChartCkTrend;
const data = [
    {time:60, kc_middle:100, close:50, is_live:false},
    {time:120, kc_middle:101, close:49, is_live:false},
    {time:180, kc_middle:1, close:1, is_live:true},
];
assert.equal(trend(data, 'bear'), 'bull');
data[1].kc_middle = 99;
assert.equal(trend(data, 'bull'), 'bear');
data[1].kc_middle = 100;
assert.equal(trend(data, 'bull'), 'range');
data[1].kc_middle = null;
assert.equal(trend(data, 'bull'), 'wait');
data[1].kc_middle = 101;
data[1].time = 180;
assert.equal(trend(data), 'wait');
assert.equal(trend([{kc_middle:100}, {kc_middle:101}], 'bull'), 'wait');
"""
    result = subprocess.run(
        ["node", "-e", "const SOURCE=" + json.dumps(script) + ";" + harness],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
