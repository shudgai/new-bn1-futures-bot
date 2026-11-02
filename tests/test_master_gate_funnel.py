from core.gates.master_gate_funnel import (
    MarketSnapshot,
    MasterGateFunnel,
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
