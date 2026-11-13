"""Low-latency real-time MA/KC entry evaluator and Maker trigger price predictor.

Provides zero-lag indicator updates on live WebSocket ticks without waiting
for 1m candle closure, noise-filtered slope thresholds, and closed-form
critical trigger prices for pre-placed Maker Limit orders.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from core.services.technical_indicators import compute_keltner_channels
from core.symbol_aliases import is_lobster_symbol, is_pepe_symbol


@dataclass(frozen=True)
class PeakValleyEntryConfig:
    """獨立可調參數設定（針對 1000PEPE / 龙虾 / 高波動山寨幣設計）。"""

    # 防盤整區間不開倉（任一成立即禁止開倉）
    min_kc_width_pct: float = 0.008       # KC 通道寬度 (KC_upper - KC_lower) / KC_middle 下限（預設 0.8%）
    min_ma_spread_pct: float = 0.0012     # 均線糾纏度 |MA5 - MA15| / MA15 下限（預設 0.12%）
    enable_adx_filter: bool = True        # 是否啟用 ADX 趨勢動能濾網
    min_adx: float = 20.0                 # ADX 低於此門檻視為無趨勢盤整（預設 20.0）

    # 峰谷轉向與低延遲超買超賣反轉開倉參數
    min_slope_pct: float = 0.0            # MA5 拐點斜率最小幅度比例（0.0 = 純斜率正負反轉）
    min_prev_turn_slope_pct: float = 0.00002  # 拐點前一根 MA5 斜率最小幅度（預設 0.002%/bar，防止極微小數值抖動誤判峰谷）
    min_range_efficiency_in_box: float = 0.25  # 價格仍困於窄幅橫盤箱體內時之最小淨位移效率（低於視為震盪）
    range_efficiency_lookback: int = 20   # 橫盤箱體位移效率檢查回溯 K 棒數
    box_chop_max_range_pct: float = 0.012 # 橫盤窄幅箱體震幅上限（預設 1.2%）
    enable_kc_band_reversal: bool = True  # 是否啟用低延遲 KC 上下軌超買/超賣觸軌反轉輔助觸發
    kc_band_touch_buffer_pct: float = 0.0015  # 觸及 KC 上下軌的彈性容差（預設 0.15%）
    require_directional_candle: bool = False  # True 時要求收陽開多 / 收陰開空；False 依 close>MA5 或陽線反彈確認

    # 通道極值區峰谷反轉策略參數（峰頂轉向開空 / 谷底轉向開多）
    extreme_pivot_lookback_bars: int = 3      # 最近 1~3 根 K 線內曾觸及或超越極值緩衝區
    extreme_pivot_band_ratio: float = 0.75    # 0.75: high >= KC_mid + 0.75*(KC_upper - KC_mid) / low <= KC_mid - 0.75*(KC_mid - KC_lower)
    extreme_pivot_max_long_kc_pos: float = 0.55   # 谷底開多安全空間：kc_pos <= 0.55（中軌附近或下方，保留足夠到上軌空間）
    extreme_pivot_min_short_kc_pos: float = 0.45  # 峰頂開空安全空間：kc_pos >= 0.45（中軌附近或上方，保留足夠到下軌空間）

    # 第一買點（峰谷拐點）順勢豁免 min_ma_spread_pct 參數（多空完全對稱）
    enable_inflection_ma_spread_exemption: bool = True
    inflection_prior_trend_lookback: int = 8
    inflection_5m_slope_threshold_pct: float = 0.0001

    # 第二買點（強勢突破/跌破 KC 中軌 + 通道擴張補票通道，多空完全對稱）
    enable_second_buy_mid_breakout: bool = True
    second_buy_cross_lookback_bars: int = 2
    second_buy_expansion_lookback: int = 2

    # 指標週期設定（沿用系統標準）
    kc_ema_period: int = 20
    kc_atr_period: int = 14
    kc_multiplier: float = 1.5
    adx_period: int = 14

    # 5M 大局觀最高指導原則 (5M Master Trend Filter)
    enable_5m_master_trend_filter: bool = True  # 是否強制啟用 5M 定多空方向（BEAR_ONLY / BULL_ONLY / CHOP_LOCK）
    min_5m_kc_width_pct: float = 0.0035         # 5M 通道寬度過窄視為 CHOP_LOCK（預設 0.35%）
    min_5m_reversal_slope_pct: float = 0.00010  # 5M 中軌明確斜率門檻（預設 0.01%/根）


DEFAULT_PEAK_VALLEY_CONFIG = PeakValleyEntryConfig()
PEPE_PEAK_VALLEY_CONFIG = PeakValleyEntryConfig(
    min_kc_width_pct=0.0035,
    min_ma_spread_pct=0.0007,
    enable_adx_filter=True,
    min_adx=12.0,
)
LOBSTER_PEAK_VALLEY_CONFIG = PeakValleyEntryConfig(
    min_kc_width_pct=0.0028,
    min_ma_spread_pct=0.0004,
    enable_adx_filter=True,
    min_adx=15.0,
)


def resolve_peak_valley_entry_config(
    symbol: Optional[str] = None,
    config: Optional[PeakValleyEntryConfig] = None,
) -> PeakValleyEntryConfig:
    """依交易對解析適配的峰谷開倉參數，顯式傳入 config 時優先保留呼叫端設定。"""
    if config is not None:
        return config
    if symbol and is_lobster_symbol(symbol):
        return LOBSTER_PEAK_VALLEY_CONFIG
    if symbol and is_pepe_symbol(symbol):
        return PEPE_PEAK_VALLEY_CONFIG
    return DEFAULT_PEAK_VALLEY_CONFIG


def _compute_adx_series(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Compute ADX using the repository's Wilder-alpha smoothing formula."""
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=close.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=close.index,
    )
    alpha = 1.0 / max(1, int(period))
    tr_smooth = tr.ewm(alpha=alpha, adjust=False).mean()
    plus_di = 100.0 * (plus_dm.ewm(alpha=alpha, adjust=False).mean() / (tr_smooth + 1e-12))
    minus_di = 100.0 * (minus_dm.ewm(alpha=alpha, adjust=False).mean() / (tr_smooth + 1e-12))
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-12)
    return dx.ewm(alpha=alpha, adjust=False).mean()


