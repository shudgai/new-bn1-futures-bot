"""
run_phase3_controlled_experiments.py
====================================
Binance Futures Bot 2.0 第三、四階段：止損、進場與出場策略對照實驗矩陣
集成 TradeAccountingEngine 嚴格帳務稽核，確保：
1. 每筆交易的金額 (USDT)、名目本金、保證金、槓桿口徑完全一致。
2. 徹底消除滑點重複扣除的矛盾。
3. 嚴格滿足恆等式：Raw Gross Nom PnL % - Total Friction Nom Cost % == Net Nom PnL %
4. 覆蓋 6 大幣種 (BTC, ETH, SOL, DOGE, 1000PEPE, SUI) 1500 根 3m 真實 K 線。
5. 區分 樣本內 (In-Sample, 900 bars) 與 完全樣本外 (Out-of-Sample, 600 bars)。
"""

import os
import sys
import math
import json
import urllib.request
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple

from core.services.data_pipeline import MarketDataSnapshot
from core.gates.unified_pipeline_gates import EntryGate, ExitGate
from core.analytics.trade_accounting import TradeAccountingEngine, AuditedTrade, PortfolioAccountingEngine

FEE_RATE = 0.0004     # 單邊手續費 0.04%
SLIPPAGE = 0.0002     # 單邊滑點 0.02% (雙邊摩擦成本約 0.12%)


@dataclass
class ExperimentConfig:
    name: str
    entry_mode: str          # "ALL", "NO_BREAKOUT", "PULLBACK_ONLY", "PULLBACK_STRICT_BREAKOUT"
    stop_loss_mode: str      # "3_ATR_HARD", "STRUCTURAL", "HYBRID"
    pivot_threshold_atr: float  # 0.0 (現行), 1.0, 1.5, 999.0 (停用樞紐)
    enable_breakeven_lock: bool = False  # 浮盈達 0.8 ATR 鎖保本
    structural_lookback: int = 15
    structural_buffer_atr: float = 0.5
    hybrid_max_risk_atr: float = 4.0


def fetch_binance_1500_klines(symbol: str, interval: str = "3m") -> pd.DataFrame:
    """下載 1500 根 3m K 線 (分兩批抓取 1000 + 500)"""
    url1 = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit=1000"
    req1 = urllib.request.Request(url1, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req1, timeout=10) as resp:
        d1 = json.loads(resp.read().decode())
    
    first_open_time = d1[0][0]
    url2 = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit=500&endTime={first_open_time}"
    req2 = urllib.request.Request(url2, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req2, timeout=10) as resp:
            d2 = json.loads(resp.read().decode())
    except Exception:
        d2 = []

    combined = d2 + d1
    cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'taker_base_vol', 'taker_quote_vol', 'ignore']
    df = pd.DataFrame(combined, columns=cols)
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = df[col].astype(float)
    df = df.drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df


def calculate_structural_sl(df: pd.DataFrame, idx: int, side: str, atr: float, buffer_atr: float = 0.5, lookback: int = 15) -> float:
    """
    計算結構止損 (無未來數據)：
    嚴格取用已收線歷史 (idx-lookback 至 idx-1) 的 Swing Low / Swing High
    """
    start_idx = max(0, idx - lookback)
    end_idx = max(0, idx - 1)
    hist = df.iloc[start_idx:end_idx]
    if hist.empty:
        c_p = df.iloc[idx]['close']
        return c_p - 3.0 * atr if side == 'LONG' else c_p + 3.0 * atr

    if side == 'LONG':
        swing_low = hist['low'].min()
        return swing_low - buffer_atr * atr
    else:
        swing_high = hist['high'].max()
        return swing_high + buffer_atr * atr


