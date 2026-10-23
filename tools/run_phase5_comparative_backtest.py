"""
tools/run_phase5_comparative_backtest.py
========================================
Phase 5.1 獨立驗收：Phase 5 修改前 (Old) vs 修改後 (New) 嚴格對照回測
確保：
1. 相同進場訊號 (EntryGate 完全同質)
2. 相同起始資金 ($10,000 USDT)、相同單筆名義倉位 ($1,000 USDT, 5x 槓桿, 保證金 $200)
3. 相同費率 (Taker 0.05%) 與滑點 (0.01%)，折合單筆名義摩擦 0.12%
4. 時間順序嚴格切分：樣本內 (In-Sample) vs 樣本外 (Out-of-Sample)
5. 獨立比對：
   - A 組：Phase 5 修改前版本 (scratch/backup_phase5_exit_refactor)
   - B 組：Phase 5 修改後版本 (core/gates/unified_pipeline_gates.py)
"""

import os
import sys
sys.path.insert(0, os.path.abspath("."))
import json
import importlib.util
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Tuple

# 載入舊版 ExitGate (Phase 5 修改前)
spec_old = importlib.util.spec_from_file_location(
    "old_pipeline_gates",
    "scratch/backup_phase5_exit_refactor/unified_pipeline_gates.py"
)
old_mod = importlib.util.module_from_spec(spec_old)
spec_old.loader.exec_module(old_mod)
OldExitGate = old_mod.ExitGate

# 載入新版 ExitGate (Phase 5 修改後)
from core.gates.unified_pipeline_gates import ExitGate as NewExitGate
from core.gates.unified_pipeline_gates import EntryGate
from core.services.data_pipeline import MarketDataSnapshot
from core.analytics.trade_accounting import TradeAccountingEngine

FEE_RATE = 0.0005      # 單邊手續費 0.05% (Binance VIP0 Taker)
SLIPPAGE = 0.0001      # 單邊滑點 0.01%
LEVERAGE = 5.0
NOTIONAL = 1000.0


def precompute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df_calc = df.copy()
    close = df_calc["close"]
    high = df_calc["high"]
    low = df_calc["low"]

    df_calc["ma5"] = close.rolling(window=5).mean()
    df_calc["ma15"] = close.rolling(window=15).mean()
    df_calc["ema_fast"] = close.ewm(span=15, adjust=False).mean()
    df_calc["ema_slow"] = close.ewm(span=40, adjust=False).mean()
    df_calc["ma_spread_pct"] = ((df_calc["ema_fast"] - df_calc["ema_slow"]).abs() / df_calc["ema_slow"]) * 100.0

    tr = np.maximum(high - low, np.maximum((high - close.shift(1)).abs(), (low - close.shift(1)).abs()))
    df_calc["atr"] = tr.rolling(window=14).mean().bfill().fillna(close * 0.01)

    df_calc["kc_middle"] = close.ewm(span=20, adjust=False).mean()
    df_calc["kc_upper"] = df_calc["kc_middle"] + (df_calc["atr"] * 1.5)
    df_calc["kc_lower"] = df_calc["kc_middle"] - (df_calc["atr"] * 1.5)
    df_calc["bandwidth"] = (df_calc["kc_upper"] - df_calc["kc_lower"]) / df_calc["kc_middle"]

    ema60_series = close.ewm(span=60, adjust=False).mean()
    df_calc["ema60"] = ema60_series
    df_calc["macro_slope_10"] = ema60_series - ema60_series.shift(10)
    df_calc["slow_slope_10"] = df_calc["ema_slow"] - df_calc["ema_slow"].shift(10)
    df_calc["fast_slope_3"] = df_calc["ema_fast"] - df_calc["ema_fast"].shift(3)
    df_calc["slow_slope_3"] = df_calc["ema_slow"] - df_calc["ema_slow"].shift(3)
    df_calc["kc_middle_slope_5"] = (df_calc["kc_middle"] - df_calc["kc_middle"].shift(5)) / 5.0
    return df_calc


