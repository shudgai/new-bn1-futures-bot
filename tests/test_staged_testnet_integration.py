"""REST-boundary tests: production account, runtime, transport and JSON journals.

No network credentials, live orders or original contract fixtures are used.
"""

import asyncio
import copy
from unittest.mock import AsyncMock

import pytest

import core.testnet_account as accounts
from core.services.exits.staged_testnet_runtime import release_testnet_staged
from core.services.exits.staged_testnet_transport import BinanceStagedTransport


POLICY = {"stage_thresholds": {1: 1.5, 2: 3}, "atr_multipliers": {1: None, 2: 1.5, 3: 1}, "partial_tp_r": 1.5}
SYMBOL = "BTC/USDT"


class RestExchange:
    precisionMode = 4
    apiKey = "offline-test-account"
    urls = {"api": {"fapiPrivate": "https://testnet.binancefuture.com/fapi/v1",
                    "fapiPrivateV2": "https://testnet.binancefuture.com/fapi/v2"}}

    def __init__(self, side="LONG"):
        self.side = side
        self.qty = 10.0
        self.price = 100.0
        self.calls = []
        self.orders = {"1": dict(orderId="1", clientOrderId="entry", symbol="BTCUSDT",
                                  side="BUY" if side == "LONG" else "SELL", positionSide="BOTH",
                                  status="FILLED", executedQty="10")}
        self.algos = {}
        self.fills = {"1": [self.fill("1", 10, 0, 0.5)]}
        self.cancel_timeout = False
        self.place_timeout = False
        self.hide_exit_fills = False

    def fill(self, oid, qty, pnl, commission):
        return dict(id=f"{oid}1", orderId=oid, symbol="BTCUSDT", positionSide="BOTH",
                    qty=str(qty), realizedPnl=str(pnl), commission=str(commission),
                    commissionAsset="USDT", price=str(self.price))

    def market(self, symbol):
        return {"precision": {"price": 0.0001}}

    async def load_markets(self):
        return {}

    def amount_to_precision(self, symbol, qty):
        return str(round(qty, 3))

    def price_to_precision(self, symbol, price):
        return str(round(price, 4))

    async def fapiPrivateV2GetBalance(self):
        return [dict(asset="USDT", balance="1000", availableBalance="900")]

    async def fapiPrivateV2GetPositionRisk(self):
        return [dict(symbol="BTCUSDT", positionSide="BOTH",
                     positionAmt=str(self.qty if self.side == "LONG" else -self.qty),
                     entryPrice="100", markPrice=str(self.price), leverage="10", unRealizedProfit="0")]

    async def request(self, path, api, method, params):
        self.calls.append((path, method, copy.deepcopy(params)))
        if path == "positionRisk":
            return await self.fapiPrivateV2GetPositionRisk()
        if path in {"openOrders", "openAlgoOrders"}:
            return []
        if path == "userTrades":
            oid = str(params["orderId"])
            return [] if self.hide_exit_fills and oid != "1" else copy.deepcopy(self.fills.get(oid, []))
        if method == "POST":
            oid = str(len(self.orders)+len(self.algos)+1)
            base = dict(symbol="BTCUSDT", side=params["side"], positionSide=params["positionSide"])
            if path == "algoOrder":
                row = dict(base, algoId=oid, clientAlgoId=params["clientAlgoId"], algoStatus="NEW",
                           actualOrderId="", quantity=params["quantity"], triggerPrice=params["triggerPrice"])
                self.algos[params["clientAlgoId"]] = row
            else:
                qty = float(params["quantity"])
                assert params["reduceOnly"] == "true" and qty <= self.qty
                self.qty -= qty
                row = dict(base, orderId=oid, clientOrderId=params["newClientOrderId"], status="FILLED",
                           executedQty=str(qty))
                self.orders[oid] = row
                sign = 1 if self.side == "LONG" else -1
                self.fills[oid] = [self.fill(oid, qty, sign*(self.price-100)*qty, self.price*qty*0.0005)]
            if self.place_timeout:
                self.place_timeout = False
                raise TimeoutError("accepted but ACK lost")
            return copy.deepcopy(row)
        if path == "algoOrder":
            row = self.algos[params["clientAlgoId"]]
        elif "orderId" in params:
            row = self.orders[str(params["orderId"])]
        else:
            row = next(r for r in self.orders.values() if r["clientOrderId"] == params["origClientOrderId"])
        if method == "DELETE":
            if self.cancel_timeout:
                raise TimeoutError("cancel unknown")
            row["algoStatus" if path == "algoOrder" else "status"] = "CANCELED"
            return {"code": "200", "msg": "success"}
        return copy.deepcopy(row)


@pytest.fixture
def setup_account(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "STATE_FILE", str(tmp_path/"account.json"))
    monkeypatch.setattr(accounts, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(accounts.BinanceTestnetAccount, "credentials_configured", staticmethod(lambda: True))

    def create(exchange=None):
        ex = exchange or RestExchange()
        account = accounts.BinanceTestnetAccount(ex)
        if ex.qty:
            account.positions[SYMBOL] = dict(side=ex.side, qty=ex.qty, entry_price=100,
                                             sl=99 if ex.side == "LONG" else 101)
        account._cancel_orphan_entry_orders = AsyncMock()
        account._restore_exchange_initial_stops = AsyncMock()
        return account, ex
    return create




































