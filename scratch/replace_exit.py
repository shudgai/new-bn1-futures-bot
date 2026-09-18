import re

with open("core/services/exits/dual_track_exit_service.py", "r") as f:
    content = f.read()

content = content.replace(
    "def check_dynamic_trailing_exit(position: Dict[str, Any], frame: pd.DataFrame, price: float, fee: float = 0.0005, slippage: float = 0.0005, velocity_slowdown: bool = False) -> Optional[str]:",
    "def check_dynamic_trailing_exit(position: Dict[str, Any], frame: pd.DataFrame, price: float, fee: float = 0.0005, slippage: float = 0.0005, velocity_drop_ratio: float = 0.0) -> Optional[str]:"
)

content = content.replace(
    "is_velocity_peak = velocity_slowdown",
    "is_velocity_peak = (velocity_drop_ratio >= 0.20)"
)

content = content.replace(
    "velocity_slowdown = kwargs.get(\"velocity_slowdown\", False)",
    "velocity_drop_ratio = kwargs.get(\"velocity_drop_ratio\", 0.0)"
)

content = content.replace(
    "dynamic_reason = check_dynamic_trailing_exit(position, frame, price, self.fee, self.slippage, velocity_slowdown)",
    "dynamic_reason = check_dynamic_trailing_exit(position, frame, price, self.fee, self.slippage, velocity_drop_ratio)"
)

with open("core/services/exits/dual_track_exit_service.py", "w") as f:
    f.write(content)
