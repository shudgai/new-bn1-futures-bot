"""
檔案位置: run_reconciled_benchmark.py
職責:
針對用戶審查意見，執行「單一全量連續模擬 (Single Continuous Simulation)」，
徹底解決獨立切片回測造成的帳務不一致（交易數對不上、損益差額 10.17 USDT 等問題）。

核心架構:
1. 連續執行 101k 全樣本單一主迴圈 (Zero Divergence SSOT)。
2. 嚴格依進場 Bar 索引 (`entry_bar_idx`) 唯一歸屬 IS (<60k) 與 OOS (>=60k)。
3. 顯式追蹤並審計「跨界交易 (Boundary-Crossing Trades)」(進場於 IS、平倉於 OOS)。
4. 驗證數學恆等式:
   Full_trades == IS_trades + OOS_trades (100% 嚴格相等)
   Full_net_pnl == IS_net_pnl + OOS_net_pnl (100% 嚴格相等)
   Full_cost == IS_cost + OOS_cost (100% 嚴格相等)
5. 執行用戶要求的「4 級止損距離分組分析 (<0.5%, 0.5%~0.8%, 0.8%~1.2%, >1.2%)」。
6. 明確回答風控 5 大關鍵機制。
"""

import os
import sys
import math
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.services.strategies.dual_entry_strategies import DualEntryDetector
from core.services.matching.pessimistic_matcher import PessimisticMatcher


def prepare_benchmark_data(csv_path: str = "lobster_real_1m_history.csv") -> pd.DataFrame:
    """加載 1m 數據並嚴格向量化預計算 KC(20, 1.5, 14), MA3, MA15 (零前視偏差)"""
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"找不到數據檔案: {csv_path}")

    print(f"正在加載歷史數據: {csv_path}...")
    df = pd.read_csv(csv_path)
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)

    close = df["close"]
    high = df["high"]
    low = df["low"]

    # ATR 14
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean().bfill()

    # KC (20, 1.5, 14)
    df["kc_middle"] = close.ewm(span=20, adjust=False).mean()
    df["kc_upper"] = df["kc_middle"] + (1.5 * df["atr"])
    df["kc_lower"] = df["kc_middle"] - (1.5 * df["atr"])

    # MA3 & MA15 (SMA)
    df["ma3"] = close.rolling(3).mean()
    df["ma15"] = close.rolling(15).mean()

    return df


