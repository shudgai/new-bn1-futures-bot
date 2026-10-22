import asyncio

import pandas as pd
import pytest

from core.gates.ma5_ma15_cross_entry_gate import validate_ma5_ma15_cross_entry
from core.services.entry_firewall import EntryFirewall
from core.services.exits import realtime_profit_exit
from core.services.exits.realtime_profit_exit import same_bar_net_profit_exit
from core.services.wait_entry_authority import WaitEntryAuthority
from core.symbol_aliases import (
    is_auto_entry_symbol,
    is_meme_breakout_symbol,
    to_binance_usdm_symbol,
)


def test_only_lobster_is_enabled_for_entry():
    """2026-10-21：只保留 LOBSTER，CAP 已停用自動開倉。"""
    from core.config import DEFAULT_SYMBOLS

    assert is_auto_entry_symbol("LOBSTER/USDT")
    assert is_auto_entry_symbol("龍蝦/USDT:USDT")
    # CAP 已停用 —— is_auto_entry_symbol 應回傳 False
    assert not is_auto_entry_symbol("CAP/USDT")
    assert not is_auto_entry_symbol("CAP/USDT:USDT")
    assert not is_auto_entry_symbol("BTC/USDT")
    assert is_meme_breakout_symbol("CAP/USDT")
    assert "LOBSTER/USDT" in DEFAULT_SYMBOLS
    assert to_binance_usdm_symbol("CAP/USDT") == "CAP/USDT:USDT"
    assert to_binance_usdm_symbol("CAP/USDT:USDT") == "CAP/USDT:USDT"


def test_entry_firewall_rejects_other_symbols_before_signal_evaluation():
    with pytest.raises(ValueError, match="AUTOMATIC_ENTRY_SYMBOL_DISABLED"):
        EntryFirewall.verify_can_open(
            pd.DataFrame(), "LONG", "KC_GOLDEN_CROSS_FAST_LONG",
            symbol="BTC/USDT",
        )


def test_entry_firewall_rejects_cap_now_disabled(monkeypatch):
    """2026-10-21：CAP 已從 auto_entry_symbol 移除，防火牆應拒絕 CAP 開倉。"""
    with pytest.raises(ValueError, match="AUTOMATIC_ENTRY_SYMBOL_DISABLED"):
        EntryFirewall.verify_can_open(
            pd.DataFrame(),
            "LONG",
            "KC_GOLDEN_CROSS_FAST_LONG",
            symbol="CAP/USDT",
        )


def test_wait_authority_keeps_cap_enabled_but_rejects_invalid_market_data(tmp_path):
    authority = WaitEntryAuthority(state_path=tmp_path / "wait-state.json")
    result = authority.observe(
        "CAP/USDT",
        [],
        {},
        1.0,
        quote_fresh=False,
        position_open=False,
        authoritative_position_state=True,
    )
    assert result["status"] == "WAIT_INVALID_MARKET_DATA"


def _cross_frame(side):
    if side == "LONG":
        bars = [
            {
                "timestamp": 60_000, "is_closed": True, "open": 100.0,
                "high": 100.5, "low": 99.8, "close": 100.2,
                "ma5": 99.0, "ma15": 100.0, "kc_trend": "UP",
                "kc_upper": 110.0, "kc_middle": 98.0, "kc_lower": 95.0,
                "atr": 4.0,
            },
            {
                "timestamp": 120_000, "is_closed": True, "open": 99.2,
                "high": 100.3, "low": 99.0, "close": 100.2,
                "ma5": 100.5, "ma15": 100.0, "kc_trend": "UP",
                "kc_upper": 110.0, "kc_middle": 99.0, "kc_lower": 95.0,
                "atr": 4.0,
            },
        ]
        bars.append({
            **bars[-1],
            "timestamp": 180_000,
            "open": 99.8,
            "high": 100.3,
            "low": 99.7,
            "close": 100.2,
            "ma5": 100.6,
            "ma15": 100.05,
            "kc_middle": 99.2,
        })
    else:
        bars = [
            {
                "timestamp": 60_000, "is_closed": True, "open": 102.0,
                "high": 102.5, "low": 101.5, "close": 101.8,
                "ma5": 101.0, "ma15": 100.0, "kc_trend": "DOWN",
                "kc_upper": 105.0, "kc_middle": 102.0, "kc_lower": 90.0,
                "atr": 4.0,
            },
            {
                "timestamp": 120_000, "is_closed": True, "open": 100.8,
                "high": 101.0, "low": 99.9, "close": 100.0,
                "ma5": 99.5, "ma15": 100.0, "kc_trend": "DOWN",
                "kc_upper": 105.0, "kc_middle": 101.0, "kc_lower": 90.0,
                "atr": 4.0,
            },
        ]
        bars[-1]["ma5"] = 100.1
        bars.append({
            **bars[-1],
            "timestamp": 180_000,
            "open": 100.8,
            "high": 101.0,
            "low": 99.9,
            "close": 100.0,
            "ma5": 99.4,
            "ma15": 100.0,
            "kc_middle": 100.8,
        })
    frame = pd.DataFrame(bars)
    frame.attrs["timeframe_ms"] = 60_000
    return frame


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_completed_ma_cross_is_allowed_when_macro_structure_passes(side):
    allowed, reason, _ = validate_ma5_ma15_cross_entry(_cross_frame(side), side)
    assert allowed, reason


