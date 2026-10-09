import pandas as pd

closes = [
    0.04882, # 47:00
    0.04905, # 48:00
    0.04920, # 49:00 (Entry at 50:08 so this is the bar before entry)
    0.04932, # 50:00 (Bar closing at 51:00)
    0.04949, # 51:00
    0.04953, # 52:00
    0.04969, # 53:00
    0.04981, # 54:00
    0.04963, # 55:00
    0.04896, # 56:00
]

for i in range(len(closes)):
    ma3 = sum(closes[max(0, i-2):i+1]) / min(3, i+1)
    ma5 = sum(closes[max(0, i-4):i+1]) / min(5, i+1)
    print(f"Bar {i} C:{closes[i]:.5f} MA3:{ma3:.5f} MA5:{ma5:.5f}")