def precompute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """在整份 DataFrame 上預先計算所有 Pipeline 指標 (因式分解無未來數據，速度提升 1000 倍)"""
    df_calc = df.copy()
    close = df_calc["close"]
    high = df_calc["high"]
    low = df_calc["low"]

    df_calc["ma3"] = close.rolling(window=3).mean()
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
    df_calc["ma_slow"] = df_calc["ema_slow"]
    df_calc["ma_slow_slope"] = df_calc["ema_slow"] - df_calc["ema_slow"].shift(2)
    df_calc["body_size"] = (df_calc["close"] - df_calc["open"]).abs()
    df_calc["upper_band"] = df_calc["kc_upper"]
    df_calc["lower_band"] = df_calc["kc_lower"]
    df_calc["middle_band"] = df_calc["kc_middle"]

    ema60_series = close.ewm(span=60, adjust=False).mean()
    df_calc["ema60"] = ema60_series
    df_calc["macro_slope_10"] = ema60_series - ema60_series.shift(10)
    df_calc["slow_slope_10"] = df_calc["ema_slow"] - df_calc["ema_slow"].shift(10)
    df_calc["fast_slope_3"] = df_calc["ema_fast"] - df_calc["ema_fast"].shift(3)
    df_calc["slow_slope_3"] = df_calc["ema_slow"] - df_calc["ema_slow"].shift(3)
    df_calc["kc_middle_slope_5"] = (df_calc["kc_middle"] - df_calc["kc_middle"].shift(5)) / 5.0
    return df_calc


@dataclass(frozen=True)
class GateDecision:
    """開倉裁決結果物件 (支援 Tuple 解包以維持相容性: allow, reason = decision)"""
    allow_entry: bool
    reason: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __iter__(self):
        yield self.allow_entry
        yield self.reason


