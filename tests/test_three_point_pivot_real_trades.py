"""
測試依據 2026-10-22 使用者授權：
「平倉的位置都太晚，改為標準已收線三點峰頂/谷底樞紐 (Three-Point Pivot) 平倉」
回歸案例：龙虾/USDT 08:48 買多與 09:15 買多，必須在頂部剛成型時果斷止盈落袋，
嚴禁一路坐過山車回到成本價甚至反側外軌割肉！
"""
import urllib.request, json, urllib.parse
import pandas as pd
import pytest

from core.services.data_pipeline import DataPipeline
from core.gates.unified_pipeline_gates import ExitGate

# 08:48 買多 @ 0.02108211，08:54 衝高到 0.02129 見頂成型
# 09:15 買多 @ 0.02112211，09:21 衝高到 0.02128 見頂成型
@pytest.fixture(scope="module")
def lobster_candles():
    sym = urllib.parse.quote("龙虾USDT")
    url = f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1m&endTime=1792633080000&limit=90"
    r = json.load(urllib.request.urlopen(url, timeout=10))
    df = pd.DataFrame([[k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in r],
                      columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['t'] = pd.to_datetime(df.timestamp, unit='ms') + pd.Timedelta(hours=8)
    return df

def test_trade_0848_exits_at_peak_0856(lobster_candles):
    """08:48 開多後，在 08:54 創出高點 0.02129，08:56 確認收黑時必須及時觸發 EARLY_PIVOT_PEAK_EXIT 平多"""
    df = lobster_candles
    pos = {"symbol": "龙虾/USDT", "side": "LONG", "entry_price": 0.02108211, "qty": 34776.0, "peak_roe": 0.0098}

    # 找到 08:56:00
    sub_df = df[df['t'] <= '2026-10-22 08:56:00'].copy()
    quote = sub_df.iloc[-1]['close'] # 0.02123

    snap = DataPipeline.calculate_snapshot("龙虾/USDT", sub_df, current_price=quote, position=pos)
    should_exit, reason, meta = ExitGate.evaluate(pos, snap)

    assert should_exit is True, "08:56 已收線確認 08:54 為三點頂部樞紐，必須及時平多停利！"
    assert reason == "EARLY_PIVOT_PEAK_EXIT"
    assert meta["pivot_meta"]["peak_high"] == 0.02129
    # 停利價格 0.02123 遠高於入場價 0.02108，獲利了結！

def test_trade_0915_exits_at_peak_0925(lobster_candles):
    """09:15 開多後，在 09:24 創出高點 0.02128，09:25 確認回落時必須及時觸發 EARLY_PIVOT_PEAK_EXIT 平多"""
    df = lobster_candles
    pos = {"symbol": "龙虾/USDT", "side": "LONG", "entry_price": 0.02112211, "qty": 34105.0, "peak_roe": 0.0075}

    # 找到 09:25:00
    sub_df = df[df['t'] <= '2026-10-22 09:25:00'].copy()
    quote = sub_df.iloc[-1]['close'] # 0.02121

    snap = DataPipeline.calculate_snapshot("龙虾/USDT", sub_df, current_price=quote, position=pos)
    should_exit, reason, meta = ExitGate.evaluate(pos, snap)

    assert should_exit is True, "09:25 已確認 09:24 為波段峰頂，必須及時平多停利，絕不抱到 09:35 破軌！"
    assert reason == "EARLY_PIVOT_PEAK_EXIT"
    assert meta["pivot_meta"]["peak_high"] == 0.02128
    # 停利價格 0.02121 遠高於入場價 0.02112，獲利了結！
