"""
檔案位置: run_backtest_dual_tf.py
高精度、無前視偏差、嚴格悲觀撮合的雙週期量化回測引擎 (支援 A~E 對照測試)
"""

import os
import sys
import argparse
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.services.strategies.dual_tf_ema_kc_strategy import DualTimeframeEmaKcStrategy


def load_and_resample(csv_path: str):
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"找不到歷史資料檔: {csv_path}")

    print(f"正在讀取 1 分鐘歷史資料: {csv_path}...")
    df_1m = pd.read_csv(csv_path)
    df_1m["datetime"] = pd.to_datetime(df_1m["timestamp"], unit="ms", utc=True)
    df_1m.set_index("datetime", inplace=True)
    df_1m.sort_index(inplace=True)

    print("正在執行嚴格 15 分鐘無前視偏差重採樣 (closed='left', label='left')...")
    df_15m = df_1m.resample("15min", closed="left", label="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "timestamp": "first"
    }).dropna().reset_index()

    # 15m K 線收盤時間戳 = 開盤時間戳 + 15 分鐘
    # 唯有當 1m 時間戳 >= 15m close_timestamp 時，該 15m 棒才算正式完成！
    df_15m["close_timestamp"] = df_15m["timestamp"] + 15 * 60 * 1000

    return df_1m.reset_index(), df_15m


