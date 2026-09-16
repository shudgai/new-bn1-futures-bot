"""Exit strategy implementations."""
from core.services.exits.fading_exit_service import FadingExitStrategy
from core.services.exits.hard_stop_service import HardStopExitStrategy
from core.services.exits.profit_protection_service import ProfitProtectionExitStrategy
from core.services.exits.dual_track_exit_service import DualTrackExitStrategy

__all__ = ["FadingExitStrategy", "HardStopExitStrategy", "ProfitProtectionExitStrategy", "DualTrackExitStrategy"]
