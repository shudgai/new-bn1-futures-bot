"""Persistent Owner-approved SMALL/BRIDGE/LIVE-BIG entry authority."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
from typing import Any, Mapping, Optional, Sequence
import uuid

from core.services.entry_contract import is_entry_doji


TIMEFRAME_MS = 60_000
SMALL_BODY_MAX = 0.25
BIG_BODY_MIN = 0.50
@dataclass
class WaitSymbolState:
    last_closed_bar_id: Optional[int] = None
    active_side: Optional[str] = None
    setup_bar_id: Optional[int] = None
    setup_atr: Optional[float] = None
    last_live_bar_id: Optional[int] = None
    restart_blocked_live_bar_id: Optional[int] = None
    frozen_live_atr: Optional[float] = None
    position_active: bool = False
    rebuild_after_fill: bool = False
    suspended: bool = False
    diagnostic: Optional[str] = None


class WaitEntryAuthority:
    """Observe only new authoritative bars and issue one stable claim per trigger."""

    def __init__(self, state_path: Optional[os.PathLike[str] | str] = None):
        self._path = Path(state_path) if state_path is not None else (
            Path(__file__).resolve().parents[2] / "data" / "wait_entry_state.json"
        )
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {
            "version": 2,
            "symbols": {},
            "claims": {},
            "events": {},
            "arbitrations": {},
        }
        self._primed_symbols: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("WAIT state could not be loaded safely") from exc
        if (
            not isinstance(loaded, dict)
            or loaded.get("version") not in (1, 2)
            or not isinstance(loaded.get("symbols"), dict)
            or not isinstance(loaded.get("claims"), dict)
        ):
            raise RuntimeError("WAIT state has an unsupported or invalid schema")
        migrated = loaded.get("version") == 1
        if migrated:
            loaded["version"] = 2
            loaded["events"] = {}
            loaded["arbitrations"] = {}
        if (
            not isinstance(loaded.get("events"), dict)
            or not isinstance(loaded.get("arbitrations"), dict)
        ):
            raise RuntimeError("WAIT state has an invalid outbox or arbitration record")
        self._data = loaded
        for value in self._data["symbols"].values():
            if not isinstance(value, dict):
                raise RuntimeError("WAIT state has an invalid symbol record")
        if any(not isinstance(value, dict) for value in self._data["claims"].values()):
            raise RuntimeError("WAIT state has an invalid claim record")
        changed = migrated
        for trigger_id, claim in self._data["claims"].items():
            if claim.get("status") == "CLAIMED":
                claim["status"] = "UNKNOWN"
                self._append_event(
                    "CLAIM_QUARANTINED_RESTART",
                    claim["symbol"],
                    trigger_id,
                    {"trigger_id": trigger_id},
                )
                changed = True
            elif claim.get("status") == "AVAILABLE":
                claim["status"] = "EXPIRED_RESTART"
                self._append_event(
                    "TRIGGER_EXPIRED_RESTART",
                    claim["symbol"],
                    trigger_id,
                    {"trigger_id": trigger_id},
                )
                changed = True
        if changed:
            self._save()

    def _append_event(
        self,
        event_type: str,
        symbol: str,
        event_key: str,
        payload: Mapping[str, Any],
    ) -> str:
        identity = f"{event_type}|{self._symbol_key(symbol)}|{event_key}"
        event_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        self._data["events"].setdefault(event_id, {
            "event_id": event_id,
            "type": event_type,
            "symbol": self._symbol_key(symbol),
            "event_key": event_key,
            "payload": dict(payload),
            "status": "PENDING",
            "created_at_ms": int(time.time() * 1000),
        })
        return event_id

    def pending_events(
        self,
        symbol: Optional[str] = None,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._lock:
            symbol_key = self._symbol_key(symbol) if symbol is not None else None
            pending = [
                dict(event)
                for event in self._data["events"].values()
                if event.get("status") == "PENDING"
                and (symbol_key is None or event.get("symbol") == symbol_key)
            ]
            pending.sort(key=lambda event: (event["created_at_ms"], event["event_id"]))
            return pending[:max(0, int(limit))]

    def acknowledge_events(self, event_ids: Sequence[str]) -> int:
        with self._lock:
            changed = 0
            for event_id in event_ids:
                event = self._data["events"].get(str(event_id))
                if isinstance(event, dict) and event.get("status") == "PENDING":
                    event["status"] = "DELIVERED"
                    event["delivered_at_ms"] = int(time.time() * 1000)
                    changed += 1
            if changed:
                self._save()
            return changed

    def active_claims(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {"trigger_id": trigger_id, **claim}
                for trigger_id, claim in self._data["claims"].items()
                if claim.get("status") in {"CLAIMED", "UNKNOWN"}
            ]

    def has_unknown_claims(self, except_trigger_id: Optional[str] = None) -> bool:
        with self._lock:
            return any(
                trigger_id != except_trigger_id and claim.get("status") == "UNKNOWN"
                for trigger_id, claim in self._data["claims"].items()
            )

    def has_inflight_for_symbol(
        self,
        symbol: str,
        *,
        except_trigger_id: Optional[str] = None,
    ) -> bool:
        with self._lock:
            symbol_key = self._symbol_key(symbol)
            return any(
                trigger_id != except_trigger_id
                and claim.get("symbol") == symbol_key
                and claim.get("status") in {"CLAIMED", "UNKNOWN"}
                for trigger_id, claim in self._data["claims"].items()
            )

    @staticmethod
    def position_uuid_for_trigger(trigger_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"WAIT:{trigger_id}"))

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self._path.name}.",
            dir=str(self._path.parent),
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(self._data, stream, separators=(",", ":"), sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
            directory_fd = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    def _state(self, symbol: str) -> WaitSymbolState:
        raw = self._data["symbols"].get(symbol)
        if raw is None:
            return WaitSymbolState()
        try:
            return WaitSymbolState(**raw)
        except TypeError as exc:
            raise RuntimeError(f"WAIT state for {symbol} is invalid") from exc

    def _set_state(self, symbol: str, state: WaitSymbolState) -> None:
        self._data["symbols"][symbol] = asdict(state)

    @staticmethod
    def _symbol_key(symbol: str) -> str:
        from core.symbol_aliases import is_lobster_symbol
        return "LOBSTER/USDT" if is_lobster_symbol(symbol) else symbol

    @staticmethod
    def canonical_symbol(symbol: str) -> str:
        return WaitEntryAuthority._symbol_key(symbol)

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        try:
            result = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return result if math.isfinite(result) else None

    @classmethod
    def _candle(cls, row: Mapping[str, Any]) -> Optional[dict[str, float]]:
        values = {
            name: cls._number(row.get(name))
            for name in ("timestamp", "open", "high", "low", "close", "atr")
        }
        if any(value is None for value in values.values()):
            return None
        candle = {name: float(value) for name, value in values.items()}
        if (
            candle["timestamp"] < 0
            or min(candle["open"], candle["high"], candle["low"], candle["close"], candle["atr"]) <= 0
            or candle["high"] <= candle["low"]
            or candle["high"] < max(candle["open"], candle["close"], candle["low"])
            or candle["low"] > min(candle["open"], candle["close"], candle["high"])
        ):
            return None
        return candle

    @staticmethod
    def _small_direction(candle: Mapping[str, float], reference_atr: float) -> Optional[str]:
        if is_entry_doji(SimpleNamespace(**candle)):
            return None
        body = abs(candle["close"] - candle["open"])
        if body > SMALL_BODY_MAX * reference_atr:
            return None
        if candle["close"] < candle["open"]:
            return "RED"
        if candle["close"] > candle["open"]:
            return "GREEN"
        return None

    @staticmethod
    def _trigger_id(symbol: str, side: str, live_bar_id: int, setup_bar_id: int) -> str:
        identity = f"{symbol}|{side}|{live_bar_id}|{setup_bar_id}"
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def observe(
        self,
        symbol: str,
        closed_candles: Sequence[Mapping[str, Any]],
        live_candle: Mapping[str, Any],
        quote: float,
        *,
        quote_fresh: bool,
        position_open: bool,
        authoritative_position_state: bool,
    ) -> dict[str, Any]:
        """Observe one snapshot; an initial snapshot only establishes a baseline."""
        with self._lock:
            from core.symbol_aliases import is_cap_symbol, is_lobster_symbol
            if not (is_lobster_symbol(symbol) or is_cap_symbol(symbol)):
                return {"status": "BLOCKED_SYMBOL"}
            requested_symbol = symbol
            symbol = self._symbol_key(symbol)
            if not closed_candles:
                return {"status": "WAIT_INVALID_MARKET_DATA"}
            latest = self._candle(closed_candles[-1])
            if latest is None or closed_candles[-1].get("is_closed") is False:
                return {"status": "WAIT_INVALID_MARKET_DATA"}
            state = self._state(symbol)
            latest_id = int(latest["timestamp"])
            live = self._candle(live_candle)
            live_id = int(live["timestamp"]) if live is not None else None
            first_observation = symbol not in self._primed_symbols
            if first_observation:
                self._primed_symbols.add(symbol)
                state.last_live_bar_id = live_id
                state.frozen_live_atr = latest["atr"]

            if not authoritative_position_state:
                state.suspended = True
                state.diagnostic = "WAIT_POSITION_STATE_UNKNOWN"
                self._set_state(symbol, state)
                self._save()
                return {"status": state.diagnostic}

            if position_open:
                state = WaitSymbolState(
                    last_closed_bar_id=latest_id,
                    position_active=True,
                    suspended=True,
                    diagnostic="WAIT_POSITION_OPEN",
                )
                self._set_state(symbol, state)
                self._save()
                return {"status": state.diagnostic}

            if state.position_active:
                state = WaitSymbolState(
                    last_closed_bar_id=latest_id,
                    diagnostic="WAIT_REBUILD_AFTER_FLAT",
                )
                self._set_state(symbol, state)
                self._save()
                return {"status": state.diagnostic}
            if state.rebuild_after_fill:
                state = WaitSymbolState(
                    last_closed_bar_id=latest_id,
                    diagnostic="WAIT_REBUILD_AFTER_FLAT",
                )
                self._set_state(symbol, state)
                self._save()
                return {"status": state.diagnostic}

            if first_observation and state.last_closed_bar_id is not None:
                state.last_closed_bar_id = latest_id
                state.last_live_bar_id = live_id
                state.restart_blocked_live_bar_id = live_id
                state.frozen_live_atr = latest["atr"]
                state.suspended = False
                state.diagnostic = "WAIT_RESTART_BASELINE"
                self._set_state(symbol, state)
                self._save()
                return {"status": state.diagnostic}

            if state.last_closed_bar_id is None:
                state.last_closed_bar_id = latest_id
                state.diagnostic = "WAIT_BASELINE_ESTABLISHED"
                self._set_state(symbol, state)
                self._save()
                return {"status": state.diagnostic}

            if latest_id < state.last_closed_bar_id:
                state.suspended = True
                state.diagnostic = "WAIT_DATA_SUSPENDED"
                self._set_state(symbol, state)
                self._save()
                return {"status": state.diagnostic}

            if latest_id > state.last_closed_bar_id:
                previous_id = state.last_closed_bar_id
                state.last_closed_bar_id = latest_id
                if latest_id - previous_id != TIMEFRAME_MS or len(closed_candles) < 2:
                    state.suspended = True
                    state.diagnostic = "WAIT_DATA_SUSPENDED"
                    self._set_state(symbol, state)
                    self._save()
                    return {"status": state.diagnostic}

                completed = self._candle(closed_candles[-1])
                preceding = self._candle(closed_candles[-2])
                if (
                    completed is None
                    or preceding is None
                    or closed_candles[-1].get("is_closed") is False
                    or closed_candles[-2].get("is_closed") is False
                    or int(preceding["timestamp"]) != previous_id
                ):
                    state.suspended = True
                    state.diagnostic = "WAIT_DATA_SUSPENDED"
                    self._set_state(symbol, state)
                    self._save()
                    return {"status": state.diagnostic}

                if state.suspended:
                    state.suspended = False
                    state.diagnostic = "WAIT_RESUMED_ON_NEW_DATA"
                    state.last_live_bar_id = live_id
                    state.frozen_live_atr = latest["atr"]
                    self._set_state(symbol, state)
                    self._save()
                    return {"status": state.diagnostic}

                color = self._small_direction(completed, preceding["atr"])
                body = abs(completed["close"] - completed["open"])
                directional_big = body >= BIG_BODY_MIN * preceding["atr"]
                missed_live_trigger = False
                transition = None
                if state.active_side is None:
                    if color == "RED":
                        state.active_side = "LONG"
                        state.setup_bar_id = latest_id
                        state.setup_atr = preceding["atr"]
                        transition = "SETUP_CREATED"
                    elif color == "GREEN":
                        state.active_side = "SHORT"
                        state.setup_bar_id = latest_id
                        state.setup_atr = preceding["atr"]
                        transition = "SETUP_CREATED"
                elif state.active_side == "LONG":
                    if color == "GREEN":
                        transition = "BRIDGE_COMPLETED"
                    elif color == "RED":
                        state.setup_bar_id = latest_id
                        state.setup_atr = preceding["atr"]
                        transition = "SETUP_REFRESHED"
                    elif (
                        directional_big
                        and completed["close"] > completed["open"]
                    ):
                        state.diagnostic = "MISSED_LIVE_TRIGGER"
                        missed_live_trigger = True
                        transition = "MISSED_LIVE_TRIGGER"
                elif state.active_side == "SHORT":
                    if color == "RED":
                        transition = "BRIDGE_COMPLETED"
                    elif color == "GREEN":
                        state.setup_bar_id = latest_id
                        state.setup_atr = preceding["atr"]
                        transition = "SETUP_REFRESHED"
                    elif (
                        directional_big
                        and completed["close"] < completed["open"]
                    ):
                        state.diagnostic = "MISSED_LIVE_TRIGGER"
                        missed_live_trigger = True
                        transition = "MISSED_LIVE_TRIGGER"
                if transition is not None:
                    self._append_event(
                        transition,
                        symbol,
                        f"{latest_id}:{state.active_side}:{state.setup_bar_id}",
                        {
                            "bar_id": latest_id,
                            "side": state.active_side,
                            "setup_bar_id": state.setup_bar_id,
                        },
                    )
                state.last_live_bar_id = None
                state.frozen_live_atr = None
                if missed_live_trigger:
                    self._set_state(symbol, state)
                    self._save()
                    return {"status": state.diagnostic}

            if state.suspended:
                self._set_state(symbol, state)
                self._save()
                return {"status": state.diagnostic or "WAIT_DATA_SUSPENDED"}

            if first_observation:
                state.diagnostic = "WAIT_RESTART_BASELINE"
                self._set_state(symbol, state)
                self._save()
                return {"status": state.diagnostic}

            if state.restart_blocked_live_bar_id == live_id:
                state.diagnostic = "WAIT_RESTART_BASELINE"
                self._set_state(symbol, state)
                self._save()
                return {"status": state.diagnostic}
            if (
                state.restart_blocked_live_bar_id is not None
                and live_id != state.restart_blocked_live_bar_id
            ):
                state.restart_blocked_live_bar_id = None

            if state.suspended:
                self._set_state(symbol, state)
                self._save()
                return {"status": state.diagnostic or "WAIT_DATA_SUSPENDED"}

            if state.active_side is None or state.setup_bar_id is None:
                state.diagnostic = "WAIT_NO_ACTIVE_SETUP"
                self._set_state(symbol, state)
                self._save()
                return {"status": state.diagnostic}

            if (
                live is None
                or not quote_fresh
                or live_candle.get("is_closed") is not False
                or live_id != latest_id + TIMEFRAME_MS
                or self._number(quote) is None
                or float(quote) <= 0
                or float(quote) > live["high"]
                or float(quote) < live["low"]
            ):
                state.diagnostic = "WAIT_DATA_SUSPENDED"
                state.suspended = True
                self._set_state(symbol, state)
                self._save()
                return {"status": state.diagnostic}

            if state.last_live_bar_id != live_id:
                state.last_live_bar_id = live_id
                state.frozen_live_atr = latest["atr"]
            fixed_atr = state.frozen_live_atr
            directional_body = (
                float(quote) - live["open"]
                if state.active_side == "LONG"
                else live["open"] - float(quote)
            )
            if fixed_atr is None or directional_body < BIG_BODY_MIN * fixed_atr:
                state.diagnostic = "WAIT_LIVE_TRIGGER"
                self._set_state(symbol, state)
                self._save()
                return {"status": state.diagnostic}

            trigger_id = self._trigger_id(
                symbol,
                state.active_side,
                live_id,
                state.setup_bar_id,
            )
            if any(
                item["symbol"] == symbol and item["status"] == "UNKNOWN"
                for item in self._data["claims"].values()
            ):
                state.diagnostic = "WAIT_SYMBOL_QUARANTINED"
                self._set_state(symbol, state)
                self._save()
                return {"status": state.diagnostic}
            if any(
                item["symbol"] == symbol
                and item["status"] == "CLAIMED"
                and claimed_trigger_id != trigger_id
                for claimed_trigger_id, item in self._data["claims"].items()
            ):
                state.diagnostic = "WAIT_SYMBOL_SUBMISSION_IN_FLIGHT"
                self._set_state(symbol, state)
                self._save()
                return {"status": state.diagnostic}
            claim = self._data["claims"].get(trigger_id)
            if claim is None:
                claim = {
                    "symbol": symbol,
                    "side": state.active_side,
                    "live_bar_id": live_id,
                    "setup_bar_id": state.setup_bar_id,
                    "position_uuid": self.position_uuid_for_trigger(trigger_id),
                    "status": "AVAILABLE",
                    "filled_qty": 0.0,
                    "remaining_reconciled": False,
                }
                self._data["claims"][trigger_id] = claim
                self._append_event(
                    "LIVE_TRIGGER_AVAILABLE",
                    symbol,
                    trigger_id,
                    {
                        "trigger_id": trigger_id,
                        "side": state.active_side,
                        "live_bar_id": live_id,
                        "setup_bar_id": state.setup_bar_id,
                    },
                )
            state.diagnostic = "WAIT_TRIGGER_AVAILABLE"
            self._set_state(symbol, state)
            self._save()
            if claim["status"] != "AVAILABLE":
                if claim["status"] == "CLAIMED":
                    return {
                        "status": "TRIGGER_CLAIMED",
                        "trigger_id": trigger_id,
                        "symbol": requested_symbol,
                        "side": state.active_side,
                        "live_bar_id": live_id,
                        "setup_bar_id": state.setup_bar_id,
                        "atr": fixed_atr,
                        "quote": float(quote),
                        "position_uuid": claim["position_uuid"],
                    }
                return {"status": "WAIT_TRIGGER_ALREADY_CLAIMED"}
            return {
                "status": "TRIGGER_AVAILABLE",
                "trigger_id": trigger_id,
                "symbol": requested_symbol,
                "side": state.active_side,
                "live_bar_id": live_id,
                "setup_bar_id": state.setup_bar_id,
                "atr": fixed_atr,
                "quote": float(quote),
                "position_uuid": claim["position_uuid"],
            }

    def claim(self, trigger_id: str) -> bool:
        with self._lock:
            claim = self._data["claims"].get(trigger_id)
            if not isinstance(claim, dict) or claim.get("status") != "AVAILABLE":
                return False
            if any(
                item.get("symbol") == claim.get("symbol")
                and item.get("status") in {"CLAIMED", "UNKNOWN"}
                for item in self._data["claims"].values()
            ):
                return False
            claim["status"] = "CLAIMED"
            self._append_event(
                "TRIGGER_CLAIMED",
                claim["symbol"],
                trigger_id,
                {"trigger_id": trigger_id},
            )
            self._save()
            return True

    def claim_external(
        self,
        trigger_id: str,
        *,
        symbol: str,
        side: str,
        authority: str,
        live_bar_id: int,
        setup_bar_id: int,
        client_order_id: str,
    ) -> bool:
        if authority != "KC2BAR" or side not in {"LONG", "SHORT"}:
            return False
        with self._lock:
            symbol_key = self._symbol_key(symbol)
            existing = self._data["claims"].get(trigger_id)
            if existing is not None:
                return False
            if any(
                claim.get("symbol") == symbol_key
                and claim.get("status") in {"CLAIMED", "UNKNOWN"}
                for claim in self._data["claims"].values()
            ):
                return False
            claim = {
                "symbol": symbol_key,
                "side": side,
                "authority": authority,
                "live_bar_id": int(live_bar_id),
                "setup_bar_id": int(setup_bar_id),
                "position_uuid": self.position_uuid_for_trigger(trigger_id),
                "client_order_id": client_order_id,
                "status": "CLAIMED",
                "filled_qty": 0.0,
                "remaining_reconciled": False,
            }
            self._data["claims"][trigger_id] = claim
            self._append_event(
                "KC2BAR_TRIGGER_CLAIMED",
                symbol_key,
                trigger_id,
                {
                    "trigger_id": trigger_id,
                    "side": side,
                    "live_bar_id": int(live_bar_id),
                    "setup_bar_id": int(setup_bar_id),
                },
            )
            self._save()
            return True

    def is_external_claim_valid(
        self,
        trigger_id: str,
        symbol: str,
        side: str,
        live_bar_id: int,
        setup_bar_id: int,
        client_order_id: Optional[str] = None,
    ) -> bool:
        with self._lock:
            claim = self._data["claims"].get(trigger_id)
            return bool(
                isinstance(claim, dict)
                and claim.get("authority") == "KC2BAR"
                and claim.get("symbol") == self._symbol_key(symbol)
                and claim.get("side") == side
                and claim.get("live_bar_id") == int(live_bar_id)
                and claim.get("setup_bar_id") == int(setup_bar_id)
                and claim.get("position_uuid")
                == self.position_uuid_for_trigger(trigger_id)
                and (
                    client_order_id is None
                    or claim.get("client_order_id") == client_order_id
                )
                and claim.get("status") == "CLAIMED"
            )

    def is_claim_valid(
        self, trigger_id: str, symbol: str, side: str, live_bar_id: int,
    ) -> bool:
        with self._lock:
            claim = self._data["claims"].get(trigger_id)
            symbol_key = self._symbol_key(symbol)
            state = self._state(symbol_key)
            return bool(
                isinstance(claim, dict)
                and claim.get("symbol") == symbol_key
                and claim.get("side") == side
                and claim.get("live_bar_id") == live_bar_id
                and claim.get("status") == "CLAIMED"
                and state.active_side == side
                and state.setup_bar_id == claim.get("setup_bar_id")
                and claim.get("position_uuid")
                == self.position_uuid_for_trigger(trigger_id)
            )

    def is_order_identity_bound(self, trigger_id: str, client_order_id: str) -> bool:
        with self._lock:
            claim = self._data["claims"].get(trigger_id)
            return bool(
                isinstance(claim, dict)
                and claim.get("status") == "CLAIMED"
                and claim.get("client_order_id") == client_order_id
            )

    def unknown_claims(self, symbol: str) -> list[dict[str, Any]]:
        with self._lock:
            symbol_key = self._symbol_key(symbol)
            return [
                {"trigger_id": trigger_id, **claim}
                for trigger_id, claim in self._data["claims"].items()
                if claim.get("symbol") == symbol_key
                and claim.get("status") == "UNKNOWN"
            ]

    def bind_order_identity(self, trigger_id: str, client_order_id: str) -> bool:
        with self._lock:
            claim = self._data["claims"].get(trigger_id)
            if not isinstance(claim, dict) or claim.get("status") != "CLAIMED":
                return False
            existing = claim.get("client_order_id")
            if existing is not None and existing != client_order_id:
                return False
            claim["client_order_id"] = client_order_id
            self._append_event(
                "ORDER_ID_BOUND",
                claim["symbol"],
                trigger_id,
                {
                    "trigger_id": trigger_id,
                    "client_order_id": client_order_id,
                },
            )
            self._save()
            return True

    def revalidate_claim(
        self,
        trigger: Mapping[str, Any],
        closed_candles: Sequence[Mapping[str, Any]],
        live_candle: Mapping[str, Any],
        quote: float,
        *,
        quote_fresh: bool,
        position_open: bool,
        authoritative_position_state: bool,
    ) -> bool:
        result = self.observe(
            str(trigger.get("symbol") or ""),
            closed_candles,
            live_candle,
            quote,
            quote_fresh=quote_fresh,
            position_open=position_open,
            authoritative_position_state=authoritative_position_state,
        )
        return bool(
            result.get("status") == "TRIGGER_CLAIMED"
            and result.get("trigger_id") == trigger.get("trigger_id")
            and result.get("side") == trigger.get("side")
            and result.get("live_bar_id") == trigger.get("live_bar_id")
            and self.is_claim_valid(
                str(trigger.get("trigger_id") or ""),
                str(trigger.get("symbol") or ""),
                str(trigger.get("side") or ""),
                int(trigger.get("live_bar_id") or -1),
            )
        )

    def resolve(
        self,
        trigger_id: str,
        *,
        filled_qty: float,
        position_present: bool,
        outcome: str,
        remaining_reconciled: bool = False,
    ) -> str:
        with self._lock:
            claim = self._data["claims"].get(trigger_id)
            if not isinstance(claim, dict) or claim.get("status") != "CLAIMED":
                return "WAIT_CLAIM_NOT_ACTIVE"
            quantity = self._number(filled_qty)
            if quantity is None or quantity < 0:
                claim["status"] = "UNKNOWN"
            elif quantity > 0 and position_present:
                claim["filled_qty"] = quantity
                claim["status"] = (
                    "CONSUMED" if remaining_reconciled else "UNKNOWN"
                )
                claim["remaining_reconciled"] = bool(remaining_reconciled)
                state = self._state(claim["symbol"])
                state.active_side = None
                state.setup_bar_id = None
                state.setup_atr = None
                state.position_active = True
                state.rebuild_after_fill = True
                state.diagnostic = "WAIT_CONSUMED_BY_POSITION"
                self._set_state(claim["symbol"], state)
                self._append_event(
                    "ENTRY_AUTHORITY_CONSUMED",
                    claim["symbol"],
                    trigger_id,
                    {
                        "trigger_id": trigger_id,
                        "authority": claim.get("authority", "WAIT"),
                        "filled_qty": quantity,
                        "position_uuid": claim.get("position_uuid"),
                    },
                )
            elif quantity == 0 and outcome == "FAILED" and not position_present:
                claim["status"] = "FAILED"
            else:
                claim["status"] = "UNKNOWN"
            self._append_event(
                f"ENTRY_CLAIM_{claim['status']}",
                claim["symbol"],
                trigger_id,
                {
                    "trigger_id": trigger_id,
                    "authority": claim.get("authority", "WAIT"),
                    "filled_qty": claim.get("filled_qty", 0.0),
                },
            )
            self._save()
            return claim["status"]

    def reconcile(
        self,
        trigger_id: str,
        *,
        filled_qty: float,
        position_present: bool,
        order_reconciled: bool,
    ) -> str:
        with self._lock:
            claim = self._data["claims"].get(trigger_id)
            if not isinstance(claim, dict) or claim.get("status") != "UNKNOWN":
                return "WAIT_CLAIM_NOT_UNKNOWN"
            quantity = self._number(filled_qty)
            if quantity is None or quantity < 0 or not order_reconciled:
                return "WAIT_RECONCILIATION_REQUIRED"
            if quantity > 0 and position_present:
                claim["filled_qty"] = quantity
                claim["status"] = "CONSUMED"
                claim["remaining_reconciled"] = True
                state = self._state(claim["symbol"])
                state.active_side = None
                state.setup_bar_id = None
                state.setup_atr = None
                state.position_active = True
                state.rebuild_after_fill = True
                state.diagnostic = "WAIT_CONSUMED_BY_RECONCILIATION"
                self._set_state(claim["symbol"], state)
                self._append_event(
                    "ENTRY_AUTHORITY_CONSUMED",
                    claim["symbol"],
                    trigger_id,
                    {
                        "trigger_id": trigger_id,
                        "authority": claim.get("authority", "WAIT"),
                        "filled_qty": quantity,
                        "position_uuid": claim.get("position_uuid"),
                    },
                )
            elif quantity == 0 and not position_present:
                claim["status"] = "FAILED"
                claim["remaining_reconciled"] = True
            else:
                return "WAIT_RECONCILIATION_REQUIRED"
            self._append_event(
                f"ENTRY_CLAIM_{claim['status']}",
                claim["symbol"],
                trigger_id,
                {
                    "trigger_id": trigger_id,
                    "authority": claim.get("authority", "WAIT"),
                    "filled_qty": claim.get("filled_qty", 0.0),
                },
            )
            self._save()
            return claim["status"]

    @staticmethod
    def arbitrate(
        authorities: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Fail closed on opposite sides; same-side authority may submit once."""
        valid = [
            dict(item) for item in authorities
            if item.get("side") in {"LONG", "SHORT"}
        ]
        sides = {item["side"] for item in valid}
        if len(sides) > 1:
            return {"status": "BLOCKED_OPPOSITE_AUTHORITIES"}
        if not valid:
            return {"status": "NO_AUTHORITY"}
        # KC2BAR remains the existing producer when both authorities agree.
        priority = {"KC2BAR": 0, "WAIT": 1, "WILD_SWING": 2}
        valid.sort(key=lambda item: (
            priority.get(str(item.get("authority") or ""), 3),
            str(item.get("authority") or ""),
        ))
        return {"status": "ONE_ORDER", "winner": valid[0]}
