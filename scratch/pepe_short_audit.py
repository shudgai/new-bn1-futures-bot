"""
Entry Audit — 1000PEPE/USDT  2026-10-02 00:45–01:10 UTC (UTC+8 08:45–09:10)

Usage:
    cd "/home/shudgai999/project/new bn"
    .venv/bin/python3 scratch/pepe_short_audit.py
"""
import asyncio, os, sys, datetime, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BOT_SYMBOL   = '1000PEPE/USDT'
CCXT_SYMBOL  = '1000PEPE/USDT'
# UTC 00:40 — give 15 min buffer before window
WINDOW_START_MS = 1759459200000 - 900_000   # 00:40 UTC
WINDOW_END_MS   = 1759459200000 + 1500_000  # 01:10 UTC

async def main():
    from dotenv import load_dotenv
    load_dotenv()

    import ccxt.async_support as ccxt
    exchange = ccxt.binanceusdm({'options': {'defaultType': 'future'}})
    try:
        await exchange.load_markets()
        # find correct ccxt symbol
        sym = None
        for k in exchange.markets:
            if '1000PEPE' in k.upper() and 'USDT' in k:
                sym = k
                break
        if sym is None:
            print("ERROR: 1000PEPE/USDT not found in markets"); await exchange.close(); return
        print(f"Using ccxt symbol: {sym}")
        raw = await exchange.fetch_ohlcv(sym, '1m', limit=200)
    except Exception as e:
        print(f"ERROR: {e}")
        await exchange.close()
        return
    finally:
        await exchange.close()

    import pandas as pd
    df = pd.DataFrame(raw, columns=['timestamp','open','high','low','close','volume'])
    for col in ['open','high','low','close']:
        df[col] = df[col].astype(float)
    df['timestamp'] = df['timestamp'].astype(float)

    from core.strategy import SuperTrendKeltnerStrategy
    strat = SuperTrendKeltnerStrategy()
    df = strat.compute_indicators(df.copy())
    df['is_closed'] = True
    df['ma5_slope']  = df['ma5'].diff()
    df['ma15_slope'] = df['ma15'].diff()

    # ── PART 1: Print window candle table ──────────────────────────────
    win = df[(df['timestamp'] >= WINDOW_START_MS) & (df['timestamp'] <= WINDOW_END_MS)].copy()
    if win.empty:
        win = df[df['timestamp'] >= WINDOW_START_MS].copy()

    def ts2t(ms):
        return datetime.datetime.utcfromtimestamp(int(ms)//1000).strftime('%H:%M:%S')

    print("\n" + "═"*110)
    print(f"  PART 1 — Candle Table  1000PEPE/USDT  UTC 00:40–01:10 (UTC+8 08:40–09:10)")
    print("═"*110)
    hdr = (f"{'time':>8} {'bar_id':>15} {'open':>11} {'high':>11} {'low':>11} {'close':>11} "
           f"{'body':>8} {'KC_lo':>11} {'MA5':>11} {'MA15':>11} "
           f"{'MA5_sl':>10} {'MA15_sl':>10} {'ATR':>10}")
    print(hdr)

    for _, row in win.iterrows():
        t    = ts2t(row['timestamp'])
        bid  = int(row['timestamp'])
        body = row['close'] - row['open']
        kc_lo   = row.get('kc_lower', float('nan'))
        ma5     = row.get('ma5', float('nan'))
        ma15    = row.get('ma15', float('nan'))
        ma5_sl  = row.get('ma5_slope', float('nan'))
        ma15_sl = row.get('ma15_slope', float('nan'))
        atr     = row.get('atr', float('nan'))
        below = '↓KC' if row['close'] < kc_lo else '   '
        red   = '🔴' if body < 0 else '🟢'
        print(f"{t} {red}{below} {bid:>15} {row['open']:>11.6f} {row['high']:>11.6f} "
              f"{row['low']:>11.6f} {row['close']:>11.6f} "
              f"{body:>+8.6f} {kc_lo:>11.6f} {ma5:>11.6f} {ma15:>11.6f} "
              f"{ma5_sl:>+10.6f} {ma15_sl:>+10.6f} {atr:>10.6f}")

    # ── PART 2+3+4: Bar-by-bar gate scan ───────────────────────────────
    from core.services.kc_pending_entry import (
        evaluate_kc_pending_entry, _INVALIDATED_SIGNALS,
        _make_signal_identity, MAX_DISTANCE_ATR
    )
    _INVALIDATED_SIGNALS.clear()   # simulate fresh restart

    full_closed = df[df['is_closed']].copy()

    print("\n" + "═"*110)
    print("  PART 2/3/4 — Bar-by-bar Gate Scan")
    print("═"*110)

    prev_state = None
    first_enter = None
    pending_info = None   # capture latest SHORT_PENDING detail
    invalidated_log = []  # capture all invalidation events

    for i in range(2, len(full_closed)):
        sub  = full_closed.iloc[:i+1].copy()
        last = sub.iloc[-1]

        # only show near the window
        if last['timestamp'] < WINDOW_START_MS - 300_000:
            # still run evaluate to keep _INVALIDATED_SIGNALS populated correctly
            evaluate_kc_pending_entry(sub, float(last['close']),
                                      code=None, symbol=BOT_SYMBOL)
            continue

        result = evaluate_kc_pending_entry(sub, float(last['close']),
                                           code=None, symbol=BOT_SYMBOL)
        state  = result.get('reason', result.get('action'))
        action = result.get('action')
        t = ts2t(last['timestamp'])
        bid = int(last['timestamp'])

        # track pending
        if 'KC_BREAKOUT_SHORT_PENDING' in str(state):
            kc_lo = last.get('kc_lower', float('nan'))
            pending_info = {
                'signal_id': result.get('pending_signal_id'),
                'b1_ts': result.get('breakout_bar_id'),
                'b2_ts': result.get('pair_confirmation_bar_id'),
                'wait_bars': result.get('pending_wait_bars'),
                'kc_lo': kc_lo,
            }

        # track invalidations
        if state == 'KC_PENDING_INVALIDATED':
            kc_lo = last.get('kc_lower', float('nan'))
            ma5   = last.get('ma5', float('nan'))
            body  = float(last['close']) - float(last['open'])
            sid   = result.get('pending_signal_id', pending_info.get('signal_id') if pending_info else None)
            comp  = _make_signal_identity(BOT_SYMBOL, 'SHORT', sid or '', bid) if sid else None
            invalidated_log.append({
                'bar_time': t, 'bar_id': bid,
                'open': float(last['open']), 'close': float(last['close']),
                'body': body, 'kc_lo': kc_lo, 'signal_id': sid, 'composite': comp
            })

        if state != prev_state:
            kc_lo   = last.get('kc_lower', float('nan'))
            ma5     = last.get('ma5', float('nan'))
            ma15    = last.get('ma15', float('nan'))
            ma5_sl  = last.get('ma5_slope', float('nan'))
            body    = float(last['close']) - float(last['open'])
            below = '↓KC' if float(last['close']) < kc_lo else '   '
            red   = '🔴' if body < 0 else '🟢'
            print(f"  {t} {red} {below} bid={bid}  [{action}]  {state}")

            if action == 'ENTER' and result.get('side') == 'SHORT':
                first_enter = result
                b1t = ts2t(result.get('breakout_bar_id', 0))
                b2t = ts2t(result.get('pair_confirmation_bar_id', 0))
                b3t = ts2t(result.get('confirmation_bar_id', 0))
                sid = result.get('pending_signal_id')
                comp = _make_signal_identity(BOT_SYMBOL, 'SHORT', sid or '', int(result.get('confirmation_bar_id', 0)))
                dist = result.get('kc_distance_atr', float('nan'))

                print()
                print("  " + "─"*106)
                print(f"  ✅ PART 2: SHORT ENTRY SIGNAL GENERATED")
                print(f"  ─ Bar1 breakout  : {b1t}")
                print(f"  ─ Bar2 confirm   : {b2t}")
                print(f"  ─ Bar3 execution : {b3t}  close={result.get('close_price'):.6f}")
                print(f"  ─ kc_lower       : {kc_lo:.6f}")
                print(f"  ─ MA5={ma5:.6f}  MA15={ma15:.6f}  MA5_slope={ma5_sl:+.6f}")
                print(f"  ─ distance_atr   : {dist:.4f}  (limit={MAX_DISTANCE_ATR})")
                print()
                print(f"  PART 3 — Finality:")
                print(f"  ─ signal_id        : {sid}")
                print(f"  ─ candidate_bar_id : {int(result.get('confirmation_bar_id', 0))}")
                print(f"  ─ composite_key    : {comp}")
                print(f"  ─ in _INVALIDATED  : {comp in _INVALIDATED_SIGNALS}")
                print()

                print("  PART 2 — Bar-by-bar checklist:")
                # get bar data
                b1_row = full_closed[full_closed['timestamp'] == result.get('breakout_bar_id')].iloc[0] if len(full_closed[full_closed['timestamp'] == result.get('breakout_bar_id')]) else None
                b2_row = full_closed[full_closed['timestamp'] == result.get('pair_confirmation_bar_id')].iloc[0] if len(full_closed[full_closed['timestamp'] == result.get('pair_confirmation_bar_id')]) else None
                b3_row = full_closed[full_closed['timestamp'] == result.get('confirmation_bar_id')].iloc[0] if len(full_closed[full_closed['timestamp'] == result.get('confirmation_bar_id')]) else None

                for label, row_data in [('Bar1', b1_row), ('Bar2', b2_row), ('Bar3', b3_row)]:
                    if row_data is not None:
                        klo = row_data.get('kc_lower', float('nan'))
                        is_red = row_data['close'] < row_data['open']
                        below_kc = row_data['close'] < klo
                        print(f"  {label}: open={row_data['open']:.6f}  close={row_data['close']:.6f}  "
                              f"KC_lower={klo:.6f}")
                        print(f"       red={is_red}  close<KC_lower={below_kc}")
                print("  " + "─"*106)
                print()

                print("  PART 4 — Tracing to ACCOUNT_SUBMIT:")
                print("  evaluate_kc_pending_entry → ENTER ✅")
                print("  → evaluate_entry_contract → would be called next by scanner")
                print("  → _fresh_channel_entry_snapshot → re-validates with finality check")
                print("  → entry_firewall.validate_account_entry:")
                print(f"    snapshot.symbol     = {BOT_SYMBOL}")
                print(f"    snapshot.side       = SHORT")
                print(f"    snapshot.signal_id  = {sid}")
                print(f"    snapshot.closed_bar = {int(result.get('confirmation_bar_id', 0))}")
                print(f"    composite identity  = {comp}")
                print(f"    invalidated_signals = {comp in _INVALIDATED_SIGNALS}")
                print()

            prev_state = state

    # ── Summary of all INVALIDATED events ─────────────────────────────
    print("\n" + "═"*110)
    print("  PART 3 — All KC_PENDING_INVALIDATED events in window")
    print("═"*110)
    if invalidated_log:
        for ev in invalidated_log:
            print(f"  {ev['bar_time']}  open={ev['open']:.6f}  close={ev['close']:.6f}  "
                  f"body={ev['body']:+.6f}  KC_lower={ev['kc_lo']:.6f}")
            print(f"         signal_id   = {ev['signal_id']}")
            print(f"         composite   = {ev['composite']}")
            is_red = ev['close'] < ev['open']
            reason = 'close > open (green bar = invalid Bar3 for SHORT)' if not is_red else 'unknown'
            print(f"         reason      = {reason}")
            print()
    else:
        print("  None in window")

    if first_enter is None:
        print(f"\n  ⚠️ No SHORT ENTER generated in window.")
        print(f"  Last gate state: {prev_state}")

    print("═"*110)

asyncio.run(main())
