import asyncio

import pandas as pd

from core.services import mtf_kc_state_machine as mtf


def _entry_frames(previous_close=100.0, open_price=101.0):
    closed = pd.DataFrame(
        [
            {
                "open": previous_close,
                "high": previous_close + 1.0,
                "low": previous_close - 1.0,
                "close": previous_close,
                "kc_middle": 100.0,
                "atr": 2.0,
                "timestamp": 60_000,
            }
        ]
        * 15
    )
    live = pd.Series(
        {
            "open": open_price,
            "high": max(open_price, 102.0),
            "low": min(open_price, 98.0),
            "close": open_price,
            "timestamp": 960_000,
            "is_closed": False,
        }
    )
    frame = pd.DataFrame([live])
    frame.attrs["entry_finality_verified"] = True
    frame.attrs["previous_close"] = previous_close
    return frame, closed, live


def _stub_entry_math(
    monkeypatch, frame, *, htf_state, mas, previous_middle=None,
):
    htf = {
        "valid": True,
        "state": htf_state,
        "normalized_slope": 0.03 if htf_state == "UPTREND" else -0.03,
        "kc_middle": 100.0,
        "kc_upper": 110.0,
        "kc_lower": 90.0,
        "atr": 5.0,
        "bar_timestamp": 900_000,
    }
    monkeypatch.setattr(mtf, "evaluate_htf_kc_state", lambda _frame: htf)
    closed = pd.DataFrame(
        [
            {
                "open": (
                    frame.attrs["previous_close"] - 0.1
                    if htf_state == "UPTREND"
                    else frame.attrs["previous_close"] + 0.1
                ),
                "high": frame.attrs["previous_close"] + 1.0,
                "low": frame.attrs["previous_close"] - 1.0,
                "close": (
                    frame.attrs["previous_close"] + 0.1
                    if htf_state == "UPTREND"
                    else frame.attrs["previous_close"] - 0.1
                ),
                "kc_middle": 100.0,
                "atr": 2.0,
                "timestamp": 60_000,
            }
        ]
        * 15
    )
    live = frame.iloc[-1]
    monkeypatch.setattr(mtf, "_valid_live_frame", lambda *_args: (closed, live))
    monkeypatch.setattr(
        "core.services.ma5_entry_slope.quote_adjusted_entry_mas",
        lambda _frame, _quote: mas,
    )
    monkeypatch.setattr(
        "core.services.technical_indicators.compute_keltner_channels",
        lambda _frame, **_kwargs: pd.DataFrame(
            [
                {
                    "kc_middle": (
                        previous_middle
                        if previous_middle is not None
                        else 99.9 if htf_state == "UPTREND" else 100.1
                    ),
                    "kc_upper": 110.0,
                    "kc_lower": 90.0,
                },
                {"kc_middle": 100.0, "kc_upper": 110.0, "kc_lower": 90.0},
            ]
        ),
    )


def test_htf_slope_threshold_is_strict(monkeypatch):
    previous = pd.Series(
        {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "kc_middle": 100.0,
        }
    )
    current = pd.Series(
        {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "kc_middle": 0.005,
            "atr": 1.0,
            "kc_upper": 10.0,
            "kc_lower": -10.0,
            "timestamp": 900_000,
        }
    )
    previous["kc_middle"] = 0.0
    frame = pd.DataFrame([previous, current])
    monkeypatch.setattr(mtf, "_valid_live_frame", lambda *_args: (frame.iloc[:1], current))

    assert mtf.evaluate_htf_kc_state(frame)["state"] == "FLAT"

    current["kc_middle"] = 0.018
    assert mtf.evaluate_htf_kc_state(frame)["state"] == "UPTREND"


def test_trend_entry_allows_long_inside_band_limit(monkeypatch):
    frame, _closed, _live = _entry_frames(open_price=101.0)
    _stub_entry_math(
        monkeypatch, frame, htf_state="UPTREND", mas=(102.0, 101.0, 101.5),
    )

    result = mtf.evaluate_mtf_entry(frame, pd.DataFrame(), quote=102.0, side="LONG")

    assert result["allowed"]
    assert result["reason"] == "MTF_KC_TREND_ENTRY"
    assert result["entry_band_ratio"] == 0.2


