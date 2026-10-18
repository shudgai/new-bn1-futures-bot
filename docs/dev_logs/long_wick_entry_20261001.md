# Long-wick automatic-entry filter

Changed the shared entry contract to reject either wick at least as long as the body, symmetrically for LONG/SHORT and across breakout, confirmation, continuation, and live order candles. The threshold is a stated implementation default for the user's long-shadow prohibition. Existing doji rejection, account risk checks, exits, and manual operations remain unchanged.

219 isolated entry regressions passed; Python compilation and explicit source/test whitespace checks passed. Missing AIDAN specifications and historical tests remain documented. See reports/long_wick_entry_20261001/README.md for evidence and scope, and deployment.json in that directory for service verification.

### [2026-10-10 14:47:51 UTC+8] - Modification Phase: Closed-bar wick calibration and revalidation diagnostics
- **Author**: AI assistant using Copilot SDK in VS Code
- **Target Files & Lines**: `core/services/entry_contract.py` (`excessive_upper_shadow_problem`); `core/engine.py` (`_fresh_channel_entry_snapshot`, structured-entry sequence logging); `tests/test_anti_bottom_shorting.py`; `tests/test_fast_path_gates.py`
- **Modification Description**: Upper-shadow classification now uses only the latest completed candle, never the evolving live tick. LONG breakout fast lanes tolerate a wick up to 1.0 times the body only when three completed KC midlines rise strictly and the latest two completed candles are green and close above MA5; other entry types retain the 0.7 ratio. Missing `exit_bar_id` is logged safely, and empty snapshots, side mismatches, and candidate-bar changes have distinct revalidation diagnostics.
- **Trigger Reason & Requirement**: Repair the missing-exit-bar execution exception and prevent transient live-candle wicks from rejecting entries, with the Owner-selected strong-trend tolerance.
- **Verification & Test Status**: Focused entry and revalidation tests: 15 passed. The separate execution regression for a candidate without `exit_bar_id` also passed; its run reported a test-isolation side effect against the already-modified telemetry log. Broader entry tests retain unrelated failures from existing worktree behavior; no deployment performed.

### [2026-10-18 13:09:30 UTC+8] - Modification Phase: Add final account-boundary wick veto
- **Author**: shudgai999 / Codex
- **Target Files & Lines**: `core/services/entry_contract.py` (`enforce_final_order_wick_gate`); `core/testnet_account.py` (exchange create-order wrapper); `core/paper_account.py` (`open_position`).
- **Modification Description**: Added a final symmetric adverse-wick check immediately before exchange submission and paper position creation. A SHORT is blocked when the latest 1m lower wick is at least the body and at least 0.25 of the latest closed ATR; LONG uses the symmetric upper-wick rule. Blocks emit `[ENTRY_VETO]` to the service logger and account logs. The check runs after entry revalidation, fails closed on missing/unverified data, and leaves reduce-only exits outside this gate.
- **Trigger Reason & Requirement**: User reported a SHORT opened at the low of a long lower-wick candle and requested a hard stop at the final order boundary plus immediate 8006 restart.
- **Verification & Test Status**: Tests not run per the user's earlier instruction. Python compilation and `git diff --check` passed. 8006 restarted and its status endpoint returned HTTP 200 with `is_running=true`, `paper_trading=true`; startup completed without errors. No `[ENTRY_VETO]` event had occurred by verification time; the log will appear when a qualifying entry is attempted. Runtime checkout is `4cddd40c` with this fix applied locally; it is not committed.
