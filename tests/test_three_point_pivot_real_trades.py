"""
tests/test_three_point_pivot_real_trades.py
===========================================
第一階段出場重構驗收（Phase 5 Exit Refactor）：
驗證 2026-10-22 龍蝦/USDT 08:48 與 09:15 交易在第一階段出場架構下的真實行為：
1. 08:48 開多 @ 0.02108211：
   - 08:54 衝高至 0.021290 (真實淨 ROE 約 +4.38%，未達 +8% 階梯鎖利啟動門檻)。
   - 08:56 價格回落至 0.021230，但 MA5 依然向上抬頭 (0.021224 > 0.021216)，且報價站在 MA5 與 KC 中軌之上。
   - 【第一階段重構規範】：微型峰頂不得單獨凌駕於趨勢續抱保護，價格守住趨勢結構時強制一票否決平多 (VETO_HOLD)！
2. 09:15 開多 @ 0.02112211：
   - 09:24 衝高至 0.021280 (真實淨 ROE 約 +3.19%，未達 +8% 階梯鎖利啟動門檻)。
   - 09:25 價格為 0.021210，牢牢守穩在 KC 中軌 (0.021164) 之上，中軌向上。
   - 【第一階段重構規範】：開倉初期呼吸保護與守穩中軌防洗，強制一票否決平多 (VETO_HOLD)！
"""
import urllib.request, json, urllib.parse, os
import pandas as pd
import pytest

from core.services.data_pipeline import DataPipeline
from core.gates.unified_pipeline_gates import ExitGate

CACHE_FILE = "scratch/lobster_20261022_90bars.json"

@pytest.fixture(scope="module")
def lobster_candles():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            raw_klines = json.load(f)
    else:
        sym = urllib.parse.quote("龙虾USDT")
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1m&endTime=1792633080000&limit=90"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw_klines = json.load(resp)
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            json.dump(raw_klines, f)

    df = pd.DataFrame(
        [[k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in raw_klines],
        columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
    )
    df['t'] = pd.to_datetime(df.timestamp, unit='ms') + pd.Timedelta(hours=8)
    return df


def test_trade_0848_exits_on_closed_outer_rail_engulfing_at_0856(lobster_candles):
    """08:56 的收線反轉吞噬滿足新授權的外軌峰頂出場條件。"""
    df = lobster_candles
    pos = {
        "symbol": "龙虾/USDT",
        "side": "LONG",
        "entry_price": 0.02108211,
        "qty": 34776.0,
        "leverage": 5.0,
        "highest_price": 0.02129,
        "peak_roe": 0.0098,
    }

    sub_df = df[df['t'] <= '2026-10-22 08:56:00'].copy()
    quote = sub_df.iloc[-1]['close'] # 0.02123

    snap = DataPipeline.calculate_snapshot("龙虾/USDT", sub_df, current_price=quote, position=pos)
    should_exit, reason, meta = ExitGate.evaluate(pos, snap)

    assert should_exit is True
    assert reason == "EXIT_LONG_AT_PEAK"
    assert meta["trigger"] == "OUTER_RAIL_ENGULFING"


def test_trade_0915_holds_on_kc_middle_support_at_0925(lobster_candles):
    """09:15 開多後，09:25 價格守在 KC 中軌之上且未達鎖利門檻，開倉呼吸保護 VETO_HOLD 續抱"""
    df = lobster_candles
    pos = {
        "symbol": "龙虾/USDT",
        "side": "LONG",
        "entry_price": 0.02112211,
        "qty": 34105.0,
        "leverage": 5.0,
        "highest_price": 0.02128,
        "peak_roe": 0.0075,
    }

    sub_df = df[df['t'] <= '2026-10-22 09:25:00'].copy()
    quote = sub_df.iloc[-1]['close'] # 0.02121

    snap = DataPipeline.calculate_snapshot("龙虾/USDT", sub_df, current_price=quote, position=pos)
    should_exit, reason, meta = ExitGate.evaluate(pos, snap)

    # 第一階段重構驗收斷言：守住 KC 中軌，VETO_HOLD 一票否決平倉
    assert should_exit is False, "09:25 價格守穩 KC 中軌之上，未見實質趨勢破壞，不得提早平倉！"
    assert reason == "VETO_HOLD"
    assert "中軌防守線" in meta.get("cause", "")