def test_trend_entry_allows_short_with_mirrored_filters(monkeypatch):
    frame, _closed, _live = _entry_frames(open_price=99.0)
    _stub_entry_math(
        monkeypatch, frame, htf_state="DOWNTREND", mas=(98.0, 99.0, 99.0),
    )

    result = mtf.evaluate_mtf_entry(frame, pd.DataFrame(), quote=98.0, side="SHORT")

    assert result["allowed"]
    assert result["side"] == "SHORT"


def test_trend_entry_rejects_long_when_ma5_is_not_above_ma15(monkeypatch):
    frame, _closed, _live = _entry_frames(open_price=101.0)
    _stub_entry_math(
        monkeypatch, frame, htf_state="UPTREND", mas=(101.4, 101.0, 101.5),
    )

    result = mtf.evaluate_mtf_entry(frame, pd.DataFrame(), quote=102.0, side="LONG")

    assert not result["allowed"]
    assert result["reason"] == "BLOCKED_MA5_NOT_ABOVE_MA15"


def test_trend_entry_rejects_short_when_ma5_is_not_below_ma15(monkeypatch):
    frame, _closed, _live = _entry_frames(open_price=99.0)
    _stub_entry_math(
        monkeypatch, frame, htf_state="DOWNTREND", mas=(98.0, 99.0, 97.5),
    )

    result = mtf.evaluate_mtf_entry(
        frame, pd.DataFrame(), quote=98.0, side="SHORT",
    )

    assert not result["allowed"]
    assert result["reason"] == "BLOCKED_MA5_NOT_BELOW_MA15"


def test_trend_entry_rejects_three_line_ma_overlap_on_both_sides(monkeypatch):
    cases = (
        ("UPTREND", "LONG", 102.0, (100.15, 100.0, 100.05)),
        ("DOWNTREND", "SHORT", 98.0, (99.85, 100.0, 99.9)),
    )
    for state, side, quote, mas in cases:
        frame, _closed, _live = _entry_frames(
            open_price=101.0 if side == "LONG" else 99.0,
        )
        _stub_entry_math(
            monkeypatch, frame, htf_state=state, mas=mas,
        )

        result = mtf.evaluate_mtf_entry(
            frame, pd.DataFrame(), quote=quote, side=side,
        )

        assert not result["allowed"]
        assert result["reason"] == "BLOCKED_MA5_MA15_KC_MIDDLE_OVERLAP"


def test_trend_entry_rejects_long_when_live_ltf_kc_midline_is_falling(monkeypatch):
    frame, _closed, _live = _entry_frames(open_price=101.0)
    _stub_entry_math(
        monkeypatch,
        frame,
        htf_state="UPTREND",
        mas=(102.0, 101.0, 101.5),
        previous_middle=100.1,
    )

    result = mtf.evaluate_mtf_entry(
        frame, pd.DataFrame(), quote=102.0, side="LONG",
    )

    assert not result["allowed"]
    assert result["reason"] == "BLOCKED_KC_MIDLINE_NOT_RISING"


def test_trend_entry_rejects_short_when_live_ltf_kc_midline_is_rising(monkeypatch):
    frame, _closed, _live = _entry_frames(open_price=99.0)
    _stub_entry_math(
        monkeypatch,
        frame,
        htf_state="DOWNTREND",
        mas=(98.0, 99.0, 99.0),
        previous_middle=99.9,
    )

    result = mtf.evaluate_mtf_entry(
        frame, pd.DataFrame(), quote=98.0, side="SHORT",
    )

    assert not result["allowed"]
    assert result["reason"] == "BLOCKED_KC_MIDLINE_NOT_FALLING"


def test_trend_entry_allows_price_in_lower_half_of_channel(monkeypatch):
    frame, _closed, _live = _entry_frames(open_price=97.0)
    _stub_entry_math(
        monkeypatch, frame, htf_state="UPTREND", mas=(98.5, 97.5, 98.0),
    )

    result = mtf.evaluate_mtf_entry(frame, pd.DataFrame(), quote=98.0, side="LONG")

    assert result["allowed"]
    assert result["entry_band_ratio"] < 0


