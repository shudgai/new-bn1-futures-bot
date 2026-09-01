import pytest

from core.engine import TradingEngine


STRUCTURED_ORDER_CASES = [
    ("BREAKOUT", "ENTER_LIMIT"),
    ("BREAKOUT", "ENTER_MARKET"),
    ("CHANNEL_SWING", "ENTER_MARKET"),
    ("EXHAUSTION_SNIPER", "ENTER_MARKET"),
    ("MA3_PIVOT", "ENTER_MARKET"),
    ("MOMENTUM_CROSS", "ENTER_MARKET"),
    ("PIVOT_TURN", "ENTER_MARKET"),
    ("SHORT_FAST_ENTRY", "ENTER_MARKET"),
    ("STRONG_LONG_BURST", "ENTER_MARKET"),
    ("SUPPORT_PULLBACK", "ENTER_LIMIT"),
]


class _Account:
    def __init__(self):
        self.positions = {}
        self.pending_limit_orders = {}
        self.orders = []
        self.logs = []

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
    def get_stop_cooldown_remaining(*_args):
        return 0.0

    @staticmethod
    def get_dynamic_leverage(*_args):
        return 1


def _engine():
    engine = object.__new__(TradingEngine)
    engine.account = _Account()
    engine.symbol_rotation = _Rotation()
    engine.btc_1h_st_direction = 0
    engine._same_side_entry_allowed = lambda *_args, **_kwargs: True
    engine._ma2_confirmation_allowed = lambda *_args, **_kwargs: True
    engine._entry_direction_allowed = lambda *_args, **_kwargs: True
    engine._continuous_entry_amount = lambda: 120.0

    async def execution_price_is_safe(*_args, **_kwargs):
        return True

    engine._execution_price_is_safe = execution_price_is_safe
    return engine


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("entry_mode,action", STRUCTURED_ORDER_CASES)
async def test_every_structured_trade_mode_uses_the_expected_order_route(
    entry_mode, action, side,
):
    engine = _engine()
    signal = {
        "action": action,
        "entry_mode": entry_mode,
        "side": side,
        "score": 100,
        "target_price": 100.0 if action == "ENTER_LIMIT" else None,
        "atr": 1.0,
        "signal_candle_low": 99.0,
        "signal_candle_high": 101.0,
        "profit_profile": "TREND_EXTENSION",
        "reason": f"{entry_mode} routing contract",
    }

    placed = await engine._place_structured_entry(
        "MODE/USDT", signal, live_price=100.0,
    )

    assert placed is True
    assert len(engine.account.orders) == 1
    order_type, order = engine.account.orders[0]
    assert order_type == ("limit" if action == "ENTER_LIMIT" else "market")
    assert order["side"] == side
    assert order["entry_context"]["entry_mode"] == entry_mode
    if entry_mode == "CHANNEL_SWING":
        assert order["sl"] == order["tp"] == 0.0
    elif side == "LONG":
        assert order["sl"] < 100.0
    else:
        assert order["sl"] > 100.0
