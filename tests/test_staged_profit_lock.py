"""Tests for Staged Profit Lock (三階段階梯式移動鎖利引擎).

涵蓋第七章/第八章 12 項完整驗證情境：
1. 淨利 < 10%：MA3 與中軌交叉、回踩中軌，檢驗回傳 (False, "VETO_HOLD")，不觸發平倉。
2. 歷史最高淨利率達到 10%：檢驗 stage 正式變更為 1，armed = True。
3. 階段 1 回吐驗證：最高淨利為 12U 時，當淨利跌至 <= 8.4U (12 * 0.70)，精確觸發 EXIT_BY_STAGED_PROFIT_LOCK_TIER1。
4. 歷史最高淨利率達到 20%：檢驗 stage 立即升級為 2。
5. 階段 2 回吐驗證：最高淨利為 25U 時，當淨利跌至 <= 21.25U (25 * 0.85)，精確觸發 EXIT_BY_STAGED_PROFIT_LOCK_TIER2。
6. 階段 2 不可降級驗證：淨利回跌至 18% 時，stage 依然為 2，底線依然維持在 25 * 0.85 = 21.25U。
7. 多單與空單對稱性：LONG 與 SHORT 兩方向在相同淨利率情境下的行為 100% 鏡像對稱。
8. 扣除費用後的淨利門檻：測試當毛利達 10.2% 但扣除手續費後淨利率僅 9.8% 時，不觸發第一階段鎖利，驗證手續費確實被扣除。
9. 邊界值測試：精確測試 9.999%、10.000%、19.999%、20.000% 的浮點數邊界切換。
10. 重啟恢復測試：模擬 save_state 與 load_state，驗證重啟後持倉的鎖利階段與最高淨利完好無損。
11. 硬止損優先級：即便處於階段 0 或階段 1，當跌破 3 ATR 時，硬止損立即生效平倉。
12. 防前視偏誤回測驗證：跳空穿越鎖利線時，成交價以跳空價結算。
"""
import copy
import json
import math
import pytest
import pandas as pd

from core.services.exits.staged_profit_lock import (
    calculate_staged_net_pnl_and_roe,
    update_staged_profit_lock_state,
    evaluate_staged_profit_lock,
    check_stage0_veto_hold,
    EXIT_REASON_TIER1,
    EXIT_REASON_TIER2,
    TIER1_START_ROE_PCT,
    TIER2_START_ROE_PCT,
)
from core.gates.unified_pipeline_gates import ExitGate, MarketDataSnapshot
from core.config import TAKER_FEE_RATE, SLIPPAGE_PCT


