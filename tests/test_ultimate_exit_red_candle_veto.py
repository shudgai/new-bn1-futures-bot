"""Owner rules for meme structure exits (SHORT and LONG mirrored).

- A SHORT may never be closed on a red/doji completed 1m bar; LONG mirrors with green/doji.
- The MA5/MA15 cross must be fresh: MA5 crosses MA15 on the evaluated completed bar.

Bars reproduce 1000PEPE 2026-11-03 06:29/06:30 UTC (14:29/14:30 Taipei).
"""

import time

import pandas as pd
import pytest

from core.gates.ultimate_short_exit_gatekeeper import (
    KC_FLIP_EXIT_REASONS,
    UltimateShortExitGatekeeper,
)

UPPER, MIDDLE = 0.004698415, 0.004690765
ATR = (UPPER - MIDDLE) / 1.5
STALE_MA5, FRESH_PREV_MA5 = 0.00469116, 0.00468600


def mirror(value, side):
    return value if side == "SHORT" else 2 * MIDDLE - value


FLIPPED_PREV_MID, ALIGNED_PREV_MID = 0.004690014, 0.004691500
# Two older KC middles (oldest first) that keep rising into FLIPPED_PREV_MID: a 3-bar turn
# against a SHORT (mirrored for LONG).
TURN_HISTORY = (0.004688600, 0.004689300)