def test_trend_entry_allows_price_near_outer_band_without_chasing(monkeypatch):
    frame, _closed, _live = _entry_frames(open_price=108.5)
    _stub_entry_math(
        monkeypatch, frame, htf_state="UPTREND", mas=(109.0, 108.0, 108.5),
    )

    result = mtf.evaluate_mtf_entry(frame, pd.DataFrame(), quote=109.0, side="LONG")

    assert result["allowed"]
    assert result["entry_band_ratio"] == 0.9


def test_entry_at_exactly_maximum_band_ratio_is_rejected(monkeypatch):
    frame, _closed, _live = _entry_frames(open_price=109.0)
    _stub_entry_math(
        monkeypatch, frame, htf_state="UPTREND", mas=(111.0, 110.0, 110.5),
    )

    result = mtf.evaluate_mtf_entry(frame, pd.DataFrame(), quote=110.0, side="LONG")

    assert not result["allowed"]
    assert result["reason"] == "BLOCKED_MTF_KC_ENTRY_CONDITIONS"


def test_flat_cycle_blocks_midline_and_outer_band_entries(monkeypatch):
    frame, _closed, _live = _entry_frames(previous_close=100.0, open_price=100.5)
    _stub_entry_math(
        monkeypatch, frame, htf_state="FLAT", mas=(101.0, 100.0, 100.5),
    )

    for quote in (101.0, 111.0):
        result = mtf.evaluate_mtf_entry(
            frame, pd.DataFrame(), quote=quote, side="LONG",
        )
        assert not result["allowed"]
        assert result["reason"] == "BLOCKED_MTF_KC_CYCLE_NOT_DIRECTIONAL"


def test_breakout_requires_a_closed_red_bar_for_short(monkeypatch):
    frame, closed, _live = _entry_frames(previous_close=100.0, open_price=101.0)
    closed["open"] = 100.2
    closed["close"] = 100.0
    monkeypatch.setattr(mtf, "evaluate_htf_kc_state", lambda _frame: {
        "valid": True,
        "state": "DOWNTREND",
        "normalized_slope": -0.04,
        "kc_middle": 100.0,
        "kc_upper": 101.0,
        "kc_lower": 99.0,
        "atr": 5.0,
        "bar_timestamp": 900_000,
    })
    monkeypatch.setattr(mtf, "_valid_live_frame", lambda *_args: (closed, pd.Series({
        "open": 101.0,
        "high": 101.5,
        "low": 98.5,
        "close": 98.7,
        "timestamp": 960_000,
        "is_closed": False,
    })))
    monkeypatch.setattr(
        "core.services.ma5_entry_slope.quote_adjusted_entry_mas",
        lambda _frame, _quote: (101.2, 103.0, 102.4),
    )
    monkeypatch.setattr(
        "core.services.technical_indicators.compute_keltner_channels",
        lambda _frame, **_kwargs: pd.DataFrame(
            [
                {"kc_middle": 100.1, "kc_upper": 101.1, "kc_lower": 99.1},
                {"kc_middle": 100.0, "kc_upper": 101.0, "kc_lower": 99.0},
            ]
        ),
    )

    result = mtf.evaluate_mtf_entry(frame, pd.DataFrame(), quote=98.7, side="SHORT")

    assert result["allowed"]
    assert result["reason"] == "MTF_KC_BREAKOUT_ENTRY"


def test_breakout_rejects_without_closed_red_confirmation(monkeypatch):
    frame, closed, _live = _entry_frames(previous_close=100.0, open_price=101.0)
    _stub_entry_math(
        monkeypatch, frame, htf_state="DOWNTREND", mas=(101.2, 103.0, 102.4),
    )
    closed["open"] = 100.0
    closed["close"] = 100.2
    monkeypatch.setattr(mtf, "_valid_live_frame", lambda *_args: (closed, frame.iloc[-1]))

    result = mtf.evaluate_mtf_entry(
        frame, pd.DataFrame(), quote=98.7, side="SHORT",
    )

    assert not result["allowed"]
    assert result["reason"] == "BLOCKED_MTF_LTF_CLOSED_BAR_COLOR"


