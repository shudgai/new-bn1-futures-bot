"""
檔案位置: run_dual_strategy_benchmark.py
職責:
針對用戶規格第九節「回測與交付報告」嚴格執行全量回測:
1. KC 首次破軌策略 (無 MA3 過濾)
2. KC 首次破軌 + MA3 方向過濾策略
3. KC 破軌＋延續開倉策略 (無 MA3 過濾)
4. KC 破軌＋延續開倉策略 (含 MA3 方向過濾)
5. MA3／MA15 交叉策略 (無 MA3 過濾)
6. MA3／MA15 交叉策略 (含 MA3 方向過濾)
7. 合併策略 (KC 破軌延續 + MA3/MA15 交叉 + MA3 方向過濾)

風控與撮合保證:
- 訊號確認與實際成交分離 (第 N 根收盤產生，第 N+1 根開盤價成交)
- 實際成交價計算有效風險與部位大小 (0.5% 單筆風險，雙邊 0.20% 成本)
- 同向持倉最多容許 3 筆部位 (遵守總風險上限，防止無限制累積)
- 反向訊號不得反手或雙向持倉
- SSOT PessimisticMatcher 撮合 (2R/4R 分批止盈、尾倉移動停利、悲觀同根保護)
- 全樣本 101k、IS 前 60k、OOS 後 41k
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


def simulate_single_strategy(
    df: pd.DataFrame,
    strategy_mode: str,  # "KC_INITIAL", "KC_CONTINUATION", "MA_CROSS", "COMBINED"
    apply_ma3_filter: bool = True,
    start_bar: int = 50,
    end_bar: int = None,
    initial_balance: float = 1000.0,
    taker_fee_rate: float = 0.0005,
    slippage_rate: float = 0.0005,
    tp1_r: float = 2.0,
    tp2_r: float = 4.0,
    max_open_positions: int = 3,
    tag: str = "BENCHMARK"
) -> Tuple[Dict[str, Any], pd.DataFrame]:
    """
    正式回測主迴圈
    """
    total_len = len(df)
    if end_bar is None or end_bar > total_len:
        end_bar = total_len

    balance = initial_balance
    peak_equity = initial_balance
    max_equity_dd_usd = 0.0
    max_equity_dd_pct = 0.0

    total_gross_profit = 0.0
    total_gross_loss = 0.0
    total_fees_paid = 0.0
    total_slippage_paid = 0.0

    current_day = None
    daily_realized_loss = 0.0
    is_daily_halted = False

    trades: List[Dict[str, Any]] = []
    # 支援同向持倉管理 (最多 max_open_positions 筆)
    active_positions: List[Dict[str, Any]] = []
    pending_signals: List[Dict[str, Any]] = []

    # 延續狀態追蹤器
    in_continuation_side: Optional[str] = None

    open_arr = df["open"].values
    high_arr = df["high"].values
    low_arr = df["low"].values
    close_arr = df["close"].values
    dt_arr = df["datetime"].values

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

        # ─────────────────────────────────────────────────────────────
        # 1. 執行前一根收盤產生的 Pending 訊號 (下一根開盤價成交)
        # ─────────────────────────────────────────────────────────────
        if pending_signals and not is_daily_halted:
            for sig in pending_signals:
                side = sig["side"]

                # 檢查反向衝突: 若已有反向持倉，嚴禁反手或同時持有反向部位
                current_side = active_positions[0]["direction"] if active_positions else None
                if current_side is not None and current_side != side:
                    continue  # 丟棄反向訊號

                # 檢查同向持倉數量上限
                if len(active_positions) >= max_open_positions:
                    continue  # 已達最大同向持倉數

                base_sl = sig["base_sl"]
                actual_entry_p = b_open * (1.0 + slippage_rate) if side == "LONG" else b_open * (1.0 - slippage_rate)
                risk_dist = (actual_entry_p - base_sl) if side == "LONG" else (base_sl - actual_entry_p)

                if risk_dist > 0:
                    risk_amt = balance * 0.005  # 0.5% 單筆風險
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

        # ─────────────────────────────────────────────────────────────
        # 2. 持倉撮合推演 (SSOT PessimisticMatcher)
        # ─────────────────────────────────────────────────────────────
        still_active: List[Dict[str, Any]] = []
        for pos in active_positions:
            side = pos["direction"]
            match_fn = PessimisticMatcher.match_long_bar if side == "LONG" else PessimisticMatcher.match_short_bar
            m_res = match_fn(
                b_open=b_open, b_high=b_high, b_low=b_low, b_close=b_close,
                pos=pos,
                taker_fee_rate=taker_fee_rate, slippage_rate=slippage_rate
            )

            if m_res["realized_gross_pnl"] > 0:
                total_gross_profit += m_res["realized_gross_pnl"]
            elif m_res["realized_gross_pnl"] < 0:
                total_gross_loss += abs(m_res["realized_gross_pnl"])

            total_fees_paid += m_res["realized_fee"]
            total_slippage_paid += m_res["realized_slip"]
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

        # ─────────────────────────────────────────────────────────────
        # 3. 軌道外延續狀態維護與新訊號檢測 (在當根收盤進行)
        # ─────────────────────────────────────────────────────────────
        # 檢查延續狀態是否終止 (價格是否回到 KC 內)
        if in_continuation_side is not None:
            if DualEntryDetector.is_price_returned_inside_kc(df, i, in_continuation_side):
                in_continuation_side = None

        if not is_daily_halted:
            new_sigs: List[Dict[str, Any]] = []

            # 模式 A: KC 破軌判定 (首次破軌 vs 延續破軌)
            if strategy_mode in ["KC_INITIAL", "KC_CONTINUATION", "COMBINED"]:
                # 1. 首次破軌檢查 (未在延續狀態時)
                if in_continuation_side is None:
                    init_sig = DualEntryDetector.check_kc_initial_breakout(
                        df, current_idx=i, apply_ma3_filter=apply_ma3_filter
                    )
                    if init_sig is not None:
                        new_sigs.append(init_sig)
                        in_continuation_side = init_sig["side"]  # 啟動延續狀態

                # 2. 延續開倉檢查 (已在延續狀態且策略允許延續時)
                elif strategy_mode in ["KC_CONTINUATION", "COMBINED"]:
                    cont_sig = DualEntryDetector.check_kc_continuation_signal(
                        df, current_idx=i, in_continuation_side=in_continuation_side, apply_ma3_filter=apply_ma3_filter
                    )
                    if cont_sig is not None:
                        new_sigs.append(cont_sig)

            # 模式 B: MA3/MA15 交叉判定
            if strategy_mode in ["MA_CROSS", "COMBINED"]:
                ma_sig = DualEntryDetector.check_ma_cross_signal(
                    df, current_idx=i, apply_ma3_filter=apply_ma3_filter
                )
                if ma_sig is not None:
                    new_sigs.append(ma_sig)

            if new_sigs:
                pending_signals = new_sigs

        # ─────────────────────────────────────────────────────────────
        # 4. 權益與回撤實時計算
        # ─────────────────────────────────────────────────────────────
        unrealized = 0.0
        for pos in active_positions:
            side = pos["direction"]
            rem_q = pos["remaining_qty"]
            e_p = pos["entry_price"]
            raw_unrealized = (b_close - e_p) * rem_q if side == "LONG" else (e_p - b_close) * rem_q
            est_fee = (e_p + b_close) * rem_q * (taker_fee_rate + slippage_rate)
            unrealized += (raw_unrealized - est_fee)

        current_equity = balance + unrealized
        if current_equity > peak_equity:
            peak_equity = current_equity

        dd_usd = peak_equity - current_equity
        dd_pct = dd_usd / peak_equity if peak_equity > 0 else 0.0
        if dd_usd > max_equity_dd_usd:
            max_equity_dd_usd = dd_usd
        if dd_pct > max_equity_dd_pct:
            max_equity_dd_pct = dd_pct

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

            if raw_pnl > 0:
                total_gross_profit += raw_pnl
            else:
                total_gross_loss += abs(raw_pnl)

            total_fees_paid += fee
            total_slippage_paid += slip
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
    net_profit = balance - initial_balance
    total_trades = len(tdf)
    win_trades = len(tdf[tdf["realized_pnl"] > 0]) if not tdf.empty else 0
    loss_trades = len(tdf[tdf["realized_pnl"] <= 0]) if not tdf.empty else 0
    win_rate = (win_trades / total_trades) if total_trades > 0 else 0.0

    gross_pf = (total_gross_profit / total_gross_loss) if total_gross_loss > 0 else (999.0 if total_gross_profit > 0 else 0.0)
    net_wins = tdf[tdf["realized_pnl"] > 0]["realized_pnl"].sum() if not tdf.empty else 0.0
    net_losses = abs(tdf[tdf["realized_pnl"] < 0]["realized_pnl"].sum()) if not tdf.empty else 0.0
    net_pf = (net_wins / net_losses) if net_losses > 0 else 0.0

    long_trades = tdf[tdf["direction"] == "LONG"] if not tdf.empty else pd.DataFrame()
    short_trades = tdf[tdf["direction"] == "SHORT"] if not tdf.empty else pd.DataFrame()

    long_wins = len(long_trades[long_trades["realized_pnl"] > 0]) if not long_trades.empty else 0
    short_wins = len(short_trades[short_trades["realized_pnl"] > 0]) if not short_trades.empty else 0

    long_net_pnl = long_trades["realized_pnl"].sum() if not long_trades.empty else 0.0
    short_net_pnl = short_trades["realized_pnl"].sum() if not short_trades.empty else 0.0

    long_gross_prof = long_trades[long_trades["realized_gross_pnl"] > 0]["realized_gross_pnl"].sum() if not long_trades.empty else 0.0
    long_gross_loss = abs(long_trades[long_trades["realized_gross_pnl"] < 0]["realized_gross_pnl"].sum()) if not long_trades.empty else 0.0
    long_gross_pf = long_gross_prof / long_gross_loss if long_gross_loss > 0 else 0.0

    short_gross_prof = short_trades[short_trades["realized_gross_pnl"] > 0]["realized_gross_pnl"].sum() if not short_trades.empty else 0.0
    short_gross_loss = abs(short_trades[short_trades["realized_gross_pnl"] < 0]["realized_gross_pnl"].sum()) if not short_trades.empty else 0.0
    short_gross_pf = short_gross_prof / short_gross_loss if short_gross_loss > 0 else 0.0

    if not tdf.empty:
        tdf["entry_dt"] = pd.to_datetime(tdf["entry_time"])
        tdf["exit_dt"] = pd.to_datetime(tdf["exit_time"])
        tdf["duration_min"] = (tdf["exit_dt"] - tdf["entry_dt"]).dt.total_seconds() / 60.0
        avg_duration = tdf["duration_min"].mean()
    else:
        avg_duration = 0.0

    stats = {
        "tag": tag,
        "strategy_mode": strategy_mode,
        "apply_ma3_filter": apply_ma3_filter,
        "total_trades": total_trades,
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "win_rate_pct": round(win_rate * 100.0, 2),
        "long_win_rate_pct": round(long_wins / len(long_trades) * 100.0, 2) if len(long_trades) > 0 else 0.0,
        "short_win_rate_pct": round(short_wins / len(short_trades) * 100.0, 2) if len(short_trades) > 0 else 0.0,
        "gross_profit_usd": round(total_gross_profit, 2),
        "gross_loss_usd": round(total_gross_loss, 2),
        "gross_pf": round(gross_pf, 3),
        "long_gross_pf": round(long_gross_pf, 3),
        "short_gross_pf": round(short_gross_pf, 3),
        "net_pnl_usd": round(net_profit, 2),
        "long_net_pnl_usd": round(long_net_pnl, 2),
        "short_net_pnl_usd": round(short_net_pnl, 2),
        "net_pf": round(net_pf, 3),
        "expected_value_usd": round(net_profit / total_trades, 3) if total_trades > 0 else 0.0,
        "total_fees_usd": round(total_fees_paid, 2),
        "total_slippage_usd": round(total_slippage_paid, 2),
        "total_cost_usd": round(total_fees_paid + total_slippage_paid, 2),
        "max_equity_dd_pct": round(max_equity_dd_pct * 100.0, 2),
        "avg_duration_min": round(avg_duration, 1),
        "final_equity_usd": round(balance, 2)
    }

    return stats, tdf


def main():
    csv_path = "lobster_real_1m_history.csv"
    df = prepare_benchmark_data(csv_path)

    total_bars = len(df)
    split_bar = 60000

    print("\n" + "=" * 90)
    print(" 🚀【交易策略全配置獨立回測與驗證】")
    print(f" 數據集: {csv_path} (總棒數: {total_bars}, IS 前 60k, OOS 後 41k)")
    print(" 成本設定: Taker 0.05% + 滑點 0.05% (雙邊 0.20%) | 單筆風險 0.5% | 每日虧損上限 2%")
    print("=" * 90)

    # 用戶規格第九節指定的 7 種實驗配置:
    experiments = [
        ("KC首次破軌 (無MA3過濾)", "KC_INITIAL", False),
        ("KC首次破軌 (含MA3過濾)", "KC_INITIAL", True),
        ("KC破軌+延續開倉 (無MA3過濾)", "KC_CONTINUATION", False),
        ("KC破軌+延續開倉 (含MA3過濾)", "KC_CONTINUATION", True),
        ("MA3/MA15交叉 (無MA3過濾)", "MA_CROSS", False),
        ("MA3/MA15交叉 (含MA3過濾)", "MA_CROSS", True),
        ("合併策略 (KC破軌延續+MA交叉+MA3過濾)", "COMBINED", True),
    ]

    summary_rows = []
    trade_exports = {}

    for name, mode, ma3_filt in experiments:
        print(f"\n[執行進度] 正在回測: {name} ...")

        # 1. 全樣本 (101k)
        s_all, tdf_all = simulate_single_strategy(
            df=df, strategy_mode=mode, apply_ma3_filter=ma3_filt,
            start_bar=50, end_bar=total_bars, tag=f"{name} [全樣本 101k]"
        )
        summary_rows.append(s_all)
        trade_exports[f"{name}_all"] = tdf_all

        # 2. 樣本內 (IS 前 60k)
        s_is, tdf_is = simulate_single_strategy(
            df=df, strategy_mode=mode, apply_ma3_filter=ma3_filt,
            start_bar=50, end_bar=split_bar, tag=f"{name} [IS 前 60k]"
        )
        summary_rows.append(s_is)

        # 3. 樣本外 (OOS 後 41k)
        s_oos, tdf_oos = simulate_single_strategy(
            df=df, strategy_mode=mode, apply_ma3_filter=ma3_filt,
            start_bar=split_bar, end_bar=total_bars, tag=f"{name} [OOS 後 41k]"
        )
        summary_rows.append(s_oos)
        trade_exports[f"{name}_oos"] = tdf_oos

    df_summary = pd.DataFrame(summary_rows)
    cols = [
        "tag", "total_trades", "long_trades", "short_trades", "win_rate_pct",
        "gross_pf", "net_pf", "net_pnl_usd", "expected_value_usd",
        "total_cost_usd", "max_equity_dd_pct", "avg_duration_min"
    ]

    print("\n" + "=" * 90)
    print(" 📊【全策略獨立回測總覽表】")
    print("=" * 90)
    print(df_summary[cols].to_string(index=False))

    # 匯出結果 CSV
    os.makedirs("scratch/comprehensive_dual_eval", exist_ok=True)
    df_summary.to_csv("scratch/comprehensive_dual_eval/all_strategies_benchmark_summary.csv", index=False)

    for k, tdf in trade_exports.items():
        safe_name = k.replace("/", "_").replace(" ", "_").replace("(", "").replace(")", "").replace("+", "_")
        out_csv = f"scratch/comprehensive_dual_eval/{safe_name}_trades.csv"
        req_cols = [
            "id", "signal_time", "direction", "signal_source",
            "kc_upper", "kc_lower", "kc_middle", "ma3", "ma15",
            "intended_exec_price", "actual_exec_price", "initial_sl_distance",
            "orig_qty", "realized_gross_pnl", "total_fees", "total_slippage",
            "realized_pnl", "exit_reason", "entry_time", "exit_time"
        ]
        avail_cols = [c for c in req_cols if c in tdf.columns]
        tdf[avail_cols].to_csv(out_csv, index=False)

    print("\n所有回測與明細匯出完畢。")


if __name__ == "__main__":
    main()
