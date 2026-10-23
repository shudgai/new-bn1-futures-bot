"""單元測試：量化合約策略優化核心引擎 (Anti-Chop & Momentum Entry + Agile Exit Gate)"""
import pytest
import pandas as pd
import numpy as np

from core.services.strategies.anti_chop_agile_strategy import (
    AntiChopEntryGate,
    AntiChopExitGate,
)


def _make_sample_df(
    closes, opens=None, highs=None, lows=None,
    kc_mid=100.0, kc_up=102.0, kc_low=98.0,
    slope=0.0, atr=1.0, ma3=None,
):
    n = len(closes)
    opens = opens or closes
    highs = highs or [max(o, c) + 0.2 for o, c in zip(opens, closes)]
    lows = lows or [min(o, c) - 0.2 for o, c in zip(opens, closes)]
    if ma3 is None:
        ma3 = pd.Series(closes).rolling(3, min_periods=1).mean().tolist()

    df = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "atr": [atr] * n,
        "kc_middle": [kc_mid] * n,
        "kc_upper": [kc_up] * n,
        "kc_lower": [kc_low] * n,
        "kc_middle_slope": [slope] * n,
        "ma3": ma3,
    })
    return df


# =============================================================================
# 測試模組 1：進場門禁重構 (AntiChopEntryGate)
# =============================================================================

def test_entry_macro_trend_filter_blocks_counter_trend():
    """大趨勢一票否決：中軌下行嚴禁做多，中軌上行嚴禁做空。"""
    # 1. 中軌大角度下行 (slope < -0.00001)，嘗試開多 -> 必須被攔截
    df_bear = _make_sample_df(closes=[99.0, 102.5, 103.0], slope=-0.0005)
    allowed_l, reason_l, _ = AntiChopEntryGate.evaluate_entry(
        df_bars=df_bear, target_side="LONG", current_quote=103.0,
    )
    assert allowed_l is False
    assert reason_l == "BLOCKED_LONG_ON_BEARISH_TREND"

    # 2. 中軌大角度上行 (slope > 0.00001)，嘗試開空 -> 必須被攔截
    df_bull = _make_sample_df(closes=[101.0, 97.5, 97.0], slope=0.0005)
    allowed_s, reason_s, _ = AntiChopEntryGate.evaluate_entry(
        df_bars=df_bull, target_side="SHORT", current_quote=97.0,
    )
    assert allowed_s is False
    assert reason_s == "BLOCKED_SHORT_ON_BULLISH_TREND"


def test_entry_chop_and_squeeze_filter():
    """死魚盤與箱體中軸糾纏一票否決。"""
    # 中軌完全走平 (slope=0)，且價格距離中軸不到 0.3 ATR -> 阻斷
    df_chop = _make_sample_df(
        closes=[100.0, 100.1, 100.05],
        opens=[100.0, 100.05, 100.08],
        kc_mid=100.0, slope=0.0, atr=1.0,
    )
    allowed, reason, _ = AntiChopEntryGate.evaluate_entry(
        df_bars=df_chop, target_side="SHORT", current_quote=100.05,
    )
    assert allowed is False
    assert reason == "BLOCKED_CHOP_AND_SQUEEZE"


def test_entry_super_impulse_bypass():
    """超級強動能脈衝豁免：大實體 K 棒 (>= 1.0 ATR) 順應趨勢無長影線，直接放行上車。"""
    # 爆量突破上軌大陽線 (body = 1.3 ATR, upper_wick = 0.1 ATR, bias = 1.8 ATR < 2.5 ATR)
    df_impulse = _make_sample_df(
        closes=[100.0, 101.0, 102.8],
        opens=[100.0, 100.5, 101.5],
        highs=[100.2, 101.2, 102.9],
        lows=[99.8, 100.4, 101.4],
        kc_mid=101.0, kc_up=102.0, slope=0.0002, atr=1.0,
    )
    # 即使傳入剛平倉冷卻狀態，超級強動能依然能夠豁免進場！
    last_trade = {"side": "long", "time": 1000.0}
    allowed, reason, meta = AntiChopEntryGate.evaluate_entry(
        df_bars=df_impulse, target_side="LONG", current_quote=102.8,
        last_closed_trade=last_trade, now_ts=1010.0,  # 僅過 10 秒
    )
    assert allowed is True
    assert reason == "AUTHORIZED_BY_SUPER_IMPULSE"
    assert meta["is_super_impulse"] is True


