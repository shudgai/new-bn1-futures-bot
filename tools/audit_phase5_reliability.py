"""
tools/audit_phase5_reliability.py
==================================
Phase 5.2 回測可信度審計與樣本外風險分析工具
完全符合最高優先原則：
1. 100% 離線，禁止連接 Binance Mainnet，不發送任何真實訂單。
2. 凍結策略參數，不調整進出場門檻。
3. 使用 TradeAccountingEngine 嚴格帳務稽核，確保恆等式成立：
   Net PnL == Raw Gross PnL - Total Friction Cost
4. 計算 MTM (Mark-to-Market 逐根 K 線) 最大回撤 與 已平倉最大回撤。
5. 包含：
   - 任務 A：程式正確性與對比驗證
   - 任務 B：績效統計與完整核對 (LONG/SHORT, 出場原因, 連續虧損)
   - 任務 C：樣本外分段分析、Top 20 虧損明細、交易路徑分叉 vs 固定進場對照
   - 任務 D：成本敏感度壓力測試 (0.01%, 0.03%, 0.05% 滑價)
"""

import os
import sys
sys.path.insert(0, os.path.abspath("."))
import json
import math
import importlib.util
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Tuple

# 載入舊版 ExitGate (Phase 5 備份)
spec_old = importlib.util.spec_from_file_location(
    "old_pipeline_gates",
    "scratch/backup_phase5_exit_refactor/unified_pipeline_gates.py"
)
old_mod = importlib.util.module_from_spec(spec_old)
spec_old.loader.exec_module(old_mod)
OldExitGate = old_mod.ExitGate

# 載入新版 ExitGate (Phase 5 重構後)
from core.gates.unified_pipeline_gates import ExitGate as NewExitGate
from core.gates.unified_pipeline_gates import EntryGate
from core.services.data_pipeline import MarketDataSnapshot
from core.analytics.trade_accounting import TradeAccountingEngine, AuditedTrade

LEVERAGE = 5.0
NOTIONAL = 1000.0
INITIAL_EQUITY = 10000.0


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


def simulate_full_path(
    df: pd.DataFrame,
    symbol: str,
    exit_gate_cls: Any,
    label: str,
    fee_rate: float = 0.0005,
    slippage_rate: float = 0.0001,
    start_bar: int = 50,
    end_bar: int = None
) -> Tuple[List[Dict[str, Any]], pd.Series]:
    """
    動態路徑回測：依實盤邏輯執行，平倉時間點直接影響後續冷卻與進場路徑
    返回：(成交記錄清單, 逐根 K 線 MTM 權益曲線)
    """
    if end_bar is None:
        end_bar = len(df)

    trades = []
    in_pos = False
    pos = {}
    last_exit_ts = 0
    current_equity = INITIAL_EQUITY
    bar_equity = []

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

            # 3 ATR 硬止損悲觀檢查 (同一根 K 線內若低點跌破止損，優先觸發止損)
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
                should_exit, exit_reason, _ = exit_gate_cls.evaluate(
                    pos=pos,
                    market_data=snapshot
                )
                raw_exit_p = close_p

            if should_exit:
                audited = TradeAccountingEngine.settle_trade(
                    symbol=symbol,
                    exp_name=label,
                    period="AUDIT",
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
                trades.append(t_dict)

                in_pos = False
                pos = {}
                last_exit_ts = bar_ts
                bar_equity.append(current_equity)
                continue
            else:
                # 仍持倉中，記錄 MTM 浮動權益
                bar_equity.append(current_equity + floating_pnl)

        else:
            bar_equity.append(current_equity)

            # 檢查冷卻時間 (180秒)
            if (bar_ts - last_exit_ts) < 180:
                continue

            # 產生候選訊號
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
                    entry_p = close_p
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
                        "entry_reason": candidate_signal,
                    }

    return trades, pd.Series(bar_equity)


