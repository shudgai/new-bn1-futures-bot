"""
SHADOW PARITY TEST — Requirement 7 & 8

Tests:
  7. Duplicate suppression: same closed frame twice -> zero duplicate signals.
     Restart: reload state + same frame -> zero duplicate signals.

  8. Historical parity: feed 6000-bar replay dataset bar-by-bar through live
     shadow detector. Compare LONG/SHORT timestamps with frozen offline Control.
     Report mismatch count and first 10 mismatches.

PRODUCTION_CHANGED = NO
"""
import sys, json, os, types
sys.path.insert(0, '/home/shudgai999/project/new bn')

# ── Redirect shadow log to a temp file for this test ─────────────────────────
import core.services.reversal_shadow_logger as _rsl
_rsl._LOG_PATH    = "/tmp/shadow_parity_test.jsonl"
_rsl._DEDUPE_PATH = "/tmp/shadow_parity_test_dedupe.json"
for f in [_rsl._LOG_PATH, _rsl._DEDUPE_PATH]:
    try: os.remove(f)
    except FileNotFoundError: pass

import numpy as np
import pandas as pd

# ── Load dataset (same as trade_replay.py) ────────────────────────────────────
def load_df(path):
    raw = json.load(open(path))
    cols = ['timestamp','open','high','low','close','volume','close_time','qav','trades','tbb','tbq']
    df = pd.DataFrame(raw, columns=cols)
    df = df.drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)
    for c in ['timestamp','open','high','low','close']: df[c] = pd.to_numeric(df[c])
    return df

df4 = load_df('/home/shudgai999/project/new bn/scratch/v4_oos_klines.json')
dfh = load_df('/home/shudgai999/project/new bn/scratch/holdout_v1_klines.json')
df  = pd.concat([dfh, df4], ignore_index=True).drop_duplicates(subset=['timestamp']).sort_values('timestamp').reset_index(drop=True)
df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
df['ma3']  = df['close'].rolling(3).mean()
df['ma5']  = df['close'].rolling(5).mean()
df['ma15'] = df['close'].rolling(15).mean()
df['tr']   = pd.concat([df['high']-df['low'],
                         (df['high']-df['close'].shift(1)).abs(),
                         (df['low']-df['close'].shift(1)).abs()], axis=1).max(axis=1)
df['atr']       = df['tr'].rolling(14).mean()
df['kc_middle'] = df['close'].rolling(20).mean()
df.attrs['timeframe_ms'] = 60000
print(f"Dataset: {len(df)} bars  {df['datetime'].iloc[0]} -> {df['datetime'].iloc[-1]}", flush=True)

# ── Mock engine ───────────────────────────────────────────────────────────────
mock_engine = types.SimpleNamespace(account=types.SimpleNamespace(trades=[]))

def make_frame(df, up_to_t):
    """Return a frame with indicators through bar t (inclusive), last bar live."""
    sub = df.iloc[:up_to_t + 1].copy()
    sub.attrs['timeframe_ms'] = 60000
    return sub

# ── Offline reference (frozen collect_new CONTROL) ────────────────────────────
def body_ratio(r):
    rng = r['high'] - r['low']
    return abs(r['close'] - r['open']) / rng if rng > 1e-9 else 0.0

def collect_control():
    lookback, isolation, min_body = 5, 2, 0.5
    signals, consumed = [], set()
    for t in range(50, len(df) - 2):
        c1, c = df.iloc[t-1], df.iloc[t]
        br  = body_ratio(c)
        atr = float(c['atr'])
        if atr <= 0: continue
        for side in ('LONG', 'SHORT'):
            if side == 'LONG':
                if not (c['close'] > c['open'] and br >= min_body
                        and c['ma5'] >= c1['ma5'] and c['close'] > c['ma5']): continue
                for p in range(t-15, t-isolation+1):
                    if p < lookback or (side, p) in consumed: continue
                    if df['low'].iloc[p-isolation:t+1].idxmin() == p:
                        if (df['close'].iloc[p] < df['close'].iloc[p-lookback]
                                and df['ma15'].iloc[p] <= df['ma15'].iloc[p-1]):
                            pv = float(df['low'].iloc[p])
                            consumed.add((side, p))
                            signals.append({'side': 'LONG', 't': t, 'ts': int(df['timestamp'].iloc[t]), 'p': p})
                            break
            else:
                if not (c['close'] < c['open'] and br >= min_body
                        and c['ma5'] <= c1['ma5'] and c['close'] < c['ma5']): continue
                for p in range(t-15, t-isolation+1):
                    if p < lookback or (side, p) in consumed: continue
                    if df['high'].iloc[p-isolation:t+1].idxmax() == p:
                        if (df['close'].iloc[p] > df['close'].iloc[p-lookback]
                                and df['ma15'].iloc[p] >= df['ma15'].iloc[p-1]):
                            pv = float(df['high'].iloc[p])
                            consumed.add((side, p))
                            signals.append({'side': 'SHORT', 't': t, 'ts': int(df['timestamp'].iloc[t]), 'p': p})
                            break
    return signals

