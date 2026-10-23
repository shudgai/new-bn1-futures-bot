#!/usr/bin/env python3
"""
tools/diagnose_phase5_5_target_trades.py
========================================
Phase 5.5 離線開空位置專項診斷工具：針對 2026-10-23 12:31 ~ 12:55 圖中交易
提取前後 20 根 K 棒所有關鍵技術指標、時序、狀態對齊與 Forward Returns / MFE / MAE。
"""

import urllib.request
import json
import os
import math
import numpy as np
import pandas as pd
from datetime import datetime

def main():
    url = 'http://127.0.0.1:8006/api/klines?symbol=%E9%BE%99%E8%99%BE%2FUSDT&timeframe=1m&limit=200&include_live=true'
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        res = json.loads(resp.read().decode())
    
    bars = res.get('data', [])
    if not bars:
        print("Error: No data retrieved")
        return
    
    df = pd.DataFrame(bars)
    df['dt'] = pd.to_datetime(df['time'], unit='s') + pd.Timedelta(hours=8)
    
    # 讀取 paper_account.json
    with open('data/paper_account.json') as f:
        acc = json.load(f)
    
    trades = [t for t in acc.get('trades', []) if '龙虾' in str(t.get('symbol')) and '10/23 12:' in str(t.get('time'))]
    
    print("=" * 80)
    print("【圖中 10/23 12:20 ~ 13:00 目標交易記錄】")
    print("=" * 80)
    for idx, t in enumerate(trades):
        print(f"[{idx+1}] 時間: {t['time']} | 動作: {t['action']} | 價格: {t['price']} | 數量: {t.get('qty', 0):.2f} | 理由: {t.get('reason')} | 損益: {t.get('pnl', 0):.4f}U")
    
    # 保存該區間的完整 K 棒資料至 CSV
    df.to_csv("scratch/phase5_5_diagnosis/lobster_live_200bars.csv", index=False)
    print(f"\n已導出 200 根 1m K 線至 scratch/phase5_5_diagnosis/lobster_live_200bars.csv")
    
    # 找出圖中的兩筆賣空 (12:37:09 與 12:42:40) 以及平空 (12:39:07 與 12:52:35)
    # 我們找到對應的 bar index
    short_entries = [t for t in trades if t['action'] == 'OPEN_SHORT']
    short_exits = [t for t in trades if t['action'] == 'CLOSE_SHORT']
    
    # 計算 EMA 與斜率指標
    close = df['close']
    df['ema_fast'] = close.ewm(span=15, adjust=False).mean()
    df['ema_slow'] = close.ewm(span=40, adjust=False).mean()
    df['red_ma'] = close.ewm(span=40, adjust=False).mean()
    df['ma5_slope'] = (df['ma5'] - df['ma5'].shift(1))
    df['kc_mid_slope'] = (df['kc_middle'] - df['kc_middle'].shift(1))
    df['ema_fast_slope'] = (df['ema_fast'] - df['ema_fast'].shift(1))
    df['ema_slow_slope'] = (df['ema_slow'] - df['ema_slow'].shift(1))
    
    # ATR 計算
    tr = np.maximum(df['high'] - df['low'], np.maximum((df['high'] - df['close'].shift(1)).abs(), (df['low'] - df['close'].shift(1)).abs()))
    df['atr'] = tr.rolling(14).mean().bfill()
    
    print("\n" + "=" * 80)
    print("【二、逐筆檢查開空訊號前後 20 根 K 棒數據與結構特徵】")
    print("=" * 80)
    
    for s_idx, entry in enumerate(short_entries, 1):
        # 匹配時間戳
        # 交易時間 e.g. "10/23 12:37:09"
        e_time_str = entry['time']
        # 尋找前一個已收線 bar 或當前 bar
        # trade id 是毫秒時間戳
        trade_ts_sec = entry['id'] / 1000.0
        
        # 找到最近的 bar
        bar_matches = df[df['time'] <= trade_ts_sec]
        if bar_matches.empty:
            continue
        bar_idx = bar_matches.index[-1]
        target_bar = df.iloc[bar_idx]
        
        print(f"\n--- [開空訊號 #{s_idx}] 時間: {e_time_str} (Bar Time: {target_bar['dt']}) ---")
        print(f"進場成交價: {entry['price']} | Bar 收盤價: {target_bar['close']} | 成交訊號理由: {entry.get('reason')}")
        print(f"KC 上軌: {target_bar['kc_upper']:.5f} | 中軌: {target_bar['kc_middle']:.5f} | 下軌: {target_bar['kc_lower']:.5f}")
        print(f"MA5: {target_bar['ma5']:.5f} (斜率: {target_bar['ma5_slope']:+.7f})")
        print(f"MA15: {target_bar['ma15']:.5f} | EMA15: {target_bar['ema_fast']:.5f} (斜率: {target_bar['ema_fast_slope']:+.7f})")
        print(f"Red MA / EMA40: {target_bar['red_ma']:.5f} (斜率: {target_bar['ema_slow_slope']:+.7f})")
        print(f"KC 中軌斜率: {target_bar['kc_mid_slope']:+.7f} | ATR: {target_bar['atr']:.6f}")
        
        # 價格相對位置
        dist_to_mid = (target_bar['close'] - target_bar['kc_middle']) / target_bar['atr']
        dist_to_low = (target_bar['close'] - target_bar['kc_lower']) / target_bar['atr']
        print(f"價格距中軌: {dist_to_mid:+.2f} ATR | 距下軌: {dist_to_low:+.2f} ATR")
        
        # 前 20 根 K 棒的最低價與當前反彈幅度
        prev_20 = df.iloc[max(0, bar_idx - 20):bar_idx + 1]
        lowest_in_20 = prev_20['low'].min()
        lowest_idx = prev_20['low'].idxmin()
        lowest_time = df.iloc[lowest_idx]['dt']
        bounce_from_low = (target_bar['close'] - lowest_in_20) / target_bar['atr']
        bounce_pct = (target_bar['close'] - lowest_in_20) / lowest_in_20 * 100.0
        
        print(f"過去 20 根最低點: {lowest_in_20:.5f} (發生在 {lowest_time})")
        print(f"從最低點反彈幅度: +{bounce_from_low:.2f} ATR (+{bounce_pct:.2f}%)")
        
        # 檢查問題 1~4:
        # 1. 價格是否已從低點反彈，且 MA5、短期 EMA 或紅色趨勢線已轉為向上
        # 2. KC 中軌斜率是否已走平或向上
        # 3. 是否在靠近中軌、跌深反彈位置追空
        # 4. 是否誤認趨勢
        is_rebounded = bounce_from_low >= 0.5
        ma5_up = target_bar['ma5_slope'] > 0
        ema15_up = target_bar['ema_fast_slope'] > 0
        kc_mid_flat_or_up = target_bar['kc_mid_slope'] >= -0.000002
        near_mid = abs(dist_to_mid) < 0.6
        
        print(f"-> 診斷 Q1: 價格已從低點明顯反彈? {is_rebounded} | MA5向上? {ma5_up} | 短期EMA向上? {ema15_up}")
        print(f"-> 診斷 Q2: KC 中軌走平或向上? {kc_mid_flat_or_up} (slope={target_bar['kc_mid_slope']:+.7f})")
        print(f"-> 診斷 Q3: 靠近中軌追空? {near_mid} (距中軌僅 {dist_to_mid:+.2f} ATR)")
        
        # 計算後續 3, 5, 10, 15 根 K 棒報酬與 MFE / MAE
        forward_returns = {}
        for f_bars in [3, 5, 10, 15]:
            if bar_idx + f_bars < len(df):
                f_close = df.iloc[bar_idx + f_bars]['close']
                # 空單報酬: (entry_p - f_close) / entry_p
                ret = (entry['price'] - f_close) / entry['price'] * 100.0
                forward_returns[f"{f_bars}bars"] = ret
            else:
                forward_returns[f"{f_bars}bars"] = None
        
        # MFE / MAE 計算 (進場後至平倉或最多 15 根)
        sub_future = df.iloc[bar_idx:min(len(df), bar_idx + 16)]
        max_favorable = (entry['price'] - sub_future['low'].min()) / entry['price'] * 100.0
        max_adverse = (sub_future['high'].max() - entry['price']) / entry['price'] * 100.0
        
        print(f"後續 3 根報酬: {forward_returns.get('3bars', 0):+.2f}% | 5 根: {forward_returns.get('5bars', 0):+.2f}% | 10 根: {forward_returns.get('10bars', 0):+.2f}% | 15 根: {forward_returns.get('15bars', 0):+.2f}%")
        print(f"最大有利幅度 (MFE): +{max_favorable:.2f}% | 最大不利幅度 (MAE): -{max_adverse:.2f}%")
        
        # 列出前後 10 根 K 棒明細
        window_start = max(0, bar_idx - 10)
        window_end = min(len(df), bar_idx + 11)
        sub_window = df.iloc[window_start:window_end][['dt', 'open', 'high', 'low', 'close', 'ma5', 'kc_middle', 'kc_lower', 'ma5_slope', 'kc_mid_slope']]
        sub_window.to_csv(f"scratch/phase5_5_diagnosis/signal_{s_idx}_window.csv", index=False)
        print(f"已導出訊號前後視窗至 scratch/phase5_5_diagnosis/signal_{s_idx}_window.csv")

if __name__ == '__main__':
    main()
