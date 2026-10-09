with open("tests/test_fast_path_gates.py", "r") as f:
    content = f.read()

content = content.replace("eng = TradingEngine(account)", "eng = TradingEngine()\n    eng.account = account")
content = content.replace("'kc_upper': 110,", "'kc_upper': 110, 'ma3': 100, 'ma5': 100, 'ma15': 100,")
content = content.replace("'kc_upper': 110+10,", "'kc_upper': 110+10, 'ma3': 100, 'ma5': 100, 'ma15': 100,")
content = content.replace("'kc_upper': kc_upper,", "'kc_upper': kc_upper, 'ma3': 100, 'ma5': 100, 'ma15': 100,")
content = content.replace("'kc_upper': kc_lower+10,", "'kc_upper': kc_lower+10, 'ma3': 100, 'ma5': 100, 'ma15': 100,")
content = content.replace("'kc_upper': 100,", "'kc_upper': 100, 'ma3': 100, 'ma5': 100, 'ma15': 100,")

with open("tests/test_fast_path_gates.py", "w") as f:
    f.write(content)

