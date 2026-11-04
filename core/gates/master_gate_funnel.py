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
    )
    from core.services.exits.strict_true_pivot import PROFIT_PIVOT_REASONS
    return frozenset(r.upper() for r in (
        *KC_FLIP_EXIT_REASONS.values(), *KC_MIDDLE_MA15_BREAK_REASONS.values(),
        *STRUCTURE_EXIT_REASONS.values(), *PROFIT_PIVOT_REASONS,
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
    if macro_frame is None:
        macro_frame = closed
    calculated_macro = str(DataPipeline.calculate_snapshot(
        symbol, macro_frame, current_price=float(current["close"]),
    ).macro_trend).upper()
    macro = str(macro_trend or frame.attrs.get("macro_trend") or calculated_macro).upper()
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
        return (
            m.is_closed
            and body > 0
            and math.isfinite(kc_position)
            and kc_position <= long_max
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
        side = str(position_side or "").upper()
        if not m.is_closed or m.timeframe_ms != 60_000.0:
            return None
        if side == "LONG" and cls.is_top_pivot_short(m):
            return "SHORT", "TRIGGER_TOP_PIVOT_CROSS"
        if side == "SHORT" and cls.is_bottom_pivot_long(m):
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
                side == "SHORT" and cls.is_top_pivot_short(m)
                or side == "LONG" and cls.is_bottom_pivot_long(m)
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
    def is_ma_cross_slope_entry(m: MarketSnapshot, side: str) -> bool:
        """A fresh cross is priority only when KC, MA5, and MA15 slopes are established."""
        if side not in ("LONG", "SHORT") or not math.isfinite(m.atr) or m.atr <= 0:
            return False
        sign = 1 if side == "LONG" else -1
        slope_floor = MEME_MIN_KC_MIDDLE_SLOPE_ATR * m.atr
        crossed = (
            sign * (m.ma5_prev - m.ma15_prev) <= 0
            and sign * (m.ma5_curr - m.ma15_curr) > 0
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
        elif side == "LONG":
            if cls.is_bottom_pivot_long(m):
                return "TRIGGER_BOTTOM_PIVOT_CROSS"
            if cls.is_big_body_breakout(m, "LONG"):
                return "TRIGGER_EARLY_BREAKOUT_CHANNEL"
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
        }
        pivot_turn = priority in {
            "TRIGGER_TOP_PIVOT_CROSS", "TRIGGER_BOTTOM_PIVOT_CROSS",
        }
        if (not pivot_turn and not breakout_priority and sign * (m.kc_middle - m.kc_middle_prev) <= 0):
            return f"BLOCKED_{side}_KC_DIRECTION"
        if not pivot_turn and not breakout_priority:
            slope_limit = MEME_MIN_KC_MIDDLE_SLOPE_ATR * m.atr
            kc_slope = sign * (m.kc_middle - m.kc_middle_prev)
            if abs(kc_slope) < slope_limit and not math.isclose(
                abs(kc_slope), slope_limit, rel_tol=1e-9, abs_tol=0,
            ):
                return f"BLOCKED_{side}_KC_MIDDLE_TOO_FLAT"
        if not breakout_priority:
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
        if is_in_cooldown and not priority:
            return block("BLOCKED_BY_POST_EXIT_COOLDOWN")
        blocked, reason = cls.evaluate_market_blacklist(m)
        if blocked:
            return block(reason)
        sign = 1 if side == "LONG" else -1
        pivot_reversal = priority in {
            "TRIGGER_TOP_PIVOT_CROSS", "TRIGGER_BOTTOM_PIVOT_CROSS",
        }
        from core.symbol_aliases import is_meme_breakout_symbol
        if is_meme_breakout_symbol(m.symbol):
            distance_atr = meme_distance_atr(m.symbol, m.atr)
            valid_outer_breakout = cls.is_big_body_breakout(m, side)
            if side == "SHORT":
                short_extension = m.close_price - m.kc_lower
                if short_extension < 0 and not valid_outer_breakout:
                    return block("VETO_SHORT_BELOW_KC_LOWER_ANTI_CHASE")
            if side == "LONG":
                long_extension = m.close_price - m.kc_upper
                if long_extension > 0 and not valid_outer_breakout:
                    return block("VETO_LONG_ABOVE_KC_UPPER_ANTI_CHASE")
            if any(
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
        if (
            macro == ("BEARISH" if side == "LONG" else "BULLISH")
            and not reverse_entry and not pivot_reversal
        ):
            return block(f"BLOCKED_{side}_MACRO_TREND_CONFLICT")
        squeeze_breakout_long = side == "LONG" and cls.is_squeeze_breakout_long(m)
        big_body_breakout = cls.is_big_body_breakout(m, side)
        # A fresh top/bottom turn bypasses only the lagging KC slope filter.
        trend_block = cls.swing_trend_block(m, side, priority)
        if trend_block:
            return block(trend_block)
        required_levels = (m.kc_middle, m.ma15_curr)
        if any(sign * (price - level) <= 0
               for price in (m.quote, m.close_price) for level in required_levels):
            return block(f"BLOCKED_{side}_PRICE_MIDDLE_MA15")
        # Owner 2026-11-03: every channel needs MA5 already beyond MA15 on both bars, sloping
        # with the side; the fresh-cross bar never opens.
        if sign * (m.ma5_curr - m.ma5_prev) <= 0 or sign * (m.ma5_curr - m.ma15_curr) <= 0:
            return block(f"BLOCKED_{side}_MA5_DIRECTION_OR_ALIGNMENT")
        fresh_cross = sign * (m.ma5_prev - m.ma15_prev) <= 0
        fresh_cross_priority = priority in {
            "TRIGGER_TOP_PIVOT_CROSS", "TRIGGER_BOTTOM_PIVOT_CROSS",
            MA_CROSS_SLOPE_TRIGGER,
        }
        if fresh_cross and not fresh_cross_priority:
            return block(f"BLOCKED_{side}_MA5_MA15_FRESH_CROSS")
        if sign * (m.close_price - m.open_price) <= 0:
            return block(f"BLOCKED_{side}_DIRECTIONAL_CANDLE")
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

        ma15_limit = MEME_BREAKOUT_MA15_EXTENSION_ATR if big_body_breakout else MEME_MA15_EXTENSION_ATR
        extreme_over = over_limit(sign * (extreme - m.ma15_curr), MEME_MA15_EXTENSION_ATR)
        if over_limit(sign * (extreme - m.ma15_curr), ma15_limit):
            return block(f"BLOCKED_{side}_MA15_EXTENSION_1_ATR")
        bias_price = m.open_price if big_body_breakout else extreme
        if over_limit(sign * (bias_price - m.ma15_curr), ma15_limit):
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
            and m.ma15_curr < m.ma15_prev
            and m.ma5_curr < m.ma5_prev
        ):
            trigger_hit = "TRIGGER_B_BREAKDOWN_LOWER_RAIL"
        elif (m.close_price < m.kc_lower and m.quote < m.kc_lower
              and m.ma15_curr < m.ma15_prev):
            trigger_hit = "TRIGGER_C_BREAKDOWN_LOWER_RAIL"
        elif cls.is_slope_aligned_trend_entry(m, "SHORT"):
            trigger_hit = "TRIGGER_ALIGNED_TREND_SLOPES"

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
        if (not math.isfinite(current_time_ms) or p.open_timestamp_ms <= 0
                or current_time_ms < p.open_timestamp_ms
                or int(current_time_ms // 60_000) == int(p.open_timestamp_ms // 60_000)
                or p.bars_held < 5 or not m.is_closed):
            return hold("VETO_HOLD_MIN_5_COMPLETED_BARS")
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
        if sign * (m.close_price - m.kc_middle) >= -0.50 * m.atr:
            return hold(f"VETO_HOLD_{p.side}_050_ATR_BUFFER")
        if sign * (m.close_price - m.open_price) >= 0:
            return hold(f"VETO_HOLD_{p.side}_DIRECTIONAL_CANDLE")
        if sign * (m.ma5_curr - m.ma15_curr) >= 0:
            return hold(f"VETO_HOLD_{p.side}_MA5_MA15_NOT_REVERSED")
        if (not p.has_confirmed_pivot or p.estimated_net_pnl is None
                or not math.isfinite(p.estimated_net_pnl) or p.estimated_net_pnl <= 0):
            return hold("VETO_HOLD_TRUE_PIVOT_AND_POSITIVE_NET_REQUIRED")
        from core.services.exits.strict_true_pivot import PROFIT_PIVOT_REASONS
        if candidate_exit_reason not in PROFIT_PIVOT_REASONS:
            return hold("VETO_HOLD_EXIT_AUTHORITY_REQUIRED")
        return GateDecision(True, "CLOSE_POSITION", candidate_exit_reason, details)
