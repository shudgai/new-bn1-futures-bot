"""Bridge to the current core runner, not an implementation of staged risk.

Inline legacy branches are observed with read-only Python tracing. No core
function is patched, copied, replaced, or evaluated from modified source.
"""

import ast
import asyncio
import inspect
import math
import sys
from types import SimpleNamespace
from typing import Any, Callable

import pandas as pd

from core.services import symbol_runner
from core.services.exits import profit_protection_service as profit_core
from core.services.strategies.unified_entry_strategy import UnifiedEntryStrategy


class ProductionCapabilityMissing(NotImplementedError):
    """The real core has no equivalent API; the adapter must not invent it."""


class ProductionValuationMismatch(AssertionError):
    """An independent test PNL cannot silently replace the real core valuation."""


class _AccountBridge:
    """Account I/O double, not PaperAccount/TestnetAccount integration coverage."""

    def __init__(self, owner: Any, raw_position: dict) -> None:
        self.owner = owner
        self.positions = ({owner.symbol: raw_position}
                          if owner.position.qty > 0 else {})
        self.position_meta: dict = {}
        self.channel_profit_reentries: dict = {}
        self.saved_states: list[dict] = []
        self.order_sequence = 0

    def log(self, message: str, level: str = "INFO") -> None:
        self.owner.logs.append(message)
        print(f"{level}: {message}")

    def save_state(self) -> None:
        # An observation only, NOT a claim of real durable storage.
        import copy
        self.saved_states.append(copy.deepcopy(self.position_meta))

    async def _order(self, symbol: str, kind: str, qty: float) -> bool:
        self.order_sequence += 1
        response = self.owner.exchange.place_order(
            f"core-order-{self.order_sequence}", kind, qty, reduce_only=True,
        )
        # Only exchange-confirmed fills update the local account view.
        if response["status"] != "FILLED":
            return False
        remaining = self.owner.exchange.get_position()["qty"]
        if remaining == 0:
            self.positions.pop(symbol, None)
        else:
            self.positions[symbol]["qty"] = remaining
        return True

    async def close_position(self, symbol: str, price: float, reason: str,
                             **kwargs: Any) -> bool:
        self.owner.order_reasons.append(reason)
        return await self._order(symbol, "MARKET_EXIT", self.positions[symbol]["qty"])

    async def partial_close_position(self, symbol: str, price: float, reason: str,
                                    fraction: float = 0.5) -> bool:
        self.owner.order_reasons.append(reason)
        return await self._order(symbol, "PARTIAL_TP", self.positions[symbol]["qty"] * fraction)

    async def open_position(self, **kwargs: Any) -> bool:
        # Never silently bypass the real account's entry/risk validation.
        raise ProductionCapabilityMissing(
            "Account open route requires its own production transport adapter")


