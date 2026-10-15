import pytest
import pandas as pd
from core.engine import TradingEngine

@pytest.fixture
def engine():
    return object.__new__(TradingEngine)




