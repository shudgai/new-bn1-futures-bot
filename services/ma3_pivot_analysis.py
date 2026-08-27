"""Quantify MA3 pivot geometry without changing trading decisions."""

from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd


def _atr(frame: pd.DataFrame) -> pd.Series:
    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(14, min_periods=3).mean()


def _bucket(sharpness: float) -> str:
    if sharpness < 0.10:
        return "<0.10 ATR"
    if sharpness < 0.25:
        return "0.10–0.24 ATR"
    if sharpness < 0.50:
        return "0.25–0.49 ATR"
    return "≥0.50 ATR"


def analyze_ma3_pivots(frame: pd.DataFrame, horizon_bars: int = 5,
                       target_atr: float = 0.30, stop_atr: float = 0.25) -> dict:
    """Measure confirmed MA3 V/倒V pivots and their subsequent excursion."""
    required = {"open", "high", "low", "close"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"K線缺少欄位: {', '.join(missing)}")
    if horizon_bars < 1:
        raise ValueError("horizon_bars 必須至少為 1")
    if target_atr <= 0 or stop_atr <= 0:
        raise ValueError("target_atr 與 stop_atr 必須大於 0")

    data = frame.copy().reset_index(drop=True)
    for column in required:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data["ma3"] = data["close"].rolling(3).mean()
    data["atr14"] = _atr(data)
    records: list[dict] = []

    # i is the first closed bar on the right-hand side of the pivot at i - 1.
    for i in range(4, len(data) - horizon_bars):
        left = float(data.at[i - 1, "ma3"] - data.at[i - 2, "ma3"])
        right = float(data.at[i, "ma3"] - data.at[i - 1, "ma3"])
        atr = float(data.at[i, "atr14"])
        if not np.isfinite(atr) or atr <= 0:
            continue
        side = "LONG" if left < 0 < right else "SHORT" if left > 0 > right else None
        if side is None:
            continue

        left_atr, right_atr = abs(left) / atr, abs(right) / atr
        sharpness = left_atr + right_atr
        balance = min(left_atr, right_atr) / max(left_atr, right_atr)
        entry = float(data.at[i, "close"])
        future = data.iloc[i + 1:i + 1 + horizon_bars]
        if side == "LONG":
            target_hit = future["high"] >= entry + target_atr * atr
            stop_hit = future["low"] <= entry - stop_atr * atr
        else:
            target_hit = future["low"] <= entry - target_atr * atr
            stop_hit = future["high"] >= entry + stop_atr * atr

        first_target = int(np.flatnonzero(target_hit.to_numpy())[0]) if target_hit.any() else None
        first_stop = int(np.flatnonzero(stop_hit.to_numpy())[0]) if stop_hit.any() else None
        outcome = (
            "success" if first_target is not None and (first_stop is None or first_target < first_stop)
            else "failure" if first_stop is not None else "unresolved"
        )
        records.append({
            "index": i,
            "timestamp": data.at[i, "timestamp"] if "timestamp" in data.columns else i,
            "side": side,
            "left_slope_atr": round(left_atr, 4),
            "right_slope_atr": round(right_atr, 4),
            "sharpness_atr": round(sharpness, 4),
            "balance": round(balance, 4),
            "bucket": _bucket(sharpness),
            "outcome": outcome,
        })

    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        grouped[record["bucket"]].append(record)
    buckets = []
    for label in ("<0.10 ATR", "0.10–0.24 ATR", "0.25–0.49 ATR", "≥0.50 ATR"):
        rows = grouped[label]
        resolved = [row for row in rows if row["outcome"] != "unresolved"]
        buckets.append({
            "sharpness": label,
            "pivots": len(rows),
            "resolved": len(resolved),
            "success_rate": round(sum(row["outcome"] == "success" for row in resolved) / len(resolved), 4) if resolved else None,
        })
    return {
        "definition": {
            "sharpness": "(|左側MA3斜率| + |右側MA3斜率|) / ATR14",
            "balance": "較小斜率 / 較大斜率；越接近 1，V 型越對稱",
            "confirmation": "第一根右側收線，避免使用會重繪的未收線 K",
        },
        "settings": {"horizon_bars": horizon_bars, "target_atr": target_atr, "stop_atr": stop_atr},
        "summary": {"pivots": len(records), "resolved": sum(row["outcome"] != "unresolved" for row in records)},
        "buckets": buckets,
        "recent_pivots": records[-30:],
    }
