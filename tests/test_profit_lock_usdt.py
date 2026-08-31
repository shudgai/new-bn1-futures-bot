import pytest

import core.paper_account as pa_module
from core.paper_account import (
    PaperAccount,
    get_outer_run_net_giveback_usdt,
    get_profit_lock_giveback_usdt,
)

def test_profit_lock_uses_configured_one_usdt_gap():
    for peak_usdt in (0.0, 1.0, 5.0, 100.0):
        assert get_profit_lock_giveback_usdt(peak_usdt) == pytest.approx(1.0)


def test_outer_run_net_giveback_is_fixed_for_every_position_size():
    assert get_outer_run_net_giveback_usdt(75.0) == pytest.approx(1.0)
    assert get_outer_run_net_giveback_usdt(150.0) == pytest.approx(1.0)
    assert get_outer_run_net_giveback_usdt(300.0) == pytest.approx(1.0)


@pytest.mark.anyio
async def test_fee_floor_then_half_usdt_ladder(tmp_path, monkeypatch):
    monkeypatch.setattr(pa_module, "STATE_FILE", str(tmp_path / "profit_lock_account.json"))
    monkeypatch.setattr(pa_module, "ENABLE_PROFIT_LOCK_USDT", True)
    monkeypatch.setattr(pa_module, "DISABLE_STOP_LOSS", True)
    monkeypatch.setattr(pa_module, "ENABLE_PROFIT_BANK", True)
    monkeypatch.setattr(pa_module, "ENABLE_FIXED_PROFIT_LOCK_PCT", True)
    monkeypatch.setattr(pa_module, "ENABLE_EARLY_PROFIT_GUARD", True)
    monkeypatch.setattr(pa_module, "ENABLE_TRAILING_STOP", True)
    monkeypatch.setattr(pa_module, "ENABLE_PROFIT_GIVEBACK_EXIT", True)
    monkeypatch.setattr(pa_module, "PROFIT_LOCK_FLOOR_USDT", 0.0)
    monkeypatch.setattr(pa_module, "PROFIT_LOCK_TRIGGER_USDT", 0.0)
    monkeypatch.setattr(pa_module, "PROFIT_LOCK_FEE_MULTIPLIER", 2.0)
    monkeypatch.setattr(pa_module, "PROFIT_LOCK_GIVEBACK_USDT", 0.8)
    monkeypatch.setattr(pa_module, "PROFIT_LOCK_LADDER_STEP_USDT", 0.5)

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

    # Before the next 0.5U threshold, the first 1.50U floor stays fixed.
    await account.update_positions({"BTC/USDT": 100.372})
    assert account.positions["BTC/USDT"]["sl"] == pytest.approx(100.20)

    # At 2.80U peak, advance one 0.5U step: 1.50U -> 2.00U locked.
    await account.update_positions({"BTC/USDT": 100.3733333334})
    assert account.positions["BTC/USDT"]["sl"] == pytest.approx(100.2666666667)

    # At 3.30U peak, advance the next 0.5U step: 2.00U -> 2.50U locked.
    await account.update_positions({"BTC/USDT": 100.44})
    assert account.positions["BTC/USDT"]["sl"] == pytest.approx(100.3333333333)
    assert account.positions["BTC/USDT"]["profit_lock_mode"] == "0.5U_LADDER_0.8U_GAP"
    assert not account.position_meta["BTC/USDT"].get("fixed_profit_lock_pct_armed")
    assert not account.position_meta["BTC/USDT"].get("early_profit_guard_armed")

    await account.update_positions({"BTC/USDT": 100.34})
    assert "BTC/USDT" in account.positions
    await account.update_positions({"BTC/USDT": 100.32})
    assert "BTC/USDT" not in account.positions


