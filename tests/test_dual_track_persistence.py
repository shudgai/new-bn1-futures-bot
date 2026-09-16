"""Exit observations survive account synchronization and process restarts."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

import core.paper_account as paper_module
import core.testnet_account as testnet_module
from core.services.symbol_runner import process_single_symbol_runner
from test_close_deduplication import account_for


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("mode", ["paper", "testnet"])
async def test_runner_preserves_exit_observations_across_reload(side, mode, tmp_path, monkeypatch):
    # Arrange an isolated account and a confirmed swing with favorable profit.
    symbol = "DOGE/USDT"
    if mode == "testnet":
        account, exchange = await account_for(side, tmp_path, monkeypatch)
    else:
        monkeypatch.setattr(paper_module, "STATE_FILE", str(tmp_path / "paper.json"))
        account = paper_module.PaperAccount()
        assert await account.open_position(
            symbol, side, 100., 25., 0., 0., "review", leverage=1,
            apply_slippage=False, entry_context={"entry_mode": "CHANNEL_SWING"},
        )
    sign = 1 if side == "LONG" else -1
    frame = pd.DataFrame({
        "timestamp": [60000, 120000, 180000, 240000],
        "open": [100.] * 4, "close": [100.] * 4, "atr": [1.] * 4,
        "high": [101., 102., 101., 101.], "low": [99., 98., 99., 99.],
        "kc_middle": [100., 100., 100. + sign, 100. + sign],
    })
    engine = SimpleNamespace(account=account, tickers={}, _take_over_manual_position=Mock())

    # Act through the real runner; no exchange order should occur on this quote.
    await process_single_symbol_runner(
        engine, symbol, 0, None, False, exit_frame=frame, exit_quote=100. + sign,
    )
    swing_key = "last_valid_swing_low" if side == "LONG" else "last_valid_swing_high"
    expected_swing = 98. if side == "LONG" else 102.
    expected_peak = account.positions[symbol]["ratchet_lock_state"]["max_net_atr"]
    assert expected_peak > .35
    assert account.positions[symbol][swing_key] == expected_swing
    if mode == "testnet":
        await account.refresh(force=True)
        assert account.positions[symbol].get("ratchet_lock_state", {}).get("max_net_atr") == expected_peak
    # A newer peak after synchronization must also be written to disk.
    await process_single_symbol_runner(
        engine, symbol, 0, None, False, exit_frame=frame, exit_quote=100. + sign * 1.5,
    )
    newer_peak = account.positions[symbol]["ratchet_lock_state"]["max_net_atr"]
    assert newer_peak > expected_peak
    expected_peak = newer_peak
    if mode == "testnet":
        restored = testnet_module.BinanceTestnetAccount(exchange)
        await restored.initialize()
    else:
        restored = paper_module.PaperAccount()

    # Assert the same protection remains, and the subsequent retracement closes.
    assert restored.positions[symbol].get("ratchet_lock_state", {}).get("max_net_atr") == expected_peak
    assert restored.positions[symbol].get(swing_key) == expected_swing
    engine.account = restored
    restored.close_position = AsyncMock(return_value=False)
    await process_single_symbol_runner(
        engine, symbol, 0, None, False, exit_frame=frame, exit_quote=100. + sign * .4,
    )
    restored.close_position.assert_awaited_once()
    assert "RATCHET_PROFIT_LOCK_EXIT" in restored.close_position.call_args.args[2]
