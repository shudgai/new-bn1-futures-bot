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


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_real_account_partial_ledger_and_restart(setup_account, side):
    async def scenario():
        account, ex = setup_account(RestExchange(side))
        runtime = await account.enable_staged_risk(SYMBOL, POLICY, ["1"])
        assert runtime.engine.position.stop_order_id
        ex.price = 103 if side == "LONG" else 97
        await runtime.engine.tick(ex.price, 1, runtime.valuation(runtime.engine.position, ex.price), 1)
        assert runtime.engine.position.qty == 5
        expected = 15-0.5-ex.price*5*0.0005
        assert runtime.engine.position.realized_net_pnl == pytest.approx(expected)
        await runtime.engine.update_stop_loss()
        stage, stop = runtime.engine.position.stage, runtime.engine.position.stop_price
        posts = sum(method == "POST" for _, method, _ in ex.calls)
        release_testnet_staged(account)
        restored, _ = setup_account(ex)
        await restored.initialize()
        state = restored.staged_risk_runtimes[SYMBOL].engine.position
        assert (state.qty, state.stage, state.stop_price) == (5, stage, stop)
        assert state.realized_net_pnl == pytest.approx(expected)
        assert sum(method == "POST" for _, method, _ in ex.calls) == posts
        release_testnet_staged(restored)
    asyncio.run(scenario())


def test_cancel_timeout_never_submits_replacement(setup_account):
    async def scenario():
        account, ex = setup_account()
        runtime = await account.enable_staged_risk(SYMBOL, POLICY, ["1"])
        ex.cancel_timeout = True
        await runtime.engine.update_stop_loss(100.5)
        assert runtime.engine.position.status == "RECONCILE"
        assert sum(method == "POST" for _, method, _ in ex.calls) == 1
        await account.refresh(force=True)
        assert runtime.engine.position.status == "RECONCILE"
        release_testnet_staged(account)
    asyncio.run(scenario())


def test_lost_ack_restores_same_client_id_without_resubmit(setup_account):
    async def scenario():
        account, ex = setup_account()
        ex.place_timeout = True
        runtime = await account.enable_staged_risk(SYMBOL, POLICY, ["1"])
        assert runtime.engine.position.status == "RECONCILE"
        client_id = runtime.engine.position.stop_order_id
        release_testnet_staged(account)
        restored, _ = setup_account(ex)
        await restored.initialize()
        assert restored.staged_risk_runtimes[SYMBOL].engine.position.stop_order_id == client_id
        assert sum(method == "POST" for _, method, _ in ex.calls) == 1
        release_testnet_staged(restored)
    asyncio.run(scenario())


def test_lagging_fill_ledger_blocks_until_complete(setup_account):
    async def scenario():
        account, ex = setup_account()
        runtime = await account.enable_staged_risk(SYMBOL, POLICY, ["1"])
        ex.price = 103
        ex.hide_exit_fills = True
        await runtime.engine.tick(103, 1, 29, 1)
        assert runtime.engine.position.status == "RECONCILE"
        posts = sum(method == "POST" for _, method, _ in ex.calls)
        await runtime.engine.tick(104, 1, 39, 2)
        assert sum(method == "POST" for _, method, _ in ex.calls) == posts
        ex.hide_exit_fills = False
        await runtime.engine.reconcile()
        assert runtime.engine.position.qty == 5
        assert runtime.engine.position.status == "OPEN"
        release_testnet_staged(account)
    asyncio.run(scenario())


def test_closed_trade_uses_full_ledger_and_projects_once(setup_account):
    async def scenario():
        account, ex = setup_account()
        runtime = await account.enable_staged_risk(SYMBOL, POLICY, ["1"])
        ex.price = 103
        await runtime.engine.tick(103, 1, 29, 1)
        for bar in range(2, 7):
            ex.price = 102
            await runtime.engine.tick(102, 1, 15, bar)
        assert runtime.engine.position.status == "CLOSED"
        await account.refresh(force=True)
        assert len(account.trades) == 1
        assert account.trades[0]["pnl"] == pytest.approx(25-0.5-0.2575-0.255)
        assert not account.staged_risk_runtimes
        again, _ = setup_account(ex)
        await again.initialize()
        assert len(again.trades) == 1
        assert again.realized_pnl == pytest.approx(account.realized_pnl)
    asyncio.run(scenario())