def run_dual_tf_backtest(
    csv_path: str = "lobster_real_1m_history.csv",
    variant: str = "A",
    ema_gap_atr_mult: float = 0.20,
    kc_expand_filter: bool = False,
    strict_pivot_breakout_only: bool = False,
    min_breakout_atr_mult: float = 0.0,
    initial_balance: float = 1000.0,
    taker_fee_rate: float = 0.0005,
    slippage_rate: float = 0.0005,
    output_csv: str = None
):
    df_1m, df_15m = load_and_resample(csv_path)

    strategy = DualTimeframeEmaKcStrategy(
        risk_per_trade_pct=0.005,
        daily_max_loss_pct=0.02,
        taker_fee_rate=taker_fee_rate,
        slippage_buffer_pct=slippage_rate,
    )

    print(f"正在向量化計算 1m 與 15m 技術指標 (Variant: {variant})...")
    df_1m_calc = strategy.compute_indicators(df_1m)
    df_15m_calc = strategy.compute_indicators(df_15m)

    # 15m 帶寬計算 (normalized bandwidth)
    df_15m_calc["bandwidth"] = (df_15m_calc["kc_upper"] - df_15m_calc["kc_lower"]) / df_15m_calc["kc_middle"]
    df_15m_calc["bandwidth_ma20"] = df_15m_calc["bandwidth"].rolling(20).mean()

    print("正在前向滾動計算各 Bar 截止時已確認的分形點 (零前視偏差)...")
    p_highs, p_lows = strategy.precompute_confirmed_pivots(
        highs=df_1m_calc["high"].values,
        lows=df_1m_calc["low"].values,
        left=2,
        right=2
    )

    # 狀態變數
    balance = initial_balance
    peak_equity = initial_balance
    max_equity_dd_usd = 0.0
    max_equity_dd_pct = 0.0

    trades = []
    active_position = None

    # 每日風控變數
    current_day = None
    day_start_equity = initial_balance
    daily_realized_loss = 0.0

    # 15m 時間戳搜尋 (嚴格依 close_timestamp 對齊)
    idx_15m = 0
    close_ts_15m_list = df_15m_calc["close_timestamp"].values

    total_len = len(df_1m_calc)
    ts_arr = df_1m_calc["timestamp"].values
    dt_arr = df_1m_calc["datetime"].values
    open_arr = df_1m_calc["open"].values
    high_arr = df_1m_calc["high"].values
    low_arr = df_1m_calc["low"].values
    close_arr = df_1m_calc["close"].values

    total_gross_profit = 0.0
    total_gross_loss = 0.0
    total_fees_paid = 0.0
    total_slippage_paid = 0.0

    for i in range(50, total_len):
        curr_ts = ts_arr[i]
        bar_dt = pd.Timestamp(dt_arr[i])
        bar_day = bar_dt.date()

        # 每日重置
        if current_day != bar_day:
            current_day = bar_day
            day_start_equity = balance
            daily_realized_loss = 0.0

        # 當日最大權益虧損熔斷 (2%)
        is_daily_halted = (daily_realized_loss >= day_start_equity * strategy.daily_max_loss_pct)

        # 嚴格尋找已完成收盤的 15m 棒
        # 1m 開盤時間戳 curr_ts 必須 >= 15m close_timestamp
        while idx_15m < len(close_ts_15m_list) and close_ts_15m_list[idx_15m] <= curr_ts:
            idx_15m += 1
        completed_15m_idx = max(0, idx_15m - 1)

        if completed_15m_idx < 40 or close_ts_15m_list[completed_15m_idx] > curr_ts:
            continue

        row_15m = df_15m_calc.iloc[completed_15m_idx]
        ema_gap = abs(row_15m["ema15"] - row_15m["ema40"])
        min_gap = ema_gap_atr_mult * row_15m["atr"] if not pd.isna(row_15m["atr"]) else 0.0

        # 15m 宏觀趨勢判定
        macro_trend = "NEUTRAL"
        if ema_gap >= min_gap:
            # KC 帶寬擴張過濾 (Variant C)
            kc_expanded = True
            if kc_expand_filter:
                kc_expanded = bool(row_15m["bandwidth"] >= row_15m["bandwidth_ma20"])

            if kc_expanded:
                if (
                    row_15m["ema15"] > row_15m["ema40"]
                    and row_15m["ema15_slope"] > 0
                    and row_15m["ema40_slope"] >= -1e-6
                    and row_15m["close"] >= row_15m["kc_middle"]
                ):
                    macro_trend = "BULLISH"
                elif (
                    row_15m["ema15"] < row_15m["ema40"]
                    and row_15m["ema15_slope"] < 0
                    and row_15m["ema40_slope"] <= 1e-6
                    and row_15m["close"] <= row_15m["kc_middle"]
                ):
                    macro_trend = "BEARISH"

        b_open = open_arr[i]
        b_high = high_arr[i]
        b_low = low_arr[i]
        b_close = close_arr[i]
        latest_p_high = p_highs[i]
        latest_p_low = p_lows[i]

        # ══════════════════════════════════════════════════════════════
        # 1. 持倉管理與極限悲觀撮合 (Pessimistic Matching)
        # ══════════════════════════════════════════════════════════════
        if active_position is not None:
            side = active_position["side"]
            entry_p = active_position["entry_price"]
            r_dist = active_position["r_distance"]
            orig_qty = active_position["orig_qty"]
            curr_sl = active_position["current_sl"]
            tp1_p = active_position["tp1_price"]
            tp2_p = active_position["tp2_price"]
            tp1_done = active_position["tp1_done"]
            tp2_done = active_position["tp2_done"]

            closed_this_bar = False

            # 多單撮合
            if side == "LONG":
                # A. 悲觀撮合原則：優先檢查停損！
                if b_low <= curr_sl:
                    exec_p = min(b_open, curr_sl)
                    rem_qty = active_position["remaining_qty"]
                    raw_pnl = (exec_p - entry_p) * rem_qty
                    fee = (entry_p + exec_p) * rem_qty * taker_fee_rate
                    slip = (entry_p + exec_p) * rem_qty * slippage_rate

                    if raw_pnl > 0:
                        total_gross_profit += raw_pnl
                    else:
                        total_gross_loss += abs(raw_pnl)

                    total_fees_paid += fee
                    total_slippage_paid += slip
                    net_pnl = raw_pnl - fee - slip

                    balance += net_pnl
                    active_position["realized_pnl"] += net_pnl
                    if active_position["realized_pnl"] < 0:
                        daily_realized_loss += abs(active_position["realized_pnl"])

                    active_position["exit_time"] = str(bar_dt)
                    active_position["exit_price"] = exec_p
                    active_position["exit_reason"] = "結構停損/移動停利觸發"
                    trades.append(active_position)
                    active_position = None
                    closed_this_bar = True

                # B. 停損未觸及，檢查分批止盈 (以目標價精確成交)
                if not closed_this_bar:
                    # +1R (平倉 30%)
                    if not tp1_done and b_high >= tp1_p:
                        close_qty = orig_qty * 0.30
                        raw_pnl = (tp1_p - entry_p) * close_qty
                        fee = (entry_p + tp1_p) * close_qty * taker_fee_rate
                        slip = (entry_p + tp1_p) * close_qty * slippage_rate
                        net_pnl = raw_pnl - fee - slip

                        total_gross_profit += raw_pnl
                        total_fees_paid += fee
                        total_slippage_paid += slip

                        balance += net_pnl
                        active_position["realized_pnl"] += net_pnl
                        active_position["remaining_qty"] -= close_qty
                        active_position["tp1_done"] = True
                        curr_sl = max(curr_sl, entry_p)  # 上移保本
                        active_position["current_sl"] = curr_sl

                    # +2R (再平倉 30%) - 嚴格按成交順序檢驗
                    if active_position["tp1_done"] and not tp2_done and b_high >= tp2_p:
                        close_qty = orig_qty * 0.30
                        raw_pnl = (tp2_p - entry_p) * close_qty
                        fee = (entry_p + tp2_p) * close_qty * taker_fee_rate
                        slip = (entry_p + tp2_p) * close_qty * slippage_rate
                        net_pnl = raw_pnl - fee - slip

                        total_gross_profit += raw_pnl
                        total_fees_paid += fee
                        total_slippage_paid += slip

                        balance += net_pnl
                        active_position["realized_pnl"] += net_pnl
                        active_position["remaining_qty"] -= close_qty
                        active_position["tp2_done"] = True
                        curr_sl = max(curr_sl, entry_p + (1.0 * r_dist))  # 鎖定 +1R
                        active_position["current_sl"] = curr_sl

                    # C. 尾倉 40% 結構移動停利
                    if latest_p_low is not None and not np.isnan(latest_p_low) and latest_p_low > curr_sl:
                        curr_sl = latest_p_low
                        active_position["current_sl"] = curr_sl

                    # D. 15m 宏觀反轉清倉
                    if macro_trend == "BEARISH":
                        exec_p = b_close
                        rem_qty = active_position["remaining_qty"]
                        raw_pnl = (exec_p - entry_p) * rem_qty
                        fee = (entry_p + exec_p) * rem_qty * taker_fee_rate
                        slip = (entry_p + exec_p) * rem_qty * slippage_rate
                        net_pnl = raw_pnl - fee - slip

                        if raw_pnl > 0:
                            total_gross_profit += raw_pnl
                        else:
                            total_gross_loss += abs(raw_pnl)

                        total_fees_paid += fee
                        total_slippage_paid += slip
                        balance += net_pnl
                        active_position["realized_pnl"] += net_pnl

                        if active_position["realized_pnl"] < 0:
                            daily_realized_loss += abs(active_position["realized_pnl"])

                        active_position["exit_time"] = str(bar_dt)
                        active_position["exit_price"] = exec_p
                        active_position["exit_reason"] = "15m 宏觀反轉 (BEARISH)"
                        trades.append(active_position)
                        active_position = None

            # 空單撮合
            elif side == "SHORT":
                if b_high >= curr_sl:
                    exec_p = max(b_open, curr_sl)
                    rem_qty = active_position["remaining_qty"]
                    raw_pnl = (entry_p - exec_p) * rem_qty
                    fee = (entry_p + exec_p) * rem_qty * taker_fee_rate
                    slip = (entry_p + exec_p) * rem_qty * slippage_rate

                    if raw_pnl > 0:
                        total_gross_profit += raw_pnl
                    else:
                        total_gross_loss += abs(raw_pnl)

                    total_fees_paid += fee
                    total_slippage_paid += slip
                    net_pnl = raw_pnl - fee - slip

                    balance += net_pnl
                    active_position["realized_pnl"] += net_pnl
                    if active_position["realized_pnl"] < 0:
                        daily_realized_loss += abs(active_position["realized_pnl"])

                    active_position["exit_time"] = str(bar_dt)
                    active_position["exit_price"] = exec_p
                    active_position["exit_reason"] = "結構停損/移動停利觸發"
                    trades.append(active_position)
                    active_position = None
                    closed_this_bar = True

                if not closed_this_bar:
                    if not tp1_done and b_low <= tp1_p:
                        close_qty = orig_qty * 0.30
                        raw_pnl = (entry_p - tp1_p) * close_qty
                        fee = (entry_p + tp1_p) * close_qty * taker_fee_rate
                        slip = (entry_p + tp1_p) * close_qty * slippage_rate
                        net_pnl = raw_pnl - fee - slip

                        total_gross_profit += raw_pnl
                        total_fees_paid += fee
                        total_slippage_paid += slip

                        balance += net_pnl
                        active_position["realized_pnl"] += net_pnl
                        active_position["remaining_qty"] -= close_qty
                        active_position["tp1_done"] = True
                        curr_sl = min(curr_sl, entry_p)
                        active_position["current_sl"] = curr_sl

                    if active_position["tp1_done"] and not tp2_done and b_low <= tp2_p:
                        close_qty = orig_qty * 0.30
                        raw_pnl = (entry_p - tp2_p) * close_qty
                        fee = (entry_p + tp2_p) * close_qty * taker_fee_rate
                        slip = (entry_p + tp2_p) * close_qty * slippage_rate
                        net_pnl = raw_pnl - fee - slip

                        total_gross_profit += raw_pnl
                        total_fees_paid += fee
                        total_slippage_paid += slip

                        balance += net_pnl
                        active_position["realized_pnl"] += net_pnl
                        active_position["remaining_qty"] -= close_qty
                        active_position["tp2_done"] = True
                        curr_sl = min(curr_sl, entry_p - (1.0 * r_dist))
                        active_position["current_sl"] = curr_sl

                    if latest_p_high is not None and not np.isnan(latest_p_high) and latest_p_high < curr_sl:
                        curr_sl = latest_p_high
                        active_position["current_sl"] = curr_sl

                    if macro_trend == "BULLISH":
                        exec_p = b_close
                        rem_qty = active_position["remaining_qty"]
                        raw_pnl = (entry_p - exec_p) * rem_qty
                        fee = (entry_p + exec_p) * rem_qty * taker_fee_rate
                        slip = (entry_p + exec_p) * rem_qty * slippage_rate
                        net_pnl = raw_pnl - fee - slip

                        if raw_pnl > 0:
                            total_gross_profit += raw_pnl
                        else:
                            total_gross_loss += abs(raw_pnl)

                        total_fees_paid += fee
                        total_slippage_paid += slip
                        balance += net_pnl
                        active_position["realized_pnl"] += net_pnl

                        if active_position["realized_pnl"] < 0:
                            daily_realized_loss += abs(active_position["realized_pnl"])

                        active_position["exit_time"] = str(bar_dt)
                        active_position["exit_price"] = exec_p
                        active_position["exit_reason"] = "15m 宏觀反轉 (BULLISH)"
                        trades.append(active_position)
                        active_position = None

        # ══════════════════════════════════════════════════════════════
        # 2. 空倉進場判定 (SSOT 規則)
        # ══════════════════════════════════════════════════════════════
        elif active_position is None and not is_daily_halted:
            last_k = df_1m_calc.iloc[i]
            prev_k = df_1m_calc.iloc[i - 1]
            atr_1m = float(last_k["atr"])

            if macro_trend == "BULLISH":
                support_zone_high = max(last_k["ema15"], last_k["kc_middle"]) * 1.002
                support_zone_low = min(last_k["ema40"], last_k["kc_middle"]) * 0.998
                touched_support = (last_k["low"] <= support_zone_high and last_k["high"] >= support_zone_low) or (prev_k["low"] <= support_zone_high)
                reclaimed = (last_k["close"] > last_k["open"]) and (last_k["close"] >= last_k["ema15"])

                # 突破品質過濾 (Variant D)
                has_breakout = False
                if strict_pivot_breakout_only:
                    if latest_p_high is not None and not np.isnan(latest_p_high):
                        breakout_margin = min_breakout_atr_mult * atr_1m
                        has_breakout = (last_k["close"] >= latest_p_high + breakout_margin)
                else:
                    has_breakout = (latest_p_high is not None and not np.isnan(latest_p_high) and last_k["close"] > latest_p_high) or (last_k["close"] > prev_k["high"])

                if touched_support and reclaimed and has_breakout:
                    base_low = latest_p_low if (latest_p_low is not None and not np.isnan(latest_p_low)) else float(min(last_k["low"], prev_k["low"]))
                    struct_sl = base_low - (0.5 * atr_1m)
                    risk_dist = b_close - struct_sl

                    if risk_dist > 0:
                        risk_amt = balance * 0.005
                        eff_risk = risk_dist + (b_close * (taker_fee_rate * 2 + slippage_rate))
                        qty = risk_amt / eff_risk
                        active_position = {
                            "id": len(trades) + 1,
                            "side": "LONG",
                            "entry_time": str(bar_dt),
                            "entry_price": b_close,
                            "current_sl": struct_sl,
                            "r_distance": risk_dist,
                            "orig_qty": qty,
                            "remaining_qty": qty,
                            "tp1_price": b_close + (1.0 * risk_dist),
                            "tp2_price": b_close + (2.0 * risk_dist),
                            "tp1_done": False,
                            "tp2_done": False,
                            "realized_pnl": 0.0,
                            "entry_reason": "15m多頭+1m回踩守穩EMA/KC",
                        }

            elif macro_trend == "BEARISH":
                res_zone_low = min(last_k["ema15"], last_k["kc_middle"]) * 0.998
                res_zone_high = max(last_k["ema40"], last_k["kc_middle"]) * 1.002
                touched_res = (last_k["high"] >= res_zone_low and last_k["low"] <= res_zone_high) or (prev_k["high"] >= res_zone_low)
                weakened = (last_k["close"] < last_k["open"]) and (last_k["close"] <= last_k["ema15"])

                has_breakdown = False
                if strict_pivot_breakout_only:
                    if latest_p_low is not None and not np.isnan(latest_p_low):
                        breakout_margin = min_breakout_atr_mult * atr_1m
                        has_breakdown = (last_k["close"] <= latest_p_low - breakout_margin)
                else:
                    has_breakdown = (latest_p_low is not None and not np.isnan(latest_p_low) and last_k["close"] < latest_p_low) or (last_k["close"] < prev_k["low"])

                if touched_res and weakened and has_breakdown:
                    base_high = latest_p_high if (latest_p_high is not None and not np.isnan(latest_p_high)) else float(max(last_k["high"], prev_k["high"]))
                    struct_sl = base_high + (0.5 * atr_1m)
                    risk_dist = struct_sl - b_close

                    if risk_dist > 0:
                        risk_amt = balance * 0.005
                        eff_risk = risk_dist + (b_close * (taker_fee_rate * 2 + slippage_rate))
                        qty = risk_amt / eff_risk
                        active_position = {
                            "id": len(trades) + 1,
                            "side": "SHORT",
                            "entry_time": str(bar_dt),
                            "entry_price": b_close,
                            "current_sl": struct_sl,
                            "r_distance": risk_dist,
                            "orig_qty": qty,
                            "remaining_qty": qty,
                            "tp1_price": b_close - (1.0 * risk_dist),
                            "tp2_price": b_close - (2.0 * risk_dist),
                            "tp1_done": False,
                            "tp2_done": False,
                            "realized_pnl": 0.0,
                            "entry_reason": "15m空頭+1m反抽受阻EMA/KC",
                        }

        # ══════════════════════════════════════════════════════════════
        # 3. 完整帳戶權益 (Equity) 與真實動態回撤追蹤
        # ══════════════════════════════════════════════════════════════
        unrealized_cur = 0.0
        if active_position is not None:
            side = active_position["side"]
            rem_q = active_position["remaining_qty"]
            e_p = active_position["entry_price"]
            raw_unrealized = (b_close - e_p) * rem_q if side == "LONG" else (e_p - b_close) * rem_q
            est_close_fee = (e_p + b_close) * rem_q * (taker_fee_rate + slippage_rate)
            unrealized_cur = raw_unrealized - est_close_fee

        current_equity = balance + unrealized_cur
        if current_equity > peak_equity:
            peak_equity = current_equity

        eq_dd_usd = peak_equity - current_equity
        eq_dd_pct = eq_dd_usd / peak_equity if peak_equity > 0 else 0.0

        if eq_dd_usd > max_equity_dd_usd:
            max_equity_dd_usd = eq_dd_usd
        if eq_dd_pct > max_equity_dd_pct:
            max_equity_dd_pct = eq_dd_pct

    # ══════════════════════════════════════════════════════════════
    # 4. 期末最後一根強制結算 (扣除手續費與滑價)
    # ══════════════════════════════════════════════════════════════
    if active_position is not None:
        final_p = close_arr[-1]
        side = active_position["side"]
        rem_q = active_position["remaining_qty"]
        e_p = active_position["entry_price"]
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
        active_position["realized_pnl"] += net_pnl
        active_position["exit_time"] = str(pd.Timestamp(dt_arr[-1]))
        active_position["exit_price"] = final_p
        active_position["exit_reason"] = "期末強制結算"
        trades.append(active_position)
        active_position = None

    # ══════════════════════════════════════════════════════════════
    # 5. 匯出逐筆明細與回報
    # ══════════════════════════════════════════════════════════════
    tdf = pd.DataFrame(trades)
    if output_csv and not tdf.empty:
        os.makedirs(os.path.dirname(output_csv), exist_ok=True)
        tdf.to_csv(output_csv, index=False)

    net_profit = balance - initial_balance
    total_trades = len(tdf)
    win_trades = len(tdf[tdf["realized_pnl"] > 0]) if not tdf.empty else 0
    loss_trades = len(tdf[tdf["realized_pnl"] <= 0]) if not tdf.empty else 0
    win_rate = (win_trades / total_trades) if total_trades > 0 else 0.0

    gross_pf = (total_gross_profit / total_gross_loss) if total_gross_loss > 0 else (999.0 if total_gross_profit > 0 else 0.0)
    net_wins = tdf[tdf["realized_pnl"] > 0]["realized_pnl"].sum() if not tdf.empty else 0.0
    net_losses = abs(tdf[tdf["realized_pnl"] < 0]["realized_pnl"].sum()) if not tdf.empty else 0.0
    net_pf = (net_wins / net_losses) if net_losses > 0 else 0.0

    long_trades = tdf[tdf["side"] == "LONG"] if not tdf.empty else pd.DataFrame()
    short_trades = tdf[tdf["side"] == "SHORT"] if not tdf.empty else pd.DataFrame()

    report = {
        "variant": variant,
        "total_bars": total_len,
        "total_trades": total_trades,
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "win_trades": win_trades,
        "loss_trades": loss_trades,
        "win_rate": win_rate,
        "gross_profit_usd": total_gross_profit,
        "gross_loss_usd": total_gross_loss,
        "gross_pf": gross_pf,
        "fees_paid_usd": total_fees_paid,
        "slippage_paid_usd": total_slippage_paid,
        "net_pnl_usd": net_profit,
        "net_pf": net_pf,
        "expectancy_usd": (net_profit / total_trades) if total_trades > 0 else 0.0,
        "max_equity_dd_usd": max_equity_dd_usd,
        "max_equity_dd_pct": max_equity_dd_pct,
        "final_equity_usd": balance,
    }

    return report, tdf


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="lobster_real_1m_history.csv")
    parser.add_argument("--variant", default="A")
    args = parser.parse_args()

    rep, _ = run_dual_tf_backtest(args.csv, variant=args.variant)
    print("\n" + "═" * 70)
    print(f" 雙週期策略回測報告 (Variant {rep['variant']})")
    print("═" * 70)
    for k, v in rep.items():
        if isinstance(v, float):
            print(f"{k:<25}: {v:.4f}")
        else:
            print(f"{k:<25}: {v}")
    print("═" * 70)