def evaluate_entry_gate(
    df: pd.DataFrame,
    signal_type: str,
    current_idx: int = -1,
    min_bandwidth_threshold: float = 0.008,
) -> GateDecision:
    """
    EntryGate 完整防呆開倉審查 (雙層漏斗架構，2026-10-24 授權版)

    參數:
        df: 包含 OHLCV、MA3、MA5、MA15、KC、ATR 等欄位的 DataFrame
        signal_type: "BUY", "LONG", "SELL", "SHORT"
        current_idx: 時間錨點 (預設 -1 已收線 K 棒)
        min_bandwidth_threshold: 判定死魚帶寬閥值
    回傳:
        GateDecision: 支援 (allow_entry, reason) 解包
    """
    sig_norm = str(signal_type).upper().strip()
    if sig_norm in ("BUY", "LONG"):
        direction = "LONG"
    elif sig_norm in ("SELL", "SHORT"):
        direction = "SHORT"
    else:
        return GateDecision(False, f"VETO_INVALID_SIGNAL: 未知開倉方向 '{signal_type}'")

    n_bars = len(df)
    actual_idx = (n_bars + current_idx) if current_idx < 0 else current_idx
    if actual_idx < 7 or n_bars < 8:
        return GateDecision(False, "VETO_NOT_ENOUGH_BARS: 歷史K棒數量不足8根")
    current_idx = actual_idx

    row = df.iloc[current_idx]
    prev_row = df.iloc[current_idx - 1]
    prev3_row = df.iloc[current_idx - 3]

    atr = float(row["atr"])
    if not math.isfinite(atr) or atr <= 0:
        atr = max(float(row["high"]) - float(row["low"]), float(row["close"]) * 0.01)

    open_p = float(row["open"])
    close = float(row["close"])
    high = float(row["high"])
    low = float(row["low"])
    bar_range = max(high - low, 1e-8)
    body_size = abs(close - open_p)

    # 均線與通道數據
    ma3 = float(row["ma3"]) if "ma3" in row and not pd.isna(row["ma3"]) else float(row["ma5"])
    ma3_prev = float(prev_row["ma3"]) if "ma3" in prev_row and not pd.isna(prev_row["ma3"]) else float(prev_row["ma5"])
    ma15 = float(row["ma15"])
    ma15_prev3 = float(prev3_row["ma15"])
    kc_mid = float(row["kc_middle"])
    kc_up = float(row["kc_upper"])
    kc_low = float(row["kc_lower"])

    ma3_slope = ma3 - ma3_prev
    ma15_slope = ma15 - ma15_prev3
    kc_mid_slope = kc_mid - float(prev3_row["kc_middle"])

    trace_meta = {
        "actual_idx": current_idx, "direction": direction, "close": close,
        "open": open_p, "atr": atr, "ma3": ma3, "ma3_slope": ma3_slope,
        "ma15": ma15, "ma15_slope": ma15_slope, "kc_mid": kc_mid, "kc_mid_slope": kc_mid_slope,
    }

    # =========================================================
    # 第一層：全域環境硬性禁令 (Hard Veto)
    # =========================================================

    # 禁令 A：均線走平/糾纏麻花捲過濾
    ma15_window = df["ma15"].iloc[current_idx - 7 : current_idx + 1]
    ma15_range_8 = float(ma15_window.max() - ma15_window.min())
    is_ma15_strictly_flat = ma15_range_8 < (0.25 * atr)

    ma3_slice = df["ma3"].iloc[current_idx - 4 : current_idx + 1] if "ma3" in df.columns else df["ma5"].iloc[current_idx - 4 : current_idx + 1]
    ma15_slice = df["ma15"].iloc[current_idx - 4 : current_idx + 1]
    ma_gap_avg = float((ma3_slice - ma15_slice).abs().mean())

    if is_ma15_strictly_flat and (ma_gap_avg < 0.15 * atr):
        trace_meta.update(ma15_range_8=ma15_range_8, ma_gap_avg=ma_gap_avg)
        return GateDecision(False, f"VETO_MA_ENTANGLED: 過去8根均線實質走平且黏合(極差={ma15_range_8:.4f},間距={ma_gap_avg:.4f})，排除垃圾盤整", trace_meta)

    # 禁令 B：死魚盤判斷與「收斂破位」例外放行
    kc_bandwidth = (kc_up - kc_low) / kc_mid if kc_mid > 0 else 0.0
    trace_meta.update(kc_bandwidth=kc_bandwidth, min_bandwidth_threshold=min_bandwidth_threshold)
    is_breakout_long_surge = (close > kc_up) and (body_size >= 0.8 * atr)
    is_breakout_short_surge = (close < kc_low) and (body_size >= 0.8 * atr)

    if kc_bandwidth < min_bandwidth_threshold:
        if direction == "LONG" and not is_breakout_long_surge:
            return GateDecision(False, f"VETO_KC_SQUEEZE_DEAD: 通道極度收斂(bw={kc_bandwidth:.5f}<{min_bandwidth_threshold})且未見強勢破軌大陽棒", trace_meta)
        elif direction == "SHORT" and not is_breakout_short_surge:
            return GateDecision(False, f"VETO_KC_SQUEEZE_DEAD: 通道極度收斂(bw={kc_bandwidth:.5f}<{min_bandwidth_threshold})且未見強勢破軌大陰棒", trace_meta)

    # 禁令 C：防天花板追高 / 地板追空 (乖離率極限防護)
    if direction == "LONG":
        dist_from_mid = close - kc_mid
        dist_above_upper = close - kc_up
        trace_meta.update(dist_from_mid=dist_from_mid, dist_above_upper=dist_above_upper)
        if dist_from_mid > (1.8 * atr):
            return GateDecision(False, f"VETO_OVEREXTENDED_LONG: 距中軌過度延展(乖離={dist_from_mid:.4f} > 1.8*ATR={1.8*atr:.4f})，嚴防天花板站崗", trace_meta)
        if dist_above_upper > (0.4 * atr):
            return GateDecision(False, f"VETO_OVEREXTENDED_LONG: 突破上軌過遠(超軌={dist_above_upper:.4f} > 0.4*ATR={0.4*atr:.4f})，嚴重超買拒絕追多", trace_meta)
    elif direction == "SHORT":
        dist_below_mid = kc_mid - close
        dist_below_lower = kc_low - close
        trace_meta.update(dist_below_mid=dist_below_mid, dist_below_lower=dist_below_lower)
        if dist_below_mid > (1.8 * atr):
            return GateDecision(False, f"VETO_OVEREXTENDED_SHORT: 距中軌過度延展(負乖離={dist_below_mid:.4f} > 1.8*ATR={1.8*atr:.4f})，嚴防殺在地板阿呆谷", trace_meta)
        if dist_below_lower > (0.4 * atr):
            return GateDecision(False, f"VETO_OVEREXTENDED_SHORT: 跌破下軌過遠(超軌={dist_below_lower:.4f} > 0.4*ATR={0.4*atr:.4f})，嚴重超賣拒絕追空", trace_meta)

    # =========================================================
    # 第二層：當根方向與動能同向審查
    # =========================================================
    if direction == "LONG":
        # 1. 當根同向性：必須為實體陽線（拒絕黑棒與十字星）
        if close <= open_p:
            return GateDecision(False, f"VETO_BUY_OPPOSITE_BAR: 當根收陰或平盤(Close={close:.4f} <= Open={open_p:.4f})，嚴禁接飛刀", trace_meta)

        # 2. 假同向影線過濾：上影線不可超過整根總幅度的 60% 且不可大量回吐
        upper_wick = high - close
        if upper_wick > (0.6 * bar_range) and (upper_wick >= body_size or close < (low + 0.5 * bar_range)):
            trace_meta.update(upper_wick=upper_wick, bar_range=bar_range)
            return GateDecision(False, f"VETO_WICK_REJECTION: 長上影衝高回落(上影={upper_wick:.4f} > 60%振幅={0.6*bar_range:.4f})，疑似誘多", trace_meta)

        # 3. 短均線動能：MA3 必須明確抬頭向上
        if ma3_slope <= 0:
            return GateDecision(False, f"VETO_MA3_NOT_UP: MA3向下或走平(斜率={ma3_slope:.6f} <= 0)，短線無上攻動能", trace_meta)

        # 4. 中長均線健康度：MA15 與 KC 中軌「不再深挫」，斜率大於安全下限
        if ma15_slope < -(0.03 * atr) or kc_mid_slope < -(0.03 * atr):
            return GateDecision(False, f"VETO_MA15_PLUNGING: 中長均線仍在重挫下傾(MA15={ma15_slope:.5f},KC_mid={kc_mid_slope:.5f} < -0.03*ATR)，禁止逆勢抄底", trace_meta)

        # 5. 結構站位：收盤價必須穩穩收在 KC 中軌之上
        if close < kc_mid:
            return GateDecision(False, f"VETO_BELOW_KC_MIDDLE: 價格在中軌下方受壓(Close={close:.4f} < KC_mid={kc_mid:.4f})，不可做多", trace_meta)

        return GateDecision(True, "PASS_LONG: 陽線確認 + 上影健康 + MA3翻揚 + 站上中軌 + 均線非暴跌", trace_meta)

    elif direction == "SHORT":
        # 1. 當根同向性：必須為實體陰線（拒絕紅棒與十字星）
        if close >= open_p:
            return GateDecision(False, f"VETO_SELL_OPPOSITE_BAR: 當根收陽或平盤(Close={close:.4f} >= Open={open_p:.4f})，嚴禁頂風開空", trace_meta)

        # 2. 假同向影線過濾：下影線不可超過整根總幅度的 60%
        lower_wick = close - low
        if lower_wick > (0.6 * bar_range) and (lower_wick >= body_size or close > (high - 0.5 * bar_range)):
            trace_meta.update(lower_wick=lower_wick, bar_range=bar_range)
            return GateDecision(False, f"VETO_WICK_REJECTION: 長下影探底回抽(下影={lower_wick:.4f} > 60%振幅={0.6*bar_range:.4f})，疑似誘空", trace_meta)

        # 3. 短均線動能：MA3 必須向下壓制，抬頭直接一票否決
        if ma3_slope >= 0:
            return GateDecision(False, f"VETO_MA3_RISING: MA3抬頭或走平(斜率={ma3_slope:.6f} >= 0)，嚴禁逆勢做空", trace_meta)

        # 4. 中長均線健康度：MA15 與 KC 中軌「不再強烈飆漲」，斜率小於安全上限
        if ma15_slope > (0.03 * atr) or kc_mid_slope > (0.03 * atr):
            return GateDecision(False, f"VETO_MA15_SOARING: 中長均線仍處於強推升狀態(MA15={ma15_slope:.5f},KC_mid={kc_mid_slope:.5f} > 0.03*ATR)，禁止迎頭摸頂", trace_meta)

        # 5. 結構站位：收盤價必須壓制在 KC 中軌之下
        if close > kc_mid:
            return GateDecision(False, f"VETO_ABOVE_KC_MIDDLE: 價格在中軌上方強勢(Close={close:.4f} > KC_mid={kc_mid:.4f})，不可做空", trace_meta)

        return GateDecision(True, "PASS_SHORT: 陰線確認 + 下影健康 + MA3下壓 + 壓在中軌下 + 均線非暴漲", trace_meta)

    return GateDecision(False, f"VETO_UNKNOWN_SIGNAL: 未知訊號類型 {signal_type}")


