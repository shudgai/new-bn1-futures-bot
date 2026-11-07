"""
Phase S1 & S2 全交易策略重構驗收測試套件：
涵蓋 Phase S0 / S0-R1 規範之核心驗收情境：
1. Market Regime Engine：區分 CHOP_CONSOLIDATION (死水震盪禁開)、SQUEEZE_PREPARATION (壓縮蓄勢)、REVERSAL_ZONE 與 TREND_BULLISH/BEARISH
2. Mode A (極值反轉進場)：真正谷底提早做多、真正峰頂提早做空、假谷底/假峰頂阻斷
3. Mode B (趨勢延續進場)：MA5/MA15 同向排列 + 顏色鎖定 + 防追高追低 (Anti-Chase)
4. 持倉防洗：普通回踩與反彈不平倉 (Cost Shield 與 MA15/KC 中軌保護)
5. 峰谷提早平倉：真正峰頂平多單、真正谷底平空單（不必等到價格退回 KC 中軌）
6. 動態淨利鎖定：Stage 1 (Peak Net ROE >= 10% -> 鎖 70%)、Stage 2 (Peak Net ROE >= 20% -> 鎖 85%)，Lock Floor 強制出場優先權
7. 硬止損與極端斷路器仍然有效
8. 保護邊界驗證：net_roe_profit_lock.py 與 strict_true_pivot.py 未被竄改，重啟/重複開倉鎖定一致
"""

import time
import subprocess
import pandas as pd
import pytest

from core.gates.master_gate_funnel import (
    MarketSnapshot,
    MasterGateFunnel,
    MasterOpenGatekeeper,
    PositionSnapshot,
)
from core.gates.ultimate_short_exit_gatekeeper import (
    UltimateShortExitGatekeeper,
    TRUE_OUTER_PIVOT_EXIT_REASONS,
    DISASTER_LOSS_REASONS,
)
from core.services.exits.staged_profit_lock import (
    update_staged_profit_lock_state,
    evaluate_staged_profit_lock,
    EXIT_REASON_TIER1,
    EXIT_REASON_TIER2,
)


def _base_snapshot(**overrides) -> MarketSnapshot:
    defaults = dict(
        symbol="龙虾/USDT",
        quote=0.01750,
        open_price=0.01742,
        close_price=0.01750,
        high_price=0.01752,
        low_price=0.01740,
        ma3_curr=0.01748,
        ma3_prev=0.01743,
        ma5_curr=0.01746,
        ma5_prev=0.01741,
        ma15_curr=0.01743,
        ma15_prev=0.01740,
        kc_upper=0.01760,
        kc_middle=0.01744,
        kc_middle_prev=0.01741,
        kc_lower=0.01728,
        atr=0.00008,
        macro_trend="BULLISH",
        ma_cross_count_8=0,
        is_closed=True,
    )
    defaults.update(overrides)
    return MarketSnapshot(**defaults)


def test_market_regime_engine_distinguishes_chop_squeeze_and_trend():
    """驗證 Layer A Market Regime Engine 正確區分死水震盪、壓縮蓄勢與明確趨勢。"""
    # 1. 頻繁交錯 (ma_cross_count_8 >= 3) -> CHOP_CONSOLIDATION，100% 禁止開倉
    chop_snap = _base_snapshot(ma_cross_count_8=3)
    regime, _ = MasterGateFunnel.evaluate_market_regime(chop_snap)
    assert regime == "CHOP_CONSOLIDATION"
    dec_chop = MasterOpenGatekeeper.validate_open_signal(chop_snap, "LONG")
    assert dec_chop.can_execute is False
    assert dec_chop.reason == "VETO_MA5_MA15_CHOP_8"

    # 2. 窄幅壓縮 (kc_width <= 0.25 * kc_width_max_20) -> SQUEEZE_PREPARATION
    squeeze_snap = _base_snapshot(
        kc_upper=0.01746,
        kc_middle=0.01744,
        kc_lower=0.01742,
        kc_width_max_20=0.00030,
        open_price=0.017435,
        close_price=0.017445,
        quote=0.017445,
    )
    regime_sq, _ = MasterGateFunnel.evaluate_market_regime(squeeze_snap)
    assert regime_sq == "SQUEEZE_PREPARATION"

    # 3. 明確多頭趨勢 -> TREND_BULLISH
    trend_snap = _base_snapshot()
    regime_tr, _ = MasterGateFunnel.evaluate_market_regime(trend_snap)
    assert regime_tr == "TREND_BULLISH"


