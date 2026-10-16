import asyncio
from types import SimpleNamespace

import pandas as pd
import pytest

from core.engine import TradingEngine
from core.services.entry_firewall import validate_account_entry
from test_wild_swing_strategy import _frame


@pytest.fixture(autouse=True)
def approve_final_qwen_check(monkeypatch):
    from core.vision import chart_guard

    async def approve(*args, **kwargs):
        return True, "test approved"

    monkeypatch.setenv("ENABLE_VISION_GUARD", "true")
    monkeypatch.setenv("USE_LOCAL_QWEN", "true")
    monkeypatch.setattr(chart_guard, "evaluate_qwen_entry_vision", approve)


class FakeAccount:
    positions = {}
    closing_lock = set()
    pending_limit_orders = {}

    def __init__(self, frame):
        self.frame = frame
        self.positions = {}
        self.closing_lock = set()
        self.pending_limit_orders = {}
        self.logs = []

    async def entry_frame_provider(self, symbol):
        assert symbol == "LOBSTER/USDT"
        return self.frame.copy()

    async def open_position(self, **kwargs):
        self.entry_context = kwargs["entry_context"]
        return True

    def get_wallet_balance(self):
        return 100.0

    def get_available_balance(self):
        return 100.0

    def log(self, message, level):
        self.logs.append((message, level))


def _context(bar_id):
    return {
        "entry_mode": "WILD_SWING",
        "source": "WILD_SWING_1M",
        "entry_pattern": "CROSSOVER",
        "entry_signal_code": "WILD_SWING_BASELINE_BREAK_AND_MA_CROSS",
        "entry_signal_mode": "CROSSOVER",
        "channel_confirmation_bar_id": bar_id,
        "dual_tf_meta": {"bar_id": bar_id},
    }


def test_wild_swing_firewall_revalidates_closed_signal_and_candle_identity():
    frame = _frame("SHORT")
    frame["atr"] = 2.0
    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["entry_finality_verified"] = True
    bar_id = float(frame.iloc[-1]["timestamp"])

    result = asyncio.run(
        validate_account_entry(
            FakeAccount(frame), "LOBSTER/USDT", "SHORT", _context(bar_id),
        )
    )

    assert result["entry_mode"] == "WILD_SWING"
    assert result["entry_pattern"] == "CROSSOVER"
    assert result["entry_signal_mode"] == "CROSSOVER"
    assert result["side"] == "SHORT"
    assert result["confirmation_bar_id"] == bar_id


@pytest.mark.parametrize(
    "change", [
        "bar_id", "unverified", "timeframe", "mode", "signal_code", "source",
        "missing_pattern",
    ],
)
def test_wild_swing_firewall_rejects_stale_or_unverified_market_data(change):
    frame = _frame("SHORT")
    frame["atr"] = 2.0
    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["entry_finality_verified"] = True
    bar_id = float(frame.iloc[-1]["timestamp"])
    context = _context(bar_id)
    if change == "bar_id":
        context["dual_tf_meta"]["bar_id"] -= 60_000
    elif change == "unverified":
        frame.attrs["entry_finality_verified"] = False
    elif change == "timeframe":
        frame.attrs["timeframe_ms"] = 300_000
    elif change == "mode":
        context["entry_signal_mode"] = "CONTINUATION"
    elif change == "signal_code":
        context["entry_signal_code"] = "WILD_SWING_TREND_CONTINUATION"
    elif change == "source":
        context["source"] = "WILD_SWING_5M"
    else:
        context.pop("entry_pattern")

    with pytest.raises(ValueError, match="WILD_SWING"):
        asyncio.run(
            validate_account_entry(
                FakeAccount(frame), "LOBSTER/USDT", "SHORT", context,
            )
        )