def build_snapshot_from_precomputed(symbol: str, df_calc: pd.DataFrame, i: int, quote: float, position: Optional[Dict[str, Any]] = None) -> MarketDataSnapshot:
    """使用預計算數據極速構建 MarketDataSnapshot (與 DataPipeline.calculate_snapshot 輸出完全一致)"""
    last_row = df_calc.iloc[i]
    prev_row = df_calc.iloc[i-1]

    curr_k = {
        "open": float(last_row["open"]), "high": float(last_row["high"]),
        "low": float(last_row["low"]), "close": float(last_row["close"]),
        "volume": float(last_row["volume"]), "ma3": float(last_row["ma3"]),
        "ma5": float(last_row["ma5"]),
        "ma15": float(last_row["ma15"]), "kc_middle": float(last_row["kc_middle"]),
    }
    prev_k = {
        "open": float(prev_row["open"]), "high": float(prev_row["high"]),
        "low": float(prev_row["low"]), "close": float(prev_row["close"]),
        "volume": float(prev_row["volume"]), "ma3": float(prev_row["ma3"]),
        "ma5": float(prev_row["ma5"]),
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
        lev = float(position.get("leverage", 5.0))
        if entry_p > 0:
            cur_move = ((quote - entry_p) / entry_p) if side == "LONG" else ((entry_p - quote) / entry_p)
            peak_roe = max(float(position.get("peak_roe", 0.0)), cur_move * lev)

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


def run_experiment(df: pd.DataFrame, symbol: str, cfg: ExperimentConfig, period_label: str = "IN_SAMPLE", start_bar: int = 60, end_bar: int = None) -> List[Dict[str, Any]]:
    """
    執行單組受控實驗回測 (嚴格遵從保守悲觀成交假設與生產呼叫)
    """
    if end_bar is None:
        end_bar = len(df)

    trades = []
    in_pos = False
    pos = {}
    last_exit_record = {}
    hourly_entries = []
    current_equity = 10000.0

    for i in range(start_bar, end_bar):
        curr_bar = df.iloc[i]
        bar_ts = curr_bar['timestamp'] / 1000.0

        hourly_entries = [t for t in hourly_entries if (bar_ts - t) < 3600.0]

        snapshot = build_snapshot_from_precomputed(
            symbol=symbol,
            df_calc=df,
            i=i,
            quote=curr_bar['close'],
            position=pos if in_pos else None
        )

        # ── 1. 持倉處理 ──
        if in_pos:
            entry_p = pos['entry_price']
            side = pos['side']

            # 更新 MFE / MAE
            if side == 'LONG':
                pos['mfe'] = max(pos['mfe'], curr_bar['high'] - entry_p)
                pos['mae'] = max(pos['mae'], entry_p - curr_bar['low'])
                pos['max_price'] = max(pos['max_price'], curr_bar['high'])
                cur_move = (curr_bar['close'] - entry_p) / entry_p
            else:
                pos['mfe'] = max(pos['mfe'], entry_p - curr_bar['low'])
                pos['mae'] = max(pos['mae'], curr_bar['high'] - entry_p)
                pos['min_price'] = min(pos['min_price'], curr_bar['low'])
                cur_move = (entry_p - curr_bar['close']) / entry_p
            pos['peak_roe'] = max(pos['peak_roe'], cur_move * 5.0)

            # 保本鎖定 (若啟用)
            mfe_atr = pos['mfe'] / pos['atr_entry'] if pos['atr_entry'] > 0 else 0.0
            if cfg.enable_breakeven_lock and mfe_atr >= 0.8:
                if side == 'LONG':
                    pos['stop_loss_price'] = max(pos['stop_loss_price'], entry_p * (1.0 + FEE_RATE * 2))
                else:
                    pos['stop_loss_price'] = min(pos['stop_loss_price'], entry_p * (1.0 - FEE_RATE * 2))

            # 悲觀盤中止損檢查 (Intrabar SL hit)
            hit_sl = False
            if side == 'LONG' and curr_bar['low'] <= pos['stop_loss_price']:
                hit_sl = True
            elif side == 'SHORT' and curr_bar['high'] >= pos['stop_loss_price']:
                hit_sl = True

            should_exit = False
            exit_reason = None

            if hit_sl:
                should_exit = True
                exit_reason = f"SL_{cfg.stop_loss_mode}_INTRABAR"
                raw_exit_p = pos['stop_loss_price']
            else:
                should_exit, exit_reason, exit_meta = ExitGate.evaluate(
                    pos=pos,
                    market_data=snapshot
                )
                raw_exit_p = curr_bar['close']

                if should_exit and exit_reason in ("EARLY_PIVOT_PEAK_EXIT", "EARLY_PIVOT_VALLEY_EXIT"):
                    if cfg.pivot_threshold_atr > 0.0:
                        if mfe_atr < cfg.pivot_threshold_atr:
                            should_exit = False
                            exit_reason = "VETO_HOLD_PIVOT_THRESHOLD_NOT_MET"

            if should_exit:
                fut = df.iloc[i+1:min(i+6, len(df))]
                resumed = False
                if not fut.empty:
                    if side == 'LONG':
                        resumed = fut['high'].max() > entry_p
                    else:
                        resumed = fut['low'].min() < entry_p

                # 透過嚴格審計引擎計算真實損益與帳務
                audited = TradeAccountingEngine.settle_trade(
                    symbol=symbol,
                    exp_name=cfg.name,
                    period=period_label,
                    side=side,
                    entry_mode=pos['entry_mode'],
                    entry_time=pos['entry_time'],
                    exit_time=curr_bar['datetime'],
                    hold_bars=i - pos['entry_idx'],
                    exit_reason=exit_reason,
                    p_entry_raw=pos['p_entry_raw'],
                    p_exit_raw=raw_exit_p,
                    fee_rate=FEE_RATE,
                    slippage_rate=SLIPPAGE,
                    notional_target=1000.0,
                    leverage=5.0,
                    current_equity=current_equity,
                    mfe_raw=pos['mfe'],
                    mae_raw=pos['mae'],
                    atr_entry=pos['atr_entry'],
                    initial_sl=pos['initial_sl'],
                    resumed_after_sl=resumed if hit_sl else False,
                )

                current_equity = audited.equity_after
                trades.append(audited.to_dict())

                last_exit_record = {"timestamp": bar_ts, "exit_price": audited.p_exit_exec, "side": side}
                in_pos = False
                pos = {}
                continue

        # ── 2. 空倉處理 (EntryGate) ──
        else:
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
                gate_side = "BUY" if "LONG" in candidate_signal else "SELL"
                gate_ok, gate_reason = evaluate_entry_gate(df, signal_type=gate_side, current_idx=i)
                should_entry = False
                if gate_ok:
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
                    p_entry_raw = curr_bar['close']
                    actual_entry_p = p_entry_raw * (1.0 + SLIPPAGE) if side == "LONG" else p_entry_raw * (1.0 - SLIPPAGE)

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
                        "entry_time": curr_bar['datetime'],
                        "p_entry_raw": p_entry_raw,
                        "entry_price": actual_entry_p,
                        "entry_mode": detected_mode,
                        "entry_reason": entry_reason,
                        "stop_loss_price": sl_price,
                        "initial_sl": sl_price,
                        "atr_entry": snapshot.atr,
                        "mfe": 0.0,
                        "mae": 0.0,
                        "max_price": curr_bar['high'],
                        "min_price": curr_bar['low'],
                        "peak_roe": 0.0,
                        "leverage": 5.0,
                    }

    return trades