class LegacyProductionAdapter:
    """Runs the production symbol loop with supplied market/account test doubles.

    risk_params is recorded as requested metadata. fe8b72bc does not consume it;
    this adapter intentionally does not calculate stages or ATR stops for core.
    """

    symbol = "RED_EYE/USDT"

    def __init__(self, position: Any, exchange: Any, old_breakeven: Callable,
                 old_drawdown: Callable, risk_params: dict) -> None:
        self.position = position
        self.exchange = exchange
        self.risk_params = risk_params
        self.logs: list[str] = []
        self.order_reasons: list[str] = []
        self.observed_legacy: list[str] = []
        self.trace_failures: list[AssertionError] = []
        self.native_net: float | None = None
        self.production_calls = 0
        self.raw = dict(side=position.side, entry_price=position.entry_price,
                        qty=position.qty, margin=1000.0, leverage=1,
                        open_timestamp=1.0, entry_mode="CHANNEL_SWING",
                        sl=position.stop_price, staged_risk_params=risk_params)
        self.raw["channel_profit_protection"] = {
            "identity": [position.side, 1.0, position.entry_price, position.qty],
            "policy": "realtime_peak_exit_v1", "peak_net": position.peak_net_pnl,
            "locked_net": 0.0, "pending": False,
        }
        self.account = _AccountBridge(self, self.raw)
        self.engine = SimpleNamespace(
            account=self.account, tickers={},
            _take_over_manual_position=lambda *args: None,
            get_velocity_drop_ratio=lambda symbol: 1.0,
        )
        self._hooks = self._find_legacy_lines(old_breakeven, old_drawdown)

    @staticmethod
    def _find_legacy_lines(old_be: Callable, old_dd: Callable) -> dict:
        # Locate actual branch-body lines by AST, never by guessed line numbers.
        source, first = inspect.getsourcelines(profit_core.protection)
        tree = ast.parse("".join(source))
        conditions = {
            "not triggered and peak_net >= 2.5": ("legacy_breakeven", old_be),
            "not triggered and peak_net >= 3.0": ("legacy_drawdown", old_dd),
        }
        hooks = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and ast.unparse(node.test) in conditions:
                hooks[first + node.body[0].lineno - 1] = conditions[ast.unparse(node.test)]
        if len(hooks) != 2:
            raise ProductionCapabilityMissing(
                "Legacy source changed: review tracing anchors before using adapter")
        return hooks

    def _trace(self, frame: Any, event: str, arg: Any) -> Any:
        if frame.f_code is not profit_core.protection.__code__:
            return None
        if event == "line":
            if "net" in frame.f_locals:
                self.native_net = float(frame.f_locals["net"])
            if frame.f_lineno in self._hooks:
                name, observer = self._hooks[frame.f_lineno]
                self.observed_legacy.append(name)
                self.logs.append(f"LEGACY_BRANCH_EXECUTED: {name}")
                try:
                    observer()
                except AssertionError as exc:
                    # Defer test failure until the unchanged core completes.
                    self.trace_failures.append(exc)
        return self._trace

    def _frame(self, price: float, atr: float, bar: int,
               historical_high: float, historical_low: float) -> pd.DataFrame:
        # Market input mapping: neutral closed bodies isolate the risk path.
        # Historical extrema stay in old rows and are genuinely passed to core.
        entry = self.position.entry_price
        rows = [dict(timestamp=(bar + i) * 60000, open=entry, close=entry,
                     high=max(entry, historical_high), low=min(entry, historical_low),
                     atr=atr, kc_upper=entry+10*atr, kc_lower=entry-10*atr,
                     kc_middle=entry, ema_20=entry, ma3=entry, ma7=entry,
                     ma15=entry, volume=100) for i in range(12)]
        rows[-1].update(open=price, close=price, high=price, low=price)
        return pd.DataFrame(rows)

    def _sync(self) -> None:
        p = self.position
        if self.symbol not in self.account.positions:
            p.qty, p.status = 0, "CLOSED"
        else:
            p.qty = self.raw["qty"]
        state = self.raw.get("channel_profit_protection", {})
        if "peak_net" in state:
            p.peak_net_pnl = state["peak_net"]
        if state.get("stop_price", 0) > 0:
            p.stop_price = state["stop_price"]
        # No guessed Stage/extrema/RECONCILE state is synthesized here.

    def tick_native(self, price: float, atr: float, bar: int,
                    historical_high: float = 100, historical_low: float = 100) -> None:
        """Exercise actual core valuation; used by adapter provenance tests."""
        frame = self._frame(price, atr, bar, historical_high, historical_low)
        previous_trace = sys.gettrace()
        self.native_net = None
        log_start = len(self.logs)
        try:
            sys.settrace(self._trace)
            self.production_calls += 1
            asyncio.run(symbol_runner.process_single_symbol_runner(
                self.engine, self.symbol, float(bar), None, False,
                exit_frame=frame, exit_quote=price, exit_only=True,
            ))
        finally:
            sys.settrace(previous_trace)
            self._sync()
        if self.trace_failures:
            raise AssertionError("PRODUCTION_LEGACY_NOT_SILENT: " +
                                 ", ".join(self.observed_legacy)) from self.trace_failures[0]
        if any("處理失敗" in message for message in self.logs[log_start:]):
            raise AssertionError("PRODUCTION_RUNNER_ERROR: " + self.logs[-1])

    def tick(self, price: float, atr: float, net_pnl: float, bar: int,
             historical_high: float = 100, historical_low: float = 100) -> None:
        self.tick_native(price, atr, bar, historical_high, historical_low)
        if self.native_net is not None and not math.isclose(
                self.native_net, net_pnl, rel_tol=1e-9, abs_tol=1e-9):
            raise ProductionValuationMismatch(
                f"Core net={self.native_net}, contract net={net_pnl}; "
                "independent PNL injection is unsupported; core was NOT overwritten")

    def submit_stop(self) -> None:
        raise ProductionCapabilityMissing(
            "MISSING_STAGED_STOP_ROUTE: runner has no staged native STOP intent API; "
            "existing TestnetAccount native stops require a separate transport adapter")

    def reconcile(self) -> None:
        raise ProductionCapabilityMissing(
            "MISSING_STAGED_RECONCILE: runner has no staged UNKNOWN reconciliation API")

    def evaluate_entry(self, price: float, ma7: float, atr: float) -> str:
        frame = self._frame(price, atr, 1, ma7, ma7)
        frame["ma7"] = ma7
        frame["ema_20"] = ma7
        frame["kc_middle"] = ma7
        frame["kc_upper"] = ma7+0.5*atr
        frame["kc_lower"] = ma7-0.5*atr
        frame.loc[frame.index[-3], ["open", "close", "ma3"]] = [ma7-0.2*atr, ma7+0.8*atr, ma7-0.1*atr]
        frame.loc[frame.index[-2], ["open", "close", "ma3"]] = [ma7, ma7+atr, ma7]
        frame["high"] = frame[["open", "close"]].max(axis=1)
        frame["low"] = frame[["open", "close"]].min(axis=1)
        allowed, reason, _ = UnifiedEntryStrategy().evaluate_entry(
            frame, price, "LONG", symbol=self.symbol,
            use_staged_risk_engine=self.raw.get("use_staged_risk_engine", False),
        )
        self.logs.append(reason)  # Keep real reason, do not fabricate contract text.
        print(f"PRODUCTION_ENTRY: {reason}")
        return "ENTRY_ELIGIBLE" if allowed else "ENTRY_BLOCKED"


