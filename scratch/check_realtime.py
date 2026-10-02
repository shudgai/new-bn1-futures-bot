import sys
with open('core/services/exits/realtime_profit_exit.py') as f:
    for i, line in enumerate(f):
        if 'def cached_tick_indicators' in line:
            for j in range(20):
                print(next(f).rstrip())
            break
