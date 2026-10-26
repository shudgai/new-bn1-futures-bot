import pytest
from core.config import DEFAULT_SYMBOLS, ENTRY_DISABLED_SYMBOLS, HIGH_BETA_CONFIG

@pytest.mark.parametrize("symbol", ["龙虾/USDT"])
def test_entry_parity_same_gate_and_logic(symbol):
    assert symbol not in ENTRY_DISABLED_SYMBOLS
    assert symbol in DEFAULT_SYMBOLS
    config = HIGH_BETA_CONFIG.get(symbol, HIGH_BETA_CONFIG.get("DEFAULT", {}))
    assert config == HIGH_BETA_CONFIG.get("DEFAULT", {}), f"{symbol} should use DEFAULT HIGH_BETA_CONFIG"

@pytest.mark.parametrize("symbol", ["龙虾/USDT", "1000PEPE/USDT"])
def test_exit_parity_waterfall(symbol):
    pass
