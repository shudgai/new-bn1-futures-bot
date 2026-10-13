"""Comprehensive symmetry unit tests for Short Entries:
1. MA5 Death Cross Fast Short (KC_DEATH_CROSS_FAST_SHORT)
2. Closed-bar KC lower band breakout Short (evaluate_closed_entry & evaluate_entry_contract)
3. Real-time lower band breach Short (AUTHORIZED_REALTIME_BREAKOUT)
4. Full mirror symmetry between Long and Short
"""
import pytest
import pandas as pd
import numpy as np

from core.gates.pipeline import pipeline, DEATH_CROSS_FAST_SHORT_CODE, GOLDEN_CROSS_FAST_LONG_CODE
from core.services.entry_contract import evaluate_entry_contract
from core.services.strategies.unified_entry_strategy import (
    check_ma_cross_entry,
    evaluate_closed_entry,
    validate_ma_alignment,
)


def _build_base_dataframe(n=30, base_price=100.0, trend="DOWN"):
    """Helper to generate consistent OHLCV dataframe with indicators."""
    records = []
    price = base_price
    atr = 1.0
    for i in range(n):
        if trend == "DOWN":
            price -= 0.2
            o = price + 0.15
            c = price - 0.15
            h = o + 0.05
            l = c - 0.05
        elif trend == "UP":
            price += 0.2
            o = price - 0.15
            c = price + 0.15
            h = c + 0.05
            l = o - 0.05
        else: # FLAT
            o = price
            c = price
            h = price + 0.1
            l = price - 0.1
            
        records.append({
            "timestamp": 1700000000000 + i * 60000,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 100.0,
            "is_closed": True,
            "atr": atr,
            "kc_middle": price,
            "kc_upper": price + 2.0 * atr,
            "kc_lower": price - 2.0 * atr,
            "ma3": price,
            "ma5": price,
            "ma15": price,
            "channel_state": "KC向下" if trend == "DOWN" else "KC向上",
        })
    df = pd.DataFrame(records)
    df.attrs['timeframe_ms'] = 60000
    return df


