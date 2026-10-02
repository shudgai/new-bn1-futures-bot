"""
REVERSAL SHADOW LOGGER v2 — Parity-fixed for live forward shadow.

Parity fixes (2026-10-02):
  1. Pivot iteration: range(t-15, t-2) oldest→newest, matches frozen offline Control
  2. argmin/argmax check, not just min/max (matches idxmin/idxmax)
  3. Persistent dedupe: symbol+side+pivot_ts_ms survives restart
  4. Startup restore: consumed pivots loaded from existing JSONL
  5. Production matching: same direction, ≤30 bars, pivot survival, one-to-one
  6. Frozen initial_risk_pct at SIGNAL_OPEN; shadow_R never changes
  7. True append-only event log (SIGNAL_OPEN / PRODUCTION_MATCH / SHADOW_CLOSE)
  8. pivot_timestamp_ms in every SIGNAL_OPEN event

PRODUCTION_CHANGED = NO
"""
from __future__ import annotations
import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ReversalShadow")

# ── Frozen constants (must not change without re-running parity test) ─────────
_LOOKBACK     = 5
_ISOLATION    = 2
_MIN_BODY     = 0.5
_SL_ATR_MULT  = 1.5   # production initial SL (dual_track_exit_service.py SL_INIT_MULT)
_H2_MOVE_MIN  = 1.5
_H2_MA3_MIN   = 0.05
_H3_MOVE_MIN  = 2.0
_MATCH_WINDOW = 30    # max bars after shadow entry to accept production match

_LOG_PATH    = "logs/reversal_shadow.jsonl"
_DEDUPE_PATH = "logs/reversal_shadow_dedupe.json"

# ── Module-level state ────────────────────────────────────────────────────────
# { symbol: {
#     'consumed_l': set of pivot_ts_ms (LONG consumed pivots),
#     'consumed_s': set of pivot_ts_ms (SHORT consumed pivots),
#     'open_shadows': list of shadow dicts,
#     'used_prod_entries': set of (ts_ms_str, side) used for matching
# }}
_state: Dict[str, Any] = {}
_log_initialized = False


# ─────────────────────────────────────────────────────────────────────────────
# Persistence helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_log_dir() -> None:
    os.makedirs("logs", exist_ok=True)


def _append_event(event: Dict) -> None:
    """True append-only write. Never reads or rewrites the file."""
    try:
        _ensure_log_dir()
        event.setdefault("logged_at_ms", int(time.time() * 1000))
        with open(_LOG_PATH, "a") as f:
            f.write(json.dumps(event) + "\n")
    except Exception:
        pass


def _save_dedupe() -> None:
    """Persist consumed pivot timestamps to survive restart."""
    try:
        _ensure_log_dir()
        out: Dict[str, Dict[str, List[int]]] = {}
        for sym, st in _state.items():
            out[sym] = {
                "l": sorted(st.get("consumed_l", set())),
                "s": sorted(st.get("consumed_s", set())),
            }
        with open(_DEDUPE_PATH, "w") as f:
            json.dump(out, f)
    except Exception:
        pass


