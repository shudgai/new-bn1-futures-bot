# Binance Demo runtime mode

### [2026-11-18 06:10:26 UTC+8] - Modification Phase: Stop defaulting demo operators to paper fills
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: `.env.example`; `tests/test_status_api.py`
- **Modification Description**: Set the example profile to `PAPER_TRADING=false` and `USE_DEMO_TRADING=true`, retaining enabled order submission. Added a regression check for the example profile. The local ignored `.env` was also changed to the same mode and is not committed.
- **Trigger Reason & Requirement**: User reported that no positions appeared on the exchange. Order audit records showed `PAPER_FILLED`, and the runtime configuration had paper trading enabled despite demo trading also being configured.
- **Verification & Test Status**: `tests/test_status_api.py -k 'effective_runtime_environment or example_environment'`: 5 passed. Config smoke check and `git diff --check` passed. Broader run of `tests/test_status_api.py tests/test_startup_guard.py`: 23 passed, 4 failed (one existing status response type assertion; two socket cases blocked by sandbox permissions; the new profile assertion initially failed due test key normalization and passed after correction). Service restart was attempted but host authentication is required.
