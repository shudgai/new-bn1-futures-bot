# Live third-candle entry

Changed the shared entry contract from three closed candles to two closed directional candles plus a live third candle of either color. The user subsequently removed the live-color requirement, symmetrically for LONG and SHORT. A live candle is required. Confirmation timestamps retain their closed-second-candle meaning for scanner/account compatibility; breakout timestamp identifies the first candle.

Doji body/range below 10% is blocked on the two confirmation candles and live third. The existing 0.10 ATR chase cap, same-close-bar guard, account revalidation and holding exits remain. No extra time limit was added. Earlier unrelated candles no longer count toward the three-candle doji guard.

84 targeted tests passed, including actual two-closed-plus-live inputs, opposite-color latest quotes accepted at account revalidation, flat live doji rejection, long wicks, doji boundaries and chase guards. Syntax and git diff --check passed. Tests use mocked exchange calls; no real test orders. This is not a full-repository test claim.
