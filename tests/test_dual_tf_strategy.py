"""單元測試：5M（操作級別）+ 15M（大趨勢級別）跨週期共振架構
測試覆蓋：
1. 15M 大趨勢反向一票否決
2. 5M MA5 走平/彎折否決開倉
3. TrendHoldingImmunity 攔截常規平倉 (未破 5M MA15 100% 免疫)
4. Hard Breakeven 保本移損觸發
"""
import pytest
import pandas as pd
from core.indicators.dual_timeframe_provider import DualTimeframeProvider
from core.gates.meme_shared_protection_gate import MemeSharedProtectionGate
from core.gates.trend_holding_immunity_and_exit_gate import TrendHoldingAndExitManager


def make_candle_df(candles):
    df = pd.DataFrame(candles)
    df.attrs['timeframe_ms'] = 300000 # 5m
    return df


def test_15m_big_trend_veto_blocks_counter_entry():
    """1. 覆蓋 15M 大趨勢反向否決：15M 為 DOWN 時開多 100% 被否決；15M 為 UP 時開空 100% 被否決"""
    df_15m_down = make_candle_df([
        {'open': 100.0, 'close': 95.0, 'high': 100.0, 'low': 94.0, 'ma5': 96.0, 'ma15': 99.0, 'kc_middle': 98.0, 'kc_upper': 102.0, 'kc_lower': 94.0, 'atr': 2.0, 'is_closed': True},
        {'open': 95.0, 'close': 92.0, 'high': 95.5, 'low': 91.0, 'ma5': 93.5, 'ma15': 97.0, 'kc_middle': 96.0, 'kc_upper': 100.0, 'kc_lower': 92.0, 'atr': 2.0, 'is_closed': True, 'channel_state': 'DOWN'},
    ])

    df_5m_bull = make_candle_df([
        {'open': 90.0, 'close': 93.0, 'high': 93.5, 'low': 89.8, 'ma5': 91.0, 'ma15': 90.5, 'kc_middle': 91.0, 'kc_upper': 93.0, 'kc_lower': 89.0, 'atr': 1.0, 'is_closed': True},
        {'open': 93.0, 'close': 94.0, 'high': 94.2, 'low': 92.8, 'ma5': 92.5, 'ma15': 91.0, 'kc_middle': 91.5, 'kc_upper': 93.5, 'kc_lower': 89.5, 'atr': 1.0, 'is_closed': True},
    ])

    # 15M 空頭，哪怕 5M 出現金叉或突破，開多一票否決
    passed, reason, _ = MemeSharedProtectionGate.evaluate_entry(
        df_5m=df_5m_bull, df_15m=df_15m_down, side='LONG', symbol='LOBSTER/USDT'
    )
    assert passed is False
    assert "15M_TREND_NOT_UP" in reason

    # 反向：15M 多頭，開空一票否決
    df_15m_up = make_candle_df([
        {'open': 100.0, 'close': 105.0, 'high': 106.0, 'low': 99.0, 'ma5': 103.0, 'ma15': 101.0, 'kc_middle': 102.0, 'kc_upper': 106.0, 'kc_lower': 98.0, 'atr': 2.0, 'is_closed': True},
        {'open': 105.0, 'close': 108.0, 'high': 109.0, 'low': 104.0, 'ma5': 106.0, 'ma15': 103.0, 'kc_middle': 104.0, 'kc_upper': 108.0, 'kc_lower': 100.0, 'atr': 2.0, 'is_closed': True, 'channel_state': 'UP'},
    ])

    passed_short, reason_short, _ = MemeSharedProtectionGate.evaluate_entry(
        df_5m=df_5m_bull, df_15m=df_15m_up, side='SHORT', symbol='LOBSTER/USDT'
    )
    assert passed_short is False
    assert "15M_TREND_NOT_DOWN" in reason_short