@pytest.mark.anyio
async def test_outer_run_ignores_one_usdt_giveback_even_after_pivot(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(pa_module, "STATE_FILE", str(tmp_path / "outer_run_giveback.json"))
    monkeypatch.setattr(pa_module, "ENABLE_PROFIT_LOCK_USDT", True)
    monkeypatch.setattr(pa_module, "ENABLE_FIXED_PROFIT_LOCK_PCT", False)
    account = PaperAccount()
    assert await account.open_position(
        "BTC/USDT", "LONG", 100.0, 300.0, 95.0, 0.0, "OUTER_RUN",
        leverage=1, signal_score=100, apply_slippage=False,
        entry_context={"entry_mode": "MA3_MA15_MARKET", "wave_regime": "TREND"},
    )
    account.positions["BTC/USDT"]["outer_run_active"] = True
    account.position_meta["BTC/USDT"]["outer_run_active"] = True

    await account.update_positions({"BTC/USDT": 102.0})
    assert "BTC/USDT" in account.positions

    await account.update_positions({"BTC/USDT": 101.65})
    # 峰頂尚未出現：即使已從最高淨利回吐超過1U，OUTER_RUN仍不停利。
    assert "BTC/USDT" in account.positions

    # 即使殘留舊版峰頂保護旗標，連續波段也必須繼續持有。
    account.positions["BTC/USDT"]["outer_run_pivot_protect_armed"] = True
    account.position_meta["BTC/USDT"]["outer_run_pivot_protect_armed"] = True
    await account.update_positions({"BTC/USDT": 101.65})
    assert "BTC/USDT" in account.positions
    assert not any(
        str(trade.get("action") or "").startswith("CLOSE")
        for trade in account.trades
    )


@pytest.mark.anyio
async def test_kc_structure_ignores_pivot_and_one_usdt_giveback(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(pa_module, "STATE_FILE", str(tmp_path / "kc_pivot_giveback.json"))
    monkeypatch.setattr(pa_module, "ENABLE_PROFIT_LOCK_USDT", True)
    monkeypatch.setattr(pa_module, "ENABLE_FIXED_PROFIT_LOCK_PCT", False)
    account = PaperAccount()
    assert await account.open_position(
        "BTC/USDT", "LONG", 100.0, 150.0, 95.0, 0.0, "KC trend",
        leverage=1, signal_score=100, apply_slippage=False,
        entry_context={"entry_mode": "MA3_MA15_MARKET", "wave_regime": "TREND"},
    )

    await account.update_positions({"BTC/USDT": 102.0})
    assert account.positions["BTC/USDT"]["sl"] == pytest.approx(95.0)
    await account.update_positions({"BTC/USDT": 101.0})
    assert "BTC/USDT" in account.positions
    assert not account.position_meta["BTC/USDT"].get("profit_lock_usdt_armed")

    account.positions["BTC/USDT"]["kc_pivot_protect_armed"] = True
    account.position_meta["BTC/USDT"]["kc_pivot_protect_armed"] = True
    await account.update_positions({"BTC/USDT": 101.0})
    assert "BTC/USDT" in account.positions
    assert not any(
        str(trade.get("action") or "").startswith("CLOSE")
        for trade in account.trades
    )


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


@pytest.mark.anyio
async def test_one_usdt_floor_advances_every_two_usdt(tmp_path, monkeypatch):
    monkeypatch.setattr(pa_module, "STATE_FILE", str(tmp_path / "one_by_two_lock.json"))
    monkeypatch.setattr(pa_module, "ENABLE_PROFIT_LOCK_USDT", True)
    monkeypatch.setattr(pa_module, "ENABLE_FIXED_PROFIT_LOCK_PCT", False)
    monkeypatch.setattr(pa_module, "ENABLE_PROFIT_BANK", False)
    monkeypatch.setattr(pa_module, "ENABLE_TRAILING_STOP", False)
    monkeypatch.setattr(pa_module, "ENABLE_PROFIT_GIVEBACK_EXIT", False)
    monkeypatch.setattr(pa_module, "PROFIT_LOCK_TRIGGER_USDT", 1.0)
    monkeypatch.setattr(pa_module, "PROFIT_LOCK_FLOOR_USDT", 1.0)
    monkeypatch.setattr(pa_module, "PROFIT_LOCK_FEE_MULTIPLIER", 1.0)
    monkeypatch.setattr(pa_module, "PROFIT_LOCK_GIVEBACK_USDT", 2.0)
    monkeypatch.setattr(pa_module, "PROFIT_LOCK_LADDER_STEP_USDT", 2.0)
    monkeypatch.setattr(pa_module, "PROFIT_LOCK_BASE_MARGIN_USDT", 50.0)

    account = PaperAccount()
    assert await account.open_position(
        "BTC/USDT", "LONG", 100.0, 50.0, 95.0, 0.0, "1U/2U ladder",
        leverage=1, signal_score=100, apply_slippage=False,
        entry_context={"entry_mode": "MA_ALIGNMENT"},
    )

    await account.update_positions({"BTC/USDT": 105.98})  # 2.99U
    assert account.positions["BTC/USDT"]["sl"] == pytest.approx(95.0)

    await account.update_positions({"BTC/USDT": 106.0})   # 3U peak -> lock 1U
    assert account.positions["BTC/USDT"]["sl"] == pytest.approx(102.0)

    await account.update_positions({"BTC/USDT": 110.0})   # 5U peak -> lock 3U
    assert account.positions["BTC/USDT"]["sl"] == pytest.approx(106.0)
    assert account.positions["BTC/USDT"]["profit_lock_mode"] == "2U_LADDER_2U_GAP"
