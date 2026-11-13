"""Tests for low-latency EMA5/ZLEMA5 entry, maker trigger prediction, and loss-fix guards."""
import time
import numpy as np
import pandas as pd
import pytest

from core.services.low_latency_entry import (
    PeakValleyEntryConfig,
    compute_ema5_and_zlema5,
    evaluate_peak_valley_inflection_entry,
    evaluate_realtime_ma_kc_entry,
    predict_maker_trigger_price,
)
from core.services.market_consolidation_filter import evaluate_market_blacklist
from core.services.entry_methods import POST_CLOSE_KC_REENTRY, evaluate_entry_method
from core.gates.ultimate_short_exit_gatekeeper import UltimateShortExitGatekeeper


def _make_bars(closes):
    now_min = int(time.time() // 60)
    rows = []
    for i, c in enumerate(closes):
        ts = (now_min - len(closes) + i) * 60_000
        rows.append({
            "timestamp": ts,
            "open": c * 0.999,
            "high": c * 1.002,
            "low": c * 0.998,
            "close": c,
            "volume": 1000.0,
            "is_closed": True,
        })
    return pd.DataFrame(rows)


def test_zlema5_reacts_faster_than_sma5_on_inflection():
    closes = [100.0] * 20 + [100.5, 101.2, 102.5]
    s = pd.Series(closes)
    sma5 = s.rolling(5).mean()
    ema5, zlema5 = compute_ema5_and_zlema5(s)
    assert zlema5.iloc[-1] > ema5.iloc[-1] > sma5.iloc[-1]


def test_realtime_entry_triggers_intrabar_and_filters_flat_noise():
    # Uptrend forming on live tick
    closes = [100.0 + i * 0.05 for i in range(25)]
    df = _make_bars(closes)
    df.loc[df.index[-1], "is_closed"] = False

    res_long = evaluate_realtime_ma_kc_entry(
        df, live_price=102.0, use_zlema=True, min_slope_pct=0.0002, min_slope_atr=0.03,
    )
    assert res_long["action"] == "OPEN_LONG"
    assert res_long["side"] == "LONG"

    # Flat chop should be rejected by minimum slope threshold
    flat_df = _make_bars([100.0 + (0.001 if i % 2 else -0.001) for i in range(25)])
    res_flat = evaluate_realtime_ma_kc_entry(
        flat_df, live_price=100.002, use_zlema=True, min_slope_pct=0.0005, min_slope_atr=0.05,
    )
    assert res_flat["action"] == "HOLD"
    assert res_flat["reason"] == "FLAT_SLOPE_NOISE_FILTERED"


def test_predict_maker_trigger_price_achieves_target_slopes():
    prev_ema5 = 100.0
    prev_kc_mid = 99.8
    min_slope = 0.001
    pred = predict_maker_trigger_price(
        prev_ema5, prev_kc_mid, "LONG", min_slope_pct=min_slope,
    )
    assert pred is not None
    p = pred["trigger_price"]
    alpha5 = 2.0 / 6.0
    alpha20 = 2.0 / 21.0
    next_ema5 = alpha5 * p + (1.0 - alpha5) * prev_ema5
    next_kc = alpha20 * p + (1.0 - alpha20) * prev_kc_mid
    assert (next_ema5 - prev_ema5) / prev_ema5 >= min_slope - 1e-12
    assert (next_kc - prev_kc_mid) / prev_kc_mid >= min_slope - 1e-12


def test_dead_chop_adx_below_15_blocked_even_with_meme_impulse():
    n = 30
    close = 100 + 0.5 * np.sin(np.arange(n) / 3.0) + np.arange(n) * 0.02
    frame = pd.DataFrame({
        "timestamp": [1_700_000_000_000 + i * 60_000 for i in range(n)],
        "open": close - 0.05, "close": close, "high": close + 0.6, "low": close - 0.6,
        "ma5": close, "ma15": close - 0.1, "atr": 0.2, "adx": 13.5, "volume": 100.0,
        "kc_middle": 100 + np.arange(n) * 0.05, "is_closed": True,
    })
    frame["kc_upper"] = frame["kc_middle"] + 2
    frame["kc_lower"] = frame["kc_middle"] - 2
    last = frame.index[-1]
    frame.loc[last, ["open", "close", "high", "low"]] = [100.4, 100.4 + 0.2 * 0.65, 100.6, 100.39]
    blocked, reason, _ = evaluate_market_blacklist(
        frame, -1, side="LONG", meme_policy=True, is_breakout=True, independent_kc_direction=True,
    )
    assert blocked is True
    assert reason == "VETO_DONCHIAN_RANGE_LOW_TREND"


def test_post_close_reentry_blocks_giant_spike_chase():
    now_min = int(time.time() // 60)
    rows = [
        {
            "timestamp": (now_min - 2) * 60_000,
            "open": 100.0, "high": 101.0, "low": 99.8, "close": 100.8,
            "kc_upper": 101.0, "kc_middle": 100.0, "kc_lower": 99.0,
            "ma5": 100.4, "ma15": 100.0, "atr": 1.0, "is_closed": True,
        },
        {
            "timestamp": (now_min - 1) * 60_000,
            "open": 100.8, "high": 102.7, "low": 100.7, "close": 102.6,  # body = 1.8 > 1.5 ATR spike!
            "kc_upper": 101.5, "kc_middle": 100.2, "kc_lower": 98.9,
            "ma5": 101.0, "ma15": 100.1, "atr": 1.0, "is_closed": True,
        },
    ]
    df = pd.DataFrame(rows)
    df.attrs.update(timeframe_ms=60_000, snapshot_ms=time.time() * 1000)
    last_exit = (now_min - 3) * 60
    sig = evaluate_entry_method(
        df, quote=102.7, symbol="龙虾/USDT",
        last_exit_timestamp=last_exit, expected_method=POST_CLOSE_KC_REENTRY,
    )
    assert sig is None


def test_kc_middle_slope_exit_rejects_micro_noise_below_005_atr():
    now = time.time()
    latest_minute = int(now // 60)
    frame = pd.DataFrame([
        {
            "timestamp": (latest_minute - 2) * 60_000, "is_closed": True,
            "open": 100.0, "high": 100.3, "low": 99.7, "close": 100.1,
            "ma5": 100.0, "ma15": 100.0, "kc_middle": 100.000, "atr": 1.0,
            "kc_upper": 102.0, "kc_lower": 98.0,
        },
        {
            "timestamp": (latest_minute - 1) * 60_000, "is_closed": True,
            "open": 100.1, "high": 100.4, "low": 99.8, "close": 100.2,
            "ma5": 100.1, "ma15": 100.0, "kc_middle": 99.995, "atr": 1.0,  # -0.005 ATR micro noise
            "kc_upper": 102.0, "kc_lower": 98.0,
        },
    ])
    frame.attrs["timeframe_ms"] = 60_000
    pos = {"side": "LONG", "entry_price": 100.0, "qty": 1.0, "open_timestamp": now - 300}
    assert UltimateShortExitGatekeeper.kc_middle_slope_exit_candidate(pos, frame, now=now) is None


def _make_inflection_frame(*, side: str, kc_width_pct: float = 0.02, ma_spread_pct: float = 0.005, adx: float = 28.0) -> pd.DataFrame:
    """Build a 22-bar 1m OHLCV+indicator DataFrame with an unambiguous peak/valley MA5 inflection."""
    now_min = int(time.time() // 60)
    rows = []
    for i in range(22):
        ts = (now_min - 22 + i) * 60_000
        if side == "LONG":
            # KC midline still falling while latest bar inflects upward off the valley
            base_mid = 105.0 - i * 0.25
            ma15 = base_mid + 0.8
            if i < 19:
                ma5 = base_mid - 0.4 + i * 0.01
                o, c = ma5 + 0.1, ma5 - 0.1
            elif i == 19:
                ma5 = 100.0
                o, c = 100.2, 99.8
            elif i == 20:
                ma5 = 99.5  # prev_slope = -0.5 <= 0
                o, c = 99.8, 99.4
            else:
                ma5 = 100.2  # curr_slope = +0.7 > 0 (valley inflection)
                o, c = 99.5, 100.8  # green bar closing above MA5
        else:
            # KC midline still rising while latest bar inflects downward off the peak
            base_mid = 95.0 + i * 0.25
            ma15 = base_mid - 0.8
            if i < 19:
                ma5 = base_mid + 0.4 - i * 0.01
                o, c = ma5 - 0.1, ma5 + 0.1
            elif i == 19:
                ma5 = 100.0
                o, c = 99.8, 100.2
            elif i == 20:
                ma5 = 100.5  # prev_slope = +0.5 >= 0
                o, c = 100.2, 100.6
            else:
                ma5 = 99.8  # curr_slope = -0.7 < 0 (peak inflection)
                o, c = 100.5, 99.2  # red bar closing below MA5

        # Apply exact MA15 spread on final bar while keeping trend direction intact
        if i == 21:
            ma15 = ma5 * (1.0 - ma_spread_pct) if side == "LONG" else ma5 * (1.0 + ma_spread_pct)
            if ma_spread_pct == 0.0:
                ma15 = ma5

        half_w = base_mid * (kc_width_pct / 2.0)
        rows.append({
            "timestamp": ts,
            "open": o,
            "high": max(o, c) + 0.15,
            "low": min(o, c) - 0.15,
            "close": c,
            "volume": 2000.0,
            "kc_middle": base_mid,
            "kc_upper": base_mid + half_w,
            "kc_lower": base_mid - half_w,
            "ma5": ma5,
            "ma15": ma15,
            "atr": 0.6,
            "adx": adx,
            "is_closed": True,
        })
    df = pd.DataFrame(rows)
    df.attrs["timeframe_ms"] = 60_000.0
    return df


def test_peak_valley_inflection_long_triggers_even_when_kc_middle_falling():
    df = _make_inflection_frame(side="LONG", kc_width_pct=0.02, ma_spread_pct=0.005, adx=28.0)
    res = evaluate_peak_valley_inflection_entry(df)
    assert res["action"] == "OPEN_LONG"
    assert res["side"] == "LONG"
    assert res["kc_direction"] == "DOWN"
    assert res["is_consolidation"] is False


def test_peak_valley_inflection_short_triggers_even_when_kc_middle_rising():
    df = _make_inflection_frame(side="SHORT", kc_width_pct=0.02, ma_spread_pct=0.005, adx=28.0)
    res = evaluate_peak_valley_inflection_entry(df)
    assert res["action"] == "OPEN_SHORT"
    assert res["side"] == "SHORT"
    assert res["kc_direction"] == "UP"
    assert res["is_consolidation"] is False


def test_peak_valley_consolidation_filter_blocks_all_three_conditions_and_is_tunable():
    # 1. Narrow KC width
    df_narrow = _make_inflection_frame(side="LONG", kc_width_pct=0.004, ma_spread_pct=0.005, adx=28.0)
    res_narrow = evaluate_peak_valley_inflection_entry(df_narrow)
    assert res_narrow["action"] == "HOLD"
    assert res_narrow["reason"] == "CONSOLIDATION_KC_WIDTH_TOO_NARROW"
    assert res_narrow["is_consolidation"] is True

    # 2. MA5 / MA15 entangled (without trend-aligned spread exemption)
    df_entangled = _make_inflection_frame(side="SHORT", kc_width_pct=0.02, ma_spread_pct=0.0004, adx=28.0)
    res_entangled = evaluate_peak_valley_inflection_entry(df_entangled, enable_inflection_ma_spread_exemption=False)
    assert res_entangled["action"] == "HOLD"
    assert res_entangled["reason"] == "CONSOLIDATION_MA_ENTANGLED"

    # 3. Low ADX
    df_low_adx = _make_inflection_frame(side="LONG", kc_width_pct=0.02, ma_spread_pct=0.005, adx=15.0)
    res_low_adx = evaluate_peak_valley_inflection_entry(df_low_adx)
    assert res_low_adx["action"] == "HOLD"
    assert res_low_adx["reason"] == "CONSOLIDATION_LOW_ADX"

    # Tunable override allows trading when thresholds are explicitly relaxed
    custom_cfg = PeakValleyEntryConfig(min_kc_width_pct=0.003, min_adx=12.0)
    res_tuned = evaluate_peak_valley_inflection_entry(df_low_adx, config=custom_cfg)
    assert res_tuned["action"] == "OPEN_LONG"


def test_central_entry_gate_accepts_meme_gate_entry_short_below_bb_lower(monkeypatch):
    from types import SimpleNamespace
    from core.gates.central_entry_gate import CentralEntryGate

    now_min = int(time.time() // 60)
    rows = []
    for i in range(25):
        close = 100.0 - i * 0.6
        kc_mid = close + 0.5
        rows.append({
            "timestamp": (now_min - 25 + i) * 60_000,
            "open": close + 0.25,
            "high": close + 0.35,
            "low": close - 0.1,
            "close": close,
            "volume": 3000.0,
            "kc_middle": kc_mid,
            "kc_upper": kc_mid + 1.5,
            "kc_lower": kc_mid - 0.4,
            "bb_upper": kc_mid + 1.5,
            "bb_lower": close + 0.8,  # close < bb_lower - 0.5 * atr
            "ma5": close + 0.2,
            "ma15": kc_mid + 0.2,
            "atr": 1.0,
            "adx": 30.0,
            "is_closed": True,
        })
    df = pd.DataFrame(rows)
    df.attrs["timeframe_ms"] = 60_000.0
    df.attrs["snapshot_ms"] = time.time() * 1000.0
    df.attrs["entry_method_context"] = {
        "entry_method_authority": "MEME_GATE_ENTRY",
        "entry_method_signal": {"side": "SHORT", "event_id": "evt-1"},
    }
    monkeypatch.setattr("core.services.entry_methods.revalidate_entry_method", lambda *_a, **_k: (True, "ENTRY_METHOD_REVALIDATED:MEME_GATE_ENTRY"))
    monkeypatch.setattr("core.gates.central_entry_gate.CentralEntryGate._extract_market_metrics", staticmethod(lambda _frame, _price, allow_intrabar=False: {
        "valid": True,
        "current_price": float(df.iloc[-1]["close"]),
        "close": float(df.iloc[-1]["close"]),
        "open": float(df.iloc[-1]["open"]),
        "high": float(df.iloc[-1]["high"]),
        "low": float(df.iloc[-1]["low"]),
        "kc_middle": float(df.iloc[-1]["kc_middle"]),
        "kc_middle_prev": float(df.iloc[-2]["kc_middle"]),
        "kc_middle_slope": float(df.iloc[-1]["kc_middle"] - df.iloc[-2]["kc_middle"]),
        "kc_upper": float(df.iloc[-1]["kc_upper"]),
        "kc_lower": float(df.iloc[-1]["kc_lower"]),
        "bb_lower": float(df.iloc[-1]["bb_lower"]),
        "ma5_curr": float(df.iloc[-1]["ma5"]),
        "ma5_prev": float(df.iloc[-2]["ma5"]),
        "ma15_curr": float(df.iloc[-1]["ma15"]),
        "ma15_prev": float(df.iloc[-2]["ma15"]),
        "atr": 1.0,
        "volume": 3000.0,
        "volume_avg": 1000.0,
        "min_prev_width": 1.0,
        "max_prev_width": 2.0,
        "is_closed": True,
    }))
    monkeypatch.setattr("core.services.market_consolidation_filter.evaluate_market_blacklist", lambda *_a, **_k: (False, "PASS_MARKET_BLACKLIST", {}))
    monkeypatch.setattr("core.gates.master_gate_funnel.evaluate_meme_entry_frame", lambda *_a, **_k: SimpleNamespace(can_execute=True, reason="OK", details={}))
    monkeypatch.setattr("core.gates.master_gate_funnel.MasterGateFunnel.priority_channel", staticmethod(lambda *_a, **_k: "TRIGGER_TEST"))
    res = CentralEntryGate.verify_and_authorize(
        symbol="1000PEPE/USDT",
        side="SHORT",
        price=float(df.iloc[-1]["close"]),
        klines_or_snapshot=df,
        source="UNIFIED_PIPELINE|ENTRY_METHOD|MEME_GATE_ENTRY",
    )
    assert res.reason != "VETO_CHASING_SHORT_BELOW_MIDLINE"


def test_execution_manager_pipeline_opens_peak_short_when_kc_middle_rising(monkeypatch, tmp_path):
    import asyncio
    from core.paper_account import PaperAccount
    from core.services.data_pipeline import DataPipeline
    from core.services.execution_manager import ExecutionManager

    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
    monkeypatch.setattr("core.services.order_audit_trail.AUDIT_LOG_FILE", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(ExecutionManager, "_last_exit_record", {})
    monkeypatch.setattr(ExecutionManager, "_last_trade_bar_time", {})
    monkeypatch.setattr(ExecutionManager, "_hourly_entry_records", {})

    account = PaperAccount()
    account.balance = account.available_balance = 200.0
    frame = _make_inflection_frame(side="SHORT", kc_width_pct=0.025, ma_spread_pct=0.001, adx=30.0)
    # Place peak reversal high near/within 2 ATR of kc_upper and open/close valid
    quote = float(frame.iloc[-1]["close"])
    account.entry_frame_provider = lambda *_a, **_k: frame
    snapshot = DataPipeline.calculate_snapshot("ETH/USDT", frame, current_price=quote)
    assert snapshot is not None
    snapshot.raw_df = frame
    snapshot.curr_k = frame.iloc[-1].to_dict()
    snapshot.prev_k = frame.iloc[-2].to_dict()
    monkeypatch.setattr(DataPipeline, "calculate_snapshot", staticmethod(lambda *_a, **_k: snapshot))

    res = asyncio.run(ExecutionManager.process_symbol_pipeline(
        "ETH/USDT", account, klines_df=frame, quote=quote,
    ))
    assert res["status"] == "ENTRY_EXECUTED", "\n".join(str(x.get("text")) for x in account.logs[-8:])
    assert "ETH/USDT" in account.positions
    assert account.positions["ETH/USDT"]["side"] == "SHORT"


def test_meme_gate_entry_failure_releases_bar_claim(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from core.services.entry_methods import MEME_GATE_ENTRY
    from core.services.execution_manager import ExecutionManager

    frame = _make_inflection_frame(side="SHORT", kc_width_pct=0.025, ma_spread_pct=0.001, adx=30.0)
    bar_id = int(frame.iloc[-1]["timestamp"])
    symbol = "1000PEPE/USDT"
    signal = {
        "side": "SHORT",
        "method": MEME_GATE_ENTRY,
        "event_id": "evt-meme-1",
        "confirmation_bar_id": bar_id,
        "last_exit_timestamp": None,
        "entry_pattern": "BREAKOUT",
        "structure_confirmation": "CONFIRMED_MEME_GATE_TRIGGER",
        "channel_expanding": True,
        "entry_atr": float(frame.iloc[-2]["atr"]),
    }

    monkeypatch.setattr("core.services.entry_methods.evaluate_entry_method", lambda *_a, **_k: signal)
    monkeypatch.setattr("core.services.entry_methods.revalidate_entry_method", lambda *_a, **_k: (True, "OK"))
    monkeypatch.setattr(ExecutionManager, "_last_exit_record", {})
    monkeypatch.setattr(ExecutionManager, "_last_trade_bar_time", {})
    monkeypatch.setattr(ExecutionManager, "_hourly_entry_records", {})
    monkeypatch.setattr(ExecutionManager, "_last_open_error", {})

    async def fail_extension(**kwargs):
        ExecutionManager._last_open_error[kwargs["symbol"]] = (
            "[FORBIDDEN_ENTRY] BLOCKED_SHORT_MA15_EXTENSION_1_ATR"
        )
        return False

    submit = AsyncMock(side_effect=fail_extension)
    monkeypatch.setattr(ExecutionManager, "open_position", submit)
    account = SimpleNamespace(
        positions={},
        entry_filter_last={"strict_ma3_ma15_last_trade_bar": {}},
        last_closed_at={},
        save_state=Mock(),
    )

    first = asyncio.run(ExecutionManager._evaluate_closed_entry_methods(
        symbol=symbol,
        account=account,
        engine_or_account=account,
        frame=frame,
        quote=float(frame.iloc[-1]["close"]),
        macro_trend="BEARISH",
        only_method=MEME_GATE_ENTRY,
    ))
    assert first is not None and first["reason"] == "ENTRY_METHOD_FINAL_GATE_VETO"
    assert symbol not in account.entry_filter_last["strict_ma3_ma15_last_trade_bar"]
    assert symbol not in ExecutionManager._last_trade_bar_time

    second = asyncio.run(ExecutionManager._evaluate_closed_entry_methods(
        symbol=symbol,
        account=account,
        engine_or_account=account,
        frame=frame,
        quote=float(frame.iloc[-1]["close"]),
        macro_trend="BEARISH",
        only_method=MEME_GATE_ENTRY,
    ))
    assert second is not None and second["reason"] == "ENTRY_METHOD_FINAL_GATE_VETO"
    assert submit.await_count == 2


def test_meme_gate_entry_final_veto_does_not_block_other_channels(monkeypatch, tmp_path):
    import asyncio
    import time
    from core.paper_account import PaperAccount
    from core.services.data_pipeline import DataPipeline
    from core.services.entry_methods import MA5_MA15_CROSS, MEME_GATE_ENTRY
    from core.services.execution_manager import ExecutionManager

    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
    monkeypatch.setattr("core.services.order_audit_trail.AUDIT_LOG_FILE", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(ExecutionManager, "_last_exit_record", {})
    monkeypatch.setattr(ExecutionManager, "_last_trade_bar_time", {})
    monkeypatch.setattr(ExecutionManager, "_hourly_entry_records", {})
    monkeypatch.setattr(ExecutionManager, "_last_open_error", {})

    account = PaperAccount()
    account.balance = account.available_balance = 200.0
    symbol = "1000PEPE/USDT"
    frame = _make_inflection_frame(side="SHORT", kc_width_pct=0.025, ma_spread_pct=0.005, adx=30.0)
    now_bar_ms = int((time.time() // 60) * 60_000) - 120_000
    for idx in range(len(frame)):
        frame.loc[frame.index[idx], "timestamp"] = now_bar_ms - (len(frame) - 1 - idx) * 60_000
        mid_v = 102.3 - idx * 0.1
        half_w = mid_v * 0.0125
        ma15_v = 99.9 if idx == len(frame) - 1 else 102.0 - idx * 0.08
        frame.loc[frame.index[idx], ["kc_middle", "kc_upper", "kc_lower", "ma15"]] = [
            mid_v, mid_v + half_w, mid_v - half_w, ma15_v,
        ]
        if idx < 19:
            ma5_v = ma15_v - 0.5
            frame.loc[frame.index[idx], ["ma5", "open", "close", "high", "low"]] = [
                ma5_v, ma5_v + 0.1, ma5_v - 0.1, ma5_v + 0.2, ma5_v - 0.2,
            ]
        elif idx == 19:
            frame.loc[frame.index[idx], ["ma5", "open", "close", "high", "low"]] = [
                100.0, 99.9, 100.1, 100.2, 99.8,
            ]
        elif idx == 20:
            frame.loc[frame.index[idx], ["ma5", "open", "close", "high", "low"]] = [
                100.2, 100.0, 100.3, 100.4, 99.9,
            ]
        else:
            frame.loc[frame.index[idx], ["ma5", "open", "close", "high", "low"]] = [
                99.7, 100.4, 99.2, 100.5, 99.1,
            ]
    frame.attrs["entry_finality_verified"] = True
    frame.attrs["macro_trend"] = "BEARISH"
    frame.attrs["snapshot_ms"] = int(time.time() * 1000) - 10_000
    bar_id = int(frame.iloc[-1]["timestamp"])
    quote = float(frame.iloc[-1]["close"])

    async def _provider(*_a, **_k):
        return frame

    account.entry_frame_provider = _provider

    snapshot = DataPipeline.calculate_snapshot(symbol, frame, current_price=quote)
    assert snapshot is not None
    snapshot.raw_df = frame
    snapshot.curr_k = frame.iloc[-1].to_dict()
    snapshot.prev_k = frame.iloc[-2].to_dict()
    monkeypatch.setattr(DataPipeline, "calculate_snapshot", staticmethod(lambda *_a, **_k: snapshot))

    meme_signal = {
        "side": "SHORT",
        "method": MEME_GATE_ENTRY,
        "event_id": "evt-meme-veto",
        "confirmation_bar_id": bar_id,
        "last_exit_timestamp": None,
        "entry_pattern": "BREAKOUT",
        "structure_confirmation": "CONFIRMED_MEME_GATE_TRIGGER",
        "channel_expanding": True,
        "entry_atr": float(frame.iloc[-2]["atr"]),
    }
    cross_signal = {
        "side": "SHORT",
        "method": MA5_MA15_CROSS,
        "event_id": "evt-cross-fallback",
        "confirmation_bar_id": bar_id,
        "last_exit_timestamp": None,
        "entry_pattern": "CROSSOVER",
        "structure_confirmation": "MA_CROSSOVER",
        "channel_expanding": True,
        "entry_atr": float(frame.iloc[-2]["atr"]),
    }

    def fake_evaluate(df, **kwargs):
        if kwargs.get("expected_method") == MEME_GATE_ENTRY:
            return meme_signal
        if kwargs.get("expected_method") == MA5_MA15_CROSS:
            return cross_signal
        return None

    monkeypatch.setattr("core.services.entry_methods.evaluate_entry_method", fake_evaluate)
    monkeypatch.setattr("core.services.entry_methods.revalidate_entry_method", lambda *_a, **_k: (True, "OK"))

    real_open = ExecutionManager.open_position
    meme_attempts = []
    other_attempts = []

    async def open_wrapper(*args, **kwargs):
        meta = kwargs.get("meta") or {}
        authority = meta.get("entry_method_authority")
        if authority == MEME_GATE_ENTRY:
            meme_attempts.append(authority)
            ExecutionManager._last_open_error[symbol] = (
                "[FORBIDDEN_ENTRY] BLOCKED_SHORT_MA15_EXTENSION_1_ATR"
            )
            return False
        other_attempts.append(authority)
        return await real_open(*args, **kwargs)

    monkeypatch.setattr(ExecutionManager, "open_position", open_wrapper)

    res = asyncio.run(ExecutionManager.process_symbol_pipeline(
        symbol, account, klines_df=frame, quote=quote,
    ))
    assert len(meme_attempts) == 1
    assert other_attempts == [MA5_MA15_CROSS]
    assert res["status"] == "ENTRY_EXECUTED", "\n".join(str(x.get("text")) for x in account.logs[-8:])
    assert symbol in account.positions
    assert account.positions[symbol]["side"] == "SHORT"
    assert account.positions[symbol]["entry_method_authority"] == MA5_MA15_CROSS


def test_pepe_symbol_specific_entry_and_exit_config_resolution():
    from core.services.low_latency_entry import (
        DEFAULT_PEAK_VALLEY_CONFIG,
        LOBSTER_PEAK_VALLEY_CONFIG,
        PEPE_PEAK_VALLEY_CONFIG,
        resolve_peak_valley_entry_config,
    )
    from core.services.low_latency_exit import (
        DEFAULT_PEAK_VALLEY_EXIT_CONFIG,
        LOBSTER_PEAK_VALLEY_EXIT_CONFIG,
        PEPE_PEAK_VALLEY_EXIT_CONFIG,
        resolve_peak_valley_exit_config,
    )

    assert resolve_peak_valley_entry_config("1000PEPE/USDT") == PEPE_PEAK_VALLEY_CONFIG
    assert PEPE_PEAK_VALLEY_CONFIG.min_kc_width_pct == pytest.approx(0.0035)
    assert PEPE_PEAK_VALLEY_CONFIG.min_ma_spread_pct == pytest.approx(0.0007)
    assert PEPE_PEAK_VALLEY_CONFIG.min_adx == pytest.approx(12.0)

    assert resolve_peak_valley_entry_config("龙虾/USDT") == LOBSTER_PEAK_VALLEY_CONFIG
    assert LOBSTER_PEAK_VALLEY_CONFIG.min_kc_width_pct == pytest.approx(0.0028)
    assert LOBSTER_PEAK_VALLEY_CONFIG.min_ma_spread_pct == pytest.approx(0.0004)
    assert LOBSTER_PEAK_VALLEY_CONFIG.min_adx == pytest.approx(15.0)
    assert resolve_peak_valley_entry_config("ETH/USDT") == DEFAULT_PEAK_VALLEY_CONFIG

    assert resolve_peak_valley_exit_config("1000PEPE/USDT") == PEPE_PEAK_VALLEY_EXIT_CONFIG
    assert PEPE_PEAK_VALLEY_EXIT_CONFIG.kc_collapse_width_pct == pytest.approx(0.0038)
    assert PEPE_PEAK_VALLEY_EXIT_CONFIG.normal_min_kc_width_pct == pytest.approx(0.0048)
    assert PEPE_PEAK_VALLEY_EXIT_CONFIG.flip_min_kc_width_pct == pytest.approx(0.0075)
    assert PEPE_PEAK_VALLEY_EXIT_CONFIG.flip_min_adx == pytest.approx(16.0)

    assert resolve_peak_valley_exit_config("龙虾/USDT") == LOBSTER_PEAK_VALLEY_EXIT_CONFIG
    assert LOBSTER_PEAK_VALLEY_EXIT_CONFIG.kc_collapse_width_pct == pytest.approx(0.0022)
    assert LOBSTER_PEAK_VALLEY_EXIT_CONFIG.normal_min_kc_width_pct == pytest.approx(0.0028)
    assert LOBSTER_PEAK_VALLEY_EXIT_CONFIG.flip_min_kc_width_pct == pytest.approx(0.0030)
    assert LOBSTER_PEAK_VALLEY_EXIT_CONFIG.flip_min_ma_spread_pct == pytest.approx(0.00045)
    assert LOBSTER_PEAK_VALLEY_EXIT_CONFIG.flip_min_adx == pytest.approx(18.0)
    assert resolve_peak_valley_exit_config("ETH/USDT") == DEFAULT_PEAK_VALLEY_EXIT_CONFIG


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_pepe_inflection_opens_at_058_kc_width_and_145_adx_while_eth_blocks(side):
    frame = _make_inflection_frame(side=side, kc_width_pct=0.0058, ma_spread_pct=0.0010, adx=14.5)

    pepe_res = evaluate_peak_valley_inflection_entry(
        frame, symbol="1000PEPE/USDT", require_directional_candle=True,
    )
    assert pepe_res["action"] == f"OPEN_{side}"
    assert pepe_res["is_consolidation"] is False

    eth_res = evaluate_peak_valley_inflection_entry(
        frame, symbol="ETH/USDT", require_directional_candle=True,
    )
    assert eth_res["action"] == "HOLD"
    assert eth_res["is_consolidation"] is True

    chop_frame = _make_inflection_frame(side=side, kc_width_pct=0.0035, ma_spread_pct=0.0010, adx=10.0)
    pepe_chop = evaluate_peak_valley_inflection_entry(
        chop_frame, symbol="1000PEPE/USDT", require_directional_candle=True,
    )
    assert pepe_chop["action"] == "HOLD"
    assert pepe_chop["is_consolidation"] is True


def test_pepe_flat_kc_and_below_mid_inflection_opens_end_to_end_through_execution_gates(monkeypatch, tmp_path):
    import asyncio
    from core.gates.central_entry_gate import CentralEntryGate
    from core.paper_account import PaperAccount
    from core.services.data_pipeline import DataPipeline
    from core.services.entry_firewall import ma5_ma15_entry_gate_problem, validate_account_entry
    from core.services.execution_manager import ExecutionManager
    from core.services.math_execution_gate import validate_entry_math

    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper.json"))
    monkeypatch.setattr("core.services.order_audit_trail.AUDIT_LOG_FILE", str(tmp_path / "audit.jsonl"))
    for attr in ("_last_exit_record", "_last_trade_bar_time", "_hourly_entry_records", "_pv_exit_records", "_pv_flip_records"):
        monkeypatch.setattr(ExecutionManager, attr, {})

    symbol = "1000PEPE/USDT"
    frame = _make_inflection_frame(side="LONG", kc_width_pct=0.0058, ma_spread_pct=0.0010, adx=14.5)
    # 構造 KC 中軌完全走平 + MA15 下行 + 價格在 KC 中軌之下（谷底反彈初期）
    frame["kc_middle"] = 101.20
    half_w = 101.20 * (0.0058 / 2.0)
    frame["kc_upper"] = 101.20 + half_w
    frame["kc_lower"] = 101.20 - half_w
    frame["ma15"] = [101.0 - i * 0.02 for i in range(len(frame))]
    # MA5 在倒數第 3~2 根持續向下且最後 2 根仍在 100 附近，保持谷底反彈在 KC 中軌之下且發散度 >= 0.0007
    last_ma15 = float(frame.iloc[-1]["ma15"])
    frame.loc[frame.index[-3], "ma5"] = last_ma15 - 0.10
    frame.loc[frame.index[-2], "ma5"] = last_ma15 - 0.15
    frame.loc[frame.index[-1], "ma5"] = last_ma15 - 0.08
    frame.loc[frame.index[-1], ["open", "close", "high", "low"]] = [
        last_ma15 - 0.16, last_ma15 - 0.02, last_ma15, last_ma15 - 0.18,
    ]
    frame.attrs.update(
        symbol=symbol,
        entry_finality_verified=True,
        is_peak_valley_inflection=True,
        macro_trend="BEARISH",
        snapshot_ms=int(time.time() * 1000) - 5_000,
    )
    quote = float(frame.iloc[-1]["close"])
    assert quote < float(frame.iloc[-1]["kc_middle"])

    # 1. entry_firewall / CentralEntryGate / math_execution_gate 皆必須帶 symbol=1000PEPE/USDT 通過
    assert ma5_ma15_entry_gate_problem(frame, "LONG", quote=quote, symbol=symbol) is None
    central_res = CentralEntryGate.verify_and_authorize(
        symbol=symbol,
        side="LONG",
        price=quote,
        klines_or_snapshot=frame,
        source="UNIFIED_PIPELINE|PEAK_VALLEY_INFLECTION_LONG",
    )
    assert central_res.passed is True, central_res.reason
    math_ok, math_reason = validate_entry_math(frame, "LONG", symbol=symbol)
    assert math_ok is True, math_reason

    account = PaperAccount()
    account.balance = account.available_balance = 200.0

    async def _provider(*_a, **_k):
        return frame

    account.entry_frame_provider = _provider
    fw_decision = asyncio.run(validate_account_entry(
        account,
        symbol,
        "LONG",
        {
            "signal_code": "PEAK_VALLEY_INFLECTION_LONG",
            "entry_type": "PEAK_VALLEY_INFLECTION_LONG",
            "is_peak_valley_inflection": True,
            "entry_gate_quote": quote,
        },
    ))
    assert fw_decision["entry_phase"] == "PEAK_VALLEY_INFLECTION"

    # 2. 完整 ExecutionManager pipeline（KC 走平下不得被 BLOCKED_ENTRY_KC_DIRECTION_FLAT 擋死）
    snapshot = DataPipeline.calculate_snapshot(symbol, frame, current_price=quote)
    assert snapshot is not None
    snapshot.raw_df = frame
    snapshot.curr_k = frame.iloc[-1].to_dict()
    snapshot.prev_k = frame.iloc[-2].to_dict()
    snapshot.kc_middle_slope = 0.0
    monkeypatch.setattr(DataPipeline, "calculate_snapshot", staticmethod(lambda *_a, **_k: snapshot))
    monkeypatch.setattr("core.services.entry_methods.evaluate_entry_method", lambda *_a, **_k: None)

    res = asyncio.run(ExecutionManager.process_symbol_pipeline(
        symbol, account, klines_df=frame, quote=quote,
    ))
    assert res["status"] == "ENTRY_EXECUTED", (
        f"res={res}, open_err={ExecutionManager._last_open_error.get(symbol)}"
    )
    assert symbol in account.positions
    assert account.positions[symbol]["side"] == "LONG"


def test_pepe_exit_does_not_false_collapse_at_normal_055_kc_width():
    from dataclasses import replace
    from core.services.low_latency_exit import evaluate_peak_valley_exit, resolve_peak_valley_exit_config

    df = _make_inflection_frame(side="LONG", kc_width_pct=0.0055, ma_spread_pct=0.0012, adx=16.0)
    # 模擬進場後微幅浮虧但 MA5 續揚（尚未反轉且 KC 寬度 0.55% 未縮破 PEPE 0.38% 崩塌線）
    df.loc[df.index[-1], "close"] = float(df.iloc[-1]["ma5"]) + 0.05
    pos = {
        "symbol": "1000PEPE/USDT",
        "side": "LONG",
        "entry_price": float(df.iloc[-1]["close"]) + 0.05,
        "leverage": 5.0,
        "entry_kc_width_pct": 0.0060,
        "is_peak_valley_inflection": True,
    }
    res_pepe = evaluate_peak_valley_exit(
        df,
        position=pos,
        live_price=float(df.iloc[-1]["close"]),
        symbol="1000PEPE/USDT",
        config=replace(resolve_peak_valley_exit_config("1000PEPE/USDT"), exit_mode="legacy_pv"),
    )
    assert res_pepe["should_exit"] is False
    assert res_pepe["reason"] == "HOLD_NO_EXIT_TRIGGER"

    res_eth = evaluate_peak_valley_exit(
        df,
        position={**pos, "symbol": "ETH/USDT"},
        live_price=float(df.iloc[-1]["close"]),
        symbol="ETH/USDT",
        config=replace(resolve_peak_valley_exit_config("ETH/USDT"), exit_mode="legacy_pv"),
    )
    assert res_eth["should_exit"] is True
    assert res_eth["reason"] == "ACTIVE_EXIT_PV_CONSOLIDATION_FAIL_SL"


def test_1000pepe_1319_range_box_chop_and_micro_noise_slope_blocked():
    """回歸測試：1000PEPE/USDT 13:19 橫盤箱體震盪＋微幅斜率雜訊不得觸發 PEAK_VALLEY_INFLECTION_SHORT，且 CentralEntryGate 不得繞過箱體低趨勢攔截。"""
    import numpy as np
    from core.gates.central_entry_gate import CentralEntryGate

    now_min = int(time.time() // 60)
    # 1. 窄幅橫盤箱體（振幅 ~0.65% < 1.2%，淨位移效率 < 0.25）-> CONSOLIDATION_RANGE_BOX_CHOP
    closes = [
        0.004560, 0.004558, 0.004557, 0.004563, 0.004565,
        0.004571, 0.004564, 0.004568, 0.004568, 0.004571,
        0.004577, 0.004571, 0.004562, 0.004558, 0.004562,
        0.004557, 0.004555, 0.004560, 0.004558, 0.004554,
        0.004557,
    ]
    rows = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i > 0 else c + 0.000001
        h = max(o, c) + 0.000004
        l = min(o, c) - 0.000004
        rows.append({
            "timestamp": (now_min - len(closes) + i) * 60_000,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 5000.0,
            "kc_upper": 0.004574,
            "kc_middle": 0.004562,
            "kc_lower": 0.004550,
            "ma15": 0.004564,
            "atr": 0.0000075,
            "adx": 18.2,
            "is_closed": i < len(closes) - 1,
        })
    df_box = pd.DataFrame(rows)
    df_box.attrs["timeframe_ms"] = 60_000.0
    res_box = evaluate_peak_valley_inflection_entry(df_box, live_price=0.004557, symbol="1000PEPE/USDT")
    assert res_box["action"] == "HOLD"
    assert res_box["reason"] == "CONSOLIDATION_RANGE_BOX_CHOP"
    assert res_box["is_consolidation"] is True

    # 2. 非箱體場景下，前一根 MA5 僅極微小正斜率 (+2e-8) 亦不得誤判為波峰反轉開空
    df_micro = _make_inflection_frame(side="SHORT", kc_width_pct=0.006, ma_spread_pct=0.0014, adx=21.0)
    df_micro.loc[df_micro.index[-3], "ma5"] = 100.0000
    df_micro.loc[df_micro.index[-2], "ma5"] = 100.0005  # +0.0005% (< 0.002% min_prev_turn_slope_pct)
    df_micro.loc[df_micro.index[-1], "ma5"] = 99.8500
    # 寬軌道隔離 kc_overbought_short，單獨驗證峰谷拐點微米斜率雜訊攔截
    df_micro["kc_upper"] = df_micro["kc_middle"] + 2.0
    df_micro["kc_lower"] = df_micro["kc_middle"] - 2.0
    res_micro = evaluate_peak_valley_inflection_entry(df_micro, symbol="1000PEPE/USDT")
    assert res_micro["action"] == "HOLD"

    # 3. CentralEntryGate 對 PEAK_VALLEY_INFLECTION 不得繞過 VETO_DONCHIAN_RANGE_LOW_TREND
    n = 25
    c_arr = 0.004560 + 0.000008 * np.sin(np.arange(n) / 2.2)
    gate_df = pd.DataFrame({
        "timestamp": [(now_min - n + i) * 60_000 for i in range(n)],
        "open": c_arr + 0.000001,
        "high": c_arr + 0.000012,
        "low": c_arr - 0.000012,
        "close": c_arr,
        "volume": np.array([500000.0] * (n - 1) + [900000.0]),
        "kc_middle": 0.004651 - np.arange(n) * 0.0000040,
        "kc_upper": (0.004651 - np.arange(n) * 0.0000040) + (0.000010 + np.arange(n) * 0.0000002),
        "kc_lower": (0.004651 - np.arange(n) * 0.0000040) - (0.000010 + np.arange(n) * 0.0000002),
        "ma5": c_arr - 0.000003,
        "ma15": np.full(n, 0.0045585),
        "atr": np.full(n, 0.000008),
        "adx": np.full(n, 17.5),
        "is_closed": True,
    })
    # Make last closed bar bearish inside Donchian box & falling MA5
    gate_df.loc[gate_df.index[-3], "high"] = float(gate_df.iloc[-1]["close"]) + 0.00003
    gate_df.loc[gate_df.index[-3], "low"] = float(gate_df.iloc[-1]["close"]) - 0.00003
    gate_df.loc[gate_df.index[-2], "ma5"] = float(gate_df.iloc[-1]["ma5"]) + 0.000005
    gate_df.loc[gate_df.index[-1], "open"] = float(gate_df.iloc[-1]["close"]) + 0.000004
    gate_df.loc[gate_df.index[-1], "high"] = float(gate_df.iloc[-1]["close"]) + 0.0000045
    gate_df.loc[gate_df.index[-1], "low"] = float(gate_df.iloc[-1]["close"]) - 0.0000005
    gate_df.attrs.update(
        symbol="1000PEPE/USDT",
        timeframe_ms=60_000.0,
        snapshot_ms=time.time() * 1000.0,
        entry_finality_verified=True,
        is_peak_valley_inflection=True,
    )
    res_gate = CentralEntryGate.verify_and_authorize(
        symbol="1000PEPE/USDT",
        side="SHORT",
        price=float(gate_df.iloc[-1]["close"]),
        klines_or_snapshot=gate_df,
        source="UNIFIED_PIPELINE|PEAK_VALLEY_INFLECTION_SHORT",
    )
    assert res_gate.passed is False
    assert res_gate.reason == "VETO_DONCHIAN_RANGE_LOW_TREND"


def _make_5m_trend_df(direction: str, bars: int = 25) -> pd.DataFrame:
    now_5m = int(time.time() // 300)
    if direction == "UP":
        closes = [100.0 + i * 0.25 for i in range(bars)]
    elif direction == "DOWN":
        closes = [110.0 - i * 0.25 for i in range(bars)]
    else:
        closes = [100.0 + (0.01 if i % 2 == 0 else -0.01) for i in range(bars)]
    return pd.DataFrame({
        "timestamp": [(now_5m - bars + i) * 300_000 for i in range(bars)],
        "open": [c - 0.05 for c in closes],
        "high": [c + 0.15 for c in closes],
        "low": [c - 0.15 for c in closes],
        "close": closes,
        "volume": [5000.0] * bars,
        "is_closed": [True] * bars,
    })


def test_symmetric_first_buy_inflection_exempts_ma_spread_when_trend_aligned():
    """第一買點（峰谷拐點）多空對稱豁免 min_ma_spread_pct：5M 順勢放行，5M 逆勢仍被 MA 糾纏攔截。"""
    # LONG 谷底轉向 + MA5/MA15 距離極近 (0.01% < 0.04% 门檻)
    df_long = _make_inflection_frame(side="LONG", kc_width_pct=0.006, ma_spread_pct=0.0001, adx=22.0)
    df_5m_up = _make_5m_trend_df("UP")
    df_5m_down = _make_5m_trend_df("DOWN")

    res_long_pass = evaluate_peak_valley_inflection_entry(
        df_long, symbol="龙虾/USDT", df_5m=df_5m_up, require_directional_candle=True,
    )
    assert res_long_pass["action"] == "OPEN_LONG"
    assert res_long_pass["ma_spread_exempted"] is True

    # 5M 空頭且無前段上漲回踩 KC 下軌 -> 不得豁免，維持 CONSOLIDATION_MA_ENTANGLED
    df_long_no_edge = df_long.copy()
    df_long_no_edge["low"] = df_long_no_edge["kc_middle"]
    df_long_no_edge["kc_lower"] = df_long_no_edge["kc_middle"] - 0.8
    res_long_block = evaluate_peak_valley_inflection_entry(
        df_long_no_edge, symbol="龙虾/USDT", df_5m=df_5m_down, require_directional_candle=True,
    )
    assert res_long_block["action"] == "HOLD"
    assert res_long_block["reason"] == "CONSOLIDATION_MA_ENTANGLED"

    # SHORT 波峰轉向 + MA5/MA15 距離極近 (0.01% < 0.04% 门檻)
    df_short = _make_inflection_frame(side="SHORT", kc_width_pct=0.006, ma_spread_pct=0.0001, adx=22.0)
    res_short_pass = evaluate_peak_valley_inflection_entry(
        df_short, symbol="龙虾/USDT", df_5m=df_5m_down, require_directional_candle=True,
    )
    assert res_short_pass["action"] == "OPEN_SHORT"
    assert res_short_pass["ma_spread_exempted"] is True

    df_short_no_edge = df_short.copy()
    df_short_no_edge["high"] = df_short_no_edge["kc_middle"]
    df_short_no_edge["kc_upper"] = df_short_no_edge["kc_middle"] + 0.8
    res_short_block = evaluate_peak_valley_inflection_entry(
        df_short_no_edge, symbol="龙虾/USDT", df_5m=df_5m_up, require_directional_candle=True,
    )
    assert res_short_block["action"] == "HOLD"
    assert res_short_block["reason"] == "CONSOLIDATION_MA_ENTANGLED"


def test_symmetric_second_buy_kc_mid_breakout_and_channel_expansion():
    """第二買點（強勢突破/跌破 KC 中軌 + 通道擴張 + 5M 不逆勢）多空完全對稱。"""
    now_min = int(time.time() // 60)
    n = 25

    def _build_second_buy_df(side: str, expanding: bool = True) -> pd.DataFrame:
        closes = np.full(n, 100.0)
        kc_mid = np.full(n, 100.0)
        ma15 = np.full(n, 100.0)
        ma5 = np.full(n, 100.0)
        kc_half = np.full(n, 0.50)
        if expanding:
            kc_half[-1] = 0.65  # width = 1.30 > avg(1.00, 1.00)
        if side == "LONG":
            closes[-3] = 99.8
            closes[-2] = 99.9
            closes[-1] = 100.40
            opens = closes - 0.15
            opens[-1] = 99.95
            # 持續向上而非拐點（prev_ma5_slope >= 0 且 curr_ma5_slope > 0）
            ma5[-3] = 99.95
            ma5[-2] = 100.00
            ma5[-1] = 100.15
            ma15[-1] = 100.02
        else:
            closes[-3] = 100.2
            closes[-2] = 100.1
            closes[-1] = 99.60
            opens = closes + 0.15
            opens[-1] = 100.05
            ma5[-3] = 100.05
            ma5[-2] = 100.00
            ma5[-1] = 99.85
            ma15[-1] = 99.98
        return pd.DataFrame({
            "timestamp": [(now_min - n + i) * 60_000 for i in range(n)],
            "open": opens,
            "high": np.maximum(opens, closes) + 0.05,
            "low": np.minimum(opens, closes) - 0.05,
            "close": closes,
            "volume": [5000.0] * n,
            "kc_middle": kc_mid,
            "kc_upper": kc_mid + kc_half,
            "kc_lower": kc_mid - kc_half,
            "ma5": ma5,
            "ma15": ma15,
            "adx": [24.0] * n,
            "is_closed": [True] * n,
        })

    df_5m_flat = _make_5m_trend_df("FLAT")
    df_5m_down = _make_5m_trend_df("DOWN")
    df_5m_up = _make_5m_trend_df("UP")

    # LONG 第二買點通過
    res_l = evaluate_peak_valley_inflection_entry(
        _build_second_buy_df("LONG", expanding=True), symbol="龙虾/USDT", df_5m=df_5m_flat, require_directional_candle=True,
    )
    assert res_l["action"] == "OPEN_LONG"
    assert res_l["trigger_mode"] == "KC_MID_BREAKOUT_EXPANSION"

    # SHORT 第二買點通過
    res_s = evaluate_peak_valley_inflection_entry(
        _build_second_buy_df("SHORT", expanding=True), symbol="龙虾/USDT", df_5m=df_5m_flat, require_directional_candle=True,
    )
    assert res_s["action"] == "OPEN_SHORT"
    assert res_s["trigger_mode"] == "KC_MID_BREAKDOWN_EXPANSION"

    # 通道未擴張 -> 拒絕
    assert evaluate_peak_valley_inflection_entry(
        _build_second_buy_df("LONG", expanding=False), symbol="龙虾/USDT", df_5m=df_5m_flat, require_directional_candle=True,
    )["action"] == "HOLD"
    assert evaluate_peak_valley_inflection_entry(
        _build_second_buy_df("SHORT", expanding=False), symbol="龙虾/USDT", df_5m=df_5m_flat, require_directional_candle=True,
    )["action"] == "HOLD"

    # 5M 明確逆勢 -> 拒絕
    assert evaluate_peak_valley_inflection_entry(
        _build_second_buy_df("LONG", expanding=True), symbol="龙虾/USDT", df_5m=df_5m_down, require_directional_candle=True,
    )["action"] == "HOLD"
    assert evaluate_peak_valley_inflection_entry(
        _build_second_buy_df("SHORT", expanding=True), symbol="龙虾/USDT", df_5m=df_5m_up, require_directional_candle=True,
    )["action"] == "HOLD"


def test_extreme_pivot_reversal_strategy_long_and_short():
    """驗證【通道極值區峰谷反轉策略】：峰頂轉向開空、谷底轉向開多，且過濾地板空與天花板多。"""
    now_min = int(time.time() // 60)
    n = 25

    # 1. 峰頂開空：最近 3 根曾衝高觸及 KC 上軌極值區 (>= kc_mid + 0.75 * half_width)
    #    當前 1M 收陰跌破 MA5，MA5 由正轉負下彎，且當前價格在 KC 中軌附近或上方 (kc_pos >= 0.45)
    c_s = np.full(n, 100.0)
    h_s = np.full(n, 100.2)
    l_s = np.full(n, 99.8)
    o_s = np.full(n, 100.0)
    m5_s = np.full(n, 100.0)

    # 倒數第 2 根衝到上軌極值區：kc_mid=100.0, kc_upper=101.0 -> 0.75 極值門檻 = 100.75
    h_s[-2] = 100.85
    c_s[-2] = 100.70
    o_s[-2] = 100.10
    m5_s[-3] = 100.20
    m5_s[-2] = 100.50  # 前一根正斜率 +0.30

    # 當前根收陰跌破 MA5，MA5 下彎至 100.40 (-0.10)
    o_s[-1] = 100.50
    c_s[-1] = 100.30
    h_s[-1] = 100.55
    l_s[-1] = 100.25
    m5_s[-1] = 100.40  # curr_ma5_slope = -0.10 < 0
    # kc_pos: (100.30 - 99.0) / 2.0 = 0.65 >= 0.45 (在中軌上方，空間充足)

    df_peak = pd.DataFrame({
        "timestamp": [(now_min - n + i) * 60_000 for i in range(n)],
        "open": o_s,
        "high": h_s,
        "low": l_s,
        "close": c_s,
        "volume": [5000.0] * n,
        "kc_middle": [100.0] * n,
        "kc_upper": [101.0] * n,
        "kc_lower": [99.0] * n,
        "ma5": m5_s,
        "ma15": [100.0] * n,  # ma_spread = 0.04% < 0.12% 盤整門檻，但極值反轉應成功豁免
        "adx": [25.0] * n,
        "is_closed": [True] * n,
    })

    res_peak = evaluate_peak_valley_inflection_entry(df_peak, symbol="龙虾/USDT", require_directional_candle=True)
    assert res_peak["action"] == "OPEN_SHORT"
    assert res_peak["trigger_mode"] == "EXTREME_PEAK_REVERSAL"
    assert res_peak["touched_upper_extreme"] is True

    # 若在地板追空 (kc_pos < 0.45，如 close=99.30) -> 安全空間不足，拒絕開空
    df_peak_floor = df_peak.copy()
    # 讓 ma15 貼近 ma5 形成均線糾纏 (0.02% < 0.04%)，使得普通 peak_inflection 被糾纏擋下，僅能靠極值反轉
    # 但極值反轉因 kc_pos < 0.45 拒絕開空，故最終為 HOLD
    df_peak_floor.loc[df_peak_floor.index[-1], ["open", "close", "high", "low"]] = [99.50, 99.30, 99.55, 99.25]
    df_peak_floor.loc[df_peak_floor.index[-1], "ma5"] = 99.40
    df_peak_floor.loc[df_peak_floor.index[-1], "ma15"] = 99.41
    res_floor = evaluate_peak_valley_inflection_entry(df_peak_floor, symbol="龙虾/USDT", require_directional_candle=True)
    assert res_floor["action"] == "HOLD"

    # 2. 谷底開多：最近 3 根曾下探觸及 KC 下軌極值區 (<= kc_mid - 0.75 * half_width)
    #    當前 1M 收陽站上 MA5，MA5 由負轉正上勾，且當前價格在 KC 中軌附近或下方 (kc_pos <= 0.55)
    c_l = np.full(n, 100.0)
    h_l = np.full(n, 100.2)
    l_l = np.full(n, 99.8)
    o_l = np.full(n, 100.0)
    m5_l = np.full(n, 100.0)

    # 倒數第 2 根探底到下軌極值區：kc_mid=100.0, kc_lower=99.0 -> 0.75 極值門檻 = 99.25
    l_l[-2] = 99.15
    c_l[-2] = 99.30
    o_l[-2] = 99.90
    m5_l[-3] = 99.80
    m5_l[-2] = 99.50  # 前一根負斜率 -0.30

    # 當前根收陽站上 MA5，MA5 上勾至 99.60 (+0.10)
    o_l[-1] = 99.50
    c_l[-1] = 99.70
    h_l[-1] = 99.75
    l_l[-1] = 99.45
    m5_l[-1] = 99.60  # curr_ma5_slope = +0.10 > 0
    # kc_pos: (99.70 - 99.0) / 2.0 = 0.35 <= 0.55 (在中軌下方，空間充足)

    df_valley = pd.DataFrame({
        "timestamp": [(now_min - n + i) * 60_000 for i in range(n)],
        "open": o_l,
        "high": h_l,
        "low": l_l,
        "close": c_l,
        "volume": [5000.0] * n,
        "kc_middle": [100.0] * n,
        "kc_upper": [101.0] * n,
        "kc_lower": [99.0] * n,
        "ma5": m5_l,
        "ma15": [100.0] * n,  # ma_spread 糾纏，但極值反轉應成功豁免
        "adx": [25.0] * n,
        "is_closed": [True] * n,
    })

    res_valley = evaluate_peak_valley_inflection_entry(df_valley, symbol="1000PEPE/USDT", require_directional_candle=True)
    assert res_valley["action"] == "OPEN_LONG"
    assert res_valley["trigger_mode"] == "EXTREME_VALLEY_REVERSAL"
    assert res_valley["touched_lower_extreme"] is True

    # 若在天花板追多 (kc_pos > 0.55，如 close=100.70) -> 安全空間不足，拒絕開多
    df_valley_roof = df_valley.copy()
    # 讓 ma15 貼近 ma5 形成均線糾纏，使得普通 valley_inflection 被糾纏擋下，僅能靠極值反轉
    # 但極值反轉因 kc_pos > 0.55 拒絕開多，故最終為 HOLD
    df_valley_roof.loc[df_valley_roof.index[-1], ["open", "close", "high", "low"]] = [100.50, 100.70, 100.75, 100.45]
    df_valley_roof.loc[df_valley_roof.index[-1], "ma5"] = 100.60
    df_valley_roof.loc[df_valley_roof.index[-1], "ma15"] = 100.59
    res_roof = evaluate_peak_valley_inflection_entry(df_valley_roof, symbol="1000PEPE/USDT", require_directional_candle=True)
    assert res_roof["action"] == "HOLD"


def test_1000pepe_1319_range_box_chop_and_micro_noise_slope_blocked():
    """回歸測試：1000PEPE/USDT 13:19 橫盤箱體震盪＋微幅斜率雜訊不得觸發 PEAK_VALLEY_INFLECTION_SHORT，且 CentralEntryGate 不得繞過箱體低趨勢攔截。"""
    import numpy as np
    from core.gates.central_entry_gate import CentralEntryGate

    now_min = int(time.time() // 60)
    # 1. 窄幅橫盤箱體（振幅 ~0.65% < 1.2%，淨位移效率 < 0.25）-> CONSOLIDATION_RANGE_BOX_CHOP
    closes = [
        0.004560, 0.004558, 0.004557, 0.004563, 0.004565,
        0.004571, 0.004564, 0.004568, 0.004568, 0.004571,
        0.004577, 0.004571, 0.004562, 0.004558, 0.004562,
        0.004557, 0.004555, 0.004560, 0.004558, 0.004554,
        0.004557,
    ]
    rows = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i > 0 else c + 0.000001
        h = max(o, c) + 0.000004
        l = min(o, c) - 0.000004
        rows.append({
            "timestamp": (now_min - len(closes) + i) * 60_000,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 5000.0,
            "kc_upper": 0.004574,
            "kc_middle": 0.004562,
            "kc_lower": 0.004550,
            "ma15": 0.004564,
            "atr": 0.0000075,
            "adx": 18.2,
            "is_closed": i < len(closes) - 1,
        })
    df_box = pd.DataFrame(rows)
    df_box.attrs["timeframe_ms"] = 60_000.0
    res_box = evaluate_peak_valley_inflection_entry(df_box, live_price=0.004557, symbol="1000PEPE/USDT")
    assert res_box["action"] == "HOLD"
    assert res_box["reason"] == "CONSOLIDATION_RANGE_BOX_CHOP"
    assert res_box["is_consolidation"] is True

    # 2. 非箱體場景下，前一根 MA5 僅極微小正斜率 (+2e-8) 亦不得誤判為波峰反轉開空
    df_micro = _make_inflection_frame(side="SHORT", kc_width_pct=0.006, ma_spread_pct=0.0014, adx=21.0)
    df_micro.loc[df_micro.index[-3], "ma5"] = 100.0000
    df_micro.loc[df_micro.index[-2], "ma5"] = 100.0005  # +0.0005% (< 0.002% min_prev_turn_slope_pct)
    df_micro.loc[df_micro.index[-1], "ma5"] = 99.8500
    df_micro["kc_upper"] = df_micro["kc_middle"] + 2.0
    df_micro["kc_lower"] = df_micro["kc_middle"] - 2.0
    res_micro = evaluate_peak_valley_inflection_entry(df_micro, symbol="1000PEPE/USDT")
    assert res_micro["action"] == "HOLD"

    # 3. CentralEntryGate 對 PEAK_VALLEY_INFLECTION 不得繞過 VETO_DONCHIAN_RANGE_LOW_TREND
    n = 25
    c_arr = 0.004560 + 0.000008 * np.sin(np.arange(n) / 2.2)
    gate_df = pd.DataFrame({
        "timestamp": [(now_min - n + i) * 60_000 for i in range(n)],
        "open": c_arr + 0.000001,
        "high": c_arr + 0.000012,
        "low": c_arr - 0.000012,
        "close": c_arr,
        "volume": np.array([500000.0] * (n - 1) + [900000.0]),
        "kc_middle": 0.004651 - np.arange(n) * 0.0000040,
        "kc_upper": (0.004651 - np.arange(n) * 0.0000040) + (0.000010 + np.arange(n) * 0.0000002),
        "kc_lower": (0.004651 - np.arange(n) * 0.0000040) - (0.000010 + np.arange(n) * 0.0000002),
        "ma5": c_arr - 0.000003,
        "ma15": np.full(n, 0.0045585),
        "atr": np.full(n, 0.000008),
        "adx": np.full(n, 17.5),
        "is_closed": True,
    })
    # Make last closed bar bearish inside Donchian box & falling MA5
    gate_df.loc[gate_df.index[-3], "high"] = float(gate_df.iloc[-1]["close"]) + 0.00003
    gate_df.loc[gate_df.index[-3], "low"] = float(gate_df.iloc[-1]["close"]) - 0.00003
    gate_df.loc[gate_df.index[-2], "ma5"] = float(gate_df.iloc[-1]["ma5"]) + 0.000005
    gate_df.loc[gate_df.index[-1], "open"] = float(gate_df.iloc[-1]["close"]) + 0.000004
    gate_df.loc[gate_df.index[-1], "high"] = float(gate_df.iloc[-1]["close"]) + 0.0000045
    gate_df.loc[gate_df.index[-1], "low"] = float(gate_df.iloc[-1]["close"]) - 0.0000005
    gate_df.attrs.update(
        symbol="1000PEPE/USDT",
        timeframe_ms=60_000.0,
        snapshot_ms=time.time() * 1000.0,
        entry_finality_verified=True,
        is_peak_valley_inflection=True,
    )
    res_gate = CentralEntryGate.verify_and_authorize(
        symbol="1000PEPE/USDT",
        side="SHORT",
        price=float(gate_df.iloc[-1]["close"]),
        klines_or_snapshot=gate_df,
        source="UNIFIED_PIPELINE|PEAK_VALLEY_INFLECTION_SHORT",
    )
    assert res_gate.passed is False
    assert res_gate.reason == "VETO_DONCHIAN_RANGE_LOW_TREND"


def _make_5m_trend_df(direction: str, bars: int = 25) -> pd.DataFrame:
    now_5m = int(time.time() // 300)
    if direction == "UP":
        closes = [100.0 + i * 0.25 for i in range(bars)]
    elif direction == "DOWN":
        closes = [110.0 - i * 0.25 for i in range(bars)]
    else:
        closes = [100.0 + (0.01 if i % 2 == 0 else -0.01) for i in range(bars)]
    return pd.DataFrame({
        "timestamp": [(now_5m - bars + i) * 300_000 for i in range(bars)],
        "open": [c - 0.05 for c in closes],
        "high": [c + 0.15 for c in closes],
        "low": [c - 0.15 for c in closes],
        "close": closes,
        "volume": [5000.0] * bars,
        "is_closed": [True] * bars,
    })


def test_symmetric_first_buy_inflection_exempts_ma_spread_when_trend_aligned():
    """第一買點（峰谷拐點）多空對稱豁免 min_ma_spread_pct：5M 順勢放行，5M 逆勢仍被 MA 糾纏攔截。"""
    # LONG 谷底轉向 + MA5/MA15 距離極近 (0.01% < 0.04% 门檻)
    df_long = _make_inflection_frame(side="LONG", kc_width_pct=0.006, ma_spread_pct=0.0001, adx=22.0)
    df_5m_up = _make_5m_trend_df("UP")
    df_5m_down = _make_5m_trend_df("DOWN")

    res_long_pass = evaluate_peak_valley_inflection_entry(
        df_long, symbol="龙虾/USDT", df_5m=df_5m_up, require_directional_candle=True,
    )
    assert res_long_pass["action"] == "OPEN_LONG"
    assert res_long_pass["ma_spread_exempted"] is True

    # 5M 空頭且無前段上漲回踩 KC 下軌 -> 不得豁免，維持 CONSOLIDATION_MA_ENTANGLED
    df_long_no_edge = df_long.copy()
    df_long_no_edge["low"] = df_long_no_edge["kc_middle"]
    df_long_no_edge["kc_lower"] = df_long_no_edge["kc_middle"] - 0.8
    res_long_block = evaluate_peak_valley_inflection_entry(
        df_long_no_edge, symbol="龙虾/USDT", df_5m=df_5m_down, require_directional_candle=True,
    )
    assert res_long_block["action"] == "HOLD"
    assert res_long_block["reason"] == "CONSOLIDATION_MA_ENTANGLED"

    # SHORT 波峰轉向 + MA5/MA15 距離極近 (0.01% < 0.04% 门檻)
    df_short = _make_inflection_frame(side="SHORT", kc_width_pct=0.006, ma_spread_pct=0.0001, adx=22.0)
    res_short_pass = evaluate_peak_valley_inflection_entry(
        df_short, symbol="龙虾/USDT", df_5m=df_5m_down, require_directional_candle=True,
    )
    assert res_short_pass["action"] == "OPEN_SHORT"
    assert res_short_pass["ma_spread_exempted"] is True

    df_short_no_edge = df_short.copy()
    df_short_no_edge["high"] = df_short_no_edge["kc_middle"]
    df_short_no_edge["kc_upper"] = df_short_no_edge["kc_middle"] + 0.8
    res_short_block = evaluate_peak_valley_inflection_entry(
        df_short_no_edge, symbol="龙虾/USDT", df_5m=df_5m_up, require_directional_candle=True,
    )
    assert res_short_block["action"] == "HOLD"
    assert res_short_block["reason"] == "CONSOLIDATION_MA_ENTANGLED"


def test_symmetric_second_buy_kc_mid_breakout_and_channel_expansion():
    """第二買點（強勢突破/跌破 KC 中軌 + 通道擴張 + 5M 不逆勢）多空完全對稱。"""
    now_min = int(time.time() // 60)
    n = 25

    def _build_second_buy_df(side: str, expanding: bool = True) -> pd.DataFrame:
        closes = np.full(n, 100.0)
        kc_mid = np.full(n, 100.0)
        ma15 = np.full(n, 100.0)
        ma5 = np.full(n, 100.0)
        kc_half = np.full(n, 0.50)
        if expanding:
            kc_half[-1] = 0.65  # width = 1.30 > avg(1.00, 1.00)
        if side == "LONG":
            closes[-3] = 99.8
            closes[-2] = 99.9
            closes[-1] = 100.40
            opens = closes - 0.15
            opens[-1] = 99.95
            # 持續向上而非拐點（prev_ma5_slope >= 0 且 curr_ma5_slope > 0）
            ma5[-3] = 99.95
            ma5[-2] = 100.00
            ma5[-1] = 100.15
            ma15[-1] = 100.02
        else:
            closes[-3] = 100.2
            closes[-2] = 100.1
            closes[-1] = 99.60
            opens = closes + 0.15
            opens[-1] = 100.05
            ma5[-3] = 100.05
            ma5[-2] = 100.00
            ma5[-1] = 99.85
            ma15[-1] = 99.98
        return pd.DataFrame({
            "timestamp": [(now_min - n + i) * 60_000 for i in range(n)],
            "open": opens,
            "high": np.maximum(opens, closes) + 0.05,
            "low": np.minimum(opens, closes) - 0.05,
            "close": closes,
            "volume": [5000.0] * n,
            "kc_middle": kc_mid,
            "kc_upper": kc_mid + kc_half,
            "kc_lower": kc_mid - kc_half,
            "ma5": ma5,
            "ma15": ma15,
            "adx": [24.0] * n,
            "is_closed": [True] * n,
        })

    df_5m_flat = _make_5m_trend_df("FLAT")
    df_5m_down = _make_5m_trend_df("DOWN")
    df_5m_up = _make_5m_trend_df("UP")

    # LONG 第二買點通過
    res_l = evaluate_peak_valley_inflection_entry(
        _build_second_buy_df("LONG", expanding=True), symbol="龙虾/USDT", df_5m=df_5m_flat, require_directional_candle=True,
    )
    assert res_l["action"] == "OPEN_LONG"
    assert res_l["trigger_mode"] == "KC_MID_BREAKOUT_EXPANSION"

    # SHORT 第二買點通過
    res_s = evaluate_peak_valley_inflection_entry(
        _build_second_buy_df("SHORT", expanding=True), symbol="龙虾/USDT", df_5m=df_5m_flat, require_directional_candle=True,
    )
    assert res_s["action"] == "OPEN_SHORT"
    assert res_s["trigger_mode"] == "KC_MID_BREAKDOWN_EXPANSION"

    # 通道未擴張 -> 拒絕
    assert evaluate_peak_valley_inflection_entry(
        _build_second_buy_df("LONG", expanding=False), symbol="龙虾/USDT", df_5m=df_5m_flat, require_directional_candle=True,
    )["action"] == "HOLD"
    assert evaluate_peak_valley_inflection_entry(
        _build_second_buy_df("SHORT", expanding=False), symbol="龙虾/USDT", df_5m=df_5m_flat, require_directional_candle=True,
    )["action"] == "HOLD"

    # 5M 明確逆勢 -> 拒絕
    assert evaluate_peak_valley_inflection_entry(
        _build_second_buy_df("LONG", expanding=True), symbol="龙虾/USDT", df_5m=df_5m_down, require_directional_candle=True,
    )["action"] == "HOLD"
    assert evaluate_peak_valley_inflection_entry(
        _build_second_buy_df("SHORT", expanding=True), symbol="龙虾/USDT", df_5m=df_5m_up, require_directional_candle=True,
    )["action"] == "HOLD"


def test_extreme_pivot_reversal_strategy_long_and_short():
    """驗證【通道極值區峰谷反轉策略】：峰頂轉向開空、谷底轉向開多，且過濾地板空與天花板多。"""
    now_min = int(time.time() // 60)
    n = 25

    # 1. 峰頂開空：最近 3 根曾衝高觸及 KC 上軌極值區 (>= kc_mid + 0.75 * half_width)
    #    當前 1M 收陰跌破 MA5，MA5 由正轉負下彎，且當前價格在 KC 中軌附近或上方 (kc_pos >= 0.45)
    c_s = np.full(n, 100.0)
    h_s = np.full(n, 100.2)
    l_s = np.full(n, 99.8)
    o_s = np.full(n, 100.0)
    m5_s = np.full(n, 100.0)

    # 倒數第 2 根衝到上軌極值區：kc_mid=100.0, kc_upper=101.0 -> 0.75 極值門檻 = 100.75
    h_s[-2] = 100.85
    c_s[-2] = 100.70
    o_s[-2] = 100.10
    m5_s[-3] = 100.20
    m5_s[-2] = 100.50  # 前一根正斜率 +0.30

    # 當前根收陰跌破 MA5，MA5 下彎至 100.40 (-0.10)
    o_s[-1] = 100.50
    c_s[-1] = 100.30
    h_s[-1] = 100.55
    l_s[-1] = 100.25
    m5_s[-1] = 100.40  # curr_ma5_slope = -0.10 < 0
    # kc_pos: (100.30 - 99.0) / 2.0 = 0.65 >= 0.45 (在中軌上方，空間充足)

    df_peak = pd.DataFrame({
        "timestamp": [(now_min - n + i) * 60_000 for i in range(n)],
        "open": o_s,
        "high": h_s,
        "low": l_s,
        "close": c_s,
        "volume": [5000.0] * n,
        "kc_middle": [100.0] * n,
        "kc_upper": [101.0] * n,
        "kc_lower": [99.0] * n,
        "ma5": m5_s,
        "ma15": [100.0] * n,  # ma_spread = 0.04% < 0.12% 盤整門檻，但極值反轉應成功豁免
        "adx": [25.0] * n,
        "is_closed": [True] * n,
    })

    res_peak = evaluate_peak_valley_inflection_entry(df_peak, symbol="龙虾/USDT", require_directional_candle=True)
    assert res_peak["action"] == "OPEN_SHORT"
    assert res_peak["trigger_mode"] == "EXTREME_PEAK_REVERSAL"
    assert res_peak["touched_upper_extreme"] is True

    # 若在地板追空 (kc_pos < 0.45，如 close=99.30) -> 安全空間不足，拒絕開空
    df_peak_floor = df_peak.copy()
    # 讓 ma15 貼近 ma5 形成均線糾纏 (0.02% < 0.04%)，使得普通 peak_inflection 被糾纏擋下，僅能靠極值反轉
    # 但極值反轉因 kc_pos < 0.45 拒絕開空，故最終為 HOLD
    df_peak_floor.loc[df_peak_floor.index[-1], ["open", "close", "high", "low"]] = [99.50, 99.30, 99.55, 99.25]
    df_peak_floor.loc[df_peak_floor.index[-1], "ma5"] = 99.40
    df_peak_floor.loc[df_peak_floor.index[-1], "ma15"] = 99.41
    res_floor = evaluate_peak_valley_inflection_entry(df_peak_floor, symbol="龙虾/USDT", require_directional_candle=True)
    assert res_floor["action"] == "HOLD"

    # 2. 谷底開多：最近 3 根曾下探觸及 KC 下軌極值區 (<= kc_mid - 0.75 * half_width)
    #    當前 1M 收陽站上 MA5，MA5 由負轉正上勾，且當前價格在 KC 中軌附近或下方 (kc_pos <= 0.55)
    c_l = np.full(n, 100.0)
    h_l = np.full(n, 100.2)
    l_l = np.full(n, 99.8)
    o_l = np.full(n, 100.0)
    m5_l = np.full(n, 100.0)

    # 倒數第 2 根探底到下軌極值區：kc_mid=100.0, kc_lower=99.0 -> 0.75 極值門檻 = 99.25
    l_l[-2] = 99.15
    c_l[-2] = 99.30
    o_l[-2] = 99.90
    m5_l[-3] = 99.80
    m5_l[-2] = 99.50  # 前一根負斜率 -0.30

    # 當前根收陽站上 MA5，MA5 上勾至 99.60 (+0.10)
    o_l[-1] = 99.50
    c_l[-1] = 99.70
    h_l[-1] = 99.75
    l_l[-1] = 99.45
    m5_l[-1] = 99.60  # curr_ma5_slope = +0.10 > 0
    # kc_pos: (99.70 - 99.0) / 2.0 = 0.35 <= 0.55 (在中軌下方，空間充足)

    df_valley = pd.DataFrame({
        "timestamp": [(now_min - n + i) * 60_000 for i in range(n)],
        "open": o_l,
        "high": h_l,
        "low": l_l,
        "close": c_l,
        "volume": [5000.0] * n,
        "kc_middle": [100.0] * n,
        "kc_upper": [101.0] * n,
        "kc_lower": [99.0] * n,
        "ma5": m5_l,
        "ma15": [100.0] * n,  # ma_spread 糾纏，但極值反轉應成功豁免
        "adx": [25.0] * n,
        "is_closed": [True] * n,
    })

    res_valley = evaluate_peak_valley_inflection_entry(df_valley, symbol="1000PEPE/USDT", require_directional_candle=True)
    assert res_valley["action"] == "OPEN_LONG"
    assert res_valley["trigger_mode"] == "EXTREME_VALLEY_REVERSAL"
    assert res_valley["touched_lower_extreme"] is True

    # 若在天花板追多 (kc_pos > 0.55，如 close=100.70) -> 安全空間不足，拒絕開多
    df_valley_roof = df_valley.copy()
    # 讓 ma15 貼近 ma5 形成均線糾纏，使得普通 valley_inflection 被糾纏擋下，僅能靠極值反轉
    # 但極值反轉因 kc_pos > 0.55 拒絕開多，故最終為 HOLD
    df_valley_roof.loc[df_valley_roof.index[-1], ["open", "close", "high", "low"]] = [100.50, 100.70, 100.75, 100.45]
    df_valley_roof.loc[df_valley_roof.index[-1], "ma5"] = 100.60
    df_valley_roof.loc[df_valley_roof.index[-1], "ma15"] = 100.59
    res_roof = evaluate_peak_valley_inflection_entry(df_valley_roof, symbol="1000PEPE/USDT", require_directional_candle=True)
    assert res_roof["action"] == "HOLD"
