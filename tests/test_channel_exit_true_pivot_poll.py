import asyncio

import pandas as pd
import pytest

from core.services.exits.entry_atr_protection import enforce_atr_protection
from core.services.exits.authorized_position_exit import (
    automated_position_exit_allowed,
)


def _valley_frame(live_bar_ms):
    frame = pd.DataFrame(
        [
            *[
                {
                    "timestamp": live_bar_ms - (900_000 - index * 60_000),
                    "open": 13.0, "high": 15.0, "low": 12.0, "close": 13.0,
                    "ma5": 13.0, "ma15": 13.5, "kc_middle": 12.0,
                    "is_closed": True,
                }
                for index in range(10)
            ],
            {
                "timestamp": live_bar_ms - 300_000,
                "open": 13.0, "high": 14.0, "low": 12.0, "close": 13.0,
                "ma5": 13.0, "ma15": 13.5, "kc_middle": 12.0,
                "is_closed": True,
            },
            {
                "timestamp": live_bar_ms - 240_000,
                "open": 11.5, "high": 12.0, "low": 11.0, "close": 11.5,
                "ma5": 11.0, "ma15": 12.0, "kc_middle": 11.5,
                "is_closed": True,
            },
            {
                "timestamp": live_bar_ms - 180_000,
                "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
                "ma5": 10.0, "ma15": 11.5, "kc_middle": 11.0,
                "is_closed": True,
            },
            {
                "timestamp": live_bar_ms - 120_000,
                "open": 10.4, "high": 10.9, "low": 9.2, "close": 10.0,
                "ma5": 10.0, "ma15": 11.0, "kc_middle": 11.2,
                "is_closed": True,
            },
            {
                "timestamp": live_bar_ms - 60_000,
                "open": 8.5, "high": 8.6, "low": 7.4, "close": 8.2,
                "ma5": 10.2, "ma15": 11.2, "kc_middle": 11.0,
                "is_closed": True,
            },
            {
                "timestamp": live_bar_ms,
                "open": 10.5, "high": 10.7, "low": 10.3, "close": 10.6,
                "ma5": 10.5, "ma15": 11.0, "kc_middle": 10.8,
                "is_closed": False,
            },
        ]
    )
    frame["atr"] = 1.0
    frame["kc_upper"] = 15.0
    frame["kc_middle"] = 11.0
    frame["kc_lower"] = 8.0
    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["snapshot_ms"] = live_bar_ms + 2_000
    return frame


def _short_position(live_bar_ms):
    return {
        "side": "SHORT",
        "entry_price": 15.0,
        "qty": 1.0,
        "open_timestamp": (live_bar_ms - 299_000) / 1_000,
        "entry_mode": "CHANNEL_SWING",
    }


class _Account:
    def __init__(self, symbol, position, frame, close_results):
        self.positions = {symbol: position}
        self.position_meta = {}
        self.channel_exit_frame_provider = lambda _symbol: frame
        self.close_results = list(close_results)
        self.close_attempts = []
        self.logs = []

    def log(self, message, *_args):
        self.logs.append(message)

    def save_state(self):
        pass

    async def close_position(self, symbol, price, reason, *, is_manual=False):
        position = self.positions[symbol]
        metadata = self.position_meta[symbol]
        if not automated_position_exit_allowed(
            symbol, position, metadata, reason, price, is_manual=is_manual,
        ):
            return False
        self.close_attempts.append((symbol, price, reason))
        return self.close_results.pop(0)


@pytest.mark.parametrize("symbol", ["LOBSTER/USDT", "CAP/USDT"])
def test_channel_short_true_valley_closes_and_retries_without_current_frame(
    monkeypatch, symbol,
):
    live_bar_ms = 1_800_000_000_000
    position = _short_position(live_bar_ms)
    frame = _valley_frame(live_bar_ms)
    monkeypatch.setattr(
        "core.services.exits.entry_atr_protection.time.time",
        lambda: (live_bar_ms + 2_000) / 1_000,
    )
    account = _Account(
        symbol,
        position,
        frame,
        close_results=[False, False, True],
    )

    first = asyncio.run(enforce_atr_protection(account, symbol, 10.6))

    assert first is True
    assert len(account.close_attempts) == 1
    assert account.close_attempts[0][2].endswith(
        "EXIT_BY_TRUE_VALLEY_EXTREME_REVERSAL"
    )
    assert position["kc_true_extreme_exit_state"]["pending"]["reason"] == (
        "EXIT_BY_TRUE_VALLEY_EXTREME_REVERSAL"
    )

    account.channel_exit_frame_provider = lambda _symbol: None
    unavailable = asyncio.run(enforce_atr_protection(account, symbol, 10.6))
    assert unavailable is True
    assert len(account.close_attempts) == 2

    account.channel_exit_frame_provider = lambda _symbol: frame
    retried = asyncio.run(enforce_atr_protection(account, symbol, 10.6))
    assert retried is True
    assert len(account.close_attempts) == 3


def test_unconfirmed_valley_does_not_authorize_a_kc_middle_exit(monkeypatch):
    from core.services.exits import peak_trailing_exit

    live_bar_ms = 1_800_000_000_000
    position = _short_position(live_bar_ms)
    frame = _valley_frame(live_bar_ms)
    frame.loc[14, ["open", "high", "low", "close"]] = [
        9.6, 10.9, 9.4, 10.6,
    ]
    account = _Account("LOBSTER/USDT", position, frame, close_results=[True])
    monkeypatch.setattr(
        peak_trailing_exit,
        "evaluate_peak_trailing",
        lambda *_args, **_kwargs: {
            "type": "KC_MIDLINE_CLOSE_BREACH",
            "trigger": "KC_MIDLINE_CLOSE_BREACH",
        },
    )

    closed = asyncio.run(enforce_atr_protection(account, "LOBSTER/USDT", 10.6))

    assert closed is False
    assert account.close_attempts == []


def test_channel_short_closes_on_actual_kc_midline_touch(monkeypatch):
    live_bar_ms = 1_800_000_000_000
    position = _short_position(live_bar_ms)
    frame = _valley_frame(live_bar_ms)
    frame.loc[14, ["open", "high", "low", "close"]] = [
        9.6, 10.9, 9.4, 10.6,
    ]
    monkeypatch.setattr(
        "core.services.exits.entry_atr_protection.time.time",
        lambda: (live_bar_ms + 2_000) / 1_000,
    )
    account = _Account(
        "CAP/USDT", position, frame, close_results=[True],
    )

    closed = asyncio.run(enforce_atr_protection(account, "CAP/USDT", 11.0))

    assert closed is True
    assert len(account.close_attempts) == 1
    assert account.close_attempts[0][2].endswith("EXIT_BY_KC_MIDLINE_RETRACE")
