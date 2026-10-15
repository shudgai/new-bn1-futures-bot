import asyncio

from core.gates.trend_riding_strategy import AntiSlippageOrderExecutor


class FakeExchange:
    def __init__(self):
        self.orders = {}
        self.last_price = 100.0

    def price_to_precision(self, symbol, price):
        return str(price)

    async def fetch_ticker(self, symbol):
        return {"last": self.last_price}

    async def fetch_order(self, order_id, symbol):
        return dict(self.orders[order_id])


class FakeAccount:
    def __init__(self, partial_on_first=False):
        self.exchange = FakeExchange()
        self.tickers = {"LOBSTER/USDT": 100.5}
        self.position_meta = {"LOBSTER/USDT": {}}
        self.sent = []
        self.partial_on_first = partial_on_first

    def log(self, *args):
        pass

    def save_state(self):
        pass

    async def _send_order(self, symbol, order_type, side, qty, price, params):
        assert order_type == "limit"
        assert params["reduceOnly"] is True
        assert all(order["status"] != "open" for order in self.exchange.orders.values())
        order_id = str(len(self.sent) + 1)
        self.sent.append((qty, price))
        if len(self.sent) == 1:
            self.exchange.orders[order_id] = {
                "id": order_id,
                "status": "open",
                "filled": 0.0,
                "remaining": qty,
                "average": None,
            }
        else:
            self.exchange.orders[order_id] = {
                "id": order_id,
                "status": "closed",
                "filled": qty,
                "remaining": 0.0,
                "average": price,
            }
        return {"id": order_id, "status": "open"}

    async def _cancel_order(self, symbol, order_id):
        order = self.exchange.orders[order_id]
        order.update({
            "status": "canceled",
            "filled": 0.3 if self.partial_on_first and order_id == "1" else order["filled"],
            "remaining": 0.7 if self.partial_on_first and order_id == "1" else order["remaining"],
        })

    async def refresh(self, force=False):
        self.refreshed = force


def test_watchdog_cancels_before_repricing_and_reports_only_confirmed_full_fill():
    account = FakeAccount()

    success, order = asyncio.run(
        AntiSlippageOrderExecutor.execute_confirmed_limit_close(
            account,
            "LOBSTER/USDT",
            "SHORT",
            1.0,
            100.0,
            watchdog_timeout_sec=0,
        )
    )

    assert success is True
    assert order["status"] == "CLOSED"
    assert order["filled"] == 1.0
    assert len(account.sent) == 2
    assert account.exchange.orders["1"]["status"] == "canceled"


def test_partial_fill_is_reconciled_and_never_retried_for_the_original_quantity():
    account = FakeAccount(partial_on_first=True)

    success, result = asyncio.run(
        AntiSlippageOrderExecutor.execute_confirmed_limit_close(
            account,
            "LOBSTER/USDT",
            "LONG",
            1.0,
            100.0,
            watchdog_timeout_sec=0,
        )
    )

    assert success is False
    assert result["error"] == "PARTIAL_FILL"
    assert account.refreshed is True
    assert len(account.sent) == 1
