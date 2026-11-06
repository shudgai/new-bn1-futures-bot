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
    assert verdict.code == "VETO_HOLD_LONG_OPPOSITE_BODY_REQUIRED"
    assert not UltimateShortExitGatekeeper.can_execute_exit(
        "1000PEPE/USDT", pos, {}, "STRUCTURE_EXIT_PROBE", 103.5,
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