def _initialize_from_log() -> None:
    """
    On first call, restore consumed pivots and re-open open shadows from JSONL.
    Called once lazily to avoid penalizing normal bar-close latency.
    """
    global _log_initialized
    if _log_initialized:
        return
    _log_initialized = True

    # 1. Restore consumed pivots from dedupe file (fast path)
    try:
        if os.path.exists(_DEDUPE_PATH):
            with open(_DEDUPE_PATH) as f:
                raw = json.load(f)
            for sym, sides in raw.items():
                st = _state.setdefault(sym, {})
                st["consumed_l"] = set(int(x) for x in sides.get("l", []))
                st["consumed_s"] = set(int(x) for x in sides.get("s", []))
    except Exception:
        pass

    # 2. Restore open shadows from JSONL (find SIGNAL_OPEN without SHADOW_CLOSE)
    if not os.path.exists(_LOG_PATH):
        return
    try:
        opens: Dict[str, Dict] = {}
        closes: set = set()
        prod_matches: set = set()
        with open(_LOG_PATH) as f:
            for line in f:
                try:
                    ev = json.loads(line)
                    sid = ev.get("signal_id")
                    evt = ev.get("event")
                    if not sid or not evt:
                        continue
                    if evt == "SIGNAL_OPEN":
                        opens[sid] = ev
                    elif evt == "SHADOW_CLOSE":
                        closes.add(sid)
                    elif evt == "PRODUCTION_MATCH":
                        prod_matches.add(sid)
                except Exception:
                    continue

        for sid, ev in opens.items():
            if sid in closes:
                continue
            sym  = ev.get("symbol")
            side = ev.get("side")
            ep   = float(ev.get("entry_price", 0) or 0)
            atr  = float(ev.get("entry_atr",   0) or 0)
            ts   = int(ev.get("entry_ts_ms",   0) or 0)
            isl  = float(ev.get("initial_sl",  0) or 0)
            irp  = float(ev.get("initial_risk_pct", 0) or 0)
            pv   = float(ev.get("pivot_price", 0) or 0)
            pts  = int(ev.get("pivot_timestamp_ms", 0) or 0)
            if not all([sym, side, ep > 0, atr > 0, ts > 0]):
                continue
            pos = _init_shadow_position(side, ep, atr, ts)
            pos["sl"] = isl  # re-freeze to initial SL
            st  = _state.setdefault(sym, {})
            st.setdefault("open_shadows", []).append({
                "signal_id":        sid,
                "side":             side,
                "entry_price":      ep,
                "entry_atr":        atr,
                "entry_ts_ms":      ts,
                "pivot_price":      pv,
                "pivot_ts_ms":      pts,
                "initial_sl":       isl,
                "initial_risk_pct": irp,
                "pos":              pos,
                "bars_held":        0,
                "bars_since_entry": 0,
                "pivot_invalidated": False,
                "prod_matched":     sid in prod_matches,
                "prod_exhausted":   False,
            })
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Candidate detection — 1:1 with frozen offline Control
# ─────────────────────────────────────────────────────────────────────────────

def _body_ratio(open_: float, high: float, low: float, close: float) -> float:
    rng = high - low
    return abs(close - open_) / rng if rng > 1e-9 else 0.0


def _argmin_pos(closed, start: int, end_inclusive: int) -> int:
    """Return positional index of minimum low in [start, end_inclusive]. First occurrence wins."""
    try:
        sub = closed['low'].iloc[start:end_inclusive + 1]
        return start + int(sub.argmin())
    except Exception:
        return -1


def _argmax_pos(closed, start: int, end_inclusive: int) -> int:
    """Return positional index of maximum high in [start, end_inclusive]. First occurrence wins."""
    try:
        sub = closed['high'].iloc[start:end_inclusive + 1]
        return start + int(sub.argmax())
    except Exception:
        return -1


