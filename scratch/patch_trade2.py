import re
with open('scratch/replay_parabolic.py', 'r') as f:
    code = f.read()

code = code.replace("real_evaluate_trend_hold = pte.evaluate_trend_hold", "from core.services.exits.trend_hold_evaluator import evaluate_trend_hold\nreal_evaluate_trend_hold = evaluate_trend_hold")

with open('scratch/replay_parabolic.py', 'w') as f:
    f.write(code)
