"""Explicit testnet activation and startup recovery from leased trade journals."""

import copy
import hashlib
from datetime import datetime
from functools import partial
from zoneinfo import ZoneInfo
from pathlib import Path
import time

from .staged_risk_service import install_staged_runtime, staged_enabled
from .staged_state_store import FileStagedStore
from .staged_testnet_transport import BinanceStagedTransport, testnet_net_pnl


class TradeStore:
    """Keep engine intents and transport accounting in one atomic document."""

    def __init__(self, file_store, document):
        self.file_store = file_store
        self.document = document

    def load(self):
        return copy.deepcopy(self.document.get("engine"))

    def save(self, state):
        self.document["engine"] = copy.deepcopy(state)
        self.flush()

    def flush(self):
        self.file_store.save(self.document)


def _account_id(account):
    # Bind journals to credentials without persisting credentials themselves.
    key = getattr(account.exchange, "apiKey", "")
    if not key:
        raise ValueError("STAGED_ACCOUNT_ID_REQUIRED")
    return hashlib.sha256(key.encode()).hexdigest()


def _directory(account):
    return Path(account.staged_state_directory)


def _attach(account, store):
    doc = store.document
    symbol = doc["symbol"]
    if doc["account_id"] != _account_id(account):
        raise ValueError("STAGED_JOURNAL_ACCOUNT_MISMATCH")
    transport = BinanceStagedTransport(account.exchange, symbol, doc["transport"], store.flush)
    state = doc.get("engine")
    if state:
        if state.get("pending_request") and state["pending_request"]["id"] not in doc["transport"]["requests"]:
            transport.remember(state["pending_request"])
        account.positions[symbol] = dict(side=state["side"], entry_price=state["entry_price"],
                                        qty=state["qty"], sl=state["stop_price"],
                                        staged_position_id=state["position_id"])
    from core.config import TAKER_FEE_RATE, SLIPPAGE_PCT
    runtime = install_staged_runtime(account, symbol, transport, doc["risk_params"], store,
                                     valuation=partial(testnet_net_pnl, fee=TAKER_FEE_RATE, slippage=SLIPPAGE_PCT))
    account._staged_stores[symbol] = store
    return runtime


async def enable_testnet_staged(account, symbol, risk_params, entry_order_ids):
    """Explicit administrative activation; never called by automatic entries.

    Requires a fully filled, unmodified entry and no unmanaged open orders.
    Existing protection must be explicitly migrated before calling this method.
    """
    if symbol in getattr(account, "staged_risk_runtimes", {}):
        raise ValueError("STAGED_RUNTIME_ALREADY_INSTALLED")
    position = account.positions[symbol]
    entry_ids = list(dict.fromkeys(str(value) for value in entry_order_ids))
    if not entry_ids:
        raise ValueError("STAGED_ENTRY_ORDER_IDS_REQUIRED")
    journal = dict(side=position["side"], entry_price=float(position["entry_price"]),
                   initial_qty=float(position["qty"]), entry_order_ids=entry_ids, requests={})
    # Read-only preflight, before setting any flag or creating a journal.
    transport = BinanceStagedTransport(account.exchange, symbol, journal, lambda: None)
    for endpoint in ("openOrders", "openAlgoOrders"):
        orders = await transport._request(endpoint, "GET", {"symbol": transport.raw_symbol})
        if not isinstance(orders, list) or orders:
            raise ValueError("STAGED_UNMANAGED_ORDERS_REQUIRE_MIGRATION")
    remote = await transport.get_position()
    if remote["qty"] != float(position["qty"]) or remote["qty"] <= 0:
        raise ValueError("STAGED_ACTIVATION_POSITION_CHANGED")
    document = dict(version=1, symbol=symbol, account_id=_account_id(account),
                    risk_params=copy.deepcopy(risk_params), transport=journal, finalized=False,
                    opening_position=copy.deepcopy(position))
    name = hashlib.sha256(symbol.encode()).hexdigest()
    file_store = FileStagedStore(_directory(account)/f"{name}.json")
    existing = file_store.load()
    if existing and not existing.get("finalized"):
        file_store.close()
        raise ValueError("STAGED_EXISTING_JOURNAL_REQUIRES_RESTORE")
    store = TradeStore(file_store, document)
    if not hasattr(account, "_staged_stores"):
        account._staged_stores = {}
    try:
        runtime = _attach(account, store)
    except BaseException:
        store.file_store.close()
        raise
    await runtime.engine.reconcile(refresh_open=True)
    if runtime.engine.position.status == "OPEN":
        await runtime.engine.update_stop_loss()
    return runtime


