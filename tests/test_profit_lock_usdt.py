import pytest

import core.paper_account as pa_module
from core.paper_account import PaperAccount


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
    monkeypatch.setattr(pa_module, "ENABLE_PROFIT_GIVEBACK_EXIT", False)

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

    # 本金150U：回吐空間6U；手續費兩倍地板1.5U；
    # 半段啟動門檻=1.5+3=4.5U。價格到100.61時浮盈4.575U。
    await account.update_positions({"BTC/USDT": 100.61})
    assert account.positions["BTC/USDT"]["sl"] == pytest.approx(100.4575)

    # 0.9%峰值時，舊百分比 trailing 會把SL收緊到約100.84；
    # U額規則改為保留峰值75%，且至少鎖住1.5U。
    await account.update_positions({"BTC/USDT": 100.9})
    assert account.positions["BTC/USDT"]["sl"] == pytest.approx(100.675)
    assert not account.position_meta["BTC/USDT"].get("fixed_profit_lock_pct_armed")
    assert not account.position_meta["BTC/USDT"].get("early_profit_guard_armed")
