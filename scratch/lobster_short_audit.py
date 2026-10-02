"""
Entry Audit — 龙虾/USDT  2026-10-02 ~08:40–09:10 UTC

Usage:
    cd "/home/shudgai999/project/new bn"
    .venv/bin/python3 scratch/lobster_short_audit.py
"""
import asyncio, os, sys, datetime, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BOT_SYMBOL   = '龙虾/USDT'          # as bot uses it
CCXT_SYMBOL  = '龙虾/USDT:USDT'     # ccxt perp format
WINDOW_START_MS = 1759459200000     # 2026-10-02 08:40:00 UTC

async def main():
    from dotenv import load_dotenv
    load_dotenv()

    import ccxt.async_support as ccxt
    exchange = ccxt.binanceusdm({'options': {'defaultType': 'future'}})
    try:
        await exchange.load_markets()
        # fetch_ohlcv needs the ccxt perp symbol
        raw = await exchange.fetch_ohlcv(CCXT_SYMBOL, '1m', limit=200)
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

    # ── compute MA5 slope ───────────────────────────────────────────────
    if 'ma5' in df.columns:
        df['ma5_slope'] = df['ma5'].diff()

    # ── filter window ───────────────────────────────────────────────────
    win = df[df['timestamp'] >= WINDOW_START_MS - 600_000].copy()

    print("\n══════════════════════════════════════════════════════════════════════════════")
    print(f"  Entry Audit  {BOT_SYMBOL}  ~08:40–09:10 UTC 2026-10-02")
    print("══════════════════════════════════════════════════════════════════════════════")
    print(f"{'Time':>5} {'D':>2} {'↓KC':>4} | {'open':>10} {'close':>10} | "
          f"{'KC_lo':>10} {'MA5':>10} {'MA15':>10} {'MA5_sl':>9} {'ATR':>8}")

    for _, row in win.iterrows():
        t = datetime.datetime.utcfromtimestamp(int(row['timestamp'])//1000).strftime('%H:%M')
        red = row['close'] < row['open']
        body_dir = '🔴' if red else '🟢'
        kc_lo = row.get('kc_lower', float('nan'))
        below = '↓KC' if row['close'] < kc_lo else '   '
        ma5 = row.get('ma5', float('nan'))
        ma15 = row.get('ma15', float('nan'))
        ma5_sl = row.get('ma5_slope', float('nan'))
        atr  = row.get('atr', float('nan'))
        print(f"{t}  {body_dir} {below:>4} | {row['open']:>10.6f} {row['close']:>10.6f} | "
              f"{kc_lo:>10.6f} {ma5:>10.6f} {ma15:>10.6f} {ma5_sl:>+9.6f} {atr:>8.6f}")

    # ── bar-by-bar rolling scan ─────────────────────────────────────────
    from core.services.kc_pending_entry import evaluate_kc_pending_entry, _INVALIDATED_SIGNALS
    _INVALIDATED_SIGNALS.clear()

    full_closed = df[df['is_closed']].copy()
    print("\n──────────────────────────────────────────────────────────────────────────────")
    print("  Bar-by-bar gate scan")
    print("──────────────────────────────────────────────────────────────────────────────")

    prev_state = None
    first_enter = None
    for i in range(2, len(full_closed)):
        sub  = full_closed.iloc[:i+1].copy()
        last = sub.iloc[-1]
        if last['timestamp'] < WINDOW_START_MS - 600_000:
            continue
        t = datetime.datetime.utcfromtimestamp(int(last['timestamp'])//1000).strftime('%H:%M')

        result = evaluate_kc_pending_entry(sub, float(last['close']),
                                           code=None, symbol=BOT_SYMBOL)
        state  = result.get('reason', result.get('action'))
        action = result.get('action')

        if state != prev_state:
            kc_lo  = last.get('kc_lower', float('nan'))
            ma5    = last.get('ma5', float('nan'))
            ma15   = last.get('ma15', float('nan'))
            ma5_sl = last.get('ma5_slope', float('nan'))
            body_dir = '🔴' if last['close'] < last['open'] else '🟢'
            below = '↓KC' if last['close'] < kc_lo else '   '
            print(f"  {t} {body_dir} {below} → [{action}]  {state}")
            if action == 'ENTER':
                first_enter = result
                b1_ts = datetime.datetime.utcfromtimestamp(int(result.get('breakout_bar_id',0))//1000).strftime('%H:%M')
                b2_ts = datetime.datetime.utcfromtimestamp(int(result.get('pair_confirmation_bar_id',0))//1000).strftime('%H:%M')
                b3_ts = datetime.datetime.utcfromtimestamp(int(result.get('confirmation_bar_id',0))//1000).strftime('%H:%M')
                print(f"         ✅ SHORT ENTRY APPROVED")
                print(f"         Bar1 (breakout)  = {b1_ts}")
                print(f"         Bar2 (confirm)   = {b2_ts}")
                print(f"         Bar3 (execution) = {b3_ts}")
                print(f"         close_price      = {result.get('close_price'):.6f}")
                print(f"         kc_lower         = {kc_lo:.6f}")
                print(f"         MA5              = {ma5:.6f}  MA15={ma15:.6f}  slope={ma5_sl:+.6f}")
                print(f"         signal_id        = {result.get('pending_signal_id')}")
                print()
            prev_state = state

    if first_enter is None:
        print("\n  ⚠️  No ENTER generated in window — last gate reason was:", prev_state)

    print("══════════════════════════════════════════════════════════════════════════════\n")

asyncio.run(main())
