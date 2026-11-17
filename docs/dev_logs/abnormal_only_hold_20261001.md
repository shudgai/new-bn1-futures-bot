# Abnormal-only holding policy

The user requested continued holding until an abnormal adverse candle. The screenshot's PEPE close was verified as CLOSED_BELOW_MA5 at 16:54:02 UTC+8. Removed independent MA/KC/doji/peak-profit exit authorities and routed held scans through the shared persistent tick evaluator. The stated default is an adverse original-open-to-quote body >= 1.2 prior closed 1M ATR, symmetrically LONG/SHORT. Retained initial/account hard stops and manual operations; entry filters remain unchanged.

258 relevant tests passed. Python and inline JavaScript syntax and scoped diff checks passed. Five legacy close-suite fixture failures and missing AIDAN/historical test files are disclosed separately. See reports/abnormal_only_hold_20261001/README.md, incident.json, tests.txt and deployment.json for details.

### [2026-11-17 22:35:00 UTC+8] - Modification Phase: Consecutive abnormal exit and confirmed pivot close
- **Author**: shudgai999 / Copilot
- **Target Files & Lines**: `core/services/exits/priority_exit_manager.py` (`evaluate_priority_exit`); `core/services/execution_manager.py` (`_attempt_priority_exit_reversal`); `tests/test_priority_exit_manager.py`
- **Modification Description**: Require two adjacent adverse abnormal candles before the P0 circuit-breaker close; a non-abnormal next candle clears the pending abnormal state. A confirmed true peak/valley authorizes a close independently of macro-trend alignment. If the same observation also confirms the requested MA5 reversal, the controlled reversal path is evaluated first and retains normal entry and account-risk gates.
- **Trigger Reason & Requirement**: User requested delayed, consecutive abnormal-candle confirmation, true-pivot exits, and gated MA5 reversal handling for both sides.
- **Verification & Test Status**: Focused exit/pivot regression selection: 32 passed; `git diff --check` passed. A broader entry-and-exit selection reported 297 passed and 22 failures, all in `tests/test_strict_entry_contract.py`; it was not compared with a clean baseline. Runtime is paper-trading; no live order was placed.