def test_missing_journal_stops_startup_before_legacy(setup_account):
    async def scenario():
        account, _ = setup_account()
        account.position_meta[SYMBOL] = {"use_staged_risk_engine": True}
        with pytest.raises(ValueError, match="JOURNAL_MISSING"):
            await account.initialize()
        account._cancel_orphan_entry_orders.assert_not_awaited()
        account._restore_exchange_initial_stops.assert_not_awaited()
    asyncio.run(scenario())


def test_live_endpoint_is_rejected_before_any_request(setup_account):
    async def scenario():
        account, ex = setup_account()
        ex.urls = {"api": {"fapiPrivate": "https://fapi.binance.com/fapi/v1"}}
        with pytest.raises(ValueError, match="TESTNET_ENDPOINT"):
            await account.enable_staged_risk(SYMBOL, POLICY, ["1"])
        assert not ex.calls
        assert not account.staged_risk_runtimes
    asyncio.run(scenario())


def test_flag_off_does_not_construct_transport(setup_account):
    async def scenario():
        account, ex = setup_account()
        await account.initialize()
        assert not ex.calls
        assert not account.staged_risk_runtimes
    asyncio.run(scenario())


def test_triggered_stop_waits_for_child_then_records_fill(setup_account):
    async def scenario():
        account, ex = setup_account()
        runtime = await account.enable_staged_risk(SYMBOL, POLICY, ["1"])
        client_id = runtime.engine.position.stop_order_id
        algo = ex.algos[client_id]
        algo["algoStatus"] = "TRIGGERED"
        await runtime.engine.reconcile(refresh_open=True)
        assert runtime.engine.position.status == "RECONCILE"
        assert len(ex.algos) == 1
        algo["actualOrderId"] = "20"
        ex.orders["20"] = dict(orderId="20", clientOrderId="child", symbol="BTCUSDT",
                               positionSide="BOTH", side="SELL", status="FILLED", executedQty="10")
        ex.qty = 0
        ex.price = 99
        ex.fills["20"] = [ex.fill("20", 10, -10, 0.495)]
        await account.refresh(force=True)
        assert not account.staged_risk_runtimes
        assert account.trades[0]["pnl"] == pytest.approx(-10.995)
        assert account.trades[0]["price"] == 99
        assert sum(method == "POST" for _, method, _ in ex.calls) == 1
    asyncio.run(scenario())


def test_cancel_success_requires_followup_terminal_query(setup_account):
    async def scenario():
        account, ex = setup_account()
        runtime = await account.enable_staged_risk(SYMBOL, POLICY, ["1"])
        old = runtime.engine.position.stop_order_id
        await runtime.engine.update_stop_loss(100.5)
        events = [(path, method) for path, method, _ in ex.calls]
        delete = events.index(("algoOrder", "DELETE"))
        assert events[delete+1] == ("algoOrder", "GET")
        assert ex.algos[old]["algoStatus"] == "CANCELED"
        assert runtime.engine.position.stop_order_id != old
        release_testnet_staged(account)
    asyncio.run(scenario())


@pytest.mark.parametrize("side,target,expected", [("LONG", 100.12341, 100.1235), ("SHORT", 99.12349, 99.1234)])
def test_price_rounding_never_loosens_protection(setup_account, side, target, expected):
    async def scenario():
        account, ex = setup_account(RestExchange(side))
        runtime = await account.enable_staged_risk(SYMBOL, POLICY, ["1"])
        await runtime.engine.update_stop_loss(target)
        assert float(ex.algos[runtime.engine.position.stop_order_id]["triggerPrice"]) == expected
        release_testnet_staged(account)
    asyncio.run(scenario())


def test_incorrect_query_identity_cannot_unlock_runtime(setup_account):
    async def scenario():
        account, ex = setup_account()
        runtime = await account.enable_staged_risk(SYMBOL, POLICY, ["1"])
        ex.algos[runtime.engine.position.stop_order_id]["clientAlgoId"] = "other-trade"
        await runtime.engine.reconcile(refresh_open=True)
        assert runtime.engine.position.status == "RECONCILE"
        await runtime.engine.tick(103, 1, 29, 1)
        assert sum(method == "POST" for _, method, _ in ex.calls) == 1
        release_testnet_staged(account)
    asyncio.run(scenario())


