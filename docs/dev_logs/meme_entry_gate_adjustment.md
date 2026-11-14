# Meme Entry Gate Adjustments

### [2026-11-14 08:40:00 UTC+8] - Modification Phase: Apply meme-specific entry veto limits
- **Author**: shudgai999 / Copilot
- **Target Files & Lines**: `core/services/market_consolidation_filter.py` (`evaluate_market_blacklist`); `core/gates/central_entry_gate.py` (`verify_and_authorize`); related entry-gate tests.
- **Modification Description**: Meme-symbol candidates no longer receive the generic low-ADX/low-ATR Donchian range veto; the decision is recorded as `meme_donchian_low_trend_override`. The central MA15 bias ceiling is 1.5 ATR for meme symbols and remains 1.2 ATR for other symbols. Other filters remain active.
- **Trigger Reason & Requirement**: User clarified that the strategy trades meme coins and authorized the Donchian exception for all meme signals and a 1.5 ATR MA15 ceiling.
- **Verification & Test Status**: Targeted consolidation, low-latency, and MA15 boundary regressions — 63 passed. `git diff --check` passed. Broader central/funnel integration runs were not fully green: 22 failures in central/funnel tests and 6 failures in the additional integration run; failures include existing fixture/expectation mismatches and an unrelated API `NameError`, and are not reported as passing. No deployment, restart, commit, or exchange order was performed.
