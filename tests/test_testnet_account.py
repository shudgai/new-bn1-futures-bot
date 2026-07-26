import pytest

import core.testnet_account as testnet_module
from core.testnet_account import BinanceTestnetAccount


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeTestnetExchange:
    def __init__(self):
        self.positions = []
        self.orders = []
        self.cancelled = []
        self.leverage = None

    async def load_markets(self):
        return {}

    async def fapiPrivateV2GetBalance(self):
        return [{"asset": "USDT", "balance": "1000", "availableBalance": "900"}]

    async def fapiPrivateV2GetPositionRisk(self):
        return list(self.positions)

    def amount_to_precision(self, symbol, amount):
        return str(round(amount, 3))

    def price_to_precision(self, symbol, price):
        return str(round(price, 4))

    async def set_leverage(self, leverage, symbol):
        self.leverage = (symbol, leverage)

    async def create_order(self, symbol, order_type, side, qty, price=None, params=None):
        order = {
            "id": str(len(self.orders) + 1),
            "symbol": symbol,
            "type": order_type,
            "side": side,
            "amount": qty,
            "params": params or {},
        }
        self.orders.append(order)
        if str(order_type).lower() == "market" and not (params or {}).get("reduceOnly"):
            signed_qty = qty if side == "buy" else -qty
            self.positions = [{
                "symbol": symbol.replace("/", ""),
                "positionAmt": str(signed_qty),
                "entryPrice": "100",
                "markPrice": "100",
                "leverage": str(self.leverage[1]),
                "unRealizedProfit": "0",
            }]
            order["average"] = 100.0
        elif str(order_type).lower() == "market" and (params or {}).get("reduceOnly"):
            self.positions = []
            order["average"] = 101.0
        return order

    async def request(self, path, api, method, params):
        if method == "DELETE":
            self.cancelled.append(params["symbol"])
            return {}
        order = {
            "id": str(len(self.orders) + 1),
            "symbol": params["symbol"],
            "type": params["type"],
            "side": params["side"].lower(),
            "amount": float(params["quantity"]),
            "params": params,
        }
        self.orders.append(order)
        return {"algoId": order["id"]}

    async def cancel_all_orders(self, symbol):
        self.cancelled.append(symbol)


@pytest.mark.anyio
async def test_testnet_account_places_entry_stop_and_take_profit(tmp_path, monkeypatch):
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(
        BinanceTestnetAccount,
        "credentials_configured",
        staticmethod(lambda: True),
    )
    exchange = FakeTestnetExchange()
    account = BinanceTestnetAccount(exchange)

    await account.initialize()
    success = await account.open_position(
        "DOGE/USDT",
        "LONG",
        100.0,
        50.0,
        98.0,
        103.0,
        "Fast_Keltner_SuperTrend",
        atr=1.0,
        leverage=5,
        signal_score=100,
    )

    assert success is True
    assert account.balance == 1000.0
    assert account.available_balance == 900.0
    assert "DOGE/USDT" in account.positions
    assert [order["type"] for order in exchange.orders] == [
        "market",
        "STOP_MARKET",
        "TAKE_PROFIT_MARKET",
    ]
    assert exchange.orders[1]["params"]["reduceOnly"] == "true"
    assert exchange.orders[2]["params"]["reduceOnly"] == "true"


@pytest.mark.anyio
async def test_testnet_account_manual_close_is_reduce_only(tmp_path, monkeypatch):
    monkeypatch.setattr(testnet_module, "STATE_FILE", str(tmp_path / "testnet.json"))
    monkeypatch.setattr(
        BinanceTestnetAccount,
        "credentials_configured",
        staticmethod(lambda: True),
    )
    exchange = FakeTestnetExchange()
    account = BinanceTestnetAccount(exchange)
    await account.initialize()
    await account.open_position(
        "DOGE/USDT", "LONG", 100.0, 50.0, 98.0, 103.0, "test", leverage=5
    )

    success = await account.close_position("DOGE/USDT", 101.0, "手動平倉")

    assert success is True
    assert "DOGE/USDT" not in account.positions
    assert exchange.orders[-1]["params"]["reduceOnly"] is True
    assert "DOGE/USDT" in exchange.cancelled