def test_entry_anti_chase_bias_filter():
    """過度延伸防追高防線：偏離慢線乖離超過 2.5 ATR 時禁止追單。"""
    df_chase = _make_sample_df(
        closes=[100.0, 101.0, 102.8],
        kc_mid=100.0, slope=0.0002, atr=1.0,
    )
    # 報價 103.0，偏離中軌 3.0 ATR > 2.5 ATR
    allowed, reason, meta = AntiChopEntryGate.evaluate_entry(
        df_bars=df_chase, target_side="LONG", current_quote=103.0,
    )
    assert allowed is False
    assert reason == "BLOCKED_OVEREXTENDED_BIAS"
    assert meta["bias_atr"] >= 2.5


def test_entry_two_bar_breakout_confirmation():
    """兩根收盤破軌確認：前一根收在軌外，當根亦收在軌外。"""
    # 1. 僅當根破軌 (前一根仍在軌內) -> 需等待第 2 根確認
    df_1bar = _make_sample_df(
        closes=[100.0, 101.5, 102.2],  # kc_up=102.0, p1=101.5 < 102.0
        opens=[100.0, 101.0, 101.6],
        kc_mid=100.0, kc_up=102.0, slope=0.0001, atr=1.0,
    )
    allowed_1, reason_1, _ = AntiChopEntryGate.evaluate_entry(
        df_bars=df_1bar, target_side="LONG", current_quote=102.2,
    )
    assert allowed_1 is False
    assert reason_1 == "WAITING_2BAR_CLOSE_CONFIRMATION"

    # 2. 連續兩根收在軌外 -> 放行開多
    df_2bars = _make_sample_df(
        closes=[100.0, 102.2, 102.4],  # p1=102.2 > 102.0, curr=102.4 > 102.0
        opens=[100.0, 101.5, 102.2],
        kc_mid=100.0, kc_up=102.0, slope=0.0001, atr=1.0,
    )
    allowed_2, reason_2, _ = AntiChopEntryGate.evaluate_entry(
        df_bars=df_2bars, target_side="LONG", current_quote=102.4,
    )
    assert allowed_2 is True
    assert reason_2 == "AUTHORIZED_BY_2BAR_BREAKOUT_LONG"


# =============================================================================
# 測試模組 3：出場與止盈防禦 (AntiChopExitGate)
# =============================================================================

def test_exit_ma3_v_reversal_exit_short():
    """空單谷底 MA3 急銳 V 轉反彈：大陽線 (>=0.5 ATR) 站上 MA3，第一時間敏捷平空逃底！"""
    # MA3 走勢: p2=95.0, p1=94.0, curr=94.8 (V 型向上勾頭)
    # 當根收飽滿陽線 (open=94.0, close=94.8, body=0.8 ATR)
    df_v_bottom = _make_sample_df(
        closes=[96.0, 94.0, 94.8],
        opens=[96.5, 95.8, 94.0],
        ma3=[95.0, 94.0, 94.5],  # curr(94.5) > p1(94.0) <= p2(95.0)
        kc_mid=98.0, slope=-0.0005, atr=1.0,
    )
    should_exit, reason, meta = AntiChopExitGate.evaluate_exit(
        df_bars=df_v_bottom,
        position_side="SHORT",
        entry_price=98.0,
        current_quote=94.8,
    )
    assert should_exit is True
    assert reason == "EXIT_SHORT_BY_MA3_V_BOTTOM"
    assert meta["bull_body"] >= 0.5