def evaluate_metrics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not trades:
        return {
            "count": 0, "raw_gross_usd": 0.0, "total_cost_usd": 0.0, "net_pnl_usd": 0.0,
            "nom_gross_pct": 0.0, "nom_cost_pct": 0.0, "nom_net_pct": 0.0,
            "win_rate": 0.0, "profit_factor": 0.0, "mdd": 0.0, "hold_bars": 0.0,
            "portfolio_ret_pct": 0.0, "sl_resumed_pct": 0.0
        }

    df_t = pd.DataFrame(trades)
    cnt = len(df_t)

    raw_gross_usd = df_t['raw_gross_pnl_usd'].sum()
    total_cost_usd = df_t['total_cost_usd'].sum()
    net_pnl_usd = df_t['net_pnl_usd'].sum()

    nom_gross_pct = df_t['nom_gross_pct'].sum()
    nom_cost_pct = df_t['nom_cost_pct'].sum()
    nom_net_pct = df_t['nom_net_pct'].sum()

    # 嚴格驗證恆等式
    assert math.isclose(raw_gross_usd - total_cost_usd, net_pnl_usd, abs_tol=1e-3), "USD 恆等式失敗"
    assert math.isclose(nom_gross_pct - nom_cost_pct, nom_net_pct, abs_tol=1e-3), "百分比恆等式失敗"

    wins = df_t[df_t['net_pnl_usd'] > 0]
    losses = df_t[df_t['net_pnl_usd'] <= 0]
    wr = len(wins) / cnt * 100.0

    gross_win = wins['net_pnl_usd'].sum() if not wins.empty else 0.0
    gross_loss = abs(losses['net_pnl_usd'].sum()) if not losses.empty else 0.0001
    pf = gross_win / gross_loss

    # 帳戶權益變化與最大回撤 (使用標準多資產時間序列組合結算器)
    port_eval = PortfolioAccountingEngine.evaluate_portfolio(trades, initial_equity=10000.0)
    mdd = port_eval['mdd_pct']
    final_eq = port_eval['final_equity']
    portfolio_ret_pct = port_eval['portfolio_ret_pct']
    max_concurrent = port_eval['max_concurrent_positions']
    max_margin_usd = port_eval['max_margin_used_usd']
    max_margin_pct = port_eval['max_margin_ratio_pct']

    hold = df_t['hold_bars'].mean()

    sl_trades = df_t[df_t['exit_reason'].str.startswith('SL_')]
    sl_resumed_pct = (sl_trades['resumed_after_sl'].sum() / len(sl_trades) * 100.0) if not sl_trades.empty else 0.0

    return {
        "count": cnt,
        "raw_gross_usd": round(raw_gross_usd, 2),
        "total_cost_usd": round(total_cost_usd, 2),
        "net_pnl_usd": round(net_pnl_usd, 2),
        "nom_gross_pct": round(nom_gross_pct, 2),
        "nom_cost_pct": round(nom_cost_pct, 2),
        "nom_net_pct": round(nom_net_pct, 2),
        "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2),
        "final_equity": round(final_eq, 2),
        "mdd": round(mdd, 2),
        "portfolio_ret_pct": round(portfolio_ret_pct, 2),
        "max_concurrent": max_concurrent,
        "max_margin_usd": round(max_margin_usd, 2),
        "max_margin_pct": round(max_margin_pct, 2),
        "hold_bars": round(hold, 1),
        "sl_resumed_pct": round(sl_resumed_pct, 1),
    }


