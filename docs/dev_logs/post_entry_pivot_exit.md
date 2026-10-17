# Post-entry pivot exit correction

## Problem
SUI and lobster accepted a pivot on the entry candle even when its start preceded the actual fill. They also searched older candles and replayed that pivot after newer candles or restart. Recent paper closes included three-point-pivot exits around the existing 120-second minimum hold boundary. This establishes a stale-signal mechanism, not proof that every early close was erroneous.

## Change
All symbols now require the pivot candle to begin strictly after the actual position opening timestamp, and its confirmation candle to be the latest closed candle. Entry-candle extrema cannot establish their ordering relative to the fill from OHLC alone. Removed the symbol-specific historical replay exception. Existing minimum-hold modification, account hard stops, waterfall exits, direction correction, and retries of already authorized pending closes remain unchanged.

## Verification
83 passed: test_kc_outer_pivot_strategy.py, test_channel_exit_grace.py, test_realtime_profit_exit.py. Includes long/short SUI/lobster rejection before and after confirmation, later candles, restart-like recovery, and minimum-hold expiry. Previous tests expecting the retired entry-candle exception were updated to reflect the requested behavior.

WAIT candidate remains isolated and disabled. No manual trading orders were placed by this repair. Live market behavior requires subsequent fills for observation.

### [2026-10-17 13:59:43 UTC+8] - Modification Phase: Remove KC midline veto from confirmed pivot exits
- **Author**: Copilot (workspace identity: shudgai999)
- **Target Files & Lines**: `core/services/exits/strict_true_pivot.py` (`confirmed_true_pivot_exit`); `core/services/exits/realtime_profit_exit.py` (three-point-pivot exit path); `tests/test_true_peak_exit_without_midline.py`; `tests/test_wild_swing_strategy.py` (confirmed-pivot fixture)
- **Modification Description**: Confirm post-entry peaks/troughs from three contiguous completed candles. Exit on a strict local extreme plus either reversal body confirmation or MA5 reversal; remove the remaining KC-middleline veto from the legacy real-time pivot branch. Return the actual close result and log failed closes as retry-pending.
- **Trigger Reason & Requirement**: User requested an immediate close on a confirmed true peak without waiting for price to cross the KC middleline.
- **Verification & Test Status**: Focused pivot-exit suite: 35 passed, 4 unrelated entry assertions deselected; Python compilation and `git diff --check` passed. Broader mixed exit/entry regression: 20 failed, 85 passed, 2 deselected; failures include existing entry-route and legacy exit expectations, so deployment remains blocked pending triage.

### [2026-10-17 14:09:00 UTC+8] - Modification Phase: Persist confirmed pivot close retries
- **Author**: Copilot (workspace identity: shudgai999)
- **Target Files & Lines**: `core/services/exits/strict_true_pivot.py` (identity-bound pending-ticket helpers); `core/services/exits/realtime_profit_exit.py` (`enforce_realtime_profit_exit`); `core/services/strategies/wild_swing_strategy.py` (`evaluate_exit`); `core/services/symbol_runner.py` (`_process_wild_swing_symbol`); `tests/test_true_peak_exit_without_midline.py`; `tests/test_wild_swing_runner.py`
- **Modification Description**: Persist a validated, position-identity-bound true-pivot close ticket before submitting the close. The production Wild Swing runner restores the ticket from position metadata and retries it before evaluating new candle structure; the realtime helper shares the same ticket validation. Account hard-stop evaluation remains earlier in the realtime helper.
- **Trigger Reason & Requirement**: Continue validating true-pivot exits and prevent an already-confirmed pivot close from being lost after a failed close and a new candle or restart.
- **Verification & Test Status**: Focused helper/strategy/runner selection: 14 passed. Python compilation and `git diff --check` passed. The broader selected exit/runner/strategy regression reported 34 failed and 113 passed; failures include legacy exit expectations and entry/runner cases outside this change and are not proven baseline failures. WAIT/runtime gates remain blocked, so deployment remains blocked.