def compute_comprehensive_metrics(trades: List[Dict[str, Any]], bar_equity: pd.Series, initial_equity: float = INITIAL_EQUITY) -> Dict[str, Any]:
    if not trades:
        return {
            "total_trades": 0, "net_pnl": 0.0, "return_pct": 0.0,
            "win_rate": 0.0, "profit_factor": 0.0, "max_drawdown_closed_pct": 0.0,
            "max_drawdown_mtm_pct": 0.0, "avg_bars": 0.0, "avg_net_pnl": 0.0,
            "avg_fee": 0.0, "avg_slip": 0.0, "avg_friction": 0.0,
            "raw_gross_pnl": 0.0, "total_fees": 0.0, "total_slip": 0.0, "funding_fees": 0.0,
            "max_consecutive_losses": 0, "max_consecutive_loss_amount": 0.0,
            "long_metrics": {}, "short_metrics": {}, "exit_reason_breakdown": {},
            "avg_mfe_usd": 0.0, "avg_mae_usd": 0.0,
        }

    df_t = pd.DataFrame(trades)
    net_pnl = df_t["net_pnl_usd"].sum()
    raw_gross_pnl = df_t["raw_gross_pnl_usd"].sum()
    total_fees = df_t["total_fee_usd"].sum()
    total_slip = df_t["total_slip_usd"].sum()
    funding_fees = df_t.get("funding_fee_usd", pd.Series(0.0, index=df_t.index)).sum()
    total_friction = total_fees + total_slip + funding_fees

    # 嚴格驗證恆等式
    assert math.isclose(net_pnl, raw_gross_pnl - total_friction, abs_tol=1e-4), "淨損益恆等式不吻合！"

    wins = df_t[df_t["net_pnl_usd"] > 0]
    losses = df_t[df_t["net_pnl_usd"] <= 0]
    win_rate = (len(wins) / len(df_t)) * 100.0

    gross_profit = wins["net_pnl_usd"].sum() if not wins.empty else 0.0
    gross_loss = abs(losses["net_pnl_usd"].sum()) if not losses.empty else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    # 已平倉權益回撤
    closed_equity = [initial_equity]
    for p in df_t["net_pnl_usd"]:
        closed_equity.append(closed_equity[-1] + p)
    s_closed = pd.Series(closed_equity)
    dd_closed = (s_closed - s_closed.cummax()) / s_closed.cummax() * 100.0
    max_dd_closed = abs(dd_closed.min())

    # MTM 逐根權益回撤
    dd_mtm = (bar_equity - bar_equity.cummax()) / bar_equity.cummax() * 100.0
    max_dd_mtm = abs(dd_mtm.min()) if not bar_equity.empty else max_dd_closed

    # 連續虧損統計
    max_cons_losses = 0
    cur_cons_losses = 0
    max_cons_loss_amt = 0.0
    cur_cons_loss_amt = 0.0

    for p in df_t["net_pnl_usd"]:
        if p <= 0:
            cur_cons_losses += 1
            cur_cons_loss_amt += abs(p)
            if cur_cons_losses > max_cons_losses:
                max_cons_losses = cur_cons_losses
            if cur_cons_loss_amt > max_cons_loss_amt:
                max_cons_loss_amt = cur_cons_loss_amt
        else:
            cur_cons_losses = 0
            cur_cons_loss_amt = 0.0

    # LONG / SHORT 指標
    def get_side_metrics(side_df: pd.DataFrame) -> Dict[str, Any]:
        if side_df.empty:
            return {"trades": 0, "net_pnl": 0.0, "win_rate": 0.0, "pf": 0.0, "max_dd_closed": 0.0}
        w = side_df[side_df["net_pnl_usd"] > 0]
        l = side_df[side_df["net_pnl_usd"] <= 0]
        gp = w["net_pnl_usd"].sum() if not w.empty else 0.0
        gl = abs(l["net_pnl_usd"].sum()) if not l.empty else 0.0
        pf = (gp / gl) if gl > 0 else (999.0 if gp > 0 else 0.0)
        c_eq = [initial_equity]
        for p in side_df["net_pnl_usd"]:
            c_eq.append(c_eq[-1] + p)
        s_c = pd.Series(c_eq)
        dd = (s_c - s_c.cummax()) / s_c.cummax() * 100.0
        return {
            "trades": len(side_df),
            "net_pnl": side_df["net_pnl_usd"].sum(),
            "win_rate": len(w) / len(side_df) * 100.0,
            "pf": pf,
            "max_dd_closed": abs(dd.min())
        }

    long_metrics = get_side_metrics(df_t[df_t["side"] == "LONG"])
    short_metrics = get_side_metrics(df_t[df_t["side"] == "SHORT"])

    # 出場原因統計
    exit_breakdown = {}
    for reason, grp in df_t.groupby("exit_reason"):
        exit_breakdown[reason] = {
            "trades": len(grp),
            "net_pnl": grp["net_pnl_usd"].sum(),
            "avg_pnl": grp["net_pnl_usd"].mean(),
            "win_rate": (len(grp[grp["net_pnl_usd"] > 0]) / len(grp)) * 100.0
        }

    return {
        "total_trades": len(df_t),
        "initial_equity": initial_equity,
        "final_equity": initial_equity + net_pnl,
        "net_pnl": net_pnl,
        "return_pct": (net_pnl / initial_equity) * 100.0,
        "raw_gross_pnl": raw_gross_pnl,
        "total_fees": total_fees,
        "total_slip": total_slip,
        "funding_fees": funding_fees,
        "total_friction": total_friction,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "avg_win": wins["net_pnl_usd"].mean() if not wins.empty else 0.0,
        "avg_loss": losses["net_pnl_usd"].mean() if not losses.empty else 0.0,
        "max_drawdown_closed_pct": max_dd_closed,
        "max_drawdown_mtm_pct": max_dd_mtm,
        "avg_bars": df_t["hold_bars"].mean(),
        "avg_net_pnl": df_t["net_pnl_usd"].mean(),
        "avg_fee": total_fees / len(df_t),
        "avg_slip": total_slip / len(df_t),
        "avg_friction": total_friction / len(df_t),
        "avg_mfe_usd": df_t["mfe_usd"].mean(),
        "avg_mae_usd": df_t["mae_usd"].mean(),
        "max_consecutive_losses": max_cons_losses,
        "max_consecutive_loss_amount": max_cons_loss_amt,
        "long_metrics": long_metrics,
        "short_metrics": short_metrics,
        "exit_reason_breakdown": exit_breakdown,
    }


