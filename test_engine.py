import json, urllib.request

def check():
    data = json.loads(urllib.request.urlopen('http://127.0.0.1:8006/api/status').read().decode('utf-8'))
    print("max_slots:", data.get("max_slots"))
    print("effective_slots:", data.get("effective_slots"))
    print("balance:", data.get("balance"))
    print("available_balance:", data.get("available_balance"))
    print("trade_amount:", data.get("trade_amount"))

check()
