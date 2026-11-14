# Meme Entry Gate Adjustments

### [2026-11-14 08:40:00 UTC+8] - Modification Phase: Apply meme-specific entry veto limits
- **Author**: shudgai999 / Copilot
- **Target Files & Lines**: `core/services/market_consolidation_filter.py` (`evaluate_market_blacklist`); `core/gates/central_entry_gate.py` (`verify_and_authorize`); related entry-gate tests.
- **Modification Description**: Meme-symbol candidates no longer receive the generic low-ADX/low-ATR Donchian range veto; the decision is recorded as `meme_donchian_low_trend_override`. The central MA15 bias ceiling is 1.5 ATR for meme symbols and remains 1.2 ATR for other symbols. Other filters remain active.
- **Trigger Reason & Requirement**: User clarified that the strategy trades meme coins and authorized the Donchian exception for all meme signals and a 1.5 ATR MA15 ceiling.
- **Verification & Test Status**: Targeted consolidation, low-latency, and MA15 boundary regressions — 63 passed. `git diff --check` passed. Broader central/funnel integration runs were not fully green: 22 failures in central/funnel tests and 6 failures in the additional integration run; failures include existing fixture/expectation mismatches and an unrelated API `NameError`, and are not reported as passing. No deployment, restart, commit, or exchange order was performed.

### [2026-11-14 13:57:45 UTC+8] - Modification Phase: Allow confirmed meme continuation shorts
- **Author**: shudgai999 / Copilot
- **Target Files & Lines**: `core/services/entry_methods.py` (`MEME_SHORT_CONTINUATION_TRIGGERS`); `core/gates/master_gate_funnel.py` (MA15 extension checks); `core/gates/central_entry_gate.py` (confirmed continuation validation); related gate tests.
- **Modification Description**: For LOBSTER and PEPE shorts only, a closed-bar, latest-bar, identity-verified continuation trigger may pass the existing MA15 extension ceilings, with the funnel's continuation ceiling capped at 3.5 ATR. Other symbols, sides, and entry methods retain existing limits. The meme consolidation veto adjustment remains active.
- **Trigger Reason & Requirement**: User requested that LOBSTER and PEPE be able to enter during sustained bearish movement rather than being repeatedly blocked solely by generic MA15 extension filters.
- **Verification & Test Status**: Central-entry and consolidation regressions — 69 passed. The focused continuation cases — 20 passed. Full `test_meme_master_funnel.py` had 243 failures and 516 passes; the recorded baseline had 243 failures and 505 passes, and all 11 newly added cases passed. Python compilation and `git diff --check` passed. Channel-specific suites named in the repository guidance are not present in this checkout.
- **Deployment & Runtime Check**: Restarted user service `binance-8006.service`; it is active and `/api/status` reports the bot running in paper-trading mode. Existing 1000PEPE paper position remained present after restart. No live order was sent.
- **Runtime Finding**: The latest LOBSTER SHORT `POST_CLOSE_KC_REENTRY` candidate was rejected as `VETO_MA15_BIAS_EXTENSION` at 2.04 ATR versus the 1.5 ATR ceiling. The newer LOBSTER LONG re-entry candidate was also rejected at 2.32 ATR. These are not whitelisted `MEME_GATE_ENTRY` continuation triggers, so the continuation exception does not authorize them; no additional re-entry exemption was made.
