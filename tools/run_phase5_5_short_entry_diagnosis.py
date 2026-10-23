#!/usr/bin/env python3
"""
tools/run_phase5_5_short_entry_diagnosis.py
===========================================
Phase 5.5 開空位置錯誤診斷與對照回測引擎 (A/B/C/D 組)

嚴格遵守最高原則：
1. 僅離線診斷與回測，不連 Mainnet，不發真實訂單。
2. 凍結正式交易策略代碼，不修改正式進場、出場及風控參數。
3. 不為了增加交易筆數而放寬進場條件，不刪除虧損交易。
4. 僅使用當下已收線 K 棒，禁止未來函數與前視偏差。
5. 保留現有趨勢優先原則與極端行情災難性風控。
"""

import os
import sys
sys.path.insert(0, os.path.abspath("."))
import json
import math
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Tuple

from core.gates.unified_pipeline_gates import ExitGate, EntryGate
from core.services.data_pipeline import MarketDataSnapshot
from core.analytics.trade_accounting import TradeAccountingEngine, AuditedTrade

LEVERAGE = 5.0
NOTIONAL = 1000.0
INITIAL_EQUITY = 10000.0
FEE_RATE = 0.0005
SLIPPAGE = 0.0001


def precompute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df_calc = df.copy()
    close = df_calc["close"]
    high = df_calc["high"]
    low = df_calc["low"]
    volume = df_calc.get("volume", pd.Series(0.0, index=df_calc.index))

    df_calc["ma5"] = close.rolling(window=5).mean()
    df_calc["ma15"] = close.rolling(window=15).mean()
    df_calc["ema_fast"] = close.ewm(span=15, adjust=False).mean()
    df_calc["ema_slow"] = close.ewm(span=40, adjust=False).mean()
    df_calc["ma_spread_pct"] = ((df_calc["ema_fast"] - df_calc["ema_slow"]).abs() / df_calc["ema_slow"]) * 100.0

    tr = np.maximum(high - low, np.maximum((high - close.shift(1)).abs(), (low - close.shift(1)).abs()))
    df_calc["atr"] = tr.rolling(window=14).mean().bfill().fillna(close * 0.01)
    df_calc["atr_pct"] = (df_calc["atr"] / close) * 100.0

    df_calc["kc_middle"] = close.ewm(span=20, adjust=False).mean()
    df_calc["kc_upper"] = df_calc["kc_middle"] + (df_calc["atr"] * 1.5)
    df_calc["kc_lower"] = df_calc["kc_middle"] - (df_calc["atr"] * 1.5)
    df_calc["bandwidth"] = (df_calc["kc_upper"] - df_calc["kc_lower"]) / df_calc["kc_middle"]
    df_calc["bandwidth_q25"] = df_calc["bandwidth"].rolling(window=20).quantile(0.25)

    ema60_series = close.ewm(span=60, adjust=False).mean()
    df_calc["ema60"] = ema60_series
    df_calc["macro_slope_10"] = ema60_series - ema60_series.shift(10)
    df_calc["slow_slope_10"] = df_calc["ema_slow"] - df_calc["ema_slow"].shift(10)
    df_calc["fast_slope_3"] = df_calc["ema_fast"] - df_calc["ema_fast"].shift(3)
    df_calc["slow_slope_3"] = df_calc["ema_slow"] - df_calc["ema_slow"].shift(3)
    df_calc["kc_middle_slope_5"] = (df_calc["kc_middle"] - df_calc["kc_middle"].shift(5)) / 5.0
    df_calc["kc_middle_slope_1"] = (df_calc["kc_middle"] - df_calc["kc_middle"].shift(1))
    df_calc["ma5_slope_1"] = (df_calc["ma5"] - df_calc["ma5"].shift(1))

    # K 棒實體與影線
    body = (close - df_calc["open"]).abs()
    bar_range = (high - low).replace(0, 0.000001)
    df_calc["body_size"] = body
    df_calc["body_ratio"] = body / bar_range
    df_calc["upper_wick"] = high - np.maximum(close, df_calc["open"])
    df_calc["lower_wick"] = np.minimum(close, df_calc["open"]) - low
    df_calc["upper_wick_ratio"] = df_calc["upper_wick"] / bar_range
    df_calc["lower_wick_ratio"] = df_calc["lower_wick"] / bar_range

    # 滾動極值
    df_calc["rolling_min_15"] = low.rolling(window=15).min()
    df_calc["rolling_max_15"] = high.rolling(window=15).max()

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

    macro_slope_10 = float(last_row.get("macro_slope_10", 0.0))
    slow_slope_10 = float(last_row.get("slow_slope_10", 0.0))
    kc_mid_slope = float(last_row.get("kc_middle_slope_5", 0.0))

    if macro_slope_10 > 0.00001 and slow_slope_10 > 0:
        macro_trend = "BULLISH"
    elif macro_slope_10 < -0.00001 and slow_slope_10 < 0:
        macro_trend = "BEARISH"
    else:
        macro_trend = "NEUTRAL"

    peak_roe = 0.0
    if position:
        peak_roe = position.get("peak_roe", 0.0)

    sub_slice = df_calc.iloc[max(0, i-40):i+1]

    fast_slope = float(last_row.get("fast_slope_3", 0.0))
    slow_slope = float(last_row.get("slow_slope_3", 0.0))

    return MarketDataSnapshot(
        symbol=symbol,
        current_price=quote,
        curr_k=curr_k,
        prev_k=prev_k,
        ma5=curr_k["ma5"],
        ma15=curr_k["ma15"],
        ema_fast=float(last_row["ema_fast"]),
        ema_slow=float(last_row["ema_slow"]),
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


def classify_market_regime(df_calc: pd.DataFrame, i: int) -> str:
    """分類四種行情：TREND_CONTINUATION, PULLBACK, CHOP, REVERSAL"""
    row = df_calc.iloc[i]
    bw = row["bandwidth"]
    bw_q25 = row.get("bandwidth_q25", bw)
    ma_spread = row["ma_spread_pct"]
    kc_slope = abs(row["kc_middle_slope_5"])

    if bw <= bw_q25 and ma_spread < 0.15:
        return "CHOP"
    
    # 趨勢反轉
    if (row["close"] > row["kc_upper"] and row["macro_slope_10"] < 0) or (row["close"] < row["kc_lower"] and row["macro_slope_10"] > 0):
        return "REVERSAL"
    
    # 趨勢回踩
    dist_to_mid = abs(row["close"] - row["kc_middle"]) / max(row["atr"], 0.00001)
    if dist_to_mid < 0.8:
        return "PULLBACK"
    
    return "TREND_CONTINUATION"


def simulate_cohort(
    df: pd.DataFrame,
    symbol: str,
    group_mode: str = "A",
    fee_rate: float = FEE_RATE,
    slippage_rate: float = SLIPPAGE,
    start_bar: int = 50,
    end_bar: int = None,
    bounce_threshold_atr: float = 1.0,
    kc_slope_threshold: float = -0.000002,
) -> Tuple[List[Dict[str, Any]], pd.Series, List[Dict[str, Any]], Dict[str, Any]]:
    """
    通用回測引擎：
    - group_mode:
        "A": 基準策略 (Baseline)
        "B": 增加空頭結構確認 (KC中軌未轉正 + 拒絕反彈 > bounce_threshold_atr 摸空)
        "C": B 組 + 要求價格反彈後重新轉弱確認 (收黑跌破前棒實體中點/低點，拒絕直接追空/猜頂摸空)
        "D": 對稱的多單測試 (B、C 理念對稱應用於多單：KC中軌向上 + 拒絕下跌 > 1.0 ATR 摸多 + 要求收陽轉強確認棒)
    """
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
    total_opened_positions = 0
    total_closed_trades = 0

    for i in range(start_bar, end_bar):
        curr_bar = df.iloc[i]
        prev_bar = df.iloc[i-1]
        prev2_bar = df.iloc[i-2] if i >= 2 else prev_bar
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
                    exp_name=group_mode,
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
                t_dict["mae_usd"] = (pos['mae'] / entry_p) * NOTIONAL
                t_dict["mfe_usd"] = (pos['mfe'] / entry_p) * NOTIONAL
                t_dict["entry_bar_idx"] = pos['entry_idx']
                t_dict["exit_bar_idx"] = i
                t_dict["entry_timestamp"] = pos['entry_ts']
                t_dict["exit_timestamp"] = bar_ts
                trades.append(t_dict)

                total_closed_trades += 1
                in_pos = False
                pos = {}
                last_exit_ts = bar_ts
                bar_equity.append(current_equity)
                continue
            else:
                bar_equity.append(current_equity + floating_pnl)

        else:
            bar_equity.append(current_equity)

            # 冷卻期檢查 (180 秒)
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

                    # ──────────────────────────────────────────────────
                    # Phase 5.5 對照組濾網邏輯 (Filter Logic)
                    # ──────────────────────────────────────────────────
                    filter_passed = True
                    filter_reject_reason = ""

                    # B 組規則：空頭結構確認 (KC中軌未轉正 + 拒絕跌深反彈 > 1.0 ATR 追空)
                    if group_mode in ("B", "C", "D"):
                        if entry_side == "SHORT":
                            # 1. KC 中軌斜率必須未轉正 (要求嚴格下行)
                            if row["kc_middle_slope_1"] > kc_slope_threshold:
                                filter_passed = False
                                filter_reject_reason = f"VETO_KC_MID_NOT_DOWN (slope={row['kc_middle_slope_1']:+.7f} > {kc_slope_threshold})"
                            
                            # 2. 價格自過去 15 根最低點反彈幅度
                            min_15 = row.get("rolling_min_15", row["low"])
                            bounce_atr = (close_p - min_15) / atr_val
                            if filter_passed and bounce_atr > bounce_threshold_atr:
                                filter_passed = False
                                filter_reject_reason = f"VETO_SHORT_BOUNCE_OVER_LIMIT (bounce={bounce_atr:.2f} ATR > {bounce_threshold_atr})"

                    # C 組規則：B 組 + 價格反彈後重新轉弱確認 (不在反彈上行時直接追空)
                    if filter_passed and group_mode in ("C", "D"):
                        if entry_side == "SHORT":
                            # 1. 當根 K 棒必須收陰 (close < open)
                            if curr_bar["close"] >= curr_bar["open"]:
                                filter_passed = False
                                filter_reject_reason = "VETO_SHORT_NOT_BEAR_CANDLE"
                            # 2. 必須跌破前一根 K 棒實體中點或低點，證明反彈動能衰竭轉弱
                            prev_mid = (prev_bar["open"] + prev_bar["close"]) / 2.0
                            if filter_passed and curr_bar["close"] > prev_mid:
                                filter_passed = False
                                filter_reject_reason = f"VETO_SHORT_NO_WEAK_CONFIRMATION (close={curr_bar['close']} > prev_mid={prev_mid})"
                            # 3. 貼近中軌 (< 0.25 ATR) 且 MA5 向上時嚴禁摸空
                            dist_to_mid = abs(close_p - snapshot.kc_middle) / atr_val
                            if filter_passed and dist_to_mid < 0.25 and row["ma5_slope_1"] >= 0:
                                filter_passed = False
                                filter_reject_reason = "VETO_SHORT_NEAR_MID_WITH_MA5_UP"

                    # D 組規則：對稱的多單測試 (B、C 理念對稱應用於做多)
                    if filter_passed and group_mode == "D":
                        if entry_side == "LONG":
                            # 1. KC 中軌斜率必須嚴格向上
                            if row["kc_middle_slope_1"] < -kc_slope_threshold:
                                filter_passed = False
                                filter_reject_reason = f"VETO_KC_MID_NOT_UP (slope={row['kc_middle_slope_1']:+.7f} < {-kc_slope_threshold})"
                            # 2. 價格自過去 15 根最高點回撤幅度不大於 1.0 ATR (拒絕深跌接飛刀)
                            max_15 = row.get("rolling_max_15", row["high"])
                            dip_atr = (max_15 - close_p) / atr_val
                            if filter_passed and dip_atr > bounce_threshold_atr:
                                filter_passed = False
                                filter_reject_reason = f"VETO_LONG_DIP_OVER_LIMIT (dip={dip_atr:.2f} ATR > {bounce_threshold_atr})"
                            # 3. 當根 K 棒必須收陽 (close > open) 且站上前棒實體中點，確認守穩轉強
                            if filter_passed and curr_bar["close"] <= curr_bar["open"]:
                                filter_passed = False
                                filter_reject_reason = "VETO_LONG_NOT_BULL_CANDLE"
                            prev_mid = (prev_bar["open"] + prev_bar["close"]) / 2.0
                            if filter_passed and curr_bar["close"] < prev_mid:
                                filter_passed = False
                                filter_reject_reason = f"VETO_LONG_NO_STRONG_CONFIRMATION (close={curr_bar['close']} < prev_mid={prev_mid})"

                    if not filter_passed:
                        total_filtered_signals += 1
                        rejected_signals.append({
                            "bar_idx": i,
                            "timestamp": bar_ts,
                            "group": group_mode,
                            "signal": candidate_signal,
                            "side": entry_side,
                            "price": close_p,
                            "macro_trend": macro_trend,
                            "reject_reason": filter_reject_reason,
                            "atr": atr_val,
                            "kc_middle": snapshot.kc_middle,
                            "ma5_slope": row["ma5_slope_1"],
                            "kc_mid_slope": row["kc_middle_slope_1"],
                        })
                        continue

                    # 正式進場開倉
                    total_opened_positions += 1
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
    return trades, pd.Series(bar_equity), rejected_signals, metrics


def calculate_summary_metrics(trades: List[Dict[str, Any]], bar_equity: pd.Series, candidate_count: int, filtered_count: int) -> Dict[str, Any]:
    if not trades:
        return {
            "trade_count": 0, "candidate_signals": candidate_count, "filtered_signals": filtered_count,
            "win_rate": 0.0, "net_pnl_usd": 0.0, "profit_factor": 0.0,
            "max_drawdown_usd": 0.0, "max_drawdown_pct": 0.0,
            "avg_holding_bars": 0.0, "total_fees": 0.0, "total_slippage": 0.0,
        }

    df_t = pd.DataFrame(trades)
    wins = df_t[df_t["net_pnl_usd"] > 0]
    losses = df_t[df_t["net_pnl_usd"] <= 0]

    gross_profit = wins["net_pnl_usd"].sum() if not wins.empty else 0.0
    gross_loss = abs(losses["net_pnl_usd"].sum()) if not losses.empty else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)

    # 逐根 MTM 回撤
    s_equity = pd.Series(bar_equity) if not isinstance(bar_equity, pd.Series) else bar_equity
    peak_series = s_equity.cummax()
    dd_series = peak_series - s_equity
    max_dd_usd = float(dd_series.max()) if not dd_series.empty else 0.0
    max_dd_pct = float((dd_series / peak_series).max() * 100.0) if not dd_series.empty else 0.0

    return {
        "trade_count": len(trades),
        "candidate_signals": candidate_count,
        "filtered_signals": filtered_count,
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100.0, 2),
        "net_pnl_usd": round(df_t["net_pnl_usd"].sum(), 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown_usd": round(max_dd_usd, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "avg_holding_bars": round(df_t["hold_bars"].mean(), 2),
        "total_fees": round(df_t["total_fee_usd"].sum(), 2),
        "total_slippage": round(df_t["total_slip_usd"].sum(), 2),
    }


