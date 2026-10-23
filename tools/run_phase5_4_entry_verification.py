"""
tools/run_phase5_4_entry_verification.py
========================================
Phase 5.4 進場濾網驗證與多空風險診斷全套工具

涵蓋任務：
- 任務 A：驗證 Phase 5.3 公平性（候選訊號數、被濾除數、開倉數、平倉數完整對帳，C組拒絕訊號對照反事實結果）
- 任務 B：多空進場風險特徵矩陣（依趨勢、過度延伸、影線反轉分組深度統計）
- 任務 C：A~E 五組離線策略對照
  * A 組：基準策略 (Baseline)
  * B 組：僅大週期方向濾網 (HTF Macro Filter)
  * C 組：B 組 + KC 過度延伸 (>0.5 ATR) + 反向長影線 (>45% range)
  * D 組：C 組 + 盤整/低帶寬死水過濾 (Low Bandwidth Squeeze / Chop Filter)
  * E 組：C 組 + 缺乏動能/小實體過濾 (Weak Momentum / Low Body & Volume Filter)
- 任務 D：嚴格三段式時間切分驗證 (Study 5,000 bars -> Validation 2,500 bars -> Strict Holdout 2,500 bars)
- 任務 E：多空分開報表、成本敏感度壓力測試與 CSV 輸出
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
    df_calc["atr_change_5"] = (df_calc["atr"] - df_calc["atr"].shift(5)) / df_calc["atr"].shift(5) * 100.0

    df_calc["kc_middle"] = close.ewm(span=20, adjust=False).mean()
    df_calc["kc_upper"] = df_calc["kc_middle"] + (df_calc["atr"] * 1.5)
    df_calc["kc_lower"] = df_calc["kc_middle"] - (df_calc["atr"] * 1.5)
    df_calc["bandwidth"] = (df_calc["kc_upper"] - df_calc["kc_lower"]) / df_calc["kc_middle"]
    df_calc["bandwidth_change_5"] = (df_calc["bandwidth"] - df_calc["bandwidth"].shift(5)) / df_calc["bandwidth"].shift(5) * 100.0
    df_calc["bandwidth_q25"] = df_calc["bandwidth"].rolling(window=20).quantile(0.25)

    ema60_series = close.ewm(span=60, adjust=False).mean()
    df_calc["ema60"] = ema60_series
    df_calc["macro_slope_10"] = ema60_series - ema60_series.shift(10)
    df_calc["slow_slope_10"] = df_calc["ema_slow"] - df_calc["ema_slow"].shift(10)
    df_calc["fast_slope_3"] = df_calc["ema_fast"] - df_calc["ema_fast"].shift(3)
    df_calc["slow_slope_3"] = df_calc["ema_slow"] - df_calc["ema_slow"].shift(3)
    df_calc["kc_middle_slope_5"] = (df_calc["kc_middle"] - df_calc["kc_middle"].shift(5)) / 5.0

    # K 棒實體與影線
    body = (close - df_calc["open"]).abs()
    bar_range = (high - low).replace(0, 0.000001)
    df_calc["body_size"] = body
    df_calc["body_ratio"] = body / bar_range
    df_calc["upper_wick"] = high - np.maximum(close, df_calc["open"])
    df_calc["lower_wick"] = np.minimum(close, df_calc["open"]) - low
    df_calc["upper_wick_ratio"] = df_calc["upper_wick"] / bar_range
    df_calc["lower_wick_ratio"] = df_calc["lower_wick"] / bar_range

    # 量能特徵
    df_calc["vol_ma5"] = volume.rolling(5).mean().bfill().replace(0, 1.0)
    df_calc["rel_volume"] = volume / df_calc["vol_ma5"]

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


def simulate_full_path(
    df: pd.DataFrame,
    symbol: str,
    group_mode: str = "A",
    fee_rate: float = FEE_RATE,
    slippage_rate: float = SLIPPAGE,
    start_bar: int = 50,
    end_bar: int = None
) -> Tuple[List[Dict[str, Any]], pd.Series, List[Dict[str, Any]], Dict[str, int]]:
    """
    通用回測引擎：
    - group_mode:
        "A": 基準策略 (Phase 5.3 正式基準)
        "B": 僅大週期方向 (BULLISH 禁做空, BEARISH 禁做多)
        "C": B + KC過度延伸 (>0.5 ATR) + 反向影線 (>45% range)
        "D": C + 盤整/低帶寬過濾 (bandwidth <= 20棒下四分位數 且 ma_spread < 0.15%)
        "E": C + 弱動能過濾 (breakout 實體 < 0.35 ATR 或 相對量能 < 0.8)
    返回:
      (已平倉交易清單, 逐根 K 線 MTM 權益, 被拒絕訊號清單, 計數審計字典)
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

    # 審計計數器
    total_candidate_signals = 0
    total_filtered_signals = 0
    total_opened_positions = 0
    total_closed_trades = 0

    for i in range(start_bar, end_bar):
        curr_bar = df.iloc[i]
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

            # 更新 MFE / MAE
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

            # 3 ATR 硬止損悲觀檢查 (盤中跌破/突破優先離場)
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
                    # 濾網規則判斷 (Filter Rules Evaluation)
                    # ──────────────────────────────────────────────────
                    filter_passed = True
                    filter_reject_reason = ""

                    # B 組規則：大週期方向
                    if group_mode in ("B", "C", "D", "E"):
                        if entry_side == "LONG" and macro_trend == "BEARISH":
                            filter_passed = False
                            filter_reject_reason = "VETO_MACRO_BEARISH_BLOCK_LONG"
                        elif entry_side == "SHORT" and macro_trend == "BULLISH":
                            filter_passed = False
                            filter_reject_reason = "VETO_MACRO_BULLISH_BLOCK_SHORT"

                    # C 組規則：KC過度延伸 + 反向長影線
                    if filter_passed and group_mode in ("C", "D", "E"):
                        if "BREAKOUT" in candidate_signal or "BREAKDOWN" in candidate_signal:
                            if entry_side == "LONG":
                                ext_dist = (close_p - snapshot.kc_upper) / atr_val
                                if ext_dist > 0.5:
                                    filter_passed = False
                                    filter_reject_reason = f"VETO_OVER_EXTENDED_UPPER_KC ({ext_dist:.2f} ATR > 0.5)"
                                elif row.get("upper_wick_ratio", 0) > 0.45:
                                    filter_passed = False
                                    filter_reject_reason = f"VETO_LONG_UPPER_WICK ({row.get('upper_wick_ratio',0):.2f} > 0.45)"
                            else:
                                ext_dist = (snapshot.kc_lower - close_p) / atr_val
                                if ext_dist > 0.5:
                                    filter_passed = False
                                    filter_reject_reason = f"VETO_OVER_EXTENDED_LOWER_KC ({ext_dist:.2f} ATR > 0.5)"
                                elif row.get("lower_wick_ratio", 0) > 0.45:
                                    filter_passed = False
                                    filter_reject_reason = f"VETO_SHORT_LOWER_WICK ({row.get('lower_wick_ratio',0):.2f} > 0.45)"

                    # D 組規則：C 組 + 盤整/低帶寬死水過濾
                    if filter_passed and group_mode in ("D", "E"):
                        # 若帶寬處於低位且 EMA 快慢線發散率 < 0.15%，判定為震盪死水
                        bw_q25 = row.get("bandwidth_q25", snapshot.bandwidth)
                        if snapshot.bandwidth <= bw_q25 and snapshot.ma_spread_pct < 0.15:
                            filter_passed = False
                            filter_reject_reason = f"VETO_CHOP_LOW_BANDWIDTH (bw={snapshot.bandwidth:.4f} <= q25={bw_q25:.4f}, spread={snapshot.ma_spread_pct:.2f}%)"

                    # E 組規則：D 組 + 突破缺乏動能過濾
                    if filter_passed and group_mode == "E":
                        if "BREAKOUT" in candidate_signal or "BREAKDOWN" in candidate_signal:
                            body_size = row.get("body_size", 0.0)
                            rel_vol = row.get("rel_volume", 1.0)
                            if body_size < 0.35 * atr_val:
                                filter_passed = False
                                filter_reject_reason = f"VETO_WEAK_BODY ({body_size:.6f} < 0.35 ATR)"
                            elif rel_vol < 0.8:
                                filter_passed = False
                                filter_reject_reason = f"VETO_LOW_VOLUME (rel_vol={rel_vol:.2f} < 0.8)"

                    if not filter_passed:
                        total_filtered_signals += 1
                        ext_val = (close_p - snapshot.kc_upper) / atr_val if entry_side == "LONG" else (snapshot.kc_lower - close_p) / atr_val
                        wick_val = row.get("upper_wick_ratio", 0.0) if entry_side == "LONG" else row.get("lower_wick_ratio", 0.0)
                        rejected_signals.append({
                            "bar_idx": i,
                            "timestamp": bar_ts,
                            "group": group_mode,
                            "signal": candidate_signal,
                            "side": entry_side,
                            "price": close_p,
                            "macro_trend": macro_trend,
                            "kc_ext_atr": ext_val,
                            "wick_ratio": wick_val,
                            "reject_reason": filter_reject_reason,
                        })
                        continue

                    # 通過濾網，正式開倉
                    total_opened_positions += 1
                    entry_p = close_p
                    atr = max(snapshot.atr, entry_p * 0.005)
                    sl_p = entry_p - 3.0 * atr if entry_side == "LONG" else entry_p + 3.0 * atr

                    in_pos = True
                    pos = {
                        "symbol": symbol,
                        "side": entry_side,
                        "entry_price": entry_p,
                        "entry_idx": i,
                        "entry_ts": bar_ts,
                        "stop_loss_price": sl_p,
                        "atr_entry": atr,
                        "mfe": 0.0,
                        "mae": 0.0,
                        "peak_roe": 0.0,
                        "amount": NOTIONAL,
                        "leverage": LEVERAGE,
                        "entry_reason": candidate_signal,
                    }

    audit_counts = {
        "candidate_signals": total_candidate_signals,
        "filtered_signals": total_filtered_signals,
        "opened_positions": total_opened_positions,
        "closed_trades": total_closed_trades,
        "still_in_pos": 1 if in_pos else 0,
    }

    return trades, pd.Series(bar_equity), rejected_signals, audit_counts


