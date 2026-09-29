import json
import pandas as pd
from core.services.candle_data import generate_1m_dataset

with open("data/paper_account.json", "r") as f:
    pass # we can't easily get live klines from a saved JSON, but maybe there's a local database?