print("Running offline collect_control()...", flush=True)
ref_sigs = collect_control()
ref_long  = [s['ts'] for s in ref_sigs if s['side'] == 'LONG']
ref_short = [s['ts'] for s in ref_sigs if s['side'] == 'SHORT']
print(f"Offline: LONG={len(ref_long)} SHORT={len(ref_short)}", flush=True)

# ── Test 7: Duplicate suppression ─────────────────────────────────────────────
print("\n=== TEST 7: Duplicate Suppression ===", flush=True)
_rsl._state.clear()
_rsl._log_initialized = False
for f in [_rsl._LOG_PATH, _rsl._DEDUPE_PATH]:
    try: os.remove(f)
    except FileNotFoundError: pass

# Use bar 200 as test frame (well into valid range)
test_t = 200
frame_200 = make_frame(df, test_t)

_rsl.record_reversal_shadow_candidates(mock_engine, 'BTCUSDT', frame_200)
sigs_after_first = [l for l in open(_rsl._LOG_PATH).readlines() if '"SIGNAL_OPEN"' in l]
n_first = len(sigs_after_first)

# Call again with same frame
_rsl.record_reversal_shadow_candidates(mock_engine, 'BTCUSDT', frame_200)
sigs_after_second = [l for l in open(_rsl._LOG_PATH).readlines() if '"SIGNAL_OPEN"' in l]
n_second = len(sigs_after_second)

dup_same_frame = n_second - n_first
print(f"Same frame twice: signals after 1st={n_first}, after 2nd={n_second}", flush=True)
print(f"  Duplicates from same-frame replay: {dup_same_frame}  {'✅ PASS' if dup_same_frame == 0 else '❌ FAIL'}", flush=True)

# Simulate restart: clear in-memory state, reload from log
_rsl._state.clear()
_rsl._log_initialized = False
_rsl.record_reversal_shadow_candidates(mock_engine, 'BTCUSDT', frame_200)
sigs_after_restart = [l for l in open(_rsl._LOG_PATH).readlines() if '"SIGNAL_OPEN"' in l]
n_restart = len(sigs_after_restart)
dup_restart = n_restart - n_second
print(f"After restart + same frame: total SIGNAL_OPEN={n_restart}", flush=True)
print(f"  Duplicates after restart: {dup_restart}  {'✅ PASS' if dup_restart == 0 else '❌ FAIL'}", flush=True)

# ── Test 8: Historical Parity ─────────────────────────────────────────────────
print("\n=== TEST 8: Historical Parity ===", flush=True)
_rsl._state.clear()
_rsl._log_initialized = False
for f in [_rsl._LOG_PATH, _rsl._DEDUPE_PATH]:
    try: os.remove(f)
    except FileNotFoundError: pass

SYMBOL = 'PARITY_TEST'
for t in range(50, len(df)):
    frame = make_frame(df, t)
    _rsl.record_reversal_shadow_candidates(mock_engine, SYMBOL, frame)

# Parse shadow signals
shadow_long, shadow_short = [], []
with open(_rsl._LOG_PATH) as f:
    for line in f:
        try:
            ev = json.loads(line)
            if ev.get('event') != 'SIGNAL_OPEN' or ev.get('symbol') != SYMBOL: continue
            ts = ev['entry_ts_ms']
            if ev['side'] == 'LONG':
                shadow_long.append(ts)
            else:
                shadow_short.append(ts)
        except Exception: continue

print(f"Shadow:  LONG={len(shadow_long)} SHORT={len(shadow_short)}", flush=True)
print(f"Offline: LONG={len(ref_long)}    SHORT={len(ref_short)}", flush=True)

def compare(shadow_ts, ref_ts, side):
    shadow_set = set(shadow_ts)
    ref_set    = set(ref_ts)
    only_shadow = sorted(shadow_set - ref_set)
    only_ref    = sorted(ref_set - shadow_set)
    matched     = len(shadow_set & ref_set)
    total_ref   = len(ref_set)
    mismatches  = len(only_shadow) + len(only_ref)
    print(f"\n  {side}:")
    print(f"    Matched:        {matched}/{total_ref}  ({matched/total_ref*100:.1f}% of offline)" if total_ref else "  No offline signals")
    print(f"    Only in shadow: {len(only_shadow)}")
    print(f"    Only in offline:{len(only_ref)}")
    print(f"    Total mismatches: {mismatches}  {'✅ PASS' if mismatches == 0 else '❌ FAIL'}")
    if only_shadow[:10]:
        print(f"    First 10 shadow-only: {[pd.Timestamp(ts, unit='ms') for ts in only_shadow[:10]]}")
    if only_ref[:10]:
        print(f"    First 10 ref-only:    {[pd.Timestamp(ts, unit='ms') for ts in only_ref[:10]]}")

compare(shadow_long,  ref_long,  'LONG')
compare(shadow_short, ref_short, 'SHORT')

print("\nPRODUCTION_CHANGED = NO")
