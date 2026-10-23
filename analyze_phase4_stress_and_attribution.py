"""
檔案位置: analyze_phase4_stress_and_attribution.py
功能:
1. 成本敏感度雙軌驗證:
   - 測試 A (固定交易清單): 使用 1.0x 基準產生的固定交易名單 (全樣本 512 筆 / OOS 238 筆)，固定進出場與部位大小，僅重算手續費與滑價摩擦。Gross PF 嚴格恆定。
   - 測試 B (動態系統重跑): 讓成本參與動態資金管理、倉位規模與每日虧損停手機制，評估整個交易系統的連鎖反應。
2. 多空方向深度歸因 (LONG vs SHORT):
   - 多空各自的 Gross PF 與 Net PF (未計成本前是否已虧損)
   - 多空各自的平均停損距離 (R)、平均部位名義價值 (Notional)、平均摩擦成本
   - 空單微結構檢驗: 最大不利價格變動 (MAE in R)、停損棒影線幅度 (Wick Ratio)、15m ATR 波動分位數、跳空額外滑價 (Gap Slippage beyond SL)
3. 出場原因精確分流歸因:
   - 初始結構停損 (0% TP)
   - TP1後保本停損
   - TP2後尾倉移動停損
   - 同根觸碰保守撮合 (悲觀保本 / 悲觀移動停利)
   - 15m 宏觀反轉清倉
   - 期末強制結算
"""

import os
import sys
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_phase4_controlled_experiments import load_and_preprocess_data, simulate_strategy


def run_test_a_fixed_list(tdf_orig: pd.DataFrame, tag_prefix: str) -> pd.DataFrame:
    """
    測試 A: 固定交易清單重新定價摩擦成本。
    嚴格保持交易次數、進出場點位、毛損益完全不變，純粹分離手續費與滑價倍率的侵蝕效應。
    """
    results = []
    scenarios = [
        ("1.0x 基準摩擦 (雙邊0.20%)", 1.0),
        ("1.5x 壓力摩擦 (雙邊0.30%)", 1.5),
        ("2.0x 極限摩擦 (雙邊0.40%)", 2.0),
    ]

    total_gross_profit = tdf_orig[tdf_orig["realized_gross_pnl"] > 0]["realized_gross_pnl"].sum()
    total_gross_loss = abs(tdf_orig[tdf_orig["realized_gross_pnl"] < 0]["realized_gross_pnl"].sum())
    fixed_gross_pf = total_gross_profit / total_gross_loss if total_gross_loss > 0 else 0.0

    for name, mult in scenarios:
        tdf = tdf_orig.copy()
        tdf["adj_fees"] = tdf["total_fees"] * mult
        tdf["adj_slip"] = tdf["total_slippage"] * mult
        tdf["adj_net_pnl"] = tdf["realized_gross_pnl"] - tdf["adj_fees"] - tdf["adj_slip"]

        wins = tdf[tdf["adj_net_pnl"] > 0]
        losses = tdf[tdf["adj_net_pnl"] < 0]
        net_wins_sum = wins["adj_net_pnl"].sum()
        net_losses_sum = abs(losses["adj_net_pnl"].sum())
        net_pf = net_wins_sum / net_losses_sum if net_losses_sum > 0 else 0.0

        results.append({
            "test_type": "測試 A (固定交易清單)",
            "scenario": f"{tag_prefix} {name}",
            "total_trades": len(tdf),
            "win_trades": len(wins),
            "win_rate_pct": round(len(wins) / len(tdf) * 100.0, 2),
            "gross_profit_usd": round(total_gross_profit, 2),
            "gross_loss_usd": round(total_gross_loss, 2),
            "gross_pf": round(fixed_gross_pf, 3),
            "total_fees_usd": round(tdf["adj_fees"].sum(), 2),
            "total_slippage_usd": round(tdf["adj_slip"].sum(), 2),
            "total_cost_usd": round(tdf["adj_fees"].sum() + tdf["adj_slip"].sum(), 2),
            "net_pnl_usd": round(tdf["adj_net_pnl"].sum(), 2),
            "net_pf": round(net_pf, 3)
        })

    return pd.DataFrame(results)