def evaluate_peak_valley_consolidation(
    *,
    kc_upper: float,
    kc_middle: float,
    kc_lower: float,
    ma5: float,
    ma15: float,
    adx: Optional[float] = None,
    config: PeakValleyEntryConfig = DEFAULT_PEAK_VALLEY_CONFIG,
) -> Tuple[bool, str, Dict[str, Any]]:
    """檢查是否落入盤整區間；滿足任一盤整條件即回傳 (True, reason, metrics)。"""
    core_nums = (kc_upper, kc_middle, kc_lower, ma5, ma15)
    if not all(math.isfinite(v) and v > 0 for v in core_nums) or kc_upper <= kc_lower:
        return True, "CONSOLIDATION_INVALID_METRICS", {
            "kc_width_pct": 0.0,
            "ma_spread_pct": 0.0,
            "adx": adx,
        }

    kc_width_pct = (kc_upper - kc_lower) / kc_middle
    ma_spread_pct = abs(ma5 - ma15) / ma15
    metrics: Dict[str, Any] = {
        "kc_width_pct": kc_width_pct,
        "ma_spread_pct": ma_spread_pct,
        "adx": adx,
        "min_kc_width_pct": config.min_kc_width_pct,
        "min_ma_spread_pct": config.min_ma_spread_pct,
        "min_adx": config.min_adx,
    }

    if kc_width_pct < config.min_kc_width_pct:
        return True, "CONSOLIDATION_KC_WIDTH_TOO_NARROW", metrics
    if ma_spread_pct < config.min_ma_spread_pct:
        return True, "CONSOLIDATION_MA_ENTANGLED", metrics
    if config.enable_adx_filter:
        if adx is None or not math.isfinite(adx):
            return True, "CONSOLIDATION_ADX_UNAVAILABLE", metrics
        if adx < config.min_adx:
            return True, "CONSOLIDATION_LOW_ADX", metrics

    return False, "TREND_VOLATILITY_OK", metrics


def _resolve_5m_trend_state(
    df_5m: Optional[pd.DataFrame],
    df_1m: Optional[pd.DataFrame],
    live_price: Optional[float],
    *,
    slope_threshold_pct: float = 0.0001,
) -> Dict[str, Any]:
    """解析 5M 多週期趨勢狀態（供第一買點順勢豁免與第二買點不逆勢濾網共用）。"""


def resolve_5m_trend_master_state(
    df_5m: Optional[pd.DataFrame],
    df_1m: Optional[pd.DataFrame],
    live_price: Optional[float] = None,
    *,
    slope_threshold_pct: float = 0.0001,
) -> Dict[str, Any]:
    """外部通用入口：解析 5M K 棒或重採樣 5M 之大局觀趨勢與模式 (BEAR_ONLY / BULL_ONLY / CHOP_LOCK)。"""
    return _resolve_5m_trend_state(df_5m, df_1m, live_price, slope_threshold_pct=slope_threshold_pct)