def compute_metrics(trades: List[Dict[str, Any]], bar_equity: pd.Series) -> Dict[str, Any]:
    if not trades:
        return {
            "total_trades": 0, "net_pnl": 0.0, "return_pct": 0.0,
            "win_rate": 0.0, "profit_factor": 0.0, "max_drawdown_closed_pct": 0.0,
            "max_drawdown_mtm_pct": 0.0, "avg_bars": 0.0, "avg_net_pnl": 0.0,
            "total_friction": 0.0, "sl_count": 0, "sl_rate": 0.0,
            "long_metrics": {}, "short_metrics": {}
        }

    df_t = pd.DataFrame(trades)
    net_pnl = df_t["net_pnl_usd"].sum()
    total_friction = df_t["total_fee_usd"].sum() + df_t["total_slip_usd"].sum()

    wins = df_t[df_t["net_pnl_usd"] > 0]
    losses = df_t[df_t["net_pnl_usd"] <= 0]
    win_rate = (len(wins) / len(df_t)) * 100.0

    gp = wins["net_pnl_usd"].sum() if not wins.empty else 0.0
    gl = abs(losses["net_pnl_usd"].sum()) if not losses.empty else 0.0
    pf = (gp / gl) if gl > 0 else (999.0 if gp > 0 else 0.0)

    # 已平倉回撤
    closed_eq = [INITIAL_EQUITY]
    for p in df_t["net_pnl_usd"]:
        closed_eq.append(closed_eq[-1] + p)
    s_c = pd.Series(closed_eq)
    dd_c = (s_c - s_c.cummax()) / s_c.cummax() * 100.0
    max_dd_c = abs(dd_c.min())

    # MTM 回撤
    dd_mtm = (bar_equity - bar_equity.cummax()) / bar_equity.cummax() * 100.0
    max_dd_mtm = abs(dd_mtm.min()) if not bar_equity.empty else max_dd_c

    # 止損率
    sl_trades = df_t[df_t["exit_reason"] == "CIRCUIT_BREAKER_HARD_SL"]
    sl_rate = (len(sl_trades) / len(df_t)) * 100.0 if len(df_t) > 0 else 0.0

    # LONG / SHORT
    longs = df_t[df_t["side"] == "LONG"]
    shorts = df_t[df_t["side"] == "SHORT"]

    def calc_side(sdf: pd.DataFrame) -> Dict[str, Any]:
        if sdf.empty:
            return {"trades": 0, "net_pnl": 0.0, "win_rate": 0.0, "pf": 0.0, "sl_rate": 0.0, "avg_pnl": 0.0}
        w = sdf[sdf["net_pnl_usd"] > 0]
        l = sdf[sdf["net_pnl_usd"] <= 0]
        g_p = w["net_pnl_usd"].sum() if not w.empty else 0.0
        g_l = abs(l["net_pnl_usd"].sum()) if not l.empty else 0.0
        pf_s = (g_p / g_l) if g_l > 0 else (999.0 if g_p > 0 else 0.0)
        sl_s = len(sdf[sdf["exit_reason"] == "CIRCUIT_BREAKER_HARD_SL"])
        return {
            "trades": len(sdf),
            "net_pnl": sdf["net_pnl_usd"].sum(),
            "win_rate": len(w) / len(sdf) * 100.0,
            "pf": pf_s,
            "avg_pnl": sdf["net_pnl_usd"].mean(),
            "sl_rate": (sl_s / len(sdf)) * 100.0,
        }

    return {
        "total_trades": len(df_t),
        "net_pnl": net_pnl,
        "return_pct": (net_pnl / INITIAL_EQUITY) * 100.0,
        "win_rate": win_rate,
        "profit_factor": pf,
        "max_drawdown_closed_pct": max_dd_c,
        "max_drawdown_mtm_pct": max_dd_mtm,
        "avg_bars": df_t["hold_bars"].mean(),
        "avg_net_pnl": df_t["net_pnl_usd"].mean(),
        "total_friction": total_friction,
        "sl_count": len(sl_trades),
        "sl_rate": sl_rate,
        "long_metrics": calc_side(longs),
        "short_metrics": calc_side(shorts),
    }


