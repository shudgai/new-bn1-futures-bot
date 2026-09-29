import requests
import pandas as pd

url = "https://fapi.binance.com/fapi/v1/klines"
params = {
    "symbol": "1000LUNCUSDT", # Wait, what is 龙虾? 
    "interval": "1m",
    "limit": 10
}
# What is 龙虾? 龙虾 is usually meant for some meme coin, maybe PEPE? The logs say "龙虾/USDT". The actual symbol might be mapped. Let's check symbol mapping.
