# Trend Continuation Hold Lock

## Current dual-trend override (2026-11-14)

For automated ordinary exits, a position may close only after both the confirmed
post-entry KC middle-line direction and the independently supplied higher-timeframe
macro trend have reversed against the position. Missing, stale, or invalid evidence
fails closed. Verified emergency exits remain subject to their existing evidence
checks.

After a successful full close under this dual-trend condition, the opposite side is
evaluated through the normal entry and account-safety gates. If those gates reject
the entry, a persisted reversal ticket is retried on each fresh market snapshot only
while both reversals remain confirmed. The ticket is cleared when either direction
reverts or an opposite position is opened. No strategy-entry gate is bypassed.

The policy below records the earlier behavior and is superseded where it conflicts
with this override.

## Historical policy

Every non-hard Channel Swing exit is vetoed if any observed directional candle, non-adverse MA5 slope, or same-side MA5/KC rail support remains. LONG and SHORT are symmetric. Realtime CK correction and automatic breakout reversal apply the same arbitration. Explicit manual closes remain available.

Profit locks additionally require an unleveraged historical price peak of at least 5%, at least 1.2 percentage points of giveback, and price beyond MA5 in the adverse direction with an adverse MA5 slope. Existing wider tier allowances remain. Pending closes are revalidated and cancelled if conditions recover; missing MA confirmation cannot authorize profit-taking.

This user override also blocks non-hard emergency exits while any continuation condition holds. Hard-stop handling remains independent, using existing enabled policies.
