"""Confirmed pivots never bypass the meme holding/structure qualifications."""

import asyncio
import copy
from types import SimpleNamespace

import pytest

from core.services.exits.strict_true_pivot import (
    evaluate_position_exit, profitable_true_pivot_exit,
    PROFIT_PIVOT_PENDING_KEY, store_confirmed_pivot_exit,
)
from core.services.exits.authorized_position_exit import automated_position_exit_allowed
from core.services.math_execution_gate import validate_exit_at_execution
from test_profit_pivot_exit import pivot_frame
from test_kc_wave_exit import position

SYMBOLS = ["龙虾/USDT", "CAP/USDT", "1000PEPE/USDT"]


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("saved", [False, True])
@pytest.mark.parametrize("account_type", ["paper", "testnet"])
@pytest.mark.parametrize("fault", [
    None, "buffer", "two_below_buffer", "ma_equal", "ma_original_side",
    "same_color", "three_held", "live", "gap", "invalid", "no_frame",
])
def test_all_boundaries_enforce_structure_even_for_persisted_pivots(
    symbol, side, saved, account_type, fault, caplog, monkeypatch, tmp_path,
):
    from core.paper_account import PaperAccount
    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
    frame = pivot_frame(side)
    pos = position(side, frame)
    pivot = profitable_true_pivot_exit(pos, frame)
    assert pivot is not None
    if saved:
        assert store_confirmed_pivot_exit(pos, pivot, key=PROFIT_PIVOT_PENDING_KEY)
    sign = 1 if side == "LONG" else -1
    last, prev = frame.index[-1], frame.index[-2]
    close = float(frame.loc[last, "close"])
    if fault in ("buffer", "two_below_buffer"):
        frame.loc[last, "kc_middle"] = close + sign * 0.2
        frame.loc[prev, "kc_middle"] = frame.loc[last, "kc_middle"] - sign * 0.01
        if fault == "two_below_buffer":
            frame.loc[prev, "close"] = close + sign * 0.05
            frame.loc[prev, "low" if side == "LONG" else "high"] = close
            frame.loc[prev, "kc_middle"] = frame.loc[prev, "close"] + sign * 0.1
            frame.loc[last, "kc_middle"] = frame.loc[prev, "kc_middle"] + sign * 0.01
    elif fault == "ma_equal":
        frame.loc[last, "ma15"] = frame.loc[last, "ma5"]
    elif fault == "ma_original_side":
        frame.loc[last, "ma15"] = frame.loc[last, "ma5"] - sign * 0.1
    elif fault == "same_color":
        frame.loc[last, "open"] = close - sign * 0.01
    elif fault == "three_held":
        pos["open_timestamp"] = float(frame.iloc[-3]["timestamp"]) / 1000
    elif fault == "live":
        frame.loc[last, "is_closed"] = False
    elif fault == "gap":
        frame.loc[prev, "timestamp"] -= 10_000
    elif fault == "invalid":
        frame.loc[last, "ma15"] = float("nan")
    elif fault == "no_frame":
        frame = None
    expected = fault is None
    quote = 100.3 if side == "LONG" else 99.7
    caplog.set_level("INFO")
    assert (profitable_true_pivot_exit(pos, frame, quote=quote) is not None) is expected
    if account_type == "paper":
        account = PaperAccount()
    else:
        from core.testnet_account import BinanceTestnetAccount
        from test_testnet_account import FakeTestnetExchange
        from unittest.mock import AsyncMock
        account = BinanceTestnetAccount(
            FakeTestnetExchange(), state_file=str(tmp_path / "testnet.json"),
        )
        account.refresh = AsyncMock()
    account.positions[symbol] = pos
    account._channel_exit_frames = {symbol: frame}
    assert automated_position_exit_allowed(symbol, pos, {}, pivot[0], quote, frame=frame) is expected
    assert validate_exit_at_execution(account, symbol, pos, quote, pivot[0])[0] is expected
    assert asyncio.run(account.close_position(symbol, quote, pivot[0])) is expected
    assert (symbol not in account.positions) is expected
    if fault in ("buffer", "two_below_buffer"):
        assert f"[VETO_HOLD_{side}_050_ATR_BUFFER]" in caplog.text