def build_snapshot(symbol: str, df_calc: pd.DataFrame, i: int, quote: float, position: Dict[str, Any] = None) -> MarketDataSnapshot:
    last_row = df_calc.iloc[i]
    prev_row = df_calc.iloc[i-1]

    curr_k = {
        "open": float(last_row["open"]), "high": float(last_row["high"]),
        "low": float(last_row["low"]), "close": float(last_row["close"]),
        "volume": float(last_row.get("volume", 0.0)), "ma5": float(last_row["ma5"]),
        "ma15": float(last_row["ma15"]), "kc_middle": float(last_row["kc_middle"]),
    }
    prev_k = {
        "open": float(prev_row["open"]), "high": float(prev_row["high"]),
        "low": float(prev_row["low"]), "close": float(prev_row["close"]),
        "volume": float(prev_row.get("volume", 0.0)), "ma5": float(prev_row["ma5"]),
        "ma15": float(prev_row["ma15"]), "kc_middle": float(prev_row["kc_middle"]),
    }

    ema_fast = float(last_row["ema_fast"])
    ema_slow = float(last_row["ema_slow"])
    fast_slope = float(last_row["fast_slope_3"]) if not pd.isna(last_row["fast_slope_3"]) else 0.0
    slow_slope = float(last_row["slow_slope_3"]) if not pd.isna(last_row["slow_slope_3"]) else 0.0
    kc_mid_slope = float(last_row["kc_middle_slope_5"]) if not pd.isna(last_row["kc_middle_slope_5"]) else 0.0
    macro_slope_10 = float(last_row["macro_slope_10"]) if not pd.isna(last_row["macro_slope_10"]) else 0.0
    slow_slope_10 = float(last_row["slow_slope_10"]) if not pd.isna(last_row["slow_slope_10"]) else 0.0
    ema60 = float(last_row["ema60"])

    is_htf_bull = (ema_fast >= ema_slow and (macro_slope_10 >= 0 or slow_slope_10 >= 0 or kc_mid_slope >= 0)) or (quote > ema60 and macro_slope_10 > 0)
    is_htf_bear = (ema_fast <= ema_slow and (macro_slope_10 <= 0 or slow_slope_10 <= 0 or kc_mid_slope <= 0)) or (quote < ema60 and macro_slope_10 < 0)

    if is_htf_bull and not is_htf_bear:
        macro_trend = "BULLISH"
    elif is_htf_bear and not is_htf_bull:
        macro_trend = "BEARISH"
    else:
        macro_trend = "NEUTRAL"

    peak_roe = 0.0
    if position:
        entry_p = float(position.get("entry_price") or 0.0)
        side = str(position.get("side", "")).upper()
        if entry_p > 0:
            cur_move = ((quote - entry_p) / entry_p) if side == "LONG" else ((entry_p - quote) / entry_p)
            peak_roe = max(float(position.get("peak_roe", 0.0)), cur_move * LEVERAGE * 100.0)

    sub_slice = df_calc.iloc[max(0, i-25):i+1]

    return MarketDataSnapshot(
        symbol=symbol,
        current_price=quote,
        curr_k=curr_k,
        prev_k=prev_k,
        ma5=curr_k["ma5"],
        ma15=curr_k["ma15"],
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        ma_spread_pct=float(last_row["ma_spread_pct"]),
        fast_slope=fast_slope,
        slow_slope=slow_slope,
        kc_upper=float(last_row["kc_upper"]),
        kc_middle=float(last_row["kc_middle"]),
        kc_lower=float(last_row["kc_lower"]),
        kc_middle_slope=kc_mid_slope,
        atr=float(last_row["atr"]),
        peak_roe=peak_roe,
        min_price=float(df_calc["low"].iloc[max(0, i-20):i+1].min()),
        max_price=float(df_calc["high"].iloc[max(0, i-20):i+1].max()),
        macro_trend=macro_trend,
        macro_slope=macro_slope_10,
        slow_slope_5=slow_slope_10,
        bandwidth=float(last_row["bandwidth"]),
        raw_df=sub_slice,
    )


