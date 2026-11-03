import asyncio
import json
import re
import subprocess
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from fastapi import HTTPException, Response


@pytest.fixture
def chart_api(monkeypatch, tmp_path):
    import core.config as config
    import core.paper_account as paper

    monkeypatch.setattr(config, "PAPER_TRADING", True)
    monkeypatch.setattr(paper, "STATE_FILE", str(tmp_path / "paper.json"))
    from services import api

    monkeypatch.setattr(api, "_kline_cache", {})
    monkeypatch.setattr(api, "_kline_inflight", {})
    monkeypatch.setattr(api.engine, "fetch_klines", AsyncMock(
        side_effect=AssertionError("Chart must not use the trading REST cache"),
    ))
    return api


def chart_frame(*, minutes_old=0, include_live=True):
    current_minute = int(time.time() // 60 * 60_000)
    latest = current_minute - minutes_old * 60_000
    frame = pd.DataFrame([
        {
            "timestamp": latest - (39 - index) * 60_000,
            "open": 100 + index * 0.1, "close": 100.05 + index * 0.1,
            "high": 100.2 + index * 0.1, "low": 99.9 + index * 0.1,
            "volume": 100, "is_closed": index < 39 or not include_live,
        }
        for index in range(40)
    ])
    frame.attrs["timeframe_ms"] = 60_000
    return frame


@pytest.mark.parametrize("symbol", ["1000PEPE/USDT", "龙虾/USDT"])
def test_chart_reads_fresh_public_data_not_trading_cache(chart_api, monkeypatch, symbol):
    api = chart_api
    frame = chart_frame()
    fetch = AsyncMock(return_value=frame)
    monkeypatch.setattr(api, "fetch_chart_klines", fetch)
    result = asyncio.run(api.get_klines(Response(), symbol, "1m", 200, True))
    assert result["data"][-1]["time"] == frame.iloc[-1]["timestamp"] // 1000
    assert result["data"][-1]["is_live"]
    assert result["cache"]["source"] == "live"
    fetch.assert_awaited_once_with(
        api.engine.exchange, symbol, timeframe="1m", limit=200,
        keep_live=True, max_retries=1,
    )
    api.engine.fetch_klines.assert_not_awaited()


def test_expired_upstream_candles_are_not_labeled_live(chart_api, monkeypatch):
    api = chart_api
    monkeypatch.setattr(api, "fetch_chart_klines", AsyncMock(return_value=chart_frame(minutes_old=6)))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.get_klines(Response(), "1000PEPE/USDT", "1m", 200, True))
    assert exc.value.status_code == 503
    assert "過期" in exc.value.detail
    assert not api._kline_cache


def test_failed_refresh_retains_last_chart_with_explicit_stale_flag(chart_api, monkeypatch):
    api = chart_api
    key = ("1000PEPE/USDT", "1m", 200, True)
    payload = {"symbol": key[0], "data": [{"time": 123}]}
    api._kline_cache[key] = {"payload": payload, "saved_at": time.monotonic() - 10}
    monkeypatch.setattr(api, "fetch_chart_klines", AsyncMock(return_value=chart_frame(minutes_old=6)))
    result = asyncio.run(api.get_klines(Response(), *key))
    assert result["data"] == payload["data"]
    assert result["cache"]["source"] == "stale"
    assert result["cache"]["age_seconds"] >= 10


def test_chart_requests_remain_single_flight_and_cached(chart_api, monkeypatch):
    api = chart_api
    fetch = AsyncMock(return_value=chart_frame())
    monkeypatch.setattr(api, "fetch_chart_klines", fetch)

    async def scenario():
        results = await asyncio.gather(*[
            api.get_klines(Response(), "1000PEPE/USDT", "1m", 200, True)
            for _ in range(3)
        ])
        cached = await api.get_klines(Response(), "1000PEPE/USDT", "1m", 200, True)
        assert all(result["data"] == cached["data"] for result in results)
        assert cached["cache"]["source"] == "cache"
        assert not api._kline_inflight

    asyncio.run(scenario())
    fetch.assert_awaited_once()


def test_chart_timeout_cleans_inflight_request(chart_api, monkeypatch):
    api = chart_api
    monkeypatch.setattr(api, "fetch_chart_klines", AsyncMock(side_effect=asyncio.TimeoutError))
    with pytest.raises(HTTPException):
        asyncio.run(api.get_klines(Response(), "1000PEPE/USDT", "1m", 200, True))
    assert not api._kline_inflight
    assert not api._kline_cache