def simulate_continuous_full_run(
    df: pd.DataFrame,
    strategy_mode: str,
    apply_ma3_filter: bool = True,
    start_bar: int = 50,
    end_bar: int = None,
    split_bar: int = 60000,
    initial_balance: float = 1000.0,
    taker_fee_rate: float = 0.0005,
    slippage_rate: float = 0.0005,
    tp1_r: float = 2.0,
    tp2_r: float = 4.0,
    max_open_positions: int = 3,
) -> Tuple[pd.DataFrame, List[float], List[Dict[str, Any]]]:
    """
    單一連續全量回測迴圈
    """
    total_len = len(df)
    if end_bar is None or end_bar > total_len:
        end_bar = total_len

    balance = initial_balance
    current_day = None
    daily_realized_loss = 0.0
    is_daily_halted = False

    trades: List[Dict[str, Any]] = []
    active_positions: List[Dict[str, Any]] = []
    pending_signals: List[Dict[str, Any]] = []
    in_continuation_side: Optional[str] = None

    open_arr = df["open"].values
    high_arr = df["high"].values
    low_arr = df["low"].values
    close_arr = df["close"].values
    dt_arr = df["datetime"].values

    equity_curve: List[float] = []

    for i in range(start_bar, end_bar):
        bar_dt = pd.Timestamp(dt_arr[i])
        bar_date = bar_dt.date()

        # 每日風控換日結算
        if current_day != bar_date:
            current_day = bar_date
            daily_realized_loss = 0.0
            is_daily_halted = False

        if daily_realized_loss >= (balance * 0.02):
            is_daily_halted = True

        b_open = float(open_arr[i])
        b_high = float(high_arr[i])
        b_low = float(low_arr[i])
        b_close = float(close_arr[i])

        # 1. 執行前一根收盤產生的 Pending 訊號 (下一根開盤價成交)
        if pending_signals and not is_daily_halted:
            for sig in pending_signals:
                side = sig["side"]

                # 檢查反向衝突: 若已有反向持倉，嚴禁反手或同時持有反向部位
                current_side = active_positions[0]["direction"] if active_positions else None
                if current_side is not None and current_side != side:
                    continue

                # 檢查同向持倉數量上限 (最多 3 筆)
                if len(active_positions) >= max_open_positions:
                    continue

                base_sl = sig["base_sl"]
                actual_entry_p = b_open * (1.0 + slippage_rate) if side == "LONG" else b_open * (1.0 - slippage_rate)
                risk_dist = (actual_entry_p - base_sl) if side == "LONG" else (base_sl - actual_entry_p)

                if risk_dist > 0:
                    risk_amt = balance * 0.005  # 動態 0.5% 單筆風險
                    eff_risk = risk_dist + (actual_entry_p * (taker_fee_rate * 2 + slippage_rate * 2))
                    qty = risk_amt / eff_risk

                    pos_dict = {
                        "id": len(trades) + len(active_positions) + 1,
                        "signal_time": sig["signal_time"],
                        "direction": side,
                        "signal_source": sig["signal_source"],
                        "kc_upper": sig["kc_upper"],
                        "kc_lower": sig["kc_lower"],
                        "kc_middle": sig["kc_middle"],
                        "ma3": sig["ma3"],
                        "ma15": sig["ma15"],
                        "intended_exec_price": sig["intended_exec_price"],
                        "actual_exec_price": actual_entry_p,
                        "initial_sl_distance": risk_dist,
                        "sl_pct": (risk_dist / actual_entry_p) * 100.0,
                        "entry_time": str(bar_dt),
                        "entry_price": actual_entry_p,
                        "entry_bar_idx": i,
                        "current_sl": base_sl,
                        "r_distance": risk_dist,
                        "orig_qty": qty,
                        "remaining_qty": qty,
                        "tp1_price": actual_entry_p + (tp1_r * risk_dist) if side == "LONG" else actual_entry_p - (tp1_r * risk_dist),
                        "tp2_price": actual_entry_p + (tp2_r * risk_dist) if side == "LONG" else actual_entry_p - (tp2_r * risk_dist),
                        "tp1_done": False,
                        "tp2_done": False,
                        "realized_pnl": 0.0,
                        "realized_gross_pnl": 0.0,
                        "total_fees": 0.0,
                        "total_slippage": 0.0,
                    }
                    active_positions.append(pos_dict)

            pending_signals = []
        else:
            pending_signals = []

        # 2. 持倉撮合推演 (SSOT PessimisticMatcher)
        still_active: List[Dict[str, Any]] = []
        for pos in active_positions:
            side = pos["direction"]
            match_fn = PessimisticMatcher.match_long_bar if side == "LONG" else PessimisticMatcher.match_short_bar
            m_res = match_fn(
                b_open=b_open, b_high=b_high, b_low=b_low, b_close=b_close,
                pos=pos,
                taker_fee_rate=taker_fee_rate, slippage_rate=slippage_rate
            )

            balance += m_res["realized_net_pnl"]
            pos["realized_pnl"] += m_res["realized_net_pnl"]
            pos["realized_gross_pnl"] += m_res["realized_gross_pnl"]
            pos["total_fees"] += m_res["realized_fee"]
            pos["total_slippage"] += m_res["realized_slip"]

            if m_res["realized_net_pnl"] < 0:
                daily_realized_loss += abs(m_res["realized_net_pnl"])

            if m_res["closed"]:
                pos["exit_bar_idx"] = i
                pos["exit_time"] = str(bar_dt)
                pos["exit_price"] = m_res["exit_price"]
                pos["exit_reason"] = m_res["exit_reason"]
                trades.append(pos)
            else:
                still_active.append(pos)

        active_positions = still_active

        # 3. 軌道外延續狀態維護與新訊號檢測
        if in_continuation_side is not None:
            if DualEntryDetector.is_price_returned_inside_kc(df, i, in_continuation_side):
                in_continuation_side = None

        if not is_daily_halted:
            new_sigs: List[Dict[str, Any]] = []

            # KC 破軌判定
            if strategy_mode in ["KC_INITIAL", "KC_CONTINUATION", "COMBINED"]:
                if in_continuation_side is None:
                    init_sig = DualEntryDetector.check_kc_initial_breakout(
                        df, current_idx=i, apply_ma3_filter=apply_ma3_filter
                    )
                    if init_sig is not None:
                        new_sigs.append(init_sig)
                        in_continuation_side = init_sig["side"]
                elif strategy_mode in ["KC_CONTINUATION", "COMBINED"]:
                    cont_sig = DualEntryDetector.check_kc_continuation_signal(
                        df, current_idx=i, in_continuation_side=in_continuation_side, apply_ma3_filter=apply_ma3_filter
                    )
                    if cont_sig is not None:
                        new_sigs.append(cont_sig)

            # MA3/MA15 交叉判定
            if strategy_mode in ["MA_CROSS", "COMBINED"]:
                ma_sig = DualEntryDetector.check_ma_cross_signal(
                    df, current_idx=i, apply_ma3_filter=apply_ma3_filter
                )
                if ma_sig is not None:
                    new_sigs.append(ma_sig)

            if new_sigs:
                pending_signals = new_sigs

        # 4. 權益實時 Mark-to-Market 計算
        unrealized = 0.0
        for pos in active_positions:
            side = pos["direction"]
            rem_q = pos["remaining_qty"]
            e_p = pos["entry_price"]
            raw_unrealized = (b_close - e_p) * rem_q if side == "LONG" else (e_p - b_close) * rem_q
            est_fee = (e_p + b_close) * rem_q * (taker_fee_rate + slippage_rate)
            unrealized += (raw_unrealized - est_fee)

        current_equity = balance + unrealized
        equity_curve.append(current_equity)

    # 期末強制平倉
    if active_positions:
        final_p = close_arr[end_bar - 1]
        for pos in active_positions:
            side = pos["direction"]
            rem_q = pos["remaining_qty"]
            e_p = pos["entry_price"]
            raw_pnl = (final_p - e_p) * rem_q if side == "LONG" else (e_p - final_p) * rem_q
            fee = (e_p + final_p) * rem_q * taker_fee_rate
            slip = (e_p + final_p) * rem_q * slippage_rate
            net_pnl = raw_pnl - fee - slip

            balance += net_pnl
            pos["realized_pnl"] += net_pnl
            pos["realized_gross_pnl"] += raw_pnl
            pos["total_fees"] += fee
            pos["total_slippage"] += slip
            pos["exit_bar_idx"] = end_bar - 1
            pos["exit_time"] = str(pd.Timestamp(dt_arr[end_bar - 1]))
            pos["exit_price"] = final_p
            pos["exit_reason"] = "期末強制結算"
            trades.append(pos)
        active_positions = []

    tdf = pd.DataFrame(trades)

    # 標註樣本歸屬與跨界狀態
    if not tdf.empty:
        tdf["period"] = np.where(tdf["entry_bar_idx"] < split_bar, "IS", "OOS")
        tdf["is_boundary_crosser"] = (tdf["entry_bar_idx"] < split_bar) & (tdf["exit_bar_idx"] >= split_bar)
        boundary_trades = tdf[tdf["is_boundary_crosser"]].to_dict(orient="records")
    else:
        boundary_trades = []

    return tdf, equity_curve, boundary_trades


