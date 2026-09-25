"""USD-M testnet REST transport with durable intents and order-scoped accounting.

Uses the account's authenticated CCXT client, never constructs a live client.
Only one-way USDT positions are supported. Missing history is UNKNOWN, not flat.
"""

import copy
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import math
import re
from urllib.parse import urlparse


class BinanceStagedTransport:
    TERMINAL = {"FILLED", "CANCELED", "EXPIRED", "REJECTED", "EXPIRED_IN_MATCH"}

    def __init__(self, exchange, symbol, journal, save):
        self.exchange = exchange
        self.symbol = symbol
        self.raw_symbol = symbol.replace("/", "").replace(":USDT", "")
        self.journal = journal
        self.save = save
        self._check_endpoint()

    def _check_endpoint(self):
        for api in ("fapiPrivate", "fapiPrivateV2"):
            url = self.exchange.urls["api"][api]
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname not in {
                "testnet.binancefuture.com", "demo-fapi.binance.com",
            }:
                raise ValueError("STAGED_TESTNET_ENDPOINT_REQUIRED")

    async def _request(self, path, method, params, api="fapiPrivate"):
        self._check_endpoint()
        return await self.exchange.request(path, api, method, params)

    def remember(self, request):
        key = request["id"]
        old = self.journal["requests"].get(key)
        if old is not None and old != request:
            raise ValueError("STAGED_CLIENT_ID_REUSED")
        self.journal["requests"][key] = copy.deepcopy(request)
        self.save()

    def _validate_order(self, row, request, *, child=False):
        expected_side = "SELL" if self.journal["side"] == "LONG" else "BUY"
        if (row.get("symbol") != self.raw_symbol or row.get("side") != expected_side
                or row.get("positionSide") != "BOTH"):
            raise ValueError("STAGED_ORDER_IDENTITY_MISMATCH")
        if not child:
            field = "clientAlgoId" if request["kind"] == "STOP" else "clientOrderId"
            if row.get(field) != request["id"]:
                raise ValueError("STAGED_CLIENT_ID_MISMATCH")

    async def place_order(self, request):
        if (request["kind"] not in {"STOP", "PARTIAL_TP", "MARKET_EXIT", "MANUAL_REDUCE"}
                or request["side"] != self.journal["side"]
                or request["reduce_only"] is not True
                or not re.fullmatch(r"[.A-Z:/a-z0-9_-]{1,36}", request["id"])):
            raise ValueError("STAGED_INVALID_REQUEST")
        if request["id"] in self.journal["requests"]:
            self.remember(request)
            return await self.get_order(request["id"])
        qty = float(request["qty"])
        amount = self.exchange.amount_to_precision(self.symbol, qty)
        # Do not silently round half of a position to a different TP fraction.
        if not math.isfinite(qty) or qty <= 0 or float(amount) != qty:
            raise ValueError("STAGED_QUANTITY_PRECISION_REQUIRED")
        params = dict(symbol=self.raw_symbol, side="SELL" if request["side"] == "LONG" else "BUY",
                      positionSide="BOTH", quantity=amount, reduceOnly="true")
        path = "order"
        if request["kind"] == "STOP":
            stop = float(request["stop_price"])
            trigger = self.exchange.price_to_precision(self.symbol, stop)
            if not math.isfinite(stop) or stop <= 0:
                raise ValueError("STAGED_STOP_PRECISION_REQUIRED")
            if float(trigger) != stop:
                if self.exchange.precisionMode != 4:  # CCXT TICK_SIZE
                    raise ValueError("STAGED_TICK_SIZE_REQUIRED")
                tick = Decimal(str(self.exchange.market(self.symbol)["precision"]["price"]))
                rounding = ROUND_CEILING if request["side"] == "LONG" else ROUND_FLOOR
                trigger = str((Decimal(str(stop))/tick).to_integral_value(rounding=rounding)*tick)
            path = "algoOrder"
            params.update(algoType="CONDITIONAL", type="STOP_MARKET", triggerPrice=trigger,
                          workingType="CONTRACT_PRICE", clientAlgoId=request["id"])
        else:
            params.update(type="MARKET", newClientOrderId=request["id"], newOrderRespType="RESULT")
        self.remember(request)
        row = await self._request(path, "POST", params)
        self._validate_order(row, request)
        # Re-query even an ACK: only authoritative order state unlocks the engine.
        return await self.get_order(request["id"])

    async def get_order(self, order_id):
        request = self.journal["requests"][order_id]
        is_stop = request["kind"] == "STOP"
        params = {"clientAlgoId": order_id} if is_stop else {
            "symbol": self.raw_symbol, "origClientOrderId": order_id,
        }
        row = await self._request("algoOrder" if is_stop else "order", "GET", params)
        self._validate_order(row, request)
        actual = row.get("actualOrderId") if is_stop else row.get("orderId")
        if is_stop and actual and str(actual) != "0":
            child = await self._request("order", "GET", {"symbol": self.raw_symbol, "orderId": actual})
            self._validate_order(child, request, child=True)
            if str(child["orderId"]) != str(actual):
                raise ValueError("STAGED_CHILD_ID_MISMATCH")
            row = child
        status = str(row.get("status") or row.get("algoStatus") or "UNKNOWN").upper()
        if actual and str(actual) != "0":
            self.journal.setdefault("order_ids", {})[order_id] = str(actual)
            self.save()
        if is_stop and status in {"TRIGGERED", "FINISHED"}:
            status = "UNKNOWN"  # No child execution evidence yet.
        if status == "NEW":
            status = "OPEN"
        if status == "EXPIRED_IN_MATCH":
            status = "EXPIRED"
        return dict(id=order_id, kind=request["kind"], status=status,
                    executed_qty=float(row.get("executedQty") or 0), exchange_id=actual)

    async def cancel_order(self, order_id):
        before = await self.get_order(order_id)
        if before["status"] in self.TERMINAL:
            return before
        request = self.journal["requests"][order_id]
        child_id = self.journal.get("order_ids", {}).get(order_id)
        if request["kind"] == "STOP" and not child_id:
            await self._request("algoOrder", "DELETE", {"clientAlgoId": order_id})
        else:
            params = {"symbol": self.raw_symbol}
            params.update({"orderId": child_id} if child_id else {"origClientOrderId": order_id})
            await self._request("order", "DELETE", params)
        return await self.get_order(order_id)

    async def _fills(self, order_id):
        rows = {}
        cursor = 0
        while True:
            page = await self._request("userTrades", "GET", {
                "symbol": self.raw_symbol, "orderId": order_id, "fromId": cursor, "limit": 1000,
            })
            for fill in page:
                if (str(fill["orderId"]) != str(order_id) or fill["symbol"] != self.raw_symbol
                        or fill["positionSide"] != "BOTH" or fill["commissionAsset"] != "USDT"):
                    raise ValueError("STAGED_FILL_IDENTITY_OR_FEE_ASSET")
                key = str(fill["id"])
                if key in rows and rows[key] != fill:
                    raise ValueError("STAGED_CONFLICTING_FILL")
                rows[key] = fill
            if len(page) < 1000:
                break
            next_cursor = max(int(row["id"]) for row in page) + 1
            if next_cursor <= cursor:
                raise ValueError("STAGED_TRADE_PAGINATION_STALLED")
            cursor = next_cursor
        return list(rows.values())

    async def get_position(self):
        rows = await self._request("positionRisk", "GET", {"symbol": self.raw_symbol}, "fapiPrivateV2")
        rows = [row for row in rows if row["symbol"] == self.raw_symbol]
        if len(rows) != 1 or rows[0].get("positionSide") != "BOTH":
            raise ValueError("STAGED_ONE_WAY_POSITION_REQUIRED")
        signed = float(rows[0]["positionAmt"])
        sign = 1 if self.journal["side"] == "LONG" else -1
        if not math.isfinite(signed) or signed * sign < 0:
            raise ValueError("STAGED_POSITION_SIDE_CHANGED")
        qty = abs(signed)
        if qty and not math.isclose(float(rows[0]["entryPrice"]), self.journal["entry_price"], rel_tol=1e-8):
            raise ValueError("STAGED_POSITION_ENTRY_CHANGED")
        fills = []
        entered = 0.0
        for entry_id in self.journal["entry_order_ids"]:
            entry = await self._request("order", "GET", {"symbol": self.raw_symbol, "orderId": entry_id})
            if (entry.get("status") != "FILLED" or str(entry["orderId"]) != entry_id
                    or entry.get("symbol") != self.raw_symbol or entry.get("positionSide") != "BOTH"
                    or entry.get("side") != ("BUY" if sign == 1 else "SELL")):
                raise ValueError("STAGED_ENTRY_EXECUTION_REQUIRED")
            trades = await self._fills(entry_id)
            total = sum(float(t["qty"]) for t in trades)
            if not math.isclose(total, float(entry["executedQty"]), abs_tol=1e-10):
                raise ValueError("STAGED_ENTRY_LEDGER_INCOMPLETE")
            entered += total
            fills.extend(trades)
        if not math.isclose(entered, self.journal["initial_qty"], abs_tol=1e-10):
            raise ValueError("STAGED_ENTRY_QUANTITY_MISMATCH")
        exited = 0.0
        for client_id in self.journal["requests"]:
            order = await self.get_order(client_id)
            actual = order["exchange_id"]
            if not actual or str(actual) == "0":
                continue
            trades = await self._fills(actual)
            total = sum(float(t["qty"]) for t in trades)
            if not math.isclose(total, order["executed_qty"], abs_tol=1e-10):
                raise ValueError("STAGED_EXIT_LEDGER_INCOMPLETE")
            exited += total
            fills.extend(trades)
        if not math.isclose(entered-exited, qty, abs_tol=1e-10):
            raise ValueError("STAGED_POSITION_LEDGER_MISMATCH")
        net = sum(float(t["realizedPnl"])-float(t["commission"]) for t in fills)
        if not math.isfinite(net):
            raise ValueError("STAGED_INVALID_LEDGER")
        self.journal["fills"] = {str(t["id"]): t for t in fills}
        self.journal["realized_net_pnl"] = net
        self.save()
        return {"qty": qty, "realized_net_pnl": net}


def testnet_net_pnl(position, price, fee=0.0005, slippage=0.0005):
    """Ledger includes actual opening fees; reserve only remaining exit costs."""
    if not position.native_pnl_valid:
        raise ValueError("STAGED_REALIZED_PNL_REQUIRED")
    sign = 1 if position.side == "LONG" else -1
    execution = price * (1-sign*slippage)
    return position.realized_net_pnl + sign*(execution-position.entry_price)*position.qty - execution*position.qty*fee
