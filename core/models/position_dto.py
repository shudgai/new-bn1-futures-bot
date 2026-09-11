"""Position Data Transfer Object (DTO) for Type-Safe Trade & Risk Management."""
from typing import Dict, Any, Optional
from dataclasses import dataclass, field

@dataclass
class PositionDTO:
    symbol: str
    side: str
    entry_price: float
    qty: float
    leverage: float = 1.0
    open_timestamp: float = 0.0
    entry_mode: str = "CHANNEL_SWING"
    margin: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": self.entry_price,
            "qty": self.qty,
            "leverage": self.leverage,
            "open_timestamp": self.open_timestamp,
            "entry_mode": self.entry_mode,
            "margin": self.margin,
            **self.extra,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PositionDTO":
        if not data:
            return cls(symbol="", side="LONG", entry_price=0.0, qty=0.0)
        symbol = str(data.get("symbol") or "")
        side = str(data.get("side") or "LONG").upper()
        entry_price = float(data.get("entry_price") or 0.0)
        qty = float(data.get("qty") or 0.0)
        leverage = float(data.get("leverage") or 1.0)
        open_timestamp = float(data.get("open_timestamp") or 0.0)
        entry_mode = str(data.get("entry_mode") or "CHANNEL_SWING")
        margin = float(data.get("margin") or 0.0)
        
        known_keys = {"symbol", "side", "entry_price", "qty", "leverage", "open_timestamp", "entry_mode", "margin"}
        extra = {k: v for k, v in data.items() if k not in known_keys}
        
        return cls(
            symbol=symbol, side=side, entry_price=entry_price, qty=qty,
            leverage=leverage, open_timestamp=open_timestamp,
            entry_mode=entry_mode, margin=margin, extra=extra
        )
