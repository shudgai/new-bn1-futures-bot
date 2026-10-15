import pandas as pd
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








