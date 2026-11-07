"""專屬驗證：
1. 綠 K 陽線 / 波段拉升中 100% 阻斷平多 (解決 1000PEPE 過敏平多)
2. 未收線 Tick 穿透 100% 阻斷平多/平空
3. 頂部大陰線下殺貫穿 KC 中軌與 MA15 時極速平多與瞬間反手 (解決龍蝦滯後平多)
4. MasterOpenGatekeeper 審查與黑名單硬鎖
"""

import time
import pytest
import pandas as pd
from core.gates.ultimate_short_exit_gatekeeper import (
    UltimateShortExitGatekeeper,
    FAST_TOP_EXIT_REASONS,
)
from core.gates.master_gate_funnel import (
    MasterOpenGatekeeper,
    MasterGateFunnel,
    MarketSnapshot,
)

# 測試用輔助常數
MIDDLE = 100.0
ATR = 2.0
UPPER = MIDDLE + 1.5 * ATR
LOWER = MIDDLE - 1.5 * ATR


def _make_frame(rows):
    df = pd.DataFrame(rows)
    df.attrs.update(timeframe_ms=60_000, snapshot_ms=time.time() * 1000)
    return df


def test_green_candle_upward_structure_vetoes_close_long():
    """驗證 1000PEPE 拉升波段中，綠 K 陽線或運行於 KC 中軌/MA15 之上時，100% 阻斷平多！"""
    now_min = int(time.time() // 60)
    # 建立 6 根已收線 K 棒，最新一根為綠 K (close > open)，運行於中軌與 MA15 之上
    rows = []
    for i in range(6):
        rows.append({
            "timestamp": (now_min - 6 + i) * 60_000,
            "open": 98.0 + i * 1.0,
            "close": 98.5 + i * 1.0,
            "high": 99.0 + i * 1.0,
            "low": 97.5 + i * 1.0,
            "ma5": 99.0 + i * 0.8,
            "ma15": 98.0 + i * 0.5,
            "kc_middle": 98.0 + i * 0.4,
            "kc_upper": 102.0 + i * 0.4,
            "kc_lower": 94.0 + i * 0.4,
            "atr": 2.0,
            "is_closed": True,
        })
    df = _make_frame(rows)
    pos = {
        "symbol": "1000PEPE/USDT",
        "side": "LONG",
        "entry_price": 98.0,
        "qty": 1000.0,
        "open_timestamp": (now_min - 10) * 60,
    }
    # 嘗試平多
    verdict = UltimateShortExitGatekeeper.evaluate(
        "1000PEPE/USDT", pos, {}, "STRUCTURE_EXIT_PROBE", 103.5,
        frame=df, is_bar_closed=True,
    )
    # 必須一票否決續抱！
    assert verdict.verdict == "HOLD"
    assert verdict.code in ("VETO_HOLD_LONG_OPPOSITE_BODY_REQUIRED", "VETO_HOLD_LONG_ABOVE_MA15", "VETO_HOLD_LONG_ON_GREEN_CANDLE")
    assert not UltimateShortExitGatekeeper.can_execute_exit(
        "1000PEPE/USDT", pos, {}, "STRUCTURE_EXIT_PROBE", 103.5,
        frame=df, is_bar_closed=True,
    )


def test_above_ma15_vetoes_close_long():
    """驗證未跌破 MA15 (close >= ma15) 時，100% 認定為回踩洗盤，絕對禁止平多！"""
    now_min = int(time.time() // 60)
    # 建立 6 根已收線 K 棒，最新一根為小紅 K 回踩但收盤穩在 MA15 之上
    rows = []
    for i in range(5):
        rows.append({
            "timestamp": (now_min - 6 + i) * 60_000,
            "open": 98.0 + i * 1.0,
            "close": 98.5 + i * 1.0,
            "high": 99.0 + i * 1.0,
            "low": 97.5 + i * 1.0,
            "ma5": 99.0 + i * 0.8,
            "ma15": 98.0 + i * 0.5,
            "kc_middle": 98.0 + i * 0.4,
            "kc_upper": 102.0 + i * 0.4,
            "kc_lower": 94.0 + i * 0.4,
            "atr": 2.0,
            "is_closed": True,
        })
    # 第 6 根：小陰線回踩，close 101.5 > ma15 100.5
    rows.append({
        "timestamp": (now_min - 1) * 60_000,
        "open": 102.0,
        "close": 101.5,
        "high": 102.5,
        "low": 101.0,
        "ma5": 102.0,
        "ma15": 100.5,
        "kc_middle": 100.0,
        "kc_upper": 103.0,
        "kc_lower": 97.0,
        "atr": 2.0,
        "is_closed": True,
    })
    df = _make_frame(rows)
    pos = {
        "symbol": "龙虾/USDT",
        "side": "LONG",
        "entry_price": 99.0,
        "qty": 1000.0,
        "open_timestamp": (now_min - 10) * 60,
    }
    verdict = UltimateShortExitGatekeeper.evaluate(
        "龙虾/USDT", pos, {}, "STRUCTURE_EXIT_PROBE", 101.5,
        frame=df, is_bar_closed=True,
    )
    assert verdict.verdict == "HOLD"
    assert verdict.code == "VETO_HOLD_LONG_ABOVE_MA15"
    assert not UltimateShortExitGatekeeper.can_execute_exit(
        "龙虾/USDT", pos, {}, "STRUCTURE_EXIT_PROBE", 101.5,
        frame=df, is_bar_closed=True,
    )


def test_unclosed_bar_tick_vetoes_exit():
    """驗證未收線 (is_bar_closed=False) 時，嚴禁因 Tick 插針過敏平多！"""
    now_min = int(time.time() // 60)
    rows = [
        {
            "timestamp": (now_min - 3) * 60_000,
            "open": 98.0,
            "close": 99.0,
            "high": 99.5,
            "low": 97.5,
            "ma5": 98.5,
            "ma15": 98.0,
            "kc_middle": 98.0,
            "kc_upper": 101.0,
            "kc_lower": 95.0,
            "atr": 2.0,
            "is_closed": True,
        },
        {
            "timestamp": (now_min - 2) * 60_000,
            "open": 99.0,
            "close": 100.0,
            "high": 100.5,
            "low": 98.5,
            "ma5": 99.5,
            "ma15": 99.0,
            "kc_middle": 99.0,
            "kc_upper": 102.0,
            "kc_lower": 96.0,
            "atr": 2.0,
            "is_closed": True,
        },
        {
            "timestamp": (now_min - 1) * 60_000,
            "open": 100.0,
            "close": 96.0,  # 哪怕盤中下插成大黑K
            "high": 101.0,
            "low": 95.0,
            "ma5": 98.0,
            "ma15": 99.0,
            "kc_middle": 100.0,
            "kc_upper": 103.0,
            "kc_lower": 97.0,
            "atr": 2.0,
            "is_closed": True,
        },
    ]
    df = _make_frame(rows)
    pos = {
        "symbol": "1000PEPE/USDT",
        "side": "LONG",
        "entry_price": 100.0,
        "qty": 1000.0,
        "open_timestamp": (now_min - 10) * 60,
    }
    # 傳入 is_bar_closed=False 代表當前 1m K 棒仍在 60 秒走動中 (Unclosed Bar)
    verdict = UltimateShortExitGatekeeper.evaluate(
        "1000PEPE/USDT", pos, {}, "STRUCTURE_EXIT_PROBE", 96.0,
        frame=df, is_bar_closed=False,
    )
    assert verdict.verdict == "HOLD"
    assert verdict.code == "VETO_EXIT_BAR_NOT_CLOSED"
    assert not UltimateShortExitGatekeeper.can_execute_exit(
        "1000PEPE/USDT", pos, {}, "STRUCTURE_EXIT_PROBE", 96.0,
        frame=df, is_bar_closed=False,
    )


def test_fast_top_exit_channel_allows_immediate_close_long():
    """驗證龍蝦在峰頂收出大陰線下殺貫穿 KC 中軌與 MA15 且 MA5 死叉 MA15 時，極速平多通道授權平倉！"""
    now_min = int(time.time() // 60)
    rows = []
    # 過去幾棒衝頂
    for i in range(5):
        rows.append({
            "timestamp": (now_min - 6 + i) * 60_000,
            "open": 102.0 + i * 0.5,
            "close": 103.0 + i * 0.5,
            "high": 104.0 + i * 0.5,
            "low": 101.5 + i * 0.5,
            "ma5": 102.5 + i * 0.5,
            "ma15": 101.0 + i * 0.5,
            "kc_middle": 100.0,
            "kc_upper": 103.0,
            "kc_lower": 97.0,
            "atr": 2.0,
            "is_closed": True,
        })
    # 當前收線棒：頂部大實體紅 K (陰線下殺，Body = 104.0 - 99.0 = 5.0 >= 0.35 * 2.0)，跌破 KC 中軌 (100.0) 與 MA15 (101.0)，MA5 下殺死叉 MA15
    rows.append({
        "timestamp": (now_min - 1) * 60_000,
        "open": 104.0,
        "close": 99.0,
        "high": 104.5,
        "low": 98.8,
        "ma5": 100.5,   # ma5 < ma15 (死叉)
        "ma15": 101.0,
        "kc_middle": 100.0,
        "kc_upper": 103.0,
        "kc_lower": 97.0,
        "atr": 2.0,
        "is_closed": True,
    })
    df = _make_frame(rows)
    pos = {
        "symbol": "龙虾/USDT",
        "side": "LONG",
        "entry_price": 95.0,
        "qty": 500.0,
        "open_timestamp": (now_min - 10) * 60,
    }
    verdict = UltimateShortExitGatekeeper.evaluate(
        "龙虾/USDT", pos, {}, "STRUCTURE_EXIT_PROBE", 99.0,
        frame=df, is_bar_closed=True,
    )
    # 頂部極速平多通道授權平倉（無論放行理由為 FAST_TOP 或 TRUE_PEAK，皆為頂部授權出場，絕非 HOLD 遲鈍等到下軌）！
    assert verdict.verdict == "ALLOW"
    assert verdict.code in (FAST_TOP_EXIT_REASONS["LONG"], "EXIT_LONG_TRUE_PEAK_TOP")
    assert UltimateShortExitGatekeeper.can_execute_exit(
        "龙虾/USDT", pos, {}, verdict.code, 99.0,
        frame=df, is_bar_closed=True,
    )


def test_top_pivot_instant_position_flip():
    """驗證頂部大實體紅 K 死叉確立時，evaluate_position_flip 瞬間授權反手開空 (FLIP TO SHORT)！"""
    snap = MarketSnapshot(
        symbol="龙虾/USDT",
        quote=99.0,
        open_price=104.0,
        close_price=99.0,
        high_price=104.5,
        low_price=98.8,
        ma3_curr=99.5,
        ma3_prev=103.0,
        ma5_curr=100.5,
        ma5_prev=102.5,
        ma15_curr=101.0,
        ma15_prev=100.8,
        kc_upper=103.0,
        kc_middle=100.0,
        kc_middle_prev=100.0,
        kc_lower=97.0,
        atr=2.0,
        macro_trend="BEARISH",
        ma_cross_count_8=0,
        is_closed=True,
    )
    flip = MasterGateFunnel.evaluate_position_flip(snap, "LONG")
    assert flip is not None
    target_side, trigger = flip
    assert target_side == "SHORT"
    assert trigger == "TRIGGER_TOP_PIVOT_CROSS"


def test_master_open_gatekeeper_blacklist_and_validation():
    """驗證 MasterOpenGatekeeper 對開倉的嚴格審查與黑名單阻斷！"""
    # 均線斜率向下 + 當根為陰線時，開多必須被阻斷
    snap_bearish = MarketSnapshot(
        symbol="1000PEPE/USDT",
        quote=99.0,
        open_price=100.0,
        close_price=99.0,
        high_price=100.5,
        low_price=98.5,
        ma3_curr=99.0,
        ma3_prev=100.0,
        ma5_curr=99.2,
        ma5_prev=100.2,
        ma15_curr=101.0,
        ma15_prev=101.2,
        kc_upper=103.0,
        kc_middle=100.0,
        kc_middle_prev=100.1,
        kc_lower=97.0,
        atr=2.0,
        macro_trend="BEARISH",
        ma_cross_count_8=0,
        is_closed=True,
    )
    decision = MasterOpenGatekeeper.validate_open_signal(snap_bearish, "LONG")
    assert not decision.can_execute
    assert decision.reason in {
        "BLOCKED_LONG_DIRECTIONAL_CANDLE",
        "BLOCKED_LONG_ON_BEARISH_TREND",
        "BLOCKED_LONG_MACRO_TREND_CONFLICT",
        "BLOCKED_LONG_MA5_SLOPE_TOO_FLAT",
        "BLOCKED_LONG_MA5_NOT_ABOVE_MA15",
        "BLOCKED_LONG_CANDLE_NOT_BULLISH",
    }

    # 驗證 MA5 走平 / 微幅向下時開多 100% 一票否決
    snap_flat_ma5 = snap_bearish._replace(
        macro_trend="BULLISH",
        kc_middle=100.2,
        kc_middle_prev=100.1,
        ma5_curr=100.0,
        ma5_prev=100.0, # 走平
        ma15_curr=99.0,
        close_price=101.0,
        open_price=100.0,
        quote=101.0,
    )
    decision_flat = MasterOpenGatekeeper.validate_open_signal(snap_flat_ma5, "LONG")
    assert not decision_flat.can_execute
    assert decision_flat.reason == "BLOCKED_LONG_MA5_SLOPE_TOO_FLAT"

    # 驗證下殺陰線 (close <= open) 時開多 100% 一票否決
    snap_red_candle = snap_bearish._replace(
        macro_trend="BULLISH",
        kc_middle=100.2,
        kc_middle_prev=100.1,
        ma5_curr=101.0,
        ma5_prev=100.0,
        ma15_curr=99.0,
        close_price=99.5,
        open_price=100.5, # 紅K / 陰線下殺
        quote=99.5,
    )
    decision_red = MasterOpenGatekeeper.validate_open_signal(snap_red_candle, "LONG")
    assert not decision_red.can_execute
    assert decision_red.reason in {"BLOCKED_LONG_DIRECTIONAL_CANDLE", "BLOCKED_LONG_CANDLE_NOT_BULLISH"}

    # 驗證 KC 向下空頭格局下，超遠離下軌地板追空 (close < kc_lower - 0.8*atr 且乖離過大) 被 FOMO 地板空否決
    snap_oversold_short = MarketSnapshot(
        symbol="1000PEPE/USDT",
        quote=95.0,
        open_price=98.5,
        close_price=95.0, # 遠離 kc_lower(97.0) 達到 2.0 (1.0 ATR)，且 MA15(99.0) - close(95.0) = 4.0 (2.0 ATR)
        high_price=98.5,
        low_price=95.0,
        ma3_curr=96.0,
        ma3_prev=98.0,
        ma5_curr=96.5,
        ma5_prev=98.5,
        ma15_curr=99.0,
        ma15_prev=99.2,
        kc_upper=103.0,
        kc_middle=100.0,
        kc_middle_prev=100.1,
        kc_lower=97.0,
        atr=2.0,
        macro_trend="BEARISH",
        ma_cross_count_8=0,
        is_closed=True,
    )
    decision_oversold = MasterOpenGatekeeper.validate_open_signal(snap_oversold_short, "SHORT")
    assert not decision_oversold.can_execute
    assert decision_oversold.reason in {"BLOCKED_SHORT_OVERSOLD_FLOOR_FOMO", "BLOCKED_SHORT_MA15_EXTENSION_1_ATR", "BLOCKED_SHORT_KC_LOWER_OVERSOLD"}

    # 驗證大趨勢 KC 向上時 100% 嚴禁開空
    snap_bullish_short = snap_oversold_short._replace(
        kc_middle=100.2,
        kc_middle_prev=100.1, # KC 向上
        macro_trend="BULLISH",
    )
    decision_bullish_short = MasterOpenGatekeeper.validate_open_signal(snap_bullish_short, "SHORT")
    assert not decision_bullish_short.can_execute
    assert decision_bullish_short.reason in {"BLOCKED_SHORT_ON_BULLISH_TREND", "BLOCKED_SHORT_MACRO_TREND_CONFLICT"}


def test_zero_delay_entry_emergency_circuit_breaker_and_position_flip():
    """驗證：
    1. 解決太晚開倉：起漲/起跌點秒開倉 + 天花板/地板 Anti-FOMO 一票否決
    2. 解決太晚平倉：毫秒級極端斷路器 (Emergency Circuit Breaker) 豁免 is_bar_closed
    3. 核心連動：真峰頂/真谷底 100% 一鍵雙向 Position Flip
    """
    # 1. 天花板追多一票否決 (close > KC_Upper + 0.8 * ATR)
    snap_ceiling_long = MarketSnapshot(
        symbol="1000PEPE/USDT",
        quote=105.0,
        open_price=101.0,
        close_price=105.0,
        high_price=105.2,
        low_price=101.0,
        ma3_curr=104.0,
        ma3_prev=102.0,
        ma5_curr=103.5,
        ma5_prev=101.5,
        ma15_curr=101.0,
        ma15_prev=100.5,
        kc_upper=103.0,
        kc_middle=100.5,
        kc_middle_prev=100.0,
        kc_lower=98.0,
        atr=2.0,
        macro_trend="BULLISH",
        ma_cross_count_8=0,
        is_closed=True,
    )
    res_ceiling = MasterOpenGatekeeper.validate_open_signal(snap_ceiling_long, "LONG")
    assert not res_ceiling.can_execute
    assert res_ceiling.reason == "BLOCKED_LONG_OVERBOUGHT_CEILING_FOMO"

    # 2. 盤中毫秒級極端斷路器 (豁免 is_bar_closed == False)
    df_live_dump = pd.DataFrame([
        {
            "timestamp": 1700000000000 + i * 60000,
            "open": 102.0,
            "high": 102.5,
            "low": 101.5,
            "close": 102.0,
            "kc_upper": 104.0,
            "kc_middle": 101.0,
            "kc_lower": 98.0,
            "ma5": 101.8,
            "ma15": 101.2,
            "atr": 1.0,
            "is_closed": True,
        }
        for i in range(6)
    ] + [
        {
            "timestamp": 1700000000000 + 6 * 60000,
            "open": 102.0,
            "high": 102.0,
            "low": 100.0,
            "close": 100.1,
            "kc_upper": 104.0,
            "kc_middle": 101.0,
            "kc_lower": 98.0,
            "ma5": 101.0,
            "ma15": 101.2,
            "atr": 1.0,
            "is_closed": False,  # 盤中未收線！
        }
    ])
    pos_long = {
        "symbol": "1000PEPE/USDT",
        "side": "LONG",
        "entry_price": 101.8,
        "open_timestamp": 1700000000000,
    }
    wf_hit, wf_reason, wf_ev = UltimateShortExitGatekeeper.check_waterfall_emergency_exit(
        "1000PEPE/USDT", pos_long, {}, 100.1, frame=df_live_dump,
    )
    assert wf_hit is True
    assert wf_reason == "EXIT_LONG_EMERGENCY_WATERFALL"
    assert UltimateShortExitGatekeeper.can_execute_exit(
        "1000PEPE/USDT", pos_long, {}, wf_reason, 100.1, frame=df_live_dump, is_bar_closed=False,
    ) is True


def test_1000pepe_0822_cost_zone_hold_until_true_valley():
    """
    回歸測試 1000PEPE/USDT 08:16 開空 (0.0049851) -> 08:22 成本區小綠 K 震盪：
    1. 08:21 收線價 (0.0049862) 距離成本價僅 +0.31 ATR (< 0.50 ATR 保護盾)，且未突破 0.50 ATR 緩衝區、無新鮮金叉、波段尚未展開至真谷底。
    2. 08:22:21 盤中價格已順向跌回 MA15 與成本價下方 (quote = 0.0049815 < ma15 = 0.00498453)，嚴禁誤觸 EXIT_SHORT_FAST_BOTTOM_BREAK_MA15_MID 提前平倉！
    3. 續抱至 08:25~08:30 跌破 KC 下軌探底 (0.0049726) 後，於 08:31 谷底實體綠 K 站上 MA5 才允許平空獲利了結！
    """
    base_ts = 1794010560  # 08:16:00
    rows = [
        {"timestamp": (base_ts + i * 60) * 1000, "open": 0.0049850, "high": 0.0049860, "low": 0.0049830, "close": 0.0049845,
         "ma5": 0.0049850, "ma15": 0.0049845, "kc_middle": 0.0049855, "kc_upper": 0.0049915, "kc_lower": 0.0049795, "atr": 3.5e-6, "is_closed": True}
        for i in range(4)
    ]
    # 08:20 bar
    rows.append({
        "timestamp": (base_ts + 4 * 60) * 1000,
        "open": 0.0049865, "high": 0.0049868, "low": 0.0049844, "close": 0.0049847,
        "ma5": 0.00498464, "ma15": 0.00498434, "kc_middle": 0.00498532, "kc_upper": 0.0049910, "kc_lower": 0.0049798, "atr": 3.5e-6, "is_closed": True,
    })
    # 08:21 bar (成本區微幅震盪小綠 K)
    rows.append({
        "timestamp": (base_ts + 5 * 60) * 1000,
        "open": 0.0049847, "high": 0.0049863, "low": 0.0049847, "close": 0.0049862,
        "ma5": 0.00498492, "ma15": 0.00498453, "kc_middle": 0.00498540, "kc_upper": 0.0049916, "kc_lower": 0.0049800, "atr": 3.5e-6, "is_closed": True,
    })
    df_0822 = _make_frame(rows)
    pos_0822 = {
        "symbol": "1000PEPE/USDT",
        "side": "SHORT",
        "entry_price": 0.0049851,
        "qty": 69921.0,
        "open_timestamp": base_ts + 13.9,
        "trough_price": 0.0049808,
        "lowest_price": 0.0049808,
    }
    # 無論在 08:22:01 (quote=0.0049862) 或 08:22:21 (quote=0.0049815 順向下跌中)，皆必須 100% HOLD 續抱！
    d_at_close = UltimateShortExitGatekeeper.evaluate(
        "1000PEPE/USDT", pos_0822, {}, "STRUCTURE_EXIT_PROBE", 0.0049862,
        frame=df_0822, is_bar_closed=True, now=base_ts + 6 * 60 + 1,
    )
    assert d_at_close.verdict == "HOLD"

    d_at_082221 = UltimateShortExitGatekeeper.evaluate(
        "1000PEPE/USDT", pos_0822, {}, "STRUCTURE_EXIT_PROBE", 0.0049815,
        frame=df_0822, is_bar_closed=True, now=base_ts + 6 * 60 + 21,
    )
    assert d_at_082221.verdict == "HOLD"


def test_lobster_0715_0730_0809_first_millisecond_open_long():
    """
    回歸測試 龙虾/USDT 三大起漲關鍵 K 棒於第 1 時間授權開多 (OPEN_LONG)：
    1. 07:15（07:14 谷底後第 1 根起漲綠 K，0.91 ATR 從中軌下方起漲突破中軌，MA5 金叉 MA15）
    2. 07:30（剛金叉/觸碰 MA15 當根的 3.21 ATR 突破大陽線）
    3. 08:09（剛金叉當根的 0.64 ATR 破上軌大陽線）
    """
    # 1. 07:15 谷底後第 1 根起漲綠 K
    snap_0715 = MarketSnapshot(
        symbol="龙虾/USDT",
        quote=0.0172900,
        open_price=0.0172600,
        close_price=0.0172900,
        high_price=0.0173100,
        low_price=0.0172600,
        ma3_curr=0.0172700,
        ma3_prev=0.0172550,
        ma5_curr=0.0172620,
        ma5_prev=0.0172500,
        ma15_curr=0.0172527,
        ma15_prev=0.0172520,
        kc_upper=0.0173094,
        kc_middle=0.0172601,
        kc_middle_prev=0.0172569,
        kc_lower=0.0172108,
        atr=0.0000329,
        macro_trend="BULLISH",
        ma_cross_count_8=0,
        is_closed=True,
    )
    dec_0715 = MasterOpenGatekeeper.validate_open_signal(snap_0715, "LONG")
    assert dec_0715.can_execute is True
    assert dec_0715.action == "OPEN_LONG"

    # 2. 07:30 剛金叉/觸碰 MA15 當根的 3.21 ATR 大陽線
    snap_0730 = MarketSnapshot(
        symbol="龙虾/USDT",
        quote=0.0174200,
        open_price=0.0173100,
        close_price=0.0174200,
        high_price=0.0174400,
        low_price=0.0173100,
        ma3_curr=0.0173500,
        ma3_prev=0.0173000,
        ma5_curr=0.0173240,
        ma5_prev=0.0173000,
        ma15_curr=0.0173240,
        ma15_prev=0.0173153,
        kc_upper=0.0173731,
        kc_middle=0.0173109,
        kc_middle_prev=0.0172994,
        kc_lower=0.0172488,
        atr=0.0000343,
        macro_trend="BULLISH",
        ma_cross_count_8=0,
        is_closed=True,
    )
    dec_0730 = MasterOpenGatekeeper.validate_open_signal(snap_0730, "LONG")
    assert dec_0730.can_execute is True
    assert dec_0730.action == "OPEN_LONG"

    # 3. 08:09 剛金叉當根的 0.64 ATR 大陽線
    snap_0809 = MarketSnapshot(
        symbol="龙虾/USDT",
        quote=0.0175700,
        open_price=0.0175500,
        close_price=0.0175700,
        high_price=0.0175800,
        low_price=0.0175500,
        ma3_curr=0.0175533,
        ma3_prev=0.0175200,
        ma5_curr=0.0175220,
        ma5_prev=0.0175100,
        ma15_curr=0.0175160,
        ma15_prev=0.0175100,
        kc_upper=0.0175546,
        kc_middle=0.0175043,
        kc_middle_prev=0.0174974,
        kc_lower=0.0174539,
        atr=0.0000314,
        macro_trend="BULLISH",
        ma_cross_count_8=0,
        is_closed=True,
    )
    dec_0809 = MasterOpenGatekeeper.validate_open_signal(snap_0809, "LONG")
    assert dec_0809.can_execute is True
    assert dec_0809.action == "OPEN_LONG"


def test_multi_bar_structure_hl_lh_and_two_point_true_pivot_flip():
    """
    驗證：
    1. MasterOpenGatekeeper 多空雙向「拉遠看大結構」開倉硬鎖：
       - 做多 (OPEN_LONG)：必須 KC 向上 + 底底高 (Current_Swing_Low > Prev_Swing_Low) + 收盤價 > MA5 + MA5 拐頭向上；破位 (LL) 100% 阻斷！
       - 做空 (OPEN_SHORT)：必須 KC 向下 + 頂頂低 (Current_Swing_High < Prev_Swing_High) + 收盤價 < MA5 + MA5 拐頭向下；破位 (HH) 或處於 KC 下軌超賣區 (close <= KC_Lower + 0.3 * ATR) 100% 阻斷！
    2. UltimateExitGatekeeper 精簡版真峰谷 2 點秒平＋秒反手（取消實體大小限制）：
       - 真谷底 2 點 (close > ma5 且 ma5 > ma5_prev) -> CLOSE_SHORT + OPEN_LONG
       - 真峰頂 2 點 (close < ma5 且 ma5 < ma5_prev) -> CLOSE_LONG + OPEN_SHORT
    """
    from core.gates.ultimate_short_exit_gatekeeper import UltimateExitGatekeeper

    # 1. 多頭底底高 (HL: current_swing_low > prev_swing_low) 通過，破底 (LL: current_swing_low <= prev_swing_low) 一票否決
    snap_long_hl = MarketSnapshot(
        symbol="1000PEPE/USDT",
        quote=0.005030,
        open_price=0.005020,
        close_price=0.005030,
        high_price=0.005032,
        low_price=0.005018,
        ma3_curr=0.005026,
        ma3_prev=0.005020,
        ma5_curr=0.005024,
        ma5_prev=0.005019,
        ma15_curr=0.005018,
        ma15_prev=0.005015,
        kc_upper=0.005045,
        kc_middle=0.005020,
        kc_middle_prev=0.005017,
        kc_lower=0.004995,
        atr=0.000012,
        macro_trend="BULLISH",
        ma_cross_count_8=0,
        is_closed=True,
        current_swing_low=0.005012,
        prev_swing_low=0.005002,
    )
    dec_hl = MasterOpenGatekeeper.validate_open_signal(snap_long_hl, "LONG")
    assert dec_hl.can_execute is True
    assert dec_hl.action == "OPEN_LONG"

    snap_long_ll = snap_long_hl._replace(current_swing_low=0.004998, prev_swing_low=0.005002)
    dec_ll = MasterOpenGatekeeper.validate_open_signal(snap_long_ll, "LONG")
    assert dec_ll.can_execute is False
    assert dec_ll.reason == "BLOCKED_LONG_MULTI_BAR_STRUCTURE_NOT_HL"

    # 2. 空頭頂頂低 (LH: current_swing_high < prev_swing_high) 通過，破頂 (HH) 或下軌超賣區 (close <= KC_Lower + 0.3 * ATR) 一票否決
    snap_short_lh = MarketSnapshot(
        symbol="1000PEPE/USDT",
        quote=0.005010,
        open_price=0.005018,
        close_price=0.005010,
        high_price=0.005019,
        low_price=0.005008,
        ma3_curr=0.005012,
        ma3_prev=0.005018,
        ma5_curr=0.005014,
        ma5_prev=0.005019,
        ma15_curr=0.005018,
        ma15_prev=0.005021,
        kc_upper=0.005042,
        kc_middle=0.005017,
        kc_middle_prev=0.005020,
        kc_lower=0.004992,
        atr=0.000012,
        macro_trend="BEARISH",
        ma_cross_count_8=0,
        is_closed=True,
        current_swing_high=0.005025,
        prev_swing_high=0.005038,
    )
    dec_lh = MasterOpenGatekeeper.validate_open_signal(snap_short_lh, "SHORT")
    assert dec_lh.can_execute is True
    assert dec_lh.action == "OPEN_SHORT"

    snap_short_hh = snap_short_lh._replace(current_swing_high=0.005040, prev_swing_high=0.005038)
    dec_hh = MasterOpenGatekeeper.validate_open_signal(snap_short_hh, "SHORT")
    assert dec_hh.can_execute is False
    assert dec_hh.reason == "BLOCKED_SHORT_MULTI_BAR_STRUCTURE_NOT_LH"

    # 下軌超賣區 (close <= kc_lower + 0.3 * atr = 0.004992 + 0.0000036 = 0.0049956) 嚴禁追空
    snap_short_oversold_zone = snap_short_lh._replace(
        open_price=0.005000,
        close_price=0.004995,
        quote=0.004995,
        low_price=0.004994,
    )
    dec_oversold_zone = MasterOpenGatekeeper.validate_open_signal(snap_short_oversold_zone, "SHORT")
    assert dec_oversold_zone.can_execute is False
    assert dec_oversold_zone.reason == "BLOCKED_SHORT_OVERSOLD_FLOOR_FOMO"

    # 3. 精簡版真峰谷 2 點秒平＋秒反手（取消實體大小限制）
    ok_bot, close_act_bot, flip_act_bot = UltimateExitGatekeeper.evaluate_two_point_pivot_flip(
        "SHORT", close_price=0.004985, ma5_curr=0.004982, ma5_prev=0.004980, is_bar_closed=True,
    )
    assert ok_bot is True
    assert close_act_bot == "CLOSE_SHORT"
    assert flip_act_bot == "OPEN_LONG"

    ok_top, close_act_top, flip_act_top = UltimateExitGatekeeper.evaluate_two_point_pivot_flip(
        "LONG", close_price=0.005030, ma5_curr=0.005033, ma5_prev=0.005035, is_bar_closed=True,
    )
    assert ok_top is True
    assert close_act_top == "CLOSE_LONG"
    assert flip_act_top == "OPEN_SHORT"


def test_breakout_verification_gate_true_breakout_vs_oversold_trap():
    """
    驗證 MasterOpenGatekeeper「破軌真偽識別模組 (Breakout Verification Gate)」：
    1. 做多真多頭爆發起點 (True Bullish Breakout)：
       - 通道擴張 (KC_Upper - KC_Lower > 1.2 * KC_Width_MA5) + MA5 斜率 >= 0.15 * ATR + 底底高 HL -> 允許 OPEN_LONG
       - 若通道未張口或 MA5 斜率不足 -> 100% 阻斷開多！
    2. 做空超賣末端陷阱阻斷 (Anti-Oversold Trap Gate)：
       - 長下影線 ((min(open, close) - low) >= 0.4 * ATR) -> 100% 阻斷 OPEN_SHORT
       - 標頭 KC 向上 -> 100% 阻斷 OPEN_SHORT
       - 跌破下軌但通道未張口或 MA5 斜率走平 -> 100% 阻斷 OPEN_SHORT
    """
    # 1. 真多頭破軌 (通道寬度 0.000060 > 1.2 * 0.000045 = 0.000054，MA5 斜率 0.000003 >= 0.15 * 0.000012 = 0.0000018，且底底高 HL)
    snap_true_breakout = MarketSnapshot(
        symbol="1000PEPE/USDT",
        quote=0.005052,
        open_price=0.005018,
        close_price=0.005052,
        high_price=0.005054,
        low_price=0.005017,
        ma3_curr=0.005040,
        ma3_prev=0.005022,
        ma5_curr=0.005032,
        ma5_prev=0.005020,
        ma15_curr=0.005022,
        ma15_prev=0.005018,
        kc_upper=0.005050,
        kc_middle=0.005020,
        kc_middle_prev=0.005016,
        kc_lower=0.004990,
        atr=0.000012,
        macro_trend="BULLISH",
        ma_cross_count_8=0,
        is_closed=True,
        current_swing_low=0.005012,
        prev_swing_low=0.005000,
        kc_width_ma5=0.000045,
    )
    dec_true_bo = MasterOpenGatekeeper.validate_open_signal(snap_true_breakout, "LONG")
    assert dec_true_bo.can_execute is True
    assert dec_true_bo.action == "OPEN_LONG"

    # 通道未張口 (kc_width_curr = 0.000060 <= 1.2 * 0.000055 = 0.000066) -> 阻斷開多
    snap_narrow_bo = snap_true_breakout._replace(kc_width_ma5=0.000055)
    dec_narrow_bo = MasterOpenGatekeeper.validate_open_signal(snap_narrow_bo, "LONG")
    assert dec_narrow_bo.can_execute is False
    assert dec_narrow_bo.reason == "BLOCKED_LONG_BREAKOUT_CHANNEL_NOT_EXPANDING"

    # MA5 斜率不足 (< 0.15 * ATR = 0.0000018) -> 阻斷開多
    snap_weak_ma5_bo = snap_true_breakout._replace(ma5_curr=0.005023, ma5_prev=0.005022)
    dec_weak_ma5_bo = MasterOpenGatekeeper.validate_open_signal(snap_weak_ma5_bo, "LONG")
    assert dec_weak_ma5_bo.can_execute is False
    assert dec_weak_ma5_bo.reason == "BLOCKED_LONG_BREAKOUT_MA5_SLOPE_WEAK"

    # 2. 做空：長下影線 ((min(open, close) - low) >= 0.4 * ATR) -> 100% 阻斷 OPEN_SHORT
    snap_short_wick_trap = MarketSnapshot(
        symbol="1000PEPE/USDT",
        quote=0.005005,
        open_price=0.005018,
        close_price=0.005005,
        high_price=0.005019,
        low_price=0.004998,  # lower_wick = 0.005005 - 0.004998 = 0.000007 >= 0.4 * 0.000012 (0.0000048)
        ma3_curr=0.005008,
        ma3_prev=0.005016,
        ma5_curr=0.005010,
        ma5_prev=0.005016,
        ma15_curr=0.005015,
        ma15_prev=0.005018,
        kc_upper=0.005045,
        kc_middle=0.005015,
        kc_middle_prev=0.005018,
        kc_lower=0.004985,
        atr=0.000012,
        macro_trend="BEARISH",
        ma_cross_count_8=0,
        is_closed=True,
        current_swing_high=0.005022,
        prev_swing_high=0.005035,
    )
    dec_wick_trap = MasterOpenGatekeeper.validate_open_signal(snap_short_wick_trap, "SHORT")
    assert dec_wick_trap.can_execute is False
    assert dec_wick_trap.reason == "BLOCKED_SHORT_LONG_LOWER_WICK_V_TRAP"

    # 做空：跌破下軌但通道未張口 -> 100% 阻斷 OPEN_SHORT
    snap_short_flat_channel = snap_short_wick_trap._replace(
        open_price=0.005016,
        close_price=0.004982,
        quote=0.004982,
        low_price=0.004981,  # 無長下影線
        kc_width_ma5=0.000055,  # kc_width_curr = 0.000060 <= 1.2 * 0.000055
    )
    dec_flat_ch = MasterOpenGatekeeper.validate_open_signal(snap_short_flat_channel, "SHORT")
    assert dec_flat_ch.can_execute is False
    assert dec_flat_ch.reason == "BLOCKED_SHORT_BREAKDOWN_CHANNEL_FLAT_OR_NARROW"


def test_1000pepe_1048_hold_above_ma15_and_1100_block_duplicate_long():
    """
    回歸測試 1000PEPE/USDT 10:40~11:20 實盤走勢：
    1. 10:40~10:44 底部開多 (0.0049976) 後，10:48~10:50 回踩至 0.0050025：
       1m 收盤價仍位於 MA15 上方 (close = 0.0050025 >= ma15 = 0.0049980)，且 MA5 未死叉 MA15 (ma5 = 0.0050055 >= ma15)，
       UltimateShortExitGatekeeper 必須 100% 傳回 HOLD (VETO_HOLD_LONG_ABOVE_MA15)，嚴禁誤觸 EXIT_LONG_TRUE_PEAK_TOP 平多賣飛！
    2. 11:00 半山腰 (0.0050165) 當系統已持有 OPEN_LONG 多單時：
       MasterOpenGatekeeper 必須 100% 傳回 False (BLOCKED_DUPLICATE_LONG_POSITION_ALREADY_HELD)，阻斷二次追高買多！
    """
    base_ts = 1794019440  # 10:44:00
    rows = [
        {
            "timestamp": (base_ts + i * 60) * 1000,
            "open": 0.0049970 + i * 0.0000030,
            "high": 0.0050010 + i * 0.0000030,
            "low": 0.0049960 + i * 0.0000030,
            "close": 0.0050000 + i * 0.0000030,
            "ma5": 0.0049980 + i * 0.0000025,
            "ma15": 0.0049940 + i * 0.0000010,
            "kc_middle": 0.0049950 + i * 0.0000010,
            "kc_upper": 0.0050080 + i * 0.0000010,
            "kc_lower": 0.0049820 + i * 0.0000010,
            "atr": 0.0000065,
            "is_closed": True,
        }
        for i in range(4)
    ]
    # 10:48 bar (衝高觸及上軌 0.0050130)
    rows.append({
        "timestamp": (base_ts + 4 * 60) * 1000,
        "open": 0.0050120,
        "high": 0.0050135,
        "low": 0.0050070,
        "close": 0.0050085,
        "ma5": 0.0050065,
        "ma15": 0.0049975,
        "kc_middle": 0.0049990,
        "kc_upper": 0.0050120,
        "kc_lower": 0.0049860,
        "atr": 0.0000065,
        "is_closed": True,
    })
    # 10:49 bar (回踩至 0.0050025，但收盤仍穩站 MA15=0.0049985 之上，且 MA5=0.0050055 > MA15 未死叉)
    rows.append({
        "timestamp": (base_ts + 5 * 60) * 1000,
        "open": 0.0050085,
        "high": 0.0050090,
        "low": 0.0050015,
        "close": 0.0050025,
        "ma5": 0.0050055,
        "ma15": 0.0049985,
        "kc_middle": 0.0049995,
        "kc_upper": 0.0050125,
        "kc_lower": 0.0049865,
        "atr": 0.0000065,
        "is_closed": True,
    })
    df_1049 = _make_frame(rows)
    pos_1044 = {
        "symbol": "1000PEPE/USDT",
        "side": "LONG",
        "entry_price": 0.0049976,
        "qty": 70000.0,
        "open_timestamp": base_ts + 10.0,
        "highest_price": 0.0050135,
        "peak_price": 0.0050135,
    }
    verdict_1049 = UltimateShortExitGatekeeper.evaluate(
        "1000PEPE/USDT", pos_1044, {}, "STRUCTURE_EXIT_PROBE", 0.0050025,
        frame=df_1049, is_bar_closed=True, now=base_ts + 6 * 60 + 1,
    )
    assert verdict_1049.verdict == "HOLD"
    assert verdict_1049.code == "VETO_HOLD_LONG_ABOVE_MA15"

    # 2. 11:00 半山腰 (0.0050165) 已持有 OPEN_LONG 時，100% 阻斷二次開多
    snap_1100 = MarketSnapshot(
        symbol="1000PEPE/USDT",
        quote=0.0050165,
        open_price=0.0050090,
        close_price=0.0050165,
        high_price=0.0050180,
        low_price=0.0050085,
        ma3_curr=0.0050130,
        ma3_prev=0.0050080,
        ma5_curr=0.0050110,
        ma5_prev=0.0050060,
        ma15_curr=0.0050030,
        ma15_prev=0.0050010,
        kc_upper=0.0050180,
        kc_middle=0.0050040,
        kc_middle_prev=0.0050020,
        kc_lower=0.0049900,
        atr=0.0000070,
        macro_trend="BULLISH",
        ma_cross_count_8=0,
        is_closed=True,
        has_position=True,
        position_side="LONG",
        position_size=70000.0,
    )
    dec_1100 = MasterOpenGatekeeper.validate_open_signal(snap_1100, "LONG")
    assert dec_1100.can_execute is False
    assert dec_1100.reason == "BLOCKED_DUPLICATE_LONG_POSITION_ALREADY_HELD"


def test_pivot_turn_and_ma_cross_override_exempts_header_and_oversold_and_flips():
    """
    驗證「真峰谷轉彎與 MA5/MA15 交叉最高優先級開倉/反手通道」：
    1. 谷底轉彎 / 金叉成立 (is_bar_closed == True, close > ma5, ma5 > ma5_prev)：
       - 100% 豁免『KC 向下』滯後標頭與走平限制，空倉時瞬間授權 OPEN_LONG，持空單時瞬間 CLOSE_SHORT + OPEN_LONG！
    2. 頂部轉彎 / 死叉成立 (is_bar_closed == True, close < ma5, ma5 < ma5_prev)：
       - 100% 豁免『KC 向上』滯後標頭與 KC 下軌超賣區限制 (如 0.004980 死叉 / 11:15 頂部死叉)，
         空倉時瞬間授權 OPEN_SHORT，持多單時瞬間 CLOSE_LONG + OPEN_SHORT！
    """
    from core.gates.ultimate_short_exit_gatekeeper import UltimateExitGatekeeper

    # 1. 谷底 V 轉起漲陽線：頂部標頭仍卡在 KC 向下 (kc_middle < kc_middle_prev, macro_trend="BEARISH")
    snap_valley_turn = MarketSnapshot(
        symbol="1000PEPE/USDT",
        quote=0.004978,
        open_price=0.004971,
        close_price=0.004978,
        high_price=0.004979,
        low_price=0.004970,
        ma3_curr=0.004976,
        ma3_prev=0.004972,
        ma5_curr=0.004975,
        ma5_prev=0.004973,
        ma15_curr=0.004980,
        ma15_prev=0.004982,
        kc_upper=0.004992,
        kc_middle=0.004981,
        kc_middle_prev=0.004983,  # 標頭仍為 KC 向下
        kc_lower=0.004970,
        atr=0.000006,
        macro_trend="BEARISH",
        ma_cross_count_8=0,
        is_closed=True,
    )
    # 驗證 evaluate_pivot_or_cross_open 專用通道：
    # 空倉時：100% 豁免標頭限制，毫秒級授權 OPEN_LONG
    dec_valley_open = MasterOpenGatekeeper.evaluate_pivot_or_cross_open(snap_valley_turn, "LONG")
    assert dec_valley_open.can_execute is True
    assert dec_valley_open.action == "OPEN_LONG"
    assert dec_valley_open.details.get("header_exempt") is True

    # 持空單時：瞬間授權 CLOSE_SHORT + OPEN_LONG 反手
    dec_valley_flip = MasterOpenGatekeeper.evaluate_pivot_or_cross_open(
        snap_valley_turn, "LONG", has_position=True, position_side="SHORT", position_size=70000.0
    )
    assert dec_valley_flip.can_execute is True
    assert dec_valley_flip.action == "OPEN_LONG"
    assert dec_valley_flip.details.get("close_action") == "CLOSE_SHORT"
    assert dec_valley_flip.details.get("is_flip") is True

    # 2. 死叉成立時價格已掉入 KC 下軌超賣區 (0.004980 附近死叉，close <= kc_lower + 0.3 * atr)：
    #    100% 豁免超賣限制與標頭限制，毫秒級授權 OPEN_SHORT
    snap_death_cross_oversold = MarketSnapshot(
        symbol="1000PEPE/USDT",
        quote=0.004979,
        open_price=0.004986,
        close_price=0.004979,
        high_price=0.004987,
        low_price=0.004978,
        ma3_curr=0.004981,
        ma3_prev=0.004985,
        ma5_curr=0.004982,
        ma5_prev=0.004985,
        ma15_curr=0.004984,
        ma15_prev=0.004984,  # ma5_prev(0.004985) >= ma15_prev(0.004984) 且 ma5_curr(0.004982) < ma15_curr(0.004984) 死叉成立！
        kc_upper=0.004998,
        kc_middle=0.004988,
        kc_middle_prev=0.004988,  # 甚至 KC 走平或向上
        kc_lower=0.004978,        # close(0.004979) <= kc_lower + 0.3*atr (0.0049798)
        atr=0.000006,
        macro_trend="BULLISH",
        ma_cross_count_8=0,
        is_closed=True,
    )
    dec_dc_short = MasterOpenGatekeeper.evaluate_pivot_or_cross_open(snap_death_cross_oversold, "SHORT")
    assert dec_dc_short.can_execute is True
    assert dec_dc_short.action == "OPEN_SHORT"
    assert dec_dc_short.details.get("oversold_exempt") is True

    # 11:15 持多單時頂部死叉：瞬間 CLOSE_LONG + OPEN_SHORT 秒反手
    dec_top_flip = MasterOpenGatekeeper.evaluate_pivot_or_cross_open(
        snap_death_cross_oversold, "SHORT", has_position=True, position_side="LONG", position_size=70000.0
    )
    assert dec_top_flip.can_execute is True
    assert dec_top_flip.action == "OPEN_SHORT"
    assert dec_top_flip.details.get("close_action") == "CLOSE_LONG"
    assert dec_top_flip.details.get("is_flip") is True

    # UltimateExitGatekeeper.evaluate_two_point_pivot_flip 同時支援持倉反手與空倉秒開
    ok_flat_long, act1, act2 = UltimateExitGatekeeper.evaluate_two_point_pivot_flip(
        "FLAT", close_price=0.004978, ma5_curr=0.004975, ma5_prev=0.004973, is_bar_closed=True
    )
    assert (ok_flat_long, act1, act2) == (True, "OPEN_LONG", "OPEN_LONG")

    ok_flat_short, act3, act4 = UltimateExitGatekeeper.evaluate_two_point_pivot_flip(
        "FLAT", close_price=0.004979, ma5_curr=0.004982, ma5_prev=0.004985, is_bar_closed=True
    )
    assert (ok_flat_short, act3, act4) == (True, "OPEN_SHORT", "OPEN_SHORT")









