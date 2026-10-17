import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import core.engine as engine_module
from core.engine import TradingEngine
from core.services.order_sizing import calculate_order_qty
from core.symbol_aliases import to_binance_usdm_symbol
from core.testnet_account import BinanceTestnetAccount


LOBSTER = "龙虾/USDT"
PEPE = "1000PEPE/USDT"
CAP = "CAP/USDT"


def _ohlcv_rows(count=2):
    current_minute = int(time.time() // 60 * 60_000)
    return [
        [
            current_minute - (count - index - 1) * 60_000,
            1.0, 1.2, 0.9, 1.1, 10.0 + index,
        ]
        for index in range(count)
    ]


def _engine_with_kline_cache():
    engine = object.__new__(TradingEngine)
    engine._wild_swing_kline_cache = {}
    engine._wild_swing_kline_updated_at = {}
    engine._wild_swing_kline_stream_ready = set()
    engine._wild_swing_kline_fallback_logged = set()
    engine._wild_swing_kline_rest_cache = {}
    engine._wild_swing_kline_rest_last_attempt = {}
    engine.account = type(
        "Account",
        (),
        {"log": lambda self, *_args, **_kwargs: None},
    )()
    return engine


@pytest.mark.parametrize(
    ("symbol", "exchange_symbol"),
    [
        (LOBSTER, "龙虾/USDT:USDT"),
        ("龍蝦/USDT", "龙虾/USDT:USDT"),
        ("LOBSTER/USDT", "龙虾/USDT:USDT"),
        (CAP, "CAP/USDT:USDT"),
        (PEPE, "1000PEPE/USDT:USDT"),
    ],
)
def test_market_data_alias_maps_to_exchange_symbol(symbol, exchange_symbol):
    assert to_binance_usdm_symbol(symbol) == exchange_symbol


def test_active_symbols_start_independent_wild_swing_kline_tasks(monkeypatch):
    async def wait_forever():
        await asyncio.Event().wait()

    async def scenario():
        engine = object.__new__(TradingEngine)
        engine.ticker_task = None
        engine.wild_swing_kline_tasks = {}
        engine._ticker_loop = wait_forever
        engine._wild_swing_kline_loop = lambda _symbol: wait_forever()

        engine.start_market_data()
        assert set(engine.wild_swing_kline_tasks) == {LOBSTER, CAP}
        assert not engine.ticker_task.done()
        assert all(not task.done() for task in engine.wild_swing_kline_tasks.values())

        tasks = [engine.ticker_task, *engine.wild_swing_kline_tasks.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    monkeypatch.setattr(engine_module, "DEFAULT_SYMBOLS", [LOBSTER, CAP])
    asyncio.run(scenario())


def test_websocket_kline_loops_cache_both_symbols_independently():
    class FakeWebSocket:
        def __init__(self):
            self.calls = []
            self.delivered = set()

        async def watch_ohlcv(self, symbol, timeframe, limit):
            self.calls.append((symbol, timeframe, limit))
            if symbol in self.delivered:
                raise asyncio.CancelledError
            self.delivered.add(symbol)
            return _ohlcv_rows()

    async def scenario():
        engine = _engine_with_kline_cache()
        engine.ws_exchange = FakeWebSocket()
        results = await asyncio.gather(
            engine._wild_swing_kline_loop(LOBSTER),
            engine._wild_swing_kline_loop(PEPE),
            return_exceptions=True,
        )

        assert all(isinstance(result, asyncio.CancelledError) for result in results)
        assert set(engine.ws_exchange.calls) == {
            ("龙虾/USDT:USDT", "1m", 200),
            ("1000PEPE/USDT:USDT", "1m", 200),
        }
        assert len(engine.ws_exchange.calls) == 4
        assert set(engine._wild_swing_kline_cache) == {LOBSTER, PEPE}
        assert set(engine._wild_swing_kline_updated_at) == {LOBSTER, PEPE}

    asyncio.run(scenario())


def test_websocket_partial_updates_preserve_cached_candle_history():
    class FakeWebSocket:
        def __init__(self, update):
            self.update = update
            self.calls = 0

        async def watch_ohlcv(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return [self.update]
            raise asyncio.CancelledError

    async def scenario():
        engine = _engine_with_kline_cache()
        history = _ohlcv_rows(count=5)
        update = list(history[-1])
        update[4] = 1.15
        update[5] = 99.0
        engine._wild_swing_kline_cache[LOBSTER] = history
        engine.ws_exchange = FakeWebSocket(update)

        result = await asyncio.gather(
            engine._wild_swing_kline_loop(LOBSTER),
            return_exceptions=True,
        )

        cached = engine._wild_swing_kline_cache[LOBSTER]
        assert isinstance(result[0], asyncio.CancelledError)
        assert len(cached) == 5
        assert cached[-1][4] == 1.15
        assert cached[-1][5] == 99.0

    asyncio.run(scenario())


@pytest.mark.parametrize("symbol", [LOBSTER, PEPE])
def test_fresh_websocket_kline_cache_serves_wild_swing_frames(symbol):
    async def scenario():
        engine = _engine_with_kline_cache()
        engine.exchange = type("Exchange", (), {})()
        engine.exchange.fetch_ohlcv = AsyncMock()
        engine._wild_swing_kline_cache[symbol] = _ohlcv_rows(count=20)
        engine._wild_swing_kline_updated_at[symbol] = time.monotonic()

        frame = await engine.fetch_klines(
            symbol, timeframe="1m", limit=20, keep_live=True,
        )

        assert len(frame) == 20
        assert frame.iloc[-1]["is_closed"] is False or not frame.iloc[-1]["is_closed"]
        engine.exchange.fetch_ohlcv.assert_not_awaited()

    asyncio.run(scenario())


def test_rest_bootstrap_seeds_websocket_history_cache():
    async def scenario():
        engine = _engine_with_kline_cache()
        engine.exchange = type("Exchange", (), {})()
        rows = _ohlcv_rows(count=20)
        engine.exchange.fetch_ohlcv = AsyncMock(return_value=rows)
        engine._wild_swing_kline_cache[LOBSTER] = [rows[-1]]
        observed_at = time.monotonic()
        engine._wild_swing_kline_updated_at[LOBSTER] = observed_at

        frame = await engine.fetch_klines(
            LOBSTER, "1m", limit=20, keep_live=True,
        )
        next_frame = await engine.fetch_klines(
            LOBSTER, "1m", limit=20, keep_live=True,
        )

        assert len(frame) == len(next_frame) == 20
        assert len(engine._wild_swing_kline_cache[LOBSTER]) == 20
        assert engine._wild_swing_kline_updated_at[LOBSTER] == observed_at
        engine.exchange.fetch_ohlcv.assert_awaited_once()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("symbol", "exchange_symbol"),
    [
        (LOBSTER, "龙虾/USDT:USDT"),
        (PEPE, "1000PEPE/USDT:USDT"),
    ],
)
def test_stale_websocket_cache_falls_back_to_rest(symbol, exchange_symbol):
    async def scenario():
        engine = _engine_with_kline_cache()
        engine.exchange = type("Exchange", (), {})()
        engine.exchange.fetch_ohlcv = AsyncMock(return_value=_ohlcv_rows())
        engine._wild_swing_kline_cache[symbol] = _ohlcv_rows()
        engine._wild_swing_kline_updated_at[symbol] = time.monotonic() - 16.0

        frame = await engine.fetch_klines(
            symbol, timeframe="1m", limit=20, keep_live=True,
        )

        assert len(frame) == 2
        engine.exchange.fetch_ohlcv.assert_awaited_once_with(
            exchange_symbol, timeframe="1m", limit=20,
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("symbol", [LOBSTER, PEPE])
def test_short_websocket_cache_falls_back_to_rest(symbol):
    async def scenario():
        engine = _engine_with_kline_cache()
        engine.exchange = type("Exchange", (), {})()
        engine.exchange.fetch_ohlcv = AsyncMock(return_value=_ohlcv_rows(count=20))
        engine._wild_swing_kline_cache[symbol] = _ohlcv_rows()
        engine._wild_swing_kline_updated_at[symbol] = time.monotonic()

        frame = await engine.fetch_klines(
            symbol, timeframe="1m", limit=20, keep_live=True,
        )

        assert len(frame) == 20
        engine.exchange.fetch_ohlcv.assert_awaited_once_with(
            to_binance_usdm_symbol(symbol), timeframe="1m", limit=20,
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("symbol", [LOBSTER, CAP])
def test_rest_kline_fallback_is_throttled_and_reuses_cached_data(symbol, monkeypatch):
    async def scenario():
        engine = _engine_with_kline_cache()
        engine.exchange = type("Exchange", (), {})()
        engine.exchange.fetch_ohlcv = AsyncMock(return_value=_ohlcv_rows(count=20))
        clock = [100.0]
        monkeypatch.setattr(engine_module.time, "monotonic", lambda: clock[0])

        first = await engine.fetch_klines(symbol, "1m", limit=20, keep_live=True)
        clock[0] += 2.9
        second = await engine.fetch_klines(symbol, "1m", limit=20, keep_live=True)

        assert len(first) == len(second) == 20
        engine.exchange.fetch_ohlcv.assert_awaited_once()

        clock[0] += 0.11
        await engine.fetch_klines(symbol, "1m", limit=20, keep_live=True)
        assert engine.exchange.fetch_ohlcv.await_count == 2

    asyncio.run(scenario())


def test_rest_kline_cooldown_is_per_symbol_and_alias_independent():
    async def scenario():
        engine = _engine_with_kline_cache()
        engine.exchange = type("Exchange", (), {})()
        engine.exchange.fetch_ohlcv = AsyncMock(return_value=_ohlcv_rows(count=2))

        await engine.fetch_klines(LOBSTER, "15m", limit=2, keep_live=True)
        await engine.fetch_klines("LOBSTER/USDT", "1m", limit=2, keep_live=True)
        await engine.fetch_klines(CAP, "15m", limit=2, keep_live=True)

        assert engine.exchange.fetch_ohlcv.await_count == 2
        assert engine.exchange.fetch_ohlcv.await_args_list[0].args == (
            "龙虾/USDT:USDT",
        )
        assert engine.exchange.fetch_ohlcv.await_args_list[1].args == (
            "CAP/USDT:USDT",
        )

    asyncio.run(scenario())


def test_rest_cached_candle_finality_is_recomputed_during_cooldown(monkeypatch):
    async def scenario():
        engine = _engine_with_kline_cache()
        engine.exchange = type("Exchange", (), {})()
        rows = _ohlcv_rows(count=3)
        engine.exchange.fetch_ohlcv = AsyncMock(return_value=rows)
        current_bar_open_ms = rows[-1][0]
        clock_ms = [current_bar_open_ms + 5_000]
        monkeypatch.setattr(
            engine_module.time, "time", lambda: clock_ms[0] / 1000,
        )

        first = await engine.fetch_klines("LOBSTER/USDT", "1m", 3, keep_live=True)
        clock_ms[0] += 60_000
        second = await engine.fetch_klines("LOBSTER/USDT", "1m", 3, keep_live=True)

        assert not bool(first.iloc[-1]["is_closed"])
        assert bool(second.iloc[-1]["is_closed"])
        engine.exchange.fetch_ohlcv.assert_awaited_once()

    asyncio.run(scenario())


def test_kline_websocket_idle_timeout_enters_reconnect_backoff(monkeypatch):
    class FakeWebSocket:
        def __init__(self):
            self.calls = 0

        async def watch_ohlcv(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                await asyncio.Event().wait()
            raise asyncio.CancelledError

    async def scenario():
        engine = _engine_with_kline_cache()
        engine.ws_exchange = FakeWebSocket()

        async def no_wait(_seconds):
            return None

        monkeypatch.setattr(
            engine_module,
            "WILD_SWING_KLINE_WS_IDLE_TIMEOUT_SECONDS",
            0.001,
        )
        monkeypatch.setattr(engine_module.asyncio, "sleep", no_wait)

        result = await asyncio.gather(
            engine._wild_swing_kline_loop(LOBSTER),
            return_exceptions=True,
        )

        assert isinstance(result[0], asyncio.CancelledError)
        assert engine.ws_exchange.calls == 2

    asyncio.run(scenario())


def test_ticker_websocket_idle_timeout_retries_without_rest_polling(monkeypatch):
    class FakeWebSocket:
        def __init__(self):
            self.calls = 0

        async def watch_tickers(self):
            self.calls += 1
            if self.calls == 1:
                await asyncio.Event().wait()
            raise asyncio.CancelledError

    async def scenario():
        engine = object.__new__(TradingEngine)
        engine.ws_exchange = FakeWebSocket()
        engine.account = SimpleNamespace(log=lambda *_args, **_kwargs: None)
        engine.last_ticker_success_ts = time.time()
        engine._handle_ticker_data_failure = lambda _error: None
        engine.update_market_prices = AsyncMock()
        sleep_calls = []

        async def no_wait(seconds):
            sleep_calls.append(seconds)

        monkeypatch.setattr(
            engine_module,
            "WILD_SWING_TICKER_WS_IDLE_TIMEOUT_SECONDS",
            0.001,
        )
        monkeypatch.setattr(engine_module.asyncio, "sleep", no_wait)
        await engine._ticker_loop()

        assert engine.ws_exchange.calls == 2
        assert sleep_calls == [1.0]
        engine.update_market_prices.assert_not_awaited()

    asyncio.run(scenario())


def test_order_sizing_uses_canonical_exchange_symbol():
    class FakeExchange:
        def __init__(self):
            self.symbols = []

        def market(self, symbol):
            self.symbols.append(("market", symbol))
            return {
                "linear": True,
                "contractSize": 1,
                "info": {
                    "filters": [
                        {
                            "filterType": "LOT_SIZE",
                            "stepSize": "0.001",
                            "minQty": "0.001",
                            "maxQty": "10000",
                        },
                        {
                            "filterType": "MARKET_LOT_SIZE",
                            "stepSize": "0.001",
                            "minQty": "0.001",
                            "maxQty": "10000",
                        },
                    ],
                },
            }

        def amount_to_precision(self, symbol, amount):
            self.symbols.append(("amount", symbol))
            return f"{float(amount):.3f}"

    exchange = FakeExchange()

    qty = calculate_order_qty(
        exchange, PEPE, margin=1, leverage=3, price=0.01,
    )

    assert qty == 300
    assert exchange.symbols == [
        ("market", "1000PEPE/USDT:USDT"),
        ("amount", "1000PEPE/USDT:USDT"),
    ]


def test_market_price_fetch_uses_exchange_symbols_and_restores_internal_aliases(
    monkeypatch,
):
    class FakeExchange:
        async def fetch_tickers(self, symbols):
            self.symbols = symbols
            return {
                "龙虾/USDT:USDT": {"last": 0.028, "quoteVolume": 1000},
                "1000PEPE/USDT:USDT": {"last": 0.01, "quoteVolume": 2000},
            }

    async def scenario():
        engine = object.__new__(TradingEngine)
        engine.exchange = FakeExchange()
        engine.account = SimpleNamespace(positions={})
        engine.tickers = {}
        engine.ticker_volumes = {}
        engine._observe_channel_entry_quote = lambda *_args: None
        monkeypatch.setattr(engine_module, "DEFAULT_SYMBOLS", [LOBSTER, PEPE])

        await engine.update_market_prices()

        assert engine.exchange.symbols == [
            "龙虾/USDT:USDT", "1000PEPE/USDT:USDT",
        ]
        assert engine.tickers[LOBSTER] == 0.028
        assert engine.tickers[PEPE] == 0.01
        assert engine.ticker_volumes[LOBSTER] == 1000
        assert engine.ticker_volumes[PEPE] == 2000

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("symbol", "exchange_symbol"),
    [
        (LOBSTER, "龙虾/USDT:USDT"),
        (PEPE, "1000PEPE/USDT:USDT"),
    ],
)
def test_testnet_order_boundary_uses_canonical_contract_symbol(
    tmp_path, symbol, exchange_symbol,
):
    class FakeExchange:
        def __init__(self):
            self.create_order = AsyncMock(
                return_value={"id": "test-order", "status": "closed"},
            )

    exchange = FakeExchange()
    raw_create_order = exchange.create_order
    account = BinanceTestnetAccount(
        exchange, state_file=str(tmp_path / "testnet-account.json"),
    )

    asyncio.run(
        account._send_order(
            symbol,
            "market",
            "sell",
            1.0,
            params={"reduceOnly": True},
        )
    )

    raw_create_order.assert_awaited_once_with(
        exchange_symbol, "market", "sell", 1.0, None, {"reduceOnly": True},
    )
