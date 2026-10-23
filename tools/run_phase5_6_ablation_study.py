#!/usr/bin/env python3
"""
tools/run_phase5_6_ablation_study.py
====================================
Phase 5.6 獨立複核：消融實驗 (Ablation Study)
專門隔離各項修正，單獨測試：
1. Baseline: 原正式基準
2. Variant_BounceOnly: 只增加反彈幅度限制 (bounce <= 1.0 ATR)
3. Variant_TurnWeakOnly: 只增加轉弱 K 棒確認 (close < open 且 close < prev_mid)
4. Variant_KCSlopeOnly: 只調整 KC 中軌斜率條件 (kc_mid_slope < -0.000002)
5. Full_Symmetric: 多空兩端完整條件 (D 組)
"""

import os
import sys
sys.path.insert(0, os.path.abspath("."))
import json
import math
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Tuple

from tools.run_phase5_5_short_entry_diagnosis import (
    precompute_indicators,
    build_snapshot,
    classify_market_regime,
    calculate_summary_metrics,
    LEVERAGE,
    NOTIONAL,
    INITIAL_EQUITY,
    FEE_RATE,
    SLIPPAGE,
)
from core.gates.unified_pipeline_gates import ExitGate, EntryGate
from core.analytics.trade_accounting import TradeAccountingEngine


