#!/usr/bin/env python3
"""
tools/diagnose_holdout_long_trades.py
=====================================
Phase 5.7 第二階段：HOLDOUT 43 筆多單虧損根因逐筆深度診斷工具
以實際指標、K 線特徵、MFE/MAE 及交易紀錄為客觀證據，逐筆分類剖析。
"""

import os
import sys
sys.path.insert(0, os.path.abspath("."))
import json
import math
import numpy as np
import pandas as pd

from tools.run_phase5_5_short_entry_diagnosis import (
    precompute_indicators,
    build_snapshot,
    classify_market_regime,
    LEVERAGE,
    NOTIONAL,
    FEE_RATE,
    SLIPPAGE,
)

def main():
    print("=" * 80)
    print("🔬 Phase 5.7 多單逐筆虧損根因診斷 (HOLDOUT 43 筆多單)")
    print("=" * 80)

    csv_path = "scratch/lobster_real_1m_history.csv"
    raw_df = pd.read_csv(csv_path)
    df_slice = raw_df.iloc[-10000:].copy().reset_index(drop=True)
    df_calc = precompute_indicators(df_slice)

    # HOLDOUT 集為最後 2,500 根
    holdout_offset = 7500
    holdout_df = df_calc.iloc[holdout_offset:].copy().reset_index(drop=True)

    # 讀取 Phase 5.5 導出的 D 組交易
    trades_df = pd.read_csv("scratch/phase5_5_diagnosis/phase5_5_trades_comparison.csv")
    d_longs = trades_df[(trades_df['dataset'] == 'HOLDOUT (2,500 bars)') & 
                        (trades_df['group'] == 'D') & 
                        (trades_df['side'] == 'LONG')].copy().reset_index(drop=True)

    print(f"成功讀取 HOLDOUT D 組多單共 {len(d_longs)} 筆")

    analyzed_trades = []

    for idx, row in d_longs.iterrows():
        entry_idx = int(row['entry_time'])  # 相對於 holdout_df 的索引
        exit_idx = int(row['exit_time'])

        if entry_idx >= len(holdout_df):
            continue

        bar_in = holdout_df.iloc[entry_idx]
        bar_out = holdout_df.iloc[exit_idx] if exit_idx < len(holdout_df) else holdout_df.iloc[-1]

        atr = float(bar_in['atr'])
        p_entry = float(row['p_entry_exec'])
        p_exit = float(row['p_exit_exec'])
        pnl = float(row['net_pnl_usd'])
        hold_bars = int(row['hold_bars'])

        # 指標快照
        kc_mid = float(bar_in['kc_middle'])
        kc_up = float(bar_in['kc_upper'])
        kc_low = float(bar_in['kc_lower'])
        bw = float(bar_in['bandwidth'])
        bw_q25 = float(bar_in.get('bandwidth_q25', bw))
        ma5 = float(bar_in['ma5'])
        ma15 = float(bar_in['ma15'])
        ema_fast = float(bar_in['ema_fast'])
        ema_slow = float(bar_in['ema_slow'])
        ma_spread = float(bar_in['ma_spread_pct'])

        kc_slope = float(bar_in['kc_middle_slope_1'])
        ma5_slope = float(bar_in['ma5_slope_1'])
        slow_slope = float(bar_in['slow_slope_10'])
        macro_slope = float(bar_in['macro_slope_10'])

        macro_trend = "BULLISH" if macro_slope > 0.00001 and slow_slope > 0 else (
            "BEARISH" if macro_slope < -0.00001 and slow_slope < 0 else "NEUTRAL"
        )

        dist_mid_atr = (bar_in['close'] - kc_mid) / atr
        dist_up_atr = (bar_in['close'] - kc_up) / atr

        # MFE / MAE
        mfe_usd = float(row.get('mfe_usd', 0.0))
        mae_usd = float(row.get('mae_usd', 0.0))
        mfe_atr = (float(row.get('mfe_raw', 0.0))) / atr
        mae_atr = (float(row.get('mae_raw', 0.0))) / atr

        # 根因客觀判定 (基於指標證據，非事後諸葛)
        root_cause = "UNCLASSIFIABLE"
        evidence = ""

        # 1. 逆大趨勢做多 (大趨勢明確偏空或慢速線向下)
        if macro_trend == "BEARISH" or slow_slope < -0.000005:
            root_cause = "COUNTER_TREND_BEARISH_MACRO"
            evidence = f"大趨勢偏空(macro={macro_trend}, slow_slope={slow_slope:+.7f})"

        # 2. 上軌天花板過度延伸追多
        elif dist_up_atr > 0.4:
            root_cause = "OVER_EXTENDED_UPPER_RAIL"
            evidence = f"價格超出上軌過多(dist_up={dist_up_atr:.2f} ATR > 0.4)"

        # 3. 窄幅震盪死水假突破
        elif bw <= bw_q25 and ma_spread < 0.15:
            root_cause = "CHOP_LOW_BANDWIDTH"
            evidence = f"帶寬處於低位(bw={bw:.4f} <= q25={bw_q25:.4f}, spread={ma_spread:.2f}%)"

        # 4. 回踩均線缺乏推升動能 (小實體或上影線過長)
        elif bar_in.get('body_size', 0.0) < 0.35 * atr or bar_in.get('upper_wick_ratio', 0.0) > 0.45:
            root_cause = "PULLBACK_WEAK_MOMENTUM"
            evidence = f"K棒動能不足(body={bar_in.get('body_size',0)/atr:.2f}ATR, upper_wick={bar_in.get('upper_wick_ratio',0):.2f})"

        # 5. 曾大幅獲利但未及時落袋轉虧
        elif mfe_atr >= 1.5 and pnl <= 0:
            root_cause = "PROFIT_TURNED_LOSS_UNLOCKED"
            evidence = f"浮盈曾達{mfe_atr:.2f}ATR但未及時停利"

        # 6. 順勢正常虧損或無法確定
        else:
            if pnl > 0:
                root_cause = "PROFITABLE_TRADE"
                evidence = f"順勢獲利平倉 (ROE: {pnl:+.2f}U)"
            else:
                root_cause = "NORMAL_PULLBACK_STOP"
                evidence = f"正常試錯止損 (ROE: {pnl:+.2f}U)"

        analyzed_trades.append({
            "trade_id": idx + 1,
            "entry_bar": entry_idx,
            "exit_bar": exit_idx,
            "entry_time": bar_in.get('timestamp', entry_idx),
            "exit_time": bar_out.get('timestamp', exit_idx),
            "side": "LONG",
            "entry_price": p_entry,
            "exit_price": p_exit,
            "hold_bars": hold_bars,
            "pnl_usd": pnl,
            "entry_reason": row.get('entry_mode', 'UNKNOWN'),
            "exit_reason": row.get('exit_reason', 'UNKNOWN'),
            "macro_trend": macro_trend,
            "slow_slope": slow_slope,
            "kc_mid_slope": kc_slope,
            "bandwidth": bw,
            "dist_mid_atr": round(dist_mid_atr, 2),
            "dist_up_atr": round(dist_up_atr, 2),
            "mfe_atr": round(mfe_atr, 2),
            "mae_atr": round(mae_atr, 2),
            "root_cause": root_cause,
            "evidence": evidence,
        })

    df_res = pd.DataFrame(analyzed_trades)
    df_res.to_csv("scratch/phase5_5_diagnosis/holdout_long_43trades_diagnosis.csv", index=False)
    print("✅ 43 筆多單逐筆明細已導出至 scratch/phase5_5_diagnosis/holdout_long_43trades_diagnosis.csv")

    # 匯總各根因之筆數與虧損金額
    print("\n" + "=" * 80)
    print("【HOLDOUT 43 筆多單根因歸類與損益統計】")
    print("=" * 80)
    summary = df_res.groupby('root_cause').agg(
        trade_count=('trade_id', 'count'),
        total_pnl=('pnl_usd', 'sum'),
        avg_pnl=('pnl_usd', 'mean'),
        win_count=('pnl_usd', lambda x: (x > 0).sum()),
    )
    summary['win_rate'] = (summary['win_count'] / summary['trade_count'] * 100).round(1)
    summary['total_pnl'] = summary['total_pnl'].round(2)
    summary['avg_pnl'] = summary['avg_pnl'].round(2)
    print(summary.to_string())

    with open("scratch/phase5_5_diagnosis/holdout_long_root_causes.json", "w") as f:
        json.dump(summary.to_dict(orient="index"), f, indent=2)

if __name__ == "__main__":
    main()
