import pytest

import core.paper_account as pa_module
from core.paper_account import (
    PaperAccount,
    get_outer_run_net_giveback_usdt,
    get_profit_lock_giveback_usdt,
)


def seed_test_position(account, symbol, *, side, price, margin, leverage,
                       stop, take_profit=0.0, atr=1.0, entry_mode="",
                       wave_regime=""):
    """Seed a controlled fill so these tests isolate exit/lock behavior."""
    qty = margin * leverage / price
    fee = qty * price * pa_module.TAKER_FEE_RATE
    account.balance -= margin + fee
    position = {
        "symbol": symbol, "side": side, "entry_price": price, "qty": qty,
        "margin": margin, "leverage": leverage, "sl": stop,
        "initial_sl": stop, "tp": take_profit, "initial_tp": take_profit,
        "atr": atr, "entry_atr": atr, "open_timestamp": 1.0,
        "entry_mode": entry_mode, "wave_regime": wave_regime,
    }
    account.positions[symbol] = position
    account.position_meta[symbol] = {
        "sl": stop, "tp": take_profit, "atr": atr,
        "entry_mode": entry_mode, "wave_regime": wave_regime,
    }
    account.save_state()
    return position


def test_profit_lock_giveback_scales_with_peak_ratio():
    # Below the ratio break-even point, the fixed 1.0U floor applies.
    assert get_profit_lock_giveback_usdt(0.0) == pytest.approx(1.0)
    assert get_profit_lock_giveback_usdt(1.0) == pytest.approx(1.0)
    # Above it, PROFIT_LOCK_TRAIL_RATIO (0.20) takes over so bigger peaks
    # tolerate a proportionally wider giveback instead of a fixed 1.0U.
    assert get_profit_lock_giveback_usdt(5.0) == pytest.approx(1.0)
    assert get_profit_lock_giveback_usdt(100.0) == pytest.approx(20.0)


def test_outer_run_net_giveback_is_fixed_for_every_position_size():
    assert get_outer_run_net_giveback_usdt(75.0) == pytest.approx(1.0)
    assert get_outer_run_net_giveback_usdt(150.0) == pytest.approx(1.0)
    assert get_outer_run_net_giveback_usdt(300.0) == pytest.approx(1.0)


@pytest.mark.anyio
async def test_paper_price_update_does_not_own_profit_lock_exit(tmp_path, monkeypatch):
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
    account.balance = 1000.0  # Include funds for the entry fee as well as margin.
    seed_test_position(account, "BTC/USDT", side="LONG", price=100.0,
                       margin=150.0, leverage=5, stop=98.0,
                       entry_mode="MA_ALIGNMENT")

    # Price updates maintain valuation and peak state. The unified exit gate
    # owns lock authorization; account marking must not invent a second ladder.
    for price in (100.20, 100.2666666667, 100.32, 100.3333333334, 100.44, 100.34):
        await account.update_positions({"BTC/USDT": price})
        assert account.positions["BTC/USDT"]["sl"] == pytest.approx(98.0)
    assert account.positions["BTC/USDT"]["mark_price"] == pytest.approx(100.34)
    assert "profit_lock_mode" not in account.positions["BTC/USDT"]
    assert not any(
        str(trade.get("action") or "").startswith("CLOSE")
        for trade in account.trades
    )


@pytest.mark.anyio
async def test_outer_run_ignores_one_usdt_giveback_even_after_pivot(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(pa_module, "STATE_FILE", str(tmp_path / "outer_run_giveback.json"))
    monkeypatch.setattr(pa_module, "ENABLE_PROFIT_LOCK_USDT", True)
    monkeypatch.setattr(pa_module, "ENABLE_FIXED_PROFIT_LOCK_PCT", False)
    account = PaperAccount()
    account.balance = 1000.0  # Include funds for the entry fee as well as margin.
    seed_test_position(account, "BTC/USDT", side="LONG", price=100.0,
                       margin=300.0, leverage=1, stop=95.0,
                       entry_mode="MA3_MA15_MARKET", wave_regime="TREND")
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
    account.balance = 1000.0  # Include funds for the entry fee as well as margin.
    seed_test_position(account, "BTC/USDT", side="LONG", price=100.0,
                       margin=150.0, leverage=1, stop=95.0,
                       entry_mode="MA3_MA15_MARKET", wave_regime="TREND")

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
    account.balance = 1000.0  # Include funds for the entry fee as well as margin.
    seed_test_position(account, "BTC/USDT", side="LONG", price=100.0,
                       margin=150.0, leverage=5, stop=98.0,
                       take_profit=104.0, entry_mode="TREND_END", atr=1.0)

    await account.update_positions({"BTC/USDT": 104.2})

    assert "BTC/USDT" in account.positions


@pytest.mark.anyio
async def test_account_update_does_not_apply_legacy_usdt_ladder(tmp_path, monkeypatch):
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
    account.balance = 1000.0  # Include funds for the entry fee as well as margin.
    seed_test_position(account, "BTC/USDT", side="LONG", price=100.0,
                       margin=50.0, leverage=1, stop=95.0,
                       entry_mode="MA_ALIGNMENT")

    for price in (105.98, 106.0, 110.0):
        await account.update_positions({"BTC/USDT": price})
        assert account.positions["BTC/USDT"]["sl"] == pytest.approx(95.0)
    assert account.positions["BTC/USDT"]["mark_price"] == pytest.approx(110.0)
    assert "profit_lock_mode" not in account.positions["BTC/USDT"]
    assert not any(
        str(trade.get("action") or "").startswith("CLOSE")
        for trade in account.trades
    )
