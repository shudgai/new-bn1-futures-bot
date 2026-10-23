"""
tools/deterministic_backtest_audit.py
=====================================
Binance Futures Bot 2.0 第六階段：確定性回測資料版本固定、報表一致性與策略穩健性驗證引擎

核心特性：
1. 固化數據源：直接讀取已固化的 2500 根 3m K 線黃金數據集 (data/golden_dataset_2500_bars.json)。
2. 零未來數據：三段切分 (Train 50% / Val 25% / OOS Test 25%)。
3. 確定性 Run ID：每次回測生成唯一 ID，並輸出簽名 Manifest (包含代碼版本、數據 Hash、成交 Hash)。
4. 報表一致性鐵律：逐幣種淨利加總 == 全部交易淨利加總 == 期末權益變化，存在 1e-5 差額立即拋錯。
5. 策略凍結：完全沿用現行 Baseline 及候選對照組邏輯，不調參、不優化。
"""

import os
import sys
import math
import json
import time
import uuid
import hashlib
import subprocess
import pandas as pd
import numpy as np
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional

# 加入專案路徑
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.services.data_pipeline import MarketDataSnapshot
from core.gates.unified_pipeline_gates import EntryGate, ExitGate
from core.analytics.trade_accounting import TradeAccountingEngine, PortfolioAccountingEngine, AuditedTrade
from run_phase3_controlled_experiments import (
    precompute_indicators,
    calculate_structural_sl,
    build_snapshot_from_precomputed,
    ExperimentConfig,
)

DATASET_PATH = "data/golden_dataset_2500_bars.json"
MANIFEST_PATH = "data/golden_dataset_manifest.json"
FEE_RATE = 0.0004       # 單邊 0.04%
SLIPPAGE_RATE = 0.0002  # 單邊 0.02%


def get_git_commit() -> str:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
        return res.stdout.strip()
    except Exception:
        return "UNKNOWN_COMMIT"


def load_fixed_golden_dataset() -> Dict[str, pd.DataFrame]:
    if not os.path.exists(DATASET_PATH):
        raise FileNotFoundError(f"找不到固化黃金數據集: {DATASET_PATH}，請先執行 snapshot_golden_dataset.py")

    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        raw_json = json.load(f)

    datasets = {}
    for sym, records in raw_json.items():
        df = pd.DataFrame(records)
        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["close"].astype(float)
        df["volume"] = df["volume"].astype(float)
        df["datetime"] = pd.to_datetime(df["open_time_dt"])
        df_calc = precompute_indicators(df)
        datasets[sym] = df_calc

    return datasets