def frame(side, opened, close, prev_ma5, prev_mid=FLIPPED_PREV_MID, older_mids=(), ma5=0.00469292):
    now_minute = int(time.time() // 60)
    m = lambda v: mirror(v, side)  # noqa: E731
    rows = [
        dict(timestamp=(now_minute - 2 - len(older_mids) + i) * 60_000, open=m(0.0046905),
             close=m(0.0046925), ma5=m(prev_ma5), ma15=m(0.004686533), kc_middle=m(mid))
        for i, mid in enumerate(older_mids)
    ] + [
        dict(timestamp=(now_minute - 2) * 60_000, open=m(0.0046905), close=m(0.0046925),
             ma5=m(prev_ma5), ma15=m(0.004686533), kc_middle=m(prev_mid)),
        dict(timestamp=(now_minute - 1) * 60_000, open=m(opened), close=m(close),
             ma5=m(ma5), ma15=m(0.004686927), kc_middle=MIDDLE),
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
    assert result.code == f"EXIT_{side}_KC_MIDDLE_MA15_BREAK"


@pytest.mark.parametrize("side", ["SHORT", "LONG"])
def test_structure_break_does_not_require_half_atr_or_fresh_ma_cross(side):
    # A reversal close beyond both structure levels is sufficient even below the
    # former 0.50 ATR buffer and when MA5 crossed before the current bar.
    result = verdict(side, 0.0046910, 0.0046920, prev_ma5=STALE_MA5)
    assert result.verdict == "ALLOW"
    assert result.code == f"EXIT_{side}_KC_MIDDLE_MA15_BREAK"
    position = {
        "side": side,
        "entry_price": mirror(0.00470653, side),
        "qty": 1,
        "open_timestamp": time.time() - 40 * 60,
    }
    assert UltimateShortExitGatekeeper.can_execute_exit(
        "1000PEPE/USDT",
        position,
        {},
        result.code,
        mirror(0.0046920, side),
        frame=frame(side, 0.0046910, 0.0046920, STALE_MA5),
        is_bar_closed=True,
    )


@pytest.mark.parametrize("side", ["SHORT", "LONG"])
def test_structure_break_must_close_beyond_both_kc_middle_and_ma15(side):
    result = verdict(side, 0.0046895, 0.0046905)
    assert result.verdict == "HOLD"
    assert result.code == f"VETO_HOLD_{side}_KC_MIDDLE_MA15_NOT_BROKEN"


@pytest.mark.parametrize("side", ["SHORT", "LONG"])
@pytest.mark.parametrize("opened,close", [
    (0.0046990, 0.0046979),   # position-direction body (red for SHORT), beyond buffer
    (0.0046979, 0.0046979),   # flat doji
])
def test_position_direction_or_doji_bar_always_holds(side, opened, close):
    result = verdict(side, opened, close)
    assert result.verdict == "HOLD"
    assert result.code == f"VETO_HOLD_{side}_OPPOSITE_BODY_REQUIRED"


@pytest.mark.parametrize("side", ["SHORT", "LONG"])
def test_structure_break_still_requires_five_completed_bars(side):
    result = shield_verdict(
        side,
        "STRUCTURE_EXIT_PROBE",
        0.0,
        minutes_held=3,
    )
    assert result.verdict == "HOLD"
    assert result.code == "VETO_HOLD_MIN_5_COMPLETED_BARS"


def shield_verdict(side, reason, entry_offset_atr, close=0.0046979, prev_mid=ALIGNED_PREV_MID,
                   minutes_held=40, older_mids=(), ma5=0.00469292):
    # entry_offset_atr > 0 puts the entry on the profitable side of the quote.
    sign = 1 if side == "LONG" else -1
    price = mirror(close, side)
    position = {"side": side, "entry_price": price - sign * entry_offset_atr * ATR, "qty": 1,
                "open_timestamp": time.time() - minutes_held * 60}
    return UltimateShortExitGatekeeper.evaluate(
        "1000PEPE/USDT", position, {}, reason, price,
        frame=frame(side, 0.0046926, close, FRESH_PREV_MA5, prev_mid, older_mids, ma5),
        is_bar_closed=True,
    )


@pytest.mark.parametrize("side", ["SHORT", "LONG"])
@pytest.mark.parametrize("reason", [
    "EXIT_BY_EXTREME_WATERFALL",
    "EXIT_SHORT_DISASTER_CLOSED_ABOVE_KC_UPPER", "EXIT_LONG_DISASTER_CLOSED_BELOW_KC_LOWER",
])
@pytest.mark.parametrize("offset", [-0.999, -0.5, 0.0, 0.3, 0.999])
def test_cost_shield_holds_strategy_and_waterfall_exits_near_entry(side, reason, offset):
    # 1000PEPE 2026-11-03 17:24: waterfall bar while quote was still at cost.
    result = shield_verdict(side, reason, offset)
    assert result.verdict == "HOLD"
    expected = (
        "VETO_WATERFALL_LIVE_BODY_OR_STRUCTURE_NOT_CONFIRMED"
        if reason == "EXIT_BY_EXTREME_WATERFALL"
        else f"VETO_HOLD_{side}_COST_SHIELD"
    )
    assert result.code == expected


@pytest.mark.parametrize("side", ["SHORT", "LONG"])
@pytest.mark.parametrize("offset", [-1.01, 1.01])
def test_cost_shield_releases_beyond_one_atr(side, offset):
    result = shield_verdict(side, "STRUCTURE_EXIT_PROBE", offset)
    assert result.code != f"VETO_HOLD_{side}_COST_SHIELD"
    assert result.verdict == "ALLOW"
    waterfall = shield_verdict(side, "EXIT_BY_EXTREME_WATERFALL", offset)
    assert waterfall.verdict == "HOLD"
    assert waterfall.code == "VETO_WATERFALL_LIVE_BODY_OR_STRUCTURE_NOT_CONFIRMED"


@pytest.mark.parametrize("side", ["SHORT", "LONG"])
def test_cost_shield_never_blocks_three_atr_disaster_loss(side):
    result = shield_verdict(side, "STRUCTURE_EXIT_PROBE", -3.01)
    assert result.verdict == "ALLOW"
    assert result.code == f"EXIT_{side}_DISASTER_LOSS_3ATR"


# Owner 2026-11-03 "回踩不平倉，開倉後就死抱到轉向": inside the cost shield only a confirmed
# turn releases: KC middle against the position on 3 consecutive completed bars, MA5 beyond
# MA15 against the position and the completed close across the middle.
@pytest.mark.parametrize("side", ["SHORT", "LONG"])
@pytest.mark.parametrize("offset", [-0.999, 0.0, 0.999])
def test_kc_flip_with_close_across_middle_releases_cost_shield(side, offset):
    result = shield_verdict(side, KC_FLIP_EXIT_REASONS[side], offset, prev_mid=FLIPPED_PREV_MID,
                            older_mids=TURN_HISTORY)
    assert result.verdict == "ALLOW"
    assert result.code == f"EXIT_{side}_KC_FLIP_CLOSED_{'BELOW' if side == 'LONG' else 'ABOVE'}_MIDDLE"
    assert UltimateShortExitGatekeeper.can_execute_exit(
        "1000PEPE/USDT", {"side": side, "entry_price": mirror(0.0046979, side), "qty": 1,
                          "open_timestamp": time.time() - 40 * 60},
        {}, result.code, mirror(0.0046979, side),
        frame=frame(side, 0.0046926, 0.0046979, FRESH_PREV_MA5, older_mids=TURN_HISTORY),
        is_bar_closed=True)


TURN = dict(prev_mid=FLIPPED_PREV_MID, older_mids=TURN_HISTORY)


@pytest.mark.parametrize("side", ["SHORT", "LONG"])
@pytest.mark.parametrize("kwargs", [
    dict(prev_mid=MIDDLE, older_mids=TURN_HISTORY),    # flat latest middle is not a turn
    dict(prev_mid=ALIGNED_PREV_MID, older_mids=TURN_HISTORY),  # middle still with the position
    dict(prev_mid=FLIPPED_PREV_MID),                   # 1-bar turn only: a pullback
    dict(prev_mid=FLIPPED_PREV_MID, older_mids=(0.004688600, 0.004690500)),  # 2-bar turn only
    dict(prev_mid=FLIPPED_PREV_MID, older_mids=(0.004689300, 0.004689300)),  # flat 3rd bar back
    dict(TURN, ma5=0.0046860),                         # MA5 still on the position side of MA15
    dict(TURN, close=0.0046900),                       # turned but close still on position side
    dict(TURN, minutes_held=3),                        # inside the 5-bar protection
])
def test_kc_flip_requires_turn_close_across_and_min_bars(side, kwargs):
    result = shield_verdict(side, KC_FLIP_EXIT_REASONS[side], 0.0, **kwargs)
    assert result.verdict == "HOLD"
    expected_reason = (
        "VETO_HOLD_MIN_5_COMPLETED_BARS"
        if kwargs.get("minutes_held", 40) < 5
        else f"VETO_HOLD_{side}_COST_SHIELD"
    )
    assert result.code == expected_reason


@pytest.mark.parametrize("side", ["SHORT", "LONG"])
@pytest.mark.parametrize("reason", ["EXIT_BY_EXTREME_WATERFALL",
                                    "EXIT_SHORT_DISASTER_CLOSED_ABOVE_KC_UPPER"])
def test_kc_flip_does_not_unlock_other_reasons(side, reason):
    result = shield_verdict(side, reason, 0.0, **TURN)
    assert result.verdict == "HOLD"


@pytest.mark.parametrize("side", ["SHORT", "LONG"])
def test_kc_flip_waits_for_closed_bar(side):
    price = mirror(0.0046979, side)
    result = UltimateShortExitGatekeeper.evaluate(
        "1000PEPE/USDT", {"side": side, "entry_price": price, "qty": 1,
                          "open_timestamp": time.time() - 40 * 60},
        {}, "STRUCTURE_EXIT_PROBE", price,
        frame=frame(side, 0.0046926, 0.0046979, FRESH_PREV_MA5, older_mids=TURN_HISTORY),
        is_bar_closed=False)
    assert result.code == "VETO_EXIT_BAR_NOT_CLOSED"


@pytest.mark.parametrize("side", ["SHORT", "LONG"])
def test_final_exit_boundary_allows_only_a_fresh_closed_opposite_rail_disaster(side):
    from core.services.exits.authorized_position_exit import automated_position_exit_allowed

    close = 0.004699
    price = mirror(close, side)
    position = {
        "side": side,
        "entry_price": mirror(0.004710, side),
        "qty": 1,
        "open_timestamp": time.time() - 40 * 60,
    }
    data = frame(side, 0.004698, close, FRESH_PREV_MA5)
    assert automated_position_exit_allowed(
        "1000PEPE/USDT", position, {}, "OPPOSITE_KC_BAND_BREACH",
        price, frame=data,
    )


@pytest.mark.parametrize("side", ["SHORT", "LONG"])
@pytest.mark.parametrize("reason", ["EXIT_BY_PULLBACK", "REALTIME_PULLBACK_EXIT"])
def test_ordinary_pullback_exit_is_explicitly_vetoed_until_structure_break(side, reason):
    from core.services.exits.authorized_position_exit import automated_position_exit_allowed

    symbol = "龙虾/USDT"
    position = {
        "side": side,
        "entry_price": 0.0168,
        "qty": 1.0,
        "open_timestamp": time.time() - 600,
    }
    verdict = UltimateShortExitGatekeeper.evaluate(
        symbol, position, {}, reason, position["entry_price"],
    )
    assert verdict.verdict == "HOLD"
    assert verdict.code == "VETO_HOLD_ORDINARY_PULLBACK_UNTIL_STRUCTURE_BREAK"
    assert not automated_position_exit_allowed(
        symbol, position, {}, reason, position["entry_price"],
    )


@pytest.mark.parametrize("side", ["SHORT", "LONG"])
def test_wick_beyond_structure_levels_does_not_count_without_close_break(side):
    sign = 1 if side == "LONG" else -1
    data = frame(side, 0.0046926, 0.0046979, FRESH_PREV_MA5)
    current = data.index[-1]
    middle = float(data.loc[current, "kc_middle"])
    ma15 = float(data.loc[current, "ma15"])
    close = max(middle, ma15) + 1e-7 if side == "LONG" else min(middle, ma15) - 1e-7
    opening = close + 1e-7 if side == "LONG" else close - 1e-7
    data.loc[current, ["open", "close"]] = [opening, close]
    if side == "LONG":
        data.loc[current, "low"] = min(middle, ma15) - 1e-5
    else:
        data.loc[current, "high"] = max(middle, ma15) + 1e-5

    entry = close - sign * 2 * ATR
    position = {
        "side": side,
        "entry_price": entry,
        "qty": 1,
        "open_timestamp": time.time() - 40 * 60,
    }
    result = UltimateShortExitGatekeeper.evaluate(
        "1000PEPE/USDT", position, {}, "STRUCTURE_EXIT_PROBE", close,
        frame=data, is_bar_closed=True,
    )

    assert result.verdict == "HOLD"
    assert result.code == f"VETO_HOLD_{side}_KC_MIDDLE_MA15_NOT_BROKEN"