def simulate_backtest(df: pd.DataFrame, symbol: str, exit_gate_cls: Any, label: str, start_bar: int = 50, end_bar: int = None) -> List[Dict[str, Any]]:
    if end_bar is None:
        end_bar = len(df)

    trades = []
    in_pos = False
    pos = {}
    last_exit_ts = 0
    current_equity = 10000.0

    for i in range(start_bar, end_bar):
        curr_bar = df.iloc[i]
        bar_ts = curr_bar['timestamp'] / 1000.0 if curr_bar['timestamp'] > 1e11 else curr_bar['timestamp']

        snapshot = build_snapshot(
            symbol=symbol,
            df_calc=df,
            i=i,
            quote=curr_bar['close'],
            position=pos if in_pos else None
        )

        if in_pos:
            entry_p = pos['entry_price']
            side = pos['side']

            # 追蹤 MFE / MAE
            if side == 'LONG':
                pos['mfe'] = max(pos['mfe'], curr_bar['high'] - entry_p)
                pos['mae'] = max(pos['mae'], entry_p - curr_bar['low'])
                cur_move = (curr_bar['close'] - entry_p) / entry_p
            else:
                pos['mfe'] = max(pos['mfe'], entry_p - curr_bar['low'])
                pos['mae'] = max(pos['mae'], curr_bar['high'] - entry_p)
                cur_move = (entry_p - curr_bar['close']) / entry_p

            pos['peak_roe'] = max(pos.get('peak_roe', 0.0), cur_move * LEVERAGE * 100.0)

            # 3 ATR 硬止損
            hit_sl = False
            sl_price = pos['stop_loss_price']
            if side == 'LONG' and curr_bar['low'] <= sl_price:
                hit_sl = True
            elif side == 'SHORT' and curr_bar['high'] >= sl_price:
                hit_sl = True

            should_exit = False
            exit_reason = None
            if hit_sl:
                should_exit = True
                exit_reason = "CIRCUIT_BREAKER_HARD_SL"
                exec_exit_price = sl_price
            else:
                should_exit, exit_reason, _ = exit_gate_cls.evaluate(
                    pos=pos,
                    market_data=snapshot
                )
                exec_exit_price = curr_bar['close']

            if should_exit:
                p_entry_exec = entry_p * (1.0 + SLIPPAGE if side == 'LONG' else 1.0 - SLIPPAGE)
                p_exit_exec = exec_exit_price * (1.0 - SLIPPAGE if side == 'LONG' else 1.0 + SLIPPAGE)

                qty = NOTIONAL / p_entry_exec
                gross_pnl = (p_exit_exec - p_entry_exec) * qty if side == 'LONG' else (p_entry_exec - p_exit_exec) * qty
                fee_entry = NOTIONAL * FEE_RATE
                fee_exit = (qty * p_exit_exec) * FEE_RATE
                slippage_cost = (abs(p_entry_exec - entry_p) + abs(p_exit_exec - exec_exit_price)) * qty
                total_friction = fee_entry + fee_exit + slippage_cost
                net_pnl = gross_pnl - (fee_entry + fee_exit)

                margin = NOTIONAL / LEVERAGE
                net_roe_pct = (net_pnl / margin) * 100.0

                current_equity += net_pnl

                mfe_val = (pos['mfe'] * qty)
                mae_val = (pos['mae'] * qty)

                trades.append({
                    "symbol": symbol,
                    "side": side,
                    "entry_idx": pos['entry_idx'],
                    "exit_idx": i,
                    "hold_bars": i - pos['entry_idx'],
                    "p_entry": entry_p,
                    "p_exit": exec_exit_price,
                    "exit_reason": exit_reason,
                    "gross_pnl": gross_pnl,
                    "net_pnl": net_pnl,
                    "net_roe_pct": net_roe_pct,
                    "friction": total_friction,
                    "mfe_val": mfe_val,
                    "mae_val": mae_val,
                    "equity": current_equity,
                })

                in_pos = False
                pos = {}
                last_exit_ts = bar_ts
                continue

        else:
            # 冷却時間：前次平倉後至少間隔 3 根 K 棒
            if (bar_ts - last_exit_ts) < 180:
                continue

            # 候選訊號產生
            ma5_curr = snapshot.curr_k.get("ma5", snapshot.current_price)
            ma5_prev = snapshot.prev_k.get("ma5", ma5_curr)
            ma15_curr = snapshot.curr_k.get("ma15", snapshot.ma15)
            macro_trend = snapshot.macro_trend

            is_above_slow = (snapshot.curr_k["close"] >= snapshot.ema_slow)
            is_slow_up = snapshot.slow_slope >= 0
            is_bull_align = (snapshot.ema_fast >= snapshot.ema_slow)
            is_below_slow = (snapshot.curr_k["close"] <= snapshot.ema_slow)
            is_slow_down = snapshot.slow_slope <= 0
            is_bear_align = (snapshot.ema_fast <= snapshot.ema_slow)

            is_bullish_wave = (
                macro_trend == "BULLISH" or is_bull_align or (is_above_slow and is_slow_up)
                or (snapshot.kc_middle_slope >= -0.000005 and snapshot.curr_k["close"] >= snapshot.kc_middle)
            )
            is_bearish_wave = (
                macro_trend == "BEARISH" or is_bear_align or (is_below_slow and is_slow_down)
                or (snapshot.kc_middle_slope <= 0.000005 and snapshot.curr_k["close"] <= snapshot.kc_middle)
            )

            is_breakout_long = (
                (snapshot.current_price > snapshot.kc_upper or snapshot.curr_k["close"] > snapshot.kc_upper)
                and snapshot.kc_middle_slope >= -0.000005 and ma5_curr > ma5_prev
            )
            is_breakdown_short = (
                (snapshot.current_price < snapshot.kc_lower or snapshot.curr_k["close"] < snapshot.kc_lower)
                and snapshot.kc_middle_slope <= 0.000005 and ma5_curr < ma5_prev
            )

            is_cross_long = (
                ma5_curr >= ma15_curr and ma5_curr > ma5_prev
                and snapshot.kc_middle_slope >= -0.000005
                and snapshot.curr_k["close"] > snapshot.curr_k["open"]
                and snapshot.curr_k["close"] >= snapshot.kc_middle
            )
            is_cross_short = (
                ma5_curr <= ma15_curr and ma5_curr < ma5_prev
                and snapshot.kc_middle_slope <= 0.000005
                and snapshot.curr_k["close"] < snapshot.curr_k["open"]
                and snapshot.curr_k["close"] <= snapshot.kc_middle
            )

            candidate_signal = None
            if not is_bearish_wave:
                if is_breakout_long:
                    candidate_signal = "BREAKOUT_LONG"
                elif is_cross_long:
                    candidate_signal = "ENTRY_LONG_EMA_CROSS_MID"
            elif not is_bullish_wave:
                if is_breakdown_short:
                    candidate_signal = "BREAKDOWN_SHORT"
                elif is_cross_short:
                    candidate_signal = "ENTRY_SHORT_EMA_CROSS_MID"

            if candidate_signal:
                last_exit_ctx = {
                    "last_exit_time": last_exit_ts,
                    "hourly_entry_count": 0,
                    "current_time": bar_ts,
                }
                can_enter, entry_reason, entry_meta = EntryGate.evaluate(
                    signal_type=candidate_signal,
                    market_data=snapshot,
                    last_exit_context=last_exit_ctx
                )
                if can_enter:
                    entry_side = entry_meta.get("side", "LONG" if "LONG" in candidate_signal else "SHORT")
                    entry_p = curr_bar['close']
                    atr = max(snapshot.atr, entry_p * 0.005)
                    sl_p = entry_p - 3.0 * atr if entry_side == "LONG" else entry_p + 3.0 * atr

                    in_pos = True
                    pos = {
                        "symbol": symbol,
                        "side": entry_side,
                        "entry_price": entry_p,
                        "entry_idx": i,
                        "stop_loss_price": sl_p,
                        "atr_entry": atr,
                        "mfe": 0.0,
                        "mae": 0.0,
                        "peak_roe": 0.0,
                        "amount": NOTIONAL,
                        "leverage": LEVERAGE,
                    }

    return trades


