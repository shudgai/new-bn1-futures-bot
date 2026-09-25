# Closed breakout and outside-rail pullback — 2026-09-25

Author: shudgai999 / Codex. User explicitly retained other valid entries and
required two closed same-color bodies for the breakout route. The follow-up
explicitly permits continuation after a missed entry or successful close when
price remains beyond the original breakout rail and starts an adverse pullback.
No favorable turn or rail-proximity threshold is required for this continuation.

## Behavior

- First closed candle opens inside/on its own outer rail and its directional body
  closes strictly outside. The second closed candle has the same color and closes
  strictly outside its own rail. Each body is at least 20% of its full range.
- Live price must remain strictly outside the current same-side rail. Closed KC
  direction and MA3/MA15 alignment remain required. Live candle color is not a gate.
- Later candles may continue the confirmed breakout while their closed prices
  remain outside. The confirmation pair must be verifiable in available closed
  history; no synthetic or future confirmation is used. Gaps invalidate the chain.
- Continuation uses actual successive quotes: falling for LONG, rising for SHORT.
  Equal quotes preserve a just-observed pullback for revalidation, but cannot
  create one. Favorable movement revokes it. Bar/close identity changes, holding
  a position, fills, restart, stale quotes, or leaving the rail reset observation.
  Returning outside requires a newly observed pullback; no permanent candidate
  invalidation lock is added. A closed return inside breaks the historical chain.
- The existing opposite-rail observed-turn entry retains its own conditions.
  Scans, compatibility entry, fresh snapshots, normal reentry dispatch and final
  submission share the new entry evaluation. Old retired codes stay unsupported.
- Reentry tickets must match an actual close fill. Reentry starts after the actual
  fill candle, including delayed fills. Abnormal tickets retain their dedicated
  pullback gate. Existing account limits, per-candle limits, adverse-market guards
  and profit-room checks remain. Exit decisions are unchanged.

## Validation

154 passed across test_closed_breakout_entry.py, test_breakout_pullback_entry.py,
test_outer_turn_entry.py, test_kc_mid_turn_exit.py and
test_staged_testnet_integration.py. Tests cover both sides, low prices, unfinished
candles, bodies/wicks, gap opens, rail touches, quote freshness, actual scanner and
snapshot dispatch, normal reentry dispatch, delayed close fills, and abnormal
pullback gating. JUnit: reports/red_eye_production/closed_breakout_regression.xml.

This is offline validation, not exchange acceptance. No service restart, deployment,
commit or push was performed. The earlier staged red-eye run had 36 passed /
28 failed (27 tracing-anchor failures and one legacy protection assertion);
those failures were not repaired or represented as passing here.

## Missing-file provenance audit — 2026-09-25

The three historical test files are intentionally absent from this branch:
`tests/test_channel_swing.py`, `tests/test_channel_position_path.py`, and
`tests/test_channel_swing_execution.py` were deleted by ancestor commit
`480d40c287a095ffb0d155863824a655fc691fbc` on 2026-09-18. Its message is
"V9.0 Test Suite Refactoring: Clean legacy tests and introduce V9 core logic tests".
That commit deleted 76 test files and added `tests/test_v9_core_logic.py`, which
remains tracked. This is evidence of deliberate cleanup, not an uncommitted
loss or an inferred merge-conflict omission. The old files were not restored.
The commit does not prove equivalent coverage in the replacement suite.
AGENTS.md still references the deleted test paths; those references are stale,
and the three historical suites were not executed in the validation above.

AIDAN is not entirely absent: `AIDAN/common/high_beta_risk_system.md` is tracked.
The common/Python files referenced by AGENTS.md are absent, and searches across
all locally available refs found no history for those paths. The repository is
not shallow and has no tracked submodule configuration for these specifications.
No local evidence establishes whether their omission was intentional or an
incomplete import. AGENTS.md identifies `HuangTingInternetStudio/MDs` as the
canonical upstream, but a matching imported revision has not been established.
No specifications were fabricated, restored from an unverified version, or edited.
This audit used local Git history; remote refs were not refreshed.

These two provenance findings are separate from the 28 observed test failures:
the failed tests were collected and executed, then failed at adapter trace-anchor
validation (27 cases) or the legacy protection assertion (one case). They did not
fail because of missing test modules or AIDAN imports. No trading code or test
assertions changed during this audit, and no test rerun was needed for this
 documentation-only clarification.
