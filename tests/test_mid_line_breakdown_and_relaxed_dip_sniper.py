import pytest
import pandas as pd
import numpy as np
from core.services.key_level_dip_sniper import (
    KeyLevelDipSniperService,
    check_comprehensive_short_entry,
    check_comprehensive_long_entry,
)
from core.gates.macro_candle_structure_gate import MacroCandleStructureGate
from core.gates.trend_riding_strategy import can_open_position
from core.gates.meme_shared_protection_gate import MemeSharedProtectionGate


def make_test_df(rows):
    df = pd.DataFrame(rows)
    df.attrs['timeframe_ms'] = 180_000.0
    return df


def test_mid_line_breakdown_entry_0838():
    """
    測試 08:38 貫穿中軌起跌入口 (MID_LINE_BREAKDOWN)：
    實體大陰棒由中軌上方灌破中軌與 13EMA，此時即使價格尚未碰到 KC 下軌，
    也第一時間判定為波段起跌點，立即發出 OPEN_SHORT。
    """
    # 模擬 08:38 前後的 K 棒結構 (3M 級別)
    # 中軌 100.0, 13EMA/MA15 99.8, 上軌 105.0, 下軌 95.0, ATR 1.0
    candle = {
        'open': 100.5,    # 由中軌上方開盤
        'high': 100.6,
        'low': 98.2,
        'close': 98.4,    # 實體大陰線灌破中軌 100.0 與 MA15 99.8，但尚未碰下軌 95.0
    }
    prev_candle = {
        'open': 101.0,
        'high': 101.5,
        'low': 100.2,
        'close': 100.4,
    }
    kc_mid = 100.0
    ma5 = 99.5
    prev_ma5 = 100.2
    ma15 = 99.8
    prev_ma15 = 100.0
    atr = 1.0

    action, pattern, reason = check_comprehensive_short_entry(
        candle, prev_candle, kc_mid, ma5, prev_ma5, ma15, prev_ma15,
        atr=atr, kc_lower=95.0, kc_upper=105.0, kc_trend="DOWN",
    )
    assert action == "OPEN_SHORT"
    assert pattern == "MID_LINE_BREAKDOWN"
    assert "灌破中軌" in reason


def test_veto_short_ma5_lifted_when_resumed_down_and_below_kc_mid():
    """
    測試 08:33 ~ 08:36 的否決日誌問題：
    上一根已收盤棒微幅走平 (例如 ma5_curr >= ma5_prev 浮點誤差 +8e-6)，
    但當 MA5 重新拐頭向下且跌破 KC 中軌時，必須立即解除 Veto 否決，恢復開空權限。
    """
    rows = [
        {
            'open': 100.0, 'high': 100.5, 'low': 99.5, 'close': 99.8,
            'ma5': 99.9, 'ma15': 100.2, 'kc_middle': 100.0, 'kc_upper': 103.0, 'kc_lower': 97.0,
            'atr': 1.0, 'is_closed': True, 'timestamp': 1000,
        },
        {
            # 上一根已收盤：微弱走平 (+0.000008)
            'open': 99.8, 'high': 100.0, 'low': 99.2, 'close': 99.4,
            'ma5': 99.900008, 'ma15': 100.1, 'kc_middle': 99.9, 'kc_upper': 102.9, 'kc_lower': 96.9,
            'atr': 1.0, 'is_closed': True, 'timestamp': 2000,
        },
        {
            # 當前 live K 棒：正在大跌灌破中軌，最新價 98.5
            'open': 99.4, 'high': 99.5, 'low': 98.4, 'close': 98.5,
            'ma5': 99.5, 'ma15': 100.0, 'kc_middle': 99.8, 'kc_upper': 102.8, 'kc_lower': 96.8,
            'atr': 1.0, 'is_closed': False, 'timestamp': 3000,
        }
    ]
    df = make_test_df(rows)

    # 驗證 can_open_position 在 MID_LINE_BREAKDOWN 或跌破中軌時放行，不觸發 VETO_SHORT: MA5_SLOPE_NOT_NEGATIVE
    allowed, reason, details = can_open_position(
        df,
        side="SHORT",
        quote=98.5,
        symbol="CAP/USDT",
        entry_confirmation="MID_LINE_BREAKDOWN",
    )
    assert allowed is True
    assert "GATEKEEPER_PERMIT" in reason