def compute_metrics(trades: List[Dict[str, Any]], initial_equity: float = 10000.0) -> Dict[str, Any]:
    if not trades:
        return {
            "total_trades": 0, "net_pnl": 0.0, "return_pct": 0.0,
            "win_rate": 0.0, "profit_factor": 0.0, "max_drawdown_pct": 0.0,
            "avg_bars": 0.0, "avg_net_pnl": 0.0, "avg_friction": 0.0,
            "mfe_gap_pct": 0.0, "exit_reasons": {},
            "long_trades": 0, "long_pnl": 0.0, "long_win_rate": 0.0,
            "short_trades": 0, "short_pnl": 0.0, "short_win_rate": 0.0,
        }

    df_t = pd.DataFrame(trades)
    net_pnl = df_t["net_pnl"].sum()
    return_pct = (net_pnl / initial_equity) * 100.0
    wins = df_t[df_t["net_pnl"] > 0]
    losses = df_t[df_t["net_pnl"] <= 0]
    win_rate = (len(wins) / len(df_t)) * 100.0 if len(df_t) > 0 else 0.0

    gross_profit = wins["net_pnl"].sum() if not wins.empty else 0.0
    gross_loss = abs(losses["net_pnl"].sum()) if not losses.empty else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    # 最大回撤
    equity_curve = [initial_equity]
    for p in df_t["net_pnl"]:
        equity_curve.append(equity_curve[-1] + p)
    eq_series = pd.Series(equity_curve)
    peak = eq_series.cummax()
    dd = (eq_series - peak) / peak * 100.0
    max_dd = abs(dd.min())

    avg_bars = df_t["hold_bars"].mean()
    avg_net_pnl = df_t["net_pnl"].mean()
    avg_friction = df_t["friction"].mean()

    # MFE 捕捉差距 (最大潛在獲利 vs 實際淨獲利)
    total_mfe = df_t["mfe_val"].sum()
    mfe_gap = (total_mfe - net_pnl) / len(df_t) if len(df_t) > 0 else 0.0

    # 出場原因統計
    reasons = df_t["exit_reason"].value_counts().to_dict()

    # LONG / SHORT 分開
    longs = df_t[df_t["side"] == "LONG"]
    shorts = df_t[df_t["side"] == "SHORT"]

    long_trades = len(longs)
    long_pnl = longs["net_pnl"].sum() if long_trades > 0 else 0.0
    long_win_rate = (len(longs[longs["net_pnl"] > 0]) / long_trades * 100.0) if long_trades > 0 else 0.0

    short_trades = len(shorts)
    short_pnl = shorts["net_pnl"].sum() if short_trades > 0 else 0.0
    short_win_rate = (len(shorts[shorts["net_pnl"] > 0]) / short_trades * 100.0) if short_trades > 0 else 0.0

    return {
        "total_trades": len(df_t),
        "net_pnl": net_pnl,
        "return_pct": return_pct,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_dd,
        "avg_bars": avg_bars,
        "avg_net_pnl": avg_net_pnl,
        "avg_friction": avg_friction,
        "mfe_gap_per_trade": mfe_gap,
        "exit_reasons": reasons,
        "long_trades": long_trades,
        "long_pnl": long_pnl,
        "long_win_rate": long_win_rate,
        "short_trades": short_trades,
        "short_pnl": short_pnl,
        "short_win_rate": short_win_rate,
    }


