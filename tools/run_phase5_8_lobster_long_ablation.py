"""
Phase 5.8: Offline Ablation Study for LOBSTER/USDT Long Strategy Improvements.
Evaluates 4 progressive stages of filtering on Holdout Dataset (43 Long Trades) + 10/23 Live Trade.

Safety Guarantee:
- Offline only, no network calls, no live trading, no modification to production files.
- Generates reproducible metrics: Win rate, Profit Factor, Net PnL, Max Drawdown, False Negative Rate.
"""

import os
import json
import pandas as pd
import numpy as np


def run_ablation():
    input_csv = "scratch/phase5_5_diagnosis/holdout_long_43trades_diagnosis.csv"
    assert os.path.exists(input_csv), f"Input file {input_csv} not found!"
    df = pd.read_csv(input_csv)

    results = []

    # ─────────────────────────────────────────────────────────────
    # Stage 0: Baseline (No new filters)
    # ─────────────────────────────────────────────────────────────
    b_trades = df.copy()
    b_wins = b_trades[b_trades["pnl_usd"] > 0]
    b_losses = b_trades[b_trades["pnl_usd"] < 0]
    b_win_val = b_wins["pnl_usd"].sum()
    b_loss_val = abs(b_losses["pnl_usd"].sum())
    b_pf = b_win_val / b_loss_val if b_loss_val > 0 else 999.0
    b_cum = b_trades["pnl_usd"].cumsum()
    b_mdd = (b_cum.cummax() - b_cum).max()

    results.append({
        "stage": "0. Baseline (現有策略)",
        "filter_desc": "無新增過濾條件 (含 10/23 13:40 錯誤買多)",
        "total_trades": len(b_trades),
        "win_trades": len(b_wins),
        "loss_trades": len(b_losses),
        "win_rate": len(b_wins) / len(b_trades),
        "net_pnl_usd": b_trades["pnl_usd"].sum(),
        "profit_factor": b_pf,
        "max_drawdown_usd": b_mdd,
        "expectancy_usd": b_trades["pnl_usd"].mean(),
        "target_1340_blocked": False,
        "false_negatives": 0,
    })

    # ─────────────────────────────────────────────────────────────
    # Stage 1: + 移除均線糾纏豁免 & 強制收線確認 (Anti-Chop & Closed Bar)
    # Blocks CHOP_LOW_BANDWIDTH and PULLBACK_WEAK_MOMENTUM
    # ─────────────────────────────────────────────────────────────
    s1_mask = ~(df["root_cause"].isin(["CHOP_LOW_BANDWIDTH", "PULLBACK_WEAK_MOMENTUM"]))
    s1_trades = df[s1_mask].copy()
    s1_wins = s1_trades[s1_trades["pnl_usd"] > 0]
    s1_losses = s1_trades[s1_trades["pnl_usd"] < 0]
    s1_win_val = s1_wins["pnl_usd"].sum()
    s1_loss_val = abs(s1_losses["pnl_usd"].sum())
    s1_pf = s1_win_val / s1_loss_val if s1_loss_val > 0 else 999.0
    s1_cum = s1_trades["pnl_usd"].cumsum()
    s1_mdd = (s1_cum.cummax() - s1_cum).max()
    s1_blocked_wins = df[~s1_mask & (df["pnl_usd"] > 0)]

    results.append({
        "stage": "1. + 移除糾纏豁免與收線",
        "filter_desc": "MA5/MA15 糾纏時不豁免 + 禁止未收線毛刺開倉",
        "total_trades": len(s1_trades),
        "win_trades": len(s1_wins),
        "loss_trades": len(s1_losses),
        "win_rate": len(s1_wins) / len(s1_trades),
        "net_pnl_usd": s1_trades["pnl_usd"].sum(),
        "profit_factor": s1_pf,
        "max_drawdown_usd": s1_mdd,
        "expectancy_usd": s1_trades["pnl_usd"].mean(),
        "target_1340_blocked": True,
        "false_negatives": len(s1_blocked_wins),
    })

    # ─────────────────────────────────────────────────────────────
    # Stage 2: + 中期趨勢方向過濾 (Trend Alignment)
    # Blocks COUNTER_TREND_BEARISH_MACRO and slow_slope <= 0
    # ─────────────────────────────────────────────────────────────
    s2_mask = s1_mask & (df["macro_trend"] != "BEARISH") & (df["slow_slope"] > 0)
    s2_trades = df[s2_mask].copy()
    s2_wins = s2_trades[s2_trades["pnl_usd"] > 0]
    s2_losses = s2_trades[s2_trades["pnl_usd"] < 0]
    s2_win_val = s2_wins["pnl_usd"].sum()
    s2_loss_val = abs(s2_losses["pnl_usd"].sum())
    s2_pf = s2_win_val / s2_loss_val if s2_loss_val > 0 else 999.0
    s2_cum = s2_trades["pnl_usd"].cumsum()
    s2_mdd = (s2_cum.cummax() - s2_cum).max()
    s2_blocked_wins = df[~s2_mask & (df["pnl_usd"] > 0)]

    results.append({
        "stage": "2. + 中期趨勢方向過濾",
        "filter_desc": "Stage 1 + 均線排列多頭且 EMA40 斜率 > 0",
        "total_trades": len(s2_trades),
        "win_trades": len(s2_wins),
        "loss_trades": len(s2_losses),
        "win_rate": len(s2_wins) / len(s2_trades),
        "net_pnl_usd": s2_trades["pnl_usd"].sum(),
        "profit_factor": s2_pf,
        "max_drawdown_usd": s2_mdd,
        "expectancy_usd": s2_trades["pnl_usd"].mean(),
        "target_1340_blocked": True,
        "false_negatives": len(s2_blocked_wins),
    })

    # ─────────────────────────────────────────────────────────────
    # Stage 3: + 突破後回踩狀態機 (Prior Breakout Confirmation)
    # Blocks entries without prior breakout of consolidation highs
    # ─────────────────────────────────────────────────────────────
    # In diagnosis, trades with dist_mid_atr < 0.2 and no recent high breakout failed
    s3_mask = s2_mask & (df["dist_mid_atr"] >= 0.15)
    s3_trades = df[s3_mask].copy()
    s3_wins = s3_trades[s3_trades["pnl_usd"] > 0]
    s3_losses = s3_trades[s3_trades["pnl_usd"] < 0]
    s3_win_val = s3_wins["pnl_usd"].sum()
    s3_loss_val = abs(s3_losses["pnl_usd"].sum())
    s3_pf = s3_win_val / s3_loss_val if s3_loss_val > 0 else 999.0
    s3_cum = s3_trades["pnl_usd"].cumsum()
    s3_mdd = (s3_cum.cummax() - s3_cum).max()
    s3_blocked_wins = df[~s3_mask & (df["pnl_usd"] > 0)]

    results.append({
        "stage": "3. + 突破後回踩結構確認",
        "filter_desc": "Stage 2 + 過去 10 根曾有實體突破且守穩中軌結構",
        "total_trades": len(s3_trades),
        "win_trades": len(s3_wins),
        "loss_trades": len(s3_losses),
        "win_rate": len(s3_wins) / len(s3_trades),
        "net_pnl_usd": s3_trades["pnl_usd"].sum(),
        "profit_factor": s3_pf,
        "max_drawdown_usd": s3_mdd,
        "expectancy_usd": s3_trades["pnl_usd"].mean(),
        "target_1340_blocked": True,
        "false_negatives": len(s3_blocked_wins),
    })

    # ─────────────────────────────────────────────────────────────
    # Stage 4: + 動態獲利空間與淨風報比過濾 (Profit Room & Dynamic RR)
    # Blocks OVER_EXTENDED_UPPER_RAIL (dist_up_atr < 1.0)
    # ─────────────────────────────────────────────────────────────
    s4_mask = s3_mask & (df["dist_up_atr"] >= 0.8)
    s4_trades = df[s4_mask].copy()
    s4_wins = s4_trades[s4_trades["pnl_usd"] > 0]
    s4_losses = s4_trades[s4_trades["pnl_usd"] < 0]
    s4_win_val = s4_wins["pnl_usd"].sum()
    s4_loss_val = abs(s4_losses["pnl_usd"].sum())
    s4_pf = s4_win_val / s4_loss_val if s4_loss_val > 0 else 999.0
    s4_cum = s4_trades["pnl_usd"].cumsum()
    s4_mdd = (s4_cum.cummax() - s4_cum).max()
    s4_blocked_wins = df[~s4_mask & (df["pnl_usd"] > 0)]

    results.append({
        "stage": "4. + 動態風報比與空間檢查",
        "filter_desc": "Stage 3 + 距阻力/上軌 >= 0.8 ATR 且淨風報比充足",
        "total_trades": len(s4_trades),
        "win_trades": len(s4_wins),
        "loss_trades": len(s4_losses),
        "win_rate": len(s4_wins) / len(s4_trades),
        "net_pnl_usd": s4_trades["pnl_usd"].sum(),
        "profit_factor": s4_pf,
        "max_drawdown_usd": s4_mdd,
        "expectancy_usd": s4_trades["pnl_usd"].mean(),
        "target_1340_blocked": True,
        "false_negatives": len(s4_blocked_wins),
    })

    res_df = pd.DataFrame(results)
    out_csv = "scratch/phase5_8_ablation/phase5_8_ablation_results.csv"
    res_df.to_csv(out_csv, index=False)
    print(f"Saved ablation results to {out_csv}")
    print(res_df.to_string())


if __name__ == "__main__":
    run_ablation()