def test_5m_ma5_flat_or_reverse_slope_vetoes_entry():
    """2. 覆蓋 5M MA5 走平/彎折否決開倉：MA5 缺乏向上發散動能時，真交叉或破軌一票否決"""
    df_15m_up = make_candle_df([
        {'open': 100.0, 'close': 105.0, 'high': 106.0, 'low': 99.0, 'ma5': 103.0, 'ma15': 101.0, 'kc_middle': 102.0, 'kc_upper': 106.0, 'kc_lower': 98.0, 'atr': 2.0, 'is_closed': True},
        {'open': 105.0, 'close': 108.0, 'high': 109.0, 'low': 104.0, 'ma5': 106.0, 'ma15': 103.0, 'kc_middle': 104.0, 'kc_upper': 108.0, 'kc_lower': 100.0, 'atr': 2.0, 'is_closed': True, 'channel_state': 'UP'},
    ])

    # 構造 5M MA5 走平（ma5_slope 接近 0 或彎頭向下）
    df_5m_flat_ma5 = make_candle_df([
        {'open': 100.0, 'close': 102.0, 'high': 102.5, 'low': 99.5, 'ma5': 102.0, 'ma15': 101.0, 'kc_middle': 100.0, 'kc_upper': 103.0, 'kc_lower': 97.0, 'atr': 1.0, 'is_closed': True},
        {'open': 102.0, 'close': 102.1, 'high': 102.5, 'low': 101.5, 'ma5': 102.05, 'ma15': 101.2, 'kc_middle': 100.5, 'kc_upper': 103.5, 'kc_lower': 97.5, 'atr': 1.0, 'is_closed': True},
        {'open': 102.1, 'close': 102.0, 'high': 102.4, 'low': 101.8, 'ma5': 102.0, 'ma15': 101.3, 'kc_middle': 100.8, 'kc_upper': 103.8, 'kc_lower': 97.8, 'atr': 1.0, 'is_closed': True},
    ])

    passed, reason, _ = MemeSharedProtectionGate.evaluate_entry(
        df_5m=df_5m_flat_ma5, df_15m=df_15m_up, side='LONG', symbol='LOBSTER/USDT'
    )
    assert passed is False
    assert "REJECT_ENTRY" in reason


def test_trend_holding_immunity_intercepts_premature_exit():
    """3. 覆蓋 TrendHoldingImmunity 攔截常規平倉：未破 5M MA15 前 100% 駁回平多"""
    position = {
        'symbol': 'LOBSTER/USDT',
        'side': 'LONG',
        'entry_price': 100.0,
        'atr': 2.0,
        'peak_profit_atr': 0.8, # 未達保本觸發線
        'peak_roe_pct': 0.05,
    }

    # 5M 棒仍在 MA15 102 之上 (收盤 103.5)
    df_5m = make_candle_df([
        {'open': 100.0, 'close': 104.0, 'high': 105.0, 'low': 99.0, 'ma5': 103.0, 'ma15': 101.0, 'kc_middle': 100.0, 'atr': 2.0, 'is_closed': True},
        {'open': 104.0, 'close': 103.5, 'high': 104.5, 'low': 103.0, 'ma5': 103.5, 'ma15': 102.0, 'kc_middle': 101.0, 'atr': 2.0, 'is_closed': True},
    ])

    df_15m = make_candle_df([
        {'open': 100.0, 'close': 104.0, 'high': 105.0, 'low': 99.0, 'ma5': 103.0, 'ma15': 101.0, 'kc_middle': 101.0, 'channel_state': 'UP', 'is_closed': True},
    ])

    # 遇到任何常規雜訊平倉（十字星、回調、碰軌），TrendHoldingAndExitManager 100% 駁回
    can_exit, reason, _ = TrendHoldingAndExitManager.evaluate_exit(
        position=position, df_5m=df_5m, df_15m=df_15m, quote=103.5, candidate_reason='EXIT_BY_PULLBACK'
    )
    assert can_exit is False
    assert "TREND_INTACT_IMMUNITY" in reason


def test_ratchet_profit_lock_exit_trigger():
    """4. 覆蓋獲利鎖利規則：持倉浮盈淨利達 10% 啟動，自歷史最高淨利回吐滿 20% 市價平倉，不設硬保底"""
    position = {
        'symbol': 'LOBSTER/USDT',
        'side': 'LONG',
        'entry_price': 100.0,
        'qty': 10.0,
        'atr': 2.0,
        'peak_profit_atr': 3.0,
        'peak_roe_pct': 0.15, # 曾大賺 15% (衝到 115)
        'highest_price': 115.0,
    }

    # 價格回吐跌破鎖利底線 100 * (1 + 0.15 * 0.8) = 112.0，現價 111.0 (回吐超過 20%)
    df_5m = make_candle_df([
        {'open': 115.0, 'close': 111.0, 'high': 115.0, 'low': 110.5, 'ma5': 113.0, 'ma15': 108.0, 'kc_middle': 107.0, 'atr': 2.0, 'is_closed': True},
    ])

    can_exit, reason, details = TrendHoldingAndExitManager.evaluate_exit(
        position=position, df_5m=df_5m, quote=111.0, candidate_reason='CHECK_EXIT'
    )
    assert can_exit is True
    assert reason == "EXIT_BY_RATCHET_PROFIT_LOCK"
    assert details['giveback_ratio'] >= 0.20


