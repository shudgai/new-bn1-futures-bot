"""
Master Gate Funnel & Holding Protection Module
極度嚴謹的自動化交易系統審查專家與程式架構模組
"""

import math
import logging
import time
from typing import Dict, Any, Optional, Tuple, NamedTuple

import pandas as pd

from core.services.candle_data import closed_entry_candles
from core.services.market_consolidation_filter import (
    check_entry_signal,
    evaluate_market_blacklist,
    is_market_in_consolidation,
)

logger = logging.getLogger("MasterGateFunnel")

# Binance USDⓈ-M PRICE_FILTER tick sizes. A meme ATR of only a few ticks makes
# distance limits (MA15 bias, rail extension, exit buffers) trip on 2-3 ticks.
MEME_PRICE_TICK = {"龙虾/USDT": 0.00001, "CAP/USDT": 0.00001, "1000PEPE/USDT": 0.0000001}
MEME_DISTANCE_ATR_MIN_TICKS = 8
# Anti-chase limits (ATR multiples, symmetric LONG/SHORT), relaxed per Owner 2026-11-03.
MEME_MA15_EXTENSION_ATR = 1.0
MEME_BREAKOUT_MA15_EXTENSION_ATR = 1.0
MEME_BREAKOUT_OPEN_BEYOND_BAND_ATR = 0.25
PIVOT_KC_POS_LONG_MAX = 0.35
PIVOT_KC_POS_SHORT_MIN = 0.65
MEME_PIVOT_KC_POS_LONG_MAX = 0.75
MEME_PIVOT_KC_POS_SHORT_MIN = 0.25
PIVOT_MAX_ABSOLUTE_BIAS_ATR = 1.2

# Owner 2026-11-03: after a structure exit (KC flip / true pivot / 0.5 ATR AND-lock) the
# opposite side may open on the close bar and the next this-many 1m bars, symmetric.
REVERSE_WINDOW_BARS = 3
# Owner 2026-11-03: reverse only into a clear trend. KC middle moving < 0.05 ATR per bar is
# flat, and >= 2 MA5/MA15 crosses in the last 8 completed bars is chop; both block.
REVERSE_KC_FLAT_ATR = 0.05
REVERSE_CHOP_CROSSES = 2
REVERSE_TRIGGER = "TRIGGER_REVERSE_AFTER_STRUCTURE_EXIT"
MA_CROSS_SLOPE_TRIGGER = "TRIGGER_MA5_MA15_CROSS_WITH_TREND_SLOPE"
_STRUCTURE_EXITS: Dict[str, Tuple[str, float]] = {}


def _structure_reverse_reasons() -> frozenset:
    from core.gates.ultimate_short_exit_gatekeeper import (
        KC_FLIP_EXIT_REASONS,
        KC_MIDDLE_MA15_BREAK_REASONS,
        STRUCTURE_EXIT_REASONS,
        TRUE_OUTER_PIVOT_EXIT_REASONS,
    )
    from core.services.exits.strict_true_pivot import PROFIT_PIVOT_REASONS
    return frozenset(r.upper() for r in (
        *KC_FLIP_EXIT_REASONS.values(), *KC_MIDDLE_MA15_BREAK_REASONS.values(),
        *STRUCTURE_EXIT_REASONS.values(), *TRUE_OUTER_PIVOT_EXIT_REASONS.values(),
        *PROFIT_PIVOT_REASONS,
    ))


def _reverse_key(symbol) -> str:
    from core.symbol_aliases import to_canonical_display_symbol
    return to_canonical_display_symbol(symbol) or str(symbol)


def record_position_close(symbol, closed_side, reason, closed_at: Optional[float] = None) -> None:
    """Every successful close replaces the record; only structure exits open a reverse window."""
    try:
        key = _reverse_key(symbol)
        side = str(closed_side or "").upper()
        normalized_reason = str(reason or "").strip().upper()
        structure_reasons = _structure_reverse_reasons()
        is_structure_exit = any(
            normalized_reason == structure_reason
            or normalized_reason.endswith(f" {structure_reason}")
            for structure_reason in structure_reasons
        )
        if side in ("LONG", "SHORT") and is_structure_exit:
            _STRUCTURE_EXITS[key] = (side, float(closed_at if closed_at is not None else time.time()))
            logger.info("[REVERSE_WINDOW_OPEN] symbol=%s closed_side=%s reason=%s", key, side, reason)
        else:
            _STRUCTURE_EXITS.pop(key, None)
    except Exception as exc:  # recording must never break the close path
        logger.error("REVERSE_WINDOW_RECORD_FAILED symbol=%s error=%s", symbol, exc)


def reverse_window_side(symbol, now: Optional[float] = None) -> Optional[str]:
    record = _STRUCTURE_EXITS.get(_reverse_key(symbol))
    if not record:
        return None
    side, closed_at = record
    now = time.time() if now is None else now
    if not (closed_at <= now < math.floor(closed_at / 60) * 60 + (REVERSE_WINDOW_BARS + 1) * 60):
        return None
    return "LONG" if side == "SHORT" else "SHORT"


def meme_distance_atr(symbol, atr: float) -> float:
    """ATR used only for distance/bias limits; body triggers keep the raw ATR."""
    from core.symbol_aliases import is_meme_breakout_symbol, to_canonical_display_symbol
    if not is_meme_breakout_symbol(symbol):
        return atr
    tick = MEME_PRICE_TICK.get(to_canonical_display_symbol(symbol))
    if not tick or not math.isfinite(atr) or atr <= 0:
        return atr
    return max(atr, MEME_DISTANCE_ATR_MIN_TICKS * tick)


def pivot_kc_position_bounds(symbol) -> Tuple[float, float]:
    """Return the KC location bounds for confirmed pivot entries."""
    from core.symbol_aliases import is_meme_breakout_symbol
    if is_meme_breakout_symbol(symbol):
        return MEME_PIVOT_KC_POS_LONG_MAX, MEME_PIVOT_KC_POS_SHORT_MIN
    return PIVOT_KC_POS_LONG_MAX, PIVOT_KC_POS_SHORT_MIN


class MarketSnapshot(NamedTuple):
    symbol: str
    quote: float                # 最新報價
    open_price: float           # 當前 K 棒開盤價
    close_price: float          # 當前 K 棒最新收盤價/現價
    high_price: float           # 當前 K 棒最高價
    low_price: float            # 當前 K 棒最低價
    ma3_curr: float             # 當前 MA3
    ma3_prev: float             # 前一根 MA3
    ma5_curr: float             # 當前 MA5
    ma5_prev: float             # 前一根 MA5
    ma15_curr: float            # 當前 MA15
    ma15_prev: float            # 前一根 MA15
    kc_upper: float             # KC 上軌
    kc_middle: float            # KC 中軌
    kc_middle_prev: float       # 前一根 KC 中軌
    kc_lower: float             # KC 下軌
    atr: float                  # ATR 波動值
    macro_trend: str            # 大週期趨勢: "BULLISH", "BEARISH", "RANGE"
    ma_cross_count_8: int       # 近 8 根已收線 K 棒 MA5 與 MA15 交叉次數
    timeframe_ms: float = 60000.0
    is_closed: bool = True
    kc_width_max_20: float = 0.0  # 近 20 根已收線 KC 最大寬度；> 0 時改用相對收斂判定
    kc_middle_prev2: Optional[float] = None  # 前二根 KC 中軌（None = 非 frame 建立，不套用趨勢過濾）
    kc_middle_prev3: Optional[float] = None  # 前三根 KC 中軌
    adx: Optional[float] = None              # 最新已收線 ADX
    current_swing_low: Optional[float] = None   # 當前波段谷底低點 (Current_Swing_Low)
    prev_swing_low: Optional[float] = None      # 前一波段谷底低點 (Prev_Swing_Low)
    current_swing_high: Optional[float] = None  # 當前波段峰頂高點 (Current_Swing_High)
    prev_swing_high: Optional[float] = None     # 前一波段峰頂高點 (Prev_Swing_High)
    kc_width_prev: Optional[float] = None       # 前一根 KC 通道寬度 (KC_Width_prev)
    kc_width_ma5: Optional[float] = None        # 近 5 根 KC 通道平均寬度 (KC_Width_MA5)
    kc_lower_prev: Optional[float] = None       # 前一根 KC 下軌 (KC_Lower_prev)
    kc_upper_prev: Optional[float] = None       # 前一根 KC 上軌 (KC_Upper_prev)
    has_position: bool = False                  # 是否已有持倉 (防重複加碼/半山腰追高硬鎖)
    position_side: Optional[str] = None         # 當前持倉方向 ("LONG" / "SHORT")
    position_size: float = 0.0                  # 當前持倉數量 (position > 0 嚴禁二次開倉)


# Owner 2026-11-03: use the latest completed KC middle slope for entry direction.
MEME_TREND_KC_BARS = 1
MEME_MIN_KC_MIDDLE_SLOPE_ATR = 0.05
MEME_MIN_MA5_MIDDLE_DISTANCE_ATR = 0.10


def _slope_reaches_threshold(value: float, threshold: float) -> bool:
    return value >= threshold or math.isclose(value, threshold, rel_tol=1e-9, abs_tol=0)


# Owner 2026-11-03: MEME 1m KC widths are structurally < 1% of price, so the squeeze veto is
# relative: current width <= 25% of the widest of the last 20 closed bars (AGENTS.md rule).
MEME_SQUEEZE_RELATIVE_RATIO = 0.25
MEME_SQUEEZE_LOOKBACK = 20


class PositionSnapshot(NamedTuple):
    symbol: str
    side: str                   # "LONG" 或 "SHORT"
    entry_price: float          # 開倉成本價
    open_timestamp_ms: float    # 開倉時間戳 (毫秒)
    bars_held: int              # 已持倉 K 棒數
    peak_roe_pct: float         # 歷史最高 ROE 百分比 (如 15.0 代表 +15%)
    has_confirmed_pivot: bool   # 是否已收線確認三點真峰谷
    lowest_price_since_entry: float = 0.0   # 空單波段最低價
    highest_price_since_entry: float = 0.0  # 多單波段最高價
    estimated_net_pnl: Optional[float] = None


class GateDecision(NamedTuple):
    can_execute: bool
    action: str                 # "OPEN_LONG", "OPEN_SHORT", "HOLD", "CLOSE_POSITION"
    reason: str
    details: Dict[str, Any]


def market_snapshot_from_frame(frame, symbol, quote, macro_trend=None) -> MarketSnapshot:
    from core.services.exits.trend_lock import _closed_snapshot_fresh
    from core.services.data_pipeline import DataPipeline

    if not isinstance(frame, pd.DataFrame):
        raise ValueError("MEME_GATE_CLOSED_FRAME_REQUIRED")
    closed = closed_entry_candles(frame)
    if len(closed) < 8 or float(frame.attrs.get("timeframe_ms", 0)) != 60_000:
        raise ValueError("MEME_GATE_EIGHT_CLOSED_1M_BARS_REQUIRED")
    snapshot_ms = float(frame.attrs.get("snapshot_ms", 0))
    if not 0 <= time.time() * 1000 - snapshot_ms <= 180_000 or not _closed_snapshot_fresh(frame, closed):
        raise ValueError("MEME_GATE_STALE_SNAPSHOT")
    recent = closed.tail(8)
    keys = ("timestamp", "open", "high", "low", "close", "ma5", "ma15",
            "kc_upper", "kc_middle", "kc_lower", "atr")
    values = recent[list(keys)].astype(float)
    if any(not math.isfinite(v) or v <= 0 for v in values.to_numpy().flat):
        raise ValueError("MEME_GATE_INVALID_CLOSED_DATA")
    if any(not math.isclose(b - a, 60_000, abs_tol=1)
           for a, b in zip(values["timestamp"], values["timestamp"].iloc[1:])):
        raise ValueError("MEME_GATE_NONCONTIGUOUS_CANDLES")
    if any(row.high < max(row.open, row.close) or row.low > min(row.open, row.close)
           or not row.kc_lower < row.kc_middle < row.kc_upper for row in values.itertuples()):
        raise ValueError("MEME_GATE_INVALID_OHLC_OR_CHANNEL")
    spread = values["ma5"] - values["ma15"]
    signs = spread.map(lambda x: 1 if x > 0 else -1 if x < 0 else float("nan")).ffill()
    crosses = int((signs * signs.shift(1) < 0).sum())
    current, previous = closed.iloc[-1], closed.iloc[-2]
    widths = (closed["kc_upper"].astype(float) - closed["kc_lower"].astype(float)).tail(MEME_SQUEEZE_LOOKBACK)
    widths = widths[widths.map(lambda w: math.isfinite(w) and w > 0)]
    kc_width_max_20 = float(widths.max()) if len(widths) else 0.0
    macro_frame = frame.attrs.get("macro_frame")
    has_explicit_macro_frame = macro_frame is not None
    if macro_frame is None:
        macro_frame = closed
    calculated_macro = str(DataPipeline.calculate_snapshot(
        symbol, macro_frame, current_price=float(current["close"]),
    ).macro_trend).upper()
    kc_mid_diff = float(current["kc_middle"]) - float(previous["kc_middle"])
    kc_slope_macro = "BULLISH" if kc_mid_diff > 0 else ("BEARISH" if kc_mid_diff < 0 else "NEUTRAL")
    explicit_attr_macro = str(frame.attrs.get("macro_trend") or "").upper()
    has_conflicting_slope_col = False
    if "macro_kc_middle_slope" in frame.columns:
        try:
            col_slope = float(frame["macro_kc_middle_slope"].iloc[-1])
            if (col_slope > 0 and kc_mid_diff < 0) or (col_slope < 0 and kc_mid_diff > 0):
                has_conflicting_slope_col = True
        except Exception:
            pass
    if (
        not has_explicit_macro_frame
        and not has_conflicting_slope_col
        and kc_slope_macro in ("BULLISH", "BEARISH")
        and calculated_macro != kc_slope_macro
        and (not explicit_attr_macro or explicit_attr_macro == kc_slope_macro)
    ):
        calculated_macro = kc_slope_macro
    macro = str(macro_trend or explicit_attr_macro or calculated_macro).upper()
    # Neither an upstream label nor a local override may hide contrary macro evidence.
    if calculated_macro in ("BULLISH", "BEARISH") and macro != calculated_macro:
        if macro in ("BULLISH", "BEARISH"):
            raise ValueError("MEME_GATE_MACRO_EVIDENCE_CONFLICT")
        macro = calculated_macro
    return MarketSnapshot(
        symbol=symbol, quote=float(quote), open_price=float(current["open"]),
        close_price=float(current["close"]), high_price=float(current["high"]),
        low_price=float(current["low"]), ma3_curr=0, ma3_prev=0,
        ma5_curr=float(current["ma5"]), ma5_prev=float(previous["ma5"]),
        ma15_curr=float(current["ma15"]), ma15_prev=float(previous["ma15"]),
        kc_upper=float(current["kc_upper"]), kc_middle=float(current["kc_middle"]),
        kc_middle_prev=float(previous["kc_middle"]), kc_lower=float(current["kc_lower"]),
        atr=float(previous["atr"]), macro_trend=macro, ma_cross_count_8=crosses,
        is_closed=True, kc_width_max_20=kc_width_max_20,
        kc_middle_prev2=float(values["kc_middle"].iloc[-3]),
        kc_middle_prev3=float(values["kc_middle"].iloc[-4]),
        adx=float(current["adx"]) if "adx" in current.index else None,
    )


