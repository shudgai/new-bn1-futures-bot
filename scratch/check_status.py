import requests
try:
    r = requests.get("http://127.0.0.1:8006/api/status")
    data = r.json()
    print("HTTP_STATUS =", r.status_code)
    print("ACTIVE =", True)
    print("SYMBOLS =", data.get("symbols", []))
    print("POSITIONS =", data.get("positions", {}))
except Exception as e:
    print("Error:", e)
