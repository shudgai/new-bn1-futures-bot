# Doji followed by adverse pressure

The active DOJI_REVERSAL_EXIT previously accepted any counter-direction tick below/above the prior low/high or MA5 after a 15% doji. It did not require the previously authorized 0.5 ATR adverse body. This defect is verified in code; the specific user-observed trade remains unidentified, so no claim is made that its historical close was caused by this rule.

The corrected rule uses an immediately preceding completed candle with body/range <=25%, followed by the current candle whose adverse body from its original open reaches >=0.5 times the preceding completed ATR. The current candle must have body/range >25%; another doji does not trigger. Both boundaries use floating-point tolerance. No wait for the current close, no historical wick-order reconstruction, no MA-touch shortcut. The existing favorable excursion >=2 entry ATR and profitable prior close requirements remain. The doji must begin after entry; stale/missing/nonadjacent/invalid OHLC cannot authorize this exit.

Confirmed pressure bypasses the lagging trend-hold soft-exit veto in both the evaluator and execution adapter. Waterfall and initial/account hard stops retain priority. Other independent exit rules remain unchanged and can still close a position for their own reasons.

Old DOJI_REVERSAL_EXIT pending states without rule version 2 are revoked; peaks and unrelated pending exits survive. Newly valid doji triggers persist with original/next candle IDs, original open, trigger quote, closed ATR and both body ratios for retry. The realtime adapter stores DOJI_REVERSAL_EXIT in the close reason to distinguish it from waterfall exits. No live account state is manually rewritten.

Validation: 100 passed across test_doji_following_pressure_exit.py (44), test_kc_live_third_confirmation.py (28), and test_doji_reversal_exit.py (28). Isolated tests cover both sides, exact thresholds, zero-body/weak continuation, invalid sequence, hard-stop/waterfall priority, migration, cached-tick execution under HOLD, persistence and retry without REST. No real test orders.

Separate existing regression baseline: 61 passed / 11 failed before and after, identical failing test identities, no new failures. Full output is retained in regression_before.txt and regression_after.txt. No full-suite pass is claimed. Syntax compilation and git diff --check passed. AIDAN specification files remain absent; other working-tree edits were preserved.