def test_breakout_cannot_bypass_live_candle_color(monkeypatch):
    frame, closed, _live = _entry_frames(previous_close=100.0, open_price=98.0)
    closed["open"] = 100.2
    closed["close"] = 100.0
    monkeypatch.setattr(mtf, "evaluate_htf_kc_state", lambda _frame: {
        "valid": True,
        "state": "DOWNTREND",
        "normalized_slope": -0.04,
        "kc_middle": 100.0,
        "kc_upper": 101.0,
        "kc_lower": 99.0,
        "atr": 5.0,
        "bar_timestamp": 900_000,
    })
    monkeypatch.setattr(mtf, "_valid_live_frame", lambda *_args: (closed, pd.Series({
        "open": 98.0,
        "high": 99.0,
        "low": 97.5,
        "close": 98.7,
        "timestamp": 960_000,
        "is_closed": False,
    })))
    monkeypatch.setattr(
        "core.services.ma5_entry_slope.quote_adjusted_entry_mas",
        lambda _frame, _quote: (98.0, 99.0, 99.0),
    )
    monkeypatch.setattr(
        "core.services.technical_indicators.compute_keltner_channels",
        lambda _frame, **_kwargs: pd.DataFrame([
            {"kc_middle": 100.1, "kc_upper": 101.1, "kc_lower": 99.1},
            {"kc_middle": 100.0, "kc_upper": 101.0, "kc_lower": 99.0},
        ]),
    )

    result = mtf.evaluate_mtf_entry(
        frame, pd.DataFrame(), quote=98.7, side="SHORT",
    )

    assert not result["allowed"]
    assert result["reason"] == "WAIT_MTF_KC_NEXT_BAR_COLOR"


def test_wrong_color_arms_one_bar_candidate_and_next_green_bar_confirms(monkeypatch):
    frame, _closed, _live = _entry_frames(open_price=101.0)
    _stub_entry_math(
        monkeypatch, frame, htf_state="UPTREND", mas=(101.0, 100.0, 100.5),
    )

    waiting = mtf.evaluate_mtf_entry(
        frame, pd.DataFrame(), quote=100.5, side="LONG",
    )

    assert not waiting["allowed"]
    assert waiting["reason"] == "WAIT_MTF_KC_NEXT_BAR_COLOR"
    candidate = waiting["deferred_candidate"]

    next_frame, _closed, _live = _entry_frames(open_price=100.3)
    next_frame.loc[next_frame.index[-1], "timestamp"] = 1_020_000
    _stub_entry_math(
        monkeypatch, next_frame, htf_state="UPTREND", mas=(101.0, 100.0, 100.5),
    )

    confirmed = mtf.evaluate_mtf_entry(
        next_frame,
        pd.DataFrame(),
        quote=100.8,
        side="LONG",
        pending_candidate=candidate,
    )

    assert confirmed["allowed"]
    assert confirmed["reason"] == "MTF_KC_DEFERRED_ENTRY"


def test_deferred_candidate_does_not_survive_more_than_one_bar(monkeypatch):
    frame, _closed, _live = _entry_frames(previous_close=101.0, open_price=100.3)
    frame.loc[frame.index[-1], "timestamp"] = 1_080_000
    _stub_entry_math(
        monkeypatch, frame, htf_state="DOWNTREND", mas=(99.5, 100.0, 100.0),
    )
    candidate = {
        "side": "LONG",
        "source_bar_id": 960_000,
        "state": "UPTREND",
        "reason": "MTF_KC_TREND_ENTRY",
    }

    result = mtf.evaluate_mtf_entry(
        frame,
        pd.DataFrame(),
        quote=100.8,
        side="LONG",
        pending_candidate=candidate,
    )

    assert not result["allowed"]
    assert result["reason"] == "BLOCKED_MTF_KC_ENTRY_CONDITIONS"


