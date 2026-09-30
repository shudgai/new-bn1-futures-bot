### [2026-09-17 06:48:40 UTC+8] - Modification Phase: Repair strategy integration and exit state persistence
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: `core/services/symbol_runner.py` entry dispatch and exit observation persistence; `core/services/exits/dual_track_exit_service.py` state keys; `core/testnet_account.py` refresh and close retry classification; `core/engine.py` shadow status contract; corresponding runner, persistence, status and close-deduplication tests.
- **Modification Description**: Pass the explicit direction and unpack the entry strategy tuple. Persist ratchet peaks and confirmed swing extrema before asynchronous operations, restore them through testnet refresh, and copy nested state to keep later peak changes detectable. Restore the pre-refactor BTC shadow status response. Recognize DualTrackExit as a strategy close for the existing 30-second failure cooldown while retaining immediate manual overrides. Run the lifecycle test with the available AnyIO backend instead of the missing pytest-asyncio plugin.
- **Trigger Reason & Requirement**: User authorized corrections to four reproduced review findings. Entry/exit formulas and risk parameters are unchanged. Concurrent strategy-interface edits were reverted by the other editor; integration follows the current explicit-side interface. Concurrent diagnostic additions and untracked scripts are preserved.
- **Verification & Test Status**: Isolated copies omit `.env` and account data; fake exchanges perform no network orders. Focused suite: 24 passed (before corrections: 12 failed, 12 passed). Required trading suites plus testnet/account lifecycle: 223 passed, 80 failed, 2 skipped. All 75 required-suite failures match the original review; the additional five account failures also reproduce on the unchanged pre-fix copy. Four existing FastAPI deprecation warnings remain in the focused suite. No service restart, deployment, commit or push performed.


### [2026-09-30 15:00:59 UTC+8] - Modification Phase: Entry gate consistency
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: core/engine.py structured entry; pure_trend_v2.py evaluation diagnostics; symbol_runner.py; services/api.py; tests/test_entry_gate_consistency.py
- **Modification Description**: Remove duplicate obsolete preflight; expose actual shared evaluation rejection, remove guessed MA15 diagnosis.
- **Trigger Reason & Requirement**: User requested correction after missed-entry audit. Preserve current strategy filters and exits.
- **Verification & Test Status**: 59 targeted tests passed; compile and diff checks passed. Legacy boundary fixture also fails unmodified HEAD; no database reseed for isolated in-memory tests.

### [2026-09-30 16:24:02 UTC+8] - Modification Phase: Missed breakout continuation
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: pure_trend_v2.py outside_continuation_side/evaluate_v2_frame; tests/test_missed_breakout_continuation.py; tests/test_entry_gate_consistency.py
- **Modification Description**: Allow adjacent directional outside closes to continue after missed initial entry, bypass consolidation and second-bar-only restriction for continuation. Preserve live rail, extreme break and all other gates.
- **Trigger Reason & Requirement**: Explicit user request for later continuation entries.
- **Verification & Test Status**: 89 targeted tests passed; isolated in-memory accounts, no reseed or external orders; diff check passed.

### [2026-09-30 16:46:38 UTC+8] - Modification Phase: First adverse body after doji
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: doji_reversal_exit.py; engine._instant_quote_exit; symbol_runner; dual_track_exit_service; test_doji_reversal_exit.py
- **Modification Description**: Confirmed doji <=25% followed by live adverse body >=0.5 prior closed ATR triggers durable full close. Fast cached-frame path, no REST or scan lock.
- **Trigger Reason & Requirement**: User explicitly approved thresholds and requested first-candle exit.
- **Verification & Test Status**: 117 targeted tests passed; compile and diff checks passed; isolated mocked account, no DB reseed.

### [2026-09-30 16:51:42 UTC+8] - Modification Phase: Advancing momentum exhaustion entry filter
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: pure_trend_v2.advancing_body_rejection; engine fresh snapshot and submit revalidation; entry momentum tests
- **Modification Description**: Enforce ignition body 50%, prior closed ATR 0.5, adverse wick 1.5x and LONG wick 40%; original episode anchor; precise rejection logs.
- **Trigger Reason & Requirement**: Explicit user entry filter request with restart authorization.
- **Verification & Test Status**: 132 targeted tests passed; compile and diff checks passed. Isolated accounts; no DB reseed required.

### [2026-09-30] - Modification Phase: Restore tick profit protection
- **Author**: shudgai999 / Codex
- **Problem**: The runner read quantity instead of qty; the dedicated trade callback only checked doji exits. PEPE paper fill records show 16:47:05 entry and 16:59:49 midpoint close (UTC+8).
- **Change**: Route profit protection and initial ATR stops through the cached-data tick service; positive-peak 25% drawdown, fixed 0.8 ATR retracement and observed outer-band live-MA3 reversal. Persist position-bound peaks and retries, reuse account close locking, and remove duplicate runner rules. Update the UI status.
- **Validation**: 163 relevant tests passed, including 21 new cases and isolated paper/fake-testnet reload and concurrent reduce-only market closing. Twenty additional legacy failures reproduced on untouched HEAD; required legacy suites and AIDAN specification files are absent. Syntax and diff checks passed.