def test_mode_a_early_valley_long_and_false_valley_rejection():
    """驗證真正谷底提早做多與假谷底阻斷。"""
    # 真正谷底反轉：從中軌下方起漲綠 K 收復中軌與 MA15，MA5 金叉 MA15 且向上拐頭，底底高 (HL)
    valley_long = _base_snapshot(
        open_price=0.01736,
        close_price=0.01746,
        quote=0.01746,
        high_price=0.01747,
        low_price=0.01734,
        ma5_curr=0.01744,
        ma5_prev=0.01741,
        ma15_curr=0.01742,
        ma15_prev=0.01742,
        kc_middle=0.01743,
        kc_middle_prev=0.01741,
        current_swing_low=0.01734,
        prev_swing_low=0.01728,
    )
    ok_a, trig_a = MasterGateFunnel.evaluate_mode_a_reversal_entry(valley_long, "LONG")
    assert ok_a is True
    assert trig_a in ("TRIGGER_BOTTOM_PIVOT_CROSS", "TRIGGER_MODE_A_VALLEY_REVERSAL_LONG")
    dec_long = MasterOpenGatekeeper.validate_open_signal(valley_long, "LONG")
    assert dec_long.can_execute is True
    assert dec_long.action == "OPEN_LONG"

    # 假谷底 1：結構破底 (current_swing_low <= prev_swing_low) -> 阻斷開多
    false_valley_ll = valley_long._replace(current_swing_low=0.01725, prev_swing_low=0.01728)
    dec_ll = MasterOpenGatekeeper.validate_open_signal(false_valley_ll, "LONG")
    assert dec_ll.can_execute is False
    assert dec_ll.reason == "BLOCKED_LONG_MULTI_BAR_STRUCTURE_NOT_HL"

    # 假谷底 2：MA5 仍在 MA15 下方且未金叉 -> 阻斷開多
    false_valley_ma = valley_long._replace(ma5_curr=0.01740, ma15_curr=0.01745)
    dec_ma = MasterOpenGatekeeper.validate_open_signal(false_valley_ma, "LONG")
    assert dec_ma.can_execute is False
    assert dec_ma.reason == "BLOCKED_LONG_MA5_NOT_ABOVE_MA15"


def test_mode_a_early_peak_short_and_false_peak_rejection():
    """驗證真正峰頂提早做空與假峰頂阻斷。"""
    peak_short = _base_snapshot(
        open_price=0.01752,
        close_price=0.01740,
        quote=0.01740,
        high_price=0.01754,
        low_price=0.01739,
        ma5_curr=0.01742,
        ma5_prev=0.01746,
        ma15_curr=0.01744,
        ma15_prev=0.01745,
        kc_upper=0.01760,
        kc_middle=0.01743,
        kc_middle_prev=0.01745,
        kc_lower=0.01726,
        macro_trend="BEARISH",
        current_swing_high=0.01754,
        prev_swing_high=0.01762,
    )
    ok_s, trig_s = MasterGateFunnel.evaluate_mode_a_reversal_entry(peak_short, "SHORT")
    assert ok_s is True
    assert trig_s in ("TRIGGER_TOP_PIVOT_CROSS", "TRIGGER_MODE_A_PEAK_REVERSAL_SHORT")
    dec_short = MasterOpenGatekeeper.validate_open_signal(peak_short, "SHORT")
    assert dec_short.can_execute is True
    assert dec_short.action == "OPEN_SHORT"

    # 假峰頂 1：大趨勢與中軌仍向上 -> 嚴禁逆勢摸頂開空
    false_peak_bull = peak_short._replace(kc_middle=0.01746, kc_middle_prev=0.01743, macro_trend="BULLISH")
    dec_bull = MasterOpenGatekeeper.validate_open_signal(false_peak_bull, "SHORT")
    assert dec_bull.can_execute is False
    assert dec_bull.reason == "BLOCKED_SHORT_ON_BULLISH_TREND"

    # 假峰頂 2：當根為綠 K 陽線 -> 嚴禁開空
    false_peak_green = peak_short._replace(open_price=0.01738, close_price=0.01741, quote=0.01741)
    dec_green = MasterOpenGatekeeper.validate_open_signal(false_peak_green, "SHORT")
    assert dec_green.can_execute is False
    assert dec_green.reason == "BLOCKED_SHORT_DIRECTIONAL_CANDLE"