def test_flat_cycle_waits_for_closed_adverse_1m_bar(monkeypatch):
    from core.services.execution_manager import ExecutionManager

    account = type("Account", (), {
        "positions": {},
        "position_meta": {
            "龙虾/USDT": {"entry_method_authority": "MTF_KC_STATE_MACHINE"},
        },
        "save_state": lambda self, strict=False: None,
    })()
    position = {
        "side": "LONG",
        "entry_price": 100.0,
        "amount": 1.0,
        "open_timestamp": 900_000,
        "entry_method_authority": "MTF_KC_STATE_MACHINE",
    }
    account.positions["龙虾/USDT"] = position
    close_calls = []
    monkeypatch.setattr(mtf, "evaluate_htf_kc_state", lambda _frame: {
        "valid": True,
        "state": "FLAT",
        "normalized_slope": 0.0,
        "kc_middle": 100.0,
        "kc_upper": 110.0,
        "kc_lower": 90.0,
        "atr": 5.0,
        "bar_timestamp": 900_000,
    })
    monkeypatch.setattr(
        mtf, "update_mtf_position_protection", lambda *_args: None,
    )
    monkeypatch.setattr(mtf, "authorize_mtf_exit", lambda *_args: True)
    closed_bar = pd.DataFrame([{
        "open": 100.0,
        "close": 100.0,
        "timestamp": 900_000,
    }])
    live_bar = pd.Series({
        "open": 100.0,
        "close": 100.0,
        "timestamp": 960_000,
        "is_closed": False,
    })
    monkeypatch.setattr(
        mtf, "_valid_live_frame", lambda *_args: (closed_bar, live_bar),
    )

    async def close_position(_cls, _account, symbol, quote, reason, meta=None):
        close_calls.append((symbol, quote, reason))
        return True

    monkeypatch.setattr(
        ExecutionManager, "close_position", classmethod(close_position),
    )
    result = asyncio.run(ExecutionManager._process_mtf_kc_state_machine(
        symbol="龙虾/USDT",
        account=account,
        engine_or_account=account,
        frame_1m=pd.DataFrame([{"timestamp": 960_000}]),
        frame_15m=pd.DataFrame(),
        quote=101.0,
        position=position,
    ))

    assert result["status"] == "HOLD"
    assert result["reason"] == "WAIT_MTF_EXIT_CLOSED_BAR_COLOR"
    assert close_calls == []

    closed_bar = pd.DataFrame([{
        "open": 101.0,
        "close": 100.5,
        "timestamp": 960_000,
    }])
    live_bar = pd.Series({
        "open": 100.5,
        "close": 100.6,
        "timestamp": 1_020_000,
        "is_closed": False,
    })
    result = asyncio.run(ExecutionManager._process_mtf_kc_state_machine(
        symbol="龙虾/USDT",
        account=account,
        engine_or_account=account,
        frame_1m=pd.DataFrame([{"timestamp": 1_020_000}]),
        frame_15m=pd.DataFrame(),
        quote=100.6,
        position=position,
    ))

    assert result["status"] == "EXIT_EXECUTED"
    assert close_calls == [(
        "龙虾/USDT",
        100.6,
        "MTF_KC_STATE_MACHINE_FLAT_CYCLE_END_EXIT",
    )]


def test_favorable_closed_bar_cancels_pending_mtf_exit(monkeypatch):
    holder = {
        "closed": pd.DataFrame([{
            "open": 100.0,
            "close": 100.0,
            "timestamp": 900_000,
        }]),
        "live": pd.Series({"timestamp": 960_000, "is_closed": False}),
    }
    monkeypatch.setattr(
        mtf, "_valid_live_frame",
        lambda *_args: (holder["closed"], holder["live"]),
    )

    for side, favorable_open, favorable_close in (
        ("LONG", 100.0, 100.5),
        ("SHORT", 100.5, 100.0),
    ):
        holder["closed"] = pd.DataFrame([{
            "open": 100.0,
            "close": 100.0,
            "timestamp": 900_000,
        }])
        holder["live"] = pd.Series({"timestamp": 960_000, "is_closed": False})
        position = {
            "side": side,
            "entry_price": 100.0,
            "open_timestamp": 900_000,
        }
        metadata = {}
        armed = mtf.evaluate_mtf_exit_confirmation(
            pd.DataFrame(), position, metadata,
            "MTF_KC_STATE_MACHINE_FLAT_CYCLE_END_EXIT",
        )
        assert armed["waiting"]

        holder["closed"] = pd.DataFrame([{
            "open": favorable_open,
            "close": favorable_close,
            "timestamp": 960_000,
        }])
        holder["live"] = pd.Series({"timestamp": 1_020_000, "is_closed": False})
        confirmed = mtf.evaluate_mtf_exit_confirmation(
            pd.DataFrame(), position, metadata, None,
        )

        assert not confirmed["allowed"]
        assert confirmed["changed"]
        assert "mtf_pending_closed_bar_exit" not in metadata


