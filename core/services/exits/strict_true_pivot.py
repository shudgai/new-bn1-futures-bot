"""Strict closed-candle peak and valley confirmations."""

import copy
import logging
import math
import time
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from core.services.candle_data import closed_entry_candles

CONFIRMED_PIVOT_PENDING_KEY = "confirmed_true_pivot_exit_pending"
_CONFIRMED_PIVOT_REASONS = {
    "LONG": "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT",
    "SHORT": "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT",
}
PROFIT_PIVOT_EXIT_POLICY = "PROFIT_CONFIRMED_TRUE_PIVOT_ONLY_V1"
PROFIT_PIVOT_REASONS = frozenset(_CONFIRMED_PIVOT_REASONS.values())
PIVOT_BYPASSABLE_STRUCTURE_HOLDS = (
    "_050_ATR_BUFFER",
    "_MA5_MA15_NOT_REVERSED", "_STRUCTURE_AND_TRAIL_NOT_READY",
)
PROFIT_PIVOT_PENDING_KEY = "profit_true_pivot_exit_pending_v1"


def evaluate_position_exit(
    position: Dict[str, Any], frame: Optional[pd.DataFrame],
) -> Tuple[bool, str, Dict[str, Any]]:
    """Qualify ordinary exits; this alone never supplies pivot/order authority."""
    logger = logging.getLogger(__name__)
    from core.symbol_aliases import is_meme_breakout_symbol
    if position.get("symbol") and not is_meme_breakout_symbol(position["symbol"]):
        return True, "MEME_HOLDING_SCOPE_NOT_APPLICABLE", {}
    identity = _position_identity(position)
    details: Dict[str, Any] = {}

    def reject(reason: str) -> Tuple[bool, str, Dict[str, Any]]:
        logger.info("[%s] side=%s details=%s", reason, position.get("side"), details)
        logger.info("[VETO_HOLD_%s] reason=%s", position.get("side"), reason)
        if position.get("side") == "SHORT":
            logger.info("[UI_DISPLAY: 續抱] symbol=%s reason=%s", position.get("symbol"), reason)
        return False, reason, details

    if identity is None or not isinstance(frame, pd.DataFrame):
        return reject("VETO_HOLD_STRUCTURE_DATA_REQUIRED")
    from core.services.exits.trend_lock import _closed_snapshot_fresh

    closed = closed_entry_candles(frame)
    keys = ("timestamp", "open", "high", "low", "close", "kc_middle", "ma5", "ma15", "atr")
    if len(closed) < 2 or not set(keys).issubset(closed.columns):
        return reject("VETO_HOLD_STRUCTURE_DATA_REQUIRED")
    try:
        snapshot = float(frame.attrs["snapshot_ms"])
        if (float(frame.attrs.get("timeframe_ms", 60_000)) != 60_000
                or not 0 <= time.time() * 1000 - snapshot <= 180_000
                or not _closed_snapshot_fresh(frame, closed)):
            return reject("VETO_HOLD_STRUCTURE_STALE_OR_INTERVAL")
        side, opened, entry, _ = identity
        start = math.ceil(opened * 1000 / 60_000) * 60_000
        held = closed.loc[closed["timestamp"].astype(float) >= start]
        details["bars_held"] = len(held)
        if len(held) < 5:
            return reject("VETO_HOLD_MIN_5_COMPLETED_BARS")
        rows = [{key: float(row[key]) for key in keys} for _, row in held.iterrows()]
        if any(not math.isfinite(value) or value <= 0 for row in rows for value in row.values()):
            return reject("VETO_HOLD_STRUCTURE_INVALID_DATA")
        if any(
            row["high"] < max(row["open"], row["close"])
            or row["low"] > min(row["open"], row["close"])
            or row["high"] < row["low"] for row in rows
        ) or any(
            not math.isclose(b["timestamp"] - a["timestamp"], 60_000, abs_tol=1)
            for a, b in zip(rows, rows[1:])
        ):
            return reject("VETO_HOLD_STRUCTURE_INVALID_DATA")
        prev, current = rows[-2:]
        sign = 1 if side == "LONG" else -1
        atr = current["atr"]
        distance = sign * (current["close"] - current["kc_middle"])
        details.update(
            confirmation_bar_id=current["timestamp"], reference_atr=atr,
            kc_middle=current["kc_middle"], confirmation_close=current["close"],
            confirmation_ma5=current["ma5"], confirmation_ma15=current["ma15"],
        )
        if distance >= -0.5 * atr:
            return reject(f"VETO_HOLD_{side}_050_ATR_BUFFER")
        if sign * (current["close"] - current["open"]) >= 0:
            return reject(f"VETO_HOLD_{side}_DIRECTIONAL_CANDLE")
        if sign * (current["ma5"] - current["ma15"]) >= 0:
            return reject(f"VETO_HOLD_{side}_MA5_MA15_NOT_REVERSED")
        structure = distance < -0.5 * atr
        extreme = max(row["high"] for row in rows) if side == "LONG" else min(
            row["low"] for row in rows
        )
        observed = position.get("highest_price" if side == "LONG" else "lowest_price")
        if observed is not None:
            observed = float(observed)
            if not math.isfinite(observed) or observed <= 0:
                return reject("VETO_HOLD_STRUCTURE_INVALID_DATA")
            extreme = max(extreme, observed) if side == "LONG" else min(extreme, observed)
        gain = sign * (extreme - entry)
        retreat = sign * (extreme - current["close"])
        trailing = gain > 2.5 * atr and retreat > 1.5 * atr
        details.update(
            peak_gain_atr=gain / atr, retreat_atr=retreat / atr,
            structure_broken=structure, trailing_qualified=trailing,
        )
        if not structure:
            return reject(f"VETO_HOLD_{side}_STRUCTURE_AND_TRAIL_NOT_READY")
        return True, "MA5_050_ATR_STRUCTURE_QUALIFIED", details
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        logger.error("VETO_HOLD_STRUCTURE_INVALID_DATA: %s", exc)
        return reject("VETO_HOLD_STRUCTURE_INVALID_DATA")


