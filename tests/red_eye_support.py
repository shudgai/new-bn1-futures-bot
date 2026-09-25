"""Executable reference contract only; NOT the production trading engine."""

from dataclasses import asdict, dataclass
from threading import RLock
from typing import Callable


RISK_PARAMS = {
    # ASSUMPTION: Using Option A parameters when option="A".
    "A": {"stage_thresholds": {1: 1.0, 2: 3.0},
          "atr_multipliers": {1: None, 2: 1.0, 3: 1.0}, "partial_tp_r": 1.0},
    # ASSUMPTION: Using Option B parameters when option="B".
    "B": {"stage_thresholds": {1: 1.5, 2: 3.0},
          "atr_multipliers": {1: None, 2: 1.5, 3: 1.0}, "partial_tp_r": 1.5},
}


@dataclass
class PositionState:
    side: str = "LONG"
    entry_price: float = 100.0
    initial_stop: float = 99.0
    stop_price: float = 99.0
    qty: float = 10.0
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

    def snapshot(self) -> dict:
        return asdict(self)


class MockExchange:
    """Local responses and authoritative exchange state are independent.

    No wall-clock sleeps: faults are deterministic and do not contact a network.
    Reduce-only orders cannot reverse a position. Resting STOP/TP orders fill
    only when explicitly injected, so a missing execution report is testable.
    """

    def __init__(self, qty: float = 10.0) -> None:
        self.qty = qty
        self.orders: dict[str, dict] = {}
        self.events: list[tuple] = []
        self.place_fault: str | None = None
        self.cancel_fault: str | None = None
        self.position_timeout = False
        self.ambiguous_orders = False
        self.call_count_for_old_logic = 0

    def old_logic(self, *args: object, **kwargs: object) -> None:
        self.call_count_for_old_logic += 1
        raise AssertionError("舊版 2.5U／3U 邏輯被呼叫")

    def place_order(self, order_id: str, kind: str, qty: float,
                    reduce_only: bool = True) -> dict:
        self.events.append(("place", order_id, kind, qty, reduce_only))
        if order_id in self.orders:
            return dict(self.orders[order_id])
        order = dict(id=order_id, kind=kind, qty=qty, status="OPEN",
                     reduce_only=reduce_only)
        self.orders[order_id] = order
        fault, self.place_fault = self.place_fault, None
        # 故障注入：交易所已成交，但網路延遲／斷線吞掉 ACK。
        if fault in {"timeout_after_fill", "unknown_after_fill"}:
            self.fill(order_id)
            if fault == "timeout_after_fill":
                raise TimeoutError("模擬斷線：成交回報遺失")
            return {**order, "status": "UNKNOWN"}
        # 故障注入：委託仍在交易所掛單，本地只得到 UNKNOWN。
        if fault == "unknown_open":
            return {**order, "status": "UNKNOWN"}
        if kind == "MARKET_EXIT":
            self.fill(order_id)
        return dict(order)

    def fill(self, order_id: str) -> None:
        order = self.orders[order_id]
        if order["status"] != "OPEN":
            return
        filled = min(self.qty, order["qty"])
        self.qty -= filled
        order.update(status="FILLED", filled_qty=filled)
        self.events.append(("fill", order_id, filled))

    def cancel_order(self, order_id: str) -> dict:
        self.events.append(("cancel", order_id))
        fault, self.cancel_fault = self.cancel_fault, None
        # 故障注入：撤單與成交競態，或撤單網路延遲而結果未知。
        if fault == "fill_then_timeout":
            self.fill(order_id)
            raise TimeoutError("模擬撤單斷線：TP 已先成交")
        if fault == "timeout":
            raise TimeoutError("模擬網路延遲：撤單未確認")
        order = self.orders[order_id]
        if order["status"] == "OPEN":
            order["status"] = "CANCELED"
        return dict(order)

    def get_position(self) -> dict:
        self.events.append(("get_position",))
        if self.position_timeout:
            raise TimeoutError("模擬重連後對帳仍逾時")
        return {"qty": self.qty}

    def get_order(self, order_id: str) -> dict:
        self.events.append(("get_order", order_id))
        order = self.orders[order_id]
        return {**order, "status": "UNKNOWN"} if self.ambiguous_orders else dict(order)