def test_close_in_current_live_bar_fails_closed_for_stale_or_invalid_identity(monkeypatch):
    live = pd.Series({"timestamp": 960_000})
    monkeypatch.setattr(
        mtf, "_valid_live_frame", lambda *_args: (pd.DataFrame(), live),
    )

    assert mtf.close_in_current_live_1m_bar(pd.DataFrame(), 960_000) is True
    assert mtf.close_in_current_live_1m_bar(pd.DataFrame(), 900_000) is False
    assert mtf.close_in_current_live_1m_bar(pd.DataFrame(), 1_020_000) is None


def test_mtf_position_loss_does_not_create_a_per_position_hard_stop():
    position = {
        "side": "LONG",
        "entry_price": 100.0,
        "atr": 5.0,
        "entry_method_authority": "MTF_KC_STATE_MACHINE",
    }

    result = mtf.update_mtf_position_protection(position, {}, 89.0)

    assert result is None
    assert "mtf_kc_stop_price" not in position


def test_mtf_exit_ticket_passes_execution_gate_only_at_authorized_price():
    from types import SimpleNamespace

    from core.services.math_execution_gate import validate_exit_at_execution

    symbol = "龙虾/USDT"
    reason = "MTF_KC_STATE_MACHINE_FLAT_CYCLE_END_EXIT"
    position = {
        "side": "LONG",
        "entry_method_authority": "MTF_KC_STATE_MACHINE",
    }
    metadata = {"entry_method_authority": "MTF_KC_STATE_MACHINE"}
    account = SimpleNamespace(
        positions={symbol: position},
        position_meta={symbol: metadata},
        save_state=lambda: None,
    )

    assert mtf.authorize_mtf_exit(
        account, symbol, reason, 99.0, {"state": "FLAT"},
    )
    allowed, code, evidence = validate_exit_at_execution(
        account, symbol, position, 99.0, reason,
    )

    assert allowed
    assert code == "MTF_KC_STATE_MACHINE_EXIT_APPROVED"
    assert evidence == {"state": "FLAT"}
    assert not validate_exit_at_execution(
        account, symbol, position, 99.01, reason,
    )[0]


def test_authorized_mtf_exit_bypasses_profit_only_and_disabled_stop_vetoes(monkeypatch):
    from core.paper_account import PaperAccount

    symbol = "龙虾/USDT"
    reason = "MTF_KC_STATE_MACHINE_FLAT_CYCLE_END_EXIT"
    position = {
        "symbol": symbol,
        "side": "LONG",
        "entry_price": 100.0,
        "qty": 1.0,
        "margin": 10.0,
        "entry_method_authority": "MTF_KC_STATE_MACHINE",
    }
    metadata = {"entry_method_authority": "MTF_KC_STATE_MACHINE"}
    account = object.__new__(PaperAccount)
    account.positions = {symbol: position}
    account.position_meta = {symbol: metadata}
    account.closing_lock = set()
    account.pending_limit_orders = {}
    account.balance = 90.0
    account.realized_pnl = 0.0
    account.last_closed_at = {}
    account.last_closed_side = {}
    account.entry_filter_last = {}
    account.trades = []
    account.on_trade_closed = None
    account.log = lambda *_args, **_kwargs: None
    account.save_state = lambda: None
    account._auto_close_reject_logged_at = {}

    assert mtf.authorize_mtf_exit(
        account, symbol, reason, 99.0, {"state": "FLAT"},
    )
    monkeypatch.setattr("core.paper_account.ONLY_CLOSE_ON_PROFIT", True)
    monkeypatch.setattr("core.paper_account.DISABLE_STOP_LOSS", True)

    closed = asyncio.run(account.close_position(symbol, 99.0, reason))

    assert closed
    assert symbol not in account.positions
    assert symbol not in account.position_meta


def test_downward_htf_cycle_never_authorizes_long_entry(monkeypatch):
    frame, _closed, _live = _entry_frames(open_price=101.0)
    _stub_entry_math(
        monkeypatch, frame, htf_state="DOWNTREND", mas=(98.0, 99.0, 99.0),
    )

    result = mtf.evaluate_mtf_entry(
        frame, pd.DataFrame(), quote=102.0, side="LONG",
    )

    assert not result["allowed"]
    assert result["reason"] == "BLOCKED_MTF_KC_ENTRY_CONDITIONS"


