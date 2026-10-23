"""
tools/snapshot_golden_dataset.py
================================
持久化下載並鎖定 6 大幣種 2500 根 3m K 線為標準黃金數據集 (Golden Dataset)。
記錄數據時間範圍、K 線數量與 SHA-256 雜湊值，確保後續所有回測完全可重現 (Deterministic)。
"""

import os
import json
import time
import hashlib
import urllib.request
import pandas as pd
from typing import Dict, Any

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "1000PEPEUSDT", "SUIUSDT"]
TOTAL_BARS = 2500
INTERVAL = "3m"
DATASET_PATH = "data/golden_dataset_2500_bars.json"
MANIFEST_PATH = "data/golden_dataset_manifest.json"


def fetch_klines_paginated(symbol: str, total_limit: int = 2500, interval: str = "3m") -> pd.DataFrame:
    all_bars = []
    end_time = None
    remaining = total_limit
    while remaining > 0:
        batch_limit = min(remaining, 1000)
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={batch_limit}"
        if end_time:
            url += f"&endTime={end_time}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not data:
            break
        all_bars = data + all_bars
        end_time = data[0][0] - 1
        remaining -= len(data)
        if len(data) < batch_limit:
            break
        time.sleep(0.1)

    df = pd.DataFrame(all_bars, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_base_vol",
        "taker_quote_vol", "ignore"
    ])
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)
    df["open_time_dt"] = pd.to_datetime(df["open_time"], unit="ms")
    return df


def main():
    os.makedirs("data", exist_ok=True)
    dataset = {}
    meta = {}

    print(f"[*] 開始下載並固化 6 大幣種 {TOTAL_BARS} 根 3m K 線快照...")
    for sym in SYMBOLS:
        df = fetch_klines_paginated(sym, TOTAL_BARS, INTERVAL)
        # 轉為字典清單儲存
        records = df.to_dict(orient="records")
        # 轉換 Timestamp 為字串
        for r in records:
            r["open_time_dt"] = str(r["open_time_dt"])
        dataset[sym] = records

        meta[sym] = {
            "bars_count": len(df),
            "start_time": str(df["open_time_dt"].iloc[0]),
            "end_time": str(df["open_time_dt"].iloc[-1]),
            "start_open": float(df["open"].iloc[0]),
            "end_close": float(df["close"].iloc[-1]),
        }
        print(f"    - {sym}: {len(df)} 根 K 線 ({meta[sym]['start_time']} ~ {meta[sym]['end_time']})")

    # 序列化存檔
    content_str = json.dumps(dataset, sort_keys=True)
    sha256 = hashlib.sha256(content_str.encode("utf-8")).hexdigest()

    manifest = {
        "dataset_name": "golden_dataset_2500_bars",
        "symbols": SYMBOLS,
        "interval": INTERVAL,
        "total_bars_target": TOTAL_BARS,
        "snapshot_sha256": sha256,
        "snapshot_created_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "symbols_meta": meta,
    }

    with open(DATASET_PATH, "w", encoding="utf-8") as f:
        f.write(content_str)

    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"\n[+] 黃金數據集已成功鎖定存檔至: {DATASET_PATH}")
    print(f"[+] 中繼資訊清單已輸出至: {MANIFEST_PATH}")
    print(f"[+] 資料集 SHA-256: {sha256}")


if __name__ == "__main__":
    main()
