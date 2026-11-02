"""Explicit candle finality at the market-data snapshot boundary."""

import math
import re
import sys
from typing import Optional

import numpy as np
import pandas as pd

CANDLE_STATUS_CLOSED = "CLOSED"
CANDLE_STATUS_LIVE = "LIVE"
CANDLE_STATUS_UNKNOWN = "UNKNOWN"


def mark_candle_closure(frame: pd.DataFrame, timeframe: str, snapshot_ms: int) -> pd.DataFrame:
    match = re.fullmatch(r"([1-9][0-9]*)([mhdw])", timeframe)
    if not match:
        raise ValueError("Unsupported candle timeframe")
    interval = int(match[1]) * {"m": 60000, "h": 3600000, "d": 86400000, "w": 604800000}[match[2]]
    result = frame.copy()
    if "timestamp" not in result.columns:
        result["is_closed"] = False
        result["candle_status"] = CANDLE_STATUS_UNKNOWN
        result.attrs["timeframe_ms"] = interval
        result.attrs["snapshot_ms"] = snapshot_ms
        return result

    stamps = pd.to_numeric(result["timestamp"], errors="coerce")
    valid_stamps = stamps.notna() & (stamps > 0)

    # 明確區分 CLOSED、LIVE、UNKNOWN
    is_closed_mask = valid_stamps & (stamps + interval <= snapshot_ms)
    result["is_closed"] = is_closed_mask

    status_series = pd.Series(CANDLE_STATUS_UNKNOWN, index=result.index)
    status_series[valid_stamps & is_closed_mask] = CANDLE_STATUS_CLOSED
    status_series[valid_stamps & ~is_closed_mask] = CANDLE_STATUS_LIVE
    result["candle_status"] = status_series

    result.attrs["timeframe_ms"] = interval
    result.attrs["snapshot_ms"] = snapshot_ms
    return result