def evaluate_meme_entry_frame(frame, symbol, side, quote, *, last_closed_at=None, macro_trend=None):
    """Common qualification only; independent entry/order authority is still required."""
    from core.symbol_aliases import is_meme_breakout_symbol
    if not is_meme_breakout_symbol(symbol):
        return GateDecision(True, "FILTER_PASS", "MEME_GATE_NOT_APPLICABLE", {})
    try:
        snapshot = market_snapshot_from_frame(frame, symbol, quote, macro_trend)
        cooldown = False
        if last_closed_at is not None:
            closed_at = float(last_closed_at)
            if closed_at >= 1e11:
                closed_at /= 1000
            if not math.isfinite(closed_at) or closed_at <= 0 or closed_at > time.time():
                raise ValueError("MEME_GATE_INVALID_CLOSE_IDENTITY")
            # Post-exit cooldown: 2 complete 1m bars; priority channels are exempt in the filters.
            ready_at = math.ceil(closed_at / 60) * 60 + 120
            cooldown = time.time() < ready_at
        return MasterGateFunnel.evaluate_entry_filters(snapshot, side, cooldown)
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError, IndexError) as exc:
        logger.error("MEME_GATE_DATA_BLOCK symbol=%s error=%s", symbol, exc)
        return GateDecision(False, "HOLD", "MEME_GATE_DATA_BLOCK", {"error": str(exc)})


