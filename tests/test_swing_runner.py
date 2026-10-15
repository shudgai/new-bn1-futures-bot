import asyncio

import numpy as np
import pandas as pd

from core.swing.runner import SwingRunner, bars_held, net_roes, process_symbol


def make_frame(n=40, last_close_drop=None):
    ts = 1.7e12 + np.arange(n) * 60000.0
    close = np.full(n, 100.0)
    df = pd.DataFrame({
        "timestamp": ts, "open": close, "high": close + 1, "low": close - 1, "close": close,
        "volume": 100.0, "ma5": 100.0, "ma15": 100.0, "kc_upper": 110.0, "kc_lower": 90.0,
        "atr": 2.0, "is_closed": True,
    })
    df.attrs["timeframe_ms"] = 60000
    return df


class Account:
    def __init__(self, positions=None):
        self.positions = positions or {}
        self.closed = []

    async def close_position(self, symbol, quote, reason, **kw):
        self.closed.append((reason, kw))
        self.positions.pop(symbol, None)
        return True


class Engine:
    def __init__(self, account):
        self.account = account
        self.entries = []

    async def execute_dual_tf_entry(self, **kw):
        self.entries.append(kw)
        return True


def pos(side="LONG", held_bar_ts=None, **kw):
    p = {"side": side, "entry_price": 100.0, "qty": 10.0, "margin": 100.0, "leverage": 10,
         "open_timestamp": held_bar_ts, "highest_price": 100.0, "lowest_price": 100.0}
    p.update(kw)
    return p


def test_dump_flush_closes_long_then_opens_short():
    df = make_frame()
    df.loc[df.index[-1], ["open", "close", "high", "low"]] = [101.0, 90.0, 101.0, 90.0]
    df.loc[df.index[-1], "atr"] = 3.0
    acct = Account({"X": pos(held_bar_ts=df["timestamp"].iloc[-5])})
    eng = Engine(acct)
    asyncio.run(process_symbol(eng, "X", df, 90.0, False, False))
    assert acct.closed and "DUMP_FLUSH_REVERSAL" in acct.closed[0][0]
    assert eng.entries and eng.entries[0]["side"] == "SHORT"


def test_no_action_on_entry_bar():
    df = make_frame()
    df.loc[df.index[-1], ["open", "close"]] = [101.0, 90.0]
    df.loc[df.index[-1], "atr"] = 3.0
    acct = Account({"X": pos(held_bar_ts=df["timestamp"].iloc[-1] + 61000)})
    eng = Engine(acct)
    asyncio.run(process_symbol(eng, "X", df, 90.0, False, False))
    assert not acct.closed and not eng.entries


def test_bars_held_counts_closed_bars_from_entry_bar():
    df = make_frame()
    assert bars_held({"open_timestamp": df["timestamp"].iloc[-1] + 61000}, df) == 0
    assert bars_held({"open_timestamp": df["timestamp"].iloc[-3] + 10}, df) == 3


def test_flat_market_is_idle():
    eng = Engine(Account())
    asyncio.run(process_symbol(eng, "X", make_frame(), 100.0, False, False))
    assert not eng.entries


def test_ratchet_limit_close_when_below_floor():
    df = make_frame()
    p = pos(held_bar_ts=df["timestamp"].iloc[-5], swing_peak_roe=1000.0)
    p["swing_peak_roe"] = 12.0
    acct = Account({"X": p})
    asyncio.run(process_symbol(Engine(acct), "X", df, 100.0, False, False))
    assert acct.closed and acct.closed[0][1]["is_limit"] and "RATCHET" in acct.closed[0][0]


def test_net_roes_persists_peak():
    p = pos(highest_price=110.0)
    peak, cur = net_roes(p, 100.0)
    assert peak > cur and p["swing_peak_roe"] == peak


def test_firewall_revalidates_swing_breakout_and_rejects_stale():
    import pytest
    from core.services.entry_firewall import validate_account_entry

    df = make_frame()
    df.loc[df.index[-1], ["open", "close", "high", "low"]] = [108.0, 113.0, 113.2, 107.8]
    df.loc[df.index[-2], ["open", "close", "high", "low"]] = [110.5, 111.0, 111.2, 110.4]  # prior bar already outside
    df.loc[df.index[-1], "volume"] = 250.0
    df.loc[df.index[-1], "atr"] = 3.0
    df.loc[df.index[-1], "ma5"] = 101.0
    df.attrs["entry_finality_verified"] = True
    bar_id = float(df["timestamp"].iloc[-1])

    class Acct:
        positions = {}

        async def entry_frame_provider(self, symbol):
            return df

    ctx = {"entry_mode": "WILD_SWING", "source": "WILD_SWING_1M",
           "entry_signal_code": "SWING_BREAKOUT_MOMENTUM",
           "channel_confirmation_bar_id": bar_id, "dual_tf_meta": {"bar_id": bar_id}}
    out = asyncio.run(validate_account_entry(Acct(), "X/USDT", "LONG", ctx))
    assert out["type"] == "SWING_BREAKOUT_MOMENTUM"
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(Acct(), "X/USDT", "SHORT", ctx))
    stale = dict(ctx, dual_tf_meta={"bar_id": bar_id - 60000})
    with pytest.raises(ValueError):
        asyncio.run(validate_account_entry(Acct(), "X/USDT", "LONG", stale))


def test_bar_status_line_one_line_summary():
    from core.swing.models import Bar
    from core.swing.runner import bar_status_line
    b = Bar(100, 101, 99, 100.5, 10, 101.0, 100.0, 110.0, 90.0, 2.0)
    line = bar_status_line("X", b, None, "N/A")
    assert "\n" not in line and "MA5>MA15" in line and "INSIDE" in line and "signal=NONE" in line
    b2 = Bar(100, 112, 99, 111, 10, 101.0, 100.0, 110.0, 90.0, 2.0)
    assert "ABOVE_UPPER" in bar_status_line("X", b2, None, "N/A")


def test_process_symbol_logs_once_per_bar(caplog):
    import logging
    df = make_frame()
    eng = Engine(Account())
    with caplog.at_level(logging.INFO, logger="uvicorn.error"):
        asyncio.run(process_symbol(eng, "X", df, 100.0, False, False))
        asyncio.run(process_symbol(eng, "X", df, 100.0, False, False))
    assert sum("SWING_BAR X" in r.getMessage() for r in caplog.records) == 1