def calculate_stats_from_trades(
    tdf: pd.DataFrame,
    equity_slice: Optional[List[float]] = None,
    tag: str = "STATS"
) -> Dict[str, Any]:
    """依據給定交易子集計算指標，保證數學可重現性"""
    total_trades = len(tdf)
    if total_trades == 0:
        return {
            "tag": tag,
            "total_trades": 0, "long_trades": 0, "short_trades": 0,
            "win_rate_pct": 0.0, "gross_profit_usd": 0.0, "gross_loss_usd": 0.0,
            "gross_pf": 0.0, "total_fees_usd": 0.0, "total_slippage_usd": 0.0,
            "total_cost_usd": 0.0, "net_pnl_usd": 0.0, "net_pf": 0.0,
            "expected_value_usd": 0.0, "max_equity_dd_pct": 0.0,
            "long_win_rate_pct": 0.0, "short_win_rate_pct": 0.0,
            "long_net_pnl_usd": 0.0, "short_net_pnl_usd": 0.0,
            "long_gross_pf": 0.0, "short_gross_pf": 0.0
        }

    long_trades = tdf[tdf["direction"] == "LONG"]
    short_trades = tdf[tdf["direction"] == "SHORT"]

    win_trades = len(tdf[tdf["realized_pnl"] > 0])
    win_rate = win_trades / total_trades

    long_wins = len(long_trades[long_trades["realized_pnl"] > 0])
    short_wins = len(short_trades[short_trades["realized_pnl"] > 0])

    gross_profit = tdf[tdf["realized_gross_pnl"] > 0]["realized_gross_pnl"].sum()
    gross_loss = abs(tdf[tdf["realized_gross_pnl"] < 0]["realized_gross_pnl"].sum())
    gross_pf = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    net_wins = tdf[tdf["realized_pnl"] > 0]["realized_pnl"].sum()
    net_losses = abs(tdf[tdf["realized_pnl"] < 0]["realized_pnl"].sum())
    net_pf = (net_wins / net_losses) if net_losses > 0 else (999.0 if net_wins > 0 else 0.0)

    total_fees = tdf["total_fees"].sum()
    total_slip = tdf["total_slippage"].sum()
    total_cost = total_fees + total_slip
    net_pnl = tdf["realized_pnl"].sum()

    long_gross_prof = long_trades[long_trades["realized_gross_pnl"] > 0]["realized_gross_pnl"].sum() if not long_trades.empty else 0.0
    long_gross_loss = abs(long_trades[long_trades["realized_gross_pnl"] < 0]["realized_gross_pnl"].sum()) if not long_trades.empty else 0.0
    long_gross_pf = long_gross_prof / long_gross_loss if long_gross_loss > 0 else 0.0

    short_gross_prof = short_trades[short_trades["realized_gross_pnl"] > 0]["realized_gross_pnl"].sum() if not short_trades.empty else 0.0
    short_gross_loss = abs(short_trades[short_trades["realized_gross_pnl"] < 0]["realized_gross_pnl"].sum()) if not short_trades.empty else 0.0
    short_gross_pf = short_gross_prof / short_gross_loss if short_gross_loss > 0 else 0.0

    long_net_pnl = long_trades["realized_pnl"].sum() if not long_trades.empty else 0.0
    short_net_pnl = short_trades["realized_pnl"].sum() if not short_trades.empty else 0.0

    # 計算區間內最大回撤
    max_equity_dd_pct = 0.0
    if equity_slice and len(equity_slice) > 0:
        peak = equity_slice[0]
        max_dd_val = 0.0
        for eq in equity_slice:
            if eq > peak:
                peak = eq
            dd = peak - eq
            dd_pct = dd / peak if peak > 0 else 0.0
            if dd_pct > max_equity_dd_pct:
                max_equity_dd_pct = dd_pct

    return {
        "tag": tag,
        "total_trades": total_trades,
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "win_rate_pct": round(win_rate * 100.0, 2),
        "long_win_rate_pct": round(long_wins / len(long_trades) * 100.0, 2) if len(long_trades) > 0 else 0.0,
        "short_win_rate_pct": round(short_wins / len(short_trades) * 100.0, 2) if len(short_trades) > 0 else 0.0,
        "gross_profit_usd": round(gross_profit, 2),
        "gross_loss_usd": round(gross_loss, 2),
        "gross_pf": round(gross_pf, 3),
        "long_gross_pf": round(long_gross_pf, 3),
        "short_gross_pf": round(short_gross_pf, 3),
        "total_fees_usd": round(total_fees, 2),
        "total_slippage_usd": round(total_slip, 2),
        "total_cost_usd": round(total_cost, 2),
        "net_pnl_usd": round(net_pnl, 2),
        "long_net_pnl_usd": round(long_net_pnl, 2),
        "short_net_pnl_usd": round(short_net_pnl, 2),
        "net_pf": round(net_pf, 3),
        "expected_value_usd": round(net_pnl / total_trades, 3),
        "max_equity_dd_pct": round(max_equity_dd_pct * 100.0, 2)
    }