class MasterGateFunnel:
    """交易系統開平倉審查與防誤觸總閘門"""

    # ─────────────────────────────────────────────────────────────
    # 🧭 Layer A: 市場狀態與震盪過濾引擎 (Market Regime Engine)
    # ─────────────────────────────────────────────────────────────
    @classmethod
    def evaluate_market_regime(cls, m: MarketSnapshot) -> Tuple[str, Dict[str, Any]]:
        """
        四維度獨立市場狀態分類引擎（Phase S1 架構核心）：
        結合 KC 通道寬度與變化率、KC 中軌斜率、MA5/MA15 糾纏度 (ma_cross_count_8)、ADX 與波段高低點結構 (HL/LH)：
        1. "CHOP_CONSOLIDATION"（真正無方向死水震盪）：禁止新開倉
        2. "SQUEEZE_PREPARATION"（窄幅壓縮蓄勢待發）：等待有效突破進場
        3. "REVERSAL_ZONE"（極值反轉區）：允許 Mode A 谷底/峰頂極值反轉進場
        4. "TREND_BULLISH" / "TREND_BEARISH"（明確多頭/空頭趨勢或回踩）：允許 Mode B 順勢延續進場
        """
        kc_width = m.kc_upper - m.kc_lower
        atr = m.atr if (math.isfinite(m.atr) and m.atr > 0) else 0.0
        kc_slope = m.kc_middle - m.kc_middle_prev
        ma15_slope = m.ma15_curr - m.ma15_prev
        slope_floor = MEME_MIN_KC_MIDDLE_SLOPE_ATR * atr if atr > 0 else 0.0
        kc_position = (m.close_price - m.kc_lower) / kc_width if kc_width > 0 else 0.5

        if math.isfinite(m.kc_width_max_20) and m.kc_width_max_20 > 0:
            limit = MEME_SQUEEZE_RELATIVE_RATIO * m.kc_width_max_20
            squeezed = kc_width < limit or math.isclose(kc_width, limit, rel_tol=1e-9)
        else:
            squeezed = (kc_width / m.quote < 0.010) if m.quote > 0 else False

        is_flat_mid = (
            atr > 0
            and not _slope_reaches_threshold(abs(kc_slope), slope_floor)
            and not _slope_reaches_threshold(abs(ma15_slope), slope_floor)
        )
        is_chop_crosses = m.ma_cross_count_8 >= 3
        in_outer_reversal_zone = (
            kc_position <= 0.35
            or kc_position >= 0.65
            or cls.is_bottom_pivot_long(m)
            or cls.is_top_pivot_short(m)
        )

        meta = {
            "kc_width": kc_width,
            "kc_slope": kc_slope,
            "ma15_slope": ma15_slope,
            "ma_cross_count_8": m.ma_cross_count_8,
            "squeezed": squeezed,
            "is_flat_mid": is_flat_mid,
            "kc_position": kc_position,
            "adx": m.adx,
        }

        if is_chop_crosses:
            return "CHOP_CONSOLIDATION", meta
        if squeezed:
            return "SQUEEZE_PREPARATION", meta
        if is_flat_mid and not in_outer_reversal_zone:
            return "CHOP_CONSOLIDATION", meta
        if in_outer_reversal_zone and (cls.is_bottom_pivot_long(m) or cls.is_top_pivot_short(m)):
            return "REVERSAL_ZONE", meta
        if kc_slope > 0 or str(m.macro_trend).upper() == "BULLISH":
            return "TREND_BULLISH", meta
        if kc_slope < 0 or str(m.macro_trend).upper() == "BEARISH":
            return "TREND_BEARISH", meta
        return "CHOP_CONSOLIDATION", meta

    # ─────────────────────────────────────────────────────────────
    # 🎯 Layer B: 雙模態進場引擎 (Mode A 極值反轉 vs Mode B 趨勢延續)
    # ─────────────────────────────────────────────────────────────
    @classmethod
    def evaluate_mode_a_reversal_entry(cls, m: MarketSnapshot, side: str) -> Tuple[bool, Optional[str]]:
        """
        Mode A（極值反轉進場 — Exhaustion & Pivot Reversal Entry）：
        在真正谷底附近提早做多、真正峰頂附近提早做空，不必等待滯後 EMA40/EMA60 交叉，
        但必須同時具備：極值區位置 + 動能衰竭反轉 K 棒 + 收盤越過 MA5 且 MA5 拐頭 + 結構保護。
        """
        if not m.is_closed or not math.isfinite(m.atr) or m.atr <= 0:
            return False, None
        kc_width = m.kc_upper - m.kc_lower
        if kc_width <= 0:
            return False, None
        kc_pos = (m.close_price - m.kc_lower) / kc_width
        open_pos = (m.open_price - m.kc_lower) / kc_width
        low_pos = (m.low_price - m.kc_lower) / kc_width
        high_pos = (m.high_price - m.kc_lower) / kc_width

        if side == "LONG":
            if m.close_price <= m.open_price or m.quote <= m.open_price:
                return False, None
            if m.close_price <= m.ma5_curr or m.ma5_curr <= m.ma5_prev or m.ma5_curr <= m.ma15_curr:
                return False, None
            if m.current_swing_low is not None and m.prev_swing_low is not None and m.current_swing_low <= m.prev_swing_low:
                return False, None
            if cls.is_bottom_pivot_long(m):
                return True, "TRIGGER_BOTTOM_PIVOT_CROSS"
            in_valley_zone = (low_pos <= 0.35 or open_pos <= 0.50) and kc_pos <= 0.75
            body = m.close_price - m.open_price
            if in_valley_zone and body >= 0.35 * m.atr and m.close_price > m.kc_middle and m.close_price > m.ma15_curr:
                return True, "TRIGGER_MODE_A_VALLEY_REVERSAL_LONG"

        elif side == "SHORT":
            if m.close_price >= m.open_price or m.quote >= m.open_price:
                return False, None
            if m.close_price >= m.ma5_curr or m.ma5_curr >= m.ma5_prev or m.ma5_curr >= m.ma15_curr:
                return False, None
            if m.current_swing_high is not None and m.prev_swing_high is not None and m.current_swing_high >= m.prev_swing_high:
                return False, None
            if cls.is_top_pivot_short(m):
                return True, "TRIGGER_TOP_PIVOT_CROSS"
            in_peak_zone = (high_pos >= 0.65 or open_pos >= 0.50) and kc_pos >= 0.25
            body = m.open_price - m.close_price
            if in_peak_zone and body >= 0.35 * m.atr and m.close_price < m.kc_middle and m.close_price < m.ma15_curr:
                return True, "TRIGGER_MODE_A_PEAK_REVERSAL_SHORT"

        return False, None

    @classmethod
    def evaluate_mode_b_continuation_entry(cls, m: MarketSnapshot, side: str) -> Tuple[bool, Optional[str]]:
        """
        Mode B（趨勢延續與有效突破進場 — Trend Continuation Entry）：
        僅在趨勢方向明確（KC 中軌與均線同向排列）且有有效突破或回踩中軌轉強時進場，
        嚴格執行防追高追低（Anti-Chase）限制。
        """
        if not m.is_closed or not math.isfinite(m.atr) or m.atr <= 0:
            return False, None
        if side == "LONG":
            if m.close_price <= m.open_price or m.ma5_curr <= m.ma15_curr or m.ma5_curr <= m.ma5_prev:
                return False, None
            if m.kc_middle <= m.kc_middle_prev:
                return False, None
            if cls.is_early_breakout_long(m):
                return True, "TRIGGER_1145_EARLY_BREAKOUT"
            if cls.is_squeeze_breakout_long(m):
                return True, "TRIGGER_1148_SQUEEZE_BREAKOUT"
            if cls.is_expansion(m, "LONG"):
                return True, "TRIGGER_A_SQUEEZE_EXPANSION"
            if cls.is_early_long(m):
                return True, "TRIGGER_EARLY_0748_BREAKOUT"
            if cls.is_outer_rail_continuation(m, "LONG"):
                return True, "TRIGGER_OUTER_RAIL_CONTINUATION_LONG"
            if (
                m.kc_middle <= m.low_price <= m.kc_middle + 0.35 * m.atr
                and m.ma5_prev > m.ma15_prev and m.ma5_curr > m.ma15_curr
            ):
                return True, "TRIGGER_B_PULLBACK_KC_MIDDLE_BOUNCE"
            if cls.is_slope_aligned_trend_entry(m, "LONG"):
                return True, "TRIGGER_ALIGNED_TREND_SLOPES"

        elif side == "SHORT":
            if m.close_price >= m.open_price or m.ma5_curr >= m.ma15_curr or m.ma5_curr >= m.ma5_prev:
                return False, None
            if m.kc_middle >= m.kc_middle_prev:
                return False, None
            if cls.is_big_body_breakout(m, "SHORT"):
                return True, "TRIGGER_EARLY_BREAKDOWN_CHANNEL"
            if cls.is_expansion(m, "SHORT"):
                return True, "TRIGGER_A_EARLY_BODY_BREAKDOWN"
            if (
                m.close_price < m.kc_lower
                and m.quote < m.kc_lower
                and m.close_price < m.open_price
                and (m.kc_middle - m.kc_middle_prev) < 0
                and m.ma15_curr < m.ma15_prev
                and m.ma5_curr < m.ma5_prev
            ):
                return True, "TRIGGER_B_BREAKDOWN_LOWER_RAIL"
            if cls.is_slope_aligned_trend_entry(m, "SHORT"):
                return True, "TRIGGER_ALIGNED_TREND_SLOPES"
            if cls.is_outer_rail_continuation(m, "SHORT"):
                return True, "TRIGGER_OUTER_RAIL_CONTINUATION_SHORT"

        return False, None

    # ─────────────────────────────────────────────────────────────
    # 🚫 黑名單與無效訊號過濾 (符合任一即強制 HOLD)
    # ─────────────────────────────────────────────────────────────
    @classmethod
    def evaluate_market_blacklist(cls, m: MarketSnapshot) -> Tuple[bool, str]:
        values = (m.quote, m.open_price, m.close_price, m.high_price, m.low_price,
                  m.ma5_curr, m.ma5_prev, m.ma15_curr, m.ma15_prev,
                  m.kc_upper, m.kc_middle, m.kc_middle_prev, m.kc_lower, m.atr)
        if (any(not math.isfinite(v) or v <= 0 for v in values)
                or not m.kc_lower < m.kc_middle < m.kc_upper
                or m.high_price < max(m.open_price, m.close_price)
                or m.low_price > min(m.open_price, m.close_price)
                or m.timeframe_ms != 60_000 or not m.is_closed):
            return True, "VETO_INVALID_MARKET_DATA"
        if m.ma_cross_count_8 >= 3:
            return True, "VETO_MA5_MA15_CHOP_8"
        expansion = (
            cls.is_expansion(m, "LONG") or cls.is_expansion(m, "SHORT")
            or cls.is_early_long(m) or cls.is_top_pivot_short(m)
            or cls.is_squeeze_breakout_long(m) or cls.is_bottom_pivot_long(m)
            or cls.is_big_body_breakout(m, "LONG") or cls.is_big_body_breakout(m, "SHORT")
            or cls.is_outer_rail_continuation(m, "LONG") or cls.is_outer_rail_continuation(m, "SHORT")
            or cls.is_continuation_entry(m, "LONG") or cls.is_continuation_entry(m, "SHORT")
            or cls.is_express_trend_entry(m, "LONG") or cls.is_express_trend_entry(m, "SHORT")
        )
        kc_width = m.kc_upper - m.kc_lower
        if math.isfinite(m.kc_width_max_20) and m.kc_width_max_20 > 0:
            limit = MEME_SQUEEZE_RELATIVE_RATIO * m.kc_width_max_20
            squeezed = kc_width < limit or math.isclose(kc_width, limit, rel_tol=1e-9)
        else:
            squeezed = kc_width / m.quote < 0.010
        if squeezed and not expansion:
            return True, "VETO_NARROW_SQUEEZE_RANGE"
        kc_slope = m.kc_middle - m.kc_middle_prev
        ma15_slope = m.ma15_curr - m.ma15_prev
        slope_floor = MEME_MIN_KC_MIDDLE_SLOPE_ATR * m.atr
        if (
            not _slope_reaches_threshold(abs(kc_slope), slope_floor)
            and not _slope_reaches_threshold(abs(ma15_slope), slope_floor)
            and not expansion
        ):
            return True, "VETO_FLAT_KC_MIDLINE_CHOP"
        return False, "PASS_MARKET_HEALTHY"

    @staticmethod
    def is_expansion(m: MarketSnapshot, side: str) -> bool:
        sign = 1 if side == "LONG" else -1
        body = sign * (m.close_price - m.open_price)
        threshold = (0.8 if side == "LONG" else 0.7) * m.atr
        return (
            (body >= threshold or math.isclose(body, threshold, rel_tol=1e-12, abs_tol=0))
            and sign * (m.close_price - m.kc_middle) > 0
            and sign * (m.close_price - m.ma15_curr) > 0
        )

    @staticmethod
    def is_early_long(m: MarketSnapshot) -> bool:
        body = m.close_price - m.open_price
        threshold = 0.40 * m.atr
        return (
            m.is_closed
            and body > 0
            and (body >= threshold or math.isclose(body, threshold, rel_tol=1e-12, abs_tol=0))
            and m.close_price > m.kc_middle
            and m.close_price > m.ma15_curr
            and m.ma5_curr > m.ma5_prev
            and m.ma5_curr >= m.ma15_curr
        )

    @staticmethod
    def is_bottom_v_long(m: MarketSnapshot) -> bool:
        body = m.close_price - m.open_price
        threshold = 0.50 * m.atr
        return (
            MasterGateFunnel.is_early_long(m)
            and (body >= threshold or math.isclose(body, threshold, rel_tol=1e-12, abs_tol=0))
            and m.ma5_prev > m.ma15_prev
            and m.ma5_curr > m.ma15_curr
        )

    @staticmethod
    def is_early_breakout_long(m: MarketSnapshot) -> bool:
        body = m.close_price - m.open_price
        threshold = 0.50 * m.atr
        return (
            MasterGateFunnel.is_early_long(m)
            and m.ma5_curr > m.ma15_curr
            and (body >= threshold or math.isclose(body, threshold, rel_tol=1e-12, abs_tol=0))
            and (m.open_price <= m.kc_middle or m.open_price <= m.ma15_curr)
        )

    @staticmethod
    def is_squeeze_breakout_long(m: MarketSnapshot) -> bool:
        body = m.close_price - m.open_price
        threshold = 0.70 * m.atr
        return (
            m.is_closed and body > 0
            and (body >= threshold or math.isclose(body, threshold, rel_tol=1e-12, abs_tol=0))
            and m.close_price > m.kc_upper
            and m.ma5_curr > m.ma15_curr
        )

    @staticmethod
    def is_top_pivot_short(m: MarketSnapshot) -> bool:
        body = m.open_price - m.close_price
        kc_width = m.kc_upper - m.kc_lower
        kc_position = (
            (m.close_price - m.kc_lower) / kc_width if kc_width > 0 else math.nan
        )
        _, short_min = pivot_kc_position_bounds(m.symbol)
        return (
            m.is_closed
            and body > 0
            and math.isfinite(kc_position)
            and kc_position >= short_min
            and m.kc_middle < m.kc_middle_prev
            and m.close_price < m.kc_middle
            and m.close_price < m.ma15_curr
            and m.ma5_curr < m.ma5_prev
            and m.ma5_prev >= m.ma15_prev
            and m.ma5_curr < m.ma15_curr
        )

    @staticmethod
    def is_bottom_pivot_long(m: MarketSnapshot) -> bool:
        """LONG mirror of the top-pivot death-cross channel."""
        body = m.close_price - m.open_price
        kc_width = m.kc_upper - m.kc_lower
        kc_position = (
            (m.close_price - m.kc_lower) / kc_width if kc_width > 0 else math.nan
        )
        long_max, _ = pivot_kc_position_bounds(m.symbol)
        in_bottom_zone = (
            math.isfinite(kc_position)
            and (
                kc_position <= long_max
                or (
                    m.open_price <= m.kc_middle
                    and m.close_price < m.kc_upper
                    and m.kc_middle > m.kc_middle_prev
                    and m.ma15_curr > m.ma15_prev
                    and body >= 0.80 * m.atr
                )
            )
        )
        return (
            m.is_closed
            and body > 0
            and in_bottom_zone
            and m.kc_middle > m.kc_middle_prev
            and m.close_price > m.kc_middle
            and m.close_price > m.ma15_curr
            and m.ma5_curr > m.ma5_prev
            and m.ma5_prev <= m.ma15_prev
            and m.ma5_curr > m.ma15_curr
        )

    @classmethod
    def evaluate_position_flip(
        cls, m: MarketSnapshot, position_side: str,
    ) -> Optional[Tuple[str, str]]:
        """Return the opposite side only for a closed, confirmed top/bottom reversal."""
        return cls.evaluate_top_bottom_flip(m, position_side)

    @classmethod
    def evaluate_top_bottom_flip(
        cls, m: MarketSnapshot, position_side: Optional[str] = None,
    ) -> Optional[Tuple[str, str]]:
        """
        ⚡ 一、 核心極速通道：斷頭級「真峰頂 / 真谷底」瞬間平倉＋反手開倉 (Zero-Delay Position Flip)
        當 1m K 棒確定收線 (is_bar_closed == True) 當下，一旦識別出斷頭級轉向，
        100% 繞過平倉緩衝區、100% 秒解 Cooldown，但嚴格遵守 KC 中軌方向順勢鐵律：

        1. 真峰頂 / 斷頭轉向 (平多+開空 / 無倉開空)：
           (1) 1m K 棒為「實體紅 K (陰線下殺，close < open, Body >= 0.35 ATR)」
           (2) KC 中軌必須向下 (kc_middle < kc_middle_prev)，中軌向上嚴禁開空
           (3) 1m 收盤價實質跌破 MA15 且跌破 KC 中軌
           (4) MA5 實質向下死叉 MA15 (ma5_curr < ma15_curr) 或 MA5 陡峭向下跌破 MA15

        2. 真谷底 / V轉反彈 (平空+開多 / 無倉開多)：
           (1) 1m K 棒為「實體綠 K (陽線反彈，close > open, Body >= 0.35 ATR)」
           (2) KC 中軌必須向上 (kc_middle > kc_middle_prev)，中軌向下嚴禁開多
           (3) 1m 收盤價強勢站回 MA15 且站上 KC 中軌
           (4) MA5 實質向上金叉 MA15 (ma5_curr > ma15_curr) 或 MA5 陡峭向上突破 MA15
        """
        if not m.is_closed or m.timeframe_ms != 60_000.0 or not math.isfinite(m.atr) or m.atr <= 0:
            return None

        side = str(position_side or "").upper() if position_side else None

        # 頂部斷頭轉向 -> 開空 (必須 KC 中軌向下且非多頭大勢)
        is_top_flip = (
            m.kc_middle < m.kc_middle_prev
            and str(m.macro_trend).upper() != "BULLISH"
            and (
                cls.is_top_pivot_short(m)
                or (
                    m.close_price < m.open_price
                    and (m.open_price - m.close_price) >= 0.35 * m.atr
                    and m.close_price < m.ma15_curr
                    and m.close_price < m.kc_middle
                    and m.ma5_curr < m.ma15_curr
                    and m.ma5_curr < m.ma5_prev
                    and m.ma5_prev >= m.ma15_prev
                )
            )
        )
        if is_top_flip:
            if side is None or side in ("LONG", "FLAT", ""):
                return "SHORT", "TRIGGER_TOP_PIVOT_CROSS"

        # 底部V轉反彈 -> 開多 (必須 KC 中軌向上且非空頭大勢)
        is_bottom_flip = (
            m.kc_middle > m.kc_middle_prev
            and str(m.macro_trend).upper() != "BEARISH"
            and (
                cls.is_bottom_pivot_long(m)
                or (
                    m.close_price > m.open_price
                    and (m.close_price - m.open_price) >= 0.35 * m.atr
                    and m.close_price > m.ma15_curr
                    and m.close_price > m.kc_middle
                    and m.ma5_curr > m.ma15_curr
                    and m.ma5_curr > m.ma5_prev
                    and m.ma5_prev <= m.ma15_prev
                )
            )
        )
        if is_bottom_flip:
            if side is None or side in ("SHORT", "FLAT", ""):
                return "LONG", "TRIGGER_BOTTOM_PIVOT_CROSS"

        return None

    @classmethod
    def pivot_kc_location_veto(cls, m: MarketSnapshot, side: str) -> Optional[str]:
        """Retain the location veto except for a confirmed Meme reversal pivot."""
        width = m.kc_upper - m.kc_lower
        if (
            not math.isfinite(width) or width <= 0
            or side not in ("LONG", "SHORT")
            or not math.isfinite(m.close_price)
        ):
            return "VETO_PIVOT_KC_LOCATION_DATA"
        from core.symbol_aliases import is_meme_breakout_symbol

        confirmed_pivot = (
            is_meme_breakout_symbol(m.symbol)
            and (
                side == "SHORT" and (cls.is_top_pivot_short(m) or cls.evaluate_position_flip(m, "LONG") == ("SHORT", "TRIGGER_TOP_PIVOT_CROSS"))
                or side == "LONG" and (cls.is_bottom_pivot_long(m) or cls.evaluate_position_flip(m, "SHORT") == ("LONG", "TRIGGER_BOTTOM_PIVOT_CROSS"))
            )
        )
        if confirmed_pivot:
            return None
        kc_position = (m.close_price - m.kc_lower) / width
        if not math.isfinite(kc_position):
            return "VETO_PIVOT_KC_LOCATION_DATA"
        long_max, short_min = PIVOT_KC_POS_LONG_MAX, PIVOT_KC_POS_SHORT_MIN
        if side == "LONG" and kc_position > long_max and not math.isclose(
            kc_position, long_max, rel_tol=1e-12, abs_tol=0,
        ):
            return "VETO_LONG_PIVOT_KC_POSITION"
        if side == "SHORT" and kc_position < short_min and not math.isclose(
            kc_position, short_min, rel_tol=1e-12, abs_tol=0,
        ):
            return "VETO_SHORT_PIVOT_KC_POSITION"
        return None

    @staticmethod
    def is_big_body_breakout(m: MarketSnapshot, side: str) -> bool:
        """Closed body >= 0.6 ATR that breaks out of the band from inside it on this bar."""
        sign = 1 if side == "LONG" else -1
        body = sign * (m.close_price - m.open_price)
        threshold = 0.60 * m.atr
        band = m.kc_upper if side == "LONG" else m.kc_lower
        open_tolerance = MEME_BREAKOUT_OPEN_BEYOND_BAND_ATR * m.atr
        return (
            m.is_closed
            and body > 0
            and (body >= threshold or math.isclose(body, threshold, rel_tol=1e-12, abs_tol=0))
            and sign * (m.close_price - band) > 0
            and sign * (m.open_price - band) <= open_tolerance
        )

    @staticmethod
    def is_outer_rail_continuation(m: MarketSnapshot, side: str) -> bool:
        """Closed directional continuation bar outside the KC outer rail with aligned MA5/KC slopes."""
        if side not in ("LONG", "SHORT") or not math.isfinite(m.atr) or m.atr <= 0:
            return False
        sign = 1 if side == "LONG" else -1
        body = sign * (m.close_price - m.open_price)
        threshold = 0.25 * m.atr
        band = m.kc_upper if side == "LONG" else m.kc_lower
        open_tolerance = MEME_BREAKOUT_OPEN_BEYOND_BAND_ATR * m.atr
        return (
            m.is_closed
            and body > 0
            and (body >= threshold or math.isclose(body, threshold, rel_tol=1e-12, abs_tol=0))
            and sign * (m.close_price - band) > 0
            and sign * (m.open_price - band) <= open_tolerance
            and sign * (m.ma5_curr - m.ma5_prev) > 0
            and sign * (m.kc_middle - m.kc_middle_prev) > 0
        )

    @staticmethod
    def is_continuation_entry(m: MarketSnapshot, side: str) -> bool:
        """Continuation entry when price remains on the trend side of KC middle/outer rail with body >= 0.30 ATR."""
        if side not in ("LONG", "SHORT") or not math.isfinite(m.atr) or m.atr <= 0:
            return False
        sign = 1 if side == "LONG" else -1
        body = sign * (m.close_price - m.open_price)
        threshold = 0.30 * m.atr
        return (
            m.is_closed
            and body > 0
            and (body >= threshold or math.isclose(body, threshold, rel_tol=1e-12, abs_tol=0))
            and sign * (m.close_price - m.kc_middle) > 0
            and sign * (m.ma5_curr - m.ma15_curr) > 0
            and sign * (m.ma5_curr - m.ma5_prev) > 0
            and sign * (m.kc_middle - m.kc_middle_prev) > 0
        )

    @staticmethod
    def is_express_trend_entry(m: MarketSnapshot, side: str) -> bool:
        """Express Trend Channel: trend-aligned breakout/continuation with body >= 0.35 ATR,
        MA5 > MA15 (or < for short) diverging in direction, and close beyond KC middle/MA15 or outer rail."""
        if side not in ("LONG", "SHORT") or not math.isfinite(m.atr) or m.atr <= 0:
            return False
        sign = 1 if side == "LONG" else -1
        body = sign * (m.close_price - m.open_price)
        threshold = 0.35 * m.atr
        band = m.kc_upper if side == "LONG" else m.kc_lower
        beyond_mid_or_rail = (
            (sign * (m.close_price - m.kc_middle) > 0 and sign * (m.close_price - m.ma15_curr) > 0)
            or sign * (m.close_price - band) > 0
        )
        return (
            m.is_closed
            and body > 0
            and (body >= threshold or math.isclose(body, threshold, rel_tol=1e-12, abs_tol=0))
            and beyond_mid_or_rail
            and sign * (m.ma5_curr - m.ma15_curr) > 0
            and sign * (m.ma5_curr - m.ma5_prev) > 0
            and sign * (m.kc_middle - m.kc_middle_prev) > 0
        )

    @staticmethod
    def is_ma_cross_slope_entry(m: MarketSnapshot, side: str) -> bool:
        """A fresh cross is priority only when KC, MA5, and MA15 slopes are established."""
        if side not in ("LONG", "SHORT") or not math.isfinite(m.atr) or m.atr <= 0:
            return False
        sign = 1 if side == "LONG" else -1
        slope_floor = MEME_MIN_KC_MIDDLE_SLOPE_ATR * m.atr
        touch_breakout_cross = (
            math.isclose(m.ma5_curr, m.ma15_curr, rel_tol=1e-9, abs_tol=1e-12)
            and sign * (m.ma5_prev - m.ma15_prev) < 0
            and MasterGateFunnel.is_big_body_breakout(m, side)
            and sign * (m.open_price - m.ma15_curr) < 0
        )
        crossed = (
            sign * (m.ma5_prev - m.ma15_prev) <= 0
            and (sign * (m.ma5_curr - m.ma15_curr) > 0 or touch_breakout_cross)
        )
        return (
            crossed
            and _slope_reaches_threshold(sign * (m.kc_middle - m.kc_middle_prev), slope_floor)
            and _slope_reaches_threshold(sign * (m.ma5_curr - m.ma5_prev), slope_floor)
            and _slope_reaches_threshold(sign * (m.ma15_curr - m.ma15_prev), slope_floor)
        )

    @staticmethod
    def is_slope_aligned_trend_entry(m: MarketSnapshot, side: str) -> bool:
        """Allow a post-cross entry once direction and all three slopes are established."""
        if side not in ("LONG", "SHORT") or not math.isfinite(m.atr) or m.atr <= 0:
            return False
        sign = 1 if side == "LONG" else -1
        slope_floor = MEME_MIN_KC_MIDDLE_SLOPE_ATR * m.atr
        return (
            sign * (m.ma5_curr - m.ma15_curr) > 0
            and _slope_reaches_threshold(sign * (m.kc_middle - m.kc_middle_prev), slope_floor)
            and _slope_reaches_threshold(sign * (m.ma5_curr - m.ma5_prev), slope_floor)
            and _slope_reaches_threshold(sign * (m.ma15_curr - m.ma15_prev), slope_floor)
        )

    @staticmethod
    def is_reverse_after_structure_exit(m: MarketSnapshot, side: str) -> bool:
        """Opposite-side entry right after a structure exit, only into a clear trend: KC middle
        moving that way by >= 0.05 ATR, no MA5/MA15 chop, MA5 beyond MA15 and turning the same
        way, directional candle, close and quote beyond KC middle and MA15 (anti-chase in filters)."""
        if side not in ("LONG", "SHORT") or reverse_window_side(m.symbol) != side:
            return False
        sign = 1 if side == "LONG" else -1
        atr = meme_distance_atr(m.symbol, m.atr)
        if not (math.isfinite(atr) and atr > 0):
            return False
        kc_move, flat_limit = sign * (m.kc_middle - m.kc_middle_prev), REVERSE_KC_FLAT_ATR * atr
        return (
            (kc_move >= flat_limit or math.isclose(kc_move, flat_limit, rel_tol=1e-9, abs_tol=0))
            and m.ma_cross_count_8 < REVERSE_CHOP_CROSSES
            and sign * (m.ma5_curr - m.ma15_curr) > 0
            and sign * (m.close_price - m.open_price) > 0
            and sign * (m.ma5_curr - m.ma5_prev) > 0
            and all(sign * (price - level) > 0
                    for price in (m.quote, m.close_price) for level in (m.kc_middle, m.ma15_curr))
        )

    @classmethod
    def priority_channel(cls, m: MarketSnapshot, side: str) -> Optional[str]:
        """Priority triggers that may bypass the post-exit cooldown."""
        if cls.is_reverse_after_structure_exit(m, side):
            return REVERSE_TRIGGER
        if side == "SHORT":
            if cls.is_top_pivot_short(m):
                return "TRIGGER_TOP_PIVOT_CROSS"
            if cls.is_big_body_breakout(m, "SHORT"):
                return "TRIGGER_EARLY_BREAKDOWN_CHANNEL"
            if cls.is_outer_rail_continuation(m, "SHORT"):
                return "TRIGGER_OUTER_RAIL_CONTINUATION_SHORT"
        elif side == "LONG":
            if cls.is_bottom_pivot_long(m):
                return "TRIGGER_BOTTOM_PIVOT_CROSS"
            if cls.is_big_body_breakout(m, "LONG"):
                return "TRIGGER_EARLY_BREAKOUT_CHANNEL"
            if cls.is_outer_rail_continuation(m, "LONG"):
                return "TRIGGER_OUTER_RAIL_CONTINUATION_LONG"
        if cls.is_ma_cross_slope_entry(m, side):
            return MA_CROSS_SLOPE_TRIGGER
        return None

    @staticmethod
    def swing_trend_block(
        m: MarketSnapshot, side: str, priority: Optional[str] = None,
    ) -> Optional[str]:
        """Reject flat KC direction and MA5 clustering around its middle for every entry.

        Fresh breakout/pivot authorities are treated as authorized by structure, so they bypass the
        lagging KC flatness guard without bypassing the shared market-data and anti-chase filters.
        """
        sign = 1 if side == "LONG" else -1
        if not math.isfinite(m.atr) or m.atr <= 0:
            return "VETO_INVALID_MARKET_DATA"
        breakout_priority = priority in {
            "TRIGGER_EARLY_BREAKOUT_CHANNEL",
            "TRIGGER_EARLY_BREAKDOWN_CHANNEL",
            "TRIGGER_1145_EARLY_BREAKOUT",
            "TRIGGER_1148_SQUEEZE_BREAKOUT",
            "TRIGGER_A_EARLY_BODY_BREAKDOWN",
            "TRIGGER_A_SQUEEZE_EXPANSION",
            "TRIGGER_EXPRESS_TREND_LONG",
            "TRIGGER_EXPRESS_TREND_SHORT",
            "TRIGGER_OUTER_RAIL_CONTINUATION_LONG",
            "TRIGGER_OUTER_RAIL_CONTINUATION_SHORT",
            "TRIGGER_CONTINUATION_ENTRY_LONG",
            "TRIGGER_CONTINUATION_ENTRY_SHORT",
            "TRIGGER_EXPRESS_TREND_ENTRY_LONG",
            "TRIGGER_EXPRESS_TREND_ENTRY_SHORT",
        }
        pivot_turn = priority in {
            "TRIGGER_TOP_PIVOT_CROSS", "TRIGGER_BOTTOM_PIVOT_CROSS",
        }
        if sign * (m.kc_middle - m.kc_middle_prev) <= 0:
            return f"BLOCKED_{side}_KC_DIRECTION"
        if not pivot_turn and not breakout_priority:
            slope_limit = MEME_MIN_KC_MIDDLE_SLOPE_ATR * m.atr
            kc_slope = sign * (m.kc_middle - m.kc_middle_prev)
            if abs(kc_slope) < slope_limit and not math.isclose(
                abs(kc_slope), slope_limit, rel_tol=1e-9, abs_tol=0,
            ):
                return f"BLOCKED_{side}_KC_MIDDLE_TOO_FLAT"
        if not pivot_turn and not breakout_priority:
            ma5_middle_distance = abs(m.ma5_curr - m.kc_middle)
            distance_limit = MEME_MIN_MA5_MIDDLE_DISTANCE_ATR * m.atr
            if ma5_middle_distance < distance_limit and not math.isclose(
                ma5_middle_distance, distance_limit, rel_tol=1e-9, abs_tol=0,
            ):
                return f"BLOCKED_{side}_MA5_TOO_CLOSE_TO_KC_MIDDLE"
        return None

    @classmethod
    def has_valid_priority_entry(cls, m: MarketSnapshot, side: str) -> bool:
        decision = (
            cls.evaluate_open_long(m) if side == "LONG"
            else cls.evaluate_open_short(m)
        )
        return bool(decision.can_execute and decision.details.get("priority_channel"))

    @classmethod
    def evaluate_entry_filters(cls, m: MarketSnapshot, side: str, is_in_cooldown: bool = False) -> GateDecision:
        details = {"symbol": m.symbol, "quote": m.quote, "macro": m.macro_trend}
        def block(reason):
            logger.info("[%s] symbol=%s side=%s", reason, m.symbol, side)
            if side == "SHORT":
                logger.info("[VETO_SHORT] symbol=%s reason=%s", m.symbol, reason)
            return GateDecision(False, "HOLD", reason, details)
        if side not in ("LONG", "SHORT"):
            return block("VETO_INVALID_ENTRY_SIDE")
        try:
            priority = cls.priority_channel(m, side)
        except (TypeError, ValueError):
            priority = None
        if priority:
            details["priority_channel"] = priority
        CONTINUATION_PRIORITY_TRIGGERS = {
            REVERSE_TRIGGER,
            "TRIGGER_OUTER_RAIL_CONTINUATION_LONG",
            "TRIGGER_OUTER_RAIL_CONTINUATION_SHORT",
            "TRIGGER_CONTINUATION_ENTRY_LONG",
            "TRIGGER_CONTINUATION_ENTRY_SHORT",
            "TRIGGER_EXPRESS_TREND_ENTRY_LONG",
            "TRIGGER_EXPRESS_TREND_ENTRY_SHORT",
            "TRIGGER_EARLY_BREAKDOWN_CHANNEL",
            "TRIGGER_EARLY_BREAKOUT_CHANNEL",
            "TRIGGER_ALIGNED_TREND_SLOPES",
            "TRIGGER_A_EARLY_BODY_BREAKDOWN",
            "TRIGGER_1145_EARLY_BREAKOUT",
            "TRIGGER_1148_SQUEEZE_BREAKOUT",
        }
        if is_in_cooldown:
            # 結束了後面有走勢要延續開倉（強制上車）：
            # 若為反轉觸發或滿足強勢外軌延續開倉（AGENTS.md 模組二：實體運行在 KC 外軌強勢區）/ 大實體破軌，豁免平倉冷卻期，立即授權上車！
            is_continuation_pass = (
                priority in CONTINUATION_PRIORITY_TRIGGERS
                or cls.is_reverse_after_structure_exit(m, side)
                or (
                    side == "SHORT"
                    and (
                        cls.is_outer_rail_continuation(m, "SHORT")
                        or cls.is_big_body_breakout(m, "SHORT")
                    )
                )
                or (
                    side == "LONG"
                    and (
                        cls.is_outer_rail_continuation(m, "LONG")
                        or cls.is_big_body_breakout(m, "LONG")
                    )
                )
            )
            if not is_continuation_pass:
                return block("BLOCKED_BY_POST_EXIT_COOLDOWN")
        blocked, reason = cls.evaluate_market_blacklist(m)
        if blocked:
            return block(reason)
        sign = 1 if side == "LONG" else -1
        pivot_reversal = priority in {
            "TRIGGER_TOP_PIVOT_CROSS", "TRIGGER_BOTTOM_PIVOT_CROSS",
        }
        from core.symbol_aliases import is_meme_breakout_symbol
        big_body_breakout = cls.is_big_body_breakout(m, side)
        slope_floor = MEME_MIN_KC_MIDDLE_SLOPE_ATR * m.atr
        slope_aligned_breakout = (
            _slope_reaches_threshold(sign * (m.ma15_curr - m.ma15_prev), slope_floor)
            and _slope_reaches_threshold(sign * (m.kc_middle - m.kc_middle_prev), slope_floor)
            and _slope_reaches_threshold(sign * (m.ma5_curr - m.ma5_prev), slope_floor)
            and (
                big_body_breakout
                or (
                    sign * (m.close_price - m.open_price) >= 0.30 * m.atr
                    and sign * (m.close_price - (m.kc_upper if side == "LONG" else m.kc_lower)) <= 0
                )
            )
        )
        valid_outer_breakout = big_body_breakout or cls.is_outer_rail_continuation(m, side)
        # 通道獲利空間足夠（Room Ready，拒絕貼軌內側追單，如 09:13 龍蝦買在 kc_upper 下方僅剩 5.7% 空間）：
        # 做多：若未突破 KC 上軌 (close <= kc_upper) 且非從中軌以下起漲之底部反轉/穿中軌 (open > kc_middle)，
        #       價格到 KC 上軌剩餘空間必須至少 20% 通道寬度 (kc_position <= 0.80)，貼著上軌內側無利可圖嚴禁追多！
        # 做空：若未跌破 KC 下軌 (close >= kc_lower) 且非從中軌以上起跌之頂部反轉/穿中軌 (open < kc_middle)，
        #       價格到 KC 下軌剩餘空間必須至少 20% 通道寬度 (kc_position >= 0.20)，貼著下軌內側無利可圖嚴禁追空！
        kc_width = m.kc_upper - m.kc_lower
        if math.isfinite(kc_width) and kc_width > 0 and not pivot_reversal:
            kc_pos = (m.close_price - m.kc_lower) / kc_width
            if (
                side == "LONG"
                and m.close_price <= m.kc_upper
                and m.open_price > m.kc_middle
                and kc_pos > 0.80
                and not math.isclose(kc_pos, 0.80, rel_tol=1e-9, abs_tol=0)
            ):
                return block("BLOCKED_LONG_INSUFFICIENT_KC_ROOM_ANTI_CHASE")
            if (
                side == "SHORT"
                and m.close_price >= m.kc_lower
                and m.open_price < m.kc_middle
                and kc_pos < 0.20
                and not math.isclose(kc_pos, 0.20, rel_tol=1e-9, abs_tol=0)
            ):
                return block("BLOCKED_SHORT_INSUFFICIENT_KC_ROOM_ANTI_CHASE")

        if is_meme_breakout_symbol(m.symbol):
            distance_atr = meme_distance_atr(m.symbol, m.atr)
            directional_and_aligned = (
                sign * (m.close_price - m.open_price) > 0
                and sign * (m.ma5_curr - m.ma5_prev) > 0
                and sign * (m.ma5_curr - m.ma15_curr) > 0
            )
            if directional_and_aligned:
                if side == "SHORT" and m.close_price - m.kc_lower < 0 and not valid_outer_breakout:
                    return block("VETO_SHORT_BELOW_KC_LOWER_ANTI_CHASE")
                if side == "LONG" and m.close_price - m.kc_upper > 0 and not valid_outer_breakout:
                    return block("VETO_LONG_ABOVE_KC_UPPER_ANTI_CHASE")
            if slope_aligned_breakout and abs(m.quote - m.close_price) <= 0.25 * distance_atr:
                if (
                    abs(m.open_price - m.ma15_curr) > distance_atr
                    and not math.isclose(
                        abs(m.open_price - m.ma15_curr), distance_atr,
                        rel_tol=1e-12, abs_tol=0,
                    )
                ):
                    return block(f"BLOCKED_{side}_MA15_EXTENSION_1_ATR")
            elif any(
                abs(price - m.ma15_curr) > distance_atr
                and not math.isclose(
                    abs(price - m.ma15_curr), distance_atr,
                    rel_tol=1e-12, abs_tol=0,
                )
                for price in (m.quote, m.close_price)
            ):
                return block(f"BLOCKED_{side}_MA15_EXTENSION_1_ATR")
        reverse_entry = priority == REVERSE_TRIGGER
        macro = str(m.macro_trend).upper()
        if macro not in ("BULLISH", "BEARISH", "RANGE", "NEUTRAL"):
            return block("VETO_MACRO_TREND_UNKNOWN")
        if pivot_reversal:
            location_veto = cls.pivot_kc_location_veto(m, side)
            if location_veto:
                return block(location_veto)
            details["macro_kc_position_policy"] = "CONFIRMED_PIVOT_EXCEPTION"
            bias_limit = PIVOT_MAX_ABSOLUTE_BIAS_ATR * m.atr
            if any(
                abs(price - m.ma15_curr) > bias_limit
                and not math.isclose(
                    abs(price - m.ma15_curr), bias_limit, rel_tol=1e-12, abs_tol=0,
                )
                for price in (m.close_price, m.quote)
            ):
                return block(f"VETO_{side}_PIVOT_MA15_ABSOLUTE_BIAS")
            body = abs(m.close_price - m.open_price)
            upper_wick = m.high_price - max(m.open_price, m.close_price)
            lower_wick = min(m.open_price, m.close_price) - m.low_price
            if (
                side == "LONG" and upper_wick > body
                or side == "SHORT" and lower_wick > body
            ):
                return block(f"VETO_{side}_PIVOT_EXHAUSTION_WICK")
        if macro == ("BEARISH" if side == "LONG" else "BULLISH"):
            return block(f"BLOCKED_{side}_MACRO_TREND_CONFLICT")
        squeeze_breakout_long = side == "LONG" and cls.is_squeeze_breakout_long(m)
        # A fresh top/bottom turn bypasses only the lagging KC slope filter.
        trend_block = cls.swing_trend_block(m, side, priority)
        if trend_block:
            return block(trend_block)
        required_levels = (m.kc_middle, m.ma15_curr)
        if any(sign * (price - level) <= 0
               for price in (m.quote, m.close_price) for level in required_levels):
            return block(f"BLOCKED_{side}_PRICE_MIDDLE_MA15")
        # Owner 2026-11-03: every channel needs MA5 already beyond MA15 on both bars, sloping
        # with the side; the fresh-cross bar never opens unless it is a valid priority cross.
        touch_breakout_cross = (
            math.isclose(m.ma5_curr, m.ma15_curr, rel_tol=1e-9, abs_tol=1e-12)
            and sign * (m.ma5_prev - m.ma15_prev) < 0
            and big_body_breakout
            and sign * (m.open_price - m.ma15_curr) < 0
            and cls.is_ma_cross_slope_entry(m, side)
        )
        if sign * (m.ma5_curr - m.ma5_prev) <= 0 or (
            sign * (m.ma5_curr - m.ma15_curr) <= 0 and not touch_breakout_cross
        ):
            return block(f"BLOCKED_{side}_MA5_DIRECTION_OR_ALIGNMENT")
        fresh_cross = sign * (m.ma5_prev - m.ma15_prev) <= 0 or touch_breakout_cross
        fresh_cross_priority = (
            priority in {
                "TRIGGER_TOP_PIVOT_CROSS", "TRIGGER_BOTTOM_PIVOT_CROSS",
                MA_CROSS_SLOPE_TRIGGER,
            }
            or cls.is_ma_cross_slope_entry(m, side)
        )
        if fresh_cross and not fresh_cross_priority:
            return block(f"BLOCKED_{side}_MA5_MA15_FRESH_CROSS")
        if sign * (m.close_price - m.open_price) <= 0:
            return block(f"BLOCKED_{side}_DIRECTIONAL_CANDLE")
        if is_meme_breakout_symbol(m.symbol):
            if side == "SHORT" and m.close_price - m.kc_lower < 0 and not valid_outer_breakout:
                return block("VETO_SHORT_BELOW_KC_LOWER_ANTI_CHASE")
            if side == "LONG" and m.close_price - m.kc_upper > 0 and not valid_outer_breakout:
                return block("VETO_LONG_ABOVE_KC_UPPER_ANTI_CHASE")
        extreme = max(m.quote, m.close_price) if sign == 1 else min(m.quote, m.close_price)
        # Anti-bottom/top chase: a fresh band break is measured from where the bar started.
        distance_atr = meme_distance_atr(m.symbol, m.atr)
        if not is_meme_breakout_symbol(m.symbol):
            chase_extension = (
                m.kc_lower - extreme if side == "SHORT" else extreme - m.kc_upper
            )
            if chase_extension > 0.5 * m.atr and not cls.is_big_body_breakout(m, side):
                reason = (
                    "BLOCKED_SHORT_CHASE_KC_LOWER_EXTENSION"
                    if side == "SHORT" else "BLOCKED_LONG_CHASE_KC_UPPER_EXTENSION"
                )
                return block(reason)

        def over_limit(value, multiple):
            cap = multiple * distance_atr
            return value > cap and not math.isclose(value, cap, rel_tol=1e-12, abs_tol=0)

        open_bias_breakout = slope_aligned_breakout and abs(m.quote - m.close_price) <= 0.25 * distance_atr
        ma15_limit = (
            3.5 if open_bias_breakout
            else MEME_BREAKOUT_MA15_EXTENSION_ATR if big_body_breakout
            else MEME_MA15_EXTENSION_ATR
        )
        extreme_over = over_limit(sign * (extreme - m.ma15_curr), MEME_MA15_EXTENSION_ATR)
        if over_limit(sign * (extreme - m.ma15_curr), ma15_limit):
            return block(f"BLOCKED_{side}_MA15_EXTENSION_1_ATR")
        bias_price = m.open_price if (big_body_breakout or open_bias_breakout) else extreme
        if over_limit(sign * (bias_price - m.ma15_curr), MEME_BREAKOUT_MA15_EXTENSION_ATR if (big_body_breakout or open_bias_breakout) else MEME_MA15_EXTENSION_ATR):
            return block(f"BLOCKED_{side}_MA15_EXTENSION_1_ATR")
        # When the bias-from-open exemption was actually needed, cap the rail extension tightly.
        breakout_priority = priority in {
            "TRIGGER_EARLY_BREAKOUT_CHANNEL",
            "TRIGGER_EARLY_BREAKDOWN_CHANNEL",
            "TRIGGER_1145_EARLY_BREAKOUT",
            "TRIGGER_1148_SQUEEZE_BREAKOUT",
            "TRIGGER_A_EARLY_BODY_BREAKDOWN",
            "TRIGGER_A_SQUEEZE_EXPANSION",
        }
        # 🚨 阻斷天花板追多 / 地板追空 (Anti-FOMO Overbought / Oversold Ceiling Block)
        # 1. 天花板追多一票否決：當 1m 收盤價遠離 KC 上軌 (close > KC_Upper + 0.8 * ATR)，
        #    且 MA5 距離 MA15 乖離過大時，100% 傳回 False 阻斷開多，嚴禁買在長陽線頂部天花板！
        # 2. 地板追空一票否決：當 1m 收盤價遠離 KC 下軌 (close < KC_Lower - 0.8 * ATR)，
        #    且 MA5 距離 MA15 乖離過大時，100% 傳回 False 阻斷開空，嚴禁空在長陰線底部地板！
        if side == "LONG" and not breakout_priority and (m.close_price > m.kc_upper + 0.8 * m.atr or m.quote > m.kc_upper + 0.8 * m.atr):
            return block("BLOCKED_LONG_OVERBOUGHT_CEILING_FOMO")
        if side == "SHORT" and not breakout_priority and (m.close_price < m.kc_lower - 0.8 * m.atr or m.quote < m.kc_lower - 0.8 * m.atr):
            return block("BLOCKED_SHORT_OVERSOLD_FLOOR_FOMO")

        band_limit = (
            3.0 if breakout_priority
            else 0.8 if big_body_breakout and extreme_over
            else 1.5 if squeeze_breakout_long
            else 1.2 if side == "LONG" and cls.is_expansion(m, side)
            else 0.8
        )
        band = m.kc_upper if side == "LONG" else m.kc_lower
        extension = sign * (extreme - band)
        limit = band_limit * distance_atr
        if extension > limit and not math.isclose(extension, limit, rel_tol=1e-12, abs_tol=0):
            return block(f"BLOCKED_{side}_KC_BAND_EXTENSION")
        return GateDecision(True, "FILTER_PASS", "PASS_MEME_ENTRY_FILTERS", details)

    # ─────────────────────────────────────────────────────────────
    # 🟢 開多審查門檻 (Open Long Gate)
    # ─────────────────────────────────────────────────────────────
    @classmethod
    def evaluate_open_long(cls, m: MarketSnapshot, is_in_cooldown: bool = False) -> GateDecision:
        # 🚨 第一道最高權限硬鎖：必須交由 MasterOpenGatekeeper 執行一票否決驗證
        top_decision = MasterOpenGatekeeper.validate_open_signal(m, "LONG", is_in_cooldown=is_in_cooldown)
        if not top_decision.can_execute:
            return top_decision

        filtered = cls.evaluate_entry_filters(m, "LONG", is_in_cooldown)
        if not filtered.can_execute:
            return filtered
        details = filtered.details
        trigger_hit = None
        if cls.is_bottom_pivot_long(m):
            trigger_hit = "TRIGGER_BOTTOM_PIVOT_CROSS"
        elif cls.is_early_breakout_long(m):
            trigger_hit = "TRIGGER_1145_EARLY_BREAKOUT"
        elif cls.is_squeeze_breakout_long(m):
            trigger_hit = "TRIGGER_1148_SQUEEZE_BREAKOUT"
        elif cls.is_expansion(m, "LONG"):
            trigger_hit = "TRIGGER_A_SQUEEZE_EXPANSION"
        elif cls.is_early_long(m):
            trigger_hit = "TRIGGER_EARLY_0748_BREAKOUT"
        elif cls.is_outer_rail_continuation(m, "LONG"):
            trigger_hit = "TRIGGER_OUTER_RAIL_CONTINUATION_LONG"
        elif (
            m.kc_middle <= m.low_price <= m.kc_middle + 0.35 * m.atr
            and m.ma5_prev > m.ma15_prev and m.ma5_curr > m.ma15_curr
        ):
            trigger_hit = "TRIGGER_B_PULLBACK_KC_MIDDLE_BOUNCE"
        elif m.open_price <= m.ma15_curr < m.close_price:
            trigger_hit = "TRIGGER_C_EARLY_MA15_BREAKOUT"
        elif cls.is_slope_aligned_trend_entry(m, "LONG"):
            trigger_hit = "TRIGGER_ALIGNED_TREND_SLOPES"
        if not trigger_hit:
            trigger_hit = cls.priority_channel(m, "LONG")

        if not trigger_hit:
            return GateDecision(False, "HOLD", "WAIT_VALID_LONG_TRIGGER", details)

        logger.info(f"🚀 [GATE_LONG_PASS] {m.symbol} 全條件審查通過！授權開多 ({trigger_hit})")
        logger.info("[UI_DISPLAY: 買多] symbol=%s trigger=%s stage=CANDIDATE_NOT_FILL", m.symbol, trigger_hit)
        return GateDecision(True, "OPEN_LONG", f"AUTHORIZED_LONG:{trigger_hit}", {**details, "trigger": trigger_hit})

    # ─────────────────────────────────────────────────────────────
    # 🔴 開空審查門檻 (Open Short Gate)
    # ─────────────────────────────────────────────────────────────
    @classmethod
    def evaluate_open_short(cls, m: MarketSnapshot, is_in_cooldown: bool = False) -> GateDecision:
        # 🚨 第一道最高權限硬鎖：必須交由 MasterOpenGatekeeper 執行一票否決驗證
        top_decision = MasterOpenGatekeeper.validate_open_signal(m, "SHORT", is_in_cooldown=is_in_cooldown)
        if not top_decision.can_execute:
            return top_decision

        filtered = cls.evaluate_entry_filters(m, "SHORT", is_in_cooldown)
        if not filtered.can_execute:
            return filtered
        details = filtered.details
        kc_slope = m.kc_middle - m.kc_middle_prev
        trigger_hit = None
        is_top_reversal = (
            ((m.ma5_prev >= m.kc_middle_prev and m.ma5_curr < m.kc_middle)
             or (m.ma5_prev >= m.ma15_prev and m.ma5_curr < m.ma15_curr))
            and (m.close_price < m.open_price)
            and (kc_slope < 0)
        )
        if cls.is_top_pivot_short(m):
            trigger_hit = "TRIGGER_TOP_PIVOT_CROSS"
        elif cls.is_big_body_breakout(m, "SHORT"):
            trigger_hit = "TRIGGER_EARLY_BREAKDOWN_CHANNEL"
        elif cls.is_expansion(m, "SHORT"):
            trigger_hit = "TRIGGER_A_EARLY_BODY_BREAKDOWN"
        elif is_top_reversal:
            trigger_hit = "TRIGGER_B_TOP_REVERSAL_CROSS"
        elif (
            m.close_price < m.kc_lower
            and m.quote < m.kc_lower
            and m.close_price < m.open_price
            and (m.kc_middle - m.kc_middle_prev) < 0
            and m.ma15_curr < m.ma15_prev
            and m.ma5_curr < m.ma5_prev
        ):
            trigger_hit = "TRIGGER_B_BREAKDOWN_LOWER_RAIL"
        elif (
            m.close_price < m.kc_lower
            and m.quote < m.kc_lower
            and m.close_price < m.open_price
            and (m.kc_middle - m.kc_middle_prev) < 0
            and m.ma15_curr < m.ma15_prev
        ):
            trigger_hit = "TRIGGER_C_BREAKDOWN_LOWER_RAIL"
        elif cls.is_slope_aligned_trend_entry(m, "SHORT"):
            trigger_hit = "TRIGGER_ALIGNED_TREND_SLOPES"
        elif cls.is_outer_rail_continuation(m, "SHORT"):
            trigger_hit = "TRIGGER_OUTER_RAIL_CONTINUATION_SHORT"

        if not trigger_hit:
            trigger_hit = cls.priority_channel(m, "SHORT")

        if not trigger_hit:
            return GateDecision(False, "HOLD", "WAIT_VALID_SHORT_TRIGGER", details)

        logger.info(f"🚀 [GATE_SHORT_PASS] {m.symbol} 全條件審查通過！授權開空 ({trigger_hit})")
        logger.info("[UI_DISPLAY: 賣空] symbol=%s trigger=%s stage=CANDIDATE_NOT_FILL", m.symbol, trigger_hit)
        return GateDecision(True, "OPEN_SHORT", f"AUTHORIZED_SHORT:{trigger_hit}", {**details, "trigger": trigger_hit})

    # ─────────────────────────────────────────────────────────────
    # 🛡️ 持倉呼吸與平倉保護審查 (Holding Protection Gate)
    # ─────────────────────────────────────────────────────────────
    @classmethod
    def evaluate_position_exit(
        cls,
        p: PositionSnapshot,
        m: MarketSnapshot,
        candidate_exit_reason: str,
        current_time_ms: float,
        *,
        frame=None,
    ) -> GateDecision:
        details = {
            "symbol": p.symbol, "side": p.side, "bars_held": p.bars_held,
            "peak_roe": p.peak_roe_pct, "reason": candidate_exit_reason,
        }

        def hold(reason):
            logger.info("[VETO_HOLD_%s] symbol=%s reason=%s", p.side, p.symbol, reason)
            if p.side == "SHORT":
                logger.info("[UI_DISPLAY: 續抱] symbol=%s reason=%s", p.symbol, reason)
            return GateDecision(False, "HOLD", reason, details)
        if (p.side not in ("LONG", "SHORT") or p.symbol != m.symbol
                or any(not math.isfinite(v) or v <= 0 for v in (
                    m.atr, m.quote, m.open_price, m.close_price, m.ma5_curr, m.ma15_curr,
                    m.kc_upper, m.kc_lower, m.kc_middle, p.entry_price,
                ))
                or not math.isfinite(p.open_timestamp_ms)
                or not m.kc_lower < m.kc_middle < m.kc_upper):
            return hold("VETO_EXIT_INVALID_DATA")
        from core.symbol_aliases import is_meme_breakout_symbol
        if is_meme_breakout_symbol(p.symbol):
            if candidate_exit_reason == "EXIT_BY_EXTREME_WATERFALL":
                from core.services.exits.abnormal_candle_exit import live_adverse_waterfall_evidence
                evidence = live_adverse_waterfall_evidence({
                    "symbol": p.symbol, "side": p.side, "open_timestamp": p.open_timestamp_ms,
                }, frame, m.quote, now_ms=current_time_ms)
                if evidence is not None:
                    return GateDecision(True, "CLOSE_POSITION", candidate_exit_reason, evidence)
                return hold("VETO_UNCONFIRMED_WATERFALL")
            if candidate_exit_reason == "OPPOSITE_KC_BAND_BREACH":
                return hold("VETO_OPPOSITE_RAIL_REQUIRES_WATERFALL_OR_TRUE_PIVOT")
        # Generic strategies retain their existing band-breach candidate.
        if candidate_exit_reason == "OPPOSITE_KC_BAND_BREACH" and (
            (p.side == "LONG" and m.quote < m.kc_lower)
            or (p.side == "SHORT" and m.quote > m.kc_upper)
        ):
            return GateDecision(True, "CLOSE_POSITION", candidate_exit_reason, details)
        if (not math.isfinite(current_time_ms) or p.open_timestamp_ms < 0
                or current_time_ms < p.open_timestamp_ms
                or (p.open_timestamp_ms > 0 and int(current_time_ms // 60_000) == int(p.open_timestamp_ms // 60_000))
                or p.bars_held < 5 or not m.is_closed):
            return hold("VETO_HOLD_MIN_5_COMPLETED_BARS")
        from core.services.exits.strict_true_pivot import PROFIT_PIVOT_REASONS
        sign = 1 if p.side == "LONG" else -1
        extreme = p.highest_price_since_entry if p.side == "LONG" else p.lowest_price_since_entry
        if math.isfinite(extreme) and extreme > 0:
            gain = sign * (extreme - p.entry_price)
            retreat = sign * (extreme - m.close_price)
            details.update(
                peak_gain_atr=gain / m.atr, retreat_atr=retreat / m.atr,
                trailing_active=gain > 2.5 * m.atr,
                trailing_qualified=gain > 2.5 * m.atr and retreat > 1.5 * m.atr,
            )
        # 🎯 Phase S2 鐵律：當已收線確認真峰谷 (has_confirmed_pivot) 且觸發真峰谷出場時，
        # 不得被 KC 中軌緩衝區 (050_ATR_BUFFER) 或同向 K 棒否決，必須在峰谷及時平倉！
        is_early_confirmed_pivot = (
            p.has_confirmed_pivot
            and candidate_exit_reason in ("EARLY_PIVOT_PEAK_EXIT", "EARLY_PIVOT_VALLEY_EXIT")
            and (
                (p.estimated_net_pnl is not None and math.isfinite(p.estimated_net_pnl) and p.estimated_net_pnl > 0)
                or (p.estimated_net_pnl is None and p.peak_roe_pct > 0 and sign * (m.quote - p.entry_price) > 0)
            )
        )
        if is_early_confirmed_pivot:
            return GateDecision(True, "CLOSE_POSITION", candidate_exit_reason, details)
        if sign * (m.close_price - m.kc_middle) >= -0.50 * m.atr:
            return hold(f"VETO_HOLD_{p.side}_050_ATR_BUFFER")
        if sign * (m.close_price - m.open_price) >= 0:
            return hold(f"VETO_HOLD_{p.side}_DIRECTIONAL_CANDLE")
        if sign * (m.ma5_curr - m.ma15_curr) >= 0:
            return hold(f"VETO_HOLD_{p.side}_MA5_MA15_NOT_REVERSED")
        if (not p.has_confirmed_pivot or p.estimated_net_pnl is None
                or not math.isfinite(p.estimated_net_pnl) or p.estimated_net_pnl <= 0):
            return hold("VETO_HOLD_TRUE_PIVOT_AND_POSITIVE_NET_REQUIRED")
        if candidate_exit_reason not in PROFIT_PIVOT_REASONS:
            return hold("VETO_HOLD_EXIT_AUTHORITY_REQUIRED")
        return GateDecision(True, "CLOSE_POSITION", candidate_exit_reason, details)


class MasterOpenGatekeeper(MasterGateFunnel):
    """Master Open Gatekeeper enforcing Multi-Bar Structure Verification (HL / LH),
    Breakout Verification Gate (True Breakout vs Overbought/Oversold Trap),
    Trend Direction Lock, and Anti-Oversold/Overbought FOMO hard blocks."""

    @staticmethod
    def extract_multi_bar_swing_points(closed_df: Optional[pd.DataFrame]) -> Dict[str, Optional[float]]:
        """從多根已收線 K 棒拉遠提取波段峰谷結構與 KC 通道寬度特徵。"""
        result: Dict[str, Optional[float]] = {
            "current_swing_low": None,
            "prev_swing_low": None,
            "current_swing_high": None,
            "prev_swing_high": None,
            "kc_width_prev": None,
            "kc_width_ma5": None,
            "kc_lower_prev": None,
            "kc_upper_prev": None,
        }
        if closed_df is None or not isinstance(closed_df, pd.DataFrame) or len(closed_df) < 2:
            return result
        try:
            if "kc_upper" in closed_df.columns and "kc_lower" in closed_df.columns:
                widths = closed_df["kc_upper"].astype(float) - closed_df["kc_lower"].astype(float)
                result["kc_width_prev"] = float(widths.iloc[-2])
                result["kc_lower_prev"] = float(closed_df["kc_lower"].iloc[-2])
                result["kc_upper_prev"] = float(closed_df["kc_upper"].iloc[-2])
                if len(widths) >= 6:
                    result["kc_width_ma5"] = float(widths.iloc[-6:-1].mean())
                elif len(widths) >= 2:
                    result["kc_width_ma5"] = float(widths.iloc[:-1].mean())
            if len(closed_df) >= 8:
                lows = closed_df["low"].astype(float)
                highs = closed_df["high"].astype(float)
                split_idx = max(3, len(closed_df) // 2)
                prev_lows = lows.iloc[:-split_idx]
                curr_lows = lows.iloc[-split_idx:]
                prev_highs = highs.iloc[:-split_idx]
                curr_highs = highs.iloc[-split_idx:]
                if len(prev_lows) > 0 and len(curr_lows) > 0:
                    result["prev_swing_low"] = float(prev_lows.min())
                    result["current_swing_low"] = float(curr_lows.min())
                    result["prev_swing_high"] = float(prev_highs.max())
                    result["current_swing_high"] = float(curr_highs.max())
        except Exception:
            pass
        return result

    @staticmethod
    def resolve_kc_header_mode(m: MarketSnapshot) -> str:
        """解析當前 KC 趨勢標頭模式 ('LONG_ONLY', 'SHORT_ONLY', 'NEUTRAL')"""
        if m.kc_middle > m.kc_middle_prev or str(m.macro_trend).upper() == "BULLISH":
            return "LONG_ONLY"
        if m.kc_middle < m.kc_middle_prev or str(m.macro_trend).upper() == "BEARISH":
            return "SHORT_ONLY"
        return "NEUTRAL"

    @classmethod
    def verify_breakout_authenticity(
        cls,
        m: MarketSnapshot,
        side: str,
        *,
        kc_width_ma5: Optional[float] = None,
        kc_width_prev: Optional[float] = None,
        kc_lower_prev: Optional[float] = None,
        current_swing_low: Optional[float] = None,
        prev_swing_low: Optional[float] = None,
        current_swing_high: Optional[float] = None,
        prev_swing_high: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """
        🔍 破軌真偽識別模組 (Breakout Verification Gate)：
        區分穿透 KC 軌道是【真趨勢爆發起點】還是【超買/超賣末端陷阱】：

        ⚡ 一、 做多：識別「真多頭爆發起點」(True Bullish Breakout)
        當 1m 收線 (is_bar_closed == True) 且價格突破 KC 上軌 (close_price > kc_upper) 時，必須滿足：
        1. 【通道張口驗證】：(KC_Upper - KC_Lower) > 1.2 * KC_Width_MA5 (或 > 1.25 * KC_Width_prev)
        2. 【均線斜率驗證】：(MA5_curr - MA5_prev) >= 0.15 * ATR 且 MA5 >= MA15
        3. 【大結構底底高】：Current_Swing_Low > Prev_Swing_Low
        🚨 若通道未張口、MA5 無斜率或大結構破位，100% 認定為天花板超買陷阱，阻斷開多！

        🛡️ 二、 做空：阻斷「超賣末端陷阱/地板空」(Anti-Oversold Trap Gate)
        當評估做空（尤其價格跌破 KC 下軌 close_price < kc_lower）時，出現任一特徵即 100% 阻斷 OPEN_SHORT：
        1. 【長下影線否決】：(min(open, close) - low) >= 0.4 * ATR
        2. 【標頭單向否決】：標頭大趨勢為「KC 向上 (只評估多單)」(kc_middle >= kc_middle_prev 或 macro_trend == BULLISH)
        3. 【通道平走否決】：KC 通道未拉開張口，或 MA5 斜率趨近走平 ((ma5_prev - ma5_curr) < 0.15 * ATR)
        """
        normalized = str(side or "").upper()
        kc_width_curr = m.kc_upper - m.kc_lower
        w_ma5 = kc_width_ma5 if kc_width_ma5 is not None else m.kc_width_ma5
        w_prev = kc_width_prev if kc_width_prev is not None else m.kc_width_prev
        l_prev = kc_lower_prev if kc_lower_prev is not None else m.kc_lower_prev
        curr_sl = current_swing_low if current_swing_low is not None else m.current_swing_low
        prev_sl = prev_swing_low if prev_swing_low is not None else m.prev_swing_low
        curr_sh = current_swing_high if current_swing_high is not None else m.current_swing_high
        prev_sh = prev_swing_high if prev_swing_high is not None else m.prev_swing_high

        if normalized in ("LONG", "OPEN_LONG"):
            if curr_sl is not None and prev_sl is not None and curr_sl <= prev_sl:
                return False, "BLOCKED_LONG_MULTI_BAR_STRUCTURE_NOT_HL"
            if m.close_price > m.kc_upper:
                # 1. 通道張口驗證
                if w_ma5 is not None and w_ma5 > 0:
                    if kc_width_curr <= 1.2 * w_ma5 and not math.isclose(kc_width_curr, 1.2 * w_ma5, rel_tol=1e-9):
                        return False, "BLOCKED_LONG_BREAKOUT_CHANNEL_NOT_EXPANDING"
                elif w_prev is not None and w_prev > 0:
                    if kc_width_curr <= 1.25 * w_prev and not math.isclose(kc_width_curr, 1.25 * w_prev, rel_tol=1e-9):
                        return False, "BLOCKED_LONG_BREAKOUT_CHANNEL_NOT_EXPANDING"
                # 2. 均線斜率驗證：(MA5_curr - MA5_prev) >= 0.15 * ATR 且 MA5 >= MA15
                ma5_slope = m.ma5_curr - m.ma5_prev
                if ma5_slope < 0.15 * m.atr and not math.isclose(ma5_slope, 0.15 * m.atr, rel_tol=1e-9):
                    return False, "BLOCKED_LONG_BREAKOUT_MA5_SLOPE_WEAK"
                if m.ma5_curr < m.ma15_curr and not math.isclose(m.ma5_curr, m.ma15_curr, rel_tol=1e-9):
                    return False, "BLOCKED_LONG_MA5_NOT_ABOVE_MA15"
            return True, "PASS_TRUE_BULLISH_BREAKOUT"

        if normalized in ("SHORT", "OPEN_SHORT"):
            # 1. 標頭單向否決：標頭為 KC 向上時 100% 阻斷開空
            if m.kc_middle >= m.kc_middle_prev:
                return False, "BLOCKED_SHORT_ON_BULLISH_TREND"
            if str(m.macro_trend).upper() == "BULLISH":
                return False, "BLOCKED_SHORT_MACRO_TREND_CONFLICT"
            if curr_sh is not None and prev_sh is not None and curr_sh >= prev_sh:
                return False, "BLOCKED_SHORT_MULTI_BAR_STRUCTURE_NOT_LH"
            # 2. 長下影線否決：(min(open, close) - low) >= 0.4 * ATR (妖幣採用 meme_distance_atr 避免微幅跳價誤殺)
            from core.symbol_aliases import is_meme_breakout_symbol
            effective_atr = meme_distance_atr(m.symbol, m.atr) if is_meme_breakout_symbol(m.symbol) else m.atr
            lower_wick = min(m.open_price, m.close_price) - m.low_price
            if lower_wick >= 0.4 * effective_atr or math.isclose(lower_wick, 0.4 * effective_atr, rel_tol=1e-9):
                return False, "BLOCKED_SHORT_LONG_LOWER_WICK_V_TRAP"
            # 3. 跌破下軌時之通道張口與均線斜率審查
            if m.close_price < m.kc_lower:
                if w_ma5 is not None and w_ma5 > 0:
                    if kc_width_curr <= 1.2 * w_ma5 and not math.isclose(kc_width_curr, 1.2 * w_ma5, rel_tol=1e-9):
                        return False, "BLOCKED_SHORT_BREAKDOWN_CHANNEL_FLAT_OR_NARROW"
                elif w_prev is not None and w_prev > 0:
                    if kc_width_curr <= 1.2 * w_prev and not math.isclose(kc_width_curr, 1.2 * w_prev, rel_tol=1e-9):
                        return False, "BLOCKED_SHORT_BREAKDOWN_CHANNEL_FLAT_OR_NARROW"
                if l_prev is not None and m.kc_lower >= l_prev:
                    return False, "BLOCKED_SHORT_BREAKDOWN_CHANNEL_FLAT_OR_NARROW"
                from core.symbol_aliases import is_meme_breakout_symbol
                is_fresh_non_meme_breakdown = (
                    (not is_meme_breakout_symbol(m.symbol) or cls.is_big_body_breakout(m, "SHORT"))
                    and w_ma5 is None and w_prev is None and l_prev is None
                    and m.open_price > m.kc_lower > m.close_price
                    and m.ma5_curr < m.ma5_prev
                    and m.ma15_curr < m.ma15_prev
                    and m.kc_middle < m.kc_middle_prev
                )
                ma5_down_slope = m.ma5_prev - m.ma5_curr
                if not is_fresh_non_meme_breakdown and ma5_down_slope < 0.15 * m.atr and not math.isclose(ma5_down_slope, 0.15 * m.atr, rel_tol=1e-9):
                    return False, "BLOCKED_SHORT_BREAKDOWN_MA5_SLOPE_WEAK"
            return True, "PASS_TRUE_BEARISH_BREAKDOWN"

        return False, "VETO_INVALID_ENTRY_SIDE"

    @classmethod
    def can_execute_entry(
        cls,
        m: MarketSnapshot,
        side: str,
        is_in_cooldown: bool = False,
        *,
        current_swing_low: Optional[float] = None,
        prev_swing_low: Optional[float] = None,
        current_swing_high: Optional[float] = None,
        prev_swing_high: Optional[float] = None,
        kc_width_ma5: Optional[float] = None,
        kc_width_prev: Optional[float] = None,
        kc_lower_prev: Optional[float] = None,
        has_position: Optional[bool] = None,
        position_side: Optional[str] = None,
        position_size: Optional[float] = None,
        position: Optional[Dict[str, Any]] = None,
        frame: Optional[pd.DataFrame] = None,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Unified entry prefilter interface returning (can_execute, reason, details)."""
        decision = cls.validate_open_signal(
            m,
            side,
            is_in_cooldown=is_in_cooldown,
            current_swing_low=current_swing_low,
            prev_swing_low=prev_swing_low,
            current_swing_high=current_swing_high,
            prev_swing_high=prev_swing_high,
            kc_width_ma5=kc_width_ma5,
            kc_width_prev=kc_width_prev,
            kc_lower_prev=kc_lower_prev,
            has_position=has_position,
            position_side=position_side,
            position_size=position_size,
            position=position,
            frame=frame,
        )
        return decision.can_execute, decision.reason, decision.details

    @classmethod
    def validate_open_signal(
        cls,
        m: MarketSnapshot,
        side: str,
        is_in_cooldown: bool = False,
        *,
        current_swing_low: Optional[float] = None,
        prev_swing_low: Optional[float] = None,
        current_swing_high: Optional[float] = None,
        prev_swing_high: Optional[float] = None,
        kc_width_ma5: Optional[float] = None,
        kc_width_prev: Optional[float] = None,
        kc_lower_prev: Optional[float] = None,
        has_position: Optional[bool] = None,
        position_side: Optional[str] = None,
        position_size: Optional[float] = None,
        position: Optional[Dict[str, Any]] = None,
        frame: Optional[pd.DataFrame] = None,
    ) -> GateDecision:
        normalized = str(side or "").upper()
        extracted = cls.extract_multi_bar_swing_points(frame) if frame is not None else {}
        curr_sl = (
            current_swing_low
            if current_swing_low is not None
            else (m.current_swing_low if m.current_swing_low is not None else extracted.get("current_swing_low"))
        )
        prev_sl = (
            prev_swing_low
            if prev_swing_low is not None
            else (m.prev_swing_low if m.prev_swing_low is not None else extracted.get("prev_swing_low"))
        )
        curr_sh = (
            current_swing_high
            if current_swing_high is not None
            else (m.current_swing_high if m.current_swing_high is not None else extracted.get("current_swing_high"))
        )
        prev_sh = (
            prev_swing_high
            if prev_swing_high is not None
            else (m.prev_swing_high if m.prev_swing_high is not None else extracted.get("prev_swing_high"))
        )
        w_ma5 = (
            kc_width_ma5
            if kc_width_ma5 is not None
            else (m.kc_width_ma5 if m.kc_width_ma5 is not None else extracted.get("kc_width_ma5"))
        )
        w_prev = (
            kc_width_prev
            if kc_width_prev is not None
            else (m.kc_width_prev if m.kc_width_prev is not None else extracted.get("kc_width_prev"))
        )
        l_prev = (
            kc_lower_prev
            if kc_lower_prev is not None
            else (m.kc_lower_prev if m.kc_lower_prev is not None else extracted.get("kc_lower_prev"))
        )
        pos_qty = float(position_size if position_size is not None else m.position_size or 0.0)
        pos_side = str(position_side if position_side is not None else (m.position_side or "")).upper()
        if isinstance(position, dict):
            pos_qty = max(
                pos_qty,
                abs(float(position.get("qty") or position.get("amount") or position.get("size") or 0.0)),
            )
            if not pos_side and position.get("side"):
                pos_side = str(position.get("side")).upper()
        already_holding = bool(
            (has_position is True)
            or m.has_position
            or pos_qty > 0.0
            or pos_side in ("LONG", "SHORT", "OPEN_LONG", "OPEN_SHORT")
        )
        details: Dict[str, Any] = {
            "symbol": m.symbol,
            "quote": m.quote,
            "macro": m.macro_trend,
            "current_swing_low": curr_sl,
            "prev_swing_low": prev_sl,
            "current_swing_high": curr_sh,
            "prev_swing_high": prev_sh,
            "kc_width_curr": m.kc_upper - m.kc_lower,
            "kc_width_ma5": w_ma5,
            "kc_width_prev": w_prev,
            "has_position": already_holding,
            "position_side": pos_side or None,
            "position_size": pos_qty,
        }
        # 🛡️ 零重複開倉硬鎖 (Anti-Pyramiding / Anti-Duplicate Entry Lock)：
        # 當系統已持有同向部位 (如已持有 OPEN_LONG 多單) 時，100% 傳回 False 阻斷任何二次開倉/半山腰追高加碼請求！
        target_side = "LONG" if normalized in ("LONG", "OPEN_LONG") else ("SHORT" if normalized in ("SHORT", "OPEN_SHORT") else "")
        opposite_held = bool(
            already_holding
            and pos_side in ("LONG", "SHORT", "OPEN_LONG", "OPEN_SHORT")
            and (
                (target_side == "LONG" and pos_side in ("SHORT", "OPEN_SHORT"))
                or (target_side == "SHORT" and pos_side in ("LONG", "OPEN_LONG"))
            )
        )
        if already_holding and not opposite_held:
            if normalized in ("LONG", "OPEN_LONG"):
                return GateDecision(False, "HOLD", "BLOCKED_DUPLICATE_LONG_POSITION_ALREADY_HELD", details)
            if normalized in ("SHORT", "OPEN_SHORT"):
                return GateDecision(False, "HOLD", "BLOCKED_DUPLICATE_SHORT_POSITION_ALREADY_HELD", details)
            return GateDecision(False, "HOLD", "BLOCKED_ALREADY_HOLDING_POSITION", details)

        # =========================================================================
        # 🛡️ 一、 做空開倉 (OPEN_SHORT) 100% 一票否決鐵律 (Red Candle & MA Alignment Lock)
        # =========================================================================
        if normalized in ("SHORT", "OPEN_SHORT"):
            # 1. 【100% 必須等待 1m K 棒收盤 (is_bar_closed == True)】
            if not m.is_closed:
                return GateDecision(False, "HOLD", "BLOCKED_SHORT_BAR_NOT_CLOSED", details)

            # 2. 【100% 必須為實體紅 K (陰線, close_price < open_price 且 quote < open_price)】
            #    🚨【綠 K 陽線一票否決】：若當根 K 棒為陽線 (close_price >= open_price 或 quote >= open_price)，
            #    【100% 傳回 False 一票否決】！絕對封鎖在綠 K 暴衝或 V 轉反彈途中逆勢摸頂開空！
            if m.close_price >= m.open_price or m.quote >= m.open_price:
                return GateDecision(False, "HOLD", "BLOCKED_SHORT_DIRECTIONAL_CANDLE", details)

            # 3. 【MA5 必須在 MA15 下方 (ma5_curr < ma15_curr) 且向下發散】
            #    🚨【鐵律】：開空單時 MA5 必須在 MA15 下方（或當根剛死叉跌破且滿足交叉斜率），且收盤價嚴格低於 MA5！
            if m.ma5_curr >= m.ma15_curr and not cls.is_ma_cross_slope_entry(m, "SHORT"):
                return GateDecision(False, "HOLD", "BLOCKED_SHORT_MA5_NOT_BELOW_MA15", details)
            if m.ma5_curr >= m.ma5_prev:
                return GateDecision(False, "HOLD", "BLOCKED_SHORT_MA5_SLOPE_TOO_FLAT", details)
            if m.close_price >= m.ma5_curr:
                return GateDecision(False, "HOLD", "BLOCKED_SHORT_CLOSE_NOT_BELOW_MA5", details)

            # 4. 【KC 中軌向上 100% 絕對禁止開空（最高鐵律）】：
            #    只要 KC 中軌向上 (kc_middle >= kc_middle_prev) 或 棕色 MA15 斜率向上 (ma15_curr > ma15_prev)，
            #    【100% 傳回 False 阻斷開空】！絕不允許在中軌向上升浪中逆勢伸手接刀！
            if m.kc_middle >= m.kc_middle_prev:
                return GateDecision(False, "HOLD", "BLOCKED_SHORT_ON_BULLISH_TREND", details)
            if m.ma15_curr > m.ma15_prev:
                return GateDecision(False, "HOLD", "BLOCKED_SHORT_BY_MA15_UPWARD_SLOPE", details)

            # 5. 【標頭為『KC 向上 (只評估多單)』100% 封鎖開空】：
            header_mode = cls.resolve_kc_header_mode(m)
            if header_mode == "LONG_ONLY" or str(m.macro_trend).upper() == "BULLISH":
                return GateDecision(False, "HOLD", "BLOCKED_SHORT_ON_BULLISH_TREND", details)

            # 6. 結構審查 (頂頂低 LH) 與長下影線過濾
            if curr_sh is not None and prev_sh is not None and curr_sh >= prev_sh:
                return GateDecision(False, "HOLD", "BLOCKED_SHORT_MULTI_BAR_STRUCTURE_NOT_LH", details)
            from core.symbol_aliases import is_meme_breakout_symbol
            effective_atr = meme_distance_atr(m.symbol, m.atr) if is_meme_breakout_symbol(m.symbol) else m.atr
            lower_wick = min(m.open_price, m.close_price) - m.low_price
            if lower_wick >= 0.4 * effective_atr or math.isclose(lower_wick, 0.4 * effective_atr, rel_tol=1e-9):
                return GateDecision(False, "HOLD", "BLOCKED_SHORT_LONG_LOWER_WICK_V_TRAP", details)

            # 7. 破軌真偽檢驗 (通道張口、均線斜率等)
            breakout_ok, breakout_reason = cls.verify_breakout_authenticity(
                m,
                "SHORT",
                kc_width_ma5=w_ma5,
                kc_width_prev=w_prev,
                kc_lower_prev=l_prev,
                current_swing_high=curr_sh,
                prev_swing_high=prev_sh,
            )
            if not breakout_ok:
                return GateDecision(False, "HOLD", breakout_reason, details)

            # 8. 【KC 下軌超賣區 100% 封鎖開空】：
            #    當價格低於 KC 中軌且貼近或低於 KC 下軌 (close <= kc_lower + 0.3 * atr 或 quote <= kc_lower + 0.3 * atr) 時，
            #    【100% 嚴禁地板追空】！（一般標的新鮮下軌突破將交由 evaluate_entry_filters 進行 <= 0.5 ATR 追空距離審查）
            from core.symbol_aliases import is_meme_breakout_symbol
            is_fresh_non_meme_breakdown = (
                (not is_meme_breakout_symbol(m.symbol) or cls.is_big_body_breakout(m, "SHORT"))
                and m.open_price > m.kc_lower > m.close_price
                and m.ma5_curr < m.ma5_prev
                and m.ma15_curr < m.ma15_prev
                and m.kc_middle < m.kc_middle_prev
            )
            oversold_band = m.kc_lower + 0.3 * m.atr
            if not is_fresh_non_meme_breakdown and (m.close_price <= oversold_band or m.quote <= oversold_band):
                return GateDecision(False, "HOLD", "BLOCKED_SHORT_OVERSOLD_FLOOR_FOMO", details)

            # 呼叫底層詳細漏斗驗證觸發點 (免除重複 validate_open_signal 迴圈)
            filtered = cls.evaluate_entry_filters(m, "SHORT", is_in_cooldown)
            if not filtered.can_execute:
                return filtered

            # 舊開空漏斗（若通過一票否決與空間過濾驗證，則進一步驗證細節觸發點）
            if (
                m.is_closed
                and not is_in_cooldown
                and m.kc_middle < m.kc_middle_prev
                and m.ma15_curr <= m.ma15_prev
                and str(m.macro_trend).upper() != "BULLISH"
                and (curr_sh is None or prev_sh is None or curr_sh < prev_sh)
                and m.close_price < m.open_price
                and m.close_price < m.ma5_curr
                and m.ma5_curr < m.ma5_prev
                and m.ma5_curr < m.ma15_curr
                and m.close_price > oversold_band
                and m.ma_cross_count_8 < 3
            ):
                return GateDecision(
                    True,
                    "OPEN_SHORT",
                    "AUTHORIZED_SHORT:TRIGGER_KC_DOWN_MA5_TURN_SHORT",
                    {**details, "trigger": "TRIGGER_KC_DOWN_MA5_TURN_SHORT"},
                )
            kc_slope = m.kc_middle - m.kc_middle_prev
            trigger_hit = None
            is_top_reversal = (
                ((m.ma5_prev >= m.kc_middle_prev and m.ma5_curr < m.kc_middle)
                 or (m.ma5_prev >= m.ma15_prev and m.ma5_curr < m.ma15_curr))
                and (m.close_price < m.open_price)
                and (kc_slope < 0)
            )
            if cls.is_top_pivot_short(m):
                trigger_hit = "TRIGGER_TOP_PIVOT_CROSS"
            elif cls.is_big_body_breakout(m, "SHORT"):
                trigger_hit = "TRIGGER_EARLY_BREAKDOWN_CHANNEL"
            elif cls.is_expansion(m, "SHORT"):
                trigger_hit = "TRIGGER_A_EARLY_BODY_BREAKDOWN"
            elif is_top_reversal:
                trigger_hit = "TRIGGER_B_TOP_REVERSAL_CROSS"
            elif (
                m.close_price < m.kc_lower
                and m.quote < m.kc_lower
                and m.close_price < m.open_price
                and (m.kc_middle - m.kc_middle_prev) < 0
                and m.ma15_curr < m.ma15_prev
                and m.ma5_curr < m.ma5_prev
            ):
                trigger_hit = "TRIGGER_B_BREAKDOWN_LOWER_RAIL"
            elif cls.is_slope_aligned_trend_entry(m, "SHORT"):
                trigger_hit = "TRIGGER_ALIGNED_TREND_SLOPES"
            elif cls.is_outer_rail_continuation(m, "SHORT"):
                trigger_hit = "TRIGGER_OUTER_RAIL_CONTINUATION_SHORT"

            if not trigger_hit:
                trigger_hit = cls.priority_channel(m, "SHORT")

            if not trigger_hit:
                return GateDecision(False, "HOLD", "WAIT_VALID_SHORT_TRIGGER", details)

            logger.info(f"🚀 [GATE_SHORT_PASS] {m.symbol} 全條件審查通過！授權開空 ({trigger_hit})")
            logger.info("[UI_DISPLAY: 賣空] symbol=%s trigger=%s stage=CANDIDATE_NOT_FILL", m.symbol, trigger_hit)
            return GateDecision(True, "OPEN_SHORT", f"AUTHORIZED_SHORT:{trigger_hit}", {**details, "trigger": trigger_hit})

        # =========================================================================
        # 🛡️ 二、 做多開倉 (OPEN_LONG) 100% 一票否決鐵律 (Green Candle Lock)
        # =========================================================================
        if normalized in ("LONG", "OPEN_LONG"):
            # 1. 【100% 必須等待 1m K 棒收盤 (is_bar_closed == True)】
            if not m.is_closed:
                return GateDecision(False, "HOLD", "BLOCKED_LONG_BAR_NOT_CLOSED", details)

            # 2. 【100% 必須為實體綠 K (陽線, close_price > open_price)】
            #    🚨【紅 K 陰線一票否決】：若當根 K 棒為紅 K (陰線, close_price <= open_price)，【100% 嚴禁發送買多 API】！
            if m.close_price <= m.open_price or m.quote <= m.open_price:
                return GateDecision(False, "HOLD", "BLOCKED_LONG_DIRECTIONAL_CANDLE", details)

            # 3. 【MA5 必須在 MA15 上方 (ma5_curr > ma15_curr) 且向上拐頭 (ma5_curr > ma5_prev)】
            #    🚨【鐵律】：開多單時 MA5 必須在 MA15 上方（或當根剛金叉突破且滿足交叉斜率），且收盤價嚴格站上 MA5！
            if m.ma5_curr <= m.ma15_curr and not cls.is_ma_cross_slope_entry(m, "LONG"):
                return GateDecision(False, "HOLD", "BLOCKED_LONG_MA5_NOT_ABOVE_MA15", details)
            if m.ma5_curr <= m.ma5_prev:
                return GateDecision(False, "HOLD", "BLOCKED_LONG_MA5_SLOPE_TOO_FLAT", details)
            if m.close_price <= m.ma5_curr:
                return GateDecision(False, "HOLD", "BLOCKED_LONG_CLOSE_NOT_ABOVE_MA5", details)

            # 4. 順勢與結構過濾：中軌向上、非空頭標頭、底底高 (HL)
            if m.kc_middle <= m.kc_middle_prev:
                return GateDecision(False, "HOLD", "BLOCKED_LONG_ON_BEARISH_TREND", details)
            if str(m.macro_trend).upper() == "BEARISH":
                return GateDecision(False, "HOLD", "BLOCKED_LONG_MACRO_TREND_CONFLICT", details)
            if curr_sl is not None and prev_sl is not None and curr_sl <= prev_sl:
                return GateDecision(False, "HOLD", "BLOCKED_LONG_MULTI_BAR_STRUCTURE_NOT_HL", details)
            if m.ma5_curr <= m.ma15_curr and not cls.is_ma_cross_slope_entry(m, "LONG"):
                return GateDecision(False, "HOLD", "BLOCKED_LONG_MA5_NOT_ABOVE_MA15", details)

            # 5. 破軌真偽檢驗與天花板追多防護
            breakout_ok, breakout_reason = cls.verify_breakout_authenticity(
                m,
                "LONG",
                kc_width_ma5=w_ma5,
                kc_width_prev=w_prev,
                kc_lower_prev=l_prev,
                current_swing_low=curr_sl,
                prev_swing_low=prev_sl,
            )
            if not breakout_ok:
                return GateDecision(False, "HOLD", breakout_reason, details)
            if not (cls.is_big_body_breakout(m, "LONG") and m.open_price <= m.kc_middle) and (
                m.close_price > m.kc_upper + 0.8 * m.atr or m.quote > m.kc_upper + 0.8 * m.atr
            ):
                return GateDecision(False, "HOLD", "BLOCKED_LONG_OVERBOUGHT_CEILING_FOMO", details)

            filtered = cls.evaluate_entry_filters(m, "LONG", is_in_cooldown)
            if not filtered.can_execute:
                return filtered

            # 舊開多漏斗（若通過一票否決與空間過濾驗證，則進一步驗證細節觸發點）
            if (
                m.is_closed
                and not is_in_cooldown
                and m.kc_middle > m.kc_middle_prev
                and str(m.macro_trend).upper() != "BEARISH"
                and (curr_sl is None or prev_sl is None or curr_sl > prev_sl)
                and m.close_price > m.open_price
                and m.close_price > m.ma5_curr
                and m.ma5_curr > m.ma5_prev
                and m.ma5_curr > m.ma15_curr
                and m.ma_cross_count_8 < 3
            ):
                return GateDecision(
                    True,
                    "OPEN_LONG",
                    "AUTHORIZED_LONG:TRIGGER_KC_UP_MA5_TURN_LONG",
                    {**details, "trigger": "TRIGGER_KC_UP_MA5_TURN_LONG"},
                )
            trigger_hit = None
            if cls.is_bottom_pivot_long(m):
                trigger_hit = "TRIGGER_BOTTOM_PIVOT_CROSS"
            elif cls.is_early_breakout_long(m):
                trigger_hit = "TRIGGER_1145_EARLY_BREAKOUT"
            elif cls.is_squeeze_breakout_long(m):
                trigger_hit = "TRIGGER_1148_SQUEEZE_BREAKOUT"
            elif cls.is_expansion(m, "LONG"):
                trigger_hit = "TRIGGER_A_SQUEEZE_EXPANSION"
            elif cls.is_early_long(m):
                trigger_hit = "TRIGGER_EARLY_0748_BREAKOUT"
            elif cls.is_outer_rail_continuation(m, "LONG"):
                trigger_hit = "TRIGGER_OUTER_RAIL_CONTINUATION_LONG"
            elif (
                m.kc_middle <= m.low_price <= m.kc_middle + 0.35 * m.atr
                and m.ma5_prev > m.ma15_prev and m.ma5_curr > m.ma15_curr
            ):
                trigger_hit = "TRIGGER_B_PULLBACK_KC_MIDDLE_BOUNCE"
            elif m.open_price <= m.ma15_curr < m.close_price:
                trigger_hit = "TRIGGER_C_EARLY_MA15_BREAKOUT"
            elif cls.is_slope_aligned_trend_entry(m, "LONG"):
                trigger_hit = "TRIGGER_ALIGNED_TREND_SLOPES"
            if not trigger_hit:
                trigger_hit = cls.priority_channel(m, "LONG")

            if not trigger_hit:
                return GateDecision(False, "HOLD", "WAIT_VALID_LONG_TRIGGER", details)

            logger.info(f"🚀 [GATE_LONG_PASS] {m.symbol} 全條件審查通過！授權開多 ({trigger_hit})")
            logger.info("[UI_DISPLAY: 買多] symbol=%s trigger=%s stage=CANDIDATE_NOT_FILL", m.symbol, trigger_hit)
            return GateDecision(True, "OPEN_LONG", f"AUTHORIZED_LONG:{trigger_hit}", {**details, "trigger": trigger_hit})

        return GateDecision(
            False,
            "HOLD",
            "VETO_INVALID_ENTRY_SIDE",
            {"symbol": m.symbol, "side": normalized},
        )

    @classmethod
    def evaluate_pivot_or_cross_open(
        cls,
        m: MarketSnapshot,
        side: Optional[str] = None,
        *,
        position_side: Optional[str] = None,
        has_position: Optional[bool] = None,
        position_size: Optional[float] = None,
    ) -> GateDecision:
        """
        ⚡ 真峰谷轉彎與 MA5/MA15 交叉最高優先級開倉/反手通道 (Valley Turn / Golden Cross & Peak Turn / Death Cross Override)
        當 1m K 棒確定收線 (is_bar_closed == True) 時：
        1. 谷底轉彎 / 金叉成立 (close_price > ma5_curr 且 ma5_curr > ma5_prev)：
           - 100% 豁免大趨勢標頭 (KC 向上/向下) 與走平限制！
           - 若持空單：當根收線第 1 毫秒發送 CLOSE_SHORT 且同 1 秒瞬間發送 OPEN_LONG！
           - 若為空倉：當根收線瞬間 100% 市價開多 OPEN_LONG！
        2. 頂部轉彎 / 死叉成立 (close_price < ma5_curr 且 ma5_curr < ma5_prev)：
           - 100% 豁免大趨勢標頭與超賣過濾限制！
           - 若持多單：當根收線第 1 毫秒發送 CLOSE_LONG 且同 1 秒瞬間發送 OPEN_SHORT！
           - 若為空倉：當根收線瞬間 100% 市價開空 OPEN_SHORT！
        """
        pos_side = str(position_side if position_side is not None else (m.position_side or "")).upper()
        pos_qty = float(position_size if position_size is not None else (m.position_size or 0.0))
        already_holding = bool((has_position is True) or m.has_position or pos_qty > 0.0 or pos_side in ("LONG", "SHORT"))
        details: Dict[str, Any] = {
            "symbol": m.symbol,
            "quote": m.quote,
            "close_price": m.close_price,
            "ma5_curr": m.ma5_curr,
            "ma5_prev": m.ma5_prev,
            "ma15_curr": m.ma15_curr,
            "position_side": pos_side or None,
        }
        if not m.is_closed:
            return GateDecision(False, "HOLD", "BLOCKED_PIVOT_CROSS_BAR_NOT_CLOSED", details)

        normalized = str(side or "").upper()
        check_long = normalized in ("", "LONG", "OPEN_LONG")
        check_short = normalized in ("", "SHORT", "OPEN_SHORT")

        if check_long and m.close_price > m.ma5_curr and m.ma5_curr > m.ma5_prev:
            if m.kc_middle <= m.kc_middle_prev or str(m.macro_trend).upper() == "BEARISH":
                return GateDecision(False, "HOLD", "BLOCKED_LONG_ON_BEARISH_TREND", details)
            if already_holding and pos_side in ("LONG", "OPEN_LONG"):
                return GateDecision(False, "HOLD", "BLOCKED_DUPLICATE_LONG_POSITION_ALREADY_HELD", details)
            is_flip = bool(already_holding and pos_side in ("SHORT", "OPEN_SHORT"))
            trigger = "FLIP_SHORT_TO_LONG_VALLEY_TURN_OR_GOLDEN_CROSS" if is_flip else "TRIGGER_VALLEY_TURN_OR_GOLDEN_CROSS_LONG"
            return GateDecision(
                True,
                "OPEN_LONG",
                f"AUTHORIZED_LONG:{trigger}",
                {
                    **details,
                    "trigger": trigger,
                    "close_action": "CLOSE_SHORT" if is_flip else None,
                    "open_action": "OPEN_LONG",
                    "is_flip": is_flip,
                    "header_exempt": False,
                },
            )

        if check_short and m.close_price < m.ma5_curr and m.ma5_curr < m.ma5_prev:
            if m.kc_middle >= m.kc_middle_prev or str(m.macro_trend).upper() == "BULLISH":
                return GateDecision(False, "HOLD", "BLOCKED_SHORT_ON_BULLISH_TREND", details)
            if already_holding and pos_side in ("SHORT", "OPEN_SHORT"):
                return GateDecision(False, "HOLD", "BLOCKED_DUPLICATE_SHORT_POSITION_ALREADY_HELD", details)
            is_flip = bool(already_holding and pos_side in ("LONG", "OPEN_LONG"))
            trigger = "FLIP_LONG_TO_SHORT_PEAK_TURN_OR_DEATH_CROSS" if is_flip else "TRIGGER_PEAK_TURN_OR_DEATH_CROSS_SHORT"
            return GateDecision(
                True,
                "OPEN_SHORT",
                f"AUTHORIZED_SHORT:{trigger}",
                {
                    **details,
                    "trigger": trigger,
                    "close_action": "CLOSE_LONG" if is_flip else None,
                    "open_action": "OPEN_SHORT",
                    "is_flip": is_flip,
                    "header_exempt": False,
                    "oversold_exempt": True,
                },
            )

        return GateDecision(False, "HOLD", "WAIT_PIVOT_OR_MA5_TURN_TRIGGER", details)