def test_relaxed_dip_sniper_continuation_near_lower_rail_0844():
    """
    測試 08:44 回踩均線延續開空：
    在明確 KC 向下的單邊跌勢中，行情沿著下軌下跌時，
    只要有小幅反彈觸及 3M 5EMA 遇阻轉陰，允許執行延續追空，不要因為價格靠近下軌就一票否決！
    """
    rows = [
        {
            'open': 98.0, 'high': 98.2, 'low': 95.0, 'close': 95.2,
            'ma5': 96.0, 'ma15': 98.5, 'kc_middle': 97.5, 'kc_upper': 100.5, 'kc_lower': 94.5,
            'atr': 1.0, 'is_closed': True, 'timestamp': 1000,
        },
        {
            # 08:44 處：反彈摸到 5EMA (95.8)，隨後收陰在 95.0 (靠近下軌 94.5，距離下軌僅 0.5 < 20% 通道寬度)
            'open': 95.2, 'high': 95.8, 'low': 94.8, 'close': 95.0,
            'ma5': 95.8, 'ma15': 98.0, 'kc_middle': 97.0, 'kc_upper': 100.0, 'kc_lower': 94.0,
            'atr': 1.0, 'is_closed': True, 'timestamp': 2000,
        }
    ]
    df = make_test_df(rows)

    # 1. 驗證 KeyLevelDipSniperService 產生 DIP_SNIPER
    candle = {'open': 95.2, 'high': 95.8, 'low': 94.8, 'close': 95.0}
    action, pattern, reason = check_comprehensive_short_entry(
        candle, rows[0], kc_mid=97.0, ma5=95.8, prev_ma5=96.0, ma15=98.0, prev_ma15=98.5,
        atr=1.0, kc_lower=94.0, kc_upper=100.0, kc_trend="DOWN",
    )
    assert action == "OPEN_SHORT"
    assert pattern == "DIP_SNIPER"

    # 2. 驗證 MemeSharedProtectionGate.validate_profit_room 豁免單邊跌勢中靠近下軌的 20% 否決
    room_ok, room_reason = MemeSharedProtectionGate.validate_profit_room(
        df,
        side="SHORT",
        quote=95.0,
        entry_confirmation="DIP_SNIPER",
        is_bearish_continuation=True,
    )
    assert room_ok is True
    assert "PASS" in room_reason


def test_macro_structure_gate_authorizes_mid_line_breakdown():
    """
    測試 MacroCandleStructureGate 對 MID_LINE_BREAKDOWN 的審查放行
    """
    gate = MacroCandleStructureGate()
    closed_bar = {
        'open': 100.5,
        'high': 100.6,
        'low': 98.2,
        'close': 98.4,
        'volume': 1500,
    }
    indicators = {
        'kc_upper': 105.0,
        'kc_lower': 95.0,
        'kc_middle': 100.0,
        'ma15': 99.8,
        'atr': 1.0,
        'ma5_current': 99.5,
        'ma5_previous': 100.2,
        'kc_middle_current': 100.0,
        'kc_middle_previous': 100.3,
    }
    allowed, reason = gate.can_open(
        side="SHORT",
        closed_bar=closed_bar,
        indicators=indicators,
        symbol="CAP/USDT",
        entry_pattern="BREAKOUT",
        entry_signal_mode="BREAKOUT",
        structure_confirmation="MID_LINE_BREAKDOWN",
        structure_confirmed=True,
        channel_expanding=True,
    )
    assert allowed is True
    assert "PASSED_MACRO_SHORT_GATE" in reason


def test_can_enter_short_near_band_strong_downtrend_vs_chop():
    """
    測試修正後的貼軌過濾器：
    1. 單邊下殺推軌時全力放行開空
    2. 橫盤震盪走平時嚴格攔截防誘空
    """
    from core.gates.rail_protection_filter import can_enter_short_near_band, can_enter_long_near_band

    # 1. 單邊強空頭格局 (KC中軌向下 slope=-0.0002, MA5 95.0 < MA15 98.0)
    # 貼著下軌 94.0 收陰在 94.1 (open 94.5, close 94.1)
    candle_strong_drop = {'open': 94.5, 'close': 94.1, 'high': 94.6, 'low': 94.0}
    kc = {'upper': 100.0, 'lower': 94.0, 'mid': 97.0}
    allowed, msg = can_enter_short_near_band(
        candle_strong_drop, kc, ma5=95.0, ma15=98.0, kc_mid_slope=-0.0002,
    )
    assert allowed is True
    assert "單邊主跌推軌" in msg

    # 2. 震盪走平 (KC中軌走平 slope=0.0000, 通道走平無斜率)
    # 貼著下軌 94.0 追空 (dist_to_lower 0.1 < candle_range * 0.2 或 kc_range * 0.2)
    candle_chop = {'open': 94.5, 'close': 94.1, 'high': 95.0, 'low': 94.0}
    allowed_chop, msg_chop = can_enter_short_near_band(
        candle_chop, kc, ma5=96.0, ma15=96.1, kc_mid_slope=0.0,
    )
    assert allowed_chop is False
    assert "震盪防誘空" in msg_chop


def test_can_enter_long_near_band_strong_uptrend_vs_chop():
    """
    測試多單對稱貼軌過濾器：
    1. 單邊主升推軌時全力放行開多
    2. 橫盤震盪走平時嚴格攔截防誘多
    """
    from core.gates.rail_protection_filter import can_enter_long_near_band

    # 1. 單邊強多頭格局 (KC中軌向上 slope=0.0002, MA5 105.0 > MA15 102.0)
    # 貼著上軌 106.0 收陽在 105.9 (open 105.5, close 105.9)
    candle_strong_rise = {'open': 105.5, 'close': 105.9, 'high': 106.0, 'low': 105.4}
    kc = {'upper': 106.0, 'lower': 100.0, 'mid': 103.0}
    allowed, msg = can_enter_long_near_band(
        candle_strong_rise, kc, ma5=105.0, ma15=102.0, kc_mid_slope=0.0002,
    )
    assert allowed is True
    assert "單邊主升推軌" in msg

    # 2. 震盪走平 (KC中軌走平 slope=0.0000)
    # 貼著上軌 106.0 追多
    candle_chop = {'open': 105.5, 'close': 105.9, 'high': 106.0, 'low': 105.0}
    allowed_chop, msg_chop = can_enter_long_near_band(
        candle_chop, kc, ma5=103.5, ma15=103.4, kc_mid_slope=0.0,
    )
    assert allowed_chop is False
    assert "震盪防誘多" in msg_chop