def _resolve_5m_trend_state(
    df_5m: Optional[pd.DataFrame],
    df_1m: Optional[pd.DataFrame],
    live_price: Optional[float],
    *,
    slope_threshold_pct: float = 0.0001,
) -> Dict[str, Any]:
    neutral = {
        "available": False,
        "is_5m_up": False,
        "is_5m_down": False,
        "is_5m_hard_bearish": False,
        "is_5m_hard_bullish": False,
        "slope_5m_pct": 0.0,
        "close_5m": None,
        "kc_middle_5m": None,
    }
    src = df_5m
    if src is None or not isinstance(src, pd.DataFrame) or (
        len(src) < 16 and not (len(src) >= 2 and {"ma15", "kc_middle", "close"}.issubset(src.columns))
    ):
        if isinstance(df_1m, pd.DataFrame) and len(df_1m) >= 80 and {"open", "high", "low", "close"}.issubset(df_1m.columns):
            tmp = df_1m.copy()
            ts_col = "timestamp" if "timestamp" in tmp.columns else ("time" if "time" in tmp.columns else None)
            if ts_col is not None:
                ts_vals = pd.to_numeric(tmp[ts_col], errors="coerce").astype(float)
                # 若末根時間為 NaN，依序往前補齊 60s
                if ts_vals.isna().any():
                    ts_vals = ts_vals.ffill()
                    # 若依然有 NaN，遞補時間
                    if ts_vals.isna().any():
                        ts_vals = ts_vals.bfill()
                ts_ms = ts_vals.where(ts_vals >= 1e11, ts_vals * 1000.0)
                b5_raw = ts_ms // 300_000.0
                if b5_raw.isna().any():
                    tmp["_b5"] = np.arange(len(tmp)) // 5
                else:
                    tmp["_b5"] = b5_raw.astype("int64")
            else:
                tmp["_b5"] = np.arange(len(tmp)) // 5
            agg_dict: Dict[str, str] = {"open": "first", "high": "max", "low": "min", "close": "last"}
            if "volume" in tmp.columns:
                agg_dict["volume"] = "sum"
            src = tmp.groupby("_b5", sort=True).agg(agg_dict).reset_index(drop=True)
        else:
            src = None

    if src is None or src.empty or "close" not in src.columns:
        return neutral

    w5 = src.copy()
    if live_price is not None and math.isfinite(float(live_price)) and float(live_price) > 0:
        w5.loc[w5.index[-1], "close"] = float(live_price)
        if "high" in w5.columns:
            w5.loc[w5.index[-1], "high"] = max(float(pd.to_numeric(w5["high"].iloc[-1], errors="coerce") or live_price), float(live_price))
        if "low" in w5.columns:
            w5.loc[w5.index[-1], "low"] = min(float(pd.to_numeric(w5["low"].iloc[-1], errors="coerce") or live_price), float(live_price))

    closes = pd.to_numeric(w5["close"], errors="coerce").astype(float)
    if len(w5) >= 16:
        ma15_s = closes.rolling(15, min_periods=15).mean()
        if "ma15" in w5.columns:
            ma15_s = ma15_s.combine_first(pd.to_numeric(w5["ma15"], errors="coerce").astype(float))
    elif "ma15" in w5.columns:
        ma15_s = pd.to_numeric(w5["ma15"], errors="coerce").astype(float)
    else:
        return neutral

    if len(ma15_s) < 2:
        return neutral
    ma15_curr = float(ma15_s.iloc[-1])
    ma15_prev = float(ma15_s.iloc[-2])
    close_5m = float(closes.iloc[-1])
    if "kc_middle" in w5.columns and pd.notna(w5["kc_middle"].iloc[-1]):
        kcm_5m = float(pd.to_numeric(w5["kc_middle"].iloc[-1], errors="coerce"))
    else:
        kcm_5m = float(closes.ewm(span=20, adjust=False).mean().iloc[-1])

    if not all(math.isfinite(v) and v > 0 for v in (ma15_curr, ma15_prev, close_5m, kcm_5m)):
        return neutral

    # 5M KC 通道計算（若有）
    kc_u_5m = float("nan")
    kc_l_5m = float("nan")
    if {"kc_upper", "kc_lower"}.issubset(w5.columns):
        kc_u_5m = float(pd.to_numeric(w5["kc_upper"].iloc[-1], errors="coerce"))
        kc_l_5m = float(pd.to_numeric(w5["kc_lower"].iloc[-1], errors="coerce"))
    elif len(w5) >= 20 and {"high", "low", "close"}.issubset(w5.columns):
        kc5 = compute_keltner_channels(w5, ema_period=20, atr_period=20, multiplier=2.0)
        kc_u_5m = float(kc5["kc_upper"].iloc[-1])
        kc_l_5m = float(kc5["kc_lower"].iloc[-1])

    slope_5m_pct = (ma15_curr - ma15_prev) / ma15_prev
    kcm_slope_5m_pct = 0.0
    if len(closes) >= 2:
        kcm_prev = float(closes.ewm(span=20, adjust=False).mean().iloc[-2])
        if kcm_prev > 0:
            kcm_slope_5m_pct = (kcm_5m - kcm_prev) / kcm_prev

    thr = max(0.0, float(slope_threshold_pct))
    reversal_thr = max(thr, 0.00010)

    # 判斷大局模式：BEAR_ONLY / BULL_ONLY / CHOP_LOCK / NEUTRAL
    # 1. 5M 通道過窄判定為盤整 CHOP_LOCK
    is_5m_chop = False
    if math.isfinite(kc_u_5m) and math.isfinite(kc_l_5m) and kcm_5m > 0:
        w_pct = (kc_u_5m - kc_l_5m) / kcm_5m
        if w_pct < 0.0035:
            is_5m_chop = True

    # 2. 純空頭模式 (BEAR_ONLY)：5M 價格位於 5M KC 中軌下方，且 5M 中軌或 MA15 斜率明確向下
    is_5m_bear_only = bool(
        not is_5m_chop
        and close_5m < kcm_5m
        and (kcm_slope_5m_pct <= -reversal_thr or slope_5m_pct <= -reversal_thr)
    )
    # 3. 純多頭模式 (BULL_ONLY)：5M 價格位於 5M KC 中軌上方，且 5M 中軌或 MA15 斜率明確向上
    is_5m_bull_only = bool(
        not is_5m_chop
        and close_5m > kcm_5m
        and (kcm_slope_5m_pct >= reversal_thr or slope_5m_pct >= reversal_thr)
    )

    if is_5m_chop:
        master_mode = "CHOP_LOCK"
    elif is_5m_bear_only:
        master_mode = "BEAR_ONLY"
    elif is_5m_bull_only:
        master_mode = "BULL_ONLY"
    else:
        master_mode = "NEUTRAL"

    return {
        "available": True,
        "is_5m_up": bool(slope_5m_pct > thr or (close_5m > kcm_5m and slope_5m_pct >= 0.0)),
        "is_5m_down": bool(slope_5m_pct < -thr or (close_5m < kcm_5m and slope_5m_pct <= 0.0)),
        "is_5m_hard_bearish": bool(slope_5m_pct < -thr and close_5m < kcm_5m),
        "is_5m_hard_bullish": bool(slope_5m_pct > thr and close_5m > kcm_5m),
        "master_mode": master_mode,
        "is_5m_bear_only": is_5m_bear_only,
        "is_5m_bull_only": is_5m_bull_only,
        "is_5m_chop": is_5m_chop,
        "slope_5m_pct": slope_5m_pct,
        "kcm_slope_5m_pct": kcm_slope_5m_pct,
        "close_5m": close_5m,
        "kc_middle_5m": kcm_5m,
        "kc_upper_5m": kc_u_5m if math.isfinite(kc_u_5m) else None,
        "kc_lower_5m": kc_l_5m if math.isfinite(kc_l_5m) else None,
    }


