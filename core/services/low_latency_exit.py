"""低延遲即時平倉、防禦性風控與條件式即時反手（Flip）引擎。

支援 1M K 線 + WebSocket Tick 即時報價，涵蓋：
1. Tick 級即時平倉：反向 MA5 拐點、KC 通道極值反轉止盈、動態保底鎖利、假轉折快速破位與硬止損。
2. 1M 收盤確認風險控制：進場後 KC 通道迅速縮窄進入盤整且浮虧時止損離場。
3. 條件式即時反手（Flip）：僅在「明確 MA5 拐點反轉」或「KC 通道極值反轉止盈」，且市場通過更嚴格之
   非盤整空間濾網（確保可覆蓋雙倍吃單手續費）時才允許反手；若因盤整、保底鎖利、或止損平倉則嚴禁反手。
4. 交易所 API 嚴格執行順序與單根 K 線防重複刷單鎖：
   先送出市價平倉單 -> 確認交易所/帳戶持倉完全歸零 (position == 0) 並結算損益 -> 再送出反向開倉單；
   同一根 1M K 線內最多執行一次「平倉 -> 反手」，反手後鎖死本根 K 線內的再次平倉與二次反手權限。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import inspect
import logging
import math
import time
from typing import Any, Awaitable, Callable, Dict, FrozenSet, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

from core.services.low_latency_entry import (
    PeakValleyEntryConfig,
    _build_live_close_frame,
    _compute_adx_series,
    evaluate_peak_valley_consolidation,
    resolve_5m_trend_master_state,
)
from core.services.technical_indicators import compute_keltner_channels
from core.services.strategies.outer_strategy import ck_direction
from core.symbol_aliases import is_lobster_symbol, is_pepe_symbol


logger = logging.getLogger("LowLatencyExit")

# ==============================================================================
# 【KC 肯特納通道策略 — 三層出場與防洗盤風控參數設定區】（可於此處統一微調）
# ==============================================================================
HARD_SL_ATR_MULT: float = 2.0        # 第一層：固定硬止損 ATR 倍數（進場價 ± 2.0 * ATR，盤中即時 Tick 觸發）
EXIT_BUFFER_ATR_MULT: float = 1.0    # 第二層：趨勢追蹤 KC 中軌止損緩衝區倍數（連續 2 根已收盤價跌破/漲破 中軌 ± 1.0 * ATR 才平倉）
TRAILING_START_ATR: float = 1.5      # 第三層：移動止損啟動門檻 ATR 倍數（峰值浮盈達 1.5 * ATR 且覆蓋雙邊手續費才啟動）
TRAILING_DIST_ATR: float = 2.0       # 第三層：移動止損追蹤回撤距離 ATR 倍數（與波段極值保持至少 2.0 * ATR 追蹤距離）
ROUND_TRIP_FEE_PCT: float = 0.001    # 雙邊吃單手續費率緩衝（0.10%，作為啟動移動止損之保本覆蓋門檻）

ACTIVE_EXIT_PV_PREFIX = "ACTIVE_EXIT_PV_"
ACTIVE_EXIT_KC_PREFIX = "ACTIVE_EXIT_KC_"

# 允許觸發「條件式即時反手 / 反手觀察期（Flip Watch）」的明確趨勢反轉平倉原因集合
PV_FLIP_ELIGIBLE_EXIT_REASONS: FrozenSet[str] = frozenset({
    "ACTIVE_EXIT_PV_EXTREME_FRACTAL_LONG",
    "ACTIVE_EXIT_PV_EXTREME_FRACTAL_SHORT",
    "ACTIVE_EXIT_KC_MID_BUFFER_LONG",
    "ACTIVE_EXIT_KC_MID_BUFFER_SHORT",
    "ACTIVE_EXIT_KC_TREND_REVERSAL_LONG",
    "ACTIVE_EXIT_KC_TREND_REVERSAL_SHORT",
    "ACTIVE_EXIT_PV_REVERSE_INFLECTION_LONG",
    "ACTIVE_EXIT_PV_REVERSE_INFLECTION_SHORT",
    "ACTIVE_EXIT_PV_KC_EXTREME_TP_LONG",
    "ACTIVE_EXIT_PV_KC_EXTREME_TP_SHORT",
})

# 嚴禁觸發即時反手、必須空倉觀望的平倉原因集合（硬止損 / 移動止損 / 盤整止損 / 轉盤整 FLAT 平倉 / 假轉折止損）
PV_FLIP_FORBIDDEN_EXIT_REASONS: FrozenSet[str] = frozenset({
    "ACTIVE_EXIT_KC_HARD_STOP_LOSS",
    "ACTIVE_EXIT_KC_TREND_FLAT_LONG",
    "ACTIVE_EXIT_KC_TREND_FLAT_SHORT",
    "ACTIVE_EXIT_PV_CONSOLIDATION_FAIL_SL",
    "ACTIVE_EXIT_PV_TRAILING_PROFIT_LOCK",
    "ACTIVE_EXIT_PV_FAKE_INFLECTION_SWING_BREAK",
    "ACTIVE_EXIT_PV_MAX_STOP_LOSS",
})


@dataclass(frozen=True)
class PeakValleyExitConfig:
    """獨立可調參數設定：平倉敏感度、保底鎖利、一般開倉 vs 反手開倉濾網門檻、防重複鎖。"""

    # ── 1. 反向峰谷拐點即時平倉參數 (Tick 級，不等 1M 收盤) ─────────────────────
    ma5_slope_eps: float = 0.0                # MA5 拐點斜率變動最小閾值（0.0 = 純斜率正負反轉）
    require_tick_cross_ma5: bool = True       # True 時要求即時成交價同步跌破(平多)/突破(平空) MA5

    # ── 2. KC 通道極值反轉精準止盈參數 (Tick 級) ─────────────────────────────
    kc_extreme_retrace_pct: float = 0.0025    # 觸及 KC 外軌後，自極值回撤/反彈達此比例即止盈（預設 0.25%）
    enable_kc_prev_bar_break: bool = True     # 觸及外軌後，即時價跌破前 1M 低點或突破前 1M 高點即止盈
    kc_extreme_touch_lookback_bars: int = 3   # 觸及 KC 外軌標記之有效記憶窗口（預設最近 3 根 1M K 棒）

    # ── 3. 動態保底鎖利 Trailing Stop 參數 (Tick 級) ─────────────────────────
    trailing_activate_pct: float = 0.008      # 未實現毛利達此門檻啟動移動鎖利（預設 +0.8%）
    trailing_profit_giveback_ratio: float = 0.30  # 獲利從最高點回吐達此比例立即鎖利平倉（預設回吐 30%）
    trailing_price_retrace_pct: float = 0.0050    # 價格自最高/最低點回撤達此比例觸發移動鎖利（預設 0.50%）
    use_net_roe_for_trailing: bool = False    # True 時改以槓桿後淨 ROE% 計算啟動與回吐門檻

    # ── 4. 防禦性快速止損與盤整縮口止損參數 ────────────────────────────────────
    max_stop_loss_pct: float = 0.012          # 單筆最大價差止損比例（預設 1.2%）
    swing_lookback_bars: int = 8              # 進場前最近波谷(多)/波峰(空)結構回溯 K 棒數
    swing_break_buffer_pct: float = 0.0005    # 波谷/波峰破位止損容差（預設 0.05%）
    kc_collapse_width_pct: float = 0.006      # 收線後 KC 寬度縮窄至低於此門檻視為陷入盤整（預設 0.6%）
    kc_collapse_shrink_ratio: float = 0.70    # 或 KC 寬度相對進場時縮水至 <= 70%（預設 0.70）

    # ── 5. 條件式即時反手 (Flip) 與較嚴格濾網門檻 ──────────────────────────────
    enable_conditional_flip: bool = True      # 是否啟用「條件式平倉兼即時反手」
    normal_min_kc_width_pct: float = 0.008    # 一般開倉 KC 通道寬度下限（對照基準，預設 0.8%）
    flip_min_kc_width_pct: float = 0.012      # 反手開倉 KC 通道寬度安全閾值（須嚴於一般開倉，預設 1.2%，覆蓋雙倍 Taker 手續費）
    flip_min_ma_spread_pct: float = 0.0018    # 反手開倉均線發散度 |MA5-MA15|/MA15 下限（嚴於一般開倉 0.12%，預設 0.18%）
    flip_enable_adx_filter: bool = True       # 反手開倉是否檢查 ADX 趨勢強度
    flip_min_adx: float = 25.0                # 反手開倉 ADX 下限（嚴於一般開倉 20.0，預設 25.0）

    # ── 6. 單根 K 線防重複刷單鎖與空倉冷卻設定 (Anti-Whipsaw) ──────────────────
    enforce_same_bar_flip_exit_lock: bool = True  # 反手成功後，鎖死同一根 1M K 線內的再次平倉與二次反手（等下一根 1M 開盤再評估）
    same_bar_reverse_allowed: bool = True         # 是否允許同一根 K 線內執行最多一次「合條件平倉 -> 反手」
    reentry_cooldown_seconds: float = 180.0       # 平倉後的強制空倉冷卻秒數（預設 180 秒，即 3 根 1M K 線）
    reentry_cooldown_bars: int = 3                # 平倉後的強制空倉冷卻 K 棒數（預設 3 根 1M）
    flip_on_reverse_inflection: bool = True       # 是否允許反向反轉時單棒內直接反手

    # ── 7. 脫鉤式「平倉後反手觀察期 (Flip Watch State)」與二次結構確認參數 ─────
    enable_flip_watch: bool = True                      # True: 平倉與反向開倉徹底脫鉤，先平倉再進觀察期二次確認
    flip_watch_window_bars: int = 8                     # 觀察期有效窗口 N（未來 N 根 1M K 線，超時自動失效）
    flip_watch_require_retest: bool = True              # 條件(a): 突破/跌破 KC 中軌後須經歷至少 1 根回測不破並突破前高/跌破前低
    flip_watch_retest_buffer_pct: float = 0.0           # 條件(a): 回測守穩 KC_middle / MA15 之容差比例（預設 0.0）
    flip_watch_enable_5m_filter: bool = True            # 條件(b): 啟用 5M 多週期趨勢濾網（禁止強逆勢開單）
    flip_watch_5m_ma_period: int = 15                   # 條件(b): 5M 慢線均線週期（預設 MA15）
    flip_watch_5m_slope_threshold_pct: float = 0.0001   # 條件(b): 5M MA15 強趨勢斜率閾值比例（預設 0.01%/根）
    flip_watch_require_5m_data: bool = False            # True 時缺少 5M 資料即擋單；False 時嘗試從 1M 重採樣或資料不足時放行
    flip_watch_expansion_lookback: int = 2              # 條件(c): 通道寬度開口擴張比較之前 K 棒數（預設前 2 根）
    flip_watch_expansion_ratio: float = 1.0             # 條件(c): 當前通道寬度須大於前 N 根平均寬度 * 此比例（預設 1.0）

    # ── 8. 順勢通道內「非峰谷三點禁平」防洗護欄 ──────────────────────────────
    require_3point_pivot_when_in_trend_channel: bool = True  # 仍在原趨勢且在中軌/通道內時，無已收線三點峰谷禁平（擋下普通拐點/極值反轉/鎖利）

    # ── 9. KC 肯特納通道分層出場與極值分型逃頂參數（預設主控模式） ─────────────────
    exit_mode: str = "kc_layered"                         # "kc_layered": 分層出場架構；"legacy_pv": 舊版快線峰谷低延遲模式
    hard_sl_atr_mult: float = HARD_SL_ATR_MULT            # 第一層：固定硬止損倍數（進場價 ± 2.0 * ATR，盤中即時觸發）
    use_kc_opposite_outer_hard_sl: bool = False           # True 時同時以 KC 反向外軌外緣作為輔助硬止損上限
    extreme_fractal_kc_ratio: float = 0.85                # 第二層：極值區三點分型逃頂位置門檻（距中軌至少 85% 通道半寬）
    extreme_fractal_min_profit_pct: float = 0.003         # 第二層：極值分型主動逃頂最低未實現獲利保護（預設 0.30%）
    trailing_activate_min_pct: float = 0.0060             # 第三層：移動止損最低價差啟動門檻（升級預設 0.60%）
    exit_buffer_atr_mult: float = EXIT_BUFFER_ATR_MULT    # 第四層：KC 中軌趨勢追蹤緩衝區倍數（連續 2 根收盤 1.0 * ATR）
    trailing_start_atr: float = TRAILING_START_ATR        # 第三層：移動止損啟動 ATR 門檻（1.5 * ATR）
    trailing_dist_atr: float = TRAILING_DIST_ATR          # 第三層：移動止損追蹤距離（放寬至 2.0 * ATR）
    trailing_dist_retrace_pct: float = 0.0050             # 第三層：價格自極值回撤容忍百分比門檻（預設 0.50%）
    trailing_pullback_boost_ratio: float = 1.5            # 順勢推進期間追蹤距離提高倍數（抗回踩）
    min_kc_mid_reversal_slope_pct: float = 0.00015        # 中軌明顯反轉斜率百分比門檻（預設 0.015% / 根）
    round_trip_fee_pct: float = ROUND_TRIP_FEE_PCT        # 雙邊手續費率門檻（預設 0.10%）

    # ── 10. 跨幣種大盤共振鎖倉參數 (Cross-Symbol Trend Sync Lock) ─────────────────
    enable_cross_symbol_sync_lock: bool = True            # 是否啟用跨幣種大趨勢共振鎖倉
    sync_lock_trailing_boost_ratio: float = 1.3           # 共振鎖定生效期間，移動止損追蹤容忍度放大倍率（預設 1.3 倍）
    monitored_sync_symbols: Tuple[str, ...] = (
        "1000PEPE/USDT",
        "龙虾/USDT",
    )

    # ── 11. 5M 大局觀順勢出場與結構破壞止損 ─────────────────────────────────────
    enable_5m_master_exit_policy: bool = True             # 是否在 5M 純單邊模式下啟用結構破壞與大週期鎖利
    swing_break_atr_mult: float = 0.5                     # 突破/跌破開倉前波段極值之 ATR 緩衝距離（0.5 * ATR）
    master_trailing_activate_pct: float = 0.010           # 5M 順勢單波段大幅獲利移動鎖利啟動門檻（預設 1.0%）
    master_trailing_giveback_pct: float = 0.005           # 啟動後自最高/最低利潤回撤平倉門檻（預設 0.5%）


class SafeFrameRef:
    """Identity-safe DataFrame reference for DataFrame.attrs (avoids pandas __eq__ ValueError on concat)."""

    __slots__ = ("frame",)

    def __init__(self, frame: Optional[pd.DataFrame]):
        self.frame = frame

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SafeFrameRef) and self.frame is other.frame


def wrap_frame_ref(obj: Any) -> Any:
    """Wrap a DataFrame in SafeFrameRef before storing inside DataFrame.attrs."""
    if isinstance(obj, pd.DataFrame):
        return SafeFrameRef(obj)
    return obj


def unwrap_frame_ref(obj: Any) -> Optional[pd.DataFrame]:
    """Unwrap a SafeFrameRef (or return DataFrame directly) to Optional[pd.DataFrame]."""
    if isinstance(obj, SafeFrameRef):
        obj = obj.frame
    return obj if isinstance(obj, pd.DataFrame) else None


DEFAULT_PEAK_VALLEY_EXIT_CONFIG = PeakValleyExitConfig()
PEPE_PEAK_VALLEY_EXIT_CONFIG = PeakValleyExitConfig(
    kc_collapse_width_pct=0.0038,
    normal_min_kc_width_pct=0.0048,
    flip_min_kc_width_pct=0.0075,
    flip_min_ma_spread_pct=0.0010,
    flip_min_adx=16.0,
)
LOBSTER_PEAK_VALLEY_EXIT_CONFIG = PeakValleyExitConfig(
    kc_collapse_width_pct=0.0022,
    normal_min_kc_width_pct=0.0028,
    flip_min_kc_width_pct=0.0030,
    flip_min_ma_spread_pct=0.00045,
    flip_min_adx=18.0,
)


def resolve_peak_valley_exit_config(
    symbol: Optional[str] = None,
    config: Optional[PeakValleyExitConfig] = None,
) -> PeakValleyExitConfig:
    """依交易對解析適配的峰谷平倉與條件式反手參數，顯式傳入 config 時優先保留呼叫端設定。"""
    if config is not None:
        return config
    if symbol and is_lobster_symbol(symbol):
        return LOBSTER_PEAK_VALLEY_EXIT_CONFIG
    if symbol and is_pepe_symbol(symbol):
        return PEPE_PEAK_VALLEY_EXIT_CONFIG
    return DEFAULT_PEAK_VALLEY_EXIT_CONFIG


def is_peak_valley_exit_authorized_reason(reason: Any) -> bool:
    """判斷 close_reason 是否屬於本峰谷低延遲 / KC 三層平倉授權體系。"""
    upper_reason = str(reason or "").upper()
    return (ACTIVE_EXIT_PV_PREFIX in upper_reason) or (ACTIVE_EXIT_KC_PREFIX in upper_reason)


def _safe_float(value: Any, default: float = float("nan")) -> float:
    try:
        num = float(value)
        return num if math.isfinite(num) else default
    except (TypeError, ValueError, OverflowError):
        return default


def extract_bar_id(df: Optional[pd.DataFrame]) -> Optional[int]:
    """從 K 線最新一根提取 1M K 棒整數時間戳（毫秒標準化），供單根 K 線防重複鎖比對。"""
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    raw_ts = df.iloc[-1].get("timestamp", df.iloc[-1].get("time"))
    ts_val = _safe_float(raw_ts)
    if not math.isfinite(ts_val) or ts_val <= 0:
        return None
    if ts_val < 1e11:
        ts_val *= 1000.0
    return int(round(ts_val))


def _latest_closed_bars(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame()
    if "is_closed" not in df.columns:
        return df
    last_closed = df.iloc[-1].get("is_closed")
    if last_closed is False or (type(last_closed).__name__ == "bool_" and not bool(last_closed)):
        return df.iloc[:-1] if len(df) > 1 else df
    return df


def extract_entry_structure_context(
    df: pd.DataFrame,
    side: str,
    config: PeakValleyExitConfig = DEFAULT_PEAK_VALLEY_EXIT_CONFIG,
) -> Dict[str, Any]:
    """擷取進場當下的波谷/波峰極值與 KC 通道寬度，供進場後假轉折破位止損與縮口判定使用。"""
    closed = _latest_closed_bars(df)
    if closed.empty:
        return {}
    lookback = max(2, int(config.swing_lookback_bars))
    window = closed.tail(lookback)
    lows = pd.to_numeric(window["low"], errors="coerce").dropna() if "low" in window.columns else pd.Series(dtype=float)
    highs = pd.to_numeric(window["high"], errors="coerce").dropna() if "high" in window.columns else pd.Series(dtype=float)

    kc_width_pct = float("nan")
    last_row = closed.iloc[-1]
    kc_u = _safe_float(last_row.get("kc_upper"))
    kc_l = _safe_float(last_row.get("kc_lower"))
    kc_m = _safe_float(last_row.get("kc_middle"))
    if math.isfinite(kc_u) and math.isfinite(kc_l) and math.isfinite(kc_m) and kc_m > 0:
        kc_width_pct = (kc_u - kc_l) / kc_m

    return {
        "entry_swing_low": float(lows.min()) if not lows.empty else None,
        "entry_swing_high": float(highs.max()) if not highs.empty else None,
        "entry_kc_width_pct": kc_width_pct if math.isfinite(kc_width_pct) and kc_width_pct > 0 else None,
        "entry_side": str(side or "").upper(),
    }


def can_reenter_after_pv_exit(
    last_exit_record: Optional[Mapping[str, Any]],
    *,
    current_bar_id: Optional[int],
    candidate_side: str,
    now_ts: Optional[float] = None,
    is_conditional_flip: bool = False,
    config: PeakValleyExitConfig = DEFAULT_PEAK_VALLEY_EXIT_CONFIG,
) -> Tuple[bool, str]:
    """平倉後到下一次獨立進場的防掃損鎖與冷卻時間檢查。

    - 若非條件式即時反手（例如盤整/鎖利/假轉折止損平倉後），平倉後須冷卻並鎖死同根 K 棒重進。
    - 僅合條件的即時反手（is_conditional_flip=True）可免除平倉後等待，且仍受單根 K 線最多反手一次限制。
    """
    if not last_exit_record:
        return True, "NO_PRIOR_PV_EXIT"

    now = float(now_ts if now_ts is not None else time.time())
    exit_ts = _safe_float(last_exit_record.get("exit_timestamp"), default=0.0)
    raw_exit_bar_id = last_exit_record.get("exit_bar_id")
    closed_side = str(last_exit_record.get("closed_side") or "").upper()
    norm_side = str(candidate_side or "").upper()
    exit_reason = str(last_exit_record.get("reason") or "")

    # 規範化新舊 K 棒時間戳為毫秒再比對
    def _norm_bar(bid: Any) -> Optional[int]:
        v = _safe_float(bid)
        if not math.isfinite(v) or v <= 0:
            return None
        return int(round(v * 1000.0 if v < 1e11 else v))

    cur_bid = _norm_bar(current_bar_id)
    ext_bid = _norm_bar(raw_exit_bar_id)
    same_bar = bool(cur_bid is not None and ext_bid is not None and cur_bid == ext_bid)

    if is_conditional_flip:
        # 嚴禁在盤整/保底鎖利/止損平倉後直接反手
        if exit_reason not in PV_FLIP_ELIGIBLE_EXIT_REASONS:
            return False, "BLOCKED_PV_FLIP_FORBIDDEN_BY_EXIT_REASON"
        is_reverse_flip = bool(closed_side and norm_side and closed_side != norm_side)
        if not (
            config.enable_conditional_flip
            and config.same_bar_reverse_allowed
            and config.flip_on_reverse_inflection
            and is_reverse_flip
        ):
            return False, "BLOCKED_PV_FLIP_DISABLED"
        return True, "PV_CONDITIONAL_FLIP_ALLOWED"

    # 一般獨立進場：必須滿足冷卻秒數（預設 180 秒 / 3 分鐘），且不允許在同一根 K 棒平倉後反覆進場
    elapsed = now - exit_ts if exit_ts > 0 else float("inf")
    if elapsed < max(0.0, float(config.reentry_cooldown_seconds)):
        return False, "BLOCKED_PV_EXIT_COOLDOWN"

    if same_bar:
        if exit_reason in PV_FLIP_FORBIDDEN_EXIT_REASONS or not is_conditional_flip:
            return False, "BLOCKED_PV_SAME_BAR_ANTI_WHIPSAW"

    if cur_bid is not None and ext_bid is not None and hasattr(config, "reentry_cooldown_bars") and config.reentry_cooldown_bars > 0:
        bars_diff = abs(cur_bid - ext_bid) // 60_000
        req_bars = int(config.reentry_cooldown_bars)
        if bars_diff < req_bars:
            return False, "BLOCKED_PV_EXIT_COOLDOWN_BARS"

    return True, "PV_REENTRY_ALLOWED"


def _compute_live_ma5_triplet(
    working: pd.DataFrame,
    closed: pd.DataFrame,
    live_price: float,
) -> Tuple[float, float, float]:
    """將最新成交價即時帶入未收線 K 棒，計算 (curr_ma5, prev_ma5, prev2_ma5)。"""
    if len(working) >= 7 and "close" in working.columns:
        closes = pd.to_numeric(working["close"], errors="coerce").astype(float)
        closes.iloc[-1] = float(live_price)
        ma5_s = closes.rolling(5, min_periods=5).mean()
        c_v, p_v, p2_v = _safe_float(ma5_s.iloc[-1]), _safe_float(ma5_s.iloc[-2]), _safe_float(ma5_s.iloc[-3])
        if all(math.isfinite(v) and v > 0 for v in (c_v, p_v, p2_v)):
            return c_v, p_v, p2_v

    # 短測試框架相容：若無足夠 K 線，回退讀取預計算欄位
    if len(working) >= 3 and "ma5" in working.columns:
        c_v = _safe_float(working.iloc[-1].get("ma5"))
        p_v = _safe_float(working.iloc[-2].get("ma5"))
        p2_v = _safe_float(working.iloc[-3].get("ma5"))
        if all(math.isfinite(v) and v > 0 for v in (c_v, p_v, p2_v)):
            return c_v, p_v, p2_v
    if len(closed) >= 3 and "ma5" in closed.columns:
        return (
            _safe_float(closed.iloc[-1].get("ma5")),
            _safe_float(closed.iloc[-2].get("ma5")),
            _safe_float(closed.iloc[-3].get("ma5")),
        )
    return float("nan"), float("nan"), float("nan")


def _compute_kc_mid_trend_intact(
    closed: pd.DataFrame,
    side: str,
    min_reversal_slope_pct: float = 0.00015,
) -> Tuple[bool, float, float, str, str]:
    """計算 KC 中軌連續斜率變化，嚴格判定主趨勢是否仍在推進中。

    【核心規則：中軌沒有明顯反向，絕不平倉】
    - 空單（SHORT）：只有當 KC_middle 連續 2 根 1M K 線明確向上抬頭
      (kc_mid_slope_pct >= min_reversal_slope_pct 且當前 KC_middle > 前一根 KC_middle)，
      才視為「中軌明顯向上」；只要中軌還在向下或僅是水平走平 (slope <= 0 或向上斜率不足)，
      一律判定 trend_intact = True，阻斷原因 HOLD_KC_MID_NOT_CLEARLY_UP。
    - 多單（LONG）：只有當 KC_middle 連續 2 根 1M K 線明確向下彎頭
      (kc_mid_slope_pct <= -min_reversal_slope_pct 且當前 KC_middle < 前一根 KC_middle)，
      才視為「中軌明顯向下」；只要中軌還在向上或僅是水平走平，
      一律判定 trend_intact = True，阻斷原因 HOLD_KC_MID_NOT_CLEARLY_DOWN。

    回傳：(trend_intact, s0, s1, closed_kc_dir, hold_reason)
    """
    flat_eps = 1e-12
    if closed is None or not isinstance(closed, pd.DataFrame) or len(closed) < 2:
        return True, 0.0, 0.0, "FLAT", "HOLD_KC_MID_TREND_INTACT"

    kcm_series = pd.to_numeric(closed["kc_middle"], errors="coerce").dropna() if "kc_middle" in closed.columns else pd.Series(dtype=float)
    if len(kcm_series) >= 2:
        prev_kcm = float(kcm_series.iloc[-2])
        curr_kcm = float(kcm_series.iloc[-1])
        s0 = curr_kcm - prev_kcm
        s0_pct = (s0 / prev_kcm) if (math.isfinite(prev_kcm) and prev_kcm > 0) else 0.0
    else:
        s0 = 0.0
        s0_pct = 0.0
    if len(kcm_series) >= 3:
        prev2_kcm = float(kcm_series.iloc[-3])
        s1 = prev_kcm - prev2_kcm
        s1_pct = (s1 / prev2_kcm) if (math.isfinite(prev2_kcm) and prev2_kcm > 0) else 0.0
    else:
        s1 = s0
        s1_pct = s0_pct

    closed_kc_dir = "LONG" if s0 > flat_eps else ("SHORT" if s0 < -flat_eps else "FLAT")

    threshold_pct = max(0.0, float(min_reversal_slope_pct))

    if side == "LONG":
        # 持有多單時：只有當 KC_middle 連續 2 根 1M K 線明確向下彎頭（kc_mid_slope_pct <= -min_reversal_slope_pct 且 s0 < 0）
        # 才視為「中軌明顯向下」；只要中軌還在向上或僅是水平走平，一律鎖死不准平多
        clearly_down = bool(s0 < -flat_eps and s0_pct <= -threshold_pct and s1 < -flat_eps and s1_pct <= -threshold_pct)
        trend_intact = not clearly_down
        hold_reason = "HOLD_KC_MID_NOT_CLEARLY_DOWN" if trend_intact else "KC_MID_CLEARLY_DOWN"
        return trend_intact, s0, s1, closed_kc_dir, hold_reason
    else:
        # 持有空單時：只有當 KC_middle 連續 2 根 1M K 線明確向上抬頭（kc_mid_slope_pct >= min_reversal_slope_pct 且 s0 > 0）
        # 才視為「中軌明顯向上」；只要中軌還在向下或僅是水平走平，一律鎖死不准平空
        clearly_up = bool(s0 > flat_eps and s0_pct >= threshold_pct and s1 > flat_eps and s1_pct >= threshold_pct)
        trend_intact = not clearly_up
        hold_reason = "HOLD_KC_MID_NOT_CLEARLY_UP" if trend_intact else "KC_MID_CLEARLY_UP"
        return trend_intact, s0, s1, closed_kc_dir, hold_reason


# ── 全域跨幣種行情 K 線快照緩存（Thread-safe symbol frame cache） ───────────────
_GLOBAL_CHANNEL_FRAMES: Dict[str, pd.DataFrame] = {}


def register_channel_frame(symbol: str, frame: pd.DataFrame) -> None:
    """註冊/更新單一交易對最新 1M K 線快照，供跨幣種大趨勢共振鎖倉共享使用。"""
    if symbol and isinstance(frame, pd.DataFrame) and not frame.empty:
        _GLOBAL_CHANNEL_FRAMES[str(symbol)] = frame


def get_registered_channel_frames() -> Dict[str, pd.DataFrame]:
    """取得當前所有已註冊之各幣種最新 K 線快照字典副本。"""
    return dict(_GLOBAL_CHANNEL_FRAMES)


def clear_registered_channel_frames() -> None:
    """清空快照緩存（主要供單元測試隔離使用）。"""
    _GLOBAL_CHANNEL_FRAMES.clear()


def evaluate_cross_symbol_sync_state(
    current_symbol: Optional[str],
    side: str,
    closed_current: pd.DataFrame,
    cross_symbol_frames: Optional[Mapping[str, pd.DataFrame]] = None,
    config: Optional[PeakValleyExitConfig] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """評估跨幣種大趨勢共振鎖（Cross-Symbol Trend Sync Lock）。

    運作邏輯（多空雙向對稱）：
    1. 針對監控幣種組合（預設 1000PEPE/USDT 與 龙虾/USDT）：
       - 持有空單時（side == 'SHORT'）：
         * 檢查所有監控幣種是否處於一致空頭大趨勢（closed_kc_dir == 'SHORT' 且 kc_mid_slope < 0）。
         * 若兩幣種皆為空頭推進，啟動「雙幣空頭共振鎖（Bear Sync Lock）」。
         * 解鎖條件：任一監控幣種的中軌連續 2 根 1M K 線明確向上抬頭（kc_mid_slope_pct >= min_reversal_slope_pct），
           即代表系統性共振下跌出現分化反轉，解除共振鎖定。
       - 持有多單時（side == 'LONG'）：
         * 檢查所有監控幣種是否處於一致多頭大趨勢（closed_kc_dir == 'LONG' 且 kc_mid_slope > 0）。
         * 若兩幣種皆為多頭推進，啟動「雙幣多頭共振鎖（Bull Sync Lock）」。
         * 解鎖條件：任一監控幣種的中軌連續 2 根 1M K 線明確向下彎頭（kc_mid_slope_pct <= -min_reversal_slope_pct），
           即代表系統性共振上漲出現分化反轉，解除共振鎖定。
    2. 回傳：(is_locked, lock_reason, sync_details)
    """
    cfg = config or PeakValleyExitConfig()
    if not getattr(cfg, "enable_cross_symbol_sync_lock", True):
        return False, "SYNC_LOCK_DISABLED", {}

    target_sync_symbols = list(getattr(cfg, "monitored_sync_symbols", ("1000PEPE/USDT", "龙虾/USDT")))
    reversal_slope_pct = float(getattr(cfg, "min_kc_mid_reversal_slope_pct", 0.00015))

    # 彙整所有可用幣種之 K 棒資料
    all_frames: Dict[str, pd.DataFrame] = dict(_GLOBAL_CHANNEL_FRAMES)
    if cross_symbol_frames and isinstance(cross_symbol_frames, Mapping):
        all_frames.update({str(k): v for k, v in cross_symbol_frames.items() if isinstance(v, pd.DataFrame)})
    if current_symbol and isinstance(closed_current, pd.DataFrame) and not closed_current.empty:
        all_frames[str(current_symbol)] = closed_current

    # 檢查各監控幣種狀態
    symbol_states: Dict[str, Dict[str, Any]] = {}
    valid_count = 0
    all_aligned_trend = True
    any_symbol_reversed = False

    for sym in target_sync_symbols:
        f = all_frames.get(sym)
        if f is None or not isinstance(f, pd.DataFrame) or len(f) < 2:
            # 容許短暫缺單一幣種，若目前評估的幣種就是 sym 且有 closed_current 則優先使用
            continue
        c_bars = _latest_closed_bars(f)
        if c_bars.empty:
            c_bars = f
        trend_intact, s0, s1, c_dir, h_reason = _compute_kc_mid_trend_intact(
            c_bars, side, min_reversal_slope_pct=reversal_slope_pct
        )
        is_reversed = not trend_intact  # 若 trend_intact 為 False，表示已連續 2 根明確反向抬頭/彎頭
        symbol_states[sym] = {
            "s0": s0,
            "s1": s1,
            "closed_kc_dir": c_dir,
            "trend_intact": trend_intact,
            "is_reversed": is_reversed,
            "hold_reason": h_reason,
        }
        valid_count += 1

        if side == "SHORT":
            # 空單共振需要：KC 方向為 SHORT 且中軌斜率向下 (s0 < 0)
            if not (c_dir == "SHORT" and s0 < 0):
                all_aligned_trend = False
            if is_reversed:
                any_symbol_reversed = True
        else:
            # 多單共振需要：KC 方向為 LONG 且中軌斜率向上 (s0 > 0)
            if not (c_dir == "LONG" and s0 > 0):
                all_aligned_trend = False
            if is_reversed:
                any_symbol_reversed = True

    # 至少要有 2 個監控幣種具備有效數據，才構成跨幣種共振鎖定
    if valid_count < 2:
        return False, "SYNC_LOCK_INSUFFICIENT_SYMBOLS", {"valid_count": valid_count, "symbols": symbol_states}

    # 若任一幣種已經明確反轉，共振解除
    if any_symbol_reversed:
        return False, "SYNC_LOCK_RELEASED_REVERSED", {"valid_count": valid_count, "symbols": symbol_states}

    # 若所有幣種一致處於同向推進且皆未反轉，共振鎖定成立！
    if all_aligned_trend:
        lock_code = "HOLD_CROSS_SYMBOL_BEAR_SYNC_LOCK" if side == "SHORT" else "HOLD_CROSS_SYMBOL_BULL_SYNC_LOCK"
        return True, lock_code, {"valid_count": valid_count, "symbols": symbol_states}

    return False, "SYNC_LOCK_NOT_ALIGNED", {"valid_count": valid_count, "symbols": symbol_states}


def _compute_live_ma15_and_adx(
    working: pd.DataFrame,
    closed: pd.DataFrame,
    live_price: float,
) -> Tuple[float, Optional[float]]:
    """取得即時 MA15 與 ADX，供條件式反手嚴格非盤整濾網驗證。"""
    curr_ma15 = float("nan")
    if len(working) >= 15 and "close" in working.columns:
        closes = pd.to_numeric(working["close"], errors="coerce").astype(float)
        closes.iloc[-1] = float(live_price)
        curr_ma15 = _safe_float(closes.rolling(15, min_periods=15).mean().iloc[-1])
    if not math.isfinite(curr_ma15) and "ma15" in working.columns:
        curr_ma15 = _safe_float(working.iloc[-1].get("ma15"))
    if not math.isfinite(curr_ma15) and not closed.empty and "ma15" in closed.columns:
        curr_ma15 = _safe_float(closed.iloc[-1].get("ma15"))

    adx_val: Optional[float] = None
    if "adx" in working.columns and pd.notna(working["adx"].iloc[-1]):
        cand = _safe_float(working["adx"].iloc[-1])
        if math.isfinite(cand):
            adx_val = cand
    if adx_val is None and len(working) >= 15 and {"high", "low", "close"}.issubset(working.columns):
        high_s = pd.to_numeric(working["high"], errors="coerce").astype(float)
        low_s = pd.to_numeric(working["low"], errors="coerce").astype(float)
        close_s = pd.to_numeric(working["close"], errors="coerce").astype(float)
        adx_s = _compute_adx_series(high_s, low_s, close_s, period=14)
        cand = _safe_float(adx_s.iloc[-1])
        if math.isfinite(cand):
            adx_val = cand
    return curr_ma15, adx_val


def _resolve_kc_bands(working: pd.DataFrame, closed: pd.DataFrame) -> Tuple[float, float, float]:
    """優先讀取已計算之 KC 上/中/下軌；缺少欄位時即時計算。"""
    ref = closed.iloc[-1] if not closed.empty else working.iloc[-1]
    kc_u = _safe_float(ref.get("kc_upper"))
    kc_m = _safe_float(ref.get("kc_middle"))
    kc_l = _safe_float(ref.get("kc_lower"))
    if all(math.isfinite(v) and v > 0 for v in (kc_u, kc_m, kc_l)) and kc_l < kc_m < kc_u:
        return kc_u, kc_m, kc_l

    if len(closed) >= 20:
        kc = compute_keltner_channels(closed, ema_period=20, atr_period=20, multiplier=2.0)
        kc_u = _safe_float(kc["kc_upper"].iloc[-1])
        kc_m = _safe_float(kc["kc_middle"].iloc[-1])
        kc_l = _safe_float(kc["kc_lower"].iloc[-1])
        if all(math.isfinite(v) and v > 0 for v in (kc_u, kc_m, kc_l)) and kc_l < kc_m < kc_u:
            return kc_u, kc_m, kc_l
    return float("nan"), float("nan"), float("nan")


def build_live_flip_frame(df: pd.DataFrame, live_price: float) -> pd.DataFrame:
    """建立含最新即時價格並重算 MA5/MA15 之行情 DataFrame，供反手下單邊界驗證同步使用。"""
    live_df = _build_live_close_frame(df, float(live_price))
    if isinstance(live_df, pd.DataFrame) and len(live_df) >= 1 and "close" in live_df.columns:
        closes = pd.to_numeric(live_df["close"], errors="coerce").astype(float)
        ma5_live = closes.rolling(5, min_periods=1).mean()
        ma15_live = closes.rolling(15, min_periods=1).mean()
        if "ma5" in live_df.columns:
            existing_ma5 = pd.to_numeric(live_df["ma5"], errors="coerce").astype(float)
            # 僅覆寫最新動態棒與有效滾動視窗，保留既有歷史暖身值避免前段產生 NaN
            live_df["ma5"] = closes.rolling(5, min_periods=5).mean().combine_first(existing_ma5).fillna(ma5_live)
        else:
            live_df["ma5"] = ma5_live
        if "ma15" in live_df.columns:
            existing_ma15 = pd.to_numeric(live_df["ma15"], errors="coerce").astype(float)
            live_df["ma15"] = closes.rolling(15, min_periods=15).mean().combine_first(existing_ma15).fillna(ma15_live)
        else:
            live_df["ma15"] = ma15_live
    return live_df


def evaluate_flip_eligibility(
    *,
    closed_side: str,
    exit_reason: str,
    current_bar_id: Optional[int],
    last_flip_bar_id: Optional[int],
    kc_upper: float,
    kc_middle: float,
    kc_lower: float,
    curr_ma5: float,
    curr_ma15: float,
    curr_adx: Optional[float],
    config: PeakValleyExitConfig = DEFAULT_PEAK_VALLEY_EXIT_CONFIG,
) -> Tuple[bool, Optional[str], str, Dict[str, Any]]:
    """條件式即時反手（Flip）雙重審查：平倉原因資格 + 較嚴格之非盤整波動空間濾網 + 單棒防重複鎖。"""
    norm_side = str(closed_side or "").upper()
    target_flip_side = "SHORT" if norm_side == "LONG" else ("LONG" if norm_side == "SHORT" else None)

    if not config.enable_conditional_flip or target_flip_side is None:
        return False, None, "FLIP_DISABLED", {}

    # 條件 A：僅限「明確峰谷反轉（MA5 拐點）」或「KC 極值反轉止盈」允許反手；
    # 若為盤整止損、保底鎖利（Trailing Stop）或假轉折破位/硬止損，一律嚴禁反手，平倉後空倉觀望！
    if exit_reason not in PV_FLIP_ELIGIBLE_EXIT_REASONS:
        return False, None, f"FLIP_FORBIDDEN_FOR_EXIT_REASON:{exit_reason}", {
            "flip_forbidden_reason": exit_reason,
        }

    # 防重複刷單鎖：同一根 1M K 線內若已執行過「平倉 -> 反手」，嚴禁再次反手
    if not config.same_bar_reverse_allowed:
        return False, None, "FLIP_BLOCKED_SAME_BAR_DISABLED", {}
    if current_bar_id is not None and last_flip_bar_id is not None and int(current_bar_id) == int(last_flip_bar_id):
        return False, None, "FLIP_BLOCKED_SAME_BAR_DUPLICATE_LOCK", {
            "current_bar_id": current_bar_id,
            "last_flip_bar_id": last_flip_bar_id,
        }

    # 條件 B：使用高於一般開倉的安全門檻（覆蓋平倉＋反手開倉雙倍吃單手續費與滑價）檢查非盤整市場空間
    strict_flip_kc_floor = max(float(config.flip_min_kc_width_pct), float(config.normal_min_kc_width_pct))
    flip_filter_cfg = PeakValleyEntryConfig(
        min_kc_width_pct=strict_flip_kc_floor,
        min_ma_spread_pct=float(config.flip_min_ma_spread_pct),
        enable_adx_filter=bool(config.flip_enable_adx_filter),
        min_adx=float(config.flip_min_adx),
    )
    is_cons, cons_reason, cons_metrics = evaluate_peak_valley_consolidation(
        kc_upper=kc_upper,
        kc_middle=kc_middle,
        kc_lower=kc_lower,
        ma5=curr_ma5,
        ma15=curr_ma15,
        adx=curr_adx,
        config=flip_filter_cfg,
    )
    cons_metrics["normal_min_kc_width_pct"] = float(config.normal_min_kc_width_pct)
    cons_metrics["flip_min_kc_width_pct"] = strict_flip_kc_floor
    if is_cons:
        return False, None, f"FLIP_BLOCKED_CONSOLIDATION:{cons_reason}", cons_metrics

    return True, target_flip_side, f"FLIP_{target_flip_side}_FROM_{exit_reason}", cons_metrics


def has_confirmed_three_point_pivot(
    closed_df: Optional[pd.DataFrame],
    side: str,
) -> Tuple[bool, Dict[str, Any]]:
    """檢查最近三根已收線 K 棒是否已形成確認的峰頂(LONG)/谷底(SHORT)三點樞紐。"""
    norm_side = str(side or "").upper()
    if closed_df is None or not isinstance(closed_df, pd.DataFrame) or len(closed_df) < 3:
        return False, {}
    left = closed_df.iloc[-3]
    pivot = closed_df.iloc[-2]
    confirm = closed_df.iloc[-1]
    l_h, l_l = _safe_float(left.get("high")), _safe_float(left.get("low"))
    p_o, p_h, p_l, p_c = (
        _safe_float(pivot.get("open")),
        _safe_float(pivot.get("high")),
        _safe_float(pivot.get("low")),
        _safe_float(pivot.get("close")),
    )
    c_o, c_h, c_l, c_c = (
        _safe_float(confirm.get("open")),
        _safe_float(confirm.get("high")),
        _safe_float(confirm.get("low")),
        _safe_float(confirm.get("close")),
    )
    if any(not math.isfinite(v) or v <= 0 for v in (l_h, l_l, p_o, p_h, p_l, p_c, c_o, c_h, c_l, c_c)):
        return False, {}
    if p_h < max(p_o, p_c) or p_l > min(p_o, p_c) or c_h < max(c_o, c_c) or c_l > min(c_o, c_c):
        return False, {}

    pivot_mid = (p_o + p_c) / 2.0
    c_ma5 = _safe_float(confirm.get("ma5"))
    if not math.isfinite(c_ma5) and "close" in closed_df.columns and len(closed_df) >= 5:
        ma5_val = pd.to_numeric(closed_df["close"], errors="coerce").rolling(5, min_periods=5).mean().iloc[-1]
        c_ma5 = _safe_float(ma5_val)

    info: Dict[str, Any] = {
        "pivot_high": p_h,
        "pivot_low": p_l,
        "pivot_midpoint": pivot_mid,
        "confirm_close": c_c,
        "confirm_ma5": c_ma5 if math.isfinite(c_ma5) else None,
    }
    if norm_side == "LONG":
        valid = (
            p_h > l_h
            and p_h > c_h
            and c_c < c_o
            and c_c < pivot_mid
            and (not math.isfinite(c_ma5) or c_c < c_ma5)
        )
        return bool(valid), info
    if norm_side == "SHORT":
        valid = (
            p_l < l_l
            and p_l < c_l
            and c_c > c_o
            and c_c > pivot_mid
            and (not math.isfinite(c_ma5) or c_c > c_ma5)
        )
        return bool(valid), info
    return False, {}


def _is_trend_intact_inside_channel(
    *,
    side: str,
    price: float,
    closed: pd.DataFrame,
    working: pd.DataFrame,
    kc_u: float,
    kc_m: float,
    kc_l: float,
    curr_ma5: float,
    curr_ma15: float,
) -> Tuple[bool, bool, Optional[float]]:
    """判斷是否仍順原趨勢且價格仍在通道中軌內（未突破反側外軌且趨勢未翻轉）。"""
    curr_kcm = kc_m
    prev_kcm = float("nan")
    for src in (working, closed):
        if isinstance(src, pd.DataFrame) and "kc_middle" in src.columns:
            s_kcm = pd.to_numeric(src["kc_middle"], errors="coerce").dropna()
            if len(s_kcm) >= 2:
                curr_kcm = float(s_kcm.iloc[-1])
                prev_kcm = float(s_kcm.iloc[-2])
                break
    kcm_slope = (curr_kcm - prev_kcm) if (math.isfinite(curr_kcm) and math.isfinite(prev_kcm)) else float("nan")

    if side == "LONG":
        trend_intact = bool(
            math.isfinite(kcm_slope)
            and kcm_slope >= 0
            and math.isfinite(curr_ma5)
            and math.isfinite(curr_ma15)
            and curr_ma5 >= curr_ma15
        )
        inside_channel = bool(math.isfinite(kc_l) and price > kc_l)
        return trend_intact, inside_channel, (kcm_slope if math.isfinite(kcm_slope) else None)

    trend_intact = bool(
        math.isfinite(kcm_slope)
        and kcm_slope <= 0
        and math.isfinite(curr_ma5)
        and math.isfinite(curr_ma15)
        and curr_ma5 <= curr_ma15
    )
    inside_channel = bool(math.isfinite(kc_u) and price < kc_u)
    return trend_intact, inside_channel, (kcm_slope if math.isfinite(kcm_slope) else None)


def _resolve_entry_and_closed_atr(
    position: Mapping[str, Any],
    meta: Mapping[str, Any],
    closed: pd.DataFrame,
    entry_price: float,
    kc_u: float = float("nan"),
    kc_l: float = float("nan"),
) -> Tuple[float, float]:
    """解析並鎖定「進場當下 ATR (entry_atr)」與「最新已收盤 ATR (closed_atr)」。"""
    closed_atr = float("nan")
    if isinstance(closed, pd.DataFrame) and not closed.empty:
        if "atr" in closed.columns:
            atr_series = pd.to_numeric(closed["atr"], errors="coerce").dropna()
            atr_series = atr_series[atr_series > 0]
            if not atr_series.empty:
                closed_atr = _safe_float(atr_series.iloc[-1])
        if (not math.isfinite(closed_atr) or closed_atr <= 0) and len(closed) >= 2 and {"high", "low", "close"}.issubset(closed.columns):
            h_s = pd.to_numeric(closed["high"], errors="coerce").astype(float)
            l_s = pd.to_numeric(closed["low"], errors="coerce").astype(float)
            c_s = pd.to_numeric(closed["close"], errors="coerce").astype(float)
            prev_c = c_s.shift(1)
            tr = pd.concat([(h_s - l_s), (h_s - prev_c).abs(), (l_s - prev_c).abs()], axis=1).max(axis=1)
            atr_val = _safe_float(tr.rolling(min(20, len(tr)), min_periods=1).mean().iloc[-1])
            if math.isfinite(atr_val) and atr_val > 0:
                closed_atr = atr_val
    if (not math.isfinite(closed_atr) or closed_atr <= 0) and math.isfinite(kc_u) and math.isfinite(kc_l) and kc_u > kc_l:
        # KC 上下軌預設為 中軌 ± 2.0 * ATR，故 (kc_upper - kc_lower) / 4.0 即約等於 1.0 * ATR
        closed_atr = (kc_u - kc_l) / 4.0
    if not math.isfinite(closed_atr) or closed_atr <= 0:
        closed_atr = entry_price * 0.005

    entry_atr = float("nan")
    for candidate in (
        (position or {}).get("entry_atr"),
        (meta or {}).get("entry_atr"),
        (position or {}).get("atr"),
        (meta or {}).get("atr"),
    ):
        val = _safe_float(candidate)
        if math.isfinite(val) and val > 0:
            entry_atr = val
            break
    if not math.isfinite(entry_atr) or entry_atr <= 0:
        entry_atr = closed_atr
    return entry_atr, closed_atr


def _resolve_closed_kc_state(closed: pd.DataFrame) -> Tuple[float, float, float, float, str]:
    """嚴格依據「已收盤 K 棒」計算 KC 上/中/下軌、前一根 KC 中軌與 KC 通道趨勢方向。

    回傳：(closed_kc_u, closed_kc_m, closed_kc_l, prev_closed_kc_m, closed_kc_dir)
    closed_kc_dir ∈ {"LONG", "SHORT", "FLAT"}
    """
    if closed is None or not isinstance(closed, pd.DataFrame) or closed.empty:
        return float("nan"), float("nan"), float("nan"), float("nan"), "FLAT"

    kc_u, kc_m, kc_l = _resolve_kc_bands(closed, closed)
    prev_kc_m = float("nan")
    if "kc_middle" in closed.columns and len(closed) >= 2:
        kcm_s = pd.to_numeric(closed["kc_middle"], errors="coerce").dropna()
        if len(kcm_s) >= 2:
            kc_m = _safe_float(kcm_s.iloc[-1], default=kc_m)
            prev_kc_m = _safe_float(kcm_s.iloc[-2])

    if (not math.isfinite(prev_kc_m) or prev_kc_m <= 0) and len(closed) >= 20 and {"high", "low", "close"}.issubset(closed.columns):
        kc = compute_keltner_channels(closed, ema_period=20, atr_period=20, multiplier=2.0)
        kc_u = _safe_float(kc["kc_upper"].iloc[-1], default=kc_u)
        kc_m = _safe_float(kc["kc_middle"].iloc[-1], default=kc_m)
        kc_l = _safe_float(kc["kc_lower"].iloc[-1], default=kc_l)
        prev_kc_m = _safe_float(kc["kc_middle"].iloc[-2])

    # 沿用原本的 KC 趨勢方向判斷（ck_direction 比較最近兩根已收盤 KC 中軌斜率）
    raw_dir = ck_direction(closed, has_forming_bar=False)
    if math.isfinite(kc_m) and math.isfinite(prev_kc_m) and prev_kc_m > 0:
        kc_delta = kc_m - prev_kc_m
        # 極微幅走平視為盤整（FLAT），避免把持平橫盤誤當明確趨勢
        flat_eps = max(1e-12, abs(prev_kc_m) * 1e-6)
        if abs(kc_delta) <= flat_eps:
            closed_kc_dir = "FLAT"
        elif raw_dir in ("LONG", "SHORT"):
            closed_kc_dir = raw_dir
        else:
            closed_kc_dir = "LONG" if kc_delta > flat_eps else "SHORT"
    elif raw_dir in ("LONG", "SHORT"):
        closed_kc_dir = raw_dir
    else:
        closed_kc_dir = "FLAT"

    return kc_u, kc_m, kc_l, prev_kc_m, closed_kc_dir


def evaluate_kc_layered_exit(
    df: pd.DataFrame,
    *,
    position: Mapping[str, Any],
    live_price: float,
    metadata: Optional[Mapping[str, Any]] = None,
    last_flip_bar_id: Optional[int] = None,
    symbol: Optional[str] = None,
    cross_symbol_frames: Optional[Mapping[str, pd.DataFrame]] = None,
    config: Optional[PeakValleyExitConfig] = None,
) -> Dict[str, Any]:
    """KC 肯特納通道策略 — 四層分層防洗盤出場判斷核心函式。

    出場優先順序（由高到低）：
    1. 第一層：固定硬止損（Hard Stop Loss - 盤中即時 Tick 觸發）
       - 進場價 ± 2.0 * entry_atr（可選 KC 反向外軌輔助），觸及立即市價止損，禁止進入反手觀察期。
    2. 第二層：極值區「峰谷三點（分型）」主動逃頂平倉（BAR_CLOSE 收盤確認，優先於移動止損與破中軌）
       - 多單：k[-2].high 嚴格高於前後兩棒且 k[-1] 重心下移，且波峰觸及 KC_middle + 0.85 * (KC_upper - KC_middle)，
         同時收盤未實現獲利 >= 0.30% -> 立即以 ACTIVE_EXIT_PV_EXTREME_FRACTAL_LONG 出場並進入反手觀察期。
       - 空單：對稱底分型且波谷觸及 KC_middle - 0.85 * (KC_middle - KC_lower)，獲利 >= 0.30% -> 主動逃底並進觀察期。
    3. 第三層：動態移動止損（Trailing Stop - 盤中即時 Tick 觸發）
       - 啟動門檻為 max(1.5 * entry_atr, 進場價 * 0.1%, 進場價 * trailing_activate_min_pct[0.35%]) 且覆蓋雙邊手續費，
         啟動後以 max(1.0 * ATR, 0.25%) 單向追蹤鎖利，平倉後禁止進入反手觀察期。
    4. 第四層（兜底）：中軌破位與趨勢反轉平倉（BAR_CLOSE 收盤確認）
       - 中軌緩衝破位（KC 中軌 ± 0.5 * ATR）或 KC 明確反轉（LONG<->SHORT）平倉並進入反手觀察期；
       - 若僅因 KC 方向轉為無趨勢盤整（FLAT）平倉，則嚴格禁止啟動反手觀察期。
    """
    resolved_symbol = symbol
    if not resolved_symbol and isinstance(position, Mapping):
        resolved_symbol = position.get("symbol")
    if not resolved_symbol and isinstance(metadata, Mapping):
        resolved_symbol = metadata.get("symbol")
    if not resolved_symbol and isinstance(getattr(df, "attrs", None), dict):
        resolved_symbol = df.attrs.get("symbol")
    config = resolve_peak_valley_exit_config(
        str(resolved_symbol) if resolved_symbol else None,
        config,
    )

    side = str((position or {}).get("side") or "").upper()
    entry_price = _safe_float((position or {}).get("entry_price"))
    price = _safe_float(live_price)
    leverage = _safe_float((position or {}).get("leverage"), default=1.0)
    if leverage <= 0:
        leverage = 1.0

    current_bar_id = extract_bar_id(df)
    pos_flip_bar_id = (position or {}).get("pv_flip_bar_id") or (metadata or {}).get("pv_flip_bar_id")
    effective_flip_bar_id: Optional[int] = None
    for candidate_bid in (pos_flip_bar_id, last_flip_bar_id):
        v = _safe_float(candidate_bid)
        if math.isfinite(v) and v > 0:
            effective_flip_bar_id = int(round(v * 1000.0 if v < 1e11 else v))
            break

    anti_whipsaw = {
        "same_bar_reverse_allowed": bool(config.same_bar_reverse_allowed),
        "reentry_cooldown_seconds": float(config.reentry_cooldown_seconds),
        "flip_on_reverse_inflection": bool(config.flip_on_reverse_inflection),
        "enforce_same_bar_flip_exit_lock": bool(config.enforce_same_bar_flip_exit_lock),
        "current_bar_id": current_bar_id,
        "locked_flip_bar_id": effective_flip_bar_id,
    }

    def _hold_basic(reason_code: str, trigger_lvl: str = "NONE") -> Dict[str, Any]:
        return {
            "action": "HOLD",
            "should_exit": False,
            "reason": reason_code,
            "trigger_level": trigger_lvl,
            "exit_policy": "KC_LAYERED_3TIER_V1",
            "should_flip": False,
            "flip_side": None,
            "flip_reason": "NO_EXIT",
            "flip_metrics": {},
            "enter_flip_watch": False,
            "flip_watch_side": None,
            "flip_watch_window_bars": 0,
            "current_bar_id": current_bar_id,
            "anti_whipsaw": anti_whipsaw,
            "metrics": {},
            "state_updates": {},
        }

    if (
        side not in ("LONG", "SHORT")
        or not math.isfinite(entry_price) or entry_price <= 0
        or not math.isfinite(price) or price <= 0
        or df is None or not isinstance(df, pd.DataFrame) or len(df) < 3
    ):
        return _hold_basic("HOLD_INVALID_EXIT_INPUT")

    # ── 單根 K 線防重複刷單鎖（反手後當根 1M 棒禁止再次平倉或二次反手） ──────────
    if (
        config.enforce_same_bar_flip_exit_lock
        and current_bar_id is not None
        and effective_flip_bar_id is not None
        and int(current_bar_id) == int(effective_flip_bar_id)
    ):
        return _hold_basic("HOLD_SAME_BAR_FLIP_EXIT_LOCKED", trigger_lvl="LOCKED")

    meta = dict(metadata or {})
    # 嚴格擷取「真正已收盤的歷史 K 棒」供第二層趨勢判斷使用，避免盤中未收盤價格/影線污染
    closed = _latest_closed_bars(df)
    if closed.empty:
        closed = df

    # 自動註冊當前幣種已收盤行情至跨幣種共享緩存
    if resolved_symbol:
        register_channel_frame(str(resolved_symbol), closed)

    # 跨幣種大趨勢共振鎖（Cross-Symbol Trend Sync Lock）狀態評估
    is_sync_locked, sync_lock_reason, sync_details = evaluate_cross_symbol_sync_state(
        current_symbol=str(resolved_symbol) if resolved_symbol else None,
        side=side,
        closed_current=closed,
        cross_symbol_frames=cross_symbol_frames,
        config=config,
    )

    closed_kc_u, closed_kc_m, closed_kc_l, prev_closed_kc_m, closed_kc_dir = _resolve_closed_kc_state(closed)
    entry_atr, closed_atr = _resolve_entry_and_closed_atr(
        position, meta, closed, entry_price, kc_u=closed_kc_u, kc_l=closed_kc_l,
    )

    # 跨 Tick 維護並持久化開倉後最有利價格（多單追蹤波段最高價，空單追蹤波段最低價）
    stored_peak = _safe_float(
        position.get("pv_peak_price") or meta.get("pv_peak_price") or position.get("highest_price"),
        default=entry_price,
    )
    stored_trough = _safe_float(
        position.get("pv_trough_price") or meta.get("pv_trough_price") or position.get("lowest_price"),
        default=entry_price,
    )
    peak_price = max(stored_peak, entry_price, price)
    trough_price = min(stored_trough, entry_price, price)

    # ── 【第一層：硬止損（Hard Stop Loss - 盤中即時 Tick 監控觸發）】 ─────────────
    # 進場時依進場當下 ATR 固定計算硬止損防線；若有記錄舊值則優先延續確保不隨盤中波動縮水漂移
    stored_hard_sl = _safe_float(position.get("hard_sl_price") or meta.get("hard_sl_price"))
    if math.isfinite(stored_hard_sl) and stored_hard_sl > 0:
        hard_sl_price = stored_hard_sl
    else:
        sl_dist = max(0.0, float(config.hard_sl_atr_mult) * entry_atr)
        hard_sl_price = (entry_price - sl_dist) if side == "LONG" else (entry_price + sl_dist)

    if config.use_kc_opposite_outer_hard_sl:
        if side == "LONG" and math.isfinite(closed_kc_l) and closed_kc_l > 0:
            hard_sl_price = max(hard_sl_price, closed_kc_l)
        elif side == "SHORT" and math.isfinite(closed_kc_u) and closed_kc_u > 0:
            hard_sl_price = min(hard_sl_price, closed_kc_u)

    # ── 【第三層：動態移動止損（Trailing Stop - 達 max(1.5*ATR, 雙邊手續費, 0.35% 啟動門檻) 後啟動）】 ─
    # 1. 同時要求覆蓋雙邊手續費與最低波段漲跌幅（trailing_activate_min_pct 預設 0.35%），
    #    既避免低 ATR 幣種剛開倉小賺就過早啟動被微幅震盪洗出，也能讓中低波幅幣種走到通道邊緣時順利啟動鎖利
    fee_cover_dist = entry_price * max(0.0, float(config.round_trip_fee_pct))
    min_activate_dist = entry_price * max(0.0, float(config.trailing_activate_min_pct))
    start_threshold_dist = max(
        float(config.trailing_start_atr) * entry_atr,
        fee_cover_dist,
        min_activate_dist,
    )
    peak_favorable_dist = (peak_price - entry_price) if side == "LONG" else (entry_price - trough_price)

    previously_armed = bool(position.get("pv_trailing_armed") or meta.get("pv_trailing_armed"))
    trailing_armed = bool(
        previously_armed
        or (
            start_threshold_dist > 0
            and peak_favorable_dist >= start_threshold_dist
            and peak_favorable_dist > fee_cover_dist
        )
    )

    # 2. 啟動後依 max(1.0 * ATR, trailing_price_retrace_pct) 單向更新移動止損防線（只進不退，絕不後撤）
    track_atr = closed_atr if (math.isfinite(closed_atr) and closed_atr > 0) else entry_atr
    min_retrace_dist = entry_price * max(0.0, float(config.trailing_price_retrace_pct))
    trailing_dist = max(0.0, float(config.trailing_dist_atr) * track_atr, min_retrace_dist)
    if is_sync_locked:
        # 共振鎖定期間放大回撤容忍度，防微幅洗盤回吐洗出場
        trailing_dist *= max(1.0, float(getattr(config, "sync_lock_trailing_boost_ratio", 1.3)))
    prev_trail_stop = _safe_float(
        position.get("trailing_stop_price") or meta.get("trailing_stop_price"),
        default=0.0,
    )
    trailing_stop_price = 0.0
    if trailing_armed:
        if side == "LONG":
            breakeven_floor = entry_price + fee_cover_dist
            candidate_stop = max(peak_price - trailing_dist, breakeven_floor)
            trailing_stop_price = max(prev_trail_stop, candidate_stop) if prev_trail_stop > 0 else candidate_stop
        else:
            breakeven_ceil = entry_price - fee_cover_dist
            candidate_stop = min(trough_price + trailing_dist, breakeven_ceil)
            trailing_stop_price = min(prev_trail_stop, candidate_stop) if prev_trail_stop > 0 else candidate_stop

    # ── 【第二層：趨勢追蹤平倉緩衝防線（KC 中軌 ± 0.5 * ATR）】 ─────────────────
    mid_buffer_stop = float("nan")
    if math.isfinite(closed_kc_m) and closed_kc_m > 0 and math.isfinite(closed_atr) and closed_atr > 0:
        buf_dist = float(config.exit_buffer_atr_mult) * closed_atr
        mid_buffer_stop = (closed_kc_m - buf_dist) if side == "LONG" else (closed_kc_m + buf_dist)

    closed_last_row = closed.iloc[-1]
    closed_close = _safe_float(closed_last_row.get("close"))
    closed_bar_id = extract_bar_id(closed)

    state_updates: Dict[str, Any] = {
        "entry_atr": entry_atr,
        "hard_sl_price": hard_sl_price,
        "pv_peak_price": peak_price,
        "pv_trough_price": trough_price,
        "highest_price": peak_price,
        "lowest_price": trough_price,
        "trough_price": trough_price,
        "pv_trailing_armed": trailing_armed,
        "trailing_stop_price": trailing_stop_price,
        "pv_peak_profit_pct": (peak_favorable_dist / entry_price) if entry_price > 0 else 0.0,
    }

    curr_return_pct = ((price - entry_price) / entry_price) if side == "LONG" else ((entry_price - price) / entry_price)
    metrics: Dict[str, Any] = {
        "side": side,
        "entry_price": entry_price,
        "live_price": price,
        "curr_return_pct": curr_return_pct,
        "entry_atr": entry_atr,
        "closed_atr": closed_atr,
        "hard_sl_price": hard_sl_price,
        "trailing_armed": trailing_armed,
        "trailing_start_dist": start_threshold_dist,
        "fee_cover_dist": fee_cover_dist,
        "peak_favorable_dist": peak_favorable_dist,
        "trailing_stop_price": trailing_stop_price,
        "closed_close": closed_close if math.isfinite(closed_close) else None,
        "kc_upper": closed_kc_u if math.isfinite(closed_kc_u) else None,
        "kc_middle": closed_kc_m if math.isfinite(closed_kc_m) else None,
        "kc_lower": closed_kc_l if math.isfinite(closed_kc_l) else None,
        "prev_kc_middle": prev_closed_kc_m if math.isfinite(prev_closed_kc_m) else None,
        "closed_kc_dir": closed_kc_dir,
        "mid_buffer_stop": mid_buffer_stop if math.isfinite(mid_buffer_stop) else None,
        "is_sync_locked": is_sync_locked,
        "sync_lock_reason": sync_lock_reason,
        "sync_details": sync_details,
        "closed_bar_id": closed_bar_id,
        "current_bar_id": current_bar_id,
    }

    def _build_exit(should_exit: bool, reason: str, trigger_level: str, layer: str) -> Dict[str, Any]:
        action = ("CLOSE_LONG" if side == "LONG" else "CLOSE_SHORT") if should_exit else "HOLD"
        should_flip = False
        flip_side: Optional[str] = None
        flip_reason = "NO_EXIT_TRIGGER"
        flip_metrics: Dict[str, Any] = {}
        enter_flip_watch = False
        flip_watch_side: Optional[str] = None

        if should_exit:
            if config.enable_flip_watch:
                if config.enable_conditional_flip and reason in PV_FLIP_ELIGIBLE_EXIT_REASONS:
                    enter_flip_watch = True
                    flip_watch_side = "SHORT" if side == "LONG" else "LONG"
                    flip_reason = f"ENTER_FLIP_WATCH_{flip_watch_side}_FROM_{reason}"
                else:
                    flip_reason = f"FLIP_WATCH_FORBIDDEN:{reason}"
            else:
                curr_ma5, _prev_ma5, _prev2_ma5 = _compute_live_ma5_triplet(df, closed, price)
                curr_ma15, curr_adx = _compute_live_ma15_and_adx(df, closed, price)
                should_flip, flip_side, flip_reason, flip_metrics = evaluate_flip_eligibility(
                    closed_side=side,
                    exit_reason=reason,
                    current_bar_id=current_bar_id,
                    last_flip_bar_id=effective_flip_bar_id,
                    kc_upper=closed_kc_u,
                    kc_middle=closed_kc_m,
                    kc_lower=closed_kc_l,
                    curr_ma5=curr_ma5,
                    curr_ma15=curr_ma15,
                    curr_adx=curr_adx,
                    config=config,
                )
        metrics["exit_layer"] = layer
        return {
            "action": action,
            "should_exit": should_exit,
            "reason": reason,
            "trigger_level": trigger_level,
            "exit_policy": "KC_LAYERED_3TIER_V1",
            "should_flip": should_flip,
            "flip_side": flip_side,
            "flip_reason": flip_reason,
            "flip_metrics": flip_metrics,
            "enter_flip_watch": enter_flip_watch,
            "flip_watch_side": flip_watch_side,
            "flip_watch_window_bars": int(config.flip_watch_window_bars) if enter_flip_watch else 0,
            "current_bar_id": current_bar_id,
            "anti_whipsaw": anti_whipsaw,
            "metrics": metrics,
            "state_updates": state_updates,
        }

    # ══════════════════════════════════════════════════════════════════════════
    # 第一層判斷：固定硬止損（盤中即時價 Tick 觸發，防範極端單邊暴跌暴漲）
    # ══════════════════════════════════════════════════════════════════════════
    if side == "LONG" and math.isfinite(hard_sl_price) and hard_sl_price > 0 and price <= hard_sl_price:
        return _build_exit(True, "ACTIVE_EXIT_KC_HARD_STOP_LOSS", "TICK", "LAYER1_HARD_STOP_LOSS")
    if side == "SHORT" and math.isfinite(hard_sl_price) and hard_sl_price > 0 and price >= hard_sl_price:
        return _build_exit(True, "ACTIVE_EXIT_KC_HARD_STOP_LOSS", "TICK", "LAYER1_HARD_STOP_LOSS")

    # ── 【大結構破壞止損 (Swing Break SL)】 ────────────────────────────────────
    # 價格突破開倉前明確波段極值 + 0.5*ATR 時觸發結構破壞止損
    # （有顯式提供 entry_swing_high / entry_swing_low 時優先執行）
    swing_high = _safe_float(position.get("entry_swing_high") or meta.get("entry_swing_high"))
    swing_low = _safe_float(position.get("entry_swing_low") or meta.get("entry_swing_low"))
    swing_buf = float(getattr(config, "swing_break_atr_mult", 0.5)) * entry_atr
    if side == "SHORT" and math.isfinite(swing_high) and swing_high > 0 and price > entry_price:
        if price >= (swing_high + swing_buf):
            return _build_exit(True, "ACTIVE_EXIT_SWING_HIGH_STRUCTURE_BREAK", "TICK", "LAYER1_SWING_BREAK")
    elif side == "LONG" and math.isfinite(swing_low) and swing_low > 0 and price < entry_price:
        if price <= (swing_low - swing_buf):
            return _build_exit(True, "ACTIVE_EXIT_SWING_LOW_STRUCTURE_BREAK", "TICK", "LAYER1_SWING_BREAK")

    # 計算已收盤確認時間窗：若開倉當根 1M K 棒尚未收線，不可拿開倉前的歷史已收盤棒觸發 BAR_CLOSE 出場
    open_ts_raw = (
        position.get("entry_bar_id")
        or position.get("open_timestamp")
        or position.get("created_at")
        or meta.get("open_timestamp")
    )
    open_ts_ms = _norm_bar_ms(open_ts_raw)
    open_bar_minute_ms = (int(open_ts_ms) // 60_000) * 60_000 if open_ts_ms is not None else None
    can_eval_closed_bar = not (
        open_bar_minute_ms is not None and closed_bar_id is not None and int(closed_bar_id) < int(open_bar_minute_ms)
    )

    # 5M 大局觀模式判定 (BEAR_ONLY / BULL_ONLY / CHOP_LOCK)
    src_5m = None
    if cross_symbol_frames and isinstance(cross_symbol_frames, Mapping) and "5M" in cross_symbol_frames:
        src_5m = cross_symbol_frames["5M"]
    if src_5m is None or not isinstance(src_5m, pd.DataFrame) or len(src_5m) < 16:
        resampled_5m = resample_1m_to_5m(closed)
        if len(resampled_5m) >= 16:
            src_5m = resampled_5m
    trend_5m_state = resolve_5m_trend_master_state(src_5m, closed, price) if src_5m is not None else {"available": False}
    master_5m_mode = trend_5m_state.get("master_mode", "NEUTRAL") if trend_5m_state.get("available") else "NEUTRAL"

    # 原則一：計算 KC 中軌連續斜率變化，嚴格判定中軌走勢是否明顯反向
    reversal_slope_pct = float(getattr(config, "min_kc_mid_reversal_slope_pct", 0.00015))
    mid_trend_intact, s0, s1, _calc_dir, slope_hold_reason = _compute_kc_mid_trend_intact(
        closed, side, min_reversal_slope_pct=reversal_slope_pct
    )
    kc_mid_slope = s0
    flat_eps = 1e-12
    mid_slope_intact = mid_trend_intact
    blocked_by_mid_slope_intact = False

    # ══════════════════════════════════════════════════════════════════════════
    # 第二層判斷（優先於移動止損與破中軌）：極值區「峰谷三點（分型）」主動逃頂平倉（BAR_CLOSE）
    # ══════════════════════════════════════════════════════════════════════════
    if can_eval_closed_bar and len(closed) >= 3 and math.isfinite(closed_close) and closed_close > 0:
        k3_row = closed.iloc[-3]
        k2_row = closed.iloc[-2]
        k1_row = closed.iloc[-1]
        k3_h = _safe_float(k3_row.get("high"))
        k2_h = _safe_float(k2_row.get("high"))
        k1_h = _safe_float(k1_row.get("high"))
        k3_l = _safe_float(k3_row.get("low"))
        k2_l = _safe_float(k2_row.get("low"))
        k1_l = _safe_float(k1_row.get("low"))
        k2_c = _safe_float(k2_row.get("close"))
        k1_c = _safe_float(k1_row.get("close"))
        k1_o = _safe_float(k1_row.get("open"), default=k1_c)
        k1_ma5 = _safe_float(k1_row.get("ma5"), default=float("nan"))

        k2_kc_u = _safe_float(k2_row.get("kc_upper"), default=closed_kc_u)
        k2_kc_m = _safe_float(k2_row.get("kc_middle"), default=closed_kc_m)
        k2_kc_l = _safe_float(k2_row.get("kc_lower"), default=closed_kc_l)
        if not (math.isfinite(k2_kc_u) and k2_kc_u > 0):
            k2_kc_u = closed_kc_u
        if not (math.isfinite(k2_kc_m) and k2_kc_m > 0):
            k2_kc_m = closed_kc_m
        if not (math.isfinite(k2_kc_l) and k2_kc_l > 0):
            k2_kc_l = closed_kc_l

        kc_ratio = max(0.0, float(config.extreme_fractal_kc_ratio))
        min_profit_pct = max(0.0, float(config.extreme_fractal_min_profit_pct))

        # 原則二：確認價格曾經觸及或刺穿外軌極值區，且走出確定性三點反轉
        if side == "LONG":
            touched_upper = bool(
                peak_price >= closed_kc_u
                or (math.isfinite(k2_kc_u) and k2_h >= k2_kc_u)
                or (position.get("pv_touched_kc_upper") or meta.get("pv_touched_kc_upper"))
                or (math.isfinite(k2_kc_u) and math.isfinite(k2_kc_m) and k2_h >= (k2_kc_m + kc_ratio * (k2_kc_u - k2_kc_m)))
            )
            is_top_fractal = (
                math.isfinite(k3_h) and math.isfinite(k2_h) and math.isfinite(k1_h)
                and math.isfinite(k2_l) and math.isfinite(k1_l)
                and math.isfinite(k2_c) and math.isfinite(k1_c)
                and k2_h > k3_h and k2_h > k1_h
                and k1_l < k2_l and k1_c < k2_c
                and (not math.isfinite(k1_ma5) or k1_c < k1_ma5)
            )
            closed_profit_pct = (k1_c - entry_price) / entry_price if entry_price > 0 else 0.0
            if touched_upper and is_top_fractal and closed_profit_pct >= min_profit_pct:
                if is_sync_locked:
                    logger.info(
                        "%s symbol=%s side=LONG blocked=ACTIVE_EXIT_PV_EXTREME_FRACTAL_LONG sync_details=%s",
                        sync_lock_reason, resolved_symbol or "", sync_details,
                    )
                    return _build_exit(False, sync_lock_reason, "NONE", sync_lock_reason)
                elif mid_slope_intact:
                    blocked_by_mid_slope_intact = True
                    logger.info(
                        "%s symbol=%s side=LONG blocked=ACTIVE_EXIT_PV_EXTREME_FRACTAL_LONG kc_mid_slope=%.8f dir=%s",
                        slope_hold_reason, resolved_symbol or "", kc_mid_slope, closed_kc_dir,
                    )
                else:
                    return _build_exit(
                        True, "ACTIVE_EXIT_PV_EXTREME_FRACTAL_LONG", "BAR_CLOSE", "LAYER2_EXTREME_FRACTAL_TP"
                    )
        elif side == "SHORT":
            touched_lower = bool(
                trough_price <= closed_kc_l
                or (math.isfinite(k2_kc_l) and k2_l <= k2_kc_l)
                or (position.get("pv_touched_kc_lower") or meta.get("pv_touched_kc_lower"))
                or (math.isfinite(k2_kc_l) and math.isfinite(k2_kc_m) and k2_l <= (k2_kc_m - kc_ratio * (k2_kc_m - k2_kc_l)))
            )
            is_bottom_fractal = (
                math.isfinite(k3_l) and math.isfinite(k2_l) and math.isfinite(k1_l)
                and math.isfinite(k2_h) and math.isfinite(k1_h)
                and math.isfinite(k2_c) and math.isfinite(k1_c)
                and k2_l < k3_l and k2_l < k1_l
                and k1_h > k2_h and k1_c > k2_c
                and (not math.isfinite(k1_ma5) or k1_c > k1_ma5)
            )
            closed_profit_pct = (entry_price - k1_c) / entry_price if entry_price > 0 else 0.0
            if touched_lower and is_bottom_fractal and closed_profit_pct >= min_profit_pct:
                if is_sync_locked:
                    logger.info(
                        "%s symbol=%s side=SHORT blocked=ACTIVE_EXIT_PV_EXTREME_FRACTAL_SHORT sync_details=%s",
                        sync_lock_reason, resolved_symbol or "", sync_details,
                    )
                    return _build_exit(False, sync_lock_reason, "NONE", sync_lock_reason)
                elif mid_slope_intact:
                    blocked_by_mid_slope_intact = True
                    logger.info(
                        "%s symbol=%s side=SHORT blocked=ACTIVE_EXIT_PV_EXTREME_FRACTAL_SHORT kc_mid_slope=%.8f dir=%s",
                        slope_hold_reason, resolved_symbol or "", kc_mid_slope, closed_kc_dir,
                    )
                else:
                    return _build_exit(
                        True, "ACTIVE_EXIT_PV_EXTREME_FRACTAL_SHORT", "BAR_CLOSE", "LAYER2_EXTREME_FRACTAL_TP"
                    )

    # ══════════════════════════════════════════════════════════════════════════
    # 第三層判斷：動態移動止損（已達啟動門檻且覆蓋雙邊手續費後，盤中即時保護既得浮盈）
    # 🚨 順勢波段鐵律：只要 KC 中軌方向未反向（多單未向下、空單未向上），
    #    一律強制 HOLD_KC_MID_NOT_CLEARLY_* 續抱，嚴禁在波段行進中途因微幅回吐被 TICK 移動止損洗出場！
    # ══════════════════════════════════════════════════════════════════════════
    if trailing_armed and trailing_stop_price > 0:
        if side == "LONG" and price <= trailing_stop_price:
            if mid_slope_intact:
                blocked_by_mid_slope_intact = True
                logger.info(
                    "%s symbol=%s side=LONG blocked=ACTIVE_EXIT_PV_TRAILING_PROFIT_LOCK price=%.6f stop=%.6f kc_mid_slope=%.8f dir=%s",
                    slope_hold_reason, resolved_symbol or "", price, trailing_stop_price, kc_mid_slope, closed_kc_dir,
                )
            else:
                return _build_exit(True, "ACTIVE_EXIT_PV_TRAILING_PROFIT_LOCK", "TICK", "LAYER3_TRAILING_STOP")
        if side == "SHORT" and price >= trailing_stop_price:
            if mid_slope_intact:
                blocked_by_mid_slope_intact = True
                logger.info(
                    "%s symbol=%s side=SHORT blocked=ACTIVE_EXIT_PV_TRAILING_PROFIT_LOCK price=%.6f stop=%.6f kc_mid_slope=%.8f dir=%s",
                    slope_hold_reason, resolved_symbol or "", price, trailing_stop_price, kc_mid_slope, closed_kc_dir,
                )
            else:
                return _build_exit(True, "ACTIVE_EXIT_PV_TRAILING_PROFIT_LOCK", "TICK", "LAYER3_TRAILING_STOP")

    # ══════════════════════════════════════════════════════════════════════════
    # 第四層判斷（兜底）：中軌破位與趨勢反轉平倉（嚴格等 1M K 棒收盤確認）
    # ══════════════════════════════════════════════════════════════════════════
    if not can_eval_closed_bar:
        return _build_exit(False, "HOLD_KC_WAIT_BAR_CLOSE", "NONE", "HOLD_WAIT_BAR_CLOSE")

    if len(closed) >= 2 and math.isfinite(closed_close) and closed_close > 0:
        long_mid_broken = (
            (closed_close < mid_buffer_stop) if math.isfinite(mid_buffer_stop) else (closed_close < closed_kc_m)
        )
        short_mid_broken = (
            (closed_close > mid_buffer_stop) if math.isfinite(mid_buffer_stop) else (closed_close > closed_kc_m)
        )

        # 4A. 放寬中軌止損緩衝區（已收盤價跌破 KC 中軌 - 1.0*ATR 平多 / 漲破 KC 中軌 + 1.0*ATR 平空）
        #     若處於跨幣種大趨勢共振鎖定，或處於 5M 純單邊順勢模式，或 KC 中軌沒有明顯反向，一律 HOLD 續抱，防止單邊主浪中途被假突破洗出場
        if side == "LONG" and long_mid_broken:
            if is_sync_locked:
                logger.info(
                    "%s symbol=%s side=LONG blocked=ACTIVE_EXIT_KC_MID_BUFFER_LONG sync_details=%s",
                    sync_lock_reason, resolved_symbol or "", sync_details,
                )
                return _build_exit(False, sync_lock_reason, "NONE", sync_lock_reason)
            elif master_5m_mode == "BULL_ONLY":
                logger.info(
                    "HOLD_5M_BULL_ONLY_VETO_1M_MID_BREAK symbol=%s side=LONG close=%.6f stop=%.6f",
                    resolved_symbol or "", closed_close, mid_buffer_stop,
                )
                return _build_exit(False, "HOLD_5M_BULL_ONLY_VETO_1M_MID_BREAK", "NONE", "HOLD_5M_BULL_ONLY")
            elif mid_slope_intact:
                logger.info(
                    "%s symbol=%s side=LONG blocked=ACTIVE_EXIT_KC_MID_BUFFER_LONG close=%.6f stop=%.6f kc_mid_slope=%.8f dir=%s",
                    slope_hold_reason, resolved_symbol or "", closed_close, mid_buffer_stop, kc_mid_slope, closed_kc_dir,
                )
                return _build_exit(False, slope_hold_reason, "NONE", slope_hold_reason)
            return _build_exit(True, "ACTIVE_EXIT_KC_MID_BUFFER_LONG", "BAR_CLOSE", "LAYER4_MID_BUFFER_BREAK")

        if side == "SHORT" and short_mid_broken:
            if is_sync_locked:
                logger.info(
                    "%s symbol=%s side=SHORT blocked=ACTIVE_EXIT_KC_MID_BUFFER_SHORT sync_details=%s",
                    sync_lock_reason, resolved_symbol or "", sync_details,
                )
                return _build_exit(False, sync_lock_reason, "NONE", sync_lock_reason)
            elif master_5m_mode == "BEAR_ONLY":
                logger.info(
                    "HOLD_5M_BEAR_ONLY_VETO_1M_MID_BREAK symbol=%s side=SHORT close=%.6f stop=%.6f",
                    resolved_symbol or "", closed_close, mid_buffer_stop,
                )
                return _build_exit(False, "HOLD_5M_BEAR_ONLY_VETO_1M_MID_BREAK", "NONE", "HOLD_5M_BEAR_ONLY")
            elif mid_slope_intact:
                logger.info(
                    "%s symbol=%s side=SHORT blocked=ACTIVE_EXIT_KC_MID_BUFFER_SHORT close=%.6f stop=%.6f kc_mid_slope=%.8f dir=%s",
                    slope_hold_reason, resolved_symbol or "", closed_close, mid_buffer_stop, kc_mid_slope, closed_kc_dir,
                )
                return _build_exit(False, slope_hold_reason, "NONE", slope_hold_reason)
            return _build_exit(True, "ACTIVE_EXIT_KC_MID_BUFFER_SHORT", "BAR_CLOSE", "LAYER4_MID_BUFFER_BREAK")

    if blocked_by_mid_slope_intact:
        return _build_exit(False, slope_hold_reason, "NONE", slope_hold_reason)
    return _build_exit(False, "HOLD_KC_TREND_INTACT", "NONE", "HOLD_TREND_INTACT")


def evaluate_peak_valley_exit(
    df: pd.DataFrame,
    *,
    position: Mapping[str, Any],
    live_price: float,
    metadata: Optional[Mapping[str, Any]] = None,
    last_flip_bar_id: Optional[int] = None,
    symbol: Optional[str] = None,
    cross_symbol_frames: Optional[Mapping[str, pd.DataFrame]] = None,
    config: Optional[PeakValleyExitConfig] = None,
) -> Dict[str, Any]:
    """評估策略之平倉與風控決策（預設走 KC 肯特納通道三層防洗盤出場架構，保留 legacy_pv 相容模式）。

    回傳重點欄位：
    - ``should_exit`` / ``action`` / ``reason`` / ``trigger_level`` ("TICK" | "BAR_CLOSE" | "LOCKED" | "NONE")
    - ``should_flip`` / ``flip_side`` / ``flip_reason`` / ``flip_metrics``
    - ``current_bar_id`` / ``anti_whipsaw`` / ``state_updates``
    """
    resolved_symbol = symbol
    if not resolved_symbol and isinstance(position, Mapping):
        resolved_symbol = position.get("symbol")
    if not resolved_symbol and isinstance(metadata, Mapping):
        resolved_symbol = metadata.get("symbol")
    if not resolved_symbol and isinstance(getattr(df, "attrs", None), dict):
        resolved_symbol = df.attrs.get("symbol")
    config = resolve_peak_valley_exit_config(
        str(resolved_symbol) if resolved_symbol else None,
        config,
    )
    if str(getattr(config, "exit_mode", "kc_layered") or "kc_layered").lower() != "legacy_pv":
        return evaluate_kc_layered_exit(
            df,
            position=position,
            live_price=live_price,
            metadata=metadata,
            last_flip_bar_id=last_flip_bar_id,
            symbol=resolved_symbol,
            cross_symbol_frames=cross_symbol_frames,
            config=config,
        )
    side = str((position or {}).get("side") or "").upper()
    entry_price = _safe_float((position or {}).get("entry_price"))
    price = _safe_float(live_price)
    leverage = _safe_float((position or {}).get("leverage"), default=1.0)
    if leverage <= 0:
        leverage = 1.0

    current_bar_id = extract_bar_id(df)
    pos_flip_bar_id = (position or {}).get("pv_flip_bar_id") or (metadata or {}).get("pv_flip_bar_id")
    effective_flip_bar_id: Optional[int] = None
    for candidate_bid in (pos_flip_bar_id, last_flip_bar_id):
        v = _safe_float(candidate_bid)
        if math.isfinite(v) and v > 0:
            effective_flip_bar_id = int(round(v * 1000.0 if v < 1e11 else v))
            break

    anti_whipsaw = {
        "same_bar_reverse_allowed": bool(config.same_bar_reverse_allowed),
        "reentry_cooldown_seconds": float(config.reentry_cooldown_seconds),
        "flip_on_reverse_inflection": bool(config.flip_on_reverse_inflection),
        "enforce_same_bar_flip_exit_lock": bool(config.enforce_same_bar_flip_exit_lock),
        "current_bar_id": current_bar_id,
        "locked_flip_bar_id": effective_flip_bar_id,
    }

    def _invalid_hold(reason_code: str, trigger_lvl: str = "NONE", flip_reason: str = "NO_EXIT") -> Dict[str, Any]:
        return {
            "action": "HOLD",
            "should_exit": False,
            "reason": reason_code,
            "trigger_level": trigger_lvl,
            "should_flip": False,
            "flip_side": None,
            "flip_reason": flip_reason,
            "flip_metrics": {},
            "current_bar_id": current_bar_id,
            "anti_whipsaw": anti_whipsaw,
            "metrics": {},
            "state_updates": {},
        }

    if (
        side not in ("LONG", "SHORT")
        or not math.isfinite(entry_price) or entry_price <= 0
        or not math.isfinite(price) or price <= 0
        or df is None or not isinstance(df, pd.DataFrame) or len(df) < 3
    ):
        return _invalid_hold("HOLD_INVALID_EXIT_INPUT")

    # ── 【單根 K 線防重複刷單鎖】 ──────────────────────────────────────────────
    # 若當前倉位是在同一根 1M K 線透過反手開倉建立，鎖死本根 K 線內的所有平倉與二次反手權限，
    # 強制至少等到下一根 1M K 線開盤（current_bar_id != effective_flip_bar_id）才重新評估平倉，
    # 徹底杜絕單一分鐘內劇烈插針造成連續來回雙巴！
    if (
        config.enforce_same_bar_flip_exit_lock
        and current_bar_id is not None
        and effective_flip_bar_id is not None
        and int(current_bar_id) == int(effective_flip_bar_id)
    ):
        return _invalid_hold(
            "HOLD_SAME_BAR_FLIP_EXIT_LOCKED",
            trigger_lvl="LOCKED",
            flip_reason="LOCKED_IN_SAME_1M_FLIP_BAR",
        )

    meta = dict(metadata or {})
    working = _build_live_close_frame(df, price)
    closed = _latest_closed_bars(df)
    if closed.empty:
        closed = working

    curr_ma5, prev_ma5, prev2_ma5 = _compute_live_ma5_triplet(working, closed, price)
    curr_ma15, curr_adx = _compute_live_ma15_and_adx(working, closed, price)
    kc_u, kc_m, kc_l = _resolve_kc_bands(working, closed)

    # 跨 Tick 追蹤最有利極值（波峰最高價 / 波谷最低價）與峰值報酬
    stored_peak = _safe_float(
        position.get("pv_peak_price") or meta.get("pv_peak_price") or position.get("highest_price"),
        default=entry_price,
    )
    stored_trough = _safe_float(
        position.get("pv_trough_price") or meta.get("pv_trough_price") or position.get("lowest_price"),
        default=entry_price,
    )
    peak_price = max(stored_peak, entry_price, price)
    trough_price = min(stored_trough, entry_price, price)

    if side == "LONG":
        curr_return_pct = (price - entry_price) / entry_price
        peak_return_pct = (peak_price - entry_price) / entry_price
        retrace_from_extreme_pct = (peak_price - price) / peak_price if peak_price > 0 else 0.0
    else:
        curr_return_pct = (entry_price - price) / entry_price
        peak_return_pct = (entry_price - trough_price) / entry_price
        retrace_from_extreme_pct = (price - trough_price) / trough_price if trough_price > 0 else 0.0

    stored_peak_profit = _safe_float(
        position.get("pv_peak_profit_pct") or meta.get("pv_peak_profit_pct"),
        default=0.0,
    )
    curr_profit_metric = curr_return_pct * leverage if config.use_net_roe_for_trailing else curr_return_pct
    peak_profit_metric = max(
        stored_peak_profit,
        peak_return_pct * leverage if config.use_net_roe_for_trailing else peak_return_pct,
        curr_profit_metric,
    )

    # 紀錄近期（lookback_bars 內）是否曾觸及/刺穿 KC 上下軌，避免跨過多根 K 棒後陳舊記憶誤觸發極值止盈
    live_bar_high = _safe_float(working.iloc[-1].get("high"), default=price)
    live_bar_low = _safe_float(working.iloc[-1].get("low"), default=price)
    touch_lookback = max(1, int(config.kc_extreme_touch_lookback_bars))
    max_touch_age_ms = touch_lookback * 60_000
    ts_col = "timestamp" if "timestamp" in working.columns else ("time" if "time" in working.columns else None)

    def _resolve_recent_band_touch(
        *,
        is_upper: bool,
        fallback_band: float,
        live_hit: bool,
        stored_flag: Any,
        stored_bar_raw: Any,
    ) -> Tuple[bool, Optional[int]]:
        if live_hit:
            return True, current_bar_id
        latest_recent_bid: Optional[int] = None
        band_col = "kc_upper" if is_upper else "kc_lower"
        px_col = "high" if is_upper else "low"
        recent_bars = working.tail(touch_lookback)
        for idx_pos in range(len(recent_bars) - 1, -1, -1):
            row = recent_bars.iloc[idx_pos]
            px_val = _safe_float(row.get(px_col))
            band_val = _safe_float(row.get(band_col), default=fallback_band)
            if math.isfinite(px_val) and math.isfinite(band_val) and band_val > 0:
                hit = (px_val >= band_val) if is_upper else (px_val <= band_val)
                if hit:
                    bid = _norm_bar_ms(row.get(ts_col)) if ts_col else current_bar_id
                    latest_recent_bid = bid if bid is not None else current_bar_id
                    break
        if latest_recent_bid is not None:
            return True, latest_recent_bid
        if bool(stored_flag):
            stored_bid = _norm_bar_ms(stored_bar_raw)
            if stored_bid is None or current_bar_id is None:
                return True, current_bar_id
            if 0 <= (int(current_bar_id) - int(stored_bid)) <= max_touch_age_ms:
                return True, int(stored_bid)
        return False, None

    touched_kc_upper, touched_kc_upper_bar_id = _resolve_recent_band_touch(
        is_upper=True,
        fallback_band=kc_u,
        live_hit=bool(math.isfinite(kc_u) and (price >= kc_u or live_bar_high >= kc_u)),
        stored_flag=position.get("pv_touched_kc_upper") or meta.get("pv_touched_kc_upper"),
        stored_bar_raw=position.get("pv_touched_kc_upper_bar_id") or meta.get("pv_touched_kc_upper_bar_id"),
    )
    touched_kc_lower, touched_kc_lower_bar_id = _resolve_recent_band_touch(
        is_upper=False,
        fallback_band=kc_l,
        live_hit=bool(math.isfinite(kc_l) and (price <= kc_l or live_bar_low <= kc_l)),
        stored_flag=position.get("pv_touched_kc_lower") or meta.get("pv_touched_kc_lower"),
        stored_bar_raw=position.get("pv_touched_kc_lower_bar_id") or meta.get("pv_touched_kc_lower_bar_id"),
    )

    trailing_armed = bool(
        position.get("pv_trailing_armed")
        or meta.get("pv_trailing_armed")
        or (config.trailing_activate_pct > 0 and peak_profit_metric >= config.trailing_activate_pct)
    )

    state_updates = {
        "pv_peak_price": peak_price,
        "pv_trough_price": trough_price,
        "pv_peak_profit_pct": peak_profit_metric,
        "pv_trailing_armed": trailing_armed,
        "pv_touched_kc_upper": touched_kc_upper,
        "pv_touched_kc_lower": touched_kc_lower,
        "pv_touched_kc_upper_bar_id": touched_kc_upper_bar_id,
        "pv_touched_kc_lower_bar_id": touched_kc_lower_bar_id,
    }

    # 前一根已收線 1M K 棒高低點與進場前結構波段極值
    prev_closed_bar = closed.iloc[-2] if len(closed) >= 2 else closed.iloc[-1]
    prev_closed_high = _safe_float(prev_closed_bar.get("high"))
    prev_closed_low = _safe_float(prev_closed_bar.get("low"))

    swing_low = _safe_float(position.get("entry_swing_low") or meta.get("entry_swing_low"))
    swing_high = _safe_float(position.get("entry_swing_high") or meta.get("entry_swing_high"))
    if not (math.isfinite(swing_low) and swing_low > 0) or not (math.isfinite(swing_high) and swing_high > 0):
        lookback_df = closed.iloc[:-1] if len(closed) > 2 else closed
        lookback_df = lookback_df.tail(max(2, int(config.swing_lookback_bars)))
        if not (math.isfinite(swing_low) and swing_low > 0) and "low" in lookback_df.columns:
            swing_low = _safe_float(pd.to_numeric(lookback_df["low"], errors="coerce").min())
        if not (math.isfinite(swing_high) and swing_high > 0) and "high" in lookback_df.columns:
            swing_high = _safe_float(pd.to_numeric(lookback_df["high"], errors="coerce").max())

    curr_kc_width_pct = (
        (kc_u - kc_l) / kc_m
        if (math.isfinite(kc_u) and math.isfinite(kc_l) and math.isfinite(kc_m) and kc_m > 0)
        else float("nan")
    )
    entry_kc_width_pct = _safe_float(position.get("entry_kc_width_pct") or meta.get("entry_kc_width_pct"))

    curr_ma5_slope = (curr_ma5 - prev_ma5) if (math.isfinite(curr_ma5) and math.isfinite(prev_ma5)) else float("nan")
    prev_ma5_slope = (prev_ma5 - prev2_ma5) if (math.isfinite(prev_ma5) and math.isfinite(prev2_ma5)) else float("nan")
    giveback_ratio = (
        (peak_profit_metric - curr_profit_metric) / peak_profit_metric
        if peak_profit_metric > 0
        else 0.0
    )

    has_3point_pivot, pivot_details = has_confirmed_three_point_pivot(closed, side)
    trend_intact, inside_channel, kc_mid_slope_val = _is_trend_intact_inside_channel(
        side=side,
        price=price,
        closed=closed,
        working=working,
        kc_u=kc_u,
        kc_m=kc_m,
        kc_l=kc_l,
        curr_ma5=curr_ma5,
        curr_ma15=curr_ma15,
    )
    veto_ordinary_without_3pt = bool(
        config.require_3point_pivot_when_in_trend_channel
        and trend_intact
        and inside_channel
        and not has_3point_pivot
    )

    metrics: Dict[str, Any] = {
        "side": side,
        "entry_price": entry_price,
        "live_price": price,
        "curr_return_pct": curr_return_pct,
        "peak_return_pct": peak_return_pct,
        "curr_profit_metric": curr_profit_metric,
        "peak_profit_metric": peak_profit_metric,
        "giveback_ratio": giveback_ratio,
        "retrace_from_extreme_pct": retrace_from_extreme_pct,
        "trailing_armed": trailing_armed,
        "curr_ma5": curr_ma5 if math.isfinite(curr_ma5) else None,
        "prev_ma5": prev_ma5 if math.isfinite(prev_ma5) else None,
        "curr_ma15": curr_ma15 if math.isfinite(curr_ma15) else None,
        "curr_adx": curr_adx,
        "curr_ma5_slope": curr_ma5_slope if math.isfinite(curr_ma5_slope) else None,
        "prev_ma5_slope": prev_ma5_slope if math.isfinite(prev_ma5_slope) else None,
        "kc_upper": kc_u if math.isfinite(kc_u) else None,
        "kc_middle": kc_m if math.isfinite(kc_m) else None,
        "kc_lower": kc_l if math.isfinite(kc_l) else None,
        "curr_kc_width_pct": curr_kc_width_pct if math.isfinite(curr_kc_width_pct) else None,
        "entry_kc_width_pct": entry_kc_width_pct if math.isfinite(entry_kc_width_pct) else None,
        "touched_kc_upper": touched_kc_upper,
        "touched_kc_lower": touched_kc_lower,
        "prev_closed_high": prev_closed_high if math.isfinite(prev_closed_high) else None,
        "prev_closed_low": prev_closed_low if math.isfinite(prev_closed_low) else None,
        "swing_low": swing_low if math.isfinite(swing_low) else None,
        "swing_high": swing_high if math.isfinite(swing_high) else None,
        "current_bar_id": current_bar_id,
        "has_3point_pivot": has_3point_pivot,
        "trend_intact": trend_intact,
        "inside_channel": inside_channel,
        "kc_mid_slope": kc_mid_slope_val,
        "pivot_details": pivot_details,
    }

    def _result(should_exit: bool, reason: str, trigger_level: str) -> Dict[str, Any]:
        action = ("CLOSE_LONG" if side == "LONG" else "CLOSE_SHORT") if should_exit else "HOLD"
        should_flip = False
        flip_side: Optional[str] = None
        flip_reason = "NO_EXIT_TRIGGER"
        flip_metrics: Dict[str, Any] = {}
        enter_flip_watch = False
        flip_watch_side: Optional[str] = None
        if should_exit:
            if config.enable_flip_watch:
                # 徹底脫鉤：平倉時絕不直接送反手單；僅合資格之明確反轉平倉進入觀察期
                if config.enable_conditional_flip and reason in PV_FLIP_ELIGIBLE_EXIT_REASONS:
                    enter_flip_watch = True
                    flip_watch_side = "SHORT" if side == "LONG" else "LONG"
                    flip_reason = f"ENTER_FLIP_WATCH_{flip_watch_side}_FROM_{reason}"
                else:
                    flip_reason = f"FLIP_WATCH_FORBIDDEN:{reason}"
            else:
                # 相容模式（僅當顯式停用 Flip Watch 時才啟用同棒審查）
                should_flip, flip_side, flip_reason, flip_metrics = evaluate_flip_eligibility(
                    closed_side=side,
                    exit_reason=reason,
                    current_bar_id=current_bar_id,
                    last_flip_bar_id=effective_flip_bar_id,
                    kc_upper=kc_u,
                    kc_middle=kc_m,
                    kc_lower=kc_l,
                    curr_ma5=curr_ma5,
                    curr_ma15=curr_ma15,
                    curr_adx=curr_adx,
                    config=config,
                )
        return {
            "action": action,
            "should_exit": should_exit,
            "reason": reason,
            "trigger_level": trigger_level,
            "should_flip": should_flip,
            "flip_side": flip_side,
            "flip_reason": flip_reason,
            "flip_metrics": flip_metrics,
            "enter_flip_watch": enter_flip_watch,
            "flip_watch_side": flip_watch_side,
            "flip_watch_window_bars": int(config.flip_watch_window_bars) if enter_flip_watch else 0,
            "current_bar_id": current_bar_id,
            "anti_whipsaw": anti_whipsaw,
            "metrics": metrics,
            "state_updates": state_updates,
        }

    # ══════════════════════════════════════════════════════════════════════════
    # 一、Tick 級即時平倉與風控（無需等待 1M K 線收盤，收到 Tick 即觸發）
    # ══════════════════════════════════════════════════════════════════════════

    # Rule 4A (Tick): 單筆最大價差硬止損（觸及即市價逃命，嚴禁反手）
    if config.max_stop_loss_pct > 0 and curr_return_pct <= -config.max_stop_loss_pct:
        return _result(True, "ACTIVE_EXIT_PV_MAX_STOP_LOSS", "TICK")

    # Rule 4B (Tick): 假轉折結構破位快速止損（跌破進場前波谷 / 突破進場前波峰，嚴禁反手）
    if side == "LONG" and math.isfinite(swing_low) and swing_low > 0:
        if price < swing_low * (1.0 - max(0.0, config.swing_break_buffer_pct)):
            return _result(True, "ACTIVE_EXIT_PV_FAKE_INFLECTION_SWING_BREAK", "TICK")
    if side == "SHORT" and math.isfinite(swing_high) and swing_high > 0:
        if price > swing_high * (1.0 + max(0.0, config.swing_break_buffer_pct)):
            return _result(True, "ACTIVE_EXIT_PV_FAKE_INFLECTION_SWING_BREAK", "TICK")

    # Rule 2 (Tick): 觸及 KC 通道極值後反轉精準止盈（符合條件可進一步審查反手）
    if not veto_ordinary_without_3pt and side == "LONG" and touched_kc_upper:
        prev_break = bool(
            config.enable_kc_prev_bar_break
            and math.isfinite(prev_closed_low)
            and prev_closed_low > 0
            and price < prev_closed_low
        )
        retrace_hit = bool(
            config.kc_extreme_retrace_pct > 0
            and retrace_from_extreme_pct >= config.kc_extreme_retrace_pct
        )
        if prev_break or retrace_hit:
            return _result(True, "ACTIVE_EXIT_PV_KC_EXTREME_TP_LONG", "TICK")

    if not veto_ordinary_without_3pt and side == "SHORT" and touched_kc_lower:
        prev_break = bool(
            config.enable_kc_prev_bar_break
            and math.isfinite(prev_closed_high)
            and prev_closed_high > 0
            and price > prev_closed_high
        )
        retrace_hit = bool(
            config.kc_extreme_retrace_pct > 0
            and retrace_from_extreme_pct >= config.kc_extreme_retrace_pct
        )
        if prev_break or retrace_hit:
            return _result(True, "ACTIVE_EXIT_PV_KC_EXTREME_TP_SHORT", "TICK")

    # Rule 3 (Tick): 動態保底鎖利 Trailing Stop（達啟動門檻且利潤/價格回吐超標即平倉，嚴禁反手）
    if not veto_ordinary_without_3pt and trailing_armed and peak_profit_metric > 0:
        giveback_hit = bool(
            config.trailing_profit_giveback_ratio > 0
            and giveback_ratio >= config.trailing_profit_giveback_ratio
        )
        price_retrace_hit = bool(
            config.trailing_price_retrace_pct > 0
            and retrace_from_extreme_pct >= config.trailing_price_retrace_pct
        )
        if giveback_hit or price_retrace_hit:
            return _result(True, "ACTIVE_EXIT_PV_TRAILING_PROFIT_LOCK", "TICK")

    # Rule 1 (Tick): 反向峰谷拐點即時平倉（MA5 斜率轉向 + 即時價格跌破/突破 MA5，符合條件可審查反手）
    if not veto_ordinary_without_3pt and math.isfinite(curr_ma5_slope) and math.isfinite(prev_ma5_slope):
        if side == "LONG":
            turned_down = prev_ma5_slope >= -1e-12 and curr_ma5_slope < -config.ma5_slope_eps
            crossed = (not config.require_tick_cross_ma5) or (price < curr_ma5)
            if turned_down and crossed:
                return _result(True, "ACTIVE_EXIT_PV_REVERSE_INFLECTION_LONG", "TICK")
        else:
            turned_up = prev_ma5_slope <= 1e-12 and curr_ma5_slope > config.ma5_slope_eps
            crossed = (not config.require_tick_cross_ma5) or (price > curr_ma5)
            if turned_up and crossed:
                return _result(True, "ACTIVE_EXIT_PV_REVERSE_INFLECTION_SHORT", "TICK")

    # ══════════════════════════════════════════════════════════════════════════
    # 二、1M K 線收盤確認風險控制（陷入盤整且浮虧時平倉，嚴禁反手）
    # ══════════════════════════════════════════════════════════════════════════

    is_closed_bar_present = (
        bool(df.iloc[-1].get("is_closed", True))
        or len(closed) >= 2
    )
    if is_closed_bar_present and curr_return_pct < 0 and math.isfinite(curr_kc_width_pct):
        width_narrow = bool(
            config.kc_collapse_width_pct > 0
            and curr_kc_width_pct <= config.kc_collapse_width_pct
        )
        width_shrunk = bool(
            math.isfinite(entry_kc_width_pct)
            and entry_kc_width_pct > 0
            and config.kc_collapse_shrink_ratio > 0
            and curr_kc_width_pct <= entry_kc_width_pct * config.kc_collapse_shrink_ratio
        )
        if width_narrow or width_shrunk:
            return _result(True, "ACTIVE_EXIT_PV_CONSOLIDATION_FAIL_SL", "BAR_CLOSE")

    if veto_ordinary_without_3pt:
        return _result(False, "HOLD_TREND_INTACT_WITHOUT_3POINT_PIVOT", "NONE")
    return _result(False, "HOLD_NO_EXIT_TRIGGER", "NONE")


def _norm_bar_ms(bid: Any) -> Optional[int]:
    """將 K 棒時間戳標準化為整數毫秒。"""
    v = _safe_float(bid)
    if not math.isfinite(v) or v <= 0:
        return None
    return int(round(v * 1000.0 if v < 1e11 else v))


def resample_1m_to_5m(df_1m: Optional[pd.DataFrame]) -> pd.DataFrame:
    """從 1M OHLCV DataFrame 重採樣為 5M OHLCV（保留當前未收線動態棒）。"""
    if df_1m is None or not isinstance(df_1m, pd.DataFrame) or df_1m.empty:
        return pd.DataFrame()
    ts_col = "timestamp" if "timestamp" in df_1m.columns else ("time" if "time" in df_1m.columns else None)
    if ts_col is None or not {"open", "high", "low", "close"}.issubset(df_1m.columns):
        return pd.DataFrame()
    ts_vals = pd.to_numeric(df_1m[ts_col], errors="coerce")
    valid_mask = ts_vals.notna() & (ts_vals > 0)
    if not valid_mask.any():
        return pd.DataFrame()
    work = df_1m.loc[valid_mask].copy()
    ts_num = ts_vals.loc[valid_mask].astype(float)
    ts_ms = ts_num.where(ts_num >= 1e11, ts_num * 1000.0).round().astype("int64")
    work["_bucket_5m"] = (ts_ms // 300_000) * 300_000
    agg_spec: Dict[str, Any] = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    if "volume" in work.columns:
        agg_spec["volume"] = "sum"
    grouped = work.sort_values("_bucket_5m").groupby("_bucket_5m", as_index=False).agg(agg_spec)
    grouped = grouped.rename(columns={"_bucket_5m": "timestamp"})
    return grouped


def _ensure_working_indicators(working: pd.DataFrame) -> pd.DataFrame:
    """確保 1M DataFrame 具備每根 K 棒之 kc_upper/kc_middle/kc_lower 與 ma5/ma15。"""
    df_out = working.copy()
    closes = pd.to_numeric(df_out["close"], errors="coerce").astype(float)
    if "ma5" not in df_out.columns or df_out["ma5"].isna().any():
        calc_ma5 = closes.rolling(5, min_periods=1).mean()
        df_out["ma5"] = pd.to_numeric(df_out["ma5"], errors="coerce").combine_first(calc_ma5) if "ma5" in df_out.columns else calc_ma5
    if "ma15" not in df_out.columns or df_out["ma15"].isna().any():
        calc_ma15 = closes.rolling(15, min_periods=1).mean()
        df_out["ma15"] = pd.to_numeric(df_out["ma15"], errors="coerce").combine_first(calc_ma15) if "ma15" in df_out.columns else calc_ma15

    need_kc = (
        not {"kc_upper", "kc_middle", "kc_lower"}.issubset(df_out.columns)
        or df_out[["kc_upper", "kc_middle", "kc_lower"]].isna().any().any()
    )
    if need_kc:
        if len(df_out) >= 20 and {"high", "low", "close"}.issubset(df_out.columns):
            kc = compute_keltner_channels(df_out, ema_period=20, atr_period=20, multiplier=2.0)
            for col in ("kc_upper", "kc_middle", "kc_lower"):
                if col in df_out.columns:
                    df_out[col] = pd.to_numeric(df_out[col], errors="coerce").combine_first(kc[col])
                else:
                    df_out[col] = kc[col]
        elif "kc_middle" not in df_out.columns or df_out["kc_middle"].isna().any():
            ema20 = closes.ewm(span=20, adjust=False).mean()
            df_out["kc_middle"] = pd.to_numeric(df_out["kc_middle"], errors="coerce").combine_first(ema20) if "kc_middle" in df_out.columns else ema20
    return df_out


def evaluate_5m_trend_filter(
    df_5m: Optional[pd.DataFrame],
    *,
    df_1m: Optional[pd.DataFrame],
    target_side: str,
    live_price: float,
    config: PeakValleyExitConfig = DEFAULT_PEAK_VALLEY_EXIT_CONFIG,
) -> Tuple[bool, str, Dict[str, Any]]:
    """條件(b)：5M 多週期趨勢濾網。5M MA15 強勢斜率且價格位於 5M 中軌同側時禁止逆勢開單。"""
    if not config.flip_watch_enable_5m_filter:
        return True, "FLIP_WATCH_5M_FILTER_DISABLED", {}

    norm_side = str(target_side or "").upper()
    ma_period = max(2, int(config.flip_watch_5m_ma_period))

    source_name = "EXPLICIT_5M"
    src_5m = df_5m
    if src_5m is None or not isinstance(src_5m, pd.DataFrame) or len(src_5m) < ma_period + 1:
        if src_5m is not None and isinstance(src_5m, pd.DataFrame) and len(src_5m) >= 2 and {"ma15", "kc_middle", "close"}.issubset(src_5m.columns):
            source_name = "EXPLICIT_5M_PRECOMPUTED"
        else:
            resampled = resample_1m_to_5m(df_1m)
            if len(resampled) >= ma_period + 1:
                src_5m = resampled
                source_name = "RESAMPLED_5M"
            else:
                src_5m = None

    if src_5m is None or src_5m.empty or "close" not in src_5m.columns:
        if config.flip_watch_require_5m_data:
            return False, "FLIP_WATCH_BLOCKED_5M_DATA_UNAVAILABLE", {"source": "NONE"}
        return True, "FLIP_WATCH_5M_DATA_UNAVAILABLE_SKIPPED", {"source": "NONE"}

    w5 = src_5m.copy()
    if math.isfinite(live_price) and live_price > 0:
        w5.loc[w5.index[-1], "close"] = float(live_price)
        if "high" in w5.columns:
            cur_h = _safe_float(w5.iloc[-1].get("high"), default=float(live_price))
            w5.loc[w5.index[-1], "high"] = max(cur_h, float(live_price))
        if "low" in w5.columns:
            cur_l = _safe_float(w5.iloc[-1].get("low"), default=float(live_price))
            w5.loc[w5.index[-1], "low"] = min(cur_l, float(live_price))

    closes_5m = pd.to_numeric(w5["close"], errors="coerce").astype(float)
    if len(w5) >= ma_period + 1:
        ma15_series = closes_5m.rolling(ma_period, min_periods=ma_period).mean()
        if "ma15" in w5.columns:
            existing_ma15 = pd.to_numeric(w5["ma15"], errors="coerce").astype(float)
            ma15_series = ma15_series.combine_first(existing_ma15)
    elif "ma15" in w5.columns:
        ma15_series = pd.to_numeric(w5["ma15"], errors="coerce").astype(float)
    else:
        ma15_series = pd.Series(dtype=float)

    if len(ma15_series) < 2:
        if config.flip_watch_require_5m_data:
            return False, "FLIP_WATCH_BLOCKED_5M_MA15_UNAVAILABLE", {"source": source_name}
        return True, "FLIP_WATCH_5M_MA15_UNAVAILABLE_SKIPPED", {"source": source_name}

    ma15_curr = _safe_float(ma15_series.iloc[-1])
    ma15_prev = _safe_float(ma15_series.iloc[-2])

    if "kc_middle" in w5.columns and math.isfinite(_safe_float(w5["kc_middle"].iloc[-1])) and len(w5) < 20:
        kcm_5m = _safe_float(w5["kc_middle"].iloc[-1])
    elif len(w5) >= 20 and {"high", "low", "close"}.issubset(w5.columns):
        kc5 = compute_keltner_channels(w5, ema_period=20, atr_period=20, multiplier=2.0)
        kcm_5m = _safe_float(kc5["kc_middle"].iloc[-1])
    elif "kc_middle" in w5.columns and math.isfinite(_safe_float(w5["kc_middle"].iloc[-1])):
        kcm_5m = _safe_float(w5["kc_middle"].iloc[-1])
    else:
        kcm_5m = _safe_float(closes_5m.ewm(span=20, adjust=False).mean().iloc[-1])

    close_5m = _safe_float(closes_5m.iloc[-1])
    if not all(math.isfinite(v) and v > 0 for v in (ma15_curr, ma15_prev, kcm_5m, close_5m)):
        if config.flip_watch_require_5m_data:
            return False, "FLIP_WATCH_BLOCKED_5M_METRICS_INVALID", {"source": source_name}
        return True, "FLIP_WATCH_5M_METRICS_INVALID_SKIPPED", {"source": source_name}

    slope_5m_pct = (ma15_curr - ma15_prev) / ma15_prev
    threshold = max(0.0, float(config.flip_watch_5m_slope_threshold_pct))
    details = {
        "source": source_name,
        "close_5m": close_5m,
        "kc_middle_5m": kcm_5m,
        "ma15_5m_curr": ma15_curr,
        "ma15_5m_prev": ma15_prev,
        "slope_5m_pct": slope_5m_pct,
        "slope_threshold_pct": threshold,
    }

    if norm_side == "LONG":
        if slope_5m_pct < -threshold and close_5m < kcm_5m:
            return False, "FLIP_WATCH_BLOCKED_5M_BEARISH", details
    elif norm_side == "SHORT":
        if slope_5m_pct > threshold and close_5m > kcm_5m:
            return False, "FLIP_WATCH_BLOCKED_5M_BULLISH", details

    return True, "FLIP_WATCH_5M_PASS", details


def _evaluate_kc_mid_retest_structure(
    working: pd.DataFrame,
    *,
    target_side: str,
    exit_idx: int,
    anchor_low: Optional[float],
    anchor_high: Optional[float],
    live_price: float,
    buffer_pct: float = 0.0,
) -> Tuple[bool, str, Dict[str, Any]]:
    """條件(a)：站穩 KC 中軌與回測確認（轉多 Higher Low 突破前高；轉空 Lower High 跌破前低）。"""
    norm_side = str(target_side or "").upper()
    buf = max(0.0, float(buffer_pct))
    w = working.iloc[exit_idx:]
    m_len = len(w)
    if m_len < 3:
        return False, "FLIP_WATCH_WAITING_RETEST_BARS", {"bars_since_exit": m_len}

    highs = pd.to_numeric(w["high"], errors="coerce").astype(float).to_numpy()
    lows = pd.to_numeric(w["low"], errors="coerce").astype(float).to_numpy()
    closes = pd.to_numeric(w["close"], errors="coerce").astype(float).to_numpy()
    kc_mids = pd.to_numeric(w["kc_middle"], errors="coerce").astype(float).to_numpy()
    ma15s = pd.to_numeric(w["ma15"], errors="coerce").astype(float).to_numpy()
    all_highs = pd.to_numeric(working["high"], errors="coerce").astype(float).to_numpy()
    all_lows = pd.to_numeric(working["low"], errors="coerce").astype(float).to_numpy()

    curr_kcm = float(kc_mids[-1])
    if not math.isfinite(curr_kcm) or curr_kcm <= 0:
        return False, "FLIP_WATCH_KC_MIDDLE_INVALID", {}

    for b in range(0, m_len - 2):
        kcm_b = float(kc_mids[b])
        if not math.isfinite(kcm_b) or kcm_b <= 0:
            continue

        if norm_side == "LONG":
            # 1. 突破 KC_middle
            if not (highs[b] > kcm_b and closes[b] >= kcm_b * (1.0 - buf)):
                continue
            # 2. b+1 .. M-2 至少出現回測動作，且回測期間 [b+1 .. M-1] 皆不跌破 KC_middle 或 MA15
            if not (float(lows[b + 1 : m_len - 1].min()) < float(highs[b])):
                continue
            retest_held = True
            for r in range(b + 1, m_len):
                candidates = [v for v in (kc_mids[r], ma15s[r]) if math.isfinite(v) and v > 0]
                if not candidates:
                    retest_held = False
                    break
                support_r = min(candidates) * (1.0 - buf)
                if lows[r] < support_r or (r < m_len - 1 and closes[r] < support_r):
                    retest_held = False
                    break
            if not retest_held:
                continue
            # 3. 底底高 Higher Low：回測低點高於突破前波段低點
            pre_start = max(0, exit_idx - 3)
            pre_low_slice = all_lows[pre_start : exit_idx + b + 1]
            pre_low = float(pre_low_slice.min()) if len(pre_low_slice) else float("nan")
            eff_anchor = pre_low
            if anchor_low is not None and math.isfinite(anchor_low) and anchor_low > 0:
                eff_anchor = min(pre_low, float(anchor_low)) if math.isfinite(pre_low) else float(anchor_low)
            retest_low = float(lows[b + 1 : m_len].min())
            if not (math.isfinite(eff_anchor) and retest_low > eff_anchor):
                continue
            # 4. 最新價格站穩 KC 中軌並突破前一波反彈小高點
            prior_bounce_high = float(highs[b : m_len - 1].max())
            if live_price > prior_bounce_high and live_price > curr_kcm:
                return True, "FLIP_WATCH_STRUCTURE_CONFIRMED_LONG", {
                    "breakout_offset": b,
                    "retest_bars": (m_len - 1) - (b + 1),
                    "anchor_low": eff_anchor,
                    "retest_low": retest_low,
                    "prior_bounce_high": prior_bounce_high,
                    "curr_kc_middle": curr_kcm,
                }

        elif norm_side == "SHORT":
            # 1. 跌破 KC_middle
            if not (lows[b] < kcm_b and closes[b] <= kcm_b * (1.0 + buf)):
                continue
            # 2. b+1 .. M-2 至少出現反抽動作，且反抽期間皆不突破 KC_middle 或 MA15
            if not (float(highs[b + 1 : m_len - 1].max()) > float(lows[b])):
                continue
            retest_held = True
            for r in range(b + 1, m_len):
                candidates = [v for v in (kc_mids[r], ma15s[r]) if math.isfinite(v) and v > 0]
                if not candidates:
                    retest_held = False
                    break
                resist_r = max(candidates) * (1.0 + buf)
                if highs[r] > resist_r or (r < m_len - 1 and closes[r] > resist_r):
                    retest_held = False
                    break
            if not retest_held:
                continue
            # 3. 頂頂低 Lower High：反抽高點低於跌破前波段高點
            pre_start = max(0, exit_idx - 3)
            pre_high_slice = all_highs[pre_start : exit_idx + b + 1]
            pre_high = float(pre_high_slice.max()) if len(pre_high_slice) else float("nan")
            eff_anchor = pre_high
            if anchor_high is not None and math.isfinite(anchor_high) and anchor_high > 0:
                eff_anchor = max(pre_high, float(anchor_high)) if math.isfinite(pre_high) else float(anchor_high)
            retest_high = float(highs[b + 1 : m_len].max())
            if not (math.isfinite(eff_anchor) and retest_high < eff_anchor):
                continue
            # 4. 最新價格壓制於 KC 中軌下並跌破前一波回落小低點
            prior_drop_low = float(lows[b : m_len - 1].min())
            if live_price < prior_drop_low and live_price < curr_kcm:
                return True, "FLIP_WATCH_STRUCTURE_CONFIRMED_SHORT", {
                    "breakout_offset": b,
                    "retest_bars": (m_len - 1) - (b + 1),
                    "anchor_high": eff_anchor,
                    "retest_high": retest_high,
                    "prior_drop_low": prior_drop_low,
                    "curr_kc_middle": curr_kcm,
                }

    return False, f"FLIP_WATCH_WAITING_RETEST_{norm_side}", {
        "bars_since_exit": m_len,
        "curr_kc_middle": curr_kcm,
    }


def _evaluate_kc_channel_expansion(
    working: pd.DataFrame,
    *,
    lookback: int = 2,
    expansion_ratio: float = 1.0,
) -> Tuple[bool, str, Dict[str, Any]]:
    """條件(c)：確認 KC 上下軌寬度處於開口擴張狀態（大於前 N 根 K 線平均寬度）。"""
    lb = max(1, int(lookback))
    if not {"kc_upper", "kc_middle", "kc_lower"}.issubset(working.columns) or len(working) < lb + 1:
        return False, "FLIP_WATCH_KC_EXPANSION_INSUFFICIENT_BARS", {}

    kc_u = pd.to_numeric(working["kc_upper"], errors="coerce").astype(float)
    kc_m = pd.to_numeric(working["kc_middle"], errors="coerce").astype(float)
    kc_l = pd.to_numeric(working["kc_lower"], errors="coerce").astype(float)
    width_pct = (kc_u - kc_l) / kc_m.where(kc_m > 0)
    tail_widths = width_pct.iloc[-(lb + 1):]
    if tail_widths.isna().any() or (tail_widths <= 0).any():
        return False, "FLIP_WATCH_KC_EXPANSION_INVALID", {}

    curr_width = float(tail_widths.iloc[-1])
    prev_avg = float(tail_widths.iloc[:-1].mean())
    required_floor = prev_avg * float(expansion_ratio)
    details = {
        "curr_kc_width_pct": curr_width,
        "prev_avg_kc_width_pct": prev_avg,
        "required_kc_width_pct": required_floor,
        "lookback": lb,
        "expansion_ratio": float(expansion_ratio),
    }
    if curr_width > required_floor:
        return True, "FLIP_WATCH_KC_EXPANDING", details
    return False, "FLIP_WATCH_KC_NOT_EXPANDING", details


def create_flip_watch_state(
    df: pd.DataFrame,
    *,
    symbol: str,
    closed_side: str,
    origin_exit_reason: str,
    config: Optional[PeakValleyExitConfig] = None,
) -> Optional[Dict[str, Any]]:
    """平倉後建立反手觀察期狀態（不立即開反向單，等待 N 根 1M K 線內結構二次確認）。"""
    cfg = resolve_peak_valley_exit_config(symbol, config)
    norm_side = str(closed_side or "").upper()
    watch_side = "LONG" if norm_side == "SHORT" else ("SHORT" if norm_side == "LONG" else None)
    if watch_side is None or origin_exit_reason not in PV_FLIP_ELIGIBLE_EXIT_REASONS or not cfg.enable_conditional_flip:
        return None
    exit_bar_id = extract_bar_id(df)
    if exit_bar_id is None:
        return None
    anchor_low: Optional[float] = None
    anchor_high: Optional[float] = None
    if isinstance(df, pd.DataFrame) and not df.empty:
        tail_df = df.tail(6)
        if "low" in tail_df.columns:
            l_val = _safe_float(pd.to_numeric(tail_df["low"], errors="coerce").min())
            if math.isfinite(l_val) and l_val > 0:
                anchor_low = l_val
        if "high" in tail_df.columns:
            h_val = _safe_float(pd.to_numeric(tail_df["high"], errors="coerce").max())
            if math.isfinite(h_val) and h_val > 0:
                anchor_high = h_val
    return {
        "symbol": symbol,
        "flip_watch_side": watch_side,
        "closed_side": norm_side,
        "origin_exit_reason": origin_exit_reason,
        "exit_bar_id": int(exit_bar_id),
        "exit_timestamp": time.time(),
        "window_bars": max(1, int(cfg.flip_watch_window_bars)),
        "anchor_low": anchor_low,
        "anchor_high": anchor_high,
    }


def evaluate_flip_watch_entry(
    df_1m: pd.DataFrame,
    *,
    watch_state: Optional[Mapping[str, Any]],
    live_price: float,
    df_5m: Optional[pd.DataFrame] = None,
    symbol: Optional[str] = None,
    config: Optional[PeakValleyExitConfig] = None,
) -> Dict[str, Any]:
    """評估觀察期內是否同時滿足：(a) 站穩中軸回踩底底高/頂頂低、(b) 5M 趨勢不逆勢、(c) 通道擴張非橫盤。"""
    resolved_symbol = symbol or (watch_state.get("symbol") if isinstance(watch_state, Mapping) else None)
    cfg = resolve_peak_valley_exit_config(
        str(resolved_symbol) if resolved_symbol else None,
        config,
    )

    def _hold(
        reason: str,
        *,
        expired: bool = False,
        elapsed_bars: int = 0,
        checks: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "action": "EXPIRED" if expired else "HOLD",
            "should_enter": False,
            "expired": expired,
            "side": str((watch_state or {}).get("flip_watch_side") or "").upper() or None,
            "reason": reason,
            "elapsed_bars": elapsed_bars,
            "window_bars": int((watch_state or {}).get("window_bars") or cfg.flip_watch_window_bars),
            "current_bar_id": extract_bar_id(df_1m) if isinstance(df_1m, pd.DataFrame) else None,
            "checks": checks or {},
        }

    if not isinstance(watch_state, Mapping):
        return _hold("FLIP_WATCH_NONE", expired=True)
    watch_side = str(watch_state.get("flip_watch_side") or "").upper()
    origin_reason = str(watch_state.get("origin_exit_reason") or "")
    exit_bar_id = _norm_bar_ms(watch_state.get("exit_bar_id"))
    if watch_side not in ("LONG", "SHORT") or origin_reason not in PV_FLIP_ELIGIBLE_EXIT_REASONS or exit_bar_id is None:
        return _hold("FLIP_WATCH_INVALID_STATE", expired=True)

    price = _safe_float(live_price)
    if not isinstance(df_1m, pd.DataFrame) or len(df_1m) < 3 or not math.isfinite(price) or price <= 0:
        return _hold("FLIP_WATCH_INVALID_MARKET_DATA")

    working = _ensure_working_indicators(build_live_flip_frame(df_1m, price))
    current_bar_id = extract_bar_id(working)
    if current_bar_id is None:
        return _hold("FLIP_WATCH_BAR_ID_MISSING")

    window_bars = max(1, int(watch_state.get("window_bars") or cfg.flip_watch_window_bars))
    elapsed_bars = int(round((current_bar_id - exit_bar_id) / 60_000.0))
    if elapsed_bars < 0:
        return _hold("FLIP_WATCH_CLOCK_SKEW_EXPIRED", expired=True, elapsed_bars=elapsed_bars)
    if elapsed_bars > window_bars:
        return _hold("FLIP_WATCH_EXPIRED_TTL", expired=True, elapsed_bars=elapsed_bars)
    if elapsed_bars == 0:
        return _hold("FLIP_WATCH_WAITING_NEXT_BAR", elapsed_bars=elapsed_bars)

    # 定位平倉 K 棒索引
    exit_idx = max(0, len(working) - 1 - elapsed_bars)
    ts_col = "timestamp" if "timestamp" in working.columns else ("time" if "time" in working.columns else None)
    if ts_col is not None:
        ts_series = pd.to_numeric(working[ts_col], errors="coerce").astype(float)
        ts_ms = ts_series.where(ts_series >= 1e11, ts_series * 1000.0)
        matches = working.index[ts_ms >= float(exit_bar_id)]
        if len(matches) > 0:
            pos_loc = working.index.get_loc(matches[0])
            if isinstance(pos_loc, (int, np.integer)):
                exit_idx = int(pos_loc)

    closed = _latest_closed_bars(working)
    if closed.empty:
        closed = working
    curr_ma5, prev_ma5, _ = _compute_live_ma5_triplet(working, closed, price)
    curr_ma15, curr_adx = _compute_live_ma15_and_adx(working, closed, price)
    kc_u, kc_m, kc_l = _resolve_kc_bands(working, closed)

    checks: Dict[str, Any] = {
        "elapsed_bars": elapsed_bars,
        "window_bars": window_bars,
        "exit_bar_id": exit_bar_id,
        "current_bar_id": current_bar_id,
    }

    # ── 條件 (c)：專屬低門檻非盤整 + KC 通道開口擴張 ──
    strict_flip_kc_floor = max(float(cfg.flip_min_kc_width_pct), float(cfg.normal_min_kc_width_pct))
    flip_filter_cfg = PeakValleyEntryConfig(
        min_kc_width_pct=strict_flip_kc_floor,
        min_ma_spread_pct=float(cfg.flip_min_ma_spread_pct),
        enable_adx_filter=bool(cfg.flip_enable_adx_filter),
        min_adx=float(cfg.flip_min_adx),
    )
    is_cons, cons_reason, cons_metrics = evaluate_peak_valley_consolidation(
        kc_upper=kc_u,
        kc_middle=kc_m,
        kc_lower=kc_l,
        ma5=curr_ma5,
        ma15=curr_ma15,
        adx=curr_adx,
        config=flip_filter_cfg,
    )
    checks["consolidation"] = cons_metrics
    if is_cons:
        return _hold(f"FLIP_WATCH_BLOCKED_CONSOLIDATION:{cons_reason}", elapsed_bars=elapsed_bars, checks=checks)

    exp_ok, exp_reason, exp_details = _evaluate_kc_channel_expansion(
        working,
        lookback=cfg.flip_watch_expansion_lookback,
        expansion_ratio=cfg.flip_watch_expansion_ratio,
    )
    checks["channel_expansion"] = exp_details
    if not exp_ok:
        return _hold(exp_reason, elapsed_bars=elapsed_bars, checks=checks)

    # ── 條件 (b)：5M 多週期趨勢濾網不逆勢 ──
    htf_ok, htf_reason, htf_details = evaluate_5m_trend_filter(
        df_5m,
        df_1m=working,
        target_side=watch_side,
        live_price=price,
        config=cfg,
    )
    checks["trend_5m"] = htf_details
    if not htf_ok:
        return _hold(htf_reason, elapsed_bars=elapsed_bars, checks=checks)

    # ── 條件 (a)：站穩 KC_middle 與回踩確認（底底高 / 頂頂低）──
    if cfg.flip_watch_require_retest:
        struct_ok, struct_reason, struct_details = _evaluate_kc_mid_retest_structure(
            working,
            target_side=watch_side,
            exit_idx=exit_idx,
            anchor_low=_safe_float(watch_state.get("anchor_low")) if watch_state.get("anchor_low") is not None else None,
            anchor_high=_safe_float(watch_state.get("anchor_high")) if watch_state.get("anchor_high") is not None else None,
            live_price=price,
            buffer_pct=cfg.flip_watch_retest_buffer_pct,
        )
        checks["structure_retest"] = struct_details
        if not struct_ok:
            return _hold(struct_reason, elapsed_bars=elapsed_bars, checks=checks)

    return {
        "action": f"OPEN_{watch_side}",
        "should_enter": True,
        "expired": False,
        "side": watch_side,
        "reason": f"FLIP_WATCH_CONFIRMED_{watch_side}",
        "elapsed_bars": elapsed_bars,
        "window_bars": window_bars,
        "current_bar_id": current_bar_id,
        "checks": checks,
    }


def verify_pv_flip_entry_authority(
    frame: Any,
    *,
    side: str,
    live_price: float,
    context: Optional[Mapping[str, Any]] = None,
    symbol: Optional[str] = None,
    config: Optional[PeakValleyExitConfig] = None,
    df_5m: Optional[pd.DataFrame] = None,
) -> bool:
    """獨立複驗觀察期二次確認 / 條件式反手數學權限，供執行邊界閘門使用。"""
    if not context or context.get("is_pv_flip") is not True:
        return False
    resolved_symbol = symbol or context.get("symbol")
    if not resolved_symbol and isinstance(getattr(frame, "attrs", None), dict):
        resolved_symbol = frame.attrs.get("symbol")
    config = resolve_peak_valley_exit_config(
        str(resolved_symbol) if resolved_symbol else None,
        config,
    )
    norm_side = str(side or "").upper()
    if norm_side not in ("LONG", "SHORT"):
        return False
    if not isinstance(frame, pd.DataFrame) or len(frame) < 3:
        return False
    price = _safe_float(live_price)
    if not math.isfinite(price) or price <= 0:
        return False

    watch_state = context.get("pv_flip_watch_state")
    if watch_state is None and isinstance(getattr(frame, "attrs", None), dict):
        watch_state = frame.attrs.get("pv_flip_watch_state")
    resolved_5m = df_5m if df_5m is not None else context.get("frame_5m")
    if resolved_5m is None and isinstance(getattr(frame, "attrs", None), dict):
        resolved_5m = frame.attrs.get("frame_5m")
    resolved_5m = unwrap_frame_ref(resolved_5m)

    if isinstance(watch_state, Mapping):
        watch_eval = evaluate_flip_watch_entry(
            frame,
            watch_state=watch_state,
            live_price=price,
            df_5m=resolved_5m,
            symbol=str(resolved_symbol) if resolved_symbol else None,
            config=config,
        )
        return bool(watch_eval.get("should_enter") and watch_eval.get("side") == norm_side)

    if config.enable_flip_watch:
        return False

    origin_reason = str(context.get("pv_flip_origin_exit_reason") or "")
    if origin_reason not in PV_FLIP_ELIGIBLE_EXIT_REASONS:
        return False
    if origin_reason.endswith("_LONG") and norm_side != "SHORT":
        return False
    if origin_reason.endswith("_SHORT") and norm_side != "LONG":
        return False

    working = build_live_flip_frame(frame, price)
    closed = _latest_closed_bars(working)
    if closed.empty:
        closed = working
    curr_ma5, prev_ma5, _prev2_ma5 = _compute_live_ma5_triplet(working, closed, price)
    curr_ma15, curr_adx = _compute_live_ma15_and_adx(working, closed, price)
    kc_u, kc_m, kc_l = _resolve_kc_bands(working, closed)

    eligible, flip_side, _flip_reason, _ = evaluate_flip_eligibility(
        closed_side="LONG" if norm_side == "SHORT" else "SHORT",
        exit_reason=origin_reason,
        current_bar_id=None,
        last_flip_bar_id=None,
        kc_upper=kc_u,
        kc_middle=kc_m,
        kc_lower=kc_l,
        curr_ma5=curr_ma5,
        curr_ma15=curr_ma15,
        curr_adx=curr_adx,
        config=config,
    )
    if not eligible or flip_side != norm_side:
        return False

    # 同時複驗反向動能條件確實存在（不接受偽造旗標）
    if origin_reason in (
        "ACTIVE_EXIT_PV_REVERSE_INFLECTION_LONG",
        "ACTIVE_EXIT_PV_REVERSE_INFLECTION_SHORT",
        "ACTIVE_EXIT_KC_MID_BUFFER_LONG",
        "ACTIVE_EXIT_KC_MID_BUFFER_SHORT",
        "ACTIVE_EXIT_KC_TREND_REVERSAL_LONG",
        "ACTIVE_EXIT_KC_TREND_REVERSAL_SHORT",
    ):
        if not (math.isfinite(curr_ma5) and math.isfinite(prev_ma5)):
            return False
        if norm_side == "SHORT":
            return (curr_ma5 - prev_ma5) < -config.ma5_slope_eps and ((not config.require_tick_cross_ma5) or price < curr_ma5)
        return (curr_ma5 - prev_ma5) > config.ma5_slope_eps and ((not config.require_tick_cross_ma5) or price > curr_ma5)

    # KC 極值反轉止盈觸發時，需確認前高/前低破位或最近 K 線觸及極值
    prev_closed = closed.iloc[-2] if len(closed) >= 2 else closed.iloc[-1]
    if norm_side == "SHORT":
        recent_high = _safe_float(working["high"].tail(3).max()) if "high" in working.columns else price
        touched = math.isfinite(kc_u) and recent_high >= kc_u
        prev_low = _safe_float(prev_closed.get("low"))
        return bool(touched and ((math.isfinite(prev_low) and price < prev_low) or price < curr_ma5))
    recent_low = _safe_float(working["low"].tail(3).min()) if "low" in working.columns else price
    touched = math.isfinite(kc_l) and recent_low <= kc_l
    prev_high = _safe_float(prev_closed.get("high"))
    return bool(touched and ((math.isfinite(prev_high) and price > prev_high) or price > curr_ma5))


async def _query_exchange_symbol_position_amt(account: Any, symbol: str) -> Optional[float]:
    """若帳戶連接實盤/測試網或 CCXT 交易所實例，主動查詢確認該幣種持倉淨口數。"""
    exchange = getattr(account, "exchange", None)
    if exchange is None:
        return None

    clean_fn = getattr(account, "_clean_symbol", None)
    target_clean = clean_fn(symbol) if callable(clean_fn) else str(symbol).replace(":USDT", "").replace("/", "")

    # 1. 優先使用 Binance USDM positionRisk（與 BinanceTestnetAccount 同步機制一致）
    pos_risk_fn = getattr(exchange, "fapiPrivateV2GetPositionRisk", None)
    if callable(pos_risk_fn):
        res = pos_risk_fn()
        if inspect.isawaitable(res):
            res = await res
        if isinstance(res, list):
            for row in res:
                sym = str(row.get("symbol") or "")
                norm = clean_fn(sym) if callable(clean_fn) else sym.replace("/", "")
                if norm == target_clean or sym == symbol:
                    amt = _safe_float(row.get("positionAmt"), default=0.0)
                    return float(amt)
            return 0.0

    # 2. 次選 CCXT 標準 fetch_positions（支援 Binance / Gate.io 永續合約）
    fetch_pos_fn = getattr(exchange, "fetch_positions", None)
    if callable(fetch_pos_fn):
        try:
            res = fetch_pos_fn([symbol])
        except TypeError:
            res = fetch_pos_fn()
        if inspect.isawaitable(res):
            res = await res
        if isinstance(res, list):
            for row in res:
                sym = str(row.get("symbol") or "")
                norm = clean_fn(sym) if callable(clean_fn) else sym.replace(":USDT", "").replace("/", "")
                if norm == target_clean or sym == symbol:
                    raw_amt = row.get("contracts")
                    if raw_amt is None:
                        raw_amt = (row.get("info") or {}).get("positionAmt") or (row.get("info") or {}).get("size") or 0.0
                    return float(_safe_float(raw_amt, default=0.0))
            return 0.0

    return None


async def verify_exchange_position_zero_and_settled(
    account: Any,
    symbol: str,
    *,
    timeout_sec: float = 3.0,
    poll_interval_sec: float = 0.15,
) -> Tuple[bool, str, Dict[str, Any]]:
    """步驟二安全核驗：確認交易所/帳戶持倉已完全歸零 (position == 0) 且損益已結算。"""
    deadline = time.monotonic() + max(0.2, float(timeout_sec))
    last_amt: Optional[float] = None

    while True:
        local_positions = getattr(account, "positions", {}) or {}
        closing_lock = getattr(account, "closing_lock", set()) or set()
        local_zero = (symbol not in local_positions) and (symbol not in closing_lock)

        # 若為遠端交易所帳戶，先嘗試 refresh(force=True) 或直接查詢遠端倉位是否完全歸零
        exchange_amt: Optional[float] = None
        exchange_checked = False
        if getattr(account, "exchange", None) is not None:
            try:
                exchange_amt = await _query_exchange_symbol_position_amt(account, symbol)
                if exchange_amt is not None:
                    exchange_checked = True
                    last_amt = exchange_amt
            except Exception as exc:
                logger.warning("⚠️ [PV_FLIP_VERIFY_ZERO] %s 查詢交易所持倉異常: %s", symbol, exc)
                exchange_amt = float("nan")

        if exchange_checked:
            remote_zero = math.isfinite(float(exchange_amt)) and abs(float(exchange_amt)) <= 1e-12
        else:
            # 純本地模擬帳戶無遠端 exchange，以本地 positions 移除且 closing_lock 解除確認歸零
            remote_zero = True

        # 確認損益紀錄已更新（trades 或 last_closed_trades 有本標的平倉紀錄）
        trades = getattr(account, "trades", None)
        last_closed_trades = getattr(account, "last_closed_trades", None)
        has_closed_trade_record = bool(
            (isinstance(last_closed_trades, Mapping) and symbol in last_closed_trades)
            or (
                isinstance(trades, list)
                and any(
                    str(t.get("symbol")) == symbol and str(t.get("action", "")).startswith("CLOSE_")
                    for t in trades[:10]
                )
            )
            or getattr(account, "last_closed_at", {}).get(symbol)
        )

        if local_zero and remote_zero and has_closed_trade_record:
            return True, "POSITION_ZERO_AND_SETTLED_CONFIRMED", {
                "local_zero": True,
                "remote_zero": True,
                "exchange_checked": exchange_checked,
                "position_amt": last_amt if exchange_checked else 0.0,
                "realized_pnl": getattr(account, "realized_pnl", None),
            }

        if time.monotonic() >= deadline:
            return False, "POSITION_NOT_ZERO_OR_UNSETTLED", {
                "local_zero": local_zero,
                "remote_zero": remote_zero,
                "exchange_checked": exchange_checked,
                "position_amt": last_amt,
                "has_closed_trade_record": has_closed_trade_record,
            }

        refresh_fn = getattr(account, "refresh", None)
        if callable(refresh_fn) and getattr(account, "exchange", None) is not None:
            try:
                r = refresh_fn(force=True)
                if inspect.isawaitable(r):
                    await r
            except Exception:
                pass
        await asyncio.sleep(poll_interval_sec)


async def execute_peak_valley_exit_and_conditional_flip(
    *,
    account: Any,
    engine_or_account: Any,
    symbol: str,
    df: pd.DataFrame,
    live_price: float,
    pv_exit: Mapping[str, Any],
    closed_side: str,
    close_fn: Callable[..., Awaitable[bool]],
    open_fn: Callable[..., Awaitable[bool]],
    flip_bar_registry: Optional[Dict[str, Dict[str, Any]]] = None,
    exit_record_registry: Optional[Dict[str, Dict[str, Any]]] = None,
    flip_watch_registry: Optional[Dict[str, Dict[str, Any]]] = None,
    account_key: Optional[str] = None,
    macro_trend: str = "NEUTRAL",
    atr: Optional[float] = None,
    config: Optional[PeakValleyExitConfig] = None,
) -> Dict[str, Any]:
    """嚴格依序執行：「送出市價平倉單 -> 確認歸零並結算 -> 建立觀察期狀態（徹底脫鉤）或相容立即反手」。"""
    config = resolve_peak_valley_exit_config(symbol, config)
    pv_reason = str(pv_exit.get("reason") or "")
    pv_is_closed = pv_exit.get("trigger_level") == "BAR_CLOSE"
    current_bar_id = pv_exit.get("current_bar_id") or extract_bar_id(df)
    acc_key = account_key or f"{id(account)}:{symbol}"

    # ── 步驟一：送出市價平倉單（強制 is_limit=False 避免限價懸單影響反手） ──
    close_success = await close_fn(
        account=account,
        symbol=symbol,
        price=float(live_price),
        reason=pv_reason,
        meta={
            "raw_df": df,
            "is_bar_closed": pv_is_closed,
            "is_limit": False,
            "force_market_close": True,
            "pv_exit_metrics": pv_exit.get("metrics") or {},
        },
    )
    if not close_success:
        return {
            "status": "HOLD",
            "symbol": symbol,
            "reason": "EXIT_BLOCKED_HOLD:" + pv_reason,
            "trigger_level": pv_exit.get("trigger_level"),
            "anti_whipsaw": pv_exit.get("anti_whipsaw"),
            "should_flip": False,
            "flipped": False,
            "flip_reason": "CLOSE_ORDER_FAILED",
            "success": False,
        }

    # ── 步驟二：確認倉位完全歸零 (position == 0) 且該筆已實現損益完成結算紀錄 ──
    zero_ok, zero_reason, zero_details = await verify_exchange_position_zero_and_settled(
        account, symbol, timeout_sec=3.0,
    )

    if exit_record_registry is not None:
        exit_record_registry[acc_key] = {
            "exit_timestamp": time.time(),
            "exit_bar_id": current_bar_id,
            "closed_side": str(closed_side).upper(),
            "reason": pv_reason,
            "trigger_level": pv_exit.get("trigger_level"),
            "position_zero_confirmed": zero_ok,
        }

    if config.enable_flip_watch:
        # 徹底脫鉤：平倉成功後不直接送出反向開倉單，僅合條件平倉進入「反手觀察期」等待 N 根 K 線結構確認
        enter_watch = bool(pv_exit.get("enter_flip_watch"))
        watch_side = pv_exit.get("flip_watch_side")
        watch_state: Optional[Dict[str, Any]] = None
        if enter_watch and watch_side in ("LONG", "SHORT") and zero_ok:
            watch_state = create_flip_watch_state(
                df,
                symbol=symbol,
                closed_side=closed_side,
                origin_exit_reason=pv_reason,
                config=config,
            )
            if watch_state is not None and flip_watch_registry is not None:
                flip_watch_registry[acc_key] = watch_state
            logger.info(
                "👁️ [PV_FLIP_WATCH_ENTERED] %s closed=%s reason=%s -> watch=%s window=%s bars",
                symbol, closed_side, pv_reason, watch_side, config.flip_watch_window_bars,
            )
        else:
            if flip_watch_registry is not None:
                flip_watch_registry.pop(acc_key, None)
            logger.info(
                "🏁 [PV_EXIT_NO_FLIP_WATCH] %s closed=%s reason=%s zero_ok=%s decision=%s",
                symbol, closed_side, pv_reason, zero_ok, pv_exit.get("flip_reason"),
            )
        return {
            "status": "EXIT_EXECUTED",
            "symbol": symbol,
            "reason": pv_reason,
            "trigger_level": pv_exit.get("trigger_level"),
            "anti_whipsaw": pv_exit.get("anti_whipsaw"),
            "position_zero_confirmed": zero_ok,
            "position_zero_details": zero_details,
            "should_flip": False,
            "flipped": False,
            "flip_side": None,
            "flip_reason": pv_exit.get("flip_reason"),
            "enter_flip_watch": bool(watch_state is not None),
            "flip_watch_side": watch_side if watch_state is not None else None,
            "flip_watch_window_bars": int(config.flip_watch_window_bars) if watch_state is not None else 0,
            "flip_watch_state": watch_state,
            "success": True,
        }

    want_flip = bool(pv_exit.get("should_flip"))
    flip_side = pv_exit.get("flip_side")
    flip_reason = str(pv_exit.get("flip_reason") or "")

    # 單根 K 線防重複刷單鎖複查（避免極端併發同一根 1M K 棒送出兩次反手）
    if want_flip and flip_bar_registry is not None and current_bar_id is not None:
        prior_flip_bar = (flip_bar_registry.get(acc_key) or {}).get("flip_bar_id")
        if prior_flip_bar is not None and int(prior_flip_bar) == int(current_bar_id):
            want_flip = False
            flip_reason = "FLIP_BLOCKED_SAME_BAR_DUPLICATE_LOCK"

    if not want_flip or flip_side not in ("LONG", "SHORT"):
        logger.info(
            "🏁 [PV_EXIT_NO_FLIP] %s closed=%s reason=%s zero_ok=%s flip_decision=%s",
            symbol, closed_side, pv_reason, zero_ok, flip_reason,
        )
        return {
            "status": "EXIT_EXECUTED",
            "symbol": symbol,
            "reason": pv_reason,
            "trigger_level": pv_exit.get("trigger_level"),
            "anti_whipsaw": pv_exit.get("anti_whipsaw"),
            "position_zero_confirmed": zero_ok,
            "position_zero_details": zero_details,
            "should_flip": False,
            "flipped": False,
            "flip_side": None,
            "flip_reason": flip_reason,
            "success": True,
        }

    if not zero_ok:
        logger.critical(
            "🛑 [PV_FLIP_ABORTED_POSITION_NOT_ZERO] %s 平倉後未確認歸零 (%s)，基於單向持倉安全嚴禁送出反手單！details=%s",
            symbol, zero_reason, zero_details,
        )
        return {
            "status": "EXIT_EXECUTED",
            "symbol": symbol,
            "reason": pv_reason,
            "trigger_level": pv_exit.get("trigger_level"),
            "position_zero_confirmed": False,
            "position_zero_details": zero_details,
            "should_flip": True,
            "flipped": False,
            "flip_side": None,
            "flip_reason": f"FLIP_ABORTED_{zero_reason}",
            "success": True,
        }

    # ── 步驟三：已確認原倉歸零且通過嚴格反手濾網，立即送出反向開倉市價單 ──
    # 先鎖定本根 1M K 線反手額度，確保同一根 K 線最多只會執行一次「平倉 -> 反手」
    if flip_bar_registry is not None and current_bar_id is not None:
        flip_bar_registry[acc_key] = {
            "flip_bar_id": int(current_bar_id),
            "flip_side": flip_side,
            "origin_exit_reason": pv_reason,
            "timestamp": time.time(),
        }

    live_flip_df = build_live_flip_frame(df, float(live_price))
    flip_struct = extract_entry_structure_context(live_flip_df, flip_side, config=config)
    resolved_atr = _safe_float(atr)
    if not math.isfinite(resolved_atr) or resolved_atr <= 0:
        resolved_atr = _safe_float(live_flip_df.iloc[-1].get("atr"), default=float(live_price) * 0.015)

    flip_meta: Dict[str, Any] = {
        "side": flip_side,
        "direction_override": True,
        "is_agile_trigger": True,
        "entry_type": f"PEAK_VALLEY_FLIP_{flip_side}",
        "candidate_signal_type": f"PEAK_VALLEY_FLIP_{flip_side}",
        "is_peak_valley_inflection": True,
        "is_flip": True,
        "is_pv_flip": True,
        "pv_flip_bar_id": current_bar_id,
        "pv_flip_origin_exit_reason": pv_reason,
        "entry_swing_low": flip_struct.get("entry_swing_low"),
        "entry_swing_high": flip_struct.get("entry_swing_high"),
        "entry_kc_width_pct": flip_struct.get("entry_kc_width_pct"),
        "entry_pattern": "REVERSAL",
        "is_swing_pivot": False,
        "is_cross_mid": True,
        "no_rigid_sl": True,
        "stop_loss_price": None,
        "upstream_entry_gate_authorized": True,
        "intrabar_authorized": not bool(live_flip_df.iloc[-1].get("is_closed", True)),
        "allow_kc_internal": True,
        "strategy_mode": "AGILE_MOMENTUM",
        "raw_df": live_flip_df,
        "macro_trend": macro_trend,
        "source": f"UNIFIED_PIPELINE|PEAK_VALLEY_FLIP_{flip_side}",
        "atr": resolved_atr,
    }
    if hasattr(live_flip_df, "attrs"):
        live_flip_df.attrs["symbol"] = symbol
        for k in (
            "direction_override",
            "is_agile_trigger",
            "is_cross_mid",
            "entry_type",
            "strategy_mode",
            "allow_kc_internal",
            "is_peak_valley_inflection",
            "is_pv_flip",
        ):
            live_flip_df.attrs[k] = flip_meta.get(k, True)

    flip_opened = await open_fn(
        engine_or_account=engine_or_account,
        symbol=symbol,
        side=flip_side,
        price=float(live_price),
        reason=f"PV_CONDITIONAL_FLIP:{flip_reason}",
        meta=flip_meta,
    )

    if flip_opened:
        # 雙重寫入反手 K 棒鎖至倉位與 metadata，確保本根 1M K 線結束前鎖死再次平倉與二次反手
        positions_dict = getattr(account, "positions", None)
        if isinstance(positions_dict, dict) and symbol in positions_dict and isinstance(positions_dict[symbol], dict):
            positions_dict[symbol]["pv_flip_bar_id"] = current_bar_id
            positions_dict[symbol]["is_pv_flip"] = True
            positions_dict[symbol]["pv_flip_origin_exit_reason"] = pv_reason
        pos_meta_dict = getattr(account, "position_meta", None)
        if isinstance(pos_meta_dict, dict) and symbol in pos_meta_dict and isinstance(pos_meta_dict[symbol], dict):
            pos_meta_dict[symbol]["pv_flip_bar_id"] = current_bar_id
            pos_meta_dict[symbol]["is_pv_flip"] = True
            pos_meta_dict[symbol]["pv_flip_origin_exit_reason"] = pv_reason

        logger.info(
            "🔄 [PV_CONDITIONAL_FLIP_EXECUTED] %s %s -> %s @ %s bar_id=%s exit_reason=%s (本根K棒已鎖定防雙巴)",
            symbol, closed_side, flip_side, live_price, current_bar_id, pv_reason,
        )
    else:
        logger.warning(
            "⚠️ [PV_CONDITIONAL_FLIP_VETOED] %s 平倉已確認歸零但反手單 (%s) 未開出，維持空倉觀望",
            symbol, flip_side,
        )

    return {
        "status": "EXIT_AND_FLIP_EXECUTED" if flip_opened else "EXIT_EXECUTED",
        "symbol": symbol,
        "reason": pv_reason,
        "trigger_level": pv_exit.get("trigger_level"),
        "anti_whipsaw": pv_exit.get("anti_whipsaw"),
        "position_zero_confirmed": True,
        "position_zero_details": zero_details,
        "should_flip": True,
        "flipped": bool(flip_opened),
        "flip_side": flip_side if flip_opened else None,
        "flip_reason": flip_reason,
        "flip_metrics": pv_exit.get("flip_metrics") or {},
        "locked_flip_bar_id": current_bar_id if flip_opened else None,
        "success": True,
    }


__all__ = [
    "ACTIVE_EXIT_KC_PREFIX",
    "ACTIVE_EXIT_PV_PREFIX",
    "DEFAULT_PEAK_VALLEY_EXIT_CONFIG",
    "EXIT_BUFFER_ATR_MULT",
    "HARD_SL_ATR_MULT",
    "LOBSTER_PEAK_VALLEY_EXIT_CONFIG",
    "PEPE_PEAK_VALLEY_EXIT_CONFIG",
    "PV_FLIP_ELIGIBLE_EXIT_REASONS",
    "PV_FLIP_FORBIDDEN_EXIT_REASONS",
    "PeakValleyExitConfig",
    "ROUND_TRIP_FEE_PCT",
    "SafeFrameRef",
    "TRAILING_DIST_ATR",
    "TRAILING_START_ATR",
    "build_live_flip_frame",
    "can_reenter_after_pv_exit",
    "create_flip_watch_state",
    "evaluate_5m_trend_filter",
    "evaluate_flip_eligibility",
    "evaluate_flip_watch_entry",
    "evaluate_kc_layered_exit",
    "evaluate_peak_valley_exit",
    "execute_peak_valley_exit_and_conditional_flip",
    "extract_bar_id",
    "extract_entry_structure_context",
    "has_confirmed_three_point_pivot",
    "is_peak_valley_exit_authorized_reason",
    "resample_1m_to_5m",
    "resolve_peak_valley_exit_config",
    "unwrap_frame_ref",
    "verify_exchange_position_zero_and_settled",
    "verify_pv_flip_entry_authority",
    "wrap_frame_ref",
]
