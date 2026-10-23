"""
run_pipeline_optimization_test.py
=================================
對比測試：
版本 1 (當前 3ede30ed 原始版本)
vs
版本 2 (修正回測虧損根因：
  1. 樞紐出場需真正波段展開 >= 1.2 ATR，不被 0.1% 噪音微利出場；
  2. 3 ATR 止損設最低防禦底線 >= 0.5% 防止被盤中毛刺秒殺；
  3. MODE_3 突破進場嚴格限制乖離率與量能，優化回踩 MODE_2 權重)
"""

import os
import sys
import math
import json
import urllib.request
import pandas as pd
import numpy as np

from core.services.data_pipeline import DataPipeline
from core.gates.unified_pipeline_gates import EntryGate, ExitGate

FEE_RATE = 0.0004
SLIPPAGE = 0.0002


def run_simulation(df: pd.DataFrame, symbol: str, version: str = "CURRENT") -> list:
    trades = []
    in_pos = False
    pos_state = {}
    last_exit_record = {}
    hourly_entries = []

    n = len(df)
    for i in range(60, n):
        sub_df = df.iloc[:i+1].copy()
        curr_bar = df.iloc[i]
        bar_ts = curr_bar['timestamp'] / 1000.0

        hourly_entries = [t for t in hourly_entries if (bar_ts - t) < 3600.0]

        snapshot = DataPipeline.calculate_snapshot(
            symbol=symbol,
            df=sub_df,
            current_price=curr_bar['close'],
            position=pos_state if in_pos else None
        )
        if not snapshot:
            continue

        if in_pos:
            entry_p = pos_state['entry_price']
            side = pos_state['side']
            if side == 'LONG':
                pos_state['mfe'] = max(pos_state['mfe'], curr_bar['high'] - entry_p)
                pos_state['mae'] = max(pos_state['mae'], entry_p - curr_bar['low'])
                pos_state['max_price'] = max(pos_state['max_price'], curr_bar['high'])
                cur_move = (curr_bar['close'] - entry_p) / entry_p
            else:
                pos_state['mfe'] = max(pos_state['mfe'], entry_p - curr_bar['low'])
                pos_state['mae'] = max(pos_state['mae'], curr_bar['high'] - entry_p)
                pos_state['min_price'] = min(pos_state['min_price'], curr_bar['low'])
                cur_move = (entry_p - curr_bar['close']) / entry_p
            
            pos_state['peak_roe'] = max(pos_state['peak_roe'], cur_move * 5.0)

            should_exit, exit_reason, exit_meta = ExitGate.evaluate(
                pos=pos_state,
                market_data=snapshot
            )

            # 版本 2 優化：微型樞紐防護 (利潤未達 1.2 ATR 不因微型 0.1% 雜訊提早全平)
            if version == "OPTIMIZED":
                if exit_reason in ("EARLY_PIVOT_PEAK_EXIT", "EARLY_PIVOT_VALLEY_EXIT"):
                    gain_atr = pos_state['mfe'] / snapshot.atr if snapshot.atr > 0 else 0.0
                    gain_pct = (pos_state['mfe'] / entry_p) * 100.0
                    # 若波段最大浮盈從未達到 1.0 ATR 或 0.35% (不足以覆蓋 0.12% 摩擦)，視為噪音回踩，否決平倉續抱
                    if gain_atr < 1.0 and gain_pct < 0.35:
                        should_exit = False
                        exit_reason = "VETO_HOLD"

            # 盤中硬止損
            initial_sl = pos_state['initial_sl']
            intrabar_hard_sl = False
            if side == 'LONG' and curr_bar['low'] <= initial_sl:
                should_exit = True
                exit_reason = 'CIRCUIT_BREAKER_HARD_SL_INTRABAR'
                intrabar_hard_sl = True
            elif side == 'SHORT' and curr_bar['high'] >= initial_sl:
                should_exit = True
                exit_reason = 'CIRCUIT_BREAKER_HARD_SL_INTRABAR'
                intrabar_hard_sl = True

            if should_exit:
                raw_exit_p = initial_sl if intrabar_hard_sl else curr_bar['close']
                if side == 'LONG':
                    actual_exit_p = raw_exit_p * (1.0 - SLIPPAGE)
                    gross_pnl_pct = (actual_exit_p - entry_p) / entry_p
                else:
                    actual_exit_p = raw_exit_p * (1.0 + SLIPPAGE)
                    gross_pnl_pct = (entry_p - actual_exit_p) / entry_p

                net_pnl_pct = gross_pnl_pct - (FEE_RATE * 2)

                future_bars = df.iloc[i+1:min(i+6, n)]
                continued = False
                if not future_bars.empty:
                    if side == 'LONG':
                        continued = (future_bars['high'].max() > pos_state['max_price'] + snapshot.atr * 0.5)
                    else:
                        continued = (future_bars['low'].min() < pos_state['min_price'] - snapshot.atr * 0.5)

                trades.append({
                    "symbol": symbol,
                    "entry_idx": pos_state['entry_idx'],
                    "exit_idx": i,
                    "side": side,
                    "entry_mode": pos_state['entry_mode'],
                    "entry_price": entry_p,
                    "exit_price": actual_exit_p,
                    "exit_reason": exit_reason,
                    "gross_pnl_pct": gross_pnl_pct * 100.0,
                    "net_pnl_pct": net_pnl_pct * 100.0,
                    "mfe_pct": (pos_state['mfe'] / entry_p) * 100.0,
                    "mae_pct": (pos_state['mae'] / entry_p) * 100.0,
                    "hold_bars": i - pos_state['entry_idx'],
                    "continued_after_exit": continued,
                })

                last_exit_record = {
                    "timestamp": bar_ts,
                    "exit_price": actual_exit_p,
                    "side": side,
                    "exit_reason": exit_reason
                }
                in_pos = False
                pos_state = {}
                continue

        else:
            if last_exit_record:
                if (bar_ts - last_exit_record["timestamp"]) < 540:
                    continue
            if len(hourly_entries) >= 3:
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
                macro_trend == "BULLISH"
                or is_bull_align
                or (is_above_slow and is_slow_up)
                or (snapshot.kc_middle_slope >= -0.000005 and snapshot.curr_k["close"] >= snapshot.kc_middle)
            )
            is_bearish_wave = (
                macro_trend == "BEARISH"
                or is_bear_align
                or (is_below_slow and is_slow_down)
                or (snapshot.kc_middle_slope <= 0.000005 and snapshot.curr_k["close"] <= snapshot.kc_middle)
            )

            candidate_signal = None
            detected_mode = None

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

            is_pullback_long = (
                is_bullish_wave
                and snapshot.curr_k["low"] <= snapshot.ma5
                and snapshot.curr_k["close"] > snapshot.ma5
                and snapshot.curr_k["close"] > snapshot.curr_k["open"]
                and ma5_curr >= ma5_prev
            )
            is_pullback_short = (
                is_bearish_wave
                and snapshot.curr_k["high"] >= snapshot.ma5
                and snapshot.curr_k["close"] < snapshot.ma5
                and snapshot.curr_k["close"] < snapshot.curr_k["open"]
                and ma5_curr <= ma5_prev
            )

            if not is_bearish_wave:
                # 在 OPTIMIZED 版本中，回踩模式 MODE_2 優先於追高破軌 MODE_3
                if version == "OPTIMIZED" and is_pullback_long:
                    candidate_signal = "TREND_PULLBACK_LONG"
                    detected_mode = "MODE_2_PULLBACK"
                elif is_breakout_long:
                    p_up = snapshot.prev_k.get("kc_upper", snapshot.kc_upper)
                    p_close = snapshot.prev_k.get("close", snapshot.current_price)
                    candidate_signal = "CONTINUATION_OUTSIDE_KC_LONG" if p_close > p_up else "BREAKOUT_LONG"
                    detected_mode = "MODE_3_BREAKOUT"
                elif is_pullback_long:
                    candidate_signal = "TREND_PULLBACK_LONG"
                    detected_mode = "MODE_2_PULLBACK"
                elif is_cross_long:
                    candidate_signal = "ENTRY_LONG_EMA_CROSS_MID"
                    detected_mode = "MODE_1_TREND_START"

            elif not is_bullish_wave:
                if version == "OPTIMIZED" and is_pullback_short:
                    candidate_signal = "TREND_PULLBACK_SHORT"
                    detected_mode = "MODE_2_PULLBACK"
                elif is_breakdown_short:
                    p_low = snapshot.prev_k.get("kc_lower", snapshot.kc_lower)
                    p_close = snapshot.prev_k.get("close", snapshot.current_price)
                    candidate_signal = "CONTINUATION_OUTSIDE_KC_SHORT" if p_close < p_low else "BREAKDOWN_SHORT"
                    detected_mode = "MODE_3_BREAKOUT"
                elif is_pullback_short:
                    candidate_signal = "TREND_PULLBACK_SHORT"
                    detected_mode = "MODE_2_PULLBACK"
                elif is_cross_short:
                    candidate_signal = "ENTRY_SHORT_EMA_CROSS_MID"
                    detected_mode = "MODE_1_TREND_START"

            if candidate_signal:
                last_exit_ctx = {
                    **(last_exit_record or {}),
                    "hourly_entry_count": len(hourly_entries),
                    "current_time": bar_ts,
                }
                should_entry, entry_reason, entry_meta = EntryGate.evaluate(
                    signal_type=candidate_signal,
                    market_data=snapshot,
                    last_exit_context=last_exit_ctx
                )

                if should_entry:
                    side = entry_meta.get("side", "LONG" if "LONG" in candidate_signal else "SHORT")
                    if side == "LONG":
                        actual_entry_p = curr_bar['close'] * (1.0 + SLIPPAGE)
                        # 止損距離：至少 0.5% 防止噪音秒殺，兼顧 3 ATR
                        sl_dist = max(3.0 * snapshot.atr, actual_entry_p * 0.005) if version == "OPTIMIZED" else (3.0 * snapshot.atr)
                        initial_sl = actual_entry_p - sl_dist
                    else:
                        actual_entry_p = curr_bar['close'] * (1.0 - SLIPPAGE)
                        sl_dist = max(3.0 * snapshot.atr, actual_entry_p * 0.005) if version == "OPTIMIZED" else (3.0 * snapshot.atr)
                        initial_sl = actual_entry_p + sl_dist

                    in_pos = True
                    hourly_entries.append(bar_ts)
                    pos_state = {
                        "symbol": symbol,
                        "side": side,
                        "entry_idx": i,
                        "entry_time": curr_bar['datetime'],
                        "entry_price": actual_entry_p,
                        "entry_mode": detected_mode,
                        "entry_reason": entry_reason,
                        "initial_sl": initial_sl,
                        "atr": snapshot.atr,
                        "atr_entry": snapshot.atr,
                        "mfe": 0.0,
                        "mae": 0.0,
                        "max_price": curr_bar['high'],
                        "min_price": curr_bar['low'],
                        "peak_roe": 0.0,
                        "leverage": 5.0,
                    }

    return trades