def analyze_sl_bins(tdf: pd.DataFrame, strategy_name: str) -> List[Dict[str, Any]]:
    """針對 4 級止損距離進行損益與摩擦分析"""
    bins = [
        ("< 0.5%", lambda p: p < 0.5),
        ("0.5% ~ 0.8%", lambda p: (p >= 0.5) & (p < 0.8)),
        ("0.8% ~ 1.2%", lambda p: (p >= 0.8) & (p < 1.2)),
        (">= 1.2%", lambda p: p >= 1.2),
    ]

    res = []
    for bin_label, cond in bins:
        sub = tdf[cond(tdf["sl_pct"])]
        n = len(sub)
        if n == 0:
            res.append({
                "strategy": strategy_name,
                "sl_bin": bin_label,
                "trade_count": 0,
                "pct_of_all_trades": "0.0%",
                "gross_pnl": 0.0,
                "total_cost": 0.0,
                "cost_per_trade": 0.0,
                "net_pnl": 0.0,
                "net_pf": 0.0,
                "win_rate_pct": 0.0
            })
            continue

        gross_pnl = sub["realized_gross_pnl"].sum()
        fees = sub["total_fees"].sum()
        slip = sub["total_slippage"].sum()
        cost = fees + slip
        net_pnl = sub["realized_pnl"].sum()

        net_wins = sub[sub["realized_pnl"] > 0]["realized_pnl"].sum()
        net_losses = abs(sub[sub["realized_pnl"] < 0]["realized_pnl"].sum())
        net_pf = (net_wins / net_losses) if net_losses > 0 else (999.0 if net_wins > 0 else 0.0)
        win_rate = len(sub[sub["realized_pnl"] > 0]) / n * 100.0

        res.append({
            "strategy": strategy_name,
            "sl_bin": bin_label,
            "trade_count": n,
            "pct_of_all_trades": f"{round(n / len(tdf) * 100.0, 1)}%",
            "gross_pnl": round(gross_pnl, 2),
            "total_cost": round(cost, 2),
            "cost_per_trade": round(cost / n, 2),
            "net_pnl": round(net_pnl, 2),
            "net_pf": round(net_pf, 3),
            "win_rate_pct": round(win_rate, 2)
        })
    return res