@pytest.mark.parametrize("symbol", ["龍蝦/USDT", "LOBSTER/USDT:USDT", "1000PEPEUSDT", "CAP/USDT:USDT"])
def test_aliases_cannot_skip_structure(symbol):
    pos = position("LONG", pivot_frame("LONG"))
    pos["symbol"] = symbol
    assert evaluate_position_exit(pos, None)[1] == "VETO_HOLD_STRUCTURE_DATA_REQUIRED"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_observed_position_extreme_used_for_trailing(side):
    frame = pivot_frame(side)
    sign = 1 if side == "LONG" else -1
    last, prev = frame.index[-1], frame.index[-2]
    frame.loc[last, "kc_middle"] = frame.loc[last, "close"] - sign * 0.1
    frame.loc[prev, "kc_middle"] = frame.loc[last, "kc_middle"] + sign * 0.01
    pos = position(side, frame)
    assert not evaluate_position_exit(pos, frame)[0]
    pos["highest_price" if side == "LONG" else "lowest_price"] = 100 + sign * 3
    assert evaluate_position_exit(pos, frame)[0]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("distance,allowed", [(0.499999, False), (0.5, False), (0.500001, True)])
def test_strict_half_atr_structure_boundary(side, distance, allowed):
    frame = pivot_frame(side)
    sign = 1 if side == "LONG" else -1
    last, prev = frame.index[-1], frame.index[-2]
    close = float(frame.loc[last, "close"])
    frame.loc[last, "kc_middle"] = close + sign * distance
    # Flat KC avoids the aligned buffer veto; previous close has not broken middle.
    frame.loc[prev, "kc_middle"] = frame.loc[last, "kc_middle"]
    assert evaluate_position_exit(position(side, frame), frame)[0] is allowed


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_four_fully_held_bars_not_signal_candles(side):
    frame = pivot_frame(side)
    pos = position(side, frame)
    pos["open_timestamp"] = float(frame.iloc[-4]["timestamp"]) / 1000
    assert evaluate_position_exit(pos, frame)[0]
    pos["open_timestamp"] += 0.001
    assert evaluate_position_exit(pos, frame)[1] == "VETO_HOLD_MIN_4_COMPLETED_BARS"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_two_middle_breaks_qualify_only_when_buffer_not_aligned(side):
    frame = pivot_frame(side)
    sign = 1 if side == "LONG" else -1
    last, prev = frame.index[-1], frame.index[-2]
    frame.loc[prev, "kc_middle"] = frame.loc[prev, "close"] + sign * 0.1
    frame.loc[last, "kc_middle"] = frame.loc[last, "close"] + sign * 0.1
    assert evaluate_position_exit(position(side, frame), frame)[0]


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("gain,retreat,expected", [
    (2.499999, 1.6, False), (2.5, 1.5, False), (2.5, 1.500001, True),
])
def test_trailing_thresholds_do_not_replace_pivot(side, gain, retreat, expected):
    frame = pivot_frame(side, gain=gain)
    sign = 1 if side == "LONG" else -1
    last, prev = frame.index[-1], frame.index[-2]
    close = 100 + sign * (gain - retreat)
    frame.loc[last, ["open", "high", "low", "close"]] = [
        close + sign * 0.1, close + 0.2, close - 0.2, close,
    ]
    frame.loc[last, "kc_middle"] = close - sign * 0.1
    frame.loc[prev, "kc_middle"] = frame.loc[last, "kc_middle"] + sign * 0.01
    frame.loc[last, "ma5"] = close + sign * 0.2
    frame.loc[last, "ma15"] = close + sign * 0.3
    pos = position(side, frame)
    assert evaluate_position_exit(pos, frame)[0] is expected
    # Destroy the strict pivot while preserving sufficient historic favorable movement.
    frame.loc[last, "high" if side == "LONG" else "low"] = 100 + sign * gain
    assert profitable_true_pivot_exit(pos, frame, quote=close) is None