class ReferenceStagedStrategy:
    """Reference policy with explicitly selectable Option A/B parameters.

    This deliberately small model defines the test adapter API. It does not
    establish production durability, multi-process locking or exchange safety.
    Injected legacy callables must be wired to real legacy hooks by an adapter.
    """

    def __init__(self, position: PositionState, exchange: MockExchange,
                 old_breakeven: Callable, old_drawdown: Callable,
                 risk_params: dict | None = None) -> None:
        self.position = position
        self.exchange = exchange
        self.old_breakeven = old_breakeven
        self.old_drawdown = old_drawdown
        self.logs: list[str] = []
        self.lock = RLock()
        self.risk_params = risk_params or RISK_PARAMS["B"]

    def log(self, message: str) -> None:
        self.logs.append(message)
        print(message)

    def _submit(self, kind: str, qty: float) -> str | None:
        p = self.position
        if p.status != "OPEN":
            return None
        p.next_order += 1
        order_id = f"intent-{p.next_order}"
        p.pending_order_id = order_id  # Reference intent before sending.
        try:
            response = self.exchange.place_order(order_id, kind, qty)
        except TimeoutError:
            response = {"status": "UNKNOWN"}
        if response["status"] == "UNKNOWN":
            p.status = "RECONCILE"
            self.log(f"UNKNOWN -> RECONCILE: {order_id}")
        else:
            p.pending_order_id = None
        return order_id

    def submit_stop(self) -> None:
        with self.lock:
            self._submit("STOP", self.position.qty)

    def reconcile(self) -> None:
        with self.lock:
            p = self.position
            if p.status != "RECONCILE":
                return
            try:
                remote = self.exchange.get_position()
                if remote["qty"] == 0:
                    p.qty, p.status = 0, "CLOSED"
                    self.log("RECONCILED_FLAT: exchange confirms position closed")
                    return
                if p.pending_order_id:
                    order = self.exchange.get_order(p.pending_order_id)
                    if order["status"] not in {"CANCELED", "FILLED", "REJECTED"}:
                        self.log("RECONCILE_WAIT: unresolved or live order")
                        return
                    if order["kind"] == "PARTIAL_TP" and order["status"] == "FILLED":
                        p.is_partial_tp_executed = True
                p.qty = remote["qty"]
                p.pending_order_id = None
                p.status = "OPEN"
            except TimeoutError:
                self.log("RECONCILE_WAIT: network timeout")

    def request_choppy_exit(self) -> None:
        p = self.position
        if p.status != "OPEN":
            return
        p.exit_requested = True
        if p.tp_order_id:
            try:
                order = self.exchange.cancel_order(p.tp_order_id)
                if order["status"] not in {"FILLED", "CANCELED", "REJECTED"}:
                    raise TimeoutError("cancel not terminal")
                p.qty = self.exchange.get_position()["qty"]
                p.tp_order_id = None
            except TimeoutError:
                p.pending_order_id = p.tp_order_id
                p.status = "RECONCILE"
                self.log("CHOPPY_EXIT_WAIT: TP cancel UNKNOWN -> RECONCILE")
                return
        if p.qty == 0:
            p.status = "CLOSED"
            return
        self.log("CHOPPY_EXIT")
        order_id = self._submit("MARKET_EXIT", p.qty)
        if p.status == "OPEN" and order_id:
            p.qty = self.exchange.get_position()["qty"]
            p.status = "CLOSED" if p.qty == 0 else "RECONCILE"

    def tick(self, price: float, atr: float, net_pnl: float, bar: int,
             historical_high: float = 100.0, historical_low: float = 100.0) -> None:
        with self.lock:
            p = self.position
            if p.status != "OPEN":
                return
            # Only post-entry quotes enter extrema; candle history is excluded.
            new_extreme = (price > p.highest_since_entry if p.side == "LONG"
                           else price < p.lowest_since_entry)
            p.highest_since_entry = max(p.highest_since_entry, price)
            p.lowest_since_entry = min(p.lowest_since_entry, price)
            if new_extreme:
                p.bars_without_new_extreme = 0
            elif bar > p.last_bar:
                p.bars_without_new_extreme += 1
            p.last_bar = max(bar, p.last_bar)
            p.peak_net_pnl = max(p.peak_net_pnl, net_pnl)
            risk = abs(p.entry_price - p.initial_stop)
            sign = 1 if p.side == "LONG" else -1
            achieved = sign * (price - p.entry_price) / risk
            thresholds = self.risk_params["stage_thresholds"]
            new_stage = (3 if achieved >= thresholds[2] else
                         (2 if achieved >= thresholds[1] else 1))
            p.stage = max(p.stage, new_stage)
            if p.stage >= 2:
                distance = self.risk_params["atr_multipliers"][p.stage] * atr
                candidate = (p.highest_since_entry - distance if sign == 1
                             else p.lowest_since_entry + distance)
                p.stop_price = (max(p.stop_price, candidate) if sign == 1
                                else min(p.stop_price, candidate))
            if (achieved >= self.risk_params["partial_tp_r"]
                    and not p.is_partial_tp_executed and not p.partial_tp_sent):
                p.partial_tp_sent = True
                p.tp_order_id = self._submit("PARTIAL_TP", p.qty * 0.5)
                self.log(f"STAGE_{p.stage}: 50% PARTIAL_TP submitted; ATR stop tightened")
            giveback = ((p.peak_net_pnl - net_pnl) / p.peak_net_pnl
                        if p.peak_net_pnl > 0 else 0)
            if p.exit_requested or (p.stage >= 2 and
                                    p.bars_without_new_extreme >= 5 and giveback >= 0.25):
                self.request_choppy_exit()

    def evaluate_entry(self, price: float, ma7: float, atr: float) -> str:
        with self.lock:
            if price > ma7 + 2.5 * atr:
                self.log("ENTRY_BLOCKED: Deviation > 2.5 ATR")
                return "ENTRY_BLOCKED"
            return "ENTRY_ELIGIBLE"
