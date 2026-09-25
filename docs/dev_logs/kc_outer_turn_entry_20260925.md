# KC opposite-rail turn entry — 2026-09-25

User explicitly selected option 2: SHORT above the upper rail after turning down,
LONG below the lower rail after turning up. The previously stated trend filter is
retained: the latest two closed midlines rise for LONG and fall for SHORT.

`outer_turn_entry.py` owns the shared observation contract. Three actual sampled
prices must show adverse movement followed by recovery, with prices still strictly
beyond the opposite entry rail. Equal prices do not create a turn. OHLC wicks are
never used to reconstruct quote order. Bar/direction changes, invalid data, leaving
the entry zone, restart, an existing position, or a successful fill discard the
relevant observation. Five seconds without evaluation resets observation (aligned
with the existing quote freshness window). Renewed adverse movement revokes a
ready turn and starts another observation. No ATR turn amplitude was introduced.

Unified scanning and compatibility entry delegate to this contract. Engine-owned
observations are shared with fresh-snapshot and final quote revalidation. Old
momentum/breakout/track codes cannot authorize structured orders. Relay cannot
force an entry rejected by the shared strategy. Temporary snapshot failure for a
new outer-turn signal does not create a new whole-bar invalidation lock; existing
locks are preserved. Existing account, quote, abnormal-market, profit-room and
cooldown checks remain in the execution path. A position cannot pyramid through
the retired breakout rule. Manual order handling and staged risk exits are unchanged.
The displayed strategy description now reflects this entry and current exits.

Validation: `tests/test_outer_turn_entry.py`, `tests/test_kc_mid_turn_exit.py`, and
`tests/test_staged_testnet_integration.py`: 72 passed. Includes real runner dispatch
for both sides and production quote-gate rail/freshness checks. Syntax compilation
and git diff whitespace validation pass. Existing tests were not rewritten to
pretend old breakout entries remain valid under the new policy.

Limitations: required AIDAN common/Python instructions and the three specified
Channel Swing regression files remain absent. The two prior staged adapter failures
are documented in `kc_mid_turn_exit_20260925.md`; this is not a full-suite pass.
Tests use offline data and mocks, not exchange execution. Reuse the user's earlier
8006 restart authorization to load this change; no commit or push requested here.

## Follow-up: closed MA alignment and measured recovery

User delegated the choice of direction/turn confirmation. The selected policy adds
latest closed MA3 > MA15 for LONG, MA3 < MA15 for SHORT, while retaining the closed
KC midline trend and opposite-rail location. A new crossover on the latest closed
bar is not required. Missing, equal, opposite, nonpositive, or nonfinite MA values
block entry and reset that side's observation. Live MA values are not consulted.

A turn now needs recovery of at least 0.10 times the latest closed ATR captured at
observation start. This is an implementation default, not a performance-validated
optimum. The threshold stays fixed within that observation. Adverse movement after
a ready turn revokes it and starts a new adverse leg with its own recovery. Long
and short use symmetric signed extrema, observed only from actual sampled prices.
Existing scan and final quote validation share the new guard. Exit policy remains
unchanged. The strategy description is updated to match.

Validation: 91 passed across outer-turn entry, KC exit, and offline staged testnet
integration suites. Added low-price scaling, threshold boundary, fixed ATR, new-leg
recovery, closed-vs-live MA, invalid MA, and production MA revalidation cases.
Existing outer-turn fixture now supplies the newly mandatory MA data; its original
behavior assertions remain. Syntax and whitespace checks pass. Previous missing
suites and unrelated staged-adapter failures remain as documented above.