def test_deferred_candidate_cannot_chase_past_entry_band_limit(monkeypatch):
    frame, _closed, _live = _entry_frames(open_price=106.8)
    frame.loc[frame.index[-1], "timestamp"] = 1_020_000
    _stub_entry_math(
        monkeypatch, frame, htf_state="UPTREND", mas=(114.0, 113.0, 113.5),
    )
    candidate = {
        "side": "LONG",
        "source_bar_id": 960_000,
        "state": "UPTREND",
        "reason": "MTF_KC_TREND_ENTRY",
    }

    result = mtf.evaluate_mtf_entry(
        frame,
        pd.DataFrame(),
        quote=114.0,
        side="LONG",
        pending_candidate=candidate,
    )

    assert not result["allowed"]
    assert result["reason"] == "BLOCKED_MTF_KC_ENTRY_CONDITIONS"


def test_execution_manager_persists_next_bar_candidate(monkeypatch):
    from core.services.execution_manager import ExecutionManager

    class Account:
        def __init__(self):
            self.positions = {}
            self.position_meta = {}
            self.entry_filter_last = {}
            self.saves = 0

        def save_state(self, strict=False):
            self.saves += 1

    candidate = {
        "side": "LONG",
        "source_bar_id": 960_000,
        "state": "UPTREND",
        "reason": "MTF_KC_TREND_ENTRY",
    }
    monkeypatch.setattr(mtf, "evaluate_htf_kc_state", lambda _frame: {
        "valid": True,
        "state": "UPTREND",
        "normalized_slope": 0.03,
        "kc_middle": 100.0,
        "kc_upper": 110.0,
        "kc_lower": 90.0,
        "atr": 5.0,
        "bar_timestamp": 900_000,
    })
    monkeypatch.setattr(mtf, "evaluate_mtf_entry", lambda *_args, **_kwargs: {
        "valid": True,
        "state": "UPTREND",
        "normalized_slope": 0.03,
        "allowed": False,
        "reason": "WAIT_MTF_KC_NEXT_BAR_COLOR",
        "deferred_candidate": candidate,
    })
    account = Account()
    frame = pd.DataFrame([{"timestamp": 960_000}])

    result = asyncio.run(ExecutionManager._process_mtf_kc_state_machine(
        symbol="龙虾/USDT",
        account=account,
        engine_or_account=account,
        frame_1m=frame,
        frame_15m=pd.DataFrame(),
        quote=100.0,
        position=None,
    ))

    assert result["reason"] == "WAIT_MTF_KC_NEXT_BAR_COLOR"
    assert account.entry_filter_last["mtf_kc_pending_candidates"]["龙虾/USDT"] == candidate
    assert account.saves == 1


def test_midcycle_retracement_does_not_create_an_exit():
    position = {
        "side": "LONG",
        "entry_price": 100.0,
        "mtf_ltf_atr": 1.0,
        "mtf_peak_favorable_price": 103.0,
    }
    metadata = {"mtf_kc_state_machine": True, "mtf_kc_lower_15m": 90.0}

    result = mtf.update_mtf_position_protection(position, metadata, 102.0)

    assert result is None
    assert position["mtf_peak_favorable_price"] == 103.0
    assert "mtf_kc_stop_price" not in position


def test_mtf_exit_authority_requires_matching_reason_and_position_side():
    from core.services.exits.authorized_position_exit import UltimateExitGatekeeper

    position = {"side": "LONG"}
    metadata = {
        "mtf_kc_exit_authority": {
            "reason": "MTF_KC_STATE_MACHINE_ACCOUNT_CIRCUIT_BREAKER_HARD_STOP",
            "side": "LONG",
            "price": 98.0,
        }
    }

    assert UltimateExitGatekeeper.can_execute_exit(
        "CAP/USDT",
        position,
        metadata,
        "MTF_KC_STATE_MACHINE_ACCOUNT_CIRCUIT_BREAKER_HARD_STOP",
        98.0,
    )
    assert not UltimateExitGatekeeper.can_execute_exit(
        "CAP/USDT",
        position,
        metadata,
        "MTF_KC_STATE_MACHINE_UPTREND_REVERSAL_EXIT",
        98.0,
    )


