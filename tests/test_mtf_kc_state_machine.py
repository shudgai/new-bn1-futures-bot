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


def _stub_entry_math(monkeypatch, frame, *, htf_state, mas):
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
                "open": frame.attrs["previous_close"],
                "high": frame.attrs["previous_close"] + 1.0,
                "low": frame.attrs["previous_close"] - 1.0,
                "close": frame.attrs["previous_close"],
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
            [{"kc_middle": 100.0, "kc_upper": 110.0, "kc_lower": 90.0}]
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
            "kc_middle": 100.1,
            "atr": 5.0,
            "kc_upper": 110.1,
            "kc_lower": 90.1,
            "timestamp": 900_000,
        }
    )
    frame = pd.DataFrame([previous, current])
    monkeypatch.setattr(mtf, "_valid_live_frame", lambda *_args: (frame.iloc[:1], current))

    assert mtf.evaluate_htf_kc_state(frame)["state"] == "FLAT"

    current["kc_middle"] = 100.1001
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


def test_entry_at_exactly_maximum_band_ratio_is_rejected(monkeypatch):
    frame, _closed, _live = _entry_frames(open_price=106.0)
    _stub_entry_math(
        monkeypatch, frame, htf_state="UPTREND", mas=(107.0, 106.0, 106.5),
    )

    result = mtf.evaluate_mtf_entry(frame, pd.DataFrame(), quote=106.5, side="LONG")

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


def test_breakout_bar_allows_short_even_without_red_candle(monkeypatch):
    frame, _closed, _live = _entry_frames(previous_close=100.0, open_price=101.0)
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
    monkeypatch.setattr(mtf, "_valid_live_frame", lambda *_args: (_closed, pd.Series({
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
            [{"kc_middle": 100.0, "kc_upper": 101.0, "kc_lower": 99.0}]
        ),
    )

    result = mtf.evaluate_mtf_entry(frame, pd.DataFrame(), quote=98.7, side="SHORT")

    assert result["allowed"]
    assert result["reason"] == "MTF_KC_BREAKOUT_ENTRY"


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
        monkeypatch, frame, htf_state="DOWNTREND", mas=(101.0, 100.0, 100.5),
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


def test_flat_cycle_closes_managed_position_immediately(monkeypatch):
    from core.services.execution_manager import ExecutionManager

    account = type("Account", (), {
        "positions": {},
        "position_meta": {"龙虾/USDT": {"mtf_kc_state_machine": True}},
        "save_state": lambda self, strict=False: None,
    })()
    position = {
        "side": "LONG",
        "entry_price": 100.0,
        "amount": 1.0,
        "open_timestamp": 0.0,
        "mtf_kc_state_machine": True,
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

    assert result["status"] == "EXIT_EXECUTED"
    assert close_calls == [(
        "龙虾/USDT",
        101.0,
        "MTF_KC_STATE_MACHINE_FLAT_CYCLE_END_EXIT",
    )]


def test_deferred_candidate_cannot_chase_past_entry_band_limit(monkeypatch):
    frame, _closed, _live = _entry_frames(open_price=106.8)
    frame.loc[frame.index[-1], "timestamp"] = 1_020_000
    _stub_entry_math(
        monkeypatch, frame, htf_state="UPTREND", mas=(108.0, 107.0, 107.5),
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
        quote=107.0,
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


def test_ratchet_stop_closes_after_one_atr_giveback():
    position = {
        "side": "LONG",
        "entry_price": 100.0,
        "mtf_ltf_atr": 1.0,
        "mtf_peak_favorable_price": 103.0,
    }
    metadata = {"mtf_kc_state_machine": True, "mtf_kc_lower_15m": 90.0}

    result = mtf.update_mtf_position_protection(position, metadata, 102.0)

    assert result == {
        "reason": "MTF_KC_STATE_MACHINE_RATCHET_STOP",
        "stop": 102.0,
    }


def test_mtf_exit_authority_requires_matching_reason_and_position_side():
    from core.services.exits.authorized_position_exit import UltimateExitGatekeeper

    position = {"side": "LONG"}
    metadata = {
        "mtf_kc_exit_authority": {
            "reason": "MTF_KC_STATE_MACHINE_HARD_STOP",
            "side": "LONG",
            "price": 98.0,
        }
    }

    assert UltimateExitGatekeeper.can_execute_exit(
        "CAP/USDT",
        position,
        metadata,
        "MTF_KC_STATE_MACHINE_HARD_STOP",
        98.0,
    )
    assert not UltimateExitGatekeeper.can_execute_exit(
        "CAP/USDT",
        position,
        metadata,
        "MTF_KC_STATE_MACHINE_UPTREND_REVERSAL_EXIT",
        98.0,
    )


def test_hard_stop_remains_active_before_ratchet_arms():
    position = {
        "side": "SHORT",
        "entry_price": 100.0,
        "mtf_ltf_atr": 1.0,
    }
    metadata = {"mtf_kc_state_machine": True, "mtf_kc_upper_15m": 110.0}

    result = mtf.update_mtf_position_protection(position, metadata, 102.0)

    assert result == {
        "reason": "MTF_KC_STATE_MACHINE_HARD_STOP",
        "stop": 102.0,
    }
