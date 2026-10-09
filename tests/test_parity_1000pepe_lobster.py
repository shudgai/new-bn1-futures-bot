import pytest
from core.config import ENTRY_DISABLED_SYMBOLS, HIGH_BETA_CONFIG

@pytest.mark.parametrize("symbol", ["龙虾/USDT", "1000PEPE/USDT"])
def test_entry_parity_same_gate_and_logic(symbol):
    if symbol == "1000PEPE/USDT":
        assert symbol in ENTRY_DISABLED_SYMBOLS
    else:
        assert symbol not in ENTRY_DISABLED_SYMBOLS
        config = HIGH_BETA_CONFIG.get(symbol, HIGH_BETA_CONFIG.get("DEFAULT", {}))
        assert config == HIGH_BETA_CONFIG.get("DEFAULT", {}), f"{symbol} should use DEFAULT HIGH_BETA_CONFIG"

@pytest.mark.parametrize("symbol", ["龙虾/USDT", "1000PEPE/USDT"])
def test_exit_parity_waterfall(symbol):
    pass