def test_closed_tail_is_not_mislabeled_as_live(chart_api, monkeypatch):
    api = chart_api
    frame = chart_frame(minutes_old=1, include_live=False)
    monkeypatch.setattr(api.time, "time", lambda: float(frame.iloc[-1]["timestamp"]) / 1000 + 65)
    monkeypatch.setattr(api, "fetch_chart_klines", AsyncMock(return_value=frame))
    result = asyncio.run(api.get_klines(Response(), "1000PEPE/USDT", "1m", 200, True))
    assert not result["data"][-1]["is_live"]


def test_refresh_advances_candle_after_minute_rollover(chart_api, monkeypatch):
    api = chart_api
    clock = [time.time()]
    monkeypatch.setattr(api.time, "time", lambda: clock[0])
    monkeypatch.setattr(api.time, "monotonic", lambda: clock[0])
    fetch = AsyncMock(side_effect=lambda *args, **kwargs: chart_frame())
    monkeypatch.setattr(api, "fetch_chart_klines", fetch)

    async def scenario():
        first = await api.get_klines(Response(), "1000PEPE/USDT", "1m", 200, True)
        clock[0] += 60
        second = await api.get_klines(Response(), "1000PEPE/USDT", "1m", 200, True)
        assert second["data"][-1]["time"] == first["data"][-1]["time"] + 60
        assert second["cache"]["source"] == "live"

    asyncio.run(scenario())
    assert fetch.await_count == 2


def test_chart_ui_reports_stale_data_even_with_existing_candles():
    root = Path(__file__).resolve().parents[1]
    script, = re.findall(r"<script>(.*?)</script>", (root / "web/index.html").read_text(), re.DOTALL)
    harness = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