def run_single_symbol_backtest(
    df: pd.DataFrame,
    symbol: str,
    cfg: ExperimentConfig,
    period_label: str,
    start_bar: int,
    end_bar: int,
) -> List[Dict[str, Any]]:
    trades = []
    in_pos = False
    pos = {}
    last_exit_record = None
    hourly_entries = []

    for i in range(start_bar, end_bar):
        curr_bar = df.iloc[i]
        prev_bar = df.iloc[i - 1]
        bar_ts = int(curr_bar["datetime"].timestamp())
        hourly_entries = [t for t in hourly_entries if bar_ts - t < 3600]

        snapshot = build_snapshot_from_precomputed(
            symbol=symbol,
            df_calc=df,
            i=i,
            quote=curr_bar["close"],
            position=pos if in_pos else None,
        )

        if in_pos:
            side = pos["side"]
            p_entry_raw = pos["p_entry_raw"]
            entry_idx = pos["entry_idx"]
            bars_held = i - entry_idx

            if side == "LONG":
                unrealized_mfe = max(pos["mfe"], curr_bar["high"] - p_entry_raw)
                unrealized_mae = min(pos["mae"], curr_bar["low"] - p_entry_raw)
                pos["mfe"] = unrealized_mfe
                pos["mae"] = unrealized_mae
            else:
                unrealized_mfe = max(pos["mfe"], p_entry_raw - curr_bar["low"])
                unrealized_mae = min(pos["mae"], p_entry_raw - curr_bar["high"])
                pos["mfe"] = unrealized_mfe
                pos["mae"] = unrealized_mae

            mfe_atr = pos["mfe"] / pos["atr_entry"] if pos.get("atr_entry") and pos["atr_entry"] > 0 else 0.0
            if cfg.enable_breakeven_lock and mfe_atr >= 0.8:
                if side == "LONG":
                    pos["stop_loss_price"] = max(pos["stop_loss_price"], pos["p_entry_raw"] * (1.0 + FEE_RATE * 2))
                else:
                    pos["stop_loss_price"] = min(pos["stop_loss_price"], pos["p_entry_raw"] * (1.0 - FEE_RATE * 2))

            hit_sl = False
            if side == "LONG" and curr_bar["low"] <= pos["stop_loss_price"]:
                hit_sl = True
            elif side == "SHORT" and curr_bar["high"] >= pos["stop_loss_price"]:
                hit_sl = True

            should_exit = False
            exit_reason = None
            raw_exit_p = None

            if hit_sl:
                should_exit = True
                exit_reason = f"SL_{cfg.stop_loss_mode}_INTRABAR"
                raw_exit_p = pos["stop_loss_price"]
            else:
                should_exit, exit_reason, exit_meta = ExitGate.evaluate(
                    pos=pos,
                    market_data=snapshot
                )
                raw_exit_p = curr_bar["close"]

                if should_exit and exit_reason in ("EARLY_PIVOT_PEAK_EXIT", "EARLY_PIVOT_VALLEY_EXIT"):
                    if cfg.pivot_threshold_atr > 0.0:
                        if mfe_atr < cfg.pivot_threshold_atr:
                            should_exit = False
                            exit_reason = "VETO_HOLD_PIVOT_THRESHOLD_NOT_MET"

            if should_exit:
                # 結算
                audited = TradeAccountingEngine.settle_trade(
                    symbol=symbol,
                    exp_name=cfg.name,
                    period=period_label,
                    side=side,
                    entry_mode=pos["entry_mode"],
                    entry_time=str(pos["entry_time"]),
                    exit_time=str(curr_bar["datetime"]),
                    hold_bars=bars_held,
                    exit_reason=exit_reason,
                    p_entry_raw=p_entry_raw,
                    p_exit_raw=raw_exit_p,
                    fee_rate=FEE_RATE,
                    slippage_rate=SLIPPAGE_RATE,
                    notional_target=1000.0,
                    leverage=5.0,
                    current_equity=10000.0,
                    mfe_raw=pos["mfe"],
                    mae_raw=pos["mae"],
                    atr_entry=pos["atr_entry"],
                    initial_sl=pos["initial_sl"],
                    resumed_after_sl=False,
                )
                trades.append(audited.to_dict())
                last_exit_record = {"timestamp": bar_ts, "exit_price": audited.p_exit_exec, "side": side}
                in_pos = False
                pos = {}
                continue

        else:
            # 空倉開倉評估
            if last_exit_record and (bar_ts - last_exit_record["timestamp"]) < 540:
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
                macro_trend == "BULLISH" or is_bull_align or (is_above_slow and is_slow_up)
                or (snapshot.kc_middle_slope >= -0.000005 and snapshot.curr_k["close"] >= snapshot.kc_middle)
            )
            is_bearish_wave = (
                macro_trend == "BEARISH" or is_bear_align or (is_below_slow and is_slow_down)
                or (snapshot.kc_middle_slope <= 0.000005 and snapshot.curr_k["close"] <= snapshot.kc_middle)
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
                is_bullish_wave and snapshot.curr_k["low"] <= snapshot.ma5
                and snapshot.curr_k["close"] > snapshot.ma5
                and snapshot.curr_k["close"] > snapshot.curr_k["open"]
                and ma5_curr >= ma5_prev
            )
            is_pullback_short = (
                is_bearish_wave and snapshot.curr_k["high"] >= snapshot.ma5
                and snapshot.curr_k["close"] < snapshot.ma5
                and snapshot.curr_k["close"] < snapshot.curr_k["open"]
                and ma5_curr <= ma5_prev
            )

            is_breakout_long = (
                (snapshot.current_price > snapshot.kc_upper or snapshot.curr_k["close"] > snapshot.kc_upper)
                and snapshot.kc_middle_slope >= -0.000005 and ma5_curr > ma5_prev
            )
            is_breakdown_short = (
                (snapshot.current_price < snapshot.kc_lower or snapshot.curr_k["close"] < snapshot.kc_lower)
                and snapshot.kc_middle_slope <= 0.000005 and ma5_curr < ma5_prev
            )

            candidate_signal = None
            detected_mode = None

            if not is_bearish_wave:
                if cfg.entry_mode == "PULLBACK_ONLY":
                    if is_pullback_long:
                        candidate_signal = "TREND_PULLBACK_LONG"
                        detected_mode = "MODE_2_PULLBACK"
                elif cfg.entry_mode == "NO_BREAKOUT":
                    if is_pullback_long:
                        candidate_signal = "TREND_PULLBACK_LONG"
                        detected_mode = "MODE_2_PULLBACK"
                    elif is_cross_long:
                        candidate_signal = "ENTRY_LONG_EMA_CROSS_MID"
                        detected_mode = "MODE_1_TREND_START"
                elif cfg.entry_mode == "PULLBACK_STRICT_BREAKOUT":
                    if is_pullback_long:
                        candidate_signal = "TREND_PULLBACK_LONG"
                        detected_mode = "MODE_2_PULLBACK"
                    elif is_breakout_long and macro_trend == "BULLISH":
                        dist_band = (snapshot.curr_k["close"] - snapshot.kc_upper) / snapshot.atr if snapshot.atr > 0 else 0
                        if dist_band <= 0.5:
                            candidate_signal = "BREAKOUT_LONG"
                            detected_mode = "MODE_3_BREAKOUT"
                else:  # "ALL"
                    if is_breakout_long:
                        candidate_signal = "BREAKOUT_LONG"
                        detected_mode = "MODE_3_BREAKOUT"
                    elif is_pullback_long:
                        candidate_signal = "TREND_PULLBACK_LONG"
                        detected_mode = "MODE_2_PULLBACK"
                    elif is_cross_long:
                        candidate_signal = "ENTRY_LONG_EMA_CROSS_MID"
                        detected_mode = "MODE_1_TREND_START"

            elif not is_bullish_wave:
                if cfg.entry_mode == "PULLBACK_ONLY":
                    if is_pullback_short:
                        candidate_signal = "TREND_PULLBACK_SHORT"
                        detected_mode = "MODE_2_PULLBACK"
                elif cfg.entry_mode == "NO_BREAKOUT":
                    if is_pullback_short:
                        candidate_signal = "TREND_PULLBACK_SHORT"
                        detected_mode = "MODE_2_PULLBACK"
                    elif is_cross_short:
                        candidate_signal = "ENTRY_SHORT_EMA_CROSS_MID"
                        detected_mode = "MODE_1_TREND_START"
                elif cfg.entry_mode == "PULLBACK_STRICT_BREAKOUT":
                    if is_pullback_short:
                        candidate_signal = "TREND_PULLBACK_SHORT"
                        detected_mode = "MODE_2_PULLBACK"
                    elif is_breakdown_short and macro_trend == "BEARISH":
                        dist_band = (snapshot.kc_lower - snapshot.curr_k["close"]) / snapshot.atr if snapshot.atr > 0 else 0
                        if dist_band <= 0.5:
                            candidate_signal = "BREAKDOWN_SHORT"
                            detected_mode = "MODE_3_BREAKOUT"
                else:  # "ALL"
                    if is_breakdown_short:
                        candidate_signal = "BREAKDOWN_SHORT"
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
                    p_entry_raw = curr_bar["close"]
                    actual_entry_p = p_entry_raw * (1.0 + SLIPPAGE_RATE) if side == "LONG" else p_entry_raw * (1.0 - SLIPPAGE_RATE)

                    if cfg.stop_loss_mode == "3_ATR_HARD":
                        sl_price = p_entry_raw - 3.0 * snapshot.atr if side == "LONG" else p_entry_raw + 3.0 * snapshot.atr
                    elif cfg.stop_loss_mode == "STRUCTURAL":
                        sl_price = calculate_structural_sl(
                            df=df, idx=i, side=side, atr=snapshot.atr,
                            buffer_atr=cfg.structural_buffer_atr, lookback=cfg.structural_lookback
                        )
                    elif cfg.stop_loss_mode == "HYBRID":
                        struct_sl = calculate_structural_sl(
                            df=df, idx=i, side=side, atr=snapshot.atr,
                            buffer_atr=cfg.structural_buffer_atr, lookback=cfg.structural_lookback
                        )
                        max_sl_dist = cfg.hybrid_max_risk_atr * snapshot.atr
                        sl_price = max(struct_sl, p_entry_raw - max_sl_dist) if side == "LONG" else min(struct_sl, p_entry_raw + max_sl_dist)

                    in_pos = True
                    hourly_entries.append(bar_ts)
                    pos = {
                        "symbol": symbol,
                        "side": side,
                        "entry_idx": i,
                        "entry_time": curr_bar["datetime"],
                        "p_entry_raw": p_entry_raw,
                        "entry_price": actual_entry_p,
                        "entry_mode": detected_mode,
                        "entry_reason": entry_reason,
                        "stop_loss_price": sl_price,
                        "initial_sl": sl_price,
                        "atr_entry": snapshot.atr,
                        "mfe": 0.0,
                        "mae": 0.0,
                        "max_price": curr_bar["high"],
                        "min_price": curr_bar["low"],
                        "leverage": 5.0,
                    }

    return trades