def test_ma_cross_does_not_pass_when_macro_kc_position_is_extended():
    frame = _cross_frame("LONG")
    frame.loc[frame.index[-1], "close"] = 106.0
    frame.loc[frame.index[-1], "high"] = 106.1
    frame.loc[:, "atr"] = 20.0
    allowed, reason, _ = validate_ma5_ma15_cross_entry(frame, "LONG")
    assert not allowed
    assert reason == "VETO_MACRO_KC_POSITION_LONG"


@pytest.mark.parametrize(
    ("side", "price", "expected"),
    [
        ("LONG", 100.45, True),
        ("LONG", 100.39, False),
        ("SHORT", 99.55, True),
        ("SHORT", 99.61, False),
    ],
)
def test_same_bar_exit_uses_fee_and_slippage_adjusted_net_threshold(
    side, price, expected,
):
    should_exit, net_rate = same_bar_net_profit_exit(
        {
            "side": side,
            "entry_price": 100.0,
            "open_timestamp": 60.1,
        },
        price,
        61_000,
        {"live_bar_ms": 60_000, "timeframe_ms": 60_000},
        fee_rate=0.0004,
        slippage_pct=0.0001,
    )
    assert should_exit is expected
    assert net_rate is not None


def test_same_bar_exit_does_not_apply_after_entry_candle():
    should_exit, _ = same_bar_net_profit_exit(
        {
            "side": "LONG",
            "entry_price": 100.0,
            "open_timestamp": 60.1,
        },
        101.0,
        120_000,
        {"live_bar_ms": 120_000, "timeframe_ms": 60_000},
        fee_rate=0.0004,
        slippage_pct=0.0001,
    )
    assert not should_exit


def test_same_bar_net_profit_exit_is_suppressed_for_restricted_channel_position(monkeypatch):
    class Account:
        def __init__(self, position):
            self.positions = {"LOBSTER/USDT": position}
            self.position_meta = {}
            self.closed = []

        def log(self, *_args):
            pass

        def save_state(self):
            pass

        async def close_position(self, *args, **kwargs):
            self.closed.append((args, kwargs))
            return True

    class Engine:
        is_running = True

    symbol = "LOBSTER/USDT"
    bar_ms = 1_700_000_000_000 // 60_000 * 60_000
    quote_ms = bar_ms + 2_000
    position = {
        "side": "LONG",
        "open_timestamp": (bar_ms + 1_000) / 1_000,
        "entry_price": 100.0,
        "qty": 1.0,
        "entry_mode": "CHANNEL_SWING",
    }
    account = Account(position)
    engine = Engine()
    engine.account = account
    engine._channel_exit_frames = {
        symbol: pd.DataFrame([{"timestamp": bar_ms, "is_closed": False}]),
    }
    monkeypatch.setattr(
        realtime_profit_exit,
        "cached_tick_indicators",
        lambda *_args: (
            {"live_bar_ms": bar_ms, "timeframe_ms": 60_000},
            1.0,
        ),
    )
    monkeypatch.setattr(
        realtime_profit_exit, "enforce_hard_stop",
        lambda *_args: asyncio.sleep(0, result=False),
    )
    monkeypatch.setattr(
        realtime_profit_exit.time, "time", lambda: quote_ms / 1_000,
    )

    closed = asyncio.run(
        realtime_profit_exit.enforce_realtime_profit_exit(
            engine, symbol, 100.45, quote_ms,
        )
    )
    assert not closed
    assert account.closed == []


def test_lobster_margin_sizing_uses_full_wallet():
    """2026-10-21：1槽模式，保證金使用帳戶餘額 98%（扣手續費緩衝）。"""
    from core.engine import TradingEngine
    import core.config as config

    leverage = 5
    fee_buffer = 1.0 + leverage * config.TAKER_FEE_RATE
    # 1槽時 per_slot_budget = wallet * 0.98，fee_safe = available / fee_buffer * 0.98
    expected_full = min(
        1_000.0 * 0.98,
        (1_000.0 / fee_buffer) * 0.98,
        1_000.0 * config.WILD_SWING_SYMBOL_MARGIN_FRACTION,
    )
    assert TradingEngine._full_wallet_entry_margin(
        1_000.0, 1_000.0, leverage, "LOBSTER/USDT",
    ) == pytest.approx(expected_full)
    # available < wallet 時，受 fee_safe_available 約束
    assert TradingEngine._full_wallet_entry_margin(
        1_000.0, 100.0, leverage, "LOBSTER/USDT",
    ) < 100.0
