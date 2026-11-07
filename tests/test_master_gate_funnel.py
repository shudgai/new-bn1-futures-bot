import pytest
from core.gates.master_gate_funnel import (
    MarketSnapshot,
    MasterGateFunnel,
    MasterOpenGatekeeper,
    PositionSnapshot,
)


def short_breakout_snapshot(quote=99.8):
    return MarketSnapshot(
        symbol="TESTUSDT",
        quote=quote,
        open_price=100.2,
        close_price=99.7,
        high_price=100.3,
        low_price=99.6,
        ma3_curr=99.8,
        ma3_prev=100.0,
        ma5_curr=99.9,
        ma5_prev=100.0,
        ma15_curr=100.5,
        ma15_prev=100.7,
        kc_upper=104.0,
        kc_middle=102.0,
        kc_middle_prev=102.5,
        kc_lower=100.0,
        atr=1.0,
        macro_trend="BEARISH",
        ma_cross_count_8=0,
    )


def test_fresh_short_breakdown_below_lower_band_is_authorized():
    # Arrange
    snapshot = short_breakout_snapshot()

    # Act
    decision = MasterGateFunnel.evaluate_open_short(snapshot)

    # Assert
    assert decision.can_execute is True
    assert decision.action == "OPEN_SHORT"
    assert decision.reason == "AUTHORIZED_SHORT:TRIGGER_B_BREAKDOWN_LOWER_RAIL"


def test_short_breakdown_far_below_lower_band_is_rejected_as_chasing():
    # Arrange: quote is more than 0.5 ATR below KC lower band.
    snapshot = short_breakout_snapshot(quote=99.4)

    # Act
    decision = MasterGateFunnel.evaluate_open_short(snapshot)

    # Assert
    assert decision.can_execute is False
    assert decision.reason == "BLOCKED_SHORT_CHASE_KC_LOWER_EXTENSION"


def test_confirmed_long_peak_is_not_vetoed_by_midline_or_green_candle():
    # Arrange: price remains above the KC midline and the current candle is
    # bullish, conditions that normally keep a long position open.
    market = short_breakout_snapshot()._replace(
        quote=102.7,
        open_price=102.3,
        close_price=102.6,
        high_price=102.8,
        low_price=102.2,
        kc_upper=104.0,
        kc_middle=102.0,
        kc_middle_prev=101.8,
        kc_lower=100.0,
        atr=1.0,
    )
    position = PositionSnapshot(
        symbol="TESTUSDT",
        side="LONG",
        entry_price=101.0,
        open_timestamp_ms=0.0,
        bars_held=20,
        peak_roe_pct=8.0,
        has_confirmed_pivot=True,
        highest_price_since_entry=103.0,
    )

    # Act
    decision = MasterGateFunnel.evaluate_position_exit(
        position, market, "EARLY_PIVOT_PEAK_EXIT", current_time_ms=2_000_000.0,
    )

    # Assert
    assert decision.can_execute is True
    assert decision.action == "CLOSE_POSITION"
    assert decision.reason == "EARLY_PIVOT_PEAK_EXIT"


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "1000PEPE/USDT"])
def test_kc_middle_down_strictly_blocks_long_and_allows_only_short(symbol):
    """KC 中軌往下走空頭時，龍蝦與 1000PEPE 嚴禁開多單 (連底部轉折與破軌優先通道皆不可豁免)，只能評估空單。"""
    bearish_kc_snapshot = MarketSnapshot(
        symbol=symbol,
        quote=100.3,
        open_price=99.6,
        close_price=100.3,
        high_price=100.35,
        low_price=99.55,
        ma3_curr=100.2,
        ma3_prev=99.8,
        ma5_curr=100.2,
        ma5_prev=99.8,
        ma15_curr=100.0,
        ma15_prev=99.9,
        kc_upper=102.0,
        kc_middle=99.9,
        kc_middle_prev=100.2,  # KC 中軌向下!
        kc_lower=98.0,
        atr=1.0,
        macro_trend="BEARISH",
        ma_cross_count_8=0,
    )

    assert MasterGateFunnel.is_bottom_pivot_long(bearish_kc_snapshot) is False
    assert MasterGateFunnel.evaluate_top_bottom_flip(bearish_kc_snapshot, "SHORT") is None

    filter_res = MasterGateFunnel.evaluate_entry_filters(bearish_kc_snapshot, "LONG")
    assert filter_res.can_execute is False

    open_long_res = MasterGateFunnel.evaluate_open_long(bearish_kc_snapshot)
    assert open_long_res.can_execute is False
    assert open_long_res.reason == "BLOCKED_LONG_ON_BEARISH_TREND"

    pivot_cross_res = MasterOpenGatekeeper.evaluate_pivot_or_cross_open(bearish_kc_snapshot, "LONG")
    assert pivot_cross_res.can_execute is False
    assert pivot_cross_res.reason == "BLOCKED_LONG_ON_BEARISH_TREND"


@pytest.mark.parametrize("symbol", ["龙虾/USDT", "1000PEPE/USDT"])
def test_kc_middle_up_strictly_blocks_short_and_allows_only_long(symbol):
    """KC 中軌往上走多頭時，龍蝦與 1000PEPE 嚴禁開空單 (連頂部轉折與跌破優先通道皆不可豁免)，只能評估多單。"""
    bullish_kc_snapshot = MarketSnapshot(
        symbol=symbol,
        quote=99.7,
        open_price=100.4,
        close_price=99.7,
        high_price=100.45,
        low_price=99.65,
        ma3_curr=99.8,
        ma3_prev=100.2,
        ma5_curr=99.8,
        ma5_prev=100.2,
        ma15_curr=100.0,
        ma15_prev=100.1,
        kc_upper=102.0,
        kc_middle=100.1,
        kc_middle_prev=99.8,  # KC 中軌向上!
        kc_lower=98.0,
        atr=1.0,
        macro_trend="BULLISH",
        ma_cross_count_8=0,
    )

    assert MasterGateFunnel.is_top_pivot_short(bullish_kc_snapshot) is False
    assert MasterGateFunnel.evaluate_top_bottom_flip(bullish_kc_snapshot, "LONG") is None

    filter_res = MasterGateFunnel.evaluate_entry_filters(bullish_kc_snapshot, "SHORT")
    assert filter_res.can_execute is False

    open_short_res = MasterGateFunnel.evaluate_open_short(bullish_kc_snapshot)
    assert open_short_res.can_execute is False
    assert open_short_res.reason == "BLOCKED_SHORT_ON_BULLISH_TREND"

    pivot_cross_res = MasterOpenGatekeeper.evaluate_pivot_or_cross_open(bullish_kc_snapshot, "SHORT")
    assert pivot_cross_res.can_execute is False
    assert pivot_cross_res.reason == "BLOCKED_SHORT_ON_BULLISH_TREND"

