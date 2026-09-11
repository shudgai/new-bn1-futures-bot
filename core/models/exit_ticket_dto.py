"""Exit Ticket Data Transfer Object (DTO) for Reentry & Peak Exit Tracking."""
from typing import Dict, Any, Optional
from dataclasses import dataclass, field

@dataclass
class ExitTicketDTO:
    symbol: str
    side: str
    token: str
    phase: str = "closed"
    mode: str = "outer_cycle"
    requires_pullback: bool = False
    exit_bar_id: float = 0.0
    close_reason: str = ""
    close_requested_at_ms: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "token": self.token,
            "phase": self.phase,
            "mode": self.mode,
            "requires_pullback": self.requires_pullback,
            "exit_bar_id": self.exit_bar_id,
            "close_reason": self.close_reason,
            "close_requested_at_ms": self.close_requested_at_ms,
            **self.extra,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExitTicketDTO":
        if not data:
            return cls(symbol="", side="LONG", token="")
        symbol = str(data.get("symbol") or "")
        side = str(data.get("side") or "LONG").upper()
        token = str(data.get("token") or "")
        phase = str(data.get("phase") or "closed")
        mode = str(data.get("mode") or "outer_cycle")
        requires_pullback = bool(data.get("requires_pullback", False))
        exit_bar_id = float(data.get("exit_bar_id") or 0.0)
        close_reason = str(data.get("close_reason") or "")
        close_requested_at_ms = float(data.get("close_requested_at_ms") or 0.0)

        known_keys = {
            "symbol", "side", "token", "phase", "mode", "requires_pullback",
            "exit_bar_id", "close_reason", "close_requested_at_ms"
        }
        extra = {k: v for k, v in data.items() if k not in known_keys}

        return cls(
            symbol=symbol, side=side, token=token, phase=phase, mode=mode,
            requires_pullback=requires_pullback, exit_bar_id=exit_bar_id,
            close_reason=close_reason, close_requested_at_ms=close_requested_at_ms,
            extra=extra
        )