def analyze_task_b_risk_cohorts(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    任務 B：多空進場風險特徵矩陣
    建立每一筆候選進場訊號在不同環境下的前瞻收益與止損率 (無未來偏差)
    """
    records = []
    last_exit_ts = 0

    for i in range(50, len(df) - 16):
        curr_bar = df.iloc[i]
        bar_ts = curr_bar['timestamp'] / 1000.0 if curr_bar['timestamp'] > 1e11 else curr_bar['timestamp']

        if (bar_ts - last_exit_ts) < 180:
            continue

        snapshot = build_snapshot(symbol, df, i, curr_bar['close'])
        ma5_curr = snapshot.curr_k.get("ma5", snapshot.current_price)
        ma5_prev = snapshot.prev_k.get("ma5", ma5_curr)
        ma15_curr = snapshot.curr_k.get("ma15", snapshot.ma15)
        macro_trend = snapshot.macro_trend

        is_breakout_long = (snapshot.current_price > snapshot.kc_upper and ma5_curr > ma5_prev)
        is_breakdown_short = (snapshot.current_price < snapshot.kc_lower and ma5_curr < ma5_prev)
        is_cross_long = (ma5_curr >= ma15_curr and ma5_curr > ma5_prev and curr_bar['close'] > curr_bar['open'] and curr_bar['close'] >= snapshot.kc_middle)
        is_cross_short = (ma5_curr <= ma15_curr and ma5_curr < ma5_prev and curr_bar['close'] < curr_bar['open'] and curr_bar['close'] <= snapshot.kc_middle)

        candidate_signal = None
        if is_breakout_long: candidate_signal = "BREAKOUT_LONG"
        elif is_cross_long: candidate_signal = "ENTRY_LONG_EMA_CROSS_MID"
        elif is_breakdown_short: candidate_signal = "BREAKDOWN_SHORT"
        elif is_cross_short: candidate_signal = "ENTRY_SHORT_EMA_CROSS_MID"

        if candidate_signal:
            can_enter, _, meta = EntryGate.evaluate(candidate_signal, snapshot)
            if can_enter:
                side = meta.get("side", "LONG" if "LONG" in candidate_signal else "SHORT")
                p_entry = curr_bar['close']
                atr_val = max(snapshot.atr, p_entry * 0.005)

                ext_dist = (p_entry - snapshot.kc_upper) / atr_val if side == "LONG" else (snapshot.kc_lower - p_entry) / atr_val
                wick_ratio = curr_bar.get("upper_wick_ratio", 0.0) if side == "LONG" else curr_bar.get("lower_wick_ratio", 0.0)

                # 分組分類 (Cohorts)
                # 1. 趨勢狀態
                trend_cohort = "ALIGNED" if (side == "LONG" and macro_trend == "BULLISH") or (side == "SHORT" and macro_trend == "BEARISH") else "COUNTER_OR_NEUTRAL"
                # 2. 通道延伸
                ext_cohort = "OVER_EXTENDED (>0.5 ATR)" if ext_dist > 0.5 else "NORMAL_EXT (<=0.5 ATR)"
                # 3. 影線狀態
                wick_cohort = "HEAVY_WICK (>0.45)" if wick_ratio > 0.45 else "CLEAN_WICK (<=0.45)"
                # 4. 帶寬震盪
                bw_q25 = curr_bar.get("bandwidth_q25", snapshot.bandwidth)
                chop_cohort = "SQUEEZE_CHOP" if (snapshot.bandwidth <= bw_q25 and snapshot.ma_spread_pct < 0.15) else "TRENDING_EXPANSION"

                # 未來 K 棒表現
                fut_bars = df.iloc[i+1 : i+16]
                p_1 = fut_bars.iloc[0]['close']
                p_3 = fut_bars.iloc[min(2, len(fut_bars)-1)]['close']
                p_5 = fut_bars.iloc[min(4, len(fut_bars)-1)]['close']
                p_10 = fut_bars.iloc[min(9, len(fut_bars)-1)]['close']
                p_15 = fut_bars.iloc[-1]['close']

                ret_1 = (p_1 - p_entry) / p_entry if side == "LONG" else (p_entry - p_1) / p_entry
                ret_3 = (p_3 - p_entry) / p_entry if side == "LONG" else (p_entry - p_3) / p_entry
                ret_5 = (p_5 - p_entry) / p_entry if side == "LONG" else (p_entry - p_5) / p_entry
                ret_10 = (p_10 - p_entry) / p_entry if side == "LONG" else (p_entry - p_10) / p_entry
                ret_15 = (p_15 - p_entry) / p_entry if side == "LONG" else (p_entry - p_15) / p_entry

                if side == "LONG":
                    mfe_15 = (fut_bars['high'].max() - p_entry) / p_entry
                    mae_15 = (p_entry - fut_bars['low'].min()) / p_entry
                else:
                    mfe_15 = (p_entry - fut_bars['low'].min()) / p_entry
                    mae_15 = (fut_bars['high'].max() - p_entry) / p_entry

                sl_hit_15 = (mae_15 >= (3.0 * atr_val / p_entry))

                records.append({
                    "bar_idx": i,
                    "timestamp": bar_ts,
                    "signal": candidate_signal,
                    "side": side,
                    "price": p_entry,
                    "atr": atr_val,
                    "ext_dist": ext_dist,
                    "wick_ratio": wick_ratio,
                    "macro_trend": macro_trend,
                    "trend_cohort": trend_cohort,
                    "ext_cohort": ext_cohort,
                    "wick_cohort": wick_cohort,
                    "chop_cohort": chop_cohort,
                    "ret_1_pct": ret_1 * 100.0,
                    "ret_3_pct": ret_3 * 100.0,
                    "ret_5_pct": ret_5 * 100.0,
                    "ret_10_pct": ret_10 * 100.0,
                    "ret_15_pct": ret_15 * 100.0,
                    "mfe_15_pct": mfe_15 * 100.0,
                    "mae_15_pct": mae_15 * 100.0,
                    "sl_hit_15": sl_hit_15,
                })

                last_exit_ts = bar_ts + 180

    return pd.DataFrame(records)


def main():
    print("=" * 95)
    print("🚀 啟動 Phase 5.4 進場濾網驗證與多空風險診斷")
    print("=" * 95)

    os.makedirs("scratch/phase5_4_audit", exist_ok=True)

    # 載入龍蝦真實 10,000 根 1m 數據
    df_lob = pd.read_csv("scratch/lobster_real_1m_history.csv").sort_values("timestamp").reset_index(drop=True)
    df_lob = df_lob.iloc[-10000:].reset_index(drop=True)
    df_lob = precompute_indicators(df_lob)

    # ─────────────────────────────────────────────────────────────
    # 任務 D：嚴格三段式時間切分
    # ─────────────────────────────────────────────────────────────
    # 研究/訓練集 (Train/Study: 5,000 bars, 50%)
    # 驗證集 (Validation: 2,500 bars, 25%)
    # 最終保留測試集 (Strict Holdout / Test: 2,500 bars, 25%)
    df_study = df_lob.iloc[:5000].reset_index(drop=True)
    df_val = df_lob.iloc[5000:7500].reset_index(drop=True)
    df_holdout = df_lob.iloc[7500:].reset_index(drop=True)

    print("\n【時間序列三段切分架構】")
    print(f"  1. 研究集 (Study Set):     K棒 0 ~ 5,000 (共 5,000 根 1m，時間範圍: {df_study['timestamp'].iloc[0]} ~ {df_study['timestamp'].iloc[-1]})")
    print(f"  2. 驗證集 (Validation Set): K棒 5,000 ~ 7,500 (共 2,500 根 1m，時間範圍: {df_val['timestamp'].iloc[0]} ~ {df_val['timestamp'].iloc[-1]})")
    print(f"  3. 最終保留測試集 (Strict Holdout): K棒 7,500 ~ 10,000 (共 2,500 根 1m，時間範圍: {df_holdout['timestamp'].iloc[0]} ~ {df_holdout['timestamp'].iloc[-1]})")
    print("  * 承諾：最終保留測試集在先前研究中完全封存，絕不用於挑選參數或調整規則！")

    # ─────────────────────────────────────────────────────────────
    # 任務 A：驗證 Phase 5.3 實驗公平性與完整對帳
    # ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 95)
    print("【任務 A：驗證 Phase 5.3 實驗公平性與完整訊號對帳表】")
    print("=" * 95)

    lob_is_6k = df_lob.iloc[:6000].reset_index(drop=True)
    lob_oos_4k = df_lob.iloc[6000:].reset_index(drop=True)

    sim_a_is, eq_a_is, rej_a_is, counts_a_is = simulate_full_path(lob_is_6k, "LOBSTERUSDT", "A")
    sim_b_is, eq_b_is, rej_b_is, counts_b_is = simulate_full_path(lob_is_6k, "LOBSTERUSDT", "B")
    sim_c_is, eq_c_is, rej_c_is, counts_c_is = simulate_full_path(lob_is_6k, "LOBSTERUSDT", "C")

    sim_a_oos, eq_a_oos, rej_a_oos, counts_a_oos = simulate_full_path(lob_oos_4k, "LOBSTERUSDT", "A")
    sim_b_oos, eq_b_oos, rej_b_oos, counts_b_oos = simulate_full_path(lob_oos_4k, "LOBSTERUSDT", "B")
    sim_c_oos, eq_c_oos, rej_c_oos, counts_c_oos = simulate_full_path(lob_oos_4k, "LOBSTERUSDT", "C")

    # 輸出訊號對帳表
    print(f"{'資料集 / 組別':<22}{'候選訊號總數':<14}{'濾網拒絕訊號數':<16}{'實際開倉數':<12}{'已平倉交易數':<14}{'期末持倉數':<12}")
    print("-" * 95)
    for name, c in [
        ("【樣本內】A 組 (基準)", counts_a_is),
        ("【樣本內】B 組 (大週期)", counts_b_is),
        ("【樣本內】C 組 (假突破)", counts_c_is),
        ("【樣本外】A 組 (基準)", counts_a_oos),
        ("【樣本外】B 組 (大週期)", counts_b_oos),
        ("【樣本外】C 組 (假突破)", counts_c_oos),
    ]:
        print(f"{name:<20}{c['candidate_signals']:<14}{c['filtered_signals']:<16}{c['opened_positions']:<12}{c['closed_trades']:<14}{c['still_in_pos']:<12}")

    # 導出 C 組拒絕訊號之反事實模擬分析
    df_c_rej_oos = pd.DataFrame(rej_c_oos)
    df_c_rej_oos.to_csv("scratch/phase5_4_audit/c_group_rejected_signals_oos.csv", index=False)
    print(f"\n✅ 已導出 C 組樣本外被拒絕之 {len(df_c_rej_oos)} 筆訊號至 scratch/phase5_4_audit/c_group_rejected_signals_oos.csv")

    # ─────────────────────────────────────────────────────────────
    # 任務 B：多空進場風險特徵矩陣
    # ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 95)
    print("【任務 B：多空進場風險特徵深度統計 (依群組 Cohort 細分)】")
    print("=" * 95)

    df_cohorts = analyze_task_b_risk_cohorts(df_lob, "LOBSTERUSDT")
    df_cohorts.to_csv("scratch/phase5_4_audit/lobster_entry_risk_cohorts.csv", index=False)
    print(f"✅ 已成功計算 {len(df_cohorts)} 筆進場訊號之全維度特徵矩陣")

    for side in ["LONG", "SHORT"]:
        sub_s = df_cohorts[df_cohorts["side"] == side]
        print(f"\n>>> 【{side} 單方向進場風險統計】 (共 {len(sub_s)} 筆訊號):")
        
        # 1. 通道過度延伸群組
        print("  1. 按 KC 通道外過度延伸分組 (Extension Cohort):")
        for ext_name, grp in sub_s.groupby("ext_cohort"):
            print(f"    * {ext_name:<26} (筆數: {len(grp):>3}): 3棒收益 {grp['ret_3_pct'].mean():+6.2f}%, 15棒收益 {grp['ret_15_pct'].mean():+6.2f}%, 15棒被打穿 3 ATR 止損率: {grp['sl_hit_15'].mean()*100:5.1f}%")

        # 2. 影線長度群組
        print("  2. 按反向影線比例分組 (Wick Cohort):")
        for wick_name, grp in sub_s.groupby("wick_cohort"):
            print(f"    * {wick_name:<26} (筆數: {len(grp):>3}): 3棒收益 {grp['ret_3_pct'].mean():+6.2f}%, 15棒收益 {grp['ret_15_pct'].mean():+6.2f}%, 15棒被打穿 3 ATR 止損率: {grp['sl_hit_15'].mean()*100:5.1f}%")

        # 3. 帶寬震盪死水群組
        print("  3. 按帶寬震盪/擴張分組 (Chop / Squeeze Cohort):")
        for chop_name, grp in sub_s.groupby("chop_cohort"):
            print(f"    * {chop_name:<26} (筆數: {len(grp):>3}): 3棒收益 {grp['ret_3_pct'].mean():+6.2f}%, 15棒收益 {grp['ret_15_pct'].mean():+6.2f}%, 15棒被打穿 3 ATR 止損率: {grp['sl_hit_15'].mean()*100:5.1f}%")

    # ─────────────────────────────────────────────────────────────
    # 任務 C & D：A ~ E 五組策略在三段數據上的離線對照
    # ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 95)
    print("【任務 C & D：A ~ E 五組離線策略三段式嚴格回測對照】")
    print("=" * 95)

    segments = [
        ("1. 研究集 (Study Set 5,000 bars)", df_study),
        ("2. 驗證集 (Validation Set 2,500 bars)", df_val),
        ("3. 最終保留測試集 (Strict Holdout 2,500 bars)", df_holdout),
    ]

    group_keys = ["A", "B", "C", "D", "E"]
    group_labels = {
        "A": "A 組 (基準策略)",
        "B": "B 組 (僅大週期)",
        "C": "C 組 (大週期+延伸+影線)",
        "D": "D 組 (C+低帶寬死水過濾)",
        "E": "E 組 (C+弱動能過濾)",
    }

    all_segment_metrics = {}

    for seg_name, s_df in segments:
        print(f"\n==================== 【{seg_name}】 ====================")
        print(f"{'組別':<24}{'淨損益 (USDT)':<16}{'勝率':<8}{'PF':<7}{'交易數':<8}{'回撤(MTM)':<12}{'單筆淨利':<10}{'止損率':<8}{'LONG 損益':<12}{'SHORT 損益':<12}")
        print("-" * 115)

        for gk in group_keys:
            t_list, eq_s, rej_list, counts = simulate_full_path(s_df, "LOBSTERUSDT", gk)
            m = compute_metrics(t_list, eq_s)
            all_segment_metrics[(seg_name, gk)] = m

            # 導出 CSV
            csv_path = f"scratch/phase5_4_audit/trades_{seg_name[:2]}_{gk}.csv"
            pd.DataFrame(t_list).to_csv(csv_path, index=False)

            glab = group_labels[gk]
            print(f"{glab:<22}{m['net_pnl']:+9.2f} ({m['return_pct']:+5.2f}%)  {m['win_rate']:5.1f}%  {m['profit_factor']:5.2f}  {m['total_trades']:<8}{m['max_drawdown_mtm_pct']:5.2f}%      {m['avg_net_pnl']:+6.2f}    {m['sl_rate']:5.1f}%    {m['long_metrics']['net_pnl']:+9.2f}   {m['short_metrics']['net_pnl']:+9.2f}")

    # ─────────────────────────────────────────────────────────────
    # 任務 D.5：最終保留測試集 (Strict Holdout) 成本敏感度壓力測試
    # ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 95)
    print("【任務 D.5：最終保留測試集 (Strict Holdout) 成本敏感度壓力測試】")
    print("=" * 95)

    print(f"{'成本情境':<24}{'評估組別':<22}{'淨損益 (USDT)':<16}{'PF':<8}{'勝率':<8}{'MTM 回撤':<12}{'LONG 損益':<12}{'SHORT 損益':<12}")
    print("-" * 105)

    for slip_val, sc_name in [(0.0001, "基準成本 (0.01% Slip)"), (0.0003, "較高滑價 (0.03% Slip)"), (0.0005, "壓力滑價 (0.05% Slip)")]:
        for gk in ["A", "C", "D"]:
            t_h, eq_h, _, _ = simulate_full_path(df_holdout, "LOBSTERUSDT", gk, slippage_rate=slip_val)
            m_h = compute_metrics(t_h, eq_h)
            glab = group_labels[gk]
            print(f"{sc_name:<22}{glab:<20}{m_h['net_pnl']:+9.2f} ({m_h['return_pct']:+5.2f}%)  {m_h['profit_factor']:5.2f}   {m_h['win_rate']:5.1f}%   {m_h['max_drawdown_mtm_pct']:5.2f}%       {m_h['long_metrics']['net_pnl']:+9.2f}   {m_h['short_metrics']['net_pnl']:+9.2f}")


if __name__ == "__main__":
    main()
