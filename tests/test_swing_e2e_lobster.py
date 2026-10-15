"""End-to-end swing tests: real TradingEngine + PaperAccount + entry firewall, symbol 龙虾/USDT."""
import asyncio
import time

import numpy as np
import pandas as pd
import pytest

import core.paper_account as pa
from core.swing.runner import process_symbol

SYM = "龙虾/USDT"
N = 40


def build(overrides=None, n=N, ma15_slope=0.0, close_off=0.0, ma5_off=0.0, forming=True):
    """n closed bars (+1 forming). ma15 = KC midline, band half-width 4."""
    now = int(time.time() // 60 * 60000)
    total = n + 1
    ts = now - (total - 1) * 60000 + np.arange(total) * 60000.0
    mid = 100.0 + ma15_slope * np.arange(total)
    close = mid + close_off
    body = 0.3 if close_off >= 0 else -0.3  # real bodies so the wick filter sees a normal market
    opn = close - body
    df = pd.DataFrame({
        "timestamp": ts, "open": opn, "high": np.maximum(opn, close) + 0.1, "low": np.minimum(opn, close) - 0.1,
        "close": close,
        "volume": 100.0, "ma5": mid + ma5_off, "ma15": mid, "kc_upper": mid + 4.0,
        "kc_lower": mid - 4.0, "atr": 1.0, "is_closed": True,
    })
    for idx, vals in (overrides or {}).items():
        for k, v in vals.items():
            df.loc[idx if idx >= 0 else total + idx, k] = v
    df.loc[total - 1, "is_closed"] = not forming
    df.attrs["timeframe_ms"] = 60000
    return df


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setattr(pa, "STATE_FILE", str(tmp_path / "paper.json"))
    from core.engine import TradingEngine
    e = TradingEngine()
    e.is_running = True
    return e


def run(eng, df, quote, halt=False):
    def provider_frame():
        x = df.copy()
        x.attrs["entry_finality_verified"] = True
        return x

    async def provider(symbol):
        return provider_frame()

    eng.account.entry_frame_provider = provider
    eng.tickers = {SYM: quote}
    return asyncio.run(process_symbol(eng, SYM, df, quote, halt, False))


def side(eng):
    p = eng.account.positions.get(SYM)
    return p["side"] if p else None


# ---------------- Mode 1: MA5/MA15 swing ----------------
def ma_cross_frame(direction):
    n = N
    if direction == "LONG":
        df = build(ma15_slope=0.05, close_off=0.4, ma5_off=-0.1)
        df.loc[N - 1, "ma5"] = df.loc[N - 1, "ma15"] + 0.1  # last closed bar crosses up
    else:
        df = build(ma15_slope=-0.05, close_off=-0.4, ma5_off=0.1)
        df.loc[N - 1, "ma5"] = df.loc[N - 1, "ma15"] - 0.1
    return df


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_ma_cross_opens_real_position(eng, direction):
    df = ma_cross_frame(direction)
    run(eng, df, float(df.loc[N - 1, "close"]))
    assert side(eng) == direction
    assert eng.account.positions[SYM]["entry_mode"] == "WILD_SWING"


def test_ma_cross_flat_slope_rejected(eng):
    df = ma_cross_frame("LONG")
    df["ma15"] = 100.0
    df["kc_upper"], df["kc_lower"] = 104.0, 96.0
    df.loc[N - 1, "ma5"] = 100.1
    run(eng, df, 100.4)
    assert side(eng) is None


def test_ma_cross_whipsaw_rejected(eng):
    df = ma_cross_frame("LONG")
    for i, off in zip(range(N - 8, N - 1), [-0.4, 0.4, -0.4, 0.4, -0.4, 0.4, -0.4]):
        df.loc[i, "close"] = df.loc[i, "ma15"] + off
    run(eng, df, float(df.loc[N - 1, "close"]))
    assert side(eng) is None


def test_ma_cross_outer_band_chase_rejected(eng):
    df = ma_cross_frame("LONG")
    df.loc[N - 1, "close"] = df.loc[N - 1, "kc_upper"] - 0.5  # kc_pos > 0.8
    run(eng, df, float(df.loc[N - 1, "close"]))
    assert side(eng) is None


def test_ma_cross_poor_position_rejected(eng):
    df = ma_cross_frame("LONG")
    df.loc[N - 1, "close"] = df.loc[N - 1, "ma15"] - 2.0  # kc_pos 0.25 < 0.40
    run(eng, df, float(df.loc[N - 1, "close"]))
    assert side(eng) is None


# ---------------- Mode 2: volume breakout ----------------
@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_breakout_opens_real_position(eng, direction):
    o, c = (100.0, 106.0) if direction == "LONG" else (100.0, 94.0)
    ma5 = 100.5 if direction == "LONG" else 99.5
    prev_c = 105.0 if direction == "LONG" else 95.0  # 2nd bar held outside the band (anti-trap)
    df = build({N - 2: {"close": prev_c, "open": prev_c - (0.3 if direction == "LONG" else -0.3),
                        "high": prev_c + 0.4, "low": prev_c - 0.4},
                N - 1: {"open": o, "close": c, "high": max(o, c) + 0.2, "low": min(o, c) - 0.2,
                        "volume": 300.0, "ma5": ma5}})
    run(eng, df, c)
    assert side(eng) == direction


def test_breakout_without_volume_rejected(eng):
    df = build({N - 1: {"open": 100.0, "close": 106.0, "high": 106.2, "low": 99.8}})
    run(eng, df, 106.0)
    assert side(eng) is None


def test_breakout_needs_double_volume_and_1_5_atr_body(eng):
    df = build({N - 1: {"open": 100.0, "close": 106.0, "high": 106.2, "low": 99.8, "volume": 190.0, "ma5": 100.5}})
    run(eng, df, 106.0)
    assert side(eng) is None  # 1.9x < 2.0x


def test_breakout_small_body_rejected(eng):
    df = build({N - 1: {"open": 104.5, "close": 105.0, "high": 105.1, "low": 104.4, "volume": 300.0}})
    run(eng, df, 105.0)
    assert side(eng) is None


# ---------------- Open then hold: helpers ----------------
def open_long_by_breakout(eng):
    df = build({N - 2: {"open": 104.7, "close": 105.0, "high": 105.4, "low": 104.6},  # 2nd bar outside (anti-trap)
                N - 1: {"open": 100.0, "close": 106.0, "high": 106.2, "low": 99.8, "volume": 300.0, "ma5": 100.5}})
    run(eng, df, 106.0)
    assert side(eng) == "LONG"
    pos = eng.account.positions[SYM]
    entry_bar_ts = float(df.loc[N, "timestamp"])  # forming bar when opened
    pos["open_timestamp"] = entry_bar_ts
    return pos, df


def held_frame(df, extra):
    """Entry bar closes, then `extra` closed bars (dicts), then a forming bar."""
    base = df.iloc[:N].copy()  # closed bars up to the breakout bar
    rows = [dict(df.iloc[N])] + extra
    last_ts = float(base["timestamp"].iloc[-1])
    out = []
    for i, r in enumerate(rows):
        r = dict(r)
        r.update({"timestamp": last_ts + 60000.0 * (i + 1), "is_closed": True})
        out.append(r)
    forming = dict(out[-1])
    forming.update({"timestamp": forming["timestamp"] + 60000.0, "is_closed": False})
    full = pd.concat([base, pd.DataFrame(out + [forming])], ignore_index=True)
    full.attrs["timeframe_ms"] = 60000
    return full


def bar(o, c, **kw):
    d = {"open": o, "close": c, "high": max(o, c) + 0.2, "low": min(o, c) - 0.2, "volume": 100.0,
         "ma5": 106.0, "ma15": 103.0, "kc_upper": 107.0, "kc_lower": 99.0, "atr": 1.0}
    d.update(kw)
    return d


# ---------------- Mode 3: dump-flush reversal ----------------
def test_dump_flush_closes_long_and_opens_short(eng):
    pos, df = open_long_by_breakout(eng)
    flush = bar(106.0, 100.0, ma5=105.0, ma15=104.0)  # body 6 > 1.8 ATR, below MA5/MA15
    full = held_frame(df, [bar(106.0, 106.5), flush])
    run(eng, full, 100.0)
    assert side(eng) == "SHORT"
    closes = [t for t in eng.account.trades if "CLOSE" in str(t.get("action", "")).upper()
              or "平" in str(t.get("action", ""))]
    assert closes or any("DUMP_FLUSH" in str(t) for t in eng.account.trades)


def test_dump_flush_not_on_entry_bar(eng):
    pos, df = open_long_by_breakout(eng)
    run(eng, df, 100.0)  # same bar still forming: bars_held == 0
    assert side(eng) == "LONG"


# ---------------- Ratchet profit lock ----------------
def roe_quote(pos, roe_pct):
    return pos["entry_price"] * (1 + roe_pct / 100.0 / pos["leverage"])


@pytest.mark.parametrize("peak_roe,fall_roe,expect_exit", [
    (12.0, 6.0, False),   # floor 5%
    (12.0, 4.0, True),
    (17.0, 9.0, True),    # floor 10%
    (17.0, 11.0, False),
    (23.0, 14.0, True),   # floor 15%
    (23.0, 16.0, False),
    (6.0, 1.0, True),     # micro band: peak 6% -> floor 2%
    (6.0, 3.0, False),
])
def test_ratchet_lock_end_to_end(eng, peak_roe, fall_roe, expect_exit):
    pos, df = open_long_by_breakout(eng)
    hold = bar(106.0, 106.5, ma5=106.0, ma15=100.0, kc_upper=110.0, kc_lower=90.0)  # above midline
    full = held_frame(df, [hold, hold])
    # Gross ROE target on top of fees: aim net ROE via quote; peak first, then fall.
    fee_buf = 1.5
    run(eng, full, roe_quote(pos, peak_roe + fee_buf))
    assert side(eng) == "LONG"
    run(eng, full, roe_quote(pos, fall_roe + fee_buf))
    assert (side(eng) is None) == expect_exit


def test_below_10_roe_no_ratchet_and_no_exit_above_midline(eng):
    pos, df = open_long_by_breakout(eng)
    hold = bar(106.0, 106.5, ma5=106.0, ma15=100.0, kc_upper=110.0, kc_lower=90.0)
    full = held_frame(df, [hold, hold])
    run(eng, full, roe_quote(pos, 4.0))
    run(eng, full, roe_quote(pos, -3.0))
    assert side(eng) == "LONG"  # no fixed hard stop, no lock before 5%


def test_structure_exit_blocked_inside_breathing_window(eng):
    pos, df = open_long_by_breakout(eng)
    weak = bar(104.5, 103.5, ma5=105.0, ma15=104.0)
    full = held_frame(df, [weak])  # entry bar + weak bar closed => bars_held == 2
    run(eng, full, 103.5)
    assert side(eng) == "LONG"


def test_structure_exit_when_close_below_midline(eng):
    pos, df = open_long_by_breakout(eng)
    weak = bar(104.5, 103.5, ma5=105.0, ma15=104.0)  # body 1.0 < 1.8 ATR: no flush, close under ma15
    full = held_frame(df, [bar(106.0, 106.3), weak])
    run(eng, full, 103.5)
    assert side(eng) is None
