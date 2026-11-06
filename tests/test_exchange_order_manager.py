import asyncio
import pytest
from core.services.ExchangeOrderManager import ExchangeOrderManager


class FakeExchange:
    def __init__(self):
        self.requests = []

    async def request(self, endpoint, api, method, params):
        self.requests.append((endpoint, api, method, params))
        if method == "POST":
            return {"algoId": "algo_12345", "clientAlgoId": "client_1"}
        elif method == "DELETE":
            return {"code": 200, "msg": "success"}
        return {}


class FakeAccount:
    def __init__(self, is_real=True):
        if is_real:
            self.exchange = FakeExchange()
        self.positions = {}
        self.position_meta = {}
        self.state_saved = False

    def _raw_symbol(self, symbol):
        return symbol.replace("/", "")

    def _price_to_precision(self, symbol, price):
        return f"{price:.6f}"

    def _amount_to_precision(self, symbol, qty):
        return f"{qty:.2f}"

    def save_state(self):
        self.state_saved = True

    def log(self, text, level):
        pass


def test_native_stop_order_placed_on_exchange():
    account = FakeAccount(is_real=True)
    pos = {
        "symbol": "1000PEPE/USDT",
        "side": "LONG",
        "entry_price": 0.0050,
        "qty": 10000.0,
    }
    account.positions["1000PEPE/USDT"] = pos
    account.position_meta["1000PEPE/USDT"] = {}

    ok, reason, details = asyncio.run(ExchangeOrderManager.place_or_update_native_stop_order(
        account, "1000PEPE/USDT", pos, target_stop_price=0.0052,
    ))

    assert ok is True
    assert reason == "NATIVE_STOP_ORDER_PLACED"
    assert details["algoId"] == "algo_12345"
    assert len(account.exchange.requests) == 1
    req = account.exchange.requests[0]
    assert req[0] == "algoOrder"
    assert req[2] == "POST"
    assert req[3]["side"] == "SELL"
    assert req[3]["type"] == "STOP_MARKET"
    assert req[3]["reduceOnly"] == "true"
    assert float(req[3]["triggerPrice"]) == 0.0052


def test_native_stop_order_updated_on_profit_advance():
    account = FakeAccount(is_real=True)
    pos = {
        "symbol": "1000PEPE/USDT",
        "side": "LONG",
        "entry_price": 0.0050,
        "qty": 10000.0,
        "native_lock_algo_id": "algo_old_1",
        "native_locked_price": 0.0051,
    }
    account.positions["1000PEPE/USDT"] = pos
    account.position_meta["1000PEPE/USDT"] = {
        "native_lock_algo_id": "algo_old_1",
        "native_locked_price": 0.0051,
    }

    ok, reason, details = asyncio.run(ExchangeOrderManager.place_or_update_native_stop_order(
        account, "1000PEPE/USDT", pos, target_stop_price=0.0053,
    ))

    assert ok is True
    assert len(account.exchange.requests) == 2  # 1 DELETE, 1 POST
    del_req = account.exchange.requests[0]
    assert del_req[2] == "DELETE"
    assert del_req[3]["algoId"] == "algo_old_1"

    post_req = account.exchange.requests[1]
    assert post_req[2] == "POST"
    assert float(post_req[3]["triggerPrice"]) == 0.0053


def test_native_stop_order_cancelled_on_exit():
    account = FakeAccount(is_real=True)
    account.position_meta["1000PEPE/USDT"] = {"native_lock_algo_id": "algo_to_cancel"}

    cancelled = asyncio.run(ExchangeOrderManager.cancel_native_stop_order(account, "1000PEPE/USDT"))
    assert cancelled is True
    assert len(account.exchange.requests) == 1
    req = account.exchange.requests[0]
    assert req[2] == "DELETE"
    assert req[3]["algoId"] == "algo_to_cancel"
    assert "native_lock_algo_id" not in account.position_meta["1000PEPE/USDT"]
