import pandas as pd
from typing import Dict, Any, List, Tuple, Optional

def directional_trend_quality(
    frame: pd.DataFrame, side: str, candles_count: int = 12
) -> float:
    """計算指定方向近 N 根 K 棒的趨勢品質。"""
    if frame is None or frame.empty or len(frame) < max(candles_count, 2):
        return 0.0
    recent = frame.iloc[-candles_count:]
    closes = recent["close"].astype(float)
    opens = recent["open"].astype(float)
    highs = recent["high"].astype(float)
    lows = recent["low"].astype(float)

    bodies = (closes - opens).abs()
    ranges = (highs - lows).abs().replace(0, 1e-12)
    body_ratios = bodies / ranges

    if side == "LONG":
        aligned_bars = (closes > opens).sum()
        direction_score = aligned_bars / len(recent)
    else:
        aligned_bars = (closes < opens).sum()
        direction_score = aligned_bars / len(recent)

    avg_body_ratio = float(body_ratios.mean())
    quality = (direction_score * 0.6) + (avg_body_ratio * 0.4)
    return round(quality * 100.0, 2)

def channel_volume_ratio(frame: pd.DataFrame) -> float:
    if frame is None or len(frame) < 20:
        return 1.0
    vol = frame["volume"].astype(float)
    ma20 = vol.rolling(window=20).mean().iloc[-1]
    if pd.isna(ma20) or ma20 <= 0:
        return 1.0
    return float(vol.iloc[-1] / ma20)

def channel_held_volume_is_declining(frame: pd.DataFrame) -> bool:
    if frame is None or len(frame) < 3:
        return False
    vols = frame["volume"].iloc[-3:].astype(float).tolist()
    return bool(vols[-1] < vols[-2] < vols[-3])

def channel_held_momentum_is_declining(
    frame: pd.DataFrame, side: str = "LONG"
) -> bool:
    if frame is None or len(frame) < 3:
        return False
    rows = frame.iloc[-3:]
    bodies = []
    for _, row in rows.iterrows():
        b = abs(float(row["close"]) - float(row["open"]))
        bodies.append(b)
    if side == "LONG":
        bearish_turn = float(rows.iloc[-1]["close"]) < float(rows.iloc[-1]["open"])
    else:
        bearish_turn = float(rows.iloc[-1]["close"]) > float(rows.iloc[-1]["open"])
    return bool((bodies[-1] < bodies[-2] < bodies[-3]) or bearish_turn)

def channel_long_profit_room(frame: pd.DataFrame, price: float) -> float:
    if frame is None or frame.empty:
        return 0.0
    curr = frame.iloc[-1]
    kc_upper = float(curr["kc_upper"])
    atr = max(float(curr.get("atr") or 0.0), price * 1e-6)
    room = max(0.0, (kc_upper - price) / atr)
    return float(room)

def channel_profit_room(frame: pd.DataFrame, price: float, side: str = "LONG"):
    from core.services.entry_room_service import entry_room
    from core.config import TAKER_FEE_RATE, SLIPPAGE_PCT, NET_PROFIT_GUARANTEE_BUFFER
    return entry_room(frame, price, side, TAKER_FEE_RATE, SLIPPAGE_PCT, NET_PROFIT_GUARANTEE_BUFFER)

def candidate_profit_potential(
    candidate: Dict[str, Any], frame: pd.DataFrame, price: float
) -> float:
    if frame is None or frame.empty:
        return 0.0
    side = candidate.get("side", "LONG")
    curr = frame.iloc[-1]
    kc_upper = float(curr["kc_upper"])
    kc_lower = float(curr["kc_lower"])
    target = kc_upper if side == "LONG" else kc_lower
    pct = abs(target - price) / price * 100.0 if price > 0 else 0.0
    return float(pct)

def touch_entry_math_favorable(
    candidate: Dict[str, Any], frame: pd.DataFrame, price: float
) -> bool:
    if frame is None or frame.empty:
        return False
    room_pct = candidate_profit_potential(candidate, frame, price)
    return room_pct >= 0.35

def channel_entry_requires_profit_room(reason: Optional[str]) -> bool:
    if not reason:
        return True
    exempt_reasons = {
        "KC_LIVE_UPPER_BREAK_LONG",
        "KC_LIVE_LOWER_BREAK_SHORT",
        "KC_CONTINUATION_LONG",
        "KC_CONTINUATION_SHORT",
    }
    return reason not in exempt_reasons

def channel_recent_candles_whipsawing(
    frame: pd.DataFrame, side: str = "LONG"
) -> bool:
    if frame is None or len(frame) < 4:
        return False
    recent = frame.iloc[-4:]
    colors = [
        "GREEN" if float(r["close"]) >= float(r["open"]) else "RED"
        for _, r in recent.iterrows()
    ]
    alt1 = ["GREEN", "RED", "GREEN", "RED"]
    alt2 = ["RED", "GREEN", "RED", "GREEN"]
    return colors == alt1 or colors == alt2

def channel_candidate_energy(candidate: Dict[str, Any]) -> float:
    room = float(candidate.get("profit_potential") or 0.0)
    qual = float(candidate.get("trend_quality") or 0.0)
    score = float(candidate.get("raw_score") or candidate.get("score") or 0.0)
    return room * 0.4 + qual * 0.4 + score * 0.2

def channel_confirmed_candidate_energy(candidate: Dict[str, Any]) -> float:
    return channel_candidate_energy(candidate)

def channel_price_is_outside_for_side(
    row: pd.Series, side: str
) -> bool:
    price = float(row["close"])
    if side == "LONG":
        return price > float(row["kc_upper"])
    return price < float(row["kc_lower"])

def select_strongest_same_side_candidates(
    candidates: List[Dict[str, Any]],
    score_map: Dict[str, float],
    surveillance_map: Dict[str, float],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if not candidates:
        return [], []

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for c in candidates:
        grouped.setdefault(c["side"], []).append(c)

    selected: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for side, items in grouped.items():
        sorted_items = sorted(
            items,
            key=lambda x: channel_candidate_energy(x),
            reverse=True,
        )
        selected.append(sorted_items[0])
        skipped.extend(sorted_items[1:])

    return selected, skipped

def channel_same_side_committed(
    positions: Dict[str, Any],
    pending_orders: Dict[str, Any],
    target_side: str,
) -> bool:
    for pos in positions.values():
        if pos.get("side") == target_side:
            return True
    for order in pending_orders.values():
        if order.get("side") == target_side:
            return True
    return False

def channel_takeover_net_pnl(position: Dict[str, Any], mark_price: float) -> float:
    if not position or mark_price <= 0:
        return 0.0
    entry = float(position.get("entry_price") or 0.0)
    amount = float(position.get("amount") or 0.0)
    side = position.get("side", "LONG")
    if entry <= 0 or amount <= 0:
        return 0.0

    if side == "LONG":
        raw_pnl = (mark_price - entry) * amount
    else:
        raw_pnl = (entry - mark_price) * amount

    estimated_fee = (entry + mark_price) * amount * 0.0005
    return raw_pnl - estimated_fee
