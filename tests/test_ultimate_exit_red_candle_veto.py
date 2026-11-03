"""Owner rules for meme structure exits (SHORT and LONG mirrored).

- A SHORT may never be closed on a red/doji completed 1m bar; LONG mirrors with green/doji.
- The MA5/MA15 cross must be fresh: MA5 crosses MA15 on the evaluated completed bar.

Bars reproduce 1000PEPE 2026-11-03 06:29/06:30 UTC (14:29/14:30 Taipei).
"""

import time

import pandas as pd
import pytest

from core.gates.ultimate_short_exit_gatekeeper import UltimateShortExitGatekeeper

UPPER, MIDDLE = 0.004698415, 0.004690765
ATR = (UPPER - MIDDLE) / 1.5
STALE_MA5, FRESH_PREV_MA5 = 0.00469116, 0.00468600


def mirror(value, side):
    return value if side == "SHORT" else 2 * MIDDLE - value


def frame(side, opened, close, prev_ma5):
    now_minute = int(time.time() // 60)
    m = lambda v: mirror(v, side)  # noqa: E731
    rows = [
        dict(timestamp=(now_minute - 2) * 60_000, open=m(0.0046905), close=m(0.0046925),
             ma5=m(prev_ma5), ma15=m(0.004686533), kc_middle=m(0.004690014)),
        dict(timestamp=(now_minute - 1) * 60_000, open=m(opened), close=m(close),
             ma5=m(0.00469292), ma15=m(0.004686927), kc_middle=MIDDLE),
    ]
    for row in rows:
        row.update(high=max(row["open"], row["close"]) + 1e-7, low=min(row["open"], row["close"]) - 1e-7,
                   kc_upper=row["kc_middle"] + 1.5 * ATR, kc_lower=row["kc_middle"] - 1.5 * ATR,
                   atr=ATR, is_closed=True)
    result = pd.DataFrame(rows)
    result.attrs.update(timeframe_ms=60_000, snapshot_ms=time.time() * 1000)
    return result


def verdict(side, opened, close, prev_ma5=FRESH_PREV_MA5):
    position = {"side": side, "entry_price": mirror(0.00470653, side), "qty": 1,
                "open_timestamp": time.time() - 40 * 60}
    price = mirror(close, side)
    return UltimateShortExitGatekeeper.evaluate(
        "1000PEPE/USDT", position, {}, "STRUCTURE_EXIT_PROBE", price,
        frame=frame(side, opened, close, prev_ma5), is_bar_closed=True,
    )


@pytest.mark.parametrize("side", ["SHORT", "LONG"])
def test_fresh_cross_on_reversal_body_beyond_buffer_authorizes_exit(side):
    result = verdict(side, 0.0046926, 0.0046979)
    assert result.verdict == "ALLOW"
    assert result.code == f"EXIT_{side}_STRUCTURE_BREAK_050ATR"


@pytest.mark.parametrize("side", ["SHORT", "LONG"])
def test_stale_cross_holds_even_on_reversal_body_beyond_buffer(side):
    # PEPE 14:30: MA5 had already been above MA15 since 14:27, so no fresh cross.
    result = verdict(side, 0.0046926, 0.0046979, prev_ma5=STALE_MA5)
    assert result.verdict == "HOLD"
    assert result.code == f"VETO_HOLD_{side}_MA5_MA15_NOT_CROSSED"


@pytest.mark.parametrize("side", ["SHORT", "LONG"])
@pytest.mark.parametrize("opened,close", [
    (0.0046990, 0.0046979),   # position-direction body (red for SHORT), beyond buffer
    (0.0046979, 0.0046979),   # flat doji
])
def test_position_direction_or_doji_bar_always_holds(side, opened, close):
    result = verdict(side, opened, close)
    assert result.verdict == "HOLD"
    assert result.code == f"VETO_HOLD_{side}_OPPOSITE_BODY_REQUIRED"