def evaluate_peak_valley_inflection_entry(
    df: pd.DataFrame,
    live_price: Optional[float] = None,
    *,
    symbol: Optional[str] = None,
    config: Optional[PeakValleyEntryConfig] = None,
    df_5m: Optional[pd.DataFrame] = None,
    **overrides: Any,
) -> Dict[str, Any]:
    """雙向峰谷轉向即時開倉判定（1m K 線，解除單邊 KC 方向鎖死並嚴格過濾盤整）。

    流程：
    1. 防盤整總開關（任一成立即禁止開倉）：
       - `(KC_upper - KC_lower) / KC_middle < min_kc_width_pct`
       - `abs(MA5 - MA15) / MA15 < min_ma_spread_pct`
       - `ADX < min_adx`（可透過 `enable_adx_filter` 開關）
    2. 雙向峰谷轉折即時評估（無「KC 向上只評估多 / KC 向下只評估空」限制）：
       - 谷底轉向開多 (`OPEN_LONG`)：`MA5` 斜率由負/平轉正，且當前 K 線站上 MA5（或陽線反彈確認），
         亦可選擇結合 KC 下軌超賣觸軌反彈加速捕獲轉折。
       - 波峰轉向開空 (`OPEN_SHORT`)：`MA5` 斜率由正/平轉負，且當前 K 線跌破 MA5（或陰線回落確認），
         亦可選擇結合 KC 上軌超買觸軌回落加速捕獲轉折。
    """
    resolved_symbol = symbol
    if resolved_symbol is None and isinstance(getattr(df, "attrs", None), dict):
        attr_sym = df.attrs.get("symbol")
        if isinstance(attr_sym, str) and attr_sym.strip():
            resolved_symbol = attr_sym
    cfg = resolve_peak_valley_entry_config(resolved_symbol, config)
    if overrides:
        valid_fields = set(PeakValleyEntryConfig.__dataclass_fields__)
        filtered = {k: v for k, v in overrides.items() if k in valid_fields and v is not None}
        if filtered:
            cfg = replace(cfg, **filtered)

    if df is None or not isinstance(df, pd.DataFrame) or len(df) < 15:
        return {
            "action": "HOLD",
            "side": None,
            "reason": "INSUFFICIENT_KLINES_FOR_INFLECTION",
            "is_consolidation": True,
        }

    working = df.copy()
    last_close_val = pd.to_numeric(working["close"].iloc[-1], errors="coerce")
    has_live_override = bool(
        live_price is not None
        and math.isfinite(float(live_price))
        and float(live_price) > 0
        and not (
            bool(working.iloc[-1].get("is_closed", True))
            and math.isfinite(float(last_close_val))
            and math.isclose(float(live_price), float(last_close_val), rel_tol=1e-12, abs_tol=1e-12)
        )
    )
    if has_live_override:
        working = _build_live_close_frame(working, float(live_price))

    close_s = pd.to_numeric(working["close"], errors="coerce").astype(float)
    open_s = pd.to_numeric(working["open"], errors="coerce").astype(float)
    high_s = pd.to_numeric(working["high"], errors="coerce").astype(float)
    low_s = pd.to_numeric(working["low"], errors="coerce").astype(float)
    if len(close_s) < 3 or not all(math.isfinite(v) and v > 0 for v in (close_s.iloc[-1], open_s.iloc[-1], high_s.iloc[-1], low_s.iloc[-1])):
        return {
            "action": "HOLD",
            "side": None,
            "reason": "INVALID_OHLC_DATA",
            "is_consolidation": True,
        }

    # 若 live_price 修改了最新收盤價，重新動態更新 MA5/MA15/KC/ADX 避免使用舊快照產生滯後或前視偏差
    need_recompute_kc = has_live_override or not {"kc_upper", "kc_middle", "kc_lower"}.issubset(working.columns)
    if need_recompute_kc and len(working) >= max(cfg.kc_ema_period, cfg.kc_atr_period + 1):
        kc = compute_keltner_channels(
            working,
            ema_period=cfg.kc_ema_period,
            atr_period=cfg.kc_atr_period,
            multiplier=cfg.kc_multiplier,
        )
        kc_upper_s = kc["kc_upper"]
        kc_mid_s = kc["kc_middle"]
        kc_lower_s = kc["kc_lower"]
    elif {"kc_upper", "kc_middle", "kc_lower"}.issubset(working.columns):
        kc_upper_s = pd.to_numeric(working["kc_upper"], errors="coerce").astype(float)
        kc_mid_s = pd.to_numeric(working["kc_middle"], errors="coerce").astype(float)
        kc_lower_s = pd.to_numeric(working["kc_lower"], errors="coerce").astype(float)
    else:
        kc = compute_keltner_channels(
            working,
            ema_period=cfg.kc_ema_period,
            atr_period=min(cfg.kc_atr_period, max(2, len(working) - 1)),
            multiplier=cfg.kc_multiplier,
        )
        kc_upper_s = kc["kc_upper"]
        kc_mid_s = kc["kc_middle"]
        kc_lower_s = kc["kc_lower"]

    need_recompute_ma = has_live_override or not {"ma5", "ma15"}.issubset(working.columns)
    ma5_s = close_s.rolling(5, min_periods=5).mean() if need_recompute_ma else pd.to_numeric(working["ma5"], errors="coerce").astype(float)
    ma15_s = close_s.rolling(15, min_periods=15).mean() if need_recompute_ma else pd.to_numeric(working["ma15"], errors="coerce").astype(float)

    if cfg.enable_adx_filter:
        if "adx" in working.columns and not has_live_override and pd.notna(working["adx"].iloc[-1]):
            adx_val = float(working["adx"].iloc[-1])
        else:
            adx_s = _compute_adx_series(high_s, low_s, close_s, period=cfg.adx_period)
            adx_val = float(adx_s.iloc[-1]) if pd.notna(adx_s.iloc[-1]) else None
    else:
        adx_val = None

    ma5_curr, ma5_prev, ma5_prev2 = float(ma5_s.iloc[-1]), float(ma5_s.iloc[-2]), float(ma5_s.iloc[-3])
    ma15_curr = float(ma15_s.iloc[-1])
    kc_u = float(kc_upper_s.iloc[-1])
    kc_m = float(kc_mid_s.iloc[-1])
    kc_m_prev = float(kc_mid_s.iloc[-2]) if pd.notna(kc_mid_s.iloc[-2]) else kc_m
    kc_lo = float(kc_lower_s.iloc[-1])
    c_curr, o_curr = float(close_s.iloc[-1]), float(open_s.iloc[-1])
    h_curr, l_curr = float(high_s.iloc[-1]), float(low_s.iloc[-1])
    h_prev, l_prev = float(high_s.iloc[-2]), float(low_s.iloc[-2])

    is_cons, cons_reason, cons_metrics = evaluate_peak_valley_consolidation(
        kc_upper=kc_u,
        kc_middle=kc_m,
        kc_lower=kc_lo,
        ma5=ma5_curr,
        ma15=ma15_curr,
        adx=adx_val,
        config=cfg,
    )

    if not all(math.isfinite(v) and v > 0 for v in (ma5_curr, ma5_prev, ma5_prev2, ma15_curr)):
        return {
            "action": "HOLD",
            "side": None,
            "reason": "INVALID_MA_VALUES",
            "is_consolidation": True,
            **cons_metrics,
        }

    prev_ma5_slope = ma5_prev - ma5_prev2
    curr_ma5_slope = ma5_curr - ma5_prev
    slope_floor = c_curr * max(0.0, cfg.min_slope_pct)
    kc_direction = "UP" if kc_m > kc_m_prev else ("DOWN" if kc_m < kc_m_prev else "FLAT")

    trend_5m = _resolve_5m_trend_state(
        df_5m,
        working,
        live_price,
        slope_threshold_pct=cfg.inflection_5m_slope_threshold_pct,
    )

    common_metrics: Dict[str, Any] = {
        **cons_metrics,
        "close": c_curr,
        "open": o_curr,
        "ma5": ma5_curr,
        "ma5_prev": ma5_prev,
        "ma15": ma15_curr,
        "kc_upper": kc_u,
        "kc_middle": kc_m,
        "kc_lower": kc_lo,
        "kc_direction": kc_direction,
        "prev_ma5_slope": prev_ma5_slope,
        "curr_ma5_slope": curr_ma5_slope,
        "trend_5m": trend_5m,
        "ma_spread_exempted": False,
    }

    ma_entangled_only = is_cons and cons_reason == "CONSOLIDATION_MA_ENTANGLED"
    if is_cons and not ma_entangled_only:
        return {
            "action": "HOLD",
            "side": None,
            "reason": cons_reason,
            "is_consolidation": True,
            "trigger_mode": None,
            **common_metrics,
        }

    # 1B. 橫盤窄幅箱體震盪與均線頻繁穿刺防呆：避免困在震盪區間內追在波段末梢
    if not is_cons:
        lookback_n = max(10, min(int(cfg.range_efficiency_lookback), len(working) - 1))
        box_hist = working.iloc[-1 - lookback_n : -1]
        if not box_hist.empty and cfg.min_range_efficiency_in_box > 0:
            box_h = float(pd.to_numeric(box_hist["high"], errors="coerce").max())
            box_l = float(pd.to_numeric(box_hist["low"], errors="coerce").min())
            box_span = box_h - box_l
            inside_box = math.isfinite(box_h) and math.isfinite(box_l) and box_l <= c_curr <= box_h
            if inside_box and box_span > 0 and (box_span / max(c_curr, 1e-12)) < cfg.box_chop_max_range_pct:
                start_close = float(close_s.iloc[-1 - lookback_n])
                net_disp = abs(c_curr - start_close) if math.isfinite(start_close) else 0.0
                range_efficiency = net_disp / box_span
                common_metrics["range_efficiency"] = range_efficiency
                common_metrics["box_range_pct"] = box_span / max(c_curr, 1e-12)
                if range_efficiency < cfg.min_range_efficiency_in_box:
                    return {
                        "action": "HOLD",
                        "side": None,
                        "reason": "CONSOLIDATION_RANGE_BOX_CHOP",
                        "is_consolidation": True,
                        "trigger_mode": None,
                        **common_metrics,
                    }

    is_bullish_candle = c_curr > o_curr
    is_bearish_candle = c_curr < o_curr
    long_confirm = (c_curr > ma5_curr and is_bullish_candle) if cfg.require_directional_candle else (c_curr > ma5_curr or is_bullish_candle)
    short_confirm = (c_curr < ma5_curr and is_bearish_candle) if cfg.require_directional_candle else (c_curr < ma5_curr or is_bearish_candle)

    # 1. 通道極值區峰谷反轉策略（多空雙向對稱）：真正在峰頂開空、谷底開多，不等破中軌
    #    a. 確認剛經歷過波峰/波谷：最近 1~3 根 1M K 線內，曾觸及或超過 KC 上/下軌極值緩衝區
    ext_lb = max(1, min(int(cfg.extreme_pivot_lookback_bars), len(working) - 1))
    recent_highs = high_s.iloc[-ext_lb:]
    recent_lows = low_s.iloc[-ext_lb:]
    recent_kcu = kc_upper_s.iloc[-ext_lb:]
    recent_kcm = kc_mid_s.iloc[-ext_lb:]
    recent_kcl = kc_lower_s.iloc[-ext_lb:]
    band_ratio = max(0.1, min(1.0, float(cfg.extreme_pivot_band_ratio)))

    # 上軌極值緩衝區：KC_middle + 0.75 * (KC_upper - KC_middle)
    upper_extreme_zone = recent_kcm + band_ratio * (recent_kcu - recent_kcm)
    # 下軌極值緩衝區：KC_middle - 0.75 * (KC_middle - KC_lower)
    lower_extreme_zone = recent_kcm - band_ratio * (recent_kcm - recent_kcl)

    touched_upper_extreme = bool((recent_highs >= upper_extreme_zone).any())
    touched_lower_extreme = bool((recent_lows <= lower_extreme_zone).any())

    # b. 反轉訊號確認：
    #    開空：實體陰線（close < open），收盤跌破 MA5，且 MA5 斜率開始由正轉負（下彎）
    #    開多：實體陽線（close > open），收盤站上 MA5，且 MA5 斜率開始由負轉正（上勾）
    prev_turn_floor = c_curr * max(0.0, float(cfg.min_prev_turn_slope_pct))
    extreme_short_signal = bool(
        is_bearish_candle
        and c_curr < ma5_curr
        and curr_ma5_slope < -slope_floor
        and prev_ma5_slope > -prev_turn_floor
    )
    extreme_long_signal = bool(
        is_bullish_candle
        and c_curr > ma5_curr
        and curr_ma5_slope > slope_floor
        and prev_ma5_slope < prev_turn_floor
    )

    # c. 安全空間檢查：
    #    開空：當前價格仍位於 KC 中軌附近或上方（kc_pos >= 0.45），確保下方到 KC 下軌有足夠空間，絕不在地板追空
    #    開多：當前價格仍位於 KC 中軌附近或下方（kc_pos <= 0.55），確保上方到 KC 上軌有足夠空間，絕不在天花板追多
    kc_span = max(kc_u - kc_lo, 1e-12)
    kc_pos = (c_curr - kc_lo) / kc_span
    common_metrics["kc_pos"] = kc_pos
    common_metrics["touched_upper_extreme"] = touched_upper_extreme
    common_metrics["touched_lower_extreme"] = touched_lower_extreme

    room_for_short = kc_pos >= float(cfg.extreme_pivot_min_short_kc_pos)
    room_for_long = kc_pos <= float(cfg.extreme_pivot_max_long_kc_pos)

    extreme_peak_reversal_short = bool(touched_upper_extreme and extreme_short_signal and room_for_short)
    extreme_valley_reversal_long = bool(touched_lower_extreme and extreme_long_signal and room_for_long)

    # 傳統峰谷拐點與超賣觸軌反轉（保持相容）
    valley_inflection = (prev_ma5_slope < -prev_turn_floor) and (curr_ma5_slope > slope_floor) and long_confirm
    peak_inflection = (prev_ma5_slope > prev_turn_floor) and (curr_ma5_slope < -slope_floor) and short_confirm

    # 2. 低延遲選配：KC 上下軌超買/超賣觸軌反轉（提前在 MA5 拐角加速點捕獲反轉）
    oversold_band_touch = min(l_curr, l_prev) <= kc_lo * (1.0 + cfg.kc_band_touch_buffer_pct)
    overbought_band_touch = max(h_curr, h_prev) >= kc_u * (1.0 - cfg.kc_band_touch_buffer_pct)
    kc_oversold_long = bool(
        cfg.enable_kc_band_reversal
        and oversold_band_touch
        and is_bullish_candle
        and c_curr > ma5_curr
        and curr_ma5_slope > max(slope_floor, prev_ma5_slope)
    )
    kc_overbought_short = bool(
        cfg.enable_kc_band_reversal
        and overbought_band_touch
        and is_bearish_candle
        and c_curr < ma5_curr
        and curr_ma5_slope < min(-slope_floor, prev_ma5_slope)
    )

    # 1C. 第一買點順勢豁免 min_ma_spread_pct（多空對稱）：
    #     5M 順勢，或一段趨勢後首度回踩 KC_lower 止跌（做多）/ 首度反抽 KC_upper 受阻（做空）
    prior_lb = max(4, min(int(cfg.inflection_prior_trend_lookback), len(working) - 3))
    prior_slice_end = len(working) - 2
    prior_slice_start = max(0, prior_slice_end - prior_lb)
    prior_uptrend_pullback = False
    prior_downtrend_rebound = False
    if prior_slice_end > prior_slice_start + 1:
        prior_closes = close_s.iloc[prior_slice_start:prior_slice_end]
        prior_kcm = kc_mid_s.iloc[prior_slice_start:prior_slice_end]
        prior_kcl = kc_lower_s.iloc[prior_slice_start:prior_slice_end]
        prior_kcu = kc_upper_s.iloc[prior_slice_start:prior_slice_end]
        prior_lows = low_s.iloc[prior_slice_start:prior_slice_end]
        prior_highs = high_s.iloc[prior_slice_start:prior_slice_end]
        prev_touches_lower = int((prior_lows <= prior_kcl * (1.0 + cfg.kc_band_touch_buffer_pct)).sum())
        prev_touches_upper = int((prior_highs >= prior_kcu * (1.0 - cfg.kc_band_touch_buffer_pct)).sum())
        prior_uptrend_pullback = bool(
            oversold_band_touch
            and (prior_closes > prior_kcm).any()
            and float(prior_kcm.iloc[-1]) >= float(prior_kcm.iloc[0])
            and prev_touches_lower <= 1
        )
        prior_downtrend_rebound = bool(
            overbought_band_touch
            and (prior_closes < prior_kcm).any()
            and float(prior_kcm.iloc[-1]) <= float(prior_kcm.iloc[0])
            and prev_touches_upper <= 1
        )

    long_spread_exempt = bool(
        extreme_valley_reversal_long
        or (
            cfg.enable_inflection_ma_spread_exemption
            and valley_inflection
            and c_curr > ma5_curr
            and (trend_5m["is_5m_up"] or prior_uptrend_pullback)
        )
    )
    short_spread_exempt = bool(
        extreme_peak_reversal_short
        or (
            cfg.enable_inflection_ma_spread_exemption
            and peak_inflection
            and c_curr < ma5_curr
            and (trend_5m["is_5m_down"] or prior_downtrend_rebound)
        )
    )

    if ma_entangled_only:
        want_long = long_spread_exempt
        want_short = short_spread_exempt
    else:
        want_long = extreme_valley_reversal_long or valley_inflection or kc_oversold_long
        want_short = extreme_peak_reversal_short or peak_inflection or kc_overbought_short

    # ══════════════════════════════════════════════════════════════════════
    # 【5M 大局觀最高指導原則】：5M 定多空方向，嚴格禁止逆大勢開倉
    # ══════════════════════════════════════════════════════════════════════
    if getattr(cfg, "enable_5m_master_trend_filter", True) and trend_5m.get("available"):
        master_mode = trend_5m.get("master_mode", "NEUTRAL")
        if master_mode == "CHOP_LOCK":
            return {
                "action": "HOLD",
                "side": None,
                "reason": "BLOCKED_BY_5M_CHOP_LOCK",
                "is_consolidation": True,
                "trigger_mode": None,
                **common_metrics,
            }
        elif master_mode == "BEAR_ONLY":
            # 純空頭模式：全場 100% 鎖死做多邏輯（包含 1M 突破、谷底反轉），只允許做空
            want_long = False
        elif master_mode == "BULL_ONLY":
            # 純多頭模式：全場 100% 鎖死做空邏輯，只允許做多
            want_short = False

    if want_long and not want_short:
        if extreme_valley_reversal_long:
            trigger_mode = "EXTREME_VALLEY_REVERSAL"
        elif valley_inflection:
            trigger_mode = "VALLEY_MA5_INFLECTION"
        else:
            trigger_mode = "KC_LOWER_BAND_OVERSOLD"
        common_metrics["ma_spread_exempted"] = bool(ma_entangled_only and long_spread_exempt)
        return {
            "action": "OPEN_LONG",
            "side": "LONG",
            "reason": f"{trigger_mode}_LONG",
            "is_consolidation": False,
            "trigger_mode": trigger_mode,
            **common_metrics,
        }

    if want_short and not want_long:
        if extreme_peak_reversal_short:
            trigger_mode = "EXTREME_PEAK_REVERSAL"
        elif peak_inflection:
            trigger_mode = "PEAK_MA5_INFLECTION"
        else:
            trigger_mode = "KC_UPPER_BAND_OVERBOUGHT"
        common_metrics["ma_spread_exempted"] = bool(ma_entangled_only and short_spread_exempt)
        return {
            "action": "OPEN_SHORT",
            "side": "SHORT",
            "reason": f"{trigger_mode}_SHORT",
            "is_consolidation": False,
            "trigger_mode": trigger_mode,
            **common_metrics,
        }

    # 3. 第二買點（防踏空補票通道）：強勢突破/跌破 KC 中軌 + 均線順向 + 通道擴張 + 5M 不逆勢
    allow_second_buy = cfg.enable_second_buy_mid_breakout and (not ma_entangled_only or cfg.enable_inflection_ma_spread_exemption)
    if allow_second_buy:
        exp_n = max(1, int(cfg.second_buy_expansion_lookback))
        cross_n = max(1, min(int(cfg.second_buy_cross_lookback_bars), len(close_s) - 1))
        kc_expanding = False
        if len(kc_upper_s) >= exp_n + 1:
            curr_kc_width = kc_u - kc_lo
            prev_widths = (
                kc_upper_s.iloc[-1 - exp_n : -1].astype(float)
                - kc_lower_s.iloc[-1 - exp_n : -1].astype(float)
            )
            avg_prev_width = float(prev_widths.mean())
            kc_expanding = (
                math.isfinite(curr_kc_width)
                and math.isfinite(avg_prev_width)
                and avg_prev_width > 0
                and curr_kc_width > avg_prev_width
            )
            common_metrics["kc_width_curr"] = curr_kc_width
            common_metrics["kc_width_prev_avg"] = avg_prev_width
            common_metrics["kc_expanding"] = kc_expanding

        prior_below_mid_or_ma15 = any(
            (
                math.isfinite(float(close_s.iloc[-1 - i]))
                and (
                    (math.isfinite(float(kc_mid_s.iloc[-1 - i])) and float(close_s.iloc[-1 - i]) <= float(kc_mid_s.iloc[-1 - i]))
                    or (math.isfinite(float(ma15_s.iloc[-1 - i])) and float(close_s.iloc[-1 - i]) <= float(ma15_s.iloc[-1 - i]))
                )
            )
            for i in range(1, cross_n + 1)
        )
        prior_above_mid_or_ma15 = any(
            (
                math.isfinite(float(close_s.iloc[-1 - i]))
                and (
                    (math.isfinite(float(kc_mid_s.iloc[-1 - i])) and float(close_s.iloc[-1 - i]) >= float(kc_mid_s.iloc[-1 - i]))
                    or (math.isfinite(float(ma15_s.iloc[-1 - i])) and float(close_s.iloc[-1 - i]) >= float(ma15_s.iloc[-1 - i]))
                )
            )
            for i in range(1, cross_n + 1)
        )

        second_buy_long = bool(
            is_bullish_candle
            and c_curr > kc_m
            and c_curr > ma15_curr
            and prior_below_mid_or_ma15
            and ma5_curr >= ma15_curr
            and curr_ma5_slope > slope_floor
            and kc_expanding
            and not trend_5m["is_5m_hard_bearish"]
        )
        second_buy_short = bool(
            is_bearish_candle
            and c_curr < kc_m
            and c_curr < ma15_curr
            and prior_above_mid_or_ma15
            and ma5_curr <= ma15_curr
            and curr_ma5_slope < -slope_floor
            and kc_expanding
            and not trend_5m["is_5m_hard_bullish"]
        )

        if getattr(cfg, "enable_5m_master_trend_filter", True) and trend_5m.get("available"):
            master_mode = trend_5m.get("master_mode", "NEUTRAL")
            if master_mode == "BEAR_ONLY":
                second_buy_long = False
            elif master_mode == "BULL_ONLY":
                second_buy_short = False
            elif master_mode == "CHOP_LOCK":
                second_buy_long = False
                second_buy_short = False

        if second_buy_long and not second_buy_short:
            common_metrics["ma_spread_exempted"] = bool(ma_entangled_only)
            return {
                "action": "OPEN_LONG",
                "side": "LONG",
                "reason": "KC_MID_BREAKOUT_EXPANSION_LONG",
                "is_consolidation": False,
                "trigger_mode": "KC_MID_BREAKOUT_EXPANSION",
                **common_metrics,
            }

        if second_buy_short and not second_buy_long:
            common_metrics["ma_spread_exempted"] = bool(ma_entangled_only)
            return {
                "action": "OPEN_SHORT",
                "side": "SHORT",
                "reason": "KC_MID_BREAKDOWN_EXPANSION_SHORT",
                "is_consolidation": False,
                "trigger_mode": "KC_MID_BREAKDOWN_EXPANSION",
                **common_metrics,
            }

    if ma_entangled_only:
        return {
            "action": "HOLD",
            "side": None,
            "reason": cons_reason,
            "is_consolidation": True,
            "trigger_mode": None,
            **common_metrics,
        }

    return {
        "action": "HOLD",
        "side": None,
        "reason": "CONFLICTING_INFLECTION_SIGNALS" if (want_long and want_short) else "NO_PEAK_VALLEY_INFLECTION",
        "is_consolidation": False,
        "trigger_mode": None,
        **common_metrics,
    }


