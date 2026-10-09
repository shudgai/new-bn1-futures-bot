import re
with open('scratch/replay_parabolic.py', 'r') as f:
    code = f.read()

# Remove the patch context manager
code = code.replace("with patch('core.services.exits.peak_trailing_exit.evaluate_trend_hold', return_value=('RELEASE', 'Mock')):", "")
code = code.replace("            res = evaluate_peak_trailing(pos, p, snapshot, atr=atr)", "        res = evaluate_peak_trailing(pos, p, snapshot, atr=atr)")
code = code.replace("            if res and res.get('trigger') == 'EXIT_PARABOLIC_PULLBACK_1_ATR':", "        if res and res.get('trigger') == 'EXIT_PARABOLIC_PULLBACK_1_ATR':")
code = code.replace("                pre_reason = res.get('reason')", "            pre_reason = res.get('reason')")
code = code.replace("                pre_trigger = res.get('trigger')", "            pre_trigger = res.get('trigger')")

with open('scratch/replay_parabolic.py', 'w') as f:
    f.write(code)
