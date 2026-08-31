import pytest
import pandas as pd

import core.paper_account as pa_module
from core.engine import TradingEngine
from core.paper_account import PaperAccount


def _channel_frame(lower: float = 99.0, upper: float = 101.0) -> pd.DataFrame:
    return pd.DataFrame({
        "open": [100.0] * 20,
        "close": [100.0] * 20,
        "high": [100.5] * 20,
        "low": [99.5] * 20,
        "ma3": [100.0] * 20,
        "kc_lower": [lower] * 20,
        "kc_upper": [upper] * 20,
    })


def _closed_trough(frame: pd.DataFrame):
    frame.loc[frame.index[-2], ["open", "close", "low", "high"]] = [
        99.0, 99.1, 98.9, 99.15,
    ]


def _closed_peak(frame: pd.DataFrame):
    frame.loc[frame.index[-2], ["open", "close", "low", "high"]] = [
        101.0, 100.9, 100.85, 101.1,
    ]


def test_channel_swing_enters_only_after_closed_turn_candle_and_next_breakout():
    frame = _channel_frame()
    _closed_trough(frame)

    low = TradingEngine._channel_swing_action(frame, 99.2)

    frame = _channel_frame()
    _closed_peak(frame)
    high = TradingEngine._channel_swing_action(frame, 100.8)

    assert low == {
        "action": "ENTER", "side": "LONG",
        "kc_upper": 101.0, "kc_lower": 99.0, "reason": "",
    }
    assert high == {
        "action": "ENTER", "side": "SHORT",
        "kc_upper": 101.0, "kc_lower": 99.0, "reason": "",
    }


def test_channel_swing_holds_between_entry_and_opposite_edge():
    frame = _channel_frame()

    assert TradingEngine._channel_swing_action(frame, 100.0, "LONG")["action"] == "HOLD"
    assert TradingEngine._channel_swing_action(frame, 100.0, "SHORT")["action"] == "HOLD"


def test_channel_swing_reverses_only_after_touching_opposite_rail():
    frame = _channel_frame()
    _closed_peak(frame)
    long_exit = TradingEngine._channel_swing_action(frame, 100.8, "LONG")

    frame = _channel_frame()
    _closed_trough(frame)
    short_exit = TradingEngine._channel_swing_action(frame, 99.2, "SHORT")

    assert (long_exit["action"], long_exit["side"]) == ("REVERSE", "SHORT")
    assert (short_exit["action"], short_exit["side"]) == ("REVERSE", "LONG")


def test_channel_swing_does_not_enter_from_unclosed_live_green_or_red_candle():
    frame = _channel_frame()
    frame.loc[frame.index[-1], ["open", "low"]] = [99.1, 98.9]

    no_trough = TradingEngine._channel_swing_action(frame, 99.2)

    assert no_trough["action"] == "WAIT"
    assert no_trough["reason"] == "WAIT_CLOSE_GREEN"

    frame = _channel_frame()
    frame.loc[frame.index[-1], ["open", "high"]] = [100.9, 101.1]
    no_peak = TradingEngine._channel_swing_action(frame, 100.8)

    assert no_peak["action"] == "WAIT"
    assert no_peak["reason"] == "WAIT_CLOSE_RED"


def test_channel_swing_waits_for_next_candle_breakout():
    frame = _channel_frame()
    _closed_trough(frame)

    result = TradingEngine._channel_swing_action(frame, 99.1)

    assert result["action"] == "WAIT"
    assert result["reason"] == "WAIT_BREAK_HIGH"


def test_channel_swing_cancels_failed_trough_before_entry():
    frame = _channel_frame()
    _closed_trough(frame)

    result = TradingEngine._channel_swing_action(frame, 98.8)

    assert result["action"] == "WAIT"
    assert result["reason"] == "CANCEL_LONG"


def test_channel_swing_cancels_trough_if_confirmation_candle_wicked_below_first():
    frame = _channel_frame()
    _closed_trough(frame)
    frame.loc[frame.index[-1], ["low", "high"]] = [98.8, 99.3]

    result = TradingEngine._channel_swing_action(frame, 99.2)

    assert result["action"] == "WAIT"
    assert result["reason"] == "CANCEL_LONG"


def test_channel_swing_short_waits_then_cancels_if_next_candle_breaks_high():
    frame = _channel_frame()
    _closed_peak(frame)

    waiting = TradingEngine._channel_swing_action(frame, 100.9)
    frame.loc[frame.index[-1], ["low", "high"]] = [100.7, 101.2]
    cancelled = TradingEngine._channel_swing_action(frame, 100.8)

    assert waiting["action"] == "WAIT"
    assert waiting["reason"] == "WAIT_BREAK_LOW"
    assert cancelled["action"] == "WAIT"
    assert cancelled["reason"] == "CANCEL_SHORT"


def test_channel_swing_does_not_exit_in_outer_entry_zone_before_actual_rail():
    frame = _channel_frame()
    frame.loc[frame.index[-2], ["open", "close", "low", "high"]] = [
        101.0, 100.9, 100.8, 100.95,
    ]
    # 已進入 70% 外側進場區，但尚未觸及真正上軌；持倉不得平倉反手。

    result = TradingEngine._channel_swing_action(frame, 100.8, "LONG")

    assert result["action"] == "HOLD"
    assert result["side"] is None


def test_channel_swing_does_not_chase_after_price_returns_to_middle():
    frame = _channel_frame()
    _closed_peak(frame)

    result = TradingEngine._channel_swing_action(frame, 100.6, "LONG")

    assert result["action"] == "HOLD"
    assert result["reason"] == "MISSED_UPPER"


def test_channel_swing_positions_are_not_managed_by_early_exit_loops():
    assert TradingEngine._is_continuous_wave_position({
        "entry_mode": "CHANNEL_SWING",
    })


@pytest.mark.anyio
async def test_channel_swing_position_is_stored_without_sl_or_tp(tmp_path, monkeypatch):
    monkeypatch.setattr(pa_module, "STATE_FILE", str(tmp_path / "channel_swing.json"))
    account = PaperAccount()

    opened = await account.open_position(
        "BTC/USDT", "LONG", 100.0, 50.0, 95.0, 110.0,
        "channel swing", leverage=1, signal_score=100, apply_slippage=False,
        entry_context={
            "entry_mode": "CHANNEL_SWING",
            "wave_regime": "RANGE",
            "initial_sl": 95.0,
            "initial_risk": 5.0,
        },
    )

    assert opened is True
    assert account.positions["BTC/USDT"]["sl"] == 0.0
    assert account.positions["BTC/USDT"]["tp"] == 0.0
    assert account.position_meta["BTC/USDT"]["sl"] == 0.0
    assert account.position_meta["BTC/USDT"]["initial_sl"] == 0.0

    topped_up = await account.open_position(
        "BTC/USDT", "LONG", 100.2, 25.0, 95.0, 110.0,
        "channel swing top-up", leverage=1, signal_score=100,
        apply_slippage=False,
        entry_context={"entry_mode": "CHANNEL_SWING"},
    )

    assert topped_up is False
    assert account.positions["BTC/USDT"]["margin"] == 50.0
