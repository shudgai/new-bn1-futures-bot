import pytest

import core.engine as engine_module
from core.engine import TradingEngine


SYMBOL = "MODE/USDT"


class _Account:
    def __init__(self):
        self.positions = {}
        self.pending_limit_orders = {}
        self.orders = []
        self.logs = []
        self.pullback_outcome_stats = {}

    def get_available_balance(self):
        return 150.0

    def get_wallet_balance(self):
        return 150.0

    def log(self, text, level):
        self.logs.append((text, level))

    async def open_position(self, **kwargs):
        self.orders.append(("market", kwargs))
        return True

    async def place_limit_entry(self, **kwargs):
        self.orders.append(("limit", kwargs))
        return True


class _Rotation:
    @staticmethod
    def get_dynamic_leverage(*_args, **_kwargs):
        return 1


def _base_engine():
    engine = object.__new__(TradingEngine)
    engine.account = _Account()
    engine.symbol_rotation = _Rotation()
    engine.exchange = object()
    engine.pending_pullback_candidates = {}
    engine._pullback_retry_after = {}
    engine._same_side_entry_allowed = lambda *_args, **_kwargs: True
    engine._ma2_confirmation_allowed = lambda *_args, **_kwargs: True
    engine._ma5_stop_cooldown_remaining = lambda *_args, **_kwargs: 0.0
    engine._entry_direction_allowed = lambda *_args, **_kwargs: True
    engine._record_pullback_outcome = lambda *_args, **_kwargs: None

    async def execution_price_is_safe(*_args, **_kwargs):
        return True

    engine._execution_price_is_safe = execution_price_is_safe
    return engine


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("mode,retired_method", [
    ("MA5_REVERSAL", "_place_ma5_reversal_entry"),
    ("MA5_BOTTOM_LIMIT", "_place_ma5_reversal_entry"),
    ("MA5_CROSS_PIVOT", "_place_ma5_reversal_entry"),
    ("MA3_MA15_MARKET", "_place_continuous_market_entry"),
    ("CURRENT_MAKER", "_place_current_maker_candidate"),
    ("PULLBACK", "_admit_pullback_candidates"),
])
async def test_retired_specialized_routes_cannot_place_orders(monkeypatch, side, mode, retired_method):
    engine = _base_engine()
    monkeypatch.setattr(engine_module, "DEFAULT_SYMBOLS", [SYMBOL])
    assert not hasattr(engine, retired_method)
    placed = await engine._place_structured_entry(SYMBOL, {
        "entry_mode": mode, "action": "ENTER_MARKET", "side": side,
        "score": 100, "atr": 1.0, "target_price": 100.0,
    }, live_price=100.0)
    assert placed is False
    assert engine.account.orders == []
