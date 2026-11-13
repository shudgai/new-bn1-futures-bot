"""Unit and integration tests for low-latency Peak/Valley exit and risk engine."""

import asyncio
import time

import pandas as pd
import pytest

from core.services.low_latency_exit import (
    PeakValleyExitConfig,
    can_reenter_after_pv_exit,
    clear_registered_channel_frames,
    evaluate_peak_valley_exit,
    execute_peak_valley_exit_and_conditional_flip,
    extract_entry_structure_context,
)


@pytest.fixture(autouse=True)
def _reset_global_channel_frames():
    clear_registered_channel_frames()
    yield
    clear_registered_channel_frames()


def _make_df(closes, *, is_last_closed=False, kc_width_pct=0.03):
    rows = []
    base_ts = int((time.time() // 60) * 60_000) - len(closes) * 60_000
    for i, c in enumerate(closes):
        o = closes[i - 1] if i > 0 else c
        h = max(o, c) + 0.05
        l = min(o, c) - 0.05
        mid = c
        half_w = mid * (kc_width_pct / 2.0)
        closed_flag = True if i < len(closes) - 1 else bool(is_last_closed)
        rows.append({
            "timestamp": base_ts + i * 60_000,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 2000.0,
            "kc_middle": mid,
            "kc_upper": mid + half_w,
            "kc_lower": mid - half_w,
            "ma15": mid,
            "atr": 0.4,
            "adx": 30.0,
            "is_closed": closed_flag,
        })
    df = pd.DataFrame(rows)
    df.attrs["timeframe_ms"] = 60_000.0
    df.attrs["entry_finality_verified"] = True
    df.attrs["snapshot_ms"] = int(time.time() * 1000) - 5_000
    return df


def test_reverse_ma5_inflection_exits_at_tick_speed_without_bar_close():
    # LONG: closes climbing through bar -2 (MA5 rising), live bar ticks down below live MA5
    closes_long = [100.0 + i * 0.2 for i in range(18)] + [103.6, 103.9, 103.85]
    df_long = _make_df(closes_long, is_last_closed=False)
    pos_long = {
        "side": "LONG",
        "entry_price": 102.0,
        "entry_swing_low": 99.0,
        "entry_kc_width_pct": 0.03,
    }
    res_long = evaluate_peak_valley_exit(
        df_long, position=pos_long, live_price=102.5, config=PeakValleyExitConfig(exit_mode="legacy_pv")
    )
    assert res_long["should_exit"] is True
    assert res_long["action"] == "CLOSE_LONG"
    assert res_long["reason"] == "ACTIVE_EXIT_PV_REVERSE_INFLECTION_LONG"
    assert res_long["trigger_level"] == "TICK"

    # SHORT: closes falling through bar -2 (MA5 falling), live bar ticks up above live MA5
    closes_short = [105.0 - i * 0.2 for i in range(18)] + [101.4, 101.1, 101.15]
    df_short = _make_df(closes_short, is_last_closed=False)
    pos_short = {
        "side": "SHORT",
        "entry_price": 103.0,
        "entry_swing_high": 106.0,
        "entry_kc_width_pct": 0.03,
    }
    res_short = evaluate_peak_valley_exit(
        df_short, position=pos_short, live_price=102.5, config=PeakValleyExitConfig(exit_mode="legacy_pv")
    )
    assert res_short["should_exit"] is True
    assert res_short["action"] == "CLOSE_SHORT"
    assert res_short["reason"] == "ACTIVE_EXIT_PV_REVERSE_INFLECTION_SHORT"
    assert res_short["trigger_level"] == "TICK"


def test_kc_outer_extreme_reversal_take_profit_prev_bar_and_retrace():
    closes = [100.0 + i * 0.05 for i in range(20)]
    df = _make_df(closes, is_last_closed=False, kc_width_pct=0.02)
    # Previous closed bar pushed through kc_upper (e.g., high=102.5 > kc_upper=102.0, low=100.9)
    df.loc[df.index[-2], ["high", "low", "kc_upper"]] = [102.5, 100.9, 102.0]
    df.loc[df.index[-1], "high"] = 102.6

    pos = {
        "side": "LONG",
        "entry_price": 100.5,
        "entry_swing_low": 99.0,
        "pv_peak_price": 102.6,
        "pv_touched_kc_upper": True,
    }
    cfg = PeakValleyExitConfig(
        trailing_activate_pct=0.05,  # isolate KC extreme rule
        kc_extreme_retrace_pct=0.004,
        exit_mode="legacy_pv",
    )
    # Without a 3-point pivot while trend is still intact inside channel -> MUST HOLD
    res_veto = evaluate_peak_valley_exit(df, position=pos, live_price=100.85, config=cfg)
    assert res_veto["should_exit"] is False
    assert res_veto["reason"] == "HOLD_TREND_INTACT_WITHOUT_3POINT_PIVOT"

    # Once closed bars form a confirmed 3-point pivot peak -> break previous bar low fires immediate TP
    df.loc[df.index[-4], ["high", "low", "open", "close"]] = [101.1, 100.7, 100.8, 100.9]
    df.loc[df.index[-3], ["high", "low", "open", "close", "kc_upper"]] = [102.5, 100.9, 101.2, 102.2, 102.0]
    df.loc[df.index[-2], ["high", "low", "open", "close"]] = [102.1, 100.9, 102.0, 101.05]
    res_break = evaluate_peak_valley_exit(df, position=pos, live_price=100.85, config=cfg)
    assert res_break["should_exit"] is True
    assert res_break["reason"] == "ACTIVE_EXIT_PV_KC_EXTREME_TP_LONG"
    assert res_break["trigger_level"] == "TICK"

    # Symmetric SHORT touching kc_lower and breaking previous high
    df_s = _make_df([102.0 - i * 0.05 for i in range(20)], is_last_closed=False, kc_width_pct=0.02)
    df_s.loc[df_s.index[-2], ["high", "low", "kc_lower"]] = [101.1, 99.5, 100.0]
    pos_s = {
        "side": "SHORT",
        "entry_price": 101.8,
        "entry_swing_high": 103.5,
        "pv_trough_price": 99.5,
        "pv_touched_kc_lower": True,
    }
    res_s_veto = evaluate_peak_valley_exit(df_s, position=pos_s, live_price=101.15, config=cfg)
    assert res_s_veto["should_exit"] is False
    assert res_s_veto["reason"] == "HOLD_TREND_INTACT_WITHOUT_3POINT_PIVOT"

    df_s.loc[df_s.index[-4], ["high", "low", "open", "close"]] = [101.3, 100.8, 101.2, 101.0]
    df_s.loc[df_s.index[-3], ["high", "low", "open", "close", "kc_lower"]] = [101.1, 99.5, 100.8, 99.8, 100.0]
    df_s.loc[df_s.index[-2], ["high", "low", "open", "close"]] = [101.1, 99.9, 100.0, 100.9]
    res_s = evaluate_peak_valley_exit(df_s, position=pos_s, live_price=101.15, config=cfg)
    assert res_s["should_exit"] is True
    assert res_s["reason"] == "ACTIVE_EXIT_PV_KC_EXTREME_TP_SHORT"
    assert res_s["trigger_level"] == "TICK"


def test_dynamic_trailing_profit_lock_giveback_and_retrace():
    closes = [100.0 + i * 0.04 for i in range(18)] + [100.4, 100.9]
    df = _make_df(closes, is_last_closed=False)
    cfg = PeakValleyExitConfig(
        trailing_activate_pct=0.008,       # +0.8%
        trailing_profit_giveback_ratio=0.30,  # 30% giveback of peak profit
        trailing_price_retrace_pct=0.005,     # keep giveback primary
        enable_kc_prev_bar_break=False,
        kc_extreme_retrace_pct=0.0,
        ma5_slope_eps=1.0,  # isolate trailing profit-lock behavior
        exit_mode="legacy_pv",
    )
    pos = {
        "side": "LONG",
        "entry_price": 100.0,
        "entry_swing_low": 98.0,
    }
    # Peak +1.0% (price 101.0) arms trailing
    res_arm = evaluate_peak_valley_exit(df, position=pos, live_price=101.0, config=cfg)
    assert res_arm["should_exit"] is False
    assert res_arm["state_updates"]["pv_trailing_armed"] is True

    # Pullback to 100.65 without 3-point pivot while trend is still intact inside channel -> MUST HOLD
    pos.update(res_arm["state_updates"])
    res_hold = evaluate_peak_valley_exit(df, position=pos, live_price=100.65, config=cfg)
    assert res_hold["should_exit"] is False
    assert res_hold["reason"] == "HOLD_TREND_INTACT_WITHOUT_3POINT_PIVOT"

    # Once closed bars confirm a 3-point pivot peak -> profit giveback >= 30% triggers lock!
    df.loc[df.index[-4], ["high", "low", "open", "close"]] = [100.80, 100.55, 100.60, 100.72]
    df.loc[df.index[-3], ["high", "low", "open", "close"]] = [101.05, 100.68, 100.72, 100.98]
    df.loc[df.index[-2], ["high", "low", "open", "close"]] = [100.98, 100.35, 100.98, 100.40]
    res_lock = evaluate_peak_valley_exit(df, position=pos, live_price=100.65, config=cfg)
    assert res_lock["should_exit"] is True
    assert res_lock["reason"] == "ACTIVE_EXIT_PV_TRAILING_PROFIT_LOCK"
    assert res_lock["trigger_level"] == "TICK"


def test_fake_inflection_swing_break_and_consolidation_collapse_stop_loss():
    closes = [100.0] * 14 + [100.1, 100.2, 100.3, 100.35, 100.4, 100.25]
    df = _make_df(closes, is_last_closed=True, kc_width_pct=0.005)
    pos = {
        "side": "LONG",
        "entry_price": 100.4,
        "entry_swing_low": 100.0,
        "entry_kc_width_pct": 0.02,
    }
    # 1. Tick breaks pre-entry swing low 100.0 beyond buffer -> fast swing break SL
    res_swing = evaluate_peak_valley_exit(df, position=pos, live_price=99.90, config=PeakValleyExitConfig(exit_mode="legacy_pv"))
    assert res_swing["should_exit"] is True
    assert res_swing["reason"] == "ACTIVE_EXIT_PV_FAKE_INFLECTION_SWING_BREAK"
    assert res_swing["trigger_level"] == "TICK"

    # 2. Price still above swing low (100.25), losing (-0.15%), KC shrunk to 0.005 on closed bar -> BAR_CLOSE SL
    cfg_sl = PeakValleyExitConfig(max_stop_loss_pct=0.05, exit_mode="legacy_pv")
    res_chop = evaluate_peak_valley_exit(df, position=pos, live_price=100.25, config=cfg_sl)
    assert res_chop["should_exit"] is True
    assert res_chop["reason"] == "ACTIVE_EXIT_PV_CONSOLIDATION_FAIL_SL"
    assert res_chop["trigger_level"] == "BAR_CLOSE"


def test_anti_whipsaw_blocks_same_bar_and_cooldown_reentry():
    cfg = PeakValleyExitConfig(same_bar_reverse_allowed=False, reentry_cooldown_seconds=15.0)
    rec = {
        "exit_timestamp": 1000.0,
        "exit_bar_id": 60000,
        "closed_side": "LONG",
    }
    ok_cd, reason_cd = can_reenter_after_pv_exit(
        rec, current_bar_id=120000, candidate_side="SHORT", now_ts=1005.0, config=cfg,
    )
    assert ok_cd is False and reason_cd == "BLOCKED_PV_EXIT_COOLDOWN"

    ok_bar, reason_bar = can_reenter_after_pv_exit(
        rec, current_bar_id=60000, candidate_side="SHORT", now_ts=1020.0, config=cfg,
    )
    assert ok_bar is False and reason_bar == "BLOCKED_PV_SAME_BAR_ANTI_WHIPSAW"

    ok_next, _ = can_reenter_after_pv_exit(
        rec, current_bar_id=120000, candidate_side="SHORT", now_ts=1020.0, config=cfg,
    )
    assert ok_next is True


def test_conditional_flip_eligible_vs_forbidden_and_stricter_thresholds():
    closes_long = [100.0 + i * 0.2 for i in range(18)] + [103.6, 103.9, 103.85]
    pos_long = {
        "side": "LONG",
        "entry_price": 102.0,
        "entry_swing_low": 99.0,
        "entry_kc_width_pct": 0.03,
    }
    legacy_cfg = PeakValleyExitConfig(enable_flip_watch=False, exit_mode="legacy_pv")

    # 0. 預設脫鉤模式（enable_flip_watch=True）：平倉絕不直接反手，合條件者改進 Flip Watch 觀察期
    df_wide = _make_df(closes_long, is_last_closed=False, kc_width_pct=0.03)
    res_watch = evaluate_peak_valley_exit(
        df_wide, position=pos_long, live_price=102.5, config=PeakValleyExitConfig(exit_mode="legacy_pv")
    )
    assert res_watch["should_exit"] is True
    assert res_watch["reason"] == "ACTIVE_EXIT_PV_REVERSE_INFLECTION_LONG"
    assert res_watch["should_flip"] is False
    assert res_watch["enter_flip_watch"] is True
    assert res_watch["flip_watch_side"] == "SHORT"

    # 1. 相容直翻模式驗證：Width=0.9% (< 1.2% strict flip floor) 拒絕直翻
    df_narrow_flip = _make_df(closes_long, is_last_closed=False, kc_width_pct=0.009)
    res_narrow = evaluate_peak_valley_exit(df_narrow_flip, position=pos_long, live_price=102.5, config=legacy_cfg)
    assert res_narrow["should_exit"] is True
    assert res_narrow["reason"] == "ACTIVE_EXIT_PV_REVERSE_INFLECTION_LONG"
    assert res_narrow["should_flip"] is False
    assert "CONSOLIDATION" in res_narrow["flip_reason"]

    # 2. 相容直翻模式：Width=3.0%, ADX=30 -> Flip SHORT authorized
    res_flip = evaluate_peak_valley_exit(df_wide, position=pos_long, live_price=102.5, config=legacy_cfg)
    assert res_flip["should_exit"] is True
    assert res_flip["should_flip"] is True
    assert res_flip["flip_side"] == "SHORT"

    # 3. Trailing Profit Lock -> MUST forbid Flip & Flip Watch
    cfg_trail = PeakValleyExitConfig(
        trailing_activate_pct=0.008,
        trailing_profit_giveback_ratio=0.30,
        enable_kc_prev_bar_break=False,
        kc_extreme_retrace_pct=0.0,
        ma5_slope_eps=1.0,
        exit_mode="legacy_pv",
    )
    pos_trail = {
        "side": "LONG",
        "entry_price": 100.0,
        "entry_swing_low": 98.0,
        "pv_peak_price": 101.5,
        "pv_peak_profit_pct": 0.015,
        "pv_trailing_armed": True,
    }
    res_trail = evaluate_peak_valley_exit(df_wide, position=pos_trail, live_price=100.8, config=cfg_trail)
    assert res_trail["should_exit"] is True
    assert res_trail["reason"] == "ACTIVE_EXIT_PV_TRAILING_PROFIT_LOCK"
    assert res_trail["should_flip"] is False
    assert res_trail["enter_flip_watch"] is False
    assert "FORBIDDEN" in res_trail["flip_reason"]

    # 4. Swing-break Fake Inflection SL -> MUST forbid Flip & Flip Watch
    pos_swing = {"side": "LONG", "entry_price": 103.5, "entry_swing_low": 103.0}
    res_swing = evaluate_peak_valley_exit(
        df_wide, position=pos_swing, live_price=102.8, config=PeakValleyExitConfig(exit_mode="legacy_pv")
    )
    assert res_swing["should_exit"] is True
    assert res_swing["reason"] == "ACTIVE_EXIT_PV_FAKE_INFLECTION_SWING_BREAK"
    assert res_swing["should_flip"] is False
    assert res_swing["enter_flip_watch"] is False


def test_execution_manager_end_to_end_pv_exit_no_flip_when_below_flip_width(monkeypatch, tmp_path):
    from core.paper_account import PaperAccount
    from core.services.data_pipeline import DataPipeline
    from core.services.execution_manager import ExecutionManager

    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper_nof.json"))
    monkeypatch.setattr("core.services.order_audit_trail.AUDIT_LOG_FILE", str(tmp_path / "audit_nof.jsonl"))
    monkeypatch.setattr(ExecutionManager, "_last_exit_record", {})
    monkeypatch.setattr(ExecutionManager, "_last_trade_bar_time", {})
    monkeypatch.setattr(ExecutionManager, "_hourly_entry_records", {})
    monkeypatch.setattr(ExecutionManager, "_pv_exit_records", {})
    monkeypatch.setattr(ExecutionManager, "_pv_flip_records", {})
    monkeypatch.setattr(
        "core.services.low_latency_exit.resolve_peak_valley_exit_config",
        lambda *_a, **_k: PeakValleyExitConfig(exit_mode="legacy_pv"),
    )

    account = PaperAccount()
    account.balance = account.available_balance = 200.0
    symbol = "1000PEPE/USDT"

    # kc_width_pct=0.009: exits on Tick MA5 inflection, but forbids Flip (< 0.012 flip threshold)
    closes = [100.0 + i * 0.2 for i in range(18)] + [103.6, 103.9, 103.85]
    frame = _make_df(closes, is_last_closed=False, kc_width_pct=0.009)
    struct = extract_entry_structure_context(frame, "LONG")
    account.positions[symbol] = {
        "symbol": symbol,
        "side": "LONG",
        "qty": 1.0,
        "amount": 1.0,
        "entry_price": 103.3,
        "leverage": 10,
        "margin": 10.38,
        "is_peak_valley_inflection": True,
        "entry_swing_low": struct["entry_swing_low"],
        "entry_swing_high": struct["entry_swing_high"],
        "entry_kc_width_pct": struct["entry_kc_width_pct"],
    }
    account.position_meta[symbol] = {
        "is_peak_valley_inflection": True,
        "entry_swing_low": struct["entry_swing_low"],
        "entry_swing_high": struct["entry_swing_high"],
        "entry_kc_width_pct": struct["entry_kc_width_pct"],
    }

    async def _provider(*_a, **_k):
        return frame

    account.entry_frame_provider = _provider
    account.channel_exit_frame_provider = lambda *_a, **_k: frame

    live_price = 102.5
    snapshot = DataPipeline.calculate_snapshot(symbol, frame, current_price=live_price)
    assert snapshot is not None
    snapshot.raw_df = frame
    snapshot.curr_k = frame.iloc[-1].to_dict()
    snapshot.prev_k = frame.iloc[-2].to_dict()
    monkeypatch.setattr(DataPipeline, "calculate_snapshot", staticmethod(lambda *_a, **_k: snapshot))

    res = asyncio.run(ExecutionManager.process_symbol_pipeline(
        symbol, account, klines_df=frame, quote=live_price,
    ))
    assert res["status"] == "EXIT_EXECUTED", str(account.logs[-6:])
    assert res["flipped"] is False
    assert res["position_zero_confirmed"] is True
    assert res["trigger_level"] == "TICK"
    assert res["reason"] == "ACTIVE_EXIT_PV_REVERSE_INFLECTION_LONG"
    assert symbol not in account.positions
    acc_key = ExecutionManager._get_account_key(account, symbol)
    assert acc_key in ExecutionManager._pv_exit_records


def _make_5m_df(closes_5m, *, is_last_closed=False):
    import pandas as pd

    base_ts = 1_700_000_000_000 - len(closes_5m) * 300_000
    rows = []
    for idx, c in enumerate(closes_5m):
        o = c - 0.0001 if idx > 0 and c >= closes_5m[idx - 1] else c + 0.0001
        rows.append({
            "timestamp": base_ts + idx * 300_000,
            "open": float(o),
            "high": float(max(o, c) + 0.0002),
            "low": float(min(o, c) - 0.0002),
            "close": float(c),
            "volume": 1000.0,
            "is_closed": False if (not is_last_closed and idx == len(closes_5m) - 1) else True,
        })
    return pd.DataFrame(rows)


def test_execution_manager_end_to_end_decoupled_flip_watch_and_same_bar_lock(monkeypatch, tmp_path):
    """完整端到端測試：舊通道非PV倉位統一極速平倉 -> 進入5根觀察期 -> 假反彈與5M逆風攔截 -> 二次結構確認後開多並通過CentralEntryGate。"""
    import pandas as pd
    from core.paper_account import PaperAccount
    from core.services.data_pipeline import DataPipeline
    from core.services.execution_manager import ExecutionManager
    from core.services.low_latency_exit import evaluate_flip_watch_entry

    monkeypatch.setattr("core.paper_account.STATE_FILE", str(tmp_path / "paper_flip.json"))
    monkeypatch.setattr("core.services.order_audit_trail.AUDIT_LOG_FILE", str(tmp_path / "audit_flip.jsonl"))
    monkeypatch.setattr(ExecutionManager, "_last_exit_record", {})
    monkeypatch.setattr(ExecutionManager, "_last_trade_bar_time", {})
    monkeypatch.setattr(ExecutionManager, "_hourly_entry_records", {})
    monkeypatch.setattr(ExecutionManager, "_pv_exit_records", {})
    monkeypatch.setattr(ExecutionManager, "_pv_flip_records", {})
    monkeypatch.setattr(ExecutionManager, "_pv_flip_watch_records", {})

    account = PaperAccount()
    account.balance = account.available_balance = 200.0
    symbol = "龙虾/USDT"

    # 1. 構造舊通道空單 (is_peak_valley_inflection=False)，驗證預設 KC 三層出場引擎統一接管：
    #    1a. 盤中未收盤僅反彈刺穿 MA5 絕不提前洗出場（HOLD）
    #    1b. 1M 收盤確認漲破 KC 中軌 + 0.5*ATR 緩衝後平空並進入 Flip Watch 觀察期
    closes_exit = [0.01800 - i * 0.00002 for i in range(18)] + [0.01764, 0.01762, 0.01765]
    exit_frame = _make_df(closes_exit, is_last_closed=False, kc_width_pct=0.0042)
    exit_frame["high"] = exit_frame[["open", "close"]].max(axis=1) + 0.000005
    exit_frame["low"] = exit_frame[["open", "close"]].min(axis=1) - 0.000005
    exit_frame["atr"] = 0.00005
    exit_frame.attrs["symbol"] = symbol
    account.positions[symbol] = {
        "symbol": symbol,
        "side": "SHORT",
        "qty": 1000.0,
        "amount": 1000.0,
        "entry_price": 0.01768,
        "leverage": 10,
        "margin": 17.68,
        "open_timestamp": int(exit_frame.iloc[-3]["timestamp"]),
        "is_peak_valley_inflection": False,
        "entry_method_authority": "POST_CLOSE_KC_REENTRY",
    }
    account.position_meta[symbol] = {
        "is_peak_valley_inflection": False,
        "open_timestamp": int(exit_frame.iloc[-3]["timestamp"]),
    }

    async def _exit_provider(*_a, **_k):
        return exit_frame

    account.entry_frame_provider = _exit_provider
    account.channel_exit_frame_provider = lambda *_a, **_k: exit_frame

    orig_calc_snapshot = DataPipeline.calculate_snapshot
    exit_live = 0.01770
    snap_hold = orig_calc_snapshot(symbol, exit_frame, current_price=exit_live)
    assert snap_hold is not None
    snap_hold.raw_df = exit_frame
    snap_hold.curr_k = exit_frame.iloc[-1].to_dict()
    snap_hold.prev_k = exit_frame.iloc[-2].to_dict()
    monkeypatch.setattr(DataPipeline, "calculate_snapshot", staticmethod(lambda *_a, **_k: snap_hold))

    res_hold = asyncio.run(ExecutionManager.process_symbol_pipeline(
        symbol, account, klines_df=exit_frame, quote=exit_live,
    ))
    assert res_hold["status"] == "HOLD", str(account.logs[-6:])
    assert res_hold["exit_policy"] == "KC_LAYERED_3TIER_V1"
    assert symbol in account.positions

    # 1b. 最新一根 K 棒收盤確認漲破 KC_middle + 0.5*ATR (0.01763 + 0.000025 = 0.017655 < 0.01769)
    # 且 KC 中軌已連續 2 根明確向上 (s0_pct >= 0.00015 且 s1_pct >= 0.00015)
    closed_exit_frame = exit_frame.copy()
    closed_exit_frame.loc[closed_exit_frame.index[-3], "kc_middle"] = 0.01760
    closed_exit_frame.loc[closed_exit_frame.index[-2], "kc_middle"] = 0.01762
    closed_exit_frame.loc[closed_exit_frame.index[-1], ["close", "high", "kc_middle", "is_closed"]] = [
        0.01772, 0.01773, 0.01765, True
    ]
    closed_exit_frame.loc[closed_exit_frame.index[-1], "kc_upper"] = 0.01765 * (1 + 0.0042 / 2)
    closed_exit_frame.loc[closed_exit_frame.index[-1], "kc_lower"] = 0.01765 * (1 - 0.0042 / 2)
    account.channel_exit_frame_provider = lambda *_a, **_k: closed_exit_frame
    snap_exit = orig_calc_snapshot(symbol, closed_exit_frame, current_price=exit_live)
    assert snap_exit is not None
    snap_exit.raw_df = closed_exit_frame
    snap_exit.curr_k = closed_exit_frame.iloc[-1].to_dict()
    snap_exit.prev_k = closed_exit_frame.iloc[-2].to_dict()
    monkeypatch.setattr(DataPipeline, "calculate_snapshot", staticmethod(lambda *_a, **_k: snap_exit))

    res_exit = asyncio.run(ExecutionManager.process_symbol_pipeline(
        symbol, account, klines_df=closed_exit_frame, quote=exit_live,
    ))
    assert res_exit["status"] == "EXIT_EXECUTED", str(account.logs[-6:])
    assert res_exit["flipped"] is False
    assert res_exit["enter_flip_watch"] is True
    assert res_exit["flip_watch_side"] == "LONG"
    assert res_exit["trigger_level"] == "BAR_CLOSE"
    assert res_exit["reason"] == "ACTIVE_EXIT_KC_MID_BUFFER_SHORT"
    assert symbol not in account.positions
    acc_key = ExecutionManager._get_account_key(account, symbol)
    assert acc_key in ExecutionManager._pv_flip_watch_records
    watch_state = ExecutionManager._pv_flip_watch_records[acc_key]
    exit_ts = int(exit_frame.iloc[-1]["timestamp"])

    # 2. 構造觀察期 K 線序列（符合 龙虾/USDT 實盤低門檻: width 0.35%~0.42%, MA spread >= 0.045%, ADX >= 18）
    #    - bar 0 (exit_ts): 0.01765 -> 反彈開始，突破 KC_middle
    #    - bar 1 (+1m): 突破站上 KC_middle (high=0.01773, close=0.01771, kc_mid=0.01769)
    #    - bar 2 (+2m): 回測不破 KC_middle / MA15（low=0.017695 > anchor_low, close=0.01770），形成 Higher Low
    #    - bar 3 (+3m): 再度向上突破 bar 1 小高點 0.01773 -> live=0.01776 且通道擴張
    base_history = exit_frame.iloc[:-1].copy().reset_index(drop=True)
    extra_rows = []
    # 讓 KC_middle 在最後保持 ~0.01768，且 MA5 在突破時仍可略低於 KC_middle（驗證突破舊方向鎖）
    specs = [
        # ts_offset, open, high, low, close, kcm, width_pct
        (0, 0.01763, 0.01767, 0.01762, 0.01766, 0.01767, 0.0033),
        (60_000, 0.01766, 0.01773, 0.01766, 0.01771, 0.01768, 0.0035),
        (120_000, 0.01771, 0.01772, 0.01769, 0.01770, 0.01768, 0.0035),
        (180_000, 0.01770, 0.01776, 0.01770, 0.01776, 0.01770, 0.0042),
    ]
    for off_ms, o, h, l, c, kcm, wpct in specs:
        hw = kcm * (wpct / 2.0)
        extra_rows.append({
            "timestamp": exit_ts + off_ms,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 1500.0,
            "atr": 0.00004,
            "adx": 22.5,
            "kc_middle": kcm,
            "kc_upper": kcm + hw,
            "kc_lower": kcm - hw,
            "is_closed": False if off_ms == 180_000 else True,
        })
    confirm_df = pd.concat([base_history, pd.DataFrame(extra_rows)], ignore_index=True)
    confirm_df["ma5"] = confirm_df["close"].rolling(5, min_periods=1).mean()
    confirm_df["ma15"] = confirm_df["close"].rolling(15, min_periods=1).mean()
    confirm_df.attrs["symbol"] = symbol
    confirm_df.attrs["timeframe_ms"] = 60_000.0
    confirm_df.attrs["snapshot_ms"] = int(time.time() * 1000) - 5_000
    confirm_df.attrs["entry_finality_verified"] = True
    confirm_df.attrs["macro_trend"] = "BEARISH"

    # 2a. 若 5M 趨勢仍然強烈向下且收在 5M 中軌下 -> 嚴格擋單不開多
    bearish_5m = _make_5m_df([0.0190 - i * 0.00005 for i in range(25)])
    blocked_5m = evaluate_flip_watch_entry(
        confirm_df, watch_state=watch_state, live_price=0.01776, df_5m=bearish_5m, symbol=symbol,
    )
    assert blocked_5m["should_enter"] is False
    assert blocked_5m["reason"] == "FLIP_WATCH_BLOCKED_5M_BEARISH"

    # 2b. 若通道未擴張（寬度反而縮窄）-> 嚴格擋單
    non_exp_df = confirm_df.copy()
    non_exp_df.loc[non_exp_df.index[-1], "kc_upper"] = 0.01770 * (1 + 0.0033 / 2)
    non_exp_df.loc[non_exp_df.index[-1], "kc_lower"] = 0.01770 * (1 - 0.0033 / 2)
    flat_5m = _make_5m_df([0.01770 + i * 0.000005 for i in range(25)])
    blocked_exp = evaluate_flip_watch_entry(
        non_exp_df, watch_state=watch_state, live_price=0.01776, df_5m=flat_5m, symbol=symbol,
    )
    assert blocked_exp["should_enter"] is False
    assert blocked_exp["reason"] == "FLIP_WATCH_KC_NOT_EXPANDING"

    # 2c. 5M 走平/翻揚 + 1M 回測不破中軌並突破前高 + 通道開口擴張 -> 通過觀察期並穿透舊方向鎖成功開多
    account.entry_frame_provider = lambda *_a, **_k: asyncio.sleep(0, result=confirm_df)
    snap_confirm = orig_calc_snapshot(symbol, confirm_df, current_price=0.01776)
    assert snap_confirm is not None
    snap_confirm.raw_df = confirm_df
    snap_confirm.curr_k = confirm_df.iloc[-1].to_dict()
    snap_confirm.prev_k = confirm_df.iloc[-2].to_dict()
    monkeypatch.setattr(DataPipeline, "calculate_snapshot", staticmethod(lambda *_a, **_k: snap_confirm))

    res_entry = asyncio.run(ExecutionManager.process_symbol_pipeline(
        symbol, account, klines_df=confirm_df, quote=0.01776, frame_5m=flat_5m,
    ))
    assert res_entry["status"] == "FLIP_WATCH_ENTRY_EXECUTED", (
        f"res={res_entry}, err={ExecutionManager._last_open_error.get(symbol)}, logs={account.logs[-6:]}"
    )
    assert res_entry["side"] == "LONG"
    assert symbol in account.positions
    assert account.positions[symbol]["side"] == "LONG"
    assert account.positions[symbol]["is_pv_flip"] is True
    assert acc_key not in ExecutionManager._pv_flip_watch_records

    # 3. Same-bar adversarial spike MUST be locked (no double exit or second flip inside same 1M bar)
    res_same_bar = asyncio.run(ExecutionManager.process_symbol_pipeline(
        symbol, account, klines_df=confirm_df, quote=0.01700,
    ))
    assert res_same_bar["status"] == "HOLD"
    assert res_same_bar["reason"] == "HOLD_SAME_BAR_FLIP_EXIT_LOCKED"
    assert account.positions[symbol]["side"] == "LONG"

    # 4. 超時檢驗：超過 N=8 根後自動失效
    expired_df = confirm_df.copy()
    expired_df.loc[expired_df.index[-1], "timestamp"] = exit_ts + 9 * 60_000
    expired_eval = evaluate_flip_watch_entry(
        expired_df, watch_state=watch_state, live_price=0.01776, df_5m=flat_5m, symbol=symbol,
    )
    assert expired_eval["action"] == "EXPIRED"
    assert expired_eval["expired"] is True
    assert expired_eval["reason"] == "FLIP_WATCH_EXPIRED_TTL"


def test_exchange_api_order_sequence_aborts_flip_when_position_not_zero():
    events = []

    class _FakeExchange:
        def __init__(self, remain_amt):
            self.remain_amt = remain_amt

        async def fapiPrivateV2GetPositionRisk(self):
            events.append(("VERIFY_ZERO", self.remain_amt))
            return [{"symbol": "1000PEPEUSDT", "positionAmt": str(self.remain_amt)}]

    class _FakeAccount:
        def __init__(self, remain_amt):
            self.exchange = _FakeExchange(remain_amt)
            self.positions = {"1000PEPE/USDT": {"side": "LONG"}}
            self.position_meta = {}
            self.closing_lock = set()
            self.trades = []
            self.last_closed_trades = {}
            self.last_closed_at = {}
            self.realized_pnl = 0.0

    legacy_cfg = PeakValleyExitConfig(enable_flip_watch=False, exit_mode="legacy_pv")
    closes = [100.0 + i * 0.2 for i in range(18)] + [103.6, 103.9, 103.85]
    df = _make_df(closes, is_last_closed=False, kc_width_pct=0.03)
    pv_exit = evaluate_peak_valley_exit(
        df,
        position={"side": "LONG", "entry_price": 102.0, "entry_swing_low": 99.0},
        live_price=102.5,
        config=legacy_cfg,
    )
    assert pv_exit["should_flip"] is True

    # Case 1: exchange STILL reports residual position (0.5) -> MUST abort Flip!
    acc_stuck = _FakeAccount(remain_amt=0.5)

    async def _close_stuck(**_k):
        events.append(("CLOSE_MARKET",))
        acc_stuck.positions.pop("1000PEPE/USDT", None)
        acc_stuck.last_closed_trades["1000PEPE/USDT"] = {"side": "long"}
        return True

    async def _open_never(**_k):
        events.append(("OPEN_FLIP",))
        return True

    async def _run_stuck():
        from core.services import low_latency_exit as mod
        # Shorten verify timeout for instant unit test failure on stuck position
        orig = mod.verify_exchange_position_zero_and_settled

        async def _fast_verify(account, symbol, timeout_sec=0.25, poll_interval_sec=0.05):
            return await orig(account, symbol, timeout_sec=0.25, poll_interval_sec=0.05)

        mod.verify_exchange_position_zero_and_settled = _fast_verify
        try:
            return await execute_peak_valley_exit_and_conditional_flip(
                account=acc_stuck,
                engine_or_account=acc_stuck,
                symbol="1000PEPE/USDT",
                df=df,
                live_price=102.5,
                pv_exit=pv_exit,
                closed_side="LONG",
                close_fn=_close_stuck,
                open_fn=_open_never,
                config=legacy_cfg,
            )
        finally:
            mod.verify_exchange_position_zero_and_settled = orig

    out_stuck = asyncio.run(_run_stuck())
    assert out_stuck["position_zero_confirmed"] is False
    assert out_stuck["flipped"] is False
    assert ("OPEN_FLIP",) not in events

    # Case 2: exchange confirms positionAmt == 0 -> strict Close -> VerifyZero -> OpenFlip order
    events.clear()
    acc_ok = _FakeAccount(remain_amt=0.0)

    async def _close_ok(**_k):
        events.append("CLOSE_MARKET")
        acc_ok.positions.pop("1000PEPE/USDT", None)
        acc_ok.last_closed_trades["1000PEPE/USDT"] = {"side": "long"}
        return True

    async def _open_ok(**_k):
        events.append("OPEN_FLIP")
        acc_ok.positions["1000PEPE/USDT"] = {"side": "SHORT"}
        return True

    out_ok = asyncio.run(execute_peak_valley_exit_and_conditional_flip(
        account=acc_ok,
        engine_or_account=acc_ok,
        symbol="1000PEPE/USDT",
        df=df,
        live_price=102.5,
        pv_exit=pv_exit,
        closed_side="LONG",
        close_fn=_close_ok,
        open_fn=_open_ok,
        config=legacy_cfg,
    ))
    assert out_ok["position_zero_confirmed"] is True
    assert out_ok["flipped"] is True
    assert events == ["CLOSE_MARKET", ("VERIFY_ZERO", 0.0), "OPEN_FLIP"]

    # 預設脫鉤模式：確認平倉歸零後僅建立觀察期，絕不於平倉當秒送出 OPEN_FLIP
    events.clear()
    acc_watch = _FakeAccount(remain_amt=0.0)
    watch_registry = {}

    async def _close_watch(**_k):
        events.append("CLOSE_MARKET")
        acc_watch.positions.pop("1000PEPE/USDT", None)
        acc_watch.last_closed_trades["1000PEPE/USDT"] = {"side": "long"}
        return True

    async def _open_watch_must_not_run(**_k):
        events.append("OPEN_FLIP")
        return True

    pv_watch_exit = evaluate_peak_valley_exit(
        df,
        position={"side": "LONG", "entry_price": 102.0, "entry_swing_low": 99.0},
        live_price=102.5,
        config=PeakValleyExitConfig(exit_mode="legacy_pv"),
    )
    assert pv_watch_exit["should_flip"] is False
    assert pv_watch_exit["enter_flip_watch"] is True
    out_watch = asyncio.run(execute_peak_valley_exit_and_conditional_flip(
        account=acc_watch,
        engine_or_account=acc_watch,
        symbol="1000PEPE/USDT",
        df=df,
        live_price=102.5,
        pv_exit=pv_watch_exit,
        closed_side="LONG",
        close_fn=_close_watch,
        open_fn=_open_watch_must_not_run,
        flip_watch_registry=watch_registry,
    ))
    assert out_watch["flipped"] is False
    assert out_watch["enter_flip_watch"] is True
    assert out_watch["flip_watch_side"] == "SHORT"
    assert len(watch_registry) == 1
    assert events == ["CLOSE_MARKET", ("VERIFY_ZERO", 0.0)]


def test_flip_watch_short_lower_high_5m_filter_and_forbidden_exit_clear():
    """驗證做空觀察期 (頂頂低 Lower High + 5M 多頭擋單 + 通道擴張) 與禁止反手平倉自動清除觀察狀態。"""
    from core.services.low_latency_exit import (
        create_flip_watch_state,
        evaluate_flip_watch_entry,
        verify_pv_flip_entry_authority,
    )

    symbol = "龙虾/USDT"
    closes_up = [0.01740 + i * 0.00002 for i in range(18)] + [0.01776, 0.01778, 0.01775]
    base_df = _make_df(closes_up, is_last_closed=True, kc_width_pct=0.0040)
    base_df["high"] = base_df[["open", "close"]].max(axis=1) + 0.00001
    base_df["low"] = base_df[["open", "close"]].min(axis=1) - 0.00001
    base_df["atr"] = 0.00004
    exit_ts = int(base_df.iloc[-1]["timestamp"])

    watch_state = create_flip_watch_state(
        base_df,
        symbol=symbol,
        closed_side="LONG",
        origin_exit_reason="ACTIVE_EXIT_PV_REVERSE_INFLECTION_LONG",
    )
    assert watch_state is not None
    assert watch_state["flip_watch_side"] == "SHORT"
    assert watch_state["window_bars"] == 8

    specs = [
        # bar 0 (exit): 跌破 KC_middle
        (0, 0.01775, 0.01776, 0.01770, 0.01772, 0.01774, 0.0033),
        # bar 1: 實體跌破並創新波段低點 0.01764
        (60_000, 0.01772, 0.01773, 0.01764, 0.01766, 0.01770, 0.0035),
        # bar 2: 反抽不破 KC_middle(0.01770) / MA15 且高點 0.01769 < 0.01779 (Lower High)
        (120_000, 0.01766, 0.01769, 0.01765, 0.01767, 0.01770, 0.0035),
        # bar 3: 再度跌破前一波回落小低點 0.01764，且通道擴張至 0.42%
        (180_000, 0.01767, 0.01767, 0.01760, 0.01761, 0.01768, 0.0042),
    ]
    rows = []
    for off_ms, o, h, l, c, kcm, wpct in specs:
        hw = kcm * (wpct / 2.0)
        rows.append({
            "timestamp": exit_ts + off_ms,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 1600.0,
            "atr": 0.00004,
            "adx": 23.0,
            "kc_middle": kcm,
            "kc_upper": kcm + hw,
            "kc_lower": kcm - hw,
            "is_closed": False if off_ms == 180_000 else True,
        })
    confirm_short_df = pd.concat([base_df.iloc[:-1], pd.DataFrame(rows)], ignore_index=True)
    confirm_short_df["ma5"] = confirm_short_df["close"].rolling(5, min_periods=1).mean()
    confirm_short_df["ma15"] = confirm_short_df["close"].rolling(15, min_periods=1).mean()
    confirm_short_df.attrs["symbol"] = symbol
    confirm_short_df.attrs["timeframe_ms"] = 60_000.0

    # 1. 5M 多頭強趨勢仍壓著走高 -> 嚴禁反手開空
    bullish_5m = _make_5m_df([0.0165 + i * 0.00005 for i in range(25)])
    blocked_bull = evaluate_flip_watch_entry(
        confirm_short_df, watch_state=watch_state, live_price=0.01761, df_5m=bullish_5m, symbol=symbol,
    )
    assert blocked_bull["should_enter"] is False
    assert blocked_bull["reason"] == "FLIP_WATCH_BLOCKED_5M_BULLISH"

    # 2. 5M 已轉空或走平 -> Lower High + 跌破前低 + 通道擴張 -> 放行開空且可通過數學複驗
    bearish_5m = _make_5m_df([0.0185 - i * 0.00003 for i in range(25)])
    ok_short = evaluate_flip_watch_entry(
        confirm_short_df, watch_state=watch_state, live_price=0.01761, df_5m=bearish_5m, symbol=symbol,
    )
    assert ok_short["should_enter"] is True, ok_short
    assert ok_short["side"] == "SHORT"
    assert ok_short["reason"] == "FLIP_WATCH_CONFIRMED_SHORT"

    auth_short = verify_pv_flip_entry_authority(
        side="SHORT",
        frame=confirm_short_df,
        live_price=0.01761,
        context={
            "is_pv_flip": True,
            "symbol": symbol,
            "pv_flip_watch_state": watch_state,
            "frame_5m": bearish_5m,
        },
        symbol=symbol,
        df_5m=bearish_5m,
    )
    assert auth_short is True

    # 3. 禁止反手平倉（如 TRAILING_PROFIT_LOCK）必須清除舊觀察狀態且不得新建觀察期
    acc_key = f"fake:{symbol}"
    registry = {acc_key: watch_state}

    class _MinAcc:
        def __init__(self):
            self.positions = {symbol: {"side": "SHORT"}}
            self.position_meta = {}
            self.exchange = None

    acc = _MinAcc()

    async def _close_only(**_k):
        acc.positions.pop(symbol, None)
        return True

    async def _never_open(**_k):
        raise AssertionError("open_fn must not run for forbidden exit")

    out_forbid = asyncio.run(execute_peak_valley_exit_and_conditional_flip(
        account=acc,
        engine_or_account=acc,
        symbol=symbol,
        df=base_df,
        live_price=0.01750,
        pv_exit={
            "should_exit": True,
            "reason": "ACTIVE_EXIT_PV_TRAILING_PROFIT_LOCK",
            "trigger_level": "TICK",
            "should_flip": False,
            "enter_flip_watch": False,
            "flip_watch_side": None,
            "flip_reason": "FLIP_FORBIDDEN_ON_EXIT_REASON:ACTIVE_EXIT_PV_TRAILING_PROFIT_LOCK",
            "current_bar_id": exit_ts,
        },
        closed_side="SHORT",
        close_fn=_close_only,
        open_fn=_never_open,
        flip_watch_registry=registry,
        account_key=acc_key,
    ))
    assert out_forbid["status"] == "EXIT_EXECUTED"
    assert out_forbid["enter_flip_watch"] is False
    assert out_forbid["flipped"] is False
    assert acc_key not in registry


def test_1000pepe_1213_inside_channel_bearish_no_3point_pivot_holds_short():
    """回歸測試：1000PEPE/USDT 12:13 實盤空單還在中軌內、空頭排列且無三點谷底，禁止平倉，且過期外軌觸及標記必須清除。"""
    base_ts = int((time.time() // 60) * 60_000) - 20 * 60_000
    rows = []
    # 0..16: stable downtrend leading into 12:10 trough
    for i in range(17):
        c = 0.004596 - i * 0.0000018
        rows.append({
            "timestamp": base_ts + i * 60_000,
            "open": c + 0.0000010,
            "high": c + 0.0000025,
            "low": c - 0.0000015,
            "close": c,
            "volume": 5000.0,
            "kc_upper": c + 0.0000120,
            "kc_middle": 0.0045820 - i * 0.0000005,
            "kc_lower": c - 0.0000100,
            "ma15": 0.0045800 - i * 0.0000004,
            "atr": 0.000005,
            "adx": 28.0,
            "is_closed": True,
        })
    # -4 (12:10): trough at 0.0045580 (touching lower band earlier, no 3pt pivot centered at -2)
    rows.append({
        "timestamp": base_ts + 17 * 60_000,
        "open": 0.0045690,
        "high": 0.0045695,
        "low": 0.0045580,
        "close": 0.0045658,
        "volume": 6200.0,
        "kc_upper": 0.00458310,
        "kc_middle": 0.00457380,
        "kc_lower": 0.00456450,
        "ma15": 0.00457310,
        "atr": 0.000005,
        "adx": 28.0,
        "is_closed": True,
    })
    # -3 (12:11): low 0.0045658 > 12:10 low -> NOT a 3-point valley pivot
    rows.append({
        "timestamp": base_ts + 18 * 60_000,
        "open": 0.0045666,
        "high": 0.0045688,
        "low": 0.0045658,
        "close": 0.0045658,
        "volume": 4800.0,
        "kc_upper": 0.00458287,
        "kc_middle": 0.00457352,
        "kc_lower": 0.00456416,
        "ma15": 0.00457257,
        "atr": 0.000005,
        "adx": 28.0,
        "is_closed": True,
    })
    # -2 (12:12): closes at 0.0045713 inside channel & below middle
    rows.append({
        "timestamp": base_ts + 19 * 60_000,
        "open": 0.0045658,
        "high": 0.0045732,
        "low": 0.0045658,
        "close": 0.0045713,
        "volume": 4500.0,
        "kc_upper": 0.00458329,
        "kc_middle": 0.00457331,
        "kc_lower": 0.00456332,
        "ma15": 0.00457188,
        "atr": 0.000005,
        "adx": 28.0,
        "is_closed": True,
    })
    # -1 (12:13 unclosed): live bar still in bearish trend (KCm down, MA5 < MA15), inside channel
    rows.append({
        "timestamp": base_ts + 20 * 60_000,
        "open": 0.0045714,
        "high": 0.0045731,
        "low": 0.0045713,
        "close": 0.00457306,
        "volume": 1200.0,
        "kc_upper": 0.00458262,
        "kc_middle": 0.00457248,
        "kc_lower": 0.00456233,
        "ma15": 0.00457070,
        "atr": 0.000005,
        "adx": 28.0,
        "is_closed": False,
    })
    df = pd.DataFrame(rows)
    df.attrs["timeframe_ms"] = 60_000.0

    # Stale touch from 27 minutes ago (well past lookback=3 bars) + armed trailing + extreme retrace
    stale_touch_bar = base_ts - 7 * 60_000
    pos = {
        "side": "SHORT",
        "entry_price": 0.00459604,
        "entry_swing_high": 0.0046200,
        "pv_trough_price": 0.0045580,
        "pv_peak_profit_pct": 0.00828,
        "pv_trailing_armed": True,
        "pv_touched_kc_lower": True,
        "pv_touched_kc_lower_bar_id": stale_touch_bar,
    }
    legacy_pepe_cfg = PeakValleyExitConfig(exit_mode="legacy_pv")
    res_hold = evaluate_peak_valley_exit(df, position=pos, live_price=0.00457306, config=legacy_pepe_cfg)
    assert res_hold["should_exit"] is False
    assert res_hold["reason"] == "HOLD_TREND_INTACT_WITHOUT_3POINT_PIVOT"
    assert res_hold["state_updates"]["pv_touched_kc_lower"] is False
    assert res_hold["state_updates"]["pv_touched_kc_lower_bar_id"] is None

    # Breaking above kc_upper (0.0045840 > 0.00458262) exits immediately via trailing lock / reverse inflection
    res_break_upper = evaluate_peak_valley_exit(df, position=pos, live_price=0.0045840, config=legacy_pepe_cfg)
    assert res_break_upper["should_exit"] is True

    # Forming a genuine closed 3-point valley pivot allows immediate TP / trailing exit inside channel
    df_pivot = df.copy()
    df_pivot.loc[df_pivot.index[-3], ["open", "high", "low", "close"]] = [0.0045650, 0.0045660, 0.0045550, 0.0045570]
    df_pivot.loc[df_pivot.index[-2], ["open", "high", "low", "close"]] = [0.0045575, 0.0045735, 0.0045570, 0.0045720]
    res_pivot = evaluate_peak_valley_exit(df_pivot, position=pos, live_price=0.00457306, config=legacy_pepe_cfg)
    assert res_pivot["should_exit"] is True


def test_kc_layered_exit_constants_and_three_tier_rules():
    """完整驗證 KC 三層防洗盤出場架構：參數區常數、Layer1 硬止損、Layer2 收盤追蹤與趨勢反轉、Layer3 移動止損。"""
    from core.services.low_latency_exit import (
        EXIT_BUFFER_ATR_MULT,
        HARD_SL_ATR_MULT,
        ROUND_TRIP_FEE_PCT,
        TRAILING_DIST_ATR,
        TRAILING_START_ATR,
        evaluate_kc_layered_exit,
    )

    # 0. 驗證頂部參數設定區常數
    assert HARD_SL_ATR_MULT == pytest.approx(2.0)
    assert EXIT_BUFFER_ATR_MULT in (0.5, 1.0)
    assert TRAILING_START_ATR == pytest.approx(1.5)
    assert TRAILING_DIST_ATR in (1.0, 2.0)
    assert ROUND_TRIP_FEE_PCT == pytest.approx(0.001)

    # ── 1. Layer 1：硬止損（盤中即時 Tick 觸發，固定依進場 ATR ± 2.0 * ATR，禁開 Flip Watch） ──
    closes_up = [100.0 + i * 0.2 for i in range(21)]
    df_up = _make_df(closes_up, is_last_closed=False, kc_width_pct=0.04)
    df_up["atr"] = 0.5
    pos_long = {"side": "LONG", "entry_price": 103.8, "entry_atr": 0.5}

    # Live price 102.9 > 103.8 - 1.0 = 102.8 -> 即使盤中跌破 MA5 也不得止損/平倉
    res_l1_hold = evaluate_kc_layered_exit(df_up, position=pos_long, live_price=102.9)
    assert res_l1_hold["should_exit"] is False
    assert res_l1_hold["exit_policy"] == "KC_LAYERED_3TIER_V1"

    # Live price 102.8 <= hard_sl (102.8) -> 盤中 TICK 立即觸發硬止損且禁止進入 Flip Watch
    res_l1_hit = evaluate_kc_layered_exit(df_up, position=pos_long, live_price=102.8)
    assert res_l1_hit["should_exit"] is True
    assert res_l1_hit["action"] == "CLOSE_LONG"
    assert res_l1_hit["reason"] == "ACTIVE_EXIT_KC_HARD_STOP_LOSS"
    assert res_l1_hit["trigger_level"] == "TICK"
    assert res_l1_hit["enter_flip_watch"] is False

    # 空單對稱硬止損（entry + 2.0 * ATR）
    closes_dn = [105.0 - i * 0.2 for i in range(21)]
    df_dn = _make_df(closes_dn, is_last_closed=False, kc_width_pct=0.04)
    df_dn["atr"] = 0.5
    pos_short = {"side": "SHORT", "entry_price": 101.2, "entry_atr": 0.5}
    res_s1_hit = evaluate_kc_layered_exit(df_dn, position=pos_short, live_price=102.2)
    assert res_s1_hit["should_exit"] is True
    assert res_s1_hit["action"] == "CLOSE_SHORT"
    assert res_s1_hit["reason"] == "ACTIVE_EXIT_KC_HARD_STOP_LOSS"
    assert res_s1_hit["trigger_level"] == "TICK"
    assert res_s1_hit["enter_flip_watch"] is False

    # ── 2. Layer 2：趨勢追蹤平倉（嚴禁盤中毛刺/觸碰 MA5 洗出場，只認已收盤 K 棒） ──
    # 2a. 盤中 K 棒影線與 live_price 均跌破 MA5 甚至中軌，但仍在 2.0*ATR 硬止損內且已收盤 K 棒穩守中軌 -> HOLD
    df_mid = _make_df([100.0 + i * 0.15 for i in range(20)] + [102.6], is_last_closed=False, kc_width_pct=0.04)
    df_mid["atr"] = 0.6
    df_mid.loc[df_mid.index[-3], "kc_middle"] = 102.50
    df_mid.loc[df_mid.index[-2], ["close", "kc_middle"]] = [102.75, 102.70]
    df_mid.loc[df_mid.index[-1], ["low", "close"]] = [102.15, 102.20]
    pos_l2 = {"side": "LONG", "entry_price": 102.8, "entry_atr": 0.6}
    res_wick_hold = evaluate_kc_layered_exit(df_mid, position=pos_l2, live_price=102.20)
    assert res_wick_hold["should_exit"] is False
    assert res_wick_hold["reason"] == "HOLD_KC_TREND_INTACT"

    # 2b. 開倉當分鐘 K 棒尚未收盤，不得拿開倉前一根已收盤 K 棒做 Layer 2 平倉
    pos_same_min = {
        "side": "LONG",
        "entry_price": 102.8,
        "entry_atr": 0.6,
        "open_timestamp": int(df_mid.iloc[-1]["timestamp"]),
    }
    df_mid_open_wait = df_mid.copy()
    df_mid_open_wait.loc[df_mid_open_wait.index[-2], ["close", "kc_middle"]] = [102.0, 102.70]
    res_wait = evaluate_kc_layered_exit(df_mid_open_wait, position=pos_same_min, live_price=102.5)
    assert res_wait["should_exit"] is False
    assert res_wait["reason"] == "HOLD_KC_WAIT_BAR_CLOSE"

    # 2c. 已收盤 K 棒僅微破中軌（介於 kc_mid 與 kc_mid - 0.5*ATR 之間），KC 仍向上 -> 緩衝區保護 HOLD
    df_buf = _make_df([100.0 + i * 0.15 for i in range(21)], is_last_closed=True, kc_width_pct=0.04)
    df_buf["atr"] = 0.6
    df_buf.loc[df_buf.index[-2], "kc_middle"] = 102.80
    df_buf.loc[df_buf.index[-1], ["close", "kc_middle"]] = [102.72, 102.90]
    res_buf_hold = evaluate_kc_layered_exit(
        df_buf, position={"side": "LONG", "entry_price": 103.0, "entry_atr": 0.6}, live_price=102.75
    )
    assert res_buf_hold["should_exit"] is False

    # 2d. 已收盤 K 棒跌破 kc_mid - 1.0*ATR，但 KC 中軌仍向上 -> 強制 HOLD 續抱
    df_buf_break = df_buf.copy()
    df_buf_break.loc[df_buf_break.index[-1], "close"] = 102.25
    res_buf_hold_slope = evaluate_kc_layered_exit(
        df_buf_break, position={"side": "LONG", "entry_price": 103.0, "entry_atr": 0.6}, live_price=102.35
    )
    assert res_buf_hold_slope["should_exit"] is False
    assert res_buf_hold_slope["reason"] in ("HOLD_KC_MID_SLOPE_INTACT", "HOLD_KC_MID_NOT_CLEARLY_DOWN")

    # 2d-2. 當 KC 中軌連續 2 根明顯下彎且已收盤跌破 kc_mid - 1.0*ATR -> BAR_CLOSE 緩衝破位平多
    df_buf_break_flat = df_buf_break.copy()
    df_buf_break_flat.loc[df_buf_break_flat.index[-3], "kc_middle"] = 102.80
    df_buf_break_flat.loc[df_buf_break_flat.index[-2], "kc_middle"] = 102.75
    df_buf_break_flat.loc[df_buf_break_flat.index[-1], ["close", "kc_middle"]] = [102.05, 102.70]
    res_buf_exit = evaluate_kc_layered_exit(
        df_buf_break_flat, position={"side": "LONG", "entry_price": 103.0, "entry_atr": 0.6}, live_price=102.05
    )
    assert res_buf_exit["should_exit"] is True
    assert res_buf_exit["reason"] == "ACTIVE_EXIT_KC_MID_BUFFER_LONG"
    assert res_buf_exit["trigger_level"] == "BAR_CLOSE"
    assert res_buf_exit["enter_flip_watch"] is True

    # 2e. 整體 KC 方向已收盤確認連續向下且已跌破 KC 中軌 1.0*ATR 緩衝 -> BAR_CLOSE 破位平倉
    df_rev = _make_df([100.0 + i * 0.15 for i in range(21)], is_last_closed=True, kc_width_pct=0.04)
    df_rev["atr"] = 0.6
    df_rev.loc[df_rev.index[-3], "kc_middle"] = 103.05
    df_rev.loc[df_rev.index[-2], "kc_middle"] = 103.00
    df_rev.loc[df_rev.index[-1], ["close", "kc_middle"]] = [102.30, 102.95]
    res_rev_exit = evaluate_kc_layered_exit(
        df_rev, position={"side": "LONG", "entry_price": 103.1, "entry_atr": 0.6}, live_price=102.30
    )
    assert res_rev_exit["should_exit"] is True
    assert res_rev_exit["reason"] == "ACTIVE_EXIT_KC_MID_BUFFER_LONG"
    assert res_rev_exit["trigger_level"] == "BAR_CLOSE"

    # ── 3. Layer 3：動態移動止損（1.5*ATR + 雙邊手續費覆蓋門檻 & 1.0*ATR 單向追蹤） ──
    df_tr = _make_df([100.0 + i * 0.15 for i in range(21)], is_last_closed=False, kc_width_pct=0.05)
    df_tr["atr"] = 1.0
    pos_tr = {"side": "LONG", "entry_price": 100.0, "entry_atr": 1.0}

    # 3a. 僅賺 +1.2 ATR (< 1.5 ATR 啟動門檻) -> 不得啟動移動止損
    res_not_armed = evaluate_kc_layered_exit(df_tr, position=pos_tr, live_price=101.2)
    assert res_not_armed["should_exit"] is False
    assert res_not_armed["state_updates"]["pv_trailing_armed"] is False

    # 3b. 衝高到 +2.0 ATR (102.0 >= max(1.5, 0.1)) -> 啟動移動止損，追蹤距離 = 2.0*ATR，保本 floor = 100.1
    res_armed = evaluate_kc_layered_exit(df_tr, position=pos_tr, live_price=102.0)
    assert res_armed["should_exit"] is False
    assert res_armed["state_updates"]["pv_trailing_armed"] is True
    assert res_armed["state_updates"]["trailing_stop_price"] in (pytest.approx(100.1), pytest.approx(101.0))

    # 3c. 回檔至 101.1 -> 續抱，且追蹤線單向不後退
    pos_tr.update(res_armed["state_updates"])
    res_tr_hold = evaluate_kc_layered_exit(df_tr, position=pos_tr, live_price=101.1)
    assert res_tr_hold["should_exit"] is False
    assert res_tr_hold["state_updates"]["trailing_stop_price"] in (pytest.approx(100.1), pytest.approx(101.0))

    # 3d-1. 若 KC 中軌未明顯反轉向下，即使 live_price 觸及移動止損線，也強制 HOLD 續抱！
    res_tr_mid_intact = evaluate_kc_layered_exit(df_tr, position=pos_tr, live_price=100.1)
    assert res_tr_mid_intact["should_exit"] is False
    assert res_tr_mid_intact["reason"] in ("HOLD_KC_MID_SLOPE_INTACT", "HOLD_KC_MID_NOT_CLEARLY_DOWN")

    # 3d-2. 當 KC 中軌連續 2 根明顯下彎翻空，觸及止損線立即 TICK 移動止損平倉
    df_tr_turn = df_tr.copy()
    df_tr_turn.loc[df_tr_turn.index[-4], "kc_middle"] = 102.80
    df_tr_turn.loc[df_tr_turn.index[-3], "kc_middle"] = 102.75
    df_tr_turn.loc[df_tr_turn.index[-2], "kc_middle"] = 102.70
    df_tr_turn.loc[df_tr_turn.index[-1], "kc_middle"] = 102.60  # 連續下彎
    res_tr_exit = evaluate_kc_layered_exit(df_tr_turn, position=pos_tr, live_price=100.1)
    assert res_tr_exit["should_exit"] is True
    assert res_tr_exit["reason"] == "ACTIVE_EXIT_PV_TRAILING_PROFIT_LOCK"
    assert res_tr_exit["trigger_level"] == "TICK"
    assert res_tr_exit["enter_flip_watch"] is False

    # 3e. 若 1.5*ATR 低於雙邊手續費 (atr=0.05 < fee=0.10)，峰值僅 +0.09 仍不得啟動！
    pos_small_atr = {"side": "LONG", "entry_price": 100.0, "entry_atr": 0.05}
    df_small = _make_df([100.0 + i * 0.01 for i in range(21)], is_last_closed=False, kc_width_pct=0.02)
    df_small["atr"] = 0.05
    res_fee_guard = evaluate_kc_layered_exit(df_small, position=pos_small_atr, live_price=100.09)
    assert res_fee_guard["state_updates"]["pv_trailing_armed"] is False


def test_1000pepe_1442_short_does_not_prematurely_arm_trailing_at_1443():
    """回歸測試：1000PEPE/USDT 14:42 空單（1m ATR 僅 ~0.14%）剛跌 +0.34% 不可在 14:43 過早啟動移動止損洗出場。"""
    from core.services.low_latency_exit import evaluate_kc_layered_exit

    closes_dn = [0.004560 - i * 0.0000012 for i in range(21)]
    df_dn = _make_df(closes_dn, is_last_closed=False, kc_width_pct=0.009)
    df_dn["atr"] = 6.392857e-06
    df_dn.attrs["symbol"] = "1000PEPE/USDT"

    pos_1442 = {
        "symbol": "1000PEPE/USDT",
        "side": "SHORT",
        "entry_price": 0.00453725,
        "entry_atr": 6.392857e-06,
        "pv_trough_price": 0.0045218,  # +0.34% (< 0.80% trailing_activate_pct)
    }
    res_1443 = evaluate_kc_layered_exit(df_dn, position=pos_1442, live_price=0.00452865, symbol="1000PEPE/USDT")
    assert res_1443["should_exit"] is False
    assert res_1443["state_updates"]["pv_trailing_armed"] is False
    assert res_1443["reason"] == "HOLD_KC_TREND_INTACT"


def test_kc_layered_exit_v2_extreme_fractal_035_trailing_and_flat_veto():
    """驗證升級版 4 層順序：極值 85% 三點分型逃頂 (優先於移動鎖利且進 Flip Watch)、0.35% 低門檻鎖利、FLAT 禁進觀察期。"""
    from core.services.low_latency_exit import evaluate_kc_layered_exit

    cfg_default = PeakValleyExitConfig()
    assert cfg_default.extreme_fractal_kc_ratio == pytest.approx(0.85)
    assert cfg_default.extreme_fractal_min_profit_pct == pytest.approx(0.003)
    assert cfg_default.trailing_activate_min_pct in (0.0035, 0.0060)

    # ── 1. Layer 2 極值頂分型（>=85% 半寬 + >=0.30% 獲利），優先於 Layer 3 移動止損，並允許進 Flip Watch ──
    df_top = _make_df([100.0 + i * 0.05 for i in range(21)], is_last_closed=True, kc_width_pct=0.04)
    df_top["atr"] = 0.2
    # kc_mid=101.0, kc_upper=103.0 -> 85% 門檻 = 101.0 + 0.85 * 2.0 = 102.70
    # 頂分型確認時，KC 中軌連續兩根明顯下彎（例如 kc_middle[-2]=101.05, kc_middle[-1]=100.99）
    df_top.loc[df_top.index[-4], "kc_middle"] = 101.10
    df_top.loc[df_top.index[-3], ["high", "low", "close", "kc_middle"]] = [102.10, 101.50, 102.00, 101.08]
    df_top.loc[df_top.index[-2], ["high", "low", "close", "kc_middle", "kc_upper", "kc_lower"]] = [
        102.85, 102.00, 102.70, 101.05, 103.00, 99.00
    ]
    df_top.loc[df_top.index[-1], ["high", "low", "close", "kc_middle", "kc_upper", "kc_lower"]] = [
        102.60, 101.80, 102.20, 100.99, 103.00, 99.00
    ]
    # 即使同時已啟動移動止損且 live_price 跌破 trailing_stop_price，也必須由 Layer 2 極值分型搶先平倉並進 Flip Watch
    pos_top = {
        "side": "LONG",
        "entry_price": 100.0,
        "entry_atr": 0.2,
        "pv_trailing_armed": True,
        "trailing_stop_price": 102.30,
    }
    res_top = evaluate_kc_layered_exit(df_top, position=pos_top, live_price=102.15)
    assert res_top["should_exit"] is True
    assert res_top["reason"] == "ACTIVE_EXIT_PV_EXTREME_FRACTAL_LONG"
    assert res_top["trigger_level"] == "BAR_CLOSE"
    assert res_top["enter_flip_watch"] is True
    assert res_top["flip_watch_side"] == "SHORT"

    # 1a-2. 若 KC 中軌仍向上 (kc_mid_slope > 0) 且方向仍為 LONG，極值分型亦被 HOLD 續抱阻斷
    df_top_slope_up = df_top.copy()
    df_top_slope_up.loc[df_top_slope_up.index[-1], "kc_middle"] = 101.12
    res_top_slope_hold = evaluate_kc_layered_exit(df_top_slope_up, position=pos_top, live_price=102.40)
    assert res_top_slope_hold["should_exit"] is False
    assert res_top_slope_hold["reason"] in ("HOLD_KC_MID_SLOPE_INTACT", "HOLD_KC_MID_NOT_CLEARLY_DOWN")

    # 1b. 若波峰僅在半山腰（102.10 < 102.70）-> 忽略分型續抱
    df_mid_frac = df_top.copy()
    df_mid_frac.loc[df_mid_frac.index[-2], "high"] = 102.25
    df_mid_frac.loc[df_mid_frac.index[-3], "high"] = 101.90
    res_mid_hold = evaluate_kc_layered_exit(
        df_mid_frac, position={"side": "LONG", "entry_price": 100.0, "entry_atr": 0.2}, live_price=102.20
    )
    assert res_mid_hold["should_exit"] is False

    # 1c. 若收盤獲利不足 0.30%（entry=102.0, k[-1].close=102.20 -> +0.196%）-> 忽略分型續抱
    res_low_profit = evaluate_kc_layered_exit(
        df_top, position={"side": "LONG", "entry_price": 102.0, "entry_atr": 0.2}, live_price=102.20
    )
    assert res_low_profit["should_exit"] is False

    # 1d. 對稱空單極值底分型（<= kc_mid - 0.85 * half_w）-> ACTIVE_EXIT_PV_EXTREME_FRACTAL_SHORT + Flip Watch LONG
    df_bot = _make_df([103.0 - i * 0.05 for i in range(21)], is_last_closed=True, kc_width_pct=0.04)
    df_bot["atr"] = 0.2
    df_bot.loc[df_bot.index[-4], "kc_middle"] = 100.90
    df_bot.loc[df_bot.index[-3], ["high", "low", "close", "kc_middle"]] = [101.80, 101.20, 101.30, 100.95]
    df_bot.loc[df_bot.index[-2], ["high", "low", "close", "kc_middle", "kc_upper", "kc_lower"]] = [
        101.30, 99.15, 99.30, 101.00, 103.00, 99.00
    ]
    df_bot.loc[df_bot.index[-1], ["high", "low", "close", "kc_middle", "kc_upper", "kc_lower"]] = [
        101.40, 99.40, 99.80, 101.05, 103.00, 99.00
    ]
    res_bot = evaluate_kc_layered_exit(
        df_bot, position={"side": "SHORT", "entry_price": 101.0, "entry_atr": 0.2}, live_price=99.80
    )
    assert res_bot["should_exit"] is True
    assert res_bot["reason"] == "ACTIVE_EXIT_PV_EXTREME_FRACTAL_SHORT"
    assert res_bot["trigger_level"] == "BAR_CLOSE"
    assert res_bot["enter_flip_watch"] is True
    assert res_bot["flip_watch_side"] == "LONG"

    # ── 2. Layer 3 啟動門檻：低 ATR 幣種達啟動門檻即啟動移動止損 ──
    df_low_atr = _make_df([100.0 + i * 0.01 for i in range(21)], is_last_closed=False, kc_width_pct=0.02)
    df_low_atr["atr"] = 0.14
    pos_low_atr = {"side": "LONG", "entry_price": 100.0, "entry_atr": 0.14}
    res_arm_036 = evaluate_kc_layered_exit(
        df_low_atr, position=pos_low_atr, live_price=100.36, config=PeakValleyExitConfig(trailing_activate_min_pct=0.0035)
    )
    assert res_arm_036["should_exit"] is False
    assert res_arm_036["state_updates"]["pv_trailing_armed"] is True

    # ── 3. Layer 4：中軌沒向下則 HOLD 續抱；只有中軌下彎或方向轉空才平多 ──
    df_flat = _make_df([100.0 + i * 0.05 for i in range(21)], is_last_closed=True, kc_width_pct=0.04)
    df_flat["atr"] = 0.5
    df_flat.loc[df_flat.index[-2], "kc_middle"] = 101.00
    df_flat.loc[df_flat.index[-1], ["close", "kc_middle"]] = [100.40, 101.00]  # 跌破 1.0*ATR 緩衝但中軌走平未向下 -> 強制續抱
    res_flat = evaluate_kc_layered_exit(
        df_flat, position={"side": "LONG", "entry_price": 100.8, "entry_atr": 0.5}, live_price=100.40
    )
    assert res_flat["should_exit"] is False
    assert res_flat["reason"] in ("HOLD_KC_MID_SLOPE_INTACT", "HOLD_KC_MID_NOT_CLEARLY_DOWN")

    # 若中軌連續兩根明確向下翻空且跌破緩衝區 -> 放行 ACTIVE_EXIT_KC_MID_BUFFER_LONG
    df_short_break = df_flat.copy()
    df_short_break.loc[df_short_break.index[-3], "kc_middle"] = 101.05
    df_short_break.loc[df_short_break.index[-2], "kc_middle"] = 101.00
    df_short_break.loc[df_short_break.index[-1], ["close", "kc_middle"]] = [100.40, 100.95]
    res_short_break = evaluate_kc_layered_exit(
        df_short_break, position={"side": "LONG", "entry_price": 100.8, "entry_atr": 0.5}, live_price=100.40
    )
    assert res_short_break["should_exit"] is True
    assert res_short_break["reason"] == "ACTIVE_EXIT_KC_MID_BUFFER_LONG"
    assert res_short_break["trigger_level"] == "BAR_CLOSE"


def test_1621_lobster_unpassed_kc_middle_does_not_exit_on_kc_trend_reversal():
    """回歸 16:21 龙虾/USDT：未過 KC 中軌前（空單 close <= kc_middle、多單 close >= kc_middle）不可因中軌微幅轉向/走平而平倉。"""
    from core.services.low_latency_exit import evaluate_kc_layered_exit

    # SHORT 持倉：綠K反彈但收盤仍在中軌之下 (0.01768 <= 0.01770)，即使 kc_middle 微幅上拐成 LONG 或 FLAT 也不可平倉
    df_s = _make_df([0.01780 - i * 0.000006 for i in range(21)], is_last_closed=True, kc_width_pct=0.008)
    df_s["atr"] = 0.00004
    df_s.loc[df_s.index[-2], "kc_middle"] = 0.017699
    df_s.loc[df_s.index[-1], ["open", "close", "kc_middle"]] = [0.01766, 0.01768, 0.017700]
    res_s = evaluate_kc_layered_exit(
        df_s,
        symbol="龙虾/USDT",
        position={"side": "SHORT", "entry_price": 0.01764, "entry_atr": 0.00004},
        live_price=0.01768,
    )
    assert res_s["should_exit"] is False
    assert res_s["reason"] == "HOLD_KC_TREND_INTACT"

    # LONG 持倉：對稱驗證，收盤仍在中軌之上 (0.01772 >= 0.01770)，即使 kc_middle 微幅下拐成 SHORT 也不可平倉
    df_l = _make_df([0.01755 + i * 0.000006 for i in range(21)], is_last_closed=True, kc_width_pct=0.008)
    df_l["atr"] = 0.00004
    df_l.loc[df_l.index[-2], "kc_middle"] = 0.017701
    df_l.loc[df_l.index[-1], ["open", "close", "kc_middle"]] = [0.01774, 0.01772, 0.017700]
    res_l = evaluate_kc_layered_exit(
        df_l,
        symbol="龙虾/USDT",
        position={"side": "LONG", "entry_price": 0.01768, "entry_atr": 0.00004},
        live_price=0.01772,
    )
    assert res_l["should_exit"] is False
    assert res_l["reason"] == "HOLD_KC_TREND_INTACT"


def test_kc_mid_slope_intact_blocks_mid_buffer_and_fractal_for_long_and_short():
    """回歸 1000PEPE/USDT：KC 中軌斜率方向未變時，攔截 Layer2 極值分型與 Layer4A 中軌緩衝破位平倉，保留 Layer1 硬止損與 Layer3 移動止損。"""
    from core.services.low_latency_exit import evaluate_kc_layered_exit

    # 1. SHORT 空單：KC 中軌仍向下 (100.00 -> 99.90)，單根陽線收盤衝破 KC_middle + 1.0*ATR -> 強制續抱
    df_s = _make_df([101.0 - i * 0.05 for i in range(21)], is_last_closed=True, kc_width_pct=0.04)
    df_s["atr"] = 0.4
    df_s.loc[df_s.index[-2], "kc_middle"] = 100.00
    df_s.loc[df_s.index[-1], ["open", "close", "kc_middle"]] = [99.85, 100.45, 99.90]
    res_s_hold = evaluate_kc_layered_exit(
        df_s, symbol="1000PEPE/USDT", position={"side": "SHORT", "entry_price": 100.30, "entry_atr": 0.4}, live_price=100.15
    )
    assert res_s_hold["should_exit"] is False
    assert res_s_hold["reason"] in ("HOLD_KC_MID_SLOPE_INTACT", "HOLD_KC_MID_NOT_CLEARLY_UP")

    # 但若觸及 Layer 1 硬止損 (100.30 + 2*0.4 = 101.10)，仍必須即時 TICK 保護平倉
    res_s_sl = evaluate_kc_layered_exit(
        df_s, symbol="1000PEPE/USDT", position={"side": "SHORT", "entry_price": 100.30, "entry_atr": 0.4}, live_price=101.15
    )
    assert res_s_sl["should_exit"] is True
    assert res_s_sl["reason"] == "ACTIVE_EXIT_KC_HARD_STOP_LOSS"

    # 若 KC 中軌仍向下，觸及移動止損線亦被阻斷續抱
    res_s_trail_hold = evaluate_kc_layered_exit(
        df_s,
        symbol="1000PEPE/USDT",
        position={"side": "SHORT", "entry_price": 100.30, "entry_atr": 0.4, "pv_trailing_armed": True, "trailing_stop_price": 100.05},
        live_price=100.10,
    )
    assert res_s_trail_hold["should_exit"] is False
    assert res_s_trail_hold["reason"] in ("HOLD_KC_MID_SLOPE_INTACT", "HOLD_KC_MID_NOT_CLEARLY_UP")

    # 當 KC 中軌連續 2 根明顯向上翻揚 (s0_pct >= 0.00015 且 s1_pct >= 0.00015) 觸及移動止損線時 -> 放行 ACTIVE_EXIT_PV_TRAILING_PROFIT_LOCK
    df_s_trail_turn = df_s.copy()
    df_s_trail_turn.loc[df_s_trail_turn.index[-3], "kc_middle"] = 99.95
    df_s_trail_turn.loc[df_s_trail_turn.index[-2], "kc_middle"] = 100.00
    df_s_trail_turn.loc[df_s_trail_turn.index[-1], "kc_middle"] = 100.05
    res_s_trail_exit = evaluate_kc_layered_exit(
        df_s_trail_turn,
        symbol="1000PEPE/USDT",
        position={"side": "SHORT", "entry_price": 100.30, "entry_atr": 0.4, "pv_trailing_armed": True, "trailing_stop_price": 100.05},
        live_price=100.10,
    )
    assert res_s_trail_exit["should_exit"] is True
    assert res_s_trail_exit["reason"] == "ACTIVE_EXIT_PV_TRAILING_PROFIT_LOCK"

    # 當 KC 中軌連續 2 根明顯向上翻揚 且收盤衝破 KC_middle + 1.0*ATR -> 允許 ACTIVE_EXIT_KC_MID_BUFFER_SHORT
    df_s_turn = df_s_trail_turn.copy()
    df_s_turn.loc[df_s_turn.index[-1], "close"] = 100.50
    res_s_exit = evaluate_kc_layered_exit(
        df_s_turn, symbol="1000PEPE/USDT", position={"side": "SHORT", "entry_price": 100.30, "entry_atr": 0.4}, live_price=100.50
    )
    assert res_s_exit["should_exit"] is True
    assert res_s_exit["reason"] == "ACTIVE_EXIT_KC_MID_BUFFER_SHORT"

    # 2. LONG 多單對稱：KC 中軌未連續向下，即使出現頂分型或跌破中軌緩衝 -> 強制續抱
    df_l = _make_df([99.0 + i * 0.05 for i in range(21)], is_last_closed=True, kc_width_pct=0.04)
    df_l["atr"] = 0.4
    df_l.loc[df_l.index[-2], "kc_middle"] = 100.00
    df_l.loc[df_l.index[-1], ["open", "close", "kc_middle"]] = [100.15, 99.55, 100.10]
    res_l_hold = evaluate_kc_layered_exit(
        df_l, symbol="1000PEPE/USDT", position={"side": "LONG", "entry_price": 99.70, "entry_atr": 0.4}, live_price=99.60
    )
    assert res_l_hold["should_exit"] is False
    assert res_l_hold["reason"] in ("HOLD_KC_MID_SLOPE_INTACT", "HOLD_KC_MID_NOT_CLEARLY_DOWN")


def test_1850_1858_inside_mid_buffer_does_not_exit_before_valley():
    """回歸 18:50 1000PEPE/USDT & 18:58 龙虾/USDT：收盤僅微越中軌但仍在 0.5*ATR 緩衝區內，即使 KC 中軌微幅翻紅/走平也不可提前平倉。"""
    from core.services.low_latency_exit import evaluate_kc_layered_exit

    for sym in ("1000PEPE/USDT", "龙虾/USDT"):
        # SHORT 空單：收盤僅超過中軌 +0.1*ATR（未突破 +0.5*ATR 緩衝），即使 kc_middle 微幅上拐也必須 HOLD
        df_s = _make_df([100.0 - i * 0.02 for i in range(21)], is_last_closed=True, kc_width_pct=0.02)
        df_s["atr"] = 0.40
        df_s.loc[df_s.index[-2], "kc_middle"] = 100.000
        df_s.loc[df_s.index[-1], ["open", "close", "kc_middle"]] = [99.98, 100.05, 100.005]
        res_s = evaluate_kc_layered_exit(
            df_s, symbol=sym, position={"side": "SHORT", "entry_price": 100.00, "entry_atr": 0.40}, live_price=100.05
        )
        assert res_s["should_exit"] is False
        assert res_s["reason"] == "HOLD_KC_TREND_INTACT"

        # LONG 多單對稱：收盤僅跌破中軌 -0.1*ATR（未跌破 -0.5*ATR 緩衝），即使 kc_middle 微幅下拐也必須 HOLD
        df_l = _make_df([100.0 + i * 0.02 for i in range(21)], is_last_closed=True, kc_width_pct=0.02)
        df_l["atr"] = 0.40
        df_l.loc[df_l.index[-2], "kc_middle"] = 100.000
        df_l.loc[df_l.index[-1], ["open", "close", "kc_middle"]] = [100.02, 99.95, 99.995]
        res_l = evaluate_kc_layered_exit(
            df_l, symbol=sym, position={"side": "LONG", "entry_price": 100.00, "entry_atr": 0.40}, live_price=99.95
        )
        assert res_l["should_exit"] is False
        assert res_l["reason"] == "HOLD_KC_TREND_INTACT"


def test_cross_symbol_sync_lock_blocks_mid_buffer_and_fractal():
    """驗證跨幣種大趨勢共振鎖（Cross-Symbol Trend Sync Lock）：
    當 1000PEPE 與 龙虾 雙幣同處空頭推進趨勢時，鎖死主動平空（破中軌緩衝與分型），
    但保留 Layer 1 硬止損，且移動止損容忍度自動放大 1.3 倍；任一幣種連續 2 根明確向上反轉即解鎖。
    """
    from core.services.low_latency_exit import (
        evaluate_kc_layered_exit,
        register_channel_frame,
    )

    # 1. 構造雙幣空頭推進數據：兩者皆 ck_direction == SHORT 且 KC 中軌持續向下
    pepe_df = _make_df([0.004600 - i * 0.000005 for i in range(21)], is_last_closed=True, kc_width_pct=0.03)
    pepe_df["atr"] = 0.00002
    pepe_df.loc[pepe_df.index[-2], "kc_middle"] = 0.004560
    # PEPE 當根向上刺破中軌緩衝區
    pepe_df.loc[pepe_df.index[-1], ["open", "close", "kc_middle"]] = [0.004550, 0.004590, 0.004555]

    lobster_df = _make_df([0.01800 - i * 0.00002 for i in range(21)], is_last_closed=True, kc_width_pct=0.03)
    lobster_df["atr"] = 0.0001
    lobster_df.loc[lobster_df.index[-2], "kc_middle"] = 0.01770
    lobster_df.loc[lobster_df.index[-1], ["open", "close", "kc_middle"]] = [0.01765, 0.01760, 0.01766]

    # 註冊龙虾行情
    register_channel_frame("龙虾/USDT", lobster_df)

    # 評估 PEPE 空單平倉：原本破中軌緩衝會平倉，但在雙幣共振空頭鎖下被強制鎖定續抱！
    res_pepe = evaluate_kc_layered_exit(
        pepe_df,
        symbol="1000PEPE/USDT",
        position={"side": "SHORT", "entry_price": 0.004570, "entry_atr": 0.00002},
        live_price=0.004580,
    )
    assert res_pepe["should_exit"] is False
    assert res_pepe["reason"] == "HOLD_CROSS_SYMBOL_BEAR_SYNC_LOCK"
    assert res_pepe["metrics"]["is_sync_locked"] is True

    # 2. 測試 Layer 1 硬止損絕不受共振鎖限制（必須立即市價止損保護本金）
    res_pepe_hard_sl = evaluate_kc_layered_exit(
        pepe_df,
        symbol="1000PEPE/USDT",
        position={"side": "SHORT", "entry_price": 0.004570, "entry_atr": 0.00002},
        live_price=0.004620,  # 0.004570 + 2.0 * 0.00002 = 0.004610 -> 觸發硬止損
    )
    assert res_pepe_hard_sl["should_exit"] is True
    assert res_pepe_hard_sl["reason"] == "ACTIVE_EXIT_KC_HARD_STOP_LOSS"

    # 3. 測試解鎖條件：當龙虾連續 2 根 1M K 線明確向上反轉抬頭 (s0_pct >= 0.00015 且 s1_pct >= 0.00015)
    lobster_reversed = lobster_df.copy()
    lobster_reversed.loc[lobster_reversed.index[-3], "kc_middle"] = 0.01760
    lobster_reversed.loc[lobster_reversed.index[-2], "kc_middle"] = 0.01765
    lobster_reversed.loc[lobster_reversed.index[-1], "kc_middle"] = 0.01770
    register_channel_frame("龙虾/USDT", lobster_reversed)

    res_pepe_unlocked = evaluate_kc_layered_exit(
        pepe_df,
        symbol="1000PEPE/USDT",
        position={"side": "SHORT", "entry_price": 0.004570, "entry_atr": 0.00002},
        live_price=0.004580,
    )
    # 共振解除後，PEPE 若自身中軌仍向下則退回一般中軌守護或放行
    assert res_pepe_unlocked["metrics"]["is_sync_locked"] is False
    assert res_pepe_unlocked["reason"] != "HOLD_CROSS_SYMBOL_BEAR_SYNC_LOCK"

    # 4. 多頭共振對稱測試：兩者皆 ck_direction == LONG 且 KC 中軌持續向上
    bull_pepe = _make_df([0.004500 + i * 0.000005 for i in range(21)], is_last_closed=True, kc_width_pct=0.03)
    bull_pepe["atr"] = 0.00002
    bull_pepe.loc[bull_pepe.index[-2], "kc_middle"] = 0.004580
    bull_pepe.loc[bull_pepe.index[-1], ["open", "close", "kc_middle"]] = [0.004590, 0.004550, 0.004585]

    bull_lobster = _make_df([0.01750 + i * 0.00002 for i in range(21)], is_last_closed=True, kc_width_pct=0.03)
    bull_lobster["atr"] = 0.0001
    bull_lobster.loc[bull_lobster.index[-2], "kc_middle"] = 0.01770
    bull_lobster.loc[bull_lobster.index[-1], ["open", "close", "kc_middle"]] = [0.01775, 0.01780, 0.01775]

    register_channel_frame("龙虾/USDT", bull_lobster)

    res_pepe_bull = evaluate_kc_layered_exit(
        bull_pepe,
        symbol="1000PEPE/USDT",
        position={"side": "LONG", "entry_price": 0.004570, "entry_atr": 0.00002},
        live_price=0.004560,
    )
    assert res_pepe_bull["should_exit"] is False
    assert res_pepe_bull["reason"] == "HOLD_CROSS_SYMBOL_BULL_SYNC_LOCK"
    assert res_pepe_bull["metrics"]["is_sync_locked"] is True


def test_5m_master_trend_veto_1m_mid_break_and_swing_high_sl():
    """驗證 5M 大局觀順勢出場與結構破壞止損：
    1. 當 5M 為 BEAR_ONLY 時，1M 價格即使反彈衝破 1M KC 中軌緩衝，也嚴格 HOLD 續抱不平空；
    2. 當反彈突破開倉前波段高點 + 0.5*ATR 時，放行結構破壞止損；
    3. 當平倉後進入 3 根 1M / 180 秒冷卻期，禁止立即反手或開新倉。
    """
    from core.services.low_latency_exit import (
        can_reenter_after_pv_exit,
        evaluate_kc_layered_exit,
    )

    # 構造 1M 數據（空單持倉中，最新一根 1M 向上刺破 1M KC 中軌緩衝區）
    pepe_df = _make_df([0.004580 - i * 0.000003 for i in range(21)], is_last_closed=True, kc_width_pct=0.02)
    pepe_df["atr"] = 0.00001
    pepe_df.loc[pepe_df.index[-2], "kc_middle"] = 0.004540
    # 衝破 1M KC 中軌 (0.004540 + 0.00001 = 0.004550) 到 0.004565
    pepe_df.loc[pepe_df.index[-1], ["open", "close", "kc_middle"]] = [0.004530, 0.004565, 0.004545]

    # 構造 5M K 棒為明確空頭 (BEAR_ONLY: 5M 收盤價在 5M 中軌下方且 5M 斜率向下)
    df_5m_bear = pd.DataFrame([
        {
            "timestamp": (1700000000 + i * 300) * 1000,
            "open": 0.004700 - i * 0.000005,
            "high": 0.004710 - i * 0.000005,
            "low": 0.004690 - i * 0.000005,
            "close": 0.004695 - i * 0.000005,
            "volume": 10000.0,
            "kc_middle": 0.004710 - i * 0.000004,
            "kc_upper": 0.004750,
            "kc_lower": 0.004650,
            "ma15": 0.004710 - i * 0.000004,
            "is_closed": True,
        }
        for i in range(25)
    ])

    pos = {
        "side": "SHORT",
        "entry_price": 0.004540,
        "entry_atr": 0.000015,
        "entry_swing_high": 0.004585,  # 前波大結構高點
    }

    # 1. 1M 破中軌在 5M BEAR_ONLY 架構下被一票否決，強迫續抱
    res_hold = evaluate_kc_layered_exit(
        pepe_df,
        symbol="1000PEPE/USDT",
        position=pos,
        live_price=0.004565,
        cross_symbol_frames={"5M": df_5m_bear},
    )
    assert res_hold["should_exit"] is False
    assert res_hold["reason"] == "HOLD_5M_BEAR_ONLY_VETO_1M_MID_BREAK"

    # 2. 當反彈突破前波大結構高點 + 0.5*ATR (0.004560 + 0.0000075 = 0.0045675) 但未達硬止損 (0.004570)
    pos_swing = dict(pos)
    pos_swing["entry_swing_high"] = 0.004560
    res_break = evaluate_kc_layered_exit(
        pepe_df,
        symbol="1000PEPE/USDT",
        position=pos_swing,
        live_price=0.004568,
        cross_symbol_frames={"5M": df_5m_bear},
    )
    assert res_break["should_exit"] is True
    assert res_break["reason"] == "ACTIVE_EXIT_SWING_HIGH_STRUCTURE_BREAK"
    assert res_break["trigger_level"] == "TICK"

    # 3. 平倉後冷卻測試：強制 3 根 1M K 棒冷卻
    exit_rec = {
        "exit_timestamp": 1000.0,
        "exit_bar_id": 60000,
        "closed_side": "SHORT",
        "reason": "ACTIVE_EXIT_SWING_HIGH_STRUCTURE_BREAK",
    }
    # 僅過 1 根 (bar_id = 120000) -> 阻斷
    ok_b1, r_b1 = can_reenter_after_pv_exit(
        exit_rec, current_bar_id=120000, candidate_side="LONG", now_ts=1060.0
    )
    assert ok_b1 is False and "COOLDOWN" in r_b1
    # 滿 3 根 (bar_id = 240000, now_ts = 1190.0 > 180s) -> 解除冷卻
    ok_b3, _ = can_reenter_after_pv_exit(
        exit_rec, current_bar_id=240000, candidate_side="LONG", now_ts=1200.0
    )
    assert ok_b3 is True
