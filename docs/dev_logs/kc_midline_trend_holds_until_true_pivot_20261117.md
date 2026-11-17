# KC midline trend holds to confirmed pivot — 2026-11-17

Request: while KC midline trends with the position, do not close on a touch or breach of the opposite outer rail; wait for a confirmed true peak/valley. Keep waterfall and market circuit-breaker exits.

Changes:
- Detect aligned trend from the latest two explicitly closed KC midline values.
- In an aligned LONG/SHORT trend, veto ordinary exits including opposite KC outer-rail breaches until the post-entry confirmed true peak/valley is present.
- Keep the market hard-stop/circuit-breaker priority and route waterfall candidates through the existing independent body-and-midline verification.
- Added mirrored tests for aligned and non-aligned midline directions, true pivots, and emergency exits.

Verification: 7 targeted midline/pivot authorization tests and 6 targeted waterfall/circuit-breaker tests passed; Python compilation and `git diff --check` passed. A broader legacy exit suite had 18 passes and 2 failures in fixtures without a valid open timestamp / candle snapshot; those cases do not validate this new gate.
