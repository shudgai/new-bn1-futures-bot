import re
with open('scratch/replay_parabolic.py', 'r') as f:
    code = f.read()
code = code.replace("trade['entry_price']", "trade['price']")
code = code.replace("trade['opened_at']", "trade['id']")
code = code.replace("trade['closed_at']", "next((t['id'] for t in state.get('trades', []) if t.get('pair_id') == trade['id'] and t.get('action') == 'CLOSE_SHORT'), 'NOT_FOUND')")
code = code.replace("trade['exit_price']", "next((t['price'] for t in state.get('trades', []) if t.get('pair_id') == trade['id'] and t.get('action') == 'CLOSE_SHORT'), 'NOT_FOUND')")
code = code.replace("trade['net_pnl']", "next((t['pnl'] for t in state.get('trades', []) if t.get('pair_id') == trade['id'] and t.get('action') == 'CLOSE_SHORT'), 'NOT_FOUND')")
with open('scratch/replay_parabolic.py', 'w') as f:
    f.write(code)