def _detect_candidates(closed, symbol: str) -> List[Dict]:
    """
    Detect CONTROL reversal candidates from the last closed bar.
    Iteration: range(t-15, t-isolation+1) i.e. oldest→newest pivot candidates.
    Pivot check: argmin/argmax equivalent to offline idxmin/idxmax.
    """
    try:
        n = len(closed)
        if n < _LOOKBACK + _ISOLATION + 15:
            return []

        t  = n - 1  # index of latest closed bar (confirmation bar)
        c  = closed.iloc[t]
        c1 = closed.iloc[t - 1]

        atr   = float(c.get("atr",  0) or 0)
        ma3   = float(c.get("ma3",  0) or 0)
        ma5   = float(c.get("ma5",  0) or 0)
        ma5_1 = float(c1.get("ma5", 0) or 0)
        op, hi, lo, cl = (float(c.get(k, 0) or 0) for k in ("open", "high", "low", "close"))

        if atr <= 0 or ma5 <= 0:
            return []

        br     = _body_ratio(op, hi, lo, cl)
        sym_st = _state.setdefault(symbol, {})
        candidates: List[Dict] = []

        # ── LONG ──────────────────────────────────────────────────────────────
        if cl > op and br >= _MIN_BODY and ma5 >= ma5_1 and cl > ma5:
            consumed_l = sym_st.setdefault("consumed_l", set())
            # oldest→newest, matching offline range(t-15, t-isolation+1)
            for p in range(t - 15, t - _ISOLATION + 1):
                if p < _LOOKBACK:
                    continue
                p_ts = int(closed.iloc[p].get("timestamp", 0) or 0)
                if p_ts in consumed_l:
                    continue
                # Is p the argmin of lows in [p-isolation, t]?
                if _argmin_pos(closed, max(0, p - _ISOLATION), t) != p:
                    continue
                p_close  = float(closed.iloc[p].get("close", 0) or 0)
                p5_close = float(closed.iloc[p - _LOOKBACK].get("close", 0) or 0)
                p_ma15   = float(closed.iloc[p].get("ma15", 0) or 0)
                p1_ma15  = float(closed.iloc[p - 1].get("ma15", 0) or 0)
                if p_close < p5_close and p_ma15 <= p1_ma15:
                    pv         = float(closed.iloc[p].get("low", 0) or 0)
                    move_atr   = (cl - pv) / atr
                    ma3_vs_ma5 = (ma3 - ma5) / atr
                    h2_pass    = move_atr >= _H2_MOVE_MIN and ma3_vs_ma5 >= _H2_MA3_MIN
                    consumed_l.add(p_ts)
                    candidates.append({
                        "side": "LONG", "p_val": pv, "pivot_ts_ms": p_ts,
                        "move_atr": round(move_atr, 4),
                        "ma3_vs_ma5_atr": round(ma3_vs_ma5, 4),
                        "h2_pass": h2_pass, "h3_pass": False,
                        "entry_price": cl, "entry_atr": atr,
                    })
                    break  # first valid pivot wins

        # ── SHORT ─────────────────────────────────────────────────────────────
        if cl < op and br >= _MIN_BODY and ma5 <= ma5_1 and cl < ma5:
            consumed_s = sym_st.setdefault("consumed_s", set())
            for p in range(t - 15, t - _ISOLATION + 1):
                if p < _LOOKBACK:
                    continue
                p_ts = int(closed.iloc[p].get("timestamp", 0) or 0)
                if p_ts in consumed_s:
                    continue
                # Is p the argmax of highs in [p-isolation, t]?
                if _argmax_pos(closed, max(0, p - _ISOLATION), t) != p:
                    continue
                p_close  = float(closed.iloc[p].get("close", 0) or 0)
                p5_close = float(closed.iloc[p - _LOOKBACK].get("close", 0) or 0)
                p_ma15   = float(closed.iloc[p].get("ma15", 0) or 0)
                p1_ma15  = float(closed.iloc[p - 1].get("ma15", 0) or 0)
                if p_close > p5_close and p_ma15 >= p1_ma15:
                    pv         = float(closed.iloc[p].get("high", 0) or 0)
                    move_atr   = (pv - cl) / atr
                    ma3_vs_ma5 = (ma5 - ma3) / atr
                    h3_pass    = move_atr >= _H3_MOVE_MIN
                    consumed_s.add(p_ts)
                    candidates.append({
                        "side": "SHORT", "p_val": pv, "pivot_ts_ms": p_ts,
                        "move_atr": round(move_atr, 4),
                        "ma3_vs_ma5_atr": round(ma3_vs_ma5, 4),
                        "h2_pass": False, "h3_pass": h3_pass,
                        "entry_price": cl, "entry_atr": atr,
                    })
                    break

        return candidates
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Position helpers
# ─────────────────────────────────────────────────────────────────────────────

def _init_shadow_position(side: str, entry_price: float,
                           entry_atr: float, bar_ts_ms: int) -> Dict:
    """Production-equivalent position dict. SL = entry ± 1.5 ATR (dual_track_exit_service.py:36)."""
    from core.services.exits.peak_trailing_exit import migrate_peak_state
    sign = 1 if side == "LONG" else -1
    sl   = entry_price - sign * _SL_ATR_MULT * entry_atr
    qty  = 1.0 / entry_price
    pos  = {
        "side": side,
        "open_timestamp": float(bar_ts_ms) / 1000.0,  # seconds (production convention)
        "entry_price": entry_price,
        "qty": qty,
        "margin": entry_price * qty,
        "entry_atr": entry_atr,
        "symbol": "SHADOW",
        "leverage": 1,
        "sl": sl, "stop_loss": sl, "initial_sl": sl, "atr_sl": sl,
    }
    migrate_peak_state(pos)
    return pos