let config;
const sandbox = {
    Vue: {createApp(value) {config = value; return {mount() {}};}},
    localStorage: {getItem() {return null;}},
    AbortController, console: {warn() {}},
    setTimeout() {return 1;}, clearTimeout() {},
};
vm.runInNewContext(SOURCE, sandbox);
async function main() {
    const app = {...config.data(), ...config.methods};
    const symbol = '1000PEPE/USDT';
    const state = {};
    const series = {applyOptions() {}, setData() {}, update() {}, setMarkers() {}};
    const ctx = {
        chart: {timeScale() {return {fitContent() {}};}},
        candlestickSeries: series, ma5Series: series, ma15Series: series,
        kcUpperSeries: series, kcMiddleSeries: series, kcLowerSeries: series,
    };
    app._charts = {[symbol]: ctx};
    app.chartModal = {symbols: [symbol], states: {[symbol]: state}};
    app.bot = {positions: []};
    sandbox.fetch = async () => ({ok:true, json:async () => ({
        cache: {source:'stale'},
        data: [{time:60, open:1, high:2, low:1, close:2,
                ma5:null, ma15:null, kc_upper:null, kc_middle:null, kc_lower:null}],
    })});
    await app.fetchChartData(symbol);
    assert.match(state.error, /舊 K 線/);
    assert.equal(state.ckTrend, 'wait');
    assert.equal(ctx.fetchInFlight, false);
    assert.ok(ctx.lastCandle);
    sandbox.fetch = async () => ({ok:false, status:503});
    await app.fetchChartData(symbol);
    assert.match(state.error, /HTTP 503/);
    assert.match(state.error, /保留舊 K 線/);
    assert.equal(ctx.fetchInFlight, false);
}
main().catch(error => {console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        ["node", "-e", "const SOURCE=" + json.dumps(script) + ";" + harness],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_trade_markers_use_fill_time_not_signal_time_and_do_not_shift_missing_bars(
    chart_api, monkeypatch, caplog,
):
    api = chart_api
    frame = chart_frame()
    latest = int(frame.iloc[-1]["timestamp"])
    symbol = "1000PEPE/USDT"
    monkeypatch.setattr(api, "fetch_chart_klines", AsyncMock(return_value=frame))
    monkeypatch.setattr(api.engine.account, "trades", [
        dict(symbol=symbol, action="OPEN_SHORT", id=latest + 1000, price=.0047,
             channel_confirmation_bar_id=latest - 60_000),
        dict(symbol=symbol, action="CLOSE_SHORT", id=latest + 2000, price=.0046),
        dict(symbol=symbol, action="PARTIAL_CLOSE_SHORT", id=latest + 3000, price=.00465),
        dict(symbol=symbol, action="OPEN_LONG", id=latest + 60_000, price=.0048),
        dict(symbol=symbol, action="CLOSE_LONG", id=latest - 40 * 60_000, price=.0048),
        dict(symbol=symbol, action="OPEN_LONG", id="invalid", price=.0048),
        dict(symbol=symbol, action="OPEN_LONG", id=latest + 4000, price=float("nan")),
        dict(symbol="CAP/USDT", action="OPEN_LONG", id=latest + 1000, price=1),
    ])
    result = asyncio.run(api.get_klines(Response(), symbol, "1m", 200, True))
    assert result["data"][-2]["trade_markers"] == []
    markers = result["data"][-1]["trade_markers"]
    assert [m["action"] for m in markers] == [
        "OPEN_SHORT", "CLOSE_SHORT", "PARTIAL_CLOSE_SHORT",
    ]
    assert [m["timestamp"] for m in markers] == [latest + 1000, latest + 2000, latest + 3000]
    assert "CHART_TRADE_MARKER_INVALID" in caplog.text


def test_chart_renders_all_fill_labels_prices_and_redraws_after_history_refresh():
    root = Path(__file__).resolve().parents[1]
    script, = re.findall(r"<script>(.*?)</script>", (root / "web/index.html").read_text(), re.DOTALL)
    harness = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
let config, calls = [], payload;
const sandbox = {
    Vue: {createApp(value) {config = value; return {mount() {}};}},
    localStorage: {getItem() {return null;}},
    AbortController, console: {warn() {}},
    setTimeout() {return 1;}, clearTimeout() {},
    fetch: async () => ({ok:true, json:async () => payload}),
};
vm.runInNewContext(SOURCE, sandbox);
async function main() {
    const app = {...config.data(), ...config.methods}, symbol = '1000PEPE/USDT';
    const series = {
        applyOptions() {}, setData() {}, update() {},
        setMarkers(markers) {calls.push(JSON.parse(JSON.stringify(markers)));},
    };
    const ctx = {
        chart: {timeScale() {return {fitContent() {}};}},
        candlestickSeries: series, ma5Series: series, ma15Series: series,
        kcUpperSeries: series, kcMiddleSeries: series, kcLowerSeries: series,
    };
    app._charts = {[symbol]:ctx};
    app.chartModal = {symbols:[symbol], states:{[symbol]:{}}};
    app.bot = {positions:[]};
    const candle = time => ({time, open:1, high:2, low:1, close:2,
        ma5:null, ma15:null, kc_upper:null, kc_middle:null, kc_lower:null});
    payload = {data:[candle(60), {...candle(120), trade_markers:[
        {action:'OPEN_LONG', price:'0.00471'},
        {action:'CLOSE_LONG', price:'0.00472'},
        {action:'OPEN_SHORT', price:'0.00473'},
        {action:'CLOSE_SHORT', price:'0.00474'},
        {action:'PARTIAL_CLOSE_SHORT', price:'0.00475'},
    ], block_markers:[{side:'LONG'}]}]};
    await app.fetchChartData(symbol);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].length, 2);
    const below = calls[0].find(m => m.position === 'belowBar');
    const above = calls[0].find(m => m.position === 'aboveBar');
    for (const label of ['買多 @ 0.00471', '平空 @ 0.00474', '部分平空 @ 0.00475', '🚫'])
        assert.ok(below.text.includes(label), label);
    for (const label of ['平多 @ 0.00472', '賣空 @ 0.00473'])
        assert.ok(above.text.includes(label), label);
    assert.ok(calls[0].every(m => m.time === 120));
    await app.fetchChartData(symbol);
    assert.equal(calls.length, 1);
    payload.data[0].close = 1.5;
    await app.fetchChartData(symbol);
    assert.equal(calls.length, 2); // setData must restore identical markers.
    payload.data[1].trade_markers = [];
    payload.data[1].block_markers = [];
    await app.fetchChartData(symbol);
    assert.equal(calls.length, 3);
    assert.equal(calls[2].length, 0);
}
main().catch(error => {console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        ["node", "-e", "const SOURCE=" + json.dumps(script) + ";" + harness],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
