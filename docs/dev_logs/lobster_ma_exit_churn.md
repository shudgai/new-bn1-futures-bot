
### [2026-10-07 11:35:19 UTC+8] - Modification Phase: Retired MA exit authority
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: core/services/exits/peak_trailing_exit.py (migrate_peak_state, evaluate_peak_trailing); tests/test_lobster_ma_exit_churn.py
- **Modification Description**: Remove residual parabolic MA close branch and revoke matching retired MA pending tickets while preserving peak observations and hard stops.
- **Trigger Reason & Requirement**: User reported repeated lobster entries and closes. Historical local telemetry includes MA-turn close authorization at zero peak drawdown; screenshot-to-fill identity remains unverified.
- **Verification & Test Status**: 10 isolated regression tests passed; no production account data reseed or writes. Local account trade arrays empty; binance-8006.service MainPID=0. No deployment or restart performed.

### [2026-10-07 16:14:40 UTC+8] - Modification Phase: Align peak-exit regression assertions
- **Author**: Copilot
- **Target Files & Lines**: tests/test_peak_trailing_policy.py (dynamic pullback thresholds, Doji fixture, trend-hold precedence)
- **Modification Description**: Replace obsolete fixed-2U expectations with the authorized ATR pullback bands and supply complete live OHLC for Doji evidence; no trading logic changed for this test alignment.
- **Trigger Reason & Requirement**: User asked whether the 12 reported failures would be handled. Inspection showed these assertions expected retired fixed-ladder behavior rather than regressions in the current dynamic ATR policy.
- **Verification & Test Status**: `tests/test_peak_trailing_policy.py` and `tests/test_lobster_ma_exit_churn.py`: 47 passed. The broader adjacent set still has 17 failures in telemetry/fixed-lock tests asserting other retired policies; no deployment or restart performed.

### [2026-10-07] - Modification Phase: Retire Channel Swing initial ATR stop
- **Author**: Copilot
- **Owner Scope**: Disable only the Channel Swing strategy initial 1.5 ATR stop. Account margin/price loss limits, peak pullback, mature reversal, Doji, and Waterfall remain enabled. Other entry modes retain their initial stop behavior.
- **Target Files**: core/services/exits/peak_trailing_exit.py; core/services/exit_service.py; core/services/exits/entry_atr_protection.py; core/services/exits/dual_track_exit_service.py; core/services/exits/realtime_profit_exit.py; core/paper_account.py; core/testnet_account.py; core/engine.py.
- **Modification Description**: Submit Channel Swing with zero strategy SL; preserve entry ATR for the remaining exits; clear initial stop fields and matching retired ATR pending authority in held-position migration and persistence; prevent paper startup from restoring the retired stop from old fills. Testnet retains its existing exclusion of Channel Swing native protection orders. The shadow 1.5 ATR risk benchmark remains diagnostic only. No account loss thresholds or entry qualifications changed.
- **Regression Coverage**: Both sides and both symbols, opening/subsequent-minute stop crossings, metadata-only mode, serialized restart state, preservation of observations and valid Waterfall pending, account loss-limit retry, paper restart, and mocked testnet finalization without native stop creation. All account state paths are temporary; no real exchange calls.
- **Verification & Test Status**: Six-file exit regression set: 150 passed, 4 failed. The four adjacent failures are two testnet open-return assertions and two SHORT mature-reversal assertions; all four were reproduced in an isolated compatibility run restoring pre-change ATR stop behavior (4 failed, 2 passed). No strategy changes were made to hide these failures. The earlier 17 telemetry/fixed-lock failures were not rerun in this set and remain unresolved. Requested legacy channel_swing, channel_position_path, and channel_swing_execution test files are absent from this worktree.
- **Focused Validation**: Initial-stop retirement, peak policy, retired MA churn, and Model T suites: 114 passed. Modified Python modules compile with `python3`; editor diagnostics are clear. Focused diff checks pass outside pre-existing paper/testnet trailing whitespace. The temporary baseline compatibility plugin was removed.
- **Operational Status**: Local source and test changes only; no commit, push, deployment, restart, live orders, or modification of existing production positions/orders. WAIT implementation is outside this authorization; no Production readiness is claimed. Retained account loss limits and other authorized exits can still close a position during its opening candle. Future absolute price peaks/troughs cannot be known in real time.