def test_account_save_failure_preserves_unfinalized_journal(setup_account, monkeypatch):
    async def scenario():
        account, ex = setup_account()
        runtime = await account.enable_staged_risk(SYMBOL, POLICY, ["1"])
        ex.price = 103
        await runtime.engine.tick(103, 1, 29, 1)
        for bar in range(2, 7):
            ex.price = 102
            await runtime.engine.tick(102, 1, 15, bar)
        save = account.save_state
        def broken(*, strict=False):
            if strict:
                raise OSError("disk full")
            save()
        monkeypatch.setattr(account, "save_state", broken)
        with pytest.raises(OSError, match="disk full"):
            await account.refresh(force=True)
        assert account._staged_stores[SYMBOL].document["finalized"] is False
        expected = account.realized_pnl
        monkeypatch.setattr(account, "save_state", save)
        await account.refresh(force=True)
        assert len(account.trades) == 1
        assert account.realized_pnl == expected
        assert not account._staged_stores
    asyncio.run(scenario())


def test_unknown_external_reduction_does_not_guess_realized_pnl(setup_account):
    async def scenario():
        account, ex = setup_account()
        runtime = await account.enable_staged_risk(SYMBOL, POLICY, ["1"])
        ex.qty = 8
        await runtime.engine.reconcile(refresh_open=True)
        assert runtime.engine.position.status == "RECONCILE"
        assert runtime.engine.position.qty == 10
        assert not account.trades
        release_testnet_staged(account)
    asyncio.run(scenario())


def test_transport_rejects_other_fee_assets_before_activation(setup_account):
    async def scenario():
        account, ex = setup_account()
        ex.fills["1"][0]["commissionAsset"] = "BNB"
        with pytest.raises(ValueError, match="FEE_ASSET"):
            await account.enable_staged_risk(SYMBOL, POLICY, ["1"])
        assert not account.staged_risk_runtimes
        assert not any(method == "POST" for _, method, _ in ex.calls)
    asyncio.run(scenario())


def test_manual_reduce_and_close_share_staged_ledger(setup_account):
    async def scenario():
        account, ex = setup_account()
        runtime = await account.enable_staged_risk(SYMBOL, POLICY, ["1"])
        ex.price = 102
        assert await account.partial_close_position(SYMBOL, 102, "manual partial", 0.2)
        assert runtime.engine.position.qty == 8
        assert not runtime.engine.position.partial_tp_sent
        assert not account.trades
        assert await account.close_position(SYMBOL, 102, "manual close", is_manual=True)
        assert len(account.trades) == 1
        assert account.trades[0]["pnl"] == pytest.approx(20-0.5-0.51)
        assert account.trades[0]["reason"] == "manual close"
        assert not account.staged_risk_runtimes
    asyncio.run(scenario())


def test_concurrent_activation_has_one_position_lease(setup_account):
    async def scenario():
        account, ex = setup_account()
        await account.enable_staged_risk(SYMBOL, POLICY, ["1"])
        competitor, _ = setup_account(ex)
        with pytest.raises(BlockingIOError):
            await competitor.enable_staged_risk(SYMBOL, POLICY, ["1"])
        assert sum(method == "POST" for _, method, _ in ex.calls) == 1
        release_testnet_staged(account)
    asyncio.run(scenario())


def test_nonterminal_market_ack_blocks_additional_reduction(setup_account):
    async def scenario():
        account, ex = setup_account()
        runtime = await account.enable_staged_risk(SYMBOL, POLICY, ["1"])
        real_place = runtime.engine.transport.place_order
        async def delayed_status(request):
            result = await real_place(request)
            if request["kind"] == "MANUAL_REDUCE":
                result["status"] = "PARTIALLY_FILLED"
            return result
        runtime.engine.transport.place_order = delayed_status
        assert not await account.partial_close_position(SYMBOL, 100, "partial", 0.2)
        assert runtime.engine.position.status == "RECONCILE"
        posts = sum(method == "POST" for _, method, _ in ex.calls)
        assert not await account.partial_close_position(SYMBOL, 100, "partial", 0.2)
        assert sum(method == "POST" for _, method, _ in ex.calls) == posts
        await runtime.engine.reconcile()
        assert runtime.engine.position.status == "OPEN"
        assert runtime.engine.position.qty == 8
        release_testnet_staged(account)
    asyncio.run(scenario())
