"""
tools/replay_0856_0925.py
歷史重播 2026-10-22 08:56 與 09:25 龍蝦/USDT 實際計算
"""
import urllib.request
import urllib.parse
import json
import os
import math
import pandas as pd

from core.services.data_pipeline import DataPipeline
from core.gates.unified_pipeline_gates import ExitGate
from core.services.exits.net_roe_profit_lock import (
    calculate_net_roe_pct,
    tiered_net_roe_floor,
    evaluate_net_roe_profit_lock,
    DEFAULT_LOCK_CONFIG,
)

CACHE_FILE = "scratch/lobster_20261022_90bars.json"

def get_lobster_candles():
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

def run_replay():
    df = get_lobster_candles()
    print("=== 歷史重播驗證報告（Binance USDT-M 期貨 龙虾USDT 1m K 線）===")
    print(f"資料來源: Binance FAPI klines (endTime=1792633080000 即 2026-10-22 09:38:00 UTC+8)")
    print(f"快取檔案: {CACHE_FILE}")

    targets = [
        {
            "label": "時間點 1: 2026-10-22 08:56:00 (UTC+8)",
            "cutoff_time": "2026-10-22 08:56:00",
            "pos": {
                "symbol": "龙虾/USDT",
                "side": "LONG",
                "entry_price": 0.02108211,
                "qty": 34776.0,
                "leverage": 5.0,
                "highest_price": 0.02129,
                "peak_roe": 0.0098,  # 浮盈峰值約 0.98% 價格波幅
            }
        },
        {
            "label": "時間點 2: 2026-10-22 09:25:00 (UTC+8)",
            "cutoff_time": "2026-10-22 09:25:00",
            "pos": {
                "symbol": "龙虾/USDT",
                "side": "LONG",
                "entry_price": 0.02112211,
                "qty": 34105.0,
                "leverage": 5.0,
                "highest_price": 0.02128,
                "peak_roe": 0.0075,
            }
        }
    ]

    for item in targets:
        print("\n" + "=" * 65)
        print(f"【{item['label']}】")
        cutoff = item["cutoff_time"]
        sub_df = df[df['t'] <= cutoff].copy()
        last_bar = sub_df.iloc[-1]
        prev_bar = sub_df.iloc[-2]
        quote = last_bar['close']

        pos = copy.deepcopy(item["pos"])
        snap = DataPipeline.calculate_snapshot("龙虾/USDT", sub_df, current_price=quote, position=pos)

        # 指標計算
        atr = getattr(snap, "atr", 0.0)
        kc_mid = getattr(snap, "kc_middle", 0.0)
        kc_up = getattr(snap, "kc_upper", 0.0)
        kc_low = getattr(snap, "kc_lower", 0.0)
        curr_k = getattr(snap, "curr_k", {}) or {}
        prev_k = getattr(snap, "prev_k", {}) or {}
        ma5_curr = float(curr_k.get("ma5", quote))
        ma5_prev = float(prev_k.get("ma5", ma5_curr))
        ema40 = float(getattr(snap, "ema_slow", 0.0) or 0.0)

        # ROE 計算
        entry_p = pos["entry_price"]
        raw_roe = ((quote - entry_p) / entry_p) * pos["leverage"] * 100.0
        net_roe = calculate_net_roe_pct(pos, quote, "LONG")
        peak_net_roe = calculate_net_roe_pct(pos, pos["highest_price"], "LONG")
        floor, tier = tiered_net_roe_floor(peak_net_roe)

        should_exit, reason, meta = ExitGate.evaluate(pos, snap)

        print(f"1. K 線時間: {last_bar['t']} (已收線, ts={int(last_bar['timestamp'])})")
        print(f"2. OHLC: Open={last_bar['open']:.6f}, High={last_bar['high']:.6f}, Low={last_bar['low']:.6f}, Close={last_bar['close']:.6f}, Vol={last_bar['volume']:.1f}")
        print(f"3. 通道與均線: KC_Mid={kc_mid:.6f}, KC_Upper={kc_up:.6f}, KC_Lower={kc_low:.6f}, ATR={atr:.6f}")
        print(f"   MA5當前={ma5_curr:.6f}, MA5前值={ma5_prev:.6f} (斜率={'向上' if ma5_curr>ma5_prev else '走平或向下'}), EMA40慢線={ema40:.6f}")
        print(f"4. 持倉狀態: Side={pos['side']}, Entry={entry_p:.6f}, Qty={pos['qty']}, Leverage={pos['leverage']}x, Current_Quote={quote:.6f}")
        print(f"5. 損益與鎖利:")
        print(f"   毛 ROE: {raw_roe:.2f}%")
        print(f"   當前真實淨 ROE (扣除手續費與滑價): {net_roe:.2f}%")
        print(f"   歷史最高價: {pos['highest_price']:.6f} -> 歷史峰值真實淨 ROE: {peak_net_roe:.2f}%")
        print(f"   鎖利狀態: Armed={pos.get('profit_lock_armed', False)}, 鎖利階段=Tier {tier}, 鎖利底線={floor}")
        print(f"6. ExitGate.evaluate() 判定結果:")
        print(f"   should_exit: {should_exit}")
        print(f"   reason: {reason}")
        print(f"   cause: {meta.get('cause', 'N/A')}")

if __name__ == "__main__":
    import copy
    run_replay()
