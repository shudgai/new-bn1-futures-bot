"""
單元測試：空單防洗盤硬守衛、通道波段鎖利模式、趨勢方向單向閥與破下軌順勢開空
"""

import pandas as pd
import pytest

from core.services.exits.anti_fakeout_exit_guard import (
    should_veto_short_exit,
    is_valid_short_climax_exit,
    is_valid_short_structural_reversal_exit,
)
from core.services.key_level_dip_sniper import (
    check_comprehensive_short_entry,
    check_comprehensive_long_entry,
)
from core.services.entry_firewall import market_structure_gate_problem


def test_short_exit_veto_when_13ema_falling():
    """
    【波段抱單模式測試】
    價格反抽至 KC 中軌/上軌，但 3M 13EMA 依然向下傾斜：
    空單防洗盤硬守衛必須 100% 行使 VETO 否決平空，強制維持空單持倉！
    """
    candle = {
        "close": 98.0,  # 反抽到中軌附近
        "open": 96.0,
        "high": 98.5,
        "low": 95.8,
        "volume": 100.0,
        "vol_ma": 100.0,
        "atr": 1.0,
        "kc_mid_slope": -0.001,
    }
    kc = {
        "mid": 98.2,
        "upper": 100.2,
        "lower": 96.2,
    }
    ema13 = 98.5  # 13EMA 在 98.5，且向下傾斜
    ema5 = 97.5
    position = {"side": "SHORT", "entry_price": 100.0}

    # 模擬試圖以微幅反彈、綠棒或中軌收復平倉
    veto_needed, veto_reason = should_veto_short_exit(
        candle=candle,
        kc=kc,
        ema13=ema13,
        ema5=ema5,
        position=position,
        exit_reason="EXIT_BY_SHORT_CONFIRMED_KC_MIDLINE_REVERSAL",
        quote=98.0,
    )
    assert veto_needed is True
    assert "VETO_SHORT_EXIT" in veto_reason
    assert "仍受制於13EMA" in veto_reason


def test_channel_climax_profit_lock_mode_valid():
    """
    【通道波段鎖利模式測試 - 合法放行】
    下軌穿透 + 2.0 倍爆量 + 長下影線針尖 + 實質利潤：
    允許通道波段鎖利模式平空落袋！
    """
    candle = {
        "close": 91.5,
        "open": 92.5,
        "high": 92.5,
        "low": 89.5,  # 跌破下軌 92.0
        "volume": 250.0,  # 2.5 倍均量
        "vol_ma": 100.0,
        "atr": 1.0,
    }
    kc = {
        "mid": 95.0,
        "upper": 98.0,
        "lower": 92.0,
    }
    position = {"side": "SHORT", "entry_price": 98.0}  # 浮盈達 6.5 ATR

    is_climax, msg = is_valid_short_climax_exit(
        candle=candle,
        kc=kc,
        quote=91.5,
        position=position,
    )
    assert is_climax is True
    assert "通道波段鎖利" in msg


def test_channel_climax_profit_lock_rejected_in_mid_channel():
    """
    【通道波段鎖利模式測試 - 半山腰誤觸發攔截】
    未穿透 KC 下軌 (在半山腰/中軌下方震盪)：
    嚴禁觸發見底回補！必須 100% 拒絕！
    """
    candle = {
        "close": 94.0,  # 在 94.0，未碰下軌 92.0
        "open": 94.2,
        "high": 94.5,
        "low": 93.8,  # low 93.8 > lower 92.0
        "volume": 300.0,
        "vol_ma": 100.0,
        "atr": 1.0,
    }
    kc = {
        "mid": 95.0,
        "upper": 98.0,
        "lower": 92.0,
    }
    position = {"side": "SHORT", "entry_price": 98.0}

    is_climax, msg = is_valid_short_climax_exit(
        candle=candle,
        kc=kc,
        quote=94.0,
        position=position,
    )
    assert is_climax is False
    assert "未穿透或觸及 KC 下軌" in msg


def test_structural_reversal_exit_requires_buffer():
    """
    【唯一合法結構平倉條件測試】
    3M 收盤實體必須實質衝破 13EMA + 0.15% 緩衝帶才准離場
    """
    ema13 = 100.0
    buffer_ratio = 0.0015
    threshold = 100.0 * 1.0015  # 100.15

    # 微幅觸碰 100.10，未達 100.15
    assert is_valid_short_structural_reversal_exit(100.10, ema13, buffer_ratio) is False

    # 實質衝破 100.25
    assert is_valid_short_structural_reversal_exit(100.25, ema13, buffer_ratio) is True


def test_breakdown_short_and_continuation_entries():
    """
    【雙重開空保障測試】
    1. 9:49 延續入口：均線向下且在中軌下方收陰，即刻延續開空 (CONTINUATION)
    2. 9:53/9:54 破軌入口：放量摜穿 KC 下軌，立即順勢追空 (BREAKOUT)
    """
    prev_candle = {"close": 94.0, "open": 94.5}

    # 1. 9:49 延續入口
    candle_continuation = {
        "close": 93.5, "open": 94.0, "high": 94.2, "low": 93.2,
    }
    action, pattern, reason = check_comprehensive_short_entry(
        candle=candle_continuation,
        prev_candle=prev_candle,
        kc_mid=95.0,
        ma5=93.8,
        prev_ma5=94.2,
        ma15=94.5,
        prev_ma15=94.8,
        atr=1.0,
        kc_lower=91.0,
        kc_upper=99.0,
        kc_trend="DOWN",
    )
    assert action == "OPEN_SHORT"
    assert pattern in ("CONTINUATION", "DIP_SNIPER")

    # 2. 9:53/9:54 破軌入口 (大陰線摜穿 KC 下軌 91.0)
    candle_breakdown = {
        "close": 90.5, "open": 92.0, "high": 92.2, "low": 90.0,
    }
    action_bo, pattern_bo, reason_bo = check_comprehensive_short_entry(
        candle=candle_breakdown,
        prev_candle=candle_continuation,
        kc_mid=95.0,
        ma5=92.0,
        prev_ma5=93.8,
        ma15=94.0,
        prev_ma15=94.5,
        atr=1.0,
        kc_lower=91.0,
        kc_upper=99.0,
        kc_trend="DOWN",
    )
    assert action_bo == "OPEN_SHORT"
    assert pattern_bo == "BREAKOUT"
    assert "破軌爆發" in reason_bo


def test_market_structure_gate_allows_breakout_short_without_lagging_ma15_veto():
    """
    【下單防火牆破軌加速測試】
    在價格跌破下軌且在 MA5 下方時，即使 MA15 過去線性擬合微幅向上，
    嚴禁觸發 VETO_BLOCKED_SHORT_ON_RISING_MA15 阻斷！
    """
    records = []
    for i in range(15):
        records.append({
            "close": 100.0 - i * 0.2, "open": 100.2 - i * 0.2,
            "high": 100.5 - i * 0.2, "low": 99.8 - i * 0.2,
            "ma5": 99.0 - i * 0.2, "ma15": 99.5 - i * 0.05,
            "kc_upper": 102.0, "kc_middle": 98.0, "kc_lower": 94.0,
            "atr": 1.0, "is_closed": True,
        })
    df = pd.DataFrame(records)

    # 報價 93.5，跌破 KC 下軌 94.0 且在 MA5 之下
    problem = market_structure_gate_problem(
        df,
        side="SHORT",
        quote=93.5,
        enforce_ma_direction=False,  # 破軌模式
        is_confirmed_outer_entry=True,
    )
    assert problem is None
