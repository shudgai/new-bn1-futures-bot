with open("core/services/exits/dual_track_exit_service.py", "r") as f:
    content = f.read()

old_line = 'DUAL_TRACK_STATE_KEYS = ["trade_phase", "v8_reason", "v10_phase_trailing", "has_warning_partial_close",'
new_line = 'DUAL_TRACK_STATE_KEYS = ["trade_phase", "v8_reason", "v10_phase_trailing", "has_warning_partial_close", "channel_profit_protection",'

if new_line not in content:
    content = content.replace(old_line, new_line)
    with open("core/services/exits/dual_track_exit_service.py", "w") as f:
        f.write(content)