def test_profit_lock_metrics_and_breakeven_population():
    """5. 覆蓋鎖利與保本數據寫回：浮盈達 12% 啟動鎖定 8% 淨利 (Tier 3)"""
    position = {
        'symbol': 'LOBSTER/USDT',
        'side': 'LONG',
        'entry_price': 100.0,
        'qty': 10.0,
        'atr': 2.0,
        'peak_profit_atr': 2.5,
        'peak_roe_pct': 0.12, # 12% 浮盈 (Tier 3: 5%起跑, 每3%一階 -> 鎖定 8% 淨利)
    }

    # 現價 111.0，仍高於鎖定淨利線 (8% 淨利線對應價格約 108.1)
    df_5m = make_candle_df([
        {'open': 110.0, 'close': 111.0, 'high': 112.0, 'low': 109.8, 'ma5': 110.0, 'ma15': 105.0, 'kc_middle': 104.0, 'atr': 2.0, 'is_closed': True},
    ])

    can_exit, reason, _ = TrendHoldingAndExitManager.evaluate_exit(
        position=position, df_5m=df_5m, quote=111.0
    )
    assert can_exit is False
    assert position.get('channel_profit_protection', {}).get('armed') is True
    assert position.get('channel_profit_protection', {}).get('locked_net') == 8.0 # 8% 淨利保底
    assert position.get('locked_phase') == 3 # Tier 3
    assert position.get('breakeven_price') > 107.0


def test_mode1_breakout_exempts_spatial_bias_for_lobster_and_pepe():
    """6. 覆蓋全幣種（龍蝦與 1000PEPE）：模式一（KC 破軌）100% 豁免 1.2% 均線乖離率與影線限制"""
    # 構造 15M 中軌未向下且收盤守住 MA15 的相容背景
    df_15m_compat = make_candle_df([
        {'open': 0.0400, 'close': 0.0410, 'high': 0.0415, 'low': 0.0395, 'ma5': 0.0405, 'ma15': 0.0400, 'kc_middle': 0.0402, 'kc_upper': 0.0420, 'kc_lower': 0.0384, 'atr': 0.0009, 'is_closed': True},
        {'open': 0.0410, 'close': 0.0412, 'high': 0.0418, 'low': 0.0405, 'ma5': 0.0408, 'ma15': 0.0402, 'kc_middle': 0.0405, 'kc_upper': 0.0423, 'kc_lower': 0.0387, 'atr': 0.0009, 'is_closed': True, 'channel_state': 'UP'},
    ])

    # 構造 5M 噴發大陽線：破 KC 上軌，實體達 1.5 ATR，價格大幅偏離 MA5（乖離率達 3.5% > 1.2%）
    # 同時上影線佔比略高，測試豁免效果
    df_5m_lobster_breakout = make_candle_df([
        {'open': 0.0405, 'close': 0.0410, 'high': 0.0412, 'low': 0.0403, 'ma5': 0.0406, 'ma15': 0.0402, 'kc_middle': 0.0405, 'kc_upper': 0.0418, 'kc_lower': 0.0392, 'atr': 0.0006, 'is_closed': True},
        {'open': 0.0410, 'close': 0.0425, 'high': 0.0430, 'low': 0.0408, 'ma5': 0.0410, 'ma15': 0.0405, 'kc_middle': 0.0408, 'kc_upper': 0.0420, 'kc_lower': 0.0396, 'atr': 0.0006, 'is_closed': True},
    ])

    # 測試龍蝦 LOBSTER/USDT 多單破軌
    passed_lobster, reason_lobster, meta_lobster = MemeSharedProtectionGate.evaluate_entry(
        df_5m=df_5m_lobster_breakout, df_15m=df_15m_compat, side='LONG', symbol='LOBSTER/USDT', quote=0.0425
    )
    assert passed_lobster is True
    assert reason_lobster == "AUTHORIZED_ENTRY_MODE1_KC_BREAKOUT"
    assert meta_lobster['bypass_spatial_check'] is True

    # 測試 1000PEPE/USDT 破軌
    df_5m_pepe_breakout = make_candle_df([
        {'open': 0.0040, 'close': 0.0041, 'high': 0.00412, 'low': 0.00398, 'ma5': 0.00405, 'ma15': 0.00402, 'kc_middle': 0.00405, 'kc_upper': 0.00415, 'kc_lower': 0.00395, 'atr': 0.00008, 'is_closed': True},
        {'open': 0.0041, 'close': 0.00425, 'high': 0.00428, 'low': 0.00408, 'ma5': 0.00410, 'ma15': 0.00404, 'kc_middle': 0.00407, 'kc_upper': 0.00418, 'kc_lower': 0.00396, 'atr': 0.00008, 'is_closed': True},
    ])
    passed_pepe, reason_pepe, meta_pepe = MemeSharedProtectionGate.evaluate_entry(
        df_5m=df_5m_pepe_breakout, df_15m=df_15m_compat, side='LONG', symbol='1000PEPE/USDT', quote=0.00425
    )
    assert passed_pepe is True
    assert reason_pepe == "AUTHORIZED_ENTRY_MODE1_KC_BREAKOUT"
    assert meta_pepe['bypass_spatial_check'] is True