def simulate_ablation(
    df: pd.DataFrame,
    symbol: str,
    variant_mode: str,
    fee_rate: float = FEE_RATE,
    slippage_rate: float = SLIPPAGE,
    start_bar: int = 50,
    end_bar: int = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if end_bar is None:
        end_bar = len(df)

    trades = []
    rejected_signals = []
    in_pos = False
    pos = {}
    last_exit_ts = 0
    current_equity = INITIAL_EQUITY
    bar_equity = []

    total_candidate_signals = 0
    total_filtered_signals = 0

    for i in range(start_bar, end_bar):
        curr_bar = df.iloc[i]
        prev_bar = df.iloc[i-1]
        bar_ts = curr_bar['timestamp'] / 1000.0 if curr_bar['timestamp'] > 1e11 else curr_bar['timestamp']
        close_p = curr_bar['close']

        snapshot = build_snapshot(
            symbol=symbol,
            df_calc=df,
            i=i,
            quote=close_p,
            position=pos if in_pos else None
        )

        if in_pos:
            entry_p = pos['entry_price']
            side = pos['side']

            if side == 'LONG':
                pos['mfe'] = max(pos['mfe'], curr_bar['high'] - entry_p)
                pos['mae'] = max(pos['mae'], entry_p - curr_bar['low'])
                cur_move = (close_p - entry_p) / entry_p
                floating_pnl = (close_p - entry_p) * (NOTIONAL / entry_p)
            else:
                pos['mfe'] = max(pos['mfe'], entry_p - curr_bar['low'])
                pos['mae'] = max(pos['mae'], curr_bar['high'] - entry_p)
                cur_move = (entry_p - close_p) / entry_p
                floating_pnl = (entry_p - close_p) * (NOTIONAL / entry_p)

            pos['peak_roe'] = max(pos.get('peak_roe', 0.0), cur_move * LEVERAGE * 100.0)

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
                raw_exit_p = sl_price
            else:
                should_exit, exit_reason, _ = ExitGate.evaluate(
                    pos=pos,
                    market_data=snapshot
                )
                raw_exit_p = close_p

            if should_exit:
                audited = TradeAccountingEngine.settle_trade(
                    symbol=symbol,
                    exp_name=variant_mode,
                    period="VERIFY",
                    side=side,
                    entry_mode=pos.get("entry_reason", "UNKNOWN"),
                    entry_time=pos['entry_idx'],
                    exit_time=i,
                    hold_bars=i - pos['entry_idx'],
                    exit_reason=exit_reason,
                    p_entry_raw=pos['entry_price'],
                    p_exit_raw=raw_exit_p,
                    fee_rate=fee_rate,
                    slippage_rate=slippage_rate,
                    notional_target=NOTIONAL,
                    leverage=LEVERAGE,
                    current_equity=current_equity,
                    mfe_raw=pos['mfe'],
                    mae_raw=pos['mae'],
                    atr_entry=pos['atr_entry'],
                    initial_sl=pos['stop_loss_price'],
                    resumed_after_sl=False
                )

                current_equity = audited.equity_after
                t_dict = audited.to_dict()
                t_dict["regime"] = pos.get("regime", "UNKNOWN")
                t_dict["entry_reason"] = pos.get("entry_reason", "UNKNOWN")
                t_dict["entry_bar_idx"] = pos['entry_idx']
                t_dict["exit_bar_idx"] = i
                t_dict["entry_timestamp"] = pos['entry_ts']
                t_dict["exit_timestamp"] = bar_ts
                trades.append(t_dict)

                in_pos = False
                pos = {}
                last_exit_ts = bar_ts
                bar_equity.append(current_equity)
                continue
            else:
                bar_equity.append(current_equity + floating_pnl)

        else:
            bar_equity.append(current_equity)

            if (bar_ts - last_exit_ts) < 180:
                continue

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

            is_pullback_long = (
                float(snapshot.curr_k.get("low", snapshot.current_price)) <= ma5_curr * 1.002
                and snapshot.curr_k["close"] >= ma5_curr
                and snapshot.curr_k["close"] >= snapshot.curr_k["open"]
                and ma5_curr >= ma5_prev
                and snapshot.kc_middle_slope >= -0.000005
                and snapshot.curr_k["close"] >= snapshot.ema_slow
            )
            is_pullback_short = (
                float(snapshot.curr_k.get("high", snapshot.current_price)) >= ma5_curr * 0.998
                and snapshot.curr_k["close"] <= ma5_curr
                and snapshot.curr_k["close"] <= snapshot.curr_k["open"]
                and ma5_curr <= ma5_prev
                and snapshot.kc_middle_slope <= 0.000005
                and snapshot.curr_k["close"] <= snapshot.ema_slow
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
                elif is_pullback_long:
                    candidate_signal = "TREND_PULLBACK_LONG"
                elif is_cross_long:
                    candidate_signal = "ENTRY_LONG_EMA_CROSS_MID"
            elif not is_bullish_wave:
                if is_breakdown_short:
                    candidate_signal = "BREAKDOWN_SHORT"
                elif is_pullback_short:
                    candidate_signal = "TREND_PULLBACK_SHORT"
                elif is_cross_short:
                    candidate_signal = "ENTRY_SHORT_EMA_CROSS_MID"

            if candidate_signal:
                total_candidate_signals += 1

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
                    atr_val = max(snapshot.atr, close_p * 0.005)
                    row = df.iloc[i]

                    # ──────────────────────────────────────────────
                    # 消融隔離過濾規則 (Ablation Conditions)
                    # ──────────────────────────────────────────────
                    filter_passed = True
                    reject_reason = ""

                    # A. 僅增加反彈幅度限制
                    if variant_mode == "BounceOnly":
                        if entry_side == "SHORT":
                            min_15 = row.get("rolling_min_15", row["low"])
                            bounce_atr = (close_p - min_15) / atr_val
                            if bounce_atr > 1.0:
                                filter_passed = False
                                reject_reason = f"VETO_BOUNCE_OVER_1ATR ({bounce_atr:.2f} > 1.0)"

                    # B. 僅增加轉弱 K 棒確認
                    elif variant_mode == "TurnWeakOnly":
                        if entry_side == "SHORT":
                            if curr_bar["close"] >= curr_bar["open"]:
                                filter_passed = False
                                reject_reason = "VETO_SHORT_NOT_BEAR_CANDLE"
                            prev_mid = (prev_bar["open"] + prev_bar["close"]) / 2.0
                            if filter_passed and curr_bar["close"] > prev_mid:
                                filter_passed = False
                                reject_reason = "VETO_SHORT_NO_WEAK_CONFIRMATION"

                    # C. 僅調整 KC 中軌斜率條件
                    elif variant_mode == "KCSlopeOnly":
                        if entry_side == "SHORT":
                            if row["kc_middle_slope_1"] > -0.000002:
                                filter_passed = False
                                reject_reason = f"VETO_KC_MID_NOT_DOWN (slope={row['kc_middle_slope_1']:+.7f})"

                    # D. 多空兩端完整條件 (D組)
                    elif variant_mode == "Full_Symmetric":
                        if entry_side == "SHORT":
                            if row["kc_middle_slope_1"] > -0.000002:
                                filter_passed = False
                                reject_reason = "VETO_KC_MID_NOT_DOWN"
                            min_15 = row.get("rolling_min_15", row["low"])
                            if filter_passed and ((close_p - min_15) / atr_val) > 1.0:
                                filter_passed = False
                                reject_reason = "VETO_BOUNCE_OVER_1ATR"
                            if filter_passed and curr_bar["close"] >= curr_bar["open"]:
                                filter_passed = False
                                reject_reason = "VETO_SHORT_NOT_BEAR_CANDLE"
                            prev_mid = (prev_bar["open"] + prev_bar["close"]) / 2.0
                            if filter_passed and curr_bar["close"] > prev_mid:
                                filter_passed = False
                                reject_reason = "VETO_SHORT_NO_WEAK_CONFIRMATION"
                        elif entry_side == "LONG":
                            if row["kc_middle_slope_1"] < 0.000002:
                                filter_passed = False
                                reject_reason = "VETO_KC_MID_NOT_UP"
                            max_15 = row.get("rolling_max_15", row["high"])
                            if filter_passed and ((max_15 - close_p) / atr_val) > 1.0:
                                filter_passed = False
                                reject_reason = "VETO_DIP_OVER_1ATR"
                            if filter_passed and curr_bar["close"] <= curr_bar["open"]:
                                filter_passed = False
                                reject_reason = "VETO_LONG_NOT_BULL_CANDLE"
                            prev_mid = (prev_bar["open"] + prev_bar["close"]) / 2.0
                            if filter_passed and curr_bar["close"] < prev_mid:
                                filter_passed = False
                                reject_reason = "VETO_LONG_NO_STRONG_CONFIRMATION"

                    if not filter_passed:
                        total_filtered_signals += 1
                        continue

                    entry_p = close_p
                    atr = max(snapshot.atr, entry_p * 0.005)
                    sl_p = entry_p - 3.0 * atr if entry_side == "LONG" else entry_p + 3.0 * atr

                    in_pos = True
                    regime = classify_market_regime(df, i)
                    pos = {
                        "symbol": symbol,
                        "side": entry_side,
                        "entry_price": entry_p,
                        "entry_idx": i,
                        "entry_ts": bar_ts,
                        "stop_loss_price": sl_p,
                        "atr_entry": atr,
                        "peak_roe": 0.0,
                        "mfe": 0.0,
                        "mae": 0.0,
                        "entry_reason": entry_reason,
                        "regime": regime,
                    }

    metrics = calculate_summary_metrics(trades, bar_equity, total_candidate_signals, total_filtered_signals)
    return trades, metrics


def main():
    print("=" * 80)
    print("🔬 Phase 5.6 獨立消融實驗 (Ablation Study)：單一變數隔離測試")
    print("=" * 80)

    csv_path = "scratch/lobster_real_1m_history.csv"
    raw_df = pd.read_csv(csv_path)
    df_slice = raw_df.iloc[-10000:].copy().reset_index(drop=True)
    df_calc = precompute_indicators(df_slice)

    holdout_df = df_calc.iloc[7500:].copy().reset_index(drop=True)

    variants = [
        ("Baseline (原正式基準)", "Baseline"),
        ("A. 只增加反彈幅度限制 (Bounce <= 1.0 ATR)", "BounceOnly"),
        ("B. 只增加轉弱 K 棒確認 (Bear & Break Mid)", "TurnWeakOnly"),
        ("C. 只調整 KC 中軌斜率 (Slope < -0.000002)", "KCSlopeOnly"),
        ("D. 多空兩端完整條件 (Full Symmetric)", "Full_Symmetric"),
    ]

    ablation_summary = []

    for label, v_mode in variants:
        trades, metrics = simulate_ablation(
            df=holdout_df,
            symbol="龙虾/USDT",
            variant_mode=v_mode,
        )
        df_t = pd.DataFrame(trades)
        
        # 多空分項
        long_t = df_t[df_t["side"] == "LONG"] if not df_t.empty else pd.DataFrame()
        short_t = df_t[df_t["side"] == "SHORT"] if not df_t.empty else pd.DataFrame()

        long_net = long_t["net_pnl_usd"].sum() if not long_t.empty else 0.0
        short_net = short_t["net_pnl_usd"].sum() if not short_t.empty else 0.0
        
        long_pf = (long_t[long_t["net_pnl_usd"]>0]["net_pnl_usd"].sum() / abs(long_t[long_t["net_pnl_usd"]<=0]["net_pnl_usd"].sum())) if not long_t.empty and abs(long_t[long_t["net_pnl_usd"]<=0]["net_pnl_usd"].sum()) > 0 else 0.0
        short_pf = (short_t[short_t["net_pnl_usd"]>0]["net_pnl_usd"].sum() / abs(short_t[short_t["net_pnl_usd"]<=0]["net_pnl_usd"].sum())) if not short_t.empty and abs(short_t[short_t["net_pnl_usd"]<=0]["net_pnl_usd"].sum()) > 0 else 0.0

        item = {
            "variant": label,
            "trade_count": metrics["trade_count"],
            "filtered_count": metrics["filtered_signals"],
            "win_rate": metrics["win_rate"],
            "net_pnl_usd": metrics["net_pnl_usd"],
            "profit_factor": metrics["profit_factor"],
            "max_drawdown_pct": metrics["max_drawdown_pct"],
            "long_trades": len(long_t),
            "long_net_pnl": round(long_net, 2),
            "long_pf": round(long_pf, 2),
            "short_trades": len(short_t),
            "short_net_pnl": round(short_net, 2),
            "short_pf": round(short_pf, 2),
        }
        ablation_summary.append(item)
        print(f"\n[{label}]")
        print(f"  總交易數: {metrics['trade_count']:3d} 筆 (過濾: {metrics['filtered_signals']:2d}) | 勝率: {metrics['win_rate']:5.1f}% | 淨損益: {metrics['net_pnl_usd']:+7.2f} U | PF: {metrics['profit_factor']:4.2f} | MaxDD: {metrics['max_drawdown_pct']:4.2f}%")
        print(f"  - LONG  : {len(long_t):2d} 筆 | 淨損益: {long_net:+7.2f} U | PF: {long_pf:4.2f}")
        print(f"  - SHORT : {len(short_t):2d} 筆 | 淨損益: {short_net:+7.2f} U | PF: {short_pf:4.2f}")

    with open("scratch/phase5_5_diagnosis/phase5_6_ablation_summary.json", "w") as f:
        json.dump(ablation_summary, f, indent=2)
    pd.DataFrame(ablation_summary).to_csv("scratch/phase5_5_diagnosis/phase5_6_ablation_summary.csv", index=False)

    print("\n✅ 消融實驗結果已成功導出至 scratch/phase5_5_diagnosis/phase5_6_ablation_summary.csv")


if __name__ == "__main__":
    main()
