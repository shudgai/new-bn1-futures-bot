import pytest
from core.services.exits.staged_profit_lock import check_exit_conditions, evaluate_staged_profit_lock


def test_scheme1_short_wick_ignored():
    """空單下殺浮盈達 15%，回抽回吐 20%，但 MA3 仍低於 MA15 (插針未反轉) -> 忽略鎖利續抱"""
    entry_p = 100.0
    current_p = 88.0  # 浮盈 12%
    highest_margin = 0.15  # 最高浮盈 15% (回吐 (0.15 - 0.12)/0.15 = 20%)
    current_ma3 = 87.0
    current_ma15 = 89.0  # MA3 < MA15 (空頭排列未反轉)

    action = check_exit_conditions(
        position="SHORT",
        entry_price=entry_p,
        current_price=current_p,
        highest_profit_margin=highest_margin,
        current_ma3=current_ma3,
        current_ma15=current_ma15,
    )
    assert action == "HOLD", "MA3 仍壓在 MA15 下方，應判定為插針假拉高並忽略鎖利續抱！"


def test_scheme1_short_trend_reversed_triggers_exit():
    """空單浮盈達 15%，回吐達 20%，且 MA3 > MA15 (趨勢反轉) -> 觸發平空離場"""
    entry_p = 100.0
    current_p = 88.0  # 浮盈 12%
    highest_margin = 0.15  # 最高浮盈 15%
    current_ma3 = 89.5
    current_ma15 = 89.0  # MA3 > MA15 (黃金交叉反轉)

    action = check_exit_conditions(
        position="SHORT",
        entry_price=entry_p,
        current_price=current_p,
        highest_profit_margin=highest_margin,
        current_ma3=current_ma3,
        current_ma15=current_ma15,
    )
    assert action == "CLOSE_SHORT", "MA3 > MA15 且回吐達 20%，應立即觸發平空鎖利！"


def test_scheme1_long_wick_ignored():
    """多單衝高浮盈達 15%，回踩回吐 20%，但 MA3 仍高於 MA15 (插針未反轉) -> 忽略鎖利續抱"""
    entry_p = 100.0
    current_p = 112.0  # 浮盈 12%
    highest_margin = 0.15  # 最高浮盈 15%
    current_ma3 = 113.0
    current_ma15 = 111.0  # MA3 > MA15 (多頭排列未反轉)

    action = check_exit_conditions(
        position="LONG",
        entry_price=entry_p,
        current_price=current_p,
        highest_profit_margin=highest_margin,
        current_ma3=current_ma3,
        current_ma15=current_ma15,
    )
    assert action == "HOLD", "MA3 仍守在 MA15 上方，應判定為插針洗盤並忽略鎖利續抱！"


def test_scheme1_long_trend_reversed_triggers_exit():
    """多單浮盈達 15%，回吐達 20%，且 MA3 < MA15 (死亡交叉反轉) -> 觸發平多離場"""
    entry_p = 100.0
    current_p = 112.0  # 浮盈 12%
    highest_margin = 0.15  # 最高浮盈 15%
    current_ma3 = 110.5
    current_ma15 = 111.0  # MA3 < MA15 (死叉反轉)

    action = check_exit_conditions(
        position="LONG",
        entry_price=entry_p,
        current_price=current_p,
        highest_profit_margin=highest_margin,
        current_ma3=current_ma3,
        current_ma15=current_ma15,
    )
    assert action == "CLOSE_LONG", "MA3 < MA15 且回吐達 20%，應立即觸發平多鎖利！"


def test_evaluate_staged_profit_lock_integration_with_scheme1():
    """整合測試 evaluate_staged_profit_lock 帶入 current_ma3/ma15 的忽略與觸發分支"""
    pos = {
        "side": "SHORT",
        "entry_price": 100.0,
        "qty": 1.0,
        "leverage": 1.0,
        "margin": 100.0,
        "fee_rate": 0.0,
        "slippage_pct": 0.0,
        "accumulated_funding_fee": 0.0,
        "lowest_price": 85.0,  # 浮盈 15% (滿足最高淨利 >= 10%)
        "highest_favorable_price": 85.0,
    }
    # 價格回吐至 88.0 (浮盈 12%，回吐 (15-12)/15 = 20%)，若 MA3 仍小於 MA15 (未反轉)
    should_exit, reason, meta = evaluate_staged_profit_lock(
        position=pos,
        current_price=88.0,
        side="SHORT",
        current_ma3=87.0,
        current_ma15=89.0,
    )
    assert should_exit is False
    assert reason == "VETO_HOLD_SCHEME1_MA3_MA15_FILTER"
    assert "忽略鎖利" in meta["cause"]

    # 價格回吐至 88.0，若 MA3 > MA15 (已反轉)
    should_exit, reason, meta = evaluate_staged_profit_lock(
        position=pos,
        current_price=88.0,
        side="SHORT",
        current_ma3=90.0,
        current_ma15=89.0,
    )
    assert should_exit is True
    assert "PROFIT_LOCK" in reason