def analyze_side_breakdown(tdf: pd.DataFrame) -> pd.DataFrame:
    """
    多空方向 (LONG vs SHORT) 獨立三組關鍵指標比較
    """
    rows = []
    for side in ["LONG", "SHORT"]:
        sub = tdf[tdf["side"] == side]
        if sub.empty:
            continue

        n_trades = len(sub)
        gross_prof = sub[sub["realized_gross_pnl"] > 0]["realized_gross_pnl"].sum()
        gross_loss = abs(sub[sub["realized_gross_pnl"] < 0]["realized_gross_pnl"].sum())
        gross_pf = gross_prof / gross_loss if gross_loss > 0 else 0.0

        wins = sub[sub["realized_pnl"] > 0]
        losses = sub[sub["realized_pnl"] < 0]
        net_prof = wins["realized_pnl"].sum()
        net_loss = abs(losses["realized_pnl"].sum())
        net_pf = net_prof / net_loss if net_loss > 0 else 0.0

        total_net_pnl = sub["realized_pnl"].sum()
        total_costs = sub["total_fees"].sum() + sub["total_slippage"].sum()

        avg_risk_dist = sub["r_distance"].mean()
        avg_risk_pct = (sub["r_distance"] / sub["entry_price"] * 100.0).mean()
        avg_notional = (sub["entry_price"] * sub["orig_qty"]).mean()
        avg_cost_per_trade = total_costs / n_trades if n_trades > 0 else 0.0
        avg_net_pnl = total_net_pnl / n_trades if n_trades > 0 else 0.0

        rows.append({
            "side": side,
            "trade_count": n_trades,
            "win_rate_pct": round(len(wins) / n_trades * 100.0, 2),
            "gross_profit_usd": round(gross_prof, 2),
            "gross_loss_usd": round(gross_loss, 2),
            "gross_pf": round(gross_pf, 3),
            "net_pf": round(net_pf, 3),
            "total_costs_usd": round(total_costs, 2),
            "net_pnl_usd": round(total_net_pnl, 2),
            "avg_net_pnl_usd": round(avg_net_pnl, 2),
            "avg_risk_dist_usd": round(avg_risk_dist, 4),
            "avg_risk_pct": round(avg_risk_pct, 2),
            "avg_notional_usd": round(avg_notional, 2),
            "avg_cost_usd": round(avg_cost_per_trade, 2)
        })

    return pd.DataFrame(rows)


def analyze_microstructure_and_mae(tdf: pd.DataFrame, df_1m: pd.DataFrame, df_15m: pd.DataFrame) -> dict:
    """
    空單 vs 多單微結構調查:
    1. MAE (Maximum Adverse Excursion in R)
    2. 停損棒影線幅度 (Wick vs Body)
    3. 進場時 15m ATR 分位數分佈
    4. 跳空超越停損價 (Gap beyond SL)
    """
    mae_dict = {"LONG": [], "SHORT": []}
    wick_ratio_stops = {"LONG": [], "SHORT": []}
    gap_sl_counts = {"LONG": 0, "SHORT": 0}
    atr15m_quantiles = {"LONG": [], "SHORT": []}

    atr15m_series = df_15m["atr"].dropna()

    for _, row in tdf.iterrows():
        side = row["side"]
        e_idx = int(row["entry_bar_idx"])
        x_idx = int(row["exit_bar_idx"]) if not np.isnan(row.get("exit_bar_idx", np.nan)) else e_idx
        e_price = row["entry_price"]
        r_dist = row["r_distance"]

        # 區間價格切片
        slice_high = df_1m["high"].iloc[e_idx:x_idx + 1]
        slice_low = df_1m["low"].iloc[e_idx:x_idx + 1]

        if side == "SHORT":
            max_high = slice_high.max()
            mae = max_high - e_price
            mae_r = mae / r_dist if r_dist > 0 else 0.0
            mae_dict["SHORT"].append(mae_r)

            # 停損棒影線檢查
            if "停損" in str(row["exit_reason"]):
                x_bar = df_1m.iloc[x_idx]
                body = abs(x_bar["close"] - x_bar["open"])
                upper_wick = x_bar["high"] - max(x_bar["open"], x_bar["close"])
                wick_ratio = upper_wick / (body + 1e-6)
                wick_ratio_stops["SHORT"].append(wick_ratio)

                # 是否跳空超越停損 (開盤價已破停損)
                if x_bar["open"] > row["current_sl"]:
                    gap_sl_counts["SHORT"] += 1

        else:  # LONG
            min_low = slice_low.min()
            mae = e_price - min_low
            mae_r = mae / r_dist if r_dist > 0 else 0.0
            mae_dict["LONG"].append(mae_r)

            if "停損" in str(row["exit_reason"]):
                x_bar = df_1m.iloc[x_idx]
                body = abs(x_bar["close"] - x_bar["open"])
                lower_wick = min(x_bar["open"], x_bar["close"]) - x_bar["low"]
                wick_ratio = lower_wick / (body + 1e-6)
                wick_ratio_stops["LONG"].append(wick_ratio)

                if x_bar["open"] < row["current_sl"]:
                    gap_sl_counts["LONG"] += 1

        # 15m ATR 分位數
        entry_time = pd.to_datetime(row["entry_time"], utc=True)
        if "datetime" in df_15m.columns:
            matched_15m = df_15m[pd.to_datetime(df_15m["datetime"], utc=True) <= entry_time]
            if not matched_15m.empty:
                cur_atr = matched_15m.iloc[-1]["atr"]
                if not np.isnan(cur_atr):
                    pct = (atr15m_series < cur_atr).mean() * 100.0
                    atr15m_quantiles[side].append(pct)

    summary = {
        "LONG_avg_mae_r": round(float(np.mean(mae_dict["LONG"])), 2) if mae_dict["LONG"] else 0.0,
        "SHORT_avg_mae_r": round(float(np.mean(mae_dict["SHORT"])), 2) if mae_dict["SHORT"] else 0.0,
        "LONG_stop_avg_wick_ratio": round(float(np.mean(wick_ratio_stops["LONG"])), 2) if wick_ratio_stops["LONG"] else 0.0,
        "SHORT_stop_avg_wick_ratio": round(float(np.mean(wick_ratio_stops["SHORT"])), 2) if wick_ratio_stops["SHORT"] else 0.0,
        "LONG_gap_beyond_sl_count": gap_sl_counts["LONG"],
        "SHORT_gap_beyond_sl_count": gap_sl_counts["SHORT"],
        "LONG_avg_atr15m_quantile": round(float(np.mean(atr15m_quantiles["LONG"])), 1) if atr15m_quantiles["LONG"] else 0.0,
        "SHORT_avg_atr15m_quantile": round(float(np.mean(atr15m_quantiles["SHORT"])), 1) if atr15m_quantiles["SHORT"] else 0.0,
    }
    return summary


