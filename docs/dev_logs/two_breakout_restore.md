# Restore two automatic breakout entries — 2026-10-07

The user requested returning to immediate rail breakout and ordinary confirmed
breakout, because layered MA and structural entry producers obscured behavior.
This supersedes the earlier pending question about relaxing middle-band direction
for reversal. Exit and account risk policies are retained.

## Verified historical facts

- CAP opened long at 13:26:33 Taipei through `KC_MA_CROSS_TREND_LONG`.
  The recorded live body was only 0.00005 against closed ATR 0.000268;
  it fails the restored immediate minimum body of 0.000134 and lacks the
  ordinary breakout pair. A saved historical snapshot now covers this rejection.
- The confirmed closed MA5 exit deployed at 13:36:11 and closed this CAP long
  at 13:36:14 via `EXIT_CONFIRMED_TREND_REVERSAL`. It was not active earlier.
- CAP entry diagnostics at 13:36 included same-bar post-exit blocking and live
  peak retracement blocking. At 13:37 the old chase cap also blocked attempts.
  These logs do not prove a valid historical immediate breakout at every quote.

## Current automatic entry authority

- Only `KC_LIVE_BODY_BREAKOUT_LONG/SHORT` and `KC_2BAR_CONFIRM_LONG/SHORT`
  are accepted by the shared scan, execution, reentry and account firewall.
- Immediate: original forming open inside or touching both KC boundaries,
  latest quote strictly outside the selected boundary, directional body at least
  0.5 of the previous closed ATR. No MA cross, middle-band direction, previous
  long-body veto, wick retracement gate, or chase cap is required.
- Ordinary: first completed directional body opens inside KC and closes outside;
  the next completed body is same-color and closes outside, both bodies at least
  20% of their high-low range; latest quote remains strictly outside. Existing
  ordinary CK/MA5 alignment and live MA3 direction remain.
- Ordinary CK direction uses the actual latest two closed candles, fixing the
  prior accidental extra-candle lag when the helper was given a closed-only frame.
- MA crossover, channel body, structural, pivot, pullback and naked outside
  continuation codes fail closed. Post-close reevaluation no longer returns early
  with a retired MA3 producer; abnormal reentry checks use the same shared contract.
- Normal close remains blocked for the closing candle; next candle may evaluate
  a valid ordinary pair or fresh immediate breakout without requiring both pair
  candles to postdate the close. Direct reversal still requires complete fresh
  opposite entry validation and verified netting settlement.
- Preserve quote finality, stale-data rejection, per-bar deduplication, abnormal
  reentry pullback and account exposure controls. Preserve entry-bound 2.0 ATR
  arming then 20% net-peak giveback, confirmed MA5 exit, waterfall and hard stops.
- Proof version advances to `entry-gate-20261007-v41-two-breakouts`, invalidating
  old cached automatic entry permissions. Website badge describes current rules.

## Validation

The primary regression suite passed 149 tests, covering both directions,
historical CAP rejection, strict inside-open/quote/body boundaries, ordinary
pair confirmation, retired codes, final-account revalidation, exits, hard risk,
netting reversal, uncertain and partial exchange fills, and evidence signatures.
Reversal fixtures now require genuine immediate breakouts instead of retired
MA-only trend entries; changed-condition denial retracts the quote inside KC.

Fresh REST and final account checks are still required before order submission;
immediate evaluation is not a guarantee of zero network latency or execution.

## Superseding exit instruction

The later explicit no-lock instruction replaces all profit locking and the standalone MA5 exit with the scoped pre-06:00 structural/reversal rules described in `pre0600_no_profit_lock.md`. The two entry families above remain active.
