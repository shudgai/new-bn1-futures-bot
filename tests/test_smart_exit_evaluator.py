"""
tests/test_smart_exit_evaluator.py
==================================
驗證智慧平倉邏輯 (evaluate_smart_exit)：
1. 中軌向上時，短線回踩 MA5 絕不平多 (HOLD)
2. 峰頂三點確立時，第一時間平多翻空 (FLIP_TO_SHORT)
3. 中軌轉向向下且實體跌破中軌時，平多翻空
4. 空單對稱測試
"""
import pandas as pd
import pytest
from core.services.smart_exit_evaluator import evaluate_smart_exit
from core.strategies.true_peak_trough_strategy import PositionState
from core.gates.unified_pipeline_gates import ExitGate
from core.services.data_pipeline import MarketDataSnapshot


def test_smart_exit_holds_on_pullback_when_kc_mid_is_up():
    """
    復盤場景：買多在 0.01752，價格拉升後小幅回踩 MA5，但 KC 中軌持續向上。
    系統必須堅定 HOLD，絕不在半山腰平多被洗出場！
    """
    df = pd.DataFrame([
        {"open": 0.01740, "high": 0.01745, "low": 0.01739, "close": 0.01744, "kc_mid": 0.01735},
        {"open": 0.01744, "high": 0.01755, "low": 0.01743, "close": 0.01752, "kc_mid": 0.01738},
        {"open": 0.01752, "high": 0.01757, "low": 0.01748, "close": 0.01749, "kc_mid": 0.01742}, # 回踩但未跌破前低 l1(0.01743)，且中軌強烈向上
    ])
    result = evaluate_smart_exit(df, PositionState.LONG)
    assert result["action"] == "HOLD"
    assert "KC中軌持續向上" in result["reason"]


def test_smart_exit_holds_on_green_bar_surge():
    """
    1000PEPE 案例復盤：大綠 K 陽線拉升噴出時，系統必須 100% 強行鎖定 HOLD，不准平多！
    """
    df = pd.DataFrame([
        {"open": 0.00510, "high": 0.00512, "low": 0.00508, "close": 0.00511, "kc_mid": 0.00505},
        {"open": 0.00511, "high": 0.00516, "low": 0.00510, "close": 0.00515, "kc_mid": 0.00508},
        {"open": 0.00515, "high": 0.00522, "low": 0.00514, "close": 0.00521, "kc_mid": 0.00512}, # 強勢大綠 K (close 0.00521 > open 0.00515)
    ])
    result = evaluate_smart_exit(df, PositionState.LONG)
    assert result["action"] == "HOLD"
    assert "強勢陽線" in result["reason"]


def test_smart_exit_flips_to_short_on_true_peak_3bar():
    """
    衝高至最高峰頂 0.01757 後，第 3 根收陰且跌破第 2 根最低價 l1，峰頂三點 100% 確立！
    立刻發動高位平多鎖盈 / 翻空！
    """
    df = pd.DataFrame([
        {"open": 0.01745, "high": 0.01750, "low": 0.01744, "close": 0.01749, "kc_mid": 0.01735},
        {"open": 0.01749, "high": 0.01757, "low": 0.01750, "close": 0.01756, "kc_mid": 0.01738}, # 第 2 點：衝出高點 0.01757, low=0.01750
        {"open": 0.01755, "high": 0.01756, "low": 0.01746, "close": 0.01748, "kc_mid": 0.01740}, # 第 3 點：收陰 (0.01748 < 0.01755) 且跌破 l1 (0.01748 < 0.01750)
    ])
    result = evaluate_smart_exit(df, PositionState.LONG)
    assert result["action"] == "FLIP_TO_SHORT"
    assert "峰頂三點" in result["reason"]


def test_smart_exit_flips_when_kc_midline_turns_down_and_breaks():
    """
    若中軌轉向向下且價格跌破中軌，波段結束，平多離場。
    """
    df = pd.DataFrame([
        {"open": 0.01750, "high": 0.01752, "low": 0.01745, "close": 0.01746, "kc_mid": 0.01748},
        {"open": 0.01746, "high": 0.01747, "low": 0.01735, "close": 0.01736, "kc_mid": 0.01745},
        {"open": 0.01736, "high": 0.01737, "low": 0.01728, "close": 0.01730, "kc_mid": 0.01740}, # 陰線收跌破中軌 (0.01730 < 0.01740)
    ])
    result = evaluate_smart_exit(df, PositionState.LONG)
    assert result["action"] == "FLIP_TO_SHORT"


