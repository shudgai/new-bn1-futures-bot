"""Order-level mutual exclusion and duplicate execution safeguards."""
from __future__ import annotations

from typing import Callable, Optional


class AntiDuplicateExecutionMixin:
    """Keep local order execution mutually exclusive and idempotent per bar."""

    def _init_execution_guard(
        self,
        order_executor: Optional[Callable[[str, str], bool]] = None,
    ) -> None:
        self.last_executed_bar_time = None
        self.is_ordering = False
        self.order_executor = order_executor

    def execute_order(
        self,
        action_type: str,
        direction: str,
        *,
        bar_time=None,
    ) -> bool:
        """Execute one OPEN/CLOSE after lock, position, and bar checks pass."""
        action_type = str(action_type or "").upper()
        direction = str(direction or "").upper()
        if action_type not in {"OPEN", "CLOSE"} or direction not in {"LONG", "SHORT"}:
            return False
        if self.is_ordering:
            return False
        if bar_time is not None and bar_time == self.last_executed_bar_time:
            return False
        if action_type == "OPEN" and self.position is not None:
            return False
        if action_type == "CLOSE" and self.position is None:
            return False

        self.is_ordering = True
        try:
            if self.order_executor is not None and not bool(self.order_executor(action_type, direction)):
                return False
            self.position = direction if action_type == "OPEN" else None
            self.last_executed_bar_time = bar_time
            clear_state = getattr(self, "_clear_trigger_state", None)
            if clear_state is not None:
                clear_state()
            if action_type == "CLOSE":
                self.pending_signal = direction
            else:
                self.pending_signal = None
            return True
        except Exception:
            return False
        finally:
            self.is_ordering = False


__all__ = ["AntiDuplicateExecutionMixin"]
