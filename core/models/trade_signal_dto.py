"""Trade Signal Data Transfer Object (DTO) for Entry Decision Pipelines."""
from typing import Dict, Any, Optional
from dataclasses import dataclass, field

@dataclass
class TradeSignalDTO:
    action: str  # ENTER, WAIT, EXIT
    side: Optional[str] = None
    reason: str = "WAIT"
    entry_mode: str = "CHANNEL_SWING"
    profit_reentry_token: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_entry(self) -> bool:
        return self.action == "ENTER" and self.side in ("LONG", "SHORT")

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "action": self.action,
            "side": self.side,
            "reason": self.reason,
            "entry_mode": self.entry_mode,
        }
        if self.profit_reentry_token:
            result["profit_reentry_token"] = self.profit_reentry_token
        result.update(self.extra)
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TradeSignalDTO":
        if not data:
            return cls(action="WAIT", reason="NO_DATA")
        action = str(data.get("action") or "WAIT")
        side = data.get("side")
        if side is not None:
            side = str(side).upper()
        reason = str(data.get("reason") or "WAIT")
        entry_mode = str(data.get("entry_mode") or "CHANNEL_SWING")
        profit_reentry_token = data.get("profit_reentry_token")

        known_keys = {"action", "side", "reason", "entry_mode", "profit_reentry_token"}
        extra = {k: v for k, v in data.items() if k not in known_keys}

        return cls(
            action=action, side=side, reason=reason,
            entry_mode=entry_mode, profit_reentry_token=profit_reentry_token,
            extra=extra
        )
