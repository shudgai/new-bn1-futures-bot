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


def test_hard_breakeven_trigger():
    """4. 覆蓋 Hard Breakeven 保本移損觸發：浮盈峰值曾達 >= 1.2 ATR 後，回撤碰觸保本線即刻平倉"""
    position = {
        'symbol': 'LOBSTER/USDT',
        'side': 'LONG',
        'entry_price': 100.0,
        'atr': 2.0,
        'peak_profit_atr': 1.6, # 曾大賺 1.6 ATR (衝到 103.2)
        'peak_roe_pct': 0.09,
    }

    df_5m = make_candle_df([
        {'open': 100.0, 'close': 103.0, 'high': 103.5, 'low': 99.5, 'ma5': 102.0, 'ma15': 101.0, 'kc_middle': 100.5, 'atr': 2.0, 'is_closed': True},
        # 價格一路回撤跌回成本價 100.02
        {'open': 103.0, 'close': 100.02, 'high': 103.0, 'low': 99.8, 'ma5': 101.0, 'ma15': 100.5, 'kc_middle': 100.5, 'atr': 2.0, 'is_closed': True},
    ])

    can_exit, reason, details = TrendHoldingAndExitManager.evaluate_exit(
        position=position, df_5m=df_5m, quote=100.02, candidate_reason='CHECK_EXIT'
    )
    assert can_exit is True
    assert reason == "EXIT_BY_HARD_BREAKEVEN_PROTECTION"


def test_profit_lock_metrics_and_breakeven_population():
    """5. 覆蓋鎖利與保本數據寫回：持倉在浮盈發展中正確計算並寫入 locked_profit_usdt 與 breakeven_price"""
    position = {
        'symbol': 'LOBSTER/USDT',
        'side': 'LONG',
        'entry_price': 100.0,
        'qty': 10.0,
        'atr': 2.0,
        'peak_profit_atr': 1.5,
        'peak_roe_pct': 0.085,
    }

    df_5m = make_candle_df([
        {'open': 100.0, 'close': 102.5, 'high': 103.0, 'low': 99.8, 'ma5': 102.0, 'ma15': 101.0, 'kc_middle': 100.5, 'atr': 2.0, 'is_closed': True},
    ])

    can_exit, reason, _ = TrendHoldingAndExitManager.evaluate_exit(
        position=position, df_5m=df_5m, quote=102.5
    )
    assert can_exit is False
    assert position.get('breakeven_price') == 100.05
    assert position.get('locked_profit_usdt') == pytest.approx(0.5, rel=1e-3)
    assert position.get('channel_profit_protection', {}).get('armed') is True
    assert position.get('channel_profit_protection', {}).get('status') == 'ARMED'