def analyze_exit_reason_breakdown(tdf: pd.DataFrame) -> pd.DataFrame:
    """
    出場原因細分歸因 (涵蓋初始停損、保本停損、尾倉移動停損、保守撮合、宏觀反轉等)
    """
    tdf = tdf.copy()
    tdf["entry_dt"] = pd.to_datetime(tdf["entry_time"])
    tdf["exit_dt"] = pd.to_datetime(tdf["exit_time"])
    tdf["duration_min"] = (tdf["exit_dt"] - tdf["entry_dt"]).dt.total_seconds() / 60.0

    groups = []
    for reason, sub in tdf.groupby("exit_reason"):
        count = len(sub)
        wins = sub[sub["realized_pnl"] > 0]
        gross_pnl = sub["realized_gross_pnl"].sum()
        total_costs = sub["total_fees"].sum() + sub["total_slippage"].sum()
        net_pnl = sub["realized_pnl"].sum()
        avg_pnl = sub["realized_pnl"].mean()
        avg_dur = sub["duration_min"].mean()

        groups.append({
            "exit_reason": reason,
            "trade_count": count,
            "pct_of_total": round(count / len(tdf) * 100.0, 2),
            "win_count": len(wins),
            "win_rate_pct": round(len(wins) / count * 100.0, 2),
            "gross_pnl_usd": round(gross_pnl, 2),
            "costs_usd": round(total_costs, 2),
            "net_pnl_usd": round(net_pnl, 2),
            "avg_net_pnl_usd": round(avg_pnl, 2),
            "avg_duration_min": round(avg_dur, 1)
        })

    df_out = pd.DataFrame(groups).sort_values(by="trade_count", ascending=False)
    return df_out


