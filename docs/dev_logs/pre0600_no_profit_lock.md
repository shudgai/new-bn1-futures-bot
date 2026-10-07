# Pre-06:00 Taipei exits without profit locks — 2026-10-07

The latest instruction explicitly disables profit locking and restores the exit
style used before 2026-10-07 06:00 Asia/Taipei (2026-10-06 22:00 UTC).
Keep the independently restored two-breakout entry contract.

## Baseline evidence and scope

Before the cutoff, recorded completed trades use structural pivot exits,
waterfalls and initial ATR hard stops. Examples include lobster structure exit
at 04:51:05 and waterfall exit at 05:35:31, and CAP waterfall exit at 05:50:19.
Pre-cutoff development records describe confirmed structure v2 with a closed
pivot break and 0.1 entry-ATR buffer, and the v33 post-entry reversal exit with
at least 0.5 entry ATR of prior favorable excursion. The committed reversal
helper at 8f256d9 enforces a real outside pivot and a live half-ATR body.

The old version also contained profit protection. The user's explicit no-lock
instruction supersedes that portion. No byte-identical deployment archive is
available; this is a scoped rule restoration using the saved trade records,
development records and existing historical reversal helper, not a whole-tree
Git rollback. Unrelated working changes and completed history are preserved.

## Result

- Channel Swing has no percentage, fixed-USDT, ATR or moving-pivot profit lock.
  A peak and giveback alone do not close the position.
- Confirmed structural pivot break plus a current strict break beyond the
  0.1 fixed entry-ATR buffer can close, without new opposite-entry qualification.
- Restore confirmed post-entry opposite pivot reversal after at least 0.5 entry
  ATR of previously observed favorable movement; no standalone MA5-turn exit.
- Preserve current valid initial stop, account hard limits, and 1.5 previous
  closed-ATR adverse live-body waterfall. Do not resize or reset positions.
- Retire soft pending tickets and profit lines in both position and metadata,
  including today’s ATR2/20-percent tickets and standalone MA5 exit tickets.
  New-policy verified structure/reversal and hard-risk retries persist.
- Account close boundary rejects late obsolete profit/MA5/rail close requests.
- Structural close executes inline without REST entry checks. Background quote
  followups still avoid blocking protective quote processing.
- Profit display reports disabled. Testnet sync cancels only identified old
  profit algo orders and queries uncertain client IDs without posting replacements;
  failed cancellation retains IDs for retry. Active runtime remains paper trading.

## Verification

219 focused tests passed; compilation and diff checks passed. Tests cover both
directions, giveback holding, closed structure and buffer requirements, normal
MA5 retirement, post-entry reversal and retry, actual paper structural closing
without entry data, initial and waterfall risk, both-store migration, late-close
rejection, identified profit-order cancellation, restored entries, fresh account
firewall, durable direct netting, finality and evidence signatures. The lobster
13:32 historical price inputs now hold at the old 20-percent giveback when no
other independent exit condition is supplied; this is a rule replay, not a claim
of future profitability or an alternate historical fill.