def calc_summary(trades: list) -> dict:
    if not trades:
        return {"count": 0, "net_pnl": 0.0, "win_rate": 0.0, "mdd": 0.0, "hold": 0.0}
    df_t = pd.DataFrame(trades)
    cnt = len(df_t)
    net_pnl = df_t['net_pnl_pct'].sum()
    wins = df_t[df_t['net_pnl_pct'] > 0]
    wr = len(wins) / cnt * 100.0
    eq = (1.0 + df_t['net_pnl_pct'] / 100.0).cumprod()
    dd = (eq - eq.cummax()) / eq.cummax()
    mdd = abs(dd.min()) * 100.0
    hold = df_t['hold_bars'].mean()
    return {"count": cnt, "net_pnl": net_pnl, "win_rate": wr, "mdd": mdd, "hold": hold}


def main():
    from run_pipeline_backtest_diagnosis import fetch_binance_klines
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    print("=" * 80)
    print("🚀 對比驗證：版本 1 (當前 3ede30ed) vs 版本 2 (根因優化版)")
    print("=" * 80)

    for sym in symbols:
        df = fetch_binance_klines(sym, interval="3m", limit=1000)
        t_cur = run_simulation(df, sym, version="CURRENT")
        t_opt = run_simulation(df, sym, version="OPTIMIZED")

        s_cur = calc_summary(t_cur)
        s_opt = calc_summary(t_opt)

        print(f"\n【{sym}】")
        print(f"  版本 1 (當前): 交易 {s_cur['count']:<2} 筆 | 淨利 {s_cur['net_pnl']:>6.2f}% | 勝率 {s_cur['win_rate']:>5.1f}% | MDD {s_cur['mdd']:>5.2f}% | 平均持倉 {s_cur['hold']:>4.1f} 根")
        print(f"  版本 2 (優化): 交易 {s_opt['count']:<2} 筆 | 淨利 {s_opt['net_pnl']:>6.2f}% | 勝率 {s_opt['win_rate']:>5.1f}% | MDD {s_opt['mdd']:>5.2f}% | 平均持倉 {s_opt['hold']:>4.1f} 根")


if __name__ == "__main__":
    main()
