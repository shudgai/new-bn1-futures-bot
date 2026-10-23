"""
檔案位置: run_phase4_controlled_experiments.py
嚴格消融與分階段受控實驗回測引擎 (雙週期策略)
落實使用者指定四個階段:
  階段 1: 固定版本 E, 測試止盈距離 (E0 ~ E4)
  階段 2: 固定基準止盈, 獨立測試 15m 波動度過濾 (ATR% 分位數 vs VR 相對波動比)
  階段 3: 組合有效正貢獻條件
  階段 4: 樣本內 (In-Sample) vs 樣本外 (Out-of-Sample) 分段穩健性驗證
"""

import os
import sys
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.services.strategies.dual_tf_ema_kc_strategy import DualTimeframeEmaKcStrategy
from core.services.matching.pessimistic_matcher import PessimisticMatcher


def load_and_preprocess_data(csv_path: str = "lobster_real_1m_history.csv"):
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"找不到歷史檔案: {csv_path}")

    print(f"正在加載 1 分鐘歷史數據: {csv_path}...")
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
    df_15m["close_timestamp"] = df_15m["timestamp"] + 15 * 60 * 1000

    strategy = DualTimeframeEmaKcStrategy(risk_per_trade_pct=0.005, daily_max_loss_pct=0.02)
    print("正在向量化計算 1m 與 15m 技術指標...")
    df_1m_calc = strategy.compute_indicators(df_1m.reset_index())
    df_15m_calc = strategy.compute_indicators(df_15m)

    # 15m 帶寬與波動指標
    df_15m_calc["bandwidth"] = (df_15m_calc["kc_upper"] - df_15m_calc["kc_lower"]) / df_15m_calc["kc_middle"]
    df_15m_calc["bandwidth_ma20"] = df_15m_calc["bandwidth"].rolling(20).mean()

    # ATR% = (ATR / Close) * 100%
    df_15m_calc["atr_pct"] = (df_15m_calc["atr"] / df_15m_calc["close"]) * 100.0

    # VR = ATR / MA20(ATR)
    df_15m_calc["atr_ma20"] = df_15m_calc["atr"].rolling(20).mean()
    df_15m_calc["vr"] = df_15m_calc["atr"] / df_15m_calc["atr_ma20"]

    print("正在前向滾動計算各 Bar 截止時已確認的分形點 (零前視偏差)...")
    p_highs, p_lows = strategy.precompute_confirmed_pivots(
        highs=df_1m_calc["high"].values,
        lows=df_1m_calc["low"].values,
        left=2,
        right=2
    )

    return df_1m_calc, df_15m_calc, p_highs, p_lows


