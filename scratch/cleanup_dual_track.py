import re

with open("core/services/exits/dual_track_exit_service.py", "r") as f:
    content = f.read()

# 1. Replace evaluate_exit
new_evaluate = """    def evaluate_exit(self, position: Dict[str, Any], frame: pd.DataFrame, price: float, velocity_drop_ratio: float = 0.0, **kwargs) -> Optional[str]:
        if frame is None or len(frame) < 3:
            return None
            
        side = position.get("side")
        if not side:
            return None

        # 1. 帳戶硬止損 (極端防禦)
        hard_stop_reason = check_hard_stop_exit(position, frame, price)
        if hard_stop_reason:
            return hard_stop_reason

        # 2. 純機械式：動態防禦 (0.5 ATR) + 階梯限價鎖利 (1.0 ATR)
        ladder_reason = check_atr_step_trailing_stop(
            position, frame, price
        )
        if ladder_reason:
            return ladder_reason

        return None"""

content = re.sub(r'    def evaluate_exit\(.*?return None', new_evaluate, content, flags=re.DOTALL, count=1)

# 2. Remove is_momentum_strong
content = re.sub(r'def is_momentum_strong.*?return True\n\n', '', content, flags=re.DOTALL)

# 3. Remove check_emergency_exit, check_peak_exhaustion_exit, check_swing_trailing_exit
content = re.sub(r'def check_emergency_exit.*?return None\n\n', '', content, flags=re.DOTALL)
content = re.sub(r'def check_peak_exhaustion_exit.*?return None\n\n', '', content, flags=re.DOTALL)
content = re.sub(r'def check_swing_trailing_exit.*?return None\n\n', '', content, flags=re.DOTALL)

with open("core/services/exits/dual_track_exit_service.py", "w") as f:
    f.write(content)
print("Done")
