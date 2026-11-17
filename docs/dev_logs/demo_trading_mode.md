# Binance Demo runtime mode

### [2026-11-18 06:10:26 UTC+8] - Modification Phase: Stop defaulting demo operators to paper fills
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: `.env.example`; `tests/test_status_api.py`
- **Modification Description**: Set the example profile to `PAPER_TRADING=false` and `USE_DEMO_TRADING=true`, retaining enabled order submission. Added a regression check for the example profile. The local ignored `.env` was also changed to the same mode and is not committed.
- **Trigger Reason & Requirement**: User reported that no positions appeared on the exchange. Order audit records showed `PAPER_FILLED`, and the runtime configuration had paper trading enabled despite demo trading also being configured.
- **Verification & Test Status**: `tests/test_status_api.py -k 'effective_runtime_environment or example_environment'`: 5 passed. Config smoke check and `git diff --check` passed. Broader run of `tests/test_status_api.py tests/test_startup_guard.py`: 23 passed, 4 failed (one existing status response type assertion; two socket cases blocked by sandbox permissions; the new profile assertion initially failed due test key normalization and passed after correction). Service restart was attempted but host authentication is required.

### [2026-11-18 06:13:34 UTC+8] - Modification Phase: Repair focused verification failures
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: `tests/test_status_api.py` (`test_status_response_preserves_shadow_contract`)
- **Modification Description**: Updated the status endpoint test to parse and assert the returned `JSONResponse` body and headers, matching the endpoint's current response contract. The startup socket cases were unchanged; they pass when run outside the restricted socket sandbox.
- **Trigger Reason & Requirement**: User requested repair of the four failures reported by the prior focused run.
- **Verification & Test Status**: `tests/test_status_api.py tests/test_startup_guard.py`: 27 passed. `git diff --check` passed. Four FastAPI lifecycle deprecation warnings remain.

### [2026-11-18 06:22:35 UTC+8] - Modification Phase: Deploy and restart Binance Demo mode
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: `binance-8006.service`; `.env`; `/api/status`
- **Modification Description**: Restarted the active user service from this checkout after confirming the local `.env` selects Binance Demo mode. The service is active with its Uvicorn child running.
- **Trigger Reason & Requirement**: User explicitly requested deployment and restart after the paper-to-demo mode correction.
- **Verification & Test Status**: `/api/status` returned HTTP 200 with `is_running=true`, `environment=binance_demo_trading`, `paper_trading=false`, and `entry_submission_enabled=true`. No manual order was submitted.