async def restore_testnet_staged(account):
    """Run before refresh, orphan cleanup, or legacy stop restoration."""
    if getattr(account, "staged_risk_runtimes", {}):
        raise ValueError("STAGED_RESTORE_REQUIRES_EMPTY_REGISTRY")
    account._staged_stores = {}
    try:
        for path in sorted(_directory(account).glob("*.json")):
            file_store = FileStagedStore(path)
            try:
                doc = file_store.load()
                if doc.get("version") != 1:
                    raise ValueError("STAGED_UNKNOWN_JOURNAL_VERSION")
                if doc.get("finalized"):
                    file_store.close()
                    continue
                if doc["symbol"] in account._staged_stores:
                    raise ValueError("STAGED_DUPLICATE_SYMBOL_JOURNAL")
                if not doc.get("engine"):
                    raise ValueError("STAGED_ENGINE_JOURNAL_MISSING")
                _attach(account, TradeStore(file_store, doc))
            except BaseException:
                file_store.close()
                raise
        for symbol, meta in account.position_meta.items():
            if staged_enabled({}, meta) and symbol not in account._staged_stores:
                raise ValueError(f"STAGED_RUNTIME_JOURNAL_MISSING: {symbol}")
        for runtime in getattr(account, "staged_risk_runtimes", {}).values():
            await runtime.engine.reconcile(refresh_open=True)
            if runtime.engine.position.status == "OPEN":
                await runtime.engine.update_stop_loss()
    except BaseException:
        release_testnet_staged(account)
        raise


def record_staged_close(account, symbol):
    """Idempotent account projection from the complete, durable execution ledger."""
    store = account._staged_stores[symbol]
    state = store.document["engine"]
    if state["status"] != "CLOSED":
        return False
    position_id = state["position_id"]
    if not any(t.get("staged_position_id") == position_id for t in account.trades):
        ledger = store.document["transport"]
        net = ledger["realized_net_pnl"]
        fills = list(ledger["fills"].values())
        closing = [f for f in fills if str(f["orderId"]) not in ledger["entry_order_ids"]]
        closed_qty = sum(float(f["qty"]) for f in closing)
        price = sum(float(f["price"])*float(f["qty"]) for f in closing)/closed_qty
        account.realized_pnl += net
        account.trades.insert(0, dict(id=f"staged-{position_id}", symbol=symbol,
                                     action=f"CLOSE_{state['side']}", side=state["side"],
                                     qty=store.document["transport"]["initial_qty"],
                                     pnl=net, price=price, fee=sum(float(f["commission"]) for f in fills),
                                     amount=store.document.get("opening_position", {}).get("margin", 0),
                                     time=datetime.now(ZoneInfo("Asia/Taipei")).strftime("%m/%d %H:%M:%S"),
                                     exit_type="STAGED_RISK", status="CLOSED", staged_position_id=position_id,
                                     reason=state["terminal_reason"] or "STAGED_EXCHANGE_EXIT"))
    account.last_closed_at[symbol] = time.time()
    account.positions.pop(symbol, None)
    account.position_meta.pop(symbol, None)
    # Account persistence must succeed before retiring canonical trade evidence.
    account.save_state(strict=True)
    store.document["finalized"] = True
    store.flush()
    store.file_store.close()
    account._staged_stores.pop(symbol)
    account.staged_risk_runtimes.pop(symbol)
    return True


def release_testnet_staged(account):
    """Shutdown only, after all account and quote workers have stopped."""
    for store in getattr(account, "_staged_stores", {}).values():
        store.file_store.close()
    account._staged_stores = {}
    account.staged_risk_runtimes = {}