def test_smart_exit_holds_on_red_bar_dump_for_short():
    """
    龍蝦/USDT 空單復盤場景：
    0.01765823 開空後，當根 1m K 線收盤是紅 K (陰線急跌，例如 close 0.017611 < open 0.017650)，
    賣壓正猛烈爆發中，系統必須 100% 強行鎖定 HOLD，絕不平空！
    """
    df = pd.DataFrame([
        {"open": 0.01770, "high": 0.01772, "low": 0.01768, "close": 0.01769, "kc_mid": 0.01775},
        {"open": 0.01769, "high": 0.01770, "low": 0.01764, "close": 0.01765, "kc_mid": 0.01772},
        {"open": 0.01765, "high": 0.01766, "low": 0.01760, "close": 0.01761, "kc_mid": 0.01768}, # 強勢大紅 K 陰線急跌 (close 0.01761 < open 0.01765)
    ])
    result = evaluate_smart_exit(df, PositionState.SHORT)
    assert result["action"] == "HOLD"
    assert "強勢陰線" in result["reason"] or "紅K" in result["reason"] or "跌" in result["reason"]


def test_smart_exit_short_trough_3bar():
    """
    空單對稱：谷底三點確立，低位精準平空並翻多。
    """
    df = pd.DataFrame([
        {"open": 0.01750, "high": 0.01752, "low": 0.01745, "close": 0.01746, "kc_mid": 0.01755},
        {"open": 0.01746, "high": 0.01747, "low": 0.01730, "close": 0.01732, "kc_mid": 0.01752}, # 第 2 點：探低 0.01730, high=0.01747
        {"open": 0.01733, "high": 0.01750, "low": 0.01732, "close": 0.01749, "kc_mid": 0.01750}, # 第 3 點：收陽且突破 h1 (0.01749 > 0.01747)
    ])
    result = evaluate_smart_exit(df, PositionState.SHORT)
    assert result["action"] == "FLIP_TO_LONG"
    assert "谷底三點" in result["reason"]


def test_exit_gate_holds_on_ma5_pullback_when_kc_slope_up():
    """
    驗證 ExitGate 內部整合：當 KC 中軌向上 (slope > 0) 且未現三點峰頂時，
    哪怕價格跌破 MA5/MA15，100% 觸發 VETO_HOLD，絕不提早平多！
    """
    pos = {
        "side": "LONG",
        "entry_price": 0.01752,
        "highest_price": 0.01757,
        "peak_roe": 0.002, # 浮盈較小，無三點峰頂
        "open_timestamp": 1000.0,
    }
    df = pd.DataFrame([
        {"open": 0.01740, "high": 0.01745, "low": 0.01738, "close": 0.01744, "ma5": 0.01742, "ma15": 0.01738, "kc_middle": 0.01735, "atr": 0.0001, "kc_upper": 0.01755, "kc_lower": 0.01715},
        {"open": 0.01744, "high": 0.01755, "low": 0.01743, "close": 0.01752, "ma5": 0.01746, "ma15": 0.01740, "kc_middle": 0.01738, "atr": 0.0001, "kc_upper": 0.01758, "kc_lower": 0.01718},
        {"open": 0.01752, "high": 0.01757, "low": 0.01746, "close": 0.01748, "ma5": 0.01750, "ma15": 0.01742, "kc_middle": 0.01742, "atr": 0.0001, "kc_upper": 0.01762, "kc_lower": 0.01722},
    ])
    snap = MarketDataSnapshot(
        symbol="龙虾/USDT",
        current_price=0.01748,
        curr_k={"open": 0.01752, "close": 0.01748, "high": 0.01757, "low": 0.01746, "ma5": 0.01750},
        prev_k={"open": 0.01744, "close": 0.01752, "high": 0.01755, "low": 0.01743, "ma5": 0.01746},
        ma5=0.01750,
        ma15=0.01742,
        ema_fast=0.01745,
        ema_slow=0.01740,
        ma_spread_pct=0.05,
        fast_slope=0.00001,
        slow_slope=0.00001,
        kc_middle=0.01742,
        kc_upper=0.01762,
        kc_lower=0.01722,
        kc_middle_slope=0.00004, # KC 中軌向上
        atr=0.0001,
        macro_trend="BULLISH",
        ma3=0.01748,
        prev_ma3=0.01752,
    )
    snap.raw_df = df
    should_exit, reason, meta = ExitGate.evaluate(pos, snap)
    assert should_exit is False
    assert reason == "VETO_HOLD"


