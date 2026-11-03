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
    is_closed: bool = False


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
        is_closed=True,
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
            # Two fully completed post-close candles; a partial closing candle is excluded.
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
        )
        kc_width_ratio = (m.kc_upper - m.kc_lower) / m.quote
        if kc_width_ratio < 0.010 and not expansion:
            return True, "VETO_NARROW_SQUEEZE_RANGE"
        kc_slope = m.kc_middle - m.kc_middle_prev
        ma15_slope = m.ma15_curr - m.ma15_prev
        if abs(kc_slope) < 0.05 * m.atr and abs(ma15_slope) < 0.05 * m.atr and not expansion:
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
            and m.ma5_prev <= m.ma15_prev
            and m.ma5_curr > m.ma15_curr
        )

    @staticmethod
    def is_top_pivot_short(m: MarketSnapshot) -> bool:
        body = m.open_price - m.close_price
        threshold = 0.40 * m.atr
        return (
            m.is_closed
            and body > 0
            and (body >= threshold or math.isclose(body, threshold, rel_tol=1e-12, abs_tol=0))
            and m.close_price < m.kc_middle
            and m.close_price < m.ma15_curr
            and m.ma5_curr < m.ma5_prev
            and m.ma5_prev >= m.ma15_prev
            and m.ma5_curr < m.ma15_curr
        )

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
        if is_in_cooldown:
            return block("BLOCKED_BY_POST_EXIT_COOLDOWN")
        blocked, reason = cls.evaluate_market_blacklist(m)
        if blocked:
            return block(reason)
        sign = 1 if side == "LONG" else -1
        macro = str(m.macro_trend).upper()
        if macro not in ("BULLISH", "BEARISH", "RANGE", "NEUTRAL"):
            return block("VETO_MACRO_TREND_UNKNOWN")
        if macro == ("BEARISH" if side == "LONG" else "BULLISH"):
            return block(f"BLOCKED_{side}_MACRO_TREND_CONFLICT")
        slope = sign * (m.kc_middle - m.kc_middle_prev)
        early_long = side == "LONG" and cls.is_early_long(m)
        top_pivot_short = side == "SHORT" and cls.is_top_pivot_short(m)
        if slope < 0 or (slope == 0 and not (
            cls.is_expansion(m, side) or early_long or top_pivot_short
        )):
            return block(f"BLOCKED_{side}_KC_DIRECTION")
        if any(sign * (price - level) <= 0
               for price in (m.quote, m.close_price) for level in (m.kc_middle, m.ma15_curr)):
            return block(f"BLOCKED_{side}_PRICE_MIDDLE_MA15")
        alignment = sign * (m.ma5_curr - m.ma15_curr)
        if (sign * (m.ma5_curr - m.ma5_prev) <= 0
                or alignment < 0 or alignment == 0 and not early_long):
            return block(f"BLOCKED_{side}_MA5_DIRECTION_OR_ALIGNMENT")
        if sign * (m.close_price - m.open_price) <= 0:
            return block(f"BLOCKED_{side}_DIRECTIONAL_CANDLE")
        extreme = max(m.quote, m.close_price) if sign == 1 else min(m.quote, m.close_price)
        bias = sign * (extreme - m.ma15_curr)
        if bias > m.atr and not math.isclose(bias, m.atr, rel_tol=1e-12, abs_tol=0):
            return block(f"BLOCKED_{side}_MA15_EXTENSION_1_ATR")
        band_limit = 1.2 if side == "LONG" and cls.is_expansion(m, side) else (0.5 if side == "LONG" else 0.8)
        band = m.kc_upper if side == "LONG" else m.kc_lower
        extension = sign * (extreme - band)
        limit = band_limit * m.atr
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
        if cls.is_bottom_v_long(m):
            trigger_hit = "TRIGGER_BOTTOM_V_REVERSAL_0903"
        elif cls.is_expansion(m, "LONG"):
            trigger_hit = "TRIGGER_A_SQUEEZE_EXPANSION"
        elif cls.is_early_long(m):
            trigger_hit = "TRIGGER_EARLY_0748_BREAKOUT"
        elif (
            m.kc_middle <= m.low_price <= m.kc_middle + 0.35 * m.atr
            and m.ma5_prev <= m.ma15_prev and m.ma5_curr > m.ma15_curr
        ):
            trigger_hit = "TRIGGER_B_PULLBACK_KC_MIDDLE_BOUNCE"
        elif m.open_price <= m.ma15_curr < m.close_price:
            trigger_hit = "TRIGGER_C_EARLY_MA15_BREAKOUT"

        if not trigger_hit:
            return GateDecision(False, "HOLD", "WAIT_VALID_LONG_TRIGGER", details)

        logger.info(f"🚀 [GATE_LONG_PASS] {m.symbol} 全條件審查通過！授權開多 ({trigger_hit})")
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
        elif cls.is_expansion(m, "SHORT"):
            trigger_hit = "TRIGGER_A_EARLY_BODY_BREAKDOWN"
        elif is_top_reversal:
            trigger_hit = "TRIGGER_B_TOP_REVERSAL_CROSS"
        elif (m.close_price < m.kc_lower and m.quote < m.kc_lower
              and m.ma15_curr < m.ma15_prev):
            trigger_hit = "TRIGGER_C_BREAKDOWN_LOWER_RAIL"

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
                    m.kc_upper, m.kc_lower, m.kc_middle, p.entry_price, p.open_timestamp_ms,
                ))
                or not m.kc_lower < m.kc_middle < m.kc_upper):
            return hold("VETO_EXIT_INVALID_DATA")
        # This is a candidate only; the account boundary verifies band-breach math.
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