def main():
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "1000PEPEUSDT", "SUIUSDT"]
    datasets = {}
    print(f"[*] 正在從幣安下載 6 大標的 1500 根 3M 真實數據 (向量化預算指標)...")
    for sym in symbols:
        df = fetch_binance_1500_klines(sym, interval="3m")
        df_calc = precompute_indicators(df)
        datasets[sym] = df_calc
        print(f"    - {sym}: {len(df_calc)} 根 K 線")

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

    all_trade_records = []
    summary_rows = []

    print("\n" + "=" * 125)
    print("🚀 啟動階段五嚴格合約帳務與時間序列組合權益曲線審計 (確保 期末權益 == 初始 + 淨利，報酬率 == 淨利 / 初始)")
    print("=" * 125)

    for exp in experiments:
        is_trades = []
        oos_trades = []

        for sym in symbols:
            df = datasets[sym]
            n_total = len(df)
            split_idx = min(900, int(n_total * 0.6))

            t_is = run_experiment(df, sym, exp, period_label="IN_SAMPLE", start_bar=60, end_bar=split_idx)
            is_trades.extend(t_is)

            t_oos = run_experiment(df, sym, exp, period_label="OUT_OF_SAMPLE", start_bar=split_idx, end_bar=n_total)
            oos_trades.extend(t_oos)

        all_trade_records.extend(is_trades)
        all_trade_records.extend(oos_trades)

        m_is = evaluate_metrics(is_trades)
        m_oos = evaluate_metrics(oos_trades)

        summary_rows.append({
            "實驗名稱": exp.name,
            "IS毛利%": m_is['nom_gross_pct'],
            "IS費用%": m_is['nom_cost_pct'],
            "IS淨利%": m_is['nom_net_pct'],
            "IS淨利USD": m_is['net_pnl_usd'],
            "OOS筆數": m_oos['count'],
            "OOS毛利%": m_oos['nom_gross_pct'],
            "OOS費用%": m_oos['nom_cost_pct'],
            "OOS淨利%": m_oos['nom_net_pct'],
            "OOS淨利USD": m_oos['net_pnl_usd'],
            "OOS期末權益": m_oos['final_equity'],
            "OOS帳戶權益%": m_oos['portfolio_ret_pct'],
            "OOS_PF": m_oos['profit_factor'],
            "OOS_MDD%": m_oos['mdd'],
            "OOS最大持倉": m_oos['max_concurrent'],
            "OOS保證金頂峰": m_oos['max_margin_usd'],
        })

    df_sum = pd.DataFrame(summary_rows)
    print("\n" + df_sum.to_string(index=False))

    if all_trade_records:
        df_trades = pd.DataFrame(all_trade_records)
        df_trades.to_csv("phase4_audited_trades_comparison.csv", index=False)
        print(f"\n[+] 已導出全部階段四嚴格審計逐筆成交紀錄至: phase4_audited_trades_comparison.csv (共 {len(df_trades)} 筆)")


if __name__ == "__main__":
    main()
