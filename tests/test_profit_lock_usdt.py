import pytest

import core.paper_account as pa_module
from core.paper_account import PaperAccount, get_profit_lock_giveback_usdt

def test_profit_lock_uses_fixed_point_eight_usdt_giveback():
    for peak_usdt in (0.0, 1.0, 5.0, 100.0):
        assert get_profit_lock_giveback_usdt(peak_usdt) == pytest.approx(0.8)


@pytest.mark.anyio
async def test_fee_floor_then_one_usdt_ladder(tmp_path, monkeypatch):
    monkeypatch.setattr(pa_module, "STATE_FILE", str(tmp_path / "profit_lock_account.json"))
    monkeypatch.setattr(pa_module, "ENABLE_PROFIT_LOCK_USDT", True)
    monkeypatch.setattr(pa_module, "DISABLE_STOP_LOSS", True)
    monkeypatch.setattr(pa_module, "ENABLE_PROFIT_BANK", True)
    monkeypatch.setattr(pa_module, "ENABLE_FIXED_PROFIT_LOCK_PCT", True)
    monkeypatch.setattr(pa_module, "ENABLE_EARLY_PROFIT_GUARD", True)
    monkeypatch.setattr(pa_module, "ENABLE_TRAILING_STOP", True)
    monkeypatch.setattr(pa_module, "ENABLE_PROFIT_GIVEBACK_EXIT", True)

    account = PaperAccount()
    account.balance = 150.0
    assert await account.open_position(
        "BTC/USDT", "LONG", 100.0, 150.0, 98.0, 0.0, "MA alignment",
        atr=1.0, leverage=5, signal_score=100, apply_slippage=False,
        entry_context={"entry_mode": "MA_ALIGNMENT"},
    )

    # Notional=750U, round-trip fee=0.75U, first protected floor=1.50U.
    # Activation adds the 0.8U giveback, so the first threshold is 2.30U.
    await account.update_positions({"BTC/USDT": 100.30})
    assert account.positions["BTC/USDT"]["sl"] == pytest.approx(0.0)

    await account.update_positions({"BTC/USDT": 100.3066666667})
    assert account.positions["BTC/USDT"]["sl"] == pytest.approx(100.20)

    # Below the next full 1U step, the protection line stays fixed.
    await account.update_positions({"BTC/USDT": 100.43})
    assert account.positions["BTC/USDT"]["sl"] == pytest.approx(100.20)

    # At 3.30U peak, advance exactly one 1U step: 1.50U -> 2.50U locked.
    await account.update_positions({"BTC/USDT": 100.44})
    assert account.positions["BTC/USDT"]["sl"] == pytest.approx(100.3333333333)
    assert account.positions["BTC/USDT"]["profit_lock_mode"] == "1U_LADDER_0.8U_TRAIL"
    assert not account.position_meta["BTC/USDT"].get("fixed_profit_lock_pct_armed")
    assert not account.position_meta["BTC/USDT"].get("early_profit_guard_armed")

    await account.update_positions({"BTC/USDT": 100.34})
    assert "BTC/USDT" in account.positions
    await account.update_positions({"BTC/USDT": 100.32})
    assert "BTC/USDT" not in account.positions


@pytest.mark.anyio
async def test_wide_trail_mode_does_not_exit_at_fixed_take_profit(tmp_path, monkeypatch):
    monkeypatch.setattr(
        pa_module, "STATE_FILE", str(tmp_path / "trend_end_account.json")
    )
    monkeypatch.setattr(pa_module, "ENABLE_PROFIT_LOCK_USDT", True)
    account = PaperAccount()
    account.balance = 150.0
    assert await account.open_position(
        "BTC/USDT", "LONG", 100.0, 150.0, 98.0, 104.0,
        "trend-end mode", atr=1.0, leverage=5, signal_score=100,
        apply_slippage=False,
    )

    await account.update_positions({"BTC/USDT": 104.2})

    assert "BTC/USDT" in account.positions
