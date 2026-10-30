import pytest
from core.services.exits.staged_trailing_exit_engine import evaluate_staged_trailing_exit


def test_long_staged_ladder_and_trailing():
    entry_price = 100.0
    pos = {"side": "LONG", "entry_price": entry_price}

    # 1. 浮盈 +2% (<4%) -> 不觸發保本
    should_exit, reason, meta = evaluate_staged_trailing_exit(pos, 102.0, "BULLISH")
    assert not should_exit
    assert pos.get("trailing_stage") == 0

    # 2. 浮盈 +5% (>=4%) -> 進入階段 1 (保本 100.3)
    should_exit, reason, meta = evaluate_staged_trailing_exit(pos, 105.0, "BULLISH")
    assert not should_exit
    assert pos.get("trailing_stage") == 1
    assert pos.get("trailing_stop_price") == pytest.approx(100.3)

    # 3. 浮盈 +12% (>=10%) -> 進入階段 2 (小利 105.0)
    should_exit, reason, meta = evaluate_staged_trailing_exit(pos, 112.0, "BULLISH")
    assert not should_exit
    assert pos.get("trailing_stage") == 2
    assert pos.get("trailing_stop_price") == pytest.approx(105.0)

    # 4. 浮盈 +16% (>=15%) -> 進入階段 3 (中利 110.0)
    should_exit, reason, meta = evaluate_staged_trailing_exit(pos, 116.0, "BULLISH")
    assert not should_exit
    assert pos.get("trailing_stage") == 3
    assert pos.get("trailing_stop_price") == pytest.approx(110.0)

    # 5. 浮盈 +25% (>=20%) -> 進入階段 4 (移動止盈啟動，最高價 125.0)
    should_exit, reason, meta = evaluate_staged_trailing_exit(pos, 125.0, "BULLISH")
    assert not should_exit
    assert pos.get("trailing_stage") == 4
    assert pos.get("peak_price") == 125.0

    # 6. 回吐 4% (120.0，跌幅 4% < 5%) -> 續抱
    should_exit, reason, meta = evaluate_staged_trailing_exit(pos, 120.0, "BULLISH")
    assert not should_exit

    # 7. 回吐 5.6% (118.0，自 125 回吐 5.6% >= 5%) -> 觸發階段 4 移動止盈
    should_exit, reason, meta = evaluate_staged_trailing_exit(pos, 118.0, "BULLISH")
    assert should_exit is True
    assert "STAGE4_TRAILING_STOP_5PCT" in reason


def test_short_staged_ladder_and_trailing():
    entry_price = 100.0
    pos = {"side": "SHORT", "entry_price": entry_price}

    # 1. 浮盈 +5% (價格跌至 95，>=4%) -> 階段 1 保本 (99.7)
    should_exit, reason, meta = evaluate_staged_trailing_exit(pos, 95.0, "BEARISH")
    assert not should_exit
    assert pos.get("trailing_stage") == 1
    assert pos.get("trailing_stop_price") == pytest.approx(99.7)

    # 2. 浮盈 +11% (價格跌至 89，>=10%) -> 階段 2 (95.0)
    should_exit, reason, meta = evaluate_staged_trailing_exit(pos, 89.0, "BEARISH")
    assert not should_exit
    assert pos.get("trailing_stage") == 2
    assert pos.get("trailing_stop_price") == pytest.approx(95.0)

    # 3. 浮盈 +22% (價格跌至 78，>=20%) -> 階段 4 (移動止盈，低點 78.0)
    should_exit, reason, meta = evaluate_staged_trailing_exit(pos, 78.0, "BEARISH")
    assert not should_exit
    assert pos.get("trailing_stage") == 4
    assert pos.get("trough_price") == 78.0

    # 4. 自 78.0 反彈至 82.0 (反彈 5.12% >= 5%) -> 觸發階段 4 移動止盈
    should_exit, reason, meta = evaluate_staged_trailing_exit(pos, 82.0, "BEARISH")
    assert should_exit is True
    assert "STAGE4_TRAILING_STOP_5PCT" in reason


def test_macro_trend_reversal_exit():
    pos_long = {"side": "LONG", "entry_price": 100.0}
    should_exit, reason, _ = evaluate_staged_trailing_exit(pos_long, 101.0, "BEARISH")
    assert should_exit is True
    assert "MACRO_TREND_REVERSAL_TO_BEAR" in reason

    pos_short = {"side": "SHORT", "entry_price": 100.0}
    should_exit, reason, _ = evaluate_staged_trailing_exit(pos_short, 99.0, "BULLISH")
    assert should_exit is True
    assert "MACRO_TREND_REVERSAL_TO_BULL" in reason