def compute_ema5_and_zlema5(close: pd.Series) -> Tuple[pd.Series, pd.Series]:
    """Compute responsive EMA5 and Zero-Lag EMA5 (ZLEMA5) from a close series."""
    series = pd.to_numeric(close, errors="coerce").astype(float)
    ema5 = series.ewm(span=5, adjust=False).mean()
    lag = (5 - 1) // 2  # 2 bars for 5-period ZLEMA
    de_lagged = 2.0 * series - series.shift(lag).fillna(series)
    zlema5 = de_lagged.ewm(span=5, adjust=False).mean()
    return ema5, zlema5


def _build_live_close_frame(df: pd.DataFrame, live_price: float) -> pd.DataFrame:
    """Return a copy of df whose latest forming candle reflects live_price."""
    working = df.copy()
    if working.empty:
        return working
    last_idx = working.index[-1]
    is_closed = working.iloc[-1].get("is_closed")
    if is_closed is False or (type(is_closed).__name__ == "bool_" and not bool(is_closed)):
        working.loc[last_idx, "close"] = float(live_price)
        if "high" in working.columns:
            working.loc[last_idx, "high"] = max(float(working.loc[last_idx, "high"]), float(live_price))
        if "low" in working.columns:
            working.loc[last_idx, "low"] = min(float(working.loc[last_idx, "low"]), float(live_price))
    else:
        last_ts = float(working.iloc[-1].get("timestamp") or working.iloc[-1].get("time") or 0.0)
        new_row = {
            "timestamp": last_ts + 60_000.0 if last_ts > 1e11 else (last_ts * 1000.0 + 60_000.0 if last_ts > 0 else 0.0),
            "time": (last_ts + 60.0 if last_ts < 1e11 and last_ts > 0 else (last_ts / 1000.0 + 60.0 if last_ts >= 1e11 else 0.0)),
            "open": float(live_price),
            "high": float(live_price),
            "low": float(live_price),
            "close": float(live_price),
            "volume": 0.0,
            "is_closed": False,
        }
        working = pd.concat([working, pd.DataFrame([new_row])], ignore_index=True)
    return working