def simulate_strategy(
    df_1m_calc: pd.DataFrame,
    df_15m_calc: pd.DataFrame,
    p_highs: np.ndarray,
    p_lows: np.ndarray,
    start_bar: int = 50,
    end_bar: int = None,
    tp1_r: float = 1.0,
    tp2_r: float = 2.0,
    vol_mode: str = "none",  # "none", "atr_pct", "vr"
    vol_threshold: float = 0.0,
    initial_balance: float = 1000.0,
    taker_fee_rate: float = 0.0005,
    slippage_rate: float = 0.0005,
    tag: str = "TEST"
):
    """
    極度悲觀撮合的回測主迴圈
    """
    total_len = len(df_1m_calc)
    if end_bar is None or end_bar > total_len:
        end_bar = total_len

    balance = initial_balance
    peak_equity = initial_balance
    max_equity_dd_usd = 0.0
    max_equity_dd_pct = 0.0

    trades = []
    active_position = None

    # 每日風控
    current_day = None
    day_start_equity = initial_balance
    daily_realized_loss = 0.0

    idx_15m = 0
    close_ts_15m_list = df_15m_calc["close_timestamp"].values

    # 將 1m 與 15m 常用欄位預先抽取為 NumPy 1D 陣列 (極速索引，避免 iloc 性能開銷)
    ts_arr = df_1m_calc["timestamp"].values
    dt_arr = df_1m_calc["datetime"].values
    open_arr = df_1m_calc["open"].values
    high_arr = df_1m_calc["high"].values
    low_arr = df_1m_calc["low"].values
    close_arr = df_1m_calc["close"].values
    atr_1m_arr = df_1m_calc["atr"].values
    ema15_1m_arr = df_1m_calc["ema15"].values
    ema40_1m_arr = df_1m_calc["ema40"].values
    kc_mid_1m_arr = df_1m_calc["kc_middle"].values

    # 15m 陣列
    m15_ema15 = df_15m_calc["ema15"].values
    m15_ema40 = df_15m_calc["ema40"].values
    m15_ema15_slope = df_15m_calc["ema15_slope"].values
    m15_ema40_slope = df_15m_calc["ema40_slope"].values
    m15_kc_mid = df_15m_calc["kc_middle"].values
    m15_close = df_15m_calc["close"].values
    m15_atr = df_15m_calc["atr"].values
    m15_bw = df_15m_calc["bandwidth"].values
    m15_bw_ma20 = df_15m_calc["bandwidth_ma20"].values
    m15_atr_pct = df_15m_calc["atr_pct"].values
    m15_vr = df_15m_calc["vr"].values

    total_gross_profit = 0.0
    total_gross_loss = 0.0
    total_fees_paid = 0.0
    total_slippage_paid = 0.0

    for i in range(start_bar, end_bar):
        curr_ts = ts_arr[i]
        bar_dt = pd.Timestamp(dt_arr[i])
        bar_day = bar_dt.date()

        if current_day != bar_day:
            current_day = bar_day
            day_start_equity = balance
            daily_realized_loss = 0.0

        is_daily_halted = (daily_realized_loss >= day_start_equity * 0.02)

        # 嚴格尋找已完成收盤的 15m 棒
        while idx_15m < len(close_ts_15m_list) and close_ts_15m_list[idx_15m] <= curr_ts:
            idx_15m += 1
        completed_15m_idx = max(0, idx_15m - 1)

        if completed_15m_idx < 40 or close_ts_15m_list[completed_15m_idx] > curr_ts:
            continue

        c_idx = completed_15m_idx
        c_atr = m15_atr[c_idx]
        ema_gap = abs(m15_ema15[c_idx] - m15_ema40[c_idx])
        min_gap = 0.20 * c_atr if not np.isnan(c_atr) else 0.0

        # 版本 E 的宏觀趨勢判定 (包含 KC 帶寬擴張)
        macro_trend = "NEUTRAL"
        if ema_gap >= min_gap:
            kc_expanded = bool(m15_bw[c_idx] >= m15_bw_ma20[c_idx])
            
            # 波動度過濾檢查
            vol_passed = True
            if vol_mode == "atr_pct":
                vol_passed = (m15_atr_pct[c_idx] >= vol_threshold)
            elif vol_mode == "vr":
                vol_passed = (m15_vr[c_idx] >= vol_threshold)

            if kc_expanded and vol_passed:
                if (
                    m15_ema15[c_idx] > m15_ema40[c_idx]
                    and m15_ema15_slope[c_idx] > 0
                    and m15_ema40_slope[c_idx] >= -1e-6
                    and m15_close[c_idx] >= m15_kc_mid[c_idx]
                ):
                    macro_trend = "BULLISH"
                elif (
                    m15_ema15[c_idx] < m15_ema40[c_idx]
                    and m15_ema15_slope[c_idx] < 0
                    and m15_ema40_slope[c_idx] <= 1e-6
                    and m15_close[c_idx] <= m15_kc_mid[c_idx]
                ):
                    macro_trend = "BEARISH"

        b_open = open_arr[i]
        b_high = high_arr[i]
        b_low = low_arr[i]
        b_close = close_arr[i]
        latest_p_high = p_highs[i]
        latest_p_low = p_lows[i]

        # ──────────────────────────────────────────────────────────────
        # 1. 持倉撮合 (直接呼叫經過單元測試的 SSOT PessimisticMatcher 模組)
        # ──────────────────────────────────────────────────────────────
        if active_position is not None:
            side = active_position["side"]
            match_fn = PessimisticMatcher.match_long_bar if side == "LONG" else PessimisticMatcher.match_short_bar
            m_res = match_fn(
                b_open=b_open, b_high=b_high, b_low=b_low, b_close=b_close,
                pos=active_position,
                taker_fee_rate=taker_fee_rate, slippage_rate=slippage_rate
            )

            # 即時帳務累計 (手續費、滑價與損益精準更新)
            if m_res["realized_gross_pnl"] > 0:
                total_gross_profit += m_res["realized_gross_pnl"]
            elif m_res["realized_gross_pnl"] < 0:
                total_gross_loss += abs(m_res["realized_gross_pnl"])

            total_fees_paid += m_res["realized_fee"]
            total_slippage_paid += m_res["realized_slip"]
            balance += m_res["realized_net_pnl"]
            active_position["realized_pnl"] += m_res["realized_net_pnl"]
            active_position["realized_gross_pnl"] += m_res["realized_gross_pnl"]
            active_position["total_fees"] += m_res["realized_fee"]
            active_position["total_slippage"] += m_res["realized_slip"]

            # 即時累加當日已實現虧損
            if m_res["realized_net_pnl"] < 0:
                daily_realized_loss += abs(m_res["realized_net_pnl"])

            if m_res["closed"]:
                active_position["exit_bar_idx"] = i
                active_position["exit_time"] = str(bar_dt)
                active_position["exit_price"] = m_res["exit_price"]
                active_position["exit_reason"] = m_res["exit_reason"]
                trades.append(active_position)
                active_position = None
            else:
                # 未平倉部位: 檢查分形結構移動停損與 15m 宏觀反轉清倉
                if side == "LONG":
                    if latest_p_low is not None and not np.isnan(latest_p_low) and latest_p_low > active_position["current_sl"]:
                        active_position["current_sl"] = latest_p_low

                    if macro_trend == "BEARISH":
                        exec_p = b_close
                        rem_qty = active_position["remaining_qty"]
                        e_p = active_position["entry_price"]
                        raw_pnl = (exec_p - e_p) * rem_qty
                        fee = (e_p + exec_p) * rem_qty * taker_fee_rate
                        slip = (e_p + exec_p) * rem_qty * slippage_rate
                        net_pnl = raw_pnl - fee - slip

                        if raw_pnl > 0:
                            total_gross_profit += raw_pnl
                        else:
                            total_gross_loss += abs(raw_pnl)
                        total_fees_paid += fee
                        total_slippage_paid += slip
                        balance += net_pnl
                        active_position["realized_pnl"] += net_pnl
                        active_position["realized_gross_pnl"] += raw_pnl
                        active_position["total_fees"] += fee
                        active_position["total_slippage"] += slip
                        if net_pnl < 0:
                            daily_realized_loss += abs(net_pnl)

                        active_position["remaining_qty"] = 0.0
                        active_position["exit_bar_idx"] = i
                        active_position["exit_time"] = str(bar_dt)
                        active_position["exit_price"] = exec_p
                        active_position["exit_reason"] = "15m 宏觀反轉 (BEARISH)"
                        trades.append(active_position)
                        active_position = None

                elif side == "SHORT":
                    if latest_p_high is not None and not np.isnan(latest_p_high) and latest_p_high < active_position["current_sl"]:
                        active_position["current_sl"] = latest_p_high

                    if macro_trend == "BULLISH":
                        exec_p = b_close
                        rem_qty = active_position["remaining_qty"]
                        e_p = active_position["entry_price"]
                        raw_pnl = (e_p - exec_p) * rem_qty
                        fee = (e_p + exec_p) * rem_qty * taker_fee_rate
                        slip = (e_p + exec_p) * rem_qty * slippage_rate
                        net_pnl = raw_pnl - fee - slip

                        if raw_pnl > 0:
                            total_gross_profit += raw_pnl
                        else:
                            total_gross_loss += abs(raw_pnl)
                        total_fees_paid += fee
                        total_slippage_paid += slip
                        balance += net_pnl
                        active_position["realized_pnl"] += net_pnl
                        active_position["realized_gross_pnl"] += raw_pnl
                        active_position["total_fees"] += fee
                        active_position["total_slippage"] += slip
                        if net_pnl < 0:
                            daily_realized_loss += abs(net_pnl)

                        active_position["remaining_qty"] = 0.0
                        active_position["exit_bar_idx"] = i
                        active_position["exit_time"] = str(bar_dt)
                        active_position["exit_price"] = exec_p
                        active_position["exit_reason"] = "15m 宏觀反轉 (BULLISH)"
                        trades.append(active_position)
                        active_position = None

        # ──────────────────────────────────────────────────────────────
        # 2. 進場判定 (版本 E SSOT: 雙邊手續費與雙邊滑價)
        # ──────────────────────────────────────────────────────────────
        elif active_position is None and not is_daily_halted:
            atr_1m = float(atr_1m_arr[i])
            curr_ema15 = ema15_1m_arr[i]
            curr_ema40 = ema40_1m_arr[i]
            curr_kc_mid = kc_mid_1m_arr[i]
            prev_low = low_arr[i - 1]
            prev_high = high_arr[i - 1]

            if macro_trend == "BULLISH":
                support_zone_high = max(curr_ema15, curr_kc_mid) * 1.002
                support_zone_low = min(curr_ema40, curr_kc_mid) * 0.998
                touched_support = (b_low <= support_zone_high and b_high >= support_zone_low) or (prev_low <= support_zone_high)
                reclaimed = (b_close > b_open) and (b_close >= curr_ema15)

                has_breakout = False
                if latest_p_high is not None and not np.isnan(latest_p_high):
                    has_breakout = (b_close >= latest_p_high + (0.1 * atr_1m))

                if touched_support and reclaimed and has_breakout:
                    base_low = latest_p_low if (latest_p_low is not None and not np.isnan(latest_p_low)) else float(min(b_low, prev_low))
                    struct_sl = base_low - (0.5 * atr_1m)
                    risk_dist = b_close - struct_sl

                    if risk_dist > 0:
                        risk_amt = balance * 0.005
                        # 修正: 雙邊手續費 + 雙邊滑價
                        eff_risk = risk_dist + (b_close * (taker_fee_rate * 2 + slippage_rate * 2))
                        qty = risk_amt / eff_risk
                        active_position = {
                            "id": len(trades) + 1,
                            "side": "LONG",
                            "entry_bar_idx": i,
                            "entry_time": str(bar_dt),
                            "entry_price": b_close,
                            "current_sl": struct_sl,
                            "r_distance": risk_dist,
                            "orig_qty": qty,
                            "remaining_qty": qty,
                            "tp1_price": b_close + (tp1_r * risk_dist),
                            "tp2_price": b_close + (tp2_r * risk_dist),
                            "tp1_done": False,
                            "tp2_done": False,
                            "realized_pnl": 0.0,
                            "realized_gross_pnl": 0.0,
                            "total_fees": 0.0,
                            "total_slippage": 0.0,
                            "entry_reason": "版本E順勢做多",
                        }

            elif macro_trend == "BEARISH":
                res_zone_low = min(curr_ema15, curr_kc_mid) * 0.998
                res_zone_high = max(curr_ema40, curr_kc_mid) * 1.002
                touched_res = (b_high >= res_zone_low and b_low <= res_zone_high) or (prev_high >= res_zone_low)
                weakened = (b_close < b_open) and (b_close <= curr_ema15)

                has_breakdown = False
                if latest_p_low is not None and not np.isnan(latest_p_low):
                    has_breakdown = (b_close <= latest_p_low - (0.1 * atr_1m))

                if touched_res and weakened and has_breakdown:
                    base_high = latest_p_high if (latest_p_high is not None and not np.isnan(latest_p_high)) else float(max(b_high, prev_high))
                    struct_sl = base_high + (0.5 * atr_1m)
                    risk_dist = struct_sl - b_close

                    if risk_dist > 0:
                        risk_amt = balance * 0.005
                        # 修正: 雙邊手續費 + 雙邊滑價
                        eff_risk = risk_dist + (b_close * (taker_fee_rate * 2 + slippage_rate * 2))
                        qty = risk_amt / eff_risk
                        active_position = {
                            "id": len(trades) + 1,
                            "side": "SHORT",
                            "entry_bar_idx": i,
                            "entry_time": str(bar_dt),
                            "entry_price": b_close,
                            "current_sl": struct_sl,
                            "r_distance": risk_dist,
                            "orig_qty": qty,
                            "remaining_qty": qty,
                            "tp1_price": b_close - (tp1_r * risk_dist),
                            "tp2_price": b_close - (tp2_r * risk_dist),
                            "tp1_done": False,
                            "tp2_done": False,
                            "realized_pnl": 0.0,
                            "realized_gross_pnl": 0.0,
                            "total_fees": 0.0,
                            "total_slippage": 0.0,
                            "entry_reason": "版本E順勢做空",
                        }

        # ──────────────────────────────────────────────────────────────
        # 3. 完整帳戶權益 (Equity) 與回撤
        # ──────────────────────────────────────────────────────────────
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

    # 期末強制平倉 (修正: 平倉後重新計算最終權益與最大回撤)
    if active_position is not None:
        final_p = close_arr[end_bar - 1]
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
        active_position["realized_gross_pnl"] += raw_pnl
        active_position["total_fees"] += fee
        active_position["total_slippage"] += slip
        active_position["exit_bar_idx"] = end_bar - 1
        active_position["exit_time"] = str(pd.Timestamp(dt_arr[end_bar - 1]))
        active_position["exit_price"] = final_p
        active_position["exit_reason"] = "期末強制結算"
        trades.append(active_position)
        active_position = None

        # 重新計算最終權益與最大回撤
        current_equity = balance
        if current_equity > peak_equity:
            peak_equity = current_equity
        eq_dd_usd = peak_equity - current_equity
        eq_dd_pct = eq_dd_usd / peak_equity if peak_equity > 0 else 0.0
        if eq_dd_usd > max_equity_dd_usd:
            max_equity_dd_usd = eq_dd_usd
        if eq_dd_pct > max_equity_dd_pct:
            max_equity_dd_pct = eq_dd_pct

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

    long_trades = tdf[tdf["side"] == "LONG"] if not tdf.empty else pd.DataFrame()
    short_trades = tdf[tdf["side"] == "SHORT"] if not tdf.empty else pd.DataFrame()

    avg_win = tdf[tdf["realized_pnl"] > 0]["realized_pnl"].mean() if win_trades > 0 else 0.0
    avg_loss = tdf[tdf["realized_pnl"] < 0]["realized_pnl"].mean() if loss_trades > 0 else 0.0

    res = {
        "tag": tag,
        "total_trades": total_trades,
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "win_rate_pct": win_rate * 100.0,
        "gross_pf": gross_pf,
        "net_pf": net_pf,
        "net_pnl_usd": net_profit,
        "fees_slippage_usd": total_fees_paid + total_slippage_paid,
        "expectancy_usd": (net_profit / total_trades) if total_trades > 0 else 0.0,
        "max_equity_dd_pct": max_equity_dd_pct * 100.0,
        "avg_win_usd": avg_win,
        "avg_loss_usd": avg_loss,
        "final_equity_usd": balance
    }
    return res, tdf