def run_full_suite():
    print("=" * 80)
    print("🚀 啟動 Phase 5.1 修改前 (Old) vs 修改後 (New) 雙核心受控歷史回測")
    print("=" * 80)

    # 1. 載入 LOBSTER 1m 真實歷史 (取最近 10,000 根 1m K 線，約 7 天連續高波動行情)
    lobster_csv = "scratch/lobster_real_1m_history.csv"
    if os.path.exists(lobster_csv):
        df_lob = pd.read_csv(lobster_csv)
        df_lob = df_lob.sort_values("timestamp").reset_index(drop=True)
        # 取最後 10,000 根
        df_lob = df_lob.iloc[-10000:].reset_index(drop=True)
        df_lob = precompute_indicators(df_lob)
        print(f"✅ 載入 LOBSTER 1m 數據: {len(df_lob)} 根 (範圍: {df_lob['timestamp'].iloc[0]} ~ {df_lob['timestamp'].iloc[-1]})")

        # 時間順序切分：樣本內 (前 6000 根, 60%) vs 樣本外 (後 4000 根, 40%)
        lob_is = df_lob.iloc[:6000].reset_index(drop=True)
        lob_oos = df_lob.iloc[6000:].reset_index(drop=True)

        for period_name, sub_df in [("LOBSTER 1m (樣本內 In-Sample 6,000 bars)", lob_is),
                                     ("LOBSTER 1m (樣本外 Out-of-Sample 4,000 bars)", lob_oos)]:
            print(f"\n--- 【{period_name}】---")
            trades_old = simulate_backtest(sub_df, "LOBSTERUSDT", OldExitGate, "Old")
            trades_new = simulate_backtest(sub_df, "LOBSTERUSDT", NewExitGate, "New")

            m_old = compute_metrics(trades_old)
            m_new = compute_metrics(trades_new)

            print(f"[A 組 - 修改前 (Old)]")
            print(f"  淨損益: {m_old['net_pnl']:+.2f} USDT ({m_old['return_pct']:+.2f}%) | 勝率: {m_old['win_rate']:.1f}% | PF: {m_old['profit_factor']:.2f} | 最大回撤: {m_old['max_drawdown_pct']:.2f}%")
            print(f"  總交易次數: {m_old['total_trades']} 筆 | 平均持倉: {m_old['avg_bars']:.1f} 根 | 平均每筆淨利: {m_old['avg_net_pnl']:+.2f} USDT | 平均摩擦: {m_old['avg_friction']:.2f} USDT")
            print(f"  LONG: {m_old['long_trades']} 筆, 勝率 {m_old['long_win_rate']:.1f}%, 損益 {m_old['long_pnl']:+.2f} USDT | SHORT: {m_old['short_trades']} 筆, 勝率 {m_old['short_win_rate']:.1f}%, 損益 {m_old['short_pnl']:+.2f} USDT")
            print(f"  出場原因分佈: {m_old['exit_reasons']}")

            print(f"[B 組 - 修改後 (New)]")
            print(f"  淨損益: {m_new['net_pnl']:+.2f} USDT ({m_new['return_pct']:+.2f}%) | 勝率: {m_new['win_rate']:.1f}% | PF: {m_new['profit_factor']:.2f} | 最大回撤: {m_new['max_drawdown_pct']:.2f}%")
            print(f"  總交易次數: {m_new['total_trades']} 筆 | 平均持倉: {m_new['avg_bars']:.1f} 根 | 平均每筆淨利: {m_new['avg_net_pnl']:+.2f} USDT | 平均摩擦: {m_new['avg_friction']:.2f} USDT")
            print(f"  LONG: {m_new['long_trades']} 筆, 勝率 {m_new['long_win_rate']:.1f}%, 損益 {m_new['long_pnl']:+.2f} USDT | SHORT: {m_new['short_trades']} 筆, 勝率 {m_new['short_win_rate']:.1f}%, 損益 {m_new['short_pnl']:+.2f} USDT")
            print(f"  出場原因分佈: {m_new['exit_reasons']}")

    # 2. 載入 Golden Dataset (6 大幣種 3m 2,500 根)
    golden_json = "data/golden_dataset_2500_bars.json"
    if os.path.exists(golden_json):
        print("\n" + "=" * 80)
        print("✅ 載入 Golden Dataset (6 大幣種 2500 根 3m K 線)")
        with open(golden_json) as f:
            golden_data = json.load(f)

        all_old_is = []
        all_new_is = []
        all_old_oos = []
        all_new_new_oos = []

        for symbol, klines in golden_data.items():
            cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore']
            df_sym = pd.DataFrame(klines, columns=cols)
            for c in ['open', 'high', 'low', 'close', 'volume']:
                df_sym[c] = df_sym[c].astype(float)
            df_sym = precompute_indicators(df_sym)

            # 切分：樣本內前 1500 根，樣本外後 1000 根
            df_is = df_sym.iloc[:1500].reset_index(drop=True)
            df_oos = df_sym.iloc[1500:].reset_index(drop=True)

            all_old_is.extend(simulate_backtest(df_is, symbol, OldExitGate, "Old"))
            all_new_is.extend(simulate_backtest(df_is, symbol, NewExitGate, "New"))

            all_old_oos.extend(simulate_backtest(df_oos, symbol, OldExitGate, "Old"))
            all_new_new_oos.extend(simulate_backtest(df_oos, symbol, NewExitGate, "New"))

        for name, t_old, t_new in [("Golden Dataset 6 幣種 (樣本內 In-Sample 9,000 根)", all_old_is, all_new_is),
                                   ("Golden Dataset 6 幣種 (樣本外 Out-of-Sample 6,000 根)", all_old_oos, all_new_new_oos)]:
            print(f"\n--- 【{name}】---")
            m_old = compute_metrics(t_old)
            m_new = compute_metrics(t_new)

            print(f"[A 組 - 修改前 (Old)]")
            print(f"  淨損益: {m_old['net_pnl']:+.2f} USDT ({m_old['return_pct']:+.2f}%) | 勝率: {m_old['win_rate']:.1f}% | PF: {m_old['profit_factor']:.2f} | 最大回撤: {m_old['max_drawdown_pct']:.2f}%")
            print(f"  總交易次數: {m_old['total_trades']} 筆 | 平均持倉: {m_old['avg_bars']:.1f} 根 | 平均每筆淨利: {m_old['avg_net_pnl']:+.2f} USDT | 平均摩擦: {m_old['avg_friction']:.2f} USDT")
            print(f"  LONG: {m_old['long_trades']} 筆, 勝率 {m_old['long_win_rate']:.1f}%, 損益 {m_old['long_pnl']:+.2f} USDT | SHORT: {m_old['short_trades']} 筆, 勝率 {m_old['short_win_rate']:.1f}%, 損益 {m_old['short_pnl']:+.2f} USDT")
            print(f"  出場原因分佈: {m_old['exit_reasons']}")

            print(f"[B 組 - 修改後 (New)]")
            print(f"  淨損益: {m_new['net_pnl']:+.2f} USDT ({m_new['return_pct']:+.2f}%) | 勝率: {m_new['win_rate']:.1f}% | PF: {m_new['profit_factor']:.2f} | 最大回撤: {m_new['max_drawdown_pct']:.2f}%")
            print(f"  總交易次數: {m_new['total_trades']} 筆 | 平均持倉: {m_new['avg_bars']:.1f} 根 | 平均每筆淨利: {m_new['avg_net_pnl']:+.2f} USDT | 平均摩擦: {m_new['avg_friction']:.2f} USDT")
            print(f"  LONG: {m_new['long_trades']} 筆, 勝率 {m_new['long_win_rate']:.1f}%, 損益 {m_new['long_pnl']:+.2f} USDT | SHORT: {m_new['short_trades']} 筆, 勝率 {m_new['short_win_rate']:.1f}%, 損益 {m_new['short_pnl']:+.2f} USDT")
            print(f"  出場原因分佈: {m_new['exit_reasons']}")


if __name__ == "__main__":
    run_full_suite()
