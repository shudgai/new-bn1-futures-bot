from types import SimpleNamespace

import pandas as pd
import pytest

from core.services.entry_contract import (
    _post_close_long_opportunity,
    evaluate_entry_contract,
    evaluate_post_close_continuation,
)
from core.services import entry_contract
from core.gates.pipeline import (
    STRATEGY_TYPE_TREND_CONTINUATION,
    entry_market_structure_problem,
    strict_kc_entry_problem,
)


def reentry_frame(*, opportunity="PULLBACK_SUPPORT", wick=None):
    live_stamp = 1_800_000
    rows = []
    for index in range(6):
        rows.append({
            "timestamp": live_stamp - (6 - index) * 60_000,
            "open": 99.9,
            "high": 100.2,
            "low": 99.7,
            "close": 100.0,
            "atr": 1.0,
            "ma5": 100.0 + index * 0.05,
            "ma15": 99.8 + index * 0.03,
            "kc_lower": 97.0,
            "kc_middle": 99.5,
            "kc_upper": 103.0,
            "is_closed": True,
        })

    support, signal = rows[-2], rows[-1]
    support.update(
        open=100.1, high=100.3, low=99.95, close=100.2,
        ma5=100.0, ma15=99.9,
    )
    signal.update(
        open=100.2, high=100.7, low=100.15, close=100.6,
        ma5=100.2, ma15=100.0,
    )
    if opportunity == "MOMENTUM_BREAKOUT":
        support.update(
            open=100.45, high=100.8, low=100.45, close=100.6,
            ma5=100.4, ma15=100.3,
        )
        signal.update(
            open=100.5, high=101.25, low=100.45, close=101.1,
            ma5=100.6, ma15=100.31,
        )
    if wick == "signal":
        signal["high"] = signal["close"] + abs(signal["close"] - signal["open"])

    rows.append({
        "timestamp": live_stamp,
        "open": 100.5 if opportunity == "PULLBACK_SUPPORT" else 101.0,
        "high": 100.9 if opportunity == "PULLBACK_SUPPORT" else 101.15,
        "low": 100.4 if opportunity == "PULLBACK_SUPPORT" else 100.9,
        "close": 100.6 if opportunity == "PULLBACK_SUPPORT" else 101.1,
        "atr": 1.0,
        "ma5": 100.3 if opportunity == "PULLBACK_SUPPORT" else 100.9,
        "ma15": 100.05 if opportunity == "PULLBACK_SUPPORT" else 100.35,
        "kc_lower": 97.0,
        "kc_middle": 99.5,
        "kc_upper": 103.0,
        "is_closed": False,
    })
    if wick == "live":
        rows[-1]["high"] = 101.2
    frame = pd.DataFrame(rows)
    frame.attrs["timeframe_ms"] = 60_000
    close_id = live_stamp - 121_000
    account = SimpleNamespace(
        positions={},
        trades=[{
            "id": close_id,
            "symbol": "CAP/USDT",
            "action": "CLOSE_LONG",
            "side": "LONG",
            "status": "CLOSED",
            "price": 100.0,
        }],
    )
    return frame, account, close_id


def test_post_close_long_uses_ma5_or_ma15_support_reclaim():
    frame, account, _ = reentry_frame()

    decision = evaluate_post_close_continuation(
        frame, 100.8, "CAP/USDT", account, diagnostics={},
    )

    assert decision is not None
    assert decision["side"] == "LONG"
    assert decision["reason"] == "POST_CLOSE_LONG_PULLBACK_RECLAIM"
    assert decision["post_close_opportunity"] == "PULLBACK_SUPPORT"


def test_post_close_long_uses_body_break_of_prior_high_at_0_6_atr():
    frame, account, close_id = reentry_frame(opportunity="MOMENTUM_BREAKOUT")
    closed = frame.loc[frame["is_closed"]]

    opportunity = _post_close_long_opportunity(closed, close_id)
    decision = evaluate_post_close_continuation(
        frame, 101.1, "CAP/USDT", account, diagnostics={},
    )

    assert opportunity is not None
    assert opportunity["kind"] == "MOMENTUM_BREAKOUT"
    assert opportunity["atr"] == pytest.approx(1.0)
    assert decision is not None
    assert decision["reason"] == "POST_CLOSE_LONG_MOMENTUM_BREAKOUT"


