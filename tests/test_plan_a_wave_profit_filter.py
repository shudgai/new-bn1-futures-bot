"""
tests/test_plan_a_wave_profit_filter.py
======================================
驗證【方案 A 波段獲利過濾（Minimum Favorable Excursion）】：
1. 多單開倉後，若最高價未脫離成本價超過 0.5 ATR 且 peak_roe < 5%，
   且價格守穩在 KC 中軌或 MA15 之上，一律判定為「成本區間正常回踩」，
   嚴禁誤判為真峰頂平倉，強制續抱！
2. 當波段實質展開（漲幅 >= 0.5 ATR 或 peak_roe >= 5% 且跌破中軌/MA15），
   確認真峰頂成型時，立即放行平倉獲利了結！
"""
import pandas as pd
import pytest
from core.services.exits.strict_true_pivot import confirmed_true_pivot_exit
from core.services.data_pipeline import DataPipeline
from core.gates.unified_pipeline_gates import ExitGate

def test_long_pullback_near_entry_price_holds_without_premature_exit():
    # 建立 20 根 K 棒
    # 開倉價 100.0，波段最高價只到 100.2 (0.2 ATR)，隨後第 3 棒小陰線回踩至 100.05
    # 價格穩穩站在 KC 中軌 (99.5) 與 MA15 (99.6) 之上
    timestamps = [1000000 + i * 60000 for i in range(20)]
    closes = [99.0 + i * 0.05 for i in range(17)] + [100.1, 100.2, 100.05]
    highs = [c + 0.05 for c in closes]
    highs[18] = 100.25 # peak
    lows = [c - 0.05 for c in closes]
    opens = [closes[i-1] if i > 0 else 99.0 for i in range(20)]
    
    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1000.0] * 20,
        "is_closed": [True] * 20,
    })
    df.attrs["timeframe_ms"] = 60000
    
    from core.strategy import SuperTrendKeltnerStrategy
    df = SuperTrendKeltnerStrategy().compute_indicators(df)
    
    pos = {
        "symbol": "1000PEPE/USDT",
        "side": "LONG",
        "entry_price": 100.0,
        "open_timestamp": timestamps[16] / 1000.0,
        "peak_roe": 0.01, # 只賺 1%，未達 5%
    }
    
    # 1. 驗證 confirmed_true_pivot_exit 嚴格攔截，不誤報真峰頂
    pivot_res = confirmed_true_pivot_exit(pos, df)
    assert pivot_res is None, "成本價附近微幅回踩不得判定為真峰頂！"
    
    # 2. 驗證 ExitGate 一票否決平倉，強制續抱
    snap = DataPipeline.calculate_snapshot("1000PEPE/USDT", df, current_price=100.05, position=pos)
    should_exit, reason, meta = ExitGate.evaluate(pos, snap)
    assert should_exit is False, "成本價附近守穩 KC 中軌與 MA15 之正常回踩，絕不可平多！"


def test_long_wave_expanded_true_peak_authorizes_exit():
    # 開倉價 100.0，波段衝高到 103.0 (遠大於 0.5 ATR，ATR 約 0.5~1.0)，peak_roe 達 15%
    # 隨後第三棒跌破實體中點且貫穿 MA15
    timestamps = [1000000 + i * 60000 for i in range(20)]
    closes = [99.0 + i * 0.1 for i in range(17)] + [102.5, 103.0, 99.8]
    highs = [c + 0.1 for c in closes]
    highs[18] = 103.2
    lows = [min(closes[i-1] if i > 0 else 99.0, closes[i]) - 0.1 for i in range(20)]
    lows[19] = 99.7
    opens = [closes[i-1] if i > 0 else 99.0 for i in range(20)]
    opens[19] = 103.0 # 大陰線貫穿
    highs[19] = 103.1 # high 必須大於等於 max(open, close)
    
    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1000.0] * 20,
        "is_closed": [True] * 20,
    })
    df.attrs["timeframe_ms"] = 60000
    
    from core.strategy import SuperTrendKeltnerStrategy
    df = SuperTrendKeltnerStrategy().compute_indicators(df)
    
    pos = {
        "symbol": "1000PEPE/USDT",
        "side": "LONG",
        "entry_price": 100.0,
        "open_timestamp": timestamps[16] / 1000.0,
        "peak_roe": 0.15, # 曾獲利 15%
    }
    
    pivot_res = confirmed_true_pivot_exit(pos, df)
    assert pivot_res is not None, "波段實質展開且見頂破位時，必須授權真峰頂平倉！"
    assert pivot_res[0] == "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT"
