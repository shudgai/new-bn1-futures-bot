import pytest

import core.paper_account as pa_module
from core.paper_account import PaperAccount, get_profit_lock_giveback_usdt


@pytest.mark.parametrize(
    ("margin", "expected_gap"),
    [
        (99.0, 1.0),
        (100.0, 1.0),
        (100.01, 2.0),
        (200.0, 2.0),
        (200.01, 3.0),
        (300.01, 4.0),
    ],
)
def test_profit_lock_capital_tiers_scale_automatically(margin, expected_gap):
    assert get_profit_lock_giveback_usdt(margin, 0.0) == pytest.approx(expected_gap)


def test_profit_lock_uses_25_percent_when_it_is_wider_than_capital_tier():
    assert get_profit_lock_giveback_usdt(150.0, 11.0) == pytest.approx(2.75)


@pytest.mark.anyio
async def test_usdt_profit_lock_is_not_tightened_by_percentage_guards(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        pa_module, "STATE_FILE", str(tmp_path / "profit_lock_account.json")
    )
    monkeypatch.setattr(pa_module, "ENABLE_PROFIT_LOCK_USDT", True)
    monkeypatch.setattr(pa_module, "PROFIT_LOCK_TRIGGER_USDT", 4.0)
    monkeypatch.setattr(pa_module, "ENABLE_PROFIT_BANK", True)
    monkeypatch.setattr(pa_module, "ENABLE_FIXED_PROFIT_LOCK_PCT", True)
    monkeypatch.setattr(pa_module, "ENABLE_EARLY_PROFIT_GUARD", True)
    monkeypatch.setattr(pa_module, "ENABLE_TRAILING_STOP", True)
    monkeypatch.setattr(pa_module, "ENABLE_PROFIT_GIVEBACK_EXIT", True)

    account = PaperAccount()
    account.balance = 150.0
    assert await account.open_position(
        "BTC/USDT",
        "LONG",
        100.0,
        150.0,
        98.0,
        0.0,
        "MA alignment",
        atr=1.0,
        leverage=5,
        signal_score=100,
        apply_slippage=False,
        entry_context={"entry_mode": "MA_ALIGNMENT"},
    )

    # 150U本金：最低回吐2U；費用保護1.5U，因此峰值未達3.5U前不啟動。
    await account.update_positions({"BTC/USDT": 100.19})
    assert account.positions["BTC/USDT"]["sl"] == pytest.approx(98.0)

    # 即使已超過手續費兩倍，仍保留原始止損，不在最高點貼線。
    await account.update_positions({"BTC/USDT": 100.21})
    assert account.positions["BTC/USDT"]["sl"] == pytest.approx(98.0)

    # 浮盈3.525U啟動：允許回吐2U，先鎖住約1.525U。
    await account.update_positions({"BTC/USDT": 100.47})
    assert account.positions["BTC/USDT"]["sl"] == pytest.approx(100.2033333333)

    # 峰值6.75U時仍給2U空間，保護4.75U，不鎖在100%最高點。
    await account.update_positions({"BTC/USDT": 100.9})
    assert account.positions["BTC/USDT"]["sl"] == pytest.approx(100.6333333333)
    assert not account.position_meta["BTC/USDT"].get("fixed_profit_lock_pct_armed")
    assert not account.position_meta["BTC/USDT"].get("early_profit_guard_armed")

    # 小於2U的正常回吐不平倉；跌破保護線後才出場。
    await account.update_positions({"BTC/USDT": 100.7})
    assert "BTC/USDT" in account.positions
    await account.update_positions({"BTC/USDT": 100.62})
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