def _make_snapshot(bar, bar_prev) -> Dict:
    ts      = int(bar.get("timestamp", 0) or 0)
    ts_prev = int(bar_prev.get("timestamp", 0) or 0)
    return {
        "quote_ms":      ts + 59999,
        "live_bar_ms":   ts,
        "closed_bar_ms": ts_prev,
        "live_open":     float(bar.get("open",    0) or 0),
        "atr":           float(bar_prev.get("atr", 0) or 0),
        "kc_middle":     float(bar.get("kc_middle", 0) or 0),
        "ma5":           float(bar.get("ma5",     0) or 0),
        "last_ma5":      float(bar_prev.get("ma5", 0) or 0),
        "ma15":          float(bar.get("ma15",    0) or 0),
        "last_open":     float(bar_prev.get("open",  0) or 0),
        "last_high":     float(bar_prev.get("high",  0) or 0),
        "last_low":      float(bar_prev.get("low",   0) or 0),
        "last_close":    float(bar_prev.get("close", 0) or 0),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def record_reversal_shadow_candidates(engine, symbol: str, frame) -> None:
    """
    Call after each 1m closed bar. 100% fail-open. Never places orders.
    Hook in symbol_runner.py after _channel_exit_frames is written.
    """
    try:
        _initialize_from_log()

        from core.config import TAKER_FEE_RATE, SLIPPAGE_PCT
        from core.services.exits.peak_trailing_exit import evaluate_peak_trailing
        from core.services.candle_data import closed_entry_candles

        fee, slip = TAKER_FEE_RATE, SLIPPAGE_PCT
        now_ms = int(time.time() * 1000)

        closed = closed_entry_candles(frame)
        if len(closed) < 3:
            return

        bar_curr = closed.iloc[-1]
        bar_prev = closed.iloc[-2]
        curr_ts  = int(bar_curr.get("timestamp", 0) or 0)

        sym_st = _state.setdefault(symbol, {})

        # ── 1. Step open shadows forward one bar ──────────────────────────────
        open_shadows: List[Dict] = sym_st.get("open_shadows", [])
        still_open: List[Dict] = []
        prod_trades = getattr(getattr(engine, "account", None), "trades", [])
        used_prod   = sym_st.setdefault("used_prod_entries", set())
        dedupe_dirty = False

        for sh in open_shadows:
            try:
                pos    = sh["pos"]
                side   = sh["side"]
                sign   = 1 if side == "LONG" else -1
                sl_now = float(pos.get("sl") or 0)
                lo_bar = float(bar_curr.get("low",  0) or 0)
                hi_bar = float(bar_curr.get("high", 0) or 0)
                cl_bar = float(bar_curr.get("close", 0) or 0)

                sh["bars_held"]        = sh.get("bars_held", 0) + 1
                sh["bars_since_entry"] = sh.get("bars_since_entry", 0) + 1

                # ── Pivot survival check (before exit eval) ────────────────
                if not sh.get("pivot_invalidated"):
                    p_val = sh.get("pivot_price", 0)
                    if side == "LONG" and p_val > 0 and lo_bar < p_val:
                        sh["pivot_invalidated"] = True
                    elif side == "SHORT" and p_val > 0 and hi_bar > p_val:
                        sh["pivot_invalidated"] = True

                # ── Production match check ─────────────────────────────────
                bse = sh["bars_since_entry"]
                if (not sh.get("prod_matched") and not sh.get("prod_exhausted")
                        and not sh.get("pivot_invalidated")):
                    if bse > _MATCH_WINDOW:
                        sh["prod_exhausted"] = True
                    else:
                        sig_ts = sh["entry_ts_ms"]
                        for tr in reversed(prod_trades[-50:]):
                            tr_ts     = float(tr.get("timestamp") or tr.get("timestamp_ms") or 0)
                            tr_side   = str(tr.get("side") or "")
                            tr_action = str(tr.get("action") or "")
                            tr_key    = (str(int(tr_ts)), tr_side)
                            if tr_key in used_prod:
                                continue
                            bars_adv = round((tr_ts - sig_ts) / 60000)
                            if (tr_action in ("OPEN_LONG", "OPEN_SHORT")
                                    and tr_ts > sig_ts
                                    and tr_ts <= curr_ts
                                    and tr_side == side
                                    and 0 < bars_adv <= _MATCH_WINDOW):
                                prod_p = float(tr.get("price") or tr.get("fill_price") or 0)
                                p_adv  = ((prod_p - sh["entry_price"]) / prod_p * 100 * sign
                                          if prod_p > 0 else None)
                                used_prod.add(tr_key)
                                sh["prod_matched"] = True
                                _append_event({
                                    "event":                  "PRODUCTION_MATCH",
                                    "signal_id":              sh["signal_id"],
                                    "production_entry_ts_ms": tr_ts,
                                    "bars_advantage":         bars_adv,
                                    "price_advantage_pct":    round(p_adv, 4) if p_adv else None,
                                    "pivot_survived":         not sh.get("pivot_invalidated", False),
                                })
                                break

                # ── Hard stop via bar extreme ──────────────────────────────
                exit_p, exit_r = None, None
                if sl_now > 0:
                    if side == "LONG" and lo_bar <= sl_now:
                        exit_p, exit_r = sl_now * (1 - slip), "EXIT_PRODUCTION_ATR_STOP"
                    elif side == "SHORT" and hi_bar >= sl_now:
                        exit_p, exit_r = sl_now * (1 + slip), "EXIT_PRODUCTION_ATR_STOP"

                # ── evaluate_peak_trailing at bar close ────────────────────
                if exit_p is None:
                    snap = _make_snapshot(bar_curr, bar_prev)
                    res  = evaluate_peak_trailing(
                        pos, cl_bar, snap,
                        atr=float(bar_curr.get("atr", 0) or 0),
                        fee=fee, slippage=slip)
                    if res and res.get("action") == "FULL_CLOSE":
                        exit_p = cl_bar * (1 - sign * slip)
                        exit_r = res.get("trigger", res.get("type", "EXIT"))

                if exit_p is not None:
                    ep             = sh["entry_price"]
                    entry_fill     = ep * (1 + sign * slip)
                    net_pnl        = (sign * (exit_p - entry_fill)
                                      - (entry_fill + exit_p) * fee) / entry_fill * 100
                    # R uses INITIAL risk (frozen at signal creation)
                    irp            = sh.get("initial_risk_pct", 0)
                    r_val          = net_pnl / irp if irp > 1e-9 else None
                    _append_event({
                        "event":              "SHADOW_CLOSE",
                        "signal_id":          sh["signal_id"],
                        "shadow_exit_reason": exit_r,
                        "shadow_bars_held":   sh["bars_held"],
                        "shadow_net_pnl_pct": round(net_pnl, 6),
                        "shadow_R":           round(r_val, 4) if r_val is not None else None,
                        "pivot_survived_at_close": not sh.get("pivot_invalidated", False),
                    })
                else:
                    still_open.append(sh)

            except Exception:
                still_open.append(sh)

        sym_st["open_shadows"] = still_open

        # ── 2. Detect new candidates ──────────────────────────────────────────
        new_cands = _detect_candidates(closed, symbol)
        for cand in new_cands:
            try:
                side      = cand["side"]
                entry_p   = cand["entry_price"]
                entry_atr = cand["entry_atr"]
                signal_id = f"REV_{symbol}_{side}_{curr_ts}"

                pos        = _init_shadow_position(side, entry_p, entry_atr, curr_ts)
                isl        = float(pos.get("sl", 0))
                sign       = 1 if side == "LONG" else -1
                entry_fill = entry_p * (1 + sign * slip)
                irp        = abs(entry_fill - isl) / entry_fill * 100  # FROZEN

                _append_event({
                    "event":               "SIGNAL_OPEN",
                    "signal_id":           signal_id,
                    "symbol":              symbol,
                    "side":                side,
                    "entry_ts_ms":         curr_ts,
                    "entry_datetime":      datetime.fromtimestamp(
                                              curr_ts / 1000, tz=timezone.utc
                                           ).isoformat(),
                    "entry_price":         entry_p,
                    "entry_atr":           entry_atr,
                    "initial_sl":          isl,
                    "initial_risk_pct":    round(irp, 6),  # frozen; R denominator
                    "pivot_price":         cand["p_val"],
                    "pivot_timestamp_ms":  cand["pivot_ts_ms"],
                    "move_atr":            cand["move_atr"],
                    "ma3_vs_ma5_atr":      cand["ma3_vs_ma5_atr"],
                    "control":             True,
                    "h2_pass":             cand["h2_pass"],
                    "h3_pass":             cand["h3_pass"],
                })

                sym_st.setdefault("open_shadows", []).append({
                    "signal_id":        signal_id,
                    "side":             side,
                    "entry_price":      entry_p,
                    "entry_atr":        entry_atr,
                    "entry_ts_ms":      curr_ts,
                    "pivot_price":      cand["p_val"],
                    "pivot_ts_ms":      cand["pivot_ts_ms"],
                    "initial_sl":       isl,
                    "initial_risk_pct": irp,
                    "pos":              pos,
                    "bars_held":        0,
                    "bars_since_entry": 0,
                    "pivot_invalidated": False,
                    "prod_matched":     False,
                    "prod_exhausted":   False,
                })
                dedupe_dirty = True
                logger.info(
                    f"REVERSAL_SHADOW {signal_id} move_atr={cand['move_atr']} "
                    f"h2={cand['h2_pass']} h3={cand['h3_pass']}"
                )
            except Exception:
                continue

        if dedupe_dirty:
            _save_dedupe()

    except Exception:
        pass  # 100% fail-open
