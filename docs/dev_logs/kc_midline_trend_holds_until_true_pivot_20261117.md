# KC midline trend holds to confirmed pivot — 2026-11-17

Request: while KC midline trends with the position, do not close on a touch or breach of the opposite outer rail; wait for a confirmed true peak/valley. Keep waterfall and market circuit-breaker exits.

Changes:
- Detect aligned trend from the latest two explicitly closed KC midline values.
- In an aligned LONG/SHORT trend, veto ordinary exits including opposite KC outer-rail breaches until the post-entry confirmed true peak/valley is present.
- Confirmed true pivots remain higher priority than the consolidation hold. Otherwise, strict closed-bar consolidation vetoes ordinary HTF/KC structure exits; verified waterfall and circuit-breaker exits remain first.
- Remove the per-position ATR hard stop for Channel/Wild Swing. Retain the market emergency checks, including the verified waterfall/circuit-breaker path.
- Added mirrored tests for midline direction, consolidation holds, true pivots, and emergency exits.

Verification: 149 targeted exit, consolidation, and hard-stop tests passed. A broader account run had 130 passes, 4 skips, and 4 failures in unrelated testnet partial-close / missing-market-data cases.