def closed_entry_candles(frame: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Use explicit finality; unknown/unverified frames are rejected.

    Never reclassify a cached live candle using a later wall-clock time.
    A closed-only snapshot with trusted source marker retains all completed candles.
    """
    if frame is None or frame.empty:
        return pd.DataFrame()

    # 檢查是否有可信任的純收線資料來源標記 (Trusted closed-only marker)
    is_trusted_closed_only = False
    if hasattr(frame, "attrs") and isinstance(frame.attrs, dict):
        if (
            frame.attrs.get("is_closed_only") is True
            or frame.attrs.get("data_source") in ("closed_only", "history_db", "rest_closed")
            or frame.attrs.get("trusted_closed") is True
        ):
            is_trusted_closed_only = True

    if "is_closed" in frame.columns:
        mask = frame["is_closed"].map(lambda value: isinstance(value, (bool, np.bool_)) and bool(value))
        result = frame.loc[mask].copy()
        # Invalid ordering or a closed row after an unfinished row is not a valid snapshot.
        if mask.any():
            last_closed_pos = int(np.flatnonzero(mask.to_numpy())[-1])
            if not mask.iloc[:last_closed_pos + 1].all():
                return frame.iloc[:0].copy()
        else:
            return frame.iloc[:0].copy()
    elif is_trusted_closed_only:
        # 可信任純收線來源：保留全部已收線 K 棒，絕不切除最後一根！
        result = frame.copy()
        result["is_closed"] = True
        result["candle_status"] = CANDLE_STATUS_CLOSED
    else:
        # UNKNOWN 收線狀態：缺少 is_closed 且無可信任來源標記，禁止預設為 True，拒絕通過！
        return frame.iloc[:0].copy()

    # 資料完整性檢驗 (Data Integrity Validation)
    if "timestamp" in result.columns:
        stamps = pd.to_numeric(result["timestamp"], errors="coerce")
        # 1. 檢查 timestamp 缺失 (NaN) 或 非法值 (<= 0)
        if stamps.isna().any() or (stamps <= 0).any():
            return frame.iloc[:0].copy()
        # 2. 檢查重複時間戳
        if stamps.duplicated().any():
            return frame.iloc[:0].copy()
        # 3. 檢查倒序或非單調嚴格遞增
        if not stamps.is_monotonic_increasing or (stamps.diff().iloc[1:] <= 0).any():
            return frame.iloc[:0].copy()
        # 4. 檢查整段時間間隔與中間斷檔
        interval = frame.attrs.get("timeframe_ms") if hasattr(frame, "attrs") else None
        if len(stamps) >= 2:
            diffs = stamps.diff().iloc[1:]
            if interval:
                # 支援毫秒 (interval) 與 秒級 (interval // 1000) 格式相容
                int_val = int(interval)
                sec_val = int_val // 1000 if int_val >= 1000 else int_val
                if ((diffs != int_val) & (diffs != sec_val)).any():
                    return frame.iloc[:0].copy()
            else:
                # 若未指定 timeframe_ms，檢查間隔是否保持一致（單一間隔，無中間斷檔或跳空漏棒）
                if len(diffs.unique()) > 1:
                    return frame.iloc[:0].copy()

    return result


def confirmed_kc_midline_slope(frame: Optional[pd.DataFrame]) -> Optional[float]:
    """Compare only the latest two explicitly completed KC basis values."""
    if not isinstance(frame, pd.DataFrame):
        return None
    closed = closed_entry_candles(frame)
    if len(closed) < 2 or not {"timestamp", "kc_middle"}.issubset(closed.columns):
        return None
    try:
        values = closed.iloc[-2:][["timestamp", "kc_middle"]].to_numpy(dtype=float)
    except (TypeError, ValueError, OverflowError):
        return None
    if not np.isfinite(values).all() or (values <= 0).any():
        return None
    interval = frame.attrs.get("timeframe_ms", 60_000)
    if not isinstance(interval, (int, float)) or not math.isfinite(interval) or interval <= 0:
        return None
    delta = values[1, 0] - values[0, 0]
    if delta not in (interval, interval / 1000):
        return None
    return float(values[1, 1] - values[0, 1])


def log_entry_gate(engine, symbol, side, stage, reason, bar_id=None, **details):
    """Bounded last-decision cache: expose silent gates without per-tick spam."""
    cache = getattr(engine, "_entry_gate_diagnostics", None)
    if not isinstance(cache, dict):
        cache = engine._entry_gate_diagnostics = {}
    key = (symbol, side, stage)
    fingerprint = (bar_id, reason)
    if cache.get(key) == fingerprint:
        return
    cache[key] = fingerprint

    # 同步紀錄至 _intercept_events 供前端 K 線圖繪製攔截 Marker
    if any(kw in str(reason) for kw in ("REJECT", "BLOCKED", "FAIL", "VETO", "CHOP", "WAIT_")):
        events = getattr(engine, "_intercept_events", None)
        if not isinstance(events, dict):
            events = engine._intercept_events = {}
        sym_events = events.setdefault(symbol, [])
        import time as _t
        try:
            ev_ts = int(float(bar_id)) if bar_id and math.isfinite(float(bar_id)) and float(bar_id) > 1e11 else int(_t.time() * 1000)
        except Exception:
            ev_ts = int(_t.time() * 1000)
        sym_events.append({
            "timestamp": ev_ts,
            "symbol": symbol,
            "side": side,
            "stage": stage,
            "reason": str(reason),
            "details": details,
        })
        if len(sym_events) > 100:
            sym_events.pop(0)

    caller = sys._getframe(1)
    source = f"{caller.f_code.co_filename}:{caller.f_lineno}"
    text = f"ENTRY_GATE source={source} stage={stage} symbol={symbol} side={side} bar={bar_id} reason={reason}"
    if details:
        text += " " + " ".join(f"{key}={value}" for key, value in details.items())
    engine.account.log(text, "INFO")
    print(text, flush=True)


def closed_entry_problem(frame):
    required = {"open", "high", "low", "close", "kc_upper", "kc_lower", "atr"}
    if not required.issubset(frame.columns):
        return "WAIT_INVALID_MARKET_DATA"
    try:
        values = frame.iloc[-2:][list(required)].astype(float)
        if not np.isfinite(values.to_numpy()).all() or (values <= 0).any().any():
            return "WAIT_INVALID_MARKET_DATA"
        if (values["kc_lower"] >= values["kc_upper"]).any():
            return "WAIT_INVALID_MARKET_DATA"
    except (TypeError, ValueError):
        return "WAIT_INVALID_MARKET_DATA"
    return None


def entry_frame_evidence(frame):
    """Serializable, closed-only evidence; never advances strategy state."""
    closed = closed_entry_candles(frame)
    if closed.empty:
        return {'closed_count': 0}
    keys = ('timestamp', 'open', 'high', 'low', 'close', 'ma3', 'ma5', 'ma15',
            'atr', 'kc_upper', 'kc_middle', 'kc_lower')
    rows = [{key: float(row[key]) for key in keys if key in row}
            for _, row in closed.tail(6).iterrows()]
    return {'closed_count': len(closed), 'is_closed': True, 'candles': rows,
            'previous5_low': float(closed['low'].iloc[-6:-1].min()) if len(closed) >= 6 else None,
            'previous5_high': float(closed['high'].iloc[-6:-1].max()) if len(closed) >= 6 else None}
