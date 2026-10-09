import json

with open('audit_candidate_a.py', 'r') as f:
    content = f.read()

# Add a loop to print the 21 trades
code = content.replace("print(\"\\nProtective-lost trades:\")", """print(\"\\nALL 21 TRADES CURRENT vs CANDIDATE:\")
    for r in results:
        print(f"{r['ot']['symbol']} {r['ot']['side']} {r['ot']['time'][:19]} | Orig PnL: {r['original_pnl']:.4f} | Cand Action: HOLD | Final Reason: {r['reason']} | Cand PnL: {r['pnl']:.4f} | Delta PnL: {r['pnl'] - r['original_pnl']:.4f} | MA15: {r['ma15_aligned']} PRC: {r['price_aligned']}")

    print(\"\\nProtective-lost trades:\")""")

with open('audit_candidate_a.py', 'w') as f:
    f.write(code)
