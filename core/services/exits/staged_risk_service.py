"""Staged R/ATR decisions and serialized reduce-only order lifecycle.

No exchange client is constructed here. Order intents are persisted before I/O;
transport acknowledgements and authoritative reconciliation are separate steps.
"""

import asyncio
import copy
import math
import uuid
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Callable, Protocol


class StagedTransport(Protocol):
    async def place_order(self, request: dict) -> dict: ...
    async def cancel_order(self, order_id: str) -> dict: ...
    async def get_order(self, order_id: str) -> dict: ...
    async def get_position(self) -> dict: ...


@dataclass
class StagedPosition:
    side: str = "LONG"
    entry_price: float = 100.0
    initial_stop: float = 99.0
    stop_price: float = 99.0
    qty: float = 0.0
    stage: int = 1
    status: str = "OPEN"
    peak_net_pnl: float = 0.0
    bars_without_new_extreme: int = 0
    highest_since_entry: float = 100.0
    lowest_since_entry: float = 100.0
    last_bar: int = -1
    partial_tp_sent: bool = False
    is_partial_tp_executed: bool = False
    tp_order_id: str | None = None
    pending_order_id: str | None = None
    exit_requested: bool = False
    next_order: int = 0
    position_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    stop_order_id: str | None = None
    stop_order_price: float | None = None
    stop_order_qty: float | None = None
    pending_operation: str | None = None
    pending_request: dict | None = None
    terminal_reason: str | None = None
    risk_params: dict | None = None
    realized_net_pnl: float = 0.0
    native_pnl_valid: bool = True

    @classmethod
    def restore(cls, data: dict) -> "StagedPosition":
        names = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in names})

    def snapshot(self) -> dict:
        return asdict(self)