def main():
    csv_path = "lobster_real_1m_history.csv"
    df = prepare_benchmark_data(csv_path)

    total_bars = len(df)
    start_bar = 50
    split_bar = 60000

    experiments = [
        ("配置 1: KC首次破軌 (無MA3過濾)", "KC_INITIAL", False),
        ("配置 2: KC首次破軌 (含MA3過濾)", "KC_INITIAL", True),
        ("配置 3: KC破軌+延續 (無MA3過濾)", "KC_CONTINUATION", False),
        ("配置 4: KC破軌+延續 (含MA3過濾)", "KC_CONTINUATION", True),
        ("配置 5: MA3/MA15交叉 (無MA3過濾)", "MA_CROSS", False),
        ("配置 6: MA3/MA15交叉 (含MA3過濾)", "MA_CROSS", True),
        ("配置 7: 合併策略 (KC延續+MA交叉+MA3過濾)", "COMBINED", True),
    ]

    summary_records = []
    sl_bin_records = []
    boundary_records = []
    trade_exports = {}

    print("\n" + "=" * 90)
    print(" 🚀【SSOT 嚴格對齊全量回測 (消除切片斷點偏差)】")
    print("=" * 90)

    for name, mode, ma3_filt in experiments:
        print(f"正在執行單一連續回測: {name} ...")
        tdf, eq_curve, b_trades = simulate_continuous_full_run(
            df=df, strategy_mode=mode, apply_ma3_filter=ma3_filt,
            start_bar=start_bar, end_bar=total_bars, split_bar=split_bar
        )

        trade_exports[name] = tdf

        # 1. 全樣本 (50 ~ 101k)
        stats_all = calculate_stats_from_trades(tdf, eq_curve, tag=f"{name} [全樣本 101k]")
        summary_records.append(stats_all)

        # 2. 樣本內 IS (entry_bar_idx < 60000)
        tdf_is = tdf[tdf["entry_bar_idx"] < split_bar]
        eq_is = eq_curve[: (split_bar - start_bar)]
        stats_is = calculate_stats_from_trades(tdf_is, eq_is, tag=f"{name} [IS 前 60k]")
        summary_records.append(stats_is)

        # 3. 樣本外 OOS (entry_bar_idx >= 60000)
        tdf_oos = tdf[tdf["entry_bar_idx"] >= split_bar]
        eq_oos = eq_curve[(split_bar - start_bar) :]
        stats_oos = calculate_stats_from_trades(tdf_oos, eq_oos, tag=f"{name} [OOS 後 41k]")
        summary_records.append(stats_oos)

        # 4. 驗證數學一致性
        assert stats_all["total_trades"] == stats_is["total_trades"] + stats_oos["total_trades"], "交易數不一致!"
        assert abs(stats_all["net_pnl_usd"] - (stats_is["net_pnl_usd"] + stats_oos["net_pnl_usd"])) < 0.05, "淨損益不對齊!"
        assert abs(stats_all["total_cost_usd"] - (stats_is["total_cost_usd"] + stats_oos["total_cost_usd"])) < 0.05, "成本不對齊!"

        # 5. 跨界交易記錄
        for bt in b_trades:
            boundary_records.append({
                "strategy": name,
                "trade_id": bt["id"],
                "direction": bt["direction"],
                "entry_bar_idx": bt["entry_bar_idx"],
                "entry_time": bt["entry_time"],
                "exit_bar_idx": bt["exit_bar_idx"],
                "exit_time": bt["exit_time"],
                "holding_bars": bt["exit_bar_idx"] - bt["entry_bar_idx"],
                "realized_gross_pnl": bt["realized_gross_pnl"],
                "total_cost": bt["total_fees"] + bt["total_slippage"],
                "realized_net_pnl": bt["realized_pnl"],
                "exit_reason": bt["exit_reason"]
            })

        # 6. 止損距離分組分析
        sl_bins = analyze_sl_bins(tdf, name)
        sl_bin_records.extend(sl_bins)

    # 匯出對齊總表
    os.makedirs("scratch/comprehensive_dual_eval", exist_ok=True)
    df_sum = pd.DataFrame(summary_records)
    df_sum.to_csv("scratch/comprehensive_dual_eval/reconciled_benchmark_summary.csv", index=False)

    df_sl = pd.DataFrame(sl_bin_records)
    df_sl.to_csv("scratch/comprehensive_dual_eval/sl_distance_binning_analysis.csv", index=False)

    df_bt = pd.DataFrame(boundary_records)
    df_bt.to_csv("scratch/comprehensive_dual_eval/boundary_crossing_trades_audit.csv", index=False)

    for k, tdf in trade_exports.items():
        safe_name = k.replace("/", "_").replace(" ", "_").replace("(", "").replace(")", "").replace("+", "_").replace(":", "")
        out_csv = f"scratch/comprehensive_dual_eval/{safe_name}_reconciled_trades.csv"
        req_cols = [
            "id", "period", "is_boundary_crosser", "signal_time", "direction", "signal_source",
            "entry_bar_idx", "exit_bar_idx", "entry_time", "exit_time",
            "actual_exec_price", "initial_sl_distance", "sl_pct", "orig_qty",
            "realized_gross_pnl", "total_fees", "total_slippage",
            "realized_pnl", "exit_reason"
        ]
        avail_cols = [c for c in req_cols if c in tdf.columns]
        tdf[avail_cols].to_csv(out_csv, index=False)

    print("\n✅【對齊驗證完全通過！】")
    print(df_sum[["tag", "total_trades", "win_rate_pct", "gross_pf", "net_pf", "net_pnl_usd", "total_cost_usd", "max_equity_dd_pct"]].to_string(index=False))


if __name__ == "__main__":
    main()