def analyze_fixed_entry_comparison(df: pd.DataFrame, symbol: str) -> Dict[str, Any]:
    """
    固定進場對照 (Fixed Entry Matched Exits)：
    使用相同的進場點，分別測試 OldExitGate 與 NewExitGate 在該持倉下的出場表現。
    精確區分：
    - 同一根 K 棒相同原因出場 (Identical)
    - 新版延後出場 (Delayed)
    - 新版提前出場 (Earlier)
    - 出場原因改變但根數相同
    """
    # 產生一組統一的進場點
    entry_points = []
    last_exit_ts = 0

    for i in range(50, len(df)):
        curr_bar = df.iloc[i]
        bar_ts = curr_bar['timestamp'] / 1000.0 if curr_bar['timestamp'] > 1e11 else curr_bar['timestamp']

        if (bar_ts - last_exit_ts) < 300:
            continue

        snapshot = build_snapshot(symbol, df, i, curr_bar['close'])
        ma5_curr = snapshot.curr_k.get("ma5", snapshot.current_price)
        ma5_prev = snapshot.prev_k.get("ma5", ma5_curr)
        ma15_curr = snapshot.curr_k.get("ma15", snapshot.ma15)
        macro_trend = snapshot.macro_trend

        is_breakout_long = (snapshot.current_price > snapshot.kc_upper and ma5_curr > ma5_prev and macro_trend != "BEARISH")
        is_breakdown_short = (snapshot.current_price < snapshot.kc_lower and ma5_curr < ma5_prev and macro_trend != "BULLISH")

        sig = "BREAKOUT_LONG" if is_breakout_long else ("BREAKDOWN_SHORT" if is_breakdown_short else None)
        if sig:
            can_enter, _, meta = EntryGate.evaluate(sig, snapshot)
            if can_enter:
                side = meta.get("side", "LONG" if "LONG" in sig else "SHORT")
                entry_p = curr_bar['close']
                atr = max(snapshot.atr, entry_p * 0.005)
                entry_points.append({
                    "entry_idx": i,
                    "entry_ts": bar_ts,
                    "side": side,
                    "entry_price": entry_p,
                    "atr": atr,
                    "sl_price": entry_p - 3.0 * atr if side == "LONG" else entry_p + 3.0 * atr,
                    "signal": sig,
                })
                last_exit_ts = bar_ts + 600  # 間隔至少 10 根避免過度重疊

    # 對每個固定進場點分別模擬 Old vs New
    matched_results = []
    for ep in entry_points:
        def run_single(exit_gate_cls: Any) -> Dict[str, Any]:
            pos = {
                "symbol": symbol, "side": ep["side"], "entry_price": ep["entry_price"],
                "entry_idx": ep["entry_idx"], "stop_loss_price": ep["sl_price"],
                "atr_entry": ep["atr"], "mfe": 0.0, "mae": 0.0, "peak_roe": 0.0,
                "amount": NOTIONAL, "leverage": LEVERAGE,
            }
            for k in range(ep["entry_idx"] + 1, min(ep["entry_idx"] + 120, len(df))):
                bar = df.iloc[k]
                snap = build_snapshot(symbol, df, k, bar['close'], pos)
                if pos["side"] == "LONG":
                    pos["mfe"] = max(pos["mfe"], bar["high"] - ep["entry_price"])
                    pos["mae"] = max(pos["mae"], ep["entry_price"] - bar["low"])
                    cur_m = (bar['close'] - ep["entry_price"]) / ep["entry_price"]
                else:
                    pos["mfe"] = max(pos["mfe"], ep["entry_price"] - bar["low"])
                    pos["mae"] = max(pos["mae"], bar["high"] - ep["entry_price"])
                    cur_m = (ep["entry_price"] - bar['close']) / ep["entry_price"]
                pos["peak_roe"] = max(pos["peak_roe"], cur_m * LEVERAGE * 100.0)

                hit_sl = (pos["side"] == "LONG" and bar["low"] <= pos["stop_loss_price"]) or (pos["side"] == "SHORT" and bar["high"] >= pos["stop_loss_price"])
                if hit_sl:
                    return {"exit_idx": k, "hold_bars": k - ep["entry_idx"], "exit_reason": "CIRCUIT_BREAKER_HARD_SL", "exit_price": pos["stop_loss_price"]}
                should_exit, reason, _ = exit_gate_cls.evaluate(pos, snap)
                if should_exit:
                    return {"exit_idx": k, "hold_bars": k - ep["entry_idx"], "exit_reason": reason, "exit_price": bar['close']}

            # 到達最大限制
            return {"exit_idx": min(ep["entry_idx"] + 120, len(df)-1), "hold_bars": 120, "exit_reason": "MAX_BARS", "exit_price": df.iloc[min(ep["entry_idx"] + 120, len(df)-1)]["close"]}

        r_old = run_single(OldExitGate)
        r_new = run_single(NewExitGate)

        # 計算損益
        qty = NOTIONAL / ep["entry_price"]
        pnl_old = (r_old["exit_price"] - ep["entry_price"]) * qty if ep["side"] == "LONG" else (ep["entry_price"] - r_old["exit_price"]) * qty
        pnl_new = (r_new["exit_price"] - ep["entry_price"]) * qty if ep["side"] == "LONG" else (ep["entry_price"] - r_new["exit_price"]) * qty

        diff_type = "IDENTICAL"
        if r_new["hold_bars"] > r_old["hold_bars"]:
            diff_type = "DELAYED_EXIT"
        elif r_new["hold_bars"] < r_old["hold_bars"]:
            diff_type = "EARLIER_EXIT"
        elif r_new["exit_reason"] != r_old["exit_reason"]:
            diff_type = "REASON_CHANGED_SAME_BAR"

        matched_results.append({
            "entry_idx": ep["entry_idx"],
            "side": ep["side"],
            "old_hold": r_old["hold_bars"],
            "new_hold": r_new["hold_bars"],
            "old_reason": r_old["exit_reason"],
            "new_reason": r_new["exit_reason"],
            "old_pnl": pnl_old,
            "new_pnl": pnl_new,
            "pnl_diff": pnl_new - pnl_old,
            "diff_type": diff_type,
        })

    df_m = pd.DataFrame(matched_results)
    type_counts = df_m["diff_type"].value_counts().to_dict()
    delayed = df_m[df_m["diff_type"] == "DELAYED_EXIT"]

    return {
        "total_matched_entries": len(df_m),
        "type_counts": type_counts,
        "delayed_count": len(delayed),
        "delayed_avg_pnl_diff": delayed["pnl_diff"].mean() if not delayed.empty else 0.0,
        "delayed_win_diff_count": len(delayed[delayed["pnl_diff"] > 0]),
        "delayed_loss_diff_count": len(delayed[delayed["pnl_diff"] < 0]),
        "total_pnl_old": df_m["old_pnl"].sum(),
        "total_pnl_new": df_m["new_pnl"].sum(),
        "net_pnl_improvement": df_m["new_pnl"].sum() - df_m["old_pnl"].sum(),
    }