class StagedRiskEngine:
    """One engine/lock per position; account runtime registry owns its lifetime."""

    TERMINAL = {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}

    def __init__(self, position: StagedPosition, transport: StagedTransport,
                 risk_params: dict, persist: Callable[[dict], None],
                 log: Callable[[str], None]) -> None:
        self.position = position
        self.transport = transport
        self.persist = persist
        self.log = log
        self.lock = asyncio.Lock()
        # Freeze policy per position; configuration changes cannot loosen a trade.
        params = copy.deepcopy(position.risk_params or risk_params)
        # JSON stores numeric mapping keys as strings.
        self.thresholds = {int(k): float(v) for k, v in params["stage_thresholds"].items()}
        self.multipliers = {int(k): (None if v is None else float(v))
                            for k, v in params["atr_multipliers"].items()}
        self.partial_tp_r = float(params["partial_tp_r"])
        if not (all(math.isfinite(v) for v in (*self.thresholds.values(), self.partial_tp_r))
                and 0 < self.thresholds[1] < self.thresholds[2] and self.partial_tp_r > 0):
            raise ValueError("Invalid staged R thresholds")
        if not all(math.isfinite(self.multipliers[k]) and self.multipliers[k] > 0 for k in (2, 3)):
            raise ValueError("Invalid ATR multipliers")
        if not all(math.isfinite(v) and v > 0 for v in
                   (position.entry_price, position.initial_stop, position.stop_price,
                    position.highest_since_entry, position.lowest_since_entry)):
            raise ValueError("Invalid position prices")
        if not math.isfinite(position.qty) or position.qty < 0:
            raise ValueError("Invalid position quantity")
        if position.side not in {"LONG", "SHORT"}:
            raise ValueError("Invalid position side")
        sign = 1 if position.side == "LONG" else -1
        if sign * (position.entry_price - position.initial_stop) <= 0:
            raise ValueError("Initial stop must define positive directional R")
        if position.stage not in {1, 2, 3}:
            raise ValueError("Invalid stage")
        position.risk_params = params

    def _save(self) -> None:
        # A failing persistence boundary must prevent any subsequent order send.
        try:
            self.persist(self.position.snapshot())
        except Exception:
            self.position.status = "RECONCILE"
            raise

    def _unknown(self, reason: str) -> None:
        self.position.status = "RECONCILE"
        self._save()
        self.log(f"RECONCILE_WAIT: {reason}")

    async def _place(self, kind: str, qty: float, stop_price: float | None = None) -> str | None:
        p = self.position
        if p.status != "OPEN":
            return None
        p.next_order += 1
        order_id = f"sr-{p.position_id}-{p.next_order}"
        request = dict(id=order_id, kind=kind, qty=qty, side=p.side,
                       reduce_only=True, stop_price=stop_price)
        p.pending_order_id = order_id
        p.pending_operation = "PLACE"
        p.pending_request = request
        p.status = "RECONCILE"  # Persist intent before the first network await.
        if kind == "PARTIAL_TP":
            p.partial_tp_sent = True
            p.tp_order_id = order_id
        elif kind == "STOP":
            p.stop_order_id = order_id
            p.stop_order_price = stop_price
            p.stop_order_qty = qty
        self._save()
        try:
            response = await self.transport.place_order(request)
        except Exception as exc:
            self._unknown(f"PLACE UNKNOWN {order_id}: {type(exc).__name__}")
            return order_id
        if response.get("id") != order_id:
            self._unknown(f"PLACE correlation mismatch {order_id}")
            return order_id
        status = str(response.get("status", "UNKNOWN")).upper()
        if status not in self.TERMINAL | {"OPEN", "PARTIALLY_FILLED"}:
            self._unknown(f"PLACE UNKNOWN {order_id}")
            return order_id
        if status in {"FILLED", "PARTIALLY_FILLED"}:
            try:
                await self._refresh_position()
            except Exception as exc:
                self._unknown(f"FILL reconciliation: {type(exc).__name__}")
                return order_id
            if kind == "PARTIAL_TP" and status == "FILLED":
                p.is_partial_tp_executed = True
        if status in {"CANCELED", "REJECTED", "EXPIRED"}:
            self.log(f"ORDER_{status}: {kind} {order_id}")
            if kind == "STOP":
                self._unknown(f"STOP {status}; protection requires reconciliation")
                return order_id
            if kind == "PARTIAL_TP":
                p.partial_tp_sent = False
                p.tp_order_id = None
        if kind in {"MARKET_EXIT", "MANUAL_REDUCE"} and status in {"OPEN", "PARTIALLY_FILLED"}:
            self._unknown("market reduction awaiting terminal fill")
            return order_id
        p.pending_order_id = None
        p.pending_operation = None
        p.pending_request = None
        p.status = "CLOSED" if p.qty == 0 else "OPEN"
        self._save()
        return order_id

    async def _refresh_position(self) -> None:
        remote = await self.transport.get_position()
        qty = float(remote["qty"])
        if not math.isfinite(qty) or qty < 0:
            raise ValueError("Invalid authoritative quantity")
        if "realized_net_pnl" in remote:
            realized = float(remote["realized_net_pnl"])
            if not math.isfinite(realized):
                raise ValueError("Invalid realized net PNL")
            self.position.realized_net_pnl = realized
            self.position.native_pnl_valid = True
        elif qty != self.position.qty:
            # A partial fill changes exposure; do not mistake the missing
            # realized leg for a drawdown. Require authoritative trade accounting.
            self.position.native_pnl_valid = False
        self.position.qty = qty

    async def _cancel(self, order_id: str) -> bool:
        p = self.position
        p.pending_order_id = order_id
        p.pending_operation = "CANCEL"
        p.status = "RECONCILE"
        self._save()
        try:
            response = await self.transport.cancel_order(order_id)
            if (response.get("id") != order_id
                    or str(response.get("status", "UNKNOWN")).upper() not in self.TERMINAL):
                self._unknown(f"CANCEL UNKNOWN {order_id}")
                return False
            await self._refresh_position()
        except Exception as exc:
            self._unknown(f"CANCEL UNKNOWN {order_id}: {type(exc).__name__}")
            return False
        if order_id == p.tp_order_id and response.get("status") == "FILLED":
            p.is_partial_tp_executed = True
        p.pending_order_id = None
        p.pending_operation = None
        p.pending_request = None
        p.status = "CLOSED" if p.qty == 0 else "OPEN"
        self._save()
        return True

    async def update_stop_loss(self, desired_stop_price: float | None = None) -> None:
        async with self.lock:
            p = self.position
            if p.status != "OPEN":
                return
            target = p.stop_price if desired_stop_price is None else float(desired_stop_price)
            if not math.isfinite(target) or target <= 0:
                raise ValueError("Invalid stop price")
            p.stop_price = max(p.stop_price, target) if p.side == "LONG" else min(p.stop_price, target)
            if p.stop_order_id and p.stop_order_price == p.stop_price and p.stop_order_qty == p.qty:
                return
            if p.stop_order_id:
                if not await self._cancel(p.stop_order_id):
                    return
                p.stop_order_id = None
            if p.status == "OPEN":
                await self._place("STOP", p.qty, p.stop_price)

    async def reconcile(self, *, refresh_open: bool = False) -> None:
        async with self.lock:
            p = self.position
            if refresh_open and p.status == "OPEN":
                p.status = "RECONCILE"
                self._save()
            if p.status != "RECONCILE":
                return
            try:
                await self._refresh_position()
                # Flat alone does not authorize reopening: clear known live orders.
                pending_id = p.pending_order_id
                ids = list(dict.fromkeys(order_id for order_id in
                           (p.pending_order_id, p.tp_order_id, p.stop_order_id) if order_id))
                for order_id in ids:
                    order = await self.transport.get_order(order_id)
                    if order.get("id") != order_id:
                        self._unknown(f"query correlation mismatch {order_id}")
                        return
                    status = str(order.get("status", "UNKNOWN")).upper()
                    if status not in self.TERMINAL:
                        known_open = (order_id != pending_id or
                                      (p.pending_operation == "PLACE" and order.get("kind") in {"STOP", "PARTIAL_TP"}))
                        if p.qty > 0 and known_open and status in {"OPEN", "PARTIALLY_FILLED"}:
                            continue  # Definitive placement ACK; pending cancels still wait.
                        if p.qty == 0 and status in {"OPEN", "PARTIALLY_FILLED"}:
                            if not await self._cancel(order_id):
                                return
                        else:
                            self._unknown(f"unresolved or live order {order_id}")
                            return
                    if order_id == p.tp_order_id and status == "FILLED":
                        p.is_partial_tp_executed = True
                    if order_id == p.stop_order_id and status in self.TERMINAL:
                        p.stop_order_id = None
                        p.stop_order_price = None
                        p.stop_order_qty = None
                # Re-read quantity after terminal order observations to avoid stale fills.
                await self._refresh_position()
            except Exception as exc:
                self._unknown(f"reconciliation: {type(exc).__name__}")
                return
            p.pending_order_id = None
            p.pending_operation = None
            p.pending_request = None
            p.stop_order_id = None if p.qty == 0 else p.stop_order_id
            p.status = "CLOSED" if p.qty == 0 else "OPEN"
            if p.qty == 0:
                p.terminal_reason = "RECONCILED_FLAT"
                self.log("RECONCILED_FLAT: exchange confirms position closed")
            self._save()

    async def request_close(self, reason: str) -> bool:
        async with self.lock:
            if self.position.status != "OPEN":
                return False
            self.position.terminal_reason = reason
            await self._exit()
            return self.position.status == "CLOSED"

    async def request_reduce(self, fraction: float) -> bool:
        async with self.lock:
            p = self.position
            if p.status != "OPEN" or not 0 < fraction < 1:
                return False
            before = p.qty
            order_id = await self._place("MANUAL_REDUCE", before*fraction)
            if p.status == "OPEN" and p.qty == before:
                p.pending_order_id = order_id
                p.pending_operation = "PLACE"
                self._unknown("manual reduction awaiting terminal fill")
            return p.status == "OPEN" and p.qty < before

    async def _exit(self) -> None:
        p = self.position
        if p.status != "OPEN":
            return
        p.exit_requested = True
        self._save()
        # TP and STOP must be terminal before a new full-close intent.
        for attribute in ("tp_order_id", "stop_order_id"):
            order_id = getattr(p, attribute)
            if order_id:
                if not await self._cancel(order_id):
                    return
                setattr(p, attribute, None)
        if p.qty == 0:
            p.status = "CLOSED"
            self._save()
            return
        p.terminal_reason = p.terminal_reason or "CHOPPY_EXIT"
        self.log(p.terminal_reason)
        await self._place("MARKET_EXIT", p.qty)
        # An acknowledged but unfilled market close must not be submitted twice.
        if p.status == "OPEN":
            p.pending_order_id = f"sr-{p.position_id}-{p.next_order}"
            p.pending_operation = "PLACE"
            self._unknown("market exit awaiting terminal fill")

    async def tick(self, price: float, atr: float, net_pnl: float, bar: int) -> None:
        async with self.lock:
            p = self.position
            if p.status != "OPEN":
                return
            if not all(math.isfinite(v) for v in (price, atr, net_pnl)) or min(price, atr) <= 0:
                self.log("STAGED_INVALID_MARKET_DATA")
                return
            if bar < p.last_bar:
                self.log("STAGED_STALE_BAR_IGNORED")
                return
            new_extreme = (price > p.highest_since_entry if p.side == "LONG"
                           else price < p.lowest_since_entry)
            p.highest_since_entry = max(p.highest_since_entry, price)
            p.lowest_since_entry = min(p.lowest_since_entry, price)
            if new_extreme:
                p.bars_without_new_extreme = 0
            elif bar > p.last_bar:
                p.bars_without_new_extreme += 1
            p.last_bar = bar
            p.peak_net_pnl = max(p.peak_net_pnl, net_pnl)
            sign = 1 if p.side == "LONG" else -1
            current_r = sign * (price-p.entry_price) / abs(p.entry_price-p.initial_stop)
            candidate_stage = 3 if current_r >= self.thresholds[2] else (2 if current_r >= self.thresholds[1] else 1)
            p.stage = max(p.stage, candidate_stage)
            if p.stage >= 2:
                extreme = p.highest_since_entry if sign == 1 else p.lowest_since_entry
                candidate = extreme-sign*self.multipliers[p.stage]*atr
                p.stop_price = max(p.stop_price, candidate) if sign == 1 else min(p.stop_price, candidate)
            self._save()
            drawdown = ((p.peak_net_pnl-net_pnl)/p.peak_net_pnl if p.peak_net_pnl > 0 else 0)
            # Once a full exit is requested, no fresh partial may precede it.
            if p.exit_requested or (p.stage >= 2 and p.bars_without_new_extreme >= 5 and drawdown >= 0.25):
                await self._exit()
                return
            if current_r >= self.partial_tp_r and not p.partial_tp_sent and not p.is_partial_tp_executed:
                await self._place("PARTIAL_TP", p.qty*0.5)
                self.log(f"STAGE_{p.stage}: 50% PARTIAL_TP submitted")