def profit_pivot_net_positive(position: Dict[str, Any], quote: float) -> bool:
    """Recheck current close economics; a historical peak is not current profit."""
    from core.config import SLIPPAGE_PCT
    from core.services.exits.staged_profit_lock import calculate_staged_net_pnl_and_roe

    try:
        slippage = float(position.get("slippage_pct", SLIPPAGE_PCT))
        if not math.isfinite(slippage) or not 0 <= slippage < 1:
            raise ValueError("invalid exit slippage")
        # Charge the closing fee on the estimated slipped fill, as accounts do.
        fill = float(quote) * (
            1 - slippage if str(position.get("side")).upper() == "LONG" else 1 + slippage
        )
        net, _, _, _ = calculate_staged_net_pnl_and_roe(
            {**position, "slippage_pct": 0.0}, fill,
        )
        if math.isfinite(net) and net > 0:
            return True
        logging.getLogger(__name__).info(
            "VETO_PROFIT_PIVOT_NONPOSITIVE_NET side=%s quote=%s estimated_net=%s",
            position.get("side"), quote, net,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        logging.getLogger(__name__).error("VETO_PROFIT_PIVOT_INVALID_NET_DATA: %s", exc)
    return False


def profitable_true_pivot_exit(
    position: Dict[str, Any],
    frame: Optional[pd.DataFrame],
    metadata: Optional[Dict[str, Any]] = None,
    *,
    quote: Optional[float] = None,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Confirm a post-entry, profitable three-bar reversal, or retry its ticket."""
    if quote is not None and not profit_pivot_net_positive(position, quote):
        return None
    structure_allowed, structure_code, structure_details = evaluate_position_exit(position, frame)
    # A profitable confirmed true pivot outranks the 0.50 ATR / MA5-MA15 hold lock
    # (Plan A true-pivot exit); data, freshness and 5-bar hold checks still apply.
    if not structure_allowed and not str(structure_code).endswith(PIVOT_BYPASSABLE_STRUCTURE_HOLDS):
        return None
    pending = pending_confirmed_pivot_exit(position, metadata, key=PROFIT_PIVOT_PENDING_KEY)
    if pending is not None and pending[1].get("exit_policy") == PROFIT_PIVOT_EXIT_POLICY:
        try:
            details = pending[1]
            gain, roe, atr = (
                float(details[key]) for key in ("peak_gain", "peak_roe", "reference_atr")
            )
            close, midpoint, ma5 = (
                float(details[key])
                for key in ("confirmation_close", "pivot_body_midpoint", "confirmation_ma5")
            )
            valid = (
                all(math.isfinite(value) for value in (gain, roe, atr, close, midpoint, ma5))
                and atr > 0 and min(close, midpoint, ma5) > 0
                and (gain >= 0.5 * atr or math.isclose(gain, 0.5 * atr, rel_tol=1e-12)
                     or roe >= 0.05 or math.isclose(roe, 0.05, rel_tol=1e-12))
                and (close < min(midpoint, ma5) if str(position.get("side")).upper() == "LONG"
                     else close > max(midpoint, ma5))
            )
            if valid:
                return pending[0], {**pending[1], "holding_structure": structure_details}
            logging.getLogger(__name__).error("Rejected inconsistent profit-pivot pending evidence")
            return None
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            logging.getLogger(__name__).error("Rejected invalid profit-pivot pending evidence: %s", exc)
            return None
    elif pending is not None:
        logging.getLogger(__name__).error("Rejected missing profit-pivot pending policy")
        return None
    identity = _position_identity(position)
    if identity is None or not isinstance(frame, pd.DataFrame):
        return None
    from core.services.exits.trend_lock import _closed_snapshot_fresh

    closed = closed_entry_candles(frame)
    if len(closed) < 3 or not _closed_snapshot_fresh(frame, closed):
        return None
    required = ("timestamp", "open", "high", "low", "close", "ma5", "atr")
    if not set(required).issubset(closed.columns):
        return None
    try:
        interval = float(frame.attrs.get("timeframe_ms", 60_000))
        if not math.isfinite(interval) or interval <= 0:
            return None
        snapshot_ms = float(frame.attrs["snapshot_ms"])
        if not 0 <= time.time() * 1000.0 - snapshot_ms <= max(180_000.0, 2 * interval):
            return None
        bars = [
            {key: float(row[key]) for key in required}
            for _, row in closed.iloc[-3:].iterrows()
        ]
        if any(not math.isfinite(value) or value <= 0 for row in bars for value in row.values()):
            return None
        if any(
            row["high"] < max(row["open"], row["close"])
            or row["low"] > min(row["open"], row["close"])
            or row["high"] < row["low"]
            for row in bars
        ):
            return None
        if any(
            not math.isclose(right["timestamp"] - left["timestamp"], interval, abs_tol=1.0)
            for left, right in zip(bars, bars[1:])
        ):
            return None
        first_held_bar = math.ceil(identity[1] * 1000.0 / interval) * interval
        if bars[0]["timestamp"] < first_held_bar:
            return None
        side, _, entry, _ = identity
        left, pivot, confirm = bars
        midpoint = (pivot["open"] + pivot["close"]) / 2.0
        if side == "LONG":
            valid = (
                pivot["high"] > left["high"] and pivot["high"] > confirm["high"]
                and confirm["close"] < confirm["open"]
                and confirm["close"] < midpoint and confirm["close"] < confirm["ma5"]
            )
            gain = max(row["high"] for row in bars) - entry
        else:
            valid = (
                pivot["low"] < left["low"] and pivot["low"] < confirm["low"]
                and confirm["close"] > confirm["open"]
                and confirm["close"] > midpoint and confirm["close"] > confirm["ma5"]
            )
            gain = entry - min(row["low"] for row in bars)
        if not valid:
            return None
        held = closed.loc[closed["timestamp"].astype(float) >= first_held_bar]
        extremes = [float(value) for value in held["high" if side == "LONG" else "low"]]
        if any(not math.isfinite(value) or value <= 0 for value in extremes):
            return None
        gain = max(gain, max(extremes) - entry if side == "LONG" else entry - min(extremes))
        peak_roe = float(position.get("peak_roe") or 0.0)
        leverage = float(position.get("leverage") or 1.0)
        if not math.isfinite(peak_roe) or not math.isfinite(leverage) or leverage <= 0:
            return None
        gain = max(0.0, gain)
        peak_roe = max(peak_roe, gain / entry * leverage)
        atr_threshold = 0.5 * confirm["atr"]
        atr_ready = gain >= atr_threshold or math.isclose(gain, atr_threshold, rel_tol=1e-12)
        roe_ready = peak_roe >= 0.05 or math.isclose(peak_roe, 0.05, rel_tol=1e-12)
        if not (atr_ready or roe_ready):
            return None
        return _CONFIRMED_PIVOT_REASONS[side], {
            "exit_policy": PROFIT_PIVOT_EXIT_POLICY,
            "pivot_bar_id": pivot["timestamp"],
            "confirm_bar_id": confirm["timestamp"],
            "pivot_high": pivot["high"],
            "pivot_low": pivot["low"],
            "confirmation_close": confirm["close"],
            "pivot_body_midpoint": midpoint,
            "confirmation_ma5": confirm["ma5"],
            "reference_atr": confirm["atr"],
            "peak_gain": gain,
            "peak_roe": peak_roe,
            "holding_structure": structure_details,
        }
    except (TypeError, ValueError, OverflowError):
        return None


def persist_profit_pivot_exit(
    account, symbol, position, reason, details, *, strict_save: bool = False,
) -> None:
    """Persist the verified close intent before any order can fail or refresh."""
    if details.get("exit_policy") != PROFIT_PIVOT_EXIT_POLICY:
        raise ValueError("unverified profit-pivot policy")
    if not store_confirmed_pivot_exit(position, (reason, details), key=PROFIT_PIVOT_PENDING_KEY):
        raise ValueError("profit-pivot position identity unavailable")
    meta = account.position_meta.setdefault(symbol, {})
    meta[PROFIT_PIVOT_PENDING_KEY] = copy.deepcopy(position[PROFIT_PIVOT_PENDING_KEY])
    if strict_save:
        account.save_state(strict=True)
    else:
        account.save_state()


def _position_identity(position: Dict[str, Any]) -> Optional[list]:
    try:
        side = str(position.get("side", "")).upper()
        opened_value = next(
            (
                position.get(key)
                for key in (
                    "open_timestamp",
                    "entry_timestamp",
                    "open_bar_id",
                    "entry_bar_id",
                )
                if position.get(key) is not None
            ),
            None,
        )
        entry_snapshot = position.get("entry_snapshot")
        if opened_value is None and isinstance(entry_snapshot, dict):
            opened_value = entry_snapshot.get(
                "entry_bar_id", entry_snapshot.get("timestamp")
            )
        opened = float(opened_value or 0.0)
        entry = float(position.get("entry_price") or 0.0)
        quantity_value = (
            position.get("qty")
            or position.get("quantity")
            or position.get("amount")
        )
        quantity = abs(float(quantity_value)) if quantity_value is not None else 0.0
        if opened >= 1e11:
            opened /= 1000.0
        if (side not in _CONFIRMED_PIVOT_REASONS
                or not math.isfinite(opened)
                or not math.isfinite(entry)
                or not math.isfinite(quantity)
                or opened <= 0
                or entry <= 0
                or quantity < 0):
            return None
        return [side, opened, entry, quantity]
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def pending_confirmed_pivot_exit(
    position: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
    *,
    key: str = CONFIRMED_PIVOT_PENDING_KEY,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    identity = _position_identity(position)
    if identity is None:
        return None
    sources = (position, metadata) if isinstance(metadata, dict) else (position,)
    for source in sources:
        ticket = source.get(key)
        if (not isinstance(ticket, dict)
                or ticket.get("identity") != identity
                or ticket.get("reason") != _CONFIRMED_PIVOT_REASONS[identity[0]]):
            continue
        details = ticket.get("details")
        if not isinstance(details, dict):
            continue
        try:
            pivot_ms = float(details["pivot_bar_id"])
            confirm_ms = float(details["confirm_bar_id"])
            if (not math.isfinite(pivot_ms) or not math.isfinite(confirm_ms)
                    or pivot_ms <= 0 or confirm_ms <= pivot_ms):
                continue
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        return ticket["reason"], dict(details)
    return None


def store_confirmed_pivot_exit(
    position: Dict[str, Any],
    pivot: Tuple[str, Dict[str, Any]],
    *,
    key: str = CONFIRMED_PIVOT_PENDING_KEY,
) -> bool:
    identity = _position_identity(position)
    reason, details = pivot
    if (identity is None
            or reason != _CONFIRMED_PIVOT_REASONS[identity[0]]
            or not isinstance(details, dict)):
        return False
    position[key] = {
        "identity": identity,
        "reason": reason,
        "details": dict(details),
    }
    return True


def analyze_market_structure(
    frame: Optional[pd.DataFrame],
    position_side: str,
) -> str:
    """Classify a completed five-bar structure without using KC levels."""
    if frame is None or frame.empty:
        return "HOLD"
    try:
        side = str(position_side).upper()
        if side not in ("LONG", "SHORT"):
            return "HOLD"
        closed = closed_entry_candles(frame)
        if len(closed) < 15:
            return "HOLD"
        bars = closed.iloc[-3:]
        if not {"open", "high", "low", "close", "ma5", "ma15"}.issubset(
            bars.columns
        ):
            return "HOLD"

        candles = []
        for _, row in bars.iterrows():
            values = tuple(float(row[name]) for name in ("open", "high", "low", "close"))
            if (
                not all(math.isfinite(value) and value > 0 for value in values)
                or values[1] < max(values[0], values[3])
                or values[2] > min(values[0], values[3])
                or values[2] > values[1]
            ):
                return "HOLD"
            candles.append(values)
        ma5 = [float(value) for value in bars["ma5"]]
        ma15 = [float(value) for value in bars["ma15"]]
        if not all(
            math.isfinite(value) and value > 0
            for value in ma5 + ma15
        ):
            return "HOLD"

        c0, c1, c2 = candles
        ma5_slope = ma5[-1] - ma5[-2]
        if side == "LONG":
            if (
                c1[1] > c0[1]
                and c1[1] > c2[1]
                and c2[3] < min(c1[0], c1[3])
                and ma5_slope < 0
            ):
                return "TRUE_PEAK"
            if c2[3] >= ma15[-1] and ma5[-1] > ma15[-1]:
                return "PULLBACK"
        else:
            if (
                c1[2] < c0[2]
                and c1[2] < c2[2]
                and c2[3] > max(c1[0], c1[3])
                and ma5_slope > 0
            ):
                return "TRUE_TROUGH"
            if c2[3] <= ma15[-1] and ma5[-1] < ma15[-1]:
                return "PULLBACK"
        return "HOLD"
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return "HOLD"


def confirmed_true_pivot_exit(
    position: Dict[str, Any],
    frame: Optional[pd.DataFrame],
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Return an exit when a three-bar completed true peak or trough confirms."""
    if not isinstance(position, dict) or frame is None or frame.empty:
        return None
    try:
        side = str(position.get("side", "")).upper()
        if side not in ("LONG", "SHORT"):
            return None

        closed = closed_entry_candles(frame)
        if len(closed) < 15:
            return None
        bars = closed.iloc[-3:]

        timeframe_ms = float(frame.attrs.get("timeframe_ms", 60_000) or 60_000)
        timestamps = [float(value) for value in bars["timestamp"]]
        if (
            not math.isfinite(timeframe_ms)
            or timeframe_ms <= 0
            or any(not math.isfinite(value) or value <= 0 for value in timestamps)
            or any(
                right - left != timeframe_ms
                for left, right in zip(timestamps, timestamps[1:])
            )
            or not {"ma5", "ma15"}.issubset(bars.columns)
        ):
            return None

        entry_timestamp = None
        entry_snapshot = position.get("entry_snapshot")
        for key in ("open_timestamp", "entry_timestamp", "open_bar_id", "entry_bar_id"):
            value = position.get(key)
            if value is not None:
                entry_timestamp = float(value)
                break
        if entry_timestamp is None and isinstance(entry_snapshot, dict):
            value = entry_snapshot.get("entry_bar_id", entry_snapshot.get("timestamp"))
            if value is not None:
                entry_timestamp = float(value)
        if entry_timestamp is None or not math.isfinite(entry_timestamp) or entry_timestamp <= 0:
            return None

        entry_ms = entry_timestamp * 1000.0 if entry_timestamp < 1e11 else entry_timestamp
        entry_bar_ms = math.floor(entry_ms / timeframe_ms) * timeframe_ms
        pivot_ms = timestamps[1]
        confirmation_ms = timestamps[2]
        if pivot_ms <= entry_bar_ms or confirmation_ms <= entry_bar_ms:
            return None

        structure = analyze_market_structure(frame, side)
        if structure not in ("TRUE_PEAK", "TRUE_TROUGH"):
            return None
        pivot = bars.iloc[1]
        confirmation = bars.iloc[2]
        entry_price = float(position.get("entry_price") or position.get("price") or 0.0)
        atr = float(confirmation.get("atr") or bars.iloc[-1].get("atr") or 0.0)
        if atr <= 0:
            atr = abs(float(confirmation["high"]) - float(confirmation["low"]))
        peak_roe = float(position.get("peak_roe", 0.0) or 0.0)
        ma5_slope = float(confirmation["ma5"]) - float(bars.iloc[1]["ma5"])
        conf_close = float(confirmation["close"])
        kc_mid = float(confirmation.get("kc_middle", 0.0))
        conf_ma15 = float(confirmation.get("ma15", 0.0))

        if side == "LONG":
            pivot_high = float(pivot["high"])
            gain = pivot_high - entry_price if entry_price > 0 else 0.0
            # 【方案 A 波段獲利過濾鐵律】：
            # 必須實質脫離成本區間 (gain >= 0.5 * ATR 或 peak_roe >= 0.05)，真峰頂才具備平倉獲利了結資格！
            # 若波段未展開（未達 0.5 ATR 且 peak_roe < 5%），100% 判定為「成本區間正常回踩」，絕不平多！
            if entry_price > 0 and (gain < 0.5 * atr and peak_roe < 0.05):
                return None

            return "EXIT_BY_CONFIRMED_TRUE_PEAK_PIVOT", {
                "pivot_bar_id": pivot_ms,
                "confirm_bar_id": confirmation_ms,
                "pivot_high": pivot_high,
                "confirmation_close": conf_close,
                "ma5_slope": ma5_slope,
            }

        pivot_low = float(pivot["low"])
        drop = entry_price - pivot_low if entry_price > 0 else 0.0
        # 空單同理：若未實質探底展開 (drop < 0.5 * ATR 且 peak_roe < 5%)，100% 判定為正常反彈，絕不平空！
        if entry_price > 0 and (drop < 0.5 * atr and peak_roe < 0.05):
            return None

        return "EXIT_BY_CONFIRMED_TRUE_VALLEY_PIVOT", {
            "pivot_bar_id": pivot_ms,
            "confirm_bar_id": confirmation_ms,
            "pivot_low": pivot_low,
            "confirmation_close": conf_close,
            "ma5_slope": ma5_slope,
        }
    except (AttributeError, KeyError, TypeError, ValueError, IndexError, OverflowError):
        return None