@pytest.mark.parametrize("wick", ["signal", "live"])
def test_long_upper_wick_blocks_reentry_on_wick_bar_or_following_bar(wick):
    frame, account, _ = reentry_frame(wick=wick)
    diagnostics = {}

    decision = evaluate_post_close_continuation(
        frame, 100.8, "CAP/USDT", account, diagnostics=diagnostics,
    )

    assert decision is None
    assert diagnostics["reason"] in {
        "BLOCKED_POST_CLOSE_UPPER_WICK_COOLDOWN",
        "BLOCKED_POST_CLOSE_LIVE_UPPER_WICK",
    }


def test_post_close_long_without_structure_opportunity_is_rejected():
    frame, account, _ = reentry_frame()
    frame.loc[frame.index[-3], ["low", "close"]] = [100.2, 100.2]
    frame.loc[frame.index[-2], ["open", "close"]] = [100.3, 100.25]
    diagnostics = {}

    decision = evaluate_post_close_continuation(
        frame, 100.8, "CAP/USDT", account, diagnostics=diagnostics,
    )

    assert decision is None
    assert diagnostics["reason"] == "BLOCKED_POST_CLOSE_NO_LONG_OPPORTUNITY"


def test_pullback_reentry_accepts_one_or_two_ma_tests_without_ma15_break():
    frame, _, close_id = reentry_frame()
    closed = frame.loc[frame["is_closed"]].copy()
    closed.loc[closed.index[-3], "low"] = closed.iloc[-3]["ma15"]
    closed.loc[closed.index[-3], "close"] = closed.iloc[-3]["ma15"]
    earlier_close_id = close_id - 60_000

    opportunity = _post_close_long_opportunity(closed, earlier_close_id)

    assert opportunity is not None
    assert opportunity["kind"] == "PULLBACK_SUPPORT"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("close", 99.8),  # The pullback candle closed below MA15.
        ("ma5", 100.1),   # MA5 curled down before the confirmation.
        ("ma15", 99.8),   # MA15 slope turned down.
    ],
)
def test_pullback_reentry_fails_closed_on_broken_trend(field, value):
    frame, _, close_id = reentry_frame()
    closed = frame.loc[frame["is_closed"]].copy()
    if field == "close":
        closed.loc[closed.index[-2], field] = value
        closed.loc[closed.index[-2], "low"] = 99.7
    elif field == "ma5":
        closed.loc[closed.index[-2], "ma5"] = 100.3
        closed.loc[closed.index[-2], "low"] = 99.9
        closed.loc[closed.index[-1], "ma5"] = 100.2
    else:
        closed.loc[closed.index[-1], field] = value

    assert _post_close_long_opportunity(closed, close_id) is None


def test_trend_continuation_can_reenter_near_upper_kc_rail():
    frame, _, _ = reentry_frame()
    frame.loc[frame.index[-1], "kc_upper"] = 100.9
    quote = 100.8

    assert strict_kc_entry_problem(
        frame, quote, "LONG",
    ) == "INSUFFICIENT_UPPER_SPACE"
    assert strict_kc_entry_problem(
        frame, quote, "LONG",
        strategy_type=STRATEGY_TYPE_TREND_CONTINUATION,
    ) is None
    assert entry_market_structure_problem(
        frame, quote, "LONG",
        entry_phase="POST_CLOSE_CONTINUATION_ENTRY",
    ) is None


def test_entry_contract_authorizes_post_close_pullback_near_upper_rail(monkeypatch):
    frame, account, _ = reentry_frame()
    frame.loc[frame.index[-1], "kc_upper"] = 100.9
    monkeypatch.setattr(
        entry_contract, "ma5_ma15_cross_chop_problem",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        entry_contract.pipeline, "market_regime_problem",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        entry_contract.pipeline, "chop_lockout_problem",
        lambda *args, **kwargs: None,
    )
    diagnostics = {}
    decision = evaluate_entry_contract(
        frame, 100.8, account=account, symbol="CAP/USDT",
        diagnostics=diagnostics,
    )

    assert decision is not None, diagnostics
    assert decision["side"] == "LONG"
    assert decision["entry_phase"] == "POST_CLOSE_CONTINUATION_ENTRY"