def evaluate_realtime_ma_kc_entry(
    df: pd.DataFrame,
    live_price: float,
    *,
    use_zlema: bool = True,
    min_slope_pct: float = 0.0002,
    min_slope_atr: float = 0.03,
) -> Dict[str, Any]:
    """Evaluate tick-level entry using EMA5/ZLEMA5 and KC middle live slopes.

    Updates the forming candle in-memory with ``live_price`` so aligned
    momentum triggers immediately instead of waiting for the 1m candle close,
    while filtering flat sideways noise via ``min_slope_pct`` and ``min_slope_atr``.
    """
    if df is None or len(df) < 20 or not math.isfinite(live_price) or live_price <= 0:
        return {"action": "HOLD", "side": None, "reason": "INVALID_OR_INSUFFICIENT_DATA"}

    working = _build_live_close_frame(df, float(live_price))
    close_s = pd.to_numeric(working["close"], errors="coerce").astype(float)
    ema5_s, zlema5_s = compute_ema5_and_zlema5(close_s)
    fast_s = zlema5_s if use_zlema else ema5_s

    kc = compute_keltner_channels(working, ema_period=20, atr_period=14, multiplier=1.5)
    kc_mid_s = kc["kc_middle"]
    atr_s = kc["atr"]

    fast_curr = float(fast_s.iloc[-1])
    fast_prev = float(fast_s.iloc[-2])
    kc_curr = float(kc_mid_s.iloc[-1])
    kc_prev = float(kc_mid_s.iloc[-2])
    atr_val = float(atr_s.iloc[-2]) if pd.notna(atr_s.iloc[-2]) else float(atr_s.iloc[-1])

    if not all(math.isfinite(v) and v > 0 for v in (fast_curr, fast_prev, kc_curr, kc_prev, atr_val)):
        return {"action": "HOLD", "side": None, "reason": "INVALID_INDICATOR_VALUES"}

    fast_slope = fast_curr - fast_prev
    kc_slope = kc_curr - kc_prev
    fast_slope_pct = fast_slope / live_price
    kc_slope_pct = kc_slope / live_price
    fast_slope_atr = fast_slope / atr_val
    kc_slope_atr = kc_slope / atr_val

    metrics = {
        "fast_name": "ZLEMA5" if use_zlema else "EMA5",
        "fast_value": fast_curr,
        "fast_slope": fast_slope,
        "fast_slope_pct": fast_slope_pct,
        "fast_slope_atr": fast_slope_atr,
        "kc_middle": kc_curr,
        "kc_slope": kc_slope,
        "kc_slope_pct": kc_slope_pct,
        "kc_slope_atr": kc_slope_atr,
        "atr": atr_val,
    }

    bull_threshold_ok = (
        fast_slope_pct >= min_slope_pct
        and kc_slope_pct >= min_slope_pct * 0.25
        and fast_slope_atr >= min_slope_atr
        and kc_slope_atr >= min_slope_atr * 0.25
    )
    bear_threshold_ok = (
        fast_slope_pct <= -min_slope_pct
        and kc_slope_pct <= -min_slope_pct * 0.25
        and fast_slope_atr <= -min_slope_atr
        and kc_slope_atr <= -min_slope_atr * 0.25
    )

    if bull_threshold_ok and live_price > fast_curr > kc_curr:
        return {
            "action": "OPEN_LONG",
            "side": "LONG",
            "reason": "REALTIME_FAST_MA_KC_ALIGNED_LONG",
            **metrics,
        }

    if bear_threshold_ok and live_price < fast_curr < kc_curr:
        return {
            "action": "OPEN_SHORT",
            "side": "SHORT",
            "reason": "REALTIME_FAST_MA_KC_ALIGNED_SHORT",
            **metrics,
        }

    if (fast_slope > 0 and kc_slope < 0) or (fast_slope < 0 and kc_slope > 0):
        reason = "SLOPE_DIRECTION_DIVERGENCE"
    elif not bull_threshold_ok and not bear_threshold_ok:
        reason = "FLAT_SLOPE_NOISE_FILTERED"
    else:
        reason = "PRICE_STRUCTURE_NOT_ALIGNED"

    return {"action": "HOLD", "side": None, "reason": reason, **metrics}