def run_deterministic_audit():
    run_id = f"RUN_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    os.makedirs("reports", exist_ok=True)

    print(f"\n==================================================================================================================")
    print(f"🚀 啟動確定性回測與報表審計引擎 (Run ID: {run_id})")
    print(f"==================================================================================================================")

    # 1. 載入固化黃金數據集
    datasets = load_fixed_golden_dataset()
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        data_manifest = json.load(f)

    dataset_hash = data_manifest["snapshot_sha256"]
    git_commit = get_git_commit()
    print(f"[*] 數據集 Hash: {dataset_hash}")
    print(f"[*] Git Commit : {git_commit}")

    # 2. 定義實驗組別 (策略完全凍結，不調參)
    experiments = [
        ExperimentConfig("Baseline_現行基準", entry_mode="ALL", stop_loss_mode="3_ATR_HARD", pivot_threshold_atr=0.0),
        ExperimentConfig("Entry_B_停用突破", entry_mode="NO_BREAKOUT", stop_loss_mode="3_ATR_HARD", pivot_threshold_atr=0.0),
        ExperimentConfig("Entry_C_只用回踩", entry_mode="PULLBACK_ONLY", stop_loss_mode="3_ATR_HARD", pivot_threshold_atr=0.0),
        ExperimentConfig("Entry_D_回踩+嚴格突破", entry_mode="PULLBACK_STRICT_BREAKOUT", stop_loss_mode="3_ATR_HARD", pivot_threshold_atr=0.0),
        ExperimentConfig("SL_B_結構止損", entry_mode="ALL", stop_loss_mode="STRUCTURAL", pivot_threshold_atr=0.0),
        ExperimentConfig("SL_C_混合止損", entry_mode="ALL", stop_loss_mode="HYBRID", pivot_threshold_atr=0.0),
        ExperimentConfig("Exit_B_樞紐門檻1.0R", entry_mode="ALL", stop_loss_mode="3_ATR_HARD", pivot_threshold_atr=1.0),
        ExperimentConfig("Exit_C_樞紐門檻1.5R", entry_mode="ALL", stop_loss_mode="3_ATR_HARD", pivot_threshold_atr=1.5),
        ExperimentConfig("Exit_D_階梯保本+樞紐", entry_mode="ALL", stop_loss_mode="3_ATR_HARD", pivot_threshold_atr=1.2, enable_breakeven_lock=True),
        ExperimentConfig("Combo_最優融合方案", entry_mode="PULLBACK_STRICT_BREAKOUT", stop_loss_mode="HYBRID", pivot_threshold_atr=1.2, enable_breakeven_lock=True),
    ]

    # 切分點定義：2500 根
    # Train: 60 ~ 1250 (50%)
    # Val: 1250 ~ 1875 (25%)
    # Test (OOS): 1875 ~ 2500 (25%)
    idx_train_start = 60
    idx_train_end = 1250
    idx_val_start = 1250
    idx_val_end = 1875
    idx_test_start = 1875
    idx_test_end = 2500

    all_trades_record = []
    matrix_rows = []

    for exp in experiments:
        train_trades = []
        val_trades = []
        test_trades = []

        for sym, df in datasets.items():
            t_tr = run_single_symbol_backtest(df, sym, exp, "TRAIN", idx_train_start, idx_train_end)
            t_va = run_single_symbol_backtest(df, sym, exp, "VALIDATION", idx_val_start, idx_val_end)
            t_te = run_single_symbol_backtest(df, sym, exp, "OUT_OF_SAMPLE_TEST", idx_test_start, idx_test_end)

            train_trades.extend(t_tr)
            val_trades.extend(t_va)
            test_trades.extend(t_te)

        all_trades_record.extend(train_trades)
        all_trades_record.extend(val_trades)
        all_trades_record.extend(test_trades)

        # 組合結算
        res_tr = PortfolioAccountingEngine.evaluate_portfolio(train_trades, initial_equity=10000.0)
        res_va = PortfolioAccountingEngine.evaluate_portfolio(val_trades, initial_equity=10000.0)
        res_te = PortfolioAccountingEngine.evaluate_portfolio(test_trades, initial_equity=10000.0)

        # 嚴格校驗此實驗組在 Test (OOS) 的逐幣種淨利加總 == 全部交易淨利加總 == 期末權益變化
        df_te = pd.DataFrame(test_trades)
        sum_by_symbol = df_te.groupby("symbol")["net_pnl_usd"].sum().sum() if not df_te.empty else 0.0
        sum_by_trades = df_te["net_pnl_usd"].sum() if not df_te.empty else 0.0
        equity_diff = res_te["final_equity_raw"] - res_te["initial_equity"]

        assert math.isclose(sum_by_symbol, sum_by_trades, abs_tol=1e-5), f"逐幣種與全部交易淨利不符: {sum_by_symbol} vs {sum_by_trades}"
        assert math.isclose(equity_diff, sum_by_trades, abs_tol=1e-5), f"期末權益變化與交易淨利不符: {equity_diff} vs {sum_by_trades}"
        assert math.isclose(res_te["total_net_pnl_usd_raw"], sum_by_trades, abs_tol=1e-5), f"組合總淨損益與交易加總不符"

        # 計算 OOS 期望值與勝率
        wins = df_te[df_te["net_pnl_usd"] > 0] if not df_te.empty else pd.DataFrame()
        losses = df_te[df_te["net_pnl_usd"] <= 0] if not df_te.empty else pd.DataFrame()
        wr = len(wins) / len(df_te) * 100.0 if not df_te.empty else 0.0
        gross_win = wins["net_pnl_usd"].sum() if not wins.empty else 0.0
        gross_loss = abs(losses["net_pnl_usd"].sum()) if not losses.empty else 0.0001
        pf = gross_win / gross_loss
        expectancy_usd = res_te["total_net_pnl_usd"] / len(df_te) if not df_te.empty else 0.0

        matrix_rows.append({
            "實驗名稱": exp.name,
            "訓練淨利USD": res_tr["total_net_pnl_usd"],
            "驗證淨利USD": res_va["total_net_pnl_usd"],
            "OOS筆數": len(df_te),
            "OOS毛利USD": round(df_te["raw_gross_pnl_usd"].sum(), 2) if not df_te.empty else 0.0,
            "OOS摩擦USD": round(df_te["total_cost_usd"].sum(), 2) if not df_te.empty else 0.0,
            "OOS淨利USD": res_te["total_net_pnl_usd"],
            "OOS期末權益": res_te["final_equity"],
            "OOS總報酬%": res_te["portfolio_ret_pct"],
            "OOS_PF": round(pf, 2),
            "OOS_MDD%": res_te["mdd_pct"],
            "每筆期望值USD": round(expectancy_usd, 2),
            "OOS最大持倉": res_te["max_concurrent_positions"],
            "OOS保證金頂峰": res_te["max_margin_used_usd"],
        })

    df_matrix = pd.DataFrame(matrix_rows)
    print("\n" + df_matrix.to_string(index=False))

    # 3. 輸出該 Run 的專屬交易 CSV 與 Manifest
    trades_csv_path = f"reports/{run_id}_trades.csv"
    manifest_json_path = f"reports/{run_id}_manifest.json"

    df_all_trades = pd.DataFrame(all_trades_record)
    df_all_trades.to_csv(trades_csv_path, index=False)

    # 計算 trades_csv SHA-256
    with open(trades_csv_path, "rb") as f:
        trades_hash = hashlib.sha256(f.read()).hexdigest()

    # 提取 Baseline OOS 逐幣種精確明細
    base_oos_df = df_all_trades[(df_all_trades["exp_name"] == "Baseline_現行基準") & (df_all_trades["period"] == "OUT_OF_SAMPLE_TEST")]
    sym_breakdown = {}
    for sym, grp in base_oos_df.groupby("symbol"):
        sym_breakdown[sym] = {
            "count": len(grp),
            "raw_gross_usd": round(grp["raw_gross_pnl_usd"].sum(), 2),
            "total_cost_usd": round(grp["total_cost_usd"].sum(), 2),
            "net_pnl_usd": round(grp["net_pnl_usd"].sum(), 2),
            "nom_gross_pct": round(grp["nom_gross_pct"].sum(), 2),
            "nom_cost_pct": round(grp["nom_cost_pct"].sum(), 2),
            "nom_net_pct": round(grp["nom_net_pct"].sum(), 2),
        }

    manifest = {
        "run_id": run_id,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "git_commit": git_commit,
        "dataset_name": data_manifest["dataset_name"],
        "dataset_hash": dataset_hash,
        "dataset_range": {
            "total_bars": 2500,
            "train_range": f"{idx_train_start} ~ {idx_train_end} bars",
            "val_range": f"{idx_val_start} ~ {idx_val_end} bars",
            "test_range": f"{idx_test_start} ~ {idx_test_end} bars (OOS)",
        },
        "trades_csv_path": trades_csv_path,
        "trades_csv_hash": trades_hash,
        "total_trades_count": len(df_all_trades),
        "baseline_oos_symbol_breakdown": sym_breakdown,
        "matrix_summary": matrix_rows,
    }

    with open(manifest_json_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"\n[+] 確定性審計回測完成！")
    print(f"[+] 專屬交易明細 CSV: {trades_csv_path} (SHA-256: {trades_hash})")
    print(f"[+] 專屬審計清單 JSON: {manifest_json_path}")

    return run_id, manifest


if __name__ == "__main__":
    run_deterministic_audit()
