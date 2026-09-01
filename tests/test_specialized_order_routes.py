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


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize(
    "requested_mode,pullback_bottom,expected_mode,post_only",
    [
        ("MA5_REVERSAL", False, "MA5_REVERSAL", False),
        ("MA5_BOTTOM_LIMIT", True, "MA5_BOTTOM_LIMIT", True),
        ("MA5_CROSS_PIVOT", False, "MA5_CROSS_PIVOT", False),
    ],
)
async def test_every_ma5_trade_form_routes_to_the_expected_limit_order(
    requested_mode, pullback_bottom, expected_mode, post_only, side,
):
    engine = _base_engine()
    signal = {
        "entry_mode": requested_mode,
        "pullback_bottom_order": pullback_bottom,
        "target_price": 100.0,
        "atr": 1.0,
        "score": 100,
        "reason": f"{requested_mode} routing contract",
    }

    placed = await engine._place_ma5_reversal_entry(
        SYMBOL, side, signal, live_price=100.0, now=1.0,
    )

    assert placed is True
    assert len(engine.account.orders) == 1
    order_type, order = engine.account.orders[0]
    assert order_type == "limit"
    assert order["side"] == side
    assert order["post_only"] is post_only
    assert order["entry_context"]["entry_mode"] == expected_mode
    if requested_mode == "MA5_CROSS_PIVOT":
        assert order["tp"] == 0.0


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_ma3_ma15_continuation_routes_to_market_for_both_sides(side):
    engine = _base_engine()
    frame = pd.DataFrame({
        "low": [99.0] * 20,
        "high": [101.0] * 20,
        "close": [100.0] * 20,
        "atr": [1.0] * 20,
        "kc_middle": [100.0] * 20,
        "kc_upper": [102.0] * 20,
        "kc_lower": [98.0] * 20,
    })

    placed = await engine._place_continuous_market_entry(
        SYMBOL, side, frame, live_price=100.0,
        entry_type=f"TREND_{side}", reason="continuation routing contract",
        score=100, timeframe="1m", wave_regime="TREND",
    )

    assert placed is True
    assert len(engine.account.orders) == 1
    order_type, order = engine.account.orders[0]
    assert order_type == "market"
    assert order["side"] == side
    assert order["entry_context"]["entry_mode"] == "MA3_MA15_MARKET"


@pytest.mark.anyio
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
async def test_current_maker_routes_to_post_only_for_both_sides(side):
    engine = _base_engine()
    candidate = {
        "symbol": SYMBOL,
        "side": side,
        "score": 100,
        "entry_mode": "CURRENT_MAKER",
        "amount_usdt": 50.0,
        "atr": 1.0,
        "reason": "current maker routing contract",
        "leverage": 1,
        "btc_regime_mode": "ALIGNED",
        "btc_direction_1h": 0,
        "btc_score_penalty": 0,
        "btc_allocation_factor": 1.0,
        "btc_pre_penalty_score": 100,
        "raw_signal_score": 100,
        "btc_adjusted_score": 100,
        "history_adjusted_score": 100,
        "history_score_multiplier": 1.0,
    }
    engine.pending_pullback_candidates[SYMBOL] = candidate

    placed = await engine._place_current_maker_candidate(
        SYMBOL, candidate, live_price=100.0, now=1.0,
    )

    assert placed is True
    order_type, order = engine.account.orders[0]
    assert order_type == "limit"
    assert order["side"] == side
    assert order["post_only"] is True
    assert order["entry_context"]["entry_mode"] == "CURRENT_MAKER"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_pullback_candidate_stage_is_created_for_both_sides(monkeypatch, side):
    engine = _base_engine()
    monkeypatch.setattr(engine_module, "DEFAULT_SYMBOLS", [SYMBOL])
    signal = {
        "side": side,
        "target_zone": 100.0,
        "atr": 1.0,
        "reason": "pullback candidate routing contract",
    }

    engine._admit_pullback_candidates(
        [(80, SYMBOL, signal, 100.0, 1.0)],
        available_balance=150.0,
        now=1.0,
    )

    candidate = engine.pending_pullback_candidates[SYMBOL]
    assert candidate["side"] == side
    assert candidate["entry_mode"] == "PULLBACK"