def main():
    csv_path = "lobster_real_1m_history.csv"
    df_1m, df_15m, p_highs, p_lows = load_and_preprocess_data(csv_path)

    total_bars = len(df_1m)
    split_bar = 60000

    print("\n" + "=" * 90)
    print(" 🚀【雙軌成本壓力測試】組合 5 (E4: 2R/4R + VR >= 1.1)")
    print("=" * 90)

    # 1. 執行基準 1.0x 回測取得完整交易明細
    res_all_1x, tdf_all_1x = simulate_strategy(
        df_1m, df_15m, p_highs, p_lows,
        start_bar=50, end_bar=total_bars,
        tp1_r=2.0, tp2_r=4.0,
        vol_mode="vr", vol_threshold=1.1,
        taker_fee_rate=0.0005, slippage_rate=0.0005,
        tag="1.0x 基準 [全樣本]"
    )

    res_oos_1x, tdf_oos_1x = simulate_strategy(
        df_1m, df_15m, p_highs, p_lows,
        start_bar=split_bar, end_bar=total_bars,
        tp1_r=2.0, tp2_r=4.0,
        vol_mode="vr", vol_threshold=1.1,
        taker_fee_rate=0.0005, slippage_rate=0.0005,
        tag="1.0x 基準 [OOS 樣本外]"
    )

    # ── 測試 A: 固定交易清單重新定價 ──────────────────────────────
    print("\n--- 【測試 A: 固定交易清單 (Pure Cost Friction)】 ---")
    df_test_a_all = run_test_a_fixed_list(tdf_all_1x, "[全樣本 512筆]")
    df_test_a_oos = run_test_a_fixed_list(tdf_oos_1x, "[OOS 238筆]")
    df_test_a = pd.concat([df_test_a_all, df_test_a_oos], ignore_index=True)
    print(df_test_a.to_string(index=False))

    # ── 測試 B: 完整策略動態重跑 ──────────────────────────────────
    print("\n--- 【測試 B: 完整策略動態重跑 (Systemic Dynamic Rerun)】 ---")
    dynamic_results = [res_all_1x, res_oos_1x]
    for mult_name, fee, slip in [
        ("1.5x 壓力", 0.00075, 0.00075),
        ("2.0x 極限", 0.0010, 0.0010)
    ]:
        r_all, _ = simulate_strategy(
            df_1m, df_15m, p_highs, p_lows,
            start_bar=50, end_bar=total_bars,
            tp1_r=2.0, tp2_r=4.0,
            vol_mode="vr", vol_threshold=1.1,
            taker_fee_rate=fee, slippage_rate=slip,
            tag=f"{mult_name} [全樣本]"
        )
        r_oos, _ = simulate_strategy(
            df_1m, df_15m, p_highs, p_lows,
            start_bar=split_bar, end_bar=total_bars,
            tp1_r=2.0, tp2_r=4.0,
            vol_mode="vr", vol_threshold=1.1,
            taker_fee_rate=fee, slippage_rate=slip,
            tag=f"{mult_name} [OOS 樣本外]"
        )
        dynamic_results.extend([r_all, r_oos])

    df_test_b = pd.DataFrame(dynamic_results)
    b_cols = ["tag", "total_trades", "win_rate_pct", "gross_pf", "net_pf", "net_pnl_usd", "fees_slippage_usd", "max_equity_dd_pct", "final_equity_usd"]
    print(df_test_b[b_cols].to_string(index=False))

    # ── 分析 2: 多空方向 (LONG vs SHORT) 深度歸因 ───────────────────
    print("\n" + "=" * 90)
    print(" 🎯【多空方向深度歸因】全樣本 512 筆")
    print("=" * 90)
    df_side = analyze_side_breakdown(tdf_all_1x)
    print(df_side.to_string(index=False))

    print("\n--- 【多空微結構與 MAE / 影線調查】 ---")
    micro_stats = analyze_microstructure_and_mae(tdf_all_1x, df_1m, df_15m)
    for k, v in micro_stats.items():
        print(f"  {k}: {v}")

    # ── 分析 3: 出場原因細緻拆解 ──────────────────────────────────
    print("\n" + "=" * 90)
    print(" 📋【出場原因細緻拆解 (Refined Exit Attribution)】全樣本 512 筆")
    print("=" * 90)
    df_exit = analyze_exit_reason_breakdown(tdf_all_1x)
    print(df_exit.to_string(index=False))

    # 匯出 CSV 報表
    os.makedirs("scratch/phase5_8_ablation", exist_ok=True)
    df_test_a.to_csv("scratch/phase5_8_ablation/cost_stress_test_a_fixed_list.csv", index=False)
    df_test_b.to_csv("scratch/phase5_8_ablation/cost_stress_test_b_dynamic.csv", index=False)
    df_side.to_csv("scratch/phase5_8_ablation/side_detailed_attribution.csv", index=False)
    df_exit.to_csv("scratch/phase5_8_ablation/exit_reason_refined_attribution.csv", index=False)

    print("\n所有分析與 CSV 產出完畢。")


if __name__ == "__main__":
    main()