def main():
    print("=" * 80)
    print("🚀 啟動 Phase 5.5 開空位置專項診斷與對照回測 (A/B/C/D 組)...")
    print("=" * 80)

    # 載入歷史數據
    csv_path = "scratch/lobster_real_1m_history.csv"
    if not os.path.exists(csv_path):
        print(f"Error: {csv_path} not found")
        return

    raw_df = pd.read_csv(csv_path)
    # 取最近 10,000 根 K 棒，進行嚴格三段式切分
    # Study Set (研究集): 5,000 根
    # Validation Set (驗證集): 2,500 根
    # Strict Holdout Test Set (保留測試集): 2,500 根
    n_total = 10000
    df_slice = raw_df.iloc[-n_total:].copy().reset_index(drop=True)
    df_calc = precompute_indicators(df_slice)

    study_df = df_calc.iloc[:5000].copy().reset_index(drop=True)
    val_df = df_calc.iloc[5000:7500].copy().reset_index(drop=True)
    holdout_df = df_calc.iloc[7500:].copy().reset_index(drop=True)

    datasets = {
        "STUDY (5,000 bars)": study_df,
        "VALIDATION (2,500 bars)": val_df,
        "HOLDOUT (2,500 bars)": holdout_df,
    }

    groups = ["A", "B", "C", "D"]
    all_results = {}
    all_trades_list = []
    all_rejected_list = []

    for d_name, d_df in datasets.items():
        all_results[d_name] = {}
        print(f"\n" + "-" * 70)
        print(f"📊 執行資料集評估: {d_name}")
        print("-" * 70)

        for g in groups:
            trades, equity_curve, rejected, metrics = simulate_cohort(
                df=d_df,
                symbol="龙虾/USDT",
                group_mode=g,
            )
            for t in trades:
                t["dataset"] = d_name
                t["group"] = g
                all_trades_list.append(t)
            for r in rejected:
                r["dataset"] = d_name
                r["group"] = g
                all_rejected_list.append(r)

            all_results[d_name][g] = metrics
            print(f"  [{g} 組] 交易數: {metrics['trade_count']:3d} 筆 | 勝率: {metrics['win_rate']:5.1f}% | "
                  f"淨損益: {metrics['net_pnl_usd']:+8.2f} U | PF: {metrics['profit_factor']:4.2f} | "
                  f"MaxDD: {metrics['max_drawdown_pct']:4.2f}% | 均持倉: {metrics['avg_holding_bars']:4.1f} 根 | "
                  f"過濾訊號數: {metrics['filtered_signals']:3d} 個")

    # 匯出逐筆 CSV 與被拒絕清單
    os.makedirs("scratch/phase5_5_diagnosis", exist_ok=True)
    pd.DataFrame(all_trades_list).to_csv("scratch/phase5_5_diagnosis/phase5_5_trades_comparison.csv", index=False)
    pd.DataFrame(all_rejected_list).to_csv("scratch/phase5_5_diagnosis/phase5_5_rejected_signals.csv", index=False)
    with open("scratch/phase5_5_diagnosis/phase5_5_metrics_summary.json", "w") as f:
        json.dump(all_results, f, indent=2)

    print("\n✅ 已成功匯出逐筆交易對照與被拒絕清單至 scratch/phase5_5_diagnosis/")

    # ──────────────────────────────────────────────────────────────
    # 多空分開統計與四種行情分類統計 (以 HOLDOUT 保留測試集為準)
    # ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("【四、HOLDOUT 保留測試集：多空分開與行情結構深度剖析】")
    print("=" * 80)
    holdout_trades = [t for t in all_trades_list if t["dataset"] == "HOLDOUT (2,500 bars)"]
    df_ht = pd.DataFrame(holdout_trades)

    if not df_ht.empty:
        for g in groups:
            sub_g = df_ht[df_ht["group"] == g]
            print(f"\n--- [{g} 組 HOLDOUT 細部表現] ---")
            for side in ["LONG", "SHORT"]:
                sub_side = sub_g[sub_g["side"] == side]
                if sub_side.empty:
                    print(f"  {side:5s}: 無交易")
                    continue
                w = sub_side[sub_side["net_pnl_usd"] > 0]
                l = sub_side[sub_side["net_pnl_usd"] <= 0]
                gp = w["net_pnl_usd"].sum()
                gl = abs(l["net_pnl_usd"].sum())
                pf = gp / gl if gl > 0 else (99.0 if gp > 0 else 0.0)
                wr = len(w) / len(sub_side) * 100.0
                net = sub_side["net_pnl_usd"].sum()
                print(f"  {side:5s}: {len(sub_side):2d} 筆 | 勝率: {wr:5.1f}% | 淨損益: {net:+7.2f} U | PF: {pf:4.2f} | 總手續費: {sub_side['total_fee_usd'].sum():.2f} U")

            print("  [行情分類表現]")
            for reg in ["TREND_CONTINUATION", "PULLBACK", "CHOP", "REVERSAL"]:
                sub_reg = sub_g[sub_g["regime"] == reg]
                if sub_reg.empty:
                    continue
                reg_net = sub_reg["net_pnl_usd"].sum()
                reg_wr = len(sub_reg[sub_reg["net_pnl_usd"] > 0]) / len(sub_reg) * 100.0
                print(f"    - {reg:18s}: {len(sub_reg):2d} 筆 | 勝率: {reg_wr:5.1f}% | 淨損益: {reg_net:+7.2f} U")

    # ──────────────────────────────────────────────────────────────
    # 敏感度壓力測試 (Sensitivity Stress Test)
    # ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("【六、參數敏感度測試 (Parameter Sensitivity Analysis)】")
    print("=" * 80)
    print("測試參數：反彈過度門檻 (bounce_threshold_atr) 從 0.6 至 1.4 ATR 在 HOLDOUT 集之穩定性")
    sensitivity_results = []
    for b_thr in [0.6, 0.8, 1.0, 1.2, 1.4]:
        _, _, _, met = simulate_cohort(
            df=holdout_df,
            symbol="龙虾/USDT",
            group_mode="C",
            bounce_threshold_atr=b_thr,
        )
        sensitivity_results.append({
            "bounce_threshold_atr": b_thr,
            "trade_count": met["trade_count"],
            "win_rate": met["win_rate"],
            "net_pnl_usd": met["net_pnl_usd"],
            "profit_factor": met["profit_factor"],
            "max_drawdown_pct": met["max_drawdown_pct"],
        })
        print(f"  門檻: {b_thr:3.1f} ATR -> 交易數: {met['trade_count']:2d} 筆 | 勝率: {met['win_rate']:5.1f}% | "
              f"淨損益: {met['net_pnl_usd']:+7.2f} U | PF: {met['profit_factor']:4.2f} | MaxDD: {met['max_drawdown_pct']:4.2f}%")

    with open("scratch/phase5_5_diagnosis/sensitivity_results.json", "w") as f:
        json.dump(sensitivity_results, f, indent=2)

    print("\n" + "=" * 80)
    print("🏁 Phase 5.5 離線診斷與對照回測全部完成！")
    print("=" * 80)


if __name__ == "__main__":
    main()