class TestShortEntrySymmetry:

    def test_ma5_death_cross_fast_short_success(self):
        """1. MA5 Death Cross: ma5 crosses below ma15, close < ma5 and close < open."""
        df = _build_base_dataframe(30, base_price=100.0, trend="DOWN")
        # 前一根 ma5 >= ma15
        df.loc[df.index[-2], 'ma5'] = 98.2
        df.loc[df.index[-2], 'ma15'] = 98.0
        # 當根 ma5 < ma15 死叉，close < ma5 且 close < open
        df.loc[df.index[-1], 'ma5'] = 97.4
        df.loc[df.index[-1], 'ma15'] = 97.8
        df.loc[df.index[-1], 'open'] = 97.5
        df.loc[df.index[-1], 'close'] = 97.0
        df.loc[df.index[-1], 'high'] = 97.6
        df.loc[df.index[-1], 'low'] = 96.9

        res = check_ma_cross_entry(df, live_price=97.0)
        assert res is not None, "check_ma_cross_entry should trigger on death cross"
        assert res['side'] == 'SHORT'
        assert res['type'] == 'KC_DEATH_CROSS_FAST_SHORT'
        assert res['bypass_spatial_check'] is True

        # 驗證 MA 對齊閘門
        passed, reason = validate_ma_alignment(df, 'SHORT', quote=97.0)
        assert passed is True, f"validate_ma_alignment SHORT failed: {reason}"

    def test_death_cross_vs_golden_cross_symmetry(self):
        """Mirror symmetry test: Golden Cross Long vs Death Cross Short."""
        # Long scenario
        df_long = _build_base_dataframe(30, base_price=100.0, trend="UP")
        df_long.loc[df_long.index[-2], 'ma5'] = 101.8
        df_long.loc[df_long.index[-2], 'ma15'] = 102.0
        df_long.loc[df_long.index[-1], 'ma5'] = 102.6
        df_long.loc[df_long.index[-1], 'ma15'] = 102.2
        df_long.loc[df_long.index[-1], 'open'] = 102.5
        df_long.loc[df_long.index[-1], 'close'] = 103.0
        df_long.loc[df_long.index[-1], 'high'] = 103.1
        df_long.loc[df_long.index[-1], 'low'] = 102.4

        res_long = check_ma_cross_entry(df_long, live_price=103.0)
        assert res_long is not None
        assert res_long['side'] == 'LONG'
        assert res_long['type'] == 'KC_GOLDEN_CROSS_FAST_LONG'

        # Short scenario
        df_short = _build_base_dataframe(30, base_price=100.0, trend="DOWN")
        df_short.loc[df_short.index[-2], 'ma5'] = 98.2
        df_short.loc[df_short.index[-2], 'ma15'] = 98.0
        df_short.loc[df_short.index[-1], 'ma5'] = 97.4
        df_short.loc[df_short.index[-1], 'ma15'] = 97.8
        df_short.loc[df_short.index[-1], 'open'] = 97.5
        df_short.loc[df_short.index[-1], 'close'] = 97.0
        df_short.loc[df_short.index[-1], 'high'] = 97.6
        df_short.loc[df_short.index[-1], 'low'] = 96.9

        res_short = check_ma_cross_entry(df_short, live_price=97.0)
        assert res_short is not None
        assert res_short['side'] == 'SHORT'
        assert res_short['type'] == 'KC_DEATH_CROSS_FAST_SHORT'

    def test_closed_bar_kc_lower_breakout_short(self):
        """2. KC Lower Band Breakout: 2 consecutive red bars below kc_lower."""
        df = _build_base_dataframe(30, base_price=100.0, trend="DOWN")
        atr = 1.0

        # Bar -2 (c1): 收紅 K 且收在 kc_lower 之外
        df.loc[df.index[-2], 'kc_upper'] = 99.0
        df.loc[df.index[-2], 'kc_middle'] = 97.0
        df.loc[df.index[-2], 'kc_lower'] = 95.0
        df.loc[df.index[-2], 'open'] = 95.2
        df.loc[df.index[-2], 'close'] = 94.6  # < 95.0 (kc_lower)
        df.loc[df.index[-2], 'high'] = 95.3
        df.loc[df.index[-2], 'low'] = 94.5
        df.loc[df.index[-2], 'ma3'] = 95.0
        df.loc[df.index[-2], 'ma5'] = 95.5
        df.loc[df.index[-2], 'ma15'] = 96.5

        # Bar -1 (c): 再次收紅 K 且收在 kc_lower 之外
        df.loc[df.index[-1], 'kc_upper'] = 98.5
        df.loc[df.index[-1], 'kc_middle'] = 96.5
        df.loc[df.index[-1], 'kc_lower'] = 94.5
        df.loc[df.index[-1], 'open'] = 94.5
        df.loc[df.index[-1], 'close'] = 93.8  # < 94.5 (kc_lower)
        df.loc[df.index[-1], 'high'] = 94.6
        df.loc[df.index[-1], 'low'] = 93.7
        df.loc[df.index[-1], 'ma3'] = 94.2
        df.loc[df.index[-1], 'ma5'] = 94.8
        df.loc[df.index[-1], 'ma15'] = 96.0

        # 第 3 根形成中 K 棒
        live_bar = {
            "timestamp": df.iloc[-1]['timestamp'] + 60000,
            "open": 93.8,
            "high": 93.9,
            "low": 93.3,
            "close": 93.4,
            "volume": 120.0,
            "is_closed": False,
            "atr": atr,
            "kc_middle": 96.0,
            "kc_upper": 98.0,
            "kc_lower": 94.0,
            "ma3": 93.8,
            "ma5": 94.2,
            "ma15": 95.5,
            "channel_state": "KC向下",
        }
        df_with_live = pd.concat([df, pd.DataFrame([live_bar])], ignore_index=True)
        df_with_live.attrs['timeframe_ms'] = 60000

        passed, reason, details = evaluate_closed_entry(df_with_live, 'SHORT', price=93.4)
        assert passed is True, f"evaluate_closed_entry SHORT failed: {reason}"
        assert details['action'] == 'ENTER'
        assert details['side'] == 'SHORT'

    def test_realtime_kc_lower_breakout_short(self):
        """3. Realtime lower band breach Short (AUTHORIZED_REALTIME_BREAKOUT)."""
        df = _build_base_dataframe(30, base_price=100.0, trend="DOWN")
        atr = 1.0

        # 取前 29 根作為已收線
        closed_df = df.iloc[:29].copy()
        last_closed_ts = closed_df.iloc[-1]['timestamp']

        # 前一根收線指標
        closed_df.loc[closed_df.index[-1], 'atr'] = atr
        closed_df.loc[closed_df.index[-1], 'ma5'] = 95.0
        closed_df.loc[closed_df.index[-1], 'ma15'] = 96.0
        closed_df.loc[closed_df.index[-2], 'ma5'] = 95.2
        closed_df.loc[closed_df.index[-2], 'ma15'] = 96.2

        # 當根形成中 K 線（嚴格相隔 60000 ms）
        live_bar = {
            "timestamp": last_closed_ts + 60000,
            "open": 94.0,
            "high": 94.1,
            "low": 93.0,
            "close": 93.2,  # 即時價 93.2，跌破 kc_lower (93.5)，實體 94.0 - 93.2 = 0.8 >= 0.5 ATR
            "volume": 200.0,
            "is_closed": False,
            "atr": atr,
            "kc_middle": 95.0,
            "kc_upper": 97.0,
            "kc_lower": 93.5,
            "ma3": 93.5,
            "ma5": 94.0,
            "ma15": 94.6,  # abs(93.2 - 94.6) = 1.4 <= 1.8 ATR
            "channel_state": "KC向下",
        }
        df_live = pd.concat([closed_df, pd.DataFrame([live_bar])], ignore_index=True)
        df_live.attrs['timeframe_ms'] = 60000

        quote = 93.2
        decision = pipeline.detect_realtime_breakout(df_live, quote=quote, side='SHORT')
        assert decision is not None, "Realtime breakout short detection should not be None"
        assert decision['side'] == 'SHORT'
        assert decision['type'] == 'AUTHORIZED_REALTIME_BREAKOUT'
        assert decision['ma5_direction_aligned'] is True

        # pipeline 授權驗證
        diagnostics = {}
        auth = pipeline.authorize(
            decision, df_live, quote=quote, requested_side='SHORT',
            symbol='BTCUSDT', diagnostics=diagnostics
        )
        assert auth is not None, f"pipeline.authorize failed: {diagnostics.get('reason')}"
        assert auth['side'] == 'SHORT'