def run_comprehensive_audit():
    print("=" * 90)
    print("🔍 啟動 Phase 5.2 回測可信度審計與樣本外風險分析")
    print("=" * 90)

    # 1. 載入龍蝦資料
    df_lob = pd.read_csv("scratch/lobster_real_1m_history.csv").sort_values("timestamp").reset_index(drop=True)
    df_lob = df_lob.iloc[-10000:].reset_index(drop=True)
    df_lob = precompute_indicators(df_lob)

    lob_is = df_lob.iloc[:6000].reset_index(drop=True)
    lob_oos = df_lob.iloc[6000:].reset_index(drop=True)

    # 樣本外切分 2 個子區段 (各 2,000 根)
    lob_oos_part1 = lob_oos.iloc[:2000].reset_index(drop=True)
    lob_oos_part2 = lob_oos.iloc[2000:].reset_index(drop=True)

    # 2. 載入 Golden Dataset
    with open("data/golden_dataset_2500_bars.json") as f:
        golden_raw = json.load(f)

    golden_dfs = {}
    cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore']
    for sym, klines in golden_raw.items():
        d = pd.DataFrame(klines, columns=cols)
        for c in ['open', 'high', 'low', 'close', 'volume']:
            d[c] = d[c].astype(float)
        golden_dfs[sym] = precompute_indicators(d)

    # ─────────────────────────────────────────────────────────────
    # 任務 A & B: 基準回測與核對 (0.05% fee, 0.01% slip)
    # ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 90)
    print("【任務 A & B：可重現基準回測與績效核對】(Taker Fee 0.05%, Slip 0.01%)")
    print("=" * 90)

    datasets = [
        ("LOBSTER 1m 樣本內 (6,000 bars)", {"LOBSTERUSDT": lob_is}),
        ("LOBSTER 1m 樣本外 (4,000 bars)", {"LOBSTERUSDT": lob_oos}),
    ]

    all_results = {}

    for d_name, d_dict in datasets:
        for ver, gate_cls in [("Old", OldExitGate), ("New", NewExitGate)]:
            trades_combined = []
            eq_combined = []
            for s, df_sub in d_dict.items():
                t, eq = simulate_full_path(df_sub, s, gate_cls, f"{d_name}_{ver}", fee_rate=0.0005, slippage_rate=0.0001)
                trades_combined.extend(t)
                eq_combined = eq  # 單幣種直接取

            m = compute_comprehensive_metrics(trades_combined, eq_combined)
            all_results[f"{d_name}_{ver}"] = (m, trades_combined)

            print(f"\n>>> 資料集: {d_name} | 版本: {ver}")
            print(f"  初始權益: ${m['initial_equity']:.2f} -> 最終權益: ${m['final_equity']:.2f}")
            print(f"  已實現淨損益: {m['net_pnl']:+.2f} USDT ({m['return_pct']:+.2f}%) | 純毛損益: {m['raw_gross_pnl']:+.2f} USDT")
            print(f"  摩擦成本: 總手續費 {m['total_fees']:.2f} USDT + 總滑點 {m['total_slip']:.2f} USDT = {m['total_friction']:.2f} USDT | 資金費率: 0.00 (未計入)")
            print(f"  交易筆數: {m['total_trades']} | 勝率: {m['win_rate']:.1f}% | PF: {m['profit_factor']:.2f}")
            print(f"  平均獲利: +{m['avg_win']:.2f} USDT | 平均虧損: {m['avg_loss']:.2f} USDT | 平均持倉: {m['avg_bars']:.1f} 根")
            print(f"  最大回撤: 已平倉 {m['max_drawdown_closed_pct']:.2f}% | MTM 逐根 {m['max_drawdown_mtm_pct']:.2f}%")
            print(f"  最大連續虧損: {m['max_consecutive_losses']} 次 | 連續虧損金額: {m['max_consecutive_loss_amount']:.2f} USDT")
            print(f"  LONG:  {m['long_metrics']['trades']} 筆, 勝率 {m['long_metrics']['win_rate']:.1f}%, 淨損益 {m['long_metrics']['net_pnl']:+.2f} USDT, PF {m['long_metrics']['pf']:.2f}, 回撤 {m['long_metrics']['max_dd_closed']:.2f}%")
            print(f"  SHORT: {m['short_metrics']['trades']} 筆, 勝率 {m['short_metrics']['win_rate']:.1f}%, 淨損益 {m['short_metrics']['net_pnl']:+.2f} USDT, PF {m['short_metrics']['pf']:.2f}, 回撤 {m['short_metrics']['max_dd_closed']:.2f}%")
            print(f"  平均 MFE: ${m['avg_mfe_usd']:.2f} | 平均 MAE: ${m['avg_mae_usd']:.2f}")
            print("  出場原因分佈 (筆數 / 淨損益 / 平均單筆 / 勝率):")
            for r, r_stat in sorted(m["exit_reason_breakdown"].items(), key=lambda x: -x[1]["trades"]):
                print(f"    - {r}: {r_stat['trades']} 筆 | 淨利: {r_stat['net_pnl']:+.2f} USDT | 平均: {r_stat['avg_pnl']:+.2f} USDT | 勝率: {r_stat['win_rate']:.1f}%")

    # ─────────────────────────────────────────────────────────────
    # 任務 C: 樣本外深入剖析 (分段、Top 20 最大虧損、路徑分叉對照)
    # ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 90)
    print("【任務 C：樣本外風險深入剖析】")
    print("=" * 90)

    # 1. 龍蝦樣本外時間分段 (Part 1 前 2000 根 vs Part 2 後 2000 根)
    print("\n--- 1. 龍蝦樣本外時間分段 (Part 1 vs Part 2) ---")
    for part_name, p_df in [("Part 1 (前半段 2,000 bars)", lob_oos_part1), ("Part 2 (後半段 2,000 bars)", lob_oos_part2)]:
        t_old, eq_old = simulate_full_path(p_df, "LOBSTERUSDT", OldExitGate, "Old")
        t_new, eq_new = simulate_full_path(p_df, "LOBSTERUSDT", NewExitGate, "New")
        m_old = compute_comprehensive_metrics(t_old, eq_old)
        m_new = compute_comprehensive_metrics(t_new, eq_new)

        print(f"\n[{part_name}]")
        print(f"  Old: 淨利 {m_old['net_pnl']:+.2f} USDT | PF {m_old['profit_factor']:.2f} | 交易數 {m_old['total_trades']} | 回撤 {m_old['max_drawdown_closed_pct']:.2f}% | LONG: {m_old['long_metrics']['net_pnl']:+.2f} | SHORT: {m_old['short_metrics']['net_pnl']:+.2f}")
        print(f"  New: 淨利 {m_new['net_pnl']:+.2f} USDT | PF {m_new['profit_factor']:.2f} | 交易數 {m_new['total_trades']} | 回撤 {m_new['max_drawdown_closed_pct']:.2f}% | LONG: {m_new['long_metrics']['net_pnl']:+.2f} | SHORT: {m_new['short_metrics']['net_pnl']:+.2f}")

    # 2. 樣本外虧損前 20 筆交易 (Top 20 Losers in New Version OOS)
    print("\n--- 2. 龍蝦樣本外 Top 20 最大虧損交易明細 (新版) ---")
    _, new_oos_trades = all_results["LOBSTER 1m 樣本外 (4,000 bars)_New"]
    df_new_oos = pd.DataFrame(new_oos_trades)
    top20_losers = df_new_oos.sort_values("net_pnl_usd").head(20)

    print(f"{'序號':<4}{'方向':<6}{'進場原因':<24}{'出場原因':<28}{'持倉':<6}{'毛損益':<10}{'手續費':<8}{'滑點':<8}{'淨損益':<10}{'MAE(USDT)':<10}")
    print("-" * 115)
    for idx, (_, r) in enumerate(top20_losers.iterrows(), 1):
        print(f"{idx:<4}{r['side']:<6}{str(r['entry_reason'])[:22]:<24}{str(r['exit_reason'])[:26]:<28}{r['hold_bars']:<6}{r['raw_gross_pnl_usd']:+8.2f}  {r['total_fee_usd']:6.2f}  {r['total_slip_usd']:6.2f}  {r['net_pnl_usd']:+8.2f}  {r['mae_usd']:8.2f}")

    # 統計 Top 20 虧損特徵
    top20_longs = len(top20_losers[top20_losers["side"] == "LONG"])
    top20_sl = len(top20_losers[top20_losers["exit_reason"] == "CIRCUIT_BREAKER_HARD_SL"])
    top20_waterfall = len(top20_losers[top20_losers["exit_reason"] == "EXIT_BY_EXTREME_WATERFALL"])
    print(f"\nTop 20 虧損特徵統計: 多單佔比 {top20_longs}/20 ({top20_longs*5}%), 硬止損佔比 {top20_sl}/20, 極端瀑布佔比 {top20_waterfall}/20")

    # 3. 固定進場對照 (Fixed Entry Comparison)
    print("\n--- 3. 隔離出場決策：固定進場對照 (Fixed Entry Matched Exits) ---")
    matched_audit = analyze_fixed_entry_comparison(df_lob, "LOBSTERUSDT")
    print(f"  總匹配進場數: {matched_audit['total_matched_entries']} 筆")
    print(f"  行為分類分佈: {matched_audit['type_counts']}")
    print(f"  新版延後出場次數: {matched_audit['delayed_count']} 筆")
    print(f"  延後出場中獲得更佳損益: {matched_audit['delayed_win_diff_count']} 筆 | 延後出場反而損益變差: {matched_audit['delayed_loss_diff_count']} 筆")
    print(f"  延後出場平均單筆損益差異: {matched_audit['delayed_avg_pnl_diff']:+.2f} USDT")
    print(f"  固定進場下 Old 總損益: {matched_audit['total_pnl_old']:+.2f} USDT | New 總損益: {matched_audit['total_pnl_new']:+.2f} USDT")
    print(f"  固定進場純出場改進淨貢獻: {matched_audit['net_pnl_improvement']:+.2f} USDT")

    # ─────────────────────────────────────────────────────────────
    # 任務 D: 成本敏感度壓力測試 (0.01%, 0.03%, 0.05% 滑價)
    # ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 90)
    print("【任務 D：成本敏感度壓力測試】")
    print("=" * 90)

    sens_results = []
    for slip_val, sc_name in [(0.0001, "基準成本 (0.01% Slip)"), (0.0003, "較高滑價 (0.03% Slip)"), (0.0005, "壓力滑價 (0.05% Slip)")]:
        for ver, gate_cls in [("Old", OldExitGate), ("New", NewExitGate)]:
            t_is, eq_is = simulate_full_path(lob_is, "LOBSTERUSDT", gate_cls, "SensIS", fee_rate=0.0005, slippage_rate=slip_val)
            t_oos, eq_oos = simulate_full_path(lob_oos, "LOBSTERUSDT", gate_cls, "SensOOS", fee_rate=0.0005, slippage_rate=slip_val)
            m_is = compute_comprehensive_metrics(t_is, eq_is)
            m_oos = compute_comprehensive_metrics(t_oos, eq_oos)

            sens_results.append({
                "scenario": sc_name,
                "version": ver,
                "is_pnl": m_is["net_pnl"],
                "is_pf": m_is["profit_factor"],
                "is_dd": m_is["max_drawdown_closed_pct"],
                "oos_pnl": m_oos["net_pnl"],
                "oos_pf": m_oos["profit_factor"],
                "oos_dd": m_oos["max_drawdown_closed_pct"],
                "oos_long": m_oos["long_metrics"]["net_pnl"],
                "oos_short": m_oos["short_metrics"]["net_pnl"],
            })

    df_sens = pd.DataFrame(sens_results)
    print(f"{'成本情境':<24}{'版本':<6}{'樣本內淨利':<12}{'IS PF':<8}{'IS 回撤':<10}{'樣本外淨利':<12}{'OOS PF':<8}{'OOS 回撤':<10}{'OOS LONG':<12}{'OOS SHORT':<12}")
    print("-" * 115)
    for _, r in df_sens.iterrows():
        print(f"{r['scenario']:<22}{r['version']:<6}{r['is_pnl']:+10.2f}  {r['is_pf']:6.2f}  {r['is_dd']:8.2f}%  {r['oos_pnl']:+10.2f}  {r['oos_pf']:6.2f}  {r['oos_dd']:8.2f}%  {r['oos_long']:+10.2f}  {r['oos_short']:+10.2f}")


if __name__ == "__main__":
    run_comprehensive_audit()