class _InjectedTransport:
    """Map a production order DTO to the supplied synchronous MockExchange API."""

    def __init__(self, exchange: Any) -> None:
        self.exchange = exchange
        self.requests: list[dict] = []

    async def place_order(self, request: dict) -> dict:
        self.requests.append(dict(request))
        return self.exchange.place_order(request["id"], request["kind"], request["qty"],
                                         reduce_only=request["reduce_only"])

    async def cancel_order(self, order_id: str) -> dict:
        return self.exchange.cancel_order(order_id)

    async def get_order(self, order_id: str) -> dict:
        return self.exchange.get_order(order_id)

    async def get_position(self) -> dict:
        return self.exchange.get_position()


class ProductionAdapter(LegacyProductionAdapter):
    """Mapping only: all stages, orders and reconciliation execute in core."""

    def __init__(self, position: Any, exchange: Any, old_breakeven: Callable,
                 old_drawdown: Callable, risk_params: dict) -> None:
        super().__init__(position, exchange, old_breakeven, old_drawdown, risk_params)
        from core.services.exits.staged_risk_service import StagedPosition, StagedRiskEngine, StagedRuntime
        self.state = StagedPosition.restore(vars(position))
        self.transport = _InjectedTransport(exchange)
        self.risk_engine = StagedRiskEngine(self.state, self.transport, risk_params,
                                             self._persist_staged, self._log_staged)
        self.runtime = StagedRuntime(self.risk_engine)
        self.account.staged_risk_runtimes = {self.symbol: self.runtime}
        self.raw["use_staged_risk_engine"] = True
        self._persist_staged(self.state.snapshot())

    def _log_staged(self, message: str) -> None:
        self.logs.append(message)
        print(message)

    def _persist_staged(self, snapshot: dict) -> None:
        self.raw["staged_risk_state"] = snapshot
        self.raw["qty"] = snapshot["qty"]
        self.raw["sl"] = snapshot["stop_price"]
        self.account.position_meta.setdefault(self.symbol, {}).update(
            use_staged_risk_engine=True, staged_risk_state=snapshot,
        )
        self.account.save_state()
        self._sync()

    def _sync(self) -> None:
        if not hasattr(self, "state"):
            return super()._sync()
        for key, value in self.state.snapshot().items():
            if hasattr(self.position, key):
                setattr(self.position, key, value)
        if self.state.status == "CLOSED":
            self.account.positions.pop(self.symbol, None)

    def tick(self, price: float, atr: float, net_pnl: float, bar: int,
             historical_high: float = 100, historical_low: float = 100) -> None:
        # Inject the valuation boundary, not a production decision function.
        previous = self.runtime.valuation
        self.runtime.valuation = lambda state, quote: net_pnl
        try:
            super().tick_native(price, atr, bar, historical_high, historical_low)
        finally:
            self.runtime.valuation = previous

    def tick_native(self, price: float, atr: float, bar: int,
                    historical_high: float = 100, historical_low: float = 100) -> None:
        super().tick_native(price, atr, bar, historical_high, historical_low)

    def submit_stop(self) -> None:
        try:
            asyncio.run(self.risk_engine.update_stop_loss())
        finally:
            self._sync()

    def reconcile(self) -> None:
        try:
            asyncio.run(self.risk_engine.reconcile())
        finally:
            self._sync()


def create_strategy(position: Any, exchange: Any, old_breakeven: Callable,
                    old_drawdown: Callable, risk_params: dict) -> ProductionAdapter:
    return ProductionAdapter(position, exchange, old_breakeven, old_drawdown, risk_params)


def create_legacy_strategy(position: Any, exchange: Any, old_breakeven: Callable,
                           old_drawdown: Callable, risk_params: dict) -> LegacyProductionAdapter:
    return LegacyProductionAdapter(position, exchange, old_breakeven, old_drawdown, risk_params)
