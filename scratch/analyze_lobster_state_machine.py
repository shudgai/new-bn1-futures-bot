from core.services.kc_pending_entry import evaluate_kc_pending_entry
import json
with open("data/paper_account.json") as f:
    data = json.load(f)

# we can just print the exact rules and see