def test_account_hard_stop_bypasses_closed_bar_exit_confirmation():
    position = {
        "side": "SHORT",
        "entry_price": 100.0,
        "mtf_ltf_atr": 1.0,
        "open_timestamp": 900_000,
    }
    metadata = {"mtf_kc_state_machine": True}

    result = mtf.evaluate_mtf_exit_confirmation(
        frame_1m=None,
        position=position,
        metadata=metadata,
        reason="ACCOUNT_CIRCUIT_BREAKER_HARD_STOP",
        evidence={"loss": 0.1},
    )

    assert result["allowed"]
    assert result["reason"] == "ACCOUNT_CIRCUIT_BREAKER_HARD_STOP"
    assert result["evidence"] == {"loss": 0.1}

    waterfall = mtf.evaluate_mtf_exit_confirmation(
        frame_1m=None,
        position=position,
        metadata=metadata,
        reason="EXIT_BY_EXTREME_WATERFALL",
        evidence={"threshold": 1.5},
    )
    assert waterfall["allowed"]
    assert waterfall["reason"] == "EXIT_BY_EXTREME_WATERFALL"


def test_mtf_midcycle_exit_reasons_are_not_authorized():
    position = {
        "side": "LONG",
        "entry_method_authority": "MTF_KC_STATE_MACHINE",
    }
    metadata = {"entry_method_authority": "MTF_KC_STATE_MACHINE"}

    result = mtf.evaluate_mtf_exit_confirmation(
        frame_1m=None,
        position=position,
        metadata=metadata,
        reason="MTF_KC_STATE_MACHINE_RATCHET_STOP",
    )

    assert not result["allowed"]
    assert result["reason"] == "BLOCKED_MTF_EXIT_REASON_NOT_AUTHORIZED"


def test_mtf_midcycle_exit_is_blocked_at_execution_gate():
    from types import SimpleNamespace

    from core.services.math_execution_gate import validate_exit_at_execution

    symbol = "龙虾/USDT"
    position = {
        "side": "LONG",
        "entry_method_authority": "MTF_KC_STATE_MACHINE",
    }
    account = SimpleNamespace(
        positions={symbol: position},
        position_meta={symbol: {"entry_method_authority": "MTF_KC_STATE_MACHINE"}},
    )

    allowed, reason, _evidence = validate_exit_at_execution(
        account, symbol, position, 99.0, "觸發移動止利 (Trailing Take-Profit)",
    )

    assert not allowed
    assert reason == "BLOCKED_MTF_MIDCYCLE_EXIT"


def test_final_mtf_open_preserves_htf_frame_and_pending_candidate(monkeypatch):
    from core.services.execution_manager import ExecutionManager

    captured = {}
    frame = pd.DataFrame(
        [
            {
                "timestamp": 60_000,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "ma5": 100.5,
                "ma15": 101.0,
                "kc_middle": 100.0,
                "is_closed": True,
            },
            {
                "timestamp": 120_000,
                "open": 100.5,
                "high": 102.0,
                "low": 100.5,
                "close": 101.0,
                "ma5": 100.8,
                "ma15": 101.2,
                "kc_middle": 100.1,
                "is_closed": False,
            },
        ]
    )
    htf_frame = pd.DataFrame([{"timestamp": 900_000}])
    pending_candidate = {"side": "LONG", "source_bar_id": 60_000}

    class Account:
        positions = {}
        position_meta = {}
        closing_lock = set()

        def get_wallet_balance(self):
            return 100.0

        def get_available_balance(self):
            return 100.0

        async def open_position(self, **kwargs):
            captured.update(kwargs)
            return True

    monkeypatch.setattr(
        "core.services.entry_methods.revalidate_entry_method",
        lambda *_args, **_kwargs: (True, "MTF_KC_STATE_MACHINE"),
    )

    opened = asyncio.run(
        ExecutionManager.open_position(
            Account(),
            "龙虾/USDT",
            "LONG",
            101.0,
            "MTF_KC_STATE_MACHINE",
            meta={
                "entry_method_authority": "MTF_KC_STATE_MACHINE",
                "entry_method_signal": "MTF_KC_TREND_ENTRY",
                "entry_method_bar_id": 120_000,
                "raw_df": frame,
                "atr": 1.0,
                "frame_15m": htf_frame,
                "mtf_pending_candidate": pending_candidate,
            },
        )
    )

    assert opened
    assert captured["entry_context"]["frame_15m"] is htf_frame
    assert captured["entry_context"]["mtf_pending_candidate"] is pending_candidate