def main():
    csv_path = "lobster_real_1m_history.csv"
    df_1m, df_15m, p_highs, p_lows = load_and_preprocess_data(csv_path)

    total_bars = len(df_1m)
    split_bar = 60000  # In-Sample 前 60,000 根 (~41 天); Out-of-Sample 後 41,000 根 (~29 天)

    print(f"\n資料集劃分: 全量 {total_bars} 根 | 訓練區間 (IS) 0 ~ {split_bar} | 樣本外 (OOS) {split_bar} ~ {total_bars}")

    # 計算 In-Sample 區間內的 15m ATR 分位數 (絕不偷看 OOS!)
    ts_split = df_1m.iloc[split_bar]["timestamp"]
    df_15m_is = df_15m[df_15m["close_timestamp"] <= ts_split]
    atr_pct_is = df_15m_is["atr_pct"].dropna()
    p30_atr = float(np.percentile(atr_pct_is, 30))
    p40_atr = float(np.percentile(atr_pct_is, 40))
    p50_atr = float(np.percentile(atr_pct_is, 50))
    print(f"In-Sample 15m ATR% 分位數: p30={p30_atr:.2f}%, p40={p40_atr:.2f}%, p50={p50_atr:.2f}%")

    # ══════════════════════════════════════════════════════════════════
    # 階段 1: 固定版本 E，測試止盈距離 (E0 ~ E4) - 在全量與 IS 測試
    # ══════════════════════════════════════════════════════════════════
    tp_configs = [
        ("E0 (基準 1R/2R)", 1.0, 2.0),
        ("E1 (1.5R/2.5R)", 1.5, 2.5),
        ("E2 (1.5R/3.0R)", 1.5, 3.0),
        ("E3 (2.0R/3.0R)", 2.0, 3.0),
        ("E4 (2.0R/4.0R)", 2.0, 4.0),
    ]

    print("\n" + "=" * 80)
    print(" 【階段 1】固定版本 E，止盈距離實驗 (E0 ~ E4) - 全量 101k 數據")
    print("=" * 80)

    p1_results = []
    for tag, tp1, tp2 in tp_configs:
        res, _ = simulate_strategy(
            df_1m, df_15m, p_highs, p_lows,
            start_bar=50, end_bar=total_bars,
            tp1_r=tp1, tp2_r=tp2,
            vol_mode="none", vol_threshold=0.0,
            tag=tag
        )
        p1_results.append(res)

    df_p1 = pd.DataFrame(p1_results)
    print(df_p1.to_string(index=False))

    # ══════════════════════════════════════════════════════════════════
    # 階段 2: 固定原本止盈 (1R/2R)，獨立測試 15m 波動度過濾
    # ══════════════════════════════════════════════════════════════════
    vol_configs = [
        ("基準 (無波動過濾)", "none", 0.0),
        (f"方法A: ATR% >= p30 ({p30_atr:.2f}%)", "atr_pct", p30_atr),
        (f"方法A: ATR% >= p40 ({p40_atr:.2f}%)", "atr_pct", p40_atr),
        (f"方法A: ATR% >= p50 ({p50_atr:.2f}%)", "atr_pct", p50_atr),
        ("方法B: VR >= 1.0", "vr", 1.0),
        ("方法B: VR >= 1.1", "vr", 1.1),
        ("方法B: VR >= 1.2", "vr", 1.2),
    ]

    print("\n" + "=" * 80)
    print(" 【階段 2】固定基準止盈 (1R/2R)，獨立測試波動度過濾 - 全量 101k 數據")
    print("=" * 80)

    p2_results = []
    for tag, mode, thresh in vol_configs:
        res, _ = simulate_strategy(
            df_1m, df_15m, p_highs, p_lows,
            start_bar=50, end_bar=total_bars,
            tp1_r=1.0, tp2_r=2.0,
            vol_mode=mode, vol_threshold=thresh,
            tag=tag
        )
        p2_results.append(res)

    df_p2 = pd.DataFrame(p2_results)
    print(df_p2.to_string(index=False))

    # 匯出結果 CSV
    os.makedirs("scratch/phase5_8_ablation", exist_ok=True)
    df_p1.to_csv("scratch/phase5_8_ablation/phase1_tp_results.csv", index=False)
    df_p2.to_csv("scratch/phase5_8_ablation/phase2_vol_results.csv", index=False)

    # ══════════════════════════════════════════════════════════════════
    # 階段 3: 組合有效正貢獻條件 (全量 101k)
    # ══════════════════════════════════════════════════════════════════
    comb_configs = [
        ("組合 0 (基準 E0)", 1.0, 2.0, "none", 0.0),
        ("組合 1 (E3: 2R/3R + ATR% >= p50)", 2.0, 3.0, "atr_pct", p50_atr),
        ("組合 2 (E3: 2R/3R + VR >= 1.1)", 2.0, 3.0, "vr", 1.1),
        ("組合 3 (E4: 2R/4R + ATR% >= p40)", 2.0, 4.0, "atr_pct", p40_atr),
        ("組合 4 (E4: 2R/4R + ATR% >= p50)", 2.0, 4.0, "atr_pct", p50_atr),
        ("組合 5 (E4: 2R/4R + VR >= 1.1)", 2.0, 4.0, "vr", 1.1),
    ]

    print("\n" + "=" * 80)
    print(" 【階段 3】組合有效條件測試 (擴大盈虧比 + 波動過濾) - 全量 101k 數據")
    print("=" * 80)

    p3_results = []
    for tag, tp1, tp2, vmode, vthresh in comb_configs:
        res, _ = simulate_strategy(
            df_1m, df_15m, p_highs, p_lows,
            start_bar=50, end_bar=total_bars,
            tp1_r=tp1, tp2_r=tp2,
            vol_mode=vmode, vol_threshold=vthresh,
            tag=tag
        )
        p3_results.append(res)

    df_p3 = pd.DataFrame(p3_results)
    print(df_p3.to_string(index=False))
    df_p3.to_csv("scratch/phase5_8_ablation/phase3_combination_results.csv", index=False)

    # ══════════════════════════════════════════════════════════════════
    # 階段 4: 樣本內 (In-Sample) vs 樣本外 (Out-of-Sample) 分段嚴格驗收
    # ══════════════════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print(" 【階段 4】樣本內 (IS: 0~60k) vs 樣本外 (OOS: 60k~101k) 分段嚴格驗證")
    print("=" * 80)

    test_candidates = [
        ("基準 E0 (1R/2R, 無波動過濾)", 1.0, 2.0, "none", 0.0),
        ("組合 4 (E4: 2R/4R + ATR% >= p50)", 2.0, 4.0, "atr_pct", p50_atr),
        ("組合 5 (E4: 2R/4R + VR >= 1.1)", 2.0, 4.0, "vr", 1.1),
    ]

    p4_results = []
    for base_tag, tp1, tp2, vmode, vthresh in test_candidates:
        # In-Sample (0 ~ 60,000)
        res_is, _ = simulate_strategy(
            df_1m, df_15m, p_highs, p_lows,
            start_bar=50, end_bar=split_bar,
            tp1_r=tp1, tp2_r=tp2,
            vol_mode=vmode, vol_threshold=vthresh,
            tag=f"{base_tag} [IS: 前60k]"
        )
        p4_results.append(res_is)

        # Out-of-Sample (60,000 ~ 101,000)
        res_oos, _ = simulate_strategy(
            df_1m, df_15m, p_highs, p_lows,
            start_bar=split_bar, end_bar=total_bars,
            tp1_r=tp1, tp2_r=tp2,
            vol_mode=vmode, vol_threshold=vthresh,
            tag=f"{base_tag} [OOS: 後41k]"
        )
        p4_results.append(res_oos)

    df_p4 = pd.DataFrame(p4_results)
    print(df_p4.to_string(index=False))
    df_p4.to_csv("scratch/phase5_8_ablation/phase4_is_oos_results.csv", index=False)



if __name__ == "__main__":
    main()