def staged_enabled(position: dict, meta: dict | None = None) -> bool:
    """Strict opt-in; string 'false' must never activate a financial policy."""
    return position.get("use_staged_risk_engine") is True or (meta or {}).get("use_staged_risk_engine") is True


def native_net_pnl(position: StagedPosition, price: float,
                   fee: float = 0.0005, slippage: float = 0.0005) -> float:
    if not position.native_pnl_valid:
        raise ValueError("STAGED_REALIZED_PNL_REQUIRED")
    sign = 1 if position.side == "LONG" else -1
    execution = price*(1-sign*slippage)
    return (position.realized_net_pnl + sign*(execution-position.entry_price)*position.qty
            - (position.entry_price+execution)*position.qty*fee)


@dataclass
class StagedRuntime:
    engine: StagedRiskEngine
    valuation: Callable[[StagedPosition, float], float] = native_net_pnl
    sync_native_stops: bool = False


async def run_staged_position(account: Any, symbol: str, frame: Any, price: float) -> None:
    """Production dispatcher. Missing dependencies fail closed, never fall back."""
    runtime = getattr(account, "staged_risk_runtimes", {}).get(symbol)
    if runtime is None:
        account.log(f"STAGED_RUNTIME_MISSING: {symbol}; legacy fallback disabled", "ERROR")
        return
    if runtime.engine.position.status == "RECONCILE":
        # Quotes never resolve UNKNOWN or authorize another order. Recovery is
        # an explicit reconciliation cycle (e.g. account refresh/reconnect).
        return
    try:
        net = runtime.valuation(runtime.engine.position, price)
    except ValueError as exc:
        account.log(f"STAGED_VALUATION_WAIT: {exc}", "ERROR")
        if runtime.sync_native_stops and runtime.engine.position.status == "OPEN":
            await runtime.engine.update_stop_loss()
        return
    await runtime.engine.tick(price, float(frame.iloc[-2]["atr"]), net,
                              int(frame.iloc[-1]["timestamp"]))
    if runtime.sync_native_stops and runtime.engine.position.status == "OPEN":
        await runtime.engine.update_stop_loss()


