import sys
import os
sys.path.append(os.getcwd())
import pytest

from core.services.strategies.outer_strategy import aligned_entry
from tests.test_channel_aligned_entry import aligned_frame

import core.config
print("Before:", core.config.ENV_MIN_KC_BANDWIDTH)
core.config.ENV_MIN_KC_BANDWIDTH = 0.0
print("After:", core.config.ENV_MIN_KC_BANDWIDTH)

f = aligned_frame("LONG", "breakout")
price = float(f.iloc[-1]["close"])
res = aligned_entry(f, price)
print("Result:", res)
