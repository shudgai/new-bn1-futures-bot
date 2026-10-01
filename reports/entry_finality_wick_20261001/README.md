# Entry settlement, wick and classification repair

User-approved: reject a second LONG confirmation candle whose upper wick is >= its body; SHORT uses lower wick symmetrically. Same rule applies to the original pair used by continuation. First candle 0.5 prior closed ATR and second same-color closed body remain required.

Order-boundary snapshots now use exchange server time, a minimum 3-second settlement margin after candle end, an actual following candle, and two independent REST reads separated by one second. All closed OHLC rows must agree; revisions, stale tails or changed confirmation bars block and retry on later evaluation. The account firewall requires this verified provider, performs its own fresh evaluation and stores the last validated candle evidence and classification. This is conservative REST settlement verification, not a guarantee against arbitrary later exchange corrections; it does not claim a WebSocket final-close flag was received.

New breakout classification remains INITIAL_BREAKOUT even with older close history. POST_EXIT_CONTINUATION applies only to an actual continuation. Idle state-machine compatibility delegates to the shared contract; its existing held-position exit branches are preserved. Manual entry and takeover are unchanged.

Chart refresh now includes historical OHLC and indicator values in its change signature. Historical revisions trigger redraw even when the current minute has not advanced; live-only updates remain incremental. This addresses a chart display mechanism capable of showing stale candle colors, without asserting it alone caused every screenshot discrepancy.

80 isolated entry regressions passed; Python compilation passed. Coverage includes red-to-green revisions between reads, stale/no-following candles, exchange time guards, exact wick boundaries, same-code strategy/firewall behavior, both-symbol post-close execution, and account risk checks. No real exchange orders were sent by tests. Historical unrelated failures and missing specification files remain documented in earlier reports; no full-suite pass is claimed.

Deployment: user service restarted at 2026-10-01 07:52:55 UTC (15:52:55 UTC+8), MainPID 568759, active/running, NRestarts=0. Status API recovered after startup retry and returned is_running=true, paper_trading=true. Initial observation has no claimed natural-market fill under this revision.

## Follow-up: settlement completion time

The second REST read could finish after the permitted candle window while verification still used exchange time sampled before that read. Two isolated regressions reproduced acceptance of a stale frame and recording the old verification time. The provider now samples exchange time again after the second read, rejects backward time or expired settlement windows, and records the completed verification timestamp. Entry predicates and exit policies are unchanged.

Validation: tests/test_strict_entry_contract.py: 103 passed, including both new regressions that failed before the fix. Python compilation passed. The changed source and test are currently untracked, so scoped git diff --check alone does not inspect their contents. No service restart, commit or push was performed for this follow-up; existing deployment evidence above describes the previous revision. Missing AIDAN specifications and historical test files remain unresolved.