def staged_entry_permission(price: float, ma7: float, atr: float,
                            side: str) -> tuple[bool, str]:
    if side not in {"LONG", "SHORT"} or not all(math.isfinite(v) and v > 0 for v in (price, ma7, atr)):
        return False, "ENTRY_BLOCKED: invalid staged market data"
    sign = 1 if side == "LONG" else -1
    if sign*(price-ma7) > 2.5*atr:
        return False, "ENTRY_BLOCKED: Deviation > 2.5 ATR"
    return True, "ENTRY_ELIGIBLE"


def install_staged_runtime(account: Any, symbol: str, transport: StagedTransport,
                           risk_params: dict, store: Any,
                           valuation: Callable[[StagedPosition, float], float] = native_net_pnl,
                           sync_native_stops: bool = True) -> StagedRuntime:
    """Explicit opt-in with a durable store/lease and injected exchange transport.

    Caller owns the store lifetime. Existing unmanaged orders must be migrated
    before opt-in; this function never cancels unrelated exchange orders.
    """
    registry = getattr(account, "staged_risk_runtimes", None)
    if registry is None:
        registry = account.staged_risk_runtimes = {}
    if symbol in registry:
        raise ValueError("A staged runtime already owns this symbol")
    raw = account.positions[symbol]
    saved = store.load()
    if saved:
        state = StagedPosition.restore(saved)
        known_id = raw.get("staged_position_id") or account.position_meta.get(symbol, {}).get("staged_position_id")
        if known_id != state.position_id or (state.status == "CLOSED" and float(raw["qty"]) > 0):
            raise ValueError("Stored staged trade identity requires explicit reconciliation")
        if state.side != raw["side"] or state.entry_price != float(raw["entry_price"]):
            raise ValueError("Stored staged identity does not match account position")
        # Reconnect never assumes cached quantity/order status is authoritative.
        if state.status != "CLOSED":
            state.status = "RECONCILE"
    else:
        entry = float(raw["entry_price"])
        stop = float(raw["sl"])
        state = StagedPosition(side=raw["side"], entry_price=entry, initial_stop=stop,
                               stop_price=stop, qty=float(raw["qty"]),
                               highest_since_entry=entry, lowest_since_entry=entry)

    def persist(snapshot: dict) -> None:
        store.save(snapshot)  # Canonical durable state; must raise on failure.
        meta = account.position_meta.setdefault(symbol, {})
        meta.update(use_staged_risk_engine=True, staged_risk_state=snapshot,
                    staged_position_id=snapshot["position_id"], sl=snapshot["stop_price"])
        current = account.positions.get(symbol)
        if current is not None:
            current.update(use_staged_risk_engine=True, staged_risk_state=snapshot,
                           staged_position_id=snapshot["position_id"],
                           sl=snapshot["stop_price"], qty=snapshot["qty"])
        account.save_state()

    engine = StagedRiskEngine(state, transport, risk_params, persist,
                              lambda message: account.log(message, "INFO"))
    persist(state.snapshot())
    runtime = StagedRuntime(engine, valuation, sync_native_stops)
    registry[symbol] = runtime
    return runtime


async def refresh_staged_runtimes(account: Any) -> None:
    """Authoritative account cycle; distinct from quote-driven decisions."""
    for runtime in tuple(getattr(account, "staged_risk_runtimes", {}).values()):
        await runtime.engine.reconcile(refresh_open=True)