def test_wild_swing_firewall_reruns_mode_specific_macro_gate(monkeypatch):
    from core.gates.macro_candle_structure_gate import MacroCandleStructureGate

    calls = []
    qwen_calls = []

    def veto(self, side, bar, indicators, **kwargs):
        calls.append(kwargs)
        return False, "TEST_MODE_VETO"

    async def qwen(*args, **kwargs):
        qwen_calls.append((args, kwargs))
        return True, "unexpected review"

    from core.vision import chart_guard

    monkeypatch.setattr(chart_guard, "evaluate_qwen_entry_vision", qwen)
    monkeypatch.setattr(MacroCandleStructureGate, "can_open", veto)
    frame = _frame("SHORT")
    frame["atr"] = 2.0
    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["entry_finality_verified"] = True
    bar_id = float(frame.iloc[-1]["timestamp"])

    with pytest.raises(ValueError, match="WILD_SWING_MACRO_GATE: TEST_MODE_VETO"):
        asyncio.run(
            validate_account_entry(
                FakeAccount(frame), "LOBSTER/USDT", "SHORT", _context(bar_id),
            )
        )

    assert calls == [{
        "entry_pattern": "CROSSOVER",
        "structure_confirmation": "MA5_MA15_1M_CROSS",
        "structure_confirmed": True,
        "channel_expanding": False,
    }]
    assert qwen_calls == []


def test_wild_swing_firewall_rejects_without_explicit_qwen_approval(monkeypatch):
    from core.vision import chart_guard

    async def reject(*args, **kwargs):
        return False, "AI_EXPLICIT_APPROVAL_REQUIRED"

    monkeypatch.setattr(chart_guard, "evaluate_qwen_entry_vision", reject)
    frame = _frame("SHORT")
    frame["atr"] = 2.0
    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["entry_finality_verified"] = True
    bar_id = float(frame.iloc[-1]["timestamp"])

    with pytest.raises(ValueError, match="WILD_SWING_QWEN_REJECTED"):
        asyncio.run(
            validate_account_entry(
                FakeAccount(frame), "LOBSTER/USDT", "SHORT", _context(bar_id),
            )
        )


def test_wild_swing_firewall_rejects_unapproved_signal_pattern(monkeypatch):
    from core.services.strategies.wild_swing_strategy import WildSwingStrategy

    frame = _frame("SHORT")
    frame["atr"] = 2.0
    frame.attrs["timeframe_ms"] = 60_000
    frame.attrs["entry_finality_verified"] = True
    bar_id = float(frame.iloc[-1]["timestamp"])
    monkeypatch.setattr(
        WildSwingStrategy,
        "evaluate_entry",
        lambda *args: {
            "side": "SHORT",
            "reason": "UNAPPROVED",
            "entry_pattern": "MA_SWING",
            "entry_signal_mode": "MA_SWING",
            "bar_id": bar_id,
        },
    )

    with pytest.raises(
        ValueError,
        match="REJECT_UNAUTHORIZED_ENTRY_PATTERN",
    ):
        asyncio.run(
            validate_account_entry(
                FakeAccount(frame), "LOBSTER/USDT", "SHORT", _context(bar_id),
            )
        )


def test_engine_passes_signal_mode_into_account_entry_context():
    account = FakeAccount(pd.DataFrame([{"timestamp": 660_000, "atr": 2.0}]))
    engine = SimpleNamespace(
        account=account,
        symbol_rotation=SimpleNamespace(get_dynamic_leverage=lambda *args: 10),
        _full_wallet_entry_margin=lambda wallet, available, leverage: 10.0,
    )
    signal = {
        "bar_id": 660_000,
        "entry_pattern": "BREAKOUT",
        "entry_signal_mode": "BREAKOUT",
    }

    result = asyncio.run(
        TradingEngine.execute_dual_tf_entry(
            engine,
            symbol="LOBSTER/USDT",
            frame=account.frame,
            price=100.0,
            side="LONG",
            mode_name="WILD_SWING_KC_OUTER_BAND_BREAKOUT",
            meta_dict=signal,
            entry_mode="WILD_SWING",
        )
    )

    assert result is True
    assert account.entry_context["entry_pattern"] == "BREAKOUT"
    assert account.entry_context["entry_signal_mode"] == "BREAKOUT"
