import pandas as pd
import pytest

import core.config as config
from core.engine import TradingEngine


SYMBOL = "TEST/USDT"


def _narrow_channel_frame() -> pd.DataFrame:
    rows = 20
    return pd.DataFrame({
        "open": [100.0] * rows,
        "high": [100.1] * rows,
        "low": [99.9] * rows,
        "close": [100.0] * rows,
        "ma3": [100.0] * rows,
        "ma15": [100.0] * rows,
        "kc_lower": [99.8] * rows,
        "kc_upper": [100.2] * rows,
        "atr": [0.1] * rows,
    })


class _IdentityStrategy:
    @staticmethod
    def compute_indicators(frame):
        return frame


class _Rotation:
    direction_map = {}

    @staticmethod
    def get_dynamic_leverage(_symbol, _score):
        return 1


class _RecordingAccount:
    def __init__(self, side: str, close_succeeds: bool):
        self.positions = {
            SYMBOL: {
                "side": side,
                "entry_mode": "CHANNEL_SWING",
                "channel_turn_low": None,
                "channel_turn_high": None,
            },
        }
        self.position_meta = {SYMBOL: {}}
        self.pending_limit_orders = {}
        self.close_succeeds = close_succeeds
        self.events = []
        self.logs = []

    def log(self, text, level):
        self.logs.append((text, level))

    def get_available_balance(self):
        return 150.0

    async def close_position(
        self, symbol, current_price, close_reason, is_manual=False,
    ):
        self.events.append(("close", symbol, current_price, close_reason))
        if self.close_succeeds:
            self.positions.pop(symbol, None)
            self.position_meta.pop(symbol, None)
        return self.close_succeeds

    async def open_position(self, **kwargs):
        self.events.append(("open", kwargs["symbol"], kwargs["side"], kwargs["price"]))
        self.positions[kwargs["symbol"]] = {
            "side": kwargs["side"],
            "entry_mode": kwargs["entry_context"]["entry_mode"],
        }
        return True


def _execution_engine(frame, side, close_succeeds):
    engine = object.__new__(TradingEngine)
    engine.account = _RecordingAccount(side, close_succeeds)
    engine.strategy = _IdentityStrategy()
    engine.symbol_rotation = _Rotation()
    engine.tickers = {SYMBOL: 100.2 if side == "SHORT" else 99.8}
    engine._channel_swing_last_reverse_bar = {SYMBOL: frame.index[-2]}
    engine._channel_chop_locked = {SYMBOL: True}
    engine._channel_chop_events = {}
    engine._channel_signal_events = {}
    engine._channel_outer_trend_wait = {}
    engine.btc_1h_st_direction = 0

    async def fetch_klines(_symbol, timeframe, limit, **_kwargs):
        assert timeframe == "1m"
        return pd.DataFrame() if limit == 30 else frame.copy()

    async def execution_price_is_safe(_symbol, _side):
        return True

    engine.fetch_klines = fetch_klines
    engine._execution_price_is_safe = execution_price_is_safe
    engine._channel_chop_state = lambda _frame: {
        "detected": True,
        "clear_direction": None,
        "ma_crosses": 3,
        "middle_crosses": 3,
        "efficiency": 0.1,
    }
    engine._record_channel_chop_event = lambda *_args, **_kwargs: None
    engine._record_channel_signal_event = lambda *_args, **_kwargs: None
    engine._directional_trend_quality = lambda *_args, **_kwargs: 1.0
    engine._same_side_entry_allowed = lambda *_args, **_kwargs: True
    engine._continuous_entry_amount = lambda: 120.0
    return engine


@pytest.mark.anyio
@pytest.mark.parametrize(
    "old_side,new_side,expected_reason",
    [
        ("SHORT", "LONG", "upper outer rechase"),
        ("LONG", "SHORT", "lower outer rechase"),
    ],
)
async def test_same_bar_outer_rechase_closes_then_opens_immediately(
    monkeypatch, old_side, new_side, expected_reason,
):
    monkeypatch.setattr(config, "ENABLE_CONTINUOUS_REVERSE_MODE", True)
    frame = _narrow_channel_frame()
    engine = _execution_engine(frame, old_side, close_succeeds=True)

    _, candidates = await engine._process_single_symbol(
        SYMBOL, now_time=1.0, btc_1m_turn=None, daily_halt=False,
    )

    assert SYMBOL not in engine.account.positions
    assert len(candidates) == 1
    assert candidates[0]["side"] == new_side
    assert expected_reason in candidates[0]["reason"]
    assert engine.account.events[0][0] == "close"

    opened = await engine._place_structured_entry(
        SYMBOL, candidates[0], candidates[0]["live_price"],
    )

    assert opened is True
    assert [event[0] for event in engine.account.events] == ["close", "open"]
    assert engine.account.positions[SYMBOL]["side"] == new_side


@pytest.mark.anyio
@pytest.mark.parametrize("old_side", ["LONG", "SHORT"])
async def test_same_bar_outer_rechase_never_opens_when_close_fails(
    monkeypatch, old_side,
):
    monkeypatch.setattr(config, "ENABLE_CONTINUOUS_REVERSE_MODE", True)
    frame = _narrow_channel_frame()
    engine = _execution_engine(frame, old_side, close_succeeds=False)

    _, candidates = await engine._process_single_symbol(
        SYMBOL, now_time=1.0, btc_1m_turn=None, daily_halt=False,
    )

    assert candidates == []
    assert [event[0] for event in engine.account.events] == ["close"]
    assert engine.account.positions[SYMBOL]["side"] == old_side