def test_evaluate_dual_strategy_all_branches():
    """
    全面驗證用戶授權之 evaluate_dual_strategy 核心邏輯：
    1. 震盪盤防護 (ADX < 20, BW < 0.004) ➔ 凍結交易
    2. 多單綠 K 暴漲 ➔ HOLD
    3. 多單遇峰頂三點確立 / 跌破中軌 ➔ FLIP_TO_SHORT
    4. 空單紅 K 急跌 ➔ HOLD
    5. 空單遇谷底三點確立 / 突破中軌 ➔ FLIP_TO_LONG
    6. 無持倉波段起點進場 (真谷底起漲開多 / 真峰頂起跌開空)
    """
    from core.strategies.true_peak_trough_strategy import evaluate_dual_strategy, PositionState

    # 1. 震盪盤保護
    df_chop = pd.DataFrame([
        {"open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0, "kc_mid": 10.0, "adx": 15.0, "bandwidth": 0.002, "rsi": 50.0},
        {"open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0, "kc_mid": 10.0, "adx": 15.0, "bandwidth": 0.002, "rsi": 50.0},
        {"open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0, "kc_mid": 10.0, "adx": 15.0, "bandwidth": 0.002, "rsi": 50.0},
    ])
    res_chop = evaluate_dual_strategy(df_chop, PositionState.LONG)
    assert res_chop["action"] == "HOLD"
    assert "震盪盤保護" in res_chop["reason"]

    # 2. 多單遇大綠 K 暴漲 ➔ HOLD
    df_long_green = pd.DataFrame([
        {"open": 10.0, "high": 10.2, "low": 9.9, "close": 10.1, "kc_mid": 10.0, "adx": 30.0, "bandwidth": 0.02, "rsi": 55.0},
        {"open": 10.1, "high": 10.4, "low": 10.0, "close": 10.3, "kc_mid": 10.1, "adx": 30.0, "bandwidth": 0.02, "rsi": 60.0},
        {"open": 10.3, "high": 10.8, "low": 10.2, "close": 10.7, "kc_mid": 10.2, "adx": 35.0, "bandwidth": 0.03, "rsi": 68.0}, # 大綠 K
    ])
    res_lg = evaluate_dual_strategy(df_long_green, PositionState.LONG)
    assert res_lg["action"] == "HOLD"
    assert "強勢大綠 K" in res_lg["reason"]

    # 3. 多單遇峰頂三點確立 ➔ FLIP_TO_SHORT
    df_long_flip = pd.DataFrame([
        {"open": 10.0, "high": 10.2, "low": 9.9, "close": 10.1, "kc_mid": 10.0, "adx": 30.0, "bandwidth": 0.02, "rsi": 55.0},
        {"open": 10.1, "high": 10.8, "low": 10.0, "close": 10.7, "kc_mid": 10.1, "adx": 30.0, "bandwidth": 0.02, "rsi": 70.0}, # 衝頂
        {"open": 10.6, "high": 10.6, "low": 9.8, "close": 9.9, "kc_mid": 10.2, "adx": 30.0, "bandwidth": 0.02, "rsi": 55.0}, # 紅 K 跌破 l1 (10.0)
    ])
    res_lf = evaluate_dual_strategy(df_long_flip, PositionState.LONG)
    assert res_lf["action"] == "FLIP_TO_SHORT"
    assert "真峰頂三點確立" in res_lf["reason"]

    # 4. 空單遇大紅 K 急跌 ➔ HOLD
    df_short_red = pd.DataFrame([
        {"open": 10.5, "high": 10.6, "low": 10.3, "close": 10.4, "kc_mid": 10.5, "adx": 30.0, "bandwidth": 0.02, "rsi": 45.0},
        {"open": 10.4, "high": 10.4, "low": 10.1, "close": 10.2, "kc_mid": 10.4, "adx": 30.0, "bandwidth": 0.02, "rsi": 40.0},
        {"open": 10.2, "high": 10.2, "low": 9.7, "close": 9.8, "kc_mid": 10.3, "adx": 35.0, "bandwidth": 0.03, "rsi": 32.0}, # 大紅 K 急跌
    ])
    res_sr = evaluate_dual_strategy(df_short_red, PositionState.SHORT)
    assert res_sr["action"] == "HOLD"
    assert "強勢大紅 K" in res_sr["reason"]

    # 5. 空單遇谷底三點確立 ➔ FLIP_TO_LONG
    df_short_flip = pd.DataFrame([
        {"open": 10.5, "high": 10.6, "low": 10.3, "close": 10.4, "kc_mid": 10.5, "adx": 30.0, "bandwidth": 0.02, "rsi": 45.0},
        {"open": 10.4, "high": 10.4, "low": 9.6, "close": 9.7, "kc_mid": 10.4, "adx": 30.0, "bandwidth": 0.02, "rsi": 30.0}, # 探底 h1=10.4
        {"open": 9.8, "high": 10.6, "low": 9.8, "close": 10.5, "kc_mid": 10.6, "adx": 30.0, "bandwidth": 0.02, "rsi": 45.0}, # 綠 K 突破 h1 (10.4) 且 c0 <= kc_mid(10.6)
    ])
    res_sf = evaluate_dual_strategy(df_short_flip, PositionState.SHORT)
    assert res_sf["action"] == "FLIP_TO_LONG"
    assert "真谷底三點確立" in res_sf["reason"]

    # 6. 無持倉捕捉谷底起漲開多
    res_open_l = evaluate_dual_strategy(df_short_flip, PositionState.NONE)
    assert res_open_l["action"] == "OPEN_LONG"
    assert "谷底波段起漲點" in res_open_l["reason"]


def test_evaluate_precise_short_entry():
    """
    驗證專治「空在最低點」的開空邏輯 (evaluate_precise_short_entry)：
    1. 價格低於中軌或觸及下軌 ➔ 100% 拒絕開空 (防追空保護)
    2. 高位 (c > kc_mid) + 陰線吞噬前低 (c0 < l1) ➔ 授權開空
    3. 高位 (c > kc_mid) + 回踩 MA5 受阻 ➔ 補救開空
    """
    from core.strategies.true_peak_trough_strategy import evaluate_precise_short_entry, PositionState

    # 1. 價格跌到中軌下方 (0.01771 <= 0.01775) ➔ 嚴禁追空！
    df_bottom = pd.DataFrame([
        {"open": 0.01775, "high": 0.01776, "low": 0.01770, "close": 0.01774, "kc_mid": 0.01775, "bb_upper": 0.01790, "bb_lower": 0.01765, "ma5": 0.01773, "rsi": 40.0},
        {"open": 0.01774, "high": 0.01775, "low": 0.01768, "close": 0.01771, "kc_mid": 0.01775, "bb_upper": 0.01790, "bb_lower": 0.01765, "ma5": 0.01772, "rsi": 35.0},
    ])
    res_b = evaluate_precise_short_entry(df_bottom, PositionState.NONE)
    assert res_b["action"] == "HOLD"
    assert "防追空保護" in res_b["reason"]

    # 2. 峰頂高位 (c 0.01792 > kc_mid 0.01775) + 陰線吞噬前低 l1(0.01794) ➔ OPEN_SHORT
    df_peak = pd.DataFrame([
        {"open": 0.01785, "high": 0.01798, "low": 0.01784, "close": 0.01796, "kc_mid": 0.01775, "bb_upper": 0.01800, "bb_lower": 0.01765, "ma5": 0.01790, "rsi": 70.0},
        {"open": 0.01796, "high": 0.01797, "low": 0.01790, "close": 0.01792, "kc_mid": 0.01775, "bb_upper": 0.01800, "bb_lower": 0.01765, "ma5": 0.01791, "rsi": 62.0},
    ])
    res_p = evaluate_precise_short_entry(df_peak, PositionState.NONE)
    assert res_p["action"] == "OPEN_SHORT"
    assert "高位精準開空" in res_p["reason"]

    # 3. 高位回踩 MA5 受阻 (c0 0.01782 <= ma5 0.01783 且 > kc_mid 0.01775) ➔ 補救開空
    df_retest = pd.DataFrame([
        {"open": 0.01780, "high": 0.01788, "low": 0.01780, "close": 0.01785, "kc_mid": 0.01775, "bb_upper": 0.01800, "bb_lower": 0.01765, "ma5": 0.01783, "rsi": 58.0},
        {"open": 0.01785, "high": 0.01786, "low": 0.01781, "close": 0.01782, "kc_mid": 0.01775, "bb_upper": 0.01800, "bb_lower": 0.01765, "ma5": 0.01783, "rsi": 55.0},
    ])
    res_r = evaluate_precise_short_entry(df_retest, PositionState.NONE)
    assert res_r["action"] == "OPEN_SHORT"
    assert "高位補救開空" in res_r["reason"]


def test_missed_bottom_secondary_recovery_long():
    """
    驗證用戶反饋痛點：
    谷底平空後，徹底錯過了谷底起漲點 (價格已在中軌上方)，但隨後拉出強勢大綠 K 陽線突破 MA5 且 KC 中軌向上
    系統必須第一時間發動補救開多買回 (Secondary Entry)，絕不能看著價格暴漲空手！
    """
    from core.strategies.true_peak_trough_strategy import evaluate_dual_strategy, PositionState

    df_missed = pd.DataFrame([
        {"open": 0.005310, "high": 0.005315, "low": 0.005308, "close": 0.005312, "kc_mid": 0.005300, "ma5": 0.005308, "bb_upper": 0.005380, "bb_lower": 0.005250, "adx": 28.0, "bandwidth": 0.02},
        {"open": 0.005312, "high": 0.005318, "low": 0.005308, "close": 0.005315, "kc_mid": 0.005302, "ma5": 0.005310, "bb_upper": 0.005380, "bb_lower": 0.005250, "adx": 29.0, "bandwidth": 0.02},
        # 第三根：徹底在中軌上方 (c0 > kc_mid, l1 > kc_mid)，強勢大綠 K 突破 MA5 (0.005320)，KC 中軌向上
        {"open": 0.005315, "high": 0.005335, "low": 0.005314, "close": 0.005330, "kc_mid": 0.005305, "ma5": 0.005320, "bb_upper": 0.005380, "bb_lower": 0.005250, "adx": 32.0, "bandwidth": 0.025},
    ])
    res = evaluate_dual_strategy(df_missed, PositionState.NONE)
    assert res["action"] == "OPEN_LONG"
    assert "補救開多買回" in res["reason"]


def test_evaluate_high_precision_strategy_modules():
    """
    全面驗證用戶指示之 4 大核心升級方法 (evaluate_high_precision_strategy)：
    1. 震盪盤保護 (帶幅收斂 + ADX 低迷 ➔ 凍結交易)
    2. 強勢 K 線保護 (綠 K 暴漲不平多，紅 K 急跌不平空)
    3. 真峰谷三點吞噬 (低位強陽突破開多/平空，高位強陰跌破開空/平多)
    4. K 線走完才發出指令 (過濾盤中未完成 K 線，以 completed_bar 為準)
    5. 錯過谷底後的順勢補救開多
    """
    from core.services.smart_exit_evaluator import evaluate_high_precision_strategy, PositionState

    # 1. 震盪盤防禦
    df_chop = pd.DataFrame([
        {"open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0, "kc_mid": 10.0, "bandwidth": 0.002, "adx": 15.0},
        {"open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0, "kc_mid": 10.0, "bandwidth": 0.002, "adx": 15.0},
    ])
    res1 = evaluate_high_precision_strategy(df_chop, PositionState.NONE)
    assert res1["action"] == "HOLD"
    assert "凍結交易" in res1["reason"]

    # 2. 強勢 K 線單邊保護鎖
    # 多單遇大綠 K 陽線
    df_long_green = pd.DataFrame([
        {"open": 10.0, "high": 10.2, "low": 9.9, "close": 10.1, "kc_mid": 10.0, "bandwidth": 0.02, "adx": 30.0},
        {"open": 10.1, "high": 10.8, "low": 10.1, "close": 10.7, "kc_mid": 10.1, "bandwidth": 0.02, "adx": 30.0}, # 大綠 K
    ])
    res_lg = evaluate_high_precision_strategy(df_long_green, PositionState.LONG)
    assert res_lg["action"] == "HOLD"
    assert "大綠 K 暴漲中" in res_lg["reason"]

    # 空單遇大紅 K 陰線
    df_short_red = pd.DataFrame([
        {"open": 10.5, "high": 10.6, "low": 10.3, "close": 10.4, "kc_mid": 10.5, "bandwidth": 0.02, "adx": 30.0},
        {"open": 10.4, "high": 10.4, "low": 9.7, "close": 9.8, "kc_mid": 10.4, "bandwidth": 0.02, "adx": 30.0}, # 大紅 K
    ])
    res_sr = evaluate_high_precision_strategy(df_short_red, PositionState.SHORT)
    assert res_sr["action"] == "HOLD"
    assert "大紅 K 急跌中" in res_sr["reason"]

    # 3. 真峰谷三點吞噬
    # 低位大綠 K 吞噬突破前高 ➔ 翻多 / 開多 (低位 c0 10.4 <= kc_mid 10.5)
    df_trough = pd.DataFrame([
        {"open": 10.2, "high": 10.3, "low": 9.5, "close": 9.6, "kc_mid": 10.5, "bb_upper": 11.0, "bb_lower": 9.2, "bandwidth": 0.02, "adx": 25.0},
        {"open": 9.7, "high": 10.4, "low": 9.7, "close": 10.4, "kc_mid": 10.5, "bb_upper": 11.0, "bb_lower": 9.2, "bandwidth": 0.02, "adx": 25.0}, # c0=10.4 > h1=10.3, c0 <= kc_mid(10.5)
    ])
    res_trough_flip = evaluate_high_precision_strategy(df_trough, PositionState.SHORT)
    assert res_trough_flip["action"] == "FLIP_TO_LONG"
    assert "谷底確立" in res_trough_flip["reason"]

    res_trough_open = evaluate_high_precision_strategy(df_trough, PositionState.NONE)
    assert res_trough_open["action"] == "OPEN_LONG"
    assert "第一時間開多" in res_trough_open["reason"]

    # 高位大紅 K 吞噬跌破前低 ➔ 翻空 / 開空
    df_peak = pd.DataFrame([
        {"open": 10.0, "high": 10.8, "low": 10.0, "close": 10.7, "kc_mid": 10.1, "bb_upper": 11.0, "bb_lower": 9.5, "bandwidth": 0.02, "adx": 25.0},
        {"open": 10.6, "high": 10.6, "low": 9.8, "close": 9.9, "kc_mid": 10.1, "bb_upper": 11.0, "bb_lower": 9.5, "bandwidth": 0.02, "adx": 25.0}, # c0=9.9 < l1=10.0, c0 >= kc_mid(10.1? wait, c0=10.2 >= 10.1)
    ])
    df_peak.iloc[-1, df_peak.columns.get_loc("close")] = 10.2  # 確保 c0 >= kc_mid(10.1) 且 < l1(10.4)
    df_peak.iloc[-2, df_peak.columns.get_loc("low")] = 10.4
    res_peak_flip = evaluate_high_precision_strategy(df_peak, PositionState.LONG)
    assert res_peak_flip["action"] == "FLIP_TO_SHORT"
    assert "峰頂確立" in res_peak_flip["reason"]

    # 4. K 線走完驗證 (Bar Completion) - 自動過濾未收盤盤中棒
    df_live = pd.DataFrame([
        {"open": 10.0, "high": 10.2, "low": 9.9, "close": 10.1, "kc_mid": 10.0, "bandwidth": 0.02, "adx": 30.0, "is_closed": True},
        {"open": 10.1, "high": 10.8, "low": 10.1, "close": 10.7, "kc_mid": 10.1, "bandwidth": 0.02, "adx": 30.0, "is_closed": True}, # 已收盤大綠 K
        {"open": 10.7, "high": 10.7, "low": 10.6, "close": 10.6, "kc_mid": 10.1, "bandwidth": 0.02, "adx": 30.0, "is_closed": False}, # 盤中假影線未收盤
    ])
    # 策略應自動以已收盤之大綠 K 為準，保護鎖生效 HOLD
    res_live = evaluate_high_precision_strategy(df_live, PositionState.LONG)
    assert res_live["action"] == "HOLD"
    assert "大綠 K 暴漲中" in res_live["reason"]

    # 5. 補救開多
    df_secondary = pd.DataFrame([
        {"open": 10.2, "high": 10.4, "low": 10.1, "close": 10.3, "kc_mid": 10.0, "ma5": 10.2, "bb_upper": 11.0, "bb_lower": 9.5, "bandwidth": 0.02, "adx": 25.0},
        {"open": 10.3, "high": 10.6, "low": 10.2, "close": 10.5, "kc_mid": 10.1, "ma5": 10.3, "bb_upper": 11.0, "bb_lower": 9.5, "bandwidth": 0.02, "adx": 25.0}, # c0 10.5 > kc_mid 10.1, is_green, >= ma5
    ])
    res_sec = evaluate_high_precision_strategy(df_secondary, PositionState.NONE)
    assert res_sec["action"] == "OPEN_LONG"
    assert "補救開多" in res_sec["reason"]


def test_evaluate_short_exit_protection_pure():
    """
    驗證用戶指示之專用空單離場保護 (evaluate_short_position_holding):
    1. 當前為紅 K (陰線急跌) ➔ 100% 強行持空不平倉 (HOLD)
    2. 當前為強勢大綠 K 且突破前高且實體比例>=55% ➔ FLIP_TO_LONG
    3. 當前為綠 K 微幅反彈但未突破前高 ➔ KC中軌向下繼續持空 (HOLD)
    """
    from core.services.smart_exit_evaluator import (
        evaluate_short_position_holding,
        evaluate_short_exit_protection,
        PositionState,
    )

    # 1. 紅 K 急跌中
    df_red = pd.DataFrame([
        {"open": 0.01770, "high": 0.01772, "low": 0.01765, "close": 0.01768, "kc_mid": 0.01780},
        {"open": 0.01768, "high": 0.01769, "low": 0.01760, "close": 0.01762, "kc_mid": 0.01775}, # 紅 K
    ])
    res_r = evaluate_short_position_holding(df_red, PositionState.SHORT)
    assert res_r["action"] == "HOLD"
    assert "紅K" in res_r["reason"] or "紅 K" in res_r["reason"]

    # 1b. 【新規則】KC 中軌已向上 + 紅 K 回踩 ➔ 第一時間平空並翻多 (FLIP_TO_LONG)
    df_mid_up_red = pd.DataFrame([
        {"open": 0.01760, "high": 0.01765, "low": 0.01758, "close": 0.01762, "kc_mid": 0.01755},
        {"open": 0.01764, "high": 0.01766, "low": 0.01760, "close": 0.01761, "kc_mid": 0.01759}, # 紅 K + 中軌向上
    ])
    res_mid_up = evaluate_short_position_holding(df_mid_up_red, PositionState.SHORT)
    assert res_mid_up["action"] == "FLIP_TO_LONG"
    assert "KC 中軌已向上" in res_mid_up["reason"]

    # 2. 綠 K 突破前高 (谷底第 3 點確立, 實體比例 >= 55%)
    # 前高 0.01769, c0=0.01772, o0=0.01762, h0=0.01773, l0=0.01761 -> range=0.00012, body=0.00010 (83% >= 55%)
    df_green_break = pd.DataFrame([
        {"open": 0.01768, "high": 0.01769, "low": 0.01760, "close": 0.01762, "kc_mid": 0.01780},
        {"open": 0.01762, "high": 0.01773, "low": 0.01761, "close": 0.01772, "kc_mid": 0.01775},
    ])
    res_g_break = evaluate_short_position_holding(df_green_break, PositionState.SHORT)
    assert res_g_break["action"] == "FLIP_TO_LONG"
    assert "谷底第 3 點確立" in res_g_break["reason"]

    # 3. 綠 K 微幅反彈但未突破前高 (前高 0.01775, c0=0.01766 < 0.01775, 中軌向下)
    df_green_small = pd.DataFrame([
        {"open": 0.01768, "high": 0.01775, "low": 0.01760, "close": 0.01762, "kc_mid": 0.01780},
        {"open": 0.01762, "high": 0.01768, "low": 0.01761, "close": 0.01766, "kc_mid": 0.01775},
    ])
    res_g_small = evaluate_short_position_holding(df_green_small, PositionState.SHORT)
    assert res_g_small["action"] == "HOLD"
    assert "KC中軌向下" in res_g_small["reason"]


def test_evaluate_long_exit_protection_pure():
    """
    驗證用戶指示之專用多單離場保護 (evaluate_long_position_holding):
    1. 當前為綠 K (陽線拉升) ➔ 100% 強行持多不平倉 (HOLD)
    2. 當前為強勢大紅 K 且跌破前低且實體比例>=55% ➔ FLIP_TO_SHORT (峰頂第 3 點確立)
    3. 當前為紅 K 微幅回踩但未跌破前低 ➔ KC中軌向上繼續持多 (HOLD)
    """
    from core.services.smart_exit_evaluator import (
        evaluate_long_position_holding,
        evaluate_long_exit_protection,
        PositionState,
    )

    # 1. 綠 K 拉升中
    df_green = pd.DataFrame([
        {"open": 0.01760, "high": 0.01765, "low": 0.01758, "close": 0.01762, "kc_mid": 0.01755},
        {"open": 0.01762, "high": 0.01770, "low": 0.01761, "close": 0.01768, "kc_mid": 0.01758}, # 綠 K
    ])
    res_g = evaluate_long_position_holding(df_green, PositionState.LONG)
    assert res_g["action"] == "HOLD"
    assert "綠K" in res_g["reason"] or "綠 K" in res_g["reason"]

    # 1b. 【新規則】KC 中軌已向下 + 綠 K 反彈 ➔ 第一時間平多並翻空 (FLIP_TO_SHORT)
    df_mid_down_green = pd.DataFrame([
        {"open": 0.01770, "high": 0.01772, "low": 0.01765, "close": 0.01768, "kc_mid": 0.01775},
        {"open": 0.01766, "high": 0.01771, "low": 0.01764, "close": 0.01769, "kc_mid": 0.01770}, # 綠 K + 中軌向下
    ])
    res_mid_down = evaluate_long_position_holding(df_mid_down_green, PositionState.LONG)
    assert res_mid_down["action"] == "FLIP_TO_SHORT"
    assert "KC 中軌已向下" in res_mid_down["reason"]

    # 2. 紅 K 跌破前低 (峰頂第 3 點確立, 實體比例 >= 55%)
    # 前低 l1=0.01761, c0=0.01758, o0=0.01768, h0=0.01769, l0=0.01757 -> range=0.00012, body=0.00010 (83% >= 55%)
    df_red_break = pd.DataFrame([
        {"open": 0.01762, "high": 0.01770, "low": 0.01761, "close": 0.01768, "kc_mid": 0.01755},
        {"open": 0.01768, "high": 0.01769, "low": 0.01757, "close": 0.01758, "kc_mid": 0.01755},
    ])
    res_r_break = evaluate_long_position_holding(df_red_break, PositionState.LONG)
    assert res_r_break["action"] == "FLIP_TO_SHORT"
    assert "峰頂第 3 點確立" in res_r_break["reason"]

    # 3. 紅 K 微幅回踩但未跌破前低 (前低 0.01755, c0=0.01764 > 0.01755, 中軌向上)
    df_red_small = pd.DataFrame([
        {"open": 0.01760, "high": 0.01770, "low": 0.01755, "close": 0.01768, "kc_mid": 0.01750},
        {"open": 0.01768, "high": 0.01769, "low": 0.01762, "close": 0.01764, "kc_mid": 0.01755},
    ])
    res_r_small = evaluate_long_position_holding(df_red_small, PositionState.LONG)
    assert res_r_small["action"] == "HOLD"
    assert "KC 中軌持續向上" in res_r_small["reason"] or "KC中軌向上" in res_r_small["reason"]