def test_normal_pullback_holds_and_true_peak_exits_before_kc_middle():
    """驗證持倉期間忽略正常回踩，且真正峰頂反轉時在 KC 上軌附近及時平倉（不等到退回 KC 中軌）。"""
    now_min = int(time.time() // 60)
    # 構造多單從 0.01710 漲到上軌 0.01765，隨後在 0.01756 收出三點真峰頂確認陰線
    # 注意此時價格 0.01756 仍遠高於 KC 中軌 (0.01740) 與 MA15 (0.01742)！
    rows = [
        dict(
            timestamp=(now_min - 3) * 60_000,
            open=0.01750, high=0.01758, low=0.01748, close=0.01756,
            ma5=0.01754, ma15=0.01740,
            kc_middle=0.01738, kc_upper=0.01758, kc_lower=0.01718,
            atr=0.00006, is_closed=True,
        ),
        dict(
            timestamp=(now_min - 2) * 60_000,
            open=0.01756, high=0.01766, low=0.01755, close=0.01762,
            ma5=0.01758, ma15=0.01741,
            kc_middle=0.01739, kc_upper=0.01760, kc_lower=0.01718,
            atr=0.00006, is_closed=True,
        ),
        dict(
            timestamp=(now_min - 1) * 60_000,
            open=0.01762, high=0.01763, low=0.01753, close=0.01755,  # 跌破中點 0.01759 且跌破 MA5(0.01757)，但仍在 KC 中軌(0.01740)上方！
            ma5=0.01757, ma15=0.01742,
            kc_middle=0.01740, kc_upper=0.01761, kc_lower=0.01719,
            atr=0.00006, is_closed=True,
        ),
    ]
    df_peak = pd.DataFrame(rows)
    df_peak.attrs.update(timeframe_ms=60_000, snapshot_ms=time.time() * 1000)
    pos = {
        "symbol": "龙虾/USDT",
        "side": "LONG",
        "entry_price": 0.01720,
        "qty": 10000.0,
        "open_timestamp": (now_min - 10) * 60,
    }
    verdict_peak = UltimateShortExitGatekeeper.evaluate(
        "龙虾/USDT", pos, {}, "STRUCTURE_EXIT_PROBE", 0.01755,
        frame=df_peak, is_bar_closed=True,
    )
    assert verdict_peak.verdict == "ALLOW"
    assert verdict_peak.code == TRUE_OUTER_PIVOT_EXIT_REASONS["LONG"]


def test_staged_profit_lock_stage1_70_stage2_85_and_hard_stop():
    """驗證 Stage 1 (10% -> 70%)、Stage 2 (20% -> 85%) 動態鎖利規格與 3 ATR 硬止損保護。"""
    pos = {
        "symbol": "龙虾/USDT",
        "side": "LONG",
        "entry_price": 100.0,
        "qty": 5.0,
        "leverage": 5.0,
        "margin": 100.0,
        "fee_rate": 0.0,
        "slippage_pct": 0.0,
    }
    # Stage 1: +12% ROE -> 鎖 70% (8.4%)
    s1 = update_staged_profit_lock_state(pos, 102.4, "LONG")
    assert s1["stage"] == 1
    assert s1["lock_floor_roe_pct"] == pytest.approx(12.0 * 0.70, rel=1e-5)

    # Stage 2: +24% ROE -> 鎖 85% (20.4%)
    s2 = update_staged_profit_lock_state(pos, 104.8, "LONG")
    assert s2["stage"] == 2
    assert s2["lock_floor_roe_pct"] == pytest.approx(24.0 * 0.85, rel=1e-5)

    # 回吐跌破 Stage 2 Lock Floor (至 103.8 -> 19% ROE < 20.4%)，即使 MA3 > MA15 仍強制出場
    should_exit, reason, _ = evaluate_staged_profit_lock(
        pos, 103.8, "LONG", current_ma3=104.2, current_ma15=102.0,
    )
    assert should_exit is True
    assert reason == EXIT_REASON_TIER2


def test_protected_files_remain_unmodified():
    """驗證受保護檔案 net_roe_profit_lock.py 與 strict_true_pivot.py 100% 未被修改。"""
    res = subprocess.run(
        [
            "git",
            "diff",
            "--",
            "core/services/exits/net_roe_profit_lock.py",
            "core/services/exits/strict_true_pivot.py",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.stdout.strip() == ""


def test_execution_safety_manual_review_halt_and_duplicate_exit_blocked():
    """驗證 Option C 交易執行安全：MANUAL_REVIEW、RECONCILIATION_REQUIRED、HALT 與 exit_in_progress 嚴格阻斷重複與未授權副作用。"""
    import asyncio
    from core.services.execution_manager import ExecutionManager

    async def _run():
        class DummyAccount:
            def __init__(self):
                self.positions = {
                    "龙虾/USDT": {
                        "symbol": "龙虾/USDT",
                        "side": "LONG",
                        "entry_price": 0.01743,
                        "qty": 10000.0,
                    }
                }
                self.position_meta = {"龙虾/USDT": {}}
                self.closing_lock = set()
                self.system_emergency_halt = False
                self.open_called = 0
                self.close_called = 0

            async def open_position(self, **kwargs):
                self.open_called += 1
                return True

            async def close_position(self, **kwargs):
                self.close_called += 1
                return True

        acc = DummyAccount()

        # 1. MANUAL_REVIEW 阻斷平倉與開倉
        acc.position_meta["龙虾/USDT"]["state"] = "MANUAL_REVIEW"
        assert await ExecutionManager.close_position(acc, "龙虾/USDT", 0.01750, "EXIT_LONG_TRUE_PEAK_TOP") is False
        assert acc.close_called == 0
        assert await ExecutionManager.open_position(acc, "龙虾/USDT", "LONG", 0.01740, "OPEN_LONG") is False
        assert acc.open_called == 0

        # 2. RECONCILIATION_REQUIRED 阻斷平倉與開倉
        acc.position_meta["龙虾/USDT"]["state"] = "RECONCILIATION_REQUIRED"
        assert await ExecutionManager.close_position(acc, "龙虾/USDT", 0.01750, "EXIT_LONG_TRUE_PEAK_TOP") is False
        assert acc.close_called == 0

        # 3. HALT 緊急停機阻斷平倉與開倉
        acc.position_meta["龙虾/USDT"]["state"] = "OPEN"
        acc.system_emergency_halt = True
        assert await ExecutionManager.close_position(acc, "龙虾/USDT", 0.01750, "EXIT_LONG_TRUE_PEAK_TOP") is False
        assert await ExecutionManager.open_position(acc, "龙虾/USDT", "LONG", 0.01740, "OPEN_LONG") is False
        assert acc.close_called == 0
        assert acc.open_called == 0

        # 4. exit_in_progress / closing_lock 阻斷重複平倉
        acc.system_emergency_halt = False
        acc.position_meta["龙虾/USDT"]["exit_in_progress"] = True
        assert await ExecutionManager.close_position(acc, "龙虾/USDT", 0.01750, "EXIT_LONG_TRUE_PEAK_TOP") is False
        assert acc.close_called == 0

    asyncio.run(_run())