def test_mode1_breakout_relaxed_15m_macro_trend_down_short():
    """7. 覆蓋高位破軌起跌（如龍蝦 0.0400 高位起跌做空）：15M 中軌未向上且收盤未突破 15M MA15 即判定相容放行"""
    df_15m_compat_short = make_candle_df([
        {'open': 0.0410, 'close': 0.0405, 'high': 0.0415, 'low': 0.0400, 'ma5': 0.0408, 'ma15': 0.0408, 'kc_middle': 0.0407, 'kc_upper': 0.0420, 'kc_lower': 0.0394, 'atr': 0.0009, 'is_closed': True},
        {'open': 0.0405, 'close': 0.0400, 'high': 0.0408, 'low': 0.0398, 'ma5': 0.0405, 'ma15': 0.0407, 'kc_middle': 0.0405, 'kc_upper': 0.0420, 'kc_lower': 0.0390, 'atr': 0.0009, 'is_closed': True, 'channel_state': 'RANGE'},
    ])

    # 5M 實體陰線摜穿 KC 下軌起跌
    df_5m_short_breakout = make_candle_df([
        {'open': 0.0405, 'close': 0.0400, 'high': 0.0408, 'low': 0.0398, 'ma5': 0.0403, 'ma15': 0.0405, 'kc_middle': 0.0404, 'kc_upper': 0.0416, 'kc_lower': 0.0392, 'atr': 0.0006, 'is_closed': True},
        {'open': 0.0400, 'close': 0.0388, 'high': 0.0402, 'low': 0.0385, 'ma5': 0.0395, 'ma15': 0.0402, 'kc_middle': 0.0402, 'kc_upper': 0.0414, 'kc_lower': 0.0390, 'atr': 0.0006, 'is_closed': True},
    ])

    passed, reason, meta = MemeSharedProtectionGate.evaluate_entry(
        df_5m=df_5m_short_breakout, df_15m=df_15m_compat_short, side='SHORT', symbol='LOBSTER/USDT', quote=0.0388
    )
    assert passed is True
    assert reason == "AUTHORIZED_ENTRY_MODE1_KC_BREAKOUT"
    assert meta['bypass_spatial_check'] is True


def test_mode2_still_enforces_ma5_bias_limit():
    """8. 覆蓋模式二（MA5/MA15 交叉震盪）：仍嚴格檢核 1.2% 均線乖離率與影線"""
    df_15m_up = make_candle_df([
        {'open': 100.0, 'close': 105.0, 'high': 106.0, 'low': 99.0, 'ma5': 103.0, 'ma15': 101.0, 'kc_middle': 102.0, 'kc_upper': 106.0, 'kc_lower': 98.0, 'atr': 2.0, 'is_closed': True},
        {'open': 105.0, 'close': 108.0, 'high': 109.0, 'low': 104.0, 'ma5': 106.0, 'ma15': 103.0, 'kc_middle': 104.0, 'kc_upper': 108.0, 'kc_lower': 100.0, 'atr': 2.0, 'is_closed': True, 'channel_state': 'UP'},
    ])

    # 交叉但未破 KC 上軌 (仍在通道內)，實體未達 0.85 ATR (不觸發模式三)，但價格偏離 MA5 達 2% (> 1.2%)
    df_5m_cross_huge_bias = make_candle_df([
        {'open': 100.0, 'close': 101.0, 'high': 101.5, 'low': 99.5, 'ma5': 100.5, 'ma15': 100.8, 'kc_middle': 101.0, 'kc_upper': 108.0, 'kc_lower': 94.0, 'atr': 3.0, 'is_closed': True},
        {'open': 101.0, 'close': 103.0, 'high': 103.2, 'low': 100.8, 'ma5': 101.5, 'ma15': 100.5, 'kc_middle': 101.5, 'kc_upper': 108.5, 'kc_lower': 94.5, 'atr': 3.0, 'is_closed': True, 'ma_cross': 'GOLDEN', 'cross_bars_ago': 1},
    ])

    passed, reason, _ = MemeSharedProtectionGate.evaluate_entry(
        df_5m=df_5m_cross_huge_bias, df_15m=df_15m_up, side='LONG', symbol='LOBSTER/USDT', quote=103.0
    )
    # quote=103.0, ma5=101.0, bias = 2.0/101.0 = 1.98% > 1.2%
    assert passed is False
    assert "MA5_BIAS_EXCEEDED" in reason



