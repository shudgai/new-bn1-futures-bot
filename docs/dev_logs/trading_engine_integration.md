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