def predict_maker_trigger_price(
    prev_ema5: float,
    prev_kc_mid: float,
    side: str,
    *,
    ema_period: int = 5,
    kc_period: int = 20,
    min_slope_pct: float = 0.0002,
) -> Optional[Dict[str, Any]]:
    """Solve for the exact price where EMA5 and KC middle both meet min_slope_pct.

    Uses EMA_t - EMA_{t-1} = alpha * (P - EMA_{t-1}), yielding:
      LONG:  P >= EMA_{t-1} * (1 + min_slope_pct / alpha)
      SHORT: P <= EMA_{t-1} * (1 - min_slope_pct / alpha)
    """
    side_up = str(side or "").upper()
    if side_up not in ("LONG", "SHORT"):
        return None
    if not (
        math.isfinite(prev_ema5) and prev_ema5 > 0
        and math.isfinite(prev_kc_mid) and prev_kc_mid > 0
        and ema_period >= 2 and kc_period >= 2
        and math.isfinite(min_slope_pct) and min_slope_pct >= 0
    ):
        return None

    alpha_fast = 2.0 / (ema_period + 1)
    alpha_kc = 2.0 / (kc_period + 1)
    sign = 1.0 if side_up == "LONG" else -1.0

    trigger_ema5 = prev_ema5 * (1.0 + sign * (min_slope_pct / alpha_fast))
    trigger_kc_mid = prev_kc_mid * (1.0 + sign * (min_slope_pct / alpha_kc))
    trigger_price = max(trigger_ema5, trigger_kc_mid) if side_up == "LONG" else min(trigger_ema5, trigger_kc_mid)

    return {
        "side": side_up,
        "trigger_price": trigger_price,
        "trigger_ema5": trigger_ema5,
        "trigger_kc_mid": trigger_kc_mid,
        "min_slope_pct": min_slope_pct,
    }