def create_mock_position(
    symbol: str = "BTCUSDT",
    side: str = "LONG",
    entry_price: float = 100.0,
    qty: float = 5.0,
    leverage: float = 5.0,
    fee_rate: float = 0.0005,
    slippage_pct: float = 0.0002,
    margin: float = 100.0,
) -> dict:
    """建立測試用標準持倉物件。

    名義價值 = 100.0 * 5.0 = 500 USDT
    槓桿 5x -> 保證金 = 100 USDT
    1% 淨利 = 1 USDT 淨損益
    """
    return {
        "symbol": symbol,
        "side": side,
        "entry_price": entry_price,
        "qty": qty,
        "quantity": qty,
        "leverage": leverage,
        "margin": margin,
        "fee_rate": fee_rate,
        "slippage_pct": slippage_pct,
        "accumulated_funding_fee": 0.0,
        "open_timestamp": 1700000000.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 測試情境 1：淨利 < 10% 時忽略 MA3 與中軌交叉，強制 VETO_HOLD
# ─────────────────────────────────────────────────────────────────────────────
def test_scenario_1_stage0_ignores_ma3_and_midline_cross():
    pos = create_mock_position(side="LONG", entry_price=100.0, margin=100.0)
    # 現價 101.5 (淨利約 +6.8%，< 10%)
    price = 101.5

    # 模擬短線 MA3 或 KC 中軌破位出場訊號
    candidate_reasons = [
        "EXIT_BY_KC_MID_STRUCTURE_BROKEN",
        "EXIT_BY_TREND_BROKEN",
        "EXIT_BY_KC_MIDLINE_RETRACE",
        "EXIT_SHORT_ON_MA5_FLAT_REVERSAL",
        "EXIT_ON_MOMENTUM_EXHAUSTION",
    ]

    for candidate in candidate_reasons:
        is_vetoed, reason, meta = check_stage0_veto_hold(pos, candidate, price, "LONG")
        assert is_vetoed is True
        assert reason == "VETO_HOLD"
        assert meta["stage"] == 0
        assert meta["suppressed_exit_reason"] == candidate


# ─────────────────────────────────────────────────────────────────────────────
# 測試情境 2：歷史最高淨利率達到 10% 啟動階段 1
# ─────────────────────────────────────────────────────────────────────────────
def test_scenario_2_peak_net_roe_reaches_10pct_arms_stage_1():
    pos = create_mock_position(side="LONG", entry_price=100.0, margin=100.0)
    # 為使淨利達到 10.0U (10.0%)，毛利需為 10.0 + 手續費 (~0.6U) = 10.6U
    # price = 102.12
    # 測試逐步漲到 10% 淨利
    price_for_10pct = 102.2
    net_pnl, net_roe, _, _ = calculate_staged_net_pnl_and_roe(pos, price_for_10pct, "LONG")
    assert net_roe >= 10.0

    state = update_staged_profit_lock_state(pos, price_for_10pct, "LONG")
    assert state["stage"] == 1
    assert state["armed"] is True
    assert pos["profit_lock_armed"] is True
    assert state["lock_floor_net_pnl"] is not None
    # 鎖利底線應為最高淨利的 70%
    expected_floor_pnl = round(state["highest_net_pnl"] * 0.70, 6)
    assert math.isclose(state["lock_floor_net_pnl"], expected_floor_pnl, rel_tol=1e-5)


# ─────────────────────────────────────────────────────────────────────────────
# 測試情境 3：階段 1 回吐 30% 驗證（最高淨利 12U，跌至 <= 8.4U 觸發 TIER1）
# ─────────────────────────────────────────────────────────────────────────────
def test_scenario_3_stage_1_giveback_triggers_tier1():
    pos = create_mock_position(side="LONG", entry_price=100.0, margin=100.0)
    # 手動給定最高淨利 12.0U (12.0% ROE)
    pos["staged_profit_lock_state"] = {
        "stage": 1,
        "highest_net_pnl": 12.0,
        "highest_net_roe_pct": 12.0,
        "lock_floor_net_pnl": 8.40,  # 12.0 * 0.70 = 8.40
        "lock_floor_roe_pct": 8.40,
        "highest_favorable_price": 102.5,
        "initial_margin": 100.0,
        "armed": True,
    }

    # 1. 淨利在 9.5U (回吐未達 30%) -> 不出場
    # 計算此時價格
    should_exit_safe, _, _ = evaluate_staged_profit_lock(pos, 102.05, "LONG")
    net_pnl_safe, _, _, _ = calculate_staged_net_pnl_and_roe(pos, 102.05, "LONG")
    if net_pnl_safe > 8.40:
        assert should_exit_safe is False

    # 2. 價格回調使得淨利跌至 8.35U (<= 8.4U) -> 觸發 TIER1
    # 找尋使淨利 <= 8.40 的價格
    price_dropped = 101.80
    net_pnl_drop, _, _, _ = calculate_staged_net_pnl_and_roe(pos, price_dropped, "LONG")
    assert net_pnl_drop <= 8.40

    should_exit, reason, meta = evaluate_staged_profit_lock(pos, price_dropped, "LONG")
    assert should_exit is True
    assert reason == EXIT_REASON_TIER1
    assert meta["stage"] == 1
    assert meta["lock_floor_net_pnl"] == 8.40


# ─────────────────────────────────────────────────────────────────────────────
# 測試情境 4：歷史最高淨利率達到 20% 升級為階段 2
# ─────────────────────────────────────────────────────────────────────────────
def test_scenario_4_peak_net_roe_reaches_20pct_upgrades_to_stage_2():
    pos = create_mock_position(side="LONG", entry_price=100.0, margin=100.0)
    # 價格漲到使淨利率達 20% (price 約 104.25)
    price_for_20pct = 104.3
    net_pnl, net_roe, _, _ = calculate_staged_net_pnl_and_roe(pos, price_for_20pct, "LONG")
    assert net_roe >= 20.0

    state = update_staged_profit_lock_state(pos, price_for_20pct, "LONG")
    assert state["stage"] == 2
    assert state["armed"] is True
    # 鎖利底線應升級為最高淨利的 85%
    expected_floor_pnl = round(state["highest_net_pnl"] * 0.85, 6)
    assert math.isclose(state["lock_floor_net_pnl"], expected_floor_pnl, rel_tol=1e-5)


# ─────────────────────────────────────────────────────────────────────────────
# 測試情境 5：階段 2 回吐 15% 驗證（最高淨利 25U，跌至 <= 21.25U 觸發 TIER2）
# ─────────────────────────────────────────────────────────────────────────────
def test_scenario_5_stage_2_giveback_triggers_tier2():
    pos = create_mock_position(side="LONG", entry_price=100.0, margin=100.0)
    # 最高淨利 25.0U，底線 = 25.0 * 0.85 = 21.25U
    pos["staged_profit_lock_state"] = {
        "stage": 2,
        "highest_net_pnl": 25.0,
        "highest_net_roe_pct": 25.0,
        "lock_floor_net_pnl": 21.25,
        "lock_floor_roe_pct": 21.25,
        "highest_favorable_price": None,
        "initial_margin": 100.0,
        "armed": True,
    }

    # 價格回踩使得淨利跌至 <= 21.25U
    # 尋找使淨利約 20.8U 的價格
    price_dropped = 104.30
    net_pnl_drop, _, _, _ = calculate_staged_net_pnl_and_roe(pos, price_dropped, "LONG")
    assert net_pnl_drop <= 21.25

    should_exit, reason, meta = evaluate_staged_profit_lock(pos, price_dropped, "LONG")
    assert should_exit is True
    assert reason == EXIT_REASON_TIER2
    assert meta["stage"] == 2
    assert meta["lock_floor_net_pnl"] == 21.25


# ─────────────────────────────────────────────────────────────────────────────
# 測試情境 6：階段 2 不可降級鐵律（淨利跌至 18%，stage 仍為 2，底線維持 21.25U）
# ─────────────────────────────────────────────────────────────────────────────
def test_scenario_6_stage_2_non_downgrading_invariant():
    pos = create_mock_position(side="LONG", entry_price=100.0, margin=100.0)
    pos["staged_profit_lock_state"] = {
        "stage": 2,
        "highest_net_pnl": 25.0,
        "highest_net_roe_pct": 25.0,
        "lock_floor_net_pnl": 21.25,
        "lock_floor_roe_pct": 21.25,
        "highest_favorable_price": None,
        "initial_margin": 100.0,
        "armed": True,
    }

    # 當價格回跌，使得即時淨利率只有 18%
    # 更新狀態
    price_18pct = 103.8
    state = update_staged_profit_lock_state(pos, price_18pct, "LONG")

    # 檢驗不可降級
    assert state["stage"] == 2
    assert state["highest_net_pnl"] == 25.0
    assert state["highest_net_roe_pct"] == 25.0
    assert state["lock_floor_net_pnl"] == 21.25
    assert state["lock_floor_roe_pct"] == 21.25


# ─────────────────────────────────────────────────────────────────────────────
# 測試情境 7：LONG 與 SHORT 對稱性（相同淨利率鏡像對稱）
# ─────────────────────────────────────────────────────────────────────────────
def test_scenario_7_long_short_mirror_symmetry():
    pos_long = create_mock_position(side="LONG", entry_price=100.0, qty=5.0, margin=100.0)
    pos_short = create_mock_position(side="SHORT", entry_price=100.0, qty=5.0, margin=100.0)

    # LONG 漲 3% vs SHORT 跌 3%
    price_long = 103.0
    price_short = 97.0

    net_pnl_l, net_roe_l, gross_l, costs_l = calculate_staged_net_pnl_and_roe(pos_long, price_long, "LONG")
    net_pnl_s, net_roe_s, gross_s, costs_s = calculate_staged_net_pnl_and_roe(pos_short, price_short, "SHORT")

    assert math.isclose(gross_l, gross_s, rel_tol=1e-5)
    assert math.isclose(net_pnl_l, net_pnl_s, rel_tol=0.01)
    assert math.isclose(net_roe_l, net_roe_s, rel_tol=0.01)

    # 兩者在相應方向創新高後的鎖利行為對稱
    state_l = update_staged_profit_lock_state(pos_long, price_long, "LONG")
    state_s = update_staged_profit_lock_state(pos_short, price_short, "SHORT")

    assert state_l["stage"] == state_s["stage"]
    assert math.isclose(state_l["lock_floor_net_pnl"], state_s["lock_floor_net_pnl"], rel_tol=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# 測試情境 8：扣除費用後的淨利門檻（毛利 10.2%，扣費後淨利 9.8%，不啟動鎖利）
# ─────────────────────────────────────────────────────────────────────────────
def test_scenario_8_fee_deduction_prevents_premature_arming():
    pos = create_mock_position(side="LONG", entry_price=100.0, qty=5.0, margin=100.0)
    # 毛利 10.2U -> price = 100.0 + 10.2 / 5.0 = 102.04
    price = 102.04
    net_pnl, net_roe, gross_pnl, total_costs = calculate_staged_net_pnl_and_roe(pos, price, "LONG")

    # 毛利率 = 10.2%
    gross_roe = (gross_pnl / 100.0) * 100.0
    assert math.isclose(gross_roe, 10.2, abs_tol=0.01)
    # 扣除費用後淨利率應小於 10.0% (約 9.6% ~ 9.8%)
    assert net_roe < 10.0

    # 執行評估，確認 stage 維持 0，不啟動鎖利
    state = update_staged_profit_lock_state(pos, price, "LONG")
    assert state["stage"] == 0
    assert state["armed"] is False
    assert state["lock_floor_net_pnl"] is None

    should_exit, reason, _ = evaluate_staged_profit_lock(pos, price, "LONG")
    assert should_exit is False
    assert reason is None


# ─────────────────────────────────────────────────────────────────────────────
# 測試情境 9：邊界值測試（9.999%、10.000%、19.999%、20.000% 浮點精度切換）
# ─────────────────────────────────────────────────────────────────────────────
def test_scenario_9_boundary_values_switching():
    pos = create_mock_position(side="LONG", entry_price=100.0, margin=100.0)

    # 1. 9.999% -> Stage 0
    pos["staged_profit_lock_state"] = {"stage": 0, "highest_net_pnl": 9.999, "highest_net_roe_pct": 9.999}
    state = update_staged_profit_lock_state(pos, 100.0, "LONG")
    assert state["stage"] == 0

    # 2. 10.000% -> Stage 1
    pos["staged_profit_lock_state"] = {"stage": 0, "highest_net_pnl": 10.0, "highest_net_roe_pct": 10.0}
    state = update_staged_profit_lock_state(pos, 100.0, "LONG")
    assert state["stage"] == 1
    assert state["armed"] is True

    # 3. 19.999% -> Stage 1
    pos["staged_profit_lock_state"] = {"stage": 1, "highest_net_pnl": 19.999, "highest_net_roe_pct": 19.999}
    state = update_staged_profit_lock_state(pos, 100.0, "LONG")
    assert state["stage"] == 1

    # 4. 20.000% -> Stage 2
    pos["staged_profit_lock_state"] = {"stage": 1, "highest_net_pnl": 20.0, "highest_net_roe_pct": 20.0}
    state = update_staged_profit_lock_state(pos, 100.0, "LONG")
    assert state["stage"] == 2
    assert state["armed"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 測試情境 10：重啟恢復與持久化驗證
# ─────────────────────────────────────────────────────────────────────────────
def test_scenario_10_restart_persistence():
    pos = create_mock_position(side="LONG", entry_price=100.0, margin=100.0)
    pos["staged_profit_lock_state"] = {
        "stage": 2,
        "highest_net_pnl": 22.5,
        "highest_net_roe_pct": 22.5,
        "lock_floor_net_pnl": 19.125,
        "lock_floor_roe_pct": 19.125,
        "highest_favorable_price": None,
        "initial_margin": 100.0,
        "armed": True,
    }

    # 序列化成 JSON 再反序列化（模擬 save_state / load_state）
    serialized = json.dumps(pos)
    restored_pos = json.loads(serialized)

    assert restored_pos["staged_profit_lock_state"]["stage"] == 2
    assert restored_pos["staged_profit_lock_state"]["highest_net_pnl"] == 22.5
    assert restored_pos["staged_profit_lock_state"]["lock_floor_net_pnl"] == 19.125

    # 在重啟後評估，階段 2 與鎖利底線正常運作
    should_exit, reason, meta = evaluate_staged_profit_lock(restored_pos, 103.5, "LONG")
    assert meta["stage"] == 2
    assert meta["lock_floor_net_pnl"] == 19.125


# ─────────────────────────────────────────────────────────────────────────────
# 測試情境 11：硬止損優先級（即便處於階段 0 或階段 1，跌破 3 ATR 硬止損優先離場）
# ─────────────────────────────────────────────────────────────────────────────
def test_scenario_11_hard_stop_loss_priority_over_staged_lock():
    pos = create_mock_position(side="LONG", entry_price=100.0, margin=100.0)
    pos["staged_profit_lock_state"] = {
        "stage": 1,
        "highest_net_pnl": 11.0,
        "highest_net_roe_pct": 11.0,
        "lock_floor_net_pnl": 7.7,
        "lock_floor_roe_pct": 7.7,
        "highest_favorable_price": None,
        "initial_margin": 100.0,
        "armed": True,
    }

    # 模擬突發暴跌穿透 3 ATR
    atr_val = 1.0  # ATR = 1.0 (1%) -> 3 ATR = 3%
    crash_price = 96.5  # 跌幅 3.5% (超額跌破 3 ATR)

    snapshot = {
        "symbol": "BTCUSDT",
        "current_price": crash_price,
        "raw_df": pd.DataFrame([
            {"timestamp": 1700000000, "open": 100.0, "high": 102.3, "low": 96.5, "close": crash_price, "is_closed": True}
        ]),
        "atr": atr_val,
        "kc_middle": 100.0,
        "kc_upper": 102.0,
        "kc_lower": 98.0,
        "macro_trend": "BEARISH",
        "curr_k": {"ma5": crash_price, "ma15": crash_price, "close": crash_price, "open": crash_price},
        "prev_k": {"ma5": crash_price, "ma15": crash_price, "close": crash_price, "open": crash_price},
        "ma5": crash_price,
        "ma15": crash_price,
        "ema_fast": crash_price,
        "ema_slow": crash_price,
        "ma_spread_pct": 0.0,
        "fast_slope": 0.0,
        "slow_slope": 0.0,
        "kc_middle_slope": 0.0,
    }

    should_exit, reason, meta = ExitGate.evaluate(pos, snapshot, crash_price, atr=atr_val)
    assert should_exit is True
    # 必須以硬止損離場，優先級最高
    assert reason == "CIRCUIT_BREAKER_HARD_SL"


# ─────────────────────────────────────────────────────────────────────────────
# 測試情境 12：防前視偏誤回測驗證（跳空開盤穿透鎖利線以開盤價成交）
# ─────────────────────────────────────────────────────────────────────────────
def test_scenario_12_anti_lookahead_gap_down_fill():
    pos = create_mock_position(side="LONG", entry_price=100.0, margin=100.0)
    pos["staged_profit_lock_state"] = {
        "stage": 1,
        "highest_net_pnl": 12.0,
        "highest_net_roe_pct": 12.0,
        "lock_floor_net_pnl": 8.4,
        "lock_floor_roe_pct": 8.4,
        "highest_favorable_price": 102.5,
        "initial_margin": 100.0,
        "armed": True,
    }

    # 理想鎖利價格約為 101.8
    # 模擬下一根 K 棒跳空低開至 101.2 (低於鎖利線)
    gap_open_price = 101.2
    net_pnl_at_open, _, _, _ = calculate_staged_net_pnl_and_roe(pos, gap_open_price, "LONG")
    assert net_pnl_at_open < 8.4

    # 驗證 evaluate 得到的現價損益為開盤價水準 (net_pnl_at_open)，絕不虛構在 8.4U 處成交
    should_exit, reason, meta = evaluate_staged_profit_lock(pos, gap_open_price, "LONG")
    assert should_exit is True
    assert reason == EXIT_REASON_TIER1
    assert meta["current_net_pnl"] < 8.4  # 成交結算以真實價格計算