def test_exit_ma3_inverted_v_exit_long():
    """多單天花板 MA3 急銳倒 V 頂下殺：大陰線 (>=0.5 ATR) 跌破 MA3，第一時間敏捷平多逃頂！"""
    # MA3 走勢: p2=104.0, p1=105.0, curr=104.2 (倒 V 型向下拐頭)
    # 當根收飽滿陰線 (open=105.0, close=104.2, body=0.8 ATR)
    df_v_top = _make_sample_df(
        closes=[103.0, 105.0, 104.2],
        opens=[102.5, 104.0, 105.0],
        ma3=[104.0, 105.0, 104.5],  # curr(104.5) < p1(105.0) >= p2(104.0)
        kc_mid=100.0, slope=0.0005, atr=1.0,
    )
    should_exit, reason, meta = AntiChopExitGate.evaluate_exit(
        df_bars=df_v_top,
        position_side="LONG",
        entry_price=100.0,
        current_quote=104.2,
    )
    assert should_exit is True
    assert reason == "EXIT_LONG_BY_MA3_INVERTED_V_TOP"
    assert meta["bear_body"] >= 0.5


def test_exit_ratchet_max_profit_drawdown():
    """棘輪式極值回撤止盈：最大波幅曾達 >= 2.0 ATR，回吐達 45% 時市價鎖利！"""
    # 空單開在 100.0，低點曾達 97.0 (max_gain = 3.0 ATR)
    # 當根為小實體十字星 (open=98.3, close=98.4, body=0.1 < 0.5 ATR，不觸發 MA3 V轉)
    # 現價反彈至 98.4 (rebound = 1.4 >= 45% of 3.0 = 1.35)
    df_normal = _make_sample_df(
        closes=[99.0, 97.5, 98.4],
        opens=[99.5, 98.8, 98.3],
        kc_mid=101.0, slope=-0.0001, atr=1.0,
    )
    should_exit, reason, _ = AntiChopExitGate.evaluate_exit(
        df_bars=df_normal,
        position_side="SHORT",
        entry_price=100.0,
        current_quote=98.4,
        peak_favorable_price=97.0,
    )
    assert should_exit is True
    assert reason == "EXIT_BY_RATCHET_MAX_PROFIT_DRAWDOWN"


def test_exit_intrabar_midline_breach_cut():
    """盤中中軌秒破緊急打斷：空單若被暴力暴衝拉破中軌 + 0.2 ATR，不等收盤即刻跳車！"""
    # 空單，中軌 100.0，ATR = 1.0 -> 秒破線為 100.2
    # 盤中最新報價直接衝到 100.35
    df_bars = _make_sample_df(
        closes=[99.0, 98.5, 99.8],
        kc_mid=100.0, slope=-0.0001, atr=1.0,
    )
    should_exit, reason, _ = AntiChopExitGate.evaluate_exit(
        df_bars=df_bars,
        position_side="SHORT",
        entry_price=102.0,
        current_quote=100.35,  # > 100.0 + 0.2
    )
    assert should_exit is True
    assert reason == "EXIT_BY_INTRABAR_MIDLINE_BREACH"


def test_exit_hold_on_pullback_defense():
    """健康回踩不平倉護航：空單在中軌下方微幅回踩，一票否決平空，死抱主跌浪！"""
    # 空單開在 100.0，現價 98.5，中軌 99.5 依然向下 (slope < 0)
    # 收一根微幅小綠 K (open=98.3, close=98.5, body=0.2 ATR，未達 MA3 V 轉條件)
    df_pullback = _make_sample_df(
        closes=[99.0, 98.3, 98.5],
        opens=[99.2, 98.8, 98.3],
        kc_mid=99.5, slope=-0.0002, atr=1.0,
    )
    should_exit, reason, _ = AntiChopExitGate.evaluate_exit(
        df_bars=df_pullback,
        position_side="SHORT",
        entry_price=100.0,
        current_quote=98.5,
    )
    assert should_exit is False
    assert reason == "HOLD_PULLBACK_BELOW_KC_MIDDLE"
